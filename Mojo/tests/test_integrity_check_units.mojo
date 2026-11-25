"""
 Unit tests for Mojo/src/common/integrity_check.mojo — non-parity
 predicate units (the parity surface itself is gated by `make
 mojo-parity`; these pin the pure string/filesystem helpers).

 Run via `make mojo-test` (auto-discovered).
"""

from std.testing import TestSuite, assert_true
from std.python import Python

from integrity_check import (
    uniq_sorted,
    _ticker_dup_suppressed,
    subset,
    norm_underscore,
    dir_structure_ok,
    filename_ok,
    name_ok,
    note_has_tag,
    csv_contains,
    is_stopword,
    is_generic,
    fuzzy_match,
    meaningful_tokens,
)


def _mk(values: List[String]) -> List[String]:
    var out = List[String]()
    for i in range(len(values)):
        out.append(values[i])
    return out^


def _eq(actual: List[String], expected: List[String]) -> Bool:
    if len(actual) != len(expected):
        return False
    for i in range(len(expected)):
        if actual[i] != expected[i]:
            return False
    return True


# ---------------------------------------------------------------- uniq_sorted


def test_uniq_sorted_collapses() raises:
    var lst = _mk(List[String]())
    lst.append("b")
    lst.append("a")
    lst.append("b")
    lst.append("a")
    var out = uniq_sorted(lst)
    var exp = List[String]()
    exp.append("a")
    exp.append("b")
    assert_true(_eq(out, exp), "dupes collapse, sorted")


def test_uniq_sorted_single() raises:
    var lst = _mk(List[String]())
    lst.append("only")
    var out = uniq_sorted(lst)
    var exp = List[String]()
    exp.append("only")
    assert_true(_eq(out, exp), "single unchanged")


def test_uniq_sorted_empty() raises:
    var lst = List[String]()
    var out = uniq_sorted(lst)
    assert_true(len(out) == 0, "empty stays empty")


# ------------------------------------------------------- _ticker_dup_suppressed


def test_ticker_dup_suppressed_alphabet_google() raises:
    var a = _mk(List[String]())
    a.append("Alphabet")
    a.append("Google")
    var b = _mk(List[String]())
    b.append("Google")
    b.append("Alphabet")
    assert_true(_ticker_dup_suppressed(a), "Alphabet/Google suppressed")
    assert_true(_ticker_dup_suppressed(b), "order-insensitive")


def test_ticker_dup_suppressed_forbes_variant() raises:
    var a = _mk(List[String]())
    a.append("Forbes & Company")
    a.append("Forbes and Company")
    assert_true(_ticker_dup_suppressed(a), "& variant suppressed")


def test_ticker_dup_suppressed_rejects_other_pairs() raises:
    var a = _mk(List[String]())
    a.append("Alphabet")
    a.append("Microsoft")
    assert_true(not _ticker_dup_suppressed(a), "unrelated pair not suppressed")


def test_ticker_dup_suppressed_requires_two() raises:
    var a = _mk(List[String]())
    a.append("Alphabet")
    assert_true(not _ticker_dup_suppressed(a), "len != 2 → False")


# ------------------------------------------------------------------- subset


def test_subset_true() raises:
    var a = _mk(List[String]())
    a.append("a")
    a.append("b")
    var b = _mk(List[String]())
    b.append("z")
    b.append("b")
    b.append("a")
    assert_true(subset(a, b), "a ⊆ b")


def test_subset_false() raises:
    var a = _mk(List[String]())
    a.append("c")
    var b = _mk(List[String]())
    b.append("a")
    b.append("b")
    assert_true(not subset(a, b), "c ∉ {a,b}")


# ------------------------------------------------------------ norm_underscore


def test_norm_underscore_spaces() raises:
    var out = norm_underscore(String("a b  c"))
    assert_true(out == "a_b__c", "spaces → underscores")


def test_norm_underscore_no_spaces() raises:
    var out = norm_underscore(String("abc_123"))
    assert_true(out == "abc_123", "unchanged when no spaces")


def test_norm_underscore_empty() raises:
    var out = norm_underscore(String(""))
    assert_true(out == "", "empty stays empty")


# ----------------------------------------------------------- dir_structure_ok


def test_dir_structure_ok_company_depth4() raises:
    assert_true(
        dir_structure_ok(String("findata/Companies/X/Y.md"), String("company")),
        "Company depth 4 ok",
    )


def test_dir_structure_ok_company_depth3_bad() raises:
    assert_true(
        not dir_structure_ok(
            String("findata/Companies/X.md"), String("company")
        ),
        "Company depth 3 bad",
    )


def test_dir_structure_ok_sectors_ge3() raises:
    assert_true(
        dir_structure_ok(String("findata/Sectors/X/Y/Z.md"), String("sector")),
        "Sectors depth ≥3 ok",
    )
    assert_true(
        dir_structure_ok(String("findata/Sectors/X.md"), String("sector")),
        "Sectors depth 3 ok",
    )


def test_dir_structure_ok_super_sectors_depth3() raises:
    assert_true(
        dir_structure_ok(
            String("findata/Super_Sectors/X.md"), String("super_sector")
        ),
        "Super_Sectors depth 3 ok",
    )
    assert_true(
        not dir_structure_ok(
            String("findata/Super_Sectors/X/Y.md"), String("super_sector")
        ),
        "Super_Sectors depth 4 bad",
    )


def test_dir_structure_ok_edition_chatter() raises:
    assert_true(
        dir_structure_ok(String("findata/The_Chatter/X.md"), String("edition")),
        "edition Chatter depth 3 ok",
    )
    assert_true(
        not dir_structure_ok(
            String("findata/The_Chatter/X/Y.md"), String("edition")
        ),
        "edition Chatter depth 4 bad",
    )


def test_dir_structure_ok_subsector_institution_always() raises:
    assert_true(
        dir_structure_ok(String("whatever.md"), String("sub_sector")),
        "sub_sector always ok",
    )
    assert_true(
        dir_structure_ok(String("whatever.md"), String("institution")),
        "institution always ok",
    )


def test_dir_structure_ok_unknown_path_bad() raises:
    assert_true(
        not dir_structure_ok(String("elsewhere/X.md"), String("company")),
        "unknown root bad",
    )


# --------------------------------------------------------------- filename_ok


def test_filename_ok_good() raises:
    assert_true(
        filename_ok(String("findata/Companies/X/Name_123.md")),
        "alnum+underscore stem ok",
    )


def test_filename_ok_bad_start() raises:
    assert_true(
        not filename_ok(String("findata/Companies/X/_Name.md")),
        "leading underscore bad",
    )


def test_filename_ok_bad_char() raises:
    assert_true(
        not filename_ok(String("findata/Companies/X/Name-123.md")), "hyphen bad"
    )


def test_filename_ok_no_md() raises:
    assert_true(
        not filename_ok(String("findata/Companies/X/Name")), "missing .md bad"
    )


def test_filename_ok_dot_in_stem() raises:
    assert_true(
        not filename_ok(String("findata/Companies/X/Name.MD")),
        "upper extension bad",
    )


# ------------------------------------------------------------------- name_ok


def test_name_ok_good() raises:
    assert_true(name_ok(String("Alpha_Ltd")), "alnum+single underscore ok")


def test_name_ok_double_underscore() raises:
    assert_true(not name_ok(String("A__B")), "double underscore bad")


def test_name_ok_trailing_underscore() raises:
    assert_true(not name_ok(String("A_")), "trailing underscore bad")


def test_name_ok_leading_non_alnum() raises:
    assert_true(not name_ok(String("_A")), "leading underscore bad")


def test_name_ok_punct() raises:
    assert_true(not name_ok(String("A.B")), "punct bad")


def test_name_ok_empty() raises:
    assert_true(not name_ok(String("")), "empty bad")


# ------------------------------------------------------------- note_has_tag


def test_note_has_tag_present() raises:
    var fm = String("tags:\n  - sbi\n  - bank\n")
    assert_true(note_has_tag(fm, String("sbi")), "tag present")


def test_note_has_tag_absent() raises:
    var fm = String("tags:\n  - sbi\n")
    assert_true(not note_has_tag(fm, String("gold")), "tag absent")


# -------------------------------------------------------------- csv_contains


def test_csv_contains_middle() raises:
    assert_true(csv_contains(String("a,b,c"), String("b")), "middle token")


def test_csv_contains_exact_only() raises:
    assert_true(not csv_contains(String("a,b,c"), String("ab")), "no substring")


def test_csv_contains_no_match() raises:
    assert_true(not csv_contains(String("a,b"), String("z")), "no match")


# ------------------------------------------------------------- stopword/generic


def test_is_stopword() raises:
    assert_true(is_stopword(String("the")), "the is stopword")
    assert_true(is_stopword(String("ltd")), "ltd is stopword")
    assert_true(not is_stopword(String("maersk")), "maersk not stopword")


def test_is_generic() raises:
    assert_true(is_generic(String("bank")), "bank generic")
    assert_true(is_generic(String("technologies")), "technologies generic")
    assert_true(not is_generic(String("infosys")), "infosys not generic")


# -------------------------------------------------------------- meaningful_tokens


def test_meaningful_tokens_dedupes() raises:
    var lower = Python.evaluate("(lambda s: s.lower())")
    var out = meaningful_tokens(String("Mahindra & Mahindra"), lower)
    # stopwords filtered; repeated 'mahindra' collapses; '&' is non-alnum
    var exp = List[String]()
    exp.append("mahindra")
    assert_true(_eq(out, exp), "dedupe + stopword filter")


def test_meaningful_tokens_empty() raises:
    var lower = Python.evaluate("(lambda s: s.lower())")
    var out = meaningful_tokens(String("the and of"), lower)
    assert_true(len(out) == 0, "all stopwords → empty")


# ---------------------------------------------------------------- fuzzy_match


def test_fuzzy_match_single_token_equal() raises:
    var a = _mk(List[String]())
    a.append("abc")
    var b = _mk(List[String]())
    b.append("abc")
    assert_true(fuzzy_match(a, b), "single shared token matches")


def test_fuzzy_match_single_token_diff() raises:
    var a = _mk(List[String]())
    a.append("abc")
    var b = _mk(List[String]())
    b.append("xyz")
    assert_true(not fuzzy_match(a, b), "no shared token")


def test_fuzzy_match_multi_token_contained() raises:
    var a = _mk(List[String]())
    a.append("johnson")
    a.append("johnson")
    a.append("holdings")
    var b = _mk(List[String]())
    b.append("johnson")
    b.append("holdings")
    # dedupe collapsed; shared = {johnson, holdings}; 'holdings' generic
    # but 'johnson' non-generic → match
    assert_true(fuzzy_match(a, b), "containment with non-generic shared")


def test_fuzzy_match_generic_only() raises:
    var a = _mk(List[String]())
    a.append("bank")
    a.append("insurance")
    var b = _mk(List[String]())
    b.append("bank")
    b.append("insurance")
    assert_true(not fuzzy_match(a, b), "only generic shared → no match")


def main() raises:
    TestSuite.discover_tests[__functions_in_module()]().run()
