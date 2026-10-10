"""Edge-convention hygiene (range review S5/M3) — the ONE symmetric set.

Before S5, three writers carried three different ideas of which
graph_edges types are symmetric: the extractor's sidecar promotion used an
8-type set (including directional invested_in / exposed_to / cited_in),
triage accepted with a 5-type set, and derive_cited_in pinned symmetric=0.
The same accepted relation promoted via different ingresses landed with
opposite flags, and UNIQUE(source, target, edge_type) cannot dedupe
reversed pairs, so contradictory duplicate rows accumulated (6 found
2026-10-11). These tests pin the shared home
(helpers/graph/edge_conventions.py) and prove every writer uses it.
"""

from __future__ import annotations

import inspect
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.graph import extract_relations as ex  # noqa: E402
from helpers.graph import triage_pending_relations as tri  # noqa: E402
from helpers.graph.edge_conventions import SYMMETRIC_EDGE_TYPES, canonical_pair  # noqa: E402


def test_symmetric_set_is_exactly_the_five():
    """The operator ruling (2026-10-11): invested_in / exposed_to / cited_in
    are directional (all 3,136 live rows carried symmetric=0), so the shared
    set is exactly the symmetric five."""
    assert SYMMETRIC_EDGE_TYPES == frozenset(
        {
            "competes_with",
            "jv_with",
            "same_group",
            "co_mentioned_in",
            "semantic_peer",
        }
    )
    for directional in ("invested_in", "exposed_to", "cited_in"):
        assert directional not in SYMMETRIC_EDGE_TYPES


def test_triage_uses_the_shared_set():
    """The triage accept lane must ALIAS the shared constant, not carry its
    own copy (the old forking is exactly how M3 happened)."""
    assert tri._SYMMETRIC_ACCEPT_TYPES is SYMMETRIC_EDGE_TYPES


def test_extractor_promote_uses_the_shared_set():
    """The sidecar-promotion path must source its symmetric set from the
    shared home (the old function-local 8-type literal is retired)."""
    src = inspect.getsource(ex.promote_relation_sidecars)
    assert "_SYMMETRIC_TYPES = SYMMETRIC_EDGE_TYPES" in src


def test_pattern_table_stays_in_sync_with_the_shared_set():
    """Hygiene for future pattern additions: any prose pattern that declares
    itself symmetric must be for a type in the shared set — a new symmetric
    pattern for a directional type (or vice versa) fails here, not in the
    data six months later."""
    for pattern in ex.PATTERNS:
        edge_type, sym = pattern[1], pattern[2]
        if sym:
            assert edge_type in SYMMETRIC_EDGE_TYPES, (
                f"pattern for {edge_type!r} declares symmetric=True but the "
                "type is not in SYMMETRIC_EDGE_TYPES"
            )


def test_cited_in_writes_follow_the_shared_set(tmp_path: Path):
    """derive_cited_in must DERIVE its flag from the shared set (not pin a
    bare 0): cited_in is directional today, so rows land symmetric=0 — and
    if the convention ever changes, this test fails instead of the data."""
    from helpers.graph import derive_cited_in

    db = tmp_path / "g.db"
    con = sqlite3.connect(str(db))
    con.execute(
        """
        CREATE TABLE graph_edges (
            source VARCHAR, target VARCHAR, edge_type VARCHAR,
            properties VARCHAR, source_ref VARCHAR,
            symmetric BOOLEAN DEFAULT FALSE, valid_from DATE,
            weight REAL,
            UNIQUE(source, target, edge_type)
        )
        """
    )
    con.commit()
    inserted = derive_cited_in.apply_edges(
        [("a", "b", {"n_quotes": 2}, "test"), ("c", "a", {}, "test")],
        conn=con,
        dry_run=False,
    )
    assert inserted == 2
    rows = con.execute(
        "SELECT source, target, symmetric FROM graph_edges ORDER BY source"
    ).fetchall()
    assert rows == [("a", "b", 0), ("c", "a", 0)]
    con.close()
    # and the derivation is honest: the flag literally comes from the set
    src = inspect.getsource(derive_cited_in.apply_edges)
    assert "EDGE_TYPE in SYMMETRIC_EDGE_TYPES" in src


def test_canonical_pair_sorts_only_symmetric_writes():
    """Symmetric writes collapse ('A','B') and ('B','A') onto one UNIQUE
    row; the helper is the single place that picks the order."""
    assert canonical_pair("Zeta", "Alpha") == ("Alpha", "Zeta")
    assert canonical_pair("Alpha", "Zeta") == ("Alpha", "Zeta")
    assert canonical_pair("same", "same") == ("same", "same")


def test_triage_write_canonicalizes_symmetric_accepts(tmp_path: Path):
    """End-to-end at the triage writer: a symmetric accept stores the sorted
    pair; a directional accept keeps the literal direction."""
    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    con.execute(
        """
        CREATE TABLE graph_edges (
            source VARCHAR, target VARCHAR, edge_type VARCHAR,
            properties VARCHAR, source_ref VARCHAR,
            symmetric BOOLEAN DEFAULT FALSE, valid_from DATE,
            UNIQUE(source, target, edge_type)
        )
        """
    )
    con.commit()
    con.close()
    orig = tri.EDGE_DB_PATH
    tri.EDGE_DB_PATH = str(db)
    try:
        tri._write_accepted_edges(
            [
                {
                    "source": "Zeta Corp",
                    "target": "Alpha Ltd",
                    "edge_type": "jv_with",
                    "symmetric": True,
                    "properties": {"origin": "test"},
                },
                {
                    "source": "Investor X",
                    "target": "Target Co",
                    "edge_type": "invested_in",
                    "symmetric": False,
                    "properties": {"origin": "test"},
                },
            ]
        )
    finally:
        tri.EDGE_DB_PATH = orig
    rows = {
        (r[0], r[1]): r[2]
        for r in sqlite3.connect(str(db)).execute(
            "SELECT source, target, edge_type FROM graph_edges"
        )
    }
    assert ("Alpha Ltd", "Zeta Corp") in rows, "symmetric accept must store the sorted pair"
    assert rows[("Alpha Ltd", "Zeta Corp")] == "jv_with"
    assert ("Investor X", "Target Co") in rows, "directional accept keeps the literal direction"
    assert rows[("Investor X", "Target Co")] == "invested_in"


def test_no_writer_redeclares_a_local_symmetric_set():
    """The retired literals must stay retired: no writer module re-declares
    its own symmetric frozenset — the whole point of the one home."""
    for mod, name in ((ex, "_SYMMETRIC_TYPES"), (tri, "_SYMMETRIC_ACCEPT_TYPES")):
        value = getattr(mod, name, None)
        if value is not None:
            assert value is SYMMETRIC_EDGE_TYPES, (
                f"{mod.__name__}.{name} is not the shared constant"
            )
        src = inspect.getsource(mod)
        assert not re.search(rf"{name}\s*=\s*frozenset", src), (
            f"{mod.__name__} re-declares {name} as a literal"
        )
