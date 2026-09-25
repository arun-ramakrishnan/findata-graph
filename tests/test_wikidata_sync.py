"""Tests for the Wikidata QID sidecar resolver."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.maintenance import wikidata_sync as ws
from helpers.misc.seed_concepts import ensure_schema


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (name TEXT, entity_type TEXT, ticker TEXT, cin TEXT)")
    conn.execute("CREATE TABLE quotes (entity TEXT)")
    conn.execute("CREATE TABLE company_embeddings (company_name TEXT)")
    conn.execute(
        "INSERT INTO entities VALUES ('Alpha Limited', 'company', 'ALPHA', 'INE000000001')"
    )
    conn.execute("INSERT INTO quotes VALUES ('Alpha Limited')")
    conn.execute("INSERT INTO company_embeddings VALUES ('Beta Limited')")
    conn.commit()
    return conn


def test_candidates_dedupe_quoted_and_embedded() -> None:
    conn = _db()
    rows = ws.candidate_entities(conn)
    assert [row["entity_name"] for row in rows] == ["Alpha Limited", "Beta Limited"]
    conn.close()


def test_resolve_requires_unique_corroborated_result() -> None:
    conn = _db()

    def search(subject: dict[str, str | None]) -> list[dict[str, str]]:
        return [{"id": "Q1", "label": "Alpha Limited", "description": "company"}]

    def fetch(qid: str) -> dict[str, object]:
        return {"claims": {"P1": [{"mainsnak": {"datavalue": {"value": "ALPHA"}}}]}}

    rows = ws.build_rows(conn, search=search, fetch_entity=fetch, limit=1)
    assert rows == [
        {
            "entity_name": "Alpha Limited",
            "qid": "Q1",
            "label": "Alpha Limited",
            "description": "company",
            "match_type": "exactMatch",
            "confidence": "high",
            "corroboration": "identifier_claim",
            "query": "ticker=ALPHA;cin=INE000000001",
            "fetched_at": ws._today(),
        }
    ]
    conn.close()


def test_uncorroborated_result_is_close_match() -> None:
    conn = _db()

    def search(subject: dict[str, str | None]) -> list[dict[str, str]]:
        return [{"id": "Q1", "label": "Alpha Limited"}]

    def fetch(qid: str) -> dict[str, object]:
        return {}

    rows = ws.build_rows(conn, search=search, fetch_entity=fetch, limit=1)
    assert rows[0]["match_type"] == "closeMatch"
    assert rows[0]["corroboration"] == "none"
    conn.close()


def test_ambiguous_results_stay_close_match() -> None:
    conn = _db()

    def search(name: str) -> list[dict[str, str]]:
        return [
            {"id": "Q1", "label": "Alpha Limited"},
            {"id": "Q2", "label": "Alpha Limited"},
        ]

    def fetch(qid: str) -> dict[str, object]:
        return {"claims": {"P1": [{"mainsnak": {"datavalue": {"value": "ALPHA"}}}]}}

    rows = ws.build_rows(conn, search=search, fetch_entity=fetch, limit=1)
    assert rows[0]["match_type"] == "closeMatch"
    conn.close()


def test_converge_rows_preserves_exact_and_candidate_states() -> None:
    conn = _db()
    ensure_schema(conn)
    rows = [
        {"entity_name": "Alpha Limited", "qid": "Q1", "match_type": "exactMatch"},
        {"entity_name": "Beta Limited", "qid": "Q2", "match_type": "closeMatch"},
    ]
    assert ws.converge_rows(conn, rows, apply=False) == {"exact": 1, "close": 1, "changed": 2}
    assert ws.converge_rows(conn, rows, apply=True) == {"exact": 1, "close": 1, "changed": 2}
    assert ws.converge_rows(conn, rows, apply=True) == {"exact": 1, "close": 1, "changed": 2}
    states = dict(
        conn.execute(
            "SELECT source_concept, status FROM concept_mappings "
            "WHERE source_ref='wikidata:qid-sync'"
        )
    )
    assert states == {"Alpha Limited": "active", "Beta Limited": "candidate"}
    conn.close()
