---
title: "Graph lane unlocks post-#456–#459 — matching quirk fix, group convergence, index centralities, backbone resilience + the unmet-trigger register"
status: executed
filed: "2026-10-31"
executed: "2026-10-31"
completed_md: "460"
area: "helpers/graph (scipy_matching.py, derive_hyperedges.py, hyper_centralities.py, l1_mst.py) + doc/improvements/pending.md"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Graph lane unlocks post-#456–#459 — matching quirk fix, group convergence, index centralities, backbone resilience + the unmet-trigger register

**Date:** 2026-10-31 · **Status:** EXECUTED (#460) ·
**Area:** `helpers/graph/scipy_matching.py`, `helpers/graph/derive_hyperedges.py`,
`helpers/graph/hyper_centralities.py`, `helpers/graph/l1_mst.py`,
`doc/improvements/pending.md`

## 1. Motivation

A repo + `doc/local` unlock sweep the morning after the 2026-10-30/31
arcs landed (completed.md #456 graph_analytics integrity, #457
hgx_over_dyads, #458 MST wiring, #459 matching wiring, plus the
embedded-DB audit) found six items the new state makes executable,
four of them worth slices. This proposal records them as slices and
freezes the STILL-GATED register (§5) so the next sweep does not
re-derive it. None of those arcs is reopened; everything here is
downstream of what they made true.

## 2. Evidence (measured 2026-10-31, this box)

| # | Unlock | Evidence |
|---|---|---|
| U1 | matching lane's Hall print is meaningless | `assign-feasibility` prints `max matching 8733/6083` — `s3_feasibility`'s `max_matching_size` is `len()` of scipy's permutation array = column count (8,733), never the matched count (958 on the same graph); `scipy_matching.py:115`. The wiring arc (completed.md #459) deliberately left this parked; `make graph-matching` now makes the wrong number one command from every user |
| U2 | `group` family staleness is structural | completed.md #457 widened `group` to 498 filer-keyed RPT disclosure sets fed by an upstream that REWRITES (S9 counterparty resolution mutates source_refs/properties); `apply_hyperedges` is ADD/UPDATE-only for every non-event family, so a filer set whose (source_ref, relationship) disappears lingers forever. The event family has an S1 convergence pass; `group` (and the curated `$.group` sets) has none |
| U3 | `index`-family centralities run TODAY | smoke: `hyper_centralities.py --sources index` → 57 loaded / 49 kept (8 giants, exactly as recorded); `ho_pagerank` near-uniform (0.00256–0.00259 — membership broadly shared), `s_betweenness` differentiates (top 0.0695). Healthy per hgx_over_dyads §2.5. Default scope (`DEFAULT_SOURCES`) still excludes `index` |
| U4 | MST backbone has a registered consumer | T1's unlock row (`doc/local/evaluations/graph_layer.md:901`): "backbone viz/resilience consumer is concrete". completed.md #458 serves top-5 edges; the resilience question (which backbone ties are the ONLY tie between their components in the FULL graph) is uncomputed |
| U5 | motif census is library-blocked, not store-blocked | #451 record corrected (#457 S5): the blocker is `compute_motifs`' `get_edges(size=order, up_to=True)` visibility (24.0% incidence coverage). A SQL census over `hyper_incidences` sees 100% — and the store now holds its densest-ever overlap substrate (index 753/744, filer sets 903/320) |
| U6 | `index` community fit is evaluable | 49 edges / 762 nodes survive the giant guard; the honest protocol (broad cap/theme-neutral indexes only) is pre-designed in hgx_over_dyads §2.3 with the circularity trap quantified |

## 3. Design

Ordered by execution. S1 first (only correctness item), S2 (staleness
is structural), S3 (decision), S4 (consumer). S5 records the
available-not-recommended tail.

### S1 — fix `s3_feasibility`'s `max_matching_size` (correctness)

`scipy_matching.py:115` computes `len(perm)`; scipy 1.18.1's
`maximum_bipartite_matching(mat, perm_type="row")` returns an array of
length #columns, so `len()` is the column count, not the match size.
Fix: count assignments (`int((perm >= 0).sum())` — the same idiom
`s2_matching` already uses). The printed Hall line
(`max matching {len}/…`) becomes a real Hall check: matched vs the
deficient-side size. Pin with a test that plants a known Hall
collision (the existing `test_s3_hall_collision` asserts the collision
fires — extend it to assert the MATCHED count too, proving the print
is no longer the column count). No CLI signature changes.

### S2 — `group` convergence pass (staleness)

Mirror the event family's convergence: after the regroup, any stored
`group` hyperedge whose label is NOT in the collected set (curated
`$.group` ∪ RPT filer refs) is stale and drops, with the drop count
printed in the derive report — same shape as the event S1 pass and the
D7 sub_sector prune. Guard the report so a first-run against an old
store shows what WOULD drop (dry-run prints, `--apply` deletes). The
8 curated corporate-group sets participate on the same terms.

### S3 — `index` centralities: decide the slot

Run `hyper_centralities.py --sources sector,theme,industry,sub_sector,index`
vs the default scope, compare live (values, wall, any store-row
churn), and pick: opt-in forever (document the measured shape in
`ontology.md` §`index`) or a default slot in `recompute-hyper`. The
ho_pagerank flatness (U3) is the argument AGAINST a default slot;
s_betweenness differentiation is the argument for. Whatever the pick,
the s-walk lanes' degeneracy census goes in the doc note. No
`graph_analytics` write without the decision.

### S4 — backbone resilience report (T1's registered consumer)

The MSF is a forest, so every forest edge is critical BY CONSTRUCTION
— the honest resilience question is on the FULL undirected projection:
which backbone pairs are bridges of the full graph (removal disconnects
their component) vs backed by alternate paths. Implement as a
`--format resilience` mode on `l1_mst.py` (bridge census over the full
projection — union-find rollback or DFS bridges, both trivial at 33k
undirected edges — cross-referenced with the forest edge list) printing
per-component: backbone edges, full-graph bridges among them, and the
largest tied-together components. Read-only; stats.py section gains one
line (bridge count) only if the mode lands clean.

### S5 — available, not recommended (no slices)

- **U5 SQL-native motif census**: one `hyper_incidences` GROUP BY over
  cross-edge 3-node overlaps, 100% incidence coverage. Still
  consumer-gated — the #451 NO-GO (no production motif lane justified)
  stands; recorded so the corrected record does not decay into
  "motifs are impossible".
- **U6 `index` community fit**: opt-in run only, broad indexes only;
  the §2.3 evidence predicts an EM collapse at k≥12 — run only if a
  consumer asks.

## 4. Acceptance criteria & shakedown

1. **S1**: `pytest tests/test_scipy_matching.py` green with the
   extended Hall-collision test; `assign-feasibility` prints the
   matched count (958-class, not 8733) on the live store.
2. **S2**: dry-run on the live store prints the stale-set census
   (expected small — the family is one day old); planting a synthetic
   stale label in a tmp DB drops it under `--apply` and survives
   dry-run; the 8 curated `$.group` sets survive when their dyads
   exist.
3. **S3**: a written decision in `ontology.md` §`index` with the
   measured comparison; `recompute-hyper` unchanged unless the default
   slot wins.
4. **S4**: `make graph-mst --format resilience` prints per-component
   bridge counts; on a toy graph (path + triangle) the bridge census
   matches hand computation; live run read-only.
5. Targeted tests green (`test_scipy_matching.py`, `test_l1_mst.py`,
   `test_hyper_incidence.py`); `make static_checks` + md-lint pass;
   `make search-fresh APPLY=1` then plain rc=0 (doc/ changes).
6. No eval-gate bullet: nothing here changes query-visible store
   semantics (S2 removes rows no query should have been serving; S3
   defaults are decided, not auto-flipped).

## 5. Unmet-trigger register (STAYS GATED — frozen 2026-10-31)

| Item | Reopen trigger | Measured now |
|---|---|---|
| LanceDB ANN-only sidecar (embedded_db_assessment.md) | cosine leg > ~1 s steady p50 (~5× corpus); sub-50 ms interactive consumer; LanceDB 1.0 + lexical parity | 190 ms steady at 179,264 rows; audits show engine lexical legs structurally off FTS5 |
| DuckDB `fts` as lexical engine (duckdb_vector_search_assessment) | snippet/highlight/phrase/boolean parity | 2026-10-30 re-check: booleans silently ignored, no snippet; externals audited worse |
| duckdb_20_sqlite_reentry port | port cost < win | B2 PASS; ≈17–24 day sizing; note_search FTS5 sidecar split precondition unchanged |
| vss HNSW index macros | working COSINE opclass on our pin + O(100k) note corpus | recall ~57% @16.5k, +75% db size, `opclass not supported` |
| `onager_ctr_personalized_pagerank` wrap | Onager honours the personalisation vector | bug live (`onager.py:1099` ignores the personalisation column, restart hardcoded). Degeneracy cell CORRECTED 2026-10-31 (post-archival audit): the 2026-09-24 "PPR ≈ 1-hop ego" verdict was re-anchored by #409 (2026-10-14, ppr_reprobe_economic.md) — PPR is seed-dependent, NOT universally degenerate (Infosys hop≥2 0.038, ego-local; HDFC Bank hop≥2 0.508, propagates; Reliance post-1-hop top-8 all real companies). Do not cite "degenerate anyway" as an independent blocker |
| MAX-engine GEMM rung | ~10⁵ embeddings under a BATCHED consumer or a measured batch-GEMM wall | convo corpus hit 179k but the consumer is single-query brute force (no GEMM shape); watch |
| numba Phase C backend | ≥500k-row production matrix | 179k closest-ever; not there |
| HGX core-periphery / MMSBM disagreement study | a consumer for block-disagreement QA | consumerless, stay-gated (#439 register) |
| T3 `min_weight_full_bipartite_matching` | fuller invested_in edge type or per-sector full coverage | 277×84, Hall collision (82/84) unchanged |
| P2.2 incremental DuckDB materialization | mirror leg busts perf budget or grows >10× | 2.6–3.7 s, fine |
| Security Phase 4 (deploy-time) | the app deploys | not deployed |
| Mojo disposition | operator ruling on freeze-vs-retire | freeze recommended, pending |
| U5 SQL-native motif census (S5; row added 2026-10-31 post-archival audit — S5 items had no trigger) | a hypergraph-structure consumer appears (e.g. the MMSBM block-disagreement row above) — run the census first; one GROUP BY over `hyper_incidences`, 100% incidence coverage | #451 NO-GO measured `compute_motifs` through a 24%-incidence-visibility window (76% of incidence mass unseen, hgx_unlock_shelf_probe.md); the SQL census itself is unmeasured |
| U6 `index` community fit (S5; row added 2026-10-31 post-archival audit — S5 items had no trigger) | a consumer asks for index-community QA — protocol pre-designed (hgx_over_dyads §2.3) | EM collapse at k=12/16 already measured (modularity 0.0000, hgx_over_dyads), so a run is confirmatory, not exploratory |

## 6. Non-goals

- No reopening of #456–#459 scope (their non-goals stand, including
  "no changes to `l1_mst.py` algorithm" — S4 adds a report MODE, not
  an algorithm change; the MST itself is untouched).
- No motif lane, no `hyper_motifs.py` (S5 is a census measurement, and
  only if a consumer appears).
- No default-scope changes to `hyper_communities` (index joins nothing
  without the S3 decision).
- No role-vocabulary canonicalisation beyond #457's case-fold
  (free-text one-offs stay folded-not-canonicalised).
