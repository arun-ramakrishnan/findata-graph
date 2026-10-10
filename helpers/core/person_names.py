#!/usr/bin/env python3
"""Conservative person, HUF, and trust name resolution helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable

from helpers.core.fuzzy_match import word_overlap_match

_HUF_TOKENS = ("HUF", "HINDU UNDIVIDED FAMILY", "UNDIVIDED FAMILY")
_TRUST_TOKENS = ("TRUST", "FAMILY OFFICE", "FAMILYOFFICE", "FO")
_TOKEN_RE = re.compile(r"[A-Z0-9]+", re.IGNORECASE)


def _matches_any_token(normalized: str, tokens: tuple[str, ...]) -> bool:
    """Word-boundary token match (S6/M4, range review) — never substring.

    The old `token in normalized` let the 2-letter family-office token
    'FO' substring-match FOODS of India, FORTIS Healthcare and INFO EDGE,
    classifying live companies as trusts so resolve_person matched them
    against the wrong pool (sole caller:
    shareholding_sync.apply_candidates).
    """
    return any(re.search(rf"\b{re.escape(token)}\b", normalized) for token in tokens)


def classify_person_name(name: str) -> str:
    normalized = re.sub(r"[()\[\]{}]", " ", name.upper())
    if _matches_any_token(normalized, _HUF_TOKENS):
        return "huf"
    if _matches_any_token(normalized, _TRUST_TOKENS):
        return "trust"
    return "person"


def person_name_key(name: str) -> str:
    tokens = _TOKEN_RE.findall(name.casefold())
    return " ".join(sorted(tokens))


def resolve_person(
    name: str, candidates: Iterable[tuple[str, str]], threshold: float = 0.6
) -> tuple[str | None, float]:
    classification = classify_person_name(name)
    eligible = [
        candidate for candidate, candidate_class in candidates if candidate_class == classification
    ]
    return word_overlap_match(name, eligible, threshold=threshold)


def dedupe_report(names: Iterable[str]) -> list[list[str]]:
    groups: dict[tuple[str, str], list[str]] = {}
    for name in names:
        key = (classify_person_name(name), person_name_key(name))
        groups.setdefault(key, []).append(name)
    return [sorted(group) for group in groups.values() if len(group) > 1]
