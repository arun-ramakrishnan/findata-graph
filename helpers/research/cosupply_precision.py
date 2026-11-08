#!/usr/bin/env python3
"""Co-supply precision vs realized co-mention (co_supply_peer_lane S2).

Scores the FROZEN co-supply pair snapshot
(``bench_data/alpha_research/cosupply_snapshot.csv``, pre-registered in
``doc/local/evaluations/cosupply_precision_prereg.md``) against
realized co-mention events: two snapshot buyers co-occurring in a
NEWLY staged edition (``co_mention_editions`` title with staging
created_at strictly after the freeze AND never staged pre-freeze —
re-staging an old edition is not a realization).

Read-only; report-only. Re-run at each derive-co-mentions cycle's
close per the pre-registration's decision rule.

Usage:
    python3 helpers/research/cosupply_precision.py freeze   # snapshot the pair table
    python3 helpers/research/cosupply_precision.py score    # score vs realizations
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402

SNAPSHOT = _REPO_ROOT / "bench_data" / "alpha_research" / "cosupply_snapshot.csv"
PREREG = _REPO_ROOT / "doc" / "local" / "evaluations" / "cosupply_precision_prereg.md"
KS = (10, 25, 50)


def freeze() -> int:
    """Snapshot the live co-supply pair table (min_shared=2) with a
    freeze stamp. Re-freezing is a NEW instrument generation — the
    prereg's as-of discipline keys on each row's computed_at."""
    import helpers.graph.co_supply as cs

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    buyers = cs.load_buyers()
    rows = cs.score_pairs(buyers, min_shared=2)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["buyer1", "buyer2", "shared", "score", "computed_at"])
        for r in rows:
            w.writerow(
                [
                    r["buyer1"].removeprefix("supply:"),
                    r["buyer2"].removeprefix("supply:"),
                    r["shared"],
                    r["score"],
                    now,
                ]
            )
    print(f"froze {len(rows)} pairs / {len(buyers)} buyers at {now} → {SNAPSHOT}")
    print(f"prereg: {PREREG.relative_to(_REPO_ROOT)} (this freeze = the instrument generation)")
    return 0


def load_snapshot() -> tuple[list[dict], str]:
    with SNAPSHOT.open() as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"empty snapshot: {SNAPSHOT} — run freeze first")
    stamps = {r["computed_at"] for r in rows}
    return rows, sorted(stamps)[0]


def ranked_candidates(rows: list[dict]) -> dict[str, list[str]]:
    """Per-buyer candidate list, deterministic from the FROZEN pairs:
    rank by shared desc, score desc, name asc (the prereg's frozen
    ranking contract)."""
    pairs = [(r["buyer1"], r["buyer2"], int(r["shared"]), float(r["score"])) for r in rows]
    out: dict[str, list[str]] = {}
    for a, b, shared, score in sorted(pairs, key=lambda p: (-p[2], -p[3], p[0], p[1])):
        out.setdefault(a, []).append(b)
        out.setdefault(b, []).append(a)
    return out


def _alias_map() -> dict[str, str]:
    from helpers.core.entity_identity import fold_registry

    return fold_registry()


def _parse(ts: str) -> datetime:
    """Parse staging (``YYYY-MM-DD HH:MM:SS``) and freeze (ISO ``...Z``)
    stamps onto one clock — string comparison mis-orders the ``T`` vs
    space separators, so the as-of guard parses instead."""
    return datetime.fromisoformat(ts.strip().replace(" ", "T").replace("Z", "+00:00")).replace(
        tzinfo=None
    )


def realized_pairs(con, freeze_stamp: str) -> tuple[set[tuple[str, str]], int]:
    """Distinct unordered buyer pairs co-occurring in NEWLY staged
    editions (created_at > freeze, title never staged pre-freeze).
    Returns (pairs, n_new_titles)."""
    freeze_dt = _parse(freeze_stamp)
    rows = con.execute(
        "SELECT DISTINCT edition_title, created_at FROM co_mention_editions"
    ).fetchall()
    pre = {r["edition_title"] for r in rows if _parse(r["created_at"]) <= freeze_dt}
    new_titles = {
        r["edition_title"]
        for r in rows
        if _parse(r["created_at"]) > freeze_dt and r["edition_title"] not in pre
    }
    pairs: set[tuple[str, str]] = set()
    for title in new_titles:
        members = [
            r["entity_name"]
            for r in con.execute(
                "SELECT entity_name FROM co_mention_editions WHERE edition_title = ?",
                (title,),
            ).fetchall()
        ]
        for i, a in enumerate(sorted(members)):
            for b in members[i + 1 :]:
                if a == b:
                    continue
                pairs.add((a, b) if a < b else (b, a))
    return pairs, len(new_titles)


def _precision_counts(
    measured: list[tuple[str, str]],
    cands: dict[str, list[str]],
    universe: list[str],
) -> tuple[dict[int, int], dict[int, float]]:
    """Per-K hit counts and pooled base rates (prereg decision rule).

    Pooled base rate: per event, min(K, |A's list|)/(N-1) — the chance
    the partner lands in A's top-K under random pairing.
    """
    hits = {k: 0 for k in KS}
    base = {k: 0.0 for k in KS}
    for a, b in measured:
        for k in KS:
            la, lb = cands.get(a, []), cands.get(b, [])
            hit = (a in lb[:k]) or (b in la[:k])
            if hit:
                hits[k] += 1
            base[k] += min(k, len(la)) / (len(universe) - 1)
    n = len(measured)
    pooled = {k: (base[k] / n if n else 0.0) for k in KS}
    return hits, pooled


def score() -> int:
    from scipy import stats as st

    rows, freeze_stamp = load_snapshot()
    universe = sorted({r["buyer1"] for r in rows} | {r["buyer2"] for r in rows})
    cands = ranked_candidates(rows)

    # alias exclusion (prereg instrument): same fold canonical → not a pair
    folds = _alias_map()
    canon = lambda n: folds.get(n.casefold(), n)  # noqa: E731
    excluded_alias = {p for p in universe if canon(p) != p}

    con = connect(read_only=True)
    try:
        realized, n_titles = realized_pairs(con, freeze_stamp)
    finally:
        con.close()

    # keep pairs whose BOTH sides are in the universe and not alias-routed together
    measured: list[tuple[str, str]] = []
    alias_pairs = 0
    for a, b in sorted(realized):
        # prereg contract: BOTH buyers must be in the snapshot universe
        if a not in cands or b not in cands:
            continue
        if canon(a) == canon(b):
            alias_pairs += 1
            continue
        measured.append((a, b))

    n = len(measured)
    hits, pooled = _precision_counts(measured, cands, universe)

    print(f"snapshot: {len(rows)} frozen pairs / {len(universe)} buyers @ {freeze_stamp}")
    print(
        f"realized: {n} new pairs over {n_titles} newly staged editions "
        f"(alias-routed pairs excluded: {alias_pairs}; "
        f"alias-routed buyers in universe: {len(excluded_alias)})"
    )
    pvals = []
    for k in KS:
        pct = f"{hits[k] / n * 100:.1f}%" if n else "—"
        print(
            f"  precision@{k}: {hits[k]}/{n} = {pct}  (base {pooled[k]:.4f}, "
            f"lift {'—' if not pooled[k] else f'{hits[k] / n / pooled[k]:.2f}x' if n else '—'})"
        )
        if n and pooled[k] > 0:
            pvals.append(float(st.binomtest(hits[k], n, p=pooled[k], alternative="greater").pvalue))
        else:
            pvals.append(float("nan"))
    if n and all(p == p for p in pvals):
        fdr = st.false_discovery_control(pvals, method="bh")
        print("  BH-adjusted (over @10/@25/@50): " + " ".join(f"{p:.4f}" for p in fdr))
    if n == 0:
        print(
            "\nNothing measurable yet — the pre-registration's outcome log "
            f"({PREREG.relative_to(_REPO_ROOT)}) records this run; re-run "
            "at each derive-co-mentions cycle's close."
        )
    return 0


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["freeze", "score"])
    args = p.parse_args(argv)
    if args.command == "freeze":
        return freeze()
    if not SNAPSHOT.exists():
        print(f"no snapshot at {SNAPSHOT} — run freeze first", file=sys.stderr)
        return 1
    return score()


if __name__ == "__main__":
    raise SystemExit(_cli())
