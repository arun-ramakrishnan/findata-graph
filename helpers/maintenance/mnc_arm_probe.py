#!/usr/bin/env python3
"""Residual MNC arms probe (read-only, advisory).

S3 of group_dyad_wave2.md: probe the four MNC arms absent from the
dyad-pass (ZFCV, ASTRAZENEC, NOVARTIS, ABBOTINDIA) for alternate
evidence — subsidiary_of edges, same_group dyads, hyperedge membership,
embedding peers — so the proposal can verdict each arm (seed-with-gate
vs documented absence).

Exit 0 always; writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.mnc_dyad_pass import _members_for_filer  # noqa: E402

# Residual arms (absent from dyad pass) → canonical Indian-listed entity
# name fragment (the master stores full names, not tickers).
_MISSING_ARMS = {
    "ZFCV": "ZF Commercial Vehicle Control Systems India",
    "ASTRAZENEC": "AstraZeneca Pharma India",
    "NOVARTIS": "Novartis India",
    "ABBOTINDIA": "Abbott India",
}
_FILER_PREFIX = "vigil:rpt:"


def _entity_row(conn, ticker: str):
    """Resolve a ticker to entity name via canonical fragment.

    The four arms exist in master under full names (e.g. "AstraZeneca
    Pharma India") but have no ticker identifier rows — prefer the
    canonical fragment so the probe examines the Indian-listed arm,
    not the foreign parent.
    """
    frag = _MISSING_ARMS[ticker]
    rows = conn.execute(
        """
        SELECT name FROM entities
        WHERE LOWER(name) = LOWER(?)
          AND entity_type = 'company'
        LIMIT 1
        """,
        [frag],
    ).fetchall()
    if rows:
        return rows[0][0]
    rows = conn.execute(
        """
        SELECT name FROM entities
        WHERE LOWER(name) LIKE ?
          AND entity_type = 'company'
        ORDER BY name
        LIMIT 1
        """,
        [f"%{frag.lower()}%"],
    ).fetchall()
    return rows[0][0] if rows else None


def probe_arm(conn, ticker: str) -> dict:
    name = _entity_row(conn, ticker)
    arm = {"ticker": ticker, "entity": name}
    if not name:
        arm["verdict"] = "absent-from-master"
        return arm

    # 1) subsidiary_of edges (either direction)
    subs = conn.execute(
        """
        SELECT source, target, source_ref
        FROM graph_edges
        WHERE edge_type = 'subsidiary_of'
          AND (source = ? OR target = ?)
        """,
        [name, name],
    ).fetchall()
    arm["subsidiary_of_edges"] = [
        {"source": r[0], "target": r[1], "source_ref": r[2]} for r in subs
    ]

    # 2) same_group dyads (direct membership)
    arm["same_group_members"] = _members_for_filer(conn, ticker)

    # 3) hyperedge membership (non-group hyperedges: family, mnc, event)
    hy = conn.execute(
        """
        SELECT DISTINCT he.edge_type, he.label
        FROM hyper_incidences hi
        JOIN hyper_edges he ON he.id = hi.edge_id
        WHERE hi.entity_name = ?
        """,
        [name],
    ).fetchall()
    arm["hyperedges"] = [{"edge_type": r[0], "label": r[1]} for r in hy]

    # 4) embedding peers (top-5 by cosine over company_embeddings)
    #     embeddings are BLOB float32 ×512; DuckDB cannot cast BLOB→
    #     FLOAT[512], so decode via struct and score in Python (4 arms
    #     only — bounded work).
    import struct

    me_row = conn.execute(
        "SELECT embedding FROM company_embeddings WHERE company_name = ?",
        [name],
    ).fetchone()
    peers: list[dict] = []
    if me_row and me_row[0] is not None:
        me_vec = struct.unpack("<512f", me_row[0])
        me_norm = sum(v * v for v in me_vec) ** 0.5
        all_rows = conn.execute(
            "SELECT company_name, embedding FROM company_embeddings WHERE company_name != ?",
            [name],
        ).fetchall()
        scored = []
        for other, blob in all_rows:
            if blob is None:
                continue
            o_vec = struct.unpack("<512f", blob)
            dot = sum(a * b for a, b in zip(me_vec, o_vec))
            o_norm = sum(v * v for v in o_vec) ** 0.5
            if me_norm and o_norm:
                scored.append((dot / (me_norm * o_norm), other))
        scored.sort(key=lambda x: -x[0])
        peers = [{"name": o, "sim": round(s, 3)} for s, o in scored[:5]]
    arm["embedding_peers"] = peers

    arm["verdict"] = (
        "evidence"
        if arm["same_group_members"] or arm["subsidiary_of_edges"]
        else "no-dyad-evidence"
    )
    return arm


def run_probe(conn) -> list[dict]:
    return [probe_arm(conn, t) for t in _MISSING_ARMS]


def _render(arms: list[dict]) -> str:
    lines = ["# Residual MNC arms probe (S3)", ""]
    for a in arms:
        lines.append(f"## {a['ticker']} → {a['entity'] or 'ABSENT'} ({a['verdict']})")
        if not a.get("entity"):
            continue
        lines.append(f"- subsidiary_of: {len(a['subsidiary_of_edges'])}")
        lines.append(f"- same_group members: {len(a['same_group_members'])}")
        lines.append(
            "- hyperedges: " + ", ".join(f"{h['edge_type']}:{h['label']}" for h in a["hyperedges"])
            or "none"
        )
        lines.append(
            "- embedding peers: "
            + ", ".join(f"{p['name']} {p['sim']}" for p in a["embedding_peers"])
        )
        lines.append("")
    return "\n".join(lines)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="memory/research.db")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    import duckdb

    conn = duckdb.connect(args.db, **memory_cap_config())
    arms = run_probe(conn)
    conn.close()

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(arms, indent=2) + "\n")
        print(f"wrote {out}")
    print(_render(arms))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
