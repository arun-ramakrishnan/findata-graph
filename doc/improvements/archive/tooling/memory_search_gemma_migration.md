---
title: "memory_search gemma migration - per-surface selector + stamp-keyed query side"
status: executed
filed: "2026-10-08"
executed: "2026-10-08"
completed_md: "367"
area: "helpers/maintenance/rebuild_memory_search"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# memory_search gemma migration — per-surface selector + stamp-keyed query side

**Date:** 2026-10-08 · **Status:** EXECUTED (completed.md #367) ·
**Area:** helpers/maintenance/rebuild_memory_search

**Follows:** `script_search_gemma_adoption.md` (completed.md #363 — the
per-surface adoption pattern: D3 sidecar client, selector,
`guard_gemma_stamp` refusal) and the full-migration timing record in
`doc/local/evaluations/emb_gemma_assessment.md` §6.2 (memory = the
cheapest second surface: 90 rows, ~2-3 min cold).

## 1. Motivation

The operator's plan migrates the small registers to gemma first.
Harness-memory records are prose — gemma's winning register (code-intent
0.964, multilingual 11/12) — and the whole cohort is 90 rows, so the
migration risk is the WIRING, not the embed. Executed same day as
filing.

Found along the way: the query side's local fallback was
granite-hardwired (`rds.query_embedder()`), so a gemma-stamped memory
index would have degraded to bm25-only SILENTLY — the 512 vs 384 dims
mismatch empties the cosine leg (the house degradation contract working
as designed, but the wrong model underneath).

## 2. Execution record (S1-S3, same day)

- **S1 selector + guard**: `resolve_memory_embedder()` — gemma whenever
  the sidecar/model is available, granite otherwise;
  `MEMORY_EMBEDDER=granite` is the explicit demotion escape;
  `guard_gemma_stamp` wired into the rebuild write path (a gemma-stamped
  memory index refuses fallback-model rebuilds — the script stamp's
  un-migration accident class).
- **S2 gemma prefix basis**: the SAME title/purpose/content through the
  shared `rebuild_script_search._gemma_basis` recipe (cap unchanged —
  recipe parity with the granite path); the prefix is load-bearing
  (1.000 -> 0.880 without).
- **S3 stamp-keyed query side**: `_query_embedder_for(stored stamp)` —
  gemma `embed_query` (search-task prefix) on a gemma-stamped index;
  anything else keeps the granite query embedder; sidecar down degrades
  to BM25-only, never mixed-model scores.
- **embed-gc**: the memory Ref follows the stamp (`text_by_model` ->
  the shared gemma recipe) — live gemma rows read referenced, granite
  rows retained as rollback.

## 3. Verification

- Tests: 7 contract tests in `tests/test_rebuild_memory_search.py`
  (gemma stamp + prefix basis on all 7 rows, sidecar-down refusal +
  explicit `MEMORY_EMBEDDER=granite` escape, cosine-leg stamp keying);
  63 green across the memory/gc/gemma modules, ruff clean.
- Live: `make memory-search-rebuild` — 90/90 cold (~2-3 min), stamp
  `embeddinggemma-2-q8_512`/512, recall sanity green through the real
  `memory_query` CLI (mode=hybrid, top hit the correct record),
  embed-gc memory cohort 90 referenced / 0 dead / 145 granite retained.

Gates: targeted only (pytest + ruff as above); the full quartet ran
2026-10-08 morning on the parent series. Fusion shape unchanged —
memory stays hybrid (bm25 fusion; exact-name lookups are bm25's floor).
