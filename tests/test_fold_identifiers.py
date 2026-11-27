import sqlite3

import duckdb

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


def test_load_cik_rows_accepts_sec_official_shapes(tmp_path):
    simple = tmp_path / "simple.json"
    simple.write_text('{"data":[{"ticker":"AAA","cik_str":123456}]}', encoding="utf-8")
    assert fold.load_cik_rows(simple) == [("AAA", "0000123456")]

    sec = tmp_path / "company_tickers.json"
    sec.write_text(
        '{"0":{"cik_str":320193,"ticker":"AAPL","title":"Apple Inc."}}',
        encoding="utf-8",
    )
    assert fold.load_cik_rows(sec) == [("AAPL", "0000320193")]

    exchange = tmp_path / "company_tickers_exchange.json"
    exchange.write_text(
        '{"fields":["cik","name","ticker","exchange"],"data":[[320193,"Apple Inc.","AAPL","Nasdaq"]]}',
        encoding="utf-8",
    )
    assert fold.load_cik_rows(exchange) == [("AAPL", "0000320193")]


def test_build_candidates_batches_figi_lookups(monkeypatch):
    conn = _conn()
    calls: list[list[str]] = []

    def fake_many(tickers, **_kwargs):
        calls.append(list(tickers))
        return {
            "AAA.NS": fold.FigiRecord(
                figi="BBG000B9XRY4",
                ticker="AAA",
                name="ALPHA",
                exch_code="IN",
                market_sector="Equity",
                security_type="Common Stock",
                share_class_figi="",
                composite_figi="",
            )
        }

    monkeypatch.setattr(fold, "openfigi_lookup_many", fake_many)
    stats = fold.build_candidates(conn, [("NSE", "ZZZ", "INE000Z00001")], [], fetch_figi=True)
    assert calls == [["AAA.NS", "BBB", "CCC.BO"]]
    assert stats.figi == 1
    assert ("Alpha", "figi", "BBG000B9XRY4", "openfigi", "openfigi") in set(stats.candidates)
    assert [r.figi for r in stats.figi_records] == ["BBG000B9XRY4"]


def test_listing_candidates_unknown_suffix_does_not_borrow():
    listings = [
        ("NSE", "BOSS", "INE000BOSS01"),
        ("BSE", "AAA", "INE000A00001"),
        ("NSE", "AAA", "INE000A00002"),
    ]
    assert fold._listing_candidates("BOSS.DE", listings) == (set(), False)
    # A suffixless ticker remains exchange-ambiguous by design.
    values, ambiguous = fold._listing_candidates("AAA", listings)
    assert values == {("BSE", "INE000A00001"), ("NSE", "INE000A00002")}
    assert ambiguous is True


def test_load_exchange_listings_skips_non_equity(tmp_path):
    path = tmp_path / "sources.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE exchange_listings ("
        "isin VARCHAR, name VARCHAR, asset_type VARCHAR, exchange VARCHAR, symbol VARCHAR)"
    )
    con.executemany(
        "INSERT INTO exchange_listings VALUES (?, ?, ?, ?, ?)",
        [
            ("INE000A00001", "Alpha", "equity", "NSE", "AAA"),
            ("INE000B00001", "Beta ETF", "other", "NSE", "BETAETF"),
            ("INE000C00001", "Gamma Debt", "debt", "BSE", "GAMMA"),
            ("INE000D00001", "Delta", None, "BSE", "DELTA"),
        ],
    )
    con.close()
    assert fold.load_exchange_listings(path) == [
        ("NSE", "AAA", "INE000A00001"),
        ("BSE", "DELTA", "INE000D00001"),
    ]


def test_is_isin_rejects_pseudo_ids():
    assert fold._is_isin("INE08PH01015") is True
    assert fold._is_isin("NSE:DCM") is False
    assert fold._is_isin("INE0QNI0101") is False


def test_operator_overrides_repair_owner_and_clear_stale_status():
    conn = _conn()
    conn.execute(
        "INSERT INTO entity_identifiers "
        "(entity_name, identifier_type, identifier_value, namespace, source_ref) "
        "VALUES ('Hugo Boss', 'isin', 'INE0QNI01012', 'NSE', 'exchange_sync')"
    )
    conn.execute(
        "INSERT INTO entity_identifiers "
        "(entity_name, identifier_type, identifier_value, namespace, source_ref) "
        "VALUES ('DCM Shriram', 'isin', 'NSE:DCMSRIND', 'NSE', 'exchange_sync')"
    )
    conn.execute(fold.ENTITY_RESOLUTION_STATUS_DDL)
    conn.execute(
        "INSERT INTO entity_resolution_status "
        "(entity_name, status, detail, decided_at) VALUES "
        "('DeFi Capital Markets, Inc. Common Stock', 'conflict', 'x', 'now')"
    )
    conn.commit()
    reassigned, removed = fold.apply_operator_overrides(conn)
    assert (reassigned, removed) == (2, 1)
    assert (
        conn.execute(
            "SELECT entity_name FROM entity_identifiers WHERE identifier_value='INE0QNI01012'"
        ).fetchone()[0]
        == "Boss Packaging Solutions"
    )
    assert (
        conn.execute(
            "SELECT entity_name FROM entity_identifiers WHERE identifier_value='NSE:DCMSRIND'"
        ).fetchone()[0]
        == "DCM Shriram Industries"
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM entity_resolution_status WHERE entity_name='DeFi Capital Markets, Inc. Common Stock'"
        ).fetchone()[0]
        == 0
    )
    # Idempotent.
    assert fold.apply_operator_overrides(conn) == (0, 0)


def test_foreign_suffixless_ticker_does_not_borrow_indian_listing():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT, ticker TEXT)")
    conn.execute(
        "INSERT INTO entities VALUES ('DeFi Capital Markets, Inc. Common Stock', 'company', 'DCM')"
    )
    ensure_schema(conn)
    stats = fold.build_candidates(conn, [("NSE", "DCM", "INE498A01018")], [])
    assert stats.candidates == []
    assert stats.unmatched_tickers == 0
    assert stats.resolution_statuses == []


def test_same_isin_collision_is_same_issuer_even_when_names_differ():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT, ticker TEXT)")
    conn.execute("INSERT INTO entities VALUES ('Different Name', 'company', 'AAA')")
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO entity_identifiers "
        "(entity_name, identifier_type, identifier_value, namespace, source_ref) "
        "VALUES ('Owner Co', 'isin', 'INE000A00001', 'NSE', 'exchange_sync')"
    )
    conn.commit()
    stats = fold.build_candidates(conn, [("NSE", "AAA", "INE000A00001")], [])
    assert stats.same_issuer_conflicts == 1
    assert stats.existing_conflicts == 0
    assert stats.resolution_statuses[0][1] == "same-issuer"


def test_build_candidates_classifies_same_issuer_collision():
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (name, entity_type, ticker) VALUES (?, 'company', ?)",
        ("Alpha Ltd", "OWN"),
    )
    conn.execute(
        "INSERT INTO entity_identifiers "
        "(entity_name, identifier_type, identifier_value, namespace, source_ref) "
        "VALUES ('Alpha Ltd', 'isin', 'INE000A00001', 'NSE', 'exchange_sync')"
    )
    listings = [("NSE", "AAA", "INE000A00001")]
    stats = fold.build_candidates(conn, listings, [])
    assert stats.same_issuer_conflicts == 1
    assert stats.existing_conflicts == 0
    assert stats.candidates == []
    assert ("Alpha", "same-issuer", "isin=INE000A00001 already owned by Alpha Ltd") in (
        stats.resolution_statuses
    )
    # The DDL vocabulary accepts the new triage status.
    conn.execute(fold.ENTITY_RESOLUTION_STATUS_DDL)
    conn.executemany(
        "INSERT OR REPLACE INTO entity_resolution_status "
        "(entity_name, status, detail, decided_at) VALUES (?, ?, ?, 'now')",
        stats.resolution_statuses,
    )
    conn.commit()
    assert (
        conn.execute(
            "SELECT status FROM entity_resolution_status WHERE entity_name='Alpha'"
        ).fetchone()[0]
        == "same-issuer"
    )


def test_persist_figi_instruments_keeps_metadata(tmp_path):
    db = tmp_path / "sources.duckdb"
    record = fold.FigiRecord(
        figi="BBG000B9XRY4",
        ticker="AAPL",
        name="APPLE INC",
        exch_code="US",
        market_sector="Equity",
        security_type="Common Stock",
        share_class_figi="BBG001S5N8V8",
        composite_figi="BBG000B9XRY4",
    )
    assert fold.persist_figi_instruments([record], path=db) == 1
    assert fold.persist_figi_instruments([record], path=db) == 1
    con = duckdb.connect(str(db))
    try:
        assert con.execute(
            "select ticker, name, market_sector, security_type, share_class_figi "
            "from figi_instruments where figi='BBG000B9XRY4'"
        ).fetchone() == ("AAPL", "APPLE INC", "Equity", "Common Stock", "BBG001S5N8V8")
    finally:
        con.close()
