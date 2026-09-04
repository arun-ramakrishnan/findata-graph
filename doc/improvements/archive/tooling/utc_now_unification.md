---
title: "utc_now unification — scope disposition (closes the #196 W8 deferred item)"
status: executed
filed: "2026-09-03"
executed: "2026-09-03"
completed_md: "199"
area: "helpers/maintenance/enrich_relations.py (2 DB audit stamps), disposition record for all other timestamp producers"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# utc_now unification — scope disposition (closes the #196 W8 deferred item)

**Date:** 2026-09-03 · **Status:** EXECUTED ·
**Area:** `helpers/maintenance/enrich_relations.py` (2 sites) · disposition record for
every other non-`utc_now` timestamp producer · follows #196 W8 execution notes

## 1. Motivation

Roll #196 (W8) adopted `env.REPO_ROOT` but explicitly did NOT adopt
`db.utc_now()` at the enrich call sites, recording that their formats
diverge and adoption "would change output bytes". Left standing, that
deferred item keeps the question alive. This proposal disposes EVERY
non-`utc_now` timestamp producer with measured evidence — adopt where the
`db.utc_now()` docstring contract demands it, pin everything else as a
documented deviation — so the question closes.

The contract (`helpers/core/db.py:58`): a DATETIME/`last_updated` column
that participates in a staleness comparison against a
`CURRENT_TIMESTAMP`-defaulted column MUST carry the
`YYYY-MM-DD HH:MM:SS` shape; anything else produces TEXT that sorts
inconsistently against `CURRENT_TIMESTAMP` values.

## 2. Census (measured 2026-09-03) and disposition

| Site | Format | Lands in | Disposition |
|---|---|---|---|
| `enrich_relations.py:1987` → `entity_gf_map.resolved_at` | `isoformat(timespec="seconds")` (T-separator) | DB audit stamp; **write-only** — grep finds no reader, no comparison | **ADOPT `utc_now()`** |
| `enrich_relations.py:2528` → `entity_ticker_status.decided_at` | same | DB audit stamp; write-only (INSERT-only, grep-verified) | **ADOPT `utc_now()`** |
| `enrich_from_yfinance.py:199` → `company_metrics.props` JSON `fetched_at` | T-isoformat inside `json.dumps` payload | self-describing JSON, no known parser | leave (deviation) |
| `enrich_relations.py` report headers `generated:` (×6: 472, 810, 1503, 1666, 1935, 2242) | T-isoformat | human-readable sidecar reports | leave (display) |
| `enrich_relations.py:576` → frontmatter `fetched_at` | T-isoformat | `findata/**` vault bytes | leave (writer-owned vault — byte churn for zero benefit) |
| `enrich_relations.py:393, 704, 806, 1372, 1499` (`today`), `:994` (`coinfer/{date}` dir) | date-only `.date().isoformat()` | report text / directory identity | leave (path identity must not shift) |
| `enrich_from_yfinance.py:469` report `ts` | `"%Y-%m-%d %H:%M:%S UTC"` suffix | `metrics_report.txt` display | leave (display) |
| `enrich_from_yfinance.py:280` `Refreshed: {today}` | **local** `datetime.now()` date-only | company note body | leave; local date is the operator-facing semantic on a display line (documented deviation — the only local-time producer in `helpers/`) |
| `git_secret_scan.py` (`helpers/misc/`) | `"%Y-%m-%dT%H:%M:%SZ"` (RFC 3339 Z) | scan report display | leave (display) |

The two ADOPT rows are the only values that live in bare DB DATETIME-ish
columns; neither participates in a comparison today, so this is
consistency-with-the-contract, not a bug fix.

## 3. Design

- Replace the two `datetime.now(UTC).isoformat(timespec="seconds")`
  expressions with `utc_now()` (imported from `helpers.core.db`, matching
  the existing `connect` import at the top of the file; both sites already
  hold an open `conn` from `helpers.core.db`).
- **Backfill the 4 pre-existing rows.** Though `resolved_at` / `decided_at`
  are audit stamps (they record the moment a decision was written), the
  instant itself is untouched by the shape change — `2026-08-25T07:15:04+00:00`
  and `2026-08-25 07:15:04` are the same datetime, just a different text
  encoding. The values are write-only (no reader, no comparison, no
  report), only 4 rows exist, and a rewrite is idempotent, so a one-shot
  SQL conversion eliminates the mixed-shape shard permanently:

  ```sql
  UPDATE entity_gf_map
     SET resolved_at = REPLACE(REPLACE(resolved_at, 'T', ' '), '+00:00', '');
  UPDATE entity_ticker_status
     SET decided_at = REPLACE(REPLACE(decided_at, 'T', ' '), '+00:00', '');
  ```

  Result: all rows (old and new) uniformly carry the `YYYY-MM-DD HH:MM:SS`
  shape the docstring contract demands, so the table is consistent today,
  not just for future writes.

## 4. Non-goals

- Every "leave" row in §2 — pinned so the churn temptation (display
  headers, vault frontmatter, path identity, note bodies) is retired with
  evidence, not re-litigated per arc.
- No `strftime` helper or format registry — one adopted shape
  (`utc_now()`), documented deviations for the rest.

## 5. Gates

Targeted pytest (the enrich_relations test module), `ruff` on the touched
file, `make qa` once at arc end. `rg 'isoformat\(timespec' helpers/`
drops by exactly 2 after landing.

## 6. Risks

- Near-zero: two write-only columns, additive library function already
  imported elsewhere in the file's package.
- The real risk was scope creep into vault/display bytes — closed by §4.
