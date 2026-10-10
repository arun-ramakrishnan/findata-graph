---
title: "Entity resolution & relation-extractor hardening — first-full-run findings"
status: deferred
filed: "2026-10-10"
executed: "2026-10-10"
completed_md: "378"
area: "helpers/graph, helpers/core"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Entity resolution & relation-extractor hardening — first-full-run findings

**Date:** 2026-10-10 · **Status:** DEFERRED (2026-10-10: the four in-run stop-gaps F1–F4 + P1 executed and test-pinned — completed.md #378; S1–S8 deferred with verdicts in §D) ·
**Area:** `helpers/graph/extract_relations.py`, `helpers/core/parse_newsletter.py`, `helpers/core/local_embedder.py`, `helpers/graph/query.py`, `findata/Misc/relation_aliases.json`

## 1. Motivation

The first full markdown_parse pipeline run since the graph-relationship
fixes (3 Chatter editions, 2026-10-10) surfaced a **class** of
resolver/extractor defects, not isolated bugs. Full evidence inventory
with measured numbers:
`doc/local/pipeline_run_2026-10-10_findings.md` (F=finded-fixed,
P=pending). Headline:

- Full-corpus `derive-relations` **crashed** on the first company note
  whose `normalized_name` fails resolution (2-vs-3 tuple unpack) —
  full-corpus relation extraction had been silently impossible (F3,
  fixed in-run).
- The parse-time semantic cross-check (`--cross-check`) was **silently
  inert**: granite-384 query vectors vs gemma-512 note vectors returned
  None for every name, misreported as "embedder/vectors unavailable"
  (F1, fixed); and its sim floor 0.55 flagged everything — true
  name→note matches measure 0.80–0.84, the unrelated band tops out at
  ~0.77 (F2, recalibrated).
- Single-token first-token aliases (`adani`, `sbi`, `premier`) shadowed
  **exact** entity names — "Adani Power" resolved to Adani Enterprises,
  "Premier Energies" to Premier Explosives, "SBI Life Insurance
  Company" to State Bank of India (P1, ordering fixed in-run; this
  proposal pins it and addresses the sibling gaps).

The in-run fixes are stop-gaps; the normalization and gating layers
still turn every variant spelling into a hand-written alias, and a
fuzzy tie can still attribute edges to the wrong company.

## 2. Slices (smallest-risk first)

- **S1 resolution-normalization classes** (P2): fold apostrophe
  variants (`McDonalds` ↔ `McDonald's`), diacritics (`Systemes` ↔
  `Systèmes`), and camelCase/compound joins (`NetwebTechnologiesIndia`,
  `UnitedSpirits`, `CyientDlm`) in `EntityResolver` — the five
  hand-aliases added on 2026-10-10 become regression fixtures and the
  alias entries retire once resolution handles them. Accept: the
  2026-10-10 unresolvable-note scan stays 0 with those aliases removed.
- **S2 fuzzy tie-break audit** (P3): the `Three M Company` → `Three M
  Paper Boards` misresolution shows score ties/near-ties can win
  silently. Accept: tie or margin<ε resolutions return None (defer to
  the queue) instead of first-insert-wins; ambiguous_log gains the
  score evidence.
- **S3 section-head noise gate** (P4): Stage 3 created a mangled
  regulator entity ("Regulatory and Development Authority of India")
  from a heading-wrap split. Accept: a regulator/stop-entity gate
  (institution allowlist + "Authority/Commission/Board of" patterns)
  skips creation and routes the section to the Quotes catch-all per
  quote_capture_coverage S4; heading-wrap repair for the
  `## <sector>` + `## <Name> | <sector> Decommissioned` split shape.
- **S4 stub-template placeholder** (P5): `render_stub` emits the
  literal block `## The Chatter — <edition title>` into every stub —
  junk plus an idempotency landmine for block-presence checks. Accept:
  stubs carry no chatter block; all existing stubs' placeholder blocks
  stripped in the same change.
- **S5 sector precedence** (P6): `guess_sector_for` gave
  Financial_Services for a payments company (Fintech_Payments
  carve-out; "Payment" singular missed the pattern) and Consumer where
  the newsletter header itself said Retail; the exchange seed had
  Century Plyboards under Consumer (plywood → Building_Materials).
  Accept: carve-out patterns cover singular forms; header-declared
  sector wins when it is one of the 42 canonical sectors; a one-off
  fix pass moves the misfiled notes (3 known from 2026-10-10).
- **S6 worklist routing for analyst-voiced sections** (P8): the JM
  Financial section was a BFSI sector preview by JM's own analyst —
  zero company content — and landed on the company worklist. Accept:
  the Stage-4 worklist marks sections whose speaker title is not
  company management (analyst/research-lead) and content spans ≥3
  named companies as `sector_commentary`, so the enhancer routes to
  the sector note, not the company note (P10's quotes-attribution
  risk shrinks with the same signal).
- **S7 cross-check/integrity transient** (P9): 3/3 parse runs with
  `--cross-check` active and NEW>0 failed the Stage-5 integrity leg;
  standalone runs pass. Repro is documented in the findings doc.
  Accept: root cause identified (suspected graph.duckdb handle/lock
  contention from `cross_check_new`'s in-process connection) and the
  fix lands — likely run cross-check in a subprocess or release the
  handle before Stage 5.
- **S8 listed/ticker enrichment** (P7): management-stated listings
  ("now been listed") and recent IPOs are invisible to the ticker
  resolver; stubs hard-code `listed: false`. Accept: the enhancer
  worklist flags `listed` contradictions found in section prose, and
  unresolved tickers surface as explicit manual-check items instead of
  silent nulls.

## 3. Acceptance criteria

- Every slice lands with a targeted-test pin; the 2026-10-10 findings
  doc's repro cases are the fixture set (McDonalds, Dassault Systèmes,
  Three M Company, NetwebTechnologiesIndia, Company20_Microns,
  IRDAI regulator header, Manipal Payment sector, JM Financial
  worklist).
- Eval gate: the resolver/extraction changes alter query-visible
  semantics (extractor rules), so acceptance MUST include
  `helpers/misc/ontology_eval_gate.py` over the frozen question set
  between dry-run and canonical apply (ontology_governance S2) —
  edges-by-type counts before/after each slice recorded in the
  completed.md entry.
- No hand-written alias added after this proposal's landing for a name
  class S1 covers; `relation_aliases.json` diff shrinks instead of
  growing.
- Full-corpus `extract_relations findata` dry-run exits 0 (F3
  regression pin) with unresolved ≤ the pre-proposal baseline (4).

## 4. Evidence

`doc/local/pipeline_run_2026-10-10_findings.md` — the full F/P
inventory with the measured calibration numbers (unrelated band
0.69–0.77, true matches 0.80–0.84), the crash traceback shape, the
poisoned-alias list, and the run's final state (integrity 100%, 332
edges extracted, 16 applied).

## D. Deferred remainder (2026-10-10)

The in-run work fixed the four one-line-mechanism defects (F1 dim-match,
F2 min_sim calibration, F3 tuple crash, P1 alias precedence — see
completed.md #378). The class-level work stays open, unchanged:

- **§S1 resolution normalization** — the ten hand-aliases added on
  2026-10-10 are regression fixtures; retire them once apostrophe/
  diacritic/camelCase folding lands in `EntityResolver`.
- **§S2 fuzzy tie-break audit** — accept: sub-ε-margin resolutions
  return None (defer to the queue) instead of first-insert-wins.
- **§S3 section-head noise gate** — the IRDAI regulator entity class.
- **§S4 stub-template placeholder** — `render_stub` still emits the
  literal `<edition title>` chatter block.
- **§S5 sector precedence** — carve-out singulars + header-trust; three
  misfiled notes hand-corrected in-run.
- **§S6 analyst-voiced worklist routing** — JM Financial class.
- **§S7 cross-check/integrity transient** — repro: parse `--apply
  --cross-check` on an edition with ≥1 NEW entity; 3/3 failed in-run,
  standalone passes.
- **§S8 listed/ticker enrichment** — management-stated listings +
  recent IPOs surface as explicit manual-check worklist items.

**Revisit trigger:** the next alias-file addition for a name class §S1
covers, or any wrong-company edge traced to a tie-break.
