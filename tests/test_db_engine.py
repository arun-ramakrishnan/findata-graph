"""Step 5 tests: engine-agnostic connection funnel (helpers/core/db_engine.py).

Builds parallel SQLite + DuckDB stores with identical schema/data, opens
both through ``connect_funnel``, and asserts equivalence of the shared
read surface: connection routing, row access (index + name), table_info,
column_names, schema_version, and read-only enforcement.
"""

from __future__ import annotations

import sqlite3

import duckdb
import pytest

from helpers.core.db_engine import (
    column_names,
    connect_funnel,
    is_duckdb_conn,
    schema_version,
    table_info,
)

ENTITIES_DDL = """
CREATE TABLE entities (
    name VARCHAR PRIMARY KEY NOT NULL,
    entity_type VARCHAR NOT NULL,
    ticker VARCHAR
);
"""

META_DDL = """
CREATE TABLE db_meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);
"""

SEED = [
    ("A Ltd", "company", "A.NS"),
    ("B Ltd", "company", "B.BO"),
    ("C Ltd", "company", "A.NS"),
    ("D Ltd", "company", None),
    ("E Ltd", "company", ""),
]


def _seed_sqlite(path):
    con = sqlite3.connect(str(path))
    con.execute(ENTITIES_DDL)
    con.execute(META_DDL)
    con.executemany("INSERT INTO entities (name, entity_type, ticker) VALUES (?,?,?)", SEED)
    con.execute("INSERT INTO db_meta VALUES ('schema_version','17')")
    con.commit()
    con.close()


def _seed_duckdb(path):
    con = duckdb.connect(str(path))
    con.execute(ENTITIES_DDL)
    con.execute(META_DDL)
    con.executemany("INSERT INTO entities (name, entity_type, ticker) VALUES (?,?,?)", SEED)
    con.execute("INSERT INTO db_meta VALUES ('schema_version','17')")
    con.close()


@pytest.fixture()
def sqlite_store(tmp_path):
    db = tmp_path / "research.db"
    _seed_sqlite(db)
    return db


@pytest.fixture()
def duckdb_store(tmp_path):
    db = tmp_path / "research.duckdb"
    _seed_duckdb(db)
    return db


def test_connect_funnel_routes_by_extension(sqlite_store, duckdb_store):
    con_sql = connect_funnel(sqlite_store)
    con_duck = connect_funnel(duckdb_store)
    assert not is_duckdb_conn(con_sql)
    assert is_duckdb_conn(con_duck)
    assert isinstance(con_duck, duckdb.DuckDBPyConnection) or hasattr(con_duck, "raw")
    con_sql.close()
    con_duck.close()


def test_rows_equal_by_index_and_name(sqlite_store, duckdb_store):
    q = "SELECT name, ticker FROM entities ORDER BY name"
    con_sql = connect_funnel(sqlite_store)
    con_duck = connect_funnel(duckdb_store)
    rows_sql = con_sql.execute(q).fetchall()
    rows_duck = con_duck.execute(q).fetchall()
    assert [tuple(r) for r in rows_sql] == [tuple(r) for r in rows_duck]
    # name access works on both engines (funnel proxy for duckdb)
    assert rows_sql[0]["name"] == rows_duck[0]["name"] == "A Ltd"
    assert rows_sql[2]["ticker"] == rows_duck[2]["ticker"] == "A.NS"
    con_sql.close()
    con_duck.close()


def test_row_factory_none_returns_raw_duckdb(duckdb_store):
    con = connect_funnel(duckdb_store, row_factory=None)
    assert isinstance(con, duckdb.DuckDBPyConnection)
    assert not isinstance(con, type("X", (), {}))
    con.close()


def test_table_info_matches_across_engines(sqlite_store, duckdb_store):
    con_sql = connect_funnel(sqlite_store)
    con_duck = connect_funnel(duckdb_store)
    info_sql = table_info(con_sql, "entities")
    info_duck = table_info(con_duck, "entities")
    assert [c["name"] for c in info_sql] == [c["name"] for c in info_duck]
    assert [c["type"] for c in info_sql] == [c["type"] for c in info_duck]
    assert [c["notnull"] for c in info_sql] == [c["notnull"] for c in info_duck]
    assert info_sql[0]["pk"] >= 1  # sqlite sees the PK
    assert [c["cid"] for c in info_sql] == [c["cid"] for c in info_duck]
    con_sql.close()
    con_duck.close()


def test_column_names_both_engines(sqlite_store, duckdb_store):
    assert column_names(connect_funnel(sqlite_store), "entities") == [
        "name",
        "entity_type",
        "ticker",
    ]
    assert column_names(connect_funnel(duckdb_store), "entities") == [
        "name",
        "entity_type",
        "ticker",
    ]


def test_schema_version_reads_db_meta_both_engines(sqlite_store, duckdb_store):
    assert schema_version(connect_funnel(sqlite_store)) == 17
    assert schema_version(connect_funnel(duckdb_store)) == 17


def test_schema_version_absent_meta_returns_none(tmp_path):
    import os

    con = sqlite3.connect(str(tmp_path / "bare.db"))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()
    con.close()
    assert schema_version(connect_funnel(tmp_path / "bare.db")) is None
    os.unlink(tmp_path / "bare.db")


def test_read_only_rejects_write_both_engines(sqlite_store, duckdb_store):
    for db in (sqlite_store, duckdb_store):
        con = connect_funnel(db, read_only=True)
        with pytest.raises(Exception):  # sqlite.OperationalError / duckdb.IOError
            con.execute("INSERT INTO entities VALUES ('X','company',NULL)")
        con.close()


def test_table_info_rejects_bad_identifier(sqlite_store):
    con = connect_funnel(sqlite_store)
    with pytest.raises(ValueError):
        table_info(con, "entities; DROP TABLE entities")
    con.close()
