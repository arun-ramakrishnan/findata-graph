---
title: "Trial gemma on company_embeddings behind a three-way labeled bank"
status: executed
filed: "2026-10-08"
executed: "2026-10-08"
completed_md: "368"
area: "helpers/graph/embeddings + eval banks"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Trial gemma on company_embeddings behind a three-way labeled bank

**Date:** 2026-10-08 · **Status:** EXECUTED — ADOPTED (completed.md #368) ·
**Area:** helpers/graph/embeddings + eval banks

**Follows:** `script_search_gemma_adoption.md` (completed.md entry 363 —
the per-surface pattern), `memory_search_gemma_migration.md`
(completed.md entry 367), and
`doc/local/evaluations/emb_gemma_assessment.md` §6 (census +
full-migration timing; company = 1,193 overview bases, ~25 min per arm).

## 1. Motivation

The surface-by-surface gemma migration is underway (scripts done,
memory done). company_embeddings is the remaining small surface — but
it feeds **semantic peer edges** in the graph, a silent-quality
regression surface: a worse model changes "companies like this one"
without any user-visible ranking to notice. So the swap is gated behind
a labeled trial, not taste.

The evidence tension worth resolving by measurement: the docs negative
(assessment §4.8) was measured on LONG bases; company bases are SHORT
overview bodies (name + sector + first-section body, cap ~1,500) — the
regime where gemma measured best (hard code bank 1.000 vs 0.793;
multilingual 11/12 vs 4/12). Unmeasured, plausibly gemma-favorable.
The trial settles it with data either way.

Also fixed in the same slice:
`build_semantic_peer_edges` still labels its output
`embeddings:bge-small:v1` — stale branding since the granite swap.

## 2. The three-way bank (S1)

Seed: `helpers/misc/embed_eval_questions.json` already carries 22
company-anchored questions in exactly the two formats needed — 12 `vss`
(Yahoo-style longName lookups: the name-variant slice) and 10
`neighbors` (pass rule: >=3 of top-5 share the sector: the
peer-similarity slice) — with ground truth verified against raw corpus
content, not the engine under test. Extend to ~35-40:

- **+8-12 relation-fact questions** (parent company, subsidiaries, group
  structure) — expects DERIVED from the graph (`parent_of` /
  `subsidiary_of` row-sets via the `ontology_eval_gate.answer_question`
  derivation pattern), never hand-authored.
- **+5-8 peer questions** for sectors the seed's 10 don't cover.

Pre-registered expectation: a **three-way split** — name variants are
vector-friendly for both models; sector/peer similarity is the actual
gemma-vs-granite contest; relation facts likely FAIL for both dense
arms (explicit relations are lexical, not semantic neighborhoods) —
which is the identifier-router lesson re-derived and argues for routing
relation queries to the graph edges (S5).

## 3. Arms + metric (S2/S3)

- Scratch company index TWICE: granite (the live basis recipe) and
  gemma (the gemma prefix basis over the same title/sector/body, cap
  unchanged) — 1,193 rows, ~25 min per arm on the sidecar.
- Score both through the bank's own rules: `vss` recall@5 (ANY-OF),
  `neighbors` sector-sharing rule, relation-fact recall (expect =
  graph row-set containment).
- The LIVE granite index stays untouched throughout; the decision
  compares scratch arms only.

## 4. Decision rule (S4)

- gemma adopts ONLY if the name-variant and peer slices show **no
  regression** vs granite (vss recall and the neighbors rule at parity
  or better). Relation-fact failures for both arms do not block
  adoption — they route to the graph (S5).
- On adopt: flip the company stamp via the standard per-surface pattern
  (selector + `guard_gemma_stamp` in `helpers/graph/embeddings.py` +
  gemma prefix basis + embed-gc `text_by_model`), re-embed live
  (~25 min), relabel the peer edges (kill the `bge-small:v1` branding),
  then `make graph-rebuild` + `make snapshot` (the standing
  company-embeddings rewrite rule).
- On hold: granite stands; record the numbers in the assessment and
  close the question.

## 5. Relation routing (S5, optional successor slice)

If relation facts fail both arms as predicted: extend the router
doctrine — company-surface relation queries route to the graph edges,
mirroring the script identifier router. NOT in this trial's scope;
the S1 bank measurements are what justify it.

## 6. Non-goals / context

- **docs stays granite** — the measured negative is final until a
  successor model generation exists (the docs questions skew exact;
  granite+bm25 hybrid owns that class).
- **convo stays granite** — 19-22 h embed for the smallest expected
  gain; real queries are exact-identifier-shaped (bm25-carried); no
  eval bank exists (author one first if ever revisited).
- **The concall endgame**: company entities are the JOIN KEY for the
  future transcript surface (issuer -> ticker -> transcript), and
  cross-surface joins are model-consistent only under a shared stamp.
  When the transcript surface lands with its own model, re-run this
  trial with THAT model — company's final model choice pairs with the
  transcript decision, not with this trial.

## 7. Acceptance criteria

1. Bank committed (`helpers/misc/company_eval_questions.json`, or an
   extension of `embed_eval_questions.json`): >=35 questions across the
   three slices, expects verified (greps / graph derivation).
2. Both scratch arms scored; numbers recorded in the execution record
   (and the assessment when adopted or finally held).
3. Decision recorded either way; on adopt, the standard per-surface
   acceptance set applies: stamp flip, bank-style live gate,
   embed-gc retention of the granite rollback rows, `make graph-rebuild`
   + `make snapshot`, peer-edge relabel.

## 8. Execution record (2026-10-08) — ADOPTED on the facet-expanded record

Bank: 41 questions (12 vss seed-verbatim + 17 neighbors/peers incl. 7
new-sector pairs + 12 relation with expects DERIVED from `subsidiary_of`
row-sets), all expects verified in-cohort. Scratch arms: granite via the
production `cached_embed_batch` path (1,191/1,192 cache hits); gemma
2.7/s, ~9.5 min. Numbers (granite vs gemma): vss 12/12 vs 12/12 (MRR
1.000 both, @1 too), neighbors+peers 10 vs 9, relation-parent 6/6 both
(gemma's Accelya hit is top-1 vs granite's top-5), relation-subsidiaries
3/6 vs **5/6**. Facet expansion on the operator's instruction before
concluding: full ledger gemma 32/41 vs 31/41, 18/41 questions judge
differently; the marquee peers flip (`5paisa Capital`) is granite's
label-pure-but-business-wrong AMC wall (0.90-0.91) vs gemma's true
broker/platform cohort; counterweights kept (granite top-10 sector mass
on Amber/Akzo/AU, peers same@10 44 vs 27); gemma's space is CALIBRATED
(0.74-0.85 spread vs granite's 0.87-0.93 compression — peer-edge weights
discriminate); hybrid-with-header-bm25 at the entity level EVALUATED AND
DECLINED (vss saturated, peers queryless, note-level hybrid already
exists in note_search; revisit on real longName drift).

**Decision: the pre-registered rule returned HOLD by one peers question;
the operator's ruling — gemma's semantic analysis of the UNNAMED edges
is what this surface ships — overrides it, with the counterweights
recorded in the assessment (§6.3). ADOPTED.**

Executed the standard per-surface acceptance set the same day:
`resolve_company_embedder` (`COMPANY_EMBEDDER=granite` escape) +
`guard_gemma_stamp` in `populate_local` (gemma-stamped table refuses the
granite fallback; maint degrades to WARNING), gemma basis via the shared
`_gemma_basis` over the unchanged title/sector/body (granite lane
byte-identical), stamp-keyed query side in `vss_index._pick_embedder`
(search task; sidecar down -> no match), embed-gc `text_db_by_model`
company recipe. Live: 1,192 rows gemma/512 (~10 min; the 1,193rd was a
ghost), live bank gate == scratch arm, gc 1,192 referenced / 0 dead /
1,196 granite retained, `graph-rebuild` + `snapshot` run, peer-edge
relabel (the `embeddings:bge-small:v1` constant replaced by the live
stamp). FOUND ALONG THE WAY: the rebuild fast path's embeddings
fingerprint called `length()` on a BLOB (no such DuckDB function) and
had silently stamped `absent` since the 1.5.6 bump — fast-path rebuilds
never saw company-embedding changes; fixed with `octet_length`
(query.py). Tests: TestCompanyGemmaMigration (7) + legacy lanes pinned
granite; 67 targeted green. Raw record:
`doc/local/evaluations/emb_gemma_assessment.md` §6.3.
