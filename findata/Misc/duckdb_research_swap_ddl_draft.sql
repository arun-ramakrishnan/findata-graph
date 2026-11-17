-- DRAFT DDL (duckdb_research_swap_steps.md Step 2, 2026-11-17)
-- generated from memory/research.db sqlite_master; NOT LIVE — do NOT
-- wire into schema/duckdb/ or make schema-dump (test_schema_drift gate).
-- Type map: INTEGER->BIGINT, TEXT->VARCHAR, REAL->DOUBLE, BLOB stays,
-- DATETIME->TIMESTAMP, DATE->DATE, CHAR(n)->VARCHAR, JSON-as-text stays.
-- FK actions LOWERED to RESTRICT (DuckDB parse-rejects CASCADE/SET NULL);
-- original actions live in duckdb_research_swap_cascade_manifest.json.
-- entities.created_at rowid 746 is malformed (Step 1 finding) — fix
-- before any load of this DDL (TIMESTAMP column otherwise rejects it).

CREATE TABLE "entities" (
  "name" VARCHAR NOT NULL,
  "entity_type" VARCHAR,
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  "file_path" VARCHAR,
  "last_updated" TIMESTAMP,
  "normalized_name" VARCHAR,
  "sector_classification" VARCHAR,
  "ticker" VARCHAR,
  "cin" VARCHAR,
  "cin_listing" VARCHAR,
  "cin_nic5" VARCHAR,
  "cin_state" VARCHAR,
  "cin_year" SMALLINT,
  "cin_ownership" VARCHAR,
  PRIMARY KEY ("name")
);
CREATE TABLE "co_mention_editions" (
  "edition_title" VARCHAR NOT NULL,
  "entity_name" VARCHAR NOT NULL,
  "newsletter" VARCHAR,
  "source_ref" VARCHAR,
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("edition_title", "entity_name"),
    FOREIGN KEY ("entity_name") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "company_embeddings" (
  "company_name" VARCHAR,
  "embedding" BLOB,
  "model" VARCHAR,
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("company_name")
);
CREATE TABLE "provenance_agents" (
  "agent_id" VARCHAR,
  "name" VARCHAR,
  "version" VARCHAR,
  "repo_ref" VARCHAR,
  "script" VARCHAR,
  "command" VARCHAR,
  "as_of" VARCHAR,
  PRIMARY KEY ("agent_id")
);
CREATE TABLE "company_metrics" (
  "id" BIGINT,
  "entity" VARCHAR,
  "metric_label" VARCHAR,
  "value_raw" VARCHAR,
  "value_num" DOUBLE,
  "unit" VARCHAR,
  "period" VARCHAR,
  "as_of_edition" VARCHAR,
  "source_quote" VARCHAR,
  "source_ref" VARCHAR,
  "properties" VARCHAR DEFAULT '''{}''',
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  "agent_id" VARCHAR,
  "source_tier" VARCHAR,
  "consolidated" BIGINT,
  PRIMARY KEY ("id"),
    FOREIGN KEY ("agent_id") REFERENCES "provenance_agents" ("agent_id") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("entity") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "competitive_overlap" (
  "a" VARCHAR NOT NULL,
  "b" VARCHAR NOT NULL,
  "c" VARCHAR NOT NULL,
  "computed_at" TIMESTAMP,
  PRIMARY KEY ("a", "b", "c")
);
CREATE TABLE "concept_mappings" (
  "source_scheme" VARCHAR,
  "source_concept" VARCHAR,
  "target_scheme" VARCHAR,
  "target_concept" VARCHAR,
  "match_type" VARCHAR,
  "source_ref" VARCHAR,
  "version" VARCHAR,
  "status" VARCHAR DEFAULT '''active'''
);
CREATE TABLE "concept_schemes" (
  "scheme_id" VARCHAR,
  "label" VARCHAR,
  "scheme_type" VARCHAR,
  "version" VARCHAR,
  "source_uri" VARCHAR,
  "license" VARCHAR,
  "attribution" VARCHAR,
  "active" BIGINT DEFAULT '1',
  PRIMARY KEY ("scheme_id")
);
CREATE TABLE "concepts" (
  "concept_id" VARCHAR,
  "scheme_id" VARCHAR,
  "concept_code" VARCHAR,
  "pref_label" VARCHAR,
  "alt_label" VARCHAR,
  "notation" VARCHAR,
  "broader_id" VARCHAR,
  "scope_note" VARCHAR,
  "source_ref" VARCHAR,
  "status" VARCHAR DEFAULT '''active''',
  PRIMARY KEY ("concept_id"),
    FOREIGN KEY ("broader_id") REFERENCES "concepts" ("concept_id") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("scheme_id") REFERENCES "concept_schemes" ("scheme_id") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "db_bump_log" (
  "ts" VARCHAR,
  "caller" VARCHAR,
  "worktree" VARCHAR,
  "generation" BIGINT
);
CREATE TABLE "db_meta" (
  "key" VARCHAR,
  "value" VARCHAR,
  PRIMARY KEY ("key")
);
CREATE TABLE "derived_cohort_meta" (
  "table_name" VARCHAR,
  "mode" VARCHAR,
  "entities_fingerprint" VARCHAR,
  "stamped_at" TIMESTAMP,
  PRIMARY KEY ("table_name")
);
CREATE TABLE "entity_gf_map" (
  "entity_name" VARCHAR,
  "gf_slug" VARCHAR,
  "kind" VARCHAR,
  "resolved_at" VARCHAR,
  "verified_name" VARCHAR,
  PRIMARY KEY ("entity_name")
);
CREATE TABLE "entity_identifiers" (
  "entity_name" VARCHAR NOT NULL,
  "identifier_type" VARCHAR NOT NULL,
  "identifier_value" VARCHAR NOT NULL,
  "namespace" VARCHAR,
  "valid_from" VARCHAR,
  "valid_to" VARCHAR,
  "source_ref" VARCHAR,
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  "last_updated" TIMESTAMP,
  PRIMARY KEY ("entity_name", "identifier_type", "identifier_value"),
    FOREIGN KEY ("entity_name") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "entity_tags" (
  "entity_name" VARCHAR NOT NULL,
  "tag" VARCHAR NOT NULL,
  PRIMARY KEY ("entity_name", "tag"),
    FOREIGN KEY ("entity_name") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "entity_ticker_status" (
  "entity_name" VARCHAR,
  "status" VARCHAR,
  "successor" VARCHAR,
  "decided_at" VARCHAR,
  PRIMARY KEY ("entity_name")
);
CREATE TABLE "events" (
  "id" BIGINT,
  "entity" VARCHAR,
  "event_type" VARCHAR,
  "event_date" DATE,
  "period" VARCHAR,
  "date_precision" VARCHAR,
  "magnitude" VARCHAR,
  "counterparty" VARCHAR,
  "source_quote" VARCHAR,
  "as_of_edition" VARCHAR,
  "source_ref" VARCHAR,
  "properties" VARCHAR DEFAULT '''{}''',
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  "counterparty_entity" VARCHAR,
  "agent_id" VARCHAR,
  "source_tier" VARCHAR,
  PRIMARY KEY ("id"),
    FOREIGN KEY ("agent_id") REFERENCES "provenance_agents" ("agent_id") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("counterparty_entity") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("entity") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "event_participants" (
  "event_id" BIGINT NOT NULL,
  "participant" VARCHAR NOT NULL,
  "role" VARCHAR NOT NULL,
  "source_ref" VARCHAR,
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("event_id", "participant", "role"),
    FOREIGN KEY ("participant") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("event_id") REFERENCES "events" ("id") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "graph_analytics" (
  "entity_name" VARCHAR NOT NULL,
  "metric" VARCHAR NOT NULL,
  "value" VARCHAR,
  "computed_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("metric", "entity_name"),
    FOREIGN KEY ("entity_name") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "graph_analytics_contract" (
  "metric" VARCHAR NOT NULL,
  "entity_name" VARCHAR NOT NULL,
  "seq" BIGINT,
  "origin" VARCHAR DEFAULT '''seed''',
  "seeded_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("metric", "entity_name"),
    FOREIGN KEY ("entity_name") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "graph_edges" (
  "id" BIGINT,
  "source" VARCHAR,
  "target" VARCHAR,
  "edge_type" VARCHAR,
  "weight" DOUBLE DEFAULT '1.0',
  "properties" VARCHAR DEFAULT '''{}''',
  "valid_from" DATE,
  "valid_to" DATE,
  "source_ref" VARCHAR,
  "symmetric" BIGINT DEFAULT '0',
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  "agent_id" VARCHAR,
  "source_tier" VARCHAR,
  PRIMARY KEY ("id"),
    FOREIGN KEY ("agent_id") REFERENCES "provenance_agents" ("agent_id") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("target") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("source") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "hidden_connections" (
  "a" VARCHAR NOT NULL,
  "b" VARCHAR NOT NULL,
  "distance" BIGINT,
  "shared_count" BIGINT,
  "intermediaries" VARCHAR,
  "computed_at" TIMESTAMP,
  PRIMARY KEY ("a", "b")
);
CREATE TABLE "hyper_edges" (
  "id" BIGINT,
  "edge_type" VARCHAR,
  "label" VARCHAR,
  "weight" DOUBLE DEFAULT '1.0',
  "valid_from" DATE,
  "valid_to" DATE,
  "source_ref" VARCHAR,
  "properties" VARCHAR DEFAULT '''{}''',
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  "agent_id" VARCHAR,
  "source_tier" VARCHAR,
  PRIMARY KEY ("id"),
    FOREIGN KEY ("agent_id") REFERENCES "provenance_agents" ("agent_id") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "hyper_incidences" (
  "edge_id" BIGINT NOT NULL,
  "entity_name" VARCHAR NOT NULL,
  "weight" DOUBLE,
  "direction" VARCHAR,
  "role" VARCHAR,
  "valid_from" DATE,
  "valid_to" DATE,
  PRIMARY KEY ("edge_id", "entity_name"),
    FOREIGN KEY ("entity_name") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("edge_id") REFERENCES "hyper_edges" ("id") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "nic2008" (
  "subclass" VARCHAR,
  "class" VARCHAR,
  "grp" VARCHAR,
  "division" VARCHAR,
  "section" VARCHAR,
  "description" VARCHAR,
  "isic4" VARCHAR,
  "nace21" VARCHAR,
  "gics" VARCHAR,
  "wikidata" VARCHAR,
  "scope_note" VARCHAR,
  "version" VARCHAR DEFAULT '''NIC-2008''',
  PRIMARY KEY ("subclass")
);
-- note_search: FTS5 VIRTUAL shadow (rollback artifact; mirrored as plain table)
CREATE TABLE "note_search" (
  "doc_type" VARCHAR,
  "file_path" VARCHAR,
  "title" VARCHAR,
  "sector" VARCHAR,
  "content" VARCHAR,
  "embedding" VARCHAR,
  "section_title" VARCHAR,
  "anchor" VARCHAR,
  "ticker" VARCHAR,
  "edge_context" VARCHAR
);
CREATE TABLE "note_search_config" (
  "k" VARCHAR NOT NULL,
  "v" VARCHAR,
  PRIMARY KEY ("k")
);
CREATE TABLE "note_search_content" (
  "id" BIGINT,
  "c0" VARCHAR,
  "c1" VARCHAR,
  "c2" VARCHAR,
  "c3" VARCHAR,
  "c4" VARCHAR,
  "c5" VARCHAR,
  "c6" VARCHAR,
  "c7" VARCHAR,
  "c8" VARCHAR,
  "c9" VARCHAR,
  PRIMARY KEY ("id")
);
CREATE TABLE "note_search_data" (
  "id" BIGINT,
  "block" BLOB,
  PRIMARY KEY ("id")
);
CREATE TABLE "note_search_docsize" (
  "id" BIGINT,
  "sz" BLOB,
  PRIMARY KEY ("id")
);
CREATE TABLE "note_search_idx" (
  "segid" VARCHAR NOT NULL,
  "term" VARCHAR NOT NULL,
  "pgno" VARCHAR,
  PRIMARY KEY ("segid", "term")
);
CREATE TABLE "note_search_meta" (
  "file_path" VARCHAR,
  "mtime" DOUBLE,
  "content_hash" VARCHAR,
  PRIMARY KEY ("file_path")
);
CREATE TABLE "note_tags" (
  "note_path" VARCHAR NOT NULL,
  "tag" VARCHAR NOT NULL,
  PRIMARY KEY ("note_path", "tag")
);
CREATE TABLE "ppr_results" (
  "seed" VARCHAR NOT NULL,
  "entity" VARCHAR NOT NULL,
  "score" DOUBLE,
  "alpha" DOUBLE DEFAULT '0.85',
  "computed_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("seed", "entity"),
    FOREIGN KEY ("entity") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "quotes" (
  "id" BIGINT,
  "entity" VARCHAR,
  "quote_text" VARCHAR,
  "paraphrase" VARCHAR,
  "speaker_name" VARCHAR,
  "speaker_title" VARCHAR,
  "as_of_edition" VARCHAR,
  "source_ref" VARCHAR,
  "properties" VARCHAR DEFAULT '''{}''',
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  "agent_id" VARCHAR,
  "source_tier" VARCHAR,
  PRIMARY KEY ("id"),
    FOREIGN KEY ("agent_id") REFERENCES "provenance_agents" ("agent_id") ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY ("entity") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "speaker_identity" (
  "alias_name" VARCHAR NOT NULL,
  "canonical_name" VARCHAR,
  "person_name" VARCHAR,
  "source_ref" VARCHAR,
  "created_at" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY ("alias_name"),
    FOREIGN KEY ("person_name") REFERENCES "entities" ("name") ON DELETE RESTRICT ON UPDATE RESTRICT
);
CREATE TABLE "supply_chokepoints" (
  "supplier" VARCHAR,
  "competitors_served" BIGINT,
  "competitor_dyads" BIGINT,
  "companies" VARCHAR,
  "computed_at" TIMESTAMP,
  PRIMARY KEY ("supplier")
);
CREATE TABLE "ultimate_parent" (
  "company" VARCHAR,
  "ultimate_parent" VARCHAR,
  "depth" BIGINT,
  "path" VARCHAR,
  "root_kind" VARCHAR,
  "coalition_pct" DOUBLE,
  "computed_at" TIMESTAMP,
  PRIMARY KEY ("company")
);

-- user indexes (23) mapped verbatim:
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
CREATE INDEX idx_entities_name_nocase ON entities(lower(name));
CREATE INDEX idx_quotes_entity_edition ON quotes(entity, as_of_edition);
CREATE INDEX idx_quotes_speaker ON quotes(speaker_name);
CREATE INDEX idx_metrics_entity_label ON company_metrics(entity, metric_label);
CREATE INDEX idx_metrics_edition ON company_metrics(as_of_edition);
CREATE INDEX idx_note_tags_tag ON note_tags(tag);
CREATE INDEX he_type_idx ON hyper_edges(edge_type);
CREATE INDEX hi_entity_idx ON hyper_incidences(entity_name);
CREATE INDEX idx_emb_company ON company_embeddings(company_name);
CREATE INDEX idx_speaker_identity_person ON speaker_identity(person_name);
CREATE INDEX idx_event_participants_participant ON event_participants(participant);
CREATE INDEX idx_ppr_results_seed_rank ON ppr_results(seed, score DESC);
