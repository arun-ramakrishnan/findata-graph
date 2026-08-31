---
title: Mojo port of `database_integrity_check.py` — full check surface
status: executed
filed: '2026-08-30'
executed: '2026-08-30'
completed_md: '182'
area: Mojo tooling / `Mojo/src/common/integrity_check.mojo` +
---

# Mojo port of `database_integrity_check.py` — full check surface

**Date:** 2026-08-30 · **Status:** EXECUTED same day (completed.md #182;
89/89 canonical keys golden parity, bench leg `db-integrity` green;
overall wall parity 251–274 ms vs python 246–258 ms warm) ·
**Area:** Mojo tooling / `Mojo/src/common/integrity_check.mojo` +
`Mojo/bench/mojo_db_integrity.py` (bench leg `db-integrity`)

Operator decisions at approval (2026-08-30):
- The port is a TOOL, not a bench-only probe — source lives in
  `Mojo/src/common/` (moved from `Mojo/src/bench/`); only the parity
  fixture stays in `Mojo/bench/`.
- Bridge-first for anything Mojo lacks: use Python modules (stdlib and
  first-party) through `std.python` instead of hand-rolling equivalents
  (float rounding/formatting, `pathlib.rglob`, set semantics, repo
  constants).

## Context / problem

- The bench-side Mojo port of `helpers/misc/database_integrity_check.py`
  (2,108 lines — the repo's largest DB-access program) currently covers 4
  sections: entities+paths, relations, note_tags, cache_reconcile. It
  already achieves golden parity, 2.3–3.1× on compute+I/O sections,
  ~1.3× on SQL-bound sections, 0.75× on the DuckDB-bound reconcile.
- The original registers **17 checks** (the `_CHECKS` registry) plus the
  inline entity/file-path validation and a ~500-line report writer.
  **13 checks are unported**: entity_tags, events, quotes,
  company_metrics, orphan_companies, hierarchy (incl. taxonomy drift),
  normalization, duplicate_tickers, fuzzy_duplicates, validity_window,
  graph_summary, market_cap_conflicts, db_meta.
- Purpose (operator-stated): the port is the **Python-interop checkout
  vehicle at scale** — the checker touches every interop seam the
  subsystem has been validating one at a time (drivers via bridge,
  vendored mojo-yaml, filesystem, string logic), and finishing it forces
  the remaining seams through the same parity discipline.

## Interop areas the remaining 13 checks exercise (the actual point)

- **First-party module imports through the bridge** — NEW territory
  (only stdlib/third-party so far): `CANONICAL_EVENT_TYPES`
  (helpers.validators.static_checks), `EXPECTED_USER_VERSION` /
  `EXPECTED_SCHEMA_VERSION` (helpers.core.db), `SUPER_SECTORS` /
  `SUB_CATEGORIES` (helpers.maintenance.build_sector_hierarchy).
  Single-source-of-truth preserved: constants are imported, not copied.
- **SQLite feature passthrough**: `json_valid()`, `PRAGMA user_version`,
  recursive CTE (hierarchy cycles), `GROUP_CONCAT` + Mojo-side CSV parse
  (market-cap conflicts), subselect-heavy queries verbatim.
- **Python set semantics rebuilt in Mojo**: symmetric difference
  (taxonomy drift), stopword/generic-word sets, `frozenset`-keyed
  suppression list (fuzzy pairs) — on `List[String]` since Mojo has no
  set type.
- **Pure-Mojo compute after one fetch**: duplicate-ticker grouping and
  the fuzzy-name matcher (tokenize → inverted index → candidate pairs →
  match rules) — the busiest Python-loop logic in the file.
- **Filesystem walk in Mojo**: `rglob("*.md")` equivalent over
  `findata/Companies` + `findata/Sectors` (normalization orphaned_files)
  — the note_tags-shaped 3.1× case.
- **Mojo-side char-class validation** replacing `re.compile`
  (normalized_name format: leading alnum + `[A-Za-z0-9_]*`, no `__`,
  no trailing `_`).
- **Report generation in Mojo**: the markdown report writer
  (numbers-only formatting, no f-string fanciness beyond `round(…, 1)`
  / `round(…, 2)` equivalents).

## Solution / design

- Extend `Mojo/src/bench/integrity_check.mojo`: 13 new `check_*` defs,
  all check logic in Mojo, data via the bridge (established routing
  rule). Registry order and result-dict keys identical to the original.
- Extend the fixture `mojo_db_integrity.py` with `python_all_checks()`:
  runs the ORIGINAL checker methods and emits flat goldens (every
  numeric key + canonical sorted string lists) for all 17 checks.
  Parity is three-way: Mojo-computed vs python-live vs stored goldens.
- Report writer: same content and filename convention as the original
  (`database_integrity_report.txt`); bench mode writes under `/tmp` so
  the operator's real report is never clobbered; standalone mode writes
  the repo-root path. Exit semantics match `main()`: errors > 0 → 1.
- Keep the existing section-timing prints (they are the bench leg's
  payload) and add per-check timings.

## Non-goals

- **No gate change**: the production checker under `make qa` stays
  Python. The port is bench-side (perf + parity + interop checkout).
  Promoting it to production checker is a separate future decision,
  taken only if it stays parity-green across a Mojo upgrade or two.
- No new checks, no schema changes, no DB driver work.

## Perf expectations (extrapolated from the 4 measured sections)

- SQL-count-only checks (entity_tags, events, quotes, company_metrics,
  orphan_companies, market_cap_conflicts, validity_window, db_meta):
  aggregate <50 ms — query-bound, bridge ~free (db-access leg finding).
- hierarchy: counts + recursive CTE + one module import + set diff —
  ~10–20 ms.
- normalization: full entity scan + two vault walks — note_tags-shaped,
  expect ~3×.
- duplicate_tickers + fuzzy_duplicates: single fetch + pure Mojo token
  logic — expect the largest relative win of the port.
- graph_summary: counts + slicing — trivial.

## Risks / mitigations

- **Dual implementation drift** (the real risk): two copies of check
  logic. Mitigation: the parity harness diffs every number and string
  list against the live original every bench run, so drift is loud; the
  port gates nothing meanwhile.
- DuckDB-bound reconcile stays 0.75× — accepted, already documented.
- Mojo string-building verbosity in the report writer — keep it
  line-based; total port stays well under the original's 2,108 lines
  (no docstrings, no argparse re-implementation).
- `EXPECTED_USER_VERSION` drift — bridge-imported, never hardcoded.

## Verification plan

1. `make mojo-build`; run `Mojo/bin/integrity_check` — all 17 checks +
   header validation, parity vs `python_all_checks()` goldens, exact.
2. Report diff vs the python report (modulo timestamp/db path lines).
3. Bench leg `db-integrity` green within its existing 120 s budget.
4. `make search-fresh APPLY=1` after the doc updates.

## Decision requested

Approve the full 17-check port + report writer as bench-side Mojo
(enhance `integrity_check.mojo`, extend the fixture, no gate change).
