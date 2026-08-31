#!/usr/bin/env python3
"""Tests for helpers/graph/derive_themes.py (D4 — theme extraction).

Two layers:
  * extract_theme_membership + derive_edges are pure functions over note text
    (no DB) — these pin the PRECISION contract: a note gets a theme only when a
    narrow alias matches, not on boilerplate (bare "pli" must NOT trigger PLI).
  * apply_edges + create_theme_entities hit a temp SQLite DB — these pin
    idempotency (INSERT OR IGNORE) and the theme-entity creation contract.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.graph import derive_themes as dt  # noqa: E402


# --------------------------------------------------------------------------- #
# Minimal schema for the DB-backed tests (matches graph_edges + entities).
# --------------------------------------------------------------------------- #
def _schema_sql():
    return """
    CREATE TABLE entities(
        name TEXT PRIMARY KEY,
        entity_type TEXT,
        normalized_name TEXT,
        file_path TEXT,
        last_updated TEXT
    );
    CREATE TABLE graph_edges(
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        edge_type TEXT NOT NULL,
        properties TEXT NOT NULL DEFAULT '{}',
        source_ref TEXT NOT NULL,
        symmetric INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source, target, edge_type)
    );
    """


def _write_note(tmp_path: Path, stem: str, body: str) -> Path:
    """Write a company note (frontmatter + body) under tmp_path."""
    p = tmp_path / f"{stem}.md"
    p.write_text(f"---\ntitle: {stem}\ntype: company\n---\n{body}", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Precision contract (no DB)                                                   #
# --------------------------------------------------------------------------- #
class TestExtractionPrecision:
    """The alias map is deliberately NARROW. These tests pin that narrowness so
    a future broadening (e.g. adding bare 'pli') doesn't silently fan out."""

    def test_pli_scheme_alias_triggers_edge(self, tmp_path):
        _write_note(
            tmp_path, "Acme_Electronics", "Acme is a beneficiary of the PLI scheme for components."
        )
        edges = dt.derive_edges(dt.extract_theme_membership(tmp_path))
        targets = {e[1] for e in edges}
        assert "PLI_Scheme" in targets

    def test_bare_pli_does_not_trigger_edge(self, tmp_path):
        """Bare 'pli' is boilerplate (appears in 42/42 sector notes) and must
        NOT create a PLI edge — this is the precision guard."""
        _write_note(
            tmp_path, "Acme_Chemicals", "The company filed its PLI. No scheme detail available."
        )
        edges = dt.derive_edges(dt.extract_theme_membership(tmp_path))
        targets = {e[1] for e in edges}
        assert "PLI_Scheme" not in targets, (
            "bare 'pli' should not trigger PLI_Scheme (precision guard)"
        )

    def test_china_plus_one_variants_match(self, tmp_path):
        _write_note(tmp_path, "Acme_Textiles", "Benefiting from the China+1 diversification trend.")
        edges = dt.derive_edges(dt.extract_theme_membership(tmp_path))
        assert any(e[0] == "Acme_Textiles" and e[1] == "China_Plus_One" for e in edges)

    def test_multiple_themes_one_note(self, tmp_path):
        """A company exposed to several themes yields one edge per theme."""
        _write_note(
            tmp_path, "Acme_Auto", "EV transition + premiumization + China plus one strategy."
        )
        edges = dt.derive_edges(dt.extract_theme_membership(tmp_path))
        themes = {e[1] for e in edges if e[0] == "Acme_Auto"}
        assert {"EV_Transition", "Premiumization", "China_Plus_One"} <= themes

    def test_matched_aliases_recorded_in_properties(self, tmp_path):
        """The properties dict carries which aliases matched — the audit trail
        for precision review."""
        _write_note(tmp_path, "Acme", "Strong EV adoption and ev strategy.")
        edges = dt.derive_edges(dt.extract_theme_membership(tmp_path))
        ev = [e for e in edges if e[1] == "EV_Transition"][0]
        props = ev[2]
        assert "matched_aliases" in props
        assert set(props["matched_aliases"]) <= {"ev adoption", "ev strategy"}

    def test_frontmatter_only_note_yields_nothing(self, tmp_path):
        """Themes live in prose, not frontmatter. A note whose only theme
        mention is inside the YAML block must not match (frontmatter is
        stripped before scanning)."""
        p = tmp_path / "Acme.md"
        p.write_text(
            "---\ntitle: Acme\ntype: company\ninvestment_theme: ev_transition\n---\nJust a stub.\n"
        )
        edges = dt.derive_edges(dt.extract_theme_membership(tmp_path))
        assert edges == []


# --------------------------------------------------------------------------- #
# DB-backed: entity creation + idempotent edge apply                           #
# --------------------------------------------------------------------------- #
class TestApplyAndIdempotency:
    def test_create_theme_entities_is_idempotent(self, tmp_path):
        conn = sqlite3.connect(":memory:")
        conn.executescript(_schema_sql())
        try:
            n1 = dt.create_theme_entities(conn, apply=True)
            assert n1 == len(dt.CANONICAL_THEMES)  # all canonical themes inserted (12 after widen)
            n2 = dt.create_theme_entities(conn, apply=True)
            assert n2 == 0  # already present (INSERT OR IGNORE)
            assert conn.execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type='theme'"
            ).fetchone()[0] == len(dt.CANONICAL_THEMES)
        finally:
            conn.close()

    def test_apply_edges_then_rerun_inserts_no_duplicates(self, tmp_path):
        """Idempotency via UNIQUE(source, target, edge_type): re-applying the
        same edges must not duplicate rows."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(_schema_sql())
        try:
            edges = [("Acme", "PLI_Scheme", {"matched_aliases": ["pli scheme"]}, "test")]
            n1 = dt.apply_edges(edges, conn=conn, dry_run=False)
            assert n1 == 1
            # Re-apply the same edge — UNIQUE constraint skips it.
            n2 = dt.apply_edges(edges, conn=conn, dry_run=False)
            assert n2 == 0
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type='exposed_to'"
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_dry_run_writes_nothing(self, tmp_path):
        conn = sqlite3.connect(":memory:")
        conn.executescript(_schema_sql())
        try:
            edges = [("Acme", "PLI_Scheme", {"matched_aliases": ["pli scheme"]}, "test")]
            n = dt.apply_edges(edges, conn=conn, dry_run=True)
            assert n == 1  # would insert
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type='exposed_to'"
            ).fetchone()[0]
            assert count == 0  # nothing written
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# extract_theme_membership — edge cases
# ---------------------------------------------------------------------------
def test_unreadable_file_skipped(tmp_path):
    """A file that raises OSError is silently skipped (line 174-175)."""
    import os

    bad = tmp_path / "bad.md"
    bad.write_text("# ok")
    os.chmod(str(bad), 0o000)
    try:
        results = list(dt.extract_theme_membership(tmp_path))
        assert results == []
    finally:
        os.chmod(str(bad), 0o644)


def test_path_to_name_missing_file_skipped(tmp_path):
    """A note not in path_to_name is skipped (line 192)."""
    note = tmp_path / "co.md"
    note.write_text("Some text about PLI scheme")
    results = list(dt.extract_theme_membership(tmp_path, {"other/path.md": "Other Co"}))
    assert results == []


def test_empty_scan_text_skipped(tmp_path):
    """A note with only frontmatter and no body yields nothing (line 197)."""
    note = tmp_path / "co.md"
    note.write_text("---\ntitle: Empty\n---\n")
    results = list(dt.extract_theme_membership(tmp_path))
    assert results == []


def test_derive_edges_multiple_themes():
    """derive_edges produces one edge per (company, theme) from membership."""
    membership = [
        ("Co A", "PLI", ["pli"]),
        ("Co A", "China Plus One", ["china plus one"]),
    ]
    edges = list(dt.derive_edges(membership))
    assert len(edges) == 2
    themes = {e[1] for e in edges}
    assert "PLI" in themes
    assert "China Plus One" in themes


def test_derive_edges_empty():
    """Empty membership produces no edges."""
    assert list(dt.derive_edges([])) == []
