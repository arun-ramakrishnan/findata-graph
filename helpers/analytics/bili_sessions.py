#!/usr/bin/env python3
"""Discover and aggregate billion-context sessions from the live proxy dashboard
and local logs.

Usage:
    python bench_data/code/bili_sessions.py
    python bench_data/code/bili_sessions.py --json
    python bench_data/code/bili_sessions.py --detail SID
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, UTC
from typing import Any
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from helpers.core.ranges import (  # noqa: E402
    RangeError,
    cutoff_from,
    parse_range,
)
from helpers.analytics.bili_analytics import (  # noqa: E402
    Analysis,
    log_rotation_family,
    parse_logs,
    fold_trigger_counts,
    parse_report,
    session_is_live,
)

RE_SES = re.compile(r"ses_[A-Za-z0-9]+")
LIVE_LOG = Path.home() / ".local" / "state" / "billion-context" / "bili.log"
ROOTS = [
    Path.home() / ".local" / "state" / "billion-context",
    Path(__file__).resolve().parents[2] / "bench_data" / "billion-context",
]
DASHBOARD_URLS = ["http://127.0.0.1:18787/__bili"]


@functools.lru_cache(maxsize=1)
def _dashboard_urls() -> tuple[str, ...]:
    """Discover proxy dashboards by walking /proc.

    Cached per-process: the PID set cannot change inside one CLI run, and the
    caller used to re-walk every process's `cmdline` once PER SESSION — 71 walks
    and 22,072 `read_text` calls for a 35-session table.
    """
    urls = ["http://127.0.0.1:18787/__bili"]
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            cmd = (pid_dir / "cmdline").read_text(errors="replace").replace("\x00", " ")
            if "billion-context" not in cmd:
                continue
            m = re.search(r"--port\s+(\d+)", cmd)
            if m:
                urls.append(f"http://127.0.0.1:{m.group(1)}/__bili")
        except OSError:
            continue
    return tuple(sorted(set(urls)))


@functools.lru_cache(maxsize=1)
def _fetch_sessions() -> tuple[dict, ...]:
    collected: list[dict] = []
    for base in _dashboard_urls():
        try:
            with urllib.request.urlopen(f"{base}/sessions", timeout=2) as r:  # noqa: S310
                collected.extend(json.load(r).get("sessions", []))
        except Exception:  # noqa: S112  # best-effort; an absent/unreachable proxy must not break the table
            continue
    # de-dup by id, keeping the entry with the newest lastSeen (different proxies
    # can expose the same session id with conflicting stale state)
    by_id: dict[str, dict] = {}
    for s in collected:
        sid = s.get("id")
        if sid is None:
            continue
        prev = by_id.get(sid)
        if prev is None or str(s.get("lastSeen", "")) > str(prev.get("lastSeen", "")):
            by_id[sid] = s
    return tuple(by_id.values())


def dashboard_sessions() -> list[dict]:
    return [dict(s) for s in _fetch_sessions()]


def dashboard_detail(sid: str) -> dict:
    return _fetch_detail(sid)


@functools.cache
def _fetch_detail(sid: str) -> dict:
    best = None
    for base in _dashboard_urls():
        try:
            with urllib.request.urlopen(f"{base}/sessions/{sid}/detail", timeout=2) as r:  # noqa: S310
                d = json.load(r)
            if best is None or str(d.get("lastSeen", "")) > str(best.get("lastSeen", "")):
                best = d
        except Exception:  # noqa: S112  # best-effort; an absent/unreachable proxy must not break the table
            continue
    return best or {}


@functools.lru_cache(maxsize=1)
def _compress_config() -> dict:
    """The proxy's view of the compress settings, or {}.

    `GET /__bili/config` exposes the config file's `compress` block. It is the
    ONLY source for `tierNudgeTokens` — the log states T1's bar (it is the
    `pendingT1=N/thr` denominator) but never T2's or T3's. Best-effort: an
    absent or unreachable proxy must not break the table.
    """
    for base in _dashboard_urls():
        try:
            with urllib.request.urlopen(f"{base}/config", timeout=2) as r:  # noqa: S310
                return json.load(r).get("compress", {}) or {}
        except Exception:  # noqa: S112  # best-effort; an absent proxy must not break the table
            continue
    return {}


def _tier_bar_override(cfg: dict, tier: str) -> int | None:
    """An explicit `tierNudgeTokens.tN` bar from the config, if set."""
    t = (cfg.get("tierNudgeTokens") or {}) if isinstance(cfg, dict) else {}
    for key in (tier.lower(), tier.upper()):
        v = t.get(key)
        if v:
            try:
                return int(v)
            except TypeError, ValueError:
                return None
    return None


def _with_tier_overrides(bars: dict[str, int], cfg: dict) -> dict[str, int]:
    """The config wins for ALL THREE tiers.

    The plugin's own rule:

        tierThresholds = {1: tierGrowthTokens?.t1 ?? nudgeGrowthTokens,
                          2: tierGrowthTokens?.t2 ?? round(nudgeGrowthTokens*1.5),
                          3: tierGrowthTokens?.t3 ?? round(nudgeGrowthTokens*1.5)}

    Two consequences the log alone cannot show:

    * The DERIVATION BASE is `nudgeGrowthTokens`, NOT T1's bar.  Once
      `tierNudgeTokens.t1` is set the two diverge, and deriving T2/T3 from
      T1's bar would give the wrong (too high) defaults.
    * The log's `pendingT1=N/thr` denominator is `nudgeGrowthTokens`, not T1's
      bar — so the log can never state T1's bar once T1 is decoupled.  The
      config is the only source for it.

    An absent or unreachable proxy leaves the log-derived bars alone.
    """
    out = dict(bars)
    base = cfg.get("nudgeGrowthTokens") if isinstance(cfg, dict) else None
    if base:
        try:
            base = int(base)
        except TypeError, ValueError:
            base = None
    if base:
        out["T2"] = round(base * 1.5)
        out["T3"] = round(base * 1.5)
    for tier in ("T1", "T2", "T3"):
        v = _tier_bar_override(cfg, tier)
        if v:
            out[tier] = v
    return out


@functools.lru_cache(maxsize=1)
def dashboard_stats_map() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for base in _dashboard_urls():
        try:
            with urllib.request.urlopen(f"{base}/stats", timeout=2) as r:  # noqa: S310
                data = json.load(r)
            for s in data.get("sessions", []):
                sid = s.get("id")
                if sid is None:
                    continue
                prev = out.get(sid)
                if prev is None or str(s.get("lastSeen", "")) > str(prev.get("lastSeen", "")):
                    out[sid] = s
        except Exception:  # noqa: S112  # best-effort; an absent/unreachable proxy must not break the table
            continue
    return out


def find_report(sid: str) -> Path | None:
    for root in ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file() and "cacherpt" in p.name and p.name.endswith(".md") and sid in p.name:
                return p
    return None


def file_logs_for_sid(sid: str) -> list[Path]:  # noqa: C901
    out: list[Path] = []
    for root in ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            name = p.name
            # The rotation family, not just the exact live filename: a
            # rotated `bili.log.old` (or `.1`, `.2.gz`) is matched by prefix
            # so a session older than one rotation window is still found.
            if (
                name.startswith("bili.log")
                or "billion-context-log" in name
                or "billion-context-full-log" in name
            ):
                out.append(p)
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            cmd = (pid_dir / "cmdline").read_text(errors="replace").replace("\x00", " ")
            if "billion-context" not in cmd:
                continue
            lp = (pid_dir / "cwd").resolve() / "bili.log"
            if lp.is_file():
                out.append(lp)
        except OSError:
            continue
    return sorted(set(out))


_SID_RE = re.compile(r"ses_[A-Za-z0-9]+|session-[0-9a-fA-F-]+|[0-9a-f]{8}-[0-9a-fA-F-]{27}")
_SID_INDEX: dict[str, list[Path]] | None = None


def sid_log_index() -> dict[str, list[Path]]:
    """session id -> log files mentioning it, built in ONE pass.

    The previous check read every candidate log in full for every session —
    35 sessions x ~12 MB of rotation history — and it could not read a
    `.gz` member at all. Scanning each file once also makes the rotated
    logs reachable, which is where every older session's `model=` lives.
    """
    global _SID_INDEX
    if _SID_INDEX is not None:
        return _SID_INDEX
    idx: dict[str, set[Path]] = {}
    seen: set[Path] = set()
    for p in file_logs_for_sid(""):
        # ROOTS overlap and the same file is matched by several name rules,
        # so dedupe on the RESOLVED path: without this one session mapped to
        # 200+ identical entries (measured 2026-10-17).
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        try:
            text = _read_log_text(p)
        except OSError:
            continue
        for sid in _SID_RE.findall(text):
            # a sub-session logs under its PARENT id; index both so
            # `session-<id>|sub:<id>` resolves to the same file
            idx.setdefault(sid, set()).add(rp)
            if "|" in sid:
                idx.setdefault(sid.split("|", 1)[0], set()).add(rp)
    _SID_INDEX = {k: sorted(v, key=lambda q: q.stat().st_mtime) for k, v in idx.items()}
    return _SID_INDEX


def _read_log_text(p: Path) -> str:
    if p.name.endswith(".gz"):
        import gzip

        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return p.read_text(errors="replace")


@functools.lru_cache(maxsize=4)
def _family_index(log_path: Path) -> dict[str, Analysis]:
    """Parse the rotation family ONCE and bucket every sample by session.

    This replaces a per-session `parse_logs(log_rotation_family(log_path),
    session_filter=sid)` — which rescanned the whole 15 MB family once per
    session. Measured on a 35-session table that was 4.1 s of the 7.7 s total,
    plus a full `log_path.read_text()` per session purely to test whether the
    session id appeared in the file at all.
    """
    whole = parse_logs(log_rotation_family(log_path))
    by_sid: dict[str, Analysis] = {}
    for n in whole.nudges:
        by_sid.setdefault(n.session, Analysis(log_path=whole.log_path)).nudges.append(n)
    for u in whole.usages:
        by_sid.setdefault(u.session, Analysis(log_path=whole.log_path)).usages.append(u)
    for f in whole.folds:
        by_sid.setdefault(f.session, Analysis(log_path=whole.log_path)).folds.append(f)
    for w in whole.window_changes:
        by_sid.setdefault(w.session, Analysis(log_path=whole.log_path)).window_changes.append(w)
    for warn in whole.warnings:
        by_sid.setdefault(warn.session, Analysis(log_path=whole.log_path)).warnings.append(warn)
    for bucket in by_sid.values():
        # Defensive: `parse_logs` already ts-sorts every series, so appending in
        # iteration order preserves order. Kept so the buckets stay sorted if
        # that guarantee is ever relaxed. (`nudges[-1]` is read as "newest" by
        # the callers, so a silent unsorted bucket would be a wrong answer,
        # not a slow one.)
        for series in (bucket.nudges, bucket.usages, bucket.folds, bucket.window_changes):
            series.sort(key=lambda s: s.ts)
        # Keys stay STRING-valued to match `parse_log` exactly: the dashboard
        # prints and `--json` consumers see `{"200000": 264}`, and a perf fix
        # must not change the shape of the data it is speeding up.
        bucket.intervals_seen = Counter(
            str(n.interval) for n in bucket.nudges if getattr(n, "interval", None)
        )
        # `parse_log` keeps this as a bare int, so it cannot be partitioned
        # post-hoc — but every violation also lands in `warnings` carrying its
        # session, so the per-session count is recoverable from there.
        bucket.reasoning_pair_violations = sum(
            1 for w in bucket.warnings if w.kind == "reasoning-pair-violated"
        )
    return by_sid


def _session_analysis(log_path: Path | None, sid: str) -> Analysis | None:
    """The bucket for `sid`, or None when the family has no samples for it.

    A sub-session (`<parent>|sub:<id>`) logs under its PARENT id, so the lookup
    has to use the parent or the bucket is empty — which is why the last
    sub-session row kept reporting an empty model.
    """
    if log_path is None:
        return None
    index = _family_index(log_path)
    return index.get(sid.split("|", 1)[0]) or index.get(sid)


def run_for_sid(sid: str) -> dict:
    d = dashboard_detail(sid)
    st = dashboard_stats_map().get(sid, {})
    report = find_report(sid)

    containing = sid_log_index().get(sid) or sid_log_index().get(sid.split("|", 1)[0], [])
    if not containing and LIVE_LOG.is_file():
        containing = log_rotation_family(LIVE_LOG)
    # Pick the log whose rotation family is the widest (covers the most
    # rotated siblings). `bili.log.old` (11 MB) is larger than `bili.log`
    # (6.8 MB) but its glob `bili.log.old*` misses `bili.log`, silently
    # dropping every session whose data lives mostly in the current file.
    log_path = (
        max(containing, key=lambda p: (len(log_rotation_family(p)), p.stat().st_size))
        if containing
        else None
    )

    switches = (
        st.get("modelSwitches", 0)
        + st.get("keySwitches", 0)
        + st.get("promptSwitches", 0)
        + st.get("projSwitches", 0)
    )

    row = {
        "session": sid,
        "title": d.get("title") or st.get("title"),
        "client": d.get("clientHint") or st.get("clientHint", ""),
        "protocol": d.get("protocol") or st.get("protocol", ""),
        "upstream": d.get("upstreamOrigin") or st.get("upstreamOrigin") or st.get("upstream", ""),
        "requests": d.get("requests", st.get("requests", 0)),
        # NOT `contextTokens`. Measured 2026-10-19 against the log on 13
        # sessions: `contextTokens` agreed with the real prompt size in 1 of 13
        # and was off by wildly varying factors elsewhere (378,583 vs 142,033;
        # 129,666 vs 389,079; 734,357 vs 201,084). `lastInputTokens` matched the
        # log EXACTLY on the one session where both were available. Whatever
        # `contextTokens` counts, it is not the context window occupancy.
        # Kept under its own key for reference; never rendered as ctxNow.
        "dashboard_context_tokens": d.get("contextTokens", st.get("contextTokens", 0)),
        "context_now": d.get("lastInputTokens", st.get("lastInputTokens", 0)),
        "cum_input": d.get("inputTokens", st.get("inputTokens", 0)),
        "cached_input": d.get("cachedTokens", st.get("cachedTokens", 0)),
        "saved": d.get("tokensSaved", st.get("tokensSaved", 0)),
        "hit_pct": d.get("cacheHitPct", st.get("cacheHitPct", 0)),
        "blocks": d.get("blocks", st.get("blocks", 0)),
        "switches": switches,
        "context_window": d.get("contextWindow", st.get("contextWindow", 0)),
        "new_content": d.get("newContent", 0),
        "comp_repay": d.get("compRepay", 0),
        "ttl_repay": d.get("ttlRepay", 0),
        "last_seen": d.get("lastSeen", st.get("lastSeen", "")),
        "context_now_src": "dashboard",
        "model": d.get("lastModel") or st.get("lastModel", ""),
        # Defaults here so a dashboard-only row still has the key: the column
        # and the banner both read it, and --json consumers must see a stable
        # shape. Overwritten from the log below when a log bucket exists.
        "reason_pair_violations": 0,
        "_viol_gap": None,
        "_viol_details": [],
        # The compress ladder, per tier. Defaults so a dashboard-only row keeps
        # a stable shape for --json consumers.
        "tier_fires": {"T1": 0, "T2": 0, "T3": 0},
        "tier_pending": {},
        "tier_bars": {},
        "tier_blocked": {},
        "log": str(log_path) if log_path else None,
        "report": str(report) if report else None,
        "source": "dashboard",
    }

    a = _session_analysis(log_path, sid)
    if a is not None:
        if report:
            parse_report(report)
        last_nudge = a.nudges[-1] if a.nudges else None
        if not row.get("client"):
            row["client"] = "log"
        if not row.get("protocol"):
            row["protocol"] = "?"
        if not row.get("upstream"):
            row["upstream"] = "?"
        row.update(
            {
                "requests": len(a.usages) or row["requests"],
                "log_hit_pct": a.overall_hit_pct(),
                "hit_pct": row.get("hit_pct") or a.overall_hit_pct(),
                "folds": len(a.folds),
                # Fold attribution per nudge kind (T1/T2/T3/esc/agent) —
                # which nudge asked for the fold, not the compress tool the
                # agent answered with. Same tags as `tier_fires`.
                "fold_triggers": dict(fold_trigger_counts(a.folds)),
                "folds_per_100k": a.folds_per_100k(),
                "intervals": dict(a.intervals_seen),
                "nudges": len(a.nudges),
                "t1_fired": sum(1 for n in a.nudges if n.fired_t1),
                "esc_fires": sum(1 for n in a.nudges if n.fired_esc),
                # The three ladder numbers, per tier: how many times the tier
                # fired, the compressible mass it is carrying, and the bar it
                # is measured against. T2/T3's bars are derived from the
                # resolved T1 bar unless the config sets them explicitly.
                "tier_fires": a.tier_fires(),
                "tier_pending": a.tier_pending(),
                "tier_bars": _with_tier_overrides(a.tier_bars(), _compress_config()),
                "tier_blocked": a.tier_blocked_counts(),
                # Ordering tests from the tier ladder: T2 needs t2_pen > t1_eff
                # (ord2), T3 needs t3_pen > t2_pen and t3_pen > t1_eff (ord3).
                # `tier_rung` is the tier that would fire next, STRANDED when a
                # pool is over its bar but blocked, else '-'. Mirrors
                # helpers/analytics/bili_tier_watch.py's ord2/ord3 flags.
                "tier_ord2": _tier_ord2(a.tier_pending()),
                "tier_ord3": _tier_ord3(a.tier_pending()),
                "tier_rung": _tier_rung(
                    a.tier_pending(), _with_tier_overrides(a.tier_bars(), _compress_config())
                ),
                "warnings": sorted(set(w.kind for w in a.warnings)),
                "reason_pair_violations": a.reasoning_pair_violations,
                "degenerate_folds": sum(1 for w in a.warnings if w.kind == "degenerate-fold"),
                "effective_interval": a.effective_interval(),
                "cum_input": row.get("cum_input") or sum(u.input_tok for u in a.usages),
                "cached_input": row.get("cached_input") or sum(u.cached_tok for u in a.usages),
                # The LAST request's prompt size is the context as of now.
                # `input_tok` is the full prompt (cached + new), which is what
                # the window limit applies to. Preferred over the dashboard
                # because the log is the ground truth and, unlike
                # `lastInputTokens`, needs no extra fetch.
                "context_now": a.usages[-1].input_tok if a.usages else 0,
                "context_now_src": ("log" if a.usages else row.get("context_now_src", "")),
                "model": row.get("model")
                or (last_nudge.model if last_nudge else row.get("model", "")),
                "saved": row.get("saved") or sum(f.saved_tok for f in a.folds),
                "source": ("log+dashboard" if row.get("source") == "dashboard" else "log"),
                # Where the two stores DISAGREE. The row is a merge of a
                # dashboard snapshot and a log parse with no marker saying
                # which number came from where — measured 2026-10-19 on
                # ses_eb005c4d7: requests 319 (dashboard) vs 432 (log),
                # lastModel None (dashboard), lastSeen 08:11 vs 13:41. A
                # silent preference is not provenance, so name the fields.
                "_disagreements": _disagreements({**st, **d}, a),
                # Raw proxy lines and the observed last-violation -> session-end
                # gap. Not columns: too long for the table, and the gap is only
                # meaningful once someone has decided to look.
                "_viol_details": [
                    w.detail for w in a.warnings if w.kind == "reasoning-pair-violated"
                ],
                "_viol_gap": _viol_gap(a),
                # Is the session still the newest thing in the log? A short
                # gap is NOT a short life — see `session_is_live`.
                "_viol_live": session_is_live(a),
            }
        )
    return row


#: Fields the log can independently verify. Dashboard-only fields (saved,
#: hit_pct, blocks) have no log twin, so they can never disagree and are not
#: listed — absence of a pair is not evidence of agreement.
_COMPARABLE: tuple[tuple[str, Callable[[Any], object]], ...] = (
    # row key, dashboard key, log value (callable over the Analysis)
    ("requests", lambda a: len(a.usages)),
    ("lastModel", lambda a: a.nudges[-1].model if a.nudges else ""),
)


def _disagreements(dash: dict, a) -> list[str]:
    """Fields where the dashboard snapshot and the log parse disagree.

    Three states, kept distinct on purpose:
      * key absent from the payload — no dashboard claim is held, say nothing
      * key present but falsy — the dashboard CLAIMED emptiness (`lastModel:
        None` against a session with 437 nudges); report as absent, never as
        agreement
      * both present and different — report both values

    Read from the RAW payload, never the merged row: `row["model"]` is already
    the log fallback when the dashboard is silent, so comparing it to the log is
    circular and always agrees.
    """
    out: list[str] = []
    for key, log_fn in _COMPARABLE:
        if key not in dash:
            continue
        dash_val = dash.get(key)
        log_val = log_fn(a)
        if not dash_val and not log_val:
            continue
        if not dash_val:
            out.append(f"{key}: dashboard absent, log={log_val or '-'}")
        elif dash_val != log_val:
            out.append(f"{key}: dashboard={dash_val!r} log={log_val!r}")
    return out


def _viol_gap_minutes_label(gap: str) -> float | None:
    """Parse `_viol_gap`'s "25 min" / "4.1 h" back into minutes, or None."""
    try:
        return float(str(gap).split()[0]) * (60 if "h" in str(gap) else 1)
    except ValueError, IndexError:
        return None


def _viol_outcome_lines(gap: str, live: bool = False) -> list[str]:
    """What the measured gap licenses us to say about THIS session.

    "already unwound" used to be asserted unconditionally, two lines below the
    measured gap it contradicted — on 2026-10-19 ses_eb005c4d7 printed 3.8 h
    under a warning that every violating session dies within ~30 min. The claim
    now follows the measurement.

    `live` short-circuits all of it: a session whose last log line is still
    the newest thing in the log has not ended, so "terminal" is a statement
    about a future that has not happened. Measured 2026-10-19 on
    ses_ec7af063cffew2a8xvEcV142n6, which was mis-called terminal while
    still answering.
    """
    if live:
        return [
            "Session is STILL RUNNING (its last log line is the newest in the",
            "log), so the outcome is not known yet.",
        ]
    mins = _viol_gap_minutes_label(gap)
    if mins is None:
        return ["Outcome UNKNOWN (no log line after the last violation)."]
    if mins < 60:
        return [
            "Terminal: this session stopped within an hour of its violation,",
            "and the log kept running afterwards.",
        ]
    return [
        "RECOVERED: the session outlived its violation, so the",
        "'violations always kill the session' rule does not hold here.",
    ]


def _viol_gap(a) -> str:
    """How long the session kept going after its LAST reasoning-pair violation.

    Measured across the retained log the gap is bimodal, not one-directional:
    3 / 10 / 29 min for three sessions that died, then 3.8 h for ses_eb005c4d7
    on 2026-10-19 (upstream https://api.z.ai, one of the two providers that
    recover). So this number, not a fixed rule, decides whether a violation
    was terminal for THIS session.
    """
    viols = [w.ts for w in a.warnings if w.kind == "reasoning-pair-violated"]
    if not viols:
        return "n/a"
    stamps = [s.ts for s in (*a.nudges, *a.usages) if s.ts > max(viols)]
    if not stamps:
        return "unknown (no log line after the last violation)"
    try:
        end = datetime.strptime(max(stamps), "%Y-%m-%dT%H:%M:%S.%fZ")
        last = datetime.strptime(max(viols), "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return "unknown (unparsable timestamp)"
    mins = (end - last).total_seconds() / 60
    return f"{mins:.0f} min" if mins < 90 else f"{mins / 60:.1f} h"


def _parse_last_seen(value) -> datetime | None:
    """Parse a dashboard `lastSeen` into an aware datetime, or None if unusable."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp


def main(argv: list[str]) -> int:  # noqa: C901
    ap = argparse.ArgumentParser(description="Discover and aggregate billion-context sessions.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--detail", metavar="SID")
    ap.add_argument(
        "--aggregate", action="store_true", help="print cross-session totals after the table"
    )
    ap.add_argument("--markdown", metavar="PATH", help="write a Markdown ledger to PATH")
    ap.add_argument(
        "--since",
        default="7d",
        metavar="RANGE",
        help=(
            "only sessions whose lastSeen is within RANGE (default 7d). "
            "Accepts 30d / 6mo / 1y / 12h / 45min, or a bare number of days. "
            "0 disables the window. The log family grows monotonically, so an "
            "unbounded table gets slower every day for sessions nobody will "
            "ever read."
        ),
    )
    ap.add_argument(
        "--all",
        dest="since",
        action="store_const",
        const="0",
        help="ignore the lastSeen window; every session the proxy or logs know about",
    )
    args = ap.parse_args(argv)

    try:
        since_text = str(args.since)
        disabled = not since_text or parse_range(since_text)[0] == 0
        since_cutoff = None if disabled else cutoff_from(since_text)
    except RangeError as exc:
        print(f"--since: {exc}", file=sys.stderr)
        return 2

    meta_sessions = dashboard_sessions()
    if meta_sessions:
        rows = [run_for_sid(m["id"]) for m in meta_sessions]
    else:
        # dashboard unavailable: fall back to every session the logs name,
        # across the whole rotation family, not just the current file
        sids = sorted(sid_log_index())
        rows = [run_for_sid(s) for s in sids]

    rows.sort(key=lambda r: r.get("last_seen") or "", reverse=True)

    if since_cutoff is not None:
        kept, dropped = [], 0
        for r in rows:
            stamp = _parse_last_seen(r.get("last_seen"))
            # A row with no parsable lastSeen is KEPT: the window is a
            # performance default, and silently dropping rows we cannot date
            # would hide sessions rather than merely defer them.
            if stamp is None or stamp >= since_cutoff:
                kept.append(r)
            else:
                dropped += 1
        if dropped:
            print(
                f"# {dropped} session(s) last seen before "
                f"{since_cutoff:%Y-%m-%d} ({since_text}) hidden — use --all to show",
                file=sys.stderr,
            )
        rows = kept

    if args.detail:
        row = next((r for r in rows if args.detail in str(r["session"])), None)
        if not row:
            print(f"session not found: {args.detail}", file=sys.stderr)
            return 2
        log = row.get("log")
        report = row.get("report")
        if not log:
            print("no log available for that session", file=sys.stderr)
            return 2
        cmd = [sys.executable, str(HERE / "bili_analytics.py"), log, "--session", row["session"]]
        if report:
            cmd.append(report)
        print("[SESSION]")
        for label, key in (
            ("client", "client"),
            ("model", "model"),
            ("protocol", "protocol"),
            ("upstream", "upstream"),
        ):
            print(f"  {label:<16}: {row.get(key) or '-'}")
        print(f"  {'requests':<16}: {row.get('requests', 0)}")
        print(
            f"  {'context now':<16}: {row.get('context_now', 0):,} "
            f"(from {row.get('context_now_src') or 'unknown'}; "
            f"dashboard's `contextTokens` was "
            f"{row.get('dashboard_context_tokens', 0):,} — a different quantity)"
        )
        print(
            f"  {'nudges/folds':<16}: {row.get('nudges', 0)} / {row.get('folds', 0)}"
            f"  (degenerate {row.get('degenerate_folds', 0)}, blocks {row.get('blocks', 0)})"
        )
        # The compress ladder, per tier. The three numbers a tier is tuned by:
        # how many times it fired, the compressible mass it is carrying, and
        # the bar that mass is measured against.
        fires = row.get("tier_fires") or {}
        pend = row.get("tier_pending") or {}
        blocked = row.get("tier_blocked") or {}
        print("  [TIERS]")
        for tier in ("T1", "T2", "T3"):
            p = pend.get(tier)
            bl = blocked.get(tier[-1], 0)
            bits = [
                f"fires {fires.get(tier, 0):>3}",
                f"pending {p:>9,}" if p is not None else "pending        -",
            ]
            if bl:
                bits.append(f"(cadence-blocked {bl}x)")
            print(f"    {tier}  " + "   ".join(bits))
        print(
            "    "
            + f"ord2={'Y' if row.get('tier_ord2') else 'N'} ord3={'Y' if row.get('tier_ord3') else 'N'} "
            + f"rung={row.get('tier_rung')}"
        )
        if row.get("_disagreements"):
            print("  dashboard/log disagree on:")
            for d in row["_disagreements"]:
                print(f"    - {d}")
        # General, not model-gated: DeepSeek and zAI were hit too. The recovery
        # difference is real but it makes the check weaker, not stronger — a
        # violation is worth surfacing whichever model produced it.
        if row.get("reason_pair_violations"):
            n = row["reason_pair_violations"]
            print(sep_line())
            print(f"  !! REASONING-PAIR VIOLATIONS: {n}")
            print("     Assistant tool-call messages without a reasoning block are")
            print("     mixed with messages that carry one. Strict-echo upstreams")
            print("     reject the request. DeepSeek and zAI recover; the rest")
            print("     crash the session.")
            gap = row.get("_viol_gap") or "?"
            live = bool(row.get("_viol_live"))
            if live:
                print("     Measured: no log line after its last violation —")
                print("     the session is still the newest thing in the log.")
            else:
                print(f"     Measured: this session ran {gap} past its last violation.")
            for line in _viol_outcome_lines(gap, live):
                print(f"     {line}")
            print("     proxy log lines:")
            for w in (row.get("_viol_details") or [])[:5]:
                print(f"       {w}")
        else:
            print(f"  {'reason-pair viol':<16}: 0  (clean)")
        print(sep_line())
        cmd += ["--session", row["session"]]
        raise SystemExit(subprocess.call(cmd))  # noqa: S603  # list-form call, shell=False (default); argv is a fixed script path plus a validated session id

    if args.json:
        # stderr, never stdout: `--json` is a machine-readable path and a human
        # callout in front of the array breaks every consumer that pipes it.
        # The `reason_pair_violations` field carries the same information.
        print_violations(rows, args, stream=sys.stderr)
        json.dump(rows, sys.stdout, indent=2)
        print()
        return 0

    print_table(rows)
    return print_aggregate(rows, args)


# Table geometry: (key, header, align, natural_cap, shrinkable)
#
# Widths are FITTED to the terminal at print time rather than hardcoded.
# `f"{s:<n}"` pads but never truncates, so one over-long value shifts every
# later column: dsh ids are 44 chars and sub-sessions (`...|sub:<id>`) reach 65
# against the old 30-wide column — that is what raggeded this table. Widening
# every column to the worst case instead produced a 263-column wall. So: take
# each column's natural width from the DATA, shrink only the SHRINKABLE ones
# until the row fits, and give `session` a 30-char cap so one lane's id format
# cannot widen the whole table. Counters and timestamps keep their full width.
# `upstream` is deliberately NOT here — it is a constant per proxy endpoint
# (https://opencode.ai, http://127.0.0.1:42593) carrying no per-session signal.
# It is shown in --detail instead, where it can be read without costing a
# column on every row.
_COLUMNS: tuple[tuple[str, str, str, int, bool], ...] = (
    # capped at the opencode id length (ses_ + 26 hex = 30) so every lane
    # reads as one column; dsh ids (session-<uuid>, 44 chars) and sub-sessions
    # (`...|sub:<id>`, 65) truncate with a visible `…` instead of forcing the
    # whole table 40 columns wider for one row's benefit. NOT shrinkable —
    # the id is the row's identity, so it keeps its 30 even on a narrow
    # terminal; client/model/upstream absorb the squeeze. --json and
    # --markdown carry the full id.
    ("session", "session", "<", 30, False),
    ("client", "client", "<", 34, True),
    ("model", "model", "<", 40, True),
    ("protocol", "proto", "<", 12, False),
    ("requests", "reqs", ">", 7, False),
    ("context_now", "ctxNow", ">", 10, False),
    ("cum_input", "cumInput", ">", 11, False),
    ("saved", "saved", ">", 8, False),
    ("hit_pct", "hit%", ">", 6, False),
    ("switches", "switches", ">", 8, False),
    # FOUR distinct counts, not one. `compacts` was showing `blocks` beside a
    # `saved` figure summed over FOLDS, so a single row mixed two units and
    # overstated compactions ~3x. Measured 2026-10-17 on ses_ec7af063:
    #   nudges 750  folds 6  blocks 18  degenerate 1
    #   nudges 2203 folds 16 blocks 57  degenerate 3
    # blocks tracks folds at ~3x (a fold writes several summary blocks); only
    # `folds` is the number of times the context was actually shrunk, and
    # `degenerate` is the catastrophe count — a fold that shrank >=80% and
    # busted the cache (post-fold hit hit 0%).
    ("nudges", "nudges", ">", 6, False),
    ("folds", "folds", ">", 6, False),
    ("degenerate_folds", "degen", ">", 6, False),
    ("blocks", "blocks", ">", 7, False),
    ("esc_fires", "esc", ">", 5, False),
    # The compress ladder, per tier: `<fires>f/<pending>` — how many times the
    # tier injected a nudge, and the compressible mass it is carrying. The
    # pending is the number compared against THAT TIER'S BAR, so a tier that
    # never cleared its bar shows `-`: T1's mass is on every nudge line, but
    # T2/T3 appear in the log's `ready:` hint only while they qualify. Three
    # columns because the ladder is the thing being tuned and a single
    # `T1/T2/T3` blob cannot be read column-wise.
    ("tier_t1", "t1", "<", 9, False),
    ("tier_t2", "t2", "<", 9, False),
    ("tier_t3", "t3", "<", 9, False),
    # `*N` when the session has recorded reasoning-pair violations, blank when
    # none. At the far right and unsqueezeable so a flagged session is
    # impossible to miss — `*` pops against a column of numbers, and the count
    # says whether it is one blip or a trend. The marker is NOT a death
    # sentence: of 4 violating sessions measured, 3 died within 3-29 min and one
    # (ses_eb005c4d7, z.ai) ran 3.8 h past its violation. `--detail` prints the
    # session's own measured gap.
    ("reason_pair_violations", "viol", ">", 6, False),
)
_LAST_SEEN = ("lastSeen", "<", 19, False)
_MIN_AVAIL, _MAX_AVAIL = 96, 200
#: Floors for the SHRINKABLE columns. The fitter's default floor is the
#: header length, which lets `model` shrink to 5 chars — "deep…" — and the lane
#: becomes unidentifiable. The tier columns added 17 chars of fixed width, so
#: without a floor the squeeze lands on the one column that names the lane.
#: A lane is worth 14 chars of model name; the rest of the table is numbers.
_MIN_WIDTH = {"model": 14, "client": 7}


def sep_line() -> str:
    return "=" * 78


_EMPTY = "-"
# Counters default to 0 like the original formatter did; only genuinely
# absent STRINGS get the `-` placeholder.
_COUNTERS = frozenset({"requests", "context_now", "cum_input", "saved", "switches", "blocks"})


# `client` carries a whole runtime fingerprint for some lanes
# (`ZCode/3.15.3 ai-sdk/provider-utils/4.0.27 runtime/node.js/24`), of which
# only the leading product token is signal — the rest is noise that forced the
# column wide enough to truncate every other lane's name. Shortened for the
# table only; --json and --markdown keep the full string.
_FIRST_TOKEN_ONLY = frozenset({"client"})


def _compact_tokens(v: int | None) -> str:
    """A token count in the fewest characters that still reads as a number."""
    if v is None:
        return "-"
    if v >= 10000:
        return f"{v // 1000}k"
    return str(v)


def _tier_cell(row: dict, tier: str) -> str:
    """`f{fires}` for one tier of the compress ladder — how many times the
    tier's nudge fired. Pending/bar detail lives under `[TIERS]` in --detail."""
    fires = (row.get("tier_fires") or {}).get(tier, 0)
    return f"{fires}f"


def _tier_ord2(pend: Mapping[str, int | None]) -> bool:
    """ord2 = t2_pen > t1_eff: T2's ordering test must clear before T2 can fire."""
    t1e = pend.get("T1")
    t2p = pend.get("T2")
    return bool(t1e is not None and t2p is not None and t2p > t1e)


def _tier_ord3(pend: Mapping[str, int | None]) -> bool:
    """ord3 = t3_pen > t2_pen and t3_pen > t1_eff: T3's ordering test."""
    t1e = pend.get("T1")
    t2p = pend.get("T2")
    t3p = pend.get("T3")
    return bool(t1e is not None and t2p is not None and t3p is not None and t3p > t2p and t3p > t1e)


def _tier_rung(pend: Mapping[str, int | None], bars: dict[str, int]) -> str:
    """Which tier would fire next: T1/T2/T3 if armed, STRANDED if a pool is
    over its bar but blocked by ordering/cadence, else '-'."""
    t1e = pend.get("T1")
    t2p = pend.get("T2")
    t3p = pend.get("T3")
    b1 = bars.get("T1")
    b2 = bars.get("T2")
    b3 = bars.get("T3")
    t1_armed = t1e is not None and b1 is not None and t1e >= b1
    if t1_armed:
        return "T1"
    t2_armed = t2p is not None and b2 is not None and t2p >= b2 and _tier_ord2(pend)
    if t2_armed:
        return "T2"
    t3_armed = t3p is not None and b3 is not None and t3p >= b3 and _tier_ord3(pend)
    if t3_armed:
        return "T3"
    over_bar = (
        (t1e is not None and b1 is not None and t1e >= b1)
        or (t2p is not None and b2 is not None and t2p >= b2)
        or (t3p is not None and b3 is not None and t3p >= b3)
    )
    return "STRANDED" if over_bar else "-"


def _natural(row: dict, key: str) -> str:
    """The cell's text. hit_pct keeps one decimal — the store may hand back
    int 97, and str() of that loses the decimal the column always showed."""
    if key == "hit_pct":
        return f"{(row.get('hit_pct') or 0):.1f}"
    if key in ("tier_t1", "tier_t2", "tier_t3"):
        return _tier_cell(row, key[-2:].upper())
    if key == "reason_pair_violations":
        n = row.get(key) or 0
        return f"*{n}" if n else ""
    v = row.get(key, 0 if key in _COUNTERS else "")
    if v is None:
        return ""
    s = str(v)
    if key in _FIRST_TOKEN_ONLY:
        # `ZCode/3.15.3 ai-sdk/provider-utils/4.0.27 ...` -> `ZCode`: the
        # leading product token, minus its version. `opencode` and `dsh`
        # carry no version, so the same rule leaves them untouched.
        s = s.split()[0].split("/")[0] if s.split() else ""
    return s


def _fit_widths(rows: list[dict], avail: int) -> list[int]:
    """Natural widths from the data, shrunk on SHRINKABLE columns until the
    rendered row fits `avail`. No column drops below its header."""
    widths = []
    for key, hdr, _a, cap, _s in _COLUMNS:
        longest = max([len(hdr), *(len(_natural(r, key)) for r in rows)])
        widths.append(min(longest, cap))
    widths.append(_LAST_SEEN[2])
    floor = [max(len(h), _MIN_WIDTH.get(k, 0)) for k, h, _a, _c, _s in _COLUMNS] + [
        len(_LAST_SEEN[0])
    ]
    flex = [i for i, spec in enumerate(_COLUMNS) if spec[4]]
    gap = 2 * (len(widths) - 1)
    while sum(widths) + gap > avail:
        cand = [i for i in flex if widths[i] > floor[i]]
        if not cand:
            break
        widths[max(cand, key=lambda i: widths[i])] -= 1
    return widths


def _cell(text: str, width: int, align: str) -> str:
    """Pad to `width`, truncating FIRST and marking the cut with `…` so a
    shortened value is visible rather than silently wrong.

    An EMPTY cell renders as `-` rather than as blank padding: a missing
    model used to leave a wide gap between client and protocol that read as
    a broken layout instead of as an absent value.
    """
    if not text.strip():
        text = _EMPTY
    s = text[: width - 1] + "\u2026" if len(text) > width else text
    return f"{s:{align}{width}}"


def _last_seen(r: dict) -> str:
    ls = (r.get("last_seen") or "").replace("T", " ").replace("Z", "")
    try:
        from datetime import datetime

        lsdt = datetime.fromisoformat(ls)
        if lsdt.tzinfo is None:
            lsdt = lsdt.replace(tzinfo=UTC)
        return lsdt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return (r.get("last_seen") or "")[:19].replace("T", " ")


def _row_cells(r: dict, widths: list[int]) -> list[str]:
    cells = [_cell(_natural(r, spec[0]), widths[i], spec[2]) for i, spec in enumerate(_COLUMNS)]
    cells.append(_cell(_last_seen(r), widths[-1], _LAST_SEEN[1]))
    return cells


def print_table(rows: list[dict]) -> None:
    import shutil

    cols = shutil.get_terminal_size(fallback=(160, 24)).columns
    widths = _fit_widths(rows, max(_MIN_AVAIL, min(cols - 1, _MAX_AVAIL)))
    header = [_cell(spec[1], widths[i], spec[2]) for i, spec in enumerate(_COLUMNS)]
    header.append(_cell(_LAST_SEEN[0], widths[-1], _LAST_SEEN[1]))
    print_violations(rows, None)
    print("  ".join(header).rstrip())
    for r in rows:
        print("  ".join(_row_cells(r, widths)).rstrip())
    print(
        "tier cells: <fires>f/<pending tokens>  — pending is the compressible "
        "mass vs that tier's bar; '-' means the tier is carrying nothing"
    )


def print_violations(rows: list[dict], args, stream=None) -> None:
    """ONE line above the table. The `viol` column is the indicator; the essay
    lives in `--detail <session>`.

    A reasoning-pair violation is worth surfacing at the top of the default
    view: strict-echo upstreams reject the request, and 3 of 4 violating
    sessions measured ended within 3-29 minutes of their last violation. It is
    NOT a guaranteed kill — the 4th (ses_eb005c4d7, z.ai) ran 3.8 h past it —
    so the count is what belongs here and `--detail` carries the session's own
    measured gap. Keeping it to one line is what makes the default view
    readable when the answer is usually `-`.
    """
    hit = [r for r in rows if r.get("reason_pair_violations")]
    if not hit:
        return
    # Sub-sessions share the parent transcript, so a violation on `session-…`
    # also shows on `session-…|sub:<id>`. Collapse on the parent so the count
    # names transcripts at risk, not rows.
    seen: dict[str, dict] = {}
    for r in sorted(hit, key=lambda r: -r["reason_pair_violations"]):
        seen.setdefault(r.get("session", "").split("|", 1)[0], r)
    out = stream or sys.stdout
    marks = ", ".join(
        f"*{r['reason_pair_violations']} {r.get('session', '')}" for r in seen.values()
    )
    print(
        f"!! reasoning-pair violation — {len(seen)} session(s) flagged: {marks}"
        f"\n   bili_sessions.py --detail <session>",
        file=out,
    )


def print_aggregate(rows: list[dict], args) -> int:
    """Cross-session totals + optional markdown ledger."""
    # aggregate across sessions
    total_reqs = sum(r.get("requests", 0) for r in rows)
    total_input = sum(r.get("cum_input", 0) for r in rows)
    total_cached = sum(r.get("cached_input", 0) for r in rows)
    total_saved = sum(r.get("saved", 0) for r in rows)
    total_ctx = sum(r.get("context_now", 0) for r in rows)
    agg_hit = (100.0 * total_cached / total_input) if total_input else 0.0
    agg_text = (
        f"AGGREGATE: sessions={len(rows)} requests={total_reqs} "
        f"cumInput={total_input} cached={total_cached} hit={agg_hit:.2f}% "
        f"contextNow={total_ctx} saved={total_saved} blocks={sum(r.get('blocks', 0) for r in rows)}"
    )

    if args.aggregate or args.markdown:
        print()
        print(agg_text)

    if args.markdown:
        path = Path(args.markdown)
        lines = [
            "# billion-context session ledger",
            "",
            agg_text,
            "",
            "| session | client | model | proto | upstream | reqs | ctxNow | cumInput | saved | hit% | switches | nudges | folds | degen | blocks | lastSeen |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
        for r in rows:
            ls = (r.get("last_seen") or "").replace("T", " ").replace("Z", "")
            try:
                from datetime import datetime

                lsdt = datetime.fromisoformat(ls)
                if lsdt.tzinfo is None:
                    lsdt = lsdt.replace(tzinfo=UTC)
                ls = lsdt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ls = (r.get("last_seen") or "")[:19].replace("T", " ")
            lines.append(
                f"| {r.get('session', '')} | {r.get('client', '')} | {r.get('model', '')} | {r.get('protocol', '')} | "
                f"{r.get('upstream', '')} | {r.get('requests', 0)} | {r.get('context_now', 0)} | "
                f"{r.get('cum_input', 0)} | {r.get('saved', 0)} | {(r.get('hit_pct') or 0):.1f} | "
                f"{r.get('switches', 0)} | {r.get('nudges', 0)} | {r.get('folds', 0)} | "
                f"{r.get('degenerate_folds', 0)} | {r.get('blocks', 0)} | {ls} |"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
