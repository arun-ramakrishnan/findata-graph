---
title: "Longest-chains cap transition — the exact→sampled crossover went live unrecorded"
status: executed
filed: "2026-10-18"
area: "helpers/graph"
executed: "2026-10-19"
completed_md: "417"
---

# Longest-chains cap transition — the exact→sampled crossover went live unrecorded

**Date:** 2026-10-18 · **Status:** EXECUTED 2026-10-19 (completed.md
entry 417 — cap raised 3000 → 6000 with the sampled cost measured;
cap-crossing tripwire landed in graph_scale_watch; S1 record landed in
the stats docstring + completed.md)

## 1. TL;DR

The S3 hard cap on `longest_chains` was accepted while the diagnostic
still ran exact: "live render unchanged at 14.4s — universe 1,722 <
3,000" (`hgx_first_scaling.md:151`). The universe has since grown
**11.6×** to 20,044 edge-touched nodes and the cap (`max_exact=3000`,
`helpers/graph/stats.py:237-238`) is now **active in production**:
diameter and distant-pairs are stride-sampled lower bounds over 15% of
roots. The transition crossed silently — no doc records it, and the
stale register (`$TMPDIR/features.txt`) went further and mis-cited the
1,722 baseline as a D10 reopen threshold. This arc records the
transition as a first-class state change and sets the policy for what
"sampling engaged" means going forward: a cap that can be crossed
without a recorded decision will be crossed again at the next growth
step.

## 2. Evidence (measured 2026-10-18, live)

**The crossover, reproduced on the live store** (18.0 s render):

```text
ALL edges (excl. index membership): 104 components | diameter 12
  (167 pairs at d=12) | median dist 4
  | SAMPLED 3000/20044 roots (cap 3000): d >= 12, distances are lower bounds
```

**Universe growth vs the cap:**

| measure | at S3 acceptance | live now |
|---|---|---|
| edge-touched universe | 1,722 | **20,044** (11.6×) |
| vs cap 3,000 | 57% of cap | **668% of cap** |
| sampled roots | n/a (exact) | 3,000 = 15.0% |
| render time | 14.4 s | 18.0 s |
| graph_edges | ~17k-class | 64,847 |

**Component structure** — the graph is now one dominant blob: largest
component 18,051 of 20,044 nodes (**90.1%**), 104 components total.
Diameter 12 across that component is a structural fact the sampled
mode only lower-bounds.

**Memory math for cap policy** — `shortest_path(indices=roots)` costs
O(roots × n) output (float64 dist + int32 pred ≈ 12 B/cell):

```text
roots=3,000  → ~0.72 GB   (current cap)
roots=6,000  → ~1.44 GB
roots=21,461 → ~5.2 GB    (the measured "~6 GB" all-roots figure,
                           stats.py:259-261, on the 14 GB box)
```

**Existing opt-out** — `exact=True` (scipy_exact_universe S2) lifts
the sample to all roots, never the gate path.

## 3. Slices

- **S1 Record the transition** — completed.md entry + a state line in
  `graph_design.md` §diagnostics: sampled mode ENGAGED since
  2026-10-16-class universe; correct the `features.txt` D10 condition
  conflation at the same change.
- **S2 Policy decision (operator)** — one of:
  (a) keep 3,000 and accept lower-bound diameters;
  (b) raise to 6,000 (~2× sample cost, ~1.4 GB — still seconds-class
  render, well under the gate budget);
  (c) scheduled exact refresh (off-gate, `exact=True`, the 80 s/6 GB
  run) on a cadence, keeping the gate path sampled.
  Recommendation: (b) + (c) — cheap headroom now, exact numbers on
  demand.
- **S3 Tripwire** — extend `graph_scale_watch` with a cap-crossing
  watcher: when n crosses `max_exact`, emit a loud line into the stats
  render header (it currently only labels the section) and a
  `graph_scale_watch` event, so the next crossover is a notification,
  not an archaeology find.

## 4. Acceptance

- S1: the sampled/exact state is queryable (render header or
  `graph_scale_watch`) without re-deriving from source.
- S3: forcing n over the cap in a fixture fires the tripwire event.
- Gate path stays sub-dominant per the S3 doctrine
  (`hgx_first_scaling.md` §3): no cap change may push the render into
  gate-budget territory (18 s today; (b) projected ~36 s is still
  off-gate — verify, don't assume).

## 5. Risks & non-goals

- No change to the incidence-native analytics lanes — this diagnostic
  stays deliberately dyadic and capped.
- No revival of the O(n²) finite-stats pass (the chunked histogram
  replacement at `stats.py:262-265` stays).
- Memory figures are analytic estimates from the measured 6 GB point;
  S2 (b) must measure before landing.

## 6. References

- `hgx_first_scaling.md` §3 S3/§6 (cap design + acceptance),
  `scipy_exact_universe.md` (exact opt-in), `stats.py:237-335`
  (implementation), `graph_scale_watch` (T1 tripwire pattern),
  `graph_rebuild_scaling_probe.md` §7 (re-open trigger discipline).

## 7. Arc addendum — executed 2026-10-19 (completed.md entry 417)

- **S2 decision: option (b)** — cap raised 3000 → 6000 via a new
  `stats.CHAIN_CAP` constant (single source of truth; `max_exact`
  defaults to it). The raise was MEASURED before landing, per the
  proposal's own acceptance: 6,000-root sampled run on the live
  20,044-node universe = **36.1 s wall / 2.9 GB process peak** (the
  analytic ~36 s projection held; dist+pred arrays alone ~1.4 GB).
  Diameter read stays 12 with ties now 311 pairs (167 at the old cap) —
  still explicitly a lower bound.
- **S3 tripwire**: `graph_scale_watch` gains a chains-cap section —
  reads the edge-touched universe from the SQLite store (same SQL as
  the diagnostic), imports `CHAIN_CAP` (no drift), and reports
  `exact` / `SAMPLED` / `SKIP`. Sampled mode is sanctioned degraded
  mode: advisory, exit stays 0. Live: `chains-cap-watch: SAMPLED —
  edge-touched n=20,044 > cap 6,000 (3.3x)`.
- **S1 record**: the crossover history (accepted exact at 1,722; went
  live unrecorded; caught 2026-10-18; policy landed) now lives in the
  `longest_chains` docstring. The `$TMPDIR/features.txt` conflation
  fix was dropped — the register is scratch (operator ruling).
- **S2(c) scheduled exact refresh: not taken** — the on-demand path
  (`exact=True`, ~80 s / ~6 GB) plus the watcher's pointer satisfies
  the intent without new scheduling machinery; revisit if exact numbers
  become a recurring need.
- **Verification**: 11/11 watcher tests (5 new: SAMPLED line, exact
  line, index-only exclusion, SKIP path, constant-drift pin);
  hyper-incidence suite green (the cap-pinning tests pass explicit
  `max_exact` values and are unaffected by the default change); ruff
  check/format clean; live watcher run validated both sections.
