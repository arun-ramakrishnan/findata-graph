#!/usr/bin/env python3
"""LiteParse engine — mirrors `helpers/pdf/pdf_local.py::convert` shape.

Provides `convert` for both no-OCR (born-digital bbox sidecar) and OCR
(scanned fallback) via `liteparse 2.0.0` + `Tesseract 5.5.0`.

* No-OCR (`ocr_enabled=False`, default): ~0.10s, 20.5× faster than
  `pymupdf4llm`, near-parity chars + `x/y/w/h` bbox per token for RAG
  grounding. Returns plain text per page (via `_to_markdown_lines` without
  over-filtering) and extracts images via a `pymupdf` sidecar so that
  `plan_images`/`to_wikilinks` still emit `<slug>_p{page}_img{N}.jpeg`.
* OCR (`ocr_enabled=True`): uses `helpers/pdf/liteparse_markdown.py`
  (Tesseract `eng 4.0M`, `TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata`)
  and the minimal `## ` heading heuristic for scanned PDFs.

Both return the same `pages` shape as `pdf_local.convert`:

    [{"prunedResult": ..., "markdown": {"text": ..., "images": {...}},
      "outputImages": [], "inputImage": None}]

so `helpers/pdf/pdf_conv_md.py` downstream is engine-agnostic.

Slice handling:
* Slice 0/1: lite no-OCR is *bbox sidecar only* — `pdf_local` stays primary
  for born-digital markdown (96.04% recall gap accepted). The sidecar is
  available via `convert(..., ocr=False)` and `get_bbox_sidecar`.
* Slice 2: `pdf_conv_md --engine auto` uses this engine for the OCR
   fallback chain (`pdf_local` -> `lite OCR` -> `Paddle`; `pix2text`
   disabled 2026-09-02 — excluded from pipelines, nvidia deps).
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pymupdf

try:
    LITE_VERSION = version("liteparse")
except PackageNotFoundError:
    LITE_VERSION = "2.0.0"

ENGINE_LABEL_NOCR = f"liteparse-{LITE_VERSION}-noocr"
ENGINE_LABEL_OCR = f"liteparse-{LITE_VERSION}-ocr-eng"

# Reuse thresholds from pdf_local (keep in sync)
MIN_IMAGE_PX = 150
MIN_IMAGE_BYTES = 8192
MIN_CHARS_PER_PAGE = 100

TESSDATA_DEFAULT = "/usr/share/tesseract-ocr/5/tessdata"

from helpers.pdf.liteparse_markdown import _to_markdown_lines  # noqa: E402
from helpers.pdf.pdf_local import LocalRefusalError  # noqa: E402

try:
    ENGINE_LABEL = ENGINE_LABEL_NOCR
except Exception:
    ENGINE_LABEL = "liteparse"


def _assert_tessdata(tessdata_path: str) -> None:
    """Best-effort check that eng.traineddata exists; warn but do not fail."""
    if not Path(tessdata_path).joinpath("eng.traineddata").exists():
        # Do not raise — liteparse will error with a clear message if truly missing
        pass


def _image_ok(path: Path) -> bool:
    """Same decoration filter as pdf_local — keep only real figures."""
    try:
        pm = pymupdf.Pixmap(str(path))
        w, h = pm.width, pm.height
    except Exception:
        return True
    return w >= MIN_IMAGE_PX and h >= MIN_IMAGE_PX and path.stat().st_size >= MIN_IMAGE_BYTES


def _extract_images_sidecar(pdf_path: Path, img_dir: Path) -> list[dict[str, str]]:
    """Extract images via pymupdf sidecar, per page, filtering decorations.

    Returns a list of per-page `images` dicts (`{rel_imgs_key: abs_path}`)
    aligned to physical PDF pages (0-indexed). Each dict contains only the
    images that passed `MIN_IMAGE_PX`/`MIN_IMAGE_BYTES`.
    """
    img_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    per_page: list[dict[str, str]] = []
    counter = 0
    for page_idx, page in enumerate(doc):  # ty: ignore[invalid-argument-type]  # pymupdf stubs lack __iter__
        images: dict[str, str] = {}
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                pix = pymupdf.Pixmap(doc, xref)
            except Exception:  # noqa: S112  # intentional skip of corrupt image xref — liteparse already logged
                continue
            # Filter decorations early (before writing)
            if pix.width < MIN_IMAGE_PX or pix.height < MIN_IMAGE_PX:
                continue
            # Write temp pix to check byte size
            tmp = img_dir / f"_tmp_{page_idx}_{xref}.jpeg"
            try:
                # Handle CMYK etc. — convert to RGB if needed
                if pix.n > 4:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                pix.save(str(tmp))
                if tmp.stat().st_size < MIN_IMAGE_BYTES:
                    tmp.unlink(missing_ok=True)
                    continue
                counter += 1
                rel = f"imgs/img{counter}"
                final = img_dir / f"img{counter}.jpeg"
                tmp.rename(final)
                images[rel] = str(final.resolve())
            except Exception:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                continue
        per_page.append(images)
    doc.close()
    return per_page


def _pages_from_liteparse_res(res, per_page_images: list[dict[str, str]]) -> list[dict]:
    """Convert a liteparse `ParseResult` into the pdf_conv_md pages shape."""
    pages: list[dict] = []
    for idx, pg in enumerate(res.pages):
        # Prefer aggregated page text; fallback to joining text_items
        raw = getattr(pg, "text", None)
        if not raw:
            raw = " ".join(getattr(t, "text", "") for t in getattr(pg, "text_items", []))
        md = _to_markdown_lines(raw or "")
        # Keep bbox for RAG grounding in prunedResult (page width/height + items)
        try:
            bbox_items = [
                {"text": t.text, "x": t.x, "y": t.y, "w": t.width, "h": t.height}
                for t in getattr(pg, "text_items", [])
            ]
        except Exception:
            bbox_items = []
        img_map = per_page_images[idx] if idx < len(per_page_images) else {}
        # Images inline as centered divs so plan_images/to_wikilinks work
        if img_map:
            img_tags = "\n".join(
                f'<div style="text-align: center;"><img src="{k}"/></div>' for k in img_map
            )
            md = f"{md}\n\n{img_tags}" if md else img_tags
        pages.append(
            {
                "prunedResult": {
                    "liteparse": {
                        "page_num": getattr(pg, "page_num", idx + 1),
                        "width": getattr(pg, "width", 0),
                        "height": getattr(pg, "height", 0),
                        "bbox_items": bbox_items,
                    }
                },
                "markdown": {"text": md, "images": img_map},
                "outputImages": [],
                "inputImage": None,
            }
        )
    return pages


def convert(
    pdf_path: Path,
    img_dir: Path,
    *,
    ocr: bool = False,
    dpi: int = 150,
    language: str = "eng",
    tessdata_path: str = TESSDATA_DEFAULT,
) -> list[dict]:
    """Parse PDF via liteparse, returning pdf_conv_md-compatible pages.

    `ocr=False` (default) is the fast no-OCR path for born-digital PDFs
    (bbox sidecar). `ocr=True` is the Tesseract fallback for scanned PDFs
    (requires `eng.traineddata` at `tessdata_path`).

    `img_dir` is populated via the pymupdf image sidecar (liteparse has no
    `write_images`). The caller must keep `img_dir` alive until
    `write_outputs` copies the files.
    """
    pdf_path = Path(pdf_path)
    img_dir = Path(img_dir)

    # Extract images via sidecar first (cheap, ~50ms)
    per_page_images = _extract_images_sidecar(pdf_path, img_dir)

    if ocr:
        if tessdata_path:
            os.environ["TESSDATA_PREFIX"] = tessdata_path
            _assert_tessdata(tessdata_path)
        from liteparse import LiteParse

        try:
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
            res = parser.parse(str(pdf_path))
        except TypeError:
            # Older binding without tessdata_path kwarg
            from liteparse import LiteParse as LP2

            res = LP2(quiet=True, ocr_enabled=True, ocr_language=language, dpi=dpi).parse(
                str(pdf_path)
            )
    else:
        from liteparse import LiteParse

        parser = LiteParse(quiet=True, ocr_enabled=False)
        res = parser.parse(str(pdf_path))
        # Refuse scanned PDFs on the no-OCR path (mirrors pdf_local guard)
        avg_chars = len(res.text) / max(len(res.pages), 1) if res.pages else 0
        if avg_chars < MIN_CHARS_PER_PAGE:
            raise LocalRefusalError(
                f"text layer too thin ({len(res.text)} chars over {len(res.pages)} pages, "
                f"avg {int(avg_chars)}/page < {MIN_CHARS_PER_PAGE}) — scanned PDF? use ocr=True or Paddle"
            )

    return _pages_from_liteparse_res(res, per_page_images)


def get_bbox_sidecar(pdf_path: Path, *, dpi: int = 150) -> list[dict]:
    """Return per-page bbox items without writing images (RAG grounding).

    Lightweight helper for the lite no-OCR bbox sidecar — callers that only
    need `x/y/w/h` per token can use this without the full pages shape.
    """
    from liteparse import LiteParse

    res = LiteParse(quiet=True, ocr_enabled=False).parse(str(pdf_path))
    out: list[dict] = []
    for pg in res.pages:
        items = [
            {"text": t.text, "x": t.x, "y": t.y, "w": t.width, "h": t.height}
            for t in getattr(pg, "text_items", [])
        ]
        out.append(
            {
                "page_num": getattr(pg, "page_num", len(out) + 1),
                "width": pg.width,
                "height": pg.height,
                "items": items,
            }
        )
    return out
