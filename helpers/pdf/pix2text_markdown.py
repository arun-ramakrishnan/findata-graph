#!/usr/bin/env python3
"""Pix2Text -> markdown for formula-heavy OCR (opt-in branch).

Opt-in formula branch per `liteparse_pdf_engine` proposal Slice 1 addendum.
Uses `pix2text` `mfd-1.5-onnx` (LaTeX for math) instead of `Tesseract`
which mangles `∫²Σ√`. Keep `liteparse_markdown.py` (Tesseract) as default
OCR fallback for financial tables/numbers (0.3s); use this only when a page
is formula-heavy (detect `∫ Σ √` or handwritten confidence <0.6) or via
`--engine pix2text` flag.

Requires: `pix2text==1.1` + `MPLBACKEND=agg`, models auto-fetched from
Hugging Face (`~/.pix2text/1.1/mfd-1.5-onnx/`). No `TESSDATA_PREFIX` needed.
"""
from __future__ import annotations

import os
from pathlib import Path

# Must be set before matplotlib is imported — `matplotlib_inline` (Jupyter)
# sets MPLBACKEND to `module://matplotlib_inline.backend_inline`, which
# matplotlib rejects as an rcParam value outside a notebook. Force Agg for
# headless Pix2Text rendering; `setdefault` is not enough when the variable
# is already `matplotlib_inline`.
if os.environ.get("MPLBACKEND", "").startswith("module://matplotlib_inline"):
    os.environ["MPLBACKEND"] = "agg"
else:
    os.environ.setdefault("MPLBACKEND", "agg")
try:
    import matplotlib  # noqa: E402  # must be after MPLBACKEND fix

    # If matplotlib was already imported with the inline backend, switch
    # before any figure is created. `force=True` is available on Matplotlib
    # >=3.6; fallback to `use` without force for older installs.
    if matplotlib.get_backend().startswith("module://matplotlib_inline"):
        try:
            matplotlib.use("Agg", force=True)  # type: ignore[call-arg]
        except TypeError:
            matplotlib.use("Agg")
except Exception:  # noqa: S110  # intentional no-log — matplotlib missing is expected in headless env
    pass  # matplotlib not installed or backend switch failed — Pix2Text will handle

def convert_pix2text(pdf_path: Path | str, *, dpi: int = 150) -> tuple[str, dict]:
    """Run Pix2Text on PDF (via PNG rendering) and return (markdown, meta).

    Renders PDF pages to images via `pymupdf`, then `Pix2Text().recognize`
    per image. Returns LaTeX-infused markdown (e.g. `$\\int$`) suitable for
    `parse_newsletter` downstream — keep `pdf_local` unchanged for born-digital.
    """
    from pix2text import Pix2Text  # ty: ignore[unresolved-import]  # intentionally not in [project].dependencies (2026-09-02, nvidia deps)
    import pymupdf

    pdf_path = Path(pdf_path)
    # Render PDF to PNGs in tmp
    doc = pymupdf.open(str(pdf_path))
    import pathlib
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="pix2text_"))
    pngs = []
    for i, page in enumerate(doc):  # ty: ignore[invalid-argument-type]  # pymupdf stubs lack __iter__
        pix = page.get_pixmap(dpi=dpi)
        p = tmp / f"page_{i+1}.png"
        pix.save(str(p))
        pngs.append(p)
    doc.close()

    p2t = Pix2Text()
    parts: list[str] = []
    for png in pngs:
        try:
            txt = p2t.recognize(str(png))
            # Pix2Text returns str or list; normalize
            if isinstance(txt, list):
                txt = "\n".join(str(x) for x in txt)
            parts.append(txt)
        except Exception as e:
            parts.append(f"<!-- pix2text failed page {png.name}: {e} -->")

    md = "\n\n".join(parts)
    meta = {
        "engine": "pix2text-mfd-1.5",
        "pages": len(pngs),
        "chars": len(md),
        "dpi": dpi,
        "page_texts": parts,
    }
    return md, meta

if __name__ == "__main__":
    import sys
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/data/ocr_samples/handwritten_formula.pdf")
    md, meta = convert_pix2text(pdf)
    print(meta)
    print(md[:2000])
