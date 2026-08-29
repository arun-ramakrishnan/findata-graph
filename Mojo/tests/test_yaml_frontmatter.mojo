# Regression tests for the vendored mojo-yaml lexer patches
# (Mojo/vendor/mojo-yaml — see its PROVENANCE.md). Each test pins one
# real-YAML behavior the upstream 0.1.2 lexer got wrong on findata
# frontmatter. Run via `make mojo-test`.

from std.testing import TestSuite, assert_true, assert_equal
from yaml import parse, YamlValue


def _s(v: YamlValue) raises -> String:
    return v.as_string()


def test_bare_multiword_value() raises:
    # Patch 2: plain scalar runs to end-of-line in value position.
    var d = parse("title: Avanti Feeds")
    assert_equal(_s(d.get("title")), "Avanti Feeds", "bare multi-word value")


def test_colon_space_breaks_plain_scalar() raises:
    # Patch 3: ": " cannot occur inside a plain scalar — this is how
    # "- key: value" sequence items stay mappings.
    var d = parse("items:\n- id: one\n- id: two")
    var seq = d.get("items").as_sequence()
    assert_equal(len(seq), 2, "sequence of mappings length")
    assert_equal(_s(seq[1].get("id")), "two", "dash-item key survives")


def test_date_scalar_is_string() raises:
    # Patch 4: date-like value is one STRING, not number-dash-number soup.
    var d = parse("verified: 2026-08-14")
    assert_equal(_s(d.get("verified")), "2026-08-14", "bare date value")


def test_digit_leading_words_stay_strings() raises:
    # Patch 5: whole-scalar resolver — 360 ONE WAM / 005380.KS / 3M India.
    var d = parse("a: 360 ONE WAM\nb: 005380.KS\nc: 3M India")
    assert_equal(_s(d.get("a")), "360 ONE WAM", "number-word first")
    assert_equal(_s(d.get("b")), "005380.KS", "ticker")
    assert_equal(_s(d.get("c")), "3M India", "digit-letter brand")


def test_typed_numbers_survive() raises:
    # Patch 5 flip side: purely numeric scalars keep INTEGER/FLOAT typing.
    var d = parse("port: 8080\nratio: 3.25")
    assert_equal(d.get("port").as_int(), 8080, "integer stays typed")
    assert_true(abs(d.get("ratio").as_float() - 3.25) < 1e-9, "float typed")


def test_single_quote_escape() raises:
    # Patch 6: '' inside single quotes = one literal quote (PyYAML parity).
    var d = parse("title: \'\'\'The Chatter: Inflection Watch\'\'\'")
    assert_equal(_s(d.get("title")), "\u0027The Chatter: Inflection Watch\u0027",
                 "quoted-escape parity with PyYAML")


def test_nested_mapping_and_block_list() raises:
    # Fixture-shape sanity: nested maps + block sequences (upstream OK).
    var d = parse(
        "server:\n  host: localhost\n  port: 8080\ntags:\n- a/b\n- c/d"
    )
    assert_equal(_s(d.get("server").get("host")), "localhost", "nested map")
    assert_equal(d.get("server").get("port").as_int(), 8080, "nested int")
    assert_equal(len(d.get("tags").as_sequence()), 2, "block list")


def test_keyword_scalars_unchanged() raises:
    # true/false keywords must still resolve (not plain strings).
    var d = parse("debug: true\nneg: no")
    assert_true(d.get("debug").as_bool(), "true keyword")
    assert_true(not d.get("neg").as_bool(), "no keyword is false")


def main() raises:
    TestSuite.discover_tests[__functions_in_module()]().run()
