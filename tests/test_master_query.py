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

_H1 = Hit(path="doc/a.md", line=3, title="A", section="s1", snippet="alpha match", score=0.5, lane="docs")
_H2 = Hit(path="helpers/x.py", line=None, title="x", section="", snippet="beta match", score=0.4, lane="scripts")
_H3 = Hit(path="~/m/memory.md", line=None, title="m", section="zcode", snippet="alpha too", score=0.3, lane="memory", kind="zcode")


def _fake_runner(query, lane, limit, mode="hybrid"):
    """Two legs with hits, one honest-empty, one degraded."""
    table = {
        "docs": ([_H1], f"{1} hits · doc_search {mode}"),
        "notes": ([], "0 hits · note_search hybrid"),
        "scripts": ([_H2], f"{1} hits · script_search {mode}"),
        "memory": ([_H3], f"{1} hits · memory_search {mode}"),
        "convo": ([], "convo_search index missing/stale — make convo-fresh APPLY=1"),
    }
    if lane not in table:
        return [], f"unknown lane {lane!r}"
    hits, status = table[lane]
    return hits[:limit], status


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
        per_leg = mq.fan_out("q", ["docs", "scripts", "memory"], 4, lane_runner=_fake_runner, parallel=False)
        flat = mq.rrf_flat(per_leg, 3)
        # rank 1 in every leg -> same RRF; ties break by canonical leg
        # order (docs < scripts < memory)
        assert [f["leg"] for f in flat] == ["docs", "scripts", "memory"]
        assert flat[0]["rrf"] == pytest.approx(1.0 / (mq.RRF_K + 1), abs=1e-6)
        assert flat[0]["path"] == "doc/a.md"

    def test_leg2_rank1_ties_with_leg1_rank2_above_leg1_rank3(self):
        # 1/(K+2) [leg1 rank2] > 1/(K+3) [leg1 rank3] — rank beats leg order
        hits1 = [_H1, dc_replace(_H1, path="b.md"), dc_replace(_H1, path="c.md")]
        per_leg = {"docs": (hits1, "3 hits"), "memory": ([_H3], "1 hits")}
        flat = mq.rrf_flat(per_leg, 4)
        assert flat[0]["path"] == "doc/a.md"
        assert flat[1]["path"] == "b.md"  # docs rank 2
        assert flat[2]["leg"] == "memory"  # rank 1 elsewhere beats rank 3
        assert flat[3]["path"] == "c.md"

    def test_reports_alias_tiebreaks_as_gates(self):
        per_leg = {"reports": ([_H1], "1 hits"), "convo": ([_H2], "1 hits")}
        flat = mq.rrf_flat(per_leg, 2)
        assert [f["leg"] for f in flat] == ["convo", "reports"]


class TestCli:
    def test_json_shape_and_exit(self, capsys):
        rc = mq.main(["alpha", "--legs", "docs,convo", "--json"])
        assert rc == 0  # docs answered even though convo degraded
        payload = json.loads(capsys.readouterr().out)
        assert payload["legs"]["docs"]["count"] == 1
        assert payload["legs"]["docs"]["results"][0]["path"] == "doc/a.md"
        assert "missing" in payload["legs"]["convo"]["status"]
        assert "flat" not in payload

    def test_json_flat_included(self, capsys):
        mq.main(["alpha", "--legs", "docs,memory", "--flat", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert {f["leg"] for f in payload["flat"]} == {"docs", "memory"}

    def test_all_legs_degraded_exit_1(self, capsys):
        rc = mq.main(["alpha", "--legs", "convo", "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["legs"]["convo"]["count"] == 0

    def test_unknown_leg_exit_2(self, capsys):
        assert mq.main(["alpha", "--legs", "nope"]) == 2
        assert "unknown leg" in capsys.readouterr().err

    def test_grouped_render(self, capsys):
        rc = mq.main(["alpha", "--legs", "docs,notes", "--serial"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "## docs" in out and "## notes" in out
        assert "index ages:" in out
        assert "doc/a.md" in out


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
