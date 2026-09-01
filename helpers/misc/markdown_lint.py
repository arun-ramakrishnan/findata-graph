#!/usr/bin/env python3
"""Markdown lint body — runs version-pinned markdownlint-cli2 with the
repo-root ``.markdownlint-cli2.jsonc`` (globs live in the file: doc/ prose
base + findata Tier-1 defect override). The advisory step ``md-lint`` calls
this; without node/npx on PATH it prints SKIP and exits 0 (same convention
as frontend-check's Node gating), so node-less boxes keep the advisory green.

LINT-ONLY by design — there is deliberately no ``--fix`` here: the fixer
rewrites whitespace wholesale and is forbidden over findata (proposal §5,
the 2026-08-19 marker-collision incident). S2's doc/ ``--fix`` sweep is a
one-off direct npx invocation, not wired into this helper.

Exit codes: 0 clean or SKIP · 1 findings present (the expected red until
S2/S4 land) · child's rc for execution failures (config/npx/download).

Usage::

    python3 helpers/misc/markdown_lint.py            # digest (advisory)
    python3 helpers/misc/markdown_lint.py --full     # raw cli2 output (S2 hand-fixes)
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Pinned (proposal §4.2); bump = a measured decision, never a silent float.
MARKDOWNLINT_CLI2_VERSION = "0.23.2"
_NPX = shutil.which("npx")
# cli2 default formatter: ``path/file.md:12:5 error MD037/Name message``
# (the column is optional and the severity word is always present)
_VIOLATION = re.compile(r"^.+?:\d+(?::\d+)?\s+(?:error|warning)\s+(MD\d+)(?:/\S+)?\s")
_SAMPLE_LINES = 15  # raw violation lines shown under the digest when red


def _digest(output: list[str]) -> tuple[int, int, Counter[str]]:
    """Return (violations, files, per-rule counts) from cli2 output."""
    rules: Counter[str] = Counter()
    files: set[str] = set()
    for line in output:
        m = _VIOLATION.match(line)
        if m:
            rules[m.group(1)] += 1
            files.add(line.split(":", 1)[0])
    return sum(rules.values()), len(files), rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--full",
        action="store_true",
        help="stream raw markdownlint-cli2 output instead of the digest",
    )
    args = parser.parse_args(argv)

    print(
        f"markdown lint (markdownlint-cli2 {MARKDOWNLINT_CLI2_VERSION}; config: .markdownlint-cli2.jsonc)"
    )
    if _NPX is None or shutil.which("node") is None:
        print("SKIP: node/npx not on PATH — markdown lint not run (needs Node >= 22)")
        return 0

    cmd = [_NPX, "-y", f"markdownlint-cli2@{MARKDOWNLINT_CLI2_VERSION}"]
    if args.full:
        return subprocess.run(cmd, cwd=REPO_ROOT).returncode  # noqa: S603  # list-form call; shell=False (default); args are version-pinned constants

    proc = subprocess.run(  # noqa: S603  # same as above; digest path captures both streams
        cmd,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    output = proc.stdout.splitlines() if proc.stdout else []
    violations, files, rules = _digest(output)
    if not violations and proc.returncode not in (0, 1):
        # Not a lint verdict — npx/config/download failure: surface the raw tail.
        print("\n".join(output[-30:]))
        print(f"FAILED: markdownlint-cli2 exited {proc.returncode} (no violation lines parsed)")
        return proc.returncode

    print(f"{violations} violation(s) in {files} file(s)")
    for rule, count in rules.most_common():
        print(f"  {rule}  {count}")
    if violations:
        print(f"sample (last {_SAMPLE_LINES}):")
        tail = [ln for ln in output if _VIOLATION.match(ln)][-_SAMPLE_LINES:]
        print("\n".join(f"  {ln}" for ln in tail))
        print("exit 1 (findings present)")
    else:
        print("clean")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
