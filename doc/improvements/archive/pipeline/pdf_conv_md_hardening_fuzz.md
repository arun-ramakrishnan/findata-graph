---
title: Harden Paddle `parse_pages`, consolidate `slugify`, add fuzz coverage
status: executed
filed: '2026-08-16'
executed: '2026-08-16'
completed_md: '114'
area: helpers/pdf/pdf_conv_md.py
---

# Proposal: Harden Paddle `parse_pages`, consolidate `slugify`, add fuzz coverage

**Status:** COMPLETE (2026-08-16)
**Scope:** `helpers/pdf/pdf_conv_md.py`, `helpers/pdf/capture_newsletter_images.py`,
new `helpers/pdf/common.py`, new `tests/test_fuzz_pdf_conv_md.py`
**Related:** `doc/procedures/markdown_parse.md` (PDF -> Markdown stage)

## Motivation
The markdown_parse workflow gained a new PDF->markdown path
(`helpers/pdf/pdf_conv_md.py`, Paddle OCR). Two issues:

1. **Fragile `parse_pages`.** It blindly indexes
   `line["result"]["layoutParsingResults"][0]` and nested `lpr[...]` keys with
   no defensive handling. A single malformed/unexpected Paddle JSONL line
   (error object, empty `layoutParsingResults`, schema drift) raises
   `KeyError`/`IndexError` and aborts the whole conversion.
2. **Duplicated `slugify`.** `slugify()` is byte-identical in both
   `pdf_conv_md.py` and `capture_newsletter_images.py`.

Meanwhile the new module has **no fuzz coverage**, while every other workflow
stage (parse_newsletter, derive_insights, extract_relations, derive_events,
frontmatter, images) already has Hypothesis fuzz tests.

## Changes
### A. Harden `parse_pages` (required)
Make per-line extraction defensive: skip (with a warning) any line that is not
a dict, or lacks `result` / `layoutParsingResults` / a `markdown` key, instead
of raising. Returns only successfully parsed pages. Preserves the existing
4-key output shape (`prunedResult`, `markdown`, `outputImages`, `inputImage`)
and stays backward-compatible with `tests/test_pdf_conv_md.py`.

### B. Consolidate `slugify` (low-risk)
- New `helpers/pdf/common.py` holds the single `slugify()` implementation.
- Both modules import it via `from helpers.pdf.common import slugify`, behind a
  `__package__`-guarded `sys.path` bootstrap so `python3 helpers/pdf/x.py`
  (script mode) still resolves the import.
- No behavior change; existing tests importing `slugify` from either module
  keep passing (the name remains a module attribute).
- `to_wikilinks` / `resolve_markdown` / `image_extension` / `plan_images` stay
  in `pdf_conv_md.py` (source-specific HTML structure; not duplicated).

### C. Add `tests/test_fuzz_pdf_conv_md.py` (Hypothesis, runs in `make qa`)
Property-based tests pinning "never raises" + output contracts for the pure
transforms:

| Function | Invariant pinned |
|---|---|
| `slugify` | never raises on arbitrary text; result has no whitespace, no `__`, no edge `_` |
| `parse_pages` | never raises on arbitrary JSON-ish list; returns list of 4-key dicts; well-formed lines preserved |
| `image_extension` | never raises; returns a string starting with `.` |
| `plan_images` | never raises on string-valued image maps; returns `(dict, int)`; counter advances by #images |
| `to_wikilinks` | never raises on arbitrary text + well-shaped plan; returns str |
| `resolve_markdown` | never raises; returns str |

## Validation
- `pytest tests/test_pdf_conv_md.py tests/test_capture_newsletter_images.py tests/test_fuzz_pdf_conv_md.py` -> all pass.
- `make qa` -> green (the new fuzz test runs in the gating pytest).
- Smoke: `python3 helpers/pdf/pdf_conv_md.py --help` and
  `python3 helpers/pdf/capture_newsletter_images.py --help` succeed
  (script-mode import still works).

## Out of scope
- Perf tests: the Paddle conversion is network/API-bound -> belongs in
  `live`/`slow` suites, not unit-perf. The previously-empty
  `test_sql_perf_guards.py` was removed; SQL perf guards are a separate effort.
- Network functions (`submit_job`/`poll_job`/`download_jsonl`/
  `write_outputs(fetch_images=True)`) are not fuzzed (require API key + network).

## Status
COMPLETE (2026-08-16). Implemented and validated.

### Validation results
- `pytest tests/test_fuzz_pdf_conv_md.py tests/test_pdf_conv_md.py \
  tests/test_capture_newsletter_images.py tests/test_fuzz_normalizers.py \
  tests/test_fuzz_images.py` -> 45 passed.
- `make qa` -> exit 0 (lint + types + deptry + static + pytest + notes + integrity + snapshot).
- Script-mode smoke: `python3 helpers/pdf/pdf_conv_md.py --help` and
  `python3 helpers/pdf/capture_newsletter_images.py --help` succeed.
- `slugify` now lives only in `helpers/pdf/common.py` (imported by both scripts
  via a `__package__`-guarded `sys.path` bootstrap + `# noqa: E402`).
- `parse_pages` skips malformed Paddle JSONL lines (warns) instead of raising.
