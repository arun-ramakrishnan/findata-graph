#!/usr/bin/env python3
"""Tests for helpers/graph/derive_insights.py — the concall-body capture layer.

Two layers, mirroring test_derive_events.py:
  * The pure extractors (iter_company_sections / extract_quotes /
    extract_metrics / render_chatter_block / _replace_or_insert_block) are
    pure functions over text — these pin the PARSING contract: which lines are
    sections, quotes, attributions, and magnitudes, and which are rejected.
  * apply_quotes / apply_metrics hit a temp SQLite DB — these pin the
    DELETE-then-INSERT idempotency + manual-row preservation contract.

The two headline correctness properties (each has a dedicated test class):
  - CURATION-SAFETY: a hand-written `## The Chatter — <edition>` block is
    NEVER clobbered by the auto block. This is the critical property — the
    auto pass must only fill empty notes, never overwrite human/agent work.
  - ATTRIBUTION COVERAGE: every observed attribution-line form in the corpus
    parses to (name, title). The corpus survey found 7+ forms.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph import derive_insights as di  # noqa: E402


# --------------------------------------------------------------------------- #
# Minimal schema for DB-backed tests (entities + quotes + company_metrics).   #
# --------------------------------------------------------------------------- #
def _schema_sql():
    return """
    CREATE TABLE entities(
        name TEXT PRIMARY KEY,
        entity_type TEXT,
        normalized_name TEXT,
        file_path TEXT,
        last_updated TEXT
    );
    CREATE TABLE quotes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity TEXT NOT NULL,
        quote_text TEXT NOT NULL,
        paraphrase TEXT,
        speaker_name TEXT,
        speaker_title TEXT,
        as_of_edition TEXT,
        source_ref TEXT NOT NULL,
        properties TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(entity, quote_text, as_of_edition)
    );
    CREATE TABLE company_metrics(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity TEXT NOT NULL,
        metric_label TEXT,
        value_raw TEXT NOT NULL,
        value_num REAL,
        unit TEXT,
        period TEXT,
        as_of_edition TEXT,
        source_quote TEXT,
        source_ref TEXT NOT NULL,
        properties TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


def _connect(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "test_insights.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(_schema_sql())
    return conn


# A representative two-company newsletter fragment with every attribution form.
_SAMPLE_NEWSLETTER = """\
# Marico DLF BSE

Welcome to the 76th edition of The Chatter.

## FMCG

## Marico Ltd. | Large Cap | FMCG

Marico is a leading Indian consumer goods company.

## [Concall]

The flagship Parachute brand achieved a five-year high in volume growth.

"Parachute Rigids delivered 10% volume growth, its strongest performance in the last 20 quarters, and gained more than 400 basis points in volume share, marking a new high."

## — Saugata Gupta, MD & CEO

The company remains committed to its long-term vision.

"With copra prices having corrected by about 35% from peak levels, we expect prices to remain range-bound."

— Saugata Gupta, MD & CEO

Cold-pressed oils offer significantly higher profit margins.

"I believe cold-pressed oil is a category of the future, and therefore we are investing in it."

-Saugata Gupta, MD & CEO

Margin defense is the priority.

"We will try to hold the gross margin percentage at the level of last year."

Badal Bagri, Group CFO

## Real Estate

## DLF | Large Cap | Real Estate

DLF is a real estate development company.

## [Concall]

The flagship ultra-luxury project has been a remarkable success.

"Dahlias has been the biggest success so far over the last eighteen months. We are almost 65% sold."

-Akash Ohri, Managing Director & Chief Business Officer

## Discussion about this post

Leave a comment.
"""


# --------------------------------------------------------------------------- #
# Segmenter                                                                   #
# --------------------------------------------------------------------------- #
class TestSegmenter:
    def test_finds_each_company_section(self):
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        names = [s.canonical_name for s in sections]
        assert "Marico" in names
        assert "DLF" in names

    def test_section_body_spans_concall_subheadings(self):
        """The body must include the [Concall] sub-heading and everything up
        to the next STRUCTURAL heading (sector/company), not break at every
        `##`. This was the original bug: body was 4 lines, missing all quotes."""
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        marico = next(s for s in sections if s.canonical_name == "Marico")
        # The body must contain the verbatim quote text (proves it spans the
        # full concall block, not just the heading + descriptor).
        assert "Parachute Rigids delivered 10% volume growth" in marico.body
        assert "## [Concall]" in marico.body

    def test_canonicalizes_ltd_suffix(self):
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        marico = next(s for s in sections if s.canonical_name == "Marico")
        assert "Ltd" not in marico.canonical_name

    def test_sector_heading_terminates_previous_section(self):
        """A `## FMCG` sector heading is a structural boundary — the previous
        company's body must not run past it into the next company."""
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        marico = next(s for s in sections if s.canonical_name == "Marico")
        # DLF's quote must NOT appear in Marico's body.
        assert "Dahlias has been the biggest success" not in marico.body

    def test_skips_newsletter_chrome(self):
        """`## Discussion about this post` / `## Comments` are chrome, not
        structural boundaries that should appear as phantom sections."""
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        names = [s.canonical_name for s in sections]
        assert "Discussion about this post" not in names


# --------------------------------------------------------------------------- #
# Attribution parsing — every observed form in the corpus                     #
# --------------------------------------------------------------------------- #
class TestAttributionForms:
    """Pin every attribution-line variant found in the corpus survey. Each
    must parse to (name, title); the role-only form returns (None, None)."""

    @pytest.mark.parametrize("line,name,title", [
        ("## — Saugata Gupta, MD & CEO", "Saugata Gupta", "MD & CEO"),
        ("— Saugata Gupta, MD & CEO", "Saugata Gupta", "MD & CEO"),
        ("-Saugata Gupta, MD & CEO", "Saugata Gupta", "MD & CEO"),
        ("Badal Bagri, Group CFO", "Badal Bagri", "Group CFO"),
        ("- Akash Ohri, MD & CBO", "Akash Ohri", "MD & CBO"),
        # Title in parens form.
        ("- Suvankar Sen (MD & CEO)", "Suvankar Sen", "MD & CEO"),
        ("- George Muthoot (Managing Director)", "George Muthoot", "Managing Director"),
        # Name-only form (title is None).
        ("- Mohit Malhotra", "Mohit Malhotra", None),
        # Single-letter-initial names.
        ("- K Krithivasan, CEO", "K Krithivasan", "CEO"),
        ("- T. V. Chowdary, Managing Director", "T. V. Chowdary", "Managing Director"),
    ])
    def test_parses_named_attribution(self, line, name, title):
        result = di._parse_attribution(line)
        assert result is not None
        assert result == (name, title)

    def test_role_only_heading_returns_anonymous(self):
        """`## Management, Executive` signals an anonymous quote (speaker
        NULL), not a missing attribution."""
        result = di._parse_attribution("## Management, Executive")
        assert result == (None, None)

    @pytest.mark.parametrize("line", [
        "",                                   # blank
        "The company said it expects growth.",  # prose (lowercase start)
        '"Another verbatim quote here that is long enough to look like a quote."',  # next quote
        "## FMCG",                            # bare sector heading (no dash)
        "---",                                # section break
        "*Source: The Chatter — Edition",     # source footer
        "![[images/foo.jpeg]]",               # image embed
        "## Real Estate",                     # sector heading
    ])
    def test_rejects_non_attribution(self, line):
        assert di._parse_attribution(line) is None

    def test_rejects_prose_starting_with_the(self):
        """`The company announced...` must not be mistaken for an attribution."""
        assert di._parse_attribution("The company announced a new partnership today.") is None


# --------------------------------------------------------------------------- #
# Quote extraction (end-to-end over the sample newsletter)                    #
# --------------------------------------------------------------------------- #
class TestExtractQuotes:
    def test_extracts_all_quotes_with_attribution(self):
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        marico = next(s for s in sections if s.canonical_name == "Marico")
        quotes = di.extract_quotes(marico, "Marico DLF BSE", "Marico_DLF_BSE")
        assert len(quotes) == 4
        # Every quote is attributed (the sample uses 4 different attribution forms).
        assert all(q.speaker_name for q in quotes)
        # The first quote's paraphrase is the line before it.
        assert "five-year high" in (quotes[0].paraphrase or "")

    def test_quote_text_has_no_surrounding_quotes(self):
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        marico = next(s for s in sections if s.canonical_name == "Marico")
        quotes = di.extract_quotes(marico, "Marico DLF BSE", "Marico_DLF_BSE")
        for q in quotes:
            assert not q.quote_text.startswith('"')
            assert not q.quote_text.endswith('"')

    def test_local_engine_italic_wrapped_quotes_extracted(self):
        # pdf_conv_md.py/pymupdf4llm italicizes every physical line and uses
        # typographic quotes, so a quote arrives as `_\u201cfirst line_` ...
        # `_closing line.\u201d_` with a `_— Name, Title_` attribution. The walker
        # must unwrap the emphasis and normalize the quotes before its
        # `"`-anchored matching (Chatter #83 yielded 0 quotes before the fix).
        body = (
            "## [Concall]\n"
            "Price hikes hadn't flowed through in Q1, creating scope for margin recovery.\n"
            "_\u201cIf we compare this with respect to the West Asia conflict impact, the price"
            " action compensated in the first quarter is very less._\n"
            "_In the coming quarters, I think you will see the impact.\u201d_\n"
            "_\u2014 Anand Sultania, Chief Financial Officer_\n"
        )
        section = di.CompanySection("Borosil", 1, body)
        quotes = di.extract_quotes(
            section, "Borosil Orchid Welspun", "Borosil_Orchid_Welspun"
        )
        assert len(quotes) == 1
        q = quotes[0]
        assert not q.quote_text.startswith(('"', "_", "\u201c"))
        assert not q.quote_text.endswith(('"', "_", "\u201d"))
        assert q.speaker_name == "Anand Sultania"
        assert q.speaker_title == "Chief Financial Officer"
        assert q.paraphrase is not None  # narrow for ty
        assert "margin recovery" in q.paraphrase

    def test_source_ref_carries_stem_and_line(self):
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        marico = next(s for s in sections if s.canonical_name == "Marico")
        quotes = di.extract_quotes(marico, "Marico DLF BSE", "Marico_DLF_BSE")
        for q in quotes:
            assert q.source_ref.startswith("derive:quotes:Marico_DLF_BSE:")
            assert q.source_ref.endswith(str(marico.heading_line))

    def test_dlf_section_quote_attribution(self):
        sections = list(di.iter_company_sections(_SAMPLE_NEWSLETTER))
        dlf = next(s for s in sections if s.canonical_name == "DLF")
        quotes = di.extract_quotes(dlf, "Marico DLF BSE", "Marico_DLF_BSE")
        assert len(quotes) == 1
        assert quotes[0].speaker_name == "Akash Ohri"
        assert "Managing Director" in (quotes[0].speaker_title or "")

    def test_horizontal_rule_in_body_does_not_infinite_loop(self):
        """Regression: a `---` line inside a concall body caused an infinite
        loop (the paraphrase accumulator did `continue` without advancing i).
        Guard with a timeout via a tiny body that would hang under the bug."""
        import signal
        body = (
            "## [Concall]\n\n"
            "A paraphrase line before the rule.\n"
            "---\n"  # this line triggered the infinite loop
            "Another paraphrase after.\n"
            '"A verbatim quote here that is long enough to qualify."\n'
            "- Speaker Name, CEO\n"
        )
        section = di.CompanySection("X", 1, body)

        def handler(signum, frame):
            raise TimeoutError("extract_quotes infinite-looped")
        signal.signal(signal.SIGALRM, handler)
        signal.alarm(3)
        try:
            quotes = di.extract_quotes(section, "Ed", "stem")
        finally:
            signal.alarm(0)
        assert len(quotes) == 1


# --------------------------------------------------------------------------- #
# Metric extraction + precision guards                                        #
# --------------------------------------------------------------------------- #
class TestExtractMetrics:
    def test_extracts_inr_crore_figure(self):
        body = (
            "## [Concall]\n\n"
            "Revenue from operations stood at Rs.2,75,972 crore against "
            "Rs.2,32,855 crore last year."
        )
        section = di.CompanySection("Test Co", 1, body)
        metrics = di.extract_metrics(section, "Ed", "stem")
        assert any(m.value_raw and "crore" in m.value_raw.lower() for m in metrics)
        assert any(m.unit == "crore" for m in metrics)

    def test_extracts_percent_and_bps(self):
        body = (
            "## [Concall]\n\n"
            "EBITDA margin expanded by 30 basis points versus Q1. "
            "We expect full-year EBITDA margin expansion of 140-150 bps."
        )
        section = di.CompanySection("Test Co", 1, body)
        metrics = di.extract_metrics(section, "Ed", "stem")
        units = {m.unit for m in metrics}
        assert "bps" in units

    def test_rejects_non_financial_context(self):
        """Counts of employees/stores/customers are NOT money magnitudes —
        the dominant false-positive class in the corpus."""
        body = (
            "## [Concall]\n\n"
            "We now have 2,50,000 employees across 1,200 stores and 50 million customers."
        )
        section = di.CompanySection("Test Co", 1, body)
        metrics = di.extract_metrics(section, "Ed", "stem")
        # None of the figures should be captured (all are non-financial context).
        for m in metrics:
            assert m.unit not in ("crore", "lakh", "bn_usd", "mn_usd")

    def test_extracts_usd_billion(self):
        body = (
            "## [Concall]\n\n"
            "We raised USD 400 million via a senior unsecured bond offering."
        )
        section = di.CompanySection("Test Co", 1, body)
        metrics = di.extract_metrics(section, "Ed", "stem")
        assert any(m.unit == "mn_usd" for m in metrics)

    def test_classifies_metric_label(self):
        body = (
            "## [Concall]\n\n"
            "Revenue grew 23% and EBITDA margin expanded by 140 bps."
        )
        section = di.CompanySection("Test Co", 1, body)
        metrics = di.extract_metrics(section, "Ed", "stem")
        labels = {m.metric_label for m in metrics if m.metric_label}
        assert "revenue" in labels or "growth" in labels
        assert "ebitda_margin" in labels or "margin" in labels


# --------------------------------------------------------------------------- #
# Note rendering — the critical curation-safety property                       #
# --------------------------------------------------------------------------- #
class TestCurationSafety:
    """The most important contract: a hand-written `## The Chatter` block is
    NEVER clobbered. The auto pass fills empty notes; it does not overwrite
    human/agent work."""

    def test_hand_written_block_is_preserved(self):
        """A note with an existing non-sentinel `## The Chatter — X` block for
        edition X must NOT get an auto block for edition X."""
        note = (
            "# Marico\n\n## Company Overview\n...\n\n"
            "## The Chatter — Marico DLF BSE\n\n"
            "**Hand-written bullet:** This was curated by a human.\n\n"
            "> \"A quote the agent selected.\"\n> — Saugata Gupta, MD & CEO\n\n"
            "*Source: The Chatter — Marico DLF BSE*\n"
        )
        quotes = [di.Quote(
            entity="Marico", quote_text="Parachute Rigids delivered 10% growth.",
            paraphrase="Volume growth highlight.", speaker_name="Saugata Gupta",
            speaker_title="MD & CEO", as_of_edition="Marico DLF BSE",
        )]
        block = di.render_chatter_block("Marico DLF BSE", quotes)
        new_text, changed = di._replace_or_insert_block(
            note, "Marico DLF BSE", block
        )
        assert not changed
        assert "Hand-written bullet" in new_text
        assert di._BEGIN not in new_text

    def test_sentinel_wrapped_auto_block_is_refreshed(self):
        """A previously-auto-generated block (sentinel-wrapped) IS replaced on
        re-run — that's the refresh contract."""
        old_block = di.render_chatter_block(
            "Marico DLF BSE",
            [di.Quote(entity="Marico", quote_text="OLD quote text here.",
                      as_of_edition="Marico DLF BSE")]
        )
        note = f"# Marico\n\n## Company Overview\n...\n\n{old_block}"
        new_block = di.render_chatter_block(
            "Marico DLF BSE",
            [di.Quote(entity="Marico", quote_text="NEW quote text here.",
                      speaker_name="Saugata Gupta", speaker_title="MD & CEO",
                      as_of_edition="Marico DLF BSE")]
        )
        new_text, changed = di._replace_or_insert_block(
            note, "Marico DLF BSE", new_block
        )
        assert changed
        assert "NEW quote text" in new_text
        assert "OLD quote text" not in new_text
        # Only one auto block (no stacking).
        assert new_text.count(di._BEGIN) == 1

    def test_all_whitespace_edition_converges(self):
        """Regression (fuzz-found 2026-08-22): an edition string that is
        entirely Unicode whitespace (\\x85 NEL matches \\s) made the
        heading regex capture the EMPTY edition, so the block never
        matched its own render and every --apply inserted a fresh copy —
        unbounded duplication, the non-convergence class of the 2026-08-19
        incident. The edition compare is now strip-normalised like the
        hand-block gate."""
        ed = "\x85"
        block = di.render_chatter_block(
            ed, [di.Quote(entity="Marico", quote_text="q.", as_of_edition=ed)])
        out1, ch1 = di._replace_or_insert_block("# Marico\n", ed, block)
        assert ch1 is True
        out2, ch2 = di._replace_or_insert_block(out1, ed, block)
        assert ch2 is False                     # recognised its own block
        assert out2 == out1
        assert out1.count(di._BEGIN) == 1       # no stacking

    def test_different_edition_hand_block_does_not_block_new_auto(self):
        """A hand-written block for edition Y does NOT block an auto block for
        edition X (different editions coexist)."""
        note = (
            "# Marico\n\n## Company Overview\n...\n\n"
            "## The Chatter — Older Edition\n\n**Old curated content.**\n"
        )
        quotes = [di.Quote(entity="Marico", quote_text="New edition quote.",
                           as_of_edition="New Edition")]
        block = di.render_chatter_block("New Edition", quotes)
        new_text, changed = di._replace_or_insert_block(note, "New Edition", block)
        assert changed
        assert "Older Edition" in new_text  # hand block preserved
        assert "New Edition" in new_text    # auto block added

    def test_empty_note_gets_auto_block(self):
        """A stub note with no Chatter block gets the auto block appended."""
        note = "# Marico\n\n## Company Overview\nA stub.\n"
        quotes = [di.Quote(entity="Marico", quote_text="A verbatim quote.",
                           speaker_name="Saugata Gupta",
                           speaker_title="MD & CEO",
                           as_of_edition="Marico DLF BSE")]
        block = di.render_chatter_block("Marico DLF BSE", quotes)
        new_text, changed = di._replace_or_insert_block(
            note, "Marico DLF BSE", block
        )
        assert changed
        assert "## The Chatter — Marico DLF BSE" in new_text


# --------------------------------------------------------------------------- #
# DB idempotency                                                              #
# --------------------------------------------------------------------------- #
class TestApplyIdempotency:
    def _quotes(self):
        return [
            di.Quote(entity="Marico",
                     quote_text="Parachute delivered 10% volume growth.",
                     speaker_name="Saugata Gupta", speaker_title="MD & CEO",
                     as_of_edition="Marico DLF BSE",
                     source_ref="derive:quotes:Marico_DLF_BSE:45"),
            di.Quote(entity="Marico",
                     quote_text="Copra prices corrected meaningfully this quarter.",
                     speaker_name="Saugata Gupta", speaker_title="MD & CEO",
                     as_of_edition="Marico DLF BSE",
                     source_ref="derive:quotes:Marico_DLF_BSE:45"),
        ]

    def test_dry_run_writes_nothing(self, tmp_path):
        conn = _connect(tmp_path)
        di.apply_quotes(self._quotes(), conn=conn, dry_run=True)
        n = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        assert n == 0
        conn.close()

    def test_apply_writes_rows(self, tmp_path):
        conn = _connect(tmp_path)
        di.apply_quotes(self._quotes(), conn=conn, dry_run=False)
        n = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        assert n == 2
        conn.close()

    def test_reapply_replaces_derived_rows(self, tmp_path):
        """DELETE-then-INSERT: a second apply with a different quote set
        replaces the first, doesn't stack."""
        conn = _connect(tmp_path)
        di.apply_quotes(self._quotes(), conn=conn, dry_run=False)
        # Second pass with one different quote.
        second = [di.Quote(entity="Marico",
                           quote_text="A completely new quote this edition.",
                           speaker_name="Saugata Gupta",
                           speaker_title="MD & CEO",
                           as_of_edition="Marico DLF BSE",
                           source_ref="derive:quotes:Marico_DLF_BSE:45")]
        di.apply_quotes(second, conn=conn, dry_run=False)
        rows = conn.execute(
            "SELECT quote_text FROM quotes WHERE source_ref LIKE 'derive:quotes:%'"
        ).fetchall()
        texts = {r["quote_text"] for r in rows}
        assert "A completely new quote this edition." in texts
        # The old derived rows are gone (replaced, not stacked).
        assert "Parachute delivered 10% volume growth." not in texts
        conn.close()

    def test_manual_rows_preserved(self, tmp_path):
        """Hand-seeded rows with a non-derive source_ref survive the LIKE
        sweep (the manual:/migration: preservation contract)."""
        conn = _connect(tmp_path)
        conn.execute(
            "INSERT INTO quotes (entity, quote_text, source_ref) "
            "VALUES ('Marico', 'A hand-seeded quote.', 'manual:curated')"
        )
        conn.commit()
        di.apply_quotes(self._quotes(), conn=conn, dry_run=False)
        texts = {r["quote_text"] for r in conn.execute(
            "SELECT quote_text FROM quotes").fetchall()}
        assert "A hand-seeded quote." in texts  # preserved
        conn.close()


# --------------------------------------------------------------------------- #
# CLI: --no-notes (maint-full DB-only contract)                               #
# --------------------------------------------------------------------------- #
class TestCliNoNotes:
    @staticmethod
    def _run(tmp_path, monkeypatch, extra_args):
        """Set up a tmp newsletter + entity DB, spy on the renderers, run _cli.

        Returns (rc, called) where ``called`` records whether each renderer
        was invoked. Each call gets its own fresh connection (``_cli`` closes
        the connection it opens in its ``finally``).
        """
        nl = tmp_path / "nl.md"
        nl.write_text(_SAMPLE_NEWSLETTER)
        db_path = tmp_path / "test_insights.db"
        init = sqlite3.connect(db_path)
        init.row_factory = sqlite3.Row
        init.executescript(_schema_sql())
        init.execute(
            "INSERT INTO entities(name, entity_type) VALUES ('Marico','company')"
        )
        init.commit()
        init.close()

        def _fresh():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(di, "connect", _fresh)

        called = {"notes": False, "metrics": False}

        def _spy_notes(*a, **k):
            called["notes"] = True
            return (0, 0, 0)

        def _spy_metrics(*a, **k):
            called["metrics"] = True
            return (0, 0)

        monkeypatch.setattr(di, "render_notes", _spy_notes)
        monkeypatch.setattr(di, "render_metrics_notes", _spy_metrics)
        rc = di._cli([*extra_args, str(nl)])
        return rc, called

    def test_no_notes_skips_renderers(self, tmp_path, monkeypatch):
        """--no-notes must skip render_notes/render_metrics_notes so a
        housekeeping run never mutates notes (the maint-full contract added
        after the profile-stripping incident)."""
        rc, called = self._run(tmp_path, monkeypatch, ["--apply", "--no-notes"])
        assert rc == 0
        assert called == {"notes": False, "metrics": False}

    def test_renderers_invoked_without_flag(self, tmp_path, monkeypatch):
        """Without --no-notes both renderers run (the standalone path)."""
        rc, called = self._run(tmp_path, monkeypatch, ["--apply"])
        assert rc == 0
        assert called["notes"] is True and called["metrics"] is True


# --------------------------------------------------------------------------- #
# --stale-only (okf_activation I): evidence-gated incremental rendering      #
# --------------------------------------------------------------------------- #
def _note_with_fm(gen_by=None, gen_at=None, sources=None, body="# Marico\n"):
    fm = ["---", "type: company", "title: Marico"]
    if gen_by:
        fm += ["generated:", f"  by: {gen_by}", f"  at: '{gen_at}'"]
    if sources is not None:
        fm.append("sources:")
        for sid, lm in sources:
            fm += [f"- id: {sid}", f"  last_modified: '{lm}'"]
    fm.append("---")
    return "\n".join(fm) + "\n" + body


class TestStaleOnlyGate:
    """_stale_only_skip: True=skip, False=render, None=no evidence."""

    def test_fresh_render_and_older_sources_skips(self):
        t = _note_with_fm("derive_insights.py/v1", "2026-08-16T00:00:00Z",
                          [("Ed1", "2026-08-15")])
        assert di._stale_only_skip(t) is True

    def test_equal_dates_skip(self):
        t = _note_with_fm("derive_insights.py/v1", "2026-08-15T00:00:00Z",
                          [("Ed1", "2026-08-15")])
        assert di._stale_only_skip(t) is True

    def test_newer_source_renders(self):
        t = _note_with_fm("derive_insights.py/v1", "2026-08-10T00:00:00Z",
                          [("Ed1", "2026-08-15")])
        assert di._stale_only_skip(t) is False

    def test_backfill_stamp_is_not_a_render_stamp(self):
        t = _note_with_fm("process:okf_backfill", "2026-08-19T00:00:00Z",
                          [("Ed1", "2026-08-15")])
        assert di._stale_only_skip(t) is None

    def test_no_sources_is_no_evidence(self):
        t = _note_with_fm("derive_insights.py/v1", "2026-08-16T00:00:00Z",
                          sources=[])
        assert di._stale_only_skip(t) is None

    def test_no_frontmatter_is_no_evidence(self):
        assert di._stale_only_skip("# Marico\n") is None


class TestCliStaleOnly:
    @staticmethod
    def _run(tmp_path, monkeypatch, extra_args):
        """tmp newsletter + two company notes (gated + renderable) + entity
        rows with file_paths; PROJECT_ROOT redirected so renders land in
        tmp. Returns (rc, stderr, gated_note, render_note)."""
        nl = tmp_path / "nl.md"
        nl.write_text(_SAMPLE_NEWSLETTER)
        gated = tmp_path / "findata" / "Companies" / "FMCG" / "Marico.md"
        gated.parent.mkdir(parents=True, exist_ok=True)
        gated.write_text(_note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("Ed1", "2026-08-15")]))
        db_path = tmp_path / "test_insights.db"
        init = sqlite3.connect(db_path)
        init.row_factory = sqlite3.Row
        init.executescript(_schema_sql())
        init.execute(
            "INSERT INTO entities(name, entity_type, file_path) "
            "VALUES ('Marico','company','findata/Companies/FMCG/Marico.md')")
        init.commit()
        init.close()

        def _fresh():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(di, "connect", _fresh)
        monkeypatch.setattr(di, "PROJECT_ROOT", tmp_path)
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = di._cli([*extra_args, str(nl)])
        return rc, err.getvalue(), gated

    def test_stale_only_heals_then_holds_fixed_point(self, tmp_path, monkeypatch):
        """#139: a fresh-stamped note missing its chatter block is DRIFT, not
        fresh — the first --stale-only apply heals it (block + generated
        bump); the second is byte-identical (byte-guard no-op) and gated."""
        rc, err, after = self._run(tmp_path, monkeypatch, ["--apply", "--stale-only"])
        assert rc == 0
        healed = after.read_text()
        assert "BEGIN auto chatter block" in healed      # the heal wrote it
        assert healed != _note_with_fm(                  # generated.at advanced
            "derive_insights.py/v1", "2026-08-16T00:00:00Z", [("Ed1", "2026-08-15")])
        (tmp_path / "test_insights.db").unlink()  # helper recreates it
        # Phase 2 bypasses _run (it re-creates the fixture note); rebuild only
        # the DB and re-invoke so the healed note is what runs.
        db_path = tmp_path / "test_insights.db"
        db_path.unlink(missing_ok=True)
        init = sqlite3.connect(db_path)
        init.row_factory = sqlite3.Row
        init.executescript(_schema_sql())
        init.execute(
            "INSERT INTO entities(name, entity_type, file_path) "
            "VALUES ('Marico','company','findata/Companies/FMCG/Marico.md')")
        init.commit()
        init.close()
        import contextlib
        import io as _io
        err2 = _io.StringIO()
        with contextlib.redirect_stderr(err2):
            rc2 = di._cli(["--apply", "--stale-only", str(tmp_path / "nl.md")])
        assert rc2 == 0
        assert after.read_text() == healed              # byte-identical
        assert "0 notes wrote" in err2.getvalue()
        assert "gated by --stale-only" in err2.getvalue()

    def test_stale_only_renders_note_with_newer_evidence(self, tmp_path, monkeypatch):
        note = tmp_path / "findata" / "Companies" / "FMCG" / "Marico.md"
        note.parent.mkdir(parents=True)
        # older render than the source edition -> must re-render
        note.write_text(_note_with_fm(
            "derive_insights.py/v1", "2026-08-10T00:00:00Z",
            [("Ed1", "2026-08-15")]))
        nl = tmp_path / "nl.md"
        nl.write_text(_SAMPLE_NEWSLETTER)
        db_path = tmp_path / "test_insights.db"
        init = sqlite3.connect(db_path)
        init.row_factory = sqlite3.Row
        init.executescript(_schema_sql())
        init.execute(
            "INSERT INTO entities(name, entity_type, file_path) "
            "VALUES ('Marico','company','findata/Companies/FMCG/Marico.md')")
        init.commit()
        init.close()

        def _fresh():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(di, "connect", _fresh)
        monkeypatch.setattr(di, "PROJECT_ROOT", tmp_path)
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = di._cli(["--apply", "--stale-only", str(nl)])
        assert rc == 0
        text = note.read_text()
        assert di._BEGIN in text           # block rendered
        assert "2026-08-10" not in text    # generated.at re-stamped
        gated = di._stale_only_skip(text)
        assert gated is True               # fixed point: re-run would skip

    def test_default_run_ignores_the_gate(self, tmp_path, monkeypatch):
        """Without --stale-only a fresh note still re-renders (opt-in, Q2)."""
        note = tmp_path / "findata" / "Companies" / "FMCG" / "Marico.md"
        note.parent.mkdir(parents=True)
        note.write_text(_note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("Ed1", "2026-08-15")]))
        nl = tmp_path / "nl.md"
        nl.write_text(_SAMPLE_NEWSLETTER)
        db_path = tmp_path / "test_insights.db"
        init = sqlite3.connect(db_path)
        init.executescript(_schema_sql())
        init.execute(
            "INSERT INTO entities(name, entity_type, file_path) "
            "VALUES ('Marico','company','findata/Companies/FMCG/Marico.md')")
        init.commit()
        init.close()

        def _fresh():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(di, "connect", _fresh)
        monkeypatch.setattr(di, "PROJECT_ROOT", tmp_path)
        import io
        import contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            rc = di._cli(["--apply", str(nl)])
        assert rc == 0
        assert di._BEGIN in note.read_text()


# --------------------------------------------------------------------------- #
# sources[] splice + gate amendment (okf_sources_maintenance §3.2):           #
# rendered notes absorb newly scanned editions at render time; the gate       #
# re-opens for a scanned edition missing from sources[].                      #
# --------------------------------------------------------------------------- #
from helpers.core import edition_index as ei  # noqa: E402


def _splice_vault(tmp_path, monkeypatch, *, stems=("My_Edition",)):
    """tmp findata vault (source tree The_Chatter/<stem>.md) with
    PROJECT_ROOT redirected and git_add_date pinned; returns (vault, index)."""
    vault = tmp_path / "findata"
    chatter = vault / "The_Chatter"
    chatter.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        (chatter / f"{stem}.md").write_text(f"# {stem.replace('_', ' ')}\n")
    (vault / "Companies").mkdir(exist_ok=True)
    monkeypatch.setattr(di, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ei, "git_add_date", lambda p: "2026-08-18")
    monkeypatch.setattr(ei, "_GIT_DATE_MEMO", {})
    return vault, ei.source_note_index(vault)


def _fm_sources(text):
    return {s["id"]: s for s in yaml.safe_load(
        text.split("---")[1]).get("sources", [])}


class TestSpliceSources:
    def test_body_edition_spliced_with_round_trip(self, tmp_path, monkeypatch):
        vault, index = _splice_vault(tmp_path, monkeypatch)
        body = ("# Marico\n\n" + di.render_chatter_block("My Edition", [
            di.Quote(entity="Marico", quote_text="A quote.")]))
        text = _note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("Old_Edition", "2026-08-15")], body=body)
        new_text, changed = di._splice_sources(text, index, vault)
        assert changed is True
        srcs = _fm_sources(new_text)
        assert set(srcs) == {"Old_Edition", "My_Edition"}
        assert srcs["My_Edition"]["last_modified"] == "2026-08-18"
        assert srcs["My_Edition"]["resource"] == (
            "/findata/The_Chatter/My_Edition.md")
        # Frontmatter survives the YAML round-trip; body is byte-identical.
        assert new_text.endswith(body)
        fm = yaml.safe_load(new_text.split("---")[1])
        assert fm["title"] == "Marico"
        assert fm["generated"]["by"] == "derive_insights.py/v1"

    def test_existing_edition_is_noop(self, tmp_path, monkeypatch):
        vault, index = _splice_vault(tmp_path, monkeypatch)
        text = _note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("My_Edition", "2026-08-15")],
            body="# Marico\n\n## The Chatter — My Edition\n")
        new_text, changed = di._splice_sources(text, index, vault)
        assert changed is False
        assert new_text == text

    def test_no_frontmatter_is_noop(self, tmp_path, monkeypatch):
        vault, index = _splice_vault(tmp_path, monkeypatch)
        new_text, changed = di._splice_sources("# Marico\n", index, vault)
        assert changed is False and new_text == "# Marico\n"

    def test_extra_stems_cover_the_metrics_case(self, tmp_path, monkeypatch):
        """Key-figures blocks never name their editions in the body — the
        scanned stems reach the splice directly (else metrics-only notes
        could never satisfy the gate)."""
        vault, index = _splice_vault(tmp_path, monkeypatch)
        text = _note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("Old_Edition", "2026-08-15")], body="# Marico\n\n## Key Figures (auto)\n")
        new_text, changed = di._splice_sources(
            text, index, vault, extra_stems=frozenset({"My_Edition"}))
        assert changed is True
        assert set(_fm_sources(new_text)) == {"Old_Edition", "My_Edition"}

    def test_nothing_to_add_is_noop(self, tmp_path, monkeypatch):
        vault, index = _splice_vault(tmp_path, monkeypatch)
        text = _note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z", sources=[],
            body="# Marico\n")
        new_text, changed = di._splice_sources(text, index, vault)
        assert changed is False and new_text == text


class TestGateAmendment:
    def test_missing_scanned_stem_forces_render(self):
        t = _note_with_fm("derive_insights.py/v1", "2026-08-16T00:00:00Z",
                          [("Old_Edition", "2026-08-15")])
        assert di._stale_only_skip(t, frozenset({"My_Edition"})) is False

    def test_present_scanned_stem_falls_through_to_dates(self):
        t = _note_with_fm("derive_insights.py/v1", "2026-08-16T00:00:00Z",
                          [("My_Edition", "2026-08-15")])
        assert di._stale_only_skip(t, frozenset({"My_Edition"})) is True

    def test_scanned_stems_ignores_unresolvable(self, tmp_path, monkeypatch):
        _, index = _splice_vault(tmp_path, monkeypatch)
        memo: dict = {}
        stems = di._scanned_stems(
            ["My Edition", "Totally Unknown Edition", None, ""], index, memo)
        assert stems == frozenset({"My_Edition"})

    def test_scanned_stems_memoizes(self, tmp_path, monkeypatch):
        _, index = _splice_vault(tmp_path, monkeypatch)
        calls = []

        def _spy(cand, idx):
            calls.append(cand)
            return next(v for k, v in idx.items()
                        if k == ei.norm_key("My Edition"))

        monkeypatch.setattr(di, "resolve_edition_string", _spy)
        memo: dict = {}
        di._scanned_stems(["My Edition"], index, memo)
        di._scanned_stems(["My Edition"], index, memo)
        assert calls == ["My Edition"]


class TestSpliceConvergence:
    """End-to-end: the original §1 hole — a note whose sources[] predates a
    newly scanned edition must render, splice, and reach a fixed point."""

    @staticmethod
    def _marico_db(tmp_path):
        conn = _connect(tmp_path)
        conn.execute(
            "INSERT INTO entities(name, entity_type, file_path) "
            "VALUES ('Marico','company','findata/Companies/Marico.md')")
        conn.commit()
        return conn

    _NL = """\
# My Edition

## Marico Ltd. | Large Cap | FMCG

Marico is a consumer goods company.

## [Concall]

"Parachute delivered its strongest volume growth in years."

— Saugata Gupta, MD & CEO
"""

    def test_new_edition_renders_splices_and_converges(
            self, tmp_path, monkeypatch):
        import contextlib
        import io
        vault, _ = _splice_vault(
            tmp_path, monkeypatch, stems=("Old_Edition", "My_Edition"))
        note = vault / "Companies" / "Marico.md"
        # Fresh by the DATE leg alone (the pre-§3.2 gate would skip this) —
        # but the scanned edition My_Edition is absent from sources[].
        note.write_text(_note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("Old_Edition", "2026-08-15")]))
        nl = tmp_path / "nl.md"
        nl.write_text(self._NL)
        db_path = tmp_path / "test_insights.db"
        conn = self._marico_db(tmp_path)
        conn.close()

        def _fresh():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(di, "connect", _fresh)
        for _ in range(2):  # run 1 renders+splices; run 2 proves the fixpoint
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                assert di._cli(["--apply", "--stale-only", str(nl)]) == 0
        fixed = note.read_text()
        assert "## The Chatter — My Edition" in fixed
        assert set(_fm_sources(fixed)) == {"Old_Edition", "My_Edition"}
        assert "2026-08-16" not in fixed  # generated.at re-stamped
        # A third run gates out and leaves the note byte-identical.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert di._cli(["--apply", "--stale-only", str(nl)]) == 0
        assert "gated by --stale-only" in err.getvalue()
        assert note.read_text() == fixed

    def test_hand_written_block_still_gets_the_sources_repair_write(
            self, tmp_path, monkeypatch):
        """Hand-written edition block preserved (skipped) + scanned edition
        missing from sources[]: the splice alone drives the write (the
        convergence requirement — without it the note would force a render
        every run while the block stays hand-curated)."""
        vault, index = _splice_vault(tmp_path, monkeypatch)
        quotes = [di.Quote(entity="Marico", quote_text="A quote.",
                           as_of_edition="My Edition")]
        hand = "## The Chatter — My Edition\n\nA hand-curated synthesis.\n"
        note = vault / "Companies" / "Marico.md"
        note.write_text(_note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("Old_Edition", "2026-08-15")],
            body="# Marico\n\n" + hand))
        conn = self._marico_db(tmp_path)
        try:
            written, skipped, gated = di.render_notes(
                {("Marico", "My Edition"): quotes}, dry_run=False, conn=conn,
                stale_only=True, index=index)
        finally:
            conn.close()
        assert (written, skipped, gated) == (1, 1, 0)
        text = note.read_text()
        assert set(_fm_sources(text)) == {"Old_Edition", "My_Edition"}
        assert hand in text            # hand-written block untouched
        assert di._BEGIN not in text   # no auto block smuggled in

    def test_metrics_only_note_converges(self, tmp_path, monkeypatch):
        vault, index = _splice_vault(tmp_path, monkeypatch)
        ms = [di.Metric(entity="Marico", metric_label="revenue",
                        value_raw="₹1,000 cr", as_of_edition="My Edition")]
        # KF block pre-rendered byte-identical: only the sources can move.
        kf = di.render_key_figures_block(ms)
        note = vault / "Companies" / "Marico.md"
        note.write_text(_note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("Old_Edition", "2026-08-15")],
            body="# Marico\n\n" + kf))
        conn = self._marico_db(tmp_path)
        try:
            w1, g1 = di.render_metrics_notes(
                {"Marico": ms}, dry_run=False, conn=conn, stale_only=True,
                index=index)
            fixed = note.read_text()
            w2, g2 = di.render_metrics_notes(
                {"Marico": ms}, dry_run=False, conn=conn, stale_only=True,
                index=index)
        finally:
            conn.close()
        assert (w1, g1) == (1, 0)          # repair write (sources only)
        assert (w2, g2) == (0, 1)          # fixed point: gated
        assert set(_fm_sources(fixed)) == {"Old_Edition", "My_Edition"}
        assert note.read_text() == fixed


# --------------------------------------------------------------------------- #
# Rendered block shape                                                        #
# --------------------------------------------------------------------------- #
class TestRenderBlock:
    def test_block_has_sentinel_markers(self):
        quotes = [di.Quote(entity="X", quote_text="A quote.", speaker_name="N",
                           speaker_title="T", as_of_edition="Ed")]
        block = di.render_chatter_block("Ed", quotes)
        assert di._BEGIN in block
        assert di._END in block
        assert "## The Chatter — Ed" in block

    def test_anonymous_quote_has_no_attribution_line(self):
        quotes = [di.Quote(entity="X", quote_text="An anonymous quote here.",
                           speaker_name=None, speaker_title=None,
                           as_of_edition="Ed")]
        block = di.render_chatter_block("Ed", quotes)
        # The quote block must not carry a "> — Name, Title" attribution line.
        # (The heading "## The Chatter — Ed" legitimately contains an em-dash;
        # we check the blockquote section specifically.)
        quote_section = block.split("> ")[1:]  # lines after the first blockquote
        attribution_lines = [ln for ln in quote_section if ln.startswith("— ")]
        assert attribution_lines == []


# --------------------------------------------------------------------------- #
# Key Figures (auto) block rendering                                          #
# --------------------------------------------------------------------------- #
class TestKeyFiguresBlock:
    def test_block_has_kf_sentinels_and_heading(self):
        metrics = [di.Metric(entity="X", value_raw="₹2,75,972 crore",
                             metric_label="revenue", unit="crore",
                             period="Q1 FY27")]
        block = di.render_key_figures_block(metrics)
        assert di._KF_BEGIN in block
        assert di._KF_END in block
        assert "## Key Figures (auto)" in block
        assert "₹2,75,972 crore" in block
        assert "revenue" in block

    def test_groups_by_label_and_shows_period(self):
        metrics = [
            di.Metric(entity="X", value_raw="₹2,75,972 crore",
                      metric_label="revenue", unit="crore", period="Q1 FY27"),
            di.Metric(entity="X", value_raw="10%", metric_label="growth",
                      unit="percent", period="Q1 FY27"),
            di.Metric(entity="X", value_raw="140 bps",
                      metric_label="ebitda_margin", unit="bps", period=None),
        ]
        block = di.render_key_figures_block(metrics)
        # Period appears in parens for dated metrics.
        assert "(Q1 FY27)" in block
        # Undated metric has no period suffix.
        assert "140 bps\n" in block or "140 bps" in block

    def test_dedups_identical_values_within_label(self):
        metrics = [
            di.Metric(entity="X", value_raw="₹2,75,972 crore",
                      metric_label="revenue"),
            di.Metric(entity="X", value_raw="₹2,75,972 crore",
                      metric_label="revenue"),  # dup
            di.Metric(entity="X", value_raw="₹2,32,855 crore",
                      metric_label="revenue"),
        ]
        block = di.render_key_figures_block(metrics)
        # The dup appears once; the two distinct values appear.
        assert block.count("₹2,75,972 crore") == 1
        assert "₹2,32,855 crore" in block

    def test_replace_or_insert_kf_is_idempotent(self):
        """Re-running replaces the existing KF block, doesn't stack."""
        note = "# X\n\n## Company Overview\nA stub.\n"
        m1 = [di.Metric(entity="X", value_raw="10%", metric_label="growth")]
        block1 = di.render_key_figures_block(m1)
        note2, _ = di._replace_or_insert_kf(note, block1)
        m2 = [di.Metric(entity="X", value_raw="20%", metric_label="growth")]
        block2 = di.render_key_figures_block(m2)
        note3, changed = di._replace_or_insert_kf(note2, block2)
        assert changed
        assert "20%" in note3
        assert "10%" not in note3
        assert note3.count(di._KF_BEGIN) == 1  # no stacking

    def test_replace_or_insert_kf_rescues_nested_profile_block(self):
        """A foreign auto-block nested inside the KF region (the historical
        enrich/derive insertion collision) must be preserved and moved out,
        not destroyed on refresh. Reproduces the Wockhardt-class stripping bug.
        """
        profile = (
            "<!-- BEGIN auto company profile (enrich_from_yfinance.py) -->\n"
            "\n## Company Profile (yfinance)\n\n- **Industry**: Pharma\n"
            "\n<!-- END auto company profile -->"
        )
        kf_old = di.render_key_figures_block(
            [di.Metric(entity="X", value_raw="10%", metric_label="growth")]
        )
        # Simulate the collision: profile lodged between _KF_BEGIN and heading.
        nested = kf_old.replace(
            f"{di._KF_BEGIN}\n\n{di._KF_HEADING}",
            f"{di._KF_BEGIN}\n\n{profile}\n\n{di._KF_HEADING}",
            1,
        )
        note = f"# X\n\n## Company Overview\nA stub.\n\n{nested}"
        kf_new = di.render_key_figures_block(
            [di.Metric(entity="X", value_raw="20%", metric_label="growth")]
        )
        out, changed = di._replace_or_insert_kf(note, kf_new)
        assert changed
        assert "## Company Profile (yfinance)" in out  # profile preserved
        assert "Pharma" in out
        assert "20%" in out and "10%" not in out       # figure refreshed
        # Profile now sits BEFORE the KF region (rescued out); KF count stable.
        assert out.index("## Company Profile (yfinance)") < out.index(di._KF_BEGIN)
        assert out.count(di._KF_BEGIN) == 1


class TestNestedBlockRescue:
    """2026-08-19 incident: the CHATTER renderer replaced its whole sentinel
    region while the key-figures block (with a nested yfinance profile) sat
    INSIDE it — 58 notes lost their profiles (the metrics pass re-inserted an
    identical KF block, hiding the KF loss from diffs). Both renderers now
    rescue nested foreign auto-blocks via _extract_nested_blocks; unbalanced
    sentinels skip the replacement rather than risk destroying content."""

    @staticmethod
    def _juniper_shape_note():
        """The observed live layout: chatter region encloses the KF region,
        which encloses the profile (Juniper_Hotels pre-image shape)."""
        profile = (
            "<!-- BEGIN auto company profile (enrich_from_yfinance.py) -->\n"
            "\n## Company Profile (yfinance)\n\n- **Industry**: Lodging\n"
            "\n_Source: yfinance | Refreshed: 2026-08-10_\n"
            "<!-- END auto company profile -->"
        )
        kf = (f"{di._KF_BEGIN}\n\n{profile}\n\n{di._KF_HEADING}\n\n"
              f"- **growth**: 9%\n\n{di._KF_END}")
        chatter = (f"{di._BEGIN}\n\n{kf}\n\n## The Chatter — Old Edition\n\n"
                   f"Old auto content.\n\n"
                   f"*Source: The Chatter — Old Edition*\n\n{di._END}")
        return (f"# X Ltd\n\n## Company Overview\nA stub.\n\n{chatter}\n",
                profile, kf)

    def test_chatter_refresh_rescues_nested_kf_and_profile(self):
        note, profile, _ = self._juniper_shape_note()
        new_block = di.render_chatter_block("Old Edition", [
            di.Quote(entity="X", quote_text="New quote.",
                     as_of_edition="Old Edition")])
        out, changed = di._replace_or_insert_block(note, "Old Edition", new_block)
        assert changed
        assert "Company Profile (yfinance)" in out   # profile preserved
        assert "## Key Figures (auto)" in out        # KF block preserved
        assert "New quote." in out                   # chatter refreshed
        assert out.count(di._KF_BEGIN) == 1
        # Both rescued BEFORE the new chatter region, not nested in it.
        assert out.index("## Company Profile (yfinance)") < out.index(di._BEGIN)
        assert out.index(di._KF_BEGIN) < out.index(di._BEGIN)

    def test_chatter_edition_swap_also_rescues(self):
        """The different-edition replacement path (matches[0] swap) must
        rescue too — it rewrites the same region."""
        note, _, _ = self._juniper_shape_note()
        new_block = di.render_chatter_block("Brand New Edition", [
            di.Quote(entity="X", quote_text="Q.", as_of_edition="Brand New Edition")])
        out, changed = di._replace_or_insert_block(note, "Brand New Edition", new_block)
        assert changed
        assert "Company Profile (yfinance)" in out
        assert "## Key Figures (auto)" in out
        assert "Brand New Edition" in out

    def test_unbalanced_nested_sentinels_skip_replacement(self):
        note, _, _ = self._juniper_shape_note()
        # Corrupt the KF region: drop its END marker inside the chatter region.
        broken = note.replace(di._KF_END + "\n\n", "", 1)
        new_block = di.render_chatter_block("Old Edition", [
            di.Quote(entity="X", quote_text="New quote.",
                     as_of_edition="Old Edition")])
        out, changed = di._replace_or_insert_block(broken, "Old Edition", new_block)
        assert changed is False
        assert out == broken  # untouched — never risk destroying content

    def test_extract_nested_blocks_returns_maximal_spans(self):
        """Double nesting yields ONE maximal span (KF with the profile inside),
        never the profile again as a separate block (no duplication)."""
        note, _, _ = self._juniper_shape_note()
        m = re.search(
            re.escape(di._BEGIN) + r".*?" + re.escape(di._END), note, re.DOTALL
        )
        assert m is not None  # _juniper_shape_note always nests a chatter block
        region = m.group(0)
        blocks = di._extract_nested_blocks(region)
        assert blocks is not None
        assert len(blocks) == 1
        assert blocks[0].startswith(di._KF_BEGIN)
        assert "Company Profile (yfinance)" in blocks[0]

    def test_extract_nested_blocks_unbalanced_is_none(self):
        # END before any BEGIN, and an unclosed BEGIN, are both unbalanced.
        assert di._extract_nested_blocks(
            "x<!-- END auto other -->y") is None
        assert di._extract_nested_blocks(
            "x<!-- BEGIN auto key figures -->y") is None
        assert di._extract_nested_blocks("no markers") == []


class TestMultiEditionBlocks:
    """2026-08-19 incident #2: the old matches[0] swap made the
    last-rendered edition EVICT a sibling edition's auto block whenever a
    note had quotes from 2+ editions — the edition's chatter was silently
    dropped, the sources splice never saw it, and --stale-only re-forced
    the render on every run (31 notes stuck). A note accumulates one
    block per scanned edition; only an edition's OWN block is replaced."""

    def test_sibling_edition_block_never_evicted(self):
        a = di.render_chatter_block("Edition A", [
            di.Quote(entity="X", quote_text="A quote.", as_of_edition="Edition A")])
        note = f"# X\n\n## Company Overview\nStub.\n\n{a}"
        b = di.render_chatter_block("Edition B", [
            di.Quote(entity="X", quote_text="B quote.", as_of_edition="Edition B")])
        out, changed = di._replace_or_insert_block(note, "Edition B", b)
        assert changed
        assert "A quote." in out and "B quote." in out   # BOTH editions kept
        assert out.count(di._BEGIN) == 2

    def test_multi_edition_render_is_idempotent(self):
        a = di.render_chatter_block("Edition A", [
            di.Quote(entity="X", quote_text="A quote.", as_of_edition="Edition A")])
        b = di.render_chatter_block("Edition B", [
            di.Quote(entity="X", quote_text="B quote.", as_of_edition="Edition B")])
        note = f"# X\n\n## Company Overview\nStub.\n\n{a}"
        _, _ = di._replace_or_insert_block(note, "Edition B", b)
        first = di._replace_or_insert_block(note, "Edition B", b)[0]
        # Re-render both editions: each replaces only its own block.
        again = di._replace_or_insert_block(first, "Edition A", a)[0]
        again = di._replace_or_insert_block(again, "Edition B", b)[0]
        assert again.count(di._BEGIN) == 2
        assert again == di._replace_or_insert_block(again, "Edition A", a)[0]

    def test_multi_edition_note_converges_under_stale_only(
            self, tmp_path, monkeypatch):
        """End-to-end pin: two editions with quotes -> both blocks render,
        sources[] gains BOTH stems, and the second --stale-only run gates
        the note out byte-identically (the 31-note fixed point)."""
        import contextlib
        import io
        vault, _ = _splice_vault(
            tmp_path, monkeypatch, stems=("Edition_A", "Edition_B"))
        nl1 = tmp_path / "nl1.md"
        nl1.write_text(
            "# Edition A\n\n## Marico Ltd. | Large Cap | FMCG\n\n"
            "Marico is a consumer goods company.\n\n## [Concall]\n\n"
            "\"The first edition delivered strong volume growth.\"\n\n"
            "— Saugata Gupta, MD & CEO\n")
        nl2 = tmp_path / "nl2.md"
        nl2.write_text(
            "# Edition B\n\n## Marico Ltd. | Large Cap | FMCG\n\n"
            "Marico is a consumer goods company.\n\n## [Concall]\n\n"
            "\"The second edition carried an even stronger quarter.\"\n\n"
            "— Saugata Gupta, MD & CEO\n")
        note = vault / "Companies" / "Marico.md"
        note.write_text(_note_with_fm(
            "derive_insights.py/v1", "2026-08-16T00:00:00Z",
            [("Old_Edition", "2026-08-15")]))
        db_path = tmp_path / "test_insights.db"
        init = sqlite3.connect(db_path)
        init.row_factory = sqlite3.Row
        init.executescript(_schema_sql())
        init.execute(
            "INSERT INTO entities(name, entity_type, file_path) "
            "VALUES ('Marico','company','findata/Companies/Marico.md')")
        init.commit()
        init.close()

        def _fresh():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(di, "connect", _fresh)
        monkeypatch.setattr(di, "PROJECT_ROOT", tmp_path)
        with contextlib.redirect_stderr(io.StringIO()):
            for nl in (nl1, nl2):
                assert di._cli(["--apply", "--stale-only", str(nl)]) == 0
        fixed = note.read_text()
        assert "first edition delivered strong volume growth" in fixed
        assert "second edition carried an even stronger quarter" in fixed
        assert set(_fm_sources(fixed)) == {
            "Old_Edition", "Edition_A", "Edition_B"}
        with contextlib.redirect_stderr(io.StringIO()):
            for nl in (nl1, nl2):
                assert di._cli(["--apply", "--stale-only", str(nl)]) == 0
        assert note.read_text() == fixed  # converged: byte-identical


class TestInsertionPlacement:
    """2026-08-19 incident #3: insertion points keyed on the first
    `## The Chatter` heading matched headings INSIDE existing auto
    regions, so new blocks were inserted between a region's BEGIN and its
    heading — stacking degenerate BEGIN markers and chaining orphaned
    bodies (66 notes). Both insertion points now bump OUT of any auto
    region; renderers refuse to write a render that breaks marker
    balance."""

    @staticmethod
    def _note_with_auto_and_curated():
        block = di.render_chatter_block("Existing Edition", [
            di.Quote(entity="X", quote_text="An existing edition quote line.",
                     as_of_edition="Existing Edition")])
        return (f"# X\n\n## Company Overview\nStub.\n\n{block}\n\n"
                "## The Chatter — A Curated Synthesis\n\nHand-written.\n")

    def test_chatter_insert_never_splits_an_existing_region(self):
        note = self._note_with_auto_and_curated()
        new_block = di.render_chatter_block("Different Edition", [
            di.Quote(entity="X", quote_text="A different edition quote line.",
                     as_of_edition="Different Edition")])
        out, changed = di._replace_or_insert_block(note, "Different Edition",
                                                   new_block)
        assert changed
        assert not re.search(
            r"<!-- BEGIN auto [^\n>]*-->\s*(?:\n\s*)*<!-- BEGIN auto ", out)
        assert out.count(di._BEGIN) == 2
        assert out.count(di._END) == 2
        # Both blocks intact: heading immediately after its own BEGIN.
        first_begin = out.index(di._BEGIN)
        assert out[first_begin + len(di._BEGIN):].lstrip("\n").startswith("##")

    def test_kf_insertion_point_stays_outside_chatter_region(self):
        """The KF fallback ("before the first ## The Chatter") originally
        landed INSIDE the chatter region — the root of the nested layouts."""
        block = di.render_chatter_block("Edition", [
            di.Quote(entity="X", quote_text="An edition quote line here.",
                     as_of_edition="Edition")])
        note = f"# X\n\n## Company Overview\nStub.\n\n{block}"
        idx = di._kf_insertion_point(note)
        region_start = note.index(di._BEGIN)
        region_end = note.index(di._END) + len(di._END)
        # Inside the region's CONTENT is forbidden; at the region start
        # (insert before the whole region) is exactly what we want.
        assert not (region_start < idx < region_end)
        assert idx <= region_start

    def test_render_skips_write_that_breaks_marker_balance(
            self, tmp_path, monkeypatch, capsys):
        """Belt-and-suspenders: even if a future render bug produces a
        degenerate structure, the note is never written."""
        vault, index = _splice_vault(tmp_path, monkeypatch)
        note = vault / "Companies" / "Marico.md"
        note.write_text(_note_with_fm(body="# Marico\n"))
        conn = sqlite3.connect(tmp_path / "t.db")
        conn.row_factory = sqlite3.Row
        conn.executescript(_schema_sql())
        conn.execute(
            "INSERT INTO entities(name, entity_type, file_path) "
            "VALUES ('Marico','company','findata/Companies/Marico.md')")
        conn.commit()

        _orig_render = di.render_chatter_block

        def _degenerate(edition, quotes, *args, **kwargs):
            return _orig_render(edition, quotes).replace(
                di._END + "\n", "", 1)  # drop the END marker

        monkeypatch.setattr(di, "render_chatter_block", _degenerate)
        quotes = [di.Quote(entity="Marico", quote_text="A quote line.",
                           as_of_edition="Edition A")]
        written, skipped, gated = di.render_notes(
            {("Marico", "Edition A"): quotes}, dry_run=False, conn=conn,
            stale_only=False, index=index)
        conn.close()
        assert (written, gated) == (0, 0)
        assert "unbalanced auto markers" in capsys.readouterr().err
        assert note.read_text() == _note_with_fm(body="# Marico\n")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# _canonicalize — pure function
# ---------------------------------------------------------------------------
def test_canonicalize_strips_ltd():
    assert di._canonicalize("Infosys Ltd") == "Infosys"


def test_canonicalize_strips_private():
    assert di._canonicalize("ABC Private Limited") == "ABC"


def test_canonicalize_strips_pipe_alias():
    assert di._canonicalize("Reliance|RIL") == "Reliance"


def test_canonicalize_strips_cap_suffix():
    assert di._canonicalize("HDFC Bank Limited") == "HDFC Bank"


def test_canonicalize_collapse_whitespace():
    assert di._canonicalize("  Multiple   Spaces  Ltd  ") == "Multiple Spaces"


# ---------------------------------------------------------------------------
# _parse_attribution — pure function
# ---------------------------------------------------------------------------
def test_parse_attribution_with_title():
    result = di._parse_attribution("Rajesh Kumar, CEO")
    assert result is not None
    assert result[0] == "Rajesh Kumar"
    assert result[1] == "CEO"


def test_parse_attribution_role_only():
    result = di._parse_attribution("## Management, Executive")
    assert result is not None
    assert result == (None, None)


def test_parse_attribution_empty():
    assert di._parse_attribution("") is None


def test_parse_attribution_too_long():
    assert di._parse_attribution("x" * 200) is None


def test_parse_attribution_single_word_rejected():
    assert di._parse_attribution("Revenue,") is None


def test_parse_attribution_prose_rejected():
    assert di._parse_attribution("The company said that") is None


def test_parse_attribution_abbreviated_title():
    result = di._parse_attribution("Jane Smith, MD & CEO")
    assert result is not None
    assert result[0] is not None
    assert "Jane Smith" in result[0]


# ---------------------------------------------------------------------------
# _label_from_window
# ---------------------------------------------------------------------------
def test_label_from_window_revenue():
    assert di._label_from_window("revenue for the quarter") == "revenue"


def test_label_from_window_ebitda_margin():
    assert di._label_from_window("ebitda margin expanded") == "ebitda_margin"


def test_label_from_window_margin():
    assert di._label_from_window("gross margin improved") == "margin"


def test_label_from_window_capex():
    assert di._label_from_window("capex of rs 5000 crore") == "capex"


def test_label_from_window_no_match():
    assert di._label_from_window("random text here") is None


# ---------------------------------------------------------------------------
# _classify_metric
# ---------------------------------------------------------------------------
def test_classify_metric_preceding():
    label = di._classify_metric("Revenue grew to Rs 5,000 crore", "5,000", 18)
    assert label == "revenue"


def test_classify_metric_following():
    label = di._classify_metric("The figure of 25% margin", "25%", 14)
    assert label == "margin"


def test_classify_metric_no_match():
    label = di._classify_metric("Some random text 42 here", "42", 17)
    assert label is None


# ---------------------------------------------------------------------------
# _parse_value_num
# ---------------------------------------------------------------------------
def test_parse_value_num_crore():
    assert di._parse_value_num("Rs 5,000 crore", "crore") == 5000.0


def test_parse_value_num_with_range():
    # Takes first number (lower bound of range)
    assert di._parse_value_num("10-20", "") == 10.0


def test_parse_value_num_empty():
    assert di._parse_value_num("no numbers", "") is None


def test_parse_value_num_decimal():
    assert di._parse_value_num("33.8", "percent") == 33.8


# ---------------------------------------------------------------------------
# _unit_of
# ---------------------------------------------------------------------------
def test_unit_of_crore():
    assert di._unit_of("Rs 5,000 crore") == "crore"


def test_unit_of_percent():
    assert di._unit_of("33.8%") == "percent"


def test_unit_of_bps():
    assert di._unit_of("140 bps") == "bps"


def test_unit_of_lakh():
    assert di._unit_of("Rs 10 lakh") == "lakh"


def test_unit_of_bn_usd():
    assert di._unit_of("$5.2 bn") == "bn_usd"


def test_unit_of_gw():
    assert di._unit_of("2.5 gw") == "gw"


def test_unit_of_mw():
    assert di._unit_of("500 mw") == "mw"


def test_unit_of_x():
    assert di._unit_of("2.5x") == "x"


def test_unit_of_none():
    assert di._unit_of("random text") is None


# ---------------------------------------------------------------------------
# _edition_title
# ---------------------------------------------------------------------------
def test_edition_title_from_stem():
    result = di._edition_title("Newsletter_2024_01_15_Tech", "")
    assert result is not None


# --------------------------------------------------------------------------- #
# OKF v0.2 generated/stale_after bump (okf_adoption.md §2.3)                    #
# --------------------------------------------------------------------------- #
from helpers.core.frontmatter import bump_generated  # noqa: E402

_OKF_NOTE = """---
title: Marico
type: company
sector: FMCG
created: 2025-11-16
verified:
- by: human:user
  at: 2026-08-18T12:00:00Z
tags: [entity_type/company, sector/fmcg]
---

## Company Overview

Marico body.
"""


class TestOkfBumpGenerated:
    def test_generated_and_stale_after_set(self):
        out = bump_generated(_OKF_NOTE, "derive_insights.py/v1",
                             now="2026-08-18T09:00:00Z")
        fm = yaml.safe_load(out.split("\n---\n")[0][4:])
        assert fm["generated"] == {"by": "derive_insights.py/v1",
                                   "at": "2026-08-18T09:00:00Z"}
        # no sources -> base = derive date + 180d
        assert fm["stale_after"] == "2027-02-14"

    def test_verified_survives_bump_byte_exact(self):
        out = bump_generated(_OKF_NOTE, "derive_insights.py/v1",
                             now="2026-08-18T09:00:00Z")
        fm = yaml.safe_load(out.split("\n---\n")[0][4:])
        assert fm["verified"] == [{"by": "human:user",
                                   "at": "2026-08-18T12:00:00Z"}]

    def test_stale_after_uses_max_source_last_modified(self):
        note = _OKF_NOTE.replace(
            "tags:",
            "sources:\n- id: a\n  resource: /Reports/A.pdf\n"
            "  last_modified: 2026-08-01\n"
            "- id: b\n  resource: /Reports/B.pdf\n"
            "  last_modified: 2026-08-13\ntags:",
        )
        out = bump_generated(note, "x", now="2026-08-18T09:00:00Z")
        fm = yaml.safe_load(out.split("\n---\n")[0][4:])
        assert fm["stale_after"] == "2027-02-09"  # 2026-08-13 + 180

    def test_body_preserved_byte_exact(self):
        out = bump_generated(_OKF_NOTE, "x", now="2026-08-18T09:00:00Z")
        assert out.endswith("---\n\n## Company Overview\n\nMarico body.\n")

    def test_key_order_preserved(self):
        out = bump_generated(_OKF_NOTE, "x", now="2026-08-18T09:00:00Z")
        keys = list(yaml.safe_load(out.split("\n---\n")[0][4:]))
        assert keys[:3] == ["title", "type", "sector"]

    def test_no_frontmatter_unchanged(self):
        assert bump_generated("# bare note\n", "x") == "# bare note\n"

    def test_broken_yaml_unchanged(self):
        bad = "---\ntitle: [unclosed\n---\nbody"
        assert bump_generated(bad, "x") == bad

    def test_idempotent_shape_when_now_fixed(self):
        a = bump_generated(_OKF_NOTE, "x", now="2026-08-18T09:00:00Z")
        b = bump_generated(a, "x", now="2026-08-18T09:00:00Z")
        assert a == b

    def test_schema_clean_after_bump(self):
        from helpers.validators.frontmatter_schema import validate_frontmatter
        out = bump_generated(_OKF_NOTE, "derive_insights.py/v1",
                             now="2026-08-18T09:00:00Z")
        fm = yaml.safe_load(out.split("\n---\n")[0][4:])
        fm.update(normalized_name="Marico",
                  permalink="/companies/fmcg/marico",
                  last_modified="2026-07-07", market_cap="large_cap",
                  ticker=None)  # required key; null = unlisted
        assert validate_frontmatter(fm, "company") == []


class TestRenderNotesBumpsFrontmatter:
    """render_notes (apply path) bumps generated on every note write."""

    NOTE = _OKF_NOTE

    def _setup(self, tmp_path):
        conn = _connect(tmp_path)
        note = tmp_path / "Marico.md"
        note.write_text(self.NOTE, encoding="utf-8")
        conn.execute(
            "INSERT INTO entities(name, file_path) VALUES (?, ?)",
            ("Marico", str(note)),
        )
        conn.commit()
        return conn, note

    def test_write_bumps_generated(self, tmp_path):
        conn, note = self._setup(tmp_path)
        quotes = [di.Quote(entity="Marico", quote_text="q1",
                           speaker_name="A", speaker_title="B",
                           as_of_edition="Marico DLF BSE",
                           source_ref="derive:quotes:X:1")]
        try:
            written, _, _ = di.render_notes(
                {("Marico", "Marico DLF BSE"): quotes},
                dry_run=False, conn=conn,
            )
            assert written == 1
            fm = yaml.safe_load(note.read_text().split("\n---\n")[0][4:])
            assert fm["generated"]["by"] == di._OKF_ACTOR
            assert fm["verified"] == [{"by": "human:user",
                                       "at": "2026-08-18T12:00:00Z"}]
            assert "BEGIN auto chatter block" in note.read_text()
        finally:
            conn.close()

    def test_dry_run_leaves_note_untouched(self, tmp_path):
        conn, note = self._setup(tmp_path)
        quotes = [di.Quote(entity="Marico", quote_text="q1",
                           speaker_name="A", speaker_title="B",
                           as_of_edition="Marico DLF BSE",
                           source_ref="derive:quotes:X:1")]
        try:
            before = note.read_text()
            di.render_notes({("Marico", "Marico DLF BSE"): quotes},
                            dry_run=True, conn=conn)
            assert note.read_text() == before  # no generated key yet
        finally:
            conn.close()


class TestAsOfEditionStems:
    """N2 (okf_readside): apply_* normalize as_of_edition to edition STEMS
    at the write boundary when an edition_index is supplied; verbatim
    otherwise (back-compat + honest miss)."""

    def _index(self):
        # Hand-built edition index (norm_key -> Path), mirroring
        # edition_index.source_note_index's shape — no real-vault coupling.
        from helpers.core.edition_index import norm_key
        return {norm_key("Threads in the Data"):
                Path("findata/The_Chatter/Threads_in_the_Data.md")}

    def _quote(self, edition):
        return di.Quote(
            entity="Marico",
            quote_text="Parachute delivered 10% volume growth this quarter.",
            speaker_name="Saugata Gupta", speaker_title="MD & CEO",
            as_of_edition=edition,
            source_ref="derive:quotes:Threads_in_the_Data:12")

    def test_stem_written_when_resolvable(self, tmp_path):
        conn = _connect(tmp_path)
        di.apply_quotes([self._quote("Threads in the Data")], conn=conn,
                        dry_run=False, index=self._index())
        got = conn.execute("SELECT as_of_edition FROM quotes").fetchone()[0]
        assert got == "Threads_in_the_Data"  # stem, not the title
        conn.close()

    def test_verbatim_when_unresolvable(self, tmp_path):
        conn = _connect(tmp_path)
        di.apply_quotes([self._quote("Blue Star")], conn=conn, dry_run=False,
                        index=self._index())
        got = conn.execute("SELECT as_of_edition FROM quotes").fetchone()[0]
        assert got == "Blue Star"  # honest miss — never guessed
        conn.close()

    def test_verbatim_without_index(self, tmp_path):
        """Back-compat: no index (tests, direct callers) -> title verbatim."""
        conn = _connect(tmp_path)
        di.apply_quotes([self._quote("Threads in the Data")], conn=conn,
                        dry_run=False)
        got = conn.execute("SELECT as_of_edition FROM quotes").fetchone()[0]
        assert got == "Threads in the Data"  # unchanged behavior
        conn.close()

    def test_metrics_stem_too(self, tmp_path):
        conn = _connect(tmp_path)
        m = di.Metric(entity="Marico", value_raw="Rs 4,400 crore",
                      metric_label="Revenue", value_num=4400.0, unit="crore",
                      period="FY26", as_of_edition="Threads in the Data",
                      source_quote="revenue grew to Rs 4,400 crore",
                      source_ref="derive:metrics:Threads_in_the_Data:12")
        di.apply_metrics([m], conn=conn, dry_run=False, index=self._index())
        got = conn.execute(
            "SELECT as_of_edition FROM company_metrics").fetchone()[0]
        assert got == "Threads_in_the_Data"
        conn.close()


class TestChatterFootnotes:
    """okf_readside N1: per-claim [^chatter-<stem>] footnotes in auto chatter
    blocks, when an edition index is supplied. No index -> legacy output."""

    def _index(self):
        from helpers.core.edition_index import norm_key
        return {norm_key("Threads in the Data"):
                Path("findata/The_Chatter/Threads_in_the_Data.md")}

    def _quote(self):
        return di.Quote(entity="Marico",
                        quote_text="Parachute delivered 10% volume growth this quarter.",
                        speaker_name="Saugata Gupta", speaker_title="MD & CEO",
                        as_of_edition="Threads in the Data",
                        source_ref="derive:quotes:T:1")

    def test_footnote_on_attribution_plus_definition(self):
        block = di.render_chatter_block("Threads in the Data", [self._quote()],
                                        index=self._index())
        assert "> — Saugata Gupta, MD & CEO [^chatter-Threads_in_the_Data]" in block
        assert "[^chatter-Threads_in_the_Data]: Threads in the Data — [[Threads_in_the_Data]]" in block
        # definition sits inside the sentinel (before the Source footer, after END? no:)
        assert block.index("[^chatter-Threads_in_the_Data]:") < block.index("*Source:")

    def test_no_footnote_when_unresolvable(self):
        block = di.render_chatter_block("Blue Star", [self._quote()],
                                        index=self._index())
        assert "[^chatter-" not in block  # honest miss, zero footnotes

    def test_no_footnote_without_index(self):
        """Back-compat: the 2-arg form (tests/direct callers) is unchanged."""
        block = di.render_chatter_block("Threads in the Data", [self._quote()])
        assert "[^chatter-" not in block
        assert "> — Saugata Gupta, MD & CEO" in block

    def test_second_render_byte_identical(self):
        q = self._quote()
        a = di.render_chatter_block("Threads in the Data", [q], index=self._index())
        b = di.render_chatter_block("Threads in the Data", [q], index=self._index())
        assert a == b  # deterministic — the --stale-only fixed point holds

    def test_hand_written_footnote_namespace_safe(self):
        """chatter- namespace: a hand footnote id can never collide."""
        block = di.render_chatter_block("Threads in the Data", [self._quote()],
                                        index=self._index())
        assert block.count("[^chatter-Threads_in_the_Data]:") == 1


# --------------------------------------------------------------------------- #
# Stable writes: no-op re-apply preserves id + created_at                      #
# --------------------------------------------------------------------------- #

class TestStableWrites:
    """The prefix replace keeps content-identical rows byte-stable.

    A no-op derive cycle used to DELETE+INSERT every row (fresh
    created_at, reshuffled ids) — guaranteeing quotes/company_metrics
    parquet churn in every snapshot. The multiset content match keeps
    unchanged rows untouched.
    """

    def test_reapply_identical_quotes_keeps_ids_and_created_at(self, tmp_path):
        conn = _connect(tmp_path)
        di.apply_quotes(_APPLY_QUOTES(), conn=conn, dry_run=False)
        # Pin old timestamps so a same-second restamp cannot hide a regression.
        conn.execute("UPDATE quotes SET created_at = '2000-01-01 00:00:00'")
        conn.commit()
        before = conn.execute(
            "SELECT id, quote_text, created_at FROM quotes ORDER BY id").fetchall()
        di.apply_quotes(_APPLY_QUOTES(), conn=conn, dry_run=False)
        after = conn.execute(
            "SELECT id, quote_text, created_at FROM quotes ORDER BY id").fetchall()
        assert [tuple(r) for r in before] == [tuple(r) for r in after]
        conn.close()

    def test_reapply_identical_metrics_keeps_ids_and_created_at(self, tmp_path):
        conn = _connect(tmp_path)
        m = [di.Metric(entity="Marico", metric_label="revenue",
                       value_raw="₹9,000 crore", value_num=9000.0,
                       unit="crore", period="FY26",
                       as_of_edition="Marico DLF BSE",
                       source_ref="derive:metrics:Marico_DLF_BSE:45")]
        di.apply_metrics(m, conn=conn, dry_run=False)
        conn.execute("UPDATE company_metrics SET created_at = '2000-01-01 00:00:00'")
        conn.commit()
        before = conn.execute(
            "SELECT id, created_at FROM company_metrics ORDER BY id").fetchall()
        di.apply_metrics(m, conn=conn, dry_run=False)
        after = conn.execute(
            "SELECT id, created_at FROM company_metrics ORDER BY id").fetchall()
        assert [tuple(r) for r in before] == [tuple(r) for r in after]
        conn.close()

    def test_changed_row_gets_fresh_id_sibling_kept(self, tmp_path):
        """Content change = delete + fresh insert; the unchanged sibling
        keeps its id (stable anchors for downstream references)."""
        conn = _connect(tmp_path)
        qs = _APPLY_QUOTES()
        di.apply_quotes(qs, conn=conn, dry_run=False)
        kept_id = conn.execute(
            "SELECT id FROM quotes WHERE quote_text = ?",
            (qs[0].quote_text,)).fetchone()[0]
        changed = [qs[0],
                   di.Quote(entity="Marico",
                            quote_text="Rewritten quote text.",
                            speaker_name="Saugata Gupta",
                            speaker_title="MD & CEO",
                            as_of_edition="Marico DLF BSE",
                            source_ref=qs[1].source_ref)]
        di.apply_quotes(changed, conn=conn, dry_run=False)
        still = conn.execute(
            "SELECT id FROM quotes WHERE quote_text = ?",
            (qs[0].quote_text,)).fetchone()
        assert still is not None and still[0] == kept_id
        texts = {r["quote_text"] for r in conn.execute(
            "SELECT quote_text FROM quotes").fetchall()}
        assert texts == {qs[0].quote_text, "Rewritten quote text."}
        conn.close()


def _APPLY_QUOTES():
    """Two-quote payload mirroring TestApplyQuotes._quotes."""
    return [
        di.Quote(entity="Marico",
                 quote_text="Parachute delivered 10% volume growth.",
                 speaker_name="Saugata Gupta", speaker_title="MD & CEO",
                 as_of_edition="Marico DLF BSE",
                 source_ref="derive:quotes:Marico_DLF_BSE:45"),
        di.Quote(entity="Marico",
                 quote_text="Copra prices corrected meaningfully this quarter.",
                 speaker_name="Saugata Gupta", speaker_title="MD & CEO",
                 as_of_edition="Marico DLF BSE",
                 source_ref="derive:quotes:Marico_DLF_BSE:45"),
    ]
