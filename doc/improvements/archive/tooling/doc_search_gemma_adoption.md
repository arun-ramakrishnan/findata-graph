---
title: "Move the doc-search leg to EmbeddingGemma-2 — hybrid serving, pure-prose@4000 basis (eval gate #386 cleared)"
status: executed
filed: "2026-10-13"
executed: "2026-10-13"
completed_md: "#388"
area: "helpers/maintenance/rebuild_doc_search.py (basis :306, cap :124, embedder+stamp paths), helpers/core/gemma_embedder.py (query side), app.py _docs_index_search :1347, helpers/misc/doc_query.py"
---

# Doc-search gemma adoption

**Date:** 2026-10-13 · **Status:** PROPOSED ·
**Area:** `helpers/maintenance/rebuild_doc_search.py`,
`helpers/core/gemma_embedder.py`, `app.py::_docs_index_search`,
`helpers/misc/doc_query.py`

**Follows:** `doc_search_gemma_eval.md` (archive/tooling, completed.md
**#386** — the cleared gate this executes), `script_search_gemma_adoption.md`
(#363 — the selector/stamp/guard shape reused here), `notes_gemma_adoption.md`
(#369 — full-pool discipline), and the #367 silent-degradation find (the
read-path keying this proposal mirrors for docs). Durable related-data
reference: `doc/local/evaluations/emb_gemma_assessment.md` **Appendix B**
— the re-trial verdict plus the artifact index (vectors, probe outputs,
scripts); this proposal and that appendix cross-reference each other.

## 1. Decision being executed

The eval gate cleared (2026-10-13): hybrid-gemma **17/18** recall@5,
MRR 0.716 vs hybrid-granite 16/18, MRR 0.659 — **zero exact-tier
regressions** on the frozen 18-q bank, drift-clean 3,207-row corpus.
The operator ruled adoption same day: **hybrid serving, @4000 cap**
("8k gives no win for now" — the reference arm measured the 8000 cap
at +0.008 MRR, noise). This proposal moves the doc leg from
granite-hardwired 384-d to gemma 512-d and pins the recipe the trial
actually scored. It does NOT touch convo (granite by standing ruling),
retention, or any other leg.

Attribution carried from #386 (shapes the recipe pin): the win is the
embedder on today's corpus, not the basis shape — §4.8's exact @4000
pure-prose recipe ties the @8k arm. So adoption takes the **cheaper,
already-shipped cap** and the **proven basis form**, byte-pinned.

## 2. Evidence (trial record, #386)

| arm | recall@5 | MRR | exact | variant | semantic |
|---|---|---|---|---|---|
| bm25 | 14/18 = 0.78 | 0.594 | 6/7 | 3/6 | 5/5 |
| hy_granite (floor) | 16/18 = 0.89 | 0.659 | 7/7 | 5/6 | 4/5 |
| hy_gemma8k | 17/18 = 0.94 | 0.716 | 7/7 | 5/6 | 5/5 |
| hy_recipe4k (**adoption recipe**) | 17/18 = 0.94 | 0.708 | 7/7 | 5/6 | 5/5 |
| vec8k (solo, diagnostic) | 16/18 = 0.89 | 0.728 | 7/7 | 5/6 | 4/5 |

- Tail pattern behind the +1: dsem-05 granite rank 20 → gemma 4;
  dsem-01 4 → 1; dex-02 2 → 1; dex-03 3 → 2. Full per-question record:
  `bench_data/embgemma2/outputs/docs_trial2_results.txt`.
- Durable vectors exist (content-hash keyed, 3,207 rows):
  `bench_data/embgemma2/vectors/docs_trial2_recipe4k.npz` is the
  adoption recipe's trial-grade snapshot — it SEEDS the cutover
  (S1 rider below), and cross-checks the post-cutover index.
- Sidecar rates measured this box (2026-10-13): 1.35–1.65 s/row @8k,
  1.46–1.85 s/row @4k fresh → 3,207-row full-pool re-embed ≈ **1–1.5 h**
  on the tuned server (`:8732`, `-c 16384 -ub 16384` — the `-ub`
  floor is mandatory for >2048-token bases, §4.6 lesson).

## 3. Design

### S1 — build path: basis + embedder + stamp (one patch, one recipe)

- **Basis** (`rebuild_doc_search.py:306`): replace the newline-form
  prefix with the trial-pinned pure-prose recipe, byte-exact:
  `f"title: {title or 'none'} | text: {section_title}\n{content[:4000]}"`
  `_EMBED_BODY_CAP` stays **4000** (`:124`) — measured no-win for 8k.
  The newline-form-with-gemma cell was never tested; one recipe, pinned
  and recorded, no basis search (#369 discipline).
- **Embedder**: section embeds via `gemma_embedder.embed_document`
  (raw 768 → MRL 512 + L2 inside; stored dims 512). Query side:
  `embed_query(task="search")` — the exact prose contract the trial
  scored. Both are part of the recipe pin.
- **Stamp**: write `embed_model = "embeddinggemma-2-q8_512"`,
  `embed_dims = 512` into `doc_search_info` on every rebuild (the trial
  already exercised this stamp path). Rebuilds with the sidecar down
  fail loudly — no silent granite fallback into a gemma-stamped db.
- **Granite escape** (#363 shape): a `DOC_EMBEDDER=granite` env selector
  keeps the old path buildable for rollback diagnostics; it must refuse
  to write into an index stamped gemma (stamp/selector mismatch =
  hard error, never a silent rewrite).
- **Embed-cache keying audit**: cache keys must bind model + basis form
  + cap, not content alone — the trial's content-hash port only worked
  because keys separated. Test: cache a row under granite, flip the
  stamp, assert the next rebuild re-embeds instead of serving the
  granite vector. This is the one silent-catastrophe class in the
  whole move; it gets a dedicated test, not a code-review glance.
- **S1 rider — cache seed (notes #369 pattern, collapses S3's window)**:
  after the key audit lands, seed `embed_cache` from
  `docs_trial2_recipe4k.npz` (3,207 vectors on the byte-exact adoption
  recipe — trial basis and S1's pinned basis are verbatim-equal, which
  is what makes the seed legal). The S3 full-pool rebuild then serves
  ~3,207/3,207 cache hits and the sidecar window shrinks from 1–1.5 h
  to minutes (notes precedent: 17,263/17,263 hits at cutover). The
  unseeded path (fresh corpus rows only) keeps the measured rates.

### S2 — read path: stamp-keyed query leg (the #367 mirror)

`search_docs` (`rebuild_doc_search.py:782`) / `app.py:1361`: load the
pool filtered by the **stamped** dims; a QueryVector whose
model/dims disagree with `doc_search_info` drops the cosine leg with a
`NOTE` (bm25-only service) — never a dims-mismatched dot product, never
a silent empty-leg result presented as hybrid. #367's find was exactly
this failure shape on memory; docs inherits the guard before cutover,
not after the first incident. Contract tests: gemma QV vs granite stamp
⇒ bm25+NOTE; granite QV vs gemma stamp ⇒ bm25+NOTE; match ⇒ hybrid.

### S3 — cutover apply (sidecar up, corpus frozen)

1. **Rollback snapshot FIRST**: extract the live granite vectors to
   `bench_data/embgemma2/vectors/docs_pre_gemma_granite.npz`
   (content-hash keyed, same format as the trial snapshots) — the live
   db is the last granite record; the trial npzs are gemma-only.
2. Corpus frozen for the embed window: drift guard (live contents ==
   row-for-row at gate time, exit 3 on drift — the trial's guard,
   reused verbatim).
3. Full-pool re-embed, 3,207 rows — **expected ~all cache hits after the
   S1 seed** (minutes); the 1–1.5 h figure stands only for unseeded
   rows. Checkpointed every 250 rows with the resume fix (checkpoint
   re-applies on resume — the savez-tmp gotcha and the resume bug are
   both known shapes; smoke must cover the checkpoint path too).
4. Stamp lands with the final transaction; `doc_index_stale` re-check.

### S4 — acceptance gates (floors recorded BEFORE the apply)

- **Frozen-bank reproduction**: post-apply live index through
  `embed_eval.py run_docs` + the trial scorer's hybrid arm must hit
  **≥ 16/18, exact 7/7, MRR ≥ 0.69** (recipe4k measured 0.708 on the
  identical recipe+corpus; −0.02 noise allowance for sidecar
  nondeterminism). 17/18 is the expectation, 16 the floor; below floor
  = investigate, never hand-tune to pass.
- **Consumer smoke**: `doc_query` (hybrid + `--bm25` leg),
  `/api/docs/search`, TUI docs lane — each serves `hybrid` mode, none
  degraded; standing criterion hybrid ≥ bm25 ≥ scan holds.
- Gates run against the live post-apply index, not the trial scratch.

### S5 — docs & bookkeeping

`doc/procedures/search.md` embedder mentions; assessment §4.9 pointer
(if not already added); completed.md entry; README live-list flip.
`doc_index_stale_mtime_gate` (the other live proposal) is untouched by
this one — different functions, but both patches edit
`rebuild_doc_search.py`; land in either order, no rebase coupling.

### S6 — field enrichment: probed (2026-10-13); combo → fast-follow S7

The notes cutover's winning levers were indexed FIELD columns (ticker,
edge_context), not embedder quality alone — so before cutting over, the
docs analogs were probed on the trial scratch (zero re-embedding;
vectors ported verbatim, vector leg invariant, deltas purely lexical):
meta (frontmatter title/status/area), links (outgoing md links +
Follows/completed.md refs, cap 900), trail (h1), combo. Harness =
mechanical search_docs replica (RRF k=60, per-file cap 2, schema-length
bm25 weights); parity anchored (base arm reproduces D1 exactly).
Record: `bench_data/embgemma2/outputs/docs_enrich_probe_2026-10-13.txt`,
`bench_data/embgemma2/scripts/docs_probe_enrich.py`.

| arm | granite hy | gemma hy | granite MRR | gemma MRR |
|---|---|---|---|---|
| base | 16/18 | 17/18 | 0.659 | 0.708 |
| meta | 16/18 | 17/18 | 0.663 | 0.699 |
| links | 16/18 | 17/18 | 0.631 | 0.708 |
| trail | 16/18 | 17/18 | 0.654 | 0.706 |
| combo | 16/18 | 17/18 | 0.673 | 0.741 |

Verdict: **recall@5 does not move on either base in any arm** — the one
joint miss (dvar-04) is immune in all arms (granite rank 7, gemma
absent): pending.md carries no inbound anchor text, distinct h1, or
distinct frontmatter carrying its query tokens. But the combo MRR gain
is REAL, not re-ranking noise: per-question rank diff
(`docs_probe_rankdiff.py`) shows it is exactly two questions promoted
one rank each, identically on both bases — dex-03 rank 2→**1** and
dvar-02 4→3 on gemma (total +0.033), dex-03 3→2 and dvar-02 4→3 on
granite (+0.014); 16/18 questions untouched, zero regressions.
Links-only *hurts* granite MRR (0.631) — the combo's meta column is
what carries the gain, links alone is negative. Meta's bm25-exact +1
(6/7 → 7/7) helps only the degraded bm25-only mode.

**Ruling: not bundled with the cutover, taken as a fast-follow slice
(S7).** The gain is real but rides on 2/18 questions and costs a
production schema migration (3 FTS columns + extraction code + weight
handling) — bundling it with the embedder move couples two variables
into one gate, against the one-change-one-gate discipline. Sequencing
is cheap: post-cutover rebuilds serve vectors from the seeded
embed_cache (minutes), so the S7 migration + bank re-run costs little
more than the gate itself. S7 gate: recall@5 ≥ 16/18, exact 7/7, MRR
≥ 0.72 on the live index (combo measured 0.741 on the trial corpus;
floor set below expectation, above the un-enriched 0.708), plus the
standard consumer smoke. Re-open sooner only if doc/ grows structured
relational density (the #386 revisit trigger's sibling).

### S7 — field enrichment (fast-follow, after cutover; gated)

The combo schema — `meta` (frontmatter title/status/area, no key
prefixes), `links` (outgoing md links + Follows/see-doc/completed.md
refs, cap 900), `trail` (h1) as indexed FTS columns appended after
`embedding` (bm25 weights +1.0 each; production's hardcoded 6-col
weights get schema-length handling), extraction in the rebuild path,
migration via drop-recreate (cheap post-seed) — measured on the trial
scratch: recall unchanged (17/18), MRR 0.708 → 0.741 (dex-03 2→1,
dvar-02 4→3; both bases agree, zero regressions). Gate: S4 floors plus
MRR ≥ 0.72 on live. Probe scripts:
`bench_data/embgemma2/scripts/docs_probe_enrich.py`,
`docs_probe_rankdiff.py`; record:
`bench_data/embgemma2/outputs/docs_enrich_probe_2026-10-13.txt`.

## 4. Risks

- **Sidecar dependency**: every doc rebuild now needs `:8732` up —
  operationally identical to the notes leg (17k-row full pools run
  this way); failure mode is loud (S1 refusal), not silent.
- **Cache collision** (the catastrophe class): granite vector served
  under a gemma key → silent quality collapse that still "works".
  Mitigated in S1 by key binding + dedicated test.
- **Corpus drift mid-embed**: the +1 lived in tail ranks; a doc/ edit
  during the window invalidates the gate comparison. Drift guard exits
  hard (S3).
- **Thin margin**: +1/18 is inside no precedent's comfort zone; the
  granite snapshot (S3) keeps the rollback a revert-plus-re-embed, and
  the `DOC_EMBEDDER` escape keeps granite one env var away. The #386
  revisit trigger stands: a literal-heavy procedures influx that moves
  the floors re-opens the question.
- **Q8_0 only** (standing ruling 2026-10-07): no quantization
  experiments ride this move.

## 5. Summary

| behavior | today | after |
|---|---|---|
| doc vector basis | granite 384-d, newline-form @4000, hardwired | gemma 512-d, pure-prose @4000, byte-pinned |
| query leg | granite `embed_query` | gemma `embed_query(task="search")`, stamp-keyed |
| mismatch behavior | #367-shaped hazard | bm25 + NOTE, tested |
| rollback | — | granite snapshot npz + `DOC_EMBEDDER=granite` escape |
| rebuild runtime | local granite | +sidecar, ~1–1.5 h full-pool, checkpointed |
| frozen bank | 16/18 hybrid floor | ≥16/18, exact 7/7, MRR ≥0.69 on live |

**Revisit trigger:** any S4 gate miss, or a corpus-shape change that
moves the bank floors — re-run the bank, never carry old numbers
forward. Serving-shape tuning (vector-primary / RRF weights — vec8k's
0.728 solo MRR is the headroom) is explicitly OUT of scope here.

## 6. Execution record (2026-10-13, the gemma_doc_srch patch)

S1–S5 landed + cutover executed same-day, gated live:

- **S1**: `resolve_doc_embedder()` (#363 shape — gemma default,
  `DOC_EMBEDDER=granite` escape) + `guard_gemma_stamp` on the write
  path; `_doc_basis()` pins the recipe byte-exact (`title: {t} |
  text: {section}\n{content[:4000]}`), legacy basis preserved verbatim
  for rollback cache keys; cache keys already bind
  `(sha256(basis_text), model)` — the audit test pins both axes.
  **S1 rider executed**: `docs_seed_cache.py` seeded 3,204 rows from
  the trial npz (400-sample bit-exact roundtrip check).
- **S2**: `query_embedder(model_label)` stamp-aware (gemma →
  `task="search"`); `search_docs` drops a mismatched QueryVector with a
  surfaced `note`, re-embeds stamp-locally, degrades bm25-only with the
  reason when no space resolves. Contract matrix tested (mismatch ⇒
  hybrid-or-bm25 + note, match ⇒ clean hybrid, sidecar-down ⇒ bm25).
- **S3**: rollback snapshot FIRST
  (`vectors/docs_pre_gemma_granite.npz`, 3,218 granite vectors,
  content-hash keyed); full-pool cutover rebuild — **3,204 cache hits /
  14 misses** (the drifted rows), 3,218/3,218 vectors 512-d, zero
  granite remnants, stamp `embeddinggemma-2-q8_512`/512, `stale:
  False`. Minutes, not the 1–1.5 h unseeded figure.
- **S4**: bank on the live index — **recall 17/18 (exact 7/7,
  semantic 5/5), MRR 0.708** vs floors (16/18, 7/7, 0.69) — the trial
  reproduced arm-for-arm (bm25 0.594 matches too). Consumer smoke:
  `doc_query` hybrid + `--bm25` legs ✓; `/api/docs/search` exercised by
  `run_docs` in-process, all 18 served `hybrid` ✓; TUI lane rides the
  same `search_docs` contract.
- **S5**: `doc/procedures/search.md` doc-leg setup rewritten
  (sidecar-first, refusal semantics, escape); assessment Appendix B.1
  cutover record; gate output archived
  (`outputs/docs_post_cutover_gate_2026-10-13.txt`).

Rollback = revert + restore the granite npz + `DOC_EMBEDDER=granite`.
S7 (combo enrichment, MRR ≥ 0.72 gate) stands as the fast-follow.

### S7 — executed 2026-10-13 (completed.md #389)

Landed same day as the cutover: `meta`/`links`/`trail` INDEXED FTS
columns (drop-recreate migration via `_migrate_schema`; re-embed fully
cache-served — 3,220 hits / 0 misses, minutes not an hour), extraction
byte-exact from the probe harness
(`bench_data/embgemma2/scripts/docs_probe_enrich.py`), schema-length
bm25 weights (extras at 1.0, legacy 6-col vector unchanged). Gate on the
live index: recall 17/18 (floor ≥16, exact 7/7 held, semantic 5/5),
**MRR hybrid 0.759** (gate ≥ 0.72; pre-S7 0.708, probe predicted 0.741)
— dex-03 rank 1, dvar-02 rank 2, dvar-04 the lone recall miss. bm25
recall also rose (0.78 → 0.83). Contract-test updates encoded the
post-hybrid world: gibberish queries serve dense-vector neighbors by
design (no-match pinned on the bm25 path), hits may match via
enrichment columns a content-only snippet cannot show, and
stamp-matched QVs serve hybrid with zero embed calls.
