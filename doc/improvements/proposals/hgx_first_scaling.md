---
title: "HGX-first scaling — h_* materialisation, incidence-native workflows, capped dyadic diagnostics"
status: proposed
filed: "2026-09-16"
executed: null
completed_md: null
area: "helpers/graph/query.py (h_* cache views), helpers/maintenance/maint.py (chain), helpers/graph/suggest_relations.py (co-membership lane), helpers/graph/stats.py (incidence-native sections) — D4 + HGX promotion, no SQLite schema change"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# HGX-first scaling — h_* materialisation, incidence-native workflows, capped dyadic diagnostics

**Date:** 2026-09-16 · **Status:** PROPOSED ·
**Area:** `helpers/graph/query.py` (h_* cache views), `helpers/maintenance/maint.py`
(chain note), `helpers/graph/suggest_relations.py` (co-membership lane),
`helpers/graph/stats.py` (incidence-native sections) — absorbs D4 from the
hyper_lane_wiring ledger; no SQLite schema change.

## 1. Motivation — measured + ruled

The 2026-09-16 live-invariants incident (fixed in e5728add) exposed the
shape of our scaling wall: a diagnostic burned 45.5M dense matrix cells
to answer questions the incidence store answers with **5,952 rows**
(four orders of magnitude). igraph is NOT the exit — RETIRED AND
DELETED per D16 (graph_design.md decision table, operator decision
2026-09-13: `helpers/graph/igraph_bridge.py` + tests removed; the
alternate-engine seat passed to the HGX hypergraph lane itself —
hy-MMSBM superseded Leiden, higher-order lanes replaced the weighted
centralities; reviving a dyadic weighted lane needs its own proposal).
This arc is that seat's purpose: HGX as the primary structure lane.

**Operator ruling (architecture stance, 2026-09-16):** the n^2 blowup
is an artifact of the DYADIC projection — pairwise distances over the
2-section of what the star store already models as incidences. The
path out is incidence-native analytics. Structure questions
(communities, centrality, roles, capture-quality, suggestions) move to
the incidence side; pairwise questions (diameter, distant pairs) stay
dyadic but CAPPED — they are inherently projections, and diagnostics
are not analytics products.

D4 (`h_*` materialisation) was gated on "a SQL-over-incidence
consumer appears" — this arc builds the consumers, so the gate
self-fulfills. HGX currently sits secondary: compute lanes wired into
maint TIER2 + three API rosters (#235), but no workflow CONSUMES hyper
structure; suggestions, stats, and triage all still ride the dyadic
projection.

## 2. Design principle

Incidence-first: any new structure consumer queries h_* in SQL
(membership counts, co-membership, block lookups — all
O(|incidences|), 5,952 rows today); nothing new materializes pairwise
output. Dyadic ops stay Onager-side and capped.

## 3. Slices

- **S1 `h_*` cache materialisation (D4 absorbed)**: graph.duckdb
  gains `h_edge` / `h_incidence` views over the star store via the
  existing attach/prep path (query.py `_attach_sqlite` /
  `_prep_graph_connection`); refreshed by the same rebuild the disk
  cache rides (test_graph_disk pins). maint.py section-333 note
  flipped (the cache now READS incidences — derive-hyperedges gains a
  paired graph-rebuild in the chain). Parity pins: view row counts ==
  star store (533 / 5,952), roles/validity columns carried.
- **S2 first SQL-over-incidence consumers**:
  - S2a stats.py: hypergraph STRUCTURE section computed over h_* —
    block sizes, top hyperedges by membership, incidence counts, and
    the capture-quality family tally (replacing the dyadic top-K chain
    tally as the capture signal).
  - S2b suggest_relations co-membership lane: companies sharing >=2
    hyperedges with no typed dyadic edge -> suggestion candidates
    (origin: co_membership), same JSONL + dedup + H4 review contract;
    SQL over h_*, no Onager pairwise ranking for this lane (the
    jaccard lane stays, gated).
  - S2c (DEFERRED): extra API endpoints beyond the #235 rosters —
    only when a consumer needs them.
- **S3 capped dyadic diagnostics (absorbs live_inv_longest_chains
  S3, archived completed.md #240)**: longest_chains keeps the edge-touched universe AND gains a
  hard cap (universe ceiling ~3k with head-count note, or sampled
  pairs); print_stats keeps a small dyadic section because
  diameter/distant-pairs are inherently pairwise — but it can never
  again dominate a gate leg.
- **HGX posture change (documented, no new compute)**: HGX outputs
  (hy-MMSBM blocks, higher-order centralities) become workflow INPUTS
  — stats sections and suggestion lanes consume them — moving HGX
  from "secondary option" to the primary structure lane — the seat D16
  gave it when igraph was retired; Onager remains the dyadic engine.

## 4. Acceptance

- S1 parity pins green (h_* == star store, both rebuild paths).
- S2a renders from SQL with zero pairwise materialization (assert:
  no n x n allocation in the section path); capture-quality tally
  covers the same edge families the dyadic tally did.
- S2b produces deduped candidates on the live store; advisory
  suggest-relations leg stays green and no slower.
- S3: capped render measured; live-invariants leg cannot regress with
  universe growth past the cap.
- Full `make qa` + `make advisory` once at arc end.

## 5. Deferred

- **D10 prediction/motifs/dynamics** — still density-gated (group
  8/k=3.8, jv 6/k=2.0); S2b co-membership suggestions are the cheap
  precursor, not the prediction lane.
- **S2c additional hyper API surface** — consumer-driven.
- **Weighted/hyper link prediction proper** — needs an HGX-native
  predictor or a D15-class engine decision; not this arc.
- **s-walk caveat stands**: projections flatten hyperedges to cliques
  (measured: the 41-member usa edge acts as a 41-clique) — the win is
  asking incidence-native questions, not re-projecting.
