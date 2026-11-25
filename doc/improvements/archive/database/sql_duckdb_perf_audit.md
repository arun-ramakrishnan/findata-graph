---
title: "SQL/DuckDB/Quack performance audit: large-dataset and graph hot paths"
status: executed
filed: "2026-11-25"
executed: "2026-11-25"
completed_md: "570"
area: helpers/core/duckdb_shared.py + helpers/graph/query.py + helpers/graph/onager.py + helpers/graph/algorithms.py + helpers/graph/incidence_batch.py + helpers/graph/derive_* + helpers/maintenance/* (DuckDB bulk-write/read hot paths)
---

# SQL/DuckDB/Quack performance audit: large-dataset and graph hot paths

Broad audit and execution proposal for DuckDB/SQL performance on large-dataset
and graph paths. The goal is to find measured, low-risk wins before changing
global knobs or doing mechanical rewrites. This proposal records the initial
checks/evidence first (per user direction 2026-11-25, m01450/m01503/m01620),
then tracks follow-up evidence passes and any accepted slices.

## Scope

- **In scope**: DuckDB/SQL execution shape on large tables/graphs — bulk
  ingest/write patterns (`executemany` vs Arrow/register/INSERT-SELECT/COPY),
  Python materialization boundaries (`fetchall`/full-edge fetches), graph
  rebuild phase costs, temp spill/memory/thread behavior under quiet-box
  evidence, and query/join/projection opportunities in hot paths.
- **Primary hot paths**: `helpers/graph/query.py` rebuild/materialization,
  `helpers/graph/onager.py` edge materialization/algorithm bridges,
  `helpers/graph/algorithms.py`, `helpers/graph/incidence_batch.py`,
  `helpers/graph/derive_*`, and maintenance DuckDB bulk-write lanes.
- **Out of scope for blind churn**: global DuckDB knob flips without
  workload evidence; contended perf-gate numbers as baseline; mechanical
  `executemany` rewrites; write-once graph-cache indexing without measured
  read win.

## Grounded current state

- **Central DuckDB config**: `helpers/core/duckdb_shared.py:271-306,317-371`
  enforces repo-wide cap/policy via `memory_cap_config()` —
  `MEMORY_LIMIT="2GB"` (operator ruling 2026-11-23), `temp_directory()` uses
  `TMPDIR/TEMP/TMP` then `tempfile.gettempdir()` (no cwd `.tmp` spill),
  optional `FINDATA_DUCKDB_THREADS` env. `connect_shared()` routes to quack
  wire when served; `assert_not_served`/swap-build split-brain rule remains.
- **Graph rebuild shape**: `helpers/graph/query.py` builds mostly set-based
  staging/resolution/materialization. Key anchors read:
  `_resolve_edges` builds `_edge_resolved_base`/`_edge_resolved` and unions
  sector/co-mention projections (`query.py:3041+`); `_materialise_edge_table`
  CTAS per edge type/spec; `_materialise_edges` stages/resolves/materializes
  registry + mixed tables + walk substrate; `_materialise_walk_substrate`
  builds `e_dir` and doubled `e_all_und` carrying edge_type/validity/properties
  (`query.py:3165-3255+` area). Swap/patch rebuild path at `query.py:1700-1835`.
- **Known good bulk pattern**: `helpers/graph/incidence_batch.py` uses Arrow
  register + `DELETE ... WHERE edge_id IN (SELECT edge_id FROM _staged)` +
  `INSERT ... SELECT ...`.
- **Known measured-not-adopted / no-churn doctrine**:
  - `preserve_insertion_order=false` globally measured-not-adopted for current
    write legs (`doc/improvements/archive/database/db_scaling_tuning.md:6.2`,
    2026-10-29 trial).
  - Read-side DuckDB FTS/knob trial measured-not-adopted for convo FTS; 5GB cap
    is policy/history, current enforced cap is 2GB (`doc/improvements/archive/database/duckdb_read_side_tuning_trial.md`, #505, 2026-11-11; `duckdb_shared.py` comments).
  - Some `executemany` paths intentionally kept after measurement (e.g.
    `helpers/graph/query.py:2596`, `helpers/graph/algorithms.py:1306` note
    executemany slower in those specific cases).
- **Scale context**: `doc/local/perf/graph_scaling.md` tracks growth/ladders
  and T1 watch around ~1M doubled-row / hyper-incidence pressure.

## Live scale snapshot (2026-11-25)

| Store/table | Value |
|---|---:|
| `memory/research.duckdb` size | 290M |
| `memory/graph.duckdb` size | 124M |
| `memory/embed_store.duckdb` size | 796M |
| `research.entities` | 27,754 |
| `research.entity_tags` | 7,431 |
| `research.graph_edges` | 86,944 |
| `research.hyper_edges` | 4,636 |
| `research.hyper_incidences` | 59,660 |
| `graph.e_all_und` | 201,572 |
| `graph.e_dir` | 100,786 |
| `graph.h_incidence` | 59,660 |
| `graph.v_node` | 27,754 |
| `graph.v_company` | 25,201 |
| `graph.v_note_embeddings` | 19,379 |
| large edge tables | `e_group 24733`, `e_supplier 18274`, `e_subsidiary 17348`, `e_comention 11384`, `e_semantic_peer 8917`, `e_listed_on_index 6622` |

Box during checks: 4 cores; load observed from `2.91,1.07,0.78` (contended)
down to `1.22,1.57,1.42` after quack stop. `graph.duckdb` had read-only lock
holder `quack_server_child.py` PID 1172600 before Check 3; lock cleared before
scratch profiling.

## Baseline / contention evidence

- **Perf gate run 4800** (2026-11-25 09:53:24, 439.3s, exit 1, 38 benchmarks,
  `.artifacts/perf/20261125T095324-945147`): `integrity_check 13.64s OK`; many
  legs `FAIL(contended)` including verify_notes, sync_tags, static_checks,
  snapshot_check, graph_*, hyper_derive, queries. Not a clean baseline.
- **`gate_query timing --leg graph_rebuild --last 10`**: latest 2026-11-25
  09:53:24 3.97s/5.0s `FAIL(contended)`; clean median ~3.91s, p25 3.82s, p75
  4.19s, max 4.86s, min 3.47s.
- **Perf harness contract**: `tests/run_perf_benchmarks.py:368-380` defines
  `graph_rebuild` as `["helpers/graph/query.py","rebuild","--duckdb",
  "{scratch}/graph_rebuild.duckdb"]`, budget 5.0s.

## Check 1 — bulk-write / `executemany` triage

Command/evidence: `rg -n "executemany" helpers/graph helpers/analytics helpers/maintenance` -> 79 hits.
Heuristic classification (includes comments/tests, needs per-call confirmation):

| Class | Count | Notes |
|---|---:|---|
| duckdb | 33 | primary conversion candidates only when large/frequent |
| unknown | 20 | classify by connection/path |
| mixed-sqlite+duckdb | 14 | common fallback pattern |
| mixed | 7 | inspect callsite |
| sqlite | 5 | low priority unless hot |

**Do-not-blind-convert / already good:**
- `helpers/graph/incidence_batch.py` Arrow register + DELETE-IN-SELECT + INSERT-SELECT.
- `helpers/graph/query.py:2596`, `helpers/graph/algorithms.py:1306` — measured
  slower `executemany` exceptions.
- `helpers/graph/derive_event_participants.py:207` DuckDB whole-rebuild path is
  Arrow staging + single DELETE+INSERT; sqlite fallback per-event executemany.
- `helpers/maintenance/tag_international_entities.py:111` DuckDB set-based
  `INSERT...SELECT`; sqlite fallback executemany.

**Candidates for row-count/frequency evidence pass (no conversion yet):**
- `helpers/graph/derive_co_mentions.py:468` — `INSERT OR IGNORE` edition rows.
- `helpers/graph/derive_merger_hyperedges.py:375`, `helpers/graph/derive_org_membership.py:188` — confirm DuckDB bulk path coverage; sqlite fallback remains.
- `helpers/maintenance/shareholding_sync.py:437,657,1129,1135,1139` — SHP refresh/delete/insert executemany cluster.
- `helpers/maintenance/mca_cin_sync.py:108`, `helpers/maintenance/date_edges.py:355`,
  `helpers/maintenance/seed_analytics_contract.py:108`, `helpers/maintenance/gc_embed_cache.py:512`,
  `helpers/maintenance/trace_contracts.py:457`.
- `helpers/maintenance/rebuild_*.py` — one-shot/index rebuild scripts; optimize only if large/hot.

## Check 2 — `fetchall()` / Python materialization boundary

Evidence: `rg -n "fetchall\(" helpers/graph` -> 200 hits (mixed control-plane and data-plane).

**Edge-scale suspects:**
- `helpers/graph/onager.py` full-edge/projection materializations around edge
  fetch sites (~540,557,804,809,816,884,1446,1452 from first-pass audit) for
  Louvain/modularity/degree/voterank/link-prediction style interop.
- `helpers/graph/algorithms.py:747` full name/score table into dict.
- `helpers/graph/query.py` has many fetchalls; classify by result cardinality
  before touching. `algorithms.py:643` DISTINCT edge types is small/control-plane.

**Recommendation direction:** keep heavy joins/aggregations in DuckDB; avoid
duplicate full-edge fetches; use Arrow/`fetchmany`/streaming only where Python
object materialization is proven necessary and API-compatible.

## Check 3 — scratch graph rebuild profile (quiet-ish)

After quack lock cleared (`lsof` empty on `memory/graph.duckdb`), ran scratch
rebuilds in `$TMPDIR/opencode-graph-profile`.

**Parity run:**
- Command: `.venv/bin/python3 helpers/graph/query.py rebuild --duckdb $TMPDIR/opencode-graph-rebuild/graph_rebuild.duckdb`
- Result: rc 0, wall 3.879s, size 45,625,344 bytes.
- `/usr/bin/time -v`: wall 0:03.69, max RSS 622,268kB, user 3.50s sys 0.53s,
  109% CPU, FS outputs 168,656, minor faults 159,743, major faults 0, swaps 0.

**Instrumented phase profile** (`connect(rebuild=True,fresh=True,stamp_centrality=False)`, monkeypatched phase wrappers):

| Phase | Seconds | Calls |
|---|---:|---:|
| `_materialise_edges` | 1.825 | 1 |
| `_materialise_walk_substrate` | 1.012 | 1 |
| `_materialise_vertices` | 0.401 | 1 |
| `_materialise_edge_table` | 0.381 | 12 |
| `_materialise_embeddings` | 0.277 | 1 |
| `_resolve_edges` | 0.233 | 1 |
| `_create_hnsw_indexes` | 0.176 | 1 |
| `_stage_edges` | 0.147 | 1 |
| `_materialise_v_nodes` | 0.123 | 1 |
| `_materialise_co_mention` | 0.072 | 2 |
| `_materialise_sector_membership` | 0.052 | 2 |
| `_materialise_hyper` | 0.046 | 1 |

Wrapped total 4.746s (instrumentation overhead included; compare uninstrumented 3.879s).

**Key counts observed in instrumented rebuild:** `_stg_edges 86944`,
`_edge_resolved_base 86944`, `_edge_resolved 100786`, `e_dir 100786`,
`e_all_und 201572`, `h_edge 4636`, `h_incidence 59660`, `v_node 27754`.

**Read:** dominant cost is edge substrate/materialization, especially
`_materialise_walk_substrate` (doubles edges into `e_dir/e_all_und`) and edge
resolve/CTAS fanout. RSS stayed well under the 2GB cap; no spill/swap signals.

## Check 4 — thread/memory/temp sweep plan (evidence-gated)

- **Current policy**: 2GB cap, TMPDIR temp spill, optional
  `FINDATA_DUCKDB_THREADS`; no unsigned extension flag needed.
- **Observed**: scratch rebuild max RSS ~622MB, 0 major faults/swaps, 109% CPU.
  No evidence for raising global memory cap.
- **Planned quiet-box matrix (not run yet):** isolated scratch rebuild/profile
  runs varying `FINDATA_DUCKDB_THREADS` in `{1,2,4}` and confirming temp dir
  placement; capture wall/RSS/FS outputs/spill indicators. Adopt only with a
  quiet-box win and no regression/risk to shared desktop usability.

## P3/P4 evidence pass (2026-11-25, scratch/read-only)

### P4 — executemany row-count/frequency matrix

Audit script (heuristic, includes comments/tests/fixtures): 79 hits, classes
`duckdb 55, mixed-sqlite+duckdb 24` (stricter window classifier than first pass).

| Site | Table/store | Live rows / shape | Read | Action |
|---|---|---:|---|---|
| `helpers/graph/derive_co_mentions.py:468` | `research.co_mention_editions` | 1,644 rows | Small; dry-run fetchall+set diff then executemany INSERT OR IGNORE | Defer; only revisit if edition volume grows materially |
| `helpers/graph/derive_merger_hyperedges.py:375` | hyper incidences fallback | `hyper_edges 4,636`, `hyper_incidences 59,660` | DuckDB path uses stage+`bulk_replace_hyper_incidences`; executemany is non-Duck fallback | No change on DuckDB hot path |
| `helpers/graph/derive_org_membership.py:188` | hyper incidences fallback | same hyper tables | DuckDB stage+bulk_replace; sqlite fallback executemany | No change on DuckDB hot path |
| `helpers/graph/derive_event_participants.py:207` | participants fallback | event-participant lane (not counted in this pass) | DuckDB whole-rebuild Arrow staging+single DELETE+INSERT; sqlite per-event executemany | No change on DuckDB hot path |
| `helpers/maintenance/shareholding_sync.py:437,657,1011,1129,1135,1139` | `sources.shp_holders/filings`, `research.graph_edges` | `shp_filings 196`, `shp_holders 8,612`, `graph_edges 86,944` | Batched per filing/sync; row counts modest; mixed read/update/delete semantics | Defer unless profiling shows sync bottleneck |
| `helpers/maintenance/mca_cin_sync.py:108` | external `SOURCES_DB mca_cin` | not measured in pass (external sources DB) | One-shot/maintenance lane, DROP+CREATE+executemany | Low priority; measure before change |
| `helpers/maintenance/date_edges.py:355` | `research.graph_edges` | 86,944 total edges | UPDATE valid_from by id; frequency unknown | Needs call-frequency/affected-row evidence |
| `helpers/maintenance/seed_analytics_contract.py:108` | `research.graph_analytics` | 203,641 rows | One-shot/seed contract insert; idempotent DO NOTHING | Low priority unless seed becomes hot |
| `helpers/maintenance/gc_embed_cache.py:512` | `embed_store` embeddings cache | table present (count query errored: `embeddings` not in expected form? verify schema) | GC/delete lane by dead ids; row count depends GC run | Needs schema-specific count/frequency evidence |
| rebuild_* / analytics/model_analytics / snapshot_db | various | mostly one-shot/offline | Comment/test hits included; many are rebuild/offline paths | Only optimize if measured hot |

**P4 read:** no immediate high-confidence DuckDB executemany rewrite target from
current live sizes. Largest relevant table in this set is `graph_analytics`
(203,641) but seed lane is not a hot runtime path; hyper/derive DuckDB paths
already use bulk/stage patterns where it matters. Next evidence needed: actual
call frequency + rows-per-call from traces/logs for shareholding_sync,
date_edges, gc_embed_cache before any conversion.

### P3 — onager/fetchall boundary micro-probe

Scratch probe (read-only attach `memory/graph.duckdb`, `:memory:` DuckDB,
2GB/TMPDIR config) materializing `g.e_dir` as `_onager_e` (`a_id,b_id,1.0`):

| Metric | Value |
|---|---:|
| `_onager_e` rows | 100,786 |
| `fetchall()` | 0.0379s, 0.0426s, 0.0407s |
| `to_arrow_table()` after DB-API result | 0.0873s, 0.0037s, 0.0028s (warned: extra copy; warm-cache effect) |

**P3 read:** a single 100k-row edge fetchall is ~40ms — not catastrophic, but
`helpers/graph/onager.py` has multiple full-edge/materialization boundaries
(e.g. edge projection, Louvain/modularity/degree/voterank/link-prediction
fetches). Cost compounds if algorithms repeatedly rematerialize or cross Python
boundaries unnecessarily. Arrow conversion is not an obvious win due DB-API
copy warning and warm-cache skew; better direction is avoiding duplicate edge
materializations and keeping compute inside DuckDB/onager SQL where possible.

### P1/P2 — substrate / edge fanout deeper profile (scratch, no code change)

Monkeypatch partial rebuild (stage+resolve+edge tables, no full rebuild finish)
then time `_materialise_walk_substrate` directly:

| Variant | Substrate wall | `e_dir` | `e_all_und` |
|---|---:|---:|---:|
| `k_shards=1` | 1.117s | 100,786 | 201,572 |
| `k_shards=2` | 1.114s | 100,786 | 201,572 |
| `k_shards=4` | 1.360s | 100,786 | 201,572 |

Isolated `e_all_und` doubling probe on 100,786-row `e_dir`-shaped temp table:

| k_shards | `e_all_und` build |
|---:|---:|
| 1 | 0.0908s |
| 2 | 0.2790s |
| 4 | 0.3939s |

**P1/P2 read:** at current scale (~100k directed edges -> ~200k undirected),
`k_shards>1` does NOT help; it adds per-shard INSERT overhead and slightly
regresses substrate build. Default `k_shards=1` single-CTAS is already the
right shape. Substrate cost (~1.0-1.1s wrapped) is dominated by the e_dir
CTAS joins (`_stg_edges` + `v_node` endpoint resolution + sector/comention
unions) plus doubling, not by sharding. Edge-table fanout (`_materialise_edge_table`
0.381s/12 + resolve/stage) remains the other materialization chunk. No code
change recommended for sharding at this scale; future growth trigger for
k_shards>1 is when single-statement materialization exceeds growth budget
(docstring Wall 1 S2 trigger), not current ~200k rows.

## Status log

- 2026-11-25 filed (m01620). Recorded initial audit scope, grounded state, live
  scale, perf contention baseline, checks 1-4 evidence, ranked opportunities,
  locked no-churn rules, and acceptance criteria. No code changes made.
- 2026-11-25 evidence pass (P3/P4): added executemany row-count matrix from
  live DB counts (`co_mention_editions 1644`, `hyper_edges 4636`,
  `hyper_incidences 59660`, `shp_filings 196`, `shp_holders 8612`,
  `graph_edges 86944`, `graph_analytics 203641`) and onager edge-fetch
  micro-probe (`_onager_e 100786`, fetchall ~40ms, Arrow no clear win).
  No immediate high-confidence executemany conversion; next evidence is
  call-frequency/rows-per-call traces for sync/GC lanes and duplicate
  onager materialization counts. No code changes made.
- 2026-11-25 deeper P1/P2 substrate probe: scratch partial rebuild timing
  shows `_materialise_walk_substrate` k_shards=1 1.117s, k=2 1.114s, k=4
  1.360s with identical counts (`e_dir 100786`, `e_all_und 201572`); isolated
  doubling probe k=1 0.0908s vs k=2 0.2790s vs k=4 0.3939s. Conclusion:
  current-scale substrate build should keep default k_shards=1; sharding only
  for growth-budget trigger, not a current win. No code changes made.
- 2026-11-25 unlock verification (m01786): all 6 locked/no-churn rules HELD in
  live code/tree (0 preserve_insertion_order hits; MEMORY_LIMIT still 2GB; no
  new indexes; gate 4800 still contended and unused as baseline; git diff empty;
  scratch-only). P1-P5 unlock gates remain CLOSED for code execution — plans,
  per-table breakdown, duplicate-scan/RSS, call-frequency traces, and thread A/B
  not yet collected. Evidence-only, scratch-only; no code changes.
- 2026-11-25 P1/P2/P4 trace execution (m01812): scratch-only probes in
  `${TMPDIR}/opencode-perf-probe/` (phaseA/plans/parity_k1/parity_k2 .duckdb).
  P2 per-type CTAS 0.4027s/12 (supplier_to 0.2033s/18,274 biggest), top-3
  EXPLAIN ANALYZE single filtered scan of `_edge_resolved` ~0.007-0.009s each —
  no plan pathology, no intermediate-reuse win. P1 e_dir 0.0287s (two hash joins
  vs v_node 27,754) + e_all_und 0.0054s (two 100,786-row scans); k=1/k=2
  content fingerprints IDENTICAL (e_dir `928268066079514940378664`, e_all_und
  `1857400741301390539911295`) with k=2 slower (1.1929s vs 1.0866s) — keep
  k=1. P4 cadence: date_edges one-shot 46,045 rows; shareholding_sync on-demand
  refresh-chain ~44 holders/filing × 196 filings; gc_embed_cache once per gate
  arc (230,391 rows scanned); mca_cin_sync one-shot — no hot-path conversion
  case. All three unlocks now CLOSED on measured NO-WIN; P5 thread A/B still
  genuinely unmet (shared box). No code/runtime changes.
- 2026-11-25 P5 thread/temp A/B (m01894): scratch rebuilds with
  `FINDATA_DUCKDB_THREADS` in {1,2,4} at load 1.41/1.86/1.70 on 4 cores.
  Knob confirmed via `current_setting('threads')` (1/2/4), mem 1.8 GiB, temp
  `/mnt/data/tmp`. Wall 3.57s/3.67s/3.61s, RSS 518/615/647 MB, CPU 99/105/109%,
  0 major faults, 0 swaps, identical 45,625,344-byte outputs — no wall win;
  more threads only raise RSS. Decision: keep threads UNSET, 2GB cap, TMPDIR
  spill; no default change. All five P1-P5 unlocks now CLOSED on measured
  no-win; no code/runtime changes.

## P1/P2/P4 trace execution (2026-11-25, m01812) — scratch, no code change

### P2 — per-edge-type CTAS timings + plans (unlock check complete)

Real fresh scratch build with `_materialise_walk_substrate` patched out to
isolate edge-table CTAS; each `_materialise_edge_table` call wrapped with wall
timer + row count:

| etype | table | wall_s | rows |
|---|---|---:|---:|
| supplier_to | `e_supplier` | 0.2033 | 18,274 |
| same_group | `e_group` | 0.0467 | 24,733 |
| subsidiary_of | `e_subsidiary` | 0.0357 | 17,348 |
| semantic_peer | `e_semantic_peer` | 0.0189 | 8,917 |
| co_mentioned_in | `e_comention` | 0.0182 | 11,384 |
| jv_with | `e_jv` | 0.0149 | 1,507 |
| competes_with | `e_competes` | 0.0137 | 3,577 |
| invested_in | `e_invested` | 0.0131 | 2,128 |
| part_of | `e_belongs` | 0.0117 | 1,229 |
| acquired | `e_acquired` | 0.0089 | 56 |
| customer_of | `e_customer` | 0.0088 | 1 |
| has_company | `e_has` | 0.0087 | 1,229 |

Total per-type CTAS 0.4027s / 12 tables. `EXPLAIN ANALYZE` on the three
heaviest (`supplier_to`, `same_group`, `subsidiary_of`) shows a single
sequential scan of `_edge_resolved` (temp, 100,786 rows) with filter
`edge_type = ? AND src_kind = 'company' AND dst_kind = 'company'`, one row
group, no join, ~0.0074–0.0086s each. **No per-table plan pathology and no
intermediate-reuse win**: each CTAS is already one cheap filtered scan; the
only measurable outlier is `supplier_to` 0.2033s (still < 5s budget). P2
unlock evidence now complete → measured result says NO code change.

### P1 — substrate plan + k=1/k=2 parity (unlock check complete)

`EXPLAIN ANALYZE` on the same staged connection:
- `e_dir` CTAS: 0.0287s total; four-arm UNION with two hash joins
  (`source = name`, `target = name`) against `v_node` (27,754 rows each),
  `_stg_edges` 86,944 rows (zonemap/dynamic filters on source/target),
  `_sector_resolved` arms 1,229 rows ×2, `_comention_resolved` 11,384 rows.
  Joins are already hash joins; no plan pathology at ~87k rows.
- `e_all_und` doubling: 0.0054s; two sequential scans of 100,786-row `_probe_edir`
  (forward + reversed projection), no join.

Content parity k=1 vs k=2 (order-independent `SUM(hash(row))` + row count on
all 6 columns): `e_dir` fingerprint `928268066079514940378664` (100,786 rows)
IDENTICAL for both; `e_all_und` fingerprint `1857400741301390539911295`
(201,572 rows) IDENTICAL for both. Substrate wall k=1 1.0866s vs k=2 1.1929s.
**P1 unlock evidence now complete → k=2 is content-identical but slower; keep
default k_shards=1, no code change.**

### P4 — call-frequency / rows-per-call traces (unlock check complete)

Code shape + live/historical evidence:

| Site | Call cadence | Rows per call | Verdict |
|---|---|---|---|
| `helpers/maintenance/date_edges.py:355` | One-shot backfill lane — not in Makefile; historical 2026-11 run 46,045 rows dated (99.5% of five edge families, `doc/improvements/archive/graph/graph_alpha_lane.md:202`) | all `plan_rows` in one `executemany` (46k at that run) | Cold one-shot; no hot-path justification |
| `helpers/maintenance/shareholding_sync.py:437,657,1011,1129,1135,1139` | `make refresh-shp` / `refresh-chain` phase B — on-demand maintenance, one documented apply 2026-10-28 (`notes_thin_tail_quality.md:553`) | per filing: named holders + aggregates (~44 avg × 196 filings total); edge mark/update/delete per reconcile batch (2026-10-28: 456 new invested_in) | Modest, infrequent; mixed read/update/delete semantics |
| `helpers/maintenance/gc_embed_cache.py:512` | `make maint` lane `MAINT_TARGET=embed-gc` — once per gate arc Phase 0 | 230,391 rows scanned on real store (`note_search_carve_out.md:276`); dead-ids delete count run-dependent | Infrequent; single delete pass |
| `helpers/maintenance/mca_cin_sync.py:108` | One-shot build/apply lane (`mca_cin_resolve` is the frequent refresh leg, not this script) | one-shot DROP+CREATE+insert | Cold; no hot-path justification |

**P4 unlock evidence now complete → all four are maintenance/refresh lanes,
not runtime hot paths; executemany conversion has no measured benefit and
would add churn. No code change.**

**Overall (m01812):** P1/P2/P4 unlock traces executed scratch-only with result
parity; all three now report measured NO-WIN at current scale/cadence, so the
code-execution gate stays closed on evidence (not on missing evidence). Remaining
unlock still genuinely unmet: **P5** quiet-box thread/temp A/B matrix (box still
shared, load ~1.0–1.5 on 4 cores) — no default knob change without it.

### P5 — thread/temp A/B matrix (2026-11-25, m01894)

Scratch-only, one rebuild per thread setting in
`${TMPDIR}/opencode-perf-probe/p5/threads_{1,2,4}.duckdb`. Box load at run
14:24:46 = 1.41, 1.86, 1.70 on 4 cores (not saturated). Knob confirmation via
`current_setting('threads')` and `temp_directory()` on a capped in-memory conn:

| FINDATA_DUCKDB_THREADS | DuckDB threads | memory_limit | temp_directory |
|---:|---:|---|---|
| 1 | 1 | 1.8 GiB | `/mnt/data/tmp` |
| 2 | 2 | 1.8 GiB | `/mnt/data/tmp` |
| 4 | 4 | 1.8 GiB | `/mnt/data/tmp` |

Rebuild A/B (identical output size 45,625,344 bytes each, rc 0):

| threads | wall | max RSS (kB) | FS outputs | major faults | swaps | CPU% |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0:03.57 | 518,444 | 168,848 | 0 | 0 | 99 |
| 2 | 0:03.67 | 615,068 | 170,448 | 0 | 0 | 105 |
| 4 | 0:03.61 | 646,964 | 170,704 | 0 | 0 | 109 |

A separate `time -v` run confirmed `Major (requiring I/O) page faults: 0`,
`Minor (reclaiming a frame) page faults: 149,421`, `Swaps: 0`.

**P5 read:** no thread setting produces a meaningful wall win at this scale —
3.57s (1 thread) vs 3.67s (2) vs 3.61s (4), all within run-to-run noise
(the clean median from gate timing was ~3.91s). More threads monotonically
increase RSS (518→615→647 kB) and CPU% without reducing wall; `threads=1` is
marginally fastest but not enough to justify a default. Temp spill correctly
resolves to `/mnt/data/tmp`; no spill/swap events at any setting. **Decision:
keep `FINDATA_DUCKDB_THREADS` UNSET (DuckDB core-count default), keep `MEMORY_LIMIT=2GB`
and TMPDIR spill policy.** No default policy change; no code/runtime edits. P5
unlock now closed on measured no-win — all five P1–P5 gates are closed on
evidence, none authorize a code change at current scale/cadence.

## Ranked opportunities / unlock map

| Priority | Opportunity | Evidence | Risk / invariant | Unlock / next check |
|---|---|---|---|---|
| P1 | Walk-substrate rebuild cost | `_materialise_walk_substrate` 1.012s wrapped; builds `e_dir 100786`, `e_all_und 201572` | Must preserve directed-cycle semantics (`e_dir`) and undirected BFS semantics (`e_all_und`), edge_type/validity/properties filters | Profile substrate CTAS SQL/plan; evaluate semantic-preserving alternatives only with plan+result parity |
| P2 | Edge resolve/CTAS fanout | `_resolve_edges` 0.233s, `_materialise_edge_table` 0.381s/12, `_stage_edges` 0.147s; 86944->100786 resolved | Endpoint-kind/registry semantics, S5 co_mentioned_in dedupe, mixed-table shapes | Per-table CTAS timing and DuckDB plan; consider intermediate reuse only if planner/materialization wins |
| P3 | Edge-scale Python materialization in onager/algorithms | 200 graph fetchalls; onager full-edge fetch pattern; algorithms full score table | Onager/API boundary may require materialized rows; avoid duplicate fetches | Instrument edge-fetch row counts/RSS/duplicate scans on scratch; test Arrow/streaming boundary where feasible |
| P4 | Proven-large DuckDB executemany writes | 79 executemany hits, 33 duckdb-classified heuristic; shareholding/derive/sync clusters | Known small-row regressions; sqlite fallback parity | Build row-count/call-frequency matrix from code+live DB stats; convert only proven large/frequent DuckDB writes |
| P5 | Targeted thread/temp sweep | 2GB cap, 622MB RSS, no spill, 109% CPU | Global knob churn; desktop contention; prior measured-not-adopted knobs | Controlled quiet-box A/B matrix; no default policy change without evidence |

## Locked / no-churn

- No global `preserve_insertion_order=false` (measured-not-adopted).
- No read-side FTS/5GB-era knob re-trial without workload-specific evidence.
- No blind indexes on write-once graph cache tables.
- No perf conclusions from contended gate run 4800; use quiet/scratch evidence.
- No mechanical `executemany` conversion; preserve measured exceptions.
- No destructive/live-store mutation for audit; scratch copies/temp DBs only.

## Unlock verification (2026-11-25, m01786)

Asked whether the locked/no-churn rules held and whether each P1-P5 unlock
condition is satisfied. Verified against live tree/code, not from session cache:

**Locked / no-churn rules — 6/6 HELD:**
1. No global `preserve_insertion_order=false`: `grep -rn preserve_insertion_order helpers/ tests/` -> 0 hits.
2. No read-side FTS / 5GB knob re-trial: `helpers/core/duckdb_shared.py:272` still `MEMORY_LIMIT = "2GB"`; no FTS pragma changes.
3. No blind indexes on write-once graph cache: only the existing best-effort `_create_hnsw_indexes` path (`query.py:2524`); no new indexes.
4. No perf conclusions from contended gate 4800: `gate_query latest --gate perf` still shows `FAIL(contended)` on all graph_* / query legs; all numbers used came from scratch/quiet runs.
5. No mechanical executemany conversion: `git diff --stat HEAD` empty; zero code edits from the audit.
6. No destructive/live-store mutation: scratch copies in `${TMPDIR}/opencode-graph-{profile,rebuild}` only; live `memory/graph.duckdb` opened read-only.

**Per-priority unlock gates — evidence status (code-change authorization still CLOSED):**

| P | Unlock / next check | Held? | Remaining prerequisite before any code change |
|---|---|---|---|
| P1 | Profile substrate CTAS SQL/plan; semantic-preserving alternatives only with plan+result parity | Partial — timing done (k=1 1.117s, k=2 1.114s, k=4 1.360s; doubling 0.0908/0.2790/0.3939s) | No `EXPLAIN ANALYZE` plan captured; no alternative CTAS evaluated with result parity. k_shards>1 already ruled out at current scale. |
| P2 | Per-table CTAS timing + DuckDB plan; intermediate reuse only if planner/materialization wins | Partial — aggregate `_materialise_edge_table` 0.381s/12 and resolve/stage timings done | No per-edge-type table breakdown or plan; no intermediate-reuse candidate measured. |
| P3 | Instrument edge-fetch row counts/RSS/duplicate scans; test Arrow/streaming boundary | Partial — `_onager_e 100786` rows, fetchall ~40ms, Arrow no clear win | No duplicate-materialization count or algorithm-level RSS delta measured; no streaming boundary trial. |
| P4 | Row-count/call-frequency matrix; convert only proven large/frequent DuckDB writes | Partial — row-count matrix done (co_mention_editions 1644, graph_analytics 203641 etc.) | Call-frequency/rows-per-call traces for shareholding_sync/date_edges/gc_embed_cache NOT collected. No conversion justified. |
| P5 | Controlled quiet-box A/B matrix; no default change without evidence | NOT RUN | No `FINDATA_DUCKDB_THREADS` {1,2,4} A/B matrix; box still shared (load ~1.0-1.5 on 4 cores). No policy change. |

**Verdict:** the 6 locked/no-churn guardrails held, and the unlock gates correctly
remain CLOSED for code execution — none of P1-P5 has the prerequisite plan/parity,
call-frequency, or A/B evidence that the proposal requires before a change. The
work done so far is evidence-only and scratch-only; no code/runtime behavior was
touched. To actually execute a slice, the next concrete evidence step is P2
per-table CTAS timing + `EXPLAIN ANALYZE` plan and P1 substrate plan/parity
(cheapest, highest-ranked), then P4 call-frequency traces.

## Risks

- **Measurement noise on shared desktop**: load/lock contention can dominate;
  use quiet-box/scratch runs and record load/RSS/wall.
- **Semantic regressions in graph substrate**: `e_dir/e_all_und` carry walk
  semantics; any optimization needs parity tests.
- **Over-optimization of cold paths**: rebuild scripts/one-shot maintenance may
  not justify complexity unless large/frequent.
- **DuckDB/Quack boundary constraints**: wire/shared-server rules forbid
  swap-replace over served handle; profile using scratch/unserved copies.

## Acceptance criteria (for future execution slices)

- Every proposed change cites a measured baseline and post-change measurement on
  the same quiet/scratch protocol (wall, RSS, row counts, correctness parity).
- Any graph-substrate optimization preserves existing query semantics and passes
  targeted graph/query/onager tests.
- Any executemany conversion includes before/after row-count and timing evidence
  for that callsite/workload; small-row regressions block adoption.
- No global knob default changes without a quiet-box A/B matrix and documented
  rollback.
