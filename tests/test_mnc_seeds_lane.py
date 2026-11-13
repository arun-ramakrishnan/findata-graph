"""Tests for the MNC seed lane loader (derive_hyperedges.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.graph.derive_hyperedges import (  # noqa: E402
    MNC_LABEL_PREFIX,
    collect_mnc_seeds,
    compact_group_membership,
    validate_family_seeds,
)


def _seed_file(tmp_path, families):
    p = tmp_path / "mnc_seeds.json"
    p.write_text(json.dumps({"version": 1, "families": families}))
    return p


def test_collect_mnc_uses_mnc_prefix(tmp_path):
    p = _seed_file(tmp_path, [{"family": "Siemens AG", "members": ["A", "B"]}])
    groups = collect_mnc_seeds(p)
    assert groups == {f"{MNC_LABEL_PREFIX}Siemens AG": {"A", "B"}}


def test_collect_mnc_missing_file_is_empty(tmp_path):
    assert collect_mnc_seeds(tmp_path / "nope.json") == {}


def test_collect_mnc_malformed_raises(tmp_path):
    p = tmp_path / "mnc_seeds.json"
    p.write_text(json.dumps({"version": 1, "families": {"not": "list"}}))
    try:
        collect_mnc_seeds(p)
    except ValueError as e:
        assert "'families' must be a list" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_collect_mnc_duplicate_raises(tmp_path):
    fams = [
        {"family": "ABB Ltd", "members": ["A"]},
        {"family": "ABB Ltd", "members": ["B"]},
    ]
    p = _seed_file(tmp_path, fams)
    try:
        collect_mnc_seeds(p)
    except ValueError as e:
        assert "duplicate family" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_validate_mnc_same_drop_rule(tmp_path):
    p = _seed_file(
        tmp_path,
        [{"family": "ABB Ltd", "members": ["Good", "Ghost"]}],
    )
    groups = collect_mnc_seeds(p)
    filtered, dropped = validate_family_seeds(groups, {"Good"})
    label = f"{MNC_LABEL_PREFIX}ABB Ltd"
    assert filtered[label] == {"Good"}
    assert dropped[label] == ["Ghost"]


def _compact_con():
    con = duckdb.connect(":memory:", **memory_cap_config())
    con.execute("CREATE TABLE hyper_edges (id INTEGER, edge_type VARCHAR, label VARCHAR)")
    con.execute("CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name VARCHAR)")
    con.execute("INSERT INTO hyper_edges VALUES (1, 'group', 'mnc:Siemens AG')")
    con.execute("INSERT INTO hyper_incidences VALUES (1, 'A'), (1, 'B'), (1, 'C')")
    con.execute("INSERT INTO hyper_edges VALUES (2, 'group', 'vigil:rpt:TCS')")
    con.execute("INSERT INTO hyper_incidences VALUES (2, 'X'), (2, 'Y'), (2, 'Z')")
    return con


def test_compact_skips_mnc_labels():
    con = _compact_con()
    groups = {f"{MNC_LABEL_PREFIX}Siemens AG": {"A", "B"}, "vigil:rpt:TCS": {"X"}}
    n = compact_group_membership(con, groups, dry_run=True)
    assert n == 2  # only the vigil label has stale members Y,Z
    con.close()


def test_compact_dry_run_does_not_write():
    con = _compact_con()
    groups = {f"{MNC_LABEL_PREFIX}Siemens AG": {"A", "B"}}
    compact_group_membership(con, groups, dry_run=True)
    n = con.execute("SELECT COUNT(*) FROM hyper_incidences WHERE edge_id=1").fetchone()[0]
    assert n == 3
    con.close()
