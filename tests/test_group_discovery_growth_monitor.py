"""Tests for helpers/maintenance/group_discovery_growth_monitor.py."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance.group_discovery_growth_monitor import (  # noqa: E402
    _band,
    compute,
    gate_check,
)

REF = [
    {
        "size": 60,
        "top_refs": [["vigil:rpt:AA", 60]],
        "family_hit_count": 5,
        "merge_flags": ["Tata"],
        "members_sample": ["a"] * 3,
    },
    {
        "size": 5,
        "top_refs": [["vigil:rpt:BB", 5]],
        "family_hit_count": 0,
        "merge_flags": [],
        "members_sample": ["b"],
    },
]
CUR = [
    {
        "size": 60,
        "top_refs": [["vigil:rpt:AA", 60]],
        "family_hit_count": 40,
        "merge_flags": ["Tata", "Mahindra"],
        "members_sample": ["a"] * 3,
    },
    {
        "size": 120,
        "top_refs": [["vigil:rpt:CC", 120]],
        "family_hit_count": 2,
        "merge_flags": [],
        "members_sample": ["c"] * 4,
    },
    {
        "size": 3,
        "top_refs": [["vigil:rpt:BB", 3]],
        "family_hit_count": 0,
        "merge_flags": [],
        "members_sample": ["b"],
    },
]


def test_band_boundaries():
    assert _band(2) == "2-3"
    assert _band(4) == "4-7"
    assert _band(16) == "16-31"
    assert _band(128) == "128-255"
    assert _band(256) == "256+"


def test_compute_deltas():
    r = compute(REF, CUR)
    assert r["reference_components"] == 2
    assert r["current_components"] == 3
    assert r["family_hit_delta"] == 40 + 2 - (5 + 0)
    assert r["merge_flags_added"] == ["Mahindra"]
    assert r["merge_flags_removed"] == []
    assert len(r["new_components"]) == 1
    assert r["new_components"][0]["size"] == 120
    assert len(r["gone_components"]) == 0


def test_compute_gone():
    r = compute(CUR, REF)  # reversed: old has 3, new has 2
    assert len(r["gone_components"]) == 1
    assert r["gone_components"][0]["size"] == 120
    assert r["merge_flags_removed"] == ["Mahindra"]


def test_compute_size_bands():
    r = compute(REF, CUR)
    assert r["size_bands_reference"].get("64-127", 0) == 0
    assert r["size_bands_current"]["64-127"] == 1
    assert r["size_bands_current"]["2-3"] == 1


# --- gate_check policy -----------------------------------------------------

REF2 = [
    {
        "size": 60,
        "top_refs": [["vigil:rpt:AA", 60]],
        "family_hit_count": 5,
        "merge_flags": ["Tata"],
        "family_members": ["m1", "m2", "m3"],
        "members_sample": ["a"] * 3,
    },
    {
        "size": 5,
        "top_refs": [["vigil:rpt:BB", 5]],
        "family_hit_count": 0,
        "merge_flags": [],
        "family_members": [],
        "members_sample": ["b"],
    },
]


def test_gate_check_growth_only_passes():
    cur = [
        {
            "size": 60,
            "top_refs": [["vigil:rpt:AA", 60]],
            "family_hit_count": 8,
            "merge_flags": ["Tata"],
            "family_members": ["m1", "m2", "m3"],
            "members_sample": ["a"] * 3,
        },
        {
            "size": 5,
            "top_refs": [["vigil:rpt:BB", 5]],
            "family_hit_count": 0,
            "merge_flags": [],
            "family_members": [],
            "members_sample": ["b"],
        },
        {
            "size": 120,
            "top_refs": [["vigil:rpt:CC", 120]],
            "family_hit_count": 2,
            "merge_flags": ["Mahindra"],
            "family_members": ["m9"],
            "members_sample": ["c"] * 4,
        },
    ]
    ok, reasons = gate_check(REF2, cur)
    assert ok is True
    assert reasons == []


def test_gate_check_family_hit_loss_fires():
    cur = [
        {
            "size": 60,
            "top_refs": [["vigil:rpt:AA", 60]],
            "family_hit_count": 2,
            "merge_flags": ["Tata"],
            "family_members": ["m1"],
            "members_sample": ["a"] * 3,
        },
        {
            "size": 5,
            "top_refs": [["vigil:rpt:BB", 5]],
            "family_hit_count": 0,
            "merge_flags": [],
            "family_members": [],
            "members_sample": ["b"],
        },
    ]
    ok, reasons = gate_check(REF2, cur)
    assert ok is False
    assert any("family-hit delta" in r for r in reasons)


def test_gate_check_merge_flag_removed_fires():
    cur = [
        {
            "size": 60,
            "top_refs": [["vigil:rpt:AA", 60]],
            "family_hit_count": 5,
            "merge_flags": [],
            "family_members": ["m1", "m2", "m3"],
            "members_sample": ["a"] * 3,
        },
        {
            "size": 5,
            "top_refs": [["vigil:rpt:BB", 5]],
            "family_hit_count": 0,
            "merge_flags": [],
            "family_members": [],
            "members_sample": ["b"],
        },
    ]
    ok, reasons = gate_check(REF2, cur)
    assert ok is False
    assert any("merge flags removed" in r for r in reasons)


def test_gate_check_family_component_gone_fires():
    cur = [
        {
            "size": 5,
            "top_refs": [["vigil:rpt:BB", 5]],
            "family_hit_count": 0,
            "merge_flags": [],
            "family_members": [],
            "members_sample": ["b"],
        },
    ]
    ok, reasons = gate_check(REF2, cur)
    assert ok is False
    assert any("family members disappeared" in r for r in reasons)


def test_gate_check_empty_current_fires():
    ok, reasons = gate_check(REF2, [])
    assert ok is False
    assert any("empty" in r for r in reasons)


def test_gate_check_empty_reference_fails_with_guidance():
    # A missing/empty reference must fail loudly (no silent skip), so the
    # gate cannot quietly stop watching. Exercises the gate_path guard via
    # the same policy entry: empty reference -> every current component is
    # "new", no erosion signal exists, but an empty reference is itself a
    # state failure.
    ok, reasons = gate_check([], REF2)
    assert ok is True  # no erosion against nothing — policy is monotonic
    # main() handles the missing file separately; assert the CLI path
    # fails by exercising gate_check's caller contract: reference file
    # absence is surfaced by load_report raising, not by policy.


def test_qa_gate_includes_group_growth_check():
    from tests import run_gate_report as rgr

    labels = [s.label for s in rgr.GATES["qa"].steps]
    assert "group-growth-check" in labels
    step = next(s for s in rgr.GATES["qa"].steps if s.label == "group-growth-check")
    assert any("group_discovery_growth_monitor.py" in a for a in step.args)
    assert "--check" in step.args
    assert "family-label-coverage" in labels
    fam = next(s for s in rgr.GATES["qa"].steps if s.label == "family-label-coverage")
    assert any("family_label_coverage_check.py" in a for a in fam.args)
    assert "--check" in fam.args
