#!/usr/bin/env python3
"""Post-processing for LiteParse text — pdf_local normalizations.

Applies the same heading/normalizations that `helpers/pdf/pdf_local.py`
applies to `pymupdf4llm` output, but to `liteparse` `res.text` (page-joined).
This closed the 1.7% recall gap (96.04% -> ~97.7%) measured in
`doc/local/perf_skills.md:9.1` 7-PDF trial, before any engine cutover.

The normalization primitives (regexes, sector prefixes, heading fixers)
are OWNED by pdf_local and imported here — this module was a full copy
until 2026-09-08 (code_duplication_consolidation S1); a "keep in sync"
comment used to stand in for the import. `CAP_TAIL_RE` and
`SECTOR_PREFIXES` are re-exported for `liteparse_markdown.py`.

No image logic here — images are handled as a sidecar via `pymupdf`
(see proposal `liteparse_pdf_engine.md` Slice 1).
"""

from __future__ import annotations

from helpers.pdf.pdf_local import (  # noqa: F401  (CAP_TAIL_RE, SECTOR_PREFIXES re-exported)
    CAP_TAIL_RE,
    PIC_BLOCK_RE,
    SECTOR_PREFIXES,
    _filter_running_headers,
    _normalize_headings,
    _strip_picture_text,
)


def normalize(text: str, title: str | None = None) -> str:
    """Apply pdf_local normalizations to liteparse text.

    `text` may be a single page or the full doc (page-joined). Pass
    `title` through across pages when normalizing per-page.
    Returns normalized text (single string, potentially with an extra
    ``\\n`` for a split glue heading).
    """
    text = _strip_picture_text(text)
    text, title = _filter_running_headers(text, title)
    text = _normalize_headings(text)
    return text


# For per-doc convenience: normalize page-joined liteparse output in one call
def normalize_doc(text: str) -> str:
    return normalize(text)
