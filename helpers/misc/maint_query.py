#!/usr/bin/env python3
"""Query the maintenance timing corpus (gate_query's sibling for maint).

Every maintenance CLI (``search-fresh`` / ``convo-fresh`` / ``embed-gc``
/ ``analytics-fresh`` / ``snapshot`` legs) records one row per run in
``maint_runs`` plus one row per phase in ``maint_phases``, inside
``outputs/gate_runs.duckdb`` — written by
``helpers/maintenance/maint_timing.py``, never by hand.

    maint_query recent [--cmd CMD] [--last N]
    maint_query timing --cmd CMD [--phase PHASE] [--last N]
    maint_query failures [--cmd CMD] [--last N]
    maint_query phases --cmd CMD [--last 1]
"""

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "outputs" / "gate_runs.duckdb"


def connect():
    import duckdb

    return duckdb.connect(str(DB_PATH), read_only=True)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, int(q * (len(ordered) - 1)))
    return ordered[idx]


def _tables_exist(con) -> bool:
    names = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    return {"maint_runs", "maint_phases"} <= names


def cmd_recent(con, args) -> str:
    """Last N maint runs, newest first (run history)."""
    q = "SELECT * FROM maint_runs"
    qargs: list = []
    if args.cmd != "all":
        q += " WHERE cmd = ?"
        qargs.append(args.cmd)
    q += " ORDER BY started_at DESC LIMIT ?"
    qargs.append(args.last)
    rows = con.execute(q, qargs).fetchall()
    if not rows:
        return "no maint runs recorded yet (instrumented CLIs write on exit)"
    cols = [d[0] for d in con.description]
    out = [f"last {len(rows)} maint runs (newest first):"]
    for row in rows:
        r = dict(zip(cols, row))
        status = "OK" if (r["exit_code"] or 0) == 0 else "FAIL"
        tgt = f" [{r['target']}]" if r["target"] else ""
        line = (
            f"  id {r['maint_run_id']}  {r['cmd']}  {r['mode']}{tgt}  "
            f"{r['started_at']}  {r['elapsed_s']:.2f}s  exit {r['exit_code']}  {status}"
        )
        if r["summary"]:
            line += f"  {r['summary'][:100]}"
        out.append(line)
    return "\n".join(out)


def cmd_timing(con, args) -> str:
    """Per-run elapsed history for a cmd, or one phase across runs."""
    if args.phase:
        rows = con.execute(
            """SELECT r.maint_run_id, r.started_at, p.elapsed_s, r.mode
               FROM maint_phases p JOIN maint_runs r USING (maint_run_id)
               WHERE r.cmd = ? AND p.phase = ?
               ORDER BY r.started_at DESC LIMIT ?""",
            [args.cmd, args.phase, args.last],
        ).fetchall()
        label = f"{args.cmd}:{args.phase}"
    else:
        rows = con.execute(
            """SELECT maint_run_id, started_at, elapsed_s, mode FROM maint_runs
               WHERE cmd = ? ORDER BY started_at DESC LIMIT ?""",
            [args.cmd, args.last],
        ).fetchall()
        label = args.cmd
    if not rows:
        return f"no history for {label!r}"
    out = [f"{label} — last {len(rows)} runs (newest first)"]
    for _, started, secs, mode in rows:
        out.append(f"  {started}  [{mode or '-'}]  {secs:7.2f}s")
    values = [row[2] for row in rows if row[2] is not None]
    if values:
        out.append(
            f"  stats: min {min(values):.2f}s · p25 {_percentile(values, 0.25):.2f}s · "
            f"median {_percentile(values, 0.5):.2f}s · p75 {_percentile(values, 0.75):.2f}s · "
            f"max {max(values):.2f}s"
        )
    return "\n".join(out)


def cmd_failures(con, args) -> str:
    """Non-zero exits, newest first."""
    q = "SELECT * FROM maint_runs WHERE COALESCE(exit_code, 0) != 0"
    qargs: list = []
    if args.cmd != "all":
        q += " AND cmd = ?"
        qargs.append(args.cmd)
    q += " ORDER BY started_at DESC LIMIT ?"
    qargs.append(args.last)
    rows = con.execute(q, qargs).fetchall()
    if not rows:
        return "no maint failures recorded"
    cols = [d[0] for d in con.description]
    out = [f"last {len(rows)} maint failures (newest first):"]
    for row in rows:
        r = dict(zip(cols, row))
        out.append(
            f"  id {r['maint_run_id']}  {r['cmd']}  {r['mode']}  {r['started_at']}  "
            f"exit {r['exit_code']}  {r['summary'][:120]}"
        )
    return "\n".join(out)


def cmd_phases(con, args) -> str:
    """Step table (phase start/end/elapsed) for the latest run(s)."""
    q = "SELECT maint_run_id FROM maint_runs"
    qargs: list = []
    if args.cmd != "all":
        q += " WHERE cmd = ?"
        qargs.append(args.cmd)
    q += " ORDER BY started_at DESC LIMIT ?"
    qargs.append(args.last)
    ids = [r[0] for r in con.execute(q, qargs).fetchall()]
    if not ids:
        return "no maint runs recorded yet"
    out = []
    for run_id in ids:
        run = con.execute(
            "SELECT cmd, mode, started_at, ended_at, elapsed_s, exit_code, summary"
            " FROM maint_runs WHERE maint_run_id = ?",
            [run_id],
        ).fetchone()
        phases = con.execute(
            "SELECT phase, started_at, ended_at, elapsed_s, extra FROM maint_phases"
            " WHERE maint_run_id = ? ORDER BY started_at",
            [run_id],
        ).fetchall()
        out.append(
            f"run {run_id}  {run[0]}  {run[1]}  {run[2]} → {run[3]}  {run[4]:.2f}s  exit {run[5]}"
        )
        if run[6]:
            out.append(f"  summary: {run[6][:160]}")
        out.append("  Step                                  Start      End        Time (s)")
        for phase, start, end, secs, extra in phases:
            line = f"    {phase:<34s} {start}  {end}  {secs:7.2f}s"
            if extra:
                line += f"  {extra[:60]}"
            out.append(line)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("recent", help="last N maint runs, newest first")
    p.add_argument("--cmd", default="all")
    p.add_argument("--last", type=int, default=10)
    p = sub.add_parser("timing", help="elapsed history for a cmd or one phase")
    p.add_argument("--cmd", required=True)
    p.add_argument("--phase", default="")
    p.add_argument("--last", type=int, default=10)
    p = sub.add_parser("failures", help="non-zero exits, newest first")
    p.add_argument("--cmd", default="all")
    p.add_argument("--last", type=int, default=10)
    p = sub.add_parser("phases", help="step table for the latest run(s)")
    p.add_argument("--cmd", default="all")
    p.add_argument("--last", type=int, default=1)
    args = ap.parse_args(argv)
    try:
        con = connect()
    except Exception as exc:
        print(f"maint_query: cannot open {DB_PATH} ({exc})")
        return 1
    try:
        if not _tables_exist(con):
            print("no maint runs recorded yet (instrumented CLIs write on exit)")
            return 0
        fn = {
            "recent": cmd_recent,
            "timing": cmd_timing,
            "failures": cmd_failures,
            "phases": cmd_phases,
        }[args.command]
        print(fn(con, args))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
