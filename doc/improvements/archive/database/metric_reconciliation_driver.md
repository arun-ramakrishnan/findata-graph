---
title: "Post-parse metric reconciliation driver (newsletter ↔ NSE-XBRL ground truth)"
status: executed
filed: "2026-11-23"
executed: "2026-11-23"
completed_md: "564"
area: helpers/maintenance
---

# Post-parse metric reconciliation driver (newsletter ↔ NSE-XBRL ground truth)

## Motivation

`xchange_filings.md` §10.3/§10.4 step 3 names the one unbuilt slice of the
XBRL filings lane: when `derive_insights` extracts a magnitude (revenue,
PAT, EBITDA) from a newsletter, nothing checks it against the filing
ground truth that already sits in `company_metrics` (63,390 nse-xbrl
rows, canonical periods, `consolidated` flag). A misparsed magnitude
(e.g. a "₹3.1 lakh crore" compound read as 3.1 crore) is stored silently
and served as fact.

Measured landscape (2026-11-23 live store):

| side | rows | period shape | labels | units |
|---|---|---|---|---|
| newsletter (`derive:metrics:`) | 4,170 | 89% NULL; rest messy (`Q3 FY26`, `FY2025`, `December 2025`) | best-effort words (`revenue` 251, `guidance` 664, NULL 2,089) | crore 584, lakh 53, percent 2,902, … |
| filing (`properties->>'via'='nse-xbrl'`) | 63,390 | canonical `FY27Q1` | `q_revenue`/`q_pat`/`q_ebitda`… | crore_inr, inr_per_share, ratio |

398 newsletter entities have filing rows under exact name match.

## Constraints discovered

- `_METRIC_CONTENT_COLS` (derive_insights.py:1902) includes `properties`,
  and applies are prefix-scoped stable replaces — a reconciliation
  annotation written into a metric row's `properties` would change the
  content hash and be rewritten on the next apply. Verdicts must ride a
  **sidecar table keyed on metric `id`** (ids survive applies).
- No parser changes for v1 (spec: content-agnostic parser, post-parse
  pass only). The driver never mutates `company_metrics`.
- Standalone vs consolidated: Reliance FY27Q1 q_revenue = 166,013
  (standalone) vs 311,850 (consolidated) crore — both legal. Prose rarely
  says which; v1 compares against both and picks the closer, preferring
  consolidated on ties within threshold.

## Design

New `helpers/maintenance/reconcile_newsletter_metrics.py`
(dry-run by default, `--apply` writes, `--report PATH`, `--threshold`,
default 0.10):

1. **Match ladder** (each rung miss → verdict `unmatched(reason)`):
   entity exact name → explicit quarter only (`Q3 FY26`/`Q3FY26` →
   `FY26Q3`; year-only `FY26` and date-ish `December 2025` refuse to
   anchor: `period_ambiguous`) → label map (`revenue`/`turnover`/
   `net sales`/`topline` → `q_revenue`; `profit`/`pat`/`net profit` →
   `q_pat`; `ebitda` → `q_ebitda`; NULL/unmapped labels never match) →
   unit map (`crore` ×1, `lakh` ×0.01 crore; `bn_usd`/`percent`/… skip:
   `unit_unmapped`) → filing candidates (entity + label + period) →
   nearest |Δ| ≤ threshold wins (`anchored`), else `suspect`.
2. **Sidecar** `metric_reconciliation` (id = newsletter metric id, PK):
   `filing_id, verdict, filing_label, filing_period, filing_value_num,
   filing_unit, filing_source_ref, delta_pct, consolidated, reason,
   reconciled_at`. Apply = prefix-stable replace keyed on the newsletter
   metric id set (stale ids pruned). Schema added to the canonical
   DuckDB store DDL + `make schema-dump`.
3. **Report**: counts per verdict/reason + suspect table (entity,
   edition, newsletter `value_raw`, filing value, Δ%) — markdown.
4. Idempotent: rerun with no source drift → same verdicts, report only.

## Rejected

- LLM re-extraction / parser changes: out of scope for v1 by spec.
- Storing verdicts in `properties`: wiped by the next apply (constraint
  above).
- Anchoring year-only periods to a guessed quarter: silently mis-anchors
  (the failure mode this driver exists to catch).

## Acceptance

- Targeted tests pin the ladder end-to-end: anchored (consolidated
  nearest-pick and standalone-closer), suspect-beyond-threshold, every
  unmatched reason, sidecar idempotency + stale pruning, and the
  report's section rendering.
- Dry-run on the live store produces a report with the corpus's true
  verdict distribution. Live census at filing time (2026-11-23): 89% of
  newsletter metrics carry no period and every explicit-quarter row is
  a growth/percent figure, so the correct live result is ZERO anchored
  — the unit and period gates must refuse them, not mis-anchor. The
  Reliance FY27Q1 anchored shape first appears from the next edition
  parsed after an exchange-refresh (the §10.3 cycle ordering); the
  sidecar baseline written now makes that pairing visible as a diff.
- `company_metrics` byte-identical after an `--apply` (zero writes).

## Execution & findings (2026-11-23)

- **Driver landed** at `helpers/maintenance/reconcile_newsletter_metrics.py`
  (default dry-run; `--apply` full-replaces the sidecar in one txn;
  `--report PATH` renders the markdown census; `--threshold` override).
  Filing-row selection uses `properties LIKE '%nse-xbrl%'` — portable
  across sqlite fixtures and the VARIANT live store alike.
- **30 targeted tests** (`tests/test_reconcile_newsletter_metrics.py`,
  sqlite `:memory:` fixtures): anchored via consolidated nearest-pick,
  anchored via standalone-closer, suspect-beyond-threshold, all six
  unmatched reasons, apply idempotency + stale pruning,
  `company_metrics` read-only proof, report section rendering.
- **Live baseline applied**: 4,170 verdicts, all `unmatched` —
  `period_missing` 3,717 / `period_ambiguous` 337 / `label_missing` 69
  / `label_unmapped` 35 / `unit_unmapped` 10 / `no_filing_rows` 2.
  The three (entity, label, period) triples that DO exist on both
  sides (Mankind Pharma FY26Q4, Netweb FY26Q3, TCS FY27Q1) are
  growth-percent rows on the newsletter side — the unit gate refuses
  them correctly. Rerun `--apply` idempotent (4,170 rows, no dupes).
- **Acceptance amendment (pre-execution, honest)**: the drafted
  "≥1 anchored in the live dry-run" was unreachable on the current
  corpus — 89% of newsletter metrics carry no period and every
  explicit-quarter row is a growth figure. Anchored pairs require the
  §10.3 cycle ordering (filings refreshed BEFORE the next parse); the
  sidecar baseline written now turns that first post-refresh edition
  into a visible diff against the 4,170 unmatched rows.
- **Test-fixture gotchas**: 3.1 lakh × 0.01 crore needs
  `pytest.approx`; the nearest-candidate rule means a suspect's
  `filing_id` can be the standalone row (99.88% off) even when a
  consolidated row also exists (99.94% off) — verdict unaffected.
