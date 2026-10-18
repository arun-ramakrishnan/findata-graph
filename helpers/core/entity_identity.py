#!/usr/bin/env python3
"""Entity identity — THE name-normalisation and registry-lookup home.

One normalisation key for every name surface (write paths, census,
dedup, resolver), persons and companies alike. Before this module
existed, four normalisers drifted (``extract_relations._canonical_key``,
``name_dedup.normalise``, ``person_names.person_name_key``, the
``entities.normalized_name`` lookup in the vigil sync) — each folded
different things, so the same real-world entity could resolve at one
path and mint a duplicate at another (the entity_name_dedup census:
1,575 collision clusters). Operator ruling 2026-10-15: full merge now,
not a person-only staging — the drift is the hazard.

Rules of the house:

- **One key**: :func:`name_key` is the only fold any code may invent.
  Superset of all four predecessors: de-accent (NFKD), footnote-marker
  strip, apostrophe drop, camelCase split, punctuation→space,
  whitespace collapse, casefold.
- **Legal lines are part of identity**: :func:`legal_line` delegates to
  ``person_names.classify_person_name`` — individual / huf / trust.
  An HUF never merges into the individual (entity_name_dedup
  non-goal; person_name_dedup §1).
- **Registries, not scattered lookups**: :func:`registry_index` (the
  entities census-key index) and :func:`fold_registry` (the signed
  merge-plan fold map) are the only lookup structures; write paths
  consult them before minting new names.
- **Two tiers, never conflated**: tier 1 = :func:`name_key` exact
  (identity — safe to auto-apply); tier 2 = ``helpers.core.fuzzy_match``
  (near-match SCORING — word_overlap/spellfix, threshold-gated, feeds
  human queues and ``person_names.resolve_person``). Fuzzy scores may
  suggest; only the key decides identity. ``fuzzy_match._tokenize`` is
  a scoring tokenizer, not an identity function.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

FOOTNOTE_RE = re.compile(r"\s*\*+\s*$")
_APOSTROPHE_RE = re.compile("['’]")
_CAMEL_RE_1 = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_RE_2 = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")
_PUNCT_RE = re.compile(r"[^\w\s]")

DEFAULT_PLAN_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_merge_plan.txt"
DEFAULT_LEDGER_PATH = _REPO_ROOT / "findata" / "Misc" / "_name_fold_ledger.jsonl"


def name_key(name: str) -> str:
    """The one normalisation key. Deterministic, conservative (no
    token re-ordering, no initials logic): ``E.I.D.PARRY (INDIA)`` ≡
    ``E.I.D. Parry (India)`` ≡ ``e.i.d. - parry india``, while
    ``HDFC Bank`` and ``HDFC Securities`` stay apart."""
    s = unicodedata.normalize("NFKD", name.strip())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = FOOTNOTE_RE.sub("", s)
    s = _APOSTROPHE_RE.sub("", s)
    s = _CAMEL_RE_1.sub(" ", s)
    s = _CAMEL_RE_2.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).casefold().strip()


def token_sort_key(name: str) -> str:
    """Order-insensitive secondary key (report-only clustering — never
    a merge key): 'IndianOil Adani Gas' ≡ 'Adani Gas IndianOil'."""
    return " ".join(sorted(name_key(name).split()))


def legal_line(name: str) -> str:
    """person/huf/trust — delegates to the #293 classifier (one
    classifier, never two)."""
    from helpers.core.person_names import classify_person_name

    return classify_person_name(name)


# --- person-shape heuristics (the company dedup's S1 filter; the
# person arc's census source). Honorifics, initial runs ("A. M.
# Patel"), and shareholding-holder phrases from the bulk filings.

_PERSON_RE = re.compile(
    r"^(?:(?:mr|mrs|ms|dr|shri|smt|prof|er|ca)\.?\s+)"
    r"|^[a-z](?:\.\s*[a-z]+)+$",  # initial-run shape on the normalised key
    re.I,
)
_PERSON_PHRASE_RE = re.compile(
    r"\b(?:hold(?:s|ing)?\s+shares?|kartha|karta|huf|trustee|proprietor"
    r"|guardian|nominee)\b",
    re.I,
)

# Company-marker vocabulary: vetoes the person shape (2026-10-16 — the
# 4 marker clusters were unlisted subsidiaries). Shared by the person
# arc's review partition.
_COMPANY_MARKER_RE = re.compile(
    r"\b(ltd|limited|inc|llc|llp|gmbh|bv|spa|packers|cements|cement"
    r"|industries|laboratories|labs|services|holdings|enterprises"
    r"|technologies|systems|solutions|infra|infrastructure|logistics"
    r"|pharma|motors|steel|power|energy|capital|finance|financial"
    r"|foundation|associates|traders|exports|retail|hotels)\b",
    re.I,
)

QUOTES_PATH = _REPO_ROOT / "findata" / "Super_Sectors" / "Quotes.md"


def is_person_name(name: str) -> bool:
    """Conservative person-shape test on the RAW name and the normalised
    key. Deliberately over-inclusive in the safe direction: a company
    wrongly excluded (A. O. Smith) just keeps its variants — visible in
    the plan's excluded section — while a person wrongly merged would be
    data damage. Operator ruling 2026-10-16: a COMPANY-MARKER spelling
    (Packers/Cements/Services/…) vetoes the person shape outright — the
    4 marker clusters were unlisted subsidiaries, and the person census
    must not stock them."""
    if _COMPANY_MARKER_RE.search(name):
        return False
    if _PERSON_RE.search(name.strip()):
        return True
    if _PERSON_PHRASE_RE.search(name):
        return True
    return bool(_PERSON_RE.search(name_key(name)))


def speaker_names(path: Path = QUOTES_PATH) -> set[str]:
    """Normalised person names harvested from the Quotes compendium —
    every ``> — <Name>, <Title>`` attribution line is a real person.
    Missing file = empty set (the shape heuristics still hold)."""
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^>\s*—\s*([^,]+),", line.strip())
        if m:
            out.add(name_key(m.group(1)))
    return out


@lru_cache(maxsize=1)
def speaker_display(path: Path = QUOTES_PATH) -> dict[str, str]:
    """name_key → display form of the FIRST Quotes occurrence — the
    stable spelling a speaker's future attributions resolve to.

    Cached (perf gate, derive_insights leg): resolve_speaker consults
    this per Quote attribution; re-walking the whole Quotes corpus per
    call made the leg O(N²) (145 s vs 9 s budget). Static within a
    process."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^>\s*—\s*([^,]+),", line.strip())
        if m:
            out.setdefault(name_key(m.group(1)), m.group(1).strip())
    return out


def resolve_speaker(name: str | None) -> str | None:
    """Canonical speaker name for a Quotes attribution (person arc S6):
    signed person-plan fold → Quotes corpus display form → as-written.
    Purely additive — an unknown name passes through untouched. The
    fold map is person_names.person_fold_registry (the person home —
    one reader per artifact)."""
    if not name or not name.strip():
        return name
    from helpers.core.person_names import person_fold_registry

    key = name_key(FOOTNOTE_RE.sub("", name.strip()))
    hit = person_fold_registry().get(key)
    if hit:
        return hit
    return speaker_display().get(key, name.strip())


_PERSON_PLAN_PATH = _REPO_ROOT / "findata" / "Misc" / "_person_merge_plan.jsonl"


def registry_index(conn) -> dict[str, str]:
    """Census-key → entity name over the ``entities`` registry: the
    resolve-before-insert map. First spelling wins (deterministic via
    the DB order); a signed plan's fold map overrides at the call site."""
    out: dict[str, str] = {}
    for (name,) in conn.execute("SELECT name FROM entities").fetchall():
        if name:
            out.setdefault(name_key(FOOTNOTE_RE.sub("", name.strip())), name)
    return out


def _ledger_folds(ledger_path: Path) -> dict[str, str]:
    """Read the append-only fold LEDGER: variant ``name_key`` → canonical.

    The durable half of the registry. ``name_dedup.apply_plan`` EXTENDS
    this file on every merge; unlike the plan artifact it is never
    regenerated, so a signed fold survives a re-plan that empties the
    plan to headers. Missing ledger = {}."""
    import json

    if not ledger_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        out[name_key(row["variant"])] = row["canonical"]
    return out


def fold_registry(
    plan_path: Path = DEFAULT_PLAN_PATH,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> dict[str, str]:
    """Folded-variant (by name_key) → canonical. Two halves unioned:

    - the append-only LEDGER (durable — ``apply_plan`` extends it, so a
      signed fold keeps routing old spellings to the canonical *forever*,
      which the plan artifact alone cannot promise because ``--plan``
      regenerates it and empties it after every merge);
    - the current PLAN artifact's ``-> fold`` rows (back-compat with a
      plan applied before the ledger existed, and the live source within
      a session that has planned but not yet applied).

    Write paths consult this so a folded spelling never re-mints a
    variant entity. Missing both = {}."""
    folded = _ledger_folds(ledger_path)
    if not plan_path.exists():
        return folded

    from helpers.graph.name_dedup import is_person_name

    current: str | None = None
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        if line.startswith("== "):
            current = line[3:].split("  (")[0].removesuffix(" [listed]")
            if is_person_name(current):  # stale plan: person block → skip
                current = None
        elif line.startswith("   ") and line.endswith("  -> fold") and current:
            variant = line[3:-9]
            if is_person_name(variant):
                continue
            folded[name_key(variant)] = current
    return folded
