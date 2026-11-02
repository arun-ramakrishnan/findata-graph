---
title: "Route hyper trail distances through a repo-native bipartite BFS lane"
status: executed
filed: "2026-11-02"
executed: "2026-11-02"
completed_md: "473"
area: "helpers/graph (new hyper_trails module), helpers/bench/hyper_scale_bench.py"
---

# Route hyper trail distances through a repo-native bipartite BFS lane

**Date:** 2026-11-02 · **Status:** EXECUTED (S1+S2 landed; S3 waits on the first path ask) ·
**Area:** `helpers/graph/` (new `hyper_trails` module),
`helpers/bench/hyper_scale_bench.py`, call site on the HGX unlock shelf
(lane 4)

## 1. Motivation

Trigger (2026-11-02 session): HGX unlock-shelf lane 4 — hyper shortest
paths — is blocked upstream. `hypergraphx` 1.8.0
`calc_ho_shortest_paths` dies in `dict_to_df` with
`ValueError: setting an array element with a sequence` (installed at
`.venv/lib/python3.14/site-packages/hypergraphx/measures/shortest_paths.py:173-194`:
`nx.all_pairs_shortest_path` returns PATH LISTS, which `dict_to_df`
assigns into a float `np.empty` matrix). Reproduced on a 3-edge toy
hypergraph — a code bug, not a data artifact.

The block arrives at the worst possible time. The growth record's last
entry (doc/local/perf/graph_scaling.md, tail 2026-09-25) saw the hyper
layer at h_edge 1,558 / 8,070 incidences; the live snapshot (2026-11-02
14:26) is 11,693 / 51,554 — a 6-7x surge with no watcher on it (fixed
same day: graph_scale_watch hyper axes). The hyper layer is now the
interesting substrate, and its distance lane is dead.

| blocker | measured | verdict |
|---|---|---|
| upstream fix | PyPI: hypergraphx 1.8.0 IS the newest release; bug unfixed | waiting is not a plan |
| upstream design (even if patched) | n x n dense `np.empty`: 18,233^2 x 8 B ~ 2.7 GB at live size; raw labels used as matrix indices | does not scale to us |
| move library | no maintained fixed alternative verified; lanes 3/5/6 already work on HGX | migration cost for one dead module |

## 2. Evidence (measured 2026-11-02, this box)

| Configuration | Result | Verdict |
|---|---|---|
| HGX `calc_ho_shortest_paths` 1.8.0 | ValueError on a 3-edge toy hg; n x n dense ~2.7 GB design | dead upstream |
| bipartite CSR from live `h_incidence` | 18,233 entities + 11,691 edge-nodes, 103,108 arcs, MB-scale | build trivial |
| scipy dijkstra (limit=40), 800 sources | 4.1 s -> 5.1 ms/source; avg 12,153 reachable entities/source | adopt |
| projected all-sources trail map | ~94 s at today's scale; linear in arcs | adopt |
| clique projection (contrast) | SUM C(m,2) = 2,453,321 dyads live; louvain OOM >= 3.42M dyads (bench 09-13) | the wall this lane avoids |

Structure findings from the same probe: 240 components; the biggest
entity-side component holds 16,239 of 18,233 entities (89%) — the FULL
trail universe is effectively one giant component, versus per-family
largest components of only 5-15% in the projected view. The bipartite
view is strictly more informative than anything the clique projection
could offer at this scale.

Ruled out, do not re-audit: library switch (no fixed fork exists —
checked PyPI JSON API 2026-11-02); waiting upstream (design is n^2-dense
even with the crash fixed); BFS over the clique projection (materializes
the measured OOM wall, Section 3).

## 3. Design

### 3.1 Why the bipartite lane does not pay the clique tax

The dyadic OOM comes from MATERIALIZING the clique projection: every
hyperedge with m members expands to C(m,2) = m(m-1)/2 dyad edges —
quadratic in m, and the cost concentrates in the heavy tail (58
hyperedges have >= 100 members; the country edge alone, m = 850,
contributes C(850,2) = 360,825 dyads, ~15% of the whole 2.45M tax).
Any analytics lane built on that projection must hold all 2.45M dyads
in memory at once — the exact point where bench louvain crossed from
"completed in 142.7 s" (2.85M dyads) to OOM (>= 3.42M, 14 GB box).

The bipartite (star) representation never builds that object. Each
hyperedge is ONE hub node with m spokes — LINEAR in m. The country edge
costs 850 arcs (1.65% of 51,554 incidences); live arcs total 2 x
h_incidence = 103,108, a few MB as a scipy CSR. The 34-48x clique
inflation (dyads per incidence: 34.2x at the 100K bench posture, ~47.6x
live) simply never happens, because the shared hyperedge is a hub the
members reference, not all-pairs edges between them.

Distance semantics are preserved EXACTLY for the unit-cost trail
metric: a hyper trail between two entities IS a path in the bipartite
incidence graph, so bipartite distance / 2 = trail distance in
hyperedge hops. Same shortest-path function HGX was attempting, minus
the dense matrix and minus the label-index bug. Per-source
`scipy.sparse.csgraph.dijkstra` keeps only a frontier in memory and
releases it per call; results stay sparse (queried pairs only), never
n x n.

Scope honesty: this lane replaces shortest paths / distance queries
ONLY. Clique-native analytics (louvain/community on the dyadic
projection) remain infeasible at the tail — that wall is now recorded
by the graph_scale_watch clique-tax WARN axis (shipped 2026-11-02), by
design, not by oversight.

### 3.2 Alternatives, and why each lost

1. Move library — no maintained fixed alternative verified; HGX keeps
   working for lanes 3/5/6 (EM fits, communities); churn without gain.
2. Wait / patch upstream — 1.8.0 is newest and unfixed; a patched
   dict_to_df still allocates n^2 dense (~2.7 GB live). Dead end twice.
3. BFS over the clique projection — materializes 2.45M dyads before
   the first query; that is the wall, not a way around it.

### 3.3 Slices

- S1: `helpers/graph/hyper_trails.py` — bipartite builder (reads
  `h_incidence` via `helpers.misc.duckdb_lock.open_read_only`,
  DISTINCT entity/edge pairs, weights carried on arcs) -> scipy CSR;
  `trail_distances(sources, limit)` per-source dijkstra returning
  sparse reachability (distance/2 semantics documented); component
  census helper. Tests: toy hypergraphs (3-chain, star, disconnected)
  vs networkx ground truth; limit semantics; absent-`h_incidence`
  SKIP.
- S2: bench leg in `hyper_scale_bench.py` (`--trail-bfs`) measuring
  ms/source and full-map wall time at the 100K synthetic posture AND on
  the live snapshot; record results in graph_scaling.md's hyper
  section.
- S3: repoint the lane-4 call site (HGX unlock shelf ledger:
  doc/improvements/pending.md "HGX unlock shelf" entry + dated addenda
  in archive/graph/hgx_unlock_shelf_probe.md) at `hyper_trails`; retire
  the `calc_ho_shortest_paths` call there. HGX stays for everything
  else.

S1 unblocks S2's measurements; S3 depends on S1 only.

## 4. Acceptance criteria & shakedown

1. Ground truth: `hyper_trails` distances equal networkx bipartite
   shortest-path/2 on three hand-built hypergraphs — zero mismatches.
2. Structural guard: built CSR has exactly 2 x DISTINCT-incidences
   arcs — the module cannot silently grow a clique projection.
3. Live posture: full-source map over 18,233 entities completes < 5 min
   wall, peak RSS < 500 MB (extrapolated from 5.1 ms/source ~ 94 s;
   3 runs, report the median — never one run).
4. Gates stay green: ruff + targeted pytest only; the 19
   graph_scale_watch tests pass untouched.

No ontology-eval bullet: read-only analytics lane; no rosters,
crosswalks, hierarchies, or extractor rules change.

| Projected outcome | Today | After |
|---|---|---|
| hyper shortest paths | blocked (upstream ValueError) | 5.1 ms/source; ~94 s full map |
| memory profile | n^2 dense ~2.7 GB (unreachable) | MB-scale CSR + per-source frontier |
| trail-universe coverage | 0 (lane dead) | 240-component census; avg 12,153 reachable entities/source |

## 5. Risks

- **Weighted-distance semantics** — `h_incidence` carries
  weight/direction/role columns; unit-cost is the trail doctrine and
  the default. Keep weights on the CSR but do not wire a weighted
  variant until a query needs it; declare the choice in the module
  docstring.
- **limit cutoffs read as "unreachable"** — same doctrine as sampled
  chains: outputs are lower bounds and MUST print the limit used
  (limit=40 bipartite steps = 20 hyperedge hops).
- **Duplicate incidences** (entity repeated inside one hyperedge) —
  DISTINCT pairs at build; live today 51,554 rows -> 51,554 distinct
  pairs, but the test guards it anyway.

## 6. Non-goals

- No louvain/community work on the dyadic projection (clique-tax wall
  stays; the watcher records its approach).
- No HGX removal — lanes 3/5/6 keep it.
- No persistent distance-store build; distances compute on demand.
- Lane-6 core-periphery and the onager personalized-pagerank upstream
  block (helpers/graph/onager.py:836) are separate ledger items.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-02 | toy hg repro, calc_ho_shortest_paths | ValueError: setting an array element with a sequence | 3-edge hg; code bug, not data |
| 2026-11-02 | bipartite build from live h_incidence | 18,233 + 11,691 nodes, 103,108 arcs; 240 components; top comp 16,239 (89%) | read-only via duckdb_lock funnel |
| 2026-11-02 | scipy dijkstra limit=40, 800 sources | 4.1 s -> 5.1 ms/source; avg reach 12,153 | full-map projection ~94 s |
| 2026-11-02 | hyper_scale_bench --incidences 100000 | hyper lane 100,030 inc, fit100 1.2 s, 9 MB; dyadic 3,422,116 dyads (34.2x) | onager LOAD fallback landed same day |
| 2026-11-02 | clique tax census, live | SUM C(m,2) = 2,453,321; max m = 850; 58 edges >= 100 | country edge alone 360,825 dyads |
| 2026-11-02 | PyPI JSON API check | hypergraphx 1.8.0 newest = installed | via curl; no fix upstream |
| 2026-11-02 | hyper_trails.py S1 + 14 tests | all green; networkx ground truth zero mismatches; arcs == 2×DISTINCT guard | hermetic, no live store |
| 2026-11-02 | hyper_trails.py --trail-live (800 src, limit 40) | build 0.44 s; 5.99 ms median / 6.64 p90; reach median 16,238 | production run of the probe lane |
| 2026-11-02 | hyper_scale_bench --trail-live --trail-full-map | 18,233 sources, wall 166.8 s, peak RSS 156 MB | full map at limit 40 steps (20 hops) |
| 2026-11-02 | hyper_scale_bench --trail-bfs 100K synthetic | 200,060 arcs, build 0.25 s; 6.67 ms/source median | vs hyper lane fit100 1.23 s / 9 MB, same posture |

## Status 2026-11-02 — S1+S2 landed

- S1: helpers/graph/hyper_trails.py + tests/test_hyper_trails.py (14
  hermetic tests: networkx ground truth zero mismatches, limit
  semantics incl. odd-step mid-hop drop, absent-store SKIP, the
  arcs == 2×DISTINCT guard). ruff clean.
- S2: hyper_scale_bench.py --trail-bfs / --trail-live /
  --trail-full-map legs; numbers recorded in
  doc/local/perf/graph_scaling.md ("Hyper-native posture + bipartite
  trail lane", 2026-11-02). Live full map: 18,233 sources in 166.8 s
  wall, 156 MB peak — the ~2.7 GB HGX dense design and the 3.42M-dyad
  louvain OOM are both moot on the distance axis.
- S3: nothing to repoint — calc_ho_shortest_paths has no live call
  site (lane 4 was never wired; pending.md shelf item 4 stays gated on
  the first path-explanation ask, which now wires THIS lane).
