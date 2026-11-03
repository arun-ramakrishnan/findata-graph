---
title: "Relations-enrich FK guard: validate endpoints and cache age before apply"
status: executed
filed: "2026-11-03"
executed: "2026-11-03"
completed_md: "477"
area: "helpers/maintenance/enrich_relations.py"
---

# Relations-enrich FK guard: validate endpoints and cache age before apply

**Date:** 2026-11-03 · **Status:** LANDED 2026-11-03 (same day, S1+S2) ·

Provenance: 2026-11-03 operator run
`make relations-enrich ARGS="--source yfinance --apply"` crashed with
`sqlite3.IntegrityError: FOREIGN KEY constraint failed` at
`enrich_relations.py:481` (`INSERT OR IGNORE INTO graph_edges`).
Root cause: the fetch cache `memory/yf_relations_fetch_cache.json`
fetched 2026-08-24 held three entity names renamed since (Amara Raja
Batteries → Amara Raja Energy and Mobility; Kinetic Engg → Kinetic
Engineering; Oil and Natural Gas → ONGC); `--apply` inserts
candidates edges into `graph_edges` whose `source`/`target`
`REFERENCES entities(name)` (schema/sqlite/research.sql:36-40), so
stale-cache names violate the FK. The tool's FK enforcement is
correct (helpers.core.db.connect()); the gap is that nothing
validates endpoints or cache age before the write.

## 1. Endpoint pre-filter in apply_edges (S1)

`apply_edges` (:451-487) deletes by `source_ref` prefix then inserts
candidates blind. Task: before the insert loop, query
`SELECT name FROM entities` into a set; partition edges into writable
and dropped; insert only writable; report dropped under a new
`[fk_dropped]` report section with the missing endpoint named
(bounded blast radius, explicit in the report instead of a crash).
Acceptance: with the current stale cache, `--apply` completes and the
report lists the 3 renamed entities under `[fk_dropped]` instead of
IntegrityError; dry-run parity counts unchanged (dry-run never
validated endpoints).

## 2. Cache-age guard (S2)

`run_yfinance_pass` (:584-615) loads any existing cache silently —
fetched_at is logged but not acted on. Task: warn when `fetched_at`
older than 14 days; refuse apply-mode passes when older than 45 days
unless `--refresh-cache` given. Thresholds per options: the stale
case here was 71 days. Acceptance: synthesized old cache triggers
warn at 14+, hard stop at 45+ with a message naming
`--refresh-cache`; fresh cache unaffected.

## Out of scope

Automatic entity-rename mapping (rename arcs own that ledger);
refresh-chain lane changes; other sources (prose/holders/...) share
the cache only via the same helpers — S1/S2 sit in the shared
apply/load paths so they cover them.

## Landed 2026-11-03 (S1 + S2, same day)

- **S1** — `filter_known_entities` (enrich_relations.py:491) partitions
  candidates against `SELECT name FROM entities`; the yfinance pass
  filters before apply (:727), logs one warning per dropped edge, and
  `write_report` gains the `fk_dropped` section (:566, :607). Dry-run
  parity counts now run over writable edges, so parity is true.
- **S2** — `_guard_cache_age` (:519): warn above 14 days; SystemExit
  above 45 (or unparseable `fetched_at`) in edge-writing mode without
  `--refresh-cache`; sits on the cache-load branch only (:674), so
  fresh fetches never see it.
- Validation: 84/84 tests/test_enrich_relations.py (8 new: filter unit,
  pass-level crash-shape with `[fk_dropped]` report assert, 6 cache-age
  guard cases); mutation proof — removing the filter call turns the
  pass-level test red, restored green; ruff check + format clean.
- Operator note: with the 71-day cache still on disk, plain `--apply`
  now exits with the guard message; `--refresh-cache` re-fetches under
  current names and makes the pass safe.
- **Follow-up same day** — progress indicator + request bounding: the
  first `--refresh-cache` run hung mid-fetch with zero output (workers
  on stalled Yahoo responses; yfinance 1.7 has no timeout parameter)
  and the operator killed it blind — twice. `fetch_all` and
  `fetch_holders_for_all` now log count/total, rate, ETA, running
  failure count, and in-flight tickers every 250 completions or 30 s,
  flagging anything over 60 s as STALLED; `fetch_company` and the
  holders fetch route through `_TimeoutSession` (connect 5 s / read
  30 s), turning a hang into a bounded failure the existing retry
  handles. 91/91 tests/test_enrich_relations.py (7 new: progress
  lines, stalled-ticker naming, failure summary, empty batch, session
  wiring + timeout injection). Cache is still written only after the
  last fetch, so a kill mid-run loses progress — incremental cache
  checkpointing noted as possible follow-up, not built.

## Eval gate

No query-visible semantics change (rosters, crosswalks, hierarchies,
extractor rules): write-path validation and a CLI guard only — no
`ontology_eval_gate` bullet required per the proposals/README
contract.
