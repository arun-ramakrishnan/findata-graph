#!/usr/bin/env python3
"""DuckDB-native git_search sidecar (duckdb_sidecar_fold S4).

Exercises the engine-agnostic rebuild/query split for the git fold:
- ``rebuild_git_duckdb`` builds the `.duckdb` store (git_search table +
  body-chunk table + meta/info stamps + native FTS index over composed
  ``searchtext`` = diff content);
- ``connect_git_db`` routes `.duckdb` → DuckDB, everything else → SQLite;
- ``search_commits`` dispatches to the native ``match_bm25`` +
  Python-highlight path and returns the same dict contract as the FTS5
  path, with since/path axes as plain SQL predicates;
- git_index_ready/stale + stored model/dims + the rare-literal router
  work on the DuckDB store.

Hermetic: tmp git repo (main branch, fixture commits) + tmp `.duckdb`
sidecar; model resolution forced to the pseudo fallback via the autouse
fixture (same shape as tests/test_git_search_index.py).
"""

from __future__ import annotations

import pytest

from helpers.maintenance import rebuild_git_search as rgs

pytestmark = [pytest.mark.integration]

_GIT_ENV = __import__("tests.test_git_search_index", fromlist=["_GIT_ENV"])._GIT_ENV


@pytest.fixture(autouse=True)
def pseudo_embedder(monkeypatch):
    """Never touch the live gemma/granite sidecars on this box."""
    from helpers.core import gemma_embedder, local_embedder

    monkeypatch.setattr(local_embedder, "available", lambda: False)
    monkeypatch.setattr(gemma_embedder, "available", lambda: False)
    monkeypatch.delenv("GIT_EMBEDDER", raising=False)


@pytest.fixture()
def repo(tmp_path):
    """Real git repo on main with a commit() helper returning the sha."""
    import shutil
    import subprocess

    root = tmp_path / "repo"
    root.mkdir()

    def git(*args: str, check: bool = True) -> str:
        git_bin = shutil.which("git")
        if git_bin is None:
            raise RuntimeError("git executable not found on PATH")
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [git_bin, "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=check,
            env=_GIT_ENV,
        )
        return proc.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "fixture@example.com")
    git("config", "user.name", "Fixture")

    def commit(files: dict[str, str], msg: str) -> str:
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        git("add", "-A")
        git("commit", "-m", msg)
        return git("rev-parse", "HEAD")

    return type(
        "Repo",
        (),
        {"root": root, "commit": staticmethod(commit), "git": staticmethod(git)},
    )()


def test_connect_routes_by_suffix(tmp_path):
    ddb = tmp_path / "x.duckdb"
    sdb = tmp_path / "x.db"
    c1 = rgs.connect_git_db(ddb)
    c2 = rgs.connect_git_db(sdb)
    try:
        import duckdb
        import sqlite3

        assert isinstance(c1, duckdb.DuckDBPyConnection)
        assert isinstance(c2, sqlite3.Connection)
    finally:
        c1.close()
        c2.close()


def test_rebuild_duckdb_builds_store_and_index(repo, tmp_path):
    repo.commit({"helpers/misc/thing.py": "def thing():\n    pass\n"}, "add thing lane")
    repo.commit({"tests/test_thing.py": "def test_thing():\n    assert True\n"}, "test thing lane")
    db = tmp_path / "git_search.duckdb"
    stats = rgs.rebuild_git_duckdb(db, git_root=repo.root)
    assert stats["total_units"] == 2
    assert stats["total_rows"] == 2
    assert stats["indexed"] == 2
    assert stats["embedded"] == 2
    conn = rgs.connect_git_db(db)
    try:
        assert rgs.git_index_ready(conn) is True
        assert rgs.git_index_stale(conn, root=repo.root) is False
        assert rgs._stored_embed_dims(conn) == 64
        schemas = {
            r[0]
            for r in conn.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
        }
        assert "fts_main_git_search" in schemas
        assert conn.execute("SELECT COUNT(*) FROM git_search_body").fetchone()[0] >= 0
    finally:
        conn.close()


def test_search_hybrid_returns_expected_shape(repo, tmp_path):
    repo.commit(
        {"helpers/misc/thing.py": "def thing():\n    return 'needle_value'\n"}, "add needle lane"
    )
    repo.commit(
        {"tests/test_thing.py": "def test_thing():\n    assert 'needle_value' in thing()\n"},
        "cover needle",
    )
    db = tmp_path / "git_search.duckdb"
    rgs.rebuild_git_duckdb(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        out = rgs.search_commits(conn, "needle lane", limit=3)
        assert out["mode"] in {"hybrid", "bm25"}  # rare-literal router may drop cosine
        assert out["results"], "query 'needle lane' must match at least one commit"
        hit = out["results"][0]
        assert set(hit) == {
            "sha",
            "date",
            "subject",
            "files",
            "snippet",
            "score",
            "similarity",
            "vector_from",
        }
        # vector-only via an explicit QueryVector (pseudo store has no live
        # gemma embedder to synthesize the query vector)
        from helpers.maintenance import rebuild_common as rbc

        model = rgs._stored_embed_model(conn)
        dims = rgs._stored_embed_dims(conn)
        if model and dims:
            qv = rbc.QueryVector(tuple([0.5] * dims), model, dims)
            vec = rgs.search_commits(conn, "needle lane", limit=3, query_vec=qv, vector_only=True)
            assert vec["mode"] == "vector"
            assert vec["results"], "vector-only path must match on this corpus"
    finally:
        conn.close()


def test_search_bm25_only_mode(repo, tmp_path):
    repo.commit({"helpers/misc/thing.py": "# nothing here\n"}, "chore: tidy")
    db = tmp_path / "git_search.duckdb"
    rgs.rebuild_git_duckdb(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        out = rgs.search_commits(conn, "tidy", limit=3, hybrid=False)
        assert out["mode"] == "bm25"
        assert out["results"], "lexical 'tidy' must match the chore commit"
        assert all(r["similarity"] is None for r in out["results"])
        assert all(r["vector_from"] is None for r in out["results"])
    finally:
        conn.close()


def test_since_and_path_filters(repo, tmp_path):
    sha1 = repo.commit({"a.txt": "alpha\n"}, "add alpha")
    sha2 = repo.commit({"b.txt": "beta\n"}, "add beta")
    db = tmp_path / "git_search.duckdb"
    rgs.rebuild_git_duckdb(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        since = rgs.search_commits(conn, "add", limit=5, since="2099-01-01")
        assert since["results"] == []
        path_only = rgs.search_commits(conn, "add", limit=5, path="b.txt")
        assert {r["sha"] for r in path_only["results"]} == {sha2}
        assert sha1 not in {r["sha"] for r in path_only["results"]}
        # 'add' matches both commits when unfiltered
        both = rgs.search_commits(conn, "add", limit=5)
        assert {r["sha"] for r in both["results"]} == {sha1, sha2}
    finally:
        conn.close()


def test_stale_detects_new_commit(repo, tmp_path):
    repo.commit({"a.txt": "alpha\n"}, "add alpha")
    db = tmp_path / "git_search.duckdb"
    rgs.rebuild_git_duckdb(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        assert rgs.git_index_stale(conn, root=repo.root) is False
    finally:
        conn.close()
    repo.commit({"b.txt": "beta\n"}, "add beta")
    conn = rgs.connect_git_db(db)
    try:
        assert rgs.git_index_stale(conn, root=repo.root) is True
    finally:
        conn.close()


def test_check_mode_reports_without_stamping(repo, tmp_path):
    repo.commit({"a.txt": "alpha\n"}, "add alpha")
    db = tmp_path / "git_search.duckdb"
    stats = rgs.rebuild_git_duckdb(db, git_root=repo.root, write=False)
    assert stats["total_units"] == 1
    assert stats["total_rows"] == 0  # nothing stored yet
    assert stats["index_stale"] is True
    assert stats["stale_new"]
    conn = rgs.connect_git_db(db)
    try:
        assert rgs._stored_embed_model(conn) is None
    finally:
        conn.close()


def test_legacy_sqlite_path_still_works(repo, tmp_path):
    """The rollback path: same pipeline through the legacy FTS5 store."""
    repo.commit({"a.txt": "alpha needle\n"}, "add alpha needle")
    db = tmp_path / "git_search.db"
    stats = rgs.rebuild(db, git_root=repo.root)
    assert stats["total_rows"] == 1
    conn = rgs.connect_git_db(db)
    try:
        out = rgs.search_commits(conn, "needle", limit=3)
        assert out["results"], "legacy path must stay queryable"
    finally:
        conn.close()
