---
title: "duckdb_sidecar_fold: doc/memory/script/git FTS5 sidecars → DuckDB native FTS"
status: executed
filed: "2026-11-15"
executed: "2026-11-15"
completed_md: "524"
area: "helpers/maintenance/rebuild_{doc,memory,script,git}_search.py + helpers/misc/{doc,memory,script,git}_query.py + rebuild_convo_search.py (transition retirement)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Sidecar fold: doc/memory/script/git FTS5 → DuckDB native FTS (phase 2b of duckdb_20_sqlite_reentry)

**Date:** 2026-11-15 · **Status:** EXECUTED · **Area:**
`helpers/maintenance/rebuild_{doc,memory,script,git}_search.py` +
`helpers/misc/{doc,memory,script,git}_query.py` + `rebuild_convo_search.py`
(transitional `convo_search_fts.db` retirement) + tests.

**Follows:** the 2026-11-15 re-open of
`doc/improvements/archive/database/duckdb_20_sqlite_reentry.md` (§5–§6):
engine still DuckDB 2.0.0; FTS/vector blockers gone in practice; surviving
blockers are code-rework. This proposal is **phase 2b** of that re-entry
(the sidecar fold), de-risked by the convo pure-stack proof (#481: native
FTS +77% MRR, HNSW inline, vec0 retired) and by the spike in reentry §6.2
(doc corpus 4,508 rows → native FTS build 7.1 s, top-10 overlap vs FTS5
2–8/10 on sampled queries).

## 1. Motivation / scope

Four SQLite FTS5 search sidecars remain: `doc_search.db`, `memory_search.db`,
`script_search.db`, `git_search.db`. They are the last FTS5 consumers after
the convo port, plus the transitional `convo_search_fts.db` (665 MB) that
`rebuild_convo_search` still syncs for pre-S3 readers. Folding them to
DuckDB-native `.duckdb` stores:

- retires 4 SQLite FTS5 stores + the 665 MB transitional sidecar (one
  engine for every search index);
- unblocks the research.db port (phase 1's note_search carve-out reuses the
  same native-FTS build pattern — this arc is the production rehearsal);
- gives the same quality upside the convo lane measured (native
  `match_bm25` beat FTS5, +77% MRR on convo; result *sets* change is an
  accepted, documented divergence);
- **does not touch research.db** — zero risk to the canonical store.

Out of scope: research.db itself, note_search (phase 1, separate), embed
cache (embed_store.db), vector mirrors (already DuckDB).

## 2. Current state (verified 2026-11-15)

Each sidecar: SQLite `connect_X_db`, FTS5 virtual table (`title`, …
`content`, honored `embedding` UNINDEXED…), column-weighted
`bm25(<t>, weights)` ranking, `snippet(<t>, idx, '<mark>', '</mark>', ' … ', N)`
for highlight markup, plus a pure-Python cosine leg over stored embeddings:

| sidecar | rows | FTS5 cols | query surface | tests |
|---|---|---|---|---|
| doc_search | 4,508 | 10 (9 indexed/UNINDEXED) | `doc_query` CLI + `search_docs` (rds) | 91 (7 query + 49 rebuild + 4 fuzz + 31 api) |
| memory_search | ~? | 8 | `memory_query` CLI + `search_memory` | test_rebuild_memory_search.py |
| script_search | ~? | 7 | `script_query` CLI + `search_scripts` | test_rebuild_script_search{,_mojo,_ts}.py |
| git_search | ~? | 6 | `git_query` CLI + `search_git` | test_git_search_index.py |
| convo_search_fts.db (transition) | 665 MB | — (FTS5 sidecar) | fallback leg only | test_convo_search.py |

Shared: `rebuild_common.RebuildCliSpec`/`QueryCliSpec` (rebuild + query
CLI shells), `read/write_apply_stamp` (meta tables), `resolve_embedder`,
`rbc` helpers. The convo rebuild (`rebuild_convo_search.py`) already
contains the production pattern for everything this arc needs: `.duckdb`
store + `PRAGMA create_fts_index` + swap-rebuild + `match_bm25` query.

## 3. Constraints / decisions

1. **Native FTS index is a one-shot structure** (no incremental
   maintenance): `create_fts_index` runs on the swap-built store;
   in-place windows re-create it (drop+recreate) — exactly the convo
   `_refresh_fts_index` discipline.
2. **Column weighting is lost in the naive native index.** FTS5's
   per-column `bm25(cols, weights)` has no DuckDB equivalent beyond the
   2026-10-30 `fields:=` scope probe. Decision: index a composed `text`
   field (title + section_title + content + meta + links + trail,
   space-joined) and document the ranking change; the hybrid cosine leg
   stays the quality guard. If `fields:=` proves sufficient in the S1
   spike, we use it; otherwise the composed-text deviation is recorded.
3. **Highlight moves to Python.** `snippet()` is gone; render `<mark>`
   spans from the stored text with a `note_snippet`-style word-window
   (`helpers/misc/note_query.py:71` pattern) in the search function.
   Consumers (CLIs/API) already treat the snippet as plain text after
   `_plain_snippet`.
4. **Legacy `.db` paths stay valid** during the arc: `connect_X_db`
   opens sqlite3 for `.db`, duckdb for `.duckdb`; search functions branch
   on connection type. Defaults flip to `.duckdb` per sidecar as each
   slice lands (tests that pin `.db` keep exercising the legacy path).
5. **Read-only + swap discipline**: `duckdb_shared.assert_not_served`
   before swap-replace; stores are small (4k–100k rows) — rebuild is
   `COPY`/staged-insert, not a required server enrollment (no quack
   slot needed for these sidecars).
6. **Kinds**: same table/meta shape as convo (`*_meta`,
   `*_info` stamp tables) so `read/write_apply_stamp` works unchanged.

## 4. Slices

**S1 — doc_search fold (this arc's pilot).** Extend
`rebuild_doc_search.py` with a DuckDB store (`doc_search.duckdb`):
`connect_doc_db` branch, `_duckdb_ddl`, rebuild path writing rows +
`create_fts_index`, `search_docs` duckdb branch (native `match_bm25` +
Python highlight). Default `DOC_DB` → `doc_search.duckdb`. New tests for
the duckdb path; legacy sqlite path stays covered by existing tests.
Acceptance: built index answers `doc_query` on a seeded tree; hybrid leg
still fuses; targeted old tests green.

**S1 findings (2026-11-15, live build):**

- **Embed-cache attach is sqlite-shaped.** `CachedEmbed` attaches the
  pooled store through `_attach_vec_db`, which sets `PRAGMA
  busy_timeout` — duckdb rejects it (`Catalog Error: unrecognized
  configuration parameter "busy_timeout"`) → attach fails → EVERY
  section embeds uncached. Fix: `_DuckCachedEmbed(CachedEmbed)` in
  `rebuild_doc_search.py` points `self._conn` at a dedicated
  `sqlite3.connect(EMBED_DB_PATH)`; all inherited cache logic (attach,
  lookup, miss-fill, `status_line`) runs unchanged on it. S2–S4 must
  reuse this adapter, not `CachedEmbed(conn=duckdb)`.
- **Replacement rebuild re-embeds nothing for unchanged text** — the
  pooled cache absorbs it (content-hash keyed). One-time cost when the
  cache is cold: the full corpus pass through gemma (~4,508 sections).
  The legacy path's per-file mtime carry (zero embed calls) remains a
  later "incremental carry" slice if rebuild latency demands it.
- DuckDB store writes are autocommit per statement (no transaction
  wrapper): an interrupted rebuild can leave a partial store. Accepted
  for S1 (wholesale rebuild repairs it); revisit if it bites.
- `--check` on the duckdb store is hash-only (no DDL/writes/embedder),
  mirroring the legacy posture; the fresh verdict on a cold store is
  "stale: all files new".
- **One-time cache seed (2026-11-15):** the legacy store carried vectors
  inline and never wrote them to the pooled cache, so the first duckdb
  rebuild would have been a full cold pass (~4,510 sections × gemma @
  ~1.1/s ≈ 70 min). Seeded `embed_cache` directly from the legacy
  rows instead (basis is reconstructible from stored columns —
  `_doc_basis(title, section_title, content, gemma=True)`; hash =
  sha256; blobs bit-exact f32; model stamp equality asserted). The
  subsequent build ran 4,510 hits / 7 misses, <90 s wall. Future S2–S4
  folds get the same seed treatment per store.

**S2 — memory_search fold.** Same mechanics
(`rebuild_memory_search.py`, `memory_query`). Kind/history filters keep
their SQL forms on the DuckDB store.

**S2 findings (2026-11-15, landed):**

- **No cache seed needed** (the S1 seed treatment was NOT required): the
  memory leg already rode the pooled `CachedEmbed` (source cohort
  `memory`), so all 385 bases were warm — live rebuild ran 385 hits /
  0 misses (~7.4 s wall incl. model load).
- **Ranking parity:** mean top-5 set overlap vs the legacy FTS5 store on
  10 real queries = 4.6/5 (same figure as the reentry §6.2 doc spike);
  sets near-identical, only intra-page order drifts (composed
  searchtext `title + purpose + content` replaces the per-column
  weights 2.0/1.5/1.0 — the accepted §3.2 deviation).
- **`_stored_meta` duckdb-iterability gotcha (new):** shared helpers
  that iterate a cursor directly (`for r in conn.execute(...)`) raise
  `TypeError: '_duckdb.DuckDBPyConnection' object is not iterable` —
  `_stored_meta` was fixed with `.fetchall()`; the same rule applies to
  every helper S3/S4 will share.
- **`--check` summary parity:** the duckdb check path skips row
  composition, so `by_kind` is now derived from units (no embedding)
  to keep the legacy summary line shape (`385 records
  (opencode=99, …)`).
- **Consumers flipped:** `search_tui._run_memory` (was house-sqlite
  `connect(read_only=True, wal=False)` on the path → engine-aware
  `rms.connect_memory_db`), `master_query._SIDECAR_BY_LEG`, the
  search_tui lane/age tables, `Makefile` echo, `db_maint`
  (`SIDECAR_DUCKDB += memory_search.duckdb` CHECKPOINT-only; legacy
  `.db` stays in `SIDECAR_SQLITE` + the `covered` set).
- **GC engine flip exposed a latent doc bug:** `gc_embed_cache` Refs
  now read the live `.duckdb` stores via the existing
  `duckdb_lock.open_read_only` path (`_open_ref` was already
  engine-aware). The doc Ref had NO gemma `text_by_model` recipe — GC
  scored all 4,517 live gemma doc rows "unreferenced" and the next
  `make embed-gc --apply` would have deleted 4,753 live pooled rows.
  Fixed with `_doc_text_gemma` (rebuild's `_doc_basis(gemma=True)`);
  post-fix: doc referenced 4,517/4,517, memory 385/385.
- Tests: `tests/test_memory_query_duckdb.py` 8 (routing, build+schema,
  hybrid shape/contract, bm25-only, kind/history filters, stale-on-new,
  check-mode no-stamp, legacy rollback path); legacy suites green
  (snapshot-lane, master_query, search_tui, embed_gc, db_maint).

**S3 — script_search fold.** Same + `--kind/--area` filters; mojo/ts
doc tables ride along in the `.duckdb` file.

**S3 findings (2026-11-15, landed):**
- `rebuild_script_duckdb` reuses the whole extraction pipeline
  (`_collect_units` with the mojo/ts content-addressed doc caches living in
  the SAME duckdb store, `_compose_rows`) and `rds._DuckCachedEmbed`
  (dedicated sqlite cache handle). No seed needed: the script gemma recipe
  was already pooled → live rebuild 840 hits / 1 miss, <90 s wall.
- Composed `searchtext` = title + content (the legacy bm25 weight matrix:
  title 2.0 / content 1.0; purpose rides inside content as the labeled
  block). kind/rel_path/area became plain SQL columns — filters are now
  real predicates instead of FTS5 UNINDEXED columns.
- `search_scripts` dispatches on conn type; the gemma shape switch
  (identifier → lexical, paraphrase → vector-only) is preserved verbatim;
  result-dict shape identical (mode/results, `area` key) — script_query and
  /api/scripts consumers untouched.
- Engine-tolerance edits: `_stored_meta`/`_cosine_leg`/`_rows_by_rid`
  `.fetchall()` (duckdb conns are not row-iterable), `script_index_ready`
  information_schema branch, `script_index_stale` catch-all, mojo/ts cache
  exception broadening.
- NEW confirmed gotcha: DuckDB `with conn:` CLOSES the connection on exit
  (ConnectionException "Connection already closed!") — both decl-cache
  INSERT blocks are engine-branched (duckdb autocommits per statement).
- Bug found by the contract suite while folding S3: the S1 doc duckdb
  store declared `anchor` VARCHAR (FTS5 had it INTEGER), violating the
  DocSearchHit `anchor: number | null` contract → emit-side int()
  normalization added in `search_docs` (rebuild_doc_search.py).
- Tests: `tests/test_script_query_duckdb.py` 8 (routing, build+schema+
  doc-cache tables, hybrid shape/contract, bm25-only, kind/area filters,
  stale-on-new, check-mode no-stamp, legacy rollback path); legacy suites
  green (rebuild_script_search, script_query, api_scripts_search,
  embed_gc, db_maint, ts_contract, doc/memory duckdb legs).
- Live ranking parity vs the FTS5 store: top-5 overlap mean 4.8/5 on the
  10-query bank (9/10 exact; one bm25 lexical query diverges in ranks 4–5
  on punctuation-token matching — expected native-vs-FTS5 detail).

**S4 — git_search fold.** Same; `git_search_body` embedding table joins
the store (cos leg unchanged, now reads DuckDB).

§S4 findings (LANDED 2026-11-15):

- Store: `git_search` (id PK, sha, adate, subject, files, content,
  embedding, searchtext=diff content) + `git_search_body` (sha,
  chunk_idx, embedding, chunk_fp) + meta/info stamps. Native
  `create_fts_index('git_search','id','searchtext')`; query path =
  `fts_main_git_search.match_bm25` + Python `<mark>` highlight + cosine
  leg (head + body chunks) + RRF; identifier/rare-literal router
  preserved.
- Consumers flipped: `git_query.py` (same `QueryCliSpec` pattern),
  `search_tui.py` (lane + ages), `master_query.py` (git leg), `store_sweep.py`
  census, `gc_embed_cache.py` git Ref, `git_eval_bank.py` `--db`
  default + docstring.
- Engine-tolerance needed (same class as S3): `.fetchall()` on meta/cos
  scans; `git_index_ready` information_schema branch; rare-literal count
  probe via `match_bm25`; `_embed_pass(cache_conn=...)` dedicated sqlite
  cache handle (embed cache is sqlite; DuckDB cannot host the vecdb
  attach); `EMBED_DB_PATH.parent.mkdir` before open.
- Fresh/aborted-store safety: `PRAGMA drop_fts_index` needs try/except
  (CatalogException when no index yet); a stale partial schema (base
  without id/searchtext + FTS shadows from an aborted build) survives
  `IF NOT EXISTS`, so the replacement rebuild drops shadows + base and
  recreates. A truly corrupt vtable (`vtable constructor failed`) could
  not be repaired in place — deleted the derived `.duckdb` and rebuilt
  warm (0 embed misses).
- Live: 574 commits/574 rows/1,677 body chunks, gemma stamp, 574 cache
  hits/0 misses; top-5 parity vs legacy sqlite 4.5/5 (top-2 agree, two
  queries swap middle ranks). `--check` on a fresh path now reports 0
  rows instead of raising (CatalogException on missing table).
- Tests: `tests/test_git_query_duckdb.py` (8 tests) — route-by-suffix,
  rebuild builds store+index, hybrid shape (mode may fall to bm25 under
  the rare-literal router; vector-only via explicit `QueryVector`), bm25
 -only, since/path filters, stale-on-new-commit, check-mode no-stamp,
  legacy sqlite rollback. 51 pass across git + db_maint suites; ruff/md-lint clean.

**S5 — convo transition retirement.** Remove the FTS5 sidecar sync
(`_fts_sync`, `FTS_DDL`, `fts_db_for`) and delete
`convo_search_fts.db`; `convo_query` drops the `_fts_hits_sqlite`
fallback. Acceptance: `grep -r "convo_search_fts" helpers/` → 0; live
convo queries native-only; snapshot/sweep references updated.

§S5 findings (LANDED 2026-11-15):

- `rebuild_convo_search.py`: `FTS_DB_NAME`/`FTS_DDL`/`fts_db_for`/
  `_fts_delete_keys`/`_fts_sync` removed; the convo embed-cache host is
  now the shared `memory/embed_store.db` (the retired sidecar was the
  only sqlite cache host left standing; `EMBED_DB_PATH.parent.mkdir`
  guard added). Rebuild flow: read window → embed (lockless) → write
  window → native-FTS refresh (its own io.lock window) → meta window
  (still ordered after the refresh succeeds).
- `convo_query.py`: `ConvoConnections` is single-handle now (ddb + db
  path; `sconn` gone); `connect()` opens no sqlite sidecar; `_fts_hits`
  is native-only (`_fts_hits_sqlite` deleted) and a store without the
  native schema answers with an empty lexical leg, never an error. The
  native FTS lives inside the same duckdb store that carries provenance,
  so the lexical leg now pre-filters `source` in SQL — the 25x
  over-fetch workaround and its comment are gone.
- Consumers: `db_maint.py` (SIDECAR_SQLITE + covered set + backup
  docstring), `store_sweep.py` `_FILING_MB`; `memory/convo_search_fts.db`
  deleted (669 MB freed; the native index lives inside the `.duckdb`
  store and was untouched).
- Tests: `_fts_hits_sqlite` fallback test → `test_native_fts_missing_returns_empty`;
  FTS delta tracking now asserted natively (fresh part searchable,
  untouched rows still answer); `test_rebuild_fts_sync_runs_outside_the_duckdb_window`
  removed (the sync step no longer exists; the native refresh holds its
  own window instead); bili/provenance fixtures rebuilt single-handle.
  105 pass across convo/db_maint/store_sweep/bili/provenance/master_query
  suites; ruff clean.

**S6 — close-out.** Full gates once (qa/integration/perf/advisory) on
operator go; archive the proposal; record in completed.md.

## 5. Acceptance criteria (arc)

- `grep -rl "fts5" helpers/` → 0 (production files; note_search excluded
  — it is phase 1, and the FTS5 references there are documented as such).
- All four query CLIs answer on their `.duckdb` stores with hybrid mode
  intact; `--db <legacy .db>` still works on the legacy path.
- `convo_search_fts.db` gone; convo sidecar sync code removed.
- Targeted suites green; full qa once on operator go.
- md-lint + search-fresh clean.

> **Follow-on (2026-11-16): the note_search carve-out has landed.** The
> phase-1 successor (`doc/improvements/proposals/database/note_search_carve_out.md`)
> completed S0 (embed-cache fold) → S4 (ops wiring). The note FTS5 shadow
> in `research.db` is now a documented rollback artifact (legacy rebuild
> path kept green), and the live note index is the DuckDB sidecar
> `memory/note_search.duckdb`. The residual `fts5` grep allowance from the
> line above is now scoped to that rollback path only.

## 6. Sizing (re-baseline from reentry §6.4)

S1 3–4 d (pilot + test rework), S2–S4 ~1.5–2 d each (pattern reuse),
S5 0.5 d, S6 0.5–1 d → **≈8–11 d total** (vs the 3–5 d coarse slot in
reentry §6.4; the pilot carries the uncertainty). This lands before the
research.db port and reduces its phase-5 consumer sweep by the four query
CLIs already ported.
