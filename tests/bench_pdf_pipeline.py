#!/usr/bin/env python3
"""Perf benchmark: local PDF pipeline (convert + render + verify) on the
largest in-tree PDF.

Exercises the whole #156/#157 path — pdf_local.convert (pymupdf4llm +
Tesseract on embedded rasters), write_outputs (image copy + wikilinks),
verify_extraction.verify (per-page coverage, md/json consistency, number
audit) — on Yes_Bank_Colgate_Allcargo.pdf (30 pages, the largest Reports/
PDF). Asserts internal budgets AND that verification passes, so a perf
regression or a correctness backslide both redden `make perf`.

Warm reference (2026-08-26, 4-core dev box): convert ≈7.4s, write ≈0.02s,
verify ≈0.11s; budgets hold ~2x headroom.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.pdf.common import slugify  # noqa: E402
from helpers.pdf.pdf_conv_md import (  # noqa: E402
    build_okf_frontmatter,
    write_outputs,
)
from helpers.pdf.pdf_local import convert  # noqa: E402
from helpers.pdf.verify_extraction import verify  # noqa: E402

PDF = REPO_ROOT / "Reports" / "Yes_Bank_Colgate_Allcargo.pdf"
CONVERT_BUDGET = 15.0
VERIFY_BUDGET = 2.0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bench_pdf_") as td_raw:
        td = Path(td_raw)
        t0 = time.perf_counter()
        pages = convert(PDF, td / "imgs")
        t1 = time.perf_counter()
        stem = slugify(PDF.stem)
        fm = build_okf_frontmatter(pages, PDF, "bench", stem, out_dir=td / "out")
        write_outputs(pages, td / "out", stem, True, fm)
        t2 = time.perf_counter()
        manifest = verify(PDF, td / "out", stem)
        t3 = time.perf_counter()
    dt_conv, dt_write, dt_ver = t1 - t0, t2 - t1, t3 - t2
    print(f"pages={len(pages)} verdict={manifest['verdict']} "
          f"doc_coverage={manifest['doc_coverage']}")
    print(f"convert={dt_conv:.2f}s write={dt_write:.2f}s verify={dt_ver:.2f}s")
    ok = True
    if dt_conv + dt_write > CONVERT_BUDGET:
        print(f"OVER_BUDGET convert+write {dt_conv + dt_write:.2f}s > {CONVERT_BUDGET}s")
        ok = False
    if dt_ver > VERIFY_BUDGET:
        print(f"OVER_BUDGET verify {dt_ver:.2f}s > {VERIFY_BUDGET}s")
        ok = False
    if manifest["verdict"] == "FAIL":
        print("FAIL verification verdict")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
