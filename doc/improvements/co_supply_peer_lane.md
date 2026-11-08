---
title: "Co-supply peer lane — score buyer-pair shared-supplier overlap from the supply star, prereg-gate promotion"
status: filed
filed: "2026-11-08"
area: "helpers/graph"
---

# Co-supply peer lane

**Date:** 2026-11-08 · **Status:** FILED (slice go: S1 authorized;
S2/S3 per-slice) · **Parent:** algorithms_adoption_sweep S3 verdict
(the census's ENRICHED supply read), D10 eval-bank discipline

## 1. TL;DR

The full-incidence census (hyper_motifs, 2026-11-08) measured the
supply family ENRICHED at both orders (k=2 +54.6, k=3 +59.6) — the
strongest cross-edge structure in the store — while supply's existing
consumers (context packs via `_HYPER_FAMILIES`) read each buyer's
portfolio individually and nothing consumes the cross-edge shape. A
supply hyperedge is one buyer's supplier portfolio (label = buyer,
members = suppliers; 683 buyers with ≥3 suppliers), so pair overlap =
**two buyers buying from the same suppliers**. This proposal wires
that signal as a co-supply peer lane: score (buyer A, buyer B) pairs
by shared-supplier count, surface report-first, and gate any
promotion behind a pre-registered eval bank — the D10
report-first/`--apply`-gated shape.

## 2. Evidence

- Census live read 2026-11-08: supply 547 deduped edges / 5,469
  nodes; 6,603 pairs share ≥2 members (1,723 at exactly 2, 1,684 at 3,
  **3,196 at 4+**); null mean 3,426 ±58 (k=2) / 1,211 ±62 (k=3) —
  `hyper_motifs.py` report, recorded in the parent proposal's S3
  verdict block.
- Supply semantics: `hyperedge_types.py` (buyer portfolios, k≥3
  threshold applied 2026-11-08; largest portfolio 222 suppliers).
- No existing consumer reads buyer-pair overlap: context packs consume
  per-buyer portfolios only; `a2_candidates.py` (supplier-candidate
  kernel) and `scipy_matching.py` (supplier→consumer assignment) are
  dyadic assignment lanes, not peer scoring.
- The census's `pair_shared_counts` + `--pairs-out` CSV
  (`helpers/graph/hyper_motifs.py`) is the ready-made scoring
  substrate — the lane does not need a new overlap implementation.

## 3. Slices

### S1 — Co-supply pair report [LANDED 2026-11-08]

- `helpers/graph/co_supply.py`: loads the supply family through the
  house guard protocol (`hyper_spectral.load_family`, deliberate
  `k2_cap=1200` — supply carries 547 k≥2 edges, cost measured trivial
  by the census), scores all buyer pairs with
  `hyper_motifs.pair_shared_counts`, normalizes as
  `shared / min(n1, n2)`, bands at the census's ≥4 high-conviction
  threshold; `make co-supply-peers` (report-only, default top 100,
  `--min-shared 2`, `--band`).
- 7 hermetic tests (`tests/test_co_supply.py`): scoring math,
  normalization, banding + band override, min-shared filter,
  determinism, CLI end-to-end (monkeypatched family), empty-family
  path.
- **Live read** (547 buyers / 5,469 suppliers): 6,603 pairs share ≥2
  suppliers (high band ≥4: 3,196, watch: 3,407) — matches the census
  counts exactly.

**S1 by-product finding — entity-duplicate pairs dominate the top
ranks.** Ranks 1-4/7 are the SAME company under two entity names
(Adani Ports and SEZ × Adani Ports and Special Economic Zone 0.99,
Indian Hotels × THE INDIAN HOTELS CO 0.98, Ambuja Cement × Ambuja
Cements 0.98, Titan × Titan Company 1.00, Larsen & Toubro × Larsen and
Toubro 0.97 — near-identical supplier portfolios). The co-supply
ranking doubles as an entity-alias worklist (near-identical portfolios
⇒ same company; feeds the name_dedup/entity_identity lanes). S2
consequence: the prereg must EXCLUDE probable-alias pairs from the
peer-signal eval — otherwise identity duplicates inflate precision
trivially (the same entity "co-occurs" with itself). S2 design
amended below.

**UPDATE 2026-11-08 — eliminated at source, not excluded.** Operator
ruled 7 canonical folds + 12 keeps from the worklist
(`bench_data/entity_alias_triage/rulings.json`); executed via a SCOPED
`name_dedup.apply_plan` (126 rows re-pointed, 1,515 duplicate edges
consolidated, 13 entities folded, zero-loss verified; record in
pending.md "Entity-alias fold round 1"). Post re-derive/rebuild:
buyers 547→517, duplicate pairs gone from the top (genuine peers rank:
ACC×Ambuja 0.97, TMPV×Tata Steel); pair mass 6,603→4,942. The S2
alias-exclusion STAYS as defense-in-depth — live proof: Bharti Airtel ×
Bharti Hexacom scores 0.97 (subsidiary with shared procurement,
correctly kept distinct).

### S2 — Pre-registered eval bank [LANDED 2026-11-08 — prereg filed, instrument frozen]

- Hypothesis (registered BEFORE measurement, linkpred-prereg shape):
  buyer pairs with ≥4 shared suppliers co-occur in future edition
  hyperedges (co-mention) at a rate above the buyer-pair base rate —
  i.e. shared supply-chain exposure predicts co-attention.
- **Alias exclusion (S1 by-product amendment)**: pairs whose buyer
  names resolve as probable aliases (the S1 finding: score ≥0.9 with
  near-identical names — Titan × Titan Company shape) are reported
  separately as an identity-dedup worklist and EXCLUDED from the
  peer-signal measurement; the base rate excludes the same entities.
- Instrument: freeze a co-supply pair snapshot
  (`bench_data/alpha_research/cosupply_snapshot.csv`), score against
  edition-family incidence realized AFTER the freeze (as-of
  discipline: pairs' member sets at freeze time only), precision@K
  style read + exact binomial vs base rate + BH FDR (the S2 stats
  layer of the parent proposal is the instrument).
- Decision rule (frozen): NO-GO if after ≥20 realized co-mention
  events cumulative precision is at/below base rate; promotion only
  on sustained ≥2× base rate.
- Report-only throughout; context-pack/UI promotion is S3 and needs
  this gate green.

**LANDED 2026-11-08.** Pre-registration filed BEFORE any measurement:
`doc/local/evaluations/cosupply_precision_prereg.md` (hypothesis,
frozen instrument, decision rule, outcome log — registration row
recorded). Runner: `helpers/research/cosupply_precision.py`
(`freeze`/`score`; 7 hermetic tests). Instrument frozen same day:
`bench_data/alpha_research/cosupply_snapshot.csv` — 4,942 frozen
pairs / 517 buyers (universe 285 with ≥1 candidate) at
2026-11-08T16:19:27Z. Two design refinements made during
implementation, recorded in the prereg: (1) the realization surface
is the `co_mention_editions` staging table (stable staging
`created_at`), NOT edition hyper_edges — those carry no valid_from
and their labels are titles; a newly-staged-edition guard (title
never staged pre-freeze) neutralizes late re-staging of old
editions, with the staging-vs-publication clock caveat on record;
(2) the alias exclusion is implemented via
`entity_identity.fold_registry` (same canonical ⇒ excluded), and the
as-of guard parses timestamps (string comparison mis-orders the ISO
``T`` vs space separators). Registration run: realized 0 (nothing
postdates the freeze yet); re-run at each `make derive-co-mentions`
close per the decision rule.

### S3 — Context-pack wiring [gated on S2]

A `co_supply_peers` section in `context_pack.py` behind the same
opt-in posture as the other hyper lanes. Only after S2's KEEP read.

## 4. Gates / eval discipline

- S1: targeted tests only; no store writes anywhere in the lane.
- S2: prereg filed in the doc before the first measurement run; the
  outcome log lives in this proposal (same shape as
  `doc/local/evaluations/linkpred_precision_prereg.md`).
- S3: consumer wiring rides the full gates quartet on the operator's
  go (`doc/procedures/gates.md`).

## 5. Risks

- **Giant-portfolio skew**: a 222-supplier buyer overlaps many pairs
  trivially; the `shared/min(n1,n2)` normalization + the ≥4 band
  (which the census shows carries the real mass) bound this. The
  null comparison in the census already accounts for degree mass.
- **Supplier-name resolution**: suppliers are entities post-fuzzy
  resolve; unresolved-filer skips (the documented 294-symbol residue)
  depress both observed and base rates symmetrically — the prereg's
  base-rate-relative design absorbs it.
- **As-of leakage**: the S2 snapshot must score only pairs whose
  supplier sets predate the realization window — the freeze file
  carries computed_at per pair, same contract as the linkpred prereg.
