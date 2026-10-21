---
title: "Hyperedge density data-quality — degenerate members, the D10 gate restatement, and the event n-ary gap"
status: executed
filed: "2026-10-18"
area: "helpers/graph"
executed: "2026-10-19"
completed_md: "420"
---

# Hyperedge density data-quality — degenerate members, the D10 gate restatement, and the event n-ary gap

**Date:** 2026-10-18 · **Status:** PROPOSED

## 1. TL;DR

The D10 prediction/motifs/dynamics lane is gated on hyperedge
*incidence density*, and the density has not merely failed to improve —
it was **never mostly there**. 74.8% of live hyperedges are degenerate
(k≤2: 157 singletons, 1,185 dyads); only 451 of 1,793 are genuinely
higher-order. The event family — the largest, 1,252 edges — contains
**zero** hyperedges with k≥3. The gate as written (group k=3.8, jv
k=2.0, `hgx_first_scaling.md:158`) watches two of the least-dense
families while the real structural ceiling is that the event pipeline
emits dyads because it has no participant-role store. This arc restates
the D10 gate as a per-family density table with measured baselines,
audits the degenerate classes, and lands the `event_participants`
n-ary store that converts event dyads into real higher-order members —
the same store the person spine needs for role links.

## 2. Evidence (measured 2026-10-18, live)

**Member-count distribution** (1,793 hyperedges / 9,979 incidences;
mean 5.57 members/edge):

```text
k= 1  →   157 hyperedges   (singletons — no co-membership signal)
k= 2  → 1,185 hyperedges   (dyads — hyperedge in name only)
k>=3  →   451 hyperedges   (true higher-order: 25.2%)
```

**Per family** (edges / k1 / k2 / k≥3):

| family | edges | k=1 | k=2 | k≥3 |
|---|---|---|---|---|
| event | 1,252 | 106 | 1,146 | **0** |
| edition | 222 | 1 | 0 | 221 |
| industry | 119 | 22 | 12 | 85 |
| sub_sector | 108 | 17 | 12 | 79 |
| sector | 42 | 0 | 1 | 41 |
| country | 21 | 11 | 3 | 7 |
| theme | 12 | 0 | 0 | 12 |
| group | 9 | 0 | 5 | 4 |
| jv | 6 | 0 | 6 | **0** |

**The D10 gate vs live** — gate recorded "group 8/k=3.8, jv 6/k=2.0";
live: group 9 edges k=3.33, jv 6 edges k=2.00. Group density *fell*.

**The register conflation** — `$TMPDIR/features.txt:8` states the D10
reopen condition as "edge-touched n grows ~10x above 1,722" (live:
20,044 = 11.6×, so it reads as fired). The 1,722 baseline belongs to
the S3 `longest_chains` cap note (`hgx_first_scaling.md:151`); the D10
gate is density, not node count. Two gates, one fired, one not — the
register merged them.

**Events have no participant store** — `events` holds 1,477 rows with
no participant-role link table; the n-ary event pattern was an explicit
non-goal of arc #234. `derive_events` computes counterparties (110/110 FK
resolution recorded at #234) but the hyperedge writer flattens each
event to a (company, counterparty) pair — hence 100% k≤2.

**s-walk caveat stands** — projections flatten hyperedges to cliques
(the 41-member country edge acts as a 41-clique,
`hgx_first_scaling.md` §5); density work must land in the incidence
store, not the projection.

## 3. Slices

- **S1 Singleton audit** — are the 157 k=1 hyperedges valid members
  (a one-company edition/theme is possible) or derive noise? Per-family
  census with the emitting lane named per the #234 writer/reader
  obligations table; output is an absorb/drop/keep verdict per family,
  not a blanket rule.
- **S2 `event_participants` store** — `(event_id, participant, role,
  source_ref)` n-ary table fed by `derive_events` counterparty +
  speaker roles (role vocabulary seeded from the existing
  acquisition/jv/guidance/management_change event types); event
  hyperedges gain k≥3 members on re-derive. Companion:
  `person_ontology_spine.md` S4 — same store, one write path.
- **S3 D10 gate restatement** — replace the two-family gate with the
  per-family density table above as the recorded baseline; reopen D10
  per-family (edition/industry/sub_sector/sector/theme are the real
  higher-order lanes today), not globally.
- **S4 Register hygiene** — correct the `features.txt` D10 condition
  (density, not n), annotate `hgx_first_scaling.md` §5 with the
  measured baseline table.

## 4. Acceptance

- **Eval-gate bullet** if S1 absorption changes hierarchy semantics:
  frozen question set, dry-run vs apply.
- Hyper parity pins hold: `h_edge`/`h_incidence` row counts == star
  store on every rebuild path.
- S2 insert idempotent per (event, participant, role); derive_events
  dry-run default; re-derive raises event-family k≥3 count above zero
  (measured acceptance).
- S3: the gate table lands with the live numbers inline; a future
  reader can re-measure the table with one query.

## 5. Risks & non-goals

- Absorbing singletons destroys provenance for genuinely one-member
  groups — S1 must verdict per family with the writer named.
- No Onager/igraph revival; no projection-side motif work (s-walk
  caveat); no prediction lane in this arc — D10 itself stays gated
  until S2 re-derives and S3's baselines move.
- Event role vocabulary is deliberately minimal (4 canonical types +
  counterparty/speaker roles); free-text roles are out of scope.

## 6. References

- `hgx_first_scaling.md` §5 (D10 gate, s-walk caveat),
  `hypergraph_incidence_hyx.md` §338 (incidence density ceiling),
  `ontology_convention_stack.md` §5 (n-ary non-goal),
  `person_ontology_spine.md` (companion S4),
  `longest_chains_cap_policy.md` (the 1,722 conflation),
  completed.md #233 (star store), #150.

## 7. Execution results (2026-10-19)

**Status: EXECUTED** — completed.md #420. S2 was landed by the
companion arc (`person_ontology_spine.md` S4, schema v16); this arc
executed S1, S3 and S4.

### S1 — singleton audit + event-family convergence (applied live)

Verdict is PER FAMILY, not a blanket rule:

| family (k=1 pre-prune) | verdict | why |
|---|---|---|
| event 106 | **DROP** | labels `type:id` — 115 pointed at deleted events (102 k=1, 13 k=2); 4 more alive but <2 participants. Derive noise; the additive lane owned no convergence. |
| industry 22 / sub_sector 17 / country 11 / org_membership 4 / edition 1 | **KEEP** | valid one-member membership facts; additive posture is correct for non-event families. |

`prune_stale_event_edges()` (`derive_hyperedges.py`, dry-run default)
now converges the event family to `event_participants`-derived truth on
every derive; dry-run reported 119 stale of 1,252, `--apply` dropped
them. Post-prune live store: **1,708 hyperedges / 10,134 incidences /
mean 5.93; k=1 55, k=2 1,173, k≥3 478 (28.0%)**; the event family is
1,133 edges, ALL k=2, zero singletons. Tests:
`TestEventFamilyConvergence` (dry-run no-write, apply drops stale +
sub-threshold with incidence cascade, idempotent second run).

### S3 — D10 gate restatement

`pending.md` "HGX D10" carries the per-family density table (the
post-prune numbers above, inline) with reopen triggers per family:
edition/theme/sector (and industry/sub_sector/org_membership at
75–85% k≥3) are prediction-grade NOW — the D10 blocker is the engine
decision, not density; event/jv reopen when participant data arrives
(speaker→event joins measured 0/1,477). `hgx_first_scaling.md` §5
annotated with a pointer. One-query re-measure recorded in pending.md.

### S4 — register hygiene

The register's "edge-touched n ~10× above 1,722" D10 condition was a
merge error with the longest_chains S3 cap baseline (n is a size
metric, not density); correction recorded in the pending.md
restatement. `features.txt` is operator-owned scratch (execution-order
table) — deliberately not edited.

### Non-goal honored

No projection-side motif work; the s-walk caveat stands. D10 itself
remains gated on the engine decision recorded in pending.md.
