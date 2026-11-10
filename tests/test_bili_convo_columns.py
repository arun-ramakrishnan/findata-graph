"""Bili first-class columns + query lane (bili_first_class_convo_search).

Covers both halves of the landed slices:

- rebuild side: the bili_active/bili_tier projection (_bili_columns), the
  in-place column migration, and the (file_path, part_id) backfill;
- query side: RowFilters leg-level filtering (the candidate pool is drawn
  from the filtered population — post-fusion filtering cannot promote a
  row that never entered the pool) and the --summary-first partition.

Hermetic: hand-built duckdb/sqlite fixtures, no embedder, no live store,
no snapshot mount.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.maintenance import rebuild_convo_search as rcs  # noqa: E402
from helpers.maintenance.rebuild_common import QueryVector  # noqa: E402
from helpers.misc import convo_query as cq  # noqa: E402

DIMS = 4

# (agent, meta-json, expected (bili_active, bili_tier))
_BILI_CASES = [
    ("bili", '{"tier": 1, "active": true}', (True, 1)),
    ("bili", '{"tier": 2, "active": false}', (False, 2)),
    ("bili", '{"tier": "3", "active": true}', (True, None)),  # tier not an int
    ("bili", '{"active": true}', (True, None)),  # no tier
    ("bili", '{"tier": 1}', (None, 1)),  # no active
    ("bili", "", (None, None)),  # empty payload
    ("bili", "not json {", (None, None)),  # garbled
    ("bili", '["tier"]', (None, None)),  # non-object
    ("build", '{"tier": 1, "active": true}', (None, None)),  # wrong agent
]


class TestBiliColumns:
    @pytest.mark.parametrize("agent,meta,expected", _BILI_CASES)
    def test_projection(self, agent, meta, expected):
        row = {"agent": agent, "meta": meta}
        assert rcs._bili_columns(row) == {
            "bili_active": expected[0],
            "bili_tier": expected[1],
        }

    def test_missing_meta_key(self):
        assert rcs._bili_columns({"agent": "bili"})["bili_active"] is None

    def test_null_meta(self):
        assert rcs._bili_columns({"agent": "bili", "meta": None}) == {
            "bili_active": None,
            "bili_tier": None,
        }


def _make_db(path, *, with_bili: bool):
    """A hand-rolled convo_search with (or without) the bili columns."""
    cols = [
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
        ("embedding", f"FLOAT[{DIMS}]"),
        ("provenance", "VARCHAR"),
    ]
    if with_bili:
        cols += [("bili_active", "BOOLEAN"), ("bili_tier", "INTEGER")]
    cols += [("content_hash", "VARCHAR")]
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE convo_search (" + ", ".join(f"{n} {t}" for n, t in cols) + ")")
    con.execute("CREATE TABLE convo_meta (key VARCHAR, value VARCHAR)")
    return con


class TestBiliMigration:
    def test_detects_missing_columns(self, tmp_path):
        db = tmp_path / "x.duckdb"
        _make_db(db, with_bili=False).close()
        assert rcs._bili_column_migration(db) is True

    def test_is_idempotent(self, tmp_path):
        db = tmp_path / "x.duckdb"
        _make_db(db, with_bili=False).close()
        assert rcs._bili_column_migration(db) is True
        assert rcs._bili_column_migration(db) is False, "second pass must be a no-op"

    def test_absent_index_needs_no_migration(self, tmp_path):
        assert rcs._bili_column_migration(tmp_path / "nope.duckdb") is False

    def test_ddl_and_stage_schema_carry_the_columns(self):
        ddl = rcs._convo_search_ddl(DIMS)
        for col in ("bili_active", "bili_tier"):
            assert col in ddl
        names = [n for n, _ in rcs._STAGE_SCHEMA_FIELDS]
        assert "bili_active" in names and "bili_tier" in names


def _corpus(tmp_path, name: str, rows: list[dict]) -> Path:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    base = {
        "session_id": "ses_x",
        "part_type": "compression-summary",
        "text": "body " * 40,
        "role": "assistant",
        "ts": None,
    }
    table = pa.Table.from_pylist([{**base, **r} for r in rows])
    pq.write_table(table, f)
    return f


def _meta_json(active, tier):
    parts = []
    if active is not None:
        parts.append(f'"active": {str(active).lower()}')
    if tier is not None:
        parts.append(f'"tier": {tier}')
    return "{" + ", ".join(parts) + "}"


class TestBiliBackfill:
    def _index_rows(self, db, file_path: str, n: int):
        con = duckdb.connect(str(db))
        for i in range(n):
            con.execute(
                "INSERT INTO convo_search (harness, session_id, part_id, file_path, "
                "row_no, role, part_type, snippet, agent, provenance) "
                "VALUES ('opencode','ses_x',?,?,?,'assistant','compression-summary','s','bili','')",
                [f"p{i}", file_path, i],
            )
        con.close()

    def _values(self, db):
        rows = (
            duckdb.connect(str(db))
            .execute("SELECT part_id, bili_active, bili_tier FROM convo_search ORDER BY part_id")
            .fetchall()
        )
        return {r[0]: (r[1], r[2]) for r in rows}

    def test_fills_bili_rows_and_leaves_non_bili_null(self, tmp_path, monkeypatch):
        db = tmp_path / "x.duckdb"
        con = _make_db(db, with_bili=False)
        for pid, agent in (("p0", "bili"), ("p1", "build")):
            con.execute(
                "INSERT INTO convo_search (harness, session_id, part_id, file_path, row_no, "
                "role, part_type, snippet, agent, provenance) "
                "VALUES ('opencode','ses_x',?,'f',0,'assistant','compression-summary','s',?,'')",
                [pid, agent],
            )
        con.close()
        f = _corpus(
            tmp_path,
            "f",
            [
                {"part_id": "p0", "agent": "bili", "meta": _meta_json(True, 1)},
                {"part_id": "p1", "agent": "build", "meta": _meta_json(True, 1)},
            ],
        )
        monkeypatch.setattr(rcs, "REPO", tmp_path)
        assert rcs._bili_column_migration(db) is True
        n_rows, n_files = rcs._backfill_bili(db, [f])
        assert (n_rows, n_files) == (1, 1)
        vals = self._values(db)
        assert vals["p0"] == (True, 1)
        assert vals["p1"] == (None, None), "non-bili rows must stay NULL"

    def test_backfill_is_row_order_independent(self, tmp_path, monkeypatch):
        """The (file_path, part_id) join doctrine: a parquet rewritten in a
        different row order must not swap meta values between rows."""
        db = tmp_path / "x.duckdb"
        con = _make_db(db, with_bili=False)
        for i in range(3):
            con.execute(
                "INSERT INTO convo_search (harness, session_id, part_id, file_path, "
                "row_no, role, part_type, snippet, agent, provenance) "
                "VALUES ('opencode','ses_x',?,?,?,'assistant','compression-summary','s','bili','')",
                [f"p{i}", "f", i],
            )
        con.close()
        # parquet rows deliberately NOT in p0,p1,p2 order
        f = _corpus(
            tmp_path,
            "f",
            [
                {"part_id": "p2", "agent": "bili", "meta": _meta_json(False, 3)},
                {"part_id": "p0", "agent": "bili", "meta": _meta_json(True, 1)},
                {"part_id": "p1", "agent": "bili", "meta": _meta_json(True, 2)},
            ],
        )
        monkeypatch.setattr(rcs, "REPO", tmp_path)
        rcs._bili_column_migration(db)
        rcs._backfill_bili(db, [f])
        assert self._values(db) == {"p0": (True, 1), "p1": (True, 2), "p2": (False, 3)}

    def test_garbled_meta_stays_null(self, tmp_path, monkeypatch):
        db = tmp_path / "x.duckdb"
        con = _make_db(db, with_bili=False)
        con.execute(
            "INSERT INTO convo_search (harness, session_id, part_id, file_path, row_no, "
            "role, part_type, snippet, agent, provenance) "
            "VALUES ('opencode','ses_x','p0','f',0,'assistant','compression-summary','s','bili','')"
        )
        con.close()
        f = _corpus(
            tmp_path,
            "f",
            [{"part_id": "p0", "agent": "bili", "meta": "not json {"}],
        )
        monkeypatch.setattr(rcs, "REPO", tmp_path)
        rcs._bili_column_migration(db)
        rcs._backfill_bili(db, [f])
        assert self._values(db)["p0"] == (None, None)


# -- query side ---------------------------------------------------------------

# (part_id, agent, part_type, bili_active, bili_tier, embedding, text)
_BILI_ROWS = [
    # part_type 'text' (prior 1.0) — a 'tool' row would carry the 0.45
    # TOOL_PRIOR and lose to the summaries before the partition even runs,
    # which would make the plain-order assertion below vacuous.
    ("t1", "build", "text", None, None, [1.0, 0.0, 0.0, 0.0], "embed cache tool call"),
    # summaries deliberately do NOT text-match the query ('embed cache') so
    # the FTS leg only sees t1 — the partition must still promote them via
    # the cosine leg, which is the discriminating case for summary_first.
    (
        "b1",
        "bili",
        "compression-summary",
        True,
        1,
        [0.9, 0.1, 0.0, 0.0],
        "storage layer decision record",
    ),
    (
        "b2",
        "bili",
        "compression-summary",
        False,
        1,
        [0.8, 0.1, 0.0, 0.0],
        "older storage layer note",
    ),
]


def _store(tmp_path):
    """duckdb index + sqlite FTS sidecar, hand-assembled, no embedder."""
    db = tmp_path / "c.duckdb"
    con = _make_db(db, with_bili=True)
    con.execute("INSERT INTO convo_meta VALUES ('embed_dims', ?)", [str(DIMS)])
    con.execute("INSERT INTO convo_meta VALUES ('embed_model', 'test-model')")
    for row_no, (pid, agent, part_type, active, tier, vec, text) in enumerate(_BILI_ROWS):
        con.execute(
            "INSERT INTO convo_search (harness, session_id, part_id, file_path, row_no, "
            "ts, role, agent, model, part_type, text_len, snippet, embedding, provenance, "
            "bili_active, bili_tier, content_hash) "
            "VALUES ('opencode','ses_x',?,'f.parquet',?,NULL,'assistant',?,'m',?,?,?,?,'',?,?,?)",
            [pid, row_no, agent, part_type, len(text), text[:80], vec, active, tier, pid],
        )
    con.close()
    sdb = tmp_path / cq.FTS_DB_NAME
    sconn = sqlite3.connect(str(sdb))
    sconn.execute(rcs.FTS_DDL)
    for _pid, _a, _pt, _act, _t, _v, text in _BILI_ROWS:
        sconn.execute("INSERT INTO convo_fts VALUES (?, 'opencode', ?, 'ses_x')", [text, _pid])
    sconn.commit()
    return cq.ConvoConnections(duckdb.connect(str(db), read_only=True), sconn, None)


def _qv():
    return QueryVector(vector=(1.0, 0.0, 0.0, 0.0), model="test-model", dims=DIMS)


class TestQueryLane:
    def test_rf_sql_fragments(self):
        params: list = []
        rf = cq.RowFilters(agent="bili", bili_active=True)
        sql = cq._rf_sql(rf, params)
        assert "agent = ?" in sql and "bili_active = ?" in sql
        assert "part_type" not in sql and "bili_tier" not in sql
        assert params == ["bili", True]

    def test_rf_sql_empty_when_no_filters(self):
        assert cq._rf_sql(cq.RowFilters(), []) == ""

    def test_row_mask_null_and_absent_semantics(self):
        table = pa.table(
            {
                "harness": ["opencode", "opencode", "opencode"],
                "part_id": ["t1", "b1", "b2"],
                "agent": ["build", "bili", "bili"],
                "bili_active": pa.array([None, True, False], type=pa.bool_()),
            }
        )
        entry = {
            "pc": pa.compute,  # _matrix_entry injects the module here
            "harness": table.column("harness"),
            "part_id": table.column("part_id"),
            "agent": table.column("agent"),
            "bili_active": table.column("bili_active"),
        }
        # NULL never matches either value
        got = cq._row_mask(entry, "opencode", None, cq.RowFilters(bili_active=True))
        assert got.tolist() == [False, True, False]
        got = cq._row_mask(entry, "opencode", None, cq.RowFilters(bili_active=False))
        assert got.tolist() == [False, False, True]
        # absent column: nothing can match
        bare = {"pc": pa.compute, "harness": entry["harness"], "part_id": entry["part_id"]}
        got = cq._row_mask(bare, "opencode", None, cq.RowFilters(bili_active=True))
        assert got is False

    def test_agent_filter_pulls_only_bili(self, tmp_path):
        con = _store(tmp_path)
        try:
            out = cq.search(
                con, "embed cache", limit=5, query_vec=_qv(), rf=cq.RowFilters(agent="bili")
            )
            hits = out["results"]
            assert [h["pointer"].rsplit(":", 1)[1] for h in hits] == ["b1", "b2"]
            assert all(h["part_type"] == "compression-summary" for h in hits)
        finally:
            con.close()

    def test_bili_active_null_never_matches(self, tmp_path):
        con = _store(tmp_path)
        try:
            out = cq.search(
                con, "embed cache", limit=5, query_vec=_qv(), rf=cq.RowFilters(bili_active=True)
            )
            assert [h["pointer"].rsplit(":", 1)[1] for h in out["results"]] == ["b1"]
            out = cq.search(
                con,
                "embed cache",
                limit=5,
                query_vec=_qv(),
                rf=cq.RowFilters(bili_active=True, bili_tier=2),
            )
            assert out["results"] == [], "no tier-2 rows exist in this store"
        finally:
            con.close()

    def test_summary_first_partitions_not_reranks(self, tmp_path):
        con = _store(tmp_path)
        try:
            out = cq.search(con, "embed cache", limit=5, query_vec=_qv(), summary_first=True)
            order = [h["pointer"].rsplit(":", 1)[1] for h in out["results"]]
            # summaries b1/b2 keep their fused order and move ahead of t1
            assert order.index("b1") < order.index("t1")
            assert order.index("b2") < order.index("t1")
            # without the flag the closest row leads (a partition, not a boost)
            plain = cq.search(con, "embed cache", limit=5, query_vec=_qv())
            assert plain["results"][0]["pointer"].rsplit(":", 1)[1] == "t1"
        finally:
            con.close()
