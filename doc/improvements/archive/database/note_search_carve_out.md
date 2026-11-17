---
title: "note_search carve-out: last FTS5 out of research.db into a DuckDB native-FTS sidecar"
status: executed
filed: "2026-11-15"
executed: "2026-11-16"
completed_md: "530"
area: "helpers/maintenance/rebuild_note_search.py + helpers/misc/note_query.py + app.py + helpers/graph/query.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# note_search carve-out: last FTS5 out of research.db → DuckDB native-FTS sidecar

**Date:** 2026-11-15 · **Status:** EXECUTED (S0 2026-11-15, S1–S5 2026-11-16) · **Area:**
`helpers/maintenance/rebuild_note_search.py` + `helpers/misc/note_query.py` +
`app.py` + `helpers/graph/query.py` + `search_tui`/`master_query` (notes legs)
+ snapshot/db_maint/gc/seed wiring + tests.

**Follows:** the `duckdb_sidecar_fold` arc (S1–S5, LANDED 2026-11-15,
completed.md #524) which folded doc/memory/script/git FTS5 sidecars + the
convo transitional sidecar into DuckDB-native `.duckdb` stores. This proposal
is the documented **phase-1 prerequisite** of `duckdb_20_sqlite_reentry`
(B1 preflight §2.2 step 1, sized 1.5–2 d): carve `note_search` out of
research.db so the canonical store becomes pure relational (no virtual
tables) before the full port.

## 1. Motivation / scope

`note_search` is the **last FTS5 table in production**. It lives inside
`memory/research.db` (the canonical store) as a virtual table with an
UNINDEXED JSON embedding column (~17.5k sectioned rows), read RO by
`note_query`, `app.py`, `search_tui`, `master_query` (notes leg),
`helpers/graph/query.py` (`fin.note_search` RO-ATTACH projections), and
maintained by `rebuild_note_search` (+ `seed_note_gemma_cache`,
`gc_embed_cache`, `snapshot_db` virtual-table backup).

Moving it to a `note_search.duckdb` sidecar:

- removes the last FTS5 from research.db (the engine-mismatch surface every
  future research.db consumer must dodge);
- unifies the notes read path with the doc/memory/script/git pattern (native
  `match_bm25` + inline `FLOAT[512]` embeddings), reusing the proven
  S1–S5 engineering;
- de-risks B1 phase 2 (engine swap in db.py) by removing the virtual table
  from the 32-table DDL port.

Out of scope: research.db itself (B1 phase 2+); the embed cache host
(`embed_store.db` — see §6); `corpus.db` (pure mtime cache, no search
semantics); the `note_vectors.duckdb` mirror (already DuckDB; may absorb the
sidecar's vector read later).

## 2. Current state (verified 2026-11-15)

- `note_search` FTS5 table in `memory/research.db`: `CREATE VIRTUAL TABLE
  note_search USING fts5(doc_type, file_path UNINDEXED, title, sector,
  content, embedding UNINDEXED, tokenize='porter unicode61')`; rows ~17.5k
  (sectioned since 2026-09-06; ~13× multiplier over files). `note_search_meta`
  is file-keyed (file_path, mtime, content_hash); the embedding model stamp
  lives in `db_meta.note_embed_model` (research.db must stay untouched by
  rebuilds — rns:437-450). Stale-schema drop/recreate exists
  (rns:712, at :1222).
- Consumers (mapped live):
  - `helpers/misc/note_query.py` — bm25 leg `:99-101` (`bm25(note_search)`
    + `snippet`… via `note_snippet` word-window already Python-side);
    `load_notes_matrix` `:113-137` reads row count + `stored_embed_dims`
    from research.db to gate `embed_matrix.f32` staleness; embedder stamp
    via `db_meta.note_embed_model` `:164-175`.
  - `app.py` — capability probe `:861-878` (`sqlite_master` +
    `pragma_table_info`), `/api/search` hybrid `:1012-1087` (`note_search
    MATCH ?` + KNN), front-page matrix staleness `:712`, `vec_row_key`
    `:729-731`, embed read `:806`.
  - `helpers/graph/query.py` — `fin.note_search` RO-ATTACH projections:
    dims probe `:872`, embeddings read `:891`, `:1183-1195`,
    `:2065-2103` (`_project_note_search_embeddings` CTAS source),
    whole-corpus KNN/join queries `:4404+`.
  - `helpers/graph/context_pack.py` (bootstrap mirror), `search_tui.py` /
    `search_tui_app.py` (notes lane; `--check` re-verifies 16,520 notes),
    `master_query.py` (notes leg), `helpers/web/prefab_views.py`.
  - Maintenance: `rebuild_note_search.py`; `seed_note_gemma_cache.py:69`
    (uses `open_cache_conn()` since the S0 embed-store fold);
    `gc_embed_cache.py` (note Ref); `snapshot_db.py` (DuckDB
    checkpoint-then-copy snapshot since S0); `db_maint.py`
    SIDECAR_SQLITE list; `store_sweep.py` census.
- Pattern available (S1–S5): native `create_fts_index` + `match_bm25`,
  native DuckDB cache adapter (`open_cache_conn()` / `cached_embed_batch`
  — the attach shim died with the S0 fold), Python `_highlight_window`,
  engine-tolerance edits (`.fetchall()`, information_schema branches),
  clean-slate drop of FTS shadows, `--check` hash-only verdict, one-time
  cache seed from legacy rows, legacy `.db` rollback paths kept.

## 3. Constraints / decisions

1. **Sidecar store** `memory/note_search.duckdb`: `note_search` (id PK,
   doc_type, file_path, title, sector, content, embedding `FLOAT[512]`,
   searchtext = title + sector + content), `note_search_meta`, `*_info`
   stamps (embed_model, embed_dims, basis versions) — same shape as the
   S1–S5 stores so `read/write_apply_stamp` works unchanged.
2. **Composed searchtext** replaces the per-column bm25 weights (accepted
   §3.2 deviation from the sidecar-fold arc); the hybrid cosine leg stays
   the quality guard. `doc_type`/`file_path` become plain SQL columns.
3. **Highlight** stays Python (`note_snippet` word-window already exists —
   reuse, do not re-implement).
4. **Legacy path preserved**: `--db <legacy .db>` keeps working
   (sqlite branch retained in rebuild/query dispatchers), matching the
   S1–S5 rollback doctrine. Tests pin both paths.
5. **The model stamp moves** from `db_meta.note_embed_model` to the
   sidecar's info table; consumers that read the stamp (note_query embedder
   selection, matrix staleness gate) switch to the sidecar.
6. **research.db untouched by rebuilds**: the carve-out must NOT add any
   write path to the canonical store (existing doctrine); snapshot_db
   virtual-table backup is replaced by sidecar export/verify.

## 4. Slices

**S1 — sidecar build — EXECUTED on 2026-11-16 (code + tests + live
smoke landed; see §6c for acceptance).** Extend `rebuild_note_search.py`
with a DuckDB path: `NOTE_DB` → `note_search.duckdb`, `connect_note_db`
suffix routing, `rebuild_note_duckdb` (replacement rebuild reusing the
existing walk/section pipeline; rows + `create_fts_index('note_search','id','searchtext')`;
meta + info stamps; native cache adapter on the host conn (S0);
`--check` hash-only verdict + `_print_staleness`). One-time cache seed from
legacy rows (basis reconstructible from stored columns; gemma/bge stamp
equality asserted). No graph-DB rebuilds, no quack slot (small store,
swap-rebuild).

**S2 — query surface flip — EXECUTED on 2026-11-16 (code + tests + live
smoke landed; see §6d for acceptance).** `note_query.py`: `bm25_hits` → native
`match_bm25` branch (`fts_main_note_search.match_bm25`), `load_notes_matrix`
staleness gate reads the sidecar (count + dims), embedder stamp reads the
sidecar info table, `main`/CLI default `--db` → `.duckdb` (legacy via
`--db`). `app.py`: capability probe (information_schema branch), `/api/search`
hybrid (native FTS + existing KNN path), `vec_row_key`/embed read → sidecar.
`search_tui`/`master_query` notes legs engine-aware (same flip as the
S1–S5 consumers).

**S3 — graph projections — EXECUTED on 2026-11-16 (code + tests + live
smoke landed; see §6e for acceptance).** `helpers/graph/query.py`: all `fin.note_search`
RO-ATTACH reads switch to the sidecar (or the existing
`note_vectors.duckdb` mirror where it already covers the read — choose the
fewer-hop source per site), `:872/:891/:1183-1195/:2065-2103/:4404+`;
`context_pack.py` bootstrap mirror follows.

**S4 — ops wiring — EXECUTED on 2026-11-16 (code + tests + live
smoke landed; see §6f for acceptance).** `snapshot_db.py`: note_search virtual-table backup →
sidecar export/verify (duckdb `COPY`/export pattern); `db_maint.py`:
`SIDECAR_DUCKDB += note_search.duckdb` + covered set, legacy `.db` stays in
`SIDECAR_SQLITE`; `gc_embed_cache.py`: note Ref → live `.duckdb` store
(recipe parity with doc/script/git); `seed_note_gemma_cache.py`: retarget
`_attach_vec_db` call to the sidecar/new adapter; `store_sweep.py` census.

**S5 — legacy path + tests + docs — EXECUTED on 2026-11-16 (tests +
docs landed; see §6g for acceptance).** Keep the sqlite rollback route green
(existing suites unchanged). New `tests/test_note_query_duckdb.py` (shape:
route-by-suffix, build+schema, hybrid shape/contract, bm25-only, stale-on-new,
check-mode no-stamp, legacy rollback path — mirror of
`test_doc_query_duckdb.py`). Full targeted suites + ruff + md-lint +
`make search-fresh` APPLY. Update the sidecar-fold proposal's acceptance note
(note_search carve-out done) and this proposal's §S-findings as slices land.

**S6 — embed-cache fold — EXECUTED as S0 on 2026-11-15 (operator
request: attach was a clunky mechanism even before this carve-out; see
§6b for acceptance).**

## 5. Acceptance criteria

- `grep -r "fts5" helpers/` → 0 (documented references only; the convo
  docstring note + this proposal's own text).
- `note_query`/`app.py` `/api/search`/`search_tui`/`master_query` answers on
  the `.duckdb` sidecar with hybrid mode intact; `--db <legacy .db>` still
  works.
- research.db has no `note_search` virtual table; `db_meta.note_embed_model`
  stamp retired (sidecar info table is the home).
- snapshot/db_maint/gc/seed reference the sidecar; `grep -r "note_search"
  helpers/graph/query.py` shows no `fin.note_search` ATTACH reads.
- Targeted suites green; full qa once on operator go; md-lint +
  search-fresh clean.

## 6. embed_store: why sqlite today, and what a pure-DuckDB cache takes

The S1–S5 ruling (duckdb can't host the vecdb attach) was about the
**attach mechanism**, not the file: the pooled cache (`embed_store.db`,
552 MB, 230,391 rows, `embed_cache(text_hash, model, embedding, …)`) is
sqlite, attached via `_attach_vec_db` into whatever main connection a rebuild
opened; DuckDB rejects `PRAGMA busy_timeout` and cannot load the sqlite
attach — hence `_DuckCachedEmbed`'s dedicated sqlite handle. That workaround
disappears if the cache *itself* is DuckDB — pure DuckDB, no attach:

- `embed_cache` schema ports 1:1; content-addressed writes are idempotent
  (partial-state risk from DuckDB autocommit is benign for a cache);
  `purge_foreign_models` is plain DELETE; `cached_embed_batch` miss-fill
  unchanged.
- Requirements: (a) single-writer per file — today all cache writes are
  maintenance paths (rebuilds, `seed_note_gemma_cache`,
  `embeddings.py` company populate), which already run under the sequential
  `search-fresh`/`maint-full` discipline; concurrent readers must open
  read-only (DuckDB allows one RW + many RO processes per file). (b)
  snapshot/db_maint backup streams switch from SQLite online backup to
  DuckDB export/verify (pattern exists). (c) `cached_embed_batch`'s
  `_attach_vec_db` + `_DuckCachedEmbed` give way to one native DuckDB cache
  adapter.
- Cost/benefit: zero user-visible search change; buys engine unification
  (only `research.db` + `corpus.db` remain sqlite) and deletes the attach
  shim. Risk: concurrency-semantics change (WAL multi-reader tolerance is
  replaced by explicit RO opens) and backup-stream rework.
  **Recommendation:** do not fold into this carve-out — S6 is a separate
  slice, taken after S1–S5 land and the research.db port decision is made.
  (Superseded 2026-11-15: the operator pulled it forward — attach was a
  clunky mechanism even before this carve-out — and it landed as S0; see
  §6b for the acceptance record.)

## 6b. S0 — embed-cache fold: acceptance (EXECUTED 2026-11-15)

Accepted as an upfront slice, before S1–S5: S0 is a prerequisite of the
carve-out, not a parallel lane — the attach shim (`_attach_vec_db` /
`_DuckCachedEmbed`) was doomed regardless, and S1's sidecar build now
uses the native DuckDB cache directly.

What landed (all in the tree, uncommitted pending operator staging):

- `helpers/core/vec_search.py`: `EMBED_DB_PATH` = `memory/embed_store.duckdb`
  (`EMBED_SQLITE_DB_PATH` = `memory/embed_store.db` kept as the legacy
  rollback + migration source); `_attach_vec_db`, `VEC_SCHEMA`/`vecdb`,
  `_store_path` deleted; added `open_cache_conn(*, read_only=False)`
  (duckdb.connect + `memory_cap_config`; parents mkdir on RW only),
  `_resolve_cache_conn(conn)` (duckdb host→host; sqlite `:memory:` →
  duckdb `:memory:`; sqlite file → `open_cache_conn()`; else best-effort),
  `_is_duckdb_conn`.
- `helpers/core/embed_cache.py`: `EMBED_CACHE_TABLE` now bare `embed_cache`;
  `EMBED_CACHE_DDL=CACHE_DDL_BARE`; Rust lookup removed (subprocess /
  msgpackio / jsonio / envs); `CachedEmbed` rewritten (`_host_conn`,
  `_resolve_cache_conn` init, `close()`; `[embed-cache] init failed` /
  `OFF — init failed` / `cache init recovered`); `cached_embed_batch`
  resolves one cache conn, DDL in try/except → degrade-uncached, close
  owns-conn in finally, `purge_foreign_models` before lookup.
- All 7 rebuilders (doc/git/convo/memory/script/note + company populate)
  and `seed_note_gemma_cache` use the native adapter; `db_maint`
  `_backup_embed_store` → `_duckdb_zstd_backup`
  (`embed_store_backup.duckdb.zst`); `snapshot_db` `_snapshot_embed_store`
  → `create_duckdb_snapshot` (`.snapshot.duckdb.zst`); `gc_embed_cache`
  (`STORE` duckdb, `CHECKPOINT` instead of `VACUUM`); probes + conftest.
- Registries: `db_maint.SIDECAR_DUCKDB` and the tar `covered` set now
  include `memory/embed_store.duckdb` (CHECKPOINT rides maint);
  `store_sweep._FILING_MB` baseline = `memory/embed_store.duckdb: 387.26`
  (measured; the legacy sqlite rollback is 527.1 MiB and stays registered
  in `SIDECAR_SQLITE`).

Migration + parity (live, verification source `memory/embed_store.duckdb`):

- 230,391 rows / 527.1 MiB sqlite → duckdb in 77.9 s via explicit
  BEGIN/COMMIT (chunked autocommit was ~30× slower and was abandoned).
- Parity: counts equal (230,391/230,391); source/model aggregates equal
  across engines; 350 blob spot-checks (50 per source) byte-equal.
- Source distribution (unchanged): convo 197,381 granite · note 19,093
  gemma · doc 7,840 (4,518 gemma + 3,322 granite) · git 4,218 (2,262
  gemma + 1,956 granite) · script 842 gemma · company 632 gemma · memory
  385 gemma.

Bugs found & fixed during the fold:

- `gc_embed_cache.retire()` reported `deleted = -1`: `cur.rowcount` was
  read *after* `CHECKPOINT` on the same cursor — DuckDB single-result
  cursors void the previous result. Count is read before the checkpoint
  (`gc_embed_cache.py:600-601`).
- `vec_codec.load_vec` let `BLOB.frombytes` `ValueError` escape → corrupt
  cache rows crashed instead of counting as a miss + healing; now caught
  → None.

Acceptance verified:

- `grep -r "_attach_vec_db|_DuckCachedEmbed|vecdb.embed_cache" helpers/
  tests/` → 0 call sites (docstrings/comments only; `EMBED_SQLITE_DB_PATH`
  remains as the documented rollback constant).
- Live smoke: `gc_embed_cache` report on the real store — 230,391 rows,
  0 dead, 5,278 other-model rollback retained (doc 3,322, git 1,956),
  all 7 cohorts referenced.
- Tests: 263 targeted tests green across the rewritten embed suite
  (test_embed_cache, test_vec_search, test_rebuild_note_search,
  test_embeddings, test_convo_search, test_embed_gc, test_snapshot_db,
  test_db_maint, test_db_maint_convo, test_store_sweep,
  test_gate_watch_legs); ruff clean; md-lint clean.
- Migration tool idempotent: `$TMPDIR/opencode/migrate_embed_store.py`
  (complete store → verify-only).

## 6c. S1 — note_search duckdb sidecar build: acceptance (EXECUTED 2026-11-16)

What landed (all in the tree, uncommitted pending operator staging):

- `helpers/maintenance/rebuild_note_search.py`: `NOTE_DUCKDB_DB` =
  `memory/note_search.duckdb`; `connect_note_db(path)` suffix-routes
  `.duckdb` → `duckdb.connect` + `memory_cap_config` + INSTALL/LOAD
  `fts` per connection (native FTS is an extension; fresh reader
  connections must load it explicitly); `rebuild_note_duckdb()` — full
  replacement rebuild reusing the existing walk/section/entity pipeline
  (`_iter_findata_docs` + `_collect_rows` with `_edge_ctx_maps`), DDL ×3
  (`note_search(id BIGINT PK, doc_type, file_path, title, sector, content,
  embedding BLOB, section_title, anchor, ticker, edge_context, searchtext)`,
  `note_search_meta`, `note_search_info`), composed `searchtext`
  (`_compose_searchtext` = space-joined doc_type/title/sector/content/
  section_title/ticker/edge_context), `create_fts_index('note_search',
  'id', 'searchtext')`, info stamps `embed_model`/`embed_dims`/`row_count`
  inside the sidecar; `_check_freshness_note_duckdb()` hash-only verdict;
  `_resolve_db` allows a missing `.duckdb` target (created on first run).
- Embedding pipeline: `CachedEmbed` on the host duckdb conn (cache lives
  in the sidecar file — S0's one-native-adapter rule), two-phase batch
  via `cached_embed_batch(conn, texts, model_label, batch_fn,
  source="note")`, degrade → uncached embedding (warn + empty vecs).
- One-time cache seed: `/mnt/data/tmp/opencode/seed_note_sidecar_cache.py`
  copies the note cohort (`embed_cache WHERE source='note' AND
  model='embeddinggemma-2-q8_512'`) from `memory/embed_store.duckdb`
  (19,093 rows) into the sidecar cache; idempotent (INSERT OR REPLACE);
  required because the sidecar's own cache starts cold while the shared
  store holds the cohort.

Bugs found & fixed during S1:

- `cached_embed_batch` returns `(vecs, stats)` — a tuple, not a dict;
  first draft did `by_idx.get(i)` → AttributeError. Fixed callers.
- DuckDB 2.0.0 `match_bm25` is a MACRO, not a table function:
  `SELECT ... FROM fts_main_note_search.match_bm25(id, 'q')` raises
  CatalogException "Table Function with name match_bm25 does not exist!"
  even after INSTALL/LOAD fts. Correct form is the predicate
  `WHERE fts_main_note_search.match_bm25(id, ?) IS NOT NULL`. (The doc
  sidecar test suite passes because it only asserts schema presence and
  never queries via the FROM form.)
- Cold first build (empty sidecar cache) attempted ~19k fresh embeds at
  ~0.8/s and the gemma sidecar timed out after ~1k — the seeded cache
  fixes the warm path; a cold rebuild without a seed is bounded by the
  embedder's throughput.

Verification (live corpus):

- Rows: sidecar 19,356 == legacy research.db 19,356. Meta: 1,398 ==
  1,398. Info stamp: `embeddinggemma-2-q8_512`/512/19,356 inside the
  sidecar == `db_meta.note_embed_model` in research.db.
- Native FTS: `fts_main_note_search.match_bm25(id,'shrimp')` → 68 hits,
  == legacy FTS5 (68).
- Row-by-row diff (legacy rebuild on a fresh copy of research.db vs
  sidecar, ordered by file_path/anchor): doc_type, file_path, title,
  sector, content, section_title, anchor, ticker 100% identical (0
  diffs); embeddings byte-identical across all 19,356 rows (2048-byte
  f32 blobs).
- `edge_context`: sidecar rows equal a fresh `_edge_ctx_maps` computation
  from live research.db. The legacy rows differ on ~5,127 rows because
  the legacy incremental `--carry` path preserves STALE edge contexts for
  unchanged files (the entity graph changed after their last full
  rebuild) — a pre-existing legacy behavior, not an S1 regression; the
  sidecar full rebuild is the correct reference.
- Warm rebuild: 0 embed misses (all hits from the seeded cache), completes
  in minutes; `--check` fresh after build, reports `stale_changed` after
  touching a file (test-covered).

Tests:

- New `tests/test_rebuild_note_search_duckdb.py` (4 tests): build →
  sidecar exists, indexed==4, total_files==3, native FTS hit (predicate
  form), store shape, info stamps, meta count, idempotency, check-mode
  freshness + drift, missing-store → all-new. 41 targeted tests green
  (note legacy + note duckdb + doc duckdb); ruff clean; md-lint clean.

## 6d. S2 — query surface flip: acceptance (EXECUTED 2026-11-16)

What landed (all in the tree, uncommitted pending operator staging):

- `helpers/misc/note_query.py`:
  - `open_note_index(db_path)` → `(conn, engine)` — `.duckdb` → DuckDB
    (read-only + `memory_cap_config` + INSTALL/LOAD `fts`), else legacy
    `db_connect`.
  - `bm25_hits` dispatches: `_bm25_hits_sqlite` (unchanged FTS5) /
    `_bm25_hits_duckdb` (native `match_bm25` predicate form, higher-is-
    better score, `IS NOT NULL` filter, quoted-token query).
  - `load_notes_matrix` → `_index_count_dims(db_path)`: duckdb reads
    `COUNT(*)` + `note_search_info` `embed_dims` stamp; sqlite reads
    count + `stored_embed_dims`.
  - `_stored_note_model(db_path)`: duckdb → `note_search_info` `embed_model`
    stamp; sqlite → `db_meta.note_embed_model` (both None-tolerant).
  - `note_snippet_highlighted(content, q)`: python <mark> snippet
    (DuckDB native FTS has no snippet()/highlight()); plain
    `note_snippet` stays for CLI/TUI.
  - `search_notes_duckdb(conn, q, limit, offset, doc_type=None)` →
    `(rows, total_count)`: AND-first/OR-fill candidate generation
    (quoted-token exprs via the doc surface's `fts_match_expr`), NOTE-
    level dedup (best-scoring section per file_path, score-desc first
    occurrence wins), `total_count` = OR-space `COUNT(DISTINCT file_path)`;
    rows carry the legacy FTS5 contract `(doc_type, file_path, title,
    sector, embedding, score, highlighted snippet, section_title, anchor)`
    so app.py's shared fusion builders run unchanged on both engines.
  - CLI/`main` default `--db` → `memory/note_search.duckdb`; legacy via
    `--db memory/research.db`.
- `app.py`:
  - `_search_notes_duckdb(q, doc_type, hybrid, limit, offset)` —
    sidecar-first branch for `/api/search`: opens the `.duckdb` store
    (read-only, fts loaded), delegates candidate generation to
    `note_query.search_notes_duckdb`, runs the shared `_hybrid_search_results`
    fusion (KNN mirror + matrix fallback + RRF k=60), returns
    `(results, total_count)` or None (sidecar absent/unusable → legacy
    sqlite FTS5 route untouched).
  - `_resolve_query_vec` now stamp-reads engine-aware (duckdb →
    `note_search_info`; sqlite → `db_meta`).
  - `/api/search` docstring updated; 400 on bad query syntax from either
    engine.
- `helpers/misc/search_tui.py`: notes lane → `nq.open_note_index`;
  `_run_notes` db → `memory/note_search.duckdb`; `INDEXES` entry →
  sidecar.
- `helpers/misc/master_query.py`: `_LEG_DB["notes"]` → sidecar.

Bugs found & fixed during S2:

- `match_bm25` appears twice in the candidate statement (SELECT score +
  WHERE predicate) — the first draft passed one `?` param for two and
  DuckDB raised `Invalid Input Error: Values were not provided for the
  following parameters: 2`. Params list carries `[expr, expr]`.

Verification (live corpus):

- `note_query` CLI hybrid smoke, `--limit 3`: sidecar (default) and
  legacy (`--db memory/research.db`) return the same top hit
  (`findata/Companies/FMCG/Sharat_Industries.md` / Business Segments,
  line 32).
- `_search_notes_duckdb("shrimp")`: 20 results / total_count 21,
  hybrid similarities present (~0.75); multi-term `"frozen food"` → 252,
  `"Avanti"` → 36; `<mark>`-highlighted snippets; NOTE-level dedup
  (one row per file_path).
- `master_query` CLI imports/resolves with the sidecar default.

Tests:

- New `tests/test_note_query_duckdb.py` (11 tests): `bm25_hits`
  duckdb routing + no-match; `note_snippet_highlighted` (marks,
  ellipsized edges, no-mark, empty); `search_notes_duckdb` legacy row
  contract (9-tuple, mark snippet, file dedup), AND→OR fill, doc_type
  filter, no-match → `([], 0)`, embedding blob 64-dims.
- 99 targeted tests green (note legacy + note duckdb + rebuild duckdb +
  shared query vector); ruff check + format clean.

## 6e. S3 — graph projections: acceptance (EXECUTED 2026-11-16)

What landed (all in the tree, uncommitted pending operator staging):

- `helpers/graph/query.py` — all four `fin.note_search` RO-ATTACH
  surfaces now read the sidecar first, sqlite fallback retained:

  - `_probe_note_embed_state` — probes the colocated
    `note_search.duckdb` info table (`note_search_info` keys
    `embed_dims`/`embed_model`) first, `db_meta` fallback.
  - `_compute_input_fingerprints` — `fps["note_embeddings"]` =
    `count:dims:model` from the sidecar when present, else
    `fin.note_search`/`fin.db_meta`.
  - `_materialise_note_embeddings` — `v_note_embeddings` CTAS reads
    the sidecar via new `_read_sidecar_note_embeddings()`; legacy
    `_read_fin_embeddings` fallback.
  - `_mark_warm` — `note_model` from sidecar info first, `db_meta`
    fallback.
  - New helpers: `_note_sidecar_path(duckdb_path)` (colocated
    `note_search.duckdb`, colocation-only by design — production-skip
    for tmp test caches), `_duckdb_path_of(con)`, `_read_sidecar_note_embeddings`.

Bugs found & fixed during S3:

- `_read_sidecar_note_embeddings` iterated the DuckDB cursor with
  `for row in cur:` — cursors are not iterable; the TypeError was
  swallowed by the broad `except` and returned `([], 0)`. FIX:
  `for row in cur.fetchall():`.
- `_probe_note_embed_state` first draft `SELECT value ...` then
  `dict(row)` → ValueError (1-tuples). FIX: `SELECT key, value`.
- `_mark_warm` first draft referenced an undefined `duckdb_path` →
  ruff F821. FIX: `_duckdb_path_of(con)`.
- Pre-existing wire-mode list-param limitation surfaced: `similar_notes`
  / `similar_notes_doc_types` / `edition_companies` passed the reference
  vector as a bound list param; the resident quack server's
  `WireConnection` inlines params via `sql_lit`, which has no list
  rendering (`TypeError: no literal rendering for list`). FIX: the
  vector now rides in the SQL as a numeric `_vec_literal` (the pattern
  `notes_like_text` already used), on all four KNN surfaces, so both
  file-mode and wire-mode connections work.

Verification (live corpus):

- `query.connect()` warm: `v_note_embeddings` 19,356 rows, dims 512;
  `_build_meta` `note_embed_dims=512`, `note_embed_model=embeddinggemma-2-q8_512`.
- `similar_notes(con, "findata/Companies/FMCG/Sharat_Industries.md", k=3)`
  over the served (wire) connection returns 3 ranked neighbors
  (top: `Points_And_Figures/Opportunity_Between_The_Lines.md`, sim 0.8838).
- `edition_companies(con, "findata/The_Chatter/A_Quarter_That_Refuses_To_Behave.md", k=2)`
  over the wire returns ranked companies (top: `Jubilant_FoodWorks`, 0.8921).

Tests:

- New `tests/test_graph_note_sidecar.py` (7 tests): sidecar
  materialisation, dims/model stamp round-trips (RW-open exclusion
  respected — tests close the write connection before `_is_warm`),
  model swap forces cold, dims drift forces cold, fingerprint reads the
  sidecar, similar_notes self-exclusion ranking.
- Graph regression battery: 185 passed (test_graph, test_graph_disk 33,
  test_graph_rebuild_fast_path, test_integration_graph_rebuild,
  test_note_embeddings, test_integration_near_duplicates,
  test_graph_note_sidecar); ruff check + format clean.

## 6f. S4 — ops wiring: acceptance (EXECUTED 2026-11-16)

What landed:

- `helpers/maintenance/gc_embed_cache.py` — the note Ref (`DEFAULT_REFS`)
  retargeted to the live sidecar: `memory/note_search.duckdb` (was
  `memory/research.db`), stamp read from `note_search_info.key='embed_model'`
  (was `db_meta.note_embed_model`). SQL text unchanged (`SELECT title, sector,
  section_title, content FROM note_search`). Legacy research.db FTS5 remains
  the rollback artifact, not the GC target.
- `helpers/maintenance/db_maint.py` — `SIDECAR_DUCKDB +=
  ("memory/note_search.duckdb", False)` (CHECKPOINT rides maint); tar
  `covered` set += `"note_search.duckdb"`; legacy `.db` stays in
  `SIDECAR_SQLITE`.
- `helpers/maintenance/snapshot_db.py` — new `DEFAULT_NOTE_DUCKDB` +
  `NOTE_SEARCH_TABLES = {note_search, note_search_meta, note_search_info}`
  (the native FTS shadow `fts_main_note_search` is derived state, excluded).
  The Parquet create path gained the `_note_parquet_worker()` (export with
  `manifest=NOTE_SEARCH_TABLES`, schema file `_schema.note.sql`, own tree
  under `snapshots/parquet/note/`, verify via
  `_verify_parquet_duckdb_side(..., subdir="note")`); `--check` verifies the
  note side when present and skips silently when absent; `--restore` rebuilds
  the sidecar from its parquet tree (force-guard included). CLI adds
  `--note-db` (default `memory/note_search.duckdb`) and `--no-note`.
- `helpers/maintenance/store_sweep.py` — census entry
  `"memory/note_search.duckdb": 216.75` (measured 2026-11-16, 227,291,136 B).
- `seed_note_gemma_cache.py` — no retarget needed: it seeds the SHARED cache
  (`embed_store.duckdb`, source='note') which still serves the legacy
  rebuild path; the sidecar received its one-time seed in S1
  (`seed_note_sidecar_cache.py`).

Bugs found & fixed:

- None in this slice (export/verify/restore reused the sources-leg
  machinery; the only traps were wiring-level: `ThreadPoolExecutor`
  max_workers 3→4 for the fourth worker, and force-guard/targets list
  extension in `_cmd_restore`).

Verification:

- New tests in `tests/test_snapshot_db.py` (2, +33 total): note-side
  export/verify/restore roundtrip (manifest exactly 3 tables, FTS shadow
  never exported, restore yields 2+2 rows and the `embed_model` stamp) and
  stray-table manifest guard (warns, skips, `tables_checked == 2`).
- Targeted suites: test_snapshot_db 33 + test_store_sweep + test_db_maint +
  test_embed_gc + test_rebuild_note_search_duckdb + test_note_query_duckdb
  = 103 passed; ruff check + format clean.
- Live capture: `snapshots/parquet/note/` produced from the real sidecar
  (note_search 51,646,134 B + note_search_meta 31,260 B + note_search_info
  464 B); real-tree `snapshot_db.py --check` fully green incl. the note leg
  ("Parquet verify (note): 3 tables checked, all OK").
- CLI smoke (tmp harness): create → check → restore across the parquet
  matrix, note leg exported/verified/restored end-to-end.

## 6g. S5 — legacy path + tests + docs: acceptance (EXECUTED 2026-11-16)

What landed:

- Gap tests appended to `tests/test_note_query_duckdb.py` (11 → 15),
  closing the S5 shape list from §4:
  - `TestBm25OnlyIndependence` — bm25-only independence: a row with NULL
    embedding still ranks lexically and the candidate rows carry the NULL
    blob without error (DuckDB native FTS does not auto-index direct
    INSERTs, so the test flips an existing row's embedding rather than
    inserting through the shadow).
  - `TestRebuildCheckModes` — check-mode no-stamp (info table + file mtime
    untouched by a `--check` run) and stale-on-new (a doc added after the
    build reports `stale_new`; clears after a real rebuild).
  - `TestLegacyRollback` — the sqlite rollback route stays green end to
    end: rebuild into a `research.db`-style store (entities seeded first —
    the legacy store IS the entity store), then `bm25_hits`/`open_note_index`
    (engine == "sqlite")/`search()` all route to FTS5. The legacy score
    assertion checks the negation relationship against raw FTS5
    `bm25()` (micro-corpus ranks sit near -1e-6, so no magnitude bound).
- Docs: §4 S5 marked EXECUTED; the archived sidecar-fold proposal
  (`doc/improvements/archive/database/duckdb_sidecar_fold.md` §5) gained a
  follow-on note recording that the note_search carve-out landed and the
  residual `fts5` grep allowance is now scoped to the documented rollback
  path.

Verification:

- Full note batch: test_note_query, test_shared_query_vector,
  test_rebuild_note_search, test_rebuild_note_search_duckdb,
  test_note_query_duckdb = 103 passed (42.08s).
- Graph regression battery unchanged-green: 185 passed (163s).
- Maintenance batch: test_snapshot_db 33 + test_store_sweep + test_db_maint
  + test_embed_gc = 88 passed (S4 record).
- ruff check clean across all 13 touched files; md-lint on proposal clean.

## 7. Sizing

S1 0.5–1 d (pattern reuse), S2 0.5–1 d (flip + app/CLI),
S3 0.5 d (projection reads), S4 0.5 d (ops wiring), S5 0.5–1 d
(tests/docs) → **≈2.5–4 d total** (B1's 1.5–2 d estimate undercounts the
graph-projection + ops surface; the pattern reuse keeps it bounded).

## 8. Risks

- `app.py` KNN/matrix staleness interplay: the sidecar must keep row-count
  and dims identical to `embed_matrix.f32` meta, or hybrid degrades to
  bm25 — keep the existing staleness gate semantics.
- `helpers/graph/query.py` `fin.note_search` consumers are the widest
  blast radius; each site verified individually (S3).
- Sectioned-row identity (`vec_row_key`) must be preserved byte-for-byte
  so KNN keys don't drift.
