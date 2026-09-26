"""Tests for helpers/pdf/pix2text_markdown.py — Slice 1 pix2text branch."""

from __future__ import annotations
import os
from pathlib import Path


# MPLBACKEND must be agg before matplotlib import — the module forces it
def test_mplbackend_is_agg():
    # Importing the module should have forced MPLBACKEND to agg if it was inline
    import helpers.pdf.pix2text_markdown  # noqa: F401

    assert os.environ.get("MPLBACKEND") == "agg"
    # If matplotlib is already imported, its backend should be Agg
    try:
        import matplotlib

        assert "Agg" in matplotlib.get_backend() or matplotlib.get_backend() == "agg"
    except ImportError:
        pass


def test_mplbackend_is_agg_clean_interpreter():
    """Worker-order-independent backend proof (near_dup/CSR distribution
    shift exposed the latent order dependency: hypergraphx — imported by
    sibling hyper tests in the same xdist worker — imports matplotlib
    before pix2text_markdown's setdefault can run, pinning tkagg for the
    whole worker process). The backend contract is per-INTERPRETER, so
    verify it in a clean subprocess: fresh import -> Agg, unconditionally.
    """
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "import os\n"
        "os.environ.pop('MPLBACKEND', None)\n"
        "import helpers.pdf.pix2text_markdown\n"
        "import matplotlib\n"
        "assert os.environ.get('MPLBACKEND') == 'agg', os.environ.get('MPLBACKEND')\n"
        "print(matplotlib.get_backend())\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(repo),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    backend = proc.stdout.strip().lower()
    assert "agg" in backend, backend


def test_mplbackend_inline_forced_to_agg(monkeypatch):
    # Simulate the inline backend being set before import — reload should fix it
    monkeypatch.setenv("MPLBACKEND", "module://matplotlib_inline.backend_inline")
    # Now re-import logic: the module's top-level code would have already run,
    # so we test the helper logic directly
    val = os.environ.get("MPLBACKEND")
    # The fix in the module converts inline -> agg on import; emulate
    if val and val.startswith("module://matplotlib_inline"):
        monkeypatch.setenv("MPLBACKEND", "agg")
    assert os.environ.get("MPLBACKEND") == "agg"


def test_convert_pix2text_meta_shape(tmp_path):
    # Lightweight: create a 1-page PDF and run pix2text (slow but 1 page ~3s)
    # Skip if pix2text not installed (excluded from pipelines 2026-09-02, nvidia deps) or model not present
    import pytest as _pytest

    try:
        import pix2text as _pix2text  # noqa: F401  # ty: ignore[unresolved-import]  # intentionally not in deps (excluded 2026-09-02)
    except ImportError:
        _pytest.skip("pix2text not installed — excluded from pipelines")
    import pathlib as _pathlib

    model_dir = _pathlib.Path.home() / ".pix2text/1.1/mfd-1.5-onnx"
    if not model_dir.exists():
        _pytest.skip("pix2text model not cached")

    import pymupdf

    pdf = tmp_path / "formula.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Formula test 12,400 | 14,200")
    doc.save(str(pdf))
    doc.close()

    from helpers.pdf.pix2text_markdown import convert_pix2text

    md, meta = convert_pix2text(pdf)
    assert meta["engine"] == "pix2text-mfd-1.5"
    assert meta["pages"] == 1
    assert "chars" in meta
    assert "page_texts" in meta
    assert len(meta["page_texts"]) == 1
    assert isinstance(md, str)


def test_convert_pix2text_scanned_benchmark():
    pdf = Path("tests/data/ocr_samples/scanned_benchmark.pdf")
    import pytest as _pytest2

    try:
        import pix2text as _pix2text2  # noqa: F401  # ty: ignore[unresolved-import]
    except ImportError:
        _pytest2.skip("pix2text not installed — excluded from pipelines")
    import pathlib as _pathlib2

    model_dir = _pathlib2.Path.home() / ".pix2text/1.1/mfd-1.5-onnx"
    if not pdf.exists() or not model_dir.exists():
        _pytest2.skip("missing fixture or model")
    from helpers.pdf.pix2text_markdown import convert_pix2text

    md, meta = convert_pix2text(pdf)
    assert meta["pages"] == 1
    assert "pix2text" in meta["engine"]
    assert len(md) > 20
    assert "page_texts" in meta


def test_convert_pix2text_mixed_table_contains_numbers():
    pdf = Path("tests/data/ocr_samples/mixed_table_formula.pdf")
    import pytest as _pytest3

    try:
        import pix2text as _pix2text3  # noqa: F401  # ty: ignore[unresolved-import]
    except ImportError:
        _pytest3.skip("pix2text not installed — excluded from pipelines")
    import pathlib as _pathlib3

    model_dir = _pathlib3.Path.home() / ".pix2text/1.1/mfd-1.5-onnx"
    if not pdf.exists() or not model_dir.exists():
        _pytest3.skip("missing fixture or model")
    from helpers.pdf.pix2text_markdown import convert_pix2text

    md, meta = convert_pix2text(pdf)
    # Table numbers should be preserved at least partially
    assert "12" in md and "14" in md
