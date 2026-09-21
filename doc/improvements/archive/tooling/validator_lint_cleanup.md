---
title: "Validator lint cleanup — two C901s and an S607 from the gate arcs"
status: executed
filed: "2026-09-21"
executed: "2026-09-21"
completed_md: "263"
area: "helpers/validators"
---

# Validator lint cleanup — two C901s and an S607 from the gate arcs

**Date:** 2026-09-21 · **Status:** EXECUTED ·
**Area:** `helpers/validators/data_format_checks.py`,
`helpers/validators/frontmatter_schema.py`,
`helpers/validators/static_checks.py`

## 1. Motivation

`make lint-audit` (`ruff check --select S,UP,C901 .`) is red on three
findings, all introduced by the gate-latency arcs (feature velocity
outran the complexity budget). The earlier search_tui_app.py findings
from the 11:49 advisory (C901 `_fill`, S608) are gone — resolved on
the operator's side — so this arc owns exactly what's red today:

| Finding | Location | Verdict |
|---|---|---|
| C901 `check_data_format` 14 > 10 | `data_format_checks.py:214` | split (scope + hygiene branches added by Slice A/D) |
| C901 `check_frontmatter_schema` 15 > 10 | `frontmatter_schema.py:225` | split (engine + scope + strict branches) |
| S607 partial executable path `["git", ...]` | `static_checks.py:154` | `# noqa: S607` per house pattern (node/python3 lines carry it) |

## 2. Evidence (measured 2026-09-21, this box)

| Configuration | Result | Verdict |
|---|---|---|
| `ruff check --select S,UP,C901 .` | 3 errors, all in gate-arc code | baseline (must be 0) |
| behavior coverage | 376 tests green incl. parity/hygiene pins | splits must not change verdicts |

## 3. Design

- `check_data_format`: extract the hygiene block into
  `_check_baseline_hygiene(z, a, scanned, fatal)` and the scoped-rel
  builder into `_scoped_scan_rels(scope)` — the entry keeps orchestration.
- `check_frontmatter_schema`: extract engine resolution into
  `_resolve_engine(strict)` (returns engine + degrade advisories) and
  the per-dirname walk into `_check_findata_dir(...)`; the entry keeps
  sequencing. Fatal/advisory threading unchanged.
- S607: `# noqa: S607` with the house justification comment
  (PATH-resolved binary by design, same as the node/python3 call sites).

Alternatives considered: raising the C901 threshold (rejected —
threshold is the budget, the code is what's over); noqa on C901
(rejected — these are splittable, not inherently complex).

Slices: S1 data_format split; S2 frontmatter split; S3 noqa + full
suites (S1–S3 are each independently landable extractions).

## 4. Acceptance criteria & shakedown

1. `ruff check --select S,UP,C901 .` → 0 errors.
2. Full validator/maint suites green (parity + hygiene + engine
   tests pin verdicts through the splits).
3. `make lint-audit` green; `make search-fresh` converges.

| Projected outcome | Today | After |
|---|---|---|
| lint-audit errors | 3 | 0 |
| check verdicts | 376 green | 376 green (no behavior change) |

## 5. Risks

- **Split changes verdicts** — mitigation: extraction-only (move code,
  don't rewrite logic); parity/hygiene/engine suites run per slice.
- **C901 still >10 after split** — mitigation: measure per slice with
  ruff; second extraction point pre-identified (walk-body loop).

## 6. Non-goals

New features; touching the search_tui findings (gone); changing what
any check asserts; the snapshot arc (separate proposal).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-21 | `ruff check --select S,UP,C901 .` | C901 14 + C901 15 + S607 | all three in gate-arc code, post-merge tree |
| 2026-09-21 | S1–S3 build | hygiene + scanned-rels extraction; engine + per-dir extraction (also removed a duplicated findata-guard); S607 noqa per house pattern | `ruff check --select S,UP,C901 .` → 0 errors; `make lint-audit` green; 214 validator tests green |
