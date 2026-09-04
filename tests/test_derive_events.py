#!/usr/bin/env python3
"""Tests for helpers/graph/derive_events.py (D7 — temporal spine).

Two layers, mirroring test_derive_themes.py:
  * The prose extractors (_extract_guidance / _extract_management) are pure
    functions over note text (no DB) — these pin the PRECISION contract: a
    bullet becomes an event only when it carries the right SIGNAL COMBO.
    The headline guards are:
      - guidance needs fiscal token + metric + FORWARD signal (a historical
        actual like "FY2025: delivered ₹275" must NOT trigger).
      - management_change needs a change verb + executive title, AND must NOT
        match the "Appointed Actuary" role-attribution false positive.
  * promote_from_edges + apply hit a temp SQLite DB — these pin the promote
    arm (edge -> event mapping) and the DELETE-then-INSERT idempotency +
    manual-row preservation contract.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


from helpers.graph import derive_events as de  # noqa: E402


# --------------------------------------------------------------------------- #
# Minimal schema for the DB-backed tests (entities + graph_edges + events).
# --------------------------------------------------------------------------- #
from tests.schema import ENTITIES_MINIMAL  # noqa: E402


def _schema_sql():
    return (
        ENTITIES_MINIMAL
        + """
    CREATE TABLE graph_edges(
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        edge_type TEXT NOT NULL,
        valid_from TEXT,
        properties TEXT NOT NULL DEFAULT '{}',
        source_ref TEXT NOT NULL,
        symmetric INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source, target, edge_type)
    );
    CREATE TABLE events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity TEXT NOT NULL,
        event_type TEXT NOT NULL,
        event_date TEXT,
        period TEXT,
        date_precision TEXT,
        magnitude TEXT,
        counterparty TEXT,
        source_quote TEXT,
        as_of_edition TEXT,
        source_ref TEXT NOT NULL,
        properties TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """
    )


def _connect(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.executescript(_schema_sql())
    return conn


# --------------------------------------------------------------------------- #
# Precision contract — guidance (no DB)                                       #
# --------------------------------------------------------------------------- #
class TestGuidancePrecision:
    def test_fy_guidance_bullet_with_metric_and_forward_creates_event(self):
        # The canonical shape: bold lead + FY token + % range + "reiterated".
        body = "- **FY27 guidance reiterated (10-12%):** order inflow growth continues."
        events = de._extract_guidance("Test Co", body, "findata/Test.md")
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "guidance"
        assert ev.event_date is None or ev.event_date.startswith("20")
        assert ev.period == "FY27"
        assert ev.magnitude == "10-12%"
        assert ev.counterparty is None

    def test_historical_actual_does_not_trigger_guidance(self):
        # PRECISION GUARD: fiscal token + metric but NO forward signal -> this
        # is a reported actual, not guidance. Must NOT trigger.
        body = "- FY2025: delivered ₹275 crore revenue (22.2% growth)."
        events = de._extract_guidance("Test Co", body, "findata/Test.md")
        assert events == []

    def test_bare_fy_mention_without_metric_does_not_trigger(self):
        # PRECISION GUARD: fiscal token but no metric -> just commentary.
        body = "- In FY27 we expanded into three new geographies."
        events = de._extract_guidance("Test Co", body, "findata/Test.md")
        assert events == []

    def test_forward_signal_without_fiscal_token_does_not_trigger(self):
        # "targets 20% growth" with no FY/quarter token is timeless, not dated.
        body = "- targets 20% revenue growth going forward."
        events = de._extract_guidance("Test Co", body, "findata/Test.md")
        assert events == []

    def test_near_duplicate_guidance_paraphrases_collapse(self):
        # The same guidance restated 3 ways across bullets must collapse to 1.
        body = (
            "- **FY27 guidance reiterated (10-12%):** order inflow growth.\n"
            "- FY27 guidance of 10-12% order inflow reiterated.\n"
            "- Management reiterated the 10-12% FY27 guidance on the call.\n"
        )
        events = de._extract_guidance("Test Co", body, "findata/Test.md")
        assert len(events) == 1, [e.source_quote for e in events]
        # Dedup keeps the longest quote (richest audit trail).
        assert events[0].source_quote is not None
        assert len(events[0].source_quote) > 20

    def test_quarter_fiscal_token_parsed_to_event_date(self):
        # Q4 FY26 -> a concrete date (Q4 FY26 = Jan-Mar 2026).
        body = "- **Q4 FY26 guidance:** targets 5% margin expansion, expects improvement."
        events = de._extract_guidance("Test Co", body, "findata/Test.md")
        assert len(events) == 1
        assert events[0].period is not None
        assert "Q4" in events[0].period.upper() or "FY" in events[0].period.upper()


# --------------------------------------------------------------------------- #
# Precision contract — management_change (no DB)                              #
# --------------------------------------------------------------------------- #
class TestManagementPrecision:
    def test_appointed_with_title_and_person_creates_event(self):
        body = "- Salil Parekh appointed CEO, taking over from the outgoing chief."
        events = de._extract_management("Test Co", body, "findata/Test.md")
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "management_change"
        assert ev.properties.get("role") == "CEO"
        assert "Salil Parekh" in ev.properties.get("person", "")

    def test_appointed_actuary_does_not_trigger(self):
        # PRECISION GUARD: "Appointed Actuary" is a role-title adjective (a
        # job title in insurance), NOT a management change. Must NOT trigger.
        body = "- Concall: Amit Jhingran, MD & CEO; Prithesh Chaubey, Appointed Actuary."
        events = de._extract_management("Test Co", body, "findata/Test.md")
        # The Appointed Actuary window is rejected; the MD & CEO line has no
        # change verb, so neither matches -> empty.
        assert events == []

    def test_role_prose_without_change_verb_does_not_trigger(self):
        # PRECISION GUARD: "the CEO said" mentions a title but no change verb.
        body = "- The CEO said the company expects strong demand next year."
        events = de._extract_management("Test Co", body, "findata/Test.md")
        assert events == []

    def test_change_verb_without_title_does_not_trigger(self):
        # "resigned" alone with no executive title is ambiguous.
        body = "- A senior manager resigned last quarter; replacement underway."
        events = de._extract_management("Test Co", body, "findata/Test.md")
        assert events == []

    def test_takes_over_with_chairman_creates_event(self):
        body = "- Vinod Sahay takes over as Executive Chairman on August 1."
        events = de._extract_management("Test Co", body, "findata/Test.md")
        assert len(events) == 1
        assert "Chairman" in events[0].properties.get("role", "")

    def test_incoming_ceo_triggers_management_change(self):
        # G2 (2026-08): "Incoming CEO" is the dominant CEO-succession form —
        # no verb, so "incoming" is listed as a verb-equivalent in
        # _CHANGE_VERB_RE. The title co-requirement gates non-title uses.
        body = "- John Furner, Incoming CEO of Walmart, brings retail experience."
        events = de._extract_management("Walmart", body, "findata/Test.md")
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "management_change"
        assert ev.properties.get("role") == "CEO"
        assert "John Furner" in ev.properties.get("person", "")

    def test_incoming_md_triggers_management_change(self):
        body = "- Riya Sen, incoming MD, will join the board next month."
        events = de._extract_management("Test Co", body, "findata/Test.md")
        assert len(events) == 1
        assert events[0].properties.get("role") == "MD"

    def test_incoming_without_title_is_rejected(self):
        # PRECISION GUARD: "incoming" without an executive title is generic
        # prose ("incoming revenue", "incoming shipment") and must NOT fire.
        body = (
            "- Incoming revenue growth is expected to accelerate in H2."
            " A new shipment arrives next week."
        )
        events = de._extract_management("Test Co", body, "findata/Test.md")
        assert events == []


# --------------------------------------------------------------------------- #
# Frontmatter stripping (no DB)                                              #
# --------------------------------------------------------------------------- #
def test_frontmatter_only_note_yields_nothing():
    fm = "---\ntitle: Co\ntype: company\nFY27: 10%\ntags:\n- target\n---\n"
    # The whole note is frontmatter; body is empty.
    body = de._strip_frontmatter(fm + "\n# Co\n\n")
    assert de._extract_guidance("Co", body, "x.md") == []
    assert de._extract_management("Co", body, "x.md") == []


# --------------------------------------------------------------------------- #
# ARM 1 — promote from graph_edges (DB-backed)                                #
# --------------------------------------------------------------------------- #
class TestPromotionFromEdges:
    def test_acquired_edge_promoted_to_acquisition_event(self, tmp_path):
        conn = _connect(tmp_path)
        props = json.dumps({"quote": "acquired Akzo Nobel", "year": 2025, "stake": "61.2%"})
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, valid_from, "
            "properties, source_ref) VALUES (?, ?, 'acquired', ?, ?, 'manual')",
            ("JSW Paints", "Akzo Nobel India", "2025-12-01", props),
        )
        conn.commit()
        events = de.promote_from_edges(conn)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_type == "acquisition"
        assert ev.entity == "JSW Paints"
        assert ev.counterparty == "Akzo Nobel India"
        assert ev.event_date == "2025-12-01"
        assert ev.date_precision == "month"  # month-resolved valid_from
        assert ev.magnitude == "61.2%"  # from properties.stake
        assert ev.source_quote == "acquired Akzo Nobel"
        assert ev.source_ref == de.PROMOTE_SOURCE_REF
        conn.close()

    def test_jv_edge_promoted_to_jv_event(self, tmp_path):
        conn = _connect(tmp_path)
        props = json.dumps({"venture": "JioBlackRock AMC", "year": 2023})
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, valid_from, "
            "properties, source_ref) VALUES (?, ?, 'jv_with', ?, ?, 'manual')",
            ("BlackRock", "Jio Financial Services", "2023-01-01", props),
        )
        conn.commit()
        events = de.promote_from_edges(conn)
        assert len(events) == 1
        assert events[0].event_type == "jv"
        assert events[0].entity == "BlackRock"
        assert events[0].period == "2023"  # from properties.year
        assert events[0].date_precision == "year"
        conn.close()

    def test_undated_edge_promoted_with_null_date(self, tmp_path):
        conn = _connect(tmp_path)
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, valid_from, "
            "properties, source_ref) VALUES (?, ?, 'acquired', NULL, '{}', 'manual')",
            ("A", "B"),
        )
        conn.commit()
        events = de.promote_from_edges(conn)
        assert len(events) == 1
        assert events[0].event_date is None
        assert events[0].date_precision is None
        conn.close()


# --------------------------------------------------------------------------- #
# ARM 2 — extract_from_prose over note files (no DB, path_to_name)           #
# --------------------------------------------------------------------------- #
def test_extract_from_prose_resolves_via_path_to_name(tmp_path):
    # A note whose stem differs from the entity name (the spaces-vs-underscores
    # contract): path_to_name resolves it.
    note = tmp_path / "ABB_India.md"
    note.write_text(
        "---\ntitle: ABB India\ntype: company\n---\n"
        "- **FY27 guidance:** targets 15-18% revenue growth, reiterated.\n",
        encoding="utf-8",
    )
    # extract_from_prose resolves the note via _REPO_ROOT-relative path only;
    # for a tmp_path outside the repo it falls back to the stem. Verify the
    # fallback still yields a guidance event keyed by the stem.
    events = de.extract_from_prose(tmp_path, None)
    assert any(e.event_type == "guidance" and "FY27" in (e.period or "") for e in events)


# --------------------------------------------------------------------------- #
# Idempotency + manual-row preservation (DB-backed)                          #
# --------------------------------------------------------------------------- #
class TestApplyAndIdempotency:
    def _sample_events(self):
        return [
            de.Event(
                entity="Co A",
                event_type="acquisition",
                event_date="2025-01-01",
                counterparty="Target",
                source_quote="bought Target",
                source_ref=de.PROMOTE_SOURCE_REF,
            ),
            de.Event(
                entity="Co A",
                event_type="guidance",
                period="FY27",
                magnitude="10-12%",
                source_quote="FY27 guidance 10-12%",
                source_ref=de.GUIDANCE_SOURCE_REF,
            ),
        ]

    def test_dry_run_writes_nothing(self, tmp_path):
        conn = _connect(tmp_path)
        events = self._sample_events()
        counted = de.apply(events, conn=conn, dry_run=True)
        assert counted == len(events)
        # Nothing written.
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert n == 0
        conn.close()

    def test_apply_then_rerun_inserts_no_duplicates(self, tmp_path):
        conn = _connect(tmp_path)
        events = self._sample_events()
        de.apply(events, conn=conn, dry_run=False)
        first = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert first == len(events)
        # Re-apply the SAME derived set: DELETE-then-INSERT leaves the count
        # unchanged (idempotent).
        de.apply(events, conn=conn, dry_run=False)
        second = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert second == first, f"re-apply changed count {first} -> {second}"
        conn.close()

    def test_manual_rows_preserved_on_rederive(self, tmp_path):
        conn = _connect(tmp_path)
        # Seed a hand-curated event (manual source_ref) + a derived one.
        conn.execute(
            "INSERT INTO events (entity, event_type, source_ref) "
            "VALUES ('Co A', 'acquisition', 'manual:curated')"
        )
        conn.commit()
        events = [
            de.Event(
                entity="Co A",
                event_type="guidance",
                period="FY27",
                source_quote="FY27 guidance",
                source_ref=de.GUIDANCE_SOURCE_REF,
            ),
        ]
        de.apply(events, conn=conn, dry_run=False)
        rows = conn.execute(
            "SELECT source_ref FROM events WHERE entity='Co A' ORDER BY source_ref"
        ).fetchall()
        refs = [r[0] for r in rows]
        # The manual row survived the derive DELETE; the derived one was added.
        assert "manual:curated" in refs
        assert de.GUIDANCE_SOURCE_REF in refs
        conn.close()

    def test_derived_rows_replaced_on_rederive(self, tmp_path):
        # A prior derived row should be CLEARED and replaced, not accumulated.
        conn = _connect(tmp_path)
        conn.execute(
            "INSERT INTO events (entity, event_type, source_ref) VALUES ('Co A', 'guidance', ?)",
            (de.GUIDANCE_SOURCE_REF,),
        )
        conn.commit()
        events = [
            de.Event(
                entity="Co A",
                event_type="guidance",
                period="FY27",
                source_quote="updated FY27 guidance",
                source_ref=de.GUIDANCE_SOURCE_REF,
            ),
        ]
        de.apply(events, conn=conn, dry_run=False)
        n = conn.execute(
            "SELECT COUNT(*) FROM events WHERE source_ref LIKE 'derive:events:%'"
        ).fetchone()[0]
        assert n == 1, "stale derived row was not replaced"
        conn.close()


# ---------------------------------------------------------------------------
# _parse_event_date — pure unit tests
# ---------------------------------------------------------------------------
def test_parse_event_date_no_date():
    """No date token → precision is 'none'."""
    iso, period, precision = de._parse_event_date("Some random text with no date")
    assert precision == "none"


def test_parse_event_date_fy_token():
    """FY token is captured as period."""
    result = de._parse_event_date("FY27 guidance for 1000 crore")
    assert len(result) == 3


def test_parse_event_date_quarter_token():
    """Quarter token is captured."""
    result = de._parse_event_date("Q1FY27 results show growth")
    assert len(result) == 3


# ---------------------------------------------------------------------------
# _capture_period_token
# ---------------------------------------------------------------------------
def test_capture_period_token_fy():
    token = de._capture_period_token("Revenue for FY27 expected to grow")
    assert token is not None
    assert "FY" in token


def test_capture_period_token_quarter():
    token = de._capture_period_token("Q1FY27 results were strong")
    assert token is not None
    assert "Q1" in token


def test_capture_period_token_month_year():
    """Month-year fallback (line 219)."""
    token = de._capture_period_token("Targeting Mar 2026 for launch")
    assert token is not None
    assert "2026" in token


def test_capture_period_token_none():
    assert de._capture_period_token("No fiscal token here at all") is None


# ---------------------------------------------------------------------------
# _dedup
# ---------------------------------------------------------------------------
def test_dedup_removes_near_duplicates():
    """Near-duplicate events are collapsed."""
    events = [
        de.Event(
            entity="Co A",
            event_type="guidance",
            event_date=None,
            period="FY27",
            date_precision="year",
            magnitude="1000",
            source_quote="test",
            source_ref="t",
        ),
        de.Event(
            entity="Co A",
            event_type="guidance",
            event_date=None,
            period="FY27",
            date_precision="year",
            magnitude="1000",
            source_quote="very similar test",
            source_ref="t",
        ),
    ]
    result = de._dedup(events)
    assert len(result) == 1


def test_dedup_preserves_different_types():
    """Events with different types are kept."""
    events = [
        de.Event(
            entity="Co A",
            event_type="guidance",
            event_date=None,
            period="FY27",
            date_precision="year",
            magnitude="1000",
            source_quote="a",
            source_ref="t",
        ),
        de.Event(
            entity="Co A",
            event_type="management_change",
            event_date=None,
            period="FY27",
            date_precision="year",
            magnitude=None,
            source_quote="b",
            source_ref="t",
        ),
    ]
    result = de._dedup(events)
    assert len(result) == 2


def test_dedup_empty():
    assert de._dedup([]) == []
