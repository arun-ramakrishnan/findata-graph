#!/usr/bin/env python3
"""S7 triage_pending_quotes tests — bucketing, decisions round-trip,
alias application with target validation, and worklist lifecycle."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from helpers.graph import triage_pending_quotes as tpq  # noqa: E402


class TestBucket:
    def test_alias_candidate_with_suggestions(self):
        assert tpq.bucket("Anything", ["Some Entity"]) == "alias_candidate"

    def test_garbage_shapes(self):
        for c in (
            "Voltas I",
            "InfoEdge (Naukri)]",
            "NETGEAR,",
            "Jubilant Foodworks \\",
            "Mold-Tek Packaging_",
        ):
            assert tpq.bucket(c, []) == "garbage_shape", c

    def test_stub_candidate_clean_names(self):
        for c in (
            "Premier Energies",
            "Ambuja Cements",
            "HAL",
            "Apple",
            "Poonawala Poonawalla Fincorp",
        ):
            assert tpq.bucket(c, []) == "stub_candidate", c


class TestDecisionsRoundTrip:
    def test_build_only_open_sorted(self, tmp_path, monkeypatch):
        wl = {
            "entries": {
                "B Open": {"status": "open", "suggestions": ["B Entity"], "notes": []},
                "A Garbage": {"status": "open", "suggestions": [], "notes": []},
                "C Resolved": {"status": "resolved", "suggestions": [], "notes": []},
            }
        }
        rows = tpq.build_decisions(wl)
        assert [r["canonical"] for r in rows] == ["A Garbage", "B Open"]
        assert rows[0]["bucket"] == "stub_candidate"
        assert rows[1]["bucket"] == "alias_candidate"
        assert all(r["decision"] == "" for r in rows)

    def test_write_load_round_trip(self, tmp_path, monkeypatch):
        rows = [
            {
                "id": "x",
                "canonical": "A",
                "bucket": "stub_candidate",
                "suggestions": [],
                "notes": [],
                "decision": "",
            }
        ]
        monkeypatch.setattr(tpq, "DECISIONS", tmp_path / "d.jsonl")
        tpq.write_decisions(rows)
        assert tpq.load_decisions() == rows


class TestApplyDecisions:
    @staticmethod
    def _setup(tmp_path, monkeypatch, entities):
        db = tmp_path / "t.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE entities (name TEXT, normalized_name TEXT, entity_type TEXT)")
        for e in entities:
            c.execute(
                "INSERT INTO entities (name, normalized_name, entity_type) VALUES (?, ?, 'company')",
                (e, e),
            )
        c.commit()
        c.close()

        def _connect():
            cc = sqlite3.connect(db)
            cc.row_factory = sqlite3.Row
            return cc

        monkeypatch.setattr(tpq, "connect", _connect)
        monkeypatch.setattr(tpq, "WORKLIST", tmp_path / "wl.json")
        monkeypatch.setattr(tpq, "ALIASES", tmp_path / "a.json")
        monkeypatch.setattr(tpq, "DECISIONS", tmp_path / "d.jsonl")
        wl = {
            "entries": {
                "Exide": {"status": "open", "suggestions": ["Exide Industries"], "notes": []},
                "Premier Energies": {"status": "open", "suggestions": [], "notes": []},
                "Junk ]": {"status": "open", "suggestions": [], "notes": []},
            }
        }
        tmp_path.joinpath("wl.json").write_text(json.dumps(wl))
        tpq.write_decisions(tpq.build_decisions(wl))  # report runs first

    def test_apply_aliases_stubs_and_lifecycle(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries", "Premier Energies"])
        rows = tpq.load_decisions()
        by_c = {r["canonical"]: r for r in rows}
        by_c["Exide"]["decision"] = "alias:Exide Industries"
        by_c["Junk ]"]["decision"] = "discard"
        # Premier Energies left undecided (stub flow pending)
        tpq.write_decisions(rows)
        assert tpq.cmd_apply() == 0
        aliases = json.loads(tpq.ALIASES.read_text())
        assert aliases["exide"] == "Exide Industries"
        wl = json.loads(tpq.WORKLIST.read_text())["entries"]
        assert wl["Exide"]["status"] == "decided"
        assert wl["Junk ]"]["status"] == "decided"
        assert wl["Premier Energies"]["status"] == "open"  # undecided stays

    def test_alias_target_validation_rejects(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries"])
        rows = tpq.load_decisions()
        rows[0]["decision"] = "alias:Nonexistent Ltd"
        tpq.write_decisions(rows)
        assert tpq.cmd_apply() == 1
        assert not tpq.ALIASES.exists()  # nothing applied on validation failure
