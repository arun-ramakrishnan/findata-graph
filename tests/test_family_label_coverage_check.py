"""Tests for helpers/maintenance/family_label_coverage_check.py."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance.family_label_coverage_check import (  # noqa: E402
    FAMILY_ROOT_TOKENS,
    coverage_report,
)

SEEDS = {
    "Tata": {"t1", "t2", "t3"},
    "Adani": {"a1", "a2"},
}


def L(label, members, companies):
    return {label: {"members": set(members), "companies": set(companies)}}


def test_token_bearing_multi_label_company_fires():
    labels = L("vigil:rpt:TCS", ["t1", "t2", "Tata Electronics"], ["t1", "t2", "Tata Electronics"])
    labels.update(
        L("vigil:rpt:TMPV", ["t1", "t3", "Tata Electronics"], ["t1", "t3", "Tata Electronics"])
    )
    r = coverage_report(labels, SEEDS, {"Tata"})
    assert len(r["violations"]) == 2
    for v in r["violations"]:
        assert v["missing"] == ["Tata Electronics"]
        assert v["family"] == "Tata"


def test_token_bearing_uses_family_root_tokens_only():
    assert "tata" in FAMILY_ROOT_TOKENS["Tata"]
    # a token not on the family root list must NOT fire
    labels = L("vigil:rpt:TCS", ["t1", "t2", "TVS Motors"], ["t1", "t2", "TVS Motors"])
    labels.update(L("vigil:rpt:TMPV", ["t1", "t3", "TVS Motors"], ["t1", "t3", "TVS Motors"]))
    r = coverage_report(labels, SEEDS, {"Tata"})
    assert r["violations"] == []


def test_non_token_multi_label_company_is_advisory():
    labels = L(
        "vigil:rpt:TCS",
        ["t1", "t2", "HyperVault AI Data Center"],
        ["t1", "t2", "HyperVault AI Data Center"],
    )
    labels.update(
        L(
            "vigil:rpt:TMPV",
            ["t1", "t3", "HyperVault AI Data Center"],
            ["t1", "t3", "HyperVault AI Data Center"],
        )
    )
    r = coverage_report(labels, SEEDS, {"Tata"})
    assert r["violations"] == []
    member_names = {a["member"] for a in r["advisory"]}
    assert "HyperVault AI Data Center" in member_names


def test_single_label_company_outside_seed_is_informational():
    labels = L("vigil:rpt:TCS", ["t1", "t2", "x"], ["t1", "t2", "x"])
    r = coverage_report(labels, SEEDS, {"Tata"})
    assert r["violations"] == []
    assert r["advisory"] == []
    rows = {x["label"]: x for x in r["labels"]}
    assert rows["vigil:rpt:TCS"]["outside_single"] == ["x"]
    assert rows["vigil:rpt:TCS"]["outside_multi"] == []


def test_noncompany_members_ignored():
    labels = L("vigil:rpt:TCS", ["t1", "t2", "Air India Sats"], ["t1", "t2"])
    r = coverage_report(labels, SEEDS, {"Tata"})
    assert r["violations"] == []
    rows = {x["label"]: x for x in r["labels"]}
    assert rows["vigil:rpt:TCS"]["coverage"] == "FULL"


def test_family_label_never_evaluated():
    labels = L("family:Tata", ["t1", "t2", "t3"], ["t1", "t2", "t3"])
    r = coverage_report(labels, SEEDS, {"Tata"})
    assert r["labels"] == []


def test_other_type_labels_never_evaluated():
    labels = L("$.group", ["t1", "t9"], ["t1", "t9"])
    r = coverage_report(labels, SEEDS, {"Tata"})
    assert r["labels"] == []


def test_no_overlap_filer_label_none():
    labels = L("vigil:rpt:OTHER", ["x1", "x2"], ["x1", "x2"])
    r = coverage_report(labels, SEEDS, {"Tata"})
    rows = {x["label"]: x for x in r["labels"]}
    assert rows["vigil:rpt:OTHER"]["coverage"] == "NONE"
    assert rows["vigil:rpt:OTHER"]["intersects"] is None


def test_noncomplete_family_token_multi_is_informational():
    labels = L("vigil:rpt:ADDANIGREEN", ["a1", "Tata Power"], ["a1", "Tata Power"])
    labels.update(L("vigil:rpt:ADANIPORTS", ["a2", "Tata Power"], ["a2", "Tata Power"]))
    r = coverage_report(labels, SEEDS, {"Tata"})
    assert r["violations"] == []  # Adani not complete
    rows = {x["label"]: x for x in r["labels"]}
    assert rows["vigil:rpt:ADDANIGREEN"]["outside_multi"] == ["Tata Power"]
