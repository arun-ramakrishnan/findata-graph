---
title: "Land the graph-rebuild scaling probe in-repo so the vault-scaling ladder stops citing a deleted script"
status: executed
filed: "2026-10-01"
executed: "2026-10-01"
completed_md: "325"
area: "helpers/bench/ + doc/improvements/archive/graph/vault_scaling.md (materialisation cost of helpers/graph/query.py:902-951)"
---

# Land the graph-rebuild scaling probe in-repo so the vault-scaling ladder stops citing a deleted script

**Date:** 2026-10-01 · **Status:** EXECUTED (filed, deferred, then executed
2026-10-01; completed.md #325) ·
**Area:** `helpers/bench/` (new bench), `helpers/graph/query.py` (measurement
target only — no code change), `doc/improvements/archive/graph/vault_scaling.md`
§2/§3.1, `doc/local/perf/graph_scaling.md` §3

## 0. Execution record (2026-10-01)

Executed the same day it was filed. S1–S3 landed; S4 landed as the §3.1
re-anchor plus the tier-label correction; S5 was descoped (see §7).

**Built:** `tests/bench_rebuild_scale.py` — a sibling to the existing
`tests/bench_scale_bfs.py`, not a replacement. It clones production's schema
with no rows, bulk-generates a synthetic source at a requested R, and times
the **shipping** path `query.connect(rebuild=True)` → `_build_graph`
(schema 17: 56 DROPs + ~50 CTAS over an attached SQLite). Conventions are the
sibling's: `_best_of` minimum-of-N, `tempfile.mkdtemp(prefix="scale_bfs_")`
so `tmp_sweep.py:63` already reaps it, `--rows/--keep`, deterministic
Knuth-multiplicative hashing with no RNG state, and deliberately **not** a
`make perf` leg. It asserts production `memory/graph.duckdb` and
`memory/research.db` are byte-unchanged (size + mtime) after every run.

**Measured (this box, best-of-3, R = `e_all_und` doubled rows):**

| R | entities | directed edges | rebuild (s) | duckdb |
|---|---|---|---|---|
| 0 (schema only) | 4 | 1 | **1.47** | 30.8 MB |
| 115,030 (live) | 22,036 | 57,515 | **2.01** | 30.8 MB |
| 1,000,000 (T1) | 191,570 | 500,000 | **3.99** | 35.8 MB |
| 10,000,000 (T2) | 1,915,708 | 5,000,000 | **28.08** | 209.8 MB |

**T(R) ≈ 1.47 s + 2.52 µs·R**; the 5 s line is crossed at **R ≈ 1.40 M** =
**12.2× live volume** — i.e. at T1, one tier above where the ladder puts it.

Fixed-cost breakdown at R=0: extension `INSTALL`+`LOAD` 0.236 s, `ATTACH`
0.003 s, 56 × `DROP TABLE IF EXISTS` 0.021 s, residual (planning + executing
the CTAS set) ≈ 1.21 s. So the intercept is still **~82% fixed statement
overhead** — the finding that holds P2.2 — and the per-row term is ~2.5 µs.

Three witnesses agree on what production pays today: the `make perf`
`graph_rebuild` leg measured **2.57 s**, the real-data clone probe **2.62 s**,
and this synthetic harness **2.01 s**. The first two agree within 2%; the
synthetic is ~23% lower because its source is leaner than a 307 MB copy of
production. Use the synthetic curve for ladder/budget decisions (reproducible
from a clean checkout) and the clone figure for current production cost.

Raw artefacts of the first-pass probe, and the harness's own three bug fixes,
are preserved under [`evidence/p22/`](evidence/p22/README.md).

## 1. Motivation

> **PREMISE CORRECTION (2026-10-01, at execution).** This proposal
> originally claimed the ladder's in-repo rewrite *"never happened"*. **That was
> wrong**: `tests/bench_scale_bfs.py` has been the in-repo ladder harness
> since `completed.md` #204 Phase 0 (2026-09-04), which records it as the
> "in-repo ladder rewrite after the /tmp wipe". The real gap is narrower and
> sharper, and it is what S1 below actually closed — see §1a.

`doc/improvements/archive/graph/vault_scaling.md:61-62` still cites
`/tmp/scale_bfs.py` and its scratch DBs as *"since deleted by /tmp cleanup"*,
which is stale — the rewrite exists. But the `materialize` column it records
is not a production rebuild measurement, for two independent reasons, both
verified 2026-10-01.

Trigger: the 2026-10-01 P2.2 re-decision. Re-measuring the rebuild to retire
the *"rebuild > 5 s"* trigger criterion produced a curve that both supersedes
the ladder and **repeats the ladder's own sin**: the new numbers came from an
ad-hoc harness under `$TMPDIR`, so `vault_scaling.md` §3.1 now carries an
explicit *"cited-but-unreproducible — the same failure mode it corrects"*
caveat. This arc closes that loop: the probe lands in-repo, the T1/T2 points
get measured rather than projected, and the ladder stops depending on
`/tmp`.

Pain it removes, concretely:

- nobody can re-derive what the rebuild costs at 1M or 10M doubled rows, which
  is exactly the decision the ladder is supposed to support;
- the T2 label is a tier off (the 5 s line is reached at ~907K rows = T1), and
  nothing in the repo notices;
- a future re-anchor repeats this arc's ~40 minutes of harness debugging.

## 1a. Why the ladder's `materialize` column is not a production number

The ladder's column and this harness disagree by 5.6–8.0×. Two independent
causes, both verified 2026-10-01 — not one, which is why the first draft of
this proposal mis-attributed the whole gap to a deleted script:

1. **Different function, different substrate.** `bench_scale_bfs.py` times
   `_materialise_walk_substrate` — the `e_dir` + `e_all_und` CTAS pair — on a
   purpose-built synthetic **DuckDB**: no SQLite ATTACH, no `v_node` and its
   per-row correlated `market_cap` subselect, no per-`edge_type` tables, no
   hypergraph, no note embeddings, no DROP churn. This harness times
   `_build_graph`, which is all of those. At the same R:
   1 M → 0.5 s (ladder) vs **3.99 s** (production) = **8.0×**;
   10 M → 5.0 s (ladder) vs **28.08 s** (production) = **5.6×**.
2. **Density.** `bench_scale_bfs.py --degree` defaults to **22** with help
   text *"prod ≈ 22.4"*, but the live corpus measures **2.61** directed
   rows/node (5.22 doubled; 57,515 edges over the 22,054 nodes that appear in
   an edge). The sibling therefore builds graphs ~8.4× denser than
   production, changing both the GEMM shape and the per-block mask density.
   `--density-check` in the new harness separates the two effects at a scale
   both can reach.

Consequence for the ladder: the T2 tier label *"~10M (rebuild > 5 s)"* is
wrong twice over — the 5 s comes from a function the ladder never claimed to
describe, and it lands a tier late. Both corrections are in
`vault_scaling.md` §2 and §3.1.

## 2. Evidence (measured 2026-10-01, this box)

Scratch-isolated: a non-`DB_PATH` `db_path` resolves to a sibling `.duckdb`
(`helpers/graph/query.py:954-958`), so production `memory/graph.duckdb` is
never opened for write. Source scaled by row duplication with remapped keys
(value columns such as `entity_tags.tag` left unsuffixed so distinct-tag
cardinality is preserved). R = `e_all_und` doubled rows.

| Configuration | Result | Verdict |
|---|---|---|
| ladder's recorded materialize @ 1M / 10M / 100M | 0.5 s / 5.0 s / 48.5 s | **superseded** — unreproducible, pre-retirement build, 6–10× low |
| R = 0 (schema only, no rows) | 2.28 s | baseline — the fixed-cost intercept |
| R = 115,030 (live) | 2.62 s | baseline — today's cost |
| R = 460,120 (3× duplication) | 3.72 s | candidate — slope |
| **fit** | **T(R) ≈ 2.28 s + 3.0 µs·R** | **adopt** (0/1×/3× consistent; zero-row point pins the slope) |
| projected R = 1M (T1) | ~5.3 s | projected — needs S2 |
| projected R ≈ 10M (T2) | ~32 s | projected — needs S2 |

Fixed-cost decomposition of the 2.28 s intercept (measured on the R=0 build):

| Component | Cost |
|---|---|
| extension `INSTALL`+`LOAD` (`sqlite`, `vss`) | 0.292 s |
| `ATTACH` the 307 MB sqlite as `fin` | ~0.000 s (lazy) |
| 56 × `DROP TABLE IF EXISTS` | 0.214 s |
| 12 empty CTAS DDL | 0.078 s |
| residual: plan + execute ~50 CTAS over the attached sqlite | 1.696 s |

What the numbers mean: at live scale the rebuild is **~87% fixed statement
overhead and ~13% per-row**, and the build is *linear with a large
intercept*, not sublinear-then-linear as the old ladder claimed. Two
consequences already recorded in `graph_pending.md` P2.2: the 5 s line is
crossed at ~907K rows (T1, not T2), and incremental refresh is the wrong
lever because row-level diffing would target the 13% term while adding
partial-application risk to the single file-level `_build_meta.generation`
stamp. Ruled out along the way: indexing `entity_tags` for `v_node`'s
per-row market-cap subselect — no super-linear term appeared over the
measured range, so it is a micro-optimisation, not a lever.

## 3. Design

**S1 — land a generating harness** at `helpers/bench/bench_graph_rebuild_scale.py`
(alongside `hyper_scale_bench.py`, the closest existing analogue). It must
**generate** a synthetic source (seeded, mirroring `hyper_scale_bench.py`'s
determinism rule) at a requested R rather than duplicating the real 307 MB
`research.db` — that is what makes T1/T2 reachable: duplication cost 1.68 GB
of SQLite at 10× and would need ~17 GB at 100×, whereas generation is bounded
by the target row count. Hard guards: refuse to run when the resolved
`db_path` is `helpers.graph.query.DB_PATH`, force every scratch artefact
under `$TMPDIR`, and assert after the run that `memory/graph.duckdb`'s mtime
and size are unchanged. Two scale modes — `--generate R` (default) and
`--from-real K` (k-fold duplication, the 2026-10-01 method) — with a
cross-check that both agree on slope at a scale both can reach. Per-file ruff
exemption registered in `pyproject.toml` only if the seeded generator needs
`S608`-class relief, matching the `helpers/bench/hyper_*_bench.py` entries.
Reachable from `make` (a `make graph-rebuild-bench` target, or a leg inside
`make perf` — operator's pick at execution).

**S2 — measure T1 and T2 for real.** Run the S1 harness at R = 1M and
R ≈ 10M, ≥3 repetitions each, reporting the **minimum** (never a single run
— the house rule for timing-shaped numbers). Replace the "projected" rows in
`vault_scaling.md` §3.1 with measured values and delete the
cited-but-unreproducible caveat. This is the slice that makes the doc
self-supporting.

**S3 — fold the breakdown into the harness** (`--breakdown`): emit the
extension / DROP / DDL / residual split alongside the total, so §3.1's
fixed-cost table is regenerated rather than restated by hand.

**S4 — propagate.** Correct the T2 tier label in `vault_scaling.md` §2 and in
`doc/local/perf/graph_scaling.md` §3 (the latter untracked — local reference
only, the tracked ladder is authoritative). Record the T0 `Current` figure
from the same run so the two tables cannot drift. `make search-fresh
APPLY=1` then `make search-fresh` (rc=0) per the archival checklist.

**S5 — close the rot class.** A cheap guard so this cannot recur: a static
check (or a `make perf` leg) that fails when a recorded materialize number in
`vault_scaling.md` §3.1 disagrees with a fresh harness run beyond a stated
tolerance. Keep it advisory-only in the first landing — a gating check on doc
prose would make the corpus hostage to bench noise.

Ordering: S1 unblocks S2; S2 unblocks S4 (the doc must carry measured numbers
before the labels move); S3 is independent of S2 but should land with it;
S5 only after S2 has produced a stable baseline to compare against.

## 4. Acceptance criteria & shakedown

1. `python3 helpers/bench/bench_graph_rebuild_scale.py --generate 1000000`
   prints a materialize wall time and a fitted `T(R) = a + b·R`, and exits
   non-zero if the production-untouched assertion fails.
2. `vault_scaling.md` §3.1 carries measured (not projected) T1 and T2 values,
   each from ≥3 runs with the min reported, plus the run command and date.
3. `make qa` legs that must stay green: `lint`, `md-lint`, `types`,
   `static_checks`, `pytest`. The known-flaky
   `tests/test_fuzz_regex.py::test_bold_line_regex_scales_subquadratically`
   (gate 1137; load-sensitive, passes idle — separately owned, must not be
   attributed to this arc) is not a blocker for S1–S4 but must not be
   *newly* destabilised.
4. `make search-fresh` rc=0 after the doc edits.
5. A second operator can reproduce §3.1's table from a clean checkout with
   one command and no `/tmp` residue — verified by handing the command to a
   fresh shell.

Eval gate: **not applicable.** This arc adds a measurement harness and
corrects documentation; it alters no query-visible semantics (no rosters,
crosswalks, hierarchies or extractor rules), so
`helpers/misc/ontology_eval_gate.py` is not required.

| Projected outcome | Today | After |
|---|---|---|
| rebuild cost at T1 (R=1M) | ~5.3 s (projected) | measured, reproducible |
| rebuild cost at T2 (R≈10M) | ~32 s (projected) | measured, reproducible |
| ladder's `materialize` provenance | deleted `/tmp` script | in-repo harness, `helpers/bench/` |
| re-anchor effort for the next session | rebuild the harness from scratch | one command |

## 5. Risks

- **A synthetic source is not the real distribution** — the slope could
  differ from duplication's. Mitigation: keep `--from-real` as a cross-check
  at a scale both modes reach, and note that the *intercept* (fixed statement
  overhead) is distribution-independent while only the slope is at risk.
- **The generator's own per-row cost contaminates the measurement** —
  building a 10M-row source can dominate wall time and leak into the timing.
  Mitigation: time only the `query.connect(..., rebuild=True)` call, never the
  source build; report the two separately.
- **Disk blowout at T2** — a 10M-row synthetic source is not small.
  Mitigation: build under `$TMPDIR`, delete after each point, and cap with an
  explicit `--max-gb` guard that fails loudly rather than filling the volume.
- **Another unreproducible number if the harness rots** — the failure this
  arc exists to kill, re-entering through the new file. Mitigation: S5.
- **Scope creep into the actual optimisation** — implementing (ii)/(iii)/C
  here would make this arc a code change wearing a measurement's clothes.
  Mitigation: §6.

## 6. Non-goals

- **Not** implementing P2.2 incremental refresh — held on measurement in
  `archive/graph/graph_pending.md`.
- **Not** implementing the three cheaper wins that hold P2.2: the
  `edge_type`-discriminated single-scan fold (ii), table-level dirty tracking
  (iii), or calling `rebuild()` from the ingest lanes (C). Each is a separate
  proposal; this arc only establishes that they, not P2.2, are the lever.
- **Not** re-measuring the query-side columns of the ladder (`1 expand*`,
  full 5-level BFS, the ART-index row). They measure query time, not
  materialisation, and nothing in the 2026-10-01 work touched them.
- **Not** moving the tier ladder's thresholds. This arc re-anchors the
  *evidence*; changing T1/T2 row counts is an operator decision.
- **Not** touching `doc/local/` beyond the one table in S4 — it is
  untracked (`doc/local/` has 0 tracked files) and is a local mirror only.

## 7. Residual after execution (re-open triggers)

S1–S4 executed 2026-10-01 (§0). Two items from the original S1–S5 did not
land, and the deferral that used to govern this whole file is retired.

**Descoped, with reasons:**

- **S5 (the anti-rot guard)** — a static check or `make perf` leg failing when
  `vault_scaling.md` §3.1's recorded numbers disagree with a fresh harness run
  beyond a tolerance. Descoped because §3.1 is now reproducible by one
  command, so the rot class it guarded against is gone; a doc-prose gate would
  make the corpus hostage to bench noise. If a future session finds §3.1
  drifting from the harness, this is the slice to re-add.
- **The `--from-real` cross-check mode** from S1. The synthetic harness's
  validation is the three-witness agreement recorded in §0 (`make perf`
  2.57 s / clone 2.62 s / synthetic 2.01 s at the same scale), which is
  stronger evidence than a single cross-check mode, so the extra code path was
  not written. `--density-check` covers the density question.

**What §3.1 now claims, and the standing caveat.** `vault_scaling.md` §3.1
carries the measured T1/T2 values and the fixed-cost breakdown, and its
provenance is `tests/bench_rebuild_scale.py` — reproducible from a clean
checkout, so the *"cited-but-unreproducible — the same failure mode it
corrects"* caveat is **retired**. The residual caveat is narrower and stated
there: the synthetic intercept (1.47 s) is lower than the real-source clone
intercept (2.28 s) because the generated source is leaner, so §3.1 is the
ladder/budget curve while the clone probe is production's current cost.

**Re-open this file when:**

1. `e_all_und` passes ~700K, i.e. the graph reaches T1 and the ladder's tier
   assignment must be re-decided against a measured point rather than a
   projection;
2. the materialisation changes shape — a new table in `_EXTRA_MATERIALIZED`,
   a schema bump past 17, or the (ii) statement-count fold / (iii) table-level
   dirty tracking successors of P2.2 landing, any of which invalidates the
   fitted intercept;
3. `--degree` in the sibling is reconciled with the measured 2.61, which would
   retire reason 2 in §1a and leave only the function difference.

**Do not** re-open on the strength of the old 5 s ladder line alone: §2 of
`vault_scaling.md` answers the over-budget band with a recorded waiver plus the
ART bridge, and that remains the designed response.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-10-01 | `scale_probe.py 1` | R=115,030, 2.62 s, 39.5 MB | live scale via duplication; first harness landed after a column-count bug in the `graph_edges` insert |
| 2026-10-01 | `scale_probe.py 3` | R=460,120, 3.72 s, 55.8 MB | 4× rows for +42% time — the sublinear-looking shape that the R=0 point then explained |
| 2026-10-01 | R=0 build (delete all input rows, rebuild) | 2.28 s | **the intercept**; without it the 1×/3× pair alone would have fit a line through the origin and implied a 9.5×/12.8× cost at T1/T2 |
| 2026-10-01 | fixed-cost breakdown on the R=0 build | 0.292 / ~0 / 0.214 / 0.078 / 1.696 s | ext INSTALL+LOAD / ATTACH / 56 DROPs / 12 empty CTAS / residual CTAS execution |
| 2026-10-01 | `scale_probe.py 10` | **abandoned** | clone, not build, was the bottleneck: 307 MB → 1.68 GB at 10×; 100× would be ~17 GB. Motivation for S1's generating harness |
| 2026-10-01 | duckdb 1.5.6 lock matrix, `witr -f` on the cache | RW holder excludes every opener incl. RO | confirms a stale-cache `read_only=True` caller cannot take the RO fast path (the C rationale in P2.2) |

Reproduction caveat, stated plainly: the runs above used an ad-hoc harness
under `$TMPDIR` (`scale_probe.py`, since cleaned up). They are the evidence
for S1–S4 and are recorded in `vault_scaling.md` §3.1, but they are **not yet
reproducible from a clean checkout** — that is the defect this proposal
exists to remove.
