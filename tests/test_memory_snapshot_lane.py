"""
Snapshot-history lane tests — S5 of doc/improvements/archive/convo_search/
timeshift_snapshot_lane.md: doctrine that has ROTATED OUT of the live harness
pools survives only in timeshift images, and is indexed into the SAME
memory_search sidecar under provenance kinds (zcode-snap / prime-snap /
opencode-snap) instead of a separate index.

The lane is DEDUPE-FIRST: a record byte-identical to its live counterpart is
NOT history, and one unchanged record sitting in N images is ONE row naming
all N. Hermetic: tmp live pools + a two-image tmp mount, $HOME redirected so
the opencode lane (which resolves the image copy relative to $HOME) stays
inside tmp.
"""

import json
from pathlib import Path

import pytest

from helpers.maintenance import rebuild_memory_search as rms

pytestmark = [pytest.mark.integration]

_SNAP_ZCODE = (
    "---\n"
    "name: user-workflow\n"
    "description: Operator contract for gates\n"
    "  and staging discipline\n"
    "---\n"
    "\n"
    "The operator owns stgit patch structure; agents edit files.\n"
)
_SNAP_ZCODE_OLD = (
    "---\n"
    "name: user-workflow\n"
    "description: Operator contract for gates\n"
    "  and staging discipline\n"
    "---\n"
    "\n"
    "The operator owns stgit patch structure; SNAPSHOT WORDING.\n"
)
_SNAP_OC_RECORD = (
    "ts=2026-09-25T00:00:00Z type=preference scope=findata-graph/gates "
    'content="TARGETED tests only." tags=gates\n'
)


def _fake_embed(text: str) -> list[float]:
    return [0.5] * 8


def _image(home_root: Path, stamp: str) -> Path:
    """One image's $HOME, built through the module helper so the
    <stamp>/localhost/home/<user> layout stays the module's business."""
    return rms._snap_home(home_root, stamp)


def _write_image(root: Path, stamp: str, *, zcode_text: str) -> None:
    """zcode + prime + opencode doctrine for ONE image.

    Both images carry the SAME prime/opencode records (dedupe must collapse
    them to one row naming both stamps) and a scope symlinked OUT of the
    image (which must contribute nothing)."""
    sh = _image(root, stamp)
    zdir = sh / ".zcode" / "cli" / "memories" / "projects" / "scope-main" / "memory"
    zdir.mkdir(parents=True)
    (zdir / "user-workflow.md").write_text(zcode_text)
    # OUTSIDE the mount root on purpose: a directory under root would look
    # like another image to _snap_stamps.
    outside = root.parent / "outside-memory"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "leaked.md").write_text("---\nname: leaked\n---\n\nLIVE TEXT LEAK.\n")
    linked = sh / ".zcode" / "cli" / "memories" / "projects" / "scope-leak"
    linked.mkdir(parents=True)
    (linked / "memory").symlink_to(outside, target_is_directory=True)
    pdir = sh / ".prime" / "agent" / "harness"
    pdir.mkdir(parents=True)
    (pdir / "harness_state.json").write_text(
        json.dumps(
            {
                "entries": {
                    "memory": {
                        "snap_only": {
                            "title": "snap only",
                            "content": "snapshot-only rule",
                        }
                    }
                }
            }
        )
    )
    odir = sh / "Research" / "findata-graph" / ".opencode" / "memory"
    odir.mkdir(parents=True)
    (odir / "2026-09-25.logfmt").write_text(_SNAP_OC_RECORD)


@pytest.fixture
def snap_tree(tmp_path, monkeypatch):
    """Live pools + a TWO-IMAGE mount, all under tmp.

    Image 1 carries an OLD version of the live zcode doctrine (real history);
    image 2 carries the LIVE text (must be deduped away)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    zcode = tmp_path / "zcode-projects"
    mem = zcode / "scope-main" / "memory"
    mem.mkdir(parents=True)
    (mem / "user-workflow.md").write_text(_SNAP_ZCODE)
    prime = tmp_path / "prime" / "harness" / "harness_state.json"
    prime.parent.mkdir(parents=True)
    prime.write_text(
        json.dumps(
            {"entries": {"memory": {"live_only": {"title": "live", "content": "live only"}}}}
        )
    )
    oc = home / "Research" / "findata-graph" / ".opencode" / "memory"
    oc.mkdir(parents=True)
    (oc / "2026-09-24.logfmt").write_text(
        'ts=2026-09-24T00:00:00Z type=context scope=s content="live record"\n'
    )

    root = tmp_path / "snap-root"
    _write_image(root, "2026-08-26_19-00-01", zcode_text=_SNAP_ZCODE_OLD)
    _write_image(root, "2026-09-26_20-00-02", zcode_text=_SNAP_ZCODE)

    monkeypatch.setattr(rms, "MEMORY_DB", tmp_path / "memory_search.db")
    monkeypatch.setattr(rms, "ZCODE_PROJECTS", zcode)
    monkeypatch.setattr(rms, "PRIME_STATE", prime)
    monkeypatch.setattr(rms, "OPENCODE_MEM", oc)
    monkeypatch.setattr(rms, "SNAP_ROOT", root)
    return tmp_path


def _rebuild(tree, **kw):
    kw.setdefault("zcode_projects", tree / "zcode-projects")
    kw.setdefault("prime_state", tree / "prime" / "harness" / "harness_state.json")
    kw.setdefault(
        "opencode_dir", tree / "home" / "Research" / "findata-graph" / ".opencode" / "memory"
    )
    return rms.rebuild(tree / "memory_search.db", embed_fn=_fake_embed, **kw)


def _kinds(tree) -> dict[str, int]:
    conn = rms.connect_memory_db(tree / "memory_search.db")
    try:
        return {
            r[0]: r[1]
            for r in conn.execute("SELECT kind, COUNT(*) FROM memory_search GROUP BY kind")
        }
    finally:
        conn.close()


class TestSnapshotHistoryLane:
    def test_dedupe_and_provenance(self, snap_tree):
        stats = _rebuild(snap_tree)
        assert stats["history_snapshots"] == 2
        assert stats["history_units"] == 3
        # live row + ONE history row per pool
        assert _kinds(snap_tree) == {
            "zcode": 1,
            "zcode-snap": 1,
            "prime": 1,
            "prime-snap": 1,
            "opencode": 1,
            "opencode-snap": 1,
        }

        conn = rms.connect_memory_db(snap_tree / "memory_search.db")
        try:
            zc = conn.execute(
                "SELECT content, purpose, source_path FROM memory_search WHERE kind = 'zcode-snap'"
            ).fetchone()
            oc_row = conn.execute(
                "SELECT title, purpose FROM memory_search WHERE kind = 'opencode-snap'"
            ).fetchone()
            pr_row = conn.execute(
                "SELECT purpose FROM memory_search WHERE kind = 'prime-snap'"
            ).fetchone()
        finally:
            conn.close()
        # image 2 carried the live text -> deduped away; image 1''s OLD
        # wording survived and the row names the single image it was seen in
        assert "SNAPSHOT WORDING" in zc[0]
        assert "agents edit files" not in zc[0]
        assert zc[1].startswith("seen 2026-08-26_19-00-01 in 1 image(s)")
        assert "2026-08-26_19-00-01" in zc[2]
        # one unchanged record in two images collapsed to ONE row naming both
        assert oc_row[0] == "findata-graph/gates"
        assert oc_row[1].startswith("seen 2026-08-26_19-00-01, 2026-09-26_20-00-02 in 2 image(s)")
        assert pr_row[0].startswith("seen 2026-08-26_19-00-01, 2026-09-26_20-00-02 in 2 image(s)")

    def test_scope_symlinked_out_of_image_is_ignored(self, snap_tree):
        _rebuild(snap_tree)
        conn = rms.connect_memory_db(snap_tree / "memory_search.db")
        try:
            leaked = conn.execute(
                "SELECT COUNT(*) FROM memory_search WHERE name = 'leaked'"
            ).fetchone()[0]
        finally:
            conn.close()
        # the 2026-11-07 image carries live-pointing scope symlinks; following
        # one would re-index today's pool as "history"
        assert leaked == 0

    def test_history_filters(self, snap_tree):
        _rebuild(snap_tree)
        conn = rms.connect_memory_db(snap_tree / "memory_search.db")
        try:
            default = rms.search_memories(conn, "operator gates", hybrid=False)
            only = rms.search_memories(conn, "operator gates", hybrid=False, history="only")
            include = rms.search_memories(conn, "operator gates", hybrid=False, history="include")
            explicit = rms.search_memories(conn, "operator gates", hybrid=False, kind="zcode-snap")
        finally:
            conn.close()
        assert default["results"]
        assert {r["kind"] for r in default["results"]} <= {"zcode", "prime", "opencode"}
        assert only["results"]
        assert {r["kind"] for r in only["results"]} <= {"zcode-snap", "prime-snap", "opencode-snap"}
        assert any(r["kind"].endswith("-snap") for r in include["results"])
        assert explicit["results"]
        assert all(r["kind"] == "zcode-snap" for r in explicit["results"])

    def test_absent_mount_contributes_nothing_and_no_drift(self, snap_tree, monkeypatch):
        _rebuild(snap_tree)  # index WITH history rows
        monkeypatch.setattr(rms, "SNAP_ROOT", snap_tree / "unmounted")
        conn = rms.connect_memory_db(snap_tree / "memory_search.db")
        try:
            # a backup mount going away is NORMAL, not index drift
            assert rms.memory_index_stale(conn) is False
        finally:
            conn.close()
        stats = _rebuild(snap_tree)
        assert stats["history_units"] == 0
        assert stats["history_snapshots"] == 0
        assert not any(k.endswith("-snap") for k in _kinds(snap_tree))

    def test_history_row_vanishing_from_present_mount_is_drift(self, snap_tree):
        _rebuild(snap_tree)
        stale_file = (
            _image(snap_tree / "snap-root", "2026-08-26_19-00-01")
            / ".zcode"
            / "cli"
            / "memories"
            / "projects"
            / "scope-main"
            / "memory"
            / "user-workflow.md"
        )
        assert stale_file.is_file()
        stale_file.unlink()
        conn = rms.connect_memory_db(snap_tree / "memory_search.db")
        try:
            assert rms.memory_index_stale(conn) is True
        finally:
            conn.close()
