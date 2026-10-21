#!/usr/bin/env python3
"""Live watcher for the compress tier ladder -- read-only, writes nothing.

The tier ladder (T1 fold -> T2 distill -> T3 condense) is decided by the
plugin on the request path, where the model context is visible. When a rung
is armed but never fires, the log alone does not say why:

  * the plugin's `ready:` hint lists only the tiers that are OVER their bar,
    so a pool under the bar is invisible there;
  * the log prints `pendingT1=N/interval`, where `interval` is
    `nudgeGrowthTokens` -- NOT the T1 bar when `tierNudgeTokens.t1` is set;
  * a rung blocked by the ORDERING test (`t2Pen > t1Eff`) or by the growth
    floor looks exactly like an idle session.

This watcher answers that question live, from the same three sources the
plugin uses:

  * the live log tail (rotation-safe) -- the plugin's own last verdict per
    session: `pendingT1` (T1 effective mass), growth, usage, and the
    `INJECT T1/T2/T3/ESC` fires;
  * the session store (`sessions/index.db`) -- the tier pools, EXACTLY, as
    the sum of summary tokens over the active tier-N blocks. The block
    store is the only honest source for an under-bar pool;
  * the config file -- the three bars and the growth floor, RE-READ AT
    EVERY SAMPLE so a hot config change is picked up without a restart
    (the proxy re-reads its config live; a watcher that caches it reports
    bars the plugin stopped using).

The rung selection itself is NOT re-implemented here: it is delegated to
`bili_tier_nudge.decide()`, so the watcher and the manual nudge can never
disagree about why a rung did or did not fire.

Heartbeat (one line per sample):

    <ts> sess=<sid> ctx= 66.9k growth= 15728/20000  t1eff= 13.7k/40.0k
    t2pen= 13.2k/5.0k  t3pen=     0/3.0k  ord2=N ord3=N
    fires T1=38 T2=5 T3=0 esc=9 folds=52 saved=2.36M blocks=23
    blocked[1:79,2:27] STRANDED log_age=2508s | <plugin reason>

`STRANDED` is the diagnosis this watcher exists for: T1 is under its bar
(so it cannot fire) while the tier-2 pool is not above the T1 effective
mass (so T2 cannot fire either) -- the ladder sits idle until a T1 fold
drains the fresh mass and manufactures T2's precondition.

Usage:
    python helpers/analytics/bili_tier_watch.py                       # 1 hour
    python helpers/analytics/bili_tier_watch.py --seconds 600 --sample-secs 30
    python helpers/analytics/bili_tier_watch.py --once --json
    python helpers/analytics/bili_tier_watch.py --session ses_abc123

Paths are discovered, not hardcoded (`bili_logpath` walks /proc for the
running proxy); override with `BILI_LOG` / `--config` / `--db`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.analytics import bili_logpath, bili_tier_nudge as btn  # noqa: E402

DEFAULT_DB = Path.home() / ".local/share/billion-context/sessions/index.db"
DEFAULT_CONFIG = Path.home() / ".config/billion-context/billion-context.json"
SEED_BYTES = 8 * 1024 * 1024
SAMPLE_SECS = 60
WATCH_SECS = 3600

RE_NUDGE = re.compile(
    r"nudge (?P<kind>idle|INJECT(?: T[123?])?): "
    r"usage=(?P<pct>[\d.]+)% \((?P<tok>\d+)/(?P<win>\d+)\), "
    r"growth=(?P<growth>-?\d+)/(?P<floor>\d+) "
    r"\(ref=(?P<ref>\d+), interval=(?P<interval>\d+)\), "
    r"pendingT1=(?P<pt1>\d+)/(?P<pthr>\d+)"
    r"(?: model=(?P<model>[^,]+),)? .*?reason=\"(?P<reason>[^\"]*)\""
)
RE_BLOCKED = re.compile(r"blocked:\s*T([123?])\b")
RE_FOLD = re.compile(
    r"\[acp-compress-obs\] shrink (\d+)% \(~(\d+)/(\d+) tok\)(?: foldPoint=(\S+))?"
)
RE_INJECT = re.compile(r"nudge INJECT(?:-ESC)? T([123?]):")
RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
# The log carries the session id in TWO shapes, and a line-scoped filter
# must recognise BOTH or it silently drops every fold:
#   nudge lines:  [...][sess=ses_XXX][...][ses_XXX] nudge INJECT T1
#   fold  lines:  [...][v=0.1.202][ses_XXX][plugin] [acp-compress-obs] ...
# The old `\[sess=(\S+?)\]` matched only the nudge form, so fold lines
# yielded an empty session id and were discarded — the watcher reported
# folds=0 while the log plainly contained them. The bare form must be
# anchored to the `ses_` prefix so it cannot swallow `[info]`/`[plugin]`.
RE_SESS = re.compile(r"\[sess=([A-Za-z0-9_]+)\]|\[(ses_[A-Za-z0-9_]+)\]")


def line_session(line: str) -> str:
    """The session id carried by a log line, or '' when it carries none."""
    m = RE_SESS.search(line)
    if not m:
        return ""
    return m.group(1) or m.group(2) or ""


def fmt(n: int) -> str:
    """1234 -> '1.2k', 1234567 -> '1.23M'."""
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def now_ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# rotation-safe log tail
# ---------------------------------------------------------------------------


class Tail:
    """Incremental tail over a log file that may be rotated under us.

    v1 of this watcher (scratch, never committed) kept a byte offset into
    `bili.log`; when the log rotated (`bili.log` -> `bili.log.old`, the new
    file SHORTER than the offset) every poll read zero bytes and the watcher
    re-printed the same stale sample for an hour. Rotation is detected by
    (st_dev, st_ino) plus a shrinking size; on rotation the offset resets
    and the whole new file is re-read.

    Re-reading is safe because the CONSUMER is idempotent: fires and folds
    are counted from sets of (timestamp, tier) / timestamp keys, and a
    verdict is applied only when it is not older than the newest verdict
    already held for that session.
    """

    def __init__(self, log: Path, old: Path | None = None, seed_bytes: int = SEED_BYTES):
        self.log = log
        self.old = old
        self.seed_bytes = seed_bytes
        self.offset = 0
        self.ino: int | None = None
        self.rotations = 0

    def _tail(self, path: Path) -> str:
        try:
            with path.open("rb") as fh:
                fh.seek(0, 2)
                end = fh.tell()
                fh.seek(max(0, end - self.seed_bytes))
                data = fh.read()
        except OSError:
            return ""
        if path == self.log:
            self.offset = end
            try:
                self.ino = path.stat().st_ino
            except OSError:
                self.ino = None
        return data.decode("utf-8", "replace")

    def seed(self) -> str:
        """The existing tail of the family: rotated file first, then live."""
        parts = []
        if self.old is not None and self.old.is_file():
            parts.append(self._tail(self.old))
        parts.append(self._tail(self.log))
        return "".join(parts)

    def poll(self) -> str:
        """Bytes appended since the last poll; re-seeds after a rotation."""
        try:
            st = self.log.stat()
        except OSError:
            return ""
        if self.ino is not None and (st.st_ino != self.ino or st.st_size < self.offset):
            self.rotations += 1
            self.offset = 0
        try:
            with self.log.open("rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
                self.offset = fh.tell()
        except OSError:
            return ""
        if self.ino is None:
            self.ino = st.st_ino
        return data.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# the watch state
# ---------------------------------------------------------------------------


@dataclass
class Watch:
    """Live tier-ladder state, accumulated from the log tail."""

    session: str = ""
    verdicts: dict[str, btn.Verdict] = field(default_factory=dict)
    windows: dict[str, int] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    fires: Counter = field(default_factory=Counter)
    folds: int = 0
    saved: int = 0
    blocked: Counter = field(default_factory=Counter)
    _seen_fires: set[tuple[str, str]] = field(default_factory=set)
    _seen_folds: set[tuple[str, str, str]] = field(default_factory=set)
    pending_injects: list[dict] = field(default_factory=list)
    _seen_injects: set[tuple[str, str]] = field(default_factory=set)
    inject_packets_written: set[str] = field(default_factory=set)
    db: Path = field(default=DEFAULT_DB)
    inject_packet_path: str | None = None

    def touch(self, sid: str) -> None:
        if sid and sid not in self.verdicts:
            self.order.append(sid)

    @property
    def current_session(self) -> str:
        """The session of the most recent verdict (the newest one seen)."""
        return self.order[-1] if self.order else ""

    def consume(self, text: str) -> None:
        """Fold log text into the state. Idempotent: re-reading is safe."""
        for line in text.splitlines():
            tm = RE_TS.match(line)
            ts = tm.group(1) if tm else ""
            if "nudge " in line:
                self._consume_nudge(line, ts)
            if "[acp-compress-obs]" in line and ts:
                self._consume_fold(line, ts)

    def _consume_nudge(self, line: str, ts: str) -> None:
        m = RE_NUDGE.search(line)
        if m and ts.startswith("20"):
            sid = line_session(line)
            self.touch(sid)
            prev = self.verdicts.get(sid)
            if prev is None or ts >= prev.ts:
                reason = m.group("reason")
                blocked = RE_BLOCKED.search(reason)
                self.verdicts[sid] = btn.Verdict(
                    ts=ts,
                    usage_tok=int(m.group("tok")),
                    usage_pct=float(m.group("pct")),
                    growth=int(m.group("growth")),
                    ref=int(m.group("ref")),
                    interval=int(m.group("interval")),
                    pending_t1=int(m.group("pt1")),
                    pending_thr=int(m.group("pthr")),
                    model=(m.group("model") or "").strip(),
                    reason=reason,
                    tier_pending={},
                    blocked_tier=(blocked.group(1) if blocked else ""),
                )
                self.windows[sid] = int(m.group("win"))
                if blocked:
                    self.blocked[blocked.group(1)] += 1
        mi = RE_INJECT.search(line)
        if mi:
            sid = line_session(line)
            if self.session and sid != self.session:
                return
            tier = mi.group(1)
            key = (ts, tier)
            if key in self._seen_injects:
                return
            self._seen_injects.add(key)
            tier_name = {"1": "T1", "2": "T2", "3": "T3"}.get(tier, "ESC")
            self.fires[tier_name] += 1
            if tier in ("2", "3") and self.inject_packet_path:
                self._emit_inject_packet(sid, tier, ts)

    def _consume_fold(self, line: str, ts: str) -> None:
        # The dedup key must carry the session AND the fold point, not the
        # timestamp alone: the plugin can emit several folds inside one
        # second (different fold points), and a timestamp-only key
        # collapsed them into one.
        sid = line_session(line)
        mf = RE_FOLD.search(line)
        point = mf.group(4) if mf and len(mf.groups()) >= 4 else ""
        key = (ts, sid, point)
        if key in self._seen_folds:
            return
        if self.session and sid != self.session:
            return
        self._seen_folds.add(key)  # type: ignore[arg-type]
        if mf:
            self.folds += 1
            self.saved += int(mf.group(2))

    def _emit_inject_packet(self, sid: str, tier: str, ts: str) -> None:
        """Render a paste-ready `compress({...})` packet for an INJECT T2/T3.

        T2 distills tier-1 blocks into one tier-2 block; T3 condenses tier-2
        blocks into one tier-3 block. The endpoints are block IDs (`bN`),
        which is what makes the fold climb one rung instead of recycling the
        pool at tier 1. Writes one JSON object per inject to
        `self.inject_packet_path` (append mode, idempotent by tier).
        """
        state = btn.load_session_state(self.db, sid)
        tier_int = int(tier)

        # T2 distills tier-1 blocks; T3 condenses tier-2 blocks.
        source_tier = tier_int - 1
        blocks = state.by_tier(source_tier)
        if not blocks:
            return

        packet = {
            "session": sid,
            "tier": tier_int,
            "tier_name": f"T{tier_int}",
            "ts": ts,
            "blocks": [
                {
                    "block_id": b.block_id,
                    "topic": b.topic,
                    "summary_tokens": b.summary_tokens,
                    "compressed_tokens": b.compressed_tokens,
                    "eff_ids": b.eff_ids,
                }
                for b in blocks
            ],
            "pool_tokens": state.pool(source_tier),
            "compress": {
                "startId": blocks[0].block_id,
                "endId": blocks[-1].block_id,
                "summary": f"{'Distill' if tier_int == 2 else 'Condense'} "
                f"{len(blocks)} {source_tier}-block{'s' if len(blocks) > 1 else ''} "
                f"into a T{tier_int} block",
            },
        }

        key = f"{sid}-T{tier_int}"
        if key in self.inject_packets_written:
            return
        self.inject_packets_written.add(key)

        packet_path = self.inject_packet_path
        if not packet_path:
            return
        path = Path(packet_path)
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(packet, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"# inject packet write failed: {exc}", file=sys.stderr, flush=True)


@dataclass
class Sample:
    """One heartbeat: the state of one session at one instant."""

    ts: str
    sampled_at: str
    session: str
    ctx: int
    limit: int
    growth: int
    floor: int
    t1eff: int
    bar1: int
    t2pen: int
    bar2: int
    t3pen: int
    bar3: int
    ord2: bool
    ord3: bool
    growth_ok: bool
    flags: list[str]
    fires: dict
    folds: int
    saved: int
    blocks: int
    blocked: dict
    reason: str
    tier: int | None
    blocked_by: str
    log_age: int | None

    def render(self) -> str:
        age = f" log_age={self.log_age}s" if self.log_age is not None else ""
        blk = ",".join(f"{k}:{v}" for k, v in sorted(self.blocked.items()))
        fires = self.fires
        return (
            f"{self.sampled_at} vts={self.ts} sess={self.session or '-'} "
            f"ctx={fmt(self.ctx):>7} "
            f"growth={self.growth:>6}/{self.floor:<6} "
            f"t1eff={fmt(self.t1eff):>6}/{fmt(self.bar1):<6} "
            f"t2pen={fmt(self.t2pen):>6}/{fmt(self.bar2):<5} "
            f"t3pen={fmt(self.t3pen):>6}/{fmt(self.bar3):<5} "
            f"ord2={'Y' if self.ord2 else 'N'} ord3={'Y' if self.ord3 else 'N'} "
            f"fires T1={fires.get('T1', 0)} T2={fires.get('T2', 0)} "
            f"T3={fires.get('T3', 0)} esc={fires.get('ESC', 0)} "
            f"folds={self.folds} saved={fmt(self.saved)} blocks={self.blocks} "
            f"blocked[{blk}] {' '.join(self.flags) or '-'} rung={self.tier or '-'}"
            f" blocked_by={self.blocked_by or '-'}{age} | {self.reason[:90]}"
        )

    def as_dict(self) -> dict:
        return {
            "ts": self.ts,
            "sampled_at": self.sampled_at,
            "session": self.session,
            "ctx": self.ctx,
            "limit": self.limit,
            "growth": self.growth,
            "growth_floor": self.floor,
            "t1_eff": self.t1eff,
            "t2_pen": self.t2pen,
            "t3_pen": self.t3pen,
            "bars": {"t1": self.bar1, "t2": self.bar2, "t3": self.bar3},
            "ordering": {"ord2": self.ord2, "ord3": self.ord3},
            "flags": self.flags,
            "fires": dict(self.fires),
            "folds": self.folds,
            "saved": self.saved,
            "blocks": self.blocks,
            "blocked": dict(self.blocked),
            "rung": self.tier,
            "blocked_by": self.blocked_by,
            "log_age": self.log_age,
            "reason": self.reason,
        }


def build_sample(
    watch: Watch,
    bars: btn.Bars,
    db: Path,
    session: str | None = None,
) -> Sample:
    """One heartbeat for one session, from the live state + the block store."""
    sid = session or watch.current_session
    verdict = watch.verdicts.get(sid)
    state = btn.load_session_state(db, sid) if sid else btn.SessionState(session_id="")
    limit = watch.windows.get(sid, 0)
    dec = btn.decide(bars, state, verdict, limit or None)

    t1_eff = verdict.pending_t1 if verdict else 0
    growth = verdict.growth if verdict else 0
    growth_ok = growth >= bars.growth_floor
    ord2 = dec.t2_pen > t1_eff
    ord3 = dec.t3_pen > dec.t2_pen and dec.t3_pen > t1_eff

    flags = []
    if growth_ok:
        flags.append("GROWTH-OK")
    if t1_eff >= bars.t1:
        flags.append("T1-ELIGIBLE")
    if dec.t2_pen >= bars.t2 and ord2 and growth_ok:
        flags.append("T2-ARMED")
    if dec.t3_pen >= bars.t3 and ord3 and growth_ok:
        flags.append("T3-ARMED")
    if t1_eff < bars.t1 and dec.t2_pen > 0 and dec.t2_pen <= t1_eff and dec.t3_pen <= dec.t2_pen:
        flags.append("STRANDED")

    age = None
    if verdict and verdict.ts:
        try:
            t = datetime.strptime(verdict.ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
            age = int(time.time() - t.timestamp())
        except ValueError:
            age = None

    return Sample(
        ts=verdict.ts if verdict else now_ts(),
        sampled_at=now_ts(),
        session=sid,
        ctx=verdict.usage_tok if verdict else 0,
        limit=limit,
        growth=growth,
        floor=bars.growth_floor,
        t1eff=t1_eff,
        bar1=bars.t1,
        t2pen=dec.t2_pen,
        bar2=bars.t2,
        t3pen=dec.t3_pen,
        bar3=bars.t3,
        ord2=ord2,
        ord3=ord3,
        growth_ok=growth_ok,
        flags=flags,
        fires=dict(watch.fires),
        folds=watch.folds,
        saved=watch.saved,
        blocks=len(state.active),
        blocked=dict(watch.blocked),
        reason=verdict.reason if verdict else "",
        tier=dec.tier,
        blocked_by=dec.blocked_by,
        log_age=age,
    )


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    ap = argparse.ArgumentParser(description="Watch the compress tier ladder live (read-only).")
    ap.add_argument("--seconds", type=int, default=WATCH_SECS, help="watch duration; 0 = forever")
    ap.add_argument("--sample-secs", type=int, default=SAMPLE_SECS, help="heartbeat period")
    ap.add_argument("--seed-bytes", type=int, default=SEED_BYTES, help="bytes of log to seed from")
    ap.add_argument("--session", default=None, help="pin a session id (default: the newest seen)")
    ap.add_argument("--once", action="store_true", help="one sample, then exit")
    ap.add_argument("--json", action="store_true", help="emit samples as JSON")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="plugin config file")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="session store")
    ap.add_argument(
        "--inject-packet",
        default=None,
        help="append a paste-ready compress({...}) packet here for each new INJECT T2/T3",
    )
    args = ap.parse_args(argv)

    log, old = bili_logpath.discover_all()
    log_path = Path(log)
    old_path = Path(old)
    if not log_path.exists():
        print(f"FATAL: {log_path} not found", file=sys.stderr)
        return 1

    tail = Tail(log_path, old_path, args.seed_bytes)
    watch = Watch(session=args.session or "", db=args.db)
    watch.inject_packet_path = args.inject_packet
    watch.consume(tail.seed())
    bars = btn.Bars.from_file(args.config)

    if not args.json:
        print(
            f"# watching {log_path} (seeded {args.seed_bytes} B) "
            f"bar1={bars.t1} bar2={bars.t2} bar3={bars.t3} floor={bars.growth_floor} "
            f"sample every {args.sample_secs}s for {args.seconds}s",
            flush=True,
        )

    def emit() -> None:
        # Re-read the config EVERY sample: the proxy re-reads its config
        # live, so a cached bar would report thresholds the plugin stopped
        # using.
        bars_now = btn.Bars.from_file(args.config)
        sample = build_sample(watch, bars_now, args.db, args.session)
        if args.json:
            print(json.dumps(sample.as_dict(), indent=2), flush=True)
        else:
            print(sample.render(), flush=True)

    if args.once:
        emit()
        return 0

    deadline = time.time() + args.seconds if args.seconds else None
    last = 0.0
    while deadline is None or time.time() < deadline:
        time.sleep(2)
        try:
            text = tail.poll()
        except OSError as exc:  # keep watching through transient errors
            print(f"# poll error: {exc}", file=sys.stderr, flush=True)
            continue
        if text:
            watch.consume(text)
        now = time.time()
        if now - last >= args.sample_secs:
            last = now
            emit()
    if not args.json:
        print("# watcher exit (max duration reached)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
