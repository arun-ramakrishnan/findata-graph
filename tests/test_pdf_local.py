"""Unit tests for helpers/pdf/pdf_local.py (no network calls)."""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.pdf.pdf_local import (  # noqa: E402
    MIN_IMAGE_BYTES,
    MIN_IMAGE_PX,
    LocalRefusalError,
    convert,
    _filter_running_headers,
    _image_ok,
    _normalize_headings,
    _strip_picture_text,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _noise_jpeg(path: Path, w: int, h: int, noisy: bool = True) -> Path:
    """Write a real JPEG; noisy ones exceed MIN_IMAGE_BYTES, flat ones don't."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h))
    if noisy:  # pseudo-noise: guaranteed above MIN_IMAGE_BYTES
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                pix.set_pixel(x, y, ((x * 7 + y * 3) % 256, (x * y) % 256, (x + y) % 256))
    else:  # flat fill: below MIN_IMAGE_BYTES at these sizes
        pix.clear_with(90)
    pix.save(str(path), output="jpg")
    return path


def _text_pdf(path: Path, lines: list[str]) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + 14 * i), line, fontname="helv", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# pure text transforms
# ---------------------------------------------------------------------------
def test_strip_picture_text_removes_blocks():
    t = "before\n<!-- Start of picture text -->BADGE<br>AD TEXT<!-- End of picture text -->\nafter"
    out = _strip_picture_text(t)
    assert "BADGE" not in out and "before" in out and "after" in out


def test_normalize_headings_strips_wrappers():
    out = _normalize_headings("### **<u>Marico Ltd. | Large Cap | FMCG</u>**")
    assert out == "### Marico Ltd. | Large Cap | FMCG"


def test_normalize_headings_splits_sector_glue():
    out = _normalize_headings(
        "# Engineering & Capital Goods Inox India | Small Cap | Engineering & Capita Goods"
    )
    assert out == (
        "## Engineering & Capital Goods\n## Inox India | Small Cap | Engineering & Capita Goods"
    )


def test_normalize_headings_glue_with_subtitle_tail():
    out = _normalize_headings("### Regulator RBI Governor |Winning in the AI Era")
    assert out == "## Regulator\n## RBI Governor | Winning in the AI Era"


def test_normalize_headings_no_split_when_sector_only():
    # pre-pipe is exactly the sector phrase — nothing follows, no split
    out = _normalize_headings("# Logistics | a look at the sector")
    assert out.startswith("# Logistics")


def test_normalize_headings_no_split_for_legal_suffix_remainder():
    # "Retail Ltd" is a company name starting with a sector word — remainder
    # is a pure legal suffix, must NOT be split off
    out = _normalize_headings("## Retail Ltd | Mid Cap | Retail")
    assert out == "## Retail Ltd | Mid Cap | Retail"


def test_normalize_headings_rescues_bold_body_heading():
    out = _normalize_headings("**Logistics** **<u>Delhivery | Large Cap | Logistics</u>**")
    assert out == ("## Logistics\n## Delhivery | Large Cap | Logistics")


def test_normalize_headings_leaves_plain_bold_alone():
    assert _normalize_headings("**Some bold sentence.**") == "**Some bold sentence.**"


def test_filter_running_headers(tmp_path=None):
    title = "The Chatter: Marico, DLF, BSE, Nykaa & More"
    page1 = f"# {title}\n\nbody one stays\n\n3/22\n\n8/6/26, 8:32 AM\n"
    out, got_title = _filter_running_headers(page1, None)
    assert got_title == title
    assert "body one stays" in out
    assert "3/22" not in out and "8/6/26" not in out
    # page 2 repeats the title — dropped
    page2 = f"# {title}\n\nbody two stays\n"
    out2, _ = _filter_running_headers(page2, got_title)
    assert out2.count(title) == 0 and "body two stays" in out2


def test_filter_running_headers_dedups_long_urls():
    url = "https://thechatter.zerodha.com/p/some-post?publication_id=1&x=2"
    t = f"{url}\nbody\n{url}\n"
    out, _ = _filter_running_headers(t, None)
    assert out.count(url) == 1 and "body" in out


# ---------------------------------------------------------------------------
# image filter
# ---------------------------------------------------------------------------
def test_image_ok_keeps_large_noisy(tmp_path):
    p = _noise_jpeg(tmp_path / "big.jpeg", MIN_IMAGE_PX + 50, MIN_IMAGE_PX + 50, noisy=True)
    assert p.stat().st_size >= MIN_IMAGE_BYTES
    assert _image_ok(p) is True


def test_image_ok_drops_small_dimensions(tmp_path):
    p = _noise_jpeg(tmp_path / "tiny.jpeg", 40, 40, noisy=True)
    assert _image_ok(p) is False


def test_image_ok_drops_few_bytes(tmp_path):
    p = _noise_jpeg(tmp_path / "flat.jpeg", MIN_IMAGE_PX + 50, MIN_IMAGE_PX + 50, noisy=False)
    assert p.stat().st_size < MIN_IMAGE_BYTES
    assert _image_ok(p) is False


# ---------------------------------------------------------------------------
# convert(): guard + pages shape
# ---------------------------------------------------------------------------
def test_convert_refuses_scanned_pdf(tmp_path):
    doc = pymupdf.open()
    doc.new_page()  # blank page: no text layer
    pdf = tmp_path / "scan.pdf"
    doc.save(str(pdf))
    doc.close()
    with pytest.raises(LocalRefusalError, match="text layer too thin"):
        convert(pdf, tmp_path / "imgs")


def test_convert_pages_shape(tmp_path):
    pdf = _text_pdf(
        tmp_path / "doc.pdf",
        [
            "The Chatter: Test Edition",
            "Welcome body text here, long enough to matter for the guard.",
            "More body text to keep the average character count safely above.",
        ],
    )
    pages = convert(pdf, tmp_path / "imgs")
    assert len(pages) == 1
    page = pages[0]
    assert set(page) == {"prunedResult", "markdown", "outputImages", "inputImage"}
    assert page["prunedResult"] is None
    assert "Welcome body text" in page["markdown"]["text"]
    assert page["markdown"]["images"] == {}
