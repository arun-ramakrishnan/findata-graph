"""Default DuckDB memory cap for TEST-SIDE ``duckdb.connect`` calls.

Operator ruling 2026-11-23: every DuckDB connection is capped at 2 GB
(``helpers.core.duckdb_shared.MEMORY_LIMIT``). The 2026-11-23 gate-arc swap
storm was driven by concurrent uncapped claims — 4 xdist workers, each
connection free to claim DuckDB's ~70%-of-RAM default — and benches and
research one-shots were repeat offenders in earlier swap incidents.

Production, maintenance, bench and research sites pass
``**memory_cap_config()`` themselves and are held to it by
helpers/validators/static_checks.py ``check_duckdb_memory_cap`` and its
pytest twin (tests/test_static_checks.py
``test_no_uncapped_duckdb_connect_outside_allowlist``). The test tree owns
~230 ``duckdb.connect`` sites instead of calling sites: they are capped
HERE, once per process, rather than edited one by one.

Installed at conftest import (before any test module loads), so every xdist
worker has it. A caller's own ``config`` keys survive and a caller's own
``memory_limit`` or ``temp_directory`` win — the cap is a default, not a
clamp.

tests/hermeticity_watch.py wraps ``duckdb.connect`` later (its fixture runs
after this module is installed) and calls through to the capped wrapper, so
the live-store recorder still sees the caller's kwargs unchanged.
"""

from __future__ import annotations

from functools import wraps

import duckdb

from helpers.core.duckdb_shared import memory_cap_config

#: The ``duckdb.connect`` that was in place when this module first loaded —
#: the uncapped builtin, or an outer wrapper installed before us. Kept as a
#: name so tests can open an uncapped connection as a baseline no matter how
#: many wrappers sit on ``duckdb.connect`` by the time they run.
REAL_CONNECT = getattr(duckdb.connect, "__wrapped__", duckdb.connect)


def _capped_connect(*args, **kwargs):
    config = dict(kwargs.get("config") or {})
    for key, value in memory_cap_config()["config"].items():
        config.setdefault(key, value)
    kwargs["config"] = config
    return REAL_CONNECT(*args, **kwargs)


def install() -> None:
    """Cap ``duckdb.connect`` by default. Idempotent: a second call (this
    module imported again under another name) never wraps the wrapper."""
    if getattr(duckdb.connect, "__wrapped__", None) is not REAL_CONNECT:
        duckdb.connect = wraps(REAL_CONNECT)(_capped_connect)
