#!/usr/bin/env python3
"""graph_rebuild_fast_path — per-input dirty tracking (S1) + stale fail-fast (S2).

Hermetic unit tests over a seeded sqlite DB (conftest's unit schema): the
copy-then-patch rebuild lane, the fingerprint stamping, the dependency
closure, and the stale read-only posture. Disk-level behaviour (swap
atomicity, io_lock nesting) stays covered by tests/test_graph_disk.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")

from tests.conftest import (  # noqa: E402
    _UNIT_EDGES,
    _UNIT_ENTITIES,
    _UNIT_SCHEMA,
    _UNIT_TAGS,
)

import helpers.graph.query as q  # noqa: E402


@pytest.fixture()
def unit_db(tmp_path: Path) -> Path:
    db = tmp_path / "unit.db"
    con = sqlite3.connect(str(db))
    con.executescript(_UNIT_SCHEMA)
    # db_meta is production-only (helpers.core.db); the unit schema omits it.
    con.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.executemany(
        "INSERT INTO entities "
        "(name, entity_type, file_path, sector_classification, ticker) "
        "VALUES (?,?,?,?,?)",
        _UNIT_ENTITIES,
    )
    con.executemany("INSERT INTO entity_tags (entity_name, tag) VALUES (?,?)", _UNIT_TAGS)
    con.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,?)",
        _UNIT_EDGES,
    )
    con.executemany(
        "INSERT INTO hyper_edges (id, edge_type, label, source_ref) VALUES (?,?,?,?)",
        [(1, "sector", "Banking", "seed"), (2, "sector", "Technology", "seed")],
    )
    con.executemany(
        "INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (?,?)",
        [(1, "HDFC Bank"), (1, "ICICI Bank"), (2, "Infosys"), (2, "No Ticker Co")],
    )
    con.execute("INSERT INTO db_meta (key, value) VALUES ('generation', '100')")
    con.commit()
    con.close()
    return db


@pytest.fixture()
def build_spy(monkeypatch):
    """Count every materialise-unit call so tests can assert skip/rebuild."""
    calls: dict = {
        "edge_tables": [],
        "v_nodes": 0,
        "substrate": 0,
        "hyper": 0,
        "embeddings": 0,
        "note": 0,
    }
    real_edge = q._materialise_edge_table

    def spy_edge(con, etype, spec):
        calls["edge_tables"].append(etype)
        real_edge(con, etype, spec)

    monkeypatch.setattr(q, "_materialise_edge_table", spy_edge)
    for name, key in (
        ("_materialise_v_nodes", "v_nodes"),
        ("_materialise_walk_substrate", "substrate"),
        ("_materialise_hyper", "hyper"),
        ("_materialise_embeddings", "embeddings"),
        ("_materialise_note_embeddings", "note"),
    ):
        real = getattr(q, name)

        def make(real=real, key=key):
            def spy(con, *a, **kw):
                calls[key] += 1
                return real(con, *a, **kw)

            return spy

        monkeypatch.setattr(q, name, make())

    # _materialise_vertices delegates to the two spied halves in the full path
    monkeypatch.setattr(
        q,
        "_materialise_vertices",
        lambda con: (q._materialise_v_nodes(con), q._materialise_embeddings(con)),
    )
    return calls


def _reset_spy(spy: dict) -> None:
    """Zero the counters after the seeding connect — the tests assert on
    what the REBUILD under observation did, not the cold build."""
    for k in spy:
        spy[k] = [] if k == "edge_tables" else 0


def _bump_generation(db: Path) -> None:
    con = sqlite3.connect(str(db))
    con.execute(
        "UPDATE db_meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key='generation'"
    )
    con.commit()
    con.close()


def _meta(db: Path, key: str) -> str | None:
    con = duckdb.connect(str(db.with_suffix(".duckdb")), read_only=True)
    try:
        r = con.execute("SELECT value FROM _build_meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# S1 — fingerprint stamping + copy-then-patch
# --------------------------------------------------------------------------- #
class TestFingerprintStamping:
    def test_full_build_stamps_fingerprints(self, unit_db):
        con = q.connect(unit_db)
        con.close()
        con = duckdb.connect(str(unit_db.with_suffix(".duckdb")), read_only=True)
        try:
            keys = dict(
                con.execute("SELECT key, value FROM _build_meta WHERE key LIKE 'fp:%'").fetchall()
            )
        finally:
            con.close()
        assert "fp:edges:@all" in keys
        assert keys["fp:edges:@all"].split(":")[0] == str(len(_UNIT_EDGES))
        assert "fp:vertices" in keys and "fp:hyper" in keys
        assert keys["fp:embeddings"] == "absent"  # unit db has no company_embeddings


class TestDirtyTracking:
    def test_noop_rebuild_skips_every_table(self, unit_db, build_spy):
        con = q.connect(unit_db)
        con.close()
        _reset_spy(build_spy)
        _bump_generation(unit_db)  # a non-graph sqlite write staled the cache
        q.rebuild(unit_db)
        assert build_spy == {
            "edge_tables": [],
            "v_nodes": 0,
            "substrate": 0,
            "hyper": 0,
            "embeddings": 0,
            "note": 0,
        }
        # the cache is fresh again without a single CTAS
        assert _meta(unit_db, "generation") == "101"

    def test_single_edge_type_write_rebuilds_only_dependents(self, unit_db, build_spy):
        con = q.connect(unit_db)
        con.close()
        _reset_spy(build_spy)
        s = sqlite3.connect(str(unit_db))
        s.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES ('HDFC Bank', 'Infosys', 'jv_with', 't')"
        )
        s.commit()
        s.close()
        _bump_generation(unit_db)
        q.rebuild(unit_db)
        assert build_spy["edge_tables"] == ["jv_with"]
        assert build_spy["substrate"] == 1  # e_dir/e_all_und cover ALL edge types
        assert build_spy["v_nodes"] == 0
        assert build_spy["hyper"] == 0
        # and the new edge is served
        con = q.connect(unit_db, read_only=True)
        try:
            n = con.execute("SELECT COUNT(*) FROM e_jv").fetchone()[0]
        finally:
            con.close()
        assert n == 1

    def test_mutated_edge_row_picked_up(self, unit_db, build_spy):
        con = q.connect(unit_db)
        con.close()
        _reset_spy(build_spy)
        s = sqlite3.connect(str(unit_db))
        s.execute("UPDATE graph_edges SET source_ref='mutated' WHERE edge_type='competes_with'")
        s.commit()
        s.close()
        _bump_generation(unit_db)
        q.rebuild(unit_db)
        assert build_spy["edge_tables"] == ["competes_with"]

    def test_entity_change_rebuilds_vertices_and_all_edges(self, unit_db, build_spy):
        con = q.connect(unit_db)
        con.close()
        _reset_spy(build_spy)
        s = sqlite3.connect(str(unit_db))
        s.execute(
            "INSERT INTO entities (name, entity_type, file_path, sector_classification) "
            "VALUES ('Axis Bank', 'company', 'findata/Companies/Banking/Axis_Bank.md', 'Banking')"
        )
        s.commit()
        s.close()
        _bump_generation(unit_db)
        q.rebuild(unit_db)
        # ids are row_number() over entities — any entity change shifts them,
        # so every v_node-joined table must follow (the dependency closure).
        assert build_spy["v_nodes"] == 1
        assert len(build_spy["edge_tables"]) == len(q.EDGE_REGISTRY)
        assert build_spy["substrate"] == 1
        assert build_spy["embeddings"] == 1

    def test_patch_fallback_without_fingerprints(self, unit_db, build_spy):
        con = q.connect(unit_db)
        con.close()
        raw = duckdb.connect(str(unit_db.with_suffix(".duckdb")))
        raw.execute("DELETE FROM _build_meta WHERE key LIKE 'fp:%'")
        raw.close()
        _reset_spy(build_spy)
        _bump_generation(unit_db)
        q.rebuild(unit_db)
        # full build: every unit rebuilt, fingerprints re-stamped for next time
        assert build_spy["v_nodes"] == 1
        assert len(build_spy["edge_tables"]) == len(q.EDGE_REGISTRY)
        assert _meta(unit_db, "fp:edges:@all") is not None


# --------------------------------------------------------------------------- #
# S2 — stale fail-fast on the read-only lane
# --------------------------------------------------------------------------- #
class TestStaleFailFast:
    def test_stale_read_only_fails_fast(self, unit_db, monkeypatch):
        monkeypatch.delenv("GRAPH_STALE_REBUILD", raising=False)
        con = q.connect(unit_db)
        con.close()
        _bump_generation(unit_db)
        with pytest.raises(q.GraphCacheStaleError, match="make graph-rebuild"):
            q.connect(unit_db, read_only=True)
        # the cache file survives (the operator can `make graph-rebuild`,
        # which patches it — no full rebuild is forced by the failed open)
        assert unit_db.with_suffix(".duckdb").exists()

    def test_stale_read_only_inline_escape(self, unit_db, monkeypatch):
        monkeypatch.setenv("GRAPH_STALE_REBUILD", "inline")
        con = q.connect(unit_db)
        con.close()
        _bump_generation(unit_db)
        con = q.connect(unit_db, read_only=True)  # today's behaviour restored
        con.close()
        assert _meta(unit_db, "generation") == "101"

    def test_cold_read_only_still_builds(self, unit_db, monkeypatch):
        monkeypatch.delenv("GRAPH_STALE_REBUILD", raising=False)
        con = q.connect(unit_db, read_only=True)  # no cache file yet
        con.close()
        assert _meta(unit_db, "schema_version") == q._SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# S3 — single-scan fold: parity witness
# --------------------------------------------------------------------------- #
class TestSingleScanParity:
    """The fold to _edge_resolved must preserve the per-table JOIN semantics
    exactly. The oracle here is the PRE-FOLD semantics — name→id resolution
    against v_node with per-kind filters — enumerated independently of the
    fold machinery."""

    def test_edge_tables_match_name_join_oracle(self, unit_db):
        con = q.connect(unit_db)
        con.close()
        dcon = duckdb.connect(str(unit_db.with_suffix(".duckdb")), read_only=True)
        try:
            ids = dict(dcon.execute("SELECT name, id FROM v_node").fetchall())
            seed_by_type: dict[str, list[tuple[str, str, str]]] = {}
            for src, tgt, et, ref in _UNIT_EDGES:
                seed_by_type.setdefault(et, []).append((src, tgt, ref))
            for et, spec in q.EDGE_REGISTRY.items():
                rows = dcon.execute(
                    f"SELECT * FROM {spec['table']}"  # noqa: S608  # rides the EDGE_REGISTRY constant
                ).fetchall()
                expected = [
                    (ids[src], ids[tgt], 1.0, "{}", ref, None, None)
                    for src, tgt, ref in seed_by_type.get(et, [])
                ]
                assert sorted(rows) == sorted(expected), et
            n_all = dcon.execute("SELECT COUNT(*) FROM e_all_und").fetchone()[0]
            assert n_all == 2 * len(_UNIT_EDGES)
        finally:
            dcon.close()

    def test_kind_filter_drops_cross_kind_endpoints(self, unit_db):
        # The fold's risk point: _edge_resolved resolves BOTH endpoints
        # against ALL of v_node; the per-table kind filter must reproduce
        # the old projection-table JOIN (which bound companies only). A
        # company->sector row typed competes_with must DROP, not bind the
        # sector's id.
        s = sqlite3.connect(str(unit_db))
        s.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES ('Banking', 'HDFC Bank', 'competes_with', 'x')"
        )
        s.commit()
        s.close()
        con = q.connect(unit_db)
        con.close()
        dcon = duckdb.connect(str(unit_db.with_suffix(".duckdb")), read_only=True)
        try:
            n = dcon.execute("SELECT COUNT(*) FROM e_competes").fetchone()[0]
        finally:
            dcon.close()
        assert n == 1  # only HDFC Bank -> ICICI Bank survives

    def test_witness_has_teeth(self, unit_db):
        # mutation sensitivity: the row comparator must be able to FAIL —
        # two different edge tables' content is not equal
        con = q.connect(unit_db)
        con.close()
        dcon = duckdb.connect(str(unit_db.with_suffix(".duckdb")), read_only=True)
        try:
            belongs = sorted(dcon.execute("SELECT * FROM e_belongs").fetchall())
            has = sorted(dcon.execute("SELECT * FROM e_has").fetchall())
        finally:
            dcon.close()
        assert belongs != has
