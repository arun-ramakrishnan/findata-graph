#!/usr/bin/env python3
"""Stamp a human ``verified[]`` entry onto notes (OKF v0.2 §5.2/§5.3).

The trust vocabulary is plumbed end to end — the ``--okf`` census counts the
human-reviewed tier, ``bump_generated`` preserves ``verified`` on every
re-render, the schemas validate it — but nothing writes it except hand-YAML
surgery. This is that zero-friction writer:

    python3 helpers/misc/okf_verify.py findata/Companies/FMCG/Marico.md
    python3 -m helpers.misc.okf_verify <note>... --by human:user

Semantics (okf_readside N3):

- appends ``verified: [{by: <actor>, at: <now UTC>}]`` via the safe YAML
  round-trip (parse → append → render), preserving ``generated`` and every
  other key — mirroring bump_generated's preservation guarantee;
- idempotent per actor: a note already carrying an entry with the same
  ``by`` is reported and skipped (no write, no duplicate);
- ``--by`` must be ``human:<id>`` (strictly human by OKF design, adoption
  Q2 — machine confirmation is ``generated``, not ``verified``);
- dry-run default: ``--apply`` performs the write (note-content footprint
  rule: the operator stamps, never a background pass).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.edition_index import _body  # noqa: E402
from helpers.core.frontmatter import (  # noqa: E402
    iso_now_utc,
    render_frontmatter,
    split_frontmatter,
    stringify_dates,
)
import yaml  # noqa: E402


def verify_note(path: Path, by: str, *, apply: bool = False) -> str:
    """Stamp one note; returns a status string ('stamped'|'skipped'|...)."""
    if not path.exists():
        return f"missing: {path}"
    text = path.read_text(encoding="utf-8", errors="replace")
    opener, fm_text, _ = split_frontmatter(text)
    if not opener:
        return f"no frontmatter: {path}"
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return f"unparseable frontmatter: {path}"
    if not isinstance(fm, dict):
        return f"unparseable frontmatter: {path}"
    verified = fm.get("verified")
    verified = list(verified) if isinstance(verified, list) else []
    if any(isinstance(v, dict) and v.get("by") == by for v in verified):
        return f"already verified by {by}: {path}"
    verified.append({"by": by, "at": iso_now_utc()})
    fm["verified"] = verified
    # _body strips the closing dashes + exactly one newline (the documented
    # round-trip rule — split_frontmatter's third element still carries them).
    new_text = render_frontmatter(stringify_dates(fm)) + _body(text)
    if apply:
        path.write_text(new_text, encoding="utf-8")
        return f"stamped: {path}"
    return f"would stamp: {path}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Stamp a human verified[] entry onto note frontmatter "
                    "(OKF §5.2/§5.3; the human-reviewed census tier).",
    )
    p.add_argument("notes", nargs="+", type=Path,
                   help="Note path(s) to verify.")
    p.add_argument("--by", default="human:user",
                   help="Human actor (must be human:<id>; default: human:user).")
    p.add_argument("--apply", action="store_true",
                   help="Write (default: dry-run report).")
    args = p.parse_args(argv)
    if not args.by.startswith("human:") or len(args.by) <= len("human:"):
        p.error("--by must be human:<id> (strictly human; adoption Q2)")
    rc = 0
    for note in args.notes:
        status = verify_note(note, args.by, apply=args.apply)
        print(status)
        rc |= 1 if status.startswith(("missing", "no frontmatter",
                                       "unparseable")) else 0
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
