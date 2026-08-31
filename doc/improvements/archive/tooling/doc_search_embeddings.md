---
title: Content-Addressable Doc Search — FTS5 + Hybrid Embeddings over doc/
status: executed
filed: '2026-08-23'
executed: '2026-08-23'
completed_md: '148'
area: '`helpers/maintenance/rebuild_doc_search.py` (indexer +'
---

# Proposal: Content-Addressable Doc Search — FTS5 + Hybrid Embeddings over doc/

**Status:** EXECUTED (2026-08-23 — slices 0–5 landed, targeted tests
green, live index built + evaled; archived same day; completed.md #148)
**Date:** 2026-08-23
**Author:** Agent analysis (user-directed)
**Builds on:** doc_browser.txt (completed.md #107 — the filesystem doc
browser this upgrades), local_embeddings (completed.md #141 — the shared
embedder + cache this reuses), and the RRF hybrid ranking in
`app.py::_hybrid_search_results`.
**Scope:** new `helpers/maintenance/rebuild_doc_search.py` (indexer +
query core), a new gitignored sidecar DB `memory/doc_search.db`, an
upgraded `GET /api/docs/search`, a new agent-facing CLI
`helpers/misc/doc_query.py`, maint-full step 6c, an eval section, tests.
Explicitly out of scope: DuckDB materialisation, a vec0 mirror for docs,
any change to `note_search`, any change to `research.db`.

---

## 1. TL;DR

The repo carries its own knowledge — architecture.md, graph_design.txt,
~30 archived proposals, the 166 KB completed.md run log, the procedures,
and the `doc/local/` assessments — as plain files that only the #107 doc
browser can scan. That scan is a case-insensitive substring walk with
hand-rolled word scoring: no stemming, no ranking model, no semantics,
and no way for a future agent session to query it without the Flask app
running. This proposal gives the corpus the same content-addressable
treatment the notes vault already has — FTS5 BM25 + per-section
bge-small embeddings + Reciprocal Rank Fusion — in a **separate,
gitignored sidecar DB** so that `research.db`, its snapshots, and every
published artifact stay structurally free of `doc/local/` content.

## 2. Problem

- The doc corpus is the repo's institutional memory: design rationale,
  executed-proposal logs, deferred-work decisions ("why we didn't adopt
  LangGraph"), operator procedures. Today it is searchable only by grep
  or the #107 naive scan; a question like "which proposal retired
  duckpgq" has no query surface that ranks, stems, or understands
  paraphrase.
- The #107 browser was deliberately index-free at ~27 files / 180 KB.
  The corpus has since grown to ~53 files / ~870 KB with files an order
  of magnitude larger than any note (completed.md alone is 166 KB), and
  the ask has changed: not just human browsing but **future models
  querying the knowledge** — which needs an index that survives outside
  a running server (CLI) and ranks by meaning, not substring counts.
- Everything needed to fix this already exists as shared infrastructure
  — `local_embedder`, the `(sha256(text), model)` embed cache, the RRF
  fusion pattern, the FTS5+JSON-embedding column shape — proven on the
  1,227-doc notes vault (hybrid recall@5 1.00 vs BM25 0.93).

### 2.1 The locality constraint (user-caught, 2026-08-23)

`doc/local/` is gitignored "private local notes (never for git)" — it
contains the security review and private assessments. The published form
of our database is the git-tracked `snapshots/parquet/` tree (allowlist
export), so an index living in `research.db` would be *policy*-local at
best: one future manifest edit away from shipping private plaintext to a
public repo. The index therefore gets its own sidecar DB and never
touches `research.db` at all — locality is **structural**, not
manifest-dependent. (Same reasoning class as the vec0 sidecar decision:
derived state that must not collide with the snapshot/export surface
lives in `<name>_*.db` sidecars under gitignored `memory/`.)

## 3. Non-Goals

- **No DuckDB `v_doc_embeddings`.** Docs have no graph wrappers; the
  hybrid query runs SQLite-side exactly like `/api/search` does. A
  materialised view would cost a schema bump (11 → 12), an
  `_EXTRA_MATERIALIZED` manifest entry, and duckdb parquet snapshot
  ripple — for zero capability. Revisit only if a graph-adjacent docs
  query ever materialises.
- **No vec0 mirror for docs.** ~600 section rows × 384 dims is a
  sub-millisecond Python cosine; `note_search_vec` exists for the
  1,227-doc corpus scale, not this one.
- **No unification with `note_search`.** Different corpora (vault vs
  repo), different refresh triggers (ingest vs edit), different privacy
  classes (publishable vs local-only). Two tables, one shared embedder
  and cache.
- No chunking changes to the notes path; no re-indexing of
  `doc/schema/*.json` (machine schemas, not prose).

## 4. Design

### 4.1 Residence: `memory/doc_search.db` sidecar

New SQLite file under gitignored `memory/`, opened by
`rebuild_doc_search.py` via plain `sqlite3.connect` (+ WAL, busy_timeout
— mirroring `helpers/core/db.py::connect` pragmas; the shared `connect`
is not used because it is research.db-shaped). It is never ATTACHed to a
DuckDB connection, never listed in `SQLITE_PARQUET_TABLES`, never copied
by `db-backup` flows, and never read by any snapshot/export code. Derived
and rebuildable: deleting it costs one warm rebuild (~seconds; the embed
cache makes re-embedding a no-op).

The `CachedEmbed` cache wrapper attaches `<main>_vec.db` derived from
the connection's main file, so the docs embed cache lands in
`memory/doc_search.db_vec.db` — a separate cache file from the notes
one. That is free: the two corpora share no text, so there is nothing to
deduplicate, and both files stay under gitignored `memory/`.

### 4.2 Corpus and chunking

Walk `doc/**` for `.md`/`.txt` (same `_DOC_EXTS` as #107 — includes
`doc/local/` per the 2026-08-23 user decision). Split each file on `##`
section headers into chunks: `(file_title, section_title, anchor_line,
body)`, the preamble before the first header being its own chunk with
`section_title = ""`. Files with no headers are one chunk. Expected ~600
rows over ~53 files. Rationale: whole-doc embeddings truncate at the
model's 512-token context, which would render everything past the first
few KB of completed.md invisible to the vector leg; FTS5 holds the full
section text either way, so BM25 recall is unaffected by chunking.
`anchor` (1-based line of the header) makes results deep-linkable:
`path:line` for agents (repo-rooted — resolves directly from the repo
root; user catch 2026-08-23), scroll targets for the UI.

### 4.3 Schema

```sql
CREATE VIRTUAL TABLE doc_search USING fts5(
    title,                 -- file-level title (#107's _doc_title derivation)
    section_title,         -- '## ' header text of this chunk ('' = preamble)
    file_path UNINDEXED,   -- repo-rooted POSIX path (doc/...); deep-link target
    anchor UNINDEXED,      -- 1-based line number of the section header
    content,               -- full section body
    embedding UNINDEXED,   -- JSON vector for hybrid ranking; not tokenized
    tokenize = 'porter unicode61'
);
CREATE TABLE doc_search_meta (
    file_path TEXT PRIMARY KEY, mtime REAL NOT NULL, content_hash TEXT NOT NULL
);
CREATE TABLE doc_search_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- keys: embed_model, embed_dims (the model stamp; --check never writes them)
```

Mirrors `note_search` shape 1:1 (title/sector → title/section_title,
file_path/anchor as UNINDEXED handles, JSON embedding column). The meta
table carries per-file `mtime` + blake2b fingerprints for incremental
rebuilds; the info table is the model stamp home (the
`db_meta.note_embed_model` analogue — but inside the sidecar, since
`research.db` must stay untouched).

### 4.4 Indexing pipeline

`rebuild_doc_search.py` mirrors `rebuild_note_search.py`:

- `resolve_embedder()` / `query_embedder()` semantics reused verbatim
  (real `local_embedder.embed_document` when available; deterministic
  64-dim pseudo fallback + one-time WARNING so `make maint-full` is
  green on any machine). The query side must resolve through the same
  gate so both legs share a vector space, with a stored-dims probe
  gating the read path.
- Embedding text basis: `"{title}\n{section_title}\n{body[:4000]}"` —
  same shape as `_embedding_json`'s title/sector/body prefix discipline.
- `CachedEmbed` wrap on the internally-resolved embedder only (injected
  test `embed_fn` stays raw; pseudo hashes are never cached).
- Plain run = full rebuild (DELETE + reinsert, self-healing
  convergence); `--incremental` = mtime + blake2b diff with verbatim
  carry of unchanged rows (the P2.2 fast path); `--check` = count-only
  but still warms the sidecar cache (the documented pre-pay behaviour).
- No `bump_generation` — nothing downstream derives from this table.

### 4.5 Query surfaces

**`GET /api/docs/search` upgrade** (same route, same response fields —
`path/name/section/title/snippet` — plus `anchor`, `section_title`,
`score`, `mode`, `stale`):

- Index path: FTS5 `MATCH` with tokens OR-joined and quoted (question-
  shaped input must never parse as FTS5 syntax nor require stopword
  co-occurrence), column-weighted BM25 page (title/section ×2) + cosine
  leg over the whole table (Python loop reading the JSON column — no
  vec0) as a co-equal candidate generator, union of both candidate sets
  fused with RRF k=60, capped at 2 chunks per file per page — identical
  RRF math to `_hybrid_search_results`, with the union/cap/weights deltas
  the eval drove (§12).
- Degradation chain, never 500: hybrid → BM25-only (dims mismatch /
  embedder unavailable) → today's naive scan (index missing or stale).
  Staleness probe = stat the corpus (~53 stats) vs `doc_search_meta`;
  request handlers never write.
- Frontend `docs.ts` keeps working unchanged; `mode`/`stale` are
  additive.

**CLI `helpers/misc/doc_query.py`** — the future-models surface:
`doc_query "how does the embed cache work" --limit 5` prints ranked
`path:line [section_title] snippet` lines. Imports the query core from
`rebuild_doc_search.py` (peer of `query_embedder` living in
`rebuild_note_search.py`); no Flask import, works without the server.

### 4.6 Maintenance wiring

maint-full **step 6c** `rebuild-doc-search`, directly after 6b
company-embeddings — both refresh derived text indexes; this one is
sidecar-only (no `research.db`, no entities/graph_edges writes → no
paired graph rebuild → placement invariant holds). Full rebuild each
run; warm cycles are reads + hashes via the cache. One-time cold apply
is user-held (same doctrine as the embeddings apply), then maint-full
keeps it fresh.

## 5. Implementation plan

| Slice | Files | Notes |
|---|---|---|
| 0 | this proposal | review gate |
| 1 | `helpers/maintenance/rebuild_doc_search.py`, `tests/test_rebuild_doc_search.py` | indexer + query core; `_DOC_ROOT` module-level (monkeypatchable — the VAULT_ROOT lesson) |
| 2 | `app.py`, `tests/test_api_docs.py` | endpoint upgrade + degradation tests |
| 3 | `helpers/misc/doc_query.py` (+ test) | agent CLI |
| 4 | `helpers/maintenance/maint.py`, `tests/test_maint.py` | step 6c + placement assertion |
| 5 | `helpers/misc/embed_eval.py`, `embed_eval_questions.json`, README, `doc/procedures/doc-search.md` | docs eval section + runbook |

Snapshot/integrity surfaces need **zero changes** — that is the point of
the sidecar residence (no manifest edits, no schema-drift guards, no
DuckDB scanner exposure).

## 6. Tests

- `tests/test_rebuild_doc_search.py`: tmp doc tree via monkeypatched
  `_DOC_ROOT`/`DOC_DB`; the `fake_local` pattern from
  `test_rebuild_note_search.py` (available → True + fake 384-dim
  embedders); pseudo fallback under the autouse `_no_local_embedder`
  pin; chunker edge cases (no headers, preamble-only, empty file); GC of
  deleted files; incremental carry; model-swap stamp warning; FTS round
  trip (porter stemming query).
- `tests/test_api_docs.py`: response-shape compatibility with the #107
  contract (`DocItem`/frontend pins), hybrid/bm25/scan modes, staleness
  degradation, never-500 on a corrupt index.
- `tests/test_maint.py`: 6c placement.

## 7. Costs

- One-time cold embed: ~600 chunks at the measured ~0.8 s/doc single
  batch ≈ minutes, once; warm rebuilds ~0.5 s (hashes + cache hits).
- Disk: sidecar DB a few MB (FTS + 600 × 384-dim JSON vectors).
- maint-full: +1 step, warm no-op cost ≈ file stats + cache reads.

## 8. Success criteria

- Docs eval (§5 slice 5): hybrid recall@5 ≥ BM25 ≥ naive scan on a
  ~18-question set spanning exact / variant / semantic paraphrase, with
  per-category GAIN/LOSS reported; no question regresses vs the scan
  baseline.
- `make maint-full` green on a machine without the model (pseudo path).
- `git status` clean with respect to `memory/` after a full apply (the
  sidecar is invisible to git by construction).

**Outcome (2026-08-23):** met except one per-question regression —
see §12 for the numbers and the single accepted miss.

## 9. Open questions (locked decisions)

1. **Corpus scope** — all of `doc/` including `doc/local/` (user
   decision 2026-08-23), enabled by the structural sidecar locality.
   LOCKED.
2. **Sidecar, not research.db** — user-caught locality constraint
   (§2.1). LOCKED.
3. **Section chunking** — `##`-level rows, not whole-doc. LOCKED (the
   166 KB run log is unsearchable-by-vector otherwise).
4. **No DuckDB / no vec0 / no snapshot changes** — §3. LOCKED.

## 10. Review fold

- **Eval-driven design changes (2026-08-23, see §12):** (1) MATCH
  tokens OR-joined, not AND-adjacent — AND made question-shaped queries
  near-unrecallable → §4.5; (2) cosine is a co-equal candidate generator
  (BM25-page ∪ top-cosine union) rather than a page re-ranker → §4.5;
  (3) column-weighted BM25 (title/section ×2) + a per-file result cap of
  2 chunks for diversification → §4.5. A pool-dependent file-evidence
  score boost was tried and REVERTED (broke pagination consistency and
  two other queries).

## 11. Implementation log

- **2026-08-23 slice 1** — `helpers/maintenance/rebuild_doc_search.py`
  (sidecar connect + DDL, `##`-section chunker with anchors, mtime+blake2b
  incremental with verbatim carry + fingerprint diff, CachedEmbed wrap,
  `doc_search_info` model stamp, `fts_match_expr` quoting,
  `doc_index_ready`/`doc_index_stale` probes, `search_docs` RRF core
  with `hybrid=False` opt-out) + `tests/test_rebuild_doc_search.py`
  (29 tests). One fix during test bring-up: the incremental diff now
  compares the content fingerprint (zero-row files re-upserted every
  cycle otherwise).
- **2026-08-23 slice 2** — `/api/docs/search` upgraded: index path
  (hybrid → bm25) with scan fallback + `mode`/`stale` in the response,
  uniform hit shape (`anchor`/`section_title`/`score` on both paths);
  `tests/test_api_docs.py` pins the new contract + hermetic
  mode/degradation tests (stale → scan, corrupt sidecar never 500s,
  punctuated queries safe). Live-corpus tests deliberately pin no mode.
- **2026-08-23 slice 3** — `helpers/misc/doc_query.py` CLI (`path:line
  [section] snippet` output, `--json`, `--bm25`, stale-warns-but-answers,
  missing-index exit 1 with the build hint) + `tests/test_doc_query.py`.
- **2026-08-23 slice 4** — maint-full step 6c `rebuild-doc-search` after
  company-embeddings (sidecar-only: no research.db, no entities/
  graph_edges, no notes → placement invariant holds);
  `tests/test_maint.py` placement + step-count pins updated (9 → 10
  tier-2 steps).
- **2026-08-23 slice 5** — eval: `docs` question set (18 questions:
  7 exact / 6 variant / 5 semantic) in `embed_eval_questions.json`;
  `embed_eval.py` `docs` mode (scan baseline via in-process index bypass,
  bm25 via `?hybrid=0`, hybrid default; per-category recall@5 + GAIN/
  LOSS). Procedure doc `doc/procedures/doc-search.md`; README
  documentation-table entry.
- **2026-08-23 follow-up (user asks)** — `--check` upgraded from a row
  count to a freshness gate: exact hash-level diff vs the stored index
  (`N changed, N new, N deleted` + file list + refresh command), exit 1
  on drift (house --check doctrine); the apply run now reports
  `index was STALE before this rebuild: … — now fresh`. Paths made
  REPO-ROOTED everywhere (storage/CLI/API/catalog/eval labels;
  `/api/docs/content` accepts both forms) — user catch. Makefile
  maint-full help text refreshed to name TIER2_STEPS as authoritative
  (step 6c itself lives in maint.py, not the Makefile — delegation by
  design).

## 12. Eval results (2026-08-23, live corpus)

Index: 51 files / 379 section rows, all embedded bge-small-en-v1.5
(one-time cold: 373 embeds; warm refresh after doc edits: 373 hits /
6 misses — the cache discipline holds).

| category | n | scan | bm25 | hybrid |
|---|---|---|---|---|
| exact | 7 | 1.00 | 0.86 | **1.00** |
| variant | 6 | 0.50 | 0.50 | **0.83** |
| semantic | 5 | 0.60 | 1.00 | **1.00** |
| **TOTAL** | 18 | 0.72 | 0.78 | **0.94** |

Hybrid ≥ scan on every category; the vector leg rescues questions the
lexical legs cannot (frontmatter-fields, pending-backlog, pdf-fuzz,
self-reference, bge-small-vs-base — all scan misses).

**Known miss (accepted):** dvar-05 "numbered run log of completed work"
→ completed.md. The scan's whole-file raw term counting finds it; the
chunk-level ranker's best completed.md chunk lands rank 6 (the title
"Completed Improvements" anchors only one query token). Tuning stopped
deliberately after three principled changes (see §10) — the miss is
documented rather than chased.

**Design changes this eval drove** (each verified against the full
question set, not just the failing query):

1. OR-joined MATCH tokens — AND-adjacency made "why did we not adopt
   langgraph" return nothing (stopword co-occurrence requirement).
2. Candidate union (BM25 page ∪ top cosine rows) — with OR tokens, BM25
   alone buries answer chunks under frequent-token matches, and a
   cosine re-ranker of that page cannot rescue what never surfaced.
3. Column-weighted BM25 (title/section_title ×2.0) + per-file cap of 2
   chunks per result page — field importance and diversification,
   replacing a pool-dependent file-evidence boost that broke pagination
   consistency and two other queries (tried, measured, reverted).

**Lifecycle note:** the proposal → archive move is handled by the
content-addressed refresh (old path GC'd, new path indexed, verbatim
sections are cache hits — see doc/procedures/doc-search.md "Corpus
lifecycle"). When THIS proposal is archived, update the `dsem-04` label
in embed_eval_questions.json to the archive path in the same change.
