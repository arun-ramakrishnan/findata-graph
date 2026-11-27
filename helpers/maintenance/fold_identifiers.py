#!/usr/bin/env python3
"""Fold exchange ISINs and optional SEC CIKs into the identifier registry."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402  (after the REPO_ROOT sys.path insert, house pattern)
from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.vocab import RESOLUTION_STATUS_VALUES, sql_in  # noqa: E402

SOURCES_DB = REPO_ROOT / "memory" / "data" / "sources.duckdb"
EXCHANGE_SUFFIXES = {".NS": "NSE", ".BO": "BSE", ".BSE": "BSE", ".T": "TSE"}

# Operator-reviewed repairs, 2026-11-27 (exchange_ticker_data_update S5):
# the ISIN/listing name is authoritative; the prior owner was captured via
# a colliding symbol. Apply once, before recomputing candidates.
_OPERATOR_ISIN_REASSIGNMENTS = {
    "INE0QNI01012": "Boss Packaging Solutions",  # was Hugo Boss (BOSS.DE collision)
    "INE499A01024": "DCM Shriram",  # was DCM (wrong Indian issuer)
    "NSE:DCMSRIND": "DCM Shriram Industries",  # pseudo-ID, not DCM Shriram Ltd
    "NSE:DALMIASUG": "Dalmia Bharat Sugar and Industries",  # sugar != cement
}
# US/foreign entities with a bare ticker must never borrow an Indian exchange
# listing just because the symbol matches (e.g. DeFi Capital Markets / DCM.NS).
_FOREIGN_SUFFIXLESS_NAMES = frozenset({"DeFi Capital Markets, Inc. Common Stock"})

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def _is_isin(value: str) -> bool:
    return bool(_ISIN_RE.fullmatch(value.strip().upper()))


@dataclass(frozen=True)
class FigiRecord:
    """One OpenFIGI instrument record; metadata is captured, not discarded."""

    figi: str
    ticker: str
    name: str
    exch_code: str
    market_sector: str
    security_type: str
    share_class_figi: str
    composite_figi: str


@dataclass
class FoldStats:
    entity_tickers: int = 0
    listing_rows: int = 0
    cik_rows: int = 0
    direct_isin: int = 0
    cik: int = 0
    figi: int = 0
    ambiguous_tickers: int = 0
    unmatched_tickers: int = 0
    duplicate_values: int = 0
    existing_conflicts: int = 0
    same_issuer_conflicts: int = 0
    candidates: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    resolution_statuses: list[tuple[str, str, str]] = field(default_factory=list)
    figi_records: list[FigiRecord] = field(default_factory=list)


ENTITY_RESOLUTION_STATUS_DDL = f"""
CREATE TABLE IF NOT EXISTS entity_resolution_status (
    entity_name TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ({sql_in(RESOLUTION_STATUS_VALUES)})),
    detail TEXT,
    decided_at TEXT NOT NULL
)
"""

FIGI_INSTRUMENTS_DDL = """
CREATE TABLE IF NOT EXISTS figi_instruments (
    figi TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    name TEXT,
    exch_code TEXT,
    market_sector TEXT,
    security_type TEXT,
    share_class_figi TEXT,
    composite_figi TEXT,
    fetched_at TEXT NOT NULL
)
"""


def apply_operator_overrides(conn) -> tuple[int, int]:
    """Repair operator-reviewed identifier owners; drop stale foreign conflicts.

    Returns ``(reassigned_rows, removed_status_rows)``. Safe to re-run: the
    UPDATEs become no-ops once the correct owner is in place.
    """
    from helpers.core.db import utc_now

    now = utc_now()
    reassigned = 0
    removed = 0
    with conn:
        for value, new_owner in _OPERATOR_ISIN_REASSIGNMENTS.items():
            cur = conn.execute(
                "UPDATE entity_identifiers SET entity_name = ?, last_updated = ? "
                "WHERE identifier_value = ? AND entity_name <> ?",
                (new_owner, now, value, new_owner),
            )
            reassigned += cur.rowcount
            cur = conn.execute(
                "DELETE FROM entity_resolution_status "
                "WHERE entity_name = ? AND status = 'conflict' AND detail LIKE ?",
                (new_owner, f"%{value}%"),
            )
            removed += cur.rowcount
        for name in _FOREIGN_SUFFIXLESS_NAMES:
            cur = conn.execute(
                "DELETE FROM entity_resolution_status "
                "WHERE entity_name = ? AND status IN ('conflict', 'same-issuer')",
                (name,),
            )
            removed += cur.rowcount
    return reassigned, removed


def ensure_resolution_status_vocab(conn) -> None:
    """Widen the live entity_resolution_status CHECK to the current vocab.

    DuckDB cannot ALTER a CHECK constraint in place, so rebuild the tiny
    table (a few thousand triage rows) under a temporary name. Safe to call
    on both fresh and already-current stores; sqlite test fixtures skip it.
    """
    conn.execute(ENTITY_RESOLUTION_STATUS_DDL)
    try:
        row = conn.execute(
            "SELECT constraint_text FROM duckdb_constraints() "
            "WHERE table_name = 'entity_resolution_status' AND constraint_type = 'CHECK'"
        ).fetchone()
    except Exception:  # sqlite unit fixtures: their DDL already uses current vocab
        return
    if row is None or "same-issuer" in row[0]:
        return
    tmp = "entity_resolution_status_vocab_rebuild"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(ENTITY_RESOLUTION_STATUS_DDL.replace("entity_resolution_status", tmp))
    conn.execute(
        f"INSERT INTO {tmp} (entity_name, status, detail, decided_at) "  # noqa: S608 - constant temp table name
        "SELECT entity_name, status, detail, decided_at FROM entity_resolution_status"
    )
    conn.execute("DROP TABLE entity_resolution_status")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO entity_resolution_status")


def persist_resolution_statuses(
    conn: sqlite3.Connection, statuses: list[tuple[str, str, str]]
) -> int:
    """Persist fold-time gaps as non-terminal resolution statuses.

    These are NOT re-probe exclusions: they are operator-visible triage
    labels for entities whose ISIN/CIK could not be folded cleanly.
    """
    from helpers.core.db import utc_now

    ensure_resolution_status_vocab(conn)
    now = utc_now()
    for entity, status, detail in statuses:
        conn.execute(
            "INSERT OR REPLACE INTO entity_resolution_status "
            "(entity_name, status, detail, decided_at) VALUES (?, ?, ?, ?)",
            (entity, status, detail, now),
        )
    conn.commit()
    return len(statuses)


def _base_symbol(ticker: str) -> str:
    return ticker.strip().upper().replace("-", ".").split(".", 1)[0]


_LEGAL_SUFFIX_TOKENS = frozenset(
    {"LIMITED", "LTD", "PRIVATE", "PVT", "INC", "INCORPORATED", "CORP", "CORPORATION"}
)


def _normalize_company_name(name: str) -> str:
    """Canonicalize an issuer name for same-issuer conflict triage.

    Deliberately conservative: punctuation/case/whitespace, corporate
    suffixes, and simple plural tokens only. It must NOT treat parent or
    similarly named companies as the same issuer.
    """
    # Apostrophes are word characters in Python's Unicode \\w class; strip
    # them before the punctuation splitter (Investor's == Investors).
    text = re.sub(r"['\u2019]", "", name.strip().upper())
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    tokens: list[str] = []
    for token in text.split():
        if token in _LEGAL_SUFFIX_TOKENS:
            continue
        if len(token) > 4 and token.endswith("IES"):
            token = f"{token[:-3]}Y"
        elif len(token) > 5 and token.endswith("ESSES"):
            # Businesses -> Business.
            token = token[:-2]
        elif len(token) > 4 and token.endswith("SES"):
            # Businesses -> Business, Cases -> Case.
            token = token[:-1]
        elif len(token) > 3 and token.endswith("S") and not token.endswith(("SS", "US")):
            token = token[:-1]
        tokens.append(token)
    return " ".join(tokens)


_RIGHTS_SUFFIX_RE = re.compile(r"[\s\-]*(?:R|RE|EA)\d*$", flags=re.IGNORECASE)


def _rights_entitlement_name(name: str) -> str | None:
    """Return the ordinary-share name behind a rights-entitlement row.

    Exchange listing names suffix rights entitlements as ``-RE``, ``-R``,
    or ``-EA`` (often numbered). Only strip an explicit terminal marker;
    a name that merely contains ``Re`` is untouched.
    """
    stripped = name.strip()
    if not re.search(r"(?:[\s\-]|\A)(?:R|RE|EA)\d*$", stripped, flags=re.IGNORECASE):
        return None
    return _RIGHTS_SUFFIX_RE.sub("", stripped)


def _same_issuer_name(candidate: str, owner: str) -> bool:
    """True when a conflicted listing is a name/security-class variant.

    Matches exact normalized names and ordinary-share names behind
    rights-entitlement rows. It does not token-match partial names, so a
    parent/subsidiary or similarly named issuer stays a real conflict.
    """
    candidate_norm = _normalize_company_name(candidate)
    owner_norm = _normalize_company_name(owner)
    if candidate_norm == owner_norm:
        return True
    rights = _rights_entitlement_name(candidate)
    return bool(rights) and _normalize_company_name(rights) == owner_norm


def load_exchange_listings(path: Path = SOURCES_DB) -> list[tuple[str, str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    con = duckdb.connect(str(path), read_only=True, **memory_cap_config())
    try:
        rows = con.execute(
            "SELECT exchange, symbol, isin FROM exchange_listings "
            "WHERE isin IS NOT NULL AND TRIM(isin) <> '' "
            "AND symbol IS NOT NULL AND TRIM(symbol) <> '' "
            "AND (asset_type = 'equity' OR asset_type IS NULL)"
        ).fetchall()
    finally:
        con.close()
    return [
        (str(exchange).upper(), str(symbol).upper(), str(isin).upper())
        for exchange, symbol, isin in rows
    ]


def _cik10(cik: object) -> str:
    """SEC CIKs are numeric; EDGAR uses 10-digit zero-padded form."""
    s = str(cik).strip().upper()
    if s.startswith("CIK"):
        s = s[3:].strip()
    return s.zfill(10) if s.isdigit() else s


def load_cik_rows(path: Path | None) -> list[tuple[str, str]]:
    """Load CIK maps from simple rows or SEC official JSON shapes.

    Supported:
    - list[dict] `{"ticker","cik"}` / `{"symbol","cik_str"}`
    - SEC `company_tickers.json`: `{"0": {"cik_str", "ticker", "title"}, ...}`
    - SEC `company_tickers_exchange.json`: `{"fields":["cik","name","ticker","exchange"], "data":[[...]]}`
    - `{"data":[...]}`.
    """
    if path is None:
        return []
    data = jsonio.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        rows: list[object] = data
        fields: list[str] = []
    else:
        fields = [str(x) for x in (data.get("fields") or data.get("fieldNames") or [])]
        raw_rows = data.get("data", [])
        if isinstance(raw_rows, list):
            rows = raw_rows
        else:
            rows = []
        if not rows and not fields:
            # SEC company_tickers.json: top-level numeric keys -> row objects.
            rows = list(data.values())
    out: list[tuple[str, str]] = []
    for row in rows:
        rec: dict[str, object] = {}
        if isinstance(row, dict):
            rec = row
        elif isinstance(row, list) and fields:
            rec = dict(zip(fields, row, strict=False))
        else:
            continue
        ticker = rec.get("ticker") or rec.get("symbol")
        cik = rec.get("cik_str") or rec.get("cik")
        if ticker and cik is not None:
            out.append((str(ticker).upper(), _cik10(cik)))
    return out


def _listing_candidates(
    ticker: str, listings: list[tuple[str, str, str]]
) -> tuple[set[tuple[str, str]], bool]:
    symbol = _base_symbol(ticker)
    suffix = ticker.strip().upper().rsplit(".", 1)[-1]
    exchange = EXCHANGE_SUFFIXES.get(f".{suffix}")
    if "." in ticker and f".{suffix}" not in EXCHANGE_SUFFIXES:
        # Unknown exchange suffix: never borrow another exchange's listing
        # (e.g. BOSS.DE must not claim NSE BOSS).
        return set(), False
    if exchange:
        keys = [(exchange, symbol)]
    else:
        # Suffixless tickers are exchange-ambiguous by design; if more than
        # one exchange has the same symbol, callers treat them as ambiguous.
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


def _openfigi_job(ticker: str) -> tuple[str, str, dict[str, str]]:
    """Return (input_ticker, symbol, OpenFIGI job) for one ticker."""
    raw = ticker.strip().upper()
    symbol = _base_symbol(raw)
    suffix = raw.rsplit(".", 1)[-1]
    exch_code = "IN" if raw.endswith((".NS", ".BO", ".BSE")) else "US"
    if suffix == symbol:  # no dot
        exch_code = "US"
    return ticker, symbol, {"idType": "TICKER", "idValue": symbol, "exchCode": exch_code}


def _figi_record(item: dict[str, object], symbol: str, exch_code: str) -> FigiRecord | None:
    if not item.get("figi"):
        return None
    return FigiRecord(
        figi=str(item["figi"]).strip().upper(),
        ticker=str(item.get("ticker") or symbol).strip().upper(),
        name=str(item.get("name") or "").strip(),
        exch_code=str(item.get("exchCode") or exch_code).strip().upper(),
        market_sector=str(item.get("marketSector") or "").strip(),
        security_type=str(item.get("securityType") or "").strip(),
        share_class_figi=str(item.get("shareClassFIGI") or "").strip().upper(),
        composite_figi=str(item.get("compositeFIGI") or "").strip().upper(),
    )


def openfigi_lookup_many(  # noqa: C901 - chunked HTTP retry+parse+progress loop
    tickers: list[str], *, timeout: float = 15.0, chunk_size: int = 10
) -> dict[str, FigiRecord]:
    """Batch OpenFIGI lookups; no paid key, no marketSector guess.

    Free-tier limits are request-shaped (>=25 jobs returns HTTP 413), so
    chunk jobs (10 per POST) rather than issuing one request per ticker.
    HTTP 429 (rate limit) retries the chunk with backoff; other failed
    chunks print a stderr warning and their tickers are simply absent
    from the returned mapping (keyed by input ticker).
    """
    unique = list(dict.fromkeys(t.strip() for t in tickers if t.strip()))
    out: dict[str, FigiRecord] = {}
    chunks = [unique[start : start + chunk_size] for start in range(0, len(unique), chunk_size)]
    for index, chunk in enumerate(chunks):
        jobs: list[dict[str, str]] = []
        meta: list[tuple[str, str, str]] = []
        for ticker in chunk:
            input_ticker, symbol, job = _openfigi_job(ticker)
            jobs.append(job)
            meta.append((input_ticker, symbol, job["exchCode"]))
        data: object = None
        for attempt in range(3):
            req = urllib.request.Request(
                "https://api.openfigi.com/v3/mapping",
                data=json.dumps(jobs).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "findata-graph/1.0 (openfigi probe; no paid key)",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https API
                    data = jsonio.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    time.sleep(30.0 * (attempt + 1))
                    continue
                print(
                    f"WARN: openfigi chunk {index + 1}/{len(chunks)} failed: "
                    f"HTTP {exc.code} ({len(chunk)} tickers skipped)",
                    file=sys.stderr,
                    flush=True,
                )
                break
            except (OSError, urllib.error.URLError, ValueError, jsonio.JSONDecodeError) as exc:
                print(
                    f"WARN: openfigi chunk {index + 1}/{len(chunks)} failed: {exc} "
                    f"({len(chunk)} tickers skipped)",
                    file=sys.stderr,
                    flush=True,
                )
                break
        if index % 25 == 24 or index == len(chunks) - 1:
            done = min((index + 1) * chunk_size, len(unique))
            print(
                f"openfigi progress: {done}/{len(unique)} tickers, {len(out)} records",
                file=sys.stderr,
                flush=True,
            )
        if not isinstance(data, list):
            continue
        for (input_ticker, symbol, exch_code), first in zip(meta, data):
            if not isinstance(first, dict) or not isinstance(first.get("data"), list):
                continue
            for item in first["data"]:
                if not isinstance(item, dict):
                    continue
                record = _figi_record(item, symbol, exch_code)
                if record is not None:
                    out[input_ticker] = record
                    break
    return out


def openfigi_lookup(ticker: str, *, timeout: float = 15.0) -> FigiRecord | None:
    """OpenFIGI instrument record for a ticker; no paid key, no marketSector guess.

    US + India are the current main markets; use exchCode US/IN. Returns
    None on no match, network errors, or malformed responses. FIGI is not
    an ISIN and is not used as an alias for another entity.
    """
    return openfigi_lookup_many([ticker], timeout=timeout).get(ticker)


def openfigi_figi(ticker: str, *, timeout: float = 15.0) -> str | None:
    """Backward-compatible FIGI string helper."""
    record = openfigi_lookup(ticker, timeout=timeout)
    return record.figi if record else None


def build_candidates(  # noqa: C901
    conn: sqlite3.Connection,
    listings: list[tuple[str, str, str]],
    cik_rows: list[tuple[str, str]],
    *,
    fetch_figi: bool = False,
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
    figi_tickers: list[str] = []
    figi_entities: dict[str, list[str]] = defaultdict(list)
    for row in entities:
        ticker = str(row["ticker"])
        foreign_skip = "." not in ticker and row["name"] in _FOREIGN_SUFFIXLESS_NAMES
        values, ambiguous = (
            (set(), False) if foreign_skip else _listing_candidates(ticker, listings)
        )
        if ambiguous:
            stats.ambiguous_tickers += 1
            stats.resolution_statuses.append(
                (row["name"], "ambiguous", f"multiple exchange ISINs for {ticker}")
            )
            values = set()
        elif not values:
            if not foreign_skip:
                stats.unmatched_tickers += 1
                stats.resolution_statuses.append(
                    (row["name"], "unmatched", f"no exchange ISIN for {ticker}")
                )
                if fetch_figi:
                    figi_tickers.append(ticker)
                    figi_entities[ticker].append(row["name"])
        for exchange, isin in values:
            proposed[(row["name"], "isin", exchange)].add(isin)
            stats.direct_isin += 1
        ciks = set() if foreign_skip else cik_by_symbol.get(_base_symbol(ticker), set())
        owners = set() if foreign_skip else entity_by_symbol.get(_base_symbol(ticker), set())
        if len(ciks) == 1 and len(owners) == 1 and row["name"] in owners:
            proposed[(row["name"], "cik", "sec.gov")].add(next(iter(ciks)))
            stats.cik += 1
        elif len(ciks) > 1:
            stats.ambiguous_tickers += 1
            stats.resolution_statuses.append(
                (row["name"], "ambiguous", f"multiple SEC CIKs for {ticker}")
            )

    if fetch_figi and figi_tickers:
        seen_figi: set[str] = set()
        for ticker, record in openfigi_lookup_many(figi_tickers).items():
            if record.figi not in seen_figi:
                stats.figi_records.append(record)
                seen_figi.add(record.figi)
            for entity_name in figi_entities[ticker]:
                proposed[(entity_name, "figi", "openfigi")].add(record.figi)
                stats.figi += 1

    for (entity, identifier_type, namespace), values in proposed.items():
        for value in sorted(values):
            owner = conn.execute(
                "SELECT entity_name FROM entity_identifiers "
                "WHERE identifier_type=? AND identifier_value=?",
                (identifier_type, value),
            ).fetchone()
            if owner is not None:
                if owner[0] != entity:
                    if _same_issuer_name(entity, owner[0]) or _is_isin(value):
                        # Operator ruling 2026-11-27: a real ISIN is the
                        # authority on the underlying security, so a collision
                        # on the same ISIN is same-issuer triage, never a
                        # re-probe ban and never an identifier overwrite.
                        stats.same_issuer_conflicts += 1
                        stats.resolution_statuses.append(
                            (
                                entity,
                                "same-issuer",
                                f"{identifier_type}={value} already owned by {owner[0]}",
                            )
                        )
                    else:
                        stats.existing_conflicts += 1
                        stats.resolution_statuses.append(
                            (
                                entity,
                                "conflict",
                                f"{identifier_type}={value} owned by {owner[0]}",
                            )
                        )
                continue
            if identifier_type == "cik":
                source_ref = "sec.gov/company_tickers"
            elif identifier_type == "figi":
                source_ref = "openfigi"
            else:
                source_ref = "exchange_sync"
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


def persist_figi_instruments(records: list[FigiRecord], path: Path = SOURCES_DB) -> int:
    """Upsert OpenFIGI metadata into sources.duckdb::figi_instruments."""
    if not records:
        return 0
    from helpers.core.db import utc_now

    con = duckdb.connect(str(path), **memory_cap_config())
    try:
        con.execute(FIGI_INSTRUMENTS_DDL)
        now = utc_now()
        con.executemany(
            "INSERT OR REPLACE INTO figi_instruments "
            "(figi, ticker, name, exch_code, market_sector, security_type, "
            "share_class_figi, composite_figi, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r.figi,
                    r.ticker,
                    r.name,
                    r.exch_code,
                    r.market_sector,
                    r.security_type,
                    r.share_class_figi,
                    r.composite_figi,
                    now,
                )
                for r in records
            ],
        )
        con.commit()
    finally:
        con.close()
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write new registry rows")
    mode.add_argument("--check", action="store_true", help="exit 1 when rows are missing")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help=(
            "research store; default = the canonical store via helpers.core.db "
            "(memory/research.duckdb, FINDATA_RESEARCH_DB_PATH honoured)"
        ),
    )
    parser.add_argument("--sources", type=Path, default=SOURCES_DB)
    parser.add_argument("--cik-json", type=Path)
    parser.add_argument(
        "--figi",
        action="store_true",
        help="query OpenFIGI (no paid key) for tickers with no exchange ISIN; add identifier_type='figi'",
    )
    parser.add_argument(
        "--persist-status",
        action="store_true",
        help="persist unmatched/ambiguous/conflict fold gaps as non-terminal entity_resolution_status rows (requires --apply)",
    )
    args = parser.parse_args(argv)
    if args.persist_status and not args.apply:
        print("ERROR: --persist-status requires --apply", file=sys.stderr)
        return 2

    from helpers.core.db import connect
    from helpers.misc.backfill_identifiers import ensure_schema

    if args.db is not None and not args.db.exists():
        print("ERROR: research store not found", file=sys.stderr)
        return 1
    if not args.sources.exists():
        print("ERROR: sources.duckdb is missing", file=sys.stderr)
        return 1
    conn = connect(args.db)
    try:
        listings = load_exchange_listings(args.sources)
        ciks = load_cik_rows(args.cik_json)
        overrides = (0, 0)
        if args.apply:
            ensure_schema(conn)
            overrides = apply_operator_overrides(conn)
        stats = build_candidates(conn, listings, ciks, fetch_figi=args.figi)
        written = apply_candidates(conn, stats.candidates) if args.apply else 0
        figi_written = (
            persist_figi_instruments(stats.figi_records, args.sources)
            if args.apply and args.figi
            else 0
        )
        status_written = (
            persist_resolution_statuses(conn, stats.resolution_statuses)
            if args.apply and args.persist_status
            else 0
        )
    except (OSError, duckdb.Error, jsonio.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(
        f"identifier fold: listings={stats.listing_rows} ciks={stats.cik_rows} "
        f"direct_isin={stats.direct_isin} cik={stats.cik} figi={stats.figi} "
        f"ambiguous={stats.ambiguous_tickers} unmatched={stats.unmatched_tickers} "
        f"conflicts={stats.existing_conflicts} same_issuer={stats.same_issuer_conflicts} "
        f"candidates={len(stats.candidates)} overridden={overrides[0]} "
        f"stale_statuses={overrides[1]} written={written} "
        f"figi_written={figi_written} statuses={status_written}",
        file=sys.stderr,
        flush=True,
    )
    if args.check and stats.candidates:
        return 1
    if not args.apply:
        print(
            "dry-run: no rows written; pass --apply at the operator checkpoint.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
