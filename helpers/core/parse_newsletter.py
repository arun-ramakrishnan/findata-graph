#!/usr/bin/env python3
"""
End-to-end newsletter parser orchestrator.

Runs the mechanical stages of doc/procedures/markdown_parse.md for a single
newsletter, so the only step a human/agent still does interactively is Stage 4
(enriching notes with hand-curated insights). Everything else is one command:

    python3 helpers/core/parse_newsletter.py <newsletter.md> [--apply]

WHAT IT DOES
------------
Stage 0  Capture expiring OCR-crop images  (delegates to capture_newsletter_images.py --rewrite)
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
from helpers.core.db import utc_now  # noqa: E402  (Bundle T1: UTC last_updated)

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
# Heading regex — reused across calls.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

# --- company-heading constants (hoisted from extract_companies for reuse) -----
_CAP_TOKENS = (
    "large cap", "mid cap", "small cap", "micro cap", "nano cap", "mega cap",
    "unlisted",
)
# Cut the name at the first cap token (incl. OCR variants like 'Larg Cap', 'Mid Cap.').
_CAP_CUT_RE = re.compile(r"\s+(?:large|larg|mid|small|micro|nano|mega)\s*cap", re.IGNORECASE)
_SECTOR_WORDS = frozenset({
    "retail", "energy", "renewables", "metals", "metal", "healthcare",
    "pharma", "pharmaceuticals", "hospitals", "diagnostics", "technology",
    "semiconductors", "financial services", "nbfc", "housing finance",
    "capital markets", "fintech", "insurance", "consumer durables",
    "automotive", "automotives", "telecom", "telecommunications",
    "real estate", "realty", "defence", "defense", "chemicals",
    "aerospace & defence", "engineering & capital goods", "railways",
    "ems manufacturing", "consumer", "software", "hotels", "hotel",
    "tourism", "logistics", "media", "entertainment", "textiles",
    "building materials", "packaging", "agriculture", "education",
    "edtech", "electronics", "mining", "aviation", "infrastructure",
    "fertilizer",
})


def log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}")


# ===========================================================================
# DB helpers (direct sqlite3; the SQLite MCP is for the agent, not this script)
# ===========================================================================
def get_existing_entity_names(conn) -> set:
    """All company entity names + normalized_names currently in the DB."""
    rows = conn.execute(
        "SELECT name FROM entities WHERE entity_type='company'"
    ).fetchall()
    norm = conn.execute(
        "SELECT normalized_name FROM entities WHERE entity_type='company' "
        "AND normalized_name IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows} | {r[0].replace("_", " ") for r in norm}


def get_sector_dirs() -> set:
    """Existing sector directory names under findata/Companies/."""
    return {p.name for p in COMPANIES.iterdir() if p.is_dir()}


def get_sector_entities(conn) -> set:
    rows = conn.execute(
        "SELECT name FROM entities WHERE entity_type='sector'"
    ).fetchall()
    return {r[0] for r in rows}


# ===========================================================================
# Stage 0: image capture
# ===========================================================================
def capture_images(md_path: Path, apply: bool) -> bool:
    """Returns True if images were captured/already-present, False on failure."""
    log("0", f"capturing images -> {CAPTURE_SCRIPT.name} --rewrite")
    if not apply:
        log("0", "DRY-RUN: would run capture with --rewrite")
        return True
    cmd = ["python3", str(CAPTURE_SCRIPT), str(md_path), "--rewrite"]
    try:
        subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
        return True
    except subprocess.CalledProcessError as e:
        log("0", f"capture FAILED (exit {e.returncode})")
        return False


# ===========================================================================
# Stage 1: extract candidate companies from the rewritten source
# ===========================================================================
def extract_companies(content: str):
    """
    Yield (candidate_name, line_number) for each company-looking section heading.
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
        yield canonical, line_no


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



def classify(companies, existing_names: set):
    """
    Split into (new, existing, uncertain).
    - existing: exact name/normalized match.
    - uncertain: no exact match but a strong fuzzy match exists -> do NOT
      auto-create (would risk a duplicate); flag for confirmation.
    - new: no match at all.
    """
    existing_lower = {n.lower() for n in existing_names}
    existing_list = list(existing_names)
    new_co, existing_co, uncertain_co = [], [], []
    seen = set()
    for name, line in companies:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        if name in existing_names or key in existing_lower:
            existing_co.append((name, line))
            continue
        # Use shared fuzzy matcher (exact -> abbreviation -> word_overlap -> spellfix)
        fuzzy, method, score = fuzzy_match(name, existing_list)
        if fuzzy:
            uncertain_co.append(
                {"candidate": name, "likely_existing": fuzzy, "section_line": line}
            )
        else:
            new_co.append((name, line))
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
        # ---- Engineering carve-outs ----
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
    tags = [
        "entity_type/company",
        f"sector/{sector.lower()}",
        "geography/india",
    ]
    tag_block = "\n".join(f"- {t}" for t in tags)
    today = date.today().isoformat()
    # Title is UNQUOTED (canonical style; verify_notes warns on quotes for both
    # sectors and companies). ticker:null marks an unlisted company, which is a
    # meaningful category, so it is made explicit with `listed: false` (mirrors
    # the 106 existing unlisted notes).
    ticker_line = "null" if not ticker else repr(ticker)
    listed_line = "\nlisted: false" if not ticker else ""
    return f"""---
title: {name}
type: company
ticker: {ticker_line}{listed_line}
tags:
{tag_block}
normalized_name: {normalized_name}
sector: {sector}
permalink: {permalink}
created: '{today}'
last_modified: '{today}'
---

# {name}

## Company Overview
{name} — auto-generated stub from newsletter parsing. To be enriched.

## The Chatter — <edition title>

*Source: The Chatter — <edition title>*
"""


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
            # Bidirectional membership edges if the sector entity exists.
            # NB: writes to graph_edges (since the Phase-1 migration the
            # `relations` name is a read-only VIEW over graph_edges).
            se = sector_entities if sector_entities is not None else get_sector_entities(conn)
            if sector in se:
                conn.execute(
                    "INSERT OR IGNORE INTO graph_edges"
                    " (source, target, edge_type, source_ref) VALUES (?,?,?,?)",
                    (name, sector, "part_of", "parse_newsletter"),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO graph_edges"
                    " (source, target, edge_type, source_ref) VALUES (?,?,?,?)",
                    (sector, name, "has_company", "parse_newsletter"),
                )
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
def emit_worklist(
    md_path, edition_title, new_cos, existing_cos, uncertain_cos, out_dir
):
    slug = md_path.stem
    wl = {
        "newsletter": str(md_path.relative_to(PROJECT_ROOT)),
        "edition_title": edition_title,
        "new_entities": [{"name": n, "section_line": ln} for n, ln in new_cos],
        "existing_entities_to_enhance": [
            {"name": n, "section_line": ln} for n, ln in existing_cos
        ],
        "uncertain_entities": uncertain_cos,
        "instructions": (
            "For each entity, read its concall section (starting at section_line in "
            "the newsletter), extract 3-5 bullet insights + 1 verbatim quote, and "
            "append/replace a '## The Chatter — <edition_title>' block. See "
            "doc/procedures/markdown_parse.md#enhancing-existing-entities."
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
        r = subprocess.run(["python3", str(script)], cwd=str(PROJECT_ROOT))  # noqa: S603,S607  # list-form call; shell=False (default); args are constants/controlled paths; PATH-resolved interpreter/binary (python3/node/grep) by design
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
        ["python3", str(ALGORITHMS), "--all", "--apply"],  # noqa: S607  # PATH-resolved interpreter/binary (python3/node/grep) by design
        cwd=str(PROJECT_ROOT),
    )
    if r.returncode != 0:
        log("6", f"analytics FAILED (exit {r.returncode}); entity/notes unaffected")
        return False
    return True


# ===========================================================================
# Stage 2b (--cross-check): semantic guard for the NEW/known extractor gap
# ===========================================================================
def cross_check_new(new_names, min_sim=0.55):
    """Flag NEW-classified names that look like EXISTING company notes.

    The known extractor gap: dry-run classification can miss an existing
    company (different spelling/alias) and flag it NEW, which --apply
    would stub as a duplicate entity. For each NEW name, embed the name
    (query prefix) and KNN it against the existing company notes in the
    warm graph's ``v_note_embeddings`` (query.notes_like_text). Read-only;
    surfaces close matches for manual confirmation before --apply.

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
def main():  # noqa: C901
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
    args = ap.parse_args()

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
    companies = list(extract_companies(content))
    existing = get_existing_entity_names(conn)
    sector_dirs = get_sector_dirs()

    new_cos, existing_cos, uncertain_cos = classify(companies, existing)

    log("1", f"extracted {len(companies)} candidate sections")
    log(
        "2",
        f"{len(new_cos)} NEW, {len(existing_cos)} existing to enhance, "
        f"{len(uncertain_cos)} uncertain (fuzzy match — needs confirmation)",
    )
    for u in uncertain_cos:
        log("2", f"  ? {u['candidate']}  ~=  existing '{u['likely_existing']}'")
    if args.cross_check and new_cos:
        cross_check_new([name for name, _line in new_cos])
    print()

    # Stage 3: new entities
    log("3", f"creating {len(new_cos)} new entities...")
    # Hoist the sector-entity query out of the per-entity loop (was N+1).
    sector_entities = get_sector_entities(conn) if args.apply else set()
    created = []

    # Resolve sectors for all new companies (fast, pure CPU).
    resolved = []  # (name, line, sector) tuples for companies with a sector
    for name, line in new_cos:
        window = content.split("\n")[line - 1 : line + 2]
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
            ticker_results = list(
                pool.map(lambda ns: search_ticker(ns[0]), resolved)
            )
        tickers = {
            resolved[i][0]: (r[0] if r else None)
            for i, r in enumerate(ticker_results)
        }
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
    wl = emit_worklist(
        md_path, edition_title, new_cos, existing_cos, uncertain_cos, out_dir
    )
    log("4", f"enhancement worklist -> {wl.relative_to(PROJECT_ROOT)}")
    log(
        "4",
        "Stage 4 (insight enrichment) is NOT auto-executed. "
        "Hand the worklist to an agent.",
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
    if args.apply:
        conn.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
