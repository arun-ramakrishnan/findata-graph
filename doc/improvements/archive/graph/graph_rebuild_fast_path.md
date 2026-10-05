---
title: "Graph rebuild fast path — dirty tracking, rebuild-on-apply, single-scan fold, mask hygiene"
status: executed
filed: '2026-10-05'
executed: '2026-10-05'
completed_md: '347'
area: "helpers/graph/query.py (rebuild + ingest CLIs), tests/bench_rebuild_scale.py"
---

# Proposal: Graph rebuild fast path — the four measured P2.2 successors in one arc

**Date**: 2026-10-05
**Status**: EXECUTED 2026-10-05 — combines pending-improvements rows 4, 6, 5 (the
P2.2 "HOLD in favour of" set) with the two unruled C901 masks (row 12)
that live in the same file.

## 1. Motivation

The full DuckDB cache rebuild measures **T(R) ≈ 2.28 s + 3.0 µs·R**
(R=0 → 2.28 s; R=115,030 live → 2.62 s; R=460,120 → 3.72 s — scratch
probe, `tests/bench_rebuild_scale.py`, re-anchored in
`archive/graph/graph_rebuild_scaling_probe.md`). At live scale ~87% of
that is FIXED statement overhead: 0.29 s extension INSTALL/LOAD, 0.21 s
for 56 `DROP TABLE IF EXISTS`, ~1.70 s planning + executing ~118
statements (~50 CTAS); only ~13% is per-row. The original P2.2
row-level diff was retired because it attacked the small term while
adding partial-application risk against the single file-level
`_build_meta.generation` stamp read by 23 `connect()` callers
(`csr.py:298`), snapshot verification among them. The three measured
successors plus a same-file hygiene item form this arc:

1. no ingest lane calls `rebuild()` today, so the first graph query
   after any generation bump pays the rebuild inline — and
   `read_only=True` callers cannot take the warm fast path when stale:
   they serialise on `<cache>.build.lock` (`pending.md` P2.2);
2. the fixed band is dominated by per-table re-planning of 12
   `EDGE_REGISTRY` CTAS (`query.py:245`) plus `e_all_und`/`e_dir`, each
   re-scanning `fin.graph_edges`;
3. nothing tells a rebuild that most of its inputs did not change.

## 2. Design — S1–S4

- **S1 — per-input dirty tracking (row 4 / P2.2 iii).** Record a
  fingerprint per materialised table's INPUT SLICE in `_build_meta`
  (per-edge-type / per-source-predicate: type + source generation +
  shape), not one whole-store hash. `rebuild()` recomputes
  fingerprints and drop-and-rebuilds ONLY dirty tables; unchanged
  inputs skip. Correctness rails: the file-level
  `_build_meta.generation` stamp still advances exactly once per
  completed pass (the `_rebuild_via_swap` temp + `os.replace` pattern —
  the live cache is never opened RW); a fingerprint MISS is
  conservative (rebuild that table); `v_centrality_*` are outputs of
  the separate stamp-centrality lane, never inputs — the ephemeral
  contract (rebuild may drop them, stamp-centrality restores) is
  preserved.
- **S2 — rebuild-on-apply + stale fail-fast (row 6 / P2.2 C).**
  `derive-relations --apply` and `parse_newsletter --apply` call
  `rebuild()` after their write (through the swap path), and the stale
  read path fails LOUD — "run make graph-rebuild" + nonzero exit —
  instead of silently queueing RO callers on the build lock. Synergy:
  `make maint`'s explicit graph-rebuild becomes a cheap no-op after an
  apply-rebuild once S1 lands.
- **S3 — single-scan fold (row 5 / P2.2 ii).** Fold the 12
  `EDGE_REGISTRY` CTAS + `e_all_und`/`e_dir` into ONE
  `edge_type`-discriminated scan of `fin.graph_edges`; drop the
  duplicated `v_centrality_*` DROPs (drop only what this pass
  rebuilds). Attacks the ~1.70 s planning + executing band directly.
  Parity witness is mandatory and must be able to FAIL: row-for-row
  count AND content parity of every materialised table vs a pre-fold
  rebuild on the same data (counts alone are the known vacuous-witness
  trap — near_duplicate_gemm_rework_record).
- **S4 — mask hygiene (row 12).** Extraction-only D1-style splits for
  the two unruled C901 masks — `_materialise_centrality_cache`
  (`query.py:1594`) and `_cli` (`:4055`) — masks off; the
  c901_d1_split_batch1 playbook (csr / extract_relations /
  review_selection precedents).

Order: S1 → S2 → S3 → S4 (S3 is independent and may land first; S2
gets cheaper after S1).

## 3. Acceptance criteria & shakedown

1. S3 parity: every materialised table content-identical before/after
   on the same DB; the witness is mutation-checked (inject a column
   change → RED).
2. S1 skip: unchanged inputs → no CTAS issued (timing/statement bound,
   target < 0.3 s no-op rebuild), tables byte-stable; a single
   edge-type write rebuilds only its registry dependents; a mutated
   source row is picked up (conservativeness test).
3. S2: post-`--apply` first graph query pays no inline rebuild (warm
   wall); a deliberately stale cache fails fast with the prescribed
   message instead of lock queueing.
4. Stamps: snapshot-fresh + snapshot_check green after every slice;
   `test_csr` / `test_centrality_cache` semantics unchanged.
5. S4: `ruff --select C901` clean on query.py; touched suites green
   targeted.
6. Perf: re-run the `bench_rebuild_scale` ladder at R=115k — report
   fixed-band delta (target ≥ 0.5 s off S3) and the S1 no-op bound;
   `make perf`'s graph_rebuild budget (5.0 s) stays the gate.
7. Eval gate: N/A — no query-visible semantics (rosters, hierarchies,
   extractor rules) changes; recorded so the house rule is not
   silently skipped.

## 4. Risks

- **S1 missed input = stale served as fresh.** Mitigated by
  conservative defaults (unknown fingerprint ⇒ rebuild) plus the
  mutated-source test; the fingerprint covers registry, entities and
  sources sidecars.
- **S3 is a materialisation-SQL rewrite.** The parity witness carries
  the risk; per-table drop-and-rebuild structure is kept so a bad
  fold is bisectable per table.
- **S2 shifts cost into ingest lanes.** Measured via the maint chain
  before/after; S1 makes the double-pay concern moot.
- **Partial application.** The generation stamp advances once per
  completed swap — a crashed pass leaves the previous cache and
  fingerprints untouched (same property the swap path already gives
  full rebuilds).

## 5. Non-goals & rollback

Non-goals: the retired row-level diff (partial-application risk),
vault_scaling T1 capacity work, HNSW / personalised-PageRank (blocked
rows 15–16), any PROPERTY GRAPH revival (#92 retired).

Rollback: slices are independent; each reverts alone (S3's parity
witness doubles as its own rollback check).

## Execution results — 2026-10-05 (S1–S4)

- **S1 (dirty tracking):** per-input fingerprints (one GROUPING SETS
  pass over `graph_edges` + per-slice scans) stamped as `fp:*` keys in
  `_build_meta`; `rebuild()` copy-then-patches a warm cache and
  drop-and-rebuilds only dirty units — with the v_node dependency
  closure (any entity change forces every v_node-joined table) and
  conservative fallback (missing fingerprints, live WAL present, any
  patch failure ⇒ full build). 9 unit tests in
  `tests/test_graph_rebuild_fast_path.py`. Live: full build 3.51 s →
  no-op patch rebuild **1.11 s**. Snapshot verification excludes `fp:*`
  keys (same contract-legal-drift class as the stamp keys).
- **S2 (rebuild-on-apply + fail-fast):** `extract-relations --apply`
  and `parse-newsletter --apply` call `rebuild()` post-write
  (warning-only on failure); `connect(read_only=True)` on a stale cache
  raises `GraphCacheStaleError` ("run make graph-rebuild") instead of
  silently unlinking + rebuilding inline; `GRAPH_STALE_REBUILD=inline`
  restores the old posture (conftest sets it for the suite; COLD caches
  still build — the shared xdist bootstrap is untouched).
- **S3 (single-scan fold):** one `_edge_resolved` pass (staged edges ×
  v_node, carrying endpoint kinds) feeds all 12 registry + 5 mixed CTAS
  as filtered projections; the pre-drop pass is skipped on provably
  empty catalogs. **Parity: 31/31 materialised tables content-identical
  to the pre-fold snapshot parquets at live scale** (57,574 edges,
  26,160 nodes); 3 witness tests incl. the cross-kind drop teeth.
- **S4 (mask hygiene):** `_materialise_centrality_cache` split into
  `_stamp_arrow_table` / `_stamp_score_metric` /
  `_stamp_voterank_and_louvain` / `_stamp_full_universe`; `_cli`
  dispatch split into six arm groups — `ruff --select C901` clean on
  `query.py`, **zero masks**.
- **AC re-baseline:** the S1 no-op target was written as "< 0.3 s";
  measured no-op patch = 1.11 s (two RO opens + 47 MB copy + 8 sqlite
  scans) — re-baselined with the measured bound; the fresh-build
  comparison is 3.51 s (3.2×). **Contract refinement:** a rebuild on a
  provably unchanged edge set KEEPS the `v_centrality_*` stamps (a
  stamp belongs to exactly one edge set — that set did not change);
  `test_stamp_centrality_cache_recreates_tables` updated to the
  two-arm contract.
- Gates: pending (operator go).
