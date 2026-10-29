---
title: "Bili absorb-stat display — block-shrink census + absorb↔fold cache-cost correlation across the three tools"
status: executed
filed: '2026-10-29'
executed: '2026-10-29'
completed_md: '452'
area: helpers/analytics
---

# Bili absorb-stat display — block-shrink census + absorb↔fold cache-cost correlation across the three tools

## 1. TL;DR

The proxy's absorb mode compacts one long tool output into a distilled
summary and logs it as `[acp-absorb] absorbed mNNNNN (~5.5K tokens →
summary ~1.1K) ... (credit -5523 tok)` — but none of the three bili
tools could see it: folds were counted, absorbs were invisible. This arc
collates absorb events and displays them everywhere. Census:
**31 absorbs, ~195,777 tok credited, 90% average block shrink** (3
blowups counted but excluded from the mean). The measured correlation
answers the compaction-health question directly: **absorbs are the cheap
path** (tail-anchored, zero KV-cache trash) while **folds are the
powerful-but-expensive path** (prefix-rewriting, 31–363k tok cache trash
on bad folds).

## 2. Motivation

The absorb↔shrink review (2026-10-29) mapped how block shrink
correlates with folds and cache health across the bili.log family —
but the numbers lived only in that session's analysis. The tools should
say it directly. Two sub-defects surfaced en route and are fixed in the
same arc: the fold counters read zero against the plugin v0.1.209 log
format, and bili_sessions table rows showed abs=0 even after parsing
worked (family bucketing skipped absorbs).

## 3. The correlation map — what the numbers say

### 3.1 Block shrink (absorbs) is consistently deep: ~90%

Across the 31 absorbed events (7 sessions), the summary replaces ~9/10
of the original block. Per-session averages (whole rotation family):

| session    | absorbs | credit tok | avg block shrink |
|------------|---------|------------|------------------|
| ses_ec9ae5 | 1       | 9,500      | 98.4%            |
| c1831f3d   | 6       | 35,000     | 97.6%            |
| ses_eb005c | 4       | 29,758     | 94.4%            |
| 72f82566   | 5       | 40,300     | 93.1%            |
| 48c2c2da   | 2       | 8,500      | 89.0%            |
| c73275e4   | 10      | 55,519     | 84.5%            |
| 6e772ff0   | 3       | 17,200     | 75.6%            |
| **total**  | **31**  | **195,777**| **90%**          |

### 3.2 Absorbs trash nothing — tail-anchoring

An absorbed block is the *most recent* tool result: it sits at the tail
of the context, so replacing it never invalidates the cached prefix.
Measured: next-3 cache hits after an absorb run 34–100%, mean ~85–90%.
Absorbed long outputs are therefore compaction *for free* — deep shrink
with zero KV-cache cost.

### 3.3 Window shrink (folds) rewrites the prefix

Folds average ~63% window shrink and cut into the cached middle of the
context. When the rewrite lands badly (next-5 hits < 20%) the KV-cache
trash is 31–363k tokens per fold; anchor-preserving late folds
(c73275e4 after 10:22) trash 0k with next hits recovering 54–79% →
90%+. Tail-trim folds barely shrink at all: ses_eb005c's folds trim only
2% of 2–2.9M-token contexts (postCtx stays ~2M).

### 3.4 absorbShare — which mechanism did the work

absorbShare = absorbed / (absorbed + folded) separates the regimes:
c1831f3d = 100% (absorb-only, 0 folds — compaction achieved entirely by
the free path), 6e772ff0 = 22%, the remaining sessions 1–14%. High
absorbShare with deep block shrink is the healthy signature; high fold
share with low post-fold hit rates is the expensive one.

### 3.5 Degenerate folds — the trashed-context signature

3 folds shrank 88–100% with postCtx collapsing to 13–21k: the context
was effectively discarded, not compacted. The display flags these via
the existing degen column; absorb display adds the contrast point
(absorbs never degenerate).

Absorbs also precede folds by 0–12 min and cluster in bursts — an
absorb burst is a leading indicator that a fold wave is coming.

## 4. Slices

- **S1 — fold-zero pre-reqs** (bili_analytics.py): RE_FOLD updated for
  the v0.1.209 "this-fold · cumulative" trailer —
  `[acp-compress-obs] shrink (\d+)%[^ (]*\(~(\d+)/(\d+) tok\)(?:.*?foldPoint=(\S+))?`;
  RE_SESS gains the bare [session-<uuid>] form (4th alternative);
  RE_PREFIX timestamp made Z-optional (\.\d+Z?). Same body fix in
  RE_COMPRESS_OBS_BODY (named groups).
- **S2 — bili_analytics.py**: AbsorbEvent(ts, session, ref, orig_tok,
  summ_tok, warned) + block_shrink_pct property
  (round(100*(1-summ/orig), 1)); Analysis.absorbs + absorb_stats() →
  (events, credit_tok, avg_shrink_of_successful, blowups) — blowups
  (warned=True, summary ≥ original; 2 of 30 measured, worst -3335%
  "shrink") excluded from the mean but still credited at original size;
  RE_ABSORB_BODY =
  `[acp-absorb] absorbed (?P<ref>m\d+) \(~(?P<orig>[\d.]+[KM]?) tokens → summary ~(?P<summ>[\d.]+[KM]?)``
  + optional WARNING clause; _parse_tok K/M-suffix parser; parse branch
  before the usage branch; merge extend+sort; CLI summary line after
  "total folds"; JSON export carries the absorb list.
- **S3 — bili_sessions.py**: merge row.update adds
  absorbs/absorb_credit/absorb_shrink; `_COLUMNS` adds
  ('absorbs','abs','>',5,False) after folds; `_COUNTERS` keys; --detail
  prints "absorbed blocks : N events, ~X tok credited, avg block shrink
  Y%"; **root-cause fix** — `_family_index` now buckets `whole.absorbs` per
  session (:351) and sorts the series (:360); the hand-bucketing had
  skipped absorbs, which is why every row showed abs=0.
- **S4 — bili_tier_watch.py**: RE_ABSORB (ref+orig) + `_tok_k`; Watch
  fields absorbs/absorb_credit/per_session_absorbs/
  per_session_absorb_credit/`_seen_absorbs` (dedup key (ts, sid, ref));
  consume branch + `_consume_absorb` (mirrors `_consume_fold`,
  session-filtered); Sample fields; heartbeat renders abs=N/credit
  after shrink%; as_dict keys; build_sample per-session wiring.

## 5. Acceptance criteria & shakedown (all verified live)

| surface | requirement | verified output |
|---------|-------------|-----------------|
| bili_analytics.py CLI | absorb summary line | total absorbs : 31 (credit ~195,777 tok, avg block shrink 90%, 3 blowup(s)) |
| regex coverage | no format misses | 9 bili.log + 22 .old = 31, complete |
| bili_sessions.py table | abs column | c73275e4=10, c1831f3d=6, 72f82566=5, ses_eb005c=4, 6e772ff0=3, 48c2c2da=2, ses_ec9ae5=1 |
| --detail | absorbed-block line | absorbed blocks : 10 events, ~55,519 tok credited, avg block shrink 84% |
| --json (sessions) | three keys per row | absorbs/absorb_credit/absorb_shrink match census |
| bili_tier_watch.py heartbeat | abs=N/credit | folds=4 saved=137.4k blocks=0 shrink%=70 abs=3/17.3k |
| --json (watcher) | absorb keys | absorbs:3 absorb_credit:17300 |

Note the two counting scopes are both correct: the watcher counts the
live tail window (folds 4, abs 3) while bili_sessions counts the whole
rotation family (folds 12, abs 10 for c73275e4).

## 6. Incidents & notes

1. `_family_index` bucketing skipped absorbs → abs=0 on every table row
   despite correct parsing; fixed at bili_sessions.py:351 (+ sort tuple
   :360).
2. The RE_ABSORB insertion briefly replaced the RE_INJECT definition
   (edit anchor not restored) → NameError: RE_INJECT at `_consume_nudge`
   :347; restored at bili_tier_watch.py:115.
3. Blowups: summaries ≥ original (worst -3335%) are counted, excluded
   from the shrink mean, and still credited at original size — the
   proxy credits orig_tok regardless.
4. Companion "tool absorb executed via plugin" ack lines must not be
   counted as absorb events (parser keys on the [acp-absorb] tag).

## 7. Non-goals

- No proxy/plugin changes — the tooling stays read-only over the log.
- No web-dashboard surface; CLI/JSON only.
- Fold-window shrink semantics unchanged; absorb display adds the
  contrast, it does not redefine fold metrics.

## Appendix — raw verification log

- bili_sessions.py --aggregate rows carry abs (values above);
  run_for_sid('session-c73275e4-…') → absorbs 10, credit 55,519,
  shrink 84.5, folds 12; 48c2c2da → 2 / 8,500 / 89.0.
- bili_tier_watch.py --once --session session-c73275e4-… heartbeat and
  --json outputs quoted in §5.
- Correlation raw numbers (block/window shrink, cache trash, hit-rate
  windows, absorbShare, degenerate folds) quoted in §3 from the
  2026-10-29 absorb↔shrink review over the same log family.
