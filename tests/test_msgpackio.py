"""Tests for msgpackio (S1) — golden parity vs orjson (jsonio)."""

from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
