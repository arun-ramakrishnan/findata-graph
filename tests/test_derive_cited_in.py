#!/usr/bin/env python3
"""Tests for helpers/graph/derive_cited_in.py (okf_activation P).

Two layers, mirroring test_derive_themes.py:
  * edition_notes / extract_citations / derive_edges are pure functions
    over a tmp vault — these pin the projection contract (stems as entity
    names, PDF/unknown-id skipping, n_quotes enrichment, stem-collision
    failure).
  * create_edition_entities / apply_edges hit a tmp SQLite DB — these pin
    idempotency (INSERT OR IGNORE / UNIQUE constraint) and the
    non-edition collision guard.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from helpers.graph import derive_cited_in as dc  # noqa: E402


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
        source_ref TEXT,
        symmetric INTEGER DEFAULT 0,
        UNIQUE(source, target, edge_type)
    );
    CREATE TABLE quotes(
        entity TEXT,
        as_of_edition TEXT
    );
    """


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "findata"
    chatter = vault / "The_Chatter"
    chatter.mkdir(parents=True)
    (chatter / "Edition_One.md").write_text(
        "---\ntitle: The Chatter: Edition One\ntype: newsletter\n---\n"
        "# Edition One\n", encoding="utf-8")
    (chatter / "Edition_Two.md").write_text(
        "---\ntitle: The Chatter: Edition Two\ntype: newsletter\n---\n"
        "# Edition Two\n", encoding="utf-8")
    (chatter / "image_map.md").write_text("chrome\n", encoding="utf-8")
    cos = vault / "Companies" / "Agri"
    cos.mkdir(parents=True)
    (cos / "Alpha_Co.md").write_text(
        "---\ntype: company\ntitle: Alpha Co\nsources:\n"
        "- id: Edition_One\n"
        "  resource: /findata/The_Chatter/Edition_One.md\n"
        "  title: 'The Chatter: Edition One'\n"
        "  last_modified: '2026-08-15'\n"
        "---\n# Alpha\n", encoding="utf-8")
    (cos / "Beta_Co.md").write_text(
        "---\ntype: company\ntitle: Beta Co\nsources:\n"
        "- id: Edition_One\n"
        "  resource: /findata/The_Chatter/Edition_One.md\n"
        "  last_modified: '2026-08-15'\n"
        "- id: not_a_stem\n"
        "  resource: /Reports/x.pdf\n"
        "  last_modified: '2026-08-15'\n"
        "---\n# Beta\n", encoding="utf-8")
    (vault / "Sectors").mkdir()
    (vault / "Sectors" / "Agri_Sector.md").write_text(
        "---\ntype: sector\ntitle: Agri\nsources:\n"
        "- id: Edition_Two\n"
        "  resource: /findata/The_Chatter/Edition_Two.md\n"
        "  title: 'The Chatter: Edition Two'\n"
        "  last_modified: '2026-08-15'\n"
        "---\n# Agri\n", encoding="utf-8")
    return vault


PATH_TO_NAME = {
    "findata/Companies/Agri/Alpha_Co.md": "Alpha Co",
    "findata/Companies/Agri/Beta_Co.md": "Beta Co",
    "findata/Sectors/Agri_Sector.md": "Agri Sector",
}


def test_edition_notes_index_and_chrome_skip(tmp_path):
    editions = dc.edition_notes(_make_vault(tmp_path))
    assert [(e["stem"], e["file_path"]) for e in editions] == [
        ("Edition_One", "findata/The_Chatter/Edition_One.md"),
        ("Edition_Two", "findata/The_Chatter/Edition_Two.md"),
    ]
    assert editions[0]["title"] == "The Chatter: Edition One"


def test_edition_notes_fail_loudly_on_stem_collision(tmp_path):
    vault = _make_vault(tmp_path)
    (vault / "The_PlotLines").mkdir()
    (vault / "The_PlotLines" / "Edition_One.md").write_text("# dup\n",
                                                            encoding="utf-8")
    with pytest.raises(ValueError, match="edition stem collision"):
        dc.edition_notes(vault)


def test_extract_citations_skips_pdf_and_unknown_ids(tmp_path):
    vault = _make_vault(tmp_path)
    stems = {e["stem"] for e in dc.edition_notes(vault)}
    citations, stats = dc.extract_citations(vault, PATH_TO_NAME, stems)
    assert sorted(citations) == [
        ("Agri Sector", "Edition_Two", "/findata/The_Chatter/Edition_Two.md"),
        ("Alpha Co", "Edition_One", "/findata/The_Chatter/Edition_One.md"),
        ("Beta Co", "Edition_One", "/findata/The_Chatter/Edition_One.md"),
    ]
    assert stats == {"skipped_pdf": 1, "unknown_id": 0}


def test_derive_edges_enrich_with_quote_counts(tmp_path):
    vault = _make_vault(tmp_path)
    stems = {e["stem"] for e in dc.edition_notes(vault)}
    citations, _ = dc.extract_citations(vault, PATH_TO_NAME, stems)
    nq = {("Alpha Co", "Edition_One"): 3}
    edges = dc.derive_edges(citations, nq)
    by_pair = {(s, t): props for s, t, props, _ in edges}
    assert by_pair[("Alpha Co", "Edition_One")]["n_quotes"] == 3
    assert by_pair[("Beta Co", "Edition_One")]["n_quotes"] == 0
    assert by_pair[("Agri Sector", "Edition_Two")]["resource"] == (
        "/findata/The_Chatter/Edition_Two.md")
    assert all(ref == dc.SOURCE_REF for _, _, _, ref in edges)


def test_quote_counts_bridge_via_edition_index(tmp_path):
    vault = _make_vault(tmp_path)
    from helpers.core.edition_index import source_note_index
    con = sqlite3.connect(":memory:")
    con.executescript(_schema_sql())
    con.executemany("INSERT INTO quotes VALUES (?, ?)", [
        ("Alpha Co", "The Chatter — Edition One"),   # variant form resolves
        ("Alpha Co", "Edition One"),
        ("Alpha Co", "Yahoo Finance"),               # unresolvable: no count
    ])
    counts = dc.quote_counts(con, source_note_index(vault))
    assert counts == {("Alpha Co", "Edition_One"): 2}


def test_create_entities_and_edges_idempotent(tmp_path):
    vault = _make_vault(tmp_path)
    editions = dc.edition_notes(vault)
    con = sqlite3.connect(":memory:")
    con.executescript(_schema_sql())
    con.execute("INSERT INTO entities VALUES ('Alpha Co','company',NULL,"
                "'findata/Companies/Agri/Alpha_Co.md',NULL)")
    con.execute("INSERT INTO entities VALUES ('Agri Sector','sector',NULL,"
                "'findata/Sectors/Agri_Sector.md',NULL)")

    n1 = dc.create_edition_entities(con, editions, apply=True)
    assert n1 == 2
    assert con.execute(
        "SELECT name, entity_type, normalized_name, file_path FROM entities "
        "WHERE entity_type='edition' ORDER BY name").fetchall() == [
        ("Edition_One", "edition", "Edition_One",
         "findata/The_Chatter/Edition_One.md"),
        ("Edition_Two", "edition", "Edition_Two",
         "findata/The_Chatter/Edition_Two.md"),
    ]
    # idempotent: everything exists -> 0
    assert dc.create_edition_entities(con, editions, apply=True) == 0

    stems = {e["stem"] for e in editions}
    citations, _ = dc.extract_citations(vault, PATH_TO_NAME, stems)
    edges = dc.derive_edges(citations, {})
    assert dc.apply_edges(edges, conn=con, dry_run=False) == 3
    assert dc.apply_edges(edges, conn=con, dry_run=False) == 0  # UNIQUE
    assert dc.apply_edges(edges, conn=con, dry_run=True) == 0   # dry-run too
    rows = con.execute("SELECT source, target, edge_type, symmetric "
                       "FROM graph_edges ORDER BY source").fetchall()
    assert rows == [
        ("Agri Sector", "Edition_Two", "cited_in", 0),
        ("Alpha Co", "Edition_One", "cited_in", 0),
        ("Beta Co", "Edition_One", "cited_in", 0),
    ]


def test_create_entities_refuses_non_edition_collision(tmp_path):
    vault = _make_vault(tmp_path)
    editions = dc.edition_notes(vault)
    con = sqlite3.connect(":memory:")
    con.executescript(_schema_sql())
    con.execute("INSERT INTO entities VALUES ('Edition_One','company',NULL,"
                "'findata/Companies/Agri/Edition_One.md',NULL)")
    with pytest.raises(RuntimeError, match="collide with existing"):
        dc.create_edition_entities(con, editions, apply=True)
