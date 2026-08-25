#!/usr/bin/env python3
"""Local PDF -> markdown-pages converter (the no-OCR engine).

Produces the same ``pages`` shape as ``pdf_conv_md.parse_pages()`` —
``[{"prunedResult": None, "markdown": {"text": ..., "images": {...}},
"outputImages": [], "inputImage": None}]`` — so every downstream stage of
``pdf_conv_md.py`` (image plan, wikilinks, OKF frontmatter, output
writing) is engine-agnostic and works unchanged for locally parsed PDFs.

Engine: pymupdf4llm (PyMuPDF). Born-digital PDFs only — page rasters are
never OCR'd (embedded images are extracted as files, not read). A PDF
without a usable text layer raises :class:`LocalRefusalError` so callers
can fall back to an OCR engine (the Paddle API).

Normalizations applied to pymupdf4llm output (tuned on the 7-PDF trial,
``doc/local/local_pdf_engine_trial.md``; word recall 96-98.6% vs the
Paddle/GLM reference notes, zero content lost):

- headings: strip ``**`` / ``<u>`` wrappers (parse_newsletter's SECTION_RE
  wants clean ``Name | Cap | Sector``), split sector+company headings
  pymupdf4llm glues onto one line, and rescue company headings that lost
  their ``#`` marker (whole-line bold);
- picture-text blocks (Tesseract text of embedded rasters): dropped —
  the trial found only ad/footer/badge text in them (Q2, 2026-08-25);
- running headers/footers dropped: pure page numbers (``3/22``), short
  date lines (``8/6/26, 8:32 AM``), exact repeats of the document title,
  duplicate long URLs;
- embedded images kept when >= MIN_IMAGE_PX on both sides AND
  >= MIN_IMAGE_BYTES (Q3, accepted 2026-08-25) — smaller ones are
  logos/badges and their refs are dropped;
- kept image refs rewritten to the Paddle text convention
  (``<div style="text-align: center;"><img src="imgs/<key>"/></div>``)
  with the images map pointing at the extracted local files, so
  ``plan_images``/``to_wikilinks`` work verbatim.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pymupdf
import pymupdf4llm

# Decoration filter (proposal Q3, accepted 2026-08-25).
MIN_IMAGE_PX = 150
MIN_IMAGE_BYTES = 8192
# A page with less text than this on average is treated as scanned.
MIN_CHARS_PER_PAGE = 100

try:
    ENGINE_LABEL = f"pymupdf4llm-{version('pymupdf4llm')}"
except PackageNotFoundError:  # pragma: no cover - metadata always present
    ENGINE_LABEL = "pymupdf4llm"

# Tesseract text pymupdf4llm inlines next to embedded rasters.
PIC_BLOCK_RE = re.compile(
    r"<!-- Start of picture text -->.*?<!-- End of picture text -->", re.S
)
# pymupdf4llm image reference: ![](path)
IMG_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# Pure page numbers: "3/22", "1/27".
PAGE_NUM_RE = re.compile(r"^\d{1,3}/\d{1,3}$")
# Short date headers: "8/6/26, 8:32 AM".
DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
# Heading line (any level) for wrapper stripping.
HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+)$")
# Whole-line bold (no #): pymupdf4llm sometimes emits company headings as
# **Sector** **<u>Company | Cap | Sector</u>** body lines (seen: Delhivery,
# SBI_Delhivery_Titan p11) — rescue them into real headings.
BOLD_LINE_RE = re.compile(r"^(?:\*\*.+?\*\*\s*)+$")
# Cap tokens in the "| Large Cap |" tail — a glued heading always has one.
CAP_TAIL_RE = re.compile(
    r"\|[^|]*\b(?:large|mid|small|micro|nano|mega)\s*cap", re.I
)
# Sector phrases a glued heading may start with (mirrors parse_newsletter
# _SECTOR_WORDS + the observed newsletter vocabulary; longest-first order
# matters — checked at match time, so keep this list readable instead).
SECTOR_PREFIXES = (
    "aerospace & defence", "engineering & capital goods",
    "tourism & hospitality", "heavy electrical equipment",
    "consumer durables", "financial services", "financial service",
    "real estate", "auto ancillary", "capital markets",
    "housing finance", "building materials", "ems manufacturing",
    "regulator", "logistics", "retail", "fmcg", "healthcare", "pharma",
    "technology", "software", "energy", "renewables", "metals",
    "chemicals", "defence", "telecom", "telecommunications", "media",
    "entertainment", "textiles", "packaging", "agriculture", "education",
    "edtech", "electronics", "mining", "aviation", "infrastructure",
    "railways", "hotels", "hotel", "tourism", "diversified", "insurance",
    "nbfc", "consumer", "fertilizer", "diagnostics", "hospitals",
    "automotive", "pharmaceuticals",
)
_LEGAL_SUFFIX_ONLY = {"ltd", "ltd.", "limited", "pvt", "private", "inc",
                      "co", "corp"}


class LocalRefusalError(RuntimeError):
    """The local engine refuses this PDF (no usable text layer)."""


def _assert_text_layer(pdf_path: Path) -> None:
    """Refuse scanned PDFs: the local engine never OCRs page rasters."""
    doc = pymupdf.open(str(pdf_path))
    n_pages = len(doc)
    total = sum(len(page.get_text()) for page in doc)
    doc.close()
    if n_pages == 0 or total / n_pages < MIN_CHARS_PER_PAGE:
        raise LocalRefusalError(
            f"text layer too thin ({total} chars over {n_pages} pages, "
            f"avg {total // max(n_pages, 1)}/page < {MIN_CHARS_PER_PAGE}) — "
            "scanned PDF? use the Paddle OCR engine"
        )


def _strip_picture_text(text: str) -> str:
    """Drop the Tesseract text pymupdf4llm inlines for embedded rasters."""
    return PIC_BLOCK_RE.sub("", text)


def _filter_running_headers(text: str, title: str | None) -> tuple[str, str | None]:
    """Drop running headers/footers; return (text, document title).

    Drops pure page numbers, short date lines, exact repeats of the
    document title (the first heading), and duplicate long URLs. The
    first title occurrence is kept and returned for later pages.
    """
    kept: list[str] = []
    seen_urls: set[str] = set()
    title_seen = title is not None  # recorded on an earlier page already
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
    """Drop ``**``/``<u>`` emphasis wrappers from heading text."""
    for junk in ("**", "<u>", "</u>"):
        s = s.replace(junk, "")
    return s.strip()


def _split_sector_glue(body: str) -> tuple[str, str] | None:
    """Split a glued ``Sector Company | Cap | Sector`` heading.

    pymupdf4llm concatenates adjacent sector + company headings onto one
    line (``Engineering & Capital Goods Inox India | Small Cap | ...``, or
    ``Regulator RBI Governor | <subtitle>`` — the tail may be a cap triple
    OR a subtitle, so no cap-token requirement here).
    Returns (sector, company-with-tail) when the pre-pipe text starts with
    a known sector phrase and a real company name follows; else None.
    """
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
    """Return a fixed heading line, or None to leave the line untouched."""
    stripped = line.strip()
    m = HEADING_LINE_RE.match(stripped)
    if m:
        body = _strip_wrappers(m.group(2))
        split = _split_sector_glue(body)
        if split:
            return f"## {split[0]}\n## {split[1]}"
        return f"{m.group(1)} {body}" if body != m.group(2) else None
    # Rescue whole-line-bold company headings (no # marker at all).
    if BOLD_LINE_RE.match(stripped) and "|" in stripped:
        body = _strip_wrappers(stripped)
        split = _split_sector_glue(body)
        if split:
            return f"## {split[0]}\n## {split[1]}"
        if CAP_TAIL_RE.search(body):
            return f"## {body}"
    return None


def _normalize_headings(text: str) -> str:
    """Apply heading fixes: strip wrappers, rescue bold headings, split glue.

    pymupdf4llm emits e.g. ``### **<u>Marico Ltd. | Large Cap | FMCG</u>**``
    (wrappers must go), sometimes glues sector+company headings into one
    line, and occasionally drops the ``#`` marker entirely (bold-only body
    line). parse_newsletter's SECTION_RE wants clean
    ``Name | Cap | Sector`` lines at heading level.
    """
    out: list[str] = []
    for line in text.splitlines():
        fixed = _fix_heading_line(line)
        out.append(fixed if fixed is not None else line)
    return "\n".join(out)


def _image_ok(path: Path) -> bool:
    """Decoration filter: >= MIN_IMAGE_PX both sides AND >= MIN_IMAGE_BYTES."""
    try:
        pm = pymupdf.Pixmap(str(path))
        w, h = pm.width, pm.height
    except Exception:  # noqa: BLE001 - unreadable dims: keep, never lose figures
        return True
    return w >= MIN_IMAGE_PX and h >= MIN_IMAGE_PX and path.stat().st_size >= MIN_IMAGE_BYTES


def _rewrite_images(text: str, counter: int) -> tuple[str, dict[str, str], int]:
    """Rewrite ``![](path)`` refs to the Paddle imgs/ convention.

    Kept images become
    ``<div style="text-align: center;"><img src="imgs/imgN"/></div>`` and
    the returned map points ``imgs/imgN`` at the extracted local file, so
    ``plan_images``/``to_wikilinks`` in pdf_conv_md work verbatim. Dropped
    decorations have their refs removed entirely.
    """
    images: dict[str, str] = {}

    def _sub(m: re.Match) -> str:
        nonlocal counter
        p = Path(m.group(1))
        if not p.is_file():
            print(f"  warn: image ref target missing, dropped: {p}")
            return ""
        if not _image_ok(p):
            return ""
        counter += 1
        rel = f"imgs/img{counter}"
        images[rel] = str(p.resolve())
        return f'<div style="text-align: center;"><img src="{rel}"/></div>'

    return IMG_REF_RE.sub(_sub, text), images, counter


def convert(pdf_path: Path, img_dir: Path) -> list[dict]:
    """Parse a born-digital PDF into the pdf_conv_md pages shape.

    ``img_dir`` receives the extracted raw images (pymupdf4llm writes
    them there); it must stay alive until the caller finishes copying
    (``write_outputs``), because the images map points into it.
    """
    _assert_text_layer(pdf_path)
    img_dir.mkdir(parents=True, exist_ok=True)
    chunks = pymupdf4llm.to_markdown(
        str(pdf_path),
        page_chunks=True,
        write_images=True,
        image_path=str(img_dir),
        image_format=".jpeg",
    )
    pages: list[dict] = []
    title: str | None = None
    img_counter = 0
    for chunk in chunks:
        text = chunk.get("text", "")
        text = _strip_picture_text(text)
        text, title = _filter_running_headers(text, title)
        text = _normalize_headings(text)
        text, images, img_counter = _rewrite_images(text, img_counter)
        pages.append(
            {
                "prunedResult": None,
                "markdown": {"text": text, "images": images},
                "outputImages": [],
                "inputImage": None,
            }
        )
    return pages
