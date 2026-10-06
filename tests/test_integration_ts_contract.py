#!/usr/bin/env python3
"""P6 — TypeScript / frontend type-contract validation.

These tests treat `frontend/types/api.ts` as the contract between the app.py
backend and the findata frontend. The api.ts interfaces are hand-written to
mirror the `jsonify({...})` blocks in app.py; shape-drift is caught ONLY by
manual `make frontend-check` (which validates findata.ts against api.ts, but
NOT api.ts against app.py).

This suite closes the REVERSE direction: for each endpoint, hit it via a
Flask test_client and assert every key the api.ts interface declares is
actually present in the response body. If app.py changes a response shape
without updating api.ts, these tests fail.

This is deliberately Python-only (no Node / no tsc) to keep the QA gate
Python-only per frontend/README.md. The tsc direction (findata.ts vs api.ts)
remains a Makefile gate (make frontend-check).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

PROJECT_ROOT = Path(__file__).resolve().parents[1]

API_TYPES = PROJECT_ROOT / "frontend" / "types" / "api.ts"


# Parsing + assertions live in tests/api_contract.py so the guard generator
# (helpers/misc/gen_api_guards.py) and this suite share one model of the
# contract — they cannot disagree about what api.ts declares.
from tests.api_contract import (  # noqa: E402
    INTERFACES as _interfaces,
    app_routes as _app_routes,
    assert_contract as _assert_contract,
    assert_keys as _assert_keys,
    assert_type as _assert_type,
    assert_types as _assert_types,
    consumed_routes as _consumed_routes,
    required_keys as _required_keys,
)
# --------------------------------------------------------------------------- #
# Test: api.ts itself parses into interfaces
# --------------------------------------------------------------------------- #


class TestApiTsParses:
    def test_file_exists(self):
        assert API_TYPES.exists()
        assert API_TYPES.stat().st_size > 0

    def test_parse_produces_expected_interfaces(self):
        """Sanity: our parser must find the known interfaces, else the
        contract tests silently test nothing."""
        expected = {
            "ErrorResponse",
            "SectorsResponse",
            "StatsResponse",
            "EntitiesResponse",
            "EntityDetail",
            "SearchResponse",
            "GraphRefreshResponse",
            "CompanyNeighbors",
            "SectorNeighbors",
            "ShortestPathResponse",
            "EventsResponse",
            "DocsResponse",
            "DocItem",
            "DocContentResponse",
            "DocSearchResponse",
            "DocSearchHit",
            "GraphCloudResponse",
            "GraphCloudNode",
            "GraphCloudEdge",
            "RelationshipTypeSummary",
            "GraphStatsResponse",
        }
        found = set(_interfaces.keys())
        assert expected.issubset(found), f"missing: {expected - found}"

    def test_entity_detail_extends_entity_list_item(self):
        """EntityDetail must inline EntityListItem keys."""
        detail = _interfaces.get("EntityDetail", {})
        assert "name" in detail
        assert "entity_type" in detail
        assert "frontmatter" in detail


# --------------------------------------------------------------------------- #
# Shared fixture: seeded Flask test_client with all needed tables
# --------------------------------------------------------------------------- #

from tests.schema import GRAPH_ANALYTICS, NOTE_SEARCH_FTS  # noqa: E402

_SCHEMA = (
    """
CREATE TABLE entities (
    name                  TEXT PRIMARY KEY NOT NULL,
    entity_type           TEXT NOT NULL,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_path             TEXT,
    last_updated          DATETIME,
    normalized_name       TEXT,
    sector_classification TEXT,
    ticker                TEXT
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag)
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
    source_ref  TEXT NOT NULL,
    symmetric   INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
"""
    + GRAPH_ANALYTICS
    + """
CREATE TABLE events (
    id            INTEGER PRIMARY KEY,
    entity        TEXT NOT NULL,
    event_type    TEXT,
    event_date    TEXT,
    period        TEXT,
    date_precision TEXT,
    magnitude     TEXT,
    counterparty  TEXT,
    source_quote  TEXT,
    as_of_edition TEXT
);
"""
    + NOTE_SEARCH_FTS
)

# Note file content — must exist on disk for /api/entity to return frontmatter
GOOD_NOTE = """---
title: HDFC Bank
type: company
tags:
- entity_type/company
- sector/banking
normalized_name: HDFC_Bank
---
# HDFC Bank

A large private-sector bank.
"""


@pytest.fixture
def contract_client(tmp_path):
    """Flask test_client backed by a fully-seeded synthetic DB."""
    import app as A

    db_path = tmp_path / "contract.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)

    # Entities
    conn.executemany(
        "INSERT INTO entities(name, entity_type, sector_classification, "
        "file_path, ticker) VALUES (?,?,?,?,?)",
        [
            (
                "HDFC Bank",
                "company",
                "Banking",
                "findata/Companies/Banking/Hdfc_Bank.md",
                "HDFCBANK",
            ),
            (
                "ICICI Bank",
                "company",
                "Banking",
                "findata/Companies/Banking/ICICI_Bank.md",
                "ICICIBANK",
            ),
            ("Infosys", "company", "Technology", "findata/Companies/Technology/Infosys.md", "INFY"),
            ("Banking", "sector", None, "findata/Sectors/Banking.md", None),
            ("Technology", "sector", None, "findata/Sectors/Technology.md", None),
        ],
    )

    # Tags
    conn.executemany(
        "INSERT INTO entity_tags(entity_name, tag) VALUES (?,?)",
        [
            ("HDFC Bank", "market_cap/large_cap"),
            ("HDFC Bank", "sector/banking"),
            ("ICICI Bank", "market_cap/large_cap"),
            ("Infosys", "market_cap/small_cap"),
        ],
    )

    # Edges
    conn.executemany(
        "INSERT INTO graph_edges(source, target, edge_type, source_ref) VALUES (?,?,?,?)",
        [
            ("HDFC Bank", "Banking", "part_of", "seed"),
            ("Banking", "HDFC Bank", "has_company", "seed"),
            ("ICICI Bank", "Banking", "part_of", "seed"),
            ("Infosys", "Technology", "part_of", "seed"),
            ("HDFC Bank", "ICICI Bank", "competes_with", "seed"),
        ],
    )

    # Events
    conn.execute(
        "INSERT INTO events(entity, event_type, event_date, counterparty) "
        "VALUES ('HDFC Bank', 'acquisition', '2023-06-01', 'Target Co')"
    )
    conn.execute(
        "INSERT INTO events(entity, event_type, event_date) "
        "VALUES ('Infosys', 'guidance', '2024-01-15')"
    )

    # FTS index
    conn.execute(
        "INSERT INTO note_search(doc_type, file_path, title, sector, content) "
        "VALUES ('company', 'findata/Companies/Banking/Hdfc_Bank.md', "
        "'HDFC Bank', 'Banking', 'HDFC Bank is a large private-sector bank')"
    )
    conn.execute(
        "INSERT INTO note_search(doc_type, file_path, title, sector, content) "
        "VALUES ('chatter', 'findata/The_Chatter/Test_Edition.md', "
        "'Test Edition', '', 'banking sector growth')"
    )
    conn.commit()
    conn.close()

    # The real HDFC Bank note exists at findata/Companies/Banking/Hdfc_Bank.md
    # so app.py can read it directly. No fixture file needed.

    # Wire app.get_db_connection to our DB
    from tests.helpers import flask_test_client  # noqa: E402

    # Monkeypatch get_graph_connection to raise so DuckDB endpoints aren't hit
    saved_ggc = A.get_graph_connection
    A.get_graph_connection = lambda: (_ for _ in ()).throw(  # ty: ignore[invalid-assignment]
        RuntimeError("no DuckDB in contract test")
    )

    try:
        with flask_test_client(db_path, track_conns=True) as client:
            yield client
    finally:
        A.get_graph_connection = saved_ggc


# --------------------------------------------------------------------------- #
# SQLite-only endpoints (full contract verification)
# --------------------------------------------------------------------------- #


class TestSectorsContract:
    def test_response_keys_match_sectorsresponse(self, contract_client):
        r = contract_client.get("/api/sectors")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "SectorsResponse")

    def test_entity_keys_match(self, contract_client):
        r = contract_client.get("/api/sectors")
        data = r.get_json()
        assert data["classifications"] == ["Banking", "Technology"]
        se = data["sector_entities"]
        # Each sector entity must match SectorEntity shape
        for entity in se:
            _assert_contract(entity, "SectorEntity")


class TestStaticCacheHeaders:
    def test_bundle_responses_force_revalidation(self, contract_client):
        """The stale-bundle window (proposal S4): bundles are rebuilt on
        deploy under constant URLs, so they must ship Cache-Control: no-cache
        (revalidate; not heuristic freshness) — mirroring /api/graph/*."""
        r = contract_client.get("/static/findata.bundle.js")
        assert r.status_code == 200
        assert r.headers.get("Cache-Control") == "no-cache"

    def test_handler_is_scoped_to_static_bundles(self, contract_client):
        """Our handler touches only /static/*.bundle.js — API responses keep
        their own caching regime (graph routes' no-cache comes from
        _graph_cache_headers, stats from nothing). Note: Flask already sends
        Cache-Control: no-cache on ALL static files because
        SEND_FILE_MAX_AGE_DEFAULT is None (measured 2026-10-06, correcting
        proposal D3); the handler makes the bundle policy explicit and
        test-gated instead of incidental to that default."""
        r = contract_client.get("/api/stats")
        assert r.headers.get("Cache-Control") != "no-cache"


class TestStatsContract:
    def test_response_keys_match_statsresponse(self, contract_client):
        r = contract_client.get("/api/stats")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "StatsResponse")

    def test_values_correct(self, contract_client):
        r = contract_client.get("/api/stats")
        data = r.get_json()
        assert data["total_entities"] == 5
        assert data["entity_counts"]["company"] == 3
        assert data["entity_counts"]["sector"] == 2
        assert data["top_sectors"]["Banking"] == 2
        assert data["market_cap_counts"]["large_cap"] == 2


class TestEntitiesContract:
    def test_response_keys_match_entitiesresponse(self, contract_client):
        r = contract_client.get("/api/entities")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "EntitiesResponse")

    def test_item_keys_match_entitylistitem(self, contract_client):
        r = contract_client.get("/api/entities")
        data = r.get_json()
        assert data["entities"]
        for item in data["entities"]:
            _assert_contract(item, "EntityListItem")

    def test_total_and_pagination(self, contract_client):
        r = contract_client.get("/api/entities")
        data = r.get_json()
        assert data["total_count"] == 5
        assert data["limit"] == 50
        assert data["offset"] == 0


class TestEntityDetailContract:
    def test_keys_include_entitylistitem_plus_detail(self, contract_client):
        """EntityDetail = EntityListItem + {frontmatter, content, raw_content}."""
        r = contract_client.get("/api/entity/HDFC%20Bank")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "EntityDetail")

    def test_missing_entity_returns_error_shape(self, contract_client):
        r = contract_client.get("/api/entity/Does%20Not%20Exist")
        assert r.status_code == 404
        _assert_contract(r.get_json(), "ErrorResponse")


class TestSearchContract:
    def test_response_keys_match_searchresponse(self, contract_client):
        r = contract_client.get("/api/search?q=bank")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "SearchResponse")

    def test_result_keys_match_searchresult(self, contract_client):
        r = contract_client.get("/api/search?q=bank")
        data = r.get_json()
        assert data["results"]
        for hit in data["results"]:
            _assert_contract(hit, "SearchResult")

    def test_empty_query_returns_error(self, contract_client):
        r = contract_client.get("/api/search")
        assert r.status_code == 400
        _assert_contract(r.get_json(), "ErrorResponse")


class TestEventsContract:
    def test_response_keys_match_eventsresponse(self, contract_client):
        r = contract_client.get("/api/events/HDFC%20Bank")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "EventsResponse")
        assert data["entity"] == "HDFC Bank"
        assert data["event_count"] == 1

    def test_event_item_keys_match_eventitem(self, contract_client):
        r = contract_client.get("/api/events/HDFC%20Bank")
        data = r.get_json()
        assert data["events"]
        for ev in data["events"]:
            _assert_contract(ev, "EventItem")

    def test_events_response_has_all_keys(self, contract_client):
        """Cover every key in EventItem, not just required ones."""
        r = contract_client.get("/api/events/HDFC%20Bank")
        data = r.get_json()
        for ev in data["events"]:
            _assert_contract(ev, "EventItem")


# --------------------------------------------------------------------------- //
# Docs endpoints — filesystem-backed, no DB needed
# --------------------------------------------------------------------------- //


class TestDocsContract:
    """GET /api/docs, /api/docs/content, /api/docs/search are filesystem-
    backed (doc/ corpus) — the DB-less contract_client is sufficient."""

    def test_catalog_keys_match_docsresponse_and_docitem(self, contract_client):
        r = contract_client.get("/api/docs")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "DocsResponse")
        assert data["docs"]
        for doc in data["docs"]:
            _assert_contract(doc, "DocItem")

    def test_content_keys_match_doccontentresponse(self, contract_client):
        # A real doc that always exists in the repo.
        r = contract_client.get("/api/docs/content?path=design/architecture.md")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "DocContentResponse")

    def test_search_keys_match_docsearchresponse_and_hit(self, contract_client):
        r = contract_client.get("/api/docs/search?q=graph")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "DocSearchResponse")
        for hit in data["results"]:
            _assert_contract(hit, "DocSearchHit")

    def test_search_empty_query_returns_error(self, contract_client):
        r = contract_client.get("/api/docs/search")
        assert r.status_code == 400
        _assert_contract(r.get_json(), "ErrorResponse")

    def test_content_unknown_path_returns_error(self, contract_client):
        r = contract_client.get("/api/docs/content?path=nope.md")
        assert r.status_code == 404
        _assert_contract(r.get_json(), "ErrorResponse")


# --------------------------------------------------------------------------- #
# Script search (unified_search S1) — script_search sidecar                     #
# --------------------------------------------------------------------------- #


class TestScriptSearchContract:
    @pytest.fixture
    def script_env(self, tmp_path, monkeypatch):
        """Tmp tree + sidecar so the contract holds on any machine (the
        live memory/script_search.db exists only on built boxes)."""
        from helpers.maintenance import rebuild_script_search as rss

        tree = tmp_path
        (tree / "helpers" / "misc").mkdir(parents=True)
        (tree / "tests").mkdir()
        (tree / "helpers" / "misc" / "contract_probe.py").write_text(
            '#!/usr/bin/env python3\n"}"}Contract probe helper."}"}\n'.replace("}", chr(34))
        )
        (tree / "app.py").write_text('"}"}Flask app."}"}\n'.replace("}", chr(34)))
        (tree / "Makefile").write_text(".RECIPEPREFIX := >\nqa: ## gate\n> true\n")
        monkeypatch.setattr(rss, "SCRIPT_DB", tree / "script_search.db")
        monkeypatch.setattr(rss, "HELPERS_ROOT", tree / "helpers")
        monkeypatch.setattr(rss, "TESTS_ROOT", tree / "tests")
        monkeypatch.setattr(rss, "APP_PY", tree / "app.py")
        monkeypatch.setattr(rss, "MAKEFILE", tree / "Makefile")
        monkeypatch.setattr(rss, "BACKUP_DIR", tree / "db-backup")
        rss.rebuild(write=True)
        return tree

    def test_keys_match_scriptsearchresponse_and_hit(self, contract_client, script_env):
        r = contract_client.get("/api/scripts/search?q=contract probe")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "ScriptSearchResponse")
        for hit in data["results"]:
            _assert_contract(hit, "ScriptSearchHit")

    def test_missing_q_returns_error(self, contract_client, script_env):
        r = contract_client.get("/api/scripts/search")
        assert r.status_code == 400
        _assert_contract(r.get_json(), "ErrorResponse")


# --------------------------------------------------------------------------- #
# Graph cloud + graph stats — SQLite-backed, full contract verification
# --------------------------------------------------------------------------- #


class TestGraphCloudContract:
    def test_cloud_response_keys_match_graphcloudresponse(self, contract_client):
        r = contract_client.get("/api/graph/cloud")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "GraphCloudResponse")

    def test_cloud_node_keys_match_graphcloudnode(self, contract_client):
        data = contract_client.get("/api/graph/cloud").get_json()
        assert data["nodes"], "cloud should return at least the 5 seed entities"
        for node in data["nodes"]:
            _assert_contract(node, "GraphCloudNode")

    def test_cloud_edge_keys_match_graphcloudedge(self, contract_client):
        data = contract_client.get("/api/graph/cloud").get_json()
        assert data["edges"], "cloud should return the seed edges"
        for edge in data["edges"]:
            _assert_contract(edge, "GraphCloudEdge")

    def test_cloud_relationship_types_match(self, contract_client):
        data = contract_client.get("/api/graph/cloud").get_json()
        types = data["relationship_types"]
        assert types, "cloud should summarise the seed edge types"
        for t in types:
            _assert_contract(t, "RelationshipTypeSummary")

    def test_cloud_filtered_response_keeps_shape(self, contract_client):
        r = contract_client.get("/api/graph/cloud?edge_type=competes_with")
        assert r.status_code == 200
        data = r.get_json()
        assert data["total_edges"] == 1
        _assert_contract(data, "GraphCloudResponse")


class TestGraphStatsContract:
    def test_stats_response_keys_match_graphstatsresponse(self, contract_client):
        r = contract_client.get("/api/graph/stats")
        assert r.status_code == 200
        data = r.get_json()
        # GraphStatsResponse has inline object types ({...}) whose fields the
        # flattened key-parser also picks up, so assert the top level + each
        # declared nested block explicitly.
        assert set(data) == {
            "structure",
            "structure_exact",
            "entities",
            "edges",
            "sectors",
            "hygiene",
            "staleness",
        }
        assert set(data["entities"]) == {"total", "by_type"}
        assert set(data["edges"]) == {"total", "by_type"}
        assert set(data["sectors"]) == {"count", "top", "size_distribution"}
        assert set(data["hygiene"]) == {
            "orphan_companies",
            "no_ticker",
            "self_loops",
            "orphan_edges",
            "conflicting_market_cap",
        }
        assert set(data["staleness"]) == {
            "stale",
            "most_recent_entity_update",
            "most_recent_analytics_compute",
        }
        # structure is None without the DuckDB graph layer (contract client).
        assert data["structure"] is None


# --------------------------------------------------------------------------- #
# DuckDB endpoints — verify error shape + structure WITHOUT DuckDB
# --------------------------------------------------------------------------- #


class TestGraphNeighborsContract:
    def test_error_shape_on_duckdb_missing(self, contract_client):
        """Without DuckDB, /api/graph/neighbors returns 500 with
        ErrorResponse shape (not a crash)."""
        r = contract_client.get("/api/graph/neighbors/HDFC%20Bank")
        assert r.status_code == 500
        data = r.get_json()
        assert "error" in data

    def test_invalid_asof_returns_error(self, contract_client):
        """as_of validation happens before DuckDB, so a bad as_of returns 400
        with the ErrorResponse shape."""
        r = contract_client.get("/api/graph/neighbors/HDFC%20Bank?as_of=banana")
        assert r.status_code == 400
        _assert_contract(r.get_json(), "ErrorResponse")


# --------------------------------------------------------------------------- #
# Scan ALL /api/* response shapes against api.ts (loose extra-keys check)
# --------------------------------------------------------------------------- #


class TestApiTsSelfConsistent:
    """api.ts itself must not declare an interface that app.py never returns.

    We can't enumerate app.py's jsonify keys statically here, but we CAN
    enforce that every interface we parse is sane (has at least one field)
    and that optional/required flags are parsed correctly."""

    def test_every_interface_has_fields(self):
        empty = [n for n, f in _interfaces.items() if not f]
        assert not empty, f"interfaces with no parsed fields: {empty}"

    def test_every_field_keeps_its_declared_type(self):
        """S1: the parser must capture type text for every field.

        Without this the type assertions silently degrade to key-presence
        checks, which is the bug this slice exists to close.
        """
        untyped = [
            f"{iface}.{name}"
            for iface, fields in _interfaces.items()
            for name, fld in fields.items()
            if not fld.type_text
        ]
        assert not untyped, f"fields parsed with no type text: {untyped}"

    def test_inline_object_fields_are_not_flattened(self):
        """Depth-aware scan: an inline object's fields belong to it, not to
        the enclosing interface (GraphStatsResponse.entities et al)."""
        stats = _interfaces["GraphStatsResponse"]
        assert set(stats) == {
            "structure",
            "structure_exact",
            "entities",
            "edges",
            "sectors",
            "hygiene",
            "staleness",
        }
        assert stats["entities"].type_text.startswith("{")
        # `total` lives inside the entities/edges blocks, not at top level.
        assert "total" not in stats, "nested inline fields leaked to top level"
        assert set(stats["entities"].subfields) == {"total", "by_type"}
        assert set(stats["sectors"].subfields) == {"count", "top", "size_distribution"}
        # A nested array-of-inline-objects keeps its shape one level down.
        assert stats["sectors"].subfields["top"].type_text == "{ sector: string; n: number }[]"

    def test_array_suffix_after_inline_object_is_kept(self):
        fld = _interfaces["CompanyNeighbors"]["jv_partners"]
        assert fld.type_text == "{ partner: string; venture: string }[]"
        # The element shape is one `[]` deep, so `subfields` is empty at
        # this level — the array branch of _assert_type handles the object.
        assert fld.subfields == {}
        assert fld.type_text[:-2] == "{ partner: string; venture: string }"

    def test_optionality_is_parsed(self):
        assert _interfaces["EntityDetail"]["frontmatter"].optional is True
        assert _interfaces["EntityDetail"]["content"].optional is True
        assert _interfaces["EntityDetail"]["name"].optional is False

    def test_error_response_is_uniform(self):
        """Every /api/* error body should be {error: string}."""
        assert _required_keys("ErrorResponse") == ["error"]


# --------------------------------------------------------------------------- #
# Type checker self-tests — a checker that never fires is the same blind
# spot this slice exists to close, so each branch gets a failing payload.
# --------------------------------------------------------------------------- #


class TestTypeAssertions:
    def test_number_field_rejects_string(self):
        with pytest.raises(AssertionError, match=r"total_count: expected number"):
            _assert_types({"total_count": "5"}, "EntitiesResponse")

    def test_nullable_field_accepts_null(self):
        _assert_types({"sector_classification": None}, "EntityListItem")

    def test_nullable_field_rejects_wrong_kind(self):
        with pytest.raises(AssertionError, match=r"file_path: expected string"):
            _assert_types({"file_path": 7}, "EntityListItem")

    def test_array_element_type_is_checked(self):
        ok = {"entities": [{"name": "A", "file_path": None}]}
        _assert_types(ok, "EntitiesResponse")
        with pytest.raises(AssertionError, match=r"entities\[1\].name: expected string"):
            _assert_types({"entities": [{"name": "A"}, {"name": 3}]}, "EntitiesResponse")

    def test_bool_is_not_a_number(self):
        with pytest.raises(AssertionError, match="expected number"):
            _assert_types({"total_count": True}, "EntitiesResponse")

    def test_string_literal_union_is_enforced(self):
        with pytest.raises(AssertionError, match="one of"):
            _assert_types({"entity_type": "fund"}, "CompanyNeighbors")
        _assert_types({"entity_type": "company"}, "CompanyNeighbors")

    def test_record_values_are_checked(self):
        _assert_types({"structure_exact": {"nodes": 3}}, "GraphStatsResponse")
        with pytest.raises(AssertionError, match=r"structure_exact.nodes: expected number"):
            _assert_types({"structure_exact": {"nodes": "3"}}, "GraphStatsResponse")

    def test_inline_object_is_checked_one_level_down(self):
        good = {"sectors": {"count": 2, "top": [{"sector": "Banking", "n": 2}]}}
        _assert_types(good, "GraphStatsResponse")
        bad = {"sectors": {"count": 2, "top": [{"sector": "Banking", "n": "2"}]}}
        with pytest.raises(AssertionError, match=r"sectors\.top\[0\].n: expected number"):
            _assert_types(bad, "GraphStatsResponse")

    def test_unknown_accepts_anything(self):
        _assert_types({"frontmatter": {"anything": [1, {"deep": True}]}}, "EntityDetail")

    def test_missing_key_is_left_to_presence_check(self):
        _assert_types({}, "EntitiesResponse")  # no raise: presence is _assert_keys' job

    def test_tuple_shape_is_checked(self):
        # Correct arity passes ...
        _assert_type([1.0, 2.0], "[number, number] | null", "x", frozenset())
        # ... a wrong one fails with the tuple expectation named.
        with pytest.raises(AssertionError, match="tuple"):
            _assert_type([1.0], "[number, number] | null", "x", frozenset())


# --------------------------------------------------------------------------- #
# S2 — the frontend-consumed /api/graph/* routes that had no shape coverage.
#
# These run against the hermetic `unit_client` fixture (tests/conftest.py:338):
# seeded SQLite + the isolated DuckDB graph layer, so helpers.graph.* routes
# answer for real instead of the 500 the DuckDB-blocked contract_client gives.
# --------------------------------------------------------------------------- #


class TestGraphAnalyticsContract:
    def test_positions_match_graphpositionsresponse(self, unit_client):
        r = unit_client.get("/api/graph/positions")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "GraphPositionsResponse")
        assert data["node_count"] >= 0

    def test_suggestions_match_suggestionsresponse(self, unit_client):
        r = unit_client.get("/api/graph/suggestions")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "SuggestionsResponse")

    def test_metric_groups_match_metricgroupsresponse(self, unit_client):
        r = unit_client.get("/api/graph/metrics/louvain_community")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "MetricGroupsResponse")

    def test_metric_seeds_match_metricseedsresponse(self, unit_client):
        # The route serves a seeds *subset* by name; the hermetic fixture has
        # no voterank rows, so assert the shape (list of strings, total in
        # sync) rather than specific membership.
        r = unit_client.get("/api/graph/metrics/voterank?seeds=HDFC%20Bank")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "MetricSeedsResponse")
        assert isinstance(data["seeds"], list)
        assert data["total"] == len(data["seeds"])

    def test_co_mentions_match_comentionsresponse(self, unit_client):
        r = unit_client.get("/api/graph/co-mentions?top=10")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "CoMentionsResponse")

    def test_bridges_match_bridgesresponse(self, unit_client):
        r = unit_client.get("/api/graph/bridges")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "BridgesResponse")

    def test_edges_by_year_match_edgesbyyearresponse(self, unit_client):
        r = unit_client.get("/api/graph/edges-by-year")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "EdgesByYearResponse")

    def test_near_duplicates_match_nearduplicatesresponse(self, unit_client):
        r = unit_client.get("/api/graph/near-duplicates?min_sim=0.1")
        assert r.status_code == 200
        _assert_contract(r.get_json(), "NearDuplicatesResponse")

    def test_semantic_match_semanticresponse(self, unit_client):
        r = unit_client.get("/api/graph/semantic/HDFC%20Bank")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "SemanticResponse")
        assert data["company"] == "HDFC Bank"

    def test_shortest_match_shortestpathresponse(self, unit_client):
        r = unit_client.get("/api/graph/shortest?a=HDFC%20Bank&b=Infosys")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "ShortestPathResponse")
        assert data["source"] == "HDFC Bank"
        assert data["target"] == "Infosys"

    def test_neighbors_company_match_companyneighbors(self, unit_client):
        r = unit_client.get("/api/graph/neighbors/HDFC%20Bank")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "CompanyNeighbors")
        assert data["entity_type"] == "company"
        assert "ICICI Bank" in data["peers"]

    def test_neighbors_sector_match_sectorneighbors(self, unit_client):
        r = unit_client.get("/api/graph/neighbors/Banking")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "SectorNeighbors")
        assert data["entity_type"] == "sector"
        assert data["member_count"] == len(data["members"])

    def test_refresh_match_graphrefreshresponse(self, unit_client):
        r = unit_client.post("/api/graph/refresh")
        assert r.status_code == 200
        data = r.get_json()
        _assert_contract(data, "GraphRefreshResponse")
        assert data["status"] in {"ok", "recomputed", "unchanged"}


class TestGraphAnalyticsErrorShapes:
    """Floor coverage for the two routes the hermetic fixture cannot feed.

    `/api/graph/edition_companies` needs chatter derive rows and
    `/api/graph/similar` needs note_search embeddings — neither exists in the
    unit fixture, so their success shapes are certified only when a future
    fixture grows those tables. What IS certified here is the error envelope
    and the parameter validation, both of which the frontend branches on.
    """

    def test_edition_companies_requires_edition_param(self, unit_client):
        r = unit_client.get("/api/graph/edition_companies")
        assert r.status_code == 400
        _assert_contract(r.get_json(), "ErrorResponse")

    def test_edition_companies_rejects_non_integer_k(self, unit_client):
        r = unit_client.get("/api/graph/edition_companies?edition=Anything&k=abc")
        assert r.status_code == 400
        _assert_contract(r.get_json(), "ErrorResponse")

    def test_edition_companies_unresolvable_edition_is_404(self, unit_client):
        r = unit_client.get("/api/graph/edition_companies?edition=No%20Such%20Edition")
        assert r.status_code == 404
        _assert_contract(r.get_json(), "ErrorResponse")

    def test_similar_unknown_note_is_404(self, unit_client):
        r = unit_client.get("/api/graph/similar/No%20Such%20Note.md")
        assert r.status_code == 404
        _assert_contract(r.get_json(), "ErrorResponse")

    def test_suggestions_unknown_method_is_400(self, unit_client):
        r = unit_client.get("/api/graph/suggestions?method=nonsense")
        assert r.status_code == 400
        data = r.get_json()
        _assert_keys(data, "ErrorResponse")
        assert isinstance(data["error"], str)


# --------------------------------------------------------------------------- #
# S2 coverage guard — every route the TS client calls must have a contract
# test above. Derived from the sources, so a NEW frontend call site fails
# here until it is covered (proposal AC 2: 12 -> 25).
# --------------------------------------------------------------------------- #


_COVERED_ROUTES = frozenset(
    {
        # SQLite / filesystem tier (contract_client)
        "/api/docs",
        "/api/docs/content",
        "/api/docs/search",
        "/api/scripts/search",
        "/api/entities",
        "/api/entity/<path:entity_path>",
        "/api/events/<path:name>",
        "/api/search",
        "/api/sectors",
        "/api/stats",
        "/api/graph/cloud",
        "/api/graph/stats",
        # hermetic graph tier (unit_client)
        "/api/graph/bridges",
        "/api/graph/co-mentions",
        "/api/graph/edges-by-year",
        "/api/graph/metrics/<metric>",
        "/api/graph/near-duplicates",
        "/api/graph/neighbors/<path:name>",
        "/api/graph/positions",
        "/api/graph/refresh",
        "/api/graph/semantic/<path:name>",
        "/api/graph/shortest",
        "/api/graph/suggestions",
        # error-shape only (fixture cannot serve the success body)
        "/api/graph/edition_companies",
        "/api/graph/similar/<path:note_path>",
    }
)


class TestRouteCoverage:
    def test_every_consumed_route_has_a_contract_test(self):
        consumed = _consumed_routes()
        uncovered = sorted(consumed - _COVERED_ROUTES)
        assert not uncovered, (
            f"frontend calls {len(uncovered)} route(s) with no contract test: {uncovered}"
        )

    def test_covered_routes_are_still_consumed(self):
        """No rot: a route the frontend stopped calling must be dropped from
        _COVERED_ROUTES. Checked against the union of consumed routes and
        app.py's own declarations — a set can only be stale by having an
        entry in neither."""
        known = _consumed_routes() | set(_app_routes())
        stale = sorted(_COVERED_ROUTES - known)
        assert not stale, f"_COVERED_ROUTES names routes no longer declared or called: {stale}"

    def test_coverage_count_matches_the_measured_surface(self):
        """25 consumed routes as measured on 2026-10-06 (proposal B2)."""
        assert len(_consumed_routes()) == 25
