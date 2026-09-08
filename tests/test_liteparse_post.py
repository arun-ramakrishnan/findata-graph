"""Tests for helpers/pdf/liteparse_post.py — single-owner pin (S1).

Until 2026-09-08 liteparse_post was a full copy of pdf_local's
normalization block with a `# keep in sync` comment standing in for the
import (code_duplication_consolidation F2). These tests pin the
consolidation: the module imports pdf_local's primitives (identity) and
its `normalize` contract behaves on a fixture with every filter class
(page numbers, date lines, title repeats, dup URLs, glue headings).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import helpers.pdf.liteparse_post as lp  # noqa: E402
import helpers.pdf.pdf_local as pl  # noqa: E402


class TestSingleOwner:
    def test_primitives_are_pdf_locals(self):
        # Identity, not equality: a re-copy inside liteparse_post fails here.
        assert lp._filter_running_headers is pl._filter_running_headers
        assert lp._normalize_headings is pl._normalize_headings
        assert lp._strip_picture_text is pl._strip_picture_text
        assert lp.PIC_BLOCK_RE is pl.PIC_BLOCK_RE
        assert lp.CAP_TAIL_RE is pl.CAP_TAIL_RE
        assert lp.SECTOR_PREFIXES is pl.SECTOR_PREFIXES

    def test_reexports_satisfy_liteparse_markdown_import(self):
        # liteparse_markdown.py: `from helpers.pdf.liteparse_post import
        # CAP_TAIL_RE, SECTOR_PREFIXES` — the re-export surface contract.
        from helpers.pdf import liteparse_markdown

        assert liteparse_markdown.CAP_TAIL_RE is pl.CAP_TAIL_RE
        assert liteparse_markdown.SECTOR_PREFIXES is pl.SECTOR_PREFIXES


class TestNormalizeBehavior:
    FIXTURE = (
        "<!-- Start of picture text -->tess junk<!-- End of picture text -->\n"
        "# The Chatter: Fixtures & More\n"
        "3/22\n"
        "8/6/26, 8:32 AM\n"
        "# The Chatter: Fixtures & More\n"
        "https://example.com/very/long/path/that/exceeds/forty/chars/in/total\n"
        "https://example.com/very/long/path/that/exceeds/forty/chars/in/total\n"
        "## Engineering & Capital Goods Inox India | Small Cap | Engineering\n"
        "body line\n"
    )

    def test_all_filter_classes_fire(self):
        out = lp.normalize(self.FIXTURE)
        assert "tess junk" not in out  # picture block stripped
        # Title discovered from the first heading is CONSUMED by discovery:
        # every title line drops within this call (pdf_local semantics —
        # "first kept" applies across per-page calls via the returned title).
        assert "The Chatter: Fixtures & More" not in out
        assert "\n3/22\n" not in f"\n{out}"  # page number dropped
        assert "8/6/26" not in out  # short date line dropped
        assert out.count("https://example.com") == 1  # dup long URL dropped
        assert "## Engineering & Capital Goods\n## Inox India | Small Cap | Engineering" in out
        assert "body line" in out

    def test_title_discovery_then_passthrough(self):
        # The per-page contract: call 1 discovers the title; call 2 (later
        # page) receives it and suppresses the running header.
        page1 = "# The Chatter: Fixtures & More\ncontent one\n"
        text1, title = pl._filter_running_headers(page1, None)
        assert title == "The Chatter: Fixtures & More"
        page2 = f"# {title}\ncontent two\n"
        text2, _ = pl._filter_running_headers(page2, title)
        assert "content two" in text2 and title not in text2
