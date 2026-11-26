---
title: "Onager synthetic-edge materialization: Arrow stage replaces giant VALUES insert"
status: "executed"
filed: "2026-11-26"
executed: "2026-11-26"
completed_md: "574"
area: "helpers/graph/onager.py (synthetic `edges=` materialization) + tests/test_onager_capabilities.py"
---

# Onager synthetic-edge materialization: Arrow stage replaces giant VALUES insert

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

**Date:** 2026-11-26 · **Status:** EXECUTED (2026-11-26, completed.md #574) ·
**Area:** `helpers/graph/onager.py` + `tests/test_onager_capabilities.py`

## 1. Motivation

`helpers/graph/onager._materialize_edges()` materializes a caller-supplied
Python edge list into the temp `_onager_e(src BIGINT, dst BIGINT, weight
DOUBLE)` table used by onager/fallback graph algorithms. The 2026-08-15
implementation (blame `2587a3a179`) replaced DuckDB `executemany` — measured
at ~4.6–5.2 s for the onager edge set in the 2026-08-14 prime-rlm diagnosis
(`memory/data/harness/prime-rlm/conversations/019ff54d-5816-70fc-b9e1-672d423fbb80.parquet:387ddc45:0,:7588a272:0`)
— with one giant `INSERT ... VALUES (?,?,?),...` SQL string. That removed the
executemany pathology but left a Python/SQL-string construction cost that
scales poorly with edge count.

This is the **synthetic `edges=` path**. It is distinct from the DB-backed
wire/quack path (`_materialize_from_db`, `helpers/graph/onager.py:291-366`),
which already uses Arrow registration because quack refuses remote scans
inside a CTAS (`:308-310`): “no remote scans inside a CTAS, which the quack
executor refuses”. The synthetic path has no remote scan; its source is an
in-memory Python edge list, so Arrow stage is a local-relation operation.

## 2. Evidence (measured 2026-11-26, this box)

Scratch microbench, in-memory DuckDB, same process, alternating old/new over
6 samples per size after warmup; rows verified by `SELECT count(*)`:

| n edges | old VALUES (ms) | new Arrow stage (ms) | Verdict |
|---:|---:|---:|---|
| 100 | 5.63–7.77 | 2.53–5.73 (first 250.66, cold) | small-n parity / small win |
| 1,000 | 35.45–42.21 | 2.92–5.73 | ~8× |
| 20,000 | 881.93–1013.08 | 7.74–8.92 | ~110× |
| 100,000 | 5280.76–5629.47 | 28.08–45.14 | ~150× |

Measurement: `/tmp/onager_mat_bench2.py` (scratch, not committed), `.venv/bin/python3`,
DuckDB + PyArrow in-repo environment. The old path builds a flat Python list
and a giant SQL string; the new path builds three Arrow arrays and registers
them as a local relation.

**Impact scope:** `_materialize_edges` has only one production caller,
`_ensure_materialized(..., edges=...)` at `helpers/graph/onager.py:460`.
`rg` shows no test callers outside the new parity test. Public graph APIs
accept `edges=` widely for tests/benches/synthetic callers, so this is a
low-risk synthetic-path speedup, not a measured `graph_rebuild` budget win.
`gate_query timing --leg graph_rebuild --last 5` remained 3.65–4.35 s / 5.0 s
budget before this change; the DB-backed rebuild does not use this path.

## 3. Design

### S1 — Arrow-stage synthetic edge materialization (this change)

Replace the VALUES SQL string with:

1. Build `pa.table({"src": int64, "dst": int64, "weight": float64})` from the
   Python edge list.
2. Drain pending results defensively (`SELECT 1` fetchall in suppress) — DuckDB
   2.0 refuses `register()`/`unregister()` while a result is pending, the same
   posture as `_materialize_from_db:360-364`.
3. `raw.register(stage, tbl)` where `raw = getattr(con, "raw", con)` so the
   DuckDB connection proxy is handled the same way as
   `helpers/graph/incidence_batch.py:51-67`.
4. `CREATE OR REPLACE TEMP TABLE _onager_e AS SELECT CAST(src AS BIGINT) ... FROM stage`.
5. Drain + unregister in `finally`, suppressing cleanup exceptions.

The empty-edges case keeps the old contract: create/replace `_onager_e` with
the same schema and zero rows.

**Alternatives considered:**

| Alternative | Why not |
|---|---|
| Restore `executemany` | Measured pathological in 2026-08-14 (4.6–5.2 s); unchanged. |
| Keep VALUES string | Correct but scales badly; 20k edges ~0.9–1.0 s per materialization in scratch probe. |
| Rust/PyO3 edge materializer | No measured production hot path justifying a new Rust boundary; Arrow stage already gives ~100× at 20k. |
| Change DB-backed wire path | Different constraint (quack remote-CTAS refusal); already Arrow-staged and correct. |

## 4. Acceptance criteria & shakedown

1. **Parity:** `tests/test_onager_capabilities.py::test_materialize_edges_parity_with_legacy_values_insert`
   proves the Arrow-staged path matches the legacy VALUES insert for schema,
   row values, ordering, and empty input.
2. **Existing contracts green:** `.venv/bin/python3 -m pytest tests/test_onager_capabilities.py tests/test_graph_fallbacks.py -q`
   — 113 passed (2026-11-26, this box).
3. **Lint:** `.venv/bin/ruff check helpers/graph/onager.py` clean.
4. **No quack-path regression:** `_materialize_from_db` wire path untouched;
   it keeps its own Arrow register + drain/unregister + remote-CTAS comment.
5. **Drain posture:** register/unregister wrapped with pending-result drain,
   matching DuckDB 2.0 behaviour already handled in `_materialize_from_db`.

## 5. Risks

- **Pending-result registration error:** DuckDB 2.0 refuses register/unregister
  with a pending result. Mitigated by defensive `SELECT 1` drain before
  register and in finally before unregister; exercised by targeted tests.
- **Connection proxy shape:** some call sites pass proxied DuckDB connections.
  Mitigated by `raw = getattr(con, "raw", con)` (same pattern as
  `incidence_batch.py`).
- **Large SQL string removed, but Arrow arrays allocate:** allocation is O(n)
  in Python/Arrow and measured far cheaper than giant SQL string construction
  at the sizes that mattered.

## 6. Non-goals

- No change to `_materialize_from_db` / quack wire semantics.
- No claim of `graph_rebuild` budget improvement.
- No Rust kernel.
- No change to `_onager_e` schema or downstream algorithm semantics.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-26 | `.venv/bin/python3 /tmp/onager_mat_bench2.py` | 20k old 882–1013ms, new 7.7–8.9ms; 100k old 5281–5629ms, new 28–45ms | in-memory DuckDB, alternating 6 samples/size, drain before register |
| 2026-11-26 | `.venv/bin/python3 -m pytest tests/test_onager_capabilities.py tests/test_graph_fallbacks.py -q` | 113 passed, 4.56s | after parity test added |
| 2026-11-26 | `.venv/bin/ruff check helpers/graph/onager.py` | All checks passed | |
