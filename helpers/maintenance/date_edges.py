#!/usr/bin/env python3
"""Backfill ``graph_edges.valid_from`` from per-family provenance
(graph_alpha_lane S1 — the leak-free testing pre-requisite).

Evidence classes, each machine-resolvable — nothing fabricated:

- **Class A (RPT filings)**: ``same_group`` / ``supplier_to`` /
  ``subsidiary_of`` rows with ``vigil:rpt:*`` source_refs carry
  ``properties.period_end`` ("31-MAR-2026") — the filing period's own
  end date. ~33.6k rows, 96-99% coverage.
- **Class B (ref-embedded dates)**: ``competes_with``
  (``yfinance:industry:2026-08-25``, ``coinfer:<date>:*``) and
  ``semantic_peer`` (``embeddings:...:<date>``) carry the derive-batch
  date in the ref. Stamped as the EVIDENCE date (conservative: the
  relation may predate the derive, never the reverse — the as-of study
  biases toward missing signal, never fake signal).
- **Class C (dir-level derive refs)**: ``co_mentioned_in`` etc. ref a
  vault DIRECTORY whose rows carry no per-row date — left UNDATED and
  counted (fabricating precision is the failure mode this lane exists
  to avoid).

Pre-existing ``valid_from`` values are NEVER overwritten. Dry-run
default: per-family census + date histogram; ``--apply`` is one
transaction; ``--revert <dump>`` restores the pre-apply state from the
JSON dump the dry-run/apply writes.

Usage:
    python3 helpers/maintenance/date_edges.py            # dry-run census
    python3 helpers/maintenance/date_edges.py --apply --dump <path>
    python3 helpers/maintenance/date_edges.py --revert <dump.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402

#: Class A families: RPT rows dated from properties.period_end.
CLASS_A_FAMILIES = ("same_group", "supplier_to", "subsidiary_of")
#: Class B families: date embedded in source_ref (first ISO date match).
CLASS_B_FAMILIES = ("competes_with", "semantic_peer")


def _parse_period_end(raw: str) -> str | None:
    """'31-MAR-2026' -> '2026-03-31' (ISO)."""
    try:
        return datetime.strptime(raw.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def _ref_date(source_ref: str | None) -> str | None:
    if not source_ref:
        return None
    import re

    m = re.search(r"(\d{4}-\d{2}-\d{2})", source_ref)
    return m.group(1) if m else None


def plan(conn) -> tuple[list[tuple[int, str]], dict]:
    """Compute the backfill plan: [(rowid, date)] + per-family census.

    Pure read; both dry-run and apply consume it. Pre-dated rows are
    skipped (never overwritten) and counted as ``already_dated``.
    """
    plan_rows: list[tuple[int, str]] = []
    census: dict[str, dict[str, int]] = {}
    for et in (*CLASS_A_FAMILIES, *CLASS_B_FAMILIES):
        c = census.setdefault(
            et,
            {"undated": 0, "datable": 0, "unresolvable": 0, "already_dated": 0},
        )
        for rowid, source_ref, props, valid_from in conn.execute(
            "SELECT rowid, source_ref, properties, valid_from FROM graph_edges WHERE edge_type = ?",
            (et,),
        ).fetchall():
            if valid_from is not None:
                c["already_dated"] += 1
                continue
            c["undated"] += 1
            date = None
            if et in CLASS_A_FAMILIES:
                try:
                    pe = json.loads(props or "{}").get("period_end")
                except ValueError, TypeError:
                    pe = None
                if pe:
                    date = _parse_period_end(pe)
            else:
                date = _ref_date(source_ref)
            if date:
                c["datable"] += 1
                plan_rows.append((rowid, date))
            else:
                c["unresolvable"] += 1
    return plan_rows, census


def _histogram(dates: list[str]) -> dict[str, int]:
    """Year-month buckets, sorted — the shape check before apply."""
    buckets: dict[str, int] = {}
    for d in dates:
        buckets[d[:7]] = buckets.get(d[:7], 0) + 1
    return dict(sorted(buckets.items()))


def revert(dump_path: str, conn=None) -> int:
    """Restore valid_from (or NULL) for the rows recorded in the dump."""
    own = conn is None
    if own:
        conn = connect()
    try:
        payload = json.loads(Path(dump_path).read_text())
        with conn:
            for rowid, old in payload["rows"]:
                conn.execute(
                    "UPDATE graph_edges SET valid_from = ? WHERE rowid = ?",
                    (old, rowid),
                )
        print(f"revert: restored {len(payload['rows'])} row(s) from {dump_path}")
        return 0
    finally:
        if own:
            conn.close()


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--apply", action="store_true", help="write the backfill (default: dry-run census)"
    )
    p.add_argument(
        "--dump",
        default=None,
        help="path for the pre-apply state dump (required with --apply; also the --revert input)",
    )
    p.add_argument("--revert", default=None, metavar="DUMP", help="restore valid_from from a dump")
    args = p.parse_args(argv)
    if args.revert:
        return revert(args.revert)
    con = connect()
    try:
        plan_rows, census = plan(con)
        dates = [d for _rid, d in plan_rows]
        print("per-family census:")
        for et, c in census.items():
            print(
                f"  {et:16} undated {c['undated']:6} | datable {c['datable']:6} | "
                f"unresolvable {c['unresolvable']:5} | already_dated {c['already_dated']}"
            )
        hist = _histogram(dates)
        print(f"date histogram ({len(dates)} rows, year-month):")
        for ym, n in list(hist.items())[:40]:
            print(f"  {ym}: {n}")
        if not args.apply:
            print(f"DRY-RUN: {len(plan_rows)} row(s) would be dated; pass --apply --dump <path>")
            return 0
        if not args.dump:
            p.error("--apply requires --dump <path> (the revert input)")
        dump_payload = {
            "rows": [[rowid, None] for rowid, _d in plan_rows]  # pre-apply state: all NULL
        }
        Path(args.dump).write_text(json.dumps(dump_payload))
        with con:
            con.executemany(
                "UPDATE graph_edges SET valid_from = ? WHERE rowid = ?",
                [(d, rowid) for rowid, d in plan_rows],
            )
        print(f"APPLY: dated {len(plan_rows)} row(s); pre-state dumped to {args.dump}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(_cli())
