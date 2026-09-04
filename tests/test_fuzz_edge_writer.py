"""Fuzz tests for the shared derive-* edge writer
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §5 B7 —
this file was a 0-byte placeholder for helpers/graph/_edge_writer.py).

Properties run against a tmp SQLite DB with the production graph_edges
DDL (UNIQUE(source, target, edge_type) + CHECK(source != target)):
upsert idempotence, dry-run/apply parity, the CHECK guard, and the
documented NO-swap-dedup semantics (pair canonicalisation is the
CALLER's job — derive_co_mentions.derive_edges sorts each pair).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


from helpers.graph._edge_writer import apply_typed_edges  # noqa: E402

_NAME = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r"),
    min_size=1,
    max_size=20,
).filter(lambda s: s.strip())

_SETTINGS = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_DDL = """
CREATE TABLE graph_edges (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    properties  TEXT NOT NULL DEFAULT '{}',
    valid_from  DATE,
    valid_to    DATE,
    source_ref  TEXT NOT NULL,
    symmetric   INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
"""


@pytest.fixture(scope="module")
def db_path():
    fd, name = tempfile.mkstemp(suffix=".db")
    import os

    os.close(fd)
    Path(name).unlink()
    conn = sqlite3.connect(name)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return Path(name)


def _conn(db):
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    return c


@_SETTINGS
@given(
    st.lists(st.tuples(_NAME, _NAME), min_size=0, max_size=8, unique=True),
    st.sampled_from([("theme_of", 0), ("co_mentioned_in", 1)]),
)
def test_apply_idempotent_and_dry_run_parity(db_path, pairs, type_sym):
    edge_type, symmetric = type_sym
    pairs = [(a, b) for a, b in pairs if a != b]
    edges = [(a, b, {"k": "v"}, "fuzz") for a, b in pairs]
    c = _conn(db_path)
    try:
        c.execute("DELETE FROM graph_edges")
        c.commit()
        would = apply_typed_edges(
            edges, edge_type=edge_type, symmetric=symmetric, conn=c, dry_run=True
        )
        assert would == len(pairs)
        assert c.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 0
        first = apply_typed_edges(
            edges, edge_type=edge_type, symmetric=symmetric, conn=c, dry_run=False
        )
        assert first == len(pairs)
        # Re-apply: UNIQUE(source, target, edge_type) swallows everything.
        second = apply_typed_edges(
            edges, edge_type=edge_type, symmetric=symmetric, conn=c, dry_run=False
        )
        assert second == 0
        # Dry-run after apply also reports zero.
        again = apply_typed_edges(
            edges, edge_type=edge_type, symmetric=symmetric, conn=c, dry_run=True
        )
        assert again == 0
    finally:
        c.close()


@_SETTINGS
@given(st.lists(st.tuples(_NAME, _NAME), min_size=1, max_size=6, unique=True))
def test_self_edges_never_written(db_path, pairs):
    """CHECK(source != target) + INSERT OR IGNORE: self-edges are silently
    dropped on apply — the count and the table agree."""
    edge_type = "theme_of"
    edges = [(a, a, {}, "fuzz") for a, _ in pairs]
    c = _conn(db_path)
    try:
        c.execute("DELETE FROM graph_edges")
        c.commit()
        n = apply_typed_edges(edges, edge_type=edge_type, symmetric=0, conn=c, dry_run=False)
        assert n == 0
        assert (
            c.execute("SELECT COUNT(*) FROM graph_edges WHERE source = target").fetchone()[0] == 0
        )
    finally:
        c.close()


def test_no_swap_dedup_by_the_writer_itself(db_path):
    """Characterisation: the writer does NOT canonicalise (source, target)
    — (A, B) and (B, A) are distinct UNIQUE keys and both insert. Pair
    canonicalisation is the CALLER's contract (derive_co_mentions sorts
    each pair before delegating). If this ever changes, update the
    callers that rely on pass-through ordering."""
    c = _conn(db_path)
    try:
        c.execute("DELETE FROM graph_edges")
        c.commit()
        n1 = apply_typed_edges(
            [("A", "B", {}, "fuzz")], edge_type="t", symmetric=1, conn=c, dry_run=False
        )
        n2 = apply_typed_edges(
            [("B", "A", {}, "fuzz")], edge_type="t", symmetric=1, conn=c, dry_run=False
        )
        assert (n1, n2) == (1, 1)
        assert c.execute("SELECT COUNT(*) FROM graph_edges WHERE edge_type='t'").fetchone()[0] == 2
    finally:
        c.close()
