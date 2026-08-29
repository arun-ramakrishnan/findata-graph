"""
Tier 3: correctness of the load-bearing maintenance script snapshot_db.py.

Uses the script's own functions against a throwaway DB (not the live one) to
prove create -> verify -> restore round-trips data faithfully.
"""

from compression import zstd  # stdlib PEP 784
import sqlite3
from pathlib import Path

from maintenance.snapshot_db import create_snapshot, verify_snapshot


def _make_db(path: Path, n_entities: int = 5) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE entities (name TEXT PRIMARY KEY, x TEXT);"
        "CREATE TABLE relations (id INTEGER PRIMARY KEY, src TEXT, dst TEXT);"
    )
    conn.executemany(
        "INSERT INTO entities VALUES (?,?)", [(f"e{i}", "v") for i in range(n_entities)]
    )
    conn.executemany(
        "INSERT INTO relations (src,dst) VALUES (?,?)", [("e0", "e1"), ("e1", "e2")]
    )
    conn.commit()
    conn.close()


def _logger():
    import logging

    return logging.getLogger("test_snapshot")


def test_snapshot_creates_zstd_and_roundtrips(tmp_path):
    db = tmp_path / "src.db"
    snap = tmp_path / "out.db.zst"
    _make_db(db, n_entities=7)

    info = create_snapshot(db, snap, _logger())
    assert snap.exists()
    assert info["compressed_bytes"] < info["source_bytes"]  # actually compressed
    # zstd magic header (0x28 B5 2F FD)
    assert snap.read_bytes()[:4] == b"\x28\xb5\x2f\xfd"

    v = verify_snapshot(snap, db, _logger())
    assert v["match"] is True
    assert v["integrity"] == "ok"
    assert v["entities"] == 7
    assert v["relations"] == 2


def test_verify_detects_staleness(tmp_path):
    """A snapshot taken before extra rows are added must no longer match."""
    db = tmp_path / "src.db"
    snap = tmp_path / "out.db.zst"
    _make_db(db, n_entities=3)
    create_snapshot(db, snap, _logger())

    # Mutate the source after snapshotting.
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO entities VALUES (?,?)", [("extra1", "v"), ("extra2", "v")]
    )
    conn.commit()
    conn.close()

    v = verify_snapshot(snap, db, _logger())
    assert v["match"] is False
    assert v["entities"] == 3  # snapshot still has 3
    assert v["source_entities"] == 5  # live now has 5


def test_restore_via_zstd_matches_source(tmp_path):
    """Restore path documented in the script: zstd -dc -> db, then compare counts."""
    db = tmp_path / "src.db"
    snap = tmp_path / "out.db.zst"
    restored = tmp_path / "restored.db"
    _make_db(db, n_entities=4)
    create_snapshot(db, snap, _logger())

    with zstd.open(snap, "rb") as fin, open(restored, "wb") as fout:
        fout.write(fin.read())

    rconn = sqlite3.connect(restored)
    ents = rconn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    rels = rconn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    rconn.close()
    assert ents == 4 and rels == 2



# ---------------------------------------------------------------------------
# Parquet snapshot tests (Bundle L1)
# ---------------------------------------------------------------------------

import pyarrow.parquet as pq


def _make_db_with_data(path: Path) -> None:
    """Create a SQLite DB with multiple tables for Parquet export testing."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT, ticker TEXT);
        CREATE TABLE graph_edges (id INTEGER PRIMARY KEY, source TEXT, target TEXT, edge_type TEXT, weight REAL);
        CREATE TABLE events (id INTEGER PRIMARY KEY, entity TEXT, event_type TEXT, event_date DATE);
        CREATE TABLE db_meta (key TEXT, value TEXT);
        CREATE VIRTUAL TABLE note_search USING fts5(name, content);
        """
    )
    conn.executemany(
        "INSERT INTO entities VALUES (?,?,?)",
        [("Reliance", "company", "RELIANCE.NS"), ("TCS", "company", "TCS.NS"), ("Nifty50", "index", None)],
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, weight) VALUES (?,?,?,?)",
        [("Reliance", "TCS", "co_mentioned_in", 1.0)],
    )
    conn.execute("INSERT INTO events VALUES (1, 'Reliance', 'earnings', '2026-01-15')")
    conn.execute("INSERT INTO db_meta VALUES ('version', '8')")
    conn.execute("INSERT INTO note_search (name, content) VALUES ('Reliance', 'oil and gas giant')")
    conn.commit()
    conn.close()


def test_parquet_sqlite_export_roundtrips(tmp_path):
    """SQLite tables export to Parquet and row counts match on read-back."""
    from maintenance.snapshot_db import export_parquet_sqlite

    db = tmp_path / "src.db"
    out_dir = tmp_path / "parquet" / "sqlite"
    _make_db_with_data(db)

    result = export_parquet_sqlite(db, out_dir, _logger())
    assert not result.get("skipped")
    assert "entities" in result["tables"]
    assert "note_search" not in result["tables"]  # FTS5 excluded

    # Read back and verify counts
    entities_pq = pq.read_table(out_dir / "entities.parquet")
    assert entities_pq.num_rows == 3
    edges_pq = pq.read_table(out_dir / "graph_edges.parquet")
    assert edges_pq.num_rows == 1
    events_pq = pq.read_table(out_dir / "events.parquet")
    assert events_pq.num_rows == 1
    meta_pq = pq.read_table(out_dir / "db_meta.parquet")
    assert meta_pq.num_rows == 1


def test_parquet_sqlite_excludes_fts5_shadow_tables(tmp_path):
    """FTS5 derived shadow tables (_data/_idx/_docsize/_config) must not be
    exported; the content shadow (_content) is kept so restore can rebuild
    the index via ``('rebuild')``."""
    from maintenance.snapshot_db import _list_sqlite_tables

    db = tmp_path / "src.db"
    _make_db_with_data(db)

    con = sqlite3.connect(db)
    tables = _list_sqlite_tables(con)
    con.close()
    assert "note_search" not in tables            # virtual table itself
    assert "note_search_content" in tables         # content shadow — needed for rebuild
    assert "note_search_data" not in tables        # derived shadow
    assert "note_search_idx" not in tables         # derived shadow
    assert "note_search_docsize" not in tables     # derived shadow
    assert "note_search_config" not in tables      # derived shadow
    assert "entities" in tables


def test_parquet_verify_detects_mismatch(tmp_path):
    """Verify flags when source has more rows than the Parquet snapshot."""
    from maintenance.snapshot_db import export_parquet_sqlite, verify_parquet_snapshot

    db = tmp_path / "src.db"
    parquet_base = tmp_path / "parquet"
    _make_db_with_data(db)

    export_parquet_sqlite(db, parquet_base / "sqlite", _logger())

    # Add rows to source after snapshot
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO entities VALUES (?,?,?)",
        [("Extra1", "company", "X.NS"), ("Extra2", "company", "Y.NS")],
    )
    conn.commit()
    conn.close()

    result = verify_parquet_snapshot(parquet_base, None, db, _logger())
    assert result["match"] is False
    assert len(result["mismatches"]) > 0
    # Should mention entities mismatch
    assert any("entities" in m for m in result["mismatches"])


def test_parquet_verify_passes_on_match(tmp_path):
    """Verify passes when Parquet matches source."""
    from maintenance.snapshot_db import export_parquet_sqlite, verify_parquet_snapshot

    db = tmp_path / "src.db"
    parquet_base = tmp_path / "parquet"
    _make_db_with_data(db)

    export_parquet_sqlite(db, parquet_base / "sqlite", _logger())
    result = verify_parquet_snapshot(parquet_base, None, db, _logger())
    assert result["match"] is True
    assert result["tables_checked"] >= 4  # entities, edges, events, db_meta
    assert result["mismatches"] == []


def test_parquet_pandas_readability(tmp_path):
    """Exported Parquet must be readable by pandas (the key portability goal)."""
    from maintenance.snapshot_db import export_parquet_sqlite

    db = tmp_path / "src.db"
    out_dir = tmp_path / "parquet" / "sqlite"
    _make_db_with_data(db)

    export_parquet_sqlite(db, out_dir, _logger())

    import pandas as pd
    df = pd.read_parquet(out_dir / "entities.parquet")
    assert len(df) == 3
    assert "name" in df.columns
    assert "ticker" in df.columns
    assert df.iloc[0]["name"] == "Reliance"
    assert df.iloc[0]["ticker"] == "RELIANCE.NS"


def test_parquet_missing_db_returns_skipped(tmp_path):
    """Export should gracefully skip if the source DB does not exist."""
    from maintenance.snapshot_db import export_parquet_sqlite

    result = export_parquet_sqlite(
        tmp_path / "nonexistent.db", tmp_path / "out", _logger()
    )
    assert result.get("skipped") is True


def test_parquet_duckdb_export_order_deterministic(tmp_path):
    """maint_full_zero_churn F4: export bytes must be a pure function of
    content. A physical row reorder (what a graph rebuild does) must not
    churn the blob — previously COPY (SELECT * FROM t) leaked physical
    order into the parquet bytes (e_belongs/e_has, 2026-08-22 audit)."""
    import duckdb

    from maintenance.snapshot_db import export_parquet_duckdb

    db = tmp_path / "graph.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE e_belongs (company_name INT, sector_name INT, "
        "weight VARCHAR, properties VARCHAR, source_ref VARCHAR, "
        "valid_from VARCHAR, valid_to VARCHAR)"
    )
    # Insert deliberately out of key order.
    con.executemany(
        "INSERT INTO e_belongs VALUES (?, ?, '1.0', '{}', 'seed', NULL, NULL)",
        [(5, 100), (1, 100), (3, 100)],
    )
    con.commit()
    con.close()

    out1 = tmp_path / "snap1" / "duckdb"
    out2 = tmp_path / "snap2" / "duckdb"
    export_parquet_duckdb(db, out1, _logger())

    # Simulate a rebuild: same content, different physical order.
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE t AS SELECT * FROM e_belongs ORDER BY company_name DESC"
    )
    con.execute("DROP TABLE e_belongs")
    con.execute("ALTER TABLE t RENAME TO e_belongs")
    con.close()

    export_parquet_duckdb(db, out2, _logger())

    a = (out1 / "e_belongs.parquet").read_bytes()
    b = (out2 / "e_belongs.parquet").read_bytes()
    assert a == b
    # And the export is in canonical (column) order, not insertion order.
    rel = duckdb.connect().execute(
        f"SELECT company_name FROM read_parquet('{out1 / 'e_belongs.parquet'}')"  # noqa: S608  # tmp_path-constant identifier
    ).fetchall()
    assert [r[0] for r in rel] == [1, 3, 5]
