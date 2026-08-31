#!/usr/bin/env python3
"""Phase 1 migration: relations → graph_edges.

Idempotent. Safe to re-run. Run once on a snapshot-protected DB:

    python3 helpers/maintenance/migrate_to_graph_edges.py

What it does (all in one SQLite transaction):
  0. Ensures `entities` exists with the canonical schema (name-suffix CHECK,
     all 10 columns, 4 production indexes). IF NOT EXISTS = no-op on live DB.
  1. Creates `graph_edges` table if missing (per doc/design/graph_design.txt §4).
  2. Creates `graph_analytics` table if missing.
  3. Backfills `graph_edges` from `relations` (idempotent via INSERT OR IGNORE).
  4. Replaces the legacy `relations` TABLE with a VIEW backed by graph_edges.
     All existing readers (snapshot_db, database_integrity_check, doc snippets)
     keep working unchanged because the view exposes the same column names:
     source, target, relation_type.
  5. Sanity-checks row counts and prints a summary.

Notes:
- Legacy writers (parse_newsletter, move_sector) must be migrated separately
  to write to graph_edges directly. Until then, any INSERT INTO relations
  will fail because the view is read-only.
- WAL mode + PRAGMA foreign_keys = ON are set via the central connect() helper.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402

DB_PATH = PROJECT_ROOT / "memory" / "research.db"

# --- E1: canonical entities DDL ----------------------------------------------
# The live entities table was built out-of-band and grew via ALTER TABLE ADD
# COLUMN; the name-suffix CHECK existed only in the .db file + doc/design/db_schema.md,
# never in source. This is the single canonical definition — a from-scratch
# rebuild now reproduces the production schema exactly (verified against the
# live DB: 8 columns, same order, same CHECK). CREATE TABLE IF NOT EXISTS
# makes migrate() a no-op on the existing table.
#
# Bundle C2 (2026-07-28): market_cap and index_membership were DROPPED.
# market_cap was a stale TEXT column disagreeing with the market_cap/* tag
# for 126 companies (the tag is the source of truth, derived from note YAML
# via sync_tags.py E5a logic). index_membership was 99.4% empty. Both are
# sourced from entity_tags on read now (see market_cap_sql() in db.py).
ENTITIES_DDL = """
CREATE TABLE IF NOT EXISTS entities (
    name                  TEXT PRIMARY KEY NOT NULL,
    entity_type           TEXT NOT NULL,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_path             TEXT,
    last_updated          DATETIME,
    normalized_name       TEXT,
    sector_classification TEXT,
    ticker                TEXT,
    -- Bundle M4: the company-name-suffix guard (reject 'Foo Pvt Ltd' /
    -- 'Foo Private Limited' etc.) is scoped to entity_type='company' only.
    -- It was a blanket CHECK that wrongly rejected legitimate taxonomy
    -- names containing 'Private' — e.g. the 'Private_Sector' sub_sector
    -- under Banking. Sectors/super_sectors/sub_sectors are curated and
    -- don't carry the Ltd/Pvt suffix noise the guard exists to catch.
    -- Placed last (standard table-constraint position) so SQLite parses it
    -- as a table-level CHECK, not an inline column constraint.
    CHECK (entity_type != 'company'
           OR (name NOT LIKE '%Limited'
               AND name NOT LIKE '%Ltd'
               AND name NOT LIKE '%Ltd.'
               AND name NOT LIKE '%Pvt%'
               AND name NOT LIKE '%Private%'))
);
"""

# The four production indexes on entities (in addition to the NOCASE resolver
# index below). These had no source DDL either; centralizing them here so a
# fresh build is index-complete without a manual step.
ENTITIES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_entities_sector_classification "
    "ON entities(sector_classification);",
    "CREATE INDEX IF NOT EXISTS idx_entities_normalized_name ON entities(normalized_name);",
    "CREATE INDEX IF NOT EXISTS idx_entities_entity_type ON entities(entity_type);",
    # Bundle Q1: file_path lookup is the hot path for /api/entity/<path>
    # and derive_co_mentions. Without this index both did a full SCAN of
    # entities (verified via EXPLAIN QUERY PLAN). The OR-form query in
    # api_entity_detail is also rewritten to a UNION of two indexed SELECTs.
    "CREATE INDEX IF NOT EXISTS idx_entities_file_path ON entities(file_path);",
]

GRAPH_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS graph_edges (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL
                  REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    target      TEXT NOT NULL
                  REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    edge_type   TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    properties  TEXT NOT NULL DEFAULT '{}',
    valid_from  DATE,
    valid_to    DATE,
    source_ref  TEXT NOT NULL,
    symmetric   INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target),
    CHECK (json_valid(properties))
);
"""

GRAPH_EDGES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ge_type_idx   ON graph_edges(edge_type);",
    # NOTE: no ge_source_idx. A standalone index on graph_edges(source) is
    # redundant with sqlite_autoindex_graph_edges_1 — the UNIQUE(source,target,
    # edge_type) constraint's auto-index, which leads with `source` in BINARY
    # collation. EXPLAIN QUERY PLAN confirms the planner uses the auto-index for
    # a source-only filter whether or not ge_source_idx exists (verified against
    # memory/research.db, 2026-08-04). Keeping it just doubled the write cost on
    # every edge insert. If you ever need a NON-BINARY source index (e.g.
    # COLLATE NOCASE), that would be a genuinely different index and should be
    # added explicitly — but none of the hot queries need it today.
    "CREATE INDEX IF NOT EXISTS ge_target_idx ON graph_edges(target);",
    "CREATE INDEX IF NOT EXISTS ge_valid_idx  ON graph_edges(valid_from, valid_to);",
]

# D7 — temporal spine. Events are TIMESTAMPED HAPPENINGS (acquisitions, JVs,
# guidance, management changes) that give every company a timeline. They are
# instances (many per company), so they are a typed TABLE, not graph vertices
# (mirroring graph_analytics: a derived analytic store FK-> entities, never
# mirrored to DuckDB). Today only `acquired` carries valid_from; this table
# generalises the date to every event type and adds guidance/management_change,
# which have no representation at all. The `as_of_edition` column is the
# newsletter-edition provenance the roadmap proposed. FK CASCADE matches the
# siblings (events auto-vanish when an entity is deleted).
EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY,
    entity         TEXT NOT NULL
                     REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    event_type     TEXT NOT NULL,        -- acquisition|jv|guidance|management_change
    event_date     DATE,                 -- normalized + sortable (YYYY-MM-DD); nullable
    period         TEXT,                 -- raw token preserved: "FY27","Q1FY26","Mar 2026"
    date_precision TEXT,                 -- day|month|quarter|year|none (granularity of event_date)
    magnitude      TEXT,                 -- "Rs 708 cr AUM" | "10-12%" | "58.96% stake"
    counterparty   TEXT,                 -- "Akzo Nobel India" (acq/jv); NULL for guidance/mgmt
    source_quote   TEXT,                 -- verbatim audit trail (provenance)
    as_of_edition  TEXT,                 -- sourcing newsletter edition
    source_ref     TEXT NOT NULL,        -- "derive:events:..." | "manual:..." | "migration:..."
    properties     TEXT NOT NULL DEFAULT '{}',
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (json_valid(properties))
);
"""

EVENTS_INDEXES = [
    # Timeline query: WHERE entity=? [AND event_type=?] ORDER BY event_date.
    "CREATE INDEX IF NOT EXISTS idx_events_entity_type ON events(entity, event_type);",
    "CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);",
]

# Case-insensitive index on entities.name. Every /api/graph/<name> request
# resolves the path segment via `WHERE name = ? COLLATE NOCASE`; without this
# index SQLite falls back to a SCAN of the entities PRIMARY KEY (verified via
# EXPLAIN QUERY PLAN). With it, the resolver becomes a SEARCH. Idempotent.
ENTITIES_NAME_NOCASE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_entities_name_nocase ON entities(name COLLATE NOCASE);"
)

# Quotes — verbatim executive quotes lifted from newsletter concall sections.
# The body of each company's `## [Concall]` block carries paraphrase + quote +
# `— Name, Title` attribution units; `parse_newsletter.py` only reads the
# heading line and defers all body extraction to a manual Stage 4. This table
# is the deterministic capture layer: the `derive_insights.py` pass walks each
# concall section, extracts every quote with its speaker + paraphrase, and
# writes it here. Speakers are plain string attributes (NOT entities) — this
# honors the D6 deferral (free-text rosters aren't structured enough to be
# first-class person nodes); a quote row is the cheaper attribution carrier.
# FK CASCADE matches the siblings. source_ref prefix `derive:quotes:` is the
# DELETE-then-INSERT idempotency key (mirror of derive_events.py).
QUOTES_DDL = """
CREATE TABLE IF NOT EXISTS quotes (
    id            INTEGER PRIMARY KEY,
    entity        TEXT NOT NULL
                     REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    quote_text    TEXT NOT NULL,
    paraphrase    TEXT,
    speaker_name  TEXT,
    speaker_title TEXT,
    as_of_edition TEXT,                 -- edition_title the quote appeared in
    source_ref    TEXT NOT NULL,        -- "derive:quotes:<newsletter_stem>:<line>"
    properties    TEXT NOT NULL DEFAULT '{}',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity, quote_text, as_of_edition),
    CHECK (json_valid(properties))
);
"""

QUOTES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_quotes_entity_edition ON quotes(entity, as_of_edition);",
    "CREATE INDEX IF NOT EXISTS idx_quotes_speaker ON quotes(speaker_name);",
]

# Company metrics — financial magnitudes (₹/%/bps/$bn/GW) lifted from concall
# prose. The newsletters are dense with figures ("₹2,75,972 crore revenue",
# "140-150 bps EBITDA expansion", "$400 mn bond"); before this table they
# surfaced only as guidance-event `%` or acquisition deal-value. This is the
# narrow capture arm of D1 (the deferred full metrics/time-series layer): it
# stores the figure + its verbatim provenance + a best-effort metric_label,
# but does NOT build the cross-edition tracking view D1 envisioned. The
# newsletters are the recurring source that made D1's deferral rationale moot.
COMPANY_METRICS_DDL = """
CREATE TABLE IF NOT EXISTS company_metrics (
    id            INTEGER PRIMARY KEY,
    entity        TEXT NOT NULL
                     REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    metric_label  TEXT,                 -- revenue|ebitda_margin|capex|aum|growth|...
    value_raw     TEXT NOT NULL,        -- "₹2,75,972 crore" | "140-150 bps" | "10%"
    value_num     REAL,                 -- parsed numeric (range lower bound)
    unit          TEXT,                 -- crore|lakh|bps|percent|bn_usd|gw|mw|x
    period        TEXT,                 -- "Q1 FY27" | "FY28" | "full year" (best-effort)
    as_of_edition TEXT,
    source_quote  TEXT,                 -- verbatim line it came from (provenance)
    source_ref    TEXT NOT NULL,        -- "derive:metrics:<newsletter_stem>:<line>"
    properties    TEXT NOT NULL DEFAULT '{}',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (json_valid(properties))
);
"""

COMPANY_METRICS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_metrics_entity_label ON company_metrics(entity, metric_label);",
    "CREATE INDEX IF NOT EXISTS idx_metrics_edition ON company_metrics(as_of_edition);",
]

GRAPH_ANALYTICS_DDL = """
CREATE TABLE IF NOT EXISTS graph_analytics (
    entity_name TEXT NOT NULL
                  REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    metric      TEXT NOT NULL,
    value       TEXT NOT NULL,
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- PK column order is metric-first (Bundle P3, 2026-07-27). Every hot query
    -- is `WHERE metric = ? [ORDER BY entity_name]` (/api/graph/metrics/<metric>,
    -- /api/graph/analytics/<metric>); metric-first turns a full SCAN into a
    -- prefix SEARCH and satisfies the ORDER BY for free. No query filters by
    -- entity_name alone (the resolver filters by entity first, then metric),
    -- so the previous (entity_name, metric) order served no access pattern.
    -- Safe to reverse: all consumers use named-column access; the only upsert
    -- is INSERT OR REPLACE (column-order-agnostic); DuckDB never reads this
    -- table. The live DB is brought into conformance by rebuild_schema.py.
    PRIMARY KEY (metric, entity_name)
);
"""

RELATIONS_VIEW_DDL = """
CREATE VIEW IF NOT EXISTS relations AS
    SELECT source, target, edge_type AS relation_type
    FROM graph_edges
"""


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _view_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _object_kind(conn, name: str) -> str | None:
    row = conn.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def migrate(verbose: bool = True) -> dict:  # noqa: C901
    """Run the migration. Returns a stats dict."""
    conn = connect(DB_PATH)
    stats: dict = {}
    try:
        conn.execute("BEGIN")
        n_relations_before = (
            conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
            if _table_exists(conn, "relations")
            else 0
        )

        # 0. entities table + production indexes. Must precede graph_edges and
        #    graph_analytics (both FK-reference entities(name)). IF NOT EXISTS
        #    makes this a no-op on the live DB; the value is on fresh builds /
        #    rebuilds (Bundle E1).
        conn.execute(ENTITIES_DDL)
        for idx in ENTITIES_INDEXES:
            conn.execute(idx)

        # 1. graph_edges table
        conn.execute(GRAPH_EDGES_DDL)
        for idx in GRAPH_EDGES_INDEXES:
            conn.execute(idx)

        # 1b. Case-insensitive index on entities.name so the COLLATE NOCASE
        #     resolver used by every /api/graph/<name> request searches instead
        #     of scans. (Bundle C2.)
        conn.execute(ENTITIES_NAME_NOCASE_INDEX)

        # 2. graph_analytics table
        conn.execute(GRAPH_ANALYTICS_DDL)

        # 2b. events table (D7 — temporal spine). IF NOT EXISTS makes this a
        #     no-op on existing DBs; the value is on fresh builds. FK->entities.
        conn.execute(EVENTS_DDL)
        for idx in EVENTS_INDEXES:
            conn.execute(idx)

        # 2c. quotes + company_metrics (derive_insights capture layer). Both
        #     IF NOT EXISTS -> no-op on existing DBs; created on fresh builds
        #     or when migrate() runs against a pre-quotes DB. FK->entities.
        conn.execute(QUOTES_DDL)
        for idx in QUOTES_INDEXES:
            conn.execute(idx)
        conn.execute(COMPANY_METRICS_DDL)
        for idx in COMPANY_METRICS_INDEXES:
            conn.execute(idx)

        # 3. Backfill from legacy relations table (if it still exists as a table)
        backfilled = 0
        if _table_exists(conn, "relations"):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO graph_edges
                    (source, target, edge_type, weight, properties, source_ref, symmetric)
                SELECT source, target, relation_type, 1.0, '{}',
                       'migration:relations', 0
                FROM relations
                """
            )
            backfilled = cur.rowcount or 0

        # 4. Replace legacy relations TABLE with a VIEW (idempotent)
        #    Drop the table only if it still exists as a table.
        if _table_exists(conn, "relations"):
            conn.execute("DROP TABLE relations")
        conn.execute("DROP VIEW IF EXISTS relations")
        conn.execute(RELATIONS_VIEW_DDL)

        conn.commit()

        # 5. Stats
        stats["relations_rows_before"] = n_relations_before
        stats["graph_edges_backfilled"] = backfilled
        stats["graph_edges_total"] = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        stats["relations_view_rows"] = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        stats["object_kind_relations"] = _object_kind(conn, "relations")
        stats["object_kind_graph_edges"] = _object_kind(conn, "graph_edges")
        stats["object_kind_graph_analytics"] = _object_kind(conn, "graph_analytics")
        stats["object_kind_events"] = _object_kind(conn, "events")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    if verbose:
        print("== relations → graph_edges migration ==")
        for k, v in stats.items():
            print(f"  {k:32} {v}")
    return stats


if __name__ == "__main__":
    migrate()
