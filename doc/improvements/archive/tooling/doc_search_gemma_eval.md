---
title: "Doc-search gemma eval — does EmbeddingGemma-2 buy recall on the doc corpus that lexical already owns?"
status: executed
filed: "2026-10-12"
executed: "2026-10-13"
completed_md: "386"
area: "helpers/misc/doc_query.py, helpers/maintenance/rebuild_doc_search.py, helpers/misc/embed_eval.py, bench_data/embgemma2/banks/embed_eval_questions.json"
---

# Doc-search gemma eval

**Date:** 2026-10-12 · **Status:** EXECUTED 2026-10-13 ·
**Area:** `helpers/misc/doc_query.py`, `helpers/maintenance/rebuild_doc_search.py`,
`helpers/misc/embed_eval.py`, `bench_data/embgemma2/banks/embed_eval_questions.json`

**Follows:** `script_search_gemma_adoption.md` (completed.md #363 — the
adoption slice pattern reused here), `notes_query_side_levers_trial.md`
(#377 — the pool-compression lesson), `notes_gemma_adoption.md` (#369),
and `doc/local/evaluations/emb_gemma_assessment.md` §4.8/§4.8b (the
2026-10-07 docs-leg negative this trial re-opens on a new basis).

## 1. Problem

Gemma now serves four surfaces — scripts (#363), memory (#367), company
(#368), notes (#369) — while `doc_search` remains granite-hardwired:
no embedder selector, no stamp, 384-d `granite-embedding-97m-r2` over
~3,192 section rows (measured 2026-10-12, live). The gemma
roadmap's "docs leg as decision gate" (#363) did run once: assessment
§4.8 (2026-10-07) re-embedded all 2,987 then-live sections with gemma
Q8@512 through the production sidecar and scored production
`search_docs` — granite hybrid MRR 0.566 vs gemma 0.546 (exact 0.750
vs 0.714), window-controlled (§4.8b), verdict "granite holds". That
negative is RECIPE-BOUND, not final: it ran the production doc-prefix
basis (`title: {t} | text: {sect}\n{content[:4000]}`) under a
file-level MRR gate, and §4.8b tested window length only — the basis
shape was never a variable. The lineage has already watched one
settled-looking verdict flip on vector quality: the notes quick-pool
36/36 tie was pool compression (#377), and the full pool broke gemma's
way (+6). Vector-basis quality is a live variable; this trial asks the
one untested question — does the notes-winning pure-prose basis flip
the §4.8 negative at a recall@5 gate?

The prior is genuinely uncertain, which is what makes this an eval and
not a migration. Every surface where gemma won had an anchor the
vectors could bite: scripts (caller/test structure, ident 0.643 →
1.000), notes (entity edges + header metadata, +11 over shipped),
company (sector/ticker substrate). The doc corpus — procedures,
improvement records, hygiene notes — is heavy in code literals,
commands, paths and section anchors: exactly the substrate where
lexical (FTS identifiers) is strongest and vectors must prove they are
additive rather than decorative. Granite may simply hold here, and that
is a verdict, not a failure.

## 2. Evidence (measured 2026-10-12, this box)

- Live `doc_search`: 3,192 rows, `embed_model =
  granite-embedding-97m-r2`, 384 dims, no `doc_search_info` stamp keys
  beyond the granite label — the pre-#367 memory shape.
- The eval set already exists and is frozen:
  `bench_data/embgemma2/banks/embed_eval_questions.json`, `docs`
  section — 18 questions (exact / variant / semantic), ground truth
  verified against raw corpus content, ANY-OF recall@5.
- The harness already exists: `helpers/misc/embed_eval.py run_docs`
  reports scan vs BM25 vs hybrid recall@5 with the standing criterion
  **hybrid >= BM25 >= scan, no scan regression** (report-only, exits 0 —
  the gate wrapper is D3 below).
- Scale is affordable: 3.2k sections is one-fifth of the notes
  full-pool (17,263 sections @ ~1.07/s ≈ 4.5 h), so a full-corpus gemma
  embed is a ~1 h sidecar run, not an overnight. No npz seed exists
  for docs (notes had one); the trial embeds fresh.
- `doc_query` already fuses hybrid by default with a `--bm25`
  lexical-only leg — the arms to compare exist on the query side
  today; only the vector basis would change.
- §4.8's negative ships reusable artifacts: `bench_data/embgemma2/
  vectors/docs_leg_q8.npz` (2,987×768 cached gemma vectors — the §4.8
  recipe re-scores against the shifted corpus with no re-embed),
  `scripts/docs_leg_score.py`, `outputs/docs_leg_results.txt`. The
  §4.8 recipe becomes a free third reference arm in D2.

## 3. Design

### D1 — granite baseline (floors, not vibes)

Run the frozen `docs` section against the live granite index: BM25,
hybrid-granite, per-category. These numbers become the acceptance
floors the same way #377's 41 became #369's floor-39. No code changes.

### D2 — gemma trial arm (scratch only, never live)

Embed the doc corpus with the gemma basis into a **scratch DB via
`--db`** (the flag every rebuild CLI already takes) — the live index
keeps its granite stamp throughout; no live stamp change rides the
trial (house pattern, #377 §3). Basis recipe follows the established
pattern (a doc-side prefix over the section text, byte-pinned like the
notes pure-prose basis); Q8_0 only (standing operator ruling,
2026-10-07 — Q6 stays out).

Scoring, full-pool only: the #377 lesson is load-bearing here — the
notes quick-pool 36/36 tie was pool compression, and the full pool
broke it (+6 real). At 3.2k sections a full embed is cheap enough
that a quick-pool-only verdict is not acceptable evidence. Arms:
BM25 (shared leg), hybrid-granite, hybrid-gemma, per category.

**Eval-gate bullet** (required: this alters query-visible semantics
iff adopted): frozen `embed_eval_questions.json` `docs` section,
criterion **hybrid-gemma >= hybrid-granite with no exact-query
regression**; the adoption floor is set from the D1 numbers in the
verdict record, never chosen after seeing D2.

### D3 — verdict (decision gate, adoption separate)

Adopt iff D2 clears the gate by a margin worth the second basis to
maintain (notes set the precedent: +11; scripts: ident +0.357). A
tie or a semantic-only win with exact-tier cost is a "granite holds"
verdict — recorded, archived, no slices follow. Adoption itself is
the operator's separate decision with D2's numbers attached.

### D4 — adoption slices (only on a clear win)

The #367 shape, the cheapest analog (single surface, selector +
stamp): `DOC_EMBEDDER`-style selector with granite escape,
`guard_gemma_stamp` write refusal, stamp-keyed query side (the #367
silent-degradation find: a gemma-stamped index under a granite-
hardwired reader empties the cosine leg on dims mismatch — the doc
reader gets the same keying), production bank gate at the recorded
floor, granite rows retained as rollback insurance. Live cutover is
its own change with its own gate run, never bundled with the trial.

### Slice order / independence

D1 first (floors before arms). D2 is the trial. D3 is a verdict
record either way. D4 exists only on a clear win. Nothing here touches
convo (granite by standing ruling), the live stamp, or retention.

## 4. Risks

- **Lexical saturation**: if BM25 already takes exact + most variant
  questions, vectors can only contest semantic — the trial may
  correctly return "granite holds". That is priced in, not a failure
  mode.
- **Basis mismatch**: doc sections mix prose, commands and tables; a
  prose-tuned prefix may underperform on literal-heavy sections. One
  basis recipe, pinned and recorded; basis search is out of scope
  (same discipline as #369).
- **Corpus drift mid-trial**: doc/ edits during the embed change the
  ground truth under the scorer. Trial embeds from a recorded HEAD;
  drift check before scoring (the verdict records the HEAD).
- **Bank staleness depresses the floor**: dsem-01/dsem-02/dvar-04
  missed under BOTH arms in §4.8 (labelled staleness there) and still
  miss under every mode (2026-10-13 probe). Verify those expects
  against the current corpus BEFORE freezing D1 floors — a uniform
  depression cancels in the gemma-vs-granite delta but hides real
  headroom.
- **Second-basis maintenance**: adoption doubles the embed paths to
  maintain (granite escape + gc cohorts). The D3 margin bar exists
  for exactly this reason.

## 5. Summary

| behavior | today | after D3 |
|---|---|---|
| doc vector basis | granite 384-d, hardwired | verdict: gemma or granite-holds |
| doc eval numbers | §4.8 MRR (production basis, negative) | D1 recall@5 floors + §4.8 recipe as free reference arm |
| trial index | — | scratch `--db`, live untouched |
| live stamp | granite | unchanged unless D4 (separate decision) |
| adoption slices | — | only on a clear win (D4, #367 shape) |

**Revisit trigger:** a D2 win inside the margin-of-noise band on a
re-run, or corpus-shape change (e.g. a literal-heavy procedures influx)
that invalidates the D1 floors — re-run D1/D2, never carry old floors
forward.

## 6. D3 verdict (2026-10-13 — gate cleared; adoption ruled)

**Gate: CLEARED.** hy_gemma8k 17/18 vs hy_granite 16/18 — zero
exact-tier regressions, zero regressions of any kind. Operator ruling
(2026-10-13): **adopt** — hybrid serving, @4000 cap ("8k gives no win
for now"). Successor proposal: `doc_search_gemma_adoption.md`.

D1 floors (frozen before arms; settled corpus 3,207 rows @ HEAD
`408c8f90`, corrected bank): hybrid-granite 16/18 (exact 7/7, variant
5/6, semantic 4/5), bm25 14/18; sole all-mode miss dvar-04.

D2 scorecard (5 arms, drift-clean 3,207 rows, all modes hybrid; full
per-question record: `bench_data/embgemma2/outputs/docs_trial2_results.txt`):

| arm | recall@5 | MRR |
|---|---|---|
| bm25 | 14/18 = 0.78 | 0.594 |
| hy_granite (floor) | 16/18 = 0.89 | 0.659 |
| **hy_gemma8k** | **17/18 = 0.94** | **0.716** |
| hy_recipe4k | 17/18 = 0.94 | 0.708 |
| vec8k (solo, diagnostic) | 16/18 = 0.89 | 0.728 |

**Attribution — the finding that outlives the verdict:** the basis-cap
hypothesis is REFUTED by the reference arm — `hy_recipe4k` (§4.8's
exact @4000 recipe re-scored on today's corpus) ties gemma8k; the 8000
cap is worth +0.008 MRR. §4.8's negative was corpus drift + stale bank
labels, not the recipe. The +1 is dsem-05 (granite rank 20 → gemma 4);
tail ranks broadly better (dsem-01: 4 → 1). vec8k posts the best MRR
of all arms (0.728) — gemma vectors rank better solo than fused; RRF
with bm25 dilutes a strong vector leg (informs any future serving
tuning; hybrid remains the gate shape).

Margin vs §3's bar: +1/18 and +0.057 MRR — far under the notes
precedent (+11) that set the "worth a second basis" bar; no exact
cost, so not the automatic "granite holds" case. The operator weighed
the thin margin against the tail-quality pattern and ruled adoption.

Artifacts: `bench_data/embgemma2/vectors/docs_trial2_gemma8k.npz` +
`docs_trial2_recipe4k.npz` (3,207 rows each, per-row content-hash keys
— the portability fix vs §4.8's corpus-hash keying),
`outputs/docs_d1_floors_2026-10-13.txt`,
`scripts/docs_trial2_{build,score,snapshot}.py`. Trial dbs stayed
ephemeral (TMPDIR); the content-keyed npzs are the durable form.
