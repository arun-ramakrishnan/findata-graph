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
