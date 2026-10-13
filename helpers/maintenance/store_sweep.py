#!/usr/bin/env python3
"""Store-wide reclaim sweep: census + VACUUM/ANALYZE/CHECKPOINT every store.

Covers the registered sidecar estate — ``db_maint.SIDECAR_SQLITE`` /
``SIDECAR_DUCKDB``, the single decision home this module imports (never
re-implements). The primaries ``research.db`` / ``graph.duckdb`` are
db_maint's own maintained objects. Before store_wide_reclaim, `make
maint` only vacuumed its primary and checkpointed the graph cache while
merely backing up the sidecars, so script_search/doc_search/
md_lint_cache and the data/*.duckdb stores accumulated dead pages with
no maint path at all (measured 2026-10-14: script_search carried 529
freelist pages = 40% of the file). Proposal:
``doc/improvements/archive/tooling/store_wide_reclaim.md``.

Per sqlite store: census -> ``DBMaintainer.reclaim_freelist()`` (the
method patched 2026-10-14 that re-measures after ``incremental_vacuum``
and falls back to a full VACUUM above ``WASTED_PCT``) -> ANALYZE only
when ``sqlite_stat1`` has never been primed ->
``wal_checkpoint(TRUNCATE)`` -> ``quick_check`` when a mutation
happened. No ``io_lock`` on the sqlite side (2026-10-14 audit ruling):
WAL already gives concurrent readers plus a single writer, so the engine
converts every conflict into a bounded ``busy_timeout`` (5 s via the
house ``connect()``) instead of an unbounded flock wait — a busy store
becomes a ``skipped`` row, never a crash, never a force. Per DuckDB
store: census -> ``io_lock(exclusive)`` -> CHECKPOINT + VACUUM +
CHECKPOINT (the ``db_maint._maintain_duckdb`` pattern; the flock estate
is where DuckDB coordination lives — db_maint backup SH / maint EX,
agent_traces EX, index_sync EX); a foreign RW holder fails the open and
is skipped, never forced.

Connection discipline: house ``helpers.core.db.connect()`` (B2 guard —
the raw sqlite module is never opened directly here). The sweep issues no
DML, and VACUUM /
ANALYZE / PRAGMAs are not DDL-that-implicit-begins, so nothing ever runs
inside a transaction on the deferred house connection (empirically validated
2026-10-14: VACUUM + ANALYZE + wal_checkpoint through connect() on a scratch
db). Side effect worth knowing: connect() normalizes journal_mode to WAL —
a one-time header change for ``md_lint_cache.db`` (currently ``delete``),
which is the house doctrine, not an accident.

Usage::

    python3 helpers/maintenance/store_sweep.py            # census + act
    python3 helpers/maintenance/store_sweep.py --dry-run  # read-only census

Scratch-census numbers of record live in the proposal; this tool is the
repeatable runner behind it.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.core.db import connect  # noqa: E402
from helpers.misc.duckdb_lock import io_lock  # noqa: E402
from helpers.maintenance.db_maint import (  # noqa: E402
    SIDECAR_DUCKDB,
    SIDECAR_SQLITE,
    DBMaintainer,
)

# db_maint's own maintained objects — outside the sidecar registry and
# outside this sweep (single-owner rule; db_maint.run reclaims them).
# Repo-relative so the cross-check composes with a retargeted REPO.
_PRIMARIES = {"memory/research.db", "memory/graph.duckdb"}  # noqa: E402

MB = 1048576


def _logger() -> logging.Logger:
    lg = logging.getLogger("store_sweep")
    if not lg.handlers:
        lg.setLevel(logging.INFO)
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("  [%(levelname)s] %(message)s"))
        lg.addHandler(h)
    return lg


def discover(suffix: str) -> list[Path]:
    """Every store file of one engine, lexicographic (memory/ then
    memory/data/); 0-byte kept (reported)."""
    return sorted(REPO.glob(f"memory/*{suffix}")) + sorted(REPO.glob(f"memory/data/*{suffix}"))


def _wal_bytes(p: Path) -> int:
    wal = p.with_name(p.name + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def sqlite_census(conn, p: Path) -> dict:
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
    try:
        stat1 = conn.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()[0]
    except Exception:  # sqlite3.OperationalError: table never existed
        stat1 = -1
    return {
        "bytes": p.stat().st_size,
        "journal": conn.execute("PRAGMA journal_mode").fetchone()[0],
        "auto_vac": conn.execute("PRAGMA auto_vacuum").fetchone()[0],
        "pages": pages,
        "freelist": freelist,
        "pct": (freelist / pages * 100.0) if pages else 0.0,
        "stat1": stat1,
        "wal": _wal_bytes(p),
    }


def run_sqlite(p: Path, *, dry_run: bool, lg: logging.Logger) -> dict:
    rel = str(p.relative_to(REPO))
    row: dict = {"store": rel, "engine": "sqlite"}
    if p.stat().st_size == 0:
        row["action"] = "skipped (0-byte artifact)"
        return row
    if dry_run:
        conn = connect(p, read_only=True)
        try:
            before = sqlite_census(conn, p)
        finally:
            conn.close()
        row.update(action="census-only", **before)
        return row

    t0 = time.time()
    # No io_lock on the sqlite side (2026-10-14 audit ruling): WAL already
    # provides concurrent readers + a single writer, so any conflict is a
    # bounded busy_timeout (5 s, house connect()) — not an unbounded flock
    # wait. A loss becomes a `skipped` row below, never a crash.
    try:
        # House connect() — B2 discipline (FK + WAL); see module docstring
        # for why VACUUM is transaction-safe on this deferred connection.
        conn = connect(p)
        try:
            before = sqlite_census(conn, p)
            # backup_path is a required ctor arg the sweep never uses: the
            # maintainer's backups belong to `make maint` (db_maint.run),
            # this tool only reclaims free pages.
            maint = DBMaintainer(
                db_path=p,
                backup_path=p.with_name(p.name + ".sweep-unused.bak"),
                backup_sidecars=False,
                logger=lg,
            )
            mode = maint.reclaim_freelist(conn, before["auto_vac"], before["freelist"])
            analyzed = False
            if before["stat1"] <= 0:
                conn.execute("ANALYZE")
                analyzed = True
            if mode != "skipped":
                # In WAL mode VACUUM's rebuild lives in the -wal file; the
                # checkpoint makes the on-disk .db the real post-sweep size.
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                quick = conn.execute("PRAGMA quick_check").fetchone()[0]
            else:
                quick = "-"  # clean stores skip the scan (FTS5 quick_check = seconds)
            after = sqlite_census(conn, p)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001  # per-store isolation IS the contract
        if isinstance(e, sqlite3.Error) and (
            "locked" in str(e).lower() or "busy" in str(e).lower()
        ):
            # Single-writer contention (a live writer VACUUM'd against):
            # the designed degradation, same shape as the DuckDB skip.
            row.update(action="skipped (database is locked)", secs=round(time.time() - t0, 2))
            lg.warning(f"{rel}: {row['action']}")
        else:
            row.update(action=f"error ({type(e).__name__})", secs=round(time.time() - t0, 2))
            lg.error(f"{rel}: {row['action']}: {e}")
        return row
    row.update(
        action=mode,
        analyzed=analyzed,
        before_bytes=before["bytes"],
        after_bytes=after["bytes"],
        freelist=before["freelist"],
        freelist_after=after["freelist"],
        wasted_pct=round(before["pct"], 1),
        stat1=before["stat1"],
        quick=quick,
        secs=round(time.time() - t0, 2),
    )
    return row


def run_duckdb(p: Path, *, dry_run: bool, lg: logging.Logger, vacuum: bool = True) -> dict:
    rel = str(p.relative_to(REPO))
    row: dict = {"store": rel, "engine": "duckdb", "bytes": p.stat().st_size}
    if dry_run:
        row.update(
            action="census-only",
            before_bytes=p.stat().st_size,
            after_bytes=p.stat().st_size,
            wal=_wal_bytes(p),
        )
        return row

    import duckdb

    t0 = time.time()
    before = p.stat().st_size
    try:
        with io_lock(p, exclusive=True):
            con = duckdb.connect(str(p))
            try:
                con.execute("CHECKPOINT")
                if vacuum:
                    con.execute("VACUUM")
                    # Flush VACUUM's rewrite out of the WAL (the final
                    # checkpoint of the db_maint._maintain_duckdb pattern).
                    con.execute("CHECKPOINT")
            finally:
                con.close()
        action = "checkpoint+vacuum" if vacuum else "checkpoint"
    except duckdb.IOException as e:  # another RW holder (busy) -> never force
        action = f"skipped ({type(e).__name__})"
        lg.warning(f"{rel}: {action}")
    except Exception as e:  # noqa: BLE001  # per-store isolation IS the contract
        action = f"error ({type(e).__name__})"
        lg.error(f"{rel}: {action}: {e}")
    row.update(
        action=action,
        before_bytes=before,
        after_bytes=p.stat().st_size,
        wal=_wal_bytes(p),
        secs=round(time.time() - t0, 2),
    )
    return row


def _fmt(n: int | None) -> str:
    return f"{n / MB:7.1f}M" if n is not None else "      -"


def exit_code(rows: list[dict]) -> int:
    """0 unless a store genuinely failed.

    Busy skips are the designed degradation (hot stores) — never a
    failure. An ``error (…)`` row, or a ``quick_check`` that did not come
    back ``ok``, is: the #380 anti-pattern is reporting success while
    having done less than claimed.
    """
    for r in rows:
        if str(r.get("action", "")).startswith("error"):
            return 1
        if r["engine"] == "sqlite" and r.get("quick") not in (None, "-", "ok"):
            return 1
    return 0


def unregistered_stores() -> list[Path]:
    """Stores on disk outside the registry (and the two primaries).

    The completeness cross-check behind the sweep's "every store" claim:
    an unregistered store is owned elsewhere or registry drift — either
    way it is reported, never silently swept (#380: a tool must not
    claim more than it did).
    """
    registered = set(SIDECAR_SQLITE) | {rel for rel, _ in SIDECAR_DUCKDB} | _PRIMARIES
    out: list[Path] = []
    for suffix in (".db", ".duckdb"):
        for p in discover(suffix):
            if str(p.relative_to(REPO)) not in registered:
                out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="read-only census of every store: no locks, no VACUUM/ANALYZE/CHECKPOINT",
    )
    args = ap.parse_args()
    lg = _logger()

    rows: list[dict] = []
    for rel in SIDECAR_SQLITE:
        print(f"-- sqlite {rel}", flush=True)
        rows.append(run_sqlite(REPO / rel, dry_run=args.dry_run, lg=lg))
    for rel, vacuum in SIDECAR_DUCKDB:
        print(f"-- duckdb {rel}{'' if vacuum else ' (checkpoint-only)'}", flush=True)
        rows.append(run_duckdb(REPO / rel, dry_run=args.dry_run, lg=lg, vacuum=vacuum))

    for p in unregistered_stores():
        lg.warning(
            f"unregistered store on disk — own it in db_maint.SIDECAR_* or note it "
            f"owned-elsewhere: {p.relative_to(REPO)}"
        )

    print("\n=== STORE SWEEP ===" + (" (dry run)" if args.dry_run else ""))
    hdr = (
        f"{'store':40} {'eng':6} {'action':26} {'before':>8} {'after':>8} {'freed':>8} {'note':24}"
    )
    print(hdr)
    print("-" * len(hdr))
    total = 0
    for r in rows:
        freed = 0
        note = ""
        if r["engine"] == "sqlite":
            if r.get("action") == "census-only":
                note = f"fl {r['freelist']} {r['pct']:.1f}% stat1 {r['stat1']} {r['journal']}"
            elif "before_bytes" in r:
                freed = r["before_bytes"] - r["after_bytes"]
                note = (
                    f"fl {r['freelist']}->{r.get('freelist_after', 0)} "
                    f"{r.get('wasted_pct', 0)}% stat1 {r.get('stat1', '?')}"
                    + (" ANALYZE" if r.get("analyzed") else "")
                    + f" qc:{r.get('quick', '-')}"
                ).strip()
            else:
                note = "-"
        else:
            if "before_bytes" in r:
                freed = r["before_bytes"] - r["after_bytes"]
            note = f"wal {_fmt(r['wal']).strip()}" if "wal" in r else ""
        total += freed
        print(
            f"{r['store']:40} {r['engine']:6} {r.get('action', '?'):26} "
            f"{_fmt(r.get('before_bytes', r.get('bytes')))} "
            f"{_fmt(r.get('after_bytes', r.get('bytes')))} {_fmt(freed)} {note}"
            + (f" {r['secs']}s" if "secs" in r else "")
        )
    print(f"\nreclaimed: {_fmt(total).strip()} across {len(rows)} stores")
    return exit_code(rows)


if __name__ == "__main__":
    raise SystemExit(main())
