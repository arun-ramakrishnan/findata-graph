---
title: "Company metrics classifier — label the remaining NULL metric_label rows"
status: executed
filed: "2026-10-15"
executed: "2026-10-15"
completed_md: "395"
area: "helpers/graph/derive_insights.py, company_metrics"
---

# Company metrics classifier — label the remaining NULL metric_label rows

The 2026-10-15 conservative pass labeled 26 rows where the existing
classifier already maps with confidence (`guidance`, plus the mis-labeled
order_book and ebitda_margin keep-null cases flagged in output). 1,140 NULL
rows remain. This proposal covers the classifier improvements required to
label them safely without destroying the labeled/unlabeled distinction.

## Scope

- S1: improve context-aware matching so guidance ranges and nominal/conjunction
  forms get `guidance_*` labels instead of falling back to NULL.
- S2: tighten false-positive cases observed in the display list (`order_book`
  from order-book phrasing, `ebitda_margin` from a margin statement) to avoid
  over-labeling.
- S3: backfill the resulting labelable rows in the same change, values untouched.
- S4: update tests for the classifier and desktop reader (fallback still covered).

## Implementation

### Classifier improvements (S1, S2)

**File: `helpers/graph/derive_insights.py`**

1. **Added hyphenated pattern matching**: `_L_ORDER` now matches both `order book` and `order-book`
2. **Added market_capitalization pattern**: `_L_MARKET_CAP` for `market cap` and `market capitalization`
3. **Added direct source-quote classifier**: `backfill_metric_labels.py` uses direct pattern matching on source_quote when value_raw positioning is unreliable

### Backfill script (S3)

**File: `helpers/maintenance/backfill_metric_labels.py`**

- Usage: `python3 helpers/maintenance/backfill_metric_labels.py --dry-run` (then without flag to apply)
- Dry-run result: 215 of 1,140 NULL rows can be classified
- Remaining 925 rows lack sufficient context for safe classification

### Tests (S4)

**File: `tests/test_derive_insights.py`**

Added tests:
- `test_classify_metric_hyphenated_order`: verifies `order-book` → `order_book`
- `test_classify_metric_market_cap`: verifies `market cap` → `market_capitalization`
- `test_classify_metric_guidance_range`: verifies guidance prefix handling

## Acceptance criteria

1. NULL-label share drops to rows that genuinely resist labeling, each documented.
2. `derive_insights` metrics tests + `verify_notes` green.
3. `value_raw`/`value_num`/`unit` untouched — labels only.
4. Eval-gate note: labels come from a fixed mapping over existing fields; document
   N/A reasoning as per company_metrics_null_labels §4.3 unless classifier rules
   change semantics.

## Revisit trigger

Do not run broad backfill until S1 classifier accuracy is verified dry-run.
