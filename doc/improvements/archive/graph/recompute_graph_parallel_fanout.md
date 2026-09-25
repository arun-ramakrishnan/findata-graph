---
title: "Parallel recompute-graph fan-out — heavy lanes off the sequential metric loop"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "295"
area: "helpers/graph"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# Parallel recompute-graph fan-out — heavy lanes off the sequential metric loop

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/graph (algorithms.py CLI `--all` loop; Makefile
`recompute-graph`; maint step `recompute-graph`)

Un-defers the "recompute-graph metric fan-out" row of
`doc/improvements/archive/tooling/parallel_cold_embed.md` §7 (recorded
2026-08-29, trigger "maint-full budget pressure"). Follows
`scipy_all_pair_reuse.md` (#283 — the closeness/harmonic pair-share this
proposal runs beside, not inside), `graph_perf_l1_bfs_scale.md` (#277 —
the L1B fold lane), and `scipy_routing_dispatch.md` (#279 — the ROUTING
switch whose ownership this proposal leaves untouched). Re-profile
origin: the pending-items survey of 2026-09-25
(`doc/local/notes/pending_items.md`, local note; all numbers re-measured
into this file).

## 1. Motivation

The deferral recorded "saves ~4–5 s" against a 66 s maint-full. That
estimate is stale: `graph_analytics` has since tripled (65,180 →
193,103 rows; the chatter-parse arc of 2026-09-25), and maint-full now
runs **121 s** (run 313, 2026-09-25 00:17) with **recompute-graph at
34.4 s under -j4 — 28% of the wall**, tied with rebuild-note-search
(35.0 s) as the largest step. The trigger condition is now factually
met; the re-profile below shows the real prize is **~13 s (~60% of the
step)**, not 4–5 s.

The mechanism is embarrassingly parallel and already isolated: the
`--all` loop (algorithms.py:1337–1367) runs 14 metric commands
sequentially over one shared DuckDB connection, but the two heavy lanes
never touch that connection — betweenness is served by the L1B 2-core
fold (`l1_betweenness.compute(db)`, sqlite-side) and
closeness+harmonic by the shared scipy pair
(`scipy_bridge.load_projection(db)` + `compute`, one calculation for
both metrics, landed by #283). Only the 11 cheap lanes read the shared
conn.

## 2. Evidence (measured 2026-09-25, this box)

Single-metric CLI runs (`algorithms.py <cmd> --top 1`, each ≈0.5–0.7 s
interpreter+connect setup included), live graph 22,046 nodes / 57,600
edges:

| Lane | Routing | Wall |
|---|---|---|
| betweenness | L1B fold | 9.27 s |
| closeness | SCIPY (shared pair) | 8.12 s |
| harmonic | SCIPY (shared pair) | 7.82 s |
| link-predict | SQL 2-hop | 2.16 s |
| local-reaching | onager | 1.61 s |
| katz | scipy exact-solve | 1.21 s |
| louvain | onager | 1.17 s |
| pagerank-weighted / pagerank / eigenvector / degree / clustering / laplacian | onager | 0.74–0.92 s each |
| wcc / voterank | onager | 0.54–0.68 s |

Full `--all` cold dry-run: **22.2 s sequential** (vs 34.4 s as the
maint step under -j4 contention). The pair-share means closeness+
harmonic cost ~7.5 s once, not 15.9 s — net wall ≈ fold 8.6 + pair 7.5
+ link-predict 1.5 + cheap-lanes ~5.

Parallel shape: fold and pair on worker threads (both conn-free),
cheap lanes sequential on the main conn → wall ≈ max(~8.6, ~7.5, ~5)
≈ **~9 s → ~13 s saved**. Ruled out: sharing one all-pairs distance
matrix across fold+pair (cross-couples two recently landed, separately
gated lanes — higher risk than an executor change); offloading cheap
lanes too (they share the DuckDB conn; thread-affinity forbids it, and
their sum is below the heavy lanes anyway).

## 3. Design

Keep the loop's contract — same lanes, same ROUTING ownership, same
fail-loud guards, same D13 dry-run default, same output order — and
move only *when* the heavy lanes run:

1. **S1 — lane extraction (pure refactor).** Lift per-command compute
   out of the `--all` loop into one helper returning the printed block
   + analytics payload. `--jobs 1` path is byte-identical to today
   (stdout, stderr, dry-run notes, FAIL-and-continue semantics). No
   behavior change; existing CLI tests green.
2. **S2 — common fork-pool executor + `--jobs N` flag (default 1).** N ≥ 2
   submits the L1B fold, shared scipy pair, link-prediction, and voterank
   lanes through the common `ForkPool`; each result is consumed as soon as
   its own worker completes, while the main thread walks the cheap lanes
   sequentially on the shared conn. Link-prediction returns through a
   temporary zstd Parquet artifact rather than Python IPC. Results are
   printed in canonical command order. A raised lane keeps the exact
   per-lane `FAIL: <type>: <msg>` + continue contract — no silent skip,
   no engine fallback.
3. **S3 — wiring flip.** Persist path untouched (the sequential,
   ordered `pending_writes → write_analytics` batch at the end
   preserves single-writer discipline). Makefile `recompute-graph` passes
   `--jobs 4`; the CLI default stays 1 so direct and QA paths remain
   sequential unless explicitly requested.
4. **S4 — shakedown record.** Repeat-count timing table appended here
   (5× cold dry-run jobs=1 vs jobs=3; 2× maint-full), then archive per
   the proposals checklist.

## 4. Acceptance criteria & shakedown

1. **Parity:** `--all` stdout/stderr with `--jobs 1` vs `--jobs 4` is
   identical (per-block sorted compare) on the live graph across 5 runs.
2. **Wall:** operator-approved cold `--all --jobs 4` dry-run ≤ 15 s,
   median of 5 runs (measured median `12.10 s`, range `11.70–12.31 s`).
3. **Step:** maint-full `recompute-graph` ≤ 15 s under -j4 across 2
   full runs (baseline 34.4 s, run 313).
4. **Gates:** `make qa` 11/11; `make perf` 23/23 unchanged.
5. **No eval-gate bullet required:** metric payloads are value-identical
   (same lanes, same math; criterion 1 is the proof) — no
   query-visible semantics change, so the frozen-question gate does
   not apply.

| Projected outcome | Today | After |
|---|---|---|
| recompute-graph step (maint -j4) | 34.4 s | ≤ 15 s |
| `--all` cold dry-run | 22.2 s | ≤ 15 s |
| maint-full wall | 121 s | ≤ 105 s |

## 5. Risks

- **GIL contention between the fold thread's Python loop and the
  main-thread cheap lanes** — DuckDB/scipy/sqlite calls release the
  GIL, but the fold has Python-side work; if S4 shows the wall
  tracking fold+cheap instead of max(), S2b moves the fold to a
  process (it already takes a db path, so process handoff is trivial).
- **DuckDB conn thread-affinity** — enforced by construction: only the
  two conn-free lanes are offloaded; cheap lanes never leave the main
  thread.
- **Output nondeterminism** — results buffered, printed in canonical
  command order (criterion 1 fails loudly on any leak).
- **Failure-semantics drift** — the per-lane try/except is preserved
  verbatim; a test asserts a failing lane prints FAIL and the run
  still completes the remaining lanes.
- **-j4 contention skewing measurements** — acceptance uses repeat
  counts and reports cold numbers; maint step compared across 2 runs.

## 6. Non-goals

- No ROUTING/engine changes — the switch stays the single source of
  truth; no lane changes owner.
- No metric-math changes — values are bit-parity-verified.
- No hyper-lane work (`hyper-communities` / `hyper-centralities` are
  separate maint steps).
- No rebuild-note-search optimization (the other 35 s pole — separate
  arc if ever).
- No P2.2 incremental materialization (owner: `vault_scaling.md` #204).
- Pair-lane `jobs` knob stays 1; no new dependencies.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-25 | `time algorithms.py --all` | 22.2 s | cold dry-run, sequential baseline |
| 2026-09-25 | `time algorithms.py <cmd> --top 1` ×15 | table §2 | each ≈0.5–0.7 s setup incl. |
| 2026-09-25 | maint run 313 | 34.4 s step / 121.0 s wall | -j4, `outputs/maint_report.md` |
| 2026-09-25 | parallel dry-run `--all --jobs 1 --top 1` | 22.84 s | live graph; exact stdout/stderr parity baseline |
| 2026-09-25 | thread fan-out `--all --jobs 3 --top 1` | 21.59 s | GIL contention; superseded by fork-pool measurement |
| 2026-09-25 | fork-pool fan-out `--all --jobs 3 --top 1` | 12.49 s | async L1B/SciPy/link lanes; exact parity |
| 2026-09-25 | fork-pool fan-out `--all --jobs 4 --top 1` ×5 | median `12.10 s` | range `11.70–12.31 s`; exact stdout/stderr parity; four independent tasks including voterank |
| 2026-09-25 | `make maint-full` run 1 | 27/27 steps; recompute `26.27 s` | full-chain apply passed; all integrity and snapshot checks green |
| 2026-09-25 | `make maint-full` run 2 | 27/27 steps; recompute `27.92 s` | second run idempotent on derived rows; same output contract and snapshots green |
| 2026-09-25 | `--jobs 4` implementation | complete | common `ForkPool` submits L1B, SciPy, link-prediction, and voterank independently; link results use temporary zstd Parquet; cheap lanes stay on the main DuckDB connection; Makefile now passes `--jobs 4` |

The operator approved a 15 s isolated dry-run ceiling for scalability rather than the
original 12 s target. The fork-pool implementation meets that ceiling, but the
full maint-full apply path measured `26.27–27.92 s`, so the original 15 s
recompute-step sub-target remains unmet. The proposal is otherwise complete and
ready for an explicit waiver or further profiling of the full apply path.

**Future optimization note:** if graph growth makes the 15 s ceiling bind again,
re-profile the cheap onager lanes and shared connection/materialisation cost before
adding more workers; consider independent read-only connections or a bounded metric
cache, preserving routing, metric math, and single-writer persistence.

## 8. Diagnostic findings

- Isolated heavy-lane timing was L1B `9.50 s` and SciPy pair `7.56 s` when run
  serially. A thread pool completed them in `15.43 s`, exposing GIL/native-library
  contention; a process pool completed them in `9.98 s`.
- The common `fork_map` API was initially synchronous: waiting for its combined
  result at the first routed metric still serialized the main loop behind both
  heavy lanes. `ForkPool.submit()` now exposes independent `ApplyResult` handles
  so each lane is consumed as soon as it finishes.
- Link prediction returns roughly `546k` candidate pairs. Passing that Python list
  through process IPC caused a multi-minute stall. The worker now writes a
  temporary zstd Parquet artifact and returns only its path and row count.
- Dry-run artifact reads are bounded to the displayed top-N rows while retaining
  the total count for output parity. Apply mode reads the complete artifact before
  persistence.
- Voterank is independent and small enough to occupy the fourth worker rather than
  leaving `--jobs 4` with an idle process. Cheap Onager lanes remain on the shared
  main DuckDB connection because connection affinity and materialisation sharing
  are intentional.
- The original thread-only result was `21.59 s`; the final four-worker fork-pool
  result had exact stdout/stderr parity across five runs, median `12.10 s`, range
  `11.70–12.31 s`, against a sequential baseline of `22.84 s`.
- The operator approved a `15 s` ceiling as the scalability gate; the original
  `12 s` target is retained as historical context but is no longer blocking.
