"""Tests for helpers/analytics/bili_tier_nudge.py — the manual tier-ladder nudge.

The tool reimplements the plugin's rung selection from three offline sources:
the config file (the bars), the bili log (the plugin's own last verdict) and
the session store (the tier pools). These tests pin the arithmetic the plugin
does not expose: bar resolution, the pool semantics per tier, the strict
T1 -> T2 -> T3 ordering test, the growth and cadence gates, and the pressure
branch that bypasses both.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.analytics import bili_tier_nudge as btn  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _bars(t1=40000, t2=5000, t3=3000, floor=20000, growth=30000) -> btn.Bars:
    return btn.Bars(
        nudge_growth=growth,
        t1=t1,
        t2=t2,
        t3=t3,
        growth_floor=floor,
    )


def _verdict(**kw) -> btn.Verdict:
    base: dict[str, Any] = dict(
        ts="2026-10-19T18:00:00.000Z",
        usage_tok=100000,
        usage_pct=12.0,
        growth=25000,
        ref=83739,
        interval=30000,
        pending_t1=0,
        pending_thr=30000,
        model="glm-5.3-flash",
        reason="",
    )
    base.update(kw)
    return btn.Verdict(**base)


def _state(tier1=(), tier2=(), tier3=(), shown=None, kstate_tokens=0) -> btn.SessionState:
    st = btn.SessionState(session_id="s1")
    for tier, ids in ((1, tier1), (2, tier2), (3, tier3)):
        for i, bid in enumerate(ids):
            st.blocks.append(
                btn.Block(
                    block_id=bid,
                    tier=tier,
                    active=True,
                    topic=f"topic {bid}",
                    summary="x" * 400,  # 100 tokens
                    compressed_tokens=5000,
                    eff_ids=10,
                )
            )
    st.last_shown_by_tier = shown or {}
    st.kstate_tokens = kstate_tokens
    return st


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "index.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, kstate_json TEXT)")
    con.execute(
        "CREATE TABLE blocks ("
        "id INTEGER PRIMARY KEY, ord INTEGER, session_id TEXT, block_id TEXT, "
        "tier INTEGER, active INTEGER, payload_json TEXT)"
    )
    con.commit()
    con.close()
    return db


def _add_block(db, block_id, tier, summary_len=400, active=1, session="s1", shown=None):
    payload = {
        "blockId": block_id,
        "tier": tier,
        "topic": "topic",
        "summary": "x" * summary_len,
        "effectiveMessageIds": ["h_" + block_id] * 3,
        "compressedTokens": 5000,
    }
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO blocks (ord, session_id, block_id, tier, active, payload_json) "
        "VALUES (?,?,?,?,?,?)",
        (abs(hash(block_id)) % 1000, session, block_id, tier, active, json.dumps(payload)),
    )
    if shown is not None:
        con.execute(
            "INSERT OR REPLACE INTO sessions (session_id, kstate_json) VALUES (?,?)",
            (
                session,
                json.dumps({"nudge": {"lastShownByTier": shown, "lastPerMessageNudgeTokens": 0}}),
            ),
        )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# bars
# ---------------------------------------------------------------------------


def test_bars_explicit_tier_tokens_win(tmp_path):
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(
        json.dumps(
            {
                "compress": {
                    "nudgeGrowthTokens": 30000,
                    "tierNudgeTokens": {"t1": 40000, "t2": 5000, "t3": 3000},
                }
            }
        )
    )
    bars = btn.Bars.from_file(cfg)
    assert (bars.t1, bars.t2, bars.t3) == (40000, 5000, 3000)
    assert bars.nudge_growth == 30000
    assert bars.source["t1"] == "file"


def test_bars_t1_falls_back_to_nudge_growth(tmp_path):
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(
        json.dumps({"compress": {"nudgeGrowthTokens": 30000, "tierNudgeTokens": {"t2": 5000}}})
    )
    bars = btn.Bars.from_file(cfg)
    assert bars.t1 == 30000
    assert bars.t2 == 5000


def test_bars_growth_floor_is_hard_at_20000(tmp_path):
    """minGrowthFloor 2e4 is not settable; 0.45 * nudgeGrowthTokens only
    raises the floor above 44,444."""
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 30000}}))
    assert btn.Bars.from_file(cfg).growth_floor == 20000

    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 40000}}))
    assert btn.Bars.from_file(cfg).growth_floor == 20000

    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 50000}}))
    assert btn.Bars.from_file(cfg).growth_floor == 22500


def test_bars_missing_config_file_falls_back(tmp_path):
    """The plugin's own defaults: nudgeGrowthTokens 50,000, so t1 50,000 and
    the floor 0.45 x 50,000 = 22,500."""
    bars = btn.Bars.from_file(tmp_path / "nope.json")
    assert bars.nudge_growth == 50000
    assert bars.t1 == 50000
    assert (bars.t2, bars.t3) == (75000, 75000)
    assert bars.growth_floor == 22500
    assert bars.source["t1"] == "fallback nudgeGrowthTokens"


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------


def test_t1_fires_when_the_bar_is_crossed():
    st = _state()
    dec = btn.decide(_bars(), st, _verdict(pending_t1=41387), 868928)
    assert dec.tier == 1
    assert dec.t1_eff == 41387


def test_t1_shadows_t2():
    """Strict else-if: if T1 is over its bar, T2 is never considered."""
    st = _state(tier1=[f"b{i}" for i in range(6)])  # 600 tokens of summaries
    dec = btn.decide(_bars(), st, _verdict(pending_t1=45000), 868928)
    assert dec.tier == 1


def test_t2_needs_the_pool_over_the_bar():
    st = _state(tier1=[f"b{i}" for i in range(6)])  # 600 tokens < bar 5000
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0), 868928)
    assert dec.tier is None
    assert any("T2 size" in g for g in dec.gates)


def test_t2_fires_when_tier1_summaries_cross_the_bar():
    st = _state(tier1=[f"b{i}" for i in range(60)])  # 6000 tokens
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0), 868928)
    assert dec.tier == 2
    assert dec.t2_pen == 6000


def test_t2_is_blocked_by_the_ordering_test():
    """The stranded band: t2Pen over its bar but BELOW t1Eff."""
    st = _state(tier1=[f"b{i}" for i in range(60)])  # t2Pen 6000
    dec = btn.decide(_bars(), st, _verdict(pending_t1=15000), 868928)
    assert dec.tier is None
    assert dec.t2_pen >= 5000


def test_t3_fires_when_the_tier2_pool_beats_the_others():
    st = _state(tier2=[f"c{i}" for i in range(40)])  # 4000 tokens
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0), 868928)
    assert dec.tier == 3
    assert dec.t3_pen == 4000


def test_t2_outranks_t3_when_both_are_armed():
    st = _state(tier1=[f"b{i}" for i in range(60)], tier2=["c0"])
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0), 868928)
    assert dec.tier == 2


def test_growth_floor_blocks_the_rung():
    st = _state(tier1=[f"b{i}" for i in range(60)])
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0, growth=14220), 868928)
    assert dec.tier is None
    assert dec.blocked_by == "growth floor"


def test_cadence_gate_blocks_the_rung():
    """`tokenCount - lastShownByTier[t] >= growthFloor`."""
    st = _state(tier1=[f"b{i}" for i in range(60)], shown={"2": 90000})
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0, usage_tok=100000), 868928)
    assert dec.tier is None
    assert any("cadence T2" in g for g in dec.gates)


def test_pressure_branch_picks_the_largest_pool_ignoring_the_ordering():
    """At 75% usage the plugin ignores the growth floor and the ordering, and
    picks whichever pool is largest."""
    st = _state(tier1=[f"b{i}" for i in range(60)])  # t2Pen 6000
    dec = btn.decide(
        _bars(),
        st,
        _verdict(pending_t1=100, growth=0, usage_tok=130000, usage_pct=75.0),
        168000,
    )
    assert dec.pressure is True
    assert dec.tier == 2


def test_pressure_branch_needs_the_minimum_benefit():
    st = _state(tier1=["b0"])  # 100 tokens, benefit floor is 5000
    dec = btn.decide(
        _bars(),
        st,
        _verdict(pending_t1=0, growth=0, usage_tok=160000, usage_pct=95.0),
        168000,
    )
    assert dec.pressure is True
    assert dec.tier is None
    assert any("minPressureBenefit" in g for g in dec.gates)


def test_no_verdict_means_no_decision():
    dec = btn.decide(_bars(), _state(), None, 868928)
    assert dec.tier is None
    assert dec.blocked_by == "no verdict"


# ---------------------------------------------------------------------------
# the session store
# ---------------------------------------------------------------------------


def test_load_session_state_reads_tiers_and_skips_inactive(tmp_path):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    _add_block(db, "b2", 2)
    _add_block(db, "b3", 1, active=0)
    st = btn.load_session_state(db, "s1")
    assert [b.block_id for b in st.by_tier(1)] == ["b1"]
    assert [b.block_id for b in st.by_tier(2)] == ["b2"]


def test_pool_is_the_sum_of_summary_tokens_of_the_lower_tier(tmp_path):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1, summary_len=400)
    _add_block(db, "b2", 1, summary_len=400)
    _add_block(db, "c1", 2, summary_len=400)
    st = btn.load_session_state(db, "s1")
    assert st.pool(1) == 200  # two blocks x 100 tokens
    assert st.pool(2) == 100
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0), 868928)
    assert dec.t2_pen == 200
    assert dec.t3_pen == 100


def test_load_session_state_reads_the_cadence_state(tmp_path):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1, shown={"2": 90000, "1": 10})
    st = btn.load_session_state(db, "s1")
    assert st.last_shown_by_tier == {"2": 90000, "1": 10}


def test_missing_db_is_not_fatal(tmp_path):
    st = btn.load_session_state(tmp_path / "nope.db", "s1")
    assert st.blocks == []
    assert st.pool(1) == 0


# ---------------------------------------------------------------------------
# nudge rendering
# ---------------------------------------------------------------------------


def test_render_nudge_t2_uses_block_endpoints():
    st = _state(tier1=[f"b{i}" for i in range(6)])
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0), 868928)
    text = btn.render_nudge(2, _bars(), st, _verdict(pending_t1=0), dec, 868928)
    assert "[TIER 2 DISTILLATION TRIGGER]" in text
    assert 'startId: "b0"' in text
    assert 'endId: "b5"' in text
    assert "compress({ content: [" in text


def test_render_nudge_t3_targets_the_tier2_blocks():
    st = _state(tier2=[f"c{i}" for i in range(4)])
    dec = btn.decide(_bars(), st, _verdict(pending_t1=0), 868928)
    text = btn.render_nudge(3, _bars(), st, _verdict(pending_t1=0), dec, 868928)
    assert "[TIER 3 CONDENSATION TRIGGER]" in text
    assert 'startId: "c0"' in text
    assert 'endId: "c3"' in text


def test_render_nudge_t1_asks_for_one_call():
    st = _state()
    dec = btn.decide(_bars(), st, _verdict(pending_t1=41387), 868928)
    text = btn.render_nudge(1, _bars(), st, _verdict(pending_t1=41387), dec, 868928)
    assert "compress early and keep context lean" in text
    assert "ONE call" in text


def test_format_tier_target_blocks_reports_the_ratio():
    st = _state(tier1=["b1"])
    text = btn.format_tier_target_blocks(st.by_tier(1), 1)
    assert "b1" in text
    assert "→" in text


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def _log_line(session="s1", fired="idle", **kw):
    f = {
        "usage_pct": "12",
        "usage_tok": "104162",
        "window": "868928",
        "growth": "20423",
        "floor": "20000",
        "ref": "83739",
        "interval": "30000",
        "pending": "15269",
        "pending_thr": "30000",
        "model": "glm-5.3-flash",
        "reason": "T1 effective 15269 >= 40000, growth 20423",
    }
    f.update(kw)
    return (
        f"2026-10-19T18:00:00.000Z [info] [sess={session}] [v=0.1.202] [sess={session}] "
        f"nudge {fired}: usage={f['usage_pct']}% ({f['usage_tok']}/{f['window']}), "
        f"growth={f['growth']}/{f['floor']} (ref={f['ref']}, interval={f['interval']}), "
        f"pendingT1={f['pending']}/{f['pending_thr']} model={f['model']}, "
        f'reason="{f["reason"]}"\n'
    )


def test_cli_table_lists_every_session(tmp_path, capsys):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    log = tmp_path / "bili.log"
    log.write_text(_log_line())
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 30000}}))
    rc = btn.main(["--log", str(log), "--db", str(db), "--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "bars:" in out
    assert "t1eff=" in out
    assert "s1" in out


def test_cli_session_detail_shows_the_gates(tmp_path, capsys):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    log = tmp_path / "bili.log"
    log.write_text(_log_line())
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 30000}}))
    rc = btn.main(
        ["--session", "s1", "--log", str(log), "--db", str(db), "--config", str(cfg), "--gates"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "pools" in out
    assert "decision:" in out
    assert "growth" in out


def test_cli_nudge_exits_1_when_no_rung_is_ready(tmp_path, capsys):
    db = _make_db(tmp_path)
    log = tmp_path / "bili.log"
    log.write_text(_log_line(growth="100", pending="0", reason="growth 100 < floor 20000"))
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 30000}}))
    rc = btn.main(
        [
            "--session",
            "s1",
            "--log",
            str(log),
            "--db",
            str(db),
            "--config",
            str(cfg),
            "--nudge",
        ]
    )
    assert rc == 1
    assert "no nudge" in capsys.readouterr().err


def test_cli_force_tier_renders_even_when_the_gates_fail(tmp_path, capsys):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    log = tmp_path / "bili.log"
    log.write_text(_log_line(growth="100", pending="0", reason="growth 100 < floor 20000"))
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 30000}}))
    rc = btn.main(
        [
            "--session",
            "s1",
            "--log",
            str(log),
            "--db",
            str(db),
            "--config",
            str(cfg),
            "--nudge",
            "--force-tier",
            "2",
        ]
    )
    assert rc == 0
    assert "TIER 2 DISTILLATION TRIGGER" in capsys.readouterr().out


def test_cli_json_is_machine_readable(tmp_path, capsys):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    log = tmp_path / "bili.log"
    log.write_text(_log_line())
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 30000}}))
    rc = btn.main(
        ["--session", "s1", "--log", str(log), "--db", str(db), "--config", str(cfg), "--json"]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["session"] == "s1"
    assert "t1_eff" in payload[0]["pools"]
    assert payload[0]["bars"]["t1"] == 30000


def test_cli_json_and_write_produces_a_json_file(tmp_path, capsys):
    """`--json --write PATH` must write the JSON payload, not the nudge text."""
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    log = tmp_path / "bili.log"
    log.write_text(_log_line())
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 30000}}))
    out_file = tmp_path / "out.json"
    rc = btn.main(
        [
            "--session",
            "s1",
            "--log",
            str(log),
            "--db",
            str(db),
            "--config",
            str(cfg),
            "--json",
            "--write",
            str(out_file),
        ]
    )
    assert rc == 0
    payload = json.loads(out_file.read_text())
    assert payload[0]["session"] == "s1"


def test_cli_nudge_and_write_produces_the_nudge_text(tmp_path, capsys):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    log = tmp_path / "bili.log"
    log.write_text(_log_line())
    cfg = tmp_path / "billion-context.json"
    cfg.write_text(json.dumps({"compress": {"nudgeGrowthTokens": 30000}}))
    out_file = tmp_path / "out.md"
    rc = btn.main(
        [
            "--session",
            "s1",
            "--log",
            str(log),
            "--db",
            str(db),
            "--config",
            str(cfg),
            "--nudge",
            "--force-tier",
            "2",
            "--write",
            str(out_file),
        ]
    )
    assert rc == 0
    body = out_file.read_text()
    assert "TIER 2 DISTILLATION TRIGGER" in body
    assert "answer with: compress({ content: [" in body


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
