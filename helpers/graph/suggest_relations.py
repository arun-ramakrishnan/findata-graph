#!/usr/bin/env python3
"""Link-prediction suggestions -> _pending_suggestions.txt (C2, 2026-08-18).

Closed-loop "suggested relations" with zero new UI:

    onager_link_prediction ranks MISSING pairs by neighbourhood similarity
    (shared co-mention / JV / competes / same-group neighbours)  ->
    this module filters (score floor, company-only endpoints, no existing
    typed edge of ANY kind, no prior identical suggestion)  ->
    JSONL entries appended to findata/_pending_suggestions.txt  ->
    a human accepts them through the H4 review workflow.

Population split (pending_relations_triage, 2026-08-25): suggestions live
in their OWN file, NOT the extraction-miss sidecar — mixing the two made
the triage queue look 3x its true size. Same JSONL contract (edge_type,
source, target_mention, quote, edition) plus C2 extras (origin, score,
method) so rows stay machine-distinguishable:

    {"edge_type": "suggested", "source": "Acme", "target_mention": "Beta",
     "quote": "", "edition": "link-prediction/jaccard/2026-08-18",
     "origin": "link_prediction", "score": 0.83, "method": "jaccard"}

``edge_type: "suggested"`` is deliberate: the projection cannot know WHICH
typed edge is missing — the human assigns one during triage.

Read-only by default (prints a table); ``--append`` writes the file.
Idempotency: re-running never duplicates — suggestions are deduped against
(a) every pair already present in ``graph_edges`` (any type, either
direction) and (b) suggestions already in the file.

Usage:
    python3 helpers/graph/suggest_relations.py                # dry-run, top 25
    python3 helpers/graph/suggest_relations.py --top 50 --min-score 0.4
    python3 helpers/graph/suggest_relations.py --append       # write file
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo root: helpers/graph/suggest_relations.py -> parents[2] (house
# bootstrap; mirrors context_pack.py so the script works as a subprocess).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import duckdb  # noqa: E402  # after sys.path bootstrap

from helpers.graph.algorithms import link_prediction  # noqa: E402
from helpers.graph.query import connect as duckdb_connect  # noqa: E402

DEFAULT_DUCKDB = _REPO_ROOT / "memory" / "graph.duckdb"
ORIGIN = "link_prediction"
SUGGESTED_EDGE_TYPE = "suggested"
# Own file since pending_relations_triage (2026-08-25): review candidates
# must not share the extraction-miss queue. Was extract_relations.
# SIDECAR_PATH (findata/_pending_relations.txt).
SUGGESTIONS_PATH = _REPO_ROOT / "findata" / "_pending_suggestions.txt"
SIDECAR_PATH = SUGGESTIONS_PATH  # back-compat name for callers/tests


@dataclass(frozen=True)
class Suggestion:
    """One predicted-but-absent relation, ready for the review queue."""

    source: str
    target: str
    score: float
    method: str
    edition: str

    def to_row(self) -> dict[str, object]:
        """The sidecar JSONL entry (Unresolved contract + C2 extras)."""
        return {
            "edge_type": SUGGESTED_EDGE_TYPE,
            "source": self.source,
            "target_mention": self.target,
            "quote": "",
            "edition": self.edition,
            "origin": ORIGIN,
            "score": round(self.score, 4),
            "method": self.method,
        }


def _company_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Names of company-kind nodes (suggestions only pair companies)."""
    return {r[0] for r in con.execute("SELECT name FROM v_company").fetchall()}


def existing_edge_pairs(conn: sqlite3.Connection | None = None) -> set[frozenset[str]]:
    """All (source, target) name pairs already in ``graph_edges``.

    Uses the house connection helper (static-checked rule); pass an open
    sqlite3 connection for tests.
    """
    if conn is None:
        from helpers.core.db import connect

        conn = connect()  # house helper (static-checked rule)
    rows = conn.execute("SELECT source, target FROM graph_edges").fetchall()
    return {frozenset((s, t)) for s, t in rows}


def prior_suggestion_pairs(path: Path = SIDECAR_PATH) -> set[frozenset[str]]:
    """Pairs already suggested in the sidecar (origin=link_prediction)."""
    pairs: set[frozenset[str]] = set()
    if not path.exists():
        return pairs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # malformed lines are the human's to clean (H4)
        if row.get("origin") == ORIGIN:
            pairs.add(frozenset((row.get("source", ""), row.get("target_mention", ""))))
    return pairs


def filter_suggestions(
    pairs: list[tuple[str, str, float]],
    *,
    existing_pairs: set[frozenset[str]],
    companies: set[str],
    top: int = 25,
    min_score: float = 0.3,
    companies_only: bool = True,
) -> list[tuple[str, str, float]]:
    """Apply the C2 acceptance filters to raw link-prediction pairs.

    Drops: below-floor scores, non-company endpoints (unless
    ``companies_only=False``), and pairs with any existing edge. Keeps at
    most ``top``, preserving the caller's (score-desc) order.
    """
    seen: set[frozenset[str]] = set()
    out: list[tuple[str, str, float]] = []
    for a, b, score in pairs:
        if len(out) >= top:
            break
        if score < min_score or not a or not b or a == b:
            continue
        key = frozenset((a, b))
        if key in seen or key in existing_pairs:
            continue
        if companies_only and (a not in companies or b not in companies):
            continue
        seen.add(key)
        out.append((a, b, score))
    return out


def suggest_relations(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    method: str = "jaccard",
    edge_types: list[str] | None = None,
    top: int = 25,
    min_score: float = 0.3,
    companies_only: bool = True,
    existing_pairs: set[frozenset[str]] | None = None,
) -> list[Suggestion]:
    """Predict missing relations and format them as review-queue entries.

    Pulls a headroom candidate list from ``link_prediction`` (top*8, so the
    filters can discard most of it) and returns at most ``top`` Suggestions.
    """
    own = False
    if con is None:
        # House connection (query.connect): attaches research.db as `fin`
        # and materialises the e_* cache — exactly what onager_link_prediction's
        # DB path expects (same pattern as algorithms.link_prediction).
        con = duckdb_connect(read_only=True)
        own = True
    try:
        companies = _company_names(con)
        raw = link_prediction(con, edge_types=edge_types, method=method, top=max(top * 8, top))
    finally:
        if own:
            con.close()
    if existing_pairs is None:
        existing_pairs = existing_edge_pairs()
    edition = f"link-prediction/{method}/{dt.date.today().isoformat()}"
    pairs = filter_suggestions(
        raw,
        existing_pairs=existing_pairs,
        companies=companies,
        top=top,
        min_score=min_score,
        companies_only=companies_only,
    )
    return [Suggestion(a, b, s, method, edition) for a, b, s in pairs]


def append_suggestions(
    suggestions: list[Suggestion],
    path: Path = SIDECAR_PATH,
    existing_pairs: set[frozenset[str]] | None = None,
) -> int:
    """Append Suggestions to the sidecar, deduped. Returns count written."""
    if not suggestions:
        return 0
    if existing_pairs is None:
        existing_pairs = existing_edge_pairs()
    known = prior_suggestion_pairs(path)
    fresh = [
        s
        for s in suggestions
        if frozenset((s.source, s.target)) not in known
        and frozenset((s.source, s.target)) not in existing_pairs
    ]
    if not fresh:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for s in fresh:
            f.write(json.dumps(s.to_row(), ensure_ascii=False) + "\n")
    return len(fresh)


def render(suggestions: list[Suggestion]) -> str:
    """Human-readable dry-run table."""
    if not suggestions:
        return "no suggestions passed the filters"
    lines = ["score   pair", "-" * 60]
    for s in suggestions:
        lines.append(f"{s.score:<6.3f}  {s.source}  <->  {s.target}")
    lines.append(f"({len(suggestions)} suggestions · review queue: --append)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Link-prediction relation suggestions (C2)")
    p.add_argument(
        "--method",
        default="jaccard",
        help="jaccard | adamic-adar | common-neighbors | pref-attach | resource-alloc",
    )
    p.add_argument(
        "--edge-types",
        default=None,
        help="comma-separated projection types (default: non-membership set)",
    )
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--min-score", type=float, default=0.3)
    p.add_argument(
        "--all-kinds",
        action="store_true",
        help="allow non-company endpoints (default: companies only)",
    )
    p.add_argument(
        "--append",
        action="store_true",
        help="append to findata/_pending_relations.txt (default: dry-run)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=SIDECAR_PATH,
        help="sidecar path (default: findata/_pending_relations.txt)",
    )
    args = p.parse_args(argv)
    if not DEFAULT_DUCKDB.exists():
        print(f"error: {DEFAULT_DUCKDB} not found (run make graph-rebuild first)", file=sys.stderr)
        return 2
    edge_types = args.edge_types.split(",") if args.edge_types else None
    suggestions = suggest_relations(
        method=args.method,
        edge_types=edge_types,
        top=args.top,
        min_score=args.min_score,
        companies_only=not args.all_kinds,
    )
    if args.append:
        n = append_suggestions(suggestions, path=args.out)
        print(
            f"appended {n} suggestions to {args.out}"
            + ("" if n == len(suggestions) else f" (of {len(suggestions)}; rest deduped)")
        )
    else:
        print(render(suggestions))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
