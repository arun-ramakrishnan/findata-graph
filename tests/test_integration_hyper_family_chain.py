#!/usr/bin/env python3
"""Integration: the hyperedge family chain (test_coverage_gap_sweep S3).

source dyads → collect_hyperedges → apply_hyperedges (dry-run, then
real, then idempotency) → HIF parquet serialization → roundtrip
validation → (hgx-gated) hypergraph construction. Proves the star
family registry, the store DDL and the HIF writer agree on ONE fixture
store — the seam failures the registry's invariants alone cannot see.

hgx leg (Hypergraph construction from the applied store) runs for real
— hypergraphx 1.8.0 is in the venv — but stays importor-skipped so the
chain still proves itself on an hgx-less box; test_hyper_wiring owns
the wiring-level coverage for those lanes.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import EDGES_12COL, ENTITIES_8COL, HYPER_STORE  # noqa: E402

from helpers.graph import derive_hyperedges as dh  # noqa: E402
from helpers.graph import hyper_hif as hh  # noqa: E402

# --------------------------------------------------------------------------- #
# Fixture store: hermetic by construction — entities.file_path stays NULL       #
# (the industry leg skips when nothing is resolvable), no quotes/events         #
# tables (edition/event families stay empty), so only the dyad regroup and      #
# the symmetric regroup fire.                                                   #
# --------------------------------------------------------------------------- #
MEMBERS = ("Alpha Ltd", "Beta Ltd", "Gamma Ltd", "Delta Ltd")


def _seed(conn: sqlite3.Connection) -> None:
    conn.executescript(ENTITIES_8COL)
    conn.executescript(EDGES_12COL)
    conn.executescript(HYPER_STORE)
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [(m,) for m in MEMBERS],
    )
    dyads = [
        # theme star: exposed_to
        ("Alpha Ltd", "IT_Services", "exposed_to", "{}"),
        ("Beta Ltd", "IT_Services", "exposed_to", "{}"),
        ("Gamma Ltd", "Cement", "exposed_to", "{}"),
        # country star: listed_in
        ("Alpha Ltd", "India", "listed_in", "{}"),
        ("Delta Ltd", "India", "listed_in", "{}"),
        # index star: listed_on_index (the S2 hgx_over_dyads lane)
        ("Alpha Ltd", "NIFTY50", "listed_on_index", "{}"),
        ("Beta Ltd", "NIFTY50", "listed_on_index", "{}"),
        ("Gamma Ltd", "NIFTY50", "listed_on_index", "{}"),
        # S4 tree/portfolio families
        ("Beta Ltd", "HoldCo", "subsidiary_of", "{}"),
        ("Gamma Ltd", "HoldCo", "subsidiary_of", "{}"),
        ("Delta Ltd", "HoldCo", "subsidiary_of", "{}"),
        # symmetric: same_group via $.group — both endpoints are members
        ("Alpha Ltd", "Beta Ltd", "same_group", json.dumps({"group": "Promoter_Group_1"})),
        ("Gamma Ltd", "Delta Ltd", "same_group", json.dumps({"group": "Promoter_Group_1"})),
        # symmetric: jv_with via $.venture
        ("Alpha Ltd", "Gamma Ltd", "jv_with", json.dumps({"venture": "VentureX"})),
    ]
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, weight, source_ref, properties) "
        "VALUES (?, ?, ?, 1.0, 't', ?)",
        dyads,
    )
    conn.commit()


@pytest.fixture()
def chain_store(tmp_path: Path):
    db = tmp_path / "chain.db"
    conn = sqlite3.connect(str(db))
    _seed(conn)
    yield db, conn
    conn.close()


def test_collect_regroups_dyads_into_expected_families(chain_store):
    _db, conn = chain_store
    hyper, weights = dh.collect_hyperedges(conn)
    # star sources regroup source→label
    assert hyper["theme"] == {"IT_Services": {"Alpha Ltd", "Beta Ltd"}, "Cement": {"Gamma Ltd"}}
    assert hyper["country"] == {"India": {"Alpha Ltd", "Delta Ltd"}}
    assert hyper["index"] == {"NIFTY50": {"Alpha Ltd", "Beta Ltd", "Gamma Ltd"}}
    assert hyper["subsidiary"] == {"HoldCo": {"Beta Ltd", "Gamma Ltd", "Delta Ltd"}}
    # symmetric sources regroup BOTH endpoints via the properties label
    assert hyper["group"] == {
        "Promoter_Group_1": {"Alpha Ltd", "Beta Ltd", "Gamma Ltd", "Delta Ltd"}
    }
    assert hyper["jv"] == {"VentureX": {"Alpha Ltd", "Gamma Ltd"}}
    # hermetic fixture: no quotes/events/industry sources fired
    assert weights == {}
    assert hyper["edition"] == {}  # co_mentioned_in is a symmetric source — empty here
    assert hyper.get("event", {}) == {}  # no events table → the family never materialises
    assert hyper["industry"] == {}


def test_dry_run_reports_but_writes_nothing(chain_store):
    _db, conn = chain_store
    hyper, weights = dh.collect_hyperedges(conn)
    stats = dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=True)
    # the dry-run reports what WOULD land: 6 families, all non-empty here
    landed = {et: counts for et, counts in stats.items() if counts != (0, 0)}
    assert set(landed) == {"theme", "country", "index", "subsidiary", "group", "jv"}
    assert (
        sum(n for n, _i in landed.values()) == 7
    )  # 7 labels (theme has two: IT_Services + Cement)
    # memberships: theme 3 + country 2 + index 3 + subsidiary 3 + group 4 + jv 2
    assert sum(i for _n, i in landed.values()) == 17
    # ...and the store is untouched
    assert conn.execute("SELECT COUNT(*) FROM hyper_edges").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0] == 0


def test_apply_writes_store_then_is_idempotent(chain_store):
    db, conn = chain_store
    hyper, weights = dh.collect_hyperedges(conn)
    dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)

    edges = {
        (et, label, json.loads(props)["n_members"])
        for et, label, props in conn.execute("SELECT edge_type, label, properties FROM hyper_edges")
    }
    assert ("index", "NIFTY50", 3) in edges
    assert ("subsidiary", "HoldCo", 3) in edges
    assert ("group", "Promoter_Group_1", 4) in edges
    assert len(edges) == 7
    # every hyperedge carries its upstream lineage (the S8 convergence key)
    for et, _label, props in conn.execute("SELECT edge_type, label, properties FROM hyper_edges"):
        assert json.loads(props)["upstream_types"] == dh._upstream_types(et)
    # incidences = union of memberships = 17
    n_inc = conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0]
    assert n_inc == 17

    # re-run: INSERT OR IGNORE both uniqueness keys → 0/0 everywhere
    hyper2, _w2 = dh.collect_hyperedges(conn)
    stats2 = dh.apply_hyperedges(hyper2, conn=conn, dry_run=False)
    assert all(counts == (0, 0) for counts in stats2.values())


def test_hif_serialization_roundtrip(chain_store, tmp_path: Path):
    db, conn = chain_store
    hyper, weights = dh.collect_hyperedges(conn)
    dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
    sources = ["theme", "country", "index", "subsidiary", "group", "jv"]

    out_dir = tmp_path / "hif"
    paths = hh.write_hif_parquet(sources, out_dir, db_path=str(db))
    assert set(paths) == {hh.NODES_FN, hh.EDGES_FN, hh.INCIDENCES_FN}

    counts = hh.validate_hif_roundtrip(sources, out_dir, db_path=str(db))
    # store counts flow through the parquet untouched
    assert counts["edges"] == 7
    assert counts["incidences"] == 17
    assert counts["nodes"] == 4  # all four entities carry at least one incidence
    assert counts["weighted"] == 0  # unweighted fixture


def test_hif_rejects_store_drift(chain_store, tmp_path: Path):
    """The validator's reason to exist: a store that gains a hyperedge
    after the export must fail the count check, not pass silently."""
    db, conn = chain_store
    hyper, weights = dh.collect_hyperedges(conn)
    dh.apply_hyperedges(hyper, weights, conn=conn, dry_run=False)
    sources = ["theme", "country", "index", "subsidiary", "group", "jv"]
    out_dir = tmp_path / "hif"
    hh.write_hif_parquet(sources, out_dir, db_path=str(db))

    conn.execute(
        "INSERT INTO hyper_edges (id, edge_type, label, weight, source_ref) "
        "VALUES (99, 'theme', 'Post_Export_Theme', 1.0, 't')"
    )
    conn.execute("INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (99, 'Alpha Ltd')")
    conn.commit()
    with pytest.raises(ValueError, match="edge count drift"):
        hh.validate_hif_roundtrip(sources, out_dir, db_path=str(db))


def test_hypergraph_builds_from_applied_store(chain_store):
    """The algorithms lane's entry point, gated on hypergraphx (absent
    from this venv — runs when the extension returns)."""
    hgx = pytest.importorskip("hypergraphx")
    _db, conn = chain_store
    hyper, _w = dh.collect_hyperedges(conn)
    dh.apply_hyperedges(hyper, conn=conn, dry_run=False)

    rows = conn.execute(
        "SELECT he.edge_type || ':' || he.label, hi.entity_name "
        "FROM hyper_incidences hi JOIN hyper_edges he ON he.id = hi.edge_id"
    ).fetchall()
    families: dict[str, set[str]] = {}
    for eid, member in rows:
        families.setdefault(eid, set()).add(member)
    hg = hgx.Hypergraph(edge_list=[frozenset(m) for m in families.values()])
    assert hg.num_nodes() == 4
    assert hg.num_edges() == 7
