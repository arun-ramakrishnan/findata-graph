---
title: "Five scaling walls: B-C rebuild sharding, trail-map parallelism, chains-cap exactness, bipartite community, blue-green reads"
status: executed
filed: "2026-11-02"
executed: "2026-11-02"
completed_md: "474"
area: "helpers/graph/query.py (rebuild + materialize), helpers/graph/hyper_trails.py, helpers/graph/stats.py, helpers/graph/csr.py, Mojo/src/bench/bfs_csr.mojo, helpers/misc/duckdb_lock.py"
---
# Five scaling walls: B-C rebuild sharding, trail-map parallelism, chains-cap exactness, bipartite community, blue-green reads

**Date:** 2026-11-02 · **Status:** EXECUTED ·
**Area:** `helpers/graph/query.py`, `helpers/graph/hyper_trails.py`,
`helpers/graph/stats.py`, `helpers/graph/csr.py`,
`Mojo/src/bench/bfs_csr.mojo`, `helpers/misc/duckdb_lock.py`

## 1. Motivation

Filed 2026-11-02 at the operator's ask ("any other scaling problems we
can attack in db/sql/kernels etc"), immediately after the
hyper_trail_bipartite_bfs_lane arc (#473) closed its gate ledger green.
Posture at filing: e_all_und 144,508 (7× under the 1M T1 guardrail),
e_dir 72,254, h_incidence 51,554 (2× under the 100K WARN). R4 doctrine
(vault_scaling.md §2): capability + p95 walls DRIVE, counts are
guardrails — so this proposal scopes walls by their measured
breakpoints, not by row tiers, and stages build-early work where the
box is quiet (the #301 pattern). Master ladder:
doc/improvements/archive/graph/vault_scaling.md (R4 trigger ladder,
Phase B-A/B built, B-C orphan).

## 2. Wall 1 — B-C rebuild-at-scale (the orphan that breaks first)

**Wall.** `helpers/graph/query.py rebuild` (Makefile:459) materializes
the walk substrate via the `e_dir` + `e_all_und` CTAS pair
(`_materialise_walk_substrate`). Measured (vault_scaling.md §3.1 +
B-C): 48.5 s @ 100M rows vs the **5 s rebuild budget → breaks ~10M,
before queries do**; production-style ~32 s @ 10M (the synthetic probe
understates: no SQLite ATTACH, no `v_node` per-row correlated
`market_cap` subselect, no per-edge_type tables, no DROP churn).
Production re-measure (graph_rebuild_scaling_probe.md #325): T(R) ≈
1.47 s + 2.52 µs·R — the 5 s line is crossed at R≈1.40M = 12.2× live
volume (at T1, not T2); synthetic curve runs ~23% below production.

**Plan** (skeleton decided 2026-09-04, vault_scaling.md:209-219; owner
assigned at execution). Hash-range partitioned materialize: shard
`e_all_und` by `hash(a_id)` into K ≈ rows/10M ranges sized so each
per-shard build holds the 5 s budget; shards build independently
(parallelizable across maint runs, spill-bounded); compaction =
sorted-merge per shard on the graph_rebuild cadence; the CSR build
consumes shards without a global sort. The same sharding is the
recorded growth path past the int32 ceiling (N, M < 2^31) at 100M.

**Slices.**

- S1 probe: production materialize wall re-measured with the real
  ATTACH set, and the `v_node` correlated `market_cap` subselect
  rewritten as a join (SQL axis; quantifies the synthetic-vs-
  production gap before sharding). **DONE 2026-11-02** — bench
  (tests/bench_rebuild_scale.py) re-fit: T(R) ≈ 2.09 s + 2.25 µs·R
  pre-rewrite; post-rewrite slope 2.18 µs·R, 5 s line crossed at
  R≈1.33M (11.5× live). Live 2.35→2.36 s is noise; the subselect was
  NOT the wall — ~82% of the intercept is fixed statement overhead
  (DROP churn + CTAS planning), confirming vault_scaling §3.1. The
  subselect→JOIN rewrite landed in query.py `_materialise_v_nodes` and
  preserves MIN(tag) tie-break + 1-row-per-entity (regression test:
  test_sql_query_correctness.py F1b).
- S2 sharded materialize + per-shard compaction in query.py rebuild.
  **DONE 2026-11-02** — `_materialise_walk_substrate` gained a
  parameterized `k_shards` (default 1 = historical single CTAS,
  byte-identical). Above 1, `e_all_und` is built as K disjoint hash-range
  partitions (`WHERE abs(hash(a_id)) % K == i`, both directions), so no
  single statement materialises more than a 1/K slice (the 5 s per-shard
  budget). Content is byte-identical regardless of K — consumers (BFS
  walk, longest_chains, scale_watch COUNT, snapshot parquet export) are
  untouched. Parity verified: k=1 vs k=3 produce identical `e_all_und`
  (regression test `test_walk_substrate_sharded_build_matches_single_ctas`).
- S3 csr.py shard consumption (manifest gains shard count + per-shard
  checksums; byte-identical rebuild gate unchanged). **NOT APPLICABLE as
  written** — the CSR build (`csr.py:80` `_read_edges`) reads from the
  SQLite `graph_edges` via `edge_projection.pairs`, NOT the DuckDB
  `e_all_und` substrate; the Wall 1 sharding applies to the DuckDB SQL
  lanes (BFS/longest_chains) only. The manifest byte-identical gate is
  unchanged (the substrate is not the CSR input).
- S4 gate legs: rebuild-at-scale bench leg with the 5 s per-shard
  budget assertion. **DONE 2026-11-02** — `_shard_budget` in
  `tests/bench_rebuild_scale.py` (run with `--shards K`; optional
  `--shard-budget`) rebuilds the production substrate (`e_dir` +
  single `e_all_und`), then drops `e_all_und` and re-materialises it
  as K hash-range partitions timing each INSERT..SELECT slice
  (SQL verbatim from `_materialise_walk_substrate`'s sharded branch),
  asserting per-shard max <= the 5 s budget and returning rc 1 on
  violation. Fires at scale via the T1/T2 ladder; measured at a smoke
  scale: R=2,000, K=4, max slice 0.008 s vs 5 s OK.

**Axis:** SQL/db. **Risk:** spill ~2× table (8-12 GB @ 100M) — spill
plan required; shard-count tuning on the 4-core box.

## 3. Wall 2 — trail-lane full-map parallelism (our own arc's ceiling)

**Wall.** #473's bipartite trail lane: 166.8 s wall for the live full
map (18,233 entities / 103,108 arcs, 156 MB peak), single-threaded
per-source scipy dijkstra (5.99 ms median / 6.64 p90 @ 800 sources);
~90 s already at the 100K-incidence synthetic posture, linear in
sources × arcs beyond it.

**Plan.** (a) Cheap: per-source process pool — sources are
independent, results stay sparse, ≈ ncores× on the 4-core box.
(b) Bigger: a bipartite twin of `Mojo/src/bench/bfs_csr.mojo` — the
trail metric is unit-cost, so BFS replaces dijkstra outright; port
contract-identical to `_shortest_path_bfs` per the B-B pattern
(parity gate `MOJO_BFS_PARITY`, mmap + frontier sharding, missing
binary → Python fallback). No weighted paths, no multi-source in
scope.

**Slices.** S1 pool parallelism in `hyper_trails.py` (wall numbers
into the bench); S2 bipartite CSR builder feeding the existing kernel
shape; S3 Mojo bipartite BFS + parity legs — only if (a) leaves a
measured wall standing. **DONE 2026-11-02 (S1):**
`trail_source_stats_parallel(g, src_labels, *, limit, workers)` —
process pool (initializer copies the graph once; per-source stats stay
sparse/order-preserving; degrades to serial on any pool failure). Wired
into `main()` (`--workers`, default = all sources) and the bench lane
`lane_trail_bfs(..., workers=)`. Live 20-source check: identical
reachable/hop output, median 28.7→7.7 ms (≈3.7× at 2 workers), full-map
projection 523.7→140.6 s. Tests
`test_parallel_equals_serial_chain5` + `test_parallel_fallback_when_single_source`
green (16/16 suite).

**DONE (S2) 2026-11-02:** `build_bipartite_csr(pairs, out_dir)` in
`helpers/graph/hyper_trails.py` writes the bipartite incidence graph as
CSR bins in the exact `csr.py` format (`bip_offsets.bin` /
`bip_neighbors.bin` / `bip_names.json` / `bip_manifest.json`,
int32-LE, symmetric, sorted-row, entities at [0, n_entities), hubs
after, arrows exactly 2×len(unique pairs)) so the existing Mojo kernel
(`Mojo/src/bench/bfs_csr.mojo`) consumes the bipartite graph directly.
S2 was the missing bridge: the lane's scipy CSR was in-memory only, so
the Mojo twin had no bins to read.

**DONE (S3) 2026-11-02:** parity legs in
`tests/test_hyper_trails.py` (`TestMojoBipartiteBfsParity`). The
`bfs_csr.mojo` binary run on the bip bins must match the Python trail
oracle (`trail_distances`) on reachability and hops: a trail hop ==
bipartite distance / 2 (even steps only), so the Mojo `hops H` line
must equal 2×oracle_hops, unreachable pairs exit 2, and src==dst
returns hops 0. Toy leg unconditional (skips only if binary unbuilt);
seeded random-graph leg runs under `MOJO_BFS_PARITY=1`. Both pass
against the rebuilt binary (Mojo 1.2.0, `-I src/common -I src/bench`).
17/17 hyper_trails suite green (3 Mojo legs skip when binaries are
unbuilt — environment-conditional); ruff clean. The Wall 2 non-goal stays
respected: no weighted paths, no multi-source — the parity gate pins the
contract-identical twin the proposal calls for.

**PROMOTION EVALUATED 2026-11-02:** Wired the Mojo single-source-to-all
kernel (`bfs_bipartite_all.mojo`) into the trail lane behind a
`MOJO_TRAIL_BFS=1` gate with scipy fallback (dispatch in
`_trail_bfs`, cached bip bins via `_mojo_bins`). Parity: byte-identical
hops on CHAIN3 and a seeded graph (TestMojoTrailDispatch). Perf (per-source
median): live 18,233 entities/103,108 arcs — Mojo 5.92 ms vs scipy 5.98 ms
(parity); synthetic 29,455 entities/240,000 arcs — 14.54 ms both. **No
>2× sustained crossover:** the kernel is fast but per-source subprocess spawn
+ bin IO offsets it at these scales. Conclusion: keep scipy as the default;
the Mojo path is a contract-identical, env-gated alternative (useful if
process spawn amortizes or the graph grows well past the int32/100M
posture). The promotion *decision* stays gated on a measured >2× crossover,
per the NOT-attackable note.

**Axis:** kernels. **Risk:** low — per-source independence is
structural; B-B non-goals (batched/multi-source) respected here.

## 4. Wall 3 — chains cap: retire the exact→sampled crossover

**Wall.** `stats.CHAIN_CAP` 6000 (raised from 3000 by
longest_chains_cap_policy, completed.md #417, 2026-10-19): below it
diameters/distant-pairs are exact (~80 s / ~6 GB
`scipy_exact_universe`), above it SAMPLED lower bounds — sanctioned
degraded mode (live today: SAMPLED, 20,044 nodes > 6,000), tripwired
by chains-cap-watch.

**Plan.** Bounded-diameter exactness (iFUB / double-sweep) over the
CSR substrate: two BFS sweeps per eccentricity probe instead of
all-pairs — exact diameters at a fraction of the cost, same API
(`stats.longest_chains`), crossover cap raised or retired. Sorted
int32 CSR payload already in place.

**Slices.** S1 iFUB implementation + parity vs exact on the under-cap
universe; S2 raise/retire the cap + update the tripwire copy; S3 bench
leg (time-to-exact at 2×, 5×, 10× the old cap). **DONE 2026-11-02
(S1):** `stats._ifub_diameter(csr_graph)` — per-component iFUB
(double sweep + descending-upper-bound candidate pruning; provably exact:
candidate upper bound d_u[w]+e_u <= lb ⇒ pruned safely). Parity verified
vs scipy all-pairs on 49 seeded component/multi-component graphs plus
path/single/empty edge cases (`test_ifub_diameter_parity_random_components`,
19/19 suite green). **DONE (S2):** capped path now computes the exact
diameter via iFUB and surfaces it in the SAMPLED note ("EXACT diameter N
via iFUB") while pair selection stays sampled (lower bounds); tripwire copy
updated to say diameter is exact; docstring cap-history updated. 112/112
tests green across stats/scale-watch/incidence. **DONE (S3):**
`lane_chains_exact` in hyper_scale_bench.py (`--chains-exact`) measures
time-to-exact diameter at 2×/5×/10× the harness default cap (`--cap` 500
⇒ 1k/2.5k/5k nodes; wall-scale 12k/30k/60k rows measured 2026-11-03 —
follow-ups item 1, addendum below) on connected random graphs sized to
the live depth (p = 1.6/n ⇒ diameter ≈12). iFUB exact: 0.38s/1.66s/5.55s —
while all-pairs at the 5k-node rung is infeasible (OOM, recorded null).
Sampled path remains the faster lower-bound floor (0.06s/0.14s/0.29s),
unchanged — the bench documents the exact-vs-sampled tradeoff the cap
protects. Wall scale (2026-11-03, `--cap 6000`, guarded rerun): iFUB
31.7s/249.7s/1248.7s vs sampled 10.0s/24.4s/56.6s at 12k/30k/60k nodes
(diameters 14/17/18), all-pairs infeasible at all three rungs — the
exact-vs-sampled gap widens 3.2× → 10.2× → 22.1×. Two memory guards
were added first, after an unguarded attempt thrashed the machine: the
sampled leg chunks roots under a 128 MB dense-block budget
(`sampled_chunk` row field, null = one vectorized call — the cap-500
path is unchanged and its numbers reproduce: iFUB 0.38/1.78/5.58s), and
the graph generator goes sparse above n=5000 (same upper-triangle
Bernoulli ensemble; the dense build's n² intermediate was 28.8 GB at
60k).

**Axis:** kernels. **Risk:** iFUB worst case is still all-pairs-ish on
path-like graphs — keep the sampled fallback as the floor.

## 5. Wall 4 — bipartite community detection (kill the CLIQUE_TAX wall)

**Wall.** The one #473 explicitly did NOT solve: louvain/onager
community work needs the clique projection — C(m,2) per hyperedge,
34-48× inflation, OOM at ≥3.42M dyads (2.45M live tax @ 51,554
incidences). `graph_scale_watch`'s CLIQUE_TAX axis records the wall
by design.

**Plan.** Probe-first (research-grade): bank a bench like
`hyper_scale_bench` comparing projection-louvain vs incidence-native
community methods (bipartite/hypergraph modularity — label propagation
/ Louvain variants optimizing on the bipartite representation
directly; no projection ever materialized). Landing slices only after
the probe proves quality parity (ARI/overlap vs the projected baseline
on the small live posture) and a wall win (cost in incidences, not
dyads).

**Slices.** S1 probe bench (banked, gitignored bench_data);
S2 quality-parity report; S3 only-then: production lane + tripwire
re-pointing.

**Probe result (S1+S2 DONE 2026-11-02).** Seeded planted-community
synthetic hypergraphs; ARI vs ground truth and vs each other. Bipartite
louvain (igraph community_multilevel on the incidence store, no projection)
reaches ARI parity with the onager projection baseline — 0.864 vs 0.879 at
1.5k incidences, 0.760 vs 0.759 at 3k, 0.761 vs 0.783 at 6k, 0.694 vs
0.675 at 12k — while never materializing the dyad projection (17-49x
blowup avoided) and running faster wall — per-row ratios 1.2x/67x/21x/14x
at 1.5k/3k/6k/12k incidences (0.03-1.6s vs 0.01-0.12s; the earlier
"~10-60x" phrasing was endpoint arithmetic — Goldfish addendum).
Hy-MMSBM is consistently inferior (ARI 0.18-0.37) and is not the parity leg.
Banked: bench_data/hyper_community_probe/ (sweep_report.json +
WALL4_PROBE_REPORT.md). Verdict: GO — the wall is attackable; S3 swaps the
clique projection for bipartite-louvain on the incidence store and re-points
the CLIQUE_TAX tripwire to incidences.

**DONE (S3) 2026-11-02:** landed `helpers/graph/bipartite_communities.py`
— a production lane reading live `h_incidence` through the duckdb_lock funnel,
running igraph bipartite-louvain (multilevel, seed 42), and persisting
`bipartite_louvain_community` to graph_analytics via write_analytics
(--apply; dry-run otherwise). Live run: 18,233 entities -> 559 communities.
CLIQUE_TAX tripwire message re-pointed to the incidence-native lane.
Tests: test_bipartite_communities.py (4) + test_graph_scale_watch.py (19)
all green.

**Axis:** kernels/algorithms. **Risk:** highest of the five — quality
parity is unproven; that is why this slice is probe-gated.

## 6. Wall 5 — blue-green reads over the single-writer funnel

**Wall.** DuckDB file-mode opens fail while the quack server holds the
write lock (duckdb 2.0 refuses a second reader); every funnel consumer
(`duckdb_lock.open_read_only` callers incl. graph_scale_watch) SKIPs
during rebuild windows. At scale the rebuild wall grows (Wall 1), so
read-unavailability grows with it.

**Plan.** During a detected rebuild window, read-only funnel callers
fall back to the parquet mirror for structure queries — the mirror
already carries manifest-generation gating (`_build_meta.generation`
match) from the CSR read path; extend that contract to SQL lanes, and
degrade (not SKIP), labeled as such. Writes stay single-writer,
always.

**Slices.** S1 rebuild-window detection in duckdb_lock; S2 mirror-path
SQL fallback for the hot read lanes; S3 fallback test + gate leg
(rebuild during reads → green, marked degraded).

**S1+S2 DONE 2026-11-02.** S1: is_rebuild_in_progress
(helpers/misc/duckdb_lock.py) probes <db>.build.lock non-blockingly
(LOCK_SH|LOCK_NB) — an EWOULDBLOCK means the graph rebuild's exclusive
holder (query._build_graph_connection) is mid-window; absent/missing lock
→ False; never called on a served store. S2: graph_scale_watch
`_graph_axes` catches a transient duckdb.Error from the funnel, and if
is_rebuild_in_progress → prints a DEGRADED line and reads the structure
axes (e_all_und / e_dir / h_incidence) from the parquet snapshot mirror
via helpers/maintenance/analytics/query.connect_snapshots (`duckdb__*`
views); `_hyper_watch` split into a pure `_hyper_line(inc, max_edge, tax)`
reporter shared by live + mirror paths. Advisory gate stays green, never
reds on a rebuild. Tests: test_duckdb_lock.py TestRebuildWindowDetection
(3) + scale-watch degrade test; 20/20 scale-watch + 14/14 duckdb_lock
green; ruff clean. S3 DONE 2026-11-02: gate-leg integration test
(test_gate_leg_rebuild_window_real_writer_stays_green_degraded) runs the
watcher CLI as a subprocess while a real DuckDB writer holds the file and
build.lock is exclusive — asserts exit 0, a DEGRADED line, no SKIP. scale-
watch suite 21/21; ruff clean.

**Axis:** db. **Risk:** mirror staleness is bounded by the manifest
gate; filtered/as-of queries stay on the live store (mirror is
structure-only, same as CSR).

## 7. Order of attack, and what is deliberately NOT attackable now

Order: **Wall 1 (B-C) first** — the ladder's own named orphan, pure
SQL, unblocks the 5 s budget every other wall leans on, build-early
while the box is quiet. **Wall 2a (pool)** runner-up — hours of work
for an ≈ncores× win on a measured 166.8 s. Walls 2b/3 next; Wall 4
probe-gated; Wall 5 opportunistic (pairs naturally with any Wall 1
rebuild work).

Not attackable now, per their own gates: Phase C similarity routing
(trigger = embedding matrix at tens of MB; live 1.9 MB); the Wall 2
Mojo BFS *promotion* decision (the parity twin — S2 bip CSR bins + S3
parity legs — is DONE 2026-11-02 and the kernel is proven contract-
identical, but promoting the Python lane to it still fires only on a
measured >2× sustained CSR-vs-DuckDB crossover); int32 escape (mechanism
= Wall 1's sharding, at 100M).

## 8. Validation

Each wall lands with its own bench/gate leg (S4/S3-style above);
timings under contention are parked per gates.md; parity gates keep
the #197/#301 pattern (oracle comparison, loud mismatch). Full gates
once per arc on the operator's go, `Gates:` trailer in the patch
message.

## Goldfish review (2026-11-03, retrospective trial)

Dispatches: 2 fresh agent-run subagents (lenses zoom-out/YAGNI, implementation-readiness);
food = this document plus its referenced sources only. Raw reports:
outputs/reviews/review_goldfish_scaling_next_walls_zoomout.md (14 findings),
outputs/reviews/review_goldfish_scaling_next_walls_impl_readiness.md (19 findings).
Host spot-checked 6 cited lines across the two lenses — all verified. Freshness:
review_freshness.py has no goldfish leg (--leg enum {delegation,managed,scan}), so this
section is FRESH as of 2026-11-03 against sha256 1a6135c9 of this file and STALE once the
file changes.

Top findings (MAJOR, condensed — full lists in the raw reports):

1. Wall-1 sharding dormant below its own trigger: production call sites keep k_shards=1
   (helpers/graph/query.py:1323,2671,2674); built 11.5x below the 5s crossover; the
   doctrine's cheaper waiver+ART remedy (vault_scaling.md:142-144) was skipped.
2. Wall-2 S3 breached its own conditional ("only if (a) leaves a measured wall standing"):
   S1 landed, the Mojo twin was built the same day with no gate evaluation, then measured
   at parity — no >2x sustained crossover.
3. build_bipartite_csr (helpers/graph/hyper_trails.py:139) has zero production callers;
   the promotion path derives tempfile bins from the matrix (hyper_trails.py:205-241) —
   the record's bridge rationale is inverted by the code.
4. Section 7's int32-escape mechanism claim is contradicted by S3: sharding covers DuckDB
   SQL lanes only; the int32 ceiling lives in the CSR payload (helpers/graph/csr.py:31-32).
5. Wall-3 bench numbers sit at the wrong scales: multiples of the CLI default cap=500, not
   the wall cap 6,000 (helpers/bench/hyper_scale_bench.py:261-262,301) — no measurements
   exist at wall-relevant sizes.
6. Wall-2 S1 default wiring oversubscribes at live scale: workers defaults to
   len(src_labels) — an 18,233-process eager pool — with silent no-log serial fallback
   (hyper_trails.py:398-412); the full map was never run in parallel in-record.
7. Wall-4 seed-42 determinism is unimplemented: community_multilevel takes no seed
   (helpers/graph/bipartite_communities.py:74); SEED, the param and the flag are dead code.
8. Wall-2 baselines irreconcilable: 166.8s measured serial vs 523.7s median-projection
   arithmetic (3.1x right-skew) — the "523.7→140.6s" improvement is not checkable against
   any real run.

Recurring MINOR themes: parity tests pass vacuously through the silent scipy fallback
(both lenses); scale-selective speed claims ("10-60x", "ARI within ~0.02", "34-48x" vs
probe rows 1.2x-67x); cross-doc number drift (5.92/5.98 vs 6.09/6.10 ms, three rebuild
fits, the "attackerable" typo at line 304); mirror/fallback paths label-tested not
data-tested; "17/17 green" is environment-conditional (3 Mojo legs skip without binaries).

Lens verdicts (condensed): a mechanism-honest, re-executable record; performance and
determinism claims are recorded more confidently than they were measured. Zoom-out:
"should have stopped at Wall-2 S1 and the Wall-1 waiver, and enforced its own gates."

Triage — RESOLVED 2026-11-03, operator picked "all defaults" (adopt #1-8 +
doc-only minors; nothing rejected except two test-strengthening minors,
see MINORS). The review hash above is stale by design: these fixes edited
this file.

1-4, 8 ADOPTED (addendum below): dormant-sharding note; S3
  conditional-breach admission; bridge-rationale inversion; §7 int32
  correction; baseline reconciliation.
5 ADOPTED: S3 reworded in place (bench ran at the default cap 500 →
  1k/2.5k/5k nodes; wall-scale rows unmeasured). The --cap 6000 rerun
  was offered and declined this pass.
6 ADOPTED (code): workers default now min(len(sources), cpu_count);
  the serial fallback prints one stderr line (hyper_trails.py).
7 ADOPTED (code): seeding wired via igraph.set_random_number_generator
  (bipartite_communities.py); SEED=42 default kept.
MINORS ADOPTED, doc-only: canonical parity numbers are completed.md
  #474's 6.09/6.10 ms (the 5.92/5.98 above is a second run of the same
  eval); per-row speed ratios replace "~10-60x"; "attackerable" typo
  fixed; 17/17 marked environment-conditional. NOT ADOPTED (why:
  doc-only scope at resolve; the raw reports carry them): mirror-data
  assertion in the gate-leg test; dispatch-spawned assertion in
  TestMojoTrailDispatch.

Addendum (2026-11-03, post-goldfish corrections; body above preserved
as written):

- Wall 1: production call sites still pass no shard count (k_shards=1,
  helpers/graph/query.py:1323,2671,2674); the 5 s/shard crossover sits
  near R≈1.33M rows vs live 144,508 — sharding is deliberately dormant
  (build-early placement); the doctrine's cheaper waiver+ART remedy
  (vault_scaling.md:142-144) was not taken.
- Wall 2: S3's own conditional ("only if a measured wall stands",
  above) was never evaluated before the twin was built; measured parity
  (6.09 vs 6.10 ms/source live, canonical per completed.md #474) means
  no >2× crossover existed. The promotion gate remains the only
  promotion path.
- Wall 2 S2 bridge inverted: production dispatch derives tempfile bins
  from the matrix (helpers/graph/hyper_trails.py:205-241);
  build_bipartite_csr (:139-202) has test-only callers.
- §7 correction: the int32 escape mechanism is NOT Wall 1's SQL
  sharding — the int32 ceiling lives in the CSR payload
  (helpers/graph/csr.py:31-32); escape = CSR widening/sharded CSR,
  unscoped.
- Wall 2 S1: full-map figures are median×n projections (28.7 ms ×
  18,233 = 523.7 s); the measured serial wall was 166.8 s (right-skewed
  per-source distribution, 3.1× gap) and the full map was never run in
  parallel in-record — read the improvement against 166.8 s.
- Wall 4: probe speed range is per-row 1.2x/67x/21x/14x (1.5k/3k/6k/12k
  incidences); the 6k ARI gap is 0.022, slightly over the "~0.02"
  phrasing. Seed 42 was unimplemented at execution (dead param); wired
  2026-11-03 (triage #7).
- Wall 3 (2026-11-03, goldfish_trial_followups item 1): the `--cap 6000`
  rerun declined at triage has since run — iFUB 31.7s/249.7s/1248.7s vs
  sampled 10.0s/24.4s/56.6s at 12k/30k/60k, all-pairs infeasible ×3;
  cap-500 numbers reproduce (S3 paragraph above updated). The rerun is
  guarded (sampled-leg 128 MB chunking; sparse generator above n=5000)
  after the unguarded first attempt thrashed the machine 2026-11-03.
