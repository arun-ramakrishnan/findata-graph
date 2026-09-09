"""Country vocabulary + ticker→home-market classification (country layer).

Single source for the geography vocabulary shared by derive_countries
(entities + listed_in edges), geo_converge (note convergence), sync_tags
(company geography whitelist), verify_notes (known-good tag values), and
parse_newsletter (tag seeding at note creation). A value enters the
vocabulary only via a ticker mapping or an audited ADR/OTC override —
which is why the module is the listed_in target set by construction.

Semantics: listed_in / geography:<value> is the HOME market (primary
listing country), not the ADR venue. Sector notes are exempt — their
geography/* tags describe COVERAGE and may legitimately say global
(C2 scope: company notes only).
"""

from __future__ import annotations

# Exchange-suffix -> home market. Closed map: a dotted ticker whose suffix
# is absent here is NEVER guessed (worklisted, reason unmapped_suffix).
TICKER_SUFFIX_TO_COUNTRY: dict[str, str] = {
    ".NS": "india",
    ".BO": "india",
    ".NSE": "india",
    ".BSE": "india",
    ".KS": "south_korea",
    ".KQ": "south_korea",
    ".SG": "singapore",
    ".PA": "france",
    ".TW": "taiwan",
    ".MI": "italy",
    ".HK": "hong_kong",
    ".SS": "china",
    ".SR": "saudi_arabia",
    ".T": "japan",
    ".HE": "finland",
    ".DE": "germany",
}

# Plain (dotless) symbols default to the US listing — EXCEPT the audited
# foreign ADR/OTC lines, whose home market is elsewhere (2026-09-09 audit
# of all 64 plain symbols in the DB; Yahoo ADR/OTC names — mostly the
# 5-letter -Y/-F endings — are OTC lines of foreign primary listings).
# Adding a symbol here is a curation decision; the ticker trail lands in
# edge properties so every assignment is auditable after the fact.
PLAIN_TICKER_OVERRIDES: dict[str, str] = {
    "AZN": "uk",
    "BABA": "china",
    "BIDU": "china",
    "BIRK": "austria",
    "DEO": "uk",
    "GSK": "uk",
    "HCMLY": "switzerland",
    "HKHHY": "netherlands",
    "HLN": "uk",
    "INVZ": "israel",
    "JTEKY": "japan",
    "KUBTY": "japan",
    "NVS": "switzerland",
    "PUK": "uk",
    "SCBFF": "uk",
    "SFFYF": "netherlands",
    "SLF": "canada",
    "SNY": "france",
    "SZKMY": "japan",
    "TTE": "france",
    "UL": "uk",
    "VFS": "vietnam",
    "VLVLY": "sweden",
}

# The full country vocabulary: every value the ticker mapper can emit.
# This is the whitelist sync_tags enforces for company geography/* tags
# and the target set of listed_in edges.
COUNTRY_VOCABULARY = (
    frozenset(TICKER_SUFFIX_TO_COUNTRY.values())
    | frozenset(PLAIN_TICKER_OVERRIDES.values())
    | {"usa"}
)

# Company-note geography tag values that are NOT countries, and their
# convergence fate (geo_converge C2):
#   * regional qualifiers fold into india (they were only ever applied to
#     India-focused companies in this corpus);
#   * vague scope values are dropped outright (the geography key is a
#     single-valued home market; multi-market is a listed_in question).
# Sector notes never carry these (measured 2026-09-09) and keep their
# coverage-style geography/india + geography/global tags untouched.
REGIONAL_TO_INDIA = frozenset({"domestic_focused", "pan_india", "north_india", "west_india"})
DROPPED_GEOGRAPHY_VALUES = frozenset({"global", "international", "south_asia"})


def classify_ticker(ticker: str | None) -> tuple[str | None, str]:
    """(country | None, via) for one ticker symbol.

    via is one of 'suffix:<suf>' | 'plain:override' | 'plain:default' |
    'no_ticker' | 'unmapped_suffix' — recorded in edge properties so the
    assignment trail is auditable after the fact.
    """
    t = (ticker or "").strip()
    if not t:
        return None, "no_ticker"
    if "." in t:
        suffix = t[t.rfind(".") :].upper()
        country = TICKER_SUFFIX_TO_COUNTRY.get(suffix)
        if country:
            return country, f"suffix:{suffix}"
        return None, "unmapped_suffix"
    country = PLAIN_TICKER_OVERRIDES.get(t.upper())
    if country:
        return country, "plain:override"
    return "usa", "plain:default"
