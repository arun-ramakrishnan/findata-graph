#!/usr/bin/env python3
"""Derive the ``org_membership`` hyperedge family (person_ontology_spine S3).

ORG Membership/Role activation, scoped the only way the data allows: the
287 person→company ``invested_in`` edges the SHP lane
(``helpers/maintenance/shareholding_sync.py``) already holds become
role-typed membership rows in the incidence store:

- one ``org_membership`` hyperedge per company (the label IS the org);
- one ``hyper_incidences`` row per person member, carrying the v11
  n-ary facets — ``role`` (``promoter`` | ``holder``), ``weight``
  (the stake percent from the filing), ``valid_from`` (filing period);
- ``hyper_edges.properties.n_members`` kept honest.

Role derivation is a per-row property read, NOT an inference:
``properties.category`` containing ``promoter`` (case-insensitive) →
``promoter``, any other category → ``holder``. Measured live
2026-10-19: 205 promoter / 82 holder rows over 277 persons and 34
companies, every row carrying both a category and a valid_from.

Why a hyperedge family and not a new table: the incidence store IS the
n-ary convention (v11 added role/valid_from/valid_to on
``hyper_incidences`` for exactly this shape), with idempotency keys and
member-count upkeep already built. The ``invested_in`` dyads STAY — they
are the economic edge analytics degree counts on, and the ingest-side
truth this derive reads; the family is the ORG-Membership convention
view, same coexistence as theme dyads + theme hyperedges.

Convergence: this lane OWNS the ``org_membership`` family outright, so
``--apply`` replaces its incidences wholesale and drops memberless
hyperedges — shrinkage converges, unlike the additive derive_hyperedges
backfill whose upstream is shared. ``graph_edges`` UNIQUE(source, target,
edge_type) means one row per (person, company) — filings collapse at
ingest — but the collect pass still folds by (company, person), latest
``valid_from`` then ``source_ref``, so a pre-UNIQUE restore shape cannot
double-count a member.

Provenance: source_ref ``derive:org_membership`` maps to the
``derive_org_membership`` agent via ``backfill_row_provenance`` (the
writer stamps rows itself; the converger only catches restores).
Dry-run is the default; ``--apply`` writes inside one transaction.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.db import DEFAULT_DB_PATH, connect  # noqa: E402

MEMBERSHIP_HYPER_TYPE = "org_membership"
SOURCE_REF = "derive:org_membership"
AGENT_ID = "derive_org_membership"
SOURCE_TIER = "derive"

#: The ORG-Membership role vocabulary this lane writes. ``promoter`` is a
#: filing fact (category says so); ``holder`` is every other named row.
MEMBERSHIP_ROLES: tuple[str, ...] = ("promoter", "holder")

_LATEST_MEMBER_SQL = """
SELECT source, target, properties, valid_from
FROM graph_edges
WHERE edge_type = 'invested_in'
  AND source IN (SELECT name FROM entities WHERE entity_type = 'person')
  AND target IN (SELECT name FROM entities WHERE entity_type = 'company')
ORDER BY target, source, valid_from, source_ref
"""


def _role_for_category(category: str | None) -> str:
    return "promoter" if category and "promoter" in category.lower() else "holder"


def collect(conn) -> dict[str, dict[str, dict]]:
    """Person members per company, latest filing per (company, person).

    Returns ``{company: {person: {role, weight, valid_from}}}`` — the
    dry-run report and the apply input are the same shape.
    """
    latest: dict[tuple[str, str], dict] = {}
    for source, target, props_json, valid_from in conn.execute(_LATEST_MEMBER_SQL):
        try:
            props = jsonio.loads(props_json) if props_json else {}
        except TypeError, ValueError:
            props = {}
        latest[(target, source)] = {
            "role": _role_for_category(props.get("category")),
            "weight": props.get("stake_pct"),
            "valid_from": valid_from,
        }
    members: dict[str, dict[str, dict]] = {}
    for (label, person), facet in latest.items():
        members.setdefault(label, {})[person] = facet
    return members


def apply_derivation(conn, members: dict[str, dict[str, dict]], *, dry_run: bool = True) -> dict:
    """Replace the family's rows with ``members``; return the change stats."""
    existing = {
        label: eid
        for eid, label in conn.execute(
            "SELECT id, label FROM hyper_edges WHERE edge_type = ?",
            (MEMBERSHIP_HYPER_TYPE,),
        )
    }
    new_edges = sum(1 for label in members if label not in existing)
    dropped_edges = sorted(set(existing) - set(members))
    new_inc = sum(
        1
        for label, mems in members.items()
        for person in mems
        if label not in existing
        or conn.execute(
            "SELECT 1 FROM hyper_incidences WHERE edge_id = ? AND entity_name = ?",
            (existing[label], person),
        ).fetchone()
        is None
    )
    stats = {
        "companies": len(members),
        "members": sum(len(m) for m in members.values()),
        "new_edges": new_edges,
        "dropped_edges": dropped_edges,
        "new_incidences": new_inc,
    }
    if dry_run:
        return stats
    with conn:
        for label in dropped_edges:
            conn.execute(
                "DELETE FROM hyper_edges WHERE edge_type = ? AND label = ?",
                (MEMBERSHIP_HYPER_TYPE, label),
            )  # incidences follow by ON DELETE CASCADE
        for label, mems in sorted(members.items()):
            props_json = json.dumps({"n_members": len(mems)}, sort_keys=True)
            if label in existing:
                edge_id = existing[label]
                conn.execute(
                    "UPDATE hyper_edges SET properties = ?, source_ref = ?, agent_id = ?, "
                    "source_tier = ? WHERE id = ?",
                    (props_json, SOURCE_REF, AGENT_ID, SOURCE_TIER, edge_id),
                )
                conn.execute("DELETE FROM hyper_incidences WHERE edge_id = ?", (edge_id,))
            else:
                cur = conn.execute(
                    "INSERT INTO hyper_edges (edge_type, label, properties, source_ref, "
                    "agent_id, source_tier) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        MEMBERSHIP_HYPER_TYPE,
                        label,
                        props_json,
                        SOURCE_REF,
                        AGENT_ID,
                        SOURCE_TIER,
                    ),
                )
                edge_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO hyper_incidences "
                "(edge_id, entity_name, weight, role, valid_from) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        edge_id,
                        person,
                        facet["weight"],
                        facet["role"],
                        facet["valid_from"],
                    )
                    for person, facet in sorted(mems.items())
                ],
            )
    return stats


def derive(db_path: str | Path = DEFAULT_DB_PATH, *, apply: bool = False) -> dict:
    """Census + apply in one call; the printed report is the sync artifact."""
    conn = connect(db_path)
    try:
        members = collect(conn)
        stats = apply_derivation(conn, members, dry_run=not apply)
        roles = {}
        for mems in members.values():
            for facet in mems.values():
                roles[facet["role"]] = roles.get(facet["role"], 0) + 1
        k_counts: dict[int, int] = {}
        for mems in members.values():
            k_counts[len(mems)] = k_counts.get(len(mems), 0) + 1
        print(
            f"org_membership: companies={stats['companies']} "
            f"members={stats['members']} roles={dict(sorted(roles.items()))}"
        )
        print(f"  k distribution: {dict(sorted(k_counts.items()))}")
        print(
            f"  {'[dry-run]' if not apply else '[apply]'} "
            f"new_edges={stats['new_edges']} dropped_edges={len(stats['dropped_edges'])} "
            f"new_incidences={stats['new_incidences']}"
        )
        return stats
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="research.db path")
    parser.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    args = parser.parse_args(argv)
    derive(args.db, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
