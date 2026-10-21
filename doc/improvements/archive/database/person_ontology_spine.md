---
title: "Person ontology spine — resolve the D6 drift: speakers, membership roles, participant links"
status: executed
filed: "2026-10-18"
area: "doc/design + helpers/graph"
executed: "2026-10-19"
completed_md: "419"
---

# Person ontology spine — resolve the D6 drift: speakers, membership roles, participant links

**Date:** 2026-10-18 · **Status:** PROPOSED

## 1. TL;DR

The D6 "no person type" deferral has been **silently overtaken by the
data**: 277 `person` entities now live in the store (30 from the
SHP-holder lane #267, 247 added 2026-10 by the person-name arcs
(proposals #398/#399), while `doc/design/ontology.md:142` still asserts "No
`person` type — the D6 deferral stands" and the roster block at
`:153` lists `person` anyway — a self-contradiction inside one file.
Meanwhile the largest person-shaped population in the corpus — quote
speakers — has **no identity link at all**: 1,419 distinct
`quotes.speaker_name` strings (8,687 quote rows, 2,418 with NULL
speaker) resolve to nothing. The ORG Membership/Post/Role conventions
that #234 built explicitly "activate only when that gate lifts (MCA
DIN keys them when it does)" — the gate lifted through a side door and
the conventions never noticed. This arc builds the person identity
spine on the machinery the person-name arcs already proved
(`entity_identity.py`, `person_dedup.py`, the legal-line identity
model), resolves speakers against it, and activates the ORG membership
conventions with the role links the events table still lacks.

## 2. Evidence (measured 2026-10-18, live store)

| Fact | Value | Anchor |
|---|---|---|
| `person` entities | **277** — 30 created 2026-09 (SHP holders), 247 created 2026-10 | `entities` probe |
| person→company `invested_in` | 287 edges (was 30 of 799 at #267, 2026-09-22) | `graph_edges` probe |
| distinct `quotes.speaker_name` | **1,419** strings; 2,418 of 8,687 quotes have NULL speaker | `quotes` probe |
| top speaker strings | K. Krithivasan 46 · Karan Shah 41 · Jensen Huang 38 · Atul Daga 36 | `quotes` probe |
| person-name census at #399 | "28 graph person clusters, 30 SHP stubs, **188** Quotes speakers" (2026-10-15) | completed.md #399 |
| speaker-string vs person-census gap | 1,419 raw strings vs 188 censused speakers — **most speaker strings were never in scope** of the dedup arc | this box |
| events with participant roles | **0** — `events` holds 1,477 rows, no participant link table | `sqlite_master` probe |
| event hyperedges | 1,252 edges, ALL k≤2 (106 k=1, 1,146 k=2, zero k≥3) | `hyper_incidences` probe |
| doc contradiction | `doc/design/ontology.md:142` "No `person` type" vs roster `:153` listing `person` | read |
| gate history | `person_resolver_lane.md` (executed 2026-09-25): "Opens the hierarchy-ledger D6 row … now unblocked"; quote speakers explicitly stay strings (its §5 non-goal) | archive |
| ORG activation clause | "ORG Membership/Post/Role activate only when that gate lifts (MCA DIN keys them when it does)" | `ontology_convention_stack.md` §5/§7 |
| DIN source | MCA bulk 403; no appointments source — the DIN lane stays blocked | `person_resolver_lane.md` §5 |
| identity machinery | `helpers/core/entity_identity.py` (tier model), `helpers/graph/person_dedup.py` (census/plan/check, NEVER-MERGE lines), `helpers/graph/name_dedup.py` (fold ledger) | #398/#399 |
| capture-quality tie-in | `management_change` FP set: 5 rows, ≥2 false positives, Infosys succession double-counted (board quote + summary bullet) | `person_resolver_lane.md` §1 |

## 3. Why this is the big miss

Every other deferred ontology item has a clean trigger story; the
person lane's trigger **fired and nobody re-read the gate**. The store
already treats persons as first-class for holdings (277 entities, 287
edges, FK-cascaded), the identity model already handles the hard part
(HUF/trust/individual legal lines with a NEVER-MERGE ledger), and the
documented rationale for keeping speakers as strings ("the D6
deferral") no longer describes the store. The cost of the drift is
concrete: 1,419 speaker strings with zero entity linkage means
speaker-level analytics (management-change capture quality, chatter
attribution, the #234 S4 n-ary event pattern) all stop at a string
compare.

## 4. Slices

- **S1 Speaker census + normalization map** — extend the #399 identity
  registry over all 1,419 speaker strings: legal-line parse, fold
  clusters, NEVER-MERGE lines; census/plan/check like `person_dedup`,
  report-only first. Output: resolution plan with per-cluster evidence.
- **S2 `person` resolution for quotes** — link `quotes` speakers to
  `person` entities (FK or side-table), dry-run default with `--apply`
  guard; unmatched strings stay strings and are counted. The
  D6-for-quotes deferral is formally retired here, not silently.
- **S3 ORG Membership/Role conventions** — activate the #234 ORG
  Membership convention for the *person→company* pairs the store
  already holds (287 `invested_in` edges): role-typed membership rows
  keyed on `person_dedup` clusters. DIN/directorship stays blocked
  (MCA 403) and is a watchpoint, not a slice.
- **S4 `event_participants` n-ary table** — the #234 S4 non-goal,
  unblocked by S1/S2: `(event_id, participant, role)` with the
  counterparty roles derive_events already computes. Makes event
  hyperedges genuinely higher-order (today zero k≥3) and feeds the D10
  motif lane. See `hyperedge_density_dq.md` — same data, one write path.
- **S5 doc roster correction** — rewrite `doc/design/ontology.md:142`
  (the contradiction), update the entity_types prose, retire the
  "speaker_* catch-all" sentence, annotate #234 §7.

## 4a. Execution log

- **S1 DONE 2026-10-19.** `helpers/graph/speaker_census.py` (report-only;
  `speaker_census` / `write_plan` / `check`, CLI `--census` default,
  `make speaker-census`). Identity policy imported, not re-implemented
  (`person_dedup._merge_line`/`_base_key`,
  `entity_identity._COMPANY_MARKER_RE`/`name_key`). Measured live:
  `strings=1419 clusters=1410 multi_spelling=9 quotes=6269
  matched_entity=3 unmatched=1407 never_merge=0 company_shaped=3`;
  plan written to `findata/Misc/_speaker_resolution_plan.txt` +
  `.jsonl` (1,410 rows); baseline 12 recorded. 13 tests.
- **S2 DONE 2026-10-19 (map + operator-sanctioned mint).**
  `helpers/graph/speaker_resolution.py` + schema v15:
  `speaker_identity` (alias → canonical → person FK, source_ref
  `speaker_resolution:S2`) and the `v_quote_speaker` LEFT-JOIN view.
  Resolution at SPEAKER level rather than a `quotes` column: the
  decision is many-to-one, so a column would duplicate it 8,687× and go
  stale for future quotes. The map landed first (1,419 aliases, 3
  linked); the operator then ordered `--mint` (default `--min-quotes
  2`): **973 persons minted (277 → 1,250, all pathless), 985 aliases
  linked, 5,743 of 6,269 speaker-bearing quotes resolve (91.6%)**;
  speaker-census and person-census gates stayed at baseline, integrity
  green after the post-write `graph-rebuild` + `stamp-centrality`.
  Refusals reported, never force-merged: ~20 canonical names already
  taken by an entity of another type (misclassified companies — e.g.
  Anmol Jain, Ashish Bhandari, Deepak Joshi, Gunjan Shah exist as
  `company`) and 3 company-shaped attributions (`Camshafts Limited`,
  `Management Ather Energy`, `Power Ventures Limited`). 15 tests.
- **S3 DONE 2026-10-19.** `helpers/graph/derive_org_membership.py`
  (`make derive-org-membership`; dry-run default): the 287
  person→company `invested_in` dyads → the **`org_membership`
  hyperedge family** (label = company, member = person,
  `hyper_incidences.role` ∈ {`promoter`, `holder`} read off
  `properties.category`, `weight` = stake_pct, `valid_from` = filing
  period). Live: 34 companies / 287 members (205 promoter / 82
  holder), k up to 32; agent `derive_org_membership` registered and
  stamped at write time (prefix mapped in
  `backfill_row_provenance.PREFIX_AGENTS`). The lane OWNS its family:
  `--apply` replaces incidences wholesale and drops memberless
  hyperedges, so shrinkage converges. The `invested_in` dyads STAY —
  economic edge for analytics, ingest-side truth for the derive; the
  coexistence mirrors theme dyads + theme hyperedges. DIN/directorship
  stays blocked (MCA 403) — watchpoint, not a slice. 8 tests.
- **S4 DONE 2026-10-19.** `helpers/graph/derive_event_participants.py`
  (`make derive-event-participants`; dry-run default) + schema v16:
  the **`event_participants(event_id, participant, role)`** n-ary
  store, PK `(event_id, participant, role)`, FKs cascade both ways.
  `EVENT_ROLE_BY_TYPE` is the single source of truth (entity-first):
  acquisition → acquirer/target, jv → partner/partner, guidance →
  issuer, management_change → subject; unknown types carry NO rows
  (never confabulate). The lane OWNS the table (per-event
  delete+replace, content-aware `changed_events`, orphan purge
  converges). Live: 1,477 events → **2,610 rows** (partner 2,152 /
  issuer 337 / acquirer 59 / target 59 / subject 3); agent
  `derive_event_participants` registered, provenance converged,
  `make schema-dump` clean, integrity exit 0 after `graph-rebuild`.
  `derive_hyperedges` builds the `event` hyperedge family from the
  store when present (label emitted at `len(members) >= 2` —
  guidance/management_change singletons stay out), falling back to the
  `events` pair query filtered to mapped types on pre-v16 restores.
  **Measured dead end recorded**: a speaker→event join (density_dq's
  "speakers feed k≥3" hope) does not exist upstream —
  `events.as_of_edition` free-text vs quote stems joins 0/1,477,
  `source_quote` exact-matches 0 rows, 40-char substring rescue 14/461
  (3%). k≥3 arrives with data, not SQL. 11 tests.
- **S5 DONE 2026-10-19.** `doc/design/ontology.md` §2.6 rewritten (the
  "No person type" contradiction is gone: `person` exists, opened by
  #267, now 1,250 entities with the S2 map story); the speaker
  catch-all sentence retired in favour of the map; `event_roles`
  roster extended with `issuer`/`subject` (S4 vocabulary);
  `ontology_convention_stack.md` §6 non-goals annotated — person-type
  and event_participants items SUPERSEDED by S2/S4, DIN still blocked.
  Eval gate (`ontology_eval_gate.py --candidate …`, undeclared-ACCEPT
  81/81) is an arc-end gate item — runs with the full gates once the
  operator calls them.

## 5. Acceptance

- **Eval-gate bullet (roster + edge semantics change):**
  `ontology_eval_gate.py` over the frozen question set, dry-run vs
  canonical apply, undeclared-ACCEPT must stay 81/81.
- S2: no quote row loses its speaker string; resolution is additive
  (`speaker_entity` or side-table), NULL-speaker rows untouched.
- S3: membership rows carry `source_ref` + `agent_id` provenance per
  the #234 writer/reader obligations table.
- S4: participant insert is idempotent per (event, participant, role).
- Targeted: person_dedup/name_dedup tests, derive_events tests, new
  lane tests. Full `make qa` once at arc end on the operator's go.

## 6. Risks & non-goals

- **Merge hazard is the whole risk**: an HUF never merges into the
  individual (#293 class). The NEVER-MERGE ledger is load-bearing —
  S2 must route through it, never around it.
- No person notes in the vault, no person embeddings, no UI surfaces
  (the #298 non-goals stand until this arc amends them explicitly).
- No DIN scraping; no appointments inference from prose beyond the
  existing management_change filters.
- `person` count growth (30 → 277 in a month) needs a provenance audit
  in S1 before anything links to it — if the 247 are rename-folds, not
  new holders, the census must say so.

## 7. References

- `doc/improvements/archive/graph/person_resolver_lane.md` (executed
  2026-09-25) — opened the D6 row; quote-speaker deferral.
- completed.md #267 (SHP holder persons), #398/#399 (identity
  registry, legal-line dedup), #234 (ORG conventions, §8-R3), #150.
- `doc/design/ontology.md` §2.6, `ontology_governance.md` §7,
  `hyperedge_density_dq.md` (companion, event n-ary).
