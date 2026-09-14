#!/usr/bin/env python3
"""CIN / LLPIN identifier parsing (ontology_convention_stack S3).

Corporate Identity Number (MCA, India): 21 chars, e.g.
``L01631KA2010PTC096843`` — listing flag | 5-digit NIC-2008 | ROC state
code | incorporation year | ownership class | ROC serial. The parser is
FORMAT-strict but VALUE-LENIENT: structure violations are errors, while
unknown state/ownership codes, out-of-window years, and pre-2008
vintages are warnings — the value still parses, because MCA legacy data
(NIC-1987/1998/2004 codes inside pre-2008 CINs) is real and the
cross-checks are WARNING-tier (proposal §3, memo §6.6).

LLPIN shape (``AAA-1234``) is recognised DISTINCTLY so callers route the
value into ``entity_identifiers`` as ``llpin`` — LLPs never carry a CIN
— instead of surfacing a generic format error.

Pure module: no DB imports, no I/O. Consumed by
helpers/misc/backfill_identifiers.py (facet convergence + --set-cin),
the ``check_identifiers`` integrity check, and the triage funnel.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

CIN_RE = re.compile(r"^([LU])(\d{5})([A-Z]{2})(\d{4})([A-Z]{3})(\d{6})$")

# LLPIN: 3 alphanumerics, optional hyphen, 4 digits (AAA-1234 / AAA1234).
LLPIN_RE = re.compile(r"^[A-Z0-9]{3}-?\d{4}$")

# Ownership class (positions 16-18). Unknown codes warn, never reject —
# MCA mints new classes and the integrity check is advisory.
OWNERSHIP_CLASSES: frozenset[str] = frozenset({"PLC", "PTC", "OPC", "FTC", "GOI", "SGC", "NPC"})

# Attested ROC state codes (memo §6.6 core set — the major ROC seats and
# Union territories). Positions 6-7 are an ROC OFFICE code rather than a
# strict state code, so unknown values warn rather than reject.
ROC_STATES: frozenset[str] = frozenset(
    {
        "AN",
        "AP",
        "AS",
        "BR",
        "CH",
        "CT",
        "DL",
        "GA",
        "GJ",
        "HP",
        "HR",
        "JH",
        "JK",
        "KA",
        "KL",
        "MH",
        "MP",
        "OD",
        "PB",
        "PY",
        "RJ",
        "TN",
        "UP",
        "UT",
        "WB",
    }
)

# Incorporation-year sanity window; CINs from before 2008 carry legacy
# NIC-1987/1998/2004 codes (cin_nic5 must not be treated as NIC-2008).
_YEAR_MIN = 1850
_NIC_2008_EPOCH = 2008


@dataclass(frozen=True)
class CinParse:
    """Result of :func:`parse_cin` — ``ok`` iff ``error is None``.

    ``warnings`` entries are ``"kind: detail"`` strings so callers can
    count by prefix (the integrity check aggregates these kinds).
    """

    raw: str
    value: str | None = None  # normalized 21-char CIN
    listing: str | None = None  # L listed / U unlisted
    nic5: str | None = None  # NIC code (pre-2008 vintages: legacy series)
    state: str | None = None  # ROC state/office code
    year: int | None = None  # incorporation year
    ownership: str | None = None  # 3-char class
    serial: str | None = None  # ROC serial
    error: str | None = None  # "format" | "llpin"
    message: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.error is None


def normalize(value: str | None) -> str:
    """Strip whitespace/separators + uppercase — input hygiene, not validation."""
    return re.sub(r"[\s\-_.]", "", (value or "")).upper()


def parse_cin(value: str | None) -> CinParse:
    """Parse a CIN, or reject LLPIN-shaped input distinctly."""
    raw = value or ""
    norm = normalize(raw)
    if not norm:
        return CinParse(raw=raw, error="format", message="empty identifier")
    if LLPIN_RE.match(norm):
        return CinParse(
            raw=raw,
            error="llpin",
            message=(
                f"{norm} is LLPIN-shaped — LLPs carry an LLPIN, never a CIN; "
                "store in entity_identifiers as identifier_type='llpin'"
            ),
        )
    m = CIN_RE.match(norm)
    if m is None:
        return CinParse(
            raw=raw,
            error="format",
            message=(
                f"{norm!r} is not a 21-char CIN (expected L|U + 5 digits + "
                "2-letter state + 4-digit year + 3-letter ownership + 6-digit serial)"
            ),
        )
    listing, nic5, state, year_s, ownership, serial = m.groups()
    year = int(year_s)
    warnings: list[str] = []
    if ownership not in OWNERSHIP_CLASSES:
        warnings.append(f"unknown_ownership: {ownership}")
    if state not in ROC_STATES:
        warnings.append(f"unknown_state: {state}")
    if year < _YEAR_MIN or year > datetime.date.today().year:
        warnings.append(f"year_out_of_window: {year}")
    if year < _NIC_2008_EPOCH:
        warnings.append(f"vintage_pre2008: {year}")
    return CinParse(
        raw=raw,
        value=norm,
        listing=listing,
        nic5=nic5,
        state=state,
        year=year,
        ownership=ownership,
        serial=serial,
        warnings=tuple(warnings),
    )
