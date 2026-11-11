---
title: "company_metrics grain rule (scope-keyed), metric gate shape, and the NL->SQL question bank"
status: executed
filed: 2026-11-11
executed: "2026-11-11"
completed_md: '506'
area: database
---

# company_metrics grain rule, metric gate shape, and the NL->SQL question bank

**Date:** 2026-11-11 · **Status:** EXECUTED (completed.md #506) ·
**Area:** company_metrics store semantics / ontology eval gate / answerability bank

## 1. Motivation

`company_metrics` has no row identity. 79,396 rows collapse to 49,826
distinct `(entity, metric_label, period)` keys; the table carries a surrogate
`id INTEGER PRIMARY KEY` and two plain indexes
(`helpers/maintenance/migrate_to_graph_edges.py:284-299`) — no natural-key
constraint, so duplicates are silently admitted. Measured impact: naive
`SUM(value_num)` over `q_revenue` = 12,680,960.89 vs 7,519,934.98 under a
per-key rule — **1.69x inflation** (`q_ebitda` 1.68x, `q_total_income` 1.69x,
`q_pbt` 1.66x).

The trial (`bench_data/metric_grain/`, 2026-11-11; nine read-only DuckDB
measurement scripts + JSON artifacts + ruling menu) proved the conflicts are
**not duplicates**:

- **0 of 63,390** document-level keys carry two different values — every
  source document is internally consistent.
- **99.7% of the 18,956 conflicting keys are consolidated-vs-standalone
  scope** (taxonomy: scope_only 18,846 / window+scope 43 / vintage_only 21).
  The discriminator already exists: `properties.consolidated`, written
  doc-level clean by `helpers/maintenance/ingest_nse_xbrl.py:143`
  (2,973 docs, 0 mixed).
- "Latest filing wins" is not principled: filing-gap median is 44 seconds and
  the standalone document is newer in 5,569 keys — it silently picks a scope,
  a different one per key.
- Accounting-identity oracle (`q_ebitda >= q_pbt`): naive R0 breaks 4,919 of
  5,442 pairs; scope-qualified R9 breaks 1,517/1,544 (98.25%); each scope
  alone is 98.3% coherent — two different truths, not one wrong one.

Two downstream gaps block the question bank (Path A of the Auto Ontology
assessment, `doc/local/evaluations/auto_ontology_assessment.md` §8):

- The frozen ontology gate cannot see any of this — none of its 11 shapes
  touches `company_metrics` (`helpers/misc/ontology_eval_gate.py:92-311`).
- Pins are numbers and numbers depend on the grain. The probe's `sql` shape
  (landed 2026-11-11) is the entry point, but there is no bank to seed.

## 2. Operator ruling (received 2026-11-11, session m01000)

- **A — the key: A1 (ruled).** Grain = `(entity, metric_label, period,
  consolidated)`, `consolidated` NULL for populations that do not carry it
  (yfinance, derive_internal). **Both scopes are legitimate, distinct facts
  and both survive.**
- **B — residual arbitration: B1 (default, carried here).** Within a scope,
  latest filing wins — needed only for the residual **49** same-scope
  restatements (32 material; e.g. KRBL `q_basic_eps` 0.0 -> 6.79).
- **C — derive_internal labels: C1 for the store, C3 for the bank (default,
  carried here).** `metric_label` stays uncontrolled in the store;
  `derive_internal` is excluded from the initial bank until faceted (its 46
  conflicting keys are multi-facet facts — `guidance` 16, `capacity` 11,
  `growth` 8 — e.g. Adani Power `capacity` FY25 carries 6 values under one
  `source_ref`).
- **D — bank shape: D1 (follows from A1).** The bank exposes one series per
  `(label, scope)`; where both scopes exist a pin is a value per scope.

Full decision surface: `bench_data/metric_grain/menu.md` + `rulings.json`
(decisions recorded there the same day).

## 3. Change

### 3.1 Grain rule — read-time certification, no row destruction

The store stays append-only (every row keeps `agent_id` / `source_ref` /
`source_tier` provenance). The grain is enforced by a **certified view**, not
by rewriting history:

- Promote `consolidated` from `properties` JSON to a first-class nullable
  column on `company_metrics` (backfill from `properties.consolidated`;
  NULL where absent). Ingest (`ingest_nse_xbrl.py`) writes it directly.
- Certified view `company_metrics_ruled` (name TBD): one row per
  `(entity, metric_label, period, consolidated)` — latest filing within
  scope (B1); both scopes kept (A1).
- Integrity check: new grain audit in
  `helpers/misc/database_integrity_check.py` (`check_company_metrics` today
  checks totals/orphans/properties only) reporting key violations under the
  ruled key — advisory count, not a gate.

Rejected alternative: write-time dedup (upsert on ingest). It would destroy
provenance rows the repo's posture keeps (`doc/design/ontology.md:17`
"conventions, not runtimes"; every table carries agent/source provenance), and
the 49 restatements show "latest" is a query-time arbitration, not an ingest
fact.

### 3.2 Metric gate shape (lands first, with tests)

Per `doc/procedures/ontology-gate.md` — "New SHAPES land in the gate runner +
tests first":

- New shape `metric_value` in `helpers/misc/ontology_eval_gate.py`
  (`_SHAPES` + `_answer_metric_value`): params `(entity, metric_label,
  period, scope)`; answer = the ruled value from the certified view.
- Pins for a seed set of metric questions added to the frozen question set
  (`helpers/misc/ontology_questions.json`, v6 -> v7).
- The probe (`helpers/misc/answerability_probe.py`) gains the same shape over
  the Parquet snapshot; `--parity` extends to it.

### 3.3 The question bank (git-tracked)

- New file `helpers/misc/answerability_questions.json` — the permanent,
  git-tracked home (sibling of `ontology_questions.json`). Entry shape:
  `{id, question, intent, shape: "sql", params: {sql}, expected, grain}` —
  `expected` is the pin; for metric questions it is a value per scope (D1).
- Seeded with 10-15 NL questions across the probe's intents (identity,
  relations, nary, control, events, plus 3-5 metric questions on
  `q_revenue`/`q_ebitda`/`q_pbt` per scope).
- `make answerability-probe` runs the bank alongside the frozen set;
  `--fail-on drifted` is the opt-in CI gate for it.

### 3.4 Homes (per operator instruction, m01000)

- **Trial evidence** (`measure_*.py`, `*.json`, `menu.md`, `rulings.json`):
  stays in `bench_data/metric_grain/` — durable and gitignored by design
  (AGENTS.md: `bench_data/<topic>/` is the past-trial recovery bank). This
  proposal carries the decisive numbers, so git history holds the conclusions
  and `bench_data/` holds the reproducible evidence.
- **Question bank**: `helpers/misc/answerability_questions.json` (§3.3).
- **Ruling record**: this proposal §2 + `rulings.json` decisions.

## 4. Eval gate & acceptance

- **Eval-gate bullet (house rule, `README.md` 2026-08-21):**
  `ontology_eval_gate.py` over the frozen question set (v7, now including
  `metric_value` pins) between dry-run and canonical apply — zero drift on
  non-metric shapes; metric pins answered exactly.
- `answerability_probe --parity` -> 0 mismatches, live vs snapshot, including
  the new shape.
- Bank at seed time: every entry `answered` or `negative`; zero `drifted`,
  zero `errored` (`--fail-on drifted` exit 0).
- Targeted tests only (gate shape tests, probe shape tests, bank fixture
  tests); full gates remain operator-owned.

## 5. Risks

- **Query-visible change is the point**: any consumer of raw
  `SUM(company_metrics.value_num)` sees 1.69x inflation until it migrates to
  the certified view. Mitigation: view + integrity audit + this proposal is
  the migration notice; the gate shape pins the ruled values.
- NULL keys (3,550 NULL `period`, 1,991 NULL `metric_label`) sit outside the
  key; the bank never pins them; the view keeps them (unkeyed rows pass
  through, flagged by the audit).
- SQLite `UNIQUE` treats NULLs as distinct — if a constraint is ever added to
  the base table it must be a partial index `WHERE` the key is complete; the
  view makes that optional.
- `derive_internal` exclusion (C3) leaves 3,982 rows outside the bank; a
  facet vocabulary is the follow-on (not this proposal).
- The 49 restatements make "latest filing" a real arbitration: the view must
  use filing timestamp, not `created_at` (differs in 24 groups) or
  `as_of_edition` (present on only 3,982 rows).

## 6. Reproducing the trial

`bench_data/metric_grain/` — nine `measure_*.py` scripts (read-only DuckDB
over `snapshots/parquet/sqlite/company_metrics.parquet`), one JSON artifact
each; `menu.md` §8 lists the commands.
