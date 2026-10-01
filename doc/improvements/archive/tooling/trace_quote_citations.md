---
title: "Trace-quote citations — stable event anchors and a resolver for doc references"
status: executed
filed: "2026-10-02"
executed: "2026-10-02"
completed_md: "336"
area: "helpers/misc/trace_quote.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Trace-quote citations — stable event anchors and a resolver for doc references

**Date:** 2026-10-02 · **Status:** EXECUTED ·
**Area:** `helpers/misc/trace_quote.py` (new resolver CLI) + the trace
store's read path; measurement record
`doc/local/engineering/capture_traces.md` §10

Adapted (not adopted) from the whiteboard evaluation — see "Follows"
provenance below.

## 1. Motivation

Arc records say what was decided but rarely point at the trace of the
turn that decided it. Today, connecting a proposal's claim to the
underlying agent-session evidence means re-running searches against the
conversation corpus and hoping the row is still where memory says it
was. The whiteboard evaluation showed the missing consumer layer: an
event-id-addressable citation (`review-trace:<traceId>#<eventId>` in
their scheme) that resolves to the exact quoted event — clickable
provenance from document to decision.

We already have both halves separately: a fine-grained behavioral-trace
star schema (every fact kind carries a declared identity column), and
the pointer-resolution precedent (`convo_query --expand POINTER`).
This arc joins them: a citation grammar over our store plus a resolver
CLI, so proposals, completed.md entries, and engineering records can
cite `agent-trace:...` tokens that any future session resolves in one
command.

## 2. Evidence (measured 2026-10-02, this box)

Join-path verification (schema-level, recorded with live counts in
`doc/local/engineering/capture_traces.md` §10): every proposed anchor
kind has a declared key today — `fact_turn(turn_id)`,
`fact_tool_call(tool_call_id)`, `fact_model_request(request_id)`,
`fact_event(span_id)` — and the one schema wrinkle is already mapped:
`fact_event` has no session column, so an event anchor resolves
span_id → trace_id → `dim_session` (two joins, all columns present).

| Capability | Today | With citations |
|---|---|---|
| cite a specific decision turn | prose description, no pointer | `agent-trace:<sess>#turn:N` token |
| resolve the pointer | manual store queries | one CLI command, context included |
| rot detection | none | advisory doc-hygiene leg flags unresolved tokens |
| id stability guarantee | unknown (never measured) | measured + documented anchor set |

Ruled out: citing by timestamp (collides within a burst of tool calls);
citing by prose quote (drifts on any re-render); a TUI lane now
(non-goal — the CLI is the consumer).

## 3. Design

Grammar: `agent-trace:<session_id>#<anchor>` with anchor ∈
`turn:<turn_id>` | `tool:<tool_call_id>` | `event:<span_id>` |
`req:<request_id>` — mirrors the whiteboard shape but over our keys.

- **S1 — grammar + resolver CLI (`helpers/misc/trace_quote.py`):**
  `trace_quote <citation>` validates the token, resolves it, and prints
  the row plus context (for a turn: its model requests and tool calls;
  for a tool call: the parent turn and adjacent calls; for an event:
  the parent-span chain). Unknown id → near-miss suggestions
  (same session, closest ts), exit 1. No store change — works against
  the current schema. Precedent: `convo_query --expand`'s pointer form.
- **S2 — anchor-stability harness:** measure which identity columns
  survive a re-ingest unchanged (harness-native) vs which are derived
  ordinals that re-number on rebuild. Method: re-ingest parity run;
  per-column match-rate table recorded in §10. The DOCUMENTED stable
  set becomes the legal anchor set; any unstable kind is either
  re-based on its stable parent key or marked caveat-anchored in the
  grammar docs. Citations minted before S2's verdict carry a
  re-verify note.
- **S3 — doc wiring + advisory resolve check:** convention (grammar,
  when to cite) in the resolver docstring with the durable record in
  `doc/local/engineering/capture_traces.md`; an advisory doc-hygiene
  leg resolves every `agent-trace:` token under `doc/**` and reports
  unresolved ones (reference-rot sweep pattern, non-blocking).

Order: S1 first (immediate value, zero store change); S2's census also
feeds the typed-contracts proposal's id-field specs; S3 last so the
advisory leg only ever sees the stable anchor set.

## 4. Acceptance criteria & shakedown

1. Resolver shakedown against the live store: one citation per anchor
   kind resolves with correct context (scripted, output shapes pinned
   in tests via SYNTHETIC fixtures — no real transcript content in
   tracked files, data-discretion rule).
2. Unknown-id behavior pinned: near-miss suggestion list + exit 1,
   mutation-verified (strip the suggestion path → RED).
3. S2 stability table exists in §10 with per-column match rates across
   ≥2 re-ingest runs (never one run for an idempotence claim).
4. S3 advisory leg: green on a seeded valid citation, RED on a broken
   token (mutation pair), and runs non-blocking.
5. `make qa` stays green; targeted tests only (gate-arc protocol).
6. No new dependencies.

No ontology eval gate: no query-visible knowledge-graph semantics
change (analytics store consumer only).

## 5. Risks

- **Anchor instability across rebuilds** — the core risk; S2 measures
  before the convention spreads, and the advisory leg catches any
  already-rotted token at every doc sweep.
- **Store absent on a fresh clone** — resolver fails cleanly with a
  pointer to the harvest step (machine-local store by design).
- **Citation payloads leaking into tracked docs** — mitigated by rule:
  tracked docs carry the TOKEN only; payload stays in the store and
  `doc/local/`.

## 6. Non-goals

- No TUI/web rendering of citations (a future lane if the CLI earns
  it); no citation auto-insertion into docs by agents; no ingest
  changes (that is the typed-contracts proposal);
- No egress/upload/sharing of traces — local-only, and the standing
  consent-gating doctrine applies to any future upload path by
  construction.
- No backfill of old arc records with citations — new citations are
  minted going forward.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-02 | read-only census (identity columns, join path) | §10 of `doc/local/engineering/capture_traces.md` | all four anchor kinds have declared keys; event→session is the two-join `trace_id` path |

**Follows:** evaluation of devdotfast/whiteboard's `trace_quote` block
+ `review-trace:` URI scheme (`doc/local/evaluations/whiteboard_assessment.md`
addendum, 2026-10-02) — the citation grammar + resolver pattern is
adapted; their canvas/UI, hosting, and their four-kind event union are
not.
