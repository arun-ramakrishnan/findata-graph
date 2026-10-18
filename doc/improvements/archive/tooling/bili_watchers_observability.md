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
