---
title: "Verify-path injection + boundary remediation — the 272477d4a review follow-ups"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "334"
area: "helpers/maintenance/snapshot_db.py, desktop/src-vue/test/make_fixtures.py, tests/, helpers/misc/review_selection.py, .opencodereview/rule.json, doc/procedures/ocr_review.md"
---

# Verify-path injection + boundary remediation — the 272477d4a review follow-ups

**Date:** 2026-10-01 · **Status:** EXECUTED (completed.md #334) · **Severity:** HIGH (SNAP-1
was remediated on the restore paths only; the two verify paths still
interpolate repo-tracked filenames into SQL and fail open — reproduced
three ways in scratch, `read_only` bounded). Two independent delegation
legs (space-bunny/opencode, glm-5.3-flash/zcode) converged on the same
four findings with zero contradictions; both legs' evidence lives in
`doc/local/engineering/code_review.md` and
`/mnt/data/tmp/reviews/{space-bunny,glm-5.3-flash}/`.

## The tasks

| # | Slice | Finding | Files | Acceptance |
|---|---|---|---|---|
| S1 | **Verify-path identifier gates.** Every parquet stem entering `_verify_parquet_duckdb_side`/`_verify_parquet_sqlite_side` passes a verify-specific gate: regex `^[a-z_][a-z0-9_]*$` (`\Z`-anchored) or **loud ValueError** (tampered filename — never contract-legal); stem absent from the live schema → `mismatch: MISSING ON LIVE` (drift surfaces; the silent `except-continue` skip route dies); `EPHEMERAL_TABLES` absent-on-live keeps its info+skip contract (duckdb side only). DuckDB path interpolation gets `'`-doubling, same as restore. | HIGH-1 | `snapshot_db.py` both `_verify_parquet_*_side` | the §H PoC shapes raise or mismatch — `mismatches==[]` unattainable with any attacker file |
| S2 | **Regression tests, fail-open routes pinned.** (a) injection stems (`]`-escape, bare, quote) → ValueError; (b) mirror-count fail-open shape → mismatch now (no silent pass); (c) skip-route (erroring stem) → mismatch/loud, never `tables_checked` shrink; (d) ephemeral-absent still skips+logs; (e) benign roundtrip unchanged. Both gates mutation-verified (strip gate → RED). | HIGH-1 armor | `tests/test_snapshot_db.py` | red-on-mutation for both verify sides |
| S3 | **Fixtures boundary → canonicalization.** `make_fixtures._docs` normalizes each `doc_search.file_path` (posixpath.normpath) and excludes `doc/local` by normalized prefix + rejects absolute/`..` — matching the commit's own `resolve()+is_relative_to` standard; boundary test extended to the bypass shapes (`./doc/local/x`, `doc/local`, absolute) at artifact level. | MEDIUM-1 | `make_fixtures.py`, `test_desktop_fixtures_boundary.py` | bypass shapes fail the test; regenerate fixtures (content unchanged — current rows are already clean) |
| S4 | **Dead-noqa + anchor cleanup.** `:1092` `noqa: S603` → explanatory comment only (probe: nothing fires); `:904` dead `noqa: S608` dropped (fragment never executed on that line); `:916` block rewritten by S1 (noqa gone with the continue); `_SNAPSHOT_IDENT_RE` `$` → `\Z`. | LOW-1/2 + out-of-diff adds | `snapshot_db.py` | RUF100 probe clean on the file; restore/verify tests green |
| S5 | **OCR selection-drift record.** `ocr_review.md` §1: 1.12.11's extension allow-list also selects `.rs`, `.js` and the extensionless `Makefile` (observed 22/22 on 272477d4) with dedicated rule groups — the §1 `.py/.pyi/.ipynb/.json` line is stale. | procedure drift (both legs) | `doc/procedures/ocr_review.md` | table reflects observed selection |
| S6 | **opencodereview rule adds.** `rule.json` include: `desktop/src-tauri/**/*.rs`, `desktop/src-vue/src/**/*.js`, `desktop/src-vue/src/**/*.vue`, `desktop/src-vue/test/**/*.js`, `doc/design/**`, `doc/reference/**`, `doc/okf/**`, `doc/templates/**`, `doc/security/**` (excludes already protect `doc/local/**`, `findata/**`); `review_selection.PRODUCT_FAMILIES` += `desktop/src-tauri/`, `desktop/src-vue/` so the teeth assertion demands desktop coverage on desktop diff traffic (today a desktop-only patch could review nothing without firing). | both legs' rule notes | `.opencodereview/rule.json`, `review_selection.py` | `make review-patch --stack 1` teeth pass; mutation: desktop diff with zero selected → teeth fire |

## What this deliberately does NOT do

- No restore-side changes (SNAP-1 gates verified sound by both legs).
- No opener/CSP changes (DESK-1/2 landed in #332; `http://**` scope is the
  documented S2 boundary).
- No gate wiring for RUF100 (enabling it repo-wide would flag pre-existing
  dead noqas beyond this arc's scope — the two found here are fixed
  directly; broader enablement is its own decision).
- No history rewrite for the fixtures metadata already public (ruled
  2026-10-01, Addendum 7 §J).

## Verification

`tests/test_snapshot_db.py` + `test_desktop_fixtures_boundary.py` +
`test_desktop_security_posture.py` green with mutation checks on the two
new gates; `make snapshot-check` round-trips the real tracked snapshot;
`make review-patch --stack 1` teeth green with the widened families;
lint-audit + md-lint + static-checks clean; `make search-fresh APPLY=1`.
