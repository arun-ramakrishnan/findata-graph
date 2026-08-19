#!/usr/bin/env python3
"""Build the 3-level sector hierarchy: super-sector -> sector -> sub-category.

The graph historically modelled sectors as a flat peer set (42 ``sector``
entities, each linked to its companies via ``part_of``/``has_company``).
There was no sector-to-sector structure: ``/api/sectors`` returned a flat
list, and the DuckDB graph could not answer "show me all financial stocks"
or "what sub-categories exist within Metals?".

This script introduces two new entity types and a dedicated edge type:

  - ``super_sector``  — 9 GICS-style top-level groups (Financials,
    Healthcare, Industrials, Materials, Energy, Consumer Discretionary,
    Consumer Staples, Information Technology, Communication Services).
  - ``sub_sector``    — the intra-sector facets authored in 5 sector notes
    (Metals -> Iron and Steel / Aluminum / Copper / ...; Aviation ->
    Airlines / Airport Operations / ...). The other 37 sectors have no
    authored sub-categories and stay at super-sector -> sector.
  - ``belongs_to``    — the hierarchy edge (sector -> super_sector,
    sub_sector -> sector). A NEW edge type, deliberately NOT overloading
    ``part_of``: ``part_of`` is purely company -> sector, and the DuckDB
    ``EDGE_REGISTRY`` / integrity checker both assume that. Overloading it
    would silently drop the new edges from the property graph and trip the
    ``po_src_bad`` / ``po_tgt_bad`` integrity checks. ``belongs_to`` keeps
    the two relationships cleanly separated.

The taxonomy is CURATED (encoded below as data), not extracted — there is
no reliable super-sector signal anywhere in the corpus (0 YAML fields, 0
controlled vocabularies; only the Banking note mentions "Parent Sector").
Sub-categories are taken verbatim from the 5 sector notes that author
``### Sub-Sectors`` headings.

USAGE
-----
    python3 helpers/maintenance/build_sector_hierarchy.py            # write
    python3 helpers/maintenance/build_sector_hierarchy.py --check    # validate, no write
    python3 helpers/maintenance/build_sector_hierarchy.py --apply    # explicit write

Idempotent: ``INSERT OR IGNORE`` throughout. Re-running after a taxonomy
edit adds/updates only the changed rows. The ``--check`` mode validates
coverage (all 42 live sectors mapped, 0 orphans, 0 collisions) and exits
nonzero on drift — suitable for a CI gate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402

DB_PATH = PROJECT_ROOT / "memory" / "research.db"
# Vault root for note writes. Monkeypatched to a tmp dir by tests so
# build(write=True) doesn't overwrite the real findata/ vault files.
VAULT_ROOT = PROJECT_ROOT / "findata"
SUPER_SECTORS_DIR = VAULT_ROOT / "Super_Sectors"

# --------------------------------------------------------------------------- #
# The curated taxonomy — the single source of truth for the hierarchy.        #
# --------------------------------------------------------------------------- #
# 9 GICS-style super-sectors. Each maps to a list of the 42 existing `sector`
# entities. Coverage is validated at runtime: every live sector must appear in
# exactly one super-sector (no orphans, no collisions).
#
# NAMING: super-sector names must NOT collide with any existing `sector`
# entity name (entities.name is the PK, so INSERT OR IGNORE would silently
# skip a colliding super_sector and leave the existing sector row mis-typed).
# Two super-sectors share their dictionary label with a child sector:
#   - "Healthcare" (super) contains the "Healthcare" sector -> renamed
#     Healthcare_Super.
#   - "Energy" (super) contains the "Energy" sector -> renamed Energy_Super.
# The `_Super` suffix is self-documenting about the level and is applied
# only where a collision exists; the other 7 names stay as approved.
#
# Collision/oddball resolutions (see doc/improvements/findata_corpus_audit.txt M4):
#   - Capital_Markets   -> Financials    (capital-markets/investing, GICS)
#   - Media_Entertainment -> Communication Services (GICS, not Cons. Disc.)
#   - Telecommunications  -> Communication Services (primary classification)
#   - Aviation          -> Consumer Discretionary (travel/leisure)
#   - Education_Training-> Consumer Discretionary (consumer services)
#   - Diversified       -> Industrials (conglomerate/holding convention)
#   - International     -> Industrials (holding-company bucket)
SUPER_SECTORS: dict[str, list[str]] = {
    "Financials": [
        "Banking", "Financial_Services", "NBFC", "Housing_Finance",
        "Insurance", "Capital_Markets", "Fintech_Payments",
    ],
    "Healthcare_Super": ["Pharma", "Healthcare", "Hospitals", "Diagnostics"],
    "Industrials": [
        "Engineering_Capital_Goods", "EMS_Manufacturing", "Infrastructure",
        "Railways", "Logistics", "Defense",
        "Diversified", "International",
    ],
    "Materials": [
        "Chemicals", "Metals", "Mining", "Building_Materials",
        "Packaging", "Fertilizer", "Textiles",
    ],
    "Energy_Super": ["Energy", "Renewables"],
    "Consumer Discretionary": [
        "Automotive", "Retail", "Travel", "Real_Estate", "Consumer",
        "Aviation", "Education_Training",
    ],
    "Consumer Staples": ["FMCG", "Agriculture"],
    "Information Technology": ["Technology", "Semiconductors", "Electronics"],
    "Communication Services": ["Media_Entertainment", "Telecommunications"],
}

# Sub-categories (Level 3): MERGED from two disjoint corpus sources.
#   (a) The `subsector/*` YAML tags on 19 sector notes (the curated
#       controlled vocabulary — Banking: public_sector/private_sector/...,
#       Pharma: api/crams/formulations/...). These are the primary source.
#   (b) The `### Sub-Sectors` prose headings on 5 DIFFERENT sector notes
#       (Metals, Aviation, Education_Training, Logistics, Textiles) which
#       carry no subsector/* tags at all. The two sources are completely
#       disjoint — no sector uses both — so merging yields 24 sectors.
# Tags are humanized (cooperative_banks -> "Cooperative Banks"); industry
# acronyms restored to canonical caps (api -> "API", ivd -> "IVD"). The
# remaining 18 sectors (out of 42) have neither signal and stay at
# super-sector -> sector (no Level 3).
SUB_CATEGORIES: dict[str, list[str]] = {
    "Agriculture": ["Crop Production", "Food Processing", "Livestock"],
    "Automotive": ["Commercial Vehicles", "Electric Vehicles", "Manufacturing", "Two Wheelers"],
    "Aviation": ["Airlines", "Airport Operations", "Aviation Services"],
    "Banking": ["Cooperative Banks", "Foreign Banks", "Private Sector", "Public Sector"],
    "Building_Materials": ["Cement", "Ceramics", "Paints", "Sanitaryware"],
    "Chemicals": ["Agrochemicals", "Petrochemicals", "Specialty Chemicals"],
    "Defense": ["Aerospace", "Military"],
    "Diagnostics": ["Imaging", "IVD", "Pathology"],
    "Diversified": ["Multi Segment"],
    "Education_Training": [
        "Formal Education", "Test Preparation", "EdTech Platforms",
        "Vocational Training", "Corporate Training",
    ],
    "FMCG": ["Food Beverages", "Household Care", "Personal Care"],
    "Hospitals": ["Cancer Care", "Hospital Chains", "Specialty Care"],
    "Insurance": ["General Insurance", "Health Insurance", "Life Insurance"],
    "Logistics": ["Transportation", "Supply Chain Services", "Specialized Logistics"],
    "Media_Entertainment": ["Broadcasting", "Cinema", "Digital"],
    "Metals": [
        "Iron and Steel", "Aluminum", "Copper",
        "Zinc and Lead", "Precious Metals", "Minor Metals",
    ],
    "Mining": ["Coal", "Iron Ore", "Non Ferrous"],
    "Packaging": ["Flexible Packaging", "Rigid Packaging"],
    "Pharma": ["API", "CRAMS", "Formulations", "Pharma Retail", "Vaccines"],
    "Real_Estate": ["Commercial", "Residential"],
    "Renewables": ["Biofuel", "Solar", "Wind"],
    "Semiconductors": ["Design", "Foundry", "Memory"],
    "Textiles": [
        "Traditional Textiles", "Technical Textiles",
        "Apparel Manufacturing", "Textile Machinery",
    ],
    "Travel": ["Hotels", "Leisure", "Resorts"],
}


def _normalize(name: str) -> str:
    """Entity-name normalization matching the existing convention:
    spaces -> underscores, so ``Financials`` -> ``Financials`` and
    ``Consumer Discretionary`` -> ``Consumer_Discretionary``."""
    return name.replace(" ", "_")


def _note_path(kind: str, display_name: str) -> str:
    """Relative note path (from repo root) for a super-sector note."""
    stem = _normalize(display_name)
    return f"findata/Super_Sectors/{stem}.md"


def _super_sector_note(display_name: str, child_sectors: list[str]) -> str:
    """Render a super-sector markdown note.

    Uses the canonical sector-note YAML template (see doc/findata.md) with
    ``type: super_sector`` and a ``## Child Sectors (auto)`` section listing
    the member sectors as wikilinks. The section is bracketed by sentinel
    markers so it can be refreshed idempotently by re-running this script.
    """
    stem = _normalize(display_name)
    permalink = f"/super_sectors/{display_name.lower().replace(' ', '_')}"
    created = date.today().isoformat()
    # Wikilinks to the child SECTOR notes. Sector notes live in
    # findata/Sectors/<Stem>.md; Obsidian resolves [[Stem]] to them.
    links = "\n".join(
        f"- [[{_normalize(s)}]]" for s in sorted(child_sectors)
    )
    return f"""---
title: {display_name}
type: super_sector
normalized_name: {stem}
file_path: {_note_path("super_sector", display_name)}
permalink: {permalink}
tags:
- entity_type/super_sector
- super_sector/{display_name.lower().replace(' ', '_')}
created: '{created}'
last_modified: '{created}'
---

# {display_name}

A GICS-style super-sector grouping the following sectors.

<!-- BEGIN auto child sectors (build_sector_hierarchy.py) -->
## Child Sectors (auto)

{links}
<!-- END auto child sectors -->
"""


def _validate_coverage(live_sectors: set[str]) -> list[str]:  # noqa: C901
    """Return a list of coverage errors (empty == valid).

    Checks that every live sector is mapped exactly once (no orphans, no
    collisions) and that no super-sector references a non-existent sector.
    """
    errors: list[str] = []
    mapped: list[str] = []
    for ss, members in SUPER_SECTORS.items():
        for m in members:
            mapped.append(m)
            if m not in live_sectors:
                errors.append(
                    f"super-sector {ss!r} references unknown sector {m!r}"
                )
    # orphans: live sectors not mapped to any super-sector
    orphans = live_sectors - set(mapped)
    for o in sorted(orphans):
        errors.append(f"sector {o!r} is not mapped to any super-sector")
    # collisions: a sector listed under >1 super-sector
    from collections import Counter
    dupes = [s for s, c in Counter(mapped).items() if c > 1]
    for d in sorted(dupes):
        owners = [ss for ss, mem in SUPER_SECTORS.items() if d in mem]
        errors.append(
            f"sector {d!r} is mapped to {len(owners)} super-sectors: {owners}"
        )
    # sub-category parents must be real sectors
    for parent in SUB_CATEGORIES:
        if parent not in live_sectors:
            errors.append(
                f"sub-category parent {parent!r} is not a live sector"
            )
    # super-sector names must NOT collide with any existing sector name —
    # entities.name is the PK, so INSERT OR IGNORE would silently skip a
    # colliding super_sector and leave the existing sector row mis-typed.
    # (Healthcare/Energy collided before the _Super rename; this guard
    # prevents any future taxonomy edit from reintroducing that.)
    for ss in SUPER_SECTORS:
        stem = _normalize(ss)
        if stem in live_sectors:
            errors.append(
                f"super-sector {ss!r} (name {stem!r}) collides with an "
                f"existing sector entity — rename it (e.g. add a _Super suffix)"
            )
    # sub-category names must NOT collide with any existing sector name —
    # same PK-collision risk as super-sectors. The two known cases
    # (Building_Materials/Packaging having self-named sub-categories) are
    # degenerate (the sub-category == its own parent sector) and are dropped
    # from the taxonomy; this guard catches any future reintroduction. It
    # also catches within-SUB_CATEGORIES duplicates (the same sub-category
    # name under two parents).
    seen_sub: set[str] = set()
    for parent, subs in SUB_CATEGORIES.items():
        for sub in subs:
            stem = _normalize(sub)
            if stem in live_sectors:
                errors.append(
                    f"sub-category {sub!r} (under {parent!r}, name {stem!r}) "
                    f"collides with an existing sector entity — drop or rename it"
                )
            if stem in seen_sub:
                errors.append(
                    f"sub-category {sub!r} (name {stem!r}) is duplicated across "
                    f"parents — sub_sector names must be unique"
                )
            seen_sub.add(stem)
    return errors


def build(*, write: bool) -> int:  # noqa: C901
    """Create super_sector/sub_sector entities + belongs_to edges + notes.

    Returns the number of rows that would be / were written.
    """
    conn = connect(DB_PATH)
    live_sectors = {
        r[0] for r in conn.execute(
            "SELECT name FROM entities WHERE entity_type='sector'"
        ).fetchall()
    }

    errors = _validate_coverage(live_sectors)
    if errors:
        print("✗ taxonomy coverage errors:", file=sys.stderr)
        for e in errors:
            print(f"    {e}", file=sys.stderr)
        conn.close()
        return 1

    # Build the full write set (entities + edges) before touching the DB so
    # --check can report counts without writing.
    today = date.today().isoformat()
    entity_rows: list[tuple] = []
    edge_rows: list[tuple] = []

    # 9 super_sector entities.
    for ss, members in SUPER_SECTORS.items():
        stem = _normalize(ss)
        entity_rows.append((
            stem, "super_sector", _note_path("super_sector", ss),
            stem, today,
        ))
        # sector -> super_sector belongs_to edges.
        for sector in members:
            edge_rows.append((
                sector, stem, "belongs_to",
                json.dumps({"hierarchy": "sector->super_sector"}),
                f"derive:sector_hierarchy:{today}",
            ))

    # ~21 sub_sector entities + sub_sector -> sector belongs_to edges.
    for parent_sector, subs in SUB_CATEGORIES.items():
        for sub in subs:
            stem = _normalize(sub)
            # Sub-categories are facets, not first-class notes — give them a
            # synthetic path but no markdown file (file_path NULL).
            entity_rows.append((
                stem, "sub_sector", None, stem, today,
            ))
            edge_rows.append((
                stem, parent_sector, "belongs_to",
                json.dumps({"hierarchy": "sub_sector->sector"}),
                f"derive:sector_hierarchy:{today}",
            ))

    n_entities = len(entity_rows)
    n_edges = len(edge_rows)
    print(
        f"taxonomy: {len(SUPER_SECTORS)} super-sectors, "
        f"{sum(len(v) for v in SUPER_SECTORS.values())} sector->super edges, "
        f"{sum(len(v) for v in SUB_CATEGORIES.values())} sub-categories",
        file=sys.stderr,
    )
    print(
        f"would write {n_entities} entities + {n_edges} belongs_to edges",
        file=sys.stderr,
    )

    if not write:
        # Drift gate (2026-08-19, mirroring sync_sector_wikilinks --check):
        # the taxonomy maps may be fine while the NOTES lag a missed --apply.
        stale_ss = _check_super_notes()
        stale_up = len(_uplink_changes(conn))
        if stale_ss or stale_up:
            print(
                f"✗ drift: {stale_ss} super-sector note(s) + {stale_up} "
                "sector uplink(s) stale — re-run with --apply",
                file=sys.stderr,
            )
            conn.close()
            return 1
        print("(--check mode: taxonomy + notes fresh, no writes performed)",
              file=sys.stderr)
        conn.close()
        return 0

    # Write inside a single transaction so a partial failure rolls back.
    with conn:
        for name, etype, fpath, norm, today_iso in entity_rows:
            conn.execute(
                "INSERT OR IGNORE INTO entities "
                "(name, entity_type, file_path, normalized_name, last_updated) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, etype, fpath, norm, today_iso),
            )
        for src, tgt, etype, props, source_ref in edge_rows:
            conn.execute(
                "INSERT OR IGNORE INTO graph_edges "
                "(source, target, edge_type, properties, source_ref) "
                "VALUES (?, ?, ?, ?, ?)",
                (src, tgt, etype, props, source_ref),
            )

    # Super-sector notes (idempotent: overwrite — they're auto-generated).
    # Uses SUPER_SECTORS_DIR (which derives from VAULT_ROOT, monkeypatchable
    # in tests) so build(write=True) never touches the real findata/ vault.
    SUPER_SECTORS_DIR.mkdir(parents=True, exist_ok=True)
    for ss, members in SUPER_SECTORS.items():
        stem = _normalize(ss)
        note_path = SUPER_SECTORS_DIR / f"{stem}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(
            _super_sector_note(ss, members), encoding="utf-8"
        )

    # Bidirectional mapping: write the upward `super_sector:` frontmatter
    # field + an auto up-link section into each sector note so the hierarchy
    # is navigable in both directions (super-sector notes link DOWN via the
    # Child Sectors section above; sector notes link UP via this section).
    _sync_sector_uplinks(conn)

    print(f"Applied: {n_entities} entities + {n_edges} edges + "
          f"{len(SUPER_SECTORS)} super-sector notes + sector up-links.",
          file=sys.stderr)
    conn.close()
    return 0


# Sentinel markers for the upward-link section (idempotent find-replace,
# mirroring sync_sector_wikilinks.py's convention).
_UP_BEGIN = "<!-- BEGIN auto super-sector uplink (build_sector_hierarchy.py) -->"
_UP_END = "<!-- END auto super-sector uplink -->"


_CHILD_BEGIN = "<!-- BEGIN auto child sectors (build_sector_hierarchy.py) -->"
_CHILD_END = "<!-- END auto child sectors -->"


def _check_super_notes() -> int:
    """Count super-sector notes whose Child Sectors (auto) region drifted.

    Region-scoped, NOT full-file: other writers legitimately extend these
    notes (the OKF backfill adds generated/sources/stale_after frontmatter),
    so only the sentinel-bracketed section this tool owns decides drift.
    """
    stale = 0
    for ss, members in SUPER_SECTORS.items():
        note_path = SUPER_SECTORS_DIR / f"{_normalize(ss)}.md"
        if not note_path.exists():
            stale += 1
            continue
        actual_region = _child_region(note_path.read_text(encoding="utf-8"))
        expected_region = _child_region(_super_sector_note(ss, members))
        if actual_region != expected_region:
            stale += 1
    return stale


def _child_region(text: str) -> str | None:
    """The sentinel-bracketed Child Sectors region, or None when absent."""
    m = re.search(
        rf"{re.escape(_CHILD_BEGIN)}(.*?){re.escape(_CHILD_END)}", text, re.S
    )
    return m.group(1) if m else None


def _uplink_changes(conn) -> list[tuple[Path, str, str]]:
    """Sector notes whose super-sector up-link content is stale.

    Returns ``(note_path, current_content, expected_content)`` triples —
    the writer applies them; the --check drift gate counts them.
    """
    # Build sector -> super_sector lookup from the belongs_to edges.
    sector_to_super = {
        r[0]: r[1] for r in conn.execute(
            "SELECT source, target FROM graph_edges "
            "WHERE edge_type='belongs_to' AND source IN "
            "(SELECT name FROM entities WHERE entity_type='sector')"
        ).fetchall()
    }
    sectors_dir = VAULT_ROOT / "Sectors"
    changes: list[tuple[Path, str, str]] = []
    for sector, super_name in sector_to_super.items():
        note_path = sectors_dir / f"{sector}.md"
        if not note_path.exists():
            continue
        content = note_path.read_text(encoding="utf-8")
        new_content = _inject_uplink(content, super_name)
        if new_content != content:
            changes.append((note_path, content, new_content))
    return changes


def _sync_sector_uplinks(conn) -> None:
    """Write the `super_sector:` frontmatter field + the auto up-link
    section into each stale sector note (applies ``_uplink_changes``).

    Idempotent (sentinel-bracketed replace; curated content untouched).
    """
    changes = _uplink_changes(conn)
    for note_path, _old, new_content in changes:
        note_path.write_text(new_content, encoding="utf-8")
    print(f"synced super-sector up-links in {len(changes)} sector note(s)",
          file=sys.stderr)


def _inject_uplink(content: str, super_name: str) -> str:
    """Inject the `super_sector:` field + the up-link section into a note.

    The field goes in the frontmatter (after `type:`). The section goes
    before the first `## ` heading (or appended if none). Both are
    idempotent: existing field/section is replaced, not duplicated.
    """
    import re
    super_stem = _normalize(super_name)
    section = (
        f"{_UP_BEGIN}\n## Super Sector (auto)\n\n"
        f"Part of the [[{super_stem}]] super-sector.\n{_UP_END}\n"
    )

    # --- 1. Replace the auto up-link section (idempotent). ---
    # First REMOVE any existing section (wherever it landed), then insert
    # the fresh one at the correct position (after the H1 title). This keeps
    # re-runs from leaving the section stranded above the title.
    pattern = re.compile(
        re.escape(_UP_BEGIN) + r".*?" + re.escape(_UP_END) + r"\n*",
        re.DOTALL,
    )
    content = pattern.sub("", content)
    # Insert AFTER the H1 title line (the first `# Foo`). Placing before the
    # H1 would push the title below the up-link, which reads wrong. If
    # there's no H1, append at the end.
    m = re.search(r"^# .+$", content, re.MULTILINE)
    if m:
        pos = m.end()
        content = content[:pos] + "\n\n" + section + content[pos:]
    else:
        content = content.rstrip() + "\n\n" + section

    # --- 2. Set/replace the `super_sector:` frontmatter field. ---
    # Remove any existing auto-set field first (idempotent).
    content = re.sub(
        r"^super_sector:.*\n", "", content, count=1, flags=re.MULTILINE
    )
    # Insert after the `type:` line (matches CANONICAL_ORDER: title, type,
    # sector, super_sector, ...).
    content = re.sub(
        r"(^type:.*\n)",
        rf"\1super_sector: {super_name}\n",
        content, count=1, flags=re.MULTILINE,
    )
    return content


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--check", action="store_true",
        help="Validate taxonomy coverage without writing (CI gate).",
    )
    g.add_argument(
        "--apply", action="store_true",
        help="Write entities, edges, and super-sector notes.",
    )
    args = p.parse_args()
    write = args.apply or (not args.check)
    # Default (no flag) is --check for safety; require explicit --apply.
    if not args.apply and not args.check:
        print("No --apply/--check given; defaulting to --check (dry-run).",
              file=sys.stderr)
        write = False
    return build(write=write)


if __name__ == "__main__":
    sys.exit(main())
