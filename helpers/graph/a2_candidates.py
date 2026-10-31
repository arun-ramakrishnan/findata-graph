#!/usr/bin/env python3
"""S4 — A² candidate kernel over the supplier incidence matrix.

S4 (doc/improvements/proposals/scipy_csgraph_scale_lanes.md):
  Surface: B·Bᵀ over the supplier incidence matrix.
  Today: 347,302 candidate pairs in 5.7 ms.
  Output: top-K candidate pairs by shared-consumer count with shared set sizes.
  Boundary: the kernel ships with counts only. Routing candidates into
  compete_with/jv_with derivation stays behind the algorithms_assessment.md
  §6 human-triage throughput gate (H1 backlog).

House pattern: dry-run by default, no --apply (relational readout).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
DEFAULT_DB_PATH = _REPO_ROOT / "memory" / "research.db"

from helpers.core.db import connect  # noqa: E402

INDEX_NOISE = frozenset({"listed_on_index"})


def load_supplier_incidence(db_path=str(DEFAULT_DB_PATH)):
    """Return dict with suppliers, consumers, and the n×m 0-1 CSR matrix
    where n=suppliers, m=consumers, built from graph_edges edge_type='supplier_to'
    (ex-index, self-loops dropped, parallel edges collapsed to first)."""
    con = connect(str(db_path), read_only=True, row_factory=None)
    try:
        rows = con.execute(
            "SELECT source, target FROM graph_edges WHERE edge_type = 'supplier_to' AND source != target"
        ).fetchall()
    finally:
        con.close()
    # build name lists (deterministic sorted order)
    suppliers = sorted({s for s, _ in rows})
    consumers = sorted({t for _, t in rows})
    n, m = len(suppliers), len(consumers)
    col_map = {name: j for j, name in enumerate(consumers)}
    # build sparse matrix of 0/1 (shared consumer = 1 in incidence)
    # row = supplier index, col = consumer index
    pairs = set()
    for s, t in rows:
        if t in col_map:
            i = suppliers.index(s)
            j = col_map[t]
            pairs.add((i, j))
    row = np.array([r for r, _ in pairs], dtype=np.int64)
    col = np.array([c for _, c in pairs], dtype=np.int64)
    data = np.ones(len(pairs), dtype=np.int8)
    mat = csr_matrix((data, (row, col)), shape=(n, m))
    return {
        "suppliers": suppliers,
        "consumers": consumers,
        "matrix": mat,
        "n_suppliers": n,
        "n_consumers": m,
    }


def a2_kernel(supplier_incidence, top_k=10):
    """Compute B·Bᵀ over the supplier incidence matrix.

    Entry (i,j) = number of consumers shared by supplier i and supplier j.
    The diagonal entries are each supplier's degree (consumer count).

    Returns top-K pairs by shared count (excluding self-pairs i==j).
    """
    mat = supplier_incidence["matrix"]  # n × m sparse 0-1
    n = mat.shape[0]
    # compute shared count matrix (i,j) = |consumers(i) ∩ consumers(j)|
    shared = (mat @ mat.transpose()).toarray().astype(np.int64)
    # zero out diagonal (self-shared = degree, not a candidate pair)
    np.fill_diagonal(shared, 0)
    # find top-K entries (i<j pairs with shared > 0)
    results = []
    for i in range(n):
        for j in range(i + 1, n):
            val = int(shared[i, j])
            if val > 0:
                results.append(
                    (
                        val,
                        supplier_incidence["suppliers"][i],
                        supplier_incidence["suppliers"][j],
                    )
                )
    results.sort(key=lambda x: x[0], reverse=True)
    return results[:top_k]


def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(description="S4 A² candidate kernel (dry-run)")
    p.add_argument("--top", type=int, default=10, help="top candidate pairs to show")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    args = p.parse_args(argv)
    inc = load_supplier_incidence(args.db)
    top = a2_kernel(inc, top_k=args.top)
    print(f"suppliers={inc['n_suppliers']} consumers={inc['n_consumers']}")
    print(f"top {len(top)} candidate pairs by shared consumer count:")
    for shared, a, b in top:
        print(f"  {shared:5d}  {a}\n       {b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
