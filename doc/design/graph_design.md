# FinData Graph Design

**Status:** LIVE architecture (v2.0, 2026-08-15). Path D — DuckDB layered
read-only over SQLite. duckpgq was RETIRED 2026-08-14 (Phases A-E:
`doc/improvements/archive/graph/duckpgq_retirement.md`); every algorithm now runs
on **Onager** + plain SQL. NetworkX was retired the same day. This revision
compresses the historical record (see the archive proposal + git history)
and adds the full algorithm catalog (§5).

**Diagram:** `diagrams/derive_chain.{json,html}` — the derive_* chain
(extract → edges → event promotion → insights/quotes) as a staged
dataflow (archify; JSON IR is the committed source, HTML regenerable).
Executable spec: tests/test_integration_derive_chain.py. Re-render when
the chain changes (see
`improvements/archive/tooling/archify_diagram_pipeline.md`).

## 1. Problem & Scope

The knowledge graph stores 1,685 entities in SQLite (`memory/research.db`) —
1,179 companies, 207 institutions, 114 editions, 100 sub_sectors, 42 sectors,
21 countries, 12 themes, 10 super_sectors (2026-09-14) —
connected by 19,325 edges across 20 registered semantic edge types (§4;
19 populated — `penalized_by` registered with zero rows). At ~1.7k nodes /
~19k edges a server-grade graph DB is overkill; DuckDB gives in-process graph
SQL + analytics attached read-only to the existing SQLite — zero new infra.

## 2. Architecture

```text
WRITES: markdown_parse / parse_newsletter / derive-* / mutations
    └─> memory/research.db (SQLite — sole source of truth)
          entities, graph_edges, entity_tags, graph_analytics

READS (read-only ATTACH per session, sqlite_scanner):
    memory/graph.duckdb (disk cache; schema v14, §8)
      v_node (1685 rows: all 8 entity kinds) + 18 e_* tables
    ├── pattern queries ............ plain SQL JOINs (query.py)
    ├── shortest_path / find_cycles  recursive CTEs
    └── algorithms ................. Onager extension (onager.py):
          centralities, communities, link prediction, graph metrics
```

## 3. Core Principles

1. **SQLite is the only writer of graph data.** DuckDB never writes edges.
   FK cascades propagate renames/deletes into the next DuckDB session.
   Analytics results are written *back* to SQLite (`graph_analytics`) —
   never hand-edited; `make recompute-graph` is the only writer.
2. **DuckDB is the graph engine; Onager is the algorithm library.** Pattern
   queries are plain JOINs over the materialised `v_node`/`e_*` tables;
   all graph algorithms run on Onager (Apache-2.0 community extension,
   installed at runtime — not a Python dep). duckdb is unpinned; onager
   tracks DuckDB releases.
3. **Disk-cached projections, manual invalidation.** `v_node`/`e_*` live in
   `memory/graph.duckdb`; warm connects skip materialisation (~3.5×
   faster). No auto-detection — rebuild after SQLite-side writes (§8).
   `make qa` catches drift via `check_cache_consistency` (ERROR when
   present-but-stale; WARNING-skip when absent).

Module split: `query.py` (connect/materialise + every pattern query +
`semantic_neighbors`), `onager.py` (Onager-backed metrics over
`(src,dst,weight)` integer edges), `algorithms.py` (`compute()` dispatcher,
CLI, persistence), `stats.py` (human summary), `embeddings.py`
(`v_embeddings`), `derive_*.py`/`extract_relations.py` (edge producers).

## 4. SQLite Schema (source of truth)

```sql
CREATE TABLE graph_edges (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL REFERENCES entities(name)
                  ON DELETE CASCADE ON UPDATE CASCADE,
    target      TEXT NOT NULL REFERENCES entities(name)
                  ON DELETE CASCADE ON UPDATE CASCADE,
    edge_type   TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    properties  TEXT NOT NULL DEFAULT '{}',   -- JSON
    valid_from  DATE, valid_to  DATE,         -- NULL valid_to = current
    source_ref  TEXT NOT NULL,                -- provenance, e.g. "The Chatter #69 L218"
    symmetric   INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
CREATE INDEX ge_type_idx   ON graph_edges(edge_type);
CREATE INDEX ge_target_idx ON graph_edges(target);
-- no ge_source_idx: UNIQUE(source,target,edge_type) auto-index leads with
-- `source` and covers source-only filters (EXPLAIN-verified).

CREATE TABLE graph_analytics (          -- written ONLY by recompute-graph
    entity_name TEXT NOT NULL REFERENCES entities(name)
                  ON DELETE CASCADE ON UPDATE CASCADE,
    metric      TEXT NOT NULL,
    value       TEXT NOT NULL,           -- JSON
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric, entity_name)    -- metric-first: hot queries are
);                                      -- WHERE metric=? ORDER BY entity_name

CREATE VIEW relations AS                -- backward-compat (READ-ONLY!)
    SELECT source, target, edge_type AS relation_type FROM graph_edges;
```

Edge types (extensible; TEXT column). Live counts 2026-09-14 (20 registered
types, 19 populated, 19,325 rows; previous snapshot 2026-08-19 said 12):

| edge_type | Direction / semantics | sym | rows |
|---|---|---|---|
| `semantic_peer` | company ↔ company (embedding kNN similarity, derived — enrich_relations E3 via DuckDB VSS; weight = rank score) | 1 | 7806 |
| `competes_with` | company ↔ company | 1* | 3576 |
| `cited_in` | company/sector → edition (OKF provenance; okf_activation P) | 0 | 1913 |
| `co_mentioned_in` | company ↔ company (newsletter co-mention, derived) | 1 | 1329 |
| `part_of` / `has_company` | company ↔ sector (legacy two-row pair) | – | 1179 each |
| `listed_in` | company → country (geography lane, derived) | 0 | 930 |
| `invested_in` | investor (institution/company) → company | 0 | 715 |
| `exposed_to` | company → theme (cross-sector) | 0 | 359 |
| `belongs_to` | sector → super_sector / sub_sector → sector (hierarchy) | 0 | 142 |
| `jv_with` | company ↔ company (JV; `properties.venture` where prose names it — the `jv` regroup key, D5) | 1* | 81 |
| `subsidiary_of` | subsidiary → parent | 0 | 68 |
| `acquired` | acquirer → acquired (temporal; set valid_to) | 0 | 52 |
| `supplier_to` / `customer_of` | company → company (asymmetric, one direction) | 0 | 9 / 1 |
| `regulated_by` | company → institution (RBI, SEBI) | 0 | 17 |
| `same_group` | company ↔ company (promoter group; cross-note lane `derive:relations:cross_note` — group prose accumulated across files, D6) | 1 | 34 |

Sub_sector classification (D11, 2026-09-15): three lanes — the S11/S17
industry-alias map, the D7 note `subsector:` field, and the
version-controlled `COMPANY_SUB_SECTORS` map in derive_hyperedges.py
(operator-curated; canonical precedence, map wins; NO note writes — the
preferred lane until the D12/D13 NIC revisit). Naming rule: children
name the segment only; the parent supplies the domain word; never
collide with a sector name (machine-warned via
`build_sector_hierarchy --check`).
| `approved_by` | company/institution → institution | 0 | 9 |
| `rated_by` | company → rating agency (CRISIL) | 0 | 5 |
| `penalized_by` | company → institution (RBI, SEBI) | 0 | 0 — registered, not yet produced |

`sym` drift: `competes_with` (105 rows) and `jv_with` (10 rows) carry
`symmetric=0` against the symmetric convention (`sym=1` marked `1*`).
Untriaged — clean up or relax the convention before relying on `symmetric`
in queries.

**Edition nodes (okf_activation P, 2026-08-19):** editions are entities
(`entity_type='edition'`, name = note STEM — the canonical edition key;
quotes/company_metrics.as_of_edition now STORE stems too — normalized at
the derive write boundary via edition_index, #136: 99.4%/99.7% of sourced
rows join directly; 4 known mangled/unresolvable titles stay verbatim and
are reported, never guessed). `cited_in` edges project the OKF `sources[]`
frontmatter into the graph (props `{resource, n_quotes}`); fan-in is hub-
skewed (`A_Quarter_That_Refuses_To_Behave` = 393/1,913, 2026-09-14), so analytics
exclude it from activity views (`_MEMBERSHIP_TYPES`), link-prediction's
default projection omits it, and context packs rank it last and never
expand hops through it. Standalone target `make derive-cited-in-rebuild`
(writes entities+edges, then rebuilds this DuckDB cache).

**Symmetric convention:** one row per pair in canonical alphabetical order
(`source LE target`). Legacy `part_of`/`has_company` keep the two-row
bidirectional pattern for backward compat. `belongs_to`/`exposed_to` are
materialised via dedicated CTASs outside `EDGE_REGISTRY` (mixed endpoint
kinds). `listed_on_index` was never built (`index_membership` column dropped
2026-07-28; re-ingest from YAML if ever wanted) — distinct from the live
`listed_in` company→country lane above.

Constraints: orphan edges impossible (FK); no self-loops (CHECK); no
duplicate directed edges (UNIQUE); provenance NOT NULL; WAL handles
concurrent writes; DuckDB is read-only.

## 5. Graph Algorithms Catalog

Everything below is Onager-backed unless noted. CLI:
`python3 helpers/graph/algorithms.py <cmd> [--top N] [--apply]`;
`--all --apply` (= `make recompute-graph`) refreshes every metric.

### 5.1 Node metrics → `graph_analytics`, JSON value

| metric (CLI cmd) | semantics (all hand-verified) |
|---|---|
| `degree` | degree / (n−1), undirected, reverse rows deduped |
| `pagerank` | damping default; ranking preserved vs old duckpgq (2026-08-14) |
| `eigenvector` | L2-normalised, sign-fixed so max is positive |
| `closeness` | exact, undirected ((n−1)/Σd) |
| `betweenness` | exact; `--top N` caps output |
| `clustering` | local clustering coefficient |
| `harmonic` | Σ 1/d(v,u); well-defined on disconnected graphs |
| `katz` | **alpha pinned 1e-4** (Onager default 0.1 DIVERGES on the live graph: hub degree 89 → λmax>10; "Convergence failed after 100 iterations"); ranking stable in [1e-4,1e-2]; beta=1 |
| `laplacian` | Qi et al. 2012: X(v)=d²+d+2·Σ_{u∈N(v)} d(u) |
| `local-reaching` | Onager variant = \|2-hop neighbourhood incl. self\| (NOT networkx's definition) |
| `wcc` | weakly-connected component id |
| `louvain` | community id (+ modularity) |

### 5.2 Pair-valued: link prediction (`link-predict`)

`onager_lnk_{jaccard,adamic_adar,common_neighbors,pref_attach,resource_alloc}`
(`--method`). Canonicalised pairs (LEAST/GREATEST — onager pair direction is
layout-dependent), existing edges excluded both directions, positive scores
only, score-desc. Default projection = non-membership types
(co_mentioned_in, jv_with, competes_with, same_group) so predictions are not
trivial sector co-occurrence; `--edge-types a,b` overrides. Live: 3,509
candidate pairs / 236 entity rows (jaccard). **Opt-in `--apply`** (D13;
default dry-run): per-node candidate lists
`{"method","edge_types","candidates":[{name,score}...]}` under metric
`link_prediction`; recompute replaces wholesale.

### 5.3 Graph-valued

- `graph_metrics()` (Phase 2) — one round-trip over the eight `onager_mtr_*`:
  density, diameter, radius, avg_path_length, transitivity, triangles
  (unique = per-node sum/3), avg_clustering, assortativity. Unweighted;
  node set = edge endpoints; **diameter/radius/APL are NULL on disconnected
  projections** (no component collapse); assortativity 0.0 on regular
  graphs. Live (full edge set, connected): density .0042, 5460 triangles,
  transitivity .271, avg_clustering .193, assortativity −.405, diameter 8,
  radius 5, APL 4.32. Consumers: `make graph-stats` ("Structure" section)
  and `/api/graph/stats` (`structure` block; null-safe degradation).
  **Result cached per (generation, edge_types) in the P2.3 query cache**
  (2026-08-15, #106): pure function of the edge set, so repeat calls are
  ~1ms instead of ~300ms; `clear_graph_cache()` on rebuild/refresh evicts.
- `voterank` (Phase 3) — ordered seed list (output order IS the ranking; do
  not re-sort; `num_seeds` caps). Opt-in `--apply` like every metric (D13):
  `{"seeds":[ordered names]}` per seed node. Live: 10 sector hubs
  (Automotive first). List-valued → not in `_METRIC_DISPATCH`.

### 5.4 Query-layer algorithms (plain SQL / VSS — `query.py`)

- `shortest_path(src, dst, max_hops, as_of)` — recursive CTE (was ANY
  SHORTEST + CTE fallback pre-retirement; now CTE-only).
- `find_cycles()` — recursive CTE.
- `semantic_neighbors(company, k, cross_sector, as_of)` — cosine similarity
  over `v_embeddings` via VSS scalar functions (~3ms @ 1k; brute-force,
  HNSW macros broken on vss b833341).
- Pattern queries — plain JOINs over `e_*`: `sector_of`, `sector_members`
  (+ market_cap variants), `theme_members`, hierarchy walks
  (`super_sector_of`, `sectors_in_super`, `sub_sectors_of`), `neighbors`,
  `peers`, `jv_partners`, `group_siblings`, `acquisitions`,
  `subsidiary_of_company`, `suppliers_and_customers`,
  `company_neighbors_bundle`, plus batch reports `co_mention_top`,
  `cross_sector_bridges`, `edges_by_year`. `as_of` temporal slicing on 8
  wrappers (NULL valid_from = always-valid).

### 5.5 Cross-cutting conventions

Unweighted by default (weights ignored by all onager metrics except
pagerank-weighted, S2); undirected with
reverse/duplicate rows deduped; `{}`/`[]` on empty edge sets; company-only
rows where the historical contract demands (some metrics 1165 rows vs 1648).

Weight carry-through (pagerank_graph_enhancements S2, 2026-09-22): the
pre-S2 census note "onager ignores weights" was WRONG — `onager_ctr_pagerank`
consumes the materialised weight column (verified: 9:1 weighted star gives
0.371 vs 0.075 leaf scores). Contract since S2: the plain `pagerank` metric
passes unit weights (`onager_pagerank(weighted=False)`); the distinct
`pagerank_weighted` metric consumes carried weights — cited_in carries
`n_quotes + 1` (backfilled at derive time by `derive_cited_in.backfill_weights`,
live range 1.0–44.0), invested_in carries stake %, competes_with carries
similarity. co_mentioned_in has NO recoverable per-pair count in the store
(properties hold only the latest edition; the UNIQUE constraint collapses
cross-edition history) — unit weights, derive-time gap noted. Scale
heterogeneity (stakes ≤100 vs similarity ≤0.914) is carried raw; the
weighted top-10 overlaps the unweighted 10/10 (weights refine, do not
distort — evaluated live 2026-09-22).

Temporal as-of projection (pagerank_graph_enhancements S3, 2026-09-22):
``listed_on_index.valid_from`` is backfilled from ``properties.as_of``
(6,615/6,615 rows, the constituents-CSV snapshot date; wired into
``derive_indices.backfill_validity``) — joining ``invested_in`` (filing
dates, 799/799) as the only fully dated types. ``onager_pagerank(as_of=D)``
filters the projection to edges valid at D (NULL ``valid_from`` =
always-valid; ``valid_to`` must be after D); ISO dates are validated before
SQL inlining. Surface: ``query.pagerank(as_of=...)`` /
``pagerank_weighted(as_of=...)`` and ``algorithms.py pagerank --as-of``.
Live semantics check: invested_in edges valid 799 today vs 91 at 2025-06-30
— the filter genuinely excludes later filings; the Economic top-8 is
stable across D (structural edges are undated and dominate), which is the
honest current-state reading.

Pagerank projection (pagerank_graph_enhancements S1, 2026-09-22): the
persisted `pagerank` metric runs over the **ECONOMIC projection** —
`onager.ECONOMIC_EDGE_TYPES`, every non-membership edge type
(competes_with, cited_in, co_mentioned_in, invested_in, subsidiary_of,
same_group, jv_with, supplier_to, customer_of, acquired, semantic_peer,
rated_by, regulated_by, approved_by) — so the rank measures economic
centrality, not index-membership degree. `query.pagerank` defaults to
`edge_label="Economic"`; the CLI maps the default to it (explicit
`--edge-label` overrides); the legacy membership view stays available as
`BelongsTo`. On the live 56k-edge graph the two projections share 0/10
of their top-10 (Economic: Reliance, M&M, JSW Energy, Wipro, Infosys;
BelongsTo: membership-star artifacts).
**Do not wrap `onager_ctr_personalized_pagerank`**: its personalisation
column is ignored, the restart node is hardcoded to node_id 1, and it errors
without one (Onager bug, documented in onager.py — revisit on a future
build). Tests: `tests/test_onager_capabilities.py` (contract, hand-computed
values), `tests/test_integration_graph_algorithms.py` (dispatcher + CLI +
persistence), perf: `graph_link_prediction` benchmark.

Ontology vocabulary (edge/event/hyperedge/role rosters, binding decisions
D-O1…O6): `doc/design/ontology.md` — the master reference.

### 5.6 Persistence & refresh

`make recompute-graph` → `--all --apply`: the 15 dyadic metrics (12
dispatch metrics + `pagerank_weighted` + link_prediction + voterank;
2026-09-22). Node metrics store
`{"value": X}` (louvain adds `"community"`+`"modularity"`; wcc
`"componentId"`). UPSERT on metric-first PK; recompute replaces wholesale.
`make qa` warns when `computed_at` < `max(entities.last_updated)` (advisory).
`make recompute-hyper` → the HGX lanes (`hyper_communities` +
`hyper_centralities`; also maint-full TIER2, after `derive-hyperedges`
and before the tail snapshot): 4 hyper metrics at 1,165 company rows
(`hypermmsbm_community`, `ho_pagerank`, `s_betweenness`, `s_closeness`);
the eigen trio is uniform-hypergraph-only and skips at the mixed-size
default scope. Seeded fits (seed 42) → warm cycles converge byte-stable
values through the same upsert (zero snapshot churn).

## 6. API surface (`app.py`)

| Route | Backed by |
|---|---|
| `GET /api/graph/peers/<name>` | `query.peers` |
| `GET /api/graph/semantic/<name>` | `query.semantic_neighbors` (VSS, `?k= ?metric= ?cross_sector=`) |
| `GET /api/graph/neighbors/<name>` | ego bundle (sector+peers+jv+siblings+acq+parent+suppliers+customers), `?as_of=` |
| `GET /api/graph/shortest?a=&b=` | `query.shortest_path` (+`as_of`) |
| `GET /api/graph/sector/<name>` | `sector_of` / `sector_members` |
| `GET /api/graph/country/<name>` | `country_companies` (#219 layer; `?market_cap=`) |
| `GET /api/graph/exposure` | `country_exposure_bundle` (per-country totals + country x sector matrix; `?country= ?sector=`) |
| `GET /api/graph/stats` | SQLite aggregates + Onager `structure` block (null-safe; Onager block cached per generation, #106) |
| `GET /api/graph/metrics/<metric>` | `graph_analytics` reader |
| `GET /api/graph/co-mentions` `bridges` `edges-by-year` | batch reports |
| `POST /api/graph/refresh` | rebuild cache + reset the long-lived connection |

Long-lived cached DuckDB connection (`get_graph_connection()`; lazy,
failure-cached with TTL, thread-safe). Case-insensitive entity resolution;
JSON 404s scoped to `/api/`; ETag from `_build_meta`. UI: Graph tab in
`findata.html` (cytoscape.js ego network, typeahead, shortest-path tool).

## 7. Sync Contract

| Operation | SQLite | DuckDB | Analytics |
|---|---|---|---|
| add/rename/delete entity or edge | txn; FK cascades | nothing (read next session) | optional |
| newsletter parse / derive-* | txn | nothing | optional hook |
| ad-hoc query | none | open warm session, query | n/a |
| recompute | read | run algos, write back to SQLite | *is* the refresh |
| `make qa` | none | cache-consistency ERROR-if-stale | advisory staleness |

Materialisation write-ups (add entity → INSERT + part_of/has_company + note
file; rename → UPDATE, cascades, mv note + YAML; delete → DELETE, cascades,
rm note) are mechanical; see git history for worked examples.

## 8. Disk Cache (`memory/graph.duckdb`)

- **API:** `connect()` (warm-aware), `connect(rebuild=True)` /
  `make graph-rebuild`, `connect(fresh=True)` / `fresh_rebuild()` (drop +
  recreate; version bumps, corruption), `connect(db_path=...)` → colocated
  `.duckdb` (test isolation). Cold ~1.1s / warm ~0.3s.
- **`_build_meta`:** schema_version (code constant; mismatch = rebuild),
  built_at, source_db, generation, duckdb_version, note_embed_dims,
  note_embed_model. `_is_warm()` opens read-only and checks (the
  note_embed_* pair catches same-dims model swaps).
- **Staleness:** no auto-detection — after any SQLite-side write
  (`parse_newsletter --apply`, `derive-relations`, stubs, ...) rebuild via
  `make graph-rebuild` / `POST /api/graph/refresh` /
  `check_cache_consistency` (per-table counts; runs in `make qa`). Rejected
  alternatives: row-count compare (misses renames), generation counter
  (touches every writer).
- **Concurrency:** DuckDB allows one RW or many RO connections — never both.
  `/api/graph/refresh` rebuilds + resets the Flask connection in one call.
- **Snapshots/maintenance:** `make snapshot` snapshots both SQLite and
  DuckDB (Parquet into git-tracked `snapshots/parquet/` + zstd copies into
  local `db-backup/`; `--no-duckdb` to skip); `make maint` adds CHECKPOINT +
  VACUUM (`--skip-duckdb` to skip); `make snapshot-restore` rebuilds
  `memory/` from the Parquet snapshot; `memory/*.duckdb*` gitignored.
- **Extensions:** `make update-extensions` (weekly; `connect()` never calls
  UPDATE — 5s round-trip).
- **CLI inspection:** duckdb/sqlite3 CLIs are fine for one-off looks
  (~140ms); anything reusable belongs in a wrapper (in-process queries are
  ~25× cheaper). `COPY (...) TO 'x.parquet'` exports any result (PARQUET
  recommended; CSV untyped; JSON COPY broken on 1.5.4).
- **`market_cap`** is tag-derived: `v_node` materialises it from
  `entity_tags` via a correlated MIN(tag) subselect (exactly one row per
  entity even with conflicting tags). SQLite side uses `market_cap_sql()` /
  JOIN. Columns were dropped 2026-07-28; live regression guards protect
  against stale-snapshot restores.

## 9. Live Caveats

1. **`relations` VIEW is read-only.** SQLite views aren't writable; all
   writers target `graph_edges`. (`doc/procedures/misc/*.md` may still show
   the old INSERT-into-relations pattern.)
2. **ATTACH is session-scoped** — DuckDB refuses to persist cross-engine
   ATTACHes; every `connect()` re-issues it. The `fin` alias is fresh per
   session; only the row→id materialisation is cached on disk.
3. **Read-only CHECKPOINT assumption** — backups open the `.duckdb`
   read-only then CHECKPOINT (valid on DuckDB ≥1.5; no online-backup API
   yet). A caught Error degrades to file+WAL copy with a WARNING — that
   WARNING on a known-good file is the signal the contract changed.
   Re-test on every version bump.
4. **SQLite WAL + `shutil.copy`** — copy via the backup API or
   `PRAGMA wal_checkpoint(TRUNCATE)` first; raw copy can miss WAL-resident
   tables.
5. **Version bumps** — onager/duckpgq-era quirks (stricter-than-ISO PGQ,
   string-key CSR segfaults, ID-collision merges) are history (see archive
   proposal). Current contract: onager tracks DuckDB; re-test
   `tests/test_onager_capabilities.py` + the CHECKPOINT assumption on bumps.

## 10. Decision Log

| # | Decision | Rationale |
|---|---|---|
| D1 | Path D (DuckDB over SQLite) | in-process, read-only attach, no server |
| D2 | SQLite sole writer | no 2PC; cascades handle rename/delete |
| D3 | `relations` → `graph_edges` (+ view alias) | single source of truth, backward compat |
| D4 | symmetric edges: one canonical row | halves undirected edge count |
| D5 | analytics: manual refresh + advisory qa gate | no hidden cost in hot paths |
| D6 | part_of/has_company keep two-row pattern | validator/parser compat |
| D7 | disk-cached DuckDB; CLI per-invocation, app long-lived | 3.5× warm connects |
| D8 | duckdb UNPINNED (was ==1.5.4 for duckpgq) | duckpgq gone; onager tracks releases |
| D9 | retire duckpgq; Onager for ALL algorithms (2026-08-14) | duckpgq had no 1.5.5 build |
| D10 | link-predict + voterank apply by default; node metrics opt-in `--apply` | hypotheses belong in the DB; bulk refresh is explicit |
| D11 | katz alpha pinned 1e-4 | Onager default diverges on the live graph |
| D12 | ppr not wrapped (Onager bug) | personalisation column ignored; restart hardcoded |
| D13 | every CLI metric opt-in `--apply` (reverses D10's link-predict/voterank default-apply) | uniform write switch; exploratory `--method`/`--edge-types` runs were silently replacing the persisted table |
| D14 | igraph stays a SECOND engine behind Onager, run from the pilot venv only (no igraph in `.venv`/pyproject); GPL-2.0-or-later cleared by the operator 2026-09-12, re-cleared without reservation same day (owner-operated repo) — pilot-venv split is now operational, not legal | keep-both: Onager keeps the validated Apache-2.0 SQL lanes; igraph covers Leiden/weighted centralities/weighted paths/max-flow where Onager has gaps (`helpers/graph/igraph_bridge.py`, ROUTING table); lazy import keeps `.venv` collection working; promotion of any lane (or of igraph into pyproject) needs its own proposal; pilot recipe carries duckdb 1.5.5 so `--apply` runs real |
| D15 | igraph integration DEFERRED — bridge stays pilot-gated, no consumers; trigger was the operator's working EasyGraph C++ source build, but the same-day re-test CLOSED the engine question: NO Easy-Graph stands (cpp betweenness broken on live data, louvain unwired; full log folded into doc/improvements/archive/graph/easygraph_cpp_readoption.md appendix) | igraph keeps every second-engine lane; what defers is prod wiring until a real consumer needs a lane (proposal §7 (b)/(c)) — wiring ahead of demand would mean maintaining an unused integration |
| D16 | igraph pilot RETIRED and deleted (`helpers/graph/igraph_bridge.py` + tests, operator decision 2026-09-13): the alternate-engine seat passes to the HGX hypergraph lane (hypergraph_incidence_hyx S7) — BSD-3, main-venv, `helpers/graph/hyper_communities.py`. | Lane disposition: Leiden superseded by hy-MMSBM overlapping communities; weighted centralities replaced by HGX higher-order lanes (`RW_stationary_state`, `s_betweenness`/`s_closeness` — s-walk semantics over incidence, a recorded semantics change); `maxflow_mincut` and directed weighted shortest-path RETIRED unserved (directed paths stay SQL BFS per D9). Onager keeps every dyadic SQL lane; reviving a dyadic weighted lane needs its own proposal |

## 11. File Layout

```text
helpers/graph/   query.py onager.py algorithms.py stats.py embeddings.py
                 derive_{co_mentions,events,insights,themes}.py
                 extract_relations.py _edge_writer.py
tests/           test_graph.py test_graph_disk.py test_onager_capabilities.py
                 test_integration_graph_algorithms.py test_graph_stats.py
                 test_api_graph*.py
doc/             graph_design.md (this file)
memory/          research.db(+wal)  graph.duckdb(+wal, gitignored)
snapshots/       parquet/ (git-tracked, restorable)  _schema.*.sql
db-backup/       *.snapshot.*.zst + *_backup.*.zst  (local scratch, gitignored)
```

Make targets: `graph-smoke` `graph-stats` `graph-algos` `graph-rebuild`
`update-extensions` `recompute-graph` `snapshot` `maint`.

---
### History (compressed)

- **v1 (2026-07-17):** design; Phase 1 migration `relations`→`graph_edges`
  + property-graph layer; Phases 2-3 (real edges, endpoints, temporal
  `as_of`, UI) landed through 2026-07-28. Version 1.5-1.9 changelogs (disk
  persistence, schema rebuild bundles, market_cap tag migration) live in
  git history.
- **2026-08-14:** duckpgq RETIRED (A-E) — algorithms → Onager, MATCH →
  plain JOINs, SHORTEST → recursive CTE, property graph deleted; NetworkX
  retired; duckdb unpinned. `doc/improvements/archive/graph/duckpgq_retirement.md`.
- **2026-08-14/15:** graph_algos proposal Phases 1-3 — link prediction
  (apply-by-default), whole-graph metrics (graph-stats + /api/graph/stats),
  extra centralities (harmonic/katz/laplacian/local-reaching/voterank).
  `doc/improvements/archive/graph/graph_algos.md`.
