---
title: "Consolidate duplicated Mojo bench/common kernels — cosine SIMD, f32 loader, bridge helpers"
status: executed
filed: "2026-09-03"
executed: "2026-09-04"
completed_md: "202"
area: "Mojo/src/bench (bench_cosine*, bench_scale, bench_pool), Mojo/src/common (integrity_check.mojo), Mojo/bench (mojo_graph_algos.py, mojo_db_access.py, mojo_db_integrity.py, max_real_matmul.py, flat_knn.py)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Consolidate duplicated Mojo kernels + bridge scaffolding

**Date:** 2026-09-03 · **Status:** PROPOSED ·
**Area:** `Mojo/src/bench` · `Mojo/src/common` · `Mojo/bench` ·
disposition index for cross-module Mojo/Python duplication found in the
2026-09-03 consolidation survey

## 1. Motivation

The Mojo bench surface grew several independent probes and per-flag
benchmarks that each re-implement the same low-level kernels and the same
Python-bridge plumbing. `.mojo` files are compiled separately; the house
pattern is "each probe stays self-contained" (there is even a comment in
`graph_algos_probe.mojo` acknowledging copy-paste). That self-containment
was pragmatic when the bench family was experimental, but it now means a
bug or optimization in the performance-critical `row_cosine` SIMD kernel is
hand-propagated to five copies — four of which are **not covered by any
test** (only `bench_cosine.row_cosine` is imported by `test_cosine.mojo`).

Consolidation is behavior-preserving: every copy is byte-identical or
parameter-identical, so extraction into shared `Mojo/src/common/` modules
changes the import site, not the logic.

## 2. Census (measured 2026-09-03) and disposition

### 2.1 Mojo kernels (`.mojo`)

| Kernel | Sites (file:lines) | Disposition |
|---|---|---|
| `row_cosine` — SIMD cosine (width/vectorize, f32→f64 accumulators, zero-norm guard) | `bench_cosine.mojo:26-48`, `bench_cosine_max.mojo:34-56`, `bench_cosine_max_parallel.mojo:18-37`, `bench_scale.mojo:29-48`, `bench_pool.mojo:26-49` (pairs byte-identical; quintet logic-/parameter-identical — docstring + inner `comptime width` placement differs) | **CONSOLIDATE** → `common/cosine.mojo` (canonicalize one docstring + `width` placement) |
| `load_f32` — binary f32 loader (alloc+unsafe_leak, `read_bytes` workaround) | `bench_cosine.mojo:51-75`, `bench_cosine_max.mojo:59-83`, `bench_cosine_max_parallel.mojo:40-57`, `bench_scale.mojo:51-68`, `bench_pool.mojo:52-64` (`bench_pool` error string omits `", got "+String(nbytes)` — pick one message) | **CONSOLIDATE** → `common/io.mojo` (or fold into `cosine.mojo`) |
| `scan_serial` — serial cosine scan | `bench_cosine_max_parallel.mojo:133-149`, `bench_scale.mojo:71-87` (plus inline `scan()` in `bench_cosine.mojo:105-117`) | **CONSOLIDATE** → `common/cosine.mojo` |
| `sys_exit` (process-exit helper; cited ranges contain only `sys_exit` — no Python-path setup lives in any `.mojo` there) | `db_access_probe.mojo:83-87`, `graph_algos_probe.mojo:400-404`, `common/integrity_check.mojo:1795-1799` | **CONSOLIDATE** → `common/bridge.mojo` |
| `contains`/`join_list`/`py_str`/`to_i` (Python-bridge helpers) | `graph_algos_probe.mojo:39-96`, `common/integrity_check.mojo:53-103` (bounding boxes; shared helpers byte-identical, `py_str`/`to_i` order swapped; self-acknowledged copies per `graph_algos_probe.mojo:36`) | **CONSOLIDATE** → `common/bridge.mojo` |
| `sort_strs` (insertion) vs `merge_sort_strs` (merge — different algos by design, not duplicates) | `common/integrity_check.mojo:60-70` (`sort_strs`), `graph_algos_probe.mojo:46-79` (two fns: `_merge_sorted` + `merge_sort_strs`) | **COLOCATE** → `common/list_utils.mojo` (two algos, one module; no unification) |

### 2.2 Python bench fixtures (`.py`)

| Piece | Sites | Disposition |
|---|---|---|
| `sum_rows` checksum oracle (logic-identical; docstring length differs) | `mojo_graph_algos.py:468-471`, `mojo_db_access.py:131-137` | **CONSOLIDATE** → `bench/bridge_utils.py` |
| `REPO` + `sys.path` + lazy read-only connect (shared idiom, not copies — DB paths/flags differ: 3 DBs + `LOAD onager` vs `query_only` vs duckdb) | `mojo_graph_algos.py:30-66`, `mojo_db_integrity.py:16-33`, `mojo_db_access.py:19-38` | **CONSOLIDATE** → `bench/bridge_utils.py` (parameterized helper: which DB/flags) |
| `aligned_array` 64B-alignment helper (one `def` + two inline idiom sites, not three copies) | `max_real_matmul.py:61-74` (`def`), inline ×2 in `flat_knn.py:61-66,83-86` | **CONSOLIDATE** → `bench/aligned_array.py` (refactor inline sites to call it) |
| `split_doc`/`read_doc` frontmatter split (same `---`-split idiom; different names/signatures/languages) | `corpus_sweep.mojo:49-54`, `mojo_regex_corpus.py:41-45`, `common/integrity_check.mojo:1274-1275` | **CONSOLIDATE** (Mojo side→`common/corpus.mojo`, Python side→`bridge_utils.py`; low value — rides along only if trivial, per §7) |

## 3. Design

- **`Mojo/src/common/cosine.mojo`** — export `row_cosine()`, `load_f32()`,
  `scan_serial()`; the five bench files and `test_cosine.mojo` import from
  it. The zero-norm guard and the `read_bytes()` workaround comments move
  with the code (single copy of the docstring; canonicalize the
  `comptime width` placement and pick one `load_f32` error string).
  `load_f32` folded here, not a separate `io.mojo` (one fewer module for
  two tightly-coupled kernels).
- **Build constraint (verified at execution):** `mojo build` refuses a
  module without `main()` ("module does not contain a 'main' function"),
  and the Makefile builds every `src/*/*.mojo` into `Mojo/bin/` — so each
  shared module carries a tiny smoke `main()` (synthetic kernel/helper
  checks) instead of changing the build wiring.
- **Import shape is FLAT, not namespaced** — `Makefile.mojo` passes
  `-I Mojo/src/<pkg>` per package dir, so consumers write
  `import cosine` / `from bridge import sys_exit`, NOT
  `from common.cosine import …`. Precedent already in tree:
  `Mojo/tests/test_cosine.mojo` does `import bench_cosine` across the
  same flag layout (now `import cosine`). Pick non-generic-enough names to avoid future
  collisions (`cosine`, `bridge`, `list_utils`, `io` — `io` is the
  riskiest; prefer folding `load_f32` into `cosine.mojo` over a bare
  `io` module).
- **`Mojo/src/common/bridge.mojo`** — export `sys_exit()` and the four
  `contains`/`join_list`/`py_str`/`to_i` helpers (no Python-path setup lives
  in `.mojo` — that idiom is the `bridge_utils.py` row below);
  `src/common/integrity_check.mojo`, `src/bench/graph_algos_probe.mojo`,
  `src/bench/db_access_probe.mojo` import from it.
- **`Mojo/src/common/list_utils.mojo`** — export `sort_strs()` (small
  lists) and `merge_sort_strs()` (large lists); both probe files import.
- **`Mojo/bench/bridge_utils.py`** — export `REPO`, `sys.path` bootstrap
  helper, parameterized lazy `connect_sqlite_ro()` (row_factory/query_only
  flags differ per file) / `connect_duckdb_ro()`, path constants, and
  `sum_rows()`; per-file idiom sites call it instead of re-rolling it.
- **`Mojo/bench/aligned_array.py`** — export `aligned_array()`; both
  `max_real_matmul.py` and `flat_knn.py` import it.
- **Execution deviations (behavior-preserving):** the dead `SQLITE_RO`
  URI strings (defined, never used — both files already used the helpers
  connect) were deleted, not centralized; `DUCKDB_RO` centralized via
  `connect_duckdb_ro()`; stale `Mojo/src/bench/integrity_check.mojo` path
  in the `mojo_db_integrity.py` docstring fixed to `src/common/`.

No CLI surface changes. Bench binaries must rebuild and produce bit-identical
parity output.

## 4. Non-goals

- **Not** changing any parity oracle contract or the `os._exit` exit-code
  gating introduced by #197 — extraction only.
- **Not** unifying the two different sort complexities into one adaptive
  sort (they serve different list sizes by design); sharing the module is
  the win.
- **Not** touching `Mojo/vendor` — unrelated to in-repo duplication.
- **Not** consolidating the two `href` variants in the frontend — that is a
  different proposal.

## 5. Gates

- Rebuild all affected Mojo binaries; run the Mojo bench legs
  (`run_bench.py`) — `db-access` 6/6, `db-integrity` golden parity 89/89,
  graph-algos parity unchanged, all rc 0.
- `test_cosine.mojo` and the other Mojo-side tests pass (now exercising the
  shared module).
- Python bench fixtures import cleanly; `make perf` unaffected.
- `ruff` + `deptry` clean on the touched Python; `make qa` once at arc end.
- `make search-fresh APPLY=1` — moving/adding Mojo modules churns the
  `kind='mojo'` script_search index (doc-sidecar cache keyed on path).

## 6. Risks

- **Compile-coupling**: Mojo `common/` modules must compile under each
  consumer's build graph. Mitigation: keep the shared modules dependency-
  free of the bench entry points, and rebuild in the same change.
- **Parity drift**: any accidental behavior change in `row_cosine`
  surfaces as a parity FAIL in the gated bench legs — that is precisely the
  safety net that makes this refactor low-risk (the legs now gate rc 1 on
  parity mismatch, per #197).
- **The one untested-quad**: `test_cosine.mojo` tests only one of the five
  `row_cosine` copies today; consolidation means the single shared copy is
  the tested one — a strict improvement, not a risk.

## 7. Deferred

- Folding `split_doc` across Mojo **and** Python into one source of truth
  is cross-language and low value (each language has its own copy anyway);
  it rides along only if the shared modules above make it trivial.
- The bench "self-contained probe" doctrine is intentionally relaxed here
  because the duplication is now >2× — a future probe should import from
  `common/`, not re-copy.
