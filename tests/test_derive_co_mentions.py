#!/usr/bin/env python3
"""Tests for helpers/graph/derive_co_mentions.py (Slice C, Graph Phase 2).

Two layers:
- Pure-function tests (extract regex behaviour, pair generation, idempotency
  on a synthetic in-memory DB) run without any external dependency.
- Live extraction tests (marked ``live``) read the real ``findata/`` vault
  and ``memory/research.db`` — skipped if either is missing.

Run:
    pytest tests/test_derive_co_mentions.py -v
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from helpers.graph.derive_co_mentions import (  # noqa: E402
    apply_edges,
    derive_edges,
    extract_co_mentions,
)
from helpers.graph import derive_co_mentions as dcm  # noqa: E402

LIVE_DB = REPO_ROOT / "memory" / "research.db"
LIVE_VAULT = REPO_ROOT / "findata" / "Companies"


# --------------------------------------------------------------------------- #
# Pure-function tests (no DB / no vault)                                       #
# --------------------------------------------------------------------------- #
class TestDeriveEdges:
    def test_alphabetical_canonical(self):
        edges = derive_edges({"Edition X": ["B", "A", "C"]})
        # Each pair emitted with source <= target.
        pairs = [(s, t) for s, t, _p, _r in edges]
        assert pairs == [("A", "B"), ("A", "C"), ("B", "C")]

    def test_properties_carry_edition_and_newsletter(self):
        edges = derive_edges({"Edition X": ["A", "B"]}, newsletter_type="The_Chatter")
        assert len(edges) == 1
        source, target, props, source_ref = edges[0]
        assert (source, target) == ("A", "B")
        assert props["edition"] == "Edition X"
        assert props["newsletter"] == "The_Chatter"
        # No edition number in this title -> key absent.
        assert "edition_number" not in props
        assert source_ref == "derive:co_mentioned:The_Chatter"

    def test_edition_number_extracted(self):
        edges = derive_edges({"Edition #52 (Mar 27, 2026)": ["A", "B"]})
        _, _, props, _ = edges[0]
        assert props["edition_number"] == 52

    def test_skips_single_entity_editions(self):
        edges = derive_edges({"Solo Edition": ["OnlyOne"]})
        assert edges == []

    def test_dedups_repeated_entities(self):
        # If the same entity appears twice in the input list, no self-loop and
        # no duplicate edge should be produced.
        edges = derive_edges({"E": ["A", "A", "B"]})
        assert len(edges) == 1
        assert (edges[0][0], edges[0][1]) == ("A", "B")

    def test_multiple_editions_independent(self):
        edges = derive_edges(
            {
                "Edition 1": ["A", "B"],
                "Edition 2": ["A", "C"],
            }
        )
        assert {(s, t, p["edition"]) for s, t, p, _r in edges} == {
            ("A", "B", "Edition 1"),
            ("A", "C", "Edition 2"),
        }


# --------------------------------------------------------------------------- #
# Idempotency on a synthetic in-memory DB                                     #
# --------------------------------------------------------------------------- #
def _build_synthetic_db(db_path: Path) -> None:
    """Create a minimal graph_edges + entities schema at db_path."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE entities (
            name TEXT PRIMARY KEY
        );
        CREATE TABLE graph_edges (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL REFERENCES entities(name)
                ON DELETE CASCADE ON UPDATE CASCADE,
            target TEXT NOT NULL REFERENCES entities(name)
                ON DELETE CASCADE ON UPDATE CASCADE,
            edge_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            properties TEXT NOT NULL DEFAULT '{}',
            valid_from DATE,
            valid_to DATE,
            source_ref TEXT NOT NULL,
            symmetric INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, target, edge_type),
            CHECK (source != target)
        );
        """
    )
    conn.executemany(
        "INSERT INTO entities (name) VALUES (?)",
        [("A",), ("B",), ("C",)],
    )
    conn.commit()
    conn.close()


@pytest.fixture
def synthetic_db(tmp_path) -> Path:
    db_path = tmp_path / "test.db"
    _build_synthetic_db(db_path)
    return db_path


class TestApplyEdges:
    def test_apply_inserts_and_counts(self, synthetic_db):
        edges = derive_edges({"Edition X": ["A", "B", "C"]})
        assert len(edges) == 3

        conn = sqlite3.connect(synthetic_db)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            n = apply_edges(edges, conn=conn, dry_run=False)
            assert n == 3
            rows = conn.execute(
                "SELECT source, target, edge_type, symmetric, source_ref "
                "FROM graph_edges ORDER BY source, target"
            ).fetchall()
            assert len(rows) == 3
            for _s, _t, edge_type, symmetric, source_ref in rows:
                assert edge_type == "co_mentioned_in"
                assert symmetric == 1
                assert source_ref == "derive:co_mentioned:The_Chatter"
        finally:
            conn.close()

    def test_apply_is_idempotent(self, synthetic_db):
        """Running insert twice should not duplicate rows."""
        edges = derive_edges({"Edition X": ["A", "B", "C"]})
        conn = sqlite3.connect(synthetic_db)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            first = apply_edges(edges, conn=conn, dry_run=False)
            second = apply_edges(edges, conn=conn, dry_run=False)
            assert first == 3
            assert second == 0  # all skipped via INSERT OR IGNORE
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type='co_mentioned_in'"
            ).fetchone()[0]
            assert count == 3
        finally:
            conn.close()

    def test_dry_run_does_not_write(self, synthetic_db):
        edges = derive_edges({"Edition X": ["A", "B"]})  # 1 pair
        conn = sqlite3.connect(synthetic_db)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            n = apply_edges(edges, conn=conn, dry_run=True)
            assert n == 1  # would insert the single pair
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type='co_mentioned_in'"
            ).fetchone()[0]
            assert count == 0  # nothing actually written
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# Live tests — require the real vault + DB                                    #
# --------------------------------------------------------------------------- #
live = pytest.mark.live
pytestmark = live


@live
class TestExtractLive:
    def setup_method(self):
        if not LIVE_DB.exists() or not LIVE_VAULT.exists():
            pytest.skip("live DB / vault not present")

    def test_extract_finds_edition_groups(self):
        editions = extract_co_mentions("The_Chatter")
        # Plenty of editions across the vault.
        assert len(editions) >= 3
        # Every MULTI-entity edition should have at least 2 entities (single-
        # entity editions are returned but produce no edges downstream).
        multi = {t: ns for t, ns in editions.items() if len(ns) >= 2}
        assert len(multi) >= 3, f"expected >=3 multi-entity editions, got {len(multi)}"

        # Edition #69 enhanced 11 companies; we should see >=10 of them
        # (allowing for any entity that may not yet be registered). The
        # canonical key carries the parenthetical because the bare heading
        # lacks an inline edition number — see _canonicalise_title.
        key = "Jio Financial, Wipro, Polycab, Piramal & More (Edition #69, Q1FY27)"
        assert key in editions, (
            f"expected Edition #69 key in editions; got keys like {sorted(editions)[:3]}..."
        )
        assert len(editions[key]) >= 10, (
            f"expected >=10 co-mentions for Edition #69, got {len(editions[key])}"
        )
        # Sanity: CEAT and Jio Financial Services were both enhanced from #69.
        assert "Jio Financial Services" in editions[key]
        assert "CEAT" in editions[key]

    def test_extract_returns_display_names_that_resolve_as_entities(self):
        # The FK target is entities.name; every name returned must exist there.
        import contextlib

        from helpers.core.db import connect

        editions = extract_co_mentions("The_Chatter")
        names = {n for ns in editions.values() for n in ns}
        assert names, "expected at least one entity name"
        # closing() — sqlite3's `with conn:` only commits/rolls back; the
        # connection itself must be closed or it leaks (ResourceWarning).
        with contextlib.closing(connect()) as conn:
            placeholders = ",".join("?" for _ in names)
            rows = conn.execute(
                f"SELECT name FROM entities WHERE name IN ({placeholders})",  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
                tuple(sorted(names)),
            ).fetchall()
        assert {r["name"] for r in rows} == names

    def test_derive_for_live_edition_69_has_expected_pair_count(self):
        editions = extract_co_mentions("The_Chatter")
        key = "Jio Financial, Wipro, Polycab, Piramal & More (Edition #69, Q1FY27)"
        n = len(editions[key])
        # n*(n-1)/2 edges, with no self-pairs, all source<target.
        edges = [e for e in derive_edges({key: editions[key]})]
        assert len(edges) == n * (n - 1) // 2
        for source, target, _p, _r in edges:
            assert source < target  # canonical alphabetical

    def test_extract_edition_number_parsed(self):
        """For the Edition #69 title, edition_number=69 in derived properties.

        The number lives in the source footer `*Source: The Chatter — ...
        (Edition #69, Q1FY27)*`, not the heading itself. ``extract_co_mentions``
        annotates the canonical title with the footer parenthetical so the
        number is recoverable from the title via ``derive_edges``.
        """
        editions = extract_co_mentions("The_Chatter")
        key = "Jio Financial, Wipro, Polycab, Piramal & More (Edition #69, Q1FY27)"
        assert key in editions
        edges = derive_edges({key: editions[key]})
        assert edges, "expected at least one edge for Edition #69"
        props = edges[0][2]
        assert props.get("edition_number") == 69, (
            f"edition_number not parsed from footer; props={props}"
        )


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------
def test_newsletter_title_raises_for_unknown():
    """_newsletter_title raises ValueError for unknown newsletter type."""
    import pytest

    with pytest.raises(ValueError, match="Unknown newsletter_type"):
        dcm._newsletter_title("nonexistent")


def test_heading_regex_matches_all_types():
    """_heading_regex returns a compiled regex for each newsletter type."""
    for ntype in dcm.NEWSLETTER_TITLES:
        rgx = dcm._heading_regex(ntype)
        assert rgx is not None
        assert hasattr(rgx, "findall")


def test_footer_regex_matches_all_types():
    """_footer_regex returns a compiled regex for each newsletter type."""
    for ntype in dcm.NEWSLETTER_TITLES:
        rgx = dcm._footer_regex(ntype)
        assert rgx is not None


def test_resolve_entity_name_found():
    """_resolve_entity_name returns entity name when file_path matches."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, file_path TEXT)")
    conn.execute("INSERT INTO entities VALUES ('Test Co', 'findata/Companies/T/test-co.md')")
    result = dcm._resolve_entity_name(conn, "findata/Companies/T/test-co.md")
    assert result == "Test Co"
    conn.close()


def test_resolve_entity_name_not_found():
    """_resolve_entity_name returns None when no match."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, file_path TEXT)")
    result = dcm._resolve_entity_name(conn, "findata/Companies/X/missing.md")
    assert result is None
    conn.close()


def test_canonicalise_title_with_suffix():
    """_canonicalise_title appends known suffix."""
    title_map = {"Q3 Earnings": "Q3 FY25"}
    result = dcm._canonicalise_title("Q3 Earnings", title_map)
    assert result == "Q3 Earnings Q3 FY25"


def test_canonicalise_title_no_suffix():
    """_canonicalise_title returns title unchanged when no suffix known."""
    result = dcm._canonicalise_title("Unknown Title", {})
    assert result == "Unknown Title"


def test_parse_edition_number_numeric():
    """_parse_edition_number extracts numbers from Edition #N pattern."""
    assert dcm._parse_edition_number("Edition #42 (Q1FY27)") == 42


def test_parse_edition_number_none():
    """_parse_edition_number returns None when no number found."""
    assert dcm._parse_edition_number("No Number Here") is None
