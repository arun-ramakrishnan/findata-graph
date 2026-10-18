"""Tests for rebuild_git_search — hermetic against a fixture git repo.

The embedder is forced to the 64-dim pseudo fallback (autouse fixture) so
no test loads the granite GGUF; the stamp-guard test proves the refusal
path without needing the real model either.
"""

import os
import shutil
import subprocess

import pytest

from helpers.maintenance import rebuild_git_search as rgs

# Isolate fixture git from the operator's global/system config (a global
# merge/push default turned `merge --no-ff` into exit 128 "No remote for
# the current branch" on one box) — the fixture defines its own identity.
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


@pytest.fixture(autouse=True)
def pseudo_embedder(monkeypatch):
    """Force the pseudo embedder regardless of what runs on the box: the
    gemma-first resolver would otherwise embed via the LIVE sidecar on
    this machine (slow, non-hermetic)."""
    from helpers.core import gemma_embedder, local_embedder

    monkeypatch.setattr(local_embedder, "available", lambda: False)
    monkeypatch.setattr(gemma_embedder, "available", lambda: False)
    monkeypatch.delenv("GIT_EMBEDDER", raising=False)


@pytest.fixture()
def repo(tmp_path):
    """A real git repo on main with a commit() helper returning the sha."""
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
        "Repo", (), {"root": root, "commit": staticmethod(commit), "git": staticmethod(git)}
    )()


def _row(conn, sha: str):
    return conn.execute(
        "SELECT sha, adate, subject, files, content FROM git_search WHERE sha = ?", (sha,)
    ).fetchone()


def test_rebuild_indexes_message_paths_and_diff(repo, tmp_path):
    sha = repo.commit(
        {"helpers/misc/thing.py": "def thing():\n    return 'needle_value'\n"},
        "[Findata] thing lane: add the resolver\n\nWHY\n\nThe needle_value lookup was missing.\n",
    )
    db = tmp_path / "git_search.db"
    stats = rgs.rebuild(db, git_root=repo.root)
    assert stats["indexed"] == 1
    assert stats["total_rows"] == 1
    conn = rgs.connect_git_db(db)
    try:
        row = _row(conn, sha)
        assert row is not None
        assert row[2].startswith("[Findata] thing lane")
        assert "helpers/misc/thing.py" in row[3]
        assert "needle_value" in row[4]  # diff text made the lexical body
        assert "WHY" in row[4]
        out = rgs.search_commits(conn, "needle_value", limit=5)
        assert out["results"], "bm25 leg must answer an identifier query"
        assert out["results"][0]["sha"] == sha
    finally:
        conn.close()


def test_excluded_paths_never_enter_content(repo, tmp_path):
    repo.commit(
        {"venv/pkg/noise.py": "x = 1\n", "findata/Sectors/Autos.md": "gold prose\n"},
        "[Findata] sector touch\n",
    )
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        (row,) = conn.execute("SELECT files, content FROM git_search").fetchall()
        assert "findata/Sectors/Autos.md" in row[0]
        assert "venv/" not in row[0]
        assert "venv/" not in row[1]
    finally:
        conn.close()


def test_diff_cap_truncates_with_marker(repo, tmp_path):
    repo.commit({"big/blob.txt": "z" * 200_000 + "\n"}, "[Findata] big blob\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        (content,) = conn.execute("SELECT content FROM git_search").fetchone()
        assert len(content) < 200_000
        assert "diff truncated" in content
    finally:
        conn.close()


def test_check_flags_new_commit_without_writes(repo, tmp_path):
    repo.commit({"a.txt": "a\n"}, "first WHY\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    new_sha = repo.commit({"b.txt": "b\n"}, "second WHY\n")
    stats = rgs.rebuild(db, write=False, git_root=repo.root)
    assert stats["index_stale"] is True
    assert stats["stale_new"] == [new_sha]
    assert stats["indexed"] == 0
    conn = rgs.connect_git_db(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM git_search").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM git_search_meta").fetchone()[0] == 1
    finally:
        conn.close()


def test_amend_tombstones_old_sha(repo, tmp_path):
    old_sha = repo.commit({"c.txt": "c\n"}, "original WHY\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    repo.git("commit", "--amend", "-m", "rewritten WHY: mechanism changed")
    new_sha = repo.git("rev-parse", "HEAD")
    assert new_sha != old_sha
    stats = rgs.rebuild(db, git_root=repo.root)
    assert stats["stale_deleted"] == [old_sha]
    assert stats["indexed"] == 1
    conn = rgs.connect_git_db(db)
    try:
        assert _row(conn, old_sha) is None
        assert _row(conn, new_sha) is not None
        assert conn.execute("SELECT COUNT(*) FROM git_search_meta").fetchone()[0] == 1
    finally:
        conn.close()


def test_exclude_version_bump_reextracts_exactly_once(repo, tmp_path, monkeypatch):
    repo.commit({"d.txt": "d\n"}, "one WHY\n")
    repo.commit({"e.txt": "e\n"}, "two WHY\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    monkeypatch.setattr(rgs, "EXCLUDE_VERSION", 99)
    stats = rgs.rebuild(db, git_root=repo.root)
    assert len(stats["stale_changed"]) == 2
    assert stats["indexed"] == 2
    # and a second run at the new version is a no-op
    stats2 = rgs.rebuild(db, git_root=repo.root)
    assert stats2["indexed"] == 0 and not stats2["index_stale"]


def test_idempotent_second_apply(repo, tmp_path):
    repo.commit({"f.txt": "f\n"}, "why one\n")
    db = tmp_path / "git_search.db"
    first = rgs.rebuild(db, git_root=repo.root)
    assert first["indexed"] == 1
    second = rgs.rebuild(db, git_root=repo.root)
    assert second["indexed"] == 0
    assert second["index_stale"] is False
    assert not second["stale_new"] and not second["stale_changed"] and not second["stale_deleted"]


def test_embed_basis_bump_reembeds_whole_corpus(repo, tmp_path, monkeypatch):
    long_msg = "basis WHY\n\n" + "x" * 600  # longer than any cap below
    repo.commit({"m.txt": "m\n"}, long_msg)
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    # version + cap change -> the basis TEXT changes -> genuine re-embed
    monkeypatch.setattr(rgs, "EMBED_BASIS_VERSION", 3)
    monkeypatch.setattr(rgs, "EMBED_BASIS_CAP", 200)
    stats = rgs.rebuild(db, git_root=repo.root)
    assert stats["indexed"] == 0  # no extraction change
    assert stats["embedded"] == 1
    conn = rgs.connect_git_db(db)
    try:
        (basis,) = conn.execute(
            "SELECT value FROM git_search_info WHERE key = 'embed_basis'"
        ).fetchone()
        assert basis == "3"
    finally:
        conn.close()
    # version bump alone with an unchanged cap -> same texts -> the shared
    # cache absorbs it (0 misses), the pass still runs
    monkeypatch.setattr(rgs, "EMBED_BASIS_VERSION", 4)
    stats2 = rgs.rebuild(db, git_root=repo.root)
    assert stats2["embedded"] == 0 and stats2["indexed"] == 0
    stats3 = rgs.rebuild(db, git_root=repo.root)
    assert "embedded" not in stats3 and stats3["indexed"] == 0


def test_unstamped_basis_upgrades_to_reembed(repo, tmp_path, monkeypatch):
    """A v1-era index (rows but no embed_basis stamp) must re-embed whole.

    The cap changes with the version so the basis TEXTS genuinely differ —
    same-code rebuilds produce identical texts that the shared cache
    (correctly) absorbs, which would prove nothing."""
    repo.commit({"n.txt": "n\n"}, "upgrade WHY\n\n" + "y" * 600)
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        conn.execute("DELETE FROM git_search_info WHERE key = 'embed_basis'")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(rgs, "EMBED_BASIS_VERSION", 5)
    monkeypatch.setattr(rgs, "EMBED_BASIS_CAP", 200)
    stats = rgs.rebuild(db, git_root=repo.root)
    assert stats["indexed"] == 0
    assert stats["embedded"] == 1
    conn = rgs.connect_git_db(db)
    try:
        (basis,) = conn.execute(
            "SELECT value FROM git_search_info WHERE key = 'embed_basis'"
        ).fetchone()
        assert basis == "5"
    finally:
        conn.close()


def test_unstamped_basis_forces_full_coverage(repo, tmp_path, monkeypatch):
    """An absent basis stamp forces WHOLE-corpus re-embed on the next pass
    (the live trap repaired: a partial apply had stamped v2 over 414
    full-basis rows and the upgrade could never fire again — absence now
    means stale, so the pass that follows covers everything before the
    stamp is rewritten)."""
    repo.commit({"o.txt": "o\n"}, "partial WHY\n\n" + "z" * 600)
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        conn.execute("DELETE FROM git_search_info WHERE key = 'embed_basis'")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(rgs, "EMBED_BASIS_VERSION", 6)
    monkeypatch.setattr(rgs, "EMBED_BASIS_CAP", 250)
    repo.commit({"p.txt": "p\n"}, "newer WHY\n")
    stats = rgs.rebuild(db, git_root=repo.root)
    assert stats["indexed"] == 1
    assert stats["embedded"] == 2, "absent basis stamp must re-embed the whole corpus"
    conn = rgs.connect_git_db(db)
    try:
        (basis,) = conn.execute(
            "SELECT value FROM git_search_info WHERE key = 'embed_basis'"
        ).fetchone()
        assert basis == "6"
    finally:
        conn.close()


def test_worktree_refusal(repo, tmp_path):
    repo.commit({"g.txt": "g\n"}, "need a commit before worktree add\n")
    wt = tmp_path / "wt"
    repo.git("worktree", "add", str(wt))
    assert rgs.primary_repo_root(repo.root) == repo.root
    with pytest.raises(RuntimeError, match="MAIN checkout only"):
        rgs.primary_repo_root(wt)


def test_stamp_guard_refuses_model_flip(repo, tmp_path):
    repo.commit({"h.txt": "h\n"}, "stamped pseudo first\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    repo.commit({"i.txt": "i\n"}, "then a new commit arrives\n")
    conn = rgs.connect_git_db(db)
    try:
        conn.execute(
            "UPDATE git_search_info SET value = 'granite-embedding-97m-r2' WHERE key = 'embed_model'"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(RuntimeError, match="refusing to mix vector spaces"):
        rgs.rebuild(db, git_root=repo.root)


def test_merge_commit_indexed_without_patch(repo, tmp_path):
    repo.commit({"base.txt": "base\n"}, "base WHY\n")
    repo.git("checkout", "-b", "side")
    repo.commit({"side.txt": "side\n"}, "side WHY\n")
    repo.git("checkout", "main")
    repo.git("merge", "--no-ff", "-m", "Merge side: routed by namespace, not directory", "side")
    db = tmp_path / "git_search.db"
    stats = rgs.rebuild(db, git_root=repo.root)
    assert stats["total_units"] == 3
    conn = rgs.connect_git_db(db)
    try:
        rows = conn.execute("SELECT subject, content FROM git_search").fetchall()
        merge = [r for r in rows if r[0].startswith("Merge side")]
        assert merge, "merge commit message must be indexed"
        # git log --numstat skips merge diffs by default (documented non-goal:
        # first-parent message+stat rows only) — the merge row is message-only,
        # and the side commit's own row carries the path.
        assert "side.txt" not in merge[0][1]
        side_rows = [r for r in rows if "side.txt" in r[1]]
        assert len(side_rows) == 1
    finally:
        conn.close()


def test_since_and_path_filters(repo, tmp_path):
    repo.commit({"findata/Sectors/Autos.md": "autos\n"}, "autos WHY\n")
    repo.commit({"helpers/misc/other.py": "other\n"}, "other WHY\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        future = "2999-01-01"
        out = rgs.search_commits(conn, "why", limit=10, since=future)
        assert out["results"] == []
        out = rgs.search_commits(conn, "why", limit=10, path="findata")
        assert [r["subject"] for r in out["results"]] == ["autos WHY"]
        # a LIKE wildcard in the path filter must not become a truncation scan
        out = rgs.search_commits(conn, "why", limit=10, path="%")
        assert out["results"] == []
    finally:
        conn.close()


def test_ready_and_stale_probe(repo, tmp_path):
    db = tmp_path / "git_search.db"
    conn = rgs.connect_git_db(db)
    try:
        assert rgs.git_index_ready(conn) is False
        assert rgs.git_index_stale(conn) is True
    finally:
        conn.close()
    repo.commit({"j.txt": "j\n"}, "probe WHY\n")
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        assert rgs.git_index_ready(conn) is True
        assert rgs.git_index_stale(conn, root=repo.root) is False
    finally:
        conn.close()
    repo.commit({"k.txt": "k\n"}, "newer WHY\n")
    conn = rgs.connect_git_db(db)
    try:
        assert rgs.git_index_stale(conn, root=repo.root) is True
    finally:
        conn.close()


def test_search_prose_finds_via_vector_leg(repo, tmp_path):
    repo.commit(
        {"l.txt": "l\n"},
        "[Findata] bili lane: route summaries by namespace\n\nWHY\n\nThe plugin compressed sessions for a harness that was not a peer.\n",
    )
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        out = rgs.search_commits(conn, "compressed sessions namespace routing", limit=3)
        assert out["results"], "hybrid search must answer a prose query"
        assert "namespace" in out["results"][0]["subject"]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# S5 — body chunks (deep-body vectors, proposal §8)
# --------------------------------------------------------------------------- #


def _long_message(subject: str, filler_topic: str, deep_mechanism: str) -> str:
    """A message whose head is generic filler and whose DEEP body (past the
    400-char basis cap) carries the mechanism text. Head+pad lands at
    exactly cap+20 so the mechanism ALWAYS starts past the head basis, and
    the tail carries enough substance to clear the 80-char chunk floor."""
    head = subject + "\n\nWHY\n\n" + (filler_topic + " considerations apply. " * 10)
    pad_len = max(0, rgs.EMBED_BASIS_CAP + 20 - len(head))
    tail = (
        "\n\nMECHANISM\n\n"
        + deep_mechanism
        + "\n"
        + "supporting detail line for the mechanism write-up.\n" * 8
    )
    return head + ("x" * pad_len) + tail


def _stored_message(msg: str) -> str:
    """The message text the INDEX sees: git -m cleanup + _messages' rstrip
    drop the final newline (measured: only that byte differs for these
    fixtures) — chunk comparisons must use the stored form."""
    return msg.rstrip("\n")


def test_body_chunker_boundaries():
    short = "subject\n\nshort body\n"
    assert rgs._body_chunks(short) == []
    msg = "s\n\n" + ("line of prose\n" * 80)  # ~1,120-char tail
    chunks = rgs._body_chunks(msg)
    assert chunks, "a long tail must yield chunks"
    assert "".join(chunks) == msg[rgs.EMBED_BASIS_CAP :]  # exact cover, no overlap
    for chunk in chunks:
        assert len(chunk) <= rgs.BODY_CHUNK_CAP or "\n" not in chunk.strip()


def test_rebuild_writes_body_rows_and_stamp(repo, tmp_path):
    msg = _long_message("body lane", "scheduling", "the flush writes only the touched buffer")
    sha = repo.commit({"g.txt": "g\n"}, msg)
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM git_search_body WHERE sha = ?", (sha,)).fetchone()[0]
        assert n == len(rgs._body_chunks(_stored_message(msg))) > 0
        (stamp,) = conn.execute(
            "SELECT value FROM git_search_info WHERE key = 'body_basis'"
        ).fetchone()
        assert stamp == str(rgs.BODY_BASIS_VERSION)
        (fp,) = conn.execute(
            "SELECT chunk_fp FROM git_search_body WHERE sha = ? AND chunk_idx = 0", (sha,)
        ).fetchone()
        want = rgs.hashlib_blake2b(
            f"v{rgs.BODY_BASIS_VERSION}|cap{rgs.BODY_CHUNK_CAP}\n"
            f"{rgs._body_chunks(_stored_message(msg))[0]}"
        )
        assert fp == want
    finally:
        conn.close()


def test_short_messages_have_no_body_rows(repo, tmp_path):
    repo.commit({"h.txt": "h\n"}, "short why\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM git_search_body").fetchone()[0] == 0
    finally:
        conn.close()


def test_deep_body_vector_leg(repo, tmp_path):
    """The S5 rule itself: a query embedding matching a DEEP body chunk
    must surface that commit through the cosine leg, marked body-sourced.

    Pseudo embeddings are content-hashed (no semantics), so the query
    vector is the exact chunk vector — equality is the strongest signal
    the fallback embedder can produce."""
    from helpers.graph.embeddings import _pseudo_embedding
    from helpers.maintenance import rebuild_common as rbc

    mechanism = "the session writer flush writes only the touched buffer and never the pool"
    msg = _long_message("writer lane", "buffering", mechanism)
    sha = repo.commit({"i.txt": "i\n"}, msg)
    repo.commit({"j.txt": "j\n"}, "unrelated filler commit\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        chunk = rgs._body_chunks(_stored_message(msg))[0]
        qv = rbc.QueryVector(tuple(_pseudo_embedding(chunk, 64)), "dry-run-v64", 64)
        scored, sims, from_body = rgs._cosine_leg(conn, "writer flush buffer", qv)
        assert scored, "cosine leg must answer via the body chunk"
        rid = conn.execute("SELECT rowid FROM git_search WHERE sha = ?", (sha,)).fetchone()[0]
        assert sims[rid] > 0.999  # exact-vector match
        assert rid in from_body
        out = rgs.search_commits(
            conn, "writer flush buffer", limit=3, vector_only=True, query_vec=qv
        )
        assert out["results"][0]["sha"] == sha
        assert out["results"][0]["vector_from"] == "body"
    finally:
        conn.close()


def test_idempotent_second_apply_embeds_nothing_incl_body(repo, tmp_path):
    repo.commit({"k.txt": "k\n"}, _long_message("idem lane", "caching", "rechunk stability"))
    db = tmp_path / "git_search.db"
    first = rgs.rebuild(db, git_root=repo.root)
    assert first.get("embedded_body", 0) > 0
    second = rgs.rebuild(db, git_root=repo.root)
    assert second["indexed"] == 0
    assert second.get("embedded", 0) == 0 and second.get("embedded_body", 0) == 0
    assert second["index_stale"] is False


def test_amend_rechunks_body_and_drops_old(repo, tmp_path):
    long1 = _long_message("rechunk", "first", "two-chunk body " + "y" * 700)
    old_sha = repo.commit({"l.txt": "l\n"}, long1)
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    repo.git("commit", "--amend", "-m", _long_message("rechunk", "second", "one short body"))
    new_sha = repo.git("rev-parse", "HEAD")
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM git_search_body WHERE sha = ?", (old_sha,)
            ).fetchone()[0]
            == 0
        )
        n_new = conn.execute(
            "SELECT COUNT(*) FROM git_search_body WHERE sha = ?", (new_sha,)
        ).fetchone()[0]
        assert n_new > 0
        assert conn.execute("SELECT COUNT(*) FROM git_search_body").fetchone()[0] == n_new
    finally:
        conn.close()


def test_body_basis_version_bump_cache_absorbed(repo, tmp_path, monkeypatch):
    """A version bump re-chunks (new fps, new stamp) at ZERO embed cost —
    the chunk texts are unchanged and the cache is content-addressed."""
    sha = repo.commit({"m2.txt": "m\n"}, _long_message("bump lane", "stamps", "stable text"))
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    before = rgs.connect_git_db(db)
    n_before = before.execute("SELECT COUNT(*) FROM git_search_body").fetchone()[0]
    fp_before = {
        r[0] for r in before.execute("SELECT chunk_fp FROM git_search_body WHERE sha = ?", (sha,))
    }
    before.close()
    monkeypatch.setattr(rgs, "BODY_BASIS_VERSION", rgs.BODY_BASIS_VERSION + 1)
    stats = rgs.rebuild(db, git_root=repo.root)
    assert stats.get("embedded_body", 0) == 0  # cache absorbed every text
    conn = rgs.connect_git_db(db)
    try:
        (stamp,) = conn.execute(
            "SELECT value FROM git_search_info WHERE key = 'body_basis'"
        ).fetchone()
        assert stamp == str(rgs.BODY_BASIS_VERSION)
        assert conn.execute("SELECT COUNT(*) FROM git_search_body").fetchone()[0] == n_before
        fp_after = {
            r[0] for r in conn.execute("SELECT chunk_fp FROM git_search_body WHERE sha = ?", (sha,))
        }
        assert fp_after and fp_after != fp_before
    finally:
        conn.close()


def test_absent_body_stamp_forces_full_chunk_pass(repo, tmp_path):
    """A pre-S5 sidecar (no body stamp, no body rows) re-chunks the WHOLE
    corpus on the next apply, not just touched shas."""
    sha1 = repo.commit({"n.txt": "n\n"}, _long_message("restore one", "a", "first mechanism"))
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    n_expected = conn.execute("SELECT COUNT(*) FROM git_search_body").fetchone()[0]
    conn.execute("DELETE FROM git_search_body")
    conn.execute("DELETE FROM git_search_info WHERE key = 'body_basis'")
    conn.commit()
    conn.close()
    sha2 = repo.commit({"o.txt": "o\n"}, _long_message("restore two", "b", "second mechanism"))
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        n_after = conn.execute("SELECT COUNT(*) FROM git_search_body").fetchone()[0]
        assert n_after == n_expected + len(
            rgs._body_chunks(_long_message("restore two", "b", "second mechanism"))
        )
        for sha in (sha1, sha2):
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM git_search_body WHERE sha = ?", (sha,)
                ).fetchone()[0]
                > 0
            )
    finally:
        conn.close()


def test_check_flags_missing_body_basis(repo, tmp_path):
    repo.commit({"p.txt": "p\n"}, _long_message("check lane", "staleness", "pending work"))
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    conn.execute("DELETE FROM git_search_info WHERE key = 'body_basis'")
    conn.commit()
    conn.close()
    stats = rgs.rebuild(db, git_root=repo.root, write=False)
    assert stats["stale_body_basis"] is True
    assert stats["index_stale"] is True


def test_rare_literal_routes_lexical(repo, tmp_path):
    """A ≥4-char term matching ≤3 rows is near-unique lexical evidence:
    the query routes lexical-only even when prose-shaped, and the row
    holding the literal wins (S5 live hit: SessionWriter, 2 rows).

    Fixture caveat the first draft hit: in a 2-doc corpus EVERY term has
    doc-frequency 1, so bm25 has no rare-vs-common contrast and the
    OR-term mass can outrank the literal holder. The anchor query uses
    terms the other commit does not carry at all."""
    rare = _long_message("rare lane", "routing", "the quibbelmontane guard owns the retry loop")
    sha_a = repo.commit({"q1.txt": "q\n"}, rare)
    _sha_b = repo.commit({"q2.txt": "q\n"}, _long_message("other lane", "buffers", "cascade"))
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        out = rgs.search_commits(conn, "quibbelmontane retry loop guard", limit=3)
        assert out["mode"] == "bm25", "rare literal must route lexical-only"
        assert out["results"][0]["sha"] == sha_a
    finally:
        conn.close()


def test_rare_literal_threshold_is_rarity_not_blanket(repo, tmp_path):
    """Terms matching MORE than _RARE_LITERAL_ROWS rows do not route — the
    router is rarity-driven, never a blanket lexical default."""
    for i in range(rgs._RARE_LITERAL_ROWS + 1):
        repo.commit({f"s{i}.txt": "s\n"}, f"shared lane {i}\n\nsharedprobe token throughout\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM git_search WHERE git_search MATCH ?", ('"sharedprobe"',)
        ).fetchone()[0]
        assert n == rgs._RARE_LITERAL_ROWS + 1
        assert rgs._has_rare_literal(conn, "sharedprobe token throughout") is False
    finally:
        conn.close()


def test_extra_never_displaces_lexical_top():
    """The S5 fusion invariant, unit-level: a cosine-only EXTRA fuses on
    its cosine term alone, so a two-leg page row at bm25 rank 1 always
    outranks it even when the extra holds cosine rank 1 — the synthetic
    bm25-position bug this pins (live: 0.66-generic chunks burying the
    bm25 -17.1 truth)."""
    row_page = (11, "page-sha", "d", "s", "f", "c", b"", 0.0, "snip")
    row_extra = (12, "extra-sha", "d", "s", "f", "c", b"", 0.0, "snip")
    hits = rgs._fuse_scores(
        [  # ty: ignore[invalid-argument-type] — unit test feeds plain tuples, not sqlite3.Row
            (0, row_page, "snip", False),
            (99, row_extra, "snip", True),
        ],
        {12: 0, 11: 5},  # extra owns cosine rank 1; page row ranks 6th
    )
    by_sha = {row[1]: score for score, row, _snip in hits}
    assert by_sha["page-sha"] > by_sha["extra-sha"]
    assert hits[0][1][1] == "page-sha"


# --------------------------------------------------------------------------- #
# Gemma adoption (operator 2026-10-17 evening) — selector + per-model basis
# --------------------------------------------------------------------------- #


def test_resolver_prefers_gemma_then_granite_then_pseudo(monkeypatch):
    from helpers.core import gemma_embedder, local_embedder

    def fake_gemma(texts):
        return [[0.1] * 512 for _ in texts]

    def fake_granite(texts):
        return [[0.2] * 384 for _ in texts]

    monkeypatch.setattr(gemma_embedder, "available", lambda: True)
    monkeypatch.setattr(gemma_embedder, "embed_document", lambda t: [0.1] * 512)
    monkeypatch.setattr(local_embedder, "available", lambda: True)
    monkeypatch.setattr(local_embedder, "embed_documents_parallel", fake_granite)

    _fn, dims, label, _warned = rgs.resolve_git_embedder()
    assert (dims, label) == (512, gemma_embedder.MODEL_LABEL), "gemma-first"
    assert _fn(["x"])[0] == [0.1] * 512

    monkeypatch.setattr(gemma_embedder, "available", lambda: False)
    _fn, dims, label, _warned = rgs.resolve_git_embedder()
    assert (dims, label) == (local_embedder.DIM, local_embedder.MODEL_ID), "granite fallback"

    monkeypatch.setattr(local_embedder, "available", lambda: False)
    _fn, dims, label, _warned = rgs.resolve_git_embedder()
    assert label == "dry-run-v64" and dims == 64, "pseudo last"


def test_resolver_env_forces_lane(monkeypatch):
    from helpers.core import gemma_embedder, local_embedder

    monkeypatch.setattr(gemma_embedder, "available", lambda: True)
    monkeypatch.setattr(gemma_embedder, "embed_document", lambda t: [0.1] * 512)
    monkeypatch.setattr(local_embedder, "available", lambda: True)
    monkeypatch.setattr(
        local_embedder, "embed_documents_parallel", lambda ts: [[0.2] * 384 for _ in ts]
    )
    monkeypatch.setenv("GIT_EMBEDDER", "granite")
    _fn, dims, label, _warned = rgs.resolve_git_embedder()
    assert label == local_embedder.MODEL_ID, "granite forced even with gemma up"


def test_gemma_basis_carries_prefix_contract():
    from helpers.core import gemma_embedder

    msg = "subject line\n\nWHY\n\n" + "body text " * 60
    head = rgs._head_embed_text(gemma_embedder.MODEL_LABEL, "subject line", msg)
    chunk = rgs._chunk_embed_text(gemma_embedder.MODEL_LABEL, "subject line", "chunk text")
    assert head.startswith("title: subject line | text: commit message head\n")
    assert head.endswith(rgs._embed_basis(msg))
    assert chunk == "title: subject line | text: commit message body\nchunk text"
    # granite/pseudo basis stays the raw text (byte-parity with the S5 cutover)
    assert rgs._head_embed_text("granite-embedding-97m-r2", "s", msg) == rgs._embed_basis(msg)
    assert rgs._chunk_embed_text("granite-embedding-97m-r2", "s", "chunk text") == "chunk text"


def test_gemma_stamp_refuses_granite_demotion(repo, tmp_path, monkeypatch):
    """guard_gemma_stamp wiring: a gemma-stamped index with the sidecar
    down must refuse the fallback (the 2026-10-08 class) at the GUARD,
    before any mixing concern; and a model change on an EXISTING index
    is refused outright either direction — cutover/un-migration go
    through delete-the-sidecar (derived state, warm rebuild)."""
    from helpers.core import gemma_embedder
    from helpers.core.gemma_embedder import GemmaStampDemotion

    repo.commit({"t1.txt": "t\n"}, _long_message("demote lane", "guards", "stamped text"))
    db = tmp_path / "git_search.db"
    # build gemma-stamped by faking availability for one rebuild
    monkeypatch.setattr(gemma_embedder, "available", lambda: True)
    monkeypatch.setattr(gemma_embedder, "embed_document", lambda t: [0.05] * 512)
    rgs.rebuild(db, git_root=repo.root)
    conn = rgs.connect_git_db(db)
    (stamp,) = conn.execute(
        "SELECT value FROM git_search_info WHERE key = 'embed_model'"
    ).fetchone()
    conn.close()
    assert stamp == gemma_embedder.MODEL_LABEL

    # sidecar down -> resolver falls to pseudo -> the DEMOTION guard fires
    monkeypatch.setattr(gemma_embedder, "available", lambda: False)
    repo.commit({"t2.txt": "t\n"}, "later WHY\n")
    with pytest.raises(GemmaStampDemotion):
        rgs.rebuild(db, git_root=repo.root)

    # even the explicit escape cannot RETAG an existing index incrementally
    # (mixing refused); un-migration = delete the sidecar + rebuild
    monkeypatch.setenv("GIT_EMBEDDER", "granite")
    with pytest.raises(RuntimeError, match="refusing to mix"):
        rgs.rebuild(db, git_root=repo.root)
    from helpers.core import local_embedder

    monkeypatch.setattr(local_embedder, "available", lambda: True)
    monkeypatch.setattr(
        local_embedder, "embed_documents_parallel", lambda ts: [[0.2] * 384 for _ in ts]
    )
    db.unlink()
    stats = rgs.rebuild(db, git_root=repo.root)  # fresh build under granite lane
    assert stats["total_rows"] == 2
    assert stats["embed_model"] == local_embedder.MODEL_ID


def test_cli_accepts_allow_stale_as_noop(repo, tmp_path, capsys):
    """The search-fresh loop forwards ARGS verbatim to every rebuilder;
    the guard-less git leg must accept --allow-stale as a documented
    no-op (hit live 2026-10-17: unrecognized-arguments abort). The
    verdict itself is whatever the reachable-set compare says — the
    contract under test is that the flag PARSES (argparse abort exits 2)."""
    repo.commit({"u.txt": "u\n"}, "noop lane WHY\n")
    db = tmp_path / "git_search.db"
    rgs.rebuild(db, git_root=repo.root)
    rc = rgs.main(["--check", "--db", str(db), "--allow-stale"])
    captured = capsys.readouterr()
    assert rc in (0, 1)
    assert "unrecognized arguments" not in captured.err + captured.out
