"""Unit tests for helpers/pdf/pdf_conv_md.py (no network calls)."""

from __future__ import annotations


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
# OKF v0.2 provenance frontmatter (okf_adoption.md §2.2)
# ---------------------------------------------------------------------------
from helpers.core.frontmatter import moddate_to_iso_date, yaml_safe_load  # noqa: E402
from helpers.pdf.pdf_conv_md import (  # noqa: E402
    _pdf_metadata,
    build_okf_frontmatter,
    write_outputs,
)

_PAGES = [{"markdown": {"text": "# The Chatter: Bosch Edition\n\nprose", "images": {}}}]


class TestModdateToIsoDate:
    def test_full_with_positive_offset(self):
        assert moddate_to_iso_date("D:20260813123045+05'30'") == "2026-08-13"

    def test_offset_crossing_utc_day_backwards(self):
        # 00:30 IST on the 13th is 19:00 UTC on the 12th
        assert moddate_to_iso_date("D:20260813003000+05'30'") == "2026-08-12"

    def test_negative_offset_crossing_forward(self):
        # 23:59 PST on the 13th is 07:59 UTC on the 14th
        assert moddate_to_iso_date("D:20260813235959-08'00'") == "2026-08-14"

    def test_date_only(self):
        assert moddate_to_iso_date("D:20260813") == "2026-08-13"

    def test_no_offset(self):
        assert moddate_to_iso_date("D:20260813123045") == "2026-08-13"

    def test_poppler_human_readable_form(self):
        # what this corpus's Reports/*.pdf actually emit (verified 2026-08-18)
        assert moddate_to_iso_date("Thu Aug 13 09:01:08 2026 IST") == "2026-08-13"
        assert moddate_to_iso_date("Mon Aug 10 21:35:08 2026") == "2026-08-10"
        assert moddate_to_iso_date("Sun Aug 16 15:26:38 2026 IST") == "2026-08-16"

    def test_human_form_bad_month_rejected(self):
        assert moddate_to_iso_date("Thu Xyz 13 09:01:08 2026 IST") is None

    def test_garbage_and_none(self):
        assert moddate_to_iso_date("garbage") is None
        assert moddate_to_iso_date("") is None
        assert moddate_to_iso_date(None) is None
        assert moddate_to_iso_date("D:20261399") is None  # invalid month/day


class TestBuildOkfFrontmatter:
    def test_block_shape_and_actor(self, tmp_path):
        pdf = tmp_path / "elsewhere.pdf"  # NOT under Reports/ -> no sources
        fm = build_okf_frontmatter(
            _PAGES, pdf, "PP-StructureV3", "the_chatter", now="2026-08-18T09:00:00Z"
        )
        assert fm.startswith("---\n")
        data = yaml_safe_load(fm.split("\n---\n")[0][4:])
        assert data["type"] == "newsletter"
        assert data["title"] == "The Chatter: Bosch Edition"  # first heading
        assert data["generated"] == {
            "by": "pdf_conv_md.py/PP-StructureV3",
            "at": "2026-08-18T09:00:00Z",
        }
        assert "sources" not in data  # Q1 decision: only when PDF is in Reports/

    def test_sources_only_when_pdf_under_reports(self, tmp_path, monkeypatch):
        repo = tmp_path / "root"
        reports = repo / "Reports"
        reports.mkdir(parents=True)
        pdf = reports / "Bosch_Amara_Zydus.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(
            "helpers.pdf.pdf_conv_md._pdf_metadata",
            lambda p: {"Title": "The Chatter: Bosch", "ModDate": "D:20260813120000+05'30'"},
        )
        # _pdf_metadata is looked up inside build_okf_frontmatter via the
        # module global, so patch the module attribute directly.
        import helpers.pdf.pdf_conv_md as PCM

        monkeypatch.setattr(
            PCM,
            "_pdf_metadata",
            lambda p: {"Title": "The Chatter: Bosch", "ModDate": "D:20260813120000+05'30'"},
        )
        monkeypatch.setattr(PCM, "__file__", str(repo / "helpers/pdf/pdf_conv_md.py"))
        fm = build_okf_frontmatter(
            [], pdf, "PP-StructureV3", "bosch_amara_zydus", now="2026-08-18T09:00:00Z"
        )
        data = yaml_safe_load(fm.split("\n---\n")[0][4:])
        assert data["sources"] == [
            {
                "id": "bosch_amara_zydus",
                "resource": "/Reports/Bosch_Amara_Zydus.pdf",
                "title": "The Chatter: Bosch",
                "author": "process:pdf_conv_md",
                "last_modified": "2026-08-13",
            }
        ]

    def test_title_falls_back_to_stem_without_headings(self, tmp_path):
        pdf = tmp_path / "x.pdf"
        fm = build_okf_frontmatter([{"markdown": {"text": "no headings"}}], pdf, "M", "my_edition")
        data = yaml_safe_load(fm.split("\n---\n")[0][4:])
        assert data["title"] == "my_edition"

    def test_title_prefers_pdf_metadata_over_headings(self, tmp_path, monkeypatch):
        # The first-H1 heuristic grabs whatever heading the layout emits
        # first (a sector header like "FMCG"); the PDF's own Title is the
        # edition's real display title and wins when pdfinfo has one.
        import helpers.pdf.pdf_conv_md as PCM

        monkeypatch.setattr(PCM, "_pdf_metadata", lambda p: {"Title": "The Chatter: Real Title"})
        fm = build_okf_frontmatter(_PAGES, tmp_path / "x.pdf", "M", "stem")
        data = yaml_safe_load(fm.split("\n---\n")[0][4:])
        assert data["title"] == "The Chatter: Real Title"

    def test_title_falls_back_to_first_heading_without_metadata(self, tmp_path, monkeypatch):
        import helpers.pdf.pdf_conv_md as PCM

        monkeypatch.setattr(PCM, "_pdf_metadata", lambda p: {})
        fm = build_okf_frontmatter(_PAGES, tmp_path / "x.pdf", "M", "stem")
        data = yaml_safe_load(fm.split("\n---\n")[0][4:])
        assert data["title"] == "The Chatter: Bosch Edition"

    def test_tags_series_and_publisher_for_known_dir(self, tmp_path):
        pdf = tmp_path / "x.pdf"
        fm = build_okf_frontmatter([], pdf, "M", "ed", out_dir="/vault/findata/The_PlotLines")
        data = yaml_safe_load(fm.split("\n---\n")[0][4:])
        assert data["tags"] == ["series/the_plotlines", "publisher/zerodha"]

    def test_tags_series_only_for_unknown_dir(self, tmp_path):
        # Accepted Q1: publisher omitted when the series is not in the map.
        pdf = tmp_path / "x.pdf"
        fm = build_okf_frontmatter([], pdf, "M", "ed", out_dir=tmp_path / "Future_Series")
        data = yaml_safe_load(fm.split("\n---\n")[0][4:])
        assert data["tags"] == ["series/future_series"]

    def test_no_tags_without_out_dir(self, tmp_path):
        pdf = tmp_path / "x.pdf"
        fm = build_okf_frontmatter([], pdf, "M", "ed")
        data = yaml_safe_load(fm.split("\n---\n")[0][4:])
        assert "tags" not in data

    def test_pdf_metadata_tooling(self, tmp_path):
        pdf = tmp_path / "sample.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        meta = _pdf_metadata(pdf)
        # poppler parses even a stub; assert only the contract shape
        assert isinstance(meta, dict)
        assert set(meta) <= {"Title", "ModDate"}


class TestWriteOutputsFrontmatter:
    def test_frontmatter_prepended_to_md(self, tmp_path):
        pages = [{"markdown": {"text": "# Hello", "images": {}}}]
        write_outputs(
            pages, tmp_path, "hello", fetch_images=False, frontmatter="---\ntype: newsletter\n---\n"
        )
        md = (tmp_path / "hello.md").read_text()
        assert md.startswith("---\ntype: newsletter\n---\n\n# Hello")

    def test_no_frontmatter_keeps_legacy_shape(self, tmp_path):
        pages = [{"markdown": {"text": "# Hello", "images": {}}}]
        write_outputs(pages, tmp_path, "hello", fetch_images=False)
        assert (tmp_path / "hello.md").read_text().startswith("# Hello")


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


# ---------------------------------------------------------------------------
# write_outputs: local-engine image copy branch (no network)
# ---------------------------------------------------------------------------
def test_write_outputs_copies_local_engine_images(tmp_path):
    src = tmp_path / "raw_img.jpeg"
    src.write_bytes(b"\xff\xd8fake-jpeg-bytes")
    pages = [
        {
            "prunedResult": None,
            "markdown": {
                "text": '<div style="text-align: center;"><img src="imgs/img1"/></div>',
                "images": {"imgs/img1": str(src)},
            },
            "outputImages": [],
            "inputImage": None,
        }
    ]
    write_outputs(pages, tmp_path / "out", "note", fetch_images=True)
    copied = tmp_path / "out" / "images" / "note_p1_img1.jpeg"
    assert copied.read_bytes() == b"\xff\xd8fake-jpeg-bytes"
    md = (tmp_path / "out" / "note.md").read_text(encoding="utf-8")
    assert "![[images/note_p1_img1.jpeg]]" in md
