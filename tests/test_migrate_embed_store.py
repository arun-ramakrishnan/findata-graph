# Tests for helpers/maintenance/migrate_embed_store.py
"""Round-trip pins for the one-shot sidecar -> embed-store migration.

Uses migrate()/sync_mirror() directly against tmp trees (no subprocess):
overlap dedupe on the (text_hash, model) key, cohort attribution via the
source column, idempotent re-runs, the .bak rename semantics (+ --no-rename
copy-only mode), and the mirror rebuild reading a bare note_search table.
"""

import json
import sqlite3

from helpers.maintenance import migrate_embed_store as MES


def _legacy(path, rows):
    """A legacy <db>_vec.db: physically a plain SQLite file whose cache
    table was created through the vecdb ATTACH alias (so it's unqualified
    inside its own file)."""
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE note_search_emb_cache ("
        " text_hash TEXT NOT NULL,"
        " model     TEXT NOT NULL,"
        " embedding TEXT NOT NULL,"
        " PRIMARY KEY (text_hash, model))"
    )
    con.executemany("INSERT INTO note_search_emb_cache VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()


class TestCachePool:
    def test_overlap_dedupes_and_labels_cohorts(self, tmp_path):
        legacy_a = tmp_path / "doc_search.db_vec.db"
        legacy_b = tmp_path / "script_search.db_vec.db"
        # 'shared' exists in BOTH files: pooled once, first writer wins.
        _legacy(legacy_a, [("shared", "m", "[1]", ), ("a-only", "m", "[2]")])
        _legacy(legacy_b, [("shared", "m", "[9]"), ("b-only", "m", "[3]")])
        store = tmp_path / "memory" / "embed_store.db"

        report = MES.migrate(store, tmp_path)
        assert [r["status"] for r in report].count("migrated") == 2

        con = sqlite3.connect(str(store))
        try:
            cohorts = dict(con.execute(
                "SELECT source, COUNT(*) FROM embed_cache GROUP BY source"
            ).fetchall())
            shared = con.execute(
                "SELECT embedding FROM embed_cache WHERE text_hash='shared'"
            ).fetchone()[0]
        finally:
            con.close()
        assert cohorts == {"doc": 2, "script": 1}  # b's shared deduped away
        assert shared == "[1]"  # INSERT OR IGNORE keeps the first copy

    def test_idempotent_rerun(self, tmp_path):
        legacy = tmp_path / "research.db_vec.db"
        _legacy(legacy, [("h1", "m", "[1]")])
        store = tmp_path / "memory" / "embed_store.db"
        first = MES.migrate(store, tmp_path)
        # Sources renamed -> rerun reports absence and leaves the pool put.
        second = MES.migrate(store, tmp_path)
        assert all(r["status"] == "absent" for r in second)
        con = sqlite3.connect(str(store))
        try:
            n = con.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0]
        finally:
            con.close()
        # Only present sources contribute (absent rows carry no counts).
        assert n == sum(r["pooled_now"] for r in first if r["status"] != "absent")

    def test_no_rename_leaves_sources_in_place(self, tmp_path):
        legacy = tmp_path / "doc_search.db_vec.db"
        _legacy(legacy, [("h", "m", "[1]")])
        report = MES.migrate(tmp_path / "store.db", tmp_path, rename=False)
        copied = [r for r in report if r["file"].endswith("doc_search.db_vec.db")]
        assert len(copied) == 1
        assert copied[0]["status"].startswith("copied")
        assert legacy.exists()
        assert not (tmp_path / "doc_search.db_vec.db.migrated.bak").exists()

    def test_rename_moves_journal_siblings_too(self, tmp_path):
        legacy = tmp_path / "research.db_vec.db"
        _legacy(legacy, [("h", "m", "[1]")])
        wal = tmp_path / "research.db_vec.db-wal"
        wal.write_bytes(b"")  # leftover from an unclean close
        MES.migrate(tmp_path / "store.db", tmp_path)
        assert not legacy.exists() and not wal.exists()
        bak = tmp_path / "research.db_vec.db.migrated.bak"
        wal_bak = tmp_path / "research.db_vec.db-wal.migrated.bak"
        assert bak.exists() and wal_bak.exists()

    def test_absent_sources_report_cleanly(self, tmp_path):
        report = MES.migrate(tmp_path / "store.db", tmp_path)
        assert {r["file"] for r in report} == set(MES.LEGACY_SOURCES)
        assert all(r["status"] == "absent" for r in report)


class TestSyncMirror:
    def test_rebuilds_mirror_from_bare_note_search(self, tmp_path, monkeypatch):
        # Bypass the house connect() schema bootstrap: the fixture seeds ONLY
        # the bare FTS5 shape sync_vec_table reads.
        monkeypatch.setattr(
            "helpers.core.db.connect", lambda path: sqlite3.connect(str(path)))
        research = tmp_path / "research.db"
        con = sqlite3.connect(str(research))
        con.execute(
            "CREATE VIRTUAL TABLE note_search USING fts5("
            "doc_type, file_path UNINDEXED, title, sector, content, "
            "embedding UNINDEXED)"
        )
        vecs = {"a.md": [1.0, 0.0], "b.md": [0.0, 1.0]}
        for fp, v in vecs.items():
            con.execute(
                "INSERT INTO note_search(doc_type, file_path, title, sector, "
                "content, embedding) VALUES ('company', ?, '', '', '', ?)",
                (fp, json.dumps(v)),
            )
        con.commit()
        con.close()

        written = MES.sync_mirror(research, dims=2)
        assert written == 2

        from helpers.core import vec_search as VS

        con = sqlite3.connect(str(VS.EMBED_DB_PATH))
        VS._load_vec_extension(con)
        try:
            keys = sorted(r[0] for r in
                          con.execute("SELECT file_path FROM note_search_vec"))
        finally:
            con.close()
        assert keys == ["a.md", "b.md"]
