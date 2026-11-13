#!/usr/bin/env python3
"""Hyperedge co-occurrence lens — read-only inspection of hypergraph structure.

The hypergraph stores label-scoped membership sets (``hyper_edges`` +
``hyper_incidences``). Per-label membership alone hides the cross-label
structure that carries the real signal — which companies bridge two labels,
how far labels overlap, and where memberships cluster. This tool renders
that structure:

- pairwise label co-occurrence per edge type (shared-member overlap +
  Jaccard), ranked;
- hub members: entities appearing in the most labels of a type (bridge
  entities that glue unrelated groups together);
- a group-type "family lens": the top overlapping label pairs, directly
  comparable to the dyad-component view (group_discovery_inventory.py).

Optionally, ``--types`` restricts to specific edge types (default: all
types with >= 2 labels). Output is a markdown report and/or ``--json`` file.
Read-only: no store writes.

Example::

    .venv/bin/python3 helpers/maintenance/hyperedge_cooccurrence_lens.py \\
        --types group --limit 15 --json bench_data/hyperedge_cooccurrence/group.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402

DEFAULT_DB = REPO_ROOT / "memory" / "research.db"


def load_labels(conn, types: tuple[str, ...] | None = None) -> dict[str, dict[str, set[str]]]:
    """Return ``{edge_type: {label: set(members)}}`` from the store."""
    rows = conn.execute(
        """
        SELECT he.edge_type, he.label, hi.entity_name
        FROM hyper_edges he
        JOIN hyper_incidences hi ON hi.edge_id = he.id
        ORDER BY he.edge_type, he.label, hi.entity_name
        """
    ).fetchall()
    out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for etype, label, member in rows:
        if types and etype not in types:
            continue
        out[etype][label].add(member)
    return {k: dict(v) for k, v in out.items()}


def cooccurrence_pairs(labels: dict[str, set[str]], min_overlap: int = 2) -> list[dict]:
    """Ranked pairwise overlap between all label pairs of one edge type."""
    names = sorted(labels)
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            inter = labels[a] & labels[b]
            if len(inter) < min_overlap:
                continue
            union = labels[a] | labels[b]
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "overlap": len(inter),
                    "jaccard": round(len(inter) / len(union), 4),
                    "size_a": len(labels[a]),
                    "size_b": len(labels[b]),
                }
            )
    pairs.sort(key=lambda p: (-float(p["overlap"]), -float(p["jaccard"])))
    return pairs


def hub_members(labels: dict[str, set[str]], min_labels: int = 2) -> list[dict]:
    """Entities appearing in the most labels of one type (bridge entities)."""
    count: dict[str, int] = defaultdict(int)
    for members in labels.values():
        for m in members:
            count[m] += 1
    hubs = [{"member": m, "labels": n} for m, n in count.items() if n >= min_labels]
    hubs.sort(key=lambda h: -int(h["labels"]))
    return hubs


def render_report(
    labels: dict[str, dict[str, set[str]]],
    limit: int = 12,
    min_overlap: int = 2,
) -> str:
    lines = ["# Hyperedge co-occurrence lens", ""]
    for etype, lab in sorted(labels.items()):
        n_lab = len(lab)
        n_mem = len(set().union(*lab.values())) if lab else 0
        lines.append(f"## {etype} ({n_lab} labels, {n_mem} unique members)")
        pairs = cooccurrence_pairs(lab, min_overlap)
        if not pairs:
            lines.append("_no overlapping label pairs_\n")
            continue
        lines.append("| rank | label A | label B | overlap | jaccard | A | B |")
        lines.append("|---|---|---|---|---|---|---|")
        for rank, p in enumerate(pairs[:limit], 1):
            lines.append(
                f"| {rank} | {p['a']} | {p['b']} | {p['overlap']} | "
                f"{p['jaccard']} | {p['size_a']} | {p['size_b']} |"
            )
        hubs = hub_members(lab, min_labels=3)
        if hubs:
            lines.append("")
            lines.append("Top bridge members (appear in most labels):")
            lines.append(", ".join(f"{h['member']} ({h['labels']})" for h in hubs[:8]))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--types", nargs="+", default=None, help="edge types (default: all)")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--min-overlap", type=int, default=2)
    ap.add_argument("--json", dest="json_out", metavar="FILE", help="write JSON dump")
    args = ap.parse_args()

    import duckdb

    conn = duckdb.connect(args.db, **memory_cap_config())
    try:
        labels = load_labels(conn, tuple(args.types) if args.types else None)
    finally:
        conn.close()

    if not labels:
        print("no labels found", file=sys.stderr)
        return 1

    print(render_report(labels, args.limit, args.min_overlap))

    if args.json_out:
        dump = {
            "types": {
                t: {
                    "labels": sorted(v),
                    "pairs": cooccurrence_pairs(v, args.min_overlap)[: args.limit],
                    "hubs": hub_members(v)[: args.limit],
                }
                for t, v in labels.items()
            }
        }
        Path(args.json_out).write_text(json.dumps(dump, indent=2) + "\n")
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
