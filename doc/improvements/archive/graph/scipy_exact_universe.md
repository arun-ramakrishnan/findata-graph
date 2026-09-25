---
title: "Scipy exact universe — all-roots chains, structure scalars, full-walk centralities"
status: executed
filed: "2026-09-26"
executed: "2026-09-26"
completed_md: "300"
area: "helpers/graph/stats.py, helpers/graph/scipy_bridge.py, tests"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Scipy exact universe — all-roots chains, structure scalars, full-walk centralities

**Date:** 2026-09-26 · **Status:** EXECUTED (operator-directed arc;
implementation landed same day — three scipy lanes plus the S6
default-serving slice per the switching policy; only exact chains
remains opt-in) ·
**Area:** `helpers/graph/scipy_bridge.py`, `helpers/graph/stats.py`,
`tests/test_scipy_bridge.py`, `tests/test_graph_stats.py`.

## 1. Motivation

The 2026-09-26 ceiling audit (graph_scaling.md) found three diagnostics
serving sampled/NULL/degraded output because all-pairs computation was
priced on the Onager engine. The scipy lane's measured source-linearity
(dijkstra ~sources·E: 1,734 sources → 2.9 s 4-way) re-prices all of
them at ~36-80 s — opt-in-lane class, not gate class:

1. `stats.py longest_chains(max_exact=3000)`: distances from 3,000
   stride-sampled roots, labeled "lower bounds" (live: 21,461 roots).
2. `graph_metrics` diameter/radius/APL: served NULL on the disconnected
   live graph (106 components) — the component-check skip.
3. Full-walk centralities: dark since the lane diet (only the 1,734-row
   company contract is served); the old 21,453-node stamp semantics
   have no successor.

Operator ruling: build all three; consumer demand is expected, not
required (2026-09-26).

## 2. Evidence (measured 2026-09-26, this box — live pipeline runs, not projections)

| Lane | Served today | MEASURED exact cost (live) | Peak RSS |
|---|---|---|---|
| longest_chains `exact=True` | 3,000/21,461 roots, lower bounds | **119.2 s / 133.1 s** (2 runs, ±10%), both projections | **7.9–9.8 GB** in-process |
| `structure_stats` jobs=4 | NULL (disconnected) | **36.2 s / 38.7 s / 41.5 s** (3 runs, scalars identical) | 0.09 GB (sharded scalar-reduce) |
| `closeness-harmonic --universe all` jobs=4 | unserved (contract only, 2.7 s) | **38.8 s** at 22,054 sources | sharded, sub-GB |

Live results: exact chains — diameter 13 confirmed (the sampled lower
bound was tight) but **621 canonical pairs at d=13 and DIFFERENT top
chains** (the 3,000-root sample missed Benedire↔Deekshitha et al.);
structure — diameter 9, radius 5 (giant component 21,681 of 22,054, 20
components), APL 4.046 over 470.06M reachable ordered pairs;
full-walk — top closeness/harmonic = NIFTY index nodes (0.465 /
10,731 raw).

### Defects found by the detailed-pipeline pass (both fixed in-arc)

1. **Degenerate radius**: global min-eccentricity on a disconnected
   graph is pinned to 1 by any 2-node isolated pair (measured live:
   radius 1). Convention: radius = min eccentricity over the LARGEST
   component (documented in the docstring).
2. **Raw closeness explodes on tiny components**: a 2-node isolated
   pair scored 22,053.0 under the contract's (N-1)/sum convention
   (contract nodes all fully reach, so it never showed). Fix:
   Wasserman-Faust scaling in the all-universe path ONLY —
   `clo_wf = clo_raw × (r-1)²/(N-1)²`, factor 1.0 for full-reach rows →
   contract parity untouched (verified: contract lane output unchanged).
3. **Shard diagonal indexing**: worker self-pair zeroing subtracted
   `lo` from GLOBAL column indices — shard 0 (lo=0) masked it, other
   shards zeroed real distances and left self-pairs counted (golden
   test caught it: reachable_pairs 10 vs 8 across jobs=2/4). Fix:
   diagonal sits at (j, lo+j) — rows shard-local, columns global.

## 3. Design

1. **S1 — `scipy_bridge.structure_stats(A, jobs=4)` + `structure` CLI**:
   sharded multi-source dijkstra (house `fork_map`, disjoint source
   shards); each worker reduces its distance chunk to (shard diameter,
   min row-eccentricity, finite-pair sum, finite-pair count) — parent
   combines to exact diameter / radius / mean over reachable ordered
   pairs. No n×n matrix ever assembles in the parent (~4 × 0.9 GB
   transient worker chunks).
2. **S2 — `longest_chains(..., exact=True)`**: full dist+predecessors
   in-process (21,461² ≈ 5.5 GB peak, documented); the old
   `triu_indices` finite-stats pass (3.7 GB index arrays at 21k — an
   OOM-shaped leftover from the 1.7k era) is replaced by a chunked
   integer histogram (median/diameter/ties identical, O(n) memory);
   tier-walk and path reconstruction unchanged. Default stays
   `max_exact=3000` (gate-safe); `--exact-chains` CLI flag opts in.
   S3 hard-cap doctrine respected: the gate leg never runs exact.
3. **S3 — `closeness-harmonic --universe all`**: all-endpoints source
   set; compute-only by contract — `--apply --universe all` refuses
   (writing 21,453 rows into the 1,734-row contract metrics would fork
   the lane-served universe).
4. **S4 — tests (landed):** goldens for the structure lane, WF
   centralities, apply-refusal, exact-chains semantics; job-count
   invariance (the invariant that caught the shard-diagonal defect).
5. **S5 — proposal + doc reconciliation (landed).**
6. **S6 — default-serving of the NULL/unserved surfaces (landed;
   operator ruling: "they should be the default")**: the switching
   policy classifies promotions by the OLD surface state —
   NULL/skipped/unserved surfaces DEFAULT into the standing pipeline
   when the new cost is maintenance-class; opt-in survives only for
   replacements of gate-budgeted output (chains) and memory hazards.
   Applied: `full_universe_stats` — ONE sharded dijkstra pass yields
   WF closeness + harmonic + structure scalars — joined
   `stamp_centrality_cache` (db_path threaded through
   `_build_graph`). New ephemeral tables `v_centrality_closeness_full`
   / `v_centrality_harmonic_full` / `v_graph_structure` in
   `_CENTRALITY_TABLES` (rebuild-drop) + `EPHEMERAL_TABLES` (manifest).
   Measured stamp wall 2.75 s → **27.5 s** (0.28 GB). Consumers:
   `stats.py` prints exact structure scalars from the stamp;
   `/api/graph/stats` gains `structure_exact`. Contract untouched:
   full-walk values live only in the stamp tables, never in
   `graph_analytics`.

### Switching policy (2026-09-26, operator ruling)

| Old surface state | New-compute cost class | Policy |
|---|---|---|
| NULL / skipped / unserved | maintenance-class (fits stamp/advisory lane) | **DEFAULT it** — flags on nothing are just unwired value |
| Existing sampled/approx gate output | exact exceeds gate budget or memory envelope | **OPT-IN**, cost documented beside the flag |
| Existing served output, frozen contract | different universe | **Separate surface, never overwrite** |

Only the chains row keeps S2 opt-in; S1/S3 are stamped defaults via S6.

## 4. Acceptance criteria & shakedown

1. `structure` golden: P3+isolated-pair fixture → diameter 2, radius 1
   (giant comp), APL 1.25, reachable_pairs 8, **job-count invariant
   (1/2/4 identical)** — the invariant is what caught the shard-diagonal
   defect.
2. Exact chains: tiny fixture — exact=True output identical to the
   sub-cap run; capped run carries the SAMPLED note, exact run does
   not; exact diameter ≥ capped diameter on a forced-cap graph.
3. Full-walk centralities: P5 closeness golden under WF scaling
   (0.4 endpoints / 0.6667 center); `--apply --universe all` refuses;
   contract lane output byte-identical pre/post (verified live).
4. Gate safety: default `longest_chains` path unchanged (16.3 s
   measured, SAMPLED note intact; 49 pre-existing tests green).
5. Live shakedown (measured): see §2 table — all three lanes timed on
   the live graph.

| Outcome | Before (served) | After (measured) |
|---|---|---|
| Longest chains at 22k | 3,000/21,461 sampled roots — 16.3 s, 1.61 GB, "lower bounds", top chains partially wrong | exact 21,461 roots — **119-133 s, 7.9-9.8 GB**; diameter 13 confirmed, 621 canonical d=13 pairs, different (true) top chains. OPT-IN (gate doctrine) |
| Diameter / radius / APL | **NULL** (Onager skipped all-pairs; disconnected) | **9 / 5 / 4.046** — 36-42 s standalone, **STAMPED DEFAULT** via S6 (consumers read `v_graph_structure`; stamp +40 s class) |
| Full-walk centralities | **Unserved** — contract-only (1,734 rows, 2.7 s) since the lane diet | all 22,054 nodes, WF-scaled — 38.8 s standalone, **STAMPED DEFAULT** via S6 (`v_centrality_*_full` tables) |
| Stamp lane wall | 2.75 s (seven tables) | **27.5 s** (ten tables incl. the shared all-universe pass), 0.28 GB |
| Gate path (chains) | 16.3 s sampled | **16.3 s sampled — unchanged by design** |

## 5. Risks

- **Memory**: exact chains measured 7.9–9.8 GB peak in-process (the
  original ~6 GB projection was low — predecessors + tier-scan
  transients) — opt-in CLI only; documented in the docstring.
- **Runtime**: exact chains ~2 min/projection — opt-in; the gate never
  pays it.
- **Conventions recorded** (no consumer can have depended on the old
  NULL/sampled semantics): APL = mean over reachable ordered pairs
  across all components; radius = min eccentricity over the largest
  component; all-universe closeness = Wasserman-Faust scaled.

## 6. Non-goals

Persisting structure scalars or full-walk centralities into
`graph_analytics` (contract fork; wait for a consumer) · promoting
exact chains into `make graph-stats` default · Barnes-Hut/exact layout
interaction · prefetch/daemon lanes.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 09-26 | L1a linearity | 1,734 src → 2.9 s 4-way / 6.5 s serial | scipy_graph_bridge |
| 09-26 | longest_chains default (sampled) | 16.3 s, 1.61 GB | gate-path baseline unchanged |
| 09-26 | longest_chains exact=True run 1 | 133.1 s, 9.81 GB | diameter 13, 621 pairs, new top chains |
| 09-26 | longest_chains exact=True run 2 | 119.2 s, 7.93 GB | same scalars |
| 09-26 | structure_stats jobs=4 (3 runs) | 36.2 / 38.7 / 41.5 s, 0.09 GB | identical scalars; run-1 outlier = load |
| 09-26 | closeness-harmonic --universe all | 38.8 s 4-way, 22,054 src | top: NIFTY TOTAL MARKET |
| 09-26 | contract lane post-change | 1,734 rows, values unchanged | WF factor 1.0 parity verified |
