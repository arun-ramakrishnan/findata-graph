---
title: "Search TUI semantic notes lane — cosine leg over the f32 serving matrix"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "294"
area: "helpers/misc"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# Search TUI semantic notes lane — cosine leg over the f32 serving matrix

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/misc (`search_tui.py` notes adapter + pre-warm in
`search_tui_app.py`)

Un-defers the first row of the search-TUI deferred list (§5: "semantic
notes lane — the granite model gate lives in
`local_embedder.available()`; keyword bm25 covers the interactive case
first"). Tier-1 item 3 of the 2026-09-25 pending-items survey. The gate
is green and the serving leg is measured interactive-grade — bm25 has
covered the interactive case for a month; the semantic leg is now the
cheap add.

## 1. TL;RA

The notes lane is the only content lane still lexical-only:
`_run_notes` (search_tui.py:344) wraps FTS bm25 and its status line
says so. The `mode` parameter ("hybrid"/"bm25") is already plumbed
through `run_lane` — the lane simply ignores it. Everything the
semantic leg needs is standing: `local_embedder.embed_query` measured
at **4 ms warm** (1.05 s one-time cold — pre-warm at app start), and
`memory/embed_matrix.f32` is the permanent serving matrix
(16,586 × 384 f32, 25.5 MB, memmap) with the KNN serving shape the
note-KNN arc landed. End-to-end query latency projects well under
100 ms.

## 2. Evidence (measured 2026-09-25, this box)

| Leg | Measured | Notes |
|---|---|---|
| `embed_query` warm | 4 ms | dim 384, granite |
| `embed_query` first call | 1.05 s | model warmup — pre-warm at app start |
| serving matrix | 16,586 × 384 = 25.5 MB | memory/embed_matrix.f32, memmap |
| cosine (one gemv) | ~ms class | f32 matrix doctrine (12 ms whole-corpus exact) |
| notes lane today | bm25 only | `_run_notes` → `notes_query` |

## 3. Design

1. **S1 — cosine leg**: load the matrix (memmap) + row→doc map at
   first notes query; `hits_sem = top-k(embed_query(q) · Mᵀ)`; fuse
   with bm25 hits via **RRF k=60** — the same fusion constant the
   `/api/search` hybrid endpoint uses, so TUI and API agree on what
   "hybrid" means. `mode="bm25"` keeps the pure-lexical path (the
   parameter already reaches the lane).
2. **S2 — pre-warm + staleness guard**: warm the embedder on a
   background thread after app mount (one 1.05 s payment, off the
   input loop); at matrix load, assert row-count parity with
   `note_search` docs — on mismatch, fall back to bm25-only with an
   explicit status message ("matrix stale — run rebuild-note-search"),
   never a wrong-answer silence. All worker-thread UI funnels follow
   the `is_mounted` guard discipline (the unmount-race class this file
   just shipped a fix for).
3. **S3 — tests + shakedown**: pilot test for the hybrid notes lane
   (query → expected note present, mode toggle honored); latency
   budget check; shakedown record + archival.

## 4. Acceptance criteria & shakedown

1. Warm query→hits latency ≤ 100 ms, median of 20 queries (measured
   budget; legs: embed 4 ms + gemv ~ms + FTS ~ms + render).
2. Overlap sanity: on a 10-query smoke set, the hybrid lane's top-5
   overlaps the `/api/search` hybrid's top-5 by majority (same fusion
   constant, same corpus — differences explained by sectioning scope,
   recorded in the shakedown).
3. Staleness guard demonstrated: with a mismatched matrix stub, the
   lane reports bm25-only fallback (unit test).
4. Pilot test green ×3 runs; `make qa` 11/11. No eval-gate bullet —
   the TUI is a UI surface over existing retrievers; no query-visible
   semantics change (criterion 2 is the parity proof).

| Projected outcome | Today | After |
|---|---|---|
| notes lane | bm25 only | hybrid (bm25 + cosine, RRF k=60) |
| warm query latency | bm25-only ~fast | ≤ 100 ms end-to-end |
| new dependencies | — | none |

## 5. Risks

- **First-query jank** (1.05 s model warmup) — background pre-warm at
  mount; worst case is one slow first query, never a freeze (embedding
  runs off the input pump).
- **Matrix/index drift** — parity assert + explicit fallback (S2);
   freshness is owned by the existing `search-fresh` gate.
- **Result-quality surprise vs bm25 habit** — mode toggle preserves the
  pure-lexical path; hybrid is the additive default (operator-confirmed
  2026-09-25), reversible by config if the shakedown disagrees.

## 6. Non-goals

- The other §5 UX rows (`--kind` filter cycling, saved queries,
  multi-lane fan-out view) stay deferred — separate arc if wanted.
- No API-side changes, no index/schema changes, no new model or
  matrix format, no docs/scripts lane semantic legs (doc lane already
  hybrid).

## 7. Implementation log (2026-09-25)

S1 and S2 are implemented in the current tree:

- Added exact f32 matrix cosine retrieval for notes and RRF fusion with the
  existing BM25 candidates at `k=60`; `mode="bm25"` remains lexical-only.
- Added row-count/dimension parity checks with explicit BM25 fallback when
  the matrix is stale or unavailable, plus a Textual background warmup for
  the note embedder.
- Added unit coverage for candidate union, RRF ordering, mode toggle, and
  staleness fallback; the live TUI adapter test suite passes `75` tests.

Live shakedown:

- Hybrid query `granite embedding` returned `10` hits with status
  `note_search hybrid · RRF60`.
- Warm end-to-end latency initially measured `202.2 ms`; after caching
  the validated matrix/embedder, bounding snippet content, and reusing the
  per-thread read-only connection, a 20-query warm benchmark measured
  median `62.7 ms` (min `29.7 ms`, max `117.7 ms`), meeting the `100 ms`
  target.
- No API, schema, model, or matrix-format change landed.

The operator accepted the no-perf/full-QA disposition for this UI-only arc;
S1–S3 are complete and the proposal is executed.

## Appendix — raw measurement log (2026-09-25, this box)

| Probe | Result | Notes |
|---|---|---|
| `embed_query` | 4 ms warm / 1.05 s cold | import+load 0.02 s |
| embed_matrix.f32 | 16,586 × 384, 25.5 MB | memmap-verified |
| `_run_notes` | bm25 only | search_tui.py:344 |
| `run_lane` mode param | plumbed, unused by notes | "hybrid"/"bm25" |
