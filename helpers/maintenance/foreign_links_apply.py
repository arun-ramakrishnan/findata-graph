#!/usr/bin/env python3
"""Apply curated foreign-link mappings into entity_identifiers (S3 lane).

Reads ``helpers/misc/foreign_links.json`` (curated from the strict tier of
``foreign_links_generate.py``) and proposes registry rows:

- foreign ticker → ``identifier_type='alias'`` with ``namespace=exchange``
  (e.g. ``('Tencent', 'alias', '00700', 'HKEX')``);
- foreign ISIN (HKEX/TWSE where the master provides one) →
  ``identifier_type='isin'`` with ``namespace=exchange``.

Both types/namespaces fit the existing ``entity_identifiers`` registry
constraints (identifier_type ∈ alias/cin/cik/isin/lei/llpin). Rows are
idempotent (``INSERT OR IGNORE``) and conflict-checked: an existing row with
the same (identifier_type, identifier_value) owned by a *different* entity is
reported and never overwritten. Dry-run by default; ``--apply`` writes.

Usage:
    python3 helpers/maintenance/foreign_links_apply.py
    python3 helpers/maintenance/foreign_links_apply.py --apply
    python3 helpers/maintenance/foreign_links_apply.py --db /path/research.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DB = _PROJECT_ROOT / "memory" / "research.db"
DEFAULT_LINKS = _PROJECT_ROOT / "helpers" / "misc" / "foreign_links.json"

SOURCE_REF = "foreign_links_lane"


def load_links(path: Path) -> dict[str, list[dict]]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object (entity -> rows)")
    return data


def validate_entities(
    conn, links: dict[str, list[dict]]
) -> tuple[dict[str, list[dict]], list[str]]:
    """Drop unknown entities; return (valid links, unknown names)."""
    names = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    unknown = [n for n in links if n not in names]
    valid = {n: rows for n, rows in links.items() if n in names}
    return valid, unknown


def build_proposed(links: dict[str, list[dict]]) -> dict[tuple[str, str, str], set[str]]:
    """(entity, identifier_type, namespace) -> set of values."""
    proposed: dict[tuple[str, str, str], set[str]] = {}
    for entity, rows in links.items():
        for row in rows:
            exchange = (row.get("exchange") or "").strip()
            symbol = (row.get("symbol") or "").strip()
            isin = (row.get("isin") or "").strip()
            if exchange and symbol:
                proposed.setdefault((entity, "alias", exchange), set()).add(symbol)
            if exchange and isin:
                proposed.setdefault((entity, "isin", exchange), set()).add(isin)
    return proposed


def scan_conflicts(conn, proposed) -> int:
    """Count existing (identifier_type, identifier_value) rows owned by a
    different entity than proposed (never overwritten)."""
    conflicts = 0
    for (entity, id_type, _namespace), values in proposed.items():
        for value in sorted(values):
            owner = conn.execute(
                "SELECT entity_name FROM entity_identifiers "
                "WHERE identifier_type=? AND identifier_value=?",
                (id_type, value),
            ).fetchone()
            if owner is not None and owner[0] != entity:
                conflicts += 1
    return conflicts


def apply_candidates(conn, proposed) -> int:
    from helpers.core.db import utc_now

    now = utc_now()
    before = conn.execute("SELECT COUNT(*) FROM entity_identifiers").fetchone()[0]
    candidates = []
    for (entity, id_type, namespace), values in proposed.items():
        for value in sorted(values):
            candidates.append((entity, id_type, value, namespace, SOURCE_REF, now))
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO entity_identifiers "
            "(entity_name, identifier_type, identifier_value, namespace, source_ref, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            candidates,
        )
    after = conn.execute("SELECT COUNT(*) FROM entity_identifiers").fetchone()[0]
    return after - before


def render_report(links, proposed, unknown, conflicts) -> str:
    lines = [
        "# Foreign-link apply (S3) — dry-run report",
        "",
        f"Curated links file: {DEFAULT_LINKS}",
        f"Entities: {len(links)} (unknown/dropped: {len(unknown)} — {', '.join(unknown) or 'none'})",
        f"Proposed registry rows: {sum(len(v) for v in proposed.values())} across {len(proposed)} (entity, type, namespace) keys",
        f"Conflicts (existing different owner — never overwritten): {conflicts}",
        "",
        "| entity | type | namespace | value |",
        "|---|---|---|---|",
    ]
    for (entity, id_type, namespace), values in sorted(proposed.items()):
        for value in sorted(values):
            lines.append(f"| {entity} | {id_type} | {namespace} | {value} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--links", type=Path, default=DEFAULT_LINKS)
    parser.add_argument(
        "--apply", action="store_true", help="write registry rows (default: dry-run)"
    )
    args = parser.parse_args(argv)

    from helpers.core.db import connect

    links = load_links(args.links)
    conn = connect(args.db)
    try:
        valid, unknown = validate_entities(conn, links)
        proposed = build_proposed(valid)
        conflicts = scan_conflicts(conn, proposed)
        print(render_report(links, proposed, unknown, conflicts))
        if args.apply:
            inserted = apply_candidates(conn, proposed)
            total_registry = conn.execute("SELECT COUNT(*) FROM entity_identifiers").fetchone()[0]
            print(f"\nAPPLIED: {inserted} new rows (total registry rows now {total_registry}).")
        else:
            print("\nDry-run (no writes). Re-run with --apply to write.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
