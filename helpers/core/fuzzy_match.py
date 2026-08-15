#!/usr/bin/env python3
"""Hybrid fuzzy company-name matcher.

Three-stage resolution pipeline:
  1. Exact match (case-insensitive)
  2. Abbreviation lookup (TCS → "Tata Consultancy Services")
  3. Word-overlap heuristic (intersection of meaningful words)
  4. Spellfix1 fallback for typos (requires sqlite-spellfix)

The word-overlap heuristic is the primary matcher because it handles
legal-name suffixes ("Tata Motors" → "Tata Motors Passenger Vehicles")
and sibling disambiguation ("Adani Port" → "Adani Ports and SEZ", not
"Adani Power") better than edit-distance methods.

Data coverage of the vault is ~97%; only a few entities are truly missing.
"""
from collections.abc import Iterable
import sqlite3

# ---------------------------------------------------------------------------
# Abbreviation table: common abbreviations → full legal names
# ---------------------------------------------------------------------------

ABBREVIATIONS = {
    # IT / Technology
    "TCS": "Tata Consultancy Services",
    "HCL": "HCL Technologies",
    "TECHM": "Tech Mahindra",
    "WPRO": "Wipro",
    "INFY": "Infosys",
    "MPHASIS": "Mphasis",
    "PERSISTENT": "Persistent Systems",
    "COFORGE": "Coforge",
    "LTTS": "L&T Technology Services",
    "LTI": "Larsen & Toubro Infotech",
    "MINDTREE": "Mindtree",
    "HEXAWARE": "Hexaware Technologies",
    "CYIENT": "Cyient",
    "ZENSAR": "Zensar Technologies",
    "NIIT": "NIIT",

    # Automotive
    "M&M": "Mahindra & Mahindra",
    "MMFIN": "Mahindra & Mahindra Financial Services",
    "TATAMOTORS": "Tata Motors Passenger Vehicles",
    "MARUTI": "Maruti Suzuki India",
    "BAJAJ-AUTO": "Bajaj Auto",
    "HEROMOTOCO": "Hero MotoCorp",
    "EICHERMOT": "Eicher Motors",
    "TVS": "TVS Motor Company",
    "ASHOKLEY": "Ashok Leyland",
    "FORCEMOT": "Force Motors",

    # Pharma
    "SUNPHARMA": "Sun Pharmaceutical Industries",
    "DRREDDY": "Dr Reddys Laboratories",
    "CIPLA": "Cipla",
    "LUPIN": "Lupin",
    "BIOCON": "Biocon",
    "DIVISLAB": "Divis Laboratories",
    "AUROPHARMA": "Aurobindo Pharma",
    "ALKEM": "Alkem Laboratories",
    "TORNTPHARM": "Torrent Pharmaceuticals",
    "GLENMARK": "Glenmark Pharmaceuticals",
    "IPCALAB": "IPCA Laboratories",

    # Banking / Finance
    "HDFCBANK": "HDFC Bank",
    "ICICIBANK": "ICICI Bank",
    "SBIN": "State Bank of India",
    "KOTAKBANK": "Kotak Mahindra Bank",
    "AXISBANK": "Axis Bank",
    "INDUSINDBK": "IndusInd Bank",
    "BANDHANBNK": "Bandhan Bank",
    "FEDERALBNK": "Federal Bank",
    "IDFCFIRSTB": "IDFC First Bank",
    "BAJFINANCE": "Bajaj Finance",
    "BAJAJFINSV": "Bajaj Finserv",
    "CHOLAFIN": "Cholamandalam Investment",
    "LTFH": "L&T Finance Holdings",
    "MMFSL": "Mahindra & Mahindra Financial Services",
    "SBILIFE": "SBI Life Insurance Company",
    "HDFCAMC": "HDFC Asset Management",
    "HDFCLIFE": "HDFC Life",
    "ICICIPRULI": "ICICI Prudential Life Insurance",
    "ICICIGI": "ICICI Lombard General Insurance",

    # FMCG
    "HINDUNILVR": "Hindustan Unilever",
    "NESTLEIND": "Nestle India",
    "BRITANNIA": "Britannia Industries",
    "DABUR": "Dabur India",
    "MARICO": "Marico",
    "GODREJCP": "Godrej Consumer Products",
    "PGHH": "P&G Hygiene and Healthcare",

    # Energy / Utilities
    "RELIANCE": "Reliance Industries",
    "NTPC": "NTPC",
    "POWERGRID": "Power Grid Corporation of India",
    "ADANIENT": "Adani Enterprises",
    "ADANIPORTS": "Adani Ports and SEZ",
    "ADANIGREEN": "Adani Green Energy",
    "TATAPOWER": "Tata Power",
    "NHPC": "NHPC",

    # Industrial / Manufacturing
    "LT": "Larsen and Toubro",
    "BHEL": "Bharat Heavy Electricals",
    "SIEMENS": "Siemens",
    "ABB": "ABB India",
    "HAL": "Hindustan Aeronautics",
    "BEL": "Bharat Electronics",
    "GRSE": "Garden Reach Shipbuilders & Engineers",
    "COCHINSHIP": "Cochin Shipyard",
    "MAZDOCK": "Mazagon Dock Shipbuilders",

    # Retail / E-Commerce
    "NYKAA": "FSN E-Commerce",
    "PAYTM": "One 97 Communications PayTM",

    # Telecom
    "BHARTIARTL": "Bharti Airtel",
    "IDEA": "Vodafone Idea",

    # Fintech (Yahoo Finance variant names)
    "One97 Communications": "One 97 Communications PayTM",
    "One97 Communications Limited": "One 97 Communications PayTM",

    # Cement
    "ULTRACEMCO": "UltraTech Cement",
    "AMBUJACEM": "Ambuja Cement",
    "SHREECEM": "Shree Cement",
    "ACC": "ACC",
    "RAMCOCEM": "The Ramco Cements",

    # Paints
    "ASIANPAINT": "Asian Paints",
    "BERGEPAINT": "Berger Paints India",
    "KANSAINER": "Kansai Nerolac Paints",

    # Consumer Durables
    "HAVELLS": "Havells India",
    "CROMPTON": "Crompton Greaves Consumer Electricals",
    "VGUARD": "V-Guard Industries",
    "SYMPHONY": "Symphony",
    "BLUESTARCO": "Blue Star",

    # Pipes / Cables
    "POLYCAB": "Polycab India",
    "KEI": "KEI Industries",
    "FINCABLES": "Finolex Cables",

    # Logistics
    "CONCOR": "Container Corporation of India",
    "INDIGO": "Interglobe Aviation",

    # Insurance
    "NIACL": "The New India Assurance",
    "GICRE": "General Insurance Corporation",
}

# Reverse lookup: entity name → abbreviation
ABBREVIATION_BY_ENTITY = {v: k for k, v in ABBREVIATIONS.items()}


# ---------------------------------------------------------------------------
# Word-overlap heuristic
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "limited", "ltd", "corporation", "corp", "company", "co", "inc",
    "incorporated", "the", "and", "of", "at", "to", "in", "on", "for",
    "with", "by", "per", "a", "an", "india", "indian",
})

# Generic words that are common suffixes/prefixes across many company names.
# A match based ONLY on these words is rejected — at least one shared word
# must be distinctive. This prevents "HDFC Asset Management" from matching
# "UTI Asset Management" while still allowing "Power Grid Corporation" to
# match "Power Grid Corporation of India" (shared "power"/"grid" are distinctive).
_GENERIC_WORDS = frozenset({
    "asset", "management", "financial", "finance", "capital", "markets",
    "services", "solutions", "industries", "holdings", "group", "ventures",
    "enterprises", "international", "global", "india", "indian",
    "life", "general", "insurance", "mutual", "fund", "securities",
    "investment", "investments", "credit", "housing", "bank", "banking",
    "power", "energy", "oil", "gas", "steel", "cement", "pharma",
    "healthcare", "technology", "technologies", "retail", "consumer",
})


def _tokenize(name: str) -> set:
    """Tokenize a company name into meaningful words."""
    # Remove common punctuation
    cleaned = name.replace("&", " ").replace("-", " ").replace(".", " ")
    return set(cleaned.lower().split()) - _STOPWORDS


def word_overlap_match(
    query: str,
    entities: Iterable[str],
    threshold: float = 0.5,
) -> tuple[str | None, float]:
    """Find best match using word-overlap heuristic.

    Args:
        query: Search query string
        entities: Iterable of entity name strings
        threshold: Minimum overlap ratio (0.0 to 1.0)

    Returns:
        (best_match_name, score) or (None, 0.0)
    """
    query_lower = query.lower().strip()
    query_parts = _tokenize(query)

    best_match = None
    best_score = 0.0

    for entity in entities:
        entity_lower = entity.lower()

        # Direct containment (high confidence). Guard against blank/whitespace
        # queries: '' (and unstripped ' ') is a substring of every string — and
        # a bare ' ' is contained by any multi-word entity — so without this
        # guard a whitespace query would false-positive onto the first entity
        # at score 1.0. Stripping query_lower makes the truthiness check reject
        # both '' and ' '.
        if query_lower and (query_lower in entity_lower or entity_lower in query_lower):
            return entity, 1.0

        # Word overlap — require at least one distinctive (non-generic)
        # shared word so that generic suffixes like "Asset Management Company"
        # alone don't trigger a match.
        entity_parts = _tokenize(entity)
        if query_parts and entity_parts:
            overlap = query_parts & entity_parts
            if overlap and (overlap - _GENERIC_WORDS):
                score = len(overlap) / max(len(query_parts), len(entity_parts))
                if score > best_score:
                    best_score = score
                    best_match = entity

    if best_score > threshold:
        return best_match, best_score
    return None, 0.0


# ---------------------------------------------------------------------------
# Hybrid matcher
# ---------------------------------------------------------------------------

def fuzzy_match(
    query: str,
    entities: Iterable[str],
    threshold: float = 0.5,
    spellfix_conn: sqlite3.Connection | None = None,
) -> tuple[str | None, str | None, float]:
    """Hybrid fuzzy matching pipeline.

    Resolution order:
      1. Exact match (case-insensitive)
      2. Abbreviation lookup (TCS → "Tata Consultancy Services")
      3. Word-overlap heuristic
      4. Spellfix1 fallback for typos (requires sqlite-spellfix + conn)

    Args:
        query: Search query string
        entities: Iterable of entity name strings
        threshold: Minimum word-overlap ratio (0.0 to 1.0)
        spellfix_conn: Optional SQLite connection with spellfix1 loaded

    Returns:
        (matched_name, method, score) or (None, None, 0.0)
        method is one of: 'exact', 'abbreviation', 'word_overlap', 'spellfix1'
    """
    # 1. Exact match (case-insensitive)
    query_lower = query.lower().strip()
    for entity in entities:
        if entity.lower() == query_lower:
            return entity, "exact", 1.0

    # 2. Abbreviation lookup (exact or substring match)
    upper_query = query.upper().strip()
    if upper_query in ABBREVIATIONS:
        return ABBREVIATIONS[upper_query], "abbreviation", 1.0
    # Also check if any abbreviation key is a substring of the query
    # This handles cases like "One97 Communications Limited" matching "One97 Communications"
    for abbr, entity in ABBREVIATIONS.items():
        if len(abbr) >= 3 and abbr.upper() in upper_query:
            return entity, "abbreviation", 1.0

    # 3. Word-overlap heuristic
    match, score = word_overlap_match(query, entities, threshold)
    if match:
        return match, "word_overlap", score

    # 4. Spellfix1 fallback for typos
    if spellfix_conn is not None:
        try:
            cursor = spellfix_conn.execute(
                """
                SELECT word, score FROM entities_fuzzy
                WHERE word MATCH ? AND distance < 200
                ORDER BY score LIMIT 1
                """,
                (query,),
            )
            result = cursor.fetchone()
            if result:
                return result[0], "spellfix1", 1.0
        except Exception:  # noqa: S110  # best-effort; ignore failure (cleanup/optional read)
            pass  # spellfix not available, skip

    return None, None, 0.0


def build_spellfix_table(conn, entities: Iterable[str]) -> bool:
    """Create and populate a spellfix1 virtual table for fuzzy matching.

    Args:
        conn: SQLite connection with extension loading enabled
        entities: Iterable of entity name strings

    Returns:
        True if table created, False if spellfix1 not available
    """
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entities_fuzzy'"
        )
        if cursor.fetchone() is None:
            conn.execute("CREATE VIRTUAL TABLE entities_fuzzy USING spellfix1")

        for entity in entities:
            conn.execute("INSERT INTO entities_fuzzy(word) VALUES (?)", (entity,))
        conn.commit()
        return True
    except Exception:
        return False
