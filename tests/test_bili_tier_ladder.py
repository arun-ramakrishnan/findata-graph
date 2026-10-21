"""Tests for the compress-ladder (T1/T2/T3) numbers in `bili_sessions`.

The ladder is three rungs, and the log exposes them only partially:

* FIRES   — a tier's nudge was injected. Observed directly (`INJECT T2`).
* PENDING — the compressible mass a tier is carrying. T1's is on every line
  (`pendingT1=N/thr`); T2's and T3's appear only while they are OVER their
  bar, in the `ready:` hint.
* BAR     — the threshold the tier is measured against. T1's is the log's
  `interval`; T2's and T3's are NOT in the log at all.

These tests pin the parsing of the `ready:`/`blocked:` hints, the derivation
of the bars T2/T3 never state, and the table cell that shows all three.
"""

from __future__ import annotations

from pathlib import Path

from helpers.analytics import bili_sessions as bs
from helpers.analytics.bili_analytics import (
    Analysis,
    NudgeSample,
    parse_logs,
    tier_blocked,
    tier_ready,
)


def _line(
    sid: str,
    kind: str = "idle",
    *,
    reason: str = "max compressible 199877 < threshold 200000",
    pending: int = 199877,
    thr: int = 200000,
    growth: int = 1000,
    floor: int = 90000,
    interval: int = 200000,
) -> str:
    return (
        f"2026-10-17T04:25:16.517Z [info] [sess={sid}] [v=0.1.197] [{sid}] "
        f"nudge {kind}: usage=37.0% (291536/792576), "
        f"growth={growth}/{floor} (ref=500, interval={interval}), "
        f"pendingT1={pending}/{thr} model=space-bunny-free, "
        f'reason="{reason}"'
    )


class TestReadyHint:
    def test_parses_t1_and_t2(self):
        assert tier_ready("growth 0 < floor 67500, ready: T1 343668, T2 28266") == {
            "T1": 343668,
            "T2": 28266,
        }

    def test_parses_all_three(self):
        assert tier_ready("ready: T1 100, T2 200, T3 300") == {
            "T1": 100,
            "T2": 200,
            "T3": 300,
        }

    def test_absent_hint_is_empty(self):
        assert tier_ready("max compressible 199877 < threshold 200000") == {}

    def test_a_tier_not_over_its_bar_is_absent(self):
        # The plugin lists a tier only while it qualifies, so a missing tier
        # means "never cleared the bar", not "unknown".
        assert tier_ready("ready: T2 28266") == {"T2": 28266}


class TestBlockedHint:
    def test_parses_the_cadence_block(self):
        assert tier_blocked("growth 0 < floor 67500, blocked: T2 (cadence)") == "2"

    def test_absent_block_is_empty(self):
        assert tier_blocked("growth 0 < floor 67500") == ""


class TestNudgeSample:
    def test_the_hints_ride_on_the_sample(self):
        a = Analysis(log_path="x")
        a.nudges.append(
            NudgeSample(
                ts="2026-10-17T04:25:16.517Z",
                session="s1",
                usage_tok=291536,
                usage_pct=37.0,
                growth=0,
                ref=500,
                interval=50000,
                pending_t1=343668,
                pending_thr=50000,
                model="m",
                reason="growth 0 < floor 67500, ready: T1 343668, T2 28266",
                tier_pending={"T1": 343668, "T2": 28266},
                blocked_tier="2",
            )
        )
        assert a.tier_fires() == {"T1": 0, "T2": 0, "T3": 0}
        assert a.tier_pending() == {"T1": 343668, "T2": 28266}
        assert a.tier_blocked_counts() == {"2": 1}


class TestTierPendingResets:
    """A tier that drops back under its bar must not keep reporting the old
    mass. The plugin's idle branch opens its reason with "max compressible"
    and its `ready:` hint lists EVERY over-bar tier, so a tier the hint
    omits is provably back under its bar."""

    def _idle(self, reason: str, pending: int = 100) -> NudgeSample:
        return NudgeSample(
            ts="t",
            session="s",
            usage_tok=0,
            usage_pct=0.0,
            growth=0,
            ref=0,
            interval=30000,
            pending_t1=pending,
            pending_thr=30000,
            model="m",
            reason=reason,
            tier_pending=tier_ready(reason),
            tier_idle=("max compressible" in reason),
        )

    def test_a_tier_that_fell_back_under_its_bar_resets(self):
        a = Analysis(log_path="x")
        a.nudges.append(self._idle("max compressible 28266, growth 0, ready: T2 28266"))
        assert a.tier_pending()["T2"] == 28266
        a.nudges.append(self._idle("max compressible 4906 < threshold 5000"))
        assert a.tier_pending()["T2"] == 0

    def test_a_fired_line_does_not_reset_the_pool(self):
        a = Analysis(log_path="x")
        a.nudges.append(self._idle("max compressible 28266, growth 0, ready: T2 28266"))
        a.nudges.append(
            NudgeSample(
                ts="t",
                session="s",
                usage_tok=0,
                usage_pct=0.0,
                growth=0,
                ref=0,
                interval=30000,
                pending_t1=24594,
                pending_thr=30000,
                model="m",
                reason="T2 distill ready: 29 tier-1 blocks (28266 tokens) >= 5000",
                fired_t2=True,
            )
        )
        assert a.tier_pending()["T2"] == 28266

    def test_t1_follows_the_log_even_down_to_zero(self):
        a = Analysis(log_path="x")
        a.nudges.append(self._idle("max compressible 1000, growth 0", pending=1000))
        a.nudges.append(self._idle("max compressible 0 < threshold 5000", pending=0))
        assert a.tier_pending()["T1"] == 0


class TestTierFires:
    def _a(self) -> Analysis:
        a = Analysis(log_path="x")
        for tier in (1, 2, 3, 1):
            a.nudges.append(
                NudgeSample(
                    ts="t",
                    session="s",
                    usage_tok=0,
                    usage_pct=0.0,
                    growth=0,
                    ref=0,
                    interval=0,
                    pending_t1=0,
                    pending_thr=0,
                    model="m",
                    reason="r",
                    fired_t1=(tier == 1),
                    fired_t2=(tier == 2),
                    fired_t3=(tier == 3),
                )
            )
        return a

    def test_counts_per_tier(self):
        assert self._a().tier_fires() == {"T1": 2, "T2": 1, "T3": 1}


class TestTierBars:
    def test_t1_bar_is_the_log_interval(self):
        a = Analysis(log_path="x")
        a.nudges.append(
            NudgeSample(
                ts="t",
                session="s",
                usage_tok=0,
                usage_pct=0.0,
                growth=0,
                ref=0,
                interval=50000,
                pending_t1=1,
                pending_thr=50000,
                model="m",
                reason="r",
            )
        )
        bars = a.tier_bars()
        assert bars["T1"] == 50000
        # T2/T3 default to 1.5x the resolved T1 bar (tier2GrowthMultiplier).
        assert bars["T2"] == 75000
        assert bars["T3"] == 75000

    def test_no_nudges_no_bars(self):
        assert Analysis(log_path="x").tier_bars() == {}


class TestConfigOverride:
    def test_the_config_wins_over_the_derived_bar(self):
        bars = {"T1": 50000, "T2": 75000, "T3": 75000}
        out = bs._with_tier_overrides(bars, {"tierNudgeTokens": {"t2": 5000}})
        assert out["T2"] == 5000
        assert out["T3"] == 75000

    def test_an_absent_config_leaves_the_derived_bars(self):
        bars = {"T1": 50000, "T2": 75000, "T3": 75000}
        assert bs._with_tier_overrides(bars, {}) == bars
        assert bs._with_tier_overrides(bars, {"tierNudgeTokens": {}}) == bars

    def test_t1_can_be_decoupled_from_nudge_growth_tokens(self):
        """`tierNudgeTokens.t1` raises T1's bar WITHOUT touching the growth
        floor, and the log's `pendingT1=N/thr` denominator is
        `nudgeGrowthTokens` -- so the config is the only source for T1's bar.
        """
        bars = {"T1": 30000, "T2": 45000, "T3": 45000}
        out = bs._with_tier_overrides(
            bars, {"nudgeGrowthTokens": 30000, "tierNudgeTokens": {"t1": 50000}}
        )
        assert out["T1"] == 50000
        # T2/T3 derive from nudgeGrowthTokens, NOT from the decoupled T1 bar.
        assert out["T2"] == 45000
        assert out["T3"] == 45000

    def test_the_derivation_base_is_nudge_growth_tokens(self):
        bars = {"T1": 50000, "T2": 75000, "T3": 75000}
        out = bs._with_tier_overrides(bars, {"nudgeGrowthTokens": 30000})
        assert out["T2"] == 45000
        assert out["T3"] == 45000
        # T1 is not in the config, so the log-derived bar stands.
        assert out["T1"] == 50000

    def test_all_three_bars_from_the_config(self):
        bars = {"T1": 30000, "T2": 45000, "T3": 45000}
        out = bs._with_tier_overrides(
            bars,
            {
                "nudgeGrowthTokens": 30000,
                "tierNudgeTokens": {"t1": 50000, "t2": 5000, "t3": 3000},
            },
        )
        assert out == {"T1": 50000, "T2": 5000, "T3": 3000}


class TestTierCell:
    def test_fires_and_pending(self):
        row = {"tier_fires": {"T1": 25}, "tier_pending": {"T1": 113124}}
        assert bs._tier_cell(row, "T1") == "25f"

    def test_a_tier_that_never_cleared_its_bar(self):
        row = {"tier_fires": {"T3": 0}, "tier_pending": {}}
        assert bs._tier_cell(row, "T3") == "0f"

    def test_compact_tokens(self):
        assert bs._compact_tokens(None) == "-"
        assert bs._compact_tokens(0) == "0"
        assert bs._compact_tokens(9999) == "9999"
        assert bs._compact_tokens(10000) == "10k"
        assert bs._compact_tokens(113124) == "113k"


class TestTable:
    def test_the_three_tier_columns_exist(self):
        keys = [spec[0] for spec in bs._COLUMNS]
        assert "tier_t1" in keys
        assert "tier_t2" in keys
        assert "tier_t3" in keys

    def test_the_tier_columns_render(self):
        row = {
            "session": "s1",
            "client": "opencode",
            "model": "m",
            "protocol": "openai",
            "requests": 1,
            "context_now": 1,
            "cum_input": 1,
            "saved": 0,
            "hit_pct": 0.0,
            "switches": 0,
            "nudges": 1,
            "folds": 0,
            "degenerate_folds": 0,
            "blocks": 0,
            "reason_pair_violations": 0,
            "tier_fires": {"T1": 9, "T2": 4, "T3": 0},
            "tier_pending": {"T1": 18826, "T2": 35476},
        }
        widths = bs._fit_widths([row], 200)
        cells = bs._row_cells(row, widths)
        assert "9f" in cells
        assert "4f" in cells
        assert "0f" in cells

    def test_the_model_column_is_not_crushed_to_its_header(self):
        # The tier columns take fixed width; without a floor the squeeze lands
        # on `model` and the lane becomes unidentifiable ("deep…").
        row = {
            "session": "s" * 30,
            "client": "opencode",
            "model": "deepseek-v4.1-flash-free",
            "protocol": "openai",
            "requests": 1,
            "context_now": 1,
            "cum_input": 1,
            "saved": 0,
            "hit_pct": 0.0,
            "switches": 0,
            "nudges": 1,
            "folds": 0,
            "degenerate_folds": 0,
            "blocks": 0,
            "reason_pair_violations": 0,
            "tier_fires": {"T1": 1, "T2": 1, "T3": 1},
            "tier_pending": {"T1": 1, "T2": 1, "T3": 1},
        }
        widths = bs._fit_widths([row], 200)
        i = [spec[0] for spec in bs._COLUMNS].index("model")
        assert widths[i] >= 14


class TestParseEndToEnd:
    def test_the_ready_hint_survives_the_parse(self, tmp_path: Path):
        p = tmp_path / "bili.log"
        p.write_text(
            "\n".join(
                [
                    _line(
                        "s1",
                        reason="growth 0 < floor 67500, ready: T1 343668, T2 28266",
                    ),
                    _line(
                        "s1",
                        "INJECT T2",
                        reason="T2 distill ready: 3 tier-1 blocks (41229 tokens) >= 5000",
                    ),
                ]
            )
            + "\n"
        )
        a = parse_logs([p], session_filter="s1")
        assert a is not None
        assert a.tier_fires() == {"T1": 0, "T2": 1, "T3": 0}
        assert a.tier_pending()["T2"] == 28266
        assert a.tier_blocked_counts() == {}
