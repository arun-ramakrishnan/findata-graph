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
    -- part_of/has_company retirement (2026-10-19): membership is written
    -- to the canonical incidence store.
    CREATE TABLE hyper_edges(
        id INTEGER PRIMARY KEY,
        edge_type TEXT NOT NULL,
        label TEXT NOT NULL,
        weight REAL NOT NULL DEFAULT 1.0,
        valid_from DATE, valid_to DATE,
        source_ref TEXT NOT NULL,
        properties TEXT NOT NULL DEFAULT '{}',
        created_at DATETIME,
        UNIQUE(edge_type, label)
    );
    CREATE TABLE hyper_incidences(
        edge_id INTEGER NOT NULL,
        entity_name TEXT NOT NULL,
        weight REAL, direction TEXT, role TEXT,
        valid_from DATE, valid_to DATE,
        PRIMARY KEY (edge_id, entity_name)
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
            # Exactly one sector membership (canonical incidence store since
            # the 2026-10-19 retirement; idempotent via the incidence PK).
            n_mem = conn.execute(
                "SELECT COUNT(*) FROM hyper_incidences hi "
                "JOIN hyper_edges he ON he.id = hi.edge_id "
                "WHERE hi.entity_name='Acme Corp' AND he.edge_type='sector' "
                "AND he.label='Tech'"
            ).fetchone()[0]
            assert n_mem == 1
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM graph_edges WHERE edge_type IN ('part_of','has_company')"
                ).fetchone()[0]
                == 0
            )
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
    sector_dirs = {"Energy", "Technology"}  # simplified test set
    results = list(pn.extract_companies(content, sector_dirs))
    assert len(results) == 1
    assert "Reliance Industries" in results[0][0]
    assert results[0][2] == "Energy"  # header_sector


def test_extract_companies_no_cap_or_pipe():
    content = "## Some Random Heading\n\nText."
    results = list(pn.extract_companies(content))
    assert len(results) == 0


def test_extract_companies_dedup():
    content = (
        "## Reliance Industries | Large Cap | Energy\n\n## Reliance Industries | Mid Cap | Energy\n"
    )
    sector_dirs = {"Energy", "Technology"}
    results = list(pn.extract_companies(content, sector_dirs))
    # classify dedups, but extract yields both; check both
    assert len(results) >= 1
    assert results[0][2] == "Energy"  # header_sector


def test_extract_companies_strips_suffix():
    content = "## Tata Motors Ltd | Large Cap | Auto\n\nText."
    sector_dirs = {"Automotive"}
    results = list(pn.extract_companies(content, sector_dirs))
    assert results[0][0] == "Tata Motors"


def test_extract_companies_too_short_rejected():
    content = "## AB | Large Cap | X\n\nText."
    results = list(pn.extract_companies(content))
    assert len(results) == 0


# --------------------------------------------------------------------------- #
# Country layer C2: ticker-derived geography seeding (no hardcoded india)      #
# --------------------------------------------------------------------------- #
def test_render_stub_geography_from_ticker():
    # Indian ticker -> india carriers (unchanged behaviour for the common case)
    note = pn.render_stub("X", "X", "Technology", "X.NS", "permalink")
    assert "- geography/india" in note
    assert "geography: india" in note
    # Foreign home market via the audited override map
    note = pn.render_stub("Dia", "Dia", "Beverages", "DEO", "permalink")
    assert "- geography/uk" in note
    assert "geography: uk" in note
    # US plain symbol
    note = pn.render_stub("Wal", "Wal", "Retail", "WMT", "permalink")
    assert "- geography/usa" in note


def test_render_stub_no_ticker_has_no_geography():
    """Unlisted companies carry NO geography carrier until a ticker lands —
    the pre-C2 hardcoded geography/india default is gone."""
    note = pn.render_stub("Unlisted Co", "Unlisted_Co", "Technology", None, "permalink")
    assert "geography/" not in note
    assert "geography:" not in note


class TestSectorCommentary:
    """S6 (resolver_extractor_hardening #396): analyst-voiced sections
    spanning >=3 named companies route to the sector note. Audit rewrite:
    the previous detector could never emit (its scan was gated on a set
    nothing populated) and counted 'co'-substring word hits."""

    def test_analyst_section_with_three_companies_routes(self):
        content = (
            "## BFSI Sector Preview\n"
            "\n"
            "Analysts at JM Financial expect private banks to re-rate.\n"
            "\n"
            "HDFC Bank trades at 3x book while ICICI Bank and Axis Bank "
            "defend margins.\n"
        )
        names = {"HDFC Bank", "ICICI Bank", "Axis Bank"}
        out = pn._detect_sector_commentary(content, names)
        assert len(out) == 1
        heading, line, companies = out[0]
        assert "BFSI" in heading
        assert line == 1
        assert set(companies) == names

    def test_company_headings_never_route(self):
        """A pipe-format company note is a company note even when it quotes
        analysts — only free-form section heads can become commentary."""
        content = (
            "## HDFC Bank | Large Cap | BFSI\n"
            "\n"
            "Analysts at the brokerage note margin pressure at HDFC Bank, "
            "ICICI Bank and Axis Bank.\n"
        )
        names = {"HDFC Bank", "ICICI Bank", "Axis Bank"}
        assert pn._detect_sector_commentary(content, names) == []

    def test_bank_word_alone_is_not_an_analyst_marker(self):
        """'Bank' in prose (HDFC Bank!) never marks a section
        non-management — only analyst/brokerage/sell-side phrases do."""
        content = (
            "## Banking capex round-up\n"
            "\n"
            "HDFC Bank, ICICI Bank and Axis Bank all guided capex higher "
            "for the year.\n"
        )
        names = {"HDFC Bank", "ICICI Bank", "Axis Bank"}
        assert pn._detect_sector_commentary(content, names) == []

    def test_two_companies_below_threshold(self):
        content = (
            "## Mid-cap technology\n"
            "\n"
            "Analysts see Netweb Technologies and Cyient DLM compounding.\n"
        )
        names = {"Netweb Technologies", "Cyient DLM"}
        assert pn._detect_sector_commentary(content, names) == []

    def test_common_word_never_counts_as_company(self):
        """The old 'co'-substring counting is gone: prose words never
        satisfy the >=3 gate."""
        content = (
            "## Macro desk\n"
            "\n"
            "Analysts expect second-order effects on income, account "
            "openings and core sector output to compound.\n"
        )
        assert pn._detect_sector_commentary(content, {"HDFC Bank"}) == []


class TestListedIssues:
    """S8: listing prose contradicts a stub's listed: false; entities
    without a ticker surface as explicit manual-check items."""

    def test_listing_prose_flags_the_company(self):
        content = (
            "## Netweb Technologies | Small Cap | Technology\n"
            "\n"
            "Netweb Technologies recently listed on the exchanges and "
            "debuted strong.\n"
        )
        contradictions = pn._detect_listed_contradictions(content, {"Netweb Technologies"})
        assert len(contradictions) == 1
        row = contradictions[0]
        assert row["company"] == "Netweb Technologies"
        assert row["line"] == 3
        assert "recently listed" in row["prose"]

    def test_word_boundary_no_false_positive(self):
        """'Targeting' must not flag a company named Target (the substring
        version of this check did)."""
        content = "## Strategy\n\nThe targeting framework improved.\n"
        assert pn._detect_listed_contradictions(content, {"Target"}) == []

    def test_missing_ticker_items_surface(self):
        new_cos = [("Freshco Ltd", 4, "FMCG")]
        existing_cos = [("Oldco", 9, None)]
        items = pn._missing_ticker_items(
            new_cos, existing_cos, stubs={"Oldco"}, tickers={"Freshco Ltd": "FRESH"}
        )
        # Oldco: no ticker -> manual check, flagged as stub.
        # Freshco: has a ticker -> not listed.
        assert items == [
            {
                "name": "Oldco",
                "is_stub": True,
                "reason": "no ticker on file — manual lookup before listing claims",
            }
        ]

    def test_deprecated_shim_still_returns_pair(self):
        content = "Netweb Technologies recently listed on the exchanges.\n"
        contradictions, missing = pn._detect_listed_issues(content, {"Netweb Technologies"})
        assert len(contradictions) == 1
        assert missing == []
