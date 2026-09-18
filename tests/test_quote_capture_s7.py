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


class TestReviewSitting:
    """review-kit S3: journaled sitting -> tool-written quote decisions."""

    @staticmethod
    def _setup(tmp_path, monkeypatch, entities):
        TestApplyDecisions._setup(tmp_path, monkeypatch, entities)
        return tmp_path

    def _review(self, answers, tmp_path, apply=True, **kw):
        it = iter(answers)
        out = []
        rep = tpq.review(
            input_fn=lambda _p: next(it),
            print_fn=out.append,
            apply=apply,
            journal_dir=tmp_path,
            **kw,
        )
        return rep, out

    def _appended(self):
        # rows the sitting appended sit AFTER the report-generated rows
        return [json.loads(x) for x in tpq.DECISIONS.read_text().splitlines() if x.strip()][3:]

    def test_suggestion_pick_appends_row_the_applier_consumes(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries", "Premier Energies"])
        # bucket order: alias_candidate (Exide) first
        rep, _ = self._review(["1", "d", "st", "y"], tmp_path)
        assert rep["approved"] == 3 and rep["applied"] == 1
        rows = self._appended()
        assert [r["decision"] for r in rows] == [
            "alias:Exide Industries",
            "discard",
            "stub",
        ]
        # the sitting's output must satisfy the EXISTING apply lane end-to-end
        assert tpq.cmd_apply() == 0
        aliases = json.loads(tpq.ALIASES.read_text())
        assert aliases["exide"] == "Exide Industries"
        wl = json.loads(tpq.WORKLIST.read_text())["entries"]
        assert wl["Exide"]["status"] == "decided"

    def test_alias_validates_and_reasks(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries"])
        # 'Exide Industry' is a substring of the entity — single candidate
        # auto-matches (no re-ask); a truly unmatched name re-asks.
        rep, out = self._review(["al Zzz NoMatch", "al Exide Industry", "y"], tmp_path, limit=1)
        assert rep["approved"] == 1
        rows = self._appended()
        assert rows[0]["decision"] == "alias:Exide Industries"
        assert any("matched: Exide Industries" in ln for ln in out)
        assert any("no entity matches" in ln for ln in out)

    def test_stub_with_cin_parses_now(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries", "Premier Energies"])
        rep, out = self._review(
            ["d", "sc BAD-CIN", "sc L01631KA2010PTC096843", "y"], tmp_path, limit=2
        )
        # bucket order: Exide (alias) -> discard; Junk ] (garbage) -> sc
        assert rep["approved"] == 2
        rows = self._appended()
        assert rows[0]["decision"] == "discard"
        assert rows[1]["decision"] == "stub|cin=L01631KA2010PTC096843"
        assert any("cin" in ln.lower() for ln in out)

    def test_park_and_reopen(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries"])
        exide_id = tpq._entry_id("Exide")
        rep, _ = self._review(["p"], tmp_path, limit=1)
        assert rep["approved"] == 0
        jlines = [json.loads(x) for x in Path(rep["journal"]).read_text().splitlines()]
        assert any(d.get("id") == exide_id and d["action"] == "skip" for d in jlines)
        # next sitting (same limit) must NOT re-ask Exide — it walks the
        # next open canonical (Junk ], garbage bucket ranks below stub)
        out = []
        it = iter(["d", "y"])
        rep2 = tpq.review(
            input_fn=lambda _p: next(it),
            print_fn=out.append,
            apply=True,
            journal_dir=tmp_path,
            limit=1,
        )
        assert rep2["approved"] == 1
        assert not any(exide_id in ln for ln in out)
        rows = self._appended()
        assert rows[0]["canonical"] == "Junk ]"
        # --skipped re-opens the parked canonical
        rep3, _ = self._review(["d", "y"], tmp_path, limit=1, skipped=True)
        assert rep3["approved"] == 1
        assert self._appended()[-1]["canonical"] == "Exide"

    def test_annotated_rows_excluded_till_redecide(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries"])
        rep, _ = self._review(["1", "y"], tmp_path, limit=1)
        assert rep["approved"] == 1
        rep2, _ = self._review(["d", "st", "y"], tmp_path, limit=2)
        assert rep2["approved"] == 2  # only the remaining open canonicals
        rep3, _ = self._review(["d", "y"], tmp_path, limit=1, redecide=True)
        assert rep3["approved"] == 1  # redecide re-walked the annotated row

    def test_dry_run_and_cli_wiring(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries"])
        rep, _ = self._review(["1", "y"], tmp_path, apply=False, limit=1)
        assert rep["approved"] == 1 and rep["applied"] == 0
        assert len(self._appended()) == 0
        calls = []
        monkeypatch.setattr(tpq, "review", lambda **kw: calls.append(kw) or {"approved": 0})
        assert tpq.main(["--review", "--limit", "2", "--dry-run"]) == 0
        assert calls == [{"limit": 2, "redecide": False, "skipped": False, "apply": False}]

    def test_alias_db_lookup_acronym_and_pick(self, tmp_path, monkeypatch):
        # fixture entities: Exide Industries / Premier Energies
        self._setup(tmp_path, monkeypatch, ["Exide Industries", "Premier Energies"])
        rep, out = self._review(["al exide ind", "y"], tmp_path, limit=1)
        rows = self._appended()
        assert rows[0]["decision"] == "alias:Exide Industries"
        assert any("matched: Exide Industries" in ln for ln in out)

    def test_alias_db_lookup_numbered_pick(self, tmp_path, monkeypatch):
        # two companies share the token -> numbered candidates -> pick 2
        self._setup(
            tmp_path,
            monkeypatch,
            ["Exide Industries", "Exide Industries Solutions"],
        )
        rep, out = self._review(["al exide", "2", "y"], tmp_path, limit=1)
        rows = self._appended()
        assert rows[0]["decision"] == "alias:Exide Industries Solutions"
        assert any("candidates:" in ln for ln in out)

    def test_quit_and_abort(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["Exide Industries"])
        rep, _ = self._review(["q"], tmp_path, limit=1)
        assert rep["approved"] == 0
        rep2, _ = self._review(["x"], tmp_path, limit=1)
        assert rep2["approved"] == 0
        jlines = Path(rep2["journal"]).read_text().splitlines()
        assert json.loads(jlines[-1])["action"] == "sitting-end"
