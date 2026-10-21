#!/usr/bin/env python3
"""Retire the ``part_of``/``has_company`` dyad pair (partof_hascompany_retirement S2).

The §8-R3 gate required hyperedge absorption BEFORE retirement. Measured
2026-10-18/19 on the live store, the absorption half is COMPLETE:

- ``part_of`` sources also in a sector hyperedge: **1207 / 1207**
- ``has_company`` targets covered: **1207 / 1207**
- pair correspondence: 1207 ``part_of`` pairs vs 1,208 sector incidences
- asymmetry probes, both directions: **zero rows**
- the one surplus incidence is an INFORMATION GAIN, not a discrepancy:
  ``Kuantum Papers`` holds BOTH ``Agriculture`` and ``Packaging`` sector
  hyperedges while its single ``part_of`` row can name only Agriculture.
  A single-valued dyad cannot express dual membership; the incidence
  store can. That is the whole argument for making it canonical.

After this migration:
- the sector hyperedges are the ONLY stored membership;
- ``helpers/graph/query.py`` projects ``part_of``/``has_company`` back out
  (``_materialise_sector_membership``) so ``e_belongs``/``e_has``, onager,
  igraph, the walk substrate, snapshots and the API are unchanged;
- the edge-type roster drops to 18 (17 populated — ``penalized_by`` was
  already empty);
- ``database_integrity_check`` validates the incidence store instead of a
  symmetric dyad pair.

Idempotent by construction: a second run finds zero rows and reports it.
Dry-run default (house rule); ``--apply`` deletes inside ONE transaction
after re-verifying absorption — the verification is the gate, not a
formality: if any ``part_of`` row lacks an incidence the migration REFUSES
rather than destroying membership the hyper store cannot reproduce.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402

RETIRED_TYPES = ("part_of", "has_company")

_ABSORPTION_SQL = """
SELECT COUNT(*) FROM graph_edges g
WHERE g.edge_type = 'part_of'
  AND NOT EXISTS (
    SELECT 1 FROM hyper_incidences hi
    JOIN hyper_edges he ON he.id = hi.edge_id
    WHERE he.edge_type = 'sector'
      AND he.label = g.target
      AND hi.entity_name = g.source
  )
"""


def measure(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """Inventory for the dry-run report and the apply-time gate."""
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        pair = con.execute(
            "SELECT edge_type, COUNT(*) FROM graph_edges "
            "WHERE edge_type IN (?, ?) GROUP BY edge_type",
            RETIRED_TYPES,
        ).fetchall()
        unabsorbed = con.execute(_ABSORPTION_SQL).fetchone()[0]
        incidences = con.execute(
            "SELECT COUNT(*) FROM hyper_incidences hi "
            "JOIN hyper_edges he ON he.id = hi.edge_id "
            "WHERE he.edge_type = 'sector'"
        ).fetchone()[0]
        gain = con.execute(
            """
            SELECT COUNT(*) FROM hyper_incidences hi
            JOIN hyper_edges he ON he.id = hi.edge_id
            WHERE he.edge_type = 'sector'
              AND NOT EXISTS (
                SELECT 1 FROM graph_edges g
                WHERE g.edge_type = 'part_of'
                  AND g.source = hi.entity_name
                  AND g.target = he.label
              )
            """
        ).fetchone()[0]
        return {
            "part_of_rows": dict(pair).get("part_of", 0),
            "has_company_rows": dict(pair).get("has_company", 0),
            "unabsorbed_part_of": unabsorbed,
            "sector_incidences": incidences,
            "incidences_without_dyad": gain,
        }
    finally:
        con.close()


def retire(db_path: str | Path = DEFAULT_DB_PATH, *, apply: bool = False) -> dict[str, int]:
    """Delete the pair after re-verifying absorption; returns the measurement."""
    stats = measure(db_path)
    total = stats["part_of_rows"] + stats["has_company_rows"]
    print(
        f"part_of={stats['part_of_rows']} has_company={stats['has_company_rows']} "
        f"(total {total}) | sector incidences={stats['sector_incidences']} | "
        f"unabsorbed part_of={stats['unabsorbed_part_of']} | "
        f"incidences without a dyad={stats['incidences_without_dyad']} (information gain)"
    )
    if stats["unabsorbed_part_of"]:
        print(
            f"REFUSED: {stats['unabsorbed_part_of']} part_of row(s) have no sector "
            "incidence — retiring them would destroy membership the hyper store "
            "cannot reproduce. Re-derive hyperedges first.",
            file=sys.stderr,
        )
        return stats
    if not total:
        print("nothing to retire — the pair is already gone")
        return stats
    if not apply:
        print("[dry-run] nothing written — pass --apply to delete the pair")
        return stats
    con = connect(db_path)
    try:
        with con:
            cur = con.execute("DELETE FROM graph_edges WHERE edge_type IN (?, ?)", RETIRED_TYPES)
            print(f"deleted {cur.rowcount} dyad row(s) in one transaction")
        remaining = con.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type IN (?, ?)", RETIRED_TYPES
        ).fetchone()[0]
        assert remaining == 0, f"retirement incomplete: {remaining} row(s) remain"  # noqa: S101
        print("verified: 0 part_of/has_company rows remain; incidences untouched")
        return stats
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="research.db path")
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = parser.parse_args(argv)
    retire(args.db, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
