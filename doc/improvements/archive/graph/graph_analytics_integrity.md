---
title: "Repair graph_analytics served-metric integrity — constant clustering, stale voterank generations, three populations, no pruning"
status: executed
filed: "2026-10-30"
executed: "2026-10-30"
completed_md: "456"
area: "helpers/graph/algorithms.py, helpers/graph/query.py, app.py, memory/research.db graph_analytics"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Repair graph_analytics served-metric integrity — constant clustering, stale voterank generations, three populations, no pruning

**Date:** 2026-10-30 · **Status:** EXECUTED (#456) ·
**Area:** `helpers/graph/algorithms.py`, `helpers/graph/query.py`, `app.py`,
`memory/research.db` (`graph_analytics`, `graph_analytics_contract`)

## 1. Motivation

A live health census of `graph_analytics` (trigger: the 2026-10-30
deferred-item audit asking "what is actually broken in the served
store"; date: 2026-10-30) found the store serving values that disagree
with the live recompute, in one case for every row of a metric:

| Pain | Measured (2026-10-30) |
|---|---|
| `local_clustering_coefficient` is a **constant** | 1,209 rows, **1 distinct value** `{"value": 0.0}`; live function returns nonzero for **all 1,209** entities |
| `/api/graph/metrics/voterank` serves a **2.5-month-old wrong list** | 26 rows carry **7 different seed lists** (2026-08-15 → 2026-10-21); API serves the 2026-08-15 sector/edition list, not the live company list |
| Three incompatible populations in one table | clustering/wcc 1,209 · pagerank 17,747 · degree/louvain/eigenvector/katz/laplacian/local_reaching 18,704 (live 19,053) |
| Rows are never pruned | 360 live nodes missing from store, 11 orphans; `link_prediction` mixes 3 generations; 169,745 rows total; the only `DELETE FROM graph_analytics` in the tree is in a *test* |
| Stale flag permanently true | `entities.last_updated` 2026-10-28T17:12:56 > `max(computed_at)` 2026-10-21 15:16:28 → `/api/graph/stats` reports `stale: true` |

The store is not a cache drifting slowly; it is the **serving layer**
(`app.py:3313` `_SCALAR_GRAPH_METRICS`, `app.py:3406+` payload metrics)
and parts of it answer questions the live graph contradicts.

## 2. Evidence (measured 2026-10-30, this box)

### D1 — clustering is structurally zero on its projection

| Projection | Nodes/Edges | Global transitivity | Nodes with ≥1 triangle |
|---|---|---|---|
| `BelongsTo` → `part_of` (what clustering/wcc compute) | 1,253 / 1,212 | **0.0** | **0** |
| ECONOMIC (`onager.ECONOMIC_EDGE_TYPES`, 14 types) | 18,841 / 44,373 | 0.10892 | 2,913 |
| `_centrality_edge_types` (19 types) | 19,053 / 47,058 | 0.10999 | 2,974 |
| all edge types (`onager_clustering(con)`) | 19,642 / — | — | 1,335 distinct cc, 3,052 nonzero |

`part_of` is a star forest: 1,210 of 1,253 nodes have degree ≤1; a
star has no triangles, so every local clustering coefficient is 0.0 by
construction — mathematically correct, informationally empty. Sampled
live values for stored-zero entities: `20 Microns 0.34278`, `360 ONE
WAM 0.20965`, `3M Company 0.20825`, `3M India 0.25312` — the stored
zeros disagree with the live function on **every row**.

Mechanism: `helpers/graph/algorithms.py:799-804` `_run_clustering` →
`helpers/graph/query.py:4821-4834` `clustering_coefficient(edge_label="BelongsTo")`
→ `_label_to_edge_type("BelongsTo")` resolves via `EDGE_REGISTRY_BY_LABEL`
(`query.py:258-265`, entry `part_of` carries `"label": "BelongsTo"`) to
**`part_of`**. The CLI special-cases pagerank only
(`algorithms.py:1410-1416`, `pr_label = "Economic" if cmd in
("pagerank", "pagerank-weighted") …`); clustering and wcc were left on
the star. The single-edge-type posture was a deliberate 2026-09-19
operator decision (`doc/improvements/archive/graph/graph_centrality_index_noise.md:105-113` §4)
made when `BelongsTo` still carried membership mass; the 2026-10-19
`part_of`/`has_company` retirement turned it into a triangle-free star
and nobody re-audited.

Tests do not catch it: `tests/test_graph.py:740` uses
`edge_label="CompetesWith"` (triangle-bearing), so it passes while the
production default is degenerate; `tests/test_graph_fallbacks.py:96-98`
`test_clustering_star_is_zero` actively *pins* zero-on-star (correct as
a unit test, blind as a projection guard).

### D2 — voterank: seven generations, wrong one served

26 rows / **7 distinct** `{"seeds": [...]}` payloads spanning
`2026-08-15 02:54:13` → `2026-10-21 15:16:01`. `write_analytics`
(`algorithms.py:1110-1137`) is an UPSERT with
`WHERE value != excluded.value` — it never deletes rows for seeds that
dropped out (grep: no `DELETE FROM graph_analytics` in `helpers/` or
`app.py`; the only occurrences are
`tests/test_integration_maint_chain.py:160,166`). The
`_persist_voterank` docstring (`algorithms.py:572-573`) claims
"Recomputing replaces the previous rows wholesale" — it does not.

`app.py:3406-3428` serves "the first strictly-longest list": the query
has no ORDER BY and plans as `SEARCH … USING INDEX
sqlite_autoindex_graph_analytics_1 (metric=?)` → alphabetical by
`entity_name` → the first row is `A_Quarter_That_Refuses_To_Behave`,
whose payload is the **2026-08-15** list. All 7 lists have 10 entries,
so `len(cand) > len(seeds)` never fires again. Measured served output:

```text
['india', 'A_Quarter_That_Refuses_To_Behave', 'Automotive', 'Technology',
 'FMCG', 'Renewable_Energy', 'Engineering_Capital_Goods', 'Chemicals',
 'Quotes', 'Banking']
```

8 of 10 are sectors/themes/editions/super_sector — not the live seed
set `['india','Reliance Industries','Mahindra & Mahindra','Tata
Steel','Tata Consultancy Services','JSW Energy','A_Quarter_That_Refuses_To_Behave',
'Tech Mahindra','Siemens','Adani Ports and SEZ']`.

Latent FK hazard: older lists carry `'Adani Ports and Special Economic
Zone'`, which is **not** in `entities` (0 rows; the live name is
`'Adani Ports and SEZ'`). On a store copy with FK on,
`_persist_voterank(["Adani Ports and Special Economic Zone"])` raises
`IntegrityError: FOREIGN KEY constraint failed` — a future recompute
emitting a stale spelling would abort the voterank leg.

### D3 — three populations; `compute()` and the CLI disagree

| Entry point | Universe |
|---|---|
| `compute()` default `edge_label="BelongsTo"` (`algorithms.py:1044-1051`) | 1,211 (pagerank/clustering/wcc take the star branch) |
| CLI `--all` (pagerank only remapped to Economic) | 17,747 persisted pagerank rows |
| `_onager_central` → `_centrality_edge_types` (degree/eigenvector/katz/laplacian/local_reaching/louvain) | 19,053 live vs 18,704 stored — 360 missing (institution 175, person 165, company 17, edition 3), 11 orphans |

`graph_analytics_contract` (schema v14) pins the served universe for
only the three scipy-routed metrics (betweenness/closeness/harmonic,
1,731 rows each); the other 16 metrics have no contract, which is how
1,209-vs-1,211 and 18,704-vs-19,053 drift went unnoticed.

### D4 — no pruning

`write_analytics` is UPSERT-only; nothing reconciles a metric's stored
population with the one just computed. `link_prediction` holds three
date generations in one metric (2026-09-24: 486 · 2026-10-16: 2,804 ·
2026-10-21: 6,316 rows). Store total 169,745 rows across 19 metrics.

Measured, do not re-audit: the 2026-10-30 census numbers above; the
`part_of` triangle census (0 of 1,253 nodes with ≥1 triangle); the
served-voterank extraction; the 360/11 missing/orphan split; the FK
repro on a store copy.

## 3. Design

Chosen mechanism: **make the store agree with the compute**, metric by
metric, each slice independently landable. Alternatives considered and
rejected:

- *Drop the metrics instead of fixing them* (serve fewer numbers) —
  loses `local_clustering_coefficient`'s real signal (2,974 nodes with
  nonzero cc on the centrality projection); the defect is the
  projection, not the metric.
- *Rewrite `clustering_coefficient`'s default label to `"Economic"` in
  `query.py`* — fixes the API default but leaves `compute()` and the
  CLI free to diverge again; the dispatch-level fix (S3) is the single
  place both entry points share.
- *Delete-then-insert inside `write_analytics` for every metric* —
  changes 16 healthy metrics' write path at once; scoped to the two
  graph-valued payload metrics (`voterank`, `link_prediction`) where
  the accumulation is proven (S1/S4).
- *Serve voterank by `MAX(computed_at)`* — still leaves 7 conflicting
  generations in the store for every other reader; replace-on-write is
  the root fix, freshest-wins is the belt-and-braces read fix.

Slices:

1. **S1 — voterank replace-on-write + freshest-wins read** (root cause
   of D2). `_persist_voterank` wraps its upsert in a transaction that
   first `DELETE FROM graph_analytics WHERE metric='voterank' AND
   entity_name NOT IN (…)`. `app.py`'s voterank branch additionally
   prefers the row with `MAX(computed_at)` (same query, ordered) so
   even a mixed store serves the newest list. Fixes the live
   wrong-answer; nothing else depends on voterank rows.
2. **S2 — clustering projection fix** (D1). In `_run_clustering` (not
   `query.py`), when `edge_label == "BelongsTo"` and `edges is None`,
   compute over `_centrality_edge_types(con)` via
   `onager_clustering(con, edge_types=…)` — the same 19-type universe
   the other nine centralities use (transitivity 0.110, 2,974 nonzero),
   keeping `compute(..., edge_label=…)` overrides working. Add a
   degeneracy guard to the recompute: a metric whose persisted distinct
   value count is 1 over >1,000 rows is logged loudly (the class of
   defect nothing currently detects).
3. **S3 — one universe per metric, documented and asserted** (D3).
   Mirror the pagerank remap for wcc/clustering at the CLI layer (or
   move the remap into `compute`, one place); add a population note to
   the metric table in `doc/design/`; extend the recompute's summary to
   print live-vs-stored row counts so drift is visible per run.
4. **S4 — prune-on-shrink for payload metrics** (D4). Same
   delete-not-in-new-population transaction as S1, applied to
   `link_prediction` (per-node candidate lists), plus a maintenance
   sweep `helpers/maintenance/prune_analytics.py` (dry-run default)
   that orphans any metric's rows whose entity left `entities` (FK
   cascade already covers this; the sweep is for the 11 orphans and
   historical stragglers).
5. **S5 — entity-name hygiene feeding the graph** (small, independent).
   The two formula-debris institution rows (`'Total Shareholding of
   Promoter and Promoter Group (A)=(A)(1)+(A)(2)'`,
   `'(B)=(B)(1)+(B)(2)+(B)(3)+(B)(4)'` — the latter holds 34
   `invested_in` edges) are parser artifacts that rank inside
   `degree_centrality`; quarantine per the resolver's existing
   conventions and re-run the affected centrality legs. Also
   parenthesise the three PEP 758 `except ValueError, TypeError:`
   clauses (`app.py:3417`, `:3435`, `:3531`) — legal on 3.14-only, but
   a hard version floor and a reviewer trap.

Order: S1 first (live wrong-answer, smallest blast radius), S2 next
(only remaining per-row lie), S3/S4 hardening, S5 hygiene. S1 and S2
are each shippable alone.

## 4. Acceptance criteria & shakedown

1. After S1: `.venv/bin/python3 - <<'PY'` running the exact
   `app.py:3406-3428` serve loop returns the **live** seed list
   (contains `Reliance Industries`, `Tata Steel`), and
   `SELECT COUNT(DISTINCT value) FROM graph_analytics WHERE
   metric='voterank'` == 1 after a recompute with `--apply`.
2. After S2: `compute("local_clustering_coefficient")` returns >1
   distinct value (expected ~1,300 distinct over 19k nodes, 2,974
   nonzero); `tests/test_graph_fallbacks.py::test_clustering_star_is_zero`
   still passes (the guard is on the projection choice, not the
   math).
3. Targeted tests stay green:
   `pytest tests/test_graph.py tests/test_graph_fallbacks.py tests/test_centrality_projection.py tests/test_api_graph_metrics.py tests/test_integration_graph_algorithms.py`
   — plus new regression tests: voterank replace-on-write (plant two
   generations, recompute, assert one), clustering-degenerate guard
   (constant metric flagged).
4. Gate: `make static_checks` and `make md-lint` pass; full `make qa`
   once at arc end per `doc/procedures/gates.md`.
5. Eval gate (served semantics change): `helpers/misc/ontology_eval_gate.py`
   over `helpers/misc/ontology_questions.json` between dry-run and
   canonical apply — zero regressions, no undeclared changes; the
   clustering/voterank value changes are declared and materialise as
   improved answers, not ranking regressions elsewhere.
6. Repeat counts: any timing- or cache-shaped claim in the shakedown
   runs 3× interleaved (house rule; none projected here beyond the
   metric recomputes themselves).

| Projected outcome | Today | After |
|---|---|---|
| `/api/graph/metrics/voterank` served list | 2026-08-15 sector/edition list | live company seed list |
| `local_clustering_coefficient` distinct values | 1 (all 0.0) | ~1,300 (2,974 nonzero nodes) |
| voterank generations in store | 7 | 1 |
| Metrics with population contract/assertion | 3 (scipy lane) | 19 (recompute prints live-vs-stored) |
| Rows pruned by recompute | 0 | payload metrics reconcile; 11 orphans swept |

## 5. Risks

- **Clustering values change for every entity** — by design (zeros were
  the defect); the eval gate + a one-line changelog note bound the
  blast radius. `avg_clustering` in `/api/graph/metrics` shifts from
  0.0 to ~0.11 — the UI treats it as a plain scalar.
- **DELETE-in-transaction on the live store** — bounded to
  `metric='voterank'`/'link_prediction' rows not in the incoming
  population, inside the same `with conn:` block; dry-run first, store
  is regenerable by `make recompute-graph`.
- **Projection widening re-introduces hub noise** — the 2026-09-19
  operator ruling excluded membership edges from pagerank for noise
  reasons; S2 widens *clustering only*, and clustering on
  `_centrality_edge_types` is dominated by the economic edges (0.110 ≈
  economic 0.109). **Operator direction 2026-10-30: the 19-type
  `_centrality_edge_types` width stands** — clustering joins the same
  population as the other nine centralities (one universe per metric,
  S3's whole point); strict ECONOMIC remains a one-token fallback if
  hub noise shows up in the eval gate, not the default.
- **S5 quarantine touches `entities`** — resolver conventions govern;
  edges cascade-delete, centrality legs re-run after.

## 6. Non-goals

- No change to `onager_clustering`'s math or the Onager extension lane.
- **Adjacent observations recorded, out of scope here:** the DuckDB
  materialised caches `v_centrality_pagerank`/`_clustering`/`_wcc` do
  not exist (seven sibling `v_centrality_*` tables do; those three
  metrics bypass `_cached_central_scores`) — relevant if S3's unified
  universe ever needs cache parity; `harmonic_centrality` is served
  unnormalised (3,898.57–9,264.68 raw path counts), `katz_centrality`
  is near-constant (1.0001–1.0854 — alpha too small to differentiate),
  `laplacian_centrality` spans 4.0–803,988.0, `local_reaching`
  2.0–13,761.0. None are correctness defects; normalisation is a
  served-semantics product question, not a store-integrity fix.
- No recompute-cadence automation (the `stale: true` flag is honest;
  scheduling `make recompute-graph` is an operator habit, not code).
- No rewrite of `graph_analytics_contract` beyond extending the
  recompute's reporting; a full served-universe contract for all 19
  metrics is a separate arc if S3's assertions prove insufficient.
- No `voterank` seed-set semantics change (what VoteRank *should*
  rank — companies vs taxonomy hubs — is a product question; S1 only
  makes the store tell the truth about what it computed).

## 7. Execution record (2026-10-30)

All five slices landed the day they were filed; S4's sweep found the
11 census orphans already cascade-cleaned.

| Projected outcome | Today (pre) | After (measured) |
|---|---|---|
| `/api/graph/metrics/voterank` served list | 2026-08-15 sector/edition list | live seed list (`Reliance Industries`, `Tata Steel`, …, `Adani Ports and SEZ`) — 10 rows / 1 generation |
| `local_clustering_coefficient` distinct values | 1 (1,209 rows, all 0.0) | **1,287** over 19,051 rows (centrality universe) |
| voterank generations in store | 7 | 1 (`replace_population=True`) |
| Population drift visibility | none | CLI `--apply` prints live-vs-stored per metric; caught +11 residue on 6 upsert-only metrics (11 live-entity stubs with no graph presence — pruned same day by operator direction; "Ashima" entity kept, its 8 stale rows pruned) |
| Rows pruned by recompute | 0 | link_prediction 10,630 rows (was 3 generations); hypermmsbm 1,102 (replace-on-write); 10 person stubs deleted via `helpers.core.db.connect()` |

Slice notes beyond the design: S2's widening went in at the DISPATCH
level (`_run_clustering`) rather than the CLI — `compute()` and the CLI
share it; S3 gave wcc the same dispatch-level widening; S5's PEP 758
fix touched the same handler body and re-STALEd the coverage ledger —
the `amend` path was found unable to restamp fingerprints (validator
defect, fixed + pinned in `test_coverage_ledger.py`). Eval gate ACCEPT
(173 questions, 0 reasons). S5's `except (ValueError, TypeError):`
parenthesisation landed in the same change.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-30 | health census: rows/distinct per metric over `graph_analytics` | `local_clustering_coefficient 1209/1`, `voterank 26/7` | 19 metrics total; full table in the arc log |
| 2026-10-30 | `_project(con, ["part_of"], None)` triangle census | 1,253 nodes, 1,212 edges, transitivity 0.0, 0 nodes ≥1 triangle, 1,210 deg ≤1 | the star that makes clustering constant |
| 2026-10-30 | `_graph_clustering(con, edge_label="BelongsTo", …)` | 1,211 rows, 1 distinct | direct production-path probe |
| 2026-10-30 | `onager_clustering(con)` all types | 1,335 distinct, 3,052 nonzero; all 1,209 stored entities nonzero | 20 Microns 0.34278, 3M India 0.25312 |
| 2026-10-30 | `_label_to_edge_type("BelongsTo")` | `part_of` | via `EDGE_REGISTRY_BY_LABEL`, `query.py:258-265` |
| 2026-10-30 | voterank generations | 26 rows, 7 lists, ts 2026-08-15 → 2026-10-21 | group-by-value census |
| 2026-10-30 | API serve loop replay (no ORDER BY, autoindex scan) | serves `['india','A_Quarter…','Automotive',…]` (Aug list) | EXPLAIN QUERY PLAN: `sqlite_autoindex_graph_analytics_1` |
| 2026-10-30 | `alg.voterank_seeds()` live | 10-name company list incl. `Adani Ports and SEZ` | vs stored `…Special Economic Zone` (0 rows in `entities`) |
| 2026-10-30 | `_persist_voterank(["Adani Ports and Special Economic Zone"])` on store copy | `IntegrityError: FOREIGN KEY constraint failed` | FK-on repro; live row cleaned up after an off-by-one test insert |
| 2026-10-30 | live `compute(m)` vs stored | degree/louvain 19,053 vs 18,704 (360 missing, 11 orphans); pagerank 17,747; wcc/clustering 1,209 vs 1,211 | three populations |
| 2026-10-30 | `MAX(last_updated)` vs `MAX(computed_at)` | 2026-10-28T17:12:56 > 2026-10-21 15:16:28 → `stale: true` | `app.py:2892-2894`, served at `:2960` |
| 2026-10-30 | `grep -rn "DELETE FROM graph_analytics" helpers/ app.py` | 0 hits (tests only: `test_integration_maint_chain.py:160,166`) | no pruning anywhere |
| 2026-10-30 | `link_prediction` by computed_at | 486 / 2,804 / 6,316 rows across 3 dates | three generations in one metric |
