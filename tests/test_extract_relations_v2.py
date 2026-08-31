#!/usr/bin/env python3
"""E1 (2026-08-23) — prose pattern family v2 + diff-audit harness.

Covers:
  1. G2 stake-percentage patterns (acquisition of N% / acquired N% /
     holds N% -> acquired | subsidiary_of with properties.stake_pct,
     <50% holdings dropped).
  2. step-down subsidiary qualifier.
  3. Group-name patterns v2 (promoter group, Group company, flagship)
     feeding same_group clustering.
  4. Resolver ambiguity audit log (Tier-C logging only).
  5. Diff-audit harness helpers (helpers/misc/relation_diff_audit.py).
  6. Snapshot parity: legacy v1 families still fire alongside v2 on a
     combined fixture document.
"""

from __future__ import annotations

import json

import pytest

from helpers.graph.extract_relations import (  # noqa: E402
    EntityResolver,
    _normalize_group_name,
    extract_relations,
)
from helpers.misc.relation_diff_audit import (  # noqa: E402
    diff_counts,
    format_table,
    load_counts,
)


def _run(resolver, content):
    return extract_relations(
        content,
        edition_title="E1 Test Edition",
        newsletter_type="The_Chatter",
        resolver=resolver,
    )


@pytest.fixture
def resolver():
    return EntityResolver(
        [
            "Hindalco Industries",
            "UltraTech Cement",
            "Grasim Industries",
            "Aditya Birla Fashion",
            "Titan",
            "Damas",
            "Tata Elxsi",
            "Triveni Engineering",
            "Tech Mahindra",
            "Mahindra CIE Automotive",
            "Tata Motors",
            "Tata Elxsi International",
            "Microsoft",
            "Great Eastern Shipping",
            "Great Eastern Energy",
            "Vedanta",
        ]
    )


# --------------------------------------------------------------------------- #
# 1. G2 stake-percentage family                                               #
# --------------------------------------------------------------------------- #
class TestStakePatterns:
    def test_acquisition_of_pct_stake(self, resolver):
        content = (
            "## Hindalco Industries | Large Cap | Metals\n\n"
            "Completion of the acquisition of 26% stake in Titan "
            "is on track.\n"
        )
        by_type, unresolved = _run(resolver, content)
        assert unresolved == []
        edges = by_type["acquired"]
        assert len(edges) == 1
        edge = edges[0]
        assert (edge.source, edge.target) == ("Hindalco Industries", "Titan")
        assert edge.properties["stake_pct"] == 26.0

    def test_acquired_pct_stake_forward(self, resolver):
        content = (
            "## Hindalco Industries | Large Cap | Metals\n\n"
            "The company picked up a 51% equity stake in Tata Elxsi "
            "last quarter.\n"
        )
        by_type, _ = _run(resolver, content)
        edge = by_type["acquired"][0]
        assert (edge.source, edge.target) == ("Hindalco Industries", "Tata Elxsi")
        assert edge.properties["stake_pct"] == 51.0

    def test_holds_majority_stake_is_reverse_subsidiary_of(self, resolver):
        # "holds 74% shareholding in X" => X is the subsidiary (source),
        # the section's company is the parent (target).
        content = (
            "## Hindalco Industries | Large Cap | Metals\n\n"
            "It holds 74% shareholding in Triveni Engineering via a unit.\n"
        )
        by_type, unresolved = _run(resolver, content)
        assert unresolved == []
        edge = by_type["subsidiary_of"][0]
        assert (edge.source, edge.target) == ("Triveni Engineering", "Hindalco Industries")
        assert edge.properties["stake_pct"] == 74.0
        assert edge.symmetric is False

    def test_holds_minority_stake_dropped_silently(self, resolver):
        # <50% is a passive holding: Tier-C drop, no edge AND no sidecar.
        content = (
            "## Hindalco Industries | Large Cap | Metals\n\n"
            "It owns 20% stake in Aditya Birla Fashion.\n"
        )
        by_type, unresolved = _run(resolver, content)
        assert by_type == {}
        assert unresolved == []

    def test_bare_acquisition_of_still_works(self, resolver):
        # v1 "acquisition of X" must not be broken by the pct variants.
        content = (
            "## Hindalco Industries | Large Cap | Metals\n\n"
            "The acquisition of Damas in May was completed ahead of plan.\n"
        )
        by_type, _ = _run(resolver, content)
        edge = by_type["acquired"][0]
        assert {edge.source, edge.target} == {"Hindalco Industries", "Damas"}
        assert "stake_pct" not in edge.properties


# --------------------------------------------------------------------------- #
# 2. step-down subsidiary                                                     #
# --------------------------------------------------------------------------- #
class TestStepDownSubsidiary:
    def test_step_down_subsidiary_of(self, resolver):
        content = (
            "## UltraTech Cement | Large Cap | Cement\n\n"
            "Its key unit operates as a wholly owned step-down subsidiary "
            "of Grasim Industries.\n"
        )
        by_type, unresolved = _run(resolver, content)
        assert unresolved == []
        edge = by_type["subsidiary_of"][0]
        assert (edge.source, edge.target) == ("UltraTech Cement", "Grasim Industries")

    def test_bare_step_down_form(self, resolver):
        content = (
            "## UltraTech Cement | Large Cap | Cement\n\n"
            "The business now runs as a step-down subsidiary of "
            "Grasim Industries.\n"
        )
        by_type, _ = _run(resolver, content)
        edge = by_type["subsidiary_of"][0]
        assert (edge.source, edge.target) == ("UltraTech Cement", "Grasim Industries")


# --------------------------------------------------------------------------- #
# 3. Group patterns v2 -> same_group clustering                               #
# --------------------------------------------------------------------------- #
class TestGroupPatternsV2:
    def test_promoter_group_clusters_pair(self, resolver):
        content = (
            "# Edition\n\n"
            "## Tata Motors | Large Cap | Auto\n\n"
            "The company is part of the Tata promoter group.\n\n"
            "## Tata Elxsi | Mid Cap | IT\n\n"
            "Also part of the Tata promoter group per the annual report.\n"
        )
        by_type, _ = _run(resolver, content)
        pairs = {(e.source, e.target) for e in by_type["same_group"]}
        assert ("Tata Elxsi", "Tata Motors") in pairs
        assert all(e.properties["group"] == "Tata" for e in by_type["same_group"])

    def test_flagship_of_group_and_group_company(self, resolver):
        content = (
            "# Edition\n\n"
            "## Tech Mahindra | Large Cap | IT\n\n"
            "It remains the flagship company of the Mahindra Group.\n\n"
            "## Mahindra CIE Automotive | Mid Cap | Auto Components\n\n"
            "A Mahindra Group company with global operations.\n"
        )
        by_type, _ = _run(resolver, content)
        pairs = {(e.source, e.target) for e in by_type["same_group"]}
        assert ("Mahindra CIE Automotive", "Tech Mahindra") in pairs
        assert by_type["same_group"][0].properties["group"] == "Mahindra"

    def test_single_member_group_yields_no_edge(self, resolver):
        content = (
            "# Edition\n\n"
            "## Tech Mahindra | Large Cap | IT\n\n"
            "It remains the flagship company of the Mahindra Group.\n"
        )
        by_type, _ = _run(resolver, content)
        assert "same_group" not in by_type

    def test_normalize_group_name(self):
        assert _normalize_group_name("Tata Group") == "Tata"
        assert _normalize_group_name("Aditya Birla Corp") == "Aditya Birla"
        assert _normalize_group_name("Mahindra") == "Mahindra"


# --------------------------------------------------------------------------- #
# 4. Resolver ambiguity audit log                                             #
# --------------------------------------------------------------------------- #
class TestAmbiguityLog:
    def test_tied_fuzzy_candidates_logged(self):
        r = EntityResolver(
            [
                "Great Eastern Shipping",
                "Great Eastern Energy",
            ]
        )
        best = r.resolve("great eastern")
        # Resolution still returns ONE of the tied candidates (which one
        # depends on candidate-set iteration order — deterministic within a
        # process, not across runs).
        assert best in {"Great Eastern Shipping", "Great Eastern Energy"}
        # ...but the N-way tie itself is recorded for Tier-C reporting.
        assert r.ambiguous_log == [
            ("great eastern", ["Great Eastern Energy", "Great Eastern Shipping"]),
        ]

    def test_clear_winner_not_logged(self):
        r = EntityResolver(["Tata Motors", "Tata Steel"])
        assert r.resolve("tata motors limited") == "Tata Motors"
        assert r.ambiguous_log == []


# --------------------------------------------------------------------------- #
# 5. Diff-audit harness                                                       #
# --------------------------------------------------------------------------- #
class TestDiffAudit:
    def test_diff_counts_rows(self):
        rows = diff_counts(
            {"acquired": 19, "jv_with": 37},
            {"acquired": 20, "jv_with": 37},
        )
        assert rows == [("acquired", 19, 20, 1), ("jv_with", 37, 37, 0)]

    def test_diff_counts_new_and_lost_types(self):
        rows = diff_counts({"a": 1}, {"b": 2})
        assert rows == [("a", 1, 0, -1), ("b", 0, 2, 2)]

    def test_format_table_alignment(self):
        table = format_table([("acquired", 19, 20, 1)])
        lines = table.splitlines()
        assert lines[0].split() == ["edge_type", "before", "after", "delta"]
        assert lines[-1].split() == ["acquired", "19", "20", "+1"]

    def test_load_counts_roundtrip(self, tmp_path):
        p = tmp_path / "counts.json"
        p.write_text(json.dumps({"files": 3, "per_type": {"acquired": 5}, "total_edges": 5}))
        assert load_counts(p) == {"acquired": 5}


# --------------------------------------------------------------------------- #
# 6. Snapshot parity: v1 families fire alongside v2                           #
# --------------------------------------------------------------------------- #
class TestSnapshotParity:
    def test_combined_document_v1_plus_v2(self, resolver):
        """Before/after snapshot contract: every family present before E1
        still fires on a document that also exercises the new ones."""
        content = (
            "# Edition\n\n"
            "## Hindalco Industries | Large Cap | Metals\n\n"
            "The asset management joint venture with Microsoft is growing. "
            "Peers like Vedanta will feel the heat. It completed the "
            "acquisition of 26% stake in Titan.\n\n"
            "## Grasim Industries | Large Cap | Cement\n\n"
            "A Mahindra Group company note would cluster here; this section "
            "instead says it is part of the Aditya Birla Group.\n"
        )
        by_type, unresolved = _run(resolver, content)
        types = set(by_type)
        # v1 families untouched...
        assert "jv_with" in types  # "joint venture with Microsoft"
        assert "competes_with" in types  # "Peers like Vedanta"
        # ...v2 families co-present on the SAME corpus.
        assert "acquired" in types  # stake pattern
        assert by_type["acquired"][0].properties.get("stake_pct") == 26.0
        assert unresolved == []
