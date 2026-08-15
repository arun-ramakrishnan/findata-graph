# FinData Knowledge Graph

A knowledge graph of Indian listed companies, sectors and investment themes,
built from a corpus of OCR'd market newsletters — stored as a synchronized
pair of **SQLite** (`memory/research.db`) and an **Obsidian-style markdown
vault** (`findata/`), with a **DuckDB + Onager** graph engine and a **Flask**
JSON API / explorer UI on top.

| | |
|---|---|
| **Entities** | 1,209 — 1,068 companies · 42 sectors · 78 sub-sectors · 9 super-sectors · 12 themes |
| **Edges** | 4,110 across 12 semantic types (`part_of`, `supplies`, `jv`, `competes`, `co_mentioned_in`, `acquired`, …) |
| **Derived data** | 342 events · 2,564 executive quotes · 1,731 financial metrics · 14 persisted graph metrics |
| **Tests** | 1,610 across 80 modules — unit / integration / fuzz / perf / live gates |

Source material is a family of market newsletters (Points & Figures, The
Chatter, The PlotLines), OCR'd to markdown outside this repo with
[GLM-OCR](https://ocr.z.ai). Everything
after the markdown arrives lives here: ingestion into entities, tickers,
relations, events, quotes and metrics; validators that keep DB and vault in
sync; graph analytics; and the serving layer.

## How it fits together

```
Reports/*.pdf ──(GLM-OCR)──>  findata/{Points_And_Figures,The_Chatter,The_PlotLines}/*.md
                                    inputs, gitignored
        │
        │  doc/procedures/markdown_parse.md  (the ingestion procedure)
        │  helpers/core/parse_newsletter.py  (Stages 0–3, 5–6 orchestration)
        ▼
findata/Companies|Sectors|Super_Sectors   +   memory/research.db (SQLite, WAL)
the vault (content source of truth)           entities · graph_edges · entity_tags
                                              events · quotes · company_metrics
                                              graph_analytics · note_search (FTS5)
        │
        ▼  reads only
memory/graph.duckdb  — DuckDB disk cache: v_node + 12 e_* edge tables
        ├── pattern queries, shortest paths, cycles … plain SQL / recursive CTEs
        └── centralities, communities, link prediction … Onager extension
        │
        ▼
app.py — Flask: /findata explorer UI + /api/graph/* JSON API
```

Three invariants hold the design together:

1. **SQLite is the sole writer.** DuckDB never writes edges; analytics results
   are written back to SQLite (`graph_analytics`) by
   `make recompute-graph` only. FK cascades propagate entity renames/deletes.
2. **Markdown is the content source of truth; the DB is the identity/relation
   source of truth.** Validators enforce the sync contract (note filename =
   `normalized_name`.md, `file_path` resolves, both directions of every
   membership edge present, no orphans/duplicates).
3. **No second store, no sync daemon.** One database, derived caches, checked
   gates (`make qa`).

## Repository layout

| Path | Contents |
|---|---|
| `app.py` | Flask server: findata viewer + graph API (case-insensitive entity resolution) |
| `findata/` | the markdown vault — 1,122 tracked notes (+ gitignored OCR inputs) |
| `helpers/core/` | `parse_newsletter`, `get_tickers` (NSE/BSE via Yahoo), `frontmatter`, `sync_tags`, `db` |
| `helpers/graph/` | `query` (cache + pattern queries), `onager`, `algorithms`, `derive_*`, `extract_relations`, `embeddings`, `stats` |
| `helpers/validators/` | `verify_notes` (notes ↔ DB), `static_checks` (syntax/tags/permalinks) |
| `helpers/maintenance/` | `db_maint`, `snapshot_db` (gzip + Parquet), `rebuild_schema`, `rename_entity`, `move_sector` |
| `helpers/misc/` | `database_integrity_check` — registry-driven DB + cache checks |
| `doc/` | architecture, schema, vault spec, graph design, procedures, improvement log |
| `tests/` | 80 pytest modules (1,610 tests) + conftest, fixtures, perf-benchmark runner |
| `frontend/` | TypeScript sources; built bundle is committed to `static/` so serving stays Node-free |
| `memory/`, `db-backup/` | runtime DB + snapshots — **gitignored**, see Quickstart |

## Quickstart

Requires Python ≥ 3.14 and [uv](https://docs.astral.sh/uv/). Node is optional
(only to rebuild the frontend bundle).

```bash
uv sync --all-extras          # runtime + dev dependencies

# The database is not in git. Either restore local snapshots:
mkdir -p memory
gzip -dc db-backup/research.snapshot.db.gz   > memory/research.db
gzip -dc db-backup/graph.snapshot.duckdb.gz  > memory/graph.duckdb
# …or start from an empty canonical schema:
# uv run python3 helpers/maintenance/rebuild_schema.py

make graph-rebuild            # (re)build the DuckDB cache from SQLite
make qa                       # full gate: lint + types + deptry + static + tests + validators
uv run python3 app.py         # http://localhost:5200   (FLASK_PORT to override)
```

Newsletter ingestion follows `doc/procedures/markdown_parse.md`: entities →
tickers → note + DB row + membership edges → validators → `make maint-full`.

## Graph engine

`memory/graph.duckdb` is a read-only cache over SQLite (schema v9: `v_node`
projection of all five entity kinds + one table per edge type). Warm connects
skip materialisation (~3.5× faster); invalidation is manual
(`make graph-rebuild`) and drift is caught by `make qa`
(`check_cache_consistency`). Algorithms run on
[Onager](https://github.com/duckdb/community-extensions) — degree, PageRank,
betweenness, closeness, eigenvector, Katz, harmonic, Louvain, WCC, clustering,
local-reaching, link prediction, VoteRank — dispatched by
`helpers/graph/algorithms.py`; every CLI write is opt-in `--apply` (dry-run by
default). duckpgq and NetworkX were retired in Aug 2026.

## API surface (excerpt)

| Route | Returns |
|---|---|
| `GET /api/entities` · `/api/search` · `/api/stats` · `/api/sectors` | listings, search, aggregates |
| `GET /api/entity/<name>` · `/api/events/<name>` | note + DB bundle, event timeline |
| `GET /api/graph/neighbors/<name>` | ego bundle: sector, peers, JV, siblings, M&A, suppliers, customers (`?as_of=`) |
| `GET /api/graph/peers/<name>` · `/api/graph/sector/<name>` | peer competitors, sector members |
| `GET /api/graph/shortest?a=&b=` | shortest path (recursive CTE, temporal `as_of`) |
| `GET /api/graph/metrics/<metric>` | persisted `graph_analytics` reader |
| `GET /api/graph/stats` · `/co-mentions` · `/bridges` · `/edges-by-year` | aggregates + batch reports |
| `POST /api/graph/refresh` | rebuild cache + reset connection |

## Make targets (selection — `make help` for all)

| Target | Purpose |
|---|---|
| `make qa` | the canonical gate: lint, types, deptry, static checks, tests, note + DB integrity, snapshot round-trip |
| `make test` · `integration` · `test-live` · `fuzz` · `perf` | tiered test entry points |
| `make static-checks` · `sync-tags` · `graph-stats` | fast hygiene + summaries |
| `make derive-relations` · `derive-themes-rebuild` · `derive-events` · `derive-insights` · `metrics-rebuild` | edge/event/quote/metric producers (`metrics-rebuild` = yfinance refresh) |
| `make graph-rebuild` · `recompute-graph` · `graph-algos` | cache rebuild · persist all metrics · dry-run smoke |
| `make snapshot` · `snapshot-check` · `maint` · `maint-full` | versioned gzip + Parquet snapshots, VACUUM/backup, post-ingest cleanup |

## Vault & note format

Company notes carry YAML front matter (`title`, `type`, `ticker`, `sector`,
`market_cap`, `normalized_name`, `permalink`, `tags`, dates) with fixed body
sections (Overview → Financial Profile → Segments → Management → Key
Insights). Note `tags:` are mirrored into `entity_tags` by
`make sync-tags`; only `entity_type/`, `sector/`, `market_cap/`,
`subsector/` namespaces are mirrored. Canonical sectors are defined by
`findata/Sectors/` (42). Full spec: [`doc/findata.md`](doc/findata.md).

## Documentation

| Doc | Scope |
|---|---|
| [`doc/architecture.md`](doc/architecture.md) | system overview, operational path, tooling map |
| [`doc/schema.md`](doc/schema.md) | SQLite + DuckDB cache schemas, integrity-check registry |
| [`doc/findata.md`](doc/findata.md) | vault layout, YAML/tag spec, sync rules |
| [`doc/graph_design.txt`](doc/graph_design.txt) | graph engine, algorithm catalog, decision log |
| [`doc/procedures/markdown_parse.md`](doc/procedures/markdown_parse.md) | the newsletter ingestion procedure |
| [`doc/improvements/`](doc/improvements/) | numbered completion log + archived proposals |

## Provenance

This repository began as a Mistral-OCR PDF→markdown pipeline
(`pdf-markdown-ocr.ipynb` + Flask upload app); that layer was removed at the
FinData base commit and the repo now starts from the OCR'd markdown — the
corpus itself was OCR'd with [GLM-OCR](https://ocr.z.ai). The upstream
history is preserved below it.
