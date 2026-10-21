"""S2 of person_ontology_spine — the speaker alias map and person minting.

The contracts pinned here are the ones the ontology depends on:

1. **The map is additive** — building it writes no entity and no quote row,
   so ``--apply`` is safe to run at any time.
2. **Resolution is many-to-one at the speaker** — every variant spelling
   points at its cluster canonical, and the view resolves quotes with zero
   per-quote writes.
3. **Minting is explicit and bounded** — ``--min-quotes`` decides the
   population, singletons stay strings, and the two refusal classes
   (company-shaped attribution, name already held by another entity type)
   are reported rather than force-merged. The ``entities.name`` PK makes a
   collision unrepresentable; silently renaming a company to fit a speaker
   would be the worst possible resolution.
4. **Re-running is idempotent** — map rows upsert, an existing
   ``person_name`` link survives, and minting is ``INSERT OR IGNORE``.
"""

from __future__ import annotations

import sqlite3

import pytest

from helpers.core.db import connect
from helpers.graph.speaker_resolution import ensure_schema, report, resolve

_DDL = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    normalized_name TEXT,
    file_path TEXT,
    created_at DATETIME
);
CREATE TABLE quotes (
    id            INTEGER PRIMARY KEY,
    entity        TEXT NOT NULL,
    quote_text    TEXT NOT NULL,
    speaker_name  TEXT,
    speaker_title TEXT,
    as_of_edition TEXT,
    source_ref    TEXT NOT NULL
);
"""

_QUOTES = [
    # 3 quotes, two spellings -> one identity, canonical 'Asha Rao'
    ("Asha Rao", "CFO"),
    ("Asha Rao", "MD"),
    ("Asha  Rao", "Chief Financial Officer"),
    # 2 quotes -> mintable at >= 2
    ("Vikram Singh", "CEO"),
    ("Vikram Singh", "Managing Director"),
    # 1 quote -> below the default threshold
    ("Solo Speaker", "Analyst"),
    # company-shaped attribution -> refused, never a person
    ("Camshafts Limited", "Company"),
    ("Camshafts Limited", "Company"),
    # name already held by a company entity -> refused, never force-merged
    ("Rakesh Sharma", "Director"),
    ("Rakesh Sharma", "Director"),
    # speakerless rows must not enter the map
    (None, None),
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "research.db"
    con = connect(path)
    try:
        con.executescript(_DDL)
        con.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
            [
                ("Asha Rao", "person"),  # already in the registry -> linked
                ("Rakesh Sharma", "company"),  # PK collision -> refused
            ],
        )
        for i, (speaker, title) in enumerate(_QUOTES, start=1):
            con.execute(
                "INSERT INTO quotes (id, entity, quote_text, speaker_name, "
                "speaker_title, as_of_edition, source_ref) "
                "VALUES (?, 'Acme Ltd', ?, ?, ?, 'Ed 1', 'test')",
                (i, f"quote {i}", speaker, title),
            )
        con.commit()
    finally:
        con.close()
    return path


def _map_rows(path):
    con = sqlite3.connect(str(path))
    try:
        return con.execute(
            "SELECT alias_name, canonical_name, person_name FROM speaker_identity "
            "ORDER BY alias_name"
        ).fetchall()
    finally:
        con.close()


def _persons(path):
    con = sqlite3.connect(str(path))
    try:
        return sorted(
            r[0] for r in con.execute("SELECT name FROM entities WHERE entity_type='person'")
        )
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Schema + view
# --------------------------------------------------------------------------- #
def test_ensure_schema_creates_map_and_view(db):
    con = connect(db)
    try:
        ensure_schema(con)
        ensure_schema(con)  # idempotent
        names = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
        assert {"speaker_identity", "v_quote_speaker"} <= names
        assert con.execute("SELECT COUNT(*) FROM speaker_identity").fetchone()[0] == 0
    finally:
        con.close()


def test_view_resolves_quotes_without_writes(db):
    con = connect(db)
    try:
        ensure_schema(con)
        con.execute(
            "INSERT INTO speaker_identity (alias_name, canonical_name, person_name, source_ref) "
            "VALUES ('Asha  Rao', 'Asha Rao', 'Asha Rao', 'test')"
        )
        con.commit()
        rows = con.execute(
            "SELECT alias_name, canonical_name, person_name FROM v_quote_speaker WHERE quote_id = 3"
        ).fetchone()
        assert tuple(rows) == ("Asha  Rao", "Asha Rao", "Asha Rao")
        # an unmapped alias is NULL, not an error
        assert (
            con.execute("SELECT canonical_name FROM v_quote_speaker WHERE quote_id = 1").fetchone()[
                0
            ]
            is None
        )
        # speakerless rows ride the view too
        assert con.execute("SELECT COUNT(*) FROM v_quote_speaker").fetchone()[0] == len(_QUOTES)
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Dry run is the default
# --------------------------------------------------------------------------- #
def test_dry_run_writes_nothing(db):
    stats = resolve(db)
    assert stats["aliases"] == 6  # 5 clusters; the Asha pair folds to 2 aliases
    assert stats["clusters"] == 5
    assert stats["linked_person"] == 2  # both Asha spellings -> one person
    assert _map_rows(db) == []
    # only the pre-existing person entity — nothing minted
    assert _persons(db) == ["Asha Rao"]


def test_dry_run_reports_refusals(db, capsys):
    resolve(db)
    out = capsys.readouterr().out
    assert "Camshafts Limited — company-shaped" in out
    assert "Rakesh Sharma — name taken by another entity" in out


# --------------------------------------------------------------------------- #
# --apply: the map only
# --------------------------------------------------------------------------- #
def test_apply_writes_map_without_minting(db):
    resolve(db, apply=True)
    rows = dict((a, (c, p)) for a, c, p in _map_rows(db))
    # both spellings collapse onto one canonical
    assert rows["Asha Rao"] == ("Asha Rao", "Asha Rao")
    assert rows["Asha  Rao"] == ("Asha Rao", "Asha Rao")
    # an unresolved cluster still has a canonical spelling, person NULL
    assert rows["Vikram Singh"] == ("Vikram Singh", None)
    assert rows["Camshafts Limited"] == ("Camshafts Limited", None)
    # no entity was created
    assert _persons(db) == ["Asha Rao"]


def test_apply_is_idempotent_and_keeps_person_link(db):
    resolve(db, apply=True)
    first = _map_rows(db)
    resolve(db, apply=True)
    assert _map_rows(db) == first


# --------------------------------------------------------------------------- #
# --mint: explicit population change
# --------------------------------------------------------------------------- #
def test_mint_default_threshold_mints_two_plus_quotes(db):
    stats = resolve(db, mint=True)
    assert stats["mintable"] == 1  # Vikram Singh (2 quotes)
    assert stats["mintable_quotes"] == 2
    assert _persons(db) == ["Asha Rao", "Vikram Singh"]
    rows = dict((a, (c, p)) for a, c, p in _map_rows(db))
    assert rows["Vikram Singh"] == ("Vikram Singh", "Vikram Singh")
    # singletons stay strings and stay unlinked
    assert rows["Solo Speaker"] == ("Solo Speaker", None)


def test_mint_respects_min_quotes_one(db):
    resolve(db, mint=True, min_quotes=1)
    assert _persons(db) == ["Asha Rao", "Solo Speaker", "Vikram Singh"]


def test_mint_respects_min_quotes_three(db):
    stats = resolve(db, mint=True, min_quotes=3)
    assert stats["mintable"] == 0
    assert _persons(db) == ["Asha Rao"]


def test_mint_never_touches_collision_or_company_shaped(db):
    resolve(db, mint=True)
    persons = _persons(db)
    assert "Rakesh Sharma" not in persons  # still the company
    assert "Camshafts Limited" not in persons
    con = sqlite3.connect(str(db))
    try:
        assert (
            con.execute("SELECT entity_type FROM entities WHERE name='Rakesh Sharma'").fetchone()[0]
            == "company"
        )
    finally:
        con.close()


def test_mint_is_idempotent(db):
    resolve(db, mint=True)
    resolve(db, mint=True)
    assert _persons(db) == ["Asha Rao", "Vikram Singh"]
    assert len(_map_rows(db)) == 6


def test_minted_person_is_pathless_and_normalised(db):
    resolve(db, mint=True)
    con = sqlite3.connect(str(db))
    try:
        row = con.execute(
            "SELECT entity_type, normalized_name, file_path FROM entities WHERE name='Vikram Singh'"
        ).fetchone()
    finally:
        con.close()
    # SHP convention: normalized_name is the UPPERCASED key
    assert row == ("person", "VIKRAM_SINGH", None)


# --------------------------------------------------------------------------- #
# Coverage report
# --------------------------------------------------------------------------- #
def test_report_counts_resolution_coverage(db):
    assert report(db)["quotes_resolved"] == 0
    resolve(db, mint=True)
    rep = report(db)
    assert rep["aliases"] == 6
    # 3 Asha quotes + 2 Vikram quotes resolve to a person
    assert rep["quotes_with_speaker"] == len([q for q in _QUOTES if q[0]])
    assert rep["quotes_resolved"] == 5
    assert rep["linked_aliases"] == 3


def test_report_on_pre_s2_db_is_zero(tmp_path):
    path = tmp_path / "old.db"
    con = connect(path)
    try:
        con.executescript(_DDL)
        con.commit()
    finally:
        con.close()
    # no speaker_identity table at all
    assert report(path)["aliases"] == 0


def test_empty_corpus_is_safe(tmp_path):
    path = tmp_path / "empty.db"
    con = connect(path)
    try:
        con.executescript(_DDL)
        con.commit()
    finally:
        con.close()
    stats = resolve(path, mint=True)
    assert stats["aliases"] == 0 and stats["mintable"] == 0
