"""Tests for helpers/graph/analytics.py (A3 parquet analytics)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.graph import analytics as AN  # noqa: E402


@pytest.fixture()
def snap(tmp_path: Path) -> Path:
    """Minimal synthetic snapshot tree (both sides) written as parquet."""
    con = duckdb.connect()
    try:
        (tmp_path / "duckdb").mkdir()
        (tmp_path / "sqlite").mkdir()
        con.execute("CREATE TABLE meta(key VARCHAR, value VARCHAR)")
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [("schema_version", "9"), ("generation", "42")])
        con.execute("COPY meta TO '" + str(tmp_path / "duckdb" / "_build_meta.parquet") + "'")
        con.execute(
            "CREATE TABLE graph_edges(id BIGINT, source VARCHAR, target VARCHAR,"
            " edge_type VARCHAR, weight DOUBLE, properties VARCHAR, valid_from VARCHAR,"
            " valid_to INTEGER, source_ref VARCHAR, is_symmetric BIGINT, created_at VARCHAR)")
        con.executemany(
            "INSERT INTO graph_edges VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?)",
            [
                (1, "A", "Sec1", "part_of", "2025-01-02 10:00:00"),
                (2, "A", "B", "co_mentioned_in", "2025-03-03 10:00:00"),
                (3, "A", "C", "co_mentioned_in", "2026-01-05 10:00:00"),
                (4, "B", "C", "subsidiary_of", "2026-02-06 10:00:00"),
            ],
        )
        con.execute("COPY graph_edges TO '" + str(tmp_path / "sqlite" / "graph_edges.parquet") + "'")
        con.execute(
            "CREATE TABLE entities(name VARCHAR, entity_type VARCHAR, created_at VARCHAR,"
            " file_path VARCHAR, last_updated VARCHAR, normalized_name VARCHAR,"
            " sector_classification VARCHAR, ticker VARCHAR)")
        con.executemany(
            "INSERT INTO entities VALUES (?, 'company', ?, NULL, NULL, NULL, ?, NULL)",
            [("A", "2025-01-01 09:00:00", "S1"),
             ("B", "2025-02-01 09:00:00", "S1"),
             ("C", "2026-01-01 09:00:00", "S2"),
             ("T1", "2025-01-01 09:00:00", None)],
        )
        con.execute("COPY entities TO '" + str(tmp_path / "sqlite" / "entities.parquet") + "'")
    finally:
        con.close()
    return tmp_path


class TestFetch:
    def test_summary_counts_every_parquet_and_meta(self, snap):
        r = AN.fetch("summary", snap)
        by_table = {(row[0], row[1]): row[2] for row in r.rows}
        assert by_table[("duckdb", "_build_meta")] == "2"
        assert by_table[("sqlite", "graph_edges")] == "4"
        assert by_table[("sqlite", "entities")] == "4"
        assert r.meta["generation"] == "42"

    def test_edge_growth_by_ingest_year(self, snap):
        r = AN.fetch("edge-growth", snap)
        got = {(row[0], row[1]): row[2] for row in r.rows}
        assert got[("2025", "part_of")] == "1"
        assert got[("2025", "co_mentioned_in")] == "1"
        assert got[("2026", "co_mentioned_in")] == "1"
        assert got[("2026", "subsidiary_of")] == "1"

    def test_sector_growth_companies_only(self, snap):
        r = AN.fetch("sector-growth", snap)
        got = {row[0]: row for row in r.rows}
        assert got["S1"][1] == "2" and got["S2"][1] == "1"
        assert "T1" not in got  # theme entity excluded
        # latest_ingest_year is per-sector; new_in_latest_year counts the
        # sector's intake in the GLOBALLY latest year (2026 here).
        assert got["S1"][2] == "2025" and got["S1"][3] == "0"  # no 2026 S1 intake
        assert got["S2"][2] == "2026" and got["S2"][3] == "1"

    def test_top_entities_excludes_membership(self, snap):
        r = AN.fetch("top-entities", snap)
        # A: (A,B)+(A,C) = 2; B: (A,B)+(B,C) = 2; C: 2; Sec1 part_of excluded.
        # Tie on deg=2 -> name order A, B, C.
        assert [row[0] for row in r.rows] == ["A", "B", "C"]
        assert r.rows[0][2] == "2"          # A co-mentions
        assert r.rows[1][1] == "2"          # B total non-membership degree
        assert r.rows[0][3] == "2025"       # A first ingest year

    def test_unknown_report_raises(self, snap):
        with pytest.raises(ValueError, match="unknown report"):
            AN.fetch("nope", snap)

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            AN.fetch("summary", tmp_path / "absent")


class TestRender:
    def test_markdown_is_aligned_and_deterministic(self, snap):
        r = AN.fetch("summary", snap)
        a, b = AN.render_markdown(r), AN.render_markdown(r)
        assert a == b
        lines = a.splitlines()
        assert lines[0] == "# Snapshot summary"
        assert any("generation=42" in ln for ln in lines)
        hdr = next(ln for ln in lines if ln.startswith("side"))
        sep = lines[lines.index(hdr) + 1]
        assert set(sep) <= {"-", " "}

    def test_json_round_trip(self, snap):
        r = AN.fetch("edge-growth", snap)
        data = json.loads(AN.render_json(r))
        assert data["title"] == "Edge growth by ingest year"
        assert len(data["rows"]) == 4 and data["headers"][0] == "year"

    def test_empty_rows_renders(self, snap):
        r = AN.Report("Empty", ["a", "b"], [])
        md = AN.render_markdown(r)
        assert "Empty" in md and "a" in md


class TestCLI:
    def test_main_summary_json(self, snap, capsys):
        rc = AN.main(["summary", "--snapshots", str(snap), "--json"])
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out)["meta"]["generation"] == "42"

    def test_main_markdown_default_report(self, snap, capsys):
        rc = AN.main(["--snapshots", str(snap)])
        assert rc == 0 and "# Snapshot summary" in capsys.readouterr().out


@pytest.fixture()
def cov_snap(tmp_path: Path) -> Path:
    """Coverage-shaped snapshot: editions + note_tags + cited_in edges.

    Separate from ``snap`` — its summary/edge-growth tests pin exact
    table counts, so cited_in/entities additions live here only.
    """
    con = duckdb.connect()
    try:
        (tmp_path / "duckdb").mkdir()
        (tmp_path / "sqlite").mkdir()
        con.execute("CREATE TABLE meta(key VARCHAR, value VARCHAR)")
        con.executemany("INSERT INTO meta VALUES (?, ?)",
                        [("schema_version", "10"), ("generation", "43")])
        con.execute("COPY meta TO '" + str(tmp_path / "duckdb" / "_build_meta.parquet") + "'")
        con.execute(
            "CREATE TABLE entities(name VARCHAR, entity_type VARCHAR, created_at VARCHAR,"
            " file_path VARCHAR, last_updated VARCHAR, normalized_name VARCHAR,"
            " sector_classification VARCHAR, ticker VARCHAR)")
        con.executemany(
            "INSERT INTO entities VALUES (?, ?, NULL, ?, NULL, NULL, ?, NULL)",
            [("A", "company", None, "S1"),
             ("B", "company", None, "S1"),
             ("C", "company", None, "S2"),
             ("Agri", "sector", None, None),
             ("Ed1", "edition", "findata/The_Chatter/Ed1.md", None),
             ("Ed2", "edition", "findata/Points_And_Figures/Ed2.md", None)],
        )
        con.execute("COPY entities TO '" + str(tmp_path / "sqlite" / "entities.parquet") + "'")
        con.execute("CREATE TABLE note_tags(note_path VARCHAR, tag VARCHAR)")
        con.executemany("INSERT INTO note_tags VALUES (?, ?)", [
            ("findata/The_Chatter/Ed1.md", "series/the_chatter"),
            ("findata/The_Chatter/Ed1.md", "publisher/zerodha"),
            ("findata/Points_And_Figures/Ed2.md", "series/points_and_figures"),
        ])
        con.execute("COPY note_tags TO '" + str(tmp_path / "sqlite" / "note_tags.parquet") + "'")
        con.execute(
            "CREATE TABLE graph_edges(id BIGINT, source VARCHAR, target VARCHAR,"
            " edge_type VARCHAR, weight DOUBLE, properties VARCHAR, valid_from VARCHAR,"
            " valid_to INTEGER, source_ref VARCHAR, is_symmetric BIGINT, created_at VARCHAR)")
        con.executemany(
            "INSERT INTO graph_edges VALUES (?, ?, ?, 'cited_in', 1.0, ?, NULL, NULL,"
            " 'derive:cited_in', 0, '2026-08-19 12:00:00')",
            [(1, "A", "Ed1", '{"n_quotes": 2, "resource": "/findata/The_Chatter/Ed1.md"}'),
             (2, "B", "Ed1", '{"n_quotes": 0, "resource": "/findata/The_Chatter/Ed1.md"}'),
             (3, "C", "Ed2", '{"n_quotes": 1}'),
             # sector note citing an edition: matrix-excluded, rollup-counted
             (4, "Agri", "Ed1", "{}")],
        )
        con.execute("COPY graph_edges TO '" + str(tmp_path / "sqlite" / "graph_edges.parquet") + "'")
    finally:
        con.close()
    return tmp_path


class TestCoverage:
    def test_matrix_joins_series_sector_and_quote_depth(self, cov_snap):
        r = AN.fetch("coverage", cov_snap)
        got = {(row[0], row[1]): row for row in r.rows}
        assert got[("series/the_chatter", "S1")][2:5] == ["2", "1", "2"]
        assert got[("series/points_and_figures", "S2")][2:5] == ["1", "1", "1"]
        # the sector-note edge never lands in the company matrix
        assert ("series/the_chatter", "Agri") not in got

    def test_rollup_and_hygiene_in_note(self, cov_snap):
        r = AN.fetch("coverage", cov_snap)
        assert "the_chatter 1 editions / 2 companies / 1 sector-notes / 2 quotes" in r.note
        assert ("points_and_figures 1 editions / 1 companies"
                " / 0 sector-notes / 1 quotes") in r.note
        assert "4/4 cited_in edges joined" in r.note
        assert "drift" not in r.note

    def test_unjoined_edges_flag_drift(self, cov_snap):
        # an edition entity with no series tag: its edge joins nothing
        con = duckdb.connect()
        try:
            con.execute(
                "COPY (SELECT * FROM read_parquet('" + str(cov_snap / "sqlite" / "graph_edges.parquet") + "')"  # noqa: S608  # fixture-local tmp path
                " UNION ALL SELECT 5, 'A', 'Ed3', 'cited_in', 1.0, '{}', NULL, NULL,"
                " 'derive:cited_in', 0, '2026-08-19 12:00:00')"
                " TO '" + str(cov_snap / "sqlite" / "graph_edges.parquet") + "'")
        finally:
            con.close()
        r = AN.fetch("coverage", cov_snap)
        assert "4/5 cited_in edges joined" in r.note
        assert "drift" in r.note

    def test_publisher_tags_dont_pollute_series(self, cov_snap):
        r = AN.fetch("coverage", cov_snap)
        assert all(row[0].startswith("series/") for row in r.rows)

    def test_main_bad_report_is_cli_error(self, snap, capsys):
        try:
            AN.main(["nope", "--snapshots", str(snap)])
        except SystemExit as e:
            assert e.code != 0  # argparse choices reject before fetch


@pytest.mark.live
class TestLiveSnapshot:
    """The git-tracked snapshots/parquet tree (always present in a checkout)."""

    def test_live_summary_fetches(self):
        r = AN.fetch("summary")
        assert "duckdb" in {row[0] for row in r.rows}
        assert "sqlite" in {row[0] for row in r.rows}
        assert r.meta.get("schema_version")
