#!/usr/bin/env python3
"""Unit tests for ``parse_newsletter.create_entity`` idempotency (Bundle F10).

F10 replaced the racy SELECT-then-INSERT guard with ``INSERT OR IGNORE``,
making the existence check atomic with the write and consistent with the
``graph_edges`` writes in the same function. These tests pin that idempotency
contract without running the full parse_newsletter pipeline.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

from helpers.core import parse_newsletter as pn  # noqa: E402


def _schema_sql():
    """Minimal entities + graph_edges schema matching what create_entity writes."""
    return """
    CREATE TABLE entities(
        name TEXT PRIMARY KEY,
        entity_type TEXT,
        sector_classification TEXT,
        normalized_name TEXT,
        file_path TEXT,
        ticker TEXT,
        last_updated TEXT
    );
    CREATE TABLE graph_edges(
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        edge_type TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        UNIQUE(source, target, edge_type)
    );
    """


class TestCreateEntityIdempotent:
    def test_create_entity_is_idempotent_on_repeat(self, tmp_path, monkeypatch):
        """F10: calling create_entity twice must not raise and must not
        duplicate the entity row. INSERT OR IGNORE makes the check atomic."""
        monkeypatch.setattr(pn, "PROJECT_ROOT", tmp_path)
        conn = sqlite3.connect(":memory:")
        conn.executescript(_schema_sql())
        # Pre-seed the sector entity so the membership edges are attempted too.
        conn.execute("INSERT INTO entities(name, entity_type) VALUES ('Tech', 'sector')")
        conn.commit()
        try:
            pn.create_entity(
                conn, "Acme Corp", "Tech", "ACME", apply=True, sector_entities={"Tech"}
            )
            # Second call: must not raise (the old SELECT-then-INSERT was racy;
            # INSERT OR IGNORE is atomic + idempotent).
            pn.create_entity(
                conn, "Acme Corp", "Tech", "ACME", apply=True, sector_entities={"Tech"}
            )
            conn.commit()
            # Exactly one entity row.
            n = conn.execute("SELECT COUNT(*) FROM entities WHERE name='Acme Corp'").fetchone()[0]
            assert n == 1
            # Exactly one part_of / one has_company edge (idempotent via
            # UNIQUE + INSERT OR IGNORE on graph_edges too).
            n_part = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE source='Acme Corp' AND edge_type='part_of'"
            ).fetchone()[0]
            n_has = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE target='Acme Corp' "
                "AND edge_type='has_company'"
            ).fetchone()[0]
            assert n_part == 1
            assert n_has == 1
        finally:
            conn.close()

    def test_create_entity_skips_edges_when_sector_missing(self, tmp_path, monkeypatch):
        """Sanity: if the sector entity doesn't exist, no membership edges are
        written (the `if sector in se` guard). This behaviour is unchanged by
        F10; the test pins it so the INSERT OR IGNORE refactor can't regress it."""
        monkeypatch.setattr(pn, "PROJECT_ROOT", tmp_path)
        conn = sqlite3.connect(":memory:")
        conn.executescript(_schema_sql())
        conn.commit()
        try:
            pn.create_entity(
                conn, "Solo Co", "GhostSector", "SOLO", apply=True, sector_entities=set()
            )
            conn.commit()
            assert (
                conn.execute("SELECT COUNT(*) FROM entities WHERE name='Solo Co'").fetchone()[0]
                == 1
            )
            assert conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 0
        finally:
            conn.close()


class TestRenderStubShape:
    """D3: render_stub must emit frontmatter matching the canonical conventions
    so new stub notes are born clean (no validator warnings). Previously the
    title was QUOTED and ticker:null omitted `listed: false` — the two drifts
    the D3 normalization pass fixed across the existing corpus."""

    def test_title_is_unquoted(self):
        s = pn.render_stub("Acme Corp", "Acme_Corp", "Tech", "ACME", "companies/tech/acme_corp")
        assert "title: Acme Corp\n" in s
        assert 'title: "Acme Corp"' not in s, "title must not be quoted"

    def test_unlisted_company_gets_listed_false(self):
        """ticker:null marks an unlisted company — render it explicit with
        `listed: false` so the listed_missing validator check doesn't fire."""
        s = pn.render_stub("Acme Corp", "Acme_Corp", "Tech", None, "companies/tech/acme_corp")
        assert "ticker: null\n" in s
        assert "listed: false\n" in s

    def test_listed_company_has_no_listed_field(self):
        """A company WITH a ticker must NOT emit `listed:` (listed:true is
        implicit; the field exists only to mark the unlisted category)."""
        s = pn.render_stub("Acme Corp", "Acme_Corp", "Tech", "ACME", "companies/tech/acme_corp")
        assert "ticker: ACME\n" in s
        assert "\nlisted:" not in s, "listed field must not appear for a listed company"

    def test_market_cap_key_always_present(self):
        """The company JSON schema REQUIRES market_cap — a stub without it
        fatals static-checks until hand-fixed. null is the schema-legal
        'unknown'; the enrichment flow upgrades it once a cap is known."""
        for ticker in ("ACME", None):
            s = pn.render_stub("Acme Corp", "Acme_Corp", "Tech", ticker, "companies/tech/acme_corp")
            assert "market_cap: null\n" in s

    def test_stub_is_born_okf_machine_confirmed(self):
        """OKF §5.2: provenance is written where it is generated — render_stub
        stamps `generated` (actor parse_newsletter.py/v1) + `stale_after`, so
        fresh stubs enter the census as machine-confirmed, not unverified."""
        s = pn.render_stub("Acme Corp", "Acme_Corp", "Tech", None, "companies/tech/acme_corp")
        assert "by: parse_newsletter.py/v1" in s
        assert re.search(r"^  at: '\d{4}-\d{2}-\d{2}T", s, re.M)
        assert re.search(r"^stale_after: '\d{4}-\d{2}-\d{2}'$", s, re.M)


# ---------------------------------------------------------------------------
# normalize_name — pure function
# ---------------------------------------------------------------------------
def test_normalize_name_basic():
    assert pn.normalize_name("Infosys") == "Infosys"


def test_normalize_name_multi_word():
    assert pn.normalize_name("HDFC Bank") == "HDFC_Bank"


def test_normalize_name_strips_special_chars():
    assert pn.normalize_name("Tata & Sons") == "Tata_Sons"


def test_normalize_name_strips_parens():
    assert pn.normalize_name("Foo (Bar)") == "Foo_Bar"


def test_normalize_name_strips_hyphens():
    assert pn.normalize_name("Foo-Bar") == "Foo_Bar"


def test_normalize_name_idempotent():
    assert pn.normalize_name(pn.normalize_name("Foo & Bar Ltd")) == pn.normalize_name(
        "Foo & Bar Ltd"
    )


def test_normalize_name_leading_digit():
    assert pn.normalize_name("360 ONE WAM") == "360_ONE_WAM"


def test_normalize_name_empty():
    assert pn.normalize_name("") == ""


def test_normalize_name_collapses_double_underscore():
    assert pn.normalize_name("A &_B") == "A_B"


# ---------------------------------------------------------------------------
# render_stub — pure function (depends on date.today, but we check structure)
# ---------------------------------------------------------------------------
def test_render_stub_structure():
    note = pn.render_stub(
        name="Test Co",
        normalized_name="Test_Co",
        sector="Technology",
        ticker="TEST.NS",
        permalink="companies/technology/test_co",
    )
    assert "title: Test Co" in note
    assert "type: company" in note
    # bump_generated round-trips the stub through YAML, so quoting survives
    # only where the plain form would be ambiguous (Ather-style unquoted is
    # the canonical emission for ordinary tickers)
    assert "ticker: TEST.NS\n" in note
    assert "sector/technology" in note
    assert "geography/india" in note
    assert "normalized_name: Test_Co" in note
    assert "permalink: companies/technology/test_co" in note
    assert "# Test Co" in note
    assert "## Company Overview" in note


def test_render_stub_no_ticker():
    note = pn.render_stub(
        name="Unlisted Co",
        normalized_name="Unlisted_Co",
        sector="Technology",
        ticker=None,
        permalink="companies/technology/unlisted_co",
    )
    assert "ticker: null" in note
    assert "listed: false" in note


def test_render_stub_tags():
    note = pn.render_stub("X", "X", "Healthcare", "X.NS", "permalink")
    assert "- entity_type/company" in note
    assert "- sector/healthcare" in note
    assert "- geography/india" in note


# ---------------------------------------------------------------------------
# extract_companies — pure function (operates on text)
# ---------------------------------------------------------------------------
def test_extract_companies_with_pipe():
    content = "## Reliance Industries | Large Cap | Energy\n\nText."
    results = list(pn.extract_companies(content))
    assert len(results) == 1
    assert "Reliance Industries" in results[0][0]


def test_extract_companies_no_cap_or_pipe():
    content = "## Some Random Heading\n\nText."
    results = list(pn.extract_companies(content))
    assert len(results) == 0


def test_extract_companies_dedup():
    content = (
        "## Reliance Industries | Large Cap | Energy\n\n## Reliance Industries | Mid Cap | Energy\n"
    )
    results = list(pn.extract_companies(content))
    # classify dedups, but extract yields both; check both
    assert len(results) >= 1


def test_extract_companies_strips_suffix():
    content = "## Tata Motors Ltd | Large Cap | Auto\n\nText."
    results = list(pn.extract_companies(content))
    assert results[0][0] == "Tata Motors"


def test_extract_companies_too_short_rejected():
    content = "## AB | Large Cap | X\n\nText."
    results = list(pn.extract_companies(content))
    assert len(results) == 0
