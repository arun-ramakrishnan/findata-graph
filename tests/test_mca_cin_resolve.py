"""Unit tests for mca_cin_resolve (CIN auto-resolver ladder)."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mca_cin_resolve", REPO / "helpers" / "maintenance" / "mca_cin_resolve.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class TestNameVariants:
    def test_listing_name_first(self):
        v = m.name_variants("Astral Pipes", "Astral Ltd")
        assert v[0] == "ASTRAL LTD"

    def test_suffix_forms(self):
        v = m.name_variants("Cosmo First")
        assert "COSMO FIRST LIMITED" in v
        assert "COSMO FIRST PVT LTD" in v

    def test_ampersand_variant(self):
        v = m.name_variants("Garden Reach Shipbuilders and Engineers")
        assert "GARDEN REACH SHIPBUILDERS & ENGINEERS" in v

    def test_parenthetical_stripped(self):
        v = m.name_variants("EaseMyTrip (Easy Trip Planners)")
        assert all("EASYMYTRIP (EASY" not in x for x in v)
        assert v[0].startswith("EASEMYTRIP")


class TestCinRe:
    def test_valid_plc(self):
        assert m.CIN_RE.fullmatch("L25200GJ1996PLC029134")

    def test_valid_pvt(self):
        assert m.CIN_RE.fullmatch("U15400CT2007PTC008170")

    def test_rejects_garbage(self):
        assert not m.CIN_RE.fullmatch("INE848H01015")
        assert not m.CIN_RE.fullmatch("L2520GJ1996PLC029134")


class TestValidateWebCandidates:
    def test_ogd_single_record_upgrade(self, monkeypatch):
        monkeypatch.setattr(
            m,
            "_ogd",
            lambda field, value, key, limit="5": [
                {
                    "cin": value,
                    "company_name": "ASTRAL LIMITED",
                    "company_status": "Active",
                    "company_class": "Public",
                    "state": "Gujarat",
                }
            ],
        )
        rows = m.validate_web_candidates(
            {"Astral Pipes": {"cin": "L25200GJ1996PLC029134"}}, key="k", revalidate=True
        )
        assert rows and rows[0]["via"] == "auto-web+ogd"
        assert rows[0]["mca_name"] == "ASTRAL LIMITED"

    def test_ogd_unknown_rejected(self, monkeypatch):
        monkeypatch.setattr(m, "_ogd", lambda field, value, key, limit="5": [])
        rows = m.validate_web_candidates(
            {"X": {"cin": "L25200GJ1996PLC029134"}}, key="k", revalidate=True
        )
        assert rows == []

    def test_bad_shape_rejected(self, monkeypatch):
        monkeypatch.setattr(m, "_ogd", lambda field, value, key, limit="5": [])
        assert m.validate_web_candidates({"X": {"cin": "INE848H01015"}}, key="k") == []


class TestAppendManual:
    def test_dedupe_against_existing(self, tmp_path, monkeypatch):
        manual = tmp_path / "mca_cin_manual.csv"
        manual.write_text(
            "entity_name,cin,mca_name,status,class,pba,state,via,query\n"
            "Astrel, L1, M1, Active, Public, NA, S, manual, q\n".replace(", ", ",")
        )
        monkeypatch.setattr(m, "MANUAL_PATH", manual)
        n = m.append_manual(
            [{"entity": "Astral Pipes", "cin": "L25200GJ1996PLC029134"}], apply=True
        )
        assert n == 1
        n2 = m.append_manual([{"entity": "dup", "cin": "L25200GJ1996PLC029134"}], apply=True)
        assert n2 == 0
        rows = list(csv.DictReader(manual.open()))
        assert len(rows) == 2


class TestPick:
    def test_single_active(self):
        assert m._pick([{"cin": "L1", "company_status": "Active"}])["cin"] == "L1"

    def test_multi_active_is_miss(self):
        assert (
            m._pick(
                [
                    {"cin": "L1", "company_status": "Active"},
                    {"cin": "L2", "company_status": "Active"},
                ]
            )
            is None
        )
