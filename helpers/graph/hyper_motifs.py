#!/usr/bin/env python3
"""Full-incidence cross-edge overlap census (HGX unlock-shelf lane 1 reopen;
algorithms_adoption_sweep S3).

The 2026-10-28 HGX motifs probe (archive/graph/hgx_unlock_shelf_probe.md)
was NO-GO because HGX's census only SEES edges of size <= order
(`get_edges(size=order, up_to=True)`) — the 2026-10-30 correction
(hgx_over_dyads S5) measured order-3 covering just 24.0% of live
incidence mass, so cross-edge intersection structure was invisible to
the instrument. The reopen trigger — "re-probe only with a census that
sees the full incidence" — is owned here.

Census statistic (per edge-type family, per order k): the number of
unordered hyperedge PAIRS sharing >= k distinct members, over ALL
deduped edges of size >= k — no size cap, the full incidence. The
proposal's SQL self-join shape is realized as a node-postings index
(same complexity class: sum over nodes of C(degree, 2) pair touches;
measured max ~30k pair-rows on the live store, trivial).

Null model: configuration model by stub matching — every node's
incidence degree is dealt as stubs (shuffled) to edge slots, preserving
node degrees and edge sizes exactly; within-edge duplicates are
deduplicated (documented conservative approximation). R replicates of
the same statistic give mean/std; norm_delta = (obs - mean) / std,
|norm_delta| >= 2 reads ENRICHED/DEPLETED, else ~NULL. House probe
protocol defaults: runs=10, seed=42 (the 2026-10-28 probe's protocol).

Standalone lane CLI (house pattern — #472): no query.py slot, no module
importers outside tests; report-only, no writes.

Usage:
    python3 helpers/graph/hyper_motifs.py
    python3 helpers/graph/hyper_motifs.py --sources edition,index
    python3 helpers/graph/hyper_motifs.py --sources group --k2-cap 600
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph import hyper_arrow as ha  # noqa: E402
from helpers.graph.hyper_spectral import load_family  # noqa: E402

DEFAULT_FAMILIES = ("edition", "industry", "sub_sector", "org_membership")
DEFAULT_ORDERS = (2, 3)


def pair_shared_counts(edges: list[list[str]]) -> dict[tuple[int, int], int]:
    """Per-pair shared-member counts for one family's edge list (edge ids
    are positions in ``edges``).

    Node-postings index: each node contributes C(degree, 2) pair touches,
    so the shared-member count for every hyperedge pair is accumulated in
    one pass. This is the census's counting core, exposed so the pair
    WORKLIST (which pairs, how shared) can be exported — the co-supply
    peer lane and the group reconciliation triage both read this shape.
    """
    postings: dict[str, list[int]] = {}
    for eid, members in enumerate(edges):
        for v in members:
            postings.setdefault(v, []).append(eid)

    shared: dict[tuple[int, int], int] = {}
    for edge_ids in postings.values():
        n = len(edge_ids)
        if n < 2:
            continue
        for i in range(n):
            a = edge_ids[i]
            for j in range(i + 1, n):
                b = edge_ids[j]
                shared[(a, b)] = shared.get((a, b), 0) + 1
    return shared


def census(edges: list[list[str]], orders: tuple[int, ...] = DEFAULT_ORDERS) -> dict[str, Any]:
    """Cross-edge pair-overlap counts for one family's edge list.

    Returns, per order k, the pair count with shared >= k plus the
    shared-count histogram (pairs bucketed at 2, 3, 4+).
    """
    shared = pair_shared_counts(edges)

    hist2 = hist3 = hist4plus = 0
    for c in shared.values():
        if c >= 4:
            hist4plus += 1
        elif c == 3:
            hist3 += 1
        elif c == 2:
            hist2 += 1
    out: dict[str, Any] = {
        "pairs_sharing_any": len(shared),
        "hist_shared_2": hist2,
        "hist_shared_3": hist3,
        "hist_shared_4plus": hist4plus,
    }
    for k in orders:
        out[f"pairs_ge_{k}"] = sum(1 for c in shared.values() if c >= k)
    return out


def relation_of(members_a: set[str], members_b: set[str]) -> str:
    """Triage relation between two overlapping edges: 'subset' when one
    member set contains the other (nesting/alias candidate), 'overlap'
    for partial sharing."""
    if members_a <= members_b or members_b <= members_a:
        return "subset"
    return "overlap"


def configuration_null(
    edges: list[list[str]],
    *,
    orders: tuple[int, ...] = DEFAULT_ORDERS,
    runs: int = 10,
    seed: int = 42,
) -> dict[str, Any]:
    """Stub-matching configuration null for the census statistic.

    Node degrees are the stub multiset; each replicate shuffles the stubs
    and deals them into the SAME edge-size slots (sizes preserved), then
    runs the identical census. Within-edge duplicate stubs are deduped —
    replicate edges can be slightly smaller than their slot size, which
    makes the null conservative (understates cross-edge overlap).
    """
    node_ids: dict[str, int] = {}
    degrees: list[int] = []
    for members in edges:
        for v in members:
            idx = node_ids.get(v)
            if idx is None:
                idx = len(degrees)
                node_ids[v] = idx
                degrees.append(0)
            degrees[idx] += 1
    stubs = np.repeat(np.arange(len(degrees), dtype=np.int32), degrees)

    sizes = [len(e) for e in edges]
    id_of = sorted(node_ids, key=lambda x: node_ids.get(x, 0))
    replicates: list[dict[str, Any]] = []
    rng = np.random.default_rng(seed)
    for _ in range(runs):
        rng.shuffle(stubs)
        pos = 0
        rep_edges: list[list[str]] = []
        for s in sizes:
            members = {id_of[i] for i in stubs[pos : pos + s]}
            pos += s
            rep_edges.append(sorted(members))
        replicates.append(census(rep_edges, orders))

    out: dict[str, Any] = {"runs": runs, "seed": seed}
    for key in (f"pairs_ge_{k}" for k in orders):
        vals = np.array([r[key] for r in replicates], dtype=float)
        out[f"{key}_null_mean"] = float(vals.mean())
        out[f"{key}_null_std"] = float(vals.std())
    return out


def verdict(norm_delta: float | None) -> str:
    if norm_delta is None:
        return "~NULL (degenerate null)"
    if norm_delta >= 2.0:
        return "ENRICHED"
    if norm_delta <= -2.0:
        return "DEPLETED"
    return "~NULL"


def run_family(
    edge_type: str,
    *,
    orders: tuple[int, ...] = DEFAULT_ORDERS,
    runs: int = 10,
    seed: int = 42,
    max_k: int = 60,
    k2_cap: int = 300,
) -> dict[str, Any]:
    """Load one family (house guard protocol), census it, null it, judge it."""
    incidence = load_family(edge_type, max_k=max_k, k2_cap=k2_cap)
    edges = [sorted(set(members)) for members in incidence.values()]
    obs = census(edges, orders)
    null = configuration_null(edges, orders=orders, runs=runs, seed=seed)
    res: dict[str, Any] = {
        "family": edge_type,
        "edges": len(edges),
        "nodes": len({v for e in edges for v in e}),
        "orders": orders,
        **obs,
        "null": null,
        "norm_delta": {},
        "verdicts": {},
    }
    for k in orders:
        key = f"pairs_ge_{k}"
        std = null[f"{key}_null_std"]
        if std > 0:
            nd = (obs[key] - null[f"{key}_null_mean"]) / std
            res["norm_delta"][k] = round(nd, 2)
        else:
            res["norm_delta"][k] = None
        res["verdicts"][k] = verdict(res["norm_delta"][k])
    return res


def _print_report(results: list[dict[str, Any]]) -> None:
    for r in results:
        print(
            f"\n{r['family']}: {r['edges']} edges / {r['nodes']} nodes — "
            f"pairs sharing >=2 members: {r['pairs_ge_2']} "
            f"(hist 2:{r['hist_shared_2']} 3:{r['hist_shared_3']} 4+:{r['hist_shared_4plus']})"
        )
        for k in r["orders"]:
            key = f"pairs_ge_{k}"
            nd = r["norm_delta"][k]
            nd_s = f"{nd:+.2f}" if nd is not None else "—"
            print(
                f"  shared>={k}: obs={r[key]} "
                f"null={r['null'][f'{key}_null_mean']:.1f}"
                f"±{r['null'][f'{key}_null_std']:.1f} "
                f"norm_delta={nd_s} → {r['verdicts'][k]}"
            )
    print(
        "\nStatistic: unordered hyperedge pairs sharing >= k distinct members, "
        "FULL incidence (fixes the HGX size<=order visibility defect). "
        "Null: stub-matching configuration model, node degrees + edge sizes "
        "preserved, within-edge dupes deduped (conservative). "
        "|norm_delta| >= 2 reads ENRICHED/DEPLETED."
    )
    print("Report-only instrument (algorithms_adoption_sweep S3) — no writes, no gate.")


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--sources",
        default=",".join(DEFAULT_FAMILIES),
        help="comma list of hyperedge families (default: the hyper_spectral defaults)",
    )
    ap.add_argument(
        "--orders",
        default=",".join(str(k) for k in DEFAULT_ORDERS),
        help="comma list of overlap orders k (default 2,3)",
    )
    ap.add_argument("--runs", type=int, default=10, help="null replicates (probe protocol: 10)")
    ap.add_argument("--seed", type=int, default=42, help="null rng seed (probe protocol: 42)")
    ap.add_argument("--max-k", type=int, default=60, help="order cap (probe protocol: <=60)")
    ap.add_argument(
        "--k2-cap",
        type=int,
        default=300,
        help="k>=2 edge cap per family (giant-k families need a deliberate override)",
    )
    ap.add_argument(
        "--pairs-out",
        metavar="PATH",
        help="export the pair worklist (label1,label2,shared,relation) as CSV; "
        "requires exactly one family in --sources",
    )
    ap.add_argument(
        "--pairs-min",
        type=int,
        default=2,
        help="minimum shared members for --pairs-out rows (default 2)",
    )
    args = ap.parse_args(argv)

    orders = tuple(int(x) for x in args.orders.split(",") if x.strip())
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    if args.pairs_out and len(sources) != 1:
        print("--pairs-out requires exactly one family in --sources", file=sys.stderr)
        return 2

    # fail fast on an unknown family — load_incidence_arrow validates the
    # store, but a typo'd family would silently census an empty list
    raw = ha.load_incidence_arrow(sources)
    known = set(raw.column("edge_type").to_pylist()) if raw.num_rows else set()
    unknown = [s for s in sources if s not in known]
    if unknown:
        print(f"unknown families (not in the store): {unknown}", file=sys.stderr)
        return 2

    results = [
        run_family(
            s,
            orders=orders,
            runs=args.runs,
            seed=args.seed,
            max_k=args.max_k,
            k2_cap=args.k2_cap,
        )
        for s in sources
    ]
    _print_report(results)

    if args.pairs_out:
        family = sources[0]
        incidence = load_family(family, max_k=args.max_k, k2_cap=args.k2_cap)
        labels = sorted(incidence)  # deterministic pair order
        edges = [sorted(set(incidence[lbl])) for lbl in labels]
        shared = pair_shared_counts(edges)
        rows = []
        for (i, j), c in sorted(shared.items(), key=lambda kv: (-kv[1], kv[0])):
            if c < args.pairs_min:
                break
            ma, mb = set(edges[i]), set(edges[j])
            rows.append((labels[i], labels[j], len(ma), len(mb), c, relation_of(ma, mb)))
        out_path = Path(args.pairs_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as fh:
            fh.write("family,label1,label2,n1,n2,shared,relation\n")
            for lbl1, lbl2, n1, n2, c, rel in rows:
                fh.write(f"{family},{_csv(lbl1)},{_csv(lbl2)},{n1},{n2},{c},{rel}\n")
        print(f"\npairs-out: {len(rows)} rows (shared>={args.pairs_min}) → {out_path}")
    return 0


def _csv(field: str) -> str:
    """Minimal CSV quoting for labels (they carry commas)."""
    if "," in field or '"' in field:
        return '"' + field.replace('"', '""') + '"'
    return field


if __name__ == "__main__":
    raise SystemExit(_cli())
