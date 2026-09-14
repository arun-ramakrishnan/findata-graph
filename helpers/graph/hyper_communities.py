#!/usr/bin/env python3
"""hy-MMSBM overlapping communities over the hyper_edges incidence store
(hypergraph_incidence_hyx, S3 — first higher-order compute lane).

Runs HypergraphX's Hy-MMSBM ("Community Detection in Large Hypergraphs",
Ruggeri et al.) over the star incidence: every company gets a SOFT membership
vector over K latent blocks instead of the taxonomy's single assignment —
the consumer that attacks the assessment §7.4 limitation (the taxonomy cannot
be inferred, only hand-authored; here the corpus's own incidence proposes the
groupings).

Read-only by default; `--apply` persists to graph_analytics via
algorithms.write_analytics (metric `hypermmsbm_community`). HGX imported
LAZILY so module collection works even where hypergraphx is absent (dep
delta scipy+tqdm only, BSD-3, main-venv per the proposal). S7 (2026-09-13):
this lane REPLACED the retired igraph pilot as the alternate engine
(bridge module removed 2026-09; see D16 in graph_design.txt).

Scope default is `sector,theme,industry` (42+12+117 hyperedges over 1,165
companies; S5 added industry — the yfinance frontmatter lane):
the country lane is measured-degenerate (the size-850 India hyperedge
collapses the EM to a single block — proposal §2 appendix). Any hyperedge
larger than 25% of nodes is EXCLUDED with a warning unless --allow-giant;
the same guard keeps future sources (editions) honest.

Usage:
    python3 helpers/graph/hyper_communities.py                       # dry-run
    python3 helpers/graph/hyper_communities.py --k 12
    python3 helpers/graph/hyper_communities.py --apply               # persist
    python3 helpers/graph/hyper_communities.py --sources sector,country,theme
    python3 helpers/graph/hyper_communities.py --sweep 4,6,8,12,16   # K report (S6)
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# S5: industry joined the default scope (117 labels / 816 memberships —
# tripled the theme lane's density; country stays opt-in, measured-degenerate).
from helpers.graph import hyper_arrow as ha  # noqa: E402  # S18(b) canonical loader

DEFAULT_SOURCES = ("sector", "theme", "industry")
METRIC = "hypermmsbm_community"


def _require_hgx():
    """Import HypergraphX lazily (keeps collection working without it)."""
    try:
        # hypergraphx 1.8.0 ships LaTeX docstrings ("\sum_{...}") that trip
        # SyntaxWarnings on py3.14 at import; they are upstream noise, not
        # ours — suppress at the seam so CLI/stderr stay clean.
        import warnings

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=SyntaxWarning)
            from hypergraphx import Hypergraph
            from hypergraphx.communities.hy_mmsbm.model import HyMMSBM
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            "hypergraphx is not installed in this interpreter "
            "(pyproject dependency since hypergraph_incidence_hyx; "
            "uv sync / uv pip install hypergraphx)."
        ) from exc
    return Hypergraph, HyMMSBM


def _exclude_degenerate(
    incidence: dict[str, list[str]],
    *,
    allow_giant: bool,
    max_frac: float = 0.25,
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Drop hyperedges the EM cannot use (degeneracy guard).

    Returns ``(kept, giant_labels, singleton_labels)``. Two measured failure
    shapes (proposal §2/§5 appendix):

    - **size 1** — a singleton "group" has no pair terms; HyMMSBM's updates
      divide 0/0, NaN propagates through u, and every node lands in block 0
      (measured: 8 singleton country lanes collapse an otherwise healthy fit).
      Always dropped — a set-valued fact needs >= 2 members.
    - **size > max_frac * n_nodes** — a giant hyperedge dominates the Poisson
      likelihood and collapses the EM to one block (measured: the size-850
      India lane). Dropped with a warning unless ``allow_giant``.
    """
    n_nodes = len({m for members in incidence.values() for m in members})
    threshold = max(2.0, max_frac * n_nodes)
    kept: dict[str, list[str]] = {}
    giants: list[str] = []
    singletons: list[str] = []
    for label, members in incidence.items():
        if len(members) < 2:
            singletons.append(label)
        elif len(members) > threshold and not allow_giant:
            giants.append(label)
        else:
            kept[label] = members
    return kept, giants, singletons


def fit_communities(
    incidence: dict[str, list[str]],
    *,
    k: int,
    seed: int,
    n_iter: int,
) -> dict[str, dict[str, Any]]:
    """Fit Hy-MMSBM; return ``{entity: {block, memberships, k, seed}}``.

    ``incidence`` must already be filtered (:func:`_exclude_degenerate`) —
    singletons NaN the EM and giants collapse it (measured, §module doc).
    Memberships are row-normalised (each company's soft assignments sum to
    1). Deterministic for a fixed seed (measured: two seeded fits bitwise
    equal).
    """
    Hypergraph, HyMMSBM = _require_hgx()
    hg = Hypergraph(edge_list=[tuple(members) for members in incidence.values()])
    model = HyMMSBM(K=k, assortative=True, seed=seed)
    model.fit(hg, n_iter=n_iter)

    node2id = hg.get_mapping()._to_int
    u = model.u / model.u.sum(axis=1, keepdims=True)
    id2node = {v: name for name, v in node2id.items()}
    hard = u.argmax(axis=1)
    out: dict[str, dict[str, Any]] = {}
    for i in range(u.shape[0]):
        name = id2node[i]
        out[name] = {
            "block": int(hard[i]),
            "memberships": {str(b): round(float(u[i, b]), 4) for b in range(k)},
            "k": k,
            "seed": seed,
        }
    return out


def sweep_k(
    incidence: dict[str, list[str]],
    *,
    ks: list[int],
    seed: int,
    n_iter: int,
    allow_giant: bool = False,
) -> list[dict[str, Any]]:
    """Fit each K and report log-likelihood + effective blocks (S6).

    Same filtered incidence and seed for every K, so the log-likelihoods are
    comparable (higher = better fit; the effective-block count shows where K
    stops buying structure — extra blocks stay empty). S13 adds a BIC-style
    column (lower = better): BIC = -2*loglik + p*ln(N), p = N(K-1) + K for
    the assortative model — pick the K at the BIC minimum, using
    effective_blocks (dead blocks) and adjacent-K gains as tiebreakers.
    Report-only: the caller persists a single chosen `--k` fit.
    """
    Hypergraph, HyMMSBM = _require_hgx()
    hg = Hypergraph(edge_list=[tuple(m) for m in incidence.values()])
    rows: list[dict[str, Any]] = []
    for k in ks:
        t0 = time.perf_counter()
        model = HyMMSBM(K=k, assortative=True, seed=seed)
        model.fit(hg, n_iter=n_iter)
        dt = time.perf_counter() - t0
        u = model.u / model.u.sum(axis=1, keepdims=True)
        effective = len({int(b) for b in u.argmax(axis=1)})
        loglik = float(model.log_likelihood(hg))
        # S13: BIC-style penalty (lower = better). Free params for the
        # assortative model: u -> N(K-1) (rows on a simplex), w -> K.
        p_free = hg.num_nodes() * (k - 1) + k
        bic = -2.0 * loglik + p_free * math.log(max(hg.num_nodes(), 2))
        rows.append(
            {
                "k": k,
                "log_likelihood": round(loglik, 1),
                "bic": round(bic, 1),
                "effective_blocks": effective,
                "fit_s": round(dt, 2),
            }
        )
    return rows


def _block_report(
    memberships: dict[str, dict[str, Any]],
    incidence: dict[str, list[str]],
    *,
    top: int = 10,
    per_block_cats: int = 3,
) -> list[str]:
    """Human-readable block table: size + dominant source categories."""
    label_of: dict[str, list[str]] = {}
    for label, members in incidence.items():
        for m in members:
            label_of.setdefault(m, []).append(label)
    blocks: dict[int, list[str]] = {}
    for entity, payload in memberships.items():
        block_key = int(payload["block"])
        blocks.setdefault(block_key, []).append(entity)
    lines = [f"blocks: {len(blocks)} (K={next(iter(memberships.values()))['k']})"]
    for b in sorted(blocks, key=lambda x: -len(blocks[x])):
        members_b = blocks[b]
        cats = Counter(c for m in members_b for c in label_of.get(m, [])).most_common(
            per_block_cats
        )
        cat_txt = ", ".join(f"{c} ({n})" for c, n in cats)
        lines.append(f"  block {b:>2}: {len(members_b):4d} companies | {cat_txt}")
    return lines[: 1 + top]


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCES),
        help="hyper_edges.edge_type values to include "
        f"(default: {','.join(DEFAULT_SOURCES)}; country is "
        "measured-degenerate — see module docstring)",
    )
    p.add_argument("--k", type=int, default=8, help="number of latent blocks")
    p.add_argument("--seed", type=int, default=42, help="EM seed (deterministic)")
    p.add_argument("--n-iter", type=int, default=500, help="max EM iterations")
    p.add_argument(
        "--allow-giant",
        action="store_true",
        help="keep hyperedges larger than 25%% of nodes (degeneracy risk)",
    )
    p.add_argument(
        "--sweep",
        default=None,
        metavar="K1,K2,...",
        help="S6: fit each K (same seed), report log-likelihood + "
        "effective blocks; report-only, ignores --apply",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="persist per-company memberships to graph_analytics "
        f"(metric {METRIC}); default is a read-only report",
    )
    args = p.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    raw = ha.incidence_dict(ha.load_incidence_arrow(sources))  # S18(b) exit: Arrow load
    kept, giants, singletons = _exclude_degenerate(raw, allow_giant=args.allow_giant)
    n_members = sum(len(v) for v in kept.values())
    print(
        f"incidence: {len(raw)} hyperedges loaded, {len(kept)} kept "
        f"({n_members} memberships over "
        f"{len({m for v in kept.values() for m in v})} entities; "
        f"sources: {', '.join(sources)})"
    )
    if giants:
        print(
            f"WARNING: excluded {len(giants)} giant hyperedge(s) >25% of nodes "
            f"(--allow-giant to keep): {', '.join(sorted(giants)[:5])}"
            + (" ..." if len(giants) > 5 else ""),
            file=sys.stderr,
        )
    if singletons:
        print(
            f"NOTE: dropped {len(singletons)} singleton hyperedge(s) (<2 members, "
            "no pair terms for the EM)",
            file=sys.stderr,
        )
    if not kept:
        raise ValueError("no hyperedges left after the degeneracy guard")

    if args.sweep:
        ks = [int(x) for x in args.sweep.split(",") if x.strip()]
        rows = sweep_k(
            kept, ks=ks, seed=args.seed, n_iter=args.n_iter, allow_giant=args.allow_giant
        )
        print(f"{'K':>4} {'log_likelihood':>16} {'bic':>12} {'effective_blocks':>16} {'fit_s':>7}")
        for r in rows:
            print(
                f"{r['k']:>4} {r['log_likelihood']:>16} {r['bic']:>12} "
                f"{r['effective_blocks']:>16} {r['fit_s']:>7}"
            )
        best = min(rows, key=lambda r: r["bic"])
        print(
            f"SWEEP report-only — BIC minimum at K={best['k']} (bic={best['bic']}); "
            f"run --k {best['k']} [--apply]"
        )
        print(
            "note: raw log-likelihood rises with K by construction; the BIC "
            "column carries the penalty — also watch effective_blocks < K "
            "(dead blocks). BIC OVER-penalises membership models (u carries "
            "N(K-1) params, ~8K BIC per K step at N=1,165 vs ~+45 loglik): "
            "treat the BIC minimum as a lower-bound guide, not a verdict"
        )
        return 0

    memberships = fit_communities(kept, k=args.k, seed=args.seed, n_iter=args.n_iter)
    for line in _block_report(memberships, kept):
        print(line)

    if args.apply:
        from helpers.graph.algorithms import write_analytics

        n = write_analytics(METRIC, memberships)
        print(f"APPLY: wrote {n} rows to graph_analytics (metric {METRIC})")
    else:
        print("DRY-RUN (use --apply to persist to graph_analytics)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
