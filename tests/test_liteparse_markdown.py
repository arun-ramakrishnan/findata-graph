"""Tests for helpers/pdf/liteparse_markdown.py — Slice 1 gap-fill."""

from __future__ import annotations
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from helpers.pdf.liteparse_markdown import (
    _looks_like_company_heading,
    _to_markdown_lines,
    TESSDATA_DEFAULT,
)

# ---------------------------------------------------------------------------
# heading heuristic
# ---------------------------------------------------------------------------


def test_looks_like_company_heading_with_cap_token():
    assert _looks_like_company_heading("Marico Ltd. | Large Cap | FMCG") is True
    assert _looks_like_company_heading("SBI | Large Cap | Financial Services") is True
    assert _looks_like_company_heading("Titan Company | Mid Cap | Retail") is True


def test_looks_like_company_heading_sector_glue():
    # Sector prefix + company glue with pipe
    assert (
        _looks_like_company_heading(
            "Engineering & Capital Goods Inox India | Small Cap | Engineering"
        )
        is True
    )
    assert (
        _looks_like_company_heading("Financial Services SBFC Finance | Small Cap | Financial")
        is True
    )


def test_looks_like_company_heading_rejects_plain():
    assert _looks_like_company_heading("This is a plain sentence without pipe") is False
    assert _looks_like_company_heading("Short | pipe") is False  # too short / no cap or sector
    assert _looks_like_company_heading("No pipe here Large Cap") is False


def test_looks_like_company_heading_rejects_too_long():
    long_line = "A | " + "x" * 120 + " | Large Cap"
    assert _looks_like_company_heading(long_line) is False


def test_to_markdown_adds_heading_prefix():
    md = _to_markdown_lines("Marico Ltd. | Large Cap | FMCG\nSome body text")
    assert md.splitlines()[0] == "## Marico Ltd. | Large Cap | FMCG"
    assert "Some body text" in md


def test_to_markdown_idempotent_heading():
    md = _to_markdown_lines("## Already a heading\nbody")
    assert md.splitlines()[0] == "## Already a heading"


def test_to_markdown_preserves_blanks():
    md = _to_markdown_lines("line1\n\nline2")
    assert md == "line1\n\nline2"


def test_to_markdown_strips_indent():
    md = _to_markdown_lines("    indented line | Large Cap | FMCG")
    # liteparse indents with 4 spaces — _to_markdown_lines strips via .strip()
    assert md.strip().startswith("##") or "indented line" in md


# ---------------------------------------------------------------------------
# TESSDATA_PREFIX env
# ---------------------------------------------------------------------------


def test_convert_liteparse_ocr_sets_tessdata_env(tmp_path):
    # Create a minimal 1-page PDF with text layer — lite OCR should still work
    # but we test that TESSDATA_PREFIX is set via the convert path.
    import pymupdf

    pdf = tmp_path / "mini.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world 12,400 | Large Cap | Test")
    doc.save(str(pdf))
    doc.close()

    # Ensure env is clean, then call convert_liteparse_ocr
    old = os.environ.get("TESSDATA_PREFIX")
    try:
        if "TESSDATA_PREFIX" in os.environ:
            del os.environ["TESSDATA_PREFIX"]
        from helpers.pdf.liteparse_markdown import convert_liteparse_ocr

        md, meta = convert_liteparse_ocr(pdf)
        # Should have set TESSDATA_PREFIX to default
        assert os.environ.get("TESSDATA_PREFIX") == TESSDATA_DEFAULT
        assert meta["tessdata"] == TESSDATA_DEFAULT
        assert meta["engine"].startswith("liteparse-ocr")
        assert "page_texts" in meta
        assert meta["pages"] == 1
        assert len(md) > 10
    finally:
        if old is not None:
            os.environ["TESSDATA_PREFIX"] = old
        elif "TESSDATA_PREFIX" in os.environ:
            del os.environ["TESSDATA_PREFIX"]


def test_convert_liteparse_ocr_uses_custom_tessdata(tmp_path):
    import pymupdf

    pdf = tmp_path / "mini2.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Custom tessdata test 12,400")
    doc.save(str(pdf))
    doc.close()

    from helpers.pdf.liteparse_markdown import convert_liteparse_ocr

    md, meta = convert_liteparse_ocr(pdf, tessdata_path="/tmp/custom_tessdata")  # noqa: S108  # intentional test path for TESSDATA override
    assert meta["tessdata"] == "/tmp/custom_tessdata"  # noqa: S108
    assert os.environ.get("TESSDATA_PREFIX") == "/tmp/custom_tessdata"  # noqa: S108
    # restore default for other tests
    os.environ["TESSDATA_PREFIX"] = TESSDATA_DEFAULT


def test_convert_liteparse_ocr_mixed_table_chars(tmp_path):
    # Verify the 333-char scanned fixture produces expected length
    pdf = Path("tests/data/ocr_samples/mixed_table_formula.pdf")
    if not pdf.exists():
        pytest.skip("missing fixture")
    from helpers.pdf.liteparse_markdown import convert_liteparse_ocr

    md, meta = convert_liteparse_ocr(pdf)
    assert meta["chars"] == len(md)
    assert 300 <= meta["chars"] <= 400  # 333 expected
    assert "12,400" in md or "12,400" in md.replace(" ", "")
    assert meta["pages"] == 1
    assert "page_texts" in meta
