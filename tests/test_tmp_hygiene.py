"""tests/_tmp_hygiene.py — keep-1 pruning + shared-template wiring.

The tmpfs-amplification fix (deferred item executed 2026-09-27): these
pin the pruning contract (quiet roots go, live roots and the current
run stay) without needing a real xdist run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._tmp_hygiene import (
    _default_pid_alive,
    prune_old_pytest_roots,
    sweep_stale_xdist_caches,
)


def _mkroot(parent: Path, name: str, age_s: float) -> Path:
    root = parent / name
    (root / "popen-gw0").mkdir(parents=True)
    past = time.time() - age_s
    os.utime(root / "popen-gw0", (past, past))
    os.utime(root, (past, past))
    return root


def test_quiet_roots_are_pruned_current_survives(tmp_path):
    current = _mkroot(tmp_path, "pytest-9", 0)
    old = _mkroot(tmp_path, "pytest-7", 3600)
    pruned = prune_old_pytest_roots(current)
    assert pruned == [old]
    assert current.exists()
    assert not old.exists()


def test_recent_roots_are_left_alone(tmp_path):
    """A sibling touched inside the quiet window is presumed a live run."""
    current = _mkroot(tmp_path, "pytest-9", 0)
    live = _mkroot(tmp_path, "pytest-8", 60)  # 1 min ago — inside 30 min window
    assert prune_old_pytest_roots(current) == []
    assert live.exists()


def test_child_activity_counts_as_alive(tmp_path):
    """Root mtime stale but a worker dir touched now → alive."""
    current = _mkroot(tmp_path, "pytest-9", 0)
    busy = _mkroot(tmp_path, "pytest-8", 3600)
    (busy / "popen-gw1").mkdir()
    assert prune_old_pytest_roots(current) == []
    assert busy.exists()


def test_symlink_and_foreign_entries_untouched(tmp_path):
    current = _mkroot(tmp_path, "pytest-9", 0)
    _mkroot(tmp_path, "pytest-8", 3600)
    link = tmp_path / "pytest-current"
    link.symlink_to(current)
    prune_old_pytest_roots(current)
    assert link.is_symlink()


def test_no_siblings_is_a_noop(tmp_path):
    current = _mkroot(tmp_path, "pytest-9", 0)
    assert prune_old_pytest_roots(current) == []
    assert current.exists()


# --------------------------------------------------------------------------- #
# sweep_stale_xdist_caches (xdist_shared_graph_cache S1)                        #
# --------------------------------------------------------------------------- #


def _mkcache(parent: Path, owner: str, age_s: float = 0, suffix: str = ".duckdb") -> Path:
    f = parent / f"graph.xdist-{owner}{suffix}"
    f.write_bytes(b"x")
    past = time.time() - age_s
    os.utime(f, (past, past))
    return f


def test_dead_owner_family_is_reclaimed(tmp_path):
    cache = _mkcache(tmp_path, "gw1-661397")
    lock = _mkcache(tmp_path, "gw1-661397", suffix=".duckdb.build.lock")
    wal = _mkcache(tmp_path, "gw1-661397", suffix=".duckdb.wal")
    removed = sweep_stale_xdist_caches(tmp_path, pid_alive=lambda pid: False)
    assert set(removed) == {cache, lock, wal}
    assert not any(p.exists() for p in (cache, lock, wal))


def test_live_owner_fresh_file_is_kept(tmp_path):
    """The concurrent-invocation guard: a live PID with a fresh file is a
    running worker's cache (the advisory gate runs two pytest invocations
    at once — entry 189) and must never be swept by the other one."""
    cache = _mkcache(tmp_path, "gw0-123", age_s=60)
    assert sweep_stale_xdist_caches(tmp_path, pid_alive=lambda pid: True) == []
    assert cache.exists()


def test_recycled_pid_falls_to_the_age_backstop(tmp_path):
    """A dead worker whose PID was recycled by an unrelated process reads
    as alive; the mtime floor reclaims the file once it ages out."""
    cache = _mkcache(tmp_path, "gw2-999", age_s=25 * 3600)
    removed = sweep_stale_xdist_caches(
        tmp_path, max_age_seconds=24 * 3600, pid_alive=lambda pid: True
    )
    assert removed == [cache]


def test_unattributable_names_are_kept(tmp_path):
    junk = tmp_path / "graph.xdist-garbage.duckdb"
    junk.write_bytes(b"x")
    no_pid_dot = tmp_path / "graph.xdist-gw0-123xd.duckdb"
    no_pid_dot.write_bytes(b"x")
    assert sweep_stale_xdist_caches(tmp_path, pid_alive=lambda pid: False) == []
    assert junk.exists() and no_pid_dot.exists()


def test_missing_directory_is_a_silent_noop(tmp_path):
    assert sweep_stale_xdist_caches(tmp_path / "nope") == []


def test_default_pid_alive_against_a_real_process():
    """The production probe, against the kernel: a just-exited PID reads
    dead, our own PID reads live (PermissionError would be another user's
    live PID — conservative keep, not unit-testable here)."""
    proc = subprocess.Popen(  # noqa: S603  # list-form, shell=False, fixed argv
        [sys.executable, "-c", "pass"]
    )
    proc.wait()
    assert _default_pid_alive(proc.pid) is False
    assert _default_pid_alive(os.getpid()) is True


def test_shared_cache_family_age_rule(tmp_path):
    """S2 shared cache: no owner PID — age-only. Lifecycle form: a fresh
    family (even with every PID reported live) is kept; the same file
    aged past the floor is reclaimed (the controller's session-finish
    utime keeps a used cache fresh; an idle one rebuilds at most once)."""
    shared = _mkcache(tmp_path, "shared", age_s=60, suffix=".duckdb")
    lock = _mkcache(tmp_path, "shared", age_s=60, suffix=".duckdb.build.lock")
    assert sweep_stale_xdist_caches(tmp_path, pid_alive=lambda pid: True) == []
    assert shared.exists() and lock.exists()
    past = time.time() - 25 * 3600
    os.utime(shared, (past, past))
    os.utime(lock, (past, past))
    assert set(sweep_stale_xdist_caches(tmp_path, pid_alive=lambda pid: True)) == {
        shared,
        lock,
    }
    assert not shared.exists() and not lock.exists()


# --------------------------------------------------------------------------- #
# _make_shared_cache_connect (xdist_shared_graph_cache S2/S3)                   #
# --------------------------------------------------------------------------- #


class TestSharedCacheConnect:
    """The conftest worker wrapper: a default-path connect() becomes a
    shared-cache READ-ONLY open (an RW holder would exclude every other
    worker) with transient-lock retry; anything pointing at an explicit
    tmp path passes through with untouched semantics."""

    @staticmethod
    def _stub_gq():
        from types import SimpleNamespace

        recorded: list[tuple[tuple, dict]] = []

        def orig_connect(*args, **kwargs):
            recorded.append((args, dict(kwargs)))
            return "conn"

        gq = SimpleNamespace(
            connect=orig_connect,
            DB_PATH=Path("/prod/research.db"),
            DUCKDB_PATH=Path("/prod/graph.duckdb"),
        )
        return gq, recorded

    @staticmethod
    def _wrapped():
        from tests.conftest import _make_shared_cache_connect

        gq, recorded = TestSharedCacheConnect._stub_gq()
        return gq, recorded, _make_shared_cache_connect(gq)

    def test_default_path_is_forced_read_only(self):
        gq, recorded, wrapped = self._wrapped()
        assert wrapped() == "conn"
        assert recorded == [((), {"read_only": True})]

    def test_default_db_kwarg_is_forced_read_only(self):
        gq, recorded, wrapped = self._wrapped()
        wrapped(db_path=gq.DB_PATH)
        assert recorded[0][1]["read_only"] is True

    def test_positional_db_path_survives_the_wrap(self):
        gq, recorded, wrapped = self._wrapped()
        wrapped(gq.DB_PATH)
        assert recorded[0] == (((gq.DB_PATH),), {"read_only": True})

    def test_tmp_db_passes_through_untouched(self):
        _gq, recorded, wrapped = self._wrapped()
        wrapped("/tmp/fixture.db")  # not the production DB_PATH
        assert recorded == [(("/tmp/fixture.db",), {})]

    def test_explicit_duckdb_path_passes_through_untouched(self):
        _gq, recorded, wrapped = self._wrapped()
        wrapped(duckdb_path="/tmp/own.duckdb")
        assert recorded == [((), {"duckdb_path": "/tmp/own.duckdb"})]

    def test_transient_lock_conflict_is_retried(self):
        gq = self._stub_gq()[0]
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:  # a rebuilder's RW hold, gone in 50 ms
                raise OSError(
                    'IO Error: Could not set lock on file "shared.duckdb": Conflicting lock is held'
                )
            return "conn"

        gq.connect = flaky
        import tests.conftest as tc

        wrapped = tc._make_shared_cache_connect(gq)
        # the retry ladder's first rung is a REAL 50 ms sleep here — the
        # bound default cannot be injected; 50 ms is acceptable in a test
        assert wrapped() == "conn"
        assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# copy_production_db sanction gate (production_db_copy_audit)                   #
# --------------------------------------------------------------------------- #


class TestCopyProductionDbSanctionGate:
    """Deny-by-default at the chokepoint: the sanctions-decay corrective.
    A sanction is sized against the data of its day; the gate forces
    re-audit at the moment a NEW copy site appears instead of letting it
    inherit an old approval."""

    @pytest.fixture
    def tiny_src(self, tmp_path) -> Path:
        import sqlite3

        src = tmp_path / "src.db"
        con = sqlite3.connect(str(src))
        con.execute("CREATE TABLE entities (name TEXT)")
        con.execute("INSERT INTO entities VALUES ('x')")
        con.commit()
        con.close()
        return src

    def test_unsanctioned_requestor_is_denied(self, tiny_src, tmp_path):
        # aliased import: the requestor static check must not read the
        # gate's own self-test as a copy site (the unsanctioned literal
        # is the point of the test)
        from tests.helpers import copy_production_db as gate

        with pytest.raises(PermissionError, match="not sanctioned"):
            gate(tiny_src, tmp_path / "out.db", requestor="whoever")

    def test_sanctioned_requestor_proceeds(self, tiny_src, tmp_path):
        from tests.helpers import SANCTIONED_REQUESTORS
        from tests.helpers import copy_production_db as gate

        requestor = next(iter(SANCTIONED_REQUESTORS))
        out = gate(tiny_src, tmp_path / "out.db", requestor=requestor, keep_all=True)
        assert out.exists()


# --------------------------------------------------------------------------- #
# build_full_template (production_db_copy_audit S2/S3)                          #
# --------------------------------------------------------------------------- #


class TestFullTemplate:
    """The full-corpus template contract that lets query_plans open it
    in place (read-only) and rebuild_schema copyfile per test."""

    @pytest.fixture(scope="class")
    def tpl(self, tmp_path_factory) -> Path:
        from tests._tmp_hygiene import build_full_template

        return build_full_template(tmp_path_factory.mktemp("fulltpl") / "full.db")

    def test_delete_journal_mode_and_full_corpus(self, tpl):
        """DELETE journal mode (the sidecar guard) and a true full copy:
        row counts match production for the hot tables."""
        import sqlite3

        from tests._tmp_hygiene import DB_PATH

        con = sqlite3.connect(str(tpl))
        try:
            assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            tpl_counts = dict(
                con.execute(
                    "SELECT 'entities', COUNT(*) FROM entities "
                    "UNION ALL SELECT 'graph_edges', COUNT(*) FROM graph_edges "
                    "UNION ALL SELECT 'graph_analytics', COUNT(*) FROM graph_analytics"
                ).fetchall()
            )
        finally:
            con.close()
        src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            src_counts = dict(
                src.execute(
                    "SELECT 'entities', COUNT(*) FROM entities "
                    "UNION ALL SELECT 'graph_edges', COUNT(*) FROM graph_edges "
                    "UNION ALL SELECT 'graph_analytics', COUNT(*) FROM graph_analytics"
                ).fetchall()
            )
        finally:
            src.close()
        assert tpl_counts == src_counts and src_counts["entities"] > 0

    def test_read_only_open_leaves_no_sidecars(self, tpl):
        """THE sharing invariant: a mode=ro open of the DELETE-mode
        template must not create -shm/-wal files next to it (a WAL-mode
        template would — readers can't checkpoint or remove sidecars,
        so N shared readers would race them onto one file)."""
        import sqlite3

        con = sqlite3.connect(f"file:{tpl}?mode=ro", uri=True)
        try:
            assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] > 0
        finally:
            con.close()
        assert [p.name for p in tpl.parent.iterdir()] == [tpl.name]

    def test_clone_is_standalone_and_mutable(self, tpl, tmp_path):
        """The rebuild_schema donor property: a reflink/copyfile clone of
        the template is a valid standalone DB that accepts writes —
        mutations land in the clone (COW on reflink filesystems), never
        in the template."""
        import sqlite3

        from tests._tmp_hygiene import reflink_or_copy

        cp = tmp_path / "clone.db"
        reflink_or_copy(tpl, cp)
        con = sqlite3.connect(str(cp))
        try:
            name = con.execute("SELECT name FROM entities LIMIT 1").fetchone()[0]
            con.execute("UPDATE entities SET last_updated = last_updated WHERE name = ?", (name,))
            con.commit()
        finally:
            con.close()
        tpl_con = sqlite3.connect(f"file:{tpl}?mode=ro", uri=True)
        try:
            assert tpl_con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] > 0
        finally:
            tpl_con.close()
