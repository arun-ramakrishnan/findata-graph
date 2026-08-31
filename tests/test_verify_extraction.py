"""Unit tests for helpers/pdf/verify_extraction.py (no network calls)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.pdf.verify_extraction import (  # noqa: E402
    canon_numbers,
    canon_words,
    verify,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _text_pdf(path: Path, pages: list[str]) -> Path:
    """Build a PDF whose get_text() round-trips words cleanly.

    Chunks each page's text at WORD boundaries (fixed-width cuts split
    words, and pymupdf reads them back with spurious spaces).
    """
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        words, line, y = text.split(), [], 72
        for w in words:
            if len(" ".join(line + [w])) > 88:
                page.insert_text((72, y), " ".join(line), fontname="helv", fontsize=11)
                line, y = [], y + 14
            line.append(w)
        if line:
            page.insert_text((72, y), " ".join(line), fontname="helv", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _write_outputs(out_dir: Path, stem: str, page_texts: list[str]) -> Path:
    """Minimal <stem>.md/.json pair shaped like pdf_conv_md output."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = [
        {
            "prunedResult": None,
            "markdown": {"text": t, "images": {}},
            "outputImages": [],
            "inputImage": None,
        }
        for t in page_texts
    ]
    (out_dir / f"{stem}.json").write_text(json.dumps(pages))
    md = "\n\n".join(page_texts)
    (out_dir / f"{stem}.md").write_text(f"---\ntype: newsletter\n---\n{md}")
    return out_dir / f"{stem}.md"


FILLER = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do "
    "eiusmod tempor incididunt ut labore et dolore magna aliqua ut enim "
    "ad minim veniam quis nostrud exercitation ullamco laboris nisi "
)


def _content_page(extra: str = "") -> str:
    return FILLER * 6 + extra


# ---------------------------------------------------------------------------
# canon helpers
# ---------------------------------------------------------------------------
def test_canon_words():
    assert canon_words("Hello, World! HELLO world") == ["hello", "world", "hello", "world"]


def test_canon_numbers_comma_insensitive():
    assert canon_numbers("14,000 and 14000") == ["14000", "14000"]


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------
def test_pass_on_clean_round_trip(tmp_path):
    body = _content_page("Revenue grew 12,345 crore in FY27.")
    pdf = _text_pdf(tmp_path / "doc.pdf", [body])
    _write_outputs(tmp_path / "out", "doc", [body])
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["verdict"] == "PASS", m
    assert m["doc_coverage"] > 0.95
    assert (tmp_path / "out" / "doc.verify.json").is_file()


def test_fail_on_gutted_md(tmp_path):
    body = _content_page("Revenue grew 12,345 crore in FY27 across segments.")
    pdf = _text_pdf(tmp_path / "doc.pdf", [body])
    _write_outputs(tmp_path / "out", "doc", [body])
    # rewrite the md with most of the body removed
    (tmp_path / "out" / "doc.md").write_text("---\ntype: newsletter\n---\nshort stub\n")
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["verdict"] == "FAIL"
    assert m["doc_coverage"] < 0.90


def test_fail_on_missing_wikilink(tmp_path):
    body = _content_page()
    pdf = _text_pdf(tmp_path / "doc.pdf", [body])
    _write_outputs(tmp_path / "out", "doc", [body])
    (tmp_path / "out" / "doc.md").write_text(
        "---\ntype: newsletter\n---\n" + body + "\n![[images/doc_p1_img1.jpeg]]\n"
    )
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["verdict"] == "FAIL"
    assert m["missing_wikilinks"] == ["doc_p1_img1.jpeg"]


def test_warn_on_missing_significant_number(tmp_path):
    body = _content_page("Guidance: 25,000-27,000 units next year.")
    pdf = _text_pdf(tmp_path / "doc.pdf", [body])
    # output lost the range numbers (dash-join artifact)
    _write_outputs(tmp_path / "out", "doc", [body.replace("25,000-27,000", "2500027000")])
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["verdict"] == "WARN"
    assert "25000" in m["missing_numbers"] and "27000" in m["missing_numbers"]


def test_small_numbers_are_noise_not_warn(tmp_path):
    body = _content_page("Flat 20 on trades, page 7 of 31.")
    pdf = _text_pdf(tmp_path / "doc.pdf", [body])
    # ad text deliberately dropped by the engine
    _write_outputs(tmp_path / "out", "doc", [body.replace("Flat 20 on trades, page 7 of 31.", "")])
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["missing_numbers"] == []
    assert m["small_number_noise_count"] >= 2


def test_tiny_ad_pages_skip_page_thresholds(tmp_path):
    big = _content_page()
    tiny_ad = "Subscribe now! Invest for free today."  # < MIN_PAGE_WORDS
    pdf = _text_pdf(tmp_path / "doc.pdf", [big, tiny_ad])
    # ad page mostly dropped (coverage ~0.2) — must not FAIL/WARN the page
    _write_outputs(tmp_path / "out", "doc", [big, "Subscribe now!"])
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["verdict"] == "PASS", m["pages"][1]


# ---------------------------------------------------------------------------
# md <-> json consistency (both artifacts are checked)
# ---------------------------------------------------------------------------
def test_md_json_consistent_on_round_trip(tmp_path):
    body = _content_page("Numbers like 45,678 survive rendering.")
    pdf = _text_pdf(tmp_path / "doc.pdf", [body])
    _write_outputs(tmp_path / "out", "doc", [body])
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["md_json_mismatch_pages"] == []
    assert all(p["md_coverage"] == 1.0 for p in m["md_json_pages"])


def test_md_json_mismatch_when_md_only_is_gutted(tmp_path):
    body = _content_page("Segment with figures 45,678 crore.")
    pdf = _text_pdf(tmp_path / "doc.pdf", [body])
    _write_outputs(tmp_path / "out", "doc", [body])
    # rewrite ONLY the md (json stays intact): rendering-stage loss
    (tmp_path / "out" / "doc.md").write_text(
        "---\ntype: newsletter\n---\n" + _content_page("unrelated words\n")
    )
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["verdict"] == "FAIL"
    assert m["md_json_mismatch_pages"] == [1]
    assert m["md_json_pages"][0]["md_coverage"] < 0.98


def test_md_json_ignores_image_markup_differences(tmp_path):
    # json carries <div><img src="imgs/img1"/></div>; md carries the
    # wikilink instead — image markup must not count as a mismatch
    body = _content_page()
    pdf = _text_pdf(tmp_path / "doc.pdf", [body])
    pages = [
        {
            "prunedResult": None,
            "markdown": {
                "text": body + '\n<div style="text-align: center;"><img src="imgs/img1"/></div>',
                "images": {"imgs/img1": "http://x/img1.jpeg"},
            },
            "outputImages": [],
            "inputImage": None,
        }
    ]
    (tmp_path / "out").mkdir(parents=True)
    (tmp_path / "out" / "doc.json").write_text(json.dumps(pages))
    (tmp_path / "out" / "doc.md").write_text(
        "---\ntype: newsletter\n---\n" + body + "\n![[images/doc_p1_img1.jpeg]]\n"
    )
    (tmp_path / "out" / "images").mkdir()
    (tmp_path / "out" / "images" / "doc_p1_img1.jpeg").write_bytes(b"\xff\xd8jpg")
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["md_json_mismatch_pages"] == []
    assert m["verdict"] == "PASS", m


def test_fail_on_dropped_content_page(tmp_path):
    p1 = _content_page()
    p2 = _content_page("Segment two details with figures 45,678.")
    pdf = _text_pdf(tmp_path / "doc.pdf", [p1, p2])
    # page 2 extracted as EMPTY — silent drop, must FAIL
    _write_outputs(tmp_path / "out", "doc", [p1, ""])
    m = verify(pdf, tmp_path / "out", "doc")
    assert m["verdict"] == "FAIL"
    assert m["pages"][1]["coverage"] < 0.50
