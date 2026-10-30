---
title: "HGX unlock-shelf probe — motifs census + reducibility measured; four lanes stay trigger-gated"
status: deferred
filed: "2026-10-28"
executed: "2026-10-28"
completed_md: "451"
area: graph
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# HGX unlock-shelf probe

**Date:** 2026-10-28 · **Status:** DEFERRED 2026-10-28 (completed.md
entry 451) — probe executed in-session, verdicts recorded, no production
wiring. This proposal exists so the probe is NOT re-derived: the
measured answers and the sharpened reopen triggers live here and in
`pending.md` ("HGX unlock shelf").

## Summary

The D10 archive §4 "unlock shelf" records six trigger-gated
hypergraphx (HGX) 1.8.0 capabilities that were never wired because each
needed a named consumer. This arc verified the shelf inventory against
the installed venv, probed the two items whose triggers had arguably
fired (motifs census, reducibility), measured real verdicts, and
re-gated everything with sharpened triggers. Result: **no lane is
wired**; two items are measured-and-closed, four stay consumer-gated.

## Inventory verification (entry-point corrections)

All six capabilities verified present in hypergraphx 1.8.0. Three
entry points differ from the D10 archive §2/§4 table — use these:

| capability | D10 archive says | actual (verified import) |
|---|---|---|
| directed motifs | `motifs.compute_directed_motifs` | `hypergraphx.motifs.directed_motifs.compute_directed_motifs` |
| s-walk shortest paths | `measures.s_centralities.calc_HO_shortest_paths` | `hypergraphx.measures.shortest_paths.{calc_HO_shortest_paths, calc_prop_true_dyad_paths_per_spl, calc_sizes_redundancies_of_shortest_paths}` |
| core-periphery | `communities.core_periphery` | `hypergraphx.communities.core_periphery.model.core_periphery` |

`hypergraphx.prediction` remains ABSENT (re-verified) — D10 §3's
first-party lane decision stands, already shipped as
`helpers/graph/hyper_predict.py` (#429).

## Probe 1 — motifs census: NO-GO on current data

`compute_motifs(h, order=3, runs_config_model=10, seed=42)` over every
k≥3 family of the live star store (221–972-node pools, 1–85 edges per
family) returns ONLY the trivial within-edge triple
`((1, 2, 3),)`:

| family | edges (k≥3) | nodes | nontrivial motifs | trivial count |
|---|---|---|---|---|
| edition | 221 | 765 | 0 | 0 |
| industry | 85 | 949 | 0 | 12 |
| sub_sector | 79 | 947 | 0 | 9 |
| org_membership | 29 | 271 | 0 | 7 |
| group | 4 | 19 | 0 | 2 |
| sector | 41 | 1,209 | 0 | 1 |
| country | 7 | 913 | 0 | 1 |
| theme | 12 | 270 | 0 | 0 |

`norm_delta` is 0.0 for every motif in every family — no enrichment
against the configuration model. **Mechanism (CORRECTED 2026-10-30,
hgx_over_dyads S5 — the original "node pools 1–2 orders larger than
edge counts" claim was wrong about the store and would have misled the
next probe):** `compute_motifs` begins
`edges = hypergraph.get_edges(size=order, up_to=True)`
(`hypergraphx/motifs/motifs.py`) — the census only ever SEES edges of
size ≤ order. Order 3 therefore measured 1,191 of 1,711 live edges
(69.6%) but only 2,420 of 10,083 incidences (**24.0% coverage — 76% of
the incidence mass invisible**), and the visible set was 93.1%
event-family (1,109/1,191), which is 100% k=2 clique-expanded dyads —
precisely the structure a motif census exists to distinguish itself
from. Hidden entirely by family at k>3: edition 98%, sector 95%, theme
100%, country 81%, sub_sector 81%, industry 80%, org_membership 76%.
Cross-edge 3-node overlap is in fact COMMON live (trigger 1 below is
MET) — the census could not have seen it.

**Verdict:** the census lane still reports nothing but trivial
structure at order 3. NO-GO stands; not wired — but the blocker is
census VISIBILITY (`get_edges(size=order, up_to=True)`), not node-pool
sparsity. Two API defects worth pinning if ever re-probed: the keyword
is `runs_config_model` (not `runs`), and `observed`/`config_model`/
`norm_delta` are lists of `(motif_tuple, count)` pairs, not the dicts
the docstring promises.

## Probe 2 — reducibility: measured; validates s-walk lanes

`layer_reducibility(h)` per family (k≥2 edges):

| family | edges | nodes | layer 2 | layer 3 | layer 4 | layer 5 | layer 6 |
|---|---|---|---|---|---|---|---|
| edition | 221 | 765 | — | **0.637** | **0.485** | 0.085 | 0.144 |
| industry | 97 | 972 | 0.0 | 0.0 | 0.0 | 0.0 | 0.04 |
| sub_sector | 91 | 971 | 0.0 | 0.0 | 0.0 | 0.0 | 0.05 |
| sector | 42 | 1,211 | 0.001 | 0.0 | 0.0 | — | 0.0 |
| org_membership | 30 | 273 | 0.001 | 0.0 | 0.0 | 0.0 | 0.0 |

(Large layers report trivial 1.0 — a layer holding a single edge is
reducible by definition; ignore.)

**Reading:** editions carry real low-order-explainable structure
(0.64 of the information survives collapsing to order 3), while
industry/sub_sector/sector/org_membership are irreducible at low
orders — genuinely higher-order. This **validates the s=2 default** of
the landed `s_betweenness`/`s_closeness` lanes
(`helpers/graph/hyper_centralities.py`): on the non-edition families
the s-walk lens is the right one, and it flags editions as the family
where dyadic/lower-order explanations partially suffice.

**Verdict:** one-shot QA measurement, recorded here — no production
lane justified (a single query with no recurring consumer).

## Items 3–6: re-gated, unchanged

- **Multi-order Laplacian** (`compute_multiorder_laplacian`,
  `are_commuting`) — needs a named spectral-feature consumer
  (entity-clustering QA is the standing candidate; none exists).
- **s-walk shortest paths** (`calc_HO_shortest_paths` et al.) — needs
  the first UI/API path-explanation ask that traverses hyperedges.
  Directed B/F-hyperpath (Gallo) remains out of HGX; the directed lane
  stays plain-SQL BFS (`graph_design.md` D9).
- **RW density** (`random_walk_density`, sig `(HG, s: ndarray,
  time) -> list`) — needs an information-diffusion analysis question
  (edition/news spread). `RW_stationary_state` itself is already
  landed as `ho_pagerank` (S12/S14).
- **Community lanes 2–4** (`core_periphery.model.core_periphery`,
  `hyperlink_comm.hyperlink_communities`, `hypergraph_mt.model`,
  `hy_sc.model`; hy_mmsbm landed in `hyper_communities.py`) — needs a
  hy-MMSBM block-disagreement QA or second-opinion metric-validation
  ask.

## Reopen triggers (do not re-probe without one)

1. **Motifs census** — a family gains dense cross-edge node-overlap at
   motif order (event family once participant data arrives; merger
   churn concentrating same_group membership). Quick check before any
   probe (table names corrected 2026-10-30 — the live names are
   `hyper_incidences`/`hyper_edges`, not `h_incidence`/`h_edge`):
   `SELECT COUNT(*) FROM (SELECT hi.entity_name, COUNT(DISTINCT
   hi.edge_id) d FROM hyper_incidences hi JOIN hyper_edges he ON
   he.id=hi.edge_id WHERE he.edge_type='<f>' GROUP BY hi.entity_name
   HAVING d>=2)` — motif signal needs many entities in ≥2 edges of the
   same family. **STATUS 2026-10-30 (hgx_over_dyads §2.5): this trigger
   is MET live** — edition 714 entities in ≥2 edges (455 in ≥3), theme
   67/19, event 284/111, and on the promoted families listed_on_index
   753/744 and same_group 903/320. The 2026-10-28 probe recorded 0,
   i.e. it measured a stale/partial store. What still blocks a
   production census is the visibility defect recorded at Probe 1 (the
   order-3 census sees 24.0% of incidence mass), NOT absent overlap.
2. **Reducibility** — re-run per-family on major store growth (>2×
   edges); numbers recorded above are the 2026-10-28 baseline.
   Re-checked 2026-10-30: NOT met — all five families at 1.00×, and
   reducibility reproduces the table above exactly (edition 0.637 at
   layer 3, 0.485 at layer 4; industry/sub_sector/sector/
   org_membership ≈ 0.0 at layers 2–6).
3. Items 3–6 — unchanged consumer-gated triggers (see above).

## Non-goals

- No `helpers/graph/hyper_motifs.py` or reducibility lane is created.
- No `graph_analytics` metric is written (existing metrics
  `ho_pagerank`/`s_betweenness`/`s_closeness`/`eigen_*`/
  `hypermmsbm_community`/`hyper_link_prediction` unchanged).
- The pending.md "HGX unlock shelf" entry (added 2026-10-28) carries
  the same verdicts in short form; this proposal is the detailed
  record.
