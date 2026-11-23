---
title: "misc_fix maintenance sweep — gate-index heal, DuckDB cap + TMPDIR law, hyper-derive batching, stamp/apply speedups"
status: executed
filed: "2026-11-23"
executed: "2026-11-23"
completed_md: "566"
area: "helpers/misc + helpers/validators + helpers/core + helpers/graph + tests"
---

# misc_fix maintenance sweep — gate-index heal, DuckDB cap + TMPDIR law, hyper-derive batching, stamp/apply speedups

**Date:** filed + executed 2026-11-23 · **Status:** EXECUTED 2026-11-23 (archived same day,
completed.md #566) ·
**Area:** `helpers/misc/gate_query.py`, `helpers/graph/derive_hyperedges.py`,
`helpers/core/duckdb_shared.py`, `helpers/validators/static_checks.py`,
`tests/duckdb_cap.py` (new), `tests/conftest.py`, plus 37 more files
**Patch:** stgit `misc_fix` (top of the stack; 43 files) on top of 2a2bf8289.
This is the follow-up record to completed.md **#565** (the gate arc itself:
integration-loop root cause + environment rebuild).

## 1. Where this proposal comes from

The 2026-11-23 gate arc (#565) left the box with a truthful-looking but
broken observability layer and a store estate that had just been moved onto
DuckDB 2.x. A ranked survey (session findings, 2026-11-23 17:30–17:36)
surfaced five defects/tickets, and the operator ordered the work order
"lets do 5, then 1/2 and then 4 and at last 3", then sharpened it with four
rulings:

- "raise budget to 12s" (hyper_derive);
- "yes for 2, lets add cap to conftest. we cant do mass edits there.
  helpers/bench,research were the ones which sent machine to swap many
  times before. Why are we making them exempt" (no category exemption);
- "everyone shud use TEMPDIR not .tmp" (DuckDB spill dirs);
- "you ca add guard to static_checks.py" (the cap guard lives in the
  validator, not only in pytest).

Items 5, 1, 2, 4 all landed the same day. Item 3 (a clean gate/perf run on
the healed estate) is deliberately left for the operator's next arc.

## 2. F1 — gate_query index blindness (the `runs.run_id` wipe)

**Problem.** Every gate run stored after 2026-11-19 13:48:51 landed with a
NULL `run_id`: 137 of 1532 `runs` rows (qa 13, advisory 15, integrity 59,
maint 12, perf 8, verify 30) plus 2,363 `legs` / 101,743 `tests` /
101,743 `test_facts` / 280 `bench` / 1,834 `artifacts` child rows. All
`WHERE run_id = ?` joins silently missed, so `gate_query recent` printed
`run None … exit 1 PASS` for failed runs and `failures --recent 14`
declared "0 of the last 14 qa runs failed — all green" while `latest`
(reads the run row itself) stayed correct. Live probes: **all 9 tables of
outputs/gate_runs.duckdb had lost every constraint and column default**
(runs 1532 rows / 84 columns / not-null 0 / defaults 0), while sibling
stores kept theirs (research.duckdb 155/33, doc_search 8/1) and all three
sat on storage_version `v2.0.0+`. Ruled out: the DuckDB 2.x upgrade
(installed 11-20 19:26, after the first NULL row) and the
`ALTER TABLE … ADD COLUMN IF NOT EXISTS` path (preserves PK/default on
2.0.1, probed in memory). Most likely a one-off CTAS-style table rebuild
by some session between 11-19 09:39 and 13:48 — trigger unpinned.
`refresh --full` never rebuilds tables, so the index could not self-heal,
and `refresh --check` was blind to schema defects.

**Fix** (`helpers/misc/gate_query.py`). `_index_schema_defects(con)` checks
the intended DDL (runs PK + `DEFAULT nextval('run_seq')` + NOT NULL,
child-table `run_id` NOT NULL; parse_state's missing PK is tolerated legacy)
against `duckdb_constraints()` + information_schema. `_heal_index_schema(con)`
CHECKPOINTs, takes a file backup
(`outputs/gate_runs.duckdb.pre-heal-<ts>.bak`), restarts `run_seq` past max
keyed id, rebuilds the six heal tables from the SCHEMA DDL and copies rows
across with an explicit shared-column list (dropping the NULL-keyed runs
era), then rewinds `parse_state.parse_offset` per file so `refresh`
re-ingests the dropped runs. CLI: `refresh --check` reports defects;
apply **refuses** to index into a constraint-less schema without `--full`;
the per-verb auto-refresh skips with a stderr warning instead of writing
into the broken schema; `main()`'s check intercept exits 1 (feeds the
gate-fresh APPLY refusal).

**Verification.** Live heal: preserved 1395 keyed runs (ids intact,
sequence restarts at 4612), re-indexed the 137 NULL-keyed runs (9 files,
38.5 s), backup `outputs/gate_runs.duckdb.pre-heal-20261123T183254.bak`
kept; post-heal probes show 0 NULL ids in runs/legs/tests, PKs on
runs(run_id) + parse_state(src_rel); `recent --gate qa --last 5` now names
run 4624 … 4620 as exit-1 FAILs and `failures --recent 14` reports "11 of
the last 14 qa runs failed" (was "all green"). Tests: a CTAS `_wipe_schema`
helper reproduces the exact wipe; two new tests (defect detection; heal via
`main(["refresh","--full"])` asserting id preservation, refusal without
`--full`, and exactly one `.pre-heal-*.bak`). 92 gate tests green. The file
grew 185 → 267 MB (rebuild dead space, reused over time).

## 3. F2 — hyper_derive 10.7× regression, hidden by F1

**Problem.** The perf leg `hyper_derive` (budget 4.0 s, set 2026-11-01 off
a 1.71 s baseline) measured **18.34 s on a quiet box** after the DuckDB
cutover (#560/#563). `gate_query timing` was blind to it because keyed
timing rows stopped at F1's first NULL run. The profile: 15,954
`execute` calls at ≈1.06 ms each — row-at-a-time SQL in
`compact_membership` (6 calls, 9.208 s) and `apply_hyperedges`
(6.366 s).

**Fix** (`helpers/graph/derive_hyperedges.py`). Statement-shape rewrite,
semantics unchanged: chunked multi-row `VALUES` / `IN` lists
(`_SQL_CHUNK = 500`; an insert-shape bench showed executemany is *not*
faster — 33.6 s vs 0.77 s for chunked VALUES on 15k rows); the
compact anti-join stages wanted membership into `temp._compact_want` and
runs one COUNT/DELETE; edge/props/valid_from restamps only where the
stored value differs; incidence inserts/upserts and the weight/role
convergence UPDATEs are batched UPDATE-FROM. Every statement runs on BOTH
engines (the store is engine-neutral since #560): DuckDB names bare VALUES
columns `col0…` while SQLite wants `column1…` — the rename wrapper picks
per `helpers.core.vec_search._is_duckdb_conn`; `IS DISTINCT FROM` is the
engine-neutral negation (house doctrine from `sync_tags.py:492-504`).

**Verification.** 41 tests across the five affected suites, both engines
(idempotence, convergence, validity windows). Old-vs-new A/B: dry-run
STDOUT and STDERR IDENTICAL (first A/B attempt was invalid — the old copy
must live INSIDE the repo tree because seed paths resolve via `__file__`).
Live dry-run **18.34 s → 4.58 s**. Budget re-baselined to **12.0** (operator
ruling, comment records the history; the perf-leg entry is the only 4.0→12.0
change; historical prose mentions left untouched).

## 4. F3 — the DuckDB memory cap is a law now, not an opt-in

**Problem.** A bare `duckdb.connect()` claims DuckDB's default memory_limit
(~70% of RAM = 11.2 GiB here); concurrent claims drove the 2026-11-23 swap
storm (#565). The cap introduced that morning (`MEMORY_LIMIT = "2GB"`) was
opt-in per call site: a census found 115 `duckdb.connect` sites under
helpers/ + app.py with 35 uncapped (10 file-backed production, 16
in-memory production/maintenance, 8 bench, 3 research one-shots, plus the
git textconv driver), and tests/ had 231 uncapped sites in 75 files. There
is no env knob. Separately, an in-memory DuckDB spills to a **cwd-relative
`.tmp`** by default (measured), dirtying the repo root.

**Fix.**
`helpers.core.duckdb_shared.memory_cap_config()` now returns
`{"memory_limit": MEMORY_LIMIT, "temp_directory": temp_directory()}`
(TMPDIR → TEMP → TMP → `tempfile.gettempdir()`; operator ruling "everyone
shud use TEMPDIR not .tmp"). All production/maintenance file-backed and
in-memory sites pass `**memory_cap_config()`; the 11 bench/research sites
were capped too after the operator struck the exemption (the doctrine
comment had grandfathered them — that carve-out predated the swap
history). `helpers/misc/parquet_textconv.py` (bare git script, cannot
import the package) passes the same limits inline; the guard checks its
literal against `MEMORY_LIMIT`. Test-side, `tests/duckdb_cap.py` (new)
installs a **default** cap wrapper via `tests/conftest.py` at import — a
caller's own `config` keys survive and its own `memory_limit` /
`temp_directory` win; no mass test edits (the wrapper must live in a
module because inside a test the outermost `duckdb.connect` is the
hermeticity guard, which has no `__wrapped__`).

**Guard.** The AST classifier moved to `helpers/validators/static_checks.py`
as the single home: `check_duckdb_memory_cap(scope=None)` registered in
CHECKS right after "SQLite helper usage" (dirty-scope aware, tests/
exempt — runtime-capped by the conftest wrapper), with the pytest twin in
`tests/test_static_checks.py` driving the same `_duckdb_cap_file_violations`.
Compliant forms: a cap-helper call inside the connect call; a `**name`
assigned from a cap helper earlier in the file; or the inline literal equal
to MEMORY_LIMIT. `_DUCKDB_CONNECT_ALLOWLIST = {}` is the escape hatch.
No doc updates needed: no live doc lists the sqlite guard either (only
archives).

**Verification.** Census re-run: 115 sites = 103 capped + 3 `**kw` via
`connect_kwargs()` + 12 formerly-exempt now capped, NONE = 0. Capped
connections report `current_setting('memory_limit')` = "1.8 GiB" (2 GB
decimal) and `temp_directory` = /mnt/data/tmp. Full non-live suite with the
conftest cap: **6632 passed / 12 skipped / exit 0 / 20:51** (the skips are
pre-existing missing-fixture marks). 188 static/lint/dirty-scope tests
green; `static_checks.py` full sweep shows "✓ DuckDB memory cap" (the two
`--full`-only Python-syntax failures in gitignored `bench_data/convo_gamma`
probe scratch are pre-existing and outside the qa dirty scope).

## 5. F4 — stamp-centrality creep is the all-universe pass

**Problem.** The `stamp-centrality` maint leg crept 59.17 → 66.92 → 76.19
→ 93.28 → 114.6 s over five days (watch item, pending.md :6). Scratch
profiles on store copies: build+stamp 63.3 s of which
`scipy_bridge.full_universe_stats` (forked dijkstra over the whole
projection) is ~57–60 s; the rest ≈ 6 s. NOT node growth — the live
projection is n=22,840 / 69,913 edges, same scale as the 27.5 s baseline
of 2026-09-26. The old worker materialised a shard-sized float distance
matrix per fork child (1–2 GB temporaries under parallel load).

**Fix** (`helpers/graph/scipy_bridge.py`). `_full_universe_worker` now
dijkstra's in 256-row blocks and reduces per block to **integer hop
histograms** (`np.bincount` reshaped), deriving reach/sum_d/harmonic/ecc
and the four structure scalars from the counts — no float D retained.

**Verification.** Live projection, parity asserted: jobs=4 70.0 → 46.1 s
(×1.52), jobs=2 121.4 → 61.3 s (×1.98), jobs=1 ~1.01 (the gain is
memory-pressure-shaped, not serial). Closeness bit-identical; harmonic
max|Δ| 1.8e-12 on values up to 10,620 (re-associated sum); structure
identical (diameter 11, radius 6, APL 4.303870439777702, reachable pairs
495,451,450, components 65, largest 22,259). 71 scipy_bridge +
centrality_cache tests green (new hand oracle, block-size sweep incl.
blocks of 1, empty graph, jobs parity). The remaining ~46 s is scipy's
heap dijkstra (~98 s CPU, ≈4.3 ms/source); the next lever is a compiled
queue-BFS hop-histogram kernel (Rust/PyO3 rung already fired for
BFS-shaped work, pending.md :799) — operator's call. A real-run re-time of
`make stamp-centrality` stays owed to the maint chain (operator-owned).

## 6. F5 — the script-index apply re-walked every Python unit

**Problem.** `rebuild_script_search --sandbox` measured 10.39 s; the
profile put 6.24 s in `_import_candidates` — `ast.walk` over 1,439,015
AST nodes (60% of the apply) to collect imports, on every apply, for
unchanged units. The pending.md :58 hypothesis ("`--allow-stale` forces
full rebuilds") was disproved: `--allow-stale` only lifts the
apply-stamp guard (`rebuild_common.stale_apply_reason`).

**Fix** (`helpers/maintenance/rebuild_script_search.py`). New
`_iter_statements(tree)` walks statement lists only (body/orelse/finalbody
+ handlers/cases bodies); imports are statements nested only via
compound-statement bodies, so the import set is identical.

**Verification.** Four new tests, including equality against the old
`ast.walk` semantics on every compound nesting form (try/except*, match
cases, async forms) and on a sample of real repo sources. `_import_candidates`
6.24 → 0.72 s, `_collect_units` 8.43 → 2.70 s; with a warm embed cache the
apply is ~4.2 s. pending.md :58 bullet CLOSED (all three deferred candidates
shipped; re-open trigger kept).

## 7. F6 — master_query `notes MISSING`

`helpers/misc/search_tui.py::index_ages` still mapped the notes leg to
`memory/research.db` — deleted by the P4 SQLite retirement (#552, 11-21).
Now maps to `memory/note_search.duckdb`; the freshness line reports notes
again. `search_tui_app.py`'s stale "notes writes research.db" hint was left
alone (UI wording, unverified claim). Test updated.

## 8. Docs

pending.md :6 watch item updated with the root cause + histogram numbers +
the compiled-kernel reopen note; :58 flipped to CLOSED with the corrected
`--allow-stale` explanation.

## 9. Eval-gate

No change alters query-visible semantics (rosters, crosswalks,
hierarchies, extractor rules) — no eval-gate bullet required. The
`gate_query recent/failures/timing` corrections restore the documented
behaviour of a derived index; the store data is untouched (hyper_derive
ran dry-run only; the 34 new hyperedges / 90 incidences it reports are
pre-existing upstream drift awaiting the operator's `--apply`).

## 10. Follow-ups

- Item 3: one clean gate battery (qa + advisory + perf, and integration)
  on the healed estate — operator go.
- Compiled BFS hop-histogram kernel (would take stamp-centrality's
  remainder below ~10 s) — optional, operator's call.
- Validator-side duckdb `.tmp` spill: covered by the cap config; no
  `.gitignore` entry added (no `.tmp/` appeared in practice).
- 549k DeprecationWarnings from `snapshot_db.py:1739` (sqlite3 datetime
  adapter, pre-existing) — unaddressed, unrelated.
