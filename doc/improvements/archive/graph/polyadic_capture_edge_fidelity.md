---
title: "Polyadic capture + edge fidelity — feed the hyperedge families, persist evidence, retire the edition clique"
status: executed
filed: "2026-11-06"
executed: "2026-11-07"
completed_md: "486+490"
area: "helpers/graph (derive_events, derive_event_participants, extract_relations, name_dedup, edge_projection, derive_co_mentions) + app.py integrity checks"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Polyadic capture + edge fidelity — feed the hyperedge families, persist evidence, retire the edition clique

**Date:** 2026-11-06 · **Status:** EXECUTED 2026-11-07 ·
**Area:** `helpers/graph/derive_events.py` · `helpers/graph/derive_event_participants.py` · `helpers/graph/extract_relations.py` · `helpers/graph/name_dedup.py` · `helpers/graph/edge_projection.py` · `helpers/graph/derive_co_mentions.py` · `app.py`

## 1. Slices

Five executable slices plus one parked probe, in priority order. All
live counts below were measured 2026-11-06 against
`memory/research.db` (read-only); every slice re-measures its own
baseline before touching data.

### S1 — N-ary event participants (the polyadic feed) — EXECUTED 2026-11-07

**Problem.** The role-bearing hyperedge model is proven but starving:
**exactly one k≥3 transactional hyperedge exists in the whole DB**
(`merger: LTM`). All 1,099 event hyperedges are k=2; `events` carries
only `entity` + `counterparty_entity`, so `collect()` can never emit a
third participant.

**Executed.** Injection boundary resolved: extraction in
`derive_events` (two new prose extractors — `_extract_acquisition_triangle`
with voice-correct roles: passive "Acquired by X from Y" makes the note
subject the target, active "acquiring … from Y" makes it the acquirer;
`_extract_jv_consortium` with lead/partner shapes "led by"/"X-led"/
"as the lead"), resolution via `EntityResolver(allow_fuzzy=False)` —
resolve-only, no mint — payload rides `events.properties.participants`
(no DDL); projection in `derive_event_participants.collect()` (payload
SUPERSEDES the pair shape; rows only for resolved names that exist —
defense in depth beyond the FK); roles flow to incidences via a
`collect_event_facets` extension reading the store rows. Roster:
`lead_partner`, `seller` added to the `event_roles` block in the same
change. `EntityResolver` gained `allow_fuzzy` (default True — no
behavior change for counterparty promotion).

**Census-first (operator-agreed gate) paid for itself twice:** the
first census measured 29 ≥3-participant candidates dominated by noise
— fuzzy mis-resolutions ("Tata Group"→"Tata Consumer Products UK
Group"), country entities as partners ("India"→india), lowercase noun
phrases as sellers (IGNORECASE leak), header spans. Fixes: strict
resolver tier, country/structural denylist, case-sensitive spans,
expanded stopwords, per-entity dedup, 7-span cap. Hardened census:
**11 events with ≥3 distinct resolved entities, all real-world
verifiable** (Ambuja←Adani←Holcim; Eicher+VECV+AB Volvo from both
sides; the Brakes India/Wheels India/TSF/Bosch cluster; the ICICI/Prudential
and Bajaj/Allianz JVs). Operator approved apply.

**Gate + apply.** Eval gate parent-vs-candidate: **ACCEPT — 173
questions, 0 reasons** (the frozen set does not yet pin event-family
membership for payload events — purely additive; add questions at the
next rebaseline). Canonical apply: 156 new events, 2815 participant
rows, event family +75 hyperedges/+164 incidences.

**Acceptance.**
- ✅ **11 NEW k≥3 event hyperedges** (8× k=3, 3× k=4; e.g.
  `Adani Enterprises (acquirer) + Ambuja Cement (target) + Holcim
  (seller)`) — LTM stops being the only one. Before: 1,099 event
  hyperedges all k=2; after: 1,161 k=2 + 11 k≥3.
- ✅ Zero new entities (payload rows land only on existing entities;
  guard test asserts the entity count is unchanged).
- ✅ Zero dangling incidences (measured 0).
- ✅ Eval gate ACCEPT.
- Roles tagged on the new incidences (acquirer/target/seller/
  lead_partner/partner) — the incidence NULL-role share drops for the
  event family.

### S2 — Supply k=1 counterparty triage (the shadow ledger)

**Problem.** 7,521 of 8,733 supply hyperedges (86%) are k=1: one
registered member among 538 distinct real entities, plus a free-text
buyer label ("100% foreign owned subsidiary Siemens Ukraine",
"4M Propack", "A R Landcraft LLP" — unlisted subs, LLPs, foundations).
The "supply star compression" story (620 portfolios, Reliance
Industries 222 suppliers) applies to 7% of the family.

**Change.** One-time curated census of the top ~200 distinct buyer
labels: operator rules each label alias-to-entity (promote to real
k=2 fact), explicit non-entity (keep as label, tag
`properties.counterparty_class`), or merge-duplicate (same label,
different spelling). Manual curation only — this is the exact lane
the 2026-10 hardening closed; no fuzzy matching, no auto-accept.

**Acceptance.**
- Census artifact in `outputs/supply_k1_census.md` with per-label
  rulings; promotion re-runs idempotent.
- k=1 share of the supply family re-measured and reported (target:
  materially below 86%; no forced target — the census tells us how
  many labels deserve entities).
- Zero word-overlap/fuzzy alias candidates auto-accepted (guard test
  mirrors `tests/test_fuzz_relations_aliases.py`).

**EXECUTED 2026-11-07** (20 operator batches of 10; plan artifacts
`findata/Misc/_name_merge_plan_s2_supply_k1.*`, folds appended to the
DEFAULT `_name_fold_ledger.jsonl`).

Measured twist vs the proposal text: the buyer labels are **already
registered entities** (7,412/7,413) — the "free-text label" framing
predates the pending-triage promotions. So the census ruled *entities*:
all 200 top-ranked labels were `alias:` folds into their canonical
parent (47 clusters; zero `keep`, zero `non-entity` — every top-200
label was a subsidiary/spelling duplicate). Notable operator calls:
word-overlap noise candidates overridden 4× (#31 → Samvardhana
Motherson International, #59 → Waaree Energies, #27 → JSW Steel,
\# 87 → Berger Paints India); the Hitachi Energy overseas family (13
mangled `Name, location` labels) folded into **Hitachi Energy (global)**,
not the census-scored Hitachi Energy India; the 12-entity Anand Rathi
family's umbrella + IT arm folded into Anand Rathi Wealth; JVs folded
per the small-scale ruling (Maruti Suzuki Toyotsu, Dilip
Buildcon-Varah ×2 spellings, Asian Paints PPG).

Acceptance:
- ✅ Census artifact `outputs/supply_k1_census.md` (200 ruled rows +
  backfill-evidence table) and the signed plan artifacts.
- ✅ k=1 share re-measured: 7,413 → **7,222** k=1 (84.2% → 83.9% of
  8,604; the proposal's 86% baseline predates the S4-era rebuilds).
  Honest yield: the top-200 surface was subsidiary duplicates, not
  alias fragmentation — the remaining long tail (5,351 tier-C buyers,
  deg ≤ 3) is untouched and now census-ranked for any future pass.
- ✅ Zero auto-accept: the only write path is `name_dedup.apply_plan`
  (`--yes` gate, signed artifacts); guard tests added
  (`tests/test_name_dedup.py::TestS2FoldLedgerInvariants`, live-marked:
  no self-folds/dangling canonicals in the ledger, S2 variants gone,
  canonicals alive).
- ✅ FK violations 0, `integrity_check` ok, 0 dangling incidences;
  graph-rebuild + stamp-centrality re-run; backup
  `db-backup/research_backup_pre_s2_2026-11-07.db`.

Two structural findings recorded for follow-up:
1. **Star-family zombie prune is S2-owned**: `derive_hyperedges`' star
   apply path is deliberately additive ("compaction belongs to a future
   full-refresh mode"), so folded buyer labels left 20 zombie supply
   hyperedges (label gone, supplier incidence alive) after the first
   two batches. The arc added a precise prune (plan-variant labels
   only); a general post-fold stale-label prune belongs in the
   full-refresh mode.
2. **Missing-tree-edge backfill evidence**: 109 of the 200 folded subs
   carried NO `subsidiary_of` edge to their canonical at fold time
   (HCL's entire overseas tail, the Hitachi mangled family, GE
   Switzerland/Tech GmbH, …) — the corpus never yielded the parent
   mention. The per-batch table in the census artifact is the restore
   recipe; it rides the S3b standing-edge backfill rider.

### S3 — Persist evidence and time on accepted relations

**Problem.** `confidence` survives on **6 of 71,940 edges (0.008%)**
and 0 hyperedges; sidecar promotion drops `as_of`/`confidence`
(`extract_relations.py` promote path keeps only
doc_type/b2_version/provenance, writes `valid_from=None`).
`valid_from` is missing exactly where transactions live: jv_with
2/1,111, rated_by 0/218, regulated_by 0/17, approved_by 0/4,
customer_of 0/1, acquired 30/57. (`source_ref` is ~100% — keep.)

**Change.** Promotion maps sidecar `as_of` → `valid_from` (or
`properties.as_of` when the column must stay NULL), and copies
`confidence` into `properties.confidence`. Add an integrity check
(edges carrying a sidecar provenance must have either `valid_from`
or `properties.as_of`) to the standing gate suite.

**Acceptance.**
- Re-measure: confidence-bearing edges and valid_from coverage on the
  six transactional types before/after; jv_with/rated_by backfill
  from surviving sidecars where re-derivable.
- New integrity check green in `make integrity` (or the repo's
  standing equivalent) on both pristine and populated DBs.

### S3b — Standing-edge evidence backfill (FILED 2026-11-07 — EXECUTED 2026-11-07, same day)

**Problem (measured post-S3, post-S4).** S3's promotion-path fix covers
edges written from now on; the standing edges keep their NULL
`valid_from`. The dates survive in `properties` for most of them:
all 214 `rated_by` edges carry `properties.date_of_rating` (verified
during the S4 census — every edge printed a date, `valid_from=None`);
`as_of` exists on acquired 30/57 and jv_with 2/1,111; regulated_by 17,
approved_by 4, customer_of 1 carry nothing derivable. S3's own
backfill flag (sidecar re-derive) was scoped to surviving sidecar
files only — it does not read `properties`.

**Change.** One idempotent backfill pass over standing edges:
`valid_from ← properties.date_of_rating` (rated_by; the property is
the canonical action date) and `valid_from ← as_of` where the column
is populated and `valid_from` is not — never overwriting an existing
`valid_from`, never guessing where no date exists (regulated_by /
approved_by / customer_of stay NULL; no fake evidence — D-O3). Plain
`UPDATE ... WHERE valid_from IS NULL AND properties` — no
delete+reinsert. Dry-run default with a count preview, `--apply` to
write; reuses the S3 follow-up hook point in `extract_relations.py`
(backfill flag) extended with the properties source.

**Acceptance.**
- rated_by `valid_from` coverage: 0/214 → ≈214/214 (exact count
  printed before/after); acquired/jv_with as_of rows folded in.
- Idempotent: second run writes 0.
- S3's sidecar-provenance integrity check stays green (every
  sidecar-provenance edge then has `valid_from`).
- No other column changes; confidence stays where it is.

**Trigger/priority.** Small and mechanical — ride it into the S1 arc
(or any db-touching pass) rather than a standalone session.

**Executed (rode the S2 arc's db-touching session).** Live re-measure
corrected the filed numbers before any write: rated_by carries
`date_of_rating` on **211/214** (not 214 — the 3 gaps are Muthoot
Capital/Muthoot Finance/Motilal Oswal, whose prose genuinely never
stated a date); acquired 30 and jv_with 3 were ALREADY backfilled by
S3's promotion path; regulated_by/approved_by/customer_of carry
nothing derivable (stay NULL — D-O3 no-guessing).

Two plain idempotent UPDATEs (no tooling needed — the SQL is the
record):
- `rated_by.valid_from ← properties.date_of_rating` (all 157 distinct
  values ISO `YYYY-MM-DD`, matching the column's existing convention) —
  211 rows written.
- one manual `subsidiary_of` edge (Kotak Mahindra Life Insurance →
  Kotak Mahindra Bank, `manual:user:2026-08-25`) carried
  `properties.date` — same evidence→valid_from spirit, 1 row written,
  flagged here as the only scope extension.

Acceptance:
- ✅ rated_by coverage 0/214 → **211/214** (exact; the 3 stay NULL —
  no fake evidence); coverage table printed per type.
- ✅ Idempotent: re-run writes 0 (both predicates).
- ✅ `database_integrity_check.py` 100% (S3's sidecar-provenance check
  scoped to `promote:sidecar:%` — untouched rows; green).
- ✅ FK violations 0, `integrity_check` ok; graph-rebuild +
  stamp-centrality re-run (valid_from rides the materialization);
  backup `db-backup/research_backup_pre_s3b_2026-11-07.db`.

**Rider scope correction (recorded, from the S2 arc):** the S2
subsidiary_of restore this rider was to carry does NOT work as filed —
the 109 folded variants no longer exist as entities, so minting
`variant→canonical` is a self-loop FK trap. What S3b carries from S2 is
the date backfill above plus the census artifact's per-batch table as
PROVENANCE (a re-mint-the-subsidiaries arc would consume it, should one
ever be wanted).

### S4 — Institution canonical fixes (surgical) — EXECUTED 2026-11-07

**Problem.** `Acuite Ratings & Research` AND `Acute Ratings &
Research` both exist as institution entities (typo-variant
near-duplicate); rating agencies are inconsistently typed. The 218
rated_by edges + 17 regulated_by + 4 approved_by depend on these
targets.

**Executed (operator rulings 2026-11-07, live census first).**
- **Ruling 1 — per-family typing, NOT all-institution:** the census
  showed CRISIL / ICRA / CARE Ratings are LISTED companies (ISINs,
  notes, Capital_Markets sector + Ratings sub_sector memberships) —
  flipping them to institution would break
  `sector_membership_endpoint_bad`, drop them from `v_company`/`e_*`
  projections and the embedding lane, and orphan their notes. The
  proposal's literal "one entity_type for the 9 agencies" is
  superseded: consistency = **one entity per agency family**, type
  follows the entity that exists.
- **Ruling 2 — Acute → Acuite** (1 edge; footprint was rated_by-only,
  so `name_dedup.apply_plan`'s graph_edges re-pointing covered it).
- **Ruling 3 — CRISIL Ratings → CRISIL full merge** (57 rated_by
  edges; footprint also held `subsidiary_of` + reciprocal
  `supplier_to` edges + 2 incidences — real legal-entity facts from
  CRISIL's own RPT filings; operator accepted the self-loop losses
  with full knowledge). Applied via the gated `apply_plan` path
  (signed plan artifact `findata/Misc/_name_merge_plan_s4_agencies.*`):
  54 rows re-pointed, 7 collisions consolidated, 2 entities folded,
  `zero_loss_ok: True`, folds appended to the default
  `_name_fold_ledger.jsonl`.
- **Re-mint guard:** `related_party_sync.apply_rating_pairs` now
  resolves agency display names through `_fold_map()` BEFORE the mint
  check — the vigil CSV spelling ("CRISIL Ratings Limited",
  "Acute…") can no longer resurrect a merged-away entity
  (`test_folded_agency_resolves_not_reminted`).
- **Census artifact:** `outputs/institution_ed1_census.md` — 7 ED-1
  pairs remain (1 real candidate `SBI Mutual Fund` ↔ `SBI Mutual
  Funds`, 6 Sub-Total debris rows; debris + 43 truncation-capped
  names flagged report-only for a future sweep).

**Acceptance.**
- ✅ Post-merge targets: 9 distinct (CRISIL 57, CARE Ratings 47,
  ICRA 42, India Ratings 41, RBI 17+4, Brickwork 11, Acuite 8,
  Infomerics 8); zero residual references to folded names anywhere.
- ✅ Typing consistent per family (listed = company, unlisted agency =
  institution, RBI = institution).
- ✅ Integrity check 100% validation rate post graph-rebuild +
  stamp-centrality.

### S5 — Retire the edition clique (incidence only) — EXECUTED 2026-11-07

**Problem.** Duality: 9,638 `co_mentioned_in` dyads coexist with
225 edition hyperedges (3,038 incidences). Sectors already got
clique-retirement (`edge_projection.py` two-way part_of/has_company
only); editions did not. 3.2× row reduction available, and the
dyads misrepresent shared-edition context as company-to-company
relations.

**Change.** Stop materialising `co_mentioned_in` pairs into
`graph_edges`; serve co-mention views from the edition incidence
family on demand (the hyper-neighbor API already returns top
co-members). Keep a projection helper for consumers that genuinely
need pairs.

**Acceptance.**
- `co_mentioned_in` absent from `graph_edges` after re-derive;
  edition family unchanged (225/3,038 + re-measure).
- Eval gate: ontology_eval_gate between dry-run and apply
  (roster/visibility change). Any PPR/embedding consumers keyed on
  co_mentioned_in re-pointed or re-run in the same change (note:
  the 2026-11-06 ppr_results projection already dropped
  co_mentioned_in volume — verify no other consumer).

**EXECUTED 2026-11-07** (sector-retirement pattern end-to-end; backup
`db-backup/research_backup_pre_s5_2026-11-07.db`).

Implementation (the part_of/has_company playbook, replicated):
- **Writer flip** (`derive_co_mentions.py`): the newsletter lane stages
  per-edition participant SETS into a new `co_mention_editions` table
  (one row per edition × company — the C(n,2) pair blowup dies at the
  source); `apply_edges` (dyad INSERT) RETIRED with a loud stub;
  `derive_edges` survives as pure pair math for parity tests.
- **Regroup drop** (`derive_hyperedges.py`): edition left
  `_SYMMETRIC_SOURCES` (the sector-precedent deletion that makes the
  store canonical); collect() unions `co_mention_editions` + quotes
  (S8). `_upstream_types("edition")` → `newsletter_co_mention+quotes`.
- **Cache compatibility projection** (`query.py`):
  `_materialise_co_mention` builds `_comention_resolved` from the
  edition family (ONE aggregated row per unordered pair,
  `weight = n_shared_editions + 1` — the retired count+1 law), merged
  into `_edge_resolved` and the walk substrate. Every cache consumer —
  onager prediction/economic projections, link prediction, Mojo parity
  bench, igraph/scipy bridges, `e_comention` API — kept working with
  ZERO changes. Leftover dyad rows are DELETEd from the resolved base
  (mid-migration double-count guard).
- **sqlite-side consumers re-pointed** to
  `project_co_mention_pairs()`: semantic_peer derivation
  (derive_relational_insights), enrich_relations ranking, metapath
  attention family. analytics' `coment` stat reads the graph_edges
  parquet — now 0 by design (documented, not re-pointed).
- **Eval gate extension** (`ontology_eval_gate.py`): new
  `--expect-shrink` for semantic re-bases — the declared loss IS the
  materialization; gained rows still regress (the gate previously only
  admitted gains). Gate: **ACCEPT — 173 questions, 0 reasons**
  (declared shrinks: `edge-hdfc-amc-co-mentioned-in`,
  `agents-graph-edges`).

Acceptance:
- ✅ `co_mentioned_in` ABSENT from graph_edges: 9,638 → 0; graph_edges
  71,291 → 61,653 (exact −9,638).
- ✅ Edition family unchanged-in-shape, grown by the staging union:
  225/3,038 → **294 hyperedges / 3,441 incidences** (+69/+403 — the
  newsletter-only editions that the dyad regroup used to collapse into
  shared labels). Stored capture rows: 9,638 dyads + 3,038 incidences
  → 3,441 incidences (~3.7× reduction).
- ✅ Semantic widening (documented): the pair view now covers the WHOLE
  edition family — quote-participation editions project pairs too, so
  the served view is 11,315 pairs (was 9,638 newsletter-only), weights
  2–12. sqlite-side helper and DuckDB cache agree exactly (11,315 =
  11,315). "Shared edition" is the context the dyads misnamed
  company-to-company relations; the projection makes that honest.
- ✅ Integrity 100%, FK 0, `integrity_check` ok; graph-rebuild +
  stamp-centrality; targeted suites green (25 co-mentions + 12
  sql_query + 97 api_unit + 80 hyper/integration + consumers).

### S6 (parked) — Buyer-side supply facts

`customer_of` has 1 edge (Indian Oil Corporation) vs 17,557
`supplier_to`. The reverse fact is never extracted. Parked: no
corpus evidence yet that buyer-perspective text exists at volume;
probe 50 editions for "customer of / key customers" sections before
slicing.

## 2. Evidence (measured 2026-11-06, live `memory/research.db`, read-only)

### 2.1 Layer sizes

| layer | count |
|---|---|
| entities | 24,928 (company 22,459 · person 1,522 · institution 536 · structural 411) |
| graph_edges | 71,940 across 18 types |
| hyper_edges | 11,691 / 51,554 incidences across 15 families |
| events | 1,488 (jv 1,083 · guidance 345 · acquisition 57 · mgmt_change 3) |
| event_participants | 2,547 (partner 2,093 · issuer 337 · target 57 · acquirer 57 · subject 3) |

### 2.2 graph_edges by type

supplier_to 17,557 · same_group 10,968 (sym) · co_mentioned_in
9,638 (sym) · semantic_peer 8,917 (sym; was 25 on 2026-10-25 —
index-era counts drift, always re-measure) · subsidiary_of 7,658 ·
listed_on_index 6,615 · competes_with 3,577 · invested_in 2,130 ·
cited_in 1,999 · jv_with 1,111 · listed_in 930 · exposed_to 359 ·
rated_by 218 · belongs_to 184 · acquired 57 · regulated_by 17 ·
approved_by 4 · customer_of 1.

### 2.3 Hyperedge families and k-distribution

| family | n_he | k=1 | k=2 | k≥3 | maxK | avgK | incidences |
|---|---|---|---|---|---|---|---|
| supply | 8,733 | 7,521 | 592 | 620 | 222 | 1.91 | 16,696 |
| group | 506 | 0 | 66 | 440 | 230 | 21.27 | 10,765 |
| subsidiary | 724 | 271 | 71 | 382 | 227 | 10.25 | 7,419 |
| index | 57 | 0 | 0 | 57 | 752 | 116.05 | 6,615 |
| edition | 225 | 1 | 0 | 224 | 35 | 13.50 | 3,038 |
| event | 1,099 | 0 | 1,099 | 0 | 2 | 2.00 | 2,198 |
| sector | 42 | 0 | 1 | 41 | 99 | 28.88 | 1,213 |
| industry | 119 | 21 | 12 | 86 | 52 | 8.50 | 1,012 |
| sub_sector | 108 | 16 | 12 | 80 | 51 | 9.25 | 999 |
| country | 21 | 11 | 3 | 7 | 850 | 44.29 | 930 |
| theme | 12 | 0 | 0 | 12 | 89 | 29.92 | 359 |
| org_membership | 34 | 4 | 1 | 29 | 32 | 8.44 | 287 |
| jv | 6 | 0 | 6 | 0 | 2 | 2.00 | 12 |
| merger | 3 | 0 | 2 | 1 | 3 | 2.33 | 7 |
| demerger | 2 | 0 | 2 | 0 | 2 | 2.00 | 4 |

Roles are NULL on 95.2% of incidences (49,058/51,554) — only
event/merger/demerger carry roles today.

### 2.4 Endpoint integrity (clean — keep it that way)

Zero dangling sources or targets across every typed relation edge,
verified per-type (acquired, jv_with, subsidiary_of, competes_with,
invested_in, rated_by, regulated_by, approved_by, customer_of,
supplier_to, exposed_to, cited_in). RBI exists as an institution
entity; regulated_by/approved_by targets resolve. S1's "unresolved
participants stay as labels" rule exists to preserve this invariant.

### 2.5 Metadata coverage

| type | n | valid_from | props | source_ref |
|---|---|---|---|---|
| jv_with | 1,111 | 2 | 1,101 | 1,111 |
| rated_by | 218 | 0 | 218 | 218 |
| regulated_by | 17 | 0 | 17 | 17 |
| approved_by | 4 | 0 | 4 | 4 |
| customer_of | 1 | 0 | 1 | 1 |
| acquired | 57 | 30 | 50 | 57 |
| subsidiary_of | 7,658 | 7,347 | 7,653 | 7,658 |
| supplier_to | 17,557 | 15,997 | 17,555 | 17,557 |
| competes_with | 3,577 | 3,522 | 3,470 | 3,577 |
| invested_in | 2,130 | 2,130 | 2,130 | 2,130 |

`confidence` in properties: 6 edges total, 0 hyperedges.

### 2.6 Geographic skew (recorded, not sliced here)

listed_in: india 850/930 = 91.4% (usa 41, uk 7, south_korea 4,
japan 4, france 4, china 3, then 1–2s); country hyperedge family
mirrors exactly. This is corpus acquisition composition, not a
resolver or extractor defect — fixing it by matching would
manufacture noise. Out of scope; revisit with source-diversity
targets.

### 2.7 Scale posture (tripwire audit, 2026-11-06 — no scale work needed)

`helpers/validators/graph_scale_watch.py` run against the live store
the same day as the measurements above:

| axis | live | warn / fire | verdict |
|---|---|---|---|
| e_all_und | 148,732 doubled rows (74,366 directed) | 500K / 1M (T1) | OK — 0.15× T1 |
| h_incidence | 51,554 | 100K / 250K (exit 1) | OK — 0.52× warn |
| max hyperedge | 850 (country star) | 1,000 (report-only) | OK |
| clique tax | 2,453,321 dyads | 3.4M (report-only) | watch, see below |
| chains cap | 19,347 edge-touched vs 6,000 | sampled lower bounds by design | OK — exact on demand (~80 s / ~6 GB) |

- The only axis with a measured wall behind it is the clique tax:
  `helpers/bench/hyper_scale_bench.py` (2026-09-13) recorded dyadic
  Louvain finishing 2.85M dyads in 142.7 s and OOMing (>440 s) at
  3.42M on the 14 GB box — the live tax sits at 72% of that wall. The
  tax is driven by large membership stars, not editions: the
  850-member country star alone is ~361K projected dyads (C(850,2)),
  index stars average k=116 (max 752), group stars average k=21,
  while all 225 edition stars max out at k=35. Mitigation already
  shipped: `helpers/graph/bipartite_communities.py` reaches ARI
  parity running on incidences directly (Wall-4 probe, 2026-11-02).
  Standing rule: community analytics stay hyper-native; never
  dyadic-Louvain the full graph. Nothing here grows the tax (S1's
  new k≥3 event stars add tens of incidences, not thousands).
- What S5 does cut is the dyadic row budget: retiring 9,638
  symmetric `co_mentioned_in` edges removes 19,276 doubled rows from
  `e_all_und` (148,732 → ~129,456, −13%), pushing the T1 ladder out.
- T1 ladder math (`doc/local/perf/graph_scaling.md`): graph growth is
  event-driven — 3 bursts account for 74% of V / 68% of E; one
  VIGIL-class intake (2026-09-23) added +19,338 V / +39,077 E in a
  single day. `e_all_und` went 114,500 (09-26) → 148,732 (11-06),
  i.e. roughly 11 VIGIL-class intakes of headroom remain before T1.
  Layout ceiling is already lifted to 50K nodes via anchored FA2
  (22,054-node live solve in 80.9 s).
- The perf gate is not scale-limited: the recent graph-leg failures
  (runs 3451, 3438, 3361) are all `FAIL(contended)` — machine
  contention during parallel qa+perf — with medians inside budget
  (eigenvector 1.25 s/2.0 · link_prediction 3.06 s/8.0 · MST
  0.72 s/2.0 · matching 1.15 s/3.0 · hyper_derive 1.39 s/4.0 ·
  louvain 1.89 s/4.0 · BFS 9.08 s/12.0). Tightest real margin:
  `graph_rebuild` median 3.87 s, p75 4.08 s vs a 5.0 s budget — the
  leg to watch at the next big intake. Per gates doctrine, contended
  timing failures stay parked, no reruns.
- `semantic_peer` 25 → 8,917 is the E3 embedding lane landing, not
  runaway growth: `build_semantic_peer_edges`
  (`helpers/maintenance/enrich_relations.py:924-1001`) takes
  per-company top-k=10 cosine from DuckDB VSS with same-QID
  suppression and an idempotent DELETE-by-prefix rebuild — bounded
  at ~10 edges per company by construction.

Verdict: no vertex/edge scale problem needs a slice now — every
tripwire is green, and both graph-touching slices here (S1, S5) move
the graph away from the only wall in sight.

## 3. Design notes

- **S1** follows the LTM existence proof: the storage, roles, and
  derivations already handle k≥3 — only extraction gates it. Extend
  `derive_events` to accept a participants list (name, role,
  resolved?) per event; `derive_event_participants` projects
  resolved ones to incidences and keeps unresolved ones in the
  event's evidence payload. Roles vocabulary extends beyond
  acquisition/jv/guidance/management_change only with ontology.md
  roster updates in the same change.
- **S2** reuses the triage-tooling pattern from
  `archive/graph/pending_relations_triage.md` (menu buckets +
  operator rulings), not the fuzzy suggester. The word-overlap guard
  and discard-persistence gate (`relation_noise.json`) stay
  authoritative.
- **S3** touches only the promotion writer in
  `extract_relations.py` (:2954-3022 area) plus a backfill flag;
  no schema change (confidence rides in `properties`).
- **S5** mirrors the sector precedent: incidence canonical, pairs
  projected. Check `ppr_results` projection config (already
  co_mentioned_in-free per 2026-11-06 #484) and any embed questions
  referencing the type.

## 4. Constraints and non-goals

- **No fuzzy entity-resolution expansion.** Operator ruling
  (2026-10-15 hardening S1–S8; word-overlap alias guard; entity-name
  dedup): past broader matching produced noise that required
  dedicated cleanup (1,575 collision clusters, 11.2% of edge rows).
  Every slice here works on existing canonical entities or
  operator-ruled manual aliases only.
- **No new hyperedge families.** The registry is complete for
  current facts; the gap is capture feeding existing families.
- **No layout/algorithm change.** HGX assessment doctrine: layout
  and shortest paths run on dyadic projections; hyperedge UI work
  (virtual fact nodes) is a separate presentation-track item, not
  this proposal.
- **group family stays labeled as filing/RPT groupings** (DQ
  2026-10-30: filer-keyed sets, not corporate groups) — nothing here
  relabels it.

## 5. Gates

- Full arc per `doc/procedures/gates.md`: `make qa` once, fixes
  validated by targeted tests only.
- Eval gate (S1, S5): `helpers/misc/ontology_eval_gate.py` over the
  frozen question set between dry-run and canonical apply.
- Integrity additions (S3) join the standing integrity suite.
- Each slice ships with a before/after count print; index-era counts
  are treated as stale until re-measured (semantic_peer 25 → 8,917
  between 2026-10-25 and 2026-11-06 shows the drift scale).

## 6. Grounded JSON schema (revision, 2026-11-06)

The earlier pasted draft schema (path / materiality / coverage / claim_type axes) was **not grounded** in repo vocabulary — all enum tokens (`small_l-scale`, `distant_minor`, `full_but_less_than_2pi`, `1_of_each_omitted`, `no_value_repeated`, `claim_type`, `godpdl`) returned zero matches across the repo. This section replaces it with a machine-checkable schema that references ONLY the live registries.

### 6.1 Schema document

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "findata-graph://polyadic_capture_edge_fidelity/grounded.v1.json",
  "title": "Polyadic capture + edge fidelity — grounded vocabulary",
  "type": "object",
  "required": [
    "relation_family",
    "k",
    "roles",
    "resolution",
    "evidence"
  ],
  "properties": {
    "relation_family": {
      "type": "string",
      "enum": [
        "country",
        "demerger",
        "edition",
        "event",
        "group",
        "index",
        "industry",
        "jv",
        "merger",
        "org_membership",
        "sector",
        "sub_sector",
        "subsidiary",
        "supply",
        "theme"
      ],
      "description": "Hyperedge family from HYPEREDGE_TYPES registry (helpers/graph/hyperedge_types.py:64-82). Gated against doc/design/ontology.md §2.5 roster and check_hyperedge_types integrity."
    },
    "k": {
      "type": "integer",
      "minimum": 1,
      "description": "Hyperedge arity (number of incidence members). k=1 triage via S2; k=2 dyadic; k>=3 polyadic (S1). Current DB: 1,099 event hyperedges at k=2, 1 merger hyperedge at k>=3."
    },
    "roles": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "Role vocabulary per family. For event families: EVENT_ROLE_BY_TYPE (helpers/graph/derive_event_participants.py:64-69) → acquirer/target (acquisition), partner/partner (jv), issuer (guidance), subject (management_change). For merger/demerger: MERGER_ROLE_BY_TYPE (hyperedge_types.py) → constituent/resultant (merger), parent/resultant (demerger). Other families carry no roles (empty array). Roles live on hyper_incidences.role, NOT the event_roles roster."
    },
    "resolution": {
      "type": "string",
      "enum": [
        "resolved",
        "unresolved",
        "pending_triage"
      ],
      "description": "Participant resolution state. resolved → incidence row written; unresolved → free-text evidence row (role + raw name) written to edition, never a new entity; pending_triage → S2 supply k=1 bucket (census + operator ruling only, no fuzzy)."
    },
    "evidence": {
      "type": "object",
      "required": [
        "source_ref",
        "valid_from",
        "confidence"
      ],
      "properties": {
        "source_ref": {
          "type": "string",
          "description": "Provenance agent + doc_id (provenance_agents.agent_id + source_ref). Stamped by derive scripts at write time."
        },
        "valid_from": {
          "type": "string",
          "format": "date",
          "description": "Transaction date from edition/filing. For graph_edges: edge_projection copies as_of → valid_from; for hyper_incidences: derive_hyperedges --roles writes valid_from. Integrity gate: every edge with sidecar provenance MUST have valid_from or properties.as_of."
        },
        "confidence": {
          "type": "number",
          "minimum": 0,
          "maximum": 1,
          "description": "Calibrated confidence (0..1). Currently only 6/71,940 edges carry it (0 hyperedges). Stored in properties.confidence per S3. No fake confidence — D-O3."
        },
        "as_of": {
          "type": "string",
          "format": "date",
          "description": "Legacy field on graph_edges (jv_with 2/1111, acquired 30/57). S3 copies as_of → valid_from; consumers SHOULD read valid_from."
        }
      }
    },
    "entity_type": {
      "type": "string",
      "enum": [
        "company",
        "person",
        "institution",
        "structural",
        "edition",
        "sector",
        "sub_sector",
        "country",
        "theme",
        "super_sector"
      ],
      "description": "Canonical entity_type from entities CHECK constraint (doc/design/db_schema.md:30). institution used for rating agencies per S4 (Acuite/Acute typo-variant, CRISIL Ratings India)."
    },
    "edge_type": {
      "type": "string",
      "enum": [
        "part_of",
        "has_company",
        "competes_with",
        "jv_with",
        "same_group",
        "supplier_to",
        "customer_of",
        "acquired",
        "subsidiary_of",
        "co_mentioned_in",
        "semantic_peer",
        "invested_in"
      ],
      "description": "Dyadic edge type from EDGE_REGISTRY (helpers/graph/query.py:248-368). 18 registered types; 17 populated; penalized_by registered with zero rows. S5 retires co_mentioned_in materialisation (served from edition incidence on demand)."
    },
    "proposal_frontmatter": {
      "type": "object",
      "required": [
        "title",
        "status",
        "filed",
        "area"
      ],
      "properties": {
        "title": { "type": "string" },
        "status": { "type": "string", "enum": ["proposed", "executed", "deferred"] },
        "filed": { "type": "string", "format": "date" },
        "executed": { "type": ["string", "null"], "format": "date" },
        "completed_md": { "type": ["string", "null"] },
        "area": { "type": "string" }
      },
      "description": "Machine-checkable proposal frontmatter per doc/okf/frontmatter.proposal.v1.json (additionalProperties: false). Bold-line header in markdown is human-only."
    }
  },
  "additionalProperties": false
}
```

### 6.2 Enum grounding map

| Schema field | Repo source | Grounding status |
|---|---|---|
| `relation_family` enum | `HYPEREDGE_TYPES` frozenset (hyperedge_types.py:64-82) ✅ | Gated 2× (doc⇄code roster + integrity check) |
| `roles` array items | `EVENT_ROLE_BY_TYPE` (derive_event_participants.py:64-69) + `MERGER_ROLE_BY_TYPE` (hyperedge_types.py) ✅ | Hyperedge roles on `hyper_incidences.role`; event roles on `event_participants.role` |
| `resolution` enum | S1/S2 design: resolved / unresolved (free-text evidence) / pending_triage (S2 census) ✅ | No fuzzy resolution (operator ruling only, 2026-10-15) |
| `evidence.source_ref` | `provenance_agents.agent_id` + edition `source_ref` ✅ | Stamped by writers at write time |
| `evidence.valid_from` | Transaction date from edition; integrity gate in `check_edges_with_sidecar_provenance` ✅ | S3 copies `as_of` → `valid_from` |
| `entity_type` enum | `entities.entity_type` CHECK (db_schema.md:30) ✅ | 4 canonical + structural labels (edition/sector/…) |
| `edge_type` enum | `EDGE_REGISTRY` keys (query.py:248) ✅ | 18 types; `co_mentioned_in` retired by S5 |
| `proposal_frontmatter` | `doc/okf/frontmatter.proposal.v1.json` ✅ | Machine schema; bold header human-only |

### 6.3 Ontology tension note (D-O2 vs. event_participants)

`doc/design/ontology.md` **D-O2** (line 418): "`events` stays the canonical temporal spine; event/jv hyperedges remain derived projections; n-ary participants land on `hyper_incidences`, never a parallel `event_participants` table."

But `person_ontology_spine` S4 (2026-10-19) **landed** `event_participants(event_id, participant, role)` (db_schema.md:158-198, v16) and `derive_event_participants.py` emits participant rows with roles from `EVENT_ROLE_BY_TYPE`. The proposal's **Area** lists `derive_event_participants.py` as touched by S1.

**Resolution for this proposal**: S1 n-ary emission feeds **both** — `event_participants` rows for event/jv hyperedges (k≥3 derived via `derive_hyperedges` importing `EVENT_ROLE_BY_TYPE`) AND `hyper_incidences` for merger/demerger families. The schema above reflects the **live vocabularies**; the D-O2 binding is recorded as a known tension for the next ontology convention slice (D-O5 order: hygiene → provenance → concepts → identifiers → n-ary → glossary).
