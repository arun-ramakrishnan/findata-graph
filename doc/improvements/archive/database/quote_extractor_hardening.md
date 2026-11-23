---
title: "Quote-extractor hardening — balance-aware closers, edition-boilerplate guard, attribution repair"
status: executed
filed: '2026-11-23'
executed: '2026-11-23'
completed_md: "562"
area: helpers/graph/derive_insights.py
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Quote-extractor hardening — balance-aware closers, edition-boilerplate guard, attribution repair

**Date:** 2026-11-23 · **Status:** EXECUTED ·
**Area:** helpers/graph/derive_insights.py (quotes extraction + repair
driver; content slice touches 4 findata company notes)

## 1. Motivation

Found 2026-11-23 auditing the IBM_Tata_Tech edition's quotes at the
operator's request ("did we record all of them properly"). The vault
side is fine; the structured side is not — and the defects are not
edition-specific, they are chronic extractor weaknesses.

Why the existing tripwire never tripped: `quote_coverage_audit.py`
(advisory, quote_capture_coverage S0) measures CAPTURE — an opening
counts as covered when its 80-char normalized prefix matches any
quotes-table row. A truncated row's prefix still matches its opening,
so coverage stays ≥ 99% while fidelity (attribution + full text) rots.
This proposal covers the fidelity axis the coverage audit cannot see.

## 2. Evidence (measured 2026-11-23, this box)

| Probe | Result | Source |
|---|---|---|
| IBM_Tata_Tech: edition attributed quotes vs quotes rows | 53 in edition; 54 rows; 52/53 attributed; 2 defective rows | quotes table, source_ref LIKE '%IBM_Tata_Tech%' |
| D1 truncated capture | row 24674 — Krishna's first quote cut at `'There are three "no-regrets, low-risk'`, speaker NULL | quotes.id = 24674 |
| D2 false positive | row 24698 — newsletter boilerplate as a "quote", entity = `IBM_Tata_Tech` (the edition entity), paraphrase swallowed frontmatter + welcome text | quotes.id = 24698 |
| Clean sibling | Hitachi_Glenmark_Titagarh: 52/52 attributed, 0 NULL, 0 junk | quotes table |
| Store-wide NULL-speaker rows | 2,481 of 9,151 (27.1%) | quotes table |
| Store-wide truncation suspects (quote_text not ending in sentence punctuation, len > 60) | ~8 in Tariffs__Tailwinds alone; also Beyond_Results_Into_the_Plot, A_Quarter_That_Refuses_To_Behave, Roots_in_the_results | quotes table |

Root mechanisms, both in `helpers/graph/derive_insights.py`:

- **D1 — premature close on embedded straight quotes.** `extract_quotes`
  (:1365) normalizes every typographic quote mark to a straight `"`
  (`_CURLY_QUOTES`, :1149) before extraction, so every quoted phrase
  inside a verbatim block becomes a potential closer.
  `_close_quote_same_line` (:1152) closes the run when a line carries
  `>= 2` quote marks with an empty tail, and `_close_quote_multiline`
  closes on any line that merely ENDS with `"` — a PDF line-wrap that
  lands an embedded phrase-quoted span at a line end truncates the
  capture to that line, loses the attribution, and spills the quote's
  remainder into the next unit's paraphrase. Row 24674 is exactly this;
  whether other editions truncate depends on line-wrap luck.
- **D2 — edition-boilerplate pseudo-section extraction.** The splitter
  creates a pre-first-`###` section whose entity is the edition itself
  (legitimate: `entities` row `IBM_Tata_Tech`, type `edition`). The
  short-opener branch of `_is_quote_open` (:1260 — starts with `"`,
  len > 20, attribution probe within the next lines) fired on the
  welcome text `“The Chatter” format to better serve your needs.` —
  a mid-sentence brand name — and the run swallowed intro lines +
  frontmatter as paraphrase. Row 24698.
- **D3 — the chronic NULL-speaker population (2,481 rows) is D1
  fallout plus attribution shapes `_find_attribution` misses.** Repair
  requires the D1 fix first, or re-deriving regenerates the same
  truncations.
- **D4 (content, writer-curated) — hand-block synthesis gaps flagged by
  the same audit:** IBM.md block covers 4 of 8 Krishna themes (missing:
  India-deploy-layer, IT-services billing-model shift, quantum-2029
  timeline, Power processors designed in India); KEI block omits the
  ₹25-30k cr data-centre TAM; Quotes.md omits Nageswaran's
  FII-rotation / sovereign-spread / rupee bullets and Sarangi's
  no-new-PLI / BCD-ALMM point. Curation at 3-5 bullets is by design;
  the audit judges these material.

## 3. Design

- **S1 — balance-aware closers (the root fix).** Close a run at line j
  only when the cumulative straight-quote count across the accumulated
  run (opener included) is EVEN and the line ends with `"` — embedded
  paired spans preserve odd parity, so only the true closer flips it.
  `_close_quote_same_line`'s `count >= 2` branch and
  `_close_quote_multiline`'s bare `endswith('"')` both gain the parity
  guard; if a run reaches section end without an even balance, fall
  back to today's behavior (close + attribution probe) so no quote is
  silently dropped. Fixtures: the real IBM line shapes (24674's
  wrap), a Modi quote with embedded `"India First"`, and the
  Tariffs__Tailwinds suspect rows as regression lines.
- **S2 — edition-boilerplate guard.** Two gates, both cheap: (a) skip
  quote extraction for the pre-first-`###` pseudo-section entirely
  (edition notes carry no verbatim speaker blocks before the first
  company heading — verified across the estate's editions); (b) the
  short-opener attr-probe branch additionally requires the opener line
  to END with quote punctuation (the welcome line does not).
- **S3 — attribution repair driver.** Banked rerunnable census in
  `bench_data/quote_extractor_census/` (NULL-speaker share, truncation
  suspects, junk-entity probes) as the before/after ruler. Repair is
  delete + re-derive PER affected edition — the writer dedupes on
  `(entity, quote_text, as_of_edition)` keep-first (~:1930), so a
  FIXED (longer) text would insert as a NEW row beside the truncated
  one; delete-first is the only idempotent path. FK-safe: `quotes`
  carries two OUTBOUND FKs (agent_id, entity → entities.name) and
  zero inbound (duckdb_constraints census 2026-11-23), so deletes are
  legal under the DuckDB 2.x referenced-row law. Drive the
  delete+re-derive from the census's defect list, not a blanket wipe.
- **S4 (optional content slice) — hand-block enrichment** for the four
  D4 gaps, writer-curated per markdown_parse Stage 5 shape.

## 4. Rejected alternatives

- **LLM attribution inference for the 2,481 NULL rows**: derivation has
  no model lane and must stay deterministic + idempotent; rule-based
  re-extraction is both. Rejected.
- **Row surgery only (fix the two IBM rows by hand)**: leaves the
  chronic population and every future edition breaks the same way.
- **Relaxing `_CURLY_QUOTES` normalization** (keep typographic marks
  distinct from closers): touches every caller of the normalized lines
  and re-opens the shapes the normalization was filed to fix.

## 5. Acceptance criteria & shakedown

1. Census driver re-run post-repair: NULL-speaker share materially down
   from 27.1% (floor bounded by genuinely unattributable legacy rows —
   measured, not assumed); truncation suspects = 0 on every re-derived
   edition.
2. IBM_Tata_Tech re-derive: 53 rows; Krishna 8/8 attributed with the
   full no-regrets text; boilerplate row gone; no other row changed.
3. Zero duplicate `(entity, quote_text, as_of_edition)` triples
   estate-wide; `make snapshot` + integrity gate exit 0.
4. Targeted suites green (derive_insights quote suites + the new
   fixture tests); full gates only on the operator's go.

## 6. Execution & findings (2026-11-23)

- **S1/S2 landed narrower and more surgical than drafted.** The
  closers gained the parity guard exactly as designed
  (`_close_quote_same_line` empty-tail branch closes only at even
  parity; `_close_quote_multiline` tracks a running parity and closes
  on `endswith('"') and parity % 2 == 0`; attrish tails still close at
  any parity — that is the inline-attribution signal, S3b). The
  pseudo-section lane was NOT skipped wholesale: the edition-note lane
  is by-design (G5 — masthead commentary rides the edition entity,
  table-only, never rendered). S2 landed as (a) a masthead guard in
  `_is_quote_open` — a `count == 2` opener whose post-close tail is
  non-empty and non-attrish is rejected (kills the brand-name class,
  row 24698) — and (b) `iter_edition_note_section` strips the YAML
  frontmatter (`_FM_RE`, count=1) and yields nothing when the
  remainder is empty (kills paraphrase pollution).
- **Fixture gotchas pinned in tests:** `_parse_attribution` rejects
  single-token names (use two-token shapes in fixtures); continuation
  lines carry no leading quote (per-line italic/bold chrome strips
  it); an opener under ~40 chars never passes `_is_quote_open`.
  `tests/test_quote_capture_s4.py::TestQuoteExtractorHardening` (5
  tests) covers the 24674 wrap, mid-run parity close, even-parity
  single line, masthead brand line, and frontmatter strip. Targeted
  suites: 298 passed (test_derive_insights.py 224 +
  test_quote_capture_s4.py 74).
- **S3 census:** `bench_data/quote_extractor_census/census.py` (banked;
  per-stem stored-vs-fresh diff keyed `(entity, quote_text)` with
  added/removed/speaker_fixed/speaker_lost/title_changed classes plus
  estate NULL-speaker and no-terminal-punct counts). BEFORE: 9,151
  stored / 9,106 fresh / 43 stems drifted / null_speaker 2,481 /
  no_terminal 754.
- **Repair simplified: the planned delete-first dance is unnecessary.**
  `apply_quotes` (:1936) is a PREFIX-SCOPED STABLE REPLACE
  (`stable_write.stable_prefix_replace`): the content diff deletes
  stale rows, keeps unchanged ids/created_at, and preserves
  hand-seeded rows. One full `derive_insights.py findata --apply`
  landed "17 new, 9,089 unchanged, 62 stale" (net −45); metrics
  0/4,170/0; 22 company-note chatter blocks regenerated, 1,284
  hand-written edition blocks skipped (curation safety held). Every
  removal was verified against source BEFORE apply — all junk deaths
  (mid-prose mis-openers like Hexaware `zero tech debt"` and ixigo
  `Half of the new flyers"`, one masthead junk row per edition) or
  truncation→full repairs (Rallis India), no legit quote lost.
- **AFTER (acceptance criteria 1-2):** stored 9,106 = fresh 9,106,
  affected stems 0 — converged. null_speaker 2,444 (−37; residue =
  legitimately anonymous units, tracked by the banked census),
  no_terminal 727. IBM_Tata_Tech: Krishna 8/8 attributed with the full
  1,041-char no-regrets text, boilerplate row 0, frontmatter
  paraphrase 0; per-speaker counts (Krishna 8 / Harris 5 / Gupta 6 /
  B.K.Singh 5 / Modi 6 / Nageswaran 9 / Agrawal 5 / R.K.Singh 5 /
  Sarangi 4) match the hand audit exactly. Duplicate triples: 0
  (writer dedupes in apply mode — fixed in #560).
- **S4 landed in place** (shape-preserving: 3-5 bullets + 1 verbatim):
  IBM.md block folds the four missing Krishna themes (India
  deploy-layer + chips, IT-billing flip + quantum-2029, open-weight
  cap) into the existing bullets; KEI block gains the ₹25-30k cr
  data-centre TAM; Quotes.md folds Nageswaran's flows/rupee macro and
  Sarangi's no-new-PLI/BCD-ALMM into their existing bullets.
- **Out of scope, noted:** mid-word source truncations in
  findata/The_Chatter/IBM_Tata_Tech.md (per-line italic blocks with
  hard right-edge cuts from pdf_conv_md) are a pdf_pipeline defect —
  the extractor is now source-faithful to them.
