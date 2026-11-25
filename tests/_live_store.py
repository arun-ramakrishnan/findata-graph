"""Where the canonical research store actually lives.

The S6 DuckDB cutover (2026-11-21) retired ``memory/research.db``; the store
is its ``.duckdb`` sibling. Legs that gated on the literal ``.db`` path have
skipped ever since, without a red anywhere:

- qa run 4776 (2026-11-24) skipped five unmarked live tests ("live DB
  missing" / "live DB absent" / "live DB not available" / "needs both the
  snapshot and the live store");
- the advisory live-invariants leg skipped ``tests/test_db_maint_duckdb.py``
  at collection (six DuckDB-backup tests) and the live half of
  ``tests/test_derive_co_mentions.py``;
- the ``l1_kernels`` and ``ppr_edge_attribution`` perf legs printed SKIP and
  exited 0, which the perf harness scores OK: 0.2 s and 0.4 s against 240 s
  and 90 s budgets for seven runs (2026-11-22 08:46 .. 2026-11-24 14:10).

Gate on what the house door resolves, never on the retired name.
"""

from __future__ import annotations

from pathlib import Path

from helpers.core.db_engine import ResearchEngine


def live_store_path() -> Path | None:
    """The store ``helpers.core.db.connect()`` opens with no argument, or None.

    Same resolution as production readers: the ``FINDATA_RESEARCH_ENGINE``
    selector, the ``FINDATA_RESEARCH_DB_PATH`` override and the ``.db`` ->
    ``.duckdb`` sibling redirect (``ResearchEngine.resolve_path``).
    """
    path = ResearchEngine.from_env().resolve_path(None)
    return path if path.is_file() else None


def require_live_store() -> Path:
    """``live_store_path()``, or a pytest skip when no store is present."""
    import pytest  # noqa: PLC0415  # test-support module; keep helpers importable without pytest

    path = live_store_path()
    if path is None:
        pytest.skip("live research store not present")
    return path
