# Graph Pending Items — Deferred (P2.2, P3.2, P3.3)

Generated 2026-08-08 after P3.1, P3.4, P3.6 landing.
P0/P1/P2.1/P2.3/P2.4/P2.5/P3.1/P3.4/P3.6 are DONE (see git log).
This file tracks intentional deferrals — not TODO debt.

## P2.2 — Incremental DuckDB materialization (deferred)
- Current: full DROP/CREATE of every materialised table on any generation bump
  (~2-3 s data-only at live scale, 2.9ms cached hit).
- Trigger: when graph_edges >10k or derive-relations ingest >500 edges/run.
- Plan: incremental_refresh() — LEFT JOIN fin.entities vs v_node for new/deleted/updated vertices (MAX(id)+1), per-e_* diff via edge_type, INSERT/DELETE not DROP. Falls back to full rebuild if delta >10% or schema_version bump. Depends on P0 gen + P1 cache (done).
- Effort: ~2d
- Status: DEFERRED — 4k edges, full rebuild OK.
- Reviewed 2026-09-05: row trigger FIRED (17,323 graph_edges > 10k; 69,292
  materialized across 17 e_*), but the deferred reason (rebuild cost) is not
  binding — full materialization measures 3.0 s vs vault_scaling's 5 s DuckDB
  rebuild budget. Scale strategy owned by archive/graph/vault_scaling.md
  (#204): re-evaluate incremental refresh when T1 fires (~1M doubled rows;
  currently 34K) or measured rebuild > 5 s. Do not build before that.
- Reviewed 2026-10-01: **HELD, and the trigger criterion is retired.** The
  "rebuild > 5 s" line was the T2 ladder LABEL in
  `archive/graph/vault_scaling.md` (whose §2 already answers the over-budget
  band with a recorded waiver + ART bridge, not with this item), and its
  materialize column was unreproducible anyway — the source script
  `/tmp/scale_bfs.py` is deleted. Re-measured scratch-isolated (production
  never opened for write; a non-DB_PATH `db_path` resolves to a sibling
  `.duckdb`): with R = `e_all_und` doubled rows,
  **T(R) ≈ 2.28 s + 3.0 µs·R** — R=0 → 2.28 s, R=115,030 (live) → 2.62 s,
  R=460,120 → 3.72 s. At live scale the rebuild is **~87% fixed statement
  overhead** (0.29 s extension INSTALL/LOAD, 0.21 s for 56
  `DROP TABLE IF EXISTS`, ~1.70 s planning + executing ~50 CTAS) and ~13%
  per-row, so P2.2's row-level diff would attack the small term while adding
  partial-application risk against the single file-level
  `_build_meta.generation` stamp that 23 `connect()` callers, `csr.py:298`
  and snapshot verification all trust. Hold in favour of, in order:
  (C) call `rebuild()` from `derive-relations --apply` / `parse_newsletter
  --apply` and make the stale-cache path fail fast — no ingest lane calls
  `rebuild()` today, and a `read_only=True` caller cannot take the RO fast
  path when the cache is stale, so N readers serialise on
  `<cache>.build.lock` behind one inline builder;
  (ii) fold the 12 `EDGE_REGISTRY` CTAS + `e_all_und`/`e_dir` into one
  `edge_type`-discriminated scan of `fin.graph_edges` and drop the
  duplicated `v_centrality_*` DROPs (they appear in both
  `_EXTRA_MATERIALIZED` and `_CENTRALITY_TABLES`) — aims at the 1.70 s, but
  check the `MATERIALISED_TABLES` / `check_cache_consistency` /
  snapshot-verify name contract before turning any projection into a view;
  (iii) table-level dirty tracking via per-input hashes in `_build_meta`,
  rebuilding only dirty tables — scales with the dominant term and keeps
  per-table drop-and-rebuild correctness. Re-anchor + in-repo probe filed as
  `graph_rebuild_scaling_probe.md`. Re-open only if all of:
  the build stops being overhead-dominated, generation bumps get frequent
  enough that (C) stops covering the exposure, or the derivation count grows
  enough to make per-derivation diff logic unavoidable. Do not re-open on the
  strength of the 5 s line alone.
  (Record correction: the "12 e_* + PROPERTY GRAPH" description above is
  stale — `PROPERTY GRAPH` was retired in #92, and `_EXTRA_MATERIALIZED` is
  31 entries subsuming `e_all_und`/`e_dir`/`h_edge`/`h_incidence`/ten
  `v_centrality_*`, so the build issues 56 DROPs plus ~50 CTAS.)

## P3.2 — Covering indexes (deferred)
- Current: EXPLAIN already SEARCH via existing indexes; index_report 0 redundant.
- Plan: add covering index entity_tags(tag, entity_name) if tag-intersection queries show TEMP B-TREE spill at 10k+ tags.
- Effort: 0.5d
- Status: DEFERRED — no spill observed (tag intersection uses TEMP B-TREE but <1ms).

## P3.3 — Parquet export wrapper (deferred)
- Current: docs mention COPY (GRAPH_TABLE…) TO … PARQUET, no helper.
- Plan: helpers/graph/query.py:export_query(sql, path) wrapper for notebook use.
- Effort: 0.5d
- Status: DEFERRED — no notebook demand yet.

## Notes
- P3.5 PRAGMA optimize already shipped in db_maint P2.5.
- To activate a deferred item: remove its entry here, implement, run make qa (per user request, qa deferred until all items done).
