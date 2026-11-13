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
from datetime import datetime
from pathlib import Path

import duckdb
from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SOURCES_DB = REPO / "memory" / "data" / "sources.duckdb"
RAW_DIR = REPO / "memory" / "data" / "rpt_raw"
WORKLIST = REPO / "memory" / "data" / "rpt_worklist.csv"
BULK_URL = "https://api.tigzig.com/vigil/v1/download/rpt_transactions?format=csv.gz"
UA = {"User-Agent": "Mozilla/5.0 research", "Referer": "https://vigil.tigzig.com/"}
SOURCE_REF_PREFIX = "vigil:rpt"

# entity_name_dedup S3: the plan artifact doubles as the fold registry.
_NAME_DEDUP_PLAN = Path(__file__).resolve().parents[2] / "findata" / "Misc" / "_name_merge_plan.txt"

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.misc.duckdb_lock import connect_with_lock_retry  # noqa: E402  (post-bootstrap)
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


# R6a/C1 guards for the subsidiary/holding-company catch-alls: texts
# naming subsidiary-ADJACENT parties are never company↔company control
# relations. Plain substring markers (no regex) so the SQL CASE mirrors
# them exactly; classify_relationship is the executable ladder.
_PERSONNEL_MARKERS = (
    "kmp",
    "key management",
    "director",
    "relative",
    "personnel",
    "employee",
    "promoter of",
)
_RELATED_PARTY_MARKERS = ("related part", "realated part")

# A-prime (rpt_attribution §2.3): step-down-subsidiary texts carry INVERTED
# polarity — the disclosed party sits BELOW the counterparty, so the
# child→parent edge must target the counterparty (sub_rev). Plain
# substring markers (no regex) so the SQL CASE mirrors them exactly.
_POLARITY_REV = ("step down subsidiar", "stepdown subsidiar", "step-down subsidiar")


def _norm_rel(text: str | None) -> str:
    """Case-folded, whitespace/nbsp-collapsed relationship text — the
    Python mirror of the SQL's ``regexp_replace(lower(relationship),
    '\\s+', ' ', 'g')`` (filings carry ``\\xa0`` around 'of')."""
    return " ".join((text or "").lower().split())


def classify_relationship(text: str | None, rel_group: str | None = None) -> str | None:
    """Relationship text → edge class: the guarded ladder (R6a/C1).

    The pre-cure CASE ran ``%holding company%`` / ``%subsidiar%``
    catch-alls with no guards, so subsidiary-ADJACENT parties ("KMP of
    Subsidiary") and third-party-parentage descriptions ("Subsidiary of
    (Ultimate) Holding Company" — a SIBLING, not a parent) minted false
    child→parent edges between group companies (live example:
    TCS→Rallis India). The cure keeps the catch-alls as the LAST
    subsidiary rules and runs guards first:

    1. personnel/KMP/relative/related-party texts → None (no control
       relation exists for these).
    2. fellow subsidiaries, or ``subsidiar* of X`` where X names a
       THIRD party (any `` of `` except ``of the company`` = the filer
       itself) → same_group (co-membership, not parentage).
    3. joint venture → jv_with; associate / common control / promoter
       group → same_group (pre-cure behavior, unchanged).
    4. step-down-subsidiary texts (A-prime, rpt_attribution §2.3) →
       sub_rev as the TEXT-LEVEL default — the disclosed party sits
       BELOW the counterparty, so the child→parent edge targets the
       counterparty; `of the company` texts (filer-owned step-down subs)
       stay sub_fwd. Direction is then re-resolved PER FILING CONTEXT in
       apply_pairs (S3 correction, 2026-11-12): the bare string is used
       both ways across filers (see the ROUTE/Ashok-Leyland anchors
       there), so only self-listing and declared-ancestor rows keep
       sub_rev.
    5. residual `subsidiar` / `wholly owned` → sub_fwd — now only
       reachable by claims that the counterparty IS the filer's
       subsidiary.
    6. `holding compan` → sub_rev — counterparty IS the filer's
       holding company.
    7. rel_group fallback → same_group.

    Mirrored clause-for-clause in the _PAIRS_SQL CASE (over the
    nbsp-normalized text); pinned by
    test_classify_mirror_agrees_with_sql.
    """
    t = _norm_rel(text)
    if any(m in t for m in _PERSONNEL_MARKERS) or any(m in t for m in _RELATED_PARTY_MARKERS):
        return None
    if "fellow subsidiar" in t or ("subsidiar" in t and " of " in t and "of the company" not in t):
        return "same_group"
    if "joint venture" in t:
        return "jv_with"
    if "associat" in t or "common control" in t or "promoter group" in t:
        return "same_group"
    # A-prime (rpt_attribution §2.3): step-down texts mean the disclosed
    # party is a step-down subsidiary OF the counterparty — child→parent
    # must target the counterparty. 'of the company' texts (filer-owned
    # step-down subs) keep sub_fwd.
    if any(m in t for m in _POLARITY_REV) and "of the company" not in t:
        return "sub_rev"
    if "subsidiar" in t or "wholly owned" in t:
        return "sub_fwd"
    if "holding compan" in t:
        return "sub_rev"
    if rel_group in ("Group Companies", "Promoter Group", "Common Control/Influence"):
        return "same_group"
    return None


# Canonical SQL form of classify_relationship's guarded ladder (R6a/C1),
# evaluated over ``r`` = regexp_replace(lower(relationship), '\\s+', ' ',
# 'g') — see _PAIRS_SQL for the embedding. Kept as the reference block;
# _PAIRS_SQL and classify_relationship are the executable homes.
_CLASSIFY_SQL = """
CASE
  WHEN regexp_matches(r, 'kmp|key management|director|relative|personnel|employee|promoter of|related part|realated part')
    THEN NULL
  WHEN r LIKE '%fellow subsidiar%'
       OR (r LIKE '%subsidiar%' AND r LIKE '% of %'
           AND r NOT LIKE '%of the company%') THEN 'same_group'
  WHEN r LIKE '%joint venture%' THEN 'jv_with'
  WHEN r LIKE '%associat%' OR r LIKE '%common control%'
       OR r LIKE '%promoter group%' THEN 'same_group'
  WHEN (r LIKE '%step down subsidiar%' OR r LIKE '%stepdown subsidiar%'
        OR r LIKE '%step-down subsidiar%')
       AND r NOT LIKE '%of the company%' THEN 'sub_rev'
  WHEN r LIKE '%subsidiar%' OR r LIKE '%wholly owned%' THEN 'sub_fwd'
  WHEN r LIKE '%holding compan%' THEN 'sub_rev'
  WHEN rel_group IN ('Group Companies', 'Promoter Group',
                     'Common Control/Influence') THEN 'same_group'
END
"""

_GARBAGE = re.compile(r"^\d+$|^\d+\s+[A-Z]|PO BOX|\bAVE\b|\bSTREET\b|\bROAD\b")

# Employee-benefit instruments vetoed as counter-parties (see
# _clean_counterparty). 'pension'/'superannuation' carry a negative
# lookahead so PFRDA-licensed pension fund MANAGEMENT companies survive.
_BENEFIT_SCHEME_RE = re.compile(
    r"\bgratuity\b|\bprovident\b"
    r"|superannuation(?!\s*(?:fund\s*)?management)"
    r"|pension(?!\s*(?:fund\s*)?management)",
    re.I,
)


_DIRECT_OWNERSHIP_TEXT = frozenset(
    {
        "holding company",
        "ultimate holding company",
        "intermediate holding company",
        "immediate holding company",
        "parent",
        "promoter",
        "promoters",
    }
)


def _is_group_relative_text(rel_norm: str) -> bool:
    """True when a relationship text names a holding company/promoter of the
    FILER rather than a direct owner of the disclosed entity.

    Bare ownership texts ("Holding Company", "Ultimate Holding Company",
    "Parent") are DIRECT claims about the disclosed entity and are read
    entity-relative (the REDINGTON RWANDA rule). Generic references ("Subsidiary
    of Holding Company", "Joint venture of Holding Company", "Promoter Group")
    are group-relative: read against a subsidiary subject they mint a parent
    chain instead of a sibling relation under the filer group. "holding company
    (" prefixed forms (e.g. "holding company (body corporate)") stay direct —
    they quantify the holding itself.

    Refined 2026-11-12 after two refuted rounds: flattening ALL subject!=filer
    sub_fwd inflated same_group 3.2x and destroyed real parent claims; the
    self-disclosure (company_name vs entity) discriminator also failed because
    filer-self rows still carry entity-relative truths (VOLTAS/TCS). Only the
    generic holdco/promoter texts flatten."""
    if rel_norm in _DIRECT_OWNERSHIP_TEXT or rel_norm.startswith("holding company ("):
        return False
    return "holding compan" in rel_norm or "promoter" in rel_norm


def _safe_display(name: str) -> str | None:
    """clean_name + CHECK-safe repair; None = unusable display.

    Filings text arrives mangled ('Privated limited', 'Solutionspvt' —
    glued, no word boundary) and survives clean_name; the entities CHECK
    is a substring LIKE, so any residue is rejected by INSERT OR IGNORE
    and would blow up the edge FK. Scrub everywhere, not just suffixes.
    entity_name_dedup S3: trailing footnote markers ('**' bold/table
    residue) are stripped first — a marker variant can never be born."""
    from helpers.maintenance.exchange_sync import clean_name  # noqa: PLC0415
    from helpers.core.entity_identity import FOOTNOTE_RE  # noqa: PLC0415

    d = clean_name(FOOTNOTE_RE.sub("", (name or "").strip()))
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
# S6 (quadratic_regex_sweep_and_cleanups): the leading `\s*` was O(n^2) on a
# long whitespace run. `(?<=\s)` supplies the same "preceded by whitespace"
# boundary at zero width; the caller collapses runs and strips afterwards.
_PAREN = re.compile(r"(?<!\s)\s*\((?:formerly|formerly known as|now known as)[^)]*\)\s*", re.I)


def _clean_counterparty(name: str) -> str | None:
    """Identity form of a counter-party name: parentheticals stripped,
    garbage rejected. None = unusable.

    2026-10-16 operator exclusion (#400 flag): employee-BENEFIT
    instruments — gratuity/provident funds, superannuation and pension
    schemes/trusts — are never counter-parties. Ind AS 24 makes them
    related parties, so filings surface them constantly, but the relation
    vocabulary has no type for 'company administers its benefit trust':
    same_group/subsidiary_of/supplier_to are all wrong classes. Pension
    FUND MANAGEMENT companies (ABSL/HDFC/Tata/Axis — operating PFRDA
    licensees) are exempt via negative lookahead. Deliberately
    over-inclusive in the safe direction: a mis-vetoed operating company
    loses only its vigil edges, while one leaked instrument grouped
    'MedPlus Employees group gratuity assurance scheme' into a corporate
    group."""
    n = _PAREN.sub(" ", (name or "").strip())
    n = re.sub(r"\s{2,}", " ", n).strip(" \"',")
    if not 3 <= len(n) <= 90 or _GARBAGE.search(n.upper()):
        return None
    if _BENEFIT_SCHEME_RE.search(n):
        return None
    return n


_PAIRS_SQL = """
    SELECT DISTINCT ON (symbol, entity_name, counter_party) symbol, entity_name,
           company_name,
           counter_party, relationship, rel_group, transaction_type,
           amount_during_period, period_end_date, xbrl_url,
    CASE
      WHEN regexp_matches(r, 'kmp|key management|director|relative|personnel|employee|promoter of|related part|realated part')
        THEN NULL
      WHEN r LIKE '%fellow subsidiar%'
           OR (r LIKE '%subsidiar%' AND r LIKE '% of %'
               AND r NOT LIKE '%of the company%') THEN 'same_group'
      WHEN r LIKE '%joint venture%' THEN 'jv_with'
      WHEN r LIKE '%associat%' OR r LIKE '%common control%'
           OR r LIKE '%promoter group%' THEN 'same_group'
      WHEN (r LIKE '%step down subsidiar%' OR r LIKE '%stepdown subsidiar%'
            OR r LIKE '%step-down subsidiar%')
           AND r NOT LIKE '%of the company%' THEN 'sub_rev'
      WHEN r LIKE '%subsidiar%' OR r LIKE '%wholly owned%' THEN 'sub_fwd'
      WHEN r LIKE '%holding compan%' THEN 'sub_rev'
      WHEN rel_group IN ('Group Companies', 'Promoter Group',
                         'Common Control/Influence') THEN 'same_group'
    END AS cls
    FROM (
      SELECT *, regexp_replace(lower(relationship), '\\s+', ' ', 'g') AS r
      FROM rpt_transactions
      WHERE symbol IS NOT NULL AND counter_party IS NOT NULL
        AND entity_name IS NOT NULL
    )
    -- Tie-break inside a (symbol, entity_name, counter_party, period)
    -- tie: prefer the row whose relationship text the ownership closure
    -- trusts (metapath._STRICT_OWNERSHIP_REL). Pre-flight found the
    -- TCS/'Tata sons' key otherwise landing on 'Ultimate Holding
    -- Company', which the trust filter rejects — silently dropping
    -- Tata sons as TCS's parent and re-rooting TCS under Voltas.
    ORDER BY symbol, entity_name, counter_party, period_end_date_parsed DESC NULLS LAST,
             CASE WHEN r IN ('subsidiary', 'subsidiaries', 'subsidiary company',
                             'wholly owned subsidiary', 'wholly owned subsidiaries',
                             'wholly-owned subsidiary', 'wholly-owned subsidiaries',
                             'wholly owned subsidiary company', 'step down subsidiary',
                             'stepdown subsidiary', 'subsidiary of the company',
                             'holding company')
                  THEN 0 ELSE 1 END
    """


def build_pairs(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Distinct (filer symbol, counter-party, class) pairs with the latest
    filing's attributes per pair (_PAIRS_SQL mirrors _CLASSIFY_SQL)."""
    rows = con.execute(_PAIRS_SQL).fetchall()
    out = []
    for sym, ent, cname, cp, rel, grp, ttype, amt, pend, url, cls in rows:
        if cls is None:
            continue
        clean = _clean_counterparty(cp)
        if clean is None:
            continue
        out.append(
            {
                "symbol": sym,
                "entity": ent,
                "company_name": cname,
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


def _fold_map() -> dict[str, str]:
    """Folded-variant → canonical map from the S1 plan artifact (the
    registry IS the artifact: entity_name_dedup §S2). Delegates to the
    one home (helpers.core.entity_identity.fold_registry) with THIS
    module's plan path kept as the monkeypatch seam for tests."""
    from helpers.core import entity_identity as ei

    return ei.fold_registry(_NAME_DEDUP_PLAN)


def _census_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Census-key → entity name over the entities registry. Delegates to
    helpers.core.entity_identity.registry_index (the one home): case,
    punctuation, footnote-marker, apostrophe and compound-join variants
    of an existing entity resolve to it instead of minting a duplicate
    (entity_name_dedup S3 resolve-before-insert)."""
    from helpers.core import entity_identity as ei

    return ei.registry_index(conn)


_LEGAL_TAIL_RE = re.compile(r"[_ ](PRIVATE|PVT|LIMITED|LTD|COMPANY|CORPORATION|CORP|INC|LLP|CO)$")


def _loose_key(name: str | None) -> str:
    """Normalized name with legal-form tails and a leading 'THE' stripped, so
    the same company written the way two filings write it lands on one key:
    'The Indian Hotels Company Limited' and 'Indian Hotels' both give
    'INDIAN_HOTELS'. Used to notice that a disclosed entity_name is the FILER
    under another spelling — a disclosed subject that is really the filer must
    not be minted as a second entity (A4 fragmentation: it split 'Indian
    Hotels' into four nodes and emptied the bank's jv/same_group pins)."""
    if not name:
        return ""
    key = _normalize(name).upper()
    while True:
        nxt = re.sub(r"^THE[_ ]", "", _LEGAL_TAIL_RE.sub("", key))
        if nxt == key or not nxt:
            return key
        key = nxt


def _resolve_party(  # the shared disclosed-party resolution ladder
    raw: str,
    norm_to_name: dict[str, str],
    fold: dict[str, str],
    census_map: dict[str, str],
    existing: set[str],
    new_entities: dict[str, tuple[str, str]],
    *,
    max_new_entities: int,
) -> str | None:
    """S2 (rpt_attribution A4): resolve a disclosed party name to an
    entity — the same ladder the counter-party walk uses: normalized hit
    → fold/census variant → mint under the shared cap. None = unresolvable
    (the caller drops the pair and counts it). No filer guard: a subject
    that resolves to the counterparty is a self-edge and drops there."""
    from helpers.core.entity_identity import FOOTNOTE_RE, name_key  # noqa: PLC0415

    norm = _normalize(raw)
    hit = norm_to_name.get(norm.upper())
    ck = name_key(FOOTNOTE_RE.sub("", raw.strip()))
    if hit is None:
        hit = fold.get(ck) or census_map.get(ck)
    if hit is None:
        display = _safe_display(raw)
        if display:
            # Within-run mints are indexed by CENSUS KEY, not by display
            # string: the registry (census_map) folds case/punctuation
            # variants of every PRE-EXISTING entity, but a name minted
            # earlier in THIS run was invisible to it, so a second spelling
            # of the same name minted a duplicate entity row (the 2026-11-11
            # replay minted 402 case-variant twins). First spelling wins,
            # exactly like registry_index.
            minted = new_entities.get(ck)
            if minted is not None:
                hit = minted[0]
            elif display in existing:
                hit = display
            elif len(new_entities) < max_new_entities:
                new_entities[ck] = (display, norm)
                hit = display
    return hit


def _period_sort(period: str | None) -> tuple[int, int, int]:
    """Filing period_end 'DD-MON-YYYY' as a sortable key; anything the
    filings do not write that way sorts as (0, 0, 0) so an unparseable
    period never wins a recency tie."""
    if period:
        try:
            d = datetime.strptime(period.strip(), "%d-%b-%Y")
            return (d.year, d.month, d.day)
        except ValueError:
            return (0, 0, 0)
    return (0, 0, 0)


def apply_pairs(  # noqa: C901  # the filer/subject/counter/edge-class resolution ladder
    conn: sqlite3.Connection,
    pairs: list[dict],
    companies: dict[str, str],
    norm_to_name: dict[str, str],
    *,
    max_new_entities: int = 60000,
    dry_run: bool = True,
) -> tuple[int, int, int, int, int, int, int, int]:
    """Create subject/counter-party entities + structural edges.

    Returns (n_new_entities, n_edges_would_or_written, n_written,
    n_unresolved_filers, n_cycles_dropped, n_noncompany_dropped,
    n_subject_unresolvable, n_vote_dropped). Upsert-latest per
    (source, target, edge_type).

    S2 (rpt_attribution A4): the edge SUBJECT is the filing's disclosed
    entity_name (resolved through the same ladder as the counter-party,
    minting under the shared cap), not the filer — a 'Holding Company'
    row filed by RELIANCE with entity_name 'Lithium Werks China
    Manufacturing Co., Ltd.' is Lithium-Werks→Reliance, not the inverted
    edge that re-rooted 238 companies under 'Reliance Lithium Werks
    B.V'. The filer stays the subject for 'of the company' texts (the
    counterparty is the filer's own subsidiary there). An unresolved
    subject drops the pair; a subject equal to the counterparty is a
    self-edge and drops.

    A sub_fwd row whose subject is NOT the filer is only a SIBLING
    relation unless the filer itself wrote the text — the 'Subsidiary'
    label is relative to whoever spoke. See the A4 discriminator below
    (pre-flight: VOLTAS's 'Universal MEP / Tata Consultancy Services /
    Subsidiary' group row otherwise re-rooted TCS under Voltas).

    2026-11-03 operator ruling: institutional investors (LIC, HDFC
    LIFE, ...) are PORTFOLIO holders, not group members — an insurer
    holding market positions across hundreds of listed companies is
    exactly what same_group must not assert. same_group pairs with a
    non-company-typed endpoint are dropped at mint (counted as
    n_noncompany_dropped); the e_group CTAS already filtered them
    (singular src_kind/dst_kind company), so they were invisible
    noise accruing in SQLite. subsidiary_of/jv_with are untouched:
    person-parent subsidiary rows are the plural-kind lanes' doctrine,
    and a JV partnership claim is genuine even with an insurer partner.
    """

    existing = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
    noncompany = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM entities WHERE COALESCE(entity_type, '') != 'company'"
        ).fetchall()
    }
    # entity_name_dedup S3 resolve-before-insert: a counter whose census
    # key matches an existing entity (or a plan-folded variant) reuses
    # that name — no duplicate entity is minted for a spelling variant.
    census_map = _census_names(conn)
    fold = _fold_map()
    # S3 polarity-correction context (ruled slice, 2026-11-12): counters
    # each symbol's filer declares as ITS OWN holding company (rows where
    # entity_name ~= company_name and the class is sub_rev). A bare
    # step-down row whose counter is in this set points UP — the subject
    # is the counter's step-down sub (ROUTE pattern). Everything else
    # points DOWN (the counter is the subject's step-down sub).
    filer_ancestors: dict[str, set[str]] = {}
    for p in pairs:
        if (
            p.get("company_name")
            and p["cls"] == "sub_rev"
            and _loose_key(p["entity"]) == _loose_key(p["company_name"])
        ):
            filer_ancestors.setdefault(p["symbol"], set()).add(_loose_key(p["counter"]))
    edges: list[dict] = []
    new_entities: dict[str, tuple[str, str]] = {}
    unresolved_filers: set[str] = set()
    n_noncompany_dropped = 0
    n_subject_unresolvable = 0
    for p in pairs:
        filer = companies.get(p["symbol"])
        if filer is None:
            unresolved_filers.add(p["symbol"])
            continue
        # S2 (rpt_attribution A4): the edge subject is the filing's
        # disclosed entity_name — except 'of the company' texts, where the
        # counterparty is the filer's own subsidiary and the filer stays
        # the subject.
        if "of the company" in _norm_rel(p["relationship"]):
            subject = filer
        elif _loose_key(p["entity"]) == _loose_key(filer):
            # The disclosed entity_name is the filer under another spelling
            # ('The Indian Hotels Company Limited' filed by INDHOTEL). Minting
            # it would fork the filer into a second node and orphan every edge
            # that names the filer — A4 subject attribution's one real cost.
            subject = filer
        else:
            subject = _resolve_party(
                p["entity"],
                norm_to_name,
                fold,
                census_map,
                existing,
                new_entities,
                max_new_entities=max_new_entities,
            )
            if subject is None:
                n_subject_unresolvable += 1
                continue
        cp_ent = _resolve_party(
            p["counter"],
            norm_to_name,
            fold,
            census_map,
            existing,
            new_entities,
            max_new_entities=max_new_entities,
        )
        if cp_ent is None or cp_ent == subject:
            continue
        if p["cls"] == "same_group" and (filer in noncompany or cp_ent in noncompany):
            n_noncompany_dropped += 1
            continue  # institutional investor — portfolio holding, not group membership
        cls = p["cls"]
        # A relationship text is read relative to entity_name (the subject) —
        # verified against the store: REDINGTON row (entity_name 'REDINGTON
        # RWANDA LTD', counter_party 'Redington Gulf FZE', relationship
        # 'Holding company') means Gulf FZE is Rwanda's parent, and only that
        # reading is true. So sub_fwd/sub_rev orientation needs no change.
        #
        # But some texts are written relative to the FILER's holding company,
        # not to the disclosed entity: 'Subsidiary of Holding Company',
        # 'Subsidiaries of Ultimate Holding Company', 'Joint venture of
        # Holding Company'. Read against a SUBSIDIARY subject they mint a
        # parent chain for that subsidiary instead of a sibling relation under
        # the filer's group (live: this is why Indian Hotels' 57 jv edges
        # collapsed to 10 and JSW Energy's same_group 211 -> 119). When the
        # text names a holding company/promoter AND the subject is not the
        # filer itself, the pair is a group relation under the filer.
        if subject != filer and cls in ("sub_fwd", "sub_rev", "jv_with"):
            rel_norm = _norm_rel(p["relationship"])
            if _is_group_relative_text(rel_norm):
                cls = "same_group"
        # S3 polarity correction (ruled slice, 2026-11-12): the BARE
        # step-down text is direction-ambiguous — the same string points
        # up in some filings (ROUTE: 'Route Mobile (UK) Limited' +
        # 'Step down subsidiary' + Proximus = the entity IS the step-down
        # sub) and down in others (Ashok Leyland/ADVENZYMES: the counter IS
        # the entity's step-down sub; the A-prime flip inverted those).
        # Resolved per pair from filing context: subject == filer → the
        # filer listing its own step-downs (sub_fwd); counter declared as
        # the FILER's holding company elsewhere in the same symbol's
        # rows → the subject sits below it (keep sub_rev); otherwise →
        # sub_fwd (the default, AL pattern).
        rerouted: str | None = None
        if cls == "sub_rev" and any(m in _norm_rel(p["relationship"]) for m in _POLARITY_REV):
            counter_is_filer_ancestor = _loose_key(p["counter"]) in filer_ancestors.get(
                p["symbol"], set()
            )
            if subject == filer or not counter_is_filer_ancestor:
                cls = "sub_fwd"
                rerouted = "self" if subject == filer else "default"
                # a reroute is a direction GUESS from an ambiguous text —
                # flagged so the cross-ref vote can resolve reroute-made
                # mutual conflicts instead of keeping both sides as a
                # phantom co-control tie.
        if cls == "same_group" and (subject in noncompany or cp_ent in noncompany):
            n_noncompany_dropped += 1
            continue  # institutional investor — portfolio holding, not group membership
        if cls == "sub_fwd":
            src, dst, etype, symm = cp_ent, subject, "subsidiary_of", 0
        elif cls == "sub_rev":
            src, dst, etype, symm = subject, cp_ent, "subsidiary_of", 0
        elif cls == "same_group":
            src, dst = (cp_ent, subject) if cp_ent < subject else (subject, cp_ent)
            etype, symm = "same_group", 1
        else:  # jv_with
            src, dst = (cp_ent, subject) if cp_ent < subject else (subject, cp_ent)
            etype, symm = "jv_with", 1
        edges.append(
            {
                "src": src,
                "dst": dst,
                "etype": etype,
                "symm": symm,
                "cls": cls,
                "rerouted": rerouted,
                "period": p["period_end"],
                "url": p["xbrl_url"],
                "rel": _norm_rel(p["relationship"]),
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
    # S2 cross-ref vote (rpt_attribution §4): two filers can assert
    # OPPOSITE directions of the same subsidiary pair. The vote keeps the
    # direction backed by more distinct filings (xbrl_url); a tie of
    # EXPLICIT sub_fwd claims keeps both (a mutual claim is co-control,
    # not a cycle — the closure's cycle-guard handles the residue). A tie
    # that carries reroute-made claims resolves by strength + recency
    # below, because those claims are guesses, not assertions.
    n_vote_dropped = 0
    own = [e for e in edges if e["etype"] == "subsidiary_of"]
    fwd: dict[tuple[str, str], set[str]] = {}
    rev: dict[tuple[str, str], set[str]] = {}
    for e in own:
        key = tuple(sorted((e["src"], e["dst"])))
        (fwd if e["src"] < e["dst"] else rev).setdefault(key, set()).add(e["url"])
    mutual = set(fwd) & set(rev)
    dropped_v: set[int] = set()
    for key in mutual:
        n_f, n_r = len(fwd[key]), len(rev[key])
        key_edges = [e for e in own if tuple(sorted((e["src"], e["dst"]))) == key]
        if n_f == n_r:
            losers = [e for e in key_edges if e["cls"] != "sub_fwd"]
            # Polarity-slice refinement: a rerouted bare step-down text is a
            # direction GUESS, not an explicit claim. When a tie key carries
            # reroute-made claims on both sides (ASHOKLEY DSD: the FY26 row
            # pairs (Hinduja Tech, DSD UK) and the 30-SEP-2025 row pairs
            # (DSD UK, Hinduja Tech) — both rerouted sub_fwd), the old
            # co-control tie minted a cycle from one ambiguous relationship.
            # Resolve by claim strength (explicit text > filer-self reroute
            # > default-down reroute), then filing recency. Keys carrying
            # only explicit claims keep the old tie.
            sub_fwd_edges = [e for e in key_edges if e["cls"] == "sub_fwd"]
            if any(e.get("rerouted") for e in key_edges) and len(sub_fwd_edges) > 1:
                strength = {"default": 0, "self": 1}
                winner = max(
                    sub_fwd_edges,
                    key=lambda e: (
                        strength.get(e.get("rerouted"), 2),
                        _period_sort(e.get("period")),
                    ),
                )
                losers += [e for e in sub_fwd_edges if e is not winner]
            for e in losers:
                dropped_v.add(id(e))
                n_vote_dropped += 1
        else:
            for e in key_edges:
                is_fwd = e["src"] < e["dst"]
                if (n_f > n_r) != is_fwd:
                    dropped_v.add(id(e))
                    n_vote_dropped += 1
    if dropped_v:
        edges = [e for e in edges if id(e) not in dropped_v]
    # S2/S4 pre-flight finding (TCS re-root): several DIFFERENT pair keys
    # resolve onto the same (source, target, edge_type) edge -- a filer's
    # 'Holding Company' row and its subsidiaries' 'Ultimate Holding Company'
    # rows all key TCS -> Tata Sons. The write path is upsert-LATEST, so an
    # untrusted row could overwrite the trusted one and
    # metapath._trust_ownership_edge then refuses the edge, re-rooting TCS at
    # itself. A trust-passing claim is never downgraded here; among equally
    # trusted claims the last row still wins (the historical semantics).
    from helpers.graph.metapath import _STRICT_OWNERSHIP_REL  # noqa: PLC0415

    def _trust_passing(edge: dict) -> bool:
        return edge["etype"] == "subsidiary_of" and _norm_rel(edge["rel"]) in _STRICT_OWNERSHIP_REL

    keep_pos: dict[tuple[str, str, str], int] = {}
    deduped: list[dict] = []
    for e in edges:
        ekey = (e["src"], e["dst"], e["etype"])
        pos = keep_pos.get(ekey)
        if pos is None:
            keep_pos[ekey] = len(deduped)
            deduped.append(e)
        elif _trust_passing(deduped[pos]) and not _trust_passing(e):
            continue  # never downgrade a trusted ownership claim
        else:
            deduped[pos] = e
    edges = deduped

    n_written = 0
    if not dry_run:
        with conn:
            for _key, (display, norm) in new_entities.items():
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
                    (
                        e["src"],
                        e["dst"],
                        e["etype"],
                        e["props"],
                        e["source_ref"],
                        e["symm"],
                    ),
                )
                n_written += cur.rowcount
    return (
        len(new_entities),
        len(edges),
        n_written,
        len(unresolved_filers),
        n_cycles_dropped,
        n_noncompany_dropped,
        n_subject_unresolvable,
        n_vote_dropped,
    )


def purge_rpt_edges(
    conn: sqlite3.Connection, edge_types: list[str], *, dry_run: bool = True
) -> dict[str, int]:
    """S2 replay hygiene (rpt_attribution §4 S4): the vigil:rpt namespace
    is fully derived from rpt_transactions, but the write path is
    upsert-only — without a purge, pairs that stop existing (attribution
    re-keys them) survive as stale edges forever. Returns per-type
    counts either way; deletes only when dry_run is false."""
    pattern = f"{SOURCE_REF_PREFIX}:%"
    counts: dict[str, int] = {}
    for etype in edge_types:
        counts[etype] = conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE source_ref LIKE ? AND edge_type=?",
            (pattern, etype),
        ).fetchone()[0]
    if not dry_run:
        with conn:
            for etype in edge_types:
                conn.execute(
                    "DELETE FROM graph_edges WHERE source_ref LIKE ? AND edge_type=?",
                    (pattern, etype),
                )
    return counts


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


def apply_supply_pairs(  # noqa: C901
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

    census_map = _census_names(conn)
    fold = _fold_map()
    edges = []
    n_cross = 0
    for p in pairs:
        filer = companies.get(p["symbol"])
        if filer is None:
            continue
        cp_ent = norm_to_name.get(_normalize(p["counter"]).upper())
        if cp_ent is None:
            from helpers.core.entity_identity import FOOTNOTE_RE, name_key  # noqa: PLC0415

            ck = name_key(FOOTNOTE_RE.sub("", p["counter"].strip()))
            hit = fold.get(ck) or census_map.get(ck)
            if hit and hit != filer:
                cp_ent = hit
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
        "same_group_cycles_dropped": 0,
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
        WHERE e1.edge_type IN ('supplier_to', 'subsidiary_of', 'same_group')
          AND e1.source < e1.target
          AND (e1.source_ref = e2.source_ref OR e1.edge_type = 'same_group')
        """
    ).fetchall()
    deletions: list[tuple[str, str, str, str]] = []  # (source, target, edge_type, ref)
    for etype, a, b, p1, p2, ref in mutuals:
        if norms.get(a) and norms.get(a) == norms.get(b):
            counts["entity_dup_flagged"] += 1
            print(f"  [dup-flag] {etype} {a} <-> {b} (normalized-name collision; resolver lane)")
            continue
        loser = None
        if etype == "subsidiary_of":
            loser = _subsidiary_cycle_loser(p1, p2, a, b)
            if loser:
                counts["subsidiary_cycles_dropped"] += 1
        elif etype == "same_group":
            # Symmetric-declared, single-row-per-pair (alphabetical) contract:
            # a doubled row (both A->B and B->A) is the G3 bug class. Keep the
            # alphabetical (a < b) row, drop the reversed one.
            loser = (b, a)
            counts["same_group_cycles_dropped"] += 1
        else:  # supplier_to
            loser = _supplier_crossperiod_loser(src, ref, a, b, norms, companies, counts)
            if loser:
                counts["supplier_crossperiod_dropped"] += 1
        if loser:
            deletions.append((*loser, etype, ref))
    _apply_deletions(conn, deletions, dry_run=dry_run)
    return counts


def _apply_deletions(
    conn: sqlite3.Connection,
    deletions: list[tuple[str, str, str, str]],
    *,
    dry_run: bool,
) -> None:
    """Delete the losers, ref-scoped when a source_ref exists. Split out
    of `prune_symmetric_artifacts` for C901; row-for-row the previous
    inline tail."""
    if deletions and not dry_run:
        with conn:
            for s, t, et, ref in deletions:
                if ref is None:
                    conn.execute(
                        "DELETE FROM graph_edges WHERE source = ? AND target = ? AND edge_type = ?",
                        (s, t, et),
                    )
                else:
                    conn.execute(
                        "DELETE FROM graph_edges WHERE source = ? AND target = ? "
                        "AND edge_type = ? AND source_ref = ?",
                        (s, t, et, ref),
                    )


def _subsidiary_cycle_loser(
    p1: str | None, p2: str | None, a: str, b: str
) -> tuple[str, str] | None:
    """The S1 cycle shape: both directions share one filer ref and
    classify sub_fwd + sub_rev — the sub_rev edge is the filing-noise
    side and loses. None = not that shape (leave untouched)."""
    j1, j2 = jsonio.loads(p1 or "{}"), jsonio.loads(p2 or "{}")
    c1 = classify_relationship(j1.get("relationship"))
    c2 = classify_relationship(j2.get("relationship"))
    if {c1, c2} != {"sub_fwd", "sub_rev"}:
        return None
    return (a, b) if c1 == "sub_rev" else (b, a)


def _supplier_crossperiod_loser(
    src: duckdb.DuckDBPyConnection,
    ref: str,
    a: str,
    b: str,
    norms: dict[str, str],
    companies: dict[str, str],
    counts: dict,
) -> tuple[str, str] | None:
    """Cross-period supplier_to mutual: keep the larger-amount direction
    (tie: the sale direction). None = keep both (disjoint-period
    evidence not established, or an unresolved filer symbol — the latter
    counted as unresolved_symbol_skipped). ref is non-null here by the
    mutual SQL: the branch requires e1.source_ref = e2.source_ref, and
    NULL refs never compare equal."""
    symbol = ref.rsplit(":", 1)[-1]
    filer = companies.get(symbol)
    if filer is None or filer not in (a, b):
        counts["unresolved_symbol_skipped"] += 1
        return None
    counter_ent: str = b if a == filer else a
    ev = _supply_direction_evidence(src, symbol, (norms.get(counter_ent) or "").upper())
    if not ev["sale_periods"] or not ev["buy_periods"]:
        return None
    if ev["sale_periods"] & ev["buy_periods"]:
        return None  # both directions evidenced in a shared period — legit
    sale_wins = (ev["sale_max"] or 0.0) >= (ev["buy_max"] or 0.0)
    return (counter_ent, filer) if sale_wins else (filer, counter_ent)


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
            # polyadic S4: a folded variant (name_dedup ledger — e.g.
            # 'Acute Ratings & Research', 'CRISIL Ratings') must resolve to
            # its canonical BEFORE the mint check, or the next vigil sync
            # re-mints the merged-away entity (the fold's reversibility).
            from helpers.core.entity_identity import name_key  # noqa: PLC0415

            display = _fold_map().get(name_key(display), display)
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
# subsidiary_of cure (R6a/C2)
# --------------------------------------------------------------------------- #


def _cure_sample(samples: dict, bucket: str, src: str, dst: str, rel) -> None:
    if len(samples.setdefault(bucket, [])) < 5:
        samples[bucket].append((src, dst, str(rel)[:80]))


def cure_subsidiary_backfill(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """One-shot reclassification of existing ``vigil:*`` subsidiary_of
    edges under the guarded ladder (R6a/C1 — see classify_relationship).

    The mint-side classifier is cured at the source, but the store
    already holds the polluted edges. Dispositions: strict claims keep;
    sibling / third-party-parentage descriptions reclass to same_group
    (canonical orientation, symmetric, properties + source_ref carried
    so the filing remains the provenance); texts that now classify
    jv_with reclass to jv_with; personnel / related-party texts delete
    (no company↔company relation exists for them), including rows whose
    properties no longer parse. Non-vigil subsidiary_of rows (prose
    extractor) are untouched — that lane owns its own convention.
    Idempotent: survivors re-classify to keep. graph_edges triggers bump
    db_meta.generation on write; run the derived-store + CSR rebuild
    chain afterwards (graph-rebuild → stamp-centrality → csr-rebuild).

    Returns ``{"counts": {...}, "samples": {disposition: [(source,
    target, relationship), ...] top-5}}`` — dry-run reports the plan
    without writing.
    """
    rows = conn.execute(
        "SELECT id, source, target, properties, source_ref FROM graph_edges "
        "WHERE edge_type='subsidiary_of' AND source_ref LIKE 'vigil:%'"
    ).fetchall()
    counts = {
        "scanned": len(rows),
        "keep": 0,
        "to_same_group": 0,
        "to_jv_with": 0,
        "dropped": 0,
        "unparsable_dropped": 0,
    }
    samples: dict[str, list] = {}
    delete_ids: list[int] = []
    reclass: list[tuple[str, str, str, str, str]] = []
    for rid, src, dst, props, ref in rows:
        try:
            rel = jsonio.loads(props or "{}").get("relationship")
        except TypeError, ValueError:
            rel = None
        cls = classify_relationship(rel)
        if cls in ("sub_fwd", "sub_rev"):
            counts["keep"] += 1
            _cure_sample(samples, "keep", src, dst, rel)
        elif cls in ("same_group", "jv_with"):
            counts["to_same_group" if cls == "same_group" else "to_jv_with"] += 1
            a, b = (src, dst) if src < dst else (dst, src)
            reclass.append((a, b, cls, props, ref))
            delete_ids.append(rid)
            _cure_sample(samples, cls, src, dst, rel)
        else:
            counts["dropped"] += 1
            if rel is None:
                counts["unparsable_dropped"] += 1
            delete_ids.append(rid)
            _cure_sample(samples, "dropped", src, dst, rel)
    if apply:
        with conn:
            for src, dst, etype, props, ref in reclass:
                conn.execute(
                    "INSERT INTO graph_edges (source, target, edge_type, weight, "
                    "properties, source_ref, symmetric, source_tier) "
                    "VALUES (?, ?, ?, 1.0, ?, ?, 1, 'external') "
                    "ON CONFLICT(source, target, edge_type) DO UPDATE SET "
                    "properties=excluded.properties, source_ref=excluded.source_ref",
                    (src, dst, etype, props, ref),
                )
            conn.executemany(
                "DELETE FROM graph_edges WHERE id = ?",
                [(rid,) for rid in delete_ids],
            )
    return {"counts": counts, "samples": samples}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:  # noqa: C901  # pass-per-flag CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--download", action="store_true", help="bulk download + load the RPT table")
    ap.add_argument(
        "--ratings-download",
        action="store_true",
        help="bulk download + load credit_ratings",
    )
    ap.add_argument("--refetch", action="store_true", help="ignore the zstd cache")
    ap.add_argument(
        "--no-load",
        action="store_true",
        help="download caches but skip loading sources.duckdb (refresh-chain phase A: keeps the parallel fetch phase free of a long RW hold)",
    )
    ap.add_argument("--apply", action="store_true", help="write edges (default: dry-run)")
    ap.add_argument(
        "--group",
        action="store_true",
        help="group-structure pass (subsidiary_of/same_group/jv_with)",
    )
    ap.add_argument(
        "--supply-chain",
        action="store_true",
        help="supplier_to pass from RPT transaction types",
    )
    ap.add_argument("--ratings", action="store_true", help="rated_by pass from credit_ratings")
    ap.add_argument(
        "--cure-subsidiary",
        action="store_true",
        help="R6a/C2 backfill: reclassify existing vigil:* subsidiary_of edges under "
        "the guarded ladder (dry-run; --apply writes; then graph-rebuild chain)",
    )
    ap.add_argument("--max-new-entities", type=int, default=60000)
    args = ap.parse_args(argv)

    if args.cure_subsidiary:
        from helpers.core.db import connect as sqlite_connect, DEFAULT_DB_PATH  # noqa: PLC0415

        conn = sqlite_connect(DEFAULT_DB_PATH)
        try:
            plan = cure_subsidiary_backfill(conn, apply=args.apply)
        finally:
            conn.close()
        for key, val in plan["counts"].items():
            print(f"  {key:20s} {val:>7,}")
        for bucket, bucket_rows in sorted(plan["samples"].items()):
            for s, t, rel in bucket_rows[:3]:
                print(f"  [{bucket}] {s} → {t}  ({rel!r})")
        print("cure: APPLIED" if args.apply else "cure: dry-run — no writes (--apply to write)")
        return 0
    # downloads are network-bound and this lane runs in parallel with
    # other sources.duckdb writers (make refresh-chain phase A) — fetch
    # the caches BEFORE taking the single-writer lock (§0 lock-holder).
    cache = download_bulk(refetch=args.refetch) if args.download else None
    rc = download_ratings(refetch=args.refetch) if args.ratings_download else None
    if args.no_load:
        # refresh-chain phase A: the caches land on disk (zstd, content-
        # addressed); the sequential apply phase re-runs --download as a
        # cache READ and does the loads + passes with no parallel siblings.
        if cache is not None:
            print(f"rpt_transactions cache ready: {cache}")
        if rc is not None:
            print(f"credit_ratings cache ready: {rc}")
        return 0
    src = connect_with_lock_retry(lambda: duckdb.connect(str(SOURCES_DB), **memory_cap_config()))
    if cache is not None:
        n = load_table(src, cache)
        print(f"loaded rpt_transactions: {n} rows")
    if rc is not None:
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

    # Bound before the branches: n_unres is only assigned inside the
    # args.group arm (apply_pairs returns it), but the unresolved-filers
    # worklist tail below is UNCONDITIONAL. Running a single pass —
    # `--supply-chain` or `--ratings` without `--group`, which is the
    # DEFAULT dry-run mode — left the name unbound and raised
    # UnboundLocalError: cannot access local variable 'n_unres' at the tail.
    # Pre-existing (present across c71c27fd0/8711c96d8/ef487cb4a), not
    # introduced by this arc. With --apply it was worse than a traceback:
    # edges were already written, and the crash at the tail skipped the
    # `if args.apply: duckdb_rebuild()` below, leaving the DuckDB cache
    # silently stale. Zero is the honest default — the group pass reports a
    # real count, the other passes have no unresolved-filer notion.
    n_unres = 0

    if args.group or args.supply_chain:
        # S2 replay hygiene (rpt_attribution §4 S4): the vigil:rpt lane is
        # fully derived from rpt_transactions — purge the previous run's
        # edges before re-applying so re-keyed pairs cannot survive as
        # stale edges (the write path is upsert-only).
        ptypes = ["subsidiary_of", "same_group", "jv_with"] if args.group else []
        if args.supply_chain:
            ptypes.append("supplier_to")
        pcounts = purge_rpt_edges(conn, ptypes, dry_run=not args.apply)
        print(f"[{mode}:purge] " + " ".join(f"{t}={pcounts[t]}" for t in ptypes))
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
        n_ent, n_edges, n_written, n_unres, n_cycles, n_nc, n_su, n_vd = apply_pairs(
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
            f"unresolved_filers={n_unres} cycles_dropped={n_cycles} "
            f"same_group_noncompany_dropped={n_nc} "
            f"subject_unresolvable={n_su} vote_dropped={n_vd}"
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
