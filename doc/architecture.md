# Architecture — FinData Knowledge Graph

The system **as it actually is** (updated 2026-08-15), flagging nothing
aspirational. Supersedes the old "Dual MCP" write-ups (that design never ran).

## 1. What this project is

A **FinData knowledge graph** — Indian-equity companies, sectors,
sub_sectors, super_sectors, and themes as a SQLite DB (`memory/research.db`)
synchronized 1:1 with a markdown vault (`findata/`). A Flask app (`app.py`)
serves the explorer UI + JSON API. Source material: Zerodha newsletters
OCR'd into markdown (OCR is external to this repo).

## 2. The operational path

```
Reports/*.pdf (gitignored)
  │  external OCR (upload pipeline removed 2026-08-02)
  ▼
findata/{Points_And_Figures,The_Chatter,The_PlotLines}/*.md  (inputs, gitignored)
  │  agent follows doc/procedures/markdown_parse.md
  │  (entities → tickers → note + DB row + graph_edges)
  ▼
findata/Companies|Sectors|Super_Sectors + memory/research.db   (the graph)
  │  validators + maintenance
  ▼
verify_notes ✓  database_integrity_check ✓  db_maint ✓  snapshot ✓
```

- **One database.** No second "Graph MCP" store, no sync daemon.
- **DB = source of truth for identity/relations; markdown = source of truth
  for content.** Kept in sync by the procedure; verified by the validators.
- **`memory/` is gitignored** — rebuilt via `make snapshot-restore` from the
  git-tracked Parquet snapshot under `snapshots/` (per-table .parquet +
  captured schema DDL; gzip copies under `db-backup/` are local-only scratch).

## 3. Repository layout

```
app.py                Flask: findata viewer + graph API (lazy-imports helpers.core.db,
                      helpers.graph.query, helpers.graph.algorithms)
memory/               research.db (SQLite, WAL) + graph.duckdb (cache) + embed_store.db (pooled vec/cache store) — gitignored
snapshots/            git-tracked Parquet snapshot (per-table + schema DDL) — restores memory/
db-backup/            local scratch: gzip snapshots + raw *_backup.* copies (gitignored)
findata/              the vault (see findata.md for layout & note format)
helpers/              core/ graph/ maintenance/ misc/ pdf/ validators/
doc/                  this file, schema.md, findata.md, okf.md, graph_design.txt,
                      procedures/, improvements/{pending,completed,archive}
tests/                pytest suite (~1.6k tests; qa gate = not-live subset)
frontend/             TypeScript UI sources
```

## 4. Data model

Authoritative: [`schema.md`](schema.md) (SQLite + DuckDB cache schemas).
Summary: 7 user tables + the FTS5 `note_search` + the `relations` VIEW —
`entities` (1,209 rows; 5 entity kinds: company 1068 / sub_sector 78 /
sector 42 / theme 12 / super_sector 9; PK `name`), `entity_tags` (note-tag
mirror), `graph_edges` (4,110 rows, 12 edge types), `events` (342),
`quotes` (2,564), `company_metrics` (1,731), `graph_analytics` (14 metrics;
written only by `make recompute-graph`). No `market_cap`/`index_membership`
columns — tag-only.

FKs are declared CASCADE and `helpers/core/db.py:connect()` **enables
`PRAGMA foreign_keys`** (so cascades fire there); a raw `sqlite3.connect()`
does not — stale children are caught by the validators. Graph engine and
the algorithm catalog: [`graph_design.txt`](graph_design.txt) (DuckDB
read-cache + Onager; duckpgq and NetworkX were retired 2026-08-14).

## 5. Filesystem ↔ DB sync contract

Enforced by the validators; the authoritative copy lives in
[`findata.md`](findata.md) §"Sync Rules" (filename = `normalized_name`.md,
PascalCase single-underscore, no `&`/`Ltd` suffixes, `file_path` resolves,
no duplicates/orphans, both-direction membership edges into `graph_edges`).
Canonical sectors are defined by `findata/Sectors/` (42); carve-outs are
checked before parent catch-alls during classification
(`parse_newsletter.py::guess_sector_for`).

## 6. Tooling — load-bearing

| Tool | Purpose |
|---|---|
| `doc/procedures/markdown_parse.md` | the ingestion procedure the agent follows |
| `doc/procedures/embeddings.md` | local-embeddings apply/pre-warm procedure + new-letter refresh model |
| `doc/procedures/script-search.md` | script metadata index (what each helper/test/make target is FOR; `script_query.py`) |
| `helpers/core/local_embedder.py` | the one embedder module (bge-small-en-v1.5; owns the BGE query/document prefix rule) |
| `helpers/core/parse_newsletter.py` | orchestrates ingestion Stages 0–3 + 5–6 (images, entities, tickers, DB writes, validation); Stage 4 (commentary lift) stays manual via `<slug>_enhancement_worklist.json` |
| `helpers/core/get_tickers.py` | name → NSE/BSE ticker via Yahoo (prefer `.NS` over `.BO`) |
| `helpers/core/frontmatter.py` | shared YAML-front-matter parsing (consolidated 2026-08 from 5 duplicate implementations) |
| `helpers/core/sync_tags.py` · `db.py` | rebuild `entity_tags` (`make sync-tags`) · connection layer |
| `helpers/graph/query.py` | DuckDB connection/cache + every pattern query + `semantic_neighbors` (consumed by `/api/graph/*`) |
| `helpers/graph/onager.py` | Onager algorithm wrappers (centralities, communities, link prediction, graph metrics) |
| `helpers/graph/algorithms.py` | `compute()` dispatcher + CLI (`make recompute-graph`; writes `graph_analytics`) |
| `helpers/graph/extract_relations.py` `derive_{co_mentions,themes,events,insights}.py` | edge/event/quote/metric producers (`make derive-*`) |
| `helpers/graph/stats.py` | `make graph-stats` human summary (incl. Onager structure section) |
| `helpers/validators/verify_notes.py` `static_checks.py` | note YAML/content/duplicates · syntax/tags/permalink/pin checks (`make static-checks`) |
| `helpers/misc/database_integrity_check.py` | registry-driven DB+cache integrity (`_CHECKS`; see schema.md for the check table) |
| `helpers/maintenance/db_maint.py` `maint.py` `snapshot_db.py` `rebuild_schema.py` | VACUUM/ANALYZE/backup/REINDEX · `maint-full` orchestrator (maint + sync-tags + recompute-graph + re-snapshot) · WAL-safe snapshots (+parquet L1) · canonical-DDL rebuild |
| `helpers/pdf/capture_newsletter_images.py` | inline images for the parse path |

**History:** three cleanup passes (Jun–Aug 2026) deleted the never-wired
"dual MCP" subsystem (~3.5k lines), all one-off `fix_*`/`migrate_*`
scripts, and `app.py`'s dead OCR upload pipeline (~740 lines; app.py is now
a pure findata graph server). Survivors form a layered DAG: `core/db.py`,
`validators/static_checks.py` (canonical vocabularies), `core/frontmatter.py`,
`graph/query.py` at the bottom; operational scripts above; no cycles. A
2026-08-14 pass retired duckpgq (→ Onager + plain SQL) and NetworkX.

## 7. Operational procedures

- **Ingest a newsletter** → `doc/procedures/markdown_parse.md`.
- **Re-apply / upgrade embeddings** → `doc/procedures/embeddings.md`.
- **Verify** → `make qa` (lint + types + deptry + static + pytest + notes +
  integrity + snapshot checks — the canonical gate).
- **Post-ingest** → `make maint-full`; routine → `make maint`; snapshot →
  `make snapshot`.
- **Entity ops** — rename: `helpers/maintenance/rename_entity.py` (atomic,
  FK-cascade); move sector: `move_sector.py`; delete: `DELETE FROM entities`
  (cascades) then drop the note and `make sync-tags`.
- Always fix issues traceable to the current run, then re-run validators.

## 8. Doc map

`architecture.md` (this file) · `schema.md` (DB + cache schemas, integrity
checks) · `findata.md` (vault, YAML, tags, sync rules) · `graph_design.txt`
(engine + algorithm catalog) · `procedures/markdown_parse.md` ·
`improvements/` (`pending.md`, `completed.md` numbered log, `archive/`
closed proposals — was `proposals/`).

## 9. Code & markdown search — codebase-memory-mcp

This repo is indexed by **codebase-memory-mcp** (project
`home-arun-Research-MCP-pdf-ocr-obsidian`, auto-index on). Prefer it over
`grep`/`find` for code/markdown/relationship search: it understands
structure (functions, classes, routes, callers/callees, Section nodes from
the vault — ~16.5k nodes / ~24k edges) and returns the containing
function/class ranked by importance.

| Task | Tool |
|---|---|
| architecture, clusters, hotspots | `get_architecture` |
| find symbol by name/semantics | `search_graph` (BM25 + regex + vector) |
| grep-style, deduped + ranked | `search_code` (~3.5× fewer tokens than raw grep) |
| read a known symbol | `get_code_snippet` (source + complexity props) |
| callers/callees/data-flow | `trace_path` |
| multi-hop / aggregations | `query_graph` (raw Cypher) |

**Cypher dialect (v0.9.0):** `= false`/`= true` not prefix `NOT`; no
`NOT (()-[:EDGE]->(n))` negation (collect TESTS edges and diff in the
caller); keep RETURN narrow + LIMIT low (resultBudget truncation). The MCP
surface is the 8 read tools above; management tools (`list_projects`,
`index_status`, `detect_changes`, …) are CLI-only:
`codebase-memory-mcp cli index_status '{"project":"…"}'`.

**Recurring hygiene audit** (each one `query_graph` call):
1. hot paths: `f.is_test = false AND f.transitive_loop_depth >= 3 ORDER BY …`
2. fan-in hubs: `count(DISTINCT caller)` over CALLS — chokepoints; diff vs test inventory for untested load-bearing code
3. duplication: `SIMILAR_TO` edges by jaccard + `search_graph(semantic_query=[…])`

Findings resolved via this audit: `search_ticker` cyclo 46→30 after deleting
66 lines of dead code (+18 tests); front-matter duplication consolidated
into `helpers/core/frontmatter.py`. Still-open candidates: the near-twin
`*_neighbors_bundle` / `_resolve_entity_*_or_404` helpers in `app.py`.

**Note:** this is a read-only analysis layer over the *codebase* — distinct
from the FinData entity graph served by DuckDB/Onager (`graph_design.txt`).

---
*Rewritten 2026-08-15 from the Jun 2026 version: §4 data model now points at
schema.md (scale/counters refreshed to live: 1,209 entities / 4,110 edges /
14 metrics); tooling table updated for the Onager era (duckpgq + NetworkX
retired); FK note corrected (db.py enables the pragma); §9 doc statuses
folded into the doc map; codebase-memory-mcp section compressed (dialect
notes + audit patterns kept, worked examples dropped — they were
illustrative only).*
