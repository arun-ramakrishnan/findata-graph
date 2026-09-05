---
title: "Full re-embed readiness — per-text call shape, runtime A/B, and the granite@2k whole-note bake-off"
status: executed
filed: "2026-09-06"
executed: "2026-09-06"
completed_md: "210"
area: "helpers/core/local_embedder.py, helpers/bench/, note/docs/company embed indexers"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Full re-embed readiness — per-text call shape, runtime A/B, and the granite@2k whole-note bake-off

**Date:** 2026-09-06 · **Status:** EXECUTED 2026-09-06 ·
**Area:** local_embedder call shape, llama.cpp runtime, embed benches,
note-search model decision (follows ../tooling/note_section_search.md §7)

## 1. Motivation

note_section_search S1–S3 landed (14,500 section rows, eval 1.00 held,
latency p50 122ms). The parallelization re-question that followed
produced three wrong turns and one real discovery before settling (full
saga: ../tooling/note_section_search.md appendix): the pinned spawn pool does NOT
beat warm serial under desktop load, but the production BATCH embed call
is ~4.5× slower than per-text on section-length texts. A full re-embed
at 48m (S3 actual) is really ~20m of compute plus ~28m of call-shape
waste — so before sanctioning any re-embed, the user directed a
first-principles revisit (five questions, answered in §3 with measured
data) and this proposal stages the work they gate.

## 2. Evidence (measured 2026-09-06, this box; embed_pool_probe, 96 real section texts, warm legs, load 4–8)

| Shape | bge @512 | granite97m @2k | Verdict |
|---|---|---|---|
| production batch serial (`embed_documents`) | 2.74/s | — | dead shape for section texts |
| per-text serial, cold first leg | 4.24/s | 2.97–3.84/s | artifact (~2.6× slow; warmup-guarded) |
| per-text serial, warm | **12.31/s** | **10.06–13.22/s** (3 runs) | the fast shape |
| pinned pool ×4 (1T/core) | 10.83/s | 7.79–10.26/s | no win under load — pool closed |
| pinned pool ×2 | 5.91/s | 5.46–6.20/s | dominated |
| RSS (load + 1 embed) | +118MB | +431MB | fine serial (14GB box, ~6 avail) |

Batch-vs-per-text warm-vs-warm = 12.31/2.74 ≈ **4.5×** (the "4.7×" in
note_section_search's appendix used the cold 2.62 leg). Suspected
mechanism: llama.cpp `embed()` accumulates every input into one decode,
so heterogeneous section lengths pay near-worst-case compute per text;
the old trial result "batching is a null lever" held only because whole
notes were uniform-length. Runtime pin: llama-cpp-python 0.3.35. Rates
are window-dependent (±5× with desktop load) — every number above is
same-window; the granite legs ran 3× to bound drift.

## 3. Design — the five first-principles questions, answered

1. **Latest bert llama.cpp?** SETTLED (S0 executed 2026-09-06, REJECTED):
   llama-cpp-python@master (newer vendored llama.cpp) measured PARITY
   with the pinned 0.3.35 on bge per-text serial (interleaved ×3, warm
   legs 0.92–1.06×) with identical compiled CPU flags — no kernel path
   added for this Skylake/bge-Q8_0 shape. Keep the pin. Reopen if a
   llama.cpp release lands with a claimed encoder/FA win AND the probe
   shows ≥1.3×.
2. **Avoid production batch?** Yes — S1 rewrites
   `local_embedder.embed_documents` to per-text (contract preserved:
   order, L2-normalise, fail-loud; embed_cache and all four consumers
   unaffected). Confirmed by a micro A/B at rebuild-realistic mix, 3
   runs, before landing.
3. **Use granite's 32k ctx in the pipelines?** No. (a) Encoder
   attention is quadratic: one 32k-token forward ≈ 256× a 2k forward's
   attention cost on CPU — the ~10% of notes beyond 2k would dominate
   runtime. (b) One mean-pooled vector per 30k-token document is signal
   soup — this corpus just proved small units win (sectioning moved
   deep recall 11→13). (c) 2k already covers ~90% of whole notes. The
   right-sized use is the whole-note bake-off at 2k (S2); depth stays
   with section vectors.
4. **Revisit models more capable than bge?** The throughput errors
   never touched the QUALITY data (docs surface regressed for every
   candidate; search/vss at ceiling) — what they invalidated is cost
   bars derived from contaminated rates. User-directed (2026-09-06):
   the three models eliminated on TIMING (nomic, MiniLM, granite)
   re-enter S2 for a clean serial per-text re-measure AND their
   first-ever deep-probe quality leg; gte stays eliminated on quality
   (15/18 docs, no timing dispute). "noline" is not a known embedding
   model (it is a Rust line editor) — if a specific 2026 model was
   meant, name it and it enters as a screened S4 candidate. Leap-class
   models (bge-m3 / e5-large / Qwen3-embed class) share a 1024-dim
   schema break (DIM, vec0 mirror, company_embeddings, 2.7× matrix) and
   the corpus pattern that fit beat size here — S4 stages at most ONE,
   only if S2 fails.
5. **+400MB RSS?** Accepted — serial loads the model once (granite
   431MB transient on a 14GB box). The old ~1.7GB figure was the pool
   artifact (4 workers × 431MB). RSS only re-enters the matrix as
   standing app.py residency IF S2 swaps the serving model.

Slices (independently landable, order matters):

- **S0 — runtime A/B**: side-venv master build; interleaved per-text
  probe legs vs 0.3.35, bge + granite, 3 runs; adopt/reject at ≥1.3×.
- **S1 — per-text call shape**: `embed_documents` rewrite + micro A/B;
  benefits note/docs/company/script indexers alike; lands regardless of
  S2 outcome (pure win, no model change).
- **S2 — candidate whole-note bake-off (user-expanded 2026-09-06)**:
  granite97m@2k, nomic@2k (two-sided prefixes), MiniLM@512, plus a bge
  whole-note control — the three timing-eliminated models re-admitted
  because their eliminations rode contaminated rates. Vehicle:
  `helpers/bench/note_deep_probe_candidates.py` (the 15 deep-probe
  questions, same legacy corpus + fusion recipe, serial per-text,
  per-model /tmp checkpoints); controls = after_bm25 13/15,
  after_hybrid (sectioned bge) 11/15, before_hybrid 11/15. Any model
  whose deep-probe hybrid ≥ 13 then faces the embed_eval surfaces via
  `embed_model_trial`. GGUFs re-fetched to models/; delete at arc close
  per the not-committed rule.
- **S3 — decision gate + optional re-embed**: swap to granite ONLY if
  search 1.00 held AND docs ≥ bge same-window AND deep-probe hybrid
  beats sectioned-bge's 11/15 by closing ≥1 fact-query gap AND notes
  ingest ≤ 3× bge per-text. Otherwise stay bge sections; S1 still
  landed. A sanctioned re-embed runs serial per-text (never pool,
  never batch).
- **S4 (conditional) — one leap candidate**: only if S2 fails to close
  the deep gap; screen runtime via embed_pool_probe first, quality via
  the trial harness; 1024-dim schema break priced before any commitment.
- **S5 (RUNNING 2026-09-06) — catastrophe gate, cross-surface**: granite
  docs + companies legs via `embed_model_trial` vs bge's recorded
  quality (docs 18/18, vss 12/12, neighbors 5/10; quality recall is
  window-independent). User decision (2026-09-06): **granite swap
  DECIDED** unless this gate shows catastrophe — granite's future-facing
  advantages (2k+ trained ctx, multilingual) outweigh small recorded
  deficits; bge-small STAYS as backup (rollback = revert constants +
  rebuild; bge vectors remain in the (text, model)-keyed cache).
- **S6 — swap execution + sanctioned full re-embed** (on S5 pass): make
  granite the global embed model via the REAL rebuild machinery — never
  column surgery (embedding column + embed_matrix + vec0 mirror rewrite
  together). Mechanics: local_embedder model constants (GGUF path,
  label `granite-embedding-97m-r2`, n_ctx 2048, NO query prefix),
  query_embedder switch, db_meta stamp, then serial per-text
  (`EMBED_POOL_WORKERS=1`) rebuilds: note_search (14.5k, ~25m),
  doc_search + script_search + company_embeddings (~1.8k rows, ~5m).
  **Raise `_SECTION_EMBED_CAP` 4000 → 8000 chars** so the 2k window is
  actually usable on the 1,450 long sections (4000 chars ≈ 1k tokens
  truncates below granite's capacity; whole sections, H2 boundaries
  UNCHANGED — composition is the proven winner, only the window grows).
  Post-swap: embed_eval all surfaces + the 15-question probe + latency
  (expect ≈121ms p50, granite query embed measured equal in the A/B).
  Rollback: revert constants, rebuild — cache returns bge vectors.

**Swap-decision Q&A (user, 2026-09-06):** (1) chunking stays H2 — the
section composition won every measurement; only the char cap rises so
granite's window sees whole long sections. (2) ctx window: granite runs
2048 (the tested shape); 32k stays a non-goal (quadratic CPU). (3) bge
stays in models/ as the rollback path. (4) pooling: NO — granite pool×4
measured 7.79–10.26/s vs serial 10.06–13.22/s twice, and 4 pinned
granite workers would hold ~1.7GB; serial per-text everywhere.

**S7 — companies base experiment (added 2026-09-06, user-directed).**
The neighbors regression (granite 3/10 vs bge 5/10, same-sector ≥3/5)
has a mechanism hypothesis: the production base
`f"{name}. {sector}. {content[:5000]}"` was DESIGNED around bge's
512-token window — truncation at ~2,000 chars acted as an accidental
feature selector weighting the sector token and opening descriptors;
granite's 2k-token window reads the full diversified business
description, and similarity follows business-adjacency over the sector
label (the miss pattern shows it: NVIDIA → AMD/Palantir/Adobe). Legs,
all in `helpers/bench/company_neighbors_base_probe.py` (production
semantics: cosine top-5 over all embedded companies, sector from
entities, ≥3/5 threshold):

- **live-full** — vectors straight from `company_embeddings` (granite,
  full base): harness validation, must reproduce 3/10 on the eval's 10
  seeds.
- **bge-full (cache)** — the OLD production bases from the bge cache
  cohort (embeddings.py rides the shared cache, so these should be
  100% hits): extends the 5/10 bge baseline to the 30-seed readout
  without embedding anything. Skipped if coverage is short.
- **granite-trunc2000** — `f"{name}. {sector}. {content[:2000]}"`:
  the bge-window equivalent under granite.
- **granite-structured** — `f"{name}. {sector}. {lead}"`, lead = body
  up to the first markdown heading / blank-line paragraph, capped
  1,500 chars: the opening-concentration hypothesis, explicit.

Readout on BOTH the 10 recorded seeds (comparability) and an extended
30-seed set (10 recorded + 20 deterministic stride picks — the n=10
delta is 2 hits, too thin to tune against). Adopt a base only if it
beats live-full on the 30-seed readout; landing = base change in
`_get_company_text` + `--clear`/`--model` reapply + `make
graph-rebuild`, and the vss leg (12/12) MUST re-verify —
`company_embeddings` feeds both consumers. Per-surface model routing
stays deferred (item 6) unless both bases fail.

**S7 measured (2026-09-06, harness validated on both anchors: live-full
reproduced 3/10, bge-cache reproduced 5/10, 100% cohort coverage):**

| Leg | 10-seed | 30-seed |
|---|---|---|
| bge-full (cache cohort) | 5/10 | 17/30 |
| granite live-full (production base) | 3/10 | 13/30 |
| granite trunc2000 | 3/10 | 13/30 |
| granite structured (overview body) | **6/10** | **17/30** |

Verdicts: (1) the WINDOW hypothesis is dead — trunc2000 is identical to
live-full, so granite's regression is not about how much text it sees;
(2) the CONTENT hypothesis wins — the first `##` section (Company
Overview prose, median 395 chars) carries the sector-coherent signal
and the long tail dilutes it; overview-base granite matches bge's
17/30 and beats live-full by +4 → **ADOPT**. Drift lesson: the
structured leg's first construction (cut at first heading) yielded
EMPTY bodies — notes open with an H1 + metadata line — so the base
degenerated to "Name. Sector." and scored a meaningless 28/30 via
sector-token string-matching; the probe now refuses degenerate bases
(body-empty guard) and the leg was re-measured on the fixed extraction
(first `##` section body, 1,500-char cap).

## 4. Acceptance criteria & shakedown

1. S0: written A/B verdict with 3 interleaved runs per leg; production
   venv diff is a version pin change or nothing.
2. S1: micro A/B (rebuild-realistic mix, 3 runs) shows ≥3× over batch;
   `embed_eval` surfaces unchanged; all indexer tests green.
3. S2: full trial-harness quality table (search/docs/vss/neighbors +
   15-question probe, recall@5) for granite@2k whole notes vs
   sectioned bge, same window, serial legs.
4. Any re-embed: warm incremental contract holds (0 hits/0 misses on
   no-op); `--check` FRESH; eval baseline 1.00 reproduced after.

| Measured outcome | Before | After (measured 2026-09-06) |
|---|---|---|
| bge 14.5k-section re-embed | 48m (S3 actual, batch serial) | ≈20m projected at 11.9–12.6/s per-text |
| granite whole-note corpus (1,242 notes) | 9–15m projected | **364s ≈ 6m measured** (3.4/s fresh, serial) |
| index-side embed call | batch 2.74/s | per-text **11.9–12.6/s in prod venv** (S1 landed) |
| standing runtime | llama-cpp-python 0.3.35 | **0.3.35 stays** — master parity 0.92–1.06×, identical CPU flags (S0 rejected) |
| notes search hybrid (27q) | bge 1.00 / bm25 0.93 | granite **1.00 / 0.93** — parity (post-swap, live) |
| deep probe hybrid (15q) | bge 11/15 | granite **14/15**; bm25 13/15; granite whole-note 11/15 |
| docs hybrid (18q) | bge 18/18 | granite **17/18** (dvar-05 run-log query; sandbox legs read 15–16/18) |
| vss top-1 (12q) | 12/12 | **12/12** — parity |
| neighbors same-sector ≥3/5 | bge 5/10 | **6/10** (overview base, S7; live-full [:5000] read 3/10) |
| hybrid endpoint p50/p95 | 122/150ms (S3, bge) | **155/193ms** (post-swap live; the 97M query cost, +27%) |

**Full re-embed: SANCTIONED and EXECUTED 2026-09-06.** The model-swap
trigger fired — granite passed the S2b + endpoint A/B gates and the user
decided the swap (§3 Q&A). The chain ran serial per-text in one pass:
note_search 14,500 / doc_search 981 / script_search 326 rows, all
granite-stamped, 0 cache hits (the model label is the bust, by design);
companies via `--clear` + `--model granite-embedding-97m-r2` (1,079 rows,
uniform — confirmed by `--stats`), then `make graph-rebuild` to refresh
the DuckDB cache. Cache cohorts after: bge 20,322 (rollback path,
preserved) + granite 16,874. The paragraph below is the STANDING
doctrine for any future re-embed:

The live index is FRESH with
eval at baseline; existing vectors remain valid (per-text differs from
batch by ≤1.3e-3/component, below eval resolution), and every
incremental rebuild already embeds new texts through the per-text
shape. Note the cache consequence: a full re-embed of UNCHANGED texts
would 100% cache-hit the old vectors and change nothing — a
numerics-only regeneration would need a model-label bust, which is not
warranted. Run a full re-embed only on a real trigger: a model swap,
a corpus-wide restructure (e.g. a future sub-H2 chunking arc), or a
cache invalidation. When triggered:
`EMBED_POOL_WORKERS=1 .venv/bin/python3 helpers/maintenance/rebuild_note_search.py`
(serial per-text, ~20m at measured rates).

## 5. Risks

- **Window contamination** (the day's recurring failure) — every rate
  claim same-window, 3 runs, warmup-guarded probe, preflight clean
  state; no verdict enters the durable record unreplicated.
- **Side-venv build cost/churn** — 15–30m compile on the loaded 4-core
  box; master may change GGUF compat (the cstr token_type_count trap) —
  the probe catches load failures immediately.
- **Granite regressions at decision time** — docs 16–17/18 vs bge 18/18,
   neighbors 3/10 vs 5/10; post-landing S7 closed neighbors to 6/10
   (overview base) and docs settled at 17/18. Search/vss parity held
   throughout; deep +3 carried the swap.
- **Leap-class schema break** — 1024-dim rebuild cascade priced in S4
  before any commitment, never mid-arc.

## 6. Non-goals

- 32k-ctx pipelines (§3.3) and any sub-H2 chunking (parent proposal).
- Parallel/pooled ingest (settled: no win under load).
- docs/companies surfaces (already section-chunked/capped by design).
- GPU float formats (BF16/FP8 doctrine, embed_model_eval.txt).
- Model quality re-litigation on cost grounds alone — quality tables
  stand; only cost bars were re-derived.

## 6b. Deferred / future-facing work (reference list, 2026-09-06)

Unresolved by decision or scope, in rough priority order — none block
the swap:

1. **Multilingual queries (granite's unmeasured edge)** — granite is
   multilingual; Hinglish/Hindi note queries were never tested (bge is
   English-only). Probe idea: 10-15 translated deep-probe questions
   through the swapped index; if they retrieve, that's a new capability
   surface, not just parity.
2. **Semantic-neighbors performance under granite (user-flagged
   2026-09-06)** — **RESOLVED by S7** (same day): the [:5000] full-note
   base read 3/10 vs bge's 5/10; the overview base (first `##` section,
   landed in `_get_company_text`) reads 6/10 live and 17/30 on the
   30-seed probe — bge parity restored, window ruled out (trunc2000 ≡
   full). Residual: re-check after the corpus grows; per-surface model
   routing stays item 6; reopen only with a concrete user-facing
   neighbors failure.
3. **Sub-H2 chunking for the residual tail** — ~80 heading-less notes +
   long single-heading sections still pool coarsely (the parent
   proposal's declared non-goal); revisit only with a concrete failing
   question that sectioning at 2k ctx doesn't catch.
4. **vec0 vs the 4096 cap at 14.5k rows** — the mirror exists but can
   never serve whole-corpus KNN; the f32 matrix is the permanent serving
   leg. Future options: chunked KNN, or accept matrix-only (current
   shape, 12ms exact).
5. **Hybrid tuning backlog** (from note_section_search §7 follow-ups):
   bm25() column weighting (title/section_title ×2.0), cosine as
   co-equal retriever with candidate union — the two fact-queries bge
   lost are now granite's to lose; re-probe after swap before tuning.
6. **Per-surface model routing architecture** — if a future surface
   needs a different model (e.g. bge stays better for companies):
   requires per-surface query-embedder routing + both models resident
   (~+550MB); surfaces can NEVER run mismatched models (cross-model
   cosine = the 6/27 A/B garbage).
7. **`embed_matrix.from_note_search` cleanup** — legacy pre-sectioning
   reader (bare file_path ids, dead for the composite-key sectioned
   index); delete or fix when touched next.
8. **llama.cpp upgrade reopen trigger** — a release claiming encoder
   wins + probe ≥1.3× (S0 rejected at parity on identical CPU flags).
9. **32k ctx** — settled non-goal for CPU (quadratic); reopen only on a
   GPU/local-accelerator change, and even then mean-pooling over 30k
   tokens is signal soup — prefer more sections.
10. **S4 leap-class screen** — only if a labeled-set revision shows bge/
    granite failing; 1024-dim schema break priced first (DIM, vec0,
    company_embeddings, 2.7× matrix).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-06 | embed_pool_probe (production batch + pool legs) | serial 2.62–2.74/s; pool×4 7.14/s | "pool 2.7×" — SUPERSEDED, call-shape artifact |
| 2026-09-06 | embed_pool_probe --gguf bge --ctx 512 | warm per-text 12.31/s; pool×4 10.83/s | cold leg 4.24/s = warmup artifact |
| 2026-09-06 | embed_pool_probe --gguf granite97m --ctx 2048 (×3) | warm per-text 10.06/10.28/13.22/s; pool×4 7.79–10.26/s | first legs 2.97–3.84/s cold |
| 2026-09-06 | embed_pool_probe --gguf nomic --ctx 2048 (×3) | warm per-text 3.16/3.73/4.46/s; pool×4 2.84–3.07/s | ~3.3–3.8× bge (12.31/s) — marginal FAIL vs 3× bar on sections; worse whole-note (token-scaled ~0.7/s vs bge's 512-truncated ~12/s). **ELIMINATED** (user, 2026-09-06: over bar in both shapes + docs 0.94→0.78 stands; neighbors +2 the sole positive, insufficient) |
| 2026-09-06 | embed_pool_probe --gguf minilm6 --ctx 512 (×3) | warm per-text 23.1–25.5/s; pool×4 18.6–22.8/s | ~2× FASTER than bge — timing elimination OVERTURNED; **ELIMINATED anyway** (user, 2026-09-06): speed-only edge on a non-binding axis; docs 15/18, cosine-only 4/15 (worst measured), 256-token native window |
| 2026-09-06 | note_deep_probe_candidates.py bge (control) | hybrid 11/15, cosine-only 6/15, 0 fresh (spill) | reproduces recorded before_hybrid 11/15 exactly — harness validated |
| 2026-09-06 | note_deep_probe_candidates.py minilm6@512 | hybrid 11/15, cosine-only 4/15, 181s | ties bge only via the BM25 page; pure-semantic BELOW bge |
| 2026-09-06 | note_deep_probe_candidates.py granite97m@2k | hybrid 11/15, cosine-only 5/15, 364s | **S2 gate FAILED**: 2k window (~92% of avg note seen) does NOT beat bge's 512-truncated whole-note vectors — long-context buys no deep-content recall on this corpus |
| 2026-09-06 | note_deep_probe_candidates.py nomic@2k | hybrid 11/15, cosine-only 5/15, 989s (1.26/s) | same 11/15; whole-note rate confirms cost elimination; misses the same 2 fact queries everyone misses |
| 2026-09-06 | S1 EXECUTED: embed_documents per-text + 152 tests + warm --check | vectors differ from batch ≤1.3e-3/component (decode-shape numerics — ~2 orders below eval resolution; cached batch-era and fresh per-text mix fine); search hybrid **1.00**/bm25 0.93 + vss 12/12 HELD; note_search --check FRESH (14,500, 0 embeds) | **S1 LANDED** — all indexers now ride the ~4.5× shape |
| 2026-09-06 | embed_eval docs surface (post-S1) | exact 0.86 / variant 0.50, "served modes ['scan']" | NOT an S1 regression: this arc's own doc/ edits made doc_search STALE and the #184 hash-first gate correctly refused hybrid; re-verify at arc close after `make search-fresh` |
| 2026-09-06 | S0 EXECUTED: side-venv llama-cpp-python@master vs prod 0.3.35, interleaved ×3, bge per-text | warm serial: prod 11.9–12.6/s, master 10.9–13.3/s = **parity (0.92–1.06×)**; identical CPU flag lines (AVX2/F16C/FMA/LLAMAFILE/OPENMP/REPACK); run-1 prod legs contaminated by post-build window (excluded) | **S0 REJECTED** — master's newer vendored llama.cpp adds no kernel path for this Skylake/bge-Q8_0 shape; keep the 0.3.35 pin, production venv untouched |
| 2026-09-06 | S2b EXECUTED: note_deep_probe_candidates --sectioned (bge control from live vectors; granite 14,488 fresh embeds, 1,482s ≈ 9.8/s) | **deep hybrid: granite 14/15 vs bge 11/15** (granite also beats bm25's 13/15; misses only tariff-ladder; bge's 4 misses = the informed-cosine fact-query failures); search hybrid BOTH 27/27; cosine-only deep: bge 10 > granite 9 | **granite sectioned WINS deep content at the eval ceiling** — user challenge vindicated; granite's weaker standalone cosine FUSES better with BM25 on the page. Control reproduced endpoint numbers exactly (11/15, 27/27) → production-comparable. Full factual A/B (sandbox research.db, real endpoint, both models end-to-end) is the sanctioned next step |
| 2026-09-06 | note_ab_granite.py first run (leg B bug, DISCARDED) | leg A prod-bge: search 27/27, deep 11/15, p50 162ms ✓; leg B: search **6/27**, deep 9/15, p50 808ms | driver bug, NOT a granite verdict: only the sandbox `embedding` column was swapped — the endpoint's cosine leg served granite QUERIES against the BGE production f32 matrix (`_flat_knn_map` → `EmbedMatrixStore`, the S3 materialized matrix) = cross-model garbage. **Load-bearing discovery: a model swap must rewrite the embedding column AND the embed_matrix AND the vec0 mirror — exactly what the rebuild machinery does; column surgery is not a swap.** Also fixed: per-request query-model reload (+600ms/query); residual found: `embed_matrix.from_note_search` is a legacy pre-sectioning reader (bare file_path ids — would fail the composite staleness gate; dead code for the sectioned index, cleanup candidate) |
| 2026-09-06 | note_ab_granite.py v2 (fixed: sandbox matrix w/ composite ids + memoized query model) | **leg A bge: 27/27, 11/15 deep, p50 121/p95 164ms; leg B granite: 27/27, 14/15 deep, p50 121/p95 150ms** (bm25 13/15 both; sandbox rewrite 14,500 cache hits) | **NOTES-SURFACE VERDICT: granite sectioned ≥ bge on every endpoint-measured axis — search ceiling held, deep +3 (fact-gap closed), latency identical.** S3 notes-surface gate PASSES (search 1.00 ✓, deep > 11/15 by 3 ✓, ingest 25m ≈ bge 20m ✓). Remaining before any swap: docs + companies surfaces same-window (granite's old docs 16/18 vs bge 18/18, neighbors 3/10 vs 5/10) AND the architecture decision — surfaces CANNOT run mismatched models (cross-model cosine = the 6/27 garbage), so the swap is either global (needs granite ≥ bge everywhere) or notes-only (needs per-surface query-embedder routing, ~+550MB resident for both models) |
| 2026-09-06 | `pip show llama-cpp-python` / `free -g` | 0.3.35; 14GB total ≈6 avail | runtime pin + RSS headroom |
| 2026-09-06 | embed_runtime_bench --rss (prior arc) | bge +118MB; granite +431MB | single-load serial figures |
| 2026-09-06 | S5 catastrophe gate — granite trial legs, docs/vss/neighbors vs bge live baselines | docs 15/18 (bge 18/18), vss **12/12** (parity), neighbors 3/10 (bge 5/10) | nothing catastrophic; deltas accepted with the notes-surface +3 — **swap DECIDED** (§3 Q&A) |
| 2026-09-06 | S6 EXECUTED: local_embedder constants → granite (sha-pinned mykor Q8_0, DIM 384, ctx 2048, QUERY_PREFIX "" — symmetric), `_SECTION_EMBED_CAP` 4000→8000, 6 bge-hardcoded test assertions → dynamic MODEL_ID (152 green); chained serial rebuild note→doc→script | note_search **14,500** / doc_search **981** / script_search **326** rows, all granite-stamped, 0 cache hits (model label = the bust); db_meta note_embed_model=granite-embedding-97m-r2; composite-id f32 matrix 14,500 rows; bge cache cohort 20,322 PRESERVED | **SWAP LANDED** — rollback = revert constants + rebuild (cache serves bge vectors again) |
| 2026-09-06 | companies: embeddings.py `--clear` then `--model granite-embedding-97m-r2` (unbuffered, launch-verified) + `make graph-rebuild` | **1,079 rows uniform granite** (dim 384, `--stats`); DuckDB graph cache refreshed from SQLite | `--maint` correctly REFUSED auto-upgrade across model labels (by-design gate — clear+apply is the documented path); graph-rebuild required so vss never reads the stale bge DuckDB vectors |
| 2026-09-06 | POST-SWAP BATTERY: embed_eval all surfaces + note_deep_probe.py --json (probe scratch re-keyed per MODEL_ID, provenance-unknown bge-era scratch deleted; 1,242 legacy bases fresh granite ≈ whole-note leg) | search **27/27** hybrid (bm25 0.93 — parity); deep **14/15** hybrid / 13/15 bm25 / 11/15 granite whole-note; docs **17/18** (miss: dvar-05); vss **12/12**; neighbors 3/10; endpoint p50 155/p95 193ms; KNN map 19.6ms @ 14,500 rows | **FINAL LIVE VERDICT: search+vss parity, deep +3, docs −1, neighbors −2 (deferred item 2), latency +27% — the 97M query cost.** Latency is the one axis above the A/B projection (121/150 interleaved; 155/193 live, same 15×3 loop) — model size at query time, not a serving regression |
| 2026-09-06 | GGUF disposition (user-sanctioned cleanup): `rm models/all-MiniLM-L6-v2-q8_0-llukas.gguf models/nomic-embed-text-v1.5.Q8_0.gguf` | models/ now: bge-small 36MB (rollback) + granite 110MB (production, sha re-verified vs pin); 165MB freed | bench harnesses (embed_model_trial, note_deep_probe_candidates) docstrings updated: eliminated legs fail fast on missing file, verdicts point here — no re-download without a new proposal; embed_model_trial's stale "granite FAILED" verdict line corrected to the S2b/A/B outcome |
| 2026-09-06 | S7 EXECUTED: company_neighbors_base_probe.py (4 legs, harness validated: live-full reproduced 3/10 AND bge-cache reproduced 5/10, 100% cohort coverage; 30-seed readout = 10 recorded + 20 deterministic) | bge-full 17/30 · granite live-full 13/30 · **granite trunc2000 13/30** · **granite overview 17/30** (6/10 on the recorded set) | window hypothesis DEAD (trunc2000 ≡ live-full — the regression is model geometry, not window); content hypothesis WINS — landed: `_get_company_text` now emits name + sector + first `##` section body (cap 1,500; `_overview_body`), repopulated 1,079 rows (~2m at 8.4/s vs ~10m on the full base) + graph-rebuild. **Landing gates: vss 12/12 held, neighbors 6/10 live (probe predicted 6/10 exactly).** Degenerate-construction lesson: cut-at-first-heading gave empty bodies ("Name. Sector." string-matching, void 28/30) — probe now refuses body-empty bases |

**S2 measured outcome (2026-09-06, all four legs same-window, control
validated)**: every model — bge whole-note control, MiniLM@512,
granite@2k, nomic@2k — lands at exactly hybrid 11/15; cosine-only
separates them: bge 6 > granite = nomic 5 > MiniLM 4. The hybrid page
is carried by BM25 (13/15 recorded); no candidate closes the 2
fact-query gap, no candidate beats bge's truncated vectors on pure
semantics. The S3 swap gate fails for the WHOLE-NOTE shape of every
candidate on deep content (plus the standing docs-surface regressions).
MiniLM and nomic ELIMINATED at user direction. **Granite is NOT
eliminated** (user challenge 2026-09-06, correct): the bake-off closed
only the whole-note path — granite under the SAME section composition
as bge was never tested, and section composition is precisely what this
corpus rewards. S2b (added): sectioned legs via
`note_deep_probe_candidates.py --sectioned` — production-faithful
semantics (AND-first+OR-fill, note-dedup BM25 page, note-best cosine
collapse, RRF k=60), bge control read straight from live index vectors
(validates the harness against endpoint numbers deep 11/15 / search
1.00), granite embedding all 14.5k sections (~20–25m serial). If
sectioned granite is competitive there, the follow-up is a FULL
FACTUAL A/B: sandbox research.db copy, real rebuild machinery, both
models re-embedded end-to-end, real evals — no more shape simulations.
Deep-content recall is structurally BM25's on fact queries and
sectioning's on semantics; window size alone moves nothing (that
conclusion stands — it is about whole notes, not sections).
