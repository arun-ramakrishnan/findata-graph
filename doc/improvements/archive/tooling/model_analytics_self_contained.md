---
title: "Vendor legacy usage clients into model_analytics"
status: executed
filed: "2026-10-06"
executed: "2026-10-06"
completed_md: "357"
area: "helpers/analytics/model_analytics.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Vendor legacy usage clients into model_analytics

**Date:** 2026-10-06 · **Status:** EXECUTED (completed.md #357) ·
**Area:** helpers/analytics/model_analytics.py (loader) + tests/test_model_analytics_self_contained.py (new)

## 1. Motivation

`model_analytics.py` is tracked (it is what `make analytics-fresh`
invokes) but functionally imports two git-ignored legacy scripts at
three lazy sites. Owner ruling 2026-10-06: the `usage_*` files are
legacy, unmaintained, and stay out of git. That leaves the tracked
loader with untracked load-bearing dependencies — and the failure mode
is silent, not loud: `load all` catches per-source exceptions into
`load_log` and continues, but the family loaders read zai's rows at
load time to drop plan-covered Zhipu rows (`_drop_api_covered`), so a
failed zai load leaves nothing to drop against and prime-rlm/opencode
**double-count** on a fresh store. A missing sibling is a wrong-spend
bug, not a crash.

Vendoring (not moving) is the right shape *because* the sources are
legacy: the ignored originals are frozen, so the vendored copy becomes
the single maintained home by definition — no fork-maintenance, no
edits to frozen files, their CLIs keep running as-is.

## 2. Evidence (measured 2026-10-06, this box)

| Import site | Names used | Verdict |
|---|---|---|
| `load_zai` | `z.load_api_key`, `z.parse_range`, `z.fetch` | vendor S1 |
| `load_zai` cost line | `z.day_cost` | vendor S1 |
| report telemetry block | `z.local_stats` | vendor S1 |
| report opencode block | `o.local_latency` (+ `DAY`/`DAY_MSG` consts, private deps) | vendor S2 |
| sibling sizes | `zai_usage_query.py` 541 lines, `opencode_usage_query.py` 797, `model_analytics.py` 1463 | ~+250 lines moved, no logic written |
| `load all` failure semantics | per-source try/except → `load_log 'error'`, loop continues; `_drop_api_covered` then double-counts (the `_drop_api_covered` ordering comment in the load dispatch) | silent wrong-spend, not a crash |
| spend baseline (parity anchor) | `fact_usage`: zai 90 rows / $833.9975, opencode 162 / $0.8960, prime-rlm 72 / $128.3353; day ranges zai 08-23→10-06, opencode 2025-10-18→10-06, prime 08-10→10-06 | S3 must reproduce exactly |
| this-morning `load all` | zai 6, prime-rlm 3, opencode 17 rows (`since=2026-10-04`), all ok | loader healthy pre-change |
| other importers of the siblings | none functional (`agent_traces.py` comment-only, one archived-doc mention) | vendoring strands nothing |

What the numbers mean: the dependency is narrow (5 public names +
their private deps), the blast radius of *not* fixing it is silent
spend double-counting on any machine without the ignored files, and
the parity anchor above makes "no behavior change" mechanically
checkable. Ruled out: tracking the siblings (owner decision, legacy
stays out).

## 3. Design

Copy the used functions into `model_analytics.py` under a `# vendored
from <file> — legacy snapshot, this copy canonical` banner; repoint
the `z.`/`o.` call sites; delete the three lazy imports. The legacy
files are not edited (optional: one header line each pointing at the
canonical copy — excluded from the slices below, operator's call).

Slices (each independently landable, in order):

- **S1 — vendor the zai client** (unblocks fresh-clone `load zai`):
  `load_api_key`, `parse_range`, `fetch` + private deps (`_day`,
  `slices`, `_read_env_key`, `_fetch_slice`, `_fold_daily`,
  `_merge_series`, `_merge_detail`, `TOKEN_FIELDS`,
  `BASE_URL`/`MAX_WINDOW_DAYS`), `local_stats`, `day_cost`; repoint
  the two `load_zai` sites + the report `local_stats` site. Key
  handling identical (`memory/.env` + `$ZAI_API_KEY`, never in repo).
- **S2 — vendor opencode `local_latency`** (unblocks fresh-clone
  report opencode leg): the function + `DAY`/`DAY_MSG` + whatever
  private helpers its body calls (verified at implementation time —
  the `or_*`/render/mining surface stays in the sibling, ad-hoc only);
  repoint the report site.
- **S3 — decoupling proof + parity**: AST test asserting zero
  `zai_usage_query`/`opencode_usage_query` imports in
  `model_analytics.py` (fresh-clone safety, runs in `make test`);
  parity shakedown — `load all` row counts per source and day-level
  `sum(cost_usd)` identical to the §2 baseline; sibling CLIs
  (`--help` + one read-only query each) verified untouched; gates
  green.

Alternatives considered: (A) track the siblings — rejected, owner
decision 2026-10-06 (legacy stays out). (B) consolidate-move, i.e.
edit the legacy files into shims over `model_analytics` — rejected:
higher touch on frozen files, and the shim would import a ~1900-line
loader to run a query CLI. (C) try/except degradation around the lazy
imports — rejected: a skipped zai load is exactly the silent
double-count, now with a warning nobody reads; the freshness contract
(`make analytics-fresh` green ⇒ figures right) forbids it.

## 4. Acceptance criteria & shakedown

1. AST decoupling test green: no `zai_usage_query` /
   `opencode_usage_query` import remains in `model_analytics.py`;
   `python3 -c "import model_analytics"` succeeds with the siblings
   temporarily moved away (fresh-clone simulation).
2. Parity: `load all` per-source row counts and day-level
   `sum(cost_usd)` per source identical to the §2 baseline (zai
   $833.9975 / opencode $0.8960 / prime-rlm $128.3353); `load_log`
   all-`ok`, zero new warnings.
3. Sibling CLIs untouched: `zai_usage_query.py --help` and one
   read-only query each run exactly as before (frozen, still ignored).
4. Gates stay green: ruff, `ty check`, `test` (incl. the new AST
   test), static_checks rc=0, md-lint 0.

| Projected outcome | Today | After |
|---|---|---|
| tracked files `model_analytics` functionally needs | 1 ignored (`zai_*`) + 1 ignored (`opencode_*`) for zai/opencode paths | 0 — self-contained |
| fresh-clone `load all` | zai FAILED + silent prime/opencode double-count | all-`ok`, figures match §2 |
| `model_analytics.py` size | 1463 lines | ~1700 (moved, not written) |
| spend figures | baseline §2 | identical (guard AC#2) |

## 5. Risks

- **API drift now owned by the tracked copy** — intended (that is the
  point), but the Zhipu/OpenRouter client code gains no second
  reviewer: any future endpoint change is fixed once, here, with a
  `load zai` shakedown. The frozen originals remain as read-only
  reference.
- **Module growth** — ~+250 lines into an already-1463-line loader.
  Mitigation: moved verbatim under the banner, no drive-by refactors;
  size is honest (it was always this logic, just three files away).
- **Key handling** — `load_api_key` behavior identical; no key
  material enters the repo (`memory/.env` stays ignored). AC#2's
  successful `load zai` proves it live.
- **No silent-scope growth** — S1/S2 move only the names in §2's
  table; `render_*`/`or_*`/mining surface stays put (verified by the
  AC#1 AST test's allowlist: the test fails on *any* sibling import,
  so stragglers cannot hide).

## 6. Non-goals

No spend-figure changes (guarded by AC#2); no legacy-file edits
(beyond the optional header pointer); no report-output changes; no
`agent_traces`/`convo_union` changes (both already self-contained,
verified 2026-10-06); no `load all` ordering/semantics changes.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-06 | `wc -l bench_data/code/{zai,opencode}_usage_query.py model_analytics.py` | 541 / 797 / 1463 | moved-not-written sizing |
| 2026-10-06 | `rg "\bz\.\|\bo\." model_analytics.py` | 6 hits: 362, 369, 370, 385, 701, 730 | the complete use-surface |
| 2026-10-06 | read the `load all` dispatch | per-source try/except + `_drop_api_covered` ordering comment | silent double-count, not a crash |
| 2026-10-06 | `rg "import.*(zai_usage_query\|opencode_usage_query)" agent_traces.py trace_contracts.py convo_union.py helpers/ tests/` | no hits | nothing else to strand |
| 2026-10-06 | `fact_usage` census (DuckDB, read-only) | counts + ranges + costs in §2 | parity anchor for S3 |
| 2026-10-06 | `make analytics-fresh APPLY=1` | zai 6 / prime-rlm 3 / opencode 17, all ok | loader healthy pre-change |
| 2026-10-06 | S1+S2 verification | `load zai` 6 rows through vendored client; AST diff: 13 fns + 8 consts identical to legacy | parity by textual identity |
| 2026-10-06 | S3 verification | AST decoupling + fresh-clone simulation green; `load all` figures identical to baseline (zai 90/$833.9975, opencode 162/$0.8960, prime-rlm 72/$128.3353); sibling `--help` clean; ruff/ty/pytest/static/md-lint green | executed 2026-10-06 as #357 |
