---
title: "pytest tmpfs amplification — shared templates, keep-1 roots, sidecar-free test runs"
status: deferred
filed: "2026-09-27"
executed: "2026-09-27"
completed_md: "307"
area: "tests/ (fixtures + conftest) + helpers/maintenance/db_maint.py knob"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). -->
# pytest tmpfs amplification — shared templates, keep-1 roots, sidecar-free test runs

**Date:** 2026-09-27 · **Status:** DEFERRED (S1-S3 executed same day; the
module/class-scoped production-copy population deferred in §D2 — sizes and
trigger inside) · **Area:** `tests/` + one production knob in `db_maint.py`

## 1. Motivation

Advisory run 547 (2026-09-27 22:27) failed with **65 test failures that
were all environmental**: `OSError [Errno 122] Disk quota exceeded` and
`sqlite3.OperationalError: disk I/O error`, cascading through
`test_db_maint_duckdb`, `test_graph`, and `test_graph_disk` (every test in
the latter errored at setup). The quota fired while `df` still showed
gigabytes free — tmpfs free space is not the user's quota, so "df looks
fine" is exactly the trap.

## 2. Evidence (measured 2026-09-27)

Where ~5.7 GB of the 7.1 GB tmpfs went (per live-invariants run ≈ 1.9 GB,
pytest's default last-3-root retention ×3):

| Amplifier | Cost | Mechanism |
|---|---|---|
| `_trimmed_template` per worker | 308 MB × 2 | session-scoped — but an xdist worker IS a session, so `-n auto` built one per worker |
| `built_cache` per worker | 347 MB × 3 | module-scoped full production sqlite backup (test.db 322 MB) + materialised cache (test.duckdb 42 MB), per worker |
| backup-test residue | 518 MB | backup tests write copies; retention policy `failed` keeps them |

And the one nobody could see from the fixtures: `DBMaintainer.run()` in
each of the 6 duckdb tests unconditionally copied the **production
sidecar world** into the test's `tmp_path` — the convo_search index
(`memory/convo_search.duckdb`, 181 MB) + FTS sidecar, the embed store,
the note corpus, the convo corpus tar, and the `memory/` catch-all tar —
several hundred MB per `.run()` call, invisible in the test code.

## 3. Design (executed)

- **S1 — one trimmed template per run** (`tests/_tmp_hygiene.py`):
  `trimmed_template()` builds the downsampled production copy once per
  run under the xdist run root (`basetemp().parent`, shared by exactly
  this run's workers), flock-protected, published via atomic
  `os.replace` (a crashed builder leaves only an ignored `.part`).
  `test_graph_disk`'s trim moved here verbatim;
  `test_db_maint_duckdb.built_cache` now `copyfile`s from it instead of
  taking its own 308 MB backup (its assertions are relative — backup ==
  source counts, `> 0` — which the downsampled corpus satisfies).
  Single-process runs (no `popen-gw` in basetemp name) build locally:
  the parent of a bare run root is shared across runs — wrong scope.
- **S2 — keep-1 root retention** (`prune_old_pytest_roots`, wired to
  `pytest_unconfigure` in conftest): gate_query already retains run
  history (junit + artifacts per run, indexed in gate_runs.duckdb), so
  stale `/tmp/pytest-of-*/pytest-N` roots are ballast. Prunes sibling
  roots quiet for 30 min — root AND worker dirs, so a concurrent live
  run is never touched; symlinks (`pytest-current`) skipped; best-effort
  (never fails a run). 5 unit tests in `tests/test_tmp_hygiene.py`.
- **S3 — sidecar-free test runs**: `DBMaintainer(backup_sidecars=False)`
  skips the five production-sidecar backups (embed store, note corpus,
  convo_search pair, convo corpus tar, memory catch-all) while keeping
  the primary sqlite + duckdb backup and CHECKPOINT/VACUUM — the
  machinery the tests actually assert. Production keeps the default.
  Applied at 16 construction sites (6 duckdb, 10 core);
  `test_db_maint_convo.py` keeps the default — it EXISTS to cover the
  sidecar backups, hermetically via monkeypatched roots.
- **Guards (the recurrence prevention):**
  `test_no_production_db_backups_in_tests` — no test file may combine
  `sqlite3.connect` with `.backup(` (the only sanctioned production copy
  is the shared template builder; allowlist = deferred §D2 population +
  the guard's own text). `test_dbmaintainer_constructions_opt_out_of_
  sidecar_backups` — every `DBMaintainer(` in tests must carry
  `backup_sidecars=False` (allowlist: the hermetic sidecar-coverage
  module).

## 4. Outcome (measured)

- live-invariants: 65 environmental failures → **green**;
  `/tmp/pytest-of-arun` **empty** after the run (keep-1 + retention),
  tmpfs back to 29%.
- db_maint trio wall: 105 s (original) → 62 s (S1 alone) → **20.6 s**
  (S3 removes the per-test production zstd copies).
- Lint/format/types/static-checks green; 138 tests green across the
  touched modules.

## 5. §D2 — deferred: module/class-scoped production copies

The remaining full-corpus copies are amortised (1× per worker or per
class), not per-test — tolerable until the trigger fires:

| Site | Scope | Cost |
|---|---|---|
| `tests/helpers.py::copy_production_db` ← `test_fuzz_shortest_path` | module | 1 × ~308 MB (vacuumed) / worker |
| `…` ← `test_graph` | module | 1 × pruned copy / worker |
| `…` ← `test_integration_derive_events_cli`, `test_integration_extract_relations_cli` | class | 1 × copy / class (integration gate only) |
| `test_integration_maint_chain`, `test_integration_near_duplicates`, `test_integration_note_writers` (inline) | module/class | 1 × copy each (integration gate only) |

Move them onto a shared FULL-corpus pruned template (same S1 mechanism,
prune-config keyed) **only if**: quota/disk-I/O errors recur in /tmp, or
a new full-corpus fixture is about to be added. The guard's allowlist is
the inventory — shrinking it is the D2 burn-down list.

## 6. Non-goals

- No basetemp repoint to disk (`--basetemp`): a fixed dir is mutually
  destructive for concurrent runs and mktemp-per-run leaks; declined
  once keep-1 + dedup fit the quota with 5.1 GB headroom.
- No change to pytest's within-run retention (`tmp_path_retention_policy
  = failed`, count 1) — already correct.
