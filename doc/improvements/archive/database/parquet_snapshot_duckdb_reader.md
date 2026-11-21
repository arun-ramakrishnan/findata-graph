---
title: "Close the implicit-sqlite surface: selector-aware house connect + the parquet snapshot axis on the DuckDB research store"
status: executed
filed: "2026-11-21"
executed: "2026-11-21"
completed_md: "553"
area: "helpers/core/db.py + helpers/maintenance/snapshot_db.py + snapshots/parquet"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# Close the implicit-sqlite surface: selector-aware house connect + the parquet snapshot axis on the DuckDB research store

**Date:** 2026-11-21 · **Status:** PROPOSED (re-scoped same day on operator
order: the house-connect sweep is the MAIN slice) ·
**Area:** `helpers/core/db.py` + `helpers/maintenance/snapshot_db.py` +
`snapshots/parquet/sqlite/` + tests

## 1. Motivation

Two post-cutover surfaces still resolve to the frozen SQLite store, both
measured — this proposal sweeps them in dependency order:

**(a) The house connect never consults the selector (MAIN).** The S6
selector default flip (2026-11-21, duckdb-first) made every
selector-mediated lane resolve to the canonical in env-less shells — but
`helpers.core.db.connect()` (:143) has no `db_engine` reference at all:
it opens whatever path it is given with raw sqlite3 semantics. 117
modules import from `helpers.core.db`; the live research-store-path
writer is the note pipeline — `rebuild_note_search` does
`connect(DEFAULT_DB)` (:948) and stamps apply facts, which is exactly
the lane behind the measured `47fdfcb2` apply-stamp divergence (§2).
Operator directive at filing: "nothing should resolve to the sqlite
default now" — the house connect is the last selector-unaware front
door.

**(b) The git-tracked parquet snapshot axis is coupled to the frozen
store.** `snapshot_db.py`'s research side has no DuckDB reader:
`export_parquet_sqlite` opens the source with a raw sqlite3 open
(:1095), `_verify_parquet_sqlite_side` counts through sqlite only
(:1320), and `DEFAULT_DB` (:114) + the CLI `--db` default (:2239) point
at `memory/research.db`. Every future vault write is invisible to the
tracked `snapshots/parquet/sqlite/` corpus (30 parquet files).

Execution gates: **S1 is NOT freeze-gated** — operator go runs it
independently (it closes the split-brain exposure). S2–S5 stay gated on
the operator's freeze-or-build call after a few weeks of post-cutover
stability (filed at the S6 close, 2026-11-21); the retire/freeze
alternative stays live until then.

## 2. Evidence (measured 2026-11-21, this box)

| Configuration | Result | Verdict |
|---|---|---|
| house `connect()` | zero `db_engine` references (db.py:143) | the last selector-unaware front door |
| import surface | 117 modules import from `helpers.core.db` (most for non-connect helpers) | census-bounded blast radius |
| live research-store writer | `rebuild_note_search.py:948` `connect(DEFAULT_DB)` — the only research-store-path caller found | the `47fdfcb2` apply-stamp writer |
| writer-helper callers | 9 modules use `bump_generation`/`ensure_db_meta` | dual-engine port set |
| note lane duckdb readiness | `rebuild_note_duckdb` branch exists (:1108 suffix dispatch) | the lane already knows both engines for its sidecar |
| frozen sqlite db_meta (axis source) | `apply_head 47fdfcb2…`, `apply_ts 2026-11-21 11:38:58`, generation 1145755, schema_version 17 | post-cutover apply stamp the canonical lacks |
| canonical duckdb db_meta | `apply_head 0ae53d43…`, `apply_ts 2026-11-21 04:24:35`, generation 1145755, schema_version 17 | same generation, different data state |
| tracked `entities.parquet` | 27,751 rows | row parity holds TODAY; divergence is forward-looking + metadata-level |
| sqlite-axis generation stamp | `_read_parquet_generation` → None | the axis carries no meta generation table (graph-axis feature) — NOT the evidence |
| canonical recovery lane | `research_backup.duckdb.zst` 29.2 MB, entities/relations exact (S6 round-trip) | proven; the parquet axis is reviewability/history, not recovery |

The apply-stamp divergence is the split-brain made concrete within
hours of cutover: the note lane's research touch resolved to the sqlite
default in an env-less shell and its apply metadata landed ONLY in the
frozen store. The selector default flip closed the selector-mediated
lanes the same day; what remains is exactly this proposal's scope — the
house connect (S1) and the snapshot lane (S2–S4). Ruled out as
evidence: a generation-stamp mismatch (no such meta table on the
parquet axis) and row-count drift (none yet — the axis freezes, it does
not corrupt). Ruled out as a fix for (b): a bare `DEFAULT_DB` re-point
— it breaks the verify/restore engine contract (SQLite-schema
restore), the exact trap S6 documented.

## 3. Design

Two workstreams, one proposal. **S1 first** (independent, un-freeze-gated),
then the snapshot axis S2–S5. The DuckDB export/verify machinery already
exists for the graph-cache side (`export_parquet_duckdb` :932,
`_export_duckdb_tables` :855, `_verify_duckdb_counts` :1207) — the
snapshot slices route the research store through that machinery rather
than write a second one.

- **S1 (MAIN) — selector-aware house connect + dual-engine writer
  helpers**: `connect()` dispatches research-store and `.duckdb` paths
  through `research_store_connect` (extension law + gated sibling
  redirect + selector; non-store `.db` paths stay unchanged sqlite via
  the wrapper's pin). The sqlite-API writer helpers go dual-engine:
  `ensure_db_meta`, `bump_generation`, `_log_bump` (+ the
  `DB_BUMP_LOG_DDL` duckdb shape), `get_generation`, `get_user_version`
  (PRAGMA → the db_meta `schema_version` read on duckdb). `_bump_caller`
  is already engine-agnostic (traceback only). The `_DuckDBConn` proxy
  covers execute/fetch/commit + name-indexed rows; sqlite3-specific APIs
  (`total_changes`, `create_function`, `executescript`) break loudly —
  the census keeps the caller set known: 9 writer-helper modules, one
  live research-store writer (`rebuild_note_search`, whose duckdb
  sidecar branch already exists). End state: a note-apply-shaped
  research touch in an env-less shell lands in the canonical, and the
  frozen store's db_meta stops moving.
- **S2 — engine-aware export**: snapshot source resolution through
  `db_engine.is_research_store_path` + `connect_funnel` (wire-if-served
  like the S4 binary-snapshot branches; export stays SERIAL behind the
  `is_served` guard per the graph-mirror wire law);
  `export_parquet_sqlite` dispatches to the DuckDB table/schema
  exporters for a DuckDB source (roster from the live store,
  FTS-shadow exclusions mirrored from the sqlite side).
- **S3 — engine-aware verify**: `_verify_parquet_sqlite_side` gains the
  duckdb live-count reader (`_verify_duckdb_counts` class) +
  `_read_duckdb_generation`; `verify_parquet_snapshot` dispatches on
  the source engine.
- **S4 — defaults and wiring**: snapshot `DEFAULT_DB` / CLI `--db`
  resolve through the research-store gate (NOT a bare re-point);
  `_create_parquet_snapshot`'s per-side export ladder (:2137) +
  `make snapshot` / `snapshot-check` + db_maint follow the resolution;
  the restore contract is PINNED — `--restore` stays SQLite-schema
  targeting the rollback anchor, stated in `--help`.
- **S5 — tests + tracked refresh**: the five snapshot test files gain
  duckdb-source cases (`test_snapshot`, `test_snapshot_db`,
  `test_integration_snapshot_cycle`, `test_hyper_roles`); one-time
  tracked `snapshots/parquet/sqlite/` refresh from the DuckDB source as
  a db_sync-style patch (textconv row deltas, snapshot-mechanics block);
  db_schema/procedure docs re-dated.

Alternatives and why they lost:

- **Bare `DEFAULT_DB` re-point** — breaks verify/restore (engine
  contract); rejected during S6.
- **Sweep the note lane as a separate ticket** — absorbed into S1 per
  operator order 2026-11-21 ("add the db.connect as main slice"): the
  split-brain exposure and its fix belong in one record.
- **Third axis `snapshots/parquet/duckdb/`** — keeps the sqlite axis
  frozen-as-history but adds a second tracked corpus, a new
  analytics-lane view namespace (`generic_analytics_lane` mounts the
  axes as `<side>__<stem>` views), and a DuckDB-schema restore
  decision. DEFERRED with an explicit trigger: adopt only if the
  operator wants engine-pure restore or axis separation after the soak.
- **Retire/freeze the axis** — zero code; the operator's fallback if
  the soak shows the git-tracked history is not load-bearing for the
  DuckDB store. Stays live until the freeze-or-build call.

## 4. Acceptance criteria & shakedown

1. Env-less probe (S1): a note-apply-shaped research touch through the
   swept lane lands in the canonical — `bump_generation` on a duckdb
   conn increments the canonical's db_meta generation, and the frozen
   store's db_meta (apply_ts/apply_head) stops moving.
2. Writer-helper parity (S1): `bump_generation` / `get_generation` /
   `ensure_db_meta` / `get_user_version` behave identically on tmp
   sqlite/duckdb pairs (same new generation, same ring semantics).
3. `verify_parquet_snapshot` green against a DuckDB source on a tmp
   probe store AND on the live canonical (funneled) — counts exact.
4. `make snapshot` then `make snapshot-check` green; the qa snapshot
   legs stay green.
5. Export → `restore_sqlite_from_parquet` into a tmp target → row
   parity exact on the rostered tables; 3 consecutive runs (the tracked
   refresh must be reproducible).
6. No eval-gate bullet owed — axis paths and analytics view names are
   unchanged (not query-visible semantics); instead record a
   `generic_analytics_lane --list` smoke showing the same `sqlite__*`
   views populated from the new source.

| Projected outcome | Today | After |
|---|---|---|
| implicit-sqlite research-store lanes | 2 (note lane + snapshot lane) | 0 |
| house connect on a research-store path, env-less | frozen store | canonical |
| axis source | sqlite (frozen at cutover) | duckdb (live canonical) |
| export/verify engines | sqlite only | sqlite + duckdb dispatch |
| tracked corpus refresh | frozen | once at S5, then tracks writes |

## 5. Risks

- **`connect()` return-type surface (S1)** — research-store-path
  callers get the funnel proxy / raw duckdb instead of a
  `sqlite3.Connection`; sqlite3-specific APIs fail loudly rather than
  silently. Census-bounded: 9 writer-helper modules, one live
  research-store writer; the helpers are ported FIRST so callers meet
  dual-engine conns with dual-engine helpers. Targeted suites per
  swept module.
- **Generation-ring semantics on duckdb** — `db_bump_log`'s rowid trim
  and PRAGMA reads need duckdb equivalents (duckdb supports rowid on
  base tables); the ring is diagnostic (best-effort, never raises) so a
  port defect degrades to a missing log entry, not a failed bump.
- **Wire-served export cost (S2)** — the canonical is 174.6 MB; the
  SERIAL guard behind `is_served` keeps the export inside the existing
  snapshot window (the S4 binary-snapshot branches already measure this
  class).
- **Tracked-corpus churn (S5)** — the refresh rewrites up to 30 tracked
  parquet files; one db_sync-style patch with textconv deltas,
  reviewed once.
- **Restore-contract confusion** — pinned to SQLite-schema/rollback
  anchor with `--help` text; no silent re-point.

## 6. Non-goals

- Third `snapshots/parquet/duckdb/` axis and DuckDB-schema restore
  (deferred alternative, trigger above).
- `note_search` sidecar axis (separate store by the S1 carve-out; the
  note lane's SIDEcar plumbing already has its duckdb branch).
- `memory/research.db` deletion and the post-cutover soak cadence
  (operator call).
- Migrating the other 100+ `helpers.core.db` importers that never touch
  research-store paths (sidecar stores, tmp fixtures, pure helpers) —
  their behavior is unchanged by S1's gated dispatch.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-21 | `.venv/bin/python3` probe: `_read_parquet_generation(snapshots/parquet/sqlite/entities.parquet)` | None | no meta generation table on this axis |
| 2026-11-21 | funnel probe: `SELECT key, value FROM db_meta` on `memory/research.duckdb` | apply_head 0ae53d43…, apply_ts 2026-11-21 04:24:35, generation 1145755, schema_version 17 | canonical state at filing |
| 2026-11-21 | raw ro sqlite3 open of `memory/research.db` | apply_head 47fdfcb2…, apply_ts 2026-11-21 11:38:58, generation 1145755, schema_version 17 | frozen store already diverged in apply metadata |
| 2026-11-21 | duckdb count on tracked `snapshots/parquet/sqlite/entities.parquet` | 27,751 rows | row parity with the cutover-day v_node count |
| 2026-11-21 | `rg` seams in `helpers/maintenance/snapshot_db.py` | `export_parquet_sqlite` :1095, `_verify_parquet_sqlite_side` :1320, `DEFAULT_DB` :114, `export_parquet_duckdb` :932, `_verify_duckdb_counts` :1207, `_create_parquet_snapshot` :2137 | slice anchors |
| 2026-11-21 | `ls snapshots/parquet/sqlite/ \| wc -l` | 30 files | tracked-axis corpus size |
| 2026-11-21 | `rg -l "from helpers.core.db import" helpers/ \| wc -l` | 117 modules | import surface (most import non-connect helpers) |
| 2026-11-21 | `rg` research-store-path connect callers | `rebuild_note_search.py:948` `connect(DEFAULT_DB)` — the only hit | the live writer on the default path |
| 2026-11-21 | `rg -l "bump_generation\|ensure_db_meta" helpers/` | 9 modules | static_checks, graph/embeddings, db.py, rebuild_schema, rebuild_note_search, seed_analytics_contract, migrate_to_graph_edges, rebuild_doc_search, database_integrity_check |
| 2026-11-21 | `rg` note-lane seams | :948 research read, :1114 index conn, :1409/:1492 `bump_generation` | duckdb sidecar branch exists at :1108 (`rebuild_note_duckdb`) |
| 2026-11-21 | selector flip probes (post-flip, env-less) | pathless → canonical gen 1145755; `ENGINE=sqlite` → frozen .db; tmp sibling refused; bare research.db → canonical | 4/4 green — the flip this proposal's S1 completes |
