#!/usr/bin/env python3
"""Tests for helpers/graph/_edge_writer.py (the shared apply_typed_edges).

This is the consolidated edge-writer that derive_co_mentions.apply_edges and
derive_themes.apply_edges delegate to. The two wrappers' own test suites
(test_derive_co_mentions.py, test_derive_themes.py) cover them end-to-end
through the public API; this file pins the shared util's contract directly,
so a future third caller (or a refactor of the wrapper signatures) has a
focused regression net.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph._edge_writer import apply_typed_edges  # noqa: E402
from tests.schema import EDGES_MINIMAL, ENTITIES_MINIMAL  # noqa: E402


def _build_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("".join([ENTITIES_MINIMAL, EDGES_MINIMAL]))
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
        [("A", "company"), ("B", "company"), ("C", "company")],
    )
    conn.commit()
    conn.close()
    return db_path


def _edges():
    return [
        ("A", "B", {"k": "v1"}, "ref1"),
        ("B", "C", {"k": "v2"}, "ref2"),
        ("A", "C", {"k": "v3"}, "ref3"),
    ]


class TestApplyTypedEdges:
    def test_inserts_and_counts(self, tmp_path):
        db = _build_db(tmp_path)
        conn = sqlite3.connect(db)
        try:
            n = apply_typed_edges(
                _edges(),
                edge_type="exposed_to",
                symmetric=0,
                conn=conn,
                dry_run=False,
            )
            assert n == 3
            rows = conn.execute(
                "SELECT source, target, edge_type, symmetric, source_ref, properties "
                "FROM graph_edges ORDER BY source, target"
            ).fetchall()
            assert len(rows) == 3
            for _s, _t, et, sym, sref, props in rows:
                assert et == "exposed_to"
                assert sym == 0
                assert sref.startswith("ref")
                assert props.startswith("{")  # JSON
        finally:
            conn.close()

    def test_dry_run_counts_without_writing(self, tmp_path):
        db = _build_db(tmp_path)
        conn = sqlite3.connect(db)
        try:
            n = apply_typed_edges(
                _edges(),
                edge_type="exposed_to",
                symmetric=0,
                conn=conn,
                dry_run=True,
            )
            assert n == 3  # would insert all 3
            # Nothing actually written.
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type = 'exposed_to'"
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()

    def test_idempotent_rerun_inserts_nothing(self, tmp_path):
        db = _build_db(tmp_path)
        conn = sqlite3.connect(db)
        try:
            first = apply_typed_edges(
                _edges(),
                edge_type="exposed_to",
                symmetric=0,
                conn=conn,
                dry_run=False,
            )
            second = apply_typed_edges(
                _edges(),
                edge_type="exposed_to",
                symmetric=0,
                conn=conn,
                dry_run=False,
            )
            assert first == 3
            assert second == 0  # all already present
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type = 'exposed_to'"
            ).fetchone()[0]
            assert count == 3
        finally:
            conn.close()

    def test_dry_run_after_insert_counts_zero(self, tmp_path):
        db = _build_db(tmp_path)
        conn = sqlite3.connect(db)
        try:
            apply_typed_edges(
                _edges(),
                edge_type="co_mentioned_in",
                symmetric=1,
                conn=conn,
                dry_run=False,
            )
            n = apply_typed_edges(
                _edges(),
                edge_type="co_mentioned_in",
                symmetric=1,
                conn=conn,
                dry_run=True,
            )
            assert n == 0  # bulk-fetch sees all existing pairs
        finally:
            conn.close()

    def test_edge_type_and_symmetric_are_passed_through(self, tmp_path):
        """The whole point of the util: edge_type + symmetric are parameters,
        not hardcoded. Two different edge types coexist without interference."""
        db = _build_db(tmp_path)
        conn = sqlite3.connect(db)
        try:
            apply_typed_edges(
                _edges(),
                edge_type="co_mentioned_in",
                symmetric=1,
                conn=conn,
                dry_run=False,
            )
            apply_typed_edges(
                _edges(),
                edge_type="exposed_to",
                symmetric=0,
                conn=conn,
                dry_run=False,
            )
            # Same (source, target) pairs, different edge_types → both present.
            cm = conn.execute(
                "SELECT symmetric FROM graph_edges WHERE edge_type = 'co_mentioned_in'"
            ).fetchall()
            et = conn.execute(
                "SELECT symmetric FROM graph_edges WHERE edge_type = 'exposed_to'"
            ).fetchall()
            assert len(cm) == 3 and {s for (s,) in cm} == {1}
            assert len(et) == 3 and {s for (s,) in et} == {0}
        finally:
            conn.close()

    def test_own_conn_opens_and_closes(self, tmp_path, monkeypatch):
        """When conn=None, the util opens its own connection and closes it
        (no leak). Verifies the own_conn finally-close branch.

        We monkeypatch the util's ``connect`` so it points at the temp DB
        instead of the live production DB (which the real ``connect()``
        would open, and where A/B/C don't exist)."""
        from helpers.graph import _edge_writer

        db = _build_db(tmp_path)
        monkeypatch.setattr(
            _edge_writer,
            "connect",
            lambda: sqlite3.connect(db),
        )
        n = apply_typed_edges(
            _edges(),
            edge_type="exposed_to",
            symmetric=0,
            conn=None,
            dry_run=False,
        )
        assert n == 3
        # Verify via a fresh connection that the write committed.
        conn = sqlite3.connect(db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type = 'exposed_to'"
            ).fetchone()[0]
            assert count == 3
        finally:
            conn.close()

    def test_empty_edge_iterable_is_noop(self, tmp_path):
        db = _build_db(tmp_path)
        conn = sqlite3.connect(db)
        try:
            n = apply_typed_edges(
                [],
                edge_type="exposed_to",
                symmetric=0,
                conn=conn,
                dry_run=False,
            )
            assert n == 0
        finally:
            conn.close()
