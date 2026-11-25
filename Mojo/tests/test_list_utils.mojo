"""
 Unit tests for Mojo/src/common/list_utils.mojo.

 Run via `make mojo-test` (auto-discovered; wildcard `Mojo/tests/*.mojo`).
"""

from std.testing import TestSuite, assert_true

from list_utils import sort_strs, merge_sort_strs


def _mk(values: List[String]) -> List[String]:
    var out = List[String]()
    for i in range(len(values)):
        out.append(values[i])
    return out^


def _empty() -> List[String]:
    return List[String]()


def _sorted_ok(actual: List[String], expected: List[String]) -> Bool:
    if len(actual) != len(expected):
        return False
    for i in range(len(expected)):
        if actual[i] != expected[i]:
            return False
    return True


def test_sort_strs_empty() raises:
    var lst = List[String]()
    var out = sort_strs(lst)
    assert_true(_sorted_ok(out, lst), "empty list sorts to empty")


def test_sort_strs_already_sorted() raises:
    var lst = _empty()
    lst.append("a")
    lst.append("b")
    lst.append("c")
    var out = sort_strs(lst)
    assert_true(_sorted_ok(out, lst), "sorted input unchanged")


def test_sort_strs_reversed() raises:
    var lst = _empty()
    lst.append("c")
    lst.append("b")
    lst.append("a")
    var out = sort_strs(lst)
    var exp = List[String]()
    exp.append("a")
    exp.append("b")
    exp.append("c")
    assert_true(_sorted_ok(out, exp), "reversed input sorted")


def test_sort_strs_duplicates() raises:
    var lst = _empty()
    lst.append("b")
    lst.append("a")
    lst.append("b")
    var out = sort_strs(lst)
    var exp = List[String]()
    exp.append("a")
    exp.append("b")
    exp.append("b")
    assert_true(_sorted_ok(out, exp), "duplicates preserved")


def test_sort_strs_lexicographic() raises:
    var lst = _empty()
    lst.append("Z")
    lst.append("a")
    lst.append("apple")
    lst.append("Apple")
    var out = sort_strs(lst)
    # Mojo String compares byte-wise: ASCII 'A'(65) < 'Z'(90) < 'a'(97)
    var exp = List[String]()
    exp.append("Apple")
    exp.append("Z")
    exp.append("a")
    exp.append("apple")
    assert_true(_sorted_ok(out, exp), "byte-wise lexicographic order")


def test_merge_sort_strs_empty() raises:
    var lst = List[String]()
    var out = merge_sort_strs(lst)
    assert_true(_sorted_ok(out, lst), "empty list merges to empty")


def test_merge_sort_strs_single() raises:
    var lst = _empty()
    lst.append("solo")
    var out = merge_sort_strs(lst)
    assert_true(_sorted_ok(out, lst), "single element unchanged")


def test_merge_sort_strs_matches_insertion() raises:
    var lst = _empty()
    lst.append("delta")
    lst.append("alpha")
    lst.append("charlie")
    lst.append("bravo")
    lst.append("echo")
    lst.append("alpha")
    var out = merge_sort_strs(lst)
    var exp = sort_strs(lst)
    assert_true(_sorted_ok(out, exp), "merge == insertion on mixed list")


def test_merge_sort_strs_pairs() raises:
    var lst = _empty()
    lst.append("slow")
    lst.append("fast")
    var out = merge_sort_strs(lst)
    var exp = List[String]()
    exp.append("fast")
    exp.append("slow")
    assert_true(_sorted_ok(out, exp), "two-element list sorted")


def main() raises:
    TestSuite.discover_tests[__functions_in_module()]().run()
