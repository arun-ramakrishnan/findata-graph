#!/usr/bin/env python3
"""Extract structured relation edges (`jv_with`, `acquired`, `subsidiary_of`,
`same_group`, `competes_with`) from newsletter source markdown.

CONTEXT
-------
Phase 2 of the graph buildout hand-seeded ~13 cross-company edges. The signal
for many more lives in the prose of `findata/The_Chatter/*.md` (and the other
newsletter inputs). This module mines those newsletters conservatively and
writes verified edges to `graph_edges`, with the source quote attached as
audit trail in `properties.quote`.

SCOPE (initial release)
-----------------------
High-precision edge types only:

  | edge_type      | symmetric? | trigger                                  |
  |----------------|-------------|------------------------------------------|
  | jv_with        | yes         | "joint venture with X", "JV with X"      |
  | acquired       | no          | "acquired X", "X acquired by Y"           |
  | subsidiary_of  | no          | "subsidiary of X", "parent of X"          |
  | same_group     | yes         | "<A>, part of the <G> Group" + "<B>, ..." |
  | supplier_to    | no          | "supplier to/for X", "supplies to X",     |
  |                |             | "securing X orders", "X% of Y's volume"  |
  | customer_of    | no          | "major customers (X, Y, Z)" etc.         |
  | competes_with  | yes         | "peers/competitors like X", "competes    |
  |                |             | with X" (negative-lookahead-guarded)     |

DESIGN PRINCIPLES
-----------------
1. **Two-anchor matches.** Every emitted edge requires BOTH a relation keyword
   AND a successfully resolved target entity (exact or fuzzy match against
   `entities.name`). Anything we cannot resolve is written to the sidecar
   `findata/_pending_relations.txt` for human review.
2. **Speaker-aware speaker filter.** Newsletter concall blocks are
   `"<verbatim quote>"\n## — Hitesh Sethia, ...`. Quotes contain the most
   relation signal but also "we acquired a customer" — fluff. We DON'T filter
   by speaker; we DO attach the source `quote` for human audit.
3. **Idempotent.** Uses `INSERT OR IGNORE` against the existing
   `UNIQUE(source, target, edge_type)` constraint.
4. **Symmetric types** (`jv_with`, `same_group`) canonicalise to
   `source LE target` per `doc/design/graph_design.txt` §4.
5. **Directional types** (`acquired`, `subsidiary_of`) keep the literal
   direction from the source. `acquired`: source acquirer, target target.
   `subsidiary_of`: source subsidiary, target parent.
6. **Always source_ref'd.** Each edge carries `properties.edition`,
   `properties.newsletter`, and `properties.quote` so a human can audit
   false positives.
7. **Per-edition scope.** Caller passes one newsletter at a time so the
   edition reference stays correct.

PUBLIC API
----------
- ``extract_relations(content, edition_title, newsletter_type, resolver)``
  → ``dict[edge_type, list[Edge]]`` plus an unresolved list.
- ``apply_edges(edges, conn, dry_run=True)`` — INSERT OR IGNORE.
- ``write_sidecar(unresolved, path)`` — append-only triage log.

CLI
---
    python3 helpers/graph/extract_relations.py findata/The_Chatter/Foo.md
    python3 helpers/graph/extract_relations.py findata/The_Chatter/Foo.md --apply
    python3 helpers/graph/extract_relations.py findata/The_Chatter/*.md --apply  # batch

See `doc/design/graph_design.txt` §4 for the symmetric-edge convention.
"""

from __future__ import annotations

try:
    from helpers.core.corpus import Corpus  # S1b shared walk

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from functools import cache
from itertools import combinations
from pathlib import Path
from typing import NamedTuple
from collections.abc import Iterable

# Bootstrap so this module is importable both as a script and as a package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.frontmatter import (  # noqa: E402  # after the sys.path bootstrap above
    strip_frontmatter as _strip_yaml_front_matter,
    _FM_RE as _YAML_FRONT_MATTER_RE,
)
from helpers.graph.triage_pending_relations import noise_target  # noqa: E402

# --------------------------------------------------------------------------- #
# Paths                                                                       #
# --------------------------------------------------------------------------- #
NEWSLETTER_DIRS = {
    "The_Chatter": _REPO_ROOT / "findata" / "The_Chatter",
    "Points_And_Figures": _REPO_ROOT / "findata" / "Points_And_Figures",
    "The_PlotLines": _REPO_ROOT / "findata" / "The_PlotLines",
}
SIDECAR_PATH = _REPO_ROOT / "findata" / "_pending_relations.txt"
# Runtime-loaded curated aliases (written by triage_pending_relations
# --apply-decisions): {"<lowercased mention>": "<existing entity name>"}.
# Git-tracked data, NOT code — triage cycles must not require code edits.
ALIAS_OVERRIDES_PATH = _REPO_ROOT / "findata" / "relation_aliases.json"


@cache
def _alias_overrides() -> dict[str, str]:
    """Loaded once per process; absent/unreadable file degrades to {}."""
    try:
        raw = json.loads(ALIAS_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return {
        str(k).lower(): str(v)
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, str) and k and v
    }


def _lookup_alias(lower_key: str) -> str | None:
    """_ALIASES wins for its own keys; the runtime alias file covers the rest."""
    return _ALIASES.get(lower_key) or _alias_overrides().get(lower_key)


# --------------------------------------------------------------------------- #
# Entity resolver                                                             #
# --------------------------------------------------------------------------- #
# Stopwords stripped before tokenising candidate names. Conservative — keep it
# small to avoid eating distinctive tokens.
_STOPWORDS = {
    "ltd",
    "ltd.",
    "limited",
    "pvt",
    "private",
    "company",
    "co",
    "co.",
    "inc",
    "inc.",
    "corp",
    "corporation",
    "the",
    "of",
    "and",
    "&",
}
_GENERIC_WORDS = {
    "bank",
    "financial",
    "finance",
    "services",
    "holdings",
    "industries",
    "capital",
    "group",
    "technologies",
    "technology",
    "energy",
    "power",
    "oil",
    "gas",
    "insurance",
    "auto",
    "motors",
    "chemicals",
    "pharma",
    "steel",
    "cement",
    "realty",
    "retail",
    "enterprises",
}

# Common abbreviation aliases. These are tried before fuzzy resolution so
# that "IOCL" → "Indian Oil Corporation" rather than returning None.
# Hand-curated — add new aliases here as the sidecar surfaces them.
_ALIASES: dict[str, str] = {
    "iocl": "Indian Oil Corporation",
    "bpcl": "Bharat Petroleum Corporation",
    "hpcl": "Hindustan Petroleum Corporation",
    "ongc": "Oil and Natural Gas",
    "gail": "GAIL India",
    "ril": "Reliance Industries",
    "jfs": "Jio Financial Services",
    "tcs": "Tata Consultancy Services",
    "infy": "Infosys",
    "wipro": "Wipro",
    "hcl": "HCL Technologies",
    "tisco": "Tata Steel",
    "vedl": "Vedanta",
    "ntpc": "NTPC",
    "rec": "REC",
    "pfc": "Power Finance Corporation",
    "hindalco": "Hindalco Industries",
    # Brand aliases (added 2026-07-19 from _pending_relations.txt triage).
    "nykaa": "FSN E-Commerce",
    # Historical names (renamed; resolve to current entity).
    "hoechst": "Sanofi",
    "hoechst gmbh": "Sanofi",
    "aventis": "Sanofi",
    "aventis pharma": "Sanofi",
    "sanofi-aventis": "Sanofi",
    # Foreign-listed ADR tickers that show up in prose.
    "deo": "Diageo plc",
    "hln": "Haleon plc",
    "sny": "Sanofi",
    # Brand / single-entity aliases added 2026-07-20 from a one-off
    # _pending_relations.txt triage pass.
    #
    # IMPORTANT: only add aliases whose alias target is the SAME entity
    # that the prose mention refers to. For MNC parents like
    # "Abbott Laboratories", "AstraZeneca Pharmaceuticals AB",
    # "Novartis AG", "Cummins Inc", "GlaxoSmithKline plc" the global
    # parent is a DIFFERENT entity from the Indian subsidiary — do NOT
    # add an alias that collapses the two (would suppress legitimate
    # `subsidiary_of` edges to a future parent stub). Those parents are
    # tracked as stub candidates in doc/improvements/pending_improvs.txt.
    "bata": "Bata India",  # "Bata" alone = Indian entity; "Bata (BN) B.V." is a stub candidate
    "ceat": "CEAT",  # single canonical entity
    "diageo": "Diageo plc",  # entity is the global plc itself
    "fintellix": "Fintellix",  # single canonical entity
    "sagility": "Sagility",  # single canonical entity
    "shigan": "Shigan Quantum Technologies",
    "swaraj": "Swaraj Engines",
    # Tier-2: foreign parents / JV partners whose stubs were added by an
    # earlier stub-batch pass. These appear in prose with trailing filler
    # ("the Volvo Group", "Innoviz requires regulatory...",
    # "Japan's Kubota Corporation") that the fuzzy matcher rejects; the
    # first-token alias lets the resolver peel the brand word.
    "totalenergies": "TotalEnergies SE",
    "volvo": "AB Volvo",
    "innoviz": "Innoviz Technologies",
    "signify": "Signify NV",
    "inovance": "Inovance Technology",
    "jayhawk": "Jayhawk",
    "estec": "ESTEC",
    "fisdom": "Fisdom",
    "vivo": "Vivo Mobile",
    "kubota": "Kubota Corporation",
    "huhtavefa": "Huhtamaki Oyj",
    "tidco": "Tamil Nadu Industrial Development Corporation",
    "npcil": "NPCIL",
    "bpsl": "BPSL",
    "prudential": "Prudential plc",
    # OCR-garbled acquisition-target name (Varun Beverages → Twizza sidecar
    # mention was captured as 'Tiza'). Maps the truncated token to the
    # correct South African beverage co stub.
    "tiza": "Twizza",
    # JV partner abbreviations surfaced by sidecar batch #3 triage.
    "jwil": "JWIL Infra",
    "jnt": "Jinnaite Machinery",
    # Brand / surface-form aliases added 2026-08-03 from the deduped
    # _pending_relations.txt triage (targets already in the KB but the
    # prose used a brand/trade name the fuzzy matcher rejected).
    "royal enfield": "Royal Enfield",
    "ramakrishna": "Ramkrishna Forgings",  # prose misspelling of canonical name
    "ecom express": "Ecom Express",
    "camso": "Camso",
    "3m company": "3M Company",
    "holcim": "Holcim",
    "nerofix": "Nerofix",
    "perma": "Nerofix",  # Kansai Nerolac acquired Nerofix + Perma together
    # Foreign parents / JV partners added 2026-08-04 from the foreign-parent
    # triage set. Surface forms in prose that the fuzzy matcher rejected.
    "kokusan denki": "Kokusan Denki",
    "heidelberg materials": "Heidelberg Materials",
    "heidelbergmaterial": "Heidelberg Materials",  # post-2023 rebrand spelling
    "hyundai motor company": "Hyundai Motor Company",
    "procter & gamble": "Procter & Gamble Company",
    "procter gamble": "Procter & Gamble Company",  # stripped-punctuation form
    "p&g": "Procter & Gamble Company",
    "samsung group": "Samsung Group",
    "totally foxed solutions": "Totally Foxed Solutions",
    "brooks": "Brooks",  # OneSource JV partner (Baroda biologics)
}


# --------------------------------------------------------------------------- #
# S4 hoisted patterns (2026-09-01): these ran as inline re.sub/re.split/     #
# re.match/re.search calls in hot loops (per mention / heading / chunk);     #
# module-level compilation mirrors the file's own convention (GROUP_RE &     #
# friends). Pattern-for-pattern moves — no semantics changes.                #
# --------------------------------------------------------------------------- #
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9 ]")
_LEADING_ARTICLE_POSS_RE = re.compile(
    r"^(?:the|a|an|sweden'?s|japan'?s|germany'?s|korea'?s|france'?s|usa'?s|u\.s\.'?s|china'?s|uk'?s|india'?s)\s+",
    re.IGNORECASE,
)
_MENTION_FIRST_TOKEN_RE = re.compile(r"[\s,/]+")
_TRAILING_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(Limited|Ltd\.?|Private|Pvt\.?|Company|Co\.?)$",
    re.IGNORECASE,
)
_GROUP_NAME_SUFFIX_RE = re.compile(
    r"\s+(?:Corp(?:oration)?|Group|Holdings|Industries)$",
    re.IGNORECASE,
)
_LEADING_ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_HEADING_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(Limited|Ltd\.?|Private|Pvt\.?)$",
    re.IGNORECASE,
)
_HEADING_START_RE = re.compile(r"^[A-Z0-9]")
_SPEAKER_ROLE_RE = re.compile(
    r"\b(managing director|chief executive|ceo|cfo|coo|cto|cio|md|director|"
    r"chairman|president|head of|founder|co-founder)\b",
    re.IGNORECASE,
)
_H1_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_TRAILING_JUNK_RE = re.compile(
    r"\s+(?:the|a|an|its|their|our|in|on|at|to|for|and|or)$",
    re.IGNORECASE,
)
_LIST_CHUNK_SPLIT_RE = re.compile(
    r"\s*,\s*|\s+\band\b\s+|\s+\bor\b\s+|\s+&\s+",
    re.IGNORECASE,
)
_LEADING_CONJ_RE = re.compile(r"^(?:and|or|&)\s+", re.IGNORECASE)
_TRAILING_NUM_UNIT_RE = re.compile(r"\s+\d+(?:\.\d+)?\s+\w+(?:\s+\w+){0,2}$")
_WS_RUN_RE = re.compile(r"\s+")


@cache
def _tokens(name: str) -> frozenset[str]:
    # Memoized: extract_relations dry-run calls _tokens 600k+ times across
    # ~1200 unique strings (entity names + prose mentions). The cache collapses
    # that to ~1200 calls. Returns frozenset (hashable) so callers can use it
    # in set operations and as a dict key.
    return frozenset(
        t for t in _NON_ALNUM_SPACE_RE.sub(" ", name.lower()).split() if t and t not in _STOPWORDS
    )


class EntityResolver:
    """Resolve a free-text mention to a canonical `entities.name`.

    Strategy (in order):
      1. Exact case-insensitive match.
      2. Suffix-stripped match (drop Ltd/Limited/Company/Private).
      3. Conservative fuzzy: >=2 distinctive (non-generic) tokens shared,
         one side's distinctive-token set ⊆ the other.

    Returns the resolved name, or None if no confident match.
    """

    def __init__(self, names: Iterable[str]):
        # All names + case-folded index for O(1) exact lookup.
        self._names: list[str] = list(names)
        self._by_lower: dict[str, str] = {n.lower(): n for n in self._names}
        # Pre-tokenized entity names (avoids re-tokenizing in the _fuzzy loop).
        self._name_tokens: list[tuple[str, frozenset]] = [(n, _tokens(n)) for n in self._names]
        # Reverse index: single token → names containing it. Lets _fuzzy find
        # candidate entities sharing ≥1 token in O(candidates) instead of
        # scanning all N entities per resolve() call.
        self._by_token: dict[str, list[str]] = {}
        for n, toks in self._name_tokens:
            for t in toks:
                self._by_token.setdefault(t, []).append(n)
        # E1 (2026-08-23): ambiguity audit log. When the fuzzy matcher finds
        # MULTIPLE candidates tied at the top score, the first wins silently
        # today; we record those ties here so runs can surface them as Tier-C
        # report lines ("mention X matched N candidates equally well"). Never
        # consulted for resolution — logging only.
        self.ambiguous_log: list[tuple[str, list[str]]] = []

    def resolve(self, mention: str) -> str | None:
        if not mention:
            return None
        m = mention.strip().rstrip(".,;:")
        if not m:
            return None
        # 0a. Alias lookup on the whole mention (IOCL → Indian Oil Corp).
        # Aliased names return in DB-canonical casing (check + return both
        # go through _by_lower), so alias-file entries with drifted casing
        # still resolve instead of silently no-oping.
        aliased = _lookup_alias(m.lower())
        if aliased and aliased.lower() in self._by_lower:
            return self._by_lower[aliased.lower()]
        # 0b. Alias lookup on the first token. MNC parent mentions in prose
        # often read "<brand> Laboratories" / "<brand> Inc" / "<brand> AG",
        # and the brand token alone maps to the Indian-listed subsidiary.
        # The alias must point at an entity that exists in the DB.
        #
        # Strip leading articles and possessives first: "the Volvo Group",
        # "Sweden's AB Volvo", "Japan's Kubota Corporation" should all
        # resolve via the brand token.
        stripped_m = _LEADING_ARTICLE_POSS_RE.sub("", m)
        first = _MENTION_FIRST_TOKEN_RE.split(stripped_m, maxsplit=1)[0].lower()
        if first and first != m.lower():
            aliased_first = _lookup_alias(first)
            if aliased_first and aliased_first.lower() in self._by_lower:
                return self._by_lower[aliased_first.lower()]
        # 1. Exact case-insensitive.
        if m.lower() in self._by_lower:
            return self._by_lower[m.lower()]
        # 2. Suffix-stripped exact.
        stripped = _TRAILING_LEGAL_SUFFIX_RE.sub("", m).strip()
        if stripped.lower() in self._by_lower:
            return self._by_lower[stripped.lower()]
        # 3. Fuzzy.
        return self._fuzzy(m)

    def _fuzzy(self, mention: str) -> str | None:  # noqa: C901
        ct = _tokens(mention)
        if not ct:
            return None
        # Single distinctive token: look for an entity with that token as
        # its ONLY distinctive token (e.g. "BlackRock" -> "BlackRock").
        if len(ct) == 1:
            tok = next(iter(ct))
            for n, et in self._name_tokens:
                if len(et) == 1 and tok in et:
                    return n
            return None
        # Multi-token: need subset match on distinctive tokens. Use the reverse
        # token index to gather only candidates sharing ≥1 token, then score.
        # The old loop scanned all N entities and re-tokenized each on every
        # call; this is O(candidates) with pre-tokenized sets.
        distinctive = ct - _GENERIC_WORDS
        if not distinctive:
            return None  # mention is all generic words; too ambiguous
        # Gather candidate names that share at least one token with the mention.
        candidates: set[str] = set()
        for t in ct:
            candidates.update(self._by_token.get(t, ()))
        best = None
        best_score = 0
        tied: list[str] = []
        for n in candidates:
            et = _tokens(n)
            if not et:
                continue
            shared = ct & et
            if len(shared) >= 2 and (shared == ct or shared == et):
                if shared - _GENERIC_WORDS:
                    score = len(shared)
                    if score > best_score:
                        best_score = score
                        best = n
                        tied = [n]
                    elif score == best_score and n != best:
                        tied.append(n)
        if best is not None and len(tied) > 1:
            self.ambiguous_log.append((mention, sorted(tied)))
        return best


# --------------------------------------------------------------------------- #
# Pattern library                                                             #
# --------------------------------------------------------------------------- #
@dataclass
class Edge:
    source: str
    target: str
    edge_type: str
    properties: dict = field(default_factory=dict)
    source_ref: str = ""
    symmetric: bool = False
    # Optional ISO date (YYYY-MM-DD) for the edge. Populated for `acquired`
    # edges from `_extract_year_from_context`; also populated by the
    # `backfill_valid_from` maintenance script when a `properties.since`
    # value is present on seed/manual edges. Stored as the `valid_from`
    # column at INSERT time. None when no temporal signal is available.
    valid_from: str | None = None


# Edge types for which `_extract_year_from_context` is invoked at extraction
# time. Today only `acquired` is included: a manual audit (2026-07) of the
# 79 `subsidiary_of`/`jv_with` quotes found that applying the helper to
# non-`acquired` prose produces ~80% false positives (financial-statement
# "as of" dates, rename events, cross-sentence bleed). The backfill script
# can still promote explicit `properties.since` values on any edge type;
# this set only governs automatic prose-mining at extraction time.
_EDGE_TYPES_WITH_PROSE_YEAR_EXTRACTION: frozenset[str] = frozenset({"acquired"})


@dataclass
class Unresolved:
    """A pattern match whose target entity couldn't be resolved.

    Written to the sidecar `findata/_pending_relations.txt` for human review.
    `direction` carries the pattern's forward/reverse flag so a later
    `accept:` triage decision can write the edge in the same orientation the
    extractor would have (reverse captures make the MENTION the source).
    """

    edge_type: str
    source: str
    target_mention: str
    quote: str
    edition: str
    direction: str = "forward"


# Relation patterns. Each pattern has:
#   - regex with at least one capture group: the target MENTION (free text).
#   - edge_type, symmetric, and a "direction" key ('forward' or 'reverse').
#     'forward' means source = the section's company, target = the captured
#     mention. 'reverse' means source = the captured mention, target = the
#     section's company.
#
# We compile with re.IGNORECASE so we catch "JV", "jv", "Joint venture" etc.
# Each pattern matches a SHORT window around the keyword (max ~80 chars) so
# the resolver has tight context.

# (regex, edge_type, symmetric, direction)
# Each pattern captures a target MENTION in group 1. The mention is then
# resolved via EntityResolver; unresolved mentions go to the sidecar.
# We allow the capture to end at any of: , ; . : ( ) / " ' newline, or the
# end of the line. We also accept a small set of common trailing stop-words
# so we can peel them back in post-processing.
PATTERNS: list[tuple[re.Pattern, str, bool, str]] = [
    # --- jv_with (symmetric) ---
    (
        re.compile(
            r"\bjoint\s+venture\s+"
            r"(?:with\s+|between\s+(?:[A-Z][\w&.\-]+\s+(?:and\s+)?)*?"
            r"|of\s+ours\s+along\s+with\s+)"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:to|for|in|and\s+|which|that|is|has|will)\s|$))",
            re.IGNORECASE,
        ),
        "jv_with",
        True,
        "forward",
    ),
    (
        re.compile(
            r"\bJV\s+(?:with\s+|between\s+)"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:to|for|in|and\s+|which|that|is|has|will)\s|$))",
            re.IGNORECASE,
        ),
        "jv_with",
        True,
        "forward",
    ),
    # G1.1 (2026-08): JV synonyms — common Indian-English / corporate forms that
    # the literal "joint venture with" / "JV with" patterns above miss. Maps to
    # the same symmetric jv_with edge; flows through the same resolution +
    # generic-target + suppression path. "partnership with" is the most frequent
    # in the corpus (~40 hits in The_Chatter); "tie-up with" is the IE idiom.
    (
        re.compile(
            r"\b(?:tie[\-\s]?up(?:ped|ping)?\s+with"
            r"|in\s+partnership\s+with|partnership\s+with"
            r"|strategic\s+alliance\s+with|alliance\s+with)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:to|for|in|and\s+|which|that|is|has|will)\s|$))",
            re.IGNORECASE,
        ),
        "jv_with",
        True,
        "forward",
    ),
    # --- acquired (asymmetric) ---
    # NOTE: the forward pattern explicitly excludes `by|from` after `acquired`
    # so it doesn't fire on `acquired by X` (the reverse pattern handles that).
    (
        re.compile(
            r"\b(?:recently\s+|newly\s+)?acquired\s+"
            r"(?!by\s|from\s)"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will)\s|$))",
            re.IGNORECASE,
        ),
        "acquired",
        False,
        "forward",
    ),
    (
        re.compile(
            r"\bacquired\s+(?:by|from)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will)\s|$))",
            re.IGNORECASE,
        ),
        "acquired",
        False,
        "reverse",
    ),
    (
        re.compile(
            r"\bacquisition\s+of\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will)\s|$))",
            re.IGNORECASE,
        ),
        "acquired",
        False,
        "forward",
    ),
    # H1 (2026-07-28): additional `acquired` REVERSE forms for corporate
    # actions that the forward "acquired X" / reverse "acquired by X" patterns
    # don't cover. All map to `acquired` (the graph conflates M&A events under
    # this type) with direction='reverse' so the named counterpart becomes the
    # source (the acquirer / continuing entity) and the section's company is
    # the target. Measured yield on the live corpus: ~3 new edges, all high-
    # precision (the verbs are unambiguous M&A language).
    #   - "demerged from X"      (section company was spun off FROM X)
    #   - "merged with X"        (section company merged INTO X)
    #   - "formed through the merger of X and Y"  (section company IS the
    #     merged entity; captures the first named constituent as source)
    (
        re.compile(
            r"\bdemerged?\s+from\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will)\s|$))",
            re.IGNORECASE,
        ),
        "acquired",
        False,
        "reverse",
    ),
    (
        re.compile(
            r"\b(?:merged?\s+with"
            r"|formed\s+through\s+(?:the\s+)?merger\s+(?:of|with))\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will|and)\s|$))",
            re.IGNORECASE,
        ),
        "acquired",
        False,
        "reverse",
    ),
    # --- subsidiary_of (asymmetric, source subsidiary, target parent) ---
    (
        re.compile(
            r"\b(?:a\s+)?(?:wholly[\-\s]owned\s+(?:step[\-\s]down\s+)?"
            r"|majority[\-\s]owned\s+|step[\-\s]down\s+|100%\s+)?"
            r"subsidiary\s+of\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:is|which|that|operates|it|has|with)\s|$))",
            re.IGNORECASE,
        ),
        "subsidiary_of",
        False,
        "forward",
    ),
    (
        re.compile(
            r"\bparent\s+company\s+of\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:is|which|that|operates|it|has|with)\s|$))",
            re.IGNORECASE,
        ),
        "subsidiary_of",
        False,
        "reverse",
    ),
    # H1 (2026-07-28): reverse subsidiary_of for the "listed subsidiary is X"
    # / "its Indian subsidiary is X" form — the section's company is the
    # PARENT and X is its (named) subsidiary. Deliberately NARROW: requires an
    # explicit copula anchor (is/named/called) OR the "listed subsidiary"
    # qualifier. The bare "subsidiary <Name>" noun-adjunct form was measured
    # at ~92% false positives ("subsidiary company", "subsidiary of Finland",
    # "subsidiary NRB Thailand serves...") and is NOT matched here.
    (
        re.compile(
            r"\b(?:listed\s+subsidiary"
            r"|(?:indian|overseas|wholly[\-\s]owned)\s+subsidiary)\s+"
            r"(?:is\s+|named\s+|called\s+)?"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:is|which|that|operates|it|has|with|a\s)\s|$))",
            re.IGNORECASE,
        ),
        "subsidiary_of",
        False,
        "reverse",
    ),
    # --- supplier_to (asymmetric: source = supplier, target = customer) ---
    # High-precision only — captured target must be a named entity.
    # General prose like "supplier to OEMs" / "supplier to the industry" is
    # rejected by the resolver (no matching entity).
    (
        re.compile(
            r"\b(?:key|leading|major|primary|exclusive|strategic|global)?\s*"
            r"supplier\s+(?:of\s+[A-Za-z0-9\s\-,]{1,60}?\s+)?"
            r"(?:to|for)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will|because|since|and\s)\s|$))",
            re.IGNORECASE,
        ),
        "supplier_to",
        False,
        "forward",
    ),
    (
        re.compile(
            r"\bsuppl(?:ies|ying|ied)\s+(?:[A-Za-z0-9\s\-,]{1,60}?\s+)?to\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will|because|since|and\s)\s|$))",
            re.IGNORECASE,
        ),
        "supplier_to",
        False,
        "forward",
    ),
    # "securing <Company> orders" / "won <Company> contract"
    # NOTE: no re.IGNORECASE here — the [A-Z] anchor must stay case-sensitive
    # so we capture the proper-noun Company rather than lowercase words.
    (
        re.compile(
            r"\b(?:securing|secured|won|winning|awarded)\s+"
            r"([A-Z][A-Za-z0-9&.\-\s]{1,60}?)\s+"
            r"(?:orders|contract|contracts|business|deal|RFQ|rfq)\b"
        ),
        "supplier_to",
        False,
        "forward",
    ),
    # "providing X% of <Company>'s volume/demand/supply" — strong signal
    # that source supplies to <Company>.
    (
        re.compile(
            r"\bprovid(?:e|es|ed|ing)\s+(?:about\s+|around\s+|nearly\s+|over\s+)?"
            r"(\d{1,3})\s*%\s+of\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?:'s|\u2019s|\s+volume|\s+demand|\s+supply|\s+requirements)?"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:for|since|because|and\s|which|that)\s|$))",
            re.IGNORECASE,
        ),
        "supplier_to",
        False,
        "forward",
    ),
    # G1.3 (2026-08): "vendor to/for X" — synonym for "supplier to" (forward).
    # Same shape as the first supplier_to pattern above; flows through the same
    # _GENERIC_SUPPLIER_TARGETS filter.
    (
        re.compile(
            r"\b(?:key|leading|major|primary|exclusive|strategic)?\s*"
            r"vendor\s+(?:of\s+[A-Za-z0-9\s\-,]{1,60}?\s+)?"
            r"(?:to|for)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will|because|since|and\s)\s|$))",
            re.IGNORECASE,
        ),
        "supplier_to",
        False,
        "forward",
    ),
    # G1.3 (2026-08): "sources/sourcing/procures from X" — REVERSE supplier_to.
    # The named party X is the SUPPLIER; the section's company is the CUSTOMER.
    # direction='reverse' so the resolver swaps source/target: the captured X
    # becomes the source (supplier), the section company becomes the target.
    (
        re.compile(
            r"\b(?:sources?|sourcing|procures?|procuring)\s+from\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|last|this|which|that|is|has|will|because|since|and\s)\s|$))",
            re.IGNORECASE,
        ),
        "supplier_to",
        False,
        "reverse",
    ),
    # --- customer_of (asymmetric: source = customer, target = supplier) ---
    # "<Company>'s largest customer is <X>" → X supplies to Company.
    # "major customers (X, Y, Z)" → listed entities are customers of source.
    (
        re.compile(
            r"\b(?:major|largest|key|biggest|primary)\s+customers?\s+"
            r"(?:\(([^)]{1,200}?)\)\s*(?:easily|also|in|during|have|has|are|is|currently)?"
            r"|\s+(?:is|are)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?))"
            r"(?=\s*(?:[,.;:!?\n/\"']|\s+(?:in|for|during|last|this|which|that|is|has|have|will|because|since|and|but|while|when|to|from|with|easily|also)\s|$))",
            re.IGNORECASE,
        ),
        "customer_of",
        False,
        "forward",
    ),
    # --- competes_with (symmetric) ---
    # High-precision patterns only. The bare "competes with X" / "competition
    # from X" forms are ~95% generic noise ("peers", "OEMs", "Chinese imports"),
    # so we anchor on (a) explicit named-list forms ("peers like X", "competitors
    # such as X", "rivals including X") and (b) bare "competes with X" guarded
    # by a negative lookahead that kills generics.
    #
    # Pattern A captures the WHOLE list span (terminated by sentence punctuation
    # only — not by "," or " and "). The comma-and-conjunction fallback in
    # extract_relations() then splits the span and resolves each chunk; this is
    # what lets "peers like Tata Motors, Ashok Leyland, and Eicher" yield three
    # source<->target edges rather than one truncated mess.
    #
    # A negative lookbehind rejects SECTOR-GROUPING contexts ("alongside
    # peers like X") which describe sector classification, not direct
    # competition. The other grouping phrases ("grouped with peers such as",
    # "mentioned alongside peers like") are filtered in extract_relations()
    # via _COMPETES_GROUPING_PREFIXES because Python re lacks variable-width
    # lookbehinds. Empirically these grouping contexts are the main FP source
    # for this pattern (e.g. "Bharti Airtel grouped with peers such as Pace
    # Digitek" — Pace Digitek is an EMS manufacturer, not a telecom peer).
    (
        re.compile(
            r"(?<!alongside )"
            r"\b(?:peers|competitors|rivals|challengers)\s+"
            r"(?:like|such\s+as|including|named|namely)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.,'\-\s]{1,120}?)"
            r"(?=\s*(?:[.;:()\n\"']|\s+(?:which|that|is|has|will|but\s|while\s|when\s|because|since)\s|$))",
            re.IGNORECASE,
        ),
        "competes_with",
        True,
        "forward",
    ),
    # Pattern B — bare "competes with X" / "rival X" with a negative lookahead
    # that rejects the generic noise which got competes_with deferred in v1.
    # Single-target capture (no list semantics); terminator stays tight.
    (
        re.compile(
            r"\b(?:competes\s+with|rival(?:s|ry)?)\s+"
            r"(?!peers|rivals|competitors|chinese|indian|european|american|japanese|korean|other|many|some|several|various|both|all|its|the\s|a\s|an\s)"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:in|for|on|at|last|this|which|that|is|has|will|and\s|but\s|while\s|when\s|because|since)\s|$))",
            re.IGNORECASE,
        ),
        "competes_with",
        True,
        "forward",
    ),
    # G2 (2026-08-23): stake-percentage family — Indian-market ownership
    # phrasing. These patterns capture TWO groups: group(1) = percentage,
    # group(2) = target mention. extract_relations() detects them by checking
    # whether group(1) is numeric and records properties.stake_pct.
    #
    # "acquisition of 26% stake in X" / "picked up a 51% equity stake in X"
    # → `acquired` (the graph conflates M&A under this type). The bare
    # "acquisition of X" pattern above cannot fire on these because its
    # capture demands an uppercase first char immediately after "of".
    (
        re.compile(
            r"\bacquisition\s+of\s+(?:a\s+)?"
            r"(?:about\s+|around\s+|nearly\s+|over\s+)?"
            r"(\d{1,3}(?:\.\d+)?)\s*%\s+"
            r"(?:stake|equity(?:\s+stake)?|shareholding|holding|shares?|interest)"
            r"\s+(?:in|of)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:from|for|last|this|which|that|is|has|will|and\s)\s|$))",
            re.IGNORECASE,
        ),
        "acquired",
        False,
        "forward",
    ),
    (
        re.compile(
            r"\b(?:acquired|purchased|bought|picked\s+up)\s+(?:a\s+)?"
            r"(?:about\s+|around\s+|nearly\s+|over\s+)?"
            r"(\d{1,3}(?:\.\d+)?)\s*%\s+"
            r"(?:stake|equity(?:\s+stake)?|shareholding|holding|shares?|interest)"
            r"\s+(?:in|of)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:from|for|last|this|which|that|is|has|will|and\s)\s|$))",
            re.IGNORECASE,
        ),
        "acquired",
        False,
        "forward",
    ),
    # "holds/owns N% stake in X" is ongoing OWNERSHIP, not an acquisition
    # event. At >=50% it implies control, so we emit subsidiary_of with
    # direction='reverse' (captured X becomes the subsidiary source, the
    # section's company the parent). Below 50% the relation is a passive
    # holding — dropped silently rather than mislabeled (Tier C: precision-
    # first; the drop is documented here, not sidecarred).
    (
        re.compile(
            r"\b(?:holds?|held|owns?|owned)\s+(?:a\s+)?"
            r"(?:about\s+|around\s+|nearly\s+|over\s+)?"
            r"(\d{1,3}(?:\.\d+)?)\s*%\s+"
            r"(?:stake|equity(?:\s+stake)?|shareholding|holding|interest)"
            r"\s+(?:in|of)\s+"
            r"((?-i:[A-Z])[A-Za-z0-9&.\-\s]{1,60}?)"
            r"(?=\s*(?:[,.;:()\n/\"']|\s+(?:via|through|which|that|is|has|will|and\s)\s|$))",
            re.IGNORECASE,
        ),
        "subsidiary_of",
        False,
        "reverse",
    ),
]

# Group-name patterns — used to derive same_group edges across multiple
# companies in the same newsletter / vault that share a group. Each captures
# the GROUP NAME; resolution happens at the batch level (not via
# EntityResolver). Group names are conglomerates, not companies.
#
# GROUP_RE is the original v1 form. The G2 additions (2026-08-23) cover the
# common Indian-market phrasings that v1 missed:
#   - "<X> promoter group"          (promoter-group membership)
#   - "flagship [company] of the <X> Group" / "<X> Group flagship"
#   - "<X> Group company/firm/entity"
GROUP_RE = re.compile(
    r"\bpart\s+of\s+(?:the\s+)?([A-Z][A-Za-z0-9&.\-\s]{2,50}?)\s+Group\b",
)
_PROMOTER_GROUP_RE = re.compile(
    r"\bpart\s+of\s+(?:the\s+)?([A-Z][A-Za-z0-9&.\-\s]{2,50}?)"
    r"\s+promoter\s+group\b",
)
_FLAGSHIP_OF_GROUP_RE = re.compile(
    r"\bflagship(?:\s+(?:company|entity))?\s+of\s+(?:the\s+)?"
    r"([A-Z][A-Za-z0-9&.\-\s]{2,50}?)\s+Group\b",
)
_GROUP_FLAGSHIP_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&.\-\s]{2,40}?)\s+Group\s+flagship\b",
)
_GROUP_COMPANY_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]+){0,4}?)"
    r"\s+Group\s+(?:compan(?:y|ies)|firm|entities?)\b",
)

GROUP_RES: list[re.Pattern] = [
    GROUP_RE,
    _PROMOTER_GROUP_RE,
    _FLAGSHIP_OF_GROUP_RE,
    _GROUP_FLAGSHIP_RE,
    _GROUP_COMPANY_RE,
]


def _normalize_group_name(raw: str) -> str:
    """Normalize a captured group name.

    Strips (a) trailing corporate suffixes swept into the capture
    ("Tata Group Corp" -> "Tata") and (b) leading articles picked up when
    the phrase sits at sentence start ("A Mahindra Group company" must key
    as "Mahindra", matching the "flagship of the Mahindra Group" form).
    """
    name = _GROUP_NAME_SUFFIX_RE.sub("", raw.strip()).strip()
    name = _LEADING_ARTICLE_RE.sub("", name).strip()
    return name


# --------------------------------------------------------------------------- #
# Newsletter → section splits                                                 #
# --------------------------------------------------------------------------- #
# Split a newsletter .md into (company_heading, body) chunks.
# A *company heading* is a level-1/2/3 heading that names a real company
# (pipe-separated cap info, OR no cap info but the resolver recognises it).
# Sub-headings inside a company section (`## [Concall]`, `## Speaker Name`,
# `## — Speaker, Title`) are NOT section boundaries — they're absorbed into
# the previous company section.
#
# We do a two-pass split:
#   Pass 1: find ALL heading lines (any level).
#   Pass 2: classify each as COMPANY or SUB-SECTION. Company section bodies
#           extend from the company heading to the NEXT company heading.
SECTION_HEADING_RE = re.compile(
    r"^\s*#{1,3}\s+(.+?)(?:\s*[|·].*|\s+(?:large|larg|mid|small|micro|nano|mega)\s*cap.*)?$",
    re.MULTILINE,
)

# Strong company-heading indicator: pipe-separated metadata, OR an explicit
# cap token in the heading text. Without either of these we still accept
# if the resolver can resolve the heading — that's decided at split time.
_PIPE_SEP_RE = re.compile(r"\s\|\s", re.MULTILINE)
_CAP_TOKEN_RE = re.compile(
    r"\b(?:large|larg|mid|small|micro|nano|mega)\s*cap\b",
    re.IGNORECASE,
)


def _split_sections(content: str) -> list[tuple[str, int, int, int]]:  # noqa: C901
    """Split a newsletter into (heading_name, heading_start_pos, body_start_pos, body_end_pos) sections.

    Only *company* sections are returned; sub-headings within a company
    section (e.g. `## [Concall]`, `## Speaker, Title`) are absorbed into
    the enclosing company's body. A heading is treated as a *company*
    heading only if it has a pipe separator (`## Foo | Large Cap | Sector`)
    OR an inline cap token (`## Foo Mid Cap Sector`). The caller additionally
    filters out headings that don't resolve to a known entity.

    Returns 4-tuples in document order:
      - heading_name: candidate company name (legal suffix stripped).
      - heading_start_pos: char offset of the heading line (the `##` prefix).
      - body_start_pos: char offset of the first char AFTER the heading line.
      - body_end_pos: char offset of the next COMPANY heading (or EOF).
    """
    # All heading candidates in document order.
    raw_matches: list[tuple[int, int, str]] = []  # (heading_start, heading_end, text)
    for m in SECTION_HEADING_RE.finditer(content):
        heading_text = m.group(1).split("|")[0].strip().rstrip("-·")
        heading_text = _HEADING_LEGAL_SUFFIX_RE.sub("", heading_text).strip()
        # Strip surrounding [] brackets (e.g. "[Concall]").
        heading_text = heading_text.strip("[]")
        if not heading_text or len(heading_text) < 3:
            continue
        if not _HEADING_START_RE.match(heading_text):
            continue
        if heading_text.lower() in _NEWSLETTER_CHROME:
            continue
        if _looks_like_speaker(heading_text):
            continue
        raw_matches.append((m.start(), m.end(), heading_text))

    # Classify each heading as company / sub-section. Only pipe-separated
    # or cap-token-bearing headings are company sections.
    company_indices: list[int] = []
    for i, (hs, _he, _text) in enumerate(raw_matches):
        # m.start() can land on the leading '\n' (because SECTION_HEADING_RE
        # uses ^...$ with re.MULTILINE and the '\n' is part of the match).
        # Advance past any whitespace to find the first '#' of the heading.
        line_start = hs
        while line_start < len(content) and content[line_start] in " \t\n":
            line_start += 1
        line_end = content.find("\n", line_start)
        line = content[line_start : line_end if line_end != -1 else len(content)]
        if _PIPE_SEP_RE.search(line) or _CAP_TOKEN_RE.search(line):
            company_indices.append(i)

    sections: list[tuple[str, int, int, int]] = []
    for ci, idx in enumerate(company_indices):
        heading_start, heading_end, heading_text = raw_matches[idx]
        nl_pos = content.find("\n", heading_end)
        body_start = (nl_pos + 1) if nl_pos != -1 else heading_end
        if ci + 1 < len(company_indices):
            body_end = raw_matches[company_indices[ci + 1]][0]
        else:
            body_end = len(content)
        sections.append((heading_text, heading_start, body_start, body_end))
    return sections


def _looks_like_speaker(heading: str) -> bool:
    """Heuristic: 'First Last, Title' headings are speaker attributions.

    Examples we want to filter:
      'Hitesh Sethia, Managing Director and Chief Executive Officer'
      'Vineet Agrawal, Group CFO'
      'Gaurav Seth, CEO'
      '— Karan Bhagat, MD and CEO'
    """
    if "," not in heading:
        return False
    return bool(_SPEAKER_ROLE_RE.search(heading))


# Heading texts that are newsletter chrome, not companies.
_NEWSLETTER_CHROME = {
    "concall",
    "reference",
    "key takeaways",
    "key takeaway",
    "summary",
    "overview",
    "the chatter",
    "points & figures",
    "the plotlines",
    "introduction",
    "conclusion",
    "all editions",
    "disclaimer",
    "the chatter —",
    "management commentary",
    "outlook",
    "earnings call",
}

# Generic noun-phrase targets that the `acquired` pattern matches too eagerly.
# These are signals like "acquired land" / "acquired customers" that aren't
# real acquisition targets. Anything matching one of these (case-insensitive,
# as a substring) is dropped to the sidecar with a `reason:generic_target` flag.
_GENERIC_ACquired_TARGETS = (
    "land",
    "property",
    "assets",
    "customers",
    "clients",
    "shares",
    "stake",
    "businesses",
    "companies",
    "brands",
    "portfolios",
    "portfolio",
    "new business",
    "recently was at",
    "aviation assets",
    "whisky brand",
    "digital brands",
    "from standard",
    "last year",
    "this year",
    "this quarter",
    " tanfac ",  # surrounded by spaces to avoid matching real "Tanfac Ltd"
)

# Generic targets for supplier_to / customer_of patterns. These are the
# words that follow "supplier to" when the target is NOT a named company:
# "supplier to OEMs", "supplier to the automotive industry", etc.
# Anything matching (case-insensitive, exact match after stripping articles)
# is rejected before resolution.
_GENERIC_SUPPLIER_TARGETS = {
    "oems",
    "oem",
    "the industry",
    "the market",
    "industry",
    "market",
    "customers",
    "clients",
    "consumers",
    "the government",
    "government",
    "the public sector",
    "public sector",
    "the private sector",
    "private sector",
    "automakers",
    "auto makers",
    "the company",
    "its customers",
    "its clients",
    "retailers",
    "wholesalers",
    "distributors",
    "dealers",
    "the indian market",
    "the indian industry",
    "india",
    "us",
    "usa",
    "uk",
    "europe",
    "china",
    "asia",
    "the world",
    "global markets",
    "the region",
    "original equipment manufacturers",
}

# Generic targets for competes_with. The bare "competes with X" / "competition
# from X" / "rivals X" forms are ~95% generic noise (e.g. "competes with peers",
# "competition from Chinese imports", "rivals exiting"). The patterns below
# anchor on a capitalised [A-Z] target, so most generics are already rejected,
# but capitalized nationalities / category nouns mid-sentence ("Chinese",
# "Indian", "OEMs", "Peers") slip through and land here. Anything matching
# (case-insensitive, exact OR first-word) is dropped before resolution —
# NOT sidecarred, because the prose carried no resolvable entity.
_GENERIC_COMPETITOR_TARGETS = {
    "peers",
    "rivals",
    "competitors",
    "competition",
    "incumbents",
    "players",
    "challengers",
    "entrants",
    "newcomers",
    "startups",
    "chinese",
    "indian",
    "european",
    "american",
    "japanese",
    "korean",
    "global",
    "domestic",
    "foreign",
    "multinational",
    "oems",
    "the industry",
    "the market",
    "others",
    "many",
    "some",
    "several",
    "various",
    "both",
    "all",
}

# Phrase prefixes that signal SECTOR GROUPING rather than direct competition.
# When the window preceding a competes_with trigger contains one of these,
# the match is rejected (silently — no sidecar; the prose named no direct
# competitor). Examples caught in dry-run triage:
#   "Bharti Airtel ... grouped with peers such as Pace Digitek"
#   "Arman Financial ... alongside peers like SBI Cards"
# Python re lacks variable-width lookbehinds, so we check by scanning a
# small window ending at the match start. The prefixes below are the words
# that IMMEDIATELY PRECEDE "peers/competitors/rivals" — note they don't
# include the trigger word itself, since the window ends right before it.
_COMPETES_GROUPING_PREFIXES = (
    "alongside",
    "grouped with",
    "grouped alongside",
    "mentioned alongside",
    "mentioned with",
    "classified with",
    "classified alongside",
    "bucketed with",
    "listed alongside",
    "listed with",
    "featured alongside",
    "featured with",
    "sector alongside",
)

# Suppressed (source, target, edge_type) triples. The extractor will refuse to
# persist these even when a pattern match fires. Use this when:
#   - the prose is in company X's note but the actual subject is company Y
#     (a sister / group company), and we have a separately-stubbed entity for Y.
#   - the edge has been hand-corrected to a different source via
#     source_ref='manual:attribution_fix'.
# Each suppression is documented with the reason.
_SUPPRESSED_EDGES: set[tuple[str, str, str]] = {
    # JSW Cement's investor deck and JSW Steel's note both mention the JSW
    # Paints acquisition of Akzo Nobel India. We have a stub for JSW Paints
    # and a manually-corrected edge JSW Paints → Akzo Nobel India; suppress
    # the mis-attributed variants so re-runs don't re-add them.
    ("JSW Cement", "Akzo Nobel India", "acquired"),
    ("JSW Steel", "Akzo Nobel India", "acquired"),
    # Newsletter section attribution errors: a Forbes Enviro Solutions
    # section was mis-attributed to Eureka Forbes (sister-entity mention
    # leaked across a section boundary in Between_the_Numbers.md).
    ("Eureka Forbes", "Forbes and Company", "subsidiary_of"),
    # A United Spirits section about Diageo plc was mis-attributed to
    # Precision Camshafts (newsletter body bleed across companies in
    # Decoding_the_Dialogue.md).
    ("Precision Camshafts", "Diageo plc", "subsidiary_of"),
    # The Groww section in A_Quarter_That_Refuses_To_Behave.md has an
    # OCR-garbled heading ('Billionbrains Garage Ventures Limited (Growy')
    # that doesn't resolve to Groww; all Groww-section prose (including
    # 'acquired Fisdom') was attributed to the prior section (ICRA).
    # The correct edge is Groww -> Fisdom, captured separately.
    ("ICRA", "Fisdom", "acquired"),
    # competes_with attribution bleeds from Opportunity_Between_The_Lines.md:
    # the prose "Indian peers like Tata Motors (349,000 units) and Ashok
    # Leyland (179,000 units)" appears inside Ravindra Energy's section but
    # actually describes FOTON/EIM's CV competitors, not Ravindra's (an EV
    # tractor company has no CV peers). Additionally the resolver maps
    # "Tata Motors" → "Tata Motors Passenger Vehicles" (the only Tata Motors
    # entity in the DB), which is the wrong subsidiary. Suppress both edges;
    # a proper Tata Motors ↔ Ashok Leyland competes_with edge should be
    # hand-seeded once a "Tata Motors" parent entity exists.
    ("Ravindra Energy", "Tata Motors Passenger Vehicles", "competes_with"),
    ("Ashok Leyland", "Tata Motors Passenger Vehicles", "competes_with"),
    # Symmetric-edge dedup: these company↔company edges already exist as a
    # hand-curated manual:foreign-parents edge in the REVERSE direction. The
    # extractor re-derives the forward direction from each entity's own note
    # ("joint venture with Brooks" in OneSource's note; "subsidiary of Lemon
    # Tree" in Totally Foxed's note) on every run, and INSERT OR IGNORE does
    # not dedupe across directions for these edge types — producing a circular
    # pair the integrity check flags. Suppress the derived direction; the
    # manual: row is the canonical one.
    ("Brooks", "OneSource Specialty Pharma", "jv_with"),
    ("Totally Foxed Solutions", "Lemon Tree Hotels", "subsidiary_of"),
}


# --------------------------------------------------------------------------- #
# Document-type detection & company-note helpers                              #
# --------------------------------------------------------------------------- #
def _parse_yaml_field(content: str, field: str) -> str | None:
    """Best-effort single-field extraction from YAML front matter.

    Handles the common one-liner form:
        field: value
    and the quoted form:
        field: "value"

    Returns None if the field is absent or no front matter is present.
    Does NOT invoke a full YAML parser — keeps the module dependency-free.
    """
    m = _YAML_FRONT_MATTER_RE.match(content)
    if not m:
        return None
    yaml_block = content[: m.end()]
    pat = re.compile(
        r"^" + re.escape(field) + r"\s*:\s*(.+?)\s*$",
        re.MULTILINE,
    )
    fm = pat.search(yaml_block)
    if not fm:
        return None
    val = fm.group(1).strip()
    # Strip surrounding quotes.
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    return val


def _detect_doc_type(content: str) -> str:
    """Detect document type from YAML front matter.

    Returns:
        'company'  — YAML `type: company`
        'sector'   — YAML `type: sector`
        'newsletter' — no YAML, unknown type, or any other value.

    Newsletter source files have no YAML front matter; synced entity notes
    (under findata/Companies/ and findata/Sectors/) all carry `type:`.
    """
    t = _parse_yaml_field(content, "type")
    if t == "company":
        return "company"
    if t == "sector":
        return "sector"
    return "newsletter"


def _resolve_h1_title(body: str, resolver: EntityResolver) -> str | None:
    """Find the first H1 heading (`# Foo`) in `body` and resolve it.

    Returns the resolved entity name, or None if no H1 / unresolved.
    Strips trailing legal suffixes (Limited/Ltd/Private) before resolving.
    """
    m = _H1_TITLE_RE.search(body)
    if not m:
        return None
    title = m.group(1).strip()
    return resolver.resolve(title)


def _make_properties(
    edition_title: str,
    newsletter_type: str,
    doc_type: str,
    quote: str,
    year: int | None = None,
) -> dict:
    """Build the audit-trail properties dict for an edge.

    For company notes, `newsletter` is omitted (it's not from a newsletter)
    and `doc_type` is set to 'company'. For newsletters, both `newsletter`
    and `doc_type='newsletter'` are set, preserving the existing schema.

    `year` is the optional 4-digit acquisition year extracted from the quote
    (see `_extract_year_from_context`). When present, it's added as
    `properties.year` for machine-friendly filtering. The DB column
    `valid_from` is populated separately at INSERT time.
    """
    base: dict
    if doc_type == "company":
        base = {
            "note": edition_title,  # normalized_name of the source note
            "doc_type": "company",
            "quote": quote,
        }
    else:
        base = {
            "edition": edition_title,
            "newsletter": newsletter_type,
            "doc_type": "newsletter",
            "quote": quote,
        }
    if year is not None:
        base["year"] = year
    return base


# --------------------------------------------------------------------------- #
# Temporal context extraction (for `acquired` edges)                          #
# --------------------------------------------------------------------------- #
# Noise patterns to strip before year extraction. These typically appear in
# the OCR window around an acquisition keyword but aren't real acquisition
# dates — e.g. "Yahoo Finance, Jun 2026" attribution lines, or generic
# "last 3 years" / "over the next 5 years" time horizons.
_NOISE_LINE_RE = re.compile(
    r"\b(?:Yahoo\s+Finance|Bloomberg|Source:\s*Yahoo|as\s+of\s+\w+\s+20\d{2})[^\\n]*",
    re.IGNORECASE,
)

# Month name → month number (1-12).
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

# "Q1 FY26" → (month, year). FY in India starts April 1, so Q1 FY26 is
# Apr-Jun 2025, Q2 FY26 is Jul-Sep 2025, Q3 FY26 is Oct-Dec 2025, Q4 FY26 is
# Jan-Mar 2026. We use the START month of the quarter (Q1→Apr, Q2→Jul,
# Q3→Oct, Q4→Jan of the following calendar year).
_FY_QUARTER_TO_MONTH = {1: 4, 2: 7, 3: 10, 4: 1}

# Captures "Q[1-4] FY\d{2}" case-insensitively, with optional space around FY.
# Examples: "Q4 FY26", "Q1FY27", "q2 fy 25".
_FY_RE = re.compile(r"\bQ([1-4])\s*(?:FY|fy)\s*(\d{2})\b")

# Captures "Month Year" — e.g. "Dec 2025", "October 2021", "Sept 2025".
_MONTH_YEAR_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\.?\s+(20\d{2})\b",
    re.IGNORECASE,
)

# Captures a standalone 4-digit year.
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

# Captures "last year" / "this year" / "previous year".
_REL_YEAR_RE = re.compile(
    r"\b(last|this|previous|prior)\s+year\b",
    re.IGNORECASE,
)


def _extract_year_from_context(
    quote: str | None,
    edition_label: str | None = None,
) -> tuple[int | None, str | None]:
    """Extract the most likely acquisition year from a prose quote.

    Args:
        quote: A snippet of prose containing the acquisition keyword. Should
            typically be the `_extract_quote_around(body, pos)` window.
        edition_label: Optional newsletter edition title or company note
            normalized_name. Currently unused (reserved for future
            edition→year resolution of `last year` / `this year` patterns).

    Returns:
        ``(year, iso_date)`` where:
          - ``year`` is the 4-digit integer (e.g. 2024), or None if no year
            could be extracted.
          - ``iso_date`` is ``YYYY-MM-01`` (first-of-month) when a month was
            captured, ``YYYY-01-01`` when only a year was captured, or None
            when no temporal signal was found.

        Choice (a) from the design plan: when only the year is known, we
        populate valid_from as ``YYYY-01-01`` to keep temporal queries
        sortable. The ``properties.year`` field preserves the precision
        level (year vs month-year).

    Extraction order (first match wins):
      1. Strip attribution-noise lines (Yahoo Finance / Bloomberg / "as of").
      2. "Month Year" (e.g. "Dec 2025", "October 2021"). Highest confidence.
      3. "Q[1-4] FY\\d{2}" — Indian fiscal quarter. Maps to calendar year.
      4. Standalone 4-digit year — pick the most plausible (see below).
      5. "last/this/previous/prior year" — reserved for edition-context
         resolution; returns None for now.

    For standalone years, when multiple candidates appear in the quote, we
    apply a plausibility filter:
      - Drop only truly-future years. A current-year acquisition (e.g. one
        completed in Jan-Jul 2026) is past-tense and must be kept — the
        live DB already carries current-year `acquired` edges, so excluding
        the current year would silently drop real signal.
      - Prefer the earliest plausible year in 2018-current_year (most
        acquisitions we cover are within the last 5-7 years).
      - If only one year remains, use it.
    """
    if not quote:
        return None, None

    # 1. Strip attribution noise.
    cleaned = _NOISE_LINE_RE.sub(" ", quote)

    # 2. Month + Year.
    m = _MONTH_YEAR_RE.search(cleaned)
    if m:
        month = _MONTHS[m.group(1).lower()]
        year = int(m.group(2))
        return year, f"{year:04d}-{month:02d}-01"

    # 3. Q[1-4] FY\d{2}.
    m = _FY_RE.search(cleaned)
    if m:
        q = int(m.group(1))
        fy_short = int(m.group(2))  # e.g. 26 for FY26
        # FY26 starts Apr 2025. Calendar year of Q start = 2000 + fy_short - 1
        # for Q1/Q2/Q3 (which fall in the earlier calendar year), or
        # 2000 + fy_short for Q4 (which falls in Jan-Mar of the later year).
        if q == 4:
            year = 2000 + fy_short  # Q4 FY26 = Jan-Mar 2026
        else:
            year = 2000 + fy_short - 1  # Q1 FY26 = Apr-Jun 2025
        month = _FY_QUARTER_TO_MONTH[q]
        return year, f"{year:04d}-{month:02d}-01"

    # 4. Standalone 4-digit year.
    # Plausibility window: 2018 up to and INCLUDING the current year.
    candidates = [int(y) for y in _YEAR_RE.findall(cleaned)]
    if candidates:
        # Filter out future years only. A current-year acquisition (e.g.
        # "Acquired by Tata Technologies in 2026" written in Jul 2026) is a
        # legitimately completed, past-tense event — excluding it would
        # silently drop real signal. Only truly-future years are rejected.
        from datetime import date as _date

        current_year = _date.today().year
        plausible = [y for y in candidates if 2018 <= y <= current_year]
        if plausible:
            # Pick the earliest plausible year. Acquisitions in our corpus
            # are typically the most recent past one mentioned, but earliest
            # avoids confusion with stale attribution lines.
            year = min(plausible)
            return year, f"{year:04d}-01-01"

    # 5. Relative year phrases — reserved for edition-context resolution.
    # Return None until we have the edition→year map wired in.
    return None, None


# --------------------------------------------------------------------------- #
# Extraction                                                                  #
# --------------------------------------------------------------------------- #
def extract_relations(  # noqa: C901
    content: str,
    *,
    edition_title: str,
    newsletter_type: str,
    resolver: EntityResolver,
    doc_type: str = "newsletter",
    source_entity_override: str | None = None,
) -> tuple[dict[str, list[Edge]], list[Unresolved]]:
    """Extract structured edges from a single document.

    Args:
        content: Document source text (with YAML front matter if present).
        edition_title: e.g. "Jio Financial, Wipro, Polycab, Piramal & More"
            or the file stem if no edition title is known.
        newsletter_type: One of NEWSLETTER_DIRS keys (used for source_ref
            audit trail).
        resolver: EntityResolver built from current entities.name set.
        doc_type: One of 'newsletter' (default; multi-section file) or
            'company' (single-section file — the whole post-YAML body is
            scanned as one section anchored to the note's entity).
        source_entity_override: For doc_type='company', the resolved
            entity name to anchor the scan to (typically the note's
            `normalized_name` resolved via `entities.name`). If None and
            doc_type='company', the function will attempt to resolve the
            H1 title.

    Returns:
        Tuple ``(edges_by_type, unresolved)`` where ``edges_by_type`` maps
        edge_type → list of ``Edge``. ``unresolved`` is the list of pattern
        matches whose target mention couldn't be resolved to a known entity.

    Audit trail:
        Edges carry ``properties.doc_type`` ("newsletter" | "company") so
        downstream readers can distinguish verbatim newsletter quotes from
        paraphrased company-note text. ``source_ref`` is also distinct:
          - newsletter: ``derive:relations:<newsletter_type>``
          - company:    ``derive:relations:company_note:<normalized_name>``
    """
    edges_by_type: dict[str, list[Edge]] = {}
    unresolved: list[Unresolved] = []
    group_to_companies: dict[str, set[str]] = {}

    if doc_type == "company":
        source_ref_default = (
            f"derive:relations:company_note:{source_entity_override or edition_title}"
        )
        # Build a single synthetic "section" covering the whole body. The
        # body is the file content with YAML front matter stripped.
        body = _strip_yaml_front_matter(content)
        source_entity = source_entity_override or _resolve_h1_title(body, resolver)
        if source_entity is None:
            return edges_by_type, unresolved
        sections: list[tuple[str, str]] = [(source_entity, body)]
        # company_note = True disables group-clustering across sections
        # (the whole file is one company, so there's nothing to cluster).
    else:
        source_ref_default = f"derive:relations:{newsletter_type}"
        # Resolve each heading to a known entity; sections whose heading
        # doesn't resolve are kept with source_entity=None so they can
        # still contribute to group-clustering (but produce no anchored
        # edges, matching pre-refactor behavior).
        sections = []
        for heading_name, _hs, body_start, body_end in _split_sections(content):
            resolved = resolver.resolve(heading_name)
            body = content[body_start:body_end]
            sections.append((resolved, body))
            # Even unresolved headings contribute group context (so we can
            # cluster resolved entities that share a group with an
            # as-yet-unresolved sibling).
            if resolved is None:
                _capture_groups(body, "", None, group_to_companies)

    for source_entity, body in sections:
        if source_entity is None:
            # Heading couldn't be resolved — can't anchor edges. Group
            # context already captured above.
            continue
        # Same-group capture (a company may belong to multiple groups; we
        # record all). Harmless for company notes (single section).
        _capture_groups(body, "", source_entity, group_to_companies)

        # Per-pattern scan.
        for pat, edge_type, symmetric, direction in PATTERNS:
            for m in pat.finditer(body):
                # Two-group patterns: (a) customer_of (parens form vs is/are
                # form — pick whichever matched); (b) G2 stake patterns
                # (group 1 = percentage, group 2 = the mention; detected by
                # group 1 being fully numeric).
                stake_pct: float | None = None
                if edge_type == "customer_of" and m.group(2):
                    target_mention = m.group(2).strip()
                elif (
                    m.lastindex is not None
                    and m.lastindex >= 2
                    and m.group(1) is not None
                    and m.group(2) is not None
                    and re.fullmatch(r"\d{1,3}(?:\.\d+)?", m.group(1))
                ):
                    stake_pct = float(m.group(1))
                    target_mention = m.group(2).strip()
                else:
                    target_mention = m.group(1).strip()
                if edge_type == "subsidiary_of" and stake_pct is not None and stake_pct < 50:
                    # G2 holds-below-50%: passive holding, not a subsidiary.
                    # Tier C drop (see PATTERNS comment).
                    continue
                # Strip trailing articles / whitespace junk.
                target_mention = _TRAILING_JUNK_RE.sub("", target_mention).strip(" .,;:")
                if not target_mention or len(target_mention) < 2:
                    continue

                # Skip obvious generic-target false positives.
                lower_mention = f" {target_mention.lower().strip()} "
                if edge_type == "acquired":
                    if any(g.strip() in lower_mention for g in _GENERIC_ACquired_TARGETS):
                        continue
                if edge_type in ("supplier_to", "customer_of"):
                    stripped_lower = target_mention.lower().strip()
                    # Reject exact-match generic targets.
                    if stripped_lower in _GENERIC_SUPPLIER_TARGETS:
                        continue
                    # Reject targets that START with a generic word ("OEMs and the ...",
                    # "the automotive industry", "auto makers", etc.).
                    first_word = stripped_lower.split()[0] if stripped_lower else ""
                    if first_word in _GENERIC_SUPPLIER_TARGETS:
                        continue
                    # Reject "the X" / "its X" patterns outright.
                    if first_word in ("the", "its", "our", "their"):
                        continue
                if edge_type == "competes_with":
                    # competes_with generic targets are NOT sidecarred — the
                    # prose ("competes with peers", "competition from Chinese
                    # imports") carries no resolvable entity, so the sidecar
                    # would just fill with noise. Drop silently.
                    stripped_lower = target_mention.lower().strip()
                    if stripped_lower in _GENERIC_COMPETITOR_TARGETS:
                        continue
                    first_word = stripped_lower.split()[0] if stripped_lower else ""
                    if first_word in _GENERIC_COMPETITOR_TARGETS:
                        continue
                    if first_word in ("the", "its", "our", "their", "other", "many", "some"):
                        continue
                    # Reject SECTOR-GROUPING contexts. The pattern's
                    # fixed-width lookbehind catches "alongside peers"; the
                    # longer grouping phrases ("grouped with peers such as",
                    # "mentioned alongside peers like") are checked here by
                    # scanning a 60-char window before the match start.
                    # These describe sector classification, not direct
                    # competition (e.g. "Bharti Airtel grouped with peers
                    # such as Pace Digitek").
                    window = body[max(0, m.start() - 60) : m.start()].lower()
                    if any(p in window for p in _COMPETES_GROUPING_PREFIXES):
                        continue

                target_entity = None
                # List-shaped mentions (comma or " and "/" or " conjunction)
                # for customer_of / competes_with: resolve per-chunk instead
                # of letting the fuzzy matcher collapse the whole list to the
                # first entity. customer_of: "major customers (IOCL 1.5 MMSCMD,
                # BPCI 0.8 MMSCMD)". competes_with: "peers like Tata Motors,
                # Ashok Leyland, and Eicher Motors" (Pattern A captures the
                # whole list span). Emits one edge per resolvable chunk.
                if edge_type in ("customer_of", "competes_with"):
                    raw = _LIST_CHUNK_SPLIT_RE.split(target_mention)
                    chunks = [c.strip() for c in raw if c.strip()]
                else:
                    chunks = []
                if len(chunks) > 1:
                    emitted_any = False
                    for chunk in chunks:
                        # Strip leading conjunction ("and Britannia" →
                        # "Britannia") and trailing lowercase words
                        # ("Ashok Leyland dominate the CV market" →
                        # "Ashok Leyland") — proper-noun company names
                        # are Capitalised throughout, so the first
                        # lowercase word marks the end of the name.
                        chunk_clean = _LEADING_CONJ_RE.sub("", chunk)
                        chunk_clean = _TRAILING_NUM_UNIT_RE.sub("", chunk_clean)
                        # Truncate at first lowercase word (heuristic for
                        # trailing prose bleed in Pattern A's wide capture).
                        words = chunk_clean.split()
                        kept: list[str] = []
                        for w in words:
                            if w and not w[0].isupper():
                                break
                            kept.append(w)
                        chunk_clean = " ".join(kept).strip(" .,;:")
                        if len(chunk_clean) < 2:
                            continue
                        chunk_entity = resolver.resolve(chunk_clean)
                        if chunk_entity is None or chunk_entity == source_entity:
                            continue
                        emitted_any = True
                        if direction == "forward":
                            csrc, ctgt = source_entity, chunk_entity
                        else:
                            csrc, ctgt = chunk_entity, source_entity
                        if symmetric and csrc > ctgt:
                            csrc, ctgt = ctgt, csrc
                        edges_by_type.setdefault(edge_type, []).append(
                            Edge(
                                source=csrc,
                                target=ctgt,
                                edge_type=edge_type,
                                properties=_make_properties(
                                    edition_title,
                                    newsletter_type,
                                    doc_type,
                                    _extract_quote_around(body, m.start()),
                                ),
                                source_ref=source_ref_default,
                                symmetric=symmetric,
                            )
                        )
                    # Only skip the sidecar if we emitted at least one edge.
                    # Otherwise, fall through so the unresolved whole-mention
                    # is recorded for human triage.
                    if emitted_any:
                        continue
                else:
                    target_entity = resolver.resolve(target_mention)
                if target_entity is None:
                    # Write-time noise gate: countries / generic phrases /
                    # mangled fragments never reach the triage queue (the
                    # measured ~35-row class of the 2026-08-25 backlog).
                    if noise_target(target_mention):
                        continue
                    # Sidecar for human review.
                    unresolved.append(
                        Unresolved(
                            edge_type=edge_type,
                            source=source_entity,
                            target_mention=target_mention,
                            quote=_extract_quote_around(body, m.start()),
                            edition=edition_title,
                            direction=direction,
                        )
                    )
                    continue
                if target_entity == source_entity:
                    continue  # self-edge; skip

                # Direction.
                if direction == "forward":
                    src, tgt = source_entity, target_entity
                else:
                    src, tgt = target_entity, source_entity

                # Symmetric canonical ordering.
                if symmetric and src > tgt:
                    src, tgt = tgt, src

                quote = _extract_quote_around(body, m.start())
                # Temporal extraction: try to pull a year/month from the
                # surrounding prose so we can populate `valid_from` (DB
                # column) and `properties.year` (filter). Currently only
                # fires for `acquired` (see
                # `_EDGE_TYPES_WITH_PROSE_YEAR_EXTRACTION` docstring for
                # why non-acquired edge types are excluded).
                year: int | None = None
                iso_date: str | None = None
                if edge_type in _EDGE_TYPES_WITH_PROSE_YEAR_EXTRACTION:
                    year, iso_date = _extract_year_from_context(
                        quote,
                        edition_label=edition_title,
                    )
                edge = Edge(
                    source=src,
                    target=tgt,
                    edge_type=edge_type,
                    properties=_make_properties(
                        edition_title,
                        newsletter_type,
                        doc_type,
                        quote,
                        year,
                    ),
                    source_ref=source_ref_default,
                    symmetric=symmetric,
                    valid_from=iso_date,
                )
                if stake_pct is not None:
                    edge.properties["stake_pct"] = stake_pct
                edges_by_type.setdefault(edge_type, []).append(edge)

    # Derive same_group edges from companies that share a group. Only
    # meaningful for newsletters (multiple sections); company notes have
    # one section so no pairs to cluster.
    if doc_type != "company":
        same_group_edges = _derive_same_group(
            group_to_companies,
            edition_title=edition_title,
            newsletter_type=newsletter_type,
            source_ref=source_ref_default,
        )
        if same_group_edges:
            edges_by_type["same_group"] = same_group_edges

    # Dedup edges within each type (preserve first occurrence's properties).
    for et, edges in edges_by_type.items():
        seen: set[tuple[str, str]] = set()
        deduped: list[Edge] = []
        for e in edges:
            key = (e.source, e.target)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(e)
        edges_by_type[et] = deduped

    return edges_by_type, unresolved


def _capture_groups(
    body: str,
    _heading: str,
    source_entity: str | None,
    group_to_companies: dict[str, set[str]],
) -> None:
    """Record group-membership matches against the section's company.

    Scans every pattern in ``GROUP_RES`` ("part of the X Group", "<X>
    promoter group", "flagship of the <X> Group", "<X> Group company",
    "<X> Group flagship"). We key on the GROUP NAME (e.g. "Aditya Birla"),
    not on the entity, so that multiple companies in the same group cluster
    together even when one of them isn't a known entity yet.
    """
    for group_re in GROUP_RES:
        for m in group_re.finditer(body):
            raw = m.group(1).strip()
            if not raw or len(raw) < 3:
                continue
            group_name = _normalize_group_name(raw)
            if not group_name:
                continue
            if source_entity:
                group_to_companies.setdefault(group_name, set()).add(source_entity)


def _derive_same_group(
    group_to_companies: dict[str, set[str]],
    *,
    edition_title: str,
    newsletter_type: str,
    source_ref: str,
) -> list[Edge]:
    """Emit same_group edges for every pair of companies in the same group.

    Requires >=2 resolved entities per group; otherwise nothing to connect.
    Edges carry `properties.group` for audit.
    """
    edges: list[Edge] = []
    for group_name, members in group_to_companies.items():
        if len(members) < 2:
            continue
        for a, b in combinations(sorted(members), 2):
            edges.append(
                Edge(
                    source=a,
                    target=b,
                    edge_type="same_group",
                    properties={
                        "group": group_name,
                        "edition": edition_title,
                        "newsletter": newsletter_type,
                    },
                    source_ref=source_ref,
                    symmetric=True,
                )
            )
    return edges


def _extract_quote_around(body: str, pos: int, *, window: int = 120) -> str:
    """Return a short window of text around `pos` for the audit trail.

    We trim to the enclosing sentence-ish region (first '. ' or '.\n' before
    and after `pos`, capped to `window` chars each side).
    """
    start = max(0, pos - window)
    end = min(len(body), pos + window)
    snippet = body[start:end].replace("\n", " ").strip()
    # Collapse runs of whitespace (OCR fragments).
    snippet = _WS_RUN_RE.sub(" ", snippet)
    # Truncate at the first sentence boundary past `pos` if reasonable.
    return snippet[:240]


# --------------------------------------------------------------------------- #
# Persistence                                                                 #
# --------------------------------------------------------------------------- #
class ApplyEdgesResult(NamedTuple):
    """Outcome of ``apply_edges`` (Bundle F5).

    Previously the function returned only the ``inserted`` count, silently
    dropping ``skipped_fk`` / ``skipped_suppressed`` — so a caller treating
    ``inserted == 0`` as success could miss that every edge was rejected by
    an integrity error (e.g. after a schema change adding a NOT NULL column).
    The three counters are now surfaced explicitly; the CLI prints all three.
    """

    inserted: int
    skipped_fk: int
    skipped_suppressed: int

    @property
    def total_seen(self) -> int:
        """Edges considered = inserted + skipped (FK + suppressed)."""
        return self.inserted + self.skipped_fk + self.skipped_suppressed


def _load_existing_edges(conn) -> set[tuple[str, str, str]]:
    """Full (source, target, edge_type) triple set from graph_edges.

    The per-call cost of this scan is ~15ms at the current edge volume —
    fine once, pure waste ×110 in the CLI reduce loop. See apply_edges.
    """
    return {
        (r[0], r[1], r[2])
        for r in conn.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
    }


def apply_edges(  # noqa: C901
    edges: Iterable[Edge],
    *,
    conn=None,
    dry_run: bool = True,
    existing: set[tuple[str, str, str]] | None = None,
) -> ApplyEdgesResult:
    """INSERT OR IGNORE each edge. Returns an :class:`ApplyEdgesResult` with
    ``inserted``, ``skipped_fk`` and ``skipped_suppressed`` counts (Bundle F5).

    ``inserted`` counts rows actually inserted (dry_run=False) or that would
    be inserted (dry_run=True). The two skip counters were previously dropped
    — callers can now distinguish "0 inserted, 0 skipped" (nothing to do)
    from "0 inserted, N skipped" (integrity errors ate the whole batch).

    ``existing`` lets a caller that applies many batches against the same
    DB pass the dry-run triple set ONCE (loaded via `_load_existing_edges`)
    instead of paying a full graph_edges scan per call — the CLI reduce
    loop over ~110 notes was re-scanning 17k edges per file (~15ms each,
    ~1.5s of pure repetition). None → self-load (per-call behaviour kept
    for external callers/tests). Only meaningful with dry_run=True.

    Suppressed edges (see `_SUPPRESSED_EDGES`) are skipped silently — these
    are typically hand-corrected attributions where the prose was in one
    company's note but the actual subject is a different entity.

    FK failures (e.g. the resolver returned a name that's not actually in
    `entities.name`) are logged to stderr and skipped — they don't abort
    the batch. The caller is expected to fix the missing entity and re-run
    (idempotent).
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()
    inserted = 0
    skipped_fk = 0
    skipped_suppressed = 0
    try:
        # Bundle U3: in dry-run mode, bulk-fetch the set of existing
        # (source, target, edge_type) triples ONCE instead of firing a
        # per-edge SELECT (was N round-trips for N edges; now 1). The set
        # is checked in-memory during the loop.
        if dry_run and existing is None:
            existing = _load_existing_edges(conn)

        # Bundle U2: wrap the loop in `with conn:` for atomic commit/rollback.
        # An FK error mid-batch no longer leaves prior INSERTs committed.
        with conn:
            for e in edges:
                # Hard-coded suppressions (mis-attributed edges that have been
                # hand-corrected to a different source).
                if (e.source, e.target, e.edge_type) in _SUPPRESSED_EDGES:
                    skipped_suppressed += 1
                    continue
                if dry_run:
                    if (e.source, e.target, e.edge_type) not in (existing or set()):
                        inserted += 1
                    continue
                props_json = json.dumps(e.properties, ensure_ascii=False, sort_keys=True)
                try:
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO graph_edges
                            (source, target, edge_type, properties, source_ref,
                             symmetric, valid_from)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            e.source,
                            e.target,
                            e.edge_type,
                            props_json,
                            e.source_ref,
                            1 if e.symmetric else 0,
                            e.valid_from,
                        ),
                    )
                    inserted += cur.rowcount
                except Exception as exc:
                    # FK violation, CHECK constraint, or any other integrity
                    # error: log and continue. Don't abort the batch — the
                    # caller can fix the missing entity and re-run (idempotent).
                    print(
                        f"warning: skipped edge {e.source} → {e.target} "
                        f"({e.edge_type}): {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    skipped_fk += 1
    finally:
        if own_conn:
            conn.close()
    if skipped_fk:
        print(
            f"warning: {skipped_fk} edge(s) skipped due to integrity errors",
            file=sys.stderr,
        )
    if skipped_suppressed:
        print(
            f"note: {skipped_suppressed} edge(s) skipped by _SUPPRESSED_EDGES "
            f"(hand-corrected attribution)",
            file=sys.stderr,
        )
    return ApplyEdgesResult(inserted, skipped_fk, skipped_suppressed)


def write_sidecar(unresolved: list[Unresolved], path: Path = SIDECAR_PATH) -> int:
    """Append unresolved matches to the sidecar file for human triage.

    Format: one JSON-lines entry per match, with `edge_type`, `source`,
    `target_mention`, `quote`, `edition`, `direction` ('forward' |
    'reverse' — the orientation the edge would take once the mention
    resolves). The file is append-only and
    re-running this script will append duplicates; users are expected to
    clear it before each batch run.
    """
    if not unresolved:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for u in unresolved:
            f.write(json.dumps(asdict(u), ensure_ascii=False) + "\n")
            n += 1
    return n


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
# Files that are NOT newsletter prose even though they live in a newsletter
# directory. Skipped during directory recursion.
_NEWSLETTER_SKIP_FILES = {
    "image_map.md",  # per-newsletter image manifest (filenames, not prose)
}


def _newsletter_type_for(path: Path) -> str:
    """Infer newsletter_type from the parent dir name."""
    parent = path.parent.name
    if parent in NEWSLETTER_DIRS:
        return parent
    # Fall back: any of the known names in the path.
    for nt in NEWSLETTER_DIRS:
        if nt.replace("_", " ") in str(path).lower() or nt in str(path):
            return nt
    return "The_Chatter"  # default


# --------------------------------------------------------------------------- #
# Directory-sharded parallelism                                                #
# --------------------------------------------------------------------------- #
# Extraction (regex matching + fuzzy resolution) is CPU-bound and GIL-locked,
# so a ProcessPool fans it out across cores. DB writes (apply_edges, sidecar)
# stay serial in the parent to preserve the single-writer contract + ordering.

# Minimum file count to justify spawning worker processes.
_PARALLEL_THRESHOLD = 20


def _extract_batch(
    file_paths: list[str],
    entity_names: list[str],
) -> list[
    tuple[str, str, list[Edge], list[Unresolved], dict[str, int], list[tuple[str, list[str]]]]
]:
    """Process a batch of newsletter files in a worker process.

    Builds its own EntityResolver from entity_names (cheap; avoids serializing
    the resolver's indexes across the process boundary). Returns per-file
    results: (display_path, doc_type, edges, unresolved, type_counts,
    ambiguities) where ``ambiguities`` lists (mention, tied_candidates)
    pairs whose fuzzy resolution had equally-scored candidates (Tier-C
    audit lines — resolution itself is unaffected).

    Must be module-level so ProcessPoolExecutor can pickle it.
    """
    resolver = EntityResolver(entity_names)
    results = []
    for fp in file_paths:
        nl_path = Path(fp)
        ambig_before = len(resolver.ambiguous_log)
        content = nl_path.read_text(encoding="utf-8")
        edition_title = nl_path.stem.replace("_", " ")
        newsletter_type = _newsletter_type_for(nl_path)
        doc_type = _detect_doc_type(content)
        if doc_type == "sector":
            results.append((str(nl_path), "sector", [], [], {}, []))
            continue
        source_entity_override = None
        if doc_type == "company":
            norm = _parse_yaml_field(content, "normalized_name")
            if norm:
                resolved = resolver.resolve(norm.replace("_", " ")) or resolver.resolve(norm)
                if resolved:
                    source_entity_override = resolved
        edges_by_type, unresolved = extract_relations(
            content,
            edition_title=edition_title,
            newsletter_type=newsletter_type,
            resolver=resolver,
            doc_type=doc_type,
            source_entity_override=source_entity_override,
        )
        type_counts = {et: len(es) for et, es in edges_by_type.items()}
        all_edges = [e for es in edges_by_type.values() for e in es]
        ambiguities = resolver.ambiguous_log[ambig_before:]
        results.append((str(nl_path), doc_type, all_edges, unresolved, type_counts, ambiguities))
    return results


def _extract_batch_arg(args: tuple[list[str], list[str]]):
    """Single-argument wrapper for ProcessPoolExecutor.map (takes a tuple)."""
    return _extract_batch(args[0], args[1])


def _expand_paths(  # noqa: C901
    raw_args: list[str],
    *,
    project_root: Path = _REPO_ROOT,
) -> list[Path]:
    """Expand a mix of file paths, directory paths, and glob patterns into a
    sorted, de-duplicated list of concrete `.md` files.

    Each arg can be:
      - a file path (`findata/The_Chatter/Foo.md`) → included as-is.
      - a directory path (`findata/The_Chatter` or `findata/Companies`) →
        recursively scanned for `*.md` files.
      - a shell-expanded glob (`findata/The_Chatter/*.md`) → already a list of
        files by the time it reaches us; each is included as-is.

    Args are resolved relative to `project_root` unless absolute.

    Files in `_NEWSLETTER_SKIP_FILES` (e.g. `image_map.md`) are excluded.
    Files under an `images/` subdir are excluded (they are `.jpeg` anyway,
    but defensive — covers any stray `.md` placed there).

    All other `.md` files are returned. Document type (newsletter vs company
    vs sector) is detected downstream from each file's YAML front matter —
    see `_detect_doc_type`. Sector notes are skipped at extraction time
    (they don't anchor company relations).

    Non-existent paths emit a warning to stderr and are skipped.
    """
    seen: set[Path] = set()
    out: list[Path] = []
    for arg in raw_args:
        p = Path(arg)
        if not p.is_absolute():
            p = (project_root / arg).resolve()
        else:
            p = p.resolve()
        if not p.exists():
            print(f"warning: path not found, skipping: {p}", file=sys.stderr)
            continue
        if p.is_dir():
            for md in sorted(p.rglob("*.md")):
                if md.name in _NEWSLETTER_SKIP_FILES:
                    continue
                rel_parts = md.relative_to(p).parts
                # Skip files inside an `images/` subdir.
                if "images" in rel_parts:
                    continue
                resolved = md.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    out.append(resolved)
        elif p.is_file():
            if p.name in _NEWSLETTER_SKIP_FILES:
                continue
            # Defensive: skip files under `images/` even if passed directly.
            try:
                rel = p.relative_to(project_root)
                if "images" in rel.parts:
                    continue
            except ValueError:
                pass
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def _cli(argv: list[str] | None = None) -> int:  # noqa: C901
    p = argparse.ArgumentParser(
        description="Extract structured relation edges from newsletter prose.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Single file (dry-run):\n"
            "  %(prog)s findata/The_Chatter/Foo.md\n"
            "  # Apply:\n"
            "  %(prog)s findata/The_Chatter/Foo.md --apply\n"
            "  # Shell-expanded glob:\n"
            "  %(prog)s findata/The_Chatter/*.md --apply\n"
            "  # Recursive directory scan (all newsletters):\n"
            "  %(prog)s findata/The_Chatter findata/Points_And_Figures --apply\n"
            "  # Everything:\n"
            "  %(prog)s findata --apply\n"
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help=(
            "Paths to newsletter .md files OR directories containing them. "
            "Directories are scanned recursively. image_map.md and files "
            "under images/ subdirs are skipped."
        ),
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write edges to graph_edges (default: dry-run).",
    )
    p.add_argument(
        "--write-sidecar",
        action="store_true",
        default=True,
        help="Append unresolved matches to findata/_pending_relations.txt "
        "(default: on). Pass --no-write-sidecar to disable.",
    )
    p.add_argument(
        "--no-write-sidecar",
        dest="write_sidecar",
        action="store_false",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print every extracted edge in addition to the summary.",
    )
    p.add_argument(
        "--counts-json",
        metavar="PATH",
        default=None,
        help=(
            "Write per-edge-type totals as JSON to PATH (E1 diff-audit "
            "harness: run once before and once after a pattern change, "
            "then diff with helpers/misc/relation_diff_audit.py)."
        ),
    )
    args = p.parse_args(argv)

    # Expand the mix of files / dirs / globs into a flat sorted list of .md
    # paths. Resolves relative to _REPO_ROOT unless absolute.
    nl_paths = _expand_paths(args.paths)
    if not nl_paths:
        print("no newsletter files found after expansion", file=sys.stderr)
        return 1

    # Load entity names once.
    conn = connect()
    try:
        names = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM entities WHERE entity_type='company'"
            ).fetchall()
        ]
    finally:
        # We keep the connection open for apply_edges below.
        pass

    total_extracted = 0
    total_applied = 0
    total_unresolved = 0
    total_skipped_fk = 0
    total_skipped_suppressed = 0
    total_ambiguous = 0
    per_type_totals: dict[str, int] = {}

    use_parallel = len(nl_paths) >= _PARALLEL_THRESHOLD

    if use_parallel:
        # Parallel map phase: shard files across workers, each builds its own
        # EntityResolver and runs the CPU-bound extract_relations. DB writes
        # stay serial in the parent (single-writer contract).
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        workers = min(4, os.cpu_count() or 1)
        chunks = [([str(p) for p in nl_paths[i::workers]], names) for i in range(workers)]
        # S1b fix: use `helpers.graph._extract_worker` (always importable) so
        # pickle is `helpers.graph._extract_worker._extract_batch_arg` not
        # `__main__._extract_batch_arg` (fails when file is run as `__main__`,
        # the normal `python3 helpers/...py` + `cProfile` path). The worker
        # delegates to `extract_relations._extract_batch` at call time.
        from helpers.graph._extract_worker import _extract_batch_arg as _worker_arg

        try:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                batch_results = list(ex.map(_worker_arg, chunks))
        except BrokenProcessPool:
            # Fallback to serial processing if child processes crash
            # (e.g., under memory pressure or OOM killer).
            import sys as _sys

            print("WARNING: ProcessPoolExecutor crashed, falling back to serial", file=_sys.stderr)
            batch_results = [_extract_batch(chunk[0], chunk[1]) for chunk in chunks]
        # Flatten worker results into per-file tuples, preserving order.
        file_results = []
        for batch in batch_results:
            file_results.extend(batch)
    else:
        # Serial path: process files directly (small inputs / tests).
        file_results = _extract_batch([str(p) for p in nl_paths], names)

    # Reduce phase: apply edges + report (serial, in the parent).
    # Perf (2026-08-26): load the dry-run existing-edge set ONCE here and
    # hand it to every apply_edges call below — was one full graph_edges
    # scan per note (~15ms × ~110 notes of identical work).
    existing_edges = _load_existing_edges(conn) if conn is not None and not args.apply else None
    for nl_path_str, doc_type, all_edges, unresolved, type_counts, ambiguities in file_results:
        nl_path = Path(nl_path_str)
        if doc_type == "sector":
            continue

        n_extracted = len(all_edges)
        total_extracted += n_extracted
        total_unresolved += len(unresolved)
        total_ambiguous += len(ambiguities)
        for et, cnt in type_counts.items():
            per_type_totals[et] = per_type_totals.get(et, 0) + cnt

        # Summary per newsletter. Print the path relative to the project root
        # when possible (cleaner output); fall back to the absolute path.
        try:
            display = str(nl_path.relative_to(_REPO_ROOT))
        except ValueError:
            display = str(nl_path)
        print(
            f"[{display}] ({doc_type}) extracted={n_extracted} "
            f"unresolved={len(unresolved)} "
            f"({'APPLY' if args.apply else 'dry-run'})",
            file=sys.stderr,
        )
        for et in sorted(type_counts):
            print(f"  {type_counts[et]:4d}  {et}", file=sys.stderr)

        if args.verbose:
            for e in all_edges:
                print(
                    f"  {e.edge_type:14s}  {e.source}  →  {e.target}  "
                    f"[{e.properties.get('edition', '')}]",
                    file=sys.stderr,
                )
            for u in unresolved:
                print(
                    f"  ? {u.edge_type:14s}  {u.source}  →  '{u.target_mention}'",
                    file=sys.stderr,
                )
            for mention, tied in ambiguities:
                # Tier-C audit line: fuzzy resolution hit an N-way tie.
                print(
                    f"  ~ ambiguous resolve: '{mention}' (equally scored: {', '.join(tied)})",
                    file=sys.stderr,
                )

        # Apply (or dry-run count).
        result = apply_edges(all_edges, conn=conn, dry_run=not args.apply, existing=existing_edges)
        total_applied += result.inserted
        total_skipped_fk += result.skipped_fk
        total_skipped_suppressed += result.skipped_suppressed

        # Sidecar.
        if args.write_sidecar and unresolved:
            write_sidecar(unresolved)

    # Commit once at the end if we applied.
    if args.apply:
        conn.commit()
    conn.close()

    print("", file=sys.stderr)
    print(
        f"TOTAL files={len(nl_paths)} extracted={total_extracted} "
        f"applied={total_applied} unresolved={total_unresolved}",
        file=sys.stderr,
    )
    if total_skipped_fk or total_skipped_suppressed:
        print(
            f"      skipped_fk={total_skipped_fk} skipped_suppressed={total_skipped_suppressed}",
            file=sys.stderr,
        )
    if total_ambiguous:
        print(f"      ambiguous_resolves={total_ambiguous}", file=sys.stderr)
    for et in sorted(per_type_totals):
        print(f"  {per_type_totals[et]:4d}  {et}", file=sys.stderr)

    if args.counts_json:
        import json

        payload = {
            "files": len(nl_paths),
            "per_type": dict(sorted(per_type_totals.items())),
            "total_edges": total_extracted,
            "total_unresolved": total_unresolved,
            "total_ambiguous": total_ambiguous,
        }
        Path(args.counts_json).write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"counts written to {args.counts_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
