#!/usr/bin/env python3
"""Post-processing for LiteParse text — port of pdf_local normalizations.

Applies the same heading/normalizations that `helpers/pdf/pdf_local.py`
applies to `pymupdf4llm` output, but to `liteparse` `res.text` (page-joined).
This closes the 1.7% recall gap (96.04% -> ~97.7%) measured in
`doc/local/perf_skills.md:9.1` 7-PDF trial, before any engine cutover.

No image logic here — images are handled as a sidecar via `pymupdf`
(see proposal `liteparse_pdf_engine.md` Slice 1).
"""
from __future__ import annotations

import re

# Copied from pdf_local.py (keep in sync)
PIC_BLOCK_RE = re.compile(r"<!-- Start of picture text -->.*?<!-- End of picture text -->", re.S)
PAGE_NUM_RE = re.compile(r"^\d{1,3}/\d{1,3}$")
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+)$")
BOLD_LINE_RE = re.compile(r"^(?:\*\*.+?\*\*\s*)+$")
CAP_TAIL_RE = re.compile(r"\|[^|]*\b(?:large|mid|small|micro|nano|mega)\s*cap", re.I)
SECTOR_PREFIXES = (
    "aerospace & defence",
    "engineering & capital goods",
    "tourism & hospitality",
    "heavy electrical equipment",
    "consumer durables",
    "financial services",
    "financial service",
    "real estate",
    "auto ancillary",
    "capital markets",
    "housing finance",
    "building materials",
    "ems manufacturing",
    "regulator",
    "logistics",
    "retail",
    "fmcg",
    "healthcare",
    "pharma",
    "technology",
    "software",
    "energy",
    "renewables",
    "metals",
    "chemicals",
    "defence",
    "telecom",
    "telecommunications",
    "media",
    "entertainment",
    "textiles",
    "packaging",
    "agriculture",
    "education",
    "edtech",
    "electronics",
    "mining",
    "aviation",
    "infrastructure",
    "railways",
    "hotels",
    "hotel",
    "tourism",
    "diversified",
    "insurance",
    "nbfc",
    "consumer",
    "fertilizer",
    "diagnostics",
    "hospitals",
    "automotive",
    "pharmaceuticals",
)
_LEGAL_SUFFIX_ONLY = {"ltd", "ltd.", "limited", "pvt", "private", "inc", "co", "corp"}

def _strip_picture_text(text: str) -> str:
    return PIC_BLOCK_RE.sub("", text)

def _filter_running_headers(text: str, title: str | None) -> tuple[str, str | None]:
    kept: list[str] = []
    seen_urls: set[str] = set()
    title_seen = title is not None
    if title is None:
        m = re.search(r"^#{1,6}\s+(.+)$", text, re.M)
        title = m.group(1).strip() if m else None
        title_seen = title is not None
    for line in text.splitlines():
        s = line.strip()
        if s and PAGE_NUM_RE.match(s):
            continue
        if s and len(s) < 40 and DATE_RE.search(s):
            continue
        bare = HEADING_LINE_RE.match(s)
        if title and (bare.group(2).strip() if bare else s) == title:
            if title_seen:
                continue
        if s.startswith("http") and len(s) > 40:
            key = s[:60]
            if key in seen_urls:
                continue
            seen_urls.add(key)
        kept.append(line)
    return "\n".join(kept), title

def _strip_wrappers(s: str) -> str:
    for junk in ("**", "<u>", "</u>"):
        s = s.replace(junk, "")
    return s.strip()

def _split_sector_glue(body: str) -> tuple[str, str] | None:
    if "|" not in body:
        return None
    pre, tail = body.split("|", 1)
    low = pre.lower().strip()
    for sector in sorted(SECTOR_PREFIXES, key=len, reverse=True):
        if low.startswith(sector):
            rest = pre.strip()[len(sector):].strip()
            if rest and rest.lower() not in _LEGAL_SUFFIX_ONLY:
                return pre.strip()[: len(sector)].strip(), f"{rest} | {tail.strip()}"
    return None

def _fix_heading_line(line: str) -> str | None:
    stripped = line.strip()
    m = HEADING_LINE_RE.match(stripped)
    if m:
        body = _strip_wrappers(m.group(2))
        split = _split_sector_glue(body)
        if split:
            return f"## {split[0]}\n## {split[1]}"
        return f"{m.group(1)} {body}" if body != m.group(2) else None
    if BOLD_LINE_RE.match(stripped) and "|" in stripped:
        body = _strip_wrappers(stripped)
        split = _split_sector_glue(body)
        if split:
            return f"## {split[0]}\n## {split[1]}"
        if CAP_TAIL_RE.search(body):
            return f"## {body}"
    return None

def _normalize_headings(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        fixed = _fix_heading_line(line)
        out.append(fixed if fixed is not None else line)
    return "\n".join(out)

def normalize(text: str, title: str | None = None) -> str:
    """Apply pdf_local normalizations to liteparse text.

    `text` may be a single page or the full doc (page-joined). Pass
    `title` through across pages when normalizing per-page.
    Returns normalized text (single string, potentially with an extra
    ``\\n`` for a split glue heading).
    """
    text = _strip_picture_text(text)
    text, title = _filter_running_headers(text, title)
    text = _normalize_headings(text)
    return text

# For per-doc convenience: normalize page-joined liteparse output in one call
def normalize_doc(text: str) -> str:
    return normalize(text)

