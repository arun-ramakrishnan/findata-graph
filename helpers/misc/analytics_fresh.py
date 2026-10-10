#!/usr/bin/env python3
"""Analytics-store freshness check (read-only).

Compares each analytics store's per-source coverage frontier (max loaded
day) against the harness frontier (max harness day). A source is STALE
only when a *completed* day is missing from the store — same-day lag is
normal operation (live sessions keep writing; old days are immutable
once loaded).

Exit 0 when fresh, 1 on drift. Refresh with
`make analytics-fresh APPLY=1` (incremental `load all` on both loaders;
full reloads stay manual `load all --full` invocations).

Covered:
  agent_traces.duckdb fact_model_request (zcode/opencode/prime)
    vs zcode db.sqlite model_usage / opencode.db session+session_v2 /
       prime sessions dir (UTC day, matching the prime loader convention)
  agent_traces.duckdb fact_model_step (zcode_rollout)
    vs rollout model-io-*.jsonl max startedAt (streaming scan; torn
    trailing lines are skipped, never fatal)
  model_usage.duckdb fact_usage (prime-rlm/opencode)
    vs prime sessions dir / opencode.db
  fact_usage 'zai' is report-only: its authority is the quota-wide
  provider API, not a local file, so no local drift verdict applies.

Unknown store sources are listed as notes, never verdicts
(forward-compatible with future sources).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.core.db import connect  # noqa: E402  (path insert must precede)

AGENT_TRACES_DB = REPO / "memory" / "data" / "agent_traces.duckdb"
MODEL_USAGE_DB = REPO / "memory" / "data" / "model_usage.duckdb"
ZCODE_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
PRIME_DIR = Path.home() / ".prime" / "agent" / "sessions"
ROLLOUT_DIR = Path.home() / ".zcode" / "cli" / "rollout"

# (store label, store path key, table, store source, harness key)
CHECKS: list[tuple[str, str, str, str, str]] = [
    ("agent_traces", "agent_traces", "fact_model_request", "zcode", "zcode"),
    ("agent_traces", "agent_traces", "fact_model_request", "opencode", "opencode"),
    ("agent_traces", "agent_traces", "fact_model_request", "prime", "prime"),
    ("agent_traces", "agent_traces", "fact_model_step", "zcode_rollout", "rollout"),
    ("model_usage", "model_usage", "fact_usage", "prime-rlm", "prime"),
    ("model_usage", "model_usage", "fact_usage", "opencode", "opencode"),
]

# Store sources with no local harness mapping: authority lives elsewhere
# (zai = quota-wide provider API). Reported, never a drift verdict.
REPORT_ONLY: dict[str, set[str]] = {"model_usage": {"zai"}}


def _ms_local_day(ms: int | float) -> date:
    return datetime.fromtimestamp(ms / 1000).date()


def harness_frontiers(  # noqa: C901  # moved verbatim; split needs a parity harness (c901 deferred class)
    zcode_db: Path = ZCODE_DB,
    opencode_db: Path = OPENCODE_DB,
    prime_dir: Path = PRIME_DIR,
    rollout_dir: Path = ROLLOUT_DIR,
) -> dict[str, date | None]:
    """Max harness day per harness key; None when the harness is absent."""
    out: dict[str, date | None] = {"zcode": None, "opencode": None, "prime": None, "rollout": None}
    if zcode_db.is_file():
        con = connect(str(zcode_db), read_only=True)
        try:
            mx = con.execute("SELECT max(started_at) FROM model_usage").fetchone()[0]
        finally:
            con.close()
        if mx is not None:
            out["zcode"] = _ms_local_day(mx)
    if opencode_db.is_file():
        con = connect(str(opencode_db), read_only=True)
        try:
            mx = max(
                con.execute("SELECT max(time_created) FROM session").fetchone()[0] or 0,
                con.execute("SELECT max(time_created) FROM session_v2").fetchone()[0] or 0,
            )
        finally:
            con.close()
        if mx:
            out["opencode"] = _ms_local_day(mx)
    if prime_dir.is_dir():
        mtimes = [p.stat().st_mtime for p in prime_dir.glob("*.jsonl") if p.is_file()]
        if mtimes:
            out["prime"] = datetime.fromtimestamp(max(mtimes), UTC).date()
    if rollout_dir.is_dir():
        latest: date | None = None
        for f in sorted(rollout_dir.glob("model-io-*.jsonl")):
            with open(f, errors="replace") as fh:
                for line in fh:
                    try:
                        ts = json.loads(line).get("startedAt") if line.strip() else None
                    except json.JSONDecodeError, AttributeError:
                        continue  # torn trailing line of a live write: skip
                    if not ts:
                        continue
                    day = _ms_local_day(
                        datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000
                    )
                    latest = day if latest is None or day > latest else latest
        out["rollout"] = latest
    return out


def store_frontiers(
    agent_traces_db: Path = AGENT_TRACES_DB,
    model_usage_db: Path = MODEL_USAGE_DB,
) -> dict[tuple[str, str], date | None]:
    """Max loaded day per (store label, source); missing file/table -> None."""
    paths = {"agent_traces": agent_traces_db, "model_usage": model_usage_db}
    out: dict[tuple[str, str], date | None] = {}
    # One (label, table) pair can serve several sources (fact_model_request
    # serves zcode/opencode/prime); query each table once, then split.
    tables: dict[tuple[str, str], set[str]] = {}
    for label, _, table, source, _ in CHECKS:
        tables.setdefault((label, table), set()).add(source)
    for (label, table), sources in tables.items():
        path = paths[label]
        got: dict[str, date] = {}
        if path.is_file():
            con = duckdb.connect(str(path), read_only=True)
            try:
                have = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
                if table in have:
                    got = {
                        s: d
                        for s, d in con.execute(
                            f"SELECT source, max(day) FROM {table} GROUP BY source"  # noqa: S608
                        ).fetchall()
                    }
            finally:
                con.close()
        for source in sorted(sources):
            out[(label, source)] = got.get(source)
        for source in sorted(set(got) - sources):
            out[(label, source)] = got[source]  # notes only, no verdict
    return out


def check(
    harness: dict[str, date | None],
    store: dict[tuple[str, str], date | None],
    today: date | None = None,
) -> tuple[list[tuple[str, str, str, str, str]], bool]:
    """Return (rows, drift). Row = (store, source, harness_day, store_day, verdict)."""
    today = today or date.today()
    rows: list[tuple[str, str, str, str, str]] = []
    drift = False
    for label, _, _, source, hkey in CHECKS:
        hday = harness.get(hkey)
        sday = store.get((label, source))
        if hday is None:
            verdict = "no-harness"
        elif sday is None:
            verdict, drift = "STALE", True
        elif hday > sday and hday < today:
            verdict, drift = "STALE", True
        else:
            verdict = "fresh"
        rows.append((label, source, str(hday), str(sday), verdict))
    return rows, drift


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only analytics-store freshness check.")
    ap.add_argument("--agent-traces", type=Path, default=AGENT_TRACES_DB)
    ap.add_argument("--model-usage", type=Path, default=MODEL_USAGE_DB)
    ap.add_argument("--zcode-db", type=Path, default=ZCODE_DB)
    ap.add_argument("--opencode-db", type=Path, default=OPENCODE_DB)
    ap.add_argument("--prime-dir", type=Path, default=PRIME_DIR)
    ap.add_argument("--rollout-dir", type=Path, default=ROLLOUT_DIR)
    args = ap.parse_args(argv)
    from helpers.maintenance.maint_timing import RunTimer

    timer = RunTimer("analytics_fresh", mode="check")
    with timer.phase("harness_frontiers"):
        harness = harness_frontiers(
            args.zcode_db, args.opencode_db, args.prime_dir, args.rollout_dir
        )
    with timer.phase("store_frontiers"):
        store = store_frontiers(args.agent_traces, args.model_usage)
    with timer.phase("check"):
        rows, drift = check(harness, store)
    print(f"{'store':<14}{'source':<10}{'harness':<12}{'store_max':<12}verdict")
    for row in rows:
        print(f"{row[0]:<14}{row[1]:<10}{row[2]:<12}{row[3]:<12}{row[4]}")
    notes = sorted(
        s
        for (label, s) in store
        if (label, s) not in {(c[0], c[3]) for c in CHECKS}
        and s not in REPORT_ONLY.get(label, set())
    )
    for source in notes:
        print(f"note: unchecked store source '{source}' (no harness mapping)")
    for label, sources in REPORT_ONLY.items():
        for source in sorted(sources & {s for (lb, s) in store if lb == label}):
            print(f"note: {label} '{source}' is report-only (provider-API authority)")
    if drift:
        print("STALE — run `make analytics-fresh APPLY=1`")
        return timer.finish(1, "STALE (see rows above)")
    print("✓ analytics stores fresh")
    return timer.finish(0, f"FRESH rows={len(rows)}")


if __name__ == "__main__":
    sys.exit(main())
