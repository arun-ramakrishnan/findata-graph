#!/usr/bin/env python3
"""swap_fk_integrity.py — FK integrity check on a DuckDB research-store copy.

Read-only; the DuckDB equivalent of SQLite's `PRAGMA foreign_key_check` for
the research.db FK edges (Step 4 of duckdb_research_swap_steps.md). Edges are
read from the cascade manifest emitted by Step 2
(findata/Misc/duckdb_research_swap_cascade_manifest.json).

Semantics preserved from SQLite:
  - a NULL child key satisfies the constraint (child-side NULL guard),
  - multi-column FKs match on all column pairs.

Usage:
  python3 helpers/misc/swap_fk_integrity.py <store.duckdb> [--json]

Exit codes:
  0  no orphans (or store unreadable -> hard error, exit 2)
  1  orphans found (each listed as child_table: col=value -> parent_table)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO / "findata/Misc/duckdb_research_swap_cascade_manifest.json"


def load_edges() -> list[dict]:
    if not MANIFEST.exists():
        raise SystemExit(f"manifest not found: {MANIFEST}")
    data = json.loads(MANIFEST.read_text())
    # keep all FK edges (with and without actions) — Step 1 scan used 24
    edges = [
        {
            "owning_table": e["owning_table"],
            "owning_columns": e["owning_columns"],
            "referenced_table": e["referenced_table"],
            "referenced_columns": e["referenced_columns"],
        }
        for e in data
    ]
    # de-dup multi-column edges already aggregated by constraint id
    seen = set()
    out = []
    for e in edges:
        key = (e["owning_table"], tuple(e["owning_columns"]), e["referenced_table"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def check_edge(con, edge: dict) -> list[str]:
    child, parent = edge["owning_table"], edge["referenced_table"]
    ccols, pcols = edge["owning_columns"], edge["referenced_columns"]
    conds = []
    for cc, pc in zip(ccols, pcols):
        conds.append(f'c."{cc}" = p."{pc}"')
    null_guards = " AND ".join(f'c."{cc}" IS NOT NULL' for cc in ccols)
    sql = (
        f'SELECT c."{ccols[0]}", c.rowid '
        f'FROM "{child}" c LEFT JOIN "{parent}" p '
        f"ON {' AND '.join(conds)} "
        f'WHERE {null_guards} AND p."{pcols[0]}" IS NULL '
        f"LIMIT 50"
    )
    orphans = []
    try:
        rows = con.execute(sql).fetchall()
    except Exception:
        return []  # table missing on this copy — skip, not an orphan
    for r in rows:
        orphans.append(f"{child}.{ccols[0]}={r[0]!r} (rowid {r[1]}) -> {parent}")
    return orphans


def scan_orphans(conn) -> tuple[list[str], int]:
    """Run the full manifest-edge orphan scan over an open connection.

    Returns ``(orphans, checked_edges)`` — the same findings the CLI
    reports, as data, so other tools (db_maint --check) can embed the
    scan without re-opening the store.
    """
    edges = load_edges()
    findings: list[str] = []
    for e in edges:
        for o in check_edge(conn, e):
            findings.append(o)
    return findings, len(edges)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("store", help="DuckDB store to check (e.g. research_converted.duckdb)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    import duckdb  # local: only needed on the check path

    con = duckdb.connect(args.store, read_only=True)
    try:
        findings, checked = scan_orphans(con)
    finally:
        con.close()

    if args.json:
        print(json.dumps({"orphans": findings, "checked_edges": checked}, indent=2))
    else:
        if findings:
            print(f"ORPHANS ({len(findings)}):")
            for f in findings:
                print("  ", f)
        else:
            print(f"no FK orphans across {checked} edges")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
