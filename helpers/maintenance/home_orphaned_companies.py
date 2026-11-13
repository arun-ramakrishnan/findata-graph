#!/usr/bin/env python3
"""Home orphaned companies into sector hyperedges (gate remediation).

The integrity gate (helpers/misc/database_integrity_check.py, check
``check_orphan_companies``) counts a company as orphaned when it has no
``sector`` hyperedge membership, no ticker/file home, and no relations edge
(VIGIL exemption). The census on 2026-11-13 found 17 premium rows:

* 12 real companies that were never assigned a sector (parse_newsletter
  gaps; several are Tata-family members like Tata 1mg / Tata AutoComp that
  carry a family seed but no sector incidence);
* 5 individuals (EGF board/director names) mis-stored as ``company`` and
  only homed by ``jv:`` event hyperedges — they are persons, not companies.

This helper remediates both classes idempotently:

* sectors: INSERT OR IGNORE a ``sector`` hyperedge per company (label must
  exist in the taxonomy) + one incidence each;
* persons: ``UPDATE entities SET entity_type='person'`` for the exact
  five rows (no other table depends on their type).

Read-only by default; ``--apply`` writes. Idempotent — safe to re-run.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import sys
from pathlib import Path
from sqlite3 import Connection

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402

REPO_ROOT = _REPO_ROOT
DEFAULT_DB = REPO_ROOT / "memory" / "research.db"

# Companies that are real legal entities but lacked a sector home. Sector
# must be one of the 42 existing ``sector`` hyperedge labels.
SECTOR_HOMES: dict[str, str] = {
    "K.T.V. Health Food": "FMCG",
    "Firefly Networks": "Technology",
    "Central UP Gas": "Energy",
    "Indianoil - Adani Gas": "Energy",
    "EcoLife GT Mobility": "Automotive",
    "Jio BLAST eSports": "Media_Entertainment",
    "Tata 1mg Technologies": "Healthcare",
    "Tata 1mg Healthcare Solutions": "Healthcare",
    "Tata Technologies GmbH": "Automotive",
    "Tata AutoComp Systems": "Automotive",
    "MahaOnline": "Technology",
    "Universal Voltas L.L.C": "Consumer",
}

# Individuals mis-stored as companies (event-anchored via ``jv:`` hyperedge
# labels). Re-typed to ``person`` — their true entity class.
PERSON_RETYPES: tuple[str, ...] = (
    "G Sekhar - Non Executive Director - EGF",
    "Vigyat Singh - Non Executive Director - EGF",
    "Achyut Dhadphale",
    "Ajay Deshpande",
    "Akshay Dhar",
)

SOURCE_REF = "orphan_homing:2026-11-13"


def _connect(db: Path):
    """Open research.db with FK enforcement (helpers.core.db.connect)."""
    return connect(db_path=db, enable_fk=True)


def _existing_sector_labels(conn: Connection) -> set[str]:
    return {
        r["label"]
        for r in conn.execute("SELECT label FROM hyper_edges WHERE edge_type='sector'").fetchall()
    }


def _plan(conn: Connection) -> tuple[list[str], list[str]]:
    """Return (missing sectors, companies with no incidence yet)."""
    sector_labels = _existing_sector_labels(conn)
    missing = sorted({s for s in SECTOR_HOMES.values() if s not in sector_labels})
    to_add = []
    for company in sorted(SECTOR_HOMES):
        exists = conn.execute(
            "SELECT COUNT(*) FROM hyper_incidences hi "
            "JOIN hyper_edges he ON he.id = hi.edge_id "
            "WHERE he.edge_type='sector' AND hi.entity_name=?",
            (company,),
        ).fetchone()[0]
        if not exists:
            to_add.append(company)
    return missing, to_add


def apply(db: Path) -> dict:
    conn = _connect(db)
    missing, to_add = _plan(conn)
    if missing:
        raise SystemExit(f"fatal: sector labels not in taxonomy: {missing}")

    now = datetime.now(UTC).isoformat()
    inserted_edges = 0
    inserted_incidences = 0
    for company in to_add:
        sector = SECTOR_HOMES[company]
        cur = conn.execute(
            "INSERT OR IGNORE INTO hyper_edges "
            "(edge_type, label, weight, source_ref, properties, created_at) "
            "VALUES ('sector', ?, 1.0, ?, ?, ?)",
            (sector, SOURCE_REF, json.dumps({"n_members": 0}), now),
        )
        if cur.rowcount:
            inserted_edges += 1
        edge_id = conn.execute(
            "SELECT id FROM hyper_edges WHERE edge_type='sector' AND label=?",
            (sector,),
        ).fetchone()["id"]
        cur = conn.execute(
            "INSERT OR IGNORE INTO hyper_incidences (edge_id, entity_name) VALUES (?, ?)",
            (edge_id, company),
        )
        if cur.rowcount:
            inserted_incidences += 1

    retyped = 0
    for name in PERSON_RETYPES:
        cur = conn.execute(
            "UPDATE entities SET entity_type='person' WHERE name=? AND entity_type='company'",
            (name,),
        )
        if cur.rowcount:
            retyped += 1

    conn.commit()
    conn.close()
    return {
        "companies_homed": len(to_add),
        "sector_edges_inserted": inserted_edges,
        "sector_incidences_inserted": inserted_incidences,
        "persons_retyped": retyped,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="write changes")
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    missing, to_add = _plan(conn)
    conn.close()
    print(f"companies needing sector home: {len(to_add)}")
    if missing:
        print(f"fatal: unknown sector labels: {missing}")
        return 1
    if not args.apply:
        print("dry-run (--apply to write)")
        return 0
    result = apply(args.db)
    for k, v in result.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
