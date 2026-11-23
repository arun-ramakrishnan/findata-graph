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

```text
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
  captured schema DDL; zstd copies under `db-backup/` are local-only scratch).

## 3. Repository layout

```text
app.py                Flask: findata viewer + graph API (lazy-imports helpers.core.db,
                      helpers.graph.query, helpers.graph.algorithms)
memory/               research.db (SQLite, WAL; frozen anchor/rollback) + research.duckdb
                      (canonical DuckDB store; selector via helpers.core.db_engine) +
                      graph.duckdb (graph cache) + embed_store.duckdb/.db (pooled
                      embedding cache) + note_search.duckdb + markus.db + wordfreq.db +
                      wip_sections.db — gitignored, restored via snapshot
snapshots/            git-tracked snapshot: parquet/sqlite + parquet/duckdb (per-table zstd
                      parquet + schema DDL) — restore target for memory/
db-backup/            local scratch: zstd snapshots + *_backup.* recovery copies (gitignored)
findata/              the vault (see findata.md for layout & note format)
helpers/              core/ graph/ maintenance/ misc/ pdf/ validators/ bench/ analytics/
doc/                  design/, procedures/, improvements/{pending,completed,archive},
                      templates/; diagrams/ = Archify JSON IR + rendered HTML
tests/                pytest suite (qa gate = not-live subset)
frontend/             TypeScript UI sources
rust/                 Rust crates: findata_graph_kernels, quack-query, embed_cache, etc.
mojo/                 Mojo graph-kernel experiments/bridges (opt-in, parity-gated)
models/               local model artifacts (GGUF/embeddings/onager dist; gitignored/symlinked)
```

## 4. Data model

Authoritative: [`db_schema.md`](db_schema.md) (SQLite + DuckDB schemas;
machine-lineage columns; cross-store layouts). Treat row counts as live DB
facts, not constants — query `memory/research.db`/`research.duckdb` for current
values.

Core stores:

- `entities` — PK `name`; entity kinds include company/institution/edition/
  sub_sector/sector/country/theme/super_sector (see schema for live enum/kinds).
- `entity_tags` — note-tag mirror.
- `graph_edges` — typed pairwise edges (`graph_edge_types.py` is the registry).
- `events`, `quotes`, `company_metrics` — extracted fact tables.
- `graph_analytics` — derived analytics; written only by `make recompute-graph`.
- `hyper_edges` + `hyper_incidences` — star incidence store for n-ary families;
  family vocabulary is code-side (`helpers/graph/hyperedge_types.py`).
- `provenance_agents` — S1 agent registry; FK-bearing rows must use a registered
  agent id (never `''`).

Research-store engine split: `memory/research.db` is the SQLite anchor/rollback
artifact; `memory/research.duckdb` is the canonical DuckDB store selected by
`helpers/core/db_engine.py` (`FINDATA_RESEARCH_ENGINE`, `FINDATA_RESEARCH_DB_PATH`).
New DB access should go through `helpers.core.db.connect()` / funnel helpers;
raw `sqlite3.connect()` is reserved for narrow, justified paths.

Derived/frozen/moving data follows the §10 format standard
(parquet-zstd at rest, Arrow in flight).

FKs are declared CASCADE and `helpers/core/db.py:connect()` **enables
`PRAGMA foreign_keys`** (so cascades fire there); a raw `sqlite3.connect()`
does not — stale children are caught by the validators. Graph engine and
the algorithm catalog: [`graph_design.md`](graph_design.md) (DuckDB
read-cache + Onager; duckpgq and NetworkX were retired 2026-08-14).
Atomicity note: `helpers/core/atomic.py` + the lineage hooks in
`db.py:conn_attributes` (stamped on the live conn); DB writes via the
helpers put a matching `machine_*` stamp on the row when present.

## 5. Filesystem ↔ DB sync contract

Updated 2026-11-22 (§3/§6, see commit ce2cf8809).

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
| `doc/procedures/markdown_parse.md` | ingestion procedure the agent follows |
| `doc/procedures/embeddings.md` / `helpers/core/local_embedder.py` | embeddings apply/refresh; one embedder module (BGE prefix rule) |
| `doc/procedures/search.md` | doc/script/note/convo/memory/git/gate search surfaces and CLIs |
| `doc/procedures/maintenance.md` | routine maintenance doctrine (PRE_FULL/TIER1/TIER2, recovery vs snapshot) |
| `helpers/core/db.py`, `helpers/core/db_engine.py` | DB connections; research-store selector/funnel (`FINDATA_RESEARCH_ENGINE`) |
| `helpers/core/parse_newsletter.py`, `get_tickers.py`, `frontmatter.py`, `sync_tags.py` | ingestion orchestration, ticker lookup, frontmatter, tag mirror |
| `helpers/graph/query.py`, `algorithms.py`, `onager.py`, `stats.py` | graph read/cache/query layer, analytics dispatcher, Onager wrappers, stats |
| `helpers/graph/extract_relations.py`, `derive_*.py` | edge/event/quote/metric/insight derivation producers |
| `helpers/validators/verify_notes.py`, `static_checks.py` | note/content checks and static lint/gate checks |
| `helpers/misc/database_integrity_check.py`, `helpers/maintenance/db_maint.py`, `maint.py`, `snapshot_db.py`, `rebuild_schema.py` | integrity, maintenance, snapshots/restore (SQLite+DuckDB parquet), schema rebuild |
| `helpers/core/duckdb_shared.py`, `helpers/misc/quack_server_child.py`, `quack_health.py` | DuckDB 2.0 quack wire routing: one resident server per store, shared discovery/health |
| `helpers/graph/findata_graph_kernels` / `rust/` | Rust kernels for hot graph/compute paths (PyO3 extension; parity-gated adoption) |
| `mojo/` + `helpers/graph/mojo_*` bridges | Mojo experiments/kernels; opt-in, parity-gated, never a silent default swap |
| `helpers/misc/llama_servers.sh`, `vendor/llamacpp/` | local llama.cpp model servers (embeddings/OCR lanes; provenance-pinned) |
| `helpers/analytics/bili_*` | billion-context session analytics/tier-watch tooling |
| `doc/procedures/diagrams.md` + `doc/design/diagrams/*` | Archify IR/HTML diagrams; validate with Archify 2.17 CLI (`$TMPDIR/archify_ref/archify/bin/archify.mjs`) |

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
  FK-cascade; DuckDB 2.0 gap handling per §6); move sector: `move_sector.py`;
  delete: `DELETE FROM entities` (cascades) then drop the note and `make
  sync-tags`.
- Always fix issues traceable to the current run, then re-run validators.

## 8. Doc map

`architecture.md` (this file) · `schema.md` (DB + cache schemas, integrity
checks) · `findata.md` (vault, YAML, tags, sync rules) · `graph_design.md`
(engine + algorithm catalog) · `procedures/markdown_parse.md` ·
`templates/` (start-from skeletons for new Python helpers, Mojo sources,
and proposals — gated by `tests/test_templates.py`) ·
`improvements/` (`pending.md`, `completed.md` numbered log, `archive/`
closed proposals — was `proposals/`).

Diagrams: `diagrams/system_overview.{json,html}` — evidence-marked
storage-topology map compiled from the JSON IR by the archify pipeline
(see `improvements/archive/tooling/archify_diagram_pipeline.md`); the HTML is
regenerable, the JSON IR is the committed source. Re-render when an arc
changes the depicted chain.

## 9. Code & markdown search — ripwire

Structural code discovery runs through **ripwire** (offline single
binary: deterministic tree-sitter call graph + BM25/PageRank, warm
~0.2–0.5 s; posture + standing flags in `AGENTS.md`, adoption record in
`improvements/archive/tooling/ripwire_adoption.md`). Map before read:
locate with ripwire, then read only what it names. It re-crawls per run
with per-file content-hash re-parse, so answers are worktree-current —
including uncommitted edits; a zero means "none found", never "index
may be stale".

| Task | Tool |
|---|---|
| orient on a task | `ripwire . --for="<task in words>"` (ranked signatures) |
| callers / transitive blast radius / read-write sites | `--callers=SYM` / `--impact=SYM` / `--uses=SYM` |
| literal or regex, enclosing symbol | `--grep=STR --grep-in=any --legend=compact` (standing flags) |
| a symbol's body + callee signatures | `--expand=SYM` |
| tests reaching a changed file/symbol | `--affected=F1,F2\|SYM` |
| docs: ranked recall, backtick mentions, stale anchors | `--recall` / `--mentions=SYM` / `--doc-drift` |
| intent (what is it for, what runs it, what tests it) | `doc_query` / `script_query` — unchanged |

**Code-health note (open):** the near-twin `*_neighbors_bundle` /
`_resolve_entity_*_or_404` helpers in `app.py` remain an unresolved
duplication candidate (surfaced by the 2026-08 graph audit).

**Note:** ripwire is a read-only analysis layer over the *codebase* —
distinct from the FinData entity graph served by DuckDB/Onager
(`graph_design.md`).

---
*Rewritten 2026-08-15; refreshed 2026-11-23 for DuckDB/quack/Rust/Mojo/llama/snapshot-parity/diagram updates. Earlier note: §4 data model now points at
schema.md (scale/counters refreshed to live: 1,209 entities / 4,110 edges /
14 metrics); tooling table updated for the Onager era (duckpgq + NetworkX
retired); FK note corrected (db.py enables the pragma); §9 doc statuses
folded into the doc map. §9 rewritten 2026-09-08 for the ripwire
adoption — the retired graph-index section is gone from live docs; its
dialect notes and audit patterns live on in the archived proposals
(ripwire_adoption.md, script_metadata_search.md).*

## 10. Data format standard — parquet(zstd) at rest, Arrow in flight

Operator directive 2026-09-13, effective repo-wide. First applied on the
hypergraph incidence lane (proposal
`improvements/archive/graph/hypergraph_incidence_hyx.md` S18); this section
is the enforceable statement for everything else.

| Tier | Standard | Why |
|---|---|---|
| At rest (any data stored to / read from disk) | **parquet, zstd codec** | columnar, restorable, git-friendly; the 45-table snapshot lane already emits it (`snapshot_db.py`: `pq.write_table(..., compression="zstd")` — codec verified on disk) |
| In flight / on-the-wire (data moving between components) | **Arrow tables** | DuckDB understands Arrow natively (`fetch_arrow_table` / `from_arrow` / `register`, zero-copy); pyarrow reads parquet(zstd) straight into Arrow, so one representation serves both tiers |

Rules:

1. New on-disk data artifacts default to parquet(zstd). Existing writers
   converge to it when touched.
2. Inter-component data movement defaults to Arrow. An engine without an
   Arrow API takes `to_pylist()` at its construction boundary (HGX does).
3. CSV/JSON are **transient serializations** produced on consumer
   demand — never stored artifacts. Exports keep the at-rest standard
   too: the HIF export's canonical form is three zstd parquet tables
   (HIF column names, memo §9.4 mapping); JSON is emitted from them
   on demand.
4. `db-backup/*.zst` (whole-SQLite-file zstd) is a DB-file backup class,
   not table data — unchanged by this standard.
5. `memory/research.db` (SQLite) stays the live source of truth; the
   standard governs derived, frozen, and moving data — not the
   operational DB.

Reference application (hyper lane): one Arrow loader with two sources —
live SQLite via DuckDB's sqlite scanner, or the zstd parquet snapshot
directly — feeding HGX (`to_pylist` boundary) and DuckDB
(`from_arrow`, SQL-over-incidence in flight with no `h_*` materiality).
