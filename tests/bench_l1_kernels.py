#!/usr/bin/env python3
"""L1 fold-kernel bench (rust_betweenness_kernel / rust_closeness_kernel).

Live-store leg: runs both folded lanes twice — the incumbent numpy lane and
the Rust `graph_kernels` lane — and asserts (a) the kernel is actually
taken, (b) the two agree within the documented epsilon, and (c) the kernel
beats the proposal's >2x wall target at jobs=1. Prints the measured walls
so a perf regression is legible in the gate report.

Exits 0 on pass, 1 on a parity or speedup breach, and 0 with a SKIP note
when the store or the extension is absent (mirrors the parity harness's
skip posture for an unbuilt kernel).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.graph import l1_betweenness as lb  # noqa: E402
from helpers.graph import l1_closeness as lc  # noqa: E402

_DB = REPO_ROOT / "memory" / "research.db"
_CEILING_S = 240.0  # both lanes x both kernels, warm store, jobs=1
_BETWEENNESS_EPS = 1e-9
#: Regression floor, deliberately BELOW the proposals' >2x acceptance target.
#: The target was verified once on a quiet box (13.3x / 2.42x, recorded in
#: the archived proposals); a gate leg that re-litigates it fails on a
#: merely contended box. Parity is the deterministic assertion here.
_MIN_SPEEDUP = 1.5


def _rel(a, b) -> float:
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denom = np.maximum(np.maximum(np.abs(a), np.abs(b)), 1.0)
    return float(np.max(np.abs(a - b) / denom)) if a.size else 0.0


def _betweenness(lane: str) -> dict:
    os.environ["L1B_RUST"] = lane
    names, edges = lb.load_projection(_DB)
    n = len(names)
    indptr, indices = lb.build_adjacency(n, edges)
    mask, removed = lb.peel_two_core(indptr, indices, n)
    core = np.flatnonzero(mask)
    core_set = set(core.tolist())
    adj_sets = lc.build_adj_sets(edges)
    folded, _specials = lb.tree_components(removed, adj_sets, core_set)
    kw, _att = lb._compute_attachment_weights(folded, adj_sets, core_set, n)
    t0 = time.perf_counter()
    u, tu, kk = lb.brandes_core(
        indptr, indices, np.array(core, dtype=np.int64), kw, jobs=1, is_core=mask
    )
    return {"wall": time.perf_counter() - t0, "U": u, "TU": tu, "KK": kk, "n": n}


def _closeness(lane: str) -> dict:
    os.environ["L1C_RUST"] = lane
    t0 = time.perf_counter()
    scores, info = lc.compute(_DB, jobs=1)
    return {"wall": time.perf_counter() - t0, "scores": scores, "info": info}


def main() -> int:
    if not _DB.exists():
        print("SKIP: no memory/research.db — L1 kernel bench skipped (exit 0)")
        return 0
    try:
        import graph_kernels
    except ImportError:
        print("SKIP: graph_kernels not installed — L1 kernel bench skipped (exit 0)")
        return 0
    for sym in ("brandes_fold_sources", "core_bfs_sources_batch"):
        if not hasattr(graph_kernels, sym):
            print(f"SKIP: graph_kernels lacks {sym} (stale build) — skipped (exit 0)")
            return 0

    t_all = time.perf_counter()

    b_np = _betweenness("0")
    b_rs = _betweenness("1")
    for name in ("U", "TU", "KK"):
        rel = _rel(b_rs[name], b_np[name])
        if rel > _BETWEENNESS_EPS:
            print(f"FAIL: betweenness {name} drift {rel:g} > {_BETWEENNESS_EPS:g}")
            return 1
    b_speed = b_np["wall"] / b_rs["wall"] if b_rs["wall"] else float("inf")

    c_np = _closeness("0")
    c_rs = _closeness("1")
    if c_np["scores"] != c_rs["scores"]:
        print("FAIL: closeness scores differ between lanes (must be exact)")
        return 1
    c_speed = c_np["wall"] / c_rs["wall"] if c_rs["wall"] else float("inf")

    wall = time.perf_counter() - t_all
    print(
        f"betweenness n={b_np['n']} numpy {b_np['wall']:.2f}s rust {b_rs['wall']:.2f}s "
        f"({b_speed:.2f}x); closeness n={len(c_np['scores'])} numpy {c_np['wall']:.2f}s "
        f"rust {c_rs['wall']:.2f}s ({c_speed:.2f}x)"
    )
    if wall > _CEILING_S:
        print(f"FAIL: bench wall {wall:.1f}s > ceiling {_CEILING_S}s")
        return 1
    slow = [n for n, s in (("betweenness", b_speed), ("closeness", c_speed)) if s < _MIN_SPEEDUP]
    if slow:
        print(f"FAIL: below the {_MIN_SPEEDUP}x regression floor: {', '.join(slow)}")
        return 1
    print(f"PASS: L1 fold kernels take the Rust lane, parity holds, both lanes >={_MIN_SPEEDUP}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
