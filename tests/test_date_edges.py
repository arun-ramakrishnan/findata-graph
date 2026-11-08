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


def _make_edition_vault(root: Path) -> Path:
    """A vault whose editions print the issue date in the body."""
    vault = root / "vault"
    chatter = vault / "The_Chatter"
    chatter.mkdir(parents=True)
    (chatter / "Dated_Edition.md").write_text(
        "---\ntitle: The Chatter Dated Edition\ntype: newsletter\n---\n"
        "# The Chatter Dated Edition\n\nEdition #52\n\nZERODHA MAR27,2026\n\nbody\n",
        encoding="utf-8",
    )
    (chatter / "Bare_Date_Edition.md").write_text(
        "# Bare Date Edition\n\nSEP 30, 2026\n\nbody\n", encoding="utf-8"
    )
    (chatter / "Undated_Edition.md").write_text(
        "# Undated Edition\n\nno masthead date here\n", encoding="utf-8"
    )
    (chatter / "image_map.md").write_text("chrome\n", encoding="utf-8")
    return vault


class TestIssueDateParsing(unittest.TestCase):
    def test_parses_both_printed_shapes(self):
        """Chatter and Points Figures print ZERODHA MAR27,2026 with no
        space before the day; a few print the bare SEP 30, 2026 form.
        Both are the edition issue date, the only date the notes carry."""
        self.assertEqual(de.parse_issue_date("ZERODHA MAR27,2026"), "2026-03-27")
        self.assertEqual(de.parse_issue_date("ZERODHA DEC 05, 2025"), "2025-12-05")
        self.assertEqual(de.parse_issue_date("SEP 30, 2026"), "2026-09-30")

    def test_impossible_and_unknown_dates_are_none(self):
        """FEB 30 must stay undated rather than roll into 2026-03-02,
        and a non-month three-letter token is not a date."""
        self.assertIsNone(de.parse_issue_date("FEB 30, 2026"))
        self.assertIsNone(de.parse_issue_date("ABC 01, 2026"))
        self.assertIsNone(de.parse_issue_date("ZERODHA MAR 00, 2026"))
        self.assertIsNone(de.parse_issue_date("no date at all"))

    def test_only_the_masthead_is_scanned(self):
        """A date-shaped line deep in the body is prose (an event date,
        a post date), never the edition issue date."""
        body = "\n" * de._ISSUE_SCAN_LINES + "OCT 09, 2025"
        self.assertIsNone(de.parse_issue_date(body))
        self.assertEqual(de.parse_issue_date("OCT 09, 2025"), "2025-10-09")


class TestEditionDates(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vault = _make_edition_vault(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_edition_dates_indexes_stems_and_skips_chrome(self):
        dates = de.edition_dates(self.vault)
        self.assertEqual(dates["Dated_Edition"], "2026-03-27")
        self.assertEqual(dates["Bare_Date_Edition"], "2026-09-30")
        self.assertNotIn("Undated_Edition", dates)
        self.assertNotIn("image_map", dates)

    def test_resource_names_one_edition(self):
        dates = de.edition_dates(self.vault)
        props = {"resource": "/findata/The_Chatter/Dated_Edition.md"}
        self.assertEqual(
            de._edition_date_for_row("derive:cited_in", props, dates, None, {}),
            "2026-03-27",
        )

    def test_derive_ref_names_one_edition_but_tree_refs_do_not(self):
        """derive:relations:<note>.md names a single edition; a dir-level
        derive:relations:<tree> names a whole tree and stays Class C
        (undated) because guessing from the tree is the failure mode."""
        dates = de.edition_dates(self.vault)
        self.assertEqual(
            de._edition_date_for_row("derive:relations:Bare_Date_Edition.md", {}, dates, None, {}),
            "2026-09-30",
        )
        self.assertIsNone(
            de._edition_date_for_row("derive:relations:The_Chatter", {}, dates, None, {})
        )

    def test_free_text_edition_resolves_through_the_shared_index(self):
        from helpers.core.edition_index import source_note_index

        dates = de.edition_dates(self.vault)
        index = source_note_index(self.vault)
        # the note TITLE (not the stem) is what properties.edition carries
        props = {"edition": "The Chatter Dated Edition"}
        self.assertEqual(
            de._edition_date_for_row("derive:relations:The_Chatter", props, dates, index, {}),
            "2026-03-27",
        )

    def test_unresolvable_edition_stays_undated(self):
        from helpers.core.edition_index import source_note_index

        dates = de.edition_dates(self.vault)
        index = source_note_index(self.vault)
        props = {"edition": "A Title That Matches Nothing At All"}
        self.assertIsNone(
            de._edition_date_for_row("derive:relations:The_Chatter", props, dates, index, {})
        )


class TestPlanClassD(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.conn = sqlite3.connect(self.tmp / "classd.db")
        self.conn.executescript(
            """
            CREATE TABLE graph_edges (
                source TEXT, target TEXT, edge_type TEXT,
                weight REAL DEFAULT 1.0, valid_from DATE, valid_to DATE,
                source_ref TEXT, properties TEXT DEFAULT \'{}\'
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref, properties) "
            "VALUES (?,?,?,?,?)",
            [
                # cited_in from a dated edition -> Class D dates it
                (
                    "THREADS",
                    "Dated Edition",
                    "cited_in",
                    "derive:cited_in",
                    json.dumps({"resource": "/findata/The_Chatter/Dated_Edition.md"}),
                ),
                # cited_in from a vault note that prints no date -> stays undated
                (
                    "X",
                    "Undated Edition",
                    "cited_in",
                    "derive:cited_in",
                    json.dumps({"resource": "/findata/The_Chatter/Undated_Edition.md"}),
                ),
                # Class B family: the ref date must WIN over an edition date
                (
                    "A",
                    "B",
                    "competes_with",
                    "yfinance:industry:2026-08-25",
                    json.dumps({"resource": "/findata/The_Chatter/Dated_Edition.md"}),
                ),
            ],
        )
        self.conn.commit()
        self.dates = {"Dated_Edition": "2026-03-27", "Bare_Date_Edition": "2026-09-30"}

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _plan_by_source(self):
        plan_rows, _census = de.plan(self.conn, dates=self.dates)
        rowid_by_source = dict(
            self.conn.execute("SELECT source, rowid FROM graph_edges").fetchall()
        )
        return {src: dict(plan_rows).get(rid) for src, rid in rowid_by_source.items()}

    def test_class_d_dates_cited_in_from_its_edition(self):
        dates = self._plan_by_source()
        self.assertEqual(dates["THREADS"], "2026-03-27")
        self.assertIsNone(dates["X"])  # undated edition stays undated

    def test_class_b_ref_date_beats_the_edition_date(self):
        """Evidence precedence: a ref-embedded derive/filing date is a
        stricter witness than the edition the row was extracted from."""
        self.assertEqual(self._plan_by_source()["A"], "2026-08-25")

    def test_plan_without_dates_keeps_the_a_b_family_set(self):
        """Class D is opt-in: the default plan must not silently start
        dating families whose only witness would be an edition date."""
        _rows, census = de.plan(self.conn)
        self.assertNotIn("cited_in", census)
        self.assertEqual(census["competes_with"]["datable"], 1)

    def test_census_closes_over_every_family(self):
        _rows, census = de.plan(self.conn, dates=self.dates)
        for et, c in census.items():
            self.assertEqual(c["undated"], c["datable"] + c["unresolvable"], et)
            total = self.conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type = ?", (et,)
            ).fetchone()[0]
            self.assertEqual(c["already_dated"] + c["undated"], total, et)


if __name__ == "__main__":
    unittest.main()
