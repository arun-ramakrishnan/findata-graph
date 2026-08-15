#!/usr/bin/env python3
"""Tests for helpers/graph/extract_relations.py — split from the
original test_extract_relations.py for navigability.

End-to-end relation extraction: JV, subsidiary, competes-with, supplier/customer, H1 new-verb patterns.
"""
from __future__ import annotations

import pytest

from helpers.graph.extract_relations import (  # noqa: E402
    EntityResolver,
    extract_relations,
)


# --------------------------------------------------------------------------- #
# Full extraction                                                             #
# --------------------------------------------------------------------------- #
class TestExtractRelations:
    @pytest.fixture
    def resolver(self):
        return EntityResolver([
            "Jio Financial Services", "BlackRock", "Allianz",
            "Titan", "IFB Industries",
            "Rallis India", "Tata Chemicals", "Tata Motors",
            "Hindustan Zinc", "Vedanta",
            "HDFC Bank", "HDB Financial Services",
        ])

    def test_jv_with_extraction(self, resolver):
        content = (
            "## Jio Financial Services Limited | Large Cap | Financial Services\n\n"
            "The asset management joint venture with BlackRock is rapidly gaining "
            "scale by targeting first-time investors.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test Edition",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "jv_with" in by_type
        assert len(by_type["jv_with"]) == 1
        edge = by_type["jv_with"][0]
        # Symmetric canonical: BlackRock <= Jio Financial Services.
        assert {edge.source, edge.target} == {"BlackRock", "Jio Financial Services"}
        assert edge.properties["edition"] == "Test Edition"
        assert edge.properties["newsletter"] == "The_Chatter"
        assert "BlackRock" in edge.properties["quote"]
        assert edge.symmetric is True
        assert unresolved == []

    def test_subsidiary_of_extraction(self, resolver):
        # Rallis India IS in the resolver (see fixture). The section should
        # produce a subsidiary_of edge to Tata Chemicals.
        content = (
            "## Rallis India Limited | Mid Cap | Agriculture\n\n"
            "Rallis India Limited, a subsidiary of Tata Chemicals Limited, is part "
            "of Tata Group, operating in Agri-Sciences.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test Edition",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "subsidiary_of" in by_type
        edge = by_type["subsidiary_of"][0]
        assert edge.source == "Rallis India"
        assert edge.target == "Tata Chemicals"
        assert unresolved == []

    def test_subsidiary_of_extraction_with_known_source(self, resolver):
        content = (
            "## Hindustan Zinc Limited | Large Cap | Metals\n\n"
            "Hindustan Zinc Limited (HZL) is a leading producer. As a subsidiary "
            "of Vedanta Limited, it holds a significant market share.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test Edition",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "subsidiary_of" in by_type
        edge = by_type["subsidiary_of"][0]
        assert edge.source == "Hindustan Zinc"
        assert edge.target == "Vedanta"
        assert edge.symmetric is False

    def test_same_group_extraction(self, resolver):
        # Two companies in the same newsletter both declare Aditya Birla Group.
        # They should get a same_group edge even though "Aditya Birla Group"
        # itself isn't a known entity.
        resolver2 = EntityResolver([
            "UltraTech Cement", "Aditya Birla Fashion and Retail",
        ])
        content = (
            "## UltraTech Cement Limited | Large Cap | Building Materials\n\n"
            "UltraTech Cement, part of the Aditya Birla Group, is a leading "
            "manufacturer of grey cement.\n\n"
            "## Aditya Birla Fashion and Retail Limited | Mid Cap | Consumer\n\n"
            "ABFRL is one of India's leading fashion retail companies, part of "
            "the Aditya Birla Group.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test Edition",
            newsletter_type="The_Chatter", resolver=resolver2,
        )
        assert "same_group" in by_type
        edge = by_type["same_group"][0]
        assert {edge.source, edge.target} == {
            "UltraTech Cement", "Aditya Birla Fashion and Retail",
        }
        assert edge.properties["group"] == "Aditya Birla"

    def test_unresolved_goes_to_sidecar(self, resolver):
        content = (
            "## Jio Financial Services Limited | Large Cap | Financial Services\n\n"
            "We completed 67% stake acquisition of Damas last quarter, expanding "
            "our footprint in luxury jewellery.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test Edition",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        # Damas is not in the resolver — should appear in unresolved.
        assert by_type == {}
        assert len(unresolved) == 1
        u = unresolved[0]
        assert u.edge_type == "acquired"
        assert u.source == "Jio Financial Services"
        assert "Damas" in u.target_mention
        assert u.edition == "Test Edition"

    def test_self_edge_skipped(self, resolver):
        # "JFS acquired JFS" should not produce an edge.
        content = (
            "## Jio Financial Services Limited | Large Cap | Financial Services\n\n"
            "Jio Financial Services acquired Jio Financial Services in a strange deal.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "acquired" not in by_type

    def test_dedup_within_edition(self, resolver):
        # Mention BlackRock twice in the same section.
        content = (
            "## Jio Financial Services Limited | Large Cap | Financial Services\n\n"
            "The joint venture with BlackRock is growing. The joint venture with "
            "BlackRock has been profitable.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert len(by_type.get("jv_with", [])) == 1


# --------------------------------------------------------------------------- #
# H1: new relationship verb patterns (2026-07-28)                              #
# --------------------------------------------------------------------------- #
# These cover corporate-action verbs the original forward/reverse "acquired"
# patterns did not match: demerger, merger, "formed through merger", and the
# anchored "listed subsidiary is X" form. All map to existing edge types
# (`acquired` for M&A, `subsidiary_of` for parent↔subsidiary) with the named
# counterpart as the source — see extract_relations.py PATTERNS for rationale.
class TestH1NewVerbPatterns:
    """End-to-end tests for the H1 verb-pattern additions."""

    @pytest.fixture
    def resolver(self):
        # Seed both the section source and the expected counterpart so the
        # edges resolve (mirrors the TestExtractRelations fixture convention).
        return EntityResolver([
            "Motherson Sumi Wiring India", "Samvardhana Motherson",
            "Devyani International", "Sapphire Foods",
            "LTM", "Larsen and Toubro",
            "Samsung Electronics", "Samsung SDI",
            "Unilever", "Hindustan Unilever",
        ])

    def test_demerged_from_produces_reverse_acquired(self, resolver):
        # "demerged from X" → X is the source (continuing parent entity),
        # section company is the target (the spun-off entity).
        content = (
            "## Motherson Sumi Wiring India | Mid Cap | Auto Components\n\n"
            "Motherson Sumi Wiring India Demerged from Samvardhana Motherson "
            "in 2021, listing the Indian wiring business separately.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "acquired" in by_type
        edge = by_type["acquired"][0]
        assert edge.source == "Samvardhana Motherson"
        assert edge.target == "Motherson Sumi Wiring India"
        assert unresolved == []

    def test_merged_with_produces_reverse_acquired(self, resolver):
        # "merged with X" → X becomes the source.
        content = (
            "## Devyani International | Mid Cap | Consumer\n\n"
            "Devyani International merged with Sapphire Foods, creating one of "
            "India's largest quick-service restaurant operators.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "acquired" in by_type
        edge = by_type["acquired"][0]
        assert edge.source == "Sapphire Foods"
        assert edge.target == "Devyani International"
        assert unresolved == []

    def test_formed_through_merger_produces_reverse_acquired(self, resolver):
        # "formed through the merger of X and Y" → captures X as the source.
        # Uses '&' inside the constituent name — the realistic form (the LTM
        # note: "merger of Larsen & Toubro Infotech and Mindtree"). 'and' is a
        # terminator stopword, so a name written "Larsen and Toubro and Y"
        # would truncate at the first 'and'; the real corpus uses '&'.
        content = (
            "## LTM | Large Cap | Technology\n\n"
            "LTM is a global technology company formed through the merger of "
            "Larsen & Toubro Infotech and Mindtree in 2022.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "acquired" in by_type
        edge = by_type["acquired"][0]
        assert edge.source == "Larsen and Toubro"
        assert edge.target == "LTM"

    def test_listed_subsidiary_is_X_produces_reverse_subsidiary_of(
        self, resolver
    ):
        # "listed subsidiary is X" / "its Indian subsidiary is X" → section
        # company is the PARENT, X is the subsidiary. This is the reverse of
        # the "subsidiary of X" forward pattern.
        content = (
            "## Unilever | Large Cap | Consumer\n\n"
            "Unilever is a global FMCG giant. Its Indian listed subsidiary is "
            "Hindustan Unilever, the country's largest FMCG company.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "subsidiary_of" in by_type
        edge = by_type["subsidiary_of"][0]
        assert edge.source == "Hindustan Unilever"
        assert edge.target == "Unilever"
        assert unresolved == []

    def test_listed_subsidiary_bare_noun_adjunct_not_matched(self, resolver):
        # CRITICAL precision guard: the bare "subsidiary <Name>" noun-adjunct
        # form ("subsidiary NRB Thailand serves...") was measured at ~92% false
        # positives. The narrow pattern requires a copula anchor (is/named/
        # called) OR the "listed subsidiary" qualifier, so this must NOT match.
        content = (
            "## Samsung Electronics | Large Cap | Technology\n\n"
            "Samsung Electronics has many arms. Subsidiary Samsung Mobile "
            "serves global markets with premium devices.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        # The bare "Subsidiary Samsung Mobile" form must not produce an edge.
        assert "subsidiary_of" not in by_type or all(
            e.source != "Samsung Electronics" and e.target != "Samsung Mobile"
            for e in by_type.get("subsidiary_of", [])
        )

    def test_new_patterns_unresolved_target_goes_to_sidecar(self, resolver):
        # If the named counterpart isn't an entity, the match goes to the
        # sidecar (same as existing patterns) — not silently dropped.
        content = (
            "## Devyani International | Mid Cap | Consumer\n\n"
            "Devyani International merged with Acme Holdings, a fictional firm.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "acquired" not in by_type
        assert len(unresolved) == 1
        assert unresolved[0].edge_type == "acquired"
        assert "Acme" in unresolved[0].target_mention


# --------------------------------------------------------------------------- #
# Generic-target filter                                                       #
# --------------------------------------------------------------------------- #
class TestGenericAcquiredFilter:
    def test_acquired_land_is_filtered(self):
        resolver = EntityResolver(["Power Grid Corporation"])
        content = (
            "## Power Grid Corporation | Large Cap | Utilities\n\n"
            "We commissioned the substation 9 to 10 months from date of "
            "acquisition of land, which may be a world record.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        # No edges and no unresolved (filter rejects before sidecar).
        assert by_type == {}
        assert unresolved == []


class TestSupplierCustomerExtraction:
    """Integration tests for supplier_to / customer_of edge extraction."""

    def test_supplier_to_with_named_entity(self):
        resolver = EntityResolver([
            "Laxmi Organic", "Hitachi Energy India",
        ])
        content = (
            "## Laxmi Organic | Mid Cap | Chemicals\n\n"
            "We are going to be the global supplier for Hitachi Energy for an "
            "SF6 replacement product with the same functionality.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "supplier_to" in by_type
        edge = by_type["supplier_to"][0]
        assert edge.source == "Laxmi Organic"
        assert edge.target == "Hitachi Energy India"
        assert edge.symmetric is False
        assert unresolved == []

    def test_supplier_to_generic_target_filtered(self):
        resolver = EntityResolver(["Bosch Limited"])
        content = (
            "## Bosch Limited | Large Cap | Auto Ancillary\n\n"
            "Bosch is a leading supplier to OEMs and the aftermarket globally.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        # OEMs is a generic target — filtered out, not sidecarred.
        assert "supplier_to" not in by_type
        assert unresolved == []

    def test_customer_of_parens_chunked(self):
        # Multi-entity parenthesised list. Only resolvable chunks emit edges.
        resolver = EntityResolver([
            "GAIL India", "Indian Oil Corporation",  # NOT BPCI
        ])
        content = (
            "## GAIL India Limited | Large Cap | Energy\n\n"
            "Competitive vulnerability revealed as major customers "
            "(IOCL 1.5 MMSCMD, BPCI 0.8 MMSCMD) easily switched to liquid fuels.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "customer_of" in by_type
        # IOCL resolves via alias to Indian Oil Corporation.
        ioc_edge = next(
            e for e in by_type["customer_of"]
            if e.target == "Indian Oil Corporation"
        )
        assert ioc_edge.source == "GAIL India"

    def test_customer_of_all_chunks_unresolved_goes_to_sidecar(self):
        resolver = EntityResolver(["GAIL India"])
        content = (
            "## GAIL India Limited | Large Cap | Energy\n\n"
            "Major customers (FOO 1.0 X, BAR 2.0 Y) easily switched this quarter.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert by_type == {}
        # FOO/BAR weren't resolvable; record the whole mention for review.
        assert len(unresolved) == 1
        assert "FOO" in unresolved[0].target_mention


# --------------------------------------------------------------------------- #
# competes_with extraction                                                    #
# --------------------------------------------------------------------------- #
class TestCompetesWithExtraction:
    """Integration tests for competes_with edge extraction.

    Mirrors TestExtractRelations / TestSupplierCustomerExtraction style:
    hand-built resolver, hand-crafted newsletter section, assert on the
    returned (by_type, unresolved) tuple.
    """

    @pytest.fixture
    def resolver(self):
        return EntityResolver([
            "Hero MotoCorp",                              # the section company
            "Bajaj Auto", "TVS Motor Company", "Eicher Motors",
            "Tata Motors", "Ashok Leyland",
            "Pfizer",                                     # Pattern B single-target
        ])

    def test_competes_with_named_list_emits_one_edge_per_target(self, resolver):
        # Pattern A happy path: comma + "and" list. The wide capture is split
        # into per-name chunks; one symmetric edge per resolved target.
        content = (
            "## Hero MotoCorp | Large Cap | Automotive\n\n"
            "Indian peers like Bajaj Auto, TVS Motor Company, and Eicher Motors "
            "are gaining share in the entry-level segment.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test Edition",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert "competes_with" in by_type
        pairs = {(e.source, e.target) for e in by_type["competes_with"]}
        # Symmetric canonical ordering: source < target alphabetically.
        assert ("Bajaj Auto", "Hero MotoCorp") in pairs
        assert ("Eicher Motors", "Hero MotoCorp") in pairs
        assert ("Hero MotoCorp", "TVS Motor Company") in pairs
        assert len(by_type["competes_with"]) == 3
        edge = by_type["competes_with"][0]
        assert edge.symmetric is True
        assert edge.properties["edition"] == "Test Edition"
        assert edge.properties["newsletter"] == "The_Chatter"
        assert "Bajaj Auto" in edge.properties["quote"]
        assert unresolved == []

    def test_competes_with_and_only_list(self, resolver):
        # "peers like A and B" — no comma, only a conjunction.
        # The split-on-conjunction branch must still produce 2 edges.
        content = (
            "## Hero MotoCorp | Large Cap | Automotive\n\n"
            "Indian peers like Tata Motors and Ashok Leyland dominate the "
            "commercial vehicle market.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="T",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        pairs = {(e.source, e.target) for e in by_type.get("competes_with", [])}
        assert ("Ashok Leyland", "Hero MotoCorp") in pairs
        assert ("Hero MotoCorp", "Tata Motors") in pairs
        assert len(by_type["competes_with"]) == 2

    def test_competes_with_bare_form_with_named_target(self, resolver):
        # Pattern B: "competes with X" where X is a proper noun.
        content = (
            "## Hero MotoCorp | Large Cap | Pharma\n\n"
            "The company competes with Pfizer in the vaccine segment.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="T",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert len(by_type.get("competes_with", [])) == 1
        edge = by_type["competes_with"][0]
        assert {edge.source, edge.target} == {"Hero MotoCorp", "Pfizer"}
        assert edge.symmetric is True
        assert unresolved == []

    def test_competes_with_generic_target_silently_dropped(self, resolver):
        # "competes with peers" — Pattern B's negative lookahead rejects;
        # Pattern A's filter rejects any residual. NO sidecar entry (the
        # prose named no entity).
        content = (
            "## Hero MotoCorp | Large Cap | Automotive\n\n"
            "The company competes with peers in the domestic market, and faces "
            "intense competition from Chinese imports.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="T",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert by_type.get("competes_with", []) == []
        cw_sidecar = [u for u in unresolved if u.edge_type == "competes_with"]
        assert cw_sidecar == [], (
            "generic competes_with targets must be silently dropped, not "
            f"sidecarred: {cw_sidecar}"
        )

    def test_competes_with_self_edge_skipped(self, resolver):
        # Section company mentioned as its own competitor — must not emit.
        content = (
            "## Hero MotoCorp | Large Cap | Automotive\n\n"
            "Indian peers like Hero MotoCorp and Bajaj Auto both grew volumes.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="T",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        cw = by_type.get("competes_with", [])
        # Only the Bajaj Auto edge; Hero<->Hero self-edge must be skipped.
        assert all(e.source != e.target for e in cw)
        assert len(cw) == 1
        assert {cw[0].source, cw[0].target} == {"Bajaj Auto", "Hero MotoCorp"}

    def test_competes_with_dedup_within_edition(self, resolver):
        # Mention the same competitor pair twice — must collapse to one edge.
        content = (
            "## Hero MotoCorp | Large Cap | Automotive\n\n"
            "The company competes with Pfizer in vaccines. It also competes "
            "with Pfizer in oncology.\n"
        )
        by_type, _ = extract_relations(
            content, edition_title="T",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert len(by_type.get("competes_with", [])) == 1

    def test_competes_with_partial_list_resolution(self):
        # Mix of resolvable + unresolvable chunks in a list. Only resolvable
        # chunks emit edges; the unresolvable name is NOT sidecarred because
        # at least one edge was emitted (matches customer_of behaviour).
        resolver = EntityResolver([
            "UPL", "Syngenta",  # PiLimited & BASF intentionally absent
        ])
        content = (
            "## UPL | Large Cap | Chemicals\n\n"
            "Competitors such as Syngenta, BASF, and Bayer dominate the "
            "agrochemicals market.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="T",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        cw = by_type.get("competes_with", [])
        # Only Syngenta resolves; BASF and Bayer are absent from the resolver.
        assert len(cw) == 1
        assert {cw[0].source, cw[0].target} == {"Syngenta", "UPL"}
        # No competes_with sidecar entries (partial success suppresses sidecar).
        assert [u for u in unresolved if u.edge_type == "competes_with"] == []

    def test_competes_with_unresolved_single_mention_goes_to_sidecar(
        self, resolver,
    ):
        # Single unresolvable target (no list): goes to sidecar for triage.
        content = (
            "## Hero MotoCorp | Large Cap | Automotive\n\n"
            "The company competes with UnknownCo in the premium segment.\n"
        )
        by_type, unresolved = extract_relations(
            content, edition_title="Test Edition",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert by_type.get("competes_with", []) == []
        cw_sidecar = [u for u in unresolved if u.edge_type == "competes_with"]
        assert len(cw_sidecar) == 1
        assert cw_sidecar[0].source == "Hero MotoCorp"
        assert "UnknownCo" in cw_sidecar[0].target_mention
        assert cw_sidecar[0].edition == "Test Edition"

    def test_competes_with_rejects_sector_grouping_context(self, resolver):
        # "alongside peers like X" / "grouped with peers such as X" describe
        # sector classification, not direct competition. Silently dropped.
        content_alongside = (
            "## Hero MotoCorp | Large Cap | Automotive\n\n"
            "Operates alongside peers like Bajaj Auto in the domestic market.\n"
        )
        by_type, _ = extract_relations(
            content_alongside, edition_title="T",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert by_type.get("competes_with", []) == []

        content_grouped = (
            "## Hero MotoCorp | Large Cap | Automotive\n\n"
            "Featured as a Large Cap company across four newsletters; grouped "
            "with peers such as Bajaj Auto and TVS Motor Company.\n"
        )
        by_type2, _ = extract_relations(
            content_grouped, edition_title="T",
            newsletter_type="The_Chatter", resolver=resolver,
        )
        assert by_type2.get("competes_with", []) == []

