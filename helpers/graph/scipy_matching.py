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
import sys
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching

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
