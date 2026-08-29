# FinData Knowledge Graph

A knowledge graph of Indian listed companies, sectors and investment themes,
built from a corpus of OCR'd market newsletters — stored as a synchronized
pair of **SQLite** (`memory/research.db`) and an **Obsidian-style markdown
vault** (`findata/`), with a **DuckDB + Onager** graph engine and a **Flask**
JSON API / explorer UI on top.

| | |
|---|---|
| **Entities** | 1,517 — 1,063 companies · 205 institutions · 108 editions · 42 sectors · 78 sub-sectors · 9 super-sectors · 12 themes |
| **Edges** | 17,022 across 15 semantic types (`part_of`, `supplies`, `jv`, `competes`, `co_mentioned_in`, `acquired`, `semantic_peer`, `invested_in`, …) |
| **Derived data** | 357 events · 2,607 executive quotes · 1,794 financial metrics · 14 persisted graph-metric kinds |
| **Notes** | 1,226 tracked markdown notes, full-text + vector searchable |
| **Tests** | 2,590 across 127 modules — unit / integration / fuzz / perf / live gates |

Source material is a family of market newsletters (Points & Figures, The
Chatter, The PlotLines), converted to markdown **inside this repo**
(`helpers/pdf/pdf_conv_md.py`): a local-first no-OCR engine
(pymupdf4llm — digital PDFs, 99.7–99.9% word coverage) with a
Paddle PP-StructureV3 API fallback for scanned pages, plus a
post-conversion self-check (`verify_extraction`) that cross-validates the
markdown against the source pages. The original corpus was OCR'd with
[GLM-OCR](https://ocr.z.ai) before the in-repo engine existed. Everything
after the markdown arrives lives here: ingestion into entities, tickers,
relations, events, quotes and metrics; validators that keep DB and vault in
sync; graph analytics; content-addressable search (docs · notes · code); and
the serving layer.

## How it fits together

```
Reports/*.pdf ──(pdf_conv_md.py: local pymupdf4llm ─┬─>  findata/{Points_And_Figures,The_Chatter,The_PlotLines}/*.md
             Paddle PP-StructureV3 fallback on scans)┘         inputs, gitignored
        │                        │
        │                        └── verify_extraction.py — per-page recall, md↔json
        │                            consistency, number/wikilink audit (FAIL exits 1)
        │  doc/procedures/markdown_parse.md  (the ingestion procedure)
        │  helpers/core/parse_newsletter.py  (Stages 0–3, 5–6 orchestration)
        ▼
findata/Companies|Sectors|Super_Sectors   +   memory/research.db (SQLite, WAL)
the vault (content source of truth)           entities · graph_edges · entity_tags
                                              events · quotes · company_metrics
                                              graph_analytics · note_search (FTS5+vec)
        │
        ▼  reads only
memory/graph.duckdb  — DuckDB disk cache: v_node + one e_* table per edge type
        ├── pattern queries, shortest paths (BFS), cycles … plain SQL
        └── centralities, communities, link prediction … Onager extension
        │
        ├── content-addressable search sidecars (SQLite FTS5 + vec0):
        │     doc/ corpus · note embeddings · script metadata — `make search-fresh`
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
| `findata/` | the markdown vault — 1,226 tracked notes (+ gitignored OCR inputs) |
| `helpers/pdf/` | `pdf_conv_md` (PDF → markdown), `pdf_local` (local engine), `verify_extraction` (post-conversion self-check) |
| `helpers/core/` | `parse_newsletter`, `get_tickers` (NSE/BSE via Yahoo), `frontmatter`, `sync_tags`, `db` |
| `helpers/graph/` | `query` (cache + pattern queries), `onager`, `algorithms`, `derive_*`, `extract_relations`, `embeddings`, `stats` |
| `helpers/validators/` | `verify_notes` (notes ↔ DB), `static_checks` (syntax/tags/permalinks) |
| `helpers/maintenance/` | `db_maint`, `snapshot_db` (zstd Parquet + zstd snapshots), `rebuild_schema`, `rename_entity`, `move_sector`, `rebuild_{doc,note,script}_search` |
| `helpers/misc/` | `database_integrity_check`, `doc_query` (doc/ knowledge index), `script_query` (code-surface index) |
| `doc/` | architecture, schema, vault spec, graph design, procedures, improvement log |
| `tests/` | 127 pytest modules (2,590 tests) + conftest, fixtures, perf-benchmark + gate runners |
| `frontend/` | TypeScript sources; built bundle is committed to `static/` so serving stays Node-free |
| `Mojo/` | SIMD kernel pilot (`src/bench` kernels + TestSuite tests; `make mojo-build`/`mojo-bench`/`mojo-test` — machinery in `Makefile.mojo`, pyproject `mojo` extra). Deliberately NOT wired into `make perf` |
| `memory/`, `db-backup/` | runtime DB + local zstd backup/snapshot scratch — **gitignored**, see Quickstart |
| `snapshots/` | **git-tracked** Parquet snapshot of both DBs + schema DDL (`make snapshot-restore` rebuilds `memory/` from it) |

## Quickstart

Requires Python ≥ 3.14 and [uv](https://docs.astral.sh/uv/). Node is optional
(only to rebuild the frontend bundle).

```bash
uv sync --all-extras          # runtime + dev dependencies

# The database is not in git as a live file; the git-tracked Parquet
# snapshot under snapshots/ is the restorable state:
make snapshot-restore          # rebuilds memory/*.db from snapshots/parquet/
# (byte-exact local alternative, if db-backup/ has fresh zstd copies:
#  for f in db-backup/*_backup.*.zst; do zstd -dc "$f" > "memory/$(basename "${f%.zst}")"; done)
# …or start from an empty canonical schema:
# uv run python3 helpers/maintenance/rebuild_schema.py

make graph-rebuild            # (re)build the DuckDB cache from SQLite
make qa                       # full gate: lint + types + deptry + static + tests + validators
                               # (qa/integration/advisory append timestamped run tables to
                               #  qa_report.txt / integration_report.txt / advisory_report.txt,
                               #  perf-style — see perf_report.txt)
uv run python3 app.py         # http://localhost:5200   (FLASK_PORT to override)
```

Newsletter ingestion follows `doc/procedures/markdown_parse.md`: entities →
tickers → note + DB row + membership edges → validators → `make maint-full`.

## Graph engine

`memory/graph.duckdb` is a read-only cache over SQLite (schema v13: `v_node`
projection of all entity kinds + one table per edge type). Warm connects
skip materialisation (~3.5× faster); invalidation is manual
(`make graph-rebuild`) and drift is caught by `make qa`
(`check_cache_consistency`). Algorithms run on
[Onager](https://github.com/duckdb/community-extensions) — degree, PageRank,
betweenness, closeness, eigenvector, Katz, harmonic, Laplacian, Louvain, WCC,
clustering, local-reaching, link prediction, VoteRank — dispatched by
`helpers/graph/algorithms.py`; every CLI write is opt-in `--apply` (dry-run by
default). Cross-process readers (the parallel `make advisory` steps, the Flask
app) connect `read_only=True` so N readers coexist; only the rebuild path
takes the write lock. duckpgq and NetworkX were retired in Aug 2026.

## Search & embeddings

Three content-addressable indexes, each SQLite FTS5 (BM25) + local
`bge-small-en-v1.5` vectors in a shared vec0 sidecar (hybrid cosine):

| Index | Corpus | Query |
|---|---|---|
| `doc_search` | all of `doc/` (66 files, 519 sections) — design/decision/history knowledge | `helpers/misc/doc_query.py "…"`, `GET /api/docs/search` |
| `note_search` | every `findata/` note (1,224 docs) | `GET /api/search?hybrid=true`, `GET /api/graph/similar/<note>` |
| `script_search` | every `helpers/**` script + test module + make target (198 units) | `helpers/misc/script_query.py "…"` |

Freshness is gated: `make search-fresh` (exit 1 on drift, `APPLY=1` to
refresh) and three advisory-gate rows. Operators:
`doc/procedures/{doc-search,script-search,embeddings}.md`.

## API surface (excerpt)

| Route | Returns |
|---|---|
| `GET /api/entities` · `/api/search` · `/api/stats` · `/api/sectors` | listings, search, aggregates |
| `GET /api/entity/<name>` · `/api/events/<name>` | note + DB bundle, event timeline |
| `GET /api/graph/neighbors/<name>` | ego bundle: sector, peers, JV, siblings, M&A, suppliers, customers (`?as_of=`) |
| `GET /api/graph/peers/<name>` · `/api/graph/sector/<name>` | peer competitors, sector members |
| `GET /api/graph/shortest?a=&b=` | shortest path (BFS, temporal `as_of`, `max_hops` ≤ 8) |
| `GET /api/graph/similar/<path>` | notes nearest a note by embedding cosine (`?k=`, `?doc_type=`) |
| `GET /api/graph/edition_companies?edition=` | companies nearest an edition note (`?k=`) |
| `GET /api/graph/semantic/<name>` | semantic nearest neighbours (`?k=`, `?cross_sector=`) |
| `GET /api/graph/metrics/<metric>` | persisted `graph_analytics` reader |
| `GET /api/graph/stats` · `/co-mentions` · `/bridges` · `/edges-by-year` | aggregates + batch reports |
| `GET /api/graph/cloud` · `/suggestions` · `/near-duplicates` | graph-stat cloud, link-prediction suggestions, duplicate-note tripwire |
| `GET /api/analytics/<report>` | read-only analytics over the git-tracked Parquet snapshot |
| `GET /api/docs` · `/docs/search` · `/docs/content` | the doc/ knowledge index over HTTP |
| `POST /api/graph/refresh` | rebuild cache + reset connection |

## Make targets (selection — `make help` for all)

| Target | Purpose |
|---|---|
| `make qa` | the canonical gate: lint, types, deptry, static checks, tests, note + DB integrity, snapshot round-trip |
| `make test` · `integration` · `live-invariants` · `fuzz` · `perf` | tiered test entry points |
| `make advisory` | non-gating parallel checks: ty-on-tests, live invariants, frontend, graph algos, analytics, suggestions, integration, lint-audit (appends advisory_report.txt) |
| `make search-fresh` | check ALL search indexes for drift (`APPLY=1` refreshes; also advisory rows) |
| `make static-checks` · `sync-tags` · `graph-stats` | fast hygiene + summaries |
| `make derive-relations` · `derive-themes-rebuild` · `derive-events` · `derive-insights` · `metrics-rebuild` | edge/event/quote/metric producers (`metrics-rebuild` = yfinance refresh) |
| `make graph-rebuild` · `recompute-graph` · `graph-algos` | cache rebuild · persist all metrics · dry-run smoke |
| `make analytics` · `suggest-relations` · `near-duplicates` · `triage-relations` | read-only Parquet reports · link-prediction suggestions · rename tripwire · pending-edge triage |
| `make snapshot` · `snapshot-check` · `maint` · `maint-full` | versioned zstd snapshots + git-tracked Parquet, VACUUM/backup, post-ingest cleanup (PRE_FULL + TIER1 + TIER2 — `procedures/maintenance.md`) |

## Vault & note format

Company notes carry YAML front matter (`title`, `type`, `ticker`, `sector`,
`market_cap`, `normalized_name`, `permalink`, `tags`, dates) with fixed body
sections (Overview → Financial Profile → Segments → Management → Key
Insights). Note `tags:` are mirrored into `entity_tags` by
`make sync-tags`; nine namespaces are mirrored (`entity_type/`,
`sector/`, `market_cap/`, `subsector/`, `holding_company/`, `geography/`,
`business_model/`, `risk_investment/`, `investment_theme/`). Canonical sectors are defined by
`findata/Sectors/` (42). Full spec: [`doc/findata.md`](doc/findata.md).

## Documentation

| Doc | Scope |
|---|---|
| [`doc/architecture.md`](doc/architecture.md) | system overview, operational path, tooling map |
| [`doc/schema.md`](doc/schema.md) | SQLite + DuckDB cache schemas, integrity-check registry |
| [`doc/findata.md`](doc/findata.md) | vault layout, YAML/tag spec, sync rules |
| [`doc/graph_design.txt`](doc/graph_design.txt) | graph engine, algorithm catalog, decision log |
| [`doc/procedures/markdown_parse.md`](doc/procedures/markdown_parse.md) | the newsletter ingestion procedure |
| [`doc/procedures/embeddings.md`](doc/procedures/embeddings.md) | local embeddings & note-search: apply procedure, pre-warm, refresh model |
| [`doc/procedures/doc-search.md`](doc/procedures/doc-search.md) | the doc/ knowledge index: build, refresh, query (API + CLI) |
| [`doc/procedures/script-search.md`](doc/procedures/script-search.md) | the code-surface index: script/test/make metadata search |
| [`doc/improvements/`](doc/improvements/) | numbered completion log + archived proposals |

## Provenance

This repository began as a Mistral-OCR PDF→markdown pipeline
(`pdf-markdown-ocr.ipynb` + Flask upload app); that layer was removed at the
FinData base commit. The corpus itself was OCR'd with
[GLM-OCR](https://ocr.z.ai); a later pass re-OCR'd two issues with Paddle
PP-StructureV3, and since Aug 2026 the repo converts PDFs itself
(local-first pymupdf4llm engine + Paddle fallback — see *How it fits
together*). The upstream history is preserved below it.
