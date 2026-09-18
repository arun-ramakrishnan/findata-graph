PRAGMA foreign_keys=OFF;

CREATE VIRTUAL TABLE note_search USING fts5(doc_type, file_path UNINDEXED, title, sector, content, embedding UNINDEXED, section_title, anchor UNINDEXED, tokenize = 'porter unicode61');

CREATE TABLE entity_tags (
                    entity_name TEXT NOT NULL,
                    tag         TEXT NOT NULL,
                    PRIMARY KEY (entity_name, tag),
                    FOREIGN KEY (entity_name) REFERENCES entities(name)
                        ON DELETE CASCADE ON UPDATE CASCADE
                );

CREATE TABLE "entities" (
    name                  TEXT PRIMARY KEY NOT NULL,
    entity_type           TEXT NOT NULL,
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_path             TEXT,
    last_updated          DATETIME,
    normalized_name       TEXT,
    sector_classification TEXT,
    ticker                TEXT, cin CHAR(21), cin_listing CHAR(1), cin_nic5 CHAR(5), cin_state CHAR(2), cin_year SMALLINT, cin_ownership CHAR(3),
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

CREATE TABLE "graph_edges" (
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
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, agent_id TEXT REFERENCES provenance_agents(agent_id) ON UPDATE CASCADE ON DELETE SET NULL, source_tier TEXT CHECK (source_tier IN ('manual','migration','derive','regulator','external')),
    UNIQUE(source, target, edge_type),
    CHECK (source != target),
    CHECK (json_valid(properties))
);

CREATE TABLE "graph_analytics" (
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

CREATE TABLE events (
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
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, counterparty_entity TEXT REFERENCES entities(name) ON UPDATE CASCADE ON DELETE SET NULL, agent_id TEXT REFERENCES provenance_agents(agent_id) ON UPDATE CASCADE ON DELETE SET NULL, source_tier TEXT CHECK (source_tier IN ('manual','migration','derive','regulator','external')),
    CHECK (json_valid(properties))
);

CREATE TABLE quotes (
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
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, agent_id TEXT REFERENCES provenance_agents(agent_id) ON UPDATE CASCADE ON DELETE SET NULL, source_tier TEXT CHECK (source_tier IN ('manual','migration','derive','regulator','external')),
    UNIQUE(entity, quote_text, as_of_edition),
    CHECK (json_valid(properties))
);

CREATE TABLE company_metrics (
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
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, agent_id TEXT REFERENCES provenance_agents(agent_id) ON UPDATE CASCADE ON DELETE SET NULL, source_tier TEXT CHECK (source_tier IN ('manual','migration','derive','regulator','external')),
    CHECK (json_valid(properties))
);

CREATE TABLE db_meta ( key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE note_tags (
                note_path TEXT NOT NULL,
                tag       TEXT NOT NULL,
                PRIMARY KEY (note_path, tag)
            );

CREATE TABLE entity_gf_map (
    entity_name TEXT PRIMARY KEY,
    gf_slug TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('yahoo_mapped_back', 'gf_only')),
    resolved_at TEXT NOT NULL,
    verified_name TEXT NOT NULL
);

CREATE TABLE entity_ticker_status (
    entity_name TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('delisted', 'amalgamated')),
    successor TEXT,
    decided_at TEXT NOT NULL
);

CREATE TABLE hyper_edges (
    id          INTEGER PRIMARY KEY,
    edge_type   TEXT NOT NULL,        -- sector|theme|country|group|edition|...
    label       TEXT NOT NULL,        -- the hyperedge's own name (category/edition/group)
    weight      REAL NOT NULL DEFAULT 1.0,
    valid_from  DATE,
    valid_to    DATE,
    source_ref  TEXT NOT NULL,
    properties  TEXT NOT NULL DEFAULT '{}',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, agent_id TEXT REFERENCES provenance_agents(agent_id) ON UPDATE CASCADE ON DELETE SET NULL, source_tier TEXT CHECK (source_tier IN ('manual','migration','derive','regulator','external')),
    UNIQUE(edge_type, label),
    CHECK (json_valid(properties))
);

CREATE TABLE hyper_incidences (
    edge_id     INTEGER NOT NULL
                  REFERENCES hyper_edges(id) ON DELETE CASCADE ON UPDATE CASCADE,
    entity_name TEXT NOT NULL
                  REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    weight      REAL,                  -- per-incidence weight (HIF granularity 2)
    direction   TEXT, role TEXT, valid_from DATE, valid_to DATE,                  -- 'head'|'tail' for directed hypergraphs; NULL = undirected
    PRIMARY KEY (edge_id, entity_name),
    CHECK (direction IS NULL OR direction IN ('head', 'tail'))
);

CREATE TABLE provenance_agents (
            agent_id  TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            version   TEXT NOT NULL,
            repo_ref  TEXT,
            script    TEXT,
            command   TEXT,
            as_of     TEXT NOT NULL
        );

CREATE TABLE concept_schemes (
            scheme_id    TEXT PRIMARY KEY,
            label        TEXT NOT NULL,
            scheme_type  TEXT NOT NULL,
            version      TEXT NOT NULL,
            source_uri   TEXT,
            license      TEXT,
            attribution  TEXT,
            active       INTEGER NOT NULL DEFAULT 1
        );

CREATE TABLE concepts (
            concept_id   TEXT PRIMARY KEY,
            scheme_id    TEXT NOT NULL REFERENCES concept_schemes(scheme_id),
            concept_code TEXT NOT NULL,
            pref_label   TEXT NOT NULL,
            alt_label    TEXT,
            notation     TEXT,
            broader_id   TEXT REFERENCES concepts(concept_id),
            scope_note   TEXT,
            source_ref   TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('candidate', 'active', 'superseded')),
            UNIQUE (scheme_id, concept_code)
        );

CREATE TABLE concept_mappings (
            source_scheme  TEXT NOT NULL,
            source_concept TEXT NOT NULL,
            target_scheme  TEXT NOT NULL,
            target_concept TEXT NOT NULL,
            match_type     TEXT NOT NULL CHECK (match_type IN
                             ('exactMatch', 'closeMatch', 'broadMatch', 'narrowMatch')),
            source_ref     TEXT NOT NULL,
            version        TEXT NOT NULL
        , status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('candidate', 'active', 'superseded')));

CREATE TABLE entity_identifiers (
        entity_name      TEXT NOT NULL REFERENCES entities(name)
                           ON DELETE CASCADE ON UPDATE CASCADE,
        identifier_type  TEXT NOT NULL CHECK (identifier_type IN
                           ('cin', 'lei', 'cik', 'isin', 'llpin', 'alias')),
        identifier_value TEXT NOT NULL,
        namespace        TEXT,
        valid_from       TEXT,
        valid_to         TEXT,
        source_ref       TEXT NOT NULL,
        created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_updated     DATETIME,
        PRIMARY KEY (entity_name, identifier_type, identifier_value),
        UNIQUE (identifier_type, identifier_value)
    );

CREATE TABLE company_embeddings (
            company_name TEXT PRIMARY KEY,
            embedding    BLOB NOT NULL,
            model        TEXT NOT NULL,
            created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
            CHECK (length(embedding) = 384 * 4)
        );

CREATE TABLE nic2008 (
  subclass    CHAR(5) PRIMARY KEY,  -- '01111'
  class       CHAR(4) NOT NULL,     -- '0111' (== ISIC Rev.4 class)
  grp         CHAR(3) NOT NULL,     -- '011' ('group' is reserved)
  division    CHAR(2) NOT NULL,     -- '01'
  section     CHAR(1) NOT NULL,     -- 'A'..'U'
  description TEXT NOT NULL,        -- 'Growing of wheat'
  isic4       CHAR(4) NOT NULL,     -- == class here
  nace21      TEXT,                 -- reserved, filled op-paced
  gics        TEXT,                 -- opaque peer code, never our own
  wikidata    TEXT,                 -- QID for the closeMatch hub
  scope_note  TEXT,                 -- PDF inclusion/exclusions (class-grain)
  version     TEXT NOT NULL DEFAULT 'NIC-2008'
);

CREATE INDEX idx_entity_tags_tag ON entity_tags(tag);

CREATE INDEX idx_entities_sector_classification ON entities(sector_classification);

CREATE INDEX idx_entities_normalized_name ON entities(normalized_name);

CREATE INDEX idx_entities_entity_type ON entities(entity_type);

CREATE INDEX idx_entities_file_path ON entities(file_path);

CREATE INDEX ge_type_idx   ON graph_edges(edge_type);

CREATE INDEX ge_target_idx ON graph_edges(target);

CREATE INDEX ge_valid_idx  ON graph_edges(valid_from, valid_to);

CREATE INDEX idx_events_entity_type ON events(entity, event_type);

CREATE INDEX idx_events_date ON events(event_date);

CREATE INDEX idx_events_type ON events(event_type);

CREATE INDEX idx_entities_name_nocase ON entities(name COLLATE NOCASE);

CREATE INDEX idx_quotes_entity_edition ON quotes(entity, as_of_edition);

CREATE INDEX idx_quotes_speaker ON quotes(speaker_name);

CREATE INDEX idx_metrics_entity_label ON company_metrics(entity, metric_label);

CREATE INDEX idx_metrics_edition ON company_metrics(as_of_edition);

CREATE INDEX idx_note_tags_tag ON note_tags(tag);

CREATE INDEX he_type_idx ON hyper_edges(edge_type);

CREATE INDEX hi_entity_idx ON hyper_incidences(entity_name);

CREATE INDEX idx_emb_company ON company_embeddings(company_name);

CREATE VIEW relations AS
    SELECT source, target, edge_type AS relation_type
    FROM graph_edges;

CREATE TRIGGER trg_entities_insert_gen AFTER INSERT ON entities BEGIN UPDATE db_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='generation'; END;

CREATE TRIGGER trg_entities_delete_gen AFTER DELETE ON entities BEGIN UPDATE db_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='generation'; END;

CREATE TRIGGER trg_entities_update_gen AFTER UPDATE ON entities BEGIN UPDATE db_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='generation'; END;

CREATE TRIGGER trg_graph_edges_insert_gen AFTER INSERT ON graph_edges BEGIN UPDATE db_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='generation'; END;

CREATE TRIGGER trg_graph_edges_delete_gen AFTER DELETE ON graph_edges BEGIN UPDATE db_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='generation'; END;

CREATE TRIGGER trg_graph_edges_update_gen AFTER UPDATE ON graph_edges BEGIN UPDATE db_meta SET value = CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='generation'; END;
