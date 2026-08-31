# contract: pytest.ini (markers, --strict-markers, hypothesis seed pin) + ruff S101 per-file-ignore (pyproject.toml)
"""<One-line purpose — script_query indexes THIS line as the module intent.>

<2-6 lines: what module/behavior this file covers, and which gate owns
it: unmarked = the default qa suite; `live` = real-DB invariants
(advisory; needs memory/research.db + the findata/ vault);
`integration` = cross-component pipelines (qa's integration step).
Fuzz modules are a NAMING convention (test_fuzz_*.py + hypothesis),
not a marker. State WHY the file needs the marker it carries.>

Run (keep the literal commands):
    .venv/bin/python3 -m pytest tests/<this>.py -n auto      # this module
    .venv/bin/python3 -m pytest tests/<this>.py -k <case>    # one case

House rules for test modules (don't fight them):
  - Marker routing decides the gate. --strict-markers rejects undeclared
    names: declare new markers in pytest.ini FIRST. Today: live /
    integration / real_graph_cache (opts a test back onto the production
    graph.duckdb path under xdist).
  - xdist safety: tests run -n auto beside other suites — NEVER open a
    real DB read-write (readers pass read_only=True; per-test temp DBs
    otherwise; the per-worker DuckDB redirect lives in conftest, don't
    defeat it). Tests may run in ANY order, in parallel.
  - Reuse tests/conftest.py fixtures (_UNIT_SCHEMA, seeded sqlite,
    unit_client) — never redefine shared fixtures locally.
  - Timing assertions flake under contention: sum a burst of runs above
    the skip threshold, take best-of-3. Wall-clock budgets live ONLY in
    `make perf`; pytest keeps ratio/complexity guards.
  - hypothesis is pinned (--hypothesis-seed=0 in pytest.ini addopts) so
    qa doesn't flap; explore with --hypothesis-seed=<n> locally, never
    repin the default to land a flake.
"""

from __future__ import annotations

import pytest

# pytestmark = [pytest.mark.integration]  # module-wide marker; justify in the docstring


def _subject(n: int) -> int:
    """Tiny pure helper — keeps arrange logic out of the asserts."""
    return n * 2


def test_subject_doubles():
    """One behavior per test; the name states the contract."""
    assert _subject(2) == 4


@pytest.mark.parametrize("value,expected", [(1, 2), (-3, -6), (0, 0)])
def test_subject_parametrized(value: int, expected: int):
    """Parametrize examples instead of copy-pasting near-twin tests."""
    assert _subject(value) == expected


# Read-only DB fixture shape (uncomment when needed — RO connections
# coexist under xdist; one RW open excludes every other worker):
#
# from helpers.core.db import connect
#
# @pytest.fixture()
# def ro_con():
#     con = connect(<db_path>, read_only=True)
#     yield con
#     con.close()
