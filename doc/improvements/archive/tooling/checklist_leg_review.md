---
title: "OCR checklist leg — host review of the 108 reviewable Python files, by checklist group"
status: executed
filed: "2026-10-10"
executed: "2026-10-10"
completed_md: "381"
area: "helpers/**, app.py, desktop/**/*.py, tests/ — the 108 rule-scanned .py files of 95caed4de..a7972551d"
---

# OCR checklist leg — host review of the 108 reviewable Python files, by checklist group

**Date:** 2026-10-10 · **Status:** PROPOSED · **Area:** the 108 rule-scanned
`.py` files in the `95caed4de..a7972551d` range.

The OCR delegation leg contributes two things and neither is findings:
the **selection** (187/480 reviewable) and a **checklist**. Every rule in
it is `source: system` — OCR carries no project rules, so the house
checklist is the only carrier (verified live again here; the doc's Known-limits
entry stands). Reviewing those 108 files against the checklist is the host's
job, and it had not been done: the earlier pass ran the scanner legs and
treated their output as the review.

Run **by checklist group** rather than by file, so each sweep is verifiable
across the whole range by grep instead of one file's worth of judgement —
and so the output answers the question that started this: *what did the
scanners miss?*

## 1. Scope and method

- **Selection:** the 108 `.py` files OCR selected for this range (both
  halves of the §1 precondition verified: 48 `tests/` paths, 60 production
  paths).
- **Diff-scoped:** findings are kept only on changed lines, the same rule
  the scan leg uses — whole-file problems are out of scope.
- **Gate dedup:** anything `make qa` already flags is not a review finding
  (ruff `E,F` blocking; `S,UP,C901` advisory). The gate's `lint-audit` is
  clean on the two new analytics modules, so the S608/S310 class below is
  genuinely review material.
- **Findings live here**, not in `doc/local/engineering/` (operator
  instruction, 2026-10-10).

### 1.1 Mechanical proxy for the checklist

The checklist groups map onto ruff rulesets the gate does **not** select, so
the sweep runs the repo's own toolchain rather than re-implementing the
rules:

| Checklist group (OCR Group 2) | ruff ruleset used |
|---|---|
| Mutable Default Arguments and Shared State | `B006`, `B008`, `B023` |
| Dead Code | `F401`, `F841`, `ARG`, `RET501`–`RET508` |
| Error Handling and Exceptions | `E722`, `S110`, `S112`, `TRY002`, `TRY003`, `TRY300`, `B904` |
| Identity and Equality Comparisons | `E721`, `PLR1714` |
| Boundary and Edge-Case Handling | `PLR2004`, `SIM` (truthiness/len), `C4` |
| Performance | `PERF`, `SIM110`, `SIM111` |
| Concurrency and Async | `ASYNC`, `S110` overlap |
| Security-Sensitive Code | `S` (already scanned — S608/S310 handled in `review_pass_findings.md` S4) |

`B` (bugbear), `PL`, `RET`, `SIM`, `PERF`, `ASYNC`, `ARG`, `TRY`, `C4` are
all outside the gate's `select = ["E","F"]` and outside the advisory
`--select S,UP,C901`. That gap is the point: a whole checklist's worth of
classes is invisible to the standing gate.

Judgement-led groups — mutable globals mutated across calls, closures over
loop variables, `None` reaching code that assumes a value, resource
lifetimes across `try`/`finally` — are **not** mechanisable and are called
out separately as unreviewed rather than claimed as clean.

## 2. Findings

Swept the off-gate rulesets over the 108 files and kept only changed lines:
**536 off-gate findings**, of which **67** are signal-class
(the rest are house-style noise: `PLC0415` deferred imports ×145,
`PLR2004` magic values ×79, unused-argument `ARG00x` ×149, long exception
messages `TRY003` ×47 — all deliberate in this codebase).

Of the 67, **2 are real defects**, **65 are benign patterns** adjudicated
against source. Dedup arithmetic per the house checklist: 536 raw − 469
house-style/benign − 2 defects = 2 distinct real findings.

### F1 — unclosed file handle (Resource Management) — MEDIUM

`helpers/analytics/agent_traces.py:1551`:

    for line in open(f, errors="replace"):

The checklist's "files opened without a `with`" item. This is the **only**
`SIM115` in the range. In practice CPython's refcounting closes the handle
when the iterator is collected at loop end, so it is a ResourceWarning-class
issue rather than an accumulating leak — but it is a real violation of the
checklist's resource-lifetime rule, and the fix is a one-line `with`. It
sits in the rollout-JSONL ingest path, which is exactly where a dropped
handle under load would matter.

**Fix:** `with open(f, errors="replace") as fh: for line in fh:`.

### F2 — lost exception cause (Error Handling) — LOW

`helpers/core/gemma_embedder.py:272`:

    except KeyError:
        raise ValueError(f"unknown gemma query task {task!r} ...")

No `from err`, so the originating `KeyError` is dropped and the traceback
the operator sees names only the `ValueError`. The checklist item is
"Original traceback lost when re-raising". LOW because the message is
self-explanatory (it names the bad task and the valid set), so no debugging
information is actually lost in practice — but the rule is explicit and the
fix is one token.

**Fix:** `raise ValueError(...) from err`.

### 2.0 Execution — both findings fixed (2026-10-10)

- **F1** `agent_traces.py:_parse_rollout` — the loop is now
  `with open(f, errors="replace") as fh:` (83 body lines re-indented under
  it). Tooth: `TestParseRolloutClosesFileHandles` feeds a good record, one
  with no `startedAt` and one malformed line, and asserts no
  `ResourceWarning` for `model-io-*`. The warning class was checked for
  sensitivity first — CPython emits it for the unclosed form and not for the
  `with` form, so the test can fail on the bug it pins. It is scoped by
  filename because `catch_warnings` is global and an unrelated test's handle
  GC'ing inside the block would otherwise fail this one. Mutant: restore the
  unclosed form -> red.
- **F2** `gemma_embedder.py:embed_query` — `except KeyError as err:` with
  `from err`. Tooth asserts `exc.value.__cause__` is the `KeyError` and the
  message still names the bad task. Mutant: drop `from err` -> red.

### 2.1 Adjudicated benign — the `B905 zip()` cluster (37 sites)

`zip(cols, row)` across `agent_traces.py`, `model_analytics.py`,
`maint_query.py`, `review_scan.py` and others. Every instance is
**length-safe by construction**: `cols` is derived from the query's own
header and `row` from that same query's `fetchall()`, so a mismatch cannot
occur. `strict=True` would be noise here. Benign.

### 2.2 Adjudicated benign — `B007` loop-var unpacking (6 sites)

`for i, (query, table, cols, bools) in enumerate(...)` where `i` is unused.
Intentional destructuring, not dead code. Benign.

### 2.3 Adjudicated benign — `PLW2901` loop-var reassignment (8 sites)

E.g. `rebuild_note_search.py:405` `a, b = (t, s) if flip else (s, t)` — a
deliberate edge-direction swap, not an accidental overwrite. Benign.

### 2.4 Adjudicated benign — `C416`/`C420`/`SIM108`/`SIM114`/`PLR1714`/`PLR1711`/`RET501`

Readability nits (dict-literal rewrites, ternary suggestion, merge
comparisons, an explicit `return None`). No correctness or security
impact. Reported as a class, not per-site.

## 2.5 Judgement-led classes — executed 2026-10-10, no findings

These were named as unreviewed when the proposal was filed. They are now
reviewed. All three globals are the checklist's *documented benign* case
("a deliberate, documented cache or sentinel"), and the checklist's
concurrency clause does not fire because concurrent invocation was checked
for and not found.

**Module-level mutable globals mutated across calls — 3 sites, benign.**

| site | what it is |
|---|---|
| `gemma_embedder.py:154` `_verified` | memo flag for a model-hash check |
| `rebuild_convo_search.py:529` `_FTS_DB_PATH` | set once at the start of a rebuild |
| `teleocr_engine.py:269` `_DICT` | documented "lazy, cached" wordlist |

Confirmed before clearing them, as the checklist requires: **no**
`ThreadPoolExecutor` / `ProcessPoolExecutor` / `multiprocessing` in the
embed path (`rebuild_note_search`, `rebuild_script_search`, `embed_cache`,
`gemma_embedder`), so the caches are not reachable from parallel workers.
A worst case is redundant computation, not a wrong answer.

**Closures capturing a loop variable — 0.** `B023` is precise here; no hit
in the 108 files.

**Resource lifetimes across `try`/`finally` — 0 new.** `SIM117` (nested
`with`) has 22 repo-wide hits but **0 on changed lines in the 108**; the
one real unclosed handle was F1, now fixed.

**`None` reaching code that assumes a value — 4 candidates, benign.**
`model_analytics.py:181` `m.get("cost", {}).items()` — a `{}` default, so
None-safe. Hand-checked the two `== 0` comparisons, which look like
float-equality defects and are not: `totals["rows"]` is a SQL `COUNT` and
`pu["reqs"]` / `pu["tools"]` are integer counters incremented with `+= 1`,
so `== 0` is exact.

**Division assumptions — 2 candidates, benign, and worth recording.**
`model_analytics.py:680` and `:1001` both divide by `len(...)`:

    buckets.setdefault(day, []).append((finished - started) / 1000)
    ...
    cell["lat"] = sum(lats) / len(lats)

`setdefault(k, []).append(x)` cannot produce an empty list — the key and
its first element are created together — so the guarded append makes
`len(lats) >= 1` an invariant rather than a hope. Not a ZeroDivisionError.
This is the reason to prefer the idiom over a bare `append` after a
`if key not in d` check.

### 2.5.1 Still not covered

The review above is diff-scoped by construction: whole-file problems outside
the changed lines of the 108 files remain out of scope, and the `--from/--to`
range means the two largest new modules were read for these five shapes only.
Nothing here is a claim that the classes are clean repo-wide.
