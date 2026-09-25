#!/usr/bin/env python3
"""Converge The Chatter edition tags from company quote coverage."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SLUG_RE = re.compile(r"[a-z0-9_]+")


@dataclass
class CoveragePlan:
    tags_by_edition: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    pairs: int = 0
    company_pairs: int = 0
    non_company_pairs: int = 0
    missing_entity_pairs: int = 0
    invalid_slug_pairs: int = 0
    missing_editions: int = 0


def build_plan(conn: sqlite3.Connection, root: Path = REPO_ROOT) -> CoveragePlan:
    company_slugs: dict[str, str] = {}
    entity_types: dict[str, str] = {}
    for row in conn.execute("SELECT name, normalized_name, entity_type, file_path FROM entities"):
        name = row["name"]
        entity_types[name] = row["entity_type"]
        if row["entity_type"] != "company" or not row["file_path"]:
            continue
        slug = Path(row["file_path"]).stem.lower()
        company_slugs[name] = slug
        if row["normalized_name"]:
            company_slugs[row["normalized_name"]] = slug

    plan = CoveragePlan()
    chatter_dir = root / "findata" / "The_Chatter"
    rows = conn.execute(
        "SELECT DISTINCT as_of_edition, entity FROM quotes "
        "WHERE as_of_edition IS NOT NULL AND entity IS NOT NULL"
    )
    for row in rows:
        plan.pairs += 1
        entity = row["entity"]
        if entity not in entity_types:
            plan.missing_entity_pairs += 1
            continue
        if entity_types[entity] != "company":
            plan.non_company_pairs += 1
            continue
        slug = company_slugs.get(entity)
        if slug is None:
            plan.missing_entity_pairs += 1
            continue
        if not SLUG_RE.fullmatch(slug):
            plan.invalid_slug_pairs += 1
            continue
        edition = Path(row["as_of_edition"]).stem
        if not (chatter_dir / f"{edition}.md").is_file():
            plan.missing_editions += 1
            continue
        plan.company_pairs += 1
        plan.tags_by_edition[edition].add(f"company/{slug}")
    return plan


def merge_tags(text: str, additions: set[str]) -> tuple[str, bool]:
    from helpers.core.frontmatter import (
        render_frontmatter,
        split_frontmatter,
        stringify_dates,
        yaml_safe_load,
    )

    dashes, yaml_body, rest = split_frontmatter(text)
    if not dashes:
        return text, False
    fields = yaml_safe_load(yaml_body) or {}
    if not isinstance(fields, dict):
        return text, False
    tags = fields.get("tags", [])
    if tags is None:
        tags = []
    if not isinstance(tags, list):
        return text, False
    current = {tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()}
    merged = sorted(current | additions)
    if merged == tags:
        return text, False
    fields = stringify_dates(fields)
    fields["tags"] = merged
    return render_frontmatter(fields) + rest, True


def apply_plan(plan: CoveragePlan, root: Path = REPO_ROOT, *, apply: bool) -> list[str]:
    changed: list[str] = []
    chatter_dir = root / "findata" / "The_Chatter"
    for edition, additions in sorted(plan.tags_by_edition.items()):
        path = chatter_dir / f"{edition}.md"
        text = path.read_text(encoding="utf-8")
        new_text, did_change = merge_tags(text, additions)
        if not did_change:
            continue
        changed.append(path.relative_to(root).as_posix())
        if apply:
            path.write_text(new_text, encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write converged edition tags")
    mode.add_argument("--check", action="store_true", help="report drift and exit 1 if stale")
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "memory" / "research.db")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 1
    from helpers.core.db import connect

    conn = connect(args.db)
    try:
        plan = build_plan(conn, args.root)
        changed = apply_plan(plan, args.root, apply=args.apply)
    finally:
        conn.close()

    print(
        f"coverage tags: pairs={plan.pairs} company={plan.company_pairs} "
        f"non_company={plan.non_company_pairs} missing_entities={plan.missing_entity_pairs} "
        f"invalid_slugs={plan.invalid_slug_pairs} missing_editions={plan.missing_editions} "
        f"changed={len(changed)}",
        file=sys.stderr,
    )
    shown = changed if args.verbose else changed[:12]
    for path in shown:
        print(f"  {path}")
    if not args.verbose and len(changed) > len(shown):
        print(f"  ... ({len(changed) - len(shown)} more; --verbose for all)")
    if args.check and changed:
        return 1
    if not args.apply:
        print(
            "dry-run: no files written; pass --apply at the operator checkpoint.", file=sys.stderr
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
