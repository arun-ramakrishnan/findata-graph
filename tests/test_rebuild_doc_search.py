"""
Tests for helpers/maintenance/rebuild_doc_search.py — the sidecar FTS5+embed
index over the repo's own doc/ corpus (proposal:
doc/improvements/archive/tooling/doc_search_embeddings.md).

These tests build a tiny synthetic doc/ tree under tmp_path, repoint the
module's DOC_ROOT + DOC_DB at it (the VAULT_ROOT lesson: never index the
live tree from tests), run the rebuild, and assert chunking, incremental
diffing, staleness probing, and the hybrid query core. The local embedder
is faked hermetically (fake_local, mirroring test_rebuild_note_search.py);
under the autouse _no_local_embedder pin the pseudo path is exercised.
"""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

# Make the helpers importable (tests run from repo root; this mirrors conftest).
REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "helpers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from helpers.core.zstd_io import decompress_file  # noqa: E402
from helpers.maintenance import rebuild_doc_search as rds  # noqa: E402

pytestmark = [pytest.mark.integration]


# --- fixtures ---------------------------------------------------------------

_ARCHITECTURE = (
    "# Architecture Guide\n"
    "\n"
    "Intro prose about the pipeline alpha.\n"
    "\n"
    "## Ingest\n"
    "Body about parsing newsletters and cache warmup.\n"
    "\n"
    "### Sub detail\n"
    "Deeper notes that must stay in the Ingest chunk.\n"
    "\n"
    "## Graph\n"
    "Edges and centrality beta notes.\n"
)

_GRAPH_TXT = "Graph Design Notes\n\nPlain text about graphs and graph builders.\n"

_PRIVATE_LOCAL_DOC = (
    "# Secret Review\n\n## Hidden Assessment\nprivate alpha assessment about security\n"
)


@pytest.fixture
def seeded_docs(tmp_path, monkeypatch):
    """Build a synthetic doc/ tree; repoint the module's DOC_ROOT + DOC_DB."""
    doc_root = tmp_path / "doc"
    (doc_root / "local").mkdir(parents=True)
    # Post-S1 layout: design docs live in doc/design/ (tmp tree mirrors live).
    (doc_root / "design").mkdir()
    (doc_root / "design" / "architecture.md").write_text(_ARCHITECTURE, encoding="utf-8")
    (doc_root / "design" / "graph_design.txt").write_text(_GRAPH_TXT, encoding="utf-8")
    (doc_root / "local" / "secret.md").write_text(_PRIVATE_LOCAL_DOC, encoding="utf-8")
    (doc_root / "empty.md").write_text("", encoding="utf-8")
    # Non-doc extension: must be skipped by the walk.
    (doc_root / "schema.json").write_text("{}\n", encoding="utf-8")

    db_path = tmp_path / "doc_search.db"
    backup_dir = tmp_path / "db-backup"
    monkeypatch.setattr(rds, "DOC_ROOT", doc_root)
    monkeypatch.setattr(rds, "DOC_DB", db_path)
    monkeypatch.setattr(rds, "BACKUP_DIR", backup_dir)
    return db_path


# Deterministic fake 8-dim embedder with keyword buckets, so cosine is
# meaningful in tests: a query containing "cache" lands near docs that do.
_KW = {"alpha": 0, "beta": 1, "cache": 2, "graph": 3, "security": 4}


def _fake_vec(text: str) -> list[float]:
    import math as _math

    v = [0.0] * 8
    low = text.lower()
    for kw, idx in _KW.items():
        if kw in low:
            v[idx] = 1.0
    n = _math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture
def fake_local(monkeypatch):
    """Make local_embedder claim availability + serve fake 8-dim vectors
    sharing one vector space across the document and query sides."""
    from helpers.core import local_embedder as LE

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _fake_vec)
    monkeypatch.setattr(LE, "embed_query", _fake_vec)
    monkeypatch.setattr(LE, "DIM", 8)
    return LE


def _conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _rows(con, query, args=()):
    return [tuple(r) for r in con.execute(query, args)]


# --- chunking (pure unit) ----------------------------------------------------


class TestSplitSections:
    def test_no_headers_single_chunk(self):
        chunks = rds._split_sections("just text\nmore text\n")
        assert chunks == [("", 1, "just text\nmore text\n")]

    def test_preamble_then_sections(self):
        text = "intro\n\n## A\nbody a\n\n## B\nbody b\n"
        chunks = rds._split_sections(text)
        assert [c[0] for c in chunks] == ["", "A", "B"]
        assert [c[1] for c in chunks] == [1, 3, 6]

    def test_h3_does_not_split(self):
        text = "## Top\n### Nested\nstill top\n"
        chunks = rds._split_sections(text)
        assert len(chunks) == 1
        assert chunks[0][0] == "Top"
        assert "### Nested" in chunks[0][2]

    def test_header_line_included_in_body(self):
        chunks = rds._split_sections("## Only\nbody\n")
        assert chunks[0][2].startswith("## Only\n")

    def test_empty_text_no_chunks(self):
        assert rds._split_sections("") == []

    def test_whitespace_only_no_chunks(self):
        assert rds._split_sections("\n  \n") == []


# --- rebuild -----------------------------------------------------------------


class TestRebuild:
    def test_rebuild_creates_index_and_chunks(self, seeded_docs):
        stats = rds.rebuild(write=True)
        assert stats["total_files"] == 4  # schema.json skipped, empty.md counted
        assert stats["total_rows"] == 6  # arch 3 + txt 1 + secret 2 + empty 0
        con = _conn(seeded_docs)
        try:
            anchors = [
                r[0]
                for r in con.execute(
                    "SELECT anchor FROM doc_search WHERE file_path = 'doc/design/architecture.md' "
                    "ORDER BY anchor"
                )
            ]
            assert anchors == [1, 5, 11]
            # The ### subsection stays inside the ## Ingest chunk.
            ingest = con.execute(
                "SELECT content FROM doc_search "
                "WHERE file_path = 'doc/design/architecture.md' AND section_title = 'Ingest'"
            ).fetchone()
            assert "Sub detail" in ingest[0]
            # .txt without headers: one chunk, empty section_title, txt title rule.
            txt = con.execute(
                "SELECT title, section_title, anchor FROM doc_search "
                "WHERE file_path = 'doc/design/graph_design.txt'"
            ).fetchone()
            assert txt[0] == "Graph Design Notes"
            assert txt[1] == ""
            # doc/local/ IS in scope (2026-08-23 user decision).
            n_secret = con.execute(
                "SELECT COUNT(*) FROM doc_search WHERE file_path = 'doc/local/secret.md'"
            ).fetchone()[0]
            assert n_secret == 2
            # Empty file: fingerprinted in meta, zero rows.
            n_empty = con.execute(
                "SELECT COUNT(*) FROM doc_search WHERE file_path = 'doc/empty.md'"
            ).fetchone()[0]
            assert n_empty == 0
            in_meta = con.execute(
                "SELECT COUNT(*) FROM doc_search_meta WHERE file_path = 'doc/empty.md'"
            ).fetchone()[0]
            assert in_meta == 1
        finally:
            con.close()

    def test_idempotent_full_rebuild(self, seeded_docs):
        rds.rebuild(write=True)
        stats = rds.rebuild(write=True)
        assert stats["content_changed"] is False
        con = _conn(seeded_docs)
        try:
            n = con.execute("SELECT COUNT(*) FROM doc_search").fetchone()[0]
            assert n == 6
        finally:
            con.close()

    def test_pseudo_fallback(self, seeded_docs, monkeypatch, capsys):
        # conftest autouse pin has available()->False here. Reset the
        # once-flag so earlier tests in this process don't consume the warning.
        monkeypatch.setattr(rds, "_pseudo_warned", False)
        stats = rds.rebuild(write=True)
        assert stats["embed_model"].startswith("dry-run-v")
        con = _conn(seeded_docs)
        try:
            row = con.execute(
                "SELECT embedding FROM doc_search WHERE embedding != '' LIMIT 1"
            ).fetchone()
            assert len(json.loads(row[0])) == 64
            # Model stamp describes content -> pseudo label recorded on apply.
            stamp = con.execute(
                "SELECT value FROM doc_search_info WHERE key = 'embed_model'"
            ).fetchone()
            assert stamp[0].startswith("dry-run-v")
        finally:
            con.close()
        assert capsys.readouterr().err.count("WARNING") == 1

    def test_check_writes_no_rows(self, seeded_docs, fake_local):
        stats = rds.rebuild(write=False)
        assert stats["total_rows"] == 6
        con = _conn(seeded_docs)
        try:
            # DDL ran (table exists) but no rows and no model stamp.
            assert con.execute("SELECT COUNT(*) FROM doc_search").fetchone()[0] == 0
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM doc_search_info WHERE key = 'embed_model'"
                ).fetchone()[0]
                == 0
            )
        finally:
            con.close()

    def test_check_fresh_verdict_exit_0(self, seeded_docs, fake_local, capsys):
        rds.rebuild(write=True)
        rc = rds.main(["--check"])
        assert rc == 0
        assert "index state: FRESH" in capsys.readouterr().err

    def test_check_fresh_after_mtime_drift(self, seeded_docs, fake_local, capsys):
        """Worktree/checkout regression (2026-08-30): mtime skew on
        identical content must stay FRESH — the content hash is the
        identity of record, mtime only the carry fast path (shared index
        DBs see per-checkout mtimes)."""
        rds.rebuild(write=True)
        future = time.time() + 1000
        for p in Path(rds.DOC_ROOT).rglob("*"):
            if p.is_file():
                os.utime(p, (future, future))
        rc = rds.main(["--check"])
        assert rc == 0
        assert "index state: FRESH" in capsys.readouterr().err

    def test_incremental_noop_after_mtime_drift(self, seeded_docs, fake_local):
        """Same skew through the APPLY path: nothing re-upserts, so the
        other checkout's stored meta is never churned."""
        rds.rebuild(write=True)
        future = time.time() + 1000
        for p in Path(rds.DOC_ROOT).rglob("*"):
            if p.is_file():
                os.utime(p, (future, future))
        stats = rds.rebuild(write=True, incremental=True)
        assert stats["upserts"] == 0
        assert stats["deletes"] == 0

    def test_check_reports_changed_and_exits_1(self, seeded_docs, fake_local, capsys):
        rds.rebuild(write=True)
        secret = Path(rds.DOC_ROOT) / "local" / "secret.md"
        secret.write_text(
            secret.read_text(encoding="utf-8") + "\n## Late\nedited section\n",
            encoding="utf-8",
        )
        future = time.time() + 10
        os.utime(secret, (future, future))

        stats = rds.rebuild(write=False)
        assert stats["index_stale"] is True
        assert stats["stale_changed"] == ["doc/local/secret.md"]
        assert stats["stale_new"] == []
        assert stats["stale_deleted"] == []
        rc = rds.main(["--check"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "index state: STALE" in err
        assert "1 changed, 0 new, 0 deleted" in err
        assert "doc/local/secret.md" in err
        assert "refresh: python3 helpers/maintenance/rebuild_doc_search.py" in err

    def test_check_reports_new_and_deleted(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        (Path(rds.DOC_ROOT) / "design" / "graph_design.txt").unlink()
        (Path(rds.DOC_ROOT) / "fresh_doc.md").write_text("## Brand\nnew\n", encoding="utf-8")
        stats = rds.rebuild(write=False)
        assert stats["stale_new"] == ["doc/fresh_doc.md"]
        assert stats["stale_deleted"] == ["doc/design/graph_design.txt"]
        assert stats["stale_changed"] == []

    def test_check_without_index_reports_all_new(self, seeded_docs):
        stats = rds.rebuild(write=False)
        assert stats["index_stale"] is True
        assert len(stats["stale_new"]) == stats["total_files"]
        assert stats["stale_deleted"] == []

    def test_check_same_mtime_edit_detected_by_hash(self, seeded_docs, fake_local):
        """The hash leg catches same-mtime content edits the mtime probe
        can't see — that's why --check is the deep diagnostic."""
        rds.rebuild(write=True)
        guide = Path(rds.DOC_ROOT) / "design" / "architecture.md"
        mtime = guide.stat().st_mtime
        guide.write_text(
            guide.read_text(encoding="utf-8") + "\n## Sneaky\nsame-mtime edit\n",
            encoding="utf-8",
        )
        os.utime(guide, (mtime, mtime))  # pin the mtime back
        stats = rds.rebuild(write=False)
        assert stats["stale_changed"] == ["doc/design/architecture.md"]


# --- db-backup recovery point -------------------------------------------------


class TestLastGoodIndexBackup:
    def _backup(self):
        return Path(rds.BACKUP_DIR) / "doc_search_backup.db.zst"

    def test_full_rebuild_writes_backup(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        backup = self._backup()
        assert backup.exists()
        plain = Path(rds.BACKUP_DIR) / "doc_search_backup_roundtrip.db"
        decompress_file(backup, plain)
        con = _conn(plain)
        try:
            assert con.execute("SELECT COUNT(*) FROM doc_search").fetchone()[0] == 6
        finally:
            con.close()

    def test_check_and_incremental_skip_backup(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        backup = self._backup()
        stamp = backup.stat().st_mtime
        time.sleep(0.01)
        rds.rebuild(write=False)  # --check
        rds.rebuild(write=True, incremental=True)
        assert backup.stat().st_mtime == stamp  # untouched by both

    def test_backup_restorable(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        backup = self._backup()
        seeded_docs.unlink()  # catastrophe: the index db is lost
        decompress_file(backup, seeded_docs)
        con = rds.connect_doc_db(seeded_docs)
        try:
            assert rds.doc_index_ready(con)
            assert not rds.doc_index_stale(con)
            out = rds.search_docs(con, "cache warmup")
            assert out["results"]
        finally:
            con.close()

    def test_incremental_upserts_only_changed(self, seeded_docs):
        rds.rebuild(write=True)
        secret = Path(rds.DOC_ROOT) / "local" / "secret.md"
        text = secret.read_text(encoding="utf-8") + "\n## More\nnew beta section\n"
        secret.write_text(text, encoding="utf-8")
        future = time.time() + 10
        os.utime(secret, (future, future))

        stats = rds.rebuild(write=True, incremental=True)
        assert stats["mode"] == "incremental"
        assert stats["upserts"] == 1
        assert stats["deletes"] == 0
        con = _conn(seeded_docs)
        try:
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM doc_search WHERE file_path = 'doc/local/secret.md'"
                ).fetchone()[0]
                == 3
            )
            # Untouched files keep their rows.
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM doc_search WHERE file_path = 'doc/design/architecture.md'"
                ).fetchone()[0]
                == 3
            )
            assert con.execute("SELECT COUNT(*) FROM doc_search").fetchone()[0] == 7
        finally:
            con.close()

    def test_incremental_carries_unchanged(self, seeded_docs):
        rds.rebuild(write=True)
        stats = rds.rebuild(write=True, incremental=True)
        assert stats["upserts"] == 0
        assert stats["deletes"] == 0

    def test_incremental_deletes_removed_file(self, seeded_docs):
        rds.rebuild(write=True)
        (Path(rds.DOC_ROOT) / "design" / "graph_design.txt").unlink()
        stats = rds.rebuild(write=True, incremental=True)
        assert stats["deletes"] == 1
        con = _conn(seeded_docs)
        try:
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM doc_search WHERE file_path = 'doc/design/graph_design.txt'"
                ).fetchone()[0]
                == 0
            )
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM doc_search_meta WHERE file_path = 'doc/design/graph_design.txt'"
                ).fetchone()[0]
                == 0
            )
        finally:
            con.close()

    def test_full_rebuild_gcs_deleted_file(self, seeded_docs):
        rds.rebuild(write=True)
        (Path(rds.DOC_ROOT) / "local" / "secret.md").unlink()
        rds.rebuild(write=True)
        con = _conn(seeded_docs)
        try:
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM doc_search WHERE file_path = 'doc/local/secret.md'"
                ).fetchone()[0]
                == 0
            )
            assert con.execute("SELECT COUNT(*) FROM doc_search").fetchone()[0] == 4
        finally:
            con.close()

    def test_model_stamp_with_local(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        con = _conn(seeded_docs)
        try:
            model = con.execute(
                "SELECT value FROM doc_search_info WHERE key = 'embed_model'"
            ).fetchone()[0]
            dims = con.execute(
                "SELECT value FROM doc_search_info WHERE key = 'embed_dims'"
            ).fetchone()[0]
            assert model == "bge-small-en-v1.5"
            assert dims == "8"
            row = con.execute(
                "SELECT embedding FROM doc_search WHERE embedding != '' LIMIT 1"
            ).fetchone()
            assert len(json.loads(row[0])) == 8
        finally:
            con.close()


# --- match-expression safety ---------------------------------------------------


class TestFtsMatchExpr:
    def test_punctuated_query_is_quoted(self):
        expr = rds.fts_match_expr("duckpgq retirement (Phase E)")
        assert expr == '"duckpgq" OR "retirement" OR "(Phase" OR "E)"'

    def test_inner_quotes_stripped(self):
        assert rds.fts_match_expr('say "hi"') == '"say" OR "hi"'

    def test_empty_query(self):
        assert rds.fts_match_expr("   ") == ""

    def test_or_not_and(self):
        # Question-shaped queries must not require every token to co-occur
        # in one chunk (the eval's langgraph lesson).
        assert rds.fts_match_expr("why did we not adopt langgraph").count(" OR ") == 5


# --- query core ----------------------------------------------------------------


class TestSearchDocs:
    def _built(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        return rds.connect_doc_db(seeded_docs)

    def test_search_ranked_with_snippet(self, seeded_docs, fake_local):
        con = self._built(seeded_docs, fake_local)
        try:
            out = rds.search_docs(con, "cache warmup")
            assert out["mode"] == "hybrid"
            top = out["results"][0]
            assert top["path"] == "doc/design/architecture.md"
            assert top["section_title"] == "Ingest"
            assert top["anchor"] == 5
            assert "<mark>" in top["snippet"]
            assert "similarity" in top
        finally:
            con.close()

    def test_result_shape_compat(self, seeded_docs, fake_local):
        con = self._built(seeded_docs, fake_local)
        try:
            out = rds.search_docs(con, "alpha", limit=10)
            paths = {r["path"] for r in out["results"]}
            assert "doc/design/architecture.md" in paths  # preamble mentions alpha
            assert "doc/local/secret.md" in paths
            for r in out["results"]:
                # The #107 /api/docs/search response contract fields.
                assert {"path", "name", "section", "title", "snippet"} <= set(r)
                assert {"section_title", "anchor", "score"} <= set(r)
            secret = next(r for r in out["results"] if r["path"] == "doc/local/secret.md")
            assert secret["section"] == "local"
            arch = next(r for r in out["results"] if r["path"] == "doc/design/architecture.md")
            assert arch["section"] == "design"  # post-S1: design docs live one level down
        finally:
            con.close()

    def test_dims_mismatch_degrades_to_bm25(self, seeded_docs, fake_local, monkeypatch):
        con = self._built(seeded_docs, fake_local)
        try:
            monkeypatch.setattr(fake_local, "embed_query", lambda t: [0.0] * 16)
            out = rds.search_docs(con, "cache warmup")
            assert out["mode"] == "bm25"
            assert out["results"]  # lexical leg still serves
            assert out["results"][0]["similarity"] is None
        finally:
            con.close()

    def test_limit_offset(self, seeded_docs, fake_local):
        con = self._built(seeded_docs, fake_local)
        try:
            full = rds.search_docs(con, "alpha", limit=10)
            assert len(full["results"]) >= 2
            paged = rds.search_docs(con, "alpha", limit=1, offset=1)
            assert len(paged["results"]) == 1
            assert paged["results"][0]["path"] == full["results"][1]["path"]
        finally:
            con.close()

    def test_empty_query_no_results(self, seeded_docs, fake_local):
        con = self._built(seeded_docs, fake_local)
        try:
            assert rds.search_docs(con, "   ")["results"] == []
        finally:
            con.close()

    def test_porter_stemming(self, seeded_docs, fake_local):
        con = self._built(seeded_docs, fake_local)
        try:
            # "edges" stem-matches content containing "Edges".
            out = rds.search_docs(con, "edges")
            assert any(r["section_title"] == "Graph" for r in out["results"])
        finally:
            con.close()


# --- staleness -----------------------------------------------------------------


class TestStaleness:
    def test_fresh_index_not_stale(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        con = rds.connect_doc_db(seeded_docs)
        try:
            assert rds.doc_index_ready(con)
            assert not rds.doc_index_stale(con)
        finally:
            con.close()

    def test_touched_file_stale(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        arch = Path(rds.DOC_ROOT) / "design" / "architecture.md"
        future = time.time() + 10
        os.utime(arch, (future, future))
        con = rds.connect_doc_db(seeded_docs)
        try:
            assert rds.doc_index_stale(con)
        finally:
            con.close()

    def test_new_file_stale(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        (Path(rds.DOC_ROOT) / "new_doc.md").write_text("## New\nbody\n", encoding="utf-8")
        con = rds.connect_doc_db(seeded_docs)
        try:
            assert rds.doc_index_stale(con)
        finally:
            con.close()

    def test_deleted_file_stale(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        (Path(rds.DOC_ROOT) / "design" / "architecture.md").unlink()
        con = rds.connect_doc_db(seeded_docs)
        try:
            assert rds.doc_index_stale(con)
        finally:
            con.close()

    def test_empty_sidecar_not_ready(self, seeded_docs):
        con = rds.connect_doc_db(seeded_docs)
        try:
            assert not rds.doc_index_ready(con)
            assert rds.doc_index_stale(con)
        finally:
            con.close()
