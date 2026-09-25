"""Tests for Wikidata suppression inputs to semantic-peer generation."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.maintenance.enrich_relations import _active_qids


def test_active_qids_only_reads_active_wikidata_mappings() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE concept_mappings (source_scheme TEXT, source_concept TEXT, "
        "target_scheme TEXT, target_concept TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO concept_mappings VALUES (?, ?, ?, ?, ?)",
        [
            ("findata:entity", "Alpha", "wikidata:qid", "Q1", "active"),
            ("findata:entity", "Beta", "wikidata:qid", "Q2", "candidate"),
            ("other", "Gamma", "wikidata:qid", "Q3", "active"),
        ],
    )
    assert _active_qids(conn) == {"Alpha": "Q1"}
    conn.close()
