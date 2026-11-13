"""
 Unit tests for Mojo/src/bench/window_shingles.mojo (pure logic; no bridge).

 Run via `make mojo-test` (auto-discovered).
"""

from std.testing import TestSuite, assert_true

from window_shingles import (
    window_ranges,
    window_jaccard,
    local_affinity,
    tokenize_lower,
)


def _lenof(lst: List[Tuple[Int, Int]]) -> Int:
    return lst.__len__()


def test_window_ranges_basic() raises:
    var r = window_ranges(7, 3)
    assert_true(_lenof(r) == 3, "7 tokens / width 3 → 3 windows")
    assert_true(r[0][0] == 0 and r[0][1] == 3, "first window")
    assert_true(r[1][0] == 3 and r[1][1] == 6, "second window")
    assert_true(r[2][0] == 6 and r[2][1] == 7, "tail window clamped")


def test_window_ranges_width_over_len() raises:
    var r = window_ranges(4, 10)
    assert_true(_lenof(r) == 1, "width > len → single window")
    assert_true(r[0][0] == 0 and r[0][1] == 4, "clamped to len")


def test_window_ranges_empty() raises:
    var r = window_ranges(0, 3)
    assert_true(_lenof(r) == 0, "empty → no windows")


def test_window_ranges_width_one() raises:
    var r = window_ranges(4, 1)
    assert_true(_lenof(r) == 4, "width 1 → n windows")


def test_window_jaccard_identical() raises:
    var a = List[String]()
    a.append("x")
    a.append("y")
    a.append("z")
    var v = window_jaccard(a, a, 0, 3, 0, 3)
    assert_true(v[0] == v[1], "identical windows → fraction 1")


def test_window_jaccard_disjoint() raises:
    var a = List[String]()
    a.append("x")
    a.append("y")
    var b = List[String]()
    b.append("p")
    b.append("q")
    var v = window_jaccard(a, b, 0, 2, 0, 2)
    assert_true(v[0] == 0, "disjoint → numerator 0")


def test_window_jaccard_partial() raises:
    var a = List[String]()
    a.append("x")
    a.append("y")
    a.append("z")
    var b = List[String]()
    b.append("y")
    b.append("z")
    b.append("w")
    var v = window_jaccard(a, b, 0, 3, 0, 3)
    # shared 2, union 4 → 2/4 = 1/2
    assert_true(v[0] * 2 == 1 * v[1], "2/4 == 1/2")


def test_local_affinity_local_overlap() raises:
    # Motivating case: global Jaccard low, local window overlap high.
    var a = List[String]()
    a.append("alpha")
    a.append("beta")
    a.append("gamma")
    a.append("delta")
    a.append("epsilon")
    a.append("zeta")
    var b = List[String]()
    b.append("omega")
    b.append("alpha")
    b.append("beta")
    b.append("gamma")
    b.append("omega")
    b.append("omega")
    var aff = local_affinity(a, b, 3)
    # window0: a[alpha,beta,gamma] ∩ b[omega,alpha,beta] → shared 2 union 4 = 1/2
    # window1: a[delta,epsilon,zeta] ∩ b[gamma,omega,omega] → shared 0
    # max 1/2; mean 1/4
    assert_true(aff[0][0] * 2 == 1 * aff[0][1], "max = 1/2")
    assert_true(aff[1][0] * 4 == 1 * aff[1][1], "mean = 1/4")


def test_local_affinity_empty() raises:
    var a = List[String]()
    var b = List[String]()
    b.append("x")
    var aff = local_affinity(a, b, 3)
    assert_true(aff[0][0] == 0 and aff[0][1] == 1, "empty → max 0/1")
    assert_true(aff[1][0] == 0 and aff[1][1] == 1, "empty → mean 0/1")


def main() raises:
    TestSuite.discover_tests[__functions_in_module()]().run()
