#!/usr/bin/env python3
"""Manual tier-ladder nudge for a billion-context session.

The plugin injects a tier nudge only when its own gates pass, and it
decides that on the request path where the model context is visible. This
tool reproduces the SAME decision from three offline sources, so an
operator can ask "which rung should fire for this session, and what would
the nudge say?" without waiting for the plugin's next evaluation:

  * the plugin's own last verdict, parsed from the bili log
    (`pendingT1` = T1 effective mass, `growth`, `usage`, the `ready:` hint);
  * the tier pools, computed EXACTLY from the session store
    (`sessions/index.db`: sum of summary tokens over active tier-N blocks);
  * the configured bars (`~/.config/billion-context/billion-context.json`).

It never mutates the session. It prints the nudge text the plugin would
inject, and the exact `compress(...)` call that answers it.

Usage:
    python helpers/analytics/bili_tier_nudge.py                 # state table
    python helpers/analytics/bili_tier_nudge.py --session SID   # one session
    python helpers/analytics/bili_tier_nudge.py --session SID --nudge
    python helpers/analytics/bili_tier_nudge.py --session SID --nudge \
        --write /mnt/data/tmp/opencode/nudge.md
    python helpers/analytics/bili_tier_nudge.py --json

Sources of truth for the rules implemented here (billion-context 0.1.202,
`dist/index.js`):
  * `tierThresholds`  = {1: tierGrowthTokens?.t1 ?? nudgeGrowthTokens,
                         2: tierGrowthTokens?.t2 ?? round(nudgeGrowthTokens*1.5),
                         3: tierGrowthTokens?.t3 ?? round(nudgeGrowthTokens*1.5)}
  * growth floor      = max(minGrowthFloor 2e4, minGrowthRatio .45 *
                         nudgeGrowthTokens)   -- 2e4 is NOT configurable
  * selection order   = strict else-if: T1 shadows T2 shadows T3
                         T1: t1Eff >= bar1
                         T2: t2Pen >= bar2 && t2Pen > t1Eff
                         T3: t3Pen >= bar3 && t3Pen > t2Pen && t3Pen > t1Eff
  * every rung additionally needs `growth >= growthFloor` and passes a
    per-tier cadence gate (`tokenCount - lastShownByTier[t] >= growthFloor`)
  * pressure branch (usage >= 75% or >= 95% emergency) IGNORES the growth
    floor and the ordering, and picks the tier with the LARGEST pool
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from helpers.analytics.bili_analytics import (  # noqa: E402
    log_rotation_family,
    parse_logs,
)

DEFAULT_LOG = Path.home() / ".local/state/billion-context/bili.log"
DEFAULT_DB = Path.home() / ".local/share/billion-context/sessions/index.db"
DEFAULT_CONFIG = Path.home() / ".config/billion-context/billion-context.json"
BUNDLE = Path.home() / ".bun/install/global/node_modules/billion-context/dist/index.js"

# Plugin defaults that the file config cannot change.
HARD_GROWTH_FLOOR = 20_000
MIN_GROWTH_RATIO = 0.45
TIER_GROWTH_MULTIPLIER = 1.5
MAX_CONTEXT_PCT = 0.75
EMERGENCY_PCT = 0.95
VIABLE_RANGE_MIN_TOKENS = 200
PRESERVE_RECENT_MESSAGES = 3
PRESERVE_RECENT_TOKENS = 5_000


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@dataclass
class Bars:
    """The three tier bars plus the growth gate, as the plugin resolves them."""

    nudge_growth: int = 50_000
    t1: int = 50_000
    t2: int = 75_000
    t3: int = 75_000
    growth_floor: int = HARD_GROWTH_FLOOR
    source: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: Path) -> Bars:
        raw: dict = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                raw = {}
        compress = raw.get("compress") or {}
        tiers = compress.get("tierNudgeTokens") or {}
        growth = int(compress.get("nudgeGrowthTokens") or 0) or 50_000
        t1 = int(tiers["t1"]) if tiers.get("t1") else growth
        t2 = int(tiers["t2"]) if tiers.get("t2") else round(growth * TIER_GROWTH_MULTIPLIER)
        t3 = int(tiers["t3"]) if tiers.get("t3") else round(growth * TIER_GROWTH_MULTIPLIER)
        floor = max(HARD_GROWTH_FLOOR, round(MIN_GROWTH_RATIO * growth))
        src = {
            "nudgeGrowthTokens": "file" if compress.get("nudgeGrowthTokens") else "default",
            "t1": "file" if tiers.get("t1") else "fallback nudgeGrowthTokens",
            "t2": "file" if tiers.get("t2") else "fallback 1.5x nudgeGrowthTokens",
            "t3": "file" if tiers.get("t3") else "fallback 1.5x nudgeGrowthTokens",
            "growthFloor": (
                "hard 20000"
                if round(MIN_GROWTH_RATIO * growth) <= HARD_GROWTH_FLOOR
                else "0.45 x nudgeGrowthTokens"
            ),
        }
        return cls(
            nudge_growth=growth,
            t1=t1,
            t2=t2,
            t3=t3,
            growth_floor=floor,
            source=src,
        )


# ---------------------------------------------------------------------------
# session store
# ---------------------------------------------------------------------------


@dataclass
class Block:
    block_id: str
    tier: int
    active: bool
    topic: str
    summary: str
    compressed_tokens: int
    eff_ids: int

    @property
    def summary_tokens(self) -> int:
        return math.ceil(len(self.summary) / 4)


@dataclass
class SessionState:
    session_id: str
    blocks: list[Block] = field(default_factory=list)
    last_shown_by_tier: dict[str, int] = field(default_factory=dict)
    kstate_tokens: int = 0

    @property
    def active(self) -> list[Block]:
        return [b for b in self.blocks if b.active]

    def by_tier(self, tier: int) -> list[Block]:
        """Active blocks of a tier, ordered by numeric block id."""
        bs = [b for b in self.active if b.tier == tier]
        return sorted(bs, key=lambda b: int(re.sub(r"\D", "", b.block_id) or 0))

    def pool(self, tier: int) -> int:
        """The tier's pending pool: sum of summary tokens over its blocks.

        T2's pool is the tier-1 summary mass; T3's is the tier-2 summary
        mass. This is exactly `pendingByTier` in the plugin.
        """
        return sum(b.summary_tokens for b in self.by_tier(tier))


def load_session_state(db: Path, session_id: str) -> SessionState:
    state = SessionState(session_id=session_id)
    if not db.is_file():
        return state
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT kstate_json FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row:
            try:
                kstate = json.loads(row["kstate_json"] or "{}")
            except json.JSONDecodeError:
                kstate = {}
            nudge = kstate.get("nudge") or {}
            state.last_shown_by_tier = {
                str(k): int(v) for k, v in (nudge.get("lastShownByTier") or {}).items()
            }
            state.kstate_tokens = int(nudge.get("lastPerMessageNudgeTokens") or 0)
        for r in con.execute(
            "SELECT block_id, tier, active, payload_json FROM blocks "
            "WHERE session_id=? ORDER BY ord",
            (session_id,),
        ):
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            state.blocks.append(
                Block(
                    block_id=r["block_id"],
                    tier=int(r["tier"]),
                    active=bool(r["active"]),
                    topic=payload.get("topic") or "",
                    summary=payload.get("summary") or "",
                    compressed_tokens=int(payload.get("compressedTokens") or 0),
                    eff_ids=len(payload.get("effectiveMessageIds") or []),
                )
            )
    finally:
        con.close()
    return state


# ---------------------------------------------------------------------------
# the plugin's last verdict
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    """The plugin's most recent evaluation for a session."""

    ts: str
    usage_tok: int
    usage_pct: float
    growth: int
    ref: int
    interval: int
    pending_t1: int
    pending_thr: int
    model: str
    reason: str
    fired: str = ""  # "", "T1", "T2", "T3", "ESC"
    tier_pending: dict[str, int] = field(default_factory=dict)
    tier_idle: bool = False
    blocked_tier: str = ""


def last_verdict(analysis, session_id: str) -> Verdict | None:
    samples = [n for n in analysis.nudges if n.session == session_id]
    if not samples:
        return None
    n = samples[-1]
    fired = ""
    if n.fired_t1:
        fired = "T1"
    elif n.fired_t2:
        fired = "T2"
    elif n.fired_t3:
        fired = "T3"
    elif n.fired_esc:
        fired = "ESC"
    return Verdict(
        ts=n.ts,
        usage_tok=n.usage_tok,
        usage_pct=n.usage_pct,
        growth=n.growth,
        ref=n.ref,
        interval=n.interval,
        pending_t1=n.pending_t1,
        pending_thr=n.pending_thr,
        model=n.model,
        reason=n.reason,
        fired=fired,
        tier_pending=dict(n.tier_pending or {}),
        tier_idle=bool(n.tier_idle),
        blocked_tier=n.blocked_tier or "",
    )


def session_ids(analysis) -> list[str]:
    return sorted({n.session for n in analysis.nudges if n.session})


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    tier: int | None
    gates: list[str] = field(default_factory=list)
    t1_eff: int = 0
    t2_pen: int = 0
    t3_pen: int = 0
    pressure: bool = False
    blocked_by: str = ""

    @property
    def label(self) -> str:
        return f"T{self.tier}" if self.tier else "none"


def decide(  # noqa: C901
    bars: Bars,
    state: SessionState,
    verdict: Verdict | None,
    limit: int | None = None,
) -> Decision:
    """Mirror the plugin's rung selection.

    `t1_eff` is the plugin's own `pendingT1` (T1 effective mass) -- it is
    measured on the request's message window, which the session store does
    NOT reproduce. The tier pools ARE reproduced exactly.
    """
    t2_pen = state.pool(1)
    t3_pen = state.pool(2)
    t1_eff = verdict.pending_t1 if verdict else 0
    growth = verdict.growth if verdict else 0
    token_count = verdict.usage_tok if verdict else state.kstate_tokens
    gates: list[str] = []

    if verdict is None:
        return Decision(
            tier=None,
            gates=["no plugin verdict in the log for this session"],
            t1_eff=0,
            t2_pen=t2_pen,
            t3_pen=t3_pen,
            blocked_by="no verdict",
        )

    growth_ok = growth >= bars.growth_floor
    gates.append(
        f"growth {growth} >= floor {bars.growth_floor}: {'OK' if growth_ok else 'BLOCKED'}"
    )

    def cadence(tier: int) -> bool:
        shown = state.last_shown_by_tier.get(str(tier), 0)
        ok = (token_count - shown) >= bars.growth_floor
        gates.append(
            f"cadence T{tier}: tokenCount {token_count} - lastShown {shown} "
            f">= {bars.growth_floor}: {'OK' if ok else 'BLOCKED'}"
        )
        return ok

    # --- normal rung: strict else-if, T1 shadows T2 shadows T3 ------------
    tier: int | None = None
    if t1_eff >= bars.t1:
        gates.append(f"T1 size: t1Eff {t1_eff} >= bar {bars.t1}: OK")
        if growth_ok and cadence(1):
            tier = 1
    else:
        gates.append(f"T1 size: t1Eff {t1_eff} < bar {bars.t1}: no")
        if t2_pen >= bars.t2 and t2_pen > t1_eff:
            gates.append(f"T2 size: t2Pen {t2_pen} >= bar {bars.t2} and > t1Eff {t1_eff}: OK")
            if growth_ok and cadence(2):
                tier = 2
        else:
            gates.append(f"T2 size: t2Pen {t2_pen} vs bar {bars.t2}, t1Eff {t1_eff}: no")
            if t3_pen >= bars.t3 and t3_pen > t2_pen and t3_pen > t1_eff:
                gates.append(
                    f"T3 size: t3Pen {t3_pen} >= bar {bars.t3} and > t2Pen {t2_pen} "
                    f"and > t1Eff {t1_eff}: OK"
                )
                if growth_ok and cadence(3):
                    tier = 3
            else:
                gates.append(
                    f"T3 size: t3Pen {t3_pen} vs bar {bars.t3}, t2Pen {t2_pen}, t1Eff {t1_eff}: no"
                )

    # --- pressure branch: ignores the growth floor and the ordering ------
    pressure = False
    if tier is None and limit:
        usage = verdict.usage_tok / limit
        if usage >= EMERGENCY_PCT or usage >= MAX_CONTEXT_PCT:
            pressure = True
            benefit = max(5000, round(limit * 0.01))
            pools = {1: t1_eff, 2: t2_pen, 3: t3_pen}
            best = max(pools, key=lambda k: pools[k])
            gates.append(
                f"pressure: usage {usage:.0%} >= {MAX_CONTEXT_PCT:.0%}: "
                f"pick largest pool -> T{best} ({pools[best]})"
            )
            if pools[best] >= benefit:
                tier = best
            else:
                gates.append(f"pressure benefit {pools[best]} < minPressureBenefit {benefit}: no")

    blocked = ""
    if tier is None:
        if not growth_ok:
            blocked = "growth floor"
        elif t1_eff < bars.t1 and t2_pen < bars.t2 and t3_pen < bars.t3:
            blocked = "no tier over its bar"
        else:
            blocked = "ordering / cadence"

    return Decision(
        tier=tier,
        gates=gates,
        t1_eff=t1_eff,
        t2_pen=t2_pen,
        t3_pen=t3_pen,
        pressure=pressure,
        blocked_by=blocked,
    )


# ---------------------------------------------------------------------------
# nudge rendering
# ---------------------------------------------------------------------------


def _extract_template_literal(name: str) -> str:
    r"""Pull a `var NAME=`...`;` template literal out of the plugin bundle.

    The rules the plugin injects are long and change between versions;
    reading them from the INSTALLED bundle keeps this tool in sync with
    what would actually be injected.
    """
    if not BUNDLE.is_file():
        return ""
    try:
        src = BUNDLE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(rf"var {re.escape(name)}=`((?:[^`\\]|\\.)*)`;", src, re.S)
    if not m:
        return ""
    body = m.group(1)
    body = re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), body)
    body = body.replace("\\`", "`").replace("\\$", "$")
    return body.replace("\\n", "\n").replace("\\t", "\t")


def format_k(n: int) -> str:
    return f"{n / 1e3:.1f}K" if n >= 1000 else str(n)


def format_tier_target_blocks(blocks: list[Block], tier: int) -> str:
    if not blocks:
        return "Target blocks: (none — no tier blocks found)"
    lines = []
    for b in blocks:
        topic = f'  "{b.topic}"' if b.topic else ""
        lines.append(
            f"  {b.block_id}  {b.eff_ids} msgs  "
            f"{format_k(b.compressed_tokens)}→{format_k(b.summary_tokens)}{topic}"
        )
    return f"Target tier-{tier} blocks to distill ({len(blocks)}):\n" + "\n".join(lines)


def compress_call(block_ids: list[Block]) -> str:
    if not block_ids:
        return ""
    start = block_ids[0].block_id
    end = block_ids[-1].block_id
    return f'compress({{ content: [{{ startId: "{start}", endId: "{end}", summary: "..." }}] }})'


def render_nudge(
    tier: int,
    bars: Bars,
    state: SessionState,
    verdict: Verdict | None,
    decision: Decision,
    limit: int | None = None,
) -> str:
    """The nudge text, mirroring `renderNudgeText` in the plugin bundle."""
    philosophy = _extract_template_literal("COMPRESS_PHILOSOPHY")
    how_to = _extract_template_literal("HOW_TO_COMPRESS_RULES")
    t2_rules = _extract_template_literal("TIER2_DISTILL_RULES")
    t3_rules = _extract_template_literal("TIER3_CONDENSE_RULES")
    t2_guidance = _extract_template_literal("DEFAULT_T2_GUIDANCE")
    t3_guidance = _extract_template_literal("DEFAULT_T3_GUIDANCE")

    head = (
        "This is an efficiency nudge to compress early and keep context lean — "
        "not an overflow warning. A separate, stronger alert will appear if "
        "the context is actually full.\n\n" + philosophy
    )
    if verdict:
        breakdown = (
            f"Context breakdown: {format_k(verdict.usage_tok)} context\n"
            f"+{format_k(verdict.growth)} since last nudge"
        )
    else:
        breakdown = "Context breakdown: (no verdict in the log)"

    if tier >= 2:
        is_t2 = tier == 2
        targets = state.by_tier(1 if is_t2 else 2)
        guidance = t2_guidance if is_t2 else t3_guidance
        rules = t2_rules if is_t2 else t3_rules
        trigger = f"[TIER {tier} {'DISTILLATION' if is_t2 else 'CONDENSATION'} TRIGGER]"
        parts = [
            head,
            "",
            breakdown,
            "",
            trigger,
            "",
            guidance,
            "",
            format_tier_target_blocks(targets, 1 if is_t2 else 2),
            f"Example: {compress_call(targets)}",
            "",
            how_to,
            "",
            rules,
        ]
        return "\n".join(p for p in parts if p != "")

    # T1: list the compressible ranges and ask for ONE call.
    parts = [
        head,
        "",
        breakdown,
        "",
        how_to,
        "",
        "Only use IDs from visible messages above. Compress older work first.",
        "",
        "💡 If you compress, fold the ranges you keep in ONE call — pass multiple "
        "content entries (`content: [{...}, {...}]`) or ONE plain string holding "
        "every range, each block starting with its 'mNNNNN–mNNNNN topic' header "
        'line (a valid JSON string: " and \\n). Ranges the task still needs can '
        "wait — they reappear in later nudges.",
    ]
    return "\n".join(p for p in parts if p != "")


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def fmt_row(sid: str, verdict: Verdict | None, state: SessionState, dec: Decision) -> str:
    v = verdict
    return (
        f"{sid[-12:]:>12}  "
        f"t1eff={dec.t1_eff:>7,}  "
        f"t2pen={dec.t2_pen:>7,}  "
        f"t3pen={dec.t3_pen:>7,}  "
        f"growth={v.growth if v else 0:>7,}  "
        f"usage={v.usage_pct if v else 0:>5.1f}%  "
        f"-> {dec.label}"
        + (f"  ({dec.blocked_by})" if dec.tier is None and dec.blocked_by else "")
        + (f"  [plugin: {v.blocked_tier}]" if v and v.blocked_tier else "")
    )


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    ap = argparse.ArgumentParser(
        description="Manual tier-ladder nudge for a billion-context session."
    )
    ap.add_argument("--session", help="session id (default: every session in the log)")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG, help="bili log path")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="session store path")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="plugin config")
    ap.add_argument("--nudge", action="store_true", help="print the nudge text")
    ap.add_argument(
        "--force-tier",
        type=int,
        choices=(1, 2, 3),
        help="render this rung's nudge even if its gates do not pass",
    )
    ap.add_argument("--write", type=Path, help="write the nudge text to this path")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--gates", action="store_true", help="print every gate and its verdict")
    args = ap.parse_args(argv)

    bars = Bars.from_file(args.config)
    paths = log_rotation_family(args.log)
    analysis = parse_logs(paths)

    if args.session:
        ids = [args.session]
    else:
        ids = session_ids(analysis)

    out: list[dict] = []
    for sid in ids:
        verdict = last_verdict(analysis, sid)
        state = load_session_state(args.db, sid)
        limit = None
        if verdict:
            limit = _limit_from_verdict(verdict)
        dec = decide(bars, state, verdict, limit)
        out.append(
            {
                "session": sid,
                "verdict": verdict,
                "state": state,
                "decision": dec,
                "limit": limit,
            }
        )

    if args.json:
        payload = []
        for row in out:
            v: Verdict | None = row["verdict"]
            st: SessionState = row["state"]
            dec: Decision = row["decision"]
            payload.append(
                {
                    "session": row["session"],
                    "bars": {
                        "t1": bars.t1,
                        "t2": bars.t2,
                        "t3": bars.t3,
                        "growth_floor": bars.growth_floor,
                        "nudge_growth_tokens": bars.nudge_growth,
                    },
                    "last_verdict": (
                        {
                            "ts": v.ts,
                            "usage_tok": v.usage_tok,
                            "usage_pct": v.usage_pct,
                            "growth": v.growth,
                            "ref": v.ref,
                            "pending_t1": v.pending_t1,
                            "model": v.model,
                            "reason": v.reason,
                            "fired": v.fired,
                            "plugin_blocked": v.blocked_tier,
                        }
                        if v
                        else None
                    ),
                    "pools": {
                        "t1_eff": dec.t1_eff,
                        "t2_pen": dec.t2_pen,
                        "t3_pen": dec.t3_pen,
                    },
                    "decision": {
                        "tier": dec.tier,
                        "label": dec.label,
                        "blocked_by": dec.blocked_by,
                        "pressure": dec.pressure,
                        "gates": dec.gates,
                    },
                    "answers": {
                        # the exact compress(...) call for each rung that has
                        # target blocks — the agent needs nothing else
                        str(t): compress_call(st.by_tier(1 if t == 2 else 2))
                        for t in (2, 3)
                        if st.by_tier(1 if t == 2 else 2)
                    },
                    "blocks": {
                        "tier1": [
                            {
                                "id": b.block_id,
                                "topic": b.topic,
                                "summary_tokens": b.summary_tokens,
                                "compressed_tokens": b.compressed_tokens,
                                "eff_ids": b.eff_ids,
                            }
                            for b in st.by_tier(1)
                        ],
                        "tier2": [
                            {
                                "id": b.block_id,
                                "topic": b.topic,
                                "summary_tokens": b.summary_tokens,
                                "compressed_tokens": b.compressed_tokens,
                                "eff_ids": b.eff_ids,
                            }
                            for b in st.by_tier(2)
                        ],
                        "tier3": [
                            {
                                "id": b.block_id,
                                "topic": b.topic,
                                "summary_tokens": b.summary_tokens,
                                "compressed_tokens": b.compressed_tokens,
                                "eff_ids": b.eff_ids,
                            }
                            for b in st.by_tier(3)
                        ],
                    },
                }
            )
        payload_text = json.dumps(payload, indent=2)
        print(payload_text)
        if args.write:
            args.write.parent.mkdir(parents=True, exist_ok=True)
            args.write.write_text(payload_text + "\n", encoding="utf-8")
            print(f"written: {args.write}", file=sys.stderr)
        if args.nudge and args.session:
            # the nudge text still goes to stdout; the file already holds JSON
            return emit_nudge(args, bars, out[0], write_text=False)
        return 0

    if not args.session:
        print(
            f"bars: t1={bars.t1:,} t2={bars.t2:,} t3={bars.t3:,} "
            f"growthFloor={bars.growth_floor:,} (nudgeGrowthTokens="
            f"{bars.nudge_growth:,})"
        )
        print()
        for row in out:
            print(
                fmt_row(
                    row["session"],
                    row["verdict"],
                    row["state"],
                    row["decision"],
                )
            )
        return 0

    row = out[0]
    v = row["verdict"]
    st = row["state"]
    dec = row["decision"]

    print(f"session {row['session']}")
    print(
        f"bars  t1={bars.t1:,}  t2={bars.t2:,}  t3={bars.t3:,}  growthFloor={bars.growth_floor:,}"
    )
    for k, v_src in bars.source.items():
        print(f"      {k}: {v_src}")
    if v:
        print(
            f"plugin {v.ts}  usage={v.usage_tok:,} ({v.usage_pct:.1f}%)  "
            f"growth={v.growth:,}  ref={v.ref:,}  t1eff={v.pending_t1:,}  "
            f"model={v.model}  fired={v.fired or '-'}"
        )
        print(f"       reason: {v.reason}")
        if v.blocked_tier:
            print(f"       plugin blocked: {v.blocked_tier}")
    print(f"pools  t1eff={dec.t1_eff:,}  t2pen={dec.t2_pen:,}  t3pen={dec.t3_pen:,}")
    for tier in (1, 2, 3):
        blocks = st.by_tier(tier)
        if blocks:
            print(f"  tier-{tier} blocks ({len(blocks)}):")
            for b in blocks:
                topic = f'  "{b.topic}"' if b.topic else ""
                print(
                    f"    {b.block_id}  {b.eff_ids} msgs  "
                    f"{format_k(b.compressed_tokens)}→{format_k(b.summary_tokens)}"
                    f"{topic}"
                )
    print(f"decision: {dec.label}" + (f"  ({dec.blocked_by})" if dec.blocked_by else ""))
    if args.gates:
        for g in dec.gates:
            print(f"    {g}")

    if args.nudge:
        return emit_nudge(args, bars, row, write_text=True)
    return 0


def emit_nudge(args, bars: Bars, row: dict, write_text: bool = True) -> int:
    """Print the nudge text and the exact `compress(...)` call that answers it.

    Returns 1 when no rung is ready (and none was forced) — the honest
    answer, since the plugin would stay idle in that state.
    """
    st: SessionState = row["state"]
    dec: Decision = row["decision"]
    v: Verdict | None = row["verdict"]
    tier = args.force_tier or dec.tier
    if tier is None:
        print()
        print(
            f"no nudge: {dec.blocked_by} -- the plugin would stay idle here.",
            file=sys.stderr,
        )
        return 1
    text = render_nudge(tier, bars, st, v, dec, row["limit"])
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)
    targets = st.by_tier(1 if tier == 2 else 2)
    answer = ""
    if tier >= 2 and targets:
        answer = f"answer with: {compress_call(targets)}"
        print()
        print(answer)
    if write_text and args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        body = text + "\n"
        if answer:
            body += "\n" + answer + "\n"
        args.write.write_text(body, encoding="utf-8")
        print(f"written: {args.write}")
    return 0


def _limit_from_verdict(v: Verdict) -> int | None:
    """The context limit, recovered from the log's `usage=x% (N/L)` pair."""
    if v.usage_pct and v.usage_tok:
        return int(round(v.usage_tok / (v.usage_pct / 100.0)))
    m = re.search(r"usage=\d+% \((\d+)/(\d+)\)", v.reason)
    if m:
        return int(m.group(2))
    return None


if __name__ == "__main__":
    raise SystemExit(main())
