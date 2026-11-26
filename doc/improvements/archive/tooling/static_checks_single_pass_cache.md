---
title: "Single-pass AST walks and per-process caches for static checks"
status: "executed"
filed: "2026-11-26"
executed: "2026-11-26"
completed_md: "575"
area: helpers/validators/static_checks.py + helpers/validators/data_format_checks.py
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Single-pass AST walks and per-process caches for static checks

**Date:** filed 2026-11-26 · executed 2026-11-26 ·
**Status:** EXECUTED (completed.md #575) ·
**Area:** `helpers/validators/static_checks.py`, `helpers/validators/data_format_checks.py`

## 1. Motivation

The perf leg `static_checks` (budget **7.0s**, `tests/run_perf_benchmarks.py:265-267`) measured
**11.32s** on this box on 2026-11-26. cProfile showed the cost was not YAML or syntax work but
repeated tokenization/AST walks over the same files:

- `static_checks.py:1752 check_dead_c901_noqa` — 10.422s cumulative
- `static_checks.py:2202 check_sqlite_residue_idioms` — 8.311s; `_residue_idiom_findings` 8.214s
- `static_checks.py:1607 _c901_comment_lines` — 6.756s; `_c901_suppression_targets` 4.886s
- `ast.walk` 2,313,171 calls / 8.229s; `tokenize` 3,149,632 tokens

The same anti-pattern exists in adjacent validators: `data_format_checks.scan_zstd_violations()`
walks one parsed tree three times (docstrings, parent map, scan). The fix is mechanical:
one walk per tree/file, memoize per-process results keyed by immutable inputs, and pass already
collected data instead of re-reading.

S1–S6 were implemented in the working tree on 2026-11-26; this proposal records the design,
evidence, and validation for the whole arc.

## 2. Evidence (measured 2026-11-26, this box)

| Configuration | Result | Verdict |
|---|---|---|
| `static_checks.py` baseline, `.venv/bin/python3` | wall 11.32s, cpu 71%, cProfile 21.202s / 48,186,493 calls | baseline |
| after S1 (cache + one AST walk), `.venv/bin/python3` | wall 6.98s; `python3` 5.94s; cProfile 14.214s / 34,160,024 calls | adopt, under 7.0s budget |
| S1 findings parity | `check_dead_c901_noqa` + `check_sqlite_residue_idioms` JSON before/after `diff` empty; full `static_checks` output diff empty except dirty-gated file count 5→6 because the modified file itself became dirty | no semantic change |
| after S7 (defect-pattern prefilter), `.venv/bin/python3` | quiet wall 6.11–6.50s; cProfile 13.439s / 30,816,062 calls (`check_sqlite_residue_idioms` 5.649→4.240s, `ast.walk` 1,575,033→1,153,430) | adopt; intermediate loaded-box 7.57–8.64s outliers parked |

S1 alone brings the measured leg under budget on a quiet box; S2-S6 remove the same structural
waste from adjacent walkers and make the design consistent. No query-visible semantics are
touched (validators only), so no eval-gate bullet is required.

## 3. Design

All caches are **per-process** (`@functools.cache`), not persistent DuckDB/SQLite caches. `static_checks`
is a short-lived CLI, so there is no TTL problem; callers accept `.cache_clear()` if a test mutates a
scanned file in-process.

### S1 — `static_checks` C901/residue one-pass + token cache (already landed)
- `_c901_comment_lines(path)` (`helpers/validators/static_checks.py:1608`) `@functools.cache`.
- `check_dead_c901_noqa()` builds `comment_lines_by_rel`; `_strip_c901` and
  `_c901_notes_for_file` reuse it (`_c901_notes_for_file` takes `comment_lines` explicitly).
- `_residue_idiom_findings()` collects `docstrings` + `string_nodes` in one `ast.walk`, preserving
  output order and the early `_SQLISH_RE`/`defer_foreign_keys` return.

### S2 — collapse nested `ast.walk(arg)` in the embedding-decode tripwire
`_flag_json_loads_on_embedding_receivers()` (`helpers/validators/static_checks.py:1454`) walks
`arg` three times (`:1474` Call check, `:1476` `Name` ids, `:1477` `Attribute` attrs). One
`ast.walk(arg)` collects `has_call`, `idents`, `attrs`; emitted finding is unchanged
(`ast.get_source_segment(src, arg)`).

### S3 — immutable per-process caches for def lines and suppression targets
- `_def_lines(src)` (`:1635`): `@functools.cache`, return `dict[str, frozenset[int]]` so cached
  values cannot be mutated. `_c901_notes_for_file` only does membership/`items()`, so no caller
  change beyond the type annotation.
- `_c901_suppression_targets()` (`:1716`): returns an immutable `tuple[Path, ...]`; the
  cached leg `_suppression_targets_for_root(root)` is `@functools.cache` and **keyed by the
  current root** (tests swap `REPO_ROOT`; a no-key global cache would cross-contaminate).
  `check_dead_c901_noqa` iterates the tuple, never mutates.

### S4 — read each C901 target once; cache enclosing-function lookup
- `check_dead_c901_noqa()` currently reads a target source for `_def_lines` and again for
  `_strip_c901`. Build `src_by_rel` once and pass it to both.
- `_enclosing_function(path, lineno)` (`:1816`) re-parses the file for every misplaced
  suppression; cache by `(path, lineno)` and return `str | None`. No mutable output.

### S5 — single-pass AST in `data_format_checks.scan_zstd_violations`
`helpers/validators/data_format_checks.py:126` (`_docstring_spans`), `:145` (parent map), and
`:156` (scan) each walk the same `tree`. One `ast.walk(tree)` collects `docstrings`, `parents`,
and `scan_nodes`; define `fn_of` after `parents` is complete, then process `scan_nodes` in
`ast.walk` order. `_docstring_spans` may be kept as a helper if tests reference it.

### S6 — shared parsed-AST cache in `data_format_checks`
Add `_parsed_python(path: Path) -> ast.Module | None` with `@functools.cache` (SyntaxError →
`None`), keyed by `Path` (test tmp paths are unique; repo paths are absolute). Use it in both
`scan_zstd_violations()` and `scan_arrow_violations()` so `DATA_LANE_MODULES` are not parsed
twice in a `check_data_format()` run. Callers do not mutate the cached AST.

### S7 — defect-pattern raw-text prefilter before AST parse (landed)
`_residue_idiom_findings()` used a coarse `SELECT|INSERT|…` gate: 270/289 helper files passed
it and paid for `ast.parse` + `ast.walk`, while only the three defect regexes can actually emit
findings. `_raw_source_has_residue_candidate()` now checks those three regexes against raw
source, then tokenizes only on a miss so implicit concatenation (`"IS NOT " "?"`) and
`FSTRING_MIDDLE` literal chunks stay candidates. Fragment-free tokens are quote-stripped
without `ast.literal_eval`; tokenize failures return `True` conservatively and `ast.parse`
remains the authority. The whole prefilter is a superset of real findings — parity is the guard.

### Explicitly not slices
- `helpers/maintenance/rebuild_script_search.py:318 _iter_statements` is the existing precedent
  (statement-list walk reached the same imports without entering expressions, measured 1.44M
  `ast.walk` nodes / 6.2s on ~660 units). It is already the canonical import walker; no new slice.
- Persistent content-addressed caches (`rebuild_script_search._mojo_decl_cached`, `_unit_hash`)
  stay in their own lane; no on-disk cache for C901 comments.
- No `rglob("*.py")` list sharing across validators: output/dirty-gating semantics differ and the
  gain is unmeasured.

## 4. Acceptance criteria & shakedown

1. `.venv/bin/python3 -m pytest tests/test_static_checks.py tests/test_parity_harness.py
   tests/test_data_format_checks.py -q` — all green.
2. `make lint-audit` and `make static-checks` — green; `make qa` not rerun in this arc (no second
   full gate run).
3. Findings parity: before/after JSON for `check_dead_c901_noqa`, `check_sqlite_residue_idioms`,
   `scan_zstd_violations`, `scan_arrow_violations` and `check_data_format()` must be identical.
4. Timing: standalone `helpers/validators/static_checks.py` stays ≤7.0s on a quiet box; repeated
   at least twice. cProfile call count must decrease from the S1 baseline (34,160,024 calls).
5. `make search-fresh APPLY=1 ARGS=--allow-stale` then plain `make search-fresh` (rc=0) after
   doc changes.

| Projected outcome | Today | After |
|---|---|---|
| `static_checks` wall (quiet box) | 11.32s → 6.98s after S1 | 6.07 / 6.10s (two quiet runs; one loaded-box outlier 7.58s) |
| `static_checks` cProfile calls | 48.2M → 34.2M after S1 → 33.43M after S2–S6 | 30.82M (30,816,062) |
| `check_sqlite_residue_idioms` cumtime | 8.214s (baseline `_residue_idiom_findings`) → 5.649s after S2–S6 | 4.240s after S7 |
| `data_format_checks` AST walks per file | 3 (zstd) + 1 (arrow) | 1 shared parse + one scan walk; findings parity OK |

## 5. Risks

- **Mutable cached values** — `_def_lines` returns frozensets; `_c901_comment_lines` returns a
  `dict` with a documented “do not mutate” contract (existing callers never mutate).
- **Stale cache after in-process file edits** — validators don't edit repo `.py`; tests using
  tmp files get unique `Path`s. `.cache_clear()` is available for future mutation-shaped tests.
- **`ast.walk` ordering** — S1/S5 preserve `ast.walk` order by collecting nodes during the walk
  and emitting after prerequisite sets are complete; parity diff is the guard.
- **Path-key normalization** — `_c901_comment_lines` currently keys raw `Path`; all production
  call sites use the resolved `REPO_ROOT`. Future callers should pass absolute paths.
- **Caching `{}` on errors** — `_c901_comment_lines` caches the empty result for `SyntaxError`/
  `TokenError`/`UnicodeDecodeError`/`OSError`; acceptable for a short-lived CLI.

## 6. Non-goals

- No query-visible semantics, rosters, extractors, or store writes.
- No patch lifecycle operations (no `stg new`/`push`/`pop`/`delete`, no commit/amend).
- No edits to pre-existing dirty files (`doc/improvements/pending.md`, `helpers/maintenance/db_maint.py`,
  `maint.py`, `snapshot_db.py`, `store_sweep.py`, `tests/test_store_sweep.py`).
- No full perf/qa rerun; timing failures on a loaded box are parked per `doc/procedures/gates.md`.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-26 | `/usr/bin/time -f 'wall=%e cpu=%P' .venv/bin/python3 helpers/validators/static_checks.py` | `wall=11.32 cpu=71%` | baseline, dirty-gated 5 python files |
| 2026-11-26 | `.venv/bin/python3 -m cProfile -s cumulative helpers/validators/static_checks.py` | 21.202s / 48,186,493 calls | `/mnt/data/tmp/static_checks_cprofile.txt` |
| 2026-11-26 | same `/usr/bin/time` after S1 | `wall=6.98 cpu=96%` (`.venv`); `wall=5.94 cpu=101%` (`python3`) | budget 7.0s; dirty-gated 6 files because `static_checks.py` is modified |
| 2026-11-26 | cProfile after S1 | 14.214s / 34,160,024 calls | `/mnt/data/tmp/static_checks_after_cprofile.txt` |
| 2026-11-26 | `.venv/bin/python3 -m pytest tests/test_static_checks.py tests/test_parity_harness.py -q` | 149 passed | after S1 |
| 2026-11-26 | `make lint-audit`, `make static-checks` | passed | after S1; `make static-checks` includes license-check |
| 2026-11-26 | after S2-S6: `/usr/bin/time` ×4, `.venv/bin/python3 helpers/validators/static_checks.py` | 7.58s, 6.87s, 6.07s, 6.10s | first two loaded-box; last two quiet and under 7.0s budget |
| 2026-11-26 | cProfile after S2-S6 | 33,426,373 calls / 15.910s | `/mnt/data/tmp/static_checks_final_cprofile.txt`; down from 34,160,024 after S1 |
| 2026-11-26 | `.venv/bin/python3 -m pytest tests/test_static_checks.py tests/test_parity_harness.py tests/test_data_format_checks.py -q` | 159 passed | after S2-S6 |
| 2026-11-26 | scratch parity script, HEAD vs working tree | `static_checks: PARITY=OK`, `data_format_checks: PARITY=OK` | `/mnt/data/tmp/static_checks_parity.py`; old modules re-rooted from scratch path to the real repo |
| 2026-11-26 | `make lint-audit`, `make static-checks` after S2-S6 | passed | C901 helper extraction kept `_flag_json_loads_on_embedding_receivers` under threshold (no new noqa) |
| 2026-11-26 | `make search-fresh APPLY=1 ARGS=--allow-stale`, then plain `make search-fresh` | all indexes FRESH | doc-index convergence |
| 2026-11-26 | after S7: `/usr/bin/time` ×4, `.venv/bin/python3 helpers/validators/static_checks.py` | 6.17s, 6.11s, 6.50s, 6.47s | quiet box, all under 7.0s budget; earlier 7.57–8.64s runs were loaded-box outliers |
| 2026-11-26 | cProfile after S7 | 30,816,062 calls / 13.439s | `/mnt/data/tmp/static_checks_s7_final_cprofile.txt`; `check_sqlite_residue_idioms` 4.240s, `ast.walk` 1,153,430 |
| 2026-11-26 | `.venv/bin/python3 -m pytest tests/test_static_checks.py tests/test_parity_harness.py tests/test_data_format_checks.py -q` | 161 passed | after S7; +2 tests (prefilter skips AST, adjacent literals still flagged) |
| 2026-11-26 | scratch parity script after S7 | `static_checks: PARITY=OK`, `data_format_checks: PARITY=OK` | `/mnt/data/tmp/static_checks_parity.py` |
| 2026-11-26 | `make lint-audit` after S7 | passed | extracted `_fstring_middle_is_candidate` / `_string_token_candidate` to keep the prefilter under C901 threshold |
| 2026-11-26 | `make static-checks`, `make md-lint` after S7 | passed | 3 non-blocking advisories, 0 lint violations |
