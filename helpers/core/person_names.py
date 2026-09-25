#!/usr/bin/env python3
"""Conservative person, HUF, and trust name resolution helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable

from helpers.core.fuzzy_match import word_overlap_match

_HUF_TOKENS = ("HUF", "HINDU UNDIVIDED FAMILY", "UNDIVIDED FAMILY")
_TRUST_TOKENS = ("TRUST", "FAMILY OFFICE", "FAMILYOFFICE", "FO")
_TOKEN_RE = re.compile(r"[A-Z0-9]+", re.IGNORECASE)


def classify_person_name(name: str) -> str:
    normalized = re.sub(r"[()\[\]{}]", " ", name.upper())
    if any(token in normalized for token in _HUF_TOKENS):
        return "huf"
    if any(token in normalized for token in _TRUST_TOKENS):
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
