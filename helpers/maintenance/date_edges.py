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
- **Class D (edition issue dates)**: rows whose provenance names ONE
  source note — ``properties.resource`` (cited_in), a
  ``derive:relations:<note>.md`` ref, or a free-text
  ``properties.edition``/``note`` — and that note prints the
  publisher's issue date in its body (``ZERODHA MAR27,2026``,
  ``ZERODHA DEC 05, 2025``, or the bare ``SEP 30, 2026`` form).
  The edition's own printed date is the relation's earliest possible
  evidence date, so stamping it is conservative (same stance as Class
  B). Free-text edition strings resolve through the canonical
  ``helpers.core.edition_index`` index; an unresolvable string stays
  UNDATED and counted — never approximated. Dir-level
  ``derive:relations:<tree>`` refs stay Class C (undated).

Pre-existing ``valid_from`` values are NEVER overwritten. Dry-run
default: per-family census + date histogram; ``--apply`` is one
transaction; ``--revert <dump>`` restores the pre-apply state from the
JSON dump the dry-run/apply writes.

Usage:
    python3 helpers/maintenance/date_edges.py            # dry-run census (A+B)
    python3 helpers/maintenance/date_edges.py --editions # + Class D, all families
    python3 helpers/maintenance/date_edges.py --apply --dump <path>
    python3 helpers/maintenance/date_edges.py --revert <dump.json>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
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

#: The publisher's issue date is PRINTED IN THE EDITION BODY — the
#: frontmatter carries no date field at all (only ``generated.at``, and 80
#: of 104 Chatter notes share one backfill constant ``2026-08-15T00:00:00Z``
#: that would silently mis-date the whole corpus). Two printed shapes:
#: ``ZERODHA MAR27,2026`` / ``ZERODHA DEC 05, 2025`` (The Chatter, Points &
#: Figures) and the bare ``SEP 30, 2026`` form a handful carry instead.
EDITION_ISSUE_RE = re.compile(r"^(?:ZERODHA\s+)?([A-Z]{3})\s*([0-9]{1,2})\s*,\s*([0-9]{4})$", re.M)
#: Only the masthead region is scanned. A date-shaped line deeper in the
#: body is prose — an event/earnings/post date — not the edition's own
#: issue date, and stamping it would fabricate precision.
_ISSUE_SCAN_LINES = 80
_MONTHS = {
    name: i
    for i, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}
#: Properties fields that name an EDITION. Deliberately excludes ``note``/
#: ``title``: on ``doc_type: company`` rows those name a company note, not
#: an edition, and dating a relation from a company note's body would stamp
#: the wrong evidence class.
_EDITION_PROP_KEYS = ("edition", "as_of_edition")


def _parse_period_end(raw: str) -> str | None:
    """'31-MAR-2026' -> '2026-03-31' (ISO)."""
    try:
        return datetime.strptime(raw.strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def _ref_date(source_ref: str | None) -> str | None:
    if not source_ref:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", source_ref)
    return m.group(1) if m else None


def parse_issue_date(text: str) -> str | None:
    """The edition body's own printed issue date -> ISO, or None.

    Scans only the masthead (``_ISSUE_SCAN_LINES``); the FIRST match wins.
    An impossible calendar date is None, never a rolled-over neighbour
    (``FEB 30, 2026`` must not silently become 2026-03-02).
    """
    m = EDITION_ISSUE_RE.search("\n".join(text.splitlines()[:_ISSUE_SCAN_LINES]))
    if not m:
        return None
    month = _MONTHS.get(m.group(1))
    if month is None:
        return None
    try:
        return datetime(int(m.group(3)), month, int(m.group(2))).date().isoformat()
    except ValueError:
        return None


def edition_dates(vault: Path) -> dict[str, str]:
    """note stem -> printed issue date, over every source note in the vault.

    Costs one vault walk (the same one ``edition_index.source_note_index``
    does); both are needed to resolve free-text edition strings, and the
    stems double as the direct-index key.
    """
    from helpers.core import edition_index as ei  # noqa: PLC0415

    out: dict[str, str] = {}
    for tree in ei.source_trees(vault):
        for p in sorted(tree.rglob("*.md")):
            if p.name in ei.CHROME_FILES or "images" in p.parts:
                continue
            d = parse_issue_date(p.read_text(encoding="utf-8", errors="replace"))
            if d:
                out[p.stem] = d
    return out


def _note_stem_from_resource(resource: object) -> str | None:
    """``/findata/The_Chatter/X.md`` -> ``X``; non-note paths -> None."""
    if not isinstance(resource, str) or not resource.endswith(".md"):
        return None
    return Path(resource).stem


def _edition_date_for_row(
    source_ref: str | None,
    props: dict,
    dates: Mapping[str, str],
    index: Mapping[str, Path] | None,
    memo: dict[str, Path | None],
) -> str | None:
    """Class D evidence: the ONE source note this row came from.

    Three provenance shapes name a single edition — ``properties.resource``
    (``cited_in``), a ``derive:relations:<note>.md`` ref, and a free-text
    ``properties.edition``/``as_of_edition``. A dir-level
    ``derive:relations:<tree>`` ref names a whole tree, so it stays class C
    (undated), never guessed from the tree.
    """
    stem = _note_stem_from_resource(props.get("resource"))
    if stem and stem in dates:
        return dates[stem]
    if isinstance(source_ref, str) and source_ref.startswith("derive:relations:"):
        tail = source_ref[len("derive:relations:") :]
        if tail.endswith(".md"):
            stem = Path(tail).stem
            if stem in dates:
                return dates[stem]
    for key in _EDITION_PROP_KEYS:
        raw = props.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        stem = _note_stem_from_resource(raw)
        if stem and stem in dates:
            return dates[stem]
        if index is None:
            continue
        from helpers.core import edition_index as ei  # noqa: PLC0415

        hit = ei.resolve_edition_string(raw, dict(index), memo)
        if hit is not None and hit.stem in dates:
            return dates[hit.stem]
    return None


def _props_dict(raw: str | None) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except ValueError, TypeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def plan(conn, dates: Mapping[str, str] | None = None, index=None) -> tuple[list, dict]:
    """Compute the backfill plan: [(rowid, date)] + per-family census.

    Pure read; both dry-run and apply consume it. Pre-dated rows are
    skipped (never overwritten) and counted as ``already_dated``.

    ``dates`` (stem -> printed issue date, from :func:`edition_dates`)
    enables Class D and widens the family set to every edge type in the
    store; without it the plan covers Classes A and B only, exactly as
    before. Evidence precedence within a row is A, then B, then D — so a
    ref-embedded or filing date always beats an edition guess, and no
    previously-datable row changes value when Class D engages.
    """
    plan_rows: list[tuple[int, str]] = []
    census: dict[str, dict[str, int]] = {}
    if dates is None:
        families = (*CLASS_A_FAMILIES, *CLASS_B_FAMILIES)
    else:
        families = tuple(
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT edge_type FROM graph_edges ORDER BY edge_type"
            ).fetchall()
        )
    memo: dict[str, Path | None] = {}
    for et in families:
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
            row_props = _props_dict(props)
            if et in CLASS_A_FAMILIES:
                pe = row_props.get("period_end")
                if pe:
                    date = _parse_period_end(pe)
            elif et in CLASS_B_FAMILIES:
                date = _ref_date(source_ref)
            if date is None and dates is not None:
                date = _edition_date_for_row(source_ref, row_props, dates, index, memo)
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
    p.add_argument(
        "--editions",
        action="store_true",
        help=(
            "also date rows from their edition's PRINTED ISSUE DATE (Class D: "
            "properties.resource / derive:relations:<note>.md / properties.edition) "
            "over every edge family in the store"
        ),
    )
    p.add_argument(
        "--vault",
        default=str(_REPO_ROOT / "findata"),
        help="vault root for --editions (default: <repo>/findata)",
    )
    args = p.parse_args(argv)
    if args.revert:
        return revert(args.revert)
    con = connect()
    try:
        dates = None
        index = None
        if args.editions:
            vault = Path(args.vault)
            if not vault.is_dir():
                print(f"ERROR: vault not found: {vault}", file=sys.stderr)
                return 1
            from helpers.core import edition_index as ei  # noqa: PLC0415

            dates = edition_dates(vault)
            index = ei.source_note_index(vault)
            print(f"edition dates: {len(dates)} note(s) print an issue date in {vault}")
        plan_rows, census = plan(con, dates=dates, index=index)
        dates = [d for _rid, d in plan_rows]
        print("per-family census:")
        for et, c in census.items():
            print(
                f"  {et:16} undated {c['undated']:6} | datable {c['datable']:6} | "
                f"unresolvable {c['unresolvable']:5} | already_dated {c['already_dated']}"
            )
        tot_undated = sum(c["undated"] for c in census.values())
        tot_datable = sum(c["datable"] for c in census.values())
        print(
            f"  TOTAL            undated {tot_undated:6} | datable {tot_datable:6} | "
            f"residual {tot_undated - tot_datable:5}"
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
