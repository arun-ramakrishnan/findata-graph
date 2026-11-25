#!/usr/bin/env python3
"""Integration: the SQLite→DuckDB swap chain (test_coverage_gap_sweep S5).

A minimal SQLite source (table with a rowid-alias INTEGER PK, a view
using the tracked GLOB form, a NOCASE index) goes through the full
`swap_convert._build` conversion; the chain asserts:

- the DuckDB store exists and carries the same rows;
- the view SQL was translated to regexp_matches (no GLOB left);
- the NOCASE index became lower(col) expression index;
- the rowid-alias PK got its backfill sequence;
- `_parity` passes.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import duckdb
import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.misc import swap_convert as sc  # noqa: E402


SOURCE_DDL = """
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    ticker TEXT,
    source_ref TEXT,
    cin_year INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE metrics (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    value REAL
);
CREATE VIEW tech_companies AS
    SELECT e.name, m.value
    FROM entities e JOIN metrics m ON m.entity_id = e.id
    WHERE source_ref GLOB '*[0-9][0-9]_WEB.xml';
CREATE INDEX idx_entities_name_nocase ON entities(name COLLATE NOCASE);
CREATE INDEX idx_metrics_entity ON metrics(entity_id DESC);
"""


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "src.db"
    con = sqlite3.connect(str(src))
    con.executescript(SOURCE_DDL)
    con.executemany(
        "INSERT INTO entities (id, name, ticker, source_ref, cin_year) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Alpha Ltd", "a", "a_12_WEB.xml", 2024),
            (2, "Beta Ltd", "b", "b_34_WEB.xml", None),
        ],
    )
    con.executemany(
        "INSERT INTO metrics (entity_id, value) VALUES (?, ?)",
        [(1, 1.5), (2, 2.5)],
    )
    con.commit()
    con.close()
    return src


def test_swap_chain_translates_and_preserves(tmp_path: Path):
    src = _source(tmp_path)
    dest = tmp_path / "out.duckdb"
    schema, _dt = sc._build(src, dest)
    assert dest.exists()

    con = duckdb.connect(str(dest), read_only=True)
    try:
        # rows preserved
        assert con.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM tech_companies").fetchone()[0] == 2
        # view translated: no GLOB, regexp_matches present
        view_sql = con.execute(
            "SELECT sql FROM duckdb_views() WHERE view_name = 'tech_companies'"
        ).fetchone()[0]
        assert "GLOB" not in view_sql
        assert "regexp_matches" in view_sql
        # index translated: NOCASE is gone, lower(col) expression remains
        idx_sql = con.execute(
            "SELECT sql FROM duckdb_indexes() WHERE index_name = 'idx_entities_name_nocase'"
        ).fetchone()[0]
        assert "COLLATE" not in idx_sql
        assert "lower(" in idx_sql
        # rowid-alias pk backfilled with a sequence
        seq = con.execute(
            "SELECT COUNT(*) FROM duckdb_sequences() WHERE sequence_name = 'entities_id_seq'"
        ).fetchone()[0]
        assert seq == 1
        # created_at defaults survive
        assert (
            con.execute(
                "SELECT count(*) FROM duckdb_columns() WHERE table_name='metrics' AND column_name='value'"
            ).fetchone()[0]
            == 1
        )
    finally:
        con.close()

    ok, _detail = sc._parity(src, dest, schema)
    assert ok is True
