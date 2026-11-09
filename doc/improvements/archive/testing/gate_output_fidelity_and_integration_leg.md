---
title: "Gate output fidelity: repair node-id reconstruction, retain per-step output, retire the integration leg"
status: executed
filed: "2026-11-09"
executed: "2026-11-09"
completed_md: "496"
area: "helpers/misc/gate_query.py + tests/run_gate_report.py + doc/procedures/gates.md"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Gate output fidelity — repair node-id reconstruction, retain per-step output, retire the integration leg

**Date:** 2026-11-09 · **Status:** EXECUTED 2026-11-09 ·
**Area:** `helpers/misc/gate_query.py`, `tests/run_gate_report.py`,
`tests/test_gate_query.py`, `tests/test_run_gate_report.py`,
`doc/procedures/gates.md`

**Follows:** `doc/improvements/archive/convo_search/convo_search_drop_hnsw_numpy_copy_compact.md`
(#495) — the same closeout arc surfaced all three defects below while
validating the gate protocol edits in `gates_fix`.

## 1. Motivation

The 2026-11-09 closeout arc produced three separate findings about the
gate tooling, each of which cost an investigation to discover and each of
which degrades the operator's ability to answer "what failed and why"
without re-running part of the battery.

1. **`gate_query` fabricated 60% of its file paths.** Marker filtering
   silently returned 118 of 783 rows. The bug is a one-line node-id
   reconstruction and is fixed in this arc's working tree (S1, already
   implemented and unit-tested) — but the index is append-only, so the
   historical rows stay wrong until a backfill (S1b).

2. **The arc battery re-executes 783 tests for a diagnostic, not a verdict.**
   `make integration` is a strict subset of `make qa`'s pytest run and
   costs ~141 s per arc. The repo already removed the identical duplicate
   from the *advisory* gate on 2026-08-31 for exactly this reason.

3. **`gate_query` cannot always show a failure without a manual rerun.**
   1,021 of 3,825 indexed FAIL legs (26.7%) carry no error text at all, and
   no raw per-step stdout survives a run — only junit and a 60-line report
   tail. When the failure is in a non-pytest leg (perf, integrity, verify,
   or a make-target leg), the tracked output is frequently the string
   "status: CONTENDED" and nothing else.

The operator's framing for (3), verbatim:
"I see that sometimes gate_query cant find the failure output and has to
rerun that part of the gate manually."

## 2. Evidence (measured 2026-11-09, this box)

### 2.1 Node-id reconstruction (root cause of the marker gap)

`helpers/misc/gate_query.py` rebuilt the node id from junit's
`classname` by replacing **every** dot with a path separator:

```python
node = cls.replace(".", "/") + ".py::" + name if cls else name
```

JUnit emits `tests.test_rebuild_schema.TestRebuildGenerationTriggers` for
`tests/test_rebuild_schema.py::TestRebuildGenerationTriggers::test_...`,
so the reconstruction produced a path that does not exist.

| Measure | Value |
|---|---|
| Distinct `file` values indexed for qa run 3520 | 1,007 |
| Paths that do not exist on disk | **821** |
| Rows attached to fabricated paths | **3,527 of 5,838 (60.4%)** |
| Rows matching `--marker integration` | **118** of 783 |
| Rows matching after the fix | **783** of 783 |

The corrected reconstruction — split on the first capitalised segment,
because `pytest.ini` pins `python_classes = Test*` — resolved
**5,838/5,838** node ids exactly against the real junit + metadata
manifests, with zero misses. Deterministic mangling meant `--node`
history still resolved consistently, which is why the defect survived
unnoticed.

### 2.2 The integration leg is redundant execution

| Measure | Value |
|---|---|
| qa run 3520 | 1,007 files · 5,838 tests |
| integration run 3529 | 164 files · 783 tests |
| integration testcases absent from qa | **0** |
| `-m "integration and live" --collect-only` | **no tests collected** |
| Wall cost of the leg | **~141 s per arc** (median, since 2026-11-01) |

Precedent: commit `d91e73c45` (2026-08-31) removed the duplicate
integration run from the **advisory** gate — "drop the duplicate
integration run from the advisory gate … the advisory gate falls from
~94s to ~45s". The in-code comment at `tests/run_gate_report.py:296`
records it as a **user directive 2026-08-31** and adds that integration
"stays explicitly runnable via `make integration`".

No CI workflow references the target, no timing budget keys off it, and
`gate_timing_watch.py` does not watch it.

### 2.3 Failure text is missing for the legs that need it most

| Gate | FAIL legs | `err_head` NULL | Share |
|---|---:|---:|---:|
| perf | 721 | 366 | **51%** |
| integrity | 230 | 95 | **41%** |
| verify | 8 | 8 | **100%** |
| qa | 417 | 0 | 0% |
| advisory | 503 | 0 | 0% |
| maint | 27 | 0 | 0% |
| integration | 14 | 0 | 0% |
| **total** | **3,825** | **1,021** | **26.7%** |

Three independent truncations feed this:

- `tests/run_gate_report.py:133` — `_TAIL_LINES = 60` lines appended to
  the report per step (raised to 120 only for the `ty-tests` digest).
- `tests/run_gate_report.py:137` — `_CAPTURE_LINES = 400` rolling
  in-memory deque; anything earlier in a stream is discarded, not written
  anywhere.
- `helpers/misc/gate_query.py:_leg_err_head` — stops after **6 non-blank
  lines**, then hard-caps to **2,000 chars**.

And no raw output is retained at all: `retain_test_artifacts()`
(`tests/run_gate_report.py:542`) copies only junit, the metadata shards,
and a fixed allowlist of JSON artefacts. `outputs/.artifacts/qa/*/*.log`
is empty for every run. A perf leg whose whole failure signal is the
string "status: CONTENDED" is therefore unrecoverable after the fact —
the report tail and the index both say the same nothing.

Storage context: `outputs/.artifacts` is 400 MB across 340 run
directories; one qa run's retained artefacts are 2.7 MB. `/mnt/data` has
160 GB free. Raw step logs are affordable.

## 3. Design

Three slices, ordered so each is independently landable and independently
useful.

**S1 — node-id reconstruction (DONE in the working tree, needs folding
into a patch).** New helper `_junit_node_id(classname, name)` in
`helpers/misc/gate_query.py`: split `classname` on `.`; the split index
is the first segment whose leading character is uppercase; the module
becomes `"/".join(parts[:split]) + ".py"`; class segments are
`"::"`-joined ahead of `name`; empty classname returns `name`. Call
site `node = _junit_node_id(cls, name)`. Regression test
`test_junit_node_id_rebuilds_class_segments_as_node_ids` in
`tests/test_gate_query.py` covers a plain function, one class, nested
classes, a real repo path, a parametrised id, and the empty-classname case.

**S1b — index backfill (landed 2026-11-09).** `gate_query refresh
--rebuild-tests` re-derives `tests`/`test_facts` from the retained
junit artefacts (dry-run by default, `--apply` to write) and purges
rows orphaned by the old `_store_run` re-parse path. Applied to the
live index: 358 runs with artefacts, 340 re-derivable, 767,846 test rows
re-derived (500,791 with a fabricated node id), 1,491,591 orphaned rows
purged. The apply drops and re-creates the four ART indexes around the
writes — DuckDB 2.0.0 cannot DELETE rows whose entries are missing from
an index ("Failed to delete all rows from index"), and the live index's
orphaned rows were unindexed, so the drop/recreate repairs that latent
staleness too. Acceptance: `run 3520 --marker integration` now returns
783 rows (was 118). Regression test:
`test_rebuild_tests_fixes_fabricated_node_ids_and_orphans`.

**S2 — retire the integration leg from the arc battery.**
`doc/procedures/gates.md` Phase 1 becomes
`make qa && make perf && make advisory`. `make integration` survives as a
**manual** target for the case where qa's single-leg digest is genuinely
not enough — that is what the 2026-08-31 directive already said it was
for. The procedure keeps a paragraph explaining what was given up (a
named gate with `-v` output and its own junit) and how to get it on
demand. The `integration` entry stays in `GATES` and stays indexed; only
its inclusion in the battery changes.

**S3 — retain full per-step output and widen the failure capture.**

- `run_step` writes its **complete** stdout to
  `outputs/.junit/<gate>.<label>.log` (streamed, not buffered in the
  400-line deque) and `retain_test_artifacts` copies those logs into the
  run directory alongside junit.
- `gate_query` records the artefact path on the leg and serves it via a
  new `gate_query output <run> --leg <name>` verb (with `--grep`), so a
  failure can be read from the index without touching the report.
- Raise the failure budget: `_TAIL_LINES` for a **failed** step goes to
  400 (60 stays for passing steps, which nobody reads), and
  `_leg_err_head` keeps 40 lines / 8,000 chars instead of 6 / 2,000.
- Fix the 1,021 historical NULLs where the raw text is still recoverable
  by re-parsing the retained reports (same pass as S1b). Measured
  2026-11-09: none of the 469 non-orphan NULL legs' source reports still
  exist, so the historical NULLs stay; S3's retained logs cover every
  future run.

**Alternative considered and rejected:** teach `gate_query` to re-run the
offending leg on demand. Rejected — the index is supposed to be a record
of what happened, and a re-run produces a different tree state, a
different load average, and (for perf legs) a different verdict. Retained
output is the honest mechanism.

**Alternative considered and rejected:** keep the integration leg and
derive its digest from qa's junit. This was the remedy written into
`gates.md` on 2026-11-09 S5. It works — qa already emits per-test markers
— but it pays 141 s to buy a name. With S3 landed, qa's digest carries
per-test filenames and full failure text anyway, which is most of what
the separate gate was valued for. Recorded here so the next session does
not have to re-derive the comparison.

## 4. Acceptance criteria & shakedown

1. `gate_query tests --run <recent qa run> --marker integration` returns
   **783** rows, and
   `gate_query tests --marker integration --last 5` spans multiple runs.
2. Every `file` value in `test_facts` for a post-fix run exists on disk:
   a one-liner assertion over the run (the pre-fix count was 821 bad of
   1,007).
3. `gate_query failures --run <a run with a perf FAIL>` prints leg output
   that is not merely "status: CONTENDED".
4. After S3, `ls outputs/.artifacts/qa/<run>/*.log` is non-empty and the
   pytest log contains the short test summary.
5. `make qa` green end to end, with `integration` still runnable
   manually and still indexed.
6. Targeted tests only: `tests/test_gate_query.py` and
   `tests/test_run_gate_report.py` (house rule — never `make pytest`).
7. `make md-lint` clean after the `gates.md` edit, and
   `make search-fresh` converges (APPLY once, then `--check` rc=0).

| Projected outcome | Today | After |
|---|---|---|
| Arc battery wall (4 legs) | ~933 s | **~792 s** (−141 s) |
| Indexed rows with a real path | 40% | 100% |
| Indexed FAIL legs with no error text | 1,021 | 0 |
| Failure output recovered without a rerun | partial | full |

## 5. Risks

- **Backfill touches append-only history.** Mitigation: S1b writes only
  `test_facts` rows derived from retained artefacts, under a
  `--rebuild-tests` flag that is dry-run by default; the report files
  themselves are never rewritten.
- **The 60→400 tail change grows the report.** `outputs/qa_report.md` is
  already 2.5 MB. Mitigation: apply the larger tail to FAILED steps only,
  and the raw logs live outside the report.
- **Retained logs grow `outputs/.artifacts`.** At ~2 MB per pytest log
  and 340 existing run directories the tree could reach a few GB.
  Mitigation: reuse the existing `gate_query rotate` keep policy and
  extend `tmp_sweep` to reap run directories past the retention window.
- **Dropping integration from the battery removes a named gate.** If a
  future arc needs to report "make integration 1/1" in a trailer, it must
  run it manually. Mitigation: documented in the procedure para alongside
  the removal, with the one-line command.

## 6. Non-goals

- Changing what `make integration` does, or deleting the target.
- Removing `integration` from `GATES` or from the index.
- Re-running or re-judging any historical perf verdict.
- Making `convo-fresh-check` pass (chronic red by design — see
  `doc/procedures/gates.md`).
- ~~Touching the `maint`/`integrity`/`verify` reporting surfaces beyond
  populating their error text.~~ **Superseded by S4** (see §7): the
  operator extended the arc after archival — those three lanes now retain
  per-leg output too (`doc/improvements/archive/testing/gate_output_fidelity_and_integration_leg.md`,
  S4 below). Original scope stands for everything else in their reports.

## 7. S4 — the three lanes that never ride the runner (2026-11-09, after archival)

**Status of this section:** implemented same day, post-archival, on operator
direction. The §6 non-goal ("touching `maint`/`integrity`/`verify`") was
superseded by this slice; everything else in §6 still stands.

### 7.1 The gap S3 left behind

S3 fixed retention for the ONE lane that runs through
`tests/run_gate_report.py` (`qa`, `advisory`, and `integration` while it
existed). Measured the same day, three lanes still retained nothing:

| lane | run count | `artifact_state` | what a red left behind |
|---|---|---|---|
| `make perf` | 173 | `not_collected` (173/173) | a 2,000-char stderr tail in `err_head` |
| `make maint` | 73 | `not_collected` (73/73) | a 60-line report tail per failed step |
| `integrity` / `verify` | 791 / 529 | `NULL` (1,320) | their own markdown report only |

`gate_query output --run N --leg L` answered `has no retained artifact
directory (not_collected) — no output log to read` for every one of them, so
the verb S3 added worked on exactly one quarter of the gate surface. The
`perf.json` / `integrity.json` adapters already existed in
`gate_query._native_artifact_records`; nothing in the repo ever wrote those
files.

### 7.2 Why a shared module instead of reusing `retain_test_artifacts`

`helpers/` must not import `tests/` (verified: no such import in the tree),
so the runner's retention helper is unreachable from `maint.py`,
`database_integrity_check.py` and `verify_notes.py`. New
`helpers/misc/gate_retain.py` holds the WRITER half of the contract and
`gate_query` keeps the READER half; both derive the same filename
(`<gate>.<label>.log`) from the same rules (labels sanitized identically:
`/`, `\`, NUL -> `_`).

### 7.3 The contract (and the one trap that cost a debugging cycle)

- **Artifacts root keys off the REPORT FILE, not a worktree guess** —
  `artifacts_root_for(report_path) = report_path.parent / ".artifacts"`.
  An earlier revision used `gate_retain.outputs_root()` (which follows the
  worktree and returns `<main>/outputs/wt/<name>`); perf always writes
  `REPO_ROOT/outputs/perf_report.md`, so writer and reader disagreed and the
  index found the block but not its logs.
- **The stamped `**Artifacts:**` value is relative to the OUTPUTS root**
  (`.artifacts/<gate>/<run-id>`), matching `test_data_items` and
  `gate_query._artifact_base` (which rejects absolute paths and `..`
  escapes). Pass the outputs root, never the artifacts root — passing the
  latter stamps `.artifacts/<gate>/<run-id>` twice.
- **The meta lines must land in the first 8 lines of the block.**
  `gate_query._extra_meta` scans `lines[:8]` only. Perf and maint append
  `· **Artifacts:** <rel>` to their existing `**Generated:**` line plus a
  following `**Exit:** N`; integrity and verify write PLACEHOLDERS at the
  top and fill them after the sections exist (a first attempt that wrote
  them up-front, before any section was rendered, retained only
  `integrity.report.log` — no per-check logs at all).
- **Section names must match the index's leg labels.** `section_name()`
  drops only the LAST parenthetical: `## RELATIONS (ERROR-level;
  gate-failing)` -> `RELATIONS`, `### yaml_structure (1)` ->
  `yaml_structure`. Integrity legs are the human section headings, NOT the
  registry keys, so the `-> errors=N` value is re-read out of each rendered
  section rather than looked up in `results`.
- **Run ids are allocated BEFORE the first step runs**, so a crashed or
  contended lane still retains the legs it completed.

### 7.4 What landed

- **New `helpers/misc/gate_retain.py`** (~160 lines, stdlib only):
  `artifacts_root_for`, `run_id_now`, `safe_label`, `step_log_name`,
  `step_log_path`, `run_dir`, `artifact_meta_value`, `write_step_log`,
  `section_name`, `split_sections`, `write_section_logs`,
  `write_json_artifact`, `prune_old_runs` (bounded at 40 runs per gate,
  newest by sortable run id). Module docstring carries the lane table and
  the layout contract.
- **`tests/run_perf_benchmarks.py`** — `run_one` now uses
  `subprocess.Popen` + `communicate()` (a stdout read loop would deadlock on
  a full stderr pipe) and streams each leg's full stdout+stderr into
  `perf.<label>.log` while the leg runs; `main` allocates the run dir
  before the contention check; new `_retain_run_artifacts` writes
  `perf.json` (`schema: "perf-json.v1"`) and returns the meta value;
  `write_report` gained `artifact_dir` and emits `**Artifacts:**` +
  `**Exit:**`.
- **`helpers/maintenance/maint.py`** — per-step full log in
  `.artifacts/maint/<run-id>/maint.<label>.log` written inside the existing
  console-streaming loop (the 60-line report tail and 400-line deque are
  unchanged), plus `**Artifacts:**`/`**Exit:**` in `_write_report`.
  `--dry-run` creates no `.artifacts` tree. A skipped file-lane step
  (`_served_file_lane_skip`) retains its skip reason, so a skipped step
  does not read as a retention gap.
- **`helpers/misc/database_integrity_check.py`** — one log per
  `## CHECK (SEV)` section, `integrity.json`, and a new
  `@staticmethod exit_code(results)` that `main()` now returns (single
  source for the 0/1 rule).
- **`helpers/validators/verify_notes.py`** — one log per
  `### <bucket> (N)` section (errors and warnings), `verify.json`,
  `**Exit:** 1 if errors else 0`.
- **`helpers/misc/gate_query.py`** — `_retained_artifact_junit` returns a
  new state `"logs"` (logs present, no junit) instead of `"missing"`;
  `_store_report_run` gained `meta` and now stores the real
  `artifact_dir`/`artifact_state` for integrity and verify instead of two
  hardcoded NULLs; `cmd_output` sanitizes labels with the writer's rule and
  its no-dir message now reads "the lane predates artifact retention".

### 7.5 Validation

- **Targeted suite: 223 passed** (`test_gate_retain` 12,
  `test_gate_query` 49, `test_run_gate_report`, `test_run_perf_benchmarks`
  25, `test_maint` 41, `test_report_append_convention` 5,
  `test_verify_notes` 69); ruff check + format clean on every touched file.
- The two writer tests in `test_report_append_convention.py` drive the REAL
  writers and assert the reader-derived filenames (`integrity.RELATIONS.log`,
  `verify.yaml_structure.log`), which is the contract that can silently
  drift.
- **End-to-end, isolated** (`GATE_QUERY_ROOT`/`GATE_QUERY_DB` pointed at a
  scratch outputs dir, real writers + real `gate_query refresh`): both
  gates index as `artifact_state='logs'` with the right `exit_code`, and
  `gate_query output --leg RELATIONS` / `--leg yaml_structure` returns the
  retained body.
- **Perf smoke:** a one-bench run produced
  `.artifacts/perf/<run-id>/perf.doc_query.log` + `perf.json` with
  `**Artifacts:**`/`**Exit:**` inside the first 8 lines of the report.
- **Incident, recorded:** an early smoke test called `gate_query.connect()`
  without repointing `DB_PATH` and wrote two phantom rows (integrity 3546,
  verify 3547) into the real `outputs/gate_runs.duckdb` from a scratch
  corpus. They were deleted with their legs/test_facts/artifacts children
  (2054 -> 2052 runs). Lesson: any smoke test must set BOTH
  `GATE_QUERY_ROOT` and `GATE_QUERY_DB` before importing, because
  `connect()` defaults to `REPO_ROOT/outputs/gate_runs.duckdb`.
- **Not validated:** no live `make perf` / `make maint` / integrity / verify
  run has yet been indexed through the new path (the arc's full battery is
  the operator's call). First live run of each lane is the real acceptance.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-09 | `gate_query tests --run 3520 --marker integration` | 118 rows | pre-fix |
| 2026-11-09 | metadata manifests, qa run 3520 | 783 integration tests | all marked |
| 2026-11-09 | walk `test_facts.file` for run 3520 | 821/1,007 missing on disk | 3,527 rows |
| 2026-11-09 | corrected reconstruction over run 3520 | 5,838/5,838 resolved | zero misses |
| 2026-11-09 | qa 3520 vs integration 3529 node sets | difference empty | strict subset |
| 2026-11-09 | `pytest tests -m "integration and live" --collect-only` | 0 collected | 6,104 deselected |
| 2026-11-09 | `pytest tests -m "integration and not live"` | 783 collected | — |
| 2026-11-09 | run 3529 elapsed | 172.2 s | contended run |
| 2026-11-09 | leg census, `gate_runs.duckdb` | 1,021/3,825 FAIL legs NULL `err_head` | 26.7% |
| 2026-11-09 | `ls outputs/.artifacts/qa/*/*.log` | empty | no raw logs kept |
| 2026-11-09 | `du -sh outputs/.artifacts` | 400 MB / 340 dirs | 2.7 MB per qa run |
| 2026-11-09 | `pytest tests/test_gate_query.py -k junit_node_id` | 1 passed | S1 regression |
| 2026-11-09 | `pytest tests/test_gate_query.py` | 49 passed | S4 fixtures + output verb |
| 2026-11-09 | `pytest` targeted set (7 files) | 223 passed | S4 writers + reader |
| 2026-11-09 | perf smoke, 1 bench, `--force` | rc 0 | `perf.doc_query.log` 1,533 B + `perf.json` 286 B |
| 2026-11-09 | isolated `gate_query refresh` over scratch outputs | 2 runs, `artifact_state='logs'` | integrity exit 1, verify exit 1 |
| 2026-11-09 | `gate_query output --leg RELATIONS --run <scratch>` | logs returned | 3-line section + `-> errors=2` |
| 2026-11-09 | phantom-row cleanup, real `gate_runs.duckdb` | 2054 -> 2052 runs | 3546 integrity / 3547 verify deleted |
| 2026-11-09 | `du -sh outputs/.artifacts` after S4 | 403 MB | unchanged (no live lane run yet) |
