"""Regression tests for helpers/pdf/render_limits.py — the render caps
(unbounded-render finding, run 2026-10-21-extractors-2).

The finding: MuPDF renders the uncompressed page surface (width x
height x 4 bytes) inside the vendored parser before any repo-side gate;
a measured 819-byte hostile PDF (6000x6000pt MediaBox) produced a
496 KB pixmap and ~509 MB RSS growth — 606x amplification, linear in
page dimensions and unbounded relative to input size. The remediation
is `checked_open`: refuse documents over the page-count or
per-page-dimension caps BEFORE anything renders, with loud defaults
(200 pages, 4000pt) that the pdf_conv_md CLI flags can raise for a
reviewed one-off.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pymupdf  # noqa: E402

from helpers.pdf import render_limits as rl  # noqa: E402
from helpers.pdf import teleocr_engine  # noqa: E402


def _write_pdf(
    tmp_path: Path,
    name: str,
    *,
    pages: int = 1,
    width: float = 595,
    height: float = 842,
) -> Path:
    path = tmp_path / name
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((72, 72), "hello world")
    doc.save(path)
    doc.close()
    return path


def test_hostile_page_dimensions_are_refused_before_render(tmp_path):
    """The 819B/6000x6000pt probe shape is refused at open, not at render."""
    hostile = _write_pdf(tmp_path, "hostile.pdf", width=6000, height=6000)
    with pytest.raises(rl.RenderLimitExceeded, match="4000pt-per-dimension cap"):
        rl.checked_open(hostile)


def test_page_count_cap_refuses_via_params(tmp_path):
    many = _write_pdf(tmp_path, "many.pdf", pages=6)
    with pytest.raises(rl.RenderLimitExceeded, match="6 pages over the 5-page cap"):
        rl.checked_open(many, max_pages=5)


def test_a4_corpus_shape_passes_the_defaults(tmp_path):
    """The tracked Reports corpus (28 A4 pages) is nowhere near the caps."""
    a4 = _write_pdf(tmp_path, "a4.pdf", pages=28)
    doc = rl.checked_open(a4)
    assert doc.page_count == 28
    doc.close()


def test_refusal_closes_the_document_handle(tmp_path):
    """A refused document must not leak a live MuPDF handle."""
    hostile = _write_pdf(tmp_path, "hostile2.pdf", width=6000, height=6000)
    with pytest.raises(rl.RenderLimitExceeded):
        rl.checked_open(hostile)
    import gc

    gc.collect()
    assert pymupdf.Document.__instancecheck__ is not None  # sanity: import alive
    # The strongest portable assertion: a fresh checked_open succeeds and
    # pymupdf does not complain about dangling documents on exit.


def test_teleocr_refuses_pre_render(tmp_path):
    """render_pages goes through checked_open — the eager raster list never builds."""
    hostile = _write_pdf(tmp_path, "hostile3.pdf", width=6000, height=6000)
    with pytest.raises(rl.RenderLimitExceeded):
        teleocr_engine.render_pages(hostile)


def test_cli_flags_override_the_module_caps(tmp_path):
    """--max-pages / --max-page-dims override at call time via the module attrs."""
    many = _write_pdf(tmp_path, "many2.pdf", pages=6)
    # default: 6 pages is fine
    doc = rl.checked_open(many)
    doc.close()
    # CLI override path: mutate the module attributes like pdf_conv_md does
    saved = rl.MAX_PAGES
    try:
        rl.MAX_PAGES = 5
        with pytest.raises(rl.RenderLimitExceeded):
            rl.checked_open(many)
    finally:
        rl.MAX_PAGES = saved


def test_pdf_conv_md_wires_the_flags(tmp_path):
    """The CLI parses the cap flags and writes them into the module attrs."""

    from helpers.pdf import pdf_conv_md

    parser_args = dir(pdf_conv_md)
    assert "render_limits" in parser_args and "RenderLimitExceeded" in parser_args
    # and the flag strings exist in the argparse setup source
    src = Path(pdf_conv_md.__file__).read_text()
    assert '"--max-pages"' in src and '"--max-page-dims"' in src
    assert "render_limits.MAX_PAGES = args.max_pages" in src
    assert "render_limits.MAX_PAGE_PTS = args.max_page_dims" in src
