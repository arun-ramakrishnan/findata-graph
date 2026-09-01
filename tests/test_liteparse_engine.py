"""Tests for helpers/pdf/liteparse_engine.py — Slice 2 engine parity."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymupdf
import pytest

from helpers.pdf.liteparse_engine import (
    ENGINE_LABEL_NOCR,
    ENGINE_LABEL_OCR,
    LocalRefusalError,
    convert,
    get_bbox_sidecar,
)


def _mini_pdf(path: Path, lines: list[str]) -> Path:
    # Ensure > MIN_CHARS_PER_PAGE (100) so no-ocr does not refuse — pad with filler
    if sum(len(ln) for ln in lines) < 120:
        lines = lines + ["Lorem ipsum dolor sit amet consectetur adipiscing elit " * 3]
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + 14 * i), line, fontname="helv", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def test_engine_labels_contain_liteparse():
    assert "liteparse" in ENGINE_LABEL_NOCR
    assert "liteparse" in ENGINE_LABEL_OCR
    assert "noocr" in ENGINE_LABEL_NOCR
    assert "ocr" in ENGINE_LABEL_OCR


def test_convert_nocr_born_digital(tmp_path):
    pdf = _mini_pdf(tmp_path / "born.pdf", ["Hello world 12,400 | Large Cap | Test", "Second line"])
    img_dir = tmp_path / "imgs"
    pages = convert(pdf, img_dir, ocr=False)
    assert len(pages) == 1
    assert "Hello world" in pages[0]["markdown"]["text"]
    assert pages[0]["markdown"]["images"] == {}  # no figures on mini pdf
    assert "liteparse" in pages[0]["prunedResult"]
    assert "bbox_items" in pages[0]["prunedResult"]["liteparse"]


def test_convert_ocr_scanned_fixture(tmp_path):
    pdf = Path("tests/data/ocr_samples/mixed_table_formula.pdf")
    if not pdf.exists():
        pytest.skip("missing fixture")
    img_dir = tmp_path / "imgs2"
    pages = convert(pdf, img_dir, ocr=True)
    assert len(pages) == 1
    md = pages[0]["markdown"]["text"]
    assert 300 <= len(md) <= 500  # 333 chars + possible image divs
    assert "12,400" in md
    assert "liteparse" in pages[0]["prunedResult"]


def test_convert_nocr_refuses_scanned(tmp_path):
    pdf = Path("tests/data/ocr_samples/mixed_table_formula.pdf")
    if not pdf.exists():
        pytest.skip("missing fixture")
    img_dir = tmp_path / "imgs3"
    with pytest.raises(LocalRefusalError):
        convert(pdf, img_dir, ocr=False)


def test_convert_nocr_image_sidecar(tmp_path):
    # Create a PDF with an embedded large image that passes MIN_IMAGE_PX/BYTES
    pdf = tmp_path / "with_img.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Text with image below " + "Lorem ipsum " * 20)
    # Create a large image via Pixmap and embed
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
    for y in range(0, 300, 2):
        for x in range(0, 400, 2):
            pix.set_pixel(x, y, ((x * 7) % 256, (y * 3) % 256, 128))
    # Save temp jpeg and embed
    tmp_img = tmp_path / "large.jpeg"
    pix.save(str(tmp_img), output="jpg")
    page.insert_image(pymupdf.Rect(72, 100, 472, 400), filename=str(tmp_img))
    doc.save(str(pdf))
    doc.close()

    img_dir = tmp_path / "imgs4"
    pages = convert(pdf, img_dir, ocr=False)
    assert len(pages) == 1
    # Image should have been extracted via sidecar
    assert len(pages[0]["markdown"]["images"]) >= 1
    # Files should exist
    for rel, abs_path in pages[0]["markdown"]["images"].items():
        assert Path(abs_path).exists()


def test_get_bbox_sidecar(tmp_path):
    pdf = _mini_pdf(
        tmp_path / "bbox.pdf", ["Bbox test line 12,400 Lorem ipsum dolor sit amet " * 5]
    )
    sidecar = get_bbox_sidecar(pdf)
    assert len(sidecar) == 1
    assert "items" in sidecar[0]
    assert len(sidecar[0]["items"]) >= 1
    first = sidecar[0]["items"][0]
    assert "x" in first and "y" in first and "w" in first and "h" in first
    assert "text" in first


def test_convert_nocr_born_digital_reports(tmp_path):
    pdf = Path("Reports/SBI_Delhivery_Titan.pdf")
    if not pdf.exists():
        pytest.skip("missing Reports fixture")
    img_dir = tmp_path / "imgs5"
    pages = convert(pdf, img_dir, ocr=False)
    assert len(pages) == 28
    assert sum(len(p["markdown"]["text"]) for p in pages) > 40000
    # Images sidecar should have at least 3 figures (as pdf_local does)
    total_imgs = sum(len(p["markdown"]["images"]) for p in pages)
    assert total_imgs >= 3
