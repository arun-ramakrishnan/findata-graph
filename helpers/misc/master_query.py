#!/usr/bin/env python3
"""
master_query — one federated query over every search leg this repo keeps.

The agent-facing GRAND UNIFIED surface: a session asks once and gets
per-leg structured results instead of picking (and remembering) the right
index CLI first. Unification lives in the CLIENT — the settled #229
doctrine: fan out over the legs in parallel, render GROUPED with
per-leg ranking, and never blend the legs' score spaces (BM25,
RRF-fused, corpus pointers and report rows are incomparable). The one
cross-leg blend offered is rank-based (`--flat`): RRF over each leg's
hit RANKS, leg-tagged, as a "best overall" reading order.

Legs (default six, `--legs all` adds the stateless structure tools):

    docs    doc/ knowledge index (doc_query backend, hybrid)
    notes   findata vault (note_search, per-note hybrid)
    scripts helpers/tests/make/Mojo/TS intent index (script_query backend)
    memory  harness-memory pools zcode/prime/opencode (memory_query core)
    convo   harvested past-session corpus pointers (convo_query backend)
    gates   gate-run reports (the search_tui reports lane)
    code    ripwire --for= structure (stateless; --legs all)
    literal rg (stateless; --legs all)

Every leg degrades independently — a missing/stale sidecar surfaces as
that leg's status, never as a failure of the whole query (the house
"every check runs even if one fails" doctrine). Freshness REPORTING
lives here (per-leg status + index ages); the freshness GATES stay
where the contracts live: `make search-fresh` (docs/notes/scripts/
memory) and `make convo-fresh` (corpus chain); the gate index refreshes
itself before every query.

Usage:
    python3 helpers/misc/master_query.py "embed cache"
    python3 helpers/misc/master_query.py "stg refresh" --legs memory,convo
    python3 helpers/misc/master_query.py "integrity" --flat --limit 4
    python3 helpers/misc/master_query.py "graph rebuild" --json

Exit codes: 0 at least one leg actually ran (hits or an honest "0 hits"),
1 no leg ran (every sidecar missing / every leg errored), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

# Repo root: helpers/misc/master_query.py -> parents[2]. Must be on
# sys.path BEFORE the search_tui import (house bootstrap) so the script
# works as a subprocess the same way it works under pytest.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.misc import search_tui as st  # noqa: E402

RRF_K = 60

# Master leg -> search_tui lane backend. "gates" is the TUI reports lane
# under its corpus-facing name; both names are accepted in --legs.
_LEGS: dict[str, str] = {
    "docs": "docs",
    "notes": "notes",
    "scripts": "scripts",
    "memory": "memory",
    "convo": "convo",
    "gates": "reports",
    "reports": "reports",  # alias
    "code": "code",
    "literal": "literal",
}
DEFAULT_LEGS = ("docs", "notes", "scripts", "memory", "convo", "gates")
ALL_LEGS = ("docs", "notes", "scripts", "memory", "convo", "gates", "code", "literal")

_ANSWERED = re.compile(r"^\d+ hits")


def parse_legs(spec: str) -> list[str]:
    """--legs value -> deduped leg list (caller order preserved).

    'all' expands to every leg incl. the stateless code/literal tools.
    Unknown names raise ValueError (usage error, exit 2)."""
    names = [n.strip().lower() for n in spec.split(",") if n.strip()]
    if names == ["all"]:
        return list(ALL_LEGS)
    legs: list[str] = []
    for n in names:
        if n not in _LEGS:
            raise ValueError(
                f"unknown leg {n!r} (known: {', '.join(DEFAULT_LEGS)}, code, literal, all)"
            )
        if n not in legs:
            legs.append(n)
    return legs


def _leg_priority(leg: str) -> int:
    """Tie-break priority for the flat order (canonical leg order; the
    reports alias ranks with gates)."""
    canonical = "gates" if leg == "reports" else leg
    return DEFAULT_LEGS.index(canonical) if canonical in DEFAULT_LEGS else len(DEFAULT_LEGS)


def fan_out(
    query: str,
    legs: list[str],
    limit: int,
    mode: str = "hybrid",
    lane_runner=None,
    parallel: bool = True,
) -> dict[str, tuple[list, str]]:
    """Run every leg, return {leg: (hits, status)} keyed by caller's name.

    ``lane_runner`` defaults to search_tui.run_lane (the terminal-free,
    unit-tested adapters — the master CLI owns NO backend logic). Legs
    run in a thread pool by default: the corpus legs mix subprocess CLIs
    and in-process embedders, so wall-clock is the slowest leg, not the
    sum. A raising leg degrades to ([], "error: ...")."""
    runner = lane_runner or st.run_lane
    ordered: list[str] = []
    for leg in legs:
        if leg not in ordered:
            ordered.append(leg)

    def _one(leg: str) -> tuple[str, tuple[list, str]]:
        try:
            hits, status = runner(_LEGS[leg], query, limit, mode)
            return leg, (hits, status)
        except Exception as exc:  # noqa: BLE001  # one leg must not fail the fan-out
            return leg, ([], f"error: {type(exc).__name__}: {exc}")

    out: dict[str, tuple[list, str]] = {}
    if parallel and len(ordered) > 1:
        with ThreadPoolExecutor(max_workers=len(ordered)) as pool:
            for leg, result in pool.map(_one, ordered):
                out[leg] = result
    else:
        for leg in ordered:
            leg, result = _one(leg)
            out[leg] = result
    return out


def rrf_flat(per_leg: dict[str, tuple[list, str]], limit: int) -> list[dict]:
    """Cross-leg reading order: RRF over each leg's hit RANKS.

    Rank-based, not score-based — the legs' score spaces (bm25, fused
    RRF, similarity) are incomparable, but "how highly did this rank in
    its own leg" is not. Ties break by canonical leg order (repo
    surfaces before harness surfaces before stateless tools)."""
    scored: list[tuple[float, int, str, object]] = []
    for leg, (hits, _status) in per_leg.items():
        pri = _leg_priority(leg)
        for rank, hit in enumerate(hits):
            scored.append((1.0 / (RRF_K + rank + 1), pri, leg, hit))
    scored.sort(key=lambda t: (-t[0], t[1]))
    flat: list[dict] = []
    for score, _pri, leg, hit in scored[:limit]:
        entry = asdict(hit)
        entry["leg"] = leg
        entry["rrf"] = round(score, 6)
        flat.append(entry)
    return flat


def _leg_answered(status: str) -> bool:
    """True when the leg actually ran (hit count line, incl. '0 hits')
    rather than degrading (missing sidecar / error / empty query)."""
    return _ANSWERED.match(status) is not None


def render(per_leg: dict[str, tuple[list, str]], ages: str) -> None:
    """Grouped default: one section per leg, status line, then hits."""
    print(f"# master search — index ages: {ages}")
    for leg, (hits, status) in per_leg.items():
        print(f"\n## {leg}  ({status})")
        if not hits:
            print("    (no hits)")
            continue
        for h in hits:
            loc = h.path if h.line is None else f"{h.path}:{h.line}"
            head = f" [{h.score:.4f}]" if isinstance(h.score, (int, float)) else ""
            title = f" {h.title}" if h.title else ""
            kind = f" {h.kind}" if h.kind else ""
            print(f"  {loc}{head}{kind}{title}"[:180])
            if h.snippet:
                print(f"      {h.snippet[:140]}")


def render_flat(flat: list[dict]) -> None:
    """--flat: one interleave, each row leg-tagged."""
    for r in flat:
        loc = r["path"] if r.get("line") is None else f"{r['path']}:{r['line']}"
        print(f"[{r['leg']:>7}] {loc}  [{r['rrf']:.6f}]")
        if r.get("snippet"):
            print(f"           {r['snippet'][:140]}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("query", nargs="?", default="", help="free-text query")
    p.add_argument(
        "--legs",
        default=",".join(DEFAULT_LEGS),
        help=f"comma list: {', '.join(DEFAULT_LEGS)}, code, literal, all "
        "(default: the six corpus legs)",
    )
    p.add_argument("--limit", type=int, default=4, help="hits per leg (default 4)")
    p.add_argument(
        "--bm25", action="store_true", help="lexical leg only (skip cosine in hybrid legs)"
    )
    p.add_argument(
        "--flat", action="store_true", help="also emit a rank-RRF cross-leg reading order"
    )
    p.add_argument(
        "--json", action="store_true", dest="as_json", help="structured output (adds flat when --flat)"
    )
    p.add_argument("--serial", action="store_true", help="run legs sequentially (debug)")
    args = p.parse_args(argv)

    try:
        legs = parse_legs(args.legs)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    mode = "bm25" if args.bm25 else "hybrid"
    per_leg = fan_out(
        args.query, legs, max(1, min(args.limit, 20)), mode, parallel=not args.serial
    )
    flat = rrf_flat(per_leg, args.limit) if args.flat else []
    answered = any(_leg_answered(status) for _h, status in per_leg.values())

    if args.as_json:
        payload = {
            "query": args.query,
            "mode": mode,
            "index_ages": st.index_ages(_REPO_ROOT),
            "legs": {
                leg: {"status": status, "count": len(hits), "results": [asdict(h) for h in hits]}
                for leg, (hits, status) in per_leg.items()
            },
        }
        if args.flat:
            payload["flat"] = flat
        print(json.dumps(payload, indent=2))
        return 0 if answered else 1

    render(per_leg, st.index_ages(_REPO_ROOT))
    if args.flat and flat:
        print("\n## flat (rank-RRF reading order)")
        render_flat(flat)
    if not any(hits for hits, _s in per_leg.values()):
        print(f"\n(no hits anywhere for {args.query!r})", file=sys.stderr)
    return 0 if answered else 1


if __name__ == "__main__":
    sys.exit(main())
