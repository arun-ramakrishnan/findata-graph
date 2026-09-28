---
title: "CSR substrate + Mojo BFS — T1 unlocked early (build-early aim)"
status: executed
filed: "2026-09-26"
executed: "2026-09-26"
completed_md: "301"
area: "helpers/graph/csr.py, helpers/graph/query.py, Mojo/src/bench/bfs_csr.mojo, tests/test_csr.py"
---

# CSR substrate + Mojo BFS — T1 unlocked early (build-early aim)

**Date:** 2026-09-26 · **Status:** EXECUTED (operator-directed unlock of
vault_scaling T1 Phase B-A/B — build-early aim was recorded in the
original ladder) ·
**Area:** `helpers/graph/csr.py` (new), `helpers/graph/query.py`
(shortest_path CSR lane), `Mojo/src/bench/bfs_csr.mojo` (new),
`tests/test_csr.py`.

## 1. Motivation

Operator unlock of the T1 ladder ahead of the ~1M doubled-row trigger
(live: 115,264 directed rows — the ladder's own build-early aim:
"Phase 0 = build substrate while DuckDB serves"). Same day, the
near-dup arc landed separately (numpy GEMM join, path-axis ceiling).

## 2. Evidence (measured 2026-09-26, this box)

| Query (live CSR, 22,054 nodes / 115,264 directed rows) | Python-CSR | Mojo binary | DuckDB |
|---|---|---|---|
| 1-hop (Maruti pair) | **0.08 ms** | 10.5 ms (startup) | 12-76 ms |
| 4-5-hop (3 pairs) | 32-39 ms | **10-15 ms** | (leg class) |
| CSR build | — | — | — 0.17 s, byte-identical rebuilds |
| Stamp-lane wall (S6 all-universe) | — | — | 27.5 s (S6) |

Parity: 6/6 CSR tests + Mojo-vs-oracle parity on toy + 25 seeded random
probes (exact path sequence, unreachability, max_hops clipping).

### 2.1 Correction (2026-09-28) — the "0.08 ms vs 12-76 ms" figure was the KERNEL, not the lane

An OCR delegation review of the landing commit `ef8a17d4` found the
promotion numbers above were the raw `bfs_path` kernel with the CSR
already resident, never measured end-to-end through
`query.shortest_path`. Two defects made the difference, both now fixed
(`doc/improvements/archive/graph/csr_lane_remediation.md`):

1. **The lane never fired in production.** The CSR gate requires
   `edge_label is None`, but the parameter default was the *recognised*
   label `"BelongsTo"` — a filter — and neither the API route nor the CLI
   passed `edge_label`. The advertised fast path was dead code behind a
   live-looking default. It was not only a perf loss: the default returned
   `None` for pairs that are genuinely connected (Maruti Suzuki → Tata
   Motors is 3 hops, served as `path: null`).
2. **Per-call setup erased most of the win.** `csr.load()` re-parsed
   `csr_names.json` and rebuilt the 22,054-entry name→id map on *every*
   call (~7.2 ms measured), so the integrated path measured 9.15 ms vs
   11.6 ms SQL — **1.27×**, not 50-300×.

Measured 2026-09-28 after the fix (live graph, best-of-15 / median-of-15,
same 22,054-node substrate):

| Pair (hops) | Kernel | **Integrated** `shortest_path` | SQL (BelongsTo) | Speedup |
|---|---|---|---|---|
| CEAT → MRF (1) | 0.038 / 0.039 ms | **0.41 / 0.45 ms** | 17.50 / 19.00 ms | **42.6×** |
| Rallis India → Tata Chemicals (1) | 0.044 / 0.044 ms | **0.41 / 0.43 ms** | 18.28 / 19.13 ms | **44.4×** |
| Tata Motors → Maruti Suzuki (2) | 1.272 / 1.324 ms | **1.69 / 1.85 ms** | 8.73 / 9.12 ms | **4.9×** |
| Maruti Suzuki → Tata Motors (2) | 3.077 / 3.207 ms | **3.60 / 3.84 ms** | 20.50 / 21.80 ms | **5.7×** |

Cold (cache empty, one-shot after restart): 8.83 ms min / 9.62 ms median.

Read the two regimes separately — there is no single honest ratio:

- **1-hop ≈ 42-44×.** Per-call overhead is a fixed ~0.37 ms (the two
  DuckDB probes plus the cache/`pos` lookup), which barely registers
  against a 0.04 ms kernel. This is the class the original 50-300× claim
  was measured on; the integrated truth is ~42×, so the record overstated
  by roughly the per-call setup.
- **2-hop ≈ 5×.** The Python `bfs_path` is the *parity oracle*, not an
  optimised kernel, so its own 1.3-3.2 ms dominates and the setup is
  proportionally large. This is where the review's 1.27× was measured;
  after the fix it is ~4-6×. The 2-hop ceiling is the reference kernel,
  **not** the CSR substrate — the Mojo lane is the answer for long hops,
  which leaves the §3 verdict (default serving is Python-CSR) unchanged.

## 3. Design

- **B-A substrate** (`helpers/graph/csr.py`): frozen binary layout
  (`csr_offsets.bin` N+1 int32 LE, `csr_neighbors.bin` M int32 LE, both
  directions, sorted slices), sorted-name deterministic ids, sha256
  manifest carrying `db_meta.generation`, byte-identical rebuilds,
  mmap loader with generation-gate freshness — drift → DuckDB fallback
  (never silent), structure-only (labels/as-of stay on SQL until a
  filtered workload proves hot).
- **B-B Mojo BFS** (`bfs_csr.mojo`): files-in/paths-out binary, exit
  0/2/1; Beamer direction switch (bottom-up past N/20 frontier
  density); BOTH modes two-phase MIN-claimant so the parent tree — and
  the path — is identical to the Python oracle across modes
  (first-claim order retracted, per the recorded B-B rule; the parity
  gate caught the divergence empirically before the doctrine did).
- **Promotion (query-path)**: `query.shortest_path` routes UNFILTERED
  queries (edge_label=None, as_of=None) through the CSR lane when the
  connection's `_build_meta.generation` matches the manifest — tmp
  fixtures and cross-store calls fail the match and fall back to SQL
  (no cross-db contamination possible). Filtered queries stay SQL
  (structure-only lane). Mojo stays a CLI/batch lane: Python wins
  short-hop (startup tax), Mojo wins long-hop 2.7-3×. **As filed this
  promotion was inert** — the callers never passed `edge_label=None` — and
  its headline speedup was kernel-only; both corrected in §2.1 (integrated
  42-44× at 1 hop, ~5× at 2 hops, ~2× cold). **Operator
  verdict (2026-09-26, confirming the measured split): Mojo also brings
  its own setup complexity — toolchain pinning, 1.0→1.1 def-only
  migration, -I import roots, a mandatory parity harness — so a >2×
  crossover alone does not justify default promotion; the default
  serving is Python-CSR, and Mojo promotes only when a sustained
  long-hop/batch workload amortizes that complexity.**

## 4. Acceptance criteria & shakedown

1. Goldens: hand-computed offsets/neighbors on the toy graph ✓
2. Byte-identical rebuild ✓; generation-drift flips `fresh` ✓; missing
   artifacts degrade open ✓
3. Random-graph BFS parity vs brute-force distances (60n/180e) ✓
4. Mojo parity: toy + 25 seeded random probes, exact path sequences ✓
5. Live: build 0.17 s; crossover table above ✓

## 5. Risks

- **Int32 ceiling**: N, M < 2³¹ — the recorded growth path past that is
  shard-by-node-range, never a silent widen.
- **Structure-only lane**: a filtered shortest_path silently using CSR
  would be wrong — the gate requires edge_label=None AND as_of=None.
- **Inert-gate hazard (added 2026-09-28)**: the gate depends on its
  *callers* passing `edge_label=None`, and the parameter default was a
  filter, so the lane shipped dead while every test that called it with
  an explicit `edge_label=None` stayed green. The parity gate cannot see
  this — it tests the lane's paths, not whether anything reaches it. Any
  future lane gated on caller shape needs a test that pins the
  *production* call shape, and the `edge_label` default should stay
  `None`-safe rather than a recognised label.
- **Python kernel is superlinear in the frontier (added 2026-09-28)**:
  the Python `bfs_path` scans the whole discovered frontier per level
  with dict bookkeeping, so hop count drives cost hard — measured
  0.038 ms at 1 hop, 1.27 ms at 2 hops, 3.08 ms at 2 hops on a denser
  pair, against a 0.37 ms fixed overhead. The 2-hop ceiling is this
  reference kernel, not the int32 substrate; Mojo (2.7-3×) is the
  recorded answer for long hops.
- **Mojo toolchain drift**: bfs_csr written for Mojo 1.1 (def-only,
  typed read); the 1.0-era house notes in cosine.mojo still apply
  (read_bytes clobbers — never use).

## 6. Non-goals

Weights/labels/as-of on CSR · batched multi-source BFS · GPU BFS (pilot
crossover stands) · CSR for other traversals (named future caller) ·
incremental updates (full build is 0.17 s).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 09-26 | build live CSR | 0.17 s, 22,054n / 115,264 rows, checksum OK | |
| 09-26 | python BFS 1-hop | 0.08 ms | startup-free |
| 09-26 | mojo BFS 1-hop | 10.5 ms | process startup dominates |
| 09-26 | mojo BFS 4-5-hop ×3 | 9.9-14.8 ms | 2.7-3× vs python |
| 09-26 | python BFS 4-5-hop ×3 | 31.9-39.1 ms | |
| 09-26 | parity probes | toy + 25 random: exact | post-MIN-claimant fix |
| 09-28 | lane active? | **NO — dead** | gate needs `edge_label=None`, default was `"BelongsTo"`; see §2.1 |
| 09-28 | integrated, pre-fix | 9.15 ms vs 11.6 ms SQL (1.27×) | per-call `load()` + 22k `pos` rebuild ~7.2 ms |
| 09-28 | integrated 1-hop, post-fix | 0.41 / 0.45 ms vs 17.50 / 19.00 ms (42.6×) | best-of-15/median-of-15 |
| 09-28 | integrated 2-hop, post-fix | 1.69 / 3.84 ms vs 8.73 / 21.80 ms (4.9-5.7×) | kernel-bound, Python parity oracle |
| 09-28 | integrated, cold | 8.83 / 9.62 ms | one-shot, cache empty |
| 09-28 | default-arg API call | `None` for a 3-hop-connected pair | user-facing bug, fixed by S1 |
