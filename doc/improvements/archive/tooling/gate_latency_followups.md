---
title: "Gate latency follow-ups — dirty-gate the .py legs, drop the dead import, iterate scopes"
status: executed
filed: "2026-09-21"
executed: "2026-09-21"
completed_md: "262"
area: "helpers/validators"
---

# Gate latency follow-ups — dirty-gate the .py legs, drop the dead import, iterate scopes

**Date:** 2026-09-21 · **Status:** EXECUTED ·
**Area:** `helpers/validators/static_checks.py`,
`helpers/validators/data_format_checks.py`,
`helpers/maintenance/maint.py` (test pins)

## 1. Motivation

The corpus legs are gated (dirty_gated_corpus_validation, flipped)
and the schema engine is fast (fastjsonschema_split_track). S0
(best-of-2, load ~1) shows the remainder is three per-file `.py`
scans — 3.35 s of the ~4.98 s total, all gateable by the same
porcelain mechanism, plus a dead import and re-walks:

| Check | Time | Gateable as |
|---|---|---|
| Data format (zstd + Arrow AST scans) | 1.36 s | dirty `.py` set (helpers/ + app.py) |
| Python syntax (py_compile walk) | 1.18 s | dirty `.py` set (repo-wide) |
| Embedding decode chokepoint (receiver scan) | 0.81 s | dirty `.py` set (helpers/ + app.py) |
| Frontmatter schema / YAML / OKF | 0.38 + 0.38 + 0.38 s | already gated |
| SQLite helper usage | 0.18 s | no (DB-state check, out of scope) |
| Coverage ledger | 0.15 s | no (ledger read, out of scope) |
| rest (JS, orphan, markers, lifecycle, pins, rosters...) | ≤0.10 s each | no prize |

Supporting measurements: `helpers.core.corpus` import chain 70/76 ms
(`-X importtime`; the `Corpus` import in static_checks is DEAD —
imported, never referenced); walk-vs-work: syntax walk 0.21 s of
1.18 s, data_format walk 0.002 s of 1.36 s (AST work dominates).

## 2. Evidence (measured 2026-09-21, this box)

| Configuration | Result | Verdict |
|---|---|---|
| per-CHECK best-of-2 (18 legs) | table above; top-3 ungated = 3.35 s (67%) | Slice A target |
| `Corpus` references in static_checks | import lines only (plus a prose comment) | Slice B = deletion |
| walk-only vs full | syntax 0.21/1.18 s; data_format 0.002/1.36 s | walks are stat-only; Slice C kills the re-walks, Slice A the work |

S1 independence (read, not measured): all three scans are per-file
pure (py_compile per file; AST/text scans keyed per site; lane list
and allowlists static). One trap found by reading: data_format's
**baseline-hygiene** rule (stale entry → fatal) needs the FULL
violation set — scoped runs must skip it (baselines are empty today,
so the trap is currently unloaded, but the logic must not arm it).

## 3. Design

- Slice A: `_DIRTY_PY_SCOPE` (same porcelain source; `.py`,
  SKIP-pruned; schema-dirt does NOT force py-full — only git failure
  does). `check_python_syntax` / `check_embedding_decode_chokepoint` /
  `check_data_format` (threaded into `data_format_checks`) take
  `py_scope=None` → global fallback (None = full). Scoped runs skip
  baseline-hygiene (documented: hygiene is a full-corpus property).
- Slice B: delete the dead `Corpus` import; fix the stale prose
  comment; measure the import delta.
- Slice C: when a scope is present, legs iterate scope members
  instead of walking (zero rglob on clean trees — syntax leg keeps
  its 0.21 s walk otherwise). No cross-run state, no staleness:
  the scope derives from live git status every run. Known divergence
  class: gitignored `.py` files (rglob sees them, porcelain doesn't)
  — documented; `--full` remains the arbiter. This retires the
  pending.md persistent-cache entry (unnecessary, not merely deferred).

Alternatives considered: persistent cross-run file-list cache
(rejected — scope-driven iteration captures the prize with zero
staleness surface); gating SQLite/ledger legs (rejected — DB-state,
no file-set semantics).

Slices: S0 DONE (above); A py-scope + tests; B dead import + measure;
C scope-iteration + parity tests.

Follow-on slices (same arc, executed 2026-09-21):

- Slice D — baseline-hygiene refinement. The Slice A skip was
  over-broad: staleness *is* provable for entries whose file was
  scanned this run. Rule now: full runs check every entry; scoped
  runs check only entries whose file is in scope (exact rel match —
  `helpers/a.py` never matches `helpers/a.py2`); unscanned files
  defer to full runs. Pinned both ways (in-scope stale flags,
  out-of-scope defers).
- Slice E — gate sqlite (175 ms, per-file substring scan, tests/
  exemption + allowlist + self-skip preserved), JS (75 ms, dirty
  `.js` set, node probe preserved), shebangs (17 ms); ledger
  read-once. Ledger outcome: NO win — re-reads were ~5 ms; the cost
  is per-route `get_source_segment` full splits (parse 24 ms +
  segments 144 ms over 82 defs). Manual extraction was rejected
  (would re-hash all 38 fingerprints → ledger re-baseline for
  150 ms). Ledger + orphan (DB-state) + DB-meta + merge +
  lifecycle = the floor (~0.5 s gated).
- Slice E also caught a real bug: the read-once refactor shadowed
  `text` with the first segment and IndexError'd on multi-route
  files — shakedown caught it (no multi-route coverage existed);
  regression test added.

## 4. Acceptance criteria & shakedown

1. Tmp-vault tests per leg: dirty-break flagged, clean scope quiet,
   full-vs-scoped parity, weakened guarantee documented (same shape
   as `test_dirty_scope.py`); baseline-hygiene skip pinned
   (seeded baseline + out-of-scope file → no stale fatal when
   scoped, fatal when full).
2. End-to-end `--dirty` on a small-dirty tree ≈ legs near-zero with
   identical verdicts to `--full`; best-of-3, solo, load-recorded.
3. Import delta measured (fresh-process import before/after B).
4. `make qa` green (operator go); ruff/format clean; search-fresh converges.

| Projected outcome | Today | After |
|---|---|---|
| top-3 py legs, small-dirty tree | ~3.35 s | milliseconds (scope walk + validate dirty only) |
| static_checks import chain | ~76 ms wall (~168 ms cumulative) | hygiene only: wall ~70 ms post-delete (startup-dominated — the 70 ms corpus chain overlapped interpreter startup; kept as dead-code removal, not a latency win) |
| clean-tree syntax leg | ~1.18 s (0.21 s walk) | ~0 (no walk, no work) |
| end-to-end, small-dirty tree | ~4.2 s | measured 0.72 s (best-of-3) vs 4.97 s full, identical verdicts |
| sqlite/js/shebang legs, small-dirty tree | 0.18 + 0.08 + 0.02 s | milliseconds (same scope pattern; node probe + tests/ exemption preserved) |
| ledger route_inventory | 0.15 s | floor: segments dominate, manual extraction rejected (fingerprint migration) |
| end-to-end, small-dirty tree (post slices D–E) | 0.72 s | measured 0.52 s best-of-3 vs 5.48 s full (box load ~7–12, noisy), identical verdicts — 10.5×; floor ≈ ledger + orphan + DB-meta + merge + lifecycle + startup |

## 5. Risks

- **Baseline-hygiene false fatal** — mitigated by skipping the rule
  under scope (pinned by test); full runs enforce it as today.
- **Ignored-file divergence** (scope-driven iteration) — documented;
  parity tests use non-ignored trees; `--full` arbiter; maint-full
  backstop unchanged.
- **Weakened guarantee widens to .py** — same accepted cost as the
  corpus flip, same mitigations (documented in-test, `--full`,
  maint-full backstop).

## 6. Non-goals

SQLite/ledger/JS legs (no file-set semantics or no prize);
changing what any check asserts; persistent caches; touching OKF/
lifecycle/schema legs.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-21 | per-CHECK best-of-2 via import + perf_counter | table §1; sum 4.98 s | load ~1; ranking run, slice shakedowns best-of-3 |
| 2026-09-21 | `-X importtime` static_checks chain | corpus 70 ms / chain 76 ms; Corpus unreferenced | Slice B |
| 2026-09-21 | walk-only vs full (syntax, data_format) | 0.208/1.180 s; 0.002/1.355 s | Slice C prize ≈0.22 s on clean trees |
| 2026-09-21 | Slices A–C build | py-scope builder + leg params + scope-iteration (all walks) + dead-import deletion; hygiene-skip + ignored-file divergence documented | 23 new tests (builder, per-leg scope/parity/weakened-guarantee, hygiene pin, no-corpus-import); 368 suite green; ruff clean |
| 2026-09-21 | shakedown best-of-3, solo, load ~2 (2 notes + 4 py dirty) | `--dirty` 0.72 s vs `--full` 4.97 s, identical verdicts (0 fatal, 1 advisory) | 6.9× end-to-end; remainder ≈ SQLite + ledger + JS + fixed costs |
| 2026-09-21 | Slice B import delta | fresh-process import 0.076 → ~0.070 s wall; cumulative importtime 168 → 52 ms | wall effect ≈ nil (startup-dominated) — kept as hygiene, projection corrected |
| 2026-09-21 | Slices D–E build | js scope + sqlite/shebang scope params; hygiene refinement (in-scope staleness flags); ledger read-once + shadowing fix | 8 new tests (js builder/scope/parity, sqlite scope/exemption/parity, shebang scope/parity, hygiene refinement, multi-route regression); 376 suite green |
| 2026-09-21 | shakedown best-of-3, solo, load ~7–12 (2 notes + 5 py dirty) | `--dirty` 0.52 s vs `--full` 5.48 s, identical verdicts (0 fatal, 1 advisory) | 10.5× end-to-end; remainder is the floor (no file-set semantics left) |
| 2026-09-21 | ledger cost autopsy | parse 24 ms + segments 144 ms over 82 defs; re-reads only ~5 ms | manual extraction rejected (38-fingerprint migration); ledger stands as floor |
