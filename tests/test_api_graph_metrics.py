"""Tests for /api/graph/metrics/<metric> — split from the original
test_api_graph.py for navigability.

Unit tests for /api/graph/metrics/<metric> (Bundle J3). Carries its own _J3_ANALYTICS seed + _seeded_sqlite_db_with_analytics helper (extends the shared _seeded_sqlite_db with graph_analytics rows).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager


import app as A
from tests.conftest import (  # noqa: E402
    _UNIT_SCHEMA, _UNIT_ENTITIES, _UNIT_TAGS, _UNIT_EDGES,
)


# ----- /api/graph/metrics/<metric> (Bundle J3, SQLite-only, runs in QA) ---- #

# Seed rows for graph_analytics used by the J3 tests below. value JSON matches
# the shapes write_analytics produces: {"value": float} for scalar metrics,
# {"community": int, "modularity": float} for louvain (G2), {"componentId": int}
# for wcc.
_J3_ANALYTICS = [
    # pagerank (scalar) — 3 companies, descending
    ("HDFC Bank",  "pagerank",      '{"value": 0.05}'),
    ("ICICI Bank", "pagerank",      '{"value": 0.03}'),
    ("Infosys",    "pagerank",      '{"value": 0.01}'),
    # closeness (scalar, G1) — different ranking than pagerank
    ("HDFC Bank",  "closeness_centrality", '{"value": 0.2}'),
    ("ICICI Bank", "closeness_centrality", '{"value": 0.4}'),
    ("Infosys",    "closeness_centrality", '{"value": 0.6}'),
    # louvain (label, G2 modularity) — HDFC+ICICI in comm 0, Infosys in comm 1
    ("HDFC Bank",  "louvain_community", '{"community": 0, "modularity": 0.55}'),
    ("ICICI Bank", "louvain_community", '{"community": 0, "modularity": 0.55}'),
    ("Infosys",    "louvain_community", '{"community": 1, "modularity": 0.55}'),
    # wcc (label, no modularity)
    ("HDFC Bank",  "weakly_connected_component", '{"componentId": 10}'),
    ("ICICI Bank", "weakly_connected_component", '{"componentId": 10}'),
    ("Infosys",    "weakly_connected_component", '{"componentId": 20}'),
    # malformed value JSON — must be skipped, not crash
    ("HDFC Bank",  "degree_centrality", '{"not_value": true}'),
]


@contextmanager
def _seeded_sqlite_db_with_analytics(tmp_path):
    """Like _seeded_sqlite_db but also seeds graph_analytics rows."""
    db_path = tmp_path / "j3_graph.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_UNIT_SCHEMA)
    conn.executemany(
        "INSERT INTO entities "
        "(name, entity_type, file_path, sector_classification, ticker) "
        "VALUES (?,?,?,?,?)",
        _UNIT_ENTITIES,
    )
    conn.executemany(
        "INSERT INTO entity_tags (entity_name, tag) VALUES (?,?)",
        _UNIT_TAGS,
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
        "VALUES (?,?,?,?)",
        _UNIT_EDGES,
    )
    conn.executemany(
        "INSERT INTO graph_analytics (entity_name, metric, value) "
        "VALUES (?,?,?)",
        _J3_ANALYTICS,
    )
    conn.commit()
    conn.close()

    def _open():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return c

    saved = A.get_db_connection
    A.get_db_connection = _open  # ty: ignore[invalid-assignment]
    try:
        yield A.app.test_client()
    finally:
        A.get_db_connection = saved


class TestGraphMetricsEndpoint:
    """Bundle J3: GET /api/graph/metrics/<metric> surfaces the computed
    centrality/community scores that graph_analytics holds. Before J3 the
    API exposed structural queries only — no way to read these over HTTP."""

    def test_unknown_metric_returns_400_with_allowlist(self, tmp_path):
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/bogus_metric")
        assert r.status_code == 400
        body = r.get_json()
        assert "bogus_metric" in body["error"]
        # The full allowlist is returned so the client can self-correct.
        assert "pagerank" in body["valid_metrics"]
        assert "closeness_centrality" in body["valid_metrics"]  # G1

    def test_metric_name_case_insensitive(self, tmp_path):
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/PAGERANK")
        assert r.status_code == 200
        assert r.get_json()["metric"] == "pagerank"

    def test_scalar_metric_ranked_desc_with_top(self, tmp_path):
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/pagerank?top=2")
        assert r.status_code == 200
        body = r.get_json()
        assert body["metric"] == "pagerank"
        assert body["total"] == 3  # 3 seeded rows
        assert len(body["ranked"]) == 2  # top=2 caps the response
        # Sorted descending by value.
        assert body["ranked"][0] == {"entity": "HDFC Bank", "value": 0.05}
        assert body["ranked"][1] == {"entity": "ICICI Bank", "value": 0.03}

    def test_scalar_metric_default_top_is_10(self, tmp_path):
        # Only 3 rows seeded — default top=10 returns all 3.
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/pagerank")
        body = r.get_json()
        assert len(body["ranked"]) == 3

    def test_scalar_metric_skips_malformed_value_json(self, tmp_path):
        # degree_centrality has one row with malformed JSON; must be skipped,
        # not 500.
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/degree_centrality")
        assert r.status_code == 200
        body = r.get_json()
        assert body["total"] == 0  # the one seeded row was malformed
        assert body["ranked"] == []

    def test_entity_filter_returns_single_row(self, tmp_path):
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/pagerank?entity=infosys")
        assert r.status_code == 200
        body = r.get_json()
        # Case-insensitive entity filter; total counts matches, ranked is the
        # 1-row result.
        assert body["total"] == 1
        assert body["ranked"] == [{"entity": "Infosys", "value": 0.01}]

    def test_entity_filter_unknown_returns_empty(self, tmp_path):
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/pagerank?entity=nonexistent")
        assert r.status_code == 200
        body = r.get_json()
        assert body["total"] == 0
        assert body["ranked"] == []

    def test_label_metric_louvain_groups_with_modularity(self, tmp_path):
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/louvain_community")
        assert r.status_code == 200
        body = r.get_json()
        assert body["metric"] == "louvain_community"
        assert body["total"] == 3
        # G2 modularity surfaced once at the top level (not per-group).
        assert body["modularity"] == 0.55
        # Groups sorted by size desc.
        assert len(body["groups"]) == 2
        g0, g1 = body["groups"]
        assert g0["label"] == 0 and g0["size"] == 2
        assert sorted(g0["members"]) == ["HDFC Bank", "ICICI Bank"]
        assert g1["label"] == 1 and g1["size"] == 1
        assert g1["members"] == ["Infosys"]

    def test_label_metric_wcc_no_modularity_key(self, tmp_path):
        # wcc has no modularity (only louvain does, per G2).
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            r = client.get("/api/graph/metrics/weakly_connected_component")
        body = r.get_json()
        assert r.status_code == 200
        assert "modularity" not in body
        assert len(body["groups"]) == 2

    def test_bad_top_returns_400(self, tmp_path):
        with _seeded_sqlite_db_with_analytics(tmp_path) as client:
            assert client.get("/api/graph/metrics/pagerank?top=abc").status_code == 400
            assert client.get("/api/graph/metrics/pagerank?top=0").status_code == 400
            assert client.get("/api/graph/metrics/pagerank?top=501").status_code == 400

