#!/usr/bin/env python3
"""swap_fk_gate.py — advisory gate leg: FK-orphan check on a converted
research-store copy (duckdb_research_swap_steps.md Step 4 wiring — the
"wire into a checkable gate step" follow-up, landed post-archive
2026-11-17).

Store selection, in order:

  1. explicit 'store' argument    -> check that store; no rebuild logic;
  2. $SWAP_CONVERTED_DUCKDB env   -> use when FRESH (mtime newer than the
     live memory/research.db), else rebuild into the scratch cache;
  3. scratch cache (default)      -> $TMPDIR/opencode/swap_fk_gate/
     research_converted.duckdb, rebuilt on the same freshness rule.

Rebuild = sqlite_scanner ATTACH (READ_ONLY) + per-table CTAS with full
declared-type fidelity. The Step-1 sqlite_all_varchar fallback for
'entities' is no longer needed: its cause (the malformed
entities.created_at literal, Birkenstock rowid 755) was fixed
2026-11-17 and every other observed shape casts cleanly to TIMESTAMP
(see the archived proposal's SS2.2 amendment). Rebuilds go through a
temp file + atomic rename so a crash never leaves a half-converted
cache behind.

Advisory convention (swap-prep-census / perf-smoke): always exits 0;
findings show up in the gate report, not the exit code. No writes
outside the scratch cache directory.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SOURCE = REPO / "memory" / "research.db"
CACHE_DIR = Path(os.environ["TMPDIR"]) / "opencode" / "swap_fk_gate"  # TMPDIR is required; /tmp is never the fallback
CACHE = CACHE_DIR / "research_converted.duckdb"

sys.path.insert(0, str(REPO))
from helpers.core.db import connect as _connect  # noqa: E402
from helpers.misc.swap_fk_integrity import scan_orphans  # noqa: E402


def _fresh(p: Path) -> bool:
    """A converted copy is fresh iff newer than the live source."""
    return p.exists() and p.stat().st_mtime > SOURCE.stat().st_mtime


def _sqlite_tables() -> list[str]:
    con = _connect(SOURCE, read_only=True, wal=False)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def _rebuild(dest: Path) -> float:
    """CTAS-convert the live store into 'dest'; returns seconds taken."""
    import duckdb  # local: only needed on the rebuild path

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=".swap_fk_gate_", suffix=".duckdb", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_str)
    tmp.unlink()
    t0 = time.time()
    tables = _sqlite_tables()
    con = duckdb.connect(str(tmp))
    try:
        con.execute("LOAD sqlite_scanner")
        con.execute(f"ATTACH '{SOURCE}' AS rs (TYPE sqlite, READ_ONLY)")
        for t in tables:
            con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM rs."{t}"')
    finally:
        con.close()
    dt = time.time() - t0
    os.replace(tmp, dest)  # atomic: never a half-converted cache
    return dt


def _select_store(explicit: str | None) -> tuple[Path, str]:
    if explicit:
        return Path(explicit), "explicit"
    env = os.environ.get("SWAP_CONVERTED_DUCKDB")
    if env:
        p = Path(env)
        if _fresh(p):
            return p, "env/fresh"
    if _fresh(CACHE):
        return CACHE, "cache/fresh"
    dt = _rebuild(CACHE)
    return CACHE, f"rebuilt in {dt:.1f}s"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "store",
        nargs="?",
        default=None,
        help="explicit converted store to check (skips locate/rebuild)",
    )
    args = ap.parse_args(argv)

    if not SOURCE.exists() and not args.store:
        print(f"swap-fk-gate: status=error (source missing: {SOURCE})")
        return 0

    try:
        store, provenance = _select_store(args.store)
    except Exception as e:  # noqa: BLE001 - advisory leg, report and stay green
        print(f"swap-fk-gate: status=error ({e})")
        return 0

    import duckdb  # local: only needed on the check path

    con = duckdb.connect(str(store), read_only=True)
    try:
        findings, checked = scan_orphans(con)
    finally:
        con.close()

    tag = f"{store} ({provenance})"
    if findings:
        print(f"swap-fk-gate: ORPHANS={len(findings)} store={tag} edges={checked}")
        for f in findings:
            print("   ", f)
    else:
        print(f"swap-fk-gate: orphans=0 store={tag} edges={checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
