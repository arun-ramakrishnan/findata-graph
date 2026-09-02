#!/usr/bin/env python3
"""Derive the ``quotes`` + ``company_metrics`` tables — the concall-body
capture layer that ``parse_newsletter.py`` defers to a manual Stage 4.

THE GAP
-------
``parse_newsletter.py:extract_companies`` reads only each company's section
*heading* line (`## Marico Ltd. | Large Cap | FMCG`). The entire concall body
— paraphrase bullets, verbatim quotes, ``— Name, Title`` attributions,
₹/%/bps figures — is never parsed. Stage 4 (``emit_worklist``) emits a
``{name, section_line}`` JSON pointer and defers all insight extraction to a
human/agent ("lift 3-5 bullets + 1 quote"). Result: only ~23% of company
notes carry any newsletter-derived content. This module is the deterministic
first pass that closes that gap.

WHAT IT CAPTURES
----------------
Two tables, one shared concall segmenter:

  * ``quotes`` — every verbatim executive quote with its speaker name + title
    + the paraphrase that preceded it. Speakers are plain string attributes
    (NOT entities) — this honors the D6 deferral (free-text rosters aren't
    structured enough for first-class person nodes); a quote row is the
    cheaper attribution carrier.
  * ``company_metrics`` — financial magnitudes (₹X crore / X% / X bps /
    $X bn / X GW) with verbatim provenance + best-effort ``metric_label``.
    The narrow capture arm of D1 (deferred full metrics layer); the
    newsletters are the recurring source that made D1's deferral moot.

THE STRUCTURE IT PARSES (verified across the 78-file The_Chatter corpus)
-----------------------------------------------------------------------
Each company section looks like::

    ## Marico Ltd. | Large Cap | FMCG
    <one-line business descriptor>

    ## [Concall]

    <paraphrase paragraph — the editor's 1-2 line summary>

    "<verbatim executive quote, often multi-line but one logical paragraph>"

    <attribution line — one of these observed forms:
        ## — Saugata Gupta, MD & CEO
        — Saugata Gupta, MD & CEO
        -Saugata Gupta, MD & CEO
        Badal Bagri, Group CFO            (bare; most common)
        - Suvankar Sen (MD & CEO)         (title in parens)
        - Mohit Malhotra                  (name only)
        ## Management, Executive          (anonymous/role-only)
    >

    <next paraphrase paragraph> ... (repeats)

The unit is (paraphrase → quote → attribution). The segmenter anchors on the
opening ``"`` of a quote and walks lines to the matching closing ``"``; the
attribution is the first non-blank, non-quote, non-OCR-garble line after.

WHAT IT WRITES BACK TO NOTES
----------------------------
For each company, the most recent edition's quotes are rendered into an
auto-managed ``## The Chatter — <edition>`` block in the company note, using
the established sentinel-marker convention (sync_sector_wikilinks.py):

    <!-- BEGIN auto chatter block (derive_insights.py) -->
    ## The Chatter — <edition>
    ...
    <!-- END auto chatter block -->

**Curation-safety rule (the critical correctness property):** if the note
already has a ``## The Chatter — <edition>`` heading that is NOT
sentinel-wrapped (i.e. hand/agent-written), the auto block for that edition
is SKIPPED — human work is never clobbered. Only sentinel-wrapped auto blocks
are refreshed on re-run.

NO LLM. Mirrors the derive_* / extract_relations.py conservative-deterministic
design (derive_themes.py:10 cites "no LLM dependency" as a design principle).

USAGE
-----
    python3 helpers/graph/derive_insights.py                 # dry-run
    python3 helpers/graph/derive_insights.py findata/The_Chatter/Marico_DLF_BSE.md
    python3 helpers/graph/derive_insights.py findata --apply  # write DB + notes
    python3 helpers/graph/derive_insights.py --verbose        # list every quote
    python3 helpers/graph/derive_insights.py findata --apply --stale-only
        # okf_activation I: render only notes whose evidence moved (a
        # sources[].last_modified newer than generated.at, or no sources
        # yet); the rest are skipped without reads/rewrites.
"""

from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# sys.path bootstrap so this works both as `python3 helpers/graph/...` (the
# Makefile form) and as a package import. Mirrors derive_events.py:54-56.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from helpers.core.corpus import Corpus  # S1b shared walk

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False

from helpers.core.db import connect  # noqa: E402
from helpers.core.stable_write import stable_prefix_replace  # noqa: E402
from helpers.core.edition_index import (  # noqa: E402
    _body,
    edition_source_entry,
    merged_sources,
    resolve_edition_string,
    source_note_index,
)
from helpers.core.frontmatter import (  # noqa: E402
    bump_generated,
    render_frontmatter,
    split_frontmatter,
    stringify_dates,
    yaml_safe_load,
)

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #
PROJECT_ROOT = _REPO_ROOT
COMPANIES_DIR = PROJECT_ROOT / "findata" / "Companies"
DB_PATH = PROJECT_ROOT / "memory" / "research.db"

# source_ref prefixes — the LIKE 'derive:quotes:%' / 'derive:metrics:%' scope
# in apply_quotes()/apply_metrics()'s stable replace clears derived rows on
# re-run while keeping id/created_at of content-identical rows (idempotency
# contract; manual:/migration: rows preserved). Mirror of derive_events.py:73.
QUOTES_PREFIX = "derive:quotes:"
METRICS_PREFIX = "derive:metrics:"

# Sentinel markers around the auto chatter block in company notes. Mirrors the
# sync_sector_wikilinks.py:60-61 convention (paired HTML comments, regex-replace
# for idempotency, curated sections outside the markers are never touched).
_BEGIN = "<!-- BEGIN auto chatter block (derive_insights.py) -->"

# OKF v0.2 actor string for the generated/stale_after bump on every auto
# block rewrite (okf_adoption.md §2.3). v-suffixed per the actor convention.
_OKF_ACTOR = "derive_insights.py/v1"
_END = "<!-- END auto chatter block -->"


def _iso_date(value) -> _dt.date | None:
    """Date part of an ISO string ('2026-08-15' or '2026-08-15T…Z'), or None."""
    if not isinstance(value, str):
        return None
    try:
        return _dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _stale_only_skip(text: str, scanned_stems: frozenset[str] = frozenset()) -> bool | None:
    """``--stale-only`` gate (okf_activation I). True=skip, False=render,
    None=no evidence (render anyway — safe default, accepted Q3).

    Skip iff the note was last rendered by THIS tool (``generated.by ==
    _OKF_ACTOR``; a ``process:okf_backfill`` stamp does NOT count — the
    first --stale-only run after the backfill re-renders every sourced
    note, then only notes whose ``sources[].last_modified`` moved past
    ``generated.at`` re-render after that) AND sources[] is non-empty AND
    no source is newer than the render date. Blocks can only change if
    quotes changed; quotes can only change if an edition changed; the
    edition's git add-date IS ``sources[].last_modified``.

    Gate amendment (okf_sources_maintenance §3.2b): ``scanned_stems`` are
    the source-note stems behind the editions THIS run scanned for the
    note. Any stem absent from sources[] forces a render — the splice that
    would add it only runs at render time, so skipping here would lock the
    note out forever while its evidence keeps moving.
    """
    opener, fm_text, _ = split_frontmatter(text)
    if not opener:
        return None
    try:
        fm = yaml_safe_load(fm_text)
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    gen = fm.get("generated")
    if not (isinstance(gen, dict) and gen.get("by") == _OKF_ACTOR):
        return None
    at = _iso_date(gen.get("at"))
    if at is None:
        return None
    srcs = fm.get("sources")
    if not isinstance(srcs, list) or not srcs:
        return None
    ids = {s.get("id") for s in srcs if isinstance(s, dict)}
    if any(stem not in ids for stem in scanned_stems):
        return False
    dates = [
        d
        for d in (_iso_date(s.get("last_modified")) for s in srcs if isinstance(s, dict))
        if d is not None
    ]
    if not dates:
        return None
    return max(dates) <= at


def _scanned_stems(editions, index: dict, memo: dict[str, str | None]) -> frozenset[str]:
    """Scanned edition free-text keys -> source-note stems (§3.2b input).

    Unmatchable editions (legacy free-text, "Yahoo Finance", ...) resolve
    to nothing and are ignored — the gate only trusts stems the index can
    see. ``memo`` is per-renderer-run: one edition key recurs across
    dozens of company notes.
    """
    stems: set[str] = set()
    for e in editions:
        if not e:
            continue
        if e not in memo:
            p = resolve_edition_string(e, index)
            memo[e] = p.stem if p is not None else None
        stem = memo[e]
        if stem:
            stems.add(stem)
    return frozenset(stems)


def _splice_sources(
    text: str,
    index: dict,
    vault: Path,
    extra_stems: frozenset[str] = frozenset(),
    memo: dict | None = None,
) -> tuple[str, bool]:
    """Merge edition entries into frontmatter ``sources[]`` (§3.2a).

    Body-driven via :func:`merged_sources` — auto-block ``## <series> —
    <edition>`` headings and ``*Source: …*`` footers resolve exactly like
    the OKF backfill's one-off pass. ``extra_stems`` covers evidence with
    no body footprint (key-figures metrics never name their editions).
    Existing entries are kept verbatim (Q2), no cap (Q3); never invents
    frontmatter and never touches a note with nothing to add. Returns
    ``(new_text, changed)``. ``memo`` caches edition-string resolution
    across calls (one edition key recurs across dozens of notes).
    """
    opener, fm_text, _ = split_frontmatter(text)
    if not opener:
        return (text, False)
    try:
        fm = yaml_safe_load(fm_text)
    except yaml.YAMLError:
        return (text, False)
    if not isinstance(fm, dict):
        return (text, False)
    merged = merged_sources(fm, text, index, vault, memo)
    have = {e.get("id") for e in merged if isinstance(e, dict)}
    for stem in sorted(extra_stems - have):
        p = resolve_edition_string(stem, index, memo)
        if p is not None and p.stem == stem:
            merged.append(edition_source_entry(p, vault))
    if not merged or merged == fm.get("sources"):
        return (text, False)
    fm["sources"] = merged
    return (render_frontmatter(stringify_dates(fm)) + _body(text), True)


# The H1 of an edition's `## The Chatter — <edition>` block. The capture group
# is the edition title (used by the curation-safety check to detect an existing
# hand-written block for the same edition).
_CHATTER_HEADING_RE = re.compile(r"^## The Chatter — (.+?)\s*$", re.MULTILINE)

# Frontmatter strip (quotes/metrics live in prose, not YAML). Same regex shape
# as derive_events.py:83.
_FM_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


# =========================================================================== #
# STAGE 1 — segment + extract                                                  #
# =========================================================================== #
# --- company-section heading detection -------------------------------------
# Reuse parse_newsletter's proven heading shape: `#{1,3} <name> [| Cap | Sector]`.
# We don't import extract_companies directly because it yields canonical names
# (Ltd stripped) and we need both the raw heading text AND the line range; the
# canonicalization is applied once at emit time via the same SUFFIX_RE.
_CAP_TOKENS = (
    "large cap",
    "mid cap",
    "small cap",
    "micro cap",
    "nano cap",
    "mega cap",
    "unlisted",
)
# P3 perf: compiled hot-path regexes (were inline re.search/re.match per call).
_CONCALL_HEADING_RE = re.compile(r"^##\s*\[Concall\]\s*$", re.MULTILINE)
_CONCALL_SUBHEADING_RE = re.compile(r"^#{2,}\s*\[?Concall\]?\s*$", re.I)
# _unit_of patterns (called per-metric, ~5K times):
_U_CRORE_RE = re.compile(r"crore|\bcr\b")
_U_BN_RE = re.compile(r"\bbn\b|billion")
_U_MN_RE = re.compile(r"\bmn\b|million")
_U_GW_RE = re.compile(r"\bgw\b")
_U_MW_RE = re.compile(r"\bmw\b")
_CAP_CUT = re.compile(r"\s+(?:large|larg|mid|small|micro|nano|mega)\s*cap", re.I)
SUFFIX_RE = re.compile(r"\s+(Limited|Ltd\.?|Private|Pvt\.?|Inc\.?|Corp\.?)$", re.I)
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
# P3 perf: compiled heading classifiers for iter_company_sections (were inline
# re.match per heading per file — 2834 section yields × 3-4 calls each).
_ATTR_DASH_RE = re.compile(r"^(##\s+)?[-–—]\s")
_ATTR_DASH_CAP_RE = re.compile(r"^(##\s+)?[-–—][A-Z]")
_ROLE_HEADING_RE = re.compile(r"^(management|executive|leadership|board)\b")
# Also pre-compiled for _canonicalize (was inline re.sub per call).
_WS_CANON_RE = re.compile(r"\s+")
_SUFFIX2_RE = re.compile(r"\s+(Limited|Ltd\.?|Private|Pvt\.?)$", re.I)
# H1 title extraction in _edition_title (was inline re.match per newsletter).
_H1_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Quote:
    """A derived quote row (mirrors the quotes table columns)."""

    entity: str
    quote_text: str
    paraphrase: str | None = None
    speaker_name: str | None = None
    speaker_title: str | None = None
    as_of_edition: str | None = None
    source_ref: str = QUOTES_PREFIX
    properties: dict = field(default_factory=dict)


@dataclass
class Metric:
    """A derived company_metrics row."""

    entity: str
    value_raw: str
    metric_label: str | None = None
    value_num: float | None = None
    unit: str | None = None
    period: str | None = None
    as_of_edition: str | None = None
    source_quote: str | None = None
    source_ref: str = METRICS_PREFIX
    properties: dict = field(default_factory=dict)


@dataclass
class CompanySection:
    """A company's concall body slice + its canonical name."""

    canonical_name: str
    heading_line: int  # 1-based line of the heading in the source file
    body: str  # the section's text (heading through next heading)


def _canonicalize(raw: str) -> str:
    """Strip legal suffixes + collapse whitespace -> the entities.name shape.

    Mirrors parse_newsletter.py:242-249 (SUFFIX_RE + Ltd/Private strip).
    """
    name = raw.split("|")[0].strip()
    cut = _CAP_CUT.search(name)
    if cut:
        name = name[: cut.start()].strip()
    name = _WS_CANON_RE.sub(" ", name).strip()
    canonical = SUFFIX_RE.sub("", name).strip()
    canonical = _SUFFIX2_RE.sub("", canonical).strip()
    return canonical


def iter_company_sections(content: str):  # noqa: C901
    """Yield ``CompanySection`` for each company heading in a newsletter.

    Slices each section's body from its heading line to the next COMPANY or
    SECTOR heading — NOT every heading. A concall body is full of internal
    sub-headings (``## [Concall]``, ``## — Attribution``, ``## Management``)
    that must stay inside the slice; only structural boundaries (the next
    company `## Foo | Cap | Sector` or sector `## FMCG`) terminate it.
    """
    # Precompute newline offsets for O(log n) line-number lookup.
    nl_offsets = [i for i, c in enumerate(content) if c == "\n"]

    def line_of(pos: int) -> int:
        return bisect.bisect_right(nl_offsets, pos) + 1

    matches = list(_HEADING_RE.finditer(content))
    # Classify each heading: is it a STRUCTURAL boundary (company or sector)?
    # A structural heading either carries a cap token/pipe (company) or is a
    # bare sector word (FMCG, Real Estate, etc.). Sub-headings inside a concall
    # block (## [Concall], ## — Name, Title, ## Management) are NOT structural.
    # P3 perf: cache classification in the first pass so the second pass
    # doesn't recompute lower/has_cap/has_pipe/canonical per heading.
    structural: list[tuple[int, int, str | None]] = []
    #                  ^start, ^idx, ^canonical (None = sector heading)
    for idx, m in enumerate(matches):
        raw = m.group(2).strip()
        lower = raw.lower()
        has_cap = any(tok in lower for tok in _CAP_TOKENS)
        has_pipe = "|" in raw
        # Sub-headings inside a concall block: ## [Concall], ## — Attribution,
        # ## Management... These are NOT structural boundaries.
        if lower.startswith("[concall]"):
            continue
        if _ATTR_DASH_RE.match(raw) or _ATTR_DASH_CAP_RE.match(raw):
            # An attribution heading like "## — Saugata Gupta, MD & CEO".
            continue
        # Bare role heading "## Management" / "## Management, Executive".
        if _ROLE_HEADING_RE.match(lower):
            continue
        # Newsletter chrome (## Comments, ## Discussion, ## Don't have a...).
        if lower.startswith(
            ("comment", "discussion", "don't", "share this", "subscribe", "about ", "welcome")
        ):
            continue
        if not (has_cap or has_pipe):
            # Sector heading (FMCG) — structural boundary.
            structural.append((m.start(), idx, None))
            continue
        canonical = _canonicalize(raw)
        if not canonical or len(canonical) < 3:
            continue
        structural.append((m.start(), idx, canonical))

    # Now walk the structural headings; each company's body runs to the next
    # structural heading (company or sector).
    for si, (start, idx, canonical) in enumerate(structural):
        if canonical is None:
            continue  # this structural heading is a sector, not a company
        # Body runs to the next structural heading, or EOF.
        end = structural[si + 1][0] if si + 1 < len(structural) else len(content)
        yield CompanySection(
            canonical_name=canonical,
            heading_line=line_of(start),
            body=content[start:end],
        )


# --- quote/attribution extraction ------------------------------------------
# Attribution line shapes observed in the corpus (see module docstring + the
# attribution survey: ~80% of quotes have an attribution within 2 lines).
# Forms, in descending frequency:
#   Badal Bagri, Group CFO                  (bare Name, Title)
#   — Saugata Gupta, MD & CEO               (em-dash / hyphen prefix)
#   -Saugata Gupta, MD & CEO                (hyphen, no space)
#   ## — Saugata Gupta, MD & CEO            (markdown heading + dash)
#   - Suvankar Sen (MD & CEO)               (title in parens)
#   - Mohit Malhotra                        (name only)
#   ## Management, Executive                (anonymous/role-only — no quote speaker)
# The name is 2-4 capitalized tokens; tolerant of single-letter initials
# (K. / T. V. / K — Indian names like "K Krithivasan" lead with an initial).
_NAME = r"[A-Z][\w.\-]*(?:\s+[A-Z][\w.\-]*){0,4}"
# Match the attribution line; capture group 1 = name, 2 = title-after-comma,
# 3 = title-in-parens. Anchored to line start; tolerates leading dash/heading.
_ATTR_RE = re.compile(r"^(?:##\s+)?[-–—]?\s*(" + _NAME + r")\s*(?:,\s*(.+?)|\s*\((.+?)\))?\s*$")
# Generic role headings that look like attributions but carry no person —
# these mark the quote as anonymous (speaker stays NULL). Checked BEFORE the
# non-attribution filter so `## Management, Executive` is recognized as an
# anonymous attribution, not rejected as a generic heading.
_ROLE_ONLY_RE = re.compile(
    r"^(?:##\s+)?[-–—]?\s*(Management|Executive[s]?|Leadership|Board|"
    r"Company Representative|Management,.+|Spokesperson)\s*$",
    re.I,
)
# Lines that are NOT attributions — prose, the next quote, OCR garble, section
# breaks. Used to decide when to stop scanning for an attribution after a quote.
# NOTE: a `## — Name, Title` attribution HEADING must not be rejected here, so
# the `#{2,}` alternative is scoped to headings WITHOUT a leading dash (a bare
# `## FMCG` sector heading IS a non-attribution; `## — Saugata Gupta` is not).
# `## Management` is handled by _ROLE_ONLY_RE (checked first).
_NOT_ATTR_RE = re.compile(
    r'^(?:"|##(?!\s*[-–—])|#{3,}|---|\*Source|!\[\[|<|http|www\.)',
    re.I,
)


def _parse_attribution(line: str) -> tuple[str | None, str | None] | None:
    """Parse an attribution line into ``(name, title)``.

    Returns ``(name, title)`` (either may be None), or ``None`` if the line is
    not an attribution. Role-only headings (``## Management, Executive``) return
    ``(None, None)`` — they signal "anonymous quote" rather than no attribution.
    """
    s = line.strip()
    if not s or len(s) > 100:
        return None
    # Role-only / anonymous: signal "anonymous quote" explicitly. Checked
    # BEFORE the non-attribution filter so `## Management, Executive` isn't
    # rejected as a generic heading.
    if _ROLE_ONLY_RE.match(s):
        return (None, None)
    if _NOT_ATTR_RE.match(s):
        return None
    m = _ATTR_RE.match(s)
    if not m:
        return None
    name = m.group(1).strip()
    title = (m.group(2) or m.group(3) or "").strip() or None
    # Require >=2 name tokens (reject single-word false hits like "Revenue,").
    # BUT allow a single-letter-initial + surname shape ("K Krithivasan").
    parts = name.split()
    if len(parts) < 2:
        return None
    # Reject obvious prose that slipped through ("The company said...").
    first = parts[0].lower()
    if first in {
        "the",
        "this",
        "these",
        "those",
        "we",
        "our",
        "while",
        "after",
        "before",
        "following",
        "despite",
        "according",
    }:
        return None
    return (name, title)


def _find_attribution(lines: list[str], start: int) -> tuple[int, tuple | None]:
    """Find the attribution line after a closing quote.

    Walks forward from ``start`` (the index AFTER the closing-quote line),
    skipping blanks. Returns ``(index, attribution)`` where attribution is
    ``(name, title)`` or ``(None, None)`` (anonymous), or ``(-1, None)`` if no
    attribution-shaped line is found before the next quote/heading/prose.
    """
    for j in range(start, min(start + 3, len(lines))):
        nj = lines[j].strip()
        if not nj:
            continue
        attr = _parse_attribution(nj)
        if attr is not None:
            return (j, attr)
        # Hit a non-attribution line (next quote / heading / prose) — stop.
        return (-1, None)
    return (-1, None)


# Local-engine (pdf_conv_md.py/pymupdf4llm) editions italicize every
# physical line, so a quote arrives as `_"first line_` … `_closing line."_`
# with the attribution as `_— Speaker, Title_`, and they use typographic
# quotes (`“…”`) where Paddle-era notes use ASCII `"`. One outer emphasis
# pair is unwrapped and curly quotes normalized to ASCII per line before
# the quote walker, so both engines feed it the same shapes. `___`
# horizontal rules are excluded (skip-listed below, not emphasis).
_LINE_EMPH_RE = re.compile(r"^\s*_(.+)_\s*$")
_CURLY_QUOTES = {"“": '"', "”": '"', "„": '"', "‟": '"'}


def extract_quotes(  # noqa: C901  # noqa anchor moved to the statement's diagnostic line (ruff-format split)
    section: CompanySection,
    edition_title: str,
    source_stem: str,
) -> list[Quote]:
    """Extract every (paraphrase → quote → attribution) unit from a section.

    Algorithm: walk lines; when a verbatim quote opens (line starts with ``"``),
    accumulate until the matching closing ``"``. The text between the previous
    quote (or section start) and this quote is the paraphrase. The attribution
    is found via ``_find_attribution`` immediately after the closing quote.
    """
    quotes: list[Quote] = []
    # Restrict to the [Concall] block if present (skip the business descriptor).
    body = section.body
    concall_m = _CONCALL_HEADING_RE.search(body)
    if concall_m:
        body = body[concall_m.end() :]

    lines = body.splitlines()
    # Unwrap per-line emphasis and normalize typographic quotes (see
    # _LINE_EMPH_RE) so local-engine quotes anchor on ASCII `"`.
    for idx, ln in enumerate(lines):
        if ln.strip() != "___":
            m = _LINE_EMPH_RE.match(ln)
            if m:
                ln = m.group(1)
        if "“" in ln or "”" in ln:
            for curly, ascii_q in _CURLY_QUOTES.items():
                ln = ln.replace(curly, ascii_q)
        lines[idx] = ln
    paraphrase_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # A verbatim quote opens with a `"` and is long enough to be a quote
        # (not a stray quoted phrase). It may close on the same line or span
        # multiple lines.
        if stripped.startswith('"') and len(stripped) > 40:
            # Accumulate the quote until the line that closes it (ends with `"`).
            quote_lines = [stripped]
            j = i
            # Same-line close?
            if stripped.endswith('"') and len(stripped) > 1:
                pass  # single-line quote
            else:
                j += 1
                while j < len(lines):
                    nxt = lines[j].rstrip()
                    quote_lines.append(nxt)
                    if nxt.endswith('"'):
                        break
                    j += 1
            quote_text = "\n".join(quote_lines).strip().strip('"').strip()
            paraphrase = "\n".join(paraphrase_lines).strip() or None
            # Collapse whitespace in the paraphrase for cleaner storage.
            if paraphrase:
                paraphrase = _PARAPHRASE_WS_RE.sub(" ", paraphrase)
            # Find the attribution after the closing quote.
            attr_idx, attr = _find_attribution(lines, j + 1)
            if attr is not None:
                speaker_name, speaker_title = attr
            else:
                speaker_name, speaker_title = None, None
            # Skip attribution-only / empty quotes.
            if len(quote_text) < 30:
                paraphrase_lines = []
                i = j + 1
                continue
            quotes.append(
                Quote(
                    entity=section.canonical_name,
                    quote_text=quote_text,
                    paraphrase=paraphrase,
                    speaker_name=speaker_name,
                    speaker_title=speaker_title,
                    as_of_edition=edition_title,
                    source_ref=f"{QUOTES_PREFIX}{source_stem}:{section.heading_line}",
                )
            )
            # Reset paraphrase accumulator; resume after the attribution (if any)
            # or after the closing quote.
            i = (attr_idx + 1) if attr_idx >= 0 else (j + 1)
            paraphrase_lines = []
        else:
            # Accumulate paraphrase (skip blank headings, OCR garble, image
            # embeds, and stray horizontal rules that leak in from the source).
            # NOTE: every branch here MUST fall through to `i += 1` at the
            # bottom — an early `continue` without advancing i is an infinite
            # loop (the line that triggered it is re-read forever).
            if stripped and not stripped.startswith(("!", "<", "http", "www.")):
                if stripped not in ("---", "***", "___"):
                    if not _CONCALL_SUBHEADING_RE.match(stripped):
                        paraphrase_lines.append(stripped)
            i += 1
    return quotes


# --- magnitude extraction --------------------------------------------------
# Reuse the proven precision-guard shape from backfill_magnitudes.py: require a
# money/percent unit (kills bare `₹4,400` revenue fragments) and reject
# non-financial contexts (employees, count of stores, etc.).
# Standalone money: ₹/Rs./INR followed by digits + a unit word. The corpus
# uses both the ₹ symbol and the "Rs." / "INR" prefix; both must match. Note
# `Rs.2,75,972` has no space between the period and the digit, so the space
# after the prefix is optional (rs\.?\s*).
_INR_RE = re.compile(
    r"(?:₹\s?|rs\.?\s*|inr\s*)[\d,]+(?:\.\d+)?\s*"
    r"(?:crore[s]?|cr\b|lakh[s]?|bn\b|mn\b|billion|million)",
    re.I,
)
_USD_RE = re.compile(
    r"(?:usd\s|\$)\s?[\d,]+(?:\.\d+)?\s*(?:bn\b|mn\b|billion|million)",
    re.I,
)
_PCT_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*[-–to ]+\s*\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*%")
_BPS_RE = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s*[-–to ]+\s*\d+\s*(?:bps|basis points)|\b\d+(?:\.\d+)?\s*(?:bps|basis points)",
    re.I,
)
_GW_MW_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:gw|mw)\b", re.I)
# Multiples (5.3x debt-to-equity, 2.0x net debt/EBITDA). Only captured when a
# ratio context word is within ~25 chars — the bare "Nx" in prose is too often
# figurative ("100x demand", "10x productivity", "3x more productive") to
# capture unconditionally. The context gate keeps the financial-ratio uses.
_MULTIPLE_CONTEXT_RE = re.compile(
    r"(?:debt|ebitda|equity|turnover|multiple|times|ratio|cover|lever(?:age|ed)?)",
    re.I,
)
_MULTIPLE_RE = re.compile(r"\b\d+(?:\.\d+)?x\b")  # matched, then context-gated

# P3 perf: compiled patterns used in hot loops (were inline re.search/re.split
# per call — 50K+ invocations in extract_metrics alone).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_PARAPHRASE_WS_RE = re.compile(r"\s+")
# Pre-compiled label classifiers for _label_from_window (were inline re.search).
_L_MARGIN = re.compile(r"\bmargin[s]?\b|\bebitda\b|\boperating margin\b")
_L_EBITDA = re.compile(r"\bebitda\b")
_L_REVENUE = re.compile(r"\brevenue\b|\bturnover\b|\bnet sales\b|\btopline\b|\btop line\b")
_L_PROFIT = re.compile(r"\bprofit\b|\bpat\b|\bnet income\b|\bearnings\b")
_L_CAPEX = re.compile(r"\bcapex\b|\bcapital expenditure\b|\bspend\b|\bsanctioned\b")
_L_AUM = re.compile(r"\baum\b|assets under management")
_L_ORDER = re.compile(r"\border book\b|\border pipeline\b|\bbid pipeline\b")
_L_GROWTH = re.compile(r"\bgrowth\b|\byoy\b|\bcagr\b")
_L_MKT_SHARE = re.compile(r"\bmarket share\b")
_L_STAKE = re.compile(r"\bstake\b")
_L_DEBT = re.compile(r"\bdebt\b|\bborrowings?\b|\bnet debt\b")

# Period tokens (FY / quarter / month-year) — reuse the derive_events shape.
_FY_RE = re.compile(r"\b(?:Q[1-4]\s*)?FY\s?\d{2,4}\b|\bQ[1-4]\s*CY\s?\d{2,4}\b", re.I)
_MONTH_YEAR_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d{2})\b",
    re.I,
)

# Non-financial contexts to reject (precision guard: these are counts/employees,
# not money magnitudes — the dominant false-positive class in the corpus).
_NON_FINANCIAL_CONTEXT = re.compile(
    r"\b(employees?|stores?|outlets?|branches?|customers?|users?|subscribers?|"
    r"people|passengers?|units?|vehicles?|cars?|two-wheelers?|"
    r"stores opened|cities|countries|lives|beds|mw installed|screens?)\b",
    re.I,
)


def _label_from_window(w: str) -> str | None:  # noqa: C901
    """Classify a lowered text window to a metric label. Returns None if no
    metric noun is present."""
    if _L_MARGIN.search(w):
        return "ebitda_margin" if _L_EBITDA.search(w) else "margin"
    if _L_REVENUE.search(w):
        return "revenue"
    if _L_PROFIT.search(w):
        return "profit"
    if _L_CAPEX.search(w):
        return "capex"
    if _L_AUM.search(w):
        return "aum"
    if _L_ORDER.search(w):
        return "order_book"
    if _L_GROWTH.search(w):
        return "growth"
    if _L_MKT_SHARE.search(w):
        return "market_share"
    if _L_STAKE.search(w):
        return "stake"
    if _L_DEBT.search(w):
        return "debt"
    return None


def _classify_metric(sentence: str, value_raw: str, figure_pos: int) -> str | None:
    """Best-effort metric_label from the context AROUND the figure.

    The metric noun usually PRECEDES its figure in English ("Revenue grew
    23%", "EBITDA margin expanded 140bps"), so we classify from the preceding
    ~30 chars first and only fall back to a following window if that's empty.
    This keeps "Revenue grew 23% and EBITDA margin..." from mislabeling the
    23% as ebitda_margin. The verbatim ``source_quote`` is the ground truth;
    ``metric_label`` is a nullable convenience for grouping.
    """
    # Prefer the preceding window (noun before the number).
    pre_start = max(0, figure_pos - 30)
    pre = sentence[pre_start:figure_pos].lower()
    label = _label_from_window(pre)
    if label:
        return label
    # Fall back to a small following window ("X% margin", "X% growth").
    post_end = min(len(sentence), figure_pos + len(value_raw) + 25)
    post = sentence[figure_pos:post_end].lower()
    return _label_from_window(post)


def _parse_value_num(value_raw: str, unit: str | None) -> float | None:
    """Extract a comparable numeric from a magnitude string (range lower bound)."""
    nums = re.findall(r"[\d,]+(?:\.\d+)?", value_raw.replace("–", "-"))
    if not nums:
        return None
    try:
        return float(nums[0].replace(",", ""))
    except ValueError:
        return None


def _unit_of(value_raw: str) -> str | None:
    v = value_raw.lower()
    if _U_CRORE_RE.search(v):
        return "crore"
    if "lakh" in v:
        return "lakh"
    if "bps" in v or "basis point" in v:
        return "bps"
    if "%" in v:
        return "percent"
    if _U_BN_RE.search(v) and ("$" in v or "usd" in v):
        return "bn_usd"
    if _U_MN_RE.search(v) and ("$" in v or "usd" in v):
        return "mn_usd"
    if _U_GW_RE.search(v):
        return "gw"
    if _U_MW_RE.search(v):
        return "mw"
    if v.endswith("x"):
        return "x"
    return None


def extract_metrics(  # noqa: C901  # noqa anchor moved to the statement's diagnostic line (ruff-format split)
    section: CompanySection,
    edition_title: str,
    source_stem: str,
) -> list[Metric]:
    """Extract financial magnitudes from a section's concall prose.

    Scans each quote + paraphrase for ₹/$/INR/USD + unit, %, bps, GW/MW, and
    multiples. Applies the non-financial-context reject filter (employees,
    stores, units) to kill the dominant false-positive class.
    """
    metrics: list[Metric] = []
    body = section.body
    concall_m = _CONCALL_HEADING_RE.search(body)
    if concall_m:
        body = body[concall_m.end() :]
    # Split into sentence-ish windows (newlines first, then sentence boundaries).
    seen_spans: set[tuple[str, str]] = set()
    for line in body.splitlines():
        line = line.strip()
        if not line or len(line) < 15:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip()
            if len(sentence) < 12:
                continue
            # Reject non-financial contexts (counts, employees, stores).
            if _NON_FINANCIAL_CONTEXT.search(sentence):
                continue
            for pat in (_INR_RE, _USD_RE, _BPS_RE, _PCT_RE, _GW_MW_RE, _MULTIPLE_RE):
                for m in pat.finditer(sentence):
                    value_raw = m.group(0).strip()
                    # Multiples need a ratio-context gate (debt/EBITDA/equity/...)
                    # to exclude figurative "Nx" uses ("100x demand", "3x growth").
                    if pat is _MULTIPLE_RE:
                        ctx_start = max(0, m.start() - 25)
                        ctx_end = min(len(sentence), m.end() + 25)
                        if not _MULTIPLE_CONTEXT_RE.search(sentence[ctx_start:ctx_end]):
                            continue
                    # De-dup identical (value, sentence) within one section.
                    key = (value_raw, sentence[:60])
                    if key in seen_spans:
                        continue
                    seen_spans.add(key)
                    unit = _unit_of(value_raw)
                    period_m = _FY_RE.search(sentence) or _MONTH_YEAR_RE.search(sentence)
                    period = period_m.group(0).strip() if period_m else None
                    metrics.append(
                        Metric(
                            entity=section.canonical_name,
                            value_raw=value_raw,
                            metric_label=_classify_metric(sentence, value_raw, m.start()),
                            value_num=_parse_value_num(value_raw, unit),
                            unit=unit,
                            period=period,
                            as_of_edition=edition_title,
                            source_quote=sentence,
                            source_ref=f"{METRICS_PREFIX}{source_stem}:{section.heading_line}",
                        )
                    )
    return metrics


# =========================================================================== #
# STAGE 2 — edition title + entity resolution                                 #
# =========================================================================== #
def _edition_title(stem: str, content: str) -> str:
    """Derive the edition title for ``as_of_edition`` + the note heading.

    Prefers the newsletter's H1 (the documented convention, markdown_parse.md);
    falls back to the filename stem with underscores/spaces normalized.
    """
    body = _FM_RE.sub("", content, count=1)
    h1 = _H1_TITLE_RE.match(body)
    if h1:
        title = h1.group(1).strip()
        # Drop a trailing edition tag if the H1 is just the newsletter name.
        if 3 <= len(title) <= 80:
            return title
    return stem.replace("_", " ").strip()


def _resolve_entities(conn, sections: list[CompanySection]) -> dict[str, str]:
    """Map each section's canonical_name -> the actual entities.name row.

    The parser canonicalizes headings (strips Ltd/Limited); the DB may hold the
    entity under a slightly different name. Resolve case-insensitively, then by
    normalized_name, so quotes/metrics land on the right entity row.
    """
    if not sections:
        return {}
    names = {s.canonical_name for s in sections}
    # Exact (case-insensitive) match.
    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT name FROM entities WHERE name COLLATE NOCASE IN ({placeholders}) "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        f"AND entity_type='company'",
        tuple(names),
    ).fetchall()
    resolved: dict[str, str] = {}
    matched_db_names = {r["name"] for r in rows}
    # Build a case-insensitive lookup from canonical -> db name.
    db_by_lower = {n.lower(): n for n in matched_db_names}
    for cn in names:
        if cn.lower() in db_by_lower:
            resolved[cn] = db_by_lower[cn.lower()]
    # Resolve the rest via normalized_name (the entities-table sync key).
    unresolved = names - set(resolved.keys())
    if unresolved:
        placeholders = ",".join("?" for _ in unresolved)
        rows = conn.execute(
            f"SELECT normalized_name, name FROM entities "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
            f"WHERE normalized_name COLLATE NOCASE IN ({placeholders}) "
            f"AND entity_type='company'",
            tuple(unresolved),
        ).fetchall()
        norm_to_name = {(r["normalized_name"] or "").lower(): r["name"] for r in rows}
        for cn in list(unresolved):
            if cn.lower() in norm_to_name:
                resolved[cn] = norm_to_name[cn.lower()]
    return resolved


# =========================================================================== #
# STAGE 3 — persist                                                            #
# =========================================================================== #
def _edition_stem(edition: str | None, index: dict | None, memo: dict[str, str]) -> str | None:
    """Canonical edition STEM for a display-title ``as_of_edition`` value.

    No index (tests / direct callers) or unresolvable title -> the value is
    stored verbatim (the honest-miss discipline of the OKF backfill).
    """
    if edition is None or index is None:
        return edition
    cached = memo.get(edition)
    if cached is not None:
        return cached
    p = resolve_edition_string(edition, index)
    stem = p.stem if p is not None else edition
    memo[edition] = stem
    return stem


_INSERT_QUOTE_SQL = """
INSERT INTO quotes
    (entity, quote_text, paraphrase, speaker_name, speaker_title,
     as_of_edition, source_ref, properties)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""
_INSERT_METRIC_SQL = """
INSERT INTO company_metrics
    (entity, metric_label, value_raw, value_num, unit, period,
     as_of_edition, source_quote, source_ref, properties)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_QUOTE_CONTENT_COLS = (
    "entity",
    "quote_text",
    "paraphrase",
    "speaker_name",
    "speaker_title",
    "as_of_edition",
    "source_ref",
    "properties",
)
_METRIC_CONTENT_COLS = (
    "entity",
    "metric_label",
    "value_raw",
    "value_num",
    "unit",
    "period",
    "as_of_edition",
    "source_quote",
    "source_ref",
    "properties",
)


def _stable_prefix_replace(
    conn, table: str, prefix: str, cols: tuple[str, ...], insert_sql: str, new_rows: list[tuple]
) -> int:
    """Prefix-scoped replace preserving id/created_at of unchanged rows.

    Thin alias of ``helpers.core.stable_write.stable_prefix_replace``
    (extracted 2026-08-22 when derive_events adopted the same contract);
    kept as a private name so the apply_quotes/apply_metrics call sites
    and their tests are untouched.
    """
    return stable_prefix_replace(conn, table, prefix, cols, insert_sql, new_rows)


def apply_quotes(
    quotes: list[Quote], *, conn=None, dry_run: bool = True, index: dict | None = None
) -> int:
    """Persist quotes — prefix-scoped stable replace of derived rows.

    Hand-seeded rows (``manual:`` / other prefixes) are preserved, and a
    re-apply with unchanged content keeps every row's id and created_at
    (see ``_stable_prefix_replace``). With ``index`` (edition_index
    norm-key map), the stored ``as_of_edition`` is the canonical edition
    STEM (okf_activation F0 — joinable to ``entities.name`` /
    ``sources[].id``); unresolvable titles are stored verbatim (honest
    miss). The in-memory field keeps the display title — render headings
    key on it.
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()
    stem_memo: dict[str, str] = {}
    try:
        if dry_run:
            return len(quotes)
        new_rows = [
            (
                q.entity,
                q.quote_text,
                q.paraphrase,
                q.speaker_name,
                q.speaker_title,
                _edition_stem(q.as_of_edition, index, stem_memo),
                q.source_ref,
                json.dumps(q.properties, ensure_ascii=False, sort_keys=True),
            )
            for q in quotes
        ]
        with conn:
            return _stable_prefix_replace(
                conn, "quotes", QUOTES_PREFIX, _QUOTE_CONTENT_COLS, _INSERT_QUOTE_SQL, new_rows
            )
    finally:
        if own_conn:
            conn.close()


def apply_metrics(
    metrics: list[Metric], *, conn=None, dry_run: bool = True, index: dict | None = None
) -> int:
    """Persist company_metrics — prefix-scoped stable replace of derived
    rows (same contract as ``apply_quotes``)."""
    own_conn = conn is None
    if own_conn:
        conn = connect()
    stem_memo: dict[str, str] = {}
    try:
        if dry_run:
            return len(metrics)
        new_rows = [
            (
                m.entity,
                m.metric_label,
                m.value_raw,
                m.value_num,
                m.unit,
                m.period,
                _edition_stem(m.as_of_edition, index, stem_memo),
                m.source_quote,
                m.source_ref,
                json.dumps(m.properties, ensure_ascii=False, sort_keys=True),
            )
            for m in metrics
        ]
        with conn:
            return _stable_prefix_replace(
                conn,
                "company_metrics",
                METRICS_PREFIX,
                _METRIC_CONTENT_COLS,
                _INSERT_METRIC_SQL,
                new_rows,
            )
    finally:
        if own_conn:
            conn.close()


# =========================================================================== #
# STAGE 4 — render auto blocks into company notes                             #
# =========================================================================== #
def render_chatter_block(
    edition: str, quotes: list[Quote], index: dict | None = None, memo: dict | None = None
) -> str:
    """Render the auto ``## The Chatter — <edition>`` markdown block.

    Shape mirrors the documented edition block (markdown_parse.md:310) so it
    renders identically to a human-written block; the sentinel HTML comments
    are invisible in Obsidian.

    With ``index`` (edition_index map), each quote attribution carries a
    per-claim footnote (okf_readside N1): ``— Name, Title [^chatter-<stem>]``
    plus one definition inside the block —
    ``[^chatter-<stem>]: <edition title> — [[<stem>]]``. IDs are
    ``chatter-``-namespaced so hand-written footnotes can never collide; an
    unresolvable edition gets NO footnotes (honest miss, same discipline as
    the sources splice). ``memo`` is the shared resolve_edition_string cache.
    """
    stem: str | None = None
    if index is not None:
        p = resolve_edition_string(edition, index, memo)
        if p is not None:
            stem = p.stem
    footnote = f" [^chatter-{stem}]" if stem else ""
    lines = [_BEGIN, "", f"## The Chatter — {edition}", ""]
    lines.append(
        f"<!-- Auto-generated by derive_insights.py from the {edition} concall. "
        f"Edit the paraphrase/quote selection by replacing this block with a "
        f"hand-written `## The Chatter — {edition}` section (this sentinel-"
        f"wrapped block is refreshed on each `--apply` run of this script). -->"
    )
    lines.append("")
    for q in quotes:
        if q.paraphrase:
            # Ellipsis INSIDE the emphasis: the [:140] cut can land on a
            # space, and `text **…` detaches the closing marker (broken
            # emphasis, md-lint MD037); `text…**` always closes cleanly.
            p_text = q.paraphrase[:140].rstrip()
            lines.append(f"- **{p_text}…**" if len(q.paraphrase) > 140 else f"- **{p_text}**")
            lines.append("")
        # Quote block (Obsidian blockquote).
        quote_display = q.quote_text if len(q.quote_text) <= 280 else (q.quote_text[:277] + "…")
        lines.append(f'> "{quote_display}"')
        if q.speaker_name or q.speaker_title:
            parts = [p for p in (q.speaker_name, q.speaker_title) if p]
            lines.append(f"> — {', '.join(parts)}{footnote}")
        lines.append("")
    if stem:
        lines.append(f"[^chatter-{stem}]: {edition} — [[{stem}]]")
        lines.append("")
    lines.append(f"*Source: The Chatter — {edition}*")
    lines.extend(["", _END, ""])
    return "\n".join(lines)


def _auto_region_spans(text: str) -> list[tuple[int, int]]:
    """Maximal (start, end) spans of top-level auto-block regions.

    Stack-walks the BEGIN/END markers (mirrors
    enrich_from_yfinance._auto_region_spans) — a non-greedy regex pairs the
    outer BEGIN with the FIRST inner END whenever regions nest, which is
    how insertion points kept landing inside sibling regions.
    """
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    for m in _AUTO_MARKER_RE.finditer(text):
        if m.group(1) == "BEGIN":
            stack.append(m.start())
        elif stack:
            start = stack.pop()
            if not stack:  # outermost pair closed
                spans.append((start, m.end()))
        # END without BEGIN: corrupted note — ignore that marker
    return spans


def _outside_auto_regions(text: str, pos: int) -> int:
    """``pos`` moved before any auto-block region that contains it.

    Insertion points keyed on headings (``## The Chatter`` …) regularly
    resolve to a heading INSIDE an existing sentinel region — inserting
    there splits the region's BEGIN from its heading and stacks degenerate
    markers (2026-08-19: 66 notes). Inserting before the whole region
    keeps every block intact and adjacent.
    """
    for start, end in _auto_region_spans(text):
        if start <= pos < end:
            return start
    return pos


def _find_insertion_point(text: str) -> int:
    """Where to insert the auto chatter block in a company note.

    Before the first existing curated `## The Chatter` / `## Key Insights` /
    `## Management Insights` / `## Newsletter synthesis` heading if present
    (keeps the auto block adjacent to related content); otherwise at end.
    The position is bumped OUT of any auto region — a heading match inside
    a sibling region must not split it.
    """
    m = re.search(
        r"^## (The Chatter|Key Insights|Management Insights|Newsletter synthesis)",
        text,
        re.MULTILINE,
    )
    if m:
        return _outside_auto_regions(text, m.start())
    return len(text)


def _existing_hand_block_for_edition(text: str, edition: str) -> bool:
    """True iff a NON-sentinel `## The Chatter — <edition>` heading exists.

    The curation-safety gate: a hand/agent-written block for this edition means
    we skip the auto block entirely (never clobber human work).
    """
    # Strip sentinel-wrapped auto blocks first, then look for the heading.
    auto_pattern = re.compile(re.escape(_BEGIN) + r".*?" + re.escape(_END) + r"\n?", re.DOTALL)
    stripped = auto_pattern.sub("", text)
    m = _CHATTER_HEADING_RE.search(stripped)
    if not m:
        return False
    return m.group(1).strip().lower() == edition.strip().lower()


def _replace_or_insert_block(text: str, edition: str, new_block: str) -> tuple[str, bool]:
    """Refresh the sentinel-wrapped block for ``edition`` or insert a new one.

    Returns ``(new_text, changed)``. If a hand-written block for this edition
    exists, returns ``(text, False)`` (curation-safety: do not clobber).
    Replacing an existing chatter region first RESCUES any foreign
    auto-blocks nested inside it (key-figures / yfinance profile) and
    re-places them before this block — see :func:`_extract_nested_blocks`;
    unbalanced nested sentinels skip the replacement rather than risk
    destroying content.
    """
    if _existing_hand_block_for_edition(text, edition):
        return (text, False)
    # Replace any existing sentinel-wrapped block whose heading matches this
    # edition (refresh on re-run). The DOTALL match spans the whole block.
    pattern = re.compile(re.escape(_BEGIN) + r".*?" + re.escape(_END) + r"\n?", re.DOTALL)

    # If there's exactly one auto block, replace it only if it's for a
    # DIFFERENT edition (we'd otherwise stack duplicates). Simplest correct
    # behavior: replace the existing auto block iff its edition == this one;
    # if a different edition's auto block exists, append this one as new.
    def _edition_of_block(blk: str) -> str | None:
        hm = _CHATTER_HEADING_RE.search(blk)
        return hm.group(1).strip() if hm else None

    def _swap(m) -> tuple[str, bool]:
        rescued = _extract_nested_blocks(m.group(0))
        if rescued is None:
            return (text, False)  # unbalanced sentinels — never risk content
        replacement = (
            ("\n\n".join(b.rstrip() for b in rescued) + "\n\n" + new_block)
            if rescued
            else new_block
        )
        new_text = text[: m.start()] + replacement + text[m.end() :]
        if new_text == text:
            # Byte-identical re-render (idempotency guard #139): the note's
            # block already matches what the current renderer produces.
            return (text, False)
        return (new_text, True)

    matches = list(pattern.finditer(text))
    for m in matches:
        # Edition compare is strip/case-normalised like the hand-block gate
        # above: the heading regex's ``(.+?)\s*$`` captures the EMPTY string
        # for an all-whitespace edition char (\x85 NEL is Unicode \s), so a
        # raw == here made the renderer re-insert a fresh block on EVERY run
        # (unbounded duplication; found by test_fuzz_derive_insights_regions
        # 2026-08-22).
        ed_of_blk = _edition_of_block(m.group(0))
        if ed_of_blk is not None and ed_of_blk.strip().lower() == edition.strip().lower():
            return _swap(m)
    # No auto block for THIS edition: insert a new one. Other editions'
    # auto blocks are NEVER evicted (a note accumulates one block per
    # scanned edition) — the old matches[0] swap made the last-rendered
    # edition destroy a sibling's block whenever a note had quotes from
    # 2+ editions, silently dropping that edition's chatter AND starving
    # the sources splice, so --stale-only re-forced the render forever
    # (2026-08-19: the 31-note non-convergence). Repeated runs stay
    # idempotent: each edition replaces only its own block.
    idx = _find_insertion_point(text)
    prefix = text[:idx]
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n" if prefix.endswith("\n") else "\n\n"
    new_text = prefix + new_block + text[idx:]
    return (new_text, True)


def _markers_balanced(text: str) -> bool:
    """Equal BEGIN/END auto-marker counts (the cheap degenerate-structure
    invariant — stacked BEGINs with orphaned bodies keep counts equal, but
    a COUNT break always means the render mangled regions)."""
    kinds = [m.group(1) for m in _AUTO_MARKER_RE.finditer(text)]
    return kinds.count("BEGIN") == kinds.count("END")


def _balanced_or_skipped(original: str, new: str, name: str) -> bool:
    """Belt-and-suspenders write gate (2026-08-19): refuse a render that
    would break the auto-marker balance of a previously-balanced note."""
    if _markers_balanced(original) and not _markers_balanced(new):
        print(
            f"WARNING: {name}: render would leave unbalanced auto "
            f"markers — write skipped, note left unchanged.",
            file=sys.stderr,
        )
        return False
    return True


def _paths_by_entity(conn, entities: list[str]) -> dict[str, str]:
    """entity -> repo-relative note path (shared by both note renderers)."""
    if not entities:
        return {}
    placeholders = ",".join("?" for _ in entities)
    rows = conn.execute(
        f"SELECT name, file_path FROM entities WHERE name IN ({placeholders}) "  # noqa: S608  # parameterized; interpolated parts are `?`-clauses / schema-constant identifiers
        f"AND file_path IS NOT NULL",
        tuple(entities),
    ).fetchall()
    return {r["name"]: r["file_path"] for r in rows}


def render_notes(  # noqa: C901  # noqa anchor moved to the statement's diagnostic line (ruff-format split)
    quotes_by_entity_edition: dict,
    *,
    dry_run: bool = True,
    conn=None,
    stale_only: bool = False,
    index: dict | None = None,
) -> tuple[int, int, int]:
    """Render auto chatter blocks into company notes.

    ``quotes_by_entity_edition`` maps ``(entity_name, edition)`` -> list[Quote].
    For each entity, refreshes every edition block that has quotes, then
    splices newly referenced editions into frontmatter ``sources[]``
    (okf_sources_maintenance §3.2a). The splice runs even when every block
    is byte-identical — a note whose only delta is new sources gets the
    repair write (and its ``stale_after`` recompute) rather than forcing a
    full re-render on every subsequent run. Returns ``(written, skipped,
    gated)`` — notes written/would-write (block and/or sources change),
    edition blocks skipped because a hand-written block for that edition
    was preserved, and notes gated out by ``stale_only``.

    ``index`` is the edition_index norm-key -> source-note map; built from
    ``PROJECT_ROOT/findata`` when omitted.
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()
    written = 0
    skipped = 0
    gated = 0
    try:
        # Resolve entity -> file_path once.
        path_by_entity = _paths_by_entity(conn, list({e for e, _ in quotes_by_entity_edition}))
        if not path_by_entity:
            return (0, 0, 0)
        vault = PROJECT_ROOT / "findata"
        if index is None:
            index = source_note_index(vault)
        stem_memo: dict[str, str | None] = {}
        res_memo: dict[str, Path | None] = {}

        # Group quotes by entity, pick the latest edition per entity.
        by_entity: dict[str, dict[str, list[Quote]]] = {}
        for (entity, edition), qs in quotes_by_entity_edition.items():
            by_entity.setdefault(entity, {})[edition] = qs

        for entity, edict in by_entity.items():
            file_path = path_by_entity.get(entity)
            if not file_path:
                continue
            p = PROJECT_ROOT / file_path
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            original_text = text
            # --stale-only gate: one decision per NOTE (covers all its
            # edition blocks — the evidence basis is note-level sources[]).
            # A scanned edition missing from sources[] forces the render
            # (§3.2b): only the render-path splice can add it. A gated note
            # is NOT skipped outright: it falls through to the render loop
            # so renderer drift (e.g. the #137 footnotes) still propagates
            # — the byte-identical guard makes this a zero-write no-op when
            # the note is current, so the gate's "no churn" property holds.
            gated_candidate = (
                stale_only
                and _stale_only_skip(text, _scanned_stems(edict, index, stem_memo)) is True
            )
            # Render ALL editions that have quotes (one auto block per
            # edition scanned, so a note accumulates its edition history).
            text_changed = False
            for edition, qs in edict.items():
                if not qs:
                    continue
                # Dedup identical quotes within the edition (same quote_text).
                seen: set[str] = set()
                unique = []
                for q in qs:
                    if q.quote_text in seen:
                        continue
                    seen.add(q.quote_text)
                    unique.append(q)
                new_block = render_chatter_block(edition, unique, index, res_memo)
                text, changed = _replace_or_insert_block(text, edition, new_block)
                if changed:
                    text_changed = True
                else:
                    skipped += 1
            # Splice sources[] even when no block changed (convergence: a
            # gated-clean note with new evidence absorbs it here, once).
            text, sources_changed = _splice_sources(text, index, vault, memo=res_memo)
            if not (text_changed or sources_changed):
                if gated_candidate:
                    gated += 1
                continue
            if not _balanced_or_skipped(original_text, text, p.name):
                skipped += 1
                continue
            if dry_run:
                written += 1
            else:
                # OKF: the note changed -> bump generated/stale_after in the
                # note's frontmatter (bump recomputes stale_after from the
                # spliced sources; preserves verified + all other keys; no-op
                # when the note has no frontmatter).
                text = bump_generated(text, _OKF_ACTOR)
                if not text.endswith("\n"):
                    text += "\n"  # notes terminate with a newline (md-lint MD047)
                p.write_text(text, encoding="utf-8")
                written += 1
    finally:
        if own_conn:
            conn.close()
    return (written, skipped, gated)


# --- Key Figures (auto) block ---------------------------------------------
# A second sentinel-wrapped section per company note that surfaces the captured
# financial magnitudes. Separate sentinels from the chatter block so the two
# refresh independently. Like the chatter block, it never touches curated
# `## Financial Performance` / `## Key Metrics` sections (different heading).
_KF_BEGIN = "<!-- BEGIN auto key figures (derive_insights.py) -->"
_KF_END = "<!-- END auto key figures -->"
_KF_HEADING = "## Key Figures (auto)"
_KF_PATTERN = re.compile(re.escape(_KF_BEGIN) + r".*?" + re.escape(_KF_END) + r"\n?", re.DOTALL)

# Foreign auto-blocks can collide into a sibling renderer's sentinel region:
# enrich_from_yfinance shares the "after ## Company Overview" insertion anchor
# with both renderers, and the two derive_insights insertion heuristics can
# interleave so the key-figures region sits INSIDE the chatter sentinels. A
# naive BEGIN→END substitution then destroys everything nested in the region —
# the 2026-08-10 profile-stripping incident (KF side) and the 2026-08-19
# incident where the chatter renderer deleted the KF block + profile from 58
# notes (the metrics pass re-inserted an identical KF block, so only the
# profile was visibly lost). Both _replace_or_insert_* paths now rescue nested
# foreign blocks via _extract_nested_blocks before replacing a region.
_AUTO_MARKER_RE = re.compile(r"<!--\s*(BEGIN|END)\s+auto\b.*?-->", re.DOTALL)


def _extract_nested_blocks(region: str) -> list[str] | None:
    """Maximal complete auto-blocks nested inside one sentinel region.

    ``region`` is a renderer's full BEGIN→END span (own markers included).
    A stack walk pairs the markers; blocks closed while the region's own
    BEGIN is still open are direct children, returned verbatim with their
    own nested content intact (deeper pairs are contained in a returned
    span — never returned separately, never duplicated). Returns None when
    the nested markers are unbalanced: the caller must then skip the
    replacement rather than risk destroying content.
    """
    stack: list[int] = []
    blocks: list[str] = []
    for m in _AUTO_MARKER_RE.finditer(region):
        if m.group(1) == "BEGIN":
            stack.append(m.start())
        elif stack:
            start = stack.pop()
            if len(stack) == 1:  # direct child of the outermost region
                blocks.append(region[start : m.end()])
        else:
            return None  # END without BEGIN — unbalanced
    if stack:
        return None  # unclosed BEGIN
    return blocks


# Display order for metric labels (most-informative first); unmapped labels
# append alphabetically. value_raw is the display string.
_LABEL_ORDER = [
    "revenue",
    "profit",
    "ebitda_margin",
    "margin",
    "growth",
    "capex",
    "order_book",
    "aum",
    "debt",
    "market_share",
    "stake",
]


def render_key_figures_block(metrics: list[Metric]) -> str:
    """Render the auto ``## Key Figures (auto)`` markdown block.

    Groups the metrics by ``metric_label`` and dedups near-identical values
    within a label. Each line shows the value + period (if any). The block is
    a compact snapshot — the verbatim ``source_quote`` on each row is the
    ground truth for query/analytics; this block is the human-readable view.
    """
    # Group by label; None-labeled figures go under "(other)".
    by_label: dict[str, list[Metric]] = {}
    for m in metrics:
        key = m.metric_label or "(other)"
        by_label.setdefault(key, []).append(m)

    def _label_sort_key(lbl: str) -> tuple[int, str]:
        try:
            return (0, f"{_LABEL_ORDER.index(lbl):02d}")
        except ValueError:
            return (1, lbl)

    lines = [_KF_BEGIN, "", _KF_HEADING, ""]
    lines.append(
        f"<!-- Auto-generated by derive_insights.py from concall magnitudes. "
        f"{len(metrics)} figure(s) across {len(by_label)} metric(s). Refreshed "
        f"on each `--apply` run of this script; do not edit by hand. -->"
    )
    lines.append("")
    if not by_label:
        lines.append("_(No financial magnitudes captured yet.)_")
    else:
        for label in sorted(by_label.keys(), key=_label_sort_key):
            ms = by_label[label]
            # Dedup by value_raw (keep the first; they're often restated).
            seen_vals: set[str] = set()
            for m in ms:
                if m.value_raw in seen_vals:
                    continue
                seen_vals.add(m.value_raw)
                period = f" ({m.period})" if m.period else ""
                lines.append(f"- **{label}**: {m.value_raw}{period}")
    lines.extend(["", _KF_END, ""])
    return "\n".join(lines)


def _kf_insertion_point(text: str) -> int:
    """Where to insert the Key Figures block. After the Company Overview /
    Financial Information sections if present (adjacent to related content);
    otherwise before the first `## The Chatter` block, else at end. The
    position is bumped OUT of any auto region — this fallback is the
    original cause of the KF-nested-inside-chatter layouts (inserting
    "before the first ## The Chatter" landed inside the chatter region,
    between its BEGIN and its heading)."""
    for heading in (r"^## Financial", r"^## Key Metrics", r"^## Company Overview"):
        m = re.search(heading, text, re.MULTILINE)
        if m:
            # Insert right AFTER this heading's section — find the next heading.
            nxt = re.search(r"^## ", text[m.end() :], re.MULTILINE)
            return _outside_auto_regions(text, m.end() + (nxt.start() if nxt else 0))
    m = re.search(r"^## The Chatter", text, re.MULTILINE)
    if m:
        return _outside_auto_regions(text, m.start())
    return len(text)


def _replace_or_insert_kf(text: str, new_block: str) -> tuple[str, bool]:
    """Refresh the sentinel-wrapped Key Figures block or insert a new one.

    A foreign auto-block nested inside the key-figures region (the known
    collision with enrich_from_yfinance's company-profile block, which shares
    the "after ## Company Overview" insertion anchor) is rescued and re-placed
    immediately before the BEGIN marker, so refreshing the figures never
    deletes a sibling auto-section. The rescue is idempotent: once the foreign
    block sits outside the region it is no longer matched here. Unbalanced
    nested sentinels skip the replacement (see _extract_nested_blocks).
    """
    m = _KF_PATTERN.search(text)
    if m:
        rescued = _extract_nested_blocks(m.group(0))
        if rescued is None:
            return (text, False)  # unbalanced sentinels — never risk content
        if rescued:
            replacement = "\n\n".join(b.rstrip() for b in rescued) + "\n\n" + new_block
        else:
            replacement = new_block
        replaced = text[: m.start()] + replacement + text[m.end() :]
        return (replaced, replaced != text)
    idx = _kf_insertion_point(text)
    prefix = text[:idx]
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n" if prefix.endswith("\n") else "\n\n"
    new_text = prefix + new_block + text[idx:]
    return (new_text, True)


def _render_metric_note(
    p: Path,
    ms: list,
    *,
    dry_run: bool,
    stale_only: bool,
    index: dict,
    vault: Path,
    stem_memo: dict[str, str | None],
    res_memo: dict[str, Path | None],
) -> str:
    """Render one entity's key-figures block + sources splice.

    Returns ``"written"`` (written or would-write), ``"gated"`` (stale_only
    evidence gate) or ``"skip"`` (no change / unbalanced rewrite).
    """
    text = p.read_text(encoding="utf-8", errors="replace")
    stems = _scanned_stems({m.as_of_edition for m in ms}, index, stem_memo)
    if stale_only and _stale_only_skip(text, stems) is True:
        return "gated"
    original_text = text
    new_block = render_key_figures_block(ms)
    text, changed = _replace_or_insert_kf(text, new_block)
    text, sources_changed = _splice_sources(text, index, vault, extra_stems=stems, memo=res_memo)
    if not (changed or sources_changed) or not _balanced_or_skipped(original_text, text, p.name):
        return "skip"
    if dry_run:
        return "written"
    # OKF: same bump as the chatter block (single generated key per note —
    # last writer wins, which is the freshest derive).
    text = bump_generated(text, _OKF_ACTOR)
    if not text.endswith("\n"):
        text += "\n"  # notes terminate with a newline (md-lint MD047)
    p.write_text(text, encoding="utf-8")
    return "written"


def render_metrics_notes(
    metrics_by_entity: dict,
    *,
    dry_run: bool = True,
    conn=None,
    stale_only: bool = False,
    index: dict | None = None,
) -> tuple[int, int]:
    """Render the auto ``## Key Figures (auto)`` block into each company note.

    ``metrics_by_entity`` maps ``entity_name`` -> list[Metric]. Returns
    ``(written, gated)`` — notes written/would-write (key-figures block
    and/or sources splice) and notes gated out by ``stale_only`` (same
    note-level evidence gate as render_notes). The block itself carries no
    edition reference, so the scanned editions reach the gate directly and
    the splice adds them as extra stems — without that, a metrics-only
    note could never satisfy the gate and would re-render on every run.
    """
    own_conn = conn is None
    if own_conn:
        conn = connect()
    written = 0
    gated = 0
    try:
        path_by_entity = _paths_by_entity(conn, list(metrics_by_entity.keys()))
        if not path_by_entity:
            return (0, 0)
        vault = PROJECT_ROOT / "findata"
        if index is None:
            index = source_note_index(vault)
        stem_memo: dict[str, str | None] = {}
        res_memo: dict[str, Path | None] = {}
        for entity, ms in metrics_by_entity.items():
            file_path = path_by_entity.get(entity)
            p = PROJECT_ROOT / file_path if file_path else None
            if not ms or p is None or not p.exists():
                continue
            outcome = _render_metric_note(
                p,
                ms,
                dry_run=dry_run,
                stale_only=stale_only,
                index=index,
                vault=vault,
                stem_memo=stem_memo,
                res_memo=res_memo,
            )
            if outcome == "gated":
                gated += 1
            elif outcome == "written":
                written += 1
    finally:
        if own_conn:
            conn.close()
    return (written, gated)


# =========================================================================== #
# Orchestration                                                               #
# =========================================================================== #
def _expand_paths(target: str) -> list[Path]:
    """Resolve a CLI target (file / dir / glob) to a list of newsletter .md files."""
    p = Path(target)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(p.rglob("*.md"))
    # Treat as glob.
    return sorted(PROJECT_ROOT.glob(target))


# Newsletter chrome to skip (non-content). Mirrors the parse_newsletter skip set.
_NEWSLETTER_CHROME_NAMES = {"image_map", "images"}


def _build_resolver_map(conn) -> dict[str, str]:
    """One DB round-trip: company name + normalized_name -> canonical db name (lowercased keys)."""
    rows = conn.execute(
        "SELECT name, normalized_name FROM entities WHERE entity_type='company'"
    ).fetchall()
    m: dict[str, str] = {}
    for r in rows:
        name = r["name"]
        norm = r["normalized_name"]
        if name:
            m[name.lower()] = name
        if norm and norm.lower() not in m:
            m[norm.lower()] = name
            m[norm.replace("_", " ").lower()] = name
            m[norm.replace(" ", "_").lower()] = name
    return m


def _scan_one_file(md: Path, resolver_map: dict[str, str]) -> tuple[list[Quote], list[Metric]]:
    """Scan a single newsletter file (thread-safe; no DB)."""
    if md.name == "image_map.md" or md.parent.name in _NEWSLETTER_CHROME_NAMES:
        return [], []
    content = md.read_text(encoding="utf-8", errors="replace")
    stem = md.stem
    edition = _edition_title(stem, content)
    sections = list(iter_company_sections(content))
    if not sections:
        return [], []
    resolved: dict[str, str] = {}
    for s in sections:
        lower = s.canonical_name.lower()
        if lower in resolver_map:
            resolved[s.canonical_name] = resolver_map[lower]
    quotes: list[Quote] = []
    metrics: list[Metric] = []
    for section in sections:
        entity_name = resolved.get(section.canonical_name)
        if not entity_name:
            continue
        section.canonical_name = entity_name
        quotes.extend(extract_quotes(section, edition, stem))
        metrics.extend(extract_metrics(section, edition, stem))
    return quotes, metrics


def scan(target: str, conn, corpus: Corpus | None = None) -> tuple[list[Quote], list[Metric]]:
    """Scan one or more newsletter files; return ``(quotes, metrics)``."""
    # S1a single-DB-query: was N queries (one per file via _resolve_entities), now 1.
    # S1b corpus: when corpus is given (maint --full --corpus), iterate over pre-parsed notes instead of re-walking.
    resolver_map = _build_resolver_map(conn)
    if corpus is not None:
        # Filter corpus notes to target newsletter trees (findata/The_Chatter etc.)
        target_paths = set(_expand_paths(target))
        # If target is a directory like findata, include all newsletters; else filter to target set
        if len(target_paths) == 1 and target_paths.pop().as_posix() == "findata":
            paths = [
                Path(n.path)
                for n in corpus.notes
                if n.path.parent.name not in _NEWSLETTER_CHROME_NAMES
                and n.path.name != "image_map.md"
                and "The_Chatter" in n.path.as_posix()
                or "Points_And_Figures" in n.path.as_posix()
                or "The_PlotLines" in n.path.as_posix()
            ]
            # Simpler: filter to newsletter trees explicitly
            paths = [
                Path(n.path)
                for n in corpus.notes
                if any(
                    t in n.path.as_posix()
                    for t in ("The_Chatter", "Points_And_Figures", "The_PlotLines")
                )
            ]
        else:
            target_set = {Path(t).as_posix() for t in _expand_paths(target)}
            paths = [Path(n.path) for n in corpus.notes if n.path.as_posix() in target_set]
        # Use corpus fast path: text already loaded, avoid re-reading file
        quotes: list[Quote] = []
        metrics: list[Metric] = []
        by_path_text = {n.path.as_posix(): n.text for n in corpus.notes}
        for md in paths:
            # _scan_one_file reads file again; use corpus text if available
            text = by_path_text.get(md.as_posix())
            if text is not None:
                # Inline _scan_one_file with corpus text to avoid re-read
                stem = md.stem
                edition = _edition_title(stem, text)
                sections = list(iter_company_sections(text))
                if not sections:
                    continue
                resolved = {
                    s.canonical_name: resolver_map[s.canonical_name.lower()]
                    for s in sections
                    if s.canonical_name.lower() in resolver_map
                }
                for section in sections:
                    en = resolved.get(section.canonical_name)
                    if not en:
                        continue
                    section.canonical_name = en
                    quotes.extend(extract_quotes(section, edition, stem))
                    metrics.extend(extract_metrics(section, edition, stem))
                continue
            q_batch, m_batch = _scan_one_file(md, resolver_map)
            quotes.extend(q_batch)
            metrics.extend(m_batch)
        return quotes, metrics
    paths = [
        p
        for p in _expand_paths(target)
        if p.name != "image_map.md" and p.parent.name not in _NEWSLETTER_CHROME_NAMES
    ]
    if not paths:
        return [], []
    quotes: list[Quote] = []
    metrics: list[Metric] = []
    for md in paths:
        q_batch, m_batch = _scan_one_file(md, resolver_map)
        quotes.extend(q_batch)
        metrics.extend(m_batch)
    return quotes, metrics


def _cli(argv: list[str] | None = None) -> int:  # noqa: C901
    p = argparse.ArgumentParser(
        description="Derive the quotes + company_metrics tables (the concall-body "
        "capture layer) from newsletter prose, and render auto "
        "`## The Chatter — <edition>` blocks into company notes.",
    )
    p.add_argument(
        "target",
        nargs="?",
        default="findata",
        help="Newsletter .md file, directory, or glob (default: findata). "
        "Sectors/Companies subfolders are skipped (no concall sections).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write quote/metric rows + render note blocks (default: dry-run).",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print every quote + metric in addition to the summary.",
    )
    p.add_argument(
        "--no-notes",
        action="store_true",
        help="Skip the note-rendering pass (DB write only).",
    )
    p.add_argument(
        "--stale-only",
        action="store_true",
        help="Render only notes whose evidence moved: skip notes whose "
        "generated.by is this tool AND max(sources[].last_modified) "
        "<= generated.at AND every scanned edition is already in "
        "sources[]. Notes without sources always render; rendered "
        "notes also get newly referenced editions spliced into "
        "sources[]. The first run after an OKF backfill re-renders "
        "all sourced notes (backfill stamps are not render stamps).",
    )
    p.add_argument(
        "--corpus",
        action="store_true",
        help="S1b: use helpers.core.corpus shared walk (maint --full) — one walk for all derivations.",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        # S1b: when --corpus, share one walk across maint --full
        if args.corpus and _HAS_CORPUS:
            try:
                # For Corpus, target is findata newsletters; load full findata and filter in scan via corpus param
                _corpus = Corpus.load("findata", workers=1, use_cache=True)  # ty: ignore[unresolved-attribute]
                quotes, metrics = scan(args.target, conn, corpus=_corpus)
            except Exception:
                quotes, metrics = scan(args.target, conn)
        else:
            quotes, metrics = scan(args.target, conn)

        # Summary.
        by_speaker: dict[str, int] = {}
        for q in quotes:
            key = q.speaker_name or "(anonymous)"
            by_speaker[key] = by_speaker.get(key, 0) + 1
        entities_with_quotes = {q.entity for q in quotes}
        entities_with_metrics = {m.entity for m in metrics}

        print(
            f"quotes={len(quotes)} metrics={len(metrics)} "
            f"entities_quotes={len(entities_with_quotes)} "
            f"entities_metrics={len(entities_with_metrics)} "
            f"({'apply' if args.apply else 'dry-run'})",
            file=sys.stderr,
        )
        if quotes:
            attributed = sum(1 for q in quotes if q.speaker_name)
            print(
                f"  quotes_attributed={attributed} "
                f"({100 * attributed / len(quotes):.0f}%) "
                f"distinct_speakers={len(by_speaker)}",
                file=sys.stderr,
            )
        if metrics:
            by_unit: dict[str, int] = {}
            for m in metrics:
                by_unit[m.unit or "(none)"] = by_unit.get(m.unit or "(none)", 0) + 1
            print(f"  metrics_by_unit: {by_unit}", file=sys.stderr)

        # One edition index for the whole run: normalizes as_of_edition to
        # stems at the write boundary + the render-side splice machinery.
        index = source_note_index(PROJECT_ROOT / "findata")
        q_written = apply_quotes(quotes, conn=conn, dry_run=not args.apply, index=index)
        m_written = apply_metrics(metrics, conn=conn, dry_run=not args.apply, index=index)
        action = "written" if args.apply else "would write"
        print(f"{q_written} quotes {action}.", file=sys.stderr)
        print(f"{m_written} metrics {action}.", file=sys.stderr)

        # Note rendering: chatter blocks (quotes) + key-figures blocks (metrics).
        if not args.no_notes:
            by_entity_edition: dict[tuple[str, str | None], list[Quote]] = {}
            for q in quotes:
                key = (q.entity, q.as_of_edition)
                bucket = by_entity_edition.get(key)
                if bucket is None:
                    bucket: list[Quote] = []
                    by_entity_edition[key] = bucket
                bucket.append(q)
            # Reuse the run-level index built above the apply calls (the
            # splice + gate-amendment machinery resolves edition strings
            # through it).
            written, skipped, gated = render_notes(
                by_entity_edition,
                dry_run=not args.apply,
                conn=conn,
                stale_only=args.stale_only,
                index=index,
            )
            n_action = "wrote" if args.apply else "would write"
            print(
                f"{written} notes {n_action} (chatter block and/or sources "
                f"splice; {skipped} edition blocks skipped — hand-written "
                f"block preserved"
                + (f"; {gated} notes gated by --stale-only" if args.stale_only else "")
                + ").",
                file=sys.stderr,
            )
            # Key Figures (auto) blocks from metrics.
            metrics_by_entity: dict[str, list[Metric]] = {}
            for m in metrics:
                metrics_by_entity.setdefault(m.entity, []).append(m)
            kf_written, kf_gated = render_metrics_notes(
                metrics_by_entity,
                dry_run=not args.apply,
                conn=conn,
                stale_only=args.stale_only,
                index=index,
            )
            print(
                f"{kf_written} key-figures notes {n_action}"
                + (f" ({kf_gated} notes gated by --stale-only)" if args.stale_only else "")
                + ".",
                file=sys.stderr,
            )

        if args.verbose:
            for q in sorted(quotes, key=lambda x: (x.entity, x.as_of_edition or "")):
                spk = f"{q.speaker_name} ({q.speaker_title})" if q.speaker_name else "(anon)"
                print(f"[Q] {q.entity} | {q.as_of_edition} | {spk} | {q.quote_text[:90]}…")
            for m in sorted(metrics, key=lambda x: (x.entity, x.as_of_edition or "")):
                print(
                    f"[M] {m.entity} | {m.as_of_edition} | {m.metric_label} | "
                    f"{m.value_raw} | {m.unit} | {(m.source_quote or '')[:70]}"
                )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
