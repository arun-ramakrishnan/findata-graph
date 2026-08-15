#!/usr/bin/env python3
"""P3 — derive_* chain integration tests.

Verifies the cross-module pipeline:
  prose → extract_relations → edges → derive_events.promote_from_edges → events
  prose → derive_insights.scan → quotes + metrics

Each derive_* module is tested in isolation by existing unit tests, but NO
test verifies the full chain end-to-end.  This suite closes that gap.

See doc/improvements/archive/integration_plan.txt § Priority 3.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from helpers.graph.extract_relations import (
    Edge,
    EntityResolver,
    apply_edges,
    extract_relations,
)
from helpers.graph.derive_events import (
    apply as apply_events,
    promote_from_edges,
)
from helpers.graph.derive_insights import (
    Metric,
    Quote,
    apply_metrics,
    apply_quotes,
    scan,
)

pytestmark = [pytest.mark.integration]

# --------------------------------------------------------------------------- #
# Schema: entities + graph_edges + events + quotes + company_metrics
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY NOT NULL,
    entity_type TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_path TEXT,
    last_updated DATETIME,
    normalized_name TEXT,
    sector_classification TEXT,
    ticker TEXT
);
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type)
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    event_type TEXT,
    event_date TEXT,
    period TEXT,
    date_precision TEXT,
    magnitude TEXT,
    counterparty TEXT,
    source_quote TEXT,
    as_of_edition TEXT,
    source_ref TEXT,
    properties TEXT
);
CREATE TABLE quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    quote_text TEXT,
    paraphrase TEXT,
    speaker_name TEXT,
    speaker_title TEXT,
    as_of_edition TEXT,
    source_ref TEXT,
    properties TEXT
);
CREATE TABLE company_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    metric_label TEXT,
    value_raw TEXT,
    value_num REAL,
    unit TEXT,
    period TEXT,
    as_of_edition TEXT,
    source_quote TEXT,
    source_ref TEXT,
    properties TEXT
);
"""


@pytest.fixture
def p3_db(tmp_path):
    """A fresh SQLite DB with the derive_* schema + seeded entities."""
    db_path = str(tmp_path / "p3_chain.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    # Seed entities
    companies = [
        ("Tata Motors", "company", "Automobiles", "findata/Companies/Automobiles/Tata_Motors.md", "TATAMOTORS"),
        ("JLR", "company", "Automobiles", "findata/Companies/Automobiles/JLR.md", None),
        ("HDFC Bank", "company", "Banking", "findata/Companies/Banking/Hdfc_Bank.md", "HDFCBANK"),
        ("Infosys", "company", "Technology", "findata/Companies/Technology/Infosys.md", "INFY"),
    ]
    for name, etype, sector, fp, ticker in companies:
        conn.execute(
            "INSERT INTO entities(name, entity_type, sector_classification, file_path, "
            "ticker, normalized_name) VALUES (?,?,?,?,?,?)",
            (name, etype, sector, fp, ticker, name.lower()),
        )
    conn.commit()
    yield conn, db_path
    conn.close()


# --------------------------------------------------------------------------- #
# Chain stage 1: extract_relations → apply_edges → graph_edges
# --------------------------------------------------------------------------- #


class TestExtractRelationsAppliesEdges:
    """extract_relations finds acquisition edges in prose; apply_edges writes them."""

    def test_acquisition_prose_creates_edge(self, p3_db):
        conn, db_path = p3_db
        resolver = EntityResolver(["Tata Motors", "JLR", "HDFC Bank", "Infosys"])
        content = """\
# Tata Motors — Concall Notes

## Tata Motors | large cap | Automobiles

Tata Motors acquired JLR in 2008.

## Automobiles

Sector overview.
"""
        edges_by_type, unresolved = extract_relations(
            content,
            edition_title="Test Edition",
            newsletter_type="test_newsletter",
            resolver=resolver,
        )
        # Should find at least one 'acquired' edge
        assert "acquired" in edges_by_type
        acquired_edges = edges_by_type["acquired"]
        assert len(acquired_edges) >= 1
        # The edge should reference Tata Motors and JLR
        sources = {e.source for e in acquired_edges}
        targets = {e.target for e in acquired_edges}
        assert "Tata Motors" in sources or "Tata Motors" in targets

    def test_apply_edges_persists_to_db(self, p3_db):
        conn, db_path = p3_db
        edge = Edge(
            source="Tata Motors",
            target="JLR",
            edge_type="acquired",
            properties={"edition": "Test", "year": 2008, "quote": "acquired JLR in 2008"},
            source_ref="derive:relations:test",
            valid_from="2008-01-01",
        )
        result = apply_edges([edge], conn=conn, dry_run=False)
        assert result.inserted == 1
        # Verify the edge is in the DB
        rows = conn.execute(
            "SELECT source, target, edge_type, valid_from FROM graph_edges"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["source"] == "Tata Motors"
        assert rows[0]["target"] == "JLR"
        assert rows[0]["edge_type"] == "acquired"
        assert rows[0]["valid_from"] == "2008-01-01"

    def test_apply_edges_idempotent(self, p3_db):
        conn, db_path = p3_db
        edge = Edge(
            source="Tata Motors",
            target="JLR",
            edge_type="acquired",
            properties={"edition": "Test"},
            source_ref="derive:relations:test",
        )
        apply_edges([edge], conn=conn, dry_run=False)
        # Re-apply should insert 0 (UNIQUE constraint)
        result = apply_edges([edge], conn=conn, dry_run=False)
        assert result.inserted == 0


# --------------------------------------------------------------------------- #
# Chain stage 2: graph_edges → derive_events.promote_from_edges → events
# --------------------------------------------------------------------------- #


class TestDeriveEventsFromEdges:
    """promote_from_edges reads graph_edges → creates Event objects → apply persists."""

    def test_promote_from_acquired_edge(self, p3_db):
        conn, db_path = p3_db
        # Insert an 'acquired' edge with temporal data
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, valid_from, "
            "properties, source_ref) VALUES (?,?,?,?,?,?)",
            ("Tata Motors", "JLR", "acquired", "2008-01-01",
             json.dumps({"year": 2008, "stake": "100%", "quote": "acquired JLR"}),
             "derive:relations:test"),
        )
        conn.commit()

        events = promote_from_edges(conn)
        assert len(events) == 1
        ev = events[0]
        assert ev.entity == "Tata Motors"
        assert ev.event_type == "acquisition"
        assert ev.counterparty == "JLR"
        assert ev.event_date == "2008-01-01"
        assert ev.date_precision == "year"
        assert "100%" in (ev.magnitude or "")

    def test_promote_from_jv_edge(self, p3_db):
        conn, db_path = p3_db
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, valid_from, "
            "properties, source_ref) VALUES (?,?,?,?,?,?)",
            ("HDFC Bank", "Infosys", "jv_with", "2023-06-15",
             json.dumps({"stake": "50%"}),
             "derive:relations:test"),
        )
        conn.commit()

        events = promote_from_edges(conn)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "jv"
        assert ev.counterparty == "Infosys"
        assert ev.event_date == "2023-06-15"
        assert ev.date_precision == "month"  # 06-15 → month precision

    def test_apply_events_persists(self, p3_db):
        conn, db_path = p3_db
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, valid_from, "
            "properties, source_ref) VALUES (?,?,?,?,?,?)",
            ("Tata Motors", "JLR", "acquired", "2008-01-01",
             json.dumps({"year": 2008}),
             "derive:relations:test"),
        )
        conn.commit()

        events = promote_from_edges(conn)
        inserted = apply_events(events, conn=conn, dry_run=False)
        assert inserted >= 1

        rows = conn.execute(
            "SELECT entity, event_type, event_date, counterparty FROM events"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["entity"] == "Tata Motors"
        assert rows[0]["event_type"] == "acquisition"
        assert rows[0]["event_date"] == "2008-01-01"
        assert rows[0]["counterparty"] == "JLR"

    def test_apply_events_idempotent(self, p3_db):
        conn, db_path = p3_db
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, valid_from, "
            "properties, source_ref) VALUES (?,?,?,?,?,?)",
            ("Tata Motors", "JLR", "acquired", "2008-01-01",
             json.dumps({"year": 2008}),
             "derive:relations:test"),
        )
        conn.commit()

        events = promote_from_edges(conn)
        apply_events(events, conn=conn, dry_run=False)
        # Re-apply: DELETE-then-INSERT, so count stays 1
        apply_events(events, conn=conn, dry_run=False)
        rows = conn.execute("SELECT * FROM events").fetchall()
        assert len(rows) == 1


# --------------------------------------------------------------------------- #
# Chain stage 3: derive_insights.scan → quotes + metrics
# --------------------------------------------------------------------------- #


class TestDeriveInsightsFromProse:
    """scan() reads newsletter files and extracts quotes + metrics."""

    def test_scan_extracts_quotes_and_metrics(self, p3_db, tmp_path):
        conn, db_path = p3_db
        # Write a synthetic newsletter file with a company section
        newsletter = tmp_path / "Test_Edition.md"
        newsletter.write_text("""\
# Test Edition

## HDFC Bank | large cap | Banking

## [Concall]

Revenue growth was strong this quarter.

"We are seeing excellent momentum in our loan book with 15% YoY growth."

— Sashi Jagdishan, CEO

The bank reported net interest income of ₹45,000 crore in FY26.
""", encoding="utf-8")

        quotes, metrics = scan(str(newsletter), conn)
        # Should find the verbatim quote
        assert len(quotes) >= 1
        q = quotes[0]
        assert q.entity == "HDFC Bank"
        assert "momentum" in q.quote_text.lower()
        assert q.speaker_name == "Sashi Jagdishan"
        # Should find the metric (₹45,000 crore)
        assert len(metrics) >= 1
        # At least one metric should mention crore
        values = [m.value_raw for m in metrics]
        assert any("crore" in v.lower() or "₹" in v for v in values)

    def test_apply_quotes_persists(self, p3_db, tmp_path):
        conn, db_path = p3_db
        q = Quote(
            entity="HDFC Bank",
            quote_text="Test quote text that is long enough to pass the threshold.",
            paraphrase="Management expressed optimism.",
            speaker_name="John Doe",
            speaker_title="CFO",
            as_of_edition="Test Edition",
            source_ref="derive:quotes:test:1",
        )
        inserted = apply_quotes([q], conn=conn, dry_run=False)
        assert inserted == 1
        rows = conn.execute("SELECT entity, quote_text, speaker_name FROM quotes").fetchall()
        assert len(rows) == 1
        assert rows[0]["entity"] == "HDFC Bank"
        assert rows[0]["speaker_name"] == "John Doe"

    def test_apply_metrics_persists(self, p3_db, tmp_path):
        conn, db_path = p3_db
        m = Metric(
            entity="HDFC Bank",
            value_raw="₹45,000 crore",
            metric_label="revenue",
            value_num=45000.0,
            unit="crore",
            period="FY26",
            as_of_edition="Test Edition",
            source_quote="Net interest income of ₹45,000 crore in FY26.",
            source_ref="derive:metrics:test:1",
        )
        inserted = apply_metrics([m], conn=conn, dry_run=False)
        assert inserted == 1
        rows = conn.execute("SELECT entity, value_raw, unit, period FROM company_metrics").fetchall()
        assert len(rows) == 1
        assert rows[0]["entity"] == "HDFC Bank"
        assert rows[0]["unit"] == "crore"
        assert rows[0]["period"] == "FY26"


# --------------------------------------------------------------------------- #
# Full chain: prose → edges → events (end-to-end)
# --------------------------------------------------------------------------- #


class TestFullChainProseToEvents:
    """End-to-end: extract_relations → apply_edges → promote_from_edges → apply_events."""

    def test_acquisition_prose_to_events(self, p3_db):
        conn, db_path = p3_db

        # Stage 1: Extract relations from prose
        resolver = EntityResolver(["Tata Motors", "JLR", "HDFC Bank", "Infosys"])
        content = """\
# Test Edition

## Tata Motors | large cap | Automobiles

Tata Motors acquired JLR in 2008.
"""
        edges_by_type, _ = extract_relations(
            content,
            edition_title="Test Edition",
            newsletter_type="test_newsletter",
            resolver=resolver,
        )

        # Flatten all edges
        all_edges: list[Edge] = []
        for etype, elist in edges_by_type.items():
            all_edges.extend(elist)

        # Stage 2: Apply edges to DB
        if all_edges:
            result = apply_edges(all_edges, conn=conn, dry_run=False)
            assert result.inserted >= 1

        # Stage 3: Promote edges to events
        events = promote_from_edges(conn)
        # The acquired edge should produce an event
        acquisition_events = [e for e in events if e.event_type == "acquisition"]
        assert len(acquisition_events) >= 1

        # Stage 4: Persist events
        if events:
            inserted = apply_events(events, conn=conn, dry_run=False)
            assert inserted >= 1

        # Verify final state
        event_rows = conn.execute(
            "SELECT entity, event_type, counterparty FROM events"
        ).fetchall()
        assert len(event_rows) >= 1
        # At least one should be an acquisition
        acq_rows = [r for r in event_rows if r["event_type"] == "acquisition"]
        assert len(acq_rows) >= 1


class TestFullChainDryRunVsApply:
    """Verify dry-run counts match actual apply counts across the chain."""

    def test_edges_dry_run_matches_apply(self, p3_db):
        conn, db_path = p3_db
        edges = [
            Edge(
                source="HDFC Bank",
                target="Infosys",
                edge_type="jv_with",
                properties={"stake": "50%"},
                source_ref="derive:relations:test",
                symmetric=True,
            ),
        ]
        dry = apply_edges(edges, conn=conn, dry_run=True)
        assert dry.inserted == 1
        real = apply_edges(edges, conn=conn, dry_run=False)
        assert real.inserted == 1
        assert dry.inserted == real.inserted

    def test_events_dry_run_returns_count(self, p3_db):
        conn, db_path = p3_db
        conn.execute(
            "INSERT INTO graph_edges(source, target, edge_type, valid_from, "
            "properties, source_ref) VALUES (?,?,?,?,?,?)",
            ("Tata Motors", "JLR", "acquired", "2008-01-01",
             json.dumps({"year": 2008}),
             "derive:relations:test"),
        )
        conn.commit()

        events = promote_from_edges(conn)
        dry = apply_events(events, conn=conn, dry_run=True)
        assert dry == len(events)
        real = apply_events(events, conn=conn, dry_run=False)
        assert real == len(events)

    def test_quotes_dry_run_returns_count(self, p3_db):
        conn, db_path = p3_db
        quotes = [Quote(entity="X", quote_text="y" * 50)]
        dry = apply_quotes(quotes, conn=conn, dry_run=True)
        assert dry == len(quotes)

    def test_metrics_dry_run_returns_count(self, p3_db):
        conn, db_path = p3_db
        metrics = [Metric(entity="X", value_raw="₹1,000 crore")]
        dry = apply_metrics(metrics, conn=conn, dry_run=True)
        assert dry == len(metrics)
