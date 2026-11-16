---
title: "Bili display parity — ledger-netted saved, spend/payback, ladder + fold-detail columns (bili_sessions & bili_tier_watch)"
status: executed
filed: '2026-11-16'
executed: '2026-11-16'
completed_md: '525'
area: helpers/analytics
---

# Bili display parity — ledger-netted saved, spend/payback, ladder + fold-detail columns

## 1. TL;DR

The `bili_sessions.py` table and the dashboard SPA disagreed 4.4× on
`saved` corpus-wide (dashboard 16.2M vs log fold-sum 3.7M, measured
2026-11-15; single session 463,447 vs 335,348), hid the tier-ladder
state the tier-watch heartbeat carried, and showed spend nowhere; the
heartbeat in turn buried the per-fold block details `bili.log` emits as
an ~8-line noise burst. This arc rebuilds the table around the page's
own ledger identity, ports the tier-watch cell formats in, adds
spend/payback + growth/act/blocked columns, removes the columns the
operator flagged as noise (switches/viol/proto + the violation banner),
and gives the tier-watch heartbeat a one-line `fold@…` detail record.
125 bili tests pass; ruff check+format clean; every cell format
live-verified against session `ses_e464ba95fffey2TwBleYG47aWf`.

## 2. saved/payback ledger parity (the mismatch, decoded)

The SPA bundle (`bili_spa.html`, 529KB) pins the page rule: the
sessions-list `savedTd` renders `netSaved ?? tokensSaved`, and the
ledger holds

```text
netSaved   = grossSaved − repayCost − summaryCost
grossSaved = Σ(saved_tok per fold × requests AFTER that fold)
```

i.e. fold savings are weighted by all requests that ran after the fold
(materialized cache reuse), minus what compression re-paid
(`repayCost`) and what the summaries themselves cost (`summaryCost`).
For `ses_e464ba95`: 4,263,196,220 − 2,140,144 − 99,273 =
**4,260,956,803** — the page's 4.26B. The old table column summed raw
per-fold `saved_tok` (1,549,025 ≈ 1.5M) — a different quantity, hence
"the page displays something else".

- `saved` now follows the page rule: `netSaved` first, `tokensSaved`
  (dashboard) fallback, raw log fold-sum last resort under `saved_folds`
  (never fake 0 when the dashboard has no ledger).
- New row keys: `net_saved`, `gross_saved`, `gross_saved_usd`,
  `saved_folds` (+ turn-1's `cost_usd`, `payback_spent_usd`,
  `payback_breakeven`, `payback_actual`).
- `spend` column = `costUsd.total`; `payback` column = two slash
  fields `$net/$spent` (net = `savedUsdGross − payback.spentUsd`),
  both `.2f` per operator request (live: `$43.83/$0.21`).
- `_fmt_w` mirrors the SPA's `fmtW` (≥1e9→b, ≥1e6→m, ≥1e3→k, lowercase
  per operator: `4.26b / 447.0m / 112.6k`); applied to saved, ctxNow,
  cumInput and the AGGREGATE totals line.

## 3. Ladder columns ported from tier-watch

- `abs` cell = `<events>/<credit>` (e.g. `13/95.6k`) — absorbs +
  absorb credit from `Analysis.absorb_stats()`.
- tier cells = `<fires>f/<pending>/<bar>` (e.g. `23f/21.3k/43.0k`),
  bars from `_compress_config()` (GET `/__bili/config`) with
  `tierNudgeTokens` overrides; `*` appended when the tier is
  ordering-ready (ord2: t2_pen > t1_eff; ord3: t3_pen > t2_pen AND
  > t1_eff; `_tier_ord2`/`_tier_ord3`).
- `growth` column = `<growth>/<floor>` in k/m (e.g. `11.6k/20.0k`);
  floor = `max(HARD_GROWTH_FLOOR, round(MIN_GROWTH_RATIO ×
  nudgeGrowthTokens))` mirroring `bili_tier_nudge.Bars.from_file`
  (HARD_GROWTH_FLOOR=20_000, MIN_GROWTH_RATIO=0.45; live
  nudgeGrowthTokens=23000 → floor 20000).
- `act` column = blocks still carrying the active flag
  (`len(load_session_state(DEFAULT_DB, sid).active)`, lru_cached,
  cleared in `reset_caches()`; `-` when the sessions index DB is
  missing — never fake).
- `blocked` column = per-tier gate holdbacks `[1:6,2:4,3:1]`
  (`Analysis.tier_blocked_counts()` over `n.blocked_tier`); fires live
  in the t1/t2/t3 cells, not here. Semantics note: tier-watch's
  `blocked[1:50,…]` is its CROSS-SESSION aggregate when unpinned — a
  grep census found 52 `blocked: T1` lines across the live log + both
  archives, only 6 tagged `ses_e464ba95`; the per-session column is
  the correct per-session number.

## 4. Operator removals

- Columns removed: `switches`, `viol`, `proto` (row keys survive for
  `--json`/`--detail`).
- The `!! reasoning-pair violation — N session(s) flagged:` banner is
  deleted entirely (`print_violations` gone; `--json` stdout pure AND
  stderr silent; `--detail` keeps the per-session gap view).
- Footer legend rewritten to document every cell format.

## 5. Tier-watch fold-detail line

`bili.log` emits an ~8-line burst around every fold (requested ranges,
ctx+refs, first msg ids, `[acp-compress-obs]` shrink/cumulative/
foldPoint/blocks/active/anchor/postCtx/ceiling, fold attribution
V/Vraw/Vp, `[Compressed …]` result, `· bN summary NNNNch` lines, tool
ack). `bili_tier_watch.py` now distills it into one second heartbeat
line:

```text
fold@2026-11-16T08:11:16: ranges=5 [m00232–m00254…m00465–m00466]
  ctx=273msg/476ref first=10msg/11.8kch shrink=71% (43.7k/61.2k)
  cum=72% foldPoint=m00232 blocks=4 act=8 anchor=3.6k postCtx=17.3k
  ceil≥20% V=61.2k Vraw=188.0k Vp=17.3k res=4blk/~43.7k excluded=2
  lastUser=m00465 sums=[b1:2215ch,…,b8:1369ch] outcome=applied
```

Untagged `· bN summary` lines attach via the last tagged sid; the
obs-branch requires `"shrink "` in the line because the fold-attribution
line also carries `[acp-compress-obs]`. Each fold renders ONCE — the
first heartbeat after its burst completes consumes it (m00687 fix) —
and the line is kind-tagged with what triggered the fold (`[T1]`/
`[T2]`/`[T3]`/`[idle]` from the preceding nudge, `[Abs]` after an
absorb act, `[Degen]` from the `[warn: degenerate-fold]` obs line;
`[auto]` when no trigger was in view). Long-running daemons must be
restarted to import the new module (fresh `watch -n90` loops are
unaffected).

## 6. Tests + verification

- `tests/test_bili_tier_ladder.py`: `TestSavedFollowsThePageLedger`
  (netSaved wins / tokensSaved fallback / log-folds stand-in),
  `TestReasoningPairViolationsAreQuiet` (no banner, stderr silent),
  tier-cell `25f/113.1k/43.0k`, ord-star, growth, blocked, act cells.
- `tests/test_bili_tier_watch.py`: `FOLD_BURST` fixture replayed
  idempotently into one dict; `fold@` render on the heartbeat.
- Suite: 125 tests pass; ruff check+format clean at each step.
- Live: table row `ses_e464ba95…` → saved=4.40b, payback=$43.83/$0.21,
  growth=11.6k/20.0k, act=6, blocked=[1:6,2:4,3:1]; heartbeat fold line
  as quoted above.

## 7. Files

- `helpers/analytics/bili_sessions.py` — ledger-parity saved, spend/
  payback/abs/growth/act/blocked cells, fmtW column formats, removals,
  legend.
- `helpers/analytics/bili_tier_watch.py` — fold-detail consume/render.
- `tests/test_bili_tier_ladder.py`, `tests/test_bili_tier_watch.py`.

No ontology/query-visible content — no eval-gate pin needed;
`make search-fresh` (advisory) re-indexes this page.
