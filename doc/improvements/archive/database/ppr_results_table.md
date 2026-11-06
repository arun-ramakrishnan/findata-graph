---
title: "PPR results table (seed-keyed persistence) — schema change for the scipy personalized PageRank lane"
status: executed
filed: "2026-11-06"
area: "helpers/graph"
executed: "2026-11-06"
completed_md: "484"
---

# PPR results table — seed-keyed persistence for the personalized PageRank lane (§C, 2026-11-06)

**Date:** 2026-11-06 · **Status:** EXECUTED 2026-11-06 (schema v17 live; 5,000 rows applied) · **Archived:** 2026-11-06, completed.md #484

Follows `scipy_personalized_pagerank.md` (§3.4/§3.5). That proposal
shipped the scipy PPR lane with CLI + API + dispatch, and a trial
`--all-ppr-top50` scope. The live trial (2026-11-06, operator: "run live
on db and report what you find") measured the §3.5 write shape and
exposed a schema-level problem:

- **865,100 UPSERT attempts** = 50 seeds × 17,302 entities against one
  table `graph_analytics` (`memory/research.db`) whose PK is
  `(metric, entity_name)` — **no seed column**.
- Net effect: **+17,302 rows** (208,481 → 225,783), all **last-seed-wins**
  (`CRISIL` — alphabetically last seed). The other 49 seeds' results
  were overwritten 49 times; history is unrecoverable.
- DB file 386 MB → 387 MB, ~9s of pointless churn per rerun.
- Worse: `value` was stored as a **plain number string** (`"0.000183"`),
  not the `{"value": ...}` shape `/api/graph/metrics/<metric>` reads, so
  `json_extract(value,'$.value')` returns NULL — the rows are unservable
  by the metrics endpoint I allowlisted.
- Cleanup done same day (operator: "clean up the 17K CRISIL rows first"):
  `DELETE FROM graph_analytics WHERE metric='pagerank_personalized'` —
  17,302 → 0 rows; `graph_analytics` back to 208,481 rows.

This proposal adds a dedicated seed-keyed table so multi-seed PPR is
queryable instead of overwritten.

## 1. TL;DR

New table `ppr_results(seed, entity, score, alpha, computed_at)` with
`PRIMARY KEY (seed, entity)`. Top-N per seed first (50×100 = 5,000
rows); full-projection persistence is a flag, not the default. Bump
`PRAGMA user_version` to 17 the same change (schema convention in
`helpers/core/db.py:57-82`).

## 2. Schema (the change)

```sql
CREATE TABLE IF NOT EXISTS ppr_results (
    seed        TEXT NOT NULL,
    entity      TEXT NOT NULL REFERENCES entities(name) ON DELETE CASCADE,
    score       REAL NOT NULL,
    alpha       REAL NOT NULL DEFAULT 0.85,
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (seed, entity)
);
CREATE INDEX IF NOT EXISTS idx_ppr_results_seed_rank ON ppr_results(seed, score DESC);
```

- `seed`, `entity`: PK (seed-first — every hot query is
  `WHERE seed = ? ORDER BY score DESC`; same Bundle-P3 argument that
  made `graph_analytics` metric-first).
- `score REAL`, not JSON `{"value": ...}` — this table is numeric-only;
  no JSON parsing tax on read, and `ORDER BY score DESC` is exact float
  order (vs `json_extract`'s precision limits).
- `alpha REAL` records the damping used per row (mixing alphas in one
  table stays honest; `WHERE seed=? AND alpha=?` narrows if needed).
- `computed_at` appends the writer's `utc_now()` (same shape as the
  rest of the schema).
- `REFERENCES entities(name) ON DELETE CASCADE` — matches
  `graph_analytics`' FK posture. `entities.name` is the PK, so existing
  row queries see no break.

Non-goals (explicitly **not** in this slice):
- No `graph_analytics` shape change, no `ppr_results` in
  `graph_analytics_metrics` serve path. `/api/graph/metrics/pagerank_personalized`
  stays a **400/404** (or serves only if we later add a
  `ppr_results`-backed branch — out of scope here).
- No DuckDB cache bump (canonical-only table, same as
  `graph_analytics_contract` at version 14 — `helpers/core/db.py`
  comment). `helpers/graph/query.py::_SCHEMA_VERSION` untouched.
- No FK verb string beyond the default (matches `graph_edges` style).

## 3. Writer seam

### 3.1 `helpers/graph/scipy_bridge.py`

- New module constant: `PPR_TABLE = "ppr_results"`.
- `write_ppr_results(conn, seed, payload: dict[str,float], *, alpha) -> int`:
  row-by-row UPSERT into `ppr_results` (INSERT ... ON CONFLICT(seed,entity)
  DO UPDATE, same deliberate no-executemany posture as
  `write_analytics`, `helpers/graph/algorithms.py`; returns
  `len(payload)`).
- `_run_ppr_lane` `--apply` + `--top` (default 100 in this proposal, was
  full projection): payload = **top-N only** (sorted desc,
  tie-break name asc) and **persists under `ppr_results`**, not
  `graph_analytics`. Single-seed `--apply` on today's store then writes
  100 rows, not 17,302.
- `--full` flag: opt-in full-projection persistence (17,302/seed) for
  audit dumps. Default off.

### 3.2 `helpers/graph/algorithms.py`

- `--all-ppr-top50` block (currently `write_analytics(PPR_METRIC, payload)`):
  switch to `write_ppr_results(conn, seed, payload_topN, alpha=alpha)`.
  Per-seed rows now accumulate under their own seed — no overwrite.
  Same 50 seeds, same guard, same 26.3s/35.8s timing.
- Keep the dry-run print shape (`seed=... payload=... applied/dry-run`).
- Remove the `pagerank_personalized` offer from any `graph_analytics`
  serve path that assumed `{"value": ...}` (currently only the allowlist
  entry — remove or comment it as deferred to `ppr_results`).

### 3.3 `app.py`

- `GET /api/graph/personalized-pagerank` — **unchanged** (read-only,
  recomputes; zero persistence). Smoke-tested 200/400 paths stay green.
- No new route in this slice.

### 3.4 Live-DB migration (the schema change in practice)

1. Add `PPR_RESULTS_DDL = """CREATE TABLE IF NOT EXISTS ppr_results
   (...);"""` (and the index) to `helpers/maintenance/migrate_to_graph_edges.py`
   next to `GRAPH_ANALYTICS_DDL`.
2. `helpers/core/db.py`: `EXPECTED_USER_VERSION = 17`,
   `EXPECTED_SCHEMA_VERSION = "17"` + the comment `17 adds ppr_results —
   the seed-keyed PPR results store (scipy_personalized_pagerank §C,
   canonical-only — no DuckDB cache bump)`.
3. `helpers/maintenance/rebuild_schema.py`: add the DDL to whatever
   `_rebuild_table` list keeps `graph_analytics` in conformance (the
   `IF NOT EXISTS` guard is enough for the live DB path; the rebuild
   path needs the column list only if we want round-trip checks).
4. Live DB (`memory/research.db`): `PRAGMA user_version = 17`, run the
   two CREATE statements once (same drift `database_integrity_check.py`
   warns about if `db_meta.schema_version` ≠ `"17"`).

## 4. Top-N default, not full

50 seeds × 17,302 entities = 865,100 attempts was the explosion.
50 seeds × top-100 = **5,000 rows** (every seed's #1..#100 walk
neighbourhood — what a human actually reads). Full dumps via `--full`.
Stored rows recording the seed set become the audit trail §3.5 wanted
("the persisted rows record which 50 seeds were run").

## 5. Acceptance

1. Live `memory/research.db`: `user_version = 17`, `db_meta.schema_version = '17'`,
   `ppr_results` exists with 0 rows, `graph_analytics` unchanged at 208,481.
2. `--all-ppr-top50 --apply --top 100` writes **5,000 rows**; each seed's
   rows are independent (no seed sees another's overwrite).
3. `SELECT COUNT(DISTINCT seed) FROM ppr_results = 50`;
   `SELECT seed, COUNT(*) GROUP BY seed` = 100 each.
4. `test_ppr_cli_dry_run_and_apply` re-pointed at `ppr_results` (payload
   is top-N, sum ≈ 1.0 across full projection **before** truncation —
   the test asserts on the truncated payload only by count, not sum);
   `test_ppr_dispatch_compute` unchanged (recompute-on-demand).
5. `app.py` API smoke paths unchanged (200 for seed, 400 for missing/
   unknown seed and bad alpha/top).
6. `/api/graph/metrics/pagerank_personalized` returns 400 (no longer
   allowlisted in `_SCALAR_GRAPH_METRICS`).
7. `database_integrity_check.py` passes with `db_meta.schema_version = '17'`
   (no drift error).
8. Targeted tests only (house rule).

## 6. Non-goals / risks

- No multi-seed-vector PPR, no directed PPR.
- `compute()` never reads `ppr_results` (same posture as
  `graph_analytics` cache rows in §3.4 of the parent proposal: the table
  is an audit trail, not a serving contract).
- Purge/staleness: no scheduled purge in this slice. `ppr_results`
  grows 5,000 rows per full `--all-ppr-top50` rerun (same seed, same
  `computed_at` refreshed via UPSERT, **not appended** — PK is
  `(seed, entity)`, so reruns **overwrite** the same 100 slots per seed,
  not 5,000 new rows per run).
  *(Superseded same day — see §7: UPSERT-only could not drop payload
  dropouts; writers now pass `replace=True`, a per-seed DELETE inside
  the write transaction.)*

## 7. Same-day evolution — projection noise passes + writer replace semantics (2026-11-06)

Three operator-driven passes after the live trial, each dropping edge
types that polluted the walk. All changes are PPR-lane-only: the
baseline `load_centrality_projection` behaviour (closeness/harmonic/
katz/eigenvector lanes and their `graph_analytics` contract) is
unchanged via the parameter defaults.

**Pass 0 — baseline.** Full centrality projection (all types minus
`listed_on_index`). Sample seed "HDFC Bank": the `india` country node
ranked **#2 (0.0088)** — `listed_in` (930 edges to one country hub)
routes seed mass to every home-market peer.

**Pass 1 — drop `listed_in`** (operator: "yes it is producing noise.
Drop listed_in and lets see"). `PPR_EXCLUDE_EDGE_TYPES =
("listed_on_index", "listed_in")` via a new `exclude_edge_types`
parameter. `india` left the top-10. But the top rows stayed IT majors
(Tech Mahindra/TCS/HCL/Wipro/Infosys ≈0.0055) regardless of seed
sector — `co_mentioned_in` (9,638 edges) is newsletter mention
**volume**, and high-volume mentioners hoover mass.

**Pass 1b — writer staleness fix (found during pass 1).** Pure UPSERT
cannot delete rows whose entities dropped out of the payload: after the
projection stopped producing `india`, the first batch's
`('3M India','india', 0.0134)` row survived forever. `write_ppr_results`
gained `replace=True` — a per-seed `DELETE FROM ppr_results WHERE
seed = ?` inside the same write transaction, then the upserts. Same
drift class `write_analytics` S3 solved with `replace_population`. All
PPR writers (batch, single-CLI, scipy_bridge lane) pass it.

**Pass 2 — include-only relational projection** (operator: "sure lets
try it"). `load_centrality_projection` grew `include_edge_types`
(allow-list flips the filter); PPR passes
`PPR_INCLUDE_EDGE_TYPES = (competes_with, same_group, supplier_to,
customer_of, subsidiary_of, jv_with, acquired, invested_in,
semantic_peer)`. The ranking's character changed completely:
HDFC Bank → IndusInd 0.017, PNB 0.017, Kotak 0.015, DCB 0.014,
BoB 0.014, Canara 0.014, Axis 0.014, CSB 0.014 (banks seed banks);
CRISIL → BSE/Oracle Financial/CDSL (its exchange ecosystem);
3M India → Thermax/Jain Irrigation/Cyient (industrials). Seed self-mass
rose (CRISIL 35%) — honest for a sparser relational graph. Cost: 66
non-company rows (mutual funds) entered stored top-100s via
`invested_in`.

**Pass 3 — drop `invested_in`** (operator: "drop invested_in"). Final
set is the 8 company↔company types. Non-company rows fell 66 → 6
(same_group/subsidiary_of endpoints typed person/institution in
`entities` — structurally real, accepted). Batch rerun: 50 seeds ×
top-100 = 5,000 rows in 17.5s.

Call sites carrying the include-set: `scipy_bridge._run_ppr_lane`,
`algorithms._run_pagerank_personalized` (dispatch + API), and the
`--all-ppr-top50` block. Test fixtures updated
(`_exact_store` uses `competes_with` edges for PPR; `fake_load`
signatures accept the kwarg); tests/test_scipy_bridge.py 49 passed.

## 8. Gate reference

`doc/procedures/gates.md` full gates ONCE per arc on operator go;
fixes validated by targeted tests only.
