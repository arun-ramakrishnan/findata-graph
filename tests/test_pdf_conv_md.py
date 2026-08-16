"""Unit tests for helpers/pdf/pdf_conv_md.py (no network calls)."""
from __future__ import annotations
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.pdf.pdf_conv_md import (  # noqa: E402
    parse_pages,
    plan_images,
    resolve_markdown,
    slugify,
    to_wikilinks,
)


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------
def test_slugify_basic():
    assert slugify("Newsletter 2024 01") == "Newsletter_2024_01"


def test_slugify_collapse_underscores():
    assert slugify("A  B") == "A_B"


def test_slugify_strip_leading_trailing():
    assert slugify("  hello  ") == "hello"


def test_slugify_no_change_already_clean():
    assert slugify("Already_Clean") == "Already_Clean"


# ---------------------------------------------------------------------------
# parse_pages
# ---------------------------------------------------------------------------
def _line(text="page text", images=None):
    return {
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {"parsing_res_list": []},
                    "markdown": {"text": text, "images": images or {}},
                    "outputImages": {},
                    "inputImage": "https://x/img.png",
                }
            ]
        }
    }


def test_parse_pages_extracts_one_page_per_line():
    pages = parse_pages([_line("a"), _line("b"), _line("c")])
    assert len(pages) == 3
    assert pages[0]["markdown"]["text"] == "a"
    assert pages[1]["markdown"]["text"] == "b"


def test_parse_pages_single_lpr_per_line():
    pages = parse_pages([_line("x")])
    assert set(pages[0]) == {"prunedResult", "markdown", "outputImages", "inputImage"}


def test_parse_pages_empty():
    assert parse_pages([]) == []


# ---------------------------------------------------------------------------
# plan_images
# ---------------------------------------------------------------------------
def test_plan_images_sequential_counter():
    p1, c = plan_images(1, {"imgs/a.jpg": "https://x/a"}, 0, "Doc")
    p2, c2 = plan_images(2, {"imgs/b.jpg": "https://x/b"}, c, "Doc")
    assert p1["imgs/a.jpg"]["filename"] == "Doc_p1_img1.jpeg"
    assert p2["imgs/b.jpg"]["filename"] == "Doc_p2_img2.jpeg"
    assert c2 == 2


def test_plan_images_png_ext():
    p, c = plan_images(1, {"imgs/a.png": "https://x/a.png"}, 0, "Doc")
    assert p["imgs/a.png"]["filename"] == "Doc_p1_img1.png"


def test_plan_images_empty():
    assert plan_images(1, {}, 5, "Doc") == ({}, 5)


# ---------------------------------------------------------------------------
# to_wikilinks
# ---------------------------------------------------------------------------
def test_to_wikilinks_replaces_centered_div():
    md = '<div style="text-align: center;"><img src="imgs/a.jpg" alt="Image" width="4%" /></div>'
    plan = {"imgs/a.jpg": {"filename": "Doc_p1_img1.jpeg", "url": "https://x/a"}}
    assert to_wikilinks(md, plan) == "![[images/Doc_p1_img1.jpeg]]"


def test_to_wikilinks_leaves_unknown_imgs():
    md = '<div style="text-align: center;"><img src="imgs/unknown.jpg" /></div>'
    assert to_wikilinks(md, {}) == md


def test_to_wikilinks_no_imgs():
    md = "# hello"
    assert to_wikilinks(md, {}) == md


# ---------------------------------------------------------------------------
# resolve_markdown
# ---------------------------------------------------------------------------
def test_resolve_markdown_rewrites_imgs():
    md = '<div><img src="imgs/img_in_image_box_1_2_3_4.jpg" alt="Image" width="10%" /></div>'
    images = {"imgs/img_in_image_box_1_2_3_4.jpg": "https://cdn.example.com/full.jpg"}
    out = resolve_markdown(md, images)
    assert 'src="https://cdn.example.com/full.jpg"' in out
    assert "imgs/" not in out


def test_resolve_markdown_leaves_unknown_imgs():
    md = '<img src="imgs/unknown.jpg" />'
    assert resolve_markdown(md, {}) == md


def test_resolve_markdown_no_imgs():
    md = "# hello"
    assert resolve_markdown(md, {}) == md
