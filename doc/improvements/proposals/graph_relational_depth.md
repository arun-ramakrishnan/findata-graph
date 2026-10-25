---
title: "Relational depth overhaul — typed metapaths, multi-hop insights, and capability-gated scaling"
status: proposed
filed: "2026-10-25"
area: "helpers/graph (traversal, derive lanes, context packs, scaling doctrine)"
---

# Relational depth — from 1–2 hop aggregations to composed relationships

**Date:** 2026-10-25 · **Status:** proposed (operator ask: "connections are
still only max 1-2 hops… we need far better insights into our
relationships… major overhaul")

## 1. Motivation — the data is already here, the composition isn't

Live relation inventory (e_all_und, 2026-10-25, 129,708 rows):

| semantics | types | rows |
|---|---|---|
| ownership/control | `subsidiary_of` 21,504 · `acquired` 118 · `invested_in` 2,718 | 24,340 |
| supply flow | `supplier_to` 33,442 · `customer_of` **2** | 33,444 |
| affiliation | `same_group` 17,840 · `part_of`/`has_company` 2,420×2 · `listed_on_index` 13,230 | 35,910 |
| competition | `competes_with` 7,156 | 7,156 |
| joint activity | `jv_with` 2,150 | 2,150 |
| attention/co-occurrence | `co_mentioned_in` 19,276 · `cited_in` 3,954 | 23,230 |

Every served surface today aggregates ONE edge type (cross_sector_bridges,
co_mention_top, edges_by_year) or ranks vectors (semantic_neighbors) —
max effective depth 1–2. Nothing composes: no ownership cascades
(A→subsidiary→sub-subsidiary), no supply chains (supplier-of-supplier),
no competitor-of-competitor, no "how are X and Y actually connected".
The hyper lane predicts links (D10) but doesn't EXPLAIN connectivity.
`csr.py::bfs_path(max_hops)` already exists (typed-agnostic); the gap is
a semantics layer + derived insights + serving.

## 2. Doctrine — capability-gated scaling, not count-gated

The operator's framing: edge-count triggers (T1 ~1M, T2 ~10M doubled
rows) measure SIZE, not VALUE. Adopted principle (generalises the D10
per-family density restatement, which already corrected this class):

- **Drivers = capability + measured wall**: promote/build scale work
  when a NAMED capability needs it at a measured pain point — e.g.
  "connect() p95 > 250 ms at depth 4" (promotes CSR+ART bridge),
  "s-walk explanations hot on live asks" (re-runs hyper_scale_bench).
- **Count ladders remain GUARDRAILS** (cost ceilings on rebuild/backup),
  never reasons to build.
- Evidence this is right: the L1 folds (algorithmic work-reduction beat
  any engine swap at this scale), the read-path fixes (#438/A′ — served
  capability, not scale), and the cosine-port measurement (2026-10-25:
  the "270× headline" collapsed to shape-dependent reality; measured-first
  doctrine paid for itself same day).

## 3. Slices

- **R1 typed metapath layer** (`helpers/graph/metapath.py`): a relation-
  semantics table over the edge types — `ownership` (transitive closure of
  subsidiary_of + acquired + majority invested_in), `flow` (supplier_to ∪
  customer_of), `affiliation` (same_group ∪ part_of ∪ listed_on_index),
  `competition` (competes_with, symmetric), `joint` (jv_with),
  `attention` (co_mentioned_in ∪ cited_in) — plus
  `connect(a, b, semantics=…, max_hops=3)` returning typed path
  explanations over the CSR substrate (bfs_path per semantics; e_all_und
  is type-complete). This FIRES the HGX shelf's recorded s-paths trigger
  ("first path-explanation UI/API ask" — the operator just made it): the
  dyadic leg is R1; the hyper leg (R5) explains connectivity THROUGH
  hyperedges (editions/groups), not just dyad chains.
- **R2 deep relationship cards**: extend `context_pack.py` (ego-subgraph
  serializer) with depth-3 typed neighborhoods and per-semantics rollups:
  ultimate parent, 2-step supply neighborhood, competitor overlap set,
  shared groups/editions. Serves app.py cards + context packs for LLM
  workflows (the pack is the existing GraphRAG-lite artifact).
- **R3 derived multi-hop insights** (new derive_* lanes, first-class
  rows): `ultimate_parent` (ownership closure — collapses the 19K VIGIL
  leaf forest to control roots), `hidden_connections` (companies with
  k-hop graph distance ≤2 AND co_mention/co-cite evidence — the
  "connected but never discussed together" set), `supply_chokepoints`
  (suppliers shared by ≥3 competitors — supplier-side betweenness on the
  flow subgraph), `competitive_overlap` (competitor-of-competitor
  triangles). Each lands as materialised views + derive CLI + eval-gate
  bullet (query-visible).
- **R4 trigger-ladder rewrite** (doc): vault_scaling §2 rows annotated
  with their CAPABILITY ("what unlocks at this tier"), promotion gates
  restated as capability+p95 walls; hypergraph-next register updated to
  match. The T1/T2/T3 ceilings stay as guardrails.
- **R5 hyper s-paths** (shelf unlock, now triggered): s-walk shortest
  paths over the incidence store as the hyper explanation leg of
  `connect()` — "A and B share hyperedges: The Chatter #69, Tata group,
  Pharma sub_sector" — alongside R1's dyadic chains. HGX's
  `s_walk` primitives; SQL/primitive lane first (8K incidences is
  trivial), hyper_scale_bench re-run only if live asks get hot.
- **R6 extraction gap**: `customer_of` has 2 rows against supplier_to's
  33K — the RPT ingest writes supplier edges but not the reverse
  customer relation (or names it differently). Audit + fix the ingest
  mapping; flow semantics are half-blind without it.

## 4. Acceptance

- R1: `connect()` answers on the live store at depth ≤4 under ~250 ms p95
  (CSR path); typed-path fixtures pinned (ownership chain, supply chain,
  mixed); eval-gate bullet (query-visible semantics).
- R2: relationship card fixtures (3 companies incl. a dual-member +
  a 19K-leaf subsidiary root); LLM-pack smoke.
- R3: each derived lane carries count/parity pins + eval-gate; the
  ultimate_parent rollup must reproduce a hand-checked ownership cascade.
- R5: s-path explanations on ≥10 live pairs; parity vs an inlined
  reference.
- R4: ladder doc rewritten with capability column; no count trigger
  remains a driver.
- Gates: targeted suites green; the harder-questions bank (notes reframe
  doctrine) gains a relational tier.

## 5. Non-goals

- No language/engine moves (ladder doctrine stands; the cosine-port
  outcome — Rust wins at production shape but only after measurement
  killed two wrong framings — is the argument for measured-first).
- No schema migration of graph_edges (all composition is read-side over
  e_all_und + CSR + incidence store).
- D10's hold families stay hold-gated (participant data, eval floors) —
  R-lanes are orthogonal (explanation/derivation, not prediction).

## 6. Execution

Deferred until operator go. Order: R6 (cheap data fix) → R1 (the core) →
R3 (derives on R1 primitives) → R2 (serving) → R5 (hyper leg) → R4
(doctrine rewrite lands with the capabilities it describes).
