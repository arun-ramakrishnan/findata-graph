#!/usr/bin/env python3
"""Hermeticity watch — live-store touch recorder for the pytest suite.

Catches, at the test that caused it, the recurring qa-gate regression
class: test code or test-invoked production code reaching into the LIVE
``memory/`` stores. Two incidents of the same class within two weeks
(qa_gate_heavy_tests #478: tests cloned the 404 MB live research.db;
2026-11-08: the maint-chain shim's db_maint step CHECKPOINT+backed-up
the live 2.5 GB convo_search index every chain run).

Design (advisory-first, operator ruling 2026-11-08):

- Recording is ALWAYS-ON inside pytest (one substring prefilter per
  connect/open — sub-second across a 5.7k-test suite), so the qa run
  every arc already pays collects the data for free.
- Attribution is per-test via pytest_runtest_protocol; ``live``-marked
  tests are exempt (live-invariants legitimately touches live stores).
- Tier A (VIOLATION): a WRITE-CAPABLE connection (sqlite3.connect, or
  duckdb.connect without read_only=True) to a live store path.
- Tier B (report-only): read-only connects and file opens under the
  live tree — parity checks make some reads sanctioned; the report
  keeps their growth visible without redding the leg.
- Allowance: tests/_tmp_hygiene.py template donors read the live
  research.db ONCE per run by design — the donor build sets
  :func:`allow` around itself.

The advisory leg (helpers/validators/hermeticity_watch.py) reads this
run's recordings from ``outputs/.junit/hermeticity/`` and exits 1 only
on tier A. Zero qa-red risk: the recorder never raises, it only writes
JSON at sessionfinish.
"""

from __future__ import annotations

import builtins
import json
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"

#: Report sink. The qa controller wipes it in pytest_configure, workers
#: each add one file, sessionfinish writes atomically.
REPORT_DIR = REPO_ROOT / "outputs" / ".junit" / "hermeticity"

#: Live-tree path prefixes that are TEST-OWNED, not production state.
#: The shared graph cache (xdist_shared_graph_cache S1/S2) lives under
#: memory/ but is swept/rebuilt by the suite itself.
_TEST_OWNED_PARTS = ("xdist-shared", ".build.lock", ".part.")

_state = threading.local()
_touches: list[dict] = []
_installed = False
_orig_sqlite_connect = None
_orig_duckdb_connect = None
_orig_open = None


def allow() -> None:
    """Mark the current thread's store touches as sanctioned (donor reads)."""
    _state.allowed = True


def disallow() -> None:
    _state.allowed = False


def _test_owned(path: Path) -> bool:
    # markers are filename-level substrings (graph.xdist-shared.duckdb)
    return any(marker in path.name for marker in _TEST_OWNED_PARTS)


def is_live_path(p: object) -> bool:
    """True for paths inside the repo's live ``memory/`` tree.

    Deliberately cheap (two C-speed string checks) — it runs on every
    connect and open in the suite. Paths outside the repo root (tmp
    basetemp, home dotdirs) fail the startswith and cost one compare.
    """
    s = str(p)
    if "memory" not in s:  # global fast reject
        return False
    try:
        resolved = Path(s).resolve()
    except OSError:
        return False
    if not resolved.is_relative_to(MEMORY_ROOT):
        return False
    return not _test_owned(resolved)


def _record(tier: str, kind: str, path: Path) -> None:
    node = getattr(_state, "node", None)
    if node is None or getattr(_state, "allowed", False):
        return
    _touches.append({"node": node, "tier": tier, "kind": kind, "path": str(path)})


def classify_connect(module: str, path: object, kwargs: dict) -> None:
    """Tier A for write-capable connects, tier B for read-only."""
    if not is_live_path(path):
        return
    ro = bool(kwargs.get("read_only")) or "mode=ro" in str(kwargs.get("uri", ""))
    _record("B" if ro else "A", f"{module}:connect{'-ro' if ro else ''}", Path(str(path)))


def install() -> None:
    """Wrap sqlite3.connect / duckdb.connect / builtins.open. Idempotent,
    never raises (a broken recorder must not break the suite)."""
    global _installed, _orig_sqlite_connect, _orig_duckdb_connect, _orig_open
    if _installed:
        return
    try:
        _orig_sqlite_connect = sqlite3.connect

        def _guarded_sqlite_connect(*args, **kwargs):
            if args:
                classify_connect("sqlite3", args[0], kwargs)
            return _orig_sqlite_connect(*args, **kwargs)

        sqlite3.connect = _guarded_sqlite_connect

        try:
            import duckdb

            _orig_duckdb_connect = duckdb.connect

            def _guarded_duckdb_connect(*args, **kwargs):
                if args:
                    classify_connect("duckdb", args[0], kwargs)
                return _orig_duckdb_connect(*args, **kwargs)

            duckdb.connect = _guarded_duckdb_connect
        except ImportError:  # pragma: no cover — duckdb is a hard dep
            pass

        _orig_open = builtins.open

        def _guarded_open(file, *args, **kwargs):
            if is_live_path(file):
                _record("B", "open", Path(str(file)))
            return _orig_open(file, *args, **kwargs)

        builtins.open = _guarded_open
        _installed = True
    except Exception:  # noqa: S110 — recorder must never break the suite
        pass


def current_node(node: str | None) -> None:
    """pytest_runtest_protocol sets this around setup/call/teardown."""
    _state.node = node


def write_report(config) -> Path | None:
    """Persist this process's touches (xdist: one file per worker)."""
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
        out = REPORT_DIR / f"{worker}.json"
        payload = {
            "worker": worker,
            "finished": datetime.now(UTC).isoformat(timespec="seconds"),
            "n_touches": len(_touches),
            "touches": _touches,
        }
        tmp = out.with_suffix(".json.part")
        tmp.write_text(json.dumps(payload, indent=1))
        os.replace(tmp, out)  # os.replace law: same-dir atomic publish
        return out
    except Exception:  # noqa: S110 — best-effort by design
        return None
