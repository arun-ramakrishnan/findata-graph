"""Tests for helpers/maintenance/group_discovery_inventory.py.

Covers the component extraction, profile fields, and merge-flag logic on a
tiny synthetic company-company same_group graph.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from helpers.maintenance.group_discovery_inventory import (  # noqa: E402
    _component_profile,
    _components,
)


@pytest.fixture()
def dyads():
    # Alpha-Beta-Gamma form one component; Delta-Epsilon another; Zeta alone.
    return [
        ("Alpha", "Beta", "vigil:rpt:XX"),
        ("Beta", "Gamma", "vigil:rpt:XX"),
        ("Delta", "Epsilon", "vigil:rpt:YY"),
    ]


def test_components_split_by_connectivity(dyads):
    comps = _components(dyads)
    sizes = sorted(len(c) for c in comps)
    # only nodes incident to at least one dyad appear (isolates are not
    # represented — they have no group evidence)
    assert sizes == [2, 3]


def test_profile_fields_and_merge_flags(dyads):
    comp = ["Alpha", "Beta", "Gamma"]
    prof = _component_profile(
        comp,
        dyads,
        sectors={"Alpha": "Banking", "Beta": "Banking"},
        families={"Alpha": {"Tata"}, "Gamma": {"Adani"}},
    )
    assert prof["size"] == 3
    assert prof["internal_edges"] == 2
    assert prof["density"] == pytest.approx(0.6667, abs=1e-4)
    assert prof["top_refs"][0] == ("vigil:rpt:XX", 2)
    assert prof["sectors"][0] == ("Banking", 2)
    assert prof["family_hit_count"] == 2
    assert prof["merge_flags"] == ["Adani", "Tata"]


def test_profile_no_family_hits_no_flags(dyads):
    prof = _component_profile(["Delta", "Epsilon"], dyads, sectors={}, families={})
    assert prof["family_hit_count"] == 0
    assert prof["merge_flags"] == []
