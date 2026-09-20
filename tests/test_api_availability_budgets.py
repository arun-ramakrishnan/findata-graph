"""Availability budgets for the hot routes (test_gap_closure S2).

Every confirmed finding in the 2026-09-19 security arc was an
availability finding (AVAIL-1 53 s GET, AVAIL-2 cubic regex). The arc
left one route budgeted (near-duplicates) and 37 with no ceiling at all.
This module stands up the pattern for the hot routes: each must return
within a measured budget, and a *malicious* parameter shape must stay
bounded rather than hang.

Budgets are measured p50 x ~50 on the seeded unit fixture (see
``_BASELINES``), generous rather than tight: a budget that flakes on CI
load is worse than no budget. Assertions are on the wall-clock envelope,
not a microbenchmark.
"""

from __future__ import annotations

import time

import pytest

# Measured 2026-09-20 on the seeded unit fixture: the COLD first call per
# route (each parametrized case gets a fresh unit_client, so the budget
# must cover the one-shot cost, not the warm steady state). Cold-start
# dominates for /api/graph/stats, whose first call builds the DuckDB
# graph layer (~766 ms measured vs ~190 ms warm).
#
# Ceiling = cold x 5, floored at 1 s. x5 is the tension point: loose
# enough to absorb xdist parallelism and CI load, tight enough that the
# AVAIL-1 class (a 53 s quadratic GET) and even a ~10x regression fails
# loudly. The floor keeps fast routes off a sub-millisecond budget that
# would flake on import/collection noise.
_BASELINES_MS = {
    "/api/graph/stats": 766.0,
    "/api/graph/positions": 1.6,
    "/api/graph/neighbors/HDFC Bank": 226.0,
    "/api/graph/similar/Companies/Banking/Hdfc_Bank.md": 194.0,
    "/api/analytics/coverage": 51.0,
    "/api/graph/co-mentions": 1.3,
    "/api/graph/sector/Banking": 214.0,
}


def _ceiling_ms(cold_ms: float) -> float:
    """Budget = max(cold x 5, 1 s). If a route regresses past this, the
    test fails loudly rather than silently serving slowly."""
    return max(cold_ms * 5.0, 1000.0)


@pytest.mark.parametrize("route", sorted(_BASELINES_MS))
def test_hot_route_returns_within_budget(unit_client, route):
    """Each hot route answers within its measured ceiling.

    Takes the best of 3 requests: under xdist the suite runs 4 workers on
    a 4-core box, and a co-running worker can add ~3x contention to a
    single-shot timing (measured: sector/Banking swings 269-805 ms under
    load). Best-of-3 measures the route's own cost, not the scheduler's,
    which is what the budget is about -- a genuine AVAIL-1-class slowdown
    (53 s) exceeds any contention envelope.
    """
    ceiling_s = _ceiling_ms(_BASELINES_MS[route]) / 1000.0
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        r = unit_client.get(route)
        best = min(best, time.perf_counter() - t0)
    # 404 is a legitimate fast answer for a fixture-shaped miss; the budget
    # guards the *compute* path, and a 404 that takes 10 s is still a bug.
    assert best < ceiling_s, (
        f"{route} took {best * 1000:.0f} ms (best of 3), ceiling "
        f"{ceiling_s * 1000:.0f} ms; status {r.status_code}"
    )


def test_malicious_limit_stays_bounded(unit_client):
    """A huge ``limit`` must not turn into unbounded row materialisation.

    This is the AVAIL-1 shape: an unauthenticated parameter scaling the
    response cost linearly or worse. The route must clamp or serve fast,
    never hang.
    """
    t0 = time.perf_counter()
    r = unit_client.get("/api/graph/near-duplicates?min_sim=0.0&limit=5001")
    elapsed = time.perf_counter() - t0
    # The route clamps limit > 500 to 400 (verified in test_api_graph_unit);
    # the budget guards against a regression that drops the clamp and
    # materialises instead.
    assert elapsed < 2.0, f"limit=5001 took {elapsed * 1000:.0f} ms"
    assert r.status_code in (200, 400)


def test_deep_path_param_stays_bounded(unit_client):
    """A deep ``<path:...>`` value must resolve or 404 fast, not scan.

    The path converters turn request segments into filesystem lookups
    (the class Addendum 1 \u00a7B flagged for the 13 post-close routes).
    A hostile deep path must not degrade into a walk.
    """
    deep = "Companies/" + "Sub/" * 40 + "Deep.md"
    t0 = time.perf_counter()
    r = unit_client.get(f"/api/graph/similar/{deep}")
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"deep path took {elapsed * 1000:.0f} ms"
    assert r.status_code in (200, 404)


def test_unknown_analytics_name_fails_fast(unit_client):
    """An unknown analytics name must 404, not compute anything."""
    t0 = time.perf_counter()
    r = unit_client.get("/api/analytics/not-a-real-report")
    elapsed = time.perf_counter() - t0
    assert r.status_code == 404
    assert elapsed < 2.0, f"unknown analytics name took {elapsed * 1000:.0f} ms"
