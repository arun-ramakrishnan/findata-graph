---
title: "Promote dyads into hyperedges — three latent families + the hy-MMSBM NaN-block defect"
status: executed
filed: "2026-10-30"
executed: "2026-10-30"
completed_md: "457"
area: "helpers/graph (derive_hyperedges, hyperedge_types, hyper_communities) + doc/design/ontology.md roster"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Promote dyads into hyperedges — three latent families + the hy-MMSBM NaN-block defect

**Date:** 2026-10-30 · **Status:** EXECUTED (#457) ·
**Area:** `helpers/graph/derive_hyperedges.py` · `helpers/graph/hyperedge_types.py` · `helpers/graph/hyper_communities.py` · `doc/design/ontology.md` §hyperedge roster

## 1. Motivation

The HGX unlock-shelf probe (#451, `archive/graph/hgx_unlock_shelf_probe.md`)
closed the motif lane **NO-GO** on 2026-10-28 with a stated cause: "node
pools (270–1,211) are large relative to edge counts (4–85) → cross-edge
3-node overlaps essentially never occur." This arc re-probed the question
from the other direction — *can the dyadic `graph_edges` table be
promoted into hyperedges that make the gated HGX lanes viable?* — and
found the NO-GO's **mechanism is wrong** while its **conclusion happens
to be right for a different reason**. It also found three families that
are latent in the dyadic store and unregistered, and a live correctness
defect in the `hypermmsbm_community` metric that puts 100 stored
companies (121 on a fresh fit) in a block they do not belong to.

The cost of leaving the defect in place: `/api/graph/metrics/hypermmsbm_community`
serves 8 blocks, and **100 of its 1,210 stored rows carry all-NaN
memberships with `block: 0`** because `np.argmax` on an all-NaN row
returns 0. Block 0 is served as **302 companies of 1,210 (25.0%)** when
only 202 of them are real. That is a silent wrong answer on a live
route, not a missing feature.

## 2. Evidence (measured 2026-10-30, this box, live `memory/research.db`)

### 2.1 The motif NO-GO is blind to the structure it measures

`compute_motifs` begins `edges = hypergraph.get_edges(size=order, up_to=True)`
(`hypergraphx/motifs/motifs.py`) — **it only ever sees edges of size ≤ order**.
Order 3 therefore sees k≤3, order 4 sees k≤4.

| Measure | Value |
|---|---|
| Live `hyper_edges` / `hyper_incidences` | 1,711 / 10,083 |
| Visible to `compute_motifs(order=3)` | 1,191 edges (69.6%) — but only **2,420 incidences (24.0%)** |
| Incidence mass invisible to the census | **76.0%** |
| `event` family share of the visible set | 1,109 / 1,191 = **93.1%**, and it is 100% k=2 |

Hidden by family (edges at k>3): edition 98% (217/222) · sector 95%
(40/42) · theme 100% (12/12) · country 81% · sub_sector 81% · industry
80% · org_membership 76%.

So the 2026-10-28 census measured a set that is 93% *clique-expanded
dyads* — precisely the structure a motif census is supposed to
distinguish itself from — while being blind to three quarters of the
actual incidence mass. **The NO-GO verdict stands** (a production motif
lane is still unjustified), but the recorded cause is wrong and would
mislead the next session into re-probing on "density" when the real
blocker is census visibility.

Two API defects compound it, both worth pinning in a test:

1. The keyword is `runs_config_model`, not `runs`
   (`compute_motifs(hypergraph, order=3, runs_config_model=10, *, seed=None, rng=None)`).
   `runs=10` → `TypeError: compute_motifs() got an unexpected keyword argument 'runs'`.
2. `observed` and `config_model` are **lists of `(motif_tuple, count)`**,
   not the dicts the docstring promises; `norm_delta` is a list of
   `(motif_tuple, float)`. A dict-style consumer dies inside the library:
   `AttributeError: 'list' object has no attribute 'items'`.

### 2.2 Three latent families in the dyadic store

The store already promotes most dyads:
`helpers/graph/derive_hyperedges.py:95-116` declares
`_STAR_SOURCES = [("theme","exposed_to"), ("country","listed_in")]` and
`_SYMMETRIC_SOURCES = [("group","same_group","$.group"),
("edition","co_mentioned_in","$.edition"), ("jv","jv_with","$.venture")]`,
with the regroup at `derive_hyperedges.py:1060-1080`
(`SELECT json_extract(properties, ?) AS label, source AS member FROM graph_edges WHERE edge_type = ? AND label IS NOT NULL`)
and the `quotes` merge into `edition` at `derive_hyperedges.py:1091-1103`.
Three real gaps remain:

| # | family | dyads | → hyperedges | compression | status |
|---|---|---|---|---|---|
| 1 | `listed_on_index` | 6,615 | 57 (all k≥3) | **116.1×** | **no family registered** — `edge_type='index'` returns 0 rows and `index` is absent from `HYPEREDGE_TYPES` |
| 2 | `same_group` (corrected key) | 10,357 | 867 at k≥3 / 481 at k≥5 | 12× | registered, but keyed on the **wrong** field — see below |
| 3 | `subsidiary_of` | 7,419 | 382 at k≥3 (724 parents, max 227) | 19× | tree, unregistered |
| 3b | `supplier_to` | 16,696 | 620 at k≥3 (8,733 target-portfolios, max 222) | 27× | portfolio, unregistered |

**Gap 1 detail.** 57 `entity_type='index'` rows exist and all 57
`listed_on_index` targets resolve to one. k ranges 10–752 (NIFTY TOTAL
MARKET 752, Nifty Smallcap 500 503, NIFTY 500 499, Nifty MidSmallcap 400
399). 1,274 distinct members, 6,615 incidences. **753 members belong to
≥2 indexes and 744 to ≥3** — dense cross-edge overlap, i.e. exactly the
condition the #451 reopen trigger asks for.

The 8 largest indexes exceed `_exclude_degenerate`'s giant threshold
(`max(2.0, 0.25 × 1,274) = 318.5` members) and are dropped from any
community fit: NIFTY TOTAL MARKET 752, Nifty Smallcap 500 503, NIFTY SME
EMERGE 503, Nifty500 Multicap 50-25-25 499, Nifty500 LargeMidSmall
Equal-Cap 499, NIFTY 500 499, Nifty MidSmallcap400 50-50 399, Nifty
MidSmallcap 400 399. What survives is 49 edges / 762 nodes.

**Gap 2 detail — the key is wrong.** `$.group` is populated on only
**34 of 10,357** rows (99.7% NULL), which is why the live `group` family
has 8 hyperedges / 29 incidences. The real set-valued key is
`(source_ref, properties.relationship)` = (filer symbol, RPT class):
**1,548 sets, 11,840 incidences, 867 at k≥3, 481 at k≥5, max 184,
mean 7.6**. `source_ref` alone yields 439 k≥3 hyperedges vs the 8 live.
It is written at `helpers/maintenance/related_party_sync.py:465` as
`f"{SOURCE_REF_PREFIX}:{p['symbol']}"` with `SOURCE_REF_PREFIX = "vigil:rpt"`
(`related_party_sync.py:43`) — the **filer's** symbol, so the honest
label is "everything declared in one company's RPT filing", **not** a
corporate group. Sample refs: `vigil:rpt:SIEMENS` 229 dyads,
`vigil:rpt:TCS` 227, `vigil:rpt:JSWENERGY` 225. Inside
`vigil:rpt:SIEMENS`: 159 Fellow Subsidiary, 65 Fellow Associate, 2
Associate of Ultimate Holding Company, **0 rows carrying `$.group`**.
Relationship vocabulary is dirty: Fellow Subsidiary 1,754 / Fellow
subsidiary 778 (case split) / Promoter Group 661 / Associate 379 /
Fellow Subsidiaries 337 / Fellow Subsidiary Company 258.

### 2.3 Promotion beats the dyadic baseline at equal granularity

Reconstructed `listed_on_index` hypergraph, `_exclude_degenerate` dropping
8 giants, 49 edges / 762 nodes, clique-projected to a graph of 762 nodes
and 97,846 edges (density 0.3375), same incidence for both lanes:

| method | granularity | NMI vs `sector_classification` | singletons |
|---|---|---|---|
| random partition (null) | — | 0.1324 | — |
| Louvain, res=1.0 | 3 comms | 0.0578 | 0 |
| Louvain, res=2.0 | 8 comms | 0.0786 | 2 |
| Louvain, res=4.0 | 733 comms | 0.6734 | **728 (99.3%)** — artifact |
| spectral(16d)+kmeans | 16 | 0.377–0.393 | — |
| **hy-MMSBM k=16** | **16** | **0.4542** | **0** |

The Louvain 0.6734 at res=4.0 is not a competitor: 728 of its 733
communities are single nodes, so it "predicts" by isolating everyone.
At equal granularity hy-MMSBM wins with no singleton degeneracy. Its
block sizes are 196/116/62/49/44/… over 16 blocks, all ≥1.

The spectral row is reproduced in-venv (sklearn is NOT installed, so the
embedding is `A @ V[:, -16:]` plus a hand-rolled k-means, seed 42):
0.3768 on the projected embedding and 0.3931 on the raw eigenvectors.
hy-MMSBM's 0.4542 clears both.

**A circularity trap, recorded so it is not re-discovered.** The same
metric on *sector-named* indexes (Nifty IT, Nifty Bank, Nifty Realty,
Nifty FMCG) reads NMI 0.7153 — but the sector label IS the index name
(100% purity on the top 7 such indexes). Restricted to the 28 **broad
cap/theme-neutral** indexes the honest number is 0.1100 at k=8 and
**0.0000 at k=12 and k=16** (the EM collapses). The all-index figure
0.4542 with giants dropped is the defensible one.

The blocks track sector, not market cap: NMI(blocks, tier) = 0.168 vs
NMI(tier, sector) = 0.036.

Permutation null for 0.4542 (200 shuffles, seed 0): mean 0.1230,
p95 0.1317, max 0.1378, **z=63.5**.

### 2.4 The hy-MMSBM NaN-block defect (live, user-visible)

`helpers/graph/hyper_communities.py` drops singleton *edges* in
`_exclude_degenerate` (its docstring: "Always dropped — a set-valued fact
needs >= 2 members") and the module docstring promises "singletons NaN
the EM". But it never guards singleton **nodes**. Measured on the default
scope `sector,theme,industry,sub_sector`:

| Measure | Value |
|---|---|
| kept hyperedges / entities | 242 / 1,212 |
| nodes with degree 1 in the fitted graph | 207 |
| **`model.u` rows summing to exactly 0** | **121** |
| NaN after `u / u.sum(axis=1, keepdims=True)` | **121** |
| `RuntimeWarning: invalid value encountered in divide` at `hyper_communities.py:135` | emitted every run |

`model.u` itself contains **no NaN** (`np.isnan(m.u).sum() == 0`, zero
`w` NaNs, `u_prior=0.0`); the NaN is created purely by the row
normalisation dividing 0/0. `np.argmax` on an all-NaN row returns **0**,
so `fit_communities` writes `block: 0` and `memberships: {all NaN}`.

Two states must be kept apart, because they differ in magnitude:

| state | entities | zero-row-sum | block 0 served | phantom share of block 0 |
|---|---|---|---|---|
| **stored** (`graph_analytics`, what the API serves today) | 1,210 | 100 | **302** | 100 / 302 = 33.1% |
| **fresh fit** (current store, default scope) | 1,212 | **121** | **204** | 121 / 204 = 59.3% |

Stored block sizes: 0:202 · 1:260 · 2:173 · 3:91 · 4:58 · 5:99 · 6:74 ·
7:153, plus the 100 NaN rows all carrying `block: 0`. `app.py:3519`
(`"hypermmsbm_community": "block"`) reads that int, and the
`isinstance(label, int)` guard at `app.py:3534` **passes** for them, so
the phantom rows are grouped into block 0 and reported as a real
community. `memberships` is not exposed by the route, so the NaN is
invisible to the consumer — only the inflated block size shows.

The fresh fit yields 121 zero rows and 204 hard block-0 assignments, of
which 121 are phantom and 83 real — i.e. the defect currently
manufactures **59.3%** of block 0.

Note the guard's own justification is also imprecise: 119 of the 121
zero rows are degree-1, but **2 are degree-2**
(`Krishca Strapping Solutions` → `sector:Metals` + `theme:Make_In_India`;
`Cigniti Technologies` → `sector:Technology` + `theme:Make_In_India`), so
"degree 1" is not the invariant either — zero row-sum is.

### 2.5 Reopen-trigger status for #451 (measured, so it is not re-probed)

- **Trigger 1 (cross-edge overlap) is MET live.** The doc's own query
  (with corrected table names — it says `h_incidence`/`h_edge`; the live
  names are `hyper_incidences`/`hyper_edges`) gives: edition **714**
  entities in ≥2 edges (455 in ≥3), theme 67/19, event 284/111, and on
  the promoted families `listed_on_index` **753/744** and `same_group`
  903/320. The 2026-10-28 probe recorded 0, i.e. it measured a
  stale/partial store.
- **Trigger 2 (reducibility on >2× edge growth) is NOT met** — all five
  families are at 1.00×. Reducibility reproduces the #451 table exactly
  (edition 0.637 at layer 3, 0.485 at layer 4; industry/sub_sector/
  sector/org_membership ≈ 0.0 at layers 2–6).
- **s-centrality lanes are healthy on `listed_on_index`** (not
  degenerate): `s_betweenness` 232/762 distinct values (30.4%),
  `s_closeness` 183–216/762, `ho_pagerank` 178/762 (23.4%).

## 3. Design

Ordered by cost/benefit. S1 is a correctness fix and should land
regardless of the family work.

### S1 — guard zero-degree nodes in `fit_communities` (correctness)

Before normalising, detect rows whose `model.u` row-sum is 0 and exclude
them from the returned mapping (or assign them an explicit
`{"block": null, "memberships": null, "unassigned": true}` payload).
Preferred: **exclude and report** — a node the EM never saw has no
community, and inventing block 0 is the defect. Report the count on
stderr next to the existing singleton/giant notes so the maint log shows
it. `app.py`'s label-metric branch already skips non-int labels
(`if isinstance(label, int)`), so a `null` block degrades gracefully
there; excluding the row is simpler and leaves `total` honest.

Also correct the module docstring's "singletons NaN the EM" claim to
name the real invariant (zero row-sum → 0/0 → NaN).

Alternatives considered:
- *Add a degree-1 node prune to `_exclude_degenerate`* — rejected: it
  would drop real companies from the fit entirely (they still belong to
  a sector), and 2 of the 121 are degree-2, so degree is not the
  invariant.
- *Add an epsilon to the denominator* — rejected: it manufactures a
  uniform membership (1/K each) that reads as a real soft assignment.
- *Post-hoc relabel NaN rows to the majority block of their largest
  hyperedge* — rejected: a heuristic guess presented as a model output.

### S2 — register `index` as a hyperedge family

Add `index` to `HYPEREDGE_TYPES` (`helpers/graph/hyperedge_types.py`) and
to the roster in `doc/design/ontology.md:165-180` (alphabetical,
insert between `industry` and `jv`), then add the derivation. Two shapes
are possible and the choice matters:

- **(a) star source**, like `theme`: `_STAR_SOURCES += [("index","listed_on_index")]`
  — members are the `source` side, label the index name.
- **(b) symmetric regroup** — rejected: `listed_on_index` is genuinely
  one-directional (company → index), and treating it as symmetric would
  put index entities into their own hyperedges.

Take (a). There is **no DDL CHECK** on `hyper_edges.edge_type` (only
`UNIQUE(edge_type, label)`), so this is a code-registry change with no
schema bump. Two gates must move together:
`helpers/validators/static_checks.py:1848-1857` reads the frozenset, and
`helpers/misc/database_integrity_check.py::check_hyperedge_types`
validates stored rows against it.

**Caveat that decides scope:** 8 of the 57 indexes exceed
`_exclude_degenerate`'s `max_frac=0.25` giant threshold and are dropped
from any community fit. The family is still worth registering for
centrality/query purposes (the s-walk lanes are healthy on it), but do
not expect all 57 to appear in `hypermmsbm_community`.

### S3 — re-key the `same_group` family

Change `_SYMMETRIC_SOURCES`'s group entry from `"$.group"` to a
composite key. Because the honest semantic is "one filer's RPT
declaration", the family should be **renamed or sub-labelled**, not
silently widened — a reader seeing `group:Tata` expects a corporate
group. Original recommendation: register a **new** family
`rpt_disclosure` keyed on `(source_ref, properties.relationship)`, and
leave `group` (the 8 real corporate groups) alone. Normalise the
relationship vocabulary (case folding at minimum: Fellow Subsidiary /
Fellow subsidiary / Fellow Subsidiaries / Fellow Subsidiary Company are
four spellings of one class) before keying on it, else the 1,548 sets
fragment.

> **Operator direction 2026-10-30: widen `group` rather than mint a
> 13th family.** Rationale: the RPT relationship vocabulary is itself
> group-affiliation language ("Fellow Subsidiary", "Promoter Group"),
> the roster/ontology-gate cost of a new family is avoided, and the
> existing `group` family is nearly empty (8 edges / 29 incidences) so
> nothing is displaced. Design implication if adopted: key on
> `source_ref` (the filer's `vigil:rpt:<SYMBOL>`) with the normalised
> relationship class carried in `role`, and KEEP the `$.group`-keyed
> corporate-group edges alongside them — labels do not collide
> (`group:Tata` vs `group:vigil:rpt:TCS`), so the family holds both
> curated groups and declared disclosure sets. The eval-gate bullet
> then covers the widened family as one roster entry. Final go at
> execution, but the arc proceeds on widened-`group` unless the
> operator retracts.

### S4 — tree/portfolio families (`subsidiary_of`, `supplier_to`)

Both are star-shaped (one parent → many children; one buyer → many
suppliers) and both compress ~20×. `subsidiary_of` at k≥3 has 382
parents (max 227) and `supplier_to` 620 target-portfolios (max 222).
Defer behind S2/S3: they add families without adding a *new* consumer,
and the giant-edge threshold will bite on the largest ones.

### S5 — correct the #451 record

Amend `archive/graph/hgx_unlock_shelf_probe.md` and the `pending.md`
"HGX unlock shelf" entry so the motif NO-GO carries the real cause
(census visibility at `get_edges(size=order, up_to=True)`, 24.0%
incidence coverage, 93.1% event-family domination) instead of the
"node pools vs edge counts" claim, and so trigger 1 is recorded as MET
with the 714/753 figures. This is a doc-only slice but it is the one
that stops the next session re-deriving the same wrong answer.

## 4. Acceptance criteria & shakedown

1. **S1:** `helpers/graph/hyper_communities.py --sources sector,theme,industry,sub_sector`
   emits **zero** `RuntimeWarning: invalid value encountered in divide`,
   and `select count(*) from graph_analytics where metric='hypermmsbm_community' and value like '%NaN%'`
   returns **0** after `--apply`.
2. **S1:** a regression test plants a hypergraph containing a node whose
   EM row-sum is exactly 0 and asserts it is absent from
   `fit_communities`' output (and that a normal node's memberships still
   sum to 1.0). Prove it non-vacuous by re-breaking the guard.
3. **S1:** `/api/graph/metrics/hypermmsbm_community` `total` drops by
   exactly the unassigned count (100 stored / 121 fresh), and block 0's
   `size` falls from 302 to 202 on the stored data — no group's `members`
   contains a phantom.
4. **S2:** `derive_hyperedges.py` produces 57 `index` hyperedges with
   6,615 incidences; `check_hyperedge_types` green; the static-checks
   roster and `doc/design/ontology.md:165-180` list the same 13 families.
5. **S2/S3:** `helpers/misc/ontology_eval_gate.py` over the frozen
   question set between dry-run and canonical apply — **mandatory**,
   since both slices change query-visible semantics (roster + hierarchy).
6. **S2/S3:** `make qa` green; `make search-fresh APPLY=1` then plain
   `make search-fresh` rc=0 (doc/ rosters changed).
7. **S3:** relationship case-folding is test-pinned — the four
   `Fellow Subsidiary*` spellings collapse to one class, and the
   pre-fold set count (1,548) and post-fold count are both recorded.

## 5. Non-goals

- **No production motif lane.** The NO-GO stands; S5 corrects its
  stated cause, it does not reopen it. `compute_motifs` is not wired.
- **No `helpers/graph/hyper_motifs.py`.**
- **No new `graph_analytics` metric** beyond correcting
  `hypermmsbm_community`'s existing rows.
- **No re-embedding / vector work** — nothing here touches the embed
  stores.
- **No change to `co_mentioned_in`** — already fully covered by the live
  `edition` family (222 edges, `properties.upstream_types =
  ["co_mentioned_in","quotes"]`); re-confirmed, no gap.
- **`competes_with`** by `properties.subsector` is NOT promoted: only 4
  subsectors, one holding 857 of 864 incidences — degenerate.
- **`invested_in` / `jv_with`** stay dyadic: measured density 0.022 and
  0.012, 21 and 63 triangles respectively — genuinely sparse, not latent
  sets.

## 6. Execution record (2026-10-30)

S1/S2/S3/S5 executed same-day; S4 (`subsidiary_of`/`supplier_to`)
deferred per §3, then EXECUTED 2026-10-31 from a worktrunk session
worktree (first run of the wt_workflow playbook): registered as star
families `subsidiary` + `supply`, applied to the live store —
382 parent portfolios (max 227) + 620 buyer portfolios (max 222);
registry-wiring invariants pinned in
`tests/test_star_family_registry.py`.

| Slice | Result (measured, live store) |
|---|---|
| S1 zero-row-sum guard | 110 nodes excluded on the fresh fit (2 of them degree-2 — the docstring's degree-invariant claim was wrong, as suspected); no RuntimeWarning (zero rows are never divided); `hypermmsbm_community` write is now replace-on-write, so the store reads **0 NaN rows / 1,102 total / block 0 = 127 real** (was 100 NaN phantoms / 302 block-0 / 1,210 rows) |
| S2 `index` family | registered + applied: **57 hyperedges / 6,615 incidences** (exactly the §2.2 figures); roster + `HYPEREDGE_TYPES` + static-checks + `check_hyperedge_types` all green |
| S3 widened `group` | operator direction implemented literally — **filer-keyed**: 498 disclosure sets / 10,738 incidences; `normalise_relationship` case-folds the four `Fellow Subsidiary*` spellings (test-pinned); fold provenance 1,545 → 1,450 (filer, class) pairs; roles = dominant folded class (~3% multi-class pairs ride direction-swapped dyads). Class-split labels (one per filer:class, exact roles) remain a one-line keying flip — interpretation recorded in `doc/design/ontology.md` §2.5 |
| S5 #451 record | corrected in `archive/graph/hgx_unlock_shelf_probe.md` + `pending.md`: real cause = `get_edges(size=order, up_to=True)` visibility (24.0% incidence coverage, 93.1% event-family); trigger 1 recorded MET (edition 714/455, listed_on_index 753/744, same_group 903/320); NO-GO stands |

Eval gate ACCEPT (173 questions, 0 reasons). By-catch: the coverage
ledger `amend` path could not restamp fingerprints — validator defect
fixed + pinned; quack stop/start + `graph-rebuild` required around the
store writes (`GraphCacheStaleError` after entity/edge changes).

## Appendix A — raw probe log

Scratch transcripts from this arc (this box, 2026-10-30):
`/mnt/data/tmp/hgx_rel.txt` (RPT key counts), `hgx_rel2.txt` (NMI on the
RPT family), `hgx_reduc.txt` (trigger-2 re-run), `hgx_normdelta.txt`
(per-family norm_delta), `hgx_blind.txt` (census visibility),
`hgx_trig1.txt` (trigger-1 quick check), `hgx_circ.txt` /
`hgx_noncirc.txt` / `hgx_sect.txt` (circularity controls),
`hgx_verify.txt` (baseline comparison), `hgx_nan*.txt` (defect
isolation), `hgx_sc*.txt` (`graph_analytics` inventory).
Consolidated verdicts also in `/mnt/data/tmp/evals.txt`.

`graph_analytics` inventory for context (19 metrics): the HGX lanes are
`ho_pagerank` 1,210 rows / 452 distinct · `s_betweenness` 1,210/479 ·
`s_closeness` 1,210/475 · `hypermmsbm_community` 1,210 rows / 9 distinct
values (8 blocks + the all-NaN phantom). `local_clustering_coefficient`
has **1 distinct value across 1,209 rows** — degenerate, worth a separate
look but out of scope here.
