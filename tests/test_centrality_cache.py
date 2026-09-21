#!/usr/bin/env python3
"""Persistent centrality cache (graph_centrality_persistent_cache, 2026-09-21).

The four contracts from the proposal's §4, against a small synthetic
SQLite source built through the full ``query.connect()`` materialisation
(including the ``_materialise_centrality_cache`` stamp):

  (a) warm rebuild -> wrappers serve table contents equal to fresh compute
  (b) generation bump -> rebuild re-stamps (scores track the new edge set)
  (c) read-only reader never writes (no ``.wal`` appears)
  (d) ``--compute`` bypass recomputes and does not read the table

Fixture graph: a competes_with path C1-C2-C3-C4-C5 (C3 is the middle) plus
a listed_on_index star over an IDX hub — the exclusion edge type, so the
stamped scores also prove the centrality projection rode through.
"""

from __future__ import annotations

import sqlite3

import pytest

duckdb = pytest.importorskip("duckdb")

from helpers.graph import algorithms as alg  # noqa: E402
from helpers.graph import query as gq  # noqa: E402

_SQL = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    file_path TEXT,
    sector_classification TEXT,
    ticker TEXT,
    last_updated DATETIME
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag)
);
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
CREATE TABLE hyper_edges (
    id INTEGER PRIMARY KEY,
    edge_type TEXT,
    label TEXT,
    weight REAL,
    valid_from TEXT,
    valid_to TEXT,
    source_ref TEXT
);
CREATE TABLE hyper_incidences (
    edge_id INTEGER,
    entity_name TEXT,
    weight REAL,
    direction TEXT,
    role TEXT,
    valid_from TEXT,
    valid_to TEXT
);
CREATE TABLE db_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_COMPANIES = [f"C{i}" for i in range(1, 6)]
_PATH = [(f"C{i}", f"C{i + 1}") for i in range(1, 5)]
_STAR = [(c, "IDX") for c in _COMPANIES]

_SCORE_TABLES = [
    "degree",
    "closeness",
    "betweenness",
    "eigenvector",
    "harmonic",
    "katz",
    "laplacian",
    "local_reaching",
]


def _build_sqlite(
    db_path, generation: int = 1, extra_edges: list[tuple[str, str, str]] | None = None
) -> None:
    db_path.unlink(missing_ok=True)  # rewrite from scratch (rebuild scenarios)
    sql = sqlite3.connect(str(db_path))
    sql.executescript(_SQL)
    sql.executemany(
        "INSERT INTO entities (name, entity_type, file_path, sector_classification, ticker) "
        "VALUES (?, 'company', ?, 'Testing', NULL)",
        [(c, f"findata/Companies/{c}.md") for c in _COMPANIES],
    )
    sql.execute("INSERT INTO entities (name, entity_type, file_path) VALUES ('IDX', 'index', NULL)")
    sql.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
        "VALUES (?, ?, 'competes_with', 'test')",
        _PATH,
    )
    sql.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
        "VALUES (?, ?, 'listed_on_index', 'test')",
        _STAR,
    )
    if extra_edges:
        sql.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES (?, ?, ?, 'test')",
            extra_edges,
        )
    sql.execute("INSERT INTO db_meta VALUES ('generation', ?)", (str(generation),))
    sql.commit()
    sql.close()


@pytest.fixture()
def cache_db(tmp_path):
    db = tmp_path / "unit_graph.db"
    _build_sqlite(db)
    gq.clear_graph_cache()
    con = gq.connect(db_path=db)  # cold build incl. the centrality stamp
    con.close()
    gq.clear_graph_cache()
    yield db
    gq.clear_graph_cache()


def test_warm_tables_equal_fresh_compute(cache_db):
    """(a) wrappers serve table contents equal to a bypassed fresh compute."""
    con = gq.connect(db_path=cache_db, read_only=True)
    try:
        for metric in _SCORE_TABLES:
            table = dict(
                con.execute(
                    f"SELECT name, score FROM v_centrality_{metric}"  # noqa: S608  # constant list
                ).fetchall()
            )
            assert table, f"v_centrality_{metric} stamped non-empty"
            with alg.bypass_centrality_cache():
                fresh = alg.compute(f"{metric}_centrality", con)
            assert table == fresh, f"{metric}: stamped table != fresh compute"
        louvain_rows = dict(
            con.execute("SELECT name, community_id FROM v_centrality_louvain").fetchall()
        )
        mod_row = con.execute(
            "SELECT value FROM _build_meta WHERE key='louvain_modularity'"
        ).fetchone()
        assert mod_row is not None
        modularity = float(mod_row[0])
        with alg.bypass_centrality_cache():
            fresh_louvain = alg.louvain_communities(con)
        assert louvain_rows == fresh_louvain.labels
        assert modularity == fresh_louvain.modularity
        cached_seeds = alg.voterank_seeds(con)
        assert cached_seeds == [
            r[0]
            for r in con.execute("SELECT name FROM v_centrality_voterank ORDER BY score").fetchall()
        ]
        with alg.bypass_centrality_cache():
            fresh_seeds = alg.voterank_seeds(con)
        assert cached_seeds == fresh_seeds
    finally:
        con.close()


def test_generation_bump_restamps(cache_db):
    """(b) a generation bump invalidates the warm file; the rebuild re-stamps.

    C1-C5 shortcut added: the path middle C3 loses betweenness to the
    endpoints — the re-stamped scores must track the new edge set.
    """
    con = gq.connect(db_path=cache_db, read_only=True)
    try:
        before = alg.betweenness_centrality(con)  # served from the stamp
    finally:
        con.close()
    _build_sqlite(cache_db, generation=2, extra_edges=[("C1", "C5", "competes_with")])
    assert not gq._is_warm(cache_db.with_suffix(".duckdb"), cache_db)
    gq.clear_graph_cache()
    con = gq.connect(db_path=cache_db)  # stale -> rebuild + re-stamp
    try:
        gen_row = con.execute("SELECT value FROM _build_meta WHERE key='generation'").fetchone()
        assert gen_row is not None and gen_row[0] == "2"
        after = alg.betweenness_centrality(con)  # table path on the new stamp
        with alg.bypass_centrality_cache():
            fresh = alg.betweenness_centrality(con)
        assert after == fresh  # re-stamp tracked the new edge set
        assert after != before  # ...and the edge set really changed scores
    finally:
        con.close()


def test_read_only_reader_never_writes(cache_db, monkeypatch):
    """(c) a read-only reader serves from the table and leaves no .wal."""
    wal = cache_db.with_suffix(".duckdb.wal")
    calls = []
    real = alg._cached_central_scores

    def spy(con, table):
        calls.append(table)
        return real(con, table)

    monkeypatch.setattr(alg, "_cached_central_scores", spy)
    con = gq.connect(db_path=cache_db, read_only=True)
    try:
        scores = alg.degree_centrality(con)
        assert scores
        assert "v_centrality_degree" in calls  # the table path actually served
        labels = alg.louvain_communities(con)
        assert labels.labels
    finally:
        con.close()
    assert not wal.exists()


def test_compute_flag_bypasses_table_read(cache_db, monkeypatch, capsys):
    """(d) --compute recomputes and never reads the cache tables."""
    calls = []
    real = alg._cached_central_scores

    def spy(con, table):
        # Record only reads that pass the bypass guard — the contract is
        # "--compute does not READ the table", and the guard precedes any SQL.
        if not alg._BYPASS_CENTRALITY_CACHE.get():
            calls.append(table)
        return real(con, table)

    monkeypatch.setattr(alg, "_cached_central_scores", spy)
    real_connect = gq.connect

    def connect_to_test_db(*args, **kwargs):
        kwargs.setdefault("db_path", cache_db)
        kwargs["read_only"] = True
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(gq, "connect", connect_to_test_db)
    rc = alg._cli(["degree", "--compute", "--top", "3"])
    assert rc == 0
    assert not calls, "--compute must not read the v_centrality_* tables"
    out = capsys.readouterr().out
    assert "C3" in out  # compute actually produced the path-middle leader
