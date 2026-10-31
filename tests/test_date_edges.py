#!/usr/bin/env python3
"""Tests for helpers/maintenance/date_edges.py (graph_alpha_lane S1)."""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance import date_edges as de  # noqa: E402


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "date_edges.db")
    conn.executescript(
        """
        CREATE TABLE graph_edges (
            rowid_counter INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, target TEXT, edge_type TEXT,
            weight REAL DEFAULT 1.0,
            valid_from DATE, valid_to DATE,
            source_ref TEXT, properties TEXT DEFAULT '{}'
        );
        """
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, properties, valid_from) "
        "VALUES (?,?,?,?,?,?)",
        [
            # Class A: RPT period_end parse
            (
                "TCS",
                "Rallis India",
                "same_group",
                "vigil:rpt:TCS",
                json.dumps({"period_end": "31-MAR-2026"}),
                None,
            ),
            # Class A: unresolvable period_end (bad format) -> stays undated
            (
                "X",
                "Y",
                "same_group",
                "vigil:rpt:X",
                json.dumps({"period_end": "March 2026?"}),
                None,
            ),
            # Class A: non-RPT ref without period_end -> stays undated
            ("P", "Q", "same_group", "manual", "{}", None),
            # Class B: date embedded in ref
            ("A", "B", "competes_with", "yfinance:industry:2026-08-25", "{}", None),
            # Class B: no date in ref -> stays undated
            ("C", "D", "semantic_peer", "embeddings:no-date-here", "{}", None),
            # already dated: never touched
            (
                "E",
                "F",
                "supplier_to",
                "vigil:rpt:E",
                json.dumps({"period_end": "30-SEP-2025"}),
                "2025-09-30",
            ),
        ],
    )
    conn.commit()
    return conn


class TestDateEdges(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.conn = _make_db(self.tmp)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _vf(self, src):
        r = self.conn.execute(
            "SELECT valid_from FROM graph_edges WHERE source = ?", (src,)
        ).fetchone()
        return r[0] if r else None

    def test_plan_class_a_parses_period_end(self):
        plan_rows, census = de.plan(self.conn)
        dates = {rowid: d for rowid, d in plan_rows}
        by_src = dict(self.conn.execute("SELECT source, rowid FROM graph_edges").fetchall())
        self.assertEqual(dates[by_src["TCS"]], "2026-03-31")
        c = census["same_group"]
        self.assertEqual(c["datable"], 1)  # X/Y unresolvable, P/Q no period_end
        self.assertEqual(c["unresolvable"], 2)
        self.assertEqual(census["supplier_to"]["already_dated"], 1)
        self.assertEqual(census["competes_with"]["datable"], 1)
        self.assertEqual(census["semantic_peer"]["unresolvable"], 1)

    def test_dry_run_writes_nothing(self):
        de.plan(self.conn)  # the plan is pure read; assert no writes happened
        self.assertIsNone(self._vf("TCS"))

    def test_apply_dates_and_dump_enables_revert(self):
        plan_rows, _c = de.plan(self.conn)
        dump = self.tmp / "dump.json"
        dump.write_text(json.dumps({"rows": [[rid, None] for rid, _d in plan_rows]}))
        with self.conn:
            self.conn.executemany(
                "UPDATE graph_edges SET valid_from = ? WHERE rowid = ?",
                [(d, rid) for rid, d in plan_rows],
            )
        self.assertEqual(self._vf("TCS"), "2026-03-31")
        self.assertEqual(self._vf("A"), "2026-08-25")
        # already-dated row untouched
        self.assertEqual(self._vf("E"), "2025-09-30")
        # revert restores the pre-apply state
        de.revert(str(dump), conn=self.conn)
        self.assertIsNone(self._vf("TCS"))
        self.assertIsNone(self._vf("A"))
        # untouched row keeps its value
        self.assertEqual(self._vf("E"), "2025-09-30")

    def test_second_plan_after_apply_is_zero_new(self):
        plan_rows, _c = de.plan(self.conn)
        with self.conn:
            self.conn.executemany(
                "UPDATE graph_edges SET valid_from = ? WHERE rowid = ?",
                [(d, rid) for rid, d in plan_rows],
            )
        plan2, census2 = de.plan(self.conn)
        self.assertEqual(len(plan2), 0)
        self.assertEqual(census2["same_group"]["undated"], 2)  # only unresolvable remain


if __name__ == "__main__":
    unittest.main()
