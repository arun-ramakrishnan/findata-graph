# Database Schema — `memory/research.db`

SQLite source of truth for the FinData knowledge graph, plus the derived
DuckDB read-cache schema (§ "memory/graph.duckdb" below). Architecture and
cache policy: `graph_design.txt`.
Live row counts (2026-08-15): entities 1209, entity_tags 6747,
graph_edges 4110, events 342, quotes 2564, company_metrics 1731,
graph_analytics 14,331, note_search (FTS5) + `relations` VIEW.

## `entities`

One row per entity; `name` is the identity (PRIMARY KEY).

| Column | Type | Notes |
|---|---|---|
| `name` | TEXT PK | CHECK rejects `Ltd`/`Limited`/`Ltd.`/`Pvt`/`Private` suffixes — scoped to `entity_type='company'` only (taxonomy names like `Private_Sector` are exempt; see DDL comment) |
| `entity_type` | TEXT | `company` (1068) \| `sub_sector` (78) \| `sector` (42) \| `theme` (12) \| `super_sector` (9) |
| `created_at` / `last_updated` | DATETIME | last_updated set on every modification |
| `file_path` | TEXT | MUST resolve to an existing file under `findata/` |
| `normalized_name` | TEXT | MUST equal the markdown filename minus `.md`, character-for-character |
| `sector_classification` | TEXT | sector entity name, e.g. `FMCG` |
| `ticker` | TEXT | NSE/BSE symbol e.g. `INFY.NS`; NULL if unlisted |

**Removed columns (tag-only now):** `market_cap` (see `architecture.md` §6
for the 4 buckets + NULL), `index_membership`, `title` (lives in note YAML
only), `enhanced_tags` (replaced by `entity_tags`).

**Indexes:** sector_classification, normalized_name, entity_type,
file_path, name_nocase (NOCASE — the case-insensitive resolver in `app.py`).

## `entity_tags`

Normalized mirror of note YAML `tags:` (the source of truth), rebuilt by
`helpers/core/sync_tags.py` (`make sync-tags`). One row per entity × tag.

| Column | Type | Notes |
|---|---|---|
| `entity_name` | TEXT | PK part; FK → `entities(name)` cascade |
| `tag` | TEXT | PK part, e.g. `sector/healthcare`, `market_cap/large_cap` |

Mirrors only `entity_type/`, `sector/`, `market_cap/`, `subsector/`; the
full tag set stays in the notes. Index: `idx_entity_tags_tag`. Typical
query: JOIN two tag aliases for tag intersection.

## `graph_edges` — canonical edge store

Directed links; supersedes the `relations` table. 4,110 rows across 12
edge types (counts + symmetric convention: `graph_design.txt` §4).
Producers: `parse_newsletter`/`markdown_parse` (membership pair),
`extract_relations.py` (company↔company from prose),
`derive_{co_mentions,themes}.py`, `build_sector_hierarchy.py`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `source` / `target` | TEXT | FK → `entities(name)` cascade |
| `edge_type` | TEXT | 12 values, `graph_design.txt` §4 |
| `weight` | REAL | default 1.0 |
| `properties` | TEXT | JSON; `CHECK (json_valid(...))` |
| `valid_from` / `valid_to` | DATE | temporal window; NULL valid_from = always-valid, NULL valid_to = current |
| `source_ref` | TEXT | provenance: edition / derive script / manual |
| `symmetric` | INTEGER | 1 = undirected semantics (e.g. `co_mentioned_in`) |
| `created_at` | DATETIME | |

**Constraints:** `UNIQUE(source, target, edge_type)`, `CHECK (source != target)`.
**Indexes:** `ge_target_idx`, `ge_type_idx`, `ge_valid_idx`; NO source-only
index — the UNIQUE auto-index leads with `source` and covers it
(EXPLAIN-verified).

## `relations` — backward-compat VIEW

`SELECT source, target, edge_type AS relation_type FROM graph_edges` —
read-only, projects ALL edge types. New code writes `graph_edges`.

## `events` — D7 temporal spine

One row per derived/manual company event. Populated by
`helpers/graph/derive_events.py` (`make derive-events`).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `entity` | TEXT | FK cascade |
| `event_type` | TEXT | `acquisition` \| `jv` \| `guidance` \| `management_change` |
| `event_date` | DATE | normalized YYYY-MM-DD; nullable |
| `period` | TEXT | raw token preserved: `FY27`, `Q1FY26`, `Mar 2026` |
| `date_precision` | TEXT | `day` \| `month` \| `quarter` \| `year` \| `none` |
| `magnitude` | TEXT | `Rs 708 cr AUM` \| `10-12%` \| `58.96% stake` |
| `counterparty` | TEXT | acq/jv party; NULL for guidance/mgmt |
| `source_quote` | TEXT | verbatim audit trail |
| `as_of_edition` | TEXT | sourcing newsletter edition |
| `source_ref` | TEXT | `derive:events:…` \| `manual:…` \| `migration:…` |
| `properties` | TEXT | JSON, json_valid CHECK |
| `created_at` | DATETIME | |

**Indexes:** entity_type, date, type.

## `quotes` — concall quote capture

Verbatim executive quotes from `## [Concall]` blocks, extracted by
`helpers/graph/derive_insights.py` (`make derive-insights`) as
paraphrase → quote → `— Name, Title` units. Speakers are string
attributes, NOT entities (D6 deferral).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `entity` | TEXT | FK cascade |
| `quote_text` | TEXT | verbatim |
| `paraphrase` | TEXT | editor's 1-2 line summary |
| `speaker_name` / `speaker_title` | TEXT | NULL name for anonymous/role-only |
| `as_of_edition` | TEXT | edition_title |
| `source_ref` | TEXT | `derive:quotes:<stem>:<line>` — LIKE sweep = idempotency key |
| `properties` | TEXT | JSON, json_valid CHECK |
| `created_at` | DATETIME | |

**UNIQUE(entity, quote_text, as_of_edition)**. Indexes: `(entity,
as_of_edition)` (timeline query), speaker.

## `company_metrics` — financial magnitude capture

₹/%/bps/$bn/GW figures from concall prose (`derive_insights.py`) — the
narrow capture arm of D1; figure + provenance + best-effort label, no
cross-edition tracking view.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `entity` | TEXT | FK cascade |
| `metric_label` | TEXT | best-effort `revenue` \| `ebitda_margin` \| `capex` \| `aum` \| `growth` \| `deal_value` \| …; NULL if not inferable |
| `value_raw` | TEXT | `₹2,75,972 crore` \| `140-150 bps` |
| `value_num` | REAL | parsed (range lower bound) |
| `unit` | TEXT | `crore` \| `lakh` \| `bps` \| `percent` \| `bn_usd` \| `gw` \| `mw` \| `x` |
| `period` | TEXT | `Q1 FY27` \| `FY28` (best-effort) |
| `as_of_edition` | TEXT | |
| `source_quote` | TEXT | verbatim line (provenance) |
| `source_ref` | TEXT | `derive:metrics:<stem>:<line>` — idempotency key |
| `properties` | TEXT | JSON, json_valid CHECK |
| `created_at` | DATETIME | |

No natural UNIQUE key; idempotency = DELETE-then-INSERT on
`source_ref LIKE 'derive:metrics:%'`. Indexes: `(entity, metric_label)`,
edition.

## `graph_analytics` — per-entity graph metrics

Written ONLY by `helpers/graph/algorithms.py` (`make recompute-graph`);
never hand-edited. 14 metrics live (12 node metrics + `link_prediction` +
`voterank`) — semantics, JSON value shapes, and refresh policy:
`graph_design.txt` §5.

| Column | Type | Notes |
|---|---|---|
| `entity_name` | TEXT | FK cascade |
| `metric` | TEXT | e.g. `pagerank`, `katz_centrality`, `link_prediction` |
| `value` | TEXT | JSON (e.g. `{"value": X}`) |
| `computed_at` | DATETIME | |

**PRIMARY KEY (metric, entity_name)** — metric-first (Bundle P3) so
`/api/graph/metrics/<metric>` does a prefix SEARCH with free ORDER BY.
No query filters by entity_name alone.

## `note_search` — FTS5

```sql
CREATE VIRTUAL TABLE note_search USING fts5(
    doc_type, file_path UNINDEXED, title, sector, content,
    tokenize = 'porter unicode61')
```

Rebuilt by `helpers/maintenance/rebuild_note_search.py`; shadow tables
`note_search_{config,data,content,docsize,idx}`.

## `memory/graph.duckdb` — DuckDB cache schema (derived)

Read-side cache rebuilt from SQLite (never hand-edited; lifecycle/staleness:
`graph_design.txt` §8). 20 objects; `_build_meta.schema_version` = "9".

| Object | Shape | Notes |
|---|---|---|
| `v_node` | `id BIGINT, name, kind, sector_classification, market_cap, ticker` | 1209 rows, all 5 entity kinds; `id` = `row_number()` at build; `market_cap` tag-derived (MIN over `entity_tags`, one row guaranteed) |
| `v_company` `v_sector` `v_sub_sector` `v_super_sector` `v_theme` | filtered copies of `v_node` | TABLES (not views), same `id` space — company-only wrappers filter on them |
| `e_*` × 12 | two semantic int-id endpoint cols + `weight, properties, source_ref, valid_from, valid_to` | one per edge_type, mapped by `EDGE_REGISTRY` in `query.py` (e.g. `e_belongs`(company_name→sector_name), `e_has`(sector_name→company_name), `e_jv`/`e_competes`/`e_group`/`e_comention`(a_name,b_name), `e_supplier`(supplier_name,customer_name), `e_customer`(customer_name,supplier_name), `e_acquired`(acquirer_name,target_name,+`year`), `e_subsidiary`(subsidiary_name,parent_name), `e_belongs_to`(child_id,parent_id), `e_exposed_to`(company_id,theme_id)) — endpoint ids reference `v_node.id` |
| `_build_meta` | `key, value` | schema_version, built_at, source_db, generation, duckdb_version; drives `_is_warm()` |
| `v_embeddings` | `company_name, id BIGINT, embedding FLOAT[]` | 1046 rows; populated by `helpers/graph/embeddings.py`; powers `semantic_neighbors` |

## Constraints & integrity summary

- FKs are declared CASCADE but **`PRAGMA foreign_keys` is OFF by default**
  in raw `sqlite3.connect()`; `helpers/core/db.py:connect()` enables it.
  Stale child rows otherwise caught by the validators.
- All `properties` columns: `CHECK (json_valid)`.
- `graph_edges`: UNIQUE(source,target,edge_type), no self-loops, FK cascade.

## Integrity checks (`database_integrity_check.py`)

Registry of `Check(name, method, severity)` (`_CHECKS`); ERROR severity
counts toward the exit code, WARNING is advisory.

| Check | Sev | Catches |
|---|---|---|
| `check_relations` | error | unknown edge type, self-loops, orphaned endpoints, part_of↔has_company direction/symmetry, belongs_to/exposed_to endpoint-kind mismatches |
| `check_entity_tags` | error | orphaned tag rows (rename-without-cascade) |
| `check_events` | error | unknown event_type, orphaned entity, bad JSON |
| `check_quotes` | error | orphaned quote rows (FK-off rename/delete), malformed `properties` JSON; tolerates a DB without the table yet |
| `check_company_metrics` | error | same shape for `company_metrics`: orphaned entity, malformed `properties` JSON |
| `check_orphan_companies` | error | companies with no `part_of` sector edge |
| `check_hierarchy` | error | taxonomy completeness/structure: orphans, multi-parents, cycles, drift vs `build_sector_hierarchy.py` |
| `check_market_cap_conflicts` | error | >1 `market_cap/*` tag (DuckDB MIN(tag) picks silently; `dedupe_market_cap_tags.py`) |
| `check_cache_consistency` | error | DuckDB↔SQLite drift: `_build_meta.schema_version` + per-table counts; SKIPS (advisory) if cache absent |
| `check_normalization` | error | missing/duplicate/bad `normalized_name` |
| `check_duplicate_tickers` | error | shared non-null ticker |
| `check_fuzzy_duplicate_names` | warn | likely-same-company name pairs |
| `check_validity_window` | warn | valid_from/valid_to coverage; M&A-date salvage gap |
| `check_graph_summary` | warn | shape snapshot — feeds `make graph-stats` |
| `check_db_meta` | error | `db_meta` table/generation row missing, non-integer generation, `PRAGMA user_version` drift vs `helpers.core.db.EXPECTED_USER_VERSION`, `schema_version` mirror drift |

## Maintenance

- Gate: `make qa` (verify_notes + integrity checker + static + snapshot round-trip).
- `python3 helpers/maintenance/db_maint.py` — VACUUM → ANALYZE →
  integrity_check → backup → REINDEX (`make maint`; + DuckDB CHECKPOINT/VACUUM).
- Pre-structural-change backup: `sqlite3 memory/research.db ".backup '<path>'"`.
- Versioned snapshot: `make snapshot` → git-tracked Parquet under
  `snapshots/parquet/` (per-table + `_schema.sqlite.sql`) + local gzip copies
  under `db-backup/`; `--check` round-trip-verifies; `make snapshot-restore`
  rebuilds `memory/` from the Parquet snapshot.

## Dropped tables / columns (history)

`images` (189 orphaned FK rows; files still served from filesystem),
`entities.enhanced_tags`, `entities.market_cap` / `index_membership`
(tag-only), base `relations` table (now a VIEW), `observations`,
`entities_backup`, `relations_backup`.
