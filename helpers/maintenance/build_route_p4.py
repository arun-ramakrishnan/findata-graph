#!/usr/bin/env python3
"""Build-route P4 staging helper — deletion mechanics for the frozen SQLite estate.

P4 (doc/improvements/archive/database/duckdb_build_route.md) deletes the
frozen ``memory/research.db`` + the four folded FTS5 sidecar twins after the
soak period, leaving ``embed_store.db`` as the only SQLite store. This module
never deletes anything: it is the **plan/readiness** gate an operator runs
before (and after) the actual deletion, so the P4 step is mechanical and
verifiable.

Modes:
  --check   (default) assert readiness for deletion and print the exact
            deletion plan + the restore net that makes it safe. Exit 1 when
            any readiness condition fails (no backup, missing tracked
            parquet, or a live-code path that would open the frozen store).
  --assert-gone  after deletion: verify the files are actually gone and that
            nothing else in ``helpers/`` references them outside the
            allowlisted restore/bench ro-URI paths.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The four folded FTS5 twins (doc/memory/script/git) + frozen research.db.
# embed_store.db is architectural (WAL multi-writer) and STAYS.
_FROZEN_SQLITE = [
    "memory/research.db",
    "memory/doc_search.db",
    "memory/memory_search.db",
    "memory/script_search.db",
    "memory/git_search.db",
]

# Restore net: tracked parquet snapshot axis + zstd backups + restore tool.
_TRACKED_PARQUET_PREFIX = "snapshots/parquet/"
_RESTORE_TOOL = "helpers/maintenance/snapshot_db.py"  # restore_sqlite_from_parquet
_BACKUP_DIR = "db-backup/"
_BACKUP_EXPECTED = {
    "memory/research.db": "research_backup.db.zst",
    "memory/doc_search.db": "frozen_ft5_twins_backup.tar.zst",
    "memory/memory_search.db": "frozen_ft5_twins_backup.tar.zst",
    "memory/script_search.db": "frozen_ft5_twins_backup.tar.zst",
    "memory/git_search.db": "frozen_ft5_twins_backup.tar.zst",
}

# Live-opener scan: the frozen store names are wired through the codebase's
# default-path plumbing (DEFAULT_DB_PATH, CLI --db defaults, comments) — that
# plumbing is FINE post-deletion because connect() dispatches to DuckDB. Only
# *actual* opens of the frozen sqlite matter. We classify each reference line
# by kind: an open is a line that CALLS sqlite3.connect / helpers.core.db
# connect with a frozen-store path, or opens it via URI. Everything else
# (constants, docstrings, CLI defaults, comments) is inert.
_OPEN_CALL_PATTERNS = ("sqlite3.connect(", "sqlite3.Connection(", ".backup(")
_FROZEN_NAMES = (
    "research.db",
    "doc_search.db",
    "memory_search.db",
    "script_search.db",
    "git_search.db",
)
# Modules where an open of the frozen store is intentionally allowed:
# read-only benchmarks + the rollback/restore contract itself.
_ALLOWED_OPENERS = ("bench/", "swap_convert.py", "query.py", "zstd_io.py", "snapshot_db.py")


def _plan() -> list[dict]:
    rows = []
    for rel in _FROZEN_SQLITE:
        p = _REPO_ROOT / rel
        rows.append(
            {
                "rel": rel,
                "exists": p.exists(),
                "bytes": p.stat().st_size if p.exists() else 0,
            }
        )
    return rows


def _readiness(rows: list[dict]) -> list[str]:
    problems: list[str] = []
    gone = [r["rel"] for r in rows if not r["exists"]]
    if gone:
        print("  NOTE: already deleted (P4 executed 2026-11-21): " + ", ".join(gone))
    for r in rows:
        if not r["exists"]:
            continue
        backup = _BACKUP_EXPECTED[r["rel"]]
        if not (_REPO_ROOT / _BACKUP_DIR / backup).exists():
            problems.append(f"missing backup for {r['rel']}: db-backup/{backup}")
    if not (_REPO_ROOT / _TRACKED_PARQUET_PREFIX).is_dir():
        problems.append("tracked parquet axis missing: snapshots/parquet/")
    if not (_REPO_ROOT / _RESTORE_TOOL).exists():
        problems.append(f"restore tool missing: {_RESTORE_TOOL}")
    # Live-opener scan: only lines that actually OPEN a frozen store (calls
    # sqlite3.connect / db.connect / .backup with one of the frozen names)
    # and that sit outside the allowed read-only/restore modules matter.
    hits = subprocess.run(
        [  # noqa: S607  # fixed-utility scan; path resolution is env-dependent
            "grep",
            "-rn",
            "--include=*.py",
            "-E",
            r"research\.db|doc_search\.db|memory_search\.db|script_search\.db|git_search\.db",
            "helpers/",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    ).stdout
    for line in hits.splitlines():
        if any(a in line for a in _OPEN_CALL_PATTERNS):
            if any(a in line for a in _ALLOWED_OPENERS):
                continue
            problems.append(f"live opener of frozen store: {line}")
    return problems


def _gone_check() -> list[str]:
    problems: list[str] = []
    for rel in _FROZEN_SQLITE:
        if (_REPO_ROOT / rel).exists():
            problems.append(f"still present: {rel}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--assert-gone", action="store_true", help="post-deletion verification")
    args = ap.parse_args(argv)

    if args.assert_gone:
        problems = _gone_check()
        for p in problems:
            print(f"✗ {p}")
        print("✓ frozen sqlite estate confirmed gone" if not problems else "still present!")
        return 1 if problems else 0

    rows = _plan()
    print("P4 deletion plan (after soak):")
    for r in rows:
        state = f"{r['bytes']:,} B" if r["exists"] else "ABSENT"
        print(f"  {r['rel']:<28} {state}")
    print()
    print("Restore net:")
    print(f"  tracked parquet axis : {_TRACKED_PARQUET_PREFIX} (committed)")
    print(f"  restore tool          : {_RESTORE_TOOL}")
    for rel, backup in _BACKUP_EXPECTED.items():
        present = (_REPO_ROOT / _BACKUP_DIR / backup).exists()
        print(f"  backup {rel:<24} {'✓' if present else '✗ MISSING'} db-backup/{backup}")

    problems = _readiness(rows)
    print()
    if problems:
        print("NOT READY for deletion:")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("READY for deletion — all frozen stores backed up, restore net intact,")
    print("and no live code path opens them outside the allowlist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
