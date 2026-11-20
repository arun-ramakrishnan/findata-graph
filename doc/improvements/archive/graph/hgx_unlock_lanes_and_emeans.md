---
title: "HGX unlock shelf — community lanes (HySC, core-periphery, HypergraphMT), multi-order spectral consumer, e-means wiring"
status: executed
filed: "2026-11-20"
executed: "2026-11-20"
completed_md: "549"
area: "helpers/graph + pyproject lens extra + .venv (faiss-cpu, scikit-learn)"
---

# HGX unlock shelf — community lanes + e-means wiring

**Date:** 2026-11-20 · **Status:** EXECUTED · **Area:** `helpers/graph`,
pyproject `lens` extra, `.venv` (faiss-cpu 1.16.0, scikit-learn 1.9.1)

## Objective

Close out pending HGX unlock-shelf items #2 (core-periphery), #3
(HypergraphMT), #6 (reducibility adoption), #8 (multi-order spectral
consumer) and wire e-means (faiss contrib) into two lanes, so the
higher-order community / spectral stack runs end-to-end without
optional-dep gaps.

## What shipped

### Unblocks
- `uv pip install 'hypergraphx[ml]'` → scikit-learn 1.9.1 into `.venv`
  (HySC / HypergraphMT importable).
- `uv pip install 'faiss-cpu>=1.16.0'` — already declared in the
  pyproject `lens` extra; `faiss.contrib.e_means.NumpyEmeans` verified.

### #2 — core_periphery lane
- New helpers/graph/hyper_core_periphery.py.
- core_periphery() returns per-node coreness in [0,1]; lane emits
  {coreness, seed, n_iter} per node, reports top-10 + count >= 0.5.
- CLI: --sources/--seed/--n-iter/--greedy-start/--allow-giant/--apply;
  dry-run default; persists metric core_periphery.
- Smoke (sector, n_iter=50): 1225 nodes in 14.8s; top coreness ~1.0;
  781/1225 >= 0.5.

### #3 — HypergraphMT lane (opt-in slow)
- New helpers/graph/hyper_hypergraphmt.py.
- HypergraphMT(n_realizations=..., verbose=False).fit(...) returns
  (u, w, maxL); memberships one-hot by argmax.
- CLI: --sources/--k/--seed/--realizations/--allow-giant/--apply;
  default realizations=1 (~13s) since full quality needs >=10 (~100s+);
  dry-run default; persists metric hypergraphmt_community.
- Smoke (sector, K=4, 1 realization): 1225 nodes in 13.3s, 4 blocks
  (919/244/49/13) — mirrors the HySC partition.

### HySC lane
- New helpers/graph/hyper_hysc.py — HySC(seed=seed).fit(hg, K=k) →
  (N,K) one-hot u; metric hysc_community.
- Smoke (sector, K=4): blocks 919/244/49/13 in 3.1s.

All lanes follow the hyper_communities.py template: lazy _require_hgx,
_exclude_degenerate guard (max_frac=0.25), DEFAULT_SOURCES=
sector,theme,industry, dry-run default, --apply persists via
helpers.graph.algorithms.write_analytics.

### #6 — reducibility one-shot
- One-shot measurement over all sources (sector/theme/industry/sub_sector):
  245 hyperedges, 1226 nodes, 50 distinct edge sizes.
- eta = 0.1419, representative layers (7, 26, 27, 30, 102).
- Layer-wise: orders >= 7 fully reducible (1.0) except 6 (0.20), 5 (0.24),
  4 (0.10), 3 (0.06), 2 (0.08); layer 102 = 0.0 (max layer, must be
  representative).
- Re-run policy: on >2x store growth; no production lane (one query, no
  recurring consumer).

### #8 — multi-order Laplacian spectral consumer
- New helpers/graph/hyper_spectral_cluster.py.
- Per chosen order: top-m eigenvectors (scipy.linalg.eigh), L2-normalize,
  cluster; reports ARI vs hand-authored sector taxonomy as QA target.
- CLI: --sources/--orders/--m/--k/--seed/--use-emeans/--apply;
  dry-run default; persists spectral_community_order<N>.
- Auto-selects e-means when faiss is importable, else sklearn k-means.
- Smoke order 3 m=4 k=8: e-means 0.61s (k-means 2.6s), ARI ~0.

### #6 adoption into #8 (2026-11-20, same arc)
- `--orders` now defaults to `reducible` — the entropy-representative
  layer set from #6, computed LIVE per loaded family (reducibility
  0.15s), sizes mapped to laplacian orders (size, else size-1). Also
  accepts `all` and comma lists.
- Smoke (sector/theme/industry): eta=0.0582, sizes [8,25,26,47,86,102]
  -> orders [8,25,26,47,86,101], ~6.5s total vs ~101 orders. ARI vs
  sector stays near zero (max 0.0418 at order 101, the mostly-isolated
  max layer) — spectral lane remains a QA instrument, not a taxonomy
  rediscovery.

### e-means wiring (2 lanes)
- helpers/graph/semantic_clusters.py — existing lane, lazy faiss import
  now satisfied; NumpyEmeans recipe pinned (k=42, nepochs=5, seed=42,
  spherical=False, batch_law="throughput").
- helpers/graph/hyper_spectral_cluster.py — new lane; auto-prefers
  e-means when faiss importable, falls back to sklearn KMeans.

## Parked (per operator)
- #4 hyperlink_communities — upstream pickle bug (None.hlcd
  ReadwriteError; exception-type mismatch in readwrite/io_pickle), not
  fixable in-repo.
- #5 motifs census — NO-GO (visibility defect: census only sees edges of
  size <= order).
- #7 s-walk shortest paths — reclassified to repo-native bipartite BFS
  (helpers/graph/hyper_trails.py).
