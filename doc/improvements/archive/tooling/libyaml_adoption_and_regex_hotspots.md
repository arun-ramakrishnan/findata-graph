---
title: "Adopt libyaml C load/dump via a shared frontmatter helper and collapse the derivation regex hotspots"
status: executed
filed: "2026-09-01"
executed: "2026-09-01"
completed_md: "193"
area: tooling
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Adopt libyaml C load/dump via a shared frontmatter helper and collapse the derivation regex hotspots

**Date:** 2026-09-01 · **Status:** EXECUTED 2026-09-01 ·
**Area:** helpers/core/frontmatter.py, helpers/graph/{derive_insights,
derive_themes, derive_events, derive_cited_in}.py,
helpers/misc/{okf_verify, backfill_okf_provenance}.py, app.py;
(benchmark context: tests/, Mojo/bench/*.py)

## 1. Motivation

Trigger: `make perf` 2026-08-28→09-01 runs show `derive_insights` at
3.5–3.7 s against a 4.0 s budget — and a 2026-09-01 cProfile run measured
it at **6.0 s under the profiler**, over budget. The dominant cost is
**pure-Python YAML**: `yaml.safe_load` on the pure-Python `SafeLoader`
accounts for **3.2 s of 6.0 s (53%)** across 586 calls. The repo already
solved this exact problem in the validators (`static_checks.py` switched
to `CSafeLoader` and documented it as "the dominant static_checks cost"),
but four more production files still call the pure-Python path, including
the shared core helper `helpers/core/frontmatter.py`.

The same profiling pass confirmed a second, independent class: the graph
derivation scripts are **regex-bound** (`derive_events` 56%,
`derive_themes` 49% of runtime in `re.Pattern.search`). This was known —
`perf_improvs.txt` P1 (2026-08-17) documented `derive_events` at 356K
`re.search` calls; the compile/double-iteration fixes landed, but the raw
search-count cost remains (270K calls today).

Test-suite relevance (the pytest/xdist optimization arc of 2026-08-31):
the 142-module suite is NOT itself regex/YAML-heavy (max 3 `re.*` uses in
any test file). Its YAML cost comes entirely from the **production code
under test** — the `test_fuzz_derive_insights_regions` class (10 tests,
0.11–0.27 s each) hammers `_splice_sources` + `bump_generated`
(YAML load AND dump) per test. Production YAML/regex fixes therefore
accelerate both the perf gate and the suite. A full-suite profile found
no other actionable Python hot spot: the remaining time is imports
(0.77 s of the `test_derive_insights` module's 1.54 s), pytest overhead,
and one already-batched `git log` subprocess (0.20 s, memoized in
`edition_index.py::_batch_add_dates`).

## 2. Evidence (measured 2026-09-01, this box; Python 3.14.4)

### 2.1 cProfile, production entry points (dry-run, live corpus)

| Script | Total | Dominant cost | Share |
|---|---|---|---|
| `derive_insights.py` | 6.034 s | `yaml.safe_load` (586 calls, scanner/parser/composer) | 3.220 s = 53% |
| | | `iter_company_sections` (2,909 calls, regex section split) | 0.865 s = 14% |
| | | `extract_metrics` (1,196 calls) | 0.686 s = 11% |
| `derive_events.py` | 1.803 s | `re.Pattern.search` — **270,653 calls** | 1.013 s = 56% |
| | | `_iter_bullets` (90,205 yields) + `re.split` (100,579) | 0.468 s = 26% |
| `derive_themes.py` | 0.688 s | `re.Pattern.search` — **81,930 calls** | 0.335 s = 49% |

Output parity baselines recorded in the Appendix (event/theme counts)
— these are the correctness anchors for S4.

### 2.2 libyaml C vs pure-Python, real frontmatter (60 company notes, 30 reps)

| Operation | Pure-Python | C (libyaml) | Speedup |
|---|---|---|---|
| load (`SafeLoader` → `CSafeLoader`) | 2.307 s | 0.237 s | **9.7x** |
| dump (`SafeDumper` → `CSafeDumper`) | 1.609 s | 0.240 s | **6.7x** |

Both C variants are present in `.venv` (PyYAML built with libyaml).
A second run (50 notes, 20 reps) measured load at 10.1x — consistent.

### 2.3 Inventory — who is on which loader today

Already converted (house pattern, try `CSafeLoader` → fall back):
`static_checks.py`, `verify_notes.py`, `frontmatter_schema.py`.

Still pure-Python, ranked by hotness:

| Site | Hotness |
|---|---|
| `helpers/graph/derive_insights.py:184, 254` | the failing perf benchmark |
| `helpers/core/frontmatter.py:224` (`bump_generated`) + `:100` (`render_frontmatter` → `yaml.safe_dump`) | shared core; per-note on every derive/backfill/parse render |
| `app.py:349` | per-request frontmatter route |
| `helpers/graph/derive_cited_in.py:157` | per-note loop |
| `helpers/misc/okf_verify.py:54` | whole-vault sweep |
| `helpers/misc/backfill_okf_provenance.py:126` | whole-vault sweep |

Test files: ~25 `yaml.safe_load` sites across 10 test modules (assertion
helpers re-parsing rendered notes). Mojo/ Python sources
(`Mojo/bench/*.py`): audited clean — they are benchmark harnesses,
already on the faster `regex` module with `re.compile`, and their YAML
leg runs vendored mojo-yaml in compiled Mojo, not Python. No action.

### 2.4 Test-suite profile (context; already optimized by the xdist arc)

| Measurement | Result |
|---|---|
| `tests/test_derive_insights.py` (144 tests) | 1.54 s; cProfile: imports 0.77 s, one batched `git log` 0.20 s, YAML ~0.05 s |
| 7 YAML-heavy modules (268 tests) | 4.76 s; slowest: `test_live_corpus_is_clean` 0.73 s (already C-loader), `mojo format` subprocess 0.41 s |
| regex per test file | ≤ 3 uses — not a test-side problem |

Verdict: the suite gains from production fixes (fuzz-regions class most),
not from test-side edits. The one cheap test-side improvement — reusing
the shared loader in the 10 fm-parsing helpers — is optional S5.

### 2.5 Ruled out (measured, do not re-audit)

- **numpy** — rejected 2026-08-17 (perf_improvs.txt): "too heavy (~15MB)
  for tiny 64-dim vector ops". No `import numpy` exists in helpers/ or
  app.py; heavy numerics already live in C (Onager, sqlite-vec,
  llama.cpp, FTS5).
- **Mojo regex bridge for hot paths** — completed.md #181: vendored
  mojo-regex measured and removed, "native engine not ready" (2026-08-29).
  The `regex`-module interop (#180) stays in Mojo bench scope.
- **Inline-regex compilation in derive_events/derive_themes** — already
  done (perf_improvs P1); the residual cost is search COUNT, hence S4's
  prefilter/literal collapse, not more `re.compile`.

## 3. Design

### S1 — shared C-preferring load/dump in `helpers/core/frontmatter.py`

Add two module-level helpers (names final at implementation):

```python
try:  # libyaml: 5-10x (load) / ~7x (dump) vs pure Python, measured §2.2
    from yaml import CSafeLoader as _SafeLoader, CSafeDumper as _SafeDumper
except ImportError:  # PyYAML without libyaml
    from yaml import SafeLoader as _SafeLoader, SafeDumper as _SafeDumper


def yaml_safe_load(text: str) -> Any: ...  # yaml.load(text, Loader=_SafeLoader)
def yaml_safe_dump(obj) -> str: ...  # yaml.dump(obj, Dumper=_SafeDumper, **_YAML_DUMP_KW)
```

The try/except then lives ONCE (today the dance is copy-pasted in 3
validator files and missing from 6 more). Wire the module's own two
sites: `render_frontmatter` (:100, dump) and `bump_generated` (:224,
load). Keep `render_frontmatter`'s byte contract: S1's gate is a
whole-vault round-trip parity check (§4).

### S2 — `derive_insights.py` on the shared loader

Replace the two `yaml.safe_load` sites (:184, :254) with the S1 helper.
This is the slice that moves the perf gate: ~2.9 s of pure-Python YAML
becomes ~0.3 s, projecting `derive_insights` 6.0 s → ~3.1 s (profiler
inflated; wall-clock ~3.5 s → ~1.6 s by the same 53% share).

### S3 — remaining production sites

`app.py:349`, `derive_cited_in.py:157`, `okf_verify.py:54`,
`backfill_okf_provenance.py:126` → S1 helper. Mechanical; each is a
one-line swap plus import. The 3 validator files may also be pointed at
the helper (deletes their local try/except) — optional, behavior-neutral.

### S4 — regex hotspot collapse (independent of S1–S3)

1. **`derive_themes.py`** — `_THEME_PATTERNS` is ~110
   `re.compile(re.escape(alias))` entries: pure literal substring tests.
   Replace the per-alias search loop with either one compiled
   per-theme alternation (`re.compile("|".join(map(re.escape, aliases)))`)
   or C-speed `in` on the already-lowercased `scan_text`. Semantics are
   identical (substring containment, no word boundaries). 82K searches
   → 12 searches (one per theme) or 12 × `in` scans.
2. **`derive_events.py`** — each bullet window runs up to 7 regexes.
   Add a cheap literal prefilter before the battery (e.g. guidance
   windows must contain one of `FY`, `Q`, `%`, `revenue`, ...; management
   windows one of `appoint`, `resign`, `CEO`, `CFO`, ...). The prefilter
   token sets are DERIVED from the compiled patterns and parity-gated:
   the full corpus run must reproduce today's 376 events / identical
   by_type histogram (Appendix baseline) before landing.
3. **`extract_relations.py`** — hoist the ~14 inline `re.sub` / `re.split`
   / `re.match` / `re.search` calls (lines 289, 346, 352, 361, 961, 971,
   1022, 1257, 1296, 1615, 1676, 1693, 1699, 1908) to module-level
   compiled patterns, mirroring the file's own existing convention
   (lines 480–875). Purely mechanical, no behavior change.

### S5 (optional) — test-side adoption

Point the 10 test modules' fm-parsing helpers at the S1 loader. Low
value (§2.4) but deletes the last pure-Python parse sites and keeps a
single loader convention repo-wide.

Order: S1 → S2 → S3 (loader chain, each landable alone); S4
independent; S5 last. After any `doc/**` edit in this arc:
`make search-fresh APPLY=1` per AGENTS.md.

## 4. Acceptance criteria & shakedown

1. `make perf` green with `derive_insights` back under its 4.0 s budget —
   **3 consecutive runs** (timing-shaped, never one run).
2. Byte-parity for the dump path: for every `findata/**/*.md` note,
   `render_frontmatter(yaml_safe_load(fm_text))` reproduces the original
   frontmatter block byte-identically (one-off script; CSafeDumper vs
   SafeDumper formatting drift is the S1 risk this retires). The
   existing verify_notes / static_checks / integrity gates re-checked
   green afterwards.
3. S4 parity gates: `derive_events` reproduces 376 events with the
   identical by_type histogram; `derive_themes` reproduces 359 edges and
   the 12 per-theme counts (Appendix baselines) — dry-run diff, no
   `--apply` needed.
4. Default pytest suite green (`make test`); the 7 YAML-heavy modules
   re-timed ×3 and reported (expected modest gain, §2.4).

| Projected outcome | Today (measured) | After |
|---|---|---|
| `make perf` derive_insights (wall) | 3.5–3.7 s (budget 4.0 s, failing under profiler) | ~1.6–2.0 s |
| derive_events wall (dry-run) | ~1.5 s (1.8 s profiled) | ~0.9–1.2 s |
| derive_themes wall (dry-run) | ~0.6 s | ~0.35 s |
| YAML load sites on pure-Python loader | 6 production files + ~10 test files | 0 |
| `app.py` frontmatter route parse | pure-Python per request | 9.7x faster parse |

## 5. Risks

- **CSafeDumper output drift vs SafeDumper** — mitigated by the §4.2
  whole-vault byte-parity gate BEFORE wiring callers; if drift appears on
  any note, ship load-only (S1 load half) and keep SafeDumper, losing
  only the 6.7x dump win (dump is the smaller share).
- **derive_events prefilter false negatives** — a window the prefilter
  rejects but a battery regex would accept silently drops events.
  Mitigated by deriving prefilter tokens mechanically from the patterns
  and the §4.3 corpus-parity gate; prefilter stays additive-or-nothing.
- **libyaml absent on a future host** — the try/except fallback keeps
  pure-Python behavior; correctness never depends on the C ext.
- **extract_relations hoist touching a 2,100-line file** — mechanical
  pattern-for-pattern moves only, no regex semantics edits; extract
  parity asserted by its existing tests (`tests/test_extract_relations*`).

## 6. Non-goals

- No numpy/numba adoption (rejected 2026-08-17, §2.5) and no new
  dependencies — PyYAML + libyaml are already installed.
- No Mojo bridge for any Python hot path (#181: measured, not ready).
- No changes to graph algorithms, sqlite-vec, llama.cpp paths (already C).
- No pytest machinery changes (xdist/conftest arc of 2026-08-31 stands).
- No `regex`-module migration for derive_* scripts (stdlib `re` is not
  the bottleneck at these pattern sizes; S4 removes the volume instead).

## 7. Results — measured after execution (2026-09-01, this box)

All five slices landed same-day. Parity gates were exact everywhere:
vault round-trip 0/1243 mismatches (loader object-graph AND dumper bytes),
`derive_events` 376 events with the identical by_type histogram and
promoted/extracted split, `derive_themes` 359 edges with all 12 per-theme
counts, `extract_relations` old-vs-new dry-run byte-identical (12,930 chars
across the 3 newsletter trees). `make perf` 22/22 after every slice.

### Production scripts (wall clock, warm, full corpus)

| Script | Before | After (×3 runs) | Gain | Budget |
|---|---|---|---|---|
| `derive_insights` | 3.5–3.7 s (perf_report 2.88–3.73) | 2.16–2.27 s in-harness · 2.41–2.52 s standalone | −35% | 4.0 s ✅ |
| `derive_events` | ~1.5 s | 0.73 s in-harness · 0.80–0.81 s standalone | −47% | 3.0 s ✅ |
| `derive_themes` | ~0.6 s | 0.50 s | −17% | — |
| `extract_relations` (perf leg) | 1.71 s | 1.69 s | neutral (mechanical hoist, no claim) | 5.0 s ✅ |
| `verify_notes` (leg) | 0.56 s (already C-loader pre-arc) | 0.45–0.56 s | neutral | 3.0 s ✅ |
| `static_checks` (leg) | 2.53 s (already C-loader pre-arc) | 2.41–2.53 s | neutral | 8.0 s ✅ |

`pdf_pipeline_local` (outside this arc's code): budget tightened 20.0 s →
7.0 s the same day after re-measuring the leg at 3.09–3.31 s ×3 — the
"Warm ≈7.5s" comment was a stale corpus/machine state; 2.2x headroom kept.

### Micro-benchmarks (same corpus, A/B in-process)

| Hot spot | Before | After | Gain |
|---|---|---|---|
| Whole-vault YAML load scan (1,243 notes) | 4.0 s | 0.5 s | 8x (§2.2 microbench: load 9.7x, dump 6.7x) |
| `derive_events` fiscal gate (89,127 windows) | 350 ms | 56 ms | 6.3x |
| `derive_events` verb gate (89,127 windows) | 524 ms | 238 ms | 2.2x |
| `derive_themes` alias loop (82K `re.search`) | 289 ms | 216 ms (`in`/memmem, 0 regex) | 1.3x |

The §3-S4 alternation option was measured and REJECTED: 341 ms — slower
than both the status quo and `in` on this corpus (alternation-engine cost
per scan). The `in` variant is also the simplest code (no regex at all).

### Test suite (§4.4)

| Set | Before | After | Gain |
|---|---|---|---|
| `test_derive_insights.py` (144 tests) | 1.54 s | 1.20 s | −22% (residue is 0.77 s fixed import cost) |
| YAML-heavy modules | 4.76 s / 268 tests | 4.22–4.31 s / 342 tests (8-module superset) | more tests, faster |

S5 swapped all 26 test-side `yaml.safe_load` sites across 11 modules
(`yaml.safe_load_all` in test_templates stays — no load_all helper);
`test_fuzz_parse_newsletter` follows that file's `core.*` path style.

### Structural deltas

- Pure-Python `yaml.safe_load` sites: 6 production files + ~10 test files
  → **0** repo-wide; one shared C-preferring helper
  (`helpers/core/frontmatter.py::yaml_safe_load/yaml_safe_dump`).
- `derive_events` regex volume: 270,653 `re.search` → literal prefilters
  reject ~98% (fiscal) / ~99.97% (verb) of windows before any regex runs;
  prefilter tokens derived mechanically from the pattern text
  (additive-or-nothing, filter parity proven on all 89,127 windows).
- `derive_themes`: 81,930 `re.search` → 0 (plain substring tests).

### Deviations from plan

- `derive_insights` landed ~2.2 s vs the projected ~1.6–2.0 s: the
  residual is the regex section-split + `extract_metrics` path —
  explicitly outside S1–S5 scope (would be a future S6-class slice).
- S3 validator dedup: `static_checks.py` deliberately NOT repointed — its
  PyYAML is optional by design (`yaml = None` degradation for the
  run-anywhere hygiene gate) and it already ran CSafeLoader, so the swap
  would be neither behavior-neutral nor a perf gain. `frontmatter_schema.py`
  gained the standard sys.path bootstrap, making bare-script invocation
  work (previously only `-m` / via static_checks).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-01 | `.venv/bin/python3 -m cProfile -s cumulative helpers/graph/derive_insights.py` | 6.034 s; safe_load 586 calls 3.220 s cum | quotes=2742 metrics=1496 (dry-run) |
| 2026-09-01 | `.venv/bin/python3 -m cProfile -s cumulative helpers/graph/derive_events.py` | 1.803 s; re.search 270,653 calls 1.013 s | companies=1078 events=376 (dated 59) |
| 2026-09-01 | `.venv/bin/python3 -m cProfile -s cumulative helpers/graph/derive_themes.py` | 0.688 s; re.search 81,930 calls 0.335 s | themes=12 edges=359 |
| 2026-09-01 | derive_themes per-theme histogram | API 19, BES 27, Bev 18, C+1 9, DCI 51, DefInd 7, EV 21, EMS 9, MII 50, PLI 18, Prem 41, RE 89 | S4 parity anchor |
| 2026-09-01 | derive_events by_type | acq 41, jv 67, guid 263, mgmt 5; promoted 108, extracted 268 | S4 parity anchor |
| 2026-09-01 | microbench: 60 FM × 30 reps, load | SafeLoader 2.307 s vs CSafeLoader 0.237 s (9.7x) | (50-note run: 10.1x) |
| 2026-09-01 | microbench: 60 FM × 30 reps, dump | SafeDumper 1.609 s vs CSafeDumper 0.240 s (6.7x) | repo _YAML_DUMP_KW |
| 2026-09-01 | `.venv/bin/python3 -m pytest tests/test_derive_insights.py -q -p no:xdist` | 144 passed 1.54 s | profile: imports 0.77 s, poll 0.20 s |
| 2026-09-01 | pytest 7 YAML-heavy modules --durations=15 | 268 passed 4.76 s | slowest: corpus walk 0.73 s, mojo format 0.41 s |
| 2026-08-28→09-01 | `make perf` (perf_report.txt) | derive_insights 2.88–3.73 s vs 4.0 s budget | trigger for this proposal |
| 2026-08-17 | perf_improvs.txt (archived) | derive_events 356K re.search documented P1 | compile fixes landed; volume remains |
