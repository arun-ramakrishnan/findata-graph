"""Tests for helpers/misc/database_integrity_check.py — the untested methods.

Most of DatabaseIntegrityChecker is already covered by test_validators.py
(validate_file_path, check_integrity, check_relations, check_entity_tags,
check_orphan_companies, check_normalization — 25+ tests). This file fills
the three remaining gaps:

  - get_all_entities        — the basic SELECT wrapper
  - check_events            — D7 events-table integrity (unknown_type,
                              orphaned entity, bad properties JSON)
  - check_duplicate_tickers — semantic-uniqueness (two companies, one ticker)

These need an `events` table, which the shared `integrity_db` fixture's
schema does not create — so each test builds a small DB in tmp_path with
the production `events` DDL and seeds the specific defect under test.
"""
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

HELPERS = Path(__file__).resolve().parents[1] / "helpers" / "misc"
sys.path.insert(0, str(HELPERS))
from database_integrity_check import DatabaseIntegrityChecker  # noqa: E402

ENTITIES_DDL = (
    "CREATE TABLE entities ("
    "name TEXT PRIMARY KEY, entity_type TEXT, file_path TEXT, "
    "normalized_name TEXT, sector_classification TEXT, ticker TEXT)"
)
EVENTS_DDL = (
    "CREATE TABLE events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "entity TEXT NOT NULL, "
    "event_type TEXT NOT NULL, "
    "event_date DATE, period TEXT, date_precision TEXT, "
    "magnitude TEXT, counterparty TEXT, source_quote TEXT, "
    "as_of_edition TEXT, source_ref TEXT NOT NULL, "
    "properties TEXT NOT NULL DEFAULT '{}', "
    "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "CHECK (json_valid(properties)))"
)


@contextmanager
def _make_checker_db(tmp_path: Path):
    """Build a minimal DB with entities + events tables, yield (path, checker).

    The checker uses base_path=tmp_path so file_path validation resolves
    against the temp dir (not the real repo). Tests insert their own rows.
    Closes the checker's memoized connection on exit.
    """
    db_path = tmp_path / "check.db"
    conn = sqlite3.connect(db_path)
    conn.execute(ENTITIES_DDL)
    conn.execute(EVENTS_DDL)
    conn.commit()
    conn.close()
    checker = DatabaseIntegrityChecker(db_path=str(db_path), base_path=str(tmp_path))
    try:
        yield db_path, checker
    finally:
        checker.close()


# --- get_all_entities -------------------------------------------------------


class TestGetAllEntities:
    def test_returns_all_rows_as_dicts(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.executemany(
                "INSERT INTO entities (name, entity_type, file_path, normalized_name, "
                "sector_classification) VALUES (?,?,?,?,?)",
                [
                    ("Zen Co", "company", "findata/Companies/X/Zen_Co.md", "Zen_Co", "X"),
                    ("X", "sector", "findata/Sectors/X.md", "X", None),
                ],
            )
            conn.commit()
            conn.close()

            entities = checker.get_all_entities()
            assert len(entities) == 2
            # each row is a dict keyed by column name
            names = {e["name"] for e in entities}
            assert names == {"Zen Co", "X"}
            # the projected columns are present
            zen = next(e for e in entities if e["name"] == "Zen Co")
            assert zen["entity_type"] == "company"
            assert zen["file_path"] == "findata/Companies/X/Zen_Co.md"

    def test_empty_db_returns_empty_list(self, tmp_path):
        with _make_checker_db(tmp_path) as (_, checker):
            assert checker.get_all_entities() == []

    def test_ordered_by_entity_type_then_name(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.executemany(
                "INSERT INTO entities (name, entity_type) VALUES (?,?)",
                [("Zeta Co", "company"), ("Alpha Co", "company"), ("Z Sector", "sector")],
            )
            conn.commit()
            conn.close()
            entities = checker.get_all_entities()
            # companies sort before sectors; within type, alphabetical
            assert [e["name"] for e in entities] == ["Alpha Co", "Zeta Co", "Z Sector"]


# --- check_events -----------------------------------------------------------


class TestCheckEvents:
    def test_clean_events_zero_errors(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities (name, entity_type) VALUES ('Co A', 'company')")
            conn.execute(
                "INSERT INTO events (entity, event_type, source_ref, properties) "
                "VALUES ('Co A', 'guidance', 'manual:x', '{}')"
            )
            conn.commit()
            conn.close()

            r = checker.check_events()
            assert r["total"] == 1
            assert r["unknown_type"] == 0
            assert r["orphaned"] == 0
            assert r["bad_properties"] == 0
            assert r["errors"] == 0

    def test_unknown_event_type_flagged(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities (name, entity_type) VALUES ('Co A', 'company')")
            # 'speculation' is not in CANONICAL_EVENT_TYPES
            conn.execute(
                "INSERT INTO events (entity, event_type, source_ref, properties) "
                "VALUES ('Co A', 'speculation', 'manual:x', '{}')"
            )
            conn.commit()
            conn.close()

            r = checker.check_events()
            assert r["unknown_type"] == 1
            assert r["errors"] >= 1

    def test_orphaned_entity_flagged(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities (name, entity_type) VALUES ('Co A', 'company')")
            # event references an entity not in the entities table
            conn.execute(
                "INSERT INTO events (entity, event_type, source_ref, properties) "
                "VALUES ('Ghost Co', 'guidance', 'manual:x', '{}')"
            )
            conn.commit()
            conn.close()

            r = checker.check_events()
            assert r["orphaned"] == 1
            assert r["errors"] >= 1

    def test_missing_events_table_returns_zeros(self, tmp_path):
        # A DB with no events table (pre-D7) → the early-return zeros branch
        db_path = tmp_path / "no_events.db"
        conn = sqlite3.connect(db_path)
        conn.execute(ENTITIES_DDL)
        conn.commit()
        conn.close()
        checker = DatabaseIntegrityChecker(db_path=str(db_path), base_path=str(tmp_path))
        try:
            r = checker.check_events()
            assert r == {
                "total": 0, "unknown_type": 0, "orphaned": 0,
                "bad_properties": 0, "errors": 0,
            }
        finally:
            checker.close()


# --- check_duplicate_tickers ------------------------------------------------


class TestCheckDuplicateTickers:
    def test_clean_distinct_tickers_zero_errors(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.executemany(
                "INSERT INTO entities (name, entity_type, ticker) VALUES (?,?,?)",
                [("Co A", "company", "A.NS"), ("Co B", "company", "B.NS")],
            )
            conn.commit()
            conn.close()

            r = checker.check_duplicate_tickers()
            assert r["duplicate_ticker_groups"] == {}
            assert r["errors"] == 0

    def test_duplicate_ticker_grouped_and_flagged(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.executemany(
                "INSERT INTO entities (name, entity_type, ticker) VALUES (?,?,?)",
                [
                    # two distinct names sharing one ticker — the bug class
                    ("Groww", "company", "GROWW.NS"),
                    ("Billionbrains", "company", "GROWW.NS"),
                    ("Other Co", "company", "OTHER.NS"),
                ],
            )
            conn.commit()
            conn.close()

            r = checker.check_duplicate_tickers()
            assert "GROWW.NS" in r["duplicate_ticker_groups"]
            group = r["duplicate_ticker_groups"]["GROWW.NS"]
            assert set(group) == {"Groww", "Billionbrains"}
            assert r["errors"] == 1  # one duplicate group

    def test_null_and_empty_tickers_ignored(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.executemany(
                "INSERT INTO entities (name, entity_type, ticker) VALUES (?,?,?)",
                [
                    ("Unlisted A", "company", None),
                    ("Unlisted B", "company", ""),
                    ("Listed C", "company", "C.NS"),
                ],
            )
            conn.commit()
            conn.close()

            r = checker.check_duplicate_tickers()
            # NULL/empty tickers are excluded — they don't form a "group"
            assert r["duplicate_ticker_groups"] == {}
            assert r["errors"] == 0

    def test_sectors_ignored(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.executemany(
                "INSERT INTO entities (name, entity_type, ticker) VALUES (?,?,?)",
                [
                    ("Co A", "company", "DUP.NS"),
                    # a sector with the same ticker string must NOT group with the company
                    ("Some Sector", "sector", "DUP.NS"),
                ],
            )
            conn.commit()
            conn.close()

            r = checker.check_duplicate_tickers()
            # only the company row counts; no duplicate group
            assert r["duplicate_ticker_groups"] == {}
            assert r["errors"] == 0



# ===========================================================================
# Additional unit tests — pure helpers + DB-backed check methods
# ===========================================================================

GRAPH_EDGES_DDL = (
    "CREATE TABLE graph_edges ("
    "source TEXT, target TEXT, edge_type TEXT, "
    "valid_from TEXT, valid_to TEXT, properties TEXT)"
)
ENTITY_TAGS_DDL = (
    "CREATE TABLE entity_tags ("
    "entity_name TEXT, tag TEXT, "
    "UNIQUE(entity_name, tag))"
)
QUOTES_DDL = (
    "CREATE TABLE quotes ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, "
    "speaker TEXT, source_quote TEXT, source_ref TEXT NOT NULL, "
    "as_of_edition TEXT, properties TEXT NOT NULL DEFAULT '{}', "
    "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
)
COMPANY_METRICS_DDL = (
    "CREATE TABLE company_metrics ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, "
    "metric_label TEXT, value_raw TEXT, value_num REAL, "
    "unit TEXT, period TEXT, as_of_edition TEXT, "
    "source_quote TEXT, source_ref TEXT NOT NULL, "
    "properties TEXT NOT NULL DEFAULT '{}', "
    "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
)


@contextmanager
def _make_rich_db(tmp_path: Path, extra_tables=None):
    """Build a DB with entities + events + optional extra tables."""
    db_path = tmp_path / "check.db"
    conn = sqlite3.connect(db_path)
    conn.execute(ENTITIES_DDL)
    conn.execute(EVENTS_DDL)
    conn.execute(GRAPH_EDGES_DDL)
    conn.execute(ENTITY_TAGS_DDL)
    conn.execute(QUOTES_DDL)
    conn.execute(COMPANY_METRICS_DDL)
    if extra_tables:
        for ddl in extra_tables:
            conn.execute(ddl)
    conn.commit()
    conn.close()
    checker = DatabaseIntegrityChecker(db_path=str(db_path), base_path=str(tmp_path))
    try:
        yield db_path, checker
    finally:
        checker.close()


# ---------------------------------------------------------------------------
# _meaningful_tokens — static method, pure
# ---------------------------------------------------------------------------
class TestMeaningfulTokens:
    def test_strips_punctuation(self):
        result = DatabaseIntegrityChecker._meaningful_tokens("Infosys, Ltd.")
        assert "infosys" in result
        assert "ltd" not in result  # stopword

    def test_empty_string(self):
        assert DatabaseIntegrityChecker._meaningful_tokens("") == set()

    def test_none_input(self):
        assert DatabaseIntegrityChecker._meaningful_tokens(None) == set()

    def test_normalizes_case(self):
        result = DatabaseIntegrityChecker._meaningful_tokens("TATA Motors")
        assert "tata" in result
        assert "motors" in result

    def test_strips_non_alnum(self):
        result = DatabaseIntegrityChecker._meaningful_tokens("360-ONE!")
        assert "360" in result
        assert "one" in result


# ---------------------------------------------------------------------------
# _check_directory_structure — semi-pure
# ---------------------------------------------------------------------------
class TestDirectoryStructure:
    def test_company_valid(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_directory_structure(
            "findata/Companies/Energy/Test_Co.md", "company") is True

    def test_company_too_deep(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_directory_structure(
            "findata/Companies/Energy/Sub/Test_Co.md", "company") is False

    def test_sector_valid(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_directory_structure(
            "findata/Sectors/Energy.md", "sector") is True

    def test_super_sector_valid(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_directory_structure(
            "findata/Super_Sectors/Financials.md", "super_sector") is True

    def test_sub_sector_exempt(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_directory_structure(
            "any/path.md", "sub_sector") is True

    def test_unknown_prefix(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_directory_structure(
            "other/Test_Co.md", "company") is False


# ---------------------------------------------------------------------------
# _check_filename_format — pure
# ---------------------------------------------------------------------------
class TestFilenameFormat:
    def test_valid_pascalcase(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_filename_format("findata/Companies/X/Test_Co.md") is True

    def test_valid_leading_digit(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_filename_format("findata/Companies/X/360_ONE.md") is True

    def test_empty_stem(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_filename_format("findata/Companies/X/.md") is False

    def test_special_chars(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        assert checker._check_filename_format("findata/Companies/X/Test-Co.md") is False


# ---------------------------------------------------------------------------
# validate_file_path — with tmp_path files
# ---------------------------------------------------------------------------
class TestValidateFilePath:
    def test_empty_path(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        ok, msg = checker.validate_file_path("", "company")
        assert ok is False

    def test_nonexistent_file(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        ok, msg = checker.validate_file_path("findata/Companies/X/Missing.md", "company")
        assert ok is False
        assert "does not exist" in msg

    def test_not_markdown(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        (tmp_path / "findata" / "Companies" / "X").mkdir(parents=True)
        (tmp_path / "findata" / "Companies" / "X" / "Test.txt").write_text("x")
        ok, msg = checker.validate_file_path("findata/Companies/X/Test.txt", "company")
        assert ok is False

    def test_valid_company(self, tmp_path):
        checker = DatabaseIntegrityChecker(base_path=str(tmp_path))
        sector_dir = tmp_path / "findata" / "Companies" / "Energy"
        sector_dir.mkdir(parents=True)
        (sector_dir / "Test_Co.md").write_text("# Test")
        ok, msg = checker.validate_file_path("findata/Companies/Energy/Test_Co.md", "company")
        assert ok is True
        assert msg == "Valid"


# ---------------------------------------------------------------------------
# check_orphan_companies — DB-backed
# ---------------------------------------------------------------------------
class TestOrphanCompanies:
    def test_no_relations_table(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            result = checker.check_orphan_companies()
            assert result["errors"] == 0
            assert result["total_companies"] == 0

    def test_clean_companies(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Co A', 'company', 'x', 'Co_A', 'X', NULL)")
            conn.execute("INSERT INTO entities VALUES ('Co B', 'company', 'x', 'Co_B', 'X', NULL)")
            conn.execute("INSERT INTO graph_edges VALUES ('Co A', 'X', 'part_of', NULL, NULL, '{}')")
            conn.execute("INSERT INTO graph_edges VALUES ('Co B', 'X', 'part_of', NULL, NULL, '{}')")
            conn.execute(
                "CREATE VIEW relations AS "
                "SELECT source, target, edge_type AS relation_type FROM graph_edges"
            )
            conn.commit()
            conn.close()
            result = checker.check_orphan_companies()
            assert result["orphan_companies"] == 0
            assert result["errors"] == 0

    def test_orphan_detected(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Co A', 'company', 'x', 'Co_A', 'X', NULL)")
            conn.execute("INSERT INTO entities VALUES ('Co B', 'company', 'x', 'Co_B', 'X', NULL)")
            # Only Co A has part_of; Co B is orphan
            conn.execute("INSERT INTO graph_edges VALUES ('Co A', 'X', 'part_of', NULL, NULL, '{}')")
            conn.execute(
                "CREATE VIEW relations AS "
                "SELECT source, target, edge_type AS relation_type FROM graph_edges"
            )
            conn.commit()
            conn.close()
            result = checker.check_orphan_companies()
            assert result["orphan_companies"] == 1
            assert result["errors"] == 1


# ---------------------------------------------------------------------------
# check_normalization — DB-backed
# ---------------------------------------------------------------------------
class TestNormalization:
    def test_clean(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Test Co', 'company', 'findata/Companies/X/Test_Co.md', 'Test_Co', 'X', NULL)")
            conn.commit()
            conn.close()
            result = checker.check_normalization()
            assert result["errors"] == 0

    def test_missing_normalized_name(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Test Co', 'company', 'x', NULL, 'X', NULL)")
            conn.commit()
            conn.close()
            result = checker.check_normalization()
            assert result["missing"] == 1
            assert result["errors"] == 1

    def test_duplicate_normalized_name(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('A', 'company', 'x', 'Same_Name', 'X', NULL)")
            conn.execute("INSERT INTO entities VALUES ('B', 'company', 'x', 'Same_Name', 'X', NULL)")
            conn.commit()
            conn.close()
            result = checker.check_normalization()
            assert len(result["duplicates"]) == 1
            assert result["errors"] >= 1

    def test_bad_format_double_underscore(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('A', 'company', 'x', 'Bad__Name', 'X', NULL)")
            conn.commit()
            conn.close()
            result = checker.check_normalization()
            assert len(result["bad_format"]) == 1

    def test_file_mismatch_warning(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Test', 'company', 'path/Different.md', 'Test', 'X', NULL)")
            conn.commit()
            conn.close()
            result = checker.check_normalization()
            assert len(result["file_mismatches"]) == 1


# ---------------------------------------------------------------------------
# check_market_cap_conflicts — DB-backed
# ---------------------------------------------------------------------------
class TestMarketCapConflicts:
    def test_no_entity_tags_table(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            result = checker.check_market_cap_conflicts()
            assert result["errors"] == 0

    def test_clean(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Co A', 'company', 'x', 'Co_A', 'X', NULL)")
            conn.execute("INSERT INTO entity_tags VALUES ('Co A', 'market_cap/large_cap')")
            conn.commit()
            conn.close()
            result = checker.check_market_cap_conflicts()
            assert result["errors"] == 0

    def test_conflict_detected(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Co A', 'company', 'x', 'Co_A', 'X', NULL)")
            conn.execute("INSERT INTO entity_tags VALUES ('Co A', 'market_cap/large_cap')")
            conn.execute("INSERT INTO entity_tags VALUES ('Co A', 'market_cap/mid_cap')")
            conn.commit()
            conn.close()
            result = checker.check_market_cap_conflicts()
            assert result["errors"] == 1
            assert len(result["conflicts"]) == 1
            assert len(result["conflicts"][0]["tags"]) == 2


# ---------------------------------------------------------------------------
# check_validity_window — DB-backed
# ---------------------------------------------------------------------------
class TestValidityWindow:
    def test_no_graph_edges(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            result = checker.check_validity_window()
            assert result["warnings"] == 0
            assert result["errors"] == 0

    def test_all_dated(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO graph_edges VALUES ('A', 'B', 'acquired', '2024-01-01', NULL, '{}')")
            conn.commit()
            conn.close()
            result = checker.check_validity_window()
            assert result["warnings"] == 0
            assert "acquired" in result["by_type"]

    def test_missing_valid_from_on_acquired(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO graph_edges VALUES ('A', 'B', 'acquired', NULL, NULL, '{}')")
            conn.commit()
            conn.close()
            result = checker.check_validity_window()
            assert result["warnings"] == 1

    def test_part_of_not_counted(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO graph_edges VALUES ('A', 'X', 'part_of', NULL, NULL, '{}')")
            conn.commit()
            conn.close()
            result = checker.check_validity_window()
            # part_of is not in _SHOULD_BE_DATED, so no warning
            assert result["warnings"] == 0


# ---------------------------------------------------------------------------
# check_fuzzy_duplicate_names — DB-backed
# ---------------------------------------------------------------------------
class TestFuzzyDuplicates:
    def test_no_duplicates(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Tata Motors', 'company', 'x', 'Tata_Motors', 'X', 'TATAMOTORS.NS')")
            conn.execute("INSERT INTO entities VALUES ('Infosys', 'company', 'x', 'Infosys', 'X', 'INFY.NS')")
            conn.commit()
            conn.close()
            result = checker.check_fuzzy_duplicate_names()
            assert len(result["fuzzy_duplicate_pairs"]) == 0

    def test_single_token_match(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Hindalco', 'company', 'x', 'Hindalco', 'X', 'A')")
            conn.execute("INSERT INTO entities VALUES ('Hindalco Industries', 'company', 'x', 'Hindalco_Industries', 'X', 'B')")
            conn.commit()
            conn.close()
            result = checker.check_fuzzy_duplicate_names()
            assert len(result["fuzzy_duplicate_pairs"]) == 1

    def test_same_ticker_skipped(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Hindalco', 'company', 'x', 'Hindalco', 'X', 'HINDALCO.NS')")
            conn.execute("INSERT INTO entities VALUES ('Hindalco Industries', 'company', 'x', 'Hindalco_Industries', 'X', 'HINDALCO.NS')")
            conn.commit()
            conn.close()
            result = checker.check_fuzzy_duplicate_names()
            # Same ticker → skipped (already caught by duplicate_tickers check)
            assert len(result["fuzzy_duplicate_pairs"]) == 0


# ---------------------------------------------------------------------------
# check_quotes — DB-backed
# ---------------------------------------------------------------------------
class TestQuotes:
    def test_no_quotes_table(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            result = checker.check_quotes()
            assert result["errors"] == 0

    def test_clean(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Co A', 'company', 'x', 'Co_A', 'X', NULL)")
            conn.execute("INSERT INTO quotes (entity, speaker, source_quote, source_ref) VALUES ('Co A', 'CEO', 'quote', 'test')")
            conn.commit()
            conn.close()
            result = checker.check_quotes()
            assert result["errors"] == 0
            assert result["total"] == 1

    def test_orphaned_entity(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO quotes (entity, speaker, source_quote, source_ref) VALUES ('Ghost', 'CEO', 'q', 't')")
            conn.commit()
            conn.close()
            result = checker.check_quotes()
            assert result["orphaned"] == 1
            assert result["errors"] == 1


# ---------------------------------------------------------------------------
# check_company_metrics — DB-backed
# ---------------------------------------------------------------------------
class TestCompanyMetrics:
    def test_no_table(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            result = checker.check_company_metrics()
            assert result["errors"] == 0

    def test_clean(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Co A', 'company', 'x', 'Co_A', 'X', NULL)")
            conn.execute("INSERT INTO company_metrics (entity, metric_label, source_ref) VALUES ('Co A', 'pe_ratio', 'test')")
            conn.commit()
            conn.close()
            result = checker.check_company_metrics()
            assert result["errors"] == 0

    def test_orphaned(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO company_metrics (entity, metric_label, source_ref) VALUES ('Ghost', 'pe_ratio', 't')")
            conn.commit()
            conn.close()
            result = checker.check_company_metrics()
            assert result["orphaned"] == 1
            assert result["errors"] == 1


# ---------------------------------------------------------------------------
# check_graph_summary — DB-backed (advisory, never errors)
# ---------------------------------------------------------------------------
class TestGraphSummary:
    def test_empty_db(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            result = checker.check_graph_summary()
            assert result["errors"] == 0
            assert result["entity_counts"] == {}

    def test_with_data(self, tmp_path):
        with _make_rich_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Co A', 'company', 'x', 'Co_A', 'Energy', NULL)")
            conn.execute("INSERT INTO entities VALUES ('Co B', 'company', 'x', 'Co_B', 'Energy', NULL)")
            conn.execute("INSERT INTO entities VALUES ('Energy', 'sector', 'x', 'Energy', NULL, NULL)")
            conn.execute("INSERT INTO graph_edges VALUES ('Co A', 'Energy', 'part_of', NULL, NULL, '{}')")
            conn.execute("INSERT INTO graph_edges VALUES ('Co B', 'Energy', 'part_of', NULL, NULL, '{}')")
            conn.commit()
            conn.close()
            result = checker.check_graph_summary()
            assert result["entity_counts"]["company"] == 2
            assert result["entity_counts"]["sector"] == 1
            assert result["edge_counts"]["part_of"] == 2
            assert result["sector_size_summary"]["sector_count"] == 1


# ---------------------------------------------------------------------------
# check_db_meta — DB-backed
# ---------------------------------------------------------------------------
class TestDbMeta:
    def test_no_db_meta_table(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            result = checker.check_db_meta()
            assert result["errors"] >= 1
            assert "db_meta" in result["reasons"][0]

    def test_with_db_meta(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO db_meta VALUES ('generation', '100')")
            conn.execute("INSERT INTO db_meta VALUES ('schema_version', '7')")
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
            conn.close()
            result = checker.check_db_meta()
            assert "generation" in result
            assert result["generation"] == "100"


# ---------------------------------------------------------------------------
# _query — DB-backed
# ---------------------------------------------------------------------------
class TestQuery:
    def test_query_returns_rows(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO entities VALUES ('Co A', 'company', 'x', 'Co_A', 'X', NULL)")
            conn.commit()
            conn.close()
            rows = checker._query("SELECT name FROM entities")
            assert len(rows) == 1
            assert rows[0][0] == "Co A"


# ---------------------------------------------------------------------------
# get_connection — memoized
# ---------------------------------------------------------------------------
class TestGetConnection:
    def test_file_not_found(self, tmp_path):
        checker = DatabaseIntegrityChecker(db_path="/nonexistent/path.db", base_path=str(tmp_path))
        with pytest.raises(FileNotFoundError):
            checker.get_connection()

    def test_memoized(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            conn1 = checker.get_connection()
            conn2 = checker.get_connection()
            assert conn1 is conn2  # same object

    def test_close_resets(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            checker.get_connection()
            checker.close()
            assert checker._conn is None


# --- check_note_tags (newsletter_notes_adoption S4) --------------------------


class TestCheckNoteTags:
    def _note(self, tags: str) -> str:
        return f"---\ntype: newsletter\ntags:\n{tags}\n---\n# Ed\n"

    def test_clean_rows_zero_errors(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            note = tmp_path / "findata" / "The_Chatter" / "Ed.md"
            note.parent.mkdir(parents=True)
            note.write_text(self._note("- series/the_chatter"))
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE note_tags (note_path TEXT NOT NULL, "
                "tag TEXT NOT NULL, PRIMARY KEY (note_path, tag))"
            )
            conn.execute(
                "INSERT INTO note_tags VALUES "
                "('findata/The_Chatter/Ed.md', 'series/the_chatter')"
            )
            conn.commit()
            conn.close()
            assert checker.check_note_tags() == {
                "total": 1, "stale": 0, "errors": 0}

    def test_missing_note_and_dropped_tag_flagged(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            note = tmp_path / "findata" / "The_Chatter" / "Ed.md"
            note.parent.mkdir(parents=True)
            note.write_text(self._note("- publisher/zerodha"))  # tag gone
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE note_tags (note_path TEXT NOT NULL, "
                "tag TEXT NOT NULL, PRIMARY KEY (note_path, tag))"
            )
            conn.executemany(
                "INSERT INTO note_tags VALUES (?, ?)",
                [("findata/The_Chatter/Ed.md", "series/the_chatter"),
                 ("findata/The_Chatter/Gone.md", "series/the_chatter")],
            )
            conn.commit()
            conn.close()
            r = checker.check_note_tags()
            assert r == {"total": 2, "stale": 2, "errors": 2}

    def test_missing_table_returns_zeros(self, tmp_path):
        with _make_checker_db(tmp_path) as (db_path, checker):
            assert checker.check_note_tags() == {
                "total": 0, "stale": 0, "errors": 0}
