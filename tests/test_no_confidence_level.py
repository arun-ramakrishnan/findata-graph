"""Regression test pinning that the `confidence_level` column is NOT present
in the entities table.

The column was dropped in Jul 2026 (pending item #3 VOID) because it was
unused — zero reads across the entire codebase. This test prevents accidental
reintroduction via a schema migration or copy-paste from stale docs.
"""

from __future__ import annotations

from helpers.core.db import connect  # noqa: E402
from tests._live_store import live_store_path


def test_confidence_level_column_dropped():
    """entities must NOT have a confidence_level column."""
    db_path = live_store_path()
    if db_path is None:
        import pytest  # noqa: PLC0415  # only needed on a store-less box

        pytest.skip("live research store not present")
    conn = connect(db_path, row_factory=None, read_only=True)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()]
    finally:
        conn.close()
    assert "confidence_level" not in cols, (
        "confidence_level column was reintroduced. It was dropped in Jul 2026 "
        "because nothing reads it. If you genuinely need a confidence signal, "
        "compute it on demand from (ticker, market_cap, note length) rather "
        "than storing a denormalized column."
    )
