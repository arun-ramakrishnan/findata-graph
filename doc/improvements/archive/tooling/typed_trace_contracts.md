---
title: "Typed trace contracts — validated ingest with a per-harness parser registry"
status: executed
filed: "2026-10-02"
executed: "2026-10-02"
completed_md: "335"
area: "bench_data/code/agent_traces.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Typed trace contracts — validated ingest with a per-harness parser registry

**Date:** 2026-10-02 · **Status:** EXECUTED ·
**Area:** `bench_data/code/agent_traces.py` (ingest/loader path);
measurement record `doc/local/engineering/capture_traces.md` §10

Adapted (not adopted) from the whiteboard evaluation — see
"Follows" provenance below.

## 1. Motivation

The behavioral-trace store's ingest path is positional and stringly:
each harness's raw rows flow into the star schema through loader code
that silently widens on unexpected shapes. Two concrete failure classes
follow: a harness-side schema change (a renamed field, a new event
family) lands as NULLs or type drift with no signal at harvest time;
and adding a harness means editing the loader rather than registering a
parser. The read side was hardened by the trace-analyzer arc
(`trace_analyzer_legs.md`, completed.md #303) — the
write side has had no equivalent pass.

The whiteboard evaluation (2026-10-02,
`doc/local/evaluations/whiteboard_assessment.md`) showed the pattern
worth adapting: a versioned typed-event contract module plus a
parser-per-harness registry, validate-at-ingest, with drift surfacing
at load time. Their display-oriented four-kind union is explicitly NOT
the model — our star schema is finer-grained and stays.

## 2. Evidence (measured 2026-10-02, this box)

Structure facts (schema-level, from a read-only census recorded in
`doc/local/engineering/capture_traces.md` §10): eight tables; the five
fact kinds and their identity columns; `load_log` carries per-source
load rows. Live row counts and session counts stay in the §10 record
(data discretion — machine-local store specifics live in `doc/local/`).

| Aspect | Today | With contracts |
|---|---|---|
| new harness onboarding | edit loader internals | register a parser module |
| harness field rename | silent NULL / drift | row rejected + counted at ingest |
| ingest shape documentation | implicit in loader code | the contract module IS the spec |
| drift visibility | none until a report query misses | load-time counts in `load_log` + report leg |

Ruled out: adopting the four-kind display union (a downgrade — our
fact tables are the query surface); pydantic or any new dependency
(stdlib dataclasses suffice; the repo already refuses new deps for
this class of work).

## 3. Design

Contracts mirror the EXISTING star schema — one frozen dataclass per
fact kind (turn, tool_call, model_request, event, file_edit) plus the
session dimension — each carrying its field spec (name, type,
required/optional). A `@register_parser("<harness>")` registry keys the
existing harness parsers; the loader dispatches through it and owns no
harness-specific code.

- **S1 — contracts + registry (pure refactor):** extract the contracts
  module beside the loader; move the existing three harness parsers
  under the registry. Behavior-preserving: full-rebuild parity (per-table
  row counts + content checksums identical pre/post) is the acceptance
  gate, run twice.
- **S2 — validate-at-ingest:** every row passes its kind's field spec
  before insert. Unknown-kind rows and unknown fields are rejected AND
  COUNTED, never silently widened. `load_log` gains outcome columns
  (rows_ok / rows_rejected / unknown_field counts). The store is
  machine-local and rebuildable, so the `load_log` schema addition rides
  a full rebuild; rebuild cost is measured at execution and recorded in
  §10. Policy starts as warn+count (a clean census of live stores must
  precede any reject-by-default flip).
- **S3 — synthetic fixtures + contract tests:** per-harness fixture
  files with SYNTHETIC minimal rows (shapes only — no real transcript
  content reaches tracked files, per the data-discretion rule);
  mutation checks: dropping a required field → RED; smuggling an
  unknown field → RED.
- **S4 — report drift leg:** per-harness validation summary (unknown
  kinds/fields, rejection counts) appended to the agent_traces report
  surface that #303 built.

Order: S1 unblocks S2 (validation needs the specs); S3 can land with
S1; S4 last.

## 4. Acceptance criteria & shakedown

1. S1 parity: two consecutive full rebuilds produce byte-identical
   per-table row counts and content checksums vs the pre-refactor
   rebuild (commands + outputs recorded in §10).
2. S3 mutation pair: strip a required field → contract test RED;
   restore → GREEN. Same for an unknown-field injection.
3. S2 census: live-store ingest reports zero unknown fields, or every
   residue counted and dispositioned in §10 before any reject flip.
4. `make qa` stays green; targeted tests only for the touched files
   (per the gate-arc protocol — no full-suite runs mid-arc).
5. No new dependencies (`pyproject`/lock unchanged).

No ontology eval gate: this arc does not alter query-visible knowledge-
graph semantics (no rosters, crosswalks, hierarchies, or extractor
rules) — the trace store is an analytics surface, not the graph.

## 5. Risks

- **Live stores already carry drift** — S2 warn-first policy and the
  clean-census precondition handle it; a premature reject flip would
  strand real rows.
- **Rebuild window discipline** — full-rebuild parity runs hold the
  store's write lock; schedule against parallel sessions (the
  io-lock/flock machinery already serializes this class of writer).
- **Over-modeling** — contracts stay 1:1 with existing tables; any
  temptation to "improve" the schema belongs to a separate proposal.

## 6. Non-goals

- No star-schema redesign, no new fact kinds, no new harness parsers
  (the registry ships with the existing three moved over).
- No egress/upload/consent machinery — local-only store; the
  consent-gating doctrine line rides the citations proposal instead.
- No change to the report's existing legs beyond the S4 append.
- Whiteboard's display union: not adopted, recorded here so future
  sessions don't re-derive it.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-02 | read-only census (information_schema + counts) | §10 of `doc/local/engineering/capture_traces.md` | 8 tables; identity columns per kind; `fact_event` reaches sessions via `trace_id` |
| 2026-10-02 | S2–S4 implemented | warn+count validation in `trace_contracts`, `load_log.rows_ok/rows_rejected/unknown_fields`, synthetic per-harness fixtures, `report --legs validation` leg; live store shows old `load_log` rows accepted=0/unknown=0 until fresh load | tests `tests/test_trace_contracts_s2_s4.py` pass |
| 2026-10-02 | S2 ingest verified (fresh loads after wiring) | fresh `agent_traces.py load` populates outcome columns with real counts; zcode/opencode census clean (0 rejected, 0 unknown fields), prime census: 20 type_errors rejected + 8 kept (0 unknown fields) | S2 census precondition met; no reject-by-default flip |

**Follows:** evaluation of devdotfast/whiteboard's `trace-protocol`
package (`doc/local/evaluations/whiteboard_assessment.md` addendum,
2026-10-02) — the typed-contract + parser-registry pattern is adapted;
their taxonomy, hosting, and egress designs are not.
