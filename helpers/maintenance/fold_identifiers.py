#!/usr/bin/env python3
"""Fold exchange ISINs and optional SEC CIKs into the identifier registry."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SOURCES_DB = REPO_ROOT / "memory" / "data" / "sources.duckdb"
DEFAULT_DB = REPO_ROOT / "memory" / "research.db"
EXCHANGE_SUFFIXES = {".NS": "NSE", ".BO": "BSE", ".BSE": "BSE"}


@dataclass
class FoldStats:
    entity_tickers: int = 0
    listing_rows: int = 0
    cik_rows: int = 0
    direct_isin: int = 0
    cik: int = 0
    ambiguous_tickers: int = 0
    unmatched_tickers: int = 0
    duplicate_values: int = 0
    existing_conflicts: int = 0
    candidates: list[tuple[str, str, str, str, str]] = field(default_factory=list)


def _base_symbol(ticker: str) -> str:
    return ticker.strip().upper().split(".", 1)[0]


def load_exchange_listings(path: Path = SOURCES_DB) -> list[tuple[str, str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            "SELECT exchange, symbol, isin FROM exchange_listings "
            "WHERE isin IS NOT NULL AND TRIM(isin) <> '' "
            "AND symbol IS NOT NULL AND TRIM(symbol) <> ''"
        ).fetchall()
    finally:
        con.close()
    return [
        (str(exchange).upper(), str(symbol).upper(), str(isin).upper())
        for exchange, symbol, isin in rows
    ]


def load_cik_rows(path: Path | None) -> list[tuple[str, str]]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("data", [])
    out: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker") or row.get("symbol")
        cik = row.get("cik_str") or row.get("cik")
        if ticker and cik is not None:
            out.append((str(ticker).upper(), str(cik).strip()))
    return out


def _listing_candidates(
    ticker: str, listings: list[tuple[str, str, str]]
) -> tuple[set[tuple[str, str]], bool]:
    symbol = _base_symbol(ticker)
    suffix = ticker.strip().upper().rsplit(".", 1)[-1]
    exchange = EXCHANGE_SUFFIXES.get(f".{suffix}")
    if exchange:
        keys = [(exchange, symbol)]
    else:
        keys = [
            (candidate_exchange, candidate_symbol)
            for candidate_exchange, candidate_symbol, _ in listings
            if candidate_symbol == symbol
        ]
    values = {
        (candidate_exchange, isin)
        for candidate_exchange, candidate_symbol, isin in listings
        if (candidate_exchange, candidate_symbol) in keys
    }
    return values, len(values) > 1


def build_candidates(  # noqa: C901
    conn: sqlite3.Connection,
    listings: list[tuple[str, str, str]],
    cik_rows: list[tuple[str, str]],
) -> FoldStats:
    stats = FoldStats(listing_rows=len(listings), cik_rows=len(cik_rows))
    entities = conn.execute(
        "SELECT name, ticker FROM entities WHERE entity_type='company' AND ticker IS NOT NULL"
    ).fetchall()
    stats.entity_tickers = len(entities)
    cik_by_symbol: dict[str, set[str]] = defaultdict(set)
    for symbol, cik in cik_rows:
        cik_by_symbol[_base_symbol(symbol)].add(cik)
    entity_by_symbol: dict[str, set[str]] = defaultdict(set)
    for row in entities:
        entity_by_symbol[_base_symbol(row["ticker"])].add(row["name"])

    proposed: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in entities:
        ticker = str(row["ticker"])
        values, ambiguous = _listing_candidates(ticker, listings)
        if ambiguous:
            stats.ambiguous_tickers += 1
            values = set()
        elif not values:
            stats.unmatched_tickers += 1
        for exchange, isin in values:
            proposed[(row["name"], "isin", exchange)].add(isin)
            stats.direct_isin += 1
        ciks = cik_by_symbol.get(_base_symbol(ticker), set())
        owners = entity_by_symbol.get(_base_symbol(ticker), set())
        if len(ciks) == 1 and len(owners) == 1 and row["name"] in owners:
            proposed[(row["name"], "cik", "sec.gov")].add(next(iter(ciks)))
            stats.cik += 1
        elif len(ciks) > 1:
            stats.ambiguous_tickers += 1

    for (entity, identifier_type, namespace), values in proposed.items():
        for value in sorted(values):
            owner = conn.execute(
                "SELECT entity_name FROM entity_identifiers "
                "WHERE identifier_type=? AND identifier_value=?",
                (identifier_type, value),
            ).fetchone()
            if owner is not None:
                if owner[0] != entity:
                    stats.existing_conflicts += 1
                continue
            source_ref = "sec.gov/company_tickers" if identifier_type == "cik" else "exchange_sync"
            stats.candidates.append((entity, identifier_type, value, namespace, source_ref))
    return stats


def apply_candidates(
    conn: sqlite3.Connection, candidates: list[tuple[str, str, str, str, str]]
) -> int:
    from helpers.core.db import utc_now

    now = utc_now()
    before = conn.execute("SELECT COUNT(*) FROM entity_identifiers").fetchone()[0]
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO entity_identifiers "
            "(entity_name, identifier_type, identifier_value, namespace, source_ref, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(*candidate, now) for candidate in candidates],
        )
    after = conn.execute("SELECT COUNT(*) FROM entity_identifiers").fetchone()[0]
    return after - before


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write new registry rows")
    mode.add_argument("--check", action="store_true", help="exit 1 when rows are missing")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--sources", type=Path, default=SOURCES_DB)
    parser.add_argument("--cik-json", type=Path)
    args = parser.parse_args(argv)

    from helpers.core.db import connect
    from helpers.misc.backfill_identifiers import ensure_schema

    if not args.db.exists() or not args.sources.exists():
        print("ERROR: research.db or sources.duckdb is missing", file=sys.stderr)
        return 1
    conn = connect(args.db)
    try:
        listings = load_exchange_listings(args.sources)
        ciks = load_cik_rows(args.cik_json)
        if args.apply:
            ensure_schema(conn)
        stats = build_candidates(conn, listings, ciks)
        written = apply_candidates(conn, stats.candidates) if args.apply else 0
    except (OSError, duckdb.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(
        f"identifier fold: listings={stats.listing_rows} ciks={stats.cik_rows} "
        f"direct_isin={stats.direct_isin} cik={stats.cik} "
        f"ambiguous={stats.ambiguous_tickers} unmatched={stats.unmatched_tickers} "
        f"conflicts={stats.existing_conflicts} candidates={len(stats.candidates)} written={written}",
        file=sys.stderr,
    )
    if args.check and stats.candidates:
        return 1
    if not args.apply:
        print("dry-run: no rows written; pass --apply at the operator checkpoint.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
