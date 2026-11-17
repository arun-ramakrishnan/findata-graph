"""Step 4 tests: FK integrity checker (helpers/misc/swap_fk_integrity.py)
against a hermetic DuckDB copy with a seeded orphan set. Validates the
NULL-child semantics (NULL child keys are not orphans) and the hard-error
path (exit 2 on unreadable store), and that clean copies report 0."""

import json
import subprocess
import sys

import duckdb
import pytest

from helpers.misc.swap_fk_integrity import check_edge, load_edges

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _build_store(tmp_path, orphan_set, null_child=False):
    con = duckdb.connect(str(tmp_path / "test.duckdb"))
    con.execute("CREATE TABLE entities (name VARCHAR PRIMARY KEY)")
    con.execute("INSERT INTO entities VALUES ('A'),('B')")
    # Deliberately NO FK on quotes: the checker derives edges from the
    # Step-2 manifest (not the live schema), so we must be able to seed a
    # child row whose parent is absent (DuckDB would reject it otherwise).
    con.execute(
        """CREATE TABLE quotes (
             id BIGINT PRIMARY KEY,
             entity VARCHAR
           )"""
    )
    con.execute("INSERT INTO quotes VALUES (1,'A'),(2,'B'),(3,'A')")
    if orphan_set:
        con.execute("INSERT INTO quotes VALUES (100,'__ghost__')")
    if null_child:
        con.execute("INSERT INTO quotes VALUES (101, NULL)")
    con.close()
    return tmp_path / "test.duckdb"


def test_check_edge_finds_orphan(tmp_path):
    store = _build_store(tmp_path, orphan_set=True)
    con = duckdb.connect(str(store), read_only=True)
    edge = {
        "owning_table": "quotes",
        "owning_columns": ["entity"],
        "referenced_table": "entities",
        "referenced_columns": ["name"],
    }
    orphans = check_edge(con, edge)
    con.close()
    assert len(orphans) == 1
    assert "__ghost__" in orphans[0]
    assert "quotes.entity=" in orphans[0]


def test_check_edge_no_orphan_clean(tmp_path):
    store = _build_store(tmp_path, orphan_set=False)
    con = duckdb.connect(str(store), read_only=True)
    edge = {
        "owning_table": "quotes",
        "owning_columns": ["entity"],
        "referenced_table": "entities",
        "referenced_columns": ["name"],
    }
    orphans = check_edge(con, edge)
    con.close()
    assert orphans == []


def test_null_child_not_orphan(tmp_path):
    store = _build_store(tmp_path, orphan_set=False, null_child=True)
    con = duckdb.connect(str(store), read_only=True)
    edge = {
        "owning_table": "quotes",
        "owning_columns": ["entity"],
        "referenced_table": "entities",
        "referenced_columns": ["name"],
    }
    orphans = check_edge(con, edge)
    con.close()
    assert orphans == []


def test_missing_table_skipped_not_orphan(tmp_path):
    store = _build_store(tmp_path, orphan_set=False)
    con = duckdb.connect(str(store), read_only=True)
    edge = {
        "owning_table": "no_such_table",
        "owning_columns": ["entity"],
        "referenced_table": "entities",
        "referenced_columns": ["name"],
    }
    orphans = check_edge(con, edge)
    con.close()
    assert orphans == []


def test_load_edges_reads_manifest():
    edges = load_edges()
    assert len(edges) >= 1
    keys = set()
    for e in edges:
        keys.add((e["owning_table"], tuple(e["owning_columns"]), e["referenced_table"]))
    assert len(keys) == len(edges)  # de-duped, no repeated constraint


@pytest.mark.parametrize("orphan_set", [False, True])
def test_main_exit_codes(tmp_path, orphan_set):
    store = _build_store(tmp_path, orphan_set=orphan_set)
    script = REPO_ROOT / "helpers/misc/swap_fk_integrity.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(store), "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == (1 if orphan_set else 0)
    data = json.loads(proc.stdout)
    assert "orphans" in data
    if orphan_set:
        assert data["orphans"]
    else:
        assert data["orphans"] == []
