#!/usr/bin/env python3
"""Pin the OCR binary this repo's review loop was verified against.

`ocr` is a global bun install (`@alibaba-group/open-code-review`) with no
version in the repo, so the loop silently changes behaviour whenever the
operator upgrades it — and the package ships no changelog, which means a
delta is only observable through the CLI surface (flags, JSON shape,
delegate-mode format support). The pin makes that drift visible at the
point of use instead of after a review whose roster was produced by a
different tool than the one the procedure documents.

`PINNED` is the version the loop's assumptions were checked against:

- `ocr delegate preview --format json` emits `reviewable_files` /
  `excluded_files` / `reviewable_count` / `total_files` /
  `total_insertions` / `total_deletions` (helpers/misc/review_selection.py);
- delegate mode supports `text|json` only — no sarif;
- `.opencodereview/rule.json`'s `rules` key is inert, so the house
  checklist is host-carried (review_selection.HOUSE_CHECKLIST);
- `--rule <path>` is the real rules surface, not rule.json.

Bumping the pin is a deliberate act: run `make ocr-version-check` after
upgrading, re-read `ocr delegate preview --help`, and update this
docstring plus doc/procedures/ocr_review.md §Invocation in the same patch.
"""

from __future__ import annotations

import re
import shutil
import subprocess

PINNED = "1.13.3"

# `open-code-review v1.13.3 (375f64c) linux/amd64, built 2026-10-22T04:27:25Z`
_VERSION_RE = re.compile(r"\bv(\d+\.\d+\.\d+)\b")


def parse_version(text: str) -> str | None:
    """Return the semver from `ocr --version` output, or None.

    Tolerates the `open-code-review ` prefix, the build hash and the
    build timestamp; a bare `1.13.3` also parses.
    """
    match = _VERSION_RE.search(text or "")
    return match.group(1) if match else None


def probe(ocr: str = "ocr", *, timeout: float = 15.0) -> tuple[str, str]:
    """Run `<ocr> --version`. Returns `(version_or_empty, raw_first_line)`."""
    binary = shutil.which(ocr)
    if binary is None:
        return (
            "",
            f"{ocr}: not on PATH (bun global install; see doc/procedures/ocr_review.md)",
        )
    try:
        proc = subprocess.run(  # noqa: S603  # resolved absolute path, fixed argv, no shell
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (
        OSError,
        subprocess.SubprocessError,
    ) as exc:  # pragma: no cover - env dependent
        return "", f"{ocr}: unavailable ({type(exc).__name__}: {exc})"
    raw = (proc.stdout or proc.stderr or "").strip()
    return (parse_version(raw) or ""), (raw.splitlines()[0] if raw else "no version output")


def status(ocr: str = "ocr") -> tuple[bool, str]:
    """Return `(matches_pin, human line)` for the roster header."""
    version, raw = probe(ocr)
    if not version:
        return False, f"OCR: version unknown — {raw} (pin {PINNED})"
    if version != PINNED:
        return (
            False,
            f"OCR: v{version} installed, v{PINNED} pinned — re-read the CLI surface before trusting this roster",
        )
    return True, f"OCR: v{version} (pinned)"


def main() -> int:
    ok, line = status()
    print(line)
    if not ok:
        print(f"pin it: bun add -g @alibaba-group/open-code-review@{PINNED}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
