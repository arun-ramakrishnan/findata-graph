#!/usr/bin/env python3
"""Sync an auto-generated company index into every sector note.

For each of the 42 sector notes in ``findata/Sectors/``, this inserts (or
refreshes) a ``## All Companies (auto)`` section listing every company in
that sector as a ``[[Display Name]]`` wikilink. The section is generated
from the SQLite source of truth (``entities`` joined to each sector via
``sector_classification``), so it is always complete and never contains
phantom links — a company appears iff its entity row exists.

DESIGN DECISIONS (Bundle H3, 2026-07-28)
----------------------------------------
- **Coexistence with curated sections.** Sector notes often have a hand-
  written ``## Major Companies`` section with editorial sub-groupings
  (Banking splits into Public/Private/Small Finance/Cooperative/Foreign).
  Those are PRESERVED untouched. The auto section is ADDITIONAL — it sits
  right before ``## Newsletter synthesis`` (the universal last heading)
  and provides the complete DB-backed index. The curated section stays as
  the editorial highlight; the auto section is the exhaustive roster.

- **Display-name wikilink form.** Links use ``[[<filename_stem>|<title>]]``
  (or just ``[[<filename_stem>]]`` when the two are identical). Obsidian
  resolves the link TARGET (the part before ``|``) against the note's
  filename basename — NOT its ``title:`` YAML field — so the stem is what
  guarantees 100% resolution with zero phantoms. The ``title`` is used only
  as the human-readable display text (the part after ``|``). Using the raw
  ``title`` as the target (a previous design) produced 36 phantom links
  where ``title`` diverges from the stem in a way Obsidian can't fold
  (e.g. ``[[3M India]]`` → file ``Three_M_India.md``, ``[[HAL]]`` →
  ``Hindustan_Aeronautics.md``).

- **Idempotent.** Re-running replaces any existing auto section (matched
  by its sentinel markers) with a fresh one. Safe to run after any company
  add/rename/sector-move. The ``--check`` flag reports drift without
  writing (returns nonzero if any sector is stale — suitable for a CI gate).

- **Two-column layout.** Companies are rendered as a bulleted list, sorted
  alphabetically, ~3 per line via a markdown table-free compact list to
  keep the section scannable for sectors with 80+ companies.

USAGE
-----
    python3 helpers/maintenance/sync_sector_wikilinks.py            # write
    python3 helpers/maintenance/sync_sector_wikilinks.py --check    # dry-run gate
    python3 helpers/maintenance/sync_sector_wikilinks.py --sector Banking
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402

SECTORS_DIR = PROJECT_ROOT / "findata" / "Sectors"
DB_PATH = PROJECT_ROOT / "memory" / "research.db"

# Sentinel markers bracket the auto section so we can find + replace it
# idempotently without touching curated content.
_BEGIN = "<!-- BEGIN auto company index (sync_sector_wikilinks.py) -->"
_END = "<!-- END auto company index -->"
_SECTION_HEADING = "## All Companies (auto)"


def _company_title(file_path: str) -> str:
    """Read the ``title:`` field from a company note (display text only).

    Falls back to the filename stem if the note has no title (shouldn't
    happen for production notes, but keeps the script robust). NOTE: the
    title is used only as the link's DISPLAY text (after the ``|`` in
    ``[[stem|title]]``); the link TARGET is the filename stem, which is
    what Obsidian resolves against. See module docstring.
    """
    p = PROJECT_ROOT / file_path
    if not p.exists():
        return Path(file_path).stem.replace("_", " ")
    txt = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', txt, re.MULTILINE)
    if m:
        return m.group(1).strip().strip("\"'")
    return Path(file_path).stem.replace("_", " ")


def _companies_for_sector(conn, sector_classification: str) -> list[tuple[str, str, str]]:
    """Return ``(name, stem, title)`` for every company in the sector, sorted by name.

    ``stem`` is the filename basename (Obsidian's link TARGET); ``title`` is
    the YAML display text (used after the ``|`` in the wikilink).
    """
    rows = conn.execute(
        "SELECT name, file_path FROM entities "
        "WHERE entity_type = 'company' AND sector_classification = ? "
        "ORDER BY name",
        (sector_classification,),
    ).fetchall()
    return [(r["name"], Path(r["file_path"]).stem, _company_title(r["file_path"])) for r in rows]


def _render_section(companies: list[tuple[str, str, str]], sector_name: str) -> str:
    """Render the auto section markdown for a list of companies."""
    lines = [
        _BEGIN,
        "",
        _SECTION_HEADING,
        "",
        f"<!-- Auto-generated from the SQLite source of truth. {len(companies)} "
        f"company note(s) in {sector_name}. Do not edit by hand — re-run "
        f"`python3 helpers/maintenance/sync_sector_wikilinks.py` to refresh. "
        f"Curated highlights live in the editorial sections above. -->",
        "",
    ]
    if not companies:
        lines.append("_(No companies tracked in this sector yet.)_")
    else:
        for _name, stem, title in companies:
            # [[stem|title]] — stem (filename basename) is the Obsidian link
            # TARGET (guaranteed to resolve); title is display text. When the
            # two are identical, emit the plain [[stem]] form for readability.
            if stem == title:
                lines.append(f"- [[{stem}]]")
            else:
                lines.append(f"- [[{stem}|{title}]]")
    lines.extend(["", _END, ""])
    return "\n".join(lines)


def _find_insertion_point(text: str) -> int:
    """Return the character offset where the auto section should go.

    The section inserts immediately BEFORE the ``## Newsletter synthesis``
    heading (the universal last section in every sector note). If that
    heading is somehow absent, fall back to the end of the file.
    """
    m = re.search(r"^## Newsletter synthesis", text, re.MULTILINE)
    if m:
        return m.start()
    return len(text)


def _replace_or_insert(text: str, new_section: str) -> tuple[str, bool]:
    """Replace the existing auto section (between sentinels) or insert new.

    Returns ``(new_text, changed)``. ``changed`` is True iff the resulting
    text differs from the input.
    """
    pattern = re.compile(
        re.escape(_BEGIN) + r".*?" + re.escape(_END) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(text):
        replaced = pattern.sub(new_section, text)
        return replaced, replaced != text
    # No existing section — insert before the Newsletter synthesis heading.
    idx = _find_insertion_point(text)
    # Ensure a blank line separates the new section from preceding content.
    prefix = text[:idx]
    if prefix and not prefix.endswith("\n\n"):
        if prefix.endswith("\n"):
            prefix += "\n"
        else:
            prefix += "\n\n"
    new_text = prefix + new_section + text[idx:]
    return new_text, True


def sync_sector(
    conn, sector_path: Path, sector_classification: str, *, dry_run: bool
) -> tuple[bool, int]:
    """Sync one sector file. Returns ``(changed, company_count)``."""
    sector_name = sector_path.stem
    companies = _companies_for_sector(conn, sector_classification)
    new_section = _render_section(companies, sector_name)

    text = sector_path.read_text(encoding="utf-8")
    new_text, changed = _replace_or_insert(text, new_section)

    if changed and not dry_run:
        sector_path.write_text(new_text, encoding="utf-8")
    return changed, len(companies)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync an auto-generated company index into every sector note."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the updated company index into sector notes (default: dry-run report)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without writing. Exits nonzero if any sector is stale.",
    )
    parser.add_argument(
        "--sector",
        default=None,
        help="Sync only this sector (by filename stem, e.g. Banking).",
    )
    args = parser.parse_args(argv)

    if not SECTORS_DIR.exists():
        print(f"ERROR: sectors dir not found: {SECTORS_DIR}", file=sys.stderr)
        return 1

    conn = connect(DB_PATH)
    stale = 0
    total_companies = 0
    sectors_processed = 0
    try:
        targets = sorted(SECTORS_DIR.glob("*.md"))
        if args.sector:
            targets = [p for p in targets if p.stem == args.sector]
            if not targets:
                print(f"ERROR: no sector file matches --sector {args.sector!r}", file=sys.stderr)
                return 1

        for sp in targets:
            # The sector_classification stored on company rows is the
            # PascalCase sector name, which equals the sector file's stem
            # (verified: 0 mismatches in the corpus audit).
            # Default is a dry-run report; --apply writes. --check keeps its
            # advisory-gate contract (dry-run + rc 1 on stale) for maint.
            dry_run = not args.apply
            changed, n = sync_sector(conn, sp, sp.stem, dry_run=dry_run)
            total_companies += n
            sectors_processed += 1
            marker = (
                "STALE"
                if (changed and args.check)
                else ("would update (dry-run)" if changed else "ok")
            )
            if changed and args.check:
                stale += 1
            print(f"  {sp.stem:<32} {n:>3} companies  [{marker}]")
    finally:
        conn.close()

    print(f"\n{sectors_processed} sector(s) processed, {total_companies} company links total.")
    if not args.apply:
        print("DRY-RUN: no sector notes written. Pass --apply to write.")
    if args.check:
        if stale:
            print(f"{stale} sector(s) are stale. Re-run with --apply to update.")
            return 1
        print("All sectors up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
