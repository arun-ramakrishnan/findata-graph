#!/usr/bin/env python3
"""One-shot health probe for the three llama-server legs
(llamacpp_unified_server_build S3 — `make llamacpp-health`; granite leg
added by granite_sidecar_selector S1).

:8732 embgemma — /v1/models identity must contain "gemma"; a live embed
                must return SERVER_DIMS (768; the 512 client-side
                truncation is the embedder's contract, not the server's).
:8731 teleocr  — /v1/models identity must contain the NaviDC GGUF name.
:8733 granite — /v1/models identity must contain "granite"; a live embed
                must return 384 dims (local_embedder.DIM).

Exit 0 only when the checked legs answer with the expected identity.
Usable from maint / cron / operator shell; read-only over loopback.

Default pair is embgemma + granite (the always-on legs); teleocr is
opt-in (--ocr) because it is down by default (OCR is on-demand —
see helpers/misc/llama_servers.sh, which never starts it unsolicited).

Usage: python3 vendor/llamacpp/health.py [--ocr] [--port-gemma 8732 --port-ocr 8731 --port-granite 8733]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

GEMMA_MODEL_SUBSTR = "gemma"  # same identity rule as gemma_embedder
OCR_MODEL_SUBSTR = "navidc-ocr"
GRANITE_MODEL_SUBSTR = "granite"  # same identity rule as local_embedder
GEMMA_SERVER_DIMS = 768
GRANITE_SERVER_DIMS = 384


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


def check_granite(port: int) -> list[str]:
    errs = []
    ident = _identity(port)
    if GRANITE_MODEL_SUBSTR not in ident.lower():
        errs.append(f"identity {ident!r} lacks {GRANITE_MODEL_SUBSTR!r} — wrong server on port?")
        return errs
    try:
        out = _post(f"http://127.0.0.1:{port}/v1/embeddings", {"input": "warm"})
        dims = len(out["data"][0]["embedding"])
        if dims != GRANITE_SERVER_DIMS:
            errs.append(
                f"embed returned {dims} dims, expected {GRANITE_SERVER_DIMS} — wrong model?"
            )
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
    ap.add_argument("--port-granite", type=int, default=8733)
    ap.add_argument(
        "--ocr",
        action="store_true",
        help="also check the teleocr leg (down by default; opt in only when OCR was started)",
    )
    args = ap.parse_args(argv)

    legs = [("embgemma", args.port_gemma, check_gemma)]
    if args.ocr:
        legs.append(("teleocr", args.port_ocr, check_ocr))
    else:
        print(f"skip  teleocr :{args.port_ocr} (down by default; re-run with --ocr)")
    legs.append(("granite", args.port_granite, check_granite))

    ok = True
    for name, port, fn in legs:
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
