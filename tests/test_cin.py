"""Unit tests for helpers/core/cin.py (S3 of the ontology_convention_stack
proposal): CIN format parsing + facet extraction, VALUE-LENIENT warnings
(unknown ROC/ownership codes, year window, pre-2008 NIC vintages), and
the distinct LLPIN rejection (LLPs never carry a CIN).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.core.cin import parse_cin


class TestParseCin:
    def test_memo_example_full_facets(self):
        p = parse_cin("L01631KA2010PTC096843")
        assert p.ok
        assert p.value == "L01631KA2010PTC096843"
        assert p.listing == "L"
        assert p.nic5 == "01631"
        assert p.state == "KA"
        assert p.year == 2010
        assert p.ownership == "PTC"
        assert p.serial == "096843"
        assert p.warnings == ()

    def test_normalization_separators_and_case(self):
        p = parse_cin(" u01631 ka 2010 ptc 096843 ")
        assert p.ok and p.value == "U01631KA2010PTC096843"
        assert p.listing == "U"

    def test_attested_values_zero_warnings(self):
        assert parse_cin("L99999MH2021PLC000111").warnings == ()
        assert parse_cin("U22120WB2013SGC098765").warnings == ()

    def test_pre2008_vintage_warns_not_rejects(self):
        # NIC-98/2004 codes live inside pre-2008 CINs — never reject.
        p = parse_cin("L01631KA1996PTC096843")
        assert p.ok
        assert p.warnings == ("vintage_pre2008: 1996",)

    def test_unknown_state_warns(self):
        p = parse_cin("L01631ZZ2010PTC096843")
        assert p.ok and p.warnings == ("unknown_state: ZZ",)

    def test_unknown_ownership_warns(self):
        p = parse_cin("L01631KA2010XYZ096843")
        assert p.ok and p.warnings == ("unknown_ownership: XYZ",)

    def test_year_out_of_window(self):
        p = parse_cin("L01631KA1750PTC096843")
        kinds = [w.split(":")[0] for w in p.warnings]
        assert "year_out_of_window" in kinds

    def test_bad_format_is_error(self):
        p = parse_cin("not-a-cin")
        assert not p.ok and p.error == "format"

    def test_truncated_cin_is_format_error(self):
        p = parse_cin("L01631KA2010PTC09684")
        assert not p.ok and p.error == "format"

    def test_empty_and_none(self):
        assert parse_cin("").error == "format"
        assert parse_cin(None).error == "format"

    def test_llpin_rejected_distinctly(self):
        p = parse_cin("AAA-1234")
        assert not p.ok and p.error == "llpin"
        assert p.message is not None and "llpin" in p.message

    def test_unhyphenated_llpin_rejected_distinctly(self):
        p = parse_cin("AAB9926")
        assert not p.ok and p.error == "llpin"

    def test_warning_kind_prefix_machine_shape(self):
        # the integrity check aggregates by "kind:" prefix — keep the shape
        p = parse_cin("U99999ZZ1750XXX000000")
        kinds = {w.split(":", 1)[0] for w in p.warnings}
        assert kinds == {
            "unknown_ownership",
            "unknown_state",
            "year_out_of_window",
            "vintage_pre2008",
        }
