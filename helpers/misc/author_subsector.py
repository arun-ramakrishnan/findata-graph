#!/usr/bin/env python3
"""Author `subsector:` frontmatter on company notes — the S2 writer of the
subsector_authoring_pass proposal.

Surgical single-line insert/replace inside the YAML frontmatter (the
`enrich_from_yfinance._update_frontmatter` discipline): the note body is
never touched, an existing `subsector:` line is replaced in place, and a
missing one is inserted directly after the `industry:` line (or at the top
of the frontmatter when the note carries no industry). Values are
case/spacing-insensitive downstream (`extract_subsector_membership`), but
write them in the canonical roster form.

    python3 helpers/misc/author_subsector.py --map MAP.json              # dry-run
    python3 helpers/misc/author_subsector.py --map MAP.json --apply      # write

MAP.json: {"<note stem>": "<sub_sector value>", ...} — stems are the
company note filenames (underscores), matched against
findata/Companies/**. Unknown stems are reported and skipped; notes
already carrying the target value are reported as no-ops.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from helpers.core.frontmatter import split_frontmatter  # noqa: E402


def author_note(path: Path, value: str) -> str:
    """Return 'replaced' | 'inserted' | 'noop' | 'no-frontmatter' for one note."""
    text = path.read_text(encoding="utf-8")
    dashes, yaml_body, rest = split_frontmatter(text)
    if not yaml_body:
        return "no-frontmatter"
    lines = yaml_body.splitlines()
    line = f"subsector: {value}"

    def _reassemble() -> None:
        body = "\n".join(lines)
        if not body.endswith("\n"):
            body += "\n"
        path.write_text(dashes + body + rest, encoding="utf-8")

    for i, ln in enumerate(lines):
        if ln.startswith("subsector:"):
            if ln == line:
                return "noop"
            lines[i] = line
            _reassemble()
            return "replaced"
    for i, ln in enumerate(lines):
        if ln.startswith("industry:"):
            lines.insert(i + 1, line)
            _reassemble()
            return "inserted"
    lines.insert(0, line)
    _reassemble()
    return "inserted"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", type=Path, required=True, help="JSON {note stem: subsector value}")
    ap.add_argument("--root", type=Path, default=REPO / "findata" / "Companies")
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run report)")
    a = ap.parse_args(argv)

    mapping: dict[str, str] = json.loads(a.map.read_text(encoding="utf-8"))
    notes: dict[str, Path] = {p.stem: p for p in sorted(a.root.rglob("*.md"))}

    counts: dict[str, int] = {
        "replaced": 0,
        "inserted": 0,
        "noop": 0,
        "no-frontmatter": 0,
        "missing": 0,
        "dry-run": 0,
    }
    for stem, value in mapping.items():
        path = notes.get(stem)
        if path is None:
            counts["missing"] += 1
            print(f"  MISSING note for stem {stem!r}")
            continue
        if not a.apply:
            counts["dry-run"] += 1
            print(f"  would author: {path.relative_to(REPO)} -> {value}")
            continue
        action = author_note(path, value)
        counts[action] += 1
        if action != "noop":
            print(f"  {action}: {path.relative_to(REPO)} -> {value}")
    mode = "APPLY" if a.apply else "DRY-RUN"
    print(f"{mode}: {json.dumps(counts, sort_keys=True)}")
    if counts["missing"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
