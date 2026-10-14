---
title: "PPR re-probe on economic projection — re-anchor the recorded blocker"
status: executed
filed: "2026-10-14"
area: "helpers/graph"
executed: "2026-10-15"
completed_md: "389"
---

# PPR re-probe on economic projection — re-anchor the recorded blocker

**Date:** 2026-10-14 · **Status:** EXECUTED

## 1. TL;DR

`doc/improvements/archive/graph/scipy_personalized_pagerank.md` §7
records a structural blocker: *"PPR on this graph is a 1-hop ego
ranking, which `GET /api/graph/neighbors` already serves cheaper."*
That measurement was taken 2026-09-24 on a graph where the economic
tail was ~960 edges. The graph has since grown to 57,589 edges with
~40,000 ownership/supply edges (`supplier_to` 16,584,
`subsidiary_of` 11,600, `same_group` 9,824).

**Re-probed 2026-10-14 (scratch-isolated, research.db read-only):**

| finding | 2026-09-24 record | 2026-10-14 probe |
|---|---|---|
| hub contamination (post-seed top-10) | `india` (deg 850), NIFTY, note hubs | **gone** — all real companies |
| Infosys seed / hop-1 / hop≥2 | 0.416 / 0.400 / 0.184 | 0.422 / 0.402 / 0.177 (full graph) |
| Wipro seed / hop-1 / hop≥2 | 0.455 / 0.416 / — | 0.450 / 0.412 / 0.138 (full graph) |
| HDFC Bank hop≥2 | not measured | **0.508** (full) / 0.508 (economic) |
| TCS hop≥2 | not measured | **0.196** (economic) |

The full-graph numbers **reproduce the recorded values**, confirming
the probe is calibrated. But on the *economic projection* (12
non-membership types), the post-1-hop top-8 for Reliance is EIH, Castrol
India, Grasim, Saregama, Asian Paints, Axis Bank — all real companies,
zero membership/index hubs.

**Conclusion:** the blocker is real but narrower than recorded. The
"1-hop ego rank" characterization holds for star-attached IT names
(Infosys hop≥2 = 0.038) but not uniformly (HDFC Bank hop≥2 = 0.508).
The recorded verdict should be re-anchored: PPR is **seed-dependent**,
not universally degenerate.

## 2. Design

- Re-run the PPR exact solve on the economic projection for a seed
  panel (Infosys, Wipro, Reliance, TCS, HDFC Bank, plus 5 random
  contract companies).
- Report seed / hop-1 / hop≥2 mass + post-1-hop top-10 per seed.
- Update `scipy_personalized_pagerank.md` §7 with the new measurement
  and the re-anchored verdict.
- **No code change.** The lane stays DEFERRED; this is a measurement
  update.

## 3. Acceptance

1. The seed panel's hop≥2 mass is reported per seed.
2. The post-1-hub top-10 shows no membership/index hubs (verified by
   name against `EDGE_TYPES_EXCLUDED_FROM_CENTRALITY`).
3. `scipy_personalized_pagerank.md` §7 is updated with the new table and
   the re-anchored verdict.
4. The implementation trap (transposed equation) is re-confirmed in the
   update.

## 4. Non-goals / risks

- **No un-deferral of PPR.** The lane stays deferred; a named consumer
  is still required.
- **No change to the Onager wrap path.** The Onager bug (restart
  hardcoded to node 1) is unchanged.
- **The economic projection is not new.** `onager.ECONOMIC_EDGE_TYPES`
  already exists; this probe uses it.

## 5. Follows

- `doc/improvements/archive/graph/scipy_personalized_pagerank.md` §7
  (the recorded blocker this re-probes).
- `doc/improvements/archive/graph/pagerank_graph_enhancements.md` §6
  (the economic projection definition).
