---
title: "Snapshot trust + country exposure — verify union, version drill, exposure views"
status: executed
filed: "2026-09-10"
executed: "2026-09-10"
completed_md: "221"
area: "helpers/maintenance/snapshot_db.py + helpers/graph (query.py, app.py, stats.py)"
---

# Snapshot trust + country exposure — verify union, version drill, exposure views

**Date:** 2026-09-10 · **Status:** EXECUTED 2026-09-10 ·
completed.md entry 221 ·
**Area:** helpers/maintenance (`snapshot_db.py`), helpers/graph
(`query.py`, `app.py`, `stats.py`), `tests/`

## 1. Motivation

Three findings from the 2026-09-10 ledger-resolution session
(doc_drift patch: 32 stale completed.md rows verified and marked):

**Verify coverage drift.** `verify_duckdb_snapshot` hardcodes its table
list as `["v_node", "v_company", "v_sector"] + EDGE_REGISTRY` (13
tables) while `helpers.graph.query.MATERIALISED_TABLES` — the
self-declared "single-source manifest of every DuckDB table the
materialisation owns", already used by `export_parquet_duckdb` as its
allow-list — carries 28. Fifteen tables ride the snapshot unverified:
`v_super_sector`, `v_sub_sector`, `v_theme`, `v_edition`,
`v_institution`, `v_country`, `v_embeddings`, `v_note_embeddings`,
`e_belongs_to`, `e_exposed_to`, `e_cited_in`, `e_listed_in`, `e_all_und`,
`e_dir`, `_build_meta`. The verify docstring already claims "ALL
materialised tables"; the code stopped believing it when the extras grew.
The geography tables landed (schema v14) straight into this blind spot.

**O3, the last open DuckDB row.** The read-only CHECKPOINT path assumes
DuckDB >= 1.5 lets a reader flush the WAL. It has a graceful fallback
(verbatim main+WAL copy) and a documented pin-bump re-test drill
(graph_design.txt §9.3), but no test — a version upgrade would surface
regression only as a silent degrade to the fallback, or worse.

**Country layer has no consumers.** #219 landed the layer today
(schema v14: `v_country` 21 rows, `e_listed_in` 930 edges, rerunnable
`derive_countries.py`) but nothing queries it: no endpoint, no bundle,
no census. The dimension analysts reason about — international vs
domestic mix, venue concentration, country x sector exposure — is one
join away and unused.

## 2. Evidence (measured 2026-09-10, this box)

| Check | Result | Verdict |
|---|---|---|
| verify list vs manifest | snapshot_db verifies 13 of `MATERIALISED_TABLES` = 28 (10 EDGE_REGISTRY + 3 hardcoded vertex); 15 tables absent | docstring/code drift; new tables born unverified |
| `_build_meta` stamps | `duckdb_version` already stamped at build | restore-side version assert is free |
| duckdb in use | 1.5.5 | drill runs green today; the test IS the drill |
| exposure demo (parquet, one join) | india 850, usa 41, uk 7, france 4, japan 4 (930 edges / 21 countries) | country queries trivial from the layer |
| cap-mix per listing | large 320, mid 255, small 242, micro 94, NULL 19 | 19 listed companies lack a cap bucket — report-only hygiene |

## 3. Design

### Part A — verify union fix (S1)

**S1** `snapshot_db.materialised_tables` := sorted
`helpers.graph.query.MATERIALISED_TABLES` (vertex-first order preserved
for readability), docstring already correct after this. Row-count
compare covers all 28; the structural spot-check stays one
EDGE_REGISTRY entry (unchanged cost). No exporter change —
`export_parquet_duckdb` already gates on the same frozenset.

### Part B — O3 version drill (S2)

**S2** `tests/test_snapshot_version_drill.py`:
- *happy-path drill*: build a tiny tmp duckdb (one vertex + one registry
  table), `create_duckdb_snapshot`, decompress/restore, assert row-count
  parity + `duckdb_version` stamp equals the running version. This is
  §9.3's drill, executable — a pin bump that breaks reader-CHECKPOINT
  turns the suite red instead of silently degrading.
- *fallback drill*: monkeypatch the read-only open to raise; assert the
  verbatim main+WAL copy is produced and the result dict flags the
  fallback path.
- *restore guard*: `verify` (or snapshot-check) warns when the restored
  file's `duckdb_version` stamp differs from the running library —
  the O3 risk becomes observable on every verify, not just at drill time.

### Part C — country exposure views (S3–S5)

**S3** `query.py`: `country_exposure_bundle(con)` — one SQL: per country,
company count, sector mix (via `e_belongs`), cap-bucket histogram
(`v_company.market_cap`), ordered by count. Bundle shape per K-series
conventions (in-SQL aggregation, Python gets one result set).

**S4** `app.py`: `GET /api/graph/country/<name>` (companies of one
country; 404 parity with entity routes) and `GET /api/graph/exposure`
(country x sector matrix; `?country=` / `?sector=` filters).

**S5** `stats.py`: country census section in graph-health (per-country
counts + the unbucketed report) and the report-only hygiene line: 19
listed + 46 total companies without a cap bucket (no ticker / no tag) —
counts only, no `findata` mutation.

## 4. Acceptance criteria & shakedown

- `verify_duckdb_snapshot` result carries 28/28 row-count keys; snapshot
  verify on the live snapshots is green.
- Version-drill tests green; the monkeypatched fallback test proves the
  §9.3 degrade path produces a restorable artifact.
- `/api/graph/country/india` returns ~850 companies;
  `/api/graph/exposure` returns the 21-country matrix; graph-health
  prints the country census.
- `make qa` fully green; `make perf` within budgets (verify cost +15
  COUNT(*)s — sub-millisecond on snapshot files).

## 5. Risks

- `MATERIALISED_TABLES` grows again -> verify list follows automatically
  (single source), so this drift class is closed, not just re-counted.
- `_build_meta`/`v_note_embeddings` row counts are cheap; no new heavy
  I/O. Structural spot-check cost unchanged.
- Two new routes add surface — both read-only bundles over existing
  tables, 404 parity pattern exists.

## 6. Non-goals

- No new derive producers (`derive_countries.py` already owns listed_in).
- No tag cleanup, no `findata` data mutation, no HNSW/macro revisits
  (deferred N5 items keep their quarterly trigger).
- No changes to the CHECKPOINT code path itself — S2 observes it.
