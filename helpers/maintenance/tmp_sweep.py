#!/usr/bin/env python3
"""Gate-front temp-dir hygiene sweep (tmpdir_sanitization S7).

Reaps THIS repo's /tmp residue before a gate run so a near-full tmpfs can
never crash the very run that exists to catch problems. Only removes
entries the sweep can prove this repo owns:

  * file basenames matching a known artifact glob (stray
    ``sp.db``/``tmp*.db`` scratch DBs, the TUI log, ...),
  * scratch directories matching a known prefix, or containing a known
    marker file (``sp.db``/``notes.duckdb`` — legacy bare-mkdtemp dirs),
  * owned by the current euid,
  * with mtime older than ``--min-age-hours`` (default 24).

The default is a dry-run REPORT; ``--apply`` performs the removal.
``--check`` is the machine signal: exit 1 when anything is reclaimable,
0 when clean. Never touches pytest basetemps (pytest's own retention
policy owns them), systemd/snap private dirs, X11 sockets, or another
user's files. The age guard spares any live, in-flight scratch (mtime-new).

Usage:
    python3 helpers/maintenance/tmp_sweep.py             # dry-run report
    python3 helpers/maintenance/tmp_sweep.py --apply     # remove
    python3 helpers/maintenance/tmp_sweep.py --check     # rc 1 if dirty
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_MIN_AGE_HOURS = 24.0

# Basename globs for files this repo writes directly into the temp dir.
# Deliberately NOT here: the `.`-prefixed `.{hash}-{chunk}.so` / `.hm`
# temp files — those belong to opencode's Rust/Zig TUI native library
# (extracted by Bun and mmap'd), not to this repo. See the proposal's
# 2026-09-17 attribution correction.
FILE_GLOBS = (
    "sp.db*",  # test_fuzz_shortest_path copy_production_db scratch (S1)
    "tmp*.db",  # mkstemp / NamedTemporaryFile(suffix=".db") scratch
    "tmp*.db-*",  #  ...plus its -wal / -shm siblings
    "tmp*.duckdb",  # NamedTemporaryFile(suffix=".duckdb") scratch
    "tmp*.duckdb-*",
    "search_tui.log",  # helpers/misc/search_tui_app.py event log (S6)
    "d12_hits.json",  # documented scratch outputs
    "cin_web*.json",
    "cin_ogd_*.json",
)
# Directory globs for scratch trees.
DIR_GLOBS = (
    "pix2text_*",  # helpers/pdf/pix2text_markdown.py (S4)
    "pdf_local_*",  # helpers/pdf/pdf_conv_md.py
    "lite_*",
    "fts_parity_*",  # helpers/bench/fts_duckdb_parity.py (S5)
    "md_lint_shard_*",  # helpers/misc/markdown_lint.py
    "scale_bfs_*",  # tests/bench_scale_bfs.py
    "bench_pdf_*",  # tests/bench_pdf_pipeline.py
    "fuzz_derive_insights_*",  # tests/test_fuzz_derive_insights_regions.py (S3)
)
# Files whose presence marks a bare ``tmpXXXXXX/`` mkdtemp dir as ours.
MARKER_FILES = ("sp.db", "sp.duckdb", "notes.duckdb")
# Never touch these (pytest retention, systemd/snap/X11, another user's tmp).
NEVER = (
    "pytest-of-*",
    "pytest-*",
    ".X11-unix",
    ".XIM-unix",
    ".ICE-unix",
    "systemd-private-*",
    "snap-private-tmp",
    "snap.*",
    "tmp.*",  # systemd PrivateTmp namespace dirs
)


def _owned_by_me(entry: Path) -> bool:
    try:
        return entry.lstat().st_uid == os.geteuid()
    except OSError:
        return False


def _old_enough(entry: Path, cutoff: float) -> bool:
    try:
        return entry.lstat().st_mtime < cutoff
    except OSError:
        return False


def _has_marker(entry: Path) -> bool:
    for name in MARKER_FILES:
        try:
            if (entry / name).exists():
                return True
        except OSError:
            continue
    return False


def candidates(tempdir: Path, min_age_hours: float) -> list[Path]:
    """Return the temp entries this repo owns and may reap."""
    cutoff = time.time() - min_age_hours * 3600
    found: list[Path] = []
    for entry in sorted(tempdir.iterdir(), key=lambda p: p.name):
        name = entry.name
        if any(fnmatch.fnmatch(name, pat) for pat in NEVER):
            continue
        if not _owned_by_me(entry) or not _old_enough(entry, cutoff):
            continue
        if entry.is_dir():
            if any(fnmatch.fnmatch(name, pat) for pat in DIR_GLOBS) or _has_marker(entry):
                found.append(entry)
        elif any(fnmatch.fnmatch(name, pat) for pat in FILE_GLOBS):
            found.append(entry)
    return found


def _size(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def _reap(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reap this repo's temp-dir residue (tmpdir_sanitization S7)."
    )
    ap.add_argument("--apply", action="store_true", help="remove (default: dry-run report)")
    ap.add_argument("--check", action="store_true", help="rc 1 if anything is reclaimable, else 0")
    ap.add_argument(
        "--min-age-hours",
        type=float,
        default=DEFAULT_MIN_AGE_HOURS,
        help=f"skip entries newer than this (default {DEFAULT_MIN_AGE_HOURS:g})",
    )
    ap.add_argument("--tempdir", default=tempfile.gettempdir(), help="temp root (default $TMPDIR)")
    args = ap.parse_args(argv)

    if args.apply and args.check:
        ap.error("--apply and --check are mutually exclusive")

    tempdir = Path(args.tempdir)
    if not tempdir.is_dir():
        print(f"tmp-sweep: {tempdir} is not a directory", file=sys.stderr)
        return 1

    found = candidates(tempdir, args.min_age_hours)
    total = sum(_size(p) for p in found)
    with_mb = f"{total / 1e6:.1f} MB"

    if not found:
        print(f"tmp-sweep: clean — nothing older than {args.min_age_hours:g}h under {tempdir}")
        return 0

    verb = "removed" if args.apply else "reclaimable"
    for path in found:
        mb = _size(path) / 1e6
        print(f"  {verb}: {path} ({mb:.1f} MB)")
        if args.apply:
            _reap(path)
    n = len(found)
    print(
        f"tmp-sweep: {n} entr{'y' if n == 1 else 'ies'} · {with_mb} {verb}"
        + ("" if args.apply else " (dry-run; pass --apply to remove)")
    )
    if args.check:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
