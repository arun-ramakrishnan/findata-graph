"""Neighbor-bundle endpoint tests — split from the original
test_api_graph.py for navigability.

Unit tests for the neighbor-bundle endpoints: sector market_cap invariant (K2), theme members (D4), and /api/events/<name> (D7).
"""

from __future__ import annotations

import sqlite3


import app as A
from tests.conftest import (  # noqa: E402
    _UNIT_SCHEMA,
)


# --------------------------------------------------------------------------- #
# Sector bundle invariant: sum(market_cap_counts) == member_count             #
# --------------------------------------------------------------------------- #
class TestSectorBundleMarketCapInvariant:
    """The /api/graph/neighbors/<sector> response must be internally consistent:
    the market_cap breakdown must cover exactly the members list.

    Previously `market_cap_counts` came from a separate SQLite query on
    `sector_classification` while `members` came from DuckDB graph edges — two
    independent sources that drift, producing sum(caps) != member_count. The
    fix derives the breakdown from the SAME members set. These tests pin that
    invariant without needing a live DuckDB connection
    (sector_members_with_market_cap is monkeypatched).

    Bundle K2: the route now fetches members + market_cap in ONE DuckDB query
    (sector_members_with_market_cap) and bucketizes in Python — no SQLite hop.
    The invariant these tests pin (sum(buckets) == member_count) is preserved
    by construction since both come from the same result set."""

    def _seed_unit_db(self, db_path):
        """Seed a tiny DB with 3 companies in 'Test' sector, one with no
        market_cap tag (the case that previously broke the invariant —
        pre-C2 this was a NULL column value; post-C2 it's the absence of a
        market_cap/* tag, which materializes as NULL in v_node too)."""
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_UNIT_SCHEMA)
        conn.executemany(
            "INSERT INTO entities "
            "(name, entity_type, file_path, sector_classification, ticker) "
            "VALUES (?,?,?,?,?)",
            [
                ("Alpha Co", "company", "x/Alpha.md", "Test", "ALPHA"),
                ("Beta Co", "company", "x/Beta.md", "Test", "BETA"),
                ("Gamma Co", "company", "x/Gamma.md", "Test", None),  # no cap tag
                ("Test", "sector", "x/Test.md", None, None),  # the sector focal node
            ],
        )
        # Alpha + Beta carry market_cap tags; Gamma Co has none (-> NULL/unknown).
        conn.executemany(
            "INSERT INTO entity_tags (entity_name, tag) VALUES (?,?)",
            [
                ("Alpha Co", "market_cap/large_cap"),
                ("Beta Co", "market_cap/small_cap"),
            ],
        )
        conn.commit()
        conn.close()

    def test_market_cap_counts_match_member_count_with_null_cap(self, tmp_path, monkeypatch):
        """sum(market_cap_counts) == member_count even when a member has NULL
        market_cap (it lands in the 'unknown' bucket)."""
        db_path = tmp_path / "invariant.db"
        self._seed_unit_db(db_path)

        # Monkeypatch the SQLite connection.
        def _open():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(A, "get_db_connection", _open)

        # Bundle K2: monkeypatch the new helper (returns (name, cap) pairs).
        import helpers.graph.query as gq

        monkeypatch.setattr(
            gq,
            "sector_members_with_market_cap",
            lambda con, sector, market_cap=None: [
                ("Alpha Co", "large_cap"),
                ("Beta Co", "small_cap"),
                ("Gamma Co", None),  # NULL cap -> 'unknown' bucket
            ],
        )

        with A.app.test_client() as c:
            r = c.get("/api/graph/neighbors/Test")
        assert r.status_code == 200
        data = r.get_json()
        assert data["member_count"] == 3
        # Gamma Co (NULL cap) lands in 'unknown'; the sum still covers all 3.
        assert data["market_cap_counts"] == {"large_cap": 1, "small_cap": 1, "unknown": 1}
        assert sum(data["market_cap_counts"].values()) == data["member_count"]

    def test_market_cap_counts_match_when_members_subset(self, tmp_path, monkeypatch):
        """When sector_members_with_market_cap returns a subset (e.g. via
        market_cap filter), the breakdown reflects only that subset, not all
        companies in the sector."""
        db_path = tmp_path / "invariant2.db"
        self._seed_unit_db(db_path)

        def _open():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(A, "get_db_connection", _open)

        import helpers.graph.query as gq

        # Simulate a market_cap=large_cap filter: only Alpha Co returned.
        monkeypatch.setattr(
            gq,
            "sector_members_with_market_cap",
            lambda con, sector, market_cap=None: [("Alpha Co", "large_cap")],
        )

        with A.app.test_client() as c:
            r = c.get("/api/graph/neighbors/Test")
        assert r.status_code == 200
        data = r.get_json()
        assert data["member_count"] == 1
        assert data["market_cap_counts"] == {"large_cap": 1}
        assert sum(data["market_cap_counts"].values()) == data["member_count"]


class TestThemeNeighborsBundle:
    """D4: the /api/graph/neighbors/<theme> branch returns a theme's exposed
    member companies (cross-sector membership via exposed_to, not part_of)."""

    def test_theme_returns_member_companies(self, tmp_path, monkeypatch):
        db_path = tmp_path / "theme_unit.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_UNIT_SCHEMA)
        conn.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?,?)",
            [
                ("PLI_Scheme", "theme"),
                ("Acme Electronics", "company"),
                ("Beta Pharma", "company"),
            ],
        )
        conn.commit()
        conn.close()

        def _open():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(A, "get_db_connection", _open)
        # theme_members hits DuckDB; monkeypatch to avoid a live graph conn.
        import helpers.graph.query as gq

        monkeypatch.setattr(
            gq,
            "theme_members",
            lambda con, theme: ["Acme Electronics", "Beta Pharma"] if theme == "PLI_Scheme" else [],
        )

        with A.app.test_client() as c:
            r = c.get("/api/graph/neighbors/PLI_Scheme")
        assert r.status_code == 200
        data = r.get_json()
        assert data["entity_type"] == "theme"
        assert data["theme"] == "PLI_Scheme"
        assert data["member_count"] == 2
        assert data["members"] == ["Acme Electronics", "Beta Pharma"]


class TestEventsEndpoint:
    """D7: GET /api/events/<name> returns a date-ordered timeline.

    Reads SQLite directly (no DuckDB), so this is a pure unit test: seed a
    company + 3 events (acquisition dated, guidance dated later, undated
    management_change) and assert the response shape + ordering.
    """

    _EVENTS_SCHEMA = (
        _UNIT_SCHEMA
        + """
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
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

    def _seed(self, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.executescript(self._EVENTS_SCHEMA)
        conn.execute(
            "INSERT INTO entities (name, entity_type, file_path) "
            "VALUES ('Test Co', 'company', 'findata/Companies/X/Test.md')"
        )
        # Insert out-of-order so we prove the endpoint sorts, not the seed.
        conn.executemany(
            "INSERT INTO events (entity, event_type, event_date, period, "
            "magnitude, counterparty, source_ref) VALUES (?,?,?,?,?,?,?)",
            [
                # id 1: undated management change.
                (
                    "Test Co",
                    "management_change",
                    None,
                    None,
                    "CEO",
                    None,
                    "derive:events:management-prose",
                ),
                # id 2: dated guidance (later).
                (
                    "Test Co",
                    "guidance",
                    "2026-04-01",
                    "FY27",
                    "10-12%",
                    None,
                    "derive:events:guidance-prose",
                ),
                # id 3: dated acquisition (earliest).
                (
                    "Test Co",
                    "acquisition",
                    "2025-01-15",
                    "2025",
                    "58% stake",
                    "Target Inc",
                    "derive:events:edge-promotion",
                ),
            ],
        )
        conn.commit()
        conn.close()

    def test_returns_date_ordered_timeline(self, tmp_path, monkeypatch):
        db_path = tmp_path / "events_unit.db"
        self._seed(db_path)

        def _open():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(A, "get_db_connection", _open)

        with A.app.test_client() as c:
            r = c.get("/api/events/Test Co")
        assert r.status_code == 200
        data = r.get_json()
        assert data["entity"] == "Test Co"
        assert data["entity_type"] == "company"
        assert data["event_count"] == 3
        types = [e["event_type"] for e in data["events"]]
        assert types == ["acquisition", "guidance", "management_change"], types
        # Dated events first (oldest -> newest), undated last.
        dates = [e["event_date"] for e in data["events"]]
        assert dates == ["2025-01-15", "2026-04-01", None], dates

    def test_404_for_unknown_entity(self, tmp_path, monkeypatch):
        db_path = tmp_path / "events_unit.db"
        self._seed(db_path)

        def _open():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(A, "get_db_connection", _open)

        with A.app.test_client() as c:
            r = c.get("/api/events/Nonexistent Co")
        assert r.status_code == 404

    def test_event_type_filter(self, tmp_path, monkeypatch):
        db_path = tmp_path / "events_unit.db"
        self._seed(db_path)

        def _open():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(A, "get_db_connection", _open)

        with A.app.test_client() as c:
            r = c.get("/api/events/Test Co?event_type=guidance")
        assert r.status_code == 200
        data = r.get_json()
        assert data["event_count"] == 1
        assert data["events"][0]["event_type"] == "guidance"

    def test_case_insensitive_resolution(self, tmp_path, monkeypatch):
        db_path = tmp_path / "events_unit.db"
        self._seed(db_path)

        def _open():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(A, "get_db_connection", _open)

        with A.app.test_client() as c:
            r = c.get("/api/events/test co")
        assert r.status_code == 200
        data = r.get_json()
        assert data["entity"] == "Test Co"  # canonical case
        assert data["event_count"] == 3
