"""Speaker census — S1 of person_ontology_spine (report-only).

The census is the load-bearing artifact of the arc: it is what the operator
reviews before S2 is allowed to link ``quotes`` to person entities. These
tests pin the three policies it must never invent for itself — spelling
folds, the legal-line never-merge boundary, and company-shaped routing —
plus the report-only contract (no database writes).
"""

from __future__ import annotations

import sqlite3

import pytest

from helpers.core.db import connect
from helpers.graph.speaker_census import check, speaker_census, write_plan

_DDL = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    file_path TEXT,
    sector_classification TEXT,
    ticker TEXT,
    last_updated DATETIME
);
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY,
    entity TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    paraphrase TEXT,
    speaker_name TEXT,
    speaker_title TEXT,
    as_of_edition TEXT,
    source_ref TEXT NOT NULL,
    properties TEXT NOT NULL DEFAULT '{}',
    created_at DATETIME,
    agent_id TEXT,
    source_tier TEXT,
    UNIQUE(entity, quote_text, as_of_edition)
);
"""

_QUOTES = [
    # B Thiagarajan: two spellings, three quotes -> ONE cluster.
    ("B Thiagarajan", "MD", "Ed 1"),
    ("B. Thiagarajan", "MD", "Ed 2"),
    ("b thiagarajan", "Managing Director", "Ed 3"),
    # Registry hit: exact base key of an existing person entity.
    ("Asha Rao", "CFO", "Ed 1"),
    # Legal-line boundary: trust vs person -> never_merge, never a fold.
    ("Deepak Agarwal", "Promoter", "Ed 1"),
    ("Deepak Agarwal Trust", "Trustee", "Ed 2"),
    # Company-shaped attribution.
    ("Camshafts Limited", None, "Ed 1"),
    # Singleton speaker.
    ("Zeta Menon", "Analyst", "Ed 1"),
    # NULL speaker rows are invisible to the census.
    (None, None, "Ed 1"),
    (None, "Analyst", "Ed 2"),
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
                ("Asha Rao", "person"),
                ("Some Co", "company"),
            ],
        )
        con.executemany(
            "INSERT INTO quotes (entity, quote_text, speaker_name, speaker_title, "
            "as_of_edition, source_ref) VALUES (?, ?, ?, ?, ?, 'test')",
            [
                ("Some Co", f"quote {i}", name, title, ed)
                for i, (name, title, ed) in enumerate(_QUOTES)
            ],
        )
        con.commit()
    finally:
        con.close()
    return path


def _census(db):
    con = connect(db)
    try:
        return speaker_census(con)
    finally:
        con.close()


def test_spelling_variants_fold_into_one_cluster(db):
    cen = _census(db)
    by_canon = {c["canonical"]: c for c in cen["clusters"]}
    assert "B Thiagarajan" in by_canon
    cluster = by_canon["B Thiagarajan"]
    assert cluster["variants"] == ["B Thiagarajan", "B. Thiagarajan", "b thiagarajan"]
    assert cluster["quotes"] == 3
    assert cen["stats"]["multi_spelling_clusters"] == 1


def test_one_cluster_per_identity(db):
    cen = _census(db)
    # B Thiagarajan (x3), Asha Rao, Deepak Agarwal, Deepak Agarwal Trust,
    # Camshafts Limited, Zeta Menon == 6 identities.
    assert cen["stats"]["clusters"] == 6
    # 8 named rows: 3 + 1 + 1 + 1 + 1 + 1
    assert cen["stats"]["speaker_strings"] == 8
    assert cen["stats"]["quote_rows"] == 8


def test_null_speakers_are_ignored(db):
    cen = _census(db)
    assert all(c["canonical"] for c in cen["clusters"])
    assert cen["stats"]["quote_rows"] == 8  # the two NULL rows are not counted


def test_registry_match_is_by_base_key(db):
    cen = _census(db)
    asha = next(c for c in cen["clusters"] if c["canonical"] == "Asha Rao")
    assert asha["entity"] == "Asha Rao"
    # Everything else is an S2 candidate, not a match.
    assert cen["stats"]["matched_entity"] == 1
    assert cen["stats"]["unmatched_entity"] == 5


def test_legal_lines_never_merge(db):
    cen = _census(db)
    assert len(cen["never_merge"]) == 1
    nm = cen["never_merge"][0]
    assert sorted(nm["lines"]) == ["person", "trust"]
    assert sorted(nm["clusters"]) == ["Deepak Agarwal", "Deepak Agarwal Trust"]
    # and the two never sit in one fold cluster
    folded = [c for c in cen["clusters"] if len(c["variants"]) > 1]
    assert len(folded) == 1
    assert "Deepak Agarwal Trust" not in folded[0]["variants"]


def test_company_shaped_attribution_is_routed_not_folded(db):
    cen = _census(db)
    assert len(cen["company_shaped"]) == 1
    assert cen["company_shaped"][0]["canonical"] == "Camshafts Limited"


def test_titles_are_evidence_only(db):
    """A drifting title must never split a speaker into two identities."""
    cen = _census(db)
    cluster = next(c for c in cen["clusters"] if c["canonical"] == "B Thiagarajan")
    assert len(cen["clusters"]) == 6
    assert "MD" in cluster["titles"]
    assert cluster["quotes"] == 3


def test_plan_artifacts_are_written_and_reviewable(db, tmp_path):
    cen = _census(db)
    path = write_plan(cen, tmp_path / "plan.txt")
    text = path.read_text(encoding="utf-8")
    assert "REPORT-ONLY" in text
    assert "== NO entity (S2 candidate)" in text
    assert "never_merge" not in text.lower() or "NEVER-MERGE" in text
    assert "Deepak Agarwal Trust" in text
    jsonl = path.with_suffix(".jsonl")
    assert jsonl.exists()
    rows = [line for line in jsonl.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 6
    import json

    parsed = [json.loads(r) for r in rows]
    assert all("canonical" in r and "quotes" in r for r in parsed)


def test_census_is_report_only(db):
    """The slice's contract: no writes, no schema change."""
    con = connect(db)
    try:
        before = con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        speaker_census(con)
        after = con.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        cols = [r[1] for r in con.execute("PRAGMA table_info(quotes)")]
    finally:
        con.close()
    assert before == after == 10
    assert "speaker_entity" not in cols


def test_check_gate_reports_without_baseline(db, tmp_path):
    con = connect(db)
    try:
        assert check(con, tmp_path / "absent.txt") == 0
    finally:
        con.close()


def test_check_gate_fails_when_a_new_spelling_appears(db, tmp_path):
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("1\n", encoding="utf-8")  # 2 regressions present now
    con = connect(db)
    try:
        assert check(con, baseline) == 1
    finally:
        con.close()


def test_check_gate_passes_at_baseline(db, tmp_path):
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("2\n", encoding="utf-8")  # one fold + one company-shaped
    con = connect(db)
    try:
        assert check(con, baseline) == 0
    finally:
        con.close()


def test_empty_corpus_is_safe(tmp_path):
    path = tmp_path / "empty.db"
    con = sqlite3.connect(str(path))
    con.executescript(_DDL)
    con.commit()
    con.close()
    cen = _census(path)
    assert cen["stats"]["clusters"] == 0
    assert cen["never_merge"] == []
    assert cen["company_shaped"] == []
