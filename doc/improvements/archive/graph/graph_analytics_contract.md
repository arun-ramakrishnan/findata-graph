---
title: "Graph analytics contract — break the self-referential served-universe loop"
status: executed
filed: "2026-10-18"
area: "helpers/graph"
executed: "2026-10-19"
completed_md: "416"
---

# Graph analytics contract — break the self-referential served-universe loop

**Date:** 2026-10-18 · **Status:** EXECUTED 2026-10-19 (completed.md
entry 416 — S1 landed: contract table live, v14, byte-identical parity
verified on the production store; S2 grow path + S3 prune policy remain
open as future slices)

## 1. TL;DR

The SCIPY/L1B-routed centralities (closeness, harmonic, betweenness)
derive their served universe **from the table they write to**:
`contract_sources()` runs `SELECT DISTINCT entity_name FROM
graph_analytics WHERE metric = ?` (`helpers/graph/scipy_bridge.py:161-177`),
and the L1B lane repeats the pattern inline
(`helpers/graph/algorithms.py:836-844`). The code comments admit the
failure mode: "UPSERT never deletes, so silently skipping would serve
stale rows indefinitely" (`algorithms.py:895-896`). The consequence is
a **frozen contract**: 1,732 names — an accumulation of capped top-N
runs spanning 2026-07-27 → 2026-10-16 — served as the centrality
universe while the graph grew to 20,044 edge-touched nodes. The set
can never grow (nothing inserts new contract names) and its
composition drifted far from its own docstring ("the persisted company
contract" — only 1,183 of 1,732 rows are companies). This arc gives
the contract a source of truth that is not its own output.

## 2. Evidence (measured 2026-10-18, live)

**The loop** — both readers, same query shape:

```sql
SELECT DISTINCT entity_name FROM graph_analytics WHERE metric = ?
```

- `helpers/graph/scipy_bridge.py:161-177` (`contract_sources`, used by
  `_run_scipy_lane_pair` at `algorithms.py:889-894`)
- `helpers/graph/algorithms.py:836-844` (`_run_l1b_lane`, with the
  KeyError drift guard at `:845-856`)

**The frozen set** — `betweenness_centrality` rows by `computed_at`:

```text
2026-07-27:  49    2026-08-25: 130    2026-09-17:   44
2026-07-28:  18    2026-08-27:   1    2026-10-16: 1388
2026-07-29:  57    2026-09-08:   1    -----------------
2026-08-04:   3    2026-09-09:   1    total:      1732
2026-08-11:   5    2026-09-10:  11
2026-08-19:   2    2026-09-14:  22
```

**Composition vs the docstring** — "company contract" is 68% true:

| entity_type | rows |
|---|---|
| company | 1,183 |
| institution | 207 |
| sub_sector | 141 |
| edition | 116 |
| sector | 42 |
| country | 21 |
| theme | 12 |
| super_sector | 10 |

**Coverage vs the live universe** (edge-touched n = 20,044 excluding
`listed_on_index`, per `stats.py:276-284`):

| metric | rows | coverage |
|---|---|---|
| degree/eigenvector/katz/laplacian/local-reaching/louvain | 20,044 | 100% |
| pagerank | 19,088 | 95.2% |
| **closeness / harmonic / betweenness** | **1,732** | **8.6%** |
| local_clustering / wcc | 1,207 | 6.0% |

**Why full-universe is not a drop-in** — the cost ladder:

- contract-only closeness+harmonic: **2.7 s** (`scipy_exact_universe.md`
  §2; the lane's live cost)
- all-roots dijkstra: **~80 s + ~6 GB peak** at 21,461 roots
  (`stats.py:259-261`, measured 2026-09-26)
- cold Onager closeness: 150-163 s (the lane exists to avoid this)
- `recompute-graph --apply` ceiling: **20 s** (`recompute_graph_parallel_fanout.md`
  §7) — a 19,088-company contract is ~11× the current lane cost and
  blows the ceiling ~1.5×over

**The only self-healing it has** — `graph_analytics.entity_name`
FK-cascades on entity delete/rename, so the set shrinks correctly on
folds. Growth has no mechanism at all.

## 3. Design

**S1 — materialize the contract (the loop-breaker).** New SQLite table
`graph_analytics_contract(metric, entity_name, seeded_at)` with the
same FK to `entities(name) ON DELETE CASCADE` — the delete/rename
self-healing is preserved because it lives in the FK, not in the
self-read. `contract_sources()` reads the contract table; the metric
rows become pure cache. Seed once from the current 1,732 distinct
names → served set is **byte-identical on day one**; parity pins and
the incumbents tests do not move. Schema v13 → v14 via
`ensure_db_meta` (`helpers/core/db.py:74`).

**S2 — explicit growth path.** `stamp-contract --grow` re-derives the
contract from source of truth — recommended derivation: **top-N
companies by `degree_centrality`** (degree IS full-universe, 20,044
rows, stamped every `recompute-graph`), N a stamped parameter defaulting
to 4,000 (~2.3× current lane cost, ≈6 s est. — inside the 20 s
ceiling with headroom). Loud: prints old/new set cardinality and the
diff. Non-company names (institutions, sectors) stay only if present
in the seed — growth adds companies, never silently drops lanes.

**S3 — prune + drift policy.** Orphan audit joins the contract to
`entities`/the name-fold ledger (`helpers/graph/name_dedup.py`): a
folded-away name leaves the contract at the next stamp instead of
KeyError-ing a lane. The `--top N` write path stops being a contract
mutation — `write_analytics` gains a `contract_only` guard so capped
runs can never re-freeze the set.

**S4 — docs.** `doc/design/graph_design.md` §5 gains the contract
section (derivation, N, stamp provenance); the "company-only contract"
claim is corrected to the actual composition; `features.txt`-class
registers pick it up.

## 4. Acceptance

- S1 parity: served values byte-identical pre/post switch (incumbents
  test class in `test_integration_graph_algorithms.py`).
- S2: `--grow` is dry-run by default with `--apply` guard; the diff
  line is machine-checkable.
- S3: a capped top-N run followed by a contract read yields the
  UNCHANGED contract set (the regression this arc exists for).
- Targeted: scipy lane + l1b + integration graph algorithm tests.
  Full `make qa` once at arc end on the operator's go.
- Budget: the scipy lane leg must stay inside the `recompute-graph`
  20 s ceiling at the default N — measured, not assumed, before S2
  lands.

## 5. Risks & non-goals

- **Not fixing the universe size in this arc's S1** — full-company
  (19,088) serving is a budget re-negotiation, explicitly out of scope
  until S2's cost is measured live.
- No change to ROUTING; no change to `v_centrality_*` stamp tables
  (separate mechanism, already stamped-on-demand).
- The KeyError drift guard stays — it is correct behavior for a
  contract name the projection lost; S3 changes *who maintains the
  set*, not the guard.

## 6. References

- `doc/improvements/archive/graph/scipy_routing_dispatch.md` (ROUTING,
  served-universe preservation test), `l1_closeness_fold.md` §8
  (contract population, audit), `centrality_rebuild_contract.md`
  (stamp-on-demand, data-only rebuild), `scipy_exact_universe.md` §2
  (cost ladder), `graph_centrality_index_noise.md` (#254 — why
  `listed_on_index` is out of the universe).

## 7. Arc addendum — S1 executed 2026-10-19 (completed.md #416)

Landed exactly as designed, one lazy-import addition the house pattern
required:

- `helpers/maintenance/seed_analytics_contract.py` — new: `CONTRACT_DDL`
  (metric-first PK, FK cascade to `entities`), `SERVED_METRICS`,
  `seed(db_path, apply=)` dry-run default; `--apply` seeds with `seq` =
  legacy query order and stamps v14 via `ensure_db_meta`; house sys.path
  bootstrap.
- `scipy_bridge.contract_sources` — contract-table reader (`ORDER BY
  seq`); **absent-or-empty → legacy self-read fallback** (restore-safe);
  docstring carries the loop story.
- `algorithms._run_l1b_lane` — inline DISTINCT duplicate collapsed onto
  the shared reader (lazy `from helpers.graph import scipy_bridge as
  _sb` per the house heavy-import pattern).
- `helpers/core/db.py` — `EXPECTED_USER_VERSION`/`EXPECTED_SCHEMA_VERSION`
  13 → 14, canonical-only (no DuckDB cache bump, v13 precedent).

**Live apply (2026-10-19):** 1,732 × 3 metrics = **5,196 contract rows**;
`user_version` 14; **byte-identical parity verified on the production
store** — served list == legacy DISTINCT list, order included, all three
metrics.

**Verification:** 8/8 new contract tests (parity, fallback, dry-run
writes nothing, idempotence, FK cascade, per-metric partial-seed
fallback, v14 constants, connection hygiene); regression net green:
66/66 `test_integration_graph_algorithms`, 71/71 scipy_bridge +
centrality_cache + graph_stats; ruff check/format clean;
`doc/design/db_schema.md` §`graph_analytics_contract` added.

**Still open (future slices, unchanged from §3):** S2 grow path
(`--grow`, top-N companies by degree — the budget-measured expansion),
S3 prune/drift policy, S4 docs beyond `db_schema.md`
(`graph_design.md` §5 contract section).
