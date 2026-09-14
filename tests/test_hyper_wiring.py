"""Wiring tests for the HGX compute lanes (hyper_lane_wiring W1).

The lanes became maint-full TIER2 steps — they must never block on a
degenerate store, and their `--apply` write path (via
algorithms.write_analytics) must be pinned: rows appear, re-runs are
idempotent, `computed_at` is never restamped (zero-churn doctrine #147).
Skip paths cover absent tables (fresh DBs), an empty store, and a
guard-exhausted store (the maint-chain fixture shape).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.graph import algorithms as alg  # noqa: E402
from helpers.graph import hyper_arrow as ha  # noqa: E402
from helpers.graph import hyper_centralities as hcen  # noqa: E402
from helpers.graph import hyper_communities as hcom  # noqa: E402
from helpers.maintenance.migrate_to_graph_edges import (  # noqa: E402
    ENTITIES_DDL,
    GRAPH_ANALYTICS_DDL,
    HYPER_EDGES_DDL,
    HYPER_INCIDENCES_DDL,
)

CO = ["Alpha", "Beta", "Gamma", "Delta"]


def _build_db(
    path: Path,
    *,
    with_store: bool = True,
    edges: list[tuple[str, str, list[str]]] | None = None,
) -> None:
    """Canonical DDLs + optional hyperedges as (edge_type, label, members).

    Default ``edges`` is a 2-member chain (sector A-B, theme B-C,
    industry C-D): every edge survives the degeneracy guard at n_nodes=4
    (threshold max(2.0, 0.25*4) = 2.0) and the whole graph stays in one
    component.
    """
    if edges is None and with_store:
        edges = [
            ("sector", "Banking", ["Alpha", "Beta"]),
            ("theme", "Digital", ["Beta", "Gamma"]),
            ("industry", "Banks", ["Gamma", "Delta"]),
        ]
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(ENTITIES_DDL)
    conn.execute(GRAPH_ANALYTICS_DDL)
    if with_store:
        conn.execute(HYPER_EDGES_DDL)
        conn.execute(HYPER_INCIDENCES_DDL)
        if edges:
            conn.executemany(
                "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
                [(n,) for n in {m for _, _, ms in edges for m in ms}],
            )
            for edge_type, label, members in edges:
                cur = conn.execute(
                    "INSERT INTO hyper_edges (edge_type, label, source_ref) VALUES (?, ?, 'test')",
                    (edge_type, label),
                )
                edge_id = cur.lastrowid
                conn.executemany(
                    "INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (?, ?)",
                    [(edge_id, m) for m in members],
                )
    conn.commit()
    conn.close()


def _redirect(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    """Point the lanes' data load AND write_analytics at the tmp DB."""
    monkeypatch.setattr(ha, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(alg, "connect", lambda *a, **k: sqlite3.connect(db_path))


# --- skip paths (never-block) -----------------------------------------------


class TestSkipPaths:
    def test_absent_tables_skip_both_lanes(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "no_store.db"
        _build_db(db, with_store=False)
        _redirect(monkeypatch, db)
        assert hcom._cli([]) == 0
        assert hcen._cli([]) == 0
        out = capsys.readouterr().out
        assert out.count("skipping") == 2

    def test_missing_db_file_skips(self, tmp_path, monkeypatch):
        _redirect(monkeypatch, tmp_path / "never_created.db")
        assert hcom._cli([]) == 0

    def test_empty_store_skips_both_lanes(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "empty_store.db"
        _build_db(db, with_store=True, edges=[])
        _redirect(monkeypatch, db)
        assert hcom._cli([]) == 0
        assert hcen._cli([]) == 0
        assert capsys.readouterr().out.count("skipping") == 2

    def test_guard_exhausted_skips_both_lanes(self, tmp_path, monkeypatch, capsys):
        # singleton-only store: the probe passes (1 hyperedge) but the
        # degeneracy guard drops everything — the maint chain must not die
        db = tmp_path / "singleton.db"
        _build_db(db, edges=[("sector", "Lonely", ["Alpha"])])
        _redirect(monkeypatch, db)
        assert hcom._cli([]) == 0
        assert hcen._cli([]) == 0
        out = capsys.readouterr().out
        assert out.count("degeneracy guard") == 2


# --- --apply write path (was unpinned) --------------------------------------


class TestApplyWritePath:
    def test_communities_apply_writes_idempotent_no_restamp(self, tmp_path, monkeypatch):
        db = tmp_path / "apply.db"
        _build_db(db)
        _redirect(monkeypatch, db)

        assert hcom._cli(["--apply"]) == 0
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT entity_name, value FROM graph_analytics "
            "WHERE metric = 'hypermmsbm_community' ORDER BY entity_name"
        ).fetchall()
        assert len(rows) == 4  # every company in the incidence store
        # sentinel stamp: a re-run must converge value WITHOUT touching it
        conn.execute(
            "UPDATE graph_analytics SET computed_at = '2000-01-01 00:00:00' "
            "WHERE metric = 'hypermmsbm_community'"
        )
        conn.commit()
        conn.close()

        assert hcom._cli(["--apply"]) == 0  # idempotent re-run
        conn = sqlite3.connect(db)
        rows2 = conn.execute(
            "SELECT entity_name, value FROM graph_analytics "
            "WHERE metric = 'hypermmsbm_community' ORDER BY entity_name"
        ).fetchall()
        stamps = conn.execute(
            "SELECT DISTINCT computed_at FROM graph_analytics WHERE metric = 'hypermmsbm_community'"
        ).fetchall()
        conn.close()
        assert rows2 == rows  # seeded fit: values identical
        assert stamps == [("2000-01-01 00:00:00",)]  # upsert never restamps

    def test_centralities_apply_writes_core_lanes(self, tmp_path, monkeypatch):
        db = tmp_path / "cen.db"
        _build_db(db)
        _redirect(monkeypatch, db)

        assert hcen._cli(["--apply"]) == 0
        conn = sqlite3.connect(db)
        counts = dict(
            conn.execute("SELECT metric, COUNT(*) FROM graph_analytics GROUP BY 1").fetchall()
        )
        conn.close()
        assert counts["ho_pagerank"] == 4
        assert counts["s_betweenness"] == 4
        assert counts["s_closeness"] == 4
        # uniform 2-member edges: the eigen trio runs too (S15 gate)
        assert {"eigen_cec", "eigen_zec", "eigen_hec"} <= set(counts)

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        db = tmp_path / "dry.db"
        _build_db(db)
        _redirect(monkeypatch, db)
        assert hcom._cli([]) == 0
        assert hcen._cli([]) == 0
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM graph_analytics").fetchone()[0] == 0
        conn.close()
