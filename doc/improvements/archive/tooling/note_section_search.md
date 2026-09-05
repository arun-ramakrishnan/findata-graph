---
title: "Note section search — per-H2-section vectors for the note_search surface"
status: executed
filed: "2026-09-06"
executed: "2026-09-06"
completed_md: "209"
area: "search index (helpers/maintenance/rebuild_note_search.py) + app.py hybrid fusion"
---

# Note section search — per-H2-section vectors for the note_search surface

**Date:** 2026-09-06 · **Status:** EXECUTED 2026-09-06 ·
**Area:** `rebuild_note_search.py` (schema + text bases + vec0 mirror) ·
`app.py` `_hybrid_search_results` (composite keys + note dedup) · tests

## 1. Motivation

The 2026-09-06 token-cap measurement (doc/local/embed_model_eval.txt
"Token-cap bite measurement", reproducible via
`helpers/bench/embed_token_cap.py`) showed the notes surface embeds only
the HEAD of each note: bge's 512-token cap (trained rope; llama.cpp has
no encoder rope-scaling) truncates 79% of notes, median note is 1,210
tokens, token-mass retained 39%. Content past ~token 512 is invisible to
the cosine leg of hybrid search — the 1.00 search ceiling measures
title/sector-led queries, not deep content. User direction 2026-09-06:
section notes with bge-small first, take the baseline, then bake off
against granite @ 2k whole-note vectors (phase 2, separate decision).

Scope honesty (user expectation corrected in-thread): this arc does NOT
touch the company_embeddings surface — the neighbors 5/10 eval score
belongs to that surface and will not move.

## 2. Evidence (measured 2026-09-06, this box)

| Configuration | Result | Verdict |
|---|---|---|
| notes whole-doc @512 cap (status quo) | 21% fit / 39% token-mass / search hybrid 1.00 | baseline |
| docs H2-sectioned @512 (status quo sibling) | 64% fit / 70% mass / docs hybrid 1.00 | the pattern works |
| granite whole-note @2k (projected) | covers ~90% of notes; 35–50m re-embed; known docs/neighbors regressions | phase-2 comparator |
| note H2-section census | 1,246 notes → **~14,520 section rows (11.7×)**; 80 notes have no `##` headings | row-growth cost, measured |

Section census method: `##`-prefix count per frontmatter-stripped
body via `helpers/core/frontmatter.split_frontmatter_with_title`
(2026-09-06, /tmp script — folded into S1's implementation).

## 3. Design

Apply the docs-surface pattern (rebuild_doc_search `_split_sections`,
2026-08 lineage) to findata notes. Slices:

- **S1 — index shape**: `note_search` gains `section_title` + `anchor`
  columns; row identity becomes `(file_path, anchor)`; one row per H2
  section plus the preamble. Text base per row:
  `f"{title}\n{sector}\n{section_title}\n{content[:4000]}"` (mirrors
  docs' cap; sections are heading-dense so virtually all fit 512). The
  preamble chunk carries the H1/title so title-typed queries behave as
  today. 80 heading-less notes yield one row each (status quo for
  them). vec0 mirror + embed_store keys move to the composite key
  (embed_store is content-hash keyed — section texts get fresh cache
  entries automatically; no invalidation needed).
- **S2 — fusion dedup**: `knn_similarities`/`_flat_knn_map` keys become
  `f"{file_path}#{anchor}"`; `_scored_rows`/`_cosine_positions` join on
  the same composite. After RRF fusion and BEFORE pagination, collapse
  section rows to notes (best fused score per `file_path`; snippet +
  section_title from the winning row). `/api/search` response keys stay
  note-shaped (API compat). The BM25 candidate page (`limit+offset`)
  may need a wider fetch — sections make the 25-row page note-sparse;
  measure in S3 and widen only if recall drops.
- **S3 — baseline + probe**: re-run `embed_eval search` (must hold
  1.00); author ~15 deep-content questions whose expect-notes' answers
  live past token 512 (measure: today's baseline is expected to miss
  most — that GAP is the arc's before-number); tests for schema,
  dedup, and no-section-note behavior; KNN latency measured at 14.5k
  rows (A1 accepted ~7ms at 1.2k; linear scan → expect ~10×, must stay
  interactive).

Phase 2 (NOT this arc): granite @ 2k whole-note vectors vs the
sectioned bge baseline on the same deep-content questions, via
`helpers/bench/embed_model_trial.py` (serial shape; granite re-fetch
source in its docstring). The probe itself is rerunnable:
`helpers/bench/note_deep_probe.py` + the 15 questions in
`helpers/bench/note_deep_probe_questions.json` (id/category/query/
expect per question; the before-leg spills its legacy vectors to
/tmp and rebuilds them in ~6m when absent). S3 results live in
doc/local/embed_model_eval.txt "NOTE SECTIONING — S3 EXECUTION
RECORD". Adopt the winner only on a measured win.

## 4. Acceptance criteria & shakedown

1. `embed_eval search` hybrid stays 1.00 (27/27) after sectioning —
   no regression on head queries.
2. Deep-content probe (15 questions, answers past token 512): sectioned
   hybrid ≥ BM25-only and ≥ pre-sectioning hybrid (the before-number
   measured in S3).
3. One cold rebuild completes; warm rebuild unchanged contract
   (content-hash cache); single-section edit re-embeds only that
   section (improvement — today any edit re-embeds the whole note).
4. Hybrid query latency at 14.5k rows measured and reported (accept if
   p50 < ~150ms on this box).
5. `make qa` green at arc end; `make advisory` swept.

| Projected outcome | Today | After |
|---|---|---|
| notes token-mass retained | 39% | ~85–95% (section-median ≪ 512) |
| note_search rows | 1,243 | ~14,520 |
| cold note embed | 6m01s | ~25–40m (short texts are faster per-row; measure) |
| warm single-note edit | re-embed whole note | re-embed changed sections only |
| search hybrid recall@5 | 1.00 | 1.00 (held) |
| deep-content recall (new probe) | expected low (measure) | material gain (the point) |
| whole-corpus KNN | ~7ms | ~70–80ms (measure; linear-ish) |

## 5. Risks

- **11.7× row growth** — vec0 KNN and FTS size scale with it; measured
  in S3, widening the candidate page only if recall demands.
- **BM25 page note-sparsity** — a 25-section page may cover few notes;
  mitigated by wider fetch + dedup (S2), decided on S3 numbers.
- **Heading-less notes (80)** stay whole-doc truncated — unchanged
  from today, listed as residual, not fixed here.
- **Snippet drift** — snippets become section-scoped; check the UI
  reading room consumes them without assumptions.
- **Cold rebuild cost** — one-time; incremental/warm paths improve.

## 6. Non-goals

- company_embeddings / semantic_neighbors surface (neighbors 5/10 does
  not move — different surface).
- docs surface (already section-chunked).
- any model swap (bge-small stays; granite comparison is phase 2
  measurement only).
- sub-H2 chunking or paragraph fallback for heading-less notes.
- note schema/writer changes (findata stays writer-owned, untouched).

## 7. Execution Results (2026-09-06, post-apply)

This section is the single durable record of the S3 execution (the
former doc/local/embed_model_eval.txt duplicate was eliminated at user
direction 2026-09-06 — the proposal is the record).

**Rebuild (serial, EMBED_POOL_WORKERS=1, ~48m wall)**: 1,243 → 14,500
section rows (11.7×; company=8,054 chatter=3,899 P&F=2,087 sector=368
super_sector=18 plotlines=74; distinct notes 1,243). All 14,500 embedded
fresh (only heading-less notes' preambles can ever cache-hit). Vec0
mirror 14,500 rows; f32 matrix 21.75 MB. Warm no-op incremental: 0
hits/0 misses (carry contract held); `--check` FRESH.

**Cap-bite after sectioning** (embed_token_cap on the live index):
median base 1,210 → **125 tokens**; fully-inside-512 21% → **90%**;
token-mass visible to vectors 39% → **80%** (residual = 1,450 long
sections, median 819 tokens, incl. the 80 heading-less notes — the
declared sub-H2 non-goal).

**Acceptance criteria (§4), as measured:**

1. `embed_eval search` hybrid **1.00 (27/27) HELD**; bm25 0.93;
   per-category identical to pre-sectioning. **PASS**.
2. Deep-content probe (15 questions, recall@5, rerunnable via
   `helpers/bench/note_deep_probe.py`): after_bm25 **13/15**, after_hybrid
   **11/15**, before_hybrid (pre-sectioning simulation) **11/15**.
   "hybrid ≥ before" **PASS**; "hybrid ≥ bm25" **FAIL on 2 fact-queries**
   — the now-informed cosine prefers topic-adjacent clusters over the
   exact-fact notes BM25 ranks top-5; pre-sectioning passed this bar only
   because its 512-truncated vectors were fact-blind (fusion degenerated
   to BM25 order). Not tuned further: a weighted RRF to win 2/15 fact
   queries would overfit the probe. Net deep-content recall moved
   11 → 13 (BM25 leg).
3. Cold rebuild completed; warm contract held live (0 cache traffic);
   single-section-edit re-embed covered by unit tests (fixtures).
   **PASS**.
4. Hybrid endpoint at 14.5k rows: **p50 122ms / p95 150ms** (n=45) —
   under the ~150ms bar, but at it, not comfortably (A1-era ~7ms KNN is
   gone; see vec0 cap below). **PASS**.

**Mechanisms discovered while measuring (all fixed in this arc):**

- FTS5 implicit AND became SECTION-scoped: a 9-term query needs one
  section holding every token (pre-sectioning: one note) — first probe
  run starved long queries to 0-1 notes (bm25 6/15). Fix: AND-first +
  OR-fill candidate generation via the doc-surface `fts_match_expr`
  token quoting (always-OR lost eval precision 1.00 → 0.93; AND-first
  restores 1.00 AND brings bm25 6 → 13). Free rider: hyphenated
  barewords ("de-risk") no longer misparse as FTS5 column filters (400).
- sqlite-vec vec0 KNN hard-caps at k=4096: whole-corpus k=None failed
  silently on every hybrid query and rode the flat-matrix fallback.
  Now routed (`VEC0_KNN_MAX`); the matrix leg is exact whole-corpus at
  ~12ms.
- Fusion cosine collapses to NOTE level (best-section similarity per
  file_path): the page row carries the BM25-best section, whose cosine
  may be mediocre while another section of the same note is the corpus's
  best match.
- Windowed fetch caps inner input at 1024 streamed sections (the window
  function over a full OR match set materialized hundreds of ms; p50
  246 → 122ms).

**Follow-up candidates (not this arc):** bm25() column weighting like the
docs surface (title/section_title ×2.0); cosine as a co-equal retriever
(docs-surface-style candidate union) if deep-content hybrid recall must
exceed bm25; per-question probe category balance.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-06 | helpers/bench/embed_token_cap.py | notes 21%/39%, docs 64%/70%, companies 24%/53% | bge tokenizer, live bases |
| 2026-09-06 | /tmp H2 census (folded into S1) | 1,246 notes → ~14,520 sections; 80 heading-less | frontmatter stripped first |
| 2026-09-05 | embed_eval (live index) | search hybrid 1.00 / docs 1.00 / vss 12/12 / neighbors 5/10 | same-day baseline, 963-row docs index |
| 2026-09-06 | helpers/bench/embed_runtime_bench.py | serial 3.65/s (MiniLM window); bge production 3.4/s idle | window-dependent; cold-embed projections use these |
| 2026-09-06 | embed_pool_probe bge production-batch serial + pool | serial 2.62–2.74/s; pool N=4 7.14/s | "pool 2.7×" — SUPERSEDED, call-shape artifact (rows below) |
| 2026-09-06 | embed_pool_probe --gguf bge --ctx 512 (per-text) | serial#2 12.31/s; pool N=4 10.83/s; pool N=2 5.91/s | per-text is 4.7× faster than the batch call; pool ≈ parity |
| 2026-09-06 | embed_pool_probe --gguf granite97m --ctx 2048 (per-text, 3 runs) | serial#2 10.06–13.22/s; pool N=4 7.8–10.3/s | granite per-section ≈ bge despite 97M + 2k ctx; first timed leg reads ~2.6× slow (cold-start artifact, now warmup-guarded) |

**Parallelization verdict (2026-09-06, embed_pool_probe over real section
texts)**: the pinned pool does NOT beat warm serial under load 4–8 —
parity at N=4 for both bge and granite (pinned 1T workers can't migrate
off stolen cores; the floating serial process can). The morning's
apparent 2.7× pool win was the call-shape artifact: the pool's per-text
workers were compared against the production batch path
(`embed_documents`, one multi-input llama.cpp decode = 2.62/s), not
against per-text serial (12.31/s). Load-bearing lever for any full
re-embed is CALL SHAPE, not parallelism: per-text serial projects
14,500 sections in ≈20m (bge) / ≈24m (granite @2k) vs the 48m the S3
rebuild took batch+serial. The settled `embed_model_trial` doctrine
("serial, never pinned pool") is CONFIRMED by clean data.
| 2026-09-06 | rebuild_note_search (EMBED_POOL_WORKERS=1) | 14,500 rows embedded, ~48m | first sectioned rebuild; warm no-op 0/0 |
| 2026-09-06 | embed_token_cap.py (sectioned index) | notes median 125 tok / 90% fit / 80% mass | was 1,210 / 21% / 39% pre-sectioning |
| 2026-09-06 | note_deep_probe.py | bm25 13/15, hybrid 11/15, before 11/15; p50 122ms | deep-content probe + latency, §7 |
