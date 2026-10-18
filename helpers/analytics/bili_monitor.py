#!/usr/bin/env python3
"""Continuous background monitor for the bili proxy log (read-only).

Every POLL_S it re-scans the live proxy log via bili_tunewatch.scan() and:
  * appends any NEW T1 fire / fold to events.log (human-readable, with burn delta)
  * every SNAP_S appends one snapshot row per session to snapshots.jsonl
  * every SNAP_S appends "<ts>\t<sid>\t<context>\t<hit>\t<burn>" to burn.tsv
Touches HEARTBEAT each poll. Exits after DURATION_S and writes monitor.exit.

  bili_monitor.py [duration_s=43200] [poll_s=60] [snap_s=300]

State goes to $BILI_WATCH_DIR (default: ${TMPDIR:-/tmp}/bili_watch, which on this
machine resolves to /mnt/data/tmp/bili_watch via tempfile.gettempdir()), so the
repo stays clean. Stop it with bili_stop_monitor.sh -- never `pkill -f`.

The log path is discovered, not hardcoded (bili_logpath.py walks /proc for the
running billion-context proxy); override with BILI_LOG.

Env: BILI_LOG, BILI_LOG_OLD, BILI_WATCH_DIR, BILI_RATE
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BILI_WATCH_DIR", os.path.join(tempfile.gettempdir(), "bili_watch"))
os.makedirs(DATA, exist_ok=True)
EVENTS = os.path.join(DATA, "events.log")
SNAPS = os.path.join(DATA, "snapshots.jsonl")
BURN = os.path.join(DATA, "burn.tsv")
HEART = os.path.join(DATA, "HEARTBEAT")
EXIT = os.path.join(DATA, "monitor.exit")


def load_tunewatch():
    spec = importlib.util.spec_from_file_location(
        "bili_tunewatch", os.path.join(HERE, "bili_tunewatch.py")
    )
    if spec is None or (loader := spec.loader) is None:
        raise ImportError("bili_tunewatch.py must sit next to bili_monitor.py")
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str]) -> int:  # noqa: C901 - one linear pass per tick
    duration = int(argv[0]) if argv else 43200
    poll = int(argv[1]) if len(argv) > 1 else 60
    snap = int(argv[2]) if len(argv) > 2 else 300
    tw = load_tunewatch()

    seen_folds: set[tuple[str, str]] = set()
    seen_fires: dict[tuple[str, int, int, int], int] = {}
    seen_inject: set[tuple[str, str]] = set()
    baseline = True
    t0 = time.time()
    last_snap = 0.0
    n_fold = n_inject = n_overlimit = n_esc = n_gatepred = n_degen = 0
    base_counts: dict[str, list[int]] = {}
    wrote_base = False
    with open(EVENTS, "a") as ev, open(SNAPS, "a") as sn, open(BURN, "a") as bu:
        ev.write(f"\n=== monitor start {now()} duration={duration}s\n")
        ev.flush()
        while time.time() - t0 < duration:
            try:
                data = tw.scan()
            except Exception as exc:  # a monitor must not die on a parse hiccup
                ev.write(f"{now()} scan error: {exc!r}\n")
                ev.flush()
                time.sleep(poll)
                continue
            for sid, row in data.items():
                for f in row["folds"]:
                    key = (sid, f["ts"])
                    if key in seen_folds:
                        continue
                    seen_folds.add(key)
                    if baseline:
                        continue
                    n_fold += 1
                    degen = float(f["shrink"]) >= 80
                    if degen:
                        n_degen += 1
                    gain = ""
                    if f.get("burn_pre") and f.get("burn_post"):
                        # burn_pre/burn_post ratio in the RIGHT direction: pre/post >= 1
                        # means the fold cut burn; < 1 means it RAISED burn (a fold that
                        # freed too little to repay its own cache rewrite). post_n < 3 is
                        # too few samples to judge - the first post-fold request is always
                        # cache-cold, so a small n biases post HIGH.
                        r = f["burn_pre"] / f["burn_post"]
                        verdict = (
                            f"{r:.1f}x cheaper" if r >= 1 else f"{1 / r:.1f}x WORSE (more burn)"
                        )
                        prov = "" if f["post_n"] >= 3 else f" PROVISIONAL(n={f['post_n']})"
                        gain = (
                            f" burn/req {f['burn_pre']:,} -> {f['burn_post']:,} ({verdict}{prov})"
                        )
                    real = ""
                    if f.get("real_shrink") is not None:
                        # REAL ctx from the [acp-usage] input= lines. The obs
                        # pre/post on the compress-obs line double-counts already
                        # folded ranges, so its shrink% understates the real drop
                        # (measured 4% vs 28.5%). obs_shrink is a lower bound, so
                        # the `>=80%` degen test below stays conservative.
                        real = (
                            f" REAL_CTX {f['real_pre']:,}->{f['real_post']:,}"
                            f" ({f['real_shrink']}% drop)"
                        )
                    ev.write(
                        f"{now()} FOLD {sid} {f['ts']} obs_shrink={f['shrink']}% saved={f['saved']:,} "
                        f"blocks={f['blocks']} anchor={f.get('anchor', 0):,}{real}{gain}"
                        f"{'  <<< DEGEN obs_shrink>=80%' if degen else ''}\n"
                    )
                    ev.flush()
                for e in row.get("t1_fires", []):
                    key = (sid, e["ts"])
                    if key in seen_inject:
                        continue
                    seen_inject.add(key)
                    if baseline:
                        c = base_counts.setdefault(sid, [0, 0, 0])
                        c[0] += 1
                        c[1] += 1 if e["over_limit"] else 0
                        c[2] += 1 if e["kind"] == "ESC" else 0
                        continue
                    if e["kind"] == "ESC":
                        n_esc += 1
                    else:
                        n_inject += 1
                        n_overlimit += 1 if e["over_limit"] else 0
                    path = (
                        "ESC?"
                        if e["kind"] == "ESC"
                        else ("OVER-LIMIT" if e["over_limit"] else "GATE")
                    )
                    ev.write(
                        f"{now()} T1-FIRE[{path}] {sid} {e['ts']} usage={e['usage']}% "
                        f"({e['input']:,}/{e['win']:,}) growth={e['g']:,}/{e['f']:,} "
                        f"pendingT1={e['p']:,}/{e['pt']:,} interval={e['iv']:,} model={e['model']} "
                        f'reason="{e["reason"]}"\n'
                    )
                    ev.flush()
                for e in row["eras"]:
                    # Key on the FULL era tuple, not interval alone: two eras
                    # can share an interval with different thresholds/floors
                    # (interval=200000/thr=200000 -> interval=200000/thr=150000
                    # collapses under (sid, interval), misreporting the new
                    # era's smaller fire count as no change).
                    k = (sid, e["interval"], e["pending_thr"], e["growth_floor"])
                    prev = seen_fires.get(k, 0)
                    seen_fires[k] = e["fires"]
                    if baseline or e["fires"] <= prev:
                        continue
                    n_gatepred += e["fires"] - prev
                    ev.write(
                        f"{now()} T1-GATE-PRED {sid} interval={e['interval']} fires={e['fires']} "
                        f"era=[{e['first_ts'][11:19]}..{e['last_ts'][11:19]}] "
                        f"maxPending={e['max_pending']:,}  (inferred from nudge evals; not a real injection)\n"
                    )
                    ev.flush()
            baseline = False
            if not wrote_base:
                wrote_base = True
                tot = sum(c[0] for c in base_counts.values())
                ovl = sum(c[1] for c in base_counts.values())
                esc = sum(c[2] for c in base_counts.values())
                base_folds = sum(len(r["folds"]) for r in data.values())
                base_degen = sum(
                    1 for r in data.values() for f in r["folds"] if float(f["shrink"]) >= 80
                )
                ev.write(
                    f"=== baseline {now()} injections already in log: {tot} real events "
                    f"({tot - esc} T1, of which {ovl} over-limit; {esc} ESC-unknown) "
                    f"across {len(base_counts)} session(s); {base_folds} fold(s) already in log "
                    f"({base_degen} degen shrink>=80%); not re-logged below\n"
                )
                for s2, c in sorted(base_counts.items()):
                    ev.write(f"    {s2}  T1={c[0] - c[2]} (over-limit={c[1]}) esc={c[2]}\n")
                ev.flush()
            if time.time() - last_snap >= snap:
                last_snap = time.time()
                for sid, row in data.items():
                    lu = row["last_usage"][-1] if row["last_usage"] else None
                    sn.write(
                        json.dumps(
                            {
                                "run_ts": row["run_ts"],
                                "sid": sid,
                                "burn_mean": row["burn_mean"],
                                "burn_last10": row["burn_last10"],
                                "burn_total": row["burn_total"],
                                "folds": len(row["folds"]),
                                "usage_n": row["usage_n"],
                                "eras": row["eras"],
                                "last": lu,
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    if lu:
                        bu.write(
                            f"{row['run_ts']}\t{sid}\t{lu['input']}\t{lu['hit']}\t{lu['burn']}\t{row['burn_mean']}\n"
                        )
                sn.flush()
                bu.flush()
            with open(HEART, "w") as hb:
                hb.write(
                    f"{now()} events={n_fold} folds (degens={n_degen}), T1-fires={n_inject + n_esc} "
                    f"({n_inject} gate/over-limit, {n_esc} ESC-unknown), "
                    f"{n_gatepred} gate-predicted; uptime={int(time.time() - t0)}s\n"
                )
            time.sleep(poll)
    with open(EXIT, "w") as fh:
        fh.write(
            f"done {now()} folds={n_fold} degens={n_degen} t1fires={n_inject} "
            f"over_limit={n_overlimit} esc={n_esc} elapsed={int(time.time() - t0)}s\n"
        )
    print(
        f"monitor done {now()} folds={n_fold} t1fires={n_inject} over_limit={n_overlimit} esc={n_esc}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
