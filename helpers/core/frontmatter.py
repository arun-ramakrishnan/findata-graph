#!/usr/bin/env python3
"""Shared YAML frontmatter utilities.

Consolidates 7 near-identical implementations from derive_events, derive_themes,
extract_relations, move_sector, and rename_entity into a single canonical API.

Public API:
    strip_frontmatter(text)            -> str                 # body without FM block
    split_frontmatter(text)            -> (str, str, str)      # ("---", yaml, rest)
    split_frontmatter_with_title(text) -> (title|None, body)   # title + body
    extract_tags(text)                 -> list[str]            # tags from YAML block
    render_frontmatter(mapping)        -> str                  # dict -> --- block
    iso_now_utc()                      -> str                  # 2026-08-18T09:00:00Z
    moddate_to_iso_date(s)             -> str|None             # PDF D:... -> YYYY-MM-DD
    bump_generated(text, by, ...)      -> str                  # OKF generated/stale_after

The last four implement the OKF v0.2 provenance vocabulary (doc/okf.md,
doc/improvements/archive/okf_adoption.md), shared by the two generators
that own the data: pdf/pdf_conv_md.py emits generated+sources at conversion
time; graph/derive_insights.py bumps generated on each auto-block rewrite.
"""
from __future__ import annotations

import datetime as _dt
import re

import yaml

# Matches a leading YAML frontmatter block.
# The \s* allows optional trailing whitespace after the --- delimiters,
# which is strictly more permissive than the bare \n used by some callers.
_FM_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """Return *text* without its leading YAML frontmatter block.

    If no frontmatter is present the original text is returned unchanged.
    """
    m = _FM_RE.match(text)
    return text[m.end():] if m else text


def split_frontmatter(text: str) -> tuple[str, str, str]:
    """Split *text* into ``(opening_dashes, yaml_body, rest_of_doc)``.

    Returns ``("", "", text)`` when no frontmatter is present.
    The *yaml_body* is the content between the two ``---`` lines (exclusive
    of the dashes themselves); *rest_of_doc* starts at the character after
    the closing ``---`` line.
    """
    if not text.startswith("---"):
        return "", "", text
    m = re.search(r"^---\s*$", text[3:], re.MULTILINE)
    if not m:
        return "", "", text
    end = m.start() + 3
    return text[:3], text[3:end], text[end:]


def split_frontmatter_with_title(text: str) -> tuple[str | None, str]:
    """Split *text* into ``(title_or_None, body)``.

    Extracts the ``title:`` field from YAML frontmatter (stripping surrounding
    quotes).  Returns ``(None, text)`` when no frontmatter is present.

    This is the note-search variant: callers that need the DB canonical name
    should prefer the entities lookup instead of the raw YAML title.
    """
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    fm = text[3:end]
    body = text[end + 4:]  # skip the closing "\n---"
    m = re.search(r"^title:\s*(.+?)\s*$", fm, re.MULTILINE)
    title = m.group(1).strip().strip("\"'") if m else None
    return title, body


# --------------------------------------------------------------------------- #
# OKF v0.2 provenance helpers (doc/okf.md; adopted 2026-08-18)                #
# --------------------------------------------------------------------------- #
# YAML dump settings for frontmatter round-trips: preserve key order (the
# hand-authored order is meaningful to readers), keep non-ASCII readable,
# and never wrap long scalar lines (inline lists stay on one line).
_YAML_DUMP_KW = dict(sort_keys=False, allow_unicode=True,
                      default_flow_style=False, width=10**6)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def render_frontmatter(mapping: dict) -> str:
    """Serialize *mapping* to a ``---``-delimited frontmatter block.

    Keys are emitted in insertion order. Returns a block ending in a single
    newline, ready to prepend to a markdown body.
    """
    dumped = yaml.safe_dump(mapping, **_YAML_DUMP_KW).rstrip("\n")
    return f"---\n{dumped}\n---\n"


def iso_now_utc() -> str:
    """Current UTC time as an OKF ISO 8601 string (``2026-08-18T09:00:00Z``)."""
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# PDF date syntax: D:YYYYMMDDHHmmSS[+HH'mm'|-HH'mm'] (every tail part optional).
_MODDATE_RE = re.compile(
    r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:([+-])(\d{2})?'?(\d{2})?'?)?$"
)


# poppler's human-readable form: "Thu Aug 13 09:01:08 2026 IST" (what this
# corpus's PDFs actually emit — verified across every Reports/*.pdf). The tz
# is a NAME (IST is ambiguous: India/Israel/Ireland), so no numeric UTC shift
# is possible without a tz database; the LOCAL date is returned as-written.
_HUMAN_PDF_DATE_RE = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+"
    r"\d{2}:\d{2}:\d{2}\s+(\d{4})(?:\s+(\S+))?$"
)
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _human_pdf_date(s: str) -> str | None:
    """``Thu Aug 13 09:01:08 2026 IST`` -> ``2026-08-13`` (local date)."""
    m = _HUMAN_PDF_DATE_RE.match(s)
    if not m:
        return None
    mon, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    if mon not in _MONTHS or not 1 <= day <= 31:
        return None
    return f"{year:04d}-{_MONTHS[mon]:02d}-{day:02d}"


def moddate_to_iso_date(s: str | None) -> str | None:
    """Convert a pdfinfo ``ModDate`` to ISO ``YYYY-MM-DD``.

    Two shapes are accepted: the PDF ``D:YYYYMMDDHHmmSS±HH'mm'`` syntax (the
    numeric offset is applied, returning the UTC date of the instant) and
    poppler's human-readable ``Thu Aug 13 09:01:08 2026 IST`` form (the one
    this corpus's PDFs actually emit), for which the LOCAL date is returned —
    a named tz cannot be numerically offset without a tz database, and the
    possible ±1-day skew near midnight is immaterial for a provenance signal.
    Returns ``None`` for anything unparsable (callers omit the key).
    """
    if not s:
        return None
    s = s.strip()
    m = _MODDATE_RE.match(s)
    if not m:
        return _human_pdf_date(s)
    y, mo, d, h, mi, sec, sign, oh, om = m.groups()
    try:
        dt = _dt.datetime(int(y), int(mo or 1), int(d or 1),
                          int(h or 0), int(mi or 0), int(sec or 0))
    except ValueError:
        return None
    if sign and oh:
        delta = _dt.timedelta(hours=int(oh), minutes=int(om or 0))
        dt = dt + delta if sign == "-" else dt - delta
    return dt.date().isoformat()


def stringify_dates(obj):
    """Deep-convert PyYAML-parsed date/datetime objects to ISO strings.

    YAML timestamps (``at: 2026-08-18T12:00:00`` without a Z suffix) load as
    datetime objects; dumping those back re-renders them in PyYAML's own
    shape (``2026-08-18 12:00:00``) and — worse — the OKF schema patterns
    expect strings. UTC datetimes render with the OKF ``Z`` suffix.
    """
    import datetime as _dt

    if isinstance(obj, _dt.datetime):
        if obj.tzinfo == _dt.UTC:
            return obj.strftime("%Y-%m-%dT%H:%M:%SZ")
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: stringify_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [stringify_dates(v) for v in obj]
    return obj


def bump_generated(
    text: str, by: str, *, stale_days: int = 180, now: str | None = None,
) -> str:
    """Bump OKF ``generated`` (and recompute ``stale_after``) in *text*'s
    frontmatter. Returns *text* unchanged when it has no frontmatter block
    or the YAML is unparsable — this helper never invents frontmatter and
    never corrupts a note.

    Read-modify-write through the YAML serializer (never a regex splice) so
    every existing key survives, **including any hand-written ``verified``**
    — the trust-tier split depends on it: ``generated`` alone = machine-
    confirmed; a ``human:`` verifier upgrades the note to human-reviewed
    (okf_adoption.md §2.3).

    ``stale_after`` is recomputed per the accepted population rule:
    ``max(sources[].last_modified) + stale_days`` when the note carries
    sources, else ``(derive date) + stale_days`` — auto content is fresh as
    of the write that produced it. Pass ``now`` for deterministic tests.
    """
    opener, fm_text, rest = split_frontmatter(text)
    if not opener:
        return text
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return text
    if not isinstance(fm, dict):
        return text
    fm = stringify_dates(fm)
    at = now or iso_now_utc()
    fm["generated"] = {"by": by, "at": at}
    base = None
    for src in fm.get("sources") or []:
        lm = src.get("last_modified") if isinstance(src, dict) else None
        if isinstance(lm, str) and _ISO_DATE_RE.match(lm):
            base = max(base or "", lm)
    if base is None:
        base = at[:10]
    try:
        fm["stale_after"] = (
            _dt.date.fromisoformat(base) + _dt.timedelta(days=stale_days)
        ).isoformat()
    except ValueError:  # noqa: S110  # leave any existing stale_after untouched
        pass
    # Rebuild: the fresh block (ends with the closing "---\n") + the ORIGINAL
    # body. split_frontmatter's *rest* starts at the closing "---" line; drop
    # exactly the dashes and ONE line-ending so the body's own leading blank
    # lines are preserved byte-exact (strip_frontmatter's regex would eat
    # them, which is right for its callers but not for a round-trip).
    body = rest
    if body.startswith("---"):
        body = body[3:].lstrip(" \t")
        if body.startswith("\n"):
            body = body[1:]
    return render_frontmatter(fm) + body


def extract_tags(text: str) -> list[str]:
    """Extract tag values from a note's YAML ``tags:`` block.

    Handles the block-list form::

        tags:
        - entity_type/company
        - sector/logistics

    Returns an empty list when no frontmatter or no tags block is present.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    in_tags = False
    tags: list[str] = []
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ln.rstrip() == "tags:":
            in_tags = True
            continue
        if in_tags:
            s = ln.strip()
            if s.startswith("- "):
                tags.append(s[2:].strip())
            elif s == "":
                continue
            else:
                in_tags = False
    return tags
