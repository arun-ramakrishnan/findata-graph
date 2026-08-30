# Cycle-safe symlink-following walk (helpers/core/fs_walk.py).
#
# The load-bearing test is the cycle one: Path.rglob("*",
# recurse_symlinks=True) — the obvious stdlib spelling of this feature —
# HANGS on a symlink loop (no cycle protection, verified on 3.14.4,
# 2026-08-30). These tests pin the behavior the #107-contract doc walkers
# (app.py + rebuild_doc_search.py) rely on.

import os
import threading
from pathlib import Path

from helpers.core.fs_walk import iter_tree_files


def _collect(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in iter_tree_files(root))


def test_plain_tree(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "sub" / "b.md").write_text("b")
    assert _collect(tmp_path) == ["a.md", "sub/b.md"]


def test_directory_symlink_followed(tmp_path):
    """The worktree doc/local case: a symlinked dir's files are yielded
    under the SYMLINK path (the path the corpus keys on)."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "deep").mkdir()
    (real / "deep" / "note.md").write_text("n")
    os.symlink(real, tmp_path / "linked")
    assert _collect(tmp_path) == ["linked/deep/note.md"]


def test_symlink_cycle_terminates(tmp_path):
    """rglob(recurse_symlinks=True) hangs here; iter_tree_files must not.

    Guarded by a worker thread + join(timeout) so a regression fails in
    seconds instead of hanging the suite."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.md").write_text("x")
    (tmp_path / "root.md").write_text("r")
    os.symlink(tmp_path, tmp_path / "sub" / "loop")  # cycle: -> root
    os.symlink(tmp_path / "sub", tmp_path / "selfref")  # cycle: -> sub
    out: list[str] | None = None

    def run():
        nonlocal out
        out = _collect(tmp_path)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "walk did not terminate on symlink cycles"
    assert out is not None
    # every real file surfaces exactly once, no loop-amplified duplicates.
    # (Whether x.md surfaces as sub/x.md or selfref/x.md is deterministic
    # but depends on sibling sort order — the inode dedupe lets whichever
    # symlink/dir is scandir'd first claim it.)
    assert len(out) == 2
    assert "root.md" in out
    assert sorted(out)[1].endswith("/x.md")


def test_dir_reachable_twice_walked_once(tmp_path):
    """Inode dedupe: the same directory symlinked under two names yields
    its files exactly once (which path wins is deterministic per tree)."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "f.md").write_text("f")
    os.symlink(real, tmp_path / "one")
    os.symlink(real, tmp_path / "two")
    out = _collect(tmp_path)
    assert len(out) == 1 and out[0].endswith("/f.md")


def test_file_symlink_resolved_broken_skipped(tmp_path):
    """Same contract the old rglob + is_file() walk had."""
    target = tmp_path / "t.md"
    target.write_text("t")
    os.symlink(target, tmp_path / "alias.md")
    os.symlink(tmp_path / "missing.md", tmp_path / "broken.md")
    assert _collect(tmp_path) == ["alias.md", "t.md"]


def test_missing_root_yields_nothing(tmp_path):
    assert _collect(tmp_path / "nope") == []


def test_walkers_share_the_contract(tmp_path, monkeypatch):
    """#107: app.py::_iter_doc_files and rebuild_doc_search's walker must
    enumerate the same symlinked corpus (repo-rooted vs doc-rel keys)."""
    import app as app_mod
    from helpers.maintenance import rebuild_doc_search as rds

    docroot = tmp_path / "doc"
    docroot.mkdir()
    real_local = tmp_path / "elsewhere"
    (real_local / "local").mkdir(parents=True)
    (real_local / "local" / "assess.md").write_text("# Assessment\nbody\n")
    (docroot / "graph.md").write_text("# Graph\n")
    os.symlink(real_local / "local", docroot / "local")

    monkeypatch.setattr(app_mod, "_DOC_ROOT", docroot)
    monkeypatch.setattr(rds, "DOC_ROOT", docroot)

    api_side = [rel for rel, _full in app_mod._iter_doc_files()]
    index_side = [rel for rel, _abs in rds._iter_doc_files(docroot)]
    assert api_side == ["graph.md", "local/assess.md"]
    assert index_side == ["doc/graph.md", "doc/local/assess.md"]
    assert [r.split("/", 1)[1] for r in index_side] == api_side


def test_unreadable_dir_degrades_silently(tmp_path):
    """Scandir failures degrade silently (old rglob contract) — no
    exception escapes the walk. Root bypasses the mode, so only assert
    where the permission actually blocks."""
    d = tmp_path / "d"
    d.mkdir()
    (d / "ok.md").write_text("ok")
    (tmp_path / "top.md").write_text("t")
    d.chmod(0o000)
    try:
        out = _collect(tmp_path)
        assert "top.md" in out
        if os.geteuid() != 0:
            assert "d/ok.md" not in out
    finally:
        d.chmod(0o755)
