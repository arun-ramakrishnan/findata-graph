---
title: "Served-store read funnel: namespace-safe liveness, wire-attach fallback, bypass routing"
status: "executed"
filed: "2026-11-03"
executed: "2026-11-03"
completed_md: "476"
area: "helpers/core/duckdb_shared.py, helpers/misc/duckdb_lock.py, five raw read bypasses"
---

# Served-store read funnel: namespace-safe liveness, wire-attach fallback, bypass routing

**Date:** 2026-11-03 · **Status:** EXECUTED ·

Provenance: discovered in a parallel unlocked-work session (2026-11-03) and
grounded in-tree the same day; same failure shape is already in the record —
2026-10-22 convo "162 test failures + multiple leg failures, all one shape"
(ses_eb65e0656ffeE3TmgW7XcIMVWl.parquet:1911).

## Symptom (reproduced twice, parallel session)

memory/.quack.json serves graph at quack:localhost:9494 (pid 3393187); port
9494 is LISTENing. In a PID namespace (shell pid 1 = bwrap, own pid 3):
os.kill(3393187, 0) raises ProcessLookupError → load_discovery() returns {}
(disc keys: [], is_served: False) → open_read_only silently degrades to file
mode → hard failure:

    _duckdb.IOException: IO Error: Could not set lock on file
    ".../memory/graph.duckdb": Conflicting lock is held in PID 0.

(PID 0 because the holder lives outside the namespace.) Both
helpers/misc/schema_dump.py --check and
pytest tests/test_schema_drift.py::TestSchemaDrift::test_tracked_ddl_matches_live_dbs
die this way (1 failed in 1.98s). There is NO real drift: attach over the
wire renders 44 duckdb_tables() rows, body sha 1cce38a510a9 — byte-identical
to tracked schema/duckdb/graph.sql (IDENTICAL: True). So the blocking gate
fails for a non-data reason in ANY PID-namespaced context: container, CI,
subagent sandbox.

## Mechanism (verified cites)

- helpers/core/duckdb_shared.py:54 `_pid_alive`: bare os.kill(pid, 0)
  (:57-59) — cannot see holders outside the namespace; `is_served` then lies.
- helpers/misc/duckdb_lock.py:180-184 open_read_only: is_served False ⇒
  silent file-mode fallback under io_lock — the degrade is the trap.
- Funnel adoption is partial (~12 live connect_shared call sites); raw
  read bypasses remain at five sites, all verified:
  helpers/misc/master_query.py:186 (gate-runs timing DB),
  helpers/misc/maint_query.py:27, helpers/misc/analytics_fresh.py:152 and
  :240 (model_usage), helpers/graph/derive_indices.py:367 (sources DB).
  helpers/graph/algorithms.py:90 duckdb_connect already delegates to
  query.connect, so its 14 read-site rows are routed — leave them.

## Slices

- **S1 — wire-attach fallback in open_read_only:** on a lock-conflict open
  failure with a matching discovery entry, attach over the wire instead of
  raising. schema_dump.duckdb_ddl already goes through open_read_only, so
  S1 alone re-greens the drift gate.
- **S2 — namespace-aware _pid_alive:** on ProcessLookupError, fall back to
  a wire/port probe (discovery host:port reachable ⇒ served) so is_served
  stops lying in namespaces.
- **S3 — route the five bypass sites** through connect_shared.
- **Tests:** tests/test_schema_drift.py already documents mutations
  M1/M2/M3; add one unit test for S1 (fake discovery entry + lock-raising
  opener).

## Explicitly out of scope

- Serving convo_search.duckdb (616 MB, unserved — .quack.json lists only
  graph): a separate, smaller wire-coverage win, not gate-relevant.
- Declined neighbours (probed, none is the better unlock): HNSW macros
  (overlap@10 ≈57% on the 16.5k corpus, +75% db size, no speed win —
  doc/improvements/pending.md:526-547); HGX lane 4 (reclassified to
  repo-native scipy bipartite BFS, doc/improvements/pending.md:428-435);
  P2.2 incremental materialisation (rebuild ≈87% fixed overhead —
  doc/improvements/pending.md:708-717); thin-tail D1-D3 with live triggers
  (doc/improvements/pending.md:6-40).
