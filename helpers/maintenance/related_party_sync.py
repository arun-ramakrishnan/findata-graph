#!/usr/bin/env python3
"""Related-party group structure from the VIGIL bulk RPT dataset.

(related_party_groups_vigil.md) Bulk download of the LODR Reg 23
related-party transactions table → ``sources.duckdb::rpt_transactions``
→ free-text relationship classification → ``subsidiary_of`` /
``same_group`` / ``jv_with`` edges (+ company entities for unlisted
counter-parties — the corporate tree).

Source: ``api.tigzig.com/vigil/v1`` (found in the site bundle; the
display API is ``POST vigil.tigzig.com/api/data {query, params}`` with a
10-row cap — the bulk CSV is the ingestion path). Republished regulator
data: edges land ``source_tier='external'`` with the source filing's
``xbrl_url`` in properties (the direct-from-narchives SHP lane stays
``'regulator'``). ``source_ref`` prefix ``vigil:rpt:<symbol>``.

The "BSE corporate group repository" this replaces does not exist: BSE
``GROUP`` is the trading segment (A/B/X/T/Z — verified against
ListofScripData, 2026-09-22); see data_sources.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SOURCES_DB = REPO / "memory" / "data" / "sources.duckdb"
RAW_DIR = REPO / "memory" / "data" / "rpt_raw"
WORKLIST = REPO / "memory" / "data" / "rpt_worklist.csv"
BULK_URL = "https://api.tigzig.com/vigil/v1/download/rpt_transactions?format=csv.gz"
UA = {"User-Agent": "Mozilla/5.0 research", "Referer": "https://vigil.tigzig.com/"}
SOURCE_REF_PREFIX = "vigil:rpt"

from helpers.maintenance.shareholding_sync import (  # noqa: E402  (post-bootstrap)
    _normalize,
    _resolve_companies,
    _scalar,
)

# --------------------------------------------------------------------------- #
# fetch + store
# --------------------------------------------------------------------------- #
_DDL = """CREATE TABLE IF NOT EXISTS rpt_transactions (
    record_id VARCHAR, seq_num VARCHAR, txn_number VARCHAR, symbol VARCHAR,
    company_name VARCHAR, period_end_date VARCHAR, broadcast_date VARCHAR,
    audited VARCHAR, filing_type_sub VARCHAR, entity_name VARCHAR,
    counter_party VARCHAR, relationship VARCHAR, transaction_type VARCHAR,
    other_details VARCHAR, approved_value VARCHAR, amount_during_period VARCHAR,
    outstanding_balance VARCHAR, audit_committee_remarks VARCHAR,
    loan_nature VARCHAR, loan_interest_rate VARCHAR, loan_tenure VARCHAR,
    loan_secured VARCHAR, loan_purpose VARCHAR, entity_pan VARCHAR,
    counter_party_pan VARCHAR, source VARCHAR, xbrl_url VARCHAR,
    rel_group VARCHAR, broadcast_date_parsed VARCHAR, period_end_date_parsed VARCHAR
)"""


def _get_raw(url: str, timeout: int = 300) -> bytes:
    req = urllib.request.Request(url, headers=dict(UA))  # noqa: S310  # https-only api lane
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  # https-only api lane
        return r.read()


_DDL_RATINGS = """CREATE TABLE IF NOT EXISTS credit_ratings (
    record_id VARCHAR, app_id VARCHAR, company_name VARCHAR, isin VARCHAR,
    equity_isin VARCHAR, nse_symbol VARCHAR, instrument_type VARCHAR,
    instrument_name VARCHAR, rating_agency VARCHAR, credit_rating VARCHAR,
    rating_action VARCHAR, date_of_rating VARCHAR, outlook VARCHAR,
    credit_rating_earlier VARCHAR, rating_action_earlier VARCHAR,
    outlook_earlier VARCHAR, date_of_rating_earlier VARCHAR,
    broadcast_datetime VARCHAR, signatory_name VARCHAR, designation VARCHAR,
    place VARCHAR, verify_status VARCHAR, match_method VARCHAR,
    listing_status VARCHAR, source_month VARCHAR, xbrl_url VARCHAR,
    red_flag_reason VARCHAR
)"""

RATINGS_URL = "https://api.tigzig.com/vigil/v1/download/credit_ratings?format=csv.gz"


def download_bulk(refetch: bool = False) -> Path:
    """Bulk CSV download, zstd-cached content-addressed by the dataset name."""
    from helpers.core.zstd_io import compress_file

    cache = RAW_DIR / "rpt_transactions.csv.gz.zst"
    if cache.is_file() and not refetch:
        return cache
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    data = _get_raw(BULK_URL)
    tmp_gz = RAW_DIR / "rpt_transactions.csv.gz"
    tmp_gz.write_bytes(data)
    compress_file(tmp_gz, cache)
    tmp_gz.unlink(missing_ok=True)
    return cache


def load_table(con: duckdb.DuckDBPyConnection, cache: Path) -> int:
    from helpers.core.zstd_io import decompress_file

    con.execute(_DDL)
    # the cache is zst(gz(csv)): decompress_file yields the gz bytes —
    # keep the .csv.gz extension so duckdb's sniffer handles that layer
    tmp = cache.with_name("rpt_transactions.load.csv.gz")
    decompress_file(cache, tmp)
    con.execute("DELETE FROM rpt_transactions")
    con.execute(
        # tmp is a module-built local cache path, not user input
        f"COPY rpt_transactions FROM '{tmp}' (FORMAT csv, HEADER, NULLSTR '')"  # noqa: S608
    )
    tmp.unlink(missing_ok=True)
    return _scalar(con, "SELECT COUNT(*) FROM rpt_transactions")


def download_ratings(refetch: bool = False) -> Path:
    """credit_ratings bulk CSV, zstd-cached (same layout as the RPT cache)."""
    from helpers.core.zstd_io import compress_file

    cache = RAW_DIR / "credit_ratings.csv.gz.zst"
    if cache.is_file() and not refetch:
        return cache
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tmp_gz = RAW_DIR / "credit_ratings.csv.gz"
    tmp_gz.write_bytes(_get_raw(RATINGS_URL))
    compress_file(tmp_gz, cache)
    tmp_gz.unlink(missing_ok=True)
    return cache


def load_ratings(con: duckdb.DuckDBPyConnection, cache: Path) -> int:
    from helpers.core.zstd_io import decompress_file

    con.execute(_DDL_RATINGS)
    tmp = cache.with_name("credit_ratings.load.csv.gz")
    decompress_file(cache, tmp)
    con.execute("DELETE FROM credit_ratings")
    con.execute(
        # tmp is a module-built local cache path, not user input
        f"COPY credit_ratings FROM '{tmp}' (FORMAT csv, HEADER, NULLSTR '')"  # noqa: S608
    )
    tmp.unlink(missing_ok=True)
    return _scalar(con, "SELECT COUNT(*) FROM credit_ratings")


# --------------------------------------------------------------------------- #
# classify + derive
# --------------------------------------------------------------------------- #


def classify_relationship(text: str | None, rel_group: str | None = None) -> str | None:
    """Python mirror of the ``_CLASSIFY_SQL`` CASE (same clause order).

    Vigil symmetric-emission S2: the mint-side and the prune-side must
    classify IDENTICALLY, and SQL CASE order is load-bearing —
    ``'%holding company%'`` fires before the ``'%subsidiar%'`` catch, so
    texts like 'Subsidiary of Holding Company' route to ``sub_rev``.
    Pinned by test against the SQL on seeded texts.
    """
    t = (text or "").lower()
    if "holding company" in t:
        return "sub_rev"
    if "fellow subsidiar" in t:
        return "same_group"
    if t.startswith("subsidiary of listed") or t.startswith("subsidiary of ultimate"):
        return "same_group"
    if "joint venture" in t:
        return "jv_with"
    if "associat" in t:
        return "same_group"
    if "common control" in t or "promoter group" in t:
        return "same_group"
    if "subsidiar" in t or "wholly owned" in t:
        return "sub_fwd"
    if rel_group in ("Group Companies", "Promoter Group", "Common Control/Influence"):
        return "same_group"
    return None


_CLASSIFY_SQL = """
CASE
  WHEN lower(relationship) LIKE '%holding company%' THEN 'sub_rev'
  WHEN lower(relationship) LIKE '%fellow subsidiar%' THEN 'same_group'
  WHEN lower(relationship) LIKE 'subsidiary of listed%'
       OR lower(relationship) LIKE 'subsidiary of ultimate%' THEN 'same_group'
  WHEN lower(relationship) LIKE '%joint venture%' THEN 'jv_with'
  WHEN lower(relationship) LIKE '%associat%' THEN 'same_group'
  WHEN lower(relationship) LIKE '%common control%'
       OR lower(relationship) LIKE '%promoter group%' THEN 'same_group'
  WHEN lower(relationship) LIKE '%subsidiar%'
       OR lower(relationship) LIKE '%wholly owned%' THEN 'sub_fwd'
  WHEN rel_group IN ('Group Companies', 'Promoter Group',
                     'Common Control/Influence') THEN 'same_group'
END
"""

_GARBAGE = re.compile(r"^\d+$|^\d+\s+[A-Z]|PO BOX|\bAVE\b|\bSTREET\b|\bROAD\b")


def _safe_display(name: str) -> str | None:
    """clean_name + CHECK-safe repair; None = unusable display.

    Filings text arrives mangled ('Privated limited', 'Solutionspvt' —
    glued, no word boundary) and survives clean_name; the entities CHECK
    is a substring LIKE, so any residue is rejected by INSERT OR IGNORE
    and would blow up the edge FK. Scrub everywhere, not just suffixes."""
    from helpers.maintenance.exchange_sync import clean_name  # noqa: PLC0415

    d = clean_name(name)
    if not d:
        return None
    if _CHECK_BAD.search(d):
        d = re.sub(r"(?i)privated?|pvt\.?|private|limited|ltd\.?", " ", d)
        d = re.sub(r"\s{2,}", " ", d).strip(" .,-")
    return d if d and not _CHECK_BAD.search(d) else None


# entities CHECK guards (substring LIKEs, case-insensitive): a display
# name tripping these would be silently dropped by INSERT OR IGNORE and
# blow up the edge FK — validate before creating.
_CHECK_BAD = re.compile(r"limited|ltd|pvt|private", re.I)
_PAREN = re.compile(r"\s*\((?:formerly|formerly known as|now known as)[^)]*\)\s*", re.I)


def _clean_counterparty(name: str) -> str | None:
    """Identity form of a counter-party name: parentheticals stripped,
    garbage rejected. None = unusable."""
    n = _PAREN.sub(" ", (name or "").strip())
    n = re.sub(r"\s{2,}", " ", n).strip(" \"',")
    if not 3 <= len(n) <= 90 or _GARBAGE.search(n.upper()):
        return None
    return n


_PAIRS_SQL = """
    SELECT DISTINCT ON (symbol, counter_party) symbol, entity_name,
           counter_party, relationship, rel_group, transaction_type,
           amount_during_period, period_end_date, xbrl_url,
    CASE
      WHEN lower(relationship) LIKE '%holding company%' THEN 'sub_rev'
      WHEN lower(relationship) LIKE '%fellow subsidiar%' THEN 'same_group'
      WHEN lower(relationship) LIKE 'subsidiary of listed%'
           OR lower(relationship) LIKE 'subsidiary of ultimate%' THEN 'same_group'
      WHEN lower(relationship) LIKE '%joint venture%' THEN 'jv_with'
      WHEN lower(relationship) LIKE '%associat%' THEN 'same_group'
      WHEN lower(relationship) LIKE '%common control%'
           OR lower(relationship) LIKE '%promoter group%' THEN 'same_group'
      WHEN lower(relationship) LIKE '%subsidiar%'
           OR lower(relationship) LIKE '%wholly owned%' THEN 'sub_fwd'
      WHEN rel_group IN ('Group Companies', 'Promoter Group',
                         'Common Control/Influence') THEN 'same_group'
    END AS cls
    FROM rpt_transactions
    WHERE symbol IS NOT NULL AND counter_party IS NOT NULL
      AND entity_name IS NOT NULL
    ORDER BY symbol, counter_party, period_end_date_parsed DESC NULLS LAST
    """


def build_pairs(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Distinct (filer symbol, counter-party, class) pairs with the latest
    filing's attributes per pair (_PAIRS_SQL mirrors _CLASSIFY_SQL)."""
    rows = con.execute(_PAIRS_SQL).fetchall()
    out = []
    for sym, ent, cp, rel, grp, ttype, amt, pend, url, cls in rows:
        if cls is None:
            continue
        clean = _clean_counterparty(cp)
        if clean is None:
            continue
        out.append(
            {
                "symbol": sym,
                "entity": ent,
                "counter_display": cp,
                "counter": clean,
                "relationship": rel,
                "rel_group": grp,
                "txn_type": ttype,
                "amount": amt,
                "period_end": pend,
                "xbrl_url": url,
                "cls": cls,
            }
        )
    return out


def apply_pairs(  # noqa: C901  # the filer/counter/edge-class resolution ladder
    conn: sqlite3.Connection,
    pairs: list[dict],
    companies: dict[str, str],
    norm_to_name: dict[str, str],
    *,
    max_new_entities: int = 60000,
    dry_run: bool = True,
) -> tuple[int, int, int, int, int]:
    """Create counter-party entities + structural edges.

    Returns (n_new_entities, n_edges_would_or_written, n_written,
    n_unresolved_filers, n_cycles_dropped). Upsert-latest per
    (source, target, edge_type).
    """

    existing = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    edges: list[dict] = []
    new_entities: dict[str, str] = {}
    unresolved_filers: set[str] = set()
    for p in pairs:
        filer = companies.get(p["symbol"])
        if filer is None:
            unresolved_filers.add(p["symbol"])
            continue
        cp_norm = _normalize(p["counter"])
        cp_ent = norm_to_name.get(cp_norm.upper())
        if cp_ent is None:
            display = _safe_display(p["counter"])
            if display and display not in existing and display not in new_entities:
                if len(new_entities) < max_new_entities:
                    new_entities[display] = cp_norm
                    cp_ent = display
                else:
                    continue  # cap reached — skip the edge, reported in dry-run
            elif display in existing:
                cp_ent = display
            elif display in new_entities:
                cp_ent = display
        if cp_ent is None or cp_ent == filer:
            continue
        if p["cls"] == "sub_fwd":
            src, dst, etype, symm = cp_ent, filer, "subsidiary_of", 0
        elif p["cls"] == "sub_rev":
            src, dst, etype, symm = filer, cp_ent, "subsidiary_of", 0
        elif p["cls"] == "same_group":
            src, dst = (cp_ent, filer) if cp_ent < filer else (filer, cp_ent)
            etype, symm = "same_group", 1
        else:  # jv_with
            src, dst = (cp_ent, filer) if cp_ent < filer else (filer, cp_ent)
            etype, symm = "jv_with", 1
        edges.append(
            {
                "src": src,
                "dst": dst,
                "etype": etype,
                "symm": symm,
                "cls": p["cls"],
                "source_ref": f"{SOURCE_REF_PREFIX}:{p['symbol']}",
                "props": json.dumps(
                    {
                        "relationship": p["relationship"],
                        "rel_group": p["rel_group"],
                        "txn_type": p["txn_type"],
                        "amount_during_period": p["amount"],
                        "period_end": p["period_end"],
                        "xbrl_url": p["xbrl_url"],
                        "counter_display": p["counter_display"],
                    },
                    sort_keys=True,
                ),
            }
        )
    # Vigil symmetric-emission S2: one filer's filing can mint BOTH
    # directions of a subsidiary_of pair (a 'Holding Company' row routes
    # sub_rev, a 'Wholly Owned Subsidiary' row routes sub_fwd — 37 live
    # cycles, 15 of them pure rule-order artifacts where the text was
    # 'Subsidiary of (Ultimate) Holding Company'). Same-ref cycles are
    # filing noise: keep the sub_fwd direction (the counter claimed as
    # the filer's subsidiary), drop the sub_rev mint. Cross-ref pairs are
    # never touched here.
    n_cycles_dropped = 0
    by_ref: dict[str, list[dict]] = {}
    for e in edges:
        if e["etype"] == "subsidiary_of":
            by_ref.setdefault(e["source_ref"], []).append(e)
    dropped: set[int] = set()
    for group in by_ref.values():
        for i, e1 in enumerate(group):
            for e2 in group[i + 1 :]:
                if e1["src"] == e2["dst"] and e1["dst"] == e2["src"]:
                    loser = e1 if e1["cls"] == "sub_rev" else e2
                    if loser["cls"] == "sub_rev":
                        dropped.add(id(loser))
                        n_cycles_dropped += 1
    if dropped:
        edges = [e for e in edges if id(e) not in dropped]
    n_written = 0
    if not dry_run:
        with conn:
            for display, norm in new_entities.items():
                conn.execute(
                    "INSERT OR IGNORE INTO entities (name, entity_type, "
                    "normalized_name, file_path) VALUES (?, 'company', ?, NULL)",
                    (display, norm),
                )
            for e in edges:
                cur = conn.execute(
                    "INSERT INTO graph_edges (source, target, edge_type, weight, "
                    "properties, source_ref, symmetric, source_tier) "
                    "VALUES (?, ?, ?, 1.0, ?, ?, ?, 'external') "
                    "ON CONFLICT(source, target, edge_type) DO UPDATE SET "
                    "properties=excluded.properties, source_ref=excluded.source_ref",
                    (e["src"], e["dst"], e["etype"], e["props"], e["source_ref"], e["symm"]),
                )
                n_written += cur.rowcount
    return len(new_entities), len(edges), n_written, len(unresolved_filers), n_cycles_dropped


_SALE_TYPES = ("Sale of goods or services", "Sale of fixed assets")
_BUY_TYPES = ("Purchase of goods or services", "Purchase of fixed assets")
_SUPPLY_TYPES_SQL = ", ".join("'" + x + "'" for x in _SALE_TYPES + _BUY_TYPES)


_SUPPLY_SQL = """
    SELECT symbol, counter_party,
           MAX(CASE WHEN transaction_type IN ('Sale of goods or services',
                                              'Sale of fixed assets')
                    THEN TRY_CAST(amount_during_period AS DOUBLE) END) AS sale_amt,
           MAX(CASE WHEN transaction_type IN ('Purchase of goods or services',
                                              'Purchase of fixed assets')
                    THEN TRY_CAST(amount_during_period AS DOUBLE) END) AS buy_amt,
           MAX(period_end_date) AS period_end,
           MAX(CASE WHEN transaction_type IN ('Sale of goods or services',
                                              'Sale of fixed assets')
                    THEN period_end_date END) AS sale_period,
           MAX(CASE WHEN transaction_type IN ('Purchase of goods or services',
                                              'Purchase of fixed assets')
                    THEN period_end_date END) AS buy_period,
           any_value(xbrl_url) AS xbrl_url,
           any_value(relationship) AS relationship
    FROM rpt_transactions
    WHERE transaction_type IN ('Sale of goods or services', 'Sale of fixed assets',
                               'Purchase of goods or services', 'Purchase of fixed assets')
      AND symbol IS NOT NULL AND counter_party IS NOT NULL
    GROUP BY symbol, counter_party
    """


def build_supply_pairs(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Distinct (filer, counter, direction) trading pairs with amounts.

    Direction: filer SELLS to counter -> supplier_to(filer, counter);
    filer BUYS from counter -> supplier_to(counter, filer). Two-way
    trading lands both edges ONLY when both directions are evidenced in
    the same period_end (vigil symmetric-emission S2: the pre-fix MAX
    aggregates spanned all periods, so a sale in one period and a
    purchase in another manufactured a mutual pair under one ref).
    Counter-parties resolve only (the group pass already created
    structural entities)."""
    # interpolated parts are module-level type-name tuples
    rows = con.execute(_SUPPLY_SQL).fetchall()
    out = []
    for sym, cp, sale_amt, buy_amt, pend, sale_period, buy_period, url, rel in rows:
        clean = _clean_counterparty(cp)
        if clean is None:
            continue
        out.append(
            {
                "symbol": sym,
                "counter_display": cp,
                "counter": clean,
                "sale_amt": sale_amt,
                "buy_amt": buy_amt,
                "period_end": pend,
                "sale_period": sale_period,
                "buy_period": buy_period,
                "xbrl_url": url,
                "relationship": rel,
            }
        )
    return out


def apply_supply_pairs(
    conn: sqlite3.Connection,
    pairs: list[dict],
    companies: dict[str, str],
    norm_to_name: dict[str, str],
    *,
    dry_run: bool = True,
) -> tuple[int, int, int]:
    """supplier_to edges (source=supplier, target=customer), weight 1.0,
    amounts in properties. Returns (n_edges, n_written,
    n_crossperiod_collapsed).

    Vigil symmetric-emission S2: both directions only when BOTH are
    evidenced in the same period_end; a cross-period aggregate keeps ONE
    edge — the larger-amount direction (tie: the sale direction), with
    BOTH amounts and both period_ends in properties so no evidence is
    lost. Cross-ref pairs are never touched here."""
    from helpers.maintenance.shareholding_sync import _normalize  # noqa: PLC0415

    edges = []
    n_cross = 0
    for p in pairs:
        filer = companies.get(p["symbol"])
        if filer is None:
            continue
        cp_ent = norm_to_name.get(_normalize(p["counter"]).upper())
        if cp_ent is None or cp_ent == filer:
            continue
        props = json.dumps(
            {
                "sale_amount": p["sale_amt"],
                "purchase_amount": p["buy_amt"],
                "period_end": p["period_end"],
                "xbrl_url": p["xbrl_url"],
                "relationship": p["relationship"],
                "counter_display": p["counter_display"],
            },
            sort_keys=True,
        )
        ref = f"{SOURCE_REF_PREFIX}:{p['symbol']}"
        if p["sale_amt"] is not None and p["buy_amt"] is not None:
            if p["sale_period"] == p["buy_period"]:
                # genuinely two-way within one reporting period
                edges.append((filer, cp_ent, props, ref))
                edges.append((cp_ent, filer, props, ref))
            else:
                # MAX-aggregate artifact: one dominant direction survives,
                # both amounts + both period_ends preserved in properties
                sale_wins = (p["sale_amt"] or 0.0) >= (p["buy_amt"] or 0.0)
                src_e, dst_e = (filer, cp_ent) if sale_wins else (cp_ent, filer)
                survivor = json.dumps(
                    {
                        "sale_amount": p["sale_amt"],
                        "purchase_amount": p["buy_amt"],
                        "sale_period_end": p["sale_period"],
                        "purchase_period_end": p["buy_period"],
                        "two_way": "cross-period",
                        "xbrl_url": p["xbrl_url"],
                        "relationship": p["relationship"],
                        "counter_display": p["counter_display"],
                    },
                    sort_keys=True,
                )
                edges.append((src_e, dst_e, survivor, ref))
                n_cross += 1
        elif p["sale_amt"] is not None:
            edges.append((filer, cp_ent, props, ref))
        elif p["buy_amt"] is not None:
            edges.append((cp_ent, filer, props, ref))
    n_written = 0
    if not dry_run:
        with conn:
            for src, dst, props, ref in edges:
                cur = conn.execute(
                    "INSERT INTO graph_edges (source, target, edge_type, weight, "
                    "properties, source_ref, symmetric, source_tier) "
                    "VALUES (?, ?, 'supplier_to', 1.0, ?, ?, 0, 'external') "
                    "ON CONFLICT(source, target, edge_type) DO UPDATE SET "
                    "properties=excluded.properties, source_ref=excluded.source_ref",
                    (src, dst, props, ref),
                )
                n_written += cur.rowcount
    return len(edges), n_written, n_cross


def _supply_direction_evidence(
    src: duckdb.DuckDBPyConnection, symbol: str, counter_norm: str
) -> dict:
    """Per-direction period sets + MAX amounts for one (filer symbol,
    normalized counter), via the same resolution ladder as the mint pass
    (raw counter_party -> _clean_counterparty -> _normalize)."""
    sale_periods: set[str] = set()
    buy_periods: set[str] = set()
    sale_max = buy_max = None
    sql_types = _SUPPLY_TYPES_SQL
    for cp, ttype, pend, amt in src.execute(
        f"""
        SELECT counter_party, transaction_type, period_end_date,
               TRY_CAST(amount_during_period AS DOUBLE)
        FROM rpt_transactions
        WHERE symbol = ? AND transaction_type IN ({sql_types})
        """,  # noqa: S608  # module-constant type-name tuple
        [symbol],
    ).fetchall():
        clean = _clean_counterparty(cp)
        if clean is None or _normalize(clean).upper() != counter_norm:
            continue
        if ttype.startswith("Sale"):
            sale_periods.add(pend)
            if amt is not None:
                sale_max = amt if sale_max is None else max(sale_max, amt)
        else:
            buy_periods.add(pend)
            if amt is not None:
                buy_max = amt if buy_max is None else max(buy_max, amt)
    return {
        "sale_periods": sale_periods,
        "buy_periods": buy_periods,
        "sale_max": sale_max,
        "buy_max": buy_max,
    }


def prune_symmetric_artifacts(
    conn: sqlite3.Connection,
    src: duckdb.DuckDBPyConnection,
    companies: dict[str, str],
    norm_to_name: dict[str, str],
    *,
    dry_run: bool = True,
) -> dict:
    """Existing-graph cleanup for same-ref symmetric emission (S2).

    Deterministic, same-ref scope ONLY — cross-ref pairs are never
    touched (that is the zero-evidence contradiction class, out of
    scope), and the mint-time equivalents in ``apply_pairs`` /
    ``apply_supply_pairs`` keep a re-run from re-creating either
    artifact:

    1. ``subsidiary_of`` cycles: both directions share one filer ref and
       classify sub_fwd + sub_rev (the S1 table: 37 live cycles, every
       one exactly this shape). The sub_rev edge is the filing-noise
       side; it is deleted. The surviving sub_fwd edge keeps its
       ``xbrl_url`` evidence untouched.
    2. ``supplier_to`` cross-period: both directions share one filer ref
       but are evidenced in DISJOINT period_end sets (S1: 91 of 4,452
       same-ref mutuals). Keep the larger-amount direction (tie: the
       sale direction); the upsert then refreshes the survivor's
       properties with both amounts + both period_ends.
    3. Entity-dup mutuals (normalized_name collision on the endpoints)
       are FLAGGED here, never merged (resolver-lane material).
    """
    norms = {
        r[0]: (r[1] or "")
        for r in conn.execute("SELECT name, normalized_name FROM entities").fetchall()
        if r[0]
    }
    counts = {
        "subsidiary_cycles_dropped": 0,
        "supplier_crossperiod_dropped": 0,
        "entity_dup_flagged": 0,
        "unresolved_symbol_skipped": 0,
    }
    mutuals = conn.execute(
        """
        SELECT e1.edge_type, e1.source, e1.target, e1.properties,
               e2.properties, e1.source_ref
        FROM graph_edges e1
        JOIN graph_edges e2
          ON e1.source = e2.target AND e1.target = e2.source
         AND e1.edge_type = e2.edge_type
        WHERE e1.edge_type IN ('supplier_to', 'subsidiary_of')
          AND e1.source < e1.target
          AND e1.source_ref = e2.source_ref
        """
    ).fetchall()
    deletions: list[tuple[str, str, str, str]] = []  # (source, target, edge_type, ref)
    for etype, a, b, p1, p2, ref in mutuals:
        if norms.get(a) and norms.get(a) == norms.get(b):
            counts["entity_dup_flagged"] += 1
            print(f"  [dup-flag] {etype} {a} <-> {b} (normalized-name collision; resolver lane)")
            continue
        if etype == "subsidiary_of":
            j1, j2 = json.loads(p1 or "{}"), json.loads(p2 or "{}")
            c1 = classify_relationship(j1.get("relationship"))
            c2 = classify_relationship(j2.get("relationship"))
            if {c1, c2} != {"sub_fwd", "sub_rev"}:
                continue  # not the S1 cycle shape; leave untouched
            loser_is_e1 = c1 == "sub_rev"
            loser = (a, b) if loser_is_e1 else (b, a)
            deletions.append((*loser, etype, ref))
            counts["subsidiary_cycles_dropped"] += 1
        else:  # supplier_to
            symbol = ref.rsplit(":", 1)[-1]
            filer = companies.get(symbol)
            if filer is None or filer not in (a, b):
                counts["unresolved_symbol_skipped"] += 1
                continue
            counter_ent: str = b if a == filer else a
            ev = _supply_direction_evidence(src, symbol, (norms.get(counter_ent) or "").upper())
            if not ev["sale_periods"] or not ev["buy_periods"]:
                continue
            if ev["sale_periods"] & ev["buy_periods"]:
                continue  # both directions evidenced in a shared period — legit
            sale_wins = (ev["sale_max"] or 0.0) >= (ev["buy_max"] or 0.0)
            loser_src: str = counter_ent if sale_wins else filer
            loser_dst: str = filer if sale_wins else counter_ent
            deletions.append((loser_src, loser_dst, etype, ref))
            counts["supplier_crossperiod_dropped"] += 1
    if deletions and not dry_run:
        with conn:
            for s, t, et, ref in deletions:
                conn.execute(
                    "DELETE FROM graph_edges WHERE source = ? AND target = ? "
                    "AND edge_type = ? AND source_ref = ?",
                    (s, t, et, ref),
                )
    return counts


def build_rating_pairs(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Latest rating per (company, agency)."""
    rows = con.execute(
        """
        SELECT DISTINCT ON (nse_symbol, rating_agency) nse_symbol,
               rating_agency, credit_rating, rating_action, date_of_rating,
               outlook, instrument_name, xbrl_url, red_flag_reason
        FROM credit_ratings
        WHERE nse_symbol IS NOT NULL AND rating_agency IS NOT NULL
        ORDER BY nse_symbol, rating_agency, date_of_rating DESC NULLS LAST
        """
    ).fetchall()
    return [
        {
            "symbol": r[0],
            "agency": r[1],
            "rating": r[2],
            "action": r[3],
            "date": r[4],
            "outlook": r[5],
            "instrument": r[6],
            "xbrl_url": r[7],
            "red_flag": r[8],
        }
        for r in rows
    ]


def apply_rating_pairs(
    conn: sqlite3.Connection,
    pairs: list[dict],
    companies: dict[str, str],
    *,
    dry_run: bool = True,
) -> tuple[int, int, int]:
    """Agency institution entities + rated_by edges (company -> agency).
    Returns (n_new_agencies, n_edges, n_written)."""
    existing = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    agencies: dict[str, str] = {}
    for p in pairs:
        ag = p["agency"]
        if ag not in agencies:
            display = _safe_display(ag)
            if display is None:
                continue
            agencies[ag] = display
    new_agencies = [d for d in agencies.values() if d not in existing]
    edges = []
    for p in pairs:
        filer = companies.get(p["symbol"])
        ag_ent = agencies.get(p["agency"])
        if filer is None or ag_ent is None or filer == ag_ent:
            continue
        edges.append((filer, ag_ent, p))
    n_written = 0
    if not dry_run:
        with conn:
            for d in new_agencies:
                conn.execute(
                    "INSERT OR IGNORE INTO entities (name, entity_type, "
                    "normalized_name, file_path) VALUES (?, 'institution', ?, NULL)",
                    (d, _normalize(d)),
                )
            for filer, ag_ent, p in edges:
                cur = conn.execute(
                    "INSERT INTO graph_edges (source, target, edge_type, weight, "
                    "properties, source_ref, symmetric, source_tier) "
                    "VALUES (?, ?, 'rated_by', 1.0, ?, ?, 0, 'external') "
                    "ON CONFLICT(source, target, edge_type) DO UPDATE SET "
                    "properties=excluded.properties, source_ref=excluded.source_ref",
                    (
                        filer,
                        ag_ent,
                        json.dumps(
                            {
                                "rating": p["rating"],
                                "action": p["action"],
                                "outlook": p["outlook"],
                                "date_of_rating": p["date"],
                                "instrument": p["instrument"],
                                "xbrl_url": p["xbrl_url"],
                                "red_flag_reason": p["red_flag"],
                            },
                            sort_keys=True,
                        ),
                        f"vigil:ratings:{p['symbol']}",
                    ),
                )
                n_written += cur.rowcount
    return len(new_agencies), len(edges), n_written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:  # noqa: C901  # pass-per-flag CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--download", action="store_true", help="bulk download + load the RPT table")
    ap.add_argument(
        "--ratings-download", action="store_true", help="bulk download + load credit_ratings"
    )
    ap.add_argument("--refetch", action="store_true", help="ignore the zstd cache")
    ap.add_argument("--apply", action="store_true", help="write edges (default: dry-run)")
    ap.add_argument(
        "--group",
        action="store_true",
        help="group-structure pass (subsidiary_of/same_group/jv_with)",
    )
    ap.add_argument(
        "--supply-chain", action="store_true", help="supplier_to pass from RPT transaction types"
    )
    ap.add_argument("--ratings", action="store_true", help="rated_by pass from credit_ratings")
    ap.add_argument("--max-new-entities", type=int, default=60000)
    args = ap.parse_args(argv)

    src = duckdb.connect(str(SOURCES_DB))
    if args.download:
        cache = download_bulk(refetch=args.refetch)
        n = load_table(src, cache)
        print(f"loaded rpt_transactions: {n} rows")
    if args.ratings_download:
        rc = download_ratings(refetch=args.refetch)
        n = load_ratings(src, rc)
        print(f"loaded credit_ratings: {n} rows")
    # no explicit pass -> all passes over whatever tables exist
    if not (args.group or args.supply_chain or args.ratings):
        args.group = args.supply_chain = True
        args.ratings = True

    from helpers.core.db import connect as sqlite_connect, DEFAULT_DB_PATH  # noqa: PLC0415

    conn = sqlite_connect(DEFAULT_DB_PATH)
    companies = _resolve_companies(src, conn)
    norm_to_name = {
        (r[0] or "").upper(): r[1]
        for r in conn.execute("SELECT normalized_name, name FROM entities").fetchall()
        if r[0]
    }
    mode = "APPLY" if args.apply else "dry-run"
    from collections import Counter

    if args.group or args.supply_chain:
        # Vigil symmetric-emission S2: clean existing same-ref artifacts
        # BEFORE the passes; the mint-time drops keep a re-run from
        # re-creating them. Same-ref scope only; dups flagged, not merged.
        pcounts = prune_symmetric_artifacts(
            conn, src, companies, norm_to_name, dry_run=not args.apply
        )
        print(
            f"[{mode}:prune] subsidiary_cycles_dropped={pcounts['subsidiary_cycles_dropped']} "
            f"supplier_crossperiod_dropped={pcounts['supplier_crossperiod_dropped']} "
            f"entity_dup_flagged={pcounts['entity_dup_flagged']} "
            f"unresolved_symbol_skipped={pcounts['unresolved_symbol_skipped']}"
        )
    if args.group:
        pairs = build_pairs(src)
        n_ent, n_edges, n_written, n_unres, n_cycles = apply_pairs(
            conn,
            pairs,
            companies,
            norm_to_name,
            max_new_entities=args.max_new_entities,
            dry_run=not args.apply,
        )
        by_cls = Counter(p["cls"] for p in pairs if p["symbol"] in companies)
        print(
            f"[{mode}:group] pairs={len(pairs)} by_class={dict(by_cls)} "
            f"new_entities={n_ent} edges={n_edges} written={n_written} "
            f"unresolved_filers={n_unres} cycles_dropped={n_cycles}"
        )
    if args.supply_chain:
        spairs = build_supply_pairs(src)
        n_edges, n_written, n_cross = apply_supply_pairs(
            conn, spairs, companies, norm_to_name, dry_run=not args.apply
        )
        print(
            f"[{mode}:supply] pairs={len(spairs)} supplier_to_edges={n_edges} "
            f"written={n_written} crossperiod_collapsed={n_cross}"
        )
    if args.ratings:
        try:
            rpairs = build_rating_pairs(src)
        except duckdb.CatalogException:
            rpairs = []
            print("(credit_ratings not loaded — run --ratings-download first)")
        if rpairs:
            n_ag, n_edges, n_written = apply_rating_pairs(
                conn, rpairs, companies, dry_run=not args.apply
            )
            print(
                f"[{mode}:ratings] pairs={len(rpairs)} new_agencies={n_ag} rated_by_edges={n_edges} written={n_written}"
            )
    if n_unres:
        print(f"(unresolved filers listed in {WORKLIST})")
        WORKLIST.write_text(
            "symbol\n"
            + "\n".join(sorted({p["symbol"] for p in pairs if p["symbol"] not in companies}))
            + "\n"
        )
    if args.apply:
        from helpers.graph.query import rebuild as duckdb_rebuild  # noqa: PLC0415

        duckdb_rebuild()
        print("duckdb property graph rebuilt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
