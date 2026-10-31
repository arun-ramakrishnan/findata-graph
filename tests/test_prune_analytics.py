#!/usr/bin/env python3
"""Tests for helpers/maintenance/prune_analytics.py (integrity S4)."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance import prune_analytics  # noqa: E402


def _make_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "prune.db")
    conn.executescript(
        """
        CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT);
        CREATE TABLE graph_analytics (
            entity_name TEXT,
            metric TEXT,
            value TEXT,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(metric, entity_name)
        );
        """
    )
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        [("Alpha Co",), ("Beta Co",)],
    )
    conn.executemany(
        "INSERT INTO graph_analytics (entity_name, metric, value) VALUES (?, ?, ?)",
        [
            ("Alpha Co", "pagerank", '{"value": 0.5}'),  # live
            ("Ghost Ltd", "pagerank", '{"value": 0.1}'),  # orphan
            ("Ghost Ltd", "degree_centrality", '{"value": 0.2}'),  # orphan
            ("Phantom Person", "louvain_community", '{"community": 0}'),  # orphan
            ("Beta Co", "pagerank", '{"value": 0.4}'),  # live
        ],
    )
    conn.commit()
    return conn


class TestPruneAnalytics(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.conn = _make_db(self.tmp)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_census_counts_orphans_per_metric(self):
        census = dict(prune_analytics.orphan_census(self.conn))
        self.assertEqual(census, {"degree_centrality": 1, "louvain_community": 1, "pagerank": 1})

    def test_main_dry_run_then_apply(self):
        import contextlib
        import io

        db = str(self.tmp / "prune.db")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = prune_analytics.main(["--db", db])
        self.assertEqual(rc, 0)
        self.assertIn("DRY-RUN", out.getvalue())
        self.assertIn("pagerank", out.getvalue())
        # dry-run wrote nothing
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM graph_analytics").fetchone()[0], 5)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = prune_analytics.main(["--db", db, "--apply"])
        self.assertEqual(rc, 0)
        self.assertIn("APPLY: deleted 3", out.getvalue())
        remaining = sorted(
            (r[0], r[1])
            for r in self.conn.execute("SELECT entity_name, metric FROM graph_analytics").fetchall()
        )
        self.assertEqual(remaining, [("Alpha Co", "pagerank"), ("Beta Co", "pagerank")])

    def test_clean_store_is_a_noop(self):
        import contextlib
        import io

        db = str(self.tmp / "prune.db")
        self.conn.execute(
            "DELETE FROM graph_analytics WHERE entity_name NOT IN ('Alpha Co', 'Beta Co')"
        )
        self.conn.commit()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = prune_analytics.main(["--db", db])
        self.assertEqual(rc, 0)
        self.assertIn("0 orphaned rows", out.getvalue())


if __name__ == "__main__":
    unittest.main()
