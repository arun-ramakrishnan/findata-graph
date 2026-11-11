---
title: "Swap residual stdlib json.dumps sites to jsonio (orjson) and hoist recurrent re.compile sites"
status: executed
filed: "2026-11-11"
executed: "2026-11-11"
completed_md: "504"
area: helpers
---

# Swap residual stdlib json.dumps sites to jsonio (orjson) and hoist recurrent re.compile sites

**Date:** 2026-11-11 · **Status:** EXECUTED 2026-11-11 ·
**Area:** helpers/analytics, helpers/maintenance, helpers/misc, helpers/graph, helpers/validators

## 1. Motivation

The orjson repo-wide cutover (`#454`, S0/S1 landed 2026-10-29) converted the
`loads` side of the mass lanes (`harvest_conversations`, `agent_traces`,
`model_analytics` — all 40 loads sites) and shipped `helpers/core/jsonio.py`.
The **dumps side was left on stdlib `json`** in the same files, and a census
of `re.compile` call sites found several recurrent per-item compiles that sit
on batch/gate paths. Both are mechanical, low-risk wins with a measurement
story.

Measured context (from `db_scaling_tuning.md` §6.3 bench): stdlib json
~800 ms vs orjson text ~515 ms (1.55–1.6×) per 81 MB corpus pass; orjson
dumps is ~2× on the same shapes. The regex hoists are hygiene-scale (µs per
call) but pay off on multi-thousand-item legs.

## 2. Slices

### S1 — `json.dumps` → `jsonio.dumps` (orjson) at residual mass-lane sites

`helpers/core/jsonio.py` exposes `dumps(obj, *, default=None, sort_keys=False, indent=None)`
returning `str` via `.decode()` (jsonio.py:74–95); `indent=2` uses
`OPT_INDENT_2`, `sort_keys` uses `OPT_SORT_KEYS`, `indent=1` falls back to
stdlib. `orjson` emits compact UTF-8 separators and unescaped non-ASCII by
default — matches the JSONL writers' intent.

| Site | File:line | Why |
|---|---|---|
| event row for delegation logs | `helpers/analytics/agent_traces.py:1800` | per-line row build in the 81 MB/pass lane |
| event row for warn/error logs | `helpers/analytics/agent_traces.py:1819` | ditto |
| tool-input truncation | `helpers/analytics/agent_traces.py:2020` | per tool call, `json.dumps(arg)[:_TOOL_INPUT_HEAD]` |
| tools JSON cap | `helpers/analytics/agent_traces.py:2058` | per tool call |
| head JSON | `helpers/analytics/agent_traces.py:719` | sort_keys=True; orjson OPT_SORT_KEYS |
| summary print | `helpers/analytics/agent_traces.py:3078–3080` | indent=2, default=str; drop the local `import json` |
| tier-watch packet JSONL | `helpers/analytics/bili_tier_watch.py:466` | per tick append, ensure_ascii=False |
| tier-watch sample print | `helpers/analytics/bili_tier_watch.py:760` | indent=2 |
| tunewatch timeline JSONL | `helpers/analytics/bili_tunewatch.py:265` | per session row, compact separators |
| monitor output | `helpers/analytics/bili_monitor.py:212` | per monitored row |

Excluded: `model_analytics.py:1688` (`indent=1` → silent stdlib fallback), all
sub-KB config sites (S0 rule), print-only/one-shot paths.

Imports: `helpers/analytics/agent_traces.py` already imports `jsonio`; the
other three files need `from helpers.core import jsonio` added. After the swap,
drop now-unused `import json` lines (check each file; e.g. agent_traces.py:49
and the local import at :3078).

### S2 — hoist recurrent `re.compile` sites (regex hygiene)

Corrected AST census (33 sites total; seed_nic2008 one-shot-per-PDF excluded
per sweep ruling). Targeted list — batch/gate legs with per-item call counts:

| Site | File:line | Note |
|---|---|---|
| `_parse_yaml_field` | `helpers/graph/extract_relations.py:1606` | per-field regex build in graph extraction; hoist to module const w/ `re.escape(field)` interpolation kept local |
| `_update_frontmatter` | `helpers/maintenance/enrich_from_yfinance.py:554` | per company row |
| `_file_enrichment` | `helpers/maintenance/rebuild_doc_search.py:408` | per file; also hoist the inline `re.search(r"\.(png\|jpg\|jpeg\|svg\|gif)$", tgt, re.I)` at :403 |
| `locate_make_target` | `helpers/misc/search_tui.py:2107` | per target lookup; use `functools.lru_cache(maxsize=128)` on the target-specific pattern |
| `check_normalization` | `helpers/misc/database_integrity_check.py:1116` | per gate run |
| `_backup_memory_sidecars` | `helpers/maintenance/db_maint.py:723` | per backup run |

Deferred (maintenance utilities, tiny call counts): `move_sector.py` (5),
`build_sector_hierarchy.py` (2), `index_sync.py:174`, `rename_entity.py:51`,
`sync_sector_wikilinks.py:164`, `derive_co_mentions.py:130/138`, `seed_nic2008.py:882`.

### S3 — (dropped after verification) `SECTION_RE` linear rewrite

Already landed inside S6 of `quadratic_regex_sweep_and_cleanups`:
`helpers/core/parse_newsletter.py:90–97` now uses `[^\S\n]*` with a comment
documenting the O(n²) fix. Nothing to do.

### SQL — no unclaimed win

`sql_query_improvements.md` B1 (`_shortest_path_cte` cycle-guard re-parse) and
the `db_scaling_tuning.md` addendum levers (FTS5 optimize,
`preserve_insertion_order=false`) were all MEASURED-NOT-ADOPTED; nothing open.

## 3. Measurement plan (baseline → convert → after)

Baselines are captured BEFORE any edit (artifact: `$TMPDIR/opencode/bench_quick_wins.py`).

- **B1 dumps microbench:** representative payload shapes from S1 sites
  (delegation event, log row, tool input/fr, head dict) × 50k iterations
  each, stdlib `json.dumps` vs `jsonio.dumps` (best-of-3, wall + MB/s +
  output-size parity). Expect ~2× on dumps throughput.
- **B2 cProfile, agent_traces event-leg:** run the delegation/log event-row
  collection over a real log snapshot; report cumulative time in
  `json.dumps` before vs `jsonio.dumps` after.
- **H1 hoist microbench:** 100k calls with inline `re.compile` vs precompiled
  const; extrapolate by per-run call counts at the S2 sites.
- Parity: for every converted shape, `json.loads(jsonio.dumps(x)) == x`
  (semantic round-trip) plus existing `tests/test_jsonio.py` (11 tests) and
  `tests/test_fuzz_jsonio.py` stay green. Targeted tests only — no
  `make pytest`/`make test`; full gate arc on operator go.

Acceptance: B1 ≥1.5× dumps; B2 shows json.dumps cumulative → jsonio.dumps
with no regression in row counts; H1 confirms per-call compile overhead and
shows the hoisted sites drop to ~0; all targeted tests pass; `ruff` clean on
touched files.

### 3.1 Measured results (2026-11-11, after conversion)

Bench artifacts: `$TMPDIR/opencode/bench_quick_wins.py`; baseline run taken
before any edit, identical runs after. `jsonio` = orjson via
`helpers/core/jsonio.py`.

**B1 — dumps microbench (50k reps, best-of-3 wall):**

| shape | Before (stdlib) | After (orjson) | Speedup |
|---|---|---|---|
| delegation-row (130 B) | 0.111 s | 0.027 s | 4.2× |
| log-warn-row (92 B) | 0.098 s | 0.025 s | 3.9× |
| tool-input (717 B) | 0.200 s | 0.028 s | 7.2× |
| tools-list (2,258 B) | 0.641 s | 0.070 s | 9.1× |
| head-dict-sorted (60 B) | 0.141 s | 0.029 s | 4.9× |

Post-edit rerun identical within variance (4.5/4.0/6.9/10.5/4.9×). Output
sizes identical (130/92/717/2258/60 B) — compact separators match the
JSONL writers' intent.

**B2 — real ledger rows** (`~/.prime/agent/rlm-ledger/20d0e37e665dc969.jsonl`,
38 lines): full loads+dumps row loop: before (stdlib) ~0.000 s, after
(orjson) ~0.000 s — both sub-ms, ratio 4.1–4.3×. No whole-leg wall delta is
claimable at current ledger size; the win is per-row throughput.

**H1 — hoist microbench (100k calls):** before (inline `re.compile`) 0.043 s,
after (precompiled) 0.019 s → **0.23 µs/call overhead**. Per-line search shape
(ref_line, 50×10k lines): before 0.179 s, after 0.179 s — 1.00× (compile
count is per pass, not per line). Verdict: S2 is hygiene, not a wall win;
keep because it also removes per-call `re` cache churn on the gate paths.

**Parity:** `json.loads(jsonio.dumps(x)) == x` for every converted shape ✓.
Tests: `tests/test_jsonio.py` + `tests/test_fuzz_jsonio.py` +
`tests/test_extract_relations_yaml.py` + `tests/test_fuzz_rebuild_doc_search.py`
+ `tests/test_enrich_from_yfinance.py` → **125 passed**; `ruff check` clean on
all 10 touched files; `py_compile` clean.

**Conversion summary (all landed in-tree, uncommitted):**
- `helpers/analytics/agent_traces.py` — 5 `json.dumps` → `jsonio.dumps`
  (:719 :1800 :1819 :2020 :2058) + summary print (:3078); dropped `import json`.
- `helpers/analytics/bili_tier_watch.py` — :466 JSONL packet, :760 sample
  print; +jsonio import; dropped `import json`.
- `helpers/analytics/bili_tunewatch.py` — :265 timeline row; +jsonio import;
  dropped `import json`.
- `helpers/analytics/bili_monitor.py` — :212 monitor row; +repo-bootstrap +
  jsonio import; dropped `import json`.
- `helpers/graph/extract_relations.py` — `_yaml_field_re` `@cache` helper
  (:1606 site now cached).
- `helpers/misc/search_tui.py` — `_make_target_re` `@functools.lru_cache(maxsize=128)`.
- `helpers/misc/database_integrity_check.py` — `_NAME_OK_RE` module const.
- `helpers/maintenance/db_maint.py` — `_SECRET_RE` module const; top-level
  `import re` replaces the local one.
- `helpers/maintenance/enrich_from_yfinance.py` — `_INDUSTRY_RE` module const.
- `helpers/maintenance/rebuild_doc_search.py` — `_REF_LINE_RE` + `_IMG_SUFFIX_RE` module consts.

## 4. Risks / rollback

- **Byte-level output change:** orjson output is compact UTF-8, byte-different
  from stdlib (unescaped non-ASCII, no spaces). Consumers of the converted
  columns (agent_traces.duckdb event rows, JSONL writers) only store/display;
  nothing re-parses these exact bytes. Semantic round-trip parity is the
  contract (B1 parity). If any downstream byte-equality test exists it will be
  found by targeted tests before landing.
- JSONL writers: orjson does not emit `ensure_ascii` escape noise — same
  readable intent; `separators=(",",":")` default matches orjson compact form.
- S2: regexes are byte-identical; only compilation frequency changes. No
  behavior change possible; `lru_cache` on `locate_make_target` bounded at
  128 entries.
- Rollback: single-file reverts; every change is independent and small.

## 5. Housekeeping

- Filed: `doc/improvements/proposals/perf_quick_wins_json_dumps_regex_hoist.md`
  (this file). On execution: `git mv` to archive + completed.md entry
  (unique number) + archive/README.md index line + frontmatter flip +
  `make search-fresh APPLY=1` (+ `--allow-stale` if mid-arc) per
  `proposals/README.md`.
