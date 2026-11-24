#!/usr/bin/env python3
"""P2 pins: set-based hyper_incidences replacement on DuckDB.

The two derive-* call sites (org_membership, merger/demerger) used one
DELETE + one executemany per edge — ~300 statements for the live family
sizes. The shared helper must collapse them to one DELETE and one
INSERT SELECT while keeping the same rows, roles and idempotence.
SQLite callers keep their per-edge path (tested in their own files).
"""

from __future__ import annotations

import pytest

from helpers.graph.derive_merger_hyperedges import apply_derivation as merger_apply
from helpers.graph.derive_org_membership import apply_derivation as org_apply
from helpers.graph.incidence_batch import bulk_replace_hyper_incidences


class _CountingConn:
    """Delegate to a raw DuckDB conn; count execute/executemany calls."""

    def __init__(self, raw) -> None:
        self.raw = raw
        self.statements: list[str] = []
        self.batches: list[str] = []

    def _run(self, sql, *args, **kwargs):
        # Consume every result so register()/unregister() never see an
        # unfinished result; the repo proxy exposes sqlite-cursor shape.
        result = self.raw.execute(sql, *args, **kwargs)
        self._rows = result.fetchall()
        self._pos = 0
        return self

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql)
        return self._run(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        self.batches.append(sql)
        return self._run(sql, *args, **kwargs)

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        out = self._rows[self._pos :]
        self._pos = len(self._rows)
        return out

    def __iter__(self):
        return iter(self.fetchall())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.raw.commit()
        else:
            try:
                self.raw.rollback()
            except Exception:  # noqa: S110  # best-effort rollback; keep the real error
                pass
        return False

    def __getattr__(self, name):
        return getattr(self.raw, name)


def _schema(conn) -> None:
    conn.execute("CREATE TABLE entities (name VARCHAR PRIMARY KEY)")
    conn.execute("CREATE SEQUENCE hyper_edges_id_seq START 100")
    conn.execute(
        "CREATE TABLE hyper_edges (id BIGINT PRIMARY KEY DEFAULT nextval('hyper_edges_id_seq'), "
        "edge_type VARCHAR NOT NULL, "
        "label VARCHAR NOT NULL, weight DOUBLE, valid_from DATE, valid_to DATE, "
        "source_ref VARCHAR NOT NULL, properties VARCHAR, agent_id VARCHAR, "
        "source_tier VARCHAR, created_at TIMESTAMP, UNIQUE(edge_type, label))"
    )
    conn.execute(
        "CREATE TABLE hyper_incidences (edge_id BIGINT, entity_name VARCHAR, "
        'weight DOUBLE, direction VARCHAR, "role" VARCHAR, valid_from DATE, '
        "valid_to DATE, PRIMARY KEY(edge_id, entity_name))"
    )
    conn.execute(
        "CREATE TABLE provenance_agents (agent_id VARCHAR PRIMARY KEY, name VARCHAR, "
        "version VARCHAR, repo_ref VARCHAR, script VARCHAR, command VARCHAR, as_of VARCHAR)"
    )
    conn.executemany(
        "INSERT INTO entities VALUES (?)",
        [("Alpha",), ("Beta",), ("Gamma",), ("Delta",)],
    )


def _writes(wrapped: _CountingConn) -> list[str]:
    return [
        sql
        for sql in wrapped.statements
        if sql.lstrip().upper().startswith(("DELETE", "INSERT", "UPDATE"))
    ]


def test_bulk_replace_is_two_statements_and_keeps_rows():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(":memory:")
    _schema(conn)
    conn.execute(
        "INSERT INTO hyper_edges (id, edge_type, label, source_ref) "
        "VALUES (1, 'sector', 'ACME', 'seed')"
    )
    conn.execute(
        "INSERT INTO hyper_incidences (edge_id, entity_name, weight, role, valid_from) "
        "VALUES (1, 'Old', 1.0, 'holder', '2024-01-01')"
    )
    wrapped = _CountingConn(conn)
    bulk_replace_hyper_incidences(
        wrapped,
        [
            ("sector", "ACME", "Alpha", 0.5, "promoter", "2025-01-01"),
            ("sector", "ACME", "Beta", None, "holder", None),
        ],
    )
    assert [sql.split()[0] for sql in wrapped.statements] == ["DELETE", "INSERT"]
    assert not wrapped.batches
    assert conn.execute(
        'SELECT entity_name, weight, "role", valid_from FROM hyper_incidences ORDER BY 1'
    ).fetchall() == [
        ("Alpha", 0.5, "promoter", __import__("datetime").date(2025, 1, 1)),
        ("Beta", None, "holder", None),
    ]


def test_org_membership_duckdb_call_site_is_batched():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(":memory:")
    _schema(conn)
    conn.execute(
        "INSERT INTO hyper_edges (id, edge_type, label, source_ref, properties) "
        "VALUES (7, 'org_membership', 'ACME', 'seed', '{}')"
    )
    conn.execute(
        "INSERT INTO hyper_incidences (edge_id, entity_name, role, valid_from) "
        "VALUES (7, 'Old', 'holder', '2024-01-01')"
    )
    wrapped = _CountingConn(conn)
    stats = org_apply(
        wrapped,
        {
            "ACME": {
                "Alpha": {
                    "weight": 12.5,
                    "role": "promoter",
                    "valid_from": "2025-01-01",
                },
                "Beta": {"weight": None, "role": "holder", "valid_from": None},
            }
        },
        dry_run=False,
    )
    assert stats["new_edges"] == 0
    # One edge UPDATE, one staged DELETE + one staged INSERT SELECT.
    assert len(_writes(wrapped)) == 3
    assert not wrapped.batches
    assert conn.execute(
        'SELECT entity_name, "role" FROM hyper_incidences ORDER BY 1'
    ).fetchall() == [("Alpha", "promoter"), ("Beta", "holder")]

    # Converged rerun: same two incidence statements, no per-edge loop.
    wrapped.statements.clear()
    org_apply(
        wrapped,
        {
            "ACME": {
                "Alpha": {
                    "weight": 12.5,
                    "role": "promoter",
                    "valid_from": "2025-01-01",
                },
                "Beta": {"weight": None, "role": "holder", "valid_from": None},
            }
        },
        dry_run=False,
    )
    assert len(_writes(wrapped)) == 3  # edge UPDATE + DELETE + INSERT
    assert not wrapped.batches


def test_merger_duckdb_call_site_is_batched():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect(":memory:")
    _schema(conn)
    wrapped = _CountingConn(conn)
    facts = [
        {
            "edge_type": "merger",
            "label": "Alpha + Beta -> Gamma",
            "members": [("Alpha", "constituent"), ("Beta", "constituent"), ("Gamma", "resultant")],
            "properties": {"note": "pin"},
        }
    ]
    stats = merger_apply(wrapped, facts, dry_run=False)
    assert stats["facts"] == 1 and stats["members"] == 3
    assert not wrapped.batches
    assert [sql.split()[0] for sql in _writes(wrapped)] == [
        "INSERT",  # provenance agent (once)
        "INSERT",  # hyper_edges
        "DELETE",  # staged incidence replace
        "INSERT",  # staged incidence insert
    ]
    assert conn.execute(
        'SELECT entity_name, "role" FROM hyper_incidences ORDER BY 1'
    ).fetchall() == [
        ("Alpha", "constituent"),
        ("Beta", "constituent"),
        ("Gamma", "resultant"),
    ]
