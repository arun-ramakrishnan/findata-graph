#!/usr/bin/env python3
"""PSU/state-enterprise pass over the same_group dyad store (read-only).

The family/MNC seed lanes deliberately excluded state-owned-enterprise
(SOE) clusters from the ``vigil:rpt:<SYMBOL>`` filer labels (v6/v7 blob
triage verdict "PSU/STATE — never seed"), but SOE identity is itself
queryable information. This pass catalogues it:

- for every known PSU/state filer symbol it pulls the company-company
  members of its ``vigil:rpt:<SYMBOL>`` same_group label (same membership
  rule as the family/MNC lanes: ``entity_type='company'`` only),
- profiles each (size, sectors, internal density, subsidiary_of texture,
  central-vs-state classification, borderline flags),
- writes a report + JSON manifest for later curation.

Read-only: no writes to any store. Exit 0 always (advisory lane). No
seeds, no gate pins.

Usage:
    python3 helpers/maintenance/psu_dyad_pass.py
    python3 helpers/maintenance/psu_dyad_pass.py --json bench_data/psu_dyad_pass/report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core import db  # noqa: E402
from helpers.maintenance.group_discovery_inventory import load_family_seeds  # noqa: E402
from helpers.maintenance.mnc_dyad_pass import (  # noqa: E402
    _members_for_filer,
    _component_ids,
)

# PSU/state filer symbols (exact, uppercase) -> ministry/group + level.
# Curated from the v6/v7 blob triage verdicts (completed.md #514 §4) —
# the PSU/STATE clusters and their borderline cases. ``level`` is
# "central" (Government of India), "state" (state government), or
# "borderline" (JV/SPV/subsidiary texture — profiled, never asserted as a
# plain PSU tag).
PSU_PARENTS: dict[str, dict[str, str]] = {
    # State Bank group
    "SBIN": {"ministry": "State Bank group", "level": "central"},
    "SBICARD": {"ministry": "State Bank group (subsidiary)", "level": "borderline"},
    "SBILIFE": {"ministry": "State Bank group (subsidiary)", "level": "central"},
    # Power PSUs
    "NHPC": {"ministry": "Power PSUs", "level": "central"},
    "NTPC": {"ministry": "Power PSUs", "level": "central"},
    "NTPCGREEN": {"ministry": "Power PSUs (green SPV)", "level": "borderline"},
    "PFC": {"ministry": "Power Finance", "level": "central"},
    "POWERGRID": {"ministry": "Power PSUs", "level": "central"},
    "PTC": {"ministry": "Power trading", "level": "central"},
    "IREDA": {"ministry": "Renewable Energy", "level": "central"},
    # Railways
    "RVNL": {"ministry": "Railways", "level": "central"},
    "IRCON": {"ministry": "Railways", "level": "central"},
    "IRCTC": {"ministry": "Railways", "level": "central"},
    "RITES": {"ministry": "Railways", "level": "central"},
    "RAILTEL": {"ministry": "Railways", "level": "central"},
    # Insurance / banking
    "LICI": {"ministry": "LIC / insurance", "level": "central"},
    "LICHSGFIN": {"ministry": "LIC Housing Finance", "level": "central"},
    "NIACL": {"ministry": "LIC / insurance", "level": "central"},
    "IDBI": {"ministry": "IDBI Bank", "level": "central"},
    # Metals / coal / minerals
    "COALINDIA": {"ministry": "Coal India", "level": "central"},
    "NMDC": {"ministry": "NMDC", "level": "central"},
    "NATIONALUM": {"ministry": "National Aluminium (NALCO)", "level": "central"},
    "NSLNISP": {"ministry": "NMDC Steel (SPV)", "level": "borderline"},
    # Petroleum
    "ONGC": {"ministry": "Petroleum", "level": "central"},
    # Logistics / ports (borderline JV texture)
    "CONCOR": {"ministry": "Container Corporation of India", "level": "central"},
    "GPPL": {"ministry": "Port JV (APM Terminals)", "level": "borderline"},
}

_FILER_PREFIX = "vigil:rpt:"


def _profile(conn, symbol: str, families: dict[str, set[str]]) -> dict | None:
    members = _members_for_filer(conn, symbol)
    if not members:
        return None
    n = len(members)

    sectors = Counter()
    fam_hits: set[str] = set()
    fam_labels: set[str] = set()
    for row in conn.execute(
        "SELECT name, sector_classification FROM entities WHERE name IN (SELECT unnest(?))",
        [members],
    ).fetchall():
        if row[1]:
            sectors[row[1]] += 1
        for lab in families.get(row[0], ()):
            fam_hits.add(row[0])
            fam_labels.add(lab)

    inner = conn.execute(
        """
        SELECT COUNT(*) FROM graph_edges e
        WHERE e.edge_type = 'same_group'
          AND e.source IN (SELECT unnest(?)) AND e.target IN (SELECT unnest(?))
        """,
        [members, members],
    ).fetchone()[0]

    sub = conn.execute(
        """
        SELECT COUNT(*) FROM graph_edges e
        WHERE e.edge_type = 'subsidiary_of'
          AND e.source IN (SELECT unnest(?)) AND e.target IN (SELECT unnest(?))
        """,
        [members, members],
    ).fetchone()[0]

    return {
        "symbol": symbol,
        "ministry": PSU_PARENTS[symbol]["ministry"],
        "level": PSU_PARENTS[symbol]["level"],
        "members": members,
        "size": n,
        "internal_dyads": inner,
        "density": round(2 * inner / (n * (n - 1)), 4) if n > 1 else 0.0,
        "subsidiary_of_internal": sub,
        "sectors": sectors.most_common(3),
        "family_hit_members": sorted(fam_hits),
        "family_hit_count": len(fam_hits),
        "seeded_families": sorted(fam_labels),
        "component_ids": _component_ids(conn, symbol),
    }


def run_pass(conn) -> tuple[list[dict], list[str]]:
    """Return (profiles for matched PSU filers, unmatched/uncharted symbols)."""
    families = load_family_seeds()
    dyads = conn.execute(
        "SELECT DISTINCT source_ref FROM graph_edges WHERE source_ref LIKE ?",
        [f"{_FILER_PREFIX}%"],
    ).fetchall()
    observed = {r[0][len(_FILER_PREFIX) :] for r in dyads}
    profiles = []
    uncharted = []
    for symbol, meta in PSU_PARENTS.items():
        if symbol not in observed:
            uncharted.append(symbol)
            continue
        profile = _profile(conn, symbol, families)
        if profile:
            profiles.append(profile)
    profiles.sort(key=lambda p: p["size"], reverse=True)
    return profiles, uncharted


def _render(profiles, uncharted) -> str:
    lines = [
        "# PSU/state-enterprise pass (SOE filer labels in the same_group dyad store)",
        "",
        f"{len(profiles)} PSU/state filer label(s) matched with members; "
        f"{len(uncharted)} table symbols absent from the store.",
        "",
        "| symbol | ministry/group | level | size | dyads | dens | sub | fam hits |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in profiles:
        lines.append(
            f"| {p['symbol']} | {p['ministry']} | {p['level']} | {p['size']} | "
            f"{p['internal_dyads']} | {p['density']} | {p['subsidiary_of_internal']} | "
            f"{p['family_hit_count']} |"
        )
    return "\n".join(lines)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--db",
        default=None,
        help=(
            "research store; default = the canonical store via helpers.core.db "
            "(memory/research.duckdb, FINDATA_RESEARCH_DB_PATH honoured)"
        ),
    )
    ap.add_argument("--json", default=None, help="write full profiles JSON")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else None
    if db_path is not None and not db_path.is_absolute():
        db_path = _PROJECT_ROOT / db_path
    conn = db.connect(db_path, read_only=True)
    try:
        profiles, uncharted = run_pass(conn)
    finally:
        conn.close()

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"profiles": profiles, "uncharted": uncharted},
                indent=2,
            )
            + "\n"
        )
        print(f"wrote {out}")
    print(_render(profiles, uncharted))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
