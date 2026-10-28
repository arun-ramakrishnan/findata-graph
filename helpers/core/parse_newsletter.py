#!/usr/bin/env python3
"""
End-to-end newsletter parser orchestrator.

Runs the mechanical stages of doc/procedures/markdown_parse.md for a single
newsletter, so the only step a human/agent still does interactively is Stage 4
(enriching notes with hand-curated insights). Everything else is one command:

    python3 helpers/core/parse_newsletter.py <newsletter.md> [--apply]

WHAT IT DOES
------------
Stage 0  Capture expiring OCR-crop images  (delegates to capture_newsletter_images.py --apply)
Stage 1  Extract candidate companies       (regex over the rewritten source)
Stage 2  Classify new vs existing          (SQLite lookup by name/normalized_name)
Stage 3  Create NEW entities               (SQLite row + markdown stub + bidirectional
         + tickers (best-effort)             relations; ticker resolved via Yahoo Finance)
Stage 4  -- EMITTED, not executed --        writes <slug>_enhancement_worklist.json
         enhancement worklist               (existing entities + new stubs, with the
                                             source line-range of each company's section,
                                             for an agent to lift insights from)
Stage 5  Validate                          (sync_tags + verify_notes + database_integrity_check)
Stage 6  Recompute graph analytics          (opt-in: --with-analytics only)
         (delegates to helpers/graph/algorithms.py --all --apply)

SAFETY
------
- DRY-RUN by default. Prints the plan; writes nothing until --apply is passed.
- Idempotent: skips images already captured, skips entities that already exist,
  never overwrites an existing note file.
- Never touches YAML of existing notes. Never deletes anything.
- Sector dirs must already exist under findata/Companies/ (no ad-hoc dirs created).
- Stage 6 (analytics) is OFF by default; it costs ~2-5s on a 950-entity graph.
  Use --with-analytics only when you intend to consume graph_analytics next.

USAGE
-----
    # Plan only (no writes):
    python3 helpers/core/parse_newsletter.py findata/The_Chatter/Foo_Bar.md

    # Execute the plan:
    python3 helpers/core/parse_newsletter.py findata/The_Chatter/Foo_Bar.md --apply

    # Execute + refresh graph analytics at the end (Stage 6):
    python3 helpers/core/parse_newsletter.py findata/The_Chatter/Foo_Bar.md --apply --with-analytics

    # Then hand the emitted <slug>_enhancement_worklist.json to an agent for Stage 4.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

# --- project paths ---------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../pdf-ocr-obsidian
# Ensure the repo root is importable when this script is run as a subprocess
# (make parse, maint orchestrator) — the late `from helpers.core.db import
# connect` inside main() needs `helpers` on sys.path.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from helpers.core.countries import classify_ticker  # noqa: E402  (country layer C2 seeding)
from helpers.core.db import utc_now  # noqa: E402  (Bundle T1: UTC last_updated)
from helpers.core.frontmatter import bump_generated  # noqa: E402  (OKF §5.2 stamp)
from helpers.graph.sector_membership import put_sector_membership  # noqa: E402  (canonical membership)

# Shared ticker resolution and entity matching (unified with get_tickers.py)
from helpers.core.get_tickers import search_ticker
from helpers.core.fuzzy_match import fuzzy_match

DB_PATH = PROJECT_ROOT / "memory" / "research.db"
FINDATA = PROJECT_ROOT / "findata"
COMPANIES = FINDATA / "Companies"
CAPTURE_SCRIPT = PROJECT_ROOT / "helpers" / "pdf" / "capture_newsletter_images.py"
SYNC_TAGS = PROJECT_ROOT / "helpers" / "core" / "sync_tags.py"
SYNC_SECTOR_WIKILINKS = PROJECT_ROOT / "helpers" / "maintenance" / "sync_sector_wikilinks.py"
VERIFY_NOTES = PROJECT_ROOT / "helpers" / "validators" / "verify_notes.py"
INTEGRITY = PROJECT_ROOT / "helpers" / "misc" / "database_integrity_check.py"
ALGORITHMS = PROJECT_ROOT / "helpers" / "graph" / "algorithms.py"

# --- entity extraction (mirrors markdown_parse.md patterns) ----------------
# Headings like:  ## Bharat Forge | Large Cap | Auto & Defence
# or              # Oil and Natural Gas Corporation Limited Large Cap Oil & Gas
SECTION_RE = re.compile(
    r"^\s*#{1,3}\s+(.+?)(?:\s*[|·].*)*$",  # company heading, drop the "| cap | sector" tail
    re.MULTILINE,
)
# Trailing legal suffixes to strip when forming the canonical short name.
SUFFIX_RE = re.compile(r"\s+(Limited|Ltd\.?|Private|Pvt\.?)$", re.IGNORECASE)
# Second legal-suffix pass (handles OCR variants like "Ltd." that survive SUFFIX_RE).
_SUFFIX2_RE = re.compile(r"\s+(Limited|Ltd\.?|Private|Pvt\.?)$", re.IGNORECASE)
# Collapse runs of whitespace to a single space (used in heading name cleanup).
_WS_RE = re.compile(r"\s+")

# S3 section-head noise gate
_NOISE_NAME_RE = re.compile(
    r"\b(regulator|regulatory|authority|commission)\b|\bboard\s+of\b",
    re.IGNORECASE,
)


def _is_noise_entity(name: str) -> bool:
    """Return True when a candidate heading names an institution, not a company."""
    if not name:
        return False
    return bool(_NOISE_NAME_RE.search(name))


# Heading regex — reused across calls.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

# --- company-heading constants (hoisted from extract_companies for reuse) -----
_CAP_TOKENS = (
    "large cap",
    "mid cap",
    "small cap",
    "micro cap",
    "nano cap",
    "mega cap",
    "unlisted",
)
# Cut the name at the first cap token (incl. OCR variants like 'Larg Cap', 'Mid Cap.').
_CAP_CUT_RE = re.compile(r"\s+(?:large|larg|mid|small|micro|nano|mega)\s*cap", re.IGNORECASE)
_SECTOR_WORDS = frozenset(
    {
        "retail",
        "energy",
        "renewables",
        "metals",
        "metal",
        "healthcare",
        "pharma",
        "pharmaceuticals",
        "hospitals",
        "diagnostics",
        "technology",
        "semiconductors",
        "financial services",
        "nbfc",
        "housing finance",
        "capital markets",
        "fintech",
        "insurance",
        "consumer durables",
        "automotive",
        "automotives",
        "telecom",
        "telecommunications",
        "real estate",
        "realty",
        "defence",
        "defense",
        "chemicals",
        "aerospace & defence",
        "engineering & capital goods",
        "railways",
        "ems manufacturing",
        "consumer",
        "software",
        "hotels",
        "hotel",
        "tourism",
        "logistics",
        "media",
        "entertainment",
        "textiles",
        "building materials",
        "packaging",
        "agriculture",
        "education",
        "edtech",
        "electronics",
        "mining",
        "aviation",
        "infrastructure",
        "fertilizer",
    }
)


def log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}")


# ===========================================================================
# DB helpers (direct sqlite3; the SQLite MCP is for the agent, not this script)
# ===========================================================================
def get_existing_entity_names(conn) -> set:
    """Names + normalized_names of companies that HAVE a note file.

    Note-gap arc (2026-10-28): this returned every company row, so a
    noteless stub row (file_path IS NULL -- e.g. bulk-imported)
    exact-hit in classify() and counted as covered; the parse then never
    wrote its note. classify()'s contract is "already has coverage", so
    only noted rows qualify now; noteless rows fall through to the
    alias/fuzzy path instead.
    """
    rows = conn.execute(
        "SELECT name FROM entities WHERE entity_type='company' AND file_path IS NOT NULL"
    ).fetchall()
    norm = conn.execute(
        "SELECT normalized_name FROM entities WHERE entity_type='company' "
        "AND normalized_name IS NOT NULL AND file_path IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows} | {r[0].replace("_", " ") for r in norm}


def get_alias_map(conn) -> dict:
    """alias identifier value -> canonical entity name (lowercased key).

    Feeds classify(): a newsletter name that exact-hits a registered alias
    resolves straight to the canonical entity, so downstream quotes and
    edges attach to the noted row instead of dying on a folded variant
    name (note-gap arc 2026-10-28: 26 variants folded, aliases registered
    under namespace 'newsletter_variant' -- but any alias counts).
    """
    return {
        r[0].lower(): r[1]
        for r in conn.execute(
            "SELECT identifier_value, entity_name FROM entity_identifiers "
            "WHERE identifier_type='alias'"
        ).fetchall()
    }


def get_sector_dirs() -> set:
    """Existing sector directory names under findata/Companies/."""
    return {p.name for p in COMPANIES.iterdir() if p.is_dir()}


def get_sector_entities(conn) -> set:
    rows = conn.execute("SELECT name FROM entities WHERE entity_type='sector'").fetchall()
    return {r[0] for r in rows}


# ===========================================================================
# Stage 0: image capture
# ===========================================================================
def capture_images(md_path: Path, apply: bool) -> bool:
    """Returns True if images were captured/already-present, False on failure."""
    log("0", f"capturing images -> {CAPTURE_SCRIPT.name} --apply")
    if not apply:
        log("0", "DRY-RUN: would run capture with --apply")
        return True
    cmd = [sys.executable, str(CAPTURE_SCRIPT), str(md_path), "--apply"]
    try:
        subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
        return True
    except subprocess.CalledProcessError as e:
        log("0", f"capture FAILED (exit {e.returncode})")
        return False


# ===========================================================================
# Stage 1: extract candidate companies from the rewritten source
# ===========================================================================
def _header_sector_of(raw: str, sector_dirs: set | None) -> str | None:
    """Declared sector from a pipe-separated heading ("Retail" in
    "Foo | Large Cap | Retail") — canonical names only."""
    if "|" not in raw or not sector_dirs:
        return None
    parts = raw.split("|")
    if len(parts) < 3:
        return None
    sector_part = parts[-1].strip().title()
    return sector_part if sector_part in sector_dirs else None


def extract_companies(content: str, sector_dirs: set | None = None):
    """
    Yield (candidate_name, line_number, header_sector) for each company-looking section heading.
    header_sector is the declared sector from pipe-separated headers (e.g., "Retail"), or None.
    Handles both newsletter heading formats:
      ## Foo Limited | Large Cap | Sector     (pipe-separated)
      ## Foo Limited Mid Cap Sector           (no pipes; cap token inline)
    """
    _nl_offsets = [i for i, c in enumerate(content) if c == "\n"]

    def _line_of(pos: int) -> int:
        return bisect.bisect_right(_nl_offsets, pos) + 1

    for m in _HEADING_RE.finditer(content):
        line_no = _line_of(m.start())
        raw = m.group(2).strip()
        lower = raw.lower()
        # Skip pure sector/group headers (no cap token, no '|').
        has_cap = any(tok in lower for tok in _CAP_TOKENS)
        has_pipe = "|" in raw
        if not (has_cap or has_pipe):
            continue
        header_sector = _header_sector_of(raw, sector_dirs)
        # Isolate the name: text before the first '|' OR before the cap token.
        name = raw.split("|")[0].strip()
        cut = _CAP_CUT_RE.search(name)
        if cut:
            name = name[: cut.start()].strip()
        name = _WS_RE.sub(" ", name).strip()
        # Drop pure sector words left after cutting.
        if not name or name.lower() in _SECTOR_WORDS:
            continue
        # Strip trailing legal suffix for the canonical short name.
        canonical = SUFFIX_RE.sub("", name).strip()
        canonical = _SUFFIX2_RE.sub("", canonical).strip()
        if not canonical or len(canonical) < 3:
            continue
        # S3 section-head noise gate: regulator / institutional section headings
        # are section heads (repeated across companies), not company entities.
        # Skip creation and let downstream quote capture route them to the
        # Quotes catch-all.
        if _is_noise_entity(canonical):
            continue
        yield canonical, line_no, header_sector


# ===========================================================================
# Stage 2: classify new vs existing (with fuzzy disambiguation)
# ===========================================================================
_STOPWORDS = {
    "the",
    "of",
    "and",
    "ltd",
    "limited",
    "private",
    "pvt",
    "india",
    "industries",
    "company",
    "corporation",
    "enterprise",
    "group",
    "holdings",
}
# Generic finance/sector words that are NOT distinctive on their own. A fuzzy
# match must share at least one token NOT in this set, otherwise two unrelated


def classify(
    companies,
    existing_names: set,
    alias_map: dict | None = None,
    *,
    fuzzy_candidates: list | None = None,
):
    """
    Split into (new, existing, uncertain).
    - existing: exact name/normalized match, or an exact alias hit resolved
      to its canonical entity (note-gap arc 2026-10-28).
    - uncertain: no exact match but a strong fuzzy match exists -> do NOT
      auto-create (would risk a duplicate); flag for confirmation.
    - new: no match at all.
    companies is a list of (name, line, header_sector) tuples.
    alias_map maps lowercased alias -> canonical entity name (may be None).
    fuzzy_candidates: entities to use for fuzzy matching (defaults to existing_names).
                      Use this to include stubs (file_path IS NULL) in fuzzy path.
    Returns (new, existing, uncertain) where each element preserves header_sector.
    """
    existing_lower = {n.lower() for n in existing_names}
    existing_list = list(existing_names)
    alias_map = alias_map or {}
    fuzzy_list = fuzzy_candidates if fuzzy_candidates is not None else existing_list
    new_co, existing_co, uncertain_co = [], [], []
    seen = set()
    for company in companies:
        name = company[0]
        line = company[1]
        header_sector = company[2] if len(company) > 2 else None
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        if name in existing_names or key in existing_lower:
            existing_co.append((name, line, header_sector))
            continue
        canonical = alias_map.get(key)
        if canonical:
            existing_co.append((canonical, line, header_sector))
            continue
        # Use shared fuzzy matcher (exact -> abbreviation -> word_overlap -> spellfix)
        fuzzy, method, score = fuzzy_match(name, fuzzy_list)
        if fuzzy:
            uncertain_co.append(
                {
                    "candidate": name,
                    "likely_existing": fuzzy,
                    "section_line": line,
                    "header_sector": header_sector,
                }
            )
        else:
            new_co.append((name, line, header_sector))
    return new_co, existing_co, uncertain_co


# ===========================================================================
# Stage 3: create NEW entities (ticker, SQLite, stub note, relations)
# ===========================================================================
def guess_sector_for(name: str, content_window: str, sector_dirs: set) -> str | None:
    """Best-effort sector from the heading's own tail or nearby text."""
    w = content_window.lower()
    # Order matters: more specific sectors (carve-outs) MUST come before their
    # parent catch-all so e.g. 'solar IPP' hits Renewables before Energy, and
    # 'diagnostics lab' hits Diagnostics before Healthcare.
    # Canonical sector list: 42 sectors (see findata/Sectors/).
    # Carve-outs added 2026-07: Renewables, Pharma, Hospitals, Diagnostics,
    # Semiconductors, NBFC, Housing_Finance, Capital_Markets, Fintech_Payments,
    # Railways, EMS_Manufacturing, Education_Training.
    rules = [
        # ---- Engineering carve-outs ----
        # Before Financial_Services: its "capital" token would otherwise shadow
        # "capital goods" here (first-match-wins ordering) and misfile every
        # "Engineering & Capital Goods" heading as a financial.
        ("Railways", ("railway", "rail", "wagon", "locomotive", "rvnl", "irfc")),
        (
            "Engineering_Capital_Goods",
            (
                "engineering",
                "capital goods",
                "electrical",
                "transformer",
                "switchgear",
                "pump",
            ),
        ),
        # ---- Financial services carve-outs (before Financial_Services) ----
        ("Banking", ("bank", "banking")),
        ("NBFC", ("nbfc",)),
        ("Housing_Finance", ("housing finance", "home loan")),
        (
            "Capital_Markets",
            (
                "stock exchange",
                "depository",
                "clearing corp",
                "amc",
                "asset management",
                "mutual fund",
            ),
        ),
        (
            "Fintech_Payments",
            (
                "fintech",
                "payments",
                "payment",
                "upi",
                "wallet",
                "insurtech",
                "policybazaar",
                "paytm",
                "razorpay",
            ),
        ),
        (
            "Insurance",
            ("insurance", "reinsurance", "life insurance", "general insurance"),
        ),
        ("Financial_Services", ("financial", "capital", "holdings")),
        # ---- Healthcare carve-outs (before Healthcare) ----
        (
            "Pharma",
            (
                "pharma",
                "drug",
                "api",
                "formulation",
                "vaccine",
                "crdap",
                "cram",
                "cro",
                "syngene",
                "medplus",
                "pharmacy",
            ),
        ),
        (
            "Hospitals",
            ("hospital", "healthcare provider", "medical center", "clinic chain"),
        ),
        (
            "Diagnostics",
            (
                "diagnostic",
                "pathlab",
                "path lab",
                "pathology",
                "imaging center",
                "ivd",
                "lab chain",
            ),
        ),
        ("Healthcare", ("healthcare", "medical", "wellness")),
        # ---- Energy carve-out ----
        (
            "Renewables",
            (
                "solar",
                "wind",
                "renewable",
                "biomass",
                "biofuel",
                "photovoltaic",
                "green energy",
            ),
        ),
        (
            "Energy",
            (
                "oil",
                "gas",
                "power",
                "energy",
                "refiner",
                "petroleum",
                "lng",
                "ongc",
                "ioc",
                "bpcl",
                "hpcl",
                "ntpc",
                "powergrid",
            ),
        ),
        # ---- Technology carve-out (Semiconductors + EMS before Technology) ----
        # NOTE: 'Technology' rule uses 'technology' (full word) not 'tech' — otherwise
        # 'Dixon Technologies' (an EMS co) would match Technology before EMS_Manufacturing.
        (
            "Semiconductors",
            (
                "semiconductor",
                "chip",
                "foundry",
                "wafer",
                "nvidia",
                "amd",
                "intel",
                "broadcom",
                "micron",
            ),
        ),
        (
            "EMS_Manufacturing",
            ("ems ", " contract manufacturing", "electronics manufacturing"),
        ),
        (
            "Technology",
            ("software", "it services", "technology", "saas", "erp", "cloud"),
        ),
        # ---- Other verticals ----
        ("Metals", ("steel", "iron", "metal", "aluminium", "zinc", "ferro")),
        (
            "Automotive",
            (
                "auto",
                "vehicle",
                "tractor",
                "commercial vehicle",
                "two-wheeler",
                "passenger vehicle",
                "ev",
            ),
        ),
        (
            "FMCG",
            (
                "fmcg",
                "consumer goods",
                "beverage",
                "food",
                "personal care",
                "home care",
            ),
        ),
        ("Consumer", ("retail", "consumer", "apparel", "footwear", "jewellery")),
        ("Chemicals", ("chemical", "specialty", "paint", "petrochemical")),
        ("Real_Estate", ("realty", "real estate", "reit", "proptech")),
        ("Telecommunications", ("telecom", "communication", "5g")),
        ("Travel", ("hotel", "travel", "tourism", "resort", "ota", "airline")),
        ("Logistics", ("logistics", "transport", "shipping", "warehouse", "courier")),
        ("Defense", ("defence", "defense", "aerospace", "military")),
        ("Textiles", ("textile", "apparel manufacturing", "garment", "yarn", "fabric")),
        (
            "Building_Materials",
            ("cement", "sanitaryware", "building material", "tile", "pipe", "plywood"),
        ),
        ("Packaging", ("packaging", "flexible packaging", "corrugated")),
        (
            "Agriculture",
            ("agriculture", "agri", "irrigation", "seed", "fertilizer", "agrochemical"),
        ),
        (
            "Education_Training",
            ("edtech", "education", "e-learning", "coaching", "niit", "byju"),
        ),
        ("Electronics", ("electronics", "consumer electronics")),
        (
            "Media_Entertainment",
            ("media", "entertainment", "broadcasting", "gaming", "esports", "content"),
        ),
        ("Mining", ("mining", "miner")),
        ("Aviation", ("aviation", "airline", "airport")),
        (
            "Infrastructure",
            ("infrastructure", "epc", "construction", "road", "highway"),
        ),
    ]
    for sector, kws in rules:
        if any(k in w for k in kws):
            if sector in sector_dirs:
                return sector
    return "Diversified" if "Diversified" in sector_dirs else None


def normalize_name(name: str) -> str:
    """PascalCase with single underscores, no special chars (per sync rules).

    Contract:
      - Output matches ^[A-Za-z0-9][A-Za-z0-9_]*$  (or empty for all-symbol input).
        Leading digit allowed for brand names that legitimately start with a
        number (e.g. "360 ONE WAM" -> "360_ONE_WAM").
      - Idempotent: normalize_name(normalize_name(x)) == normalize_name(x).
      - Underscore is the only separator; runs collapse to one.
    """
    # Replace separators (&, parens, hyphens) with spaces, then drop anything
    # that isn't alphanumeric, underscore, or space.
    n = re.sub(r"[&\(\)\-]", " ", name)
    n = re.sub(r"[^A-Za-z0-9 _]", "", n)
    # Split on whitespace, join with single underscore.
    parts = [p for p in n.split() if p]
    result = "_".join(parts)
    # Collapse any double-underscores (from e.g. "A &_B" -> "A _ B" -> "A__B").
    result = re.sub(r"__+", "_", result).strip("_")
    return result


def render_stub(name, normalized_name, sector, ticker, permalink):
    # Ticker-derived geography (country layer C2): the tag and key come
    # from the home-market classification, never a hardcoded default —
    # an unlisted company carries NO geography until a ticker lands.
    country, _via = classify_ticker(ticker)
    tags = [
        "entity_type/company",
        f"sector/{sector.lower()}",
    ]
    geo_line = ""
    if country:
        tags.append(f"geography/{country}")
        geo_line = f"geography: {country}\n"
    tag_block = "\n".join(f"- {t}" for t in tags)
    today = date.today().isoformat()
    # Title is UNQUOTED (canonical style; verify_notes warns on quotes for both
    # sectors and companies). ticker:null marks an unlisted company, which is a
    # meaningful category, so it is made explicit with `listed: false` (mirrors
    # the 106 existing unlisted notes). market_cap:null is the schema-legal
    # "unknown" — the enrichment flow upgrades it once a cap is known (the
    # frontmatter schema REQUIRES the key, so omitting it fatals static-checks).
    ticker_line = "null" if not ticker else repr(ticker)
    listed_line = "\nlisted: false" if not ticker else ""
    note = f"""---
title: {name}
type: company
ticker: {ticker_line}{listed_line}
{geo_line}tags:
{tag_block}
normalized_name: {normalized_name}
sector: {sector}
market_cap: null
permalink: {permalink}
created: '{today}'
last_modified: '{today}'
---

# {name}

## Company Overview
{name} — auto-generated stub from newsletter parsing. To be enriched.
"""
    # OKF §5.2: stamp provenance where it is generated — a fresh stub is
    # machine content, so it is born machine-confirmed, not census-unverified.
    return bump_generated(note, "parse_newsletter.py/v1")


def create_entity(conn, name, sector, ticker, apply, sector_entities=None):
    """Create an entity row + stub note (idempotent).

    ``sector_entities`` is the set of sector-entity names, hoisted out of the
    caller's loop to avoid an N+1 query (one SELECT per entity). If None,
    falls back to querying per call (slow, kept for backward compatibility).
    """
    normalized = normalize_name(name)
    file_path = f"findata/Companies/{sector}/{normalized}.md"
    abs_path = PROJECT_ROOT / file_path
    permalink = f"/companies/{sector.lower()}/{normalized.lower()}"
    if apply:
        # Insert row idempotently. INSERT OR IGNORE makes the existence
        # check atomic with the write (the previous SELECT-then-INSERT was
        # racy under concurrency and inconsistent with the graph_edges
        # writes just below, which already use INSERT OR IGNORE).
        cur = conn.execute(
            "INSERT OR IGNORE INTO entities(name, entity_type, sector_classification, "
            "normalized_name, file_path, ticker, last_updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                name,
                "company",
                sector,
                normalized,
                file_path,
                ticker,
                utc_now(),  # Bundle T1: UTC timestamp (was date.today().isoformat())
            ),
        )
        inserted = cur.rowcount > 0
        if inserted:
            # Sector membership if the sector entity exists. Since the
            # part_of/has_company retirement (2026-10-19) the incidence
            # store is canonical — one write path, and the pair that
            # graph_edges used to carry is projected back out of it.
            se = sector_entities if sector_entities is not None else get_sector_entities(conn)
            if sector in se:
                put_sector_membership(conn, name, sector, source_ref="parse_newsletter")
        # Write stub note only if absent (never clobber).
        if not abs_path.exists():
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(
                render_stub(name, normalized, sector, ticker, permalink),
                encoding="utf-8",
            )
    return normalized, file_path


# ===========================================================================
# Stage 4: emit enhancement worklist (NOT executed — for an agent)
# ===========================================================================
def _mention_re(name: str) -> re.Pattern:
    """Word-boundary pattern matching a company name in prose.

    Apostrophes are optional ("McDonald's" matches "McDonalds"), escaped
    space runs collapse to \\s+, and the \\b guards keep short names
    ("Target") out of unrelated words ("targeting") — the substring
    version of this check false-positived on exactly that.
    """
    parts = re.split(r"['’]", name)
    body = r"['’]?".join(re.escape(part) for part in parts)
    body = re.sub(r"(\\\ )+", r"\\s+", body)
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


def _build_mention_index(company_names: set) -> list[tuple[re.Pattern, str]]:
    """Sorted longest-first mention patterns (longest wins a line scan)."""
    return sorted(
        ((_mention_re(n), n) for n in company_names if n and n.strip()),
        key=lambda t: -len(t[1]),
    )


def _mentioned_in(text: str, index: list[tuple[re.Pattern, str]]) -> set:
    """Distinct company names word-boundary-mentioned in text."""
    return {name for rx, name in index if rx.search(text)}


_ANALYST_MARKER_RE = re.compile(
    r"\banalysts?\b|\bresearch (?:analyst|lead|head)\b|\bhead of research\b"
    r"|\bbrokerage\b|\bsell[- ]side\b|\bbuy[- ]side\b|\binvestment bank\b"
    r"|\bbroker note\b",
    re.IGNORECASE,
)

_LISTING_PROSE_RE = re.compile(
    r"\bnow[- ]listed\b|\brecently listed\b|\bgets? listed\b|\bgets? listed\b"
    r"|\blisting (?:on|at)\b|\b(?:made|makes) its? debut\b|\brecent ipo\b"
    r"|\bipo\b|\blisted on\b",
    re.IGNORECASE,
)


def _is_company_heading(heading: str) -> bool:
    """True when a heading parses as a company section (pipe-separated or
    inline cap token) — those are company notes even when an analyst is
    quoted; only free-form section heads can route to the sector note."""
    if "|" in heading:
        return True
    return bool(_CAP_CUT_RE.search(heading))


def _detect_sector_commentary(content: str, company_names: set) -> list[tuple[str, int, list[str]]]:
    """Detect analyst-voiced sector commentary sections (S6).

    A section qualifies when ALL of these hold:
    - it is a free-form section head (not a company-note heading),
    - its body attributes the view to a non-management speaker
      (analyst / research lead / brokerage / sell-side), and
    - it mentions >= 3 distinct known companies.

    Returns list of (raw_heading, line_number, [company_names]) — the
    enhancer routes these to the sector note instead of the company
    note. Audit note: the previous version could never emit (its scan
    was gated on a set nothing populated) and counted 'co'-substring
    word hits as companies; this one resolves names against the
    entity universe with word boundaries.
    """
    index = _build_mention_index(company_names)
    results: list[tuple[str, int, list[str]]] = []
    lines = content.split("\n")

    heading: str | None = None
    heading_line = 0
    section_companies: set = set()
    analyst_voiced = False

    def flush():
        if (
            heading is not None
            and not _is_company_heading(heading)
            and analyst_voiced
            and len(section_companies) >= 3
        ):
            results.append((heading, heading_line, sorted(section_companies)))

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("## "):
            flush()
            heading = stripped[3:].strip()
            heading_line = i
            section_companies = set()
            analyst_voiced = False
        elif heading is not None:
            if _ANALYST_MARKER_RE.search(line):
                analyst_voiced = True
            section_companies |= _mentioned_in(line, index)
    flush()

    return results


def _detect_listed_contradictions(content: str, company_names: set) -> list[dict]:
    """Flag listing/IPO prose that contradicts a stub's listed: false (S8).

    A line (or its immediate neighbour) that carries listing prose AND
    mentions a known company produces one contradiction row per company —
    word-boundary matched, so "Targeting growth" never flags Target.
    """
    index = _build_mention_index(company_names)
    contradictions: list[dict] = []
    lines = content.split("\n")
    seen: set[tuple[str, int]] = set()
    for i, line in enumerate(lines, 1):
        if not _LISTING_PROSE_RE.search(line):
            continue
        for scope in (line, lines[i - 2] if i > 1 else ""):
            for name in _mentioned_in(scope, index):
                key = (name, i)
                if key in seen:
                    continue
                seen.add(key)
                contradictions.append(
                    {
                        "company": name,
                        "line": i,
                        "prose": line.strip(),
                        "reason": (
                            "Section mentions a recent listing/IPO — verify "
                            "and update the stub's listed: false if real"
                        ),
                    }
                )
    return contradictions


def _missing_ticker_items(
    new_cos: list, existing_cos: list, stubs: set, tickers: dict
) -> list[dict]:
    """Entities with no ticker on file surface as explicit manual-check
    items (S8) instead of silent nulls in the worklist."""
    seen: set[str] = set()
    items: list[dict] = []
    for name, _line, _sector in list(new_cos) + list(existing_cos):
        if name in seen or tickers.get(name):
            continue
        seen.add(name)
        items.append(
            {
                "name": name,
                "is_stub": name in stubs,
                "reason": "no ticker on file — manual lookup before listing claims",
            }
        )
    return items


def _detect_listed_issues(content: str, company_names: set) -> tuple[list[dict], list[dict]]:
    """Deprecated shim (2026-10-15 audit): split into
    `_detect_listed_contradictions` (word-boundary listing prose) and
    `_missing_ticker_items` (entities with no ticker on file). Kept for
    any external caller; the pipeline uses the split pair.
    """
    contradictions = _detect_listed_contradictions(content, company_names)
    return contradictions, []


# Stage 4: emit enhancement worklist (NOT executed — for an agent)
def emit_worklist(
    md_path,
    edition_title,
    new_cos,
    existing_cos,
    uncertain_cos,
    out_dir,
    stubs: set | None = None,
    tickers: dict | None = None,
    sector_commentary: list | None = None,
    listed_contradictions: list | None = None,
    listed_missing: list | None = None,
):
    """D17: ``stubs`` (exchange-seeded entities with no note) and ``tickers``
    annotate the worklist so an enhancer knows which "existing" entities are
    bare stubs needing their FIRST note (an upgrade: sector_classification,
    file_path, authored fields) versus authored notes needing a chatter
    block. A stub never births a second entity — the note lands on it."""
    stubs = stubs or set()
    tickers = tickers or {}
    sector_commentary = sector_commentary or []
    listed_contradictions = listed_contradictions or []
    listed_missing = listed_missing or []
    slug = md_path.stem
    wl = {
        "newsletter": str(md_path.relative_to(PROJECT_ROOT)),
        "edition_title": edition_title,
        "new_entities": [{"name": n, "section_line": ln} for n, ln, _ in new_cos],
        "existing_entities_to_enhance": [
            {
                "name": n,
                "section_line": ln,
                "is_stub": n in stubs,
                "known_ticker": tickers.get(n),
            }
            for n, ln, _ in existing_cos
        ],
        "sector_commentary": [
            {
                "heading": raw,
                "line": line,
                "companies": companies,
                "reason": "3+ companies mentioned; speaker is not company management",
            }
            for raw, line, companies in sector_commentary
        ],
        "listed_contradictions": listed_contradictions,
        "listed_missing": listed_missing,
        "uncertain_entities": [
            {**u, "likely_is_stub": u.get("likely_existing") in stubs} for u in uncertain_cos
        ],
        "instructions": (
            "For each entity, read its concall section (starting at section_line in "
            "the newsletter), extract 3-5 bullet insights + 1 verbatim quote, and "
            "append/replace a '## The Chatter — <edition_title>' block. See "
            "doc/procedures/markdown_parse.md#enhancing-existing-entities. D17: entries "
            "with is_stub=true are exchange-seeded stubs (no note yet) — author the "
            "note ON the stub entity (it keeps its name/ticker; gains file_path, "
            "sector_classification and authored fields); NEVER create a second entity. "
            "After authoring a stub note, land its sector membership (the incidence "
            "store is canonical; no other step on this path writes it): "
            "python3 -m helpers.graph.sector_membership put '<stub entity name>' "
            "<Sector_From_The_Note>. sync_tags covers the same write at the next "
            "tag sync — the CLI exists because the two run independently."
        ),
    }
    out = out_dir / f"{slug}_enhancement_worklist.json"
    out.write_text(json.dumps(wl, indent=2), encoding="utf-8")
    return out


# ===========================================================================
# Stage 5: validate
# ===========================================================================
def run_validation(apply):
    # SYNC_SECTOR_WIKILINKS runs FIRST: an apply that created entities must
    # refresh the 42 sector-note rosters in the same run, or every roster
    # goes stale with all downstream validators still green (the user catch
    # of 2026-08-25 — Logistics/Metals needed a manual re-run after the
    # Allcargo Global/HEG creations). Region-scoped write: only the
    # sentinel-wrapped "All Companies (auto)" block changes.
    for label, script in (
        ("sync_sector_wikilinks", SYNC_SECTOR_WIKILINKS),
        ("sync_tags", SYNC_TAGS),
        ("verify_notes", VERIFY_NOTES),
        ("database_integrity_check", INTEGRITY),
    ):
        log("5", f"{label}")
        if not apply:
            log("5", "DRY-RUN: would run " + script.name)
            continue
        # sys.executable, not a PATH-resolved "python3": the parse must run
        # its validators under the same interpreter (venv or not) that is
        # running the orchestrator itself.
        r = subprocess.run([sys.executable, str(script)], cwd=str(PROJECT_ROOT))  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
        if r.returncode != 0:
            log("5", f"{label} FAILED (exit {r.returncode})")
            return False
    return True


# ===========================================================================
# Stage 6: graph analytics (opt-in)
# ===========================================================================
def run_graph_analytics():
    """Recompute and persist all graph metrics (degree, pagerank, betweenness,
    louvain, wcc, clustering) to graph_analytics.

    Delegates to `helpers/graph/algorithms.py --all --apply`. Failures are
    logged but do not invalidate the overall parse run (the entity DB and
    notes are already committed); the caller still sees a non-zero exit.
    """
    log("6", f"{ALGORITHMS.name} --all --apply")
    r = subprocess.run(  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
        [sys.executable, str(ALGORITHMS), "--all", "--apply"],
        cwd=str(PROJECT_ROOT),
    )
    if r.returncode != 0:
        log("6", f"analytics FAILED (exit {r.returncode}); entity/notes unaffected")
        return False
    return True


# ===========================================================================
# Stage 2b (--cross-check): semantic guard for the NEW/known extractor gap
# ===========================================================================
def cross_check_new(new_names, min_sim=0.80):
    """Flag NEW-classified names that look like EXISTING company notes.

    The known extractor gap: dry-run classification can miss an existing
    company (different spelling/alias) and flag it NEW, which --apply
    would stub as a duplicate entity. For each NEW name, embed the name
    (query prefix) and KNN it against the existing company notes in the
    warm graph's ``v_note_embeddings`` (query.notes_like_text). Read-only;
    surfaces close matches for manual confirmation before --apply.

    min_sim 0.80 (calibrated 2026-10-10 on the gemma-512d vector regime,
    first full pipeline run): true same-company name->note hits measure
    0.80-0.84 (TCS 0.799, PNB 0.835) while the unrelated band tops out
    at ~0.77, so the old 0.55 floor flagged every name regardless.

    Degrades to a single WARNING line when the local embedder or the
    graph connection is unavailable — never blocks the parse run.
    """
    try:
        from helpers.graph.query import connect as graph_connect
        from helpers.graph.query import notes_like_text
    except Exception as exc:
        log("2b", f"⚠ cross-check unavailable (import failed: {exc})")
        return

    try:
        gcon = graph_connect()
    except Exception as exc:
        log("2b", f"⚠ cross-check unavailable (graph connect failed: {exc})")
        return

    try:
        flagged = 0
        for name in new_names:
            hits = notes_like_text(gcon, name, k=3, min_sim=min_sim)
            if hits is None:
                log("2b", "⚠ embedder/vectors unavailable — skipping cross-check")
                return
            if not hits:
                continue
            flagged += 1
            for path, title, sim in hits:
                stem = path.rsplit("/", 1)[-1].removesuffix(".md")
                log("2b", f"  ⚠ NEW '{name}'  ~=  existing '{title}' ({stem}) sim={sim:.2f}")
        log("2b", f"{flagged} of {len(new_names)} NEW name(s) have close semantic matches")
    finally:
        gcon.close()


# ===========================================================================
# main
# ===========================================================================
def main(argv: list[str] | None = None):  # noqa: C901
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("newsletter", help="Path to the newsletter .md")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Execute writes (default: dry-run plan only)",
    )
    ap.add_argument(
        "--cross-check",
        action="store_true",
        help="After Stage 2, semantically match each NEW-flagged company "
        "name against existing embedded company notes (v_note_embeddings) "
        "and flag close hits — guards the mis-flag-NEW extractor gap "
        "before --apply. Read-only; needs the local embedder + a warm "
        "graph.duckdb, else warns and skips.",
    )
    ap.add_argument(
        "--with-analytics",
        action="store_true",
        help="After Stage 5, run helpers/graph/algorithms.py --all --apply to "
        "refresh graph_analytics (degree/pagerank/betweenness/louvain/wcc/"
        "clustering). Ignored without --apply. Adds ~2-5s on a 950-entity graph.",
    )
    args = ap.parse_args(argv)

    if args.with_analytics and not args.apply:
        sys.exit("--with-analytics requires --apply")

    md_path = (PROJECT_ROOT / args.newsletter).resolve()
    if not md_path.exists():
        sys.exit(f"newsletter not found: {md_path}")
    edition_title = md_path.stem.replace("_", " ")

    from helpers.core.db import connect

    conn = connect(DB_PATH)  # FK ON + WAL via canonical helper

    log("plan", f"newsletter = {md_path.relative_to(PROJECT_ROOT)}")
    log("plan", f"edition    = {edition_title}")
    log("plan", f"mode       = {'APPLY' if args.apply else 'DRY-RUN'}")
    print()

    # Stage 0
    if not capture_images(md_path, args.apply):
        sys.exit("Stage 0 failed; aborting before any DB writes.")
    print()

    content = md_path.read_text(encoding="utf-8")
    sector_dirs = get_sector_dirs()
    existing = get_existing_entity_names(conn)
    alias_map = get_alias_map(conn)
    companies = list(extract_companies(content, sector_dirs))

    # Stub census — UNCONDITIONAL read-only SELECT (2026-10-27 Zetwerk miss): an
    # apply-gated census emits worklists with stubs=∅ in dry-run/cross-check-only
    # invocations, so likely_is_stub lies False and Stage-5 authors skip
    # file_path=NULL stubs instead of authoring the note on them (D17).
    stubs, tickers = set(), {}
    for nm, tk in conn.execute(
        "SELECT name, ticker FROM entities WHERE entity_type='company' AND file_path IS NULL"
    ):
        stubs.add(nm)
        if tk:
            tickers[nm] = tk

    # For fuzzy matching, include stub entities so they can be matched.
    # Note: exact-match against existing (noted companies only) stays the same;
    # stubs only participate in fuzzy path.
    fuzzy_candidates = list(existing) + list(stubs)

    new_cos, existing_cos, uncertain_cos = classify(
        companies, existing, alias_map, fuzzy_candidates=fuzzy_candidates
    )

    log("1", f"extracted {len(companies)} candidate sections")
    log(
        "2",
        f"{len(new_cos)} NEW, {len(existing_cos)} existing to enhance, "
        f"{len(uncertain_cos)} uncertain (fuzzy match — needs confirmation)",
    )
    for u in uncertain_cos:
        hint = "  [STUB — author the note on it (D17)]" if u["likely_existing"] in stubs else ""
        log("2", f"  ? {u['candidate']}  ~=  existing '{u['likely_existing']}'{hint}")
    if args.cross_check and new_cos:
        cross_check_new([name for name, _, _ in new_cos])
    # S7: ensure cross-check connection release before Stage 3/5
    conn.commit()
    print()

    # Stage 3: new entities
    log("3", f"creating {len(new_cos)} new entities...")
    # Hoist the sector-entity query out of the per-entity loop (was N+1).
    sector_entities = get_sector_entities(conn) if args.apply else set()
    created = []

    # Resolve sectors for all new companies (fast, pure CPU).
    resolved = []  # (name, line, sector) tuples for companies with a sector
    for name, line, header_sector in new_cos:
        window = content.split("\n")[line - 1 : line + 2]
        # Use header-declared sector when available (overrides guess_sector_for).
        if header_sector:
            sector = header_sector
        else:
            sector = guess_sector_for(name, " ".join(window), sector_dirs)
        if not sector:
            log("3", f"  ⚠ {name}: could not guess sector — skipping (add manually)")
            continue
        resolved.append((name, line, sector))

    # Resolve tickers concurrently in --apply mode (network I/O bound).
    # In dry-run, skip ticker resolution entirely (ticker=None).
    if args.apply and resolved:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=4) as pool:
            ticker_results = list(pool.map(lambda ns: search_ticker(ns[0]), resolved))
        tickers = {resolved[i][0]: (r[0] if r else None) for i, r in enumerate(ticker_results)}
    else:
        tickers = {}

    # Bundle U2: wrap the create loop in `with conn:` so a mid-batch failure
    # (e.g. yfinance timeout in resolve_ticker) rolls back the DB writes
    # atomically. Previously the manual conn.commit() at the end meant a crash
    # left the connection's implicit-transaction state to GC — now the
    # context manager guarantees rollback on exception. Note: markdown stub
    # files written inside create_entity are NOT transactional (filesystem
    # writes can't roll back); the integrity check's orphan-files scan
    # catches any stragglers.
    with conn:
        for name, _line, sector in resolved:
            ticker = tickers.get(name)
            normalized, file_path = create_entity(
                conn, name, sector, ticker, args.apply, sector_entities
            )
            created.append((name, sector, ticker, file_path))
            log("3", f"  + {name} [{sector}] ticker={ticker}")
    print()

    # Stage 4: worklist (always emitted — dry-run or apply)
    out_dir = md_path.parent
    # D17: stub annotation — exchange-seeded entities (file_path IS NULL)
    # are the upgrade targets, distinct from authored notes to enhance.
    # stubs/tickers census lives above Stage-2 logging (unconditional; see note there)

    # S6/S8: resolve the entity universe once, then detect
    company_names = {n for n, _, _ in new_cos + existing_cos}
    sector_commentary = _detect_sector_commentary(content, company_names)
    listed_contradictions = _detect_listed_contradictions(content, company_names)
    listed_missing = _missing_ticker_items(new_cos, existing_cos, stubs, tickers)

    wl = emit_worklist(
        md_path,
        edition_title,
        new_cos,
        existing_cos,
        uncertain_cos,
        out_dir,
        stubs=stubs,
        tickers=tickers,
        sector_commentary=sector_commentary,
        listed_contradictions=listed_contradictions,
        listed_missing=listed_missing,
    )
    log("4", f"enhancement worklist -> {wl.relative_to(PROJECT_ROOT)}")
    log(
        "4",
        "Stage 4 (insight enrichment) is NOT auto-executed. Hand the worklist to an agent.",
    )
    print()

    # Stage 5: validate
    ok = run_validation(args.apply)
    if not ok:
        if args.apply:
            conn.close()
        sys.exit(1)
    print()

    # Stage 6: graph analytics (opt-in)
    if args.with_analytics:
        log("6", "recomputing graph_analytics...")
        analytics_ok = run_graph_analytics()
        print()
        log("done", "OK" + "" if analytics_ok else " (analytics reported issues)")
    else:
        log("done", "OK")
    # graph_rebuild_fast_path S2 (rebuild-on-apply): the apply wrote
    # entities/edges — refresh the DuckDB cache NOW so the first graph
    # query after the ingest doesn't pay the rebuild inline. S1's dirty
    # tracking makes this proportionate; an idempotent re-run patches to
    # a cheap no-op.
    if args.apply:
        try:
            from helpers.graph.query import rebuild as _rebuild

            log("7", "rebuilding graph cache...")
            _rebuild()
        except Exception as exc:  # noqa: BLE001  # the ingest is applied; the stale cache fails loud downstream
            print(f"WARNING: post-apply graph rebuild failed: {exc}", file=sys.stderr)
    if args.apply:
        conn.close()
    sys.exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
