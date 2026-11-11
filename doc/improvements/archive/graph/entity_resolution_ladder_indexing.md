---
title: "Entity-resolution ladder indexing: cache token sets + bucket qualifier scans in derive_insights"
status: executed
filed: "2026-11-11"
executed: "2026-11-11"
completed_md: '508'
area: graph
---

# Entity-resolution ladder indexing: cache token sets + bucket qualifier scans

**Date:** 2026-11-11 · **Status:** EXECUTED (completed.md #508, archived 2026-11-12) ·
**Area:** helpers/graph/derive_insights.py

## 1. Motivation

A cProfile of the production `derive_insights` pipeline (dry-run, 131
newsletter files, `--workers 1`, 31.37 s total) shows the entity-resolution
ladder dominates the scan leg (~26.4 s):

| function | calls | self time | share of total |
|---|---|---|---|
| `_qualifier_variants` (derive_insights.py:3356) | 729 | 7.48 s | ~24% |
| `_fuzzy_suggestions` (derive_insights.py:3601) | 94 | 6.54 s | ~21% |

Root cause is the same in both: `_build_resolver_map` produces a **48,793-key**
dict (22,240 companies × name/normalized variants), and both functions scan
the *entire* map per call:

- `_qualifier_variants` (line 3368): `for ent_key in resolver_map:` +
  `ent_key.startswith(key + " ")` — 35.6M `startswith` calls resolve4.55 s
  self), no index.
- `_fuzzy_suggestions` (line 3606): per miss, rebuilds
  `ewords = set(ent_key.replace("&"," ").split())` for every one of the
  48,793 entries — 4.6M redundant `split`+set builds (matches 4.63M
  `str.split` calls), then full-map overlap math.

Both are pure functions (result depends only on inputs) and both admit exact
parity-preserving indexes. The regex metric parser (`_extract_metrics_from_sentence`,
1.22 s cum) is **not** part of this work.

## 2. Change

### 2.1 `_fuzzy_suggestions` — token posting lists (Tier 2)
Precompute once per (resolver_map (lazily, module-level `WeakKeyDictionary`
keyed by the map object, so no lifecycle changes): for each key, its
`frozenset(key.replace("&"," ").split())` and an inverted index
`token -> [(insertion_idx, key, ent)]`.

Query: `qwords = set(exact.replace("&"," ").split())`; candidates = union of
posting lists over query tokens. Any key scoring `jac >= 0.5` must share ≥1
token with the query, so the candidate set is a superset of all scorers —
skipping non-candidates cannot change the result set. Score candidates only,
then `scored.sort(reverse=True)` + top-3 dedupe, unchanged.

Parity: final list depends only on the *set* of `(jac, ent)` pairs (ties by
reverse-lexicographic `ent`, iteration-order independent), so the inverted
index yields identical suggestions.

### 2.2 `_qualifier_variants` — first-token bucketing
Precompute `{first_word: [(insertion_idx, key)]}` per resolver_map. The
entity-side extension loop's filter `ent_key.startswith(key + " ")` implies
first-token equality, so only the query's bucket needs scanning; matched keys
carry their original insertion index and are sorted by it before append,
preserving the exact output order the caller relies on
(`_resolve_qualifier_tier` consumes `[:2]` and `[2:]`).

The trailing-strip prefix (up to two `_QUALIFIER_TOKENS` strips) is unchanged.

## 3. Measurement & acceptance

- **Before** (measured 2026-11-11, cProfile, 131 files, `--workers 1`):
  31.37 s total / 26.4 s scan / 64.3M calls; `_qualifier_variants` 7.48 s
  self (cum 11.99 s); `_fuzzy_suggestions` 6.54 s self (cum 9.29 s);
  `_resolve_ladder` cum 21.45 s.
- **After** (same invocation): **11.15 s total (2.8×) / 6.07 s scan
  (4.35×) / 10.99M calls**. `_qualifier_variants` tottime 0.055 s
  (136×), cum 0.107 s; `_fuzzy_suggestions` tottime 0.271 s (24×), cum
  0.374 s; `_resolve_ladder` cum 0.661 s.
- **Parity**: per-call capture harness (823 records: 94 fuzzy + 729
  qualifier) across the full 131-file scan — 823/823 identical before/after;
  `len(quotes)=9016`, `len(metrics)=4966` unchanged; full CLI dry-run summary
  identical (quotes 160 new/8,856 unchanged; metrics 85 new/3,982 unchanged).
- **Targeted validation only** (py_compile, ruff, parity harness); full gates
  remain operator-owned (parked per 2026-11-11 instruction).

## 4. Risks

- Caches live as instance attributes on `_ResolverMap` (a `dict` subclass
  returned by `_build_resolver_map`); `weakref.WeakKeyDictionary` was
  rejected because dict-subclass keys are unhashable via `ReferenceType`.
  Foreign plain-dict maps fall back to the uncached O(N) path
  (`AttributeError` caught).
- Order semantics of `_qualifier_variants` are behavior-bearing (caller
  slices `[:2]`/`[2:]`) — bucket matches carry original insertion index and
  are sorted by it before append; parity gate covered this explicitly.
- `_fuzzy_suggestions` ordering/ties match exactly (candidate set is a
  superset of scorers; final sort by `(jac, ent)` is iteration-order
  independent) — verified by parity gate.

## 5. Fold-ins delivered (2026-11-11, #1/#2)

- **#1 regression bench** — `tests/bench_ladder_indexing.py` (synthetic
  `_ResolverMap`, 5,000 keys; 729 qualifier + 94 fuzzy calls; asserts
  per-call ceilings 5 ms / 40 ms — measured 0.005 / 0.066 ms) and
  `tests/bench_quick_wins.py` (S1 dumps ratio floor 2.5× — measured
  4.0–9.6×), both registered in `tests/run_perf_benchmarks.py` BENCHMARKS
  as `ladder_indexing` (15 s budget) and `quick_wins_json_dumps` (8 s
  budget). `make perf` run 2026-11-11: both legs pass (0.19 s / 5.13 s);
  the run's CONTENDED exit was box load (load1 5.34 > 4 cores), not leg
  failures. `derive_insights` wall now 5.18 s vs 9.0 s budget.
- **#2 static check** — `check_loop_invariant_recomputation` in
  `helpers/validators/static_checks.py` (registered in CHECKS as
  "Loop-invariant recomputation"): AST rule flagging `set()`/`split()`/
  `re.compile()` on loop-invariant operands inside `for` loops. ADVISORY
  (never blocks `make qa`) — the tree holds ~23 sites today (5 helpers,
  2 tests, 16 bench_data scratch); a hoist sweep is the follow-on. Unit
  tests in `tests/test_static_checks.py` (4 cases; 120/120 pass).
