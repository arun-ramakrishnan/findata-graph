---
title: "Compare graph_analytics staleness as timestamps, not strings"
status: executed
filed: "2026-09-29"
executed: "2026-09-29"
completed_md: "316"
area: "helpers/graph/stats.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Compare graph_analytics staleness as timestamps, not strings

**Date:** 2026-09-29 · **Status:** DONE — AWAITING `make qa` ·
**Area:** `helpers/graph/stats.py`, `memory/research.db`

> **Disposition: S1–S3 delivered 2026-09-29.** Only the operator's
> `make qa` box is unticked. The comparison moved to
> `MAX(julianday(col))` on both sides in a new `_staleness_verdict()`
> helper (the SQLite equivalent of the `TRY_CAST` precedent — this
> `conn` is SQLite, where `julianday()` is the normaliser that parses
> both the ISO-T and space-separated shapes and turns NULL/empty into
> NULL, which `MAX` skips). The guard widened per the risk note: a side
> with no parseable stamp prints `⚠ INDETERMINATE`, never `✓ fresh`.
> Verified against the live snapshot: the banner flipped from `⚠ STALE`
> to `✓ fresh` on unchanged data (entities 09-25 04:33 vs analytics
> 09-25 17:59 by julianday). **S2 decision: the 129 legacy ISO rows
> stay untouched** — the cast reads them correctly at both consumers
> (`analytics.py:376`, `stats.py`), no writer emits the shape anymore,
> and a `rebuild_schema.py` migration would churn classified rows for
> zero query-visible benefit. The regression test class
> (`tests/test_graph_stats.py::TestStalenessVerdict`) constructs the
> same-day collision explicitly and is mutation-checked: reverting the
> decision to the raw string compare fails exactly the discriminator
> test, while the cross-day STALE and direction-guard tests stay green
> (they are format-agnostic by design). Frontmatter stays
> `status: proposed` because `check_proposal_lifecycle`
> (helpers/validators/static_checks.py:967-970) requires live proposals
> to read `proposed`, and the archive branch requires a real
> `completed_md` number not yet allocated.

## Motivation

`helpers/graph/stats.py:539-547` decides whether `graph_analytics` is stale by
comparing two SQLite TEXT columns with `>`. They are not in the same format, so
the comparison is lexicographic and currently **inverted**: the live snapshot
reports `⚠ STALE` when the analytics are demonstrably fresh.

Measured against `memory/research.db` on 2026-09-29:

```text
entities.last_updated : '2026-09-25T00:13:27.091137'
analytics.computed_at : '2026-09-25 17:59:26'
repo string compare e>a: True   <- drives the ⚠ STALE banner
real datetime compare : False  <- actual truth
```

The two columns diverge at index 10: `'T'` (84) beats `' '` (32), so the ISO row
wins the comparison regardless of the time of day. The banner is wrong in the
direction that costs work — it tells the operator to run `make recompute-graph`
on data that does not need it.

`entities.last_updated` carries **four** distinct shapes in one column:

| shape | rows | written by |
|---|---|---|
| NULL | 23,937 | never stamped |
| `YYYY-MM-DD HH:MM:SS` | 1,731 | `utc_now()` — the documented convention |
| empty string | 356 | — |
| `YYYY-MM-DDTHH:MM:SS.ffffff` | **129** | pre-Bundle-T1 legacy writes |

`helpers/core/db.py:78-88` already documents this exact hazard ("Bundle T1"):
comparing a `last_updated` value against a `CURRENT_TIMESTAMP`-defaulted column
only works if both use `utc_now()`'s space-separated shape. Every current writer
complies — `helpers/core/parse_newsletter.py:627` and
`helpers/graph/derive_indices.py:297` both call `utc_now()`. The 129 ISO rows are
legacy residue that no writer will rewrite, because those entities are already
classified and the UPDATEs guard on `sector_classification IS NULL`.

The precedent for the fix already exists in the codebase:
`helpers/graph/analytics.py:376` reads the same column as
`TRY_CAST(last_updated AS TIMESTAMP)`. Only the SQLite banner compares raw text.

## Slices

### S1 — cast both sides before comparing

Replace the raw `>` in `stats.py` with a timestamp comparison — `datetime(...)`
on both columns, or `julianday(...)`, mirroring whichever style the surrounding
code already uses. Print the original strings in the banner so a human can still
read the raw values. One condition, one file; no schema or data change.

### S2 — decide the 129 legacy rows explicitly — decided 2026-09-29, leave them

Do **not** rewrite them as a side effect of S1. Either leave them (the cast
handles them correctly) or normalise them in a `rebuild_schema.py` migration, and
say which in the commit. `helpers/maintenance/rebuild_schema.py:162` already
lists `computed_at` among the columns it manages, so it is the natural home if a
data fix is wanted.

**Decision: leave.** Both consumers that compare or read the column for
time semantics cast it (`analytics.py:376`, `stats.py` `_staleness_verdict`
since S1); no writer emits the ISO shape; the rows are already-classified
entities whose UPDATEs guard on `sector_classification IS NULL`, so nothing
will rewrite them organically either. A migration would touch 129 rows with
zero query-visible benefit.

The NULL and empty-string rows are a separate question and out of scope here:
`MAX()` skips NULLs, and 23,937 unstamped entities is a coverage question, not a
comparison bug.

### S3 — pin the same-day collision with a test

The bug only misfires when both stamps land on the **same date**, because the
date prefix decides a lexicographic comparison before index 10 is reached. A
recompute on a later day therefore *accidentally* reports correctly, and any
same-day recompute reproduces the bug. A regression test must construct that
collision explicitly — assert that an ISO `last_updated` and a
space-separated `computed_at` on the same date compare as **fresh**, and that a
genuinely older `computed_at` still reports stale.

## Non-goals

- **No change to the recompute schedule.** Only the comparison is wrong.
- **No bulk rewrite of `entities.last_updated`** unless S2 explicitly chooses it.
- **No change to `graph.duckdb` or the desktop.** The separate finding that
  `graph_analytics` is current — so the desktop needs no DuckDB driver — is
  recorded in `doc/local/evaluations/dbx_assessment.md`.

## Risks

- A `datetime()` cast on a NULL or empty string yields NULL, so the guard
  `if most_recent_entity and most_recent_analytics` must be preserved or
  widened; a NULL result must not be reported as fresh.
- Malformed values would make the cast raise or return NULL where the string
  compare succeeded. Bounded by S2 deciding the legacy rows.
- Low blast radius: this is a reporting banner in a stats path, not a write path.

## Acceptance

- [x] `stats.py` reports `✓ fresh` against the live snapshot, which is fresh
- [x] A genuinely stale snapshot still reports `⚠ STALE` (test-constructed)
- [x] The same-day ISO/space collision is covered by a regression test
      (mutation-checked: the string-compare mutation fails exactly the
      discriminator, `TestStalenessVerdict`)
- [ ] `make qa` clean

## Follows

`helpers/core/db.py:78-88` (Bundle T1, the original hazard note),
`helpers/graph/analytics.py:376` (the existing correct cast),
`doc/local/evaluations/dbx_assessment.md` (why the desktop does not need DuckDB).
