---
title: 'Mojo port of `make graph-algos` — phase 1: bridge-driven probe'
status: executed
filed: '2026-08-30'
executed: '2026-08-30'
completed_md: '183'
area: Mojo tooling / `Mojo/src/bench/graph_algos_probe.mojo` +
---

# Mojo port of `make graph-algos` — phase 1: bridge-driven probe

**Date:** 2026-08-30 · **Status:** EXECUTED same day (completed.md #183;
23/23 SQL checksum + 16/16 metric canonical parity exact, bench leg
`graph-algos` green in ~24–30 s wall; negative test verified exit 1) ·
**Area:** Mojo tooling / `Mojo/src/bench/graph_algos_probe.mojo` +
`Mojo/bench/mojo_graph_algos.py` (bench leg `graph-algos`)

Operator decisions at approval (2026-08-30):
- **Gate on parity**: unlike the db-access / db-integrity legs (which
  only print FAIL), a parity failure in this leg flips the probe's exit
  code — the leg goes red in `make mojo-bench`. New legs gate; retrofit
  of the older legs is a separate decision.
- **Functions + verbatim SQL**: the Mojo side both drives the ORIGINAL
  python module functions and executes verbatim SQL strings itself on
  the shared connections (two granularities of the same access test).
- **Comprehensive FTS**: although the graph-algos path itself contains
  zero FTS5 queries, the probe also mirrors every distinct FTS query
  shape the python side runs (note_search / doc_search / script_search
  / vec0 KNN) — one leg then covers the full "DB extensions + FTS from
  Mojo" surface.

## Context / problem

- `make graph-algos` runs `helpers/graph/algorithms.py --all --no-apply`:
  14 metrics (12 node-keyed centralities + link-predict + voterank),
  computed by the **Onager DuckDB community extension** (Rust table
  functions over `(src, dst, weight)` BIGINT temp tables) on one
  `helpers.graph.query.connect(read_only=True)` connection, which loads
  the `sqlite` ATTACH + `vss` extensions and materialises edges from
  `fin.graph_edges`. Read-only; exits 0; per-metric failures are caught
  and printed.
- The algorithms are therefore in neither Python nor SQL — the Python
  side is orchestration (edge projection SQL, table-function calls,
  canonicalisation, printing). That orchestration is exactly the layer
  the integrity port proved ports cleanly to Mojo.
- This is the next rung of the Python-interop checkout ladder
  (regex bridge → sqlite/duckdb drivers → full DB-access program):
  the new seams this leg exercises are **DuckDB extension loading from
  Mojo-driven code** (`onager`, `vss`, `sqlite` ATTACH), **table-function
  SQL with named parameters** (`seed => 42`), and **the repo's full FTS5
  surface** — none of which the existing legs reach (db-access has a
  single FTS case; nothing touches Onager).

## Solution / design

Phase 1 (this proposal): a bench-side probe, original python modules as
the engine, per the established routing rule (data + library calls via
the bridge; driving, SQL execution, consumption, canonicalisation and
verification in Mojo).

- `Mojo/bench/mojo_graph_algos.py` — fixture / parity oracle. All
  connections read-only and module-lazy: the canonical
  `query.connect(read_only=True)` graph connection; read-only URI
  sqlite handles on research.db and the doc/script sidecars; the
  vec_search KNN path with production gating and lazy backfill
  DISABLED (skip, never write). Exposes:
  - **SQL cases** — (name, sql, params) pairs the Mojo side executes
    itself. Onager-path statements are composed from the original
    module's own helpers (`_where_inline`, the materialisation SQL) so
    the strings cannot drift from `onager.py`. Cases: `_onager_int` /
    `_onager_e` materialisation, `onager_ctr_pagerank`,
    `onager_par_components`, `onager_cmm_louvain(seed => 42)`,
    `onager_lnk_jaccard`, `onager_ctr_voterank`, `duckdb_extensions()`
    inventory, edge/node per-type counts, `_build_meta`.
  - **FTS cases** — the distinct production shapes, verbatim:
    note_search rank+snippet, doc_type-filtered, hybrid select
    (`embedding, rank`, `LIMIT n, 0`), COUNT leg, raw boolean-prefix
    (`drip* OR irrigat*`); doc_search bm25-weighted with
    `fts_match_expr`-sanitised OR-quoted MATCH (fixture calls the
    original sanitizer); script_search bm25 + `kind` filter; vec0
    full-corpus KNN (packed float32 blob, `k = COUNT(*)`); doc_search
    embedding scan.
  - **Metric cases** — the original functions driven end-to-end: all
    14 `make graph-algos` metrics on the shared connection.
  - `canonical()` per metric shape (floats `%.6f` sorted by name;
    louvain → count + modularity + sorted sizes; wcc → count + sorted
    sizes; link-predict → sorted pairs; voterank → ordered list),
    native baseline with per-case reps, `cli_all()` capture of
    `--all --no-apply`.
- `Mojo/src/bench/graph_algos_probe.mojo` — the probe binary: §1
  extension inventory (FAIL if onager unloadable), §2 SQL + FTS cases
  (Mojo-side `execute`, row counts + repr-byte checksums, parity +
  timing ratio), §3 metric functions (Mojo-side canonicalisation using
  a bridge `f"{v:.6f}"` lambda, parity vs native canonicals, per-metric
  timings), §4 end-to-end `cli_all()` (rc==0, all 14 metric headers).
  SKIPs (vec unavailable, sidecar absent) are excluded from the
  denominator and never fail; any mismatch exits 1 (bridge `os._exit`
  pattern).
- `Mojo/bench/run_bench.py`: new leg `graph-algos` (~180 s budget,
  trimmed after first measurement); root Makefile `mojo-bench` help
  line refreshed to the full leg list (it is stale at 5 of 8).

Determinism relied upon (already engineered upstream, verified by the
maint_full_zero_churn audit): Louvain `seed => 42` + canonical
relabeling, `ORDER BY src, dst` materialisation, Katz `alpha=1e-4`.

## Non-goals

- **No gate change**: `make qa` / `make advisory` keep the Python CLI.
  Bench-side only, like every Mojo leg.
- **No algorithm reimplementation**: Onager (Rust) keeps the math. A
  future phase 2 full port (`Mojo/src/common/graph_algos.mojo`, in the
  integrity_check shape — Mojo-side orchestration SQL +
  `python_all_metrics()` goldens) is a separate proposal; porting the
  extension itself to Mojo is out of scope entirely.
- No writes anywhere: no `--apply`, no cache rebuild, no vec backfill.

## Risks / mitigations

- **Shared live DBs** (worktree memory/ is hardlinked to the main
  checkout): every connection read-only (ro URIs, `read_only=True`,
  `query_only` pragma); vec backfill disabled; Onager temp tables are
  session-local `CREATE OR REPLACE TEMP`. If graph.duckdb is stale,
  `query.connect` rebuilds it — flock-serialized write to a regenerable
  cache; the leg docstring names `make graph-rebuild` as precondition.
- **SQL drift vs onager.py**: Onager-path SQL is composed by calling
  the original module's own helpers, never re-typed.
- **Float formatting parity**: canonicalisation uses the same
  `f"{v:.6f}"` shape on both sides (Mojo via bridge lambda), mirroring
  the integrity port's lower/repr/round pattern.
- Onager INSTALL needs the community repo on first-ever use — already
  installed and covered by `tests/test_onager_capabilities.py`.

## Verification plan

1. `make mojo-build`; run `Mojo/bin/graph_algos_probe` from the repo
   root — all cases PASS (SKIPs itemised), summary line, exit 0.
2. `make mojo-bench MOJO_BENCH_ARGS='--leg graph-algos'` — leg green,
   appended to `Mojo/bench/bench_report.txt`.
3. Negative test (manual, uncommitted): perturb one canonical → probe
   exits 1, leg goes red.
4. `make mojo-test` — unaffected, still green.
5. `make search-fresh APPLY=1` after the doc updates.

## Decision requested

Approve phase 1 as specced above (probe + fixture + leg, no gate
change, parity-gated exit). **APPROVED 2026-08-30** with the three
operator decisions recorded in the preamble.

## Execution notes (2026-08-30, same day)

- All spec'd sections shipped; measured: 23/23 SQL cases checksum-exact,
  16/16 metric cases canonical-exact (0 skips — the vec mirror serves),
  CLI end-to-end rc 0 with all 14 headers; leg wall ~24–30 s (budget
  120 s). Mojo-vs-native per-case timings within noise throughout.
- `duckdb_extensions()` names the sqlite scanner `sqlite_scanner` (not
  `sqlite`); `v_node` keys entity types as `kind` — both fixed in the
  fixture's aux cases.
- The negative test caught a latent failure-path bug: byte-slicing
  traps past end-of-string in the canon diagnostics (voterank's 149-byte
  canonical vs the 160-byte truncation) — fixed with a clamped
  `head_bytes` helper; the gate then exits 1 cleanly.
- wcc parity uses the partition signature (count + member sizes desc)
  because onager component ids are arbitrary labels; louvain ids are
  canonically renumbered upstream and check exactly.
- graph_metrics was added as a 17th metric case (beyond the 14) — it
  exercises the eight `onager_mtr_*` whole-graph functions in one
  round-trip; called via `onager_graph_metrics` directly (not the
  query.py cached wrapper) so the Mojo-side timing is honest.
