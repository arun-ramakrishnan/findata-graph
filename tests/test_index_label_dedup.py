"""Tests for helpers/maintenance/index_label_dedup.py."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import sqlite3  # noqa: E402

from helpers.maintenance.index_label_dedup import (  # noqa: E402
    INDEX_NAME_CANONICAL,
    OTHER_MEASURED_DUPS,
    apply_fold,
    plan,
)
from tests.schema import EDGES_12COL, ENTITIES_8COL


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(ENTITIES_8COL + EDGES_12COL)
    conn.execute("PRAGMA foreign_keys = ON")
    # production graph_edges carries FK → entities(name) ON DELETE CASCADE;
    # the shared test schema omits FK clauses, so rebuild with them.
    conn.execute("DROP TABLE graph_edges")
    conn.execute(
        """
        CREATE TABLE graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
            target TEXT NOT NULL REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
            edge_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            properties TEXT NOT NULL DEFAULT '{}',
            valid_from DATE,
            valid_to DATE,
            source_ref TEXT NOT NULL,
            symmetric INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, target, edge_type)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE hyper_edges (
            id INTEGER PRIMARY KEY,
            edge_type TEXT NOT NULL,
            label TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            valid_from DATE, valid_to DATE,
            source_ref TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(edge_type, label)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE hyper_incidences (
            edge_id INTEGER NOT NULL REFERENCES hyper_edges(id) ON DELETE CASCADE,
            entity_name TEXT NOT NULL,
            weight REAL, direction TEXT, role TEXT,
            valid_from DATE, valid_to DATE,
            PRIMARY KEY (edge_id, entity_name)
        )
        """
    )
    conn.commit()
    return conn


def _conn() -> sqlite3.Connection:
    return _open(":memory:")


def _seed(conn: sqlite3.Connection) -> None:
    # canonical + alias index entities
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'index')",
        [
            ("NIFTY 500",),
            ("Nifty500 Multicap 50-25-25 Index",),
            ("Nifty500 LargeMidSmall Equal-Cap Weighted",),
            ("Reliance Industries",),
        ],
    )
    # company rows
    conn.execute("UPDATE entities SET entity_type = 'company' WHERE name = 'Reliance Industries'")
    # listed_on_index edges to both labels
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
        "VALUES ('Reliance Industries', ?, 'listed_on_index', 'derive:indices')",
        [
            ("NIFTY 500",),
            ("Nifty500 Multicap 50-25-25 Index",),
            ("Nifty500 LargeMidSmall Equal-Cap Weighted",),
        ],
    )
    # hyperedges + incidences for both labels
    conn.execute(
        "INSERT INTO hyper_edges (id, edge_type, label, source_ref) VALUES "
        "(1, 'index', 'NIFTY 500', 'derive:hyperedges:index'), "
        "(2, 'index', 'Nifty500 Multicap 50-25-25 Index', 'derive:hyperedges:index'), "
        "(3, 'index', 'Nifty500 LargeMidSmall Equal-Cap Weighted', 'derive:hyperedges:index')"
    )
    conn.executemany(
        "INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (?, ?)",
        [
            (1, "Reliance Industries"),
            (2, "Reliance Industries"),
            (3, "Reliance Industries"),
        ],
    )
    conn.commit()


def test_plan_detects_pairs_and_residuals():
    conn = _conn()
    _seed(conn)
    p = plan(conn)
    # plan() lists every fold-map pair; only the 2 seeded ones have entities
    assert len(p["pairs"]) == len(INDEX_NAME_CANONICAL)
    assert set(p["entities_to_drop"]) == {
        "Nifty500 Multicap 50-25-25 Index",
        "Nifty500 LargeMidSmall Equal-Cap Weighted",
    }
    assert len(p["hyperedge_folds"]) == 2
    # graph_edges.target refs remain (they are cascade-deleted on entity drop)
    assert p["residual_refs"]["Nifty500 Multicap 50-25-25 Index"]["graph_edges.target"] == 1
    conn.close()


def test_apply_fold_merges_and_drops():
    conn = _conn()
    _seed(conn)
    stats = apply_fold(conn)
    assert stats["hyperedges_merged"] == 2
    assert stats["entities_dropped"] == 2
    # identical member sets: no NEW incidences needed (existing canonical set covers all)
    assert stats["incidences_moved"] == 0
    # only canonical hyperedge remains
    labels = [r[0] for r in conn.execute("SELECT label FROM hyper_edges ORDER BY label").fetchall()]
    assert labels == ["NIFTY 500"]
    inc = conn.execute(
        "SELECT COUNT(*) FROM hyper_incidences hi JOIN hyper_edges he ON he.id = hi.edge_id "
        "WHERE he.label = 'NIFTY 500'"
    ).fetchone()[0]
    assert inc == 1
    # entities: canonical + company remain, aliases gone
    ents = [r[0] for r in conn.execute("SELECT name FROM entities ORDER BY name").fetchall()]
    assert ents == ["NIFTY 500", "Reliance Industries"]
    # listed_on_index edges for aliases cascade-deleted
    edges = conn.execute(
        "SELECT COUNT(*) FROM graph_edges WHERE edge_type = 'listed_on_index'"
    ).fetchone()[0]
    assert edges == 1
    conn.close()


def test_apply_idempotent():
    conn = _conn()
    _seed(conn)
    apply_fold(conn)
    stats2 = apply_fold(conn)
    assert stats2["hyperedges_merged"] == 0
    assert stats2["entities_dropped"] == 0
    conn.close()


def test_apply_persists_after_close(tmp_path):
    """Regression: apply_fold must commit — closing the conn rolls back otherwise."""
    db = tmp_path / "t.db"
    conn = _open(db)
    _seed(conn)
    conn.commit()
    apply_fold(conn)
    conn.close()

    check = sqlite3.connect(db)
    ents = [r[0] for r in check.execute("SELECT name FROM entities ORDER BY name").fetchall()]
    assert ents == ["NIFTY 500", "Reliance Industries"]
    labels = [r[0] for r in check.execute("SELECT label FROM hyper_edges").fetchall()]
    assert labels == ["NIFTY 500"]
    check.close()


def test_other_measured_dups_reported_not_folded():
    # All measured dup pairs are folded as of 2026-11-14; the residual
    # list is empty but the invariant still holds vacuously.
    assert all(isinstance(x, tuple) and len(x) == 3 for x in OTHER_MEASURED_DUPS)
    # none of the OTHER pairs are in the fold map
    for a, b, _n in OTHER_MEASURED_DUPS:
        assert a not in INDEX_NAME_CANONICAL
        assert b not in INDEX_NAME_CANONICAL
