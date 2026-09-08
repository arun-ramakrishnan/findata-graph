#!/usr/bin/env python3
"""S2 resolver ladder tests (quote_capture_coverage proposal).

Tiers are deterministic; the fuzzy tail only ever produces worklist
suggestions. Alias targets are vetted against the live entities table
(the relations-domain alias file is NOT reused — premier/micron class).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from helpers.graph import derive_insights as di  # noqa: E402

MAP = {
    "titan": "Titan",
    "cloudflare": "Cloudflare",
    "voltas": "Voltas",
    "dixon technologies": "Dixon Technologies",
    "mitsubishi chemical group": "Mitsubishi Chemical Group",
    "allied blenders and distillers": "Allied Blenders and Distillers",
    "divis laboratories": "Divis Laboratories",
    "nestle": "Nestle",
    "steel authority of india": "Steel Authority of India",
    "amazon": "Amazon",
    "l&t finance": "L&T Finance",
    "mahindra and mahindra": "Mahindra & Mahindra",
    "multi commodity exchange": "Multi Commodity Exchange",
}


class TestResolveLadder:
    def test_t1_exact(self):
        assert di._resolve_ladder("Voltas", MAP) == ("Voltas", "exact", [])

    def test_t2_unescape(self):
        ent, tier, _ = di._resolve_ladder("L\\&T Finance", MAP)
        assert (ent, tier) == ("L&T Finance", "unescape")

    def test_t1_ascii_folded_exact(self):
        # The exact key is ascii-folded, so diacritics resolve at T1.
        ent, tier, _ = di._resolve_ladder("Nestlé", MAP)
        assert (ent, tier) == ("Nestle", "exact")

    def test_t3_trailing_separator(self):
        ent, tier, _ = di._resolve_ladder("Cloudflare,", MAP)
        assert (ent, tier) == ("Cloudflare", "strip")

    def test_t3_pipe_artifact_i(self):
        ent, tier, _ = di._resolve_ladder("Voltas I", MAP)
        assert (ent, tier) == ("Voltas", "strip")

    def test_t3_parenthetical(self):
        ent, tier, _ = di._resolve_ladder("Dixon Technologies (India)", MAP)
        assert (ent, tier) == ("Dixon Technologies", "strip")

    def test_t4_query_side_qualifier(self):
        ent, tier, _ = di._resolve_ladder("Titan Company", MAP)
        assert (ent, tier) == ("Titan", "qualifier")

    def test_t4_entity_side_qualifier(self):
        ent, tier, _ = di._resolve_ladder("Mitsubishi Chemical", MAP)
        assert (ent, tier) == ("Mitsubishi Chemical Group", "qualifier")

    def test_t5_symbol_fold(self):
        ent, tier, _ = di._resolve_ladder("Mahindra & Mahindra", MAP)
        assert (ent, tier) == ("Mahindra & Mahindra", "symbol")

    def test_t6_alias(self):
        ent, tier, _ = di._resolve_ladder("SAIL", MAP)
        assert (ent, tier) == ("Steel Authority of India", "alias")

    def test_miss_with_suggestions(self):
        ent, tier, sugg = di._resolve_ladder("Totally Unknown Things", MAP)
        assert ent is None and tier == "miss"

    def test_entity_side_ambiguity_does_not_guess(self, monkeypatch):
        # Hermetic: the user alias file (S7) may legitimately resolve
        # `Welspun` by decision (it did, 2026-09-07); pin the merged map to
        # the shipped `_QUOTE_ALIASES` so this tests the ladder tiers only.
        monkeypatch.setattr(di, "_merged_quote_aliases", lambda: dict(di._QUOTE_ALIASES))
        m = dict(MAP)
        m["welspun corp"] = "Welspun Corp"
        m["welspun india"] = "Welspun India"
        ent, tier, sugg = di._resolve_ladder("Welspun", m)
        assert ent is None  # multi-hit never guesses
        assert set(sugg) == {"Welspun Corp", "Welspun India"}


class TestAliasVetting:
    """Every pinned alias target must exist in the live entities table."""

    def test_alias_targets_exist(self):
        db = PROJECT_ROOT / "memory" / "research.db"
        if not db.exists():
            pytest.skip("research.db not present")
        import sqlite3

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        names = {r[0].lower() for r in conn.execute("SELECT name FROM entities").fetchall()}
        conn.close()
        bad = {k: v for k, v in di._QUOTE_ALIASES.items() if v.lower() not in names}
        assert not bad, f"alias targets missing from entities: {bad}"


class TestJunkCanonicals:
    def test_initiatives_not_a_section(self):
        note = "## Initiatives\nsome text\n## Acme Ltd | Large Cap | FMCG\nbody\n"
        sections = list(di.iter_company_sections(note))
        assert [s.canonical_name for s in sections] == ["Acme"]

    def test_extract_sections_integration_escape(self):
        note = (
            "## L\\&T Finance | Large Cap | Financials\n"
            '"The finance quote is definitely long enough to count here."\n'
            "- Jane Smith, CFO\n"
        )
        sections = list(di.iter_company_sections(note))
        resolver = {"l&t finance": "L&T Finance"}
        quotes, _ = di._extract_sections(sections, "ed", "stem", resolver)
        assert len(quotes) == 1
        assert quotes[0].entity == "L&T Finance"


class TestBuildResolverMap:
    def test_ingests_every_company_row(self, tmp_path):
        # Regression (S2, 2026-09-07): an indentation slip made the map
        # return after the FIRST row; the S0 audit's walker=0 caught it.
        import sqlite3

        db = tmp_path / "research.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE entities (name TEXT, normalized_name TEXT, entity_type TEXT)")
        conn.executemany(
            "INSERT INTO entities VALUES (?, ?, 'company')",
            [("Acme Ltd", "Acme_Ltd"), ("Zenith", "Zenith"), ("MRF", "MRF")],
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        m = di._build_resolver_map(conn)
        conn.close()
        assert m["acme ltd"] == "Acme Ltd"
        assert m["acme_ltd"] == "Acme Ltd"
        assert m["zenith"] == "Zenith"
        assert m["mrf"] == "MRF"
        assert len(m) == 4  # 3 names + Acme's underscore variant (MRF dedupes)


class TestPersonRoleCatchAll:
    """Person/role catch-all tier (2026-09-08): chairman quotes go to
    Quotes.md — a company-shaped heading naming a person routes to the
    catch-all entity after every company tier misses."""

    # Role-titled headings.
    @pytest.mark.parametrize(
        "name",
        ["SEBI Chairman", "RBI Deputy Governor", "Finance Minister", "Chief Economist"],
    )
    def test_role_word_routes(self, name):
        assert di._person_role_heading(name) is True

    # Personal-name shapes (incl. honorific + long Indian names).
    @pytest.mark.parametrize("name", ["Brad Setser", "Dr. Rohit Chandra", "Tamal Bandyopadhyay"])
    def test_name_shape_routes(self, name):
        assert di._person_role_heading(name) is True

    # Company/brand shapes stay on the worklist, never the catch-all.
    @pytest.mark.parametrize(
        "name",
        [
            "Bajaj Hindustan",  # companyish geo token
            "Lenskart Solutions",  # companyish suffix
            "Titan Company",  # companyish suffix
            "Lenskart",  # single token — brand-shaped
            "3M India",  # digit token
            "Mahindra & Mahindra",  # `&` shape
        ],
    )
    def test_company_shapes_stay_worklisted(self, name):
        assert di._person_role_heading(name) is False

    def test_ambiguous_titlecase_triple_routes_recoverably(self):
        # `Totally Unknown Things` is a bare Titlecase triple — undecidable
        # as person vs company by shape. The tier routes it to the catch-all
        # (nothing on the floor) with the raw heading stamped on the row, so
        # a misroute is recoverable via the Quotes triage surface; the ladder
        # itself still reports miss + suggestions (test_miss_with_suggestions).
        assert di._person_role_heading("Totally Unknown Things") is True

    def test_extract_sections_routes_person_to_catch_all(self):
        note = (
            "### SEBI Chairman | 30 years of NSE Clearing Limited\n"
            '"Novation and netting transformed individual promises into obligations."\n'
            "- Shri Tuhin Kanta Pandey, Chairman, SEBI\n"
        )
        sections = list(di.iter_company_sections(note))
        assert [s.canonical_name for s in sections] == ["SEBI Chairman"]
        quotes, metrics = di._extract_sections(sections, "ed", "stem", {})
        assert len(quotes) == 1
        assert quotes[0].entity == di._CATCH_ALL_ENTITY
        assert quotes[0].properties["kind"] == "catch_all"
        assert quotes[0].properties["heading"] == (
            "SEBI Chairman | 30 years of NSE Clearing Limited"
        )
        assert metrics == []  # sector discipline: no metrics off persons

    def test_extract_sections_unknown_company_still_misses(self):
        note = (
            "## Bajaj Hindustan | Mid Cap | FMCG\n"
            '"A company quote that is long enough to count for the walker."'
            "- Jane Smith, CFO\n"
        )
        sections = list(di.iter_company_sections(note))
        quotes, metrics = di._extract_sections(sections, "ed", "stem", {})
        assert quotes == [] and metrics == []  # terminus, worklist visibility
