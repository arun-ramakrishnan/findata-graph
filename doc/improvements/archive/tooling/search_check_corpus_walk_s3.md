---
title: "Search --check corpus walks: hash-only verdicts for script and git (18x / 146x)"
status: executed
filed: "2026-11-18"
executed: "2026-11-18"
completed_md: "542"
area: "helpers/maintenance"
---

# Search `--check` corpus walks: hash-only verdicts for script and git

**Date:** 2026-11-18 · **Status:** PROPOSED ·
**Area:** `helpers/maintenance/rebuild_script_search.py`, `helpers/maintenance/rebuild_git_search.py`

## 1. Motivation

`--check` is the freshness verdict every search surface runs. Five of them are
serially invoked by `make search-fresh` (Makefile:570-585) and by the advisory
gate legs `doc-search-check` / `script-search-check` / `note-search-check` /
`memory-search-check` (tests/run_gate_report.py:416-430).

The verdict for each is a content-hash comparison of the corpus against a
stored meta table. Two of the five currently pay for a **full extraction** —
AST parsing, docstring splitting, def/import collection, or a whole-history
git diff walk — and then throw every extracted field away, keeping only
`(rel, mtime, blake2b)`.

Measured on this box 2026-11-18, warm caches, nothing changed on disk:

| Leg | `--check` wall | What it computes that it never reads | Share wasted |
|---|---|---|---|
| `doc_search` | 0.27 s | nothing (already hash-only) | 0% |
| `script_search` | **3.18 s** | `_extract_py_unit` on 648 files: `ast.parse`, `_split_docstring`, `_top_level_names`, `_import_candidates` | ~94% |
| `note_search` | 1.37 s | nothing (walks + hashes only) | 0% |
| `memory_search` | 0.48 s | nothing (walks + hashes only) | 0% |
| `git_search` | **7.23 s** | `_numstats` over 595 commits: one `git log --numstat --no-textconv` → 1.2 MB | ~96% |

Total `make search-fresh` on a clean tree: **12.5 s, of which ~10.2 s is
discarded work.** The needle has not moved since the DuckDB fold — the
watchlist recorded `L5` (rebuild cadence) as a *rebuild* concern, but the
`--check` path has the same shape of waste and nobody measured it.

Two smaller triggers, both from this session's sweep:

- The git leg's cost is **linear in history, not in drift**: 10 ms/commit
  (`-50` 627 ms, `-100` 1.28 s, `-250` 2.75 s, `-595` 5.98 s). At 1,200
  commits the check alone crosses 12 s.
- `script-search-check` is 94% of the advisory legs' combined runtime, and it
  is a leg that runs on every gate arc.

## 2. Evidence (measured 2026-11-18, this box)

### 2.1 script: the AST is not an input to the verdict

`_check_freshness_script_duckdb` (rebuild_script_search.py:2252) calls
`_collect_units(...)` (rebuild_script_search.py:1142), which calls
`_extract_py_unit` (rebuild_script_search.py:322) per file.
`_source_staleness` (rebuild_script_search.py:1346) then compares only
`(rel, mtime, hash)` triples — its docstring says so: *"mtime is only the
carry hint, never verdict input"*.

| Configuration | Result | Verdict |
|---|---|---|
| current `--check` corpus walk (conn cached, decls cached) | 2,909 ms | baseline |
| hash-only walk over the same 705 units (cached mojo/ts decls) | **178 ms** | **adopt — 18×** |

`by_kind` — the only other output of the walk — comes from `_iter_py_units`
alone, no AST needed. Verified: `{'script': 277, 'test': 371}`.

### 2.2 git: the numstat pass is not an input to the verdict either

`rebuild_git_duckdb` (rebuild_git_search.py:686) calls `_numstats(root)`
(rebuild_git_search.py:288) at line 716 — **before** the `if not write`
branch at line 722, so `--check` pays it unconditionally.

cProfile of `rebuild_git_search.py --check`, 6.160 s total:

```text
 1  6.113  6.113  rebuild_git_search.py:686(rebuild_git_duckdb)
 1  0.036  6.010  rebuild_git_search.py:288(_numstats)
 3  0.000  5.975  rebuild_git_search.py:236(_git)
 1252  5.942  {method 'poll' of 'select.poll' objects}
```

5.94 s is the git subprocess itself, not DuckDB, not Python.

| Configuration | Result | Verdict |
|---|---|---|
| `git log --numstat --no-textconv main` (current) | 5,962 ms / 1,225,244 B | baseline |
| `git rev-list main` + stored-sha compare (prototype) | **42.1 ms** | **adopt — 146×** |
| `git log --numstat --no-walk <5 shas>` (delta form) | 63.6 ms | the fallback when a delta exists |

The prototype was run against the live store:

```text
PROTOTYPE fast check:   42.1 ms
  stored=595 live=595 equal=True
CURRENT check cost: ~6160 ms  =>  speedup 146x when nothing changed
```

`_fp` (rebuild_git_search.py:367) is
`blake2b(f"v{EXCLUDE_VERSION}|cap{DIFF_CAP}\n{message}\n{files_block}")`.
Its docstring states the invariant that makes the short-circuit sound:
*"A sha's message/paths/diff are immutable; what can drift is the extraction
policy — the exclude list and the cap."* So a sha whose **immutable** inputs
are unchanged can only have drifted if the policy changed — which a stored
policy stamp detects in O(1).

## 3. Design

Both slices are the same structural move: **let the verdict decide what to
compute.** S1 and S2 are independent and separately landable.

### S1 — `script_search --check` meta-only walk

Add `meta_only: bool = False` to `_collect_units`. When set, the Python loop
yields `(rel, mtime, blake2b)` and skips `_extract_py_unit` entirely; the
Mojo/TS loops already compute the hash before calling
`_mojo_decl_cached` / `_ts_decl_cached`, so they keep the cache path (their
decl extraction is already cached and ~2 ms total). `by_kind` is produced
from the iterator's `kind` field, which needs no AST.

`_check_freshness_script_duckdb` passes `meta_only=True`. The write paths
(`rebuild`, the two `_collect_units` calls at :1415 / :2203) keep
`meta_only=False`.

Return type: keep `_collect_units` returning `(py_units, make_unit,
units_meta)` and return an empty `py_units` list when `meta_only` — callers
that asked for meta must not read unit bodies. Rejected alternative: a
separate `_collect_unit_meta()` function, because the walk and the
Mojo/TS cache plumbing would be duplicated and drift.

### S2 — `git_search --check` sha-set short-circuit

1. Stamp the extraction policy once, into `git_search_info`:
   `key='fp_policy'`, `value=f"v{EXCLUDE_VERSION}|cap{DIFF_CAP}|fdc{_FILES_DISPLAY_CAP}|ex{_EXCLUDE_DIRS}|{_EXCLUDE_SUFFIXES}"`.
   Written by the full/incremental writer alongside `embed_basis` /
   `body_basis`.
2. In the `not write` branch, compare `stored_shas == set(git rev-list REF)`
   **and** the stored `fp_policy` against the current one.
   - Equal and equal → FRESH, return immediately.
   - Otherwise → fall through to the existing numstat-based path, but scope
     the numstat call to the divergent shas only
     (`_numstats(root, shas=[...])` → `git log --numstat --no-walk`), which
     is 63.6 ms for 5 commits instead of 5,962 ms for 595.
3. `_numstats` gains an optional `shas` parameter; its default stays "all of
   REF" so the write path is untouched.

The policy stamp is the safety interlock. Without it, bumping
`EXCLUDE_VERSION` would leave every stored `fp` stale while the sha set still
matched, and the check would report FRESH on an index that needs a full
re-extract. Recording the policy makes that case a fast, correct MISMATCH.
A store predating the stamp reads as "policy unknown" → full path once, then
stamped — the same absent-store-is-all-new posture the leg already takes.

**S2 explicitly does NOT touch the write path's `_numstats` cost.** A real
incremental apply still walks all history once to build the fingerprint
inputs. That is `L5` territory and a different slice.

## 4. Acceptance criteria & shakedown

1. `make script-search-check` on a clean tree completes in ≤ 0.5 s
   (measured baseline 3.18 s) and prints `index state: FRESH` — 3 consecutive
   runs, median reported, never one run.
2. `make git-search-check` (`make git-fresh`) on a clean tree completes in
   ≤ 0.3 s (baseline 7.23 s) and prints the FRESH verdict — 3 consecutive
   runs.
3. Freshness verdicts are **byte-identical** to the pre-change behaviour on
   the same tree, for both legs. Prove by capturing the `--check` stderr on a
   clean tree before and after; the strings must match exactly.
4. Drift is still detected, and detected *correctly*:
   - touch one `helpers/**.py` → `script --check` exits 1 and names that file;
   - add one commit → `git --check` exits 1 and names that sha in `stale_new`.
5. `git_search --check` on a store with a *stale* `fp_policy` stamp must
   report stale (the interlock) — new regression test.
6. `make search-fresh` (check mode) exits 0 on a clean tree, unchanged.
7. Gates stay green: `make lint-audit`, `make types`, `make static-checks`,
   `ruff format --check`.
8. Targeted tests only, per gate doctrine:
   `pytest tests/test_rebuild_script_search.py tests/test_rebuild_script_search_mojo.py tests/test_rebuild_script_search_ts.py tests/test_git_search_index.py -q`.

| Projected outcome | Today | After |
|---|---|---|
| `script-search-check` (clean tree) | 3.18 s | ~0.25 s |
| `git-search-check` (clean tree, 595 commits) | 7.23 s | ~0.10 s |
| `make search-fresh` total | 12.5 s | ~2.4 s |
| git check cost at 1,200 commits | ~12 s | ~0.1 s (flat) |

## 4b. Outcome (measured 2026-11-18, this box — S1 + S2 landed)

| Criterion | Result |
|---|---|
| S1 script meta_only vs full `units_meta` | byte-identical on the live tree; kind sets identical (`mojo/script/test/ts`) |
| S1 script `--check`, 3 runs | **0.34 / 0.34 / 0.33 s** (was 3.18 s) — criterion 1 met |
| S1 drift proof | touching `rebuild_script_search.py` → `index state: STALE — 1 changed` naming the file |
| S2 git delta path (scratch store stamped, live repo stale 1 new/1 del) | **106.6 / 77.0 / 76.3 ms**, median 77 ms vs 6,890 ms baseline — **89×** — and the verdict names the exact new/deleted shas |
| S2 git fast path | proven by `TestFastPathPolicyStamp::test_fresh_store_takes_fast_path_and_stamps_policy` (`fast_path: True`, all three stale lists empty) |
| S2 policy interlock | `test_policy_bump_reads_stale_even_with_matching_shas`: `EXCLUDE_VERSION` bump with an unchanged sha set → `index_stale is True`, `stale_changed` non-empty |
| S2 missing-stamp store | `test_missing_policy_stamp_reads_as_stale`: pre-S2 store takes the slow path (and is re-stamped on the next APPLY) |
| Targeted tests | `tests/test_git_search_index.py tests/test_git_query_duckdb.py tests/test_rebuild_script_search*.py` → **79 passed** |
| Live `git --check` still 6.9 s | expected: the live store predates the `fp_policy` stamp, so the check takes the full-numstat path until the operator's next `make search-fresh APPLY=1` re-stamps it. Not a defect — the design requires the stamp to be writer-produced. |

**Design change vs the proposal text:** the scoped-numstat form
(`_numstats(shas=[...])` via `--no-walk`, §3 S2 step 2) was prototyped
(63.6 ms for 5 commits) and then **dropped** — with the `fp_policy` stamp in
place the check needs either zero git calls (fast path) or a full re-derive
(policy mismatch), so the scoped form had no caller left. `_numstats` is
back to its pre-proposal signature; the reasoning is recorded in its
docstring.

## 5. Risks

- **The short-circuit could mask a real drift** if some fp input is not
  covered by the sha set or the policy stamp. Mitigation: `_fp`'s docstring
  already narrows the inputs to `(message, files_block, policy)`, and
  `message`/`paths` are immutable per sha by git's own object model — an
  amended commit is a *different* sha, which changes the set. Criterion 5
  adds the policy-mismatch regression test that closes the remaining hole.
- **`meta_only` could drift from the real extraction** if the hash definition
  changes on one side only. Mitigation: both sides call the same
  `hashlib.blake2b(src.encode(...), digest_size=8)` expression; extract it to
  one helper so there is exactly one definition.
- **`_numstats(shas=...)` changes a shared function** used by the write path.
  Mitigation: the parameter is optional and defaults to current behaviour;
  criterion 3 (byte-identical verdict) covers the write path indirectly
  through the check path.

## 6. Non-goals

- **No incremental carry for `note_search`.** That is `L5` and a much larger
  slice (`rebuild_note_search.py:871` documents replacement rebuild as
  deliberate: *"the corpus is ~16k rows; full rebuild is seconds"*). Filing
  separately if the note rebuild ever becomes a real cost.
- **No change to what `--check` considers stale.** Hash doctrine, mtime-as-
  hint-only, and the worktree/checkout mtime rule (2026-08-30) all stand
  untouched.
- **No reduction of the write path's extraction cost.** Real rebuilds keep
  parsing ASTs and walking history; only the verdict gets cheaper.
- **No change to the `match_bm25` macro fallback** — that is a separate
  proposal (`fts_query_token_parity.md`).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-18 | `for f in doc script note memory; do ...rebuild_${f}_search.py --check; done` | 0.27 / 3.18 / 1.37 / 0.48 s | warm, clean tree |
| 2026-11-18 | `make search-fresh` | doc 443ms, script 3,609ms, note 2,112ms, memory 1,458ms, git 7,233ms | all FRESH |
| 2026-11-18 | cProfile `rebuild_script_search --check` | 7.904 s (cold conn) → 648× `_extract_py_unit` = 7.590 s, `_import_candidates` = 5.827 s | AST dominates |
| 2026-11-18 | prototype hash-only py walk | 63 ms / 648 units | no AST |
| 2026-11-18 | prototype hash-only full walk (cached decls) | 178.5 ms / 705 units | vs 2,909 ms = 18× |
| 2026-11-18 | cProfile `rebuild_git_search --check` | 6.160 s; `_numstats` 6.010 s; `select.poll` 5.942 s | subprocess, not duckdb |
| 2026-11-18 | `for i in 1 2 3; do ...rebuild_git_search.py --check; done` | 6.98 / 6.89 / 7.12 s | median 6.98 s, clean tree |
| 2026-11-18 | `git log --numstat --no-textconv main` | 5,962 ms / 1,225,244 B | |
| 2026-11-18 | `git log --numstat --no-textconv -{50,100,250,595}` | 627 / 1,283 / 2,748 / 5,980 ms | linear ~10 ms/commit |
| 2026-11-18 | prototype sha-set + policy compare | 42.1 ms; stored=595 live=595 equal=True | 146× |
| 2026-11-18 | `for i in 1 2 3; do ...rebuild_script_search.py --check; done` | 0.34 / 0.34 / 0.33 s | S1 landed (was 3.18 s) |
| 2026-11-18 | meta_only vs full `_collect_units` | `units_meta` byte-identical; 80.8 ms vs 54,487 ms uncached | S1 correctness |
| 2026-11-18 | `R.rebuild(scratch, write=False)` ×3, store stamped, live repo stale | 106.6 / 77.0 / 76.3 ms; verdict names 1 new + 1 deleted sha | S2 delta path, 89× vs 6,890 ms |
| 2026-11-18 | `pytest tests/test_git_search_index.py` | 38 passed (5 new in `TestFastPathPolicyStamp`) | criterion 5 interlock |
| 2026-11-18 | `pytest tests/test_git_search_index.py tests/test_git_query_duckdb.py tests/test_rebuild_script_search*.py` | 79 passed | combined sweep |
| 2026-11-18 | live `rebuild_git_search --check` ×3 after S2 | 6.86 / 6.89 / 7.03 s | unchanged: store predates the fp_policy stamp |
| 2026-11-18 | `git log --numstat --no-walk <5 shas>` | 63.6 ms / 2,549 B | delta-form cost |
| 2026-11-18 | `git rev-list main` | 4.5 ms / 24,395 B, n=595 | |
| 2026-11-18 | `by_kind` from `_iter_py_units` alone | `{'script': 277, 'test': 371}` | no AST needed |
| 2026-11-18 | `SELECT key FROM git_search_info` | `embed_model, embed_dims, embed_basis, body_basis` | no policy stamp today |
