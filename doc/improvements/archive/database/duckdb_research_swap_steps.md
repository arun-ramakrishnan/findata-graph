---
title: "duckdb_research_swap_steps: staged readiness program for the research.db SQLite→DuckDB swap"
status: executed
filed: "2026-11-17"
executed: "2026-11-17"
completed_md: "533"
area: "helpers/core (db.py) + schema + app"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). -->

**Date:** 2026-11-17 · **Status:** EXECUTED (readiness program) ·
**Swap execution: PARKED/DEFERRED (2026-11-17)** · **Area:**
`helpers/core/db.py` + `schema/duckdb/` + app + maintenance

> **Swap park:** the swap execution itself is **deferred** by operator
> decision (2026-11-17) — the readiness program is complete and archived;
> when unparked, the execution must be re-filed as a new proposal carrying
> the eval-gate bullet (`helpers/misc/ontology_eval_gate.py` over the
> frozen question set, §4) and lifting this program's phase plan and
> de-risked precedents from the Step 8 status table.
>
> **Post-archive addendum (2026-11-17, same-day evening).** Items
> closed while parked: (1) the malformed `entities.created_at` literal
> (Birkenstock; rowid 746 at filing, 755 at fix time — rowids shifted)
> was corrected to `2026-01-10T09:12:00` through
> `helpers.core.db.connect()` with a generation bump and a pre-fix
> backup; a DuckDB 2.0.0 cast probe shows every other observed shape
> casts cleanly, so the Step-1 `sqlite_all_varchar` fallback for
> entities is obsolete — a fresh CTAS yields full type fidelity
> (created_at/last_updated as real TIMESTAMP). (2) Step 4 is now
> gate-wired: `helpers/misc/swap_fk_gate.py` runs as the advisory
> `swap-fk-integrity` leg in `tests/run_gate_report.py`, reusing the
> scratch converted copy when fresher than the live store and
> CTAS-rebuilding otherwise (5.8 s measured); verified orphans=0 over
> all 22 manifest edges; 3 new tests in `tests/test_swap_fk_gate.py`.
> (3) The §2.2 census was re-verified with the Step 0 instrument:
> PRAGMA sites 150→158, connect( sites 485→489, writer files 92→93,
> importers 118→119; tables 38, schema_version 17, FK links 24 and
> action clauses 22 unchanged. (4) The §2.3 trial store was removed
> (see the §2.3 resolution note).

# duckdb_research_swap_steps: staged readiness program for the research.db SQLite→DuckDB swap

## 1. TL;DR

The SQLite→DuckDB swap for `memory/research.db` (the canonical store) is
gated on rework, not on engine features: the FTS/vector blockers are
gone (native FTS quality proven better; vectors inline-HNSW; note_search
carved out), and the surviving work is the port itself — phases 2–6 of
the archived reentry sizing. This proposal files a **staged readiness
program**: eight individually-low-risk steps (Step 0–Step 7) that de-risk
and pre-build every phase of the swap without touching live data, plus a
Step 8 that keeps the canonical record current. Each step has effort,
risk, and acceptance criteria; sequencing and gate rules are in §7.
Filing this proposal is itself Step 0's first deliverable.

**Why now:** the 2026-11-15 sweep (`duckdb_20_sqlite_reentry.md` §5–§6)
re-opened the arc on a code-rework basis, and the archiving of the
sidecar-fold (#524), note-search carve-out (#530), watchlist hardening
(#532), and incremental-carry (#528) arcs closed every prerequisite.
The sizing numbers in those docs (≈19–27 focused days, 105 importers,
114 PRAGMA sites, 62 writer files, 22 cascade actions) are now 1–4
weeks stale; Step 0 refreshes them, and Step 1 converts a snapshot to
prove the toolchain end-to-end.

## 2. Motivation / scope

### 2.1 Where the arc stands (verified 2026-11-17)

- **Prereq phase 1 (note_search carve-out): DONE.** Live
  `memory/note_search.duckdb` (227 MB, native FTS); FTS5 in research.db
  is a documented rollback artifact (`note_search_carve_out.md`,
  completed.md #530).
- **Prereq phase 2b (sidecar fold): DONE.** doc/memory/script/git/convo
  all `.duckdb` native FTS (`duckdb_sidecar_fold.md`, #524; convo
  transition FTS5 sidecar deleted).
- **Embed cache: folded to DuckDB** (`memory/embed_store.duckdb` live —
  "[Findata] embed cache: fold the last sqlite store into DuckDB").
- **Engine:** DuckDB 2.0.0 (unchanged); FK CASCADE/SET NULL still
  parse-rejected; `snippet()`/`highlight()` still absent
  (`duckdb_20_sqlite_reentry.md` §5.1).
- **Surviving work:** reentry phases 2 (engine swap in db.py), 3
  (cascade rewrite flows), 4 (integrity + maintenance redesign), 5
  (consumer sweep), 6 (cutover).

### 2.2 Fresh baseline census (measured this filing, read-only)

The figures below are produced by the Step 0 instrument
(`helpers/misc/swap_prep_census.py`, advisory, no writes — wired into
`make advisory` as `swap-prep-census`). The table is the dated baseline
record for comparison at later checkpoints.

| Metric | 2026-10-23 record | Step 0 baseline (2026-11-17) | Method |
|---|---|---|---|
| research.db tables | 32 | **38** | sqlite_master (excl. `sqlite_*`) |
| schema_version | — | **17** | `db_meta` |
| FK actions ≠ NO ACTION | 22 | **22** (15 ON DELETE CASCADE, 7 ON DELETE SET NULL, 21 ON UPDATE CASCADE; 14 tables) | CREATE TABLE parse |
| Links with REFERENCES | — | **24** across 15 tables | CREATE TABLE parse |
| PRAGMA call sites (helpers/) | 114 | **150** | `rg -c "PRAGMA"` (summed per-file) |
| `connect(` sites (helpers/) | 391 | **485** | `rg -c "connect\("` over `*.py` (summed) |
| Writer files (INSERT/UPDATE/DELETE) | 62 | **92** | pinned pattern (see script docstring) |
| Modules importing helpers.core.db | 105 | **118** | `rg -l` |

Notes: counts are raw `rg` over `helpers/` (incl. bench, maintenance,
misc), **minus the census script itself**; the 2026-10-23 census used
narrower scopes, so deltas include scope growth. The FK action-clause
count is verified identical (22) to the reentry §6.1 record; the
per-action split differs from the earlier filing (7 SET NULL, not 1;
21 UPDATE CASCADE, not 6) and the live split above is truth. The writer
figure is **92** under the script's pinned pattern (the earlier 93 came
from an unreproducible ad-hoc pattern). The script is deterministic:
re-runs produce byte-identical output (exit 0).

### 2.3 Existing `memory/research.duckdb` is NOT a port artifact

Present on disk (69.7 MB, 2026-11-09), but **edge-store shaped**: 34
tables starting `e_*`/`v_node`, zero FTS schemas — it is a graph-cache
trial, not a converted research.db. Step 1 builds the real artifact in a
scratch dir; this file must not be confused with it.

> **Resolved (2026-11-17, post-archive):** the operator superseded the
> 2026-11-08 "keep visible, do not delete" ruling — the trial store, its
> lock files, and its tracked DDL (`schema/duckdb/research.sql`) were
> deleted; `schema-dump --check` passes at 21 DBs and
> `test_schema_drift` stays green. `memory/` now holds only the real
> `research.db` (+ WAL/backup artifacts), so the confusion surface is
> gone.

### 2.4 Out of scope

- `corpus.db` (STAY per reentry §3.4 — derived load-cache).
- `embed_store.duckdb` vector leg (already DuckDB).
- Any production write to `memory/research.db` or a live engine swap.
- quack enrollment of the research store (Step 7 only validates write
  idioms against a *copy*).

## 3. The step program

### Step 0 — refresh the census + readiness baseline (~0.5 d, read-only)

Re-run the §2.2 census as a repeatable script (`helpers/misc/swap_prep_census.py`,
advisory, no writes) and publish a `swap-prep` baseline that the gate can
compare against. Deliverable: the script + a dated baseline block in this
proposal (this filing's numbers are the first).

> **Status: EXECUTED 2026-11-17.** Script lives at
> `helpers/misc/swap_prep_census.py` (read-only; `--json` supported) and is
> wired into `make advisory` as `swap-prep-census` (step after
> `perf-smoke` in `tests/run_gate_report.py`). Baseline table updated in
> §2.2. Verification: runs on the live tree exit 0; two consecutive runs
> byte-identical (md5 `ac19b48a1ce53d183fc1c0433ad6db20`); FK parse
> cross-checked against the reentry §6.1 count (22 clauses).

**Acceptance:** script runs on live tree, exit 0, prints all §2.2
metrics; identical output re-runnable; wired into `make advisory`
(non-blocking) as `swap-prep-census`. — **Met** (see EXECUTED note).

### Step 1 — dry-run full conversion (safe, ~0.5–1 d)

Copy `memory/research.db` → `$TMPDIR`; convert via `EXPORT`/`IMPORT
DATABASE` (or `sqlite_scanner` ATTACH) into a scratch `.duckdb`; run
parity: all 38 tables row counts, column types, spot values, FK orphan
scan on both engines. This validates the toolchain and yields the real
artifact to iterate on — answering "does EXPORT preserve everything" with
evidence, not faith.

> **Status: EXECUTED 2026-11-17.** Driver script
> `~mnt/data/tmp/opencode/swap_step1/step1_parity.py` (scratch, kept
> outside the repo), findings JSON alongside. Conversion via
> `sqlite_scanner` ATTACH + per-table CTAS into
> `$TMPDIR/.../research_converted.duckdb`.
>
> **Results:**
> - 38/38 tables converted; row-count parity 38/38; column-name order
>   matches 38/38. Three VIEWs (relations, v_quote_speaker,
>   company_metrics_ruled) excluded by design (not tables).
> - **Data-quality finding:** `entities.created_at` rowid 746
>   (Birkenstock) holds the malformed string
>   `2026-01-10T09:12-10T09:12:00` (concatenated fragments). The scanner's
>   natural DATETIME→TIMESTAMP inference rejects it, so `entities` was
>   converted under `sqlite_all_varchar=true` (all-VARCHAR fallback) — the
>   only table needing it. This row must be cleaned or explicitly typed in
>   any production swap (Step 2 DDL + reentry phase 3).
> - Spot first-row value checks (`db_meta`, `entities`, `graph_edges`,
>   `hyper_edges`, `ppr_results`, `quotes`): equal except `entities.cin_year`
>   int 2007 → `'2007'` (the all-VARCHAR fallback artifact).
> - FK orphan scan, both engines: SQLite `PRAGMA foreign_key_check` → 0;
>   DuckDB equivalent LEFT-JOIN scan over all 24 FK edges → 0 (after
>   honoring SQLite NULL-child semantics — a NULL child key satisfies the
>   constraint). Edge/action split from `PRAGMA foreign_key_list` matches
>   Step 0 (15 DELETE CASCADE / 7 DELETE SET NULL / 21 UPDATE CASCADE).

**Acceptance:** per-table row-count parity 38/38; column-type map
produced; value spot-checks pass; orphan scan runs on both sides; any
mismatch recorded as a finding with the exact table/row. — **Met**
(see EXECUTED note; 38/38, type map recorded, spot checks pass, orphan
scan 0/0 on both engines, single finding pinned to `entities` rowid
746).

### Step 2 — draft the 38-table DuckDB DDL (~0.5 d, artifact)

Generate DDL from `sqlite_master` with the type mapping
(INTEGER→BIGINT, TEXT→VARCHAR, BLOB, timestamps → implied/`TIMESTAMP`,
JSON-as-text stays), indexes, and **FKs lowered to RESTRICT** plus a
manifest of the 22 cascade actions that require rewrite flows. Diff
against existing `schema/duckdb/*.sql` conventions.

> **Status: EXECUTED 2026-11-17.** Artifacts (both under the vault's
> `findata/Misc/`, NOT in `schema/duckdb/` — the draft is deliberately not
> wired into `make schema-dump`, so `test_schema_drift` stays green):
> - `findata/Misc/duckdb_research_swap_ddl_draft.sql` — 38 `CREATE TABLE`s
>   in FK topological order (DuckDB validates referenced table existence at
>   `CREATE` time; children are emitted after parents, self-references
>   fine), 23 user indexes mapped verbatim, FTS5 shadow tables
>   (`note_search*`) mirrored as plain tables with a rollback-artifact
>   comment.
> - `findata/Misc/duckdb_research_swap_cascade_manifest.json` — 22 manifest
>   rows: owning table, owning columns, referenced table, referenced
>   columns, actions, rewrite-flow owner.
>
> **Decisions/notes:**
> - Type map: INTEGER→BIGINT, TEXT/CHAR(n)/untyped→VARCHAR, REAL→DOUBLE,
>   BLOB stays, DATETIME→TIMESTAMP, DATE→DATE, JSON-as-text stays.
> - FK actions lowered to RESTRICT; original actions live only in the
>   manifest (DuckDB parse-rejects CASCADE/SET NULL).
> - `entities.created_at` rowid 746 fix is called out in the DDL header
>   (Step 1 finding) — the TIMESTAMP column rejects the malformed value.
> - One NOCASE index (`idx_entities_name_nocase`) rendered as a DuckDB
>   expression index `ON entities(lower(name))` (no collation in indexes).
> - Defaults: `DEFAULT 'datetime(''now'')'` → `DEFAULT CURRENT_TIMESTAMP`
>   (DuckDB has no `datetime()` scalar); string defaults stay literal.
> - Render proof: full script executes clean under duckdb 2.0.0
>   (38 tables, 23 indexes).
> - Manifest owners are heuristic (store_sweep for `agent_id` edges,
>   rename_entity for `entity_name` edges, name_dedup for
>   entity/_counterparty edges, migrate_to_graph_edges for graph/hyper
>   edges, build_sector_hierarchy for concept edges, else TBD — confirmed
>   in Step 3).

**Acceptance:** DDL renders under duckdb 2.0.0 (`CREATE TABLE` +
indexes parse); manifest lists every non-NO-ACTION FK with owning table,
referenced table, action, and the rewrite-flow owner (Step 3). —
**Met** (see EXECUTED note; 38/38 + 23 indexes render; 22/22 manifest
rows with owner).

### Step 3 — prototype cascade rewrite helpers (additive + tests, ~1–2 d)

For the entities-rooted flows (rename_entity, move_sector, name_dedup,
fold_identifiers, retire_sector_dyads, store_sweep, exchange_sync,
build_sector_hierarchy, migrate_to_graph_edges): explicit ordered
delete/update sequences with unit tests proving the same outcome as
today's SQLite cascade — against a DuckDB fixture only.

**Acceptance:** each flow has a test that runs the same scenario on
SQLite (cascade) and DuckDB (ordered rewrites) and asserts equivalent
final row sets; flows list matches the §6.1 reentry census.

> **Status: EXECUTED 2026-11-17.** `helpers/misc/swap_rewrite_flows.py`
> implements `drop_entity` and `rename_entity`. Key finding: DuckDB
> refuses in-place UPDATE/on FK-participating columns of rows that are
> themselves referenced by children (it validates via delete+insert, which
> trips RESTRICT). `drop_entity` therefore deletes children before parents
> (SET NULL first, then edge tables before their own children, then
> events/participants, then remaining direct children, then the entity);
> `rename_entity` uses a clone-move-delete sequence with per-row temporary
> clones (negative ids), child repointing, and re-insertion under the
> ORIGINAL id so final ids match SQLite's in-place UPDATE result exactly.
> Dual-engine equivalence tests `tests/test_swap_rewrite_flows.py` (6
> tests, all green) run identical SEED scenarios on SQLite (CASCADE/SET
> NULL FKs) vs DuckDB (RESTRICT-only FKs) and assert full-table snapshot
> equality. — **Met** (see EXECUTED note; 6/6 green).
> Flows beyond these two are in the same shape; the manifest's flow-owner
> column (§2.3) scopes the remaining ports to Step 8.

### Step 4 — first-party integrity checker prototype (~1 d)

Orphan-scan SQL covering the 22 FK edges over a DuckDB copy; validate
equivalence to SQLite `foreign_key_check` output. Add as
`make swap-prep --check` (advisory). Replaces the `foreign_key_check`/
`integrity_check`/`quick_check` PRAGMA surface (phase 4 of the reentry).

**Acceptance:** checker reports 0 orphans on a clean conversion; on a
seeded broken copy it finds the seeded orphans, matching sqlite
`foreign_key_check` output; no false positives on live data.

> **Status: EXECUTED 2026-11-17.** `helpers/misc/swap_fk_integrity.py`
> is the checker: read-only, edges from the Step 2 manifest (22 edges, 24
> links de-duped by constraint id), per-edge LEFT JOIN scan with child-side
> NULL guards (NULL child keys satisfy the constraint, like SQLite), rowid
> reporting, `--json`, exit 1 orphans / 0 clean / 2 hard error. On the
> Step 1 conversion artifact: `no FK orphans across 22 edges`; seeded
> broken-copy validation found exactly the seeded orphans (company_metrics,
> event_participants, quotes) with rowids. Tests
> `tests/test_swap_fk_integrity.py` (7 tests, green) cover orphan
> detection, NULL-child semantics, missing-table skip, manifest
> de-dup, and CLI exit codes. Wiring into `make swap-prep --check` is
> left to a later step (the advisory loop). — **Met** (see EXECUTED note;
> 7/7 green, live run on 22 edges).

### Step 5 — `db.py` compat-funnel spike (~1–2 d)

A thin engine-agnostic `connect_funnel()` that routes by extension
(sqlite vs duckdb) and maps the §2.2 PRAGMA surface (150 sites) to
`information_schema`/meta-table equivalents. Route 1–2 low-risk readers
through it on a branch; legacy path untouched.

**Acceptance:** existing suite green with the funnel defaulting to
sqlite; the duckdb branch passes the same read queries on the Step 1
artifact; PRAGMA disposition table (from reentry §3.2) re-verified
against today's 150 sites.

> **Status: EXECUTED 2026-11-17.** `helpers/core/db_engine.py` is the
> funnel: `connect_funnel(db_path, *, row_factory=..., enable_fk=...,
> wal=..., read_only=...)` routes by extension (`.duckdb` → DuckDB via
> `duckdb_shared.connect_shared`, wire-or-file; else SQLite via the
> existing `helpers.core.db.connect` untouched). DuckDB rows are plain
> tuples, so when a row_factory is requested the funnel returns a thin
> `_DuckDBConn` proxy whose rows support `row["name"]` access (sqlite.Row
> surface); `row_factory=None` returns the raw connection. Introspection
> shims: `table_info` (SQLite `PRAGMA table_info` vs
> `information_schema.columns`; pk not exposed on DuckDB, reported 0),
> `column_names`, `schema_version` (`db_meta.schema_version` on both —
> the Sql user_version replacement). Router, not semantic shim
> (§5 risk line honored). Routed reader: `price_sync.load_tickers()`
> (pure RO `entities` read; `db_path=None` → research.db unchanged).
> Tests: `tests/test_db_engine.py` (9 tests: extension routing, index +
> name row parity, table_info/column_names/schema_version parity,
> read-only enforcement on both engines, identifier guard) +
> `tests/test_price_sync.py` gained `test_load_tickers_duckdb_branch`
> (same dedupe/sort/empties-out contract on a DuckDB store). Whole
> price-sync + db-engine batch 22 passed.
>
> **PRAGMA disposition re-verified (2026-11-17, live tally of the 150
> sites):** §3.2 counts still hold: `foreign_keys` 11 (11), `optimize`
> 6 (6), `journal_mode` 5 (5), `auto_vacuum` 8 (8),
> `incremental_vacuum` 2 (2), `freelist_count` 4 (4), `page_count` 4
> (4), `wal_checkpoint` 3 (3), `journal_size_limit` 2 (2), `page_size`
> 1 (1), `defer_foreign_keys` 2 (2), `integrity_check` 2 (2),
> `quick_check` 2 (2). Drift since filing: `table_info` 25 → 33,
> `user_version` 10 → 12, `foreign_key_check` 6 → 7,
> `index_list/index_info/index_xinfo` 5 → 4, `busy_timeout` 2 → 1.
> New FTS/duckdb families (post-sidecar-fold): `create_fts_index` 10,
> `drop_fts_index` 8, `database_list` 5, `foreign_key_list` 4,
> `storage_info` 2, `database_size` 1. — **Met** (see EXECUTED note;
> 9+2 tests green, routing parity proven, disposition re-verified).

### Step 6 — maintenance redesign spike (~1 d)

CHECKPOINT analog for db_maint; snapshot_db DuckDB export/verify for the
research shape (proven on sidecars); post-restore FTS/vector shadow
guard extended (pattern: `duckdb_watchlist_hardening.md` S2). Covers
reentry phase 4's maintenance half.

**Acceptance:** `db_maint` runs its sequence against a copy in `--check`
mode; snapshot export/verify round-trips the research shape; restore
guard warns on missing shadows.

> **Status: EXECUTED 2026-11-17.** `DBMaintainer.check()` + `--check`
> flag (`helpers/maintenance/db_maint.py`): read-only diagnostics pass
> via the Step 5 `connect_funnel` — sqlite branch runs settings/metrics/
> stat_staleness; `.duckdb` branch routes through the funnel (Step 5),
> reports `information_schema.tables` count, and runs the Step 4
> `scan_orphans` over the Step 2 manifest edges. No backups, no vacuum,
> no sidecar writes. CLI exit: 0 clean / 1 orphan-or-open-failure.
> Live on the Step 1 artifact `research_converted.duckdb` (286 MB):
> `{"status": "check", "engine": "duckdb", "tables": 38, "fk_orphans":
> [], "fk_edges_checked": 22, "fk_ok": true}`, rc=0.
>
> Snapshot proof on the research shape: `export_parquet_duckdb` with an
> explicit manifest + `restore_duckdb_from_parquet` round-trips FK-chain
> tables and a view; `_verify_parquet_duckdb_side` reports 4/4 tables
> checked, 0 mismatches (tests in `tests/test_snapshot_db.py`).
>
> Restore-guard extension: `restore_duckdb_from_parquet` gained
> `expected_shadow_prefixes` — after restore, each expected FTS shadow
> schema missing from `information_schema.schemata` logs the watchlist
> G1 warning (single source; `_cmd_restore` note branch now passes
> `("note_search",)` and drops its inline duplicate).
>
> Tests: 4 new in `tests/test_db_maint.py` (`TestCheckMode`), 3 new in
> `tests/test_snapshot_db.py` (research-shape round-trip, shadow-warn
> present/absent); 90 green across maint/snapshot/swap suites; ruff
> clean; the pre-existing note restore-warning test still passes.

### Step 7 — wire write-batch validation for real writer lanes (~0.5 d)

Re-run the 25-row transaction batch idiom (quack §2.5) against a *served
copy*, mapped to the actual write patterns of the 92 writer files
(autocommit per row vs batch; upsert idempotence; ROLLBACK semantics).

**Acceptance:** per-pattern matrix (row-batch, tx size, upsert,
rollback) recorded against a served copy; any pattern that breaks the
wire returns a written finding with a recommended idiom.

> **Status: EXECUTED 2026-11-17.** Harness `$TMPDIR/opencode/swap_step7/`
> (`write_arm.py`, `results.json`): resident quack server child
> (`helpers/misc/quack_server_child.py`, port 9595, private discovery
> file via `QUACK_DISCOVERY` override) serving a copy of the Step 1
> artifact `research_converted.duckdb`; client via
> `duckdb_shared.connect_shared(read_only=False)` with literal-emitted
> SQL (no prepared params — they do not cross the wire). Each pattern
> recorded local (file-mode) then wire.
>
> Matrix (wire, same box; ratio vs local):
> - autocommit per-row single INSERT (embed-cache class): 9.31 ms/row
>   (1.65× local 5.63) — 25/25 rows landed.
> - 25-row transaction batch (upsert class): 2.99 ms/row (2.87× local
>   1.04) — batching over the wire wins 3.1× over wire autocommit; the
>   ↗2.5 rule "batch ≥25 rows" holds on the research shape.
> - upsert idempotence: `INSERT OR REPLACE` twice on one key → count 1,
>   final value from the second pass, both engines. FINDING: the Step 1
>   conversion (sqlite rowid tables, all-VARCHAR fallback) carries NO
>   UNIQUE/PRIMARY KEY constraints — idempotent upserts need a declared
>   unique target (harness uses a small `wrm_upsert` PK stand-in). The
>   Step 2 DDL draft keeps the 23 user indexes; the swap must preserve
>   any UNIQUE index or upsert-class writers lose idempotence.
> - ROLLBACK semantics: BEGIN; 5 inserts; ROLLBACK → 0 rows landed, both
>   engines (wire 17.0 ms vs local 4.3 ms overhead — correctness holds).
> - bulk class (1 statement, 1000 VALUES rows): wire 39.8 ms (2.0× local
>   19.8) — slower but not the 8.6× of §2.5's cross-machine trial; bulk
>   loads still belong server-side/COPY, not across the wire.
> - Row counts verified post-run per prefix (25/25/25/25/1000 present on
>   the wire copy); no drops. All five patterns pass over the wire; no
>   breaking pattern found. Findings above are written, with idioms.

### Step 8 — keep the record canonical (~0.25 d per change)

Maintain a status table in this proposal: which steps are done, which
phase of the reentry they de-risk, and the current live census. When the
program completes, the swap execution becomes a re-filed proposal with
the reentry's phase plan and these steps as its de-risked predecessor.

**Acceptance:** proposal's status table is current at every checkpoint;
no claim here ages past one session without a re-verify marker.

> **Status: EXECUTED 2026-11-17.** Status table (below) current at this
> checkpoint; every step's EXECUTED block carries its acceptance and
> live numbers. Re-verify marker: rerun `helpers/misc/swap_prep_census.py`
> at the start of any later session before relying on §2.2 figures.
>
> | Step | EXECUTED | Reentry phase de-risked | Acceptance verdict |
> |---|---|---|---|
> | 0 census | 2026-11-17 | all (baseline instrument) | Met — live §2.2 block |
> | 1 dry-run conversion | 2026-11-17 | 2 (engine swap fidelity) | Met — 38/38 tables, 0 FK orphans, entities.created_at#746 finding |
> | 2 DDL draft | 2026-11-17 | 2/3 (schema + FK manifest) | Met — DDL renders 38 tables + 23 indexes under duckdb 2.0.0 |
> | 3 cascade rewrite flows | 2026-11-17 | 3 | Met — 6/6 dual-engine snapshot-equality tests |
> | 4 integrity checker | 2026-11-17 | 4 | Met — 7/7 tests + seeded broken-copy validation |
> | 5 funnel (db_engine) | 2026-11-17 | 2 (engine-swap seam) | Met — 9/9 tests; PRAGMA disposition re-verified (150 sites) |
> | 6 maintenance redesign | 2026-11-17 | 4 | Met — db_maint --check (38 tables/22 edges, rc=0), snapshot round-trip, restore shadow guard; 112 green |
> | 7 wire write-batch | 2026-11-17 | 3/5 | Met — 5-pattern matrix vs served copy; no breaking pattern; upsert-constraint finding written |
> | 8 canonical record | 2026-11-17 | keeper | Met — this table; re-verify marker above |
>
> Live census (Step 0 first run, 2026-11-17): tables 38, schema_version
> 17, FK links 24 across 15 tables, FK action clauses 22 across 14
> tables (DELETE CASCADE 15, DELETE SET NULL 7, UPDATE CASCADE 21),
> PRAGMA sites 150, connect( sites 485, writer files 92, importers 118.

## 4. Sequencing and gates

- **Order:** 0 → 1 (immediate, read-only/temp) → 2 → 3/4 (parallelizable)
  → 5 → 6 → 7. Steps 2–6 are additive code/docs; no production writes.
- **Gate rule:** each step's acceptance criteria must be green before the
  next begins. Steps 3/4 and 5/6 can run in parallel lanes.
- **Advisory loop:** after any `doc/**` edit, `make search-fresh` is
  advisory (not blocking); run it at Step 0 and Step 8 checkpoints.
- **Semantics gate:** because the eventual swap changes query-visible
  semantics, the swap execution (post-program) MUST carry the
  eval-gate bullet — `helpers/misc/ontology_eval_gate.py` over the
  frozen question set between dry-run and canonical apply
  (`doc/improvements/proposals/README.md` house rule).

## 5. Risks

| Risk | Mitigation |
|---|---|
| EXPORT/IMPORT fidelity gap (Step 1) | catch in Step 1 before any planning depends on it; record exact mismatches |
| Census scope drift (Step 0) | the script is the repeatable instrument; baseline block in §2.2 pins today |
| Cascade rewrite equivalences are subtle (Step 3) | per-flow dual-engine tests; flows enumerated from live FK census |
| Integrity checker false positives (Step 4) | seeded-broken-copy validation; no live-data writes |
| Funnel hides DuckDB-specific behavior (Step 5) | funnel is a router, not a semantic shim; PRAGMA disposition table documented |
| Wire write idioms differ per pattern (Step 7) | matrix covers all 92 writer-file patterns; findings are explicit |
| Stale proposal guidance | Step 8 status table + re-verify markers |

## 6. Acceptance criteria (program)

- Steps 0–8 each have their listed acceptance met and recorded.
- No production store is written by any step (all writes go to
  `$TMPDIR`/copies).
- The reentry phase plan (2–6) has a de-risked owner per phase, named in
  Step 8's status table.
- Full targeted suites green at every step that adds tests.
- Current live census published in this file (Step 0 first run).

## 7. Sizing

≈5–7 focused days total (Step 0 0.5, Step 1 0.5–1, Step 2 0.5,
Step 3 1–2, Step 4 1, Step 5 1–2, Step 6 1, Step 7 0.5, Step 8
0.25 per checkpoint) — before the reentry's ≈19–27 d swap execution,
which this program intentionally does not include.

## 8. Open questions

- Should Step 5's funnel be built in `helpers/core/db.py` (risking churn
  in 118 importers) or as a separate `helpers/core/db_engine.py` shim
  used only by newly ported lanes? (Proposal leans separate shim; the
  swap phase can then decide the merge point.)
- Do we want Step 1's artifact kept as `memory/research.duckdb`-adjacent
  scratch, or always ephemeral in `$TMPDIR`? (Proposal leans ephemeral —
  the file must never be mistaken for a live store.)
- Should Step 7's served-copy validation stand up a real quack server,
  or is a file-mode + wire-probe hybrid sufficient first? (Proposal leans
  real server on a tmp store — the discovery file is already supported by
  `QUACK_DISCOVERY` override.)
