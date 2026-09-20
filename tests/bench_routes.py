#!/usr/bin/env python3
"""Perf gate: HTTP route latency (test_gap_closure S3).

All 16 legs in run_perf_benchmarks.py invoke helper scripts directly; none
drives the Flask request path, so the gate structurally cannot catch the
AVAIL-1 class (a 53 s unauthenticated GET). This leg times the hot routes
through the Flask test client instead.

Idiom mirrors tests/bench_shortest_path.py: best-of-3 per route with an
unmeasured warm-up call absorbing the one-time plan/page-cache cost.
Production serves from a long-lived connection where steady-state is what
matters; a cold-first-call budget would measure interpreter + graph-layer
build, not the route.

Budgets set from measured steady-state x ~10 with a floor, calibrated
2026-09-20 on the seeded unit fixture (see _ROUTES). The ceiling is loose
on purpose: perf legs run on the operator's desktop, and a tight bar
flaked historically (bench_shortest_path 2026-08-22). The gate's job is
to catch an order-of-magnitude regression -- the AVAIL-1 class -- not to
microbenchmark.

Exit 1 on violation. Invoked by `make perf`.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.conftest import seeded_graph_sqlite_db  # noqa: E402

_REPS = 3

# (label, path, budget_seconds). Budgets = measured steady-state x ~10,
# floored at 0.5 s. Steady-state measured 2026-09-20 (best-of-3 after one
# warm-up call, seeded unit fixture):
#   stats ~180ms  sector ~137ms  similar ~140ms  coverage ~42ms
_ROUTES: list[tuple[str, str, float]] = [
    ("route_graph_stats", "/api/graph/stats", 0.5),
    ("route_graph_sector", "/api/graph/sector/Banking", 0.5),
    ("route_graph_similar", "/api/graph/similar/Companies/Banking/Hdfc_Bank.md", 0.5),
    ("route_analytics_coverage", "/api/analytics/coverage", 0.5),
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    violations: list[str] = []
    with seeded_graph_sqlite_db(tmp) as client:
        for label, path, budget in _ROUTES:
            # Unmeasured warm-up: first call builds plans + page cache.
            client.get(path)
            best = float("inf")
            for _ in range(_REPS):
                t0 = time.perf_counter()
                r = client.get(path)
                best = min(best, time.perf_counter() - t0)
            ms = best * 1000.0
            flag = "OK" if best < budget else "OVER_BUDGET"
            print(f"  {label:.<28s} {ms:8.2f} ms  < {budget:5.2f}s  [{flag}]")
            if best >= budget:
                violations.append(f"{label}: {ms:.0f} ms >= {budget:.2f}s")
            elif r.status_code not in (200, 404):
                violations.append(f"{label}: unexpected status {r.status_code}")
    if violations:
        for v in violations:
            print(f"VIOLATION: {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
