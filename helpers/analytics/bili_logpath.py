#!/usr/bin/env python3
"""Discover the billion-context proxy log path without hardcoding it.

The proxy is an npm module (`billion-context`); its working directory -- and
therefore `bili.log` -- lives wherever it was started from, not at a fixed path.
This walks `/proc` for the running proxy and reads its cwd, falling back to the
known state roots. The watcher suite (bili_tunewatch, bili_monitor,
bili_watch_event, bili_thresh_replay) used to hardcode
`/home/arun/.local/state/billion-context/bili.log`, which is only correct for
this machine and this user -- a second proxy started elsewhere, or a different
npm module logging under its own name, was invisible to every one of them.

Pure discovery: no db, no repo import, no writes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOTS = [
    Path.home() / ".local" / "state" / "billion-context",
    Path(__file__).resolve().parents[2] / "bench_data" / "billion-context",
]


def _proc_cwd_logs() -> list[Path]:
    """Every `bili.log` sitting in a running billion-context process's cwd."""
    out: list[Path] = []
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            cmd = (pid_dir / "cmdline").read_text(errors="replace").replace("\x00", " ")
        except OSError:
            continue
        if "billion-context" not in cmd:
            continue
        try:
            lp = (pid_dir / "cwd").resolve() / "bili.log"
        except OSError:
            continue
        if lp.is_file():
            out.append(lp)
    return out


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:  # vanished between is_file() and stat()
        return 0.0


def discover_log() -> str:
    """The live `bili.log` path: env override, then /proc, then known roots.

    A missing file is not an error -- the caller may be running where no proxy
    is up, and the returned path is still usable as a default for reporting.

    With MULTIPLE running proxies (the ambiguity this module exists to handle)
    the newest-mtime log wins and the ambiguity is announced on stderr. A
    silent first-match pinned the suite to whichever pid /proc happened to
    yield first -- and the shell twin in bili_status.sh globbed in LEXICAL pid
    order (999 sorts before 1000), so the two readers could disagree about
    which log was live.
    """
    env = os.environ.get("BILI_LOG")
    if env:
        return env
    cands = sorted(set(_proc_cwd_logs()))
    if cands:
        pick = max(cands, key=_mtime)
        if len(cands) > 1:
            names = " ".join(str(c) for c in cands)
            print(
                f"bili_logpath: {len(cands)} running proxies -- watching the "
                f"newest log {pick} (candidates: {names})",
                file=sys.stderr,
            )
        return str(pick)
    for root in ROOTS:
        p = root / "bili.log"
        if p.is_file():
            return str(p)
    return str(ROOTS[0] / "bili.log")


def discover_old(log: str | None = None) -> str:
    """The rotated sibling of `log` (default: `<log>.old`)."""
    return f"{log or discover_log()}.old"


def discover_all() -> tuple[str, str]:
    """(live_log, rotated_log) for the whole family.

    BILI_LOG pins the live log (via discover_log); BILI_LOG_OLD pins the
    rotated sibling -- advertised by the suite docstrings from day one but
    only wired here later (quality review #3). Either env alone is enough:
    the other falls back to discovery / `<log>.old`.
    """
    log = discover_log()
    old = os.environ.get("BILI_LOG_OLD") or f"{log}.old"
    return log, old
