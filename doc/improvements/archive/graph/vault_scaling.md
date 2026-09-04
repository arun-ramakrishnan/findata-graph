---
title: "Vault scaling to 100M rows — corpus, graph substrate + query, similarity"
status: executed
filed: "2026-09-04"
executed: "2026-09-04"
completed_md: "204"
area: "helpers/core/corpus, helpers/graph/query + rebuild, Mojo/src/bench (new bfs_csr.mojo), helpers/core/embed_matrix"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Vault scaling to 100M rows

**Date:** 2026-09-04 · **Status:** PROPOSED · **Mode:** plan now, execute
gated (operator decision 2026-09-04) · **Forecast:** 100M doubled rows is
a REAL growth target, not a stress ceiling.
**Area:** corpus layer · graph substrate + query + rebuild · similarity.
Folded from: `doc/local/perf_investigations.txt` §1/§5/§8 +
former `doc/local/graph_scaling.md` (deleted on fold 2026-09-04 —
those sections now point here).

## 1. Growth model

At today's density (27.7 doubled rows/note: 34,392 rows / 1243 notes),
100M rows ≈ 3.6M notes. Whether growth arrives as note volume, richer
edges/note, or new sources decides which phase bites first — record the
actual driver here when the first trigger fires. Until then all currency
is **rows, not notes** (ladder lesson: paired "5k notes / 10⁶ edges"
estimates diverge with density).

**Target decision (operator, 2026-09-04): 100M doubled ROWS — not 100M
nodes.** 100M nodes was examined and rejected: at today's degree (22.4
directed rows/node) it means ~2.24B doubled rows, which breaks the
frozen int32 CSR payload by 4.5% (2.147B cap), implies a ~112 GB DuckDB
e_all_und, ~9 GB CSR neighbors, ~18-min materialize — none of it on
this 14 GB box. At the 100M-row target int32 has 21× headroom (M = 100M
vs 2³¹). "Beyond" ceiling on this box: the mmap CSR query path stays
comfortable past ~200M rows (~840 MB); the DuckDB side (table ~50 B/row,
5 s rebuild budget) binds long before that — the recorded growth path
past 100M rows is shard-by-node-range (see B-C), never a silent int
widen.

## 2. Trigger ladder (replaces the single 100ms line)

| tier | rows (e_all_und doubled) | fires | serves queries |
|---|---|---|---|
| T0 now | 34K | — | DuckDB BFS (8–44 ms, green) |
| T1 | ~1M (full BFS > 100 ms) | build CSR (Phase B-A) | DuckDB + ART-index bridge and/or tiered-budget waiver |
| T2 | ~10M (rebuild > 5 s) | rebuild-at-scale + Mojo BFS promotion on measured >2× crossover | CSR + Mojo BFS (parity-gated) |
| T3 | 100M | full validation legs mandatory pre-promotion | CSR + Mojo BFS, direction-optimized |

Phase 0/1/2 split (operator decision 2026-09-04, build-early aim):
Phase 0 = build substrate while DuckDB serves; Phase 1 = kernel +
parity, promote on measured crossover (don't assume 10M — promote as
soon as CSR beats DuckDB >2× sustained); Phase 2 = 100M-leg validation
before promotion is complete. The 1M–10M band is never silently over
budget: ART bridge and/or waiver is recorded, not assumed.

## 3. Evidence (folded — single copy)

Ladder (/tmp/scale_bfs.py — script + scratch DBs since deleted by /tmp
cleanup; rewrite in-repo at execution time under Mojo/bench/ or tests/):

| doubled rows | materialize | 1 expand* | full 5-level BFS |
|---|---|---|---|
| 34K (today) | — | — | 8–44 ms |
| 1M | 0.5 s | 15 ms | 144 ms |
| 10M | 5.0 s | 9 ms | 321 ms |
| 100M | 48.5 s | 4 ms | 1750 ms |

\*point lookup (single-node frontier, zone-map-pruned — hence
non-monotonic); NOT per-level cost. Per-level at 100M is 1750/5 =
350 ms over ~1.6 GB id columns ≈ 4.6 GB/s effective — join +
temp-write dominated. Sublinear to 10M, linear after. ART index at
100M: 85 s build, 1750→955 ms (1.8×) — engine tweaks are <2× class.
Rebuild breaks first (~10M vs 5 s budget).

Statement timing (34K — driver innocent): 16 statements/query, none
dominant (expand ~9 ms; reconstruct one SELECT/hop ~13 ms at depth 5;
1–3 ms DDL churn/level). Round-trips are µs — fusing saves nothing.

Rejected (stay rejected): recursive-CTE rewrite (regresses B2 —
enumerated all simple paths, multi-second at hops=5; no early exit, no
clean visited state, same scans); bidirectional BFS as primary (<2×
class — add-on only); Mojo-over-DuckDB bridge (parity lesson,
mojo_pilot.md:467-480); liteparse promotion / PDF re-review (closed);
Mojo-over-bridge for any DB-marshaling path.

TaskGroup 2.7–3.0× planning band (2.89× fanout probe, 2.69–3.01×
MAX-parallel; 4.00× seen once — not a planning number). Per-cpu merge
pattern for frontier claims. 64B-aligned loads (vmovaps lesson).

## 4. Phase A — corpus layer (absorbs old S1b.4–6)

- A.1 Corpus frontmatter-only load + lazy Iterator (body ≈ 10×
  frontmatter; list fine at 2.5 MB, not 250 MB at 10k notes).
- A.2 `corpus_cache.generation` counter — one shared staleness query;
  dovetails with the existing `_build_meta.generation` anchor
  (verified 65041, schema_version 13 — reuse, don't invent).
- A.3 Advisory→gating flip: FAIL when >2000 notes or >5 walkers rglob
  without Corpus (5× re-parse 1.24 s today, ~12 s at 10k).
- No new deps, all Python. **Trigger = corpus metrics, NOT row tiers**
  (fixed 2026-09-04): fire at notes ≥ ~10k (S1b.4's 250 MB point ≈
  277K doubled rows — under uniform growth this arrives BEFORE T1;
  under density-driven growth T1 can fire with the corpus still 2.5 MB
  and Phase A unneeded). Row-tier wiring would fire it ~4× late on the
  note axis or pointlessly early on the edge axis.

## 5. Phase B — graph substrate + query + rebuild

**B-A CSR substrate (offline build + mmap query path).** Layout frozen:
`csr_offsets.bin` (N+1 × int32 LE), `csr_neighbors.bin` (M × int32 LE,
slices sorted), `csr_idmap` (reuse v_node ids or frozen remap + stamp);
both directions stored; int32 while N < 2³¹ (v2 for int64, never silent).
100M rows ≈ 420 MB (400 MB neighbors + ~18 MB offsets at degree ~22.4)
vs ~4–6 GB DuckDB rows (~50 B/row, 5 cols). Build deterministic
(byte-identical rebuilds + manifest: rows, N, M, checksum, generation,
schema_version). Query path mmaps read-only; manifest generation vs
`_build_meta.generation` mismatch → DuckDB fallback (never stale
silently). Filters (edge_label/as_of) stay on DuckDB — CSR is
structure-only until a filtered workload proves hot. Gates:
byte-identical rebuilds, slice spot-checks in CI, fallback test, build
time/bytes recorded. Spill plan required (fill peaks ~2× table,
~8–12 GB at 100M). No incremental updates while full build < 5 min.
Fires at T1.

**B-B Mojo BFS over CSR.** Binary `Mojo/src/bench/bfs_csr.mojo`
(files in, paths out — no bridge): mmaps 64B-aligned, level loop,
exit 0/2/1 (path / unreachable / error). TaskGroup frontier sharding;
per-thread next-frontiers merged at barrier; parents[x] = MIN over
same-level claimants at merge (post-merge min-reduce; min-over-frontier
in bottom-up) — reproduces oracle MIN(a_id) exactly (first-claim order
does NOT and is retracted). Direction optimization (Beamer) from day
one (default bottom-up past N/20 frontier density). Thin wrapper,
contract-identical to `_shortest_path_bfs`; filtered queries never
reach it; missing binary → DuckDB fallback (B-acceptance includes its
first non-Python runtime dep on a query path: Makefile.mojo wiring +
fallback are part of the work). Parity gate (#197 pattern,
MOJO_BFS_PARITY=1, mismatch = loud failure). Promotion: measured >2×
crossover + budgets updated + contract tests (unreachable, src==dst,
unknown, hops=0/<0, filtered delegation). No toolchain in make perf.
Non-goals: batched/multi-source BFS, weighted paths, GPU BFS (pilot
crossover stands), Onager metrics, CSR for other traversals (named
future caller, not scope). Fires at T1, promotes per ladder (Phase 1/2).

**B-C Rebuild-at-scale (the orphan that breaks first).** Materialize
48.5 s at 100M vs 5 s budget → breaks ~10M, before queries do.
Skeleton (decided 2026-09-04, detailed at execution): hash-range
partitioned materialize — shard e_all_und by hash(a_id) into K ranges
sized so per-shard build holds the 5 s budget (K ≈ rows/10M); shards
build independently (parallelizable across maint runs, spill-bounded
memory); compaction = sorted-merge per shard on the graph_rebuild
cadence; the CSR build consumes shards without a global sort. The same
sharding is the recorded growth path past 100M rows and the fallback if
a v2/int64 format is ever needed. Gated with T2; owner assigned at
execution.

## 6. Phase C — similarity at scale (absorbs old §1 + §5 rows)

Engine by batch size (MAX-eval implication): single queries stay numpy
(`embed_matrix.top_k`); batch workloads route n≈8–16 → one MAX GEMM
(2–2.5× over numpy), n≥64 → numpy sgemm; batching itself is 9–11×
per-query. `FlatKNN`/embed_matrix already 64B-aligned — MAX route needs
only session/graph caching. Caveats travel along: num_threads crash
(leave alone), shape-keyed JIT (~30 s cold), DRAM-bound ±20% bands.
ON HOLD to T2/T3: f16/int8 quantization, usearch/HNSW past ~1M vectors,
parallel-accumulator on row_cosine (needs counter-unlock proof first —
perf_investigations §4; paranoid permanence landed 2026-09-04:
/etc/sysctl.d/99-perf-event-paranoid.conf = 1, counters verified
live),
get_tickers `_best_vss_match` (folds into batched work, not standalone).
numba CLOSED-superseded (3.14 support landed AND `_flat_knn_map` wedged
numpy between vec0 and the py loop). **Trigger = matrix size, NOT row
tiers** (fixed 2026-09-04): fire when the embedding matrix reaches tens
of MB (the folded §1 gate) — T2 can arrive density-driven with matrices
still 1.9 MB and Phase C unneeded, and vice versa.

## 7. Cross-cutting

- 14GB-box memory budget per phase (B-A fill is the peak — spill plan
  mandatory, not aspirational).
- Synthetic fixtures live in-repo at execution time (Mojo/bench/ or
  tests/ — /tmp got wiped once; ladder unreproducible until rewritten).
- CI legs per tier; budgets tiered per §2 ladder, never a single line.
- Fallback doctrine shared: stale/missing artifact → DuckDB/Python
  path, loudly logged, never raised, never silent.
- Staleness anchors reused (`_build_meta.generation`), not reinvented.
- Onager metrics cliff (named, unowned 2026-09-04): pagerank/betweenness/
  louvain project `graph_edges` in-memory — ~2.4 GB projection at T3
  (100M rows; borderline on 14 GB), dead well before any 100M-node
  regime. Decision deferred to T2: feed Onager from CSR shards, or drop
  metrics to sampled/tiered cadence. Not this proposal's build scope.
- Maint/snapshot loop at scale (named, unowned 2026-09-04): db_maint
  VACUUM + snapshot gzip/parquet over research.db at 3.6M notes
  (~5–6 GB embedding JSON) — cycle times explode, and snapshot publish
  is operator-critical flow. Owner + design needed before T2-class note
  counts.
- Tier tripwire (Phase 0 ride-along): advisory check in the
  corpus-advisory pattern watching e_all_und rows / note count / matrix
  MB against the tier thresholds, so §1's "record the actual driver" is
  a measurement, not a discipline (advisory, never rc 1).

## 8. Non-goals (closed — see perf_investigations.txt DONE/CLOSED log)

Recursive CTE · bidir-as-primary · Mojo-over-bridge · liteparse/PDF
re-review · chonkie/stringzilla/polars-class deps · iGPU device code
below crossover · numba · semantica track (separate evaluation:
doc/local/semantica_evaluation.txt).
