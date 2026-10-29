#!/usr/bin/env python3
"""Tests for helpers/core/jsonio.py — orjson shim (db_scaling_tuning §6.3 S0)."""

from __future__ import annotations

import json

import pytest

from helpers.core import jsonio


class TestLoads:
    def test_str_and_bytes_parity(self):
        payload = '{"a": 1, "b": [true, null, "x"], "c": {"d": 2.5}}'
        assert jsonio.loads(payload) == jsonio.loads(payload.encode("utf-8"))

    def test_matches_stdlib_object_graph(self):
        payload = '{"k": [1, "s", {"n": null, "f": 0.25}], "u": "héllo"}'
        assert jsonio.loads(payload) == json.loads(payload)

    def test_malformed_raises_jsondecodeerror_subclass_of_valueerror(self):
        with pytest.raises(jsonio.JSONDecodeError):
            jsonio.loads("{not json")
        with pytest.raises(ValueError):
            jsonio.loads(b"\xff\xfe not utf8-clean json either")

    def test_rejects_nan_stdlib_emits(self):
        # Documented drift: orjson refuses NaN/Infinity where stdlib emits them.
        assert json.loads("NaN") != json.loads("NaN")  # NaN != itself; stdlib parses it
        with pytest.raises(jsonio.JSONDecodeError):
            jsonio.loads("NaN")


class TestDumpsBytes:
    def test_returns_bytes_utf8_unescaped(self):
        out = jsonio.dumps_bytes({"k": "héllo"})
        assert isinstance(out, bytes)
        assert out == b'{"k":"h\xc3\xa9llo"}'  # compact + non-ASCII unescaped

    def test_sort_keys(self):
        assert jsonio.dumps_bytes({"b": 1, "a": 2}, sort_keys=True) == b'{"a":2,"b":1}'

    def test_default_hook(self):
        assert jsonio.dumps_bytes({1, 2}, default=list) is not None
        class _X:
            def __init__(self):
                self.v = 7
        out = jsonio.loads(jsonio.dumps_bytes({"x": _X()}, default=lambda o: o.v))
        assert out == {"x": 7}


class TestDumps:
    def test_returns_str_compact(self):
        out = jsonio.dumps({"a": 1, "b": [1, 2]})
        assert isinstance(out, str)
        assert out == '{"a":1,"b":[1,2]}'

    def test_indent_two_uses_orjson(self):
        out = jsonio.dumps({"a": 1}, indent=2)
        assert out == '{\n  "a": 1\n}'

    def test_other_indent_falls_back_to_stdlib(self):
        out = jsonio.dumps({"a": [1]}, indent=4)
        assert out == json.dumps({"a": [1]}, indent=4)

    def test_roundtrip_str(self):
        obj = {"x": [1, "two", {"three": 3.0}], "u": "héllo"}
        assert jsonio.loads(jsonio.dumps(obj)) == obj
