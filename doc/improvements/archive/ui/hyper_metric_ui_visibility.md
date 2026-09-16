---
title: "Hyper metric UI/API visibility — the three fixed rosters + block-key label branch"
status: executed
filed: "2026-09-15"
executed: "2026-09-16"
completed_md: "235"
area: "app.py (metric rosters + label branch), templates/findata.html, frontend/src/views/graph.ts + bundle, tests/test_api_graph_metrics.py"
---

# Hyper metric UI/API visibility — the three fixed rosters + block-key
label branch

**Date:** 2026-09-15 · **Status:** EXECUTED ·
**Area:** `app.py`, `templates/findata.html`,
`frontend/src/views/graph.ts` (+ rebuilt bundle),
`tests/test_api_graph_metrics.py` — D1 of the hyper_lane_wiring §5
backlog (first in the agreed D1→D8→D5+D6→D7→D3 working order). No
schema change, no compute change: the four hyper metrics are LIVE in
`graph_analytics` (1,179 company rows each, 8 hy-MMSBM blocks, zero
churn pinned by W1) — they are simply invisible to the API/UI.

## 1. Problem

`make recompute-hyper` and the maint TIER2 steps land
`ho_pagerank`/`s_betweenness`/`s_closeness` (scalar) and
`hypermmsbm_community` (label) in `graph_analytics`, but:

1. `app.py` rosters reject all four — `_SCALAR_GRAPH_METRICS` (10
   dyadic), `_LABEL_GRAPH_METRICS` {louvain_community,
   weakly_connected_component}, `_PAYLOAD_GRAPH_METRICS`
   {link_prediction, voterank} — `/api/graph/metrics/ho_pagerank`
   400s with an allowlist that cannot name them.
2. The label branch hardcodes `label_key = "community" if louvain
   else "componentId"`; hy-MMSBM rows store `{"block": int,
   "memberships": {...}}` — a THIRD key. Even with the roster open,
   groups would come back empty (label None → row skipped).
3. `findata.html` rank select (12 options) and `graph.ts`
   `METRIC_BLURBS` (12 entries) name no hyper metric; worse, the Rank
   panel fail hint says "is `make recompute-graph` fresh?" — for a
   hyper metric whose refresh is `recompute-hyper`, that hint lies.

## 2. Design

- **Scalar three are pure roster adds**: their values carry
  `{"value": float, "s": 1, "sources": [...], "weighted": bool}` and
  the scalar branch reads `$.value` — the provenance keys are ignored
  (they describe the fit, not the score; surfacing them is a non-goal).
- **hypermmsbm_community joins the LABEL roster with a `block` key**:
  argmax block per entity → the existing groups shape. Soft
  `memberships` stay internal — hy-MMSBM is overlapping, and the
  groups shape is single-label; the honest overlapping view is a
  later decision (see scope cuts).
- **Scope cuts (what the rosters promise)**: the Rank panel gains the
  three scalar options only — louvain itself is not in the rank select
  (groups render in their own panel), so hy-MMSBM is API-only for
  now; community shading in graph.ts stays louvain — argmax
  single-color shading would misrepresent soft memberships.
- **Metric-aware fail hint**: `recompute-hyper` for the hyper set,
  `recompute-graph` otherwise — one ternary in graph.ts, not a
  per-metric map.

## 3. Slices

- **V1 API (app.py)**: `ho_pagerank`/`s_betweenness`/`s_closeness` →
  `_SCALAR_GRAPH_METRICS`; `hypermmsbm_community` →
  `_LABEL_GRAPH_METRICS`; `label_key` gains the `"block"` arm;
  docstring notes the hyper refresh path (`make recompute-hyper` /
  maint TIER2).
- **V2 UI**: `findata.html` rank select +3 options (hyper trio after
  the dyadic cluster metrics); `graph.ts` `METRIC_BLURBS` +3 and the
  metric-aware fail hint; `make frontend` bundle rebuild.
- **V3 tests**: `_J3_ANALYTICS` seeds the hyper shapes (scalar with
  provenance keys; block+memberships label); pin: hyper scalar ranked
  desc with provenance keys ignored, hypermmsbm groups built from
  `block` with memberships ignored, allowlist 400 lists hyper names.

## 4. Acceptance

- `/api/graph/metrics/{ho_pagerank,s_betweenness,s_closeness}` →
  ranked shape; `/api/graph/metrics/hypermmsbm_community` → groups
  shape with the live 8 labels; unknown still 400 with the full
  allowlist.
- Rank panel select offers the hyper trio; blurbs render; the fail
  hint names the right make target per metric family.
- `make frontend-check` green; bundle rebuilt and committed;
  test_api_graph_metrics green; targeted ruff on touched files.
- Gates: targeted per slice; full qa/advisory once at arc end with
  the operator's go.

## 5. Non-goals

- Surfacing `s`/`sources`/`weighted` provenance in responses (they are
  uniform today: s=1, sector/theme/industry, unweighted).
- An overlapping-memberships view (soft communities UI) — hy-MMSBM
  memberships stay internal until a consumer asks.
- Community-shading switcher; hyper metric toggle in the groups panel.
- Frontend tests for blurb text (the TS side has no test lane for
  string maps; `frontend-check` type-gates the record).

## 6. Execution Results

- **V1 EXECUTED (2026-09-15)** — `app.py`: the HGX scalar trio into
  `_SCALAR_GRAPH_METRICS` (provenance keys ignored by the existing
  `$.value` read), `hypermmsbm_community` into `_LABEL_GRAPH_METRICS`,
  and the `label_key` map extended to the third key (`"block"`; the
  400-on-unknown + case-insensitive paths untouched). Route docstring
  names both refresh targets.
- **V2 EXECUTED (2026-09-15)** — `findata.html` rank select +3
  (hyper pagerank / S-betweenness / S-closeness after the dyadic
  cluster block); `graph.ts` `METRIC_BLURBS` +3, `HYPER_METRICS` set,
  and the Rank fail hint metric-aware (`recompute-hyper` vs
  `recompute-graph` — the other three hint sites are louvain/suggestion
  panels and stay correct). Bundle rebuilt (`make frontend`), strict
  type-check + prettier green.
- **V3 EXECUTED (2026-09-15)** — `_J3_ANALYTICS` seeds the real hyper
  value shapes; 3 new tests pin: scalar ranked desc with provenance
  ignored, groups built from `block` with memberships ignored (no
  modularity key), allowlist 400 lists all four names. 19/19.
- **Live smoke (read-only test client)**: ho_pagerank top-1 Exide
  Industries 0.00437, s_betweenness top-1 SpaceX 0.17762, s_closeness
  top-1 Gala Precision Engineering 0.54139, hypermmsbm 8 groups
  (largest block 0 with 319 members) — 1,179 rows per metric, unknown
  metric 400s with the four names in `valid_metrics`.
- Correction en route: the preamble originally said "12 hy-MMSBM
  blocks" — that count was distinct value-JSON strings; the live argmax
  block count is 8 (verified in the smoke above).
