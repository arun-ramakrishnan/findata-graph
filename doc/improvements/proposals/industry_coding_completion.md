---
title: "Industry coding completion — scorer v2, source-noise repair, per-company coding"
status: proposed
filed: "2026-09-19"
executed: null
completed_md: null
area: "helpers/misc (seed scorer), helpers/graph (ingestion/source edges), findata/Misc worklists, note stamping"
---

# Industry coding completion — scorer v2, source-noise repair, per-company coding

**Date:** 2026-09-19 · **Status:** PROPOSED ·
**Area:** helpers/misc/seed_nic2008.py (suggest lane), source industry-edge
ingestion, note stamping + verify_notes

**Follows:** `nic2008_seed_table.md` (completed.md #246) — the label-level
coding lane is exhausted (85/117 labels coded, 101 active lanes, 163 gate
pins); 30 labels sit parked, 2 no-signal, and a handful of source labels
are provably wrong. This arc finishes the industry-coding story with the
three residual mechanisms, in dependency order.

## 1. Scorer v2 — measured, not vibes (S1)

The lexical scorer (token-F1 0.6 + containment 0.2 + difflib 0.2) has a
difflib long tail: education `85499` surfaced for Credit Services /
Telecom Services; two quick fixes tried live in #246 (n.e.c. demotion
0.8x, member-company-name vote) were REVERTED — reshuffling noise, not
removing it.

New asset: the **85 operator-attested labels are a ground-truth
benchmark set**. S1 builds the harness first (v2 vs attested answers,
top-3 precision/recall), then gates the difflib arm behind a
token-overlap precondition, and lands ONLY on strict improvement.
Post-#246 the queue refills on every derive cycle — the parked pool is
the scorecard.

## 2. Source-noise repair — mislabeled industry edges (S2)

Known-bad labels (measured 2026-09-19): **Gold** = sole member is a water
firm; **Confectioners** = sugar refining + cement + chemicals members.
These can never be coded honestly at label level. S2 is discovery-first:
trace where industry hyperedges are assigned in the ingestion path,
sample ~30 labels against member business descriptions to size the
mislabel rate, then fix at ingestion or add an override/exclusion map.
Repair may reclassify members into EXISTING good labels, shrinking the
per-company set before S3 runs.

## 3. Per-company frontmatter coding — the irreducible residue (S3)

Labels spanning 6+ divisions (Conglomerates: 3M/BEML/SRF/Thermax/Tube
Investments; A&D's electronics cluster) cannot be honestly label-coded.
S3 extends note stamping to emit per-member `industry_code` for the
flagged labels, with a verify_notes rule for per-company overrides
(industry_carriers metric semantics). Writer-vault coordination is
operator-paced by design.

## 4. Cross-cutting

- Worklist export gains the in-table (`*`) marker alongside
  vintage_counts — MCA "post-2008" CINs are NOT reliable NIC-2008
  natives (Delhivery 63090, InfoBeans 72200); table membership is the
  only hard signal (#246 finding).
- Non-goals: no model/API in ranking (pure-lexical law holds), no
  schema changes to interchange files, writer-owned vault touched only
  via the existing stamping surface in S3.

## 5. Gates

- S1: benchmark harness report (precision/recall before -> after) +
  51 seed tests; suggestions lane stays maint-unwired.
- S2: ingestion fix verified by re-derivation (worklist delta) +
  integrity check.
- S3: verify_notes migration metric + writer confirmation.
- Full `make qa` at arc close; eval-gate bullets for any
  query-visible crosswalk change (ontology_governance S2 rule).
