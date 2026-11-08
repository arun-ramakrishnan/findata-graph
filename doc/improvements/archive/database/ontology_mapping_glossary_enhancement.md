---
title: "Mapping record glossary: add repo-native mapping entries for the processed graph edge types (semantics only — magnitudes live in db_schema.md)"
status: executed
filed: "2026-11-08"
executed: "2026-11-08"
completed_md: "493"
area: "doc/reference"
---

# Mapping record glossary enhancement

**Date:** 2026-11-08 · **Status:** EXECUTED 2026-11-08 (7 repo-native glossary entries, semantics-only; db_schema.md magnitudes refreshed in the same sitting) · **Proposal:** completed.md #493

Follows `ontology_governance.md` (S0 master doc + roster-drift, S1 concept
lifecycle, S2 deterministic change gate) and
`doc/reference/ontology_glossary.md` (S5 glossary of cited external
terms). **Finalize AFTER `star_hyperedge_consumer_wiring.md` (#492) S0
lands** — its reconciliation re-measures the star shapes this glossary's
convention lines must match.

The mapping record glossary already exists at
`doc/reference/ontology_glossary.md` — referenced by
`doc/design/ontology.md:26` as the citations/licenses home. It currently
holds **six** external borrowed-vocabulary entries (ORG
`subOrganizationOf`; NIC-2008↔sub_sector crosswalk; schema.org
`Corporation`; FIBO BE `PrivateLimitedCompany`; SKOS `exactMatch`;
PROV-O `wasGeneratedBy`) plus the fixed citation-policy section. There
are **no repo-native mapping entries** for the graph edge types /
hyperedge families processed since the glossary was written:

- Dyadic: `subsidiary_of`, `invested_in` (stake semantics),
  `listed_on_index`, `supplier_to`, `same_group`, `semantic_peer`,
  `co_mentioned_in` (retired to projection)
- Hyperedge families referenced by those entries: `index`, `subsidiary`,
  `supply` (standing structure; consumers land in #492)

No code change, no registry change, no schema version move — doc-only.
The roster-drift static check is unaffected (glossary entries are not
gated rosters).

## 1. TL;DR

Add a "Repo-native mapping records" section to
`doc/reference/ontology_glossary.md`, one entry per mapping, each
carrying **Term / Kind / Direction / Semantics / External referent or
house convention / Star projection** — and **no live row counts**.

**Magnitudes live in `doc/design/db_schema.md`, not here.**
`ontology.md` §1 is explicit: "magnitudes — row counts, census sizes,
fill rates — never live here (they live in `db_schema.md`, measured
against the registry)". Rev 1 of this proposal claimed the glossary as
the measured-magnitudes home — that inverted the rule it quoted and
would have created a second staleable-baseline home (the exact
`_FILING_MB` failure closed in completed.md #491). The glossary carries
semantics + a dated "see db_schema.md §…" pointer per entry;
`db_schema.md` gets the counts, refreshed in the same sitting (its
header "Live row counts" block is dated 2026-09-14 and stale — refresh
it here too).

Entries proposed (one per mapping; ordering structural → participation →
derived → retired):

| Term | Kind | External referent | House convention (summary) |
|---|---|---|---|
| `subsidiary_of` | dyadic | ORG `subOrganizationOf` (refinement) | filer-claimed control (VIGIL) per related_party_sync ladder; sibling descriptions rehomed to `same_group`; star projection = `subsidiary` family (#492 S0 shape) |
| `same_group` | dyadic, symmetric | — (house) | fellow-subsidiary / peer regrouping from tightened `subsidiary_of`, widened per the group-widening record; `$.group` property is the hyperedge label |
| `invested_in` | dyadic | — (house) | stake_pct (shp filings) / pctHeld (yfinance) normalized to percent via `shareholding_sync.stake_pct_of`; `weight` is mixed-kind ranking, never a stake |
| `listed_on_index` | dyadic | — (house) | index membership; star projection = `index` family (57 hyperedges; the dyad type is excluded from the dyadic centrality projection, hub-noise ruling) |
| `supplier_to` | dyadic | — (house) | supplier → buyer, one row per reported supply relationship; star projection = `supply` family (#492 S0 shape) |
| `semantic_peer` | dyadic, symmetric | — (house) | embedding-neighbour cosine, brute-force `array_distance`, no ANN |
| `co_mentioned_in` | dyadic, RETIRED | — (house) | retired 2026-10-19 (#490); projected on read from `h_edge` family `edition` (294 hyperedges) — entry documents the projection contract, not stored rows |

## 2. Evidence (all measured 2026-11-08 on this box)

- `doc/reference/ontology_glossary.md` — six external entries +
  citation policy; no repo-native edge mappings.
- `doc/design/ontology.md:10-13` — anti-drift rule verbatim: "magnitudes
  — row counts, census sizes, fill rates — never live here (they live in
  `db_schema.md`, measured against the registry)". Rev 1 misquoted this;
  rev 2 complies.
- `doc/design/db_schema.md` §`graph_edges` (line 90) and §`hyper_edges`
  (line 348) are the designated magnitude surfaces; the header "Live row
  counts" block is dated 2026-09-14 — stale, refreshed in this arc.
- Live `graph_edges` dyad counts (SQLite, 2026-11-08): supplier_to
  17,174; subsidiary_of 7,559; listed_on_index 6,615; same_group 10,814
  (10,762 `vigil:rpt:*` + 52 non-VIGIL); semantic_peer 8,917;
  invested_in 2,128; competes_with 3,579; jv_with 1,108; cited_in 2,028;
  listed_in 930; acquired 57; belongs_to 184; rated_by 214;
  regulated_by 17; exposed_to 359; customer_of 1; approved_by 8;
  penalized_by 0 (registered, unpopulated); co_mentioned_in 0 (retired,
  projected on read).
- Live star shapes (2026-11-08, pre-#492-S0): index 57 hyperedges, 753
  members in ≥2 indexes (exact), 1,274 in ≥1; subsidiary 738 / supply
  8,605 hyperedges — **both awaiting #492 S0's threshold reconciliation;
  the convention lines below cite the family, db_schema.md §hyper_edges
  carries the post-S0 counts**.
- co_mention projection: served pair view 11,315 pairs (polyadic #490
  record, re-verified); edition family 294 hyperedges (261 with ≥2
  members). Rev 1's "1,644 edition sets" was unsupported — dropped.
- `doc/design/ontology.md` §2.1 roster `edge_types` (gated) — 19 types
  incl. all terms above; `co_mentioned_in` retained in roster though
  retired from storage (projected on read). §2.5 roster
  `hyperedge_types` (gated) — 15 families.

## 3. Design

Append a "Repo-native mapping records" section to
`doc/reference/ontology_glossary.md`, one entry per mapping:

```markdown
## repo `invested_in` — participation / stake edge

- **Kind:** dyadic relation, `graph_edges` (direction: holder → holding)
- **Semantics:** `stake_pct` (shp filings leg) / `pctHeld` (yfinance
  leg), both normalized to percent via `shareholding_sync.stake_pct_of`.
  The `weight` column is mixed-kind ranking across legs — never a stake.
  Structural shareholding-page rows (subtotals, totals, equation lines,
  subcategory labels) carry no stake.
- **External referent:** none (house term); FIBO BE has no exact
  "reported holder stake" property — do not force a mapping.
- **Magnitudes:** see `doc/design/db_schema.md` §`graph_edges`
  (counts dated there; this entry deliberately carries none).
```

Rules baked into every entry:

- **No live row counts** — one dated pointer to `db_schema.md` instead
  (anti-drift compliance; see TL;DR).
- **Dyad vs star kept distinct** — the entry documents the dyadic edge;
  a single "Star projection" line names the `hyper_edges` family and
  defers its shape/counts to #492 S0 + `db_schema.md` §`hyper_edges`.
  No merged dyad+star entries (rev 1 conflated them for
  `listed_on_index`/`supplier_to`).
- **Retired entries document contracts, not rows** (`co_mentioned_in`:
  the projection contract — which family, which join, degeneracy guard).

## 4. Acceptance

1. `make md-lint` green on `doc/reference/ontology_glossary.md`.
2. `make search-fresh APPLY=1` then plain `make search-fresh` (rc=0) —
   doc index converges.
3. Ontology roster-drift static check unchanged (no registry touched).
4. **`db_schema.md` refreshed in the same sitting**: §`graph_edges`
   prose counts re-measured (match the live DB at write time), header
   "Live row counts" block re-dated, §`hyper_edges` family magnitudes
   reflecting #492 S0's landed shapes.
5. Every excluded edge type has a one-line disposition (see §5) —
   no silent omissions.
6. Operator sign-off on the entry set (which terms to include; ordering).

## 5. Non-goals / risks — with per-type dispositions

Not a glossary of *all* 19 registered types. The excluded 11, each with
its reason:

- `belongs_to` / `listed_in` — structural membership whose canonical
  store is the hypergraph (`sector`/`country` families; dyads project
  from them); mapping-record treatment belongs to the family entries in
  `db_schema.md` §`hyper_edges`, not duplicated here.
- `cited_in` — provenance edge (okf_activation P: display-only),
  documented by the OKF contract, not a vocabulary mapping.
- `acquired` / `jv_with` — event-provenance types (promoted into
  `events` + `event` hyperedges by derive_events); their semantics live
  in `db_schema.md` §`events`. If the operator wants glossary entries,
  they are a clean follow-up — not blocking.
- `competes_with` — coarse co-ranking signal with no external referent
  candidate; low documentation value, excluded (revisit on demand).
- `exposed_to` / `rated_by` / `regulated_by` / `approved_by` /
  `customer_of` / `penalized_by` — small-row (≤359) lanes with
  self-describing names and no external referent; excluded to keep the
  glossary to mappings that repay citation care. `penalized_by` is
  registered-unpopulated and needs no entry at all.

Risk (accepted): a doc-only arc can still drift — mitigated by rule 4's
`db_schema.md` refresh and the existing dated-pointer convention; a
roster-style static check for prose counts is deliberately NOT added
(the anti-drift rule's answer is one magnitudes home, not a second
checker).

## 6. Gate reference

`doc/procedures/gates.md` full gates ONCE per arc on operator go; this
is doc-only so md-lint + search-fresh are the relevant gates.

## 7. Revival register

None — new work, no prior deferral. The `ontology_governance`
mapping-record-glossary item (a) awaited a metrics Q&A lane proposal
(trigger unmet); this proposal ships the glossary content directly,
de-facto unlocking item (a) without the Q&A lane. Rev 2 adds the
hard ordering: **#492 S0 first** (its re-measure feeds this arc's
`db_schema.md` refresh and the star-projection lines).
