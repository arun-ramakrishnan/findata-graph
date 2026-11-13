#!/usr/bin/env python3
"""semantic_cluster label metric — e-means over company_embeddings.

The graph layer serves three *graph-native* label metrics (louvain_community,
weakly_connected_component, hypermmsbm_community). This lane adds the
**embedding lens**: a 42-partition e-means (faiss contrib) fit over the
1,209×512 ``company_embeddings`` vectors agrees with the hand-authored
sector taxonomy at ARI 0.307 (seed 0) while staying nearly independent of
both graph lenses (ARI 0.157 / 0.053). It is a **coherence instrument**, not
a sector classifier: re-fit after an embedding-model change or store growth
and watch the sector ARI — a drop means the vector space drifted away from
the taxonomy the rest of the repo assumes (docstring caveat per proposal
acceptance 5; NMI ceiling vs sector 0.537 — half the taxonomy's information
is not in the geometry).

Read-only by default; `--apply` persists per-company cluster ids to
graph_analytics via algorithms.write_analytics (metric `semantic_cluster`,
replace_population=True). faiss-cpu is imported LAZILY (optional dep; the
lane degrades to an operator-actionable message when absent — the
hyper_communities.py lazy-import precedent). k/seed/nepochs/batch_law are
pinned and persisted in every metric payload so re-fit churn is auditable
(S3).

Usage:
    python3 helpers/graph/semantic_clusters.py                 # dry-run report
    python3 helpers/graph/semantic_clusters.py --k 42 --seed 42
    python3 helpers/graph/semantic_clusters.py --apply         # persist
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

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.graph.algorithms import write_analytics  # noqa: E402

METRIC = "semantic_cluster"

# Pinned recipe (proposal S3) — persisted in every payload so a store that
# grows silently (auto recipe changes B / alpha0) stays auditable.
EMBED_DIM = 512
K = 42
NEPOCHS = 5
SEED = 42
BATCH_LAW = "throughput"  # auto at d=512 resolves to throughput; pinned explicit (S3)
ARI_BASELINE_RANGE = (0.27, 0.31)  # measured 5-seed spread (proposal §2)


def _require_faiss():
    """Import faiss + NumpyEmeans lazily (optional dependency).

    faiss-cpu is NOT a hard venv requirement (proposal S2); when absent the
    lane exits with a message naming the install command instead of failing
    collection of the module (the hyper_communities.py precedent).
    """
    try:
        import faiss  # noqa: F401
        from faiss.contrib.e_means import NumpyEmeans
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError(
            "faiss-cpu is not installed in this interpreter — the "
            "semantic_cluster lane needs it for e-means. Install with "
            "`uv pip install faiss-cpu` (1.16.0, MIT, cp310-abi3, "
            "verified compatible with this py3.14 venv)."
        ) from exc
    return NumpyEmeans


def load_embeddings(conn):
    """Return ``(names, X)`` from company_embeddings (ordered by name).

    ``names`` is the list of company_name; ``X`` is float32 (n, 512)
    with rows in the same order. Empty / absent store -> (None, None) so
    the caller can clean-skip (maint-chain W1 precedent).
    """
    try:
        rows = conn.execute(
            "SELECT company_name, embedding FROM company_embeddings ORDER BY company_name"
        ).fetchall()
    except Exception:
        return None, None
    if not rows:
        return None, None
    names = [r[0] for r in rows]
    X = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    return names, X


def fit_clusters(X, *, k=K, nepochs=NEPOCHS, seed=SEED, batch_law=BATCH_LAW):
    """Fit e-means; return ``(labels, meta)``.

    ``labels`` is int (n,) — argmax assignment. ``meta`` carries the pinned
    recipe + fit timing + empty-cluster count (report/audit).
    """
    NumpyEmeans = _require_faiss()
    # L2-normalize (unit sphere) — the embedding lens is direction-only.
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    em = NumpyEmeans(
        Xn.shape[1],
        k,
        nepochs=nepochs,
        seed=seed,
        spherical=False,
        batch_law=batch_law,
    )
    t0 = time.perf_counter()
    _, labels = em.train(Xn)
    dt = time.perf_counter() - t0
    empty = int((np.bincount(labels, minlength=k) == 0).sum())
    meta = {
        "k": int(k),
        "seed": int(seed),
        "nepochs": int(nepochs),
        "batch_law": batch_law,
        "fit_s": round(dt, 3),
        "empty_clusters": empty,
        "model": "embeddinggemma-2-q8_512",
    }
    return labels, meta


def adjusted_rand_score(labels_true, labels_pred):
    """ARI (Rand index corrected for chance) — pure-python, no sklearn dep.

    Mirrors sklearn.metrics.adjusted_rand_score on the contingency table;
    returns 1.0 for identical partitions (denominator 0 case).
    """
    labels_true = np.asarray(labels_true)
    labels_pred = np.asarray(labels_pred)
    n = len(labels_true)
    if n == 0:
        return 1.0
    # Contingency table.
    a = labels_true.astype(np.int64, copy=False)
    b = labels_pred.astype(np.int64, copy=False)
    cm = np.zeros((int(a.max()) + 1, int(b.max()) + 1), dtype=np.int64)
    np.add.at(cm, (a, b), 1)

    def comb2(x):
        return x * (x - 1) // 2

    sum_ab = int(comb2(cm).sum())
    sum_a = int(comb2(cm.sum(axis=1)).sum())
    sum_b = int(comb2(cm.sum(axis=0)).sum())
    total = comb2(n)
    expected = sum_a * sum_b / total if total else 0.0
    max_index = (sum_a + sum_b) / 2.0
    if max_index == expected:
        return 1.0
    return float((sum_ab - expected) / (max_index - expected))


def sector_labels_for(names, conn):
    """Sector classification for the embedded companies (alignment).

    Returns ``(subset_names, cluster_idx, sector_codes)`` — only companies
    that BOTH have an embedding (from ``names``) and a non-empty
    ``sector_classification``; ``cluster_idx`` indexes back into the
    embedding order.
    """
    if not names:
        return [], [], []
    marks = ",".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT name, sector_classification FROM entities "  # noqa: S608  placeholders bounded
        f"WHERE name IN ({marks}) AND sector_classification IS NOT NULL "
        "AND sector_classification <> ''",
        names,
    ).fetchall()
    by_name = {r[0]: r[1] for r in rows}
    subset = [nm for nm in names if nm in by_name]
    idx = {nm: i for i, nm in enumerate(names)}
    cluster_idx = [idx[nm] for nm in subset]
    codes = [by_name[nm] for nm in subset]
    # numeric-encode sector codes
    uniq = {c: i for i, c in enumerate(sorted(set(codes)))}
    return subset, cluster_idx, [uniq[c] for c in codes]


def _payload(cluster, meta):
    return {"cluster": int(cluster), **meta}


def _report_block(names, labels, meta, conn):
    lines = [
        f"companies: {len(names)} (company_embeddings)",
        f"clusters: k={meta['k']} non-empty={meta['k'] - meta['empty_clusters']} "
        f"(empty {meta['empty_clusters']})",
        f"fit_s: {meta['fit_s']}",
    ]
    subset, cidx, codes = sector_labels_for(names, conn)
    if subset:
        ari = adjusted_rand_score(codes, labels[cidx])
        lines.append(
            f"sector ARI: {ari:.4f} "
            f"(overlap {len(subset)} companies; baseline {ARI_BASELINE_RANGE[0]}–"
            f"{ARI_BASELINE_RANGE[1]})"
        )
        if not (ARI_BASELINE_RANGE[0] <= ari <= ARI_BASELINE_RANGE[1]):
            lines.append(
                f"NOTE: sector ARI outside measured baseline "
                f"{ARI_BASELINE_RANGE} — the vector space may have drifted "
                "(coherence instrument reading, not a failure)"
            )
    else:
        lines.append("sector ARI: n/a (no sector_classification overlap)")
    return lines


def _cli(argv=None, *, _conn=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--k", type=int, default=K, help="number of clusters")
    p.add_argument("--seed", type=int, default=SEED, help="e-means seed (pinned default 42)")
    p.add_argument("--nepochs", type=int, default=NEPOCHS, help="e-means iterations")
    p.add_argument(
        "--batch-law", default=BATCH_LAW, help="centroid init/EMA law (throughput = auto at d=512)"
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="persist per-company clusters to graph_analytics (metric "
        f"{METRIC}); default is a read-only report",
    )
    args = p.parse_args(argv)

    own = _conn is None
    if _conn is not None:
        conn = _conn
    else:
        import duckdb

        conn = duckdb.connect("memory/research.db", **memory_cap_config())
    try:
        names, X = load_embeddings(conn)
        if names is None:
            print(
                "[semantic-clusters] company_embeddings empty/absent — nothing to compute, skipping"
            )
            return 0
        labels, meta = fit_clusters(
            X,
            k=args.k,
            nepochs=args.nepochs,
            seed=args.seed,
            batch_law=args.batch_law,
        )
        for line in _report_block(names, labels, meta, conn):
            print(line)

        if args.apply:
            values = {nm: _payload(int(lbl), meta) for nm, lbl in zip(names, labels)}
            n = write_analytics(METRIC, values, conn=conn, replace_population=True)
            print(f"APPLY: wrote {n} rows to graph_analytics (metric {METRIC})")
        else:
            print("DRY-RUN (use --apply to persist to graph_analytics)")
        return 0
    finally:
        if own:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
