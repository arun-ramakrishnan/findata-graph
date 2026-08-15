"""Unit tests for helpers/pdf/capture_newsletter_images.py."""
from __future__ import annotations
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.pdf.capture_newsletter_images import (  # noqa: E402
    slugify,
    parse_images,
    assign_pages,
    is_valid_jpeg,
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
# parse_images
# ---------------------------------------------------------------------------
def test_parse_images_finds_blocks(tmp_path):
    md = tmp_path / "test.md"
    md.write_text(
        "<div class='img'><img src='https://example.com/crop_1_2/img.jpg'></div>\n"
        "<div class='img'><img src='https://example.com/crop_2_1/img.jpg'></div>"
    )
    images, text = parse_images(md)
    assert len(images) == 2
    assert images[0]["url"].startswith("https://example.com")
    assert images[0]["crop"] == 1
    assert images[0]["ts"] == 2
    assert images[0]["idx"] == 0
    assert images[1]["crop"] == 2


def test_parse_images_empty(tmp_path):
    md = tmp_path / "empty.md"
    md.write_text("# No images here")
    images, text = parse_images(md)
    assert images == []
    assert "No images" in text


def test_parse_images_line_numbers(tmp_path):
    md = tmp_path / "test.md"
    md.write_text("Line 1\nLine 2\n<div><img src='https://x.com/crop_1_1/a.jpg'></div>\nLine 4")
    images, _ = parse_images(md)
    assert images[0]["line"] == 3


def test_parse_images_no_crop_pattern(tmp_path):
    md = tmp_path / "test.md"
    md.write_text("<div><img src='https://x.com/plain.jpg'></div>")
    images, _ = parse_images(md)
    assert len(images) == 1
    assert images[0]["crop"] == 0
    assert images[0]["ts"] == 0


# ---------------------------------------------------------------------------
# assign_pages
# ---------------------------------------------------------------------------
def test_assign_pages_basic():
    images = [
        {"crop": 1},
        {"crop": 2},
        {"crop": 1},  # new page
        {"crop": 2},
    ]
    result = assign_pages(images)
    assert result[0]["page"] == 1
    assert result[1]["page"] == 1
    assert result[2]["page"] == 2
    assert result[3]["page"] == 2


def test_assign_pages_consecutive_crop1():
    images = [
        {"crop": 1},
        {"crop": 1},  # consecutive reset → new page
        {"crop": 2},
    ]
    result = assign_pages(images)
    assert result[0]["page"] == 1
    assert result[1]["page"] == 2
    assert result[2]["page"] == 2


def test_assign_pages_empty():
    assert assign_pages([]) == []


# ---------------------------------------------------------------------------
# is_valid_jpeg
# ---------------------------------------------------------------------------
def test_is_valid_jpeg_valid(tmp_path):
    p = tmp_path / "test.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"x" * 100)
    assert is_valid_jpeg(p) is True


def test_is_valid_jpeg_valid_png(tmp_path):
    p = tmp_path / "test.png"
    p.write_bytes(b"\x89PNG" + b"x" * 100)
    assert is_valid_jpeg(p) is True


def test_is_valid_jpeg_invalid(tmp_path):
    p = tmp_path / "test.bin"
    p.write_bytes(b"\x00\x00\x00\x00" + b"x" * 100)
    assert is_valid_jpeg(p) is False


def test_is_valid_jpeg_missing(tmp_path):
    assert is_valid_jpeg(tmp_path / "nonexistent.jpg") is False


def test_is_valid_jpeg_too_small(tmp_path):
    p = tmp_path / "tiny.jpg"
    p.write_bytes(b"\xff\xd8")
    assert is_valid_jpeg(p) is False
