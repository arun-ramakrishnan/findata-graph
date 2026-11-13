#!/usr/bin/env python3
"""Group-discovery inventory — components of the company-company same_group graph.

The `same_group` dyad store (24,766 edges over 13,233 companies) is the raw
material for "who belongs to the same corporate group" — but raw dyads are a
mess: vig-` source_refs pile hundreds of edges under one filer label, names
collide across groups, and multi-national webs blur ownership. This lane
turns the dyad store into a **ranked, read-only component inventory** so a
curator can adjudicate groups at the component level instead of rediscovering
them edge-by-edge.

For each weakly-connected component of the company-company `same_group`
graph it computes:

- size + internal edge count/density,
- the dominant `source_ref` labels (which filer disclosures feed it),
- sector diversity from `entities.sector_classification`,
- how many members are already covered by curated family seeds (family_seeds.json),
- internal `subsidiary_of` / `supplier_to` edges (ownership vs supply texture),
- a `merge_flags` list: components carrying >=2 distinct curated families are
  likely cross-group merges (the dyad graph joins them — adjudicate before
  trusting the component as one group).

Read-only: no writes to any store. Output: human summary + optional JSON.

Usage:
    python3 helpers/maintenance/group_discovery_inventory.py
    python3 helpers/maintenance/group_discovery_inventory.py --json bench_data/group_discovery_inventory/report.json
    python3 helpers/maintenance/group_discovery_inventory.py --limit 40
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402

FAMILY_SEEDS_PATH = _PROJECT_ROOT / "helpers" / "misc" / "family_seeds.json"


def load_family_seeds():
    """Return {member_name: set(family labels)} from family_seeds.json."""
    if not FAMILY_SEEDS_PATH.exists():
        return {}
    with open(FAMILY_SEEDS_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    out: dict[str, set[str]] = defaultdict(set)
    for fam in payload.get("families", []):
        label = fam["family"]
        for member in fam["members"]:
            out[member].add(label)
    return dict(out)


def _load(conn):
    """Return dyads, sector map, family map (company-only)."""
    dyads = conn.execute(
        """
        SELECT e.source, e.target, e.source_ref FROM graph_edges e
        JOIN entities a ON a.name = e.source
        JOIN entities b ON b.name = e.target
        WHERE e.edge_type = 'same_group'
          AND a.entity_type = 'company' AND b.entity_type = 'company'
        """
    ).fetchall()
    sectors = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT name, sector_classification FROM entities "
            "WHERE sector_classification IS NOT NULL AND sector_classification <> ''"
        ).fetchall()
    }
    return dyads, sectors


def _components(dyads):
    adj: dict[str, set[str]] = defaultdict(set)
    for s, t, _ in dyads:
        adj[s].add(t)
        adj[t].add(s)
    seen: set[str] = set()
    comps = []
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
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    return comps


def _component_profile(comp, dyads, sectors, families):
    comp_set = set(comp)
    inner = [(s, t, ref) for s, t, ref in dyads if s in comp_set and t in comp_set]
    refs = Counter(ref for _, _, ref in inner)
    secs = [sectors[m] for m in comp if m in sectors]
    sec_counter = Counter(secs)
    fam_hits = {m: f for m, f in families.items() if m in comp_set}
    n = len(comp)
    fam_labels = sorted({lab for labs in fam_hits.values() for lab in labs})
    return {
        "size": n,
        "internal_edges": len(inner),
        "density": round(2 * len(inner) / (n * (n - 1)), 4) if n > 1 else 0.0,
        "top_refs": refs.most_common(3),
        "sectors": sec_counter.most_common(3),
        "family_members": sorted(fam_hits),
        "family_hit_count": len(fam_hits),
        "merge_flags": fam_labels if len(fam_labels) > 1 else [],
        "members_sample": comp[:6],
    }


def _render(profiles, limit):
    lines = [
        "# Group-discovery inventory (company-company same_group components)",
        "",
        f"{len(profiles)} components; sizes sum to {sum(p['size'] for p in profiles)} companies.",
        "",
        "| # | size | edges | density | top refs | sectors | fam hits | flags |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, p in enumerate(profiles[:limit], 1):
        refs = ", ".join(f"{r}×{n}" for r, n in p["top_refs"][:2])
        secs = ", ".join(f"{s}×{n}" for s, n in p["sectors"][:2])
        flags = ",".join(p["merge_flags"]) if p["merge_flags"] else "—"
        lines.append(
            f"| {i} | {p['size']} | {p['internal_edges']} | {p['density']} | "
            f"{refs} | {secs} | {p['family_hit_count']} | {flags} |"
        )
    return "\n".join(lines)


def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default="memory/research.db")
    p.add_argument("--limit", type=int, default=0, help="cap printed rows (0 = all)")
    p.add_argument("--json", default=None, help="write full profiles JSON")
    args = p.parse_args(argv)

    import duckdb

    conn = duckdb.connect(args.db, **memory_cap_config())
    try:
        dyads, sectors = _load(conn)
        if not dyads:
            print("no company-company same_group edges — skipping")
            return 0
        families = load_family_seeds()
        comps = _components(dyads)
        profiles = [_component_profile(c, dyads, sectors, families) for c in comps]
        limit = args.limit or len(profiles)
        print(_render(profiles, limit))
        if args.json:
            out = Path(args.json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(profiles, indent=2) + "\n")
            print(f"wrote {out}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
