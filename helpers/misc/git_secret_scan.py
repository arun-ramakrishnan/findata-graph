#!/usr/bin/env python3
"""Single-pass unique-blob secret scan over the full git object database.

Background: per-commit `git grep` is O(commits x tree) and blew up twice on
this repo's 10k+ commit history. This scanner enumerates every UNIQUE blob
once (rev-list --all --objects -> batch-check size filter -> cat-file --batch
streaming), which collapses the history to ~23k scannable blobs (~76s).

Incremental mode: git blobs are content-addressed and immutable, so the set
of scanned blob SHAs is an exact resume cursor. State (timestamp + scanned
set) is written under .git/secret-scan/ — repo-local, never tracked, never
pushed. A fresh clone simply runs full mode.

2026-08-17 full scan: 22,850 blobs, 14 hits, all one already-revoked
GOOGLE_API_KEY (see the private security review under doc/local (untracked)
SEC-9). Run via `make secret-scan`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = PROJECT_ROOT / ".git" / "secret-scan"
STATE_FILE = STATE_DIR / "state.json"
BLOB_SET_FILE = STATE_DIR / "blobs.txt.gz"
MAX_SIZE = 1_048_576  # secrets live in small text blobs; >1MB = binary junk

# (id, compiled pattern). Entropy-bearing prefixes only; placeholders such
# as "YOUR_API_KEY" or empty strings never match.
PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    ("aws-key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("github-pat", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("openai-key", re.compile(rb"\bsk-(proj-)?[A-Za-z0-9]{24,}\b")),
    ("slack-token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api", re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("pem-private", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY")),
    ("telegram", re.compile(rb"\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}\b")),
    ("stripe", re.compile(rb"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{16,}\b")),
    (
        "generic-assign",
        re.compile(
            rb"(?i)\b(api[_-]?key|token|passwd|password|secret)\b\s*[:=]\s*[\"'][A-Za-z0-9+/_\-]{20,}[\"']"
        ),
    ),
    (
        "env-assign",
        re.compile(rb"(?m)^(PADDLE_API_KEY|AZURE_OPENAI_API_KEY|OPENAI_API_KEY)\s*=\s*\S+"),
    ),
]


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"version": 1, "scanned": set()}
    state = json.loads(STATE_FILE.read_text())
    scanned: set[str] = set()
    if BLOB_SET_FILE.exists():
        with gzip.open(BLOB_SET_FILE, "rt", encoding="ascii") as f:
            scanned = {ln.strip() for ln in f if ln.strip()}
    state["scanned"] = scanned
    return state


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    scanned = state["scanned"]
    body = state.copy()
    del body["scanned"]
    body["blob_set_count"] = len(scanned)
    STATE_FILE.write_text(json.dumps(body, indent=2) + "\n")
    with gzip.open(BLOB_SET_FILE, "wt", encoding="ascii") as f:
        f.write("\n".join(sorted(scanned)))


def compute_delta(reachable: set[str], scanned: set[str]) -> set[str]:
    """Blobs to scan = currently reachable minus already scanned (immutable)."""
    return reachable - scanned


def _collect_reachable() -> tuple[dict[str, str], set[str]]:
    """Pass 1: (sha -> path hint) for every unique object reachable from --all."""
    hints: dict[str, str] = {}
    p = subprocess.Popen(  # noqa: S603  # list-form git call, constant args, shell=False
        ["git", "-C", str(PROJECT_ROOT), "rev-list", "--all", "--objects"],  # noqa: S607  # PATH-resolved git by design
        stdout=subprocess.PIPE,
        bufsize=1 << 20,
    )
    assert p.stdout is not None  # noqa: S101  # ty narrowing; stdout=PIPE always yields a stream
    for raw in p.stdout:
        parts = raw.rstrip(b"\n").split(b" ", 1)
        if len(parts) == 2 and len(parts[0]) == 40:
            hints.setdefault(parts[0].decode(), parts[1].decode("utf-8", "replace"))
    p.wait()
    return hints, set(hints)


def _select_blobs(hints: dict[str, str]) -> list[str]:
    """Pass 2: blob SHAs <= MAX_SIZE. Feeder thread avoids pipe deadlock."""
    bc = subprocess.Popen(  # noqa: S603  # list-form git call, constant args, shell=False
        [  # noqa: S607  # PATH-resolved git by design
            "git",
            "-C",
            str(PROJECT_ROOT),
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        bufsize=1 << 20,
    )

    def _feed(proc: subprocess.Popen[bytes], shas: list[str]) -> None:
        assert proc.stdin is not None  # noqa: S101  # ty narrowing; stdin=PIPE always yields a stream
        with proc.stdin:
            for s in shas:
                proc.stdin.write((s + "\n").encode())

    threading.Thread(target=_feed, args=(bc, list(hints)), daemon=True).start()
    out: list[str] = []
    assert bc.stdout is not None  # noqa: S101  # ty narrowing; stdout=PIPE always yields a stream
    for raw in bc.stdout:
        sha, typ, size = raw.decode().split()
        if typ == "blob" and int(size) <= MAX_SIZE:
            out.append(sha)
    bc.wait()
    return out


def _redact(fragment: bytes) -> str:
    return re.sub(rb"[A-Za-z0-9+/_\-]{16,}", b"<redacted>", fragment).decode("utf-8", "replace")


def scan_blobs(
    to_scan: list[str], hints: dict[str, str]
) -> tuple[list[tuple[str, str, str, str]], int]:
    """Pass 3: stream contents, skip binary, run patterns.

    Returns (hits, binary_skipped). Each hit: (pattern_id, sha12, path, ctx).
    """
    cf = subprocess.Popen(  # noqa: S603  # list-form git call, constant args, shell=False
        ["git", "-C", str(PROJECT_ROOT), "cat-file", "--batch"],  # noqa: S607  # PATH-resolved git by design
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        bufsize=1 << 22,
    )
    hits: list[tuple[str, str, str, str]] = []
    binary = 0
    total = len(to_scan)

    def _feed(proc: subprocess.Popen[bytes], shas: list[str]) -> None:
        assert proc.stdin is not None  # noqa: S101  # ty narrowing; stdin=PIPE always yields a stream
        with proc.stdin:
            for s in shas:
                proc.stdin.write((s + "\n").encode())

    threading.Thread(target=_feed, args=(cf, list(to_scan)), daemon=True).start()
    assert cf.stdout is not None  # noqa: S101  # ty narrowing; stdout=PIPE always yields a stream
    scanned = 0
    for sha in to_scan:
        header = cf.stdout.readline().decode()
        _name, _typ, size_s = header.split()
        content = cf.stdout.read(int(size_s))
        cf.stdout.read(1)  # trailing newline separator
        scanned += 1
        if b"\x00" in content[:8192]:
            binary += 1
            continue
        for pid, pat in PATTERNS:
            for m in pat.finditer(content):
                ls = content.rfind(b"\n", 0, m.start()) + 1
                le = content.find(b"\n", m.end())
                ctx = content[ls : le if le != -1 else len(content)][:120]
                hits.append((pid, sha[:12], hints.get(sha, "?"), _redact(ctx)))
        if scanned % 1000 == 0 or scanned == total:
            print(
                f"  progress {scanned}/{total} ({100 * scanned / total:.0f}%) hits={len(hits)}",
                file=sys.stderr,
                flush=True,
            )
    cf.wait()
    return hits, binary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--full",
        action="store_true",
        help="rescan every reachable blob (default: incremental from state)",
    )
    ap.add_argument("--out", default=None, help="also write hits to this file")
    args = ap.parse_args(argv)

    t0 = time.time()
    state = load_state()
    prev = set() if args.full or not state["scanned"] else state["scanned"]

    print("[1/3] enumerating reachable objects...", file=sys.stderr, flush=True)
    hints, _reach = _collect_reachable()

    print(
        f"[2/3] selecting blobs <=1MB from {len(hints)} unique objects...",
        file=sys.stderr,
        flush=True,
    )
    reachable = set(_select_blobs(hints))
    delta = compute_delta(reachable, prev)
    print(
        f"      reachable={len(reachable)} previously_scanned={len(prev)} new={len(delta)}",
        file=sys.stderr,
        flush=True,
    )
    if not delta:
        print(
            f"DONE cur_process_cnt/total_cnt = 0/0 (nothing new; last scan "
            f"{state.get('last_scan_utc', '?')})",
            file=sys.stderr,
        )
        return 0

    print(f"[3/3] scanning {len(delta)} blobs...", file=sys.stderr, flush=True)
    hits, binary = scan_blobs(sorted(delta), hints)

    scanned_all = prev | delta
    new_state = {
        "version": 1,
        "last_scan_utc": _now_utc(),
        "last_mode": "full" if not prev else "incremental",
        "unique_objects": len(hints),
        "blobs_scanned_total": len(scanned_all),
        "binary_skipped_last": binary,
        "hits_this_run": len(hits),
        "scanned": scanned_all,
    }
    save_state(new_state)

    lines = [f"scanned {len(delta)} new blobs ({binary} binary skipped); hits: {len(hits)}", ""]
    for pid, sha12, path, ctx in hits:
        lines += [f"{pid:15s} {sha12}  {path}", f"    {ctx}", ""]
    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report)
    print(
        f"DONE cur_process_cnt/total_cnt = {len(delta)}/{len(delta)} "
        f"hits={len(hits)} elapsed={time.time() - t0:.0f}s "
        f"(total scanned to date: {len(scanned_all)})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
