---
title: "Gate report quality traces — warnings, artifacts, and parser diagnostics"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "288"
area: "helpers/misc"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# Gate report quality traces — warnings, artifacts, and parser diagnostics

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/misc (`gate_query.py`), tests (`test_gate_query.py`),
and the existing gate-report writer contract (`tests/run_gate_report.py`)

Follows the executed gate-run search and artifact-intelligence arcs
(`completed.md` #282 and #286). This follow-up records the quality-trace
contract and its execution: incremental refresh diagnostics, structured
advisory warning capture, JUnit/artifact visibility, and writer-contract
coverage. The report files and original JUnit/native artifacts remain
authoritative; DuckDB is derived and rebuildable.

## 1. TL;DR

Gate reports already preserve useful non-failure evidence: advisory tails
contain concise `ty` diagnostics, Python warning classes, and explicit
`warning:` lines; the writer retains JUnit XML, test metadata, and native
artifacts under `.artifacts/<gate>/<run_id>/`; and the report header names
that artifact directory. `gate_query` did not expose all of that state.

This follow-up makes the evidence visible and trustworthy:

- automatic incremental refresh reports incomplete trailing blocks and
  parser rejections instead of silently returning an older or empty view;
- JUnit/artifact state appears in human `latest` and `failures` output;
- warning lines become structured, queryable `warn` records;
- `latest`, `recent`, and `failures` show warning counts without changing
  PASS/FAIL semantics;
- JUnit parsing distinguishes ordinary missing/corrupt/stale input from
  a successful retained-artifact trace.

No new dependency, report format, or gate verdict is introduced.

## 2. Current gaps and evidence

1. **Refresh visibility.** `main()` already calls `refresh(con)` before
   every non-`refresh` verb, using the default incremental path. A
   half-written trailing report is intentionally not indexed, but the old
   CLI returned only the resulting empty/older result with no explanation.
2. **Parser visibility.** `_parse_block()` returns `None` for a rejected
   block and the old refresh counter did not report that rejection.
3. **Artifact visibility.** `runs.junit_path`, `artifact_dir`, and
   `artifact_state` were persisted, but human `latest`/`failures`
   digests did not show them. `latest --json` and the detailed
   `artifacts` view were not sufficient for the common failure workflow.
4. **Warning visibility.** Advisory output is non-blocking by design;
   warning lines in `## <leg> (OK)` tails were only available through
   raw report text. They were neither failure rows nor structured
   `warn` records. Existing native JSON adapters already support
   `status=warn`, so the report-tail path should use the same artifact
   surface rather than create a competing warning store.
5. **Writer contract.** `tests/run_gate_report.py` already writes
   `**Artifacts:** <relative-dir>`, retains JUnit/metadata/native files,
   and keeps the complete configured tail. No writer rewrite is needed;
   this proposal only audits and tests that contract.

## 3. Design

1. **S1 — refresh and parser diagnostics.** Keep incremental refresh as
   the default for every query. Return and display:
   - skipped pending tail count;
   - parser rejection count;
   - files and newly indexed runs.

   A pending block remains unindexed until the next refresh. A rejected
   complete block is counted and surfaced as a warning; it is not turned
   into a synthetic PASS/FAIL run. `refresh --full` remains the explicit
   migration/rebuild path for historical rows after parser semantics
   change.

2. **S2 — JUnit/artifact trace.** Add a compact artifact line to
   `latest` and `failures` containing artifact state, relative artifact
   directory, and retained JUnit path when available. Preserve the
   existing `not_collected`, `missing`, `corrupt`, `retained`, and `live`
   states. JUnit paths already remain available in JSON and the database;
   this makes the normal human path self-locating.

3. **S3 — warning capture.** Parse only narrow warning forms from
   report leg tails:
   - `path:line:column: warning[rule] ...` (including advisory `ty`);
   - explicit `warning:` / `warn:` lines;
   - standard Python warning classes such as `UserWarning`,
     `DeprecationWarning`, `FutureWarning`, `PytestWarning`, and peers.

   Store each as an `artifacts` row with `kind=warning`, `status=warn`,
   `schema=gate-warning.v1`, bounded message/fingerprint, report source
   relation, and block offset. Query with
   `gate_query artifacts --status warn`; keep warnings separate from
   failed legs and failed tests. A warning never changes `overall_ok()`,
   a leg verdict, or the gate exit code.

4. **S4 — human views.** `latest` shows warning count and artifact/JUnit
   trace; `recent` annotates PASS runs with `warnings=N`; `failures`
   shows the warning count alongside failure detail. A failed run can
   contain both failures and warnings, and a passing advisory run can
   contain warnings.

5. **S5 — JUnit robustness and writer audit.** Catch filesystem errors
   around JUnit parsing as a missing/corrupt artifact rather than an
   unhandled query exception. Keep the existing writer's full-tail,
   flock, `**Artifacts:**`, JUnit retention, metadata, native-artifact,
   and source-offset contracts covered by focused tests.

## 4. Acceptance criteria and shakedown

1. Incremental refresh after a completed append indexes exactly the new
   block; a synthetic incomplete tail is not indexed and emits the
   pending-tail warning on the next CLI query.
2. A parser-rejected complete block increments `parse_errors` and is
   visible in the CLI warning/`refresh` output; it does not become a
   fabricated gate result.
3. Synthetic advisory report with `ty`, Python-class, and explicit
   warning lines produces exactly one `warn` artifact per line;
   `artifacts --status warn` returns them with source relation/offset.
4. `latest`, `recent`, and `failures` expose warning counts without
   changing PASS/FAIL status. A passing run with warnings remains PASS.
5. Retained JUnit output shows `artifact_state`, artifact directory, and
   JUnit path; missing/corrupt retained artifacts remain explicit and do
   not synthesize tests.
6. Writer tests continue to prove that every configured tail is present,
   `**Artifacts:**` is written, and JUnit/metadata/native files are
   retained under the run directory.
7. One controlled `gate_query refresh --full` completes against the
   existing corpus and records the rebuild duration and parser/warning
   counts. The current full-refresh attempt exceeded 120 seconds, so the
   shakedown must measure it rather than treating the timeout as a pass.
8. Focused tests: `pytest tests/test_gate_query.py tests/test_run_gate_report.py`.
   Gates: `make lint`, `make types`, and `make qa` on the operator's full
   gate pass. No ontology eval-gate bullet: this changes derived gate
   diagnostics only, not roster, crosswalk, hierarchy, or extractor
   semantics.

## 5. Risks

- Warning extraction can over-capture prose or miss a tool-specific
  format. Keep the recognizer narrow, preserve source offsets, and add
  the adapter form when a real tool format appears.
- The same warning can occur in a failed leg and a retained JSON
  artifact. Deduplication must not erase source evidence; the initial
  contract is one report-tail record per matching line, with native
  artifact records remaining independently queryable.
- A parser rejection warning is only useful if the rejected block is
  diagnosable. Include the refresh count and keep the raw report source
  authoritative; do not silently advance a stale index as if the block
  had parsed.
- Full rebuilds are intentionally explicit and may be slower than
  incremental refresh. Measure and report the cost rather than making
  every query full-rebuild.

## 6. Non-goals

- No change to gate PASS/FAIL semantics, nonblocking advisory policy, or
  exit codes.
- No new warning database, dependency, hosted service, or semantic
  search index.
- No replacement for raw Markdown, JUnit XML, or native JSON artifacts.
- No change to the existing report writer format unless a future warning
  source cannot be represented by the existing tail/artifact contract.
- No automatic warning suppression, deduplication across tools, or
  historical backfill beyond the explicit full-refresh migration.

## Appendix — current implementation trace

- `helpers/misc/gate_query.py`:
  - incremental `refresh()` and `parse_state`;
  - `tests`/`test_facts` JUnit ingestion;
  - `artifacts` records and artifact states;
  - `latest`, `recent`, and `failures` human views.
- `tests/run_gate_report.py`:
  - run tail capture and configured tail budgets;
  - JUnit/metadata/native artifact retention;
  - flock-protected append and `**Artifacts:**` header.
- `tests/test_gate_query.py` and `tests/test_run_gate_report.py`:
  synthetic report, JUnit, artifact, warning, parser, and writer contracts.
