---
title: "Graph read-path consolidation — replace raw `graph_edges` reads for co_mention_top / cross_sector_bridges / edges_by_year with hyper-store-backed reads"
status: executed
filed: "2026-10-24"
executed: "2026-10-24"
completed_md: "438"
area: "helpers/graph (query + app read paths)"
---

# Graph read-path consolidation — Residual #1 of the HGX deep-dive (Table 2)

**Date:** 2026-10-24 · **Status:** EXECUTED 2026-10-24 (completed.md #438)

## 1. TL;DR

*(Amended 2026-10-24, second session — the original "still read raw
`graph_edges`" framing was stale: S1 already landed, see §7. The live
problem is sharper.)*

The HGX deep-dive's Table 2, Residual #1 named three served read paths —
`co_mention_top`, `cross_sector_bridges`, and `edges_by_year` — as raw
`graph_edges` readers. **S1 has since landed** (§7: all three route
through `_stage_edges` → `_resolve_edges` → `_edge_resolved`, with
post-#418 hyper-store sector membership appended from
`_materialise_sector_membership`). But the S1 implementation is **broken
under DuckDB 2.0**: `_resolve_edges`' CTAS + sector-membership INSERT
appends trip the 2.0 restriction `NotImplementedException: Multiple
streaming scans or streaming scans + CTAS / insert` — **live-reproduced
2026-10-24 on all three functions**. The residual is therefore a
**2.0 regression on three served read paths** (they are down, not merely
stale), plus one genuine semantic gap: `cross_sector_bridges` resolves
sectors from the single-valued `v_node.sector_classification` node
attribute, so it cannot express the dual-membership
(`Kuantum Papers` → Agriculture + Packaging) that only the hyper store
expresses — even once unbroken.

## 2. The residual (verbatim source)

From the HGX deep-dive (Table 2, Residuals list):

- **(1) `co_mention_top` / `cross_sector_bridges` / `edges_by_year` still
  read raw `graph_edges`** — flagged unlockable NOW as read-path
  consolidation.
- **(2) D10 opt-in families event/jv/country HOLD** — participant data
  upstream absent (speakers→event measured 0/1,477).
- **(3) D10 motifs/dynamics HOLD** — trigger = S2 eval-bank floors not yet
  met.
- **(4) D10 engine decision HOLD** — **RESOLVED 2026-10-21/22**: first-party
  HGX-primitive lane landed as S1 (completed.md #429); not a hold.
- **(5) §8-R3 HOLD** — **RESOLVED 2026-10-19**: executed, completed.md
  #418 COMPLETE (absorption 1,207/1,207; dual-membership verified).
- **(6) staleness trio / covering indexes / SciPy kernel HOLD** — triggers
  not met (10k-note threshold ≈ 1,388 notes today).

## 3. Why this is the only "unlock now" item

*(Amended 2026-10-24, second session.)* Post-#418 the read surface the
residual names (`graph_edges`) is a **retired row-set**: the migration
`helpers/maintenance/retire_sector_dyads.py` deleted 2,414 dyad rows and
the read side was rewired through
`query._materialise_sector_membership` / `_resolve_edges`
(`e_belongs`/`e_has` projections) — S1's substrate is correct. What
makes this urgent is different from the original filing: the S1
substrate **fails to build at all under DuckDB 2.0** (§7 blocker,
live-reproduced), so the three paths are serving exceptions, not stale
rows. It is a correctness fix in the strictest sense — restore three
down read paths — and the cheapest unlock on the shelf.

## 4. Proposal shape

*(Amended 2026-10-24, second session — S1 was already landed, so the
slices are re-cut: unbreak, then widen.)*

- **S1' (unbreak under 2.0)**: rework `_resolve_edges` so no statement
  mixes a streaming scan with an INSERT. Preferred shape: a **single
  UNION-ALL CTAS** — the base `_stg_edges` SELECT `UNION ALL` the two
  sector-direction projections built from a materialised
  `_sector_resolved` — which removes the post-CTAS INSERT appends
  entirely. CAUTION from our own history: the 1.5.6 CSPE wrong-results
  bug was squarely in UNION-ALL arms **sharing a join subtree**; these
  arms share the `v_node` joins, so the parity test must pin **row
  counts per edge_type**, not just smoke-green. (If the union shape
  misbehaves, fall back to fully materialised intermediates: CTAS the
  base, CTAS the sector slice, CTAS the final union from the two TEMP
  tables.)
- **S2a (regression tests, all three paths)**: frozen-fixture tests
  asserting the three functions return rows again under 2.0 with
  per-edge-type row-count pins (co_mention_top / cross_sector_bridges /
  edges_by_year have NO sector semantics in common — co_mentions and
  jv/acquired edges were never retired — so the dual-membership fixture
  does NOT apply to them).
- **S2b (the real semantic change, cross_sector_bridges only)**: switch
  sector resolution from the single-valued `v_node.sector_classification`
  to the hyper-backed membership rows (`_edge_resolved` `part_of`
  direction or `_sector_resolved`) so dual membership becomes visible.
  This is the only slice where the "Kuantum Papers ↔ Agriculture +
  Packaging, both returned" fixture is achievable by design.
- **S3 (eval-gate, binds to S2b)**: S2b alters query-visible semantics
  (a dual-member company now appears in two sector-bridge groups), so
  per house rule it MUST carry an `ontology_eval_gate.py` bullet over
  the frozen question set before any dry-run/canonical apply. S1'/S2a
  are behavior-restoring, not semantics-altering, and do not need the
  gate.

## 5. Acceptance criteria

*(Amended 2026-10-24, second session.)*

1. All three functions return rows under DuckDB 2.0 (currently:
   `NotImplementedException` on every call) — S1'.
2. Per-edge-type row-count parity pinned on the frozen fixture — S2a.
3. Dual-membership fixture green on `cross_sector_bridges` ONLY, via
   hyper-backed membership rows (both sectors returned for Kuantum
   Papers) — S2b.
4. `ontology_eval_gate` green for S2b before canonical apply — S3.
5. `make qa` + targeted integration tests green.

## 6. Execution

Deferred until operator go — filed per the proposals house rule
(`doc/improvements/proposals/README.md`).

## 7. Design decisions (recorded 2026-10-18, during S1 implementation arc)

- **Routing approach A (self-contained session projections)** selected
  (see session b45). The three read sites (`co_mention_top`,
  `cross_sector_bridges`, `edges_by_year`) open their own DuckDB
  connection and route through `_stage_edges` / `_resolve_edges` /
  `_edge_resolved` (`_sector_resolved` for membership). The sqlite3
  `conn` contract is preserved (ignored) for backward compatibility.
- **Approach B (`e_*` materialised tables as the canonical read
  surface)** deferred — longer-term, avoids the session-scoped RW
  projection overhead but requires a broader table contract.
- **Connection surface**: `read_only=False` required for approach A
  (session `TEMP` + `INSERT` in `_resolve_edges`); `read_only=True`
  blocks it (observed live: DuckDB `NotImplemented` on streaming
  scans + CTAS/insert).
- **Unreconciled blocker (recorded in session)**: `_resolve_edges`
  combines a CTAS (`CREATE OR REPLACE TEMP TABLE _edge_resolved`) and
  INSERT (sector-membership append via `_materialise_sector_membership`)
  in the same session; DuckDB throws `NotImplemented: Multiple
  streaming scans or streaming scans + CTAS / insert`. Fix requires
  splitting the CTAS and INSERT chain or ordering the build differently —
  out of scope for the initial edit; tracked for the next pass.
- **Status post-edit**: three wrappers rewritten (no `graph_edges`
  refs); S2 (frozen dual-membership fixture parity) and S3
  (`ontology_eval_gate`) still due; `make qa` not yet run.

## 8. Second-session verification (2026-10-24)

- **Blocker live-reproduced**: `co_mention_top(5)`,
  `cross_sector_bridges()`, `edges_by_year()` each raise
  `NotImplementedException: Multiple streaming scans or streaming scans
  + CTAS / insert in the same query are not currently supported` under
  DuckDB 2.0. The three served read paths are DOWN, not degraded. The
  failing statements are the two `INSERT INTO _edge_resolved SELECT
  ... FROM _sector_resolved` appends in `_resolve_edges`
  (query.py:2501–2516) — a streaming-scan-plus-insert mix per statement.
- **Substrate verified correct**: `_edge_resolved` is hyper-store-backed
  for sector semantics (post-#418 appends in both stored directions);
  the §1/§3 "still raw" framing in the original filing predated §7's own
  implementation record and has been rewritten above.
- **Dual-membership gap confirmed by reading**: `cross_sector_bridges`
  (query.py:3852) joins `v_node.sector_classification` — single-valued —
  so the hyper store's dual membership is invisible to it even post-S1'.
  S2b exists for exactly this.
- **Operational footnote**: the wrappers open
  `duck_connect(read_only=False)` and the probe opened fine **with the
  quack server resident** — the split-brain guard does not trip at open
  (it governs writes-to-file). The deferred "Approach B (read-only
  safe)" note in the docstrings remains the eventual answer for
  served-mode availability; until then these paths require a quack-down
  session for the TEMP projection build.
