---
title: "Label the labelless — backfill metric_label on guidance-style company_metrics rows"
status: deferred
filed: "2026-09-28"
executed: "2026-09-28"
completed_md: "308"
area: "graph"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Label the labelless — backfill metric_label on guidance-style company_metrics rows

<!--
Deferred from the desktop S3 slice (2026-09-28): the Tauri viewer found
guidance-style rows with NULL metric_label and hardened its reader
(`label: Option`, `—` fallback, test-pinned). Whether the WRITER should
label them is a pipeline question — filed here, not fixed there.
-->

**Date:** 2026-09-28 · **Status:** PROPOSED · **Deferred from:** desktop S3
**Area:** helpers/graph/derive_insights.py (metrics writer), company_metrics table

## 1. Motivation

`company_metrics.metric_label` is nullable, and 3,119 rows (4.7%, 476
entities) carry NULL — mostly guidance ranges (`16%`, `8-10%`) with a
unit and numeric value but no label and no period. Every consumer
re-derives "what is this number?" from `value_raw` alone. The desktop
S3 reader proved the rows are otherwise sound (value_num/unit parse);
the gap is writer-side provenance, not corruption.

## 2. Evidence (measured 2026-09-28, this box)

| Probe | Result |
|---|---|
| NULL-label rows corpus-wide | 3,119 / 66,895 (4.7%) |
| Entities affected | 476 distinct |
| CEAT sample | 12 rows, e.g. `16%`/`8-10%` percent, no label, no period |
| Labeled-row shape (control) | `q_revenue`, `value_raw`, `unit`, `period=FY27Q1` — full provenance |
| Desktop impact today | none — reader hardened S3 (`Option` + fallback) |

## 3. Design

One slice: at metrics write time, derive a label for guidance-style
rows (constrained vocabulary — `guidance_*` family, mirroring the `q_*`
quarterly family) plus a period where the source line carries one;
rows that genuinely resist labeling keep NULL explicitly (no
placeholder strings — NULL must keep meaning "unlabeled", which the
desktop contract now relies on). Backfill existing NULLs in the same
change or document why not. Alternatives: (a) NOT NULL + write-time
default — rejected, destroys the labeled/unlabeled distinction;
(b) leave forever — viable; cost is borne by every reader, currently
exactly one hardened reader.

## 4. Acceptance criteria & shakedown

1. NULL-label share drops to rows that resist labeling, each with a
   documented reason class; guidance rows carry `guidance_*` labels.
2. `derive_insights` metrics tests + `verify_notes` green; no
   query-visible change to labeled rows (value_raw/value_num/units
   untouched — labels only).
3. Eval-gate: only if extractor rules change to derive the labels —
   if labels come from a fixed mapping over existing fields, N/A with
   the reasoning recorded.
4. Desktop S3 test (`entity_metrics_bundle`) re-run green against the
   backfilled DB (fallback path still covered by design, not by data).

## 5. Risks

- **Label vocabulary drift** — mitigation: closed `guidance_*` family
  reviewed like the `q_*` family, not free text.
- **Backfill churn on 3k rows** — mitigation: labels-only UPDATE,
  values untouched; snapshot-restore stays the escape hatch.

## 6. Non-goals

Value re-parsing, unit normalization, period inference beyond what the
source line carries, any desktop change (already robust).

## 7. References

- `doc/improvements/archive/ui/findata_graph_desktop.md` — S3 evidence row (origin)
- `helpers/graph/derive_insights.py` — metrics writer (owner of the fix)
- Desktop reader: `desktop/src-tauri/core/src/lib.rs::entity_metrics`, `MetricsPanel.vue`

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-28 | NULL-label census over company_metrics | 3,119 / 66,895 rows, 476 entities | guidance ranges, value+unit sound |
| 2026-09-28 | CEAT probe | 12 NULL-label rows (`16%`, `8-10%`) | desktop S3 finding #1 |

---
**Follows:** doc/improvements/archive/ui/findata_graph_desktop.md (S3 data findings, completed.md #308)
