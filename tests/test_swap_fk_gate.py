"""Step 4 gate-wiring tests: swap_fk_gate.py's explicit-store mode
must report a clean copy (orphans=0) and list seeded orphans - while
always exiting 0 (advisory convention, like swap-prep-census)."""

import duckdb

from helpers.misc.swap_fk_gate import main as gate_main
from helpers.misc.swap_fk_integrity import load_edges


def _store(tmp_path, orphan):
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.execute("CREATE TABLE entities (name VARCHAR PRIMARY KEY)")
    con.execute("INSERT INTO entities VALUES ('A')")
    # No FK declared: edges come from the Step-2 manifest, so a child
    # row with a missing parent must be seedable (same trick as
    # tests/test_swap_fk_integrity.py).
    con.execute("CREATE TABLE quotes (id BIGINT PRIMARY KEY, entity VARCHAR)")
    rows = "(1,'A')"
    if orphan:
        rows += ",(2,'__ghost__')"
    con.execute(f"INSERT INTO quotes VALUES {rows}")
    con.close()
    return tmp_path / "t.duckdb"


def test_gate_clean_copy_orphans_zero(capsys, tmp_path):
    store = _store(tmp_path, orphan=False)
    rc = gate_main([str(store)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "orphans=0" in out
    assert str(store) in out


def test_gate_orphan_listed_but_exit_zero(capsys, tmp_path):
    store = _store(tmp_path, orphan=True)
    rc = gate_main([str(store)])
    out = capsys.readouterr().out
    assert rc == 0  # advisory: findings in the report, not the exit code
    assert "ORPHANS=1" in out
    assert "__ghost__" in out


def test_manifest_still_22_edges():
    # the gate leg's coverage is exactly the Step-2 manifest edges
    assert len(load_edges()) == 22
