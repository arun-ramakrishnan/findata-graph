---
title: "PageRank graph enhancements — projection, weights, temporal backfill"
status: executed
filed: "2026-09-22"
executed: "2026-09-22"
completed_md: "270"
area: "helpers/graph"
---

# PageRank graph enhancements — projection, weights, temporal backfill

## 1. TL;DR

The stored PageRank runs over a single edge type by default —
`belongs_to`, 184 edges — and the full 18,291-edge graph it could run
over is 60% membership structure with 84% unit weights and no temporal
validity. This proposal upgrades the PageRank input in three slices that
need **no new data sources** (S1 economic projection, S2 weight
carry-through, S3 temporal backfill for as-of ranks), scopes the
new-source filings for the thin economic tail (S4), and keeps the
personalized-pagerank wrap parked on its Onager bug (S5 watch).

## 2. Background — census and current shape

Census 2026-09-22 over `fin.graph_edges` (18,291 edges, 21 types):

| slice | edges | share | weights |
|---|---|---|---|
| membership/index (listed_on_index 6,615; listed_in 930; part_of 1,181; has_company 1,181; belongs_to 184) | 10,891 | 60% | all unit |
| news/research (cited_in 1,933; co_mentioned_in 1,329) | 3,262 | 18% | all unit — counts not carried |
| competition (competes_with) | 3,578 | 20% | 0.4–1.0 (only real weights) |
| economic tail (invested_in 715, jv_with 73, subsidiary_of 68, acquired 42, same_group 37, semantic_peer 30, regulated_by 17, supplier_to 8, approved_by 7, rated_by 3, customer_of 1) | ~960 | 5% | invested_in 1.0–3.0 |

- Unit-weight edges: 15,333/18,291 (84%). `valid_from`: 100% missing
  on every type except `invested_in` (0 missing — the in-tree pattern
  to copy).
- `query.pagerank()` default is `edge_label="BelongsTo"` → a single
  184-edge type; PageRank over membership stars measures index
  membership, not economic centrality.
- Onager ignores weights entirely (house contract §5.5: "weights
  ignored by all onager metrics"; `onager_ctr_pagerank` takes a
  3-column table, no weight param). The igraph second engine
  (`igraph_bridge.py`, D14) does support weighted PageRank.

## 3. Slices

- S1 **Economic projection** — first-class multi-type projection for
  PageRank: competes_with + cited_in + co_mentioned_in + invested_in +
  ownership/supply-chain types, excluding (or weight-damped) membership
  stars. Mirrors the `DEFAULT_PREDICTION_EDGE_TYPES` philosophy from
  link prediction (non-membership edges to avoid trivial
  sector-co-occurrence centrality). Ships as a named projection in
  `onager.py`/`algorithms.py` with the recompute dispatch and stored
  metric switched to it; the belongs_to default stays available as a
  legacy label for comparison.
- S2 **Weight carry-through** — persist cited_in/co_mentioned_in
  counts (already computed at derive time from the notes corpus) as
  edge weights, and route PageRank through the igraph bridge lane with
  weights (Onager cannot take them). competes_with weights already
  exist and flow for free. Contract note: weighted vs unweighted
  outputs are different metrics — persist under a distinct metric name
  and keep both during evaluation.
- S3 **Temporal backfill → as-of PageRank** — backfill
  `listed_on_index` valid_from from the 117 index-edition notes
  (invested_in is the in-tree pattern), then add an as-of projection:
  "top-ranked companies as of date D" over the edges valid at D.
- S4 **New-source filings (scoped, separate arcs when sources are
  chosen)** — the economic tail is ~5% of edges and 1.3% for
  supply-chain alone (`customer_of` = 1). Ranked by PageRank
  leverage: (a) ownership/structure — shareholding-pattern filings
  and group trees (`subsidiary_of` 68 / `same_group` 37 / `acquired`
  42 across 6,203 companies). Sources probed 2026-09-22:
  **NSE** publishes every filing as XBRL on the open archives host
  (`nsearchives.nseindia.com/corporate/xbrl/SHP_*_WEB.xml`), with an
  incremental RSS index (`nsearchives.nseindia.com/content/RSS/
  Shareholding_Pattern.xml`, verified 200 application/xml, same-day
  items) whose entries already carry `PR_AND_PRGRP` / `PUBLIC_VAL`
  percentages, as-of and revision dates; the full XBRL holds the
  category breakdown (promoter, promoter group, public, institutional,
  custodian, employee-trust). Sample XBRL parsed (SHP V1.2, joint
  BSE+NSE taxonomy `in-bse-shp`): 119 holder rows per filing, each
  with `NameOfTheShareholder`, `PermanentAccountNumberOfShareholder`
  (PAN — a durable holder identity for dedup across companies),
  share counts, `ShareholdingAsAPercentageOfTotalNumberOfShares`,
  voting-rights %, pledge/encumbrance counts, and per-category XBRL
  contexts with period start/end — i.e. temporal validity arrives
  native. Main-site pages need a browser-like session (root 403s
  plain clients; filing page 200 with UA+headers).
  Mapping: promoter/promoter-group stake → promoter→company ownership
  edges (weight = stake %), shared promoter group → `same_group`;
  institutional holders ≥ threshold → institution nodes.
  **BSE** corporate group repository (reachable 200; exact endpoint
  pinned at execution) explicitly lists subsidiary/associate/promoter
  group membership — a direct `same_group` source. (b) supply chain —
  related-party/segment disclosures from annual reports
  (`supplier_to` 8 / `customer_of` 1); (c) ratings/coverage —
  `rated_by` 3 / `regulated_by` 17 / `approved_by` 7 as authoritative
  hubs. Each source becomes its own proposal with its own provenance
  stamps; none blocks S1–S3.
- S5 **Watch — personalized pagerank** (deferred N5-6, re-probed
  2026-09-22): personalization column still ignored, restart still
  hardcoded to `node_id 1`. Data-ready otherwise — seeds come free
  from existing membership (sector/index/portfolio). Wrap when an
  Onager build honours the column; the A/B probe recipe in pending.md
  N5-6 becomes the contract test.

## 4. Acceptance criteria

1. S1: PageRank over the economic projection differs materially from
   the membership graph (report top-10 side by side on the live db);
   contract tests with hand-computed fixture values per house pattern;
   recompute-graph persists the new metric; graph_design.md §5.5
   updated.
2. S2: weighted igraph lane lands with a distinct persisted metric
   name; competes_with/cited_in/co_mentioned_in weights verified
   end-to-end (derive → edge table → rank).
3. S3: `listed_on_index` valid_from backfilled from edition notes
   (coverage 0% → ≥95%); an as-of query returns a stable ranking for a
   past date.
4. Targeted tests green (`test_onager_capabilities.py`,
   `test_integration_graph_algorithms.py`, dispatcher tests);
   `make static-checks` clean.

## 5. Non-goals

- No personalized-pagerank wrap while the Onager bug stands (S5 stays a
  watch item).
- No new ingestion pipelines inside this proposal — S4 sources get
  their own filings with provenance.
- No changes to non-pagerank centralities in S1 (applying the
  projection to them is a follow-on evaluation).
- No duckpgq return; Onager stays primary, igraph the bridge lane (D14).

## 6. References

- pending.md N5-5 (HNSW trial — why brute KNN stays) and N5-6
  (personalized pagerank re-probe).
- `helpers/graph/onager.py` (`DEFAULT_PREDICTION_EDGE_TYPES`,
  `_materialize_from_db`, onager.py:836 personalized note);
  `helpers/graph/algorithms.py:_run_pagerank`;
  `helpers/graph/query.py:pagerank` (belongs_to default).
- graph_design.md §5.5 (weights-ignored contract), D14 (igraph lane).
- Sibling proposal: `note_knn_distance_ranking.md` (filed 2026-09-22).

## 6. Implementation addendum (2026-09-22, executed)

Executed against the live 56,014-edge graph (post owner_ingest /
related_party_groups_vigil / bse_shareholding_rss data arcs — the graph
tripled since filing).

**S1 (economic projection)** — `onager.ECONOMIC_EDGE_TYPES` (14
non-membership types); `query.pagerank` default label `Economic`; the
recompute CLI maps its default to the projection for both `--all` and the
single command; persisted `pagerank` metric recomputed + verified (top-5
persisted = Reliance, M&M, JSW Energy, Wipro, Infosys). Acceptance
side-by-side: **0/10 top-10 overlap** vs the BelongsTo membership view.

**S2 (weight carry-through) — RE-SCOPED, no igraph**: the filed premise
"Onager cannot take weights" was wrong — `onager_ctr_pagerank` consumes
the materialised weight column (verified: 9:1 weighted star -> 0.371 vs
0.075). The igraph bridge (`igraph_bridge.py`, D14) was retired with the
HGX lane and is NOT resurrected. Instead: `onager_pagerank(weighted=...)`
switch; the plain `pagerank` metric passes unit weights (contract
preserved), the distinct `pagerank_weighted` metric consumes carried
weights — cited_in backfills `n_quotes + 1` at derive time
(`derive_cited_in.backfill_weights`, live range 1.0-44.0), invested_in
carries stakes, competes_with carries similarity. co_mentioned_in stays
unit-weighted: no per-pair count survives the store (properties hold only
the latest edition; UNIQUE collapses cross-edition history) — derive-time
gap, revisit with the notes-corpus surface. Live evaluation: weighted vs
unweighted top-10 overlap **10/10** (one rank swap) — weights refine, do
not distort. Scale heterogeneity (stakes <=100 vs similarity <=0.914)
carried raw.

**S3 (temporal backfill)** — `listed_on_index.valid_from` backfilled from
`properties.as_of` (6,615/6,615; wired into
`derive_indices.backfill_validity` — simpler than the filed
edition-notes route since the CSV snapshot date already sits in
properties). As-of filter: `_where_inline(as_of=D)` validity window
(NULL valid_from = always-valid), threaded through materialisation +
batch signature, surfaced as `pagerank(as_of=...)` and
`--as-of`. Live check: invested_in valid 799 today vs 91 at 2025-06-30;
Economic top-8 stable across D (structural edges undated) — current-state
reading, honest.

S4 stays scoped for future source arcs; S5 (personalized pagerank)
stays parked on the Onager bug.
