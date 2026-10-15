---
title: "co_mentioned_in weight carry-through — derive-time pair counts"
status: executed
filed: "2026-10-14"
area: "helpers/graph"
executed: "2026-10-15"
completed_md: "89"
---

# co_mentioned_in weight carry-through — derive-time pair counts

**Date:** 2026-10-14 · **Status:** EXECUTED

## 1. TL;DR

`co_mentioned_in` edges are the only projection edge type that carries
**no weight** — all 1,329 edges are unit-weighted. The sibling
`cited_in` lane already solved the identical problem
(`derive_cited_in.backfill_weights`: `properties.n_quotes + 1`, live
range 1.0–44.0). The per-pair co-mention count is computable at derive
time from the same notes-corpus surface that produces the edges.

**Measured (2026-10-14, live):**

| edge type | rows | weight |
|---|---|---|
| `cited_in` | 1,977 | 1.0–44.0 (backfilled) |
| `co_mentioned_in` | 1,329 | **all 1.0** (no count survives the store) |
| `competes_with` | 3,578 | 0.4–1.0 |
| `invested_in` | 800 | stake % |

`pagerank_weighted` currently treats every co-mention as equal; a pair
co-mentioned in 12 notes scores the same as a pair co-mentioned once.

## 2. Why this was deferred

`doc/improvements/archive/graph/pagerank_graph_enhancements.md` §6
(Implementation addendum, 2026-09-22):

> *"co_mentioned_in stays unit-weighted: no per-pair count survives the
> store (properties hold only the latest edition; UNIQUE collapses
> cross-edition history) — derive-time gap, revisit with the
> notes-corpus surface."*

The notes-corpus surface is now available: `derive_co_mentions` scans
newsletter enhancement blocks and already knows which companies appear
together. The count is a free byproduct of that scan.

## 3. Design

- `helpers/graph/derive_co_mentions.py` — count co-occurrences per
  unordered company pair during the enhancement-block scan; store the
  count as the edge `weight`.
- `helpers/graph/derive_co_mentions.py::backfill_weights()` — idempotent
  UPDATE from the same scan (mirrors `derive_cited_in.backfill_weights`).
- Weight = `count + 1` (same convention as `cited_in`: `n_quotes + 1`).
- Live range will be 2.0–N (every existing edge has count ≥ 1).

## 4. Acceptance

1. `make derive-co-mentions` (dry-run) reports the count distribution;
   `--apply` writes weights.
2. `pagerank_weighted` top-10 changes (report side-by-side).
3. `tests/test_derive_co_mentions.py` — weight written = count + 1;
   idempotent re-run converges.
4. `make static-checks` clean.

## 5. Non-goals / risks

- **No change to `pagerank` (unweighted).** The plain metric stays
  unit-weighted per the S2 contract note.
- **No change to the edge `properties` JSON.** The weight lives in the
  `weight` column, same as `cited_in`.
- **Cross-edition collapse is not solved.** `UNIQUE(source, target,
  edge_type)` still collapses history; the weight reflects the latest
  edition's count. This is the same limitation `cited_in` accepts.

## 6. Follows

- `doc/improvements/archive/graph/pagerank_graph_enhancements.md` §3 S2
  (the deferred slice this executes).
- `helpers/graph/derive_cited_in.py::backfill_weights` (the proven pattern).
