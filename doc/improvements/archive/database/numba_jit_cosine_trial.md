---
title: "Numba JIT cosine trial"
area: database
status: executed
filed: "2026-10-18"
completed_md: "443"
executed: "2026-10-26"
---

# Proposal: Numba JIT cosine trial — 2026-10-18

Status: **EXECUTED — measured 2026-10-18 (trial) + 2026-10-26 (Phase C); NO production migration** (completed.md #443).
Filed: 2026-10-18 (post-trial, user command m01854: "do the checks, run trials and write proposals if need be").

## Operator-ordered context
Arc: HGX Residual #1 follow-on (relational-depth overhaul + repo-wide Mojo/Rust perf). R-lanes sequence: R6b → R3 → R2 → R5 → R4. Residual #1 EXECUTED (completed.md #438 + A′ #439; approach A selected at m00658; §8 verification blocker: `Multiple streaming scans + CTAS / insert` NotImplemented; TEMP build needs quack-down until approach B). Design-phase open questions carried; operator go pending.

## Trial objective
Confirm whether the numba `@njit` JIT lane delivers a production-usable cosine speedup over pure-Python `_cosine` (app.py `co_mention_top / cross_sector_bridges`), and whether the lane is alive post-correction (user corrected m01390: numba supports 3.14/3.15).

## Trial design (measured-then-claim, per ladder doctrine)
- File: `/mnt/data/tmp/opencode/trial_numba_cosine.py`
- Inputs: 17,497 `note_search` embeddings from `memory/research.db` (512-d f32 BLOB, unpacked via `helpers.core.vec_codec.load_vec`).
- Functions:
  - `_cosine_py`: pure-Python accumulators (`math.sqrt` over `sum(x*x)` and `sum(x*y)`).
  - `_cosine_numba`: `@njit` float64 loop (512 dims, same arithmetic).
- Protocol: best-of-15 repetitions (`time.perf_counter`); per-query over 64 rows; single-cosine.
- Correctness: spot-check absolute delta vs pure-Python < 1e-12.

## Measurement result (verbatim run output)

```text
loaded 17497 vecs
per-query cosine over 64 rows:
  pure-python : 3.530 ms
  numba @njit : 1.941 ms
  speedup      : 1.82x
single cosine (vs doc findata/Companies/Agriculture/EID_Parry....): py 54.2 us, numba 15.4 us
```

Implications: 1.82× per-query (3.53→1.94 ms); 3.52× single-cosine (54.2→15.4 μs). JIT compiles successfully on `.venv` py3.14.0 (`numba` 0.68.0 + `llvmlite` 0.50.0 installed via `uv pip install --python .venv/bin/python3 numba`); no `numba` or `llvmlite` present before trial.

## Cross-references (settled)
- `perf_skills.md` sec8 triage: numba row `HIGH — next`, reason "blocked on py 3.14.4 — numba 0.65.x supports 3.9-3.12": **STALE**. Corrected reason: `numba` 0.63.0 / `llvmlite` 0.46 (per `graph_scaling.md` sec7) supports Python 3.14; trial used 0.68.0; 3.14/3.15 supported per user correction (m01390).
- `graph_scaling.md` sec7 (`numba CLOSED SUPERSEDED`, 2026-09-04): holds — `#194` `wedged_flat_knn_map` (app.py:475-495, `numpy` native vec0 k=10 1.86ms) closes the cosine loop as tier-3 last resort (between native `vec0` layer and 59ms pure-Python loop). Trial confirms JIT improvement but does NOT unlock a new production tier past native `vec0`.
- `numkong-7-6.0` (`MEDIUM`, `numba alt`, 2000+ kernels): same cosine gap; same verdict — no new lane unlocked.
- `pending.md` / `graph_scaling.md` sec5 (`Cosine/KNN standing NO runtime migration`; `mojo_pilot.md`:242-254): holds.
- `db_estate_analysis.md` sec4/§6: no secondary index change; no engine switch; v2.0 consolidation gate not fired; two actionable-now remain (CSR rebuild 0.23s; convo_search.duckdb reclaim 431MB via swap-rewrite; VACUUM cannot).
- `vault_scaling.md` Phase C (ON HOLD to T2/T3): f16/int8 quant, usearch/HNSW past ~1M, parallel-accumulator on `row_cosine`; trigger = matrix size not row tiers.

## Decision
- Lane **NOT dead** (user correction confirmed; trial confirms 1.82× on 3.14.0).
- Lane **NOT promoted to production** (native `vec0` layer already closes loop per #194; ladder doctrine + measured-first: no language/engine move without a measured wall past the existing native layer).
- Lane kept as **future-candidate only**: if production cosine wall reappears past Residual #1 approach-A fix + native `vec0` layer, revisit with full production benchmark (not 64-row micro-trial).
- Proposal does **NOT** trigger a `stg new` / commit / patch — no edits to production source; documentation-only.

## Action items
- [x] Measurement executed (`trial_numba_cosine.py`, best-of-15).
- [x] Cross-docs verified (`graph_scaling.md`, `perf_skills.md`, `pending.md`, `db_estate_analysis.md`, `vault_scaling.md`).
- [x] Stale row in `perf_skills.md` sec8 noted (`blocked on py 3.14.4` stale; to be corrected by operator or follow-up edit — out of scope of this measurement proposal).
- [x] Proposal file filed (`doc/improvements/proposals/numba_jit_cosine_trial.md` — this file).
- [ ] Production proposal: **NOT proposed** (native layer closes loop; no runtime migration; operator go pending for Residual #1 design-phase open questions, not for numba).

## Constraints honored
- `.venv/bin/python3` used (not bare `python3`); `.venv` is repo-local py3.14.0.
- Scratch at `$TMPDIR` (`/mnt/data/tmp/`) only.
- No `git commit` / `stg` mutation.
- Proposal only; no production edits to `app.py`, `helpers/graph/query.py`, or `graph.duckdb`.
- No markdownlint fix applied to `findata/**` (none involved).
- Open objectives preserved: R6b→R3→R2→R5→R4 operator-agreed sequence; Residual #1 follow-on (approach A fix / approach B adoption) operator go pending.

---

## Phase C execution (2026-10-26 — the trigger fired and was measured)

Operator ordered the Phase C candidates run ("run these preload trials and
also embed-matrix ~1M+, quant, HNSW now"). Full tables, regen recipes and
trap list: `bench_data/phasec/README.md` (4 cores, 512-d, 20 queries, k=10).

- **Crossover measured**: at 250k rows numba ties numpy (29.3 ms/q both);
  at 1M×512 numba f32 `prange` **wins 117.4 vs numpy's 227.8 ms/q (1.94×)**
  with recall@10 = 1.000 vs numpy truth even under `fastmath`. RSS 2.29 GB.
- The trigger therefore has a shape: **it only exists at ≥ ~500k rows** —
  the exact scan is a bandwidth-bound GEMV that OpenBLAS does not thread
  for single vectors, while numba `prange` row-splits it. No production
  matrix is near that wall (largest live corpus 17,497×512), so the
  no-migration decision stands; numba is the designated Phase C backend
  if the wall is ever reached. The production rust kernel scales
  single-threaded (817 ms/q at 1M) and would need a rayon rewrite to
  matter at that scale.
- Traps recorded: `@njit(cache=True)` fails from stdin heredocs ("no
  locator available for <stdin>") — file-based scripts only; JIT warm-up
  excluded from timings; numba 0.68.0 + llvmlite 0.50.0 import clean on
  py3.14.0 at 1M-row scale.
- Same run settled the neighbouring questions: quantization is
  storage-only (f16/i8 dequant casts cost 11–12× latency); HNSW is
  terminally closed at 512-d (recall@10 = 0.56 at ef=1024 already costs
  10.3 ms/q; M=32 build 414 s); tight-decay jemalloc is RSS-neutral on
  all short one-shot legs (residents-only lever, already wired).
