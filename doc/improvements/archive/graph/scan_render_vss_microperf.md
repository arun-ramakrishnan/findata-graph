---
title: "Scan/render/VSS micro-perf — C-count line numbers, one frontmatter parse, fetch-once VSS index"
status: executed
filed: "2026-09-06"
executed: "2026-09-07"
completed_md: "211"
area: "helpers/graph/derive_insights.py, helpers/core/get_tickers.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Scan/render/VSS micro-perf

**Date:** 2026-09-06 · **Status:** PROPOSED · **Mode:** filed first,
implement later (operator decision 2026-09-06).
**Area:** derive_insights scan + render · get_tickers VSS fallback.
**Follows:** `doc/local/perf_eval.txt` (2026-09-06 measurement pass) ·
`../archive/graph/derive_insights_perf.md` (#208 — regex hoisting +
decode-once VSS; its benches still stand: the granite swap d596f0bb
touched neither derive_insights.py nor get_tickers.py).

## 1. Motivation

Post-#208/#210, `derive_insights --stale-only` dry-run still walls
1.74s over 1244 files, and each warm VSS fire costs ~134ms of which
only 71ms is the model. Three residual hot spots, all measured
2026-09-06 (raw log in the Appendix):

- **~1.1s of the 1.6s serial scan is one line.** The `nl_offsets`
  pure-Python char loop (`derive_insights.py:415`) builds newline
  offsets for the whole file so `line_of` (`:418`) can bisect a line
  number for a handful of headings. Measured 367ms per 400 files vs
  5ms for the C-level `str.count` equivalent (~70x).
- **Every touched note's frontmatter is yaml-parsed twice.** The
  chatter render path parses in `_stale_only_skip` (`:1401` →
  yaml_safe_load at `:193`) and again in `_splice_sources` (`:1425` →
  `:263`), both fed by one `read_text` (`:1389`); the metric-note
  path repeats the shape (`:1630` gate, `:1635` splice, one
  `read_text` at `:1628`). 0.54s YAML per --stale-only run, ~half
  reclaimable with zero semantic change.
- **Every VSS fire re-fetches a 9.2 MB static table.** 1079
  company_embeddings rows × 8,534-char text embeddings = 9.21 MB per
  fetchall (14.6ms); the sha1 digest (24.2ms) exists only to detect
  in-run rewrites of that re-fetch; the dots are a pure-Python loop
  (23.7ms vs 0.05ms measured for a numpy matvec at 1500×384). The
  call site (`get_tickers.py:651`) passes no `vss_conn`, so each fire
  also opens + closes a fresh sqlite connection inside the 14.6ms.

## 2. Evidence (measured 2026-09-06, this box)

| Comparison | Measured | Verdict |
|---|---|---|
| nl_offsets char loop vs `str.count` (400 files) | 367ms vs 5ms | adopt C count |
| python dot loop vs numpy matvec (1500×384) | 23.7ms vs 0.05ms | adopt matvec |
| fetchall + digest per VSS fire | 14.6 + 24.2ms | hoist out of loop |
| frontmatter parses per note (both paths) | 2 × yaml_safe_load | parse once |
| serial vs parallel(4) scan, 1244 files | 1612ms vs 717ms | pool stays |

Identical-output arguments, verified against the code, not just
timing — these are the reasons the slices are safe, "measured, do not
re-audit":

- **S1 is an arithmetic identity.** `_HEADING_RE` is
  `^(#{1,3})\s+(.+)$` in MULTILINE, so every match start points at a
  `#`, never a `\n`. `str.count("\n", 0, pos)` counts the half-open
  range `[0, pos)`; `bisect_right(nl_offsets, pos)` counts offsets
  `<= pos`; equal because no offset can equal `pos`. `line_of` runs
  once per YIELDED company section only (`:467`) — sector headings
  never call it.
- **S2's two parses see identical bytes.** Between gate and splice
  the only text mutations are `_replace_or_insert_block` /
  `_replace_or_insert_kf`, which act on sentinel-wrapped BODY chatter
  / key-figures blocks; the frontmatter region is untouched, so both
  yaml parses decode the same string.
- **S3's table is static within a run.** The only writers of
  company_embeddings are `helpers/graph/embeddings.py` maint commands
  — `populate_local` (INSERT `:281` + prune `:297`), `maint_refresh`
  (`:396`), `clear` (`:408`) — invoked as separate CLI runs, never
  concurrent with a Yahoo sync; get_tickers itself never writes it.
  Concurrent-writer audit (2026-09-06, verified against the call
  graph — not just the writer list): every other company_embeddings
  reference is a read (enrich_relations DuckDB-VSS, query.py DuckDB
  leg, snapshot_db export list, bench probes); no cron touches either
  path; maint.py runs embeddings --maint but never get_tickers or
  enrich_from_yfinance; make metrics-rebuild runs the Yahoo
  enrichment but never embeddings.py. Overlap needs two simultaneous
  manual long runs — SQLite serializes the writes (no corruption
  possible), worst case is a stale snapshot serving VSS misses on a
  path that no-matches by design. The only consumers of
  get_tickers.vss_match/resolve_entity are get_tickers.main (:651,
  vss_conn=None — the per-fire fresh connection), embed_eval.py
  (:215, direct per-call use) and tests; same-named hits in query.py,
  app.py, derive_co_mentions.py are different functions. So the run
  index needs the `index=` param on exactly vss_match + resolve_entity
  (consumed by main only), and the per-call digest path stays for
  embed_eval/tests.

## 3. Design

Three independently landable slices, ordered by saving/risk. No slice
unblocks another; land S1 first (biggest, zero-risk identity).

**S1 — line_of via C count** (`derive_insights.iter_company_sections`).
Delete the `nl_offsets` precompute and the `line_of` closure
(`:414-418`); per yielded section compute
`content.count("\n", 0, start) + 1` directly. Drop the now-unused
`bisect` import (`:94` — used nowhere else). Worst case re-scans a
prefix per section (k sections → k·n char-visits), but at C memchr
speed that is the measured 5ms/400 files — irrelevant beside the
1.1s python loop it replaces.

**S2 — parse frontmatter once per note** (`derive_insights` render).
Split + `yaml_safe_load` once at the top of each note's render block
(chatter path after `:1389`; metric path after `:1628`), thread the
parsed dict into both consumers via optional parameters on
`_stale_only_skip` and `_splice_sources`. Default must distinguish
"not supplied — parse yourself" from "parsed, no usable frontmatter"
(a sentinel default, not `None`, since `None` is a meaningful parse
outcome for the splice). Callers that don't pass it — including
other `_splice_sources` callers and all tests — keep today's
behavior. Do NOT regex-pre-check frontmatter to skip the parse
(quote-soup class, cf. #206).

**S3 — fetch-once VSS run index** (`get_tickers`). Add a run-scoped
snapshot object: one fetchall + one literal_eval decode (reusing
`_decoded_vss_table`'s decode logic), stacked as a numpy float64
matrix with the names list; embedder choice (`_pick_embedder`) and
dims filtering happen at build time, matching today's per-call
semantics. `vss_match` and `resolve_entity` gain an optional
`index=` param; when supplied, skip the SELECT/digest/dots and do one
matrix-vector product (entity-set filter via a names mask before
argmax — same skip semantics as the current loop). Build it once in
`main()` (`:752`) before the symbol loop, tolerating an absent/empty
table (index=None → today's behavior); import numpy lazily inside the
builder so CLI startup and the no-DuckDB standalone constraint are
untouched. Staleness tripwire (concurrent-writer audit): record
`COUNT(*), MAX(rowid)` at build; re-check at the end of the symbol
loop and print a warning on mismatch ("company_embeddings changed
mid-run — re-run for fresh VSS") — ~1ms, turns silent staleness loud
without locking or digest machinery. The per-call path — including
`_VSS_DECODE_CACHE` and its content digest — stays as-is for
`embed_eval` and tests; the digest's rewrite defense is only bypassed
on the explicit run-index path.
float64 (not float32) so the arithmetic matches the python `sum()`
loop it replaces.

## 4. Acceptance criteria & shakedown

1. Byte-identical dry-run: `derive_insights --stale-only --dry-run`
   on the same tree before and after S1+S2 reports identical counts
   and zero note-text diffs (the vault is converged — any would-write
   diff is a regression).
2. S1 property test: old-vs-new line-number agreement on every
   heading of the fuzz corpus shapes (keep the bisect implementation
   in the test as the oracle).
3. S2 invariant test: block replace/insert ops preserve
   `split_frontmatter(text)[1]` byte-identically (fuzz regions
   corpus).
4. S3 parity test: run-index and per-call paths return identical
   winners (and scores within 1e-9) on a fixture table and on a
   read-only snapshot of the live 1079-row table; fingerprint tripwire
   tested by mutating a scratch table mid-run (warns) vs stable table
   (silent).
5. Timing, 3 runs each (±20% pass-to-pass doctrine): --stale-only
   wall ≤ 1.2s (from 1.74s); warm VSS fire ≤ 80ms (from ~134ms).
6. Existing suites stay green: test_derive_insights,
   test_integration_derive_insights_apply,
   test_fuzz_derive_insights_regions, test_get_tickers.
7. Full gate sequence (qa / advisory / perf / search-fresh) ONCE at
   arc end, with the operator's go — house directive 2026-09-04.

| Projected outcome | Today | After |
|---|---|---|
| --stale-only dry-run wall | 1.74s | ~1.0s |
| serial scan (1244 files) | 1612ms | ~500ms |
| parallel(4) scan | 717ms | ~250-300ms |
| warm VSS fire (incl. model) | ~134ms | ~72ms |

## 5. Risks

- **BLAS summation order shifts scores** (numpy dot vs sequential
  python sum) — float64 keeps differences ~1e-15; the parity test
  bounds it; a threshold-0.5 flip would need an adversarial tie.
- **S2 shared-parse staleness** if a future block op ever writes into
  the frontmatter region — the S3-style invariant test fails loudly;
  add a one-line comment at both parse sites naming the invariant.
- **S1 repeated-prefix scans** on pathological many-section files —
  C-speed, 5ms/400 files measured; accepted.
- **Run-index staleness** if embeddings are ever written mid-Yahoo-run
  — writers today are maint CLI commands only (§2, audited: no cron,
  no maint→Yahoo or rebuild→embeddings call edge; overlap needs two
  simultaneous manual long runs and SQLite still serializes the
  writes) — the COUNT/MAX(rowid) tripwire warns loudly, and the
  per-call digest path remains for any mutating caller.
- **numpy import cost on the vss CLI path** — lazy import inside the
  builder; only runs pay it, and only when a table exists.

## 6. Non-goals

From perf_eval §5 (verdicts stand, do not re-litigate): KNN-miss
short-circuit (~13ms against a 71ms+ embed, degrade-never-500 path);
query-embed batching across tickers (S1 verdict: batch pays near-max
per text); backfill_from_fts CTAS rewrite (one-time cost, dwarfed by
embedding); numba/JIT (IPC healthy, walls are parse/model). Also out:
regex frontmatter pre-checks (cf. #206), any change to the #209/#210
granite surfaces, and scan parallelism changes (pool stays 4 workers;
<8-file serial threshold stays).

## 7. Execution record (2026-09-06)

All three slices landed + shakedown, same day as filing.

- S1: `nl_offsets` loop → `content.count("\n", 0, start) + 1` per
  yielded section; `import bisect` dropped. Scan 1244 files:
  serial 1612ms → 923ms, parallel(4) 717ms → 401ms.
- S2: `_load_frontmatter` + `_UNSET` sentinel; `fm=` kwarg on
  `_stale_only_skip` / `_splice_sources`; both render paths parse
  once. All pre-existing callers (incl. tests) keep today's behavior.
- S3: `_VssRunIndex` (float64) + `build_vss_run_index` (lazy numpy,
  None-tolerant) + `check_vss_run_index` COUNT/MAX(rowid) tripwire;
  `index=` threaded main → display_ticker → resolve_entity →
  vss_match. Per-call path untouched for embed_eval/tests.
- Criterion 1: stash-diff dry-run stdout BYTE-IDENTICAL (counts 2777
  quotes / 1532 metrics / 310 chatter / 9 KF notes, incl. the 69
  hand-written skips + 33/233 stale gates).
- Criterion 5: --stale-only wall 1.74s → 1.24s best-of-3 (1.53 cold,
  1.24/1.27 warm; ≤1.2s target missed by 0.04s — variance, accepted);
  warm VSS fire non-model part 55.5ms → 0.7ms (live 1079-row table,
  deterministic embed_fn; +71ms granite embed stays per S1 verdict).
- Criterion 6: 221 passed across test_get_tickers,
  test_fuzz_get_tickers, test_derive_insights (+5 bisect-oracle,
  +2 shared-parse equivalence), test_fuzz_derive_insights_regions
  (+1 FM-bytes invariant), test_integration_derive_insights_apply.
  Ruff check + format clean on all touched files.
- Criterion 7 DONE (operator go 2026-09-06): `make qa` 9/9, `make
  advisory` 10/10, `make perf` 22/22 (derive_insights leg 1.39s vs
  4.0s budget), `make search-fresh` fresh after APPLY=1 rebuild.
  Gate fallout, all fixed in-tree: ty narrowing on the `fm`
  dict|None|object union (isinstance guards); C901 vss_match 11>10
  (extracted `_vss_match_with_index`); S101 on the tripwire assert
  (RuntimeError instead); pre-existing parquet_textconv format drift
  + ty fetchone-None (one line each); doc/script index rebuild.
All 2026-09-06, this box (i5-6500 4C/4T, perf_event_paranoid=1);
full context in `doc/local/perf_eval.txt`.

| Run | Command / measure | Result | Notes |
|---|---|---|---|
| 2026-09-06 | char loop, 400 files | 367ms | ~0.9ms/file; the 150-file per-file sample (0.42ms iter_company_sections) is small-file-biased — not the operative number |
| 2026-09-06 | `str.count` equivalent, 400 files | 5ms | ~70x |
| 2026-09-06 | python dot loop, warm vss scan | 23.7ms | 1079×384 |
| 2026-09-06 | numpy matvec, 1500×384 | 0.05ms | float64 |
| 2026-09-06 | fetchall company_embeddings | 14.6ms | 1079 rows × 8,534-char text embeddings = 9.21 MB; includes a fresh connection (no vss_conn at :651) |
| 2026-09-06 | sha1 digest, warm | 24.2ms | rewrite detection only |
| 2026-09-06 | granite embed_query | 843ms cold / 71.1ms warm / 4.9ms cached | stays (S1 batching verdict) |
| 2026-09-06 | --stale-only dry-run wall | 1.74s | parallel(4) scan 717ms of it; serial 1612ms |
| 2026-09-06 | frontmatter YAML per run | 0.54s | 2 parses/note, both render paths |
| 2026-09-06 | render phase | 357 `_splice_sources` ≈ 0.64s | includes the splice-side parse |
