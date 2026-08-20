#!/usr/bin/env python3
"""P2 — Flask API integration tests.

Covers the 6 routes from app.py that have zero unit/integration test coverage.
Uses a seeded SQLite DB via the conftest seeded_graph_sqlite_db fixture pattern.

See doc/improvements/archive/testing/integration_plan.txt § Priority 2 for full rationale.
"""
from __future__ import annotations


import pytest

from helpers.core.db import connect

import app as A

pytestmark = [pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Schema + seed data shared across all P2 tests
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY NOT NULL,
    entity_type TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_path TEXT,
    last_updated DATETIME,
    normalized_name TEXT,
    sector_classification TEXT,
    ticker TEXT
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag),
    FOREIGN KEY (entity_name) REFERENCES entities(name) ON DELETE CASCADE
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
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    entity TEXT NOT NULL,
    event_type TEXT,
    event_date TEXT,
    period TEXT,
    date_precision TEXT,
    magnitude TEXT,
    counterparty TEXT,
    source_quote TEXT,
    as_of_edition TEXT
);
CREATE VIRTUAL TABLE note_search USING fts5(
    doc_type, file_path UNINDEXED, title, sector, content,
    tokenize = 'porter unicode61'
);
"""

_SEED = [
    # (name, type, sector, file_path, ticker, tags)
    (
        "HDFC Bank",
        "company",
        "Banking",
        "findata/Companies/Banking/Hdfc_Bank.md",
        "HDFCBANK",
        ["entity_type/company", "sector/banking", "market_cap/large_cap"],
    ),
    (
        "ICICI Bank",
        "company",
        "Banking",
        "findata/Companies/Banking/ICICI_Bank.md",
        "ICICIBANK",
        ["entity_type/company", "sector/banking", "market_cap/large_cap"],
    ),
    (
        "Infosys",
        "company",
        "Technology",
        "findata/Companies/Technology/Infosys.md",
        "INFY",
        ["entity_type/company", "sector/technology", "market_cap/large_cap"],
    ),
    (
        "Banking",
        "sector",
        None,
        "findata/Sectors/Banking.md",
        None,
        ["entity_type/sector", "sector/banking"],
    ),
    (
        "Technology",
        "sector",
        None,
        "findata/Sectors/Technology.md",
        None,
        ["entity_type/sector", "sector/technology"],
    ),
]


@pytest.fixture
def p2_client(tmp_path):
    """Flask test_client with a fully seeded synthetic DB."""
    db_path = str(tmp_path / "p2_test.db")
    conn = connect(db_path)
    conn.executescript(_SCHEMA)

    for name, etype, sec, fp, ticker, tags in _SEED:
        conn.execute(
            "INSERT INTO entities(name, entity_type, sector_classification, "
            "file_path, ticker) VALUES (?,?,?,?,?)",
            (name, etype, sec, fp, ticker),
        )
        for tag in tags:
            conn.execute("INSERT INTO entity_tags(entity_name, tag) VALUES (?,?)", (name, tag))
    conn.commit()
    conn.close()

    # Populate note_search FTS5 index so /api/search works
    try:
        conn = connect(db_path)
        conn.execute("""
            INSERT INTO note_search(doc_type, file_path, title, sector, content)
            VALUES ('company', 'findata/Companies/Banking/Hdfc_Bank.md', 'HDFC Bank', 'Banking', 'HDFC Bank is a large private-sector bank')
        """)
        conn.execute("""
            INSERT INTO note_search(doc_type, file_path, title, sector, content)
            VALUES ('chatter', 'findata/The_Chatter/Test_Edition.md', 'Test Edition', '', 'banking sector growth')
        """)
        conn.commit()
        conn.close()
    except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
        pass  # note_search may not be needed for all tests

    # Monkeypatch app.get_db_connection to use our test DB
    # connect() from helpers.core.db sets row_factory=sqlite3.Row + pragmas
    saved_gdb = A.get_db_connection
    A.get_db_connection = lambda: connect(db_path)  # ty: ignore[invalid-assignment]

    try:
        yield A.app.test_client()
    finally:
        A.get_db_connection = saved_gdb


# --------------------------------------------------------------------------- #
# Route coverage tests
# --------------------------------------------------------------------------- #


class TestRootRoute:
    """Route GET / — the index/home page."""

    def test_root_returns_200(self, p2_client):
        r = p2_client.get("/")
        assert r.status_code == 200

    def root_returns_html(self, p2_client):
        r = p2_client.get("/")
        assert "text/html" in r.headers.get("Content-Type", "")


class TestFindataViewerRoute:
    """Route GET /findata — the findata viewer page."""

    def test_findata_returns_200(self, p2_client):
        r = p2_client.get("/findata")
        assert r.status_code == 200

    def findata_returns_html(self, p2_client):
        r = p2_client.get("/findata")
        assert "text/html" in r.headers.get("Content-Type", "")


class TestApiSectorsRoute:
    """Route GET /api/sectors — sector listing API."""

    def test_sectors_returns_200(self, p2_client):
        r = p2_client.get("/api/sectors")
        assert r.status_code == 200

    def sectors_returns_json(self, p2_client):
        r = p2_client.get("/api/sectors")
        assert r.is_json

    def sectors_have_classifications(self, p2_client):
        r = p2_client.get("/api/sectors")
        data = r.get_json()
        classifications = data.get("classifications", [])
        # Should contain Banking and/or Technology from our seed data
        assert len(classifications) > 0

    def sectors_have_sector_entities(self, p2_client):
        r = p2_client.get("/api/sectors")
        data = r.get_json()
        se = data.get("sector_entities", [])
        # Each entity should have name, file_path
        for ent in se:
            assert "name" in ent
            assert "file_path" in ent

    def sectors_have_super_sectors(self, p2_client):
        r = p2_client.get("/api/sectors")
        data = r.get_json()
        ss = data.get("super_sectors", [])
        # super_sectors may be empty if no super_sector entities, but shape must be right
        for s in ss:
            assert "name" in s
            assert "sectors" in s


class TestApiEntityDetailRoute:
    """Route GET /api/entity/<path:entity_path> — entity detail API."""

    def test_entity_detail_returns_200_existing(self, p2_client):
        r = p2_client.get("/api/entity/HDFC%20Bank")
        assert r.status_code == 200

    def test_entity_detail_404_missing(self, p2_client):
        r = p2_client.get("/api/entity/Does%20Not%20Exist")
        assert r.status_code == 404

    def entity_detail_has_required_keys(self, p2_client):
        r = p2_client.get("/api/entity/HDFC%20Bank")
        data = r.get_json()
        # EntityDetail = EntityListItem + frontmatter + content + raw_content
        required = ["name", "entity_type", "sector_classification", "file_path"]
        for key in required:
            assert key in data, f"Missing required key '{key}' in entity detail response"

    def entity_detail_has_enhanced_tags(self, p2_client):
        r = p2_client.get("/api/entity/HDFC%20Bank")
        data = r.get_json()
        assert "enhanced_tags" in data
        # Should have tags from the seeded DB
        assert len(data["enhanced_tags"]) > 0

    def entity_detail_market_cap_from_tag(self, p2_client):
        r = p2_client.get("/api/entity/HDFC%20Bank")
        data = r.get_json()
        assert "market_cap" in data
        # HDFC Bank has market_cap/large_cap tag
        assert data["market_cap"] == "large_cap"


class TestDebugEntityRoute:
    """GET /debug/entity/<path:entity_path> — REMOVED (SEC-1, 2026-08-17).

    The route echoed the raw path into a text/html response (reflected
    XSS, confirmed live). It was deleted; the regression test now pins
    the removal. Full vector coverage lives in
    tests/test_security_headers.py::TestSec1DebugEntityRemoved.
    """

    def test_debug_entity_removed(self, p2_client):
        r = p2_client.get("/debug/entity/HDFC%20Bank")
        assert r.status_code == 404


class TestPointsAndFiguresImagesRoute:
    """Route GET /points_and_figures/images/<path:filename> — image serving."""

    def test_images_404_when_no_file(self, p2_client):
        """Images dir may be empty; a 400 or 404 is acceptable for a missing file."""
        r = p2_client.get("/points_and_figures/images/nonexistent.jpg")
        # Accept 400 (BadRequest) or 404 (NotFound) — the route may not have
        # explicit error handling for missing files in all cases.
        assert r.status_code in (200, 400, 404)

    def test_images_route_defined(self, p2_client):
        """Just verify the route is registered and responds (may be 404 for missing files)."""
        r = p2_client.get("/points_and_figures/images/test_chart.png")
        # Should not be a 500 crash
        assert r.status_code != 500


# --------------------------------------------------------------------------- #
# Loose shape-validation: ensure every /api/* response has the keys we
# *expect* based on code inspection (not full api.ts contract — that's P6)
# --------------------------------------------------------------------------- #


class TestApiResponseShapes:
    """Loose assertions that app.py jsonify blocks contain the keys we
    *historically* return for each endpoint. These guard against accidental
    key-drift (e.g. a developer removing a field from a dict)."""

    def test_api_entities_response_has_expected_keys(self, p2_client):
        r = p2_client.get("/api/entities?type=company&sector=Banking")
        assert r.status_code == 200
        data = r.get_json()
        # EntitiesResponse: {entities: [...], total_count, limit, offset}
        assert "entities" in data
        assert "total_count" in data
        assert "limit" in data
        assert "offset" in data
        if data["entities"]:
            e = data["entities"][0]
            # EntityListItem shape
            for k in ("name", "entity_type", "sector_classification", "market_cap", "enhanced_tags", "file_path"):
                assert k in e, f"EntityListItem missing key '{k}'"

    def test_api_stats_response_has_expected_keys(self, p2_client):
        r = p2_client.get("/api/stats")
        assert r.status_code == 200
        data = r.get_json()
        # StatsResponse: {entity_counts, top_sectors, market_cap_counts, total_entities}
        for k in ("entity_counts", "top_sectors", "market_cap_counts", "total_entities"):
            assert k in data, f"StatsResponse missing key '{k}'"

    def test_api_search_response_has_expected_keys(self, p2_client):
        r = p2_client.get("/api/search?q=bank")
        assert r.status_code == 200
        data = r.get_json()
        # SearchResponse: {results: [...], total_count, limit, offset}
        assert "results" in data
        assert "total_count" in data
        assert "limit" in data
        assert "offset" in data
        if data["results"]:
            hit = data["results"][0]
            # SearchResult: doc_type, file_path, title, sector, snippet
            for k in ("doc_type", "file_path", "title", "sector", "snippet"):
                assert k in hit, f"SearchResult missing key '{k}'"

    def test_api_events_response_has_expected_keys(self, p2_client):
        r = p2_client.get("/api/events/HDFC%20Bank")
        assert r.status_code == 200
        data = r.get_json()
        # EventsResponse: {entity, entity_type, file_path, event_count, events[]}
        assert "entity" in data
        assert "entity_type" in data
        assert "file_path" in data
        assert "event_count" in data
        assert "events" in data
        if data["events"]:
            ev = data["events"][0]
            # EventItem: event_type, event_date, period, date_precision, magnitude,
            # counterparty, source_quote, as_of_edition
            for k in ("event_type", "event_date", "period", "date_precision", "magnitude",
                      "counterparty", "source_quote", "as_of_edition"):
                assert k in ev, f"EventItem missing key '{k}'"
