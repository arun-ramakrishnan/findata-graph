#!/usr/bin/env python3
"""JV census over same_group labels + jv_with edges (read-only, advisory).

The family/MNC lanes deliberately excluded foreign-partner joint ventures
(e.g. Wilmar JV: AWL + RENUKA) from seeding. This census sizes the JV
population and resolves partner pairs:

- **name-pattern scan** over same_group label members: names matching
  JV shapes ("Joint Venture", "JV", "& Co", partnership/consortium
  language, foreign-partner JV subsidiaries),
- **jv_with cross-ref**: live ``jv_with`` edges from ``graph_edges``
  (edge_type='jv_with') are resolved to entity pairs; junctions with the
  name-pattern hits are flagged,
- **report** lists JV-shaped entities with partner-resolution status.

Advisory only: no writes, no seeds, no gate pins. Exit 0 always.

Usage:
    python3 helpers/maintenance/jv_dyad_census.py
    python3 helpers/maintenance/jv_dyad_census.py --json bench_data/jv_dyad_census/report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core import db  # noqa: E402
from helpers.maintenance.mnc_dyad_pass import _members_for_filer  # noqa: E402

_FILER_PREFIX = "vigil:rpt:"

# Name shapes that signal a joint-venture/partnership entity.
_JV_PATTERNS = [
    re.compile(r"joint venture", re.I),
    re.compile(r"\bJV\b", re.I),
    re.compile(r"\bJV\b", re.I),
    re.compile(r"partnership", re.I),
    re.compile(r"consortium", re.I),
    re.compile(r"& (?:co|company|partners)", re.I),
    re.compile(r"and (?:co|company|partners)", re.I),
    re.compile(r"energy (?:private|co|partners)", re.I),
    re.compile(r"power (?:private|co|partners)", re.I),
]

# Well-known foreign-partner JV anchors (from triage) — cross-ref'd.
_JV_ANCHORS = ["AWL", "RENUKA"]


def _is_jv_name(name: str) -> bool:
    return any(p.search(name) for p in _JV_PATTERNS)


def _jv_with_edges(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT source_ref, source, target
        FROM graph_edges
        WHERE edge_type = 'jv_with'
        ORDER BY source_ref
        """
    ).fetchall()
    return [{"source_ref": r[0], "source": r[1], "target": r[2]} for r in rows]


def run_census(conn) -> dict:
    """Return census dict: name-hits, jv_with edges, anchors, pairs."""
    # 1) name-pattern hits inside same_group labels
    labels = conn.execute(
        "SELECT DISTINCT source_ref FROM graph_edges WHERE source_ref LIKE ?",
        [f"{_FILER_PREFIX}%"],
    ).fetchall()
    name_hits: dict[str, list[str]] = {}
    hit_labels: Counter = Counter()
    for (ref,) in labels:
        sym = ref[len(_FILER_PREFIX) :]
        members = _members_for_filer(conn, sym)
        for m in members:
            if _is_jv_name(m):
                name_hits.setdefault(m, []).append(sym)
                hit_labels[sym] += 1

    # 2) live jv_with edges
    jv_edges = _jv_with_edges(conn)

    # 3) anchor cross-ref: AWL / RENUKA filer membership
    anchors = {}
    for a in _JV_ANCHORS:
        anchors[a] = _members_for_filer(conn, a)

    # 4) partner pairs: name-hit members that appear on both sides of a
    #    jv_with edge or inside an anchor label.
    hit_set = set(name_hits)
    pairs = []
    for e in jv_edges:
        s, t = e["source"], e["target"]
        flags = []
        if s in hit_set:
            flags.append("name-hit")
        if t in hit_set:
            flags.append("name-hit")
        if flags:
            pairs.append({**e, "flags": sorted(set(flags))})

    return {
        "name_hits": {k: sorted(v) for k, v in name_hits.items()},
        "hit_labels_total": sum(hit_labels.values()),
        "jv_with_edges_total": len(jv_edges),
        "anchors": {a: sorted(v) for a, v in anchors.items()},
        "pairs": pairs,
    }


def _render(c: dict) -> str:
    lines = [
        "# JV census (same_group name-pattern hits + jv_with cross-ref)",
        "",
        f"{len(c['name_hits'])} entity name(s) hit JV/partnership patterns across "
        f"{c['hit_labels_total']} label-memberships.",
        f"{c['jv_with_edges_total']} live jv_with edge(s) in graph_edges.",
        "Anchors: " + ", ".join(f"{a}={len(v)}" for a, v in c["anchors"].items()),
        "",
        "## JV-partner pairs (both sides resolved)",
        "",
        "| source_ref | source | target | flags |",
        "|---|---|---|---|",
    ]
    for p in c["pairs"][:40]:
        lines.append(
            f"| {p['source_ref']} | {p['source']} | {p['target']} | {','.join(p['flags'])} |"
        )
    lines += ["", "## Name-pattern hits (top 25 by label spread)", ""]
    for name, labels in sorted(c["name_hits"].items(), key=lambda kv: (-len(kv[1]), kv[0]))[:25]:
        lines.append(f"- {name} — {len(labels)} label(s): {', '.join(labels[:5])}")
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
    ap.add_argument("--json", default=None, help="write full census JSON")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else None
    if db_path is not None and not db_path.is_absolute():
        db_path = _PROJECT_ROOT / db_path
    conn = db.connect(db_path, read_only=True)
    try:
        census = run_census(conn)
    finally:
        conn.close()

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(census, indent=2) + "\n")
        print(f"wrote {out}")
    print(_render(census))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
