---
title: "SciPy Katz exact-solve — retiring the alpha pin"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "280"
---

# SciPy Katz exact-solve — retiring the alpha pin

**Date:** 2026-09-23 · **Status:** EXECUTED — archived 2026-09-23

Child of `scipy_graph_bridge.md` (S2, first item). Implementation lands in
the umbrella patch `scipy_algos`.

## 1. TL;DR

One sparse solve — ``x = (I - alpha*A)^-1 * beta*1`` — replaces Onager's
iterative Katz. The ``1e-4`` alpha pin exists only to keep Onager's power
iteration inside its convergence radius (alpha < 1/lambda_max; Onager's
default 0.1 diverges live); an exact solve needs no such margin and any
admissible alpha is one call away. Near-critical alpha restores the node
spread the pin flattens.

## 2. The algorithm, in one page

**Idea.** A node is central if it is connected to many central nodes.
Katz makes that recursion finite by counting walks of every length but
geometrically damping longer ones: each walk of length k contributes
``alpha^k``. With ``beta`` as a floor given to every node:

    x_i = beta + alpha * sum_j A_ij * x_j
        = beta * (1 + alpha*A + alpha^2*A^2 + ...) 1
        = (I - alpha*A)^-1 * beta*1          (geometric series)

The closed form is why a solve replaces an iteration: the series IS the
linear system.

**Why alpha is load-bearing.** Valid iff ``alpha < 1/lambda_max(A)``
(spectral radius). At ``alpha -> 0`` the score degenerates toward plain
degree (only 1-walks survive) — this is exactly the flattening the 1e-4
pin causes: on the live graph scores differ in the 4th decimal. As
alpha approaches the radius, long walks dominate and relative
differences SHARPEN (the ranking re-weights toward globally well-placed
nodes) — the spread restoration this lane buys. At the radius itself
Katz is undefined (the series diverges; the matrix singular).

**Worked example — star K1,4 (center c, 4 leaves), beta=1.**
``x_leaf = 1 + alpha*x_c``; ``x_c = 1 + 4*alpha*x_leaf``. Substituting:
``x_c = (1 + 4a)/(1 - 4a^2)``. At ``alpha = 0.1``:
``x_c = 1.4/0.96 = 1.4583`` (the incumbent's hand-verified value),
``x_leaf = 1.1458``. At the pin ``alpha = 1e-4``:
``x_c = 1.000400``, leaves ``1.000100`` — ratio 1.0003, i.e. FLAT.
The star's ``lambda_max = 2``, so the radius is ``alpha = 0.5``.

**Family.** Katz is attenuated eigenvector centrality with a floor:
as ``beta -> 0`` and ``alpha -> 1/lambda_max`` it converges to the
eigenvector. PageRank is the row-normalized cousin (see the PPR child);
eigenvector is the limit case.

**Complexity.** One sparse LU factorization (``spsolve``) vs a power
iteration whose step count is unknown a priori — the iteration is only
cheaper when few digits are needed AND alpha is far from the radius.

## 3. Incumbent state

- ``onager_katz`` (alpha pinned 1e-4, beta 1.0): ranking stable across
  [1e-4, 1e-2] but scores are flat; default 0.1 raises "Convergence
  failed after 100 iterations" (lambda_max > 10 at the old max degree
  89 — live max degree is now 304, so 1/lambda_max ~ 0.057 and 0.1 is
  outside the radius entirely).

## 4. Design

- Unweighted ex-index CSR (measured 2026-09-23, NOT weighted as
  first drafted: weighted solves par worse against live Onager
  (r=0.92) than unweighted (r=0.9996) — Onager's pinned Katz is
  effectively unweighted at 1e-4 scale; self-loops dropped, edges
  deduped, `listed_on_index` excluded like the centrality rule).
- ``spsolve(csc(I - alpha*A), beta*ones)``; ``--alpha`` free, default
  1e-4 for parity continuity; ``beta`` fixed 1.0 (incumbent).
- Admissibility guard: lambda_max via ``eigsh``; refuse
  ``alpha >= 0.99/lambda_max`` with the bound printed — beyond it Katz
  is not just slow, it is undefined.
- Dry-run default; ``--apply`` UPSERTs under ``katz_centrality``
  (write_analytics). ROUTING: katz stays ONAGER_DEFAULT until an
  operator lane-flip; the solver is the ``SCIPY_EXACT`` alternative.

## 5. Acceptance

1. Live parity at alpha=1e-4 vs pinned Onager Katz: r ~ 1.0.
2. Near-critical demo: at alpha = 0.9/lambda_max (measured lambda
   recorded in the test/doc) Onager fails to converge while the solve
   returns exact values; the demo alpha is lambda-adaptive, NOT the
   umbrella's 0.1 (which now exceeds the live radius on both engines).
3. Star-4 toy matches the hand-exact solution above; dense
  ``numpy.linalg.solve`` oracle at toy scale.
4. ``tests/test_scipy_bridge.py`` additions green.

## 6. Non-goals / risks

- No default-lane flip in algorithms.py (operator decision, as with L1a).
- Near-critical solves amplify float noise (alpha within 1% of the
  radius is a 1e2-1e4 condition number) — documented, not hidden.
- Weighted-direction variants out (D16 clause).

Parent: `doc/improvements/archive/graph/scipy_graph_bridge.md`; spike
`/tmp/graph_spike.txt`; lane `helpers/graph/scipy_bridge.py`.
