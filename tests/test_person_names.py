from helpers.core.person_names import (
    classify_person_name,
    dedupe_report,
    person_name_key,
    resolve_person,
)


def test_classification_separates_person_huf_and_trust():
    assert classify_person_name("Manohar G Gandhi") == "person"
    assert classify_person_name("Manhar G GANDHI (SMALL )HUF") == "huf"
    assert classify_person_name("Gandhi Family Trust") == "trust"


def test_trust_token_requires_word_boundary():
    """S6/M4 (range review): the 2-letter 'FO' family-office token used to
    substring-match, so Foods of India, Fortis Healthcare and INFO EDGE all
    classified as trusts and resolve_person matched them against the wrong
    pool. Boundary matching keeps the abbreviation useful where it is real."""
    assert classify_person_name("Foods of India Ltd") == "person"
    assert classify_person_name("Fortis Healthcare") == "person"
    assert classify_person_name("INFO EDGE") == "person"
    assert classify_person_name("Gandhi Family Office") == "trust"
    assert classify_person_name("Gandhi FO") == "trust"
    assert classify_person_name("Gandhi Family Trust") == "trust"
    # the HUF tokens get the same boundary discipline
    assert classify_person_name("X HUF") == "huf"
    assert classify_person_name("Hindu Undivided Family Traders") == "huf"


def test_key_is_casefolded_and_token_sorted():
    assert person_name_key("Manoj B Gandhi") == person_name_key("GANDHI MANOJ B")
    assert person_name_key("Manoj B Gandhi") != person_name_key("Manoj Gandhi")


def test_resolver_never_crosses_huf_boundary():
    candidates = [
        ("Manohar G Gandhi", "person"),
        ("Manhar G Gandhi (Small) HUF", "huf"),
    ]
    assert resolve_person("Manhar G Gandhi", [("Manhar G Gandhi (Small) HUF", "huf")]) == (
        None,
        0.0,
    )
    assert resolve_person("Manhar G Gandhi (Small) HUF", candidates) == (
        "Manhar G Gandhi (Small) HUF",
        1.0,
    )


def test_dedupe_report_groups_only_same_class_and_key():
    assert dedupe_report(["Manoj B Gandhi", "Gandhi Manoj B", "Manhar G Gandhi (Small) HUF"]) == [
        ["Gandhi Manoj B", "Manoj B Gandhi"]
    ]
