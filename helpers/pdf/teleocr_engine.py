#!/usr/bin/env python3
"""TeleOCR engine — mirrors `helpers/pdf/liteparse_engine.py::convert` shape.

Renders PDF pages via `pymupdf` and OCRs them through a llama.cpp
`llama-server` running the TeleOCR (ex NaviDC-OCR) Qwen2.5-VL GGUF
(Q4_K_M + mmproj-f16 vision encoder, both in `models/`). Trial evidence and
the full write-up live in `doc/local/evaluations/local_pdf_engine_trial.md`
(§Addendum "TeleOCR (NaviDC-OCR) GGUF trial on CPU") and
`doc/improvements/proposals/teleocr_pdf_fallback.md`.

Server contract (D3, assume-running): this engine owns NO process
supervision. It requires a healthy llama-server on `127.0.0.1:8731`
(override port via `TELEOCR_PORT`); `make teleocr-server` starts it. The
request surface is one OpenAI-compatible `/v1/chat/completions` POST per
page — the binary, build tree and patch
(`bench_data/teleocr/llama_cpp_qwen2vl_teleocr.patch`, required: this
model's head_dim 128 breaks stock `qwen2vl` loading) stay box-side.

Guards (the trial's load-bearing findings): the official TeleOCR prompt,
temperature 0 + `repeat_penalty 1.15`/`repeat_last_n 256` (greedy decoding
alone hits degenerate repetition loops), a `max_tokens` cap with ONE retry
at a higher penalty, then refusal — a capped-out page is never emitted.
Output goes through a LaTeX-unwrap post-process (the model serialises plain
text as `\\mbox{}`/array LaTeX with space-joined words) plus a MinerU
`<fcel>`/`<nl>` token strip.

Returns the same `pages` shape as `pdf_local.convert` / `liteparse_engine.convert`:

    [{"prunedResult": {"teleocr": {...}}, "markdown": {"text": ..., "images": {}},
      "outputImages": [], "inputImage": None}]

so `helpers/pdf/pdf_conv_md.py` downstream is engine-agnostic. No image
extraction: scanned OCR pages rarely carry figures, and `pdf_local` keeps
the figure pipeline.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pymupdf

ENGINE_LABEL = "teleocr-navidc-q4_k_m"
TELEOCR_HOST = "127.0.0.1"
TELEOCR_PORT = int(os.environ.get("TELEOCR_PORT", "8731"))
REQUEST_TIMEOUT_S = 1800  # dense pages took ~500s in the trial; 30 min headroom
DPI = 150

# Official TeleOCR prompts (TeleOCR repo base_client.py DEFAULT_*_PROMPT)
SYSTEM_PROMPT = "You are a helpful assistant."
USER_PROMPT = "What is the text in the illustrate?"

# Trial guards — see the addendum: temp 0 alone hits repetition loops
REPEAT_PENALTY = 1.15
REPEAT_LAST_N = 256
RETRY_PENALTY = 1.3  # one retry at this when the first pass caps out
MAX_TOKENS = 2048

# llama-server identity: warn (not fail) when the port serves something else
MODEL_ID_SUBSTRING = "navidc"


class TeleOCRError(Exception):
    """Base for TeleOCR engine failures."""


class TeleOCRUnavailable(TeleOCRError):
    """llama-server unreachable (or unhealthy) — include the start command."""


class TeleOCRRefused(TeleOCRError):
    """Page capped out twice — suspected repetition loop; nothing emitted."""


_START_HINT = (
    "start it with `make teleocr-server` "
    "(llama-server -m models/NaviDC-OCR-Q4_K_M.gguf "
    "--mmproj models/NaviDC-OCR-mmproj-f16.gguf --port 8731); "
    "see bench_data/teleocr/README.md"
)


def _base_url(port: int | None = None) -> str:
    return f"http://{TELEOCR_HOST}:{port or TELEOCR_PORT}"


def preflight(port: int | None = None) -> str:
    """Health + identity probe. Returns the served model id.

    Raises `TeleOCRUnavailable` when the server is down (message carries the
    start command); warns to stderr when the port serves a non-TeleOCR model.
    """
    base = _base_url(port)
    try:
        # scheme+host are the engine's own loopback constant, never caller-supplied
        with urllib.request.urlopen(f"{base}/health", timeout=10) as r:  # noqa: S310
            body = json.load(r)
        if body.get("status") != "ok":
            raise TeleOCRUnavailable(f"teleocr server unhealthy: {body} — {_START_HINT}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise TeleOCRUnavailable(
            f"teleocr server not reachable on {base}: {e} — {_START_HINT}"
        ) from e
    try:
        with urllib.request.urlopen(f"{base}/v1/models", timeout=10) as r:  # noqa: S310  # loopback constant, see above
            body = json.load(r)
        # llama-server shape drifted: {"data":[{"id":...}]} (OpenAI) vs
        # {"models":[{"name":...}]} (recent builds) — accept both.
        models = body.get("data") or body.get("models") or []
        model_id = ""
        if models:
            model_id = str(models[0].get("id") or models[0].get("name") or "")
        if MODEL_ID_SUBSTRING not in model_id.lower():
            print(
                f"  warn: port {port or TELEOCR_PORT} serves '{model_id or '?'}' — "
                f"expected a {MODEL_ID_SUBSTRING}* GGUF",
                file=sys.stderr,
                flush=True,
            )
        return model_id
    except urllib.error.URLError, OSError, json.JSONDecodeError:
        return ""


def ocr_image(
    png: bytes,
    *,
    port: int | None = None,
    user_prompt: str = USER_PROMPT,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """OCR one PNG through llama-server, with the cap-out retry ladder.

    Raises `TeleOCRRefused` when both passes hit the token cap (the trial's
    repetition-loop signature) — the caller emits nothing for that page.
    """
    b64 = base64.b64encode(png).decode()

    def _request(repeat_penalty: float) -> dict:
        body = json.dumps(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            },
                            {"type": "text", "text": user_prompt},
                        ],
                    },
                ],
                "temperature": 0.0,
                "repeat_penalty": repeat_penalty,
                "repeat_last_n": REPEAT_LAST_N,
                "max_tokens": max_tokens,
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310  # loopback constant, see above
            f"{_base_url(port)}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:  # noqa: S310  # loopback constant, see above
                return json.load(r)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            raise TeleOCRUnavailable(f"teleocr request failed: {e} — {_START_HINT}") from e

    resp = _request(REPEAT_PENALTY)
    choice = (resp.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        # Loop signature from the trial: cap-out mid-page. One retry, stiffer
        # penalty; a second cap-out refuses the page rather than emit a
        # truncated/repeating one.
        resp = _request(RETRY_PENALTY)
        choice = (resp.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise TeleOCRRefused(
                f"page capped out twice at max_tokens={max_tokens} — repetition loop, refusing"
            )
    return choice.get("message", {}).get("content", "")


# --- LaTeX unwrap post-process ------------------------------------------------

_MINERU_TOKEN_RE = re.compile(r"</?(?:f|e|l)?cel>|<nl>")
_CMD_RE = re.compile(r"\\(?:begin|end)\{[a-z]+\}")
_TEXTBOX_RE = re.compile(r"\\(?:mbox|text|mathrm|mathit|mathbf)\{([^{}]*)\}")
_CMD_TAIL_RE = re.compile(r"\\[a-zA-Z]+")
# Common math commands -> unicode glyphs (kept, not stripped: formulas are the
# reason this engine exists — trial formula pages carry ∫ Σ √ π α β γ μ σ)
_CMD_UNICODE = {
    "int": "∫",
    "sum": "Σ",
    "Sigma": "Σ",
    "sqrt": "√",
    "pi": "π",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "mu": "μ",
    "sigma": "σ",
    "infty": "∞",
    "cdot": "·",
    "times": "×",
    "approx": "≈",
    "rightarrow": "→",
    "to": "→",
    "pm": "±",
    "leq": "≤",
    "geq": "≥",
    "neq": "≠",
}
_CMD_UNI_RE = re.compile(r"\\(" + "|".join(_CMD_UNICODE) + r")(?![a-zA-Z])")
_CMD_UNICODE_SUB = lambda m: _CMD_UNICODE[m.group(1)]  # noqa: E731
# LaTeX escaped literals (\& \% \$ \# \_) decode to the literal char BEFORE
# the blanket strip — they carry prose/number content ("Nykaa \& More",
# "12.34\%"). \! is a NEGATIVE thin space (pure artifact: "12,\!400" ->
# "12,400"); \, \; \: are thin spaces -> one space.
_ESCAPED_LIT_RE = re.compile(r"\\([&%$#_{}])")
_ESCAPED_PLACEHOLDERS = {
    "&": "AMP",
    "%": "PCT",
    "$": "DOL",
    "#": "HSH",
    "_": "UND",
    "{": "LBR",
    "}": "RBR",
}


def _escaped_lit_repl(m: re.Match) -> str:
    return f"{_SENTINEL}{_ESCAPED_PLACEHOLDERS[m.group(1)]}{_SENTINEL}"


_ESCAPED_NEGSPACE_RE = re.compile(r"\\!")
_ESCAPED_SPACE_RE = re.compile(r"\\[,;:]")
# bare & = LaTeX alignment separator (structural noise); \&-decoded & is
# content and rides out the strip inside sentinels
_MATH_PUNCT_RE = re.compile(r"[\\^_~&]")
_SENTINEL = "\x00"
_DOLLAR_RE = re.compile(r"\$\$")
_BRACE_RE = re.compile(r"[{}]")
_SPACES_RE = re.compile(r"[ \t]+")
_ALPHA_RUN_RE = re.compile(r"[A-Za-z]{4,}")

_DICT_PATHS = (
    "/usr/share/dict/words",
    "/usr/share/dict/american-english",
    "/usr/share/dict/cracklib-small",
)
_DICT: set[str] | None = None


def _word_set() -> set[str]:
    """System wordlist (lazy, cached). Empty when no dict is installed —
    the join repair then degrades to the colon + case-boundary rules."""
    global _DICT
    if _DICT is None:
        words: set[str] = set()
        for cand in _DICT_PATHS:
            try:
                words = {
                    w.strip().lower()
                    for w in Path(cand).read_text(encoding="utf-8", errors="ignore").splitlines()
                    if len(w.strip()) >= 3
                }
                break
            except OSError:
                continue
        _DICT = words
    return _DICT


def _maybe_split_join(m: re.Match) -> str:
    """Split a dictionary-anchored word join ("Mixedpage" -> "Mixed page").

    The model's \\mbox serialization drops spaces at arbitrary word
    boundaries; a token is split only when BOTH parts are dictionary words
    (>=3 chars), the token itself is not a word, and it has no digits or
    all-caps acronym shape. Preserves original casing.
    """
    tok = m.group(0)
    words = _word_set()
    if not words:
        return tok
    low = tok.lower()
    if low in words or low.isupper() or len(low) < 8:
        return tok
    for i in range(3, len(low) - 2):
        if low[:i] in words and low[i:] in words:
            return f"{tok[:i]} {tok[i:]}"
    return tok


def _repair_word_joins(text: str) -> str:
    """Repair the model's space-collapse serialization:
    "Mixedpage:Financialnewsletter" -> "Mixed page: Financial newsletter".
    Colon rule + lower->Upper boundary rule always apply; dictionary
    segmentation handles the lower->lower joins when a wordlist exists.
    """
    text = re.sub(r"(?<=[a-z]):(?=[A-Za-z])", ": ", text)
    # lower->Upper boundary split; 3+ lowercase context required so mixed-case
    # acronyms (LaTeX, McDonald) survive: "andLaTeX" splits, "LaTeX" doesn't.
    text = re.sub(r"(?<=[a-z]{3})(?=[A-Z])", " ", text)
    return _ALPHA_RUN_RE.sub(_maybe_split_join, text)


def unwrap_latex(text: str) -> str:
    """TeleOCR LaTeX/array/fcel serialization -> plain markdown-ish text."""
    text = _MINERU_TOKEN_RE.sub(" ", text)
    text = _DOLLAR_RE.sub(" ", text)
    # Unwrap one nesting level at a time: \mbox{...\mbox{...}...}
    for _ in range(8):
        stripped = _TEXTBOX_RE.sub(r"\1", text)
        stripped = _CMD_RE.sub(" ", stripped)
        if stripped == text:
            break
        text = stripped
    text = _ESCAPED_LIT_RE.sub(_escaped_lit_repl, text)
    text = _ESCAPED_NEGSPACE_RE.sub("", text)  # \! -> nothing
    text = _ESCAPED_SPACE_RE.sub(" ", text)  # \, \; \: -> space
    text = _CMD_UNI_RE.sub(_CMD_UNICODE_SUB, text)  # \int -> ∫ etc.
    text = _CMD_TAIL_RE.sub(" ", text)
    text = _MATH_PUNCT_RE.sub(" ", text)
    text = _BRACE_RE.sub(" ", text)
    for real, token in _ESCAPED_PLACEHOLDERS.items():
        text = text.replace(f"{_SENTINEL}{token}{_SENTINEL}", real)
    text = text.replace(_SENTINEL, "")
    text = _repair_word_joins(text)
    text = _SPACES_RE.sub(" ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def render_pages(pdf_path: Path, *, dpi: int = DPI) -> list[bytes]:
    """Render each PDF page to PNG bytes at `dpi` (trial setting)."""
    doc = pymupdf.open(str(pdf_path))
    try:
        mat = pymupdf.Matrix(dpi / 72, dpi / 72)
        return [page.get_pixmap(matrix=mat).tobytes("png") for page in doc]
    finally:
        doc.close()


def convert(
    pdf_path: Path,
    img_dir: Path | None = None,  # accepted for engine call-site symmetry; unused
    *,
    dpi: int = DPI,
    port: int | None = None,
    max_tokens: int = MAX_TOKENS,
) -> list[dict]:
    """Parse a (typically scanned) PDF via TeleOCR, pdf_conv_md-compatible.

    Raises `TeleOCRUnavailable` when the llama-server contract (D3) is not
    met, `TeleOCRRefused` when a page loops capped-out twice.
    """
    pdf_path = Path(pdf_path)
    preflight(port)
    pages: list[dict] = []
    for idx, png in enumerate(render_pages(pdf_path, dpi=dpi)):
        raw = ocr_image(png, port=port, max_tokens=max_tokens)
        pages.append(
            {
                "prunedResult": {
                    "teleocr": {
                        "page_num": idx + 1,
                        "engine": ENGINE_LABEL,
                        "chars": len(raw),
                    }
                },
                "markdown": {"text": unwrap_latex(raw), "images": {}},
                "outputImages": [],
                "inputImage": None,
            }
        )
    return pages
