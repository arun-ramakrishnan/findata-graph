---
title: "SciPy personalized PageRank — exact solve, lane resurrection — DEFERRED"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "272"
---

# SciPy personalized PageRank — exact solve, lane resurrection — DEFERRED (trigger: a named consumer; revisit if it becomes a must)

**Date:** 2026-09-23 · **Status:** DEFERRED — archived 2026-09-23

Child of `scipy_graph_bridge.md` (S2, second item). Implementation lands
in the umbrella patch `scipy_algos`.

## 1. TL;DR

``x = (I - alpha*P)^-1 (1-alpha) v`` with ``P = D^-1 A`` row-stochastic
and ``v`` a seed vector — exact, one solve. Resurrects the
personalized-PageRank lane Onager dropped (restart vector hardcoded to
uniform; the personalization parameter never reaches the extension), at
zero iteration-divergence risk.

## 2. The algorithm, in one page

**Idea.** A random surfer: at each step follow a uniformly random edge
with probability ``alpha`` (the damping, standard 0.85), or TELEPORT
back to the seed with probability ``1 - alpha``. The score of a node is
its long-run visit probability. Personalization is entirely in WHERE
the teleport lands: uniform ``v`` gives global PageRank; a one-hot
``v`` on the seed turns the metric into "random-walk proximity to the
seed" — mass concentrates on the seed's neighbourhood.

**Equation.** The visit probabilities are the stationary distribution:

    x = alpha*P*x + (1-alpha)*v      =>      x = (I - alpha*P)^-1 (1-alpha) v

with ``P = D^-1 A`` (each of a node's neighbours equally likely). The
exact solve replaces the usual power iteration; for a stochastic P the
system is always well-conditioned (no spectral-radius gamble, unlike
Katz — the damping guarantees it).

**Two properties worth knowing.**
- ``x`` sums to 1: it is a probability distribution, so scores are
  directly comparable across seeds on the same graph.
- Dangling nodes (degree 0) have an all-zero P-row — the surfer is
  stuck. Convention: teleport back to ``v`` (the seed), which keeps the
  solve exact and the distribution intact.

**Worked example — path a-b, seed a, alpha=0.85.**
``x_a = 0.15 + 0.85*x_b``; ``x_b = 0.85*x_a`` (b walks back to a).
Solving: ``x_a = 0.15/(1-0.85^2) = 0.5405``, ``x_b = 0.4595``; sums to
1. The seed keeps the edge — and the surfers it sends out come home.

**Family.** Uniform PageRank (the healthy incumbent) is this exact
formula with ``v = 1/N``. Katz is the un-normalized cousin; the
personalized variant is the standard "who is related to X" similarity
ranking (the original use in recommendation).

## 3. Incumbent state

None to par against — that is the point. ``onager_pagerank`` exists
(uniform; healthy) but the personalized variant has no working
incumbent (restart vector hardcoded in the extension).

## 4. Design

- Same weighted-CSR build as the Katz child; ``P = D^-1 A``.
- ``v``: one-hot on the ``--seed`` entity (required); dangling rows
  teleport back to the seed.
- alpha default 0.85; dry-run default; ``--apply`` UPSERTs under
  ``pagerank_personalized``. ROUTING: SCIPY (new lane, Onager cannot).

## 5. Acceptance

1. Seed-concentration: personalised mass on the seed neighbourhood
   far exceeds the uniform-seed run on the same graph (live, 2 seeds).
2. Toy dense-inversion oracle (numpy ``solve``) exact match; the
   a-b example above is a hand oracle.
3. Handle: missing seed name raises with a clear message.
4. ``tests/test_scipy_bridge.py`` additions green.

## 6. Non-goals / risks

- No top-k list surface — per-entity floats only (contract shape parity
  with the other centrality lanes); provenance printed to stderr.
- No incumbent contract means no migration story: the metric name is
  new, first write creates it.

## 7. Structural finding (2026-09-24) — why this lane stays deferred

Re-measured on the live graph (22,046 nodes / 57,581 edges; scipy 1.18.1)
while evaluating whether to un-defer. Two independent reasons to keep it
parked, and the SECOND is the one we keep re-deriving:

1. **Onager bug (the filed reason).** `onager_ctr_personalized_pagerank`
   ignores its personalization column and hardcodes the restart to
   `node_id 1` (`onager.py:1099`, `graph_design.md:286`,
   `pending.md` N5-6). scipy sidesteps this — but not the next one.

2. **Graph structure makes PPR degenerate (the real blocker).** The graph
   is star-dominated and leaf-heavy: **84.5% of nodes are degree-1 leaves**
   (`deg1=18,639 / 22,046`), `degmax=850`, and the top hubs are
   membership/country nodes (`india` 850, NIFTY indices ~500-750) before
   any company. A random walk with the standard damping cannot propagate:

   | Seed | seed_mass | hop-1 mass | hop≥2 mass |
   |---|---|---|---|
   | Infosys (ex-index CSR) | 0.416 | 0.400 | 0.184 |
   | Wipro (ex-index CSR) | 0.455 | 0.416 | — |
   | Infosys (company-only subgraph) | 0.443 | 0.419 | 0.137 |

   ~82% of the mass sits on the seed + its direct neighbours; the
   post-seed top-10 is hub-contaminated (`india`, `NIFTY TOTAL MARKET`, a
   note hub). Stripping non-company hubs removes the *contamination*
   (Reliance/Wipro/HCL/TCS rise) but not the *localization* (hop≥2 still
   only 0.14). In short: **PPR on this graph is a 1-hop ego ranking**,
   which `GET /api/graph/neighbors` already serves cheaper.

**Implementation trap (record so nobody re-discovers it):** the equation
printed in §2 (`x = (I − αP)⁻¹(1−α)v`) is transposed. The correct
stationary form is `x = αPᵀx + (1−α)v`, i.e. solve `(I − αPᵀ)`. Using
`(I − αP)` with `P = D⁻¹A` does not normalise (`sum(x) ≈ 127`, not 1) —
it is the wrong eigenvector orientation.

**Verdict:** stays DEFERRED. Revival requires BOTH a named consumer that
wants more than the 1-hop ego bundle AND a projection that fixes the
localization (hub-stripped / per-sector subgraph), not merely a working
solver. The scipy one-solve itself is cheap (0.22-0.29 s) and correct —
the blocker is the graph, not the engine.

Parent: `doc/improvements/archive/graph/scipy_graph_bridge.md`; spike
`/tmp/graph_spike.txt`; lane `helpers/graph/scipy_bridge.py`.
