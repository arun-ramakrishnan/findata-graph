---
title: "Parquet export wrapper — file P3.3 properly or retire it"
status: executed
filed: "2026-10-18"
area: "helpers/graph"
executed: "2026-10-19"
completed_md: "421"
---

# Parquet export wrapper — file P3.3 properly or retire it

**Date:** 2026-10-18 · **Status:** PROPOSED

## 1. TL;DR

`graph_pending.md:66-69` has deferred the parquet export wrapper since
2026-08-08 on "no notebook demand yet" — but the item was never carried
into the deferred-features register, so it survives only as a dangling
P-number. Verified 2026-10-18: `export_query` exists nowhere in the
tree, while the docs reference `COPY (GRAPH_TABLE …) TO … PARQUET` and
the snapshot pipeline already writes `snapshots/parquet/duckdb/*.parquet`
(the h_* tables landed there 2026-09-16). This arc is deliberately
small: either land the 0.5-day wrapper or close the P3.3 number with a
recorded decision. What it must not keep doing is linger unregistered.

## 2. Evidence (measured 2026-10-18)

- Deferral: `doc/improvements/archive/graph/graph_pending.md:66-69`
  (`helpers/graph/query.py:export_query(sql, path)`, 0.5d, "no
  notebook demand yet").
- Implementation absence: `grep -rn "def export_query" helpers/` → no
  hits; no COPY-to-parquet helper in `query.py`.
- Export target pattern exists:
  `snapshots/parquet/duckdb/h_{edge,incidence}.parquet` landed with
  hgx_first_scaling S1 (2026-09-16).
- Demand check: no notebook/reference in `doc/` calls for ad-hoc
  graph parquet export — the honest verdict may be CLOSE, not BUILD.

## 3. Slices

- **S1 (BUILD path)** — `export_query(sql, path, con=None)`: read-only
  cache connection, DuckDB `COPY (<sql>) TO '<path>' (FORMAT PARQUET)`,
  path guard under `outputs/` or an explicit `--out`, one test.
- **S1' (CLOSE path)** — record the decision in `graph_pending.md` and
  the register; the P3.3 number is retired with the demand gap as the
  recorded reason.

## 4. Acceptance

- BUILD: round-trip test (export → read back → row-count parity vs the
  SQL), read-only connection discipline (no WAL creation on the live
  cache).
- Either path: the P3.3 entry stops being a dangling deferral.

## 5. Risks & non-goals

- No new dependency (DuckDB's writer is already in the venv).
- Not an ETL surface — notebook/utility export only; pipeline writers
  keep their own snapshot lanes.

## 6. References

- `graph_pending.md` (P3.3, Notes section: "To activate a deferred
  item: remove its entry here, implement, run make qa"),
  `hgx_first_scaling.md` §6 S1 (parquet snapshot pattern).

## 7. Execution results (2026-10-19)

**Status: EXECUTED — BUILD path (BUILD/CLOSE operator call, 2026-10-19)
— completed.md #421.**

Landed `helpers/graph/query.py:export_query(sql, path, *, con=None)`:

- `COPY (<sql>) TO '<path>' (FORMAT PARQUET)` over a connection the
  function opens itself with `connect_read_only(DUCKDB_PATH,
  attach_db=DB_PATH)` when `con` is omitted — the same read-only +
  `fin`-attached surface `connect_read_only` gives pure-reader CLIs.
- Relative paths resolve under `outputs/` (the derived-artifact scratch
  root); absolute paths are honoured; parent dirs are created; the path
  literal is single-quote escaped, so a quote in a filename cannot break
  the statement.
- Read-only discipline holds: a COPY TO an external file writes the
  filesystem, not the database — the source cache is byte-identical and
  no WAL appears (asserted, not assumed).
- Not an ETL surface: pipeline writers keep their own snapshot lanes
  (`hgx_first_scaling` S1 writes `snapshots/parquet/` directly).
- No new dependency — DuckDB's writer was already in the venv.

**Tests** — `tests/test_export_query.py` (5): round-trip row parity vs
the SQL, read-only/no-WAL discipline, relative-path resolution under a
patched `outputs/`, absolute path honoured, quote-escaped filename.

**Live smoke** against the production cache: a grouped edge-type export
returned `supplier_to` 16,721 / `subsidiary_of` 10,752 /
`co_mentioned_in` 9,638 / `same_group` 8,920 / `listed_on_index` 6,615
(the top five contain no retired `part_of`/`has_company` — the arc-4
retirement holds at the export surface too). Scratch output removed.

`graph_pending.md` P3.3 entry replaced with a SHIPPED pointer; the
dangling P-number is retired.
