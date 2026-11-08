#!/usr/bin/env python3
"""Co-supply peer lane — buyer-pair shared-supplier scoring (co_supply_peer_lane S1).

The full-incidence census (hyper_motifs, 2026-11-08) measured the supply
family ENRICHED at both orders (k=2 +54.6 / k=3 +59.6; 3,196 pairs share
4+ suppliers) while every existing consumer reads each buyer's portfolio
individually. This lane adds the cross-edge read: score (buyer A, buyer
B) pairs by how many suppliers they share.

Scoring: shared count (hyper_motifs.pair_shared_counts — the census
counting core, no second implementation) normalized as
``shared / min(n1, n2)`` — a Jaccard-style floor that stops the largest
portfolios from trivially dominating. Bands: shared >= 4 is the
high-conviction band the census identified (the mass the null says is
real structure); 2-3 is the watch band.

Report-only, no writes, no gate. Promotion (context-pack wiring) is
prereg-gated per the parent proposal's S2 — do not read ranking output
as validated signal until that bank runs.

Usage:
    python3 helpers/graph/co_supply.py                       # top 100, all bands
    python3 helpers/graph/co_supply.py --band 4              # high-conviction only
    python3 helpers/graph/co_supply.py --min-shared 3 --top 50
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph import hyper_motifs as hm  # noqa: E402
from helpers.graph.hyper_spectral import load_family  # noqa: E402

#: shared >= BAND reads high-conviction (the census's enriched mass).
BAND = 4
DEFAULT_TOP = 100
DEFAULT_MIN_SHARED = 2
#: the supply family carries 547 k>=2 edges — a deliberate override of
#: the probe protocol's 300 default (the census measured the cost:
#: ~30k pair-rows, trivial). Same posture as the census run.
K2_CAP = 1200


def load_buyers(*, k2_cap: int = K2_CAP) -> dict[str, list[str]]:
    """The supply family under the house guard protocol: label = buyer,
    members = its suppliers (``hyperedge_types.py`` semantics)."""
    return load_family("supply", max_k=60, k2_cap=k2_cap)


def score_pairs(
    buyers: dict[str, list[str]],
    *,
    min_shared: int = DEFAULT_MIN_SHARED,
    band: int = BAND,
) -> list[dict[str, Any]]:
    """Ranked (buyer, buyer) pair rows: shared count, normalized score, band.

    Deterministic: labels sorted, pairs ordered by shared desc then
    lexicographic. ``score = shared / min(n1, n2)`` in [0, 1].
    """
    labels = sorted(buyers)
    edges = [sorted(set(buyers[lbl])) for lbl in labels]
    shared = hm.pair_shared_counts(edges)
    rows: list[dict[str, Any]] = []
    for (i, j), c in shared.items():
        if c < min_shared:
            continue
        n1, n2 = len(edges[i]), len(edges[j])
        rows.append(
            {
                "buyer1": labels[i],
                "buyer2": labels[j],
                "n1": n1,
                "n2": n2,
                "shared": c,
                "score": round(c / min(n1, n2), 4),
                "band": "high" if c >= band else "watch",
            }
        )
    rows.sort(key=lambda r: (-r["shared"], r["buyer1"], r["buyer2"]))
    return rows


def _print_report(rows: list[dict[str, Any]], *, top: int, n_buyers: int, n_suppliers: int) -> None:
    high = [r for r in rows if r["band"] == "high"]
    watch = [r for r in rows if r["band"] == "watch"]
    print(
        f"co-supply peers — {n_buyers} buyers / {n_suppliers} suppliers: "
        f"{len(rows)} pairs sharing >={DEFAULT_MIN_SHARED} suppliers "
        f"(high band >={BAND}: {len(high)}, watch: {len(watch)})"
    )
    print(f"{'rank':>4} {'shared':>6} {'score':>6}  buyer1 (n1) × buyer2 (n2)")
    for rank, r in enumerate(rows[:top], 1):
        b1 = r["buyer1"].removeprefix("supply:")
        b2 = r["buyer2"].removeprefix("supply:")
        print(f"{rank:>4} {r['shared']:>6} {r['score']:>6.2f}  {b1} ({r['n1']}) × {b2} ({r['n2']})")
    if len(rows) > top:
        print(f"… {len(rows) - top} more (raise --top)")
    print(
        "\nReport-only (co_supply_peer_lane S1) — promotion is prereg-gated "
        "on the parent proposal's S2 eval bank; this ranking is not validated signal."
    )


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--top", type=int, default=DEFAULT_TOP, help="rows to print (default 100)")
    ap.add_argument(
        "--min-shared",
        type=int,
        default=DEFAULT_MIN_SHARED,
        help="minimum shared suppliers to include (default 2)",
    )
    ap.add_argument(
        "--band",
        type=int,
        default=BAND,
        help="shared >= band reads high-conviction (default 4)",
    )
    args = ap.parse_args(argv)

    buyers = load_buyers()
    if not buyers:
        print("supply family empty or absent — nothing to score", file=sys.stderr)
        return 1
    rows = score_pairs(buyers, min_shared=args.min_shared, band=args.band)
    n_suppliers = len({s for members in buyers.values() for s in members})
    _print_report(rows, top=args.top, n_buyers=len(buyers), n_suppliers=n_suppliers)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
