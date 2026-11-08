---
title: "Refresh production store census and close out iFUB frontier BFS"
status: executed
filed: "2026-11-08"
executed: "2026-11-08"
completed_md: "491"
area: "helpers/graph/stats.py, helpers/maintenance/db_maint.py, helpers/maintenance/store_sweep.py, tests/test_store_sweep.py, tests/test_integration_maint_chain.py, doc/improvements/pending.md"
---

**Date:** 2026-11-08 · **Status:** EXECUTED 2026-11-08 (S1 verified; S2 census landed + first production --swap via the S2 tripwire; S3 stale-record corrections landed) · **Proposal:** completed.md #491
**Area:** helpers/graph/stats.py, helpers/maintenance/db_maint.py, helpers/maintenance/store_sweep.py, tests/test_store_sweep.py, doc/improvements/pending.md

## 1. Motivation

This single proposal keeps the three user-selected slices together: verify the already-landed iFUB vectorized frontier BFS and --no-backup-sidecars test path; re-run the production store census and refresh its filing baseline; and correct stale pending records about C901 and the census. The original query.py C901 claim is stale, and a fresh repo-wide C901 run is clean, so there is no complexity refactor in this arc.

The census record diverged from the code: doc/improvements/pending.md:145-155 says the 2026-10-27 filing refresh reached the code, but _FILING_MB still held older values. A fresh 2026-11-08 measurement shows memory/convo_search.duckdb at 3,170.01 MiB, not the earlier 2,491.8 MiB estimate. The sweep must record the actual live state and distinguish its own maintenance estate from stores with a separate lifecycle owner and from unresolved legacy files.

## 2. Evidence (measured 2026-11-08, this box)

| Configuration | Result | Verdict |
|---|---|---|
| Old _ifub_diameter (scipy per BFS) | n=2,000: 1.246 s; n=6,000: 8.946 s; n=12,000: 41.452 s | baseline |
| Vectorized frontier BFS, helpers/graph/stats.py:160 | 0.820 / 4.646 / 15.942 s; 1.52× / 1.93× / 2.60× faster; same diameter at each size | adopt |
| Parity pin, tests/test_graph_stats.py:274 | 48 random connected cases plus path components, exact vs scipy | 1 passed, 18 deselected, 0.21 s |
| Integration chain using --no-backup-sidecars | tests/test_integration_maint_chain.py -q: 9 passed in 69.73 s | verified |
| db_maint CLI wiring | helpers/maintenance/db_maint.py:1399-1475; test flag maps to backup_sidecars=not args.no_backup_sidecars, production default remains True | verified |
| 2026-11-08 memory/convo_search.duckdb | 3,323,998,208 B / 3,170.01 MiB; 200,817 convo rows and 156,168 part-index rows | live measurement; supersedes 2,491.8 MiB estimate |
| 2026-11-08 _FILING_MB | refreshed to current measured MiB values in helpers/maintenance/store_sweep.py:272-285 | applied |
| Store sweep dry-run | exit 0; 12 registered stores; no size-watch ACT lines; 0.0 MiB reclaimed; two owned-elsewhere notes and two unresolved warnings | baseline check clean; unresolved stores remain visible |
| Backup inventory | 21 files / 4,229,130,159 B total; all .zst 2,201,659,311 B; raw .db 2,027,438,080 B; other sidecars 32,768 B | measured; includes duplicate snapshots and pre-restore files |
| Per-run transient | 2026-11-08 measured: pytest --basetemp=/mnt/data/tmp/store-census-chain-20261108, 9 passed in 75.41 s, transient 171,670,450 B (163.7 MiB) | supersedes the historical 7 MB (2026-10-19); no full gate needed for the measurement |
| Ruff C901 surface | .venv/bin/python3 -m ruff check --select C901 . → All checks passed | no pre-existing split; the added owner-report loop was extracted after main reached 11>10 |
| Confirmed separate owners | helpers/maintenance/rebuild_git_search.py:44,78; helpers/core/vec_search.py:10,37,83,532; helpers/maintenance/rebuild_note_search.py:84,1070,1155 | report outside db_maint |
| Unresolved stores | memory/research.duckdb built 2026-10-19 from memory/research.db; memory/findata-graph.db was an empty 0-byte artifact | owner ruled 2026-11-08: keep visible, adopt or delete nothing; observed absent from disk at the post-maint re-sweep (removed by operator) |

The iFUB measurements and parity are already established and need no benchmark redesign. S1 is a verification-and-record slice. S2 refreshes the filing using live bytes, reports verified owner exceptions, and leaves the legacy/empty stores visible rather than silently sweeping or deleting them. S3 corrects the stale C901 pending entry only; no pre-existing function needs a C901 split. The S2 owner-report loop was extracted into `_report_store_dispositions` after adding it made `main` C901=11>10; the repository-wide check is clean after that narrow extraction.

## 3. Design

1. S1 — iFUB and backup-hermeticity close-out. Re-run the parity pin and integration chain and confirm lint/format for the touched S1 paths. Keep --no-backup-sidecars opt-in: production DBMaintainer still defaults to backup_sidecars=True; the integration test uses the flag to avoid repo-anchored backup side effects.
2. S2 — production census and baseline refresh. Record exact bytes, row counts, SQLite freelist/page-size data, current backup artifacts, and store_sweep.py --dry-run output. Refresh `_FILING_MB` from the 2026-11-08 live measurements. Explicitly report memory/git_search.db as owned by helpers/maintenance/rebuild_git_search.py and memory/note_vectors.duckdb as the vector mirror owned by helpers/core/vec_search.py and rebuilt by helpers/maintenance/rebuild_note_search.py; neither belongs in db_maint's backup/reclaim registry. Do not hide two remaining warnings: memory/research.duckdb is a stale derived file (build metadata: built_at=2026-10-19, source memory/research.db, generation 615698), and memory/findata-graph.db is empty. Leave both untouched; owner disposition received 2026-11-08: keep both visible and unregistered, adopt or delete nothing. Recheck db-backup/ (symlink /mnt/data/findata-graph/db-backup) and report artifacts only; backup pruning is operator-owned. The original census acceptance also includes per-run transient size. Measured 2026-11-08 after the operator made /mnt/data/tmp writable: tests/test_integration_maint_chain.py under pytest --basetemp=/mnt/data/tmp/store-census-chain-20261108 — 9 passed in 75.41 s, transient footprint 171,670,450 B (163.7 MiB), entirely outside the repo tree. The historical 7 MB figure (2026-10-19) is superseded.
3. S3 — stale-record correction. Correct doc/improvements/pending.md:96-113 to say the two query.py masks are already gone and the current repo-wide C901 scan is clean. Append a 2026-11-08 census reconciliation to doc/improvements/pending.md:115-155, stating that the code baseline is now refreshed and preserving the earlier historical filing record. No hermeticity_watch.py split.

Rejected alternative: register all four discovered stores in db_maint. Two have documented separate lifecycle owners; one is a stale derived copy and one is empty with no established owner. Registering them would claim backup/reclaim responsibility without evidence. Rejected alternative: delete old backups or the stale cache; retention and artifact ownership remain operator decisions.

## 4. Acceptance criteria & shakedown

1. .venv/bin/python3 -m pytest tests/test_graph_stats.py -k 'ifub or diameter' -q passes; already 1 passed, 18 deselected.
2. .venv/bin/python3 -m pytest tests/test_integration_maint_chain.py -q passes; already 9 passed in 69.73 s.
3. .venv/bin/python3 -m pytest tests/test_store_sweep.py -q passes; helpers/maintenance/store_sweep.py --dry-run exited 0 over 12 registered stores, with no ACT lines and 0.0 MiB reclaimed. Owned-elsewhere stores are listed explicitly; the stale research DuckDB and empty DB remain visible as warnings.
4. .venv/bin/python3 -m ruff check --select C901 . stays clean. The owner-report loop was moved into `_report_store_dispositions` after it made `main` C901=11>10; Ruff check/format passes on edited Python files. No unrelated `hermeticity_watch.py` split.
5. After doc/helper edits, run make search-fresh APPLY=1 once on the main tree, then check. No make pytest or make test; full gates are once per arc on explicit go (gates.md).
6. Per-run transient re-measured 2026-11-08 with an approved writable TMPDIR: 9 passed in 75.41 s and 171,670,450 B (163.7 MiB) of basetemp under /mnt/data/tmp/store-census-chain-20261108; no repo-tree writes. The 7 MB value is now historical only.
7. Do not stage, commit, delete backups, or archive this proposal while slices are unfinished. On eventual EXECUTED archival, follow doc/templates/proposal.md and doc/improvements/proposals/README.md (next completed.md entry #491 after #490).

| Projected outcome | Current | After |
|---|---|---|
| iFUB parity and integration verification | S1 landed; targeted checks pass | recorded and repeatable |
| Filing baseline | code and dry-run reflect 2026-11-08 measured values | dry-run exited 0; no size-watch ACT lines |
| Unregistered stores | 2 documented separate owners; 2 unresolved warnings | no store silently swept; owner ruled 2026-11-08 to keep both warnings visible |
| Per-run transient | 7 MB from 2026-10-19 | 171,670,450 B (163.7 MiB) measured 2026-11-08 under writable /mnt/data/tmp |
| Repo C901 debt | 0 hits | 0 hits; stale pending claim corrected |
| Backup policy | current artifacts retained | unchanged; no deletion |

## 5. Risks

- The convo-search store is substantially larger than the earlier estimate. Census-time live size 3,323,998,208 B; row count alone does not establish reclaimable slack. The census records counts/freelist and makes no retention or vacuum claim (post-maint sizes: appendix note below).
- Unregistered files may have an unknown lifecycle. Mitigation: only suppress sweep warnings for the two separately documented owners; keep memory/research.duckdb and memory/findata-graph.db visible and untouched.
- The prior pending note says a baseline update landed when it had not reached _FILING_MB. Mitigation: record the exact current values in code and append a dated reconciliation without erasing historical context.

## 6. Non-goals

- No backup deletion or retention-policy change; db-backup/ pruning is operator-owned.
- No production-default change for backup_sidecars (stays True).
- No new C901 refactor; the repository scan is clean.
- No full gate run inside this arc (none authorized); targeted tests only. Full gates run once per arc on explicit go (gates.md).
- No mutation of memory/research.duckdb or memory/findata-graph.db; operator ruled 2026-11-08 that both stay visible and untouched.

## Appendix — raw measurement log (2026-11-08)

| Store | Bytes | MiB | Census notes |
|---|---:|---:|---|
| memory/convo_search.duckdb | 3,323,998,208 | 3170.01 | 200,817 convo rows; 156,168 part_index rows |
| memory/convo_search_fts.db | 510,296,064 | 486.66 | 200,817 FTS rows; freelist 0; page size 4096 |
| memory/embed_store.db | 471,949,312 | 450.09 | 195,839 embed_cache rows; freelist 0; page size 4096 |
| memory/research.db | 405,479,424 | 386.67 | graph_edges 61,653 |
| memory/corpus.db | 61,304,832 | 58.46 | 1,398 corpus_cache rows |
| memory/doc_search.db | 31,653,888 | 30.19 | 4,166 rows |
| memory/script_search.db | 4,530,176 | 4.32 | 764 rows |
| memory/memory_search.db | 3,293,184 | 3.14 | 191 rows |
| memory/md_lint_cache.db | 315,392 | 0.30 | 1,845 verdict rows |
| memory/graph.xdist-shared.duckdb | 69,480,448 | 66.26 | shared xdist graph cache |
| memory/data/agent_traces.duckdb | 222,048,256 | 211.76 | trace store |
| memory/data/sources.duckdb | 49,557,504 | 47.26 | source store |
| memory/data/model_usage.duckdb | 1,847,296 | 1.76 | usage store |
| memory/research.duckdb | 50,081,792 | 47.76 | stale derived file; built 2026-10-19 from memory/research.db |
| memory/findata-graph.db | 0 | 0 | empty artifact; no owner established |

- Current db-backup/ inventory (target /mnt/data/findata-graph/db-backup, checked 2026-11-08): 21 files / 4,229,130,159 B total; .zst 2,201,659,311 B; raw .db 2,027,438,080 B; other sidecars 32,768 B. The eight current named per-store backup artifacts sum to 1,704,221,448 B compressed. This includes duplicate snapshot artifacts and five 405,487,616 B research_backup_pre_* files, so distinguish footprint from per-run transient. No backups were deleted; these are measurements, not permission to prune.
- Per-run transient measured 2026-11-08 (approved writable TMPDIR): tests/test_integration_maint_chain.py -q under pytest --basetemp=/mnt/data/tmp/store-census-chain-20261108 — 9 passed in 75.41 s; basetemp footprint 171,670,450 B (163.7 MiB) after the run. The earlier same-day attempt failed before test setup with OSError: [Errno 30] Read-only file system: '/mnt/data/tmp/store-census-chain-20261108'; no test ran — kept as history.
- S1 benchmark log: n=2,000 diameter 13 1.246→0.820 s (1.52×); n=6,000 diameter 14 8.946→4.646 s (1.93×); n=12,000 diameter 16 41.452→15.942 s (2.60×); parity True at all sizes; per-BFS 2.850→1.323 ms (29 sweeps on 12k graph).
- Post-maint re-sweep (2026-11-08, after make maint PASS — first production --swap via the S2 tripwire): memory/convo_search.duckdb 3,323,998,208 → 1,300,508,672 B after the swap compaction (1596.16 s, ~1.88 GiB reclaimed), then 1,979,461,632 B (1887.8 MiB, dead 706 MB / 35.7%) after the re-run's one incremental pass — matching the known ~650-700 MB per-pass re-bloat. Post-maint: memory/embed_store.db 472,674,304 B; memory/convo_search_fts.db 577,032,192 B; memory/corpus.db 58.5 MiB; memory/doc_search.db 30.2 MiB. memory/research.duckdb 50,081,792 B unchanged (untouched, per owner ruling); memory/findata-graph.db absent from disk (operator-removed). The pre-maint table rows above are kept as the census-time baseline.
