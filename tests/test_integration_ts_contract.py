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

import re
import sqlite3
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

PROJECT_ROOT = Path(__file__).resolve().parents[1]

API_TYPES = PROJECT_ROOT / "frontend" / "types" / "api.ts"


# --------------------------------------------------------------------------- #
# Parse api.ts interfaces
# --------------------------------------------------------------------------- #


def _parse_interfaces() -> tuple[dict[str, dict], list[str]]:
    """Parse api.ts into {InterfaceName: {field: optional_bool}}.

    Handles `extends` (inlines parent fields) and comments.
    """
    text = API_TYPES.read_text(encoding="utf-8")
    ifaces: dict[str, dict] = {}
    order: list[str] = []

    # Find each interface block
    for m in re.finditer(r"export interface (\w+)(?: extends ([^{]+))?\s*\{", text):
        name = m.group(1)
        base = m.group(2).strip() if m.group(2) else None
        start = m.end()
        # Find the matching closing brace
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[start : i - 1]
        fields = {}
        for fm in re.finditer(r"^(\s*)([\w]+)(\??)\s*:\s*([^\n]+)$", body, re.MULTILINE):
            fname = fm.group(2)
            optional = bool(fm.group(3))
            fields[fname] = optional
        if base:
            base_names = [b.strip() for b in base.split(",")]
            for bn in base_names:
                if bn in ifaces:
                    # inline parent fields (parent optionality trumps if child
                    # redeclares as required for simplicity)
                    for f, opt in ifaces[bn].items():
                        fields.setdefault(f, opt)
        ifaces[name] = fields
        order.append(name)
    return ifaces, order


_interfaces, _interface_order = _parse_interfaces()


def _required_keys(iface: str) -> list[str]:
    return [f for f, opt in _interfaces.get(iface, {}).items() if not opt]


def _all_keys(iface: str) -> list[str]:
    return list(_interfaces.get(iface, {}).keys())


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
# Helper to assert response keys cover an api.ts interface
# --------------------------------------------------------------------------- #


def _assert_keys(data: dict, iface: str, required_only: bool = False):
    """Assert every field in the api.ts interface appears in the response."""
    keys = _required_keys(iface) if required_only else _all_keys(iface)
    missing = [k for k in keys if k not in data]
    assert not missing, (
        f"api.ts interface {iface} declares {missing} key(s) missing from "
        f"response {sorted(data.keys())}"
    )


# --------------------------------------------------------------------------- #
# SQLite-only endpoints (full contract verification)
# --------------------------------------------------------------------------- #


class TestSectorsContract:
    def test_response_keys_match_sectorsresponse(self, contract_client):
        r = contract_client.get("/api/sectors")
        assert r.status_code == 200
        _assert_keys(r.get_json(), "SectorsResponse")

    def test_entity_keys_match(self, contract_client):
        r = contract_client.get("/api/sectors")
        data = r.get_json()
        assert data["classifications"] == ["Banking", "Technology"]
        se = data["sector_entities"]
        # Each sector entity must match SectorEntity shape
        for entity in se:
            _assert_keys(entity, "SectorEntity")


class TestStatsContract:
    def test_response_keys_match_statsresponse(self, contract_client):
        r = contract_client.get("/api/stats")
        assert r.status_code == 200
        _assert_keys(r.get_json(), "StatsResponse")

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
        _assert_keys(r.get_json(), "EntitiesResponse")

    def test_item_keys_match_entitylistitem(self, contract_client):
        r = contract_client.get("/api/entities")
        data = r.get_json()
        assert data["entities"]
        for item in data["entities"]:
            _assert_keys(item, "EntityListItem")

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
        _assert_keys(data, "EntityDetail")

    def test_missing_entity_returns_error_shape(self, contract_client):
        r = contract_client.get("/api/entity/Does%20Not%20Exist")
        assert r.status_code == 404
        _assert_keys(r.get_json(), "ErrorResponse")


class TestSearchContract:
    def test_response_keys_match_searchresponse(self, contract_client):
        r = contract_client.get("/api/search?q=bank")
        assert r.status_code == 200
        _assert_keys(r.get_json(), "SearchResponse")

    def test_result_keys_match_searchresult(self, contract_client):
        r = contract_client.get("/api/search?q=bank")
        data = r.get_json()
        assert data["results"]
        for hit in data["results"]:
            _assert_keys(hit, "SearchResult")

    def test_empty_query_returns_error(self, contract_client):
        r = contract_client.get("/api/search")
        assert r.status_code == 400
        _assert_keys(r.get_json(), "ErrorResponse")


class TestEventsContract:
    def test_response_keys_match_eventsresponse(self, contract_client):
        r = contract_client.get("/api/events/HDFC%20Bank")
        assert r.status_code == 200
        data = r.get_json()
        _assert_keys(data, "EventsResponse")
        assert data["entity"] == "HDFC Bank"
        assert data["event_count"] == 1

    def test_event_item_keys_match_eventitem(self, contract_client):
        r = contract_client.get("/api/events/HDFC%20Bank")
        data = r.get_json()
        assert data["events"]
        for ev in data["events"]:
            _assert_keys(ev, "EventItem")

    def test_events_response_has_all_keys(self, contract_client):
        """Cover every key in EventItem, not just required ones."""
        r = contract_client.get("/api/events/HDFC%20Bank")
        data = r.get_json()
        for ev in data["events"]:
            _assert_keys(ev, "EventItem")


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
        _assert_keys(data, "DocsResponse")
        assert data["docs"]
        for doc in data["docs"]:
            _assert_keys(doc, "DocItem")

    def test_content_keys_match_doccontentresponse(self, contract_client):
        # A real doc that always exists in the repo.
        r = contract_client.get("/api/docs/content?path=design/architecture.md")
        assert r.status_code == 200
        _assert_keys(r.get_json(), "DocContentResponse")

    def test_search_keys_match_docsearchresponse_and_hit(self, contract_client):
        r = contract_client.get("/api/docs/search?q=graph")
        assert r.status_code == 200
        data = r.get_json()
        _assert_keys(data, "DocSearchResponse")
        for hit in data["results"]:
            _assert_keys(hit, "DocSearchHit")

    def test_search_empty_query_returns_error(self, contract_client):
        r = contract_client.get("/api/docs/search")
        assert r.status_code == 400
        _assert_keys(r.get_json(), "ErrorResponse")

    def test_content_unknown_path_returns_error(self, contract_client):
        r = contract_client.get("/api/docs/content?path=nope.md")
        assert r.status_code == 404
        _assert_keys(r.get_json(), "ErrorResponse")


# --------------------------------------------------------------------------- #
# Graph cloud + graph stats — SQLite-backed, full contract verification
# --------------------------------------------------------------------------- #


class TestGraphCloudContract:
    def test_cloud_response_keys_match_graphcloudresponse(self, contract_client):
        r = contract_client.get("/api/graph/cloud")
        assert r.status_code == 200
        _assert_keys(r.get_json(), "GraphCloudResponse")

    def test_cloud_node_keys_match_graphcloudnode(self, contract_client):
        data = contract_client.get("/api/graph/cloud").get_json()
        assert data["nodes"], "cloud should return at least the 5 seed entities"
        for node in data["nodes"]:
            _assert_keys(node, "GraphCloudNode")

    def test_cloud_edge_keys_match_graphcloudedge(self, contract_client):
        data = contract_client.get("/api/graph/cloud").get_json()
        assert data["edges"], "cloud should return the seed edges"
        for edge in data["edges"]:
            _assert_keys(edge, "GraphCloudEdge")

    def test_cloud_relationship_types_match(self, contract_client):
        data = contract_client.get("/api/graph/cloud").get_json()
        types = data["relationship_types"]
        assert types, "cloud should summarise the seed edge types"
        for t in types:
            _assert_keys(t, "RelationshipTypeSummary")

    def test_cloud_filtered_response_keeps_shape(self, contract_client):
        r = contract_client.get("/api/graph/cloud?edge_type=competes_with")
        assert r.status_code == 200
        data = r.get_json()
        assert data["total_edges"] == 1
        _assert_keys(data, "GraphCloudResponse")


class TestGraphStatsContract:
    def test_stats_response_keys_match_graphstatsresponse(self, contract_client):
        r = contract_client.get("/api/graph/stats")
        assert r.status_code == 200
        data = r.get_json()
        # GraphStatsResponse has inline object types ({...}) whose fields the
        # flattened key-parser also picks up, so assert the top level + each
        # declared nested block explicitly.
        assert set(data) == {"structure", "entities", "edges", "sectors", "hygiene", "staleness"}
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
        _assert_keys(r.get_json(), "ErrorResponse")


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

    def test_error_response_is_uniform(self):
        """Every /api/* error body should be {error: string}."""
        assert _required_keys("ErrorResponse") == ["error"]
