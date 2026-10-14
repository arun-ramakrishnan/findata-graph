"""Unit tests for helpers/pdf/verify_extraction.py (no network calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf


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


# --------------------------------------------------------------------------- #
# Range review M2 / M3 / L2 / L3 (2026-10-14)
# --------------------------------------------------------------------------- #


class TestSignificantDigits:
    """L3: NUM_RE matches "1.5", which is 3 CHARACTERS but 2 digits. Counting
    characters promoted two-digit figures into "significant" missing numbers."""

    def test_ignores_separators_and_decimal_point(self):
        from helpers.pdf.verify_extraction import _significant_digits

        assert _significant_digits("1.5") == 2
        assert _significant_digits("1,234") == 4
        assert _significant_digits("99,999") == 5
        assert _significant_digits("12") == 2
        assert _significant_digits("7") == 1

    def test_threshold_now_excludes_two_digit_fractions(self):
        from helpers.pdf.verify_extraction import (
            MIN_SIGNIFICANT_NUM_DIGITS,
            _significant_digits,
        )

        # "1.5" drops out (2 digits); "1.05" and "999" stay (3+).
        missing_all = ["1.5", "1.05", "999"]
        kept = [n for n in missing_all if _significant_digits(n) >= MIN_SIGNIFICANT_NUM_DIGITS]
        assert kept == ["1.05", "999"]
        assert "1.5" not in kept


class TestFrontmatterStripping:
    """M2: the frontmatter regex had no re.S, so `.` never matched a newline
    and multi-line YAML frontmatter was counted as document content."""

    def test_multiline_frontmatter_is_stripped(self):
        import re

        md = "---\nentity_type: company\nticker: ABC\nsector: X\n---\n\nbody text\n"
        stripped = re.sub(r"\A---\n.*?\n---\n", "", md, count=1, flags=re.DOTALL)
        assert "entity_type" not in stripped
        assert "body text" in stripped

    def test_the_verifier_applies_dotall(self):
        import inspect

        from helpers.pdf import verify_extraction

        src = inspect.getsource(verify_extraction)
        assert "re.DOTALL" in src, "frontmatter strip regressed to non-DOTALL"


class TestWikilinkContainment:
    """M3: WIKILINK_RE captured "../../../etc/passwd" and the check then
    stat'ed a file outside the output directory — a read-only existence
    oracle. wikilink_status refuses the traversal instead of answering it."""

    def test_traversal_is_refused_even_when_the_target_exists(self, tmp_path):
        from helpers.pdf.verify_extraction import WIKILINK_RE, wikilink_status

        images = tmp_path / "out" / "images"
        images.mkdir(parents=True)
        # the traversal target genuinely exists — the old code stat'd it and
        # reported "ok", which is the oracle
        (tmp_path / "outside.md").write_text("secret", encoding="utf-8")

        body = "![[images/../outside.md]]"
        link = WIKILINK_RE.findall(body)[0]
        assert link == "../outside.md"
        assert wikilink_status(images.resolve(), link) == "escape"

    def test_plain_missing_image_is_missing_not_escape(self, tmp_path):
        from helpers.pdf.verify_extraction import wikilink_status

        images = tmp_path / "out" / "images"
        images.mkdir(parents=True)
        assert wikilink_status(images.resolve(), "nope.png") == "missing"

    def test_present_image_is_ok(self, tmp_path):
        from helpers.pdf.verify_extraction import wikilink_status

        images = tmp_path / "out" / "images"
        images.mkdir(parents=True)
        (images / "a.png").write_bytes(b"x")
        assert wikilink_status(images.resolve(), "a.png") == "ok"

    def test_deep_traversal_is_refused(self, tmp_path):
        from helpers.pdf.verify_extraction import wikilink_status

        images = tmp_path / "out" / "images"
        images.mkdir(parents=True)
        assert wikilink_status(images.resolve(), "../../../etc/passwd") == "escape"

    def test_verify_surfaces_escapes_as_an_explicit_fail(self, tmp_path):
        (tmp_path / "outside.md").write_text("secret", encoding="utf-8")
        out = tmp_path / "out"
        (out / "images").mkdir(parents=True)
        (out / "doc.md").write_text("![[images/../outside.md]]\n\nbody\n", encoding="utf-8")
        (out / "doc.json").write_text(json.dumps({"pages": []}), encoding="utf-8")
        (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4\n")

        import inspect

        from helpers.pdf import verify_extraction

        src = inspect.getsource(verify_extraction.verify)
        # the escape class must gate the verdict, not just be tallied
        assert "escaped_links or" in src
