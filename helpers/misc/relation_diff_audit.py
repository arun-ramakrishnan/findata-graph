#!/usr/bin/env python3
"""Diff two extract_relations counts-JSON files (E1 diff-audit harness).

`extract_relations.py --counts-json before.json` and `--counts-json
after.json` capture per-edge-type totals over the same corpus. This script
prints a per-family delta table so a pattern change can be audited BEFORE it
is applied to graph_edges (dry-run only; nothing here touches the DB).

Usage:
    python3 helpers/misc/relation_diff_audit.py before.json after.json

Exit code 0 always (report-only); use --fail-on-regression to exit 1 when any
family LOSES edges (guard against pattern edits breaking v1 coverage).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_counts(path: str | Path) -> dict[str, int]:
    """Load the per_type map from a counts JSON (empty map if no matches)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    per_type = data.get("per_type", {})
    if not isinstance(per_type, dict):
        raise SystemExit(f"{path}: 'per_type' is not an object")
    return {str(k): int(v) for k, v in per_type.items()}


def diff_counts(before: dict[str, int], after: dict[str, int]) -> list[tuple[str, int, int, int]]:
    """Return sorted (edge_type, before, after, delta) rows."""
    types = sorted(set(before) | set(after))
    return [
        (t, before.get(t, 0), after.get(t, 0), after.get(t, 0) - before.get(t, 0)) for t in types
    ]


def format_table(rows: list[tuple[str, int, int, int]]) -> str:
    width = max((len(t) for t, *_ in rows), default=10) + 2
    lines = [
        f"{'edge_type':<{width}}{'before':>8}{'after':>8}{'delta':>8}",
        "-" * (width + 24),
    ]
    for t, b, a, d in rows:
        sign = "+" if d > 0 else ""
        lines.append(f"{t:<{width}}{b:>8}{a:>8}{sign + str(d):>8}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("before", help="counts JSON from BEFORE the change")
    p.add_argument("after", help="counts JSON from AFTER the change")
    p.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="exit 1 if any edge type lost edges",
    )
    args = p.parse_args(argv)

    rows = diff_counts(load_counts(args.before), load_counts(args.after))
    print(format_table(rows))
    regressions = [r for r in rows if r[3] < 0]
    if args.fail_on_regression and regressions:
        print(
            f"REGRESSION: {len(regressions)} edge type(s) lost edges: "
            f"{', '.join(r[0] for r in regressions)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
