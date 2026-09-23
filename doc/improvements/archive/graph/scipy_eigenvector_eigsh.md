---
title: "SciPy eigenvector centrality — eigsh robustness lane"
status: executed
filed: "2026-09-23"
area: "helpers/graph"
executed: "2026-09-23"
completed_md: "281"
---

# SciPy eigenvector centrality — eigsh robustness lane

**Date:** 2026-09-23 · **Status:** EXECUTED — archived 2026-09-23

Child of `scipy_graph_bridge.md` (S2, third item). Implementation lands
in the umbrella patch `scipy_algos`.

## 1. TL;DR

``eigsh(A, k=1, which="LA", tol=...)`` converges with an explicit
tolerance where Onager's power iteration raises "Convergence failed
after 100 iterations" (small/slow-mixing graphs), and agrees with
Onager wherever Onager converges (the healthy live lane, ~0.5 s).

## 2. The algorithm, in one page

**Idea.** The purest version of "central = connected to central": the
score vector must satisfy its own definition,

    A x = lambda x

Perron-Frobenius guarantees that for a connected graph with
nonnegative weights the LARGEST eigenvalue is real, positive, simple,
and has an all-positive eigenvector — that eigenvector is THE
centrality. (Katz damps this recursion and adds a floor; PageRank
normalizes rows first. Eigenvector centrality is the limit.)

**Why power iteration fails sometimes.** ``x_{k+1} = A x_k / ||A x_k||``
converges at rate ``|lambda_2/lambda_1|^k``. Slow mixing (near-degenerate
spectra: two clusters barely connected, bipartite-like structure where
``-lambda_1`` is also an eigenvalue) makes that ratio ~1 — hundreds of
iterations, and Onager's fixed 100-iteration loop raises. ``eigsh``
(ARPACK/Lanczos) builds a Krylov subspace and converges from good
spectral information with an EXPLICIT tolerance — the failure mode
becomes "slower", never "fails silently at a fixed count".

**Worked example — star K1,4.** Spectrum: ``{2, 0, 0, 0, -2}``;
``lambda_1 = 2``. Eigenvector (unit L2 norm): center ``1/sqrt(2) ~
0.7071``, leaves ``1/sqrt(8) ~ 0.3536`` each (check the eigen
equation: center row: 4 x 0.3536 = 1.4142 = 2 x 0.7071; norm:
0.5 + 4 x 0.125 = 1.0). Contract: unit L2 norm, sign flipped so the
dominant node is positive — bit-compatible with Onager/networkx.
(Correction 2026-09-23: filed version said center 2/sqrt(5),
leaves 1/sqrt(5) — satisfies the eigen equation but NOT unit norm;
the test pins the corrected values.)

**Pitfalls (shared with every eigenvector implementation).**
- Disconnected graph: only the dominant component's eigenvector is
  returned; other components score ~0.
- Bipartite graphs: ``+lambda_1`` and ``-lambda_1`` are both present —
  the principal eigenvector is well-defined, but power iteration
  oscillates between the two (a classic divergence cause); Lanczos is
  immune.

## 3. Incumbent state

- ``onager_eigenvector``: healthy live (0.5 s, 2.0 s budget leg),
  unit-norm + sign-flipped (networkx contract).
- Known failure class: fixed 100-iteration power loop on
  small/slow-mixing projections (recorded 2026-08-14 era).

## 4. Design

- Symmetric weighted CSR (same build as the Katz child);
  ``eigsh(A, k=1)`` with explicit tol; L2-normalise + sign-flip the
  dominant node positive.
- Dry-run default; ``--apply`` UPSERTs under ``eigenvector_centrality``.
- ROUTING: eigenvector stays ONAGER_DEFAULT (healthy); this is the
  robust fallback + opt-in lane, not a replacement.

## 5. Acceptance

1. Live agreement with Onager where it converges: r ~ 1.0, unit-norm.
2. Toy dense ``numpy.linalg.eigh`` oracle exact match (to tolerance);
   the star example above is the hand oracle.
3. Robustness case: a graph where Onager's power iteration fails and
   eigsh converges — reproduce in-repo if constructible, else cite the
   recorded case in the test comment.
4. ``tests/test_scipy_bridge.py`` additions green.

## 6. Non-goals / risks

- No default-lane flip.
- Bipartite degeneracy documented above, not "fixed" (same ambiguity
  Onager has).

Parent: `doc/improvements/archive/graph/scipy_graph_bridge.md`; lane
`helpers/graph/scipy_bridge.py`.
