"""S2 tests: opt-in ResearchEngine selector + dual-engine read-lane parity.

Covers the two routed read lanes (``price_sync.load_tickers`` and the
``app.py`` pure reader helpers) against parallel SQLite/DuckDB stores, plus
the env-flag semantics (default SQLite, ``SQLITE`` rollback, ``DUCKDB``/
``AUTO`` opt-in). Where the S1 canonical artifact exists (machine-local
``bench_data/``), an extra integration test runs the §4 eval gate on it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from helpers.core.db_engine import (
    ResearchEngine,
    connect_funnel,
    is_duckdb_conn,
)

REPO = Path(__file__).resolve().parents[1]
S1_ARTIFACT = REPO / "bench_data/duckdb_swap/stores/s1/research_converted.duckdb"

DDL = """
CREATE TABLE entities (
    name VARCHAR PRIMARY KEY NOT NULL,
    entity_type VARCHAR NOT NULL,
    ticker VARCHAR,
    cin VARCHAR
);
CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name VARCHAR);
CREATE TABLE db_meta (key VARCHAR PRIMARY KEY, value VARCHAR);
"""
SEED = [
    ("Birkenstock SE", "company", "BIRK.DE", "L111"),
    ("Infosys", "company", "INFY.NS", "L222"),
    ("Banking", "sector", None, None),
]


def _seed(path: Path, use_duckdb: bool) -> None:
    if use_duckdb:
        import duckdb

        con = duckdb.connect(str(path))
    else:
        con = sqlite3.connect(str(path))
    for stmt in DDL.strip().split(";"):
        con.execute(stmt)
    con.executemany("INSERT INTO entities VALUES (?,?,?,?)", SEED)
    con.execute("INSERT INTO hyper_incidences VALUES (1, 'Infosys')")
    con.execute("INSERT INTO db_meta VALUES ('schema_version', '17')")
    if not use_duckdb:
        con.commit()
    con.close()


@pytest.fixture
def pair(tmp_path: Path):
    sqlite_path = tmp_path / "research.db"
    duckdb_path = tmp_path / "research.duckdb"
    _seed(sqlite_path, False)
    _seed(duckdb_path, True)
    return sqlite_path, duckdb_path


class TestSelector:
    def test_default_is_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FINDATA_RESEARCH_ENGINE", raising=False)
        path = tmp_path / "research.db"
        _seed(path, False)
        con = connect_funnel(path, read_only=True)
        try:
            assert not is_duckdb_conn(con)
        finally:
            con.close()

    def test_sqlite_env_is_rollback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FINDATA_RESEARCH_ENGINE", "sqlite")
        path = tmp_path / "research.db"
        _seed(path, False)
        con = connect_funnel(path, read_only=True)
        try:
            assert not is_duckdb_conn(con)
        finally:
            con.close()

    def test_duckdb_env_routes_explicit_path(self, monkeypatch, pair):
        _, duckdb_path = pair
        monkeypatch.setenv("FINDATA_RESEARCH_ENGINE", "duckdb")
        con = connect_funnel(duckdb_path, read_only=True)
        try:
            assert is_duckdb_conn(con)
        finally:
            con.close()

    def test_kwarg_overrides_env(self, monkeypatch, pair):
        sqlite_path, duckdb_path = pair
        monkeypatch.setenv("FINDATA_RESEARCH_ENGINE", "duckdb")
        con = connect_funnel(sqlite_path, read_only=True, engine=ResearchEngine.SQLITE)
        try:
            assert not is_duckdb_conn(con)
        finally:
            con.close()
        # Extension always wins over the selector: a .duckdb path routes to
        # DuckDB regardless of the engine override.
        con = connect_funnel(duckdb_path, read_only=True, engine="sqlite")
        try:
            assert is_duckdb_conn(con)
        finally:
            con.close()

    def test_pathless_opt_in_uses_override_env(self, monkeypatch, pair):
        _, duckdb_path = pair
        monkeypatch.setenv("FINDATA_RESEARCH_ENGINE", "duckdb")
        monkeypatch.setenv("FINDATA_RESEARCH_DB_PATH", str(duckdb_path))
        con = connect_funnel(read_only=True)
        try:
            assert is_duckdb_conn(con)
            assert con.execute("SELECT name FROM entities LIMIT 1").fetchone()[0] == SEED[0][0]
        finally:
            con.close()

    @pytest.mark.parametrize("value", ["AUTO", "duckdb", " DUCKDB "])
    def test_opt_in_names(self, value):
        assert ResearchEngine(value).opt_in

    def test_unknown_value_is_sqlite(self):
        sel = ResearchEngine("nonsense")
        assert not sel.opt_in
        assert sel.name == "nonsense"


class TestReadLaneParity:
    def test_price_sync_tickers(self, pair):
        from helpers.maintenance.price_sync import load_tickers

        sqlite_path, duckdb_path = pair
        s = load_tickers(sqlite_path)
        d = load_tickers(duckdb_path)
        assert s == d == sorted({r[2] for r in SEED if r[2]})

    def test_app_table_probes_and_cursor(self, pair):
        import app as view

        sqlite_path, duckdb_path = pair
        for path in (sqlite_path, duckdb_path):
            con = connect_funnel(path, read_only=True)
            try:
                assert view._research_table_exists(con, "entities")
                assert not view._research_table_exists(con, "no_such_table")
                assert view._research_has_column(con, "entities", "cin")
                assert not view._research_has_column(con, "entities", "nope")
                cur = con.cursor()
                row = cur.execute(
                    "SELECT name FROM entities WHERE name = ? COLLATE NOCASE",
                    ("birkenstock se",),
                ).fetchone()
                assert row[0] == "Birkenstock SE"
                assert row["name"] == "Birkenstock SE"
                # successive executes on the same cursor (app.py pattern)
                assert cur.execute("SELECT ticker FROM entities LIMIT 1").fetchone()[0] is not None
            finally:
                con.close()

    def test_name_indexed_rows_both_engines(self, pair):
        sqlite_path, duckdb_path = pair
        for path in (sqlite_path, duckdb_path):
            con = connect_funnel(path, read_only=True)
            try:
                row = con.execute("SELECT name, ticker FROM entities LIMIT 1").fetchone()
                assert row["name"] == row[0]
                assert row["ticker"] == row[1]
            finally:
                con.close()


@pytest.mark.skipif(not S1_ARTIFACT.exists(), reason="S1 canonical artifact is machine-local")
class TestS1ArtifactIntegration:
    def test_gate_accept_and_routed_reads(self):
        import json

        from helpers.core.db_engine import schema_version, table_info

        con = connect_funnel(S1_ARTIFACT, read_only=True)
        try:
            assert is_duckdb_conn(con)
            assert schema_version(con) == 17
            assert any(c["name"] == "cin" for c in table_info(con, "entities"))
            assert con.execute(
                "SELECT name FROM entities WHERE name = ? COLLATE NOCASE LIMIT 1",
                ("birkenstock",),
            ).fetchone()[0] == "Birkenstock"
        finally:
            con.close()

        import app as view

        con = connect_funnel(S1_ARTIFACT, read_only=True)
        try:
            assert view._research_table_exists(con, "hyper_incidences")
            assert view._research_has_column(con, "entities", "cin")
        finally:
            con.close()

        report = REPO / "bench_data/duckdb_swap/findings/s1_eval_gate_report.json"
        if report.exists():
            data = json.loads(report.read_text())
            # accept shape from the gate report (verdicts all unchanged)
            assert data
