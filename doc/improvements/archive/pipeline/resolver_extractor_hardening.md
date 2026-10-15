---
title: "Resolver / extractor hardening — residual slices S4–S8"
status: executed
filed: "2026-10-15"
executed: "2026-10-15"
completed_md: "396"
area: "helpers/core, helpers/graph, findata/Companies"
---

# Resolver / extractor hardening — residual slices S4–S8

S1 (resolution normalization), S2 (fuzzy tie-break audit / defer on near-ties),
S3 (section-head noise gate), and S4 (stub-template placeholder removal) are
landed as of 2026-10-15. S3 lives in `helpers/core/parse_newsletter.py`
(`_is_noise_entity` + `_NOISE_NAME_RE`); S4 removed the chatter block from
`render_stub` and stripped it from 26 existing stubs.

Remaining slices (absorb unchanged from the filed parent proposal):

- **S4 stub-template placeholder** (P5) — DONE 2026-10-15 (moved up).
  `helpers/core/parse_newsletter.py`) emits `## The Chatter — <edition title>`
  into every stub — junk plus an idempotency landmine for block-presence
  checks. Accept: stubs carry no chatter block; all existing stubs' placeholder
  blocks stripped in the same change.
- **S5 sector precedence** (P6): `guess_sector_for` gave Financial_Services for
  a payments company (Fintech_Payments carve-out missed singular "Payment") and
  Consumer where the newsletter header itself said Retail; exchange seed had
  Century Plyboards under Consumer (plywood → Building_Materials). Accept:
  carve-out patterns cover singular forms; header-declared sector wins when it is
  one of the 42 canonical sectors; one-off fix pass moves the 3 known misfiled
  notes.
- **S6 worklist routing for analyst-voiced sections** (P8): JM Financial case —
  a BFSI sector preview by JM's own analyst, zero company content, landed on
  the company worklist. Accept: Stage-4 worklist (`emit_worklist`) marks sections
  whose speaker title is not company management (analyst / research-lead) and
  content spans ≥3 named companies as `sector_commentary`, so the enhancer
  routes to the sector note, not the company note.
- **S7 cross-check/integrity transient** (P9): 3/3 parse runs with
  `--cross-check` active and NEW>0 failed the Stage-5 integrity leg; standalone
  runs pass. Accept: root cause identified (suspected `graph.duckdb`
  handle/lock contention from `cross_check_new`'s in-process connection) and fix
  lands — cross-check in subprocess or release handle before Stage 5.
- **S8 listed/ticker enrichment** (P7): management-stated listings ("now been
  listed") and recent IPOs are invisible to the ticker resolver; stubs hard-code
  `listed: false`. Accept: enhancer worklist flags `listed` contradictions found
  in section prose, and unresolved tickers surface as explicit manual-check items
  instead of silent nulls.
- **S9 analyst-marker calibration — OPEN, deferred (added 2026-10-15 at
  execution)**: S6's `_ANALYST_MARKER_RE` is a first-cut list of eight
  hand-picked attribution phrases (analyst(s), research analyst/lead/head,
  head of research, brokerage, sell-side, buy-side, investment bank, broker
  note). Phrasings it does NOT cover yet: "in a note dated…", "street
  expects", "channel checks", "management meet notes", speaker-title lines
  ("VP — Research, <securities>"), and non-English attributions. Failure
  direction is fail-safe (a missed marker leaves the section on the company
  worklist — recall loss, never misrouting), and the false-positive
  direction is test-pinned (company headings never route; "bank" alone is
  not a marker). Accept when revisited: a calibration pass over the next
  real parse runs diffing `sector_commentary` against manually identified
  analyst sections; marker additions pinned by tests; zero management
  sections flagged in the repro set.

## Acceptance criteria

- Every slice lands with a targeted-test pin; the 2026-10-10 findings
  repro cases (McDonalds, Dassault Systèmes, Three M Company,
  NetwebTechnologiesIndia, Company20_Microns, IRDAI regulator header,
  Manipal Payment sector, JM Financial worklist) stay green.
- Eval gate: these changes alter extractor rules, so acceptance MUST include
  `helpers/misc/ontology_eval_gate.py` over the frozen question set between dry-run
  and canonical apply (ontology_governance S2). Edges-by-type counts
  before/after each slice recorded in the `completed.md` entry.
- No hand-written alias added for a name class already covered; the
  `relation_aliases.json` diff shrinks instead of growing.
- Full-corpus `extract_relations findata` dry-run exits 0 (F3 pin) with
  unresolved ≤ pre-proposal baseline (4).
- S3's `_is_noise_entity` remains the first gate; do not weaken it.

Follows: `doc/improvements/archive/tooling/resolver_extractor_hardening.md` (the S1–S3 parent arc).
