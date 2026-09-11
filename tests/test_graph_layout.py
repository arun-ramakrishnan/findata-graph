"""Precomputed cloud-layout tests — graph_rendering_overhaul lane 3.

Covers the deterministic FA2 engine, the edge-set hash gate on the
``memory/graph_layout.json`` sidecar (the embed-matrix refresh pattern),
and the ``/api/graph/positions`` endpoint through the Flask test client.
Hermetic: every test writes the sidecar to a tmp_path, never the live
``memory/`` artifact.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.graph.layout import (  # noqa: E402
    _MAX_NODES,
    compute_positions_fa2,
    edge_set_hash,
    load_or_compute_positions,
)
from tests.conftest import _UNIT_SCHEMA  # noqa: E402
from tests.helpers import flask_test_client, open_conn  # noqa: E402


def _seed_db(db_path: Path, edges: list[tuple[str, str, str]]) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_UNIT_SCHEMA)
    names = sorted({name for e in edges for name in (e[0], e[1])})
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
        [(n, "company") for n in names],
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,'test')",
        edges,
    )
    conn.commit()
    conn.close()


class TestComputePositionsFa2:
    def test_deterministic_and_finite(self):
        nodes = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
        edges = [
            ("Alpha", "Beta", "competes_with"),
            ("Beta", "Gamma", "invested_in"),
            ("Gamma", "Delta", "competes_with"),
            ("Alpha", "Epsilon", "invested_in"),
        ]
        p1 = compute_positions_fa2(nodes, edges, iterations=150)
        p2 = compute_positions_fa2(nodes, edges, iterations=150)
        assert p1 == p2, "same edge set must produce identical positions"
        assert set(p1) == set(nodes)
        coords = [tuple(v) for v in p1.values()]
        assert all(isinstance(x, int) and isinstance(y, int) for x, y in coords), (
            "positions are rounded ints for a compact sidecar"
        )
        assert len(set(coords)) == len(nodes), "distinct nodes land apart"

    def test_empty_graph(self):
        assert compute_positions_fa2([], []) == {}

    def test_node_ceiling_refusal(self):
        many = [f"n{i}" for i in range(_MAX_NODES + 1)]
        with pytest.raises(ValueError, match="ceiling"):
            compute_positions_fa2(many, [], iterations=1)


class TestHashGate:
    def _tiny_db(self, tmp_path: Path) -> Path:
        db = tmp_path / "unit.db"
        _seed_db(
            db,
            [
                ("Alpha", "Beta", "competes_with"),
                ("Beta", "Gamma", "invested_in"),
            ],
        )
        return db

    def test_reuse_then_recompute_on_edge_change(self, tmp_path: Path):
        db = self._tiny_db(tmp_path)
        sidecar = tmp_path / "layout.json"
        conn = open_conn(db)

        first = load_or_compute_positions(conn, path=sidecar)
        assert first["recomputed"] is True
        assert set(first["positions"]) == {"Alpha", "Beta", "Gamma"}
        stored = json.loads(sidecar.read_text())
        assert stored["edge_set_hash"] == first["edge_set_hash"]

        # Unchanged edge set: gated replay, no rewrite.
        second = load_or_compute_positions(conn, path=sidecar)
        assert second["recomputed"] is False
        assert second["computed_at"] == first["computed_at"]
        assert sidecar.read_text() == json.dumps(stored, separators=(",", ":")), (
            "gated replay must not rewrite the sidecar"
        )

        # Topology change → new hash → recompute.
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,'test')",
            ("Gamma", "Delta", "competes_with"),
        )
        conn.commit()
        conn.execute("INSERT INTO entities (name, entity_type) VALUES ('Delta','company')")
        conn.commit()
        third = load_or_compute_positions(conn, path=sidecar)
        assert third["recomputed"] is True
        assert third["edge_set_hash"] != first["edge_set_hash"]
        assert set(third["positions"]) == {"Alpha", "Beta", "Gamma", "Delta"}
        conn.close()

    def test_corrupt_sidecar_self_heals(self, tmp_path: Path):
        db = self._tiny_db(tmp_path)
        sidecar = tmp_path / "layout.json"
        sidecar.write_text("{ not json")
        conn = open_conn(db)
        payload = load_or_compute_positions(conn, path=sidecar)
        assert payload["recomputed"] is True
        assert set(payload["positions"]) == {"Alpha", "Beta", "Gamma"}
        conn.close()

    def test_hash_ignores_edge_order(self, tmp_path: Path):
        db = self._tiny_db(tmp_path)
        conn = open_conn(db)
        d1, nodes, edges = edge_set_hash(conn)
        # Re-insert in a different physical order (rowid differs).
        conn.execute("DELETE FROM graph_edges")
        conn.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,'test')",
            list(reversed(edges)),
        )
        conn.commit()
        d2, nodes2, _ = edge_set_hash(conn)
        assert d1 == d2 and nodes == nodes2
        conn.close()


class TestPositionsEndpoint:
    def test_get_positions_shape_and_etag(self, tmp_path: Path, monkeypatch):
        import app as A
        import helpers.graph.layout as layout

        db = tmp_path / "unit.db"
        _seed_db(
            db,
            [
                ("Alpha", "Beta", "competes_with"),
                ("Beta", "Gamma", "invested_in"),
            ],
        )
        sidecar = tmp_path / "layout.json"
        monkeypatch.setattr(layout, "POSITIONS_PATH", sidecar)
        # The /api/graph/* ETag derives from the DuckDB cache's built_at —
        # cold under xdist per-worker cache redirects. Stub it like
        # test_api_graph_unit does so the assertion is order-independent.
        monkeypatch.setattr(A, "_graph_etag", None)
        monkeypatch.setattr(A, "_graph_build_etag", lambda: 'W/"graph-test"')

        with flask_test_client(db) as client:
            r1 = client.get("/api/graph/positions")
            assert r1.status_code == 200
            body = r1.get_json()
            assert set(body["positions"]) == {"Alpha", "Beta", "Gamma"}
            assert body["engine"] == "fa2-numpy"
            assert body["recomputed"] is True
            assert r1.headers.get("ETag"), "/api/graph/* ETag policy applies"
            assert r1.headers["Cache-Control"] == "no-cache"

            # Gated replay through the endpoint.
            r2 = client.get("/api/graph/positions")
            assert r2.get_json()["recomputed"] is False

            # 304 via the shared ETag machinery.
            r3 = client.get(
                "/api/graph/positions",
                headers={"If-None-Match": r1.headers["ETag"]},
            )
            assert r3.status_code == 304
