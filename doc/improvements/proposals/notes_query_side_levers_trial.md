---
title: "Notes query-side levers before any model swap — bm25 boosts + header/edge enrichment, then granite-vs-gemma hybrids on a derive-seeded bank"
status: proposed
filed: "2026-10-08"
executed: null
completed_md: null
area: "helpers/misc/note_query + rebuild_note_search + eval banks"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Notes query-side levers before any model swap

**Date:** 2026-10-08 · **Status:** PROPOSED (execution in flight same day) ·
**Area:** helpers/misc/note_query + rebuild_note_search + eval banks

**Follows:** `company_embeddings_gemma_trial.md` (completed.md #368 —
the facets-over-simple-scores lesson and the tie-break doctrine),
`memory_search_gemma_migration.md` (#367), and the assessment record
`doc/local/evaluations/emb_gemma_assessment.md` §6.3.

## 1. Motivation

Three operator directives (2026-10-08, after the company verdict flip):

1. **The context axis is measured-dead** — the native-window grid
   (assessment §4.8) showed granite ranks identical at 4,000/8,000 chars
   and gemma@24k ≈ gemma@4k; H2 sectioning (median 1,066 chars) already
   bounds the base. The notes lever is the QUERY side (hybrid design),
   not longer windows.
2. **The eval bar is hard questions, not MRR parity with granite** —
   lookup slices saturate (company vss 12/12 for BOTH models) and decide
   nothing; verdicts turn on hard slices. Tie-break doctrine: even at
   equal scores, pick the model that answers the toughest non-intuitive
   queries, because hard-query noise propagates into the `derive_*` legs
   as noise edges the operator personally triages (the `5paisa` AMC-wall
   vs broker-cohort calibration finding is the mechanism, §6.3).
3. **Concall endgame**: Indian concall/analyst notes are heavily
   Hinglish and not yet ingested — multilingual weight in any notes
   verdict is STRATEGIC, not incidental. (convo itself stays granite —
   reaffirmed.)

Against that, the notes bm25 leg is untouched terrain: `bm25(note_search)`
runs EQUAL-WEIGHT over `doc_type/title/sector/content/section_title`, and
the index carries no ticker and no graph facts — relational queries score
only via literal token co-occurrence in bodies. Two named levers, never
tried: **column boosts** and **header/edge enrichment**.

## 2. Levers (S1/S2)

- **A — boosts (query-side only, zero rebuild)**: FTS5
  `bm25(note_search, w...)` weights over the LIVE index read-only;
  small grid (title/section_title/sector upweighted vs content).
- **B — enrichment (scratch FTS)**: `bench_data/embgemma2/
  notes_enriched.db` — the live rows plus two indexed columns:
  `ticker` (entities.ticker; currently unfindable) and `edge_context`
  (the company's ACCEPTED `graph_edges` relations rendered as text —
  parents/subsidiaries/competitors/suppliers/customers/group, capped
  400 chars). The operator's "edges" lever: relational queries become
  literal-token hits in every section of the company's note.

The live index is never written; both levers are scratch-measurable.

## 3. Bank (S3)

`bench_data/embgemma2/banks/notes_bank.json` — 53 questions, 8 tiers,
expects DERIVED from accepted `graph_edges` row-sets (the
triage-survived derive output), never hand-authored:
rel_parent 8, rel_subs 6, competes 8, supplier 6, customer 5,
same_group 6, self 6 (control), ml_hinglish 8 (OVERLAP-ORACLE tier —
directional only, never decision-grade). An answer = any section of an
expected company's note (per-section keys, mirroring `note_query`).
Banks are archived under `bench_data/embgemma2/banks/` per operator
directive (tracked twins in `helpers/misc/` where production consumers
exist).

## 4. Arms + decision rule (S4)

bm25 arms first (levers in isolation): baseline → boosted → enriched →
enriched+boosted. Then hybrids on the best query config: the SHIPPED
granite matrix vs a gemma scratch arm (pool subset: 444 companies /
3,762 sections — granite all cache hits, gemma ~30 min), pure-vector
legs for diagnosis, fusion = the production RRF (k=60, equal).

Per-tier pass@5 + MRR; decision weights the hard tiers (relations,
competes, supplier/customer, ml) over the self control. The company
trial's pattern applies verbatim: record the pre-registered narrow rule
AND the facet-expanded reading; the operator ruling arbitrates if they
diverge.

## 5. Non-goals

- **No live stamp change in this trial** — notes stay granite/384
  regardless of arm outcome; adoption is a separate decision (full-pool
  gemma embed ≈ 5-6 h) that this trial's numbers gate.
- **No derive-pipeline changes** — the trial consumes accepted edges as
  eval substrate; it does not touch extraction or triage.
- **convo stays granite** (reaffirmed; no recall bank exists).

## 6. Acceptance criteria

1. Bank + levers archived under `bench_data/embgemma2/` with provenance
   (build scripts + this proposal).
2. Every lever family measured with per-tier deltas (boost grid,
   enrichment) — including the levers that DON'T move (recorded).
3. Hybrid granite-vs-gemma verdict on the improved query side recorded
   in the assessment, both readings (narrow + facet-expanded) stated.
4. Production change (note_query weights / index enrichment) only if a
   lever proves out, following the standard per-surface pattern.

## 7. Execution log + shape decisions (in flight, 2026-10-08)

Measured so far (53-question bank, full-corpus bm25 legs):

- **Enrichment is the lever**: bm25 15/53 → 25/53 with `edge_context`
  (+ticker), all of the gain relational — supplier 0->5, customer 0->4,
  competes 0->1, rel_subs 3->4. The `edges` lever works exactly as
  framed: relational queries become literal-token hits.
- **Column boosts are a measured NULL**: both weight grids byte-identical
  to baseline on every tier (weights reorder overlapping candidates, they
  do not change which rows match). Recorded as a dead lever for this
  bank; dropped from the definitive shape.
- **Cap sensitivity**: edge_context at 400 chars buried `group:` behind
  long `subsidiaries:` lists (same_group 0/6); 900 chars recovered it.
  Composition/order is a live knob — see open decisions.
- Pure-vector granite (444-company pool): 24/53 with a COMPLEMENTARY
  profile (rel_parent 7/8 vs bm25's 3-4) — the hybrid premise holds.

**Shape decisions ruled during the trial (operator, 2026-10-08):**

1. edge_context composition: cap raised 400 -> 900; segment ORDER and
   per-relation caps stay OPEN until the definitive run.
2. Vector-basis enrichment (edge_context in the EMBED text, not just the
   bm25 field): deferred — a separate hypothesis, only after the
   bm25-side verdict.
3. Bank: current 8 tiers stand; the ml tier stays oracle-grade.
4. Boosts: dropped (null result above).
5. NO long arms before the shape is final (operator): the hybrid
   granite-vs-gemma comparison runs on a QUICK pool first (bank-named +
   expect companies, 102 companies / 981 sections — granite all cache
   hits, gemma ~23 min); the full-pool overnight run (17,263 sections,
   ~5-6 h) is the definitive arm ONLY after this shape is ratified.

**Hybrid verdict (quick pool, both vector legs, 2026-10-08):**
pass@5 totals of 53 — `bm25_base` 15, `bm25_enriched` 25, boosts NULL
(identical rows everywhere), `pv_granite` 26, `pv_gemma` 28 (identical
981-section pool — gemma +2), `hy_granite_base` 24 (the un-enriched
lexical leg DRAGS RRF below its own vector leg), and
**`hy_gemma_enriched` 32/53 — the best arm, +17 over the shipped
configuration (bm25_base+granite hybrid = 24)**. Per-tier for the
winner vs the granite hybrid: rel_parent 5 vs 7 (granite keeps the
prose-carried parent slice), rel_subs 5 vs 4, competes 2 vs 1,
supplier 5 vs 1, customer 4 vs 2, same_group 2 vs 0, ml 3 vs 3. The
levers and the model COMPOSE: neither enriched bm25 alone (25) nor
gemma vectors alone (28) reaches the composed 32.

Caveats (deliberate): vector legs search the 981-section quick pool
while bm25 searches all 17,263 rows — hybrid absolutes are depressed,
but granite-vs-gemma shares the identical bm25 leg and pool, so the
model comparison is fair; ml tier is oracle-grade and tiny; boosts
remain a null. Full-pool definitive run + composition/order tuning of
edge_context + the vector-basis-enrichment hypothesis are the
follow-ups this verdict gates — no live stamp change rides this trial.

**Shape closed 2026-10-08 (both open knobs measured, quick pool):**

- **Composition grid — FLAT above the cap threshold**: natural/900 = 25,
  rare-first/900 = 26, rare-first/700/per-rel-6 = 26 (the ±1 is oracle-tier
  noise). The 400->900 fix was the real threshold; rule: **rare-first @
  cap 900** for truncation safety, stop tuning.
- **Vector-basis enrichment REJECTED**: appending edge_context to the
  EMBED text (both legs re-embedded on the quick pool) hurts every arm —
  pv_granite 26 -> 22, pv_gemma 28 -> 25, composed hybrid 32 -> 30.
  Relation boilerplate in the basis homogenizes sections and dilutes the
  semantic signal. **Edges belong in the LEXICAL field only; the vector
  leg stays pure prose** — the §6.4 complement doctrine, sharpened.

**Extended bank (item 1, operator directive "harder questions"):** 75
questions / 12 tiers — added `compare` 8 (same-sector pairs,
pass_rule=BOTH notes in top-5), `rel_jv` 6, `rel_acquired` 3 (graph-
derived row-sets; `invested_in` excluded — 2 in-cohort endpoints, dead
substrate), `ml_hard` 5 (harder Hinglish: multi-entity, comparative,
temporal — oracle-grade). Quick-pool arms topped up (1,360 sections /
75 queries). Results hold and grow: `bm25_base` 20, `bm25_enriched` 31
(lever +11), boosts still null, pv granite 36 = pv gemma 36 (tie
overall, complementary tiers — gemma owns rel_parent 8/8 + rel_subs
5/6, granite owns compare 6/8 + same_group 3/6), and
**`hy_gemma_enriched` 41/75 (+boost 42) vs the shipped-config granite
hybrid 31/75**. Tier notes: `compare` is the hardest tier (both
hybrids 3/8; lexical 0/8 — the BOTH-notes bar is a model/structure
discriminator); `ml_hard` is unanswered by every arm (0-1/5) — the
honest frontier, oracle-graded; `rel_jv` leans enriched-lexical (3-4/6
vs granite-vector 1/6). `invested_in` excluded for dead substrate;
enriched bases cost ~0.3-0.4/s vs 0.7/s plain (embed-cost datapoint for
the full-pool run).

Enrichment generalization (operator question): the lever is
entity-corpus-specific — notes are the only surface where graph edges +
header metadata join naturally (doc/memory/convo have no entity anchor;
script's analog would be caller/test context from the call graph — a
later, separate idea; company/vss header enrichment already declined —
vss saturated). master_query inherits any production notes change
through the `note_query` client: one integration point.

**Definitive full-pool run (2026-10-08 17:32→22:00) — verdict
replicates, tie breaks, acceptance criteria met.** Gemma embed:
17,263 sections @ avg 1.07/s (~4h28m, checkpoints every 2000); granite
full = cache hits. Full-corpus scoring (`notes_score.py --pool full`,
extended 75-q bank): `bm25_base` 20, `bm25_enriched` 31 (lever +11
replicates), boosts byte-NULL a third time, `pv_granite` 23 /
`pv_gemma` **29** (the quick-pool 36/36 tie was pool compression —
gemma +6 real), `hy_granite_base` 30, **`hy_gemma_enriched` 41/75
(+boost 41)** — the quick-pool 41 replicates exactly. `ml_hard`
0-1/5 → 4/5 (quick pool lacked the answer sections — scope artifact);
`compare` stays hardest (lexical 0/8, hybrids 2v1 — the only tier the
shipped config wins). Narrow (hard-tier-weighted total) and
facet-expanded (per-tier sweep, sole loss `compare` 1v2) readings
AGREE: **`hy_gemma_enriched` +11 over shipped** — no arbitration
needed. Non-goals hold: no live stamp change; adoption is the
operator's separate decision, now gated with definitive numbers.
Scores: `notes_scores_full.json`; verdict recorded in the assessment
§6.4.
