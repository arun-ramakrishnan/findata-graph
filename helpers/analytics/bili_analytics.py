#!/usr/bin/env python3
"""
bili_analytics.py — Parse billion-context log + ACP cache report and surface
the metrics that matter for tuning nudgeGrowthTokens / tierNudgeTokens.

Usage:
    python bili_analytics.py bili.log
    python bili_analytics.py bili.log cache_report.md
    python bili_analytics.py --json bili.log cache_report.md > metrics.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class NudgeSample:
    ts: str
    session: str
    usage_tok: int
    usage_pct: float
    growth: int
    ref: int
    interval: int
    pending_t1: int
    pending_thr: int
    model: str
    reason: str
    # Resolved context window carried by every nudge line's usage denominator
    # (it can be fractional: 83558.25), so a fold can be joined to the window
    # it ran under via the nearest preceding nudge. None when unknown.
    window: float | None = None
    fired_t1: bool = False
    fired_t2: bool = False
    fired_t3: bool = False
    fired_esc: bool = False
    # The tiers that QUALIFY at this evaluation, with the compressible mass
    # each one is carrying: `{"T1": 113124, "T2": 25803}`. Sourced from the
    # nudge's own `ready:` hint, which the plugin builds as
    #   eligible.filter(t => pending >= tierThresholds[t]).map(t => `T${t} ${pending}`)
    # so a tier is present ONLY while it is over its bar. T1's mass is also
    # always available as `pending_t1`; T2/T3 have no other source.
    tier_pending: dict[str, int] = field(default_factory=dict)
    # Which tier the cadence gate held back, e.g. "blocked: T2 (cadence)".
    blocked_tier: str = ""
    # True when this line is the plugin's IDLE branch (its reason always opens
    # with "max compressible"). Only that branch carries the `ready:` hint, and
    # the hint lists EVERY eligible tier that is over its bar — so on an idle
    # line, a tier ABSENT from the hint is provably under its bar.
    tier_idle: bool = False


@dataclass
class FoldEvent:
    ts: str
    session: str
    saved_tok: int
    shrink_pct: float | None = None
    blocks: int | None = None
    anchor_tok: int | None = None
    post_ctx_tok: int | None = None
    # What triggered it, from the nearest INJECT nudge at or before the fold's
    # ts: "T1"/"T2"/"T3" (tier ladder), "esc" (usage-pressure escalation), or
    # "agent" (the model folded with nothing asking). Every fold surfaces in
    # the log as `tool compress executed via plugin` — the tool is the actuator
    # in every path — so "agent" cannot be read off the fold line alone; set
    # by `attribute_fold_triggers`.
    trigger: str = "?"
    # Resolved context window at fold time, from the nearest preceding nudge
    # of the same session (`window=` in its usage denominator). Few folds are
    # formed under a mid-session change, so the join key is nearest-in-time.
    window: float | None = None


@dataclass
class AbsorbEvent:
    """One tool-acp absorb: a long tool output replaced by a compact summary
    IN PLACE, before the block ever entered the request window. Unlike a
    fold (whole-window rewrite), the edit is local to one block — the rest
    of the prefix stays byte-identical, so the provider cache survives."""

    ts: str
    session: str
    ref: str  # mNNNNN id of the absorbed message
    orig_tok: int  # original tool-output tokens (= the credit granted)
    summ_tok: int  # summary tokens that replaced it
    warned: bool = False  # plugin's "summary not smaller" blowup warning

    @property
    def block_shrink_pct(self) -> float | None:
        if self.orig_tok <= 0:
            return None
        return round(100.0 * (1.0 - self.summ_tok / self.orig_tok), 1)


@dataclass
class CacheUsage:
    ts: str
    session: str
    input_tok: int
    cached_tok: int
    hit_pct: float
    fold_new: bool = False


@dataclass
class WindowChange:
    ts: str
    session: str
    old: str
    new: str


@dataclass
class Warning:
    ts: str
    session: str
    kind: str
    detail: str


@dataclass
class Epoch:
    """A contiguous run of nudges sharing the same interval."""

    interval: int
    nudges: list[NudgeSample]
    folds: list[FoldEvent] = field(default_factory=list)

    @property
    def start_ts(self) -> str:
        return self.nudges[0].ts if self.nudges else "?"

    @property
    def end_ts(self) -> str:
        return self.nudges[-1].ts if self.nudges else "?"

    @property
    def nudge_count(self) -> int:
        return len(self.nudges)

    @property
    def t1_fired(self) -> int:
        return sum(1 for n in self.nudges if n.fired_t1)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _parse_ts(s: str) -> datetime:
    """Parse a bili log timestamp. Fall back to a very old date on failure."""
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return datetime(1970, 1, 1)


@dataclass
class Analysis:
    log_path: str
    report_path: str | None = None
    nudges: list[NudgeSample] = field(default_factory=list)
    folds: list[FoldEvent] = field(default_factory=list)
    usages: list[CacheUsage] = field(default_factory=list)
    window_changes: list[WindowChange] = field(default_factory=list)
    absorbs: list[AbsorbEvent] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    intervals_seen: Counter = field(default_factory=Counter)
    reasoning_pair_violations: int = 0

    # --- basic aggregates ------------------------------------------------

    def total_growth_tokens(self) -> int:
        refs = [n.ref for n in self.nudges if n.ref > 0]
        if len(refs) < 2:
            return 0
        return max(0, max(refs) - min(refs))

    def folds_per_100k(self) -> float:
        growth = self.total_growth_tokens()
        if growth == 0:
            return 0.0
        return len(self.folds) * 100_000 / growth

    def absorb_stats(self) -> tuple[int, int, float, int]:
        """(events, credit tok, avg block shrink %, blowups).

        The shrink mean covers SUCCESSFUL absorbs only — blowups (the
        plugin's "summary not smaller than the original" WARNING, summary
        larger than the source) are their own signal and would drag the
        mean deeply negative (2 of 30 measured, down to -3335%)."""
        shrinks = [
            x.block_shrink_pct
            for x in self.absorbs
            if x.block_shrink_pct is not None and not x.warned
        ]
        blowups = sum(1 for x in self.absorbs if x.warned)
        return (
            len(self.absorbs),
            sum(x.orig_tok for x in self.absorbs),
            round(sum(shrinks) / len(shrinks), 1) if shrinks else 0.0,
            blowups,
        )

    def overall_hit_pct(self) -> float | None:
        total_in = sum(u.input_tok for u in self.usages)
        total_cached = sum(u.cached_tok for u in self.usages)
        if total_in == 0:
            return None
        return 100.0 * total_cached / total_in

    def effective_interval(self) -> int | None:
        for n in reversed(self.nudges):
            if n.interval:
                return n.interval
        return None

    # --- tier ladder ----------------------------------------------------
    #
    # The three numbers the compress ladder is tuned against:
    #   * FIRES    — how many times the tier actually injected a nudge
    #   * PENDING  — the compressible mass it is carrying (the thing the bar
    #                is compared against)
    #   * BAR      — the threshold the tier was measured against
    #
    # T1 is fully observable in the log. T2/T3 are observable only while they
    # are OVER their bar (the plugin lists a tier in `ready:` only when it
    # qualifies), so "T2 never appears" is itself a finding: it never cleared
    # the bar. See `tier_bars` for how the missing bars are derived.

    def tier_fires(self) -> dict[str, int]:
        out = {"T1": 0, "T2": 0, "T3": 0}
        for n in self.nudges:
            if n.fired_t1:
                out["T1"] += 1
            elif n.fired_t2:
                out["T2"] += 1
            elif n.fired_t3:
                out["T3"] += 1
        return out

    def tier_pending(self) -> dict[str, int]:
        """The LAST observed compressible mass per tier.

        T1 comes from `pendingT1=`, present on every nudge line. T2/T3 come
        from the `ready:` hint, which lists a tier only while it is over its
        bar — so a tier absent from the result never cleared its bar.

        A tier that WAS over its bar and then dropped back under it must not
        keep reporting the old number. The plugin's idle branch always opens
        its reason with "max compressible" and its `ready:` hint is a COMPLETE
        list of the over-bar tiers, so on such a line any tier the hint does
        NOT mention is provably back under its bar and is reset to 0. Lines
        that fired a tier carry a different reason and cannot be used to
        complete the list, so they never reset anything.
        """
        out: dict[str, int] = {}
        for n in self.nudges:
            out["T1"] = n.pending_t1
            for k, v in n.tier_pending.items():
                if k != "T1":
                    out[k] = v
            if n.tier_idle:
                for k in ("T2", "T3"):
                    out.setdefault(k, 0)
                    if k not in n.tier_pending:
                        out[k] = 0
        return out

    def tier_bars(self) -> dict[str, int]:
        """The bar each tier was measured against, as observed.

        T1's bar is in the log (`pendingT1=N/thr`). T2/T3's are not, so they
        are derived the way the plugin derives them:
            tier2Threshold = round(nudgeGrowthTokens * tier2GrowthMultiplier)
        with `nudgeGrowthTokens` being the RESOLVED value — which is exactly
        the T1 bar the log reports. An explicit `tierNudgeTokens.t2` overrides
        it; that comes from the config, not the log.
        """
        out: dict[str, int] = {}
        if self.nudges:
            out["T1"] = self.nudges[-1].pending_thr
        t1 = out.get("T1")
        if t1:
            out.setdefault("T2", round(t1 * 1.5))
            out.setdefault("T3", round(t1 * 1.5))
        return out

    def tier_blocked_counts(self) -> dict[str, int]:
        """How often the cadence gate held a tier back."""
        c: Counter = Counter()
        for n in self.nudges:
            if n.blocked_tier:
                c[n.blocked_tier] += 1
        return dict(c)

    def nudge_reason_counts(self) -> Counter:
        c: Counter = Counter()
        for n in self.nudges:
            # Classification is POSITIONAL first, not reason-based: an INJECT-ESC
            # line carries the same "max compressible ... < threshold" text as an
            # idle line, so a reason-first chain silently filed 22 escalations
            # under `below_compressible_threshold` as if nothing had happened.
            if n.fired_esc:
                c["fired_esc"] += 1
            elif "T1 effective" in n.reason:
                c["fired_t1"] += 1
            elif "blocked" in n.reason and "cadence" in n.reason:
                c["blocked_cadence"] += 1
            elif "max compressible" in n.reason:
                c["below_compressible_threshold"] += 1
            elif "growth" in n.reason and "floor" in n.reason:
                c["below_growth_floor"] += 1
            else:
                c["other"] += 1
        return c

    # --- size buckets ----------------------------------------------------

    def median_hit_by_size(self) -> dict[str, tuple[float, int]]:
        """Return {bucket: (median_hit, sample_count)}."""
        buckets = {
            "40K": (30_000, 50_000),
            "60K": (50_000, 70_000),
            "80K": (70_000, 90_000),
            "100K": (90_000, 110_000),
            "120K": (110_000, 130_000),
            "140K+": (130_000, 10**9),
        }
        out: dict[str, tuple[float, int]] = {}
        for name, (lo, hi) in buckets.items():
            vals = sorted(u.hit_pct for u in self.usages if lo <= u.input_tok < hi)
            if vals:
                out[name] = (vals[len(vals) // 2], len(vals))
        return out

    # --- post-fold hits (FIXED: time-proximity, not exact-ts match) ------

    def post_fold_hits(self, window: int = 3) -> list[float]:
        """Cache hit % for the first `window` requests after each fold.

        Matches by time-proximity (first usage at or after the fold ts),
        not by exact timestamp equality — the latter never matched because
        folds and usages are logged at different events.
        """
        if not self.folds or not self.usages:
            return []
        fold_times = sorted(_parse_ts(f.ts) for f in self.folds)
        usage_times = [_parse_ts(u.ts) for u in self.usages]

        hits: list[float] = []
        for ft in fold_times:
            idx = next((i for i, ut in enumerate(usage_times) if ut >= ft), None)
            if idx is None:
                continue
            for u in self.usages[idx : idx + window]:
                hits.append(u.hit_pct)
        return hits

    # --- epochs ----------------------------------------------------------

    def epochs(self) -> list[Epoch]:
        """Group nudges into contiguous same-interval runs, then attach the
        folds that occurred during each epoch's [start_ts, end_ts] range."""
        raw: list[Epoch] = []
        for n in self.nudges:
            if raw and raw[-1].interval == n.interval:
                raw[-1].nudges.append(n)
            else:
                raw.append(Epoch(interval=n.interval, nudges=[n]))

        # attach folds by timestamp range
        for ep in raw:
            start = _parse_ts(ep.start_ts)
            # end of epoch = start of next epoch (or +infinity for last)
            idx = raw.index(ep)
            end = _parse_ts(raw[idx + 1].start_ts) if idx + 1 < len(raw) else datetime.max
            ep.folds = [f for f in self.folds if start <= _parse_ts(f.ts) < end]

        return raw


# ---------------------------------------------------------------------------
# Parsing — tuned for:
#   2026-10-13T03:28:26.687Z [info] [sess=ses_...] [v=0.1.195] [ses_...] <message>
#   2026-10-29T10:02:21.244 [info] [v=0.1.209] [session-<uuid>] [plugin] <message>
# The dsh form carries NO `Z` on the timestamp and a bare `[session-…]`
# bracket (no sess= key); `Z?` and the `session-` strip in parse_log
# keep both forms on one parser.
# ---------------------------------------------------------------------------

RE_PREFIX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z?)\s+"
    r"\[(?P<level>info|warn|debug|error)\]\s+"
    r"(?:\[sess=(?P<sess1>[^\]]+)\]\s+)?"
    r"(?:\[v=[^\]]+\]\s+)?"
    r"(?:\[(?P<sess2>[^\]]+)\]\s+)?"
    r"(?P<body>.*)$"
)

# The plugin's per-connection TCP lifecycle tag. Present on ~98% of the
# log's lines; see the skip in `parse_log`.
_CONN_TAG = "[conn#"

# The kind token is TWO words — "INJECT T1" or "INJECT-ESC T?" — so it must not
# be matched with a non-space class: `INJECT[^\s:]*` stops at the space and the
# whole pattern silently fails to match. "T?" is a real observed value; the
# escalate path does not report which tier it escalated to (all 22 instances in
# the retained log sit at 70-75% usage with BOTH gates unmet, so it is a
# usage-pressure path, not a tier-2 fold).
_KIND = r"(?:idle|INJECT(?:-ESC)? T(?P<tier>[123?]))"

RE_NUDGE_BODY = re.compile(
    _KIND + r":\s*"
    r"usage=(?P<usage_pct>[\d.]+)%\s*\((?P<usage_tok>\d+)/(?P<window>[\d.]+)\),\s*"
    r"growth=(?P<growth>-?\d+)/(?P<floor>\d+)\s*"
    r"\(ref=(?P<ref>\d+),\s*interval=(?P<interval>\d+)\),\s*"
    r"pendingT1=(?P<pending>\d+)/(?P<pending_thr>\d+)\s*"
    r"model=(?P<model>[^,]+),\s*reason=\"(?P<reason>[^\"]*)\""
)

RE_COMPRESS_OBS_BODY = re.compile(
    # v0.1.209 inserted "this-fold" between shrink% and the token counts;
    # [^(]* hops to the first "(~saved/total tok)" so both log generations
    # parse. The fold-attribution / degenerate follow-on lines don't start
    # with "shrink", so they still never match.
    r"\[acp-compress-obs\] shrink (?P<shrink>[\d.]+)%[^(]*"
    r"\(~(?P<saved>\d+)/(?P<total>\d+) tok\).*?blocks=(?P<blocks>\d+) "
    r"anchor≈(?P<anchor>\d+) tok.*?postCtx≈(?P<post>\d+)"
)

RE_FOLD_APPLIED_BODY = re.compile(
    r"\[Compressed [^\]]+ → (?P<blocks>\d+) block\(s\),\s*~(?P<saved>\d+) tokens saved"
)

RE_ABSORB_BODY = re.compile(
    # `[acp-absorb] absorbed m00233 (~5.5K tokens → summary ~1.1K). …`
    # Token counts carry K/M suffixes (or are plain); the blowup WARNING,
    # when present, names the same pair again with explicit numbers. The
    # companion `tool absorb executed via plugin` ack line never matches
    # (no [acp-absorb] prefix).
    r"\[acp-absorb\] absorbed (?P<ref>m\d+)"
    r" \(~(?P<orig>[\d.]+[KM]?) tokens → summary ~(?P<summ>[\d.]+[KM]?)\)"
    r"(?:.*WARNING: your summary \(~(?P<wsum>[\d.]+[KM]?) tokens\)"
    r" is not smaller than the original \(~(?P<worig>[\d.]+[KM]?) tokens\))?"
)


def _parse_tok(s: str) -> int:
    """`781` → 781, `5.5K` → 5500, `2.1M` → 2_100_000."""
    s = s.strip()
    mult = 1
    if s.endswith("K"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000, s[:-1]
    return int(float(s) * mult)


RE_USAGE_BODY = re.compile(
    r"\[acp-usage\] input=(?P<input>\d+) cached=(?P<cached>\d+) "
    r"\(cache hit (?P<hit>[\d.]+)%\)(?P<fold_new>\s+fold=new)?"
)

RE_WINDOW_BODY = re.compile(
    r"\[window-change\] (?P<old>.+?) -> (?P<new>.+?) "
    r"\(mid-session window resolution changed"
)

RE_DEGENERATE_BODY = re.compile(r"\[warn: degenerate-fold\]")
RE_REASONING_VIOLATION_BODY = re.compile(r"reasoning-pair-violated:")

# The `ready:` hint inside the nudge's reason string, and the cadence gate's
# `blocked: TX (cadence)` marker. Both are the only place the log exposes the
# per-tier pending mass, and the per-tier block that the cadence gate is
# holding back — which tier is stuck is the question when a tier "never fires".
RE_TIER_READY = re.compile(r"ready:\s*((?:T[123]\s+[\d,]+[,\s]*)+)")
RE_TIER_PENDING_PAIR = re.compile(r"T([123])\s+([\d,]+)")
RE_TIER_BLOCKED = re.compile(r"blocked:\s*T([123?])\b")


def tier_ready(reason: str) -> dict[str, int]:
    """The qualifying tiers and their compressible mass, from `ready:`.

    Empty when the tier list is empty — an evaluation where nothing qualified
    carries no `ready:` clause at all.
    """
    m = RE_TIER_READY.search(reason)
    if not m:
        return {}
    return {f"T{t}": int(v.replace(",", "")) for t, v in RE_TIER_PENDING_PAIR.findall(m.group(1))}


def tier_blocked(reason: str) -> str:
    """The tier the cadence gate held back ("1", "2", "3" or "?"), else ""."""
    m = RE_TIER_BLOCKED.search(reason)
    return m.group(1) if m else ""


def log_rotation_family(path: Path) -> list[Path]:
    """`bili.log` plus every rotated sibling, oldest first.

    billion-context rotates the log on size and the rotated name is an
    implementation detail (`.old` on this box; `.1`, `.2.gz` are equally
    plausible), so the family is discovered by GLOB rather than assumed —
    reading only the current file silently drops every session older than
    one rotation window (measured 2026-10-17: bili.log held 8 minutes of
    history, bili.log.old held the previous 4 days, and all 23
    model-less sessions had their `model=` nudge lines in the rotated
    file). `.gz` members are decompressed transparently.

    Order is oldest-first by mtime because callers index `[-1]` for the
    newest record — parsing newest-first would make the OLDEST win.
    """
    parent = path.parent
    if not parent.is_dir():
        return [path] if path.is_file() else []
    fam = [p for p in parent.glob(path.name + "*") if p.is_file()]
    return sorted(fam, key=lambda p: (p.stat().st_mtime, p.name))


def _open_log(path: Path):
    if path.name.endswith(".gz"):
        import gzip

        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def parse_logs(paths, session_filter: str | None = None) -> Analysis:
    """`parse_log` over a rotation family, merged and ordered oldest-first.

    Every list member is re-sorted by timestamp after the union so that
    `nudges[-1]` is genuinely the newest sample regardless of how the
    rotation happened to split the session across files.
    """
    paths = [Path(p) for p in paths if Path(p).is_file()]
    if not paths:
        return Analysis(log_path="")
    merged = Analysis(log_path=str(paths[-1]))
    for p in sorted(paths, key=lambda q: (q.stat().st_mtime, q.name)):
        part = parse_log(p, session_filter=session_filter)
        merged.nudges.extend(part.nudges)
        merged.folds.extend(part.folds)
        merged.usages.extend(part.usages)
        merged.window_changes.extend(part.window_changes)
        merged.absorbs.extend(part.absorbs)
        merged.warnings.extend(part.warnings)
        merged.intervals_seen.update(part.intervals_seen)
        merged.reasoning_pair_violations += part.reasoning_pair_violations
    for series in (
        merged.nudges,
        merged.folds,
        merged.usages,
        merged.window_changes,
        merged.absorbs,
    ):
        series.sort(key=lambda s: s.ts)
    merged.warnings.sort(key=lambda w: getattr(w, "ts", "") or "")
    attribute_fold_triggers(merged)
    return merged


def parse_log(  # noqa: C901
    path: Path, session_filter: str | None = None
) -> Analysis:
    a = Analysis(log_path=str(path))
    seen_folds: list[tuple[datetime, int, int]] = []

    with _open_log(path) as fh:
        for raw in fh:
            # The plugin logs three TCP-lifecycle lines per connection
            # (`post-response close` / `linger complete` / `closed`), and 89%
            # of its connections serve a single request, so they are ~98% of
            # the log by volume. They carry no nudge/fold/usage/window
            # information — but `RE_PREFIX` DOES match them (with `sess2`
            # capturing the `conn#NNNN` tag), which would run every body
            # regex over each one. Skip them on the raw line.
            if _CONN_TAG in raw:
                continue
            pm = RE_PREFIX.match(raw)
            if not pm:
                continue
            ts = pm["ts"]
            session = pm["sess1"] or pm["sess2"] or "?"
            # dsh logs tag lines with a bare `[session-<uuid>]` bracket
            # (captured by sess2); strip the prefix so the id matches the
            # session-index sids and the --session filters downstream.
            if session.startswith("session-"):
                session = session[len("session-") :]
            if session_filter and session != session_filter:
                continue
            body = pm["body"]

            m = RE_NUDGE_BODY.search(body)
            if m:
                g = m.groupdict()
                a.nudges.append(
                    NudgeSample(
                        ts=ts,
                        session=session,
                        usage_tok=int(g["usage_tok"]),
                        usage_pct=float(g["usage_pct"]),
                        growth=int(g["growth"]),
                        ref=int(g["ref"]),
                        interval=int(g["interval"]),
                        pending_t1=int(g["pending"]),
                        pending_thr=int(g["pending_thr"]),
                        model=g["model"],
                        reason=g["reason"],
                        window=float(g["window"]),
                        fired_t1=(g["tier"] == "1"),
                        fired_t2=(g["tier"] == "2"),
                        fired_t3=(g["tier"] == "3"),
                        fired_esc=(g["tier"] == "?"),
                        tier_pending=tier_ready(g["reason"]),
                        blocked_tier=tier_blocked(g["reason"]),
                        tier_idle=("max compressible" in g["reason"]),
                    )
                )
                a.intervals_seen[g["interval"]] += 1
                continue

            m = RE_COMPRESS_OBS_BODY.search(body)
            if m:
                g = m.groupdict()
                saved = int(g["saved"])
                blocks = int(g["blocks"])
                seen_folds.append((_parse_ts(ts), saved, blocks))
                a.folds.append(
                    FoldEvent(
                        ts=ts,
                        session=session,
                        saved_tok=saved,
                        shrink_pct=float(g["shrink"]),
                        blocks=blocks,
                        anchor_tok=int(g["anchor"]),
                        post_ctx_tok=int(g["post"]),
                    )
                )
                continue

            m = RE_FOLD_APPLIED_BODY.search(body)
            if m:
                g = m.groupdict()
                saved = int(g["saved"])
                blocks = int(g["blocks"])
                fold_ts = _parse_ts(ts)
                is_dup = any(
                    abs((fold_ts - prev_ts).total_seconds()) <= 2.0
                    and prev_saved == saved
                    and prev_blocks == blocks
                    for (prev_ts, prev_saved, prev_blocks) in seen_folds
                )
                if is_dup:
                    continue
                seen_folds.append((fold_ts, saved, blocks))
                a.folds.append(
                    FoldEvent(
                        ts=ts,
                        session=session,
                        saved_tok=saved,
                        blocks=blocks,
                    )
                )
                continue

            if RE_DEGENERATE_BODY.search(body):
                a.warnings.append(
                    Warning(
                        ts=ts,
                        session=session,
                        kind="degenerate-fold",
                        detail=body[:300],
                    )
                )
                continue

            m = RE_ABSORB_BODY.search(body)
            if m:
                g = m.groupdict()
                a.absorbs.append(
                    AbsorbEvent(
                        ts=ts,
                        session=session,
                        ref=g["ref"],
                        orig_tok=_parse_tok(g["orig"]),
                        summ_tok=_parse_tok(g["summ"]),
                        warned=bool(g["wsum"]),
                    )
                )
                continue

            m = RE_USAGE_BODY.search(body)
            if m:
                g = m.groupdict()
                a.usages.append(
                    CacheUsage(
                        ts=ts,
                        session=session,
                        input_tok=int(g["input"]),
                        cached_tok=int(g["cached"]),
                        hit_pct=float(g["hit"]),
                        fold_new=bool(g["fold_new"]),
                    )
                )
                continue

            m = RE_WINDOW_BODY.search(body)
            if m:
                g = m.groupdict()
                a.window_changes.append(
                    WindowChange(
                        ts=ts,
                        session=session,
                        old=g["old"],
                        new=g["new"],
                    )
                )
                continue

            if RE_REASONING_VIOLATION_BODY.search(body):
                a.reasoning_pair_violations += 1
                a.warnings.append(
                    Warning(
                        ts=ts,
                        session=session,
                        kind="reasoning-pair-violated",
                        detail=body[:300],
                    )
                )

    return a


#: How long after an INJECT nudge its fold can still be attributed to it.
#: Measured pairs: INJECT-ESC → fold in 27s (08:18:33 → 08:19:00) and
#: INJECT T2 → fold in 99s (08:47:21 → 08:49:00) on ses_ec7af063. Ten minutes
#: is generous headroom without reaching into an unrelated era's injections;
#: past it the model folded with nothing asking, which is the `agent` trigger.
FOLD_TRIGGER_WINDOW_S = 600.0


def _nudge_trigger(n: NudgeSample) -> str | None:
    """The INJECT kind's trigger label, or None for an idle nudge."""
    if n.fired_t1:
        return "T1"
    if n.fired_t2:
        return "T2"
    if n.fired_t3:
        return "T3"
    if n.fired_esc:
        return "esc"
    return None


def attribute_fold_triggers(a: Analysis) -> None:
    """Set every fold's `trigger` (and `window`) from its session's nudges.

    The nudge line's kind token (`idle` / `INJECT T1` / `INJECT T2` /
    `INJECT T3` / `INJECT-ESC T?`) is the only trigger flag the log carries,
    so attribution is a per-session join, not a new parse: for each fold, take
    the injection nudges (any fired_* flag) of the SAME session at or before
    its ts, keep the newest, and accept it when it sits within
    `FOLD_TRIGGER_WINDOW_S`; idle stretches in between are skipped — the model
    may answer an injection several nudge evaluations late. With either no
    preceding injection or only a stale one, the fold was the agent's own
    idea (`agent`). Fold `window` is the same session's nearest preceding
    nudge window. Sessions are grouped, never mixed: interleaved sessions in
    one rotation family must not lend each other triggers. Idempotent:
    recomputed from scratch on every call.
    """
    nudges_by_sid: dict[str, list[NudgeSample]] = {}
    for n in sorted(a.nudges, key=lambda x: x.ts):
        nudges_by_sid.setdefault(n.session, []).append(n)
    for f in a.folds:
        f.trigger = "agent"
        f.window = None
        nudges = nudges_by_sid.get(f.session)
        if not nudges:
            continue
        n_ts = [n.ts for n in nudges]
        i = bisect.bisect_right(n_ts, f.ts)
        if i:
            f.window = nudges[i - 1].window
            inj = [n for n in nudges[:i] if _nudge_trigger(n)]
            if inj:
                n = inj[-1]
                if (_parse_ts(f.ts) - _parse_ts(n.ts)).total_seconds() <= FOLD_TRIGGER_WINDOW_S:
                    trig = _nudge_trigger(n)
                    if trig is not None:
                        f.trigger = trig


#: Trigger labels that map to the interval ladder (every other label — `esc`,
#: `agent` — is a different pressure and must not be counted as a ladder fold).
LADDER_TRIGGERS = ("T1", "T2", "T3")

#: Window buckets for the [FOLDS] cross-tab. Small-window sessions are
#: carried by the relative esc path while large ones ride the absolute
#: ladder; the split was measured on the 2026-10-18 tuning arc.
WINDOW_BUCKETS = ("<100k", "100-300k", "300-600k", ">=600k", "unknown")


def _window_bucket(w: float | None) -> str:
    if w is None:
        return "unknown"
    if w < 100_000:
        return "<100k"
    if w < 300_000:
        return "100-300k"
    if w < 600_000:
        return "300-600k"
    return ">=600k"


def fold_trigger_counts(folds: list[FoldEvent]) -> Counter:
    """Trigger histogram over folds: {"T1": n, "T2": n, "esc": n, "agent": n}."""
    return Counter(f.trigger for f in folds)


# ---------------------------------------------------------------------------
# Cache report parsing
# ---------------------------------------------------------------------------

RE_REPORT_GRAND = re.compile(
    r"total input\s+(?P<input>[\d.]+)[MK] tok.*?"
    r"total cached\s+(?P<cached>[\d.]+)[MK] tok\s+\(hit (?P<hit>[\d.]+)%\)",
    re.S,
)
RE_REPORT_MISS = re.compile(
    r"miss breakdown.*?new content\s+(?P<new>[\d.]+)K? tok.*?"
    r"compress re-pay\s+(?P<repay>[\d.]+)(?:K)? tok.*?"
    r"unattributed\)\s+(?P<unattr>[\d.]+)(?:K)? tok",
    re.S,
)
RE_REPORT_FOLD_ECON = re.compile(
    r"gross saved (?P<gross>[\d.]+)M tok.*?repay cost (?P<repay>[\d.]+)K tok.*?"
    r"net \+(?P<net>[\d.]+)M tok.*?verdict:\s*(?P<paid>\d+) paid back,\s*"
    r"(?P<unpaid>\d+) not paid back,\s*(?P<unobs>\d+) unobserved",
    re.S,
)
RE_REPORT_HIT_BY_SIZE = re.compile(r"hit% by input size \(median\):\s*(?P<body>[^\n]+)")
RE_REPORT_INVALIDATION = re.compile(
    r"stable-prefix re-bill by cause.*?:\s*(?P<charged>[\d.]+)(?:K)? tok charged · "
    r"(?P<unattr>[\d.]+)(?:K)? tok unattributed",
    re.S,
)


def parse_report(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict = {}

    m = RE_REPORT_GRAND.search(text)
    if m:
        out["grand"] = {
            "input_M": float(m["input"]),
            "cached_M": float(m["cached"]),
            "hit_pct": float(m["hit"]),
        }

    m = RE_REPORT_MISS.search(text)
    if m:
        out["miss"] = {
            "new_content_K": float(m["new"]),
            "compress_repay_K": float(m["repay"]),
            "unattributed_K": float(m["unattr"]),
        }

    m = RE_REPORT_FOLD_ECON.search(text)
    if m:
        out["fold_econ"] = {
            "gross_saved_M": float(m["gross"]),
            "repay_cost_K": float(m["repay"]),
            "net_M": float(m["net"]),
            "paid_back": int(m["paid"]),
            "not_paid_back": int(m["unpaid"]),
            "unobserved": int(m["unobs"]),
        }

    m = RE_REPORT_HIT_BY_SIZE.search(text)
    if m:
        buckets = {}
        for pair in re.finditer(r"(\d+K|140K\+)=([\d.]+)%", m["body"]):
            buckets[pair.group(1)] = float(pair.group(2))
        out["hit_by_size"] = buckets

    m = RE_REPORT_INVALIDATION.search(text)
    if m:
        out["invalidation"] = {
            "charged_K": float(m["charged"]),
            "unattributed_K": float(m["unattr"]),
        }

    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def fmt_pct(v: float | None) -> str:
    return f"{v:.2f}%" if v is not None else "n/a"


def _viol_gap_minutes(a) -> float | None:
    """Minutes from the LAST reasoning-pair violation to the last log line.

    None when there is no evidence either way (no violation, or no log line
    after the last one) — so callers can say "unknown" instead of guessing.
    """
    viols = [w.ts for w in a.warnings if w.kind == "reasoning-pair-violated"]
    if not viols:
        return None
    stamps = [s.ts for s in (*a.nudges, *a.usages) if s.ts > max(viols)]
    if not stamps:
        return None
    try:
        end = datetime.strptime(max(stamps), "%Y-%m-%dT%H:%M:%S.%fZ")
        last = datetime.strptime(max(viols), "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return None
    return (end - last).total_seconds() / 60


# The raw log stamps look like `2026-10-19T13:15:10. 380Z` — Go puts a space
# after the dot — so the sub-second part is unreadable by strptime. One second
# of resolution is plenty for a liveness check.
_RAW_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def log_last_timestamp(a) -> datetime | None:
    """The newest timestamp anywhere in the log family — the proxy's own clock.

    Reads only the TAIL of the newest file: the family can be 10 MB and this
    runs per report.
    """
    paths = log_rotation_family(Path(a.log_path))
    if not paths:
        return None
    try:
        with paths[-1].open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        m = _RAW_TS.match(line)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue
    return None


def session_last_timestamp(a) -> datetime | None:
    """The session's own last log line."""
    parsed = []
    for s in (*a.nudges, *a.usages, *a.warnings):
        try:
            parsed.append(datetime.strptime(s.ts, "%Y-%m-%dT%H:%M:%S.%fZ"))
        except ValueError, TypeError:
            continue
    return max(parsed) if parsed else None


def session_is_live(a, *, window_minutes: float = 3.0) -> bool:
    """Is this session still the newest thing in the log?

    A violation at the very end of a LIVE session is indistinguishable from a
    terminal one by the gap alone: the session's own last line IS the
    violation, so "time from the last violation to the session's end" is a
    couple of minutes and the verdict reads "terminal".

    Measured 2026-10-19: `bili_sessions --detail ses_ec7af063cffew2a8xvEcV142n6`
    printed "Terminal: this session did not survive its violation" for a
    session that was still running and answering. The gap measures how far the
    session got, not whether it DIED; only the log's own clock can tell the
    two apart, because the proxy keeps logging other sessions either way.
    """
    last_sess = session_last_timestamp(a)
    last_log = log_last_timestamp(a)
    if last_sess is None or last_log is None:
        return False
    return (last_log - last_sess).total_seconds() <= window_minutes * 60


def print_report(a: Analysis, report: dict | None) -> None:  # noqa: C901
    sep = "=" * 78

    print(sep)
    print("BILLION-CONTEXT SESSION ANALYSIS")
    print(f"  log:    {a.log_path}")
    if a.report_path:
        print(f"  report: {a.report_path}")
    print(sep)

    total_in = sum(u.input_tok for u in a.usages)
    total_cached = sum(u.cached_tok for u in a.usages)
    overall_hit = a.overall_hit_pct()

    print("\n[HEADLINE]")
    print(f"  requests parsed       : {len(a.usages)}")
    print(f"  total input (log)     : {total_in:,} tok")
    print(f"  total cached (log)    : {total_cached:,} tok")
    print(f"  overall cache hit     : {fmt_pct(overall_hit)}")
    if report and "grand" in report:
        g = report["grand"]
        print(f"  report grand hit      : {g['hit_pct']:.2f}% ({g['cached_M']}M / {g['input_M']}M)")

    # --- tuning state ----------------------------------------------------
    print("\n[TUNING STATE]")
    print(f"  nudge interval counts : {dict(a.intervals_seen)}")
    eff = a.effective_interval()
    if eff is not None:
        print(f"  effective interval    : {eff}")
        if eff >= 150_000:
            print("    → good for 1M window (≥150K)")
        elif eff >= 100_000:
            print("    → acceptable; 200K recommended for 1M")
        else:
            print("    → WARNING: too low for 1M window")

    # --- epochs ----------------------------------------------------------
    eps = a.epochs()
    if len(eps) > 1:
        print(f"\n[EPOCHS]  ({len(eps)} contiguous interval runs)")
        print(
            f"  {'interval':>9}  {'nudges':>7}  {'T1 fired':>8}  "
            f"{'folds':>6}  {'fold/100K':>10}  window"
        )
        for ep in eps:
            growth = 0
            if len(ep.nudges) >= 2:
                growth = max(0, ep.nudges[-1].ref - ep.nudges[0].ref)
            fpk = (len(ep.folds) * 100_000 / growth) if growth else 0.0
            window = f"{ep.start_ts[11:19]}→{ep.end_ts[11:19]}"
            print(
                f"  {ep.interval:>9}  {ep.nudge_count:>7}  {ep.t1_fired:>8}  "
                f"{len(ep.folds):>6}  {fpk:>9.2f}  {window}"
            )
        print(
            "  → compare the 200000 epoch to the 50000/100000 epochs; lower fold/100K is the goal"
        )

    # --- nudge behavior --------------------------------------------------
    print("\n[NUDGE BEHAVIOR]")
    print(f"  nudge samples         : {len(a.nudges)}")
    rc = a.nudge_reason_counts()
    for k, v in rc.most_common():
        print(f"    {k:<34}: {v}")
    fired_t1 = sum(1 for n in a.nudges if n.fired_t1)
    fired_esc = sum(1 for n in a.nudges if n.fired_esc)
    print(f"  T1 nudge fired        : {fired_t1}")
    if fired_esc:
        print(f"  ESC nudge fired       : {fired_esc}  (tier unknown — escalation path)")

    # --- folds -----------------------------------------------------------
    print("\n[FOLDS]")
    print(f"  total folds           : {len(a.folds)}")
    _abs_n, _abs_credit, _abs_shrink, _abs_blow = a.absorb_stats()
    print(
        f"  total absorbs         : {_abs_n}"
        + (
            f" (credit ~{_abs_credit:,} tok, avg block shrink {_abs_shrink:.0f}%"
            + (f", {_abs_blow} blowup(s)" if _abs_blow else "")
            + ")"
            if _abs_n
            else ""
        )
    )
    attribute_fold_triggers(a)
    trig = fold_trigger_counts(a.folds)
    if trig:
        ladder = trig.get("T1", 0) + trig.get("T2", 0) + trig.get("T3", 0)
        print(
            f"  ladder-driven (T1/T2/T3): {ladder}"
            f"  (T1 {trig.get('T1', 0)} / T2 {trig.get('T2', 0)} / T3 {trig.get('T3', 0)})"
        )
        if trig.get("esc"):
            print(f"  esc-driven (usage pressure): {trig['esc']}")
        print(f"  agent-initiated       : {trig.get('agent', 0)}")
        # Window × trigger cross-tab: small-window sessions are carried by the
        # relative esc path while large ones ride the absolute ladder; make
        # the regime split visible here (measured on the 2026-10-18 arc).
        grid: Counter = Counter()
        for f in a.folds:
            grp = "ladder" if f.trigger in LADDER_TRIGGERS else f.trigger
            grid[(_window_bucket(f.window), grp)] += 1
        print("  folds by window bucket:")
        print("           " + "".join(f"{b:>10s}" for b in WINDOW_BUCKETS))
        for grp in ("ladder", "esc", "agent"):
            print(f"  {grp:8s} " + "".join(f"{grid[(b, grp)]:>10d}" for b in WINDOW_BUCKETS))
    growth = a.total_growth_tokens()
    if growth:
        print(f"  net token growth      : {growth:,}")
        print(f"  folds per 100K growth : {a.folds_per_100k():.2f}")
        fpk = a.folds_per_100k()
        if fpk > 3:
            print("    → HIGH: raise nudgeGrowthTokens further")
        elif fpk < 0.5:
            print("    → LOW: compression rarely fires; watch context size")
        else:
            print("    → healthy range")
    if a.folds:
        saved = [f.saved_tok for f in a.folds]
        shrinks = [f.shrink_pct for f in a.folds if f.shrink_pct is not None]
        print(f"  total tokens saved    : {sum(saved):,}")
        print(f"  median saved / fold   : {sorted(saved)[len(saved) // 2]:,}")
        if shrinks:
            print(f"  median shrink %       : {sorted(shrinks)[len(shrinks) // 2]:.1f}%")
            big = [s for s in shrinks if s >= 80]
            if big:
                print(f"  folds ≥80% shrink     : {len(big)}  ← cache-busting risk")

    # --- cache hits ------------------------------------------------------
    print("\n[CACHE HITS]")
    med = a.median_hit_by_size()
    if med:
        print("  median hit by size (from log, with sample count):")
        for k, (v, n) in med.items():
            warn = "  ← low n, unreliable" if n < 5 else ""
            print(f"    {k:<8}: {v:>6.2f}%  (n={n}){warn}")
    if report and "hit_by_size" in report:
        print("  report hit by size:")
        for k, v in report["hit_by_size"].items():
            print(f"    {k:<8}: {v:>6.2f}%")

    pf = a.post_fold_hits(window=3)
    if pf:
        print("  post-fold hits (first 3 reqs after each fold):")
        print(f"    count : {len(pf)}")
        print(f"    median: {sorted(pf)[len(pf) // 2]:.2f}%")
        print(f"    min   : {min(pf):.2f}%")
        if min(pf) < 80:
            print("    → WARNING: a fold caused a severe cache bust (<80%)")
        else:
            print("    → no severe cache busts after folds")
    else:
        print("  post-fold hits        : n/a (no folds or no usages)")

    # --- warnings --------------------------------------------------------
    print("\n[WARNINGS]")
    kinds = Counter(w.kind for w in a.warnings)
    if not kinds:
        print("  (none)")
    for k, v in kinds.most_common():
        print(f"  {k:<28}: {v}")
    deg = kinds.get("degenerate-fold", 0)
    if deg == 0 and len(a.folds) > 0:
        print("  → no degenerate folds detected")
    elif deg > 0:
        print(f"  → {deg} degenerate fold(s): raise nudgeGrowthTokens")

    # --- window changes --------------------------------------------------
    print("\n[WINDOW CHANGES]")
    print(f"  total                 : {len(a.window_changes)}")
    for w in a.window_changes[-5:]:
        print(f"    {w.ts}  {w.old} -> {w.new}")

    # --- extended breakdowns ----------------------------------------------
    models = Counter(n.model for n in a.nudges)
    print("\n[BREAKDOWNS]")
    if models:
        print("  by model (nudges / T1 fired):")
        for m, c in models.most_common():
            t1 = sum(1 for n in a.nudges if n.model == m and n.fired_t1)
            print(f"    {m:<32}: {c:>4} / {t1}")
    ready_blocked = sum(
        1 for n in a.nudges if n.pending_t1 >= n.pending_thr and "blocked" in n.reason
    )
    growth_floor = sum(1 for n in a.nudges if "floor" in n.reason)
    below_comp = sum(1 for n in a.nudges if "max compressible" in n.reason)
    print("  nudge reason cross-tab:")
    print(f"    ready-but-blocked (T1)     : {ready_blocked}")
    print(f"    below growth floor        : {growth_floor}")
    print(f"    below compressible thr    : {below_comp}")
    # fold shrink buckets
    if a.folds:
        buckets = Counter()
        for f in a.folds:
            if f.shrink_pct is None:
                buckets["unknown"] += 1
            elif f.shrink_pct < 40:
                buckets["<40%"] += 1
            elif f.shrink_pct < 80:
                buckets["40-79%"] += 1
            else:
                buckets["≥80% (degenerate)"] += 1
        print("  fold shrink buckets:")
        for k in ["<40%", "40-79%", "≥80% (degenerate)", "unknown"]:
            if buckets.get(k):
                print(f"    {k:<22}: {buckets[k]}")
    # max pending/growth seen
    if a.nudges:
        print(f"  max pendingT1 seen        : {max(n.pending_t1 for n in a.nudges):,}")
        print(f"  max growth seen           : {max(n.growth for n in a.nudges):,}")
        esc = sum(1 for n in a.nudges if n.fired_esc)
        if esc:
            # The escalate path reports no tier. Counted separately because its
            # reason text is indistinguishable from an idle line's.
            print(f"  INJECT-ESC (tier unknown) : {esc}")

    # --- mistuning detection ---------------------------------------------
    print("\n[MISTUNING]")
    if a.nudges:
        for n_last in reversed(a.nudges):
            if n_last.interval:
                last_interval = n_last.interval
                break
        else:
            last_interval = None
        if last_interval is not None:
            max_pending = max(n.pending_t1 for n in a.nudges)
            print(
                f"  max pendingT1 vs last interval: {max_pending:,} / {last_interval:,}"
                f"  (never fires if max < interval)"
            )
        if ready_blocked:
            print(
                f"  WARNING: {ready_blocked} ready-but-blocked nudge(s) — "
                f"growth floor may be too high or cadence too restrictive"
            )
        if len(models) > 1:
            print(
                f"  WARNING: {len(models)} distinct models in one session "
                f"— nudge/window behavior is not directly comparable"
            )
        if len(a.window_changes) >= 3:
            print(
                f"  WARNING: {len(a.window_changes)} window changes "
                f"— interval/extent context is volatile"
            )
        eps = a.epochs()
        if len(eps) > 1:
            # Density warnings are about the interval ladder, so only
            # ladder-driven folds measure it: an `esc` fold is usage-pressure
            # and an `agent` fold is unprompted — neither is a symptom of a
            # too-low nudgeGrowthTokens.
            def _ladder_folds(ep: Epoch) -> int:
                return sum(1 for f in ep.folds if f.trigger in LADDER_TRIGGERS)

            low_interval_epochs = [ep for ep in eps if ep.interval < 150_000]
            low_folds = sum(_ladder_folds(ep) for ep in low_interval_epochs)
            if low_folds:
                print(
                    f"  WARNING: {low_folds} ladder-driven fold(s) still happened "
                    f"under interval <150K (esc/agent folds excluded) — "
                    f"config did not fully take effect"
                )
            # final epoch density
            last_ep = eps[-1]
            if last_ep.nudges and len(last_ep.nudges) >= 2:
                growth = max(0, last_ep.nudges[-1].ref - last_ep.nudges[0].ref)
                fpk = (_ladder_folds(last_ep) * 100_000 / growth) if growth else 0.0
                if fpk > 3:
                    print(
                        f"  WARNING: final epoch ladder-fold density {fpk:.2f}/100K "
                        f"> 3 — tuning not yet effective"
                    )
                else:
                    print(f"  OK: final epoch ladder-fold density {fpk:.2f}/100K ≤ 3")

    # --- efficiency -------------------------------------------------------
    print("\n[EFFICIENCY]")
    if a.folds:
        saved = [f.saved_tok for f in a.folds]
        print(f"  total saved (raw count, may double-count parser duplicates): {sum(saved):,}")
        print(
            f"  median / min / max saved  : "
            f"{sorted(saved)[len(saved) // 2]:,} / {min(saved):,} / {max(saved):,}"
        )
        # fold recovery: requests until cache hit ≥90% after each fold
        fold_times = sorted(_parse_ts(f.ts) for f in a.folds)
        usage_times = [_parse_ts(u.ts) for u in a.usages]
        recovery_counts: list[int] = []
        for ft in fold_times:
            idx = next((i for i, ut in enumerate(usage_times) if ut >= ft), None)
            if idx is None:
                continue
            needed = 0
            for u in a.usages[idx : idx + 20]:
                needed += 1
                if u.hit_pct >= 90:
                    break
            else:
                needed = 20
            recovery_counts.append(needed)
        if recovery_counts:
            print(f"  recovery requests to ≥90% hit (per fold): {recovery_counts}")
            print(
                f"  median recovery requests   : "
                f"{sorted(recovery_counts)[len(recovery_counts) // 2]}"
            )
        anchors = [f.anchor_tok for f in a.folds if f.anchor_tok is not None]
        if anchors:
            print(f"  anchor token growth (first→last): {anchors[0]:,} → {anchors[-1]:,}")
    if report:
        if "fold_econ" in report:
            fe = report["fold_econ"]
            print(
                f"  paid back / not paid back / unobserved: "
                f"{fe['paid_back']} / {fe['not_paid_back']} / {fe['unobserved']}"
            )
            if fe["not_paid_back"] > 0:
                print(f"  WARNING: {fe['not_paid_back']} fold(s) did not amortize")
        if "miss" in report:
            m = report["miss"]
            repay_ratio = m["compress_repay_K"] / max(m["new_content_K"], 1)
            print(f"  compress re-pay / new content: {repay_ratio:.2f}")
            if repay_ratio >= 1.0:
                print("  WARNING: folds cost as much as fresh content — replays are too expensive")
            elif repay_ratio >= 0.5:
                print("  NOTE: replay ratio ≥0.5 — room to consolidate folds")
        if "grand" in report:
            g = report["grand"]
            print(f"  overall report hit: {g['hit_pct']:.2f}% ({g['cached_M']}M / {g['input_M']}M)")
    post = a.post_fold_hits(window=5)
    if post:
        print(f"  post-fold 5-req median hit: {sorted(post)[len(post) // 2]:.2f}%")

    # --- report cross-check ----------------------------------------------
    if report:
        print("\n[REPORT CROSS-CHECK]")
        if "miss" in report:
            m = report["miss"]
            print(f"  new content           : {m['new_content_K']:.1f}K")
            print(f"  compress re-pay       : {m['compress_repay_K']:.1f}K")
            print(f"  unattributed miss     : {m['unattributed_K']:.1f}K")
            repay_ratio = m["compress_repay_K"] / max(m["new_content_K"], 1)
            print(f"  re-pay / new content  : {repay_ratio:.2f}")
            if repay_ratio > 1.0:
                print("    → HIGH: folds are costing more than fresh content")
        if "fold_econ" in report:
            fe = report["fold_econ"]
            print(f"  gross saved           : {fe['gross_saved_M']}M")
            print(f"  net saved             : {fe['net_M']}M")
            print(f"  paid back / not       : {fe['paid_back']} / {fe['not_paid_back']}")
            if fe["not_paid_back"] > 1:
                print("    → WARNING: multiple folds didn't amortize")
        if "invalidation" in report:
            inv = report["invalidation"]
            print(
                f"  stable-prefix re-bill : {inv['charged_K']:.1f}K charged, "
                f"{inv['unattributed_K']:.1f}K unattributed"
            )

    # --- verdict ---------------------------------------------------------
    print(f"\n{sep}")
    print("VERDICT")
    print(sep)
    verdicts = []

    if overall_hit is not None:
        if overall_hit >= 96.5:
            verdicts.append(f"✔ overall hit {overall_hit:.2f}% — excellent")
        elif overall_hit >= 95:
            verdicts.append(f"• overall hit {overall_hit:.2f}% — good, room to grow")
        else:
            verdicts.append(f"✘ overall hit {overall_hit:.2f}% — investigate")

    # per-epoch fold frequency is the meaningful signal
    if len(eps) > 1:
        best = min(
            (ep for ep in eps if ep.nudges and len(ep.nudges) >= 2),
            key=lambda ep: len(ep.folds) * 100_000 / max(1, ep.nudges[-1].ref - ep.nudges[0].ref),
            default=None,
        )
        if best is not None and best.interval == eff:
            verdicts.append(
                f"✔ latest epoch (interval={best.interval}) has the lowest "
                f"fold density — tuning is working"
            )
        elif best is not None:
            verdicts.append(
                f"• epoch interval={best.interval} has lowest fold density, "
                f"not your current {eff} — recheck tuning"
            )

    if deg == 0 and len(a.folds) > 0:
        verdicts.append("✔ no degenerate folds — prefix rewrites are minimal")
    elif deg > 0:
        verdicts.append(f"✘ {deg} degenerate fold(s) — raise nudgeGrowthTokens")

    if growth and a.folds_per_100k() <= 3:
        verdicts.append(f"✔ overall fold frequency {a.folds_per_100k():.2f}/100K — healthy")
    elif growth:
        verdicts.append(
            f"• overall fold frequency {a.folds_per_100k():.2f}/100K — "
            f"high, but dominated by old epochs (see EPOCHS)"
        )

    if pf and min(pf) >= 90:
        verdicts.append(f"✔ post-fold cache hits ≥{min(pf):.0f}% — no busting")
    elif pf and min(pf) < 80:
        verdicts.append(f"✘ post-fold cache hit dipped to {min(pf):.0f}%")
    elif pf:
        verdicts.append(f"• post-fold cache hits min {min(pf):.0f}% — mild dip")

    if a.reasoning_pair_violations:
        # The headline stays a count. The measured gap and the Terminal /
        # RECOVERED verdict live in `bili_sessions.py --detail <session>`,
        # which is where the per-session evidence belongs — printing them here
        # duplicated the number and, worse, asserted "terminal" for a session
        # that was still running (see `session_is_live`).
        verdicts.append(
            f"⚠ {a.reasoning_pair_violations} reasoning-pair violations — "
            f"strict-echo upstreams REJECT; DeepSeek/zAI recover, others kill the "
            f"session (measured outcome: bili_sessions.py --detail <session>)"
        )

    if not verdicts:
        verdicts.append("(not enough data to form a verdict)")

    for v in verdicts:
        print(f"  {v}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Analyze billion-context logs + ACP cache report.")
    ap.add_argument("log", type=Path, help="path to bili.log")
    ap.add_argument("report", nargs="?", type=Path, help="optional ACP cache report (markdown)")
    ap.add_argument("--session", help="only analyze lines for this session id")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a human report")
    args = ap.parse_args(argv)

    if not args.log.exists():
        print(f"error: log not found: {args.log}", file=sys.stderr)
        return 2

    analysis = parse_logs(log_rotation_family(args.log), session_filter=args.session)

    report_data: dict | None = None
    if args.report:
        if not args.report.exists():
            print(f"error: report not found: {args.report}", file=sys.stderr)
            return 2
        analysis.report_path = str(args.report)
        report_data = parse_report(args.report)

    if args.json:
        payload = {
            "log_path": analysis.log_path,
            "report_path": analysis.report_path,
            "headline": {
                "requests": len(analysis.usages),
                "total_input_tok": sum(u.input_tok for u in analysis.usages),
                "total_cached_tok": sum(u.cached_tok for u in analysis.usages),
                "overall_hit_pct": analysis.overall_hit_pct(),
            },
            "tuning": {
                "intervals_seen": dict(analysis.intervals_seen),
                "absorbs": [
                    {
                        "ts": x.ts,
                        "session": x.session,
                        "ref": x.ref,
                        "orig_tok": x.orig_tok,
                        "summ_tok": x.summ_tok,
                        "block_shrink_pct": x.block_shrink_pct,
                        "warned": x.warned,
                    }
                    for x in analysis.absorbs
                ],
                "effective_interval": analysis.effective_interval(),
            },
            "epochs": [
                {
                    "interval": ep.interval,
                    "nudges": ep.nudge_count,
                    "t1_fired": ep.t1_fired,
                    "folds": len(ep.folds),
                    "start_ts": ep.start_ts,
                    "end_ts": ep.end_ts,
                }
                for ep in analysis.epochs()
            ],
            "nudges": {
                "samples": len(analysis.nudges),
                "reason_counts": dict(analysis.nudge_reason_counts()),
                "t1_fired": sum(1 for n in analysis.nudges if n.fired_t1),
            },
            "folds": {
                "count": len(analysis.folds),
                "net_growth_tok": analysis.total_growth_tokens(),
                "per_100k": analysis.folds_per_100k(),
                "events": [asdict(f) for f in analysis.folds],
            },
            "cache": {
                "median_hit_by_size": {
                    k: {"median": v, "n": n} for k, (v, n) in analysis.median_hit_by_size().items()
                },
                "post_fold_hits": analysis.post_fold_hits(),
            },
            "warnings": [asdict(w) for w in analysis.warnings],
            "window_changes": [asdict(w) for w in analysis.window_changes],
            "report": report_data,
        }
        json.dump(payload, sys.stdout, indent=2)
        print()
    else:
        print_report(analysis, report_data)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
