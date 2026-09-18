---
title: "NIC-2008 seed table — versioned vocabulary, scheme projection, industry coding lane"
status: proposed
filed: "2026-09-17"
executed: null
completed_md: null
area: "database — helpers/misc (seed), helpers/core/cin.py (cross-check), integrity"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# NIC-2008 seed table — versioned vocabulary, scheme projection, industry coding lane

**Date:** 2026-09-17 · **Status:** PROPOSED ·
**Area:** database — helpers/misc (seed/converger), helpers/core/cin.py
(cross-check), integrity, ontology docs

**Follows:** the standing #234 deferral (`ontology_convention_stack.md`
§7: "NIC-2008 seed-table builder (own slice, with filling
industry_code)"); first named consumer of the #244 concept-lifecycle
promote lane (`ontology_governance.md` S1). DDL per the assessment memo
§6.5 (`doc/local/engineering/ontology_assessment.md:343`).

## 1. Motivation

`cin_nic5` is carried but references nothing. 728 companies hold a CIN
(all five facets parsed), yet the 320 distinct 5-digit codes are dead
strings — no vocabulary table to join, no descriptions, no hierarchy.
Industry coding is stalled at the same place #234 left it: 942 notes
carry free-text `industry:`, **zero** carry `industry_code`, and the
only taxonomy bridge is the 103-label industry→subsector crosswalk.
The #244 lifecycle shipped candidate/promote precisely for this
migration ("auto-suggested codes land as candidate mappings, operator
promotes") — this slice is that first consumer.

## 2. Evidence (measured 2026-09-17, this box)

Live store (`memory/research.db`, read-only) and vault grep, both
post-chatter-ingest:

| Probe | Value |
|---|---|
| entities / companies | 6,753 / 6,203 |
| CINs with cin_nic5 | 728 (all CINs parse; 728/728 since S3) |
| distinct cin_nic5 codes | 320 |
| top divisions | 74 ×79 · 24 ×66 · 65 ×51 · 72 ×32 · 40 ×30 · 29 ×30 |
| vintage ≥2008 (NIC-2008-native) | 124 companies / 87 distinct codes |
| vintage <2008 (NIC-98/2004 legacy) | 604 companies / 282 distinct codes |
| concepts (active) | 444 across 11 schemes; `industry` = 117, `subsector` = 141 |
| concept_mappings | 103 active, all industry→subsector exactMatch |
| vault `industry:` carriers / `industry_code` | 942 / 0 (#234 baseline: 816) |

The vintage split is the D-O6 caveat quantified: only 87 of 320 codes
are first-class NIC-2008 joins on incorporation year; 282 belong to
companies registered before the 2008 switchover and must stay
vintage-labeled WARNINGs, never silent FK joins.

## 3. Design

Source is free and primary: MoSPI `ec6_NIC_2008_Code.pdf` or the
queryable DGE tables (no bulk-MCA dependency). One module, two modes —
`helpers/misc/seed_nic2008.py`:

- **build mode** (operator-run, once per source refresh): parse the
  primary source, emit the tracked vocabulary file
  `helpers/misc/nic2008_seed.json` — vendored data reviewed in patches
  like code (the `embed_eval_questions.json` precedent).

  Home ruled 2026-09-17: not `memory/` (gitignored runtime state,
  snapshot-restored — a seed there would vanish from fresh checkouts
  and diverge across worktrees), not `findata/Misc` (writer-owned
  vault; its whitelisted JSONs are operator worklists, and only the
  **export** `nic_worklist.json` belongs there), not `tests/data/`
  (fixtures for tests, not core workflow — the same ruling that
  rehomed `ontology_questions.json` at #244 S2c). Machine-consumed
  vocabulary lives beside its consumer in the tracked code tree —
  `helpers/misc/` precedent ×2: `embed_eval_questions.json`,
  `ontology_questions.json` (84 KB). JSON over parquet so patch
  review sees row diffs, not byte-rewrites. Runtime truth is the
  `nic2008` table in canonical SQLite; its restorable form flows
  through the existing `snapshots/parquet/` machinery.
- **converge mode** (maint-wired PRE_FULL): project the `nic2008` table
  and scheme deterministically from the tracked JSON; idempotent;
  roster-owned rows insert active, roster-dropped rows supersede (#244
  S1 converger semantics — never delete).

Table shape = assessment §6.5 DDL plus the scope note it recommends:

```sql
CREATE TABLE nic2008 (
  subclass    CHAR(5) PRIMARY KEY,  -- '01111'
  class       CHAR(4) NOT NULL,     -- '0111' (== ISIC Rev.4 class)
  grp         CHAR(3) NOT NULL,     -- '011' ('group' is reserved)
  division    CHAR(2) NOT NULL,     -- '01'
  section     CHAR(1) NOT NULL,     -- 'A'..'U'
  description TEXT NOT NULL,        -- 'Growing of wheat'
  isic4       CHAR(4) NOT NULL,     -- == class here
  nace21      TEXT,                 -- reserved, filled op-paced
  gics        TEXT,                 -- opaque peer code, never our own
  wikidata    TEXT,                 -- QID for the closeMatch hub
  scope_note  TEXT,                 -- PDF inclusion/exclusions
  version     TEXT NOT NULL DEFAULT 'NIC-2008'
);
```

Slices (each independently landable; S1 unblocks all):

1. **S1 — vendored table + converger.** Build-mode parse pinned by
   attested rows (01111 wheat, 10728 molasses, 62090 IT services);
   1,304 subclass rows expected (§6.5, primary-source attested);
   intermediate-level counts (sections/divisions/groups/classes) are
   measured by parse tests at landing, not asserted here. SQLite
   schema v12→v13 (`db.py` EXPECTED_*); no DuckDB cache bump — the
   table is canonical-only like `concepts`. Maint chain gains the
   step; `test_maint` pins updated.
2. **S2 — scheme projection.** `nic2008` joins `concept_schemes`; the
   full five-level broader chain (section→division→group→class→
   subclass, ~2.3k concepts total) makes every level `subtree()`-
   native; `notation` = code, `pref_label` = description, `scope_note`
   from the PDF statements, `source_ref` = `seed:nic2008/<version>`.
   Master doc §2 `concept_schemes` roster gains the row. Question set
   v3: +1 subtree pin per two levels (a section, a division) and +1
   crosswalk pin — ids named at S2 following the shipped grammar.
3. **S3 — coding lane.** Deterministic suggestions for the 117
   industry labels: top-k=3 NIC subclasses per label via the existing
   concept-embedding/lexical lane (no LLM — API posture unchanged),
   landing as **candidate** industry→nic2008 `concept_mappings`
   (closeMatch default; operator re-tags at promote). The company's
   own post-2008 `cin_nic5` is a second signal to display alongside
   (87 codes where it applies). 117 labels × 3 = one operator sitting;
   promotion via the existing `--promote-map` plan-then-apply.
4. **S4 — integrity + worklist.** `check_concepts`/identifier checks
   gain cin_nic5↔nic2008 WARNINGs: code absent while vintage ≥2008 =
   data-quality warn; pre-2008 = vintage-labeled warn (D-O6;
   `helpers/core/cin.py` already warns on vintage). Export
   `findata/Misc/nic_worklist.json` (the `subsector_worklist.json`
   pattern): labels, top-3 suggestions, CIN signal — the operator
   stamps `industry_code`/`industry_label`/`industry_source:
   nic2008`/`industry_version` frontmatter from it. The vault is
   writer-owned; nothing auto-writes.
5. **S5 — gate + first live rebaseline.** The eval-gate bullet below
   is mandatory (query-visible: new scheme + crosswalks). On ACCEPT,
   `--rebaseline` — the first live rebaseline the gate will have had
   (#244 anticipated exactly this); version bump + accepting ref
   stamped in `ontology_questions.json`.

## 4. Acceptance criteria & shakedown

1. build parse: 1,304 subclass rows; attested spot rows byte-equal;
   sections A–U all present; intermediate counts pinned by tests.
2. converger idempotence: second dry-run reports zero changes —
   repeated twice, never one run.
3. `subtree()` on a projected section returns the full chain through
   to its subclasses (spot: the IT chain contains 62090).
4. **Eval-gate bullet (mandatory — query-visible change):** run the
   gate (`doc/procedures/ontology-gate.md` flow) between dry-run and
   canonical apply with `--expect-improve` naming the v3 pin ids —
   zero regressions, no undeclared changes, declared improvements
   materialize; then `--rebaseline` only on ACCEPT.
5. gates: `make qa` green with updated maint pins; scoped suites
   (seed, integrity, gate) re-run ×2.
6. worklist exported (117 labels × top-3); promotions land as active
   mappings; `verify_notes` `industry_coded` starts moving off 0
   (operator-paced; ceiling 942 carriers).

| Projected outcome | Today | After |
|---|---|---|
| joinable cin_nic5 codes | 0 of 320 | 87 native + 282 vintage-labeled |
| concept count | 444 | ~2.7k (all five NIC levels) |
| industry crosswalks | 103 (label→subsector) | + candidates → operator-promoted label→NIC |
| `industry_code` fill | 0 / 942 carriers | worklist-driven, operator-paced |

## 5. Risks

- **PDF parse brittleness** — DGE queryable tables as the alternate
  source; attested rows pin the parser; `version` absorbs NIC
  revisions (1970/87/98/2004/2008).
- **Pre-2008 false joins (D-O6)** — WARNING tier only, never FK
  enforcement; the 604/282 legacy split is measured above.
- **prefLabel collisions across chain levels** — the active-scoped
  single-prefLabel check names any at landing; descriptions are
  level-distinct in the primary source.
- **Concept-table growth (~6×)** — trivial for SQLite; subtree and
  gate-pin timings measured at S2 landing.
- **Vault discipline** — worklist export only; the writer stamps.

## 6. Non-goals

- Filling `nace21`/`gics`/`wikidata` (columns reserved; op-paced data
  acquisition, GICS stays opaque peer input — D-O3).
- Legacy NIC-98/2004 tables or back-conversion (vintage WARNING only).
- The #244 deferred DDL-enum roster lift — this arc adds no enum
  values; the same-patch trigger stays armed.
- LLM-assisted suggestions (LLM-API posture unchanged).
- Auto-writing note frontmatter.

## Execution record (progressive)

### S1 — vendored seed + table (landed 2026-09-18)

- Source reconciliation corrected two proposal misattestations:
  **62090 does not exist** in NIC-2008 (the IT pin is **62099**;
  62090 is a misprint for the 62099 subclass of class 6209), and the
  **1,304 subclass count reproduces from neither official source** —
  the MoSPI PDF ∩ the DGE queryable tables give 1,295 shared codes + 6
  zero-ending single subclasses the PDF prints and DGE drops (01420,
  30120, 31001, 37001, 47640, 81100) = **1,301**. Both corrections are
  pinned in `EXPECTED_COUNTS` comments + tests.
- Parser handles the primary PDF's real defects: collapsed
  `Division16:` header (repaired from the summary part), a note
  continuation reading `division 25` that defeats naive header regexes,
  bare-code rows, no-space code+title rows, 2 genuine typos
  (`88230→84230`, `95494→85494`), and 8 prefix-quirk codes both official
  sources print verbatim (20203, 65020, 9690x) landed under their true
  block parents via `PARENT_OVERRIDE`.
- Converger live: 1,301 rows, 21 sections; idempotent (second apply
  0/0/0, verified twice); schema v12→v13; maint PRE_FULL step 8.

### S2 — scheme projection (landed 2026-09-18)

- 2,067 concepts (21/88/238/419/1301 across five levels), scheme row
  `classification`/`NIC-2008`, notation=code, class-grain scope notes,
  `source_ref=seed:nic2008/NIC-2008`. `subtree(nic2008:J)`=97,
  `(nic2008:A)`=177, `(nic2008:62)`=12 — the IT chain resolves through
  62099 (the corrected pin; acceptance #3's "62090" was the
  misattestation).
- **Coexistence bug found + fixed in `seed_concepts`**: its apply pass
  ran an UNSCOPED `DELETE FROM concept_schemes` (would delete the
  nic2008 scheme row and dangle 2k+ concepts under maint's
  FK-enabled connect) and its supersede pass claimed the whole
  `seed:%` domain. Now: scheme reinsert scoped to its own roster ids;
  reads carve out `seed:nic2008/%` (the first carve-out attempt used
  `seed:nic2008:%` — no match, the source_ref is `seed:nic2008/<v>`).
  Pinned by `TestCoexistence`.
- Eval gate (procedure flow): undeclared scan pre→post **ACCEPT 79/79,
  zero reasons** (a new scheme moves no pinned surface); v3 subtree
  pins added (`subtree-nic2008-section-information-communication`
  root J, `subtree-nic2008-division-it-services` root 62; expected via
  `answer_question` from the gated candidate); declared run
  `--expect-improve` both ids → **ACCEPT 81/81**; canonical apply;
  live-vs-live self-check ACCEPT. Version stays 2 (bumps only at S5
  rebaseline per the question-file contract).
- Pref-label duplicates within `nic2008` are REAL (NIC repeats titles
  down single-child chains; max 6 = "Manufacture of weapons and
  ammunition") — the risk-section claim "descriptions are
  level-distinct" is wrong for ~200 labels. `check_concepts` is
  warning-severity; integrity ×2 error-free, nothing surfaced.

### S3 — coding lane (landed 2026-09-18)

- Lexical scorer (`_lex_score`): content-token F1 (0.6, with >=3-char
  prefix pairs catching airlines~air / banks~bank) + containment (0.2) +
  difflib ratio (0.2); zero token overlap = zero score (no filler rows).
  The concept EMBEDDING lane does not exist for concepts
  (company_embeddings is company-grain), so S3 ships the lexical arm —
  no model load, no API, fully deterministic.
- 319 candidate closeMatch rows live (117 labels: 109 with top-3, 8
  no-signal named in the worklist). Idempotent twice. Promoted labels
  are never re-suggested and their leftover candidates retire; PROMOTED
  rows are untouchable by the candidate converger even though they keep
  its source_ref (the first version would have superseded an operator
  promotion — caught by the promote-lane interop test).
- Promote specs need the 4-part form with `:closeMatch` (the
  `--promote-map` default is exactMatch); the worklist prints each
  command verbatim.
- Parser round 2: the PDF re-prints its column header after every page
  break as ONE line — 56 descriptions + 29 notes were glued; skip-regex
  added, seed rebuilt (counts/defect-fixes byte-identical), live
  re-converged (185 table + 91 concept updates), gate self-check clean.

### S4 — integrity + worklist (landed 2026-09-18)

- `check_cin_nic2008` (WARNING severity, report section added): live =
  93 native / 113 post-2008 codes absent from the PDF / 522 pre-2008
  vintages. Spot-checks show the 113 are genuinely absent from the
  primary PDF text (ROC-minted codes NIC-2008-as-published does not
  print) — the census signal working as designed, not a parse gap.
- `findata/Misc/nic_worklist.json` exported: 117 labels, top-3 with
  promote commands, CIN vintage signal per label, 8 no-signal labels
  named. Vault stays writer-owned; nothing auto-writes.

### S5 — gate + first live rebaseline (landed 2026-09-18)

- Arc gate parent(pre-arc)→live: the two v3 pins are exactly the
  undeclared changes (nothing else moved); declared run ACCEPT 81/81.
- **First live rebaseline**: v3 stamped, ref
  `completed.md #246 (nic2008_seed_table S5)`, J-pin frozen at 97 nodes.
- Scoped suites ×2 green: 196 tests (seed/scheme/candidates/worklist/
  concepts/gate/maint/integrity), gate live-vs-live self-check ×2,
  integrity ×2 error-free. Full `make qa` deferred to arc close with
  the operator's go. The +1 industry→nic2008 crosswalk pin lands with
  the first operator promotion (it pins an ACTIVE surface; none exist
  yet).

## Appendix — raw measurement log

| Date | Command | Result | Notes |
|---|---|---|---|
| 2026-09-17 | sqlite RO probes, `memory/research.db` | table above | post-chatter store |
| 2026-09-17 | `rg -l "^industry: \S" findata` | 942 notes | carriers |
| 2026-09-17 | `rg -l "^industry_code: …" findata` | 0 notes | uncoded |
| 2026-09-17 | vintage split query | 124/87 vs 604/282 | D-O6 sizing |
| 2026-09-13 | assessment memo §6.5 | DDL + attested rows | primary sources read then |
