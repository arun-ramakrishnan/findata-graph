#!/usr/bin/env python3
"""Live watcher for the billion-context (bili) proxy log: eras, folds, burn.

Read-only. Parses the raw proxy log family directly -- no db, no repo import,
no writes into the repo -- and reports the gate/threshold eras, every fold with
its real context drop and burn delta, and per-session quota burn.

  bili_tunewatch.py snap [SID ...]    append one JSON snapshot per session
  bili_tunewatch.py report [SID ...]  per-session era/fold/usage summary

Watchers built on it: bili_monitor.py (continuous), bili_watch_event.py (block
until the next event), bili_thresh_replay.py (threshold replay). Long-lived
output goes to $BILI_WATCH_DIR (default: ${TMPDIR:-/tmp}/bili_watch, which on this
machine resolves to /mnt/data/tmp/bili_watch via tempfile.gettempdir()).

The log path is DISCOVERED, not hardcoded: bili_logpath.py walks /proc for the
running billion-context proxy and reads its cwd, so a second proxy started
elsewhere, or an npm module logging under its own name, is found too. Override
with BILI_LOG / BILI_LOG_OLD.

Env: BILI_LOG, BILI_LOG_OLD, BILI_TIMELINE, BILI_WATCH_DIR, BILI_RATE
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime

# Standalone entry point: `python3 helpers/analytics/bili_tunewatch.py` puts the
# repo root on sys.path so `helpers.*` imports resolve. bili_sessions.py does
# the same bootstrap; without it the import below fails with ModuleNotFoundError.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from helpers.analytics.bili_logpath import discover_all  # noqa: E402

RATE = float(os.environ.get("BILI_RATE", "0.1"))  # billed cache-read multiple of input price
LOG, LOG_OLD = discover_all()
DATA = os.environ.get("BILI_WATCH_DIR", os.path.join(tempfile.gettempdir(), "bili_watch"))
TIMELINE = os.environ.get("BILI_TIMELINE", os.path.join(DATA, "timeline.jsonl"))

RE_NUDGE = re.compile(
    r"growth=(?P<g>-?\d+)/(?P<f>\d+)\s*\(ref=(?P<ref>\d+),\s*"
    r"interval=(?P<iv>\d+)\),\s*pendingT1=(?P<p>\d+)/(?P<pt>\d+)"
)
RE_FOLD = re.compile(
    r"\[acp-compress-obs\] shrink (?P<shrink>[\d.]+)% \(~(?P<saved>\d+)/(?P<pre>\d+) tok\) "
    r"foldPoint=(?P<fp>\S+) blocks=(?P<blocks>\d+) anchor≈(?P<anchor>\d+) tok.*?postCtx≈(?P<post>\d+)"
)
RE_USAGE = re.compile(r"\[acp-usage\] input=(?P<in>\d+) cached=(?P<cac>\d+)(?P<rest>.*)$")
# A model/window change. Any change of the upstream model invalidates the whole
# prefix cache (the cache lives in the provider, keyed per model), so every
# switch costs a full cold rebuild — and a mistaken switch costs it twice
# (out and back). Observed 2026-10-18: a manual mis-switch on two sessions.
RE_SWITCH = re.compile(r"\[window-change\]\s*(?P<old>\S+)\s*->\s*(?P<new>\S+)")
RE_HDR = re.compile(
    r"^(\S+)\s.*?\[((?:ses_[A-Za-z0-9]+)|(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))\]"
)
# A REAL injection event (what the proxy actually did), as opposed to the
# gate-predicted `fires` inferred from nudge evaluations. Two shapes:
#   nudge INJECT T1:      ... reason="T1 effective P >= PT, ..."        (gate path)
#   nudge INJECT T1:      ... reason="OVER-LIMIT T1: max effective pending P, usage U%"  (usage path)
#   nudge INJECT T2/T3:   ... reason="OVER-LIMIT T2 distill: max pending P (T1 effective .., T2 ..)"  (tier path)
#   nudge INJECT-ESC T?:  ... reason="max compressible P < threshold PT..." (tier unknown)
# T2/T3 are the TIER ladder: T2 re-distills the T1 summary blocks, T3 condenses
# the T2 ones. T2 has fired exactly once in the whole retained log family
# (2026-10-18T04:40:23, OVER-LIMIT path) -- and this regex only knew T1, so
# that one line was silently dropped, invisible to every watcher built here.
# Whatever the tier config ends up being, the watcher must be able to see it.
RE_T1 = re.compile(
    r"nudge INJECT\s?(?P<tier>T[123]|-ESC T\?): usage=(?P<usage>\d+)% \((?P<input>\d+)/(?P<win>\d+)\), "
    r"growth=(?P<g>-?\d+)/(?P<f>\d+) \(ref=(?P<ref>\d+), interval=(?P<iv>\d+)\), "
    r"pendingT1=(?P<p>\d+)/(?P<pt>\d+) model=(?P<model>[^,]+), reason=\"(?P<reason>[^\"]*)\""
)


def scan() -> dict:  # noqa: C901 - one linear pass over the log family
    """Return {sid: {...}} built from the whole log family (cheap: plain reads)."""
    nudges: dict = defaultdict(lambda: defaultdict(list))  # sid -> era -> [(p,g,f,ts,pending_thr)]
    folds: dict = defaultdict(list)
    usage: dict = defaultdict(list)
    scope: dict = defaultdict(list)  # sid -> (ts, pending_thr, pending)
    injections: dict = defaultdict(
        list
    )  # sid -> [{ts, kind, usage, pending, ...}] real T1/ESC events
    for fp in (LOG_OLD, LOG):
        try:
            fh = open(fp, errors="replace")
        except OSError:
            continue
        with fh:
            for ln in fh:
                h = RE_HDR.match(ln)
                if not h:
                    continue
                ts, sid = h.group(1), h.group(2)
                if "nudge" in ln:
                    m = RE_NUDGE.search(ln)
                    if m:
                        g = m.groupdict()
                        era = (int(g["iv"]), int(g["pt"]), int(g["f"]))
                        nudges[sid][era].append((int(g["p"]), int(g["g"]), ts))
                        scope[sid].append((ts, int(g["pt"]), int(g["p"])))
                    t = RE_T1.search(ln)
                    if t:
                        d = t.groupdict()
                        d["ts"] = ts
                        # kind is the TIER: "T1"/"T2"/"T3" or "ESC" (tier
                        # unknown, emergency path). `tier` keeps the raw token.
                        d["tier"] = d["tier"].strip()
                        d["kind"] = "ESC" if "-ESC" in d["tier"] else d["tier"]
                        d["over_limit"] = "OVER-LIMIT" in d["reason"]
                        for k in ("usage", "input", "win", "g", "f", "ref", "iv", "p", "pt"):
                            d[k] = int(d[k])
                        injections[sid].append(d)
                elif "acp-compress-obs" in ln and "degenerate-fold" not in ln:
                    # The exclusion scans the WHOLE line: the marker sits at
                    # char ~134, after `[info] [sess=...] [v=...] [sid]`, so
                    # the earlier `ln[:60]` slice never reached it. Fold stats
                    # stay clean today only because degenerate lines say
                    # "covers NN%" (no `shrink ` for RE_FOLD) -- keep the
                    # marker check real so that stays true by construction.
                    m = RE_FOLD.search(ln)
                    if m:
                        g = m.groupdict()
                        folds[sid].append(
                            {
                                "ts": ts,
                                "shrink": float(g["shrink"]),
                                "saved": int(g["saved"]),
                                "pre": int(g["pre"]),
                                "blocks": int(g["blocks"]),
                                "anchor": int(g["anchor"]),
                                "post": int(g["post"]),
                                "fp": g["fp"],
                            }
                        )
                elif "acp-usage" in ln:
                    m = RE_USAGE.search(ln)
                    if m:
                        g = m.groupdict()
                        usage[sid].append(
                            (ts, int(g["in"]), int(g["cac"]), "fold=new" in g["rest"])
                        )
    out = {}
    for sid in set(nudges) | set(folds) | set(usage) | set(injections):
        eras = []
        ordered = sorted(
            nudges.get(sid, {}).items(), key=lambda kv: (min(x[2] for x in kv[1]), kv[0])
        )
        for idx, (era, rr) in enumerate(ordered):
            iv, pt, fl = era
            lo = min(x[2] for x in rr)
            hi = min(x[2] for x in ordered[idx + 1][1]) if idx + 1 < len(ordered) else "~"
            era_folds = [f for f in folds.get(sid, []) if lo <= f["ts"] < hi]
            eras.append(
                {
                    "interval": iv,
                    "pending_thr": pt,
                    "growth_floor": fl,
                    "nudges": len(rr),
                    "max_pending": max(x[0] for x in rr),
                    "fires": sum(1 for x in rr if x[0] >= pt and x[1] >= fl),
                    "first_ts": min(x[2] for x in rr),
                    "last_ts": max(x[2] for x in rr),
                    "folds_after_start": len(era_folds),
                }
            )
        u = usage.get(sid, [])
        tail = u[-8:]
        burn = [(t, c * RATE + (i - c)) for t, i, c, _ in u]
        fl = folds.get(sid, [])
        for f in fl:
            pre = [b for t, b in burn if t < f["ts"]][-10:]
            post = [b for t, b in burn if t > f["ts"]][:10]
            f["burn_pre"] = round(sum(pre) / len(pre)) if pre else None
            f["burn_post"] = round(sum(post) / len(post)) if post else None
            f["post_n"] = len(post)
            # REAL context drop, from the [acp-usage] input= lines.
            #
            # The proxy's own compress-obs `pre/post` is NOT the request size: it
            # tracks the proxy's internal map, which keeps the folded content and
            # accumulates with every mid-range fold. Measured on
            # ses_ebc050416ffeNxMq6pRKMrtzwL at 2026-10-18T06:45:14 the obs line
            # said pre=746,518 while the real request was input=104,751 (7.1x),
            # and real + sum(saved) = 766,487 ~= 746,518 -- i.e. folded ranges are
            # counted twice. So the obs line's `shrink%` UNDERSTATES the real drop
            # badly (4% vs 28.5% for that fold) and `post/saved` (payback) is
            # meaningless. Never quote those; the real numbers are here.
            before = [i for t, i, _, _ in u if t < f["ts"]]
            after = [i for t, i, _, _ in u if t > f["ts"]]
            f["real_pre"] = before[-1] if before else None
            f["real_post"] = after[0] if after else None
            f["real_shrink"] = (
                round(100 * (1 - f["real_post"] / f["real_pre"]), 1)
                if f["real_pre"] and f["real_post"]
                else None
            )
        inj = injections.get(sid, [])
        # Real injections broken down by tier. T2/T3 are the tier ladder
        # (re-distill T1 summaries / condense T2 summaries); a T2/T3 count of
        # zero in a session with many active blocks is the signal that the tier
        # gate is not armed.
        tier_fires: dict = defaultdict(int)
        for e in inj:
            tier_fires[e["kind"]] += 1
        out[sid] = {
            "run_ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "log": LOG,
            "rate": RATE,
            "eras": eras,
            "folds": fl,
            "t1_fires": inj,
            "tier_fires": dict(tier_fires),
            "usage_n": len(u),
            "burn_total": round(sum(b for _, b in burn)),
            "burn_mean": round(sum(b for _, b in burn) / len(burn)) if burn else 0,
            "burn_last10": round(sum(b for _, b in burn[-10:]) / min(10, len(burn))) if burn else 0,
            "last_usage": [
                {
                    "ts": t,
                    "input": i,
                    "cached": c,
                    "hit": round(100 * c / i, 1) if i else 0.0,
                    "uncached": i - c,
                    "burn": round(c * RATE + (i - c)),
                    "fold_new": fn,
                }
                for t, i, c, fn in tail
            ],
        }
    return out


def _ts_ge(a: str, b: str) -> bool:
    return a >= b


def cmd_snap(argv: list[str]) -> int:
    data = scan()
    sids = argv or None
    n = 0
    os.makedirs(os.path.dirname(TIMELINE) or ".", exist_ok=True)
    with open(TIMELINE, "a") as fh:
        for sid, row in sorted(data.items()):
            if sids and sid not in sids:
                continue
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            n += 1
    print(f"{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} snap {n} session(s) -> {TIMELINE}")
    return 0


def cmd_report(argv: list[str]) -> int:
    data = scan()
    sids = argv or sorted(data)
    for sid in sids:
        row = data.get(sid)
        if not row:
            print(f"### {sid}: nothing")
            continue
        print(f"### {sid}   rate={row.get('rate', RATE)}")
        print(
            f"    QUOTA BURN (cached×{row.get('rate', RATE)} + uncached): "
            f"total={row['burn_total']:>12,}  mean/req={row['burn_mean']:>9,}  "
            f"mean last10={row['burn_last10']:>9,}  (n={row['usage_n']})"
        )
        tf = row.get("tier_fires") or {}
        tier_txt = "  ".join(f"{k}={tf[k]}" for k in ("T1", "T2", "T3", "ESC") if k in tf)
        print(f"    REAL INJECTIONS (tier ladder): {tier_txt or 'none'}")
        for e in row["eras"]:
            print(
                f"    era interval={e['interval']:<7} thr={e['pending_thr']:<7} "
                f"floor={e['growth_floor']:<7} nudges={e['nudges']:<4} "
                f"maxPending={e['max_pending']:>8,} fires={e['fires']:<4} "
                f"folds={e['folds_after_start']:<3} [{e['first_ts'][11:19]}..{e['last_ts'][11:19]}]"
            )
        for f in row["folds"]:
            burn = ""
            real = ""
            if f.get("real_shrink") is not None:
                real = (
                    f"  REAL ctx {f['real_pre']:>7,} -> {f['real_post']:>7,}"
                    f" = {f['real_shrink']:>5.1f}% drop"
                )
            if f.get("burn_pre") is not None:
                burn = f"  burn/req {f['burn_pre']:>7,} -> {f['burn_post']:>7,}"
                if f["burn_pre"] and f["burn_post"]:
                    burn += f" ({f['burn_pre'] / f['burn_post']:.1f}x cheaper)"
                burn += f" [post n={f['post_n']}]"
            print(
                f"    fold {f['ts']} obs_shrink={f['shrink']:>5.1f}% saved={f['saved']:>8,} "
                f"blocks={f['blocks']} {f['fp']}{real}{burn}"
            )
        print("    last usage rows (input / cached / hit / uncached / burn):")
        for u in row["last_usage"]:
            print(
                f"      {u['ts']} in={u['input']:>7,} cac={u['cached']:>7,} "
                f"hit={u['hit']:>5.1f}% uncached={u['uncached']:>7,} burn={u.get('burn', 0):>7,}"
                + ("  <-- FOLD-NEW" if u["fold_new"] else "")
            )
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "snap":
        return cmd_snap(argv[1:])
    if argv[0] == "report":
        return cmd_report(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
