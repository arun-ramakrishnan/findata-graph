---
title: Temporal Analytics — `REPORT=temporal`
status: executed
filed: '2026-08-25'
executed: '2026-08-25'
completed_md: '150'
area: helpers/graph/analytics.py` (one new report function
---

# Proposal: Temporal Analytics — `REPORT=temporal`

**Status:** EXECUTED (2026-08-25) — all four tables landed, 24 tests
green (7 new), API composite shape wired, live smoke 0.77s; see §9.
**Date:** 2026-08-25 (executed same day)
**Author:** Agent analysis (user-directed); all shapes verified live against
`snapshots/parquet` built 2026-08-23 (generation 25500, schema_version 11).
**Scope:** `helpers/graph/analytics.py` (one new report function + REPORTS
tuple + dispatch), `tests/test_analytics.py` (fixture-based SQL pins),
`doc/improvements/pending.md` (close the C3 entry), Makefile help text if
the analytics line mentions report names. No SQLite schema change, no note
writes, no new dependencies; read-only in-memory DuckDB over parquet.
**Builds on:** `tech_avenues.txt` C3 (optional leftover), #134/#136 (edition
stems joinable), `analytics.py` A3 report framework, D7 temporal spine
(`derive_events.py`).

---

## 1. TL;DR

Add a sixth read-only report — `make analytics REPORT=temporal` — that
answers "how does the corpus behave over time" with four tables:

1. **Chatter volume by quarter** — quotes per `as_of_edition`, dated by the
   edition entity's ingest date, binned by quarter.
2. **Coverage trend per series** — quotes + events per edition across the
   series timeline, flagging thin editions.
3. **Staleness curve by sector** — days since `last_updated` for company
   entities, distribution per sector (p50/p90/max + stale counts).
4. **Events timeline** — the 349-event D7 spine by `event_type` × year.

All inputs verified present and non-degenerate in the live snapshot (§3).
Est. ~150 lines in `analytics.py` + ~60 lines of tests. No pipeline
changes; refreshes for free on every `make snapshot`.

## 2. Background

`tech_avenues.txt` left C3 as an optional leftover ("temporal graph
analytics, MEDIUM"); pending.md re-ranked it 2026-08-19 as "whenever
wanted" and notes the unlock: #134/#136 made `quotes.as_of_edition`
joinable canonical stems (was verbatim titles, 99/2,564 joinable; now
2,564/2,564 sourced, 71 distinct editions). The A3 framework
(`helpers/graph/analytics.py`) already reads the parquet snapshot with
in-memory DuckDB and renders markdown/JSON; `REPORT=temporal` slots in as
report #6 next to summary / edge-growth / sector-growth / top-entities /
coverage.

## 3. Inputs — verified live (2026-08-23 snapshot)

| Parquet | Shape | Temporal axis |
|---|---|---|
| `sqlite/quotes.parquet` | 2,564 rows; `as_of_edition` 71 distinct stems, **0 null/empty**; `created_at` single-batch (2026-08-21, useless as an axis) | `as_of_edition` → join `entities` |
| `sqlite/entities.parquet` | 1,209 entities; `entity_type` ∈ {company 1,065, edition 108, sub_sector 78, sector 42, theme 12, super_sector 9}; cols include `created_at`, `last_updated`, `sector_classification` | `created_at` = edition first-ingest date; `last_updated` = freshness axis |
| `sqlite/events.parquet` | 349 rows; `event_date` spans 2020-01-01 → 2027-07-01 (guidance forward-dates), 4 `event_type`s; `date_precision`, `as_of_edition`, `source_quote` also present | `event_date` calendar time |
| `sqlite/graph_edges.parquet` | `created_at` 2026-07-17 → 2026-08-21 | derivation history only — excluded (that's what `edge-growth` already reports; the note there says "ingest years, not event years") |

**Join path for table 1/2** (the piece #136 unlocked):

```
quotes.as_of_edition  ──(stem == normalized_name?)──  entities
     WHERE entities.entity_type = 'edition'
```

Edition entities carry `normalized_name` (the stem) and `created_at`
(first ingest). Verified live: `v_edition` (DuckDB side) also exists but
has only {id, name} — the SQLite `entities.parquet` is the right source
because it has the dates. Fallback when a stem has no edition entity:
report it in the note line as unmatched (expected small; #136 left
2,564/2,564 sourced so in practice ~0).

## 4. The four tables (exact SQL sketch)

### 4.1 Chatter volume by quarter

```sql
WITH ed AS (
  SELECT normalized_name AS stem, created_at
  FROM read_parquet($entities) WHERE entity_type = 'edition'
)
SELECT year(TRY_CAST(e.created_at AS TIMESTAMP)) || '-Q'
       || quarter(TRY_CAST(e.created_at AS TIMESTAMP))          AS quarter,
       count(DISTINCT q.as_of_edition)                         AS editions,
       count(*)                                               AS quotes
FROM read_parquet($quotes) q JOIN ed e ON q.as_of_edition = e.stem
GROUP BY 1 ORDER BY 1
```

Headers: `quarter | editions | quotes`. Note line: unmatched-stem count.

### 4.2 Coverage trend per series

Per edition (chronological by edition ingest): quotes, events, and a thin
flag.

```sql
WITH ed AS (…as above…),
 agg AS (
  SELECT q.as_of_edition AS stem, count(*) AS quotes FROM quotes q GROUP BY 1
  UNION ALL SELECT as_of_edition, count(*) FROM events GROUP BY 1
)
SELECT e.created_at::DATE AS ingested, e.stem, quotes, events
FROM ed e LEFT JOIN agg …  -- pivot quotes/events into columns
ORDER BY ingested
```

Headers: `ingested | edition | quotes | events`. Thin = quotes + events
< 10 → mark `*`. Cap: 108 editions — printable; if it ever exceeds ~150
rows, add `--limit` later (not now).

### 4.3 Staleness curve by sector

```sql
SELECT sector_classification AS sector, count(*) AS companies,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY days) AS p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY days) AS p90,
       max(days) AS max_days,
       count(*) FILTER (days > 30) AS stale_gt_30d
FROM (
  SELECT sector_classification,
         date_diff('day', TRY_CAST(last_updated AS TIMESTAMP), now()) AS days
  FROM read_parquet($entities) WHERE entity_type = 'company'
)
GROUP BY 1 ORDER BY stale_gt_30d DESC, p90 DESC
```

Headers: `sector | companies | p50_days | p90_days | max_days | stale>30d`.
`now()` is report-run time (fine: staleness is relative to reading).

### 4.4 Events timeline

```sql
SELECT strftime('%Y', TRY_CAST(event_date AS DATE)) AS yr,
       event_type, count(*) AS n
FROM read_parquet($events)
GROUP BY 1, 2 ORDER BY yr, event_type
```

Headers: `year | event_type | events`. Note: event_date spans 2020→2027 —
future years are guidance forward-dates (D7 by design), the note line says
so rather than filtering them out.

## 5. Design decisions

- **One report, four tables? No — four `Report` objects**, one printed per
  section with the existing `render_markdown`. The Report dataclass is
  single-table by construction; `main()` already prints one report per
  invocation. Options considered:
  (a) single `temporal` report printing 4 sub-tables — needs render changes;
  (b) four separate reports `temporal-*` — bloats REPORTS and Makefile;
  (c) **`temporal` returns a composite** — violates the dataclass shape.
  Chosen: (a-lite) — `temporal` fetches all four, `render_markdown` handles
  a `Report` whose `rows` carry a section separator? No — **cleanest is
  option (b) collapsed into one dispatch**: `_temporal` returns a
  `list[Report]` and `main()` prints them sequentially with blank lines.
  This extends the framework minimally: `fetch()` returns `Report | list[Report]`,
  `render_markdown` stays untouched (called per report). Tests pin both
  shapes. (If a future report needs composites, the pattern is already
  proven.)
- **Edition dating via entity `created_at`, not git add-date.** pending.md
  said "git add-dates"; entity first-ingest is the faithful proxy already
  in the snapshot, avoids shelling to git inside analytics, and keeps the
  report reproducible from parquet alone. Documented in the note line.
- **`created_at` on quotes/events deliberately unused** — single-batch
  timestamps from the last re-derivation (2026-08-21), not provenance of
  when the chatter happened. The edition stem is the real time key.
- **No membership-edge tables** — `_MEMBERSHIP_TYPES` exclusion doesn't
  apply here (no edge tables used at all), but the module docstring note
  ("ingest dates ≠ event dates") is respected by construction.
- **Zero new deps; no Note-writing.** Read-only parquet + in-memory
  DuckDB; passes the note-content permission rule trivially.

## 6. Files touched

| File | Change |
|---|---|
| `helpers/graph/analytics.py` | `_temporal(root, con) -> list[Report]` (~90 lines with SQL above); add `"temporal"` to `REPORTS`; `_FETCHERS` entry; `fetch()` return type widens to `Report \| list[Report]`; `main()` loops over list results |
| `tests/test_analytics.py` | Fixture snapshot tree (tmp_path parquet copies w/ 3 editions, 5 quotes, 2 events, 6 companies across 2 sectors incl. a stale one); assert each table's headers + key rows; unmatched-stem note; composite-list return |
| `doc/improvements/pending.md` | Close the C3 entry → completed.md at execution |
| `Makefile` | help text for `analytics` gains `temporal` mention only if the line enumerates reports (it doesn't today — likely no change) |

## 7. Gates & risks

- Gates: `ruff check helpers/graph/analytics.py tests/test_analytics.py`,
  `make types`, static_checks for touched modules, the two test files
  themselves. `make analytics REPORT=temporal` smoke on the live snapshot
  (no writes).
- Risks: (i) none on SQL surface — `percentile_cont` + `count(*) FILTER`
  verified live on DuckDB 1.5.5; `strftime('%Q')` is NOT a specifier in
  1.5.5 (proposal originally sketched it; the verified form is
  `year(ts) || '-Q' || quarter(ts)`, already corrected into §4.1);
  (ii) stem join misses
  if an edition note was renamed post-#136 — surfaced, not hidden
  (unmatched count in note); (iii) 108-row table 4.2 printing long —
  acceptable today, cap deferred.
- Explicitly out of scope: any UI wiring (Research Desk), persistence of
  report output, per-quarter *semantic* drift (embedding drift over
  editions — that's a different proposal if ever wanted).

## 8. Effort

~2 h total (matches the pending.md estimate): 45 min SQL + function, 30 min
tests, 15 min smoke + gates, 30 min docs/archive/completed.md on the
execution pass.

---

## 9. Implementation log (2026-08-25)

Landed as designed (§4 SQL, §5 decisions) with three corrections found
during execution:

- **`strftime('%Q')` does not exist in DuckDB 1.5.5** — §4.1 already
  corrected to `year(ts) || '-Q' || quarter(ts)` (caught during proposal
  verification, kept out of the code).
- **Unmatched stems are NOT drift**: the live run showed 4 unmatched
  quote editions (16 quotes: United Breweries, Blue Star, "Adani Green |
  Large Cap | Energy", Tata Power). Root cause: their chatter derives
  from CONCALL sections whose H1 fallback (`_edition_title`) stores the
  concall/company heading verbatim in `as_of_edition` — the documented
  honest-miss path of #136, not entities/derive drift. The note line was
  corrected accordingly ("concall/company-source chatter ... by design").
- **NULL event_dates**: 292/349 events have no parseable `event_date`
  (218 `date_precision='none'`, 74 NULL precision). The timeline groups
  them under `?` with an explanatory note instead of hiding them.

Additional surface beyond the proposal §6: `app.py /api/analytics/<name>`
now serializes the composite as `{"titles": [...], "reports": [...]}` (flat
shape preserved for single reports; one new unit test pins both shapes).

Gates: `ruff` clean on all four touched files, `make types` clean,
static_checks pass (1 pre-existing advisory), 109 tests green across
test_analytics.py + test_api_graph_unit.py, ty test-advisory warnings all
pre-existing (inherent-14 class). Live smoke: `make analytics
REPORT=temporal` renders all four tables in **0.77s** (well under the 3s
doc-query budget class; no perf-gate entry needed — analytics has none).
