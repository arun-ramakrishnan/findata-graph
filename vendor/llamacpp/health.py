#!/usr/bin/env python3
"""One-shot health probe for the two llama-server legs
(llamacpp_unified_server_build S3 — `make llamacpp-health`).

:8732 embgemma — /v1/models identity must contain "gemma"; a live embed
                must return SERVER_DIMS (768; the 512 client-side
                truncation is the embedder's contract, not the server's).
:8731 teleocr  — /v1/models identity must contain the NaviDC GGUF name.

Exit 0 only when both legs answer with the expected identity. Usable
from maint / cron / operator shell; read-only over loopback.

Usage: python3 vendor/llamacpp/health.py [--port-gemma 8732 --port-ocr 8731]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

GEMMA_MODEL_SUBSTR = "gemma"  # same identity rule as gemma_embedder
OCR_MODEL_SUBSTR = "navidc-ocr"
GEMMA_SERVER_DIMS = 768


def _get(url: str, timeout: float = 5) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return json.load(r)


def _post(url: str, body: dict, timeout: float = 60) -> dict:
    req = urllib.request.Request(  # noqa: S310
        url, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return json.load(r)


def _identity(port: int) -> str:
    return str(_get(f"http://127.0.0.1:{port}/v1/models").get("data", [{}])[0].get("id", ""))


def check_gemma(port: int) -> list[str]:
    errs = []
    ident = _identity(port)
    if GEMMA_MODEL_SUBSTR not in ident.lower():
        errs.append(f"identity {ident!r} lacks {GEMMA_MODEL_SUBSTR!r} — wrong server on port?")
        return errs
    try:
        out = _post(f"http://127.0.0.1:{port}/v1/embeddings", {"input": "warm"})
        dims = len(out["data"][0]["embedding"])
        if dims != GEMMA_SERVER_DIMS:
            errs.append(f"embed returned {dims} dims, expected {GEMMA_SERVER_DIMS} — wrong model?")
    except Exception as e:  # noqa: BLE001 — probe reports, never raises
        errs.append(f"embed request failed: {e}")
    return errs


def check_ocr(port: int) -> list[str]:
    ident = _identity(port)
    if OCR_MODEL_SUBSTR not in ident.lower():
        return [f"identity {ident!r} lacks {OCR_MODEL_SUBSTR!r} — wrong server on port?"]
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port-gemma", type=int, default=8732)
    ap.add_argument("--port-ocr", type=int, default=8731)
    args = ap.parse_args(argv)

    ok = True
    for name, port, fn in (
        ("embgemma", args.port_gemma, check_gemma),
        ("teleocr", args.port_ocr, check_ocr),
    ):
        try:
            errs = fn(port)
        except Exception as e:  # noqa: BLE001 — connection refused lands here
            errs = [f"server down: {e}"]
        if errs:
            ok = False
            for e in errs:
                print(f"FAIL {name} :{port}: {e}")
        else:
            print(f"ok   {name} :{port}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
