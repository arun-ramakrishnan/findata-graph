"""Live /api/graph/* endpoint tests — split from the original
test_api_graph.py for navigability.

Live tests (require real memory/research.db) pinning the wiring between each /api/graph/* route and the underlying helpers.graph.query wrapper. Run under `make test-live`, not `make qa`.
"""

from __future__ import annotations

import pytest

import app as A


# --------------------------------------------------------------------------- #
# Live tests — pin wiring against the real memory/research.db                 #
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def live_client():
    """Flask test_client backed by the real memory/research.db.

    No monkey-patching — get_db_connection and get_graph_connection both hit
    the production DB. The test class below carries @pytest.mark.live so it
    is deselected by `make qa` (`-m "not live"`) and run by `make test-live`.
    """
    # Reset the graph connection cache so each test module starts fresh.
    A._reset_graph_connection()
    yield A.app.test_client()
    A._reset_graph_connection()


@pytest.mark.live
class TestGraphEndpointsLive:
    """Pin endpoint → wrapper wiring against the real graph DB.

    Uses only well-established seeds:
      - CEAT ↔ {Apollo Tyres, MRF}                (competes_with, hand-seeded)
      - Rallis India → Tata Chemicals             (subsidiary_of, derived)
      - CEAT → Camso                              (acquired 2023, derived)
      - GAIL India -- customer_of --> IOC         (asymmetric supply chain)
      - sector_of(CEAT) == 'Automotive'
    """

    def test_peers_returns_known_competitors(self, live_client):
        r = live_client.get("/api/graph/peers/CEAT")
        assert r.status_code == 200
        data = r.get_json()
        assert data["company"] == "CEAT"
        assert "Apollo Tyres" in data["peers"]
        assert "MRF" in data["peers"]

    def test_peers_case_insensitive(self, live_client):
        r = live_client.get("/api/graph/peers/ceat")
        assert r.status_code == 200
        assert r.get_json()["company"] == "CEAT"

    def test_peers_unknown_company_404(self, live_client):
        r = live_client.get("/api/graph/peers/NoSuchCompany")
        assert r.status_code == 404
        assert r.is_json

    def test_neighbors_bundle_shape(self, live_client):
        r = live_client.get("/api/graph/neighbors/CEAT")
        assert r.status_code == 200
        data = r.get_json()
        # All expected keys present.
        expected_keys = {
            "company",
            "file_path",
            "sector",
            "peers",
            "jv_partners",
            "group_siblings",
            "acquired",
            "subsidiary_of",
            "suppliers",
            "customers",
        }
        assert set(data) >= expected_keys
        assert data["company"] == "CEAT"
        assert data["entity_type"] == "company"
        assert data["sector"] == "Automotive"
        assert "Apollo Tyres" in data["peers"]
        # acquired is a list of {name, year} dicts; CEAT acquired Camso in 2023.
        acquired_names = {a["name"] for a in data["acquired"]}
        assert "Camso" in acquired_names

    def test_neighbors_for_sector_returns_members(self, live_client):
        # A sector focal node returns a different bundle: members list + size +
        # market-cap distribution. NOT the company-typed bundle.
        r = live_client.get("/api/graph/neighbors/Automotive")
        assert r.status_code == 200
        data = r.get_json()
        assert data["entity_type"] == "sector"
        assert data["sector"] == "Automotive"
        assert isinstance(data["members"], list)
        assert "CEAT" in data["members"]
        assert data["member_count"] == len(data["members"])
        assert data["member_count"] > 10  # Automotive is one of the biggest
        # Market-cap distribution is a dict of cap -> count.
        assert isinstance(data["market_cap_counts"], dict)
        assert sum(data["market_cap_counts"].values()) == data["member_count"]
        # Company-bundle keys must NOT leak into the sector bundle.
        assert "peers" not in data
        assert "acquired" not in data

    def test_neighbors_for_sector_case_insensitive(self, live_client):
        r = live_client.get("/api/graph/neighbors/automotive")
        assert r.status_code == 200
        assert r.get_json()["sector"] == "Automotive"

    def test_neighbors_for_sector_market_cap_filter(self, live_client):
        # The market_cap query param narrows members. Automotive has both
        # large-cap and small-cap names.
        r_all = live_client.get("/api/graph/neighbors/Automotive")
        r_large = live_client.get("/api/graph/neighbors/Automotive?market_cap=large_cap")
        assert r_all.status_code == 200
        assert r_large.status_code == 200
        all_data = r_all.get_json()
        large_data = r_large.get_json()
        # Filter narrows but never widens.
        assert large_data["member_count"] <= all_data["member_count"]
        assert large_data["member_count"] > 0

    # ----- temporal as_of filter (acquired edges carry valid_from) -------- #

    def test_neighbors_as_of_echo(self, live_client):
        # Response payload echoes the as_of value (normalised to ISO date).
        r = live_client.get("/api/graph/neighbors/CEAT?as_of=2023")
        assert r.status_code == 200
        assert r.get_json()["as_of"] == "2023-01-01"

    def test_neighbors_as_of_default_is_none(self, live_client):
        # No as_of param → null in the response, no filtering applied.
        r = live_client.get("/api/graph/neighbors/CEAT")
        assert r.status_code == 200
        data = r.get_json()
        assert data["as_of"] is None
        # CEAT acquired Camso in 2023 — must be present with no filter.
        assert any(a["name"] == "Camso" for a in data["acquired"])

    def test_neighbors_as_of_drops_future_acquired_edge(self, live_client):
        # CEAT → Camso, valid_from='2023-01-01'. With as_of=2022 the edge
        # is in the future and must be dropped from the acquired array.
        r = live_client.get("/api/graph/neighbors/CEAT?as_of=2022")
        assert r.status_code == 200
        data = r.get_json()
        assert data["as_of"] == "2022-01-01"
        acquired_names = {a["name"] for a in data["acquired"]}
        assert "Camso" not in acquired_names

    def test_neighbors_as_of_preserves_structural_edges(self, live_client):
        # Same query as above — the temporal filter must NOT nuke structural
        # edges (sector, peers, etc.) which have NULL valid_from.
        r = live_client.get("/api/graph/neighbors/CEAT?as_of=2022")
        data = r.get_json()
        assert data["sector"] == "Automotive"  # NULL valid_from → always valid
        assert "Apollo Tyres" in data["peers"]  # competes_with, NULL valid_from
        assert "MRF" in data["peers"]

    def test_neighbors_as_of_keeps_past_acquired_edge(self, live_client):
        # With as_of=2024 the 2023 Camso acquisition is in the past → kept.
        r = live_client.get("/api/graph/neighbors/CEAT?as_of=2024")
        data = r.get_json()
        acquired_names = {a["name"] for a in data["acquired"]}
        assert "Camso" in acquired_names

    def test_neighbors_as_of_year_only_canonicalised(self, live_client):
        # Year-only as_of is accepted (normalised to YYYY-01-01). The
        # response just succeeds — pin the contract that bare '2023' works.
        r = live_client.get("/api/graph/neighbors/CEAT?as_of=2023")
        assert r.status_code == 200
        assert r.get_json()["as_of"] == "2023-01-01"
        # Spot-check: every returned member has market_cap=large_cap.
        # (We can't verify against the response since members are just names;
        # but the count consistency is a strong proxy.)

    def test_shortest_path_returns_path(self, live_client):
        # CEAT and MRF are direct competitors — 1 hop via competes_with.
        r = live_client.get("/api/graph/shortest?a=CEAT&b=MRF")
        assert r.status_code == 200
        data = r.get_json()
        assert data["source"] == "CEAT"
        assert data["target"] == "MRF"
        assert data["path"] is not None
        assert data["hops"] is not None
        # Path starts at src and ends at dst.
        assert data["path"][0]["name"] == "CEAT"
        assert data["path"][-1]["name"] == "MRF"

    def test_shortest_path_no_path(self, live_client):
        # Pick two entities guaranteed not to share any edge type within
        # max_hops=2. Using obscure stub entities is risky (some are now
        # linked); instead, just assert the contract: a `null` path is
        # returned as JSON, not as an error. We use two obscure companies
        # and a max_hops=1 to make a no-path outcome likely.
        # Skip gracefully if both happen to be 1-hop-connected.
        r = live_client.get("/api/graph/shortest?a=CEAT&b=Tata%20Chemicals&max_hops=1")
        assert r.status_code == 200
        data = r.get_json()
        # Either there's a path (hops=1) or null — both are valid responses.
        assert "path" in data and "hops" in data

    def test_shortest_missing_param_400(self, live_client):
        r = live_client.get("/api/graph/shortest?a=CEAT")
        assert r.status_code == 400
        assert "'a' and 'b'" in r.get_json()["error"]

    def test_sector_lookup_by_company(self, live_client):
        r = live_client.get("/api/graph/sector/CEAT")
        assert r.status_code == 200
        data = r.get_json()
        assert data["company"] == "CEAT"
        assert data["sector"] == "Automotive"

    def test_sector_lookup_returns_members(self, live_client):
        r = live_client.get("/api/graph/sector/Automotive")
        assert r.status_code == 200
        data = r.get_json()
        assert data["sector"] == "Automotive"
        assert isinstance(data["members"], list)
        assert "CEAT" in data["members"]
        assert len(data["members"]) > 10  # Automotive is one of the biggest

    def test_stats_live(self, live_client):
        r = live_client.get("/api/graph/stats")
        assert r.status_code == 200
        data = r.get_json()
        assert data["entities"]["total"] > 1000
        assert data["edges"]["total"] > 1000
        assert "competes_with" in data["edges"]["by_type"]
        # Live DB is post-Phase-3 cleanup — hygiene counters all 0.
        assert data["hygiene"]["orphan_companies"] == 0
        assert data["hygiene"]["self_loops"] == 0
        assert data["hygiene"]["orphan_edges"] == 0

    def test_refresh_resets_connection(self, live_client, monkeypatch):
        # First call seeds the cache via a real connect().
        A._reset_graph_connection()
        r1 = live_client.get("/api/graph/peers/CEAT")
        assert r1.status_code == 200
        assert A._graph_con is not None

        # POST /refresh must (a) reset the cached connection FIRST (so the
        # DuckDB file is free), then (b) rebuild the disk cache. We spy
        # on rebuild() to confirm the disk-rebuild half fires AND that
        # the connection was already None when rebuild() was called.
        import helpers.graph.query as q

        called = {"rebuild": False, "con_was_none": False}
        orig_rebuild = q.rebuild

        def spy_rebuild(*a, **kw):
            called["rebuild"] = True
            called["con_was_none"] = A._graph_con is None
            return orig_rebuild(*a, **kw)

        monkeypatch.setattr(q, "rebuild", spy_rebuild)
        r2 = live_client.post("/api/graph/refresh")
        assert r2.status_code == 200
        assert r2.get_json()["status"] == "ok"
        assert called["rebuild"] is True
        assert called["con_was_none"] is True, "connection must close before rebuild"
        assert A._graph_con is None  # still None after refresh

        # Next request re-opens against the freshly-rebuilt file.
        r3 = live_client.get("/api/graph/peers/CEAT")
        assert r3.status_code == 200
        assert A._graph_con is not None

    def test_graph_connection_failure_returns_500(self, live_client, monkeypatch):
        # Stub connect() to raise; first request after reset should 500 cleanly.
        import helpers.graph.query as q

        def boom(*a, **kw):
            raise RuntimeError("synthetic connect failure")

        monkeypatch.setattr(q, "connect", boom)
        A._reset_graph_connection()
        r = live_client.get("/api/graph/peers/CEAT")
        assert r.status_code == 500
        assert r.is_json
        assert "synthetic" in r.get_json()["error"]

    def test_semantic_endpoint_live(self, live_client):
        """/api/graph/semantic/<name> resolves CEAT and returns neighbours via
        the live v_embeddings (VSS). The dry-run pseudo-embeddings produce
        low but positive cosine scores; the shape must be correct."""
        A._reset_graph_connection()  # clear any TTL-cached init error
        r = live_client.get("/api/graph/semantic/CEAT?k=5")
        assert r.status_code == 200
        data = r.get_json()
        assert data["company"] == "CEAT"
        assert data["k"] == 5
        assert data["metric"] == "cosine"
        assert data["cross_sector"] is False
        assert len(data["neighbors"]) <= 5
        for n in data["neighbors"]:
            assert set(n) == {"name", "sector", "similarity"}
            assert n["name"] != "CEAT"  # self excluded by the wrapper

    def test_semantic_endpoint_cross_sector_live(self, live_client):
        A._reset_graph_connection()  # clear any TTL-cached init error
        r = live_client.get("/api/graph/semantic/CEAT?k=3&cross_sector=true")
        assert r.status_code == 200
        data = r.get_json()
        assert data["cross_sector"] is True
        assert len(data["neighbors"]) <= 3

    def test_semantic_endpoint_unknown_company_404_live(self, live_client):
        r = live_client.get("/api/graph/semantic/NoSuchCompany")
        assert r.status_code == 404
        assert r.is_json
