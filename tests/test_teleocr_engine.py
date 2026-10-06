"""Tests for helpers/pdf/teleocr_engine.py — S1 engine + guards.

The llama-server contract (D3, assume-running) is exercised against a
stubbed HTTP server; no llama.cpp in CI. The unwrap post-process tests pin
the trial's serialization findings (LaTeX house style, MinerU token leaks,
word-join defects).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from helpers.pdf.teleocr_engine import (
    ENGINE_LABEL,
    MAX_TOKENS,
    REPEAT_PENALTY,
    RETRY_PENALTY,
    TeleOCRRefused,
    TeleOCRUnavailable,
    _word_set,
    convert,
    ocr_image,
    preflight,
    unwrap_latex,
)

HAS_DICT = bool(_word_set())

# --- stub llama-server ---------------------------------------------------------

STATE: dict[str, Any] = {"model_id": "NaviDC-OCR-Q4_K_M", "responses": [], "requests": []}


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # silence
        return

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802  # http.server API
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._send(200, {"data": [{"id": STATE["model_id"]}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802  # http.server API
        assert self.path == "/v1/chat/completions"
        length = int(self.headers.get("Content-Length", 0))
        STATE["requests"].append(json.loads(self.rfile.read(length)))
        action = STATE["responses"].pop(0) if STATE["responses"] else {"content": "ok"}
        if action == "conn-drop":
            self.wfile.close()
            return
        finish = action.get("finish_reason", "stop")
        self._send(
            200,
            {
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {"role": "assistant", "content": action.get("content", "")},
                    }
                ]
            },
        )


@pytest.fixture()
def stub_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    STATE["requests"] = []
    STATE["responses"] = []
    STATE["model_id"] = "NaviDC-OCR-Q4_K_M"
    yield srv.server_address[1]
    srv.shutdown()


def _chat_content(body: dict) -> str:
    parts = body["messages"][-1]["content"]
    return next(p["text"] for p in parts if p["type"] == "text")


# --- unwrap post-process -------------------------------------------------------


def test_unwrap_latex_array_mbox():
    raw = "$${\\begin{array}{l}\\mbox{Mixedpage:Financialnewsletter + table + formula}\\\\ \\mbox{Note:OCR must keep pipe |, commas}\\\\\\end{array}}$$"
    out = unwrap_latex(raw)
    if HAS_DICT:
        assert "Mixed page: Financial newsletter + table + formula" in out
    else:
        assert "Mixedpage: Financialnewsletter + table + formula" in out  # degraded rule set
    assert "Note: OCR must keep pipe |, commas" in out
    assert "\\mbox" not in out and "$$" not in out and "begin" not in out


def test_unwrap_strips_mineru_tokens():
    out = unwrap_latex("<fcel>Header<nl><ecel><fcel>Cell<lcel>")
    assert "<fcel>" not in out and "<nl>" not in out and "<lcel>" not in out
    assert "Header" in out and "Cell" in out


def test_unwrap_keeps_table_pipes_and_numbers():
    raw = (
        "$${\\begin{array}{l}\\mbox{Alpha\\quad|12,400\\quad|14,200\\quad|26,600}"
        "\\\\ \\mbox{Beta\\quad|8,300\\quad|9,100\\quad|17,400}\\end{array}}$$"
    )
    out = unwrap_latex(raw)
    assert "12,400" in out and "8,300" in out and "|" in out


def test_unwrap_split_boundaries_leave_acronyms():
    assert unwrap_latex("\\mbox{Complex:alpha = beta / gamma}") == "Complex: alpha = beta / gamma"
    # all-caps acronyms are not split by the lower->Upper rule
    assert unwrap_latex("\\mbox{USD EPS report}") == "USD EPS report"


def test_unwrap_decodes_latex_escapes():
    # the S3 finding: \! inside number groups, \& in prose, \% on percentages
    out = unwrap_latex("\\mbox{Nykaa \\& More} 12,\\!400 12.34\\%")
    assert "Nykaa & More" in out
    assert "12,400" in out
    assert "12.34%" in out


def test_unwrap_strips_alignment_ampersand_keeps_escaped():
    # bare & = LaTeX column separator (noise); \& = literal ampersand (content)
    out = unwrap_latex("\\mbox{Alpha} & \\mbox{12,400} & \\\\ \\mbox{Beta \\& Co}")
    assert "&" not in out.replace("Beta & Co", "")
    assert "Beta & Co" in out
    assert "12,400" in out


def test_unwrap_mixed_case_acronyms_survive():
    assert "La Te X" not in unwrap_latex("\\mbox{andLaTeX-like symbols}")
    assert "and LaTeX-like" in unwrap_latex("\\mbox{andLaTeX-like symbols}")


# --- client guards ---------------------------------------------------------


def test_ocr_image_sends_official_prompt_and_guards(stub_server):
    STATE["responses"] = [{"content": "hello"}]
    out = ocr_image(b"fakepng", port=stub_server)
    assert out == "hello"
    body = STATE["requests"][0]
    assert body["messages"][0] == {"role": "system", "content": "You are a helpful assistant."}
    assert _chat_content(body) == "What is the text in the illustrate?"
    assert body["temperature"] == 0.0
    assert body["repeat_penalty"] == REPEAT_PENALTY
    assert body["max_tokens"] == MAX_TOKENS


def test_capout_retries_higher_penalty_then_succeeds(stub_server):
    STATE["responses"] = [{"finish_reason": "length", "content": ""}, {"content": "recovered"}]
    out = ocr_image(b"fakepng", port=stub_server)
    assert out == "recovered"
    assert STATE["requests"][0]["repeat_penalty"] == REPEAT_PENALTY
    assert STATE["requests"][1]["repeat_penalty"] == RETRY_PENALTY


def test_double_capout_refuses(stub_server):
    STATE["responses"] = [
        {"finish_reason": "length", "content": ""},
        {"finish_reason": "length", "content": ""},
    ]
    with pytest.raises(TeleOCRRefused):
        ocr_image(b"fakepng", port=stub_server)


def test_preflight_ok(stub_server):
    assert "NaviDC" in preflight(stub_server)


def test_preflight_wrong_model_warns_but_serves(stub_server, capsys):
    STATE["model_id"] = "some-other-model"
    assert preflight(stub_server) == "some-other-model"
    assert "warn" in capsys.readouterr().err.lower()


def test_server_down_raises_unavailable_with_hint():
    with pytest.raises(TeleOCRUnavailable) as ei:
        preflight(port=1)  # nothing listens on port 1
    assert "make teleocr-server" in str(ei.value)


def test_request_failure_mid_ocr_raises_unavailable(stub_server):
    STATE["responses"] = ["conn-drop"]
    with pytest.raises(TeleOCRUnavailable):
        ocr_image(b"fakepng", port=stub_server)


# --- pages shape ---------------------------------------------------------


def _two_page_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for text in ("page one", "page two"):
        page = doc.new_page()
        page.insert_text((72, 72), text, fontname="helv", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def test_convert_pages_shape(stub_server, tmp_path):
    STATE["responses"] = [
        {"content": "\\mbox{Scanned OCR benchmark: 12,400}"},
        {"content": "\\mbox{page two body}"},
    ]
    pdf = _two_page_pdf(tmp_path / "scan.pdf")
    pages = convert(pdf, port=stub_server)
    assert len(pages) == 2
    assert pages[0]["markdown"]["text"] == "Scanned OCR benchmark: 12,400"
    assert pages[1]["prunedResult"]["teleocr"]["page_num"] == 2
    assert pages[0]["markdown"]["images"] == {}
    assert pages[0]["outputImages"] == [] and pages[0]["inputImage"] is None
    assert "teleocr" in pages[0]["prunedResult"]


def test_convert_unavailable_without_server(tmp_path):
    pdf = _two_page_pdf(tmp_path / "scan.pdf")
    with pytest.raises(TeleOCRUnavailable):
        convert(pdf, port=1)


def test_engine_label():
    assert ENGINE_LABEL.startswith("teleocr-")
