---
title: "Normalize test result data for historical debugging"
status: executed
filed: "2026-09-24"
executed: "2026-09-24"
completed_md: "285"
area: "tests, helpers/misc"
---

# Normalize test result data for historical debugging

**Date:** 2026-09-24 · **Status:** EXECUTED ·
**Area:** `tests/run_gate_report.py`, `tests/`, `helpers/misc/`

## 1. Motivation

The gate reports preserve pytest outcomes and JUnit XML, but the current
index stores only a basic test row: run, leg, node ID, outcome, seconds, and
bounded error text. That is enough to ask “which tests failed?” but not to
answer:

- which test setup, call, or teardown consumed the time;
- which tests are marked `live`, `integration`, or `real_graph_cache`;
- whether the same failure recurs across commits and worktrees;
- whether a test changed from skipped to passed or passed to failed;
- which xdist worker actually executed a slow test;
- how to navigate from a historical result back to source and artifact.

The worker requirement exposed an xdist-specific correctness issue during the
first manifest smoke: every worker collects the full selected test set but
executes only its assigned shard. A manifest that emitted every collected
item per worker therefore attributed tests to workers that never ran them.
The xdist worker field is valid only when backed by an actual test report.

The QA slowdown investigation showed the value of this data: run 34→37 had
the same test count but a 39s pytest regression, and JUnit plus gate metadata
identified the live tests and the synthetic `--all` test that were charged
to blocking QA.

This proposal owns the test data contract and capture layer only. Historical
comparison, clustering, and generic artifact queries belong to the separate
`gate_query` improvements proposal.

## 2. Current baseline

`helpers/misc/gate_query.py` currently ingests JUnit-backed rows into:

```text
tests(run_id, leg, node_id, outcome, seconds, err_head, err_blob)
```

The source JUnit files and Markdown gate reports remain authoritative. The
DuckDB index is derived and rebuildable. New data must preserve that
separation and remain compatible with current `gate_query tests` behavior.

All pytest gate lanes should continue to emit JUnit:

- `qa` → `outputs/.junit/qa.junit.xml`
- `integration` → `outputs/.junit/integration.junit.xml`
- `advisory` live-invariants → `outputs/.junit/advisory.junit.xml`

## 3. Canonical test fact

Normalize each JUnit testcase into a versioned, additive fact:

| Field | Source | Meaning |
|---|---|---|
| `run_id` | gate report + JUnit path | owning gate run |
| `leg` | gate report table | `pytest`, `pytest-integration`, or `live-invariants` |
| `node_id` | JUnit classname/name | stable test identity |
| `file` | normalized classname/module | source file |
| `outcome` | JUnit | passed/failed/skipped/xfail/error |
| `seconds` | JUnit testcase time | observed testcase duration |
| `phase` | JUnit/setup/call/teardown metadata | timing attribution when available |
| `markers` | pytest metadata or JUnit properties | live/integration/real-cache/etc. |
| `file_line` | pytest source metadata | navigation target |
| `err_head` | bounded failure/error text | first useful diagnostic |
| `err_blob` | capped failure/error text | source-linked diagnostic |
| `error_fingerprint` | T1 normalizer | stable grouping key |
| `worker` | xdist metadata when emitted | scheduling context |
| `artifact_schema` | T1 constant | normalizer/schema version |

The first migration is additive. Existing `tests` queries must continue to
work; new columns/tables are populated when the source artifact contains the
corresponding information.

## 4. Capture and normalization slices

### S1 — Preserve and version artifacts

1. Keep `--junitxml` on every pytest gate lane.
2. After each gate, copy the fresh JUnit plus base/worker metadata manifests
   into an immutable run directory:
   `outputs/.artifacts/<gate>/<run-id>/`.
3. Worktrees retain under their existing namespace:
   `outputs/wt/<name>/outputs/.artifacts/<gate>/<run-id>/`; main and
   worktree gate runners never share live or retained artifact files.
4. Add `Artifacts:` and existing `Worktree:` metadata to the Markdown report.
   `gate_query` resolves the retained path without relying on the latest live
   `.junit/` file.
5. Store explicit artifact state: `retained`, `live`, `missing`, `corrupt`,
   `invalid`, or `not_collected`. Missing/corrupt artifacts keep the Markdown
   report searchable and never fabricate test outcomes.
6. `refresh --full` reindexes the report and reconstructs `test_facts` from
   the retained JUnit/manifest path, making the derived index rebuildable.

### S2 — Add timing phases and markers

Capture setup/call/teardown durations when the active pytest/JUnit format
exposes them. Capture marker metadata from collection without re-running the
suite. The first marker vocabulary is `live`, `integration`,
`real_graph_cache`, and any marker already used by the gate selection.

Do not label summed JUnit durations as wall time. Under xdist, the testcase
sum is serial work while the gate's elapsed value is observed wall time.

### S3 — Stable failure fingerprints

Normalize a bounded error into a fingerprint using:

- exception type;
- normalized message shape;
- first stable source location;
- selected structured error fields.

Remove memory addresses, temporary directory names, timestamps, durations,
and other known noise before hashing. Keep the raw bounded diagnostic in the
source artifact/index for disambiguation. Fingerprints are for grouping and
must never change gate status.

### S4 — Worker and scheduling metadata

Capture xdist worker identity from actual execution, not collection. The
pytest hooks maintain two datasets:

- collection metadata: node ID, source file/line, and markers;
- report metadata: setup/call/teardown duration and outcome from
  `pytest_runtest_logreport`.

Each worker writes a separate manifest. The first `-n 2` smoke exposed the
critical distinction: both workers collect both selected tests, but each runs
only one. The initial writer emitted both tests for both workers, creating
false worker attribution.

The implemented fix filters every worker-side item that has no actual phase
report. A worker manifest therefore contains only tests that worker executed.
The controller may write an empty base manifest; ingestion accepts it without
treating it as evidence. JUnit remains authoritative for outcome and total
time, while sidecars contribute markers, source location, phase durations,
and the proven worker identity. Only sidecars whose mtimes fall inside the
gate-run window are merged, preventing stale worker files from a prior run
from contaminating the current facts.

If worker identity is unavailable, record `NULL` explicitly. Do not infer
critical-path membership from collection or serial duration alone.

### S5 — Explain and rebuild

Add a read-only explanation surface for one test fact showing source report,
JUnit path, node ID, marker, phase timings, fingerprint, and worker. A full
rebuild must reconstruct all derived facts from retained source artifacts.

## 5. Data safety and retention

- Do not store secrets, environment values, or unbounded tracebacks.
- Retain capped error text only; source artifacts remain the detailed record.
- Keep artifact schema version with every normalized row.
- Make duplicate node IDs deterministic and testable; parameterized tests can
  legitimately repeat, so identity includes run and occurrence where needed.
- Missing metadata is represented as unknown, not as a successful zero.

## 6. Acceptance criteria

1. Existing QA/integration/advisory JUnit files continue to be generated and
   indexed.
2. Existing `gate_query tests --run ...` output remains backward compatible.
3. A known passing, failing, skipped, and errored test is normalized with the
   correct outcome and source path.
4. Setup/call/teardown and marker fields are populated when available and
   explicitly unknown otherwise.
5. The same logical failure with different temporary paths/timestamps gets
   one stable fingerprint; materially different errors do not collide.
6. xdist worker metadata is retained only for tests with an actual phase
   report; collected-but-unexecuted tests are never attributed to a worker.
   Worker manifests from outside the run-time window are ignored.
7. Missing, corrupt, and unsafe retained paths produce explicit artifact
   states without corrupting the derived index or inventing test outcomes.
8. Normalized facts rebuild through `refresh --full` from immutable retained
   artifacts, including namespaced worktree runs, and include a schema version.
9. Tests cover normal, malformed, duplicate, marker, phase, fingerprint,
   retention, and rebuild cases.
10. Ruff, `ty`, pytest, static checks, and targeted Markdown/search checks
    pass for each landed slice; full QA remains operator-owned.

## 7. Risks and mitigations

- **Marker capture adds collection overhead** — reuse pytest metadata and
  measure the collection cost before expanding the vocabulary.
- **Fingerprint collisions merge distinct failures** — show the fingerprint,
  node IDs, source location, and representative raw error together.
- **JUnit format varies across pytest versions** — version the normalizer and
  test old/new representative shapes.
- **Retained diagnostics leak secrets** — cap, scrub, and run secret scanning
  before retention.
- **Index grows too large** — retain bounded fields, preserve raw artifacts
  under existing rotation, and measure row/file growth before changing policy.
- **False xdist worker attribution** — emit a worker-side test only when that
  worker produced an actual phase report; pin the behavior with a two-worker
  smoke and a unit test.
- **Stale sidecar contamination** — merge only metadata files whose mtime falls
  inside the owning run's ingestion window.

## 8. Non-goals

- No run-to-run comparison or failure clustering CLI; those belong to the
  separate `gate_query` improvements proposal.
- No replacement of JUnit or pytest.
- No automatic flaky-test suppression.
- No inference of worker critical path without worker evidence.
- No deletion of historical reports.

## Appendix — priority

| Priority | Data item | Reason |
|---:|---|---|
| P0 | outcome, node ID, seconds, source path | baseline test history |
| P0 | markers and phase durations | explains QA selection and cost |
| P0 | stable error fingerprint | recurring-failure debugging |
| P1 | worker identity | xdist scheduling context |
| P1 | artifact schema/version | safe migrations and rebuilds |
| P2 | richer properties and custom fields | future tool-specific drilldown |

## Appendix — xdist bug evidence

| Check | Before fix | After fix |
|---|---|---|
| Two selected tests under `-n 2` | `gw0` and `gw1` each listed both collected tests | each worker lists only its executed test |
| Controller manifest | empty base manifest | empty base remains non-authoritative |
| Worker proof | collection membership only | setup/call/teardown report required |
| Ingestion | risked stale/false worker data | run-window mtime filter plus report-backed worker |

The two-worker smoke and `test_test_metadata_writer_filters_unexecuted_xdist_items`
pin the corrected ownership semantics.

## Appendix — artifact retention evidence

| Check | Result |
|---|---|
| Main retained layout | `outputs/.artifacts/<gate>/<run-id>/` |
| Worktree retained layout | `outputs/wt/<name>/outputs/.artifacts/<gate>/<run-id>/` |
| Report linkage | `Artifacts:` plus `Worktree:` metadata |
| Valid retained artifact | state `retained`; JUnit facts and manifest metadata indexed |
| Missing/corrupt retained artifact | explicit state; zero fabricated `test_facts` |
| Rebuild | delete derived facts, run `refresh --full`, facts reconstructed |

`test_retained_worktree_artifact_rebuilds_test_facts`,
`test_missing_retained_artifact_is_explicit`, and
`test_corrupt_retained_artifact_is_explicit` pin the behavior.
