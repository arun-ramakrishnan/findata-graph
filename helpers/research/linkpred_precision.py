#!/usr/bin/env python3
"""Link-prediction precision vs realized `acquired` edges (S4).

Scores the FROZEN candidate snapshot
(``bench_data/alpha_research/linkpred_snapshot.parquet``, pre-registered
in ``doc/local/evaluations/linkpred_precision_prereg.md``) against
``acquired`` edges realized AFTER each candidate generation's
``computed_at``. Circular pairs — deals already in the graph when the
candidates were computed — are excluded by construction and never
counted as hits.

Read-only; report-only. Re-run at each recompute-graph cycle's close
per the pre-registration's decision rule.

Usage:
    python3 helpers/research/linkpred_precision.py score
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402

SNAPSHOT = _REPO_ROOT / "bench_data" / "alpha_research" / "linkpred_snapshot.parquet"
PREREG = _REPO_ROOT / "doc" / "local" / "evaluations" / "linkpred_precision_prereg.md"
KS = (10, 25, 50)


def realized_deals(con) -> list[tuple[str, str, str]]:
    """(acquirer, target, deal_date) — every dated acquired edge."""
    return [
        (r["source"], r["target"], r["valid_from"])
        for r in con.execute(
            "SELECT source, target, valid_from FROM graph_edges "
            "WHERE edge_type = 'acquired' AND valid_from IS NOT NULL ORDER BY valid_from"
        ).fetchall()
    ]


def _stats_lines(scored: int, hits: dict[int, int], base_rate: float) -> list[str]:
    """Significance layer (algorithms_adoption_sweep S2): exact binomial
    P(X ≥ hits) per horizon against the candidate base rate, plus BH FDR
    across the three horizon p-values. Underpowered at small N by design
    (the pre-reg's ≥10-deal floor stands) — the numbers are recorded,
    never interpreted below that floor."""
    from scipy import stats as st

    lines: list[str] = []
    pvals: list[float] = []
    for k in KS:
        p = float(st.binomtest(hits[k], scored, p=base_rate, alternative="greater").pvalue)
        pvals.append(p)
        lines.append(f"  p (binomial ≥ base {base_rate:.4f}) @{k}: {p:.4f}")
    fdr = st.false_discovery_control(pvals, method="bh")
    lines.append(
        f"  BH-adjusted (over @{KS[0]}/@{KS[1]}/@{KS[2]}): " + " ".join(f"{p:.4f}" for p in fdr)
    )
    return lines


def _scored_deals(
    deals: list[tuple[str, str, str]],
    cand: dict[str, tuple[str, list[str]]],
) -> tuple[int, dict[int, int], int, int]:
    """Score realized deals against the acquirer's frozen candidate lists.

    Circular deals (deal date <= generation date — in-graph, not a
    prediction) and acquirers with no candidate row are counted as
    exclusions, not scored; the prereg's exclusion ledger.
    """
    scored = 0
    hits = {k: 0 for k in KS}
    skipped_circular = 0
    skipped_no_row = 0
    for acq, tgt, deal_date in deals:
        # the acquirer's candidate list is the predictive surface
        row = cand.get(acq)
        if row is None:
            skipped_no_row += 1
            continue
        gen_date, names = row
        gen_date = gen_date[:10] if gen_date else ""
        if deal_date <= gen_date:
            skipped_circular += 1
            continue
        scored += 1
        for k in KS:
            if tgt in names[:k]:
                hits[k] += 1
    return scored, hits, skipped_circular, skipped_no_row


def score() -> int:
    import duckdb

    con = connect(read_only=True)
    duck = duckdb.connect()
    try:
        duck.execute(
            f"CREATE VIEW snap AS SELECT * FROM read_parquet('{SNAPSHOT}')"  # noqa: S608  constant path
        )
        cand: dict[str, tuple[str, list[str]]] = {}
        for r in duck.execute(
            "SELECT entity_name, computed_at, candidates FROM snap ORDER BY computed_at DESC"
        ).fetchall():
            # keep the NEWEST generation per entity (later generations
            # supersede; the pre-registration keys on row computed_at)
            cand.setdefault(r[0], (r[1], [c["name"] for c in json.loads(r[2])]))
        deals = realized_deals(con)
        universe = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        base_rate = (
            sum(len(names) for _g, names in cand.values()) / (len(cand) * universe)
            if cand and universe
            else 0.0
        )
        scored, hits, skipped_circular, skipped_no_row = _scored_deals(deals, cand)
        print(f"realized deals since snapshot generations: {scored}")
        print(f"  excluded as circular (deal <= generation date): {skipped_circular}")
        print(f"  excluded (no candidate row for acquirer): {skipped_no_row}")
        print(f"  candidate base rate (mean list / universe {universe}): {base_rate:.4f}")
        for k in KS:
            pct = f"{hits[k] / scored * 100:.1f}%" if scored else "—"
            print(f"  precision@{k}: {hits[k]}/{scored} = {pct}")
        if scored and base_rate > 0:
            for line in _stats_lines(scored, hits, base_rate):
                print(line)
        if scored == 0:
            print(
                "\nNothing measurable yet — the pre-registration's outcome log "
                f"({PREREG.relative_to(_REPO_ROOT)}) records this run; re-run "
                "at each recompute-graph cycle's close."
            )
        return 0
    finally:
        duck.close()
        con.close()


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["score"], help="score the frozen snapshot vs realized deals")
    p.parse_args(argv)
    if not SNAPSHOT.exists():
        print(f"no snapshot at {SNAPSHOT} — freeze one first", file=sys.stderr)
        return 1
    return score()


if __name__ == "__main__":
    raise SystemExit(_cli())
