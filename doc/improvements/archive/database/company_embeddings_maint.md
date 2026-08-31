---
title: Cached Company-Embeddings Refresh in maint-full
status: executed
filed: '2026-08-21'
executed: '2026-08-21'
completed_md: '142'
area: helpers/graph/embeddings.py
---

# Proposal: Cached Company-Embeddings Refresh in maint-full

**Status:** EXECUTED (2026-08-21) — code + tests landed; the one-time
cache-seeding populate ran (sidecar: 2,295 = 1,227 note_search + 1,068
company entries) and the first live `make maint-full` served step 6b fully
warm (`embed cache: 1068 hits, 0 misses`; table 1,068 rows, single bge
label). Archived same day per the execution rule.
**Date:** 2026-08-21
**Author:** Agent analysis (user-directed)
**Builds on:** `doc/improvements/archive/database/local_embeddings.md` (implemented
2026-08-20; live apply user-held) — this closes the staleness gap it left
open. Operator-facing how-to for the current state lives in
`doc/procedures/embeddings.md` (this proposal, not the procedure, is the
work item; the procedure gets updated when this executes).
**Scope:** `helpers/graph/embeddings.py`, shared extraction of the Q3 embed
cache, one new maint-full step. Explicitly out of scope: quant/model change
(q4_k_m), per-company fingerprint tables, HNSW.

---

## 1. TL;DR

`company_embeddings` is refreshed only by hand, so company vectors drift
stale whenever letter processing rewrites notes — and even after the
one-off apply, a maint-full refresh would re-embed all 1,050 companies
(~15–20 min) because the populate path doesn't use the Q3 cache. Fix: route
`populate_local` through the same content-hash cache, extract the cache
into a shared module, and add a maint-full step that is a no-op before the
user's apply and a seconds-scale warm refresh after it.

## 2. Problem

1. **Staleness by construction.** Nothing in maint-full touches
   `company_embeddings`. A company's text basis is `name + sector + note
   body[:5000]`; chatter/profile edits change it, but the stored vector
   stays until someone reruns the populate command. The note_search half
   already solved this (cache = economically incremental full scan).
2. **The apply doesn't seed the cache.** `populate_local` embeds via
   `embed_documents` directly. So the first maint-full refresh AFTER the
   apply is fully cold: ~1,050 × 0.8s ≈ 15–20 min inside a housekeeping
   run — exactly the budget class the Q3 cache was built to avoid.
3. **Manual refresh is all-or-nothing.** `--company X` exists but there is
   no principled "refresh what changed" path, and deleted companies leave
   stale rows behind (`INSERT OR REPLACE` never removes).

## 3. Design

### 3.1 Shared cache module

Extract `_CachedEmbed` + DDL from `rebuild_note_search` into
`helpers/core/embed_cache.py` (same sidecar table
`vecdb.note_search_emb_cache`, same `(sha256(text), model)` key — company
texts are just another text population; one cache serves both indexers).
`rebuild_note_search` imports it; behavior unchanged, tests move with it.

### 3.2 `populate_local` goes through the cache

Batch `embed_documents` stays, but per-company lookups first: unchanged
companies are cache hits (no embed), changed ones re-embed and update the
cache. With the apply now seeding the cache, every later refresh is warm:
~1,050 note reads + hashes ≈ seconds.

Add GC while we're here: after populate, delete rows whose company_name is
no longer in `entities` (stale-vector hygiene, mirrors the rebuild's
deleted-file handling).

### 3.3 New `--maint` flag (the maint-full entry point)

`python3 helpers/graph/embeddings.py --maint` — best-effort, never fails
the run:

- local embedder unavailable → one WARNING, exit 0 (mirrors
  rebuild_note_search's pseudo fallback stance; company embeddings stay
  as-is rather than silently regressing to pseudo).
- table's current model is NOT `bge-small-en-v1.5` (i.e. pre-apply pseudo
  rows) → one WARNING ("run the local_embeddings §11 apply"), exit 0.
  **maint-full must never auto-upgrade company embeddings** — the upgrade
  is the user-held apply; maint only keeps an already-applied table fresh.
- otherwise → cached populate + GC, print hits/misses, exit 0.

The existing `--model bge-small-en-v1.5` interactive path is unchanged
(and now also seeds the cache via 3.2).

### 3.4 maint-full placement

New step directly after `rebuild-note-search` (step 6) — both are "refresh
derived indexes over note text", and note content is not rewritten later in
the stack (note-rendering is standalone since the --no-notes decouple).

Rule compliance (maint-vs-maint-full invariant): the step writes only the
`company_embeddings` SQLite table → maint-full-eligible. DuckDB
`v_embeddings` is derived state materialised on `connect()` (no
entities/graph_edges writes → no paired graph rebuild), but the standing
snapshot rule applies: **`v_embeddings.parquet` drifts after any run that
changed vectors → snapshot regen stays in the user's db_sync flow**, same
as today.

## 4. Plan

1. Extract `helpers/core/embed_cache.py`; re-point rebuild_note_search
   (pure refactor; all existing cache tests must stay green).
2. `populate_local` via cache + GC; `--stats` gains hit/miss echo when a
   cached run happened.
3. `--maint` flag with the three-way gate (§3.3).
4. maint.py TIER2 entry + docstring; Makefile untouched (standalone
   `embeddings.py --model …` stays the apply path).
5. Tests (§5); targeted suites + ruff.

## 5. Tests

- Cached populate: cold run misses 1,050-equivalent fixture rows and seeds
  the cache; warm run hits everything and re-embeds only a changed note.
- GC: delete an entity → its embedding row is removed on the next run.
- `--maint` gate: pseudo-labelled table → WARNING + exit 0 + no writes;
  unavailable embedder → WARNING + exit 0; applied table → refresh runs.
- Placement: maint TIER list includes the step with the right command.
  (All hermetic via the established fake-local monkeypatch pattern.)

## 6. Costs & Success Criteria

- Warm maint-full step: seconds (reads + hashes; ~0 embeds on a no-change
  cycle; ≈ changed-notes × 0.8s otherwise).
- Cold paths: the apply itself (one-off, now cache-seeding), model swap,
  snapshot-restore (sidecar wiped) — same cold-cost profile as note_search.
- Success: maint-full leaves `company_embeddings` byte-identical on a
  no-change cycle (0 misses), refreshes exactly the touched companies
  after letter processing, never writes when pre-apply or unavailable, and
  `stats()` still shows exactly one model label.

## 7. Out of Scope

- q4_k_m / bge-base swaps (constants + re-pin if ever wanted).
- Per-company fingerprint table to skip note re-reads (reads are ~free at
  this corpus size).
- HNSW / ANN indexing (brute-force is ~3ms at 1k rows; tracked elsewhere).
- Auto-upgrade of pseudo tables inside maint (deliberately user-held).

## 8. Implementation log (2026-08-21)

- `helpers/core/embed_cache.py` — shared module: `CachedEmbed` (per-text
  wrapper, moved verbatim from rebuild_note_search) + `cached_embed_batch`
  (the batch counterpart §3.2 needed: bulk-loads the model's cache slice,
  embeds ONLY the misses through one `embed_documents` call, stores them
  back, and COMMITS inside the call — the --check pre-warm lesson).
  Guards: a short embedder reply raises instead of silently shifting
  vectors onto the wrong companies; a corrupted cache row counts as a miss.
- `rebuild_note_search.py` imports the shared wrapper; behavior, stats
  keys, and the sidecar table name (`note_search_emb_cache` — renaming
  would orphan warm caches) are unchanged.
- `embeddings.py`: `populate_local` via `cached_embed_batch` (this is what
  makes the interactive apply seed the cache) + GC
  (`DELETE ... NOT IN (SELECT name FROM entities)`); `maint_refresh()` is
  the `--maint` three-way gate; `main()` gained an `argv` param (house
  pattern) so the CLI wiring is testable.
- maint.py TIER2 step 6b right after rebuild-note-search (9 TIER2 steps
  now); Makefile untouched per §3.4.
- Tests: `tests/test_embed_cache.py` (batch contracts) + new classes in
  `tests/test_embeddings.py` (cold/warm, changed-text, GC, all three
  --maint gates, CLI wiring) + `tests/test_maint.py` placement; 94 green
  across the four affected files. ruff (E+F and S,UP,C901) + ty clean.
- Verified live (read-only): `rebuild_note_search.py --check` through the
  shared module = 1.7s warm over 1,227 docs; live `company_embeddings` is
  1,068 rows, single bge label → `--maint` takes the refresh branch.
- **Operator follow-up:** the original apply predated the company-side
  cache, so the first `--maint` would be cold (~15–20 min inside
  maint-full). Seed once instead: re-run the populate in
  `doc/procedures/embeddings.md` step 3 (deterministic → identical
  vectors, cache seeded), after which maint-full refreshes are
  seconds-scale.
