"""Shared test-DB schema (consolidation: single source of truth).

Canonical DDL for the table shapes re-defined across ~20 test files.
Each constant is the byte-exact majority text measured 2026-09-03; files
whose local DDL differs keep the differing table locally (or adopt the
canonical text when their tests pass unchanged — never silently).

Variants mirror the proposal: ``minimal`` (entities + graph_edges),
``full`` (+ entity_tags / graph_analytics / events), ``search`` (+
note_search FTS5). Files with subsets pass ``tables=[...]``; files with
exotic extras (quotes, company_metrics, …) append ``extra_ddl``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ENTITIES_8COL = """
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
"""

EDGES_12COL = """
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    UNIQUE(source, target, edge_type)
);
"""

ENTITY_TAGS = """
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag)
);
"""

GRAPH_ANALYTICS = """
CREATE TABLE graph_analytics (
    entity_name TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       TEXT NOT NULL,
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric, entity_name)
);
"""

EVENTS = """
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    event_type TEXT,
    event_date TEXT,
    period TEXT,
    date_precision TEXT,
    magnitude TEXT,
    counterparty TEXT,
    source_quote TEXT,
    as_of_edition TEXT,
    source_ref TEXT,
    properties TEXT
);
"""

NOTE_SEARCH_FTS = """
CREATE VIRTUAL TABLE note_search USING fts5(
    doc_type, file_path UNINDEXED, title, sector, content,
    tokenize = 'porter unicode61'
);
"""

ENTITIES_MINIMAL = """
CREATE TABLE entities(
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    normalized_name TEXT,
    file_path TEXT,
    last_updated TEXT
);
"""

ENTITIES_4COL = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    sector_classification TEXT,
    file_path TEXT
);
"""

RELATIONS = """
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

EDGES_MINIMAL = """
CREATE TABLE graph_edges(
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, target, edge_type)
);
"""

VARIANTS: dict[str, list[str]] = {
    "minimal": [ENTITIES_MINIMAL, EDGES_MINIMAL],
    "full": [ENTITIES_8COL, EDGES_12COL, ENTITY_TAGS, GRAPH_ANALYTICS, EVENTS],
    "search": [
        ENTITIES_8COL,
        EDGES_12COL,
        ENTITY_TAGS,
        GRAPH_ANALYTICS,
        EVENTS,
        NOTE_SEARCH_FTS,
    ],
}


def build_test_db(
    path: Path | str,
    variant: str = "full",
    *,
    tables: list[str] | None = None,
    extra_ddl: str = "",
) -> Path:
    """Create a test SQLite DB file with the shared schema.

    Args:
        path: destination file (parents created).
        variant: one of ``minimal`` / ``full`` / ``search``.
        tables: explicit DDL list overriding the variant (for subsets).
        extra_ddl: additional statements for file-specific extras.
    """
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(dst))
    try:
        conn.executescript("".join(tables if tables is not None else VARIANTS[variant]))
        if extra_ddl:
            conn.executescript(extra_ddl)
        conn.commit()
    finally:
        conn.close()
    return dst
