"""Tests for helpers/maintenance/group_discovery_growth_monitor.py."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance.group_discovery_growth_monitor import (  # noqa: E402
    _band,
    compute,
)

REF = [
    {"size": 60, "top_refs": [["vigil:rpt:AA", 60]], "family_hit_count": 5,
     "merge_flags": ["Tata"], "members_sample": ["a"] * 3},
    {"size": 5, "top_refs": [["vigil:rpt:BB", 5]], "family_hit_count": 0,
     "merge_flags": [], "members_sample": ["b"]},
]
CUR = [
    {"size": 60, "top_refs": [["vigil:rpt:AA", 60]], "family_hit_count": 40,
     "merge_flags": ["Tata", "Mahindra"], "members_sample": ["a"] * 3},
    {"size": 120, "top_refs": [["vigil:rpt:CC", 120]], "family_hit_count": 2,
     "merge_flags": [], "members_sample": ["c"] * 4},
    {"size": 3, "top_refs": [["vigil:rpt:BB", 3]], "family_hit_count": 0,
     "merge_flags": [], "members_sample": ["b"]},
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
