"""Shared-store APPLY guard: the S2 refusal matrix (worktree_fresh_trample_guard).

`stale_apply_reason` refuses when this tree's HEAD is not a forward step
over what the shared index reflects — **behind** (an ancestor of the
stamped HEAD) or **divergent** (neither an ancestor nor a descendant,
which is the shape a patch-testing worktree carrying its own commits
has). Same-HEAD, forward, and every "cannot tell" proceed: a git-less or
pre-stamp index must never be refused.

Git state is the guard's only non-deterministic input, so it is injected
here — once those facts are fixed the predicate is a pure function of
(current, stamped). The `run_rebuild_cli` cases then exercise the wiring
end to end: a refusal must reach the exit code without the rebuild ever
running, and `--sandbox` must be exempt because it cannot reach the
shared index.
"""

import sqlite3

import pytest

from helpers.core import git_state
from helpers.maintenance import rebuild_common as rbc

STAMPED = "a" * 40
CURRENT = "b" * 40
TABLE = "doc_search_info"


def _index(tmp_path, *, head=STAMPED, worktree="graph_algos", ts="2026-10-12 19:00:00"):
    """A temp index carrying — or, with ``head=None``, omitting — the S1 stamp."""
    db = tmp_path / "doc_search.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE doc_search_info (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if head is not None:
        con.executemany(
            "INSERT INTO doc_search_info (key, value) VALUES (?, ?)",
            [("apply_head", head), ("apply_worktree", worktree), ("apply_ts", ts)],
        )
    con.commit()
    con.close()
    return db


def _spec(db, *, stamp_table=TABLE):
    """A recording spec; ``calls`` proves whether a rebuild reached the index."""
    calls = []

    def _rebuild(db_path, **kwargs):
        calls.append((db_path, kwargs))
        return {"indexed": 0}

    spec = rbc.RebuildCliSpec(
        description="probe",
        default_db=str(db),
        db_help="db",
        check_help="check",
        incremental_help="incremental",
        full_help="full",
        rebuild_fn=_rebuild,
        summary=lambda stats: "ok",
        migrated_msg="",
        stamp_table=stamp_table,
    )
    return spec, calls


def _git(monkeypatch, *, head=CURRENT, ancestry, worktree=""):
    monkeypatch.setattr(git_state, "head_sha", lambda cwd=None: head)
    monkeypatch.setattr(git_state, "worktree_name", lambda cwd=None: worktree)
    monkeypatch.setattr(git_state, "is_ancestor", ancestry)


def _consulted(*_args, **_kwargs):
    raise AssertionError("git_state consulted on a case that must short-circuit")


# --------------------------------------------------------------------------- #
# Predicate: cases that must NOT refuse (all fail-open by design)              #
# --------------------------------------------------------------------------- #


def test_unarmed_spec_never_consults_git(tmp_path, monkeypatch):
    """stamp_table=None (the convo duckdb lane) is out of scope entirely."""
    db = _index(tmp_path)
    spec, _ = _spec(db, stamp_table=None)
    monkeypatch.setattr(git_state, "head_sha", _consulted)
    monkeypatch.setattr(git_state, "is_ancestor", _consulted)
    monkeypatch.setattr(git_state, "worktree_name", _consulted)
    assert rbc.stale_apply_reason(db, spec) is None


def test_same_head_proceeds(tmp_path, monkeypatch):
    """Dirty edits on the stamped HEAD are NOT stale — the edit-then-fresh case."""
    db = _index(tmp_path, head=CURRENT)
    spec, _ = _spec(db)
    monkeypatch.setattr(git_state, "head_sha", lambda cwd=None: CURRENT)
    monkeypatch.setattr(git_state, "is_ancestor", _consulted)
    assert rbc.stale_apply_reason(db, spec) is None


def test_missing_stamp_proceeds(tmp_path, monkeypatch):
    """A pre-S1 index has no stamp: warn-only, and the apply arms the guard."""
    db = _index(tmp_path, head=None)
    spec, _ = _spec(db)
    monkeypatch.setattr(git_state, "head_sha", lambda cwd=None: CURRENT)
    monkeypatch.setattr(git_state, "is_ancestor", _consulted)
    assert rbc.stale_apply_reason(db, spec) is None


def test_missing_index_proceeds(tmp_path, monkeypatch):
    """An absent file (a spec-driven fixture) is "no stamp", not a refusal."""
    spec, _ = _spec(tmp_path / "absent.db")
    monkeypatch.setattr(git_state, "head_sha", lambda cwd=None: CURRENT)
    monkeypatch.setattr(git_state, "is_ancestor", _consulted)
    assert rbc.stale_apply_reason(tmp_path / "absent.db", spec) is None


def test_no_git_proceeds(tmp_path, monkeypatch):
    """No HEAD at all (git-less fixture): warn-only."""
    db = _index(tmp_path)
    spec, _ = _spec(db)
    _git(monkeypatch, head=None, ancestry=_consulted)
    assert rbc.stale_apply_reason(db, spec) is None


def test_forward_proceeds(tmp_path, monkeypatch):
    """The stamped HEAD is an ancestor of ours: a clean advance."""
    db = _index(tmp_path)
    spec, _ = _spec(db)
    _git(
        monkeypatch,
        ancestry=lambda older, newer, cwd=None: (older, newer) == (STAMPED, CURRENT),
    )
    assert rbc.stale_apply_reason(db, spec) is None


def test_cannot_tell_proceeds(tmp_path, monkeypatch):
    """An unordered pair is "unknown", never divergent."""
    db = _index(tmp_path)
    spec, _ = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: None)
    assert rbc.stale_apply_reason(db, spec) is None


def test_half_known_ordering_proceeds(tmp_path, monkeypatch):
    """Not-behind but the reverse is unknown → warn-only, not divergent.

    A stamped sha this clone has never seen fails open on both directions;
    the guard must only refuse on a *verified* divergence.
    """
    db = _index(tmp_path)
    spec, _ = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: None if older == STAMPED else False)
    assert rbc.stale_apply_reason(db, spec) is None


# --------------------------------------------------------------------------- #
# Predicate: the two refusal shapes                                            #
# --------------------------------------------------------------------------- #


def test_behind_is_refused(tmp_path, monkeypatch):
    db = _index(tmp_path)
    spec, _ = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: True)
    reason = rbc.stale_apply_reason(db, spec)
    assert reason is not None
    assert "BEHIND" in reason
    assert STAMPED[:12] in reason
    assert CURRENT[:12] in reason
    assert "graph_algos" in reason
    assert "2026-10-12 19:00:00" in reason
    assert "--allow-stale" in reason and "--sandbox" in reason


def test_divergent_is_refused(tmp_path, monkeypatch):
    """F8: neither ancestor nor descendant → refused, not passed."""
    db = _index(tmp_path)
    spec, _ = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: False)
    reason = rbc.stale_apply_reason(db, spec)
    assert reason is not None
    assert "DIVERGED" in reason
    assert STAMPED[:12] in reason and CURRENT[:12] in reason
    assert "--allow-stale" in reason


def test_is_ancestor_rejects_a_malformed_sha():
    """A hand-edited stamp must never reach git as a revision."""
    assert git_state.is_ancestor("not-a-sha", CURRENT) is None
    assert git_state.is_ancestor(CURRENT, "zz") is None


# --------------------------------------------------------------------------- #
# Wiring: run_rebuild_cli must reach the exit code without writing             #
# --------------------------------------------------------------------------- #


def test_refusal_blocks_the_apply_and_leaves_the_index_alone(tmp_path, monkeypatch):
    """S2's gate: refused → exit 1, rebuild never ran, stamp untouched."""
    db = _index(tmp_path)
    spec, calls = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: True)
    assert rbc.run_rebuild_cli([], spec) == 1
    assert calls == []  # no rebuild_fn call ⇒ no index write, no generation bump
    assert rbc.read_apply_stamp(db, TABLE)["apply_head"] == STAMPED


def test_divergent_needs_allow_stale_too(tmp_path, monkeypatch):
    db = _index(tmp_path)
    spec, calls = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: False)
    assert rbc.run_rebuild_cli([], spec) == 1
    assert calls == []
    assert rbc.run_rebuild_cli(["--allow-stale"], spec) == 0
    assert len(calls) == 1


def test_allow_stale_applies_and_restamps(tmp_path, monkeypatch):
    """A deliberate apply re-stamps, so the other tree is not blocked after."""
    db = _index(tmp_path)
    spec, calls = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: True)
    assert rbc.run_rebuild_cli(["--allow-stale"], spec) == 0
    assert len(calls) == 1
    assert rbc.read_apply_stamp(db, TABLE)["apply_head"] == CURRENT


def test_sandbox_previews_without_touching_the_shared_index(tmp_path, monkeypatch):
    """--sandbox is exempt from the refusal: it cannot reach the shared index."""
    db = _index(tmp_path)
    spec, calls = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: True)
    assert rbc.run_rebuild_cli(["--sandbox"], spec) == 0
    assert len(calls) == 1
    scratch, _kwargs = calls[0]
    assert scratch != db and scratch.name == db.name
    # a sandbox run must NOT claim the shared index reflects this working set
    assert rbc.read_apply_stamp(db, TABLE)["apply_head"] == STAMPED


def test_check_never_consults_the_guard(tmp_path, monkeypatch):
    """--check is read-only; the guard must not run on it."""
    db = _index(tmp_path)
    spec, calls = _spec(db)
    monkeypatch.setattr(git_state, "head_sha", lambda cwd=None: CURRENT)
    monkeypatch.setattr(git_state, "is_ancestor", _consulted)
    monkeypatch.setattr(git_state, "worktree_name", lambda cwd=None: "")
    rbc.run_rebuild_cli(["--check"], spec)
    assert len(calls) == 1
    assert calls[0][1]["write"] is False


def test_unstamped_apply_stamps_the_index(tmp_path, monkeypatch):
    """Pre-S1 index: the first apply writes the stamp instead of refusing."""
    db = _index(tmp_path, head=None)
    spec, calls = _spec(db)
    _git(monkeypatch, ancestry=_consulted)
    assert rbc.run_rebuild_cli([], spec) == 0
    assert len(calls) == 1
    assert rbc.read_apply_stamp(db, TABLE)["apply_head"] == CURRENT


@pytest.mark.parametrize("flag", ["--check", "--sandbox"])
def test_check_and_sandbox_are_mutually_exclusive(tmp_path, monkeypatch, flag):
    """--sandbox is the read-only twin of --check, not a companion."""
    db = _index(tmp_path)
    spec, _ = _spec(db)
    _git(monkeypatch, ancestry=lambda older, newer, cwd=None: False)
    with pytest.raises(SystemExit):
        rbc.run_rebuild_cli([flag, "--check" if flag == "--sandbox" else "--sandbox"], spec)
