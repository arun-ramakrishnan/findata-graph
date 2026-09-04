#!/usr/bin/env python3
"""Integration tests for the `query.py near-duplicates` CLI tripwire
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §4 A4).

test_note_embeddings.py covers the wrapper; this drives the real `_cli`
dispatch (`make near-duplicates`) over a tmp DB whose note_search rows
carry hand-set embedding vectors, so the cosine threshold semantics are
exact rather than hash-dependent. The CLI must also be read-only on the
SQLite side (it is a QA tripwire, not a writer).
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest


from helpers.graph import query  # noqa: E402
from helpers.graph.query import DB_PATH  # noqa: E402
from tests.helpers import copy_production_db  # noqa: E402

pytestmark = [pytest.mark.integration]

duckdb = pytest.importorskip("duckdb")

import math  # noqa: E402

# Hand-set unit vectors in 3-D: A and B IDENTICAL (sim 1.0), D at
# cos(A,D)=0.95 (in the A/B plane), C and E on orthogonal axes (outside
# every qualifying pair). With --min-sim 0.9 exactly the pairs
# (A,B), (A,D), (B,D) qualify — pairwise angles, not just angle-to-A,
# are what the threshold sees.
_X = math.sqrt(1 - 0.95 * 0.95)
_VECS = {
    "findata/Companies/A.md": [1.0, 0.0, 0.0],
    "findata/Companies/B.md": [1.0, 0.0, 0.0],
    "findata/Companies/C.md": [0.0, 1.0, 0.0],
    "findata/Companies/D.md": [0.95, _X, 0.0],
    "findata/Companies/E.md": [0.0, 0.0, 1.0],
}


def _make_db(db_path: Path) -> None:
    """Production-schema DB with 5 company note_search rows whose embedding
    vectors are the controlled set above."""
    copy_production_db(DB_PATH, db_path)
    dst = sqlite3.connect(str(db_path))
    for stem in "ABCDE":
        dst.execute(
            "INSERT INTO entities (name, entity_type, file_path) VALUES (?, 'company', ?)",
            (f"Co {stem}", f"findata/Companies/{stem}.md"),
        )
    for path, vec in _VECS.items():
        dst.execute(
            "INSERT INTO note_search (doc_type, file_path, title, sector, "
            "content, embedding) VALUES ('company', ?, ?, '', 'body', ?)",
            (path, path.split("/")[-1][:-3], repr(vec).replace("'", '"')),
        )
    dst.commit()
    dst.close()


def _run(monkeypatch, db: Path, *args: str) -> tuple[int, str, str]:
    """Drive query._cli('near-duplicates') against a tmp DB."""
    real_connect = query.connect

    def _connect(*a, **k):
        k.pop("db_path", None)
        return real_connect(db, *a, **k)

    monkeypatch.setattr(query, "connect", _connect)
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = query._cli(["near-duplicates", *args])
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def dup_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("near_dup") / "dup.db"
    _make_db(db)
    con = query.connect(db, fresh=True)  # materialise v_note_embeddings once
    con.close()
    return db


class TestNearDuplicatesCli:
    def test_reports_only_pairs_above_threshold(self, dup_db, monkeypatch):
        rc, out, err = _run(monkeypatch, dup_db, "--min-sim", "0.9")
        assert rc == 0
        # Titles are the note stems; exactly the three qualifying pairs.
        lines = {ln.strip() for ln in out.splitlines() if "<->" in ln}
        assert lines == {
            "1.0000  A  <->  B",
            "0.9500  A  <->  D",
            "0.9500  B  <->  D",
        }
        assert "(3 pair(s))" in err

    def test_min_sim_one_reports_only_identical(self, dup_db, monkeypatch):
        rc, out, err = _run(monkeypatch, dup_db, "--min-sim", "1.0")
        assert rc == 0
        assert "(1 pair(s))" in err
        assert "1.0000" in out

    def test_pairs_sorted_desc_and_unique(self, dup_db, monkeypatch):
        rc, out, _ = _run(monkeypatch, dup_db, "--min-sim", "0.9")
        sims = [float(ln.split()[0]) for ln in out.splitlines() if "<->" in ln]
        assert sims == sorted(sims, reverse=True)
        assert len(sims) == len(set(sims)) or True  # sims may tie; pairs unique
        pairs = [ln.split("<->") for ln in out.splitlines() if "<->" in ln]
        norm = {tuple(sorted(p)) for p in pairs}
        assert len(norm) == len(pairs)  # each unordered pair once

    def test_sqlite_side_is_read_only(self, dup_db, monkeypatch):
        """The tripwire must never write the SQLite DB (read-derived cache
        only): file bytes + row contents identical before/after."""
        before = hashlib.md5(dup_db.read_bytes(), usedforsecurity=False).hexdigest()
        conn = sqlite3.connect(str(dup_db))
        rows_before = conn.execute(
            "SELECT file_path, title, embedding FROM note_search ORDER BY file_path"
        ).fetchall()
        conn.close()
        rc, _, _ = _run(monkeypatch, dup_db, "--min-sim", "0.9")
        assert rc == 0
        assert hashlib.md5(dup_db.read_bytes(), usedforsecurity=False).hexdigest() == before
        conn = sqlite3.connect(str(dup_db))
        rows_after = conn.execute(
            "SELECT file_path, title, embedding FROM note_search ORDER BY file_path"
        ).fetchall()
        conn.close()
        assert rows_after == rows_before

    def test_empty_index_degrades_cleanly(self, tmp_path, monkeypatch):
        db = tmp_path / "empty.db"
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(db))
        src.backup(dst)
        src.close()
        for t in ("note_search", "note_search_meta"):
            dst.execute(f"DELETE FROM {t}")  # noqa: S608  # schema-constant identifiers
        dst.commit()
        dst.close()
        con = query.connect(db, fresh=True)
        con.close()
        rc, out, err = _run(monkeypatch, db)
        assert rc == 0
        assert "no note pairs above" in out
        assert "(0 pair(s))" in err
