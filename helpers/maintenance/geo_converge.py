#!/usr/bin/env python3
"""Converge company-note geography to the ticker-derived country vocabulary.

Country layer C2 (doc/improvements/proposals/country_layer_institution_lanes.md).
Three geography carriers disagreed (measured 2026-09-09: 971 india tag rows
vs 95 geography: keys vs 850 India-exchange tickers); C1 made
entities.ticker the authority via listed_in edges, and this editor
converges the note layer onto the same answer:

  * ticker-backed company note -> geography: <country> key (added or
    fixed) + exactly one geography/<country> tag (extra/slop tags
    deduped onto it). Ticker wins over whatever the note said — that is
    the convergence.
  * worklisted company (no ticker) -> slop-normalize only: regional
    qualifiers fold into india, vague scope values (global /
    international / south_asia) are dropped from tags AND from the
    geography key (home market unknown = key absent, human decides via
    findata/Misc/country_worklist.json).

Sector/super-sector notes are NOT touched: their geography/* tags
describe coverage and legitimately include global (31 india / 15 global
rows measured; zero slop values).

Line surgery, never a YAML round-trip — notes are writer-owned with
per-writer formatting, and a re-render would churn every line. Only the
geography lines move; last_modified bumps only when something changed
(house rule: YAML touch = last_modified bump only).

Usage:
    python3 helpers/maintenance/geo_converge.py            # dry-run: summary + samples
    python3 helpers/maintenance/geo_converge.py --apply    # rewrite changed notes
    python3 helpers/maintenance/geo_converge.py --verbose  # every changed note
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.countries import (  # noqa: E402
    DROPPED_GEOGRAPHY_VALUES,
    REGIONAL_TO_INDIA,
    classify_ticker,
)
from helpers.core.db import connect  # noqa: E402
from helpers.core.frontmatter import split_frontmatter  # noqa: E402

COMPANIES_DIR = _REPO_ROOT / "findata" / "Companies"

_GEO_KEY_RE = re.compile(r"^geography:(?:\s+(\S+))?\s*$")
_GEO_TAG_RE = re.compile(r"^- geography/([a-z_]+)\s*$")
_TAGS_HDR_RE = re.compile(r"^tags:\s*$")
_ANY_TAG_RE = re.compile(r"^- \S+$")
_LAST_MOD_RE = re.compile(r"^last_modified:.*$")


def plan_note(text: str, country: str | None, today: str):
    """Compute the converged frontmatter for one note.

    Returns ``(new_text, changes)`` where changes is a list of short
    descriptors (key_add/key_fix/key_drop/tag_converge/regional_to_india/
    slop_drop), or ``(text, [])`` when nothing changes. Pure function —
    no filesystem access.
    """
    dashes, yaml_body, rest = split_frontmatter(text)
    if not dashes:
        return text, []
    lines = yaml_body.split("\n")
    changes: list[str] = []

    key_idx = next((i for i, ln in enumerate(lines) if _GEO_KEY_RE.match(ln)), None)
    key_val = _GEO_KEY_RE.match(lines[key_idx]).group(1) if key_idx is not None else None
    tag_idxs = [i for i, ln in enumerate(lines) if _GEO_TAG_RE.match(ln)]
    tag_vals = [_GEO_TAG_RE.match(lines[i]).group(1) for i in tag_idxs]

    # ---- tags -----------------------------------------------------------
    if country is not None:
        want = f"- geography/{country}"
        if not tag_vals:
            # Insert into the tags block: after the last existing tag line
            # (keeps the block contiguous); if there is no tags block at
            # all, leave tags alone (the key still converges).
            tag_block = [i for i, ln in enumerate(lines) if _ANY_TAG_RE.match(ln)]
            if tag_block:
                lines.insert(tag_block[-1] + 1, want)
                changes.append("tag_converge")
        elif tag_vals != [country]:
            lines[tag_idxs[0]] = want
            for i in reversed(tag_idxs[1:]):
                del lines[i]
            changes.append("tag_converge")
    else:
        regional = [v for v in tag_vals if v in REGIONAL_TO_INDIA]
        dropped = [v for v in tag_vals if v in DROPPED_GEOGRAPHY_VALUES]
        if regional:
            if any(
                v not in REGIONAL_TO_INDIA and v not in DROPPED_GEOGRAPHY_VALUES for v in tag_vals
            ):
                # A real country tag already exists (e.g. india alongside
                # domestic_focused) — just delete the slop lines.
                for i in reversed(tag_idxs):
                    if _GEO_TAG_RE.match(lines[i]).group(1) in REGIONAL_TO_INDIA:
                        del lines[i]
                changes.append("slop_drop")
            else:
                lines[tag_idxs[0]] = "- geography/india"
                for i in reversed(tag_idxs[1:]):
                    del lines[i]
                changes.append("regional_to_india")
        elif dropped:
            for i in reversed(tag_idxs):
                del lines[i]
            changes.append("slop_drop")

    # ---- geography key ----------------------------------------------------
    if country is not None:
        want_key = f"geography: {country}"
        if key_idx is None:
            tags_hdr = next((i for i, ln in enumerate(lines) if _TAGS_HDR_RE.match(ln)), None)
            if tags_hdr is not None:
                lines.insert(tags_hdr, want_key)
                changes.append("key_add")
        elif lines[key_idx] != want_key:
            lines[key_idx] = want_key
            changes.append("key_fix")
    elif key_val in DROPPED_GEOGRAPHY_VALUES:
        # No ticker-derived home market: a vague scope key is noise — drop
        # it. A country-valued key is human evidence and stays.
        del lines[key_idx]
        changes.append("key_drop")

    if not changes:
        return text, []

    # ---- last_modified bump (only when something changed) ----------------
    for i, ln in enumerate(lines):
        if _LAST_MOD_RE.match(ln):
            lines[i] = f"last_modified: '{today}'"
            break

    return dashes + "\n".join(lines) + rest, changes


def _iter_company_tickers(conn) -> dict[str, tuple[str | None, str | None]]:
    """relpath -> (entity_name, ticker) for every company with a file_path."""
    out: dict[str, tuple[str | None, str | None]] = {}
    for name, file_path, ticker in conn.execute(
        "SELECT name, file_path, ticker FROM entities "
        "WHERE entity_type = 'company' AND file_path IS NOT NULL"
    ).fetchall():
        out[file_path] = (name, ticker)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Converge company-note geography to the ticker-derived vocabulary.",
    )
    ap.add_argument(
        "--apply", action="store_true", help="Rewrite changed notes (default: dry-run)."
    )
    ap.add_argument(
        "--verbose", action="store_true", help="Print every changed note, not just samples."
    )
    args = ap.parse_args(argv)

    from datetime import date

    today = date.today().isoformat()

    conn = connect()
    try:
        by_path = _iter_company_tickers(conn)
    finally:
        conn.close()

    notes = sorted(COMPANIES_DIR.rglob("*.md"))
    changed: list[tuple[Path, str | None, list[str]]] = []
    skipped = 0
    for note in notes:
        rel = note.resolve().relative_to(_REPO_ROOT).as_posix()
        entry = by_path.get(rel)
        if entry is None:
            skipped += 1
            continue
        _name, ticker = entry
        country, _via = classify_ticker(ticker)
        text = note.read_text(encoding="utf-8")
        new_text, changes = plan_note(text, country, today)
        if changes:
            changed.append((note, country, changes))
            if args.apply:
                note.write_text(new_text, encoding="utf-8")

    kinds = Counter(c for _, _, cs in changed for c in cs)
    mode = "apply" if args.apply else "dry-run"
    print(
        f"notes={len(notes)} skipped_no_entity={skipped} changed={len(changed)} ({mode})",
        file=sys.stderr,
    )
    for kind, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {kind}", file=sys.stderr)

    shown = changed if args.verbose else changed[:12]
    for note, country, cs in shown:
        print(f"  {note.relative_to(_REPO_ROOT)} -> {country or '(worklist)'}: {', '.join(cs)}")
    if not args.verbose and len(changed) > 12:
        print(f"  ... ({len(changed) - 12} more; --verbose for all)")
    if not args.apply:
        print(
            "dry-run: no files written; pass --apply at the operator checkpoint.", file=sys.stderr
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
