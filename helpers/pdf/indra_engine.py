#!/usr/bin/env python3
"""Indra OCR engine — mirrors `helpers/pdf/teleocr_engine.py::convert` shape.

Rung 2 of the `pdf_conv_md.py` chain (`pdf_engine_indra_ocr_rung2` proposal,
2026-10-26), replacing the lite OCR Tesseract fallback: Indra OCR 1.1.2 runs
CPU-only in-process ONNX Runtime (det/rec/order models, ~76 MB, no server) and
matched teleocr's digit fidelity on all four trial probes at <1 s/page —
including printed math Tesseract mangles (trial table:
`bench_data/indra/trial_table.md`, probes in
`tests/data/ocr_datasets_samples/`).

Model contract: weights download on first run from Hugging Face
(`datalab-to/indra-ocr` @ v1.1.0, unauthenticated, cached by huggingface_hub);
point `INDRA_OCR_MODEL_DIR` at a local folder to stay offline. Weights are NOT
bundled into `models/` — they carry the AI PUBS OpenRAIL-M (modified) license
(S4 house check 2026-10-26: use compliant via the personal/research carve-out;
redistribution permitted with license-copy + attribution conditions, so a
bundled copy must ship `models/indra-ocr/LICENSE`).

mode="ocr" always: rung 2 only sees pages whose text layer `pdf_local`
already refused, so the layer is never trusted here. No image extraction
(teleocr precedent): scanned OCR pages rarely carry figures, and `pdf_local`
keeps the figure pipeline; picture regions ride in `prunedResult`.

Returns the same `pages` shape as `pdf_local.convert` / `teleocr_engine.convert`:

    [{"prunedResult": {"indra": {...}}, "markdown": {"text": ..., "images": {}},
      "outputImages": [], "inputImage": None}]

so `helpers/pdf/pdf_conv_md.py` downstream is engine-agnostic.
"""

from __future__ import annotations

import sys
from pathlib import Path

ENGINE_LABEL = "indra-ocr-1.1.2"
# Proposal-contract name for the same label (build_okf_frontmatter engine field).
INDRA_ENGINE_LABEL = ENGINE_LABEL
DPI = 200.0  # trial setting (indra default)
THREADS = 4  # OrtOCR intra-op threads; gates run parallel — no all-core grab


class IndraUnavailable(Exception):
    """indra_ocr missing, or the models cannot be loaded/downloaded."""


_CONVERTER_CACHE: dict[tuple[str, float, int], object] = {}


def _converter(mode: str = "ocr", dpi: float = DPI, threads: int = THREADS):
    """Load and cache the indra Converter (ONNX session init is per-process).

    Raises `IndraUnavailable` when indra_ocr is not installed or its models
    are neither cached nor downloadable (ModelError/InputError carry the
    library's message).
    """
    key = (mode, dpi, threads)
    if key in _CONVERTER_CACHE:
        return _CONVERTER_CACHE[key]
    try:
        import indra_ocr
    except ImportError as e:
        raise IndraUnavailable(
            "indra_ocr not installed — `uv pip install indra-ocr` "
            "(rung 2 of the pdf_conv_md chain; see "
            "doc/improvements/archive/pipeline/pdf_engine_indra_ocr_rung2.md)"
        ) from e
    try:
        conv = indra_ocr.load(mode=mode, dpi=dpi, threads=threads)
    except Exception as e:  # noqa: BLE001  # ModelError/InputError: forward the reason
        raise IndraUnavailable(f"indra models unavailable: {e}") from e
    _CONVERTER_CACHE[key] = conv
    return conv


def convert(
    pdf_path: Path,
    img_dir: Path | None = None,  # accepted for engine call-site symmetry; unused
    *,
    mode: str = "ocr",
    dpi: float = DPI,
    threads: int = THREADS,
) -> list[dict]:
    """Parse a (typically scanned) PDF via Indra OCR, pdf_conv_md-compatible.

    Raises `IndraUnavailable` when the package or its models are not
    available. A page that fails inside the library yields an entry whose
    `prunedResult.indra.source` is "error" (one bad page must not end the
    document); an all-error document surfaces through `verify_extraction`.
    """
    conv = _converter(mode=mode, dpi=dpi, threads=threads)
    pages: list[dict] = []
    for idx, page in conv.convert(pdf_path):
        md_text = page.markdown if page.markdown is not None else page.text
        if page.source == "error":
            print(
                f"  warn: indra page {idx + 1}: {page.note}",
                file=sys.stderr,
                flush=True,
            )
        pages.append(
            {
                "prunedResult": {
                    "indra": {
                        "page_num": idx + 1,
                        "engine": ENGINE_LABEL,
                        "chars": len(md_text),
                        "source": page.source,
                        "note": page.note,
                    }
                },
                "markdown": {"text": md_text, "images": {}},
                "outputImages": [],
                "inputImage": None,
            }
        )
    return pages
