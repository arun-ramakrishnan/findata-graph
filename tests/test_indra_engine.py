"""Tests for helpers/pdf/indra_engine.py — hermetic, no model download.

The indra_ocr package is stubbed (fake Converter/Page objects); the real
ONNX models are exercised by the bench arm instead
(`bench_data/indra/trial_table.md`). Pins: the pages shape
pdf_conv_md downstream expects, the markdown-None -> text fallback,
per-page error tolerance, and the IndraUnavailable ladder (package
missing, models unavailable).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from helpers.pdf import indra_engine
from helpers.pdf.indra_engine import (
    ENGINE_LABEL,
    INDRA_ENGINE_LABEL,
    IndraUnavailable,
    convert,
)


@dataclass
class _FakePage:
    text: str = "page text"
    markdown: str | None = None
    source: str = "ocr"
    note: str = ""


class _FakeConverter:
    def __init__(self, pages: list[_FakePage]):
        self.pages = pages
        self.converted: list[str] = []

    def convert(self, path):
        self.converted.append(str(path))
        yield from enumerate(self.pages)


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(indra_engine, "_CONVERTER_CACHE", {})
    # The real package must not be importable in a hermetic test run —
    # force the stub path even on a box where indra-ocr is installed.
    monkeypatch.setitem(sys.modules, "indra_ocr", None)


def _install_stub(monkeypatch, converter: _FakeConverter) -> None:
    monkeypatch.setitem(
        sys.modules,
        "indra_ocr",
        SimpleNamespace(load=lambda **kw: converter),
    )


def test_convert_pages_shape(monkeypatch):
    conv = _FakeConverter([_FakePage(markdown="## Hello\n12,400")])
    _install_stub(monkeypatch, conv)
    pages = convert("dummy.pdf")
    assert len(pages) == 1
    page = pages[0]
    assert page["markdown"] == {"text": "## Hello\n12,400", "images": {}}
    assert page["outputImages"] == []
    assert page["inputImage"] is None
    pruned = page["prunedResult"]["indra"]
    assert pruned["page_num"] == 1
    assert pruned["engine"] == ENGINE_LABEL == INDRA_ENGINE_LABEL == "indra-ocr-1.1.2"
    assert pruned["chars"] == len("## Hello\n12,400")
    assert pruned["source"] == "ocr"


def test_convert_markdown_none_falls_back_to_text(monkeypatch, capsys):
    conv = _FakeConverter([_FakePage(text="plain text", markdown=None)])
    _install_stub(monkeypatch, conv)
    pages = convert("dummy.pdf")
    assert pages[0]["markdown"]["text"] == "plain text"


def test_convert_per_page_error_tolerated(monkeypatch, capsys):
    conv = _FakeConverter(
        [_FakePage(source="error", note="RuntimeError: bad page", text="", markdown="")]
    )
    _install_stub(monkeypatch, conv)
    pages = convert("dummy.pdf")
    assert pages[0]["prunedResult"]["indra"]["source"] == "error"
    assert "bad page" in capsys.readouterr().err


def test_converter_cached_across_calls(monkeypatch):
    conv = _FakeConverter([_FakePage()])
    _install_stub(monkeypatch, conv)
    convert("dummy_a.pdf")
    convert("dummy_b.pdf")
    assert conv.converted == ["dummy_a.pdf", "dummy_b.pdf"]  # one load()


def test_unavailable_when_package_missing():
    # sys.modules["indra_ocr"] is None (autouse fixture): import raises.
    with pytest.raises(IndraUnavailable, match="not installed"):
        convert("dummy.pdf")


def test_unavailable_when_models_unavailable(monkeypatch):
    class _ModelError(OSError):
        pass

    def _boom(**kw):
        raise _ModelError("could not download the models from Hugging Face")

    monkeypatch.setitem(sys.modules, "indra_ocr", SimpleNamespace(load=_boom))
    with pytest.raises(IndraUnavailable, match="models unavailable"):
        convert("dummy.pdf")
