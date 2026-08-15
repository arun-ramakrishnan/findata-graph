"""
Tier 1: api_entities tag-based filtering.

Exercises the /api/entities filtering (sector, marketcap, intersection, search)
against a tiny seeded DB with the normalized entity_tags table, proving the
filters resolve through entity_tags rather than the legacy columns.
"""

import sqlite3
from contextlib import contextmanager

import pytest

import app as A

_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    sector_classification TEXT,
    file_path TEXT
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag),
    FOREIGN KEY (entity_name) REFERENCES entities(name)
        ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    UNIQUE(source, target, relation_type),
    FOREIGN KEY (source) REFERENCES entities(name)
        ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (target) REFERENCES entities(name)
        ON DELETE CASCADE ON UPDATE CASCADE
);
"""

# name, type, sector_classification, file_path, tags[]
# Bundle C2: market_cap dropped from the entities column; the market_cap/*
# tag in the tags list is the source of truth (tests tag-based filtering).
_SEED = [
    (
        "HDFC Bank",
        "company",
        "Banking",
        "findata/Companies/Banking/HDFC_Bank.md",
        ["entity_type/company", "sector/banking", "market_cap/large_cap"],
    ),
    (
        "Small Co-op Bank",
        "company",
        "Banking",
        "findata/Companies/Banking/Small_Bank.md",
        ["entity_type/company", "sector/banking", "market_cap/small_cap"],
    ),
    (
        "Infosys",
        "company",
        "Technology",
        "findata/Companies/Technology/Infosys.md",
        ["entity_type/company", "sector/technology", "market_cap/large_cap"],
    ),
    (
        "TinyTech",
        "company",
        "Technology",
        "findata/Companies/Technology/TinyTech.md",
        ["entity_type/company", "sector/technology", "market_cap/small_cap"],
    ),
    (
        "Banking",
        "sector",
        None,
        "findata/Sectors/Banking.md",
        ["entity_type/sector", "sector/banking"],
    ),
]


@contextmanager
def _seeded_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    for name, etype, sec, fp, tags in _SEED:
        conn.execute(
            "INSERT INTO entities (name, entity_type, sector_classification, "
            "file_path) VALUES (?,?,?,?)",
            (name, etype, sec, fp),
        )
        for t in tags:
            conn.execute(
                "INSERT INTO entity_tags (entity_name, tag) VALUES (?,?)", (name, t)
            )
    conn.commit()
    conn.close()

    saved = A.get_db_connection
    A.get_db_connection = lambda: sqlite3.connect(str(db_path))  # ty: ignore[invalid-assignment]
    try:
        yield A.app.test_client()
    finally:
        A.get_db_connection = saved


@pytest.fixture
def client(tmp_path):
    with _seeded_db(tmp_path) as c:
        yield c


def _names(resp):
    return sorted(e["name"] for e in resp.get_json()["entities"])


def _count(resp):
    return resp.get_json()["total_count"]


def test_sector_filter_uses_tags(client):
    # PascalCase sector value, as the frontend dropdown sends it
    r = client.get("/api/entities?type=company&sector=Banking")
    assert _names(r) == ["HDFC Bank", "Small Co-op Bank"]
    assert _count(r) == 2


def test_marketcap_filter_uses_tags(client):
    r = client.get("/api/entities?type=company&marketcap=large_cap")
    assert _names(r) == ["HDFC Bank", "Infosys"]


def test_tag_intersection(client):
    r = client.get("/api/entities?type=company&sector=Technology&marketcap=small_cap")
    assert _names(r) == ["TinyTech"]


def test_search_by_sector_name(client):
    r = client.get("/api/entities?type=company&search=banking")
    assert _names(r) == ["HDFC Bank", "Small Co-op Bank"]


def test_search_by_company_name(client):
    r = client.get("/api/entities?type=company&search=Infosys")
    assert _names(r) == ["Infosys"]


def test_response_includes_tags(client):
    r = client.get("/api/entities?type=company&sector=Banking")
    ent = r.get_json()["entities"][0]
    assert "entity_type/company" in ent["enhanced_tags"]
    assert "sector/banking" in ent["enhanced_tags"]


def test_no_filter_returns_all_companies(client):
    r = client.get("/api/entities?type=company")
    assert _count(r) == 4  # 4 companies (excludes the Banking sector entity)
