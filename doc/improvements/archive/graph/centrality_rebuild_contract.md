---
title: "Centrality rebuild contract — stamp on demand, keep the rebuild data-only"
status: executed
filed: "2026-09-22"
executed: "2026-09-23"
completed_md: "271"
area: "helpers/graph"
---

# Centrality rebuild contract — stamp on demand, keep the rebuild data-only

## 1. TL;DR

At the 3x-grown graph (26,120 nodes, 56,014 edges) a full
`query.rebuild()` takes **369.9 s** — and 99.5% of that is
`_materialise_centrality_cache` (measured stage profile:
vertices 0.5 s + edges 0.8 s + hyper 0.0 s + note-embeddings 0.5 s =
**1.8 s of data**, centrality stamping 371.2 s). Per-metric:
harmonic 192.1 s, closeness 183.4 s, betweenness 87.7 s — the BFS
family alone. Every SQLite-side write bumps the generation, so every
next `connect()` pays the stamp again under the build flock; parallel
gate steps queue behind it (evidence: 2026-09-22 session, collected in
`doc/local/perf/graph_scaling.md` Part 1 §A-§C).

This proposal splits the contract: **rebuild = data only** (~2 s,
back under the 5 s vault-scaling budget); the centrality stamp becomes
an explicit, on-demand lane.

## 2. Contract change

- `query.rebuild()` / `fresh_rebuild()` / implicit stale-cache connects
  materialise the data tables and **DROP** `v_centrality_*` (a stamp
  belongs to exactly one edge set; serving the old generation's scores
  over new edges is wrong). Absence already means "compute on demand"
  (the best-effort-drop semantics from
  graph_centrality_persistent_cache #259) — readers stay correct, the
  first read just pays live compute.
- New public `query.stamp_centrality_cache()` + CLI
  `python3 helpers/graph/query.py stamp-centrality` + Makefile target
  `stamp-centrality`: re-stamp the ten metrics on the WARM cache
  (explicitly not part of rebuild).
- `rebuild(stamp_centrality=True)` keeps the old one-shot behaviour for
  callers that want it (maintenance lanes).
- `make maint` gains the stamp after `graph-rebuild` (weekly home,
  never the interactive path).

## 3. Why not optimize the metrics instead

The company-projection / leaf-folded variant of the BFS family is the
real long-term fix (the stamp persists 1,734 company rows but computes
all-pairs over 26,120 nodes incl. ~19k degree-1 VIGIL leaves) — but it
CHANGES SCORES (a folded leaf is not a node), needs its own evaluation,
and does nothing for the write-then-read thrash (any data write would
still re-pay whatever the stamp costs). Splitting the contract is
correct at any stamp cost, is behaviour-neutral for scores, and
unblocks the operator workflow today. The projection work stays
scoped as a follow-up.

## 4. Acceptance

- `make graph-rebuild` completes < 5 s on the live graph (was 369.9 s).
- After a data-only rebuild: `v_centrality_*` absent; a centrality read
  returns scores equal to fresh compute (fallback path), not the old
  stamp.
- `python3 helpers/graph/query.py stamp-centrality` re-creates the
  tables; reads serve them warm.
- Updated `tests/test_centrality_cache.py` contracts (b) and the
  fixture stamp path; targeted tests green; `make static-checks` clean.
- `doc/local/perf/graph_scaling.md` Part 1 §B verdict updated; graph_design
  persistence section updated.

## 6. Execution addendum (2026-09-22)

EXECUTED same day, in patch `centrality_bld` (status stays `proposed`
per the lifecycle convention until archival).

- `_build_graph(con, *, stamp_centrality=False)`: data stages unchanged;
  the ten `v_centrality_*` tables are dropped (`_CENTRALITY_TABLES`),
  stamped only under `stamp_centrality=True`.
- Threaded through `connect(stamp_centrality=...)`, `rebuild()`,
  `fresh_rebuild()`, `_rebuild_via_swap()` (keyword-only, default
  False — every existing caller keeps working, now data-only).
- Public `stamp_centrality_cache(db_path, duckdb_path)`: warm-stamp lane
  (cold cache builds data-only first, then stamps); RW connect so the
  existing flock serializes it cross-process.
- CLI: `query.py stamp-centrality`; `rebuild --stamp-centrality` /
  `fresh --stamp-centrality` flags for the one-shot.
- Makefile: `stamp-centrality` target (help alphabetical), wired into
  `helpers/maintenance/maint.py` as step 3b (after graph-rebuild, before
  the parquet-mirror re-export).
- Tests: `test_centrality_cache.py` re-pinned — (b) generation bump now
  asserts the DROP + fallback-to-fresh-compute; new (b2) one-shot
  rebuild still stamps; new stamp-lane recreates all ten tables;
  fixture stamps explicitly. `test_graph_disk.py` `_build_graph` spies
  made kwarg-tolerant. Perf benchmark comment updated (rebuild back to
  ~3s; 8.0s budget kept as ceiling).
- Measured: live rebuild **369.9s → 3.19s** (data-only), acceptance
  `< 5s` met. First full stamp-lane run at scale: **6m3.6s**, and it
  exposed a contract hole — a concurrent data-only rebuild swapped the
  cache file mid-stamp (the flock serialises the open, not the
  minutes-long compute), the stamp wrote to the orphaned inode, and its
  tables died silently. Fixed with an inode guard + one retry in
  `stamp_centrality_cache` (regression:
  `test_stamp_detects_concurrent_swap`) and re-run clean (see
  `doc/local/perf/graph_scaling.md` Part 1 §B/§C).
- Adjacent fix (pre-existing live-db failure exposed by the run):
  `/api/graph/stats` `orphan_companies` re-scoped to note-backed
  companies (fileless = by-design intake classes: D19 listings + ~25K
  VIGIL counter-party companies); `test_api_graph_live.py` re-pinned.
  Help-ordering miss from the refresh-shp line also fixed.
