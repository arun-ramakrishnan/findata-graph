#!/usr/bin/env python3
"""Conservative person, HUF, and trust name resolution helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

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


_REPO_ROOT_PERSON = Path(__file__).resolve().parents[2]
PERSON_PLAN_PATH = _REPO_ROOT_PERSON / "findata" / "Misc" / "_person_merge_plan.jsonl"
PERSON_LEDGER_PATH = _REPO_ROOT_PERSON / "findata" / "Misc" / "_person_fold_ledger.jsonl"


@lru_cache(maxsize=1)
def person_fold_registry(path=PERSON_PLAN_PATH, ledger_path=PERSON_LEDGER_PATH) -> dict[str, str]:
    """Folded-variant (by name key) → canonical. Two halves unioned:

    - the append-only LEDGER (durable — ``name_dedup.apply_plan`` extends
      it, so a signed person fold keeps resolving *forever* even after
      ``--plan`` regenerates the plan artifact and empties it);
    - the signed PLAN artifact (the live source before the ledger existed
      / within a session that has planned but not yet applied).

    The insert-time resolver consults this BEFORE minting a new person
    entity, so a filing whose holder spelling was folded by a reviewed
    plan reuses the canonical — the dedup and the resolver cannot drift.
    Missing both = {}.

    Cached (perf gate, derive_insights leg): resolve_speaker consults
    this per Quote attribution, and the S6 hot path turned the per-call
    JSONL re-parse into an O(N²) corpus walk (145 s vs 9 s budget).
    Artifacts are static within a process; no caller mutates-and-rereads
    (name_dedup.apply_plan writes the ledger directly)."""
    import json

    from helpers.core.entity_identity import name_key

    out: dict[str, str] = {}
    if ledger_path and Path(ledger_path).exists():
        for line in Path(ledger_path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            out[name_key(row["variant"])] = row["canonical"]
    if path and Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for variant in row["variants"]:
                out[name_key(variant)] = row["canonical"]
    return out
