#!/usr/bin/env python3
"""S4 sector capture tests (quote_capture_coverage proposal).

Covers: sector routing ladder (canonical -> synonym -> catch-all),
iter_sector_sections boundary rule, edition-note (G5) rows, scan
integration (properties.kind/heading), CANONICAL_SECTORS drift-pin
against static_checks, and the sector-note render path (insertion
before `## Newsletter synthesis`, curation-safety, marker balance).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from helpers.graph import derive_insights as di  # noqa: E402

NOTE = (
    "---\n"
    "title: Test Edition\n"
    "---\n"
    "# The Chatter: Test\n"
    '"Masthead quote line that is definitely long enough to count."\n'
    "## Software\n"
    '"Software-sector quote line that is long enough to count now."\n'
    "## Acme Ltd | Large Cap | Software\n"
    '"Company quote line that is definitely long enough to count."\n'
    "## Tourism &amp; Hospitality\n"
    '"Tourism-sector quote line that is long enough to count here."\n'
    "## Design & Product Development Platform\n"
    '"Catch-all quote line that is definitely long enough to count."\n'
)


class TestResolveSector:
    def test_canonical_exact(self):
        assert di._resolve_sector("Retail") == ("Retail", "sector_commentary")
        assert di._resolve_sector("FMCG") == ("FMCG", "sector_commentary")

    def test_synonym(self):
        assert di._resolve_sector("Software") == ("Technology", "sector_commentary")
        assert di._resolve_sector("AI Computing") == ("Technology", "sector_commentary")
        assert di._resolve_sector("Tourism &amp; Hospitality") == (
            "Travel",
            "sector_commentary",
        )

    def test_catch_all(self):
        ent, kind = di._resolve_sector("Design & Product Development Platform")
        assert (ent, kind) == ("Quotes", "catch_all")


class TestIterSectorSections:
    def test_regions_routes_and_raw_heading(self):
        pairs = {}
        kinds = {}
        for sec, raw, kind in di.iter_sector_sections(NOTE):
            pairs[raw] = sec.canonical_name
            kinds[raw] = kind
        assert pairs["Software"] == "Technology"
        assert pairs["Tourism & Hospitality"] == "Travel"  # html-unescaped
        assert pairs["Design & Product Development Platform"] == "Quotes"
        assert kinds["Design & Product Development Platform"] == "catch_all"
        assert kinds["Software"] == "sector_commentary"

    def test_boundary_sector_body_runs_to_next_heading(self):
        # The Software region ends at the Acme company heading.
        for sec, raw, kind in di.iter_sector_sections(NOTE):
            if raw == "Software":
                assert "Company quote line" not in sec.body
                assert "Software-sector quote line" in sec.body

    def test_markers_never_split_sector_regions(self):
        note = "## Software\ntext\n## [Transcript]\ntext\n## Retail\nmore\n"
        regions = [raw for _, raw, _ in di.iter_sector_sections(note)]
        assert regions == ["Software", "Retail"]  # marker absorbed, Retail next


class TestEditionNote:
    def test_preamble_routes_to_edition_entity(self):
        secs = list(di.iter_edition_note_section(NOTE, "Test_Stem"))
        assert len(secs) == 1
        assert secs[0].canonical_name == "Test_Stem"
        assert "Masthead quote line" in secs[0].body

    def test_scan_content_sets_kinds(self):
        quotes, _ = di._scan_content(NOTE, "Test_Stem", "Test Edition", {})
        by_kind = {}
        for q in quotes:
            by_kind.setdefault(q.properties.get("kind"), []).append(q)
        assert "edition_note" in by_kind
        assert by_kind["edition_note"][0].entity == "Test_Stem"
        assert "sector_commentary" in by_kind
        assert "catch_all" in by_kind
        assert by_kind["catch_all"][0].properties["heading"] == (
            "Design & Product Development Platform"
        )
        # Company rows carry NO kind (reports join on entity_type).
        assert all("kind" not in q.properties for q in by_kind.get(None, []))


class TestCanonicalSectorsParity:
    def test_frozen_mirror_matches_static_checks(self):
        from helpers.validators.static_checks import CANONICAL_SECTORS

        assert di._CANONICAL_SECTORS == set(CANONICAL_SECTORS)


class TestSectorRender:
    @staticmethod
    def _setup(tmp_path, monkeypatch):
        sector_note = tmp_path / "findata" / "Sectors" / "Travel.md"
        sector_note.parent.mkdir(parents=True, exist_ok=True)
        sector_note.write_text(
            "# Travel\n\n"
            "<!-- BEGIN auto company index (sync_sector_wikilinks.py) -->\n"
            "## All Companies (auto)\n"
            "<!-- END auto company index -->\n"
            "## Newsletter synthesis — Travel (multi-edition)\n"
        )
        db_path = tmp_path / "test.db"
        init = sqlite3.connect(db_path)
        init.row_factory = sqlite3.Row
        init.execute("CREATE TABLE entities (name TEXT, entity_type TEXT, file_path TEXT)")
        init.execute("INSERT INTO entities VALUES ('Travel','sector','findata/Sectors/Travel.md')")
        init.commit()
        init.close()

        def _fresh():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(di, "PROJECT_ROOT", tmp_path)
        return _fresh, sector_note

    def test_sector_block_inserts_before_synthesis(self, tmp_path, monkeypatch):
        _fresh, sector_note = self._setup(tmp_path, monkeypatch)
        q = di.Quote(
            entity="Travel",
            quote_text="Sector quote line long enough to be stored as a row.",
            as_of_edition="Test Edition",
            properties={
                "kind": "sector_commentary",
                "heading": "Tourism & Hospitality",
            },
        )
        conn = _fresh()
        written, skipped, gated = di.render_notes(
            {("Travel", "Test Edition"): [q]}, dry_run=False, conn=conn
        )
        conn.close()
        assert written == 1
        text = sector_note.read_text()
        assert "## The Chatter — Test Edition" in text
        # Insertion point: BEFORE the Newsletter synthesis heading.
        assert text.index("## The Chatter") < text.index("## Newsletter synthesis")
        # Provenance: the raw heading travels into the note block.
        assert "Tourism & Hospitality" in text

    def test_hand_written_sector_block_preserved(self, tmp_path, monkeypatch):
        _fresh, sector_note = self._setup(tmp_path, monkeypatch)
        text0 = sector_note.read_text() + "\n## The Chatter — Test Edition\n\nHand-written.\n"
        sector_note.write_text(text0)
        q = di.Quote(
            entity="Travel",
            quote_text="Sector quote line long enough to be stored as a row.",
            as_of_edition="Test Edition",
        )
        conn = _fresh()
        di.render_notes({("Travel", "Test Edition"): [q]}, dry_run=False, conn=conn)
        conn.close()
        # Curation-safety: the hand block survives untouched.
        assert "Hand-written." in sector_note.read_text()


class TestG4secSynonyms:
    """g4sec Class B (2026-09-07): `Software Services` -> Technology,
    `Regulator` -> Banking (RBI is bank-regulator commentary)."""

    def test_software_services_routes_technology(self):
        assert di._resolve_sector("Software Services") == (
            "Technology",
            "sector_commentary",
        )

    def test_regulator_routes_banking(self):
        assert di._resolve_sector("Regulator") == ("Banking", "sector_commentary")


class TestQuoteExtractorHardening:
    """quote_extractor_hardening (2026-11-23): the two live defect classes
    from the IBM_Tata_Tech census — quotes.id 24674 (PDF line-wrap landed an
    embedded quoted phrase at a line end; the closer fired on the first
    physical line, truncating the text and losing the speaker) and 24698
    (masthead prose carrying a brand-name quoted span opened a junk unit
    attributed to the edition entity, with frontmatter in the paraphrase)."""

    @staticmethod
    def _section(body: str) -> di.CompanySection:
        return di.CompanySection(canonical_name="Acme Ltd", heading_line=1, body=body)

    def test_embedded_span_wrap_continues_to_attribution(self):
        # 24674 shape: opener line ends on an embedded quoted phrase
        # (odd parity, empty tail) — must NOT close; the run continues
        # through the attribution line.
        body = (
            "_\u201cBoth are true, and let me break down why. There are three "
            "\u201cno-regrets, low-risk\u201d_\n"
            "_where today\u2019s AI is already delivering indisputable enterprise "
            "value across the board._\n"
            "_\u2014 Arvind Krishna, Chairman & CEO, IBM_\n"
        )
        quotes = di.extract_quotes(self._section(body), "Test Edition", "Test_Stem")
        assert len(quotes) == 1
        q = quotes[0]
        assert "no-regrets, low-risk" in q.quote_text
        assert "enterprise value across the board" in q.quote_text
        assert q.speaker_name == "Arvind Krishna"
        assert q.speaker_title == "Chairman & CEO, IBM"

    def test_embedded_span_wrap_mid_run_parity_close(self):
        # S1 multiline leg: a line-ending `"` mid-run closes only at even
        # cumulative parity; the true closer flips it. Continuation lines
        # carry no leading quote (per-line emphasis strips it).
        body = (
            '"the first line of the run carries an "embedded phrase"\n'
            'second line finishes the thought."\n'
            "\u2014 Full Name, Title\n"
        )
        quotes = di.extract_quotes(self._section(body), "Test Edition", "Test_Stem")
        assert len(quotes) == 1
        assert "first line of the run" in quotes[0].quote_text
        assert "finishes the thought" in quotes[0].quote_text
        assert quotes[0].speaker_name == "Full Name"

    def test_even_parity_single_line_still_closes(self):
        # Regression guard: a genuine one-line quote whose embedded pair
        # keeps even parity still closes same-line.
        body = '"He said "run" fast, faster than anyone expected." \u2014 Head Coach, Team\n'
        quotes = di.extract_quotes(self._section(body), "Test Edition", "Test_Stem")
        assert len(quotes) == 1
        # quote_text carries no surrounding quotes (house contract); the
        # embedded pair is preserved.
        assert quotes[0].quote_text == 'He said "run" fast, faster than anyone expected.'
        assert quotes[0].speaker_name == "Head Coach"

    def test_masthead_brand_line_does_not_open_quote(self):
        # 24698 shape: exactly one balanced quoted span with non-attrish
        # prose continuing after the close — chrome prose, not an opener.
        body = (
            "## IBM | Large Cap | Technology\n"
            "We have migrated \u201cThe Chatter\u201d format to better serve "
            "your needs.\n"
            '"A real quote follows the masthead line and is long enough."\n'
            "\u2014 Speaker, Company\n"
        )
        quotes = di.extract_quotes(self._section(body), "Test Edition", "Test_Stem")
        assert len(quotes) == 1
        assert "better serve" not in quotes[0].quote_text
        assert "real quote follows" in quotes[0].quote_text

    def test_edition_note_strips_frontmatter(self):
        note = (
            "---\n"
            "type: newsletter\n"
            "title: The Chatter\n"
            "---\n"
            "# The Chatter\n"
            "Masthead prose before the first heading.\n"
            "## Software\n"
            '"Sector quote line that is long enough to count now."\n'
        )
        secs = list(di.iter_edition_note_section(note, "Test_Stem"))
        # Frontmatter stripped: the preamble body carries no YAML keys.
        for s in secs:
            assert "type: newsletter" not in s.body
            assert not s.body.lstrip().startswith("---")
