---
title: "Relational depth overhaul — typed metapaths, multi-hop insights, and capability-gated scaling"
status: executed
filed: "2026-10-25"
executed: "2026-10-25"
area: "helpers/graph (traversal, derive lanes, context packs, scaling doctrine)"
completed_md: "441"
---

# Relational depth — from 1–2 hop aggregations to composed relationships

**Date:** 2026-10-25 · **Status:** EXECUTED + ARCHIVED (#441) — all seven
slices executed the same day (operator ask: "connections are
still only max 1-2 hops… we need far better insights into our
relationships… major overhaul")

## 1. Motivation — the data is already here, the composition isn't

Live relation inventory (source `graph_edges`, 2026-10-25, **62,434**
dyads — REBASED in R6's audit: the first filing's 129,708 quoted
`e_all_und`, the derived UNDIRECTED substrate that stores both
orientations per dyad plus hyper-membership derivations, reading ~2×
high):

| semantics | types | rows |
|---|---|---|
| ownership/control | `subsidiary_of` 10,752 · `acquired` 59 · `invested_in` 1,359 | 12,170 |
| supply flow | `supplier_to` 16,721 · `customer_of` **1** | 16,722 |
| affiliation | `same_group` 8,920 · `listed_on_index` 6,615 · `listed_in` 930 · `belongs_to` 184 | 16,649 |
| competition | `competes_with` 3,578 | 3,578 |
| joint activity | `jv_with` 1,075 | 1,075 |
| attention/co-occurrence | `co_mentioned_in` 9,638 · `cited_in` 1,977 | 11,615 |
| other (rating/regulation/exposure) | `exposed_to` 359 · `rated_by` 216 · `semantic_peer` 25 · `regulated_by` 17 · `approved_by` 8 | 625 |

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

- **R1 typed metapath layer — EXECUTED 2026-10-25**
  (`helpers/graph/metapath.py` + `tests/test_metapath.py`, 22 tests): a
  relation-semantics registry over the edge types — `ownership`
  (trusted subsidiary_of + reversed acquired, directed child→parent),
  `flow` (oriented supplier_to ∪ mirrored customer_of), `affiliation`
  (same_group ∪ listed_on_index ∪ listed_in ∪ belongs_to),
  `competition`, `joint`, `attention`, plus `any` (union + peripheral
  types) — and `connect(a, b, semantics, max_hops=3)` returning typed
  hop explanations over an in-memory adjacency cached per
  db generation. Ownership trusts vigil subsidiary_of rows ONLY on the
  strict positive relationship vocabulary (R6: 6,120/10,677 live; the
  TCS→Rallis India false-parentage class is excluded — pinned by test
  AND demonstrated on live data). Measured live (warm): ownership
  depth-4 1.8 ms, flow 3.6 ms, any 9.1 ms — far under the 250 ms p95
  acceptance bar; cold adjacency build 363 ms, cached. Live demo: the
  flow metapath explains Reliance Industries ↔ Indian Oil Corporation
  as a 4-hop chain through NMIA airport → Adani Total Gas →
  IndianOil-Adani Gas. The typed layer is read-side only (read-only
  connects; no store writes) so the ontology eval gate is satisfied by
  construction — the R3 derive lanes carry the real gates. CSR
  per-family promotion stays behind the capability gate as designed
  (the untyped CSR bins carry no edge labels).
- **R2 deep relationship cards** — EXECUTED 2026-10-25:
  `context_pack.py` gains an opt-in "Deep relations" section
  (`research_db` parameter; CLI `--research-db`, default
  memory/research.db; absent file / pre-R3 tables skip it silently —
  hermetic tests never touch the live store). Sections: **Control**
  (ultimate-parent chain from the R3 table + the descendant-forest
  rollup with nearest depth-3 members; promoter-coalition annotation on
  self-roots), **Two-step supply** (rivals sharing ≥2 of the entity's
  suppliers, second-tier suppliers, suppliers shared by ≥2 customers —
  the hidden-connections corroboration doctrine applied to the card),
  **Insight rollups** (hidden connections, competitor-of-competitor
  set, chokepoint suppliers holding the entity), **Groups & editions**
  (group/edition/jv hyperedge labels). READ-PATH DECISION (resolves the
  R3 deferral): the pack reads the R3 tables DIRECTLY from research.db
  sqlite — no graph.duckdb v_* mirror; the DuckDB cache keeps its
  rebuild/snapshot contract untouched. Acceptance fixtures (LLM-pack
  smoke via the CLI): Tata Steel (self-root, 163-company descendant
  forest, shared-supply rivals = the Tata vendor network, hidden
  connection to Rallis India — structurally close, never co-mentioned),
  HDFC AMC (dual member: parent HDFC Bank at depth 2 AND parent of
  HDFC AMC International (IFSC)), Rallis India (depth-1 subsidiary;
  group: Tata). FINDING for the adjudication backlog: the largest
  nominal control root, Reliance Lithium Werks B.V. (237 descendants),
  is an INVERTED apex — Reliance Industries sits at depth 1 UNDER its
  own Dutch subsidiary (mis-oriented vigil edge; Tata Steel at 163 is
  the largest clean root).
- **R3 derived multi-hop insights** — EXECUTED 2026-10-25
  (`helpers/graph/derive_relational_insights.py`, four first-class
  research.db tables + `make derive-relational-insights`, dry-run
  default): `ultimate_parent` (ownership closure over metapath's
  strict-trust adjacency via the new public `ownership_parents()`
  accessor — one trust filter with `connect()`, no drift; min-claimant
  adjudication for the 187 multi-parent children, cycle guard for the
  2 mutual pairs; promoter-coalition companies are their own roots
  annotated with the promoter-group aggregate stake — I2 absorbed as
  designed), `hidden_connections` (structural distance exactly 2, no
  direct structural edge, no co_mentioned_in; corroboration floor ≥2
  shared intermediates — MEASURED: 93,647 pairs pass at ≥1, 1,898 at
  ≥2; intermediate degree cap 50), `supply_chokepoints` (suppliers
  serving ≥3 mutual competitors — 6 live, Indus Towers → Airtel /
  Hexacom / Vodafone Idea the showcase), `competitive_overlap`
  (competes_with triangles — 8,160). Live pins 2026-10-25: 23,697 /
  1,898 / 6 / 8,160 rows, content digests ae8e37eb / 6a7b8bc5 /
  1f367ed2 / ab50560d. Eval gate: shapes `ultimate_parent_of` +
  `insight_counterparts` (absent table answers [] — a pre-R3 parent;
  first apply declared-improved), roster v6 = 166+7 questions
  (Rallis→Tata Chemicals, TCS→Tata sons, 365Squared→Proximus Global
  acquired-reversed, HDFC AMC International (IFSC)→HDFC Bank the
  hand-checked depth-3 cascade, 5paisa↔IIFL hidden set, Indus Towers
  chokepoint, 20 Microns overlap); live-vs-live self-check and
  parent-vs-candidate gate both ACCEPT, 0 reasons. The graph.duckdb
  v_* mirror lands with R2 (the serving consumer decides direct-sqlite
  vs cache).
- **R4 trigger-ladder rewrite** — EXECUTED 2026-10-25:
  `archive/graph/vault_scaling.md` §2 rewritten — a capability column
  ("what unlocks at this tier") heads the ladder, promotion fires on
  MEASURED walls only (full-graph BFS p95 > 100 ms sustained;
  CSR-vs-DuckDB >2× sustained crossover; T3 validation legs mandatory
  pre-promotion), and every row count is demoted to an arrival ESTIMATE
  labelled "guardrail, never fires". The T1/T2/T3 ceilings stay as
  guardrails (100 ms BFS wall, 5 s rebuild budget, int32 payload cap).
  The arc's own outcomes are cited in-doc as the capability-gating case
  study: the cosine port promoted only after measurement (rust 892 q/s
  at production shape, numpy leads at 5k — shape decides), and metapath
  stayed on typed in-memory adjacency at 62K edges because the
  capability is labels+trust, which label-free CSR bins cannot carry.
  The hypergraph-next register (session memory) updated to match. No
  count trigger remains a driver.
- **R5 hyper s-paths** — EXECUTED 2026-10-25: `hyper_path(a, b,
  max_hops)` + `shared_hyperedges(a, b)` + `connect(a, b,
  semantics="hyper")` in metapath — s-walk shortest paths over the
  incidence store (1,713 hyperedges / 10,147 incidences across 12
  families) as the hyper explanation leg alongside R1's dyadic chains.
  s-distance counts hyperedge hops; distance 1 answers with ALL shared
  hyperedges ("A and B share: group Tata, edition …"), longer walks
  alternate entity / "type:label" bridges with min-(label, type)
  claimant determinism. The bipartite store is cached per generation +
  a COUNT:MAX(rowid) incidence stamp (hyper refreshes invalidate without
  depending on graph_edges triggers); absent hyper tables → empty store
  → None (pre-hyper DBs tolerated). PRIMITIVE lane only — HGX `s_walk`
  stays on the shelf (18.7 ms/pair live including the cache stamp; the
  250 ms wall untouched), hyper_scale_bench not re-run. Acceptance:
  14/14 live pairs PARITY vs an inlined reference — 12 s-distance-1
  explanations (TCS↔Rallis sharing 5 hyperedges incl. "In the Middle of
  Things"; Muthoot group+industry+sector; Grasim↔Hindalco via Aditya
  Birla; Asian Paints↔Bata via theme:Premiumization; Hinduja / TVS /
  Welspun / Adani / RPSG group pairs) + 2 s-distance-2 bridging walks —
  and 8 new hermetic tests incl. parity-vs-reference, hop-bound,
  determinism, CLI format (30 in the suite).
- **R6 extraction gap — AUDITED 2026-10-25, verdict: NOT a gap, no
  ingest change warranted**: the VIGIL RPT sync coins BOTH trade
  directions as oriented `supplier_to` edges (filer sells →
  (filer, counter); filer buys → (counter, filer) —
  related_party_sync.py:512, apply_supply_pairs); same-period two-way
  pairs emit BOTH edges (4,462 mutual pairs live), and `customer_of`'s
  single row is the prose extractor's paren-form relic (GAIL→IOCL).
  Flow direction coverage is complete via orientation — `flow()` =
  supplier_to (oriented) ∪ customer_of (mirrored). Residual caveats:
  (a) 672 cross-period MAX-aggregate pairs keep ONE edge, reverse
  direction only in properties (`two_way: cross-period`) — restorable
  by a properties-aware loader if a flow algorithm ever needs it;
  (b) the inventory double-count (above) is corrected.
- **R6a subsidiary_of cure (C1–C4, operator-agreed 2026-10-25)**: an
  explicit relationship vocabulary replaces the `_PAIRS_SQL` substring
  catch-alls — ordered ladder: personnel/KMP → drop; related-party-of-
  subsidiary → drop; fellow + third-party-parentage descriptions →
  same_group; strict positives (+ the ~15 variant spellings: step-down,
  indirect, the `subsidary` typo) → keep; plain holding-co → keep.
  One-time backfill over `vigil:*` subsidiary_of rows — EXECUTED
  2026-10-25: keep 7,332 / →same_group 1,520 / →jv_with 31 / dropped
  1,794 (post-state: subsidiary_of 10,752 → 7,407, same_group → 10,382,
  jv_with → 1,096, store 62,434 → 60,572), then graph-rebuild → CSR
  (fresh, gen 621577) → stamp-centrality so every surface (e_subsidiary,
  e_all_und, centrality, frontend) inherits the cure. Post-cure smoke:
  TCS↔Rallis = 1-hop same_group under affiliation, ownership None
  under strict trust. Eval gate ACCEPTED (`--expect-improve
  edge-rallis-india-same-group`, +8 Tata siblings); the v5 rebaseline
  ref records cure + pre-existing ingest drift (verified: only the
  rallis question changed due to the cure). C3 QA census: 2
  mutual-parent claim pairs (Lemon Tree↔Totally Foxed, Suzuki Motor
  Gujarat↔Maruti), 187 multi-parent children of 7,163 — the
  name-variant/orientation adjudication input R3 consumes. Metapath's
  strict filter stays as defense-in-depth.
- **R6b invested_in semantic correction (I1+I3)** — EXECUTED 2026-10-25:
  promote ONLY clean-investor majority rows into the ownership semantic.
  The predicate is NAME-based, not entity_type-based — the slice's
  "2 of 121" was the entity_type proxy, but live entity_type is noisy
  (holding companies typed person/institution) and the structural
  detector (`is_category_holder`: subtotal/total/equation lines +
  subcategory labels — BSE t3 mints them because the subcategory header
  masquerades as the category) yields 12 genuine majorities live: 11
  BSE (Bosch Home Comfort 74.25%, Central Bank→President of India
  81.19%, …) + Catterton→Birkenstock 55.89% from the yfinance leg
  (`pctHeld` is a fraction, normalized ×100). Applied: 220 of 1,359
  invested_in rows marked `properties.category_holder=true` (standing
  at mint in BOTH holder lanes), 12 `derive:shareholding_majority:*`
  subsidiary_of edges (weight 1.0, stake in `properties.pct`; foreign
  subsidiary_of rows never clobbered; refresh/retract standing in
  `reconcile_majority_ownership` after every sync apply; identical
  stamps skip the write). Eval gate ACCEPT — 166 questions, 0 drift,
  no rebaseline. Metapath smoke: promoted pairs 1-hop under ownership;
  participation stays out of every metapath family (minority stakes
  connect everything). `stake_pct_of` is the participation-weight
  accessor; ontology.md records invested_in as the participation family
  — control arrives via promoter coalitions (R3 absorbs I2), not single
  majorities.

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

Operator go 2026-10-25. R6 EXECUTED same day (audit verdict above); R1
EXECUTED same day (metapath layer, measured, 22 tests); R6a EXECUTED
same day (C1–C4 complete: cure applied live, rebuild chain, eval gate
ACCEPT + v5 rebaseline, QA census — see the slice above); R6b EXECUTED
same day (I1+I3 complete: 220 category-holder markers + 12 majority
promotions applied live, gate ACCEPT 0 drift — see the slice above);
R3 EXECUTED same day (four insight tables live + roster v6 + gate
ACCEPT — see the slice above); R2 EXECUTED same day (deep-relations
cards in context_pack, direct-sqlite read path, 3 fixture packs — see
the slice above); R5 EXECUTED same day (hyper s-paths + 14/14 live
parity — see the slice above); R4 EXECUTED same day (ladder doctrine
rewritten — see the slice above). **ALL SEVEN SLICES EXECUTED — the
arc is complete and ready for archival on the operator's go** (§4
acceptance rows all satisfied: R1 measured under the 250 ms bar with
pinned fixtures; R2 fixture packs rendered; R3 count/parity pins +
gated lanes + the HDFC hand-checked cascade; R5 s-path explanations
≥10 pairs with reference parity; R4 capability column live, no count
driver remains).
