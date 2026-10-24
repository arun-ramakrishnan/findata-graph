"""Seeded SQLite DB for relational / SQL correctness tests.

Builds a small, deterministic DB with the F1/F2 edge cases plus the edge
types exercised by the read-only query wrappers (co_mention_top,
cross_sector_bridges, edges_by_year, sector_members_with_market_cap):

  * "Cap Conflict Co" carries TWO market_cap/* tags (large_cap + mid_cap) —
    exercises market_cap_sql() MIN tie-break (F1) and /api/stats double-count (F2).
  * co_mentioned_in edges give a deterministic co-mention ranking.
  * acquired / jv_with edges (with valid_from) drive cross_sector_bridges
    (one cross-sector, one same-sector that must be excluded) and
    edges_by_year (by-year buckets).
  * sector hyperedge incidences give membership for
    sector_members_with_market_cap. Since the part_of/has_company retirement
    (2026-10-19) membership is CANONICAL in hyper_edges/hyper_incidences and
    the part_of/has_company pair is projected on read by
    helpers/graph/query.py — so the fixture seeds the store, not the dyads.

Schema mirrors tests/conftest._UNIT_SCHEMA (unit-test shaped; no FK cascade).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SEED_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    file_path TEXT,
    sector_classification TEXT,
    ticker TEXT
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag         TEXT NOT NULL,
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
    edge_type TEXT NOT NULL,
    label TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    valid_from DATE, valid_to DATE,
    source_ref TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(edge_type, label)
);
CREATE TABLE hyper_incidences (
    edge_id INTEGER NOT NULL,
    entity_name TEXT NOT NULL,
    weight REAL, direction TEXT, role TEXT,
    valid_from DATE, valid_to DATE,
    PRIMARY KEY (edge_id, entity_name)
);
"""


def build_seed_db(path: Path) -> Path:
    """Create the seeded DB at *path* (overwriting) and return the path."""
    if path.exists():
        path.unlink()
    con = sqlite3.connect(str(path))
    con.executescript(SEED_SCHEMA)
    entities = [
        ("HDFC Bank", "company", "findata/Companies/Banking/Hdfc_Bank.md", "Banking", "HDFCBANK"),
        (
            "ICICI Bank",
            "company",
            "findata/Companies/Banking/ICICI_Bank.md",
            "Banking",
            "ICICIBANK",
        ),
        ("Infosys", "company", "findata/Companies/Technology/Infosys.md", "Technology", "INFY"),
        ("No Ticker Co", "company", "findata/Companies/X/No_Ticker.md", "Technology", None),
        ("Cap Conflict Co", "company", "findata/Companies/X/Cap_Conflict.md", "Technology", None),
        ("Banking", "sector", "findata/Sectors/Banking.md", None, None),
        ("Technology", "sector", "findata/Sectors/Technology.md", None, None),
        ("Agriculture", "sector", "findata/Sectors/Agriculture.md", None, None),
        ("Packaging", "sector", "findata/Sectors/Packaging.md", None, None),
        # Dual-member company (graph_read_path_consolidation S2b): the legacy
        # single-valued sector_classification sees only Agriculture; the
        # canonical hyper store (below) carries Agriculture AND Packaging —
        # exactly the expressiveness gap that motivated the retirement.
        (
            "Kuantum Papers",
            "company",
            "findata/Companies/Materials/Kuantum_Papers.md",
            "Agriculture",
            None,
        ),
    ]
    con.executemany("INSERT INTO entities VALUES (?,?,?,?,?)", entities)
    tags = [
        ("HDFC Bank", "market_cap/large_cap"),
        ("ICICI Bank", "market_cap/large_cap"),
        ("Infosys", "market_cap/large_cap"),
        ("No Ticker Co", "market_cap/small_cap"),
        # F1/F2 edge case: two conflicting market_cap/* tags on one company.
        ("Cap Conflict Co", "market_cap/large_cap"),
        ("Cap Conflict Co", "market_cap/mid_cap"),
    ]
    con.executemany("INSERT INTO entity_tags (entity_name, tag) VALUES (?, ?)", tags)

    # (source, target, edge_type, source_ref, valid_from)
    edges = [
        # NB: no part_of/has_company rows — sector membership is canonical in
        # the incidence store (2026-10-19 retirement), seeded just below.
        # co-mention ranking: Infosys(4) > HDFC(3) > ICICI(2)
        ("Infosys", "HDFC Bank", "co_mentioned_in", "seed", None),
        ("Infosys", "ICICI Bank", "co_mentioned_in", "seed", None),
        ("Infosys", "No Ticker Co", "co_mentioned_in", "seed", None),
        ("Infosys", "Cap Conflict Co", "co_mentioned_in", "seed", None),
        ("HDFC Bank", "Infosys", "co_mentioned_in", "seed", None),
        ("HDFC Bank", "ICICI Bank", "co_mentioned_in", "seed", None),
        ("HDFC Bank", "No Ticker Co", "co_mentioned_in", "seed", None),
        ("ICICI Bank", "Infosys", "co_mentioned_in", "seed", None),
        ("ICICI Bank", "HDFC Bank", "co_mentioned_in", "seed", None),
        # cross-sector M&A (counted) + same-sector (must be excluded)
        ("HDFC Bank", "Infosys", "acquired", "seed", "2021-03-15"),
        ("HDFC Bank", "ICICI Bank", "acquired", "seed", "2022-07-01"),
        ("ICICI Bank", "Infosys", "jv_with", "seed", "2020-11-20"),
        # S2b dual-membership edge: Kuantum (Agriculture+Packaging) -> Infosys
        # (Technology) must yield TWO bridge groups post-S2b.
        ("Kuantum Papers", "Infosys", "acquired", "seed", "2023-02-10"),
    ]
    con.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, valid_from) "
        "VALUES (?,?,?,?,?)",
        edges,
    )
    # Sector membership — the canonical store (2026-10-19 retirement). One
    # hyperedge per sector, one incidence per member; query.py projects both
    # directions into e_belongs/e_has.
    con.executemany(
        "INSERT INTO hyper_edges (id, edge_type, label, source_ref) VALUES (?,?,?,?)",
        [
            (1, "sector", "Banking", "seed"),
            (2, "sector", "Technology", "seed"),
            (3, "sector", "Agriculture", "seed"),
            (4, "sector", "Packaging", "seed"),
        ],
    )
    con.executemany(
        "INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (?,?)",
        [
            (1, "HDFC Bank"),
            (1, "ICICI Bank"),
            (2, "Infosys"),
            (2, "No Ticker Co"),
            (2, "Cap Conflict Co"),
            # Kuantum Papers: dual membership — BOTH sector hyperedges.
            (3, "Kuantum Papers"),
            (4, "Kuantum Papers"),
        ],
    )
    con.commit()
    con.close()
    return path
