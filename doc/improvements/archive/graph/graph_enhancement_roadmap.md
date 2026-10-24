---
title: "Graph enhancement roadmap — broken-paths fix, D10 follow-through, HGX wiring (with the explicitly-not-now register)"
status: executed
filed: "2026-10-25"
executed: "2026-10-25"
completed_md: "439"
area: "helpers/graph (query, hypergraph, prediction lanes)"
---

# Graph enhancement roadmap — the now-list, sequenced

**Date:** 2026-10-25 · **Status:** proposed (umbrella filing — slices execute
per their own arcs; the read-path slice has its own live proposal)

## 1. Motivation

Post-2.0 landscape review (2026-10-24/25 sessions): the language-move
question (Rust/Mojo rewrites) was re-asked and answered by the record —
every candidate has a measured verdict (Graphina eliminated 2026-09-12,
EasyGraph rejected, igraph shelved-then-seated as backup, onager = the
Rust production engine, CSR+Mojo BFS landed-and-gated #301). Scale sits
at T0 (115K doubled rows; T1≈1M is ~8.7× away, ~11 VIGIL-class intakes;
graph_scaling.md growth record). The wins that remain at this scale are
**algorithmic and wiring work, not linguistic** — this filing sequences
them so nothing is lost track of.

## 2. Slice A — the three read paths: EXECUTED (#438) + A′ EXECUTED (2026-10-25)

**#438 (2026-10-24, parallel session)**: `_resolve_edges` re-cut into three
fully-materialised statements (both prior forms — CTAS+INSERT appends AND
the single multi-arm UNION CTAS — throw under 2.0), `edges_by_year` CASTs
DATE→VARCHAR for 2.0 typing, and `cross_sector_bridges` switched to
hyper-backed dual-membership resolution, with per-edge-type row pins and
ontology_eval_gate ACCEPT 166.

**A′ served-mode (EXECUTED 2026-10-25, this session)** — approach B, the
long-deferred item: the three wrappers now read the MATERIALISED tables
(`e_comention` / `e_jv` / `e_acquired` / `e_belongs`) over a READ-ONLY
connection through the duckdb_lock funnel (`_c_series_ro_conn`,
query.py) — wire attach under quack, read-only file open otherwise. A
file-mode RO open is NOT viable under 2.0 with a resident server (the
server's RW lock excludes cross-process file opens entirely), so the
funnel's wire path is the served route. Live-verified with the quack
server resident: all three answer (bridges = the parity-verified 72
groups).

Parity evidence (measured in an unserved window, 2026-10-25):
`cross_sector_bridges` 72/72 MATCH and `edges_by_year` 8/8 MATCH vs the
session-projection implementations from entry #438; `co_mention_top` RE-BASED —
the old metric counted each pair's stored ORIENTATION only (arbitrary
per row on the live store, which holds one orientation per unordered
pair) and dropped rows whose partner name failed v_node resolution
(measured: Dhruv Consultancy old 0 vs sqlite truth 5; 360 ONE WAM
b_side=0). New semantic = participation count (entity on either side),
matching the sqlite source's any-side counts exactly; #438 fixture pins
re-based 4/3/2 → 6/5/4 with the reference made orientation-free.
Served-compat routing pinned by test (default conn must route through
`open_read_only`, never a file-mode RW open).

**Operational event during execution**: the production `graph.duckdb`
was found MISSING (parallel-session ingest at 09:04 wrote research.db —
25,684 entities / 62,434 edges / 18 types — then died before rebuilding
the derived store; stale build.lock, no writer). Recovered via the
documented recipe: quack-stop → `make graph-rebuild` → `make
stamp-centrality` (stop/start dance) → quack-start. Source of truth
untouched throughout.

## 3. Slice B — D10 prediction-lane follow-through

Landed: hyper-native link prediction lane S1 (ed61ae830, completed.md
entry #429-family) — the engine-decision blocker resolved 2026-10-21/22.
Follow-through items, WITH their recorded per-family holds respected:

- Opt-in families event/jv/country: **HOLD — participant data upstream
  absent** (speakers→event measured 0/1,477). Re-open trigger: a
  participant-bearing ingest lane lands.
- Motifs/dynamics: **HOLD — S2 eval-bank floors not met**. Re-open
  trigger: eval-bank floors reach thresholds on the frozen question set.
- Otherwise: prediction-lane validation + eval hardening per pending.md
  D10 entries (density restatement already done — per-family density
  gates are the operating contract).

## 4. Slice C — HGX shelf wiring (capability work)

**Leg 1 (community algos) EVALUATED 2026-10-25 — verdict: STAY GATED, no
wiring earned.** The shelf's named trigger for community lanes 2–4 is
"hy-MMSBM disagreement QA"; that study ran bench-side (scratch, same
filtered incidence as the wired lane: 242 hyperedges / 1,210 nodes):

| challenger | result |
|---|---|
| hy-MMSBM (incumbent) | healthy: 0.8 s fit, 8 effective blocks — unchallenged |
| core_periphery | works, **~475–500 s MCMC**; signal genuinely ORTHOGONAL (ARI≈0 vs MMSBM argmax by construction; ρ=−0.288 vs ho_pagerank, **0/20 top-company overlap**) — a real distinct axis but consumerless and costly; wire only when a "core vs periphery" ask lands that the 6 centrality lanes can't answer |
| hyperlink_communities | **degenerate at this scale** (inlined 0.1 s after an HGX 1.8 API bug — `ReadwriteError` wraps `FileNotFoundError`, defeating its own compute fallback): 2-cut 239+3, 4-cut 218-giant; the taxonomy incidence interleaves rather than separates. Re-ask if denser families (editions/events) ever dominate |
| HySC + Hypergraph-MT | **dep-walled**: both import-block on scikit-learn (not in the lean venv); measuring them is a dep-delta decision — parked for the operator |

So the trigger's question — "does the incumbent disagree with
alternatives in a way that demands wiring them?" — is answered **no at
current scale**. Legs 2–3 keep their named triggers (s-paths: first
path-explanation UI/API ask; multi-order Laplacian: spectral-feature
consumer; per the D15 discipline both need a named consumer first).

Negatives stay closed: HGNN, KaHyPar, GPU (HD 530 no official
target).

## 5. Explicitly NOT now (the register — do not pull these forward)

| item | state | trigger |
|---|---|---|
| CSR rebuild + ART bridge | CSR substrate BUILT (#301, 0.17 s); ART bridge is the T1 action | T1: ~1M doubled rows (~11 VIGIL intakes) |
| Mojo BFS CSR promotion | kernel parity-proven (42.6× 1-hop vs SQL, 4.9–5.7× 2-hop) | T2: ~10M rows, measured >2× crossover |
| **Mojo SIMD cosine kernel promotion** | **PROVEN but NOT live — verified 2026-10-25**: no production caller (script_query + rg over helpers/: zero hits; the kernel lives in `Mojo/src/common/cosine.mojo` + bench harnesses `tests/bench_cosine_knn.py`, `Mojo/src/bench/bench_cosine*.mojo`). Production similarity = DuckDB SQL brute (`semantic_neighbors`, ~3 ms at 1,209 company embeddings) | embedding-count trigger: when SQL brute > ~50 ms class (~10⁵+ rows or repeated full-corpus scans); until then SQL is simpler and codec-adjacent to the store |
| Onager extension of missing primitives (e.g. personalized pagerank) | on-ramp OPEN since #434 (vendored Rust build owned) | when a lane is blocked on a missing primitive — incremental, not preemptive |
| igraph promotion to primary | backup seat (D17) | if onager lane fails or a family is igraph-unique |
| Language rewrites (Graphina-class) | eliminated/rejected with measured verdicts | none foreseeable — re-ask at T2 if the ladder fires |

## 6. Acceptance (per slice, at execution time)

- A: per the amended read-path proposal's §5 (row-count pins +
  dual-membership fixture on cross_sector_bridges only + eval gate).
- B: eval-bank movement on the frozen question set; holds recorded in
  pending.md stay authoritative.
- C: new served surfaces carry their own proposals (house rule) with
  eval-gate bullets where query-visible semantics move.

## 7. Execution

Deferred until operator go. Slice A′ EXECUTED 2026-10-25 (see §2);
remaining: B and C in that order (C's community-algo leg may precede
B's hold-gated remainder if the operator prefers capability over
validation hardening).
