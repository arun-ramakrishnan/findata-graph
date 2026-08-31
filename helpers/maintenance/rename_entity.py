#!/usr/bin/env python3
"""Rename an entity in the FinData knowledge graph, atomically.

The rename runs with ``PRAGMA foreign_keys = ON`` so that the PK change on
``entities.name`` cascades to every referencing row (relations.source/target,
entity_tags.entity_name, graph_edges.source/target — all declared with ``ON
UPDATE CASCADE``) inside a single ``BEGIN``…``COMMIT`` transaction. FKs are
NOT disabled; the cascade is what keeps the graph consistent.

Also moves the markdown file if `--move` is passed, and updates YAML fields
(normalized_name, file_path, permalink, title).

Usage:
    python3 helpers/maintenance/rename_entity.py <old_name> <new_name> [--sector <Sector>] [--ticker <T>]
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "memory" / "research.db"
TODAY = date.today().isoformat()  # date-only, for markdown YAML last_modified

# Ensure both the repo root (for `helpers.core.db`) and helpers/ (for
# `core.parse_newsletter`, the legacy short-form import below) are importable
# when this script runs as a subprocess.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "helpers") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "helpers"))

from helpers.core.db import connect, utc_now  # noqa: E402  (T1: utc_now for last_updated)

# Reuse the canonical normalizer rather than maintaining a second copy.
# Both functions must agree on the filename contract (PascalCase, single
# underscores, leading letter); consolidating eliminates the divergence bug
# surfaced by test_fuzz_normalizers.py.
from core.parse_newsletter import normalize_name as _normalize_name  # noqa: E402
from helpers.core.frontmatter import split_frontmatter as split_fm  # noqa: E402


def replace_field(yaml: str, field: str, value: str) -> str:
    # [ \t]* (NOT \s*) around the field/colon: \s matches \n, so \s* would let
    # group(1) swallow the line's trailing newline, and the in-place sub on the
    # 2nd call would drop content (breaks idempotency). Horizontal-whitespace
    # only keeps the match on a single line. See test_fuzz_replace_field_*.
    pat = re.compile(r"^([ \t]*" + re.escape(field) + r"[ \t]*:[ \t]*).*$", re.MULTILINE)
    if pat.search(yaml):
        return pat.sub(rf"\g<1>{value}", yaml, count=1)
    return yaml + f"{field}: {value}\n"


def main() -> int:  # noqa: C901
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    old_name = sys.argv[1]
    new_name = sys.argv[2]

    sector_override = None
    ticker_override = None
    args = sys.argv[3:]
    i = 0
    while i < len(args):
        if args[i] == "--sector" and i + 1 < len(args):
            sector_override = args[i + 1]
            i += 2
        elif args[i] == "--ticker" and i + 1 < len(args):
            ticker_override = args[i + 1]
            i += 2
        else:
            i += 1

    new_normalized = _normalize_name(new_name)

    # row_factory=None: this script uses tuple-style fetchone() unpacking.
    # FK ON is required for ON UPDATE CASCADE on rename.
    conn = connect(DB_PATH, row_factory=None)
    try:
        conn.execute("BEGIN")

        row = conn.execute(
            "SELECT name, normalized_name, sector_classification, file_path FROM entities WHERE name = ?",
            (old_name,),
        ).fetchone()
        if not row:
            print(f"ERROR: entity not found: {old_name}", file=sys.stderr)
            conn.rollback()
            return 1
        _, old_norm, sector, file_path = row
        if sector_override:
            sector = sector_override

        new_file_path = f"findata/Companies/{sector}/{new_normalized}.md"

        print(f"Renaming: {old_name} → {new_name}")
        print(f"  normalized_name: {old_norm} → {new_normalized}")
        print(f"  file_path: {file_path} → {new_file_path}")
        print(f"  sector: {sector}")

        # 1. Update entities row (PK change). With PRAGMA foreign_keys = ON and
        #    both relations.source/target and entity_tags.entity_name declared
        #    ON UPDATE CASCADE, the dependent rows follow automatically.
        # Bundle T1: last_updated uses utc_now() (full UTC datetime) so the
        # staleness comparison against graph_analytics.computed_at (also UTC
        # CURRENT_TIMESTAMP) is apples-to-apples. TODAY (date-only) is for
        # the markdown YAML last_modified field only.
        if ticker_override:
            conn.execute(
                "UPDATE entities SET name = ?, normalized_name = ?, file_path = ?, sector_classification = ?, ticker = ?, last_updated = ? WHERE name = ?",
                (
                    new_name,
                    new_normalized,
                    new_file_path,
                    sector,
                    ticker_override,
                    utc_now(),
                    old_name,
                ),
            )
        else:
            conn.execute(
                "UPDATE entities SET name = ?, normalized_name = ?, file_path = ?, sector_classification = ?, last_updated = ? WHERE name = ?",
                (new_name, new_normalized, new_file_path, sector, utc_now(), old_name),
            )

        # 2. Move + update markdown
        src = PROJECT_ROOT / file_path
        dst = PROJECT_ROOT / new_file_path
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8")
            opener, yaml, rest = split_fm(text)
            if opener:
                yaml = replace_field(yaml, "normalized_name", new_normalized)
                yaml = replace_field(yaml, "file_path", new_file_path)
                yaml = replace_field(
                    yaml, "permalink", f"companies/{sector.lower()}/{new_normalized.lower()}"
                )
                yaml = replace_field(yaml, "title", new_name)
                yaml = replace_field(yaml, "sector", sector)
                if ticker_override:
                    yaml = replace_field(yaml, "ticker", ticker_override)
                yaml = replace_field(yaml, "last_modified", TODAY)
                text = opener + yaml + rest
            src.rename(dst)
            dst.write_text(text, encoding="utf-8")
            print("  ✓ file moved + YAML updated")
        else:
            print(f"  ⚠ source file missing: {src}", file=sys.stderr)

        conn.commit()
        # Integrity check: with cascades on, this should be clean.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            print(f"  ⚠ FK violations after rename: {violations}", file=sys.stderr)
            return 1
        print(f"\n✅ Renamed {old_name} → {new_name} (DB + file).")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
