#!/usr/bin/env python3
"""S2/S3 — Supplier bipartite matching and assignment feasibility.

S2: maximum bipartite matching over `supplier_to` as 0-1 incidence matrix
(suppliers × consumers). Reports matching size + coverage % + Hall-violator
report (deficient consumer subsets).

S3: min-weight full bipartite matching feasibility checker. Reports max-matching
size, deficient sets, and whether a full assignment is admissible.
Proposal: S3 ships this feasibility checker (no lane itself — the lane
`assign-primary` is conditional on a future projection with full coverage).

House pattern: dry-run by default, no --apply.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import (
    maximum_bipartite_matching,
    min_weight_full_bipartite_matching,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
DEFAULT_DB_PATH = _REPO_ROOT / "memory" / "research.db"

from helpers.core.db import connect  # noqa: E402

INDEX_NOISE = frozenset({"listed_on_index"})


def load_supplier_incidence(db_path=str(DEFAULT_DB_PATH)):
    """Return (suppliers, consumers, mat, pos) where mat is a 0-1 CSR
    ``suppliers × consumers`` matrix."""
    con = connect(str(db_path), read_only=True, row_factory=None)
    try:
        rows = con.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type = 'supplier_to'"
        ).fetchall()
    finally:
        con.close()
    kept = [(s, t) for s, t in rows if s != t]
    suppliers = sorted({s for s, _ in kept})
    consumers = sorted({t for _, t in kept})
    n_supp = len(suppliers)
    n_cons = len(consumers)
    col_map = {name: j for j, name in enumerate(consumers)}
    # build 0-1 entries: row=supplier index, col=consumer index
    pairs = set()
    for s, t in kept:
        if t in col_map:  # target (consumer) must be in col_map
            i = suppliers.index(s)
            j = col_map[t]
            pairs.add((i, j))
    row = np.array([r for r, _ in pairs], dtype=np.int64)
    col = np.array([c for _, c in pairs], dtype=np.int64)
    data = np.ones(len(pairs), dtype=np.int8)
    mat = csr_matrix((data, (row, col)), shape=(n_supp, n_cons))
    return {
        "suppliers": suppliers,
        "consumers": consumers,
        "matrix": mat,
        "n_suppliers": n_supp,
        "n_consumers": n_cons,
    }


def s2_matching(supplier_incidence):
    """S2: maximum bipartite matching over supplier_to incidence.

    Returns dict with matching size, coverage %, Hall violators, and
    overlap report.
    """
    mat = supplier_incidence["matrix"]
    n, m = mat.shape
    col_assignment = maximum_bipartite_matching(mat, perm_type="row")
    matched = int((col_assignment >= 0).sum())
    coverage = round(matched / m * 100.0, 2) if m > 0 else 0.0
    unmatched = [j for j in range(m) if col_assignment[j] < 0]
    # degree per consumer
    deg = np.array(mat.sum(axis=0)).flatten().tolist()
    zero_deg = [j for j, d in enumerate(deg) if d == 0]
    return {
        "matching_size": matched,
        "coverage_pct": coverage,
        "unmatched_consumers": unmatched,
        "zero_degree_consumers": zero_deg,
        "n_suppliers": n,
        "n_consumers": m,
    }


def s3_feasibility(supplier_incidence, *, allow_sector_cuts=False):
    """S3: feasibility of min-weight full bipartite matching.

    Reports max-matching size, whether a full matching exists covering all
    investors (rows), and deficient sets (e.g. zero-degree consumers).

    The lane `assign-primary` is conditional on a future projection with
    full coverage; currently stays parked.
    """
    mat = supplier_incidence["matrix"].astype(float)
    n, m = mat.shape
    matching_size = len(maximum_bipartite_matching(mat, perm_type="row"))
    full = matching_size == n
    zero_cols = [j for j in range(m) if np.isclose(mat[:, j].sum(), 0)]
    return {
        "full_matching": bool(full),
        "max_matching_size": matching_size,
        "n_investors": n,
        "n_targets": m,
        "admissible": bool(full) and allow_sector_cuts,
        "deficient_sets": {
            "zero_degree_consumers": len(zero_cols),
        },
        "reason": None if full else f"Hall collisions: max matching {matching_size}/{n}",
    }


def load_investment_incidence(db_path=str(DEFAULT_DB_PATH)):
    """``investors × targets`` stake-weighted CSR over ``invested_in``.

    The S3b assignment projection: rows = investors (edge source), cols =
    targets (edge target), entries = stake weights (COALESCE(weight, 1.0)
    — the trial census found 800 rows, 277×84, 100% weighted). One edge
    per (investor, target): later duplicates fold into the first.
    """
    con = connect(str(db_path), read_only=True, row_factory=None)
    try:
        rows = con.execute(
            "SELECT source, target, COALESCE(weight, 1.0) FROM graph_edges "
            "WHERE edge_type = 'invested_in'"
        ).fetchall()
    finally:
        con.close()
    kept: dict[tuple[str, str], float] = {}
    for s, t, w in rows:
        if s == t:
            continue
        kept.setdefault((s, t), float(w))
    investors = sorted({s for s, _ in kept})
    targets = sorted({t for _, t in kept})
    inv_map = {name: i for i, name in enumerate(investors)}
    tgt_map = {name: j for j, name in enumerate(targets)}
    row = np.array([inv_map[s] for s, _ in kept], dtype=np.int64)
    col = np.array([tgt_map[t] for _, t in kept], dtype=np.int64)
    data = np.array([w for w in kept.values()], dtype=np.float64)
    mat = csr_matrix((data, (row, col)), shape=(len(investors), len(targets)))
    return {
        "investors": investors,
        "targets": targets,
        "matrix": mat,
        "n_investors": len(investors),
        "n_targets": len(targets),
    }


def s3_assign_primary(incidence, *, max_exclusions: int = 20):
    """S3b: min-stake primary-investor assignment on a COVERING projection.

    The full assignment is infeasible on ``invested_in`` as-is (Hall
    collisions: targets sharing too few investors). Construction: take
    the column-side maximum matching — unmatched targets are the Hall
    violators — exclude them (each exclusion is REPORTED with its name:
    the Hall set is an output, not silent surgery), and repeat until
    ``min_weight_full_bipartite_matching`` is admissible on the covering
    subset (nested deficiencies need the loop; hard-capped).

    The lane orientation is the transposed one from the proposal: every
    kept target is assigned its MINIMUM-stake investor. Returns
    assignment rows (target, investor, stake), the excluded set, and the
    projection stats. Read-only over the incidence dict; writes nothing.
    """
    mat = incidence["matrix"].astype(float).tolil()
    names_t = list(incidence["targets"])
    names_i = list(incidence["investors"])
    excluded: list[dict[str, str]] = []
    for _ in range(max_exclusions):
        sub = mat.tocsr()
        # perm_type="column" maximizes column coverage: the returned array
        # is row-indexed (arr[i] = column matched to row i, -1 unmatched);
        # Hall violators are the columns absent from it.
        col_match = maximum_bipartite_matching(sub, perm_type="column")
        matched_cols = {int(c) for c in col_match if c >= 0}
        dropped = [j for j in range(sub.shape[1]) if j not in matched_cols]
        if not dropped:
            break
        for j in dropped:
            excluded.append(
                {
                    "target": names_t[j],
                    "reason": "Hall violation — no full assignment possible "
                    "with this target present",
                }
            )
        mat = mat[:, [j for j in range(mat.shape[1]) if j not in set(dropped)]]
        names_t = [n for j, n in enumerate(names_t) if j not in set(dropped)]
    else:
        return {"admissible": False, "reason": "exclusion cap hit", "excluded": excluded}

    sub = mat.tocsr()
    if sub.shape[1] == 0:
        return {"admissible": False, "reason": "empty covering set", "excluded": excluded}
    try:
        col_assign = min_weight_full_bipartite_matching(sub.T, maximize=False)
    except ValueError as exc:
        return {
            "admissible": False,
            "reason": f"covering construction failed: {exc}",
            "excluded": excluded,
        }
    assignment = []
    t_idx, i_idx = col_assign
    for tj, ii in zip(t_idx, i_idx):
        assignment.append(
            {
                "target": names_t[int(tj)],
                "investor": names_i[int(ii)],
                "stake": float(sub[int(ii), int(tj)]),
            }
        )
    assignment.sort(key=lambda r: (-r["stake"], r["target"]))
    return {
        "admissible": True,
        "assignment": assignment,
        "excluded": excluded,
        "n_assigned": len(assignment),
        "n_excluded": len(excluded),
        "n_investors": len(names_i),
        "n_targets": len(names_t),
    }


def load_target_sectors(db_path=str(DEFAULT_DB_PATH)) -> dict[str, str]:
    """``target name → sector_classification`` for classified entities.

    The per-sector cut's partition key: only classified targets enter a
    sector sub-problem; unclassified targets are skipped by the cut and
    REPORTED (proposal §1 — reported, not failed).
    """
    con = connect(str(db_path), read_only=True, row_factory=None)
    try:
        rows = con.execute(
            "SELECT name, sector_classification FROM entities "
            "WHERE sector_classification IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {name: sec for name, sec in rows if sec}


def s3_assign_primary_sector(
    incidence, sectors: dict[str, str] | None = None, *, max_exclusions: int = 20
) -> dict:
    """S3 unlock (s3_sector_unlock proposal §1): per-sector cuts.

    Partition ``invested_in`` targets by ``sector_classification`` and run
    ``s3_assign_primary`` within each sector's subgraph independently —
    a sector cut is a COVERING projection because each sub-matrix is
    small enough that Hall collisions disappear (live trial 2026-10-16:
    raw 82/84 with 2 Hall violators → cuts 84/84, zero exclusions).

    Targets without a sector are skipped by the cut and reported.
    Investors keep their own sector but match in the TARGET's sector
    subgraph — an investor may appear in several cuts, at most once per
    cut (matching property); a target lives in exactly one cut, so the
    union never double-assigns. Read-only; writes nothing.
    """
    sectors = sectors or {}
    names_t = list(incidence["targets"])
    names_i = list(incidence["investors"])
    coo = incidence["matrix"].tocoo()
    triples = [
        (names_i[int(i)], names_t[int(j)], float(w)) for i, j, w in zip(coo.row, coo.col, coo.data)
    ]
    by_sector: dict[str, list[str]] = {}
    unclassified: list[str] = []
    for t in names_t:
        sec = sectors.get(t)
        if sec is None:
            unclassified.append(t)
        else:
            by_sector.setdefault(sec, []).append(t)

    sectors_out: dict[str, dict] = {}
    total_assigned = total_excluded = 0
    for sec in sorted(by_sector):
        tgts = sorted(by_sector[sec])
        tgt_set = set(tgts)
        invs = sorted({s for s, t, _ in triples if t in tgt_set})
        inv_ix = {n: i for i, n in enumerate(invs)}
        tgt_ix = {n: j for j, n in enumerate(tgts)}
        tri = [(s, t, w) for s, t, w in triples if t in tgt_set]
        rows = np.array([inv_ix[s] for s, _, _ in tri], dtype=np.int64)
        cols = np.array([tgt_ix[t] for _, t, _ in tri], dtype=np.int64)
        data = np.array([w for _, _, w in tri], dtype=np.float64)
        sub = csr_matrix((data, (rows, cols)), shape=(len(invs), len(tgts)))
        res = s3_assign_primary(
            {
                "investors": invs,
                "targets": tgts,
                "matrix": sub,
                "n_investors": len(invs),
                "n_targets": len(tgts),
            },
            max_exclusions=max_exclusions,
        )
        for row in res.get("excluded", []):
            row["sector"] = sec
        res["sector"] = sec
        sectors_out[sec] = res
        total_assigned += res.get("n_assigned", 0)
        total_excluded += res.get("n_excluded", 0)

    n_classified = sum(len(v) for v in by_sector.values())
    return {
        "admissible": all(r.get("admissible") for r in sectors_out.values()),
        "sectors": sectors_out,
        "totals": {
            "n_targets": len(names_t),
            "n_classified": n_classified,
            "n_unclassified": len(unclassified),
            "n_assigned": total_assigned,
            "n_excluded": total_excluded,
            "n_sectors": len(sectors_out),
        },
        "unclassified_targets": sorted(unclassified),
    }


def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(description="S2/S3 supplier matching & feasibility")
    sub = p.add_subparsers(dest="command")

    s2p = sub.add_parser("match-suppliers", help="maximum bipartite matching over supplier_to")
    s2p.add_argument("--top-deficits", type=int, default=5, help="top deficient subsets to show")
    s2p.add_argument("--db", default=str(DEFAULT_DB_PATH))

    s3p = sub.add_parser(
        "assign-feasibility", help="min-weight full bipartite matching feasibility"
    )
    s3p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    s3p.add_argument(
        "--sector-cuts", action="store_true", help="allow sector-based unlock condition"
    )

    a3p = sub.add_parser(
        "assign-primary",
        help="S3b: min-stake primary-investor assignment on the covering projection",
    )
    a3p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    a3p.add_argument("--max-exclusions", type=int, default=20)

    sp = sub.add_parser(
        "assign-primary-sector",
        help="S3 unlock: per-sector-cut min-stake assignment (read-only)",
    )
    sp.add_argument("--db", default=str(DEFAULT_DB_PATH))
    sp.add_argument("--max-exclusions", type=int, default=20)
    sp.add_argument("--format", choices=["text", "json"], default="text")

    args = p.parse_args(argv)
    inc = load_supplier_incidence(args.db)

    if args.command == "match-suppliers":
        res = s2_matching(inc)
        print(f"suppliers={res['n_suppliers']} consumers={res['n_consumers']}")
        print(f"matching_size={res['matching_size']} coverage={res['coverage_pct']}%")
        print(
            f"unmatched={len(res['unmatched_consumers'])} zero_degree={len(res['zero_degree_consumers'])}"
        )
        print("Done.")
    elif args.command == "assign-primary":
        inv = load_investment_incidence(args.db)
        res = s3_assign_primary(inv, max_exclusions=args.max_exclusions)
        print(
            f"investors={res.get('n_investors', inv['n_investors'])} "
            f"targets={res.get('n_targets', inv['n_targets'])}"
        )
        if not res["admissible"]:
            print(f"NOT ADMISSIBLE: {res['reason']}")
        else:
            print(f"assigned={res['n_assigned']} excluded={res['n_excluded']}")
            for row in res["excluded"]:
                print(f"  excluded: {row['target']} ({row['reason']})")
            for row in res["assignment"][:10]:
                print(f"  {row['target']} -> {row['investor']} (stake {row['stake']:.2f})")
            if res["n_assigned"] > 10:
                print(f"  … +{res['n_assigned'] - 10} more")
        print("Done.")
    elif args.command == "assign-primary-sector":
        inv = load_investment_incidence(args.db)
        sectors = load_target_sectors(args.db)
        res = s3_assign_primary_sector(inv, sectors, max_exclusions=args.max_exclusions)
        if args.format == "json":
            print(json.dumps(res, sort_keys=True, indent=2))
        else:
            t = res["totals"]
            print(
                f"targets={t['n_targets']} classified={t['n_classified']} sectors={t['n_sectors']}"
            )
            print(
                f"assigned={t['n_assigned']} excluded={t['n_excluded']} "
                f"unclassified={t['n_unclassified']}"
            )
            for sec in sorted(res["sectors"]):
                r = res["sectors"][sec]
                print(
                    f"  [{sec}] investors={r.get('n_investors', 0)} "
                    f"targets={r.get('n_targets', 0)} assigned={r.get('n_assigned', 0)} "
                    f"excluded={r.get('n_excluded', 0)}"
                )
                for row in r.get("excluded", []):
                    print(f"    excluded: {row['target']} ({row['reason']})")
            if res["unclassified_targets"]:
                print(f"  unclassified (skipped): {', '.join(res['unclassified_targets'])}")
        print("Done.")
    elif args.command == "assign-feasibility":
        res = s3_feasibility(inc, allow_sector_cuts=args.sector_cuts)
        print(f"investors={res['n_investors']} targets={res['n_targets']}")
        print(f"max_matching_size={res['max_matching_size']} full_matching={res['full_matching']}")
        print(f"admissible={res['admissible']}")
        if res["reason"]:
            print(f"reason: {res['reason']}")
        if res["deficient_sets"]:
            for k, v in res["deficient_sets"].items():
                print(f"  deficient ({k}): {v}")
        print("Done.")
    else:
        p.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
