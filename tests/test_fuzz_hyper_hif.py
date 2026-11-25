#!/usr/bin/env python3
"""Fuzz tests for the HIF export lane (S4, hyper_hif leg).

Random small hypergraph stores are exported with `write_hif_parquet`
and read back; the parquet tables must preserve:
- node set (entities named in incidences),
- edge set (edge_type:label ids),
- incidence count and per-incidence weight,
- footer metadata (network_type, sources).

Malformed-rejection: an empty source list raises (the SQL IN ()
clause is invalid); a source with no rows still exports empty tables
(valid empty export).
Can-fail proof: an incidence whose edge references a missing hyper_edge
row must not appear (the JOIN drops it) — hand-built and asserted.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pyarrow.parquet as pq
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from helpers.graph import hyper_hif as hif

_EDGE_TYPES = st.sampled_from(["sector", "theme", "industry", "sub_sector"])
_LABELS = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=20)
_ENTITY_NAMES = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=20
)
_WEIGHTS = st.one_of(
    st.none(), st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
)

_SETTINGS = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_EDGE_COLS = (
    "CREATE TABLE hyper_edges ("
    " id INTEGER PRIMARY KEY, edge_type TEXT NOT NULL, label TEXT NOT NULL,"
    " source_ref TEXT NOT NULL DEFAULT '', properties TEXT NOT NULL DEFAULT '',"
    " created_at TEXT NOT NULL DEFAULT '');"
)
_INCIDENCE_COLS = (
    "CREATE TABLE hyper_incidences ("
    " entity_name TEXT NOT NULL, edge_id INTEGER NOT NULL,"
    " weight REAL, direction TEXT NOT NULL DEFAULT '');"
)
_ENTITY_COLS = (
    "CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT NOT NULL DEFAULT '');"
)


@st.composite
def _store_data(draw):
    edges = draw(st.lists(st.tuples(_EDGE_TYPES, _LABELS), min_size=1, max_size=6))
    n_edges = len(edges)
    incidences = draw(
        st.lists(
            st.tuples(
                _ENTITY_NAMES,
                st.integers(min_value=0, max_value=n_edges - 1),
                _WEIGHTS,
            ),
            min_size=0,
            max_size=20,
        )
    )
    return edges, incidences


def _make_store(tmp_path: Path, edges, incidences):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(tmp_path / "src.db")
    db.executescript(_ENTITY_COLS + _EDGE_COLS + _INCIDENCE_COLS)
    db.executemany(
        "INSERT INTO hyper_edges (id, edge_type, label, source_ref, properties, created_at) "
        "VALUES (?, ?, ?, '', '', '')",
        [(i, et, lbl) for i, (et, lbl) in enumerate(edges)],
    )
    db.executemany(
        "INSERT INTO hyper_incidences (entity_name, edge_id, weight, direction) "
        "VALUES (?, ?, ?, '')",
        incidences,
    )
    db.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'co')",
        [(n,) for n in {n for n, _, _ in incidences}],
    )
    db.commit()
    db.close()
    return db


@_SETTINGS
@given(_store_data())
def test_export_roundtrip_preserves_sets_and_weights(tmp_path, data):
    edges, incidences = data
    import uuid

    run_dir = tmp_path / uuid.uuid4().hex
    src_db = run_dir / "src.db"
    out_dir = run_dir / "out"
    _make_store(run_dir, edges, incidences)
    sources = sorted({et for et, _ in edges})
    hif.write_hif_parquet(sources, out_dir, db_path=src_db)
    tbls = hif.read_hif_parquet(out_dir)
    nodes = {row["id"] for row in tbls["nodes"].to_pylist()}
    edge_ids = {row["id"] for row in tbls["edges"].to_pylist()}
    incs = tbls["incidences"].to_pylist()

    expected_nodes = {n for n, _, _ in incidences}
    assert nodes == expected_nodes
    assert edge_ids == {f"{et}:{lbl}" for et, lbl in edges}
    assert len(incs) == len(incidences)

    def _inc_key(t):
        # weights may be None (unlabeled) or float; None sorts first
        return (t[0], t[1], t[2] if t[2] is not None else -1.0)

    expected_incs = sorted(
        (
            (f"{edges[edge_idx][0]}:{edges[edge_idx][1]}", node, weight)
            for node, edge_idx, weight in incidences
        ),
        key=_inc_key,
    )
    got_incs = sorted(
        ((row["edge"], row["node"], row["weight"]) for row in incs),
        key=_inc_key,
    )
    assert got_incs == expected_incs

    meta = pq.read_metadata(out_dir / hif.NODES_FN).metadata
    assert meta[b"network_type"] == b"undirected"
    assert meta[b"sources"] == ",".join(sources).encode()


def test_empty_source_list_yields_empty_tables(tmp_path):
    _make_store(tmp_path, [("sector", "a")], [("e1", 0, 1.0)])
    hif.write_hif_parquet([], tmp_path / "out", db_path=tmp_path / "src.db")
    tbls = hif.read_hif_parquet(tmp_path / "out")
    assert tbls["nodes"].num_rows == 0
    assert tbls["edges"].num_rows == 0
    assert tbls["incidences"].num_rows == 0


def test_known_source_with_no_rows_yields_empty_tables(tmp_path):
    _make_store(tmp_path, [("sector", "a")], [("e1", 0, 1.0)])
    out = hif.write_hif_parquet(["theme"], tmp_path / "out", db_path=tmp_path / "src.db")
    tbls = hif.read_hif_parquet(tmp_path / "out")
    assert tbls["nodes"].num_rows == 0
    assert tbls["edges"].num_rows == 0
    assert tbls["incidences"].num_rows == 0
    assert out


def test_can_fail_incidence_with_missing_edge_is_dropped(tmp_path):
    """Hand-built bad incidence (edge_id 99 absent) must not appear."""
    _make_store(tmp_path, [("sector", "a")], [("e1", 0, 1.0), ("e2", 99, 2.0)])
    hif.write_hif_parquet(["sector"], tmp_path / "out", db_path=tmp_path / "src.db")
    incs = hif.read_hif_parquet(tmp_path / "out")["incidences"].to_pylist()
    assert [r["node"] for r in incs] == ["e1"]
    assert "e2" not in [r["node"] for r in incs]
