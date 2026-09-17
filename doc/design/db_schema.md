# Database Schema — `memory/research.db`

SQLite source of truth for the FinData knowledge graph, plus the derived
DuckDB read-cache schema (§ "memory/graph.duckdb" below). Architecture and
cache policy: `graph_design.md`.
Live row counts (2026-09-14): entities 1685, entity_tags 7211,
graph_edges 19325, events 436, quotes 8272, company_metrics 4412,
graph_analytics 23,837, hyper_edges 469 / hyper_incidences 5659,
provenance_agents 21, concept_schemes 11 / concepts 403 /
concept_mappings 68, entity_identifiers 0 (S3 registry, fills on
operator writes), note_search (FTS5) + `relations` VIEW.

## `entities`

One row per entity; `name` is the identity (PRIMARY KEY).

| Column | Type | Notes |
|---|---|---|
| `name` | TEXT PK | CHECK rejects `Ltd`/`Limited`/`Ltd.`/`Pvt`/`Private` suffixes — scoped to `entity_type='company'` only (taxonomy names like `Private_Sector` are exempt; see DDL comment) |
| `entity_type` | TEXT | `company` (1179) \| `institution` (207) \| `edition` (114) \| `sub_sector` (100) \| `sector` (42) \| `country` (21) \| `theme` (12) \| `super_sector` (10) |
| `created_at` / `last_updated` | DATETIME | last_updated set on every modification |
| `file_path` | TEXT | MUST resolve to an existing file under `findata/` |
| `normalized_name` | TEXT | MUST equal the markdown filename minus `.md`, character-for-character |
| `sector_classification` | TEXT | sector entity name, e.g. `FMCG` |
| `ticker` | TEXT | NSE/BSE symbol e.g. `INFY.NS`; NULL if unlisted |
| `cin` | CHAR(21) | MCA Corporate Identity Number — the PRIMARY live-CIN home (S3); parsed facets below |
| `cin_listing` | CHAR(1) | `L` listed / `U` unlisted (CIN position 1) |
| `cin_nic5` | CHAR(5) | NIC code; pre-2008 vintages carry legacy NIC-98/2004 series (WARNING, not NIC-2008) |
| `cin_state` | CHAR(2) | ROC state/office code, e.g. `MH` |
| `cin_year` | SMALLINT | incorporation year |
| `cin_ownership` | CHAR(3) | `PLC`/`PTC`/`OPC`/`FTC`/`GOI`/`SGC`/`NPC` (unknown codes warn) |

The five `cin_*` facets are a deterministic projection of `cin`
(`helpers/core/cin.py::parse_cin`), refreshed by maint-full PRE_FULL
`identifiers` (backfill_identifiers.py) — never authoritative. LLPs
carry an LLPIN in `entity_identifiers`, never a CIN.

**Removed columns (tag-only now):** `market_cap` (see `architecture.md` §6
for the 4 buckets + NULL), `index_membership`, `title` (lives in note YAML
only), `enhanced_tags` (replaced by `entity_tags`).

**Indexes:** sector_classification, normalized_name, entity_type,
file_path, name_nocase (NOCASE — the case-insensitive resolver in `app.py`).

## `entity_identifiers` — identifier registry (S3)

Everything that is NOT the live CIN: `lei`, `cik`, `isin`, `llpin`,
`alias` — plus `cin` in the CHECK solely for superseded/historic CINs
written directly with `source_ref` (the live one lives on entities).
Operator write surface: `backfill_identifiers.py --set-id` (format-gated,
cross-entity-ambiguous values rejected). Resolved by `/api/resolve` in
`app.py`.

| Column | Type | Notes |
|---|---|---|
| `entity_name` | TEXT | PK part; FK → `entities(name)` cascade |
| `identifier_type` | TEXT | PK part; CHECK in `cin\|lei\|cik\|isin\|llpin\|alias` |
| `identifier_value` | TEXT | PK part; `UNIQUE(identifier_type, identifier_value)` — resolves to exactly one entity |
| `namespace` | TEXT | e.g. GLEIF/SEC/ISIN agency (optional) |
| `valid_from` / `valid_to` | TEXT | validity window (ISO dates; inversion warns) |
| `source_ref` | TEXT | REQUIRED provenance (`manual` for CLI writes) |
| `created_at` / `last_updated` | DATETIME | created defaults to CURRENT_TIMESTAMP |

## `entity_tags`

Normalized mirror of note YAML `tags:` (the source of truth), rebuilt by
`helpers/core/sync_tags.py` (`make sync-tags`). One row per entity × tag.

| Column | Type | Notes |
|---|---|---|
| `entity_name` | TEXT | PK part; FK → `entities(name)` cascade |
| `tag` | TEXT | PK part, e.g. `sector/healthcare`, `market_cap/large_cap` |

Mirrors nine namespaces — `entity_type/`, `sector/`, `market_cap/`,
`subsector/`, `holding_company/`, `geography/`, `business_model/`,
`risk_investment/`, `investment_theme/`; the rest of the tag vocabulary
stays note-only. Index: `idx_entity_tags_tag`. Typical
query: JOIN two tag aliases for tag intersection.

## `graph_edges` — canonical edge store

Directed links; supersedes the `relations` table. 19,325 rows across 20
registered edge types — 19 populated; `penalized_by` is registered with
zero rows (counts + symmetric convention: `graph_design.md` §4,
refreshed 2026-09-14).
Producers: `parse_newsletter`/`markdown_parse` (membership pair),
`extract_relations.py` (company↔company from prose),
`derive_{co_mentions,themes}.py`, `build_sector_hierarchy.py`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `source` / `target` | TEXT | FK → `entities(name)` cascade |
| `edge_type` | TEXT | 20 registered values (19 populated; `penalized_by` zero rows), `graph_design.md` §4 |
| `weight` | REAL | default 1.0 |
| `properties` | TEXT | JSON; `CHECK (json_valid(...))` |
| `valid_from` / `valid_to` | DATE | temporal window; NULL valid_from = always-valid, NULL valid_to = current |
| `source_ref` | TEXT | provenance: edition / derive script / manual |
| `agent_id` / `source_tier` | TEXT | row provenance (S1): FK → `provenance_agents` ON DELETE SET NULL; tier enum manual\|migration\|derive\|regulator\|external — converged from `source_ref` prefixes by `backfill_row_provenance.py` (maint-full PRE_FULL) |
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
| `counterparty_entity` | TEXT | FK → `entities(name)` ON UPDATE CASCADE / ON DELETE SET NULL — the resolved twin of `counterparty` (S9: 110/110 exact matches); written by derive_hyperedges' S9 resolver |
| `source_quote` | TEXT | verbatim audit trail |
| `as_of_edition` | TEXT | sourcing newsletter edition — canonical edition STEM (joinable to `entities.name`); unresolvable titles verbatim (#136) |
| `source_ref` | TEXT | `derive:events:…` \| `manual:…` \| `migration:…` |
| `agent_id` / `source_tier` | TEXT | row provenance (S1), converged from `source_ref` prefixes |
| `properties` | TEXT | JSON, json_valid CHECK |
| `created_at` | DATETIME | |

**Indexes:** entity_type, date, type.

## `quotes` — concall quote capture

Verbatim executive quotes from `## [Concall]` blocks, extracted by
`helpers/graph/derive_insights.py --apply` (`make derive-insights` is the
dry-run preview) as
paraphrase → quote → `— Name, Title` units. Speakers are string
attributes, NOT entities (D6 deferral).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `entity` | TEXT | FK cascade |
| `quote_text` | TEXT | verbatim |
| `paraphrase` | TEXT | editor's 1-2 line summary |
| `speaker_name` / `speaker_title` | TEXT | NULL name for anonymous/role-only |
| `as_of_edition` | TEXT | edition STEM (as quotes, #136; H1s in the `[Company \| Cap \| Sector]` concall-header shape are guarded back to the stem at capture — concall_title_edition_normalisation D8) |
| `source_ref` | TEXT | `derive:quotes:<stem>:<line>` — LIKE sweep = idempotency key |
| `agent_id` / `source_tier` | TEXT | row provenance (S1), converged from `source_ref` prefixes |
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
| `metric_label` | TEXT | best-effort `revenue` \| `ebitda_margin` \| `capex` \| `aum` \| `growth` \| …; NULL if not inferable |
| `value_raw` | TEXT | `₹2,75,972 crore` \| `140-150 bps` |
| `value_num` | REAL | parsed (range lower bound) |
| `unit` | TEXT | `crore` \| `lakh` \| `bps` \| `percent` \| `bn_usd` \| `gw` \| `mw` \| `x` |
| `period` | TEXT | `Q1 FY27` \| `FY28` (best-effort) |
| `as_of_edition` | TEXT | |
| `source_quote` | TEXT | verbatim line (provenance) |
| `source_ref` | TEXT | `derive:metrics:<stem>:<line>` — idempotency key |
| `agent_id` / `source_tier` | TEXT | row provenance (S1), converged from `source_ref` prefixes |
| `properties` | TEXT | JSON, json_valid CHECK |
| `created_at` | DATETIME | |

No natural UNIQUE key; idempotency = DELETE-then-INSERT on
`source_ref LIKE 'derive:metrics:%'`. Indexes: `(entity, metric_label)`,
edition.

## `graph_analytics` — per-entity graph metrics

Written by `helpers/graph/algorithms.py` (`make recompute-graph`: the 12
dyadic dispatch metrics + `link_prediction`/`voterank`) AND the two HGX
lanes (`hyper_communities.py` / `hyper_centralities.py`, `make
recompute-hyper`; maint-full TIER2 after derive-hyperedges) — all three
writers converge on `algorithms.write_analytics`, an upsert that never
restamps `computed_at` on identical values. Never hand-edited. 18
metrics live (16 node metrics + `link_prediction` + `voterank` — the
dyadic family at up to 1,684 rows; 4 hyper company-only at 1,165:
`hypermmsbm_community`/`ho_pagerank`/`s_betweenness`/`s_closeness`;
the eigen trio is defined but skipped at the non-uniform default
scope) — semantics, JSON value shapes, and
refresh policy:
`graph_design.md` §5.

| Column | Type | Notes |
|---|---|---|
| `entity_name` | TEXT | FK cascade |
| `metric` | TEXT | e.g. `pagerank`, `katz_centrality`, `link_prediction` |
| `value` | TEXT | JSON (e.g. `{"value": X}`) |
| `computed_at` | DATETIME | |

**PRIMARY KEY (metric, entity_name)** — metric-first (Bundle P3) so
`/api/graph/metrics/<metric>` does a prefix SEARCH with free ORDER BY.
No query filters by entity_name alone.

## `hyper_edges` — hypergraph incidence layer (star store)

Set-valued facts as first-class rows (hypergraph_incidence_hyx proposal,
2026-09-13; source memo `doc/local/evaluations/hyper_graph_assessment.md`).
A category IS a hyperedge here (its members via `hyper_incidences`) instead
of a materialised star of dyads — star expansion is storage truth, the
`graph_edges` clique projections stay derive-time views. Live (2026-09-14,
after S5/S8–S11 + S17/S21): 469 hyperedges / 5,659 incidences over 9 edge types
(`sector` 42, `theme` 12, `country` 21, `group` 8, `edition` 109 — the S8
quotes union, weighted — `industry` 117, `event` 110, `jv` 6, `sub_sector` 55
— S11's curated + S17's grown industry→canonical map, the first
company→sub_sector linkage in the store. D7 (2026-09-15): an authored
`subsector:` note field is CANONICAL per company — exclusive precedence
over the alias-derived union; unknown values are worklisted, never fatal;
`sync_tags` mirrors it as `subsector/<slug>` in `entity_tags`).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `edge_type` | TEXT | `sector` \| `theme` \| `country` \| `group` \| `edition` \| `industry` \| `event` \| `jv` \| `sub_sector` |
| `label` | TEXT | the hyperedge's own name (category, edition title, group, venture, `event_type:id`) |
| `weight` | REAL | default 1.0; per-hyperedge weight |
| `valid_from` / `valid_to` | DATE | temporal window (HIF-compatible) |
| `source_ref` | TEXT | provenance: `derive:hyperedges:<upstream>` (edition: `co_mentioned_in+quotes`) |
| `agent_id` / `source_tier` | TEXT | row provenance (S1), converged from `source_ref` prefixes |
| `properties` | TEXT | JSON; `CHECK (json_valid(...))`; carries `n_members`, `upstream_types`; event hyperedges add the S4 facet keys below |
| `created_at` | DATETIME | |

**Constraints:** `UNIQUE(edge_type, label)` (backfill idempotency key).
Written ONLY by `helpers/graph/derive_hyperedges.py` (`make
derive-hyperedges`; maint-full TIER2). Columns are HIF-lossless — the JSON
interchange schema maps 1:1 onto the pair (memo §9.4).

## `hyper_incidences` — hypergraph membership rows

One row per (hyperedge, member). The per-incidence `weight` is REAL since
S8 (quote count per (edition, company); 1,318 weighted rows live — a
member's intensity within the set); `direction` (`head`/`tail`) exists for
directed-hypergraph parity and is still NULL (no directed source yet);
`role`/`valid_from`/`valid_to` are the S4 n-ary event facets
(ontology_convention_stack, schema v11, written by
`derive_hyperedges --roles` — maint-full TIER2).

| Column | Type | Notes |
|---|---|---|
| `edge_id` | INTEGER | PK part; FK → `hyper_edges(id)` cascade |
| `entity_name` | TEXT | PK part; FK → `entities(name)` cascade |
| `weight` | REAL | per-incidence intensity (HIF granularity 2); NULL = unweighted |
| `direction` | TEXT | `head` \| `tail` \| NULL (undirected); CHECK-enforced |
| `role` | TEXT | participant role (S4): `acquirer`/`target` (acquisition), `partner` (jv); NULL = unlabeled — unmapped types never confabulate |
| `valid_from` / `valid_to` | DATE | participant-level validity window (S4); NULL = unbounded |

**S4 event facets (live 2026-09-14):** 220 role-tagged incidences (41
acquirer + 41 target + 138 partner — the 110 counterparty-carrying
events × 2 participants); observation dates land on
`hyper_edges.valid_from` (20 dated), and event-hyperedge `properties`
carry `period`, `date_precision`, `magnitude_raw` (the audit string —
never identity), `magnitude_numeric`, `magnitude_unit` (11 parsed). The
`events` table REMAINS the canonical temporal spine (D-O2) — event/jv
hyperedges are derived projections, re-converged by the `--roles` pass,
which prints a reconciliation report (unresolved / duplicates /
conflicts / untagged types) before any apply; live: 0 unresolved, 0
duplicates, 0 conflicts, all types role-mapped.

**Indexes:** `he_type_idx` (edge_type), `hi_entity_idx` (entity_name —
"which sets is X in" without a scan). First compute consumer:
`helpers/graph/hyper_communities.py` (hy-MMSBM overlapping communities,
metric `hypermmsbm_community`); S12/S14 lanes in
`helpers/graph/hyper_centralities.py` — `ho_pagerank` consumes the
per-incidence weights (weighted higher-order walk, exact HGX reduction
when unweighted), s-lanes unweighted pending S16.

## `provenance_agents` — PROV-O agent registry (S1)

PROV-O Starting-Point lineage as a table convention
(ontology_convention_stack S1, 2026-09-14). The five fact tables above
(`graph_edges`, `events`, `quotes`, `company_metrics`, `hyper_edges`)
carry nullable `agent_id` → this registry and `source_tier`
(manual | migration | derive | regulator | external). `source_ref` is
never rewritten — it stays the idempotency key; the two columns are a
converged projection of its prefixes, written by
`helpers/misc/backfill_row_provenance.py` (maint-full PRE_FULL;
schema version 8). Live: 21 agents, 100% row coverage.

| Column | Type | Notes |
|---|---|---|
| `agent_id` | TEXT PK | producer slug (script stem or lane name) |
| `name` | TEXT | human label incl. lane scope |
| `version` | TEXT | `baseline` for pre-registry history (no fake precision); producers stamp real versions from S1b |
| `repo_ref` / `script` / `command` | TEXT | provenance of the producer itself |
| `as_of` | TEXT | seeding/stamp date |

`regulator` tier has no producer yet — reserved for regulator-filing
ingest lanes. Coverage gaps surface via the `provenance_coverage`
integrity check (WARNING).

## `concept_schemes` / `concepts` / `concept_mappings` — SKOS conventions (S2)

SKOS as table conventions (ontology_convention_stack S2, 2026-09-14;
schema v9; lifecycle `status` columns added by ontology_governance S1,
schema v12). Written ONLY by `helpers/misc/seed_concepts.py` (maint-full
PRE_FULL; lifecycle-aware DELETE-then-INSERT on the `seed:%` source_ref
prefix — rows the roster still produces are reinserted `active`, rows it
no longer produces flip to `superseded` instead of being deleted).
Live: 11 schemes / 403 concepts / 68 mappings — the 9 entity_tags
namespaces + `super_sector`/`industry`; taxonomy concepts canonical
from entity names (case-variant tags absorbed), `broader_id` from
`belongs_to` (sub_sector→sector→super_sector); crosswalks seeded from
`derive_hyperedges.SUB_SECTOR_ALIASES` (S11/S17 curated map,
exactMatch). `concept_mappings` is the ONE crosswalk home (D-O1) —
match-id columns on `concepts` are forbidden; external targets (NIC,
NACE, GICS-opaque, Wikidata QIDs) land here without FKs by design.
`subtree()` in the seeder is the reusable closure helper (recursive
CTE, not OWL; active-only by default — superseded/candidate concepts
stop grouping their descendants). Candidate rows land via
extractor/triage funnels (`agent:`/`manual:` source_ref) and promote
through the validated `--promote` / `--promote-map` surfaces
(plan-then-apply; any failure blocks the batch). Hygiene via the
`concepts` integrity check (WARNING): dangling broader, cycles,
duplicate pref_labels among ACTIVE rows (case-insensitive), empty
schemes, active mappings referencing superseded concepts, hierarchy
routing through superseded nodes, dangling candidates.

| Table | Key columns |
|---|---|
| `concept_schemes` | `scheme_id` PK · `label` · `scheme_type` (tag_namespace\|taxonomy\|label_scheme) · `version` · `active` |
| `concepts` | `concept_id` PK (`scheme:code`) · `scheme_id` FK · `concept_code` · `pref_label` · `alt_label` (underscore-spaced) · `notation` (external codes, future) · `broader_id` self-FK · `source_ref` · `status` (candidate\|active\|superseded) · UNIQUE(scheme_id, concept_code) |
| `concept_mappings` | `source_scheme` + `source_concept` · `target_scheme` + `target_concept` (no FK — external targets by design) · `match_type` CHECK (exactMatch\|closeMatch\|broadMatch\|narrowMatch) · `source_ref` · `version` · `status` (candidate\|active\|superseded) |

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
`graph_design.md` §8). 30 objects (`v_node` + 8 filtered projections + 2
embedding tables + 18 `e_*` + `_build_meta`); `_build_meta.schema_version`
= "14" — a cache stamped otherwise fails `_is_warm()` and triggers a rebuild.

| Object | Shape | Notes |
|---|---|---|
| `v_node` | `id BIGINT, name, kind, sector_classification, market_cap, ticker` | 1,685 rows, all kinds; `id` = `row_number()` at build; `market_cap` tag-derived (MIN over `entity_tags`, one row guaranteed) |
| `v_company`(1,179) `v_country`(21) `v_sector`(42) `v_sub_sector`(100) `v_super_sector`(10) `v_theme`(12) `v_edition`(114) `v_institution`(207) | filtered copies of `v_node` | TABLES (not views), same `id` space — company-only wrappers filter on them |
| `e_*` × 18 | two semantic int-id endpoint cols + `weight, properties, source_ref, valid_from, valid_to` | one per edge_type, mapped by `EDGE_REGISTRY` in `query.py` (e.g. `e_belongs`(company_name→sector_name), `e_has`(sector_name→company_name), `e_jv`/`e_competes`/`e_group`/`e_comention`(a_name,b_name), `e_supplier`(supplier_name,customer_name), `e_customer`(customer_name,supplier_name), `e_acquired`(acquirer_name,target_name,+`year`), `e_subsidiary`(subsidiary_name,parent_name), `e_belongs_to`(child_id,parent_id), `e_exposed_to`(company_id,theme_id)); the later types (`e_semantic_peer`, `e_invested`, `e_cited_in`, `e_listed_in`, `e_dir`, `e_all_und`) share those shapes — full mapping is `EDGE_REGISTRY` in `query.py` — endpoint ids reference `v_node.id` |
| `_build_meta` | `key, value` | schema_version, built_at, source_db, generation, duckdb_version, note_embed_dims, note_embed_model; drives `_is_warm()` (the note_embed_* pair catches same-dims model swaps) |
| `v_embeddings` | `company_name, id BIGINT, embedding FLOAT[]` | 1,165 rows; materialised by `helpers/graph/query.py` (`_materialise_embeddings`, CTAS from SQLite `company_embeddings` — embeddings.py writes the SQLite side); powers `semantic_neighbors` |
| `v_note_embeddings` | `file_path, doc_type, title, emb FLOAT[384]` | 16,521 rows; CTAS from the `note_search` JSON column (`_materialise_note_embeddings`); powers similar-notes / notes-like wrappers |

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
| `check_provenance_coverage` | warn | agent_id/source_tier fill per fact table; unmapped source_ref prefixes (S1) |
| `check_concepts` | warn | SKOS hygiene: dangling broader, cycles, duplicate pref_labels, empty schemes (S2) |
| `check_identifiers` | warn | CIN format/facet drift, listing↔ticker + state↔geography cross-checks, registry dangling/inverted windows (S3) |
| `check_graph_summary` | warn | shape snapshot — feeds `make graph-stats` |
| `check_db_meta` | error | `db_meta` table/generation row missing, non-integer generation, `PRAGMA user_version` drift vs `helpers.core.db.EXPECTED_USER_VERSION`, `schema_version` mirror drift |

## Maintenance

- Gate: `make qa` (verify_notes + integrity checker + static + snapshot round-trip).
- `python3 helpers/maintenance/db_maint.py` — VACUUM → ANALYZE →
  integrity_check → backup → REINDEX (`make maint`; + DuckDB CHECKPOINT/VACUUM).
- Pre-structural-change backup: `sqlite3 memory/research.db ".backup '<path>'"`.
- Versioned snapshot: `make snapshot` → git-tracked Parquet under
  `snapshots/parquet/` (per-table + `_schema.sqlite.sql`) + local zstd copies
  under `db-backup/`; `--check` round-trip-verifies; `make snapshot-restore`
  rebuilds `memory/` from the Parquet snapshot.

> **Diagram:** `diagrams/snapshot_apply.{json,html}` — the apply
> handshake: force-guard, locate dirs + DDL, SQLite tmp-build (DDL →
> parquet rows → FTS5 rebuild → FK check → atomic swap), DuckDB restore
> (CHECKPOINT), verify round-trip (archify sequence; JSON IR is the
> committed source, HTML regenerable). Re-render when the restore path
> in `helpers/maintenance/snapshot_db.py` changes.

## Dropped tables / columns (history)

`images` (189 orphaned FK rows; files still served from filesystem),
`entities.enhanced_tags`, `entities.market_cap` / `index_membership`
(tag-only), base `relations` table (now a VIEW), `observations`,
`entities_backup`, `relations_backup`.
