---
title: "Ontology convention stack — SKOS/ORG/PROV-O as table conventions, NIC/CIN identifiers, provenance registry"
status: executed
filed: "2026-09-14"
executed: "2026-09-14"
completed_md: "234"
area: "doc/design + helpers (validators, graph, maintenance) — schema-convention arc"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Ontology convention stack — SKOS/ORG/PROV-O as table conventions, NIC/CIN identifiers, provenance registry

**Date:** 2026-09-14 · **Status:** EXECUTED 2026-09-14 (completed.md #234) — S0–S5 landed, schema v7→v11 ·
**Area:** doc/design + helpers (integrity check, derive family, OKF validators, maintenance) · SQLite/DuckDB schema — **no new runtime**

## 1. Motivation

The ontology assessment (source memo: doc/local/engineering/ontology_assessment.md,
verified + amended in its §10, 2026-09-14) settled the strategic verdict:
adopt SKOS, W3C ORG, PROV-O Starting Point, and the W3C n-ary Pattern 1 as
**pure table/field conventions** on the existing SQLite + DuckDB + OKF
Markdown stack — no RDF store, no SHACL execution, no OWL imports. FIBO
(MIT) and schema.org (CC-BY-SA) are cited glossaries; GICS is
proprietary (opaque mapping target only); GDM/OXL are confabulations
(never cite). For the majority-Indian universe: NIC-2008 is the primary
open industry tree, CIN the primary company identifier (parsed into
facets), LEI high-fill via RBI mandates, CIK an ADR-bridge.

What the store lacks to act on that verdict, measured: the `industry:`
frontmatter field is free text with no code set (the single most
ontology-ready gap); `source_ref` is four tables of ad-hoc string
conventions with no agent registry; no identifier columns exist at all;
event participants have no role links; and the documented census is
stale enough that an ontology effort starting from it would inherit
drift (edge roster documented as 19/12, live registry 20).

This arc lands the conventions in six slices, ordered
hygiene-first: **S0 census → S1 provenance → S2 concepts/crosswalks →
S3 identifiers → S4 n-ary event facets → S5 glossary**.

## 2. Evidence (measured 2026-09-14, this box)

Read-only SQLite probes + code reads, this box, before filing:

| Fact | Value | Anchor |
|---|---|---|
| entities | 1,685 — company 1,179 · institution 207 · edition 114 · sub_sector 100 · sector 42 · country 21 · theme 12 · super_sector 10 | live store |
| edge roster | 20 registered types, 19 populated — `penalized_by` zero rows | `helpers/misc/database_integrity_check.py:36-57` |
| legacy pair | `part_of` 1,179 + `has_company` 1,179 = ~12% of 19,325 rows | live store |
| event types | {acquisition, jv, guidance, management_change} | `helpers/validators/static_checks.py:463` |
| counterparty FK | 110/110 resolved since S9 | live store |
| hyper store | 469 hyperedges / 5,659 incidences / 1,318 weighted over 9 types | live store (completed.md #233) |
| industry fill | 921 notes carry `industry:`; 816 non-null; 117 distinct labels | live store |
| entity_tags namespaces | 9 (the concept-scheme roster) | `doc/design/db_schema.md:41-44` |
| display roster drift | `_EDGE_SEMANTICS` 15/20 — missing listed_in, rated_by, regulated_by, approved_by, penalized_by | `app.py:2528` |
| doc census drift | db_schema says "19 edge types"/"12 values"/DuckDB "13" (query.py is "14"); README stat block pre-country-layer | `doc/design/db_schema.md:49,61,233`; `README.md:11-14` |
| load-bearing pair users | analytics `_MEMBERSHIP_TYPES`, stats `_CHAINS_EXCLUDE`, integrity symmetry, build_sector_hierarchy, move_sector, triage exclusion, ~10 test files incl. the find_cycles pin | `helpers/graph/analytics.py:53`; `helpers/graph/stats.py:64`; `tests/test_graph.py:1073-1083` |

Measured, do not re-audit: the census table above; roster/namespace
lists; drift sites. Re-measure at execution.

## 3. Design — six slices

Conventions-only: every slice lands as SQLite tables/columns + Markdown
+ validators. DuckDB stays a derived cache. SKOS/ORG/PROV-O names appear
as column/table names and glossary citations, never as imported
vocabularies.

### S0 — census hygiene (executes first; no schema change)

Correct every documented count against the registry: db_schema.md edge
roster (20 types, 19 populated, live row counts), "12 values" line,
DuckDB cache version note; graph_design.md §1/§4 (census, roster table
+ `penalized_by` 0-row entry); README.md stat block (entities 1,685 /
edges 19,325 / events 436 / quotes 8,272 / metrics 4,412 / hyper
469/5,659). Add the five country-layer types to `_EDGE_SEMANTICS`
(display-only; semantics strings per country-layer proposal). Gates:
md-lint, ruff, affected API unit tests.

### S1 — PROV-O Starting-Point lineage (R2)

`provenance_agents` registry (agent_id, name, version, repo_ref,
script, command, as_of) + nullable `agent_id` and `source_tier`
(manual|migration|derive|regulator|external) on `graph_edges`,
`events`, `quotes`, `company_metrics`, `hyper_edges`. `source_ref` is
NOT renamed — every idempotency LIKE sweep keeps working; agent ids
backfill from deterministic `source_ref` prefixes. Writer: maint-full
PRE_FULL converger + derive scripts stamp at write time. Reader: new
integrity-check advisory (agent coverage per fact table). No
`confidence` columns (assessment §10 D-O3). Regulator filings become
`source_tier='regulator'` anchors — the high-trust tier.

### S2 — SKOS concepts + crosswalks (R1)

`concept_schemes` + `concepts` (pref_label, alt_label, notation,
broader_id, scope_note, source_ref; UNIQUE(scheme_id, concept_code)) +
`concept_mappings` (source/target scheme+concept, match_type
CHECK in exactMatch|closeMatch|broadMatch|narrowMatch, version,
source_ref). Crosswalk rows live ONLY in `concept_mappings` (D-O1 — no
match-id columns on `concepts`). Seed: the 9 entity_tags namespaces as
schemes; sector hierarchy 3-level via broader (super→sector→sub);
later the versioned nic2008 seed table (assessment §6.5) as its own
scheme. Enforcement in our validators, recursive CTEs not OWL:
single-prefLabel, acyclicity, hierarchy closure. `industry:` migration
STARTS here: keep free text, add `industry_code`/`industry_label`/
`industry_source`/`industry_version` frontmatter (OKF bump +
verify_notes + sync_tags threading), fill-rate advisory with the
921/816 baseline. Writer: `seed_concepts.py` in maint-full
(deterministic projection of stamped state — PRE_FULL-legal per the
maint invariant). Reader: verify_notes advisories; closure helper;
unified_search faceting is the future consumer.

### S3 — identifiers (R3, India posture §6)

`entities` gains nullable `cin` + parsed facets (`cin_listing`,
`cin_nic5`, `cin_state`, `cin_year`, `cin_ownership` — regex per
assessment §6.6; format strict, value sets WARNING-tier). Registry
`entity_identifiers` (entity_name, identifier_type CHECK in
cin|lei|cik|isin|llpin|alias, identifier_value, namespace, validity
window, source_ref) for everything else. LLPs enter as LLPIN, never the
CIN parser. New WARNING-tier checks: cin_state vs geography tags,
cin_nic5 vs industry text (migration cross-check), cin_listing='L' ⇒
ticker expected. Writer: parse helper + triage stub funnel + backfill
script. Reader: integrity checks; app.py resolver route.

### S4 — n-ary event facets on the incidence store (R4)

`hyper_incidences` gains `role`, `valid_from`, `valid_to` (no
confidence — D-O3). Event facets beyond the row: `properties` JSON
shape spec (period, date_precision, raw + numeric magnitude, unit,
role, source_tier) so extractors can write them; raw magnitude string
stays for audit, never event identity. `events` REMAINS the canonical
temporal spine; event/jv hyperedges stay derived projections (D-O2) —
no parallel event_participants table. Backfill: resolve via
`counterparty_entity` (done, 110/110), one hyperedge per observation
with role-tagged participants, dry-run reconciliation report
(unresolved/duplicate/conflicting) before any apply. Writer:
`derive_hyperedges --roles`. Reader: reconciliation report;
centralities/communities consume unchanged.

### S5 — cited glossary (R5)

`doc/reference/ontology_glossary.md`: 5 starter entries (ORG
subOrganizationOf, schema.org Corporation, FIBO BE
PrivateLimitedCompany, SKOS exactMatch, PROV wasGeneratedBy), each with
URI + license + status attribution block; convention fixed: borrowed
names in code carry the citation in the MODULE DOCSTRING, borrowed
definitions in docs carry the per-entry block. MIT/CC-BY-SA compliance
is per-citation, never wholesale copying.

## 4. Acceptance criteria

- S0: documented counts == registry/live store at every named site;
  `_EDGE_SEMANTICS` 20/20; md-lint + affected tests green.
- S1: agent coverage 100% on backfilled tables (advisory reports it);
  `source_ref` prefixes untouched; snapshot parity holds.
- S2: prefLabel uniqueness + acyclicity advisories green on the seeded
  schemes; crosswalk rows only in `concept_mappings`; industry
  fill-rate advisory live with baseline.
- S3: CIN parser unit tests (valid, legacy NIC-98/2004 vintage →
  WARNING not reject, LLPIN rejection, bad format ERROR);
  identifier_type CHECK enforced; WARNING-tier cross-checks reporting.
- S4: reconciliation dry-run clean (0 unresolved, duplicates
  enumerated); role-tagged incidences round-trip through snapshot.
- All schema slices: db.py version constants bumped (DuckDB cache
  `_SCHEMA_VERSION` only on cache-shape change); maint-full placement
  verified; test_maint pins enumerated and updated; close order
  maint-full → search-fresh → snapshot.

## 5. Risks & trade-offs

- **Schema creep without consumers** — mitigated by the writer/reader
  obligations table (assessment §10.4): every artifact names its
  producer script and its reader/check. A slice without both is cut.
- **`part_of`/`has_company` retirement** is explicitly OUT of this arc
  (load-bearing across ~10 test files; gated on sector-hyperedge
  absorption per assessment §8-R3). S2/S4 make the gate CHEAPER, never
  trigger it.
- **NIC-2008 vintage** (2008 codes; CINs pre-2008 carry NIC-98/2004) —
  parser treats legacy codes as WARNING with vintage label; next-NIC /
  ISIC-5 watchpoint recorded, placeholder crosswalk column reserved.
- **MCA feed cost**: per-company master data free to view, bulk paid;
  AOC-4 XBRL excludes several company classes. S3's CIN path does not
  depend on bulk MCA access (parse-on-ingest works from any single
  filing or manual entry).
- **GICS dormant**: no pipeline brings peer GICS assignments in today;
  `concept_mappings` rows of match_type exactMatch with opaque
  peer-provided codes land only when such a source exists.

## 6. Non-goals

- No RDF triplestore, no SHACL/ShEx execution, no OWL import, no
  FIBO/schema.org vocabulary mirroring beyond cited glossary entries.
- No GICS tree embedding (proprietary; opaque strings only, with
  attribution, when peer-provided).
- No person type — the D6 deferral stands; `quotes.speaker_*` remains
  the catch-all. ORG Membership/Post/Role activate only when that gate
  lifts (MCA DIN keys them when it does).
- No `part_of`/`has_company` retirement, no event_participants table,
  no confidence columns, no `source_ref` rename.
- No citation of GDM/OXL (verified non-standards).

## 7. Deferred / future-facing

- `part_of`/`has_company` retirement — behind the §8-R3 gate (hyperedge
  absorption + full test-file enumeration first).
- Wikidata QID crosswalks as an embedding side-input constraining
  `semantic_peer` — after S2 lands.
- GLEIF Level-2 relationship records as an advisory checker
  cross-verifying `subsidiary_of`/`same_group` — first real external
  consumer of S3 identifiers.
- NIC seed-table builder (MoSPI PDF → `nic2008`) as its own slice once
  S2's scheme machinery is proven on the existing rosters.
- Person/DIN lane (ORG Membership activation) — separate proposal when
  D6 lifts.

## 8. Execution Results

- **S0 EXECUTED (2026-09-14)** — census hygiene, no schema change.
  Every documented count re-measured against the registry/live store and
  corrected: db_schema.md (header counts incl. hyper tables 469/5,659;
  entity_type census 1,685 w/ company 1,179 · sub_sector 100; edge
  roster 19,325 rows / 20 registered — 19 populated, `penalized_by`
  zero rows; graph_analytics 18 kinds / 23,837 rows; DuckDB cache 30
  objects / version "14" / v_country as the 8th projection;
  v_embeddings 1,165 / v_note_embeddings 16,521; hyper live counts
  post-S17/S21), graph_design.md (§1 census, cache line v14/1,685, §4
  roster + `penalized_by` row + cited_in 1,913 + belongs_to 142 +
  skew 393/1,913, §5.6 18 metrics), architecture.md §SQLite summary,
  README.md (stat block + layout table: 1,685 / 19,325 / 436 / 8,272 /
  4,412 / 469 hyperedges / 16,521 note docs / 1,348 tracked notes /
  3,094 tests × 158 modules; MD040 fence fixed on the touched file).
  `app.py` `_EDGE_SEMANTICS` completed to 20/20 (five country-layer
  types added, display-only). Gates: ruff clean, md-lint clean, pytest
  test_api_graph_unit 87/87, search-fresh APPLY + convergence rc=0.
  Numbers measured this box 2026-09-14, SQLite + DuckDB read-only
  probes; the cache was already at schema_version "14" (stale DOC, not
  stale cache).
- **S1 EXECUTED (2026-09-14)** — PROV-O Starting-Point lineage; SQLite
  schema v7→v8 (db.py EXPECTED_* bumped; DuckDB cache untouched — the
  new columns don't flow into `e_*` shapes, no `_SCHEMA_VERSION` bump).
  Landed: `provenance_agents` registry + nullable `agent_id` (FK ON
  DELETE SET NULL) and `source_tier` (CHECK enum) on the five fact
  tables; `helpers/misc/backfill_row_provenance.py` — self-ensuring
  idempotent converger (CREATE IF NOT EXISTS + guarded ALTERs,
  LIKE-escaped longest-prefix mapping, first-match-wins dry-run,
  never-blocking on partial schemas), wired as a maint-full PRE_FULL
  step after okf-backfill (15 steps now; dispatcher shim added);
  `check_provenance_coverage` integrity check (WARNING). Live apply
  (pre-backed-up): 21 agents seeded, 28,912/28,912 rows mapped —
  100% coverage, 0 unmapped; ensure_db_meta healed user_version +
  mirror to 8; full integrity check in-process: 0 errors, warnings
  unchanged (validity_window 90, pre-existing). The prefix map is
  measured, not guessed: 27 prefix lanes incl. ones the assessment
  missed (embeddings 7,776 / yfinance 4,130 / triage 77 / coinfer 105
  / Phase-2 seeds / move_sector / sector-sync:stub / extract_relations
  colon-form). SQLite LIKE trap hit + fixed: bracket `[_]` escaping is
  SQL-Server syntax — SQLite needs backslash-ESCape, or every
  underscore-carrying lane silently matches nothing. Gates: pytest 18/18
  (test_backfill_row_provenance 13 + maint-chain 5 with the new step),
  ruff clean. **S1b remaining** (deferred inside S1): derive writers
  stamp agent_id/source_tier at write time — until then the PRE_FULL
  converger covers rows between maint-full runs; `regulator` tier
  reserved, no producer yet.
- **S2 EXECUTED (2026-09-14)** — SKOS conventions; SQLite schema v8→v9
  (no DuckDB cache bump — concepts are not materialised into the
  cache). Landed: `concept_schemes`/`concepts`/`concept_mappings` +
  `helpers/misc/seed_concepts.py` (self-ensuring converger,
  DELETE-then-INSERT on `seed:%`, never-blocking on partial schemas),
  wired as the 6th PRE_FULL step (16 steps; shim added).
  Reconciliation rule: taxonomy entity names are CANONICAL — a tag
  whose lowercase form matches an entity (`sector/banking` vs entity
  `Banking`) is absorbed, never duplicated; all 57 live subsector tags
  absorbed by the 100 canonical entities; stale tag-only values kept.
  Crosswalks: `concept_mappings` seeded from
  `derive_hyperedges.SUB_SECTOR_ALIASES` (the S11/S17 operator-curated
  map) — the one crosswalk home per D-O1. `subtree()` closure helper
  (recursive CTE). Integrity: new `concepts` check (WARNING — dangling
  broader / cycles / duplicate pref_labels case-insensitive / empty
  schemes); live: all zero, 0 errors overall. OKF: company schema
  gains optional `industry_code`/`industry_label`/`industry_source`
  (enum nic2008|nace|gics-peer|manual)/`industry_version`
  (x-okf-version 0.2; key doc regenerated; sync_tags untouched — the
  fields are not tags). verify_notes: industry fill-rate advisory —
  live line: **816/1,179 non-null (921 carriers, 0 coded)**; the
  parallel worker tuple extended to carry the counters (process
  workers don't share the parent stats dict). Live apply
  (pre-backed-up): **11 schemes / 403 concepts / 68 mappings** —
  sector 42, subsector 100, super_sector 10 (all parented: 0 sector
  roots, 10 super roots), industry 117 labels; ensure_db_meta healed
  v9. Gates: pytest 30/30 new+chain, verify_notes 66/66, key-doc
  fresh test green, ruff clean. NIC seed table + filling
  `industry_code` values stays deferred (own slice per §7).
- **S3 EXECUTED (2026-09-14)** — identifiers; SQLite schema v9→v10 (no
  DuckDB cache bump — identifiers don't flow into `e_*` shapes).
  Landed: `entities.cin` + five facets (`cin_listing`/`cin_nic5`/
  `cin_state`/`cin_year`/`cin_ownership`) + `entity_identifiers`
  registry (CHECK in cin|lei|cik|isin|llpin|alias — `cin` reserved for
  superseded history via direct SQL; PK(entity,type,value) +
  UNIQUE(type,value) so an identifier resolves to exactly one entity);
  `helpers/core/cin.py` — FORMAT-strict/VALUE-lenient parser (21-char
  regex; unknown ROC/ownership codes, year window, pre-2008 NIC
  vintage = WARNING kinds, never reject; LLPIN shape `AAA-1234`
  rejected as a DISTINCT error so callers route to the registry as
  llpin); `helpers/misc/backfill_identifiers.py` — self-ensuring
  converger (guarded ALTERs + CREATE IF NOT EXISTS, skip-never-block on
  an entities-less DB) wired as the 5th PRE_FULL step (17 steps; shim
  added), facet projection is deterministic so maint-full heals drift;
  validated write surface `--set-cin NAME=CIN` / `--set-id
  NAME:TYPE=VALUE` (plan-then-apply: parse + entity-existence +
  cross-entity-ambiguity gates, failures block the whole batch;
  lei/cik/isin/llpin format-gated; NOCASE entity matching).
  `check_identifiers` integrity check (WARNING: malformed CIN, facet
  drift, listed↔ticker cross-checks, state↔geography/india
  country-level mapping, registry dangling FK + window inversion; NIC
  cross-check deferred with the nic2008 scheme). Readers:
  `/api/resolve` in `app.py` (entities.cin normalized first, registry
  second, COLLATE NOCASE; pre-S3 DBs degrade to 404, never 500).
  Triage funnel: `triage_pending_quotes.py` accepts `stub|cin=<CIN>` —
  parse-validated at apply (bad CIN blocks the batch), stubs stay
  user-held, and apply prints the ready-to-run `--set-cin` command for
  after the entity exists. maint pins updated (TestPlan 4→7 stale from
  S1/S2, full composition 15→18, dry-run count). Live apply
  (pre-backed-up): 0 cin / 0 registry rows (parse-on-ingest fills — no
  bulk MCA dependency), ensure_db_meta healed v10, in-process integrity
  0 errors + new check all-zeros, route smoke 400/404 correct. Gates:
  pytest 100/100 S3 files + 133 across all touched (parser 13,
  backfill 18, integrity 9, maint chain+pins), ruff + ruff format +
  ty clean on the changed surface. **Deferred**: NIC-2008 seed-table
  builder (own slice, with filling `industry_code`); LEI/CIK/ISIN data
  acquisition is operator-paced.
- **S4 EXECUTED (2026-09-14)** — n-ary event facets; SQLite schema
  v10→v11 (no DuckDB cache bump — the cache does not materialise the
  incidence tables). Landed: `hyper_incidences.role` / `valid_from` /
  `valid_to` (canonical DDL in migrate_to_graph_edges + guarded-ALTER
  self-ensure `ensure_incidence_facets` in the writer — no confidence
  columns, D-O3); `derive_hyperedges --roles` — event hyperedges get
  role-tagged participants (`_EVENT_ROLES`: acquisition →
  acquirer/target, jv → partner/partner; unmapped types stay NULL,
  never confabulated), observation dates on `hyper_edges.valid_from`,
  and an event-facet `properties` JSON (period, date_precision,
  magnitude_raw + magnitude_numeric + magnitude_unit; `_parse_magnitude`
  takes the first standalone numeric token — currency prefixes ride as
  the unit, ranges like `10-12%` parse to NULL with the raw string
  preserved for audit); `reconcile_events` pre-apply report (unresolved
  counterparties / duplicate observations with direction-normalized
  pairs / conflicting stored member sets / untagged types) prints in
  every `--roles` run before any apply. `events` REMAINS the canonical
  temporal spine (D-O2) — no event_participants table. maint-full
  TIER2 derive-hyperedges now runs `--apply --roles` (label + test_maint
  pins updated). Live apply (pre-backed-up): reconciliation clean —
  110 observations, 0 unresolved, 0 duplicates, 0 conflicts, all types
  role-mapped; 220 role-tagged incidences (41 acquirer / 41 target /
  138 partner), 20 dated `valid_from`, 11 numeric magnitudes; 0 new
  edges/incidences (pure facet convergence over the existing 110);
  ensure_db_meta healed v11; in-process integrity 0 errors; role-tagged
  incidences round-trip the Parquet snapshot path (export → restore
  test). Gates: pytest 17/17 new (test_hyper_roles) + 85 across
  hyper/maint suites. **Deferred**: membership-shrinkage compaction
  (pre-existing honest-scope note) and role vocabulary for future
  counterparty-carrying event types land with their extractors.
- **S5 EXECUTED (2026-09-14)** — cited glossary;
  `doc/reference/ontology_glossary.md` (new `reference/` doc area):
  5 entries — ORG `subOrganizationOf`, schema.org `Corporation`, FIBO
  BE `PrivateLimitedCompany`, SKOS `exactMatch`, PROV-O
  `wasGeneratedBy` — each with URI + source + license/status +
  usage-here block, plus the fixed citation policy (borrowed names in
  code → module-docstring citation: `seed_concepts.py` now cites the
  SKOS Reference, `backfill_row_provenance.py` cites PROV-O;
  house/registrar vocabulary — derive_hyperedges role labels, cin.py
  MCA enums — explicitly exempt; GICS opaque-strings-only; GDM/OXL
  never). No runtime surface; md-lint + search-fresh cover it.
- **ARC CLOSED (2026-09-14)** — all six slices executed. Close order
  run in full: maint-full 18/18 (the `--roles` step green in-chain:
  110 observations, 0 unresolved/duplicates/conflicts) → search-fresh
  FRESH ×3 → snapshot (parquet 50 tables, `entity_identifiers` +
  role-bearing incidences captured). Gates: `make qa` 9/9 PASS
  (2,944 passed / 3 skipped — one xdist wall-clock flake in
  test_performance rerun green in isolation + in the full re-run),
  `make advisory` 10/10 PASS after the first-run bill (ruff format on
  5 arc files, one ty `str | None` narrowing, 12 lint-audit findings
  resolved via the sanctioned noqa-with-reason / per-line S608
  patterns). SQLite schema v11; DuckDB cache "14" unchanged (no
  cache-shape change). Deferred backlog rides in §7 (S1b write-time
  stamping, NIC-2008 seed table, GLEIF L2 cross-checker, Wikidata
  crosswalks).
