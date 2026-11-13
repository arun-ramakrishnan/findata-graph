---
title: "Mojo windowed-shingle similarity lens (unfreeze step 3)"
status: executed
filed: 2026-11-13
executed: 2026-11-13
completed_md: "520"
area: tooling
---

# Mojo windowed-shingle similarity lens (unfreeze step 3)

## 1. Motivation

The Mojo lane re-evaluation (2026-11-13) left the lane viable but premised on
"nothing in production consumes Mojo". Step 1 re-added `mojo-parity` to qa;
step 2 (mojo_12_changelog_trials.md) ran 1.2 changelog trials and shipped the
first production primitive, `clamp_slice` (`Mojo/src/common/slice_utils.mojo`),
with one real consumer (`head_bytes` in `graph_algos_probe.mojo`).

This proposal is step 3: give the lane a **genuine analytical feature** that
(a) is useful on its own, (b) makes `slice_utils` an active dependency in a
hot loop, and (c) reuses the established parity harness discipline.

**Feature: windowed-shingle similarity (a.k.a. "local affinity lens").**
Cosine/token-set similarity is global — a 2-line note and a 200-line note
with the same vocabulary score high, even if the topical overlap is only in
one region. Windowed shingling splits each token sequence into overlapping
(or contiguous) windows of width `W` and computes token-set Jaccard per
window; the max (or mean) of those window scores is a **local** similarity
that resists vocabulary dilution. This is a known IR technique (shingles /
w-shingling) and maps directly onto the repo's similarity research
(semantic clusters, fuzzy-name matching).

## 2. Design

### 2.1 Why this feature needs slicing — and what we learned

The natural implementation represents each window as a `Span` view over the
token array (`tokens[i : i+W]`). The probe work in mojo_12_changelog_trials.md
§2.4 established two hard facts:

- Span slices are zero-copy views with **fatal, non-recoverable Assert
  Errors** on out-of-range/negative bounds;
- **returning** a Span from a free function is not expressible (`-> Span[T, ...]`
  → `is not concrete`; `-> Span[T, True]` → `cannot be converted from 'Bool'
  to 'Origin[mut=$0]'`).

Therefore the window set is materialized as **index-pair ranges** computed via
`clamp_slice` (which guarantees every window is legal), and each window is
sliced inline by the caller:

```mojo
var ranges = window_ranges(n, width)   # List[Tuple[Int, Int]], clamped
for k in range(ranges.__len__()):
    var lo = ranges[k][0]; var hi = ranges[k][1]
    var win = toks[lo:hi]              # Span view, always in bounds
    ... # per-window Jaccard
```

This makes `slice_utils` a required dependency of the lens, not a convenience.

### 2.2 Module + API surface

New file `Mojo/src/bench/window_shingles.mojo` (bench package; flat-binary
build requires `main` — self-check pattern):

- `window_ranges(n: Int, width: Int) -> List[Tuple[Int, Int]]` — contiguous
  windows, last one clamped short via `clamp_slice(n, i, i+width)`; returns
  `[]` when `n == 0`.
- `tokenize_lower(s: String, lower_fn: PythonObject) raises -> List[String]`
  — reuses the established bridge-lower + Mojo-side tokenization pattern from
  `integrity_check.mojo` (`meaningful_tokens` shape: alnum runs, stopword
  filter, dedupe).
- `_token_set(win: Span[String, ...]) -> List[String]` — unique tokens in a
  window (view → list; no copies of the window itself).
- `window_jaccard(win_a: Span[String, ...], win_b: Span[String, ...]) -> Float64`
  — |A∩B|/|A∪B| over the two window token sets; empty union → 0.0.
- `local_affinity(a: List[String], b: List[String], width: Int) -> Float64`
  — max window Jaccard across all aligned window pairs; mean variant exposed
  as `mean_local_affinity` for the evaluation table.

### 2.3 Parity + bench harness

- Python fixture `Mojo/bench/mojo_window_shingles.py` implements the identical
  algorithm (window_ranges / window_jaccard / local_affinity) for the
  bridge fixture (same pattern as `mojo_graph_algos.py`).
- New bench leg `window-shingles` in `Makefile.mojo` (mojo-bench table):
  runs both sides over a sample corpus and asserts parity (≤1e-9 on every
  float), plus a perf smoke (2×2k-token inputs under 200 ms).

### 2.4 Measured evidence (POC)

POC `/mnt/data/tmp/opencode/probe_window_poc.mojo` (2026-11-13, Mojo 1.2.0)
verified the core range/slice loop: `window_ranges(7, 3)` over an
`Array[String, 7]` yields `(0,3) (3,6) (6,7)` — the tail window correctly
clamped to length 1 by `clamp_slice`, and inline slicing `toks[lo:hi]`
produces the right views (`abc`, `def`, `g`). Ownership note recorded: `List`
returns need `^` transfer (not ImplicitlyCopyable).

## 3. Acceptance

1. `make mojo-build` green (new module + leg compile clean).
2. `make mojo-test` green, including new unit tests (see §4).
3. New bench leg `window-shingles` runs and **parity PASS** vs the Python
   fixture.
4. Perf smoke: 2×2k-token inputs complete < 200 ms.
5. `slice_utils` has exactly one more consumer (the lens) + `head_bytes` —
   audit check states both.
6. qa battery unaffected (no gate changes in this step).

## 4. Test plan

- Unit (`Mojo/tests/test_window_shingles.mojo`):
  - `window_ranges`: n=0 → []; width>n → single clamped window; partial
    tail (n=7,width=3 → 3 ranges, last len 1); width=1 → n ranges.
  - `window_jaccard`: identical windows → 1.0; disjoint → 0.0; empty union →
    0.0; overlapping partial.
  - `local_affinity`: synthesized cases where global Jaccard is low but a
    local window overlap is high (the motivating scenario); mean variant.
  - clamp guarantee: every produced window applied via slice never aborts
    (positive test over adversarial n/width combos).
- Parity (`Mojo/bench/`): fixture equivalence on a fixed 20-doc sample.
- Integration: bench leg entry runs in `make mojo-bench MOJO_BENCH_ARGS='--leg window-shingles'`.

## 5. Non-goals

- No graph/DB writes, no schema changes, no new tables.
- No changes to embedding models or semantic_clusters.
- No change to the qa gate set (bench-only feature).
- No re-slicing of existing lanes beyond the two consumers named.

## 6. Risks

- **Unicode/token boundaries:** tokenization is codepoint-based (matches
  integrity_check); window boundaries are token indices, not byte offsets —
  no byte/UTF-8 skew.
- **Memory:** windows are views, never materialized copies; only index-pair
  lists are held (O(n/W) tuples).
- **Determinism:** pure deterministic algorithm; fixture mirrors it exactly.
- **Bridge dependency:** lowercase via Python bridge (as today) — acceptable
  in bench context; noted as future pure-Mojo candidate.
- **Perf:** window-Jaccard is O(N·W) worst case; 200 ms smoke bounds it.

## 7. Execution plan

- S1: implement `window_shingles.mojo` + unit tests (this proposal's §2.2/§4).
- S2: Python fixture + bench leg registration + parity run.
- S3: final report with measured numbers (parity, perf table), proposal update,
  operator commit.

## 8. Implementation log

- **2026-11-13 (S1)** — `Mojo/src/bench/window_shingles.mojo` landed:
  `window_ranges` / `unique_window_tokens` / `window_jaccard` /
  `local_affinity` (max + mean), all pure, plus `tokenize_lower` mirroring
  the golden fixture (bridge lower, alnum runs, stopword filter, dedupe).
  `Mojo/tests/test_window_shingles.mojo`: 9 unit tests green
  (window ranges incl. clamped tail/width>len/empty/width=1; Jaccard
  identical/disjoint/partial via cross-multiplication; local-overlap
  motivating case; empty affinity).
- **2026-11-13 (S2)** — `Mojo/bench/mojo_window_shingles.py` golden oracle
  (20-doc deterministic sample; exact rational goldens as
  numerator/denominator), bench leg `window-shingles` registered in
  `Mojo/bench/run_bench.py` (budget 40s, gate 5s, `_venv_env()`).
- **Measured (S2 run):** `make mojo-bench MOJO_BENCH_ARGS='--leg
  window-shingles'` → **leg 0.08s PASS** (gate 5.0s); **parity 210/210**
  `0 failed, exact cross-multiplication fraction equality); perf smoke
  2×2k-token docs **0.001 ms** (budget <200 ms). Two parity bugs found &
  fixed during bring-up: missing dedupe in the Mojo tokenizer (window-count
  divergence), and raw tuple equality vs unreduced fractions → switched to
  cross-multiplication.
- Acceptance: items 1–5 VERIFIED; item 6 unchanged (no gate changes).
