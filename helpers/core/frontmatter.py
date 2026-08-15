#!/usr/bin/env python3
"""Shared YAML frontmatter utilities.

Consolidates 7 near-identical implementations from derive_events, derive_themes,
extract_relations, move_sector, and rename_entity into a single canonical API.

Public API:
    strip_frontmatter(text)            -> str                 # body without FM block
    split_frontmatter(text)            -> (str, str, str)      # ("---", yaml, rest)
    split_frontmatter_with_title(text) -> (title|None, body)   # title + body
    extract_tags(text)                 -> list[str]            # tags from YAML block
"""
from __future__ import annotations

import re

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
