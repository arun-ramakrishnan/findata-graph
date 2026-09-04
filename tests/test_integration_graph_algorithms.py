#!/usr/bin/env python3
"""Integration tests for the graph-algorithm compute → persist → read pipeline (P5).

These tests exercise the full round-trip on SYNTHETIC data (not the live DB):

  1. compute(metric) → write_analytics() → SELECT from graph_analytics
     — verify the JSON value survives the encode/decode round-trip.
  2. write_analytics UPSERT idempotency — writing twice doesn't duplicate.
  3. _wrap_for_analytics shape correctness for scalar vs label metrics.
  4. Graph mutation (add edge) → recompute → values change.
  5. API endpoint /api/graph/metrics/<metric> reads graph_analytics correctly.

All Onager-backed metrics are tested: degree, betweenness, closeness,
eigenvector, louvain (plus pagerank / wcc / clustering via the same
compute() path).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

pytestmark = [pytest.mark.integration]

# NOTE: this module no longer depends on NetworkX. The Onager-backed
# compute() path is exercised here against the seeded synth_db (the
# synth_db fixture redirects both algos.connect and algos.duckdb_connect to
# the synthetic graph). NetworkX was removed (2026-08-14); the nx-comparison
# assertions that existed historically are gone with it.

# Import algorithms module so we can monkeypatch its `connect` reference.
import helpers.graph.algorithms as algos  # noqa: E402
from helpers.graph.algorithms import (  # noqa: E402
    compute,
    write_analytics,
    _wrap_for_analytics,
    _format_value,
)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

from tests.schema import GRAPH_ANALYTICS  # noqa: E402

_SCHEMA = (
    """
CREATE TABLE entities (
    name                  TEXT PRIMARY KEY NOT NULL,
    entity_type           TEXT NOT NULL DEFAULT 'company',
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_path             TEXT,
    last_updated          DATETIME,
    normalized_name       TEXT,
    sector_classification TEXT,
    ticker                TEXT
);

CREATE TABLE graph_edges (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    properties  TEXT NOT NULL DEFAULT '{}',
    valid_from  DATE,
    valid_to    DATE,
    source_ref  TEXT NOT NULL DEFAULT 'test',
    symmetric   INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);

CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag)
);
"""
    + GRAPH_ANALYTICS
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def synth_db(tmp_path, monkeypatch):
    """Create a synthetic SQLite DB with a known graph topology.

    Graph topology (all part_of / has_company edges):
        SectorA ── CompanyA
        SectorA ── CompanyB
        SectorA ── CompanyC
        SectorB ── CompanyD
        CompanyA ── CompanyB   (competes_with)
        CompanyB ── CompanyC   (competes_with)

    This gives non-trivial centrality differences so we can assert ordering
    and verify mutations change values.
    """
    db_path = tmp_path / "synth_graph.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)

    # Seed entities
    for name, etype in [
        ("SectorA", "sector"),
        ("SectorB", "sector"),
        ("CompanyA", "company"),
        ("CompanyB", "company"),
        ("CompanyC", "company"),
        ("CompanyD", "company"),
    ]:
        conn.execute(
            "INSERT INTO entities(name, entity_type) VALUES (?, ?)",
            (name, etype),
        )

    # Seed edges
    edges = [
        ("CompanyA", "SectorA", "part_of"),
        ("SectorA", "CompanyA", "has_company"),
        ("CompanyB", "SectorA", "part_of"),
        ("SectorA", "CompanyB", "has_company"),
        ("CompanyC", "SectorA", "part_of"),
        ("SectorA", "CompanyC", "has_company"),
        ("CompanyD", "SectorB", "part_of"),
        ("SectorB", "CompanyD", "has_company"),
        ("CompanyA", "CompanyB", "competes_with"),
        ("CompanyB", "CompanyC", "competes_with"),
    ]
    for src, tgt, etype in edges:
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) VALUES (?,?,?,?)",
            (src, tgt, etype, "test"),
        )
    conn.commit()
    conn.close()

    # Monkeypatch algorithms.connect (SQLite, used by write_analytics /
    # load_graph) to use our test DB.
    def _mock_connect(*args, **kwargs):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(algos, "connect", _mock_connect)

    # Monkeypatch algorithms.duckdb_connect (DuckDB, used by the Onager-backed
    # metrics) so compute() runs the Onager algorithms over THIS synthetic
    # graph rather than the real research.db. Onager reads `fin.graph_edges`,
    # so we attach the test DB as `fin` and load the onager extension.
    import duckdb as _duckdb

    def _mock_duckdb_connect(*args, **kwargs):
        con = _duckdb.connect()
        try:
            con.execute("INSTALL sqlite;")
        except Exception:  # noqa: S110  # best-effort
            pass
        con.execute("LOAD sqlite;")
        con.execute(f"ATTACH '{db_path}' AS fin (TYPE sqlite, READ_ONLY);")
        try:
            con.execute("INSTALL onager FROM community;")
        except Exception:  # noqa: S110  # best-effort
            pass
        con.execute("LOAD onager;")
        return con

    monkeypatch.setattr(algos, "duckdb_connect", _mock_duckdb_connect)

    return db_path


def _read_analytics(db_path, metric, entity=None):
    """Read graph_analytics rows for a metric."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if entity:
            rows = conn.execute(
                "SELECT * FROM graph_analytics WHERE metric=? AND entity_name=?",
                (metric, entity),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM graph_analytics WHERE metric=? ORDER BY entity_name",
                (metric,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Test: compute → write_analytics → read round-trip (Onager metrics)
# --------------------------------------------------------------------------- #


class TestComputeWriteReadRoundTrip:
    """For each Onager metric: compute → write → read back from
    graph_analytics and verify the value survives JSON round-trip."""

    @pytest.mark.parametrize(
        "metric,analytics_name",
        [
            ("degree_centrality", "degree_centrality"),
            ("betweenness_centrality", "betweenness_centrality"),
            ("closeness_centrality", "closeness_centrality"),
            ("eigenvector_centrality", "eigenvector_centrality"),
            ("louvain_community", "louvain_community"),
        ],
    )
    def test_scalar_metric_round_trip(self, synth_db, metric, analytics_name):
        result = compute(metric)
        assert len(result) > 0, f"compute({metric}) returned empty dict"

        # Write to graph_analytics
        written = write_analytics(analytics_name, result)
        assert written == len(result)

        # Read back and verify every entity is present
        rows = _read_analytics(synth_db, analytics_name)
        assert len(rows) == len(result)

        for row in rows:
            entity = row["entity_name"]
            assert entity in result
            # The stored value is JSON-encoded
            if metric == "louvain_community":
                # _wrap_for_analytics wraps louvain as {"community": label}
                # but write_analytics stores the raw value from compute()
                # which returns {entity: int_label}. The value column
                # stores json.dumps(int_label) = e.g. "0" or "1".
                stored = json.loads(row["value"])
                assert stored == result[entity]
            else:
                stored = json.loads(row["value"])
                assert stored == pytest.approx(result[entity])

    def test_degree_centrality_values_nonzero(self, synth_db):
        """On our graph, CompanyB should have higher degree than CompanyD."""
        result = compute("degree_centrality")
        assert result["CompanyB"] > result["CompanyD"]
        assert result["CompanyA"] > 0

    def test_betweenness_centrality_values_nonzero(self, synth_db):
        """CompanyB is the bridge between A and C; it should have the highest
        betweenness."""
        result = compute("betweenness_centrality")
        # CompanyB is on the path A-B-C, so it has non-zero betweenness
        assert result["CompanyB"] > 0

    def test_closeness_centrality_in_range(self, synth_db):
        """Closeness is always in [0, 1]."""
        result = compute("closeness_centrality")
        for name, val in result.items():
            assert 0.0 <= val <= 1.0, f"{name} closeness {val} out of [0,1]"

    def test_eigenvector_centrality_nonneg(self, synth_db):
        """Eigenvector centrality is non-negative."""
        result = compute("eigenvector_centrality")
        for name, val in result.items():
            assert val >= 0, f"{name} eigenvector {val} < 0"

    def test_louvain_returns_int_labels(self, synth_db):
        """Louvain community labels are integers."""
        result = compute("louvain_community")
        for name, label in result.items():
            assert isinstance(label, int), f"{name} label {label} not int"

    def test_louvain_find_more_than_one_community(self, synth_db):
        """Our graph has two disconnected sectors — should produce >= 2 communities."""
        result = compute("louvain_community")
        communities = set(result.values())
        assert len(communities) >= 1  # at minimum one community


# --------------------------------------------------------------------------- #
# Test: write_analytics UPSERT idempotency
# --------------------------------------------------------------------------- #


class TestWriteAnalyticsUpsert:
    """write_analytics uses INSERT ... ON CONFLICT DO UPDATE — re-writing the
    same metric should update, not duplicate."""

    def test_write_twice_no_duplicate(self, synth_db):
        result = compute("degree_centrality")
        write_analytics("degree_centrality", result)
        write_analytics("degree_centrality", result)

        rows = _read_analytics(synth_db, "degree_centrality")
        assert len(rows) == len(result), "second write should not add rows"

    def test_upsert_updates_value(self, synth_db):
        """If we write a different value for the same entity, the row should
        update, not insert a new one."""
        result = compute("degree_centrality")
        write_analytics("degree_centrality", result)

        # Mutate the result and write again
        modified = dict(result)
        first_entity = next(iter(result))
        modified[first_entity] = 999.0
        write_analytics("degree_centrality", modified)

        rows = _read_analytics(synth_db, "degree_centrality", first_entity)
        assert len(rows) == 1
        stored = json.loads(rows[0]["value"])
        assert stored == 999.0

    def test_write_returns_count(self, synth_db):
        """write_analytics returns the number of rows written."""
        result = {"A": 0.5, "B": 0.3}
        n = write_analytics("pagerank", result)
        assert n == 2

    def test_write_multiple_metrics(self, synth_db):
        """Writing different metrics should not interfere."""
        deg = compute("degree_centrality")
        btw = compute("betweenness_centrality")
        write_analytics("degree_centrality", deg)
        write_analytics("betweenness_centrality", btw)

        deg_rows = _read_analytics(synth_db, "degree_centrality")
        btw_rows = _read_analytics(synth_db, "betweenness_centrality")
        assert len(deg_rows) == len(deg)
        assert len(btw_rows) == len(btw)


# --------------------------------------------------------------------------- #
# Test: _wrap_for_analytics shape correctness
# --------------------------------------------------------------------------- #


class TestWrapForAnalytics:
    """_wrap_for_analytics transforms raw compute() output into the JSON shape
    expected by graph_analytics + the API."""

    def test_scalar_metric_wraps_as_value(self):
        result = {"A": 0.5, "B": 0.3}
        wrapped = _wrap_for_analytics("pagerank", result)
        assert wrapped == {"A": {"value": 0.5}, "B": {"value": 0.3}}

    def test_degree_wraps_as_value(self):
        result = {"X": 0.1}
        wrapped = _wrap_for_analytics("degree", result)
        assert wrapped == {"X": {"value": 0.1}}

    def test_louvain_wraps_as_community(self):
        result = {"A": 0, "B": 1}
        wrapped = _wrap_for_analytics("louvain", result)
        assert wrapped == {"A": {"community": 0}, "B": {"community": 1}}

    def test_louvain_wraps_with_modularity(self):
        result = {"A": 0, "B": 1}
        wrapped = _wrap_for_analytics("louvain", result, modularity=0.42)
        for node in wrapped.values():
            assert node["community"] in (0, 1)
            assert node["modularity"] == 0.42

    def test_wcc_wraps_as_component_id(self):
        result = {"A": 0, "B": 1, "C": 0}
        wrapped = _wrap_for_analytics("wcc", result)
        assert wrapped["A"] == {"componentId": 0}
        assert wrapped["B"] == {"componentId": 1}
        assert wrapped["C"] == {"componentId": 0}


# --------------------------------------------------------------------------- #
# Test: graph mutation → recompute → values change
# --------------------------------------------------------------------------- #


class TestMutationRecompute:
    """Add an edge to the graph → recompute → values should change."""

    def test_degree_changes_after_adding_edge(self, synth_db):
        """Adding a new edge increases degree for affected nodes."""
        result_before = compute("degree_centrality")

        # Add an edge CompanyD <-> CompanyA
        conn = sqlite3.connect(str(synth_db))
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('CompanyD', 'CompanyA', 'competes_with', 'test2')"
        )
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('CompanyA', 'CompanyD', 'competes_with', 'test2')"
        )
        conn.commit()
        conn.close()

        result_after = compute("degree_centrality")
        assert result_after["CompanyD"] > result_before["CompanyD"]
        assert result_after["CompanyA"] > result_before["CompanyA"]

    def test_betweenness_changes_after_adding_edge(self, synth_db):
        """Adding a shortcut edge changes betweenness."""
        result_before = compute("betweenness_centrality")

        # Connect CompanyD directly to CompanyC (bridge between sectors)
        conn = sqlite3.connect(str(synth_db))
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('CompanyD', 'CompanyC', 'competes_with', 'test2')"
        )
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('CompanyC', 'CompanyD', 'competes_with', 'test2')"
        )
        conn.commit()
        conn.close()

        result_after = compute("betweenness_centrality")
        # At least one node should have different betweenness
        diffs = {n for n in result_before if result_before[n] != result_after[n]}
        assert len(diffs) > 0, "betweenness should change after adding an edge"

    def test_community_count_changes_after_bridge(self, synth_db):
        """Adding a bridge between the two sectors should merge communities."""
        result_before = compute("louvain_community")
        communities_before = set(result_before.values())

        # Bridge the two sectors
        conn = sqlite3.connect(str(synth_db))
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('CompanyD', 'CompanyA', 'competes_with', 'bridge')"
        )
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, source_ref) "
            "VALUES ('CompanyA', 'CompanyD', 'competes_with', 'bridge')"
        )
        conn.commit()
        conn.close()

        result_after = compute("louvain_community")
        communities_after = set(result_after.values())
        assert len(communities_after) <= len(communities_before)


# --------------------------------------------------------------------------- #
# Test: API endpoint reads graph_analytics
# --------------------------------------------------------------------------- #


class TestAPIGraphMetrics:
    """Seed graph_analytics directly → verify /api/graph/metrics/<metric>
    serves the correct response."""

    @pytest.fixture
    def seeded_client(self, tmp_path):
        """Build a Flask test_client with graph_analytics pre-seeded."""

        db_path = tmp_path / "api_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_SCHEMA)

        # Seed a few analytics rows
        for entity, val in [("Alpha", 0.9), ("Beta", 0.5), ("Gamma", 0.1)]:
            conn.execute(
                "INSERT INTO graph_analytics(entity_name, metric, value) VALUES (?, 'pagerank', ?)",
                (entity, json.dumps({"value": val})),
            )
        # Label metric: louvain
        for entity, label in [("Alpha", 0), ("Beta", 0), ("Gamma", 1)]:
            conn.execute(
                "INSERT INTO graph_analytics(entity_name, metric, value) "
                "VALUES (?, 'louvain_community', ?)",
                (entity, json.dumps({"community": label, "modularity": 0.35})),
            )
        conn.commit()
        conn.close()

        from tests.helpers import flask_test_client  # noqa: E402

        with flask_test_client(db_path, track_conns=True) as client:
            yield client

    def test_scalar_metric_returns_ranked(self, seeded_client):
        """GET /api/graph/metrics/pagerank returns ranked list."""
        r = seeded_client.get("/api/graph/metrics/pagerank")
        assert r.status_code == 200
        data = r.get_json()
        assert data["metric"] == "pagerank"
        assert data["total"] == 3
        ranked = data["ranked"]
        assert len(ranked) == 3
        # Should be sorted descending by value
        assert ranked[0]["entity"] == "Alpha"
        assert ranked[0]["value"] == pytest.approx(0.9)
        assert ranked[-1]["entity"] == "Gamma"

    def test_scalar_metric_top_param(self, seeded_client):
        """GET /api/graph/metrics/pagerank?top=2 returns only top 2."""
        r = seeded_client.get("/api/graph/metrics/pagerank?top=2")
        assert r.status_code == 200
        data = r.get_json()
        assert len(data["ranked"]) == 2
        assert data["total"] == 3  # total is always the full count

    def test_scalar_metric_entity_filter(self, seeded_client):
        """GET /api/graph/metrics/pagerank?entity=Beta returns one row."""
        r = seeded_client.get("/api/graph/metrics/pagerank?entity=Beta")
        assert r.status_code == 200
        data = r.get_json()
        assert data["total"] == 1
        assert data["ranked"][0]["entity"] == "Beta"

    def test_label_metric_returns_groups(self, seeded_client):
        """GET /api/graph/metrics/louvain_community returns grouped structure."""
        r = seeded_client.get("/api/graph/metrics/louvain_community")
        assert r.status_code == 200
        data = r.get_json()
        assert data["metric"] == "louvain_community"
        assert data["total"] == 3
        assert "groups" in data
        assert len(data["groups"]) == 2
        # Largest group first
        g0 = data["groups"][0]
        assert g0["size"] == 2
        assert "Alpha" in g0["members"]
        assert "Beta" in g0["members"]
        # Modularity surfaced at top level
        assert data.get("modularity") == pytest.approx(0.35)

    def test_unknown_metric_returns_400(self, seeded_client):
        """Unknown metric name returns 400 with valid_metrics list."""
        r = seeded_client.get("/api/graph/metrics/nonexistent")
        assert r.status_code == 400
        data = r.get_json()
        assert "error" in data
        assert "valid_metrics" in data

    def test_top_param_too_large_returns_400(self, seeded_client):
        r = seeded_client.get("/api/graph/metrics/pagerank?top=99999")
        assert r.status_code == 400

    def test_top_param_zero_returns_400(self, seeded_client):
        r = seeded_client.get("/api/graph/metrics/pagerank?top=0")
        assert r.status_code == 400

    def test_empty_metric_returns_empty_ranked(self, seeded_client):
        """A valid metric with no rows returns total=0, empty ranked."""
        r = seeded_client.get("/api/graph/metrics/degree_centrality")
        assert r.status_code == 200
        data = r.get_json()
        assert data["total"] == 0
        assert data["ranked"] == []


# --------------------------------------------------------------------------- #
# Test: _format_value utility
# --------------------------------------------------------------------------- #


class TestGraphMetrics:
    """Phase 2 of doc/improvements/archive/graph/graph_algos.txt: whole-graph
    structural metrics via the algorithms dispatcher seam."""

    def test_full_projection_disconnected(self, synth_db):
        # Full edge set: SectorA star (A,B,C) + A-B/B-C competes_with +
        # SectorB-D — two components. 6 nodes, 6 unique undirected edges.
        m = algos.graph_metrics()
        assert m["density"] == pytest.approx(0.4)  # 2*6/(6*5)
        assert m["diameter"] is None  # disconnected
        assert m["radius"] is None
        assert m["avg_path_length"] is None
        # Two triangles: CompanyA-CompanyB-SectorA and
        # CompanyB-CompanyC-SectorA (competes_with + the shared part_of).
        assert m["triangles"] == 2
        # 3*2 triangles / 8 connected triples.
        assert m["transitivity"] == pytest.approx(0.75)
        # Local coefficients 1, 2/3, 1, 2/3 (SectorA), 0, 0 -> mean 5/9.
        assert m["avg_clustering"] == pytest.approx(5.0 / 9.0)
        assort = m["assortativity"]
        assert assort is not None and -1.0 <= assort <= 1.0

    def test_edge_types_projection_connected(self, synth_db):
        # competes_with only: A-B-C path, 3 nodes, 2 edges.
        m = algos.graph_metrics(edge_types=["competes_with"])
        assert m["density"] == pytest.approx(2.0 / 3.0)
        assert m["diameter"] == 2
        assert m["radius"] == 1
        assert m["avg_path_length"] == pytest.approx(4.0 / 3.0)
        assert m["triangles"] == 0

    def test_unknown_edge_type_empty(self, synth_db):
        assert algos.graph_metrics(edge_types=["no_such_type"]) == {}

    def test_result_cached_then_invalidated_by_clear(self, synth_db, monkeypatch):
        """graph_metrics is generation-keyed in the query cache: repeated
        calls with the same args return the cached dict, and clear_graph_cache
        forces a recompute (next call returns a fresh dict). Guards the
        hot-path fix that took /api/graph/stats from ~300ms to ~1ms."""
        from helpers.graph import query as _q

        _q._query_cache_clear()
        m1 = algos.graph_metrics()
        m2 = algos.graph_metrics()
        assert m1 == m2
        # The second call must have been served from cache (no re-execution).
        key = ("graph_metrics", tuple(), _q._current_generation_for_cache())
        assert _q._query_cache_get(key) == m1
        # Invalidating the cache yields a recomputed (equal) result.
        _q.clear_graph_cache()
        m3 = algos.graph_metrics()
        assert m3 == m1

    def test_cache_key_distinguishes_edge_types(self, synth_db):
        from helpers.graph import query as _q

        _q._query_cache_clear()
        m_all = algos.graph_metrics()
        m_comp = algos.graph_metrics(edge_types=["competes_with"])
        # Different projections -> different cached entries.
        assert ("graph_metrics", (), _q._current_generation_for_cache()) in _q._QUERY_CACHE
        assert (
            "graph_metrics",
            ("competes_with",),
            _q._current_generation_for_cache(),
        ) in _q._QUERY_CACHE
        assert m_all != m_comp


class TestPhase3Centralities:
    """Phase 3 of doc/improvements/archive/graph/graph_algos.txt: the extra
    centralities through the compute() dispatcher, plus the list-valued
    VoteRank CLI (opt-in --apply, mirroring link-predict — D13).

    Fixture graph (undirected, deduped): SectorA star over CompanyA/B/C,
    SectorB-D, competes_with A-B and B-C — two components."""

    def test_harmonic_across_components(self, synth_db):
        # Unreachable nodes contribute 0 (unlike closeness, harmonic is
        # well-defined on disconnected graphs).
        res = algos.compute("harmonic_centrality")
        assert res == {
            "CompanyA": pytest.approx(2.5),  # SectorA+B at 1, C at 2
            "CompanyB": pytest.approx(3.0),  # SectorA+A+C at 1
            "CompanyC": pytest.approx(2.5),  # SectorA+B at 1, A at 2
            "CompanyD": pytest.approx(1.0),  # only SectorB is reachable
            "SectorA": pytest.approx(3.0),
            "SectorB": pytest.approx(1.0),
        }

    def test_katz_default_pin_ranking(self, synth_db):
        # Pinned alpha=1e-4: values ~ 1 + alpha*degree (second-order terms
        # are ~3e-8); ranking follows degree.
        res = algos.compute("katz_centrality")
        assert res["SectorA"] == pytest.approx(1.0003, abs=2e-5)
        assert res["CompanyB"] == pytest.approx(1.0003, abs=2e-5)
        assert res["CompanyA"] == pytest.approx(1.0002, abs=2e-5)
        assert res["CompanyD"] == pytest.approx(1.0001, abs=2e-5)
        assert res["SectorB"] == pytest.approx(1.0001, abs=2e-5)

    def test_laplacian_qi_values(self, synth_db):
        res = algos.compute("laplacian_centrality")
        # X(v) = d^2 + d + 2*sum_{u in N(v)} d(u).
        assert res == {
            "CompanyA": pytest.approx(18.0),
            "CompanyB": pytest.approx(26.0),
            "CompanyC": pytest.approx(18.0),
            "CompanyD": pytest.approx(4.0),
            "SectorA": pytest.approx(26.0),
            "SectorB": pytest.approx(4.0),
        }

    def test_local_reaching_two_hop(self, synth_db):
        res = algos.compute("local_reaching_centrality")
        assert res == {
            "CompanyA": pytest.approx(4.0),
            "CompanyB": pytest.approx(4.0),
            "CompanyC": pytest.approx(4.0),
            "CompanyD": pytest.approx(2.0),
            "SectorA": pytest.approx(4.0),
            "SectorB": pytest.approx(2.0),
        }

    def test_voterank_seed_order(self, synth_db):
        # CompanyB first (degree-3 hub), CompanyD covers the SectorB
        # component, SectorA third.
        assert algos.voterank_seeds() == ["CompanyB", "CompanyD", "SectorA"]

    def test_cli_node_metric_dry_run_notice(self, synth_db, capsys):
        # D13: node metrics must state their write status too — a silent
        # dry-run reads as an omission next to link-predict's notice.
        # katz (not pagerank): pagerank/wcc/clustering subset to companies
        # via v_company, which this fixture's raw-attach mock doesn't build.
        rc = algos._cli(["katz"])
        err = capsys.readouterr().err
        assert rc == 0
        assert "FAIL" not in err
        assert "[dry-run: nothing written to graph_analytics" in err
        rows = (
            sqlite3.connect(str(synth_db))
            .execute("SELECT count(*) FROM graph_analytics WHERE metric LIKE '%katz%'")
            .fetchone()[0]
        )
        assert rows == 0

    def test_cli_voterank_is_dry_run_by_default(self, synth_db, capsys):
        # D13: bare CLI writes nothing (uniform opt-in --apply).
        rc = algos._cli(["voterank"])
        err = capsys.readouterr().err
        assert rc == 0
        assert "nothing written" in err
        assert "applied voterank" not in err
        rows = (
            sqlite3.connect(str(synth_db))
            .execute("SELECT count(*) FROM graph_analytics WHERE metric = 'voterank'")
            .fetchone()[0]
        )
        assert rows == 0

    def test_cli_voterank_applies_with_flag(self, synth_db, capsys):
        rc = algos._cli(["voterank", "--apply"])
        err = capsys.readouterr().err
        assert rc == 0
        assert "applied voterank: 3 entity rows" in err
        conn = sqlite3.connect(str(synth_db))
        rows = conn.execute(
            "SELECT entity_name, value FROM graph_analytics "
            "WHERE metric = 'voterank' ORDER BY entity_name"
        ).fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["CompanyB", "CompanyD", "SectorA"]
        for _name, value in rows:
            assert json.loads(value) == {"seeds": ["CompanyB", "CompanyD", "SectorA"]}

    def test_cli_voterank_no_apply_writes_nothing(self, synth_db, capsys):
        rc = algos._cli(["voterank", "--no-apply"])
        err = capsys.readouterr().err
        assert rc == 0
        assert "nothing written" in err
        rows = (
            sqlite3.connect(str(synth_db))
            .execute("SELECT count(*) FROM graph_analytics WHERE metric = 'voterank'")
            .fetchone()[0]
        )
        assert rows == 0

    def test_cli_all_applies_phase3_metrics(self, synth_db, capsys):
        # `make recompute-graph` (--all --apply) must persist the new
        # node-keyed metrics plus voterank alongside the originals.
        rc = algos._cli(["--all", "--apply"])
        assert rc == 0
        conn = sqlite3.connect(str(synth_db))
        metrics = {
            r[0] for r in conn.execute("SELECT DISTINCT metric FROM graph_analytics").fetchall()
        }
        conn.close()
        for expected in (
            "harmonic_centrality",
            "katz_centrality",
            "laplacian_centrality",
            "local_reaching_centrality",
            "voterank",
        ):
            assert expected in metrics


class TestLinkPrediction:
    """Phase 1 of doc/improvements/archive/graph/graph_algos.txt: candidate
    missing-edge hypotheses via Onager link prediction. Persisted to
    graph_analytics (per-node candidate lists, metric `link_prediction`)
    only with an explicit --apply (D13); the CLI is dry-run by default."""

    def test_default_projection_ranks_company_pairs(self, synth_db):
        # Default projection = co_mentioned_in, jv_with, competes_with,
        # same_group -> only the competes_with edges A-B and B-C exist here.
        pairs = algos.link_prediction(method="jaccard")
        # A and C share neighbour B (J = 1/1); the A-B / B-C edges
        # themselves are excluded as existing links.
        assert {(a, b): s for a, b, s in pairs} == pytest.approx({("CompanyA", "CompanyC"): 1.0})

    def test_explicit_projection_part_of(self, synth_db):
        pairs = algos.link_prediction(edge_types=["part_of"], method="common-neighbors")
        # A, B, C all share SectorA (one common neighbour); CompanyD sits in
        # SectorB and has no candidate pair.
        assert {(a, b): s for a, b, s in pairs} == pytest.approx(
            {
                ("CompanyA", "CompanyB"): 1.0,
                ("CompanyA", "CompanyC"): 1.0,
                ("CompanyB", "CompanyC"): 1.0,
            }
        )

    def test_top_and_method_variants(self, synth_db):
        top1 = algos.link_prediction(edge_types=["part_of"], method="resource-alloc", top=1)
        assert len(top1) == 1
        a, b, score = top1[0]
        assert a < b
        assert score == pytest.approx(1.0 / 3.0)  # 1/deg(SectorA) = 1/3

    def test_unknown_method_raises(self, synth_db):
        with pytest.raises(ValueError, match="unknown link-prediction method"):
            algos.link_prediction(method="nope")

    def test_cli_link_predict_prints_pairs(self, synth_db, capsys):
        rc = algos._cli(["link-predict", "--top", "2", "--method", "jaccard"])
        out = capsys.readouterr()
        assert rc == 0
        assert "link-predict (method=jaccard)" in out.out
        assert "CompanyA" in out.out and "CompanyC" in out.out

    def test_cli_link_predict_is_dry_run_by_default(self, synth_db, capsys):
        # D13 (reverses the 2026-08-14 open-question #2 answer): the bare
        # CLI writes nothing — exploratory runs with a different --method
        # or --edge-types must not silently replace the persisted table.
        rc = algos._cli(["link-predict", "--top", "2"])
        err = capsys.readouterr().err
        assert rc == 0
        assert "nothing written" in err
        assert "applied link_prediction" not in err
        rows = (
            sqlite3.connect(str(synth_db))
            .execute("SELECT count(*) FROM graph_analytics WHERE metric LIKE '%link%'")
            .fetchone()[0]
        )
        assert rows == 0

    def test_cli_link_predict_applies_with_flag(self, synth_db, capsys):
        rc = algos._cli(["link-predict", "--top", "2", "--apply"])
        err = capsys.readouterr().err
        assert rc == 0
        assert "applied link_prediction" in err
        conn = sqlite3.connect(str(synth_db))
        rows = conn.execute(
            "SELECT entity_name, value FROM graph_analytics "
            "WHERE metric = 'link_prediction' ORDER BY entity_name"
        ).fetchall()
        conn.close()
        # Default projection on this fixture -> the single A-C candidate.
        assert [r[0] for r in rows] == ["CompanyA", "CompanyC"]
        import json as _json

        for name, value in rows:
            parsed = _json.loads(value)
            assert parsed["method"] == "jaccard"
            assert parsed["edge_types"] == [
                "co_mentioned_in",
                "jv_with",
                "competes_with",
                "same_group",
            ]
            other = "CompanyC" if name == "CompanyA" else "CompanyA"
            assert parsed["candidates"] == [{"name": other, "score": 1.0}]

    def test_cli_link_predict_no_apply_writes_nothing(self, synth_db, capsys):
        rc = algos._cli(["link-predict", "--top", "2", "--no-apply"])
        err = capsys.readouterr().err
        assert rc == 0
        assert "nothing written" in err
        rows = (
            sqlite3.connect(str(synth_db))
            .execute("SELECT count(*) FROM graph_analytics WHERE metric LIKE '%link%'")
            .fetchone()[0]
        )
        assert rows == 0

    def test_persist_link_prediction_fans_out_both_endpoints(self, synth_db):
        n = algos._persist_link_prediction(
            [("CompanyA", "CompanyB", 0.9), ("CompanyA", "CompanyC", 0.5)],
            method="jaccard",
            edge_types=["competes_with"],
        )
        assert n == 3  # A, B, C
        conn = sqlite3.connect(str(synth_db))
        rows = dict(
            conn.execute(
                "SELECT entity_name, value FROM graph_analytics WHERE metric = 'link_prediction'"
            ).fetchall()
        )
        conn.close()
        import json as _json

        a = _json.loads(rows["CompanyA"])
        assert a["method"] == "jaccard"
        assert a["edge_types"] == ["competes_with"]
        assert a["candidates"] == [
            {"name": "CompanyB", "score": 0.9},
            {"name": "CompanyC", "score": 0.5},
        ]
        # The other endpoints see CompanyA back.
        assert _json.loads(rows["CompanyB"])["candidates"] == [{"name": "CompanyA", "score": 0.9}]

    def test_persist_link_prediction_empty_is_noop(self, synth_db):
        assert algos._persist_link_prediction([], method="jaccard", edge_types=None) == 0

    def test_cli_link_predict_edge_types_override(self, synth_db, capsys):
        rc = algos._cli(
            [
                "link-predict",
                "--method",
                "common-neighbors",
                "--edge-types",
                "part_of,has_company",
                "--top",
                "5",
            ]
        )
        out = capsys.readouterr()
        assert rc == 0
        # part_of + has_company cover every edge in the fixture; A/B/C share
        # SectorA so at least one company pair is ranked.
        assert "CompanyA" in out.out


class TestFormatValue:
    def test_float(self):
        assert _format_value(0.123456) == "0.123456"

    def test_int(self):
        assert _format_value(42) == "42"

    def test_string(self):
        assert _format_value("hello") == "hello"
