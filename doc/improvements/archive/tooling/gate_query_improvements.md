---
title: "Add historical comparison and artifact intelligence to gate_query"
status: executed
filed: "2026-09-24"
executed: "2026-09-24"
completed_md: "286"
area: "helpers/misc"
---

# Add historical comparison and artifact intelligence to gate_query

**Date:** 2026-09-24 · **Status:** EXECUTED ·
**Area:** `helpers/misc/gate_query.py`, `tests/run_gate_report.py`, `tests/test_gate_query.py`

**Depends on:** `doc/improvements/archive/testing/test_data_items.md`

## 1. Motivation

`gate_query` already turns append-only Markdown reports and JUnit XML into a
useful historical index. Its current verbs are:

```text
latest  recent  failures  tests  timing  grep  rotate  refresh
```

The next debugging leap is not another raw report parser. It is a small set
of read-only historical questions:

- What changed between a normal run and a slow run?
- Which tests became slow, failed, skipped, or changed marker selection?
- Is this the same failure recurring across commits?
- Which non-pytest tool introduced a diagnostic?
- Is a duration regression inside a declared budget or a critical-path
  candidate?

The QA investigation demonstrated the value of these questions. `gate_query`
showed pytest moving from 116.64s at run 34 to 155.62s at run 37; the test
count stayed stable between runs 36 and 37, pointing to an execution-path
change rather than simple suite growth. JUnit then identified the live
closeness, L1B betweenness, exact-lane, and synthetic `--all` tests as the
relevant data.

This proposal owns query and index behavior. The separate test-data proposal
owns normalized JUnit facts and fingerprints; G1 consumes those facts and
never creates a competing source of truth.

## 2. Current baseline and constraints

| Capability | Current state | Gap |
|---|---|---|
| Runs/legs | indexed from Markdown with byte offsets | no run-to-run comparison |
| Tests | JUnit-backed rows | no historical test query |
| Timing | per-leg history and budget lookup | no test/artifact/critical-path view |
| Failures | newest run or explicit run | no recurrence clustering |
| Text | `grep` over report failure text | no normalized non-pytest diagnostics |
| Refresh | incremental and rebuildable | must remain so |

Hard constraints:

- Reports and original JUnit/JSON artifacts remain authoritative.
- The DuckDB index is derived, disposable, and refreshable.
- All new commands are read-only.
- Missing optional artifacts mean `not collected`, never a synthetic pass or
  fail.
- xdist serial test sums and observed wall time must be labeled separately.
- Existing CLI verbs and output remain backward compatible.

## 3. G1 work items

### G1.1 — Run comparison

Add:

```text
gate_query compare RUN_A RUN_B [--gate GATE] [--json]
```

Return:

- failed legs added, removed, resolved, or persisting;
- failed tests added, removed, resolved, or persisting;
- outcome changes for the same node ID;
- leg duration absolute/percentage deltas;
- commit, worktree, runtime, jobs, and complete/partial metadata deltas;
- explicit comparability warnings for gate-shape, test-count, environment, or
  source-version differences.

The command must query indexed facts and must not reparse full reports.

### G1.2 — Historical test queries

Add:

```text
gate_query tests --node NODE_ID --last N [--json]
gate_query tests --slowest --last N [--json]
gate_query tests --marker MARKER --last N [--json]
```

Return duration/outcome history, first/last seen, failure count, marker
history, and run context. `--slowest` must identify whether it ranks total
serial duration, setup/call/teardown duration, or a worker-aware critical
path. The first implementation may expose only metrics supported by the
normalized test data and must say when a metric is unavailable.

### G1.3 — Failure clustering

Add:

```text
gate_query clusters [--fingerprint HASH] [--last N] [--json]
```

Cluster normalized test failures by `error_fingerprint` and return:

- first/last occurrence;
- run count and distinct commit/worktree count;
- affected node IDs;
- pass/fail transition history;
- representative bounded error head;
- source report/JUnit links and offsets.

Clustering is a debugging view only. It must never alter a gate result or
silently mark a test flaky.

### G1.4 — Generic artifact records

Define a small common record for non-pytest tools:

```json
{
  "schema": "gate-artifact.v1",
  "kind": "ruff|ty|coverage|perf|integrity|security|frontend|search",
  "target": "path_or_leg",
  "status": "pass|fail|warn|info",
  "duration_s": 0.0,
  "message": "bounded summary",
  "fingerprint": "stable normalized key",
  "metadata": {}
}
```

Add read-only adapters in this order:

1. Ruff JSON diagnostics.
2. `ty` JSON diagnostics.
3. Coverage XML/JSON.
4. Perf benchmark JSON/manifest.
5. Integrity/snapshot freshness manifest.
6. Security SARIF/secret-scan output.
7. Frontend `tsc`/ESLint/Prettier JSON.
8. Search-index freshness manifests.

Adapters must preserve original artifacts and schema versions. A missing
adapter output is `not collected`; it is not a gate failure.

### G1.5 — Budget and critical-path views

Extend timing with:

- declared budget and utilization;
- median, p25, p75, min, max over a selected window;
- pass-only versus all-run views;
- test-duration totals separated by phase;
- worker-aware critical-path candidates when worker data exists;
- first/last stable-window comparisons.

The UI must not claim that summed xdist test durations equal wall time.

## 4. Index/query schema

Retain existing `runs`, `legs`, `bench`, `tests`, and parse-state tables.
Add optional derived tables/views:

```text
artifacts(run_id, kind, target, status, duration_s, message,
          fingerprint, metadata_json, schema, source_rel, source_offset)
test_facts(run_id, leg, node_id, file, outcome, seconds, phase,
           phase_seconds_json, markers_json, file_line, err_head, err_blob,
           error_fingerprint, worker, schema)
test_fact_index(node_id, fingerprint, first_run_id, last_run_id)
```

Indexes:

- `(node_id, run_id)`;
- `(error_fingerprint, run_id)`;
- `(kind, target, run_id)`;
- `(leg, seconds)`;
- `(status, run_id)`.

Migrations are additive and rebuildable from retained source artifacts.
`refresh` remains the convergence command; no query requires a full rebuild
unless explicitly requested.

## 5. Sequencing

| Order | Work | Dependency |
|---:|---|---|
| 1 | Test-data normalizer and additive schema | `test_data_items.md` |
| 2 | `compare` | indexed run/test facts |
| 3 | historical node/marker/slowest queries | normalized test facts |
| 4 | failure clusters | stable fingerprints |
| 5 | generic artifact schema and Ruff/ty adapters | artifact retention |
| 6 | coverage/perf/integrity/security/frontend adapters | stable generic schema |
| 7 | budget/critical-path views | timing plus test facts |

G1 must land in independently testable slices. T1 must land first or be
explicitly stubbed with a clear unavailable state.

## 5.1 Current implementation slice

The first query slice is implemented on top of the normalized `test_facts`
rows:

- `compare RUN_A RUN_B [--gate GATE] [--json]` compares indexed legs and test
  facts, including added/removed/persisting failures, outcome transitions,
  duration deltas, metadata deltas, and comparability warnings.
- `tests --node`, `--marker`, and `--slowest` provide historical test views;
  slowest output explicitly labels serial testcase seconds as non-wall time.
- `clusters [--fingerprint HASH] [--last N] [--json]` groups failed/error
  facts by normalized fingerprint and returns run/commit/worktree counts,
  affected nodes, transitions, representative diagnostics, and source links.
- `artifacts [--run N] [--kind KIND] [--status STATUS] [--gate GATE] [-wt NAME] [--last N] [--json]`
  exposes a versioned `gate-artifact.v1` record stream. Report legs are adapted
  into Ruff/ty/search/integrity/security records, retained
  `gate-artifacts.json` manifests are ingested read-only, and absent optional
  outputs produce no synthetic record.
- `timing` now reports min/p25/median/p75/max, budget utilization, serial
  testcase phase totals, and optional pass-only/critical-path views. Critical
  path candidates are explicitly serial durations, not wall time.
- The existing `tests --run` mode and all prior verbs remain available.
- Native Ruff/`ty` JSON, coverage JSON/XML, perf JSON/JSONL, integrity and
  snapshot JSON, security SARIF/secret-scan JSON, and frontend JSON outputs are
  parsed into bounded, versioned records. Runner retention copies each native
  file when present; malformed or missing files produce no diagnostic rows.
- Optional empty diagnostic lists produce an explicit pass record, while
  coverage budgets and perf budgets can produce fail records.

## 5.2 End-to-end example

After `gate_query` has indexed retained reports and native artifacts, one shell
invocation can refresh the derived index and exercise the complete query
workflow. Replace the example run IDs with runs from the target gate:

```bash
.venv/bin/python3 helpers/misc/gate_query.py refresh && \
.venv/bin/python3 helpers/misc/gate_query.py latest --gate qa --json && \
.venv/bin/python3 helpers/misc/gate_query.py recent --gate qa --last 5 --tests && \
.venv/bin/python3 helpers/misc/gate_query.py failures --gate qa --recent 10 --json && \
.venv/bin/python3 helpers/misc/gate_query.py compare 36 37 --gate qa --json && \
.venv/bin/python3 helpers/misc/gate_query.py tests --slowest --last 10 --gate qa --json && \
.venv/bin/python3 helpers/misc/gate_query.py clusters --last 20 --json && \
.venv/bin/python3 helpers/misc/gate_query.py timing --leg pytest --last 10 --pass-only --critical-path && \
.venv/bin/python3 helpers/misc/gate_query.py artifacts --last 20 --json
```

This produces run context, comparison deltas, test history, recurrence clusters,
serial timing candidates, and all retained artifact records in one workflow.

## 6. Acceptance criteria

1. Existing `latest`, `recent`, `failures`, `tests`, `timing`, `grep`,
   `rotate`, and `refresh` behavior remains compatible.
2. `compare` identifies the QA run-34→run-37 timing change and the run-36/37
   test-count-stable execution-path change.
3. A node-ID query returns historical outcome/duration/marker context.
4. A slowest query labels serial versus wall semantics.
5. Recurrent logical failures cluster despite temporary-path/timestamp noise,
   while materially different failures remain distinct.
6. Generic artifact adapters are read-only, versioned, and report missing
   artifacts as `not collected`.
7. Corrupt/partial source artifacts leave the Markdown report searchable and
   produce an actionable incomplete state.
8. Incremental refresh remains the default and all derived tables can be
   rebuilt from retained sources.
9. Tests cover comparison, trends, clustering, budgets, schema migration,
   xdist semantics, missing artifacts, and read-only behavior.
10. Ruff, `ty`, pytest, static checks, and targeted Markdown/search checks
    pass per slice; full QA remains operator-owned.

## 7. Risks and mitigations

- **Index becomes a second truth** — source reports/artifacts stay canonical;
  every derived row records source and schema.
- **Fingerprint collisions** — display node IDs, source location, and raw
  bounded error beside the cluster key.
- **Misleading xdist timing** — separate serial work, worker timing, and wall
  time in every relevant view.
- **Optional adapter noise** — absence is explicit and never changes status.
- **Index/storage growth** — bounded diagnostics, source rotation, and growth
  metrics before retention changes.
- **Sensitive output** — scrub, cap, and secret-scan retained diagnostics.

## 8. Non-goals

- No hosted dashboard or external CI dependency.
- No automatic flaky suppression.
- No replacement for JUnit, Markdown reports, Ruff, coverage, perf, or
  security tools.
- No bespoke report format replacing each tool's native output.
- No deletion of historical reports.

## Appendix — priority table

| Priority | Addition | Debugging value |
|---:|---|---|
| P0 | JUnit per-test history and durations | Explains QA critical path |
| P0 | `compare` | Isolates normal→slow changes |
| P0 | failure fingerprints/clusters | Finds recurring failures |
| P1 | Ruff/ty JSON | Extends diagnostics beyond pytest |
| P1 | coverage join | Finds slow/uncovered code |
| P1 | perf/integrity manifests | Explains budget/data drift |
| P2 | security/frontend/search artifacts | Full-stack regression history |
| P2 | CI/runtime metadata | Prevents invalid comparisons |
