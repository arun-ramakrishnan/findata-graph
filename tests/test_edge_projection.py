#!/usr/bin/env python3
"""Semantics pin for helpers/graph/edge_projection.py (test_coverage_gap_sweep S1).

The part_of/has_company retired-edge projection is the single shared
semantic of seven SQLite graph lanes (csr, l1_mst, l1_betweenness,
scipy_bridge, suggest_relations, layout, graph_scale_watch) — today it
is only exercised transitively through those consumers' tests. This
file pins the contract directly:

* stored dyads pass through (narrow ``graph_edges`` shapes degrade, the
  module docstring's two tolerances),
* the sector incidence family projects BOTH directions with a
  hi.weight → he.weight → 1.0 precedence,
* non-sector families never project.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import EDGES_12COL, HYPER_STORE  # noqa: E402

from helpers.graph import edge_projection as ep  # noqa: E402


def _db(tmp_path: Path, name: str, *ddl: str) -> sqlite3.Connection:
    con = sqlite3.connect(str(tmp_path / name))
    for d in ddl:
        con.executescript(d)  # HYPER_STORE carries two CREATE TABLE statements
    return con


def test_projected_types_constant():
    assert ep.PROJECTED_TYPES == ("part_of", "has_company")


def test_has_incidence_store_probe(tmp_path: Path):
    bare = _db(tmp_path, "bare.db", EDGES_12COL)
    assert ep.has_incidence_store(bare) is False
    full = _db(tmp_path, "full.db", EDGES_12COL, HYPER_STORE)
    assert ep.has_incidence_store(full) is True
    bare.close()
    full.close()


def test_narrow_two_column_edges_degrade(tmp_path: Path):
    """The module docstring's narrow tolerance: a fixture declaring only
    (source, target) reads as edge_type='' and weight=1.0 — and '' never
    matches 'listed_on_index', so centrality exclusions stay no-ops."""
    con = _db(tmp_path, "narrow.db", "CREATE TABLE graph_edges (source TEXT, target TEXT)")
    con.executemany("INSERT INTO graph_edges VALUES (?, ?)", [("A", "B"), ("B", "C")])
    rows = ep.edge_rows(con)
    assert sorted(rows) == [("A", "B", "", 1.0), ("B", "C", "", 1.0)]
    con.close()


def test_missing_weight_column_defaults_to_one(tmp_path: Path):
    """edge_type present, weight absent — COALESCE falls to 1.0."""
    con = _db(
        tmp_path,
        "noweight.db",
        "CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)",
    )
    con.executemany("INSERT INTO graph_edges VALUES (?, ?, ?)", [("A", "IDX", "listed_on_index")])
    assert ep.edge_rows(con) == [("A", "IDX", "listed_on_index", 1.0)]
    con.close()


def test_missing_edge_type_column_reads_empty(tmp_path: Path):
    """weight present, edge_type absent — the exclusion set stays a no-op."""
    con = _db(
        tmp_path, "notype.db", "CREATE TABLE graph_edges (source TEXT, target TEXT, weight REAL)"
    )
    con.execute("INSERT INTO graph_edges VALUES ('A', 'B', 2.5)")
    assert ep.edge_rows(con) == [("A", "B", "", 2.5)]
    con.close()


def test_null_weight_coalesces_to_one(tmp_path: Path):
    # EDGES_12COL forbids NULL weight (NOT NULL DEFAULT 1.0); the COALESCE
    # tolerance is for stores that declare a nullable weight column.
    con = _db(
        tmp_path,
        "nullw.db",
        "CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, weight REAL, source_ref TEXT)",
    )
    con.execute(
        "INSERT INTO graph_edges (source, target, edge_type, weight, source_ref) "
        "VALUES ('A', 'B', 'supply_chain', NULL, 't')"
    )
    assert ep.edge_rows(con) == [("A", "B", "supply_chain", 1.0)]
    con.close()


def test_plain_dyads_passthrough_without_incidence_store(tmp_path: Path):
    """Pre-retirement restore (or dyads-only fixture): no hyper tables →
    the plain read, byte-for-byte what's stored."""
    con = _db(tmp_path, "plain.db", EDGES_12COL)
    con.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, weight, source_ref) VALUES (?, ?, ?, ?, 't')",
        [("A", "B", "supply_chain", 3.0), ("A", "IDX", "listed_on_index", 1.0)],
    )
    rows = ep.edge_rows(con)
    assert ("A", "B", "supply_chain", 3.0) in rows
    assert ("A", "IDX", "listed_on_index", 1.0) in rows
    assert len(rows) == 2
    con.close()


def _seed_sector_family(
    con: sqlite3.Connection, *, hi_weight: float | None, he_weight: float | None
) -> None:
    con.execute(
        "INSERT INTO hyper_edges (id, edge_type, label, weight, source_ref) VALUES (1, 'sector', 'Banking', ?, 't')",
        (he_weight if he_weight is not None else 1.0,),
    )
    con.execute(
        "INSERT INTO hyper_incidences (edge_id, entity_name, weight) VALUES (1, 'Alpha', ?)",
        (hi_weight,),
    )
    # a non-sector family in the same store must never project
    con.execute(
        "INSERT INTO hyper_edges (id, edge_type, label, weight, source_ref) VALUES (2, 'edition', 'E1', 1.0, 't')"
    )
    con.execute("INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (2, 'Alpha')")


def test_sector_family_projects_both_directions(tmp_path: Path):
    con = _db(tmp_path, "proj.db", EDGES_12COL, HYPER_STORE)
    _seed_sector_family(con, hi_weight=None, he_weight=1.0)
    rows = ep.edge_rows(con)
    assert ("Alpha", "Banking", "part_of", 1.0) in rows
    assert ("Banking", "Alpha", "has_company", 1.0) in rows
    con.close()


def test_weight_precedence_incidence_over_edge_over_one(tmp_path: Path):
    """hi.weight when set, else he.weight, else 1.0 — one consistent
    number per membership for weighted lanes."""
    con = _db(tmp_path, "w1.db", EDGES_12COL, HYPER_STORE)
    _seed_sector_family(con, hi_weight=2.0, he_weight=5.0)
    got = {(s, t): w for s, t, _et, w in ep.edge_rows(con)}
    assert got[("Alpha", "Banking")] == 2.0  # incidence weight wins
    con.close()

    con = _db(tmp_path, "w2.db", EDGES_12COL, HYPER_STORE)
    _seed_sector_family(con, hi_weight=None, he_weight=5.0)
    got = {(s, t): w for s, t, _et, w in ep.edge_rows(con)}
    assert got[("Alpha", "Banking")] == 5.0  # falls to the hyperedge weight
    con.close()


def test_non_sector_family_never_projects(tmp_path: Path):
    con = _db(tmp_path, "nonsec.db", EDGES_12COL, HYPER_STORE)
    _seed_sector_family(con, hi_weight=None, he_weight=1.0)
    pairs = ep.pairs(con)
    # only the sector family members appear via the projection; the
    # edition-family membership ('Alpha' in 'E1') must NOT become an edge
    assert pairs.count(("Alpha", "Banking")) == 1
    assert ("Alpha", "E1") not in pairs
    assert ("E1", "Alpha") not in pairs
    con.close()


def test_pairs_is_the_endpoint_view(tmp_path: Path):
    con = _db(tmp_path, "pairs.db", EDGES_12COL, HYPER_STORE)
    _seed_sector_family(con, hi_weight=2.0, he_weight=1.0)
    dyads = [("A", "B", "supply_chain", 3.0)]
    con.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, weight, source_ref) VALUES (?, ?, ?, ?, 't')",
        dyads,
    )
    rows = ep.edge_rows(con)
    assert ep.pairs(con) == [(s, t) for s, t, _et, _w in rows]
    con.close()


def test_dyads_and_projection_coexist(tmp_path: Path):
    """A store can carry BOTH stored dyads and the projected pair — the
    UNION ALL is additive; dedupe is the caller's decision (documented)."""
    con = _db(tmp_path, "both.db", EDGES_12COL, HYPER_STORE)
    con.execute(
        "INSERT INTO graph_edges (source, target, edge_type, weight, source_ref) "
        "VALUES ('Alpha', 'Banking', 'legacy_part_of', 1.0, 't')"
    )
    _seed_sector_family(con, hi_weight=None, he_weight=1.0)
    rows = ep.edge_rows(con)
    # the stored dyad (different edge_type) AND the projected pair both surface
    assert ("Alpha", "Banking", "legacy_part_of", 1.0) in rows
    assert ("Alpha", "Banking", "part_of", 1.0) in rows
    con.close()


def test_read_convenience_opens_and_closes(tmp_path: Path):
    """The read() convenience opens the GIVEN path read-only — never the
    live research.db (db_path is passed through to connect)."""
    db = tmp_path / "conv.db"
    con = sqlite3.connect(str(db))
    con.execute(EDGES_12COL)
    con.execute(
        "INSERT INTO graph_edges (source, target, edge_type, weight, source_ref) "
        "VALUES ('A', 'B', 'supply_chain', 1.0, 't')"
    )
    con.commit()
    con.close()
    assert ep.read(db) == [("A", "B", "supply_chain", 1.0)]
