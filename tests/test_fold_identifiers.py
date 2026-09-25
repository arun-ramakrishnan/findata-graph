import sqlite3

from helpers.maintenance import fold_identifiers as fold
from helpers.misc.backfill_identifiers import ensure_schema


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT, ticker TEXT)")
    conn.executemany(
        "INSERT INTO entities VALUES (?, 'company', ?)",
        [("Alpha", "AAA.NS"), ("Beta", "BBB"), ("Gamma", "CCC.BO")],
    )
    ensure_schema(conn)
    return conn


def test_build_candidates_uses_suffixes_and_skips_ambiguous_symbols():
    conn = _conn()
    listings = [
        ("NSE", "AAA", "INE000A00001"),
        ("BSE", "AAA", "INE000A00002"),
        ("NSE", "BBB", "INE000B00001"),
        ("BSE", "BBB", "INE000B00002"),
        ("BSE", "CCC", "INE000C00001"),
    ]
    stats = fold.build_candidates(conn, listings, [])
    assert stats.direct_isin == 2
    assert stats.ambiguous_tickers == 1
    assert set(stats.candidates) == {
        ("Alpha", "isin", "INE000A00001", "NSE", "exchange_sync"),
        ("Gamma", "isin", "INE000C00001", "BSE", "exchange_sync"),
    }


def test_optional_cik_fold_is_store_only_and_idempotent():
    conn = _conn()
    ciks = [("AAA", "123456")]
    stats = fold.build_candidates(conn, [], ciks)
    assert stats.candidates == [("Alpha", "cik", "123456", "sec.gov", "sec.gov/company_tickers")]
    assert fold.apply_candidates(conn, stats.candidates) == 1
    assert fold.apply_candidates(conn, stats.candidates) == 0
    assert fold.build_candidates(conn, [], ciks).candidates == []
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 3
