#!/usr/bin/env python3
"""Derive ``listed_in`` edges (company -> country) from exchange tickers.

Country layer C1 (doc/improvements/proposals/country_layer_institution_lanes.md).
Geography is derived from the one authoritative signal — the exchange
ticker suffix on ``entities.ticker`` — never from chatter (country
mentions are noise-classified by ``noise_target`` at write time) and
never from the note tag lists (the 2026-09-09 census found the three
carriers disagree: 971 geography/india tag rows vs 95 geography: india
frontmatter keys vs 850 India-exchange tickers).

Semantics: listed_in is the HOME market (primary listing country), not
the ADR venue. Yahoo-style plain symbols are the US listing by default;
the audited foreign ADR/OTC lines carry explicit overrides (DEO -> uk,
BABA -> china, ...) so an Alibaba never lands in the usa bucket.

Conservative by design:
  * The suffix map is closed. An unmapped dotted suffix is never guessed
    — it lands in the worklist with reason ``unmapped_suffix``.
  * A company without a ticker is never guessed either — worklist with
    reason ``no_ticker`` and its current geography/* tag as a HINT
    column (parse_newsletter hardcoded geography/india pre-#215, so the
    hint is weak evidence, not an assignment; the decision is human).
  * Country entity names equal the geography tag vocabulary (india,
    usa, south_korea, ...) so the C2 convergence stays mechanical.
    Countries are bare rows — no notes, same shape as institutions.

Idempotent via UNIQUE(source, target, edge_type) — re-running is safe.
A company whose ticker LATER changes country keeps the stale edge (the
derive-* convention: INSERT OR IGNORE, never delete); the ticker trail
in edge properties makes that auditable.

Usage:
    python3 helpers/graph/derive_countries.py            # dry-run summary
    python3 helpers/graph/derive_countries.py --apply    # entities + edges + worklist
    python3 helpers/graph/derive_countries.py --verbose  # list every edge
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.countries import (  # noqa: E402
    COUNTRY_VOCABULARY,  # noqa: F401  # re-export: the listed_in target set
    PLAIN_TICKER_OVERRIDES,  # noqa: F401  # re-export (tests + curation docs)
    TICKER_SUFFIX_TO_COUNTRY,  # noqa: F401  # re-export
    classify_ticker,
)
from helpers.core.db import connect, utc_now  # noqa: E402
from helpers.graph import derive_cli as dcli  # noqa: E402  # S6 shared CLI scaffold
from helpers.graph._edge_writer import apply_typed_edges  # noqa: E402

EDGE_TYPE = "listed_in"
SOURCE_REF = "derive:countries:ticker-suffix"
WORKLIST = _REPO_ROOT / "findata" / "Misc" / "country_worklist.json"

# The ticker maps + classify_ticker live in helpers/core/countries.py
# (shared with geo_converge / sync_tags / parse_newsletter) and are
# re-exported above for this module's tests and curation docs.


def derive_rows(conn) -> tuple[list, list, dict]:
    """Classify every company entity.

    Returns ``(edges, worklist_entries, country_counts)`` where edges are
    ``(company, country, properties, source_ref)`` tuples shaped for
    apply_typed_edges, and worklist entries carry the human-decision hint
    columns (ticker, sector, current geography tag, reason).
    """
    geo_tags = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT entity_name, MIN(tag) FROM entity_tags "
            "WHERE tag LIKE 'geography/%' GROUP BY entity_name"
        )
    }
    edges: list[tuple[str, str, dict, str]] = []
    worklist: list[dict] = []
    counts: dict[str, int] = {}
    rows = conn.execute(
        "SELECT name, ticker, sector_classification FROM entities "
        "WHERE entity_type = 'company' ORDER BY name"
    ).fetchall()
    for name, ticker, sector in rows:
        country, via = classify_ticker(ticker)
        if country is None:
            worklist.append(
                {
                    "name": name,
                    "ticker": ticker,
                    "sector": sector,
                    "geography_tag": geo_tags.get(name),
                    "reason": via,
                }
            )
            continue
        counts[country] = counts.get(country, 0) + 1
        edges.append((name, country, {"ticker": ticker, "via": via}, SOURCE_REF))
    return edges, worklist, counts


def create_country_entities(conn, countries, *, apply: bool = True) -> int:
    """Insert a ``country`` entity row per derived country (idempotent).

    Bare structural rows like themes/institutions — no file_path, no
    note. Mirrors derive_themes.create_theme_entities. Returns the number
    of rows inserted (0 if all already existed; the dry-run count when
    apply=False).
    """
    if not apply:
        return len(countries)
    now = utc_now()
    inserted = 0
    with conn:
        for country in countries:
            cur = conn.execute(
                "INSERT OR IGNORE INTO entities "
                "(name, entity_type, normalized_name, last_updated) "
                "VALUES (?, 'country', ?, ?)",
                (country, country, now),
            )
            inserted += cur.rowcount
    return inserted


def write_worklist(worklist: list, path: Path = WORKLIST) -> int:
    """Persist the no-ticker/unmapped companies for human assignment.

    Same pattern as the quote worklists: a git-tracked operator surface
    that future ticker fills consume (re-running the producer after a
    ticker lands moves the company onto an edge automatically).
    """
    payload = {
        "generated": utc_now(),
        "count": len(worklist),
        "companies": sorted(worklist, key=lambda w: w["name"]),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return len(worklist)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
_ARGS = dcli.DeriveArgsSpec(
    apply_help="Write country entities + listed_in edges + worklist (default: dry-run summary).",
    stale_help="No-op for countries: the source is entities.ticker (DB-derived), "
    "so there is no watch path to gate on.",
    corpus=False,
)


def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Derive listed_in (company -> country) edges from exchange tickers.",
    )
    dcli.add_derive_args(p, _ARGS)
    args = p.parse_args(argv)

    conn = connect()
    try:
        edges, worklist, counts = derive_rows(conn)
        mode = "apply" if args.apply else "dry-run"
        print(
            f"companies={len(edges) + len(worklist)} derived_edges={len(edges)} "
            f"worklist={len(worklist)} ({mode})",
            file=sys.stderr,
        )
        for country, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {n:4d}  {country}", file=sys.stderr)
        unmapped = [w["ticker"] for w in worklist if w["reason"] == "unmapped_suffix"]
        if unmapped:
            print(
                f"  WARNING {len(unmapped)} unmapped dotted suffixes worklisted: {unmapped}",
                file=sys.stderr,
            )

        countries = sorted(counts)
        ent_inserted = create_country_entities(conn, countries, apply=args.apply)
        edge_inserted = apply_typed_edges(
            edges,
            edge_type=EDGE_TYPE,
            symmetric=0,
            conn=conn,
            dry_run=not args.apply,
        )
        action = "inserted" if args.apply else "would insert"
        print(
            f"{ent_inserted} country entities {action}; {edge_inserted} listed_in edges {action}.",
            file=sys.stderr,
        )
        if args.apply:
            write_worklist(worklist)
            print(f"worklist written: {WORKLIST} ({len(worklist)} companies)", file=sys.stderr)
        if args.verbose:
            dcli.dump_edges_verbose(edges)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_cli())
