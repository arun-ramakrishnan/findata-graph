"""Tests for helpers/core/fuzzy_match.py."""

import sys
from pathlib import Path


HELPERS = Path(__file__).resolve().parents[1] / "helpers" / "core"
sys.path.insert(0, str(HELPERS))
import fuzzy_match as fm  # noqa: E402


# Sample entities for testing
ENTITIES = [
    "Tata Consultancy Services",
    "Tata Motors Passenger Vehicles",
    "Tata Steel",
    "Tata Power",
    "Tata Chemicals",
    "HDFC Bank",
    "HDFC Life",
    "ICICI Bank",
    "Infosys",
    "Wipro",
    "Mahindra & Mahindra",
    "Bajaj Finance",
    "Bajaj Finserv",
    "Sun Pharmaceutical Industries",
    "Dr Reddys Laboratories",
    "Asian Paints",
    "Berger Paints India",
    "UltraTech Cement",
    "Ambuja Cement",
    "FSN E-Commerce",
    "One 97 Communications PayTM",
    "Interglobe Aviation",
    "Adani Ports and SEZ",
    "Adani Power",
    "Adani Enterprises",
    "Larsen and Toubro",
    "L&T Finance",
    "L&T Technology Services",
    "Bharti Airtel",
    "Hindustan Unilever",
    "Nestle India",
    "Britannia Industries",
    "Dabur India",
    "Godrej Consumer Products",
    "Pidilite Industries",
    "Polycab India",
    "Havells India",
    "Crompton Greaves Consumer Electricals",
    "KEI Industries",
    "Finolex Cables",
    "Container Corporation of India",
    "NTPC",
    "Power Grid Corporation of India",
    "Reliance Industries",
    "Maruti Suzuki India",
    "Hero MotoCorp",
    "Eicher Motors",
    "TVS Motor Company",
    "Bajaj Auto",
    "Ashok Leyland",
]


class TestAbbreviationLookup:
    def test_tcs_abbreviation(self):
        match, method, score = fm.fuzzy_match("TCS", ENTITIES)
        assert match == "Tata Consultancy Services"
        assert method == "abbreviation"
        assert score == 1.0

    def test_nykaa_abbreviation(self):
        match, method, score = fm.fuzzy_match("NYKAA", ENTITIES)
        assert match == "FSN E-Commerce"
        assert method == "abbreviation"

    def test_paytm_abbreviation(self):
        match, method, score = fm.fuzzy_match("PAYTM", ENTITIES)
        assert match == "One 97 Communications PayTM"
        assert method == "abbreviation"

    def test_m_and_m_abbreviation(self):
        match, method, score = fm.fuzzy_match("M&M", ENTITIES)
        assert match == "Mahindra & Mahindra"
        assert method == "abbreviation"

    def test_lowercase_abbreviation(self):
        match, method, score = fm.fuzzy_match("tcs", ENTITIES)
        assert match == "Tata Consultancy Services"
        assert method == "abbreviation"

    def test_unknown_abbreviation_falls_through(self):
        match, method, score = fm.fuzzy_match("XYZUNKNOWN", ENTITIES)
        assert match is None
        assert method is None


class TestExactMatch:
    def test_exact_match_case_insensitive(self):
        match, method, score = fm.fuzzy_match("HDFC Bank", ENTITIES)
        assert match == "HDFC Bank"
        assert method == "exact"

    def test_exact_match_different_case(self):
        match, method, score = fm.fuzzy_match("hdfc bank", ENTITIES)
        assert match == "HDFC Bank"
        assert method == "exact"


class TestEmptyAndWhitespaceQueries:
    """Regression: an empty query must NOT match anything.

    Before 2026-08-09, fuzzy_match("") returned a false positive
    ('Tata Consultancy Services', 'word_overlap', 1.0): the containment
    check `query_lower in entity_lower` is True for every entity when
    query_lower is the empty string ('' is a substring of everything).
    """

    def test_empty_query_returns_none(self):
        match, method, score = fm.fuzzy_match("", ENTITIES)
        assert match is None
        assert method is None
        assert score == 0.0

    def test_empty_query_word_overlap_returns_none(self):
        match, score = fm.word_overlap_match("", ENTITIES)
        assert match is None
        assert score == 0.0

    def test_whitespace_only_query_returns_none(self):
        for q in ("   ", "\t", "\n", " \t "):
            match, method, score = fm.fuzzy_match(q, ENTITIES)
            assert match is None, f"{q!r} matched {match!r}"
            assert method is None
            assert score == 0.0


class TestWordOverlap:
    def test_prefix_match(self):
        match, method, score = fm.fuzzy_match("Tata Motor", ENTITIES)
        assert match == "Tata Motors Passenger Vehicles"
        assert method == "word_overlap"

    def test_sibling_disambiguation_port_vs_power(self):
        match, method, score = fm.fuzzy_match("Adani Port", ENTITIES)
        assert match == "Adani Ports and SEZ"
        assert method == "word_overlap"

    def test_sibling_disambiguation_finance_vs_finserv(self):
        match, method, score = fm.fuzzy_match("Bajaj Fin", ENTITIES)
        assert match == "Bajaj Finance"
        assert method == "word_overlap"

    def test_multi_word_abbreviation(self):
        match, method, score = fm.fuzzy_match("Sun Pharma", ENTITIES)
        assert match == "Sun Pharmaceutical Industries"
        assert method == "word_overlap"

    def test_single_word_prefix(self):
        match, method, score = fm.fuzzy_match("Ambuja", ENTITIES)
        assert match == "Ambuja Cement"
        assert method == "word_overlap"

    def test_no_match(self):
        match, method, score = fm.fuzzy_match("Nonexistent Company XYZ", ENTITIES)
        assert match is None
        assert method is None


class TestTokenize:
    def test_removes_stopwords(self):
        tokens = fm._tokenize("Tata Motors Limited")
        assert "limited" not in tokens
        assert "tata" in tokens
        assert "motors" in tokens

    def test_handles_ampersand(self):
        tokens = fm._tokenize("Mahindra & Mahindra")
        assert "&" not in tokens
        assert "mahindra" in tokens

    def test_handles_hyphen(self):
        tokens = fm._tokenize("Crompton Greaves Consumer Electricals")
        assert "crompton" in tokens
        assert "greaves" in tokens


class TestBuildSpellfixTable:
    def test_builds_table(self, tmp_path):
        import sqlite3
        import sqlite_spellfix

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_spellfix.extension_path())

        result = fm.build_spellfix_table(conn, ENTITIES)
        assert result is True

        # Verify table exists and has data
        cursor = conn.execute("SELECT COUNT(*) FROM entities_fuzzy")
        count = cursor.fetchone()[0]
        assert count == len(ENTITIES)

        conn.close()

    def test_handles_missing_extension_gracefully(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        # Don't enable_load_extension — should return False
        result = fm.build_spellfix_table(conn, ENTITIES)
        assert result is False
        conn.close()


class TestFuzzyMatchWithSpellfix:
    def test_spellfix_fallback_for_typos(self, tmp_path):
        import sqlite3
        import sqlite_spellfix

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_spellfix.extension_path())
        fm.build_spellfix_table(conn, ENTITIES)

        # "Infosis" is a typo for "Infosys" — word-overlap fails (no shared word)
        # but spellfix1 should catch it
        match, method, score = fm.fuzzy_match("Infosis", ENTITIES, spellfix_conn=conn)
        assert match == "Infosys"
        assert method == "spellfix1"

        conn.close()


# ---------------------------------------------------------------------------
# word_overlap_match — direct unit tests (covers success path + loop continue)
# ---------------------------------------------------------------------------
def test_word_overlap_match_success():
    """Word overlap succeeds when a distinctive word is shared but no substring."""
    # "airtel" is not generic, not a substring relationship
    match, score = fm.word_overlap_match("Airtel Broadband", ["Bharti Airtel"], threshold=0.3)
    assert match == "Bharti Airtel"
    assert 0 < score <= 1.0


def test_word_overlap_match_picks_best():
    """When multiple entities overlap, the highest-scoring one wins."""
    entities = ["Tata Steel", "Tata Motors Passenger Vehicles"]
    # query "Tata Chemicals Energy" shares "tata" with both
    match, score = fm.word_overlap_match("Tata Chemicals Energy", entities, threshold=0.2)
    assert match is not None
    assert score >= 0.2


def test_word_overlap_match_below_threshold():
    """Overlap below threshold returns None."""
    match, score = fm.word_overlap_match("Airtel Broadband", ["Bharti Airtel"], threshold=0.9)
    assert match is None
    assert score == 0.0


def test_word_overlap_match_only_generic_words():
    """Overlap consisting only of generic words returns None."""
    match, score = fm.word_overlap_match("Power Energy", ["Tata Power"], threshold=0.1)
    assert match is None
    assert score == 0.0


# ---------------------------------------------------------------------------
# fuzzy_match — spellfix1 fallback edge cases
# ---------------------------------------------------------------------------
def test_fuzzy_match_spellfix_exception_is_swallowed(tmp_path):
    """If spellfix table is missing, the exception is swallowed gracefully."""
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    # No entities_fuzzy table → query raises OperationalError → swallowed
    match, method, score = fm.fuzzy_match("nonexistent", ["Some Entity"], spellfix_conn=conn)
    assert match is None
    assert score == 0.0
    conn.close()


def test_fuzzy_match_spellfix_no_results(tmp_path):
    """Spellfix returns no match when query doesn't match any row."""
    import sqlite3
    import sqlite_spellfix

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_spellfix.extension_path())
    fm.build_spellfix_table(conn, ["Tata"])
    match, method, score = fm.fuzzy_match("zzzzz", ["Some Entity"], spellfix_conn=conn)
    # Falls through spellfix to None
    assert match is None
    assert score == 0.0
    conn.close()


def test_fuzzy_match_abbreviation_substring():
    """Abbreviation found as substring of a longer query (line 291)."""
    # "TCS" is in ABBREVIATIONS, len("TCS") >= 3, "TCS" in "TCS Digital"
    match, method, score = fm.fuzzy_match("TCS Digital", ["Some Entity"])
    assert method == "abbreviation"
    assert match == "Tata Consultancy Services"
    assert score == 1.0


def test_build_spellfix_table_idempotent(tmp_path):
    """Calling build_spellfix_table twice on same conn skips CREATE (line 332->335)."""
    import sqlite3
    import sqlite_spellfix

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_spellfix.extension_path())
    assert fm.build_spellfix_table(conn, ["Infosys"]) is True
    # Second call: table already exists → skip CREATE, still INSERT
    assert fm.build_spellfix_table(conn, ["Wipro"]) is True
    conn.close()
