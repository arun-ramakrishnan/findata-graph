#!/usr/bin/env python3
"""MNC-arm pass over the same_group dyad store (read-only).

The dyad store holds related-party-disclosure group edges (``vigil:rpt:<SYMBOL>``
filer labels). Many of those filers are **arms of foreign multinationals**
(MNC subsidiaries listed in India) — their RPT disclosures enumerate their
global parent networks (e.g. ABB India disclosing ABB Switzerland). Those
components were excluded from the Indian-promoter family seed lane (v6/v7
triage verdict "MNC-ARM — foreign parent cluster, decide if we want them"),
but were never systematically catalogued. This pass does exactly that:

- for every known MNC filer symbol it pulls the company-company members of
  its ``vigil:rpt:<SYMBOL>`` same_group label (same membership rule used by
  the family-seed lane: ``entity_type='company'`` only),
- profiles each (size, sectors, internal density, subsidiary_of texture,
  overlap with seeded families, sibling-label bridges),
- writes a report + JSON manifest for later curation (e.g. a future
  ``mnc:`` label lane — **not** a family seed: these are foreign parents,
  not Indian promoter families).

Read-only: no writes to any store. Exit 0 always (advisory lane).

Usage:
    python3 helpers/maintenance/mnc_dyad_pass.py
    python3 helpers/maintenance/mnc_dyad_pass.py --json bench_data/mnc_dyad_pass/report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.group_discovery_inventory import load_family_seeds  # noqa: E402

# MNC filer symbols (exact, uppercase) -> foreign parent + country.
# Only *foreign-parent* arms belong here; Indian-promoter filers (Wipro,
# Mahindra, JSW, ...) are deliberately absent. Curated from the v6/v7 blob
# triage + standard listed-MNC arms.
MNC_PARENTS: dict[str, dict[str, str]] = {
    "POWERINDIA": {"parent": "Hitachi Energy", "country": "JP"},
    "BOSCHLTD": {"parent": "Robert Bosch GmbH", "country": "DE"},
    "BOSCH-HCIL": {"parent": "Bosch Home Comfort", "country": "DE"},
    "CRISIL": {"parent": "S&P Global", "country": "US"},
    "CIEINDIA": {"parent": "CIE Automotive", "country": "ES"},
    "MARUTI": {"parent": "Suzuki Motor Corp", "country": "JP"},
    "SCHNEIDER": {"parent": "Schneider Electric", "country": "FR"},
    "ABB": {"parent": "ABB Ltd", "country": "CH"},
    "HONAUT": {"parent": "Honeywell Automation", "country": "US"},
    "SIEMENS": {"parent": "Siemens AG", "country": "DE"},
    "BASF": {"parent": "BASF SE", "country": "DE"},
    "AKZOINDIA": {"parent": "AkzoNobel", "country": "NL"},
    "LINDEINDIA": {"parent": "Linde plc", "country": "US"},
    "3MINDIA": {"parent": "3M", "country": "US"},
    "ZFCV": {"parent": "ZF Group", "country": "DE"},
    "KSB": {"parent": "KSB SE", "country": "DE"},
    "SCHAEFFLER": {"parent": "Schaeffler AG", "country": "DE"},
    "VESUVIUS": {"parent": "Vesuvius plc", "country": "UK"},
    "CUMMINSIND": {"parent": "Cummins Inc", "country": "US"},
    "NESTLEIND": {"parent": "Nestlé", "country": "CH"},
    "GLAXO": {"parent": "GSK plc", "country": "UK"},
    "SANOFI": {"parent": "Sanofi", "country": "FR"},
    "PFIZER": {"parent": "Pfizer Inc", "country": "US"},
    "ASTRAZENEC": {"parent": "AstraZeneca plc", "country": "UK"},
    "NOVARTIS": {"parent": "Novartis AG", "country": "CH"},
    "HINDUNILVR": {"parent": "Unilever plc", "country": "UK"},
    "COLPAL": {"parent": "Colgate-Palmolive", "country": "US"},
    "ABBOTINDIA": {"parent": "Abbott Laboratories", "country": "US"},
    "TIMKEN": {"parent": "The Timken Company", "country": "US"},
}

_FILER_PREFIX = "vigil:rpt:"


def _members_for_filer(conn, symbol: str) -> list[str]:
    """Companies under one vigil:rpt:<SYMBOL> same_group label (company-only)."""
    rows = conn.execute(
        """
        SELECT DISTINCT e.source AS a, e.target AS b
        FROM graph_edges e
        JOIN entities a ON a.name = e.source
        JOIN entities b ON b.name = e.target
        WHERE e.edge_type = 'same_group'
          AND e.source_ref = ?
          AND a.entity_type = 'company' AND b.entity_type = 'company'
        """,
        [f"{_FILER_PREFIX}{symbol}"],
    ).fetchall()
    members = set()
    for s, t in rows:
        members.add(s)
        members.add(t)
    return sorted(members)


def _component_ids(conn, symbol: str) -> list[int]:
    """Weak-component ids containing any member of a filer label (via dyads)."""
    members = set(_members_for_filer(conn, symbol))
    if not members:
        return []
    dyads = conn.execute(
        """
        SELECT e.source, e.target FROM graph_edges e
        JOIN entities a ON a.name = e.source
        JOIN entities b ON b.name = e.target
        WHERE e.edge_type = 'same_group'
          AND a.entity_type = 'company' AND b.entity_type = 'company'
        """
    ).fetchall()
    adj: dict[str, set[str]] = defaultdict(set)
    for s, t in dyads:
        adj[s].add(t)
        adj[t].add(s)
    seen: set[str] = set()
    comp_of: dict[str, int] = {}
    cid = 0
    for node in adj:
        if node in seen:
            continue
        stack = [node]
        seen.add(node)
        comp = []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        for u in comp:
            comp_of[u] = cid
        cid += 1
    return sorted({comp_of[m] for m in members if m in comp_of})


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

    # sibling-label bridges: other filer labels sharing >=1 member
    bridge_rows = conn.execute(
        """
        SELECT he.label, COUNT(DISTINCT m.entity_name)
        FROM hyper_edges he
        JOIN hyper_incidences m ON m.edge_id = he.id
        WHERE he.edge_type = 'group'
          AND he.label LIKE 'vigil:rpt:%'
          AND m.entity_name IN (SELECT unnest(?))
          AND m.entity_name NOT IN (SELECT unnest(?))
        GROUP BY he.label ORDER BY 2 DESC LIMIT 5
        """,
        [members, members],
    ).fetchall()

    return {
        "symbol": symbol,
        "parent": MNC_PARENTS[symbol]["parent"],
        "country": MNC_PARENTS[symbol]["country"],
        "members": members,
        "size": n,
        "internal_dyads": inner,
        "density": round(2 * inner / (n * (n - 1)), 4) if n > 1 else 0.0,
        "subsidiary_of_internal": sub,
        "sectors": sectors.most_common(3),
        "family_hit_members": sorted(fam_hits),
        "family_hit_count": len(fam_hits),
        "seeded_families": sorted(fam_labels),
        "bridge_labels": bridge_rows,
        "component_ids": _component_ids(conn, symbol),
    }


def run_pass(conn) -> tuple[list[dict], list[str]]:
    """Return (profiles for matched MNC filers, unmatched/uncharted symbols)."""
    families = load_family_seeds()
    dyads = conn.execute(
        "SELECT DISTINCT source_ref FROM graph_edges WHERE source_ref LIKE ?",
        [f"{_FILER_PREFIX}%"],
    ).fetchall()
    observed = {r[0][len(_FILER_PREFIX) :] for r in dyads}
    profiles = []
    uncharted = []
    for symbol, meta in MNC_PARENTS.items():
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
        "# MNC-arm pass (foreign-parent filer labels in the same_group dyad store)",
        "",
        f"{len(profiles)} MNC filer label(s) matched with members; "
        f"{len(uncharted)} table symbols absent from the store.",
        "",
        "| symbol | parent | co | size | dyads | dens | sub | fam hits | seeded families |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in profiles:
        seeded = ",".join(p["seeded_families"]) if p["seeded_families"] else "—"
        lines.append(
            f"| {p['symbol']} | {p['parent']} | {p['country']} | {p['size']} | "
            f"{p['internal_dyads']} | {p['density']} | {p['subsidiary_of_internal']} | "
            f"{p['family_hit_count']} | {seeded} |"
        )
    return "\n".join(lines)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="memory/research.db")
    ap.add_argument("--json", default=None, help="write full profiles JSON")
    args = ap.parse_args(argv)

    import duckdb

    conn = duckdb.connect(args.db, **memory_cap_config())
    profiles, uncharted = run_pass(conn)
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
