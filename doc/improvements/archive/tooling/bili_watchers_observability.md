---
title: "Bili watcher suite — live tuning observability for the billion-context proxy"
status: executed
filed: '2026-10-18'
executed: '2026-10-18'
completed_md: '414'
area: helpers/analytics
---

# Bili watcher suite — live tuning observability for the billion-context proxy

## 1. TL;DR

The tuning question ("is the nudge interval right?") had no live instrument.
`bili_sessions.py` could tell you what happened after the fact; nothing could
watch the proxy while it ran. This adds a read-only watcher suite —
`bili_tunewatch.py` (scan + report), `bili_monitor.py` (continuous),
`bili_watch_event.py` (block until next fold/T1), `bili_thresh_replay.py`
(threshold replay), plus `bili_status.sh` / `bili_stop_monitor.sh`. The single
most valuable correction in the suite is that the proxy's own
`[acp-compress-obs]` shrink% understates the real context drop badly (measured
4% vs 28.5%); every fold is now reported with the real drop from the
`[acp-usage]` input= lines.

## 2. Motivation

The four-session tuning review (2026-10-18) reached a conclusion — "raise
`nudgeGrowthTokens`" — from aggregate fold density, but could not separate
interval effects from model-switch noise, and had no way to watch a fold
happen. The watcher suite fills that gap.

## 3. Slices

- **S1 — `bili_tunewatch.py`**: one-pass scan of the log family; per-session
  eras (interval / pendingT1 / growth floor / nudges / fires / folds), every
  fold with real context drop and burn delta, per-session quota burn
  (cached×RATE + uncached), last-N usage rows.
- **S2 — `bili_monitor.py`**: continuous watcher. Baseline-vs-delta design —
  the first scan is a baseline (events already in the log are counted and
  never re-logged); only new folds/injections after monitor start go to
  `events.log`. Snapshots + burn time series, heartbeat, clean exit marker.
- **S3 — `bili_watch_event.py`**: one-shot "block until the next fold/T1 for
  this session", then report.
- **S4 — `bili_thresh_replay.py`**: replay every nudge evaluation against a
  range of T1 thresholds — the direct instrument for the tuning decision.
- **S5 — `bili_status.sh` / `bili_stop_monitor.sh`**: human-facing status and
  the safe stop (never `pkill -f`, which matches the calling shell's argv).

## 4. Acceptance criteria

1. All tools run read-only; no writes into the repo.
2. `bili_tunewatch.py report <sid>` produces era/fold/burn/usage summary.
3. `bili_monitor.py` starts, polls, writes heartbeat, exits cleanly.
4. `bili_thresh_replay.py` replays thresholds and reports fire distribution.
5. `bili_stop_monitor.sh` stops the monitor without killing the caller.

## 5. Log path discovery

The proxy is an npm module; its cwd (and thus `bili.log`) lives wherever it was
started from. `bili_logpath.py` walks `/proc` for the running proxy and reads
its cwd, falling back to known state roots. `BILI_LOG` / `BILI_LOG_OLD`
override. The suite no longer hardcodes `/home/arun/.local/state/...`.

## 6. Post-landing fix (acceptance smoke)

`bili_watch_event.py` ran its 45 s follow-on collection unconditionally after
the scan loop, though its own docstring scopes that wait to "on the event" —
a no-event `max_wait=3` run took ~48 s (3 s scan + 45 s idle tail). Fixed by
gating the tail collection on `hits` (`bili_watch_event.py:76-86`); no-event
latency is now ~10 s (the residual is the scan loop's coarse `sleep(10)`
poll overshoot). Amended into the same patch (`bili_analytics`).

Acceptance smoke (2026-10-18, read-only): `bili_tunewatch.py report` — eras,
folds with real-vs-observed drop (81.7% vs 31.0% on the first fold of
`ses_ec7af063cffew2a8xvEcV142n6`); `bili_thresh_replay.py` — 3,043 nudge
evaluations / 10 sessions replayed across 7 thresholds;
`bili_monitor.py 5 2 3` — exit 0, HEARTBEAT + monitor.exit + events.log +
snapshots.jsonl + burn.tsv, clean `monitor done` summary;
`bili_stop_monitor.sh` — TERM stop, monitor exit 143, calling shell unharmed;
`ruff check` clean on the touched file.

## 7. S6 — `bili_tier_watch.py` (the ladder watcher, 2026-10-20)

The scratch watcher that answered "why is a rung that is over its bar not
firing?" during the tier-ladder arc was never committed — it lived only in
`/mnt/data/tmp/opencode/tier_watch.py`, so a scratch-dir sweep destroyed it.
It is now a repo tool: `helpers/analytics/bili_tier_watch.py`, a sibling of
`bili_tier_nudge.py` (the read-only manual nudge) in the same suite.

What it adds over the rest of the suite: the suite could say *that* a tier
fired; this one says *why* a tier did not. Per sample it prints the bars,
the three pools, the two ordering tests, the growth floor and the cadence
gates, and names the blocking reason:

    sess=ses_eb005c4d7… ctx=81.7k growth=-2650/20000 t1eff=3.0k/30.0k
    t2pen=1.2k/38.0k t3pen=2.7k/45.0k ord2=N ord3=N fires T1=40 T2=5 T3=0
    esc=9 folds=54 saved=2.43M blocks=7 blocked[1:79,2:27] - rung=-
    blocked_by=growth floor log_age=239s | max compressible 2957 < threshold
    30000; growth -2650 < floor 20000

The pools come from the session store's block tiers, not from the log: the
plugin's `ready:` hint lists only the tiers that are OVER their bar, so an
under-bar pool is invisible in the log. The config is re-read per sample
(the proxy re-reads its config live, so a cached bar goes stale).

Rotation-safe tail: the v1 scratch version kept a byte offset into the log
and, after the log rotated to a SHORTER file, read nothing forever. The repo
version re-seeds on inode change or size shrink and counts a re-scanned line
once (idempotent consumption), so a rotation cannot inflate the fire and
fold counters.

Usage: `make tier-watch` (continuous), `WATCH_ARGS="--once"` (one sample),
`--json` (machine-readable), `--config` / `--db` to point at other files.
Read-only; writes nothing into the repo. Tests:
`tests/test_bili_tier_watch.py` (20 tests) pin the rotation re-seed, the
idempotent re-scan, the verdict parse, the three armed/stranded flags
(`T1-ELIGIBLE`, `T2-ARMED`, `T3-ARMED`, `STRANDED`) and the CLI.

## 8. Suite cleanup (2026-10-20)

The suite was audited tool by tool against one question — *does this still do
something no sibling does?* — and the answer was yes for all of them, so
**nothing was deleted**. `bili_tunewatch.py` (scan + eras + burn) is the
library under both `bili_monitor.py` and `bili_watch_event.py`;
`bili_thresh_replay.py` is the only offline fire-curve instrument;
`bili_status.sh` / `bili_stop_monitor.sh` are the human entry point and the
safe stop. What was broken was not the inventory but the reachability and two
defects:

1. **The status report showed the wrong bar.** `bili_status.sh` scraped
   `interval=` out of the log to report the "effective policy" — but the
   plugin prints `compress.nudgeGrowthTokens` there, NOT
   `compress.tierNudgeTokens.t1`. With an explicit T1 bar configured, the
   report showed a bar the proxy was not using. The script now reads the
   config through `bili_tier_nudge.Bars` (the same resolver the proxy uses)
   and prints the bars with their provenance (`file` / `fallback
   nudgeGrowthTokens` / `hard 20000`), and labels the log line for what it
   is: the nudge's own denominator, not the T1 bar.
2. **`bili_monitor.py --help` crashed.** Its positional arguments go straight
   into `int()`, so `--help` died with a bare `ValueError` traceback — the
   first thing one tries on a long-running watcher. It now prints usage and
   exits 0; a non-numeric argument prints the usage to stderr and exits 2.

Two duplications went away with those fixes: the shell twin of the log
discovery (it globbed `/proc` in LEXICAL pid order, so the two readers could
disagree about which log was live — `bili_status.sh` now calls
`bili_logpath.discover_all()`, one implementation, one answer) and the
status script's exit code, which used to be whatever the last pipeline
returned (`tail`'s rc=1 when the watch dir has no `events.log` yet, which made
`make bili-status` look like a failure; a report now always exits 0 and
reports findings as lines).

Every tool now has a Makefile entry point, which is what made the suite
discoverable rather than a pile of scripts nobody ran:

    make bili-status          # the human report (bars, injections, folds, burn)
    make bili-monitor         # background monitor (DURATION_S/POLL_S/SNAP_S)
    make bili-stop-monitor    # the safe stop (never `pkill -f`)
    make bili-report          # economics report from the watcher state dir
    make bili-snap            # snapshot the proxy state now
    make bili-watch-event     # block until the next T1 fire / fold
    make bili-thresh-replay   # replay the fire curve offline

Tests: `tests/test_bili_status_script.py` (6 tests) pins the config-derived
bars, the `nudgeGrowthTokens` fallback, the hard 20,000 growth floor, the
labelling of the log denominator, and the monitor's `--help` / bad-argument
handling.
