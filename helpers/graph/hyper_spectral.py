#!/usr/bin/env python3
"""HGX multi-order spectral + RW-density lanes (lanes 3+5 of the HGX unlock
shelf; baselines from the 2026-11-02 re-probe,
doc/improvements/archive/graph/hgx_unlock_shelf_probe.md).

Two read-only lanes over the star-incidence store, per edge-type family:

  spectral-multiorder — per-order Laplacians to max_k (HGX
      laplacian_matrices_all_orders) and the nnz-based commutativity
      verdict. Productionizes the re-probe's inline workaround: HGX
      are_commuting() calls csr_array.any(), absent on this scipy, so
      commutation here is `(A - B)` nnz == 0 on csr. --spectra adds the
      per-order algebraic connectivity (eigsh k=2, opt-in: 50 orders x
      1000-node matrices is the lane's cost).
  rw-density — component annotation first (count, sizes, LC fraction:
      the 2026-11-02 structural finding is that only edition is one
      dominant component — industry/sub_sector/org_membership sit at
      6%/5%/15% LC coverage), then random_walk_density on the largest
      component with a uniform start.

Probes' guard protocol carried over: dedupe hyperedges by sorted member
tuple, max_k <= 60, k>=2 edge count <= 300 (giant-k families stay out).

Usage:
    python3 helpers/graph/hyper_spectral.py                       # both lanes, default families
    python3 helpers/graph/hyper_spectral.py --lane spectral --sources edition --spectra
    python3 helpers/graph/hyper_spectral.py --lane rw --sources industry,sub_sector
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph import hyper_arrow as ha  # noqa: E402
from helpers.graph.hyper_communities import _exclude_degenerate  # noqa: E402

DEFAULT_FAMILIES = ("edition", "industry", "sub_sector", "org_membership")


def _require_hgx():
    """Lazy HGX import (the hyper_centralities.py idiom)."""
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            from hypergraphx import Hypergraph
            from hypergraphx.dynamics.randwalk import random_walk_density
            from hypergraphx.linalg.linalg import laplacian_matrices_all_orders
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            "hypergraphx is not installed in this interpreter "
            "(pyproject dependency since hypergraph_incidence_hyx)."
        ) from exc
    return Hypergraph, laplacian_matrices_all_orders, random_walk_density


def load_family(edge_type: str, *, max_k: int = 60, k2_cap: int = 300) -> dict[str, list[str]]:
    """One family's deduped, guard-filtered incidence (re-probe protocol).

    Dedupe by sorted member tuple (the HGX member-tuple contract), drop
    singletons (0/0 degeneracy), cap k>=2 edges per the probe protocol.
    """
    raw = ha.incidence_dict(ha.load_incidence_arrow([edge_type]))
    dedup: dict[str, list[str]] = {}
    seen: set[tuple] = set()
    for lbl, members in raw.items():
        key = tuple(sorted(members))
        if key in seen:
            continue
        seen.add(key)
        dedup[lbl] = members
    kept, _giants, _singles = _exclude_degenerate(dedup, allow_giant=True)
    k2 = [lbl for lbl, ms in kept.items() if len(ms) >= 2]
    if len(k2) > k2_cap:
        raise ValueError(
            f"{edge_type}: {len(k2)} k>=2 edges exceeds the probe cap {k2_cap} "
            "(giant-k family — pass --k2-cap to override deliberately)"
        )
    return kept


def commute_nnz(A, B, *, tol: float = 1e-9) -> bool:
    """HGX are_commuting() without the csr_array.any() call this scipy lacks.

    The per-order Laplacians are built through different float paths, so
    mathematically-commuting pairs differ only in the last ulp — exact nnz
    equality false-negatives those (measured: sub_sector). Max-abs
    difference against a tolerance instead.
    """
    diff = abs((A - B).tocsr())
    return bool(diff.max() <= tol) if diff.nnz else True


def spectral_multiorder(
    incidence: dict[str, list[str]],
    *,
    max_k: int = 60,
    spectra: bool = False,
) -> dict[str, Any]:
    """Lane 3: per-order Laplacians + consecutive-order commutation verdicts.

    Returns orders found, per-boundary commute booleans, the family verdict
    (consecutive commutation is transitive, so all-boundaries-true means the
    mixed-order operator is order-decomposition-independent), and — with
    spectra=True — the per-order algebraic connectivity.

    HGX order convention: order k covers hyperedges of size k+1 only, and
    the matrix spans every node of the hypergraph (nodes without a size-k+1
    edge are isolated zeros) — so lambda_2 is 0 unless the size-k+1 section
    alone spans the family. Read lambda_2 per order with that in mind.
    """
    Hypergraph, laplacian_matrices_all_orders, _RWD = _require_hgx()

    kept_sets = {k: set(v) for k, v in incidence.items()}
    hg = Hypergraph(edge_list=[tuple(m) for m in kept_sets.values()])
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        warnings.filterwarnings("ignore", category=FutureWarning)
        laps = laplacian_matrices_all_orders(hg)
    orders = sorted(laps)[:max_k]
    if len(laps) > max_k:
        laps = {o: laps[o] for o in orders}
    boundaries: dict[int, bool] = {}
    for a, b in zip(orders, orders[1:]):
        boundaries[b] = commute_nnz(laps[a], laps[b])
    out: dict[str, Any] = {
        "orders": orders,
        "n_orders": len(orders),
        "commutes_consecutive": boundaries,
        "commutes": all(boundaries.values()) if boundaries else True,
    }
    if spectra and orders:
        # eigsh(which='SM') is unreliable on PSD (junk-tiny algebraic
        # connectivity); these matrices are small enough that dense eigh with
        # a subset index is exact and seconds-class.
        from scipy.linalg import eigh

        ac: dict[int, float] = {}
        for o in orders:
            vals = eigh(laps[o].toarray(), subset_by_index=[1, 1], eigvals_only=True)
            ac[o] = float(vals[0])
        out["algebraic_connectivity"] = ac
    return out


def rw_density_report(
    incidence: dict[str, list[str]], *, steps: int = 100
) -> dict[str, Any]:
    """Lane 5: component annotation first, then RW density on the LC.

    Component structure is the report, not a footnote: the 2026-11-02
    re-probe showed industry/sub_sector/org_membership at 6%/5%/15% LC
    coverage — a diffusion reading there is dominated by fragmentation
    before any dynamics.
    """
    Hypergraph, _LMO, random_walk_density = _require_hgx()
    kept_sets = {k: set(v) for k, v in incidence.items()}
    hg_full = Hypergraph(edge_list=[tuple(m) for m in kept_sets.values()])
    comps = sorted(
        (sorted(c) for c in hg_full.connected_components()), key=len, reverse=True
    )
    sizes = [len(c) for c in comps]
    hg_lc = hg_full.subhypergraph_largest_component()
    n = hg_lc.num_nodes()
    s0 = np.full(n, 1.0 / n)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        warnings.filterwarnings("ignore", category=FutureWarning)
        evolution = random_walk_density(hg_lc, s0, steps)
    l2 = [float(np.linalg.norm(np.atleast_1d(step))) for step in evolution]
    final_max = float(np.max(np.atleast_1d(evolution[-1])))
    return {
        "n_components": len(comps),
        "component_sizes": sizes[:10],
        "lc_nodes": sizes[0] if sizes else 0,
        "total_nodes": hg_full.num_nodes(),
        "lc_fraction": (sizes[0] / hg_full.num_nodes()) if hg_full.num_nodes() else 0.0,
        "single_component": len(comps) == 1,
        "rw_steps": steps,
        "rw_final_max_density": final_max,
        "rw_l2_first": l2[0] if l2 else None,
        "rw_l2_last": l2[-1] if l2 else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="HGX multi-order spectral + RW-density lanes (shelf lanes 3+5)"
    )
    ap.add_argument(
        "--sources",
        default=",".join(DEFAULT_FAMILIES),
        help=f"comma-separated edge-type families (default: {','.join(DEFAULT_FAMILIES)})",
    )
    ap.add_argument("--lane", choices=("spectral", "rw", "both"), default="both")
    ap.add_argument("--max-k", type=int, default=60, help="order cap (probe protocol: <=60)")
    ap.add_argument("--k2-cap", type=int, default=300, help="k>=2 edge cap per family")
    ap.add_argument("--spectra", action="store_true", help="per-order algebraic connectivity (eigsh; slower)")
    ap.add_argument("--steps", type=int, default=100, help="RW density steps")
    args = ap.parse_args(argv)

    families = [s for s in args.sources.split(",") if s]
    rc = 0
    for fam in families:
        try:
            kept = load_family(fam, max_k=args.max_k, k2_cap=args.k2_cap)
        except ValueError as exc:
            print(f"# {fam}: {exc}", file=sys.stderr)
            continue
        print(
            f"== {fam}: {len(kept)} hyperedges (deduped, guard-filtered)"
        )
        if args.lane in ("spectral", "both"):
            rep = spectral_multiorder(kept, max_k=args.max_k, spectra=args.spectra)
            verdict = "COMMUTES" if rep["commutes"] else "does NOT commute"
            print(
                f"   spectral: {rep['n_orders']} orders (1..{rep['orders'][-1] if rep['orders'] else 0}) — {verdict} across orders"
            )
            if not rep["commutes"]:
                bad = [o for o, ok in rep["commutes_consecutive"].items() if not ok]
                print(f"   spectral: non-commuting boundaries at orders {bad}")
            if args.spectra and "algebraic_connectivity" in rep:
                for o, v in rep["algebraic_connectivity"].items():
                    print(f"   spectral: order {o:3d} lambda_2 = {v:.6g}")
        if args.lane in ("rw", "both"):
            rep = rw_density_report(kept, steps=args.steps)
            tag = "single component" if rep["single_component"] else "FRAGMENTED"
            print(
                f"   rw: {tag} — LC {rep['lc_nodes']}/{rep['total_nodes']} "
                f"({rep['lc_fraction']:.1%}), {rep['n_components']} components"
            )
            print(
                f"   rw: {rep['rw_steps']} steps — final max density "
                f"{rep['rw_final_max_density']:.6f}, L2 {rep['rw_l2_first']:.6f} -> {rep['rw_l2_last']:.6f}"
            )
    return rc


if __name__ == "__main__":
    sys.exit(main())
