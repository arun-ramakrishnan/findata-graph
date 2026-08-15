#!/usr/bin/env python3
"""Performance tests — scaling-class assertions + connection-reuse units.

**History (2026-08-14):** this file previously also held 13 wall-clock
budget tests (`test_*_under_*s`) that spawned the canonical scripts via
``subprocess.run``. They duplicated ``make perf`` (tests/run_perf_benchmarks.py)
with *looser* budgets, never ran under ``make qa``/``make test``
(``-m "not live"`` deselected them), and contributed zero coverage under
``make cover`` (subprocesses are invisible to pytest-cov). They were folded
into the benchmark runner — which is a strict superset (18 budgets incl.
louvain/betweenness/eigenvector/parse_newsletter/enrich_yfinance) — and the
now-unused ``slow`` marker was removed from pytest.ini. Wall-clock budgets
live ONLY in ``make perf`` now.

What remains here runs as part of the default ``make qa`` gate (fast,
deterministic, synthetic):

1. **Scaling tests** (``test_*_scales_*``) — time-RATIO assertions between
   two synthetic sizes; they pin the complexity *class* (quadratic-pair
   cost for duplicate detection, linear per-query cost for fuzzy_match),
   not a wall-clock number, so they don't flap on loaded machines. They
   guard against the class of bug fixed in ``check_fuzzy_duplicate_names``
   (tokens recomputed in the inner loop → O(n^3)) and keep
   ``fuzzy_match`` (hybrid matcher, Bundle X1) from regressing to
   per-query O(n^2) scans. Total runtime ~0.05s.

2. **Connection-reuse units** — ``DatabaseIntegrityChecker.get_connection``
   memoization (the P2 fix) and idempotent ``close()``.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from misc.database_integrity_check import DatabaseIntegrityChecker  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def real_db_checker():
    """Checker pointed at the production DB. Read-only operations only."""
    checker = DatabaseIntegrityChecker()
    yield checker
    checker.close()


@pytest.fixture
def synthetic_db(tmp_path):
    """An in-memory-style SQLite DB with synthetic company rows.

    Returns (conn, db_path) where db_path is a temp file. Caller can
    insert rows to test scaling behavior.
    """
    db_path = tmp_path / "synthetic.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE entities (
            name TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            file_path TEXT,
            normalized_name TEXT,
            sector_classification TEXT,
            ticker TEXT
        )
    """)
    conn.execute("CREATE TABLE relations ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "source TEXT, target TEXT, relation_type TEXT)")
    conn.execute("CREATE TABLE entity_tags ("
                 "entity_name TEXT, tag TEXT, "
                 "PRIMARY KEY (entity_name, tag))")
    conn.commit()
    yield conn, db_path
    conn.close()


# ---------------------------------------------------------------------------
# 1. Scaling tests — complexity-class guards (synthetic, fast, run in qa)
# ---------------------------------------------------------------------------
# check_fuzzy_duplicate_names compares every company pair: O(n^2) pairs with
# constant per-pair cost. We test at two sizes (n=200, n=400) and assert the
# larger run is ≤ 6x the smaller run. For a truly O(n^2) algorithm with
# constant per-pair cost, the ratio would be ~4x ((400 choose 2)/(200 choose 2)).
# A regression to O(n^3) (e.g. recomputing tokens in the inner loop) would
# show ~8x.

def _run_fuzzy_check(checker):
    """Run check_fuzzy_duplicate_names and return elapsed seconds."""
    t0 = time.perf_counter()
    checker.check_fuzzy_duplicate_names()
    return time.perf_counter() - t0


def test_fuzzy_duplicates_scales_quadratically_not_worse(synthetic_db):
    """Per-pair cost must stay constant as company count grows.

    Generates 200 then 400 synthetic companies. The 400-row run should
    take ~4x the 200-row run (ratio of pair counts). Allow up to 6x to
    absorb noise. A regression to O(n^3) would show ~8x.
    """
    conn, db_path = synthetic_db

    def populate(n):
        conn.execute("DELETE FROM entities")
        for i in range(n):
            conn.execute(
                "INSERT INTO entities(name, entity_type) VALUES (?, 'company')",
                (f"Company {i} Group",),
            )
        conn.commit()

    # Small run.
    populate(200)
    checker_small = DatabaseIntegrityChecker(db_path=str(db_path))
    t_small = _run_fuzzy_check(checker_small)
    checker_small.close()

    # Large run.
    populate(400)
    checker_large = DatabaseIntegrityChecker(db_path=str(db_path))
    t_large = _run_fuzzy_check(checker_large)
    checker_large.close()

    # Avoid division by zero on extremely fast machines.
    if t_small < 0.001:
        pytest.skip("baseline too fast to measure reliably")

    ratio = t_large / t_small
    # Expected ratio for O(n^2) with constant per-pair: (400*399)/(200*199) ≈ 4.01
    # Allow 6x tolerance for noise. O(n^3) regression would be ~8x.
    assert ratio < 6.0, (
        f"Scaling regression: 400 rows took {ratio:.1f}x longer than 200 rows "
        f"(expected ~4x for clean O(n^2), budget 6x). "
        f"t_small={t_small:.3f}s, t_large={t_large:.3f}s"
    )


def test_fuzzy_match_scales_linearly_with_entities():
    """Per-query cost of fuzzy_match must stay ~linear in entity count.

    Runs a fixed batch of queries against 200 then 400 synthetic entities.
    The word-overlap stage scans every entity per query, so doubling the
    list should ~double the batch time (ratio ~2x). A regression that
    makes the scan itself O(n^2) per query (e.g. re-tokenising entities
    inside the loop, as the old check_fuzzy_duplicate_names did) shows
    ~4x. Budget 3x absorbs noise while still catching quadratic scans.
    """
    from core.fuzzy_match import fuzzy_match  # helpers/ is on sys.path (see imports)

    queries = [
        "Tata Consultancy Services", "HDFC Bank", "Mahindra", "Infosys Ltd",
        "Sun Pharma", "L&T", "TCS", "PayTM", "ICICI", "Reliance Industries",
    ]

    def batch(n):
        entities = [f"Company {i} Group Private Limited" for i in range(n)]
        t0 = time.perf_counter()
        for q in queries:
            fuzzy_match(q, entities)
        return time.perf_counter() - t0

    t_small = batch(200)
    t_large = batch(400)
    if t_small < 0.001:
        pytest.skip("baseline too fast to measure reliably")

    ratio = t_large / t_small
    # Expected ratio for O(n) per query: ~2x (200 -> 400 entities).
    # Allow 3x for noise. An O(n^2) per-query scan would be ~4x.
    assert ratio < 3.0, (
        f"Scaling regression: 400 entities took {ratio:.1f}x longer than 200 "
        f"(expected ~2x for linear per-query scan, budget 3x). "
        f"t_small={t_small:.3f}s, t_large={t_large:.3f}s"
    )


# ---------------------------------------------------------------------------
# 2. Connection reuse — get_connection must memoize (P2 fix)
# ---------------------------------------------------------------------------
def test_get_connection_memoizes(real_db_checker):
    """get_connection() must return the same connection object on repeat calls.

    This pins the P2 fix: previously each `_query()` opened a new
    connection (~5-10ms setup each). Now the connection is memoized on
    the instance and closed via close().
    """
    c1 = real_db_checker.get_connection()
    c2 = real_db_checker.get_connection()
    assert c1 is c2, "get_connection should return the memoized connection"
    real_db_checker.close()

    # After close, a new connection is created.
    c3 = real_db_checker.get_connection()
    assert c3 is not c1, "close() should release the memoized connection"
    real_db_checker.close()


def test_close_is_idempotent(real_db_checker):
    """close() must be safe to call multiple times (no error on second call)."""
    real_db_checker.get_connection()
    real_db_checker.close()
    real_db_checker.close()  # must not raise
