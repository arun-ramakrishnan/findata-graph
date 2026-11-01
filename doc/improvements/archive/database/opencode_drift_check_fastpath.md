---
title: "opencode_drift_check_fastpath: watermark-guarded drift check — frozen-snapshot skip + scalar-MAX lane guards"
status: executed
filed: "2026-11-02"
executed: "2026-11-02"
completed_md: "470"
area: "helpers/maintenance (harvest_conversations.py drift path)"
---

# opencode_drift_check_fastpath — kill the opencode drift rescans (db_scaling_tuning §6.4 spin-off)

**Date:** 2026-11-02 · **Status:** EXECUTED same day
**Follows:** `../archive/database/db_scaling_tuning.md` §6.4 (deferred to
`../pending.md`, revisit trigger invoked by operator 2026-11-02).

## 1. TL;DR

The warm `convo-fresh --check` wall (~31s, paid by every `make advisory`) is
the opencode drift check re-scanning stores that cannot have changed. Two
code-only fixes, zero foreign-store mutation:

1. **Frozen-snapshot skip**: immutable snapshot dbs with base + `:v2` + `:evt`
   watermarks present are frozen files — skip the scan entirely (mirrors
   APPLY's own "quiet" rule).
2. **Scalar-MAX lane guards**: on scanned stores, one scalar
   `MAX(...)` per lane against the existing watermark proves the lane clean;
   the GROUP BY runs only when the scalar says "something is newer" (i.e.,
   only when there is drift to report).

## 2. Evidence (measured 2026-11-02, read-only probe
`$TMPDIR/ocdrift/probe.py`)

- Corrects the 2026-10-29 profile attribution: the v2 `session_message`
  GROUP BY is 2.1ms live (140 rows — upstream migrated to `session_v2`).
  The cost is the **event-table GROUP BY** (`json_extract` per row):
  974ms live, **3868ms per snapshot** (timeshift-mount blob reads), x8
  snapshots + live ~= the whole 31s warm wall.
- The stores already carry upstream indexes (`event_aggregate_type_seq_idx`,
  `session_message_session_time_created_id_idx`, ...) — no index work needed
  from our side.
- Scalar guards on the same predicates: event `MAX(json_extract(...))`
  240ms live / 242ms snapshot; v2 scalar 0.2ms. GROUP BY runs only on
  watermark miss => clean-check cost ~= 1 scalar pass on live only.
- Immutability: snapshot sources are frozen (`_opencode_sources()`,
  `immutable=True`); `_harvest_opencode` already skips watermarked snapshots
  as "quiet" — the check re-scanning them every run is asymmetry, not
  semantics. The first-time `:v2`/`:evt` backfill visibility (comment at
  `_opencode_source_drift`) is preserved: the skip fires only when ALL THREE
  watermarks exist; partial/missing watermarks keep today's scan.

## 3. Changes

### S1 frozen-snapshot skip
`_opencode_source_drift` immutable branch: when `store.watermark(key)`,
`_wm_int(store, key+":v2")` and `_wm_int(store, key+":evt")` are all present,
return without `connect`/scan. New or partially-watermarked snapshots keep
today's behavior verbatim (scan + lane detail).

### S2 scalar-MAX lane guards
`_lane_uncovered`: before each GROUP BY, scalar `MAX` over the same predicate;
if `<=` the lane watermark (when present), the lane is clean — skip the GROUP BY.
Watermark absent (lane never harvested) => GROUP BY as today. Drift-present
output identical (per-session detail still computed on the GROUP BY path).

### Out of scope
- No CREATE INDEX / any write on harness-owned stores (the invasive options
  pending.md named — indexing, watermark-bounding via new watermarks, sidecar
  mirror — are all superseded by code-only guards on EXISTING watermarks).
- dsh lane (~4s real, `_dsh_index_lag` + 14.6k `_dsh_read_log` calls) —
  separate future look.
- APPLY-path `new_hwm` scans (infrequent; unchanged).

## 4. Acceptance

- Targeted tests: frozen-snapshot skip (all-watermarks => no connect, no
  drift lines; partial => scans), scalar-guard skip (clean => GROUP BY not
  executed; drift => identical detail to today), via in-memory sqlite fakes.
  Existing opencode lane tests stay green.
- Bench: warm `make convo-fresh` check-mode A/B — target <=6s from ~31s,
  output lines identical on a clean tree (modulo the 13-orphan pre-existing
  warning, unrelated).
- Advisory gate per gates.md at arc close (operator go).

## 5. Risk

Check output on clean stores unchanged; on drifted stores unchanged (GROUP BY
path intact). Worst case a watermarked snapshot that somehow changed content
without a new snapshot dir would be missed — timeshift snapshots are
content-frozen by construction, and a NEW dir yields a NEW path/key (hash of
path), which fails the watermark guard and is scanned.

## 6. Execution (2026-11-02)

S1 + S2 landed in helpers/maintenance/harvest_conversations.py the same day:
`_opencode_source_drift` returns without connecting for immutable sources
whose base + `:v2` + `:evt` watermarks all exist; `_lane_uncovered` runs one
scalar MAX per lane first and skips the GROUP BY when the global max is at or
below the stored watermark. No harness store was touched (no CREATE INDEX, no
schema change) — both guards ride watermarks that already existed.

- Tests: 10/10 in tests/test_opencode_v2_lanes.py (3 new: skip-when-closed
  via ghost path asserting no connect; partial-watermarks-still-scan via the
  "unreadable" branch; sqlite trace-callback proving the GROUP BY does not
  execute on clean lanes).
- Bench (same 3-rep protocol as the 2026-10-29 baseline): warm
  `harvest_conversations.py --check` 31s -> 5.5s (5.7x); cold first rep
  138s -> 8.2s. Output lines identical (only the pre-existing
  13-orphan warning, unrelated to this arc).
- Corrected attribution vs the 2026-10-29 profile: the v2 GROUP BY was
  already 2ms (session_message nearly empty post session_v2 migration); the
  wall was the event-table GROUP BY at ~3.9s per snapshot on the timeshift
  mount — see the probe in §2.
- ruff clean, md-lint clean. Archived per operator instruction 2026-11-02.
