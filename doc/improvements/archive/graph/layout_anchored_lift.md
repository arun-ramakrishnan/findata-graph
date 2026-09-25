---
title: "Layout anchored lift — sampled-anchor FA2 and the 50k ceiling"
status: executed
filed: "2026-09-26"
executed: "2026-09-26"
completed_md: "299"
area: "helpers/graph/layout.py, app.py /api/graph/positions, tests/test_graph_layout.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Layout anchored lift — sampled-anchor FA2 and the 50k ceiling

**Date:** 2026-09-26 · **Status:** EXECUTED (implementation landed
ahead of filing — operator-directed arc, 2026-09-26; operator visual
shakedown PASSED same day — hub separation and cluster readability
confirmed on the rendered cloud) ·
**Area:** `helpers/graph/layout.py` (ENGINE, `_MAX_NODES`,
`compute_positions_fa2`), `tests/test_graph_layout.py`.

## 1. Motivation

The corporate-intake arcs tripled the graph; the whole-graph cloud
layout never followed. `/api/graph/positions` serves a deterministic
server-side FA2 sidecar (`memory/graph_layout.json`, edge-set-hash
gated) whose engine carried a hard refusal at `_MAX_NODES = 8,000`
nodes — the live incident set is **22,054 nodes** (`edge_set_hash`
endpoints, 57,632 edges), so every request 503s and the client silently
falls back to its own components/concentric layout (graph.ts fetch is
deliberately fail-silent; the renderer chain cached → components →
concentric is client-side only). The server lane's product value —
stable cross-visit cloud coordinates — has been dark since the
2026-09-23 intake. The old engine's refusal comment said "grow into
Barnes-Hut before lifting it": full-repulsion cost was measured at
**7.34 s/iteration → ~73 min** for the 600-iteration solve at 22k
(memory fine, 0.22 GB) — naively lifting the ceiling was never viable.

Trigger: the 2026-09-26 scaling review closed every other open item
(centrality stamp killed by the lane diet; T1 watch armed) leaving the
layout ceiling as the only live blocker.

## 2. Evidence (measured 2026-09-26, this box)

Per-iteration repulsion cost at the live set (22,054 nodes; two full
sweeps of 3 averaged iterations each — sweep-2 values below, spread
<±10% on serial rows, larger on pool rows from pool-setup amortization;
`i5-6500 Skylake 4C/4T`):

| Configuration | per-iter | ×600 solve | Verdict |
|---|---|---|---|
| v0 current full O(n²) | 7.34 s | 73 min | baseline (refused at 8k) |
| v1 micro-optimized full | 4.48 s | 45 min | 1.64×, still dead |
| v1 full + fork pool j4 | 4.91 / 4.65 s | 49 / 47 min | **pool LOSES at full scale** |
| v2 anchored M=512, serial | 0.059 s | **36 s** | adopt candidate |
| v2 anchored M=1024, serial | 0.134 s | **1.3 min** | **adopted** |
| v2 anchored M=1024 + pool j4 | 0.281 / 0.309 s | 3 min | pool loses (payload pickle > compute) |
| v2 anchored M=2048 + pool j4 | 1.463 / 1.561 s | ~15 min | pool overhead dominates |
| v2 anchored M=4096 + pool j4 | 1.980 / 2.070 s | ~21 min | worst — do not parallelize |

Profile of v0 (share of 6.47 s iteration): d² construction 26.5%,
second divide `k/d2` + add 20.6%, overlap pass 18.7%, BLAS matmuls
20.1%, full-matrix sqrt 5.6%. The cost is **elementwise, not BLAS** —
and every term scales with the anchor count, so micro-opts and sampling
compose.

Quality (1,600-node subgraph, 600 iters, 200k sampled pairs, stress =
Σ|layout-d − hop-d| / Σ hop-d): full **7.90M**, M=256 **6.75M**,
M=512 **7.62M**, M=1024 **7.94M** — one band (±11%); sampled M=256
beats full (sampling acts as repulsion regularization). Procrustes
normalized RMSE vs the full solve: M=256 0.797, M=512 0.525,
M=1024 0.381 — divergence is expected (FA2 has near-degenerate
optima); the contract is quality parity, not coordinate cloning.

**Defect found and fixed during the trial:** a FIXED anchor set never
breaks co-location symmetry — two nodes that collapse onto each other
(neither an anchor) feel identical forces forever and stack (adversarial
cycle graph: 293/600 distinct coordinates, median radius 100 vs 755
full). Fix: **per-iteration seeded anchor resampling** — every node
serves as an anchor ~600·M/n times per solve, so every stacked pair
eventually gets mutual repulsion. Post-fix: 600/600 distinct, median
radius 800.

**Alternative engines considered and declined:** igraph FR/grid layout
(different physics — forks the browser-FA2 comparability the params
mirror; the restored igraph lane is for analytics, not layout), HGX (no
layout surface), Barnes-Hut quadtree in numpy (the old comment's plan —
a multi-slice build; sampling achieves the O(n·M) class without one),
fork-pool parallelism (measured negative at anchor scale — serial is
the ship).

## 3. Design

`compute_positions_fa2` keeps its signature, contract (rounded ints,
centred, p95-normalized to radius 1200) and refusal semantics; only the
repulsion core and the ceiling change:

1. **S1 — anchored repulsion (landed):** each iteration draws a fresh
   seeded anchor subset (`default_rng(seed+1)`, sorted); nodes repel
   against M=1024 anchors via the same chunked matmul path. Micro-opts:
   ONE reciprocal of d² feeds both the inverse-square factor and the
   overlap term; the distance sqrt runs only on the overlap-masked
   subset (d² < (sᵢ+sⱼ)²); preallocated chunk buffer. M ≥ n degenerates
   to exact full physics.
2. **S2 — ceiling 8,000 → 50,000 (landed):** anchored cost is
   O(n·M)/iter → 50k nodes ≈ 3 min/solve. Refusal contract unchanged
   (ValueError → endpoint 503 → client fallback).
3. **S3 — engine rename (landed):** ENGINE `fa2-numpy` →
   `fa2-anchored-numpy` — the sidecar staleness gate compares engine +
   params, so the first read after deploy recomputes once (81 s) and
   every later visit replays byte-identically.
4. **S4 — tests (landed):** anchored determinism + distinctness,
   M ≥ n degenerate path, ceiling refusal at 50,001, endpoint engine
   pin updated.

Order: S1→S4 all landed 2026-09-26 (single-module change); S5
(proposal + doc reconciliation) is this file.

## 4. Acceptance criteria & shakedown

1. **Live solve**: `compute_positions_fa2(incident, edges)` at the
   real edge set — 22,054 nodes / 57,632 edges in **80.9 s**, radius
   p5/p50/p95 = 159/679/1200, 21,646/22,054 distinct int coordinates
   (98.1%; merges are int-rounded close pairs, mostly degree-1 leaves).
2. **Determinism**: two consecutive solves byte-identical (verified).
3. **Gate**: `pytest tests/test_graph_layout.py -q` — 9/9 green
   (determinism, empty graph, ceiling refusal at 50,001, anchored
   subset determinism/distinctness, degenerate full path, hash gate
   reuse/recompute/corrupt/order, endpoint shape/ETag/engine pin).
4. Visual shakedown (operator): load the Graph tab cloud view, confirm
   hub separation and cluster readability vs the client fallback.

| Projected outcome | Before | After |
|---|---|---|
| `/api/graph/positions` at 22k | 503 (ceiling) | 200, sidecar in ~81 s once, then gated replay |
| Full-solve wall | ~73 min (projected, refused) | **80.9 s measured** |
| Node ceiling | 8,000 | **50,000** (~3 min solve class) |
| Cross-visit coordinates | dark (client fallback) | restored (deterministic sidecar) |

## 5. Risks

- **Visual quality unverified by eyeball** — stress parity is a proxy;
  the operator shakedown (§4.4) is the arbiter. Rollback: revert ENGINE
  to `fa2-numpy` (sidecar gate recomputes/refuses as before).
- **Request-path recompute is 81 s** where it used to refuse instantly —
  first positions request after a topology change without a refresh
  pre-warm pays the solve in-request. Refresh pre-warms (normal flow);
  if this ever bites, add a request-path budget that 503s to the client
  fallback above N seconds (follow-up, not built).
- **Anchor sampling changes layout semantics** — positions differ from
  the old engine's (they must: the old engine could not run at all).
  Recorded here; the ENGINE rename is the contract marker.
- **1.85% merged coordinates** at 22k — int rounding on a 1200-unit
  canvas; sigma renders overlaps. If visible, raise `target` or add
  sub-unit jitter to the round (follow-up).

## 6. Non-goals

Barnes-Hunt quadtree · client/WebGL engine work · server-side
components/concentric fallback lanes (client owns that chain) · igraph/
HGX layout engines · layout perf-gate leg (refresh job, not a budgeted
gate path; revisit if a leg is ever wanted).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 09-26 | 1-iter v0 profile @22,054 | 7.34 s; d² 26.5% / div2 20.6% / overlap 18.7% / matmul 20.1% | elementwise-bound |
| 09-26 | v1 micro-opt full | 4.479 s/iter | 1.64× |
| 09-26 | v2 M=512 serial | 0.059 s/iter → 36 s solve | |
| 09-26 | v2 M=1024 serial | 0.134 s/iter → 1.3 min | adopted |
| 09-26 | v2 M=1024 j4 pool | 0.281 / 0.309 s/iter (2 sweeps) | pool negative |
| 09-26 | v2 M=2048 j4 pool | 1.463 / 1.561 s/iter | pool overhead dominates |
| 09-26 | v2 M=4096 j4 pool | 1.980 / 2.070 s/iter | worst |
| 09-26 | quality 1.6k/600it | stress full 7.90M; M=256 6.75M; M=512 7.62M; M=1024 7.94M; procrustes 0.797/0.525/0.381 | parity band |
| 09-26 | fixed-anchor cycle graph | 293/600 distinct, p50 r=100 | defect |
| 09-26 | resampled cycle graph | 600/600 distinct, p50 r=800 | fixed |
| 09-26 | LIVE production solve | 80.9 s; 21,646/22,054 distinct; determinism True | ship |
