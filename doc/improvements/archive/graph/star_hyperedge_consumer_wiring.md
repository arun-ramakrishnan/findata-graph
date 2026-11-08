---
title: "Star-hyperedge consumers: reconcile index/subsidiary/supply standing structure, then wire it into context packs, recompute-hyper, and centrality lanes"
status: executed
filed: "2026-11-08"
executed: "2026-11-08"
completed_md: "492"
area: "helpers/graph"
---

# Star-hyperedge consumer wiring

**Date:** 2026-11-08 · **Status:** EXECUTED 2026-11-08 (S0 dyad audit + threshold reconciliation; S1 _HYPER_FAMILIES lane; S2 --families + rederive-hyper maint step; S3 make hyper-star-centrality) · **Proposal:** completed.md #492

Follows `hgx_over_dyads.md` (#457 applied `index`/`subsidiary`/`supply`
families as standing structure, deferred for lack of consumer) and
`polyadic_capture_edge_fidelity.md` (#490 retired edition cliques,
canonicalized co_mention).

The three standing-structure hyperedge families were registered + applied
with **zero fact-surface consumers and no re-derivation path**:

- `index` — 57 hyperedges from 6,615 `listed_on_index` dyads (116×
  compression); 753 companies sit in ≥2 indexes (measured 2026-11-08,
  exact)
- `subsidiary` — parent portfolios from `subsidiary_of` dyads (7,559
  live); **live store: 738 hyperedges incl. size-1 portfolios, no k≥3
  threshold applied** (see S0)
- `supply` — buyer portfolios from `supplier_to` dyads (17,174 live);
  **live store: 8,605 hyperedges avg 2.0 members, no k≥3 threshold
  applied** (see S0)

All written by `derive_hyperedges.py`, registered in
`helpers/graph/hyperedge_types.py` HYPEREDGE_TYPES, gated both directions
by `static_checks.py::check_ontology_doc_rosters` (§2.5 roster
`index`/`subsidiary`/`supply`), and integrity-gated by
`database_integrity_check.py::check_hyperedge_types`. The star families
are materialized in the DuckDB cache as generic `h_edge`/`h_incidence`
rows (verified 2026-11-08: all three families present). No
`recompute-hyper` maint step exists; `context_pack.py` consumes
hyperedges through the `_HYPER_FAMILIES` lane (line 68: `group`,
`edition`, `jv` only — the three new families are not in it);
`helpers/research/signal_sweep.py:77` reads dyadic `listed_on_index`
directly, not the star.

## 1. TL;DR

Reconcile the star families first (S0), then give them a named consumer
and a maint path:

- **S0 reconciliation (new — must run first)** — the k≥3 portfolio
  threshold recorded in the `_STAR_SOURCES` comment and the
  `hyperedge_types.py` docstring (382 parent / 620 buyer portfolios) was
  **never implemented in the apply path**: the star regroup
  (`derive_hyperedges.py:1228-1236`) groups unfiltered, so the live store
  carries every buyer/parent incl. singletons. S0 decides the threshold
  question (recommend: implement k≥3 as recorded), re-derives, re-measures,
  and fixes docstring + comment to match what actually lands. Direction
  is verified CORRECT (label = dyad `target` = buyer/parent;
  `supplier_to` stores source=supplier → target=buyer, sampled + counted:
  5,934 distinct suppliers / 8,603 distinct buyers).
- **S1 context_pack.py** — extend `_HYPER_FAMILIES`
  (`context_pack.py:68`) from `("group", "edition", "jv")` to include
  `index`/`subsidiary`/`supply`, so context packs surface "member of
  index X / subsidiary of parent P / supplier of buyer B" facts through
  the existing hyper lane (`h_edge ⋈ h_incidence`, `context_pack.py:472-484`).
  **Not** new `_EDGE_SPECS` entries: the star tables the earlier rev
  imagined (`e_index`/`e_supply`) do not exist, and `e_subsidiary` is
  already in `_EDGE_SPECS` (line 76, priority 1) consuming the dyadic
  mirror — that consumption stays.
- **S2 maint.py + derive CLI** — add a `--families` selector to
  `derive_hyperedges.py` (the CLI today is only
  `--apply/--verbose/--stale-only/--roles` — verified; the in-code
  "run --sources deliberately" comment is stale), then a `recompute-hyper`
  step in **TIER1_STEPS** that re-derives the standing families on each
  maint run.
- **S3 centrality lanes** — opt-in: route the three star families into
  `algorithms.py` centrality via the `PPR_INCLUDE_EDGE_TYPES` pattern
  (verified, `algorithms.py:1090`). Not part of the default path.

Each slice is additive; no schema version move (`hyper_edges.edge_type`
has no DDL CHECK — family work is a code registry change only, per
hyperedge_types.py docstring).

**Sequencing:** S0 re-measures the star shapes; the mapping-glossary
proposal (`ontology_mapping_glossary_enhancement.md`, #493) records those
shapes in `db_schema.md` — finalize #493 after this arc's S0 lands.

### Design principle — dyad vs star canonicality (operator Q&A, recorded 2026-11-08)

Neither representation is superior in the abstract; **canonicality
follows the arity of the underlying fact**, and this repo has exercised
the ruling in both directions:

- **Star canonical, dyad retired** when the dyad was a mere projection
  of a true set-fact: `part_of`/`has_company` (2026-10-19 — sector
  membership is genuinely n-ary; the `sector` family is canonical and
  the pair projects back out on read via
  `_materialise_sector_membership`), and `co_mentioned_in` (#490 —
  co-mention is a property of an edition, not of each pair).
- **Dyad canonical, star derived** when each row carries its own
  pairwise evidence: `supplier_to`/`invested_in`/`subsidiary_of` —
  per-row provenance (`source_ref`/`agent_id`/`source_tier`), temporal
  windows (`valid_from`/`valid_to`), and properties (RPT amounts,
  `stake_pct`, `relationship`) that the hyperedge store cannot carry
  (`h_edge`/`h_incidence` have no `properties` column — regroup is
  lossy by construction). `graph_edges` is the "canonical edge store"
  (`db_schema.md` §graph_edges); `hyper_edges` is the derived incidence
  layer, regrouped *from* the dyads, never the reverse.

Practical corollaries that shaped this arc:

1. Repair facts where they are atomic, re-derive projections from them
   — the flipped-twin dedupe (S0) is row-wise precisely because the
   dyad layer is where truth lives.
2. `apply_hyperedges` is additive-only ("membership shrinkage is NOT
   compacted here", docstring): a derived store that cannot compact
   without a full refresh must not be the landing place for per-batch
   writes — which is why S2 (recompute-hyper in maint) matters more
   than its size suggests.
3. Compression claims are conditional, not inherent: measured
   2026-11-08, the threshold-less supply star is 8,605 hyperedges +
   17,174 incidences = 25,779 rows for 17,174 dyads (1.5× expansion);
   the recorded 116×/19×/27× compressions materialize only at k≥3.
4. Pairwise questions stay dyad-shaped: context-pack hops
   (`_EDGE_SPECS`), the R2 shared-supplier corroboration
   (`_SUPPLY_MIN_SHARED = 2`, "a single shared supplier is hub noise"),
   and `signal_sweep`'s `index_membership` all read the dyad view; the
   star serves set semantics ("all subsidiaries of the same parent"),
   hub-noise-controlled centrality (dyadic projection excludes
   `listed_on_index`; the hyper s-walk lane measured healthy), and
   hy-MMSBM communities.

## 2. Evidence (all measured 2026-11-08 on this box unless noted)

- Live star shapes vs the recorded intent (`h_edge`/`h_incidence` via the
  served cache):

  | family | recorded (hgx arc, 2026-10-30/31) | live 2026-11-08 | k≥3 would-be today |
  |---|---|---|---|
  | index | 57 hyperedges; 753 members in ≥2 indexes | 57 ✓; 753 in ≥2 indexes ✓ (1,274 in ≥1; largest 752) | 57 (n/a) |
  | subsidiary | 382 parents at k≥3, max 227 | **738 hyperedges, sizes 1–228, avg 10.3** | 386 |
  | supply | 620 buyers at k≥3, max 222 | **8,605 hyperedges, sizes 1–223, avg 2.0** | 683 |

  The regroup (`SELECT source, target FROM graph_edges WHERE edge_type =
  ?` keyed by target) has no threshold; the recorded 382/620 numbers are
  hgx-arc measurements at k≥3 that the landed code never applies. Dyad
  growth (subsidiary_of 7,419→7,559; supplier_to 16,696→17,174) explains
  the 382→386 / 620→683 drift under a k≥3 rule.
- Dyad orientation (correctness of the star direction): `supplier_to`
  distinct source/target = 5,934/8,603; sampled rows read
  source=supplier (Talbros Automotive Components) → target=buyer (Tata
  Motors PV). `subsidiary_of` = 7,175 subsidiaries / 732 parents (cache
  holds 738 — last derivation predates the last dyad writes; S0
  re-derivation closes the drift).
- `helpers/graph/hyperedge_types.py:20-40` — docstring records the
  k≥3 shapes as if current; `derive_hyperedges.py:101-121`
  (`_STAR_SOURCES`) comment repeats them; `derive_hyperedges.py:1228-1236`
  (the regroup) implements none. Docstring/comment vs code drift is the
  S0 trigger.
- `helpers/graph/context_pack.py:68` — `_HYPER_FAMILIES = ("group",
  "edition", "jv")`; `context_pack.py:472-484` — the hyper lane queries
  `h_edge JOIN h_incidence WHERE edge_type IN (...)` and renders
  per-family fact lines. `context_pack.py:75-89` — `_EDGE_SPECS` already
  contains `("e_subsidiary", "subsidiary_name", "parent_name",
  "subsidiary_of", 1)` (dyadic mirror consumption, stays).
- `helpers/graph/derive_hyperedges.py --help` — flags are exactly
  `--apply/--verbose/--stale-only/--roles`; no `--families`, no
  `--sources`. `maint.py:210` — `_FILE_LANE_STEPS = ("graph-rebuild",
  "stamp-centrality")` is the served-store swap label tuple, not a step
  list; steps live in `TIER1_STEPS` (`maint.py:364`).
- `helpers/maintenance/maint.py` — `hyper-communities`/`hyper-centralities`
  steps exist (they consume `hyper_edges` for MMSBM/s-walk lanes) but
  nothing re-derives the hyperedges themselves before them.
- `helpers/research/signal_sweep.py:77` — `index_membership` reads dyadic
  `listed_on_index`; unchanged by this arc (non-goal).
- `doc/design/ontology.md` §2.5 roster `hyperedge_types` already includes
  all three families (gated, matches code registry — verified clean).
- **Dyad hygiene (supplier_to), measured 2026-11-08 — S0's first half:**
  - 17,174 rows collapse to **12,689 distinct unordered pairs**;
    **4,485 pairs exist in BOTH orientations**, and **4,449 of those
    carry the identical `source_ref`**. Execution correction: this is
    **not a dual-insert bug** — it is the documented same-period two-way
    emission (`related_party_sync.py:597-600`; 78% of mutuals carry both
    flows nonzero in the same period, by design). `UNIQUE(source,
    target, edge_type)` is ordered and cannot catch a flip, but none is
    stray.
  - Store composition: 17,154 of 17,174 rows are `vigil:rpt:*` (RPT
    lane); prose/newsletter ~20 — the store's semantics are
    `related_party_sync`'s to define, and its docstring already does.
  - Orientation convention on **single-orientation** rows only (twins
    excluded): source = supplier holds for **672/701 flow-classifiable
    rows (96%)** — orientation is flow-derived (purchase-dominant RPT →
    counterparty supplies; sale-dominant → filer supplies), matching
    the prose lane. The apparent "inversion" of the star lane was an
    artifact of sampling both members of two-way twins (a naive 400-row
    sample read 27%/73% `counter_display`-names-source/target for
    exactly this reason).
  - Residual: 29/701 single-orientation rows violate the flow rule
    (likely display-name noise or genuinely mutual flows) — audited
    case-by-case in S0, not bulk-flipped.

## 3. Design

### S0 — reconcile the star families (first; blocking for S1-S3 and for #493)

S0 has two halves, in order — **dyad hygiene first** (the star derives
from the dyads; repair facts where they are atomic), then the threshold.

**S0-hygiene — supplier_to dyad audit (CORRECTED at execution — rev 1 of
this section claimed a writer bug; the code reading reversed it):**

1. **No writer fix needed.** The flipped pairs are the *documented*
   same-period two-way emission (`related_party_sync.py:597-600`:
   "Two-way trading lands both edges ONLY when both directions are
   evidenced in the same period_end") — measured: 78% of mutuals carry
   both flows nonzero in the same period. The mint-time drops +
   `prune_symmetric_artifacts` keep a re-run from re-creating the
   artifact classes.
2. **Run the existing prune** (it had drifted behind the store):
   executed 2026-11-08 — `subsidiary_cycles_dropped=0`,
   `supplier_crossperiod_dropped=10` (supplier_to 17,174 → 17,164),
   `same_group_cycles_dropped=5` (10,814 → 10,809).
3. **Residue is resolver-lane material, not this arc's**: 294 mutuals
   skipped on unresolved filer symbols (`unresolved_symbol_skipped`),
   of which 98 are cross-period artifacts; ~840 no-period mutuals are
   un-decidable by the prune's rule (conservative keep — collapsing
   could destroy genuine two-way evidence). Recorded as
   `xperiod_mutual` in `database_integrity_check.check_relations` —
   REPORT-ONLY (a hard gate would fail on the known resolver residue).
4. The 29 single-orientation flow-rule violations: display-name noise
   territory (96% convention hold rate); left untouched.

**S0-threshold — reconcile the star shapes:**

1. Decide the threshold with the recorded intent as default: implement
   k≥3 for `subsidiary`/`supply` in the star regroup (configurable
   per-family constant in `_STAR_SOURCES`, e.g. `(family, dyad, min_k)`
   triples; `index` stays threshold-free — 57 indexes are each genuine
   standing structure). Rationale: the k≥3 shapes are what every record
   (docstring, comment, hgx proposal §2.5 notes) describes; without the
   threshold, S1 surfaces ~8.6k single-fact buyer stars that duplicate
   the dyadic `e_supplier` facts context_pack already renders, and the
   centrality lanes re-ingest the hub noise the hgx arc's thresholds were
   designed to keep out. Alternative (threshold-less, re-record
   everywhere) is viable but changes 3 documents and adds pack noise —
   not recommended.
2. Re-derive (`derive_hyperedges.py --apply`), then assert shape: per
   family, `MIN(portfolio size) >= k` (or the family is threshold-free),
   labels are buyers/parents (spot-check `supply` labels against dyad
   targets), and hyperedge counts ≈ the k≥3 would-be table in §2
   (683/386 ± dyad drift).
3. Fix the three text surfaces to what actually landed:
   `hyperedge_types.py` docstring, `_STAR_SOURCES` comment, and a dated
   magnitudes line in `doc/design/db_schema.md` §`hyper_edges` (the
   magnitudes home per `ontology.md` §1 — not this proposal's prose).
4. Idempotency proof (feeds acceptance 3): re-run `--apply` → identical
   `hyper_edges`/`hyper_incidences` row counts per family.

### S1 — context_pack.py `_HYPER_FAMILIES` extension

Extend the tuple:

```python
_HYPER_FAMILIES = ("group", "edition", "jv", "index", "subsidiary", "supply")
```

The existing hyper lane (`context_pack.py:472-484`) then renders each
family's fact lines for the seed entity; check the per-family display
label map (if any) gains entries for the three families, and that the
budget/trim ordering keeps hyper facts in the structural band (they are
small post-S0: ≤683 portfolios total, not a firehose). The dyadic
`e_subsidiary`/`e_supplier` `_EDGE_SPECS` rows stay as-is — the star adds
the portfolio framing ("supplier of buyer B alongside N others" context
the dyad rows cannot carry), it does not replace the dyad facts.

### S2 — derive CLI selector + maint step

Add `--families csv` to `derive_hyperedges.py` (default: all families —
today's behavior unchanged; the selector only narrows, validated against
the collected families, cross-family derivations like `sub_sector` run
BEFORE the narrowing). Then add to `TIER1_STEPS` (after `db_maint`,
BEFORE `snapshot` and `graph-rebuild`: the SQLite write must precede the
backup or snapshot_check fails on the drift — the gate arc caught the
first placement doing exactly that — and the cache's `h_edge` mirror
picks the fresh shapes up in the same maint run):

```python
(
    "rederive-hyper (re-derive standing hyperedge families)",
    [
        sys.executable,
        "helpers/graph/derive_hyperedges.py",
        "--apply",
        "--families",
        "index,subsidiary,supply",
    ],
)
```

TIER1 (data-only, idempotent — S0 step 4 proves it, fast: the regroup is
one pass over ~32k star-family dyads). Do NOT touch `_FILE_LANE_STEPS`
(that tuple drives served-store swap handling; `derive_hyperedges`
writes SQLite). Dry-run summary stays the default non-apply behavior.
**Named `rederive-hyper` to stay clear of the existing `make
recompute-hyper`**; it also joins `TIER1_FULL_SKIP` (the `--full` TIER2
lane runs the FULL derive_hyperedges --apply before its own rebuild +
tail snapshot, superseding a filtered mid-run pass).** (the HGX metrics lane over the DEFAULT families — a
different operation). **Convergence rule landed with it:** the threshold
families are delete-then-insert on apply (`apply_hyperedges` is
INSERT OR IGNORE additive-only — without the delete pass, sub-threshold
rows linger forever). **Execution lesson (2026-11-08):** the family
filter must also guard the per-family convergence prunes — a filtered
run's `hyper` lacks the unselected families, and pruning against an
empty wanted-set nukes every stored row of a family the run never
re-derived (found live: a `--families index,subsidiary,supply` run
dropped all 1,167 `event` + 506 `group` rows before the guard landed;
healed by a full `--apply`, prunes now skip unselected families).

### S3 — centrality lanes (opt-in)

Zero new code in `algorithms.py`: the hyper lanes already have the
selector — `hyper_centralities.py --sources` (the `_STAR_SOURCES`
comment's "run --sources deliberately" refers to exactly this flag;
default sources are sector/theme/industry/sub_sector, which do NOT
include the three standing families). S3 is the operator-gated
invocation, wired as a make target:

```make
hyper-star-centrality:  # OPT-IN s-walk centralities over index/subsidiary/supply
    python3 helpers/graph/hyper_centralities.py --apply --sources index,subsidiary,supply
```

The rev-1 idea (a `PPR_INCLUDE_EDGE_TYPES`-style whitelist in
`algorithms.py`) mislocated the mechanism: the dyadic centrality
projection reads `graph_edges` (the star families are not dyadic edges),
while the s-walk lanes read `hyper_edges` and already carry the selector.
Post-S0 shapes only (the threshold is what kept the hgx lanes healthy).

## 4. Acceptance

1. `make lint-audit` green (no new C901/S608).
2. **Dyad hygiene:** prune re-run landed (supplier_to 17,174 → 17,164;
   same_group −5); the new `xperiod_mutual` integrity counter is
   report-only and reads 98 (resolver-lane headroom, documented); the
   writer's two-way emission rule needs no change (targeted: the prune
   re-run reports only known classes).
3. S0 shape assertions hold on the re-derived store — LANDED
   2026-11-08: subsidiary 386 (min 3 / max 228), supply 683 (min 3 /
   max 222), index 57 unchanged; supply labels verified as dyad targets
   (buyers); `hyperedge_types.py` docstring updated to match.
3. `derive_hyperedges.py --apply --families index,subsidiary,supply` is
   idempotent (re-run → identical per-family row counts; the threshold
   families ride delete-then-insert) and the default (no `--families`)
   run is unchanged vs today — LANDED, incl. the convergence-prune guard
   for filtered runs.
4. `context_pack.py "Reliance Industries" --budget 40` output contains
   index-membership / subsidiary / supplier-portfolio facts through the
   hyper lane where present; `tests/test_context_pack.py` additions
   green (per-family fact presence; empty-store graceful).
5. `make maint` runs `recompute-hyper` green (TIER1 step list, served and
   unserved cache postures).
6. Ontology roster-drift check unchanged (no registry change — families
   already in HYPEREDGE_TYPES + doc §2.5).

## 5. Non-goals / risks

- No new dyadic edge types in `_KNOWN_EDGE_TYPES` (these are hyperedge
  families — separate registry).
- No DDL/schema change (`hyper_edges.edge_type` has no DDL CHECK).
- No centrality default change — S3 is opt-in only.
- `signal_sweep` keeps reading dyadic `listed_on_index` (its
  index_membership lane is dyad-shaped by design).
- Risk: S0's threshold decision changes `hyper_edges`/`hyper_incidences`
  row counts (drop ~7.9k supply + ~350 subsidiary rows if k≥3 lands);
  `hyper-centralities`/`hyper-communities` re-run on the next maint and
  their `graph_analytics` rows shift accordingly — expected, data-only.
- Risk: threshold removal was a deliberate choice (not drift). If the
  operator rules the threshold-less store canonical, S0 step 1 flips to
  "re-record everywhere" and the same acceptance items run against the
  threshold-less shapes.

## 6. Gate reference

`doc/procedures/gates.md` full gates ONCE per arc on operator go; fixes
validated by targeted tests only.

## 7. Revival register — preserved from hgx_over_dyads deferral

The `hgx_over_dyads` deferral (2026-09-23 → executed 2026-10-30 #457)
explicitly deferred the subsidiary/supply star families with the note
that both join as standing structure with NO consumer (the deferral
reason). This proposal closes that deferral with S1 (context-pack
consumer) + S2 (maint path); S3 centrality opt-in stays operator-gated.
Rev 2 adds S0 because review (2026-11-08) found the live store diverges
from every recorded shape — wiring consumers before reconciling would
surface unverified structure.
