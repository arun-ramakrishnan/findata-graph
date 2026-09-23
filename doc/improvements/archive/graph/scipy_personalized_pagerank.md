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

Parent: `doc/improvements/archive/graph/scipy_graph_bridge.md`; spike
`/tmp/graph_spike.txt`; lane `helpers/graph/scipy_bridge.py`.
