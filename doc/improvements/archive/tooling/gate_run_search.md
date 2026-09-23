---
title: "gate_query — DuckDB index over gate run reports (latest-run/failure iteration, timings, zstd rotation)"
status: executed
filed: "2026-09-23"
area: "helpers/misc"
executed: "2026-09-24"
completed_md: "282"
---

# gate_query — DuckDB index over gate run reports

**Date:** 2026-09-24 · **Status:** EXECUTED — archived 2026-09-24

Child of the outputs/ report convention (`doc/templates/report.md`;
append-across-runs) and the search-surface family (`doc_query` /
`script_query` / search_tui lane 6). Pattern reference:
`bench_data/code/model_analytics.py` (one local DuckDB store, load /
report verbs, idempotent).

## 1. TL;DR

Prompts running `make qa/advisory/perf` burn tokens on three things:
finding where the LATEST run starts inside append-only report files,
extracting WHICH tests failed and why, and re-deriving leg timings the
reports already contain. All three are already written to disk in a
machine-parseable shape (`**Generated/Started/Elapsed**` meta lines +
`| leg | seconds | status |` tables) and already parsed once —
in-memory, per query, whole-file — by search_tui
(`parse_gate_report` / `parse_perf_report`). This proposal adds the
missing persistence + query surface:

- **`helpers/misc/gate_query.py`** — a DuckDB-indexed, byte-offset
  incremental parser + query CLI over `outputs/*_report.md` (main) and
  `outputs/wt/<name>/outputs/*_report.md` (worktrees). The agent
  contract is two commands: `latest` (the newest run as a ~15-line
  digest: when, wall, exit, per-leg one-liners) and `failures` (failed
  legs/tests + error heads, `--full` for complete text). Iterate on
  failed runs WITHOUT parsing huge text.
- **Writer enhancements** (one file, `tests/run_gate_report.py`):
  per-gate `--junitxml` for pytest legs (exact per-test outcomes +
  durations, xdist-safe) and three extra meta lines (`**Commit:**`,
  `**Worktree:**`, `**Exit:**`) so runs are self-describing.
- **Rotation/cleanup**: report files grow unbounded (integrity report
  is 22 MB). `gate_query rotate` moves byte-exact run prefixes to
  `outputs/archives/<stem>/<stem>.<ts>.zst` (stdlib `compression.zstd`,
  PEP 784 — verified on this box's 3.14), keeps the live file bounded,
  and the index keeps every archived run queryable (offsets stay valid
  inside the decompressed archive).

Storage answer: **DuckDB, yes** — the queries are structural
(latest run, failed tests, timing history), not semantic, so this is
relational OLAP, NOT a doc_search-style FTS/embedding index. What we
keep from doc_search is the philosophy: the reports stay the source of
truth, the index is derived and rebuildable, refresh is cheap, and the
surface is a thin query CLI.

## 2. What exists (evidence, this box)

- Report format: `# make <gate> — gate|maint report` header,
  `**Generated:** … **Started:** … **Elapsed:** 55.2s · **Python:** …
  jobs=N` meta line, `| leg | seconds | status |` table with a bold
  summary row; perf variant: `# make perf — benchmark report` +
  `| Benchmark | Time (s) | Budget | Status |` (budget column already
  machine-readable).
- Parser: `helpers/misc/search_tui.py` — `RunBlock(gate, timestamp,
  jobs, steps, summary, elapsed)`, `RunStep(label, seconds, status)`,
  `parse_gate_report` / `parse_perf_report` / `report_files()` (main +
  `outputs/wt/<name>/outputs/` with `@wt` naming). Import-safe: stdlib
  only at module top.
- Corpus: 8 report families, 24 MB total, append-only, flock-guarded
  appends by `tests/run_gate_report.py` (`qa|integration|advisory`);
  `perf` via `tests/run_perf_benchmarks.py`; `maint` via
  `helpers/maintenance/maint.py`.
- Gaps: whole-file re-parse per query; no persistence; no per-test
  rows (pytest console text only); no timing history across runs; no
  CLI (lane 6 is TUI-only); unbounded growth.

## 3. Index model (DuckDB, `outputs/gate_runs.duckdb')

Lives in the MAIN repo's `outputs/` (next to the corpus it indexes;
gitignored with it; survives worktree removal). Single writer = the
refresher; readers open read-only. Raw reports remain authoritative —
drop the DB and `refresh --full` rebuilds it from text.

    runs(run_id INTEGER PK, src_rel TEXT, wt TEXT, gate TEXT,
         started_at TIMESTAMP, generated_at TIMESTAMP, elapsed_s DOUBLE,
         jobs INTEGER, python TEXT, commit TEXT, patch TEXT,
         exit_code INTEGER, summary TEXT,
         header_offset UBIGINT, nbytes UBIGINT,
         junit_path TEXT, arch_path TEXT,  -- NULL while live
         complete BOOLEAN)
    legs(run_id, leg TEXT, seconds DOUBLE, status TEXT)          -- from leg table
    bench(run_id, bench TEXT, seconds DOUBLE, budget_s DOUBLE, status TEXT)
    tests(run_id, leg TEXT, node_id TEXT, outcome TEXT,          -- junitxml (+fallback)
          seconds DOUBLE, err_head TEXT, err_blob TEXT)          -- err caps 16 KB
    parse_state(src_rel TEXT PK, offset UBIGINT, size UBIGINT,
                mtime_ns BIGINT, updated_at TIMESTAMP)

- `wt` = '' (main) or the worktree name from the path; every verb
  filters `--wt NAME` (default: main; `--all-wt` to aggregate).
- `commit/patch/exit_code` arrive with the writer enrichment (§6);
  NULL for historical blocks, parsed around, never guessed.

## 4. Incremental refresh (the model_analytics twist)

model_analytics replaces-by-window because its sources mutate in
place. Gate reports only APPEND, so refresh is byte-offset incremental:

1. `stat()` the file; if `size < offset` (rotation by hand, truncate)
   → reset offset to 0 and re-parse that file (rows upserted by
   `(src_rel, header_offset)` — idempotent).
2. Read `data[offset:]`, split into blocks on the existing header
   regexes; parse each complete block with search_tui's builders
   (imported, not duplicated — single parsing truth with the TUI).
3. A block is COMPLETE only when a successor header exists or the file
   ends with the writer's exit line. The trailing incomplete block
   stays pending: offset advances only past complete blocks, so a
   half-written live run is indexed on the NEXT refresh, never wrong.
4. junitxml: if `outputs/.junit/<gate>.junit.xml` has mtime inside the
   run's [started, generated] window, ingest per-test rows
   (`tests/...` above) and record the path.
5. Typical refresh after one gate = the new block only (~ms). First
   index build over the full 24 MB corpus is a one-off ~seconds.

## 5. The agent contract (why this saves the prompt)

    gate_query latest [--wt NAME] [--gate qa|perf|...|all]   # -wt = --wt;
        --gate all lists the newest run PER GATE, not one global winner
      → digest: gate, started, elapsed, exit, jobs, per-leg one-liners,
        FAILED leg names. ~15 lines. Replaces "find the start of the
        latest run" greps over a 432 KB file. --gate defaults to qa
        (the gate prompts run); "all" spans gates.
    gate_query failures [--run ID] [--wt] [--full] [--limit N]
      → (--gate default qa, same as latest)
      → newest (or given) run's failed legs + failed/erroring test
        node_ids + err_head (first ~6 lines each); `--full` prints
        complete err_blob or decompresses the archived block.
        Default output stays under ~40 lines.
    gate_query recent [--gate qa] [--last 10] [--tests]
      → run history: every run PASS/FAIL, failed leg names, and (with
        --tests) the failed node_ids under each failed run.
    gate_query failures --recent 15 [--gate ...]
      → cross-run view: one line per failed run among the last N runs
        (default view is single-run; --recent is the failure landscape).
    gate_query tests --run ID [--outcome failed]      # full per-test rows
    gate_query timing --leg graph_l1_betweenness [--last 20] [--all-wt]
      → history table + p50/latest/delta-vs-budget (the bench table is
        already written with budgets — zero writer change needed).
    gate_query grep SUBSTR [--run ID] [--wt]   # keyword over err text
    gate_query refresh [--full]                # auto-runs before every verb
    gate_query rotate [--apply] [--keep-runs 30] [--max-mb 8]

Failure detail comes from junitxml when present; fallback for legacy
blocks (and non-pytest legs) parses the block's own text: `^FAILED /
^ERROR` summary lines + `## <leg> (FAIL)` section tails. Every
failure row keeps `src_rel:header_offset` so `--full` can show exact
context; archived runs are decompressed on demand.

## 6. Writer enhancements (`tests/run_gate_report.py` only)

1. **junitxml**: pytest steps gain `--junitxml outputs/.junit/<gate>.junit.xml`
   (xdist merges into one file; gitignored; overwritten per run).
2. **Meta enrichment** — the report block header gains three lines:
   `**Commit:** <sha>` · `**Worktree:** <name|main>` · `**Exit:** <rc>`
   (rc = overall gate exit, written by `write_report` which already
   appends under the flock). All three are parsed-around if absent, so
   historical blocks keep indexing unchanged.

## 7. Rotation / cleanup (zstd archives)

Trigger: `gate_query rotate` (dry-run plan by default; `--apply`
executes; `--keep-runs N` default 30, `--max-mb` default 8, oldest
first; never splits a block; never touches a file whose last block is
incomplete).

- The prefix (all runs beyond the keep window) is copied BYTE-EXACT to
  `outputs/archives/<stem>/<stem>.<first-ts>.zst` via stdlib
  `compression.zstd` (PEP 784; verified 3.14 on this box), then the
  live file is rewritten with the kept tail.
- Because the archive preserves exact bytes, existing
  `(header_offset, nbytes)` spans remain valid: against the live file
  for kept runs, against the decompressed archive for archived runs
  (`arch_path` set on their rows). No re-indexing, no context loss —
  `failures --full` on a month-old run decompresses and serves.
- `parse_state.offset` adjusts by the archived prefix length in the
  same transaction as the file rewrite (crash between the two = size
  guard in §4 resets cleanly).
- Rotation is file-grain, so it also bounds reports whose block
  parsing lands later (e.g. the 22 MB integrity report — its run
  header is a different writer; indexing its sections is S4).

## 8. Slices

- **S0 — `gate_query.py` core**: schema, incremental refresh, latest +
  failures + timing + grep verbs, worktree enumeration (reuses
  search_tui `report_files()`), `--json`.
- **S1 — writer enhancements**: junitxml + Commit/Worktree/Exit meta
  lines in `run_gate_report.py`; Makefile help lines touch-up.
- **S2 — rotation**: zstd archive verb + keep policy + offset math +
  archive-aware `--full`.
- **S3 — tests**: `tests/test_gate_query.py` — append→refresh→latest
  round-trip; pending-tail non-commit; junit ingest + fallback parse;
  wt naming; rotation dry-run/apply + archive read-back + offset
  adjust; truncate reset.
- **S4 (future)**: integrity/verify-notes block parsers (own header
  formats), `--all-wt` aggregates, FTS over err_blob if grep-like
  queries outgrow LIKE.

## 9. Acceptance

1. `gate_query latest` on the live corpus reproduces the newest
   qa/perf block (spot-check vs manual tail) in < 1 s warm.
2. Injected failing run (tmp corpus): `failures` names the exact
   node_ids + error heads; `--full` matches the source text.
3. Rotation: 40-run synthetic file → `rotate --apply` → live file
   holds the last 30 runs, archive decompresses byte-identical to the
   removed prefix, `latest`/`failures` unchanged, archived `--full`
   serves from the zst.
4. Index rebuild is idempotent: `refresh --full` twice → identical row
   counts (the model_analytics rule).
5. search_tui behavior unchanged (its parsers untouched, only
   imported); existing search_tui tests green.

## 10. Non-goals / risks

- No embedding/semantic lane; no new Python dependencies (DuckDB
  already required by the repo; zstd is stdlib on 3.14).
- No writes to report content except the rotation rewrite (and that
  is byte-preserving for the kept tail).
- DuckDB single-writer: concurrent `gate_query` invocations serialize
  on the DB lock; refresh is ms-scale so contention is negligible
  (`witr -f` diagnoses a stale holder, house pattern).
- Legacy blocks without the enrichment lines index with NULL
  commit/exit — accepted; junit-less runs fall back to text parsing —
  accepted (documented above).
- outputs/ is gitignored: the index is local to this box by design
  (like model_usage.duckdb), rebuilt trivially from the reports.

Parent: `doc/templates/report.md` (report convention); companion
surface: search_tui lane 6 (TUI) — this proposal adds the CLI/index
tier beneath it.
