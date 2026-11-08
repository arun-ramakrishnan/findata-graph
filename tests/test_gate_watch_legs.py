#!/usr/bin/env python3
"""Unit tests for the 2026-11-08 gate watchers (advisory legs).

- tests/hermeticity_watch.py — live-path classification, tier split,
  donor allowance, per-test attribution.
- helpers/validators/gate_timing_watch.py — drift verdict over a seeded
  gate_runs.duckdb-shaped history.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path


from helpers.validators import gate_timing_watch
from tests import hermeticity_watch as hw


# --------------------------------------------------------------------------- #
# hermeticity_watch: classification
# --------------------------------------------------------------------------- #
class TestLivePathClassification:
    def test_live_store_paths_flagged(self):
        assert hw.is_live_path(hw.MEMORY_ROOT / "research.db")
        assert hw.is_live_path(hw.MEMORY_ROOT / "convo_search.duckdb")
        assert hw.is_live_path(str(hw.MEMORY_ROOT / "embed_store.db"))
        # relative paths resolve against CWD — pytest always runs from the
        # repo root, so pin the contract under that precondition
        if Path.cwd() == hw.REPO_ROOT:
            assert hw.is_live_path("memory/research.db")

    def test_test_owned_caches_exempt(self):
        assert not hw.is_live_path(hw.MEMORY_ROOT / "graph.xdist-shared.duckdb")
        assert not hw.is_live_path(hw.MEMORY_ROOT / "graph.duckdb.build.lock")

    def test_non_memory_paths_rejected_cheaply(self):
        assert not hw.is_live_path(str(Path(tempfile.gettempdir()) / "pytest-of-x" / "research.db"))
        assert not hw.is_live_path(Path("/home/x/.dsh/sessions/session.v4.jsonl.zstd"))
        assert not hw.is_live_path(None)
        assert not hw.is_live_path(42)

    def test_missing_path_resolves_by_prefix(self):
        # a not-yet-existing live store path (first-connect creates it) still classifies
        assert hw.is_live_path(hw.MEMORY_ROOT / "some_new_store.duckdb")


class TestRecorder:
    def test_write_connect_is_tier_a_read_is_tier_b(self, monkeypatch, tmp_path):
        monkeypatch.setattr(hw, "_touches", [])
        monkeypatch.setattr(hw._state, "node", "tests/test_x.py::test_y", raising=False)
        hw.classify_connect("sqlite3", hw.MEMORY_ROOT / "research.db", {})
        hw.classify_connect("duckdb", hw.MEMORY_ROOT / "convo_search.duckdb", {"read_only": True})
        tiers = [t["tier"] for t in hw._touches]
        assert tiers == ["A", "B"]

    def test_unattributed_and_allowed_touches_dropped(self, monkeypatch):
        monkeypatch.setattr(hw, "_touches", [])
        monkeypatch.setattr(hw._state, "node", None, raising=False)
        hw.classify_connect("sqlite3", hw.MEMORY_ROOT / "research.db", {})
        monkeypatch.setattr(hw._state, "node", "tests/test_x.py::test_y", raising=False)
        hw.allow()
        hw.classify_connect("sqlite3", hw.MEMORY_ROOT / "research.db", {})
        hw.disallow()
        assert hw._touches == []

    def test_report_written_atomically(self, monkeypatch, tmp_path):
        monkeypatch.setattr(hw, "REPORT_DIR", tmp_path / "hermeticity")
        monkeypatch.setattr(
            hw, "_touches", [{"node": "n", "tier": "B", "kind": "open", "path": "p"}]
        )
        out = hw.write_report(config=None)
        assert out is not None and out.exists()
        payload = json.loads(out.read_text())
        assert payload["n_touches"] == 1 and payload["touches"][0]["tier"] == "B"
        assert not out.with_suffix(".json.part").exists()


class TestValidatorLeg:
    def test_fire_on_tier_a(self, tmp_path, capsys):
        rec = tmp_path / "gw0.json"
        rec.write_text(
            json.dumps(
                {
                    "worker": "gw0",
                    "touches": [
                        {
                            "node": "tests/test_chain.py::test_chain",
                            "tier": "A",
                            "kind": "duckdb:connect",
                            "path": str(hw.MEMORY_ROOT / "convo_search.duckdb"),
                        }
                    ],
                }
            )
        )
        from helpers.validators import hermeticity_watch as leg

        assert leg.main(report_dir=tmp_path) == 1
        assert "FIRE" in capsys.readouterr().out

    def test_clean_when_only_reads(self, tmp_path, capsys):
        rec = tmp_path / "gw0.json"
        rec.write_text(json.dumps({"worker": "gw0", "touches": []}))
        from helpers.validators import hermeticity_watch as leg

        assert leg.main(report_dir=tmp_path) == 0
        assert "OK" in capsys.readouterr().out

    def test_skip_when_no_recordings(self, tmp_path, capsys):
        from helpers.validators import hermeticity_watch as leg

        assert leg.main(report_dir=tmp_path) == 0
        assert "SKIP" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# gate_timing_watch: drift verdict
# --------------------------------------------------------------------------- #
def _seed_history(db: Path, series: dict[str, list[float]]) -> None:
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE runs (run_id INTEGER PRIMARY KEY, started_at TIMESTAMP, gate TEXT)")
    con.execute("CREATE TABLE legs (run_id INTEGER, leg TEXT, seconds REAL, status TEXT)")
    import datetime as dt

    base = dt.datetime(2026, 11, 8, 12, 0, 0)
    rid = 1
    for leg, vals in series.items():
        # newest first in vals; started_at descends with index
        for i, v in enumerate(vals):
            started = base - dt.timedelta(hours=i)
            con.execute("INSERT INTO runs VALUES (?, ?, 'qa')", (rid, started))
            con.execute("INSERT INTO legs VALUES (?, ?, ?, 'ok')", (rid, leg, v))
            rid += 1
    con.commit()
    con.close()


class TestGateTimingWatch:
    def test_fires_on_drift(self, tmp_path, capsys):
        db = tmp_path / "gate_runs.duckdb"
        _seed_history(db, {"pytest": [500, 490, 480, 470, 460, *([310.0] * 10)]})
        rc = gate_timing_watch.main_with_db(db)
        assert rc == 1
        assert "REGRESSED" in capsys.readouterr().out

    def test_quiet_when_stable(self, tmp_path, capsys):
        db = tmp_path / "gate_runs.duckdb"
        _seed_history(db, {"pytest": [310.0] * 15})
        assert gate_timing_watch.main_with_db(db) == 0
        assert "OK" in capsys.readouterr().out

    def test_sub_second_floor_guards_noise(self, tmp_path):
        db = tmp_path / "gate_runs.duckdb"
        _seed_history(db, {"lint": [0.2] * 5 + [0.05] * 10})  # 4x ratio, 0.15s delta
        assert gate_timing_watch.main_with_db(db) == 0

    def test_insufficient_history_skips_verdict(self, tmp_path):
        db = tmp_path / "gate_runs.duckdb"
        _seed_history(db, {"new_leg": [500.0, 500.0, 500.0]})
        assert gate_timing_watch.main_with_db(db) == 0

    def test_missing_db_skips(self, tmp_path, capsys):
        assert gate_timing_watch.main_with_db(tmp_path / "absent.duckdb") == 0
        assert "SKIP" in capsys.readouterr().out
