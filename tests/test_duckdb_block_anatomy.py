#!/usr/bin/env python3
"""Tests for helpers/maintenance/duckdb_block_anatomy.py — the F4 dead-block probe.

Two test strata, deliberately:

- Hermetic: block arithmetic + trigger predicate on crafted dicts, and
  live-set accounting over real (small) synthetic stores. NOTE: small
  synthetic stores CANNOT reliably strand dead blocks — DuckDB 2.0's
  checkpoint compacts them fully (measured 2026-11-07: insert→delete→
  checkpoint, delete+reinsert, and DROP all reclaim to used==live).
  Real dead blocks come from scale (the live convo store measured
  694 MB dead after ONE incremental refresh); there is no cheap
  synthetic fixture for that, so the bloated case is pinned by the
  live-store leg below instead of a tmp_path imitation.

- Live (pytest.mark.live, the test_db_maint_duckdb convention): the
  production store, skipped when absent, asserting measurement
  integrity — never a specific dead-block count (it varies with
  incremental-refresh churn and swap rebuilds).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from helpers.maintenance import duckdb_block_anatomy as dba  # noqa: E402


def _mk_compact(tmp_path: Path) -> Path:
    db = tmp_path / "compact.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t (a INTEGER, b VARCHAR)")
    con.execute("INSERT INTO t SELECT i, 'payload-' || i FROM range(1000) t(i)")
    con.execute("CREATE TABLE u (a VARCHAR)")  # second table: union across tables
    con.execute("INSERT INTO u VALUES ('x')")
    con.close()
    return db


class TestAnatomyHermetic:
    def test_compact_store_fully_live(self, tmp_path: Path):
        """A freshly-written store: every used block holds a live segment."""
        a = dba.anatomy(_mk_compact(tmp_path))
        assert a["status"] == "ok"
        assert a["dead_blocks"] == 0
        assert a["live_blocks"] == a["blocks_used"]
        assert a["dead_pct"] == 0.0
        assert a["dead_bytes"] == 0
        assert a["file_bytes"] == (tmp_path / "compact.duckdb").stat().st_size
        assert a["dead_bytes"] == a["dead_blocks"] * a["block_size"]

    def test_multi_table_union_covers_every_table(self, tmp_path: Path):
        """live_blocks >= any single table's own block count (union across
        tables, incl. non-main schemas)."""
        db = _mk_compact(tmp_path)
        a = dba.anatomy(db)
        con = duckdb.connect(str(db), read_only=True)
        try:
            per_table = 0
            for (
                s,
                t,
            ) in con.execute(
                "SELECT schema_name, table_name FROM duckdb_tables() WHERE NOT internal"
            ).fetchall():
                q = t if s == "main" else f"{s}.{t}"
                blocks = {
                    r[[c[0] for c in con.description].index("block_id")]
                    for r in con.execute(f"PRAGMA storage_info('{q}')").fetchall()
                    if r[[c[0] for c in con.description].index("block_id")] is not None
                }
                per_table = max(per_table, len(blocks))
        finally:
            con.close()
        assert a["live_blocks"] >= per_table

    def test_absent_store_is_advisory(self, tmp_path: Path):
        a = dba.anatomy(tmp_path / "missing.duckdb")
        assert a["status"] == "absent"
        assert "dead_blocks" not in a

    def test_unreadable_store_degrades(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        db = _mk_compact(tmp_path)
        monkeypatch.setattr(
            "helpers.misc.duckdb_lock.open_read_only",
            lambda _p: (_ for _ in ()).throw(RuntimeError("locked")),
        )
        a = dba.anatomy(db)
        assert a["status"] == "unavailable"
        assert a["reason"] == "RuntimeError"

    def test_anatomy_from_con_matches_anatomy(self, tmp_path: Path):
        """The in-lock variant db_maint uses must agree with the standalone."""
        db = _mk_compact(tmp_path)
        a = dba.anatomy(db)
        con = duckdb.connect(str(db), read_only=True)
        try:
            b = dba.anatomy_from_con(con, db)
        finally:
            con.close()
        assert b["live_blocks"] == a["live_blocks"]
        assert b["dead_blocks"] == a["dead_blocks"]


def _craft(status: str = "ok", file_bytes: int = 2_500_000_000, dead_bytes: int = 0) -> dict:
    return {
        "status": status,
        "db_path": "x",
        "file_bytes": file_bytes,
        "block_size": 262144,
        "total_blocks": 10_000,
        "blocks_used": 10_000,
        "live_blocks": 8_000,
        "dead_blocks": dead_bytes // 262144,
        "dead_bytes": dead_bytes,
        "dead_pct": round(dead_bytes * 100.0 / file_bytes, 1),
    }


class TestSwapTrigger:
    """Proposal S2 predicate: dead_bytes >= max(MIN_DEAD_BYTES, MAX_DEAD_PCT% of file).

    The composite bound is >= (a lower-bound "worth a full rebuild" test,
    not a waste alarm) — the exact-at-boundary case FIRES, unlike the
    sqlite freelist's strict >. Pinned here so a future edit knows the
    boundary semantics are deliberate.
    """

    def test_default_trigger_fires_on_the_measured_bloat(self):
        """2026-11-06 live store: 694 MB dead — below, incremental stays."""
        a = _craft(file_bytes=1_900_032_000, dead_bytes=694_000_000)
        assert not dba.above_swap_trigger(a)
        assert not dba.above_swap_trigger(a, min_dead_bytes=1_000_000_000)

    def test_recalibrated_trigger_2026_11_08(self):
        """Trigger recalibrated 2026-11-08 to max(2 GB, 60%) — a swap only
        rebuilds live data (~27 min), so the trigger sets frequency, and
        the first production fire (61.7% dead after two incremental
        passes) came too early. Pin both sides of the new regime: the
        2026-11-08 bloat still fires, a mid-cycle store stays below."""
        # The fire that prompted the recalibration: 2052 MB of 3.324 GB.
        fired = _craft(file_bytes=3_323_998_208, dead_bytes=2_052_000_000)
        assert dba.above_swap_trigger(fired)
        # Post-swap store (~1.3 GB live) after two incremental passes
        # (~650 MB dead each): 50% — below both legs, incremental stays.
        mid = _craft(file_bytes=2_600_000_000, dead_bytes=1_300_000_000)
        assert not dba.above_swap_trigger(mid)

    def test_fires_above_both_legs(self):
        a = _craft(file_bytes=2_000_000_000, dead_bytes=900_000_000)
        # Both legs pinned explicitly — this test isolates the max()
        # composite, not the default trigger values.
        assert dba.above_swap_trigger(
            a, min_dead_bytes=800_000_000, max_dead_pct=40.0
        )  # pct leg: 900M >= 40% of 2G (800M)
        assert dba.above_swap_trigger(
            a, min_dead_bytes=900_000_000, max_dead_pct=40.0
        )  # byte leg at >= boundary
        assert not dba.above_swap_trigger(
            a, min_dead_bytes=1_000_000_000, max_dead_pct=40.0
        )  # 900M < 1G byte leg

    def test_at_byte_boundary_fires_ge_not_gt(self):
        a = _craft(file_bytes=1_000, dead_bytes=100)
        assert dba.above_swap_trigger(a, min_dead_bytes=100, max_dead_pct=10.0), (
            "max(100, 100) == 100, >= fires"
        )
        assert not dba.above_swap_trigger(a, min_dead_bytes=101, max_dead_pct=10.0)

    def test_non_ok_status_never_fires(self):
        for status in ("absent", "unavailable"):
            assert not dba.above_swap_trigger({"status": status})


class TestSummarize:
    def test_ok_line_carries_dead_info(self):
        s = dba.summarize(_craft(dead_bytes=694_000_000))
        assert "dead" in s and "GB" in s

    def test_unavailable_line_mentions_reason(self):
        s = dba.summarize({"status": "unavailable", "reason": "IOException"})
        assert "unavailable" in s and "IOException" in s


@pytest.mark.live
class TestLiveStore:
    """Measurement integrity on the real store — skipped when absent.
    Never asserts a specific dead count (it tracks incremental churn)."""

    def test_convo_store_measures(self):
        db = PROJECT_ROOT / "memory" / "convo_search.duckdb"
        if not db.exists():
            pytest.skip("production convo_search.duckdb absent")
        a = dba.anatomy(db)
        assert a["status"] == "ok"
        assert a["file_bytes"] == db.stat().st_size
        assert a["live_blocks"] <= a["blocks_used"] <= a["total_blocks"]
        assert a["dead_blocks"] == a["blocks_used"] - a["live_blocks"]
        assert 0.0 <= a["dead_pct"] <= 100.0
        assert isinstance(dba.above_swap_trigger(a), bool)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
