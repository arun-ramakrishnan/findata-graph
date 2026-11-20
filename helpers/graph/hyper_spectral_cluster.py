#!/usr/bin/env python3
"""Spectral community consumer (HGX unlock-shelf item #8).

Consumes the multi-order Laplacian spectra and turns them into a per-node
community assignment: for a chosen order, take the top-m eigenvectors,
L2-normalize, cluster with k-means (sklearn) -- or e-means (faiss contrib)
when available, mirroring semantic_clusters.py. Reports ARI vs the
hand-authored sector taxonomy so the spectral lane has a QA target.

Usage:
    python3 helpers/graph/hyper_spectral_cluster.py                # orders=reducible (default)
    python3 helpers/graph/hyper_spectral_cluster.py --orders 3,4,5
    python3 helpers/graph/hyper_spectral_cluster.py --orders all
    python3 helpers/graph/hyper_spectral_cluster.py --orders 3 --apply
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np  # noqa: E402
from scipy.linalg import eigh  # noqa: E402

from helpers.graph import hyper_arrow as ha  # noqa: E402
from hypergraphx import Hypergraph  # noqa: E402
from hypergraphx.linalg import laplacian_matrices_all_orders  # noqa: E402

DEFAULT_SOURCES = ("sector", "theme", "industry")
METRIC = "spectral_community"


def _require_sklearn():
    try:
        from sklearn.cluster import KMeans
        from sklearn.metrics import adjusted_rand_score
    except ImportError as exc:
        raise ImportError("scikit-learn not installed (hypergraphx[ml] extra)") from exc
    return KMeans, adjusted_rand_score


def _sector_labels(incidence):
    label_of = {}
    for label, members in incidence.items():
        if not label.startswith("sector:"):
            continue
        for m in members:
            label_of.setdefault(m, label)
    return label_of


def _eigenvectors(lap, m):
    w, V = eigh(lap.toarray())
    idx = list(range(1, min(m + 1, V.shape[1])))
    return V[:, idx]


def _cluster(vecs, k, seed=42, use_emeans=False):
    if use_emeans:
        try:
            import faiss  # noqa: F401
            from faiss.contrib.e_means import NumpyEmeans
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            Xn = (vecs / norms).astype(np.float32)
            em = NumpyEmeans(Xn.shape[1], k, nepochs=5, seed=seed, spherical=False, batch_law="throughput")
            _, labels = em.train(Xn)
            return np.asarray(labels, dtype=int)
        except Exception:
            pass
    KMeans, _ = _require_sklearn()
    km = KMeans(n_clusters=k, n_init=10, random_state=seed)
    return km.fit_predict(vecs)


def _cli(argv=None):
    p = argparse.ArgumentParser(description="Spectral community consumer")
    p.add_argument("--sources", default=",".join(DEFAULT_SOURCES))
    p.add_argument(
        "--orders",
        default="reducible",
        help="comma list of orders, or 'all', or 'reducible' (default: the "
        "entropy-representative layer set from hypergraphx reducibility, "
        "computed live on the loaded family)",
    )
    p.add_argument("--m", type=int, default=8, help="eigenvectors per order")
    p.add_argument("--k", type=int, default=8, help="clusters")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--use-emeans", action="store_true")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    raw = ha.incidence_dict(ha.load_incidence_arrow(sources))
    kept = {k: v for k, v in raw.items() if len(v) >= 2}
    KMeans, ari = _require_sklearn()

    hg = Hypergraph(edge_list=[tuple(v) for v in kept.values()])
    laps = laplacian_matrices_all_orders(hg)

    orders_raw = args.orders.strip().lower()
    if orders_raw == "reducible":
        # Adoption of unlock-shelf #6: the entropy-representative layer set
        # is the information-preserving subset of orders (reducibility one-shot:
        # all other orders are redundant at eta=0.14). Rep layers are edge
        # sizes; laps key convention: order k covers size k+1, so try size
        # first, else size-1.
        from hypergraphx.measures.reducibility import reducibility

        t0 = time.perf_counter()
        eta, rep_layers = reducibility(hg, optimization="greedy", entropy_method="count")
        orders_wanted = []
        for size in sorted(rep_layers):
            order = size if size in laps else size - 1
            if order in laps and order not in orders_wanted:
                orders_wanted.append(order)
        print(
            "reducible: eta=%.4f, representative sizes %s -> orders %s (%.2fs)"
            % (eta, sorted(rep_layers), orders_wanted, time.perf_counter() - t0)
        )
    elif orders_raw == "all":
        orders_wanted = sorted(laps)
    else:
        orders_wanted = [int(x) for x in args.orders.split(",") if x.strip()]
    node2id = hg.get_mapping()._to_int
    id2node = {v: n for n, v in node2id.items()}
    nodes = [id2node[i] for i in range(len(id2node))]
    sector_of = _sector_labels(raw)
    sector_names = [sector_of.get(n) for n in nodes]
    sector_codes = {name: i for i, name in enumerate(sorted({x for x in sector_names if x is not None}))}
    sector_labels = np.array([sector_codes[x] if x is not None else -1 for x in sector_names], dtype=int)
    mask = sector_labels >= 0

    for order in orders_wanted:
        if order not in laps:
            print("order %d not present, skipping" % order)
            continue
        t0 = time.perf_counter()
        V = _eigenvectors(laps[order], args.m)
        try:
            import faiss  # noqa: F401
            _faiss_ok = True
        except Exception:
            _faiss_ok = False
        use_emeans = args.use_emeans or _faiss_ok
        labels = _cluster(V, args.k, seed=args.seed, use_emeans=use_emeans)
        dt = time.perf_counter() - t0
        mask = sector_labels >= 0
        ari_sector = ari(labels[mask], sector_labels[mask]) if mask.any() else float("nan")
        print("order %d: clustered %d nodes in %.2fs | ARI vs sector=%.4f" % (order, len(nodes), dt, ari_sector))
        if args.apply:
            from helpers.graph.algorithms import write_analytics
            payload = {n: {"block": int(labels[i]), "order": order, "m": args.m, "k": args.k, "seed": args.seed} for i, n in enumerate(nodes)}
            metric = "%s_order%d" % (METRIC, order)
            nn = write_analytics(metric, payload, replace_population=True)
            print("  APPLY: wrote %d rows (metric %s)" % (nn, metric))
    if not args.apply:
        print("DRY-RUN (use --apply to persist)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
