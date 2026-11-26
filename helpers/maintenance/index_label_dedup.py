#!/usr/bin/env python3
"""Fold duplicate index-entity labels into their canonical name.

The NSE index sidecar carries several names that resolve to the SAME
constituent set (NIFTY 500 vs Nifty500 Multicap 50-25-25 Index vs
Nifty500 LargeMidSmall Equal-Cap Weighted — all 499/499 identical). We
treat them as aliases of one canonical index for graph purposes, so a
query for "NIFTY 500 constituents" returns one clean hyperedge instead
of three identical ones.

SCOPE (operator-approved, 2026-11-13): fold only the NIFTY-500 family
into `NIFTY 500`. EXTENDED (operator-approved, 2026-11-14): also fold
the MidSmallcap-400 and Financial-Services weight-scheme alias pairs
(Nifty MidSmallcap400 50-50 ≡ Nifty MidSmallcap 400 399/399; Nifty
Financial Services 25/50 Index ≡ Nifty Financial Services 20/20 — same
"one index, two weight schemes" class; the scheme nuance survives only
in each edge's index_slug property).

Store contract: reads/writes go through helpers.core.db.connect()
(SQLite, FK on). Idempotent; --apply writes, default dry-run.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402

# alias → canonical (NIFTY-500 family + MidSmallcap-400 + Financial
# Services weight-scheme alias pairs, operator-approved 2026-11-13/14)
INDEX_NAME_CANONICAL: dict[str, str] = {
    "Nifty500 Multicap 50-25-25 Index": "NIFTY 500",
    "Nifty500 LargeMidSmall Equal-Cap Weighted": "NIFTY 500",
    "Nifty MidSmallcap400 50-50": "Nifty MidSmallcap 400",
    "Nifty Financial Services 25/50 Index": "Nifty Financial Services",
}

# measured-but-not-folded duplicates: (a, b, members) — reported only
OTHER_MEASURED_DUPS: list[tuple[str, str, int]] = []


def _fold_pairs() -> set[tuple[str, str]]:
    return {(a, c) for a, c in INDEX_NAME_CANONICAL.items()}


def plan(conn) -> dict:
    """Describe the fold actions without touching the store."""
    pairs = _fold_pairs()
    out: dict = {"pairs": [], "hyperedge_folds": [], "entities_to_drop": []}
    for alias, canonical in sorted(pairs):
        ent_alias = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE name = ?", (alias,)
        ).fetchone()[0]
        ent_canon = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE name = ?", (canonical,)
        ).fetchone()[0]
        out["pairs"].append(
            {
                "alias": alias,
                "canonical": canonical,
                "entity_alias": ent_alias,
                "entity_canonical": ent_canon,
            }
        )
        if ent_alias and ent_canon:
            out["entities_to_drop"].append(alias)
            # hyperedge ids for both labels (index family)
            he = conn.execute(
                "SELECT id, label FROM hyper_edges WHERE label IN (?, ?) AND edge_type = 'index'",
                (alias, canonical),
            ).fetchall()
            by_label = {lb: eid for eid, lb in he}
            if alias in by_label and canonical in by_label:
                out["hyperedge_folds"].append(
                    {"alias_edge_id": by_label[alias], "canonical_edge_id": by_label[canonical]}
                )
    # residual references that must be empty before a drop
    out["residual_refs"] = {}
    for alias, _c in pairs:
        refs = {}
        for table, col in (
            ("graph_edges", "source"),
            ("graph_edges", "target"),
            ("entity_identifiers", "entity_name"),
            ("entity_tags", "entity_name"),
            ("hyper_incidences", "entity_name"),
        ):
            try:
                n = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {col} = ?",  # noqa: S608  # both names are the module's own constant list
                    (alias,),
                ).fetchone()[0]
            except sqlite3.Error:  # table/column may be absent in minimal schemas
                continue
            if n:
                refs[f"{table}.{col}"] = n
        out["residual_refs"][alias] = refs
    return out


def apply_fold(conn) -> dict:
    """Execute the fold. Returns per-alias counts."""
    stats: dict = {"hyperedges_merged": 0, "entities_dropped": 0, "incidences_moved": 0}
    for alias, canonical in sorted(_fold_pairs()):
        # 1) move incidences alias→canonical (INSERT OR IGNORE)
        he = conn.execute(
            "SELECT id, label FROM hyper_edges WHERE label IN (?, ?) AND edge_type = 'index'",
            (alias, canonical),
        ).fetchall()
        by_label = {lb: eid for eid, lb in he}
        if alias in by_label and canonical in by_label:
            alias_id, canon_id = by_label[alias], by_label[canonical]
            cur = conn.execute(
                "INSERT OR IGNORE INTO hyper_incidences (edge_id, entity_name, weight, role) "
                "SELECT ?, entity_name, weight, role FROM hyper_incidences WHERE edge_id = ?",
                (canon_id, alias_id),
            )
            stats["incidences_moved"] += cur.rowcount
            conn.execute("DELETE FROM hyper_incidences WHERE edge_id = ?", (alias_id,))
            conn.execute("DELETE FROM hyper_edges WHERE id = ?", (alias_id,))
            stats["hyperedges_merged"] += 1
        # 2) drop any alias-pinned incidences, then the alias entity. On
        # SQLite the entity FK CASCADE removed them; the canonical DuckDB
        # store has no FK constraints, so delete explicitly (both engines
        # end identical).
        conn.execute("DELETE FROM hyper_incidences WHERE entity_name = ?", (alias,))
        cur = conn.execute("DELETE FROM entities WHERE name = ?", (alias,))
        stats["entities_dropped"] += cur.rowcount
    # closing a sqlite3 connection rolls back an open transaction; persist here so
    # both the CLI and library callers keep the fold.
    conn.commit()
    return stats


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--db", default=None, help="research store path (default: the live canonical store)"
    )
    ap.add_argument("--apply", action="store_true", help="write changes (default dry-run)")
    args = ap.parse_args(argv)

    conn = connect(args.db, enable_fk=True)
    try:
        p = plan(conn)
        print("fold pairs:")
        for pair in p["pairs"]:
            print(
                f"  {pair['alias']} -> {pair['canonical']} "
                f"(entity {pair['entity_alias']} -> {pair['entity_canonical']})"
            )
        print("hyperedge folds:", len(p["hyperedge_folds"]))
        print("entities to drop:", p["entities_to_drop"])
        print("residual refs (graph_edges/hyper_incidences cascade on entity drop;")
        print("others must be empty before drop):")
        for alias, refs in p["residual_refs"].items():
            print(f"  {alias}: {refs if refs else 'none'}")
        print("OTHER measured duplicates (NOT folded, awaiting operator):")
        for a, b, n in OTHER_MEASURED_DUPS:
            print(f"  {a} ≡ {b} ({n} members)")
        if args.apply:
            stats = apply_fold(conn)
            print("applied:", stats)
        else:
            print("dry-run — pass --apply to write.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
