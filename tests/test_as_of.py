#!/usr/bin/env python3
"""Tests for helpers/graph/as_of.py (price_lane_alpha_research S0)."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.graph import as_of as ao  # noqa: E402


def _make_db(tmp_path: Path, narrow: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "asof.db")
    if narrow:
        conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT)")
        conn.executemany("INSERT INTO graph_edges VALUES (?, ?)", [("A", "B"), ("B", "C")])
    else:
        conn.executescript(
            """
            CREATE TABLE graph_edges (
                source TEXT, target TEXT, edge_type TEXT,
                weight REAL DEFAULT 1.0,
                valid_from DATE, valid_to DATE, source_ref TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO graph_edges VALUES (?,?,?,?,?,?,?)",
            [
                # windowed: visible only inside [2026-01-01, 2026-06-01)
                ("A", "B", "acquired", 1.0, "2026-01-01", "2026-06-01", "r1"),
                # open-ended: born mid-2026, never expires
                ("A", "C", "supplier_to", 2.0, "2026-03-01", None, "r2"),
                # undated: always visible
                ("B", "C", "competes_with", 1.0, None, None, "r3"),
                # born after the probe date: invisible at 2026-01-01
                ("C", "D", "jv_with", 1.0, "2026-09-01", None, "r4"),
            ],
        )
    conn.commit()
    return conn


class TestEdgesAsOf(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.conn = _make_db(self.tmp)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_window_open_and_closed(self):
        before = {e["source"] + e["target"] for e in ao.edges_as_of(self.conn, "2025-12-31")}
        inside = {e["source"] + e["target"] for e in ao.edges_as_of(self.conn, "2026-03-15")}
        after = {e["source"] + e["target"] for e in ao.edges_as_of(self.conn, "2026-07-01")}
        # before the window: only undated r3
        self.assertEqual(before, {"BC"})
        # inside: r3 (undated) + r1 (window) + r2 (born 03-01); r4 not yet born
        self.assertEqual(inside, {"BC", "AB", "AC"})
        # after r1's valid_to (exclusive): r1 gone, r3 + r2 remain
        self.assertEqual(after, {"BC", "AC"})

    def test_valid_to_is_exclusive(self):
        types = {e["source"] + e["target"] for e in ao.edges_as_of(self.conn, "2026-06-01")}
        self.assertNotIn("AB", types)  # valid_to 06-01 excludes the boundary day
        day_before = {e["source"] + e["target"] for e in ao.edges_as_of(self.conn, "2026-05-31")}
        self.assertIn("AB", day_before)

    def test_edge_type_filter(self):
        rows = ao.edges_as_of(self.conn, "2026-03-15", edge_type="acquired")
        self.assertEqual([r["source"] + r["target"] for r in rows], ["AB"])

    def test_summary_and_differential(self):
        summary = ao.as_of_summary(self.conn, "2026-03-15")
        self.assertEqual(summary, {"acquired": 1, "supplier_to": 1, "competes_with": 1})
        d = ao.differential(self.conn, "2026-03-15")
        self.assertEqual(d["live_total"], 4)
        self.assertEqual(d["asof_total"], 3)
        # expired_at: r1 still OPEN at 03-15 — nothing expired yet
        self.assertEqual(d["expired_at"], 0)
        self.assertEqual(d["not_yet_born"], 1)  # r4 born 09-01

    def test_narrow_schema_degrades_to_always_visible(self):
        import tempfile

        tmp2 = tempfile.TemporaryDirectory()
        narrow = _make_db(Path(tmp2.name), narrow=True)
        try:
            rows = ao.edges_as_of(narrow, "2026-03-15")
            self.assertEqual(len(rows), 2)  # both fixture rows, undated
            self.assertEqual(rows[0]["weight"], 1.0)
        finally:
            narrow.close()
            tmp2.cleanup()


if __name__ == "__main__":
    unittest.main()
