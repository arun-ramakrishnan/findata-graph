#!/usr/bin/env python3
"""
Sync note tags into the SQLite `entity_tags` table (normalized, searchable).

The markdown notes under findata/ are the single source of truth for tags. This
helper rebuilds `entity_tags` (one row per entity x tag) from each note's YAML
front matter so tags can be queried in SQL without scanning the filesystem.

Only these categories are mirrored (see project decision):
    entity_type/   sector/   market_cap/   subsector/   holding_company/

Design:
- Full rebuild each run (DELETE + reinsert) -> idempotent, self-correcting,
  no stale rows from deleted entities.
- Entity -> note joined via entities.file_path (the sync contract, see
  doc/architecture.md §5). Notes whose entity has no resolvable file_path are
  skipped and reported.
- The `enhanced_tags` TEXT column on entities has been retired in favour of
  this normalized table.
- E5a: sector_classification is also derived from the note's sector/* tag,
  making notes the single source of truth for sector. The PascalCase column
  value is derived from the lowercase tag slug via the canonical sector set
  (helpers/validators/static_checks.py). Rows already matching are skipped
  (IS NOT), so a no-op sync writes nothing.

Usage:
    python3 helpers/core/sync_tags.py            # rebuild, exit 0 on success
    python3 helpers/core/sync_tags.py --db PATH  # alternate DB
    python3 helpers/core/sync_tags.py --report   # print per-category breakdown

Exit codes: 0 success, 1 DB not found / fatal error.
"""

import argparse
import sys
from pathlib import Path

# Repo root: helpers/core/sync_tags.py -> parents[2]. Must be on sys.path
# BEFORE the `from helpers.core.db import connect` below so the script works
# when invoked as a subprocess (make sync-tags, maint.py --full) the same way
# it works under pytest's import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.validators.static_checks import CANONICAL_SECTORS  # noqa: E402
from helpers.core.frontmatter import extract_tags as split_front_matter  # noqa: E402

# Only these tag categories are mirrored into the DB.
# HISTORY: originally entity_type/sector/market_cap/subsector, plus
# holding_company (the one purpose-built namespace beyond the original 4, for
# the "show me holding companies" / "exclude pure holding shells" query use
# case). The four C1 namespaces below — geography/business_model/risk_investment
# /investment_theme — were long DEFERRED (no concrete query use case when
# ALLOWED_CATEGORIES was first written), which silently dropped ~3,100 tags that
# the notes carried in YAML (findata_corpus_audit.txt C1). They are now admitted
# (2026-07-30, D3): they carry real classification signal, and investment_theme
# in particular underpins the D4 cross-sector theme layer. Verify_notes now
# WARNS on values outside the known-good set per namespace, so adding a
# namespace here does not invite uncontrolled sprawl.
ALLOWED_CATEGORIES = (
    "entity_type",
    "sector",
    "market_cap",
    "subsector",
    "holding_company",
    "geography",
    "business_model",
    "risk_investment",
    "investment_theme",
)

# Reverse map: lowercase sector slug (as it appears in `sector/*` tags) -> the
# canonical PascalCase form stored in entities.sector_classification. Tags are
# guaranteed lowercase + canonical by check_tag_canonicalization(), so this is
# an unambiguous bijection (Bundle E5a — notes are the single source of truth,
# and sector_classification is derived from the note tag, not written
# independently).
_SECTOR_SLUG_TO_CANONICAL = {s.lower(): s for s in CANONICAL_SECTORS}


def allowed_tags(tags):
    out = []
    for t in tags:
        t = t.strip()
        cat = t.split("/", 1)[0]
        if cat in ALLOWED_CATEGORIES and "/" in t:
            out.append(t)
    return out


def main():  # noqa: C901
    ap = argparse.ArgumentParser(description="Sync note tags -> entity_tags table.")
    ap.add_argument(
        "--db",
        default=str(_REPO_ROOT / "memory" / "research.db"),
        help="Path to research.db (default: memory/research.db).",
    )
    ap.add_argument(
        "--report", action="store_true", help="Print per-category breakdown after sync."
    )
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = _REPO_ROOT / db_path
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)  # FK ON + WAL via canonical helper
    try:
        # Ensure table exists (matches production schema: PK + FK with
        # both cascades, established by an earlier one-shot migration).
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS entity_tags (
                entity_name TEXT NOT NULL,
                tag         TEXT NOT NULL,
                PRIMARY KEY (entity_name, tag),
                FOREIGN KEY (entity_name) REFERENCES entities(name)
                    ON DELETE CASCADE ON UPDATE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_entity_tags_tag ON entity_tags(tag);
            """
        )

        rows = conn.execute(
            "SELECT name, file_path, entity_type FROM entities ORDER BY name"
        ).fetchall()

        missing_files = []
        no_tags = []
        unknown_sectors = []
        bulk = []
        sector_updates = []  # (canonical_pascalcase, name) for E5a
        seen_entities = 0
        for name, file_path, entity_type in rows:
            if not file_path:
                missing_files.append((name, "(empty path)"))
                continue
            fp = _REPO_ROOT / file_path
            if not fp.exists():
                missing_files.append((name, file_path))
                continue
            try:
                text = fp.read_text(encoding="utf-8")
            except Exception as e:
                missing_files.append((name, f"{file_path} (read error: {e})"))
                continue
            tags = allowed_tags(split_front_matter(text))
            # dedupe while preserving order
            seen = set()
            uniq = []
            for t in tags:
                if t not in seen:
                    seen.add(t)
                    uniq.append(t)
            if not uniq:
                no_tags.append(name)
                continue
            seen_entities += 1
            for t in uniq:
                bulk.append((name, t))

            # Derive sector_classification from the note's sector/* tag (E5a).
            # Notes are the single source of truth; the column becomes fully
            # derived from the tag, eliminating drift between the two stores.
            # Only applies to companies — sector_classification classifies
            # companies into sectors, so a sector entity tagging itself
            # (sector/automotive on the Automotive sector note) is ignored.
            if entity_type == "company":
                sector_tag = next((t for t in uniq if t.startswith("sector/")), None)
                if sector_tag:
                    slug = sector_tag.split("/", 1)[1]
                    canonical = _SECTOR_SLUG_TO_CANONICAL.get(slug)
                    if canonical:
                        sector_updates.append((canonical, name))
                    else:
                        unknown_sectors.append((name, sector_tag))

        # Full rebuild inside a transaction.
        with conn:
            conn.execute("DELETE FROM entity_tags")
            if bulk:
                conn.executemany(
                    "INSERT OR IGNORE INTO entity_tags (entity_name, tag) VALUES (?, ?)",
                    bulk,
                )
            # E5a: derive sector_classification from the note's sector/* tag so
            # the column and the tag table stay in sync. IS NOT (not !=) handles
            # NULL correctly AND skips rows where the value already matches — a
            # no-op sync writes nothing. Entities without a sector tag keep their
            # existing value (non-destructive).
            sector_changed = 0
            if sector_updates:
                cur = conn.cursor()
                cur.executemany(
                    "UPDATE entities SET sector_classification = ? "
                    "WHERE name = ? AND sector_classification IS NOT ?",
                    [(canonical, name, canonical) for canonical, name in sector_updates],
                )
                sector_changed = cur.rowcount or 0

        inserted = conn.execute("SELECT COUNT(*) FROM entity_tags").fetchone()[0]
        total_entities = len(rows)

        print(
            f"sync_tags: {inserted} tags across {seen_entities} entities "
            f"(of {total_entities} total)."
        )
        if sector_changed:
            print(
                f"  sector_classification: {sector_changed} row(s) updated "
                f"from note sector/* tags."
            )

        if missing_files:
            print(
                f"  [warn] {len(missing_files)} entities with unresolvable file_path:",
                file=sys.stderr,
            )
            for n, fp in missing_files[:20]:
                print(f"    - {n}: {fp}", file=sys.stderr)
            if len(missing_files) > 20:
                print(f"    ... ({len(missing_files) - 20} more)", file=sys.stderr)
        if no_tags:
            print(
                f"  [warn] {len(no_tags)} entities with no matching tags:",
                file=sys.stderr,
            )
            for n in no_tags[:20]:
                print(f"    - {n}", file=sys.stderr)
            if len(no_tags) > 20:
                print(f"    ... ({len(no_tags) - 20} more)", file=sys.stderr)
        if unknown_sectors:
            print(
                f"  [warn] {len(unknown_sectors)} entities with non-canonical "
                f"sector tags:",
                file=sys.stderr,
            )
            for n, tag in unknown_sectors[:20]:
                print(f"    - {n}: {tag}", file=sys.stderr)

        if args.report:
            print("\n=== per-category breakdown ===")
            for cat, cnt in conn.execute(
                "SELECT substr(tag,1,instr(tag,'/')-1) AS cat, COUNT(*) "
                "FROM entity_tags GROUP BY cat ORDER BY 2 DESC"
            ):
                print(f"  {cat:14s} {cnt:5d}")
            print("\n=== top sectors ===")
            # GLOB (not LIKE) so the existing idx_entity_tags_tag (BINARY) is
            # used as a range search; LIKE 'sector/%' would fall back to a
            # SCAN. All sector tags are inserted lowercase by this script, so
            # the case-sensitivity of GLOB is a non-issue here.
            for tag, cnt in conn.execute(
                "SELECT tag, COUNT(*) FROM entity_tags WHERE tag GLOB 'sector/*' "
                "GROUP BY tag ORDER BY 2 DESC LIMIT 12"
            ):
                print(f"  {cnt:4d}  {tag}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
