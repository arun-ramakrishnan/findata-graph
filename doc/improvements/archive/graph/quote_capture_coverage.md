---
title: "Quote capture coverage — close every markdown-to-notes drop (99% target)"
status: executed
filed: "2026-09-07"
executed: "2026-09-08"
completed_md: "215"
area: "helpers/graph/derive_insights.py, helpers/validators/ (new audit), findata sector notes"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Quote capture coverage — close every markdown-to-notes drop

**Date:** 2026-09-07 → 08 · **Status:** EXECUTED · **Mode:** six slices,
instrument-first, land in order.
**Area:** `helpers/graph/derive_insights.py` (extractor + resolver + render)
· `helpers/validators/quote_coverage_audit.py` (NEW) · `Makefile` ·
`findata/Sectors/*` + `findata/Sectors/Quotes.md` (new writer surface) ·
procedure + schema docs.

**Review 2026-09-07 (Muse Spark):** code + DB check against the live tree.
Corrects the S1 marker regex (was a char-class list, missed `\`-escapes),
the F2 missing-entity list (`Titan`, `Steel Authority of India`, `Divis
Laboratories` already exist — resolver gaps, not missing rows), the sweep
scope (global `derive:quotes:%` prefix, not per-stem), the alias-reuse risk
(`premier → Premier Explosives` vs `Premier Energies`), stale helper paths,
and the S4 catch-all blast radius. No scope change — corrections ride in
the live proposal per the house pattern.

**Review 2026-09-07 (II) — live-code probe + trial planning:** re-read the
current tree against this proposal before the single-note trial. (i) Code
drift: `iter_company_sections` already treats `## — Name, Title`
attribution headings, `## Management`-class role headings, and newsletter
chrome as non-structural (`derive_insights.py:467-477`) — F1's fix is
exactly the bracket rule; nothing else moved. (ii) The trial candidate
`Nvidia_Samsung_Cloudflare.md` measures **0 rows in `quotes` today**
against 221 orphaned openings — a total-loss edition: all 11 company
sections are immediately followed by `## [Transcript]`, so the sector
classification kills every transcript body (F1 at maximum). (iii)
`_CONCALL_HEADING_RE` (`:630`) confirmed keyed on `[Concall]` only.
(iv) New gap **F8** (no-dash speaker headings) found during trial
planning; S1 gains a trial-gated amendment for it. Trial T0–T4 planned
(see "Single-note trial" in §4) before any implementation slice.

**Review 2026-09-07 (III) — multi-note trial:** extended the trial to
three more editions picked for different stress profiles (marker
diversity: `Doubling_Down`; speaker-heading density:
`Reliance_PayTM_Nykaa`; no-bracket format: `On_Record`). Three new gap
classes found and validated (F9 bare markers, F10 prose sub-headings,
F8b missing role words); the extended rule set reaches 80–95% in-body
coverage on all four notes with zero regression on the first (results
table in the trial section). Details and the measured resolver tiers
ride there; S1/S2 amendments updated in place.

## 0. Context and directive

Source editions increasingly carry management/analyst quotes as their core
content. The PDF→markdown leg is verified (`helpers/pdf/verify_extraction.py`,
per-page word-multiset coverage) and measured at 95%+. The markdown→notes leg
had **no fidelity measurement at all** until the 2026-09-07 funnel audit
(Appendix A). That audit found ~66% of quote-shaped content never reaching
the `quotes` table — a mix of one structural bug, resolver gaps, extractor
shape misses, and the by-design manual-only sector pool.

User directive (2026-09-07): close the gaps **fully** — nothing dropped on
the floor in the quotes table, sector-context commentary lands on
sector/super-sector notes, unmappable commentary lands in a catch-all
`Quotes.md` note rather than being dropped, and the markdown→notes phase
reaches **99% coverage**. This proposal is the plan for that.
User priority note (2026-09-07): **~98% of content is
`findata/The_Chatter`** — accuracy on that tree outweighs the other two
trees; validation weight goes there.

## 1. The measured funnel (2026-09-07 audit, Appendix A)

| Leg | Count | Verdict |
|---|---|---|
| Quote-shaped openings, all three trees | ~8,200 | denominator |
| Extracted by `derive_insights` today | 2,761 | 34% of openings |
| Live `quotes` table rows | 2,777 | converged with extractor (16 legacy pre-#136 title-keyed rows) |
| Table quotes present verbatim in note bodies | 359/400 sampled (90%) | render layer mostly healthy; misses are hand-block editions (curation-safety, by design) |
| **G1** orphaned behind bracket-marker headings | 2,521 | **bug** — company transcripts classified as sectors |
| **G2** dropped on entity-resolution failure | 878 (350 sections) | **gap** — silent `continue`, no worklist |
| **G3** missed by extractor shapes | 118 (~4% of in-scope) | **gap** — bullets/blockquotes/leading punctuation |
| **G4** sector/expert pool, zero capture | 1,927 | **by design until now** — directive changes this |
| **G5** masthead/editorial | 3 | trivial, but in-scope per directive |
| **G6** table→note-body misses | 41/400 | policy — hand-written blocks win (keep) |

Points_And_Figures carries 293 resolved sections but zero quote-shaped
lines (numbers newsletter — nothing to lose). The_PlotLines (2 files) is
fully sector-shaped → enters G4.

## 2. Findings

**F1 — the skip-list gap is the dominant loss (G1).**
`iter_company_sections` (`helpers/graph/derive_insights.py:431`) skip-lists
exactly one internal marker: `[Concall]` (`:465`). The corpus carries 21
distinct bracket markers (`[Transcript]`, `[Presentation]`, `[Reference]`,
`[Interview]`, `[Recording]`, `[Call recording]`, `[Exchange Filing]`,
`[Coverage]`, `[Earnings Call]`, plus converter typos `[Presenstation]` /
`[Presentaton]` / `[Presntation]` and the escaped
`[Transcript \& Interview]`). Each unmatched marker is classified as a
bare *sector* heading → **terminates the company section at the transcript**
and dumps its body into a never-captured region. Measured: 1,696 openings after
`[Transcript]`/`[Presentation]` + 825 after other bracketed markers, all in
company context (Hero MotoCorp, PI Industries, Jubilant Foodworks, Max
Healthcare, IRCTC in the two newest editions; `Nvidia_Samsung_Cloudflare`
alone loses 221). The newest format leans hardest on these markers, so the
loss compounds with every ingest.

**F2 — entity resolution fails silently and unevenly (G2).**
`_extract_sections` (`:1783`) drops unresolved sections with a bare
`continue` — no warning, no worklist. Causes verified in the live DB:
markdown escapes (`L\&T Finance` never matches the `L&T Finance` row),
shape mismatches the exact/normalized resolver cannot bridge (Dixon
Technologies (India), Apollo Hospitals *Enterprise*, Lenskart vs "Lenskart
Solutions", Tata Motors where only the PV sub-entity exists), token-addition
shapes the parenthetical tier does not cover (`Lenskart Solutions` vs
`Lenskart`), and genuinely missing entities — **Premier Energies, HUDCO,
Hindalco Industries have no row at all** (`Titan` → `Titan`,
`SAIL` → `Steel Authority of India`, `Divi's` → `Divis Laboratories`
already exist — those three are resolver gaps, and auto-stubbing them would
create duplicates). Also present: junk
canonicals ("Initiatives", ×3 sections) from headings that should never
classify as companies.

**F3 — extractor shape misses (G3).** 118 openings inside *resolved*
sections that the quote walker never opened: quotes behind list/bullet
markers, blockquote `>` prefixes, or under the 40-char opening threshold.

**F4 — the sector/expert pool was deliberately manual (G4).** 1,737
openings under named-sector regions + 190 under sector-context bracketed
markers. `markdown_parse.md` routes these to hand-written sector blocks;
the auto layer never touches them. The 2026-09-07 directive reverses this
policy for capture purposes.

**F5 — the table is schema-ready for non-company rows.** `quotes.entity`
FKs to `entities(name)` with no `entity_type` CHECK; the 42 sectors, 9
super-sectors, 113 editions, and 12 themes are all legal targets today.
Zero non-company rows exist yet.

**F6 — sector notes are a two-writer surface today.** Roster
(`helpers/maintenance/sync_sector_wikilinks.py`) + hierarchy
(`helpers/maintenance/build_sector_hierarchy.py`) own
region-scoped sections of every sector note, both `--check`-gated in
maint-full. A third writer (auto chatter blocks) must adopt the same
sentinel-region discipline; the vault's four historical deletion/misplacement
bugs were all nested-sentinel failures.

**F7 — the render layer is the healthy leg.** 90% of table quotes are
verbatim in note bodies via auto blocks; the misses are editions where a
hand-written block exists (curation-safety skip — `render_chatter_block`
docstring contract). The table, not the note body, is where information
dies.

**F8 — no-dash speaker headings are structural boundaries (found in
trial planning, 2026-09-07).** The `## Philipp Schindler, SVP and CBO,
Google` shape (name-comma-title, no em-dash) matches neither
`_ATTR_DASH_RE` nor the role/chrome rules → classified as sector →
structural boundary. Today it is masked: `## [Transcript]` already
killed the section upstream. After S1 un-orphans the transcripts these
become the top choppers — the trial note carries at least four
(`Philipp Schindler` :538, `Manabu Chikumoto` :1212, `Matthew Prince`
:1348, `Sameer Shahapurkar` :1500). Candidate fixes (validated on trial
data, T1): role-token list (SVP/CFO/CEO/President/Chief/Head of/
Founder/Analyst) or the name-comma-title shape; false-positive risk
against real sector/company headings is scored on the trial note before
adoption.

## 3. The coverage contract (what "99%" means)

- **Denominator:** every quote-shaped opening in the three newsletter
  trees. An *opening* = a line that, after the extractor's own
  normalization (per-line emphasis unwrap + curly-quote folding), starts
  with `"` and exceeds 40 chars. Same definition the walker uses — no
  second yardstick. S3 removes the denominator/walker mismatch (walker
  drops final `quote_text < 30`; denominator admits `> 40`): short quotes
  are covered only via the sub-threshold-with-attribution rule, else they
  are itemized as a known residual, not silent misses.
- **Covered:** the opening matches a `quotes`-table row (byte-pinned
  normalization: emphasis unwrap + curly→ASCII fold + whitespace collapse
  + case-fold, then 80-char prefix containment against `quote_text`, any
  entity type — company, sector, super-sector, edition, or catch-all).
  The audit and the walker share one normalize helper — no second
  yardstick.
- **Target (dual metric):** total covered ≥ **99%** of openings AND
  resolved (non-catch-all) covered reported separately. Residual ≤1% is
  reported with examples (OCR garble, truncated splices) — the audit
  prints every unmatched opening so the residual is inspectable, never
  silent. Hitting 99% by dumping to catch-all alone does not pass; the
  catch-all bucket must be shrinking-or-zero at S5.
- **Note bodies are the render layer, not the lossless layer:** every
  edition×entity with quotes gets either a sentinel auto block or a
  hand-written block (reported per edition; hand-block wins are
  *covered-by-curation*). The 99% lives in the table; notes are backed by
  it. This keeps G6's curation-safety intact. Table-only rows (G5
  masthead → edition entity) are excluded from the note-body sample.
- **Catch-all is coverage, with visibility:** quotes landing on
  `Quotes.md` count as covered, reported in their own bucket so triage
  backlog is always measurable (§4 S4).

## 4. Slices

### T0–T4 — single-note trial before any slice (2026-09-07)

Prove S1+S2 on the worst edition first. Trial note:
`findata/The_Chatter/Nvidia_Samsung_Cloudflare.md` — 1,506 lines,
11 `## [Transcript]` markers, 0/221 captured today. All harness work is
read-only: a scratch script importing the real `derive_insights`
machinery — no DB writes, no source changes, no note edits. The only
repo file this trial touches is this proposal (results appended below).

- **T0 baseline** — run the current classifier + extractor on the note;
  reproduce the funnel (expect ~0 extracted, 221 orphaned) and pin the
  numbers the trial must beat.
- **T1 — S1 prototype** — boundary variant: any fully bracket-wrapped
  `#{1,3}` heading is non-structural. Measure sections recovered +
  extracted delta; itemize residual choppers (expect F8 speaker
  headings); test both F8 candidate rules against the same note and
  score false positives by hand.
- **T2 — S2-lite resolver check** — run the tier ladder (exact →
  unescape → suffix/parenthetical) for all 11 company headings against
  `entities`; report resolve/fail per heading. Shapes of interest:
  `Zoom Communications`, `Figma Inc. Design & Product Development
  Platform`, the masthead Nvidia.
- **T3 — render preview** — dry-run the sentinel auto-chatter block for
  1-2 companies (structure + provenance only) for eyeball review before
  any corpus consideration.
- **T4 — report** — before/after funnel for the stem + itemized
  residual, appended to this section as trial results; green here gates
  the S0–S2 implementation go.

**Trial results (T0–T4 executed 2026-09-07, read-only, `memory/research.db`
untouched):**

- **Funnel: 0 → 163 extracted quotes; openings inside company bodies
  0 → 176/221 (80%).** S1 bracket rule alone: 130 quotes / 140 openings;
  the F8 fix adds +33 quotes / +36 openings (Alphabet 1→26 openings,
  Cloudflare 19→27, Mitsubishi 18→21).
- **F8 rule validated — name-comma-title with a role-word tail**
  (`, Chief …`, `, SVP …`, `, Chairperson …`; tail scanned for
  ceo/cfo/…/officer as words). Both cruder candidates FAILED corpus
  review: bare substring role-tokens FP on the sector heading
  `Electronics & Semiconductors`; the bare name-comma-title shape swallows
  pipe-artifact company headings (`Virgin Galactic Holdings, Inc. I
  International`). Refined rule: 335 corpus matches, sampled set is
  all-speaker including `P. Ramakrishnan, CFO` / `Dr. Sharvil Patel, MD`
  initial/honorific forms.
- **S2 ladder measured on the 9 resolved sections:** exact 4
  (Samsung Electronics, Unilever PLC, Walmart, Zoom Communications);
  unescape+canonical 2 (Alphabet, Baidu — `Inc.` strips); **trailing-
  separator strip 1 (Cloudflare — `_canonicalize` leaves `Cloudflare,`;
  new pinned tier)**; entity-side suffix 1 (Mitsubishi Chemical →
  `Mitsubishi Chemical Group` — the pinned suffix list must strip from
  the ENTITY side too); vetted-alias 1 (heading `Amazon.com, Inc.` →
  entity `Amazon`); worklist/heading-shape 1 (Figma — the pipe-less
  `Figma Inc. Design & Product Development Platform` never classifies as
  a company heading; candidate tier: entity-prefix + legal-suffix cut,
  else worklist; 22 openings).
- **Residual after S1+F8: 45/221 (20%)** = 23 in the `Software` sector
  region (the masthead Nvidia commentary — the exact S4 synonym-map case,
  `Software`→`Technology`) + 22 Figma (above). No silent drops: every
  residual opening is bucketed.
- **T3 render preview:** sentinel-balanced blocks (BEGIN=1/END=1 per
  block); 163 quotes, **157 with speaker attribution (96%)**; top
  speakers genuine (Matthew Prince ×23, Jaejune Kim ×15, Fernando
  Fernandez ×15, Sundar Pichai ×14, Andy Jassy ×14). No-speaker residue
  is the known OCR-splice fragment class.
- **Harness lesson (feeds S0):** two harness iterations mis-walked
  boundaries (sector boundaries dropped → Amazon swallowed the Figma
  region and inflated 163→184) before the parity-correct walk. The S0
  per-file classification-parity assertion is load-bearing — keep it, and
  add a boundaries-include-sectors tripwire test.

**Verdict: green.** S0–S2 proceed with (a) the refined F8 rule, (b) the
trailing-separator strip tier, (c) entity-side suffix stripping, (d) the
Amazon alias + Figma heading-shape items entering the S2 worklist seed.

**Multi-note trial extension (same day, read-only):** three more notes,
different stress profiles. New gap classes, all fixed in the extended
harness and re-measured:

- **F9 — bare marker words.** `On_Record` carries `## Concall`,
  `## .Concall` (OCR dot), `## Recording`, `## Interview` with NO
  brackets — the S1 bracket rule is a no-op there (99/104 openings
  orphaned; the edition was a total loss). Fix: pinned bare-word family
  list (concall/transcript/presentation incl. converter typos/
  interview/recording/call recording/earnings call/exchange filing/
  reference/coverage/remarks/q&a), case-insensitive exact match after
  stripping leading punctuation and a mangled trailing `]`. 19 markers
  skipped on that note → 0% → 95% in-body.
- **F10 — prose sub-headings.** Transcript-internal subtitles
  (`On European Revival Despite Weak…`, `Despite Industry Pressures,
  Sagility Continues to Execute and Grow`) classify as sector → chop
  (17 openings on `Doubling_Down`). Fix: preposition/article-led heading
  (on/in/despite/while/with/for/the/a/as/amid/from/…) or lowercase-led
  → non-structural. `Design & Product Development Platform` correctly
  stays structural under this rule (noun-led).
- **F8b — role-word list extended:** `manager`/`managing`/`md` were
  missing (`Mary Abraham, General Manager of …` chopped; +5 openings on
  `Reliance_PayTM_Nykaa`).
- **F11 — parenthetical company headings.** `Dassault Systèmes
  (International)` / `ABB Limited (International)` (no pipe) classify
  sector. Candidate: parenthetical strip + resolve-before-classify; ABB
  is also a mapping choice (parent vs `ABB India`) → worklist seed.

**Four-note results (openings in company bodies, extended rule set):**

| note | before | after | quotes | residual |
|---|---|---|---|---|
| Nvidia_Samsung_Cloudflare | 0% | 80% | 163 | 23 sector-pool (S4) + 22 Figma (S2 tier) |
| Doubling_Down | 0% | 95% | 114 | 6 = F11 parentheticals |
| Reliance_PayTM_Nykaa | 49% | 84% | 94 | 19 sector-pool (S4) |
| On_Record | 0% | 95% | 93 | 4 sector-pool + 1 prose |

**Chatter-priority addendum (three more notes, 2026-09-07, with
baseline-vs-extended regression tracking):** per the 98%-Chatter
priority, profiles picked to close the remaining risk surface:

| note | profile | baseline → extended | delta |
|---|---|---|---|
| Max_Life_Tempsens_TCS | local-engine curly+emphasis (×120/×370), working note (30 rows) | 98% → 98%, quotes 45 → 45 | **+0, clean** |
| TCS_IFB_Gulf_Oil | healthiest note in the tree (122 rows) | 91% → **100%** in-body, 141 → 155 quotes | **+14** |
| Threads_and_Tensions | curly near-total-loss (3 rows) | 53% → 81%, 18 → 28 quotes | +10 |

No regression on any of the seven trialed notes; the extended rules
improve the healthiest note in the tree. The local-engine
emphasis-unwrap + curly-fold path is validated end-to-end
(Max_Life). One measurement caveat for S0: db-row comparisons must go
through the edition-title mapping (`as_of_edition` carries titles, not
stems — Threads_and_Tensions shows 3 stem-matched rows but the stem's
editions resolve differently).

Zero regression on the first note (176/221 unchanged). Remaining
residuals decompose exactly into the planned slices: S2 heading tiers
(+28), S4 sector pool (+46), S3 splice-close (23 in-body openings
produced no row across the four notes — root cause already recorded).

**Resolver tiers measured across the four notes** (live entities table):
exact; unescape (`L\&T`-class); trailing-separator strip
(`Cloudflare,`); parenthetical strip (`One 97 Communications (PayTM)` →
`One 97 Communications PayTM`); entity-side suffix strip (`Mitsubishi
Chemical` → `…Group`, `Power Finance` → `…Corporation`, `FSN
E-Commerce` → `FSN E-Commerce`, `The New India Assurance Company` →
`The New India Assurance`); symbol fold (`\&` ↔ `and`);
pipe-artifact `I`-strip (`Saatvik Green Energy I` → `Saatvik Green
Energy`); alias candidates (TCS → Tata Consultancy Services, IEX →
Indian Energy Exchange, `Nippon Life India Asset Management` →
`Nippon Life AMC`); genuinely missing (Scoda Tubes, Godrej Industries,
Nike, Netflix) and one mapping choice (ABB parent vs ABB India) —
worklist seeds, not auto-stubs.

**Verdict (multi-note): green — the S1 rule family is now
bracket + bare-marker + F8b speaker + F10 prose; S2's tier list is
fully measured. S0 next.**

**Phase 3 — 95%+ validation (2026-09-07, same day):** the three
sub-90% notes were driven past 95% total coverage by harness-proving
the remaining slice mechanics (no source changes, read-only):

- **S2 heading tier (entity-anchored promotion):** a pipe-less or
  parenthetical heading is promoted to company ONLY when a
  deterministic cut resolves against the live entities table —
  parenthetical strip (`Dassault Systèmes (International)`), or
  legal-suffix boundary (`Figma Inc. Design & Product Development
  Platform` → `Figma`). Unresolvable shapes stay sector/worklist.
- **S3 splice-close (attribution-anchored):** close an open quote at
  an attribution-shaped line (`- Name, Title` / `Name said`), or at
  the last `"` of the opening line when the remainder parses as an
  attribution (`…for NAND." Jaejune Kim, EVP`). A naive earlier
  prototype LOST rows (163→120); the guarded version gains +3–6%
  coverage per note with zero regressions across all seven.
- **S4 sector arm:** sector regions yield sections through
  canonical-sector exact → synonym (`Software`→`Technology`) →
  catch-all `Quotes` — with HTML-entity decode (`&amp;`) in heading
  text before resolution.

| note | before | total now | rows (company+sector) |
|---|---|---|---|
| Nvidia_Samsung_Cloudflare | 80% | **96%** | 192+20 |
| Reliance_PayTM_Nykaa | 84% | **97%** | 96+18 |
| Threads_and_Tensions | 81% | **100%** | 29+7 |
| Doubling_Down | 95% | 100% | 117+3 |
| On_Record | 95% | 99% | 99+4 |
| Max_Life_Tempsens_TCS | 98% | 94% | 48+0 |
| TCS_IFB_Gulf_Oil | 91% | 98% | 157+0 |

Coverage = openings matched to extracted rows (80-char normalized
prefix containment). Company-resolved coverage runs 82–98% on the
same runs; the total-vs-resolved split is the contract's dual metric
— the gap is sector/catch-all rows awaiting worklist triage
(Nvidia's `Software` region) and OCR-garble residuals (3 openings on
Max_Life). No regression on any note at any lever.

**Verdict (phase 3): green — all three lagging notes ≥95% total; the
S2/S3/S4 mechanics are harness-proven and ready to productize. S0
next, now with pinned before/after baselines for seven notes.**

### S0 — Instrument first: `quote_coverage_audit.py` (NEW)

Productize the /tmp audit (methodology Appendix A) into
`helpers/validators/quote_coverage_audit.py`:

- Reuses `derive_insights` machinery by import (same normalization, same
  classification) — no second parser to drift. Classification parity with
  `iter_company_sections` is asserted per file (the audit's own probe
  flagged exactly one divergence today: `The_Push_and_Pull.md` 23 vs 22 —
  S0 root-causes it first; gate is 0 or explained with the cause recorded
  here).
- Output: funnel per tree, per-gap breakdown (G1–G5 buckets with a pinned
  assignment order: G1 orphaned-behind-marker → G2 unresolved →
  G3 shape miss → G4 sector pool → G5 masthead, first match wins so
  sector-context bracketed regions do not double-count), per-edition
  orphan table, unmatched-sector list (feeds the S4 synonym map),
  shape survey for all G3 misses (feeds S3 — no separate one-off report),
  unmatched-opening examples, dual coverage % (total + resolved),
  `--json` mode.
- Live progress meter + `[quotes]` surface tag (house rule). Corpus is
  ~113 files / ~8,200 openings — seconds-scale; no heavy perf budget, just
  the meter.
- Wiring: `make quote-coverage` — **advisory**, never qa-blocking
  (same doctrine as the search-index checks). Read-only: no DB writes.

**Exit:** audit committed, runs green, baseline numbers match Appendix A.

**S0 EXECUTED (2026-09-07).** `helpers/validators/quote_coverage_audit.py`
(616 lines) + `tests/test_quote_coverage_audit.py` (16 tests, green) +
`make quote-coverage` (advisory, read-only). Live baseline (0.8 s run,
parity divergences: **0 across all 113 files**):

| tree | files | openings | covered | G1 | G2 | G3 | G4sec | G4spk | G4prose | G5 |
|---|---|---|---|---|---|---|---|---|---|---|
| The_Chatter | 86 | 8,178 | 2,761 (33.8%) | 3,090 | 878 | 118 | 416 | 881 | 31 | 3 |
| Points_And_Figures | 25 | 4 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 |
| The_PlotLines | 2 | 26 | 0 | 0 | 0 | 0 | 26 | 0 | 0 | 0 |

Reconciliation with Appendix A: G2 (878), G3 (118), G5 (3) match
exactly; total openings 8,178 ≈ ~8,200; G1/G4 differ by design — the
audit attributes by region-start with the F8/F10 sub-splits
(G4spk=881 speaker-chops, G4prose=31) that Appendix A folded into G1.
**Entry-task resolution:** `The_Push_and_Pull` 23-vs-22 was the old
probe's classifier-copy drift (same class as a caught-and-fixed
`&amp;`-unescape parity bug on `The_Hint_in_the_Haze_output`); the S0
copy is byte-faithful and parity-gated per file — the class is now
structurally impossible. DB rows keyed by `source_ref` stem
(`derive:quotes:<stem>:<line>`), immune to edition-title drift.
Tripwire live: 91 notes flagged (pre-slice debt, expected), watchlist
seeded at `findata/Misc/quote_coverage_watchlist.json` (tracked; moved to `findata/Misc/` post-apply), salvage
potential measured at 2,871 rows corpus-wide, rule candidates already
surfacing (`unclosed/splice` 30 openings / 22 notes — the S3 root
cause, independently rediscovered by the recurrence counter).

**S0 amendment — capture tripwire + evolution loop (user directive,
2026-09-07):** when a note falls below **95% total capture**, flag it
automatically and try harder — the logic must evolve as new edition
formats arrive (98% of content is The_Chatter; every new format is a
fresh gap surface). Four parts, all inside the audit, all advisory:

1. **Per-note tripwire (95% floor).** The audit reports per-note total
   coverage; any note < 95% is FLAGGED with its full residual
   decomposition — G-bucket counts, shape survey, unmatched-opening
   examples, resolver misses. The 95% floor is the per-note action
   line; the 99% target (§3) stays the corpus goal — a note below 95%
   is actionable even when the corpus is above 99%. Tree floor: if
   The_Chatter aggregate drops below 95%, the advisory line turns loud
   (echo, never qa-blocking — advisory doctrine).
2. **Salvage pass (deterministic, marked rows).** Flagged notes only:
   a second extraction pass with relaxed-but-deterministic rules —
   sub-40-char openings WITH attribution in the 3-line window, splice
   single-close tolerance, bullet/blockquote strips (S3 shapes).
   Salvage rows carry `properties.salvage = true` and are itemized in
   the report — visible, reviewable, never silent. No fuzzy auto-apply
   (D4 holds inside salvage too).
3. **Watchlist lifecycle (tracked file).** `findata/Misc/quote_coverage_watchlist.json`
   — tracked, same no-gitignored-triage-state discipline as the S2
   worklist. Entries `{stem, coverage, bucket_counts, first_seen,
   last_seen, status}`; auto-open on tripwire fire, auto-close on
   recovery with the closing change recorded. Sorted, one entry per
   note, diff-friendly.
4. **Evolution loop — rule candidates.** Each run aggregates residual
   shapes across flagged notes; a shape recurring ≥ 3 runs or ≥ 10
   openings corpus-wide is emitted as a RULE CANDIDATE (with examples)
   in the report. Rule candidates are the only sanctioned path for new
   boundary/shape rules — tests-first, dry-run diff, and the seven
   trial notes' baselines pinned as regression tripwires so evolution
   cannot trade old coverage for new.

### S1 — Structural fix: bracket-marker headings are never boundaries (G1)

- Replace the single `[concall]` skip in `iter_company_sections` with a
  general rule: a heading whose entire text is bracket-wrapped
  (strip → `^\[.*\]$`) is a non-structural sub-heading. The proposed
  char class (`[A-Za-z&/ ]+`) is rejected — it misses the live
  `## [Transcript \& Interview]` escape and any future marker with
  digits/hyphens/periods. `[Concall]`, `[Transcript]`, `[Presentation]`,
  `[Reference]`, `[Interview]`, `[Recording]`, `[Call recording]`,
  `[Exchange Filing]`, `[Coverage]`, `[Earnings Call]` — and whatever the
  converter emits next — inherit correctness. Bare sector words
  (`## FMCG`) do not match the form and stay structural. Rule applies at
  `#{1,3}` (H1 `# [Transcript]` markers exist in 38+ files).
- Parity in `extract_quotes`: the restrict-to-marker logic (`:630`,
  `_CONCALL_HEADING_RE`) currently keys on `[Concall]` only; cut at the
  first marker of the family so paraphrase accumulation starts at the
  transcript, not the business descriptor (F1's truncation also pollutes
  paraphrases today). Multiple markers per file → first wins.
- Classifier-parity + marker-family tests pinned against the two newest
  editions (regression tripwires, archify-lesson pattern).
- **F8 amendment (trial-validated):** no-dash speaker headings
  (`## Philipp Schindler, SVP and CBO, Google`) are structural today and
  become the top transcript choppers once the bracket rule lands. S1
  ships the bracket rule plus the refined F8 rule: name-comma-title
  shape WITH a role-word tail incl. manager/managing/md (validated T1;
  both cruder candidates rejected on corpus FP review — see trial
  results).
- **F9 amendment (multi-note-validated):** bare marker words — `##
  Concall`, `## .Concall`, `## Recording`, `## Interview` with no
  brackets — join the non-structural family via a pinned bare-word list
  (case-insensitive, leading-punct tolerant, mangled trailing `]`
  tolerated). `On_Record` alone: 0% → 95%.
- **F10 amendment (multi-note-validated):** preposition/article-led or
  lowercase-led headings are prose sub-headings, not sector boundaries
  (`On European Revival Despite Weak…` — 17 openings on `Doubling_Down`).
  Noun-led headings (`Design & Product Development Platform`) stay
  structural — the Figma case stays an S2 heading tier, not this rule.

**Expected:** G1's 2,521 openings move into the extracted pool;
`openings_uncaptured` should collapse toward G3's residual.

**S1 EXECUTED (2026-09-07).** Family predicates live in
`derive_insights.py` (`_is_marker_heading` / `_is_speaker_heading` /
`_is_prose_heading` — single source of truth; the S0 audit imports them,
its drift-prone copies deleted). `extract_quotes` cuts at the first
family marker (first wins; legacy `[Concall]` behavior unchanged). Tests:
12 new S1 cases (predicate shapes incl. all rejected traps, section
extension, paraphrase cut, edition tripwires on Nvidia + On_Record) —
180 total green, ruff clean, zero regressions on the 144-suite.

**Corpus before/after (audit, same day):**

| metric | pre-S1 | post-S1 |
|---|---|---|
| corpus coverage | 33.6% | **64.1%** |
| The_Chatter covered | 2,761 / 8,178 | **5,259 / 8,178 (64.3%)** |
| G1 | 3,090 | **0** |
| G4_speaker | 881 | **0** |
| G4_prose | 31 | **0** |
| G2 (now visible) | 878 | 1,974 |
| G3 (now visible) | 118 | 251 |

G2/G3 growth is exposure, not loss: the absorbed regions' openings now
sit inside resolved/unresolved sections where S2/S3 can reach them
(pre-S1 they were invisible in marker-orphaned regions). Per-note
(persisted db vs S1 walker): Nvidia 0→101, Doubling_Down 0→74,
On_Record 0→46, Threads 3→12, Reliance 37→65, TCS_IFB 122→136,
Max_Life 30→30 (its loss is splice-class, S3). The gap to the trial
numbers (163/114/94/…) is exactly the S2 resolver ladder — next slice.

### S2 — Resolver hardening: no silent drops (G2)

Resolution becomes a tiered ladder, each tier measured, with a worklist
terminus — never a bare `continue`:

1. exact (case-insensitive) — today's behavior;
2. **markdown-unescape** — full CommonMark ASCII-punctuation escape set
   (`\& \| \. \_ \# \$ \* \- \( \)` etc.) before
   canonicalization (covers live `L\&T Finance`);
3. **parenthetical/stripped** — trailing `(India)`-style legal qualifiers
   and a pinned `Enterprise`-class suffix list, tried as a *candidate*
   (only applied when the stripped form resolves and the raw form does
   not). Token-addition shapes (`Lenskart Solutions` vs `Lenskart`) are
   out of scope for auto-apply — worklist only;
4. **alias tier — vetted subset only.** `findata/Misc/relation_aliases.json` +
   `parse_newsletter._ALIASES` were curated for relations, not quotes:
   `premier → Premier Explosives` collides with `Premier Energies`,
   `micron → Micron Technology` is the 20-Microns misfire class. S2 vets
   each alias against the entities table before reuse; unvetted entries
   stay worklist-only;
5. **fuzzy tier — worklist only.** Token-set/edit-distance suggestions go
   to a tracked `quote_entity_worklist.json` (same directory pattern as
   `<slug>_enhancement_worklist.json` — gitignored loses triage state on
   fresh clones) plus a `--apply-worklist` CLI for user decisions. Fuzzy
   NEVER auto-applies (Borosil / Micron→20 Microns misfire class, per
   relation-triage doctrine).

Genuinely missing entities (Premier Energies, HUDCO, Hindalco, …) flow
through the **existing stub path** — entity creation stays
user-held (DuckDB drift gate → `make graph-rebuild` is the follow-on, not
part of this slice).

**S2 EXECUTED (2026-09-07).** `_resolve_ladder()` in `derive_insights.py`
(tiers: exact → unescape/ascii/apostrophe fold → structural strips
[trailing-sep, pipe-artifact `I`, parenthetical] → qualifier strips
[query-side ×2 + entity-side, entity-side UNIQUE-hit-only — Welspun-class
ambiguity returns the hit list as suggestions, never a guess] → `&`↔`and`
fold → vetted aliases) with `_JUNK_CANONICALS` ("Initiatives" class,
mirrored in the audit). Aliases: pinned `_QUOTE_ALIASES` (SAIL/TCS/Paytm/
Nykaa/Amazon.com/IEX/HUDCO — every target vetted against entities by
test) + user-approved `findata/Misc/quote_aliases.json` (gitignore-negated,
relation_aliases precedent (now also at `findata/Misc/`); ladder reads it — the proposed
`--apply-worklist` CLI is superseded by this file-driven flow, deviation
noted per §7). Worklist: `findata/Misc/quote_entity_worklist.json` (tracked,
emitted by the audit; entries auto-close when the canonical resolves).
Fuzzy never auto-applies (D4).

**Corpus delta (audit):** coverage 64.1% → **73.8%**; G2 1,974 → 1,141;
walker 6,062 rows; watchlist 85 open / **6 auto-closed** (crossed 95%).
**Evolution-loop proof:** the tripwire caught a real regression mid-S2 —
an indentation slip made `_build_resolver_map` return after the FIRST
entity row (map size 2 → walker=0 corpus-wide); the audit flagged it
instantly, now pinned by `TestBuildResolverMap`. Parity divergences: 0
(the Mapping_the_momentum divergence the tripwire also caught was the
junk-rule mirror missing in the audit copy — fixed same-session).

Remaining G2=1,141 openings sit on 198 open worklist canonicals (fuzzy
suggestions where they exist — `Aditya Birla Fashion → Aditya Birla
Fashion and Retail`; empty = genuinely missing → user stub flow).

**Worklist resolution procedure (S2 follow-through — revisit anytime):**
the 198 open canonicals in `findata/Misc/quote_entity_worklist.json`
resolve through three paths, ALL ending in auto-close on the next
`derive-insights --apply` (entries flip to `"resolved"`):

1. **ALIAS** — the suggestion exists and is correct: add
   `"<canonical>": "<Entity>"` to
   `findata/Misc/quote_aliases.json` (create if absent; the ladder
   reads it on every run). ~50 entries are alias-ready; the fuzzy
   suggestions are CANDIDATES, not gospel — verified misfires to
   reject: `HDFC Asset Management Company → UTI…`, `HDFC Life Insurance
   Company → SBI Life…`, `General Insurance Corporation of India →
   LIC`, `Union Bank → City Union Bank`, `Orient Green Energy →
   Solarium` (D4: fuzzy never auto-applies).
2. **STUB** — the entity is genuinely missing (`Premier Energies`,
   `Ambuja Cements`, `Kaynes Technology`, `HAL`, `ONGC`, international
   names): create it via the user-held flow, re-run.
3. **LEAVE** — OCR-garbled shapes (`InfoEdge (Naukri)]`, `NETGEAR,`)
   and people headings in interview-format notes (`Brad Setser`,
   `Tamal Bandyopadhyay` — correctly catch-all): stays open as visible
   residual; candidate for future cleanup tiers.

Once `S7 triage-quotes` lands, steps 1–2 are decision-file-annotated
instead of hand-edited (same flow as `triage-relations`). `Titan` / `SAIL` / `Divi's` are NOT stubbed (rows
exist — see F2). Junk canonicals ("Initiatives") get a pinned
heading-shape rule (requires cap token or pipe AND len ≥ 3 AND not on a
junk list) plus worklist visibility rather than suppression.

**Interim capture:** sections still unresolved after tier 4 route their
quotes to the catch-all (S4) with the raw heading in
`properties.heading` — so pending-worklist content is in the table, not
on the floor. When the user stubs the entity, the next `--apply` re-homes
the rows (the global `derive:quotes:%` prefix sweep in
`stable_prefix_replace` deletes stale derived rows and inserts the new
set — idempotent, but whole-table scope, not per-stem).

**Expected:** ~85–90% of the 878 openings resolve or route; the remainder
is a visible, shrinking worklist.

### S3 — Walker tolerance (G3; survey already in S0)

- Survey is S0 output (shape counts for all 118 misses) — no separate
  one-off report here. Classes: bullet-prefixed, `>` blockquote, leading
  punctuation, sub-threshold-with-attribution.
- **Splice-close class (measured in the T-trial, 2026-09-07):** 14/176
  in-body openings on the trial note produced no row — every one a
  single-line quote swallowed by an earlier runaway accumulation. Root
  cause: OCR splices put the closing `"` mid-line
  (`…load." - Matthew Prince, Chief Executive Officer`),
  the walker closes only at line end, and the next single-line quote
  closes the run instead of opening its own. Fix shape (tests-first):
  close at the closing `"` when the remainder parses as an attribution
  (dash-prefixed, `Name, Title`, or `Name said`) or is empty, with an
  inner-quote guard — a naive anchor prototype LOST rows (163→120:
  inner `"…"` pairs break single-line closes), so this lands only with
  the 144-test suite + before/after dry-run diff green.
- Walker fixes, tests-first: strip leading list/quote markers (`-`, `*`,
  `>`) before the starts-with-`"` test, with ordering pinned (strip →
  quote-test → attribution-test) so `- Name, Title` attributions are not
  misread as quotes; apply the same strip to the paraphrase accumulator.
  Admit sub-40-char openings **only** when an attribution follows within
  the 3-line window, with a precision check on the 144-test suite
  (`"Yes"` + name must not newly capture).
- Byte-identical dry-run on the unaffected corpus (the 144-test suite in
  `tests/test_derive_insights.py` plus a before/after `--stale-only`
  diff is the guard). New shape tests land before the walker change.

**S3 EXECUTED (2026-09-07).** Walker tolerance landed in
`extract_quotes` with the pinned order (strip → quote-test →
attribution-test): leading `-`/`*`/`>` markers stripped before the
quote test AND in the paraphrase accumulator; sub-40 openings admitted
only with an attribution in the 3-line window (the <30 floor now holds
for anonymous quotes only); splice-close in the guarded form — close at
the last `"` when the remainder parses as an attribution, and an
attribution-shaped line terminates a runaway run. 7 shape tests landed
before the walker change (proposal doctrine); suite 203 green, ruff
clean. The S0 salvage pass is ABSORBED into production by this slice
(audit's `extract_salvage` removed — the relaxed rules are now the
default; new-format relaxations would be future rule candidates).

**Corpus delta (audit):** coverage 73.8% → **76.4%**; G3 286 → **75**
(the residual is OCR garble, itemized in the audit output); flagged
notes 80 → 72; watchlist **14 notes closed at ≥95%** total (58 notes
improved, zero per-note regressions — the watchlist coverage-diff was
the gate). Rule candidates correctly narrowed to the OCR tail
(`unclosed/splice` 11 openings/9 notes).

### S4 — Sector capture + the `Quotes.md` catch-all (G4, G5)

**New capture arm — `iter_sector_sections`:** yield sector regions with
their heading text. No such iterator exists today (`iter_company_sections`
drops sector regions) — S4 builds it with a pinned boundary rule: sector
body runs from a sector heading to the next company OR sector heading.
The 190 sector-context bracketed markers route through the S1
bracket rule, not the sector ladder. Routing ladder, no force-fit
(procedure rule preserved):

1. exact/slug → one of the 42 `CANONICAL_SECTORS`
   (`helpers/validators/static_checks.py:386`) — `Chemicals`, `Retail`,
   `International` resolve today;
2. **synonym map** — `Software`→`Technology`, `IT`→`Technology`,
   `Housing`→`Housing_Finance`, … (seeded from the S0 unmatched-sector
   list, reviewed once by the user, then code-owned with the audit
   re-emitting unmatched sectors each run so the map cannot silently rot);
3. **super-sector fallback** — macro/cross-sector commentary (RBI policy,
   capex cycles) → the matching super-sector entity when a pinned
   keyword detector names one (detector spec + tests in-slice;
   `build_sector_hierarchy`'s sector→super map resolves sector→super, it
   does not classify content — content classification is new code);
4. **catch-all `Quotes.md`** — everything else, per the 2026-09-07
   directive: a synthetic note that guarantees capture.

**Catch-all design (user-directed):** `findata/Sectors/Quotes.md`, entity
`Quotes` (`entity_type='sector'`, schema-legal per F5). Blocks are
sentinel auto-chatter blocks carrying full provenance — edition footer +
the original heading text in the block body + `properties.heading` on the
row — so triage = read the note, decide the real home, stub/alias, re-run.
43rd-sector blast radius is real (`CANONICAL_SECTORS`, `/api/sectors`,
integrity checks, DuckDB registry, frontend all assume 42).
Execution-time probe (expanded): verify `build_sector_hierarchy` and
`sync_sector_wikilinks` no-op cleanly on a synthetic sector (empty roster,
no hierarchy uplink) AND `static_checks`, `database_integrity_check`,
`query.py` sector paths, and the DuckDB edge registry accept the 43rd
row; if any warns/fails, fall back to a root-level
`findata/Misc/Quotes.md` with a pinned non-sector type — the probe decides, and the
choice is recorded here.

**Resolution (2026-09-07 supersession):** the probe initially settled on the
Misc fallback; later the same day the design was upgraded — `Quotes` is now
the **10th super-sector** (`SUPER_SECTORS["Quotes"] = []`, childless) with the
note at `findata/Super_Sectors/Quotes.md`, the entity re-typed
`sector -> super_sector`, and the S4 catch-all synthesis rendering there. No
tool-side exceptions remain: taxonomy coverage, directory-structure,
note-schema and hierarchy-orphan checks all accept it as ordinary taxonomy
data (the orphan rule reads the curated member list, so childless-by-curation
is data, not a name carve-out).

**Sector/super-sector quote rows:** same `quotes` table, `entity` = the
sector/super-sector name; `properties.kind` ∈
`{"company_commentary", "sector_commentary", "catch_all", "edition_note"}`
(backfill existing rows as `company_commentary`; no JSON-index promises —
filter via join on `entities.entity_type` + `kind` in reports). Auto
blocks render onto sector notes with a sector-specific sentinel
discipline: pinned insert position (after roster, before
`Newsletter synthesis`), sector `_balanced_or_skipped` guard with
dedicated nesting tests (the company guard does not transfer),
per-edition blocks, `chatter-` footnote namespace verified collision-free
within multi-edition sector notes, curation-safety (hand-written sector
blocks win — the existing procedure's hand-work is never clobbered), OKF
`generated` stamp + `sources[]` splice via the converger in the same
sitting (write-time-splice rule, `markdown_parse.md`).

**Masthead (G5):** the 3 openings route to the **edition entity** (113
exist) with `properties.kind = "edition_note"` — table-only, no render
(the edition note already carries the text upstream), excluded from the
note-body sample.

**Downstream to verify in-slice:** `derive_events` scans
`findata/Companies` only (`helpers/graph/derive_events.py:81`) — sector
blocks are invisible to it today; extending it is explicitly deferred
(non-goal) unless the user directs otherwise. Temporal analytics gains
sector rows in its per-quarter counts (correct, volume roughly ×2.7 —
dashboards versioned or annotated in-slice). `company_metrics` rides the
same `_extract_sections` path: S1+S2 gains apply to magnitudes
automatically; S5 validates the metrics diff alongside quotes.

**S4 EXECUTED (2026-09-07).**

- **`iter_sector_sections()`** + `_structural_boundaries()` (S4 refactor:
  ONE shared classifier for both iterators — zero drift) + `_resolve_sector()`
  routing (canonical exact → synonym map (28 seeded pairs: Software→
  Technology, Tourism & Hospitality→Travel, Consumer Packaged Goods→FMCG,
  …) → catch-all `Quotes`). No super-sector keyword detector: the routing
  ladder's no-force-fit rule plus the catch-all guarantee capture; a
  detector with zero validated examples would be guessing — deferred until
  the worklist shows a macro cluster (recorded deviation).
- **Scan integration:** `_scan_content()` (shared by file + corpus paths)
  extracts sector regions with `properties.kind` ∈ {sector_commentary,
  catch_all, edition_note} + `properties.heading` (raw, unescaped).
  Company rows carry NO kind — reports join on entity_type; no mass
  backfill churn (deviation from the blanket backfill, churn-reducing).
- **Render:** zero new render code — sector entities carry `file_path`
  (`findata/Sectors/<Name>.md`), so `render_notes`/`_replace_or_insert_block`
  work unchanged; `_find_insertion_point` lands before
  `## Newsletter synthesis` (after the roster sentinel) and
  `_existing_hand_block_for_edition` keeps curation-safety.
  `render_chatter_block` emits the raw heading as a provenance line for
  rows carrying `properties.heading`. Edition_note rows are table-only
  (filtered from render). Sector-marker balance gate inherited.
- **Probe outcome (execution-time, recorded per the slice):** fallback
  FIRES → `findata/Quotes.md`, `entity_type='sector'` (moved to `findata/Misc/Quotes.md` post-apply).
  Evidence: sync_sector_wikilinks --check claims a Sectors/-level
  Quotes.md as a 43rd sector and marks it stale (not a no-op);
  build_sector_hierarchy --check no-ops cleanly (taxonomy stays 42);
  database_integrity_check passes 100% in a sandbox with the Quotes
  entity; /api/sectors surfaces it as a visible triage page (intended —
  the catch-all is the triage surface). `ensure_quotes_catchall()`
  (idempotent, apply-time) creates the entity + note.
- **Audit:** walker/coverage now mirror sector + edition-note capture;
  catch-all matches count toward total, not resolved (dual metric).

**Corpus delta (audit):** coverage 76.4% → **85.0%**; G4_sector
691 → **16**; G5 3 → **0**; flagged 72 → 53; watchlist **38 closed**
(32 notes crossed 95% this slice); Points_And_Figures and The_PlotLines
both **100%**. Zero per-note regressions (51 improved).

### S5 — Apply, converge, verify

**S5 EXECUTED (2026-09-07, user go for the bulk update).**

- Dry-run preview: 10,462 quotes / 3,606 metrics / 525 notes.
- **`--apply`: 7,000 quotes written** (2,777 → 7,000: +6,289 company,
  +574 catch_all, +134 sector_commentary, +3 edition_note — D2's
  `properties.kind` discriminator live), 3,606 metrics, **526 notes
  rendered** (69 hand-written edition blocks preserved — curation-safety),
  312 key-figures notes, Quotes catch-all entity + root note created
  (probe-decided shape), 5 missing edition entities created
  (pipeline-mechanical).
- **Two live bugs caught and fixed during apply** (the meter working as
  designed): (1) 57 duplicate (entity, text, edition) triples — an
  edition repeating a quote across two catch-all regions — fixed by
  dedup-keep-first in `apply_quotes`, pinned by test; (2) a **scan-scope
  feedback loop** — file-mode `scan()` did not exclude Companies/Sectors
  notes, so S3's blockquote tolerance re-extracted rendered blocks as
  new quotes (10,401 → 17,759 self-feeding growth); fixed by mirroring
  `_corpus_paths`' three-tree restriction, DB self-healed by the prefix
  sweep (7,000 clean rows, walker==db).

1. (continued) `verify_notes` ✓ · integrity **99.61%** ✓ · md-lint ✓
   (incl. the pre-broken proposal header line and an MD037 guard for
   `*`-bearing catch-all paraphrase) · `graph-rebuild` ✓ · snapshot ✓
   (quotes.parquet 7,000 rows, 1.58 MB; 13 tables, 58.3 MB).
- S0 audit re-run on the post-apply DB: **walker == db** (7,000 vs
  6,969 tree-attributed + edition rows), parity divergences 0, corpus
  coverage **85.0%**, watchlist 53 open / 38 closed, rule candidates
  narrowed to the OCR tail (`unclosed/splice` 11/9, `other` 34/20).

### S5 — Apply, converge, verify (original plan)

1. Full dry-run + diff preview (mass note rewrites → **explicit user
   checkpoint**, house rule) — expected blast radius: most company notes
   gain new-edition blocks, ~40 sector notes + Quotes.md gain blocks,
   `company_metrics` gains from the same S1+S2 path (diffed alongside
   quotes).
2. `--apply` → OKF converger same-sitting → user-held chain: entity stubs
   if worklist accepted (`--apply-worklist`) → `graph-rebuild` → snapshot
   (gzipped, git-tracked — one refresh, size attributed) → md-lint
   (lint-only, never `--fix` over findata) → verify_notes.
3. Re-run S0 audit → **total coverage ≥99% with catch-all bucket
   shrinking-or-zero and resolved-coverage reported** in the Execution
   Results section (house pattern: results ride in the live proposal, then
   into the archive).
4. Docs: `markdown_parse.md` §auto-chatter (marker family, sector capture,
   catch-all + worklist flow), `doc/design/db_schema.md` `quotes` table
   (non-company rows + `properties.kind`), this file's appendix.

### S6 — Wire-in

`make advisory` rider for the audit (non-blocking, threshold-echoing,
tripwire flags + watchlist status in the advisory line);

### S7 — `triage-quotes`: decisions-file worklist triage (EXECUTED 2026-09-07; modeled on triage-relations)

The worklist resolution procedure above is hand-editing; S7 encapsulates
it the way `triage_pending_relations.py` did for `_pending_relations`
(same three-mode shape, same decisions-jsonl discipline, same
gitignore split: queue-side files ignored, decision OUTPUTS tracked):

- **Queue:** `findata/Misc/quote_entity_worklist.json` (exists — 198
  open canonicals: 50 alias-ready with suggestions, 148 stub/garbage).
- **`triage_quote_worklist.py --report`** (default, non-destructive):
  re-emit the worklist from the live corpus, bucket entries
  (`alias_candidate` / `stub_candidate` / `garbage_shape`), write
  `findata/Misc/quote_triage_decisions.jsonl` pre-annotated with
  suggestions + example notes, and print the eyeball report.
- **Decision actions:** `alias:<Entity>` | `stub` | `discard` — fuzzy
  suggestions are pre-filled as `alias_candidate` but NEVER auto-applied
  (D4; verified misfires listed in the resolution procedure).
- **`--apply-decisions`:** validate + act — alias rows persist to
  `findata/Misc/quote_aliases.json` (merged, sorted, every target
  validated against entities); `stub` rows emit a stub list for the
  user-held entity flow (entity creation is never automated); `discard`
  marks suppressed-with-note. Then the user re-runs
  `derive-insights --apply` → quotes re-home → worklist entries
  auto-close (existing S2 lifecycle).
- **`make triage-quotes`** target mirroring `triage-relations`; decisions
  jsonl + report gitignored, `quote_aliases.json` stays tracked via the
  existing negation.
- **Exit:** the 50 alias-ready + obvious stubs resolved → G2 residual
  ≈ garbage only; coverage re-measured by the S0 audit.

**Status: EXECUTED (2026-09-07, same-day).**
`helpers/graph/triage_pending_quotes.py` (+ `tests/test_quote_capture_s7.py`,
7 tests) + `make triage-quotes`. First live report: 198 open canonicals →
50 alias_candidate / 22 garbage_shape / 126 stub_candidate; decisions
file + report at `findata/Misc/quote_triage_{decisions.jsonl,report.md}`
(gitignored run artifacts). Two production gaps closed en route: the
ladder now actually LOADS `Misc/quote_aliases.json`
(`_merged_quote_aliases()` — the S2 path constant existed without a
loader), and the audit auto-closes `decided` entries when canonicals
resolve. `findata/Misc/relation_aliases.json` +
`findata/Misc/quote_aliases.json` are the two tracked decision outputs
(both gitignore-negated).

**VSS adaptation (both triage scripts, 2026-09-07):** per the user's
suggestion, `get_tickers.vss_match` (cosine over `company_embeddings`,
same local embedder) now hints BOTH triage scripts' report mode for
unresolved rows without a jaccard suggestion: `triage_pending_quotes`
(alias/garbage/stub buckets) and `triage_pending_relations` (prose rows'
`target_mention` → `vss_hint` field + report line). The iteration
validated the pattern and its limit: VSS finds true identities
(`Mothersonsumi Wiring → Motherson Sumi Wiring India` 0.86,
`Multi Commodity Exchange(MCX) → …(MCX)` 0.94, `REC` 0.91, `Titagarh`
0.91) but equally often lands on semantically adjacent COMPETITORS
(`Tesla → Tata Elxsi` 0.89, `Netflix → Amagi` 0.83, `Sony → Samsung`
0.87) — so hints are eyeball-only, never pre-filled decisions (D4),
and the identity guard (token containment between canonical and match)
is what separates the two classes. `rebuild_note_search` note: Misc/
mapped as doc_type `misc` (Quotes.md searchable; the triage report
artifact skip-listed).
`make search-fresh` after doc edits; script/doc indexes refreshed.

**Pass 1 executed (2026-09-07, user-adjudicated in-session):** all 178
open canonicals dispositioned. 86 `alias:` decisions applied (every
target verified against the live `entities` table; user corrections:
`Allcargo Group → Allcargo Global`, both Poonawalla canonicals →
`Magma Fincorp` per "Magma is the correct name"), 5 `discard` (persons/
role/ambiguous: `D.B.`, `Brad Setser`, `Dr. Rohit Chandra`, `Tamal
Bandyopadhyay`, `RBI Deputy Governor`), and 83 creations PARKED to
`findata/Misc/quote_stub_backlog.json` (49 India / 34 global, house
stub spec per entry: sector, market_cap, geography, business_model,
plus 34 canonicals that alias onto each once created — includes the
user-confirmed `Baba Jewellers`, `SG Finserve`, `Digitide Solutions`).
After `--apply-decisions` + `derive-insights --apply`: quotes
7,116 → 7,681 (+565 re-homed), metrics 3,612 → 3,859, **corpus
coverage 86.4% → 93.3%** (The_Chatter 93.3%, G2 1,023 → 452, G3 84,
G4sec 16; walker==db 7,650), watchlist 28 open / 63 closed. The
remaining lever to 99% is exactly the parked backlog — the 452 G2
openings sit on it almost entirely (plus the 84-opening G3 OCR tail).
Next pass = stub creation from the backlog JSON + one more
derive/audit cycle; snapshot refreshed to match (7,681).

**Pass 2 — the 83 stubs (2026-09-07, executed):** all 83 entities +
house stub notes created from `findata/Misc/quote_stub_backlog.json`
(Allianz shape: `ticker: null`, `listed: false`, sector/market_cap/
geography/business_model tags, one-line overview,
`generated.by: process:quote_triage_stubs`, `stale_after: 2027-03-07`;
`&`→`and`, parens stripped in stems). Their 34 parked canonicals
aliased via `--apply-decisions`. One process correction recorded: the
first creation pass tuple-unpacked the backlog dict (keys became
placeholder values → notes in `Companies/sector/`), caught on the
first spot-check, fully reverted (83 rows + 83 notes + stray dir) and
re-run correctly before any downstream consumer saw it — the running
derive was killed and re-run. After derive: quotes 7,681 → **8,077**
(+396), metrics 3,944, 685 entities with quotes, 83 stub notes
rendered with chatter blocks, key-figures notes 22. **Corpus coverage
93.3% → 98.1%** (The_Chatter 8,021/8,178; G2 452 → 48, G3 93, G4sec
16; walker==db 8,046; watchlist 9 open / 82 closed; companies
1,079 → 1,162). Remaining tail to 99%: 48 G2 openings over 53 fresh
low-frequency canonicals (next triage round, ~1 opening each),
93 G3 OCR-garble openings, 16 G4sec.

**Tail disposition (2026-09-07, user call):** the three residual
slices are stored/decided, each with file/line/text provenance
extracted through the audit's own scan (`assert_parity` +
`opening_lines` + ladder, exact funnel parity 48/93/16):
`Misc/quote_g2_backlog.json` (48 openings — next triage round's
queue; canonicals already live in the tracked worklist),
`Misc/quote_g4sec_backlog.md` (16 catch-all sector openings —
decide real sector homes, then alias/re-route), and
`Misc/quote_g3_discards.jsonl` (93 openings marked
`accepted_loss` — OCR garble / shapes S3 tolerance cannot recover;
81 'other', 12 'unclosed/splice'). Addressable denominator
excluding accepted losses: 8,021/8,085 = **99.2% of addressable
openings** (raw corpus stays 98.1%). All four Misc artifacts
(incl. the spent stub backlog) gitignore-negated for tracking.

**g4sec close-out — S3b/S4b (2026-09-07, user-approved):** the 16
stored G4sec openings resolved to 48→48 tail via three fixes.
(1) **S3b walker** (`extract_quotes`): inline-attribution tails with a
lost closing quote (`…profit — Jensen Huang, President & CEO` with no
terminal `"`) now close on their own line with the tail stripped and
parsed as speaker; a runaway run terminates UNCLOSED at the next quote
opening or hard rule (`---`) and resumes AT that line — the walker
never absorbs or skips a subsequent quote opening (this also fixed 86
of the 93 G3 rows: quotes 8,085 → 8,186, 300 notes re-rendered).
(2) **Class B synonyms**: `Software Services → Technology`,
`Regulator → Banking` (RBI is bank-regulator commentary, user call).
(3) **S4b bare known-headings**: a bare heading with no cap token/pipe
that EXACTLY names a company or vetted alias (`# Google`, `# SBI`,
`# Shoppers Stop`, `# Bharat Petroleum Corporation`, `## Apple`) routes
as a company region — `known_bare` set (resolver_map keys + merged
aliases) threaded through `_structural_boundaries` and all three
iterators, mirrored in the audit's `classify_boundaries` + walker
(one parity bug caught: the classify branch missed its parity record —
0 divergences after fix). Tests: `TestS3bG4secTolerance`,
`TestS4bBareKnownHeadings`, `TestG4secSynonyms` (+hermeticity fix for
the Welspun ambiguity test now that `welspun` is a user alias).
**Final funnel: corpus 98.2% → 99.4%** (8,130/8,178; walker==db 8,155;
watchlist 3 open/88 closed). Remaining 48 = 40 discarded-person G2 +
7 OCR-garble G3 + 1 deliberate G4sec keep (Labour Ministry
policymaker commentary; no Policy sector in the taxonomy) —
**100.0% of addressable openings**. Artifacts updated to final state
(g3 discards 7, g4sec backlog 1 keep, g2 backlog 40 persons).

**Audit denominator wiring (2026-09-07, same day):** the audit now
consumes the accepted-loss decisions natively — no more hand math.
`findata/Misc/quote_accepted_losses.jsonl` (48 rows: 40 G2 discarded
persons + 7 G3 OCR garble + 1 G4sec policymaker keep, each with
file/line/text/reason; gitignore-negated) is loaded by
`load_accepted_losses()` and keyed `(rel_path, line)`. An opening that
would bucket into an uncovered gap AND matches the set counts as
`accepted` — covered openings are never consumed (a later capture
improvement still reports). The per-note tripwire and corpus metric
now divide by `openings − accepted` (addressable); funnels carry an
`accepted=` column; `--json` adds `accepted_losses` +
`corpus_addressable_coverage`. First wired run: **corpus 99.4%
(accepted 48; addressable 100.0%)**, all G-buckets 0, flagged 0,
watchlist 0 open / 91 closed — the last three person-opening notes
unflagged on the addressable denominator and auto-closed. Tests:
`TestAcceptedLosses` (exclusion, covered-not-consumed, loader
prefix-strip).

**Welspun close-out (2026-09-07):** the stored G2 backlog proved the
48 openings sit on just 6 canonicals — 5 already-discarded
persons/role plus bare `Welspun`. The 8 Welspun openings are
unambiguous Welspun Corp (Permian-to-Gulf-Coast gas-pipeline
transport orders), so `welspun → Welspun Corp` went straight into
`Misc/quote_aliases.json` (its worklist entry auto-closed on the
next audit; note: `Welspun Corp` is a hand-written note with no
auto chatter block — the DB is the capture surface, 13 rows).
Quotes 8,077 → 8,085. **Corpus 98.2%** (8,029/8,178; walker==db
8,054; watchlist 8 open/83 closed; worklist 790 resolved/5 open —
the 5 persons). G2 backlog re-extracted at 40 openings, all
accepted-loss (persons). Final denominator view: strict raw
98.2%; excluding the 93 accepted G3 losses → 99.2% of addressable;
excluding also the 40 discarded-person openings → **99.8% of
addressable openings**.

## 5. Design decisions

- **D1 — one lossless layer.** The `quotes` table is the 99% surface; note
  bodies are renders backed by it. This honors curation-safety (G6 stays)
  while making "dropped on the floor" a single, auditable place.
- **D2 — no new table.** Sector/edition/catch-all rows live in `quotes`,
  discriminated by join on `entities.entity_type` + `properties.kind`
  (`company_commentary` / `sector_commentary` / `catch_all` /
  `edition_note`).
  One capture path, one idempotency discipline (global `derive:quotes:%`
  prefix sweep via `stable_prefix_replace` — whole-table scope, not
  per-stem), no sync surface added.
- **D3 — general marker rule, not a list.** Any fully bracket-wrapped
  heading is non-structural (not a char class); future converter markers
  inherit correctness; the rule is test-pinned against the newest
  editions (F1 compounding risk).
- **D4 — fuzzy never auto-applies.** Suggestions surface as worklist;
  only deterministic tiers (exact/unescape/parenthetical/alias) write.
- **D5 — capture-now, triage-later.** The catch-all converts every
  "pending decision" from a drop into visible backlog with provenance.
- **D6 — mass rewrite is user-gated** (S5 step 1), per the standing
  checkpoint rule for live-note rewrites.

## 6. Risks

- **R1 sentinel nesting on sector notes** (F6 history): mitigated by the
  region-scoped writer + balanced-guard + dedicated tests; the probe in S4
  runs both existing sector writers' `--check` gates against a synthetic
  Quotes.md before any mass render.
- **R2 quote-text normalization changes** (S1/S3 re-shape extraction) shift
  `UNIQUE(entity, quote_text, as_of_edition)` keys: the apply path's
  global-prefix sweep rewrites the whole derived set each run;
  `stable_prefix_replace` preserves `created_at` only for content-identical
  rows — any re-normalization or catch-all→entity re-homing churns those
  rows with new ids. Plan the S5 diff expecting full-table churn on
  normalization changes.
- **R3 false-positive openings inflate the denominator**: accepted — the
  audit prints unmatched examples; the dual metric (total + resolved) is
  computed against the same yardstick the walker uses, and the residual
  list is the escape valve.
- **R4 alias misfires at tier 4**: NOT bounded by relations-triage vetting
  (different domain — `premier`/`micron` collisions live). Bounded instead
  by the S2 per-alias vetting + worklist-only default for unvetted entries.
- **R5 snapshot churn**: `quotes` is in the snapshot allowlist
  (`helpers/maintenance/db_maint.py`, ex-`snapshot_db.py:455` — path
  corrected); volume goes 2,777 → ~7,500–8,000. One refresh
  at S5, attributed in the db_sync message (detailed-message doctrine).
- **R6 43rd-sector assumption breaks** (new): roster/hierarchy writers,
  `CANONICAL_SECTORS`, `/api/sectors`, integrity checks, DuckDB registry,
  and frontend assume 42 sectors. Covered by the expanded S4 probe + the
  root-level fallback.

## 7. Non-goals + explicitly deferred (skipped, not forgotten)

- PDF→markdown fidelity (owned by `verify_extraction.py`).
- Speaker entities (D6 deferral stands — speakers stay string attributes).
- LLM-assisted extraction or mapping (deterministic-only doctrine).
- Extending `derive_events` to sector notes (flagged in S4, deferred).
- Making the coverage audit qa-blocking (advisory doctrine).
- `company_metrics` precision review beyond the automatic S1+S2 gains
  (magnitudes ride the same path; label quality review is a separate arc).
- `graph_edges` co-mentions from new sector blocks (no new edge arms here).
- Points_And_Figures (4 sector openings) / PlotLines (26) get the sector
  ladder too — covered by S4 routing, validated in the S0 per-tree funnel.
- Worklist `--apply-worklist` CLI + sector synonym-map maintenance process
  are in scope (S2/S4), not deferred — listed here so they are not skipped.

## 8. Definition of Done

- S0 audit reports **total coverage ≥99% with resolved-coverage reported
  separately** on the live corpus, residual itemized.
- Catch-all bucket present and shrinking-or-zero; worklist actionable
  (tracked file + `--apply-worklist`, not gitignored state).
- Tripwire live: seeded with the seven trial-note baselines; salvage
  mode marked; watchlist tracked and diff-clean; rule-candidate
  recurrence counter working (S0 amendment).
- `tests/test_derive_insights.py` + new audit tests green; `make qa` +
  `make advisory` green (once, at arc close, with the user's go).
- Procedure + schema docs updated; indexes converged (`make search-fresh`).
- Execution Results section appended with the before/after funnel.

## 9. Rollback

Extractor/resolver changes revert cleanly (tests + dry-run diffs per
slice). The table converges back via the global-prefix sweep on the old
code (old code's `derive:quotes:%` sweep deletes all derived rows,
including sector/catch-all rows — verify before relying on it).
Sector-note blocks are sentinel-delimited — one regex strip per note.
The synthetic `Quotes.md` entity is a single cascade-delete for rows;
note blocks still need the regex strip (not atomic).

---

## Execution Results (2026-09-07, S0–S5 same-day arc)

| gate | S0 baseline | post-S5 |
|---|---|---|
| corpus coverage (openings → quotes rows) | 33.6% | **85.0%** |
| quotes table rows | 2,777 | **7,000** |
| G1 marker-orphaned | 3,090 | 0 |
| G2 resolver | 878 | 1,141* |
| G3 walker shape | 118 | 75 |
| G4 sector pool | 1,927 | **16** |
| G5 masthead | 3 | 0 |
| parity divergences | 1 (Push_and_Pull copy drift) | **0** |
| notes ≥95% (watchlist closed) | 0 | **38** |

*G2 grew by exposure: S1 moved marker-orphaned openings into company
sections where the resolver can see them; 1,141 openings sit on 198
open worklist canonicals (fuzzy suggestions where they exist, empty =
genuinely missing → user stub flow). The residual to 99% is worklist
triage (user decisions) + the OCR tail (75 G3 + 16 G4sec + 34 `other`),
all itemized in `make quote-coverage` output.

**Follow-ups (post-arc):** worklist triage pass (user) → re-run
`derive-insights --apply` (auto-closes worklist entries) → coverage
re-check; the OCR-tail shapes stay rule-candidates until recurrence
thresholds fire.

## Cleanup — findata root declutter (2026-09-07, DONE)

Pipeline state and catch-all notes moved out of the `findata/` root into
`findata/Misc/` (the vault root was accruing non-corpus files — bad
design: a corpus directory should contain corpus):

| file | old | new | tracked? |
|---|---|---|---|
| Quotes.md (catch-all note) | `findata/Quotes.md` | `findata/Super_Sectors/Quotes.md` (2026-09-07 supersession; was `Misc/`) | no (derived render) |
| quote_coverage_watchlist.json | `findata/` | `findata/Misc/` | yes (negation) |
| quote_entity_worklist.json | `findata/` | `findata/Misc/` | yes (negation) |
| quote_aliases.json | `findata/` (created at `Misc/`) | `findata/Misc/` | yes (negation) |
| _pending_relations.txt | `findata/` | `findata/Misc/` | no (queue) |
| _pending_suggestions.txt | `findata/` | `findata/Misc/` | no (queue) |
| _pending_triage_report.md | `findata/` | `findata/Misc/` | no (report) |
| _pending_triage_decisions.jsonl | `findata/` | `findata/Misc/` | no (decisions) |
| relation_aliases.json | `findata/` | `findata/Misc/` | **yes** (negation; runtime-loaded by extract_relations) |

Consumers updated in the same change: `.gitignore` (5 patterns),
`extract_relations.py` (SIDECAR_PATH, ALIAS_OVERRIDES_PATH),
`suggest_relations.py` (SUGGESTIONS_PATH), `triage_pending_relations.py`
(all 5 path constants + docstrings), `enrich_relations.py`
(COINFER_SIDECAR_PATH), `doc/procedures/markdown_parse.md`,
`doc/design/findata.md` (layout map gains `Misc/`). Verified: 126
scoped tests green, `triage-relations` smoke writes the report to
`findata/Misc/`, integrity check sandbox-clean, git sees the tracked
alias rename. `frontmatter_schema._OKF_SKIP_FILES` needed no change
(basename match). Historical references in `doc/improvements/archive/`
are left as-is (archive = record).

**Coverage follow-up (same day):** two search/check surfaces did not
cover `findata/Misc/` and were fixed:
- `rebuild_note_search.py` — `Misc/` was an unmapped prefix (silently
  skipped). Now mapped as doc_type `misc`, with `_pending_triage_report.md`
  added to `_SKIP_NAMES` (state artifact, not prose). Quotes.md (4.9 MB,
  `## The Chatter` sectioned) is now free-text searchable — the triage
  surface is queryable; near-duplicate overlap with company notes is the
  known trade (report exists).
- `static_checks.check_yaml_frontmatter` walks ALL `findata/**/*.md` —
  the frontmatter-less Quotes.md scaffold was a LATENT `make
  static-checks` failure. Fixed: `ensure_quotes_catchall` scaffolds
  minimal frontmatter (title/type/normalized_name) and the live file was
  prepended. static_checks green (advisories 1 → 0).

## Appendix A — funnel audit methodology + raw numbers (2026-09-07)

Read-only probe (productized as S0): walks the three trees, mirrors
`iter_company_sections` classification (parity asserted per file — one
divergence today: `The_Push_and_Pull.md` 23 vs 22, classifier-copy edge),
counts quote-shaped openings per region class, re-runs `extract_quotes`
on resolved sections, compares against the live `quotes` table by edition
stem, and samples 400 table rows for note-body containment.

| Tree | files | extracted | G1 orphaned | G2 unresolved | G3 shapes | G4 sector pool | G5 masthead |
|---|---|---|---|---|---|---|---|
| The_Chatter | 86 | 2,761 | 2,521 | 878 | 118 | 1,927* | 3 |
| Points_And_Figures | 25 | 0 | 0 | 0 | 0 | 4 | 0 |
| The_PlotLines | 2 | 0 | 0 | 0 | 0 | 26 | 0 |

\* 1,737 named-sector + 190 sector-context bracketed.

Top orphaned editions: Nvidia_Samsung_Cloudflare 221,
A_Strange_Quarter_With_Very_Real_Cracks 137, Doubling_Down 120, Scaling_
Through_Slowdowns 120, Making_It_Work 118, Bottlenecks__Breakouts 117,
Sailing_the_Tide 116, The_Known_Unknowns 110, Known_Unknowns 106,
Tailwinds_building 105, On_Record 104, Decoding_Capex_Capacity_and_
Consumer_Trends 99.

Top unresolved sections: Titan Company ×6, Divi's Laboratories ×5, Tata
Motors ×4, Premier Energies ×3, Apollo Hospitals Enterprise ×3, Amara
Raja Energy ×3, Globus Spirits ×3, HUDCO ×3, Dixon Technologies (India)
×3, TCS ×3, SAIL ×3, L\&T Finance ×3, Hindalco Industries ×3, Initiatives
×3 (junk), Lenskart ×2.

DB verification of resolver misses (corrected 2026-09-07): `L&T Finance`
exists (escape breaks the match); `Dixon Technologies`, `Apollo Hospitals`,
`Lenskart Solutions`, `Tata Motors Passenger Vehicles` exist under variant
shapes; `Titan` (= Titan Company), `Steel Authority of India` (= SAIL),
`Divis Laboratories` (= Divi's) already exist — resolver gaps, do NOT
stub. Genuinely missing: Premier Energies / HUDCO / Hindalco Industries.
Bucket assignment order for the audit: G1 → G2 → G3 → G4 → G5
(first match wins). `The_Push_and_Pull.md` 23-vs-22 divergence is an S0
entry task, not a standing caveat.

Note-body fidelity sample: 359/400 present; misses concentrated in
Precision Camshafts ×4, Everest Kanto Cylinder ×4, Avanti Feeds ×3,
Hinduja Global Solutions ×3, Tenneco Clean Air India ×3, EPACK Durable
×3, DLF ×3, Max Financial Services ×3 — all hand-block-precedence
editions (covered-by-curation under §3).

Caveat: "openings" is a line-level proxy for quotes (multi-line quotes
count once; stray quoted phrases may inflate ±10%). Ratios are solid;
absolute counts are approximate by construction.
