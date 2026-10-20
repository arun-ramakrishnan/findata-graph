#!/usr/bin/env python3
"""Render caps for the vendored MuPDF paths (parser-input sweep, 2026-10-21).

MuPDF renders the uncompressed page surface (width x height x 4 bytes per
page) inside the vendored parser, before any repo-side gate runs: a
measured 819-byte hostile PDF (6000x6000pt MediaBox) produced a 496 KB
pixmap and ~509 MB RSS growth — 606x amplification, linear in page
dimensions and unbounded relative to input size. See
doc/security/findings/2026-10-21-extractors-2.json for the measurement
and the disposition of each pymupdf.open site.

`checked_open` is the one choke point: open the document, validate the
page count and per-page dimensions BEFORE anything renders, and close on
refusal so a hostile file never leaks a live document handle. The caps
read as module attributes at call time, so the pdf_conv_md CLI flags
(--max-pages, --max-page-dims) override them for one-off larger runs
without touching the engines.

Defaults sit far above the tracked Reports corpus (0.8 MB, 28 pages, A4
= 595x842pt): 200 pages, 4000pt per page dimension.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

MAX_PAGES: int = 200
MAX_PAGE_PTS: int = 4000

#: Module self-reference for CLI overrides (`render_limits.MAX_PAGES = N`);
#: imported by pdf_conv_md alongside the exception class.
render_limits = sys.modules[__name__]


class RenderLimitExceeded(ValueError):
    """A document exceeds the render caps and is refused before parse."""


def checked_open(
    pdf_path: Path | str,
    *,
    max_pages: int | None = None,
    max_page_pts: int | None = None,
) -> pymupdf.Document:
    """Open a PDF and enforce the render caps before any page is touched.

    Reads the module defaults at call time so CLI overrides propagate.
    Raises RenderLimitExceeded (with the document closed) when the file
    exceeds the page-count or per-page-dimension cap; returns the open
    Document for the caller to close as usual otherwise.
    """
    doc = pymupdf.open(str(pdf_path))
    pages_cap = MAX_PAGES if max_pages is None else max_pages
    pts_cap = MAX_PAGE_PTS if max_page_pts is None else max_page_pts
    if doc.page_count > pages_cap:
        n = doc.page_count
        doc.close()
        raise RenderLimitExceeded(f"{pdf_path}: {n} pages over the {pages_cap}-page cap")
    for i in range(doc.page_count):
        rect = doc[i].rect
        if rect.width > pts_cap or rect.height > pts_cap:
            dims = f"{rect.width:.0f}x{rect.height:.0f}pt"
            doc.close()
            raise RenderLimitExceeded(
                f"{pdf_path}: page {i + 1} is {dims}, over the {pts_cap}pt-per-dimension cap"
            )
    return doc
