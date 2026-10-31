"""Tests for the `provenance` column in `convo_search`.

Added because `--resolve-orphans` relabels corpus `source` to
`orphan:<harness>` and, without this column, that relabel was invisible to
`convo_query` — the parquet said `orphan:opencode` and every query result
silently disagreed with it.

Two traps this file exists to pin, both hit during the build:

1. **`CREATE TABLE IF NOT EXISTS` cannot add a column.** The DDL change alone
   is a no-op on an existing table, so every pre-existing row would read `''`
   with no error raised anywhere. Hence `_provenance_migration`.
2. **19 of the 271 corpus files carry MIXED provenance** (a session harvested
   live AND covered by a snapshot has both values in one parquet), so a
   per-file backfill either writes a lie or skips the file and leaves ~2,670
   rows empty. Hence the `(file_path, row_no)` join.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from helpers.maintenance import rebuild_convo_search as rcs
from helpers.misc import convo_query as cq

SCHEMA = [
    ("harness", "VARCHAR"),
    ("session_id", "VARCHAR"),
    ("part_id", "VARCHAR"),
    ("file_path", "VARCHAR"),
    ("row_no", "BIGINT"),
    ("ts", "TIMESTAMP"),
    ("role", "VARCHAR"),
    ("agent", "VARCHAR"),
    ("model", "VARCHAR"),
    ("part_type", "VARCHAR"),
    ("text_len", "BIGINT"),
    ("snippet", "VARCHAR"),
    ("embedding", "FLOAT[]"),
    ("provenance", "VARCHAR"),
]
OLD_SCHEMA = SCHEMA[:-1]  # what an index built before the column existed looks like


def _make_db(path, *, with_provenance: bool):
    con = duckdb.connect(str(path))
    cols = ", ".join(f"{n} {t}" for n, t in (SCHEMA if with_provenance else OLD_SCHEMA))
    con.execute(f"CREATE TABLE convo_search ({cols})")
    return con


def _corpus(tmp_path, name: str, sources: list[str]) -> Path:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "session_id": "ses_x",
                    "part_id": f"p{i}",
                    "source": s,
                    "part_type": "text",
                    "text": f"body {i} " * 40,
                    "role": "assistant",
                    "ts": None,
                    "row_no": i,
                }
                for i, s in enumerate(sources)
            ]
        ),
        f,
    )
    return f


class TestMigration:
    def test_detects_a_missing_column(self, tmp_path):
        db = tmp_path / "x.duckdb"
        _make_db(db, with_provenance=False).close()
        assert rcs._provenance_migration(db) is True

    def test_is_idempotent(self, tmp_path):
        db = tmp_path / "x.duckdb"
        _make_db(db, with_provenance=False).close()
        assert rcs._provenance_migration(db) is True
        assert rcs._provenance_migration(db) is False, "second pass must be a no-op"

    def test_absent_index_needs_no_migration(self, tmp_path):
        assert rcs._provenance_migration(tmp_path / "nope.duckdb") is False

    def test_ddl_carries_the_column(self):
        assert "provenance" in rcs.CONVO_SEARCH_DDL
        # the staging schema must match the table exactly or the Arrow batch
        # is rejected or, worse, silently mis-aligned
        names = [n for n, _ in rcs._STAGE_SCHEMA_FIELDS]
        for col in names:
            assert col in rcs.CONVO_SEARCH_DDL, f"{col} missing from the DDL"


class TestBackfill:
    def _index(self, db, file_path: str, sources_by_row: list[str]):
        con = duckdb.connect(str(db))
        for i, s in enumerate(sources_by_row):
            con.execute(
                "INSERT INTO convo_search (harness, session_id, part_id, file_path, "
                "row_no, role, part_type, snippet, provenance) "
                "VALUES ('opencode','ses_x',?,?,?,'assistant','text','s','')",
                [f"p{i}", file_path, i, s],
            )
        con.close()

    def test_fills_a_single_source_file(self, tmp_path, monkeypatch):
        db = tmp_path / "x.duckdb"
        con = _make_db(db, with_provenance=True)
        con.execute("INSERT INTO convo_search (file_path, row_no, provenance) VALUES ('f', 0, '')")
        con.close()
        f = _corpus(tmp_path, "f", ["opencode:live"] * 3)
        monkeypatch.setattr(rcs, "REPO", tmp_path)
        _ = rcs._backfill_provenance(db, [f])
        row = (
            duckdb.connect(str(db))
            .execute("SELECT count(*) FROM convo_search WHERE provenance = 'opencode:live'")
            .fetchone()
        )
        got = row[0] if row else 0
        assert got == 1

    def test_handles_a_mixed_provenance_file(self, tmp_path, monkeypatch):
        """The case that makes a per-file backfill wrong: one file, two sources."""
        db = tmp_path / "x.duckdb"
        con = _make_db(db, with_provenance=True)
        con.execute("INSERT INTO convo_search (file_path, row_no, provenance) VALUES ('f', 0, '')")
        con.execute("INSERT INTO convo_search (file_path, row_no, provenance) VALUES ('f', 1, '')")
        con.execute("INSERT INTO convo_search (file_path, row_no, provenance) VALUES ('f', 2, '')")
        con.close()
        f = _corpus(
            tmp_path,
            "f",
            ["opencode:live", "opencode-snap:2026-07-26", "opencode:live"],
        )
        monkeypatch.setattr(rcs, "REPO", tmp_path)
        rcs._backfill_provenance(db, [f])
        rows = dict(
            duckdb.connect(str(db))
            .execute("SELECT row_no, provenance FROM convo_search ORDER BY row_no")
            .fetchall()
        )
        assert rows == {
            0: "opencode:live",
            1: "opencode-snap:2026-07-26",
            2: "opencode:live",
        }

    def test_leaves_nothing_empty(self, tmp_path, monkeypatch):
        db = tmp_path / "x.duckdb"
        con = _make_db(db, with_provenance=True)
        for i in range(3):
            con.execute(
                "INSERT INTO convo_search (file_path, row_no, provenance) VALUES ('f', ?, '')",
                [i],
            )
        con.close()
        f = _corpus(tmp_path, "f", ["opencode:live", "opencode:live", "zcode:live"])
        monkeypatch.setattr(rcs, "REPO", tmp_path)
        rcs._backfill_provenance(db, [f])
        row = (
            duckdb.connect(str(db))
            .execute("SELECT count(*) FROM convo_search WHERE coalesce(provenance,'') = ''")
            .fetchone()
        )
        empty = row[0] if row else 0
        assert empty == 0


class TestQuerySurface:
    def test_like_pattern_escapes_wildcards(self):
        # a bare `%` from the user must not become a match-everything pattern
        assert cq._like_pattern("orphan:") == "%orphan:%"
        assert cq._like_pattern("%") == r"%\%%"
        assert cq._like_pattern("_x") == r"%\_x%"
        assert cq._like_pattern("*") == "%"

    def test_fetch_rows_returns_provenance(self, tmp_path, monkeypatch):
        db = tmp_path / "x.duckdb"
        con = _make_db(db, with_provenance=True)
        con.execute(
            "INSERT INTO convo_search (harness, session_id, part_id, file_path, row_no, "
            "role, part_type, snippet, provenance) "
            "VALUES ('opencode','ses_x','p0','f',0,'assistant','text','s','orphan:opencode')"
        )
        con.close()
        # connect() opens the FTS sidecar eagerly; _fetch_rows never reads it.
        sqlite3.connect(str(db.parent / rcs.FTS_DB_NAME)).close()
        conn = cq.connect(db)
        try:
            rows = cq._fetch_rows(conn, [("opencode", "p0")])
        finally:
            conn.close()
        assert rows[("opencode", "p0")]["provenance"] == "orphan:opencode"

    def test_render_shows_provenance_only_when_meaningful(self, capsys):
        cq.render_hits(
            [
                {
                    "rank": 1,
                    "score": 1.0,
                    "mode": "bm25",
                    "harness": "opencode",
                    "part_type": "text",
                    "role": "assistant",
                    "ts": "2026-10-17 00:00",
                    "text_len": 10,
                    "head": "body",
                    "provenance": "orphan:opencode",
                    "pointer": "f:1",
                },
                {
                    "rank": 2,
                    "score": 1.0,
                    "mode": "bm25",
                    "harness": "opencode",
                    "part_type": "text",
                    "role": "assistant",
                    "ts": "2026-10-17 00:00",
                    "text_len": 10,
                    "head": "body",
                    "provenance": "opencode:live",
                    "pointer": "f:2",
                },
            ]
        )
        out = capsys.readouterr().out
        assert "provenance: orphan:opencode" in out
        assert out.count("provenance:") == 1, "the uninteresting live label is not printed"
