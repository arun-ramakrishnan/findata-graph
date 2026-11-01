"""Tests for msgpackio (S1) — golden parity vs orjson (jsonio)."""

from __future__ import annotations

import unittest

import msgspec

from helpers.core import msgpackio, jsonio


class TestMsgpackioGoldenParity(unittest.TestCase):
    def test_dict_roundtrip(self):
        obj = {"a": 1, "b": [2.5, 3], "c": {"d": "hello"}}
        enc = msgpackio.encode(obj)
        dec = msgpackio.decode(enc)
        self.assertEqual(obj, dec)

    def test_list_roundtrip(self):
        obj = [{"x": 1}, {"x": 2}]
        enc = msgpackio.encode(obj)
        self.assertLess(len(enc), len(jsonio.dumps(obj).encode()))
        self.assertEqual(obj, msgpackio.decode(enc))

    def test_struct_roundtrip(self):
        import msgspec

        class Point(msgspec.Struct, frozen=True):
            x: float
            y: float

        pts = [Point(1.1, 2.2), Point(3.3, 4.4)]
        enc = msgpackio.encode_struct(pts)
        dec = msgpackio.decode_struct(enc, list[Point])
        self.assertEqual(pts, dec)
        self.assertIsInstance(dec[0], Point)


class TestMsgpackioNegativeInputs(unittest.TestCase):
    """Malformed/truncated/mistyped input — the deserializer sits at the
    Rust embed-cache boundary, so garbage in must raise a TYPED error,
    never corrupt memory or half-decode."""

    def test_empty_payload_is_decode_error(self):
        with self.assertRaises(msgspec.DecodeError):
            msgpackio.decode(b"")

    def test_truncated_payloads_are_decode_errors(self):
        # cut fixarray(2) and fixstr(5) headers mid-body
        for raw in (b"\x92\x01", b"\xa5ab", b"\xc7\x05"):
            with self.subTest(raw=raw):
                with self.assertRaises(msgspec.DecodeError):
                    msgpackio.decode(raw)

    def test_trailing_garbage_rejected(self):
        """msgspec msgpack is strict: bytes after a complete document are
        an error, not a silent prefix read (pin — a lenient decoder here
        would let a corrupted cache row alias its neighbor)."""
        with self.assertRaises(msgspec.DecodeError):
            msgpackio.decode(msgpackio.encode({"a": 1}) + b"\x00")

    def test_binary_key_decodes_as_bytes_key(self):
        """Raw msgpack bin-keys decode to BYTES keys (measured
        2026-11-01): asymmetric with encode (str/int keys only). Pinned
        so a cache that starts writing bin-keys is noticed, not absorbed."""
        raw = b"\x81\xc4\x02\xff\xfe\x01"  # fixmap 1: {bin8(2) ff fe: 1}
        self.assertEqual(msgpackio.decode(raw), {b"\xff\xfe": 1})

    def test_type_mismatch_is_validation_error(self):
        enc = msgpackio.encode({"a": 1})
        with self.assertRaises(msgspec.ValidationError):
            msgpackio.decode(enc, type_=int)
        self.assertEqual(msgpackio.decode(enc, type_=dict[str, int]), {"a": 1})

    def test_struct_target_mismatch_is_validation_error(self):
        import msgspec

        class Point(msgspec.Struct, frozen=True):
            x: int

        enc = msgpackio.encode_struct(Point(x=1))
        with self.assertRaises(msgspec.ValidationError):
            msgpackio.decode_struct(enc, int)
        with self.assertRaises(TypeError):
            msgpackio.decode_struct(enc, "not-a-type")
        self.assertEqual(msgpackio.decode_struct(enc, Point), Point(x=1))


class TestMsgpackioValueEdges(unittest.TestCase):
    """Value-domain edges of the transport: special floats, wide ints,
    key types, size limits — pinned so a msgspec bump can't silently
    change the wire format."""

    def test_special_floats_roundtrip(self):
        import math

        for v, check in (
            (float("nan"), math.isnan),
            (float("inf"), lambda x: x == float("inf")),
            (-float("inf"), lambda x: x == -float("inf")),
        ):
            with self.subTest(v=v):
                check(msgpackio.decode(msgpackio.encode(v)))

    def test_int_width_boundaries(self):
        for v in (-(2**63), 2**63, 2**64 - 1, -1, 0):
            with self.subTest(v=v):
                self.assertEqual(msgpackio.decode(msgpackio.encode(v)), v)

    def test_int_overflow_is_overflow_error_not_wraparound(self):
        """2**64 (one past msgpack's uint64) must raise — silent
        wraparound would corrupt an id or a byte offset at the boundary."""
        with self.assertRaises(OverflowError):
            msgpackio.encode(2**64)

    def test_none_and_bool_roundtrip(self):
        for v in (None, True, False):
            self.assertIs(msgpackio.decode(msgpackio.encode(v)), v)

    def test_int_keyed_dict_roundtrip(self):
        self.assertEqual(msgpackio.decode(msgpackio.encode({1: "a"})), {1: "a"})

    def test_deep_nesting_roundtrip(self):
        deep = x = {}
        for _ in range(200):
            x["n"] = {}
            x = x["n"]
        self.assertEqual(msgpackio.decode(msgpackio.encode(deep)), deep)

    def test_oversized_payloads_roundtrip(self):
        self.assertEqual(
            msgpackio.decode(msgpackio.encode(list(range(100_000)))), list(range(100_000))
        )
        self.assertEqual(msgpackio.decode(msgpackio.encode("x" * 1_000_000)), "x" * 1_000_000)

    def test_encode_is_deterministic(self):
        """Same object → same bytes (the Rust side hashes/shapes on the
        payload; a nondeterministic encoder would break cache keys)."""
        obj = {"a": [1, 2.5, "x"], "b": {"c": None}}
        self.assertEqual(msgpackio.encode(obj), msgpackio.encode(obj))


if __name__ == "__main__":
    unittest.main()
