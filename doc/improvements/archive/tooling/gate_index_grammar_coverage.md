---
title: "Gate index grammar coverage — grammar-gap diagnosis banked; S1–S3 designed, not adopted"
status: executed
filed: "2026-09-26"
executed: "2026-09-26"
completed_md: "302"
area: "helpers/misc/gate_query.py, helpers/misc/search_tui.py (shared report grammar), outputs rotation"
---

# Gate index grammar coverage — grammar-gap diagnosis banked; S1–S3 designed, not adopted

**Date:** 2026-09-26 · **Status:** EXECUTED 2026-09-26 (completed.md #302 —
born-archived, concluded without implementation) ·
**Area:** helpers/misc/gate_query.py + helpers/misc/search_tui.py (shared report grammar), outputs rotation

> **Record (2026-09-26):** concluded at operator review WITHOUT
> implementation — the diagnosis stands and the S1–S3 design is banked.
> Measured state at close is unchanged: refresh 1.29 s median, 283
> integrity/verify runs unindexed, rotation still blind; the rescan
> keeps growing ~9.5 MB/day until S1 lands. Revisit trigger recorded
> in pending.md.

## 1. Motivation

Trigger: operator report, 2026-09-26 — "massive slowing in `gate_query
refresh`, and it is an incremental refresh, not even full."

The refresh is incremental in mechanism but not in cost. `refresh()`
(gate_query.py:851) advances a per-file byte offset in `parse_state`
only when the file yields parseable run blocks, and a block only starts
at a header matching `_GATE_HEADER_RE` / `_PERF_HEADER_RE`
(search_tui.py:603,617 — `# make <target> — gate|maint|benchmark
report`). Three of the thirteen indexed report copies use a different
title format, yield **zero** blocks, and therefore store
`parse_offset = 0` back on every refresh — silently (no runs, no
parse_errors, no pending tails). Every refresh re-reads, decodes, and
line-scans them in full:

| file | size | parse_offset | runs indexed | title the writer emits |
|---|---|---|---|---|
| outputs/database_integrity_report.md | 45.5 MB | 0 | 0 of 116 | `# FinData Knowledge Graph — Database Integrity Report` |
| outputs/wt/graph_algos/…/database_integrity_report.md | 1.77 MB | 0 | 0 of 65 | same |
| outputs/verify_notes_report.md | 37 KB | 0 | 0 of 102 | `# FinData Knowledge Graph — Notes Verification Report` |

The three files are 47.3 MB of the 48.9 MB corpus — 97% of all bytes —
so "incremental" refresh is effectively a full O(corpus) rescan on
every invocation. Worse, `main()` (gate_query.py:2118) auto-refreshes
before **every** subcommand, so `latest`, `failures`, `timing`, `grep`
all pay it.

The growth is accelerating and the safety net cannot catch it. The
integrity writer appends a full report per run; the per-run size grew
16 KB (first 10 runs) → 735 KB (last 10 runs). At ~13 integrity
runs/day the file gains ~9.5 MB/day, so the rescan cost grows ~0.1–0.2 s
**per day**. `cmd_rotate` (gate_query.py:1941) picks cut points from
the `runs` table, which has zero rows for these files — rotation
structurally cannot trim them. Left alone: multi-second gate_query
invocations within weeks, ~300 MB file within a month.

There is also a correctness loss hiding under the perf issue: the
`integrity` and `verify` gates are invisible to the index — no
`latest --gate integrity`, no `failures`, no `timing` rows — even
though search_tui has had run-history parsers for both formats since
the #265 UX pass.

## 2. Evidence (measured 2026-09-26, this box)

| Measurement | Result | Verdict |
|---|---|---|
| `gate_query refresh` wall (3 runs, no new bytes) | 1.33 / 1.29 / 1.27 s (median 1.29 s) | baseline |
| `gate_query latest` wall (3 runs) | 1.34 / 1.36 / 1.33 s | auto-refresh dominates; query itself negligible |
| import-only (`import gate_query`) | 0.19–0.22 s | fixed overhead; refresh work ≈ 1.1 s |
| cProfile refresh | 2.51 s total; `_split_blocks` 1.91 s cumtime, 1.52 M `re.match` calls; 4.88 M calls | rescan is the cost |
| decode + split of the 45.5 MB file alone | 0.071 s + 0.553 s → **0 blocks found** | grammar mismatch confirmed |
| `parse_state` rows for the three files | offset 0, size == current size, `updated_at` = today | stuck, rewritten every refresh |
| runs per block-header count | integrity 116, wt integrity 65, verify 102 | all dropped on the floor |
| per-run integrity size trend | first 10 avg 16 KB; last 10 avg 735 KB | growth is accelerating |
| bare `python3 helpers/misc/gate_query.py refresh` | `ModuleNotFoundError: duckdb` (0.08 s) | not the cause; venv python used for all measurements |

Ruled out: DB lock contention (`witr -f` — no holder), DB path
mismatch (`ROOT = outputs/`, DB exists at `outputs/gate_runs.duckdb`),
pathological regex backtracking (linear scan, no catastrophic
backtracking), junit parsing on the hot path (fires only for newly
stored runs, and none are stored). Measured — do not re-audit.

## 3. Design

**Chosen mechanism: teach the shared report grammar the two writer
formats and index them, reusing the parsers search_tui already has.**
search_tui is the grammar home (it owns `_REPORTS`, the header
regexes, and both run-history parsers); gate_query becomes a second
consumer of that grammar instead of a divergent one.

- **S1 — grammar + indexing (core).**
  1. search_tui gains two title regexes: `_INTEGRITY_TITLE_RE`
     (`# FinData Knowledge Graph — Database Integrity Report`) and
     `_VERIFY_TITLE_RE` (Notes Verification variant). gate_query's
     `_split_blocks` recognizes them and emits kinds `integrity` /
     `verify` with the same byte-offset bookkeeping it already does.
  2. Factor the inline per-block loop of `parse_verify_runs` into
     `st._parse_verify_chunk(lines) -> VerifyRun` (pure refactor; TUI
     behavior unchanged). Integrity already has the line-based
     `st._parse_integrity_chunk`.
  3. gate_query `_parse_block` dispatches the two new kinds;
     `_store_run` gains per-kind mapping:
     - integrity: one run per `#`-block; `generated_at`/`started_at`
       from `**Generated:**`, `elapsed_s` from `**Elapsed:**` when
       present; sections → legs (label = section heading, status
       FAIL iff severity ERROR and its `-> errors=N` > 0, `err_head`
       = first detail lines capped like `_leg_err_head`,
       `seconds` NULL); summary mirrors the gate shape
       (`N/M checks ok · integrity PASS|FAIL`); `exit_code` 0/1 by
       ERROR-level total.
     - verify: one run per `#`-block; metrics from the table rows,
       `### bucket (N)` sections → legs, `## Verdict` line into the
       summary; `exit_code` 0/1 by errors.
  4. Kind-aware completeness (pending-tail rule): integrity last
     block complete iff `**Ended:**` present, verify last block
     complete iff `## Verdict` present; non-last blocks always
     complete (same rule as gate blocks today). `_extra_meta` and
     `_warning_records` stay gate/perf-only — integrity sections
     (`## LABEL (SEVERITY…)`) must not be mis-ingested as warnings.
     junit probing is skipped for the new kinds (no artifacts exist).
- **S2 — rotation catch-up.** With runs indexed, `gate_query rotate
  --apply` finally sees the integrity corpus file; archive the prefix
  (keep default 30 runs → ~22 MB at current per-run size).
  `cmd_rotate` already shifts `parse_state` offsets; verify with a
  post-rotate refresh + `latest`.
- **S3 — loud zero-block invariant.** refresh() warns when a
  `_REPORTS` file yields zero blocks over newly-read bytes (the exact
  silent-failure class this arc closes; guards future writer drift).
  Fits the #288 diagnostics theme.

**Alternatives rejected:**

- *Switch the two writers to the `# make <target> — gate report`
  format.* The body is genuinely not leg-shaped (severity-tagged
  sections, metric tables, per-bucket issues); forcing it through the
  leg grammar loses data, and it breaks or dual-maintains search_tui's
  working parsers. Lost to S1's reuse.
- *Skip-mark zero-block files (advance offset to EOF when nothing
  matches).* One line, kills the rescan — and entrenches the blind
  spot: the gates stay unindexed forever and the data loss stays
  silent. This is the fallback if S1 is rejected, not the fix.
- *Change `main()` to not auto-refresh.* Treats the symptom; the
  rescan still dominates explicit `refresh`, and the auto-refresh is
  what keeps query answers current.

## 4. Acceptance criteria & shakedown

1. Steady-state cost: median wall of 5 consecutive `gate_query
   refresh` runs with no new bytes ≤ 0.35 s (today: 1.29 s).
2. Coverage: after the first post-S1 refresh, `gate_query latest
   --gate all` lists `integrity` and `verify` rows; `SELECT count(*)`
   ≥ 110 runs for gate `integrity` (116 blocks in file) and ≥ 95 for
   `verify` (102).
3. Failures path: `gate_query failures` surfaces ERROR-level integrity
   sections as failing legs on a seeded failing fixture;
   `gate_query timing` tolerates NULL leg seconds (no crash, legs
   simply excluded from the critical path).
4. Incrementality (test): append a synthetic run block to a tmp-corpus
   report; a second refresh parses only the appended bytes (offset
   delta assertion) and stores exactly one run.
5. Pending tail (test): an unterminated last integrity block (no
   `**Ended:**`, no following header) is skipped, offset stops at its
   start, and the skipped_pending counter fires the existing
   main() warning.
6. Rotation (S2): `rotate --apply` shrinks the corpus integrity file
   to ≤ 25 MB; an immediate refresh + `latest` stays consistent and
   archived rows carry `arch_path`.
7. Gates stay green: `make qa` (ruff, md-lint, types, deptry,
   static_checks incl. Proposal lifecycle, pytest, verify_notes,
   integrity, snapshot); `tests/test_gate_query.py` and the
   search_tui parser tests extend green. Repeat all timing claims ≥ 3
   runs; the ontology eval gate does not apply (no roster/crosswalk/
   extractor semantics touched — gate-index surface only).

| Projected outcome | Today | After |
|---|---|---|
| refresh / any gate_query command, steady state | 1.29–1.36 s | ≤ 0.35 s |
| cost growth per integrity run (+735 KB) | rescanned forever | parsed once |
| corpus integrity file | 45.5 MB, unbounded | ~22 MB, rotation works |
| gates visible in the index | 5 | 7 (integrity, verify) |
| indexed runs | 182 | ~365 |

## 5. Risks

- **Grammar drift between TUI and index** — mitigated by the single
  grammar home: both title regexes and the chunk parsers live in
  search_tui; gate_query only dispatches. Tests pin both consumers.
- **Legacy runs lack `**Ended:**`** — completeness falls back to the
  not-last-block rule; the final historical block may sit pending
  until the next integrity run appends and terminates it.
  Self-healing, no data loss.
- **`rotate` offset shift mid-corpus** — `cmd_rotate` already adjusts
  `parse_state` (`GREATEST(parse_offset - prefix, 0)`); S2 verifies
  with a post-rotate refresh before declaring done.
- **Index growth** — ~365 runs / a few thousand legs and
  `err_head`s: DuckDB-side trivial (DB is 29 MB today).
- **Concurrent append during a qa run** — the writer appends per-line
  with the file open in append mode; a reader can see a partial tail.
  The pending-tail rule already handles exactly this for qa reports;
  the new kinds inherit it.

## 6. Non-goals

- **Writer diet**: why an integrity run grew 16 KB → 735 KB (which
  sections to keep/drop) is a writer-owned content decision — separate
  arc, only diagnosed here.
- **search_tui UX changes**: parsers are reused as-is; only the
  `parse_verify_runs` chunk factor (pure refactor).
- **JUnit/artifact wiring for integrity/verify**: none exist; none is
  invented.
- **Schema changes**: `runs` / `legs` / `artifacts` / `parse_state`
  shapes stay as-is; no backfill beyond what refresh naturally parses.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-26 | `gate_query refresh` ×3 | 1.33 / 1.29 / 1.27 s | venv python, no new bytes |
| 2026-09-26 | `gate_query latest` ×3 | 1.34 / 1.36 / 1.33 s | auto-refresh + query |
| 2026-09-26 | `python3 -c "import gate_query"` ×3 | 0.22 / 0.20 / 0.19 s | fixed overhead |
| 2026-09-26 | cProfile `refresh()` | 2.51 s; `_split_blocks` 1.91 s; 1.52 M `re.match` | profiler inflates; shape is what matters |
| 2026-09-26 | decode+split 45.5 MB file | 0.071 s + 0.553 s; 0 blocks | grammar mismatch |
| 2026-09-26 | `parse_state` inspect | integrity 0/45533564, wt 0/1773107, verify 0/37554 | offset stuck at 0 |
| 2026-09-26 | `rg -c '^# '` per file | 116 / 65 / 102 runs; 0 `# make` headers | all unindexed |
| 2026-09-26 | per-run size trend | first 10 avg 16 KB; last 10 avg 735 KB | accelerating writer growth |
| 2026-09-26 | `witr -f outputs/gate_runs.duckdb` | no holder | lock contention ruled out |
| 2026-09-26 | bare `python3 … refresh` | ModuleNotFoundError: duckdb | env note, not the cause |
