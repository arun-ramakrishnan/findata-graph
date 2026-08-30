#!/usr/bin/env python
"""Symlink-safe recursive file enumeration.

``Path.rglob("*", recurse_symlinks=True)`` (Python 3.13+) follows
directory symlinks but has NO cycle protection — a symlink loop hangs
the walk outright (verified on 3.14.4, 2026-08-30). This module is the
cycle-safe replacement shared by the two #107-contract doc walkers
(``app.py::_iter_doc_files`` and
``helpers.maintenance.rebuild_doc_search._iter_doc_files``): descend
into directory symlinks (git worktrees symlink the gitignored
``doc/local/`` at the real dir's location), never revisit a directory
inode, resolve file symlinks, skip broken ones silently — matching the
old ``rglob("*")`` + ``is_file()`` behavior for everything except
directory-symlink descent.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path


def iter_tree_files(root: Path) -> Iterator[Path]:
    """Yield every file under ``root`` (root itself never yielded).

    Directory symlinks are followed; revisits are cut by
    ``(st_dev, st_ino)``, so symlink cycles terminate and a directory
    reachable by two paths is walked exactly once (which path wins is
    deterministic for a given tree — entries are scanned name-sorted).
    Broken symlinks are skipped, file symlinks resolved, unreadable
    directories ignored — the same silent-degradation contract the
    rglob walker had.
    """
    try:
        root_st = root.stat()
    except OSError:
        return
    if not os.path.isdir(root):
        return
    seen: set[tuple[int, int]] = {(root_st.st_dev, root_st.st_ino)}
    stack: list[Path] = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = sorted(os.scandir(cur), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=True):
                    yield Path(entry.path)
                elif entry.is_dir(follow_symlinks=True):
                    st = entry.stat(follow_symlinks=True)
                    key = (st.st_dev, st.st_ino)
                    if key not in seen:
                        seen.add(key)
                        stack.append(Path(entry.path))
            except OSError:
                continue
