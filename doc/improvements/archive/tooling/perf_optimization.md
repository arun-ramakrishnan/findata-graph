---
title: Perf optimization plan — `make perf` hotspots (measured 2026-08-26)
status: executed
filed: '2026-08-26'
executed: '2026-08-26'
completed_md: '163'
area: tooling
---

# Perf optimization plan — `make perf` hotspots (measured 2026-08-26)

**Status:** O1-O3 EXECUTED 2026-08-26 (completed.md #163) — make perf
22/22: graph_link_prediction 2.03s -> ~1.3s (duckdb's first parameterized
execute was importing pandas; edge-type filter now inlined literals),
extract_relations 5.33s -> ~2.0s (existing-edge set hoisted out of the
per-note reduce loop), pdf_pipeline_local 10.79s -> 3.33s (layout model
off by default after a 7-PDF corpus A/B showed it was both the time sink
AND a ~3% word-coverage loss; `--layout` opts back in). O4 watch-only.
**Method:** cProfile on the exact perf-gate command lines (22 benchmarks,
20 pass). This doc covers the two OVER_BUDGET items and the two slowest.

---

## 1. Measured state

| Benchmark | Time | Budget | Status |
|---|---|---|---|
| graph_link_prediction | 2.03s | 2.0s | ✗ over by 1.5% |
| extract_relations | 5.33s | 5.0s | ✗ over by 6.6% |
| pdf_pipeline_local | 10.79s | 20.0s | ✓ but slowest bench |
| derive_insights | 3.83s | 4.0s | ✓ borderline (not scoped) |

### 1.1 graph_link_prediction — cProfile breakdown (2.05s total)

| Component | Time |
|---|---|
| imports at module load (**pandas alone 0.48s**) | ~0.65s |
| connect() + extension LOADs | ~0.19s |
| `_materialize_from_db` (2 temp-table scans of 17k edges) | ~0.51s |
| jaccard table function + NOT EXISTS anti-join SQL | ~0.85s |

The failure is structural noise, not algorithmic collapse: a borderline
budget (2.0s) meets a fixed ~0.85s of import/startup overhead that grows
with the environment. The SQL core is an Onager C++ table function —
already native speed.

### 1.2 extract_relations — cProfile breakdown (8.6s profiled / 5.33s real)

| Component | Time (profiled) |
|---|---|
| `apply_edges`: bulk existing-triples SELECT **per note** (110 × ~15ms) | ~1.68s |
| `extract_relations` regex core (110 notes) | ~4.19s own |
| `_capture_groups` (1,537 calls) | ~0.94s |
| batch process-pool spawn overhead | ~0.07s |

The smoking gun: `_extract_batch` calls `apply_edges` once per note, and
each call re-fetches the FULL existing `(source,target,type)` triple set
(~17k rows) from SQLite. O(notes × edges) work that is identical every
time. The regex core is already process-parallelised (4 batches).

### 1.3 pdf_pipeline_local — cProfile breakdown (11.3s profiled)

| Component | Time |
|---|---|
| pymupdf4llm `to_markdown` | 9.77s |
| — of which **ONNX layout model** (`get_layout` / BoxRFDGNN) | **8.36s** |
| —— onnxruntime inference (64 calls) | 5.81s |
| —— page feature extraction | ~6.1s cum |
| write_outputs | 0.02s |
| verify_extraction | 0.13s |

85% of the pipeline is pymupdf's neural layout model running per page.
The bench comment says "Tesseract dominates" — stale: pdf_local.py never
invokes Tesseract (docstring fix needed too).

**Live A/B experiment** (Yes_Bank_Colgate_Allcargo.pdf, 30 pp):

| Config | convert | verdict | doc_coverage | images extracted | wikilink refs kept |
|---|---|---|---|---|---|
| use_layout=True (current) | 9.14s | PASS | 0.9696 | 4 (16K, 61K, 2K, 2K) | 0 |
| use_layout=False | **2.68s** | PASS | **0.9989** | 1 (63K) | 0 |

Layout-off is 3.4× faster AND recovers more source words (the layout
model currently *drops* ~3% of words). On this PDF nothing of value is
lost — zero image refs survive normalisation either way. BUT this PDF is
not representative for figures: the trial corpus has tables rendered as
images that the figure pipeline depends on. A 7-PDF corpus parity check
must decide, not one PDF.

## 2. Plan

### O1 — graph_link_prediction under budget (no output change)

- Lazy-import pandas in `helpers/graph/algorithms.py` (import inside the
  code paths that need it; CLI table rendering can stay plain strings).
  Saves ~0.5s on EVERY algorithms.py invocation (also pagerank/closeness/
  betweenness benches benefit).
- Push the edge-type WHERE filter deeper so `_materialize_from_db` scans
  once (single temp-table build instead of UNION scan + join scan).
- Gate: golden compare — top-10 jaccard pairs byte-identical before/after;
  bench ≤1.6s. Keep the 2.0s budget.

### O2 — extract_relations: hoist the existing-edge set

- Load the `(source,target,type)` triple set ONCE in `_cli` /
  `_extract_batch` and pass it into `apply_edges(existing=...)`
  (parameter defaults to None → current self-loading behaviour preserved
  for external callers/tests).
- Gate: all 55 driver tests green; double-run idempotence unchanged;
  bench ≤4.3s (~20% cut).

### O3 — pdf pipeline: layout-model decision by corpus evidence

- New throwaway-ish experiment harness (or extend bench_pdf_pipeline):
  run all 7 Reports/*.pdf both ways; record time, verify doc_coverage,
  full extracted-image inventory, and diff surviving wikilink refs
  against the Paddle/GLM reference notes (the #156 word-recall method).
- Decision matrix:
  - If figure/table extraction survives layout-off on the corpus →
    flip default to fast path (`use_layout(False)`), keep `--layout`
    opt-in flag; expected bench ~3.5s (from 10.8s).
  - If figures regress → keep layout ON but parallelise page inference
    (pymupdf.layout ships MultiProcessWrapper) and/or add per-PDF sha256
    layout cache under memory/ (PDFs are immutable inputs); target ~5-6s.
- Either way: fix the stale "Tesseract dominates" bench docstring.

### O4 — budget hygiene (small)

- derive_insights (3.83s/4.0s) and graph_rebuild (2.89s/5.0s) get watch-
  only status; revisit only if they trip.
- After O1/O2 land, re-run make perf 3× and record medians in completed.md
  so future budget debates start from data.

## 3. Risks

- O1 golden-drift: jaccard ties broken by (score DESC, lo, hi) — order is
  deterministic; compare must be exact.
- O2 concurrency: batch processes must not each reload the set (hoist
  ABOVE the pool boundary or load once per worker via initializer arg).
- O3 quality risk is real but measurable; the verifier itself is the
  referee (doc_coverage + number audit), plus manual eyeball of one
  table-heavy newsletter.

## 4. Success criteria

- make perf: 22/22 pass with ≥15% headroom on all four scoped benches.
- No behavioural change: link-prediction golden equal; extract_relations
  edge counts identical on the 3-dir bench corpus; pdf verify PASS with
  corpus-approved image inventory.
