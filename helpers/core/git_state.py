#!/usr/bin/env python3
"""Git facts about the CALLING tree — the shared-store apply guard's inputs.

`memory/` is a symlink in linked worktrees, so every tree shares ONE
`research.db` and the same search sidecars. An incremental APPLY computes
its delta as (this tree's working set) vs (the shared index's stored
fingerprints); in a tree that is BEHIND the index, that delta is non-empty
*by construction* — newer rows look changed, not-yet-existing rows look
deleted — so the apply rewrites the shared surface backward and bumps the
shared generation for content that never changed
(`worktree_fresh_trample_guard`, live-reproduced 2026-10-12: two bumps and
two full-surface rewrites per round-trip, plus "ghost" snapshot MISMATCHes).
The guard in `rebuild_common` needs three facts about the tree the session
lives in — never the DB's tree, which is main's — its HEAD sha, its worktree
name, and whether one HEAD is an ancestor of another.

Every function here is total: missing git binary, no repository, a git error
or a timeout returns `None` / `""` instead of raising. Callers treat
"unknown" as warn-only, so a git-less fixture is never refused.

The queries inherit the process working directory unless a caller passes an
explicit `cwd` — the facts describe the caller, and the CWD is the caller.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_TIMEOUT = 10.0

# merge-base takes revisions, not arbitrary text: a sha-looking guard keeps a
# malformed stamp (a truncated read, a hand-edited row) from being passed as
# something git might interpret.
_SHA_RE = re.compile(r"\A[0-9a-fA-F]{4,64}\Z")


def _run(*args: str, cwd: Path | str | None = None) -> tuple[int, str] | None:
    """(returncode, stdout) for one git query; None when git could not run."""
    try:
        out = subprocess.run(  # noqa: S603  # PATH-resolved git, fixed args (house pattern)
            ("git", *args),  # noqa: S607
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=str(cwd) if cwd is not None else None,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return out.returncode, out.stdout.strip()


def git(*args: str, cwd: Path | str | None = None) -> str | None:
    """One SUCCESSFUL git query's stdout; None on any failure.

    Report/guard metadata must never gate on a git problem, so a non-zero
    exit is indistinguishable from a missing git — both are "unknown".
    """
    res = _run(*args, cwd=cwd)
    if res is None:
        return None
    rc, out = res
    if rc != 0:
        return None
    return out or None


def head_sha(cwd: Path | str | None = None) -> str | None:
    """HEAD sha of the tree, or None outside a repository."""
    return git("rev-parse", "HEAD", cwd=cwd)


def worktree_name(cwd: Path | str | None = None) -> str:
    """`""` for the main checkout; `<name>` for a linked worktree.

    The name is the git dir's basename (e.g.
    `~/.stax/worktrees/findata-graph/.git` -> `findata-graph`), matching the
    `outputs/wt/<name>/` layout gate_query and search_tui already enumerate.
    """
    git_dir = git("rev-parse", "--absolute-git-dir", cwd=cwd)
    common = git("rev-parse", "--git-common-dir", cwd=cwd)
    if not git_dir or not common:
        return ""
    git_p, common_p = Path(git_dir).resolve(), Path(common).resolve()
    return "" if git_p == common_p else git_p.name


def is_ancestor(older: str, newer: str, cwd: Path | str | None = None) -> bool | None:
    """True when `older` is an ancestor of `newer`; None when git can't say.

    `git merge-base --is-ancestor` exits 0 (yes) / 1 (no) / other (bad
    object, shallow or absent repo). The third case is "unknown", never
    False, so callers can tell "definitely forward" from "cannot tell" and
    stay warn-only on the latter.
    """
    if not _SHA_RE.match(older) or not _SHA_RE.match(newer):
        return None
    res = _run("merge-base", "--is-ancestor", older, newer, cwd=cwd)
    if res is None:
        return None
    rc, _out = res
    if rc == 0:
        return True
    if rc == 1:
        return False
    return None
