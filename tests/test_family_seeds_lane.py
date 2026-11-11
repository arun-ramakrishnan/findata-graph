#!/usr/bin/env python3
"""S6 curated family lane (rpt_attribution_filer_resolution_and_group_family B4).

Coverage: seed loading + label namespacing, malformed-seed hard errors,
unknown-member validation (dropped, never minted), apply writes family
hyperedges + is idempotent, and S2 prune convergence (a wanted family label
survives; a removed seed is pruned on the next collect).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import EDGES_12COL, ENTITIES_8COL, HYPER_STORE  # noqa: E402

from helpers.graph import derive_hyperedges as dh  # noqa: E402


# --------------------------------------------------------------------------- #
# seed loading                                                                  #
# --------------------------------------------------------------------------- #


def test_shipped_seed_file_is_well_formed():
    doc = json.loads(dh.FAMILY_SEEDS_PATH.read_text())
    assert doc["version"] >= 1
    families = doc["families"]
    names = [f["family"] for f in families]
    assert len(names) >= 8 and len(names) == len(set(names))
    for f in families:
        assert f["members"], f["family"]
        assert all(isinstance(m, str) and m.strip() for m in f["members"])


def test_collect_namespaces_labels_under_family(tmp_path: Path):
    p = tmp_path / "seeds.json"
    p.write_text(json.dumps({"families": [{"family": "Tata", "members": ["Alpha Ltd"]}]}))
    assert dh.collect_family_seeds(p) == {"family:Tata": {"Alpha Ltd"}}


def test_missing_seed_file_is_an_empty_lane(tmp_path: Path):
    assert dh.collect_family_seeds(tmp_path / "absent.json") == {}


def test_malformed_seed_file_is_a_hard_error(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"families": {"Tata": ["Alpha Ltd"]}}))
    with pytest.raises(ValueError, match="must be a list"):
        dh.collect_family_seeds(p)

    p2 = tmp_path / "bad2.json"
    p2.write_text(json.dumps({"families": [{"family": "Tata", "members": []}]}))
    with pytest.raises(ValueError, match="bad entry"):
        dh.collect_family_seeds(p2)

    p3 = tmp_path / "dup.json"
    p3.write_text(
        json.dumps(
            {
                "families": [
                    {"family": "Tata", "members": ["Alpha Ltd"]},
                    {"family": "Tata", "members": ["Beta Ltd"]},
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="duplicate family"):
        dh.collect_family_seeds(p3)


def test_validate_drops_unknown_members_and_reports():
    groups = {"family:Tata": {"Alpha Ltd", "Ghost Ltd"}}
    filtered, dropped = dh.validate_family_seeds(groups, {"Alpha Ltd"})
    assert filtered == {"family:Tata": {"Alpha Ltd"}}
    assert dropped == {"family:Tata": ["Ghost Ltd"]}


def test_validate_drops_a_whole_family_when_nothing_resolves():
    filtered, dropped = dh.validate_family_seeds({"family:Ghost": {"Ghost Ltd"}}, set())
    assert filtered == {}
    assert dropped == {"family:Ghost": ["Ghost Ltd"]}


# --------------------------------------------------------------------------- #
# store-level: apply + prune convergence                                        #
# --------------------------------------------------------------------------- #


def _store(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "fam.db"))
    conn.executescript(ENTITIES_8COL)
    conn.executescript(EDGES_12COL)
    conn.executescript(HYPER_STORE)
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [("Alpha Ltd",), ("Beta Ltd",)],
    )
    conn.commit()
    return conn


def test_apply_writes_family_labels_and_is_idempotent(tmp_path: Path):
    conn = _store(tmp_path)
    hyper = {"group": {"family:Tata": {"Alpha Ltd", "Beta Ltd"}}}
    stats = dh.apply_hyperedges(hyper, conn=conn, dry_run=False)
    assert stats["group"] == (1, 2)
    rows = conn.execute(
        "SELECT label, source_ref FROM hyper_edges WHERE edge_type = 'group'"
    ).fetchall()
    assert rows == [("family:Tata", "derive:hyperedges:same_group")]
    members = [
        r[0]
        for r in conn.execute(
            "SELECT hi.entity_name FROM hyper_incidences hi"
            " JOIN hyper_edges he ON he.id = hi.edge_id"
            " WHERE he.edge_type = 'group' ORDER BY 1"
        )
    ]
    assert members == ["Alpha Ltd", "Beta Ltd"]
    # idempotent re-apply
    stats2 = dh.apply_hyperedges(hyper, conn=conn, dry_run=False)
    assert stats2["group"] == (0, 0)
    conn.close()


def test_prune_converges_the_family_lane(tmp_path: Path):
    conn = _store(tmp_path)
    hyper = {"group": {"family:Tata": {"Alpha Ltd"}, "family:Gone": {"Beta Ltd"}}}
    dh.apply_hyperedges(hyper, conn=conn, dry_run=False)
    # wanted keeps both
    kept = dh.prune_stale_group_edges(conn, {"family:Tata", "family:Gone"}, dry_run=True)
    assert kept["dropped"] == 0
    # a seed removed from the file (absent from wanted) is pruned
    gone = dh.prune_stale_group_edges(conn, {"family:Tata"}, dry_run=False)
    assert gone["dropped"] == 1
    labels = [r[0] for r in conn.execute("SELECT label FROM hyper_edges WHERE edge_type='group'")]
    assert labels == ["family:Tata"]
    conn.close()
