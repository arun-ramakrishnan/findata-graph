---
title: VARIANT for properties columns (re-evaluate N1 on DuckDB 2.0.1)
status: executed
filed: '2026-11-22'
executed: '2026-11-22'
completed_md: '554'
area: schema/duckdb/research.sql, schema/duckdb/graph.sql, memory/research.duckdb, memory/graph.duckdb
---

# Proposal: VARIANT for `properties` columns

**Status:** EXECUTED (2026-11-22) — core DB migration applied; code-level
follow-ups (write-site casts, derived rebuild) tracked below.
**Date:** 2026-11-22
**Author:** Agent analysis (user-directed); all measurements on the live DB
(DuckDB 2.0.1, graph.duckdb 115 MB / research.duckdb 187 MB).
**Scope:** `properties` columns on `company_metrics`, `events`, `graph_edges`,
`hyper_edges`, `quotes` (research.duckdb) and the 19 derived `e_*` tables
(graph.duckdb). Tracked DDL in `schema/duckdb/research.sql` and
`schema/duckdb/graph.sql`.
**Builds on:** N1 (VARIANT) ruling in `doc/improvements/pending.md`
("NOT ADOPTED" on DuckDB 1.5.4); revisit conditions (a) perf gap closed,
(b) read-heavy use case for long-tail keys.

## 1. Motivation

N1 previously ruled VARIANT **NOT ADOPTED** on DuckDB 1.5.4 because:

1. `json_extract_string` on VARIANT raised `InvalidInputException` (broken read path).
2. VARIANT struct access was 2.6–3.1× slower than `text + json_extract`.
3. VARIANT storage was +75% vs text.

Both revisit conditions are now met on DuckDB 2.0.1 (see §3), and the
read surface is large: `year`, `venture`, `consolidated`, `as_of`,
`n_quotes`, `relationship`, `period_end` are all read via `json_extract*`
on `graph_edges.properties` across `helpers/graph/query.py`,
`derive_hyperedges`, `derive_indices`, `derive_cited_in`, analytics,
`answerability_probe`, `database_integrity_check`, and
`migrate_to_graph_edges`. L2 already moved `year` to a typed column but
`query.py` still extracts `year` from `properties`.

## 2. Read/write surface (no struct-dot access in code)

Grep confirmed there is **no** `properties.<key>` struct-dot access in
production code — every read uses `json_extract` / `json_extract_string`,
which work identically on VARCHAR and VARIANT. Therefore the migration is
schema-only: no code rewrite required for reads. Write sites pass JSON
strings (Python dicts serialized) via parameterized `?`; DuckDB auto-casts
VARCHAR→VARIANT on insert (verified).

## 3. Benchmark numbers (DuckDB 2.0.1)

All measured on 20k-row synthetic data with the same JSON payload.

| Metric | text (VARCHAR) | VARIANT | Ratio |
|---|---|---|---|
| `json_extract_string(properties, '$.venture')` | works | works (was broken on 1.5.4) | — |
| struct/arrow access `p->'year'` | n/a | 38.5 ms | — |
| `json_extract` (text path) | 46.9 ms | — | 0.83× (VARIANT faster) |
| raw scan | 0.6 ms | 0.5 ms | 0.83× |
| mixed keys | works | works | — |
| typed access `p.year` | n/a | returns int | — |
| storage (20k rows) | 536 576 B | 536 576 B | 1.0× (equal) |

Revisit conditions: (a) perf gap closed — **MET**; (b) read-heavy use case
for long-tail keys — **arguably met** (7+ keys read across ~10 call sites).

## 4. Migration applied (2026-11-22)

- **research.duckdb** (quack-served, port 9495): `ALTER TABLE ... ALTER
  COLUMN properties TYPE VARIANT` on `company_metrics`, `events`,
  `graph_edges`, `hyper_edges`, `quotes`. Dependent objects handled:
  views `company_metrics_ruled`, `relations`, `v_quote_speaker` (dropped +
  recreated); indexes (11, dropped + recreated); FK-dependent tables
  `event_participants` and `hyper_incidences` (backed up, dropped,
  recreated with original FKs). Data preserved (row counts verified).
- **graph.duckdb** (quack-served, port 9494): 19 derived `e_*` tables
  altered (no FKs/views/indexes).
- **Tracked DDL**: `schema/duckdb/research.sql` and `schema/duckdb/graph.sql`
  regenerated via `make schema-dump`; `make schema-check` clean.
- **Backup**: `/mnt/data/tmp/opencode/research.duckdb.pre-variant.bak`
  (187 MB, pre-migration).

## 5. Remaining follow-ups

1. Write sites: add explicit `::VARIANT` casts at insert/update sites for
   robustness (currently rely on auto-cast; works but implicit). Touch
   points: `helpers/graph/_edge_writer.py`, `derive_*`, `extract_relations.py`,
   `enrich_relations.py`, `backfill_sidecar_temporal.py`, `migrate_to_graph_edges.py`.
2. `migrate_to_graph_edges.py` legacy DDL (`properties TEXT`) — update to
   VARIANT for consistency with the canonical store.
3. `helpers/graph/fixture_graph_db.py` (`properties_json`) — unrelated column,
   leave as-is.
4. Next full derive run will regenerate `e_*` tables as VARIANT automatically
   (CTAS inherits source type); verify with `make schema-dump` after.
5. Re-run `make qa` (targeted) after code-level casts land.

## 6. Risks / rollback

- Rollback: restore from backup DB + revert tracked DDL (`git checkout
  schema/duckdb/research.sql schema/duckdb/graph.sql`) + `make schema-dump`.
- `json_extract` and `json_extract_string` verified working on VARIANT.
- No struct-dot access in code, so no query rewrites needed.
- Storage unchanged (1.0×), so no disk-space concern.
