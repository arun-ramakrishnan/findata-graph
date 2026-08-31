#!/usr/bin/env python3
"""Tests for helpers/graph/extract_relations.py — split from the
original test_extract_relations.py for navigability.

Pure unit tests: entity resolution, regex patterns, section splitting.
"""

from __future__ import annotations


from helpers.graph.extract_relations import (  # noqa: E402
    EntityResolver,
    PATTERNS,
    _looks_like_speaker,
    _split_sections,
    _SUPPRESSED_EDGES,
)


# --------------------------------------------------------------------------- #
# EntityResolver                                                              #
# --------------------------------------------------------------------------- #
class TestEntityResolver:
    def test_exact_case_insensitive_match(self):
        r = EntityResolver(["Jio Financial Services", "BlackRock", "Titan"])
        assert r.resolve("Jio Financial Services") == "Jio Financial Services"
        assert r.resolve("jio financial services") == "Jio Financial Services"
        assert r.resolve("BlackRock") == "BlackRock"
        assert r.resolve("BLACKROCK") == "BlackRock"

    def test_suffix_stripped_match(self):
        r = EntityResolver(["Tata Chemicals", "HDFC Bank"])
        assert r.resolve("Tata Chemicals Limited") == "Tata Chemicals"
        assert r.resolve("Tata Chemicals Ltd") == "Tata Chemicals"
        assert r.resolve("Tata Chemicals Ltd.") == "Tata Chemicals"
        assert r.resolve("HDFC Bank Pvt Ltd") == "HDFC Bank"

    def test_single_distinctive_token_match(self):
        r = EntityResolver(["BlackRock", "Titan", "Titan Company"])
        # "BlackRock" is a single distinctive token; exact resolve path.
        assert r.resolve("BlackRock") == "BlackRock"

    def test_multi_token_subset_match(self):
        r = EntityResolver(["HDFC Bank", "HDFC Life", "ICICI Bank"])
        # 'HDFC Bank' has tokens {hdfc, bank}; mention 'HDFC Bank' shares both.
        assert r.resolve("HDFC Bank") == "HDFC Bank"

    def test_no_match_returns_none(self):
        r = EntityResolver(["Titan", "BlackRock"])
        assert r.resolve("Standard Chartered") is None
        assert r.resolve("") is None
        assert r.resolve("  ") is None

    def test_ambiguous_generic_only_mention_returns_none(self):
        # A mention that is entirely generic words (e.g. "Bank Holdings")
        # should not resolve to a company whose name shares only generic words.
        r = EntityResolver(["HDFC Bank", "ICICI Bank"])
        # "Bank" alone is a generic word; should not match.
        assert r.resolve("Bank") is None

    def test_alias_lookup(self):
        # IOCL/BPCL/ONGC resolve via the alias map even though the literal
        # abbreviation doesn't appear in entities.name.
        r = EntityResolver(["Indian Oil Corporation", "ONGC", "GAIL India"])
        assert r.resolve("IOCL") == "Indian Oil Corporation"
        assert r.resolve("iocl") == "Indian Oil Corporation"
        assert r.resolve("ONGC") == "ONGC"
        assert r.resolve("GAIL") == "GAIL India"

    def test_alias_skipped_when_target_not_in_db(self):
        # Alias pointing at a name not in the resolver returns None rather
        # than the alias target.
        r = EntityResolver([])  # empty DB
        assert r.resolve("IOCL") is None

    def test_brand_and_parent_aliases(self):
        # Brand / single-entity aliases added from the sidecar triage. Each
        # must resolve to an existing entity, and must NOT collapse a global
        # parent mention into its Indian subsidiary (which would suppress
        # legitimate subsidiary_of edges).
        r = EntityResolver(
            [
                "Bata India",
                "CEAT",
                "Diageo plc",
                "Fintellix",
                "Sagility",
                "Shigan Quantum Technologies",
                "Swaraj Engines",
            ]
        )
        assert r.resolve("bata") == "Bata India"
        assert r.resolve("CEAT") == "CEAT"
        assert r.resolve("Diageo") == "Diageo plc"
        assert r.resolve("Fintellix") == "Fintellix"
        assert r.resolve("Sagility") == "Sagility"
        assert r.resolve("Shigan") == "Shigan Quantum Technologies"
        assert r.resolve("Swaraj") == "Swaraj Engines"
        # Global-parent mentions must NOT resolve to the Indian subsidiary.
        assert r.resolve("Abbott Laboratories") is None
        assert r.resolve("Novartis AG") is None
        assert r.resolve("Cummins Inc") is None

    def test_alias_does_not_overmatch_generic_word(self):
        # "bata" is also a generic word in some locales, but as a brand it
        # must resolve. "site" / "data" / "valve" are NOT aliases.
        r = EntityResolver(["Bata India", "CEAT"])
        assert r.resolve("site") is None
        assert r.resolve("data") is None
        assert r.resolve("valve") is None

    def test_first_token_alias_resolves_parent_mention(self):
        # Prose like "subsidiary of Diageo plc" captures the full mention
        # "Diageo plc". The brand-token alias `diageo` must resolve it.
        r = EntityResolver(["Diageo plc", "CEAT"])
        assert r.resolve("Diageo plc") == "Diageo plc"
        # When the first token is NOT a known alias, fall through.
        assert r.resolve("Unknown Laboratories") is None

    def test_first_token_alias_strips_possessive(self):
        # "Japan's Kubota Corporation", "Sweden's AB Volvo", "the Volvo Group"
        # must resolve via the brand token, stripping the leading article /
        # country-possessive first.
        r = EntityResolver(
            [
                "Kubota Corporation",
                "AB Volvo",
                "TotalEnergies SE",
                "Innoviz Technologies",
            ]
        )
        assert r.resolve("Japan's Kubota Corporation") == "Kubota Corporation"
        assert r.resolve("Sweden's AB Volvo") == "AB Volvo"
        assert r.resolve("the Volvo Group") == "AB Volvo"
        assert r.resolve("Innoviz requires regulatory clearances") == "Innoviz Technologies"

    def test_icra_fisdom_attribution_bleed_is_suppressed(self):
        # The Groww section in A_Quarter_That_Refuses_To_Behave.md has an
        # OCR-garbled heading that doesn't resolve; its 'acquired Fisdom'
        # prose was mis-attributed to ICRA (the prior section). The triple
        # is hand-suppressed.
        assert ("ICRA", "Fisdom", "acquired") in _SUPPRESSED_EDGES


class TestSupplierCustomerPatterns:
    """Tests for supplier_to / customer_of patterns."""

    def test_supplier_to_pattern_matches_named_target(self):
        test = "We are the global supplier for Hitachi Energy for an SF6 replacement."
        sup_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "supplier_to"]
        matched = []
        for pat, _et, _s, _d in sup_pats:
            for m in pat.finditer(test):
                matched.append(m.group(1))
        assert any("Hitachi Energy" in m for m in matched), matched

    def test_supplier_to_pattern_rejects_generic_target(self):
        # "supplier to OEMs" should still match the pattern; the FILTER at
        # extraction time rejects it (not the regex).
        test = "We are a leading supplier to OEMs globally."
        sup_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "supplier_to"]
        matched = False
        for pat, _et, _s, _d in sup_pats:
            if pat.search(test):
                matched = True
        # Pattern fires (capture), filter rejects downstream.
        assert matched

    def test_customer_of_parens_pattern_matches(self):
        test = "major customers (IOCL 1.5 MMSCMD, BPCI 0.8 MMSCMD) easily switched"
        cust_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "customer_of"]
        matched = False
        for pat, _et, _s, _d in cust_pats:
            m = pat.search(test)
            if m and m.group(1):
                matched = True
                assert "IOCL" in m.group(1)
        assert matched

    def test_securing_orders_pattern_matches(self):
        test = "Talbros already securing Tata Motors orders and actively pursuing RFQs."
        sup_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "supplier_to"]
        matched = False
        for pat, _et, _s, _d in sup_pats:
            m = pat.search(test)
            if m and "Tata Motors" in m.group(1):
                matched = True
        assert matched

    # --- G1.3 vendor/sources patterns (2026-08) --- #
    def test_vendor_to_pattern_matches(self):
        test = "The firm is a key vendor to Maruti Suzuki for steering components."
        sup_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "supplier_to"]
        matched = False
        for pat, _et, _s, _d in sup_pats:
            m = pat.search(test)
            if m and "Maruti Suzuki" in m.group(1):
                matched = True
        assert matched, "expected 'vendor to' to match a supplier_to pattern"

    def test_sources_from_pattern_is_reverse_direction(self):
        # "sources from X" means X is the SUPPLIER (source), the section company
        # is the CUSTOMER (target). The pattern must carry direction='reverse'
        # so the resolver swaps source/target correctly.
        test = "The company sources from Hindalco for its aluminium requirements."
        sup_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "supplier_to"]
        found_reverse = False
        for pat, _et, _s, d in sup_pats:
            m = pat.search(test)
            if m and "Hindalco" in m.group(1):
                assert d == "reverse", (
                    f"'sources from' pattern must be direction='reverse' "
                    f"(captured X is the supplier), got {d!r}"
                )
                found_reverse = True
        assert found_reverse, "expected 'sources from' to match a supplier_to pattern"


# --------------------------------------------------------------------------- #
# Pattern library — direct regex tests                                        #
# --------------------------------------------------------------------------- #
class TestPatterns:
    def test_jv_with_pattern_matches(self):
        test = "The asset management joint venture with BlackRock is rapidly gaining scale"
        jv_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "jv_with"]
        assert len(jv_pats) >= 1
        matched = False
        for pat, _et, _s, _d in jv_pats:
            m = pat.search(test)
            if m:
                assert "BlackRock" in m.group(1)
                matched = True
        assert matched, "expected at least one jv_with pattern to match"

    # --- G1.1 JV synonyms (2026-08) --- #
    def test_tieup_with_pattern_matches(self):
        test = "The company announced a tie-up with Tata Motors for EV components."
        jv_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "jv_with"]
        matched = False
        for pat, _et, _s, _d in jv_pats:
            m = pat.search(test)
            if m and "Tata Motors" in m.group(1):
                matched = True
        assert matched, "expected 'tie-up with' to match a jv_with pattern"

    def test_partnership_with_pattern_matches(self):
        test = "In partnership with Reliance, the firm will launch a new platform."
        jv_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "jv_with"]
        matched = False
        for pat, _et, _s, _d in jv_pats:
            m = pat.search(test)
            if m and "Reliance" in m.group(1):
                matched = True
        assert matched, "expected 'partnership with' to match a jv_with pattern"

    def test_alliance_with_pattern_matches(self):
        test = "A strategic alliance with Siemens was formalised this quarter."
        jv_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "jv_with"]
        matched = False
        for pat, _et, _s, _d in jv_pats:
            m = pat.search(test)
            if m and "Siemens" in m.group(1):
                matched = True
        assert matched, "expected 'alliance with' to match a jv_with pattern"

    def test_partnership_with_generic_target_yields_no_resolvable_entity(self):
        # The capture group requires a capital-led proper noun ((?-i:[A-Z])...),
        # so a lowercase generic target like "the government" is NOT matched at
        # all — the [A-Z] anchor rejects it before any downstream filtering.
        # This is the first line of defence against generic-target false positives.
        test = "The company formed a partnership with the government of India."
        jv_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "jv_with"]
        captured = []
        for pat, _et, _s, _d in jv_pats:
            m = pat.search(test)
            if m:
                captured.append(m.group(1).strip())
        assert captured == [], f"expected no capture for generic target, got {captured}"

    def test_acquired_forward_pattern_matches(self):
        test = "We completed 67% stake acquisition of Damas last quarter."
        acq_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "acquired"]
        matched_mentions = []
        for pat, _et, _s, d in acq_pats:
            for m in pat.finditer(test):
                matched_mentions.append((m.group(1).strip(), d))
        # At least one should mention Damas.
        assert any("Damas" in m for m, _ in matched_mentions), matched_mentions

    def test_subsidiary_of_pattern_matches(self):
        test = "Rallis India Limited, a subsidiary of Tata Chemicals, is part of Tata Group."
        sub_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "subsidiary_of"]
        matched = False
        for pat, _et, _s, _d in sub_pats:
            m = pat.search(test)
            if m and "Tata Chemicals" in m.group(1):
                matched = True
        assert matched

    # --- H1 new verb patterns (direct-regex layer) --- #
    def test_demerged_from_pattern_matches(self):
        test = "The entity demerged from Samvardhana Motherson in 2021."
        acq_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "acquired"]
        matched = False
        for pat, _et, _s, d in acq_pats:
            m = pat.search(test)
            if m and "Samvardhana" in m.group(1) and d == "reverse":
                matched = True
        assert matched, "no acquired/reverse pattern matched 'demerged from'"

    def test_merged_with_pattern_matches(self):
        test = "Devyani International merged with Sapphire Foods last year."
        acq_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "acquired"]
        matched = False
        for pat, _et, _s, d in acq_pats:
            m = pat.search(test)
            if m and "Sapphire" in m.group(1) and d == "reverse":
                matched = True
        assert matched, "no acquired/reverse pattern matched 'merged with'"

    def test_formed_through_merger_pattern_matches(self):
        test = "LTM was formed through the merger of Larsen and Toubro in 2022."
        acq_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "acquired"]
        matched = False
        for pat, _et, _s, d in acq_pats:
            m = pat.search(test)
            if m and "Larsen" in m.group(1) and d == "reverse":
                matched = True
        assert matched, "no acquired/reverse pattern matched 'formed through merger'"

    def test_listed_subsidiary_reverse_pattern_matches(self):
        test = "Its Indian listed subsidiary is Hindustan Unilever."
        sub_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "subsidiary_of"]
        matched = False
        for pat, _et, _s, d in sub_pats:
            m = pat.search(test)
            if m and "Hindustan Unilever" in m.group(1) and d == "reverse":
                matched = True
        assert matched, "no subsidiary_of/reverse pattern matched 'listed subsidiary is X'"

    def test_listed_subsidiary_pattern_rejects_bare_noun_adjunct(self):
        # Precision guard: bare "Subsidiary <Name>" must NOT match the narrow
        # reverse pattern (measured ~92% false positives without the anchor).
        test = "Subsidiary NRB Thailand serves international markets."
        sub_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "subsidiary_of"]
        for pat, _et, _s, _d in sub_pats:
            m = pat.search(test)
            if m:
                # If any subsidiary_of pattern matches, the captured group
                # must not be "NRB Thailand serves internatio..." — that's the
                # bare noun-adjunct false-positive form.
                assert "NRB Thailand serves" not in m.group(1), (
                    f"bare noun-adjunct form falsely captured: {m.group(1)!r}"
                )

    def test_pattern_does_not_match_common_english(self):
        # "joint venture" without "with" — should not falsely capture.
        test = "We are launching a joint venture next year."
        jv_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "jv_with"]
        for pat, _et, _s, _d in jv_pats:
            assert pat.search(test) is None, f"false positive on: {test}"

    def test_competes_with_pattern_matches(self):
        # Pattern A: named-list form ("peers like X").
        test_a = "Indian peers like Tata Motors and Ashok Leyland dominate the CV market."
        # Pattern B: bare "competes with X" where X is a proper noun.
        test_b = "The company competes with Acme in the enterprise segment."
        cw_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "competes_with"]
        assert len(cw_pats) >= 2, "expected at least 2 competes_with patterns"
        matched_a = False
        matched_b = False
        for pat, _et, _s, _d in cw_pats:
            if pat.search(test_a):
                matched_a = True
            if pat.search(test_b):
                matched_b = True
        assert matched_a, "no competes_with pattern matched the named-list form"
        assert matched_b, "no competes_with pattern matched the bare 'competes with X' form"

    def test_competes_with_pattern_rejects_generic_target(self):
        # Bare "competes with peers" — Pattern B's negative lookahead must reject.
        test = "The company competes with peers in the domestic formulations market."
        cw_pats = [(p, et, s, d) for p, et, s, d in PATTERNS if et == "competes_with"]
        for pat, _et, _s, _d in cw_pats:
            # Pattern A (peers like X) won't fire on "competes with peers";
            # Pattern B (competes with X) must refuse via negative lookahead.
            m = pat.search(test)
            if m:
                # If it matched, the captured group must not be a generic word.
                assert m.group(1).strip().lower() not in {
                    "peers",
                    "rivals",
                    "competitors",
                    "chinese",
                    "indian",
                }, f"pattern falsely captured generic target: {m.group(1)!r}"


# --------------------------------------------------------------------------- #
# Section splitter                                                            #
# --------------------------------------------------------------------------- #
class TestSplitSections:
    def test_pipe_separated_company_is_section(self):
        content = (
            "# The Chatter: Foo\n\n"
            "## Jio Financial Services Limited | Large Cap | Financial Services\n\n"
            "Body about JFS.\n\n"
            "## [Concall]\n\nQuote here.\n\n"
            "## — Speaker, CEO\n\nMore.\n"
        )
        sections = _split_sections(content)
        # Only one company section expected.
        assert len(sections) == 1
        name, _hs, _bs, _be = sections[0]
        assert name == "Jio Financial Services"

    def test_speaker_heading_absorbed_into_previous_company(self):
        content = (
            "## Tata Steel Limited | Large Cap | Metals\n\n"
            "Body about Tata Steel.\n\n"
            "## — T.V. Narendran, CEO\n\n"
            "Speaker quote.\n\n"
            "## Hindalco | Large Cap | Metals\n\n"
            "Next company.\n"
        )
        sections = _split_sections(content)
        names = [s[0] for s in sections]
        # Two company sections, no speaker section.
        assert "Tata Steel" in names
        assert "Hindalco" in names
        assert not any("Narendran" in n for n in names)

    def test_looks_like_speaker(self):
        assert _looks_like_speaker("Hitesh Sethia, Managing Director and CEO")
        assert _looks_like_speaker("Vineet Agrawal, Group CFO")
        assert _looks_like_speaker("Karan Bhagat, MD and CEO")
        assert not _looks_like_speaker("Jio Financial Services")
        assert not _looks_like_speaker("Tata Steel")
