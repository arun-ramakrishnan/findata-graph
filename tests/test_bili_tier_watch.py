"""Tests for helpers/analytics/bili_tier_watch.py -- the live tier-ladder watcher.

The watcher answers one question that the log alone cannot: WHY is a rung
that is over its bar not firing? The answer is in three places -- the
plugin's last verdict (T1 effective mass, growth), the tier pools (which
only the block store knows exactly, because the log's `ready:` hint lists
only the tiers that are OVER their bar), and the configured bars (re-read
live, because the proxy re-reads its config live).

These tests pin: the rotation-safe tail (the v1 bug: a byte offset into a
rotated, SHORTER log reads nothing forever), the idempotent consumption of
re-scanned text, the verdict parsing, the three armed/stranded flags, the
live config re-read, and the one-shot CLI.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.analytics import bili_tier_nudge as btn  # noqa: E402
from helpers.analytics import bili_tier_watch as btw  # noqa: E402

NUDGE = (
    "2026-10-19T18:05:35.123Z [info] [sess=s1] [v=0.1.202] "
    "nudge idle: usage=12.0% (104162/868928), growth=21191/20000 "
    "(ref=79915, interval=30000), pendingT1=16073/30000 "
    'model=deepseek-v4.1-flash-free, reason="max compressible 16073, growth 21191"'
)
NUDGE_T2 = (
    "2026-10-19T18:06:00.000Z [info] [sess=s1] [v=0.1.202] "
    "nudge INJECT T2: usage=12.0% (104162/868928), growth=21191/20000 "
    "(ref=79915, interval=30000), pendingT1=16073/30000 "
    'model=deepseek-v4.1-flash-free, reason="T2 distill ready: 18 tier-1 blocks (17061 tokens) >= 5000"'
)
FOLD = (
    "2026-10-19T18:06:32.000Z [info] [sess=s1] [v=0.1.202] "
    "[acp-compress-obs] shrink 10% (~46284/457974 tok) foldPoint=b5 blocks=2 "
    "anchor≈1748 tok postCtx≈411690"
)
# The REAL fold line shape in the log carries the session as a BARE
# `[ses_XXX]` tag, not the `[sess=ses_XXX]` form the nudge lines use. A
# filter that only knows the nudge shape drops every fold — the watcher
# then reports folds=0 while the log plainly contains them.
FOLD_REAL_SHAPE = (
    "2026-10-20T04:14:57.135Z [info] [v=0.1.202] [ses_real1] [plugin] "
    "[acp-compress-obs] shrink 1% (~31062/2460899 tok) foldPoint=m11413 "
    "blocks=2 anchor≈24811 tok (26 active blocks, sys excluded) postCtx≈2429837"
)
INJECT_REAL_SHAPE = (
    "2026-10-20T04:39:41.717Z [info] [sess=ses_real1] [v=0.1.202] [ses_real1] "
    "nudge INJECT T1: usage=9% (83270/917504), growth=21038/20000 "
    "(ref=62232, interval=30000), pendingT1=44241/30000 model=nemotron-3.5-super-vl-free, "
    'reason="max compressible 44241"'
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


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


def _add_block(db, block_id, tier, summary_len=400, active=1, session="s1"):
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
        (
            abs(hash(block_id)) % 1000,
            session,
            block_id,
            tier,
            active,
            json.dumps(payload),
        ),
    )
    con.commit()
    con.close()


def _bars(t1=40000, t2=5000, t3=3000, floor=20000):
    return btn.Bars(nudge_growth=30000, t1=t1, t2=t2, t3=t3, growth_floor=floor)


def _watch_with(**kw) -> btw.Watch:
    w = btw.Watch()
    w.consume(kw.get("text", NUDGE))
    return w


@pytest.fixture(autouse=True)
def _hermetic_session_metrics(monkeypatch):
    """build_sample must not walk /proc or read the operator's real log family."""
    monkeypatch.setattr(btw.bili_sessions, "reset_caches", lambda: None)
    monkeypatch.setattr(btw.bili_sessions, "run_for_sid", lambda sid: {})


# ---------------------------------------------------------------------------
# the tail
# ---------------------------------------------------------------------------


def test_tail_reads_only_new_bytes(tmp_path):
    log = tmp_path / "bili.log"
    log.write_text("one\n")
    tail = btw.Tail(log)
    assert "one" in tail.seed()
    log.write_text("one\ntwo\n")
    assert "two" in tail.poll()
    assert tail.poll() == ""


def test_tail_reseeds_after_rotation(tmp_path):
    """The v1 bug: the log rotates to a SHORTER file; the offset must reset."""
    log = tmp_path / "bili.log"
    log.write_text("a\n" * 500)
    tail = btw.Tail(log)
    tail.seed()
    # rotation: a NEW inode whose size is smaller than the old offset
    log.unlink()
    log.write_text("b\n")
    assert "b" in tail.poll()
    assert tail.rotations == 1


def test_tail_poll_survives_a_missing_file(tmp_path):
    log = tmp_path / "bili.log"
    log.write_text("x\n")
    tail = btw.Tail(log)
    tail.seed()
    log.unlink()
    assert tail.poll() == ""


# ---------------------------------------------------------------------------
# consumption is idempotent
# ---------------------------------------------------------------------------


def test_consume_counts_a_fire_once():
    w = btw.Watch()
    w.consume(NUDGE_T2)
    w.consume(NUDGE_T2)  # a re-scan after a rotation must not double count
    assert w.fires["T2"] == 1


def test_consume_counts_a_fold_once():
    w = btw.Watch()
    w.consume(FOLD)
    w.consume(FOLD)
    assert w.folds == 1
    assert w.saved == 46284


def test_a_fold_line_in_the_real_log_shape_is_counted():
    """The fold tag is a BARE `[ses_XXX]`, not `[sess=ses_XXX]`.

    A filter that only knows the nudge shape silently discards every
    fold: the watcher reported folds=0 while the log held them.
    """
    w = btw.Watch(session="ses_real1")
    w.consume(FOLD_REAL_SHAPE)
    assert w.folds == 1
    assert w.saved == 31062


def test_a_pinned_session_ignores_other_sessions_folds_and_fires():
    """The counters are PER SESSION when a session is pinned.

    Without the filter the watcher summed every session in the log tail,
    so its fold/fire counts ran ahead of the per-session table.
    """
    other = FOLD_REAL_SHAPE.replace("ses_real1", "ses_other")
    other_inject = INJECT_REAL_SHAPE.replace("ses_real1", "ses_other")
    w = btw.Watch(session="ses_real1")
    w.consume(FOLD_REAL_SHAPE)
    w.consume(other)
    w.consume(INJECT_REAL_SHAPE)
    w.consume(other_inject)
    assert w.folds == 1
    assert w.fires["T1"] == 1
    assert w.saved == 31062


def test_without_a_pinned_session_the_counters_are_global():
    w = btw.Watch()
    w.consume(FOLD_REAL_SHAPE)
    w.consume(FOLD_REAL_SHAPE.replace("ses_real1", "ses_other"))
    assert w.folds == 2


def test_consume_parses_the_verdict():
    w = _watch_with()
    v = w.verdicts["s1"]
    assert v.pending_t1 == 16073
    assert v.growth == 21191
    assert v.usage_tok == 104162
    assert v.usage_pct == 12.0
    assert v.ref == 79915
    assert v.model == "deepseek-v4.1-flash-free"
    assert w.windows["s1"] == 868928
    assert w.current_session == "s1"


def test_an_older_verdict_never_overwrites_a_newer_one():
    w = btw.Watch()
    w.consume(NUDGE)
    older = NUDGE.replace("2026-10-19T18:05:35.123Z", "2026-10-19T18:00:00.000Z").replace(
        "pendingT1=16073", "pendingT1=99999"
    )
    w.consume(older)
    assert w.verdicts["s1"].pending_t1 == 16073


def test_current_session_follows_the_verdict_clock_not_discovery_order():
    """`order` records a session's first in-window nudge, so a session can be
    discovered late while carrying an older verdict (seed boundary, mixed
    producers). The default render must follow the newest VERDICT."""
    w = btw.Watch()
    w.consume(NUDGE)  # s1 @ 18:05:35
    late_but_older = NUDGE.replace("s1", "s3").replace(
        "2026-10-19T18:05:35.123Z", "2026-10-19T18:02:00.000Z"
    )
    w.consume(late_but_older)
    assert w.current_session == "s1"


def test_log_age_treats_naive_verdict_ts_as_local(tmp_path, monkeypatch):
    """The plugin's log stamps are LOCAL wall time with no offset. Stamping
    UTC onto them put the parsed time hours in the future on the IST box:
    log_age=-19747s in every row (pending.md 2026-10-30). A naive ts must
    read as local; the expected value is computed from this box's own tz so
    the test is portable."""
    from datetime import UTC, datetime

    now_epoch = datetime(2026, 10, 30, 6, 50, 0, tzinfo=UTC).timestamp()
    monkeypatch.setattr(btw.time, "time", lambda: now_epoch)
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    line = NUDGE.replace("2026-10-19T18:05:35.123Z", "2026-10-30T06:40:00.000")
    w = _watch_with(text=line)
    s = btw.build_sample(w, _bars(), db)
    naive_local = datetime.strptime("2026-10-30T06:40:00", "%Y-%m-%dT%H:%M:%S").astimezone()
    assert s.log_age == int(now_epoch - naive_local.timestamp())


def test_log_age_reads_z_stamped_lines_as_local_too(tmp_path, monkeypatch):
    """RE_TS truncates each line's stamp to seconds — any Z/offset suffix is
    dropped before Verdict.ts, so even a Z-stamped line reads as the local
    verdict clock (that IS the plugin's clock shape today)."""
    from datetime import UTC, datetime

    now_epoch = datetime(2026, 10, 30, 6, 50, 0, tzinfo=UTC).timestamp()
    monkeypatch.setattr(btw.time, "time", lambda: now_epoch)
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    w = _watch_with(text=NUDGE.replace("2026-10-19T18:05:35.123Z", "2026-10-30T06:40:00.000Z"))
    s = btw.build_sample(w, _bars(), db)
    naive_local = datetime.strptime("2026-10-30T06:40:00", "%Y-%m-%dT%H:%M:%S").astimezone()
    assert s.log_age == int(now_epoch - naive_local.timestamp())


def test_blocks_counts_all_rows_and_act_the_active_subset(tmp_path):
    """`blocks` used to carry the active-flag subset under the same label
    bili_sessions uses for the creation-count TOTAL (pending.md 2026-10-30).
    Both counts now travel under their own names."""
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1)
    _add_block(db, "b2", 1, active=0)
    w = _watch_with(text=NUDGE)
    s = btw.build_sample(w, _bars(), db)
    assert s.blocks == 2
    assert s.act == 1
    assert "blocks=2 act=1" in s.render()
    assert s.as_dict()["blocks"] == 2 and s.as_dict()["act"] == 1


# ---------------------------------------------------------------------------
# the sample: the three flags this watcher exists for
# ---------------------------------------------------------------------------


def test_stranded_when_t1_is_under_bar_and_t2_cannot_win_the_ordering(tmp_path):
    """The 2-hour idle: t1eff under the bar, t2Pen BELOW t1eff."""
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1, summary_len=400)  # 100 tokens
    w = _watch_with(text=NUDGE)  # pending_t1 = 16073
    s = btw.build_sample(w, _bars(), db)
    assert s.t2pen == 100
    assert s.t1eff == 16073
    assert s.ord2 is False
    assert "STRANDED" in s.flags
    assert "T2-ARMED" not in s.flags


def test_t2_armed_when_the_pool_is_over_bar_and_above_t1_eff(tmp_path):
    db = _make_db(tmp_path)
    for i in range(100):  # 100 * 100 tokens = 10,000 >= bar 5,000
        _add_block(db, f"b{i}", 1, summary_len=400)
    w = _watch_with(text=NUDGE.replace("pendingT1=16073", "pendingT1=100"))
    s = btw.build_sample(w, _bars(), db)
    assert s.t2pen == 10_000
    assert s.ord2 is True
    assert "T2-ARMED" in s.flags
    assert "STRANDED" not in s.flags


def test_t3_armed_needs_t3pen_to_beat_both_other_pools(tmp_path):
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1, summary_len=400)  # t2pen = 100
    for i in range(50):  # t3pen = 50 * 100 = 5,000
        _add_block(db, f"c{i}", 2, summary_len=400)
    w = _watch_with(text=NUDGE.replace("pendingT1=16073", "pendingT1=100"))
    s = btw.build_sample(w, _bars(), db)
    assert s.t3pen == 5_000
    assert s.ord3 is True
    assert "T3-ARMED" in s.flags


def test_t3_not_armed_when_its_pool_is_below_t2pen(tmp_path):
    """t3Pen must exceed t2Pen, not merely its own bar."""
    db = _make_db(tmp_path)
    for i in range(100):  # t2pen = 10,000
        _add_block(db, f"b{i}", 1, summary_len=400)
    _add_block(db, "c1", 2, summary_len=400)  # t3pen = 100
    w = _watch_with(text=NUDGE.replace("pendingT1=16073", "pendingT1=100"))
    s = btw.build_sample(w, _bars(), db)
    assert s.t3pen == 100
    assert s.ord3 is False
    assert "T3-ARMED" not in s.flags


def test_the_growth_floor_gates_the_armed_flags(tmp_path):
    db = _make_db(tmp_path)
    for i in range(100):
        _add_block(db, f"b{i}", 1, summary_len=400)
    w = _watch_with(text=NUDGE.replace("growth=21191", "growth=19999"))
    s = btw.build_sample(w, _bars(), db)
    assert s.growth_ok is False
    assert "T2-ARMED" not in s.flags
    assert "GROWTH-OK" not in s.flags


def test_t1_eligible_is_flagged_when_the_fresh_mass_clears_the_bar(tmp_path):
    db = _make_db(tmp_path)
    w = _watch_with(text=NUDGE.replace("pendingT1=16073", "pendingT1=45000"))
    s = btw.build_sample(w, _bars(t1=40000), db)
    assert "T1-ELIGIBLE" in s.flags


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------


def test_once_json_emits_one_sample(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        btw.bili_sessions,
        "run_for_sid",
        lambda sid: {
            "nudges": 3,
            "requests": 12,
            "cum_input": 1234,
            "cached_input": 1100,
        },
    )
    db = _make_db(tmp_path)
    _add_block(db, "b1", 1, summary_len=400)
    log = tmp_path / "bili.log"
    log.write_text(NUDGE + "\n")
    cfg = tmp_path / "cfg.json"
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
    rc = btw.main(
        [
            "--once",
            "--json",
            "--session",
            "s1",
            "--config",
            str(cfg),
            "--db",
            str(db),
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session"] == "s1"
    assert payload["bars"] == {"t1": 40000, "t2": 5000, "t3": 3000}
    assert payload["growth_floor"] == 20000
    assert payload["nudges"] == 3
    assert payload["requests"] == 12
    assert payload["hit_pct"] == 0.0
    assert payload["degen_flag"] is False
    assert payload["cum_input"] == 1234
    assert payload["cached_input"] == 1100


def test_a_missing_log_is_a_fatal_error(tmp_path, monkeypatch):
    monkeypatch.setattr(btw.bili_logpath, "discover_all", lambda: (str(tmp_path / "nope.log"), ""))
    assert btw.main(["--once"]) == 1


def test_the_bars_are_re_read_per_sample(tmp_path):
    """The proxy re-reads its config live; a cached bar goes stale."""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"compress": {"tierNudgeTokens": {"t1": 40000}}}))
    first = btn.Bars.from_file(cfg)
    cfg.write_text(json.dumps({"compress": {"tierNudgeTokens": {"t1": 25000}}}))
    second = btn.Bars.from_file(cfg)
    assert first.t1 == 40000
    assert second.t1 == 25000


def test_session_metrics_drops_the_bili_sessions_caches(monkeypatch):
    """bili_sessions caches per process for its one-shot CLI; the watcher
    must drop those caches or its counters freeze at the first sample."""
    drops, reads = [], []

    def fake_run(sid):
        reads.append(sid)
        return {"requests": 7}

    monkeypatch.setattr(btw.bili_sessions, "reset_caches", lambda: drops.append(True))
    monkeypatch.setattr(btw.bili_sessions, "run_for_sid", fake_run)
    assert btw._session_metrics("s1") == {"requests": 7}
    assert btw._session_metrics("s1") == {"requests": 7}
    assert drops == [True, True]  # dropped before EVERY read, not just the first
    assert reads == ["s1", "s1"]


def test_session_counters_are_refetched_per_sample(tmp_path, monkeypatch):
    """The freeze regression, end to end: successive samples must see the
    counters move as the session accrues work."""
    reads = {"n": 0}

    def fake_run(sid):
        reads["n"] += 1
        return {"nudges": reads["n"], "requests": 10 * reads["n"]}

    monkeypatch.setattr(btw.bili_sessions, "reset_caches", lambda: None)
    monkeypatch.setattr(btw.bili_sessions, "run_for_sid", fake_run)
    db = _make_db(tmp_path)
    w = _watch_with()
    first = btw.build_sample(w, _bars(), db, "s1")
    second = btw.build_sample(w, _bars(), db, "s1")
    assert (first.nudges, first.requests) == (1, 10)
    assert (second.nudges, second.requests) == (2, 20)


def test_session_counters_flow_into_render_and_json(tmp_path, monkeypatch):
    monkeypatch.setattr(btw.bili_sessions, "reset_caches", lambda: None)
    monkeypatch.setattr(
        btw.bili_sessions,
        "run_for_sid",
        lambda sid: {
            "nudges": 3,
            "hit_pct": 90.0,
            "requests": 12,
            "degenerate_folds": 1,
            "cum_input": 1234,
            "cached_input": 1100,
        },
    )
    db = _make_db(tmp_path)
    s = btw.build_sample(_watch_with(), _bars(), db, "s1")
    assert (s.nudges, s.hit_pct, s.requests) == (3, 90.0, 12)
    assert s.degen_flag is True
    assert s.cum_input == 1234 and s.cached_input == 1100
    assert "cumIn=1.2k cachedIn=1.1k" in s.render()
    d = s.as_dict()
    assert (d["nudges"], d["hit_pct"], d["requests"]) == (3, 90.0, 12)
    assert d["degen_flag"] is True and d["cum_input"] == 1234 and d["cached_input"] == 1100


@pytest.mark.parametrize("n", [0, 1, 1000, 1_000_000])
def test_fmt(n):
    assert btw.fmt(n) in {"0", "1", "1.0k", "1.00M"}


class TestLineSessionZcodeForm:
    """Zcode sessions log as `session_<hex>`; the extractor must carry the
    FULL id (the db/dashboard key it), and recognise the bare tag too."""

    Z = "session_36697bd13b3b4fbf445781dceb154794"

    def test_sess_tag_keeps_the_full_zcode_id(self):
        line = (
            f"2026-10-27T04:29:05.603Z [info] [sess={self.Z}] [v=0.1.208] [{self.Z}] "
            "[acp-usage] round 1 input=70374 cached=67392 (cache hit 96%)"
        )
        assert btw.line_session(line) == self.Z

    def test_bare_zcode_tag_is_extracted(self):
        line = (
            f"2026-10-27T04:29:05.603Z [info] [v=0.1.208] [{self.Z}] "
            "[acp-compress-obs] shrink 7% foldPoint=m01250 blocks=2"
        )
        assert btw.line_session(line) == self.Z

    def test_bracket_words_are_still_not_swallowed(self):
        assert btw.line_session("[info] hello [plugin] world") == ""
        assert btw.line_session("no tags at all") == ""

    def test_dsh_hyphen_prefix_is_stripped_but_zcode_is_not(self):
        assert btw.line_session("[sess=session-c5f777d8-d511-4222-ae94-dda4319440bc]") == (
            "c5f777d8-d511-4222-ae94-dda4319440bc"
        )
        assert btw.line_session(f"[sess={self.Z}] nudge idle") == self.Z
