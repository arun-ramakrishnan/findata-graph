---
title: "note_search carve-out: last FTS5 out of research.db into a DuckDB native-FTS sidecar"
status: proposed
filed: "2026-11-15"
executed:
completed_md:
area: "helpers/maintenance/rebuild_note_search.py + helpers/misc/note_query.py + app.py + helpers/graph/query.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# note_search carve-out: last FTS5 out of research.db → DuckDB native-FTS sidecar

**Date:** 2026-11-15 · **Status:** PROPOSED · **Area:**
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

**S1 — sidecar build.** Extend `rebuild_note_search.py` with a DuckDB
path: `NOTE_DB` → `note_search.duckdb`, `connect_note_db` suffix routing,
`rebuild_note_duckdb` (replacement rebuild reusing the existing walk/section
pipeline; rows + `create_fts_index('note_search','id','searchtext')`;
meta + info stamps; `_DuckCachedEmbed` for the pooled cache; `--check`
hash-only verdict + `_print_staleness`). One-time cache seed from legacy
rows (basis reconstructible from stored columns; gemma/bge stamp equality
asserted). No graph-DB rebuilds, no quack slot (small store, swap-rebuild).

**S2 — query surface flip.** `note_query.py`: `bm25_hits` → native
`match_bm25` branch (`fts_main_note_search.match_bm25`), `load_notes_matrix`
staleness gate reads the sidecar (count + dims), embedder stamp reads the
sidecar info table, `main`/CLI default `--db` → `.duckdb` (legacy via
`--db`). `app.py`: capability probe (information_schema branch), `/api/search`
hybrid (native FTS + existing KNN path), `vec_row_key`/embed read → sidecar.
`search_tui`/`master_query` notes legs engine-aware (same flip as the
S1–S5 consumers).

**S3 — graph projections.** `helpers/graph/query.py`: all `fin.note_search`
RO-ATTACH reads switch to the sidecar (or the existing
`note_vectors.duckdb` mirror where it already covers the read — choose the
fewer-hop source per site), `:872/:891/:1183-1195/:2065-2103/:4404+`;
`context_pack.py` bootstrap mirror follows.

**S4 — ops wiring.** `snapshot_db.py`: note_search virtual-table backup →
sidecar export/verify (duckdb `COPY`/export pattern); `db_maint.py`:
`SIDECAR_DUCKDB += note_search.duckdb` + covered set, legacy `.db` stays in
`SIDECAR_SQLITE`; `gc_embed_cache.py`: note Ref → live `.duckdb` store
(recipe parity with doc/script/git); `seed_note_gemma_cache.py`: retarget
`_attach_vec_db` call to the sidecar/new adapter; `store_sweep.py` census.

**S5 — legacy path + tests + docs.** Keep the sqlite rollback route green
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
