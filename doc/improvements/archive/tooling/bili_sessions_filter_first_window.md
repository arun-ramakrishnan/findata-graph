---
title: "bili_sessions_filter_first_window: apply --since on /sessions meta before the per-session detail fan-out"
status: executed
filed: "2026-11-02"
executed: "2026-11-02"
completed_md: "469"
area: "helpers/analytics (bili_sessions.py main window path)"
---

# bili_sessions_filter_first_window — stop fetching details for rows the window hides

**Date:** 2026-11-02 · **Status:** EXECUTED same day (operator-reported ~10x
startup regression on `bili_sessions.py --since 1d`; retro-filed at operator
request).

## 1. Diagnosis

The 2026-10-29 dsh-session verdict (convo m00708) was correct that startup is
HTTP-bound — the per-session `GET /sessions/{sid}/detail` fan-out against a
~16 req/s server ceiling — but the wall had grown since for three compounding
reasons, none of them the recent commits (absorb economics, py314 sweep are
innocent):

1. Sessions grew ~50 -> 75, and `_prime_details(sids)` fanned out for ALL of
   them BEFORE the `--since` filter ran — `--since 1d` paid for 75 sessions
   and then hid 70.
2. A second proxy is now live (ports 18787 + 18788), and `_fetch_detail`
   tries EVERY discovered proxy per sid: ~150 HTTP calls per invocation.
3. Server latency under its own GUI load (127 -> 430 ms/call recorded
   2026-10-29).

Measured 2026-11-02 pre-fix: `--since 1d` wall 10.35s; `/sessions` 232ms;
75 total sessions / 5 kept by the window.

## 2. Change

`main()`: the `/sessions` list (ONE call) already carries each session's
`lastSeen` — the same field the row filter used (last_seen is
detail/stats-derived only; the "overwritten from the log" comment in the row
builder applies to `reason_pair_violations`, not last_seen). So the window
now runs on session IDS against meta lastSeen BEFORE `_prime_details`/
`run_for_sid`; only window-kept sids are built. Details:

- Same KEPT rule as before: a sid with no parsable meta lastSeen stays — the
  window defers rows, it never silently hides them.
- Dashboard-down fallback keeps the old build-then-filter row path (pinned
  by a new test), since last_seen only exists on rows there.
- The hidden-count stderr line is unchanged (byte-identical output on the
  live box).

## 3. Results

| | before | after |
|---|---|---|
| `--since 1d` wall | 10.35s | 1.5-1.7s (~6-7x) |
| detail requests | ~150 (75 sids x 2 proxies) | ~10 (5 kept x 2) |
| table output | — | identical (only the live session's own counters advanced between captures) |

Tests: 58/58 in tests/test_bili_sessions_index.py (2 mocks updated —
`TestSinceAcceptsRanges._run` and `TestLastSeenWindow` now expose `lastSeen`
on the mocked /sessions meta, as the live dashboard does; 1 new fallback-path
test `test_fallback_window_filters_rows_without_meta`). ruff clean.

## 4. Not done (named lever)

The residual ~1.5s is the kept-sids fan-out (5 x 2 calls) + imports + the
232ms /sessions call. A bulk `/sessions/details` endpoint on the proxy would
cut the rest — server-side, out of scope here.
