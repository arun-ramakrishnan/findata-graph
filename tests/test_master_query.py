"""
Tests for helpers/misc/master_query.py — the federated master search CLI.
Core logic (leg parsing, parallel fan-out, rank-RRF flat order, grouped
render, JSON shape) via an injected fake lane runner; one integration leg
exercises the REAL memory lane against a tmp sidecar (mirrors
tests/test_rebuild_memory_search.py).
"""

import json
from dataclasses import replace as dc_replace

import pytest


from helpers.misc import master_query as mq  # noqa: E402
from helpers.misc.search_tui import Hit  # noqa: E402
from helpers.maintenance import rebuild_memory_search as rms  # noqa: E402

pytestmark = [pytest.mark.integration]

_H1 = Hit(
    path="doc/a.md", line=3, title="A", section="s1", snippet="alpha match", score=0.5, lane="docs"
)
_H2 = Hit(
    path="helpers/x.py",
    line=None,
    title="x",
    section="",
    snippet="beta match",
    score=0.4,
    lane="scripts",
)
_H3 = Hit(
    path="~/m/memory.md",
    line=None,
    title="m",
    section="zcode",
    snippet="alpha too",
    score=0.3,
    lane="memory",
    kind="zcode",
)


def _fake_runner(lane, query, limit, mode="hybrid"):
    """Two legs with hits, one honest-empty, one degraded. Signature
    matches st.run_lane (lane first) — fan_out forwards positionally."""
    table = {
        "docs": ([_H1], f"{1} hits · doc_search {mode}"),
        "notes": ([], "0 hits · note_search hybrid"),
        "scripts": ([_H2], f"{1} hits · script_search {mode}"),
        "memory": ([_H3], f"{1} hits · memory_search {mode}"),
        "convo": ([], "convo_search index missing/stale — make convo-fresh APPLY=1"),
        "reports": ([_H1], f"{1} hits · reports {mode}"),
    }
    if lane not in table:
        return [], f"unknown lane {lane!r}"
    hits, status = table[lane]
    return hits[:limit], status


@pytest.fixture
def fake_lanes(monkeypatch):
    """CLI tests inject at the seam fan_out resolves (st.run_lane at call
    time) — otherwise main() would query the LIVE tree indexes."""
    monkeypatch.setattr(mq.st, "run_lane", _fake_runner)


class TestParseLegs:
    def test_default_six(self):
        assert mq.parse_legs("docs,notes,scripts,memory,convo,gates") == list(mq.DEFAULT_LEGS)

    def test_all_expands_incl_stateless(self):
        legs = mq.parse_legs("all")
        assert legs == list(mq.ALL_LEGS)
        assert "code" in legs and "literal" in legs

    def test_dedupe_and_order_preserved(self):
        assert mq.parse_legs("memory,docs,memory") == ["memory", "docs"]

    def test_reports_alias_accepted(self):
        assert mq.parse_legs("reports") == ["reports"]

    def test_unknown_leg_raises(self):
        with pytest.raises(ValueError, match="unknown leg 'nope'"):
            mq.parse_legs("docs,nope")


class TestFanOut:
    def test_all_legs_run_and_degrade_independently(self):
        out = mq.fan_out("q", ["docs", "convo"], 4, lane_runner=_fake_runner, parallel=False)
        assert out["docs"][0] == [_H1]
        assert "missing" in out["convo"][1]

    def test_raising_leg_degrades_to_error(self):
        def boom(query, lane, limit, mode="hybrid"):
            raise RuntimeError("disk on fire")

        out = mq.fan_out("q", ["docs"], 4, lane_runner=boom, parallel=False)
        assert out["docs"] == ([], "error: RuntimeError: disk on fire")

    def test_parallel_equals_serial(self):
        legs = ["docs", "scripts", "memory"]
        par = mq.fan_out("q", legs, 4, lane_runner=_fake_runner)
        ser = mq.fan_out("q", legs, 4, lane_runner=_fake_runner, parallel=False)
        assert {k: (v[0], v[1]) for k, v in par.items()} == ser


class TestRrfFlat:
    def test_rank_order_and_leg_tags(self):
        per_leg = mq.fan_out(
            "q", ["docs", "scripts", "memory"], 4, lane_runner=_fake_runner, parallel=False
        )
        flat = mq.rrf_flat(per_leg, 3)
        # rank 1 in every leg -> same RRF; ties break by canonical leg
        # order (docs < scripts < memory)
        assert [f["leg"] for f in flat] == ["docs", "scripts", "memory"]
        assert flat[0]["rrf"] == pytest.approx(1.0 / (mq.RRF_K + 1), abs=1e-6)
        assert flat[0]["path"] == "doc/a.md"

    def test_leg2_rank1_ties_with_leg1_rank2_above_leg1_rank3(self):
        # 1/(K+2) [docs rank2] < 1/(K+1+1)... : memory rank-1 (1/62) beats
        # docs rank-2 (1/63) — a top rank in one leg outranks the second
        # slot in another, by design of rank-RRF.
        hits1 = [_H1, dc_replace(_H1, path="b.md"), dc_replace(_H1, path="c.md")]
        per_leg = {"docs": (hits1, "3 hits"), "memory": ([_H3], "1 hits")}
        flat = mq.rrf_flat(per_leg, 4)
        assert flat[0]["path"] == "doc/a.md"  # 1/61
        assert flat[1]["leg"] == "memory"  # 1/62 beats 1/63
        assert flat[2]["path"] == "b.md"  # 1/63
        assert flat[3]["path"] == "c.md"  # 1/64

    def test_reports_alias_tiebreaks_as_gates(self):
        per_leg = {"reports": ([_H1], "1 hits"), "convo": ([_H2], "1 hits")}
        flat = mq.rrf_flat(per_leg, 2)
        assert [f["leg"] for f in flat] == ["convo", "reports"]


class TestCli:
    def test_json_shape_and_exit(self, fake_lanes, capsys):
        rc = mq.main(["alpha", "--legs", "docs,convo", "--json"])
        assert rc == 0  # docs answered even though convo degraded
        payload = json.loads(capsys.readouterr().out)
        assert payload["legs"]["docs"]["count"] == 1
        assert payload["legs"]["docs"]["results"][0]["path"] == "doc/a.md"
        assert "missing" in payload["legs"]["convo"]["status"]
        assert "flat" not in payload

    def test_json_flat_included(self, fake_lanes, capsys):
        mq.main(["alpha", "--legs", "docs,memory", "--flat", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert {f["leg"] for f in payload["flat"]} == {"docs", "memory"}

    def test_all_legs_degraded_exit_1(self, fake_lanes, capsys):
        rc = mq.main(["alpha", "--legs", "convo", "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["legs"]["convo"]["count"] == 0

    def test_unknown_leg_exit_2(self, capsys):
        assert mq.main(["alpha", "--legs", "nope"]) == 2
        assert "unknown leg" in capsys.readouterr().err

    def test_grouped_render(self, fake_lanes, capsys):
        rc = mq.main(["alpha", "--legs", "docs,notes", "--serial"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "## docs" in out and "## notes" in out
        assert "index ages:" in out
        assert "doc/a.md" in out


class TestAgeGuard:
    """S2: --age-guard skips legs whose sidecar age exceeds the threshold;
    gates/code/literal have no guarded sidecar and always run."""

    @pytest.fixture
    def aged_legs(self, monkeypatch):
        """docs+notes read 100h old; scripts reads fresh; memory reads
        missing (None — the file-gone case degrades via its own status)."""
        ages = {"docs": 100.0, "notes": 100.0, "scripts": 0.5, "memory": None}
        monkeypatch.setattr(mq, "_leg_age_hours", lambda leg, root=None: ages.get(leg))

    def test_aged_leg_skipped_without_running(self, aged_legs, monkeypatch):
        calls = []

        def spy(lane, query, limit, mode="hybrid"):
            calls.append(lane)
            return [_H1], "1 hits · doc_search hybrid"

        out = mq.fan_out(
            "q", ["docs", "scripts"], 4, lane_runner=spy, parallel=False, age_guard_hours=24.0
        )
        assert "docs" in out and "skipped: index 100.0h old" in out["docs"][1]
        assert "make search-fresh APPLY=1" in out["docs"][1]
        assert out["docs"][0] == []
        assert calls == ["scripts"]  # the aged backend never ran

    def test_missing_sidecar_age_passes_through(self, aged_legs):
        def spy(lane, query, limit, mode="hybrid"):
            return [_H3], "1 hits · memory_search hybrid"

        out = mq.fan_out("q", ["memory"], 4, lane_runner=spy, parallel=False, age_guard_hours=24.0)
        assert out["memory"][0] == [_H3]  # None age -> run, own degradation path

    def test_convo_refresh_hint_is_convo_fresh(self, monkeypatch):
        monkeypatch.setattr(mq, "_leg_age_hours", lambda leg, root=None: 50.0)
        out = mq.fan_out(
            "q", ["convo"], 4, lane_runner=_fake_runner, parallel=False, age_guard_hours=24.0
        )
        assert "make convo-fresh APPLY=1" in out["convo"][1]

    def test_gates_exempt_from_guard(self, monkeypatch):
        # realistic probe: guarded legs read ancient, unguarded legs read None
        monkeypatch.setattr(
            mq,
            "_leg_age_hours",
            lambda leg, root=None: 999.0 if leg in mq._SIDECAR_BY_LEG else None,
        )
        out = mq.fan_out(
            "q", ["gates"], 4, lane_runner=_fake_runner, parallel=False, age_guard_hours=1.0
        )
        # gates has no guarded sidecar -> never aged out; the backend ran
        assert out["gates"] == ([_H1], "1 hits · reports hybrid")

    def test_probe_contract(self):
        """The real probe: guarded legs map to their sidecar, gates and
        the stateless tools return None (never age-guardable)."""
        assert mq._leg_age_hours("gates") is None
        assert mq._leg_age_hours("code") is None
        assert mq._leg_age_hours("literal") is None
        assert mq._leg_age_hours("memory") is not None
        assert mq._leg_age_hours("docs") is not None

    def test_guard_off_by_default(self):
        out = mq.fan_out("q", ["docs"], 4, lane_runner=_fake_runner, parallel=False)
        assert out["docs"][0] == [_H1]

    def test_cli_all_skipped_exit_1(self, aged_legs, capsys):
        rc = mq.main(["alpha", "--legs", "docs,notes", "--age-guard", "24", "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["age_guard_hours"] == 24.0
        assert all(leg["skipped"] for leg in payload["legs"].values())
        assert "make search-fresh APPLY=1" in payload["legs"]["docs"]["status"]

    def test_cli_mixed_skipped_still_exit_0(self, aged_legs, fake_lanes, capsys):
        rc = mq.main(["alpha", "--legs", "docs,scripts", "--age-guard", "24", "--json"])
        assert rc == 0  # scripts answered
        payload = json.loads(capsys.readouterr().out)
        assert payload["legs"]["docs"]["skipped"] is True
        assert payload["legs"]["scripts"]["skipped"] is False
        assert payload["legs"]["scripts"]["count"] == 1

    def test_cli_default_guard_value(self, aged_legs, capsys):
        mq.main(["alpha", "--legs", "docs", "--age-guard", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["age_guard_hours"] == mq.DEFAULT_AGE_GUARD_HOURS
        assert payload["legs"]["docs"]["skipped"] is True


class TestInterpreterGuard:
    """The front-door env check: a bare `python` outside the repo venv
    (no python-dotenv) must exit 2 with ONE actionable line, not the
    six-leg error cascade (hit live 2026-10-05 via ~/.local/bin/python
    3.14)."""

    def test_broken_interpreter_exit_2(self, monkeypatch, capsys):
        def broken():
            raise ModuleNotFoundError("No module named 'dotenv'")

        monkeypatch.setattr(mq, "_import_dotenv", broken)
        rc = mq.main(["anything", "--legs", "docs"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "not the repo venv" in err
        assert ".venv/bin/python3" in err

    def test_healthy_interpreter_passes(self):
        # the suite always runs under the repo venv (make qa contract)
        assert mq.interpreter_problem() is None


class TestMemoryLaneReal:
    """The one real-backend leg: master -> search_tui._run_memory ->
    rebuild_memory_search.search_memories over a tmp sidecar."""

    @pytest.fixture
    def memory_sidecar(self, tmp_path, monkeypatch):
        mem = tmp_path / "projects" / "s" / "memory"
        mem.mkdir(parents=True)
        (mem / "doctrine.md").write_text(
            "---\nname: doctrine\ndescription: test doctrine\n---\n\nThe embed cache lives in the vec sidecar.\n"
        )
        db = tmp_path / "memory_search.db"
        rms.rebuild(
            db,
            embed_fn=lambda _t: [0.0] * 8,
            zcode_projects=tmp_path / "projects",
            prime_state=tmp_path / "absent.json",
            opencode_dir=tmp_path / "absent-oc",
        )
        monkeypatch.setattr(rms, "MEMORY_DB", db)
        return db

    def test_master_over_real_memory_leg(self, memory_sidecar, capsys):
        rc = mq.main(["embed cache", "--legs", "memory", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        leg = payload["legs"]["memory"]
        assert leg["count"] == 1
        assert leg["results"][0]["lane"] == "memory"
        assert leg["results"][0]["path"].endswith("doctrine.md")

    def test_tui_memory_lane_direct(self, memory_sidecar):
        from helpers.misc import search_tui as st

        hits, status = st.run_lane("memory", "embed cache", 5)
        assert "hits" in status
        assert hits and hits[0].lane == "memory"
