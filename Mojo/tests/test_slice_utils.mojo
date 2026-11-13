"""
 Unit tests for Mojo/src/common/slice_utils.mojo.

 Run via `make mojo-test` (auto-discovered; wildcard `Mojo/tests/*.mojo`).
"""

from std.testing import TestSuite, assert_true

from slice_utils import clamp_slice


def _clamp(len_: Int, lo: Int, hi: Int) -> Tuple[Int, Int]:
    return clamp_slice(len_, lo, hi)


def test_clamp_in_bounds() raises:
    var c = _clamp(5, 1, 3)
    assert_true(c[0] == 1 and c[1] == 3, "in-bounds unchanged")


def test_clamp_negative_lo() raises:
    var c = _clamp(5, -2, 3)
    assert_true(c[0] == 0 and c[1] == 3, "negative lo clamps to 0")


def test_clamp_hi_beyond_len() raises:
    var c = _clamp(5, 1, 9)
    assert_true(c[0] == 1 and c[1] == 5, "hi clamps to len")


def test_clamp_empty_range() raises:
    var c = _clamp(5, 4, 2)
    assert_true(c[0] == 4 and c[1] == 4, "lo>hi yields empty (lo==hi)")


def test_clamp_negative_hi() raises:
    var c = _clamp(5, 0, -1)
    assert_true(c[0] == 0 and c[1] == 0, "negative hi yields empty at 0")


def test_clamp_zero_len() raises:
    var c = _clamp(0, 0, 0)
    assert_true(c[0] == 0 and c[1] == 0, "len 0 → (0,0)")
    var c2 = _clamp(0, -5, 5)
    assert_true(c2[0] == 0 and c2[1] == 0, "len 0 clamps everything")


def test_usage_slicing_safe() raises:
    # The intended call pattern: clamp, then slice — never aborts.
    var arr = Array[Int, 5](uninitialized=True)
    for i in range(5):
        arr[i] = i
    var c = clamp_slice(5, -1, 10)
    var sp = arr[c[0] : c[1]]
    assert_true(sp.__len__() == 5, "clamped slice covers full array")
    var c2 = clamp_slice(5, 2, 2)
    var sp2 = arr[c2[0] : c2[1]]
    assert_true(sp2.__len__() == 0, "empty clamped slice")


def main() raises:
    TestSuite.discover_tests[__functions_in_module()]().run()
