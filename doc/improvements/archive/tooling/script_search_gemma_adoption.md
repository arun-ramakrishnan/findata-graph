---
title: "Adopt EmbeddingGemma-2 Q8@512 for script_search (vector-primary)"
status: executed
filed: "2026-10-07"
executed: "2026-10-07"
completed_md: "363"
area: "helpers/maintenance/rebuild_script_search.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Adopt EmbeddingGemma-2 Q8@512 for script_search (vector-primary)

**Date:** 2026-10-07 · **Status:** EXECUTED (completed.md #363) ·
**Area:** helpers/maintenance/rebuild_script_search.py + helpers/core/local_embedder.py

Follows: `doc/local/evaluations/emb_gemma_assessment.md` (2026-10-07 verdict:
ADOPT gemma Q8@512 for `script_search` now; HOLD notes/docs pending the
parked docs leg; KEEP granite elsewhere). That note is the full eval
record — this proposal is the production slice only.

## 1. Motivation

The eval proved the case and the migration mechanics on two independent
setups; production still serves granite hybrid on scripts. The gap is
concentrated where it matters (rank 1 on paraphrase-intent queries), the
migration is ~6 min on 544 rows, and per-surface model stamps already
support the mix — the only thing standing between the verdict and the
win is the four-piece production slice below.

## 2. Evidence (measured 2026-10-07, this box; assessment §§3.2/3.4/3.5/4.6)

| Configuration | Result | Verdict |
|---|---|---|
| shipped granite hybrid, 54-q bank (46 intent + 8 ident) | MRR 0.874 (intent 0.914, ident 0.643) | baseline |
| proto gemma hybrid (production code + gemma QueryVector) | MRR 0.885 | matches shipped, trails vector-only ~0.05 |
| proto gemma vector-only (new shape) | MRR 0.932 (intent 0.964, ident 0.750) | **adopt** — intent +0.05 over shipped |
| hard 18 design/detail set, gemma vec + code prefix | 18/18 rank-1, MRR 1.000 (granite vec 0.793) | the card's code claim reproduces |
| identifiers 27q, BM25 full-index | 27/27 rank-1 (either model) | lexical path stays for exact lookups |
| gemma vec, NO query prefix, hard set | MRR 1.000 -> 0.880 | prefix omission is the silent trap |
| 512d vs 768d | code hard 18/18 both; multilingual fused 0.718 vs 0.722 | 512d lossless; 256d drops 3 rank-1s |
| best-config throughput (Q8 4T, quiet box) | 1.35 texts/s (Q6 3T 1.44/s, gated on recall leg) | 544 rows ≈ 6 min; 3.6-3.9x granite (structural) |

One paragraph: vector-only wins on intent in both scratch fusion
(0.963) and the production-code sidecar (0.964); equal-weight RRF
dilutes the stronger arm on code while helping on notes, so fusion
policy splits by surface — scripts goes vector-primary, BM25 keeps
identifiers (1.000 vs 0.750). Q8_0 is the only quant passing the
fidelity bar (cos-min 0.99951, top-10 agree 0.985); 512d keeps the
notes matrix (if later adopted) at 35 MB, below the 50 MB Mojo
GPU-routing bar. Measured, do not re-audit: the Vulkan iGPU path
(CLOSED), the spawn pool (saturates shared bandwidth, CLOSED), batch
and slot knobs (null), ubatch-2048 forced by 4000-char bases.

## 3. Design

Slices, each independently landable; S1 unblocks S2–S4:

- **S1 — gemma backend behind a per-surface selector.** `local_embedder.py`
  is the single source of truth for ALL surfaces (notes, docs, vss,
  companies) — its constants must NOT be swapped. The script surface
  resolves gemma whenever the sidecar/model is available (default
  follows availability, so house rebuilds cannot silently un-migrate —
  a default-granite draft let one `make search-fresh APPLY=1` revert
  the cutover, caught by the bank gate; `SCRIPT_EMBEDDER=granite`
  forces the shared path) with artifact pin (file + sha256 per
  assessment §1, DIM 512, query-side code prefix
  `task: code retrieval | query: {q}`) while every other surface keeps
  granite. The doc-side prefix (`title: {t} | text: {content}`) is basis
  text owned by `rebuild_script_search.py`, not the embedder — that half
  lands here too. Backend path: spike the vendored llama-cpp wheel with
  #30054 first (0.3.36 cannot load the arch; keeps the in-process shape —
  pool, `_EMBED_LOCK`, `available()` gating — untouched); llama-server
  sidecar is the fallback, designed behind the same interface so the
  swap stays contained. Cache label: the shared cache is keyed
  (sha256, model), so a new label is a clean miss set — granite rows
  untouched, scripts re-embed is one-off.
- **S2 — vector-only mode + identifier router.** `hybrid=False` is
  BM25-only today (`rebuild_script_search.py:1626` skips `_cosine_leg`
  entirely); add the vector-only shape the prototype proved. Router
  stays dumb: single-bare-token queries take the lexical path,
  everything else goes vector-primary (fixed weights fail one side —
  assessment §5.2). `master_query`'s rg/ripwire fan-out backstops it
  regardless.
- **S3 — prefix + stamp contract tests.** Pin the query prefix, the
  doc-basis prefix, and the `embeddinggemma-2-q8_512`/512 stamp. Both
  sides: omission degrades silently on either end, so the test covers
  both. Selector test: every non-script surface resolves granite.
- **S4 — migration + production cutover.** Proven recipe (assessment
  §3.5): copy the live sidecar, gemma Q8@512 over the production basis
  verbatim + doc prefix, truncate 768->512 + L2-renorm, `pack_f32`,
  stamp; shipped `search_scripts` ranks it with zero ranking-code
  changes. Bank the 54-question set into the repo (scratch banks are
  not a gate) and re-run it against the production path post-cutover.

Alternatives: (A) blanket swap — rejected: ~30 h full-corpus cost for
an unmeasured convo win. (B) Q6 quant now — rejected by operator ruling
2026-10-07 after the recall leg RAN (assessment §4.7): UD-Q6_K_XL matches
Q8 on every labelled leg (hard 18/18 @768 and @512, ident RRF w=1
27/27) but its +7-10% throughput is too minimal to matter — KEEP Q8_0;
Q6 stays rehabilitated-on-record as the fallback if embed wall time
ever becomes critical. (C) sidecar-first — rejected: new supervision
infra vs a wheel spike that preserves every existing contract.

## 4. Acceptance criteria & shakedown

1. Scratch-copy migration reproduces the prototype: vector-only intent
   MRR >= 0.96 on the repo-banked 54-q set; identifiers BM25 1.000;
   granite-384d QueryVector against the 512d sidecar degrades to BM25
   on every query (never mixes models — §3.5 safety checks, re-run).
   REHEARSED 2026-10-07 on a scratch copy (546 rows, stamp
   `embeddinggemma-2-q8_512`/512, 546/546 embedded, 0 cache hits on the
   fresh label): `helpers/bench/script_eval_bank.py` through the
   production path scores intent MRR 0.964 / R@5 1.000 and ident 8/8
   MRR 1.000 — the prototype numbers to three decimals. Like-for-like
   granite baseline on the live index: intent 0.914, ident 0.643
   (hybrid dilutes identifiers, as in §3.5).
2. Prefix tests fail loudly when either prefix constant is emptied;
   selector test asserts granite on all non-script surfaces.
3. Targeted suites green (`script_search`, `master_query`,
   `local_embedder`, shared-vector) + ruff + `ty` — never `make pytest`.
4. Query-visible semantics gate: the banked 54-q set re-run between
   pre-cutover granite and post-cutover gemma — intent improves, zero
   identifier regressions.

| Projected outcome | Today | After |
|---|---|---|
| scripts intent MRR (46q) | 0.914 (granite hybrid) | >= 0.96 (gemma vector-only) |
| scripts ident MRR (8q) | 1.000 (BM25) | 1.000 (unchanged path) |
| scripts re-embed wall | — | ~6 min one-off |
| other surfaces | granite | granite (selector-pinned) |

## 5. Risks

- **Wheel with #30054 unavailable/blocked** — sidecar fallback behind
  the same backend interface; S2–S4 are backend-agnostic.
- **Silent prefix omission (either side)** — S3 pins both constants by
  test; the failure mode is measured, not hypothetical.
- **Cross-surface leakage (gemma vectors in a granite index)** — stamps
  + `check_query_vector` reject on label/dims; S3 selector test holds
  the default.
- **Upstream drift (unsloth re-quant, arch support)** — sha256 pin +
  fidelity-bar re-check on any artifact change.

## 6. Non-goals

Notes/docs/convo/companies surfaces (granite stays; the parked docs
leg gates the all-surfaces memo, not this arc); multimodal/torch path;
Q6 recall leg; llama-server hosting decisions beyond the S1 fallback.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-07 | scratch sidecar eval, 54q | vector-only 0.932 / intent 0.964 | assessment §3.5, `proto_script_search.db` |
| 2026-10-07 | quiet-box server bench, 16 texts | gemma Q8 4T 1.35/s vs granite 5.24/s | assessment §4.6; gap structural |
| 2026-10-07 | quant fidelity, 200 texts | Q8 cos-min 0.99951 / top-10 0.985 | only passing arm; assessment §3.1 |

Full tables, banks, and bench procedure:
`doc/local/evaluations/emb_gemma_assessment.md` (machine-local working
note; raw material archived at `bench_data/embgemma2/` — scripts/banks,
outputs/logs, vectors/caches).
