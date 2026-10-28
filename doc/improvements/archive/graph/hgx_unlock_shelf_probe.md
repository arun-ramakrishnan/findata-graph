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
against the configuration model. **Mechanism:** the store's node pools
are 1–2 orders of magnitude larger than the edge counts, so two
hyperedges almost never share 3+ nodes — the multi-edge intersection
configurations that constitute higher-order motifs cannot occur. The
D10 trigger ("motif counts are meaningful only where k≥3 mass exists")
was necessary but not sufficient: k≥3 *edge* mass (479 edges, 28%) is
present, but k≥3 *cross-edge overlap* mass is absent.

**Verdict:** the census lane would report nothing but trivial
structure. NO-GO; not wired.

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
   probe: `SELECT COUNT(*) FROM (SELECT hi.entity_name, COUNT(DISTINCT
   hi.edge_id) d FROM h_incidence hi JOIN h_edge he ON he.id=hi.edge_id
   WHERE he.edge_type='<f>' GROUP BY hi.entity_name HAVING d>=2)` —
   motif signal needs many entities in ≥2 edges of the same family.
2. **Reducibility** — re-run per-family on major store growth (>2×
   edges); numbers recorded above are the 2026-10-28 baseline.
3. Items 3–6 — unchanged consumer-gated triggers (see above).

## Non-goals

- No `helpers/graph/hyper_motifs.py` or reducibility lane is created.
- No `graph_analytics` metric is written (existing metrics
  `ho_pagerank`/`s_betweenness`/`s_closeness`/`eigen_*`/
  `hypermmsbm_community`/`hyper_link_prediction` unchanged).
- The pending.md "HGX unlock shelf" entry (added 2026-10-28) carries
  the same verdicts in short form; this proposal is the detailed
  record.
