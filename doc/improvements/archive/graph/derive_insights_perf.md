---
title: "derive_insights + entity-resolution micro-perf — re hoisting, decode-once embeddings"
status: executed
filed: "2026-09-05"
executed: "2026-09-05"
completed_md: "208"
area: "helpers/graph/derive_insights.py, helpers/core/get_tickers.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# derive_insights + entity-resolution micro-perf

**Date:** 2026-09-05 · **Status:** EXECUTED 2026-09-05 (both slices
landed — completed.md #208) · **Mode:** two small slices, land
together.
**Area:** helpers/graph/derive_insights.py (regex hoisting) ·
helpers/core/get_tickers.py (decode-once VSS table).

## 0. Context (why this, why not JIT)

JIT evaluation (2026-09-05, `perf_event_paranoid=1` live counters):
`derive_insights` IPC 2.1 / L1D-miss 1.4%, `extract_relations` IPC 2.6 /
2.8 CPUs — healthy retire, nothing instruction-bound. Serial-worker
cProfile attributes the wall to `iter_company_sections` 0.89s (string
splitting), `yaml_safe_load` 0.54s (already-C CSafeLoader), C-engine
`re.search` 0.31s, `extract_metrics` 0.39s — none `@njit`-shaped
(strings/YAML/regex/JSON). The only numeric loops (app `tier-3` cosine
fallback ~2.4ms, tickers scan) ceiling at numpy parity (~0.04ms) or are
decode-dominated. So: **no numba dependency** (non-goal, stays in
vault_scaling non-goals). The two items below are what the traces say is
actually cheap and safe to take.

## 1. Slice 1 — hoist per-file regex compiles (derive_insights.py)

**Evidence:** cProfile `--workers 1`: `re._compile` 6502 calls
(+ `sre_compile` sub-calls ~0.1s). Module-level batteries are already
compiled; the misses are inline string patterns + per-call
`re.compile(...)` of constant patterns, executed once per file
(~1243 files × ~5 sites):

| Site | Current | Fix |
|---|---|---|
| `_parse_value_num:784` `re.findall(r"[\d,]+(?:\.\d+)?", ...)` | string pattern per metric | module `_NUM_RE` |
| `_find_insertion_point:1194` `re.search(r"^## (The Chatter\|Key Insights\|...)", ...)` | string pattern per file | module `_INSERT_HEADING_RE` |
| `_existing_hand_block_for_edition:1211`, `_strip_auto_block:1234` `re.compile(re.escape(_BEGIN)+...)` | SAME pattern recompiled per file (`_BEGIN/_END` are constants) | one module `_AUTO_BLOCK_RE` |
| `_kf_insertion_point:1556` loop `re.search(heading, ...)` ×3 + `:1559 r"^## "` + `:1561 r"^## The Chatter"` | 3–5 compiles per file | module `_KF_SECTION_RE` (single alternation), `_H2_RE`, `_CHATTER_H2_RE` |
| `_kf block:1444` `re.compile(re.escape(_KF_BEGIN)+...)` | same-shape per-file recompile | module `_KF_BLOCK_RE` |

**Expected gain:** ~0.1s off ~2.1s stale-only (compile + cache-lookup
overhead only; match execution unchanged). Opportunistic cleanup, not a
headline speedup — framed honestly.

**Risk:** near-zero (pattern semantics identical; compiled == string
for `re` module functions). Guard: `tests/test_derive_insights.py`
(144 tests, exercises pool default) + byte-identical `--stale-only`
dry-run diff before/after.

## 2. Slice 2 — decode-once company_embeddings (get_tickers.py)

**Evidence:** `_best_vss_match:266` + `_candidate_vec:217`
(`ast.literal_eval` per row per call); measured 1500×384
`company_embeddings`: **1502ms decode-dominated scan per call**
(literal_eval ≈ 1ms/row; the dots are 0.04ms). Worse,
`vss_match` re-fetches + re-decodes the whole table **per ticker query**
(`resolve_entity:366` loops long/short names), so a newsletter run pays
~1.5s N times.

**Fix:** per-process decoded-table cache inside `vss_match`:
key = sha1 over the raw embedding strings (content-digest, not
COUNT/rowid — any mutation is a different key by construction),
value = `[(name, vec|None)]` with dims pre-validation left at scan
time (identical skip semantics). Bounded (evict-oldest past 8) so long
runs with rotating conns can't grow it. Keeps the best-effort contract
(no raise, absent/bad table → no-match) and the standalone-CLI
constraint (no numpy/DuckDB dep — plain dot loop stays).

**Measured 2026-09-05** (1500×384 tmp table, 20 repeated queries):
per repeated query **1502.6ms → 71.2ms (21×)**; per-run cost
`N × 1.5s → 1.5s once + N × 71ms` (residual is sqlite fetch + digest,
not decode). 20-ticker run: ~30s → ~3s.

**Risk:** low; invalidation is the only subtlety — fingerprint covers
row add/remove/rewrite (rowid changes on re-INSERT; in-place UPDATE
keeps rowid — acceptable: embeddings table is write-once per rebuild;
note the assumption in a comment). Guard: existing get_tickers tests +
N=20-query microbench before/after.

## 3. Non-goals

- numba / any JIT dep (evaluated, rejected — see §0).
- `app.py _cosine` fallback rewrite (tier-3, ~2.4ms, rarely fires).
- Touching match semantics, thresholds, or embedder selection.

## 4. Acceptance

- `tests/test_derive_insights.py` + get_tickers/entity tests green.
- `ruff check` + `format` clean on both files.
- Timing: stale-only delta recorded (medians 1.85s → 1.71s best-of-3,
  ~0.1s at the edge of run-to-run noise — cleanup value, not speedup);
  20-query VSS microbench recorded (1502.6ms → 71.2ms per repeated
  query, 21×).
- `make search-fresh APPLY=1` after the doc change.
