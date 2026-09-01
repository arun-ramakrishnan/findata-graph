#!/usr/bin/env python3
"""Markdown lint body — runs version-pinned markdownlint-cli2 with the
repo-root ``.markdownlint-cli2.jsonc`` (globs live in the file: doc/ prose
base + findata Tier-1 defect override). The qa step ``md-lint`` calls this;
without node/npx on PATH it prints SKIP and exits 0 (same convention as
frontend-check's Node gating), so node-less boxes keep the gate green.

LINT-ONLY by design — there is deliberately no ``--fix`` here: the fixer
rewrites whitespace wholesale and is forbidden over findata (proposal §5,
the 2026-08-19 marker-collision incident).

Stale-scan cache (2026-09-01, ``archive/tooling/md_lint_cache.md``):
verdicts are recorded per file in the
gitignored sidecar ``memory/md_lint_cache.db`` keyed by content hash +
config hash + cli2 version; only changed/new files are re-linted. A stale
subset runs inside a scratch symlink mirror that reproduces repo-relative
paths, so overrides/filters match identically — cli2 command-line globs
are ADDITIVE to the config globs and cannot scope a subset themselves.
Deleted files are pruned; a config edit or version bump flushes
everything. ``--full`` bypasses the cache and streams raw cli2 output
(hand-fix sessions). A missing/unparseable config or a locked sidecar
degrades to the uncached full run.

Exit codes: 0 clean or SKIP · 1 findings present · child's rc for
execution failures (config/npx/download).

Usage::

    python3 helpers/misc/markdown_lint.py            # digest (qa gate)
    python3 helpers/misc/markdown_lint.py --full     # raw cli2 output, uncached
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# House bootstrap (mirrors doc_query.py): repo root on sys.path BEFORE the
# helpers import so the script works identically as a subprocess and under
# pytest.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.core.db import connect  # noqa: E402

# Pinned (proposal §4.2); bump = a measured decision, never a silent float.
# It is also part of the cache key — a bump flushes all recorded verdicts.
MARKDOWNLINT_CLI2_VERSION = "0.23.2"
_NPX = shutil.which("npx")
_CONFIG_PATH = REPO_ROOT / ".markdownlint-cli2.jsonc"
_CACHE_DB = REPO_ROOT / "memory" / "md_lint_cache.db"  # sidecar, gitignored
# cli2 default formatter: ``path/file.md:12:5 error MD037/Name message``
# (the column is optional and the severity word is always present)
_VIOLATION = re.compile(r"^.+?:\d+(?::\d+)?\s+(?:error|warning)\s+(MD\d+)(?:/\S+)?\s")
_SAMPLE_LINES = 15  # raw violation lines shown under the digest when red
_CMD = [_NPX or "npx", "-y", f"markdownlint-cli2@{MARKDOWNLINT_CLI2_VERSION}"]


def _digest_lines(lines: list[str]) -> tuple[int, int, Counter[str]]:
    """Return (violations, files, per-rule counts) from cli2 output lines."""
    rules: Counter[str] = Counter()
    files: set[str] = set()
    for line in lines:
        m = _VIOLATION.match(line)
        if m:
            rules[m.group(1)] += 1
            files.add(line.split(":", 1)[0])
    return sum(rules.values()), len(files), rules


def _jsonc_loads(text: str) -> dict:
    """Parse JSONC (``//`` and ``/* */`` comments) honoring string literals."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and text[i + 1 : i + 2] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "/" and text[i + 1 : i + 2] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        else:
            out.append(c)
        i += 1
    return json.loads("".join(out))


def _corpus_files() -> list[Path]:
    """Expand the config globs (``<root>/**/*.md``) minus ``ignores``.

    The walk must stay in sync with the config globs — a globs edit is a
    rebuild (delete the sidecar), while rule/override edits self-flush via
    the config-hash key.
    """
    try:
        cfg = _jsonc_loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        ignores = [str(p) for p in cfg.get("ignores", [])]
        roots = sorted({str(g).split("/**", 1)[0] for g in cfg.get("globs", [])})
    except OSError, ValueError:
        return []
    files: list[Path] = []
    for root in roots:
        base = REPO_ROOT / root
        if base.is_dir():
            files.extend(sorted(base.rglob("*.md")))
    return [
        f
        for f in files
        if not any(fnmatch.fnmatch(f.relative_to(REPO_ROOT).as_posix(), pat) for pat in ignores)
    ]


def _cache_key(f: Path) -> str:
    """Stable per-file key: repo-relative posix (== cli2's printed path)."""
    try:
        return f.relative_to(REPO_ROOT).as_posix()
    except ValueError:  # out-of-repo (tests) — mirrored under the same shape
        return str(f)


def _hash_file(f: Path) -> str:
    return hashlib.blake2b(f.read_bytes(), digest_size=16).hexdigest()


def _config_signature() -> str:
    try:
        raw = _CONFIG_PATH.read_bytes()
    except OSError:
        raw = b""
    return hashlib.blake2b(raw + MARKDOWNLINT_CLI2_VERSION.encode(), digest_size=16).hexdigest()


def _cache_open() -> sqlite3.Connection | None:
    """Open the sidecar (creating schema); None degrades to an uncached run."""
    try:
        _CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        # House P0: sidecars go through helpers.core.db.connect (fk/wal off —
        # a single-writer key-value cache, not relational state).
        conn = connect(_CACHE_DB, row_factory=None, enable_fk=False, wal=False)
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS verdicts ("
            "path TEXT PRIMARY KEY, hash TEXT NOT NULL, violations TEXT NOT NULL)"
        )
        return conn
    except sqlite3.Error:
        return None


def _split_stale(
    conn: sqlite3.Connection, files: list[Path]
) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Flush on config/version change, prune deleted files, split stale.

    Returns ``(cached, stale)`` where ``cached`` maps key -> recorded
    violation lines and ``stale`` is a list of ``(key, hash)`` pairs to scan.
    """
    sig = _config_signature()
    if dict(conn.execute("SELECT key, value FROM meta")).get("config_hash") != sig:
        conn.execute("DELETE FROM verdicts")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('config_hash', ?)", (sig,))
        conn.commit()
    rows = {p: (h, v) for p, h, v in conn.execute("SELECT path, hash, violations FROM verdicts")}
    cached: dict[str, list[str]] = {}
    stale: list[tuple[str, str]] = []
    for f in files:
        key = _cache_key(f)
        recorded = rows.get(key)
        digest = _hash_file(f)
        if recorded and recorded[0] == digest:
            cached[key] = json.loads(recorded[1])
        else:
            stale.append((key, digest))
    live = {_cache_key(f) for f in files}
    dead = [(p,) for p in rows if p not in live]
    if dead:
        conn.executemany("DELETE FROM verdicts WHERE path = ?", dead)
        conn.commit()
    return cached, stale


def _mirror_scratch(stale: list[tuple[str, str]]) -> Path:
    """Build the scratch tree: config copy + one symlink per stale file."""
    scratch = Path(tempfile.mkdtemp(prefix="md_lint_shard_"))
    shutil.copyfile(_CONFIG_PATH, scratch / _CONFIG_PATH.name)
    for key, _ in stale:
        target = Path(key) if key.startswith("/") else REPO_ROOT / key
        dest = scratch / key.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target.resolve(), dest)
    return scratch


def _run_cli2(cwd: Path) -> tuple[list[str], int]:
    proc = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are version-pinned constants
        _CMD,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return (proc.stdout.splitlines() if proc.stdout else []), proc.returncode


def _run_stale(
    stale: list[tuple[str, str]],
) -> tuple[dict[str, list[str]], list[str], list[str], int]:
    """Lint the stale subset (or the whole corpus when the walk failed).

    Returns ``(fresh, stray, raw, rc)``: per-key violation lines for stale
    files, violation lines we could not map to a stale key, the full raw
    output (for failure tails), and the child's exit code.
    """
    scratch = _mirror_scratch(stale) if stale else None
    try:
        raw, rc = _run_cli2(scratch if scratch is not None else REPO_ROOT)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
    fresh: dict[str, list[str]] = {}
    stray: list[str] = []
    stale_keys = {key for key, _ in stale}
    for line in raw:
        if not _VIOLATION.match(line):
            continue
        head = line.split(":", 1)[0]
        if head in stale_keys:
            fresh.setdefault(head, []).append(line)
        elif f"/{head}" in stale_keys:  # out-of-repo keys mirror as abs-less paths
            fresh.setdefault(f"/{head}", []).append(line)
        else:
            stray.append(line)
    return fresh, stray, raw, rc


def _report(lines: list[str], rc: int) -> int:
    """Print the digest; exit 1 on findings, else the child's 0/1 verdict."""
    violations, nfiles, rules = _digest_lines(lines)
    print(f"{violations} violation(s) in {nfiles} file(s)")
    for rule, count in rules.most_common():
        print(f"  {rule}  {count}")
    if violations:
        print(f"sample (last {_SAMPLE_LINES}):")
        tail = [ln for ln in lines if _VIOLATION.match(ln)][-_SAMPLE_LINES:]
        print("\n".join(f"  {ln}" for ln in tail))
        print("exit 1 (findings present)")
        return 1
    print("clean")
    return rc


def _record_and_report(
    conn: sqlite3.Connection | None,
    stale: list[tuple[str, str]],
    fresh: dict[str, list[str]],
    raw: list[str],
    rc: int,
    lines: list[str],
) -> int:
    """Persist verdicts for the scanned set, then print the digest."""
    if not lines and rc not in (0, 1):
        # Not a lint verdict — npx/config/download failure: surface the raw tail.
        print("\n".join(raw[-30:]))
        print(f"FAILED: markdownlint-cli2 exited {rc} (no violation lines parsed)")
        if conn is not None:
            conn.close()
        return rc

    if conn is not None:
        if stale and rc in (0, 1):
            # rc >= 2 is an execution failure (npx/config) — recording the
            # scanned set as clean would silently mask their violations.
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?)",
                    [(key, digest, json.dumps(fresh.get(key, []))) for key, digest in stale],
                )
                conn.commit()
            except sqlite3.Error:
                pass  # verdict already reported; a lost record only costs a re-scan
        conn.close()
    return _report(lines, rc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--full",
        action="store_true",
        help="stream raw markdownlint-cli2 output, bypassing the cache",
    )
    args = parser.parse_args(argv)

    print(
        f"markdown lint (markdownlint-cli2 {MARKDOWNLINT_CLI2_VERSION}; "
        "config: .markdownlint-cli2.jsonc)"
    )
    if _NPX is None or shutil.which("node") is None:
        print("SKIP: node/npx not on PATH — markdown lint not run (needs Node >= 22)")
        return 0

    if args.full:
        return subprocess.run(_CMD, cwd=REPO_ROOT).returncode  # noqa: S603  # same shape as _run_cli2

    files = _corpus_files()
    conn = _cache_open() if files else None
    cached: dict[str, list[str]] = {}
    stale: list[tuple[str, str]] = []
    if conn is not None:
        try:
            cached, stale = _split_stale(conn, files)
        except sqlite3.Error, OSError:
            stale = [(_cache_key(f), _hash_file(f)) for f in files]
        else:
            print(
                f"scan: {len(stale)}/{len(files)} files "
                f"({len(files) - len(stale)} unchanged, answered from cache)"
            )

    fresh, stray, raw, rc = _run_stale(stale) if (stale or not files) else ({}, [], [], 0)
    keys = [_cache_key(f) for f in files]
    lines = [ln for key in keys for ln in [*fresh.get(key, []), *cached.get(key, [])]]
    lines.extend(stray)
    return _record_and_report(conn, stale, fresh, raw, rc, lines)


if __name__ == "__main__":
    raise SystemExit(main())
