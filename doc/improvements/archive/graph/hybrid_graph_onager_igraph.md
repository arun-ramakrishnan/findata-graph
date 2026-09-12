---
title: "Hybrid graph compute — Onager default with igraph second engine"
status: executed
filed: "2026-09-12"
executed: "2026-09-12"
completed_md: "230"
area: "helpers/graph/"
---

# Hybrid graph compute — Onager default with igraph second engine

**Date:** 2026-09-12 · **Status:** EXECUTED (completed.md #230) — verdict: integration DEFERRED (D15) ·
**Area:** helpers/graph/ (igraph_bridge.py, algorithms.py, query.py, tests)

## 1. Motivation

Onager is the prod graph engine (12 dispatch metrics + link-pred 5 methods + voterank + 8 graph-metrics, Apache-2.0, SQL-native). But it has structural gaps that matter now that weights are continuous (semantic_peer/competes 0.4–0.914, not near-binary):

- Weighted centralities missing: betweenness, closeness, eigenvector, harmonic, laplacian, local-reaching, and all graph_metrics ignore weight (only pagerank/louvain/katz/link-pred honor it). Live parity r 0.83–0.97 assumed near-binary weights — that assumption is now stale.
- No Leiden (Louvain only). Alt community deferred with no consumer.
- No personalized PageRank (dropped as Onager bug), no flow/cuts/MST/iso/motifs, weighted paths unwired (shortest_path is SQL BFS).
- Eigenvector fragile: fails convergence on toy 12n and live shape.
- Speed headroom: full 1648n/19261e PR+Louvain 0.28s Onager (incl 29.6ms remap) vs <0.1s igraph in-memory.

Pilot (helpers/graph/igraph_bridge.py + tests/test_igraph_bridge.py, 7 passed) proves keep-both works: Onager default 10 lanes, igraph for what it does best. GPL cleared by user (main igraph = github.com/igraph/igraph C core).

## 2. Evidence (measured 2026-09-12, this box, full 1648n/19261e)

| Workload | Onager | igraph 1.0.0 (C core, /tmp/venv_igraph) |
|---|---|---|
| Toy 12n/16e PR | 693ms first / 165ms steady, SECTOR_AUTO 0.11643 | 0.04ms, identical top-4 to 5dp |
| Toy Louvain | 186/118ms, 3 comms Q=0.3321 | 0.17ms Louvain+Leiden, 3 comms Q=0.5196 (convention diff, same count) |
| Toy eigenvector | FAILS (convergence 100 iter) | CONVERGES 0.117–1.0 |
| Full build | 29.6ms SQL remap/19k rows | 0.01–0.03s Python build |
| Full PR | ~0.2–0.3s with Louvain | ~0.01s |
| Full Louvain+Leiden | — (Louvain only) | ~0.02–0.04s, Q≈0.53, 14 Leiden comms |
| Full betweenness | unweighted only | weighted OK (Easy-Graph took 23.1s; igraph ms-scale on 2k sample) |
| Toy path TCS→MARUTI | 3 hops SQL BFS | identical nodes |

Ruled out: Graphina (alpha 0.3.5, 11/12 dispatch, no VoteRank, eigenvector fails, int-only + materialise tax — watch-list only). Easy-Graph 1.6.2 wheel-only verdict (pure-Python fallback: PR 2.03s, Louvain 0.31s coarse 4 comms, betweenness 23.1s, link-pred None-stubs, MultiGraph broken, no 3.14 wheel + torch 1.3GB) is **SUPERSEDED 2026-09-12**: the operator source-built the C++ backend (`cpp_easygraph.cpython-311-x86_64-linux-gnu.so`; Python 3.11 venv `/tmp/venv_eg311`; eval scripts `/tmp/eval_eg_cpp.py` (+GraphC, overflow probes, cpp-direct louvain), `/tmp/bench_eg.py`, `/tmp/probe_eg.py`). Re-test EXECUTED 2026-09-12, same day (full log `doc/local/easy_graph_run.txt`, condensed in graph_layer.md): verdict **NO Easy-Graph stands** — cpp PR (~0.02s, correct top5) and cpp-direct louvain (0.015s, Q=0.48, 12–13 comms) are real, but cpp betweenness is numerically broken on live data (378/1648 nodes overflow to ~1e20+; 199/200 sampled values disagree with pure-python BC) — and weighted betweenness was the ONE op that justified a second engine; louvain is not hybrid-wired (cpp export dead code, python path crashes on GraphC); ODR violation in cpp sources. Engine question CLOSED: igraph keeps every lane in the table above.

Full logs: /tmp/eval_onager_baseline.md, /tmp/eval_igraph_hands.md, /tmp/eval_easygraph.md, /tmp/pilot_igraph.md — all CLEANED with the /tmp space purge 2026-09-12; the numbers survive in §2/appendix here and in graph_layer.md. The toy baseline JSON survived and now lives at tests/data/onager_toy_baseline.json (consumed by the parity test). Live notes: doc/local/evaluations/graph_layer.md (67KB). Algorithm reference: doc/local/evaluations/algorithms_assessment.md §1.

## 3. Design

Keep-both routing. Onager stays default prod path (SQL projection, string names, write_analytics/--apply seam, live 14 metrics). igraph is second engine via helpers/graph/igraph_bridge.py (lazy igraph import so .venv collects without igraph; igraph lives in pilot venv until adoption gate). Pilot-venv recreate (the original went with the /tmp purge 2026-09-12): `python3.14 -m venv /tmp/venv_igraph && /tmp/venv_igraph/bin/pip install igraph pytest pyyaml hypothesis python-dotenv numpy duckdb` — numpy is pulled by conftest's autouse embed-matrix fixture (every test; already a repo dependency), hypothesis by pytest.ini `--hypothesis-seed`, dotenv+yaml by the conftest import chain; duckdb (added 2026-09-12, owner direction — already a repo dependency, MPL-2.0) makes the `--apply` persist path run for real: query.py imports it at module level. Without these the in-tree test/apply runs die at import. Footprint measured 2026-09-12: 184 MB on disk (igraph 17 MB incl. the C core, numpy 43 MB, duckdb ~59 MB); /tmp had 5.4G free.

ROUTING (already in bridge):
- ONAGER_DEFAULT: pagerank, WCC, clustering, degree, closeness, betweenness, eigenvector, louvain, link-prediction, graph_metrics.
- IGRAPH: leiden, weighted_* centralities, weighted_shortest_path, maxflow_mincut, louvain_compare.

Lane rationale (recorded 2026-09-12 — nothing moves OFF Onager; igraph lanes
are additive, dry-run default, same write_analytics UPSERT seam, D13 `--apply`):

| Lane | Engine | Why |
|---|---|---|
| pagerank, WCC, clustering, degree, closeness, betweenness, eigenvector, louvain, link-prediction, graph_metrics | Onager | validated in prod, live parity r 0.83–0.97, Apache-2.0, wired into CLI/API |
| leiden_community | igraph | no Onager equivalent; seeded via `_seed_rng` (igraph has no seed arg); full graph Q≈0.53 / 14 comms / 0.05s |
| weighted_betweenness, weighted_closeness | igraph | Onager versions ignore weights — stale with continuous weights (semantic_peer 0.4–0.914) |
| weighted_eigenvector | igraph | Onager fails convergence (toy 12n + live); igraph converges |
| weighted_pagerank | igraph | weights honored end to end |
| weighted_shortest_path | igraph | weighted dist + vpath on a per-etype subgraph; repo default is unweighted SQL BFS hops |
| maxflow_mincut | igraph | chokepoint analysis (S5): s-t flow/cut, weights as capacities, edge/vertex connectivity; live TCS→Maruti 28.1, ec 40 / vc 32 |
| louvain_compare | igraph | seeded side-by-side igraph-Louvain vs igraph-Leiden (Q + comm counts — compare counts, not Q) |
| link-prediction, VoteRank, graph_metrics | Onager only | no igraph builtin for link-pred (hand-roll rejected); no reason to move |

NOTE (S6 nuance): the shipped `louvain_compare` compares the two igraph
partitions (multilevel vs Leiden); the earlier Onager-vs-igraph Q framing
lives in §2's measured table instead.

Slices (each independently landable, in order):
- S1 — Bridge harden: per-edge_type projection + sorted name_to_id (deterministic ids) + batched add_edges + vs[name]/es[weight,etype] — done in pilot, needs review. NCOL roundtrip docs LANDED 2026-09-12: `build_graph` docstring records the caveat (edgelist drops `es["etype"]` — label-filtered analyses cannot roundtrip; SQLite `graph_edges` stays the sole source of truth).
- S2 — Leiden: leiden_communities(weights, resolution, seed) + persist to graph_analytics (metric leiden_community) + CLI leiden [--apply]. No Onager equivalent. Determinism landed with the pilot review (2026-09-12): igraph's community_leiden has NO seed argument, so the bridge installs random.Random(42) as igraph's process RNG per call (mirrors Onager's seed => 42); unseeded full-graph runs measured Q 0.5326 vs 0.5308 before the fix. Acceptance: 14 comms Q≈0.53 on full graph, 2 seeded runs identical.
- S3 — Weighted centralities: weighted_betweenness / closeness / eigenvector (+strength, weighted clustering) with weights=g.es[weight]. Acceptance: toy parity vs pilot JSON + full-graph run <1s, values differ from unweighted Onager (proves weight matters).
- S4 — Weighted path: weighted_shortest_path per-etype subgraph (distances + vpath) + CLI. Acceptance: TCS→MARUTI weighted dist 4.0 matches igraph hands-on; label filter respected. Vocabulary note (review 2026-09-12): the bridge's edge_label takes RAW snake_case edge_type values (belongs_to), while query.shortest_path takes CamelCase EDGE_REGISTRY labels ("BelongsTo") resolved via reg["edge_type"] — semantics mirror, vocabulary differs. Keep the bridge on raw edge_type (matches graph_edges) and document the mapping at wiring time.
- S5 — Maxflow/mincut: LANDED 2026-09-12. `maxflow_mincut(g, source, target)` + CLI `maxflow-mincut --src/--dst`; weights act as capacities (igraph `capacity=` — singular, unlike the other APIs); returns maxflow, mincut, source/target sides, cut edges, s-t edge/vertex connectivity. Toy hand-verified (S-A1-T + S-B5-T: maxflow 6 = mincut, ec/vc 2; single-bridge chokepoint 1). Live: TCS→Maruti Suzuki India 28.1 = 28.1, 40 cut edges (the boundary), ec 40 / vc 32. CLI prints a summary (side counts + target_side + cut edges); dry-run only — an s-t pair has no per-entity metric shape.
- S6 — Louvain compare: louvain_compare (Onager Q vs igraph weighted Q + partition counts, NOT Q equality — conventions differ). Acceptance: same 3-comms toy, full-graph counts logged.
- S7 — Adoption gate: CLOSED 2026-09-12. Licence decision recorded as **D14** (doc/design/graph_design.txt): keep-both, igraph stays second engine, GPL-2.0-or-later operator-cleared, **pilot venv only** (no igraph in .venv/pyproject; lane promotion needs its own proposal). Real --apply + read-back: leiden persisted 1648 rows under `igraph_leiden_readback` into a /tmp COPY of research.db via `write_analytics` (UPSERT), 1648/1648 read back, Q=0.5312 in 0.05s — live DB untouched; caveat resolved 2026-09-12 (owner direction): duckdb added to the recipe and the read-back re-verified with the REAL import chain (query.py duckdb 1.5.5, no stub) — 1648/1648 again. Docs updated: algorithms_assessment.md §1 routing note + graph_layer.md S7 decision section. Why the lane stays pilot despite the new functionality: (a) licence — VOID as a blocker since 2026-09-12: the owner re-cleared GPL use without reservation (repo is owner-operated); the pilot-venv split is now purely an operational choice, not a legal one — pyproject promotion is on the table whenever the operator calls it; (b) validation debt — Onager lanes carry months of prod use + parity measurements, the igraph lanes have pilot tests + one smoke; (c) no wired consumer yet — nothing in app/API/CLI default paths calls the bridge, so being wrong costs nothing in prod; (d) promotion is cheap — each lane that earns a consumer gets its own proposal (D14), and the bridge already speaks the same write_analytics seam. Smoke pins relaxed to bounds (19.0k–21.0k edges / 1.6k–1.8k nodes; counts assert against g.vcount()).

Alternatives considered: Onager-only (loses weighted/Leiden/flow, keeps speed/simplicity — rejected for weight-continuity reason); full-migrate to igraph (loses SQL projection, link-pred native, VoteRank, Apache licence — rejected); Easy-Graph (slower than Onager on full graph, stubs/broken — rejected); Graphina (alpha — rejected).

## 4. Acceptance criteria & shakedown

1. `/tmp/venv_igraph/bin/python -m pytest tests/test_igraph_bridge.py` → 9 passed (maxflow chokepoint test added 2026-09-12), full smoke <0.5s, bounds-checked projection. (Pilot venv now carries pyyaml + hypothesis + python-dotenv + numpy + duckdb — for tests/conftest.py, pytest.ini addopts, and the `--apply` persist path's query.py duckdb import; installed 2026-09-12; the filing-time "7 passed" had dodged conftest by running a /tmp copy of the test file. That venv was purged with the /tmp space cleanup the same day — recreated per the §3 recipe 2026-09-12 (Python 3.14.4, igraph 1.0.0).)
2. `.venv/bin/python -m pytest tests/test_igraph_bridge.py` → 1 skipped (no igraph), bridge import + routing CLI clean.
3. Leiden on full graph deterministic — bridge seeds igraph's RNG (random.Random(42) per call; igraph has no native seed argument); 2 runs same partition + Q. Verified 2026-09-12: unseeded runs differed (Q 0.5326 vs 0.5308), seeded runs identical (toy + full asserted in tests).
4. Weighted vs unweighted deltas logged (proves handover value).
5. Real --apply writes graph_analytics rows readable back (one metric, then revert).
6. Ruff clean on bridge + tests.

| Projected outcome | Today (Onager only) | After (hybrid) |
|---|---|---|
| Leiden | absent | 14 comms, Q≈0.53, <0.05s |
| Weighted BC/CC/EV | unweighted only | weighted, <1s full |
| Weighted path | SQL BFS hops | weighted dist + vpath |
| Maxflow/mincut | absent | toy-verified wrapper |
| Louvain compare | single Q | both Qs + counts logged |
| Full PR+Louvain+Leiden | 0.28s | <0.1s igraph leg |

## 5. Risks

- **GPL-2.0-or-later (igraph)** — user cleared; record in repo decision log, keep lazy import + pilot-venv until gate S7 signs off.
- **Q-convention mismatch (0.3321 vs 0.5196 toy)** — compare counts, not Q equality; document conventions.
- **Link-pred has no igraph builtin** — stays on Onager; hand-roll only if needed via neighbors().
- **ID remap + subgraph projection** — sorted names for determinism; per-label projection for filtered paths; deletion renumbers (store names).
- **Modularity/weight semantics** — igraph honors continuous 0.4–3.0 weights; Onager unweighted lanes keep old values (dual values during transition — label metrics `leiden_*` / `weighted_*`).

## 6. Non-goals

- No full migration off Onager. No link-pred/VoteRank/graph_metrics move. No .venv igraph dependency before S7 gate. No Easy-Graph/Graphina work (ruled out above). No shortest_path SQL BFS replacement (igraph path is additive per-label).

## 7. Deferral (2026-09-12, D15)

Integration is DEFERRED, not abandoned:

- **What stays:** the bridge + tests in-patch (pilot-gated, lazy import,
  dry-run default, opt-in `--apply`), the pilot venv (§3 recipe incl.
  duckdb), and the recorded S1–S7 work. No prod wiring — no app/API/CLI
  default path calls the bridge; Onager prod lanes untouched.
- **Why:** at deferral time the EasyGraph C++ build had re-opened the
  engine choice before any consumer wired into an igraph lane; the
  same-day re-test then CLOSED it (NO Easy-Graph stands — cpp
  betweenness broken, louvain unwired; see §2). What defers now is
  plain consumer-driven promotion (D14's original posture): IGRAPH
  lanes stay dry-run-only capabilities until a real analysis need
  wires in.
- **Revival conditions (any one):** (a) RESOLVED 2026-09-12 — the
  EasyGraph verdict landed and igraph's lanes stand head-to-head;
  (b) a concrete analysis need (Leiden / weighted centralities /
  weighted path / flow-cut) gets a real consumer; (c) the operator
  calls it.
- Until then the suite stays green (9 passed pilot / 1 skipped
  `.venv`) and nothing in the prod analytics path changes.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-12 | onager baseline toy + 2k-sample | PR 0.139s + Louvain 0.141s = 0.28s/881n | /tmp/eval_onager_baseline.md + _raw.json |
| 2026-09-12 | igraph toy + 2k-sample | PR 0.002s + Louvain 0.002s = 0.004s | /tmp/eval_igraph_hands.md |
| 2026-09-12 | graphina toy + 2k-sample | PR 1ms, Louvain 1ms, BC 20ms, CC 58ms, EV FAIL | /tmp/eval_graphina_hands.md |
| 2026-09-12 | easygraph full 1648n/19261e | PR 2.03s, Louvain 0.31s/4 comms, BC 23.1s | /tmp/eval_easygraph.md, no C++ .so in wheel |
| 2026-09-12 | pilot full 1648n/19261e | build 0.02s, PR 0.01s, Louvain+Leiden 0.03s <0.1s | /tmp/pilot_igraph.md, 7 passed |
| 2026-09-12 | determinism probe (review) | unseeded leiden x2: Q 0.5326/0.5308 differ; seeded(42) identical | fix landed same day: _seed_rng(42) per call |
| 2026-09-12 | pilot venv recreate (§3 recipe) | Python 3.14.4 + igraph 1.0.0; in-tree suite 9 passed 0.34s; .venv run 1 skipped | /tmp/venv_igraph was lost with the /tmp purge |
| 2026-09-12 | S5 maxflow-mincut live (TCS→Maruti Suzuki India) | maxflow 28.1 = mincut, 40 cut edges (Maruti boundary), ec 40 / vc 32 | weights as capacities; CLI prints summary + target_side |
| 2026-09-12 | S7 real --apply read-back (research.db /tmp COPY) | 1648 rows written + read back under igraph_leiden_readback, Q=0.5312, 0.05s | duckdb stubbed at the query.py import seam; live DB untouched |
| live | research.db counts | 1649 entities / 19261 edges / 1648 endpoints | graph_layer.md Real scale section |
