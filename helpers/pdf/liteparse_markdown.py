#!/usr/bin/env python3
"""LiteParse -> markdown for OCR fallback (scanned PDFs).

Keeps `helpers/pdf/pdf_local.py` unchanged (per review) — this module is
*only* for the scanned path where `pdf_local` refuses (`MIN_CHARS_PER_PAGE 100`).
It wraps `liteparse` OCR (`ocr_enabled=True`, `TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata` `eng 4.0M`)
and emits a minimal markdown that `helpers/pdf/pdf_conv_md.py` downstream
(`parse_newsletter` `## Name | Cap | Sector`, `rebuild_doc_search` `## `, `verify_extraction`)
can ingest without Paddle `PP-StructureV3` cost.

Design: text-only markdown, images via pymupdf sidecar (same as proposal).
No heading-normalization over-filtering — raw liteparse text is indented plain
text, so we only add `## ` where a line looks like a company heading.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # noqa: E402  # run as script: python helpers/pdf/liteparse_markdown.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from helpers.pdf.liteparse_post import CAP_TAIL_RE, SECTOR_PREFIXES  # noqa: E402

TESSDATA_DEFAULT = "/usr/share/tesseract-ocr/5/tessdata"


def _looks_like_company_heading(line: str) -> bool:
    """Heuristic for OCR path: line with pipe + cap token, or sector+company glue."""
    s = line.strip()
    if "|" not in s or len(s) < 10 or len(s) > 120:
        return False
    if CAP_TAIL_RE.search(s):
        return True
    # Sector prefix + rest with pipe
    if "|" in s:
        pre = s.split("|", 1)[0].strip().lower()
        for sector in SECTOR_PREFIXES:
            if pre.startswith(sector) and len(pre) > len(sector) + 2:
                return True
    return False


def _to_markdown_lines(text: str) -> str:
    """Convert liteparse plain text to minimal markdown.

    * Preserve blank lines (paragraph breaks).
    * Prefix detected company headings with `## ` (idempotent if already `## `).
    * Strip excessive indent (liteparse indents with 4 spaces).
    """
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        # Keep blank lines
        if not line:
            out.append("")
            continue
        # Already a heading?
        if line.startswith("#"):
            out.append(line)
            continue
        if _looks_like_company_heading(line):
            # Clean wrappers if any (unlikely from OCR but harmless)
            clean = line.replace("**", "").replace("<u>", "").replace("</u>", "").strip()
            out.append(f"## {clean}")
        else:
            out.append(line)
    return "\n".join(out)


def convert_liteparse_ocr(
    pdf_path: Path, *, dpi: int = 150, language: str = "eng", tessdata_path: str = TESSDATA_DEFAULT
) -> tuple[str, dict]:
    """Run liteparse OCR and return (markdown_text, meta).

    `meta` contains `engine`, `tessdata`, `items`, `pages` for provenance.
    Caller is responsible for `TESSDATA_PREFIX` or explicit `tessdata_path`.
    """
    # Ensure TESSDATA_PREFIX for underlying Rust Tesseract
    if tessdata_path:
        os.environ["TESSDATA_PREFIX"] = tessdata_path
    from liteparse import LiteParse

    parser = (
        LiteParse(
            quiet=True,
            ocr_enabled=True,
            ocr_language=language,
            dpi=dpi,
            tessdata_path=tessdata_path,
        )
        if tessdata_path
        else LiteParse(quiet=True, ocr_enabled=True, ocr_language=language, dpi=dpi)
    )
    # liteparse API: tessdata_path kwarg is available as `tessdata_path` in newer bindings; fallback to env
    try:
        res = parser.parse(str(pdf_path))
    except TypeError:
        # older binding without tessdata_path kwarg
        from liteparse import LiteParse as LP2

        res = LP2(quiet=True, ocr_enabled=True, ocr_language=language, dpi=dpi).parse(str(pdf_path))

    # Build per-page markdown (for pdf_conv_md per-page verify) and
    # combined markdown. liteparse `res.text` is the joined doc text, but
    # per-page `res.pages[i].text` (or concatenated `text_items`) lets us
    # keep the pages shape so `verify_extraction` per-page coverage works on
    # multi-page PDFs like SBI 28p. Fallback to single combined page if the
    # per-page split is unavailable.
    page_texts: list[str] = []
    try:
        for p in res.pages:
            # Prefer the page-level aggregated text if present, else join
            # individual text items for that page.
            pt = getattr(p, "text", None)
            if not pt:
                pt = " ".join(getattr(t, "text", "") for t in getattr(p, "text_items", []))
            page_texts.append(_to_markdown_lines(pt or ""))
    except Exception:
        page_texts = []

    md = _to_markdown_lines(res.text)
    # If per-page texts were collected, keep them; otherwise single page.
    if page_texts and len(page_texts) == len(res.pages):
        # Re-derive combined from pages to stay consistent with verify
        # (joined with blank line — matches pdf_conv_md page joining semantically)
        combined_from_pages = "\n\n".join(page_texts)
        # Prefer the original joined text if it differs only by whitespace,
        # but store per-page splits for the engine chain.
        meta_page_texts = page_texts
    else:
        meta_page_texts = [md] if md else []
        combined_from_pages = md

    meta = {
        "engine": f"liteparse-ocr-{language}",
        "tessdata": tessdata_path,
        "pages": len(res.pages),
        "items": sum(len(p.text_items) for p in res.pages),
        "chars": len(md),
        "page_texts": meta_page_texts,
        "combined_chars": len(combined_from_pages),
    }
    return md, meta


if __name__ == "__main__":
    import sys

    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("Reports/SBI_Delhivery_Titan.pdf")
    md, meta = convert_liteparse_ocr(pdf)
    print(meta)
    print(md[:2000])
