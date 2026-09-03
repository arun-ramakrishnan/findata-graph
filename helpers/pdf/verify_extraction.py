#!/usr/bin/env python3
"""Verify a converted markdown extraction against its source PDF.

The pdfmux-inspired self-check pdf_conv_md runs after writing outputs
(better than pdfmux's, which could not do per-page silent-drop detection
on unsegmented markdown — our ``<stem>.json`` output IS page-segmented,
for both engines).

Checks, all against the PDF's own text layer (no OCR):
- per-page word-multiset coverage (source vs extracted page markdown) —
  a page far below the rest signals a silent drop;
- document-level coverage + top missing tokens (diagnosis aid);
- number multiset audit — financial newsletters live and die by numbers;
- wikilink integrity — every ``![[images/...]]`` target must exist;
- md/json consistency — the .md body must contain the .json pages' text.

Writes ``<stem>.verify.json`` (manifest: sha256 of source + extraction,
engine, thresholds, per-page metrics, verdict) and prints one summary
line. Exit 0 on PASS/WARN, 1 on FAIL.

Usage
-----
    python3 helpers/pdf/verify_extraction.py <source.pdf> <output_dir>
        [--stem S] [--warn-below F] [--fail-below F] [--quiet]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pymupdf  # noqa: E402

# Per-page coverage thresholds, tuned on the 7-PDF Reports corpus
# (doc/local/local_pdf_engine_trial.md): deliberate drops (footer ads,
# banners, page numbers) keep healthy pages at ~0.90+; ad-heavy pages
# (first/last) dip lower. WARN flags suspicious pages, FAIL only pages
# that lost most of their content.
WARN_BELOW = 0.85
FAIL_BELOW = 0.50
# Document-level thresholds (md is the artifact downstream consumes, so
# doc coverage reads the md itself, not the json). Healthy local-engine
# conversions measure 96.7-97.3% (the gap = deliberately dropped ads);
# a whole company section gutted from the md lands ~85-88% -> FAIL.
WARN_DOC_BELOW = 0.95
FAIL_DOC_BELOW = 0.90
# md-vs-json per-page consistency: write_outputs renders the md FROM the
# json pages, so after stripping image markup (json carries <img> tags,
# md carries wikilinks) every page's words must appear in the md body.
# Below this is a rendering bug, not a content judgment -> FAIL.
MD_JSON_FAIL_BELOW = 0.98
# Pages below this many source words are ad/banner/subscribe pages (p1 and
# the last page measured 41-138 words vs 1500+ content pages on the corpus)
# — coverage thresholds skip them; the numbers audit still covers them.
MIN_PAGE_WORDS = 150
# Missing numbers with fewer digits are pagination/ad noise ("Flat 20",
# page numbers 1-35); >=3 digits are financial figures worth reporting.
MIN_SIGNIFICANT_NUM_DIGITS = 3

NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
WIKILINK_RE = re.compile(r"!\[\[images/([^\]]+)\]\]")
# Image markup in BOTH artifacts (json: html img tags/divs; md: wikilinks)
# — stripped before md-vs-json comparison so only text is compared.
_IMG_DIV_RE = re.compile(r"<div[^>]*>\s*<img[^>]*/?>\s*</div>")
_IMG_TAG_RE = re.compile(r"<img[^>]*/?>")


def _strip_image_markup(text: str) -> str:
    """Remove image markup (html tags and wikilinks) from both artifacts."""
    t = _IMG_DIV_RE.sub(" ", text)
    t = _IMG_TAG_RE.sub(" ", t)
    return WIKILINK_RE.sub(" ", t)


def canon_words(text: str) -> list[str]:
    """Lowercased alphanumeric word tokens (order-free multiset member)."""
    return re.sub(r"[^0-9a-zA-Z]+", " ", text.lower()).split()


def canon_numbers(text: str) -> list[str]:
    """Digit tokens with thousand separators / decimals normalized out.

    ``14,000`` and ``14000`` compare equal (OCR/converter variants).
    """
    return [n.replace(",", "") for n in NUM_RE.findall(text)]


def _multiset_coverage(src: list[str], out: list[str]) -> float:
    """Fraction of src tokens present in out (multiset intersection / src)."""
    if not src:
        return 1.0
    from collections import Counter

    have = Counter(src) & Counter(out)
    return sum(have.values()) / len(src)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


def _source_pages(pdf_path: Path) -> list[str]:
    doc = pymupdf.open(str(pdf_path))
    try:
        return [page.get_text() for page in doc]
    finally:
        doc.close()


def verify(
    pdf_path: Path,
    out_dir: Path,
    stem: str,
    *,
    warn_below: float = WARN_BELOW,
    fail_below: float = FAIL_BELOW,
) -> dict:
    """Verify outputs; return the manifest dict (also written to disk)."""
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    manifest_path = out_dir / f"{stem}.verify.json"
    md_text = md_path.read_text(encoding="utf-8")
    pages = json.loads(json_path.read_text(encoding="utf-8"))

    src_pages = _source_pages(pdf_path)
    page_metrics: list[dict] = []
    for i, src in enumerate(src_pages):
        out = pages[i]["markdown"]["text"] if i < len(pages) else ""
        page_metrics.append(
            {
                "page": i + 1,
                "src_words": len(canon_words(src)),
                "out_words": len(canon_words(out)),
                "coverage": round(_multiset_coverage(canon_words(src), canon_words(out)), 4),
            }
        )
    doc_src = canon_words("\n".join(src_pages))
    md_body = re.sub(r"\A---\n.*?\n---\n", "", md_text, count=1)
    doc_out = canon_words(md_body)
    doc_coverage = _multiset_coverage(doc_src, doc_out)

    # md-vs-json per-page consistency: the md is rendered FROM the json
    # pages, so (after image-markup stripping) each page's words must be
    # present in the md body. Catches rendering-stage drops that per-page
    # source-vs-json metrics cannot see (json side stays healthy).
    md_body_stripped_words = canon_words(_strip_image_markup(md_body))
    md_json_pages = []
    for i, page in enumerate(pages):
        jw = canon_words(_strip_image_markup(page["markdown"]["text"]))
        md_json_pages.append(
            {
                "page": i + 1,
                "json_words": len(jw),
                "md_coverage": round(_multiset_coverage(jw, md_body_stripped_words), 4),
            }
        )
    md_json_bad = [
        m for m in md_json_pages if m["json_words"] >= 20 and m["md_coverage"] < MD_JSON_FAIL_BELOW
    ]
    from collections import Counter

    missing_words = Counter(doc_src) - Counter(doc_out)

    src_nums = canon_numbers("\n".join(src_pages))
    out_nums = canon_numbers(md_text)  # md is what downstream consumes
    missing_all = sorted(set(src_nums) - set(out_nums), key=lambda s: (-len(s), s))
    missing_nums = [n for n in missing_all if len(n) >= MIN_SIGNIFICANT_NUM_DIGITS]

    wikilinks = WIKILINK_RE.findall(md_text)
    missing_links = sorted({w for w in wikilinks if not (out_dir / "images" / w).is_file()})

    content_pages = [m for m in page_metrics if m["src_words"] >= MIN_PAGE_WORDS]
    bad_pages = [m for m in content_pages if m["coverage"] < fail_below]
    warn_pages = [m for m in content_pages if fail_below <= m["coverage"] < warn_below]
    if bad_pages or missing_links or doc_coverage < FAIL_DOC_BELOW or md_json_bad:
        verdict = "FAIL"
    elif (
        warn_pages
        or doc_coverage < WARN_DOC_BELOW
        or fail_below <= doc_coverage < WARN_DOC_BELOW
        or missing_nums
    ):
        verdict = "WARN"
    else:
        verdict = "PASS"

    manifest = {
        "schema_version": 1,
        "source": {
            "path": str(pdf_path),
            "sha256": _sha256(pdf_path),
            "pages": len(src_pages),
        },
        "extraction": {
            "md": str(md_path),
            "sha256": _sha256(md_path),
            "engine": _engine_of(md_text),
        },
        "thresholds": {"warn_below": warn_below, "fail_below": fail_below},
        "doc_coverage": round(doc_coverage, 4),
        "pages": page_metrics,
        "verdict": verdict,
        "missing_numbers": missing_nums[:50],
        "missing_numbers_count": len(missing_nums),
        "small_number_noise_count": len(missing_all) - len(missing_nums),
        "missing_wikilinks": missing_links,
        "md_json_pages": md_json_pages,
        "md_json_mismatch_pages": [m["page"] for m in md_json_bad],
        "top_missing_words": [w for w, _ in missing_words.most_common(15)],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


def _engine_of(md_text: str) -> str:
    m = re.search(r"by:\s*[\'\"]?([^\n\'\"]+)", md_text)
    return m.group(1).strip() if m else "unknown"


def summarize(manifest: dict) -> str:
    m = manifest
    lo = min((p["coverage"] for p in m["pages"]), default=1.0)
    line = f"verify {m['verdict']}: doc coverage {m['doc_coverage']:.1%}, lowest page {lo:.1%}"
    if m["missing_numbers_count"]:
        line += f", {m['missing_numbers_count']} source number(s) missing"
    if m["missing_wikilinks"]:
        line += f", {len(m['missing_wikilinks'])} broken wikilink(s)"
    if m["md_json_mismatch_pages"]:
        line += f", md/json mismatch on page(s) {m['md_json_mismatch_pages']}"
    return line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify converted markdown vs source PDF")
    ap.add_argument("source_pdf")
    ap.add_argument("output_dir")
    ap.add_argument("--stem", help="note stem (default: PDF stem)")
    ap.add_argument("--warn-below", type=float, default=WARN_BELOW)
    ap.add_argument("--fail-below", type=float, default=FAIL_BELOW)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    pdf_path = Path(args.source_pdf)
    out_dir = Path(args.output_dir)
    if not pdf_path.is_file() or not out_dir.is_dir():
        print("error: need an existing PDF and output dir", file=sys.stderr)
        return 2
    stem = args.stem or pdf_path.stem
    try:
        manifest = verify(
            pdf_path, out_dir, stem, warn_below=args.warn_below, fail_below=args.fail_below
        )
    except (OSError, KeyError, json.JSONDecodeError) as e:
        print(f"error: verification failed: {e}", file=sys.stderr)
        return 2
    if not args.quiet:
        print(summarize(manifest))
        print(f"manifest: {out_dir / (stem + '.verify.json')}")
    return 0 if manifest["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
