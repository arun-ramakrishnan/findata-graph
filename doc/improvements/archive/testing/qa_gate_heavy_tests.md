---
title: "Slim qa-gate heavy tests (S1 dsh_lane + S2 rebuild_schema)"
status: executed
filed: "2026-11-04"
executed: "2026-11-04"
completed_md: "478"
area: "tests/test_dsh_lane.py, tests/test_rebuild_schema.py, helpers/maintenance/harvest_conversations.py"
---

# Slim qa-gate heavy tests (S1 dsh_lane + S2 rebuild_schema)

**Date:** 2026-11-04 · **Status:** EXECUTED (completed.md #478) ·
**Area:** tests/test_dsh_lane.py, tests/test_rebuild_schema.py, helpers/maintenance/harvest_conversations.py

## 1. Motivation

qa gate pytest leg regressed from ~137s wall (2026-09-24) to 352s wall (2026-11-03 run 3402 FAIL). Per-file JUnit parse across 135 artifacts: test-time sum 412s → 1242s (3.01×), test count 3478 → 5637 (1.62×), mean/test 118ms → 220ms (1.86×). NOT graph-driven: live graph (Nov-04) v_node 24,928, e_all_und 148,732 — edges +30% since Sep-26 baseline (doc/local/perf/graph_scaling.md 26,153 nodes, 114,500 e_all_und) while nodes went DOWN. Jump was a step-change Sep-24 → Oct-09 from NEW FILES (96 files adding ~340s), then plateaued.

## 2. Evidence (measured 2026-11-04, this box)

| Configuration | Result | Verdict |
|---|---|---|
| status quo (run 3402) | pytest 353s wall, 5630 passed/2 failed | baseline |
| test_dsh_lane.py | 134.6s (JUnit sum), 8 harvest() calls | S1 target <40s |
| test_rebuild_schema.py | 123.4s (JUnit sum), clones 404MB DB per rebuild | S2 target <30s |
| test_integration_maint_chain.py | 111.7s | S3 landed (split adds +1 chain run) |
| test_graph_scale_watch | timing flake (holder 8.0s, synthetic 10-row) | not graph-size |

Top 4 slowest = test_dsh_lane.py (134.6s, commit 7b3141d5b Oct-11): harvest() 8 calls (4 tests × 1–3× each); cost = full 5-lane union + flush/commit (harvest_conversations.py:2147 runs opencode/prime-rlm/zcode/dsh/bili unconditionally; force_lane only forces re-read of one lane; no lanes filter exists). Verified by reading file: test_dsh_lane.py ALREADY passes isolated `corpus_root=tmp_path/"corpus"` and `db=tmp_path/"c.duckdb"` (line 138) → NO real-corpus hazard. `database/backup`: 0 hits → NOT a hazard.

## 3. Design

Chosen mechanism: test-only isolation parameters; production defaults untouched. Two independently landable slices:

**S1 (harvest lanes filter + test_dsh_lane.py module-scoped fixture):** Add private `lanes: tuple[str,...] | None = None` kwarg to `harvest()` (default None = all 5 lanes; production CLI unchanged). Update the lane loop to filter to only selected lanes. In test_dsh_lane.py, pass `lanes=("dsh",)` to every harvest() call and introduce a module-scoped synthetic session fixture to avoid re-creating the .dsh tree per test. Target: file-time 134.6s → <40s.

**S2 (test_rebuild_schema.py trimmed template + SAVEPOINT):** Replace `tmp_db` fixture (clones full_template 404MB per test) with `build_trimmed_template` (120 co + CEAT only). Add module-scoped clone + SQLite SAVEPOINT per mutating test to preserve isolation (fixture docstring requires isolated file). Target: file-time 123.4s → <30s. **As landed:** the SAVEPOINT isolation scheme was REJECTED in implementation — it broke under real write patterns ("SAVEPOINT games", tests/test_rebuild_schema.py:31); what shipped is the trimmed template plus per-test clones of it.

**S3 (test_integration_maint_chain.py synthetic seed):** Landed 2026-11-04 (supersedes the earlier "defer" — user asked whether the S3 debt was fixed, then to complete it). The fixture no longer backs up the 404MB live DB: `_build_db` reflink-clones the shared `schema_template` (canonical DDL, zero rows, ~2MB) and `_seed_test_data` inserts a minimal FK-consistent corpus — 42 sectors (build_sector_hierarchy `_validate_coverage` requires exactly the SUPER_SECTORS member set, zero orphans; the step INSERT-OR-IGNOREs the 10 super + 142 sub entities itself), HDFC Bank/ICICI Bank with repo-root-relative `file_path` (`findata/Companies/…`, the derive_events join contract), the 182 COMPANY_SUB_SECTORS map members, 3 graph_edges, and a db_meta scrub of production apply-stamps (worktree_fresh_trample_guard). The combined `test_full_chain_order_and_idempotence` is split into `test_chain_order` + `test_chain_idempotence` (+1 full chain run, ~27s — accepted for isolation/readability); `_shim_derive_events` disables the `--corpus` fast path (Corpus.load returns real-repo notes that can never resolve under the tmp `_REPO_ROOT`). File: 111.7s → ~121s JUnit sum for 7 tests, standalone serial 120–122s vs 78.5s for the old 6-test file.

Alternatives considered:
- Reduce xdist workers: already exclusive per gate_wall_time_reclaim.md (sequencing, not worker counts).
- Drop live tests: qa already uses `-m not live`; slowness is in non-live tests.
- Skip tests: not acceptable — coverage must be preserved.

## 4. Acceptance criteria & shakedown

1. S1: `.venv/bin/python3 -m pytest tests/test_dsh_lane.py -k "dsh" --junitxml=/tmp/j1.xml` → JUnit file-time <40s (not make pytest).
2. S2: `.venv/bin/python3 -m pytest tests/test_rebuild_schema.py --junitxml=/tmp/j2.xml` → JUnit file-time <30s.
3. Isolation preserved: run each file twice consecutively → both green.
4. No coverage cut: `make pytest` coverage delta = 0 (pytest-cov diff).
5. No production-default change: `maint harvest --help` unchanged; CLI default = all 5 lanes.
6. Targeted `-k` during dev only; ONE `make qa` + `make perf` at arc end.
7. Archive proposal → ../archive/qa_gate_heavy_tests/; completed.md entry (check duplicates; next #429, suffix never renumber); pending.md note; `make search-fresh APPLY=1`.

| Projected outcome | Today | After |
|---|---|---|
| test_dsh_lane.py JUnit time | 134.6s | <40s |
| test_rebuild_schema.py JUnit time | 123.4s | <30s |
| qa pytest leg (wall) | 353s | ~220s (projected) |

## 5. Risks

- **Lanes filter introduces bug** — mitigation: module-scoped fixture + run file twice green; production callers pass nothing (default None = all lanes).
- **Trimmed template misses schema** — mitigation: build_trimmed_template already used elsewhere; (as landed the SAVEPOINT isolation was rejected — per-test clones preserve isolation instead).
- **Timing flake returns** — mitigation: test_graph_scale_watch holder already widened to 8.0s; synthetic 10-row table decoupled from graph size.

## 6. Non-goals

- No change to production harvest CLI defaults or behavior.
- No change to graph-scale watch thresholds (already fixed).
- No optimization of the maint_chain step scripts themselves (S3 touches only the test fixture/shims; db_maint's ~12s per chain run is production code, out of scope).
- No coverage reduction.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-04 | gate_query latest --gate qa | pytest 353.0s wall, 5630 passed/2 failed | run 3402 FAIL |
| 2026-11-04 | JUnit parse 135 artifacts | sum 1242s, count 5637, mean 220ms | vs Sep-24: 412s/3478/118ms |
| 2026-11-04 | live graph counts | v_node 24928, e_all_und 148732 | +30% edges since Sep-26, nodes DOWN |
| 2026-11-04 | test_dsh_lane.py per-file | 134.6s sum, 8 harvest() calls | commit 7b3141d5b Oct-11 |
| 2026-11-04 | test_rebuild_schema.py per-file | 123.4s sum, clones 404MB | tmp_db fixture per test |
