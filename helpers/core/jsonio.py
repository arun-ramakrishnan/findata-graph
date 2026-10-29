#!/usr/bin/env python3
"""Repo-wide JSON engine shim over orjson (S0, db_scaling_tuning.md §6.3).

Mass-lane parse speedups measured 2026-10-29 on the live session-JSONL corpus
(81MB/pass, prime 4.6MB + zcode model-io 76MB): 1.55-1.6x stdlib on text
reads, 2.5-2.7x with -22% peak RSS on ``rb`` reads — the memory win exists
ONLY on the bytes path (orjson re-encodes str input to bytes before parsing,
so text mode is +10% RSS). orjson is ingestion-edge-only per the routing
principle (architecture.md §10): it strengthens the JSON->columnar conversion
of foreign harness-owned JSONL and consumer-demand stdout/exports; it must
not create or expand stored-JSON columns.

Public API:
    loads(text_or_bytes)   -> Any        # orjson.loads, unchanged
    dumps(obj, ...)        -> str        # str-expecting callers
    dumps_bytes(obj, ...)  -> bytes      # fast path for callers that accept bytes
    JSONDecodeError        -> orjson.JSONDecodeError

Catching decode errors:
    orjson.JSONDecodeError subclasses ValueError (like the stdlib class) but
    is a DISTINCT class — a converted site catching decode errors must catch
    ``jsonio.JSONDecodeError``, not ``json.JSONDecodeError``.

dumps guardrails (§6.3 deltas, per converted site):
    - Output is orjson's canonical compact UTF-8 form: byte-DIFFERENT from
      stdlib json.dumps (no ``", "`` separators, non-ASCII unescaped). Any
      writer that needs byte parity with a stdlib-era artifact stays stdlib.
    - ``sort_keys=True`` is honoured via orjson's OPT_SORT_KEYS — but writers
      that are diff-stability-critical were ruled to stay stdlib or pre-sort;
      check the §6.3 guardrail list before converting one.
    - ``indent=2`` maps to orjson's OPT_INDENT_2; any other indent value
      falls back to stdlib json (orjson has no other indent knob).
    - Bytes output only: ``json.dumps``' str side effects (ensure_ascii,
      float repr) are not reproduced; loads is the exact-API surface.
"""

from __future__ import annotations

import json
from typing import Any

import orjson

__all__ = ["loads", "dumps", "dumps_bytes", "JSONDecodeError"]

# Distinct class from stdlib json.JSONDecodeError (both ValueError subclasses).
# Re-exported so converted sites have one import point for the catch.
JSONDecodeError = orjson.JSONDecodeError

# API-identical for the repo's shapes: accepts str | bytes | bytearray, raises
# JSONDecodeError on malformed input. orjson additionally rejects NaN/Infinity
# by default (stdlib emits them) — log corpora don't carry them; if one ever
# surfaces the per-line try/except skip already handles the raise.
loads = orjson.loads


def dumps_bytes(
    obj: Any,
    *,
    default: Any = None,
    sort_keys: bool = False,
) -> bytes:
    """Serialize to compact UTF-8 bytes — the native orjson output."""
    opts = orjson.OPT_SORT_KEYS if sort_keys else 0
    kwargs: dict[str, Any] = {}
    if default is not None:
        kwargs["default"] = default
    return orjson.dumps(obj, option=opts, **kwargs)


def dumps(
    obj: Any,
    *,
    default: Any = None,
    sort_keys: bool = False,
    indent: int | None = None,
) -> str:
    """Serialize to a str via dumps_bytes().decode() for str-expecting callers.

    indent=2 uses orjson's OPT_INDENT_2; any other indent falls back to
    stdlib json (which also restores stdlib byte form for that call).
    """
    if indent is not None and indent != 2:
        kwargs = {"default": default} if default is not None else {}
        return json.dumps(obj, sort_keys=sort_keys, indent=indent, **kwargs)
    opts = 0
    if sort_keys:
        opts |= orjson.OPT_SORT_KEYS
    if indent == 2:
        opts |= orjson.OPT_INDENT_2
    kwargs = {"default": default} if default is not None else {}
    return orjson.dumps(obj, option=opts, **kwargs).decode("utf-8")
