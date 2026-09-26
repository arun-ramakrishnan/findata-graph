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
  short-hop (startup tax), Mojo wins long-hop 2.7-3×. **Operator
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
