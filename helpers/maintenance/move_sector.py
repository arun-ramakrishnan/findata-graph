#!/usr/bin/env python3
"""Move a company entity from one sector_classification to another.

Updates:
  1. entities.sector_classification (SQLite)
  2. relations rows referencing the old/new sector (part_of / has_company)
  3. Markdown file: physically moves to new sector dir
  4. YAML front matter: sector, permalink, file_path, tags (sector/ tag)

Usage:
    python3 helpers/maintenance/move_sector.py <entity_name> <new_sector>
    python3 helpers/maintenance/move_sector.py --batch <json_file>

Batch JSON format (list of {name, new_sector}):
    [{"name": "Haleon India", "new_sector": "Consumer"}, ...]

Safety:
    - Validates new sector exists in canonical 42-sector list.
    - Verifies source file exists; refuses to overwrite existing dest file.
    - Idempotent: if entity is already in new_sector, skips.
    - Runs in a transaction; rolls back on any error.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DB_PATH = PROJECT_ROOT / "memory" / "research.db"
COMPANIES_DIR = PROJECT_ROOT / "findata" / "Companies"

# Import the canonical sector set from the validator (single source of truth).
# Previously a hand-maintained copy here that had drifted (missing FMCG,
# Logistics, Media_Entertainment; spurious Pharmaceuticals). Bundle E5a bonus.
from helpers.validators.static_checks import CANONICAL_SECTORS  # noqa: E402
from helpers.core.db import utc_now  # noqa: E402  (Bundle T1: UTC last_updated)
from helpers.core.frontmatter import split_frontmatter as split_front_matter  # noqa: E402

TODAY = date.today().isoformat()  # date-only, for markdown YAML last_modified


def err(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def normalize_sector_tag_value(sector: str) -> str:
    """Map a PascalCase sector to its lowercase tag-slug form used in YAML tags.

    Most notes use lowercase (sector/healthcare, sector/fintech_payments) but
    some legacy notes use the PascalCase form (sector/Semiconductors). We
    normalize to lowercase to match the canonical pattern.
    """
    return sector.lower()


def update_yaml_field(yaml_text: str, field: str, new_value: str) -> str:
    """Replace the value of `field:` in YAML front matter. Adds the field if
    absent (before the closing ---)."""
    # Match field at start of line, optional quote around value.
    pattern = re.compile(
        r"^(\s*" + re.escape(field) + r")\s*:\s*.*$",
        re.MULTILINE,
    )
    if pattern.search(yaml_text):
        return pattern.sub(rf"\1: {new_value}", yaml_text, count=1)
    # Field absent — insert before closing ---
    return yaml_text.replace("---\n", f"---\n{field}: {new_value}\n", 1)


def update_yaml_sector_tag(yaml_text: str, old_sector: str, new_sector: str) -> str:
    """Replace sector/<old> tag (any case) with sector/<new_lower> in tags list."""
    old_pat = re.compile(
        r"^(\s*-\s*)sector/" + re.escape(old_sector) + r"\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    new_tag = f"sector/{normalize_sector_tag_value(new_sector)}"
    if old_pat.search(yaml_text):
        return old_pat.sub(rf"\g<1>{new_tag}", yaml_text, count=1)
    # No existing sector tag — add one (rare).
    return yaml_text


def update_yaml_permalink(yaml_text: str, new_sector: str, normalized_name: str) -> str:
    new_permalink = f"companies/{new_sector.lower()}/{normalized_name.lower()}"
    pattern = re.compile(r"^(\s*permalink\s*:\s*).*$", re.MULTILINE)
    if pattern.search(yaml_text):
        return pattern.sub(rf"\g<1>{new_permalink}", yaml_text, count=1)
    return yaml_text + f"permalink: {new_permalink}\n"


def update_yaml_file_path(yaml_text: str, new_sector: str, normalized_name: str) -> str:
    new_fp = f"findata/Companies/{new_sector}/{normalized_name}.md"
    pattern = re.compile(r"^(\s*file_path\s*:\s*).*$", re.MULTILINE)
    if pattern.search(yaml_text):
        return pattern.sub(rf"\g<1>{new_fp}", yaml_text, count=1)
    return yaml_text + f"file_path: {new_fp}\n"


def update_yaml_sector_field(yaml_text: str, new_sector: str) -> str:
    """Update `sector:` field. Accept quoted or unquoted; preserve nothing fancy."""
    pattern = re.compile(r"^(\s*sector\s*:\s*).*$", re.MULTILINE)
    if pattern.search(yaml_text):
        return pattern.sub(rf"\g<1>{new_sector}", yaml_text, count=1)
    return yaml_text + f"sector: {new_sector}\n"


def bump_last_modified(yaml_text: str) -> str:
    pattern = re.compile(r"^(\s*last_modified\s*:\s*).*$", re.MULTILINE)
    if pattern.search(yaml_text):
        return pattern.sub(rf"\g<1>{TODAY}", yaml_text, count=1)
    return yaml_text + f"last_modified: {TODAY}\n"


def move_entity(conn: sqlite3.Connection, entity_name: str, new_sector: str, dry_run: bool = False) -> bool:
    print(f"\n→ {entity_name} → {new_sector}")

    if new_sector not in CANONICAL_SECTORS:
        err(f"'{new_sector}' is not in the canonical 42-sector list")
        return False

    cur = conn.cursor()
    row = cur.execute(
        "SELECT name, normalized_name, sector_classification, file_path FROM entities WHERE name = ?",
        (entity_name,),
    ).fetchone()
    if not row:
        err(f"entity not found in DB: {entity_name}")
        return False
    name, normalized_name, old_sector, file_path = row

    if old_sector == new_sector:
        ok(f"already in {new_sector}, skipping")
        return True

    if not file_path or not file_path.startswith("findata/"):
        err(f"bad file_path in DB: {file_path}")
        return False

    src_abs = PROJECT_ROOT / file_path
    if not src_abs.exists():
        err(f"source file missing: {src_abs}")
        return False

    new_file_path = f"findata/Companies/{new_sector}/{normalized_name}.md"
    dst_abs = PROJECT_ROOT / new_file_path
    if dst_abs.exists() and src_abs.resolve() != dst_abs.resolve():
        err(f"destination already exists: {dst_abs}")
        return False

    # Read & update markdown (read-only until the move is committed).
    text = src_abs.read_text(encoding="utf-8")
    opener, yaml_body, rest = split_front_matter(text)
    if not opener:
        err("no YAML front matter found")
        return False

    yaml_body = update_yaml_sector_field(yaml_body, new_sector)
    yaml_body = update_yaml_sector_tag(yaml_body, old_sector, new_sector)
    yaml_body = update_yaml_permalink(yaml_body, new_sector, normalized_name)
    yaml_body = update_yaml_file_path(yaml_body, new_sector, normalized_name)
    yaml_body = bump_last_modified(yaml_body)

    new_text = opener + yaml_body + rest

    if dry_run:
        print("  [DRY RUN] Would:")
        print(f"    DB:   sector_classification {old_sector} → {new_sector}")
        print(f"    FILE: {file_path} → {new_file_path}")
        print(f"    REL:  {old_sector}↔{name} dropped, {new_sector}↔{name} added")
        return True

    # 1. Update the DB FIRST (entities + graph_edges), inside the caller's
    #    transaction. The filesystem move happens LAST (step 2) so a failure
    #    during the DB writes leaves the markdown file untouched — otherwise a
    #    rolled-back DB would point at a sector whose file was already
    #    relocated (split-brain). See the Slice-D transactional-hardening
    #    finding: move_entity() must not move the file before the DB commits.
    #    Bundle T1: last_updated uses utc_now() (full UTC datetime) so the
    #    staleness comparison against graph_analytics.computed_at (also UTC
    #    CURRENT_TIMESTAMP) is apples-to-apples. TODAY (date-only) is for
    #    the markdown YAML last_modified field only.
    cur.execute(
        "UPDATE entities SET sector_classification = ?, file_path = ?, last_updated = ? WHERE name = ?",
        (new_sector, new_file_path, utc_now(), name),
    )
    ok("DB entities row updated (sector_classification, file_path)")

    # Edges: drop old part_of/has_company, add new bidirectional pair.
    # NB: writes to graph_edges (since the Phase-1 migration the
    # `relations` name is a read-only VIEW over graph_edges).
    cur.execute(
        "DELETE FROM graph_edges WHERE source = ? AND target = ? AND edge_type = 'part_of'",
        (name, old_sector),
    )
    cur.execute(
        "DELETE FROM graph_edges WHERE source = ? AND target = ? AND edge_type = 'has_company'",
        (old_sector, name),
    )
    # Insert new pair only if not already present
    cur.execute(
        "INSERT OR IGNORE INTO graph_edges (source, target, edge_type, source_ref) "
        "VALUES (?, ?, 'part_of', 'move_sector')",
        (name, new_sector),
    )
    cur.execute(
        "INSERT OR IGNORE INTO graph_edges (source, target, edge_type, source_ref) "
        "VALUES (?, ?, 'has_company', 'move_sector')",
        (new_sector, name),
    )
    ok(f"graph_edges updated: {old_sector}↔{name} → {new_sector}↔{name}")

    # 2. Move the file LAST, only after the DB writes succeeded. A failure
    #    above aborts before the filesystem is touched.
    dst_abs.parent.mkdir(parents=True, exist_ok=True)
    src_abs.rename(dst_abs)
    ok(f"file moved → {new_file_path}")
    dst_abs.write_text(new_text, encoding="utf-8")
    ok("YAML updated (sector, permalink, file_path, sector/ tag, last_modified)")

    # 5. entity_tags: rebuild happens via sync_tags.py at the end
    return True


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    from helpers.core.db import connect
    # row_factory=None: this helper uses positional tuple unpacking
    conn = connect(DB_PATH, row_factory=None)
    rc = 0

    try:
        if args[0] == "--batch":
            if len(args) < 2:
                print("error: --batch requires a json file path", file=sys.stderr)
                return 2
            batch = json.loads(Path(args[1]).read_text())
            entries = [(e["name"], e["new_sector"]) for e in batch]
        elif len(args) == 2:
            entries = [(args[0], args[1])]
        else:
            print(__doc__)
            return 2

        conn.execute("BEGIN")
        all_ok = True
        for name, new_sector in entries:
            if not move_entity(conn, name, new_sector):
                all_ok = False
                break
        if all_ok:
            conn.commit()
            print("\n✅ All moves committed.")
        else:
            conn.rollback()
            print("\n❌ Rolled back due to errors.", file=sys.stderr)
            rc = 1
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Fatal: {e}", file=sys.stderr)
        rc = 1
    finally:
        conn.close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
