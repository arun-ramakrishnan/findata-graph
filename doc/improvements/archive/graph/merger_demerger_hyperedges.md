---
title: "Merger/demerger n-ary hyperedges — stop forcing ternary facts into dyad vocabulary"
status: executed
filed: "2026-10-21"
executed: "2026-10-21"
completed_md: "426"
area: "helpers/graph"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Merger/demerger n-ary hyperedges — stop forcing ternary facts into dyad vocabulary

<!--
House proposal skeleton (matches the executed corpus's shape). Rules:
- File the proposal BEFORE implementing multi-slice work (house rule 2026-08-21).
- Every number is MEASURED on this box — keep the raw log in the Appendix.
- On EXECUTED: git mv to ../archive/<topic>/, completed.md entry (unique
  number), pending.md sweep, archive/README topic line, README pointer reset.
-->

**Date:** 2026-10-21 · **Status:** PROPOSED ·
**Area:** `helpers/graph` (triage, hyperedge derive, schema, extractor)

## 1. Motivation

The relation triage lane has no honest way to express a merger or demerger.
Today the extractor funnels the H1 phrases `merged with` / `demerged from`
into the **reverse-`acquired`** branch (`extract_target_binding.md`), so a
merger is stored as an acquisition — a semantic lie. The triage accept
vocabulary (`_ACCEPT_EDGE_TYPES`, `triage_pending_relations.py:84`) has 12
dyadic types and none of them fits:

| Surface phrase | Correct semantics | Current mapping |
|---|---|---|
| "A merged with B" | ternary: A + B → survivor | `acquired` (reverse) — wrong |
| "X demerged from Y" | directional parent → child | `acquired` (reverse) — wrong |
| "A and B formed JV" | dyadic JV | `jv_with` — fine (not this arc) |

**Trigger:** live triage demo 2026-10-21, row 5 (`LTM —[acquired]→ Larsen &
Toubro Infotech`, quote "formed through the merger of Larsen & Toubro
Infotech"). Reviewer caught it; the only legal dispositions were `d`
(discard) or `a/same_group` (a distortion). Two more rows in the same demo
carried demerger language. This is a recurring class, not a one-off.

**Cost measured today:** every merger/demerger mention is either silently
stored as `acquired` (wrong edge, pollutes centrality/MST/CSR consumers) or
discarded by a careful reviewer (information lost). Neither is acceptable.

## 2. Evidence (measured 2026-10-21, this box)

| Configuration | Result | Verdict |
|---|---|---|
| status quo | `acquired` reverse-mapping; reviewer discards | baseline (lossy) |
| n-ary hyperedge | express A+B→C honestly | adopt (below) |

The graph already owns the mechanism that fits: `hyper_edges` +
`hyper_incidences` (n-ary incidence store, `migrate_to_graph_edges.py:143`),
built for exactly "set-valued facts a singular dyad cannot express" and
precedented by the canonical sector hyperedges (Kuantum Papers dual
membership) and role-tagged event hyperedges (`EVENT_ROLE_BY_TYPE`:
acquirer/target, partner/partner). Adding `merger`/`demerger` as **hyperedge
types** reuses this machinery and leaves the dyad vocabulary untouched (no
12-type set churn, no query-projection ripple, no Mojo parity break).

Ruled out: adding `merged_with`/`demerged_from` dyad edge types — a merger
is not a dyad; a dyadic A–B edge cannot carry the survivor C, and the
ripple (query.py projection, snapshot parquet, Mojo parity, static checks)
is large for a semantically wrong model.

## 3. Design

- **S1 — schema:** add `merger` / `demerger` to the hyperedge type universe
  (`hyper_edges.edge_type`), documented in `doc/design/architecture.md` and
  the derive module docstring. **As built:** the vocabulary is a CODE
  registry (`helpers/graph/hyperedge_types.py`), because
  `hyper_edges.edge_type` carries NO DDL CHECK — a family is a registry
  entry, not a migration (so the schema version does not move). The
  registry is gated two ways: the ontology roster check
  (`<!-- roster: hyperedge_types -->` in `doc/design/ontology.md` §2.5,
  both directions) and the integrity gate's `check_hyperedge_types`
  (unknown family / bad role = gate-failing).
- **S2 — derive:** a merger/demerger derivation from the triage decisions
  file (see S3) — role-tagged incidences: merger = A `constituent`, B
  `constituent`, C `resultant`; demerger = parent `source`, child
  `resultant`. Deterministic, idempotent via `UNIQUE(edge_type, label)`.
  **As built:** the lane is its own module
  (`helpers/graph/derive_merger_hyperedges.py`, `make derive-merger-hyperedges`,
  dry-run default) rather than a branch inside `derive_hyperedges.py` —
  same precedent as `derive_org_membership.py` / `derive_event_participants.py`
  (one module owns one family, and the owner converges — shrinkage drops
  facts the journal no longer holds). The lane reads the decisions journal
  only: a fact exists after a human accepted it, and it never writes a
  `graph_edges` row.
- **S3 — triage mapping:** relation rows whose edge/verb is merger or
  demerger route to the incidence store instead of the dyad accept path. The
  review menu gains verbs to express them; decisions journal to
  `findata/Misc/_pending_triage_decisions.jsonl` as today, consumed by S2.
  **The journal row contract S3 must write (fixed by S2, so S3 implements
  to it):** a journal row carries `edge_type` ∈ {`merger`, `demerger`} plus
  — for `merger` — `constituents` (list of company names, ≥2, distinct) and
  optional `resultant` (the survivor); for `demerger` — `parent` (the
  company that split) and `child` (the spun-off company). Acceptance is the
  house verb `accept:<edge_type>[:<target>]` (`accept:merger`,
  `accept:merger:<resultant>`, `accept:demerger`, `accept:demerger:<child>`,
  and a bare `accept` all count); a verb naming a DIFFERENT edge type than
  the row is a contradiction and the row is skipped. Members must already
  exist in `entities` — S3's review lane owns entity creation, S2 only
  reports unresolved names.
- **S4 — eval gate:** `helpers/misc/ontology_eval_gate.py` over the frozen
  question set, between dry-run and canonical apply. This is a
  query-visible semantics change → the gate is mandatory.
- **S5 — docs + close-out:** proposal archived, `completed.md` entry,
  README live-list reset, `make search-fresh APPLY=1`.

## 4. Acceptance criteria & shakedown

1. A merger row triaged via the new verbs lands as one `hyper_edges` row +
   three role-tagged `hyper_incidences` rows (source entity check: rows
   exist, roles correct).
2. The dyad graph is byte-identical before/after for non-merger rows
   (no regression in existing accepts).
3. Eval gate passes with zero regressions and the declared merger/demerger
   question-set improvements materialize.
4. `make qa` stays green (static checks include the new hyperedge types).

| Projected outcome | Today | After |
|---|---|---|
| merger/demerger rows stored honestly | 0 | 100% of triaged |
| `acquired` reverse-misfires | present (recurring) | 0 new |
| dyad vocab / query projection churn | — | none |

## 5. Risks

- **Ternary survivor ambiguity** — a merger may name the survivor (label) or
  not (synthetic label). Mitigation: label = survivor entity when named,
  else a deterministic synthetic key; the raw quote stays in `properties`.
- **Role vocabulary drift** — `constituent`/`resultant`/`source` must stay in
  lockstep with `EVENT_ROLE_BY_TYPE` conventions. Mitigation: same shared-map
  pattern the event lane already uses. **Resolved in S1:** the merger roles
  are deliberately NOT event roles — `constituent`/`resultant`/`parent` are a
  per-family vocabulary (the same discipline as `org_membership`'s
  promoter/holder), held in `helpers/graph/hyperedge_types.py` and gated
  against the live rows by `check_hyperedge_types`.
- **Old misfires remain** — existing `acquired` rows that are really
  mergers (e.g. `Lunolux → Shapoorji Pallonji & Company`) are out of scope;
  a `--redecide` pass can revisit them later. Noted as non-goal.

## 6. Non-goals

- Adding dyad edge types `merged_with`/`demerged_from` to `_ACCEPT_EDGE_TYPES`.
- Re-grading historical `acquired` rows (separate `--redecide` cleanup).
- Event-lane changes (this is edges/hyperedges; events are a separate spine).

## 7. Progress

| Slice | State | Evidence (2026-10-21) |
|---|---|---|
| S1 vocabulary | **done** | `helpers/graph/hyperedge_types.py`; roster gate in `doc/design/ontology.md` §2.5; `check_hyperedge_types` in the integrity gate (live: 1,708 rows, 0 unknown, 0 bad role) |
| S2 derive lane | **done** | `helpers/graph/derive_merger_hyperedges.py` + `make derive-merger-hyperedges`; 15 tests in `tests/test_merger_hyperedges.py`; live dry-run: 0 facts (no merger/demerger decision accepted yet — expected, S3 is what produces them) |
| S3 triage mapping | **done** | extractor emits `merger`/`demerger` sidecar rows (never dyad edges — `merged with`/`demerged from`/`formed through the merger of`, directions `forward`/`reverse`/`resultant`); sitting verbs `a[/merger[/NAME]\|/demerger[/NAME]]` build structured members, journaled verbatim; `_parse_nary_accept` validates them at apply time (member-exists, ≥2 distinct constituents, family contradictions are errors); `make derive-merger-hyperedges` in the follow-up chain. Tests: `tests/test_merger_triage.py` (20), restaged pins in `tests/test_extract_relations_{extraction,resolver}.py` |
| S4 eval gate | **done** | new shape `hyperedge_labels` in `helpers/misc/ontology_eval_gate.py`; questions `hyper-merger-families` + `hyper-demerger-families` (expected `[]`) in `helpers/misc/ontology_questions.json` v4 (164→166); gate run parent-vs-candidate (live DB copies, no-op derive apply): **ACCEPT — 166 questions, 0 reason(s)** |
| S5 docs + close-out | **done** | `doc/design/ontology.md` §2.5 roster, `doc/design/architecture.md` §4, `doc/design/db_schema.md`; archived to `doc/improvements/archive/graph/merger_demerger_hyperedges.md`; completed.md #426; live-proposal list reset |

All five slices landed and the lane took its **first live facts** on
2026-10-21: a six-row sitting (mercury pre-annotated) produced five
merger/demerger hyperedges in `memory/research.db` — `LTM` (LTM:resultant +
Larsen & Toubro Infotech/Mindtree:constituent), `merger: Clix Capital +
Lakshmi Vilas Bank`, `merger: Devyani International + Sapphire Foods India`,
`Bajel Projects`, and `Motherson Sumi Wiring India` — with **zero** dyadic
`merged_with`/`demerged_from` rows. Two apply-time bugs surfaced and were
fixed with regression tests: the apply re-derived members from the raw
triple instead of honouring the sitting's journaled entity names, and the
`resultant` direction made the verb's name both constituent and resultant
(UNIQUE violation). Live integrity `check_hyperedge_types`:
`{'total': 1713, 'unknown_type': 0, 'bad_role': 0, 'errors': 0}`; dry-run
idempotent; 485 targeted tests pass.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-21 | triage demo row 5 | `d` | merger unexpressible; captured |
| 2026-10-21 | `triage_pending_relations.py --apply-decisions` | 1 edge written, 6 decisions | 2 accepts deduped; 3 discards |
| 2026-10-21 | `grep _ACCEPT_EDGE_TYPES` | 12 dyadic types | no merger/demerger |
| 2026-10-21 | live DDL `hyper_incidences` | role column present | S4 precedent |
