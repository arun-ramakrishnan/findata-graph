"""P3.3 parquet export wrapper — helpers/graph/query.py:export_query.

The 2026-08-08 deferral asked for a notebook-grade ``COPY (<sql>) TO
parquet`` utility over the graph cache. The acceptance contract:

- round-trip parity: rows read back == rows the SQL returns;
- read-only discipline: exporting must NOT dirty the source cache
  (no WAL file, no size churn) — COPY TO writes the filesystem, not
  the database;
- relative paths resolve under ``outputs/`` (house scratch root).

Self-contained DuckDB fixtures — no production cache needed.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from helpers.graph import query


@pytest.fixture
def source_db(tmp_path: Path) -> Path:
    """A tiny standalone DuckDB file with a two-row table."""
    db = tmp_path / "cache.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS v(n, s)")
    con.close()
    return db


def test_round_trip_row_parity(tmp_path: Path, source_db: Path):
    con = duckdb.connect(str(source_db), read_only=True)
    try:
        out = query.export_query("SELECT * FROM t ORDER BY n", tmp_path / "out.parquet", con=con)
        sql_rows = con.execute("SELECT COUNT(*) FROM t").fetchone()[0]  # ty: ignore[not-subscriptable]  # COUNT(*) always yields exactly one row
    finally:
        con.close()
    assert out.exists()
    back = duckdb.connect()  # in-memory reader
    try:
        back_rows = back.execute(f"SELECT COUNT(*) FROM read_parquet('{out}')").fetchone()[0]  # ty: ignore[not-subscriptable]  # noqa: S608  # local tmp parquet path, test-only
        vals = back.execute(f"SELECT n FROM read_parquet('{out}') ORDER BY n").fetchall()  # noqa: S608  # local tmp parquet path, test-only
    finally:
        back.close()
    assert back_rows == sql_rows == 2
    assert [r[0] for r in vals] == [1, 2]


def test_read_only_discipline_no_wal(tmp_path: Path, source_db: Path):
    """Exporting from a read-only con must leave the cache file alone."""
    before = {p.name: p.stat().st_size for p in source_db.parent.iterdir()}
    con = duckdb.connect(str(source_db), read_only=True)
    try:
        query.export_query("SELECT * FROM t", tmp_path / "probe.parquet", con=con)
    finally:
        con.close()
    after = {p.name: p.stat().st_size for p in source_db.parent.iterdir()}
    # The export itself lands in the same dir; the assertion is that the
    # CACHE entries are byte-identical and no DuckDB WAL appeared.
    assert before.items() <= after.items(), f"cache changed: {before} -> {after}"
    assert not any(name.endswith((".wal", ".tmp")) for name in after)


def test_relative_path_resolves_under_outputs(tmp_path: Path, source_db: Path, monkeypatch):
    """A bare filename lands in outputs/ (the derived-artifact scratch root)."""
    fake_root = tmp_path / "repo"
    (fake_root / "outputs").mkdir(parents=True)
    monkeypatch.setattr(query, "PROJECT_ROOT", fake_root)
    con = duckdb.connect(str(source_db), read_only=True)
    try:
        out = query.export_query("SELECT * FROM t", "probe/notebook_export.parquet", con=con)
    finally:
        con.close()
    assert out == fake_root / "outputs" / "probe" / "notebook_export.parquet"
    assert out.exists()


def test_absolute_path_honoured(tmp_path: Path, source_db: Path):
    con = duckdb.connect(str(source_db), read_only=True)
    try:
        out = query.export_query("SELECT 42 AS n", tmp_path / "abs.parquet", con=con)
    finally:
        con.close()
    assert out == tmp_path / "abs.parquet"


def test_path_with_quote_is_escaped(tmp_path: Path, source_db: Path):
    """A single quote in the filename must not break the COPY literal."""
    con = duckdb.connect(str(source_db), read_only=True)
    try:
        out = query.export_query("SELECT * FROM t", tmp_path / "it's.parquet", con=con)
    finally:
        con.close()
    assert out.exists()
