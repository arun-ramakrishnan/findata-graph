---
title: "Snapshot restore SQL injection — manifest-gate the parquet identifiers"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "333"
area: "helpers/maintenance/snapshot_db.py (restore_duckdb_from_parquet, restore_sqlite_from_parquet), helpers/graph/derive_insights.py"
---

# Snapshot restore SQL injection — manifest-gate the parquet identifiers

**Date:** 2026-10-01 · **Status:** EXECUTED (completed.md #333) · **Severity:** HIGH
(exploit-today ordering; operator-triggered restore of repo-tracked
content, repo is public). Confirmed with source trace + bounded local
observed result (Addendum 7 §H): a snapshot-dir parquet filename
carrying SQL executed `ATTACH`/`CREATE TABLE`/`INSERT` in the restore
connection.

## Finding

`restore_duckdb_from_parquet` (`helpers/maintenance/snapshot_db.py:1120-1124`):

```python
tname = pf.stem
con.execute(f"INSERT INTO {tname} SELECT * FROM read_parquet('{pf}')")
```

Both the identifier and the quoted path come from the snapshot
directory's filenames. The export side filters tables against the
materialisation manifest (`:702-715`); restore trusts every `*.parquet`
glob hit. The noqa ("identifiers come from the snapshot's own file
names") records provenance, not a control — the SEC-6 lesson applies.

The SQLite twin (`:1062-1070`) has the same shape with `[...]` brackets
and no `]]` escaping (column names too, `:1067`); Python
`executemany`'s single-statement rule blocks the one-shot multi-statement
shape, but the variant needs its own probe — treat as needs_validation
and fix in the same slice.

## Slices

- **S1 — identifier allowlist on restore.** Restore validates every
  `pf.stem` (and SQLite column name) against the schema the restore
  itself just applied: enumerate the created tables (duckdb
  `information_schema.tables` / sqlite `sqlite_master`) and refuse any
  stem outside that set with a loud `UNEXPECTED SNAPSHOT FILE` error.
  Additionally require the identifier shape `^[a-z_][a-z0-9_]*$` and
  double any `'` in the interpolated `read_parquet('...')` path (or pass
  the path as a parameter — duckdb supports `read_parquet(?)`). The
  manifest filter already exists on export; reuse it on restore so both
  directions share the gate.
- **S2 — SQLite twin.** Same allowlist for `[tname]` and column
  identifiers; escape `]` by doubling, or better, build the INSERT from
  the allowlisted identifiers only and reject anything else.
- **S3 — regression test.** The confirmed PoC from Addendum 7 §H as a
  pytest: scratch snapshot dir with the malicious stem, restore raises
  the allowlist error, and no side-effect DB materialises. Mutation
  rule: strip the allowlist check, the test must go red.
- **S4 — derive_insights containment (hardening note from §H).**
  `_paths_by_entity` (`derive_insights.py:2007-2011`, writes at
  `:2132`/`:2406`): resolve `PROJECT_ROOT / file_path` and require
  `is_relative_to(PROJECT_ROOT / "findata")` before read/write; skip +
  warn on violations. All current writers are safe — this closes the
  one place a DB string becomes a write path.

## Verification

Existing snapshot round-trip tests stay green
(`make snapshot-check`); the S3 PoC test red-on-mutation; ruff/types on
the touched module. Restore of the real tracked snapshot is
byte-identical in table/row counts (the allowlist never rejects a
legitimate name).
