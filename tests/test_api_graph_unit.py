"""Tests for the /api/graph/* Flask endpoints — split from the original
test_api_graph.py for navigability.

Unit tests (SQLite-only, run in `make qa`) for the /api/graph/* Flask endpoints: /stats, _resolve_entity_or_404 + 404 split, ETag/cache headers, /shortest param validation, _normalise_as_of, /refresh, and the graph-connection lazy-init / thread-safety guards.

Shared schema + seed + unit_client fixture live in tests/conftest.py.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import app as A  # noqa: F401 — monkeypatched by unit_client
from tests.conftest import (  # noqa: E402
    _UNIT_EDGES,
)
from tests.conftest import seeded_graph_sqlite_db as _seeded_sqlite_db


# ----- /api/graph/stats (SQLite-only, runs in QA) ------------------------- #

class TestGraphStats:
    def test_stats_response_shape(self, unit_client):
        r = unit_client.get("/api/graph/stats")
        assert r.status_code == 200
        data = r.get_json()
        # Top-level keys present.
        assert set(data) == {"entities", "edges", "hygiene", "sectors",
                             "staleness", "structure"}
        # entities sub-shape: 4 companies + 2 sectors (Banking, Technology).
        assert data["entities"]["total"] == 6
        assert data["entities"]["by_type"]["company"] == 4
        assert data["entities"]["by_type"]["sector"] == 2
        # edges sub-shape: total + by_type mapping.
        assert data["edges"]["total"] == len(_UNIT_EDGES)
        # 4 part_of (HDFC, ICICI, Infosys, No Ticker Co → their sectors),
        # 1 has_company (Banking → HDFC), 1 competes_with, 1 subsidiary_of.
        assert data["edges"]["by_type"]["part_of"] == 4
        assert data["edges"]["by_type"]["has_company"] == 1
        assert data["edges"]["by_type"]["competes_with"] == 1
        assert data["edges"]["by_type"]["subsidiary_of"] == 1
        # hygiene counters — the seeded DB has 1 company with no ticker and
        # zero orphans / self-loops / orphan edges.
        assert data["hygiene"]["no_ticker"] == 1
        assert data["hygiene"]["orphan_companies"] == 0
        assert data["hygiene"]["self_loops"] == 0
        assert data["hygiene"]["orphan_edges"] == 0
        assert data["hygiene"]["conflicting_market_cap"] == 0

    def test_stats_staleness_fresh_when_no_analytics(self, unit_client):
        """F8: no graph_analytics rows → most_recent_analytics is NULL → not
        stale (nothing to compare against; mirrors stats.py:148 guard)."""
        data = unit_client.get("/api/graph/stats").get_json()
        assert data["staleness"]["stale"] is False
        assert data["staleness"]["most_recent_analytics_compute"] is None

    def test_stats_staleness_fresh_when_analytics_at_or_after_entities(self, tmp_path):
        """F8: analytics computed after the last entity update → not stale."""
        with _seeded_sqlite_db(tmp_path) as client:
            # Stamp one entity's last_updated to yesterday and analytics to now.
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "UPDATE entities SET last_updated = '2026-01-01' WHERE name = 'HDFC Bank'"
            )
            conn.execute(
                "INSERT INTO graph_analytics(entity_name, metric, value, computed_at) "
                "VALUES ('HDFC Bank', 'pagerank', '0.5', '2026-06-01 12:00:00')"
            )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/stats").get_json()
        assert data["staleness"]["stale"] is False
        assert data["staleness"]["most_recent_entity_update"] == "2026-01-01"
        assert data["staleness"]["most_recent_analytics_compute"] == "2026-06-01 12:00:00"

    def test_stats_staleness_flagged_when_entity_newer_than_analytics(self, tmp_path):
        """F8: an entity updated after the last analytics compute → stale=True.

        This is the signal operators need to run `make recompute-graph`."""
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            # Analytics computed June 1; entity updated June 2 (later day).
            conn.execute(
                "UPDATE entities SET last_updated = '2026-06-02' WHERE name = 'HDFC Bank'"
            )
            conn.execute(
                "INSERT INTO graph_analytics(entity_name, metric, value, computed_at) "
                "VALUES ('HDFC Bank', 'pagerank', '0.5', '2026-06-01 12:00:00')"
            )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/stats").get_json()
        assert data["staleness"]["stale"] is True
        assert data["staleness"]["most_recent_entity_update"] == "2026-06-02"
        assert data["staleness"]["most_recent_analytics_compute"] == "2026-06-01 12:00:00"

    def test_stats_structure_block_values(self, unit_client):
        """Phase 2: Onager structural metrics over the seeded graph.

        6 nodes (4 companies + 2 sectors), 6 unique undirected edges
        (has_company Banking->HDFC dedups against HDFC->Banking part_of).
        Connected: diameter 4 (HDFC Bank <-> No Ticker Co via ICICI/Infosys/
        Technology), radius 2 (Infosys is everyone's nearest hub), APL
        31/15. One triangle (HDFC Bank-ICICI Bank-Banking); transitivity
        3/7; mean local clustering 7/18."""
        data = unit_client.get("/api/graph/stats").get_json()
        m = data["structure"]
        assert m is not None
        assert m["density"] == pytest.approx(0.4)
        assert m["diameter"] == 4
        assert m["radius"] == 2
        assert m["avg_path_length"] == pytest.approx(31.0 / 15.0)
        assert m["triangles"] == 1
        assert m["transitivity"] == pytest.approx(3.0 / 7.0)
        assert m["avg_clustering"] == pytest.approx(7.0 / 18.0)
        assort = m["assortativity"]
        assert assort is not None and -1.0 <= assort <= 1.0

    def test_stats_structure_none_when_graph_layer_fails(self, unit_client, monkeypatch):
        """Degrade gracefully: a broken graph layer yields structure=null
        while the SQLite-side payload stays intact (200 + full shape)."""
        def _boom():
            raise RuntimeError("graph layer down")

        monkeypatch.setattr(A, "get_graph_connection", _boom)
        r = unit_client.get("/api/graph/stats")
        assert r.status_code == 200
        data = r.get_json()
        assert data["structure"] is None
        assert data["entities"]["total"] == 6

    def test_stats_sectors_distribution(self, unit_client):
        r = unit_client.get("/api/graph/stats")
        data = r.get_json()
        # Two company-bearing sectors: Banking (2) and Technology (2).
        assert data["sectors"]["count"] == 2
        sizes = {s["sector"]: s["n"] for s in data["sectors"]["top"]}
        assert sizes["Banking"] == 2
        assert sizes["Technology"] == 2
        # min/max/mean over [2, 2] is all 2.
        assert data["sectors"]["size_distribution"]["min"] == 2
        assert data["sectors"]["size_distribution"]["max"] == 2
        assert data["sectors"]["size_distribution"]["mean"] == 2.0


class TestGraphStatsConflictingMarketCap:
    """C6: the graph-health CTE must surface conflicting_market_cap — companies
    with >1 distinct market_cap tag. This is the runtime tripwire for the
    latent A1/A2 divergence (SQLite vs DuckDB market_cap derivation)."""

    def test_zero_conflicts_in_clean_seeded_db(self, unit_client):
        data = unit_client.get("/api/graph/stats").get_json()
        assert data["hygiene"]["conflicting_market_cap"] == 0

    def test_detects_conflict_when_company_has_two_cap_tags(self, tmp_path):
        """Inject a second market_cap tag on a company and verify the counter
        catches it."""
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            # HDFC Bank already has market_cap/large_cap; add mid_cap conflict.
            conn.execute(
                "INSERT INTO entity_tags (entity_name, tag) "
                "VALUES ('HDFC Bank', 'market_cap/mid_cap')"
            )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/stats").get_json()
        assert data["hygiene"]["conflicting_market_cap"] == 1

    def test_multiple_conflicting_companies_count_separately(self, tmp_path):
        """Two companies with conflicting caps should both be counted."""
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            # HDFC Bank already has large_cap; add mid_cap.
            conn.execute(
                "INSERT INTO entity_tags (entity_name, tag) "
                "VALUES ('HDFC Bank', 'market_cap/mid_cap')"
            )
            # Infosys already has large_cap; add small_cap.
            conn.execute(
                "INSERT INTO entity_tags (entity_name, tag) "
                "VALUES ('Infosys', 'market_cap/small_cap')"
            )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/stats").get_json()
        assert data["hygiene"]["conflicting_market_cap"] == 2


# ----- C-series analytical endpoints (SQLite-only) ------------------------- #

class TestCoMentions:
    """C1: /api/graph/co-mentions"""

    def test_response_shape_empty(self, unit_client):
        """Seeded DB has no co_mentioned_in edges → empty ranked list."""
        r = unit_client.get("/api/graph/co-mentions")
        assert r.status_code == 200
        data = r.get_json()
        assert set(data) == {"ranked"}
        assert data["ranked"] == []

    def test_returns_ranked_with_counts(self, tmp_path):
        """Inject co_mentioned_in edges and verify ranking."""
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            conn.executemany(
                "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
                "VALUES (?,?,?,?)",
                [
                    ("HDFC Bank", "Edition1", "co_mentioned_in", "seed"),
                    ("HDFC Bank", "Edition2", "co_mentioned_in", "seed"),
                    ("HDFC Bank", "Edition3", "co_mentioned_in", "seed"),
                    ("ICICI Bank", "Edition1", "co_mentioned_in", "seed"),
                ],
            )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/co-mentions").get_json()
        assert len(data["ranked"]) == 2
        assert data["ranked"][0] == {"entity": "HDFC Bank", "co_mentions": 3}
        assert data["ranked"][1] == {"entity": "ICICI Bank", "co_mentions": 1}

    def test_top_param_limits_results(self, tmp_path):
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            for i in range(10):
                conn.execute(
                    "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
                    "VALUES (?,?,?,?)",
                    (f"Co{i}", "Edition1", "co_mentioned_in", "seed"),
                )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/co-mentions?top=3").get_json()
        assert len(data["ranked"]) == 3

    def test_bad_top_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/co-mentions?top=abc")
        assert r.status_code == 400


class TestCrossSectorBridges:
    """C3: /api/graph/bridges"""

    def test_response_shape_empty(self, unit_client):
        """Seeded DB has no jv_with/acquired edges → empty bridges list."""
        r = unit_client.get("/api/graph/bridges")
        assert r.status_code == 200
        data = r.get_json()
        assert set(data) == {"bridges"}
        assert data["bridges"] == []

    def test_detects_cross_sector_bridge(self, tmp_path):
        """Inject acquired edge between companies in different sectors."""
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
                "VALUES (?,?,?,?)",
                ("HDFC Bank", "Infosys", "acquired", "seed"),
            )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/bridges").get_json()
        assert len(data["bridges"]) == 1
        bridge = data["bridges"][0]
        assert bridge["edge_type"] == "acquired"
        assert bridge["sector_a"] == "Banking"
        assert bridge["sector_b"] == "Technology"
        assert bridge["count"] == 1

    def test_same_sector_not_a_bridge(self, tmp_path):
        """Edge within the same sector should not appear."""
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
                "VALUES (?,?,?,?)",
                ("HDFC Bank", "ICICI Bank", "acquired", "seed"),
            )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/bridges").get_json()
        assert data["bridges"] == []


class TestEdgesByYear:
    """C4: /api/graph/edges-by-year"""

    def test_response_shape_empty(self, unit_client):
        """Seeded DB has no dated edges → empty timeline."""
        r = unit_client.get("/api/graph/edges-by-year")
        assert r.status_code == 200
        data = r.get_json()
        assert set(data) == {"timeline"}
        assert data["timeline"] == []

    def test_returns_chronological_timeline(self, tmp_path):
        """Inject dated edges and verify year/edge_type/count rows."""
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            conn.executemany(
                "INSERT INTO graph_edges (source, target, edge_type, source_ref, valid_from) "
                "VALUES (?,?,?,?,?)",
                [
                    ("HDFC Bank", "ICICI Bank", "acquired", "seed", "2023-06-01"),
                    ("HDFC Bank", "Infosys", "jv_with", "seed", "2024-01-15"),
                    ("ICICI Bank", "Infosys", "acquired", "seed", "2023-11-30"),
                ],
            )
            conn.commit()
            conn.close()
            data = client.get("/api/graph/edges-by-year").get_json()
        assert len(data["timeline"]) == 2
        assert data["timeline"][0] == {"year": "2023", "edge_type": "acquired", "count": 2}
        assert data["timeline"][1] == {"year": "2024", "edge_type": "jv_with", "count": 1}

    def test_undated_edges_excluded(self, tmp_path):
        """Edges with NULL valid_from should not appear in the timeline."""
        with _seeded_sqlite_db(tmp_path) as client:
            db_path = tmp_path / "unit_graph.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO graph_edges (source, target, edge_type, source_ref, valid_from) "
                "VALUES (?,?,?,?,?)",
                ("HDFC Bank", "ICICI Bank", "acquired", "seed", "2025-01-01"),
            )
            # part_of edges have NULL valid_from (already in seed) — should be excluded
            conn.commit()
            conn.close()
            data = client.get("/api/graph/edges-by-year").get_json()
        assert len(data["timeline"]) == 1
        assert data["timeline"][0]["year"] == "2025"


# ----- _resolve_entity_or_404 + JSON 404 split (runs in QA) --------------- #

class TestResolveAnd404:
    def test_peers_unknown_company_returns_json_404(self, unit_client):
        # The resolver 404s before any DuckDB call — exercises both the
        # resolver and the JSON 404 errorhandler under /api/.
        r = unit_client.get("/api/graph/peers/Nonexistent")
        assert r.status_code == 404
        assert r.is_json
        assert "Nonexistent" in r.get_json()["error"]

    def test_peers_case_insensitive_resolver(self, unit_client, monkeypatch):
        # The resolver returns the canonical name regardless of URL casing.
        # We don't actually invoke DuckDB here (no graph_con wired); we just
        # check the canonical-name plumbing by stubbing peers().
        import helpers.graph.query as q

        seen = {}

        def fake_peers(con, company):
            seen["company"] = company
            return []

        # Patch where the route imports it from (inside the function body the
        # import is `from helpers.graph.query import peers`, so patching the
        # source module is sufficient).
        monkeypatch.setattr(q, "peers", fake_peers)
        # Also wire get_graph_connection to a dummy so no real DuckDB is hit.
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        r = unit_client.get("/api/graph/peers/hdfc%20bank")
        assert r.status_code == 200
        assert r.get_json()["company"] == "HDFC Bank"
        assert seen["company"] == "HDFC Bank"

    def test_non_api_404_returns_html(self, unit_client):
        # Outside /api/, the new errorhandler must keep returning HTML so the
        # browser's address-bar navigation doesn't suddenly serve JSON. Pick
        # a path that doesn't match any registered route (note: /entity/<path>
        # is registered and always 200s, so it won't trigger a 404).
        r = unit_client.get("/this-route-does-not-exist-anywhere")
        assert r.status_code == 404
        assert not r.is_json
        assert b"<html" in r.data.lower()

    def test_api_404_returns_json(self, unit_client):
        # A path under /api/ that doesn't match any route → JSON 404.
        r = unit_client.get("/api/nonexistent")
        assert r.status_code == 404
        assert r.is_json
        assert "error" in r.get_json()


# ----- /api/graph/semantic/<name> (VSS, deferred N5 item) ------------------ #

class TestGraphSemantic:
    """GET /api/graph/semantic/<name> — vector-similarity neighbours.

    The route resolves the entity case-insensitively (404 if unknown), then
    delegates to ``helpers.graph.query.semantic_neighbors``. DuckDB is stubbed
    so the tests run under `make qa` (no live embeddings required)."""

    def test_semantic_response_shape(self, unit_client, monkeypatch):
        import helpers.graph.query as q

        seen = {}

        def fake_semantic(con, company, k=10, metric="cosine",
                          cross_sector=False):
            seen.update(company=company, k=k, metric=metric,
                        cross_sector=cross_sector)
            return [("Infosys", "Technology", 0.9),
                    ("TCS", "Technology", 0.85)]

        monkeypatch.setattr(q, "semantic_neighbors", fake_semantic)
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        r = unit_client.get("/api/graph/semantic/hdfc%20bank?k=2&metric=cosine")
        assert r.status_code == 200
        data = r.get_json()
        assert data["company"] == "HDFC Bank"  # canonical name
        assert data["k"] == 2
        assert data["metric"] == "cosine"
        assert data["cross_sector"] is False
        assert data["neighbors"] == [
            {"name": "Infosys", "sector": "Technology", "similarity": 0.9},
            {"name": "TCS", "sector": "Technology", "similarity": 0.85},
        ]
        assert seen["company"] == "HDFC Bank"
        assert seen["k"] == 2

    def test_semantic_cross_sector_flag(self, unit_client, monkeypatch):
        import helpers.graph.query as q

        seen = {}
        monkeypatch.setattr(q, "semantic_neighbors",
                            lambda con, company, k=10, metric="cosine",
                            cross_sector=False: (seen.update(
                                cross_sector=cross_sector), [])[1])
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        unit_client.get("/api/graph/semantic/HDFC%20Bank?cross_sector=true")
        assert seen["cross_sector"] is True

    def test_semantic_unknown_company_404(self, unit_client):
        r = unit_client.get("/api/graph/semantic/Nonexistent")
        assert r.status_code == 404
        assert r.is_json
        assert "Nonexistent" in r.get_json()["error"]

    def test_semantic_bad_k_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/semantic/HDFC%20Bank?k=abc")
        assert r.status_code == 400

    def test_semantic_negative_k_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/semantic/HDFC%20Bank?k=-1")
        assert r.status_code == 400

    def test_semantic_bad_metric_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/semantic/HDFC%20Bank?metric=bogus")
        assert r.status_code == 400

    def test_semantic_empty_neighbors_not_error(self, unit_client, monkeypatch):
        """No embeddings / no neighbours → 200 with an empty list (mirrors the
        CLI's no-results behaviour), not a 500."""
        import helpers.graph.query as q

        monkeypatch.setattr(q, "semantic_neighbors", lambda *a, **k: [])
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        r = unit_client.get("/api/graph/semantic/HDFC%20Bank")
        assert r.status_code == 200
        assert r.get_json()["neighbors"] == []


class TestGraphSimilarNotes:
    """GET /api/graph/similar/<path:note_path> — sql_capability_unlocks A4.

    Read-only GET over helpers.graph.query.similar_notes (v_note_embeddings
    KNN). 404 for an unknown/unembedded note; a findata-relative path gets
    the findata/ prefix added; k/doc_type validated."""

    def test_similar_response_shape(self, unit_client, monkeypatch):
        import helpers.graph.query as q

        seen = {}

        def fake_similar(con, file_path, k=10, doc_type=None):
            seen.update(file_path=file_path, k=k, doc_type=doc_type)
            return [("findata/Companies/Banking/ICICI_Bank.md", "ICICI Bank", 0.93)]

        monkeypatch.setattr(q, "similar_notes", fake_similar)
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        r = unit_client.get(
            "/api/graph/similar/Companies/Banking/Hdfc_Bank.md?k=1&doc_type=company"
        )
        assert r.status_code == 200
        data = r.get_json()
        # findata/ prefix added when the caller passes a vault-relative path.
        assert data["note"] == "findata/Companies/Banking/Hdfc_Bank.md"
        assert data["k"] == 1
        assert data["doc_type"] == "company"
        assert data["neighbors"] == [
            {"file_path": "findata/Companies/Banking/ICICI_Bank.md",
             "title": "ICICI Bank", "similarity": 0.93}
        ]
        assert seen["file_path"] == "findata/Companies/Banking/Hdfc_Bank.md"
        assert seen["k"] == 1

    def test_similar_prefixed_path_unchanged(self, unit_client, monkeypatch):
        import helpers.graph.query as q

        seen = {}
        monkeypatch.setattr(
            q, "similar_notes",
            lambda con, fp, k=10, doc_type=None: (seen.update(file_path=fp), [])[1])
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        r = unit_client.get("/api/graph/similar/findata/Sectors/Banking.md")
        assert r.status_code == 200
        assert seen["file_path"] == "findata/Sectors/Banking.md"

    def test_similar_unknown_note_404(self, unit_client, monkeypatch):
        import helpers.graph.query as q

        monkeypatch.setattr(q, "similar_notes", lambda *a, **k: None)
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        r = unit_client.get("/api/graph/similar/Companies/Nope.md")
        assert r.status_code == 404
        assert "no embedded note" in r.get_json()["error"]

    def test_similar_bad_k_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/similar/Companies/X.md?k=abc")
        assert r.status_code == 400

    def test_similar_negative_k_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/similar/Companies/X.md?k=-1")
        assert r.status_code == 400


class TestGraphEditionCompanies:
    """GET /api/graph/edition_companies — sql_capability_unlocks A4.

    Read-only GET over helpers.graph.query.edition_companies (edge-free
    reverse of cited_in). 400 without ?edition=, 404 unresolvable."""

    def test_edition_companies_response_shape(self, unit_client, monkeypatch):
        import helpers.graph.query as q

        seen = {}

        def fake_edition(con, edition, k=10):
            seen.update(edition=edition, k=k)
            return [("findata/Companies/Defense/Bharat_Electronics.md",
                     "Bharat_Electronics", 0.84)]

        monkeypatch.setattr(q, "edition_companies", fake_edition)
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        r = unit_client.get("/api/graph/edition_companies?edition=BEL_HUL_Tata_Capital&k=5")
        assert r.status_code == 200
        data = r.get_json()
        assert data["edition"] == "BEL_HUL_Tata_Capital"
        assert data["k"] == 5
        assert data["companies"] == [
            {"file_path": "findata/Companies/Defense/Bharat_Electronics.md",
             "title": "Bharat_Electronics", "similarity": 0.84}
        ]
        assert seen["k"] == 5

    def test_edition_required_400(self, unit_client):
        r = unit_client.get("/api/graph/edition_companies")
        assert r.status_code == 400
        assert "edition" in r.get_json()["error"]

    def test_edition_unresolvable_404(self, unit_client, monkeypatch):
        import helpers.graph.query as q

        monkeypatch.setattr(q, "edition_companies", lambda *a, **k: None)
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())

        r = unit_client.get("/api/graph/edition_companies?edition=Nope")
        assert r.status_code == 404

    def test_edition_bad_k_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/edition_companies?edition=X&k=abc")
        assert r.status_code == 400


# ----- /api/graph/* cache headers (C4, SQLite-only) ----------------------- #

class TestGraphCacheHeaders:
    """C4: GET /api/graph/* responses carry an ETag (from _build_meta.built_at)
    and Cache-Control: no-cache, with a 304 short-circuit on If-None-Match.

    Pure unit tests — monkeypatch _graph_build_etag so they don't need the
    live DuckDB cache, and use /api/graph/stats (SQLite-only) so they run
    under `make qa`.
    """

    def test_graph_response_has_etag_and_cache_control(self, unit_client, monkeypatch):
        """A GET /api/graph/* 200 carries both Cache-Control: no-cache and an
        ETag derived from the cache build time."""
        monkeypatch.setattr(A, "_graph_build_etag", lambda: 'W/"graph-2026-07-22"')
        r = unit_client.get("/api/graph/stats")
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "no-cache"
        assert r.headers["ETag"] == 'W/"graph-2026-07-22"'

    def test_graph_304_on_if_none_match(self, unit_client, monkeypatch):
        """When the client sends If-None-Match equal to the current ETag, the
        server returns 304 with an empty body (per RFC 7232 §4.1)."""
        etag = 'W/"graph-2026-07-22"'
        monkeypatch.setattr(A, "_graph_build_etag", lambda: etag)
        r = unit_client.get("/api/graph/stats", headers={"If-None-Match": etag})
        assert r.status_code == 304
        assert len(r.get_data()) == 0
        # The 304 still echoes the ETag + Cache-Control for the client.
        assert r.headers["ETag"] == etag
        assert r.headers["Cache-Control"] == "no-cache"

    def test_etag_invalidated_on_refresh(self, unit_client, monkeypatch):
        """POST /api/graph/refresh clears the cached ETag so the next response
        derives a fresh one from the rebuilt cache's built_at. Without this, a
        refresh would keep serving the old ETag and clients could get stale
        304s against pre-refresh data."""
        # Simulate the build time advancing across a refresh: the first call
        # returns the old build date, and after _reset_graph_connection() runs
        # (as /api/graph/refresh does), the next call returns the new date.
        state = {"built": "2026-07-22"}
        monkeypatch.setattr(A, "_graph_etag", None)

        def fake_etag():
            # Reads the live global; _reset_graph_connection clears it.
            global_seen = getattr(A, "_graph_etag", None)
            if global_seen is None:
                # Re-derive (simulating the fresh built_at read).
                A._graph_etag = f'W/"graph-{state["built"]}"'
            return A._graph_etag

        monkeypatch.setattr(A, "_graph_build_etag", fake_etag)

        # First response: old build.
        r1 = unit_client.get("/api/graph/stats")
        etag1 = r1.headers["ETag"]
        assert "2026-07-22" in etag1

        # refresh: bumps built_at + clears the cached ETag. We don't have a
        # live DuckDB to rebuild, so drive _reset_graph_connection directly
        # (it's what /api/graph/refresh calls after rebuild succeeds) and bump
        # the simulated build time.
        A._reset_graph_connection()
        state["built"] = "2026-08-01"

        r2 = unit_client.get("/api/graph/stats")
        etag2 = r2.headers["ETag"]
        assert etag2 != etag1, "ETag must change after refresh invalidation"
        assert "2026-08-01" in etag2

    def test_non_graph_route_not_cached(self, unit_client, monkeypatch):
        """The ETag hook is scoped to /api/graph/* — non-graph routes must not
        get an ETag or Cache-Control header."""
        monkeypatch.setattr(A, "_graph_build_etag", lambda: 'W/"graph-2026-07-22"')
        r = unit_client.get("/api/stats")
        assert r.status_code == 200
        assert "ETag" not in r.headers
        assert "Cache-Control" not in r.headers

    def test_graph_route_skips_etag_when_unavailable(self, unit_client, monkeypatch):
        """If _graph_build_etag returns None (cold cache / DuckDB unavailable),
        the hook still sets Cache-Control but omits the ETag — caching never
        breaks the request."""
        monkeypatch.setattr(A, "_graph_build_etag", lambda: None)
        r = unit_client.get("/api/graph/stats")
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "no-cache"
        assert "ETag" not in r.headers


# ----- /api/graph/shortest param validation (SQLite-only) ----------------- #

class TestShortestParamValidation:
    def test_missing_a_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/shortest?b=HDFC%20Bank")
        assert r.status_code == 400
        assert "'a' and 'b'" in r.get_json()["error"]

    def test_missing_b_returns_400(self, unit_client):
        r = unit_client.get("/api/graph/shortest?a=HDFC%20Bank")
        assert r.status_code == 400
        assert "'a' and 'b'" in r.get_json()["error"]

    def test_unknown_entity_returns_404(self, unit_client):
        r = unit_client.get("/api/graph/shortest?a=HDFC%20Bank&b=Nonexistent")
        assert r.status_code == 404
        assert "Nonexistent" in r.get_json()["error"]

    def test_bad_max_hops_returns_400(self, unit_client, monkeypatch):
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())
        r = unit_client.get(
            "/api/graph/shortest?a=HDFC%20Bank&b=ICICI%20Bank&max_hops=banana"
        )
        assert r.status_code == 400
        assert "max_hops" in r.get_json()["error"]

    def test_out_of_range_max_hops_returns_400(self, unit_client, monkeypatch):
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())
        r = unit_client.get(
            "/api/graph/shortest?a=HDFC%20Bank&b=ICICI%20Bank&max_hops=99"
        )
        assert r.status_code == 400

    def test_max_hops_boundary_eight_ok_nine_400(self, unit_client, monkeypatch):
        """sql_capability_unlocks B3: the cap is the graph diameter (8).
        9 is rejected with the diameter rationale in the message; 8 passes
        validation (any later failure is a connection concern, not the
        max_hops 400 — the monkeypatched connection makes it 500)."""
        monkeypatch.setattr(A, "get_graph_connection", lambda: object())
        r = unit_client.get(
            "/api/graph/shortest?a=HDFC%20Bank&b=ICICI%20Bank&max_hops=9"
        )
        assert r.status_code == 400
        assert "between 1 and 8" in r.get_json()["error"]
        r8 = unit_client.get(
            "/api/graph/shortest?a=HDFC%20Bank&b=ICICI%20Bank&max_hops=8"
        )
        assert r8.status_code != 400


# ----- _normalise_as_of + as_of validation (pure unit, runs in QA) -------- #

class TestAsOfNormaliser:
    def test_year_only(self):
        from helpers.graph.query import _normalise_as_of
        assert _normalise_as_of("2023") == "2023-01-01"

    def test_year_month(self):
        from helpers.graph.query import _normalise_as_of
        assert _normalise_as_of("2023-06") == "2023-06-01"

    def test_full_date(self):
        from helpers.graph.query import _normalise_as_of
        assert _normalise_as_of("2023-06-15") == "2023-06-15"

    def test_none_and_empty(self):
        from helpers.graph.query import _normalise_as_of
        assert _normalise_as_of(None) is None
        assert _normalise_as_of("") is None
        assert _normalise_as_of("   ") is None

    def test_bad_shape_raises(self):
        from helpers.graph.query import _normalise_as_of
        for bad in ["banana", "202", "20230", "2023-6", "2023-06-1", "abcd-06-15"]:
            with pytest.raises(ValueError):
                _normalise_as_of(bad)

    def test_predicate_empty_when_no_as_of(self):
        from helpers.graph.query import _as_of_predicate
        assert _as_of_predicate(None) == ""
        assert _as_of_predicate("") == ""

    def test_predicate_contains_iso(self):
        from helpers.graph.query import _as_of_predicate
        pred = _as_of_predicate("2023")
        assert "2023-01-01" in pred
        assert "valid_from IS NULL" in pred
        assert "valid_to IS NULL" in pred

    def test_route_rejects_bad_as_of_with_json_400(self, unit_client):
        # Pins both the validation AND the JSON-vs-HTML 400 split.
        r = unit_client.get("/api/graph/neighbors/HDFC%20Bank?as_of=banana")
        assert r.status_code == 400
        assert r.is_json
        assert "as_of must be" in r.get_json()["error"]


# ----- /api/graph/refresh ------------------------------------------------- #

class TestGraphRefresh:
    def test_refresh_resets_cached_connection(self, unit_client, monkeypatch):
        # Seed the cache with a sentinel, call refresh, then verify the next
        # get_graph_connection call no longer returns the sentinel. The sentinel
        # is a bare object() with no .close() — _reset_graph_connection guards
        # the close with getattr(_, "close", None), which is exactly why this
        # unit test doesn't need a fake-connection stub.
        sentinel = object()
        monkeypatch.setattr(A, "_graph_con", sentinel, raising=False)
        # Sanity: cache is populated.
        assert A.get_graph_connection() is sentinel

        r = unit_client.post("/api/graph/refresh")
        assert r.status_code == 200
        assert r.get_json()["status"] == "ok"

        # After refresh, the cache is cleared. The next get_graph_connection
        # would try to actually open DuckDB; we don't want that in a unit test,
        # so we just verify the cached sentinel was discarded.
        assert A._graph_con is None
        assert A._graph_con_error is None

    def test_refresh_closes_real_connection(self, unit_client, monkeypatch):
        """A1: refresh must call .close() on the cached DuckDB connection
        before nulling it, so the file lock is released for the rebuild
        (DuckDB single-writer contract, doc/graph_design.txt §8)."""
        closed = {"n": 0}

        class FakeCon:
            def close(self):
                closed["n"] += 1

        monkeypatch.setattr(A, "_graph_con", FakeCon(), raising=False)
        r = unit_client.post("/api/graph/refresh")
        assert r.status_code == 200
        assert closed["n"] == 1, "cached connection must be closed on refresh"

    def test_refresh_returns_500_on_rebuild_failure(self, unit_client, monkeypatch):
        """A2: rebuild failure must surface as HTTP 500 with the error in the
        body, not a false 200 + status:ok. The connection is still reset."""
        import helpers.graph.query as q

        def boom(*a, **kw):
            raise RuntimeError("rebuild blew up")

        monkeypatch.setattr(q, "rebuild", boom)
        r = unit_client.post("/api/graph/refresh")
        assert r.status_code == 500
        body = r.get_json()
        assert body["status"] == "error"
        assert "rebuild blew up" in body["message"]
        # Connection was reset before the rebuild attempt.
        assert A._graph_con is None


# ----- lazy-init guard ---------------------------------------------------- #

class TestGraphConnectionGuard:
    def test_failure_cached_within_ttl(self, unit_client, monkeypatch):
        """If connect() raises, the exception is cached and re-raised on
        subsequent calls WITHOUT re-invoking connect() — but only while the
        cache is within _GRAPH_ERROR_TTL. Pins the fast-fail-during-burst
        half of A3; test_cached_error_expires_after_ttl pins the other."""
        import helpers.graph.query as q

        calls = {"n": 0}

        def boom(*a, **kw):
            calls["n"] += 1
            raise RuntimeError("synthetic connect failure")

        monkeypatch.setattr(q, "connect", boom)
        # Reset the cache so our patched connect() gets called.
        monkeypatch.setattr(A, "_graph_con", None, raising=False)
        monkeypatch.setattr(A, "_graph_con_error", None, raising=False)
        monkeypatch.setattr(A, "_graph_error_at", None, raising=False)

        with pytest.raises(RuntimeError, match="synthetic"):
            A.get_graph_connection()
        with pytest.raises(RuntimeError, match="synthetic"):
            A.get_graph_connection()
        # Second call must NOT have re-invoked connect — cached error is fresh.
        assert calls["n"] == 1

    def test_cached_error_expires_after_ttl(self, unit_client, monkeypatch):
        """A3: once the cached error is older than _GRAPH_ERROR_TTL, the next
        call retries connect() instead of re-raising the stale error. This is
        what lets a transient blip (mid-copy file, momentary lock) auto-recover
        without an operator hitting /api/graph/refresh."""
        import helpers.graph.query as q

        calls = {"n": 0}

        def boom_then_ok(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return object()  # second call succeeds

        monkeypatch.setattr(q, "connect", boom_then_ok)
        monkeypatch.setattr(A, "_graph_con", None, raising=False)
        monkeypatch.setattr(A, "_graph_con_error", None, raising=False)
        monkeypatch.setattr(A, "_graph_error_at", None, raising=False)

        with pytest.raises(RuntimeError, match="transient"):
            A.get_graph_connection()  # caches the error
        assert calls["n"] == 1

        # Push the cached-error timestamp past TTL so get_graph_connection
        # treats it as stale and retries.
        stale = time.monotonic() - A._GRAPH_ERROR_TTL - 1
        monkeypatch.setattr(A, "_graph_error_at", stale, raising=False)

        con = A.get_graph_connection()  # retries, succeeds
        assert con is not None
        assert calls["n"] == 2


# ----- thread-safety (A4) ------------------------------------------------- #

class TestGraphConnectionThreadSafety:
    """A4: _graph_lock serializes init/reset of _graph_con so two Flask worker
    threads can't both call connect() or race a refresh's null against a
    query's read."""

    def test_concurrent_init_calls_connect_once(self, unit_client, monkeypatch):
        """Five threads racing lazy-init: connect() invoked exactly once and
        every thread sees the same connection object."""
        import helpers.graph.query as q

        calls = {"n": 0}

        def slow_connect(*a, **kw):
            calls["n"] += 1
            time.sleep(0.05)  # widen the race window
            return object()

        monkeypatch.setattr(q, "connect", slow_connect)
        monkeypatch.setattr(A, "_graph_con", None, raising=False)
        monkeypatch.setattr(A, "_graph_con_error", None, raising=False)
        monkeypatch.setattr(A, "_graph_error_at", None, raising=False)

        results: list = []
        errors: list = []

        def worker():
            try:
                results.append(A.get_graph_connection())
            except Exception as e:  # pragma: no cover - surfaced via assert
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"workers raised: {errors}"
        assert calls["n"] == 1, "connect() must be called exactly once under contention"
        assert all(r is results[0] for r in results), \
            "all threads must share the single connection"




# ----- /api/graph/cloud (whole-graph force cloud) --------------------------- #

class TestGraphCloud:
    def test_cloud_response_shape(self, unit_client):
        r = unit_client.get("/api/graph/cloud")
        assert r.status_code == 200
        data = r.get_json()
        assert set(data) == {"nodes", "edges", "relationship_types",
                             "total_nodes", "total_edges"}
        # Seed: 6 entities, 7 edges (all entity pairs covered).
        assert data["total_edges"] == len(_UNIT_EDGES)
        # Every edge endpoint is present as a node; no duplicates.
        node_ids = [n["id"] for n in data["nodes"]]
        assert len(node_ids) == len(set(node_ids))
        assert set(node_ids) == {"HDFC Bank", "ICICI Bank", "Infosys",
                                 "No Ticker Co", "Banking", "Technology"}
        # Nodes carry entity_type for colouring.
        by_id = {n["id"]: n for n in data["nodes"]}
        assert by_id["HDFC Bank"]["entity_type"] == "company"
        assert by_id["Banking"]["entity_type"] == "sector"

    def test_cloud_edges_match_seed(self, unit_client):
        data = unit_client.get("/api/graph/cloud").get_json()
        edge_tuples = {(e["source"], e["target"], e["edge_type"])
                       for e in data["edges"]}
        assert edge_tuples == {(s, t, et) for s, t, et, _ in _UNIT_EDGES}

    def test_cloud_relationship_types_summary(self, unit_client):
        data = unit_client.get("/api/graph/cloud").get_json()
        types = {t["edge_type"]: t for t in data["relationship_types"]}
        # Ordered by count desc: part_of (4), then 1 each.
        assert data["relationship_types"][0]["edge_type"] == "part_of"
        assert types["part_of"]["count"] == 4
        assert types["part_of"]["symmetric"] is False
        assert types["part_of"]["semantics"]  # non-empty human description
        # Symmetric relationships flagged for arrow-less rendering.
        assert types["competes_with"]["symmetric"] is True
        assert types["competes_with"]["count"] == 1

    def test_cloud_edge_type_filter_isolates(self, unit_client):
        data = unit_client.get("/api/graph/cloud?edge_type=competes_with").get_json()
        assert data["total_edges"] == 1
        assert data["edges"][0]["edge_type"] == "competes_with"
        # Only incident nodes (HDFC Bank, ICICI Bank) remain.
        node_ids = {n["id"] for n in data["nodes"]}
        assert node_ids == {"HDFC Bank", "ICICI Bank"}

    def test_cloud_edge_type_filter_unknown_returns_empty(self, unit_client):
        data = unit_client.get("/api/graph/cloud?edge_type=zzz").get_json()
        assert data["total_edges"] == 0
        assert data["nodes"] == []
        assert data["edges"] == []
        # Summary card still reflects the whole corpus.
        assert {t["edge_type"] for t in data["relationship_types"]} == \
            {e[2] for e in _UNIT_EDGES}
    def test_cloud_unknown_entity_type_defaults_to_unknown(self, unit_client):
        """Nodes with no entities row still render (colour = 'unknown')."""
        data = unit_client.get("/api/graph/cloud").get_json()
        assert all(n["entity_type"] for n in data["nodes"])


# ----- /api/graph/cloud: unknown semantics fallback ------------------------ #

class TestGraphCloudEdgeSemantics:
    def test_custom_edge_type_gets_default_semantics(self, tmp_path):
        """An edge_type not in _EDGE_SEMANTICS still gets a sensible default
        (custom/derived) rather than crashing."""
        import sqlite3
        from tests.conftest import seeded_graph_sqlite_db

        with seeded_graph_sqlite_db(tmp_path) as c:
            conn = sqlite3.connect(str(tmp_path / "unit_graph.db"))
            conn.execute(
                "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
                "VALUES (?,?,?,?)",
                ("HDFC Bank", "Infosys", "custom_link", "test"),
            )
            conn.commit()
            conn.close()
            data = c.get("/api/graph/cloud").get_json()
            types = {t["edge_type"]: t for t in data["relationship_types"]}
            assert types["custom_link"]["symmetric"] is False
            assert "custom" in types["custom_link"]["semantics"]
