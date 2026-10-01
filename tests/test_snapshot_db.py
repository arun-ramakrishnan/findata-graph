"""Unit tests for helpers/maintenance/snapshot_db.py."""

from __future__ import annotations
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest


from helpers.core.env import REPO_ROOT  # noqa: E402
from helpers.maintenance import snapshot_db  # noqa: E402
from helpers.maintenance.snapshot_db import (  # noqa: E402
    _list_sqlite_tables,
    _list_duckdb_tables,
    create_snapshot,
    verify_snapshot,
    export_parquet_sqlite,
    export_parquet_duckdb,
    restore_sqlite_from_parquet,
    restore_duckdb_from_parquet,
    main as snapshot_main,
)


_log = logging.getLogger("test_snapshot")


# ---------------------------------------------------------------------------
# REPO_ROOT (folds the old snapshot_db._compute_root — helpers de-dup)
# ---------------------------------------------------------------------------
def test_repo_root_is_path():
    root = REPO_ROOT
    assert isinstance(root, Path)
    # Worktree-agnostic (2026-09-03): "the root of THIS checkout", not the
    # main checkout's directory name — stax worktrees are named after their
    # branch (the old `== "pdf-ocr-obsidian"` pin failed in every worktree).
    assert root == Path(__file__).resolve().parents[1]
    assert (root / ".git").exists()  # a git work tree (file in worktrees)


# ---------------------------------------------------------------------------
# _list_sqlite_tables
# ---------------------------------------------------------------------------
def test_list_sqlite_tables_basic():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (name TEXT)")
    conn.execute("CREATE TABLE graph_edges (source TEXT)")
    tables = _list_sqlite_tables(conn)
    assert "entities" in tables
    assert "graph_edges" in tables
    conn.close()


def test_list_sqlite_tables_excludes_sqlite_internal():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE foo (x INTEGER)")
    tables = _list_sqlite_tables(conn)
    assert "foo" in tables
    assert not any(t.startswith("sqlite_") for t in tables)
    conn.close()


def test_list_sqlite_tables_sorted():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE zebra (x)")
    conn.execute("CREATE TABLE apple (x)")
    conn.execute("CREATE TABLE mango (x)")
    tables = _list_sqlite_tables(conn)
    assert tables == sorted(tables)
    conn.close()


def test_list_sqlite_tables_excludes_fts():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (name TEXT)")
    conn.execute("CREATE VIRTUAL TABLE note_search USING FTS5(content)")
    tables = _list_sqlite_tables(conn)
    assert "entities" in tables
    assert "note_search" not in tables
    conn.close()


# ---------------------------------------------------------------------------
# _list_duckdb_tables
# ---------------------------------------------------------------------------
def test_list_duckdb_tables():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE a (x INTEGER)")
    con.execute("CREATE TABLE b (y TEXT)")
    tables = _list_duckdb_tables(con)
    assert "a" in tables
    assert "b" in tables
    assert tables == sorted(tables)
    con.close()


def test_list_duckdb_tables_empty():
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect(":memory:")
    assert _list_duckdb_tables(con) == []
    con.close()


# ---------------------------------------------------------------------------
# create_snapshot / verify_snapshot — round-trip with temp DB
# verify_snapshot requires entities + relations tables
# ---------------------------------------------------------------------------
def _make_test_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE relations (source TEXT, target TEXT, edge_type TEXT)")
    conn.execute("INSERT INTO entities VALUES ('Test Co')")
    conn.execute("INSERT INTO relations VALUES ('A', 'B', 'acquired')")
    conn.commit()
    conn.close()


def test_snapshot_roundtrip(tmp_path):
    src_db = tmp_path / "src.db"
    _make_test_db(src_db)

    snap_path = tmp_path / "snapshot.db.zst"
    info = create_snapshot(src_db, snap_path, _log)
    assert snap_path.exists()
    assert info["compressed_bytes"] > 0

    result = verify_snapshot(snap_path, src_db, _log)
    assert result["match"] is True


def test_snapshot_detects_modification(tmp_path):
    src_db = tmp_path / "src.db"
    _make_test_db(src_db)

    snap_path = tmp_path / "snapshot.db.zst"
    create_snapshot(src_db, snap_path, _log)

    # Modify the source AFTER snapshot
    conn = sqlite3.connect(str(src_db))
    conn.execute("INSERT INTO entities VALUES ('Another Co')")
    conn.commit()
    conn.close()

    result = verify_snapshot(snap_path, src_db, _log)
    assert result["match"] is False


def test_snapshot_no_source_db(tmp_path):
    src_db = tmp_path / "src.db"
    _make_test_db(src_db)

    snap_path = tmp_path / "snapshot.db.zst"
    create_snapshot(src_db, snap_path, _log)

    # Verify without source — just checks integrity
    result = verify_snapshot(snap_path, None, _log)
    assert result["integrity"] == "ok"


# ---------------------------------------------------------------------------
# Parquet restore: schema DDL + data + FTS5 rebuild round-trip
# ---------------------------------------------------------------------------
def _make_fts_db(path):
    """entities + regular FTS5 table (content shadow) + a NULL-heavy table."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO entities VALUES ('Alpha', 'revenue growth')")
    conn.execute("INSERT INTO entities VALUES ('Beta', NULL)")
    conn.execute(
        "CREATE VIRTUAL TABLE note_search USING FTS5(name, content, tokenize='porter unicode61')"
    )
    conn.execute("INSERT INTO note_search(name, content) VALUES ('Alpha', 'revenue growth')")
    conn.execute("INSERT INTO note_search(name, content) VALUES ('Beta', 'cost cuts')")
    conn.commit()
    conn.close()


def test_list_sqlite_tables_keeps_fts_content_shadow():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (name TEXT)")
    conn.execute("CREATE VIRTUAL TABLE note_search USING FTS5(content)")
    conn.execute("INSERT INTO note_search(content) VALUES ('hello')")
    tables = _list_sqlite_tables(conn)
    # content shadow IS data (needed to rebuild the index); derived shadows are not
    assert "note_search_content" in tables
    assert "note_search" not in tables
    assert "note_search_data" not in tables
    assert "note_search_idx" not in tables
    conn.close()


def test_restore_sqlite_roundtrip_with_fts(tmp_path):
    src_db = tmp_path / "src.db"
    _make_fts_db(src_db)

    pq_base = tmp_path / "snapshots" / "parquet"
    export_parquet_sqlite(src_db, pq_base / "sqlite", _log)
    assert (pq_base / "_schema.sqlite.sql").exists()
    # content shadow exported; derived shadows not
    files = {p.name for p in (pq_base / "sqlite").glob("*.parquet")}
    assert "note_search_content.parquet" in files
    assert not any("note_search_data" in f for f in files)

    target = tmp_path / "restored.db"
    info = restore_sqlite_from_parquet(pq_base / "sqlite", target, _log)
    assert target.exists()
    assert info["tables"]["entities"] == 2
    assert info["tables"]["note_search_content"] == 2

    conn = sqlite3.connect(str(target))
    # data + NULL fidelity
    rows = dict(conn.execute("SELECT name, note FROM entities"))
    assert rows == {"Alpha": "revenue growth", "Beta": None}
    # FTS index rebuilt from the content shadow
    n = conn.execute(
        "SELECT COUNT(*) FROM note_search WHERE note_search MATCH 'revenue'"
    ).fetchone()[0]
    assert n == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_restore_sqlite_missing_schema_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        restore_sqlite_from_parquet(tmp_path / "nope", tmp_path / "t.db", _log)


def test_restore_duckdb_roundtrip(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    src = tmp_path / "src.duckdb"
    con = duckdb.connect(str(src))
    con.execute("CREATE TABLE v_node (id INTEGER, name VARCHAR)")
    con.execute("CREATE TABLE e_belongs (node1 INTEGER, node2 INTEGER)")
    con.execute("INSERT INTO v_node VALUES (1, 'A'), (2, NULL)")
    con.execute("INSERT INTO e_belongs VALUES (1, 2)")
    con.close()

    pq_base = tmp_path / "snapshots" / "parquet"
    export_parquet_duckdb(src, pq_base / "duckdb", _log)
    assert (pq_base / "_schema.duckdb.sql").exists()

    target = tmp_path / "restored.duckdb"
    info = restore_duckdb_from_parquet(pq_base / "duckdb", target, _log)
    assert info["tables"]["v_node"] == 2
    assert info["tables"]["e_belongs"] == 1

    con = duckdb.connect(str(target), read_only=True)
    names = con.execute("SELECT name FROM v_node WHERE id=2").fetchone()
    assert names == (None,)
    assert con.execute("SELECT COUNT(*) FROM v_node").fetchone()[0] == 2
    con.close()


def test_main_restore_refuses_existing_target_without_force(tmp_path, monkeypatch):
    # an existing "live" DB + no --force -> refusal before anything runs
    live = tmp_path / "live.db"
    live.write_bytes(b"sentinel")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "snapshot_db.py",
            "--restore",
            "--db",
            str(live),
            "--parquet-dir",
            str(tmp_path / "pq"),
        ],
    )
    assert snapshot_main() == 1
    assert live.read_bytes() == b"sentinel"  # untouched


def test_export_skips_and_warns_on_stray_tables(tmp_path, caplog):
    """Manifest guard (2026-08-21): a DuckDB table outside
    MATERIALISED_TABLES is scratch state — it gets NO parquet file, NO
    schema-DDL line, and a loud WARNING naming it (a benchmark leftover
    leaked an orphan parquet into a snapshot commit through this exact
    hole; snapshot-check passed because a stray on both sides is
    'consistent'). The scratch table is named e_bench_scratch, NOT
    e_all_und — that one became a real manifest member the same day
    (sql_capability_unlocks B1 materialises it as the BFS substrate)."""
    duckdb = pytest.importorskip("duckdb")
    from helpers.graph.query import MATERIALISED_TABLES

    src = tmp_path / "cache.duckdb"
    con = duckdb.connect(str(src))
    con.execute("CREATE TABLE v_node (id BIGINT, name VARCHAR)")
    con.execute("CREATE TABLE e_bench_scratch (a VARCHAR, b VARCHAR)")
    con.execute("INSERT INTO e_bench_scratch VALUES ('X', 'Y')")
    con.close()

    out_dir = tmp_path / "pq" / "duckdb"
    with caplog.at_level(logging.WARNING, logger="test_snapshot"):
        res = export_parquet_duckdb(src, out_dir, _log)

    assert "v_node" in res["tables"]  # manifest table exported
    assert "e_bench_scratch" not in res["tables"]  # stray skipped
    assert (out_dir / "v_node.parquet").exists()
    assert not (out_dir / "e_bench_scratch.parquet").exists()
    schema = (tmp_path / "pq" / "_schema.duckdb.sql").read_text()
    assert "v_node" in schema
    assert "e_bench_scratch" not in schema  # no DDL leak either
    assert "e_bench_scratch" in caplog.text  # the warning names the stray
    # Guard must not be vacuous — the manifest covers the real tables,
    # including the walk substrates + note vectors added by
    # sql_capability_unlocks B1/A1.
    assert {
        "v_node",
        "v_embeddings",
        "v_note_embeddings",
        "e_all_und",
        "e_dir",
        "_build_meta",
    } <= MATERIALISED_TABLES


# ---------------------------------------------------------------------------
# Embed-store zstd backup (run_snapshot binary path): per-db legacy sibling
# keeps its paired artifact name; otherwise the shared store rides along.
# ---------------------------------------------------------------------------
def test_run_snapshot_backs_up_vec_sidecar(tmp_path):
    """<db>_vec.db rides along as <out>.db_vec.db.zst; absent sidecar skips."""
    from helpers.maintenance.snapshot_db import _cmd_create

    src_db = tmp_path / "src.db"
    _make_test_db(src_db)
    out = tmp_path / "snap" / "snapshot.db.zst"

    # No sidecar yet: run must succeed and log the skip.
    rc = _cmd_create(
        src_db,
        out,
        None,
        None,
        None,
        None,
        None,
        fmt="binary",
        with_duckdb=False,
        logger=_log,
    )
    assert rc == 0
    assert not (tmp_path / "snap" / "snapshot.db_vec.db.zst").exists()

    # Sidecar present: it rides along, WAL-merged by the online backup.
    vec = sqlite3.connect(str(tmp_path / "src.db_vec.db"))
    vec.execute("CREATE TABLE cache (k TEXT PRIMARY KEY, v TEXT)")
    vec.execute("INSERT INTO cache VALUES ('x', 'y')")
    vec.commit()
    vec.close()
    rc = _cmd_create(
        src_db,
        out,
        None,
        None,
        None,
        None,
        None,
        fmt="binary",
        with_duckdb=False,
        logger=_log,
    )
    assert rc == 0
    vec_zst = tmp_path / "snap" / "snapshot.db_vec.db.zst"
    assert vec_zst.exists() and vec_zst.stat().st_size > 0


def test_run_snapshot_backs_up_shared_embed_store(tmp_path):
    """Without a legacy sibling the consolidated EMBED_DB_PATH store is
    zstd-compressed as <stem>.snapshot.db.zst."""
    from helpers.core import vec_search as VS
    from helpers.maintenance.snapshot_db import _cmd_create

    src_db = tmp_path / "src.db"
    _make_test_db(src_db)
    out = tmp_path / "snap" / "snapshot.db.zst"

    store_dir = tmp_path / "memory"
    store_dir.mkdir(exist_ok=True)  # conftest autouse created it
    store = store_dir / "embed_store.db"
    sconn = sqlite3.connect(str(store))
    sconn.execute("CREATE TABLE embed_cache (k TEXT PRIMARY KEY)")
    sconn.execute("INSERT INTO embed_cache VALUES ('x')")
    sconn.commit()
    sconn.close()

    saved = VS.EMBED_DB_PATH
    VS.EMBED_DB_PATH = store
    try:
        rc = _cmd_create(
            src_db,
            out,
            None,
            None,
            None,
            None,
            None,
            fmt="binary",
            with_duckdb=False,
            logger=_log,
        )
    finally:
        VS.EMBED_DB_PATH = saved
    assert rc == 0
    store_zst = tmp_path / "snap" / "embed_store.snapshot.db.zst"
    assert store_zst.exists() and store_zst.stat().st_size > 0


def test_run_snapshot_reuses_embed_store_zst_when_unchanged(tmp_path):
    """D3 (maint_full_single_snapshot.md): an unchanged embed store skips the
    ~36 MB recompress — second run reuses the .zst byte-for-byte; a write
    to the store forces a fresh compress."""
    import os
    from helpers.core import vec_search as VS
    from helpers.maintenance.snapshot_db import _cmd_create

    src_db = tmp_path / "src.db"
    _make_test_db(src_db)
    out = tmp_path / "snap" / "snapshot.db.zst"

    store_dir = tmp_path / "memory"
    store_dir.mkdir(exist_ok=True)  # conftest autouse created it
    store = store_dir / "embed_store.db"
    sconn = sqlite3.connect(str(store))
    sconn.execute("CREATE TABLE embed_cache (k TEXT PRIMARY KEY)")
    sconn.execute("INSERT INTO embed_cache VALUES ('x')")
    sconn.commit()
    sconn.close()

    saved = VS.EMBED_DB_PATH
    VS.EMBED_DB_PATH = store
    try:
        rc = _cmd_create(
            src_db, out, None, None, None, None, None, fmt="binary", with_duckdb=False, logger=_log
        )
        assert rc == 0
        store_zst = tmp_path / "snap" / "embed_store.snapshot.db.zst"
        first_bytes = store_zst.read_bytes()
        first_mtime = store_zst.stat().st_mtime_ns

        # Unchanged store: backdate it below the .zst mtime (mtime comparisons
        # within one test can collide at coarse resolutions), re-run, and the
        # .zst must be reused untouched.
        os.utime(store, ns=((first_mtime - 1_000_000) // 1, 0))
        rc = _cmd_create(
            src_db, out, None, None, None, None, None, fmt="binary", with_duckdb=False, logger=_log
        )
        assert rc == 0
        assert store_zst.stat().st_mtime_ns == first_mtime
        assert store_zst.read_bytes() == first_bytes

        # A real write (new row ⇒ new size + fresh mtime) forces recompression.
        sconn = sqlite3.connect(str(store))
        sconn.execute("INSERT INTO embed_cache VALUES ('y')")
        sconn.commit()
        sconn.close()
        rc = _cmd_create(
            src_db, out, None, None, None, None, None, fmt="binary", with_duckdb=False, logger=_log
        )
        assert rc == 0
        assert store_zst.stat().st_mtime_ns != first_mtime
        assert store_zst.read_bytes() != first_bytes
    finally:
        VS.EMBED_DB_PATH = saved


# ---------------------------------------------------------------------------
# --quick generation freshness (snapshot_fresh_gate S1)
# ---------------------------------------------------------------------------
def _seed_quick_pair(tmp_path, gen="7"):
    """Live sqlite + live duckdb + snapshot parquets, all at one generation."""
    import duckdb

    live_db = tmp_path / "live.db"
    con = sqlite3.connect(live_db)
    con.execute("CREATE TABLE db_meta (key TEXT, value TEXT)")
    con.execute("INSERT INTO db_meta VALUES ('generation', ?)", (gen,))
    con.commit()
    con.close()
    live_dd = tmp_path / "live.duckdb"
    con = duckdb.connect(str(live_dd))
    con.execute("CREATE TABLE _build_meta (key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO _build_meta VALUES ('generation', ?)", [gen])
    con.close()
    snap_base = tmp_path / "snap"
    (snap_base / "sqlite").mkdir(parents=True)
    (snap_base / "duckdb").mkdir(parents=True)
    con = duckdb.connect()
    # NOTE: COPY TO cannot take a ? path parameter in this DuckDB build
    # (silently writes nowhere) — f-string with noqa, fixture-local tmp
    # path only (same pattern as test_analytics.py / test_snapshot.py).
    con.execute(  # noqa: S608  # fixture-local tmp path
        f"COPY (SELECT 'generation' AS key, '{gen}' AS value) TO '{snap_base / 'sqlite' / 'db_meta.parquet'}'"
    )
    con.execute(  # noqa: S608  # fixture-local tmp path
        f"COPY (SELECT 'generation' AS key, '{gen}' AS value) TO '{snap_base / 'duckdb' / '_build_meta.parquet'}'"
    )
    con.close()
    return live_db, live_dd, snap_base


def test_quick_green_on_fresh_tree(tmp_path, caplog):
    from helpers.maintenance.snapshot_db import _cmd_quick

    live_db, live_dd, snap_base = _seed_quick_pair(tmp_path)
    with caplog.at_level(logging.INFO, logger="snapshot_db"):
        assert _cmd_quick(snap_base, live_db, live_dd, logging.getLogger("snapshot_db")) == 0
    assert "sqlite generation: live=7 snapshot=7 -> OK" in caplog.text
    assert "duckdb generation: live=7 snapshot=7 -> OK" in caplog.text


def test_quick_fails_with_remediation_on_drift(tmp_path, caplog):
    from helpers.maintenance.snapshot_db import _cmd_quick

    live_db, live_dd, snap_base = _seed_quick_pair(tmp_path)
    con = sqlite3.connect(live_db)
    con.execute("UPDATE db_meta SET value='8' WHERE key='generation'")
    con.commit()
    con.close()
    with caplog.at_level(logging.INFO, logger="snapshot_db"):
        assert _cmd_quick(snap_base, live_db, live_dd, logging.getLogger("snapshot_db")) == 1
    assert "sqlite generation: live=8 snapshot=7 -> MISMATCH" in caplog.text
    assert "run make snapshot" in caplog.text


def test_quick_fail_closed_on_missing_snapshot(tmp_path, caplog):
    from helpers.maintenance.snapshot_db import _cmd_quick

    live_db, live_dd, snap_base = _seed_quick_pair(tmp_path)
    (snap_base / "sqlite" / "db_meta.parquet").unlink()
    with caplog.at_level(logging.INFO, logger="snapshot_db"):
        assert _cmd_quick(snap_base, live_db, live_dd, logging.getLogger("snapshot_db")) == 1
    assert "snapshot=None" in caplog.text


# -- SNAP-1: restore interpolates snapshot filenames into SQL — the gate ---- //
# (security_evaluation Addendum 7 §H; fix proposal
# doc/improvements/archive/security/snapshot_restore_sql_injection.md)


def _write_parquet(path: Path, rows: dict) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table(rows), path)


def test_restore_duckdb_rejects_sql_in_parquet_stem(tmp_path):
    """The Addendum 7 §H PoC: a stem carrying SQL must be refused, and no
    side-effect artifact (ATTACHed DB) may materialize."""
    pytest.importorskip("duckdb")

    pq_dir = tmp_path / "parquet"
    pq_dir.mkdir()
    ext = tmp_path / "ext.duckdb"
    # no "/" in the stem (it must be one filename); read_parquet/ATTACH
    # resolve relative to CWD, so the restore below runs with cwd=tmp_path
    malicious = (
        "t1 SELECT * FROM read_parquet('x.parquet'); "
        "ATTACH 'ext.duckdb' AS ext; "
        "CREATE TABLE ext.pwned(a INTEGER); "
        "INSERT INTO ext.pwned VALUES (42); --"
    )
    assert len(malicious) + len(".parquet") < 255  # filename length sanity
    (pq_dir / f"{malicious}.parquet").write_bytes(b"")
    (tmp_path / "_schema.duckdb.sql").write_text("CREATE TABLE t1(a INTEGER);\n")
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        _write_parquet(tmp_path / "x.parquet", {"a": [1]})
        with pytest.raises(ValueError, match="UNEXPECTED SNAPSHOT FILE"):
            restore_duckdb_from_parquet(pq_dir, tmp_path / "t.duckdb", _log)
    finally:
        os.chdir(cwd)

    # pre-fix behaviour: attacker SQL ran and ext.duckdb materialized
    assert not ext.exists()


def test_restore_sqlite_rejects_unknown_stem_and_columns(tmp_path):
    pq_dir = tmp_path / "parquet"
    pq_dir.mkdir()
    (tmp_path / "_schema.sqlite.sql").write_text("CREATE TABLE entities(name TEXT, note TEXT);\n")
    # stem is not a schema table
    _write_parquet(pq_dir / "entities_evil.parquet", {"name": ["x"]})
    with pytest.raises(ValueError, match="UNEXPECTED SNAPSHOT FILE"):
        restore_sqlite_from_parquet(pq_dir, tmp_path / "t.db", _log)

    # stem is a table but a column is foreign (bracket-escape class)
    _write_parquet(pq_dir / "entities.parquet", {"name": ["x"], "Evil": ["y"]})
    with pytest.raises(ValueError, match="UNEXPECTED SNAPSHOT COLUMN"):
        restore_sqlite_from_parquet(pq_dir, tmp_path / "t.db", _log)


def test_restore_duckdb_benign_restore_still_passes_gate(tmp_path):
    """Control: the gate must not refuse a legitimate snapshot (regression
    guard for the allowlist itself)."""
    duckdb = pytest.importorskip("duckdb")

    pq_dir = tmp_path / "parquet"
    pq_dir.mkdir()
    (tmp_path / "_schema.duckdb.sql").write_text("CREATE TABLE v_node(id INTEGER, name VARCHAR);\n")
    _write_parquet(pq_dir / "v_node.parquet", {"id": [1, 2], "name": ["A", None]})
    info = restore_duckdb_from_parquet(pq_dir, tmp_path / "t.duckdb", _log)
    assert info["tables"]["v_node"] == 2
    con = duckdb.connect(str(tmp_path / "t.duckdb"), read_only=True)
    assert con.execute("SELECT COUNT(*) FROM v_node").fetchone()[0] == 2
    con.close()


# -- verify-path gates (verify_injection_and_boundary_remediation S2): the
# VERIFY half had the same filename->SQL class SNAP-1 fixed on restore, plus
# a fail-open skip route. Both routes pinned here; both gates
# mutation-verified (strip _verify_gate_stem -> RED).


def _scratch_truth_db(path: Path, rows: int = 5) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE entities(name TEXT)")
    con.executemany("INSERT INTO entities VALUES (?)", [(f"r{i}",) for i in range(rows)])
    con.commit()
    con.close()


def test_verify_sqlite_injection_stem_raises(tmp_path):
    """The ]-escape that executed SQL pre-fix must now raise (regex gate)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq_dir = tmp_path / "pq" / "sqlite"
    pq_dir.mkdir(parents=True)
    sdb = tmp_path / "s.db"
    _scratch_truth_db(sdb)
    stem = "entities] UNION SELECT COUNT(*) FROM entities LIMIT 1 --"
    pq.write_table(pa.table({"name": [f"x{i}" for i in range(5)]}), pq_dir / f"{stem}.parquet")
    pq.write_table(pa.table({"name": [f"r{i}" for i in range(5)]}), pq_dir / "entities.parquet")
    with pytest.raises(ValueError, match="UNEXPECTED SNAPSHOT FILE"):
        snapshot_db._verify_parquet_sqlite_side(pq_dir.parent, sdb, _log)


def test_verify_sqlite_missing_live_table_is_a_mismatch_not_a_skip(tmp_path):
    """A regex-legal stem absent from the live schema must surface as a
    mismatch (drift), never as a silent tables_checked shrink."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq_dir = tmp_path / "pq" / "sqlite"
    pq_dir.mkdir(parents=True)
    sdb = tmp_path / "s.db"
    _scratch_truth_db(sdb)
    pq.write_table(pa.table({"name": ["x"]}), pq_dir / "ghost_table.parquet")
    res = snapshot_db._verify_parquet_sqlite_side(pq_dir.parent, sdb, _log)
    assert res["tables_checked"] == 0
    assert res["mismatches"] == ["sqlite/ghost_table: MISSING ON LIVE"]


def test_verify_sqlite_erroring_stem_fails_closed(tmp_path, monkeypatch):
    """Pre-fix, an erroring live query was swallowed -> verify passed. Now a
    live-side error is a mismatch entry (gate fails loudly)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq_dir = tmp_path / "pq" / "sqlite"
    pq_dir.mkdir(parents=True)
    sdb = tmp_path / "s.db"
    _scratch_truth_db(sdb)
    # deterministic live-side failure: wrap the connection so the COUNT
    # errors while the gate's schema read passes (SQLite's 1/0 is NULL, not
    # an error, so a view trick does not work here)
    real = sqlite3.connect(sdb)

    class _CountFails:
        def execute(self, sql, *a):
            if sql.strip().upper().startswith("SELECT COUNT(*)"):
                raise sqlite3.OperationalError("mocked live failure")
            return real.execute(sql, *a)

        def close(self):
            real.close()

    monkeypatch.setattr(snapshot_db, "connect", lambda *a, **k: _CountFails())
    pq.write_table(pa.table({"name": ["x"]}), pq_dir / "entities.parquet")
    res = snapshot_db._verify_parquet_sqlite_side(pq_dir.parent, sdb, _log)
    assert any("ERROR" in m for m in res["mismatches"]), res
    assert res["tables_checked"] == 0


def test_verify_duckdb_injection_stem_raises(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq_dir = tmp_path / "pq" / "duckdb"
    pq_dir.mkdir(parents=True)
    gdb = tmp_path / "g.duckdb"
    con = duckdb.connect(str(gdb))
    con.execute("CREATE TABLE entities(name VARCHAR)")
    con.close()
    stem = "entities WHERE 1=0 UNION SELECT 999 --"
    pq.write_table(pa.table({"name": [f"x{i}" for i in range(5)]}), pq_dir / f"{stem}.parquet")
    with pytest.raises(ValueError, match="UNEXPECTED SNAPSHOT FILE"):
        snapshot_db._verify_parquet_duckdb_side(pq_dir.parent, gdb, _log)


def test_verify_ephemeral_absent_still_skips_quietly(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    import pyarrow as pa
    import pyarrow.parquet as pq
    from helpers.graph.query import EPHEMERAL_TABLES

    ephemeral = sorted(EPHEMERAL_TABLES)[0]
    pq_dir = tmp_path / "pq" / "duckdb"
    pq_dir.mkdir(parents=True)
    gdb = tmp_path / "g.duckdb"
    con = duckdb.connect(str(gdb))
    con.execute("CREATE TABLE entities(name VARCHAR)")
    con.execute("INSERT INTO entities VALUES ('r0')")
    con.close()
    pq.write_table(pa.table({"name": ["x"]}), pq_dir / "entities.parquet")
    pq.write_table(pa.table({"a": [1]}), pq_dir / f"{ephemeral}.parquet")
    res = snapshot_db._verify_parquet_duckdb_side(pq_dir.parent, gdb, _log)
    assert res["mismatches"] == [], res
    assert res["tables_checked"] == 1  # ephemeral skipped, entities checked
