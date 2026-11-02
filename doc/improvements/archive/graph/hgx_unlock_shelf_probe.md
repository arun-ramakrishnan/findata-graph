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

## Re-probe 2026-11-02 (live store 6.8× the baseline)

Trigger 2 (">2× edges on any of the five measured families") FIRED:
the live store grew from 1,708 hyperedges (2026-10-19 post-prune,
`hyperedge_density_dq.md` §7 S1) to 11,693 hyperedges / 51,554
incidences via the supply, subsidiary, group, index and merger/
demerger arcs. This section records the 2026-11-02 re-probe; the
2026-10-28 sections above are the historical baseline, not rewritten.

Store at re-probe: 11,693 hyperedges / 51,554 incidences / mean
density 4.41; k=1 7,845, k=2 1,867, k≥3 1,979 (16.9%). New families
vs baseline: supply 8,733 (k≥3 620), subsidiary 724 (382), group 506
(440), index 57 (57), merger 3 (1), demerger 2 (0). Event is 1,099
edges, all k=2 — the "participant data" reopen condition never
fired. 36 edition and 35 event rows are duplicate-label duplicates
(same member set) collapsing under the HGX member-tuple contract
(edition 225 raw → 189 unique).

Probe protocol: `ha.load_incidence_arrow()` over all edge types,
dedupe by sorted member tuple, guard max_k ≤ 60 and k≥2 edges ≤ 300
(the giant-k families — country 850, index 752, group 230,
subsidiary 227, supply 222, sector 99, theme 89 — stay out of scope
at full scale this pass); `Hypergraph(edge_list=...)` exactly as
`hyper_communities.py:140`. All lanes together ran ~2 min.

### Lane 1 — motifs census: NO-GO re-confirmed

`compute_motifs(order=3, runs_config_model=10, seed=42)` over the
five measurable families — edition (188 k≥3), industry (86),
sub_sector (80), org_membership (29), merger (1):
n_nonzero_delta = 0 in EVERY family; only the trivial within-edge
triple is observed. The visibility defect
(`get_edges(size=order, up_to=True)`) is unchanged in 1.8.0. NO-GO
stands; reopen trigger unchanged (needs a census that sees the full
incidence or a deliberately clique-expanded one).

### Lane 2 — reducibility: numbers moved, verdict unchanged

| family | layer 3 | layer 4 | layer 5 | layer 6 |
|---|---|---|---|---|
| edition | 0.5486 | 0.4006 | 0.080 | 0.129 |
| industry | 0.0 | 0.0 | 0.0002 | 0.067 |
| sub_sector | 0.0 | 0.0 | 0.0002 | 0.040 |
| org_membership | 0.0001 | 0.0 | 0.0 | 0.0 |
| merger | 0.0 (L2 0.059) | — | — | — |

Edition remains the only family with low-order-explainable structure
(L3 0.637→0.5486, L4 0.485→0.4006 vs 2026-10-28);
industry/sub_sector/org_membership remain ≈0.0 at layers 2–6 —
genuinely higher-order, so the s=2 default of the landed
`s_betweenness`/`s_closeness` lanes stays validated. Large layers
still report trivial 1.0 (single-edge layers). No production lane
justified; next re-run on major store growth.

### Lane 3 — multi-order Laplacian: FIRST MEASURED

`laplacian_matrices_all_orders()` builds per-order Laplacians to
max_k for every measurable family (edition 34 orders, industry 51,
sub_sector 50, org_membership 31, merger 2). Commutativity: the
installed `are_commuting()` is broken on this scipy — it calls
`csr_array.any()`, absent on scipy csr_array (linalg.py:475); the
probe inlines the identical nnz-based check. Results: sub_sector
COMMUTES across orders 1–50; edition, industry, org_membership do
NOT commute; merger/jv/demerger trivially (order ≤ 2).

Reading: on sub_sector a single multiorder Laplacian has an
order-decomposition-independent spectral meaning; on edition/
industry/org_membership the mixed-order operator is basis-dependent,
so any spectral consumer there must fix an order weighting first.
Still no named spectral-feature consumer → stays consumer-gated,
now with a measured baseline. Note: `compute_multiorder_laplacian`
raises a divide-by-zero RuntimeWarning when degree-weighting empty
orders — pin if this lane is ever wired.

### Lane 4 — s-walk shortest paths: BLOCKED UPSTREAM (code bug)

`calc_HO_shortest_paths()` fails on every measurable family with
"ValueError: setting an array element with a sequence" raised in
`dict_to_df` (measures/shortest_paths.py:262) — HGX 1.8.0's
DataFrame construction is broken on this py3.14/numpy stack (ragged
path-length dicts). Not a data problem; the lane cannot produce
results on this venv regardless of graph. Stays consumer-gated with
the added blocker "upstream dict_to_df fix".

### Lane 5 — RW density: measurable only on largest components

`random_walk_density` over `subhypergraph_largest_component()` (the
repo idiom, `hyper_centralities.py:181`):

| family | LC nodes / total | final max density | L2 first→last |
|---|---|---|---|
| edition | 771 / 771 | 0.006035 | 0.00130→0.00224 |
| industry | 63 / 1004 | 0.024935 | 0.01587→0.01701 |
| sub_sector | 51 / 999 | 0.019608 | 0.01961→0.01961 |
| org_membership | 41 / 277 | 0.047194 | 0.02439→0.02693 |
| jv / merger / demerger | 3 / 3 / 2 | — | — |

Structural finding: edition is the only family whose k≥2 graph is
one dominant component; industry/sub_sector/org_membership are
fragmented (LC holds 6%/5%/15% of nodes) — a diffusion lens there is
dominated by component structure before any dynamics. RW density
stays gated on an information-diffusion question; new pre-condition:
a family that is both connected and interesting.

### Lane 6 — community lanes: core-periphery measured; two blocked

`core_periphery(hg, greedy_start=False, N_ITER=200, seed=42)` —
first measurement: top-coreness runs ≈1.0 (edition 1.0/0.999/0.998…,
industry 1.0/0.999/0.999…, demerger 1.0/0.94/0.932…) i.e. a smooth
core→periphery gradient rather than a two-block split. Cost is real:
49.7 s @771 nodes (edition), 22.9 s @984 (industry) at N_ITER=200 —
scaling to the giant-k families is impractical as-is.

- HySC + HypergraphMT: ImportError "HySC requires scikit-learn" —
  the venv lacks the hypergraphx[ml] extra (sklearn absent). Until
  sklearn is added (a dependency decision, not a probe), both are
  environment-blocked.
- hyperlink_communities: ReadwriteError "Failed to load pickle
  'None.hlcd'" — upstream bug (save_distances=None still consults a
  pickle path). Environment-independent; blocked upstream.

### Provenance of the superseded readings

The stale numbers this re-probe supersedes trace to two records: the
density table (1,708 / 478 k≥3 / mean 5.93) was written 2026-10-19
by `archive/graph/hyperedge_density_dq.md` §7 S1 and restated into
pending.md "HGX D10"; the shelf verdicts and the "trigger NOT met
(1.00×)" note were written 2026-10-28 in this file (completed.md
entry #451) and restated into pending.md "HGX unlock shelf". Both were
correct when written; neither was re-checked after the
supply/subsidiary/group/index arcs grew the store 6.8×. Correction
pattern: dated RESTATEMENT blocks in pending.md (density table +
shelf entry) plus this dated addendum; archived proposals left
as-written (historical records).
=== LANE 6 POST-INSTALL RESULT ===
(2026-11-02) sklearn installed per user go (m00250); HySC + HypergraphMT load OK (sklearn present); hyperlink_communities still BLOCKED upstream (None.hlcd pickle, HGX 1.8.0 bug — not installable); core_periphery cost unchanged (~49.7s @771, N_ITER=200). Lane 6 blocker 1 RESOLVED; blockers 2/3 unchanged.
=== PROPOSAL STATE ===
Proposal hgx_lanes_3_5_wire.md (lanes 3+5 wire, advisory production) intact; shelf +135 + pending 359/414 intact; no commit/stg.
FIX APPLIED: .any() -> np.any() equiv at installed linalg.py (HGX 1.8.0); Lane 3 spectral wire + Lane 5 RW annotation both done; proposal finalized; shelf append next; gate deferred to user end.

=== LANE 4 BLOCKER RECLASSIFIED ===
(2026-11-02) The Lane 4 section above ("BLOCKED UPSTREAM (code bug)")
stays as the historical record; the blocker itself moves from
"upstream bug" to "we own the implementation": the upstream dict_to_df
fix is a dead end twice over (HGX 1.8.0 IS the newest release, and even
a patched dict_to_df still allocates an n x n dense np.empty — ~2.7 GB
at 18,233 entities — with raw labels as matrix indices). Unit-cost
trail distances go repo-native: scipy bipartite BFS over h_incidence
(103,108 arcs, MB-scale — never the 2.45M-dyad clique projection);
bipartite distance/2 = trail hops, exact. Measured 5.1 ms/source,
~94 s full map. Proposal:
doc/improvements/archive/graph/hyper_trail_bipartite_bfs_lane.md
(archived 2026-11-02 as completed.md #473; filed 2026-11-02). The calc_HO_shortest_paths call site retires when that
proposal's S3 lands; HGX keeps lanes 3/5/6.
