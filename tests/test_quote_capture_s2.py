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
