# Ontology — master reference

The normative front door for the repository's ontology layer: the
controlled vocabularies, the binding decisions, and the map of where
live truth lives. Scope = the SQLite store's semantic conventions
(`memory/research.db`), the OKF frontmatter vocabulary, and the derived
DuckDB cache's contract with them.

**The anti-drift contract (read before editing):** membership of a
vocabulary is normative and lives here; **magnitudes — row counts,
census sizes, fill rates — never live here** (they live in
`db_schema.md`, measured against the registry). Code registries are the
executable truth; this document restates them for humans, and the
roster-drift static check (qa-gated) fails when the two disagree in
either direction.

## 1. Posture — conventions, not runtimes

SKOS, W3C ORG, PROV-O Starting Point, and the W3C n-ary Pattern 1 are
adopted as **pure table/field conventions** on SQLite + OKF Markdown —
no RDF store, no SHACL/ShEx execution, no OWL imports. FIBO (MIT) and
schema.org (CC-BY-SA) are cited glossaries for borrowed names; GICS is
proprietary (opaque mapping target only); GDM/OXL are confabulations
(never cite). Full research record:
`doc/local/engineering/ontology_assessment.md`; execution history:
`doc/improvements/archive/database/ontology_convention_stack.md`
(#234); citations and licenses: `doc/reference/ontology_glossary.md`.

## 2. Vocabulary rosters

Each roster below sits inside `<!-- roster: name -->` …
`<!-- /roster -->` markers. Rosters marked **gated** are diffed against
their code registry by the `Ontology doc rosters` check in
`helpers/validators/static_checks.py` (a registry change and its doc
update must land in the same change). Others are documented-only until
a shared constant exports cleanly (deferred, ontology_governance §7).

### 2.1 Edge types — dyadic relation vocabulary (gated)

Registry: `_KNOWN_EDGE_TYPES`
(`helpers/misc/database_integrity_check.py`) — the 20-type vocabulary,
check-gated against this doc. (`EDGE_REGISTRY` in
`helpers/graph/query.py` is the DuckDB-materialized SUBSET, not the
vocabulary.) Endpoint conventions and per-type semantics:
`doc/design/db_schema.md` §graph_edges, `doc/design/graph_design.md` §4.

<!-- roster: edge_types -->

- part_of
- has_company
- competes_with
- jv_with
- same_group
- supplier_to
- customer_of
- acquired
- subsidiary_of
- co_mentioned_in
- belongs_to
- exposed_to
- cited_in
- semantic_peer
- invested_in
- listed_in
- listed_on_index
- rated_by
- regulated_by
- approved_by
- penalized_by

<!-- /roster -->

Legacy exception: `part_of`/`has_company` keep the two-row
bidirectional pair (retirement gated on sector-hyperedge absorption,
entry 234 §8-R3). Endpoint conventions and per-type semantics:
`doc/design/db_schema.md` §graph_edges, `doc/design/graph_design.md` §4.

### 2.2 Symmetric edge types — one row, `source LE target` (documented)

<!-- roster: symmetric_edge_types -->

- co_mentioned_in
- competes_with
- jv_with
- same_group
- semantic_peer

<!-- /roster -->

### 2.3 Canonical event types — the temporal spine (gated)

Registry: `CANONICAL_EVENT_TYPES`
(`helpers/validators/static_checks.py`). `events` is the canonical
temporal spine (D-O2); `event`/`jv` hyperedges are derived projections.
`earnings` is deliberately absent (no reliable date source; deferred to
the D8 transcripts lane).

<!-- roster: canonical_event_types -->

- acquisition
- guidance
- jv
- management_change

<!-- /roster -->

### 2.4 Event participant roles (documented)

Registry: `_EVENT_ROLES` (`helpers/graph/derive_hyperedges.py`) —
acquisition → acquirer/target, jv → partner/partner. Unmapped event
types leave incidences unlabeled (never confabulated). The role
vocabulary:

<!-- roster: event_roles -->

- acquirer
- partner
- target

<!-- /roster -->

### 2.5 Hyperedge types — incidence store vocabulary (documented)

`hyper_edges` over `hyper_incidences` (star store; HIF/Arrow lanes per
entry 233). Roles/validity facets on incidences per entry 234 S4.

<!-- roster: hyperedge_types -->

- country
- edition
- event
- group
- industry
- jv
- sector
- sub_sector
- theme

<!-- /roster -->

### 2.6 Entity types (documented)

No `person` type — the D6 deferral stands; `quotes.speaker_*` is the
catch-all. ORG Membership/Post/Role activate only when that gate lifts
(MCA DIN keys them when it does).

<!-- roster: entity_types -->

- company
- country
- edition
- index
- institution
- sector
- sub_sector
- super_sector
- theme

<!-- /roster -->

### 2.7 Concept schemes (documented)

The 9 `entity_tags` namespaces plus the three non-tag schemes (`industry`
from live hyper-edge labels, `super_sector` from the taxonomy trio,
`nic2008` from the vendored NIC-2008 seed). Registry: `_TAG_NAMESPACES`
+ `_EXTRA_SCHEMES` (`helpers/misc/seed_concepts.py`); `nic2008` lives in
its own converger (`NIC2008_SCHEME_ID` in `helpers/misc/seed_nic2008.py`)
with a five-level section→subclass broader chain — the two convergers own
disjoint `seed:` source-ref domains (the `seed:nic2008/%` carve-out).
Crosswalks live ONLY in `concept_mappings` (D-O1).

<!-- roster: concept_schemes -->

- business_model
- entity_type
- geography
- holding_company
- industry
- investment_theme
- market_cap
- nic2008
- risk_investment
- sector
- subsector
- super_sector

<!-- /roster -->

### 2.8 CHECK-constraint enums (gated — `helpers/core/vocab.py`, #244c)

Registry: `helpers/core/vocab.py` exports MATCH_TYPE_VALUES /
IDENTIFIER_TYPE_VALUES / SOURCE_TIER_VALUES / CONCEPT_STATUS_VALUES;
the DDL CHECK clauses (seed_concepts, backfill_identifiers,
backfill_row_provenance) build from the same constants, so a value
change must land in all three places at once. (Gating was deferred at
filing, ontology_governance 244c; joined the gated rosters
2026-09-19.)

From the canonical DDL (`helpers/core/db.py`, `helpers/core/cin.py`,
entry-234 slices):

match_type (concept_mappings, SKOS):

<!-- roster: match_type_values -->

- broadMatch
- closeMatch
- exactMatch
- narrowMatch

<!-- /roster -->

identifier_type (entity_identifiers; `cin` on entities is parsed into
facets, registry rows carry history):

<!-- roster: identifier_type_values -->

- alias
- cin
- cik
- isin
- lei
- llpin

<!-- /roster -->

source_tier (row provenance; `regulator` reserved, no producer yet):

<!-- roster: source_tier_values -->

- derive
- external
- manual
- migration
- regulator

<!-- /roster -->

Concept lifecycle status (concepts/concept_mappings, lands with S1 of
ontology_governance): `candidate` → `active` → `superseded`; seed-owned
rows supersede (never delete) when the roster drops them, resurrect on
re-add; promotion via the validated `--promote` surface.

<!-- roster: concept_status_values -->

- active
- candidate
- superseded

<!-- /roster -->

## 3. Binding decisions (D-O1 … D-O6)

From the assessment memo §10.3 — binding, supersede conflicting prose
anywhere else:

| ID | Decision |
|---|---|
| D-O1 | One crosswalk home: `concept_mappings` rows only; no match-id columns on `concepts` |
| D-O2 | `events` stays the canonical temporal spine; event/jv hyperedges remain derived projections; n-ary participants land on `hyper_incidences`, never a parallel `event_participants` table |
| D-O3 | No fake confidence: no `confidence` columns until a calibrated producer exists; `source_tier` enum carries the trust story |
| D-O4 | Identifier homes: `entities.cin` + five parsed facets for segmentation; the registry (`entity_identifiers`, CHECK-constrained types) for lei/cik/isin/llpin/alias |
| D-O5 | Slice order for convention work: hygiene → provenance → concepts → identifiers → n-ary → glossary |
| D-O6 | India caveats on record: NIC-2008 vintage (next-NIC/ISIC-5 watchpoint), MCA bulk access paid, AOC-4 XBRL coverage gaps |

## 4. Writer / reader obligations

Every ontology artifact names its producer script and its reader or
check — an artifact without both is cut (the §10.4 doctrine). Current
table (extend in the same change as any new artifact):

| Artifact | Writer (named) | Reader / check (named) |
|---|---|---|
| `provenance_agents` + `agent_id`/`source_tier` | maint-full PRE_FULL converger (`backfill_row_provenance.py`); derive scripts stamp at write time | `check_provenance_coverage` (advisory %) |
| `concept_schemes`/`concepts`/`concept_mappings` | `seed_concepts.py` (PRE_FULL, supersede/upsert); scheme `nic2008` + its 2,067 concepts: `seed_nic2008.py` same slot (disjoint source-ref domain) | `check_concepts` advisories; `subtree()` closure; roster-drift static check (this doc) |
| `entities.cin*` + `entity_identifiers` | `backfill_identifiers.py` converger; `--set-cin`/`--set-id` validated writes; triage `cin=<CIN>` funnel | `check_identifiers`; `/api/resolve` |
| `hyper_incidences.role/valid_from/valid_to` | `derive_hyperedges --roles` | reconcile_events dry-run report vs `events` |
| Concept `status` columns (S1) | `seed_concepts.py` supersede/upsert; `--promote` surface | `subtree()` active-only; `check_concepts` advisories |
| Ontology change gate (S2) | `ontology_eval_gate.py` over `helpers/misc/ontology_questions.json` | acceptance criteria of ontology-affecting proposals |
| `doc/reference/ontology_glossary.md` | operator/agent edits | doc_query corpus |
| This document | same-change update with any registry change | roster-drift static check (qa-gated) |

## 5. Where live truth lives

| Question | Answer lives at |
|---|---|
| How many rows / entities / fill rates? | `doc/design/db_schema.md` (measured; never transcribed here) |
| Which edge types / event types exist? | The code registries (§2.1, §2.3) — check-gated against this doc |
| Which hyperedge types / roles? | `derive_hyperedges.py` registries (§2.4, §2.5) |
| What does an extractor write? | The extractor module's docstring |
| License/attribution for a borrowed name? | `doc/reference/ontology_glossary.md` |
| Why was X decided? | `doc/local/engineering/ontology_assessment.md` (record), `doc/improvements/` (proposals), `completed.md` (run log) |
| Is the derived cache fresh? | `memory/graph.duckdb` `_SCHEMA_VERSION` + snapshot manifest (not this doc) |

## 6. Governance of this document

- Roster updates land in the **same change** as their registry change —
  the qa-gated roster-drift check enforces it (loader table: edge types,
  canonical event types; joins as constants export cleanly).
- New vocabularies join with a marker block; CHECK enums follow the
  §2.8 pattern.
- This document never carries counts, percentages, or dates-as-data —
  those are measurements belonging to `db_schema.md` or `completed.md`.
- Citation policy for borrowed names (SKOS/ORG/PROV-O/FIBO/schema.org)
  is fixed in the glossary; borrowed names in code carry the citation in
  the module docstring.
- Ontology-affecting changes run the eval gate:
  `helpers/misc/ontology_eval_gate.py` over the frozen set
  `helpers/misc/ontology_questions.json` (the §2 rosters pinned as
  answerable row-sets) — operating procedure, coverage map, and
  rebaseline flow in `doc/procedures/ontology-gate.md`.

Borrowed vocabulary names (prefLabel, exactMatch, subOrganizationOf,
wasGeneratedBy, …) are used per `doc/reference/ontology_glossary.md`
with W3C/EDM/schema.org attribution.
