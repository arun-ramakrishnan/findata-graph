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
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


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
    fired_t1: bool = False
    fired_t2: bool = False
    fired_t3: bool = False


@dataclass
class FoldEvent:
    ts: str
    session: str
    saved_tok: int
    shrink_pct: Optional[float] = None
    blocks: Optional[int] = None
    anchor_tok: Optional[int] = None
    post_ctx_tok: Optional[int] = None


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
    report_path: Optional[str] = None
    nudges: list[NudgeSample] = field(default_factory=list)
    folds: list[FoldEvent] = field(default_factory=list)
    usages: list[CacheUsage] = field(default_factory=list)
    window_changes: list[WindowChange] = field(default_factory=list)
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

    def overall_hit_pct(self) -> Optional[float]:
        total_in = sum(u.input_tok for u in self.usages)
        total_cached = sum(u.cached_tok for u in self.usages)
        if total_in == 0:
            return None
        return 100.0 * total_cached / total_in

    def effective_interval(self) -> Optional[int]:
        for n in reversed(self.nudges):
            if n.interval:
                return n.interval
        return None

    def nudge_reason_counts(self) -> Counter:
        c: Counter = Counter()
        for n in self.nudges:
            if "T1 effective" in n.reason:
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
            for u in self.usages[idx:idx + window]:
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
            end = (
                _parse_ts(raw[idx + 1].start_ts)
                if idx + 1 < len(raw)
                else datetime.max
            )
            ep.folds = [
                f for f in self.folds
                if start <= _parse_ts(f.ts) < end
            ]

        return raw


# ---------------------------------------------------------------------------
# Parsing — tuned for:
#   2026-10-13T03:28:26.687Z [info] [sess=ses_...] [v=0.1.195] [ses_...] <message>
# ---------------------------------------------------------------------------

RE_PREFIX = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z)\s+"
    r"\[(?P<level>info|warn|debug|error)\]\s+"
    r"(?:\[sess=(?P<sess1>[^\]]+)\]\s+)?"
    r"(?:\[v=[^\]]+\]\s+)?"
    r"(?:\[(?P<sess2>[^\]]+)\]\s+)?"
    r"(?P<body>.*)$"
)

RE_NUDGE_BODY = re.compile(
    r"nudge (?:idle|INJECT T(?P<tier>[123])):\s*"
    r"usage=(?P<usage_pct>[\d.]+)%\s*\((?P<usage_tok>\d+)/(?P<window>\d+)\),\s*"
    r"growth=(?P<growth>-?\d+)/(?P<floor>\d+)\s*"
    r"\(ref=(?P<ref>\d+),\s*interval=(?P<interval>\d+)\),\s*"
    r"pendingT1=(?P<pending>\d+)/(?P<pending_thr>\d+)\s*"
    r"model=(?P<model>[^,]+),\s*reason=\"(?P<reason>[^\"]*)\""
)

RE_COMPRESS_OBS_BODY = re.compile(
    r"\[acp-compress-obs\] shrink (?P<shrink>[\d.]+)% "
    r"\(~(?P<saved>\d+)/(?P<total>\d+) tok\).*?blocks=(?P<blocks>\d+) "
    r"anchor≈(?P<anchor>\d+) tok.*?postCtx≈(?P<post>\d+)"
)

RE_FOLD_APPLIED_BODY = re.compile(
    r"\[Compressed [^\]]+ → (?P<blocks>\d+) block\(s\),\s*~(?P<saved>\d+) tokens saved"
)

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


def parse_log(path: Path, session_filter: Optional[str] = None) -> Analysis:
    a = Analysis(log_path=str(path))
    seen_folds: list[tuple[datetime, int, int]] = []

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            pm = RE_PREFIX.match(raw)
            if not pm:
                continue
            ts = pm["ts"]
            session = pm["sess1"] or pm["sess2"] or "?"
            if session_filter and session != session_filter:
                continue
            body = pm["body"]

            m = RE_NUDGE_BODY.search(body)
            if m:
                g = m.groupdict()
                a.nudges.append(NudgeSample(
                    ts=ts, session=session,
                    usage_tok=int(g["usage_tok"]),
                    usage_pct=float(g["usage_pct"]),
                    growth=int(g["growth"]),
                    ref=int(g["ref"]),
                    interval=int(g["interval"]),
                    pending_t1=int(g["pending"]),
                    pending_thr=int(g["pending_thr"]),
                    model=g["model"],
                    reason=g["reason"],
                    fired_t1=(g["tier"] == "1"),
                    fired_t2=(g["tier"] == "2"),
                    fired_t3=(g["tier"] == "3"),
                ))
                a.intervals_seen[g["interval"]] += 1
                continue

            m = RE_COMPRESS_OBS_BODY.search(body)
            if m:
                g = m.groupdict()
                saved = int(g["saved"])
                blocks = int(g["blocks"])
                seen_folds.append((_parse_ts(ts), saved, blocks))
                a.folds.append(FoldEvent(
                    ts=ts, session=session,
                    saved_tok=saved,
                    shrink_pct=float(g["shrink"]),
                    blocks=blocks,
                    anchor_tok=int(g["anchor"]),
                    post_ctx_tok=int(g["post"]),
                ))
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
                a.folds.append(FoldEvent(
                    ts=ts, session=session,
                    saved_tok=saved,
                    blocks=blocks,
                ))
                continue

            if RE_DEGENERATE_BODY.search(body):
                a.warnings.append(Warning(
                    ts=ts, session=session,
                    kind="degenerate-fold",
                    detail=body[:300],
                ))
                continue

            m = RE_USAGE_BODY.search(body)
            if m:
                g = m.groupdict()
                a.usages.append(CacheUsage(
                    ts=ts, session=session,
                    input_tok=int(g["input"]),
                    cached_tok=int(g["cached"]),
                    hit_pct=float(g["hit"]),
                    fold_new=bool(g["fold_new"]),
                ))
                continue

            m = RE_WINDOW_BODY.search(body)
            if m:
                g = m.groupdict()
                a.window_changes.append(WindowChange(
                    ts=ts, session=session,
                    old=g["old"], new=g["new"],
                ))
                continue

            if RE_REASONING_VIOLATION_BODY.search(body):
                a.reasoning_pair_violations += 1
                a.warnings.append(Warning(
                    ts=ts, session=session,
                    kind="reasoning-pair-violated",
                    detail=body[:300],
                ))

    return a


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
RE_REPORT_HIT_BY_SIZE = re.compile(
    r"hit% by input size \(median\):\s*(?P<body>[^\n]+)"
)
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

def fmt_pct(v: Optional[float]) -> str:
    return f"{v:.2f}%" if v is not None else "n/a"


def print_report(a: Analysis, report: Optional[dict]) -> None:
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
        print(f"  report grand hit      : {g['hit_pct']:.2f}% "
              f"({g['cached_M']}M / {g['input_M']}M)")

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
        print(f"  {'interval':>9}  {'nudges':>7}  {'T1 fired':>8}  "
              f"{'folds':>6}  {'fold/100K':>10}  window")
        for ep in eps:
            growth = 0
            if len(ep.nudges) >= 2:
                growth = max(0, ep.nudges[-1].ref - ep.nudges[0].ref)
            fpk = (len(ep.folds) * 100_000 / growth) if growth else 0.0
            window = f"{ep.start_ts[11:19]}→{ep.end_ts[11:19]}"
            print(f"  {ep.interval:>9}  {ep.nudge_count:>7}  {ep.t1_fired:>8}  "
                  f"{len(ep.folds):>6}  {fpk:>9.2f}  {window}")
        print("  → compare the 200000 epoch to the 50000/100000 epochs; "
              "lower fold/100K is the goal")

    # --- nudge behavior --------------------------------------------------
    print("\n[NUDGE BEHAVIOR]")
    print(f"  nudge samples         : {len(a.nudges)}")
    rc = a.nudge_reason_counts()
    for k, v in rc.most_common():
        print(f"    {k:<34}: {v}")
    fired = sum(1 for n in a.nudges if n.fired_t1)
    print(f"  T1 nudge fired        : {fired}")

    # --- folds -----------------------------------------------------------
    print("\n[FOLDS]")
    print(f"  total folds           : {len(a.folds)}")
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
        print(f"  median saved / fold   : {sorted(saved)[len(saved)//2]:,}")
        if shrinks:
            print(f"  median shrink %       : {sorted(shrinks)[len(shrinks)//2]:.1f}%")
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
        print(f"    median: {sorted(pf)[len(pf)//2]:.2f}%")
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
            print(f"  max pendingT1 vs last interval: {max_pending:,} / {last_interval:,}"
                  f"  (never fires if max < interval)")
        if ready_blocked:
            print(f"  WARNING: {ready_blocked} ready-but-blocked nudge(s) — "
                  f"growth floor may be too high or cadence too restrictive")
        if len(models) > 1:
            print(f"  WARNING: {len(models)} distinct models in one session "
                  f"— nudge/window behavior is not directly comparable")
        if len(a.window_changes) >= 3:
            print(f"  WARNING: {len(a.window_changes)} window changes "
                  f"— interval/extent context is volatile")
        eps = a.epochs()
        if len(eps) > 1:
            # folds that happened under the lowest interval era
            low_interval_epochs = [ep for ep in eps if ep.interval < 150_000]
            low_folds = sum(len(ep.folds) for ep in low_interval_epochs)
            if low_folds:
                print(f"  WARNING: {low_folds} fold(s) still happened under "
                      f"interval <150K — config did not fully take effect")
            # final epoch density
            last_ep = eps[-1]
            if last_ep.nudges and len(last_ep.nudges) >= 2:
                growth = max(0, last_ep.nudges[-1].ref - last_ep.nudges[0].ref)
                fpk = (len(last_ep.folds) * 100_000 / growth) if growth else 0.0
                if fpk > 3:
                    print(f"  WARNING: final epoch fold density {fpk:.2f}/100K "
                          f"> 3 — tuning not yet effective")
                else:
                    print(f"  OK: final epoch fold density {fpk:.2f}/100K ≤ 3")

    # --- efficiency -------------------------------------------------------
    print("\n[EFFICIENCY]")
    if a.folds:
        saved = [f.saved_tok for f in a.folds]
        print(f"  total saved (raw count, may double-count parser duplicates): "
              f"{sum(saved):,}")
        print(f"  median / min / max saved  : "
              f"{sorted(saved)[len(saved)//2]:,} / {min(saved):,} / {max(saved):,}")
        # fold recovery: requests until cache hit ≥90% after each fold
        fold_times = sorted(_parse_ts(f.ts) for f in a.folds)
        usage_times = [_parse_ts(u.ts) for u in a.usages]
        recovery_counts: list[int] = []
        for ft in fold_times:
            idx = next((i for i, ut in enumerate(usage_times) if ut >= ft), None)
            if idx is None:
                continue
            needed = 0
            for u in a.usages[idx:idx + 20]:
                needed += 1
                if u.hit_pct >= 90:
                    break
            else:
                needed = 20
            recovery_counts.append(needed)
        if recovery_counts:
            print(f"  recovery requests to ≥90% hit (per fold): {recovery_counts}")
            print(f"  median recovery requests   : "
                  f"{sorted(recovery_counts)[len(recovery_counts)//2]}")
        anchors = [f.anchor_tok for f in a.folds if f.anchor_tok is not None]
        if anchors:
            print(f"  anchor token growth (first→last): "
                  f"{anchors[0]:,} → {anchors[-1]:,}")
    if report:
        if "fold_econ" in report:
            fe = report["fold_econ"]
            print(f"  paid back / not paid back / unobserved: "
                  f"{fe['paid_back']} / {fe['not_paid_back']} / {fe['unobserved']}")
            if fe["not_paid_back"] > 0:
                print(f"  WARNING: {fe['not_paid_back']} fold(s) did not amortize")
        if "miss" in report:
            m = report["miss"]
            repay_ratio = m["compress_repay_K"] / max(m["new_content_K"], 1)
            print(f"  compress re-pay / new content: {repay_ratio:.2f}")
            if repay_ratio >= 1.0:
                print("  WARNING: folds cost as much as fresh content "
                      "— replays are too expensive")
            elif repay_ratio >= 0.5:
                print("  NOTE: replay ratio ≥0.5 — room to consolidate folds")
        if "grand" in report:
            g = report["grand"]
            print(f"  overall report hit: {g['hit_pct']:.2f}% "
                  f"({g['cached_M']}M / {g['input_M']}M)")
    post = a.post_fold_hits(window=5)
    if post:
        print(f"  post-fold 5-req median hit: "
              f"{sorted(post)[len(post)//2]:.2f}%")

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
            print(f"  stable-prefix re-bill : {inv['charged_K']:.1f}K charged, "
                  f"{inv['unattributed_K']:.1f}K unattributed")

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
            key=lambda ep: (
                len(ep.folds) * 100_000
                / max(1, ep.nudges[-1].ref - ep.nudges[0].ref)
            ),
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
        verdicts.append(
            f"⚠ {a.reasoning_pair_violations} reasoning-pair violations — "
            f"strict-echo upstreams may reject"
        )

    if not verdicts:
        verdicts.append("(not enough data to form a verdict)")

    for v in verdicts:
        print(f"  {v}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Analyze billion-context logs + ACP cache report."
    )
    ap.add_argument("log", type=Path, help="path to bili.log")
    ap.add_argument("report", nargs="?", type=Path,
                    help="optional ACP cache report (markdown)")
    ap.add_argument("--session", help="only analyze lines for this session id")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON instead of a human report")
    args = ap.parse_args(argv)

    if not args.log.exists():
        print(f"error: log not found: {args.log}", file=sys.stderr)
        return 2

    analysis = parse_log(args.log, session_filter=args.session)

    report_data: Optional[dict] = None
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
                    k: {"median": v, "n": n}
                    for k, (v, n) in analysis.median_hit_by_size().items()
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