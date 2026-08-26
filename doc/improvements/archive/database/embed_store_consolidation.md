# Proposal: Single embed store — consolidate the vector sidecars

**Date**: 2026-08-26
**Status**: EXECUTED 2026-08-27 (steps 2–7 same sitting as review folding).
Live migration pooled **3,211 cache rows** (legacy-research 2,327 / doc
600 / script 284) into `memory/embed_store.db` (WAL, integrity ok) and
rebuilt `note_search_vec` at **1,224 rows** via the production write path;
legacy sidecars renamed `*.migrated.bak` (never deleted). Backup streams
repointed (`db_maint._backup_embed_store` twin + gzip stream named after
the store); rds/rss last-good backups narrowed to index-only. Suites
green: vec/cache/migration/rebuilders/db_maint/snapshot/api-search +
full-tree ruff. Revisit triggers in §4C stand.
**Depends on**: `helpers/core/vec_search.py` (A1 sidecar), `helpers/core/embed_cache.py`
(shared cache), `sqlite-vec` (already installed); independent of Relations/GF arcs.
**Trigger**: user observation 2026-08-26 — *"there are 3+ use cases [of a vector
sidecar] in memory … inconvenient as they are not sitting in one db"* — plus two
consecutive backup-stream catches (`make snapshot` skipped `research.db_vec.db`;
db-backup carried only its `.gz`, not the raw twin). Both catches were pure
fallout of the multi-file split, not of any single design decision.

---

## 1. Problem statement

Every embedding-derived artifact lives in its own gitignored file under
`memory/`, and **the same logical thing — the content-hash embed cache — exists
as three physically separate copies** behind mechanically derived filenames.
The operational cost is repeated and real:

1. Backup plumbing must know about every pair independently. The
   `<main>_vec.db` naming is re-derived from scratch in FOUR places
   (`db_maint.py:297-303`, `snapshot_db.py:1010-1014`,
   `rebuild_doc_search.py:376-380`, `rebuild_script_search.py:443-448`), and
   each derivation site historically missed one stream until caught by hand.
2. Restore/warm-up documentation has to enumerate files instead of pointing at
   one recovery point (`doc/procedures/embeddings.md:117-119`).
3. Nothing dedupes across corpora even where hashes could coincide.

## 2. Findings — current state (live counts 2026-08-26)

### 2.1 Inventory

| File (size) | Engine | Contents | Owner |
|---|---|---|---|
| `memory/research.db` (65 MB) | SQLite | source of truth incl. `note_search` FTS5 (JSON-vector column) + `company_embeddings` (1,063 JSON rows) | `helpers/core/db.py:47` |
| `memory/research.db_vec.db` (24 MB) | **SQLite + sqlite-vec** | `note_search_vec` vec0 mirror (1,224 rows) + embed cache (2,327) | `vec_search.py`, `embed_cache.py` |
| `memory/doc_search.db` (9.4 MB) + `.db_vec.db` (5 MB) | SQLite FTS5 + cache sidecar | `doc_search` FTS5 (496 sections), meta/info tables; sidecar = cache only (569) | `rebuild_doc_search.py:73` |
| `memory/script_search.db` (3.2 MB) + `.db_vec.db` (2.5 MB) | SQLite FTS5 + cache sidecar | `script_search` FTS5 (247 rows), meta/info; sidecar = cache only (284) | `rebuild_script_search.py:73` |
| `memory/graph.duckdb` (11 MB) | DuckDB | `v_embeddings` (1,063) + `v_note_embeddings` (1,224) CTAS caches over research.db columns | `helpers/graph/query.py:990-1113` |

**Correction to prior session notes**: all `_vec.db` sidecars are *SQLite*
(via the `sqlite-vec` extension, `pip sqlite-vec 0.1.9`), not DuckDB. The
sidecar path is derived mechanically from the main DB via
`PRAGMA database_list` (`vec_search.py:73-83`: `memory/research.db` →
`memory/research.db_vec.db`) and attached under schema alias `vecdb`
(`VEC_SCHEMA`, vec_search.py:62).

### 2.2 The three "use cases" decomposed

1. **Note-search KNN leg** — `note_search_vec` vec0 virtual table
   (`FLOAT[384] distance_metric=cosine`, vec_search.py:144-157), sourced from
   the JSON column in research.db. Serves app.py hybrid `/api/search`
   (`embedding MATCH ? AND k=?`, `1 - distance`; app.py:586-592) via C-side
   KNN, with lazy create+backfill on first hybrid query when absent
   (`knn_similarities` → `lazy_backfill=True`, vec_search.py:220-238, :265).
   Source of truth remains the JSON column; the mirror is excluded from parquet
   snapshots ("rebuilt, not shipped", vec_search.py:11-22).
2. **Shared content-hash embed cache** — one schema
   `(text_hash TEXT, model TEXT, embedding TEXT/json, PK(text_hash, model))`,
   defined once (`EMBED_CACHE_TABLE = "vecdb.note_search_emb_cache"`,
   embed_cache.py:28-36; sha256 key `_hash` :39-40), but instantiated **once
   per index sidecar** because attach follows the host DB's derived path.
   Written by `CachedEmbed`/`cached_embed_batch` (:43-96, :99-174) used by all
   three rebuilders AND by `--check` pre-warm (early-commit sites rds:479-483,
   rns:508-512). research's copy serves BOTH note-search and
   company-embeddings populations (2,327 rows). Code documents the duplication
   as intentional-but-incidental: "a separate cache file … is free: the
   corpora share no text" (rds:423-427).
3. **graph.duckdb vector caches** — `v_embeddings FLOAT[]` +
   `v_note_embeddings FLOAT[384]`, materialised by CTAS during
   `make graph-rebuild` (`_materialise_embeddings` query.py:990-1044,
   `_materialise_note_embeddings` :1047-1113), consumed by whole-corpus SQL
   cosine joins (`similar_notes` :2581, `notes_like_entity` :2628,
   `near_duplicate_notes` :2774, get_tickers `vss_match`). Listed in
   `MATERIALISED_TABLES` + drop list (:716-735).

### 2.3 How embeddings are stored & searched

| Surface | Storage | Search |
|---|---|---|
| `company_embeddings` | research.db TEXT `"[-0.03,…]"` w/ `CHECK(json_array_length=384)` (helpers/graph/embeddings.py:98-106) | DuckDB `array_cosine_similarity` over ~1k rows (~3 ms). **No HNSW on purpose**: "vss b833341 macros broken" posture at query.py:336-339, :1004-1008 |
| `note_search` embeddings | research.db FTS5 UNINDEXED JSON column (source of truth) | A1 C-side KNN via vec0 mirror (above); Python-cosine fallback if extension/table absent |
| doc/script search | FTS5 UNINDEXED JSON column | pure-Python dot-loop scan — deliberately no vec0 mirror at ~500-row scale: "sub-millisecond" (rds:755-757, rss `_cosine_leg` :790-823) |
| entity resolution | same `company_embeddings` strings | `get_tickers.vss_match` Python dot product (L2-normalised ⇒ dot==cosine, get_tickers.py:257-270) |

Model/dims constants are centralised in `helpers/core/local_embedder.py`
(`MODEL_ID = "bge-small-en-v1.5"` :49, sha256 pin :52, `DIM = 384` :61,
asymmetric `QUERY_PREFIX` :65, `embed_query` vs `embed_document` :155-164).

### 2.4 Rebuild / freshness machinery (what touches these files)

- Three sibling rebuilders (`rebuild_doc_search.py`,
  `rebuild_script_search.py`, `rebuild_note_search.py`) share structure, not
  code: identical `--check` gates (hash-exact drift vs `{table}_meta`, exit 1,
  writes only cache warm), identical model stamping (`*_info` tables for
  doc/script; `db_meta.note_embed_model` for notes since research.db must stay
  untouched — rns:437-450), identical pseudo-embedder fallback seam.
- Notes/doc refresh inside `maint-full` TIER2 steps 6c/9
  (`maint.py:152,175-177`); script only via explicit
  `make script-search-rebuild` / `make search-fresh APPLY=1`. Gates:
  `make search-fresh` runs all three `--check`s (Makefile:195-204);
  advisory runs them as three parallel steps
  (`tests/run_gate_report.py:151-156`).
- Apply paths hand-bump generation (`bump_generation`, db.py:212-215 —
  "--check/sidecar-only paths must NEVER bump"); DuckDB `_is_warm` compares
  generation + dims/model stamps to catch same-dims model swaps
  (query.py:514-603, probe :612-657).
- Concurrency around graph.duckdb: flock'd single-builder +
  pid-tagged atomic swaps (query.py:440-498, :772-804); N read-only readers
  coexist (`connect(read_only=True)` fast path).

### 2.5 Backup / publication layer

- **gzip stream** (`snapshot_db._cmd_create` :997-1025): gzips research.db,
  graph.duckdb (+WAL if non-empty), parquet exports, AND (since the user
  catch) the vec sidecar as copy-only `research.snapshot.db_vec.db.gz`
  — name re-derived by string surgery, cannot reuse
  `verify_snapshot` (hardcoded entities/relations).
- **raw twins** (`db_maint._backup` :277-286 + `_backup_vec` :288-314):
  sqlite online-backup into `db-backup/research_backup{,_vec}.db`.
- **per-indexer last-good backups**: `_backup_sidecars` after FULL rewrite
  only → `db-backup/{doc_search,script_search}_backup{,_vec}.db`
  (rds:364-394 call site :556; rss:441-458).
- **parquet publication**: `SQLITE_PARQUET_TABLES` closed allowlist
  (snapshot_db.py:450-461) excludes everything derived; restore regenerates
  FTS via `'rebuild'` (:443-449) but does NOT reproduce any sidecar — post
  `make snapshot-restore` the caches are cold/lazy-populated
  (procedures/embeddings.md:117-119).
- No retention policy anywhere: fixed-name overwrite semantics in all streams.

## 3. Load-bearing constraints (verified)

1. **research.db catalog purity**: DuckDB `ATTACH … TYPE sqlite` dies on vec0/
   spellfix1 virtual tables in the catalog ("no such module: vec0") — nothing
   virtual may move back into research.db (vec_search.py:57-61; belt-and-braces
   guard snapshot_db.py:512-521). ⇒ consolidation target must be a SEPARATE
   file (which it already effectively is).
2. **Privacy locality**: doc/script indexes stay out of research.db because
   they carry `doc/local/` plaintext (rds:22-30; procedures/doc-search.md:18-22;
   published db doctrine — snapshots/parquet IS the public artefact). NOTE the
   pooled CACHE does NOT inherit this concern: keys are sha256(text), values
   are float arrays — zero plaintext.
3. **Write-authority asymmetry**: Flask handlers are read-only toward index
   DBs with the SOLE exception of the vec0 lazy backfill (app.py comment
   :941-947); consolidation keeps that exception shape (one file written by
   request-time backfill + batch processes).
4. **Generation discipline**: any new write surface inside research.db would
   need trigger coverage — consolidation avoids research.db entirely, so no
   bump-rule changes.
5. **Model purity guards**: `(text_hash, model)` keyed cache must never serve
   cross-model vectors (embed_cache.py:16-18);
   `_ensure_single_model` SystemExit guard stays (embeddings.py:185-201).
6. **Hermeticity**: autouse conftest `_no_local_embedder` forces pseudo-64dim
   path in tests; tests heavily monkeypatch module constants (DOC_DB,
   BACKUP_DIR…) — any new path constant MUST be retargetable the same way.
7. **duckpgq retired / vss ANN unavailable** — re-probed empirically on the
   2026-08 1.5.4→1.5.5 bump: scalar `array_cosine_similarity` +
   brute-force KNN work (the production graph.duckdb pattern), but
   `CREATE INDEX … USING HNSW (emb COSINE)` fails with
   `NotImplementedException: Index with opclass not supported yet!`
   even with `hnsw_enable_experimental_persistence=true`. Any design
   assuming DuckDB-side ANN is off the table (unneeded regardless:
   brute-force ≈ ms at corpus sizes).
   *(MotherDuck "search using duckdb" 3-part series assessed against this
   under §4C.)*
8. **Backups assume sqlite online-backup API + WAL consistency**, never file
   copies while writers may hold connections (all four derivation sites rely
   on this).

### 3.1 Verified probes 2026-08-26 (post 1.5.4→1.5.5 pin bump)

- **Scanner still chokes on vec0 under duckdb 1.5.5** (empirical probe with
  `/tmp/vec_probe.db`: plain table + vec0 vtable):
  `ATTACH … (TYPE SQLITE)` succeeds and direct reads of *ordinary* tables
  work (`SELECT * FROM probe.plain` OK), but ANY catalog-wide scan or query
  touching the virtual table dies with
  `no such module: vec0` (`Failed to prepare query "PRAGMA table_info('vecdemo')"`).
  Cause is structural: sqlite_scanner bundles its own embedded SQLite runtime
  with no third-party vtable modules. Live research.db contains exactly ONE
  virtual object — `note_search` FTS5 — which survives only because FTS5 is
  compiled into the scanner. So the A1 separation remains REQUIRED by the
  toolchain, not just prudent; consolidation must target a separate sidecar
  file either way.
- **Parquet publication captures vectors only via the research.db lineage**:
  `snapshots/parquet/sqlite/company_embeddings.parquet` (1,063 rows, JSON
  string embeddings + model col) ships; `note_search_content.parquet`
  (1,224 rows) is the external-content FTS shadow — text-only (id + c0..c5),
  exists solely to rebuild FTS on restore, carries NO embeddings;
  `snapshots/parquet/duckdb/{v_embeddings,v_note_embeddings}.parquet`
  (1,063 / 1,224 FLOAT[]) ship both full vector sets. NEVER published:
  all three `*_vec.db` sidecars (cache + mirror), and `doc_search.db` /
  `script_search.db` outright (privacy locality). Consequence: after
  `snapshot-restore`, graph.duckdb comes back vector-warm while the embed
  cache/mirror are cold until lazy backfill / next maint-full.

## 4. Options considered

### A. Status quo
Costs recur: every new derived index adds another derived-filename pair that
every backup stream must remember; documented cold-restore surface grows.
Not acceptable given two catches in one week.

### B. External vector DB (Chroma / LanceDB / Qdrant) — REJECTED
Re-litigates settled calls: Chroma assessed 2026-08, status quo retained
(`doc/local/chromadb_assessment.md`); OpenViking pilot deferred
(doc/local fact-check); HNSW is a documented non-goal of the local-embeddings
arc ("brute-force is ~3ms at this scale",
`archive/database/local_embeddings.md:62-71`). Zero-egress rule forbids cloud
vector services. New persistence format = NEW backup-stream problem (the exact
class we're fixing) without removing the per-corpus fragmentation of FTS5+BM25
legs that hybrid ranking depends on. **No.**

### C. Consolidate everything into DuckDB — REJECTED
Would force losing/reimplementing SQLite FTS5 BM25 ranking (DuckDB fts is not
FTS5-equivalent) and cannot absorb vec0-bearing catalogs anyway (constraint 1
inverted: DuckDB scanner chokes on the SQLite file's virtual tables; vss ANN
unavailable per constraint 7). Engine split between FTS (SQLite) and SQL joins
(DuckDB) is justified and stands.

*Assessed against the MotherDuck "search using duckdb" series (2026-08-26,
user query):* part 1's technique — brute-force `array_cosine_similarity`
`ORDER BY score DESC LIMIT k` over FLOAT[] columns — is EXACTLY the existing
production pattern in graph.duckdb (`v_embeddings`/`v_note_embeddings`
materialisations + scalar-scan wrappers); nothing new to adopt. Part 2 is a
LlamaIndex `DuckDBVectorStore` walkthrough (no SQL vector layer at all; adds
a heavy retrieval framework we deliberately don't depend on). Part 3's FTS
leg is DuckDB's `fts` extension (`PRAGMA create_fts_index` + fixed-parameter
`match_bm25`, index in `fts_main_<table>` shadow schema) — strictly thinner
than our FTS5 surface (no column-weighted `bm25()`, no `snippet()`/
`highlight()`, no NEAR/phrase/column-filter query syntax), and its hybrid
fusion (convex combination α=0.8, RRF discussed) is one we already implement
and have EVAL'D (hybrid 1.00 vs BM25-only 0.93). Porting would rewrite every
consumer surface (app.py endpoint, two CLIs, all gates/tests/restore paths)
for zero measured retrieval gain at 247–1224-row scale.

**Revisit triggers** (not a flat never): corpus reaches O(100k+) indexed
notes where brute-force stops being ms-scale AND upstream vss ships a working
COSINE opclass on our pin; or a need for filtered-ANN joins that SQLite-side
Python scoring can't express; or Quack reaching production (DuckDB v2.0,
fall 2026 — dissolves the single-writer blocker for new designs, leaving
only blockers 1/3). Full evidence + addendum:
`doc/local/duckdb_vector_search_assessment.md`.

### D. Single consolidated SQLite embed store — CHOSEN
One file owns all sqlite-vec sidecar state. Scope variants:

| Variant | Contents | Verdict |
|---|---|---|
| D-min | pool 3 cache copies only; keep note mirror beside research.db (2 sidecar files remain) | viable fallback |
| **D-full (chosen)** | pool caches AND relocate `note_search_vec` into the shared store | one home for ALL sidecar vector state; sync/backfill/backups collapse to one artifact |
| D-max | additionally give doc/script vec0 KNN legs | rejected for scope discipline: no perf need at 500/250 rows; adds churn |

User confirmed D-full 2026-08-26.

## 5. Design (D-full)

New single store: `memory/embed_store.db` (SQLite + sqlite-vec):

- `embed_cache` — pooled content-hash cache; schema unchanged except the name
  (`note_search_emb_cache` → `embed_cache`; referenced solely through the
  constant in embed_cache.py, so rename cost is one line).
- `note_search_vec` — vec0 mirror table moved here verbatim (same DDL, same
  dims-from-DDL mismatch-rebuild rule, vec_search.py:340-353).

Code changes (review round: the attach already has a SINGLE modification
point — `CachedEmbed._try_init` delegates to `vec_search._attach_vec_db`
(embed_cache.py:59-63), so the whole flip is one function; do NOT create a
second attach path in embed_cache):

- **`vec_search.py`** owns the flip: `EMBED_DB_PATH` (default
  `memory/embed_store.db`, retargetable module attribute per constraint 6)
  lives here next to `_attach_vec_db`, which targets it for file-backed main
  connections, sets `journal_mode=WAL` + `busy_timeout=5000` (Flask
  lazy-backfill × rebuilder concurrency, constraints 3/8). The
  `<main>_vec.db` derivation (`_sidecar_path` :73-83) is retired for
  file-backed conns. Lazy-create/backfill untouched; `sync_vec_table` and
  `knn_similarities` signatures unchanged → `rebuild_note_search.py` and
  `app.py` call sites unaffected mechanically.
- **Hermeticity trap (review round, must-preserve)**: `_sidecar_path`
  currently returns `:memory:` for in-memory/temporary main connections —
  that branch is what keeps tests and throwaway conns isolated. The new
  attach MUST keep it verbatim; otherwise every in-memory test connection
  silently shares the LIVE store. §8 pins this with a test.
- **`embed_cache.py`**: table-name constant only
  (`note_search_emb_cache` → `embed_cache`); attach continues via the
  vec_search helper. Public APIs (`CachedEmbed`/`cached_embed_batch`)
  unchanged.

## 6. Migration plan

New `helpers/maintenance/migrate_embed_store.py`:

1. Create `memory/embed_store.db`, pooled `embed_cache` table.
2. For each legacy sidecar present
   (`research.db_vec.db`, `doc_search.db_vec.db`, `script_search.db_vec.db`):
   `INSERT OR IGNORE` its `note_search_emb_cache` rows INTO `embed_cache`
   (copy SQL references BOTH names — legacy read side, new write side);
   report per-source rows vs pooled-distinct delta.
3. `--sync-mirror` flag: populate `note_search_vec` straight from live
   research.db via `sync_vec_table` (avoids relying on first-query lazy
   backfill; ~seconds at 1,224 rows).
4. Rename processed legacy files to `*.migrated.bak` — never delete; print
   cleanup instruction for after gates go green. Rollback = un-rename +
   constants flip back.

Observability note (2026-08-26 review query): pooling removes the FILE
boundary that made cache rows attributable per corpus. Nothing about
staleness reporting changes — `--check` drift lives in per-index `_meta`
fingerprint tables inside each index DB (rds:443/:495-509 pattern),
model stamps stay in `*_info`/db_meta, hit/miss counters are per-run —
so all three checks keep reporting `stale_new/changed/deleted`
separately with independent exits. The only loss is cohort analytics
("how many doc texts are cached?"). Cheap insurance while migrating
anyway: add `source TEXT NOT NULL DEFAULT ''` to the pooled
`embed_cache` DDL (rebuilders stamp 'doc'/'script'/'note'/'company').

Concurrency note: parallel advisory steps currently write disjoint
sidecars; post-consolidation they share ONE store's writer lock for
cache-miss commits. WAL + short early-commit transactions +
busy_timeout=5000 absorb this (worst case slight cold-run slowdown).
   constants flip back.

Cold alternative would re-embed ≈16 min (cold-cache figure,
local-embeddings memory) — copying preserves the warm cache.

## 7. Operational impact

- `db_maint._backup_vec` → generalized raw twin `db-backup/embed_store_backup.db`.
- `snapshot_db._cmd_create` gzip block → direct shared-store reference,
  output `db-backup/embed_store.snapshot.db.gz` (copy-only semantics kept &
  documented).
- `rebuild_doc_search._backup_sidecars` / `rebuild_script_search._backup_sidecar`
  **narrowed, NOT removed** (review fix): drop only the `<index>_vec.db`
  entry from each backup list. The last-good copy of the INDEX itself
  (`doc_search_backup.db`, rds:376-380) stays — it protects against a bad
  full rewrite (failed rebuild rolls back, previous good backup survives,
  per its docstring), which is orthogonal to cache centralisation.
  Post-migration the vec-twin source file won't exist and the existing
  `if not src.exists(): continue` guard would skip it anyway, but the list
  entry is retired explicitly. Per-indexer `*_info` model stamps stay
  inside each index DB.
- WAL switch creates `embed_store.db-wal`/`-shm` siblings in `memory/`
  (gitignored wholesale; the sqlite online-backup API is WAL-safe, so no
  backup stream changes behavior — doc note only).
- graph.duckdb: untouched (§2.2 case 3 — engine boundary justified by
  constraint 1 direction + SQL-join consumers).
- Procedures updated: `doc/procedures/embeddings.md` (layout, recovery notes,
  warm-up checklist commands), `doc/procedures/doc-search.md` recovery §,
  `doc/architecture.md` datastore rows, `doc/graph_design.txt` §A1 (mirror
  lives in shared store outside research.db; ban on virtual tables INSIDE
  research.db unchanged).

## 8. Tests

- Rewrite `test_db_maint.TestBackupVec` + `test_snapshot_db` vec-gz test for
  the new artifact names.
- rds `TestSidecarBackup` + rss `test_full_backup_written`: keep the
  last-good INDEX backup assertions, drop the vec-twin assertions
  (mirrors §7 narrowing).
- **New: in-memory-branch test (review round)** — an in-memory main
  connection must attach an ANONYMOUS in-memory sidecar, never
  `EMBED_DB_PATH`, even with the real store present on disk (pins the §5
  hermeticity trap).
- Retarget `test_vec_search` / `test_rebuild_*_search` to `EMBED_DB_PATH`
  (module-attribute monkeypatch, `DOC_DB`/`BACKUP_DIR` convention).
- New migration round-trip test: overlapping + distinct hashes across seeded
  legacy caches → INSERT OR IGNORE semantics, counts, `.bak` renames, both
  table names in the copy SQL.
- Hermeticity intact: pseudo-model label keys keep tmp-dir isolation.

## 9. Risks / rollback

- Concurrent writers on one SQLite file (request-time backfill during a
  rebuilder) — mitigated by WAL + busy_timeout; collision class already
  exists today per-file.
- Pooled-store corruption would affect all indexes at once — offset by
  content-addressed rebuildability (any hash can be recomputed) + maint twins.
- Rollback fully additive (constant flip + `.bak` un-renames); no
  research.db schema/generation/publication-manifest surface changes.

## 10. Execution checklist

1. (this doc) File proposal → review.
2. embed_store core: `vec_search.py` attach flip + `EMBED_DB_PATH` + WAL +
   in-memory branch preserved; `embed_cache.py` table rename.
3. Migration script + run against live sidecars (+ `--sync-mirror`).
   **Sequencing warning (review round): steps 2+3 in ONE sitting** — between
   the constants flip and the migration, any search rebuild or hybrid query
   populates the new store COLD (worst case ~16 min re-embed; the legacy
   warm cache is only reachable via the migration copy). No `maint-full` /
   `search-fresh` / Flask traffic in the gap.
4. Repoint backup streams (db_maint, snapshot_db) + narrow per-indexer
   backups to index-db-only (§7).
5. Test updates (§8).
6. Docs (§7) + archive proposal + completed.md entry; 3× search rebuilds
   `--incremental --check` in wrap-up; `make types-tests`.
7. **Live verification (review round)**: `make search-fresh` (all three
   FRESH), one hybrid `/api/search` query confirming a KNN mirror hit
   (not the Python fallback), `db_maint` run emits
   `db-backup/embed_store_backup.db`, `make snapshot` emits
   `db-backup/embed_store.snapshot.db.gz`.
