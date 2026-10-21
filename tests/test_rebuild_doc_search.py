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

import os
import sqlite3
import time
from pathlib import Path

import pytest

# Make the helpers importable (tests run from repo root; this mirrors conftest).

from helpers.core.vec_codec import load_vec  # noqa: E402
from helpers.maintenance import rebuild_common as rbc  # noqa: E402
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
    monkeypatch.setattr(rds, "DOC_ROOT", doc_root)
    monkeypatch.setattr(rds, "DOC_DB", db_path)
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


# --- S7 field enrichment -----------------------------------------------------


class TestEnrichment:
    def test_extraction_meta_links_trail(self):
        text = (
            "---\n"
            "title: Widget Audit\n"
            "status: executed\n"
            "area: helpers/misc\n"
            "extra: ignored\n"
            "---\n"
            "# Widget Audit Guide\n"
            "\n"
            "Body without the secret token.\n"
            "\n"
            "See [the docs](doc/procedures/x.md) and [upstream](https://out.sa) "
            "and [anchor](#local) and ![pic](x.png).\n"
            "**Follows**: `doc/improvements/proposals/w.md`\n"
            "Recorded in completed.md #12.\n"
        )
        meta, links, trail = rds._file_enrichment(text)
        assert meta == "Widget Audit executed helpers/misc"
        assert trail == "Widget Audit Guide"
        # real link + ref lines kept; http/anchor/image targets skipped
        assert "the docs doc/procedures/x.md" in links
        assert "Follows" in links and "completed.md #12" in links
        assert "https://out.sa" not in links
        assert "(#local)" not in links
        assert "x.png" not in links

    def test_extraction_links_cap_900(self):
        text = "# T\n" + "\n".join(f"[l{i}](doc/f{i}.md)" for i in range(200))
        _meta, links, _trail = rds._file_enrichment(text)
        assert len(links) == 900
        assert "l0" in links

    def test_meta_column_is_matchable(self, seeded_docs, fake_local):
        """A token living ONLY in frontmatter (nowhere in body/title/h1)
        must find the file through the indexed meta column (S7's point):
        frontmatter is the ticker-analog findability lever."""
        (Path(rds.DOC_ROOT) / "design" / "quuxer.md").write_text(
            "---\n"
            "title: Zorblaster quadrux pipeline\n"
            "status: executed\n"
            "---\n"
            "# Quuxer Guide\n"
            "\n"
            "Ordinary body prose about nothing in particular.\n",
            encoding="utf-8",
        )
        rds.rebuild(write=True)
        con = rds.connect_doc_db(seeded_docs)
        try:
            out = rds.search_docs(con, "quadrux", hybrid=False)
            paths = [r["path"] for r in out["results"]]
            assert any("quuxer.md" in p for p in paths)
        finally:
            con.close()

    def test_schema_migration_drop_recreate(self, seeded_docs, fake_local):
        """A pre-S7 6-col index is dropped and recreated with the 9-col
        schema on the next rebuild (FTS5 can't ALTER ADD); the rebuild
        repopulates fully and search serves hybrid."""
        con = rds.connect_doc_db(seeded_docs)
        try:
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS doc_search USING fts5("
                "title, section_title, file_path UNINDEXED, anchor UNINDEXED, "
                "content, embedding UNINDEXED, tokenize = 'porter unicode61')"
            )
            con.execute(
                "INSERT INTO doc_search (title, section_title, file_path, anchor, "
                "content, embedding) VALUES ('old', 'old', 'doc/x.md', 1, 'old body', NULL)"
            )
            con.commit()
        finally:
            con.close()
        stats = rds.rebuild(write=True)
        assert stats["mode"] == "full"
        con = rds.connect_doc_db(seeded_docs)
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(doc_search)")]
            assert cols[-3:] == ["meta", "links", "trail"]
            out = rds.search_docs(con, "cache warmup")
            assert out["mode"] == "hybrid"
        finally:
            con.close()


class TestGemmaAdoption:
    """doc_search_gemma_adoption S1/S2 contract tests."""

    def test_basis_pin_byte_exact(self):
        """The adoption recipe, byte-exact (#386 pin): pure-prose form,
        `title: none` when absent, cap 4000."""
        assert (
            rds._doc_basis("Arch Guide", "Ingest", "body text", gemma=True)
            == "title: Arch Guide | text: Ingest\nbody text"
        )
        assert (
            rds._doc_basis("", "S", "x" * 5000, gemma=True)
            == "title: none | text: S\n" + "x" * 4000
        )
        # Legacy granite basis unchanged (rollback cache keys preserved).
        assert rds._doc_basis("T", "S", "b", gemma=False) == "T\nS\nb"

    def test_cache_keys_bind_model_and_basis(self):
        """The catastrophe-class audit (S1): a granite vector can never be
        served under a gemma key. Cache keys are (sha256(basis_text),
        model) — BOTH axes move the key, so a stamp flip re-embeds."""
        from helpers.core.embed_cache import _hash

        legacy = rds._doc_basis("T", "S", "body", gemma=False)
        gemma = rds._doc_basis("T", "S", "body", gemma=True)
        assert _hash(legacy) != _hash(gemma), "basis form must move the key"
        # (hash, model) is the PK: same text under two labels = two rows.
        assert rds.gemma_embedder.MODEL_LABEL != "granite-embedding-97m-r2"

    def test_resolve_selector_escapes(self, monkeypatch):
        """DOC_EMBEDDER=granite forces the legacy path even with gemma up;
        DOC_EMBEDDER=gemma forces gemma when available."""
        from helpers.core import gemma_embedder as gem

        monkeypatch.setattr(gem, "available", lambda: True)
        monkeypatch.setenv("DOC_EMBEDDER", "granite")
        fn, dims, label, gemma_active = rds.resolve_doc_embedder()
        assert gemma_active is False
        assert label != gem.MODEL_LABEL

        monkeypatch.setenv("DOC_EMBEDDER", "gemma")
        fn, dims, label, gemma_active = rds.resolve_doc_embedder()
        assert gemma_active is True
        assert label == gem.MODEL_LABEL
        assert dims == gem.DIM

    def test_guard_refuses_fallback_on_gemma_stamp(self, tmp_path):
        """A gemma-stamped index refuses a fallback-model rebuild write
        (the 2026-10-08 un-migration incident shape); DOC_EMBEDDER=granite
        is the explicit escape."""
        from helpers.core import gemma_embedder as gem

        with pytest.raises(gem.GemmaStampDemotion):
            gem.guard_gemma_stamp(gem.MODEL_LABEL, "granite-embedding-97m-r2", None)
        # Explicit escape passes; matched stamp passes.
        gem.guard_gemma_stamp(gem.MODEL_LABEL, "granite-embedding-97m-r2", "granite")
        gem.guard_gemma_stamp(gem.MODEL_LABEL, gem.MODEL_LABEL, None)

    def test_gemma_build_stamps_and_bases(self, seeded_docs, monkeypatch):
        """Full-pool gemma build: stamp lands, embedded texts carry the
        pinned pure-prose basis (never the legacy newline form)."""
        from helpers.core import gemma_embedder as gem

        seen_texts: list[str] = []

        def _fake_gemma_doc(text: str) -> list[float]:
            seen_texts.append(text)
            return [0.1] * gem.DIM

        monkeypatch.setattr(gem, "available", lambda: True)
        monkeypatch.setattr(gem, "embed_document", _fake_gemma_doc)
        monkeypatch.setattr(gem, "embed_query", lambda text, task="search": [0.2] * gem.DIM)
        monkeypatch.delenv("DOC_EMBEDDER", raising=False)
        stats = rds.rebuild(write=True)
        assert stats["embed_model"] == gem.MODEL_LABEL
        con = rds.connect_doc_db(seeded_docs)
        try:
            info = dict(con.execute("SELECT key, value FROM doc_search_info").fetchall())
            assert info["embed_model"] == gem.MODEL_LABEL
            assert info["embed_dims"] == str(gem.DIM)
            assert seen_texts
            assert all(t.startswith("title: ") for t in seen_texts)
        finally:
            con.close()

    def test_read_path_stamp_mismatch_matrix(self, seeded_docs, fake_local, monkeypatch):
        """S2: mismatched spaces are never dotted. QV/stamp disagreement
        drops the QV (note surfaced) and the leg re-embeds stamp-locally;
        a resolvable space serves hybrid, an unresolvable one (sidecar
        down) degrades to bm25 with the reason."""
        from helpers.core import gemma_embedder as gem

        rds.rebuild(write=True)  # granite-stamped index (fake_local)
        con = rds.connect_doc_db(seeded_docs)
        try:
            stamp = rds.stored_embed_model(con)
            assert stamp is not None  # fixture index is granite-stamped
            # gemma QV (512d) vs granite stamp (8d in the fixture): QV
            # dropped → local granite embed fits → hybrid + mismatch note.
            gqv = rbc.QueryVector(
                vector=tuple([0.3] * gem.DIM), model=gem.MODEL_LABEL, dims=gem.DIM
            )
            out = rds.search_docs(con, "cache warmup", query_vec=gqv)
            assert out["mode"] == "hybrid"
            assert "note" in out and "mismatch" in out["note"]
            # Matched-stamp QV serves hybrid with no note.
            lqv = rbc.QueryVector(vector=tuple(_fake_vec("cache warmup")), model=stamp, dims=8)
            out2 = rds.search_docs(con, "cache warmup", query_vec=lqv)
            assert out2["mode"] == "hybrid"
            assert "note" not in out2
        finally:
            con.close()

        # Gemma-stamped index (fake gemma embedder): granite QV dropped,
        # stamp-aware local gemma embed (512d) fits → hybrid + note.
        monkeypatch.setattr(gem, "available", lambda: True)
        monkeypatch.setattr(gem, "embed_document", lambda text: [0.1] * gem.DIM)
        monkeypatch.setattr(gem, "embed_query", lambda text, task="search": [0.2] * gem.DIM)
        monkeypatch.setenv("DOC_EMBEDDER", "gemma")
        try:
            stats = rds.rebuild(write=True)
        finally:
            monkeypatch.delenv("DOC_EMBEDDER", raising=False)
        assert stats["embed_model"] == gem.MODEL_LABEL
        con = rds.connect_doc_db(seeded_docs)
        try:
            granite_qv = rbc.QueryVector(
                vector=tuple(_fake_vec("cache warmup")),
                model="granite-embedding-97m-r2",
                dims=8,
            )
            out3 = rds.search_docs(con, "cache warmup", query_vec=granite_qv)
            assert out3["mode"] == "hybrid"
            assert "note" in out3 and "mismatch" in out3["note"]

            # Sidecar down on the gemma index: no resolvable space →
            # bm25-only with the reason.
            def _dead_query(text, task="search"):
                raise ConnectionError("sidecar down")

            monkeypatch.setattr(gem, "embed_query", _dead_query)
            out4 = rds.search_docs(con, "cache warmup", query_vec=granite_qv)
            assert out4["mode"] == "bm25"
            assert "note" in out4
        finally:
            con.close()


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
            assert len(load_vec(row[0]) or ()) == 64
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


# --- incremental upserts ------------------------------------------------------


class TestIncrementalUpserts:
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
            assert model == fake_local.MODEL_ID
            assert dims == "8"
            row = con.execute(
                "SELECT embedding FROM doc_search WHERE embedding != '' LIMIT 1"
            ).fetchone()
            assert len(load_vec(row[0]) or ()) == 8
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

    def test_touched_file_hash_equal_not_stale(self, seeded_docs, fake_local):
        """mtime_gate S1: a touch (or a cross-tree --allow-stale apply)
        skews mtimes on hash-equal content — the reader must hash-verify
        before judging, not flag stale. The 2026-10-13 live failure: a
        sanctioned cross-tree apply left every consumer scan-degraded.
        """
        rds.rebuild(write=True)
        arch = Path(rds.DOC_ROOT) / "design" / "architecture.md"
        future = time.time() + 10
        os.utime(arch, (future, future))
        con = rds.connect_doc_db(seeded_docs)
        try:
            assert not rds.doc_index_stale(con)
            # The cross-tree shape proper: meta itself carries the OTHER
            # tree's mtimes (older), disk has newer — neither matches.
            skewed = future - 5000
            con.execute("UPDATE doc_search_meta SET mtime = ?", (skewed,))
            con.commit()
            assert not rds.doc_index_stale(con)  # hash-equal ⇒ fresh
        finally:
            con.close()

    def test_content_change_stale(self, seeded_docs, fake_local):
        rds.rebuild(write=True)
        arch = Path(rds.DOC_ROOT) / "design" / "architecture.md"
        arch.write_text(
            _ARCHITECTURE.replace("centrality beta", "centrality gamma"),
            encoding="utf-8",
        )
        con = rds.connect_doc_db(seeded_docs)
        try:
            # mtime skew → hash-verify → hash differs ⇒ stale.
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

    def test_writer_heals_skewed_mtimes(self, seeded_docs, fake_local):
        """mtime_gate S2: an incremental apply over hash-equal files whose
        meta mtimes lag the disk (the cross-tree-apply residue) converges
        meta to disk — no upserts, no row churn, and the raw-mtime reader
        check passes afterwards.
        """
        rds.rebuild(write=True)
        con = rds.connect_doc_db(seeded_docs)
        try:
            meta_before = dict(con.execute("SELECT file_path, mtime FROM doc_search_meta"))
            rows_before = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(content)), 0) FROM doc_search"
            ).fetchone()
        finally:
            con.close()
        # Simulate the residue: disk mtimes moved on, meta still carries the
        # other tree's (older) stamps. Content untouched.
        future = time.time() + 100
        for p in Path(rds.DOC_ROOT).rglob("*"):
            os.utime(p, (future, future))
        stats = rds.rebuild(write=True, incremental=True)
        assert stats["upserts"] == 0
        assert stats["mtime_healed"] == len(meta_before)
        con = rds.connect_doc_db(seeded_docs)
        try:
            meta_after = dict(con.execute("SELECT file_path, mtime FROM doc_search_meta"))
            rows_after = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(content)), 0) FROM doc_search"
            ).fetchone()
            assert rows_before == rows_after  # zero row churn
            disk_now = {rel: rds._mtime_of(rds.DOC_ROOT, rel) for rel in meta_after}
            assert all(meta_after[rel] == m for rel, m in disk_now.items())
            assert not rds.doc_index_stale(con)
        finally:
            con.close()

    def test_empty_sidecar_not_ready(self, seeded_docs):
        con = rds.connect_doc_db(seeded_docs)
        try:
            assert not rds.doc_index_ready(con)
            assert rds.doc_index_stale(con)
        finally:
            con.close()
