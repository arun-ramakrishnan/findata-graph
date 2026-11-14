#!/usr/bin/env python3
"""Q3 PPR edge-type attribution harness (graph_kernels_ppr_lane §7).

Answer to the proposal's open query: *which relational edge type explains
which share of a seed's PPR neighborhood?* Mechanics:

- leave-one-type-out over the 8 relational PPR types (``PPR_INCLUDE_EDGE_TYPES``)
  — re-solve the cohort on each ablation projection and diff the resulting
  top-k neighborhoods against the full relational projection
  (top-k Jaccard, normalized rank displacement, score L-inf shift,
  self-mass delta);
- cross-projection diff (relational 8-type vs economic 14-type
  ``onager.ECONOMIC_EDGE_TYPES``) — the same neighborhood diff with the
  projection itself as the treatment.

This is a composition harness, not a new kernel lane: it calls
``load_centrality_projection`` + ``personalized_pagerank_scores_multi``
(the S1 batch API) and therefore inherits their lane guards and budgets.
Read-only: never writes to the store (report/JSON go to
``bench_data/graph_kernels_ppr/``).

Determinism: the cohort is chosen by even spread over the *sorted* full
projection names when ``--seeds`` is absent; metric aggregation is mean /
median / min over the scored seeds in cohort order.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph.onager import ECONOMIC_EDGE_TYPES  # noqa: E402
from helpers.graph.scipy_bridge import (  # noqa: E402
    DEFAULT_DB_PATH,
    PPR_ALPHA_DEFAULT,
    PPR_INCLUDE_EDGE_TYPES,
    load_centrality_projection,
    personalized_pagerank_scores_multi,
)

_DEFAULT_COHORT = 20
_DEFAULT_TOP_K = 10
_SCHEMA = "ppr_edge_attribution/v1"


def _resolve_cohort(
    names: list[str], seeds: list[str] | None, cohort_size: int
) -> tuple[list[str], list[str]]:
    """Pick the cohort; returns ``(chosen, dropped)``.

    Explicit seeds are kept in the order given and filtered against the
    projection; a missing seed is reported (dropped), never fatal — an
    ablation can legitimately orphan a seed out of the projection.
    With no seeds, spread ``cohort_size`` names evenly over the sorted
    full-projection names (deterministic; population-scale sweep posture).
    """
    if seeds:
        chosen = [s for s in seeds if s in names]
        dropped = [s for s in seeds if s not in names]
        return chosen, dropped
    if not names:
        return [], []
    step = max(1, len(names) // max(1, cohort_size))
    return names[::step][:cohort_size], []


def _topk_others(scores: np.ndarray, names: list[str], seed: str, k: int) -> list[str]:
    pos = {n: i for i, n in enumerate(names)}
    si = pos[seed]
    order = np.argsort(-np.asarray(scores, dtype=float))
    return [names[int(j)] for j in order if int(j) != si][:k]


def _order_ranks(union: list[str], pos: dict[str, int], scores: np.ndarray) -> dict[str, int]:
    """Rank each union member by descending score; absent names rank last.

    Every union member gets a rank (missing name scores as -inf, i.e.
    lands at the tail) so ``rank_displacement_norm`` is total.
    """
    decorated = [(scores[pos[n]] if n in pos else -np.inf, n) for n in union]
    decorated.sort(key=lambda x: x[0], reverse=True)
    return {n: i + 1 for i, (_score, n) in enumerate(decorated)}


def _diff_neighborhoods(
    full_scores: np.ndarray,
    full_names: list[str],
    ablated_scores: np.ndarray | None,
    ablated_names: list[str],
    seed: str,
    k: int,
) -> dict[str, Any]:
    """Diff the seed's top-k neighborhood between two projections.

    Returns a metrics dict (0 = identical behavior on this seed):
    - ``topk_jaccard``: |T∩T'| / |T∪T'| over the top-k *other* names;
    - ``topk_overlap``: |T∩T'|;
    - ``rank_displacement_norm``: mean |rank_full - rank_ablated| over the
      union ranking, normalized by ``k`` (1.0 = full reversal on every
      union member, 0.0 = identical order);
    - ``max_score_shift``: max |score_full - score_ablated| over the union
      (absent name scores as 0 in that projection);
    - ``self_mass_delta``: |PPR(seed)[seed] full - ablated|;
    - ``coverage_ok``: whether ``seed`` exists in both projections.
    """
    if seed not in ablated_names or ablated_scores is None:
        return {
            "topk_jaccard": 0.0,
            "topk_overlap": 0,
            "rank_displacement_norm": 1.0,
            "max_score_shift": float(np.max(full_scores)),
            "self_mass_delta": None,
            "coverage_ok": False,
        }
    T = _topk_others(full_scores, full_names, seed, k)
    Tp = _topk_others(ablated_scores, ablated_names, seed, k)
    union = sorted(set(T) | set(Tp))
    pos_f = {n: i for i, n in enumerate(full_names)}
    pos_a = {n: i for i, n in enumerate(ablated_names)}
    rank_f = _order_ranks(union, pos_f, full_scores)
    rank_a = _order_ranks(union, pos_a, ablated_scores)
    displacement = sum(abs(rank_f[n] - rank_a[n]) for n in union)
    max_shift = max(
        (
            abs(float(full_scores[pos_f[n]]) - float(ablated_scores[pos_a[n]]))
            for n in union
            if n in pos_f and n in pos_a
        ),
        default=0.0,
    )
    return {
        "topk_jaccard": len(set(T) & set(Tp)) / max(1, len(set(T) | set(Tp))),
        "topk_overlap": len(set(T) & set(Tp)),
        "rank_displacement_norm": displacement / (k * max(1, len(union))),
        "max_score_shift": max_shift,
        "self_mass_delta": abs(
            float(full_scores[pos_f[seed]]) - float(ablated_scores[pos_a[seed]])
        ),
        "coverage_ok": True,
    }


def _aggregate(per_seed: list[dict[str, Any]], k: int) -> dict[str, Any]:
    """Collapse per-seed diffs into cohort aggregates (mean/median/min)."""
    scored = [m for m in per_seed if m["coverage_ok"]]
    n = len(scored)
    if n == 0:
        return {
            "seeds_scored": 0,
            "seeds_requested": len(per_seed),
            "topk_jaccard_mean": None,
            "topk_jaccard_median": None,
            "topk_jaccard_min": None,
            "rank_displacement_norm_mean": None,
            "max_score_shift_mean": None,
            "self_mass_delta_mean": None,
            "worst_seed": None,
        }
    jac = [m["topk_jaccard"] for m in scored]
    disp = [m["rank_displacement_norm"] for m in scored]
    shift = [m["max_score_shift"] for m in scored]
    smd = [m["self_mass_delta"] for m in scored if m["self_mass_delta"] is not None]
    worst = min(scored, key=lambda m: (m["topk_jaccard"], m["rank_displacement_norm"]))
    return {
        "seeds_scored": n,
        "seeds_requested": len(per_seed),
        "topk_jaccard_mean": float(np.mean(jac)),
        "topk_jaccard_median": float(np.median(jac)),
        "topk_jaccard_min": float(min(jac)),
        "rank_displacement_norm_mean": float(np.mean(disp)),
        "max_score_shift_mean": float(np.mean(shift)),
        "self_mass_delta_mean": float(np.mean(smd)) if smd else None,
        "worst_seed": {
            "seed": worst.get("_seed", None),
            "topk_jaccard": worst["topk_jaccard"],
            "rank_displacement_norm": worst["rank_displacement_norm"],
            "max_score_shift": worst["max_score_shift"],
        },
    }


def _projection_stats(A: Any) -> dict[str, int]:
    return {"entities": int(A.shape[0]), "edges": int(A.nnz // 2)}


def run(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    seeds: list[str] | None = None,
    cohort_size: int = _DEFAULT_COHORT,
    top_k: int = _DEFAULT_TOP_K,
    alpha: float = PPR_ALPHA_DEFAULT,
    skip_types: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the Q3 attribution suite; returns the report dict.

    Relational full (the 8 PPR include types) is the base. Each ablated
    projection drops exactly one type; the seed cohort is diffed against
    the base. Then the economic projection (14 types) is diffed against
    the relational base — the cross-projection treatment.
    """
    t0 = time.perf_counter()
    types = tuple(t for t in PPR_INCLUDE_EDGE_TYPES if t not in skip_types)
    rel_full_A, rel_full_names = load_centrality_projection(
        db_path, include_edge_types=PPR_INCLUDE_EDGE_TYPES
    )
    cohort, dropped_seeds = _resolve_cohort(rel_full_names, seeds, cohort_size)
    if not cohort:
        raise ValueError("PPR edge attribution: empty cohort after seed resolution")

    rel_full_scores = personalized_pagerank_scores_multi(
        rel_full_A, rel_full_names, cohort, alpha=alpha
    )

    ablation: dict[str, Any] = {}
    for t in types:
        A_t, names_t = load_centrality_projection(
            db_path, include_edge_types=tuple(x for x in PPR_INCLUDE_EDGE_TYPES if x != t)
        )
        kept = [s for s in cohort if s in names_t]
        scores_t = (
            personalized_pagerank_scores_multi(A_t, names_t, kept, alpha=alpha) if kept else {}
        )
        per_seed: list[dict[str, Any]] = []
        for s in cohort:
            m = _diff_neighborhoods(
                rel_full_scores[s], rel_full_names, scores_t.get(s), names_t, s, top_k
            )
            m["_seed"] = s
            per_seed.append(m)
        ablation[t] = {
            "params": {"dropped_type": t, "top_k": top_k, "alpha": alpha},
            "projection": _projection_stats(A_t),
            "cohort": {
                "requested": len(cohort),
                "scored": sum(1 for m in per_seed if m["coverage_ok"]),
                "dropped_seeds": [s for s in cohort if s not in names_t],
            },
            "metrics": _aggregate(per_seed, top_k),
        }

    eco_A, eco_names = load_centrality_projection(
        db_path, include_edge_types=tuple(ECONOMIC_EDGE_TYPES)
    )
    eco_kept = [s for s in cohort if s in eco_names]
    eco_scores = (
        personalized_pagerank_scores_multi(eco_A, eco_names, eco_kept, alpha=alpha)
        if eco_kept
        else {}
    )
    eco_per_seed: list[dict[str, Any]] = []
    for s in cohort:
        m = _diff_neighborhoods(
            rel_full_scores[s], rel_full_names, eco_scores.get(s), eco_names, s, top_k
        )
        m["_seed"] = s
        eco_per_seed.append(m)

    report = {
        "schema": _SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "wall_seconds": round(time.perf_counter() - t0, 2),
        "parameters": {
            "db_path": str(db_path),
            "seeds": seeds if seeds is not None else f"even-spread x{cohort_size}",
            "cohort_size": cohort_size,
            "top_k": top_k,
            "alpha": alpha,
        },
        "relational_types": list(PPR_INCLUDE_EDGE_TYPES),
        "economic_types": list(ECONOMIC_EDGE_TYPES),
        "projection_stats": {
            "relational": _projection_stats(rel_full_A),
            "economic": _projection_stats(eco_A),
        },
        "cohort": {
            "seeds": cohort,
            "dropped_seeds": dropped_seeds,
        },
        "ablation": ablation,
        "cross_projection": {
            "params": {"treatment": "economic vs relational", "top_k": top_k, "alpha": alpha},
            "projection": _projection_stats(eco_A),
            "cohort": {
                "requested": len(cohort),
                "scored": sum(1 for m in eco_per_seed if m["coverage_ok"]),
                "dropped_seeds": [s for s in cohort if s not in eco_names],
            },
            "metrics": _aggregate(eco_per_seed, top_k),
        },
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    """Human-visible report (same numbers as the JSON, plus context)."""
    p = report["parameters"]
    lines = [
        "# PPR edge-type attribution (Q3 harness)",
        "",
        f"- schema: {report['schema']} · generated {report['generated_at']} · "
        f"wall {report['wall_seconds']}s",
        f"- db: `{p['db_path']}` · alpha {p['alpha']} · top-k {p['top_k']} · "
        f"cohort {len(report['cohort']['seeds'])} seeds "
        f"({p['seeds']})",
        f"- projections: relational {report['projection_stats']['relational']} · "
        f"economic {report['projection_stats']['economic']}",
        "",
        "## Leave-one-type-out (relational 8-type base)",
        "",
        "| dropped type | jaccard μ | jaccard med | jaccard min | rank disp μ | max shift μ | scored |",
        "|---|---|---|---|---|---|---|",
    ]
    for t, block in report["ablation"].items():
        m = block["metrics"]
        lines.append(
            f"| {t} | {m['topk_jaccard_mean']} | {m['topk_jaccard_median']} | "
            f"{m['topk_jaccard_min']} | {m['rank_displacement_norm_mean']} | "
            f"{m['max_score_shift_mean']} | {m['seeds_scored']}/{m['seeds_requested']} |"
        )
    x = report["cross_projection"]
    m = x["metrics"]
    lines += [
        "",
        "## Cross-projection diff (economic 14-type vs relational 8-type)",
        "",
        "| jaccard μ | jaccard med | jaccard min | rank disp μ | max shift μ | scored |",
        f"| {m['topk_jaccard_mean']} | {m['topk_jaccard_median']} | "
        f"{m['topk_jaccard_min']} | {m['rank_displacement_norm_mean']} | "
        f"{m['max_score_shift_mean']} | {m['seeds_scored']}/{m['seeds_requested']} |",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Q3 PPR edge-type attribution harness")
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH), help="store path")
    ap.add_argument(
        "--seeds", default=None, help="comma-separated seed names (default: even-spread cohort)"
    )
    ap.add_argument("--cohort-size", type=int, default=_DEFAULT_COHORT)
    ap.add_argument("--top-k", type=int, default=_DEFAULT_TOP_K)
    ap.add_argument("--alpha", type=float, default=PPR_ALPHA_DEFAULT)
    ap.add_argument("--skip-types", default=None, help="comma-separated types to skip in ablations")
    ap.add_argument(
        "--json",
        default=None,
        help="JSON out (default bench_data/graph_kernels_ppr/edge_attribution.json)",
    )
    ap.add_argument(
        "--report",
        default=None,
        help="markdown out (default <json dir>/edge_attribution_report.md)",
    )
    args = ap.parse_args()

    seeds = [s.strip() for s in args.seeds.split(",") if s.strip()] if args.seeds else None
    skip = (
        tuple(s.strip() for s in args.skip_types.split(",") if s.strip()) if args.skip_types else ()
    )

    json_out = (
        Path(args.json)
        if args.json
        else (_PROJECT_ROOT / "bench_data" / "graph_kernels_ppr" / "edge_attribution.json")
    )
    report_out = (
        Path(args.report) if args.report else json_out.with_name("edge_attribution_report.md")
    )

    report = run(
        args.db,
        seeds=seeds,
        cohort_size=args.cohort_size,
        top_k=args.top_k,
        alpha=args.alpha,
        skip_types=skip,
    )
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report_out.write_text(render_markdown(report) + "\n")
    print(f"wrote {json_out} and {report_out} (wall {report['wall_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
