---
title: "SQLite retirement build route — full DuckDB consolidation (cleanup + sidecar twin deletion + concurrent-writer audit)"
status: executed
filed: "2026-11-21"
executed: "2026-11-21"
completed_md: "552"
area: "helpers/core/db.py + helpers/core/db_engine.py + helpers/maintenance + memory/ estate"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# SQLite retirement build route — full DuckDB consolidation

**Date:** 2026-11-21 · **Status:** PROPOSED · **Decision:** operator
ordered the **BUILD route** (2026-11-21): retire SQLite — delete the
research.db anchor and the folded sidecar twins, design a full move to
DuckDB, and audit the SQLite-dependent concurrent writers with a
performance-mitigation plan.

## 1. Decision record

- Freeze-or-build call (parquet_snapshot_duckdb_reader.md S2–S5 gate):
  **BUILD**. The frozen `memory/research.db` rollback anchor and the
  folded sidecar twins are scheduled for deletion, not indefinite
  retention.
- This proposal supersedes the deferred posture of
  `duckdb_full_consolidation.md` (#483, "do not merge now; deferred"):
  the triggers that kept that gate closed have changed since
  2026-11-06 (below, §3).

## 2. Estate inventory (live, 2026-11-21)

| Store | Size | Status | Disposition |
|---|---|---|---|
| `memory/research.db` | 363 MB | frozen rollback anchor | **DELETE after soak** (canonical `research.duckdb` 174.6 MB) |
| `memory/doc_search.db` | 45.7 MB | legacy FTS5 twin (fold S1 landed) | **DELETE after soak** |
| `memory/memory_search.db` | 6.9 MB | legacy FTS5 twin (fold S2 landed) | **DELETE after soak** |
| `memory/script_search.db` | 5.1 MB | legacy FTS5 twin (fold S3 landed) | **DELETE after soak** |
| `memory/git_search.db` | 41.3 MB | legacy FTS5 twin (fold S4 landed) | **DELETE after soak** |
| `memory/convo_search_fts.db` | retired (669 MB freed) | auto S5 | already gone |
| `memory/embed_store.db` | 552 MB | embed-cache pool (SQLite WAL) | **STAY** (architectural, §5) |
| `memory/corpus.db` | 61.6 MB | S1b corpus cache | **MOVE → `corpus.duckdb`** (trivial single-writer cache, §5) |
| `memory/md_lint_cache.db` | 0.3 MB | operational lint cache | **MOVE → `md_lint_cache.duckdb`** (trivial single-writer cache, §5) |
| harness-owned (`bili index.db`, `opencode.db`, dsh/prime) | — | outside repo estate | n/a |

Already DuckDB: `research.duckdb` (canonical, quack-served),
`graph.duckdb` (quack-served), `convo_search.duckdb`,
`note_search.duckdb`, `doc_search.duckdb`, `memory_search.duckdb`,
`script_search.duckdb`, `git_search.duckdb`, `embed_store.duckdb`,
`note_vectors.duckdb`, `gate_runs.duckdb`.

## 3. What already landed (build route is now cheaper than the 2026-11-06 sizing)

The blockers that stopped #431/#483 have mostly cleared:

| Old blocker | Status today |
|---|---|
| FTS5 `note_search` INSIDE research.db (carve-out prerequisite) | **GONE** — note_search left research.db (completed.md #530): DuckDB native-FTS sidecar |
| FTS5 sidecar twins (doc/memory/script/git) | **FOLDED** — `duckdb_sidecar_fold.md` S1–S4 landed 2026-11-15; consumers flipped; legacy `.db` rollback-only |
| House `connect()` not selector-aware | **FIXED** — S1 of parquet_snapshot_duckdb_reader.md (this day): `connect()` dispatches through `research_store_connect`; writer helpers dual-engine |
| Quack wire serving | **LIVE** — servers serve `graph.duckdb` AND `research.duckdb` RW (pids verified, health=ok); WireConnection executes with inline literals; `assert_not_served` refuses file-mode writes to served stores |
| Vector leg | solved (pure-stack #481; HNSW inline; note_vectors mirror) |
| FTS quality | convo port measured +77% MRR (0.347→0.5076); Doc FTS constructible (spike §6.2) |
| Snippet/highlight | 4 call sites mapped, Python `note_snippet` drop-in exists |

Remaining work is therefore the **cascade rewrites, snippet rework,
maintenance/integrity finalization on DuckDB, deletion mechanics, and
the concurrent-writer audit** — not the full 19–27 focused-day port.

## 4. Concurrent-writer audit (SQLite-dependent lanes → DuckDB plan)

| Lane | Store | SQLite WAL semantics today | DuckDB plan / mitigation |
|---|---|---|---|
| Flask app (`app.py`) | research.db (RW holder, writes nothing) | parallel readers | flip to RO opens (mandatory under quack P2); reads already wire-routed |
| rebuild_note_search | canonical | busy-queue one-shot | wire write through quack (serialized); already duckdb branch |
| derive-* family (stable_write) | research.db | DELETE-prefix-then-INSERT batched one-shots | wire write (serialized); batch within one transaction |
| maintenance syncs (related_party, shareholding, exchange, wikidata, fold_identifiers, rename_entity, move_sector, …) | research.db | operator-invoked one-shots | wire write; ordered rewrites replace CASCADE (phase 3) |
| sync_tags / coverage / sector-links | research.db | busy-queue (parse_newsletter spawns sync_tags) | serialized via quack single-RW; queue not crash |
| snapshot_db (4-worker pool) | research.db / sidecars | WAL snapshot reads | per-store workers keep shape; SQLite half already dispatches to DuckDB export/verify (S2–S4) |
| db_maint | research.db / sidecars | VACUUM/ANALYZE/REINDEX | CHECKPOINT-only + first-party integrity scans |
| embed-cache warming (rebuilds, benches, CachedEmbed backfills) | embed_store.db | **multi-process WAL writers (the load-bearing case)** | **STAY SQLite** — DuckDB admits one RW process; pooling value is parallel warming. Mitigation: keep it; route rebuild embed backfills through a single writer when/if it ever moves |
| corpus walk+parse | corpus.db | single-writer cache | MOVE — one table, SQL portable (INSERT OR REPLACE, PRAGMA table_info, ALTER ADD COLUMN); loss = 8.6 s rebuild |
| md_lint_cache | md_lint_cache.db | single-writer | MOVE — meta+verdicts tables; loss = verdict recompute |
| legacy sidecar `.db` twins | — | rollback-only now | delete; rollback-path tests updated to "absent is handled" |

Verdict: the only load-bearing SQLite multi-writer is **embed_store.db**
(WAL parallel warming), which is a designed stay. corpus.db and
md_lint_cache.db are trivial single-writer caches and move early (§5).
Everything else serializes cleanly under quack's single-RW-per-store
model or is a CLI one-shot.

## 5. Design — phases

- **P1 — cascade rewrites (prerequisite, unchanged sizing):** 22 FK
  rows ≠ NO ACTION (15 CASCADE delete, 21 CASCADE update, 7 SET NULL
  delete; entities-rooted, ≤2 hops) → explicit ordered multi-table
  rewrites inside one transaction for: rename_entity, move_sector,
  store_sweep, exchange_sync, name_dedup, retire_sector_dyads,
  fold_identifiers, build_sector_hierarchy, migrate_to_graph_edges.
- **P1 addendum — canonical store has ZERO FK constraints (2026-11-21):**
  the canonical `research.duckdb` DDL was regenerated without any
  `REFERENCES` clause — `information_schema.table_constraints` shows only
  CHECK(152) + PRIMARY KEY(30), no FK. The frozen SQLite store *did*
  declare them (`hyper_incidences.entity_name`, `graph_edges.source/
  target`, `quotes.entity`, `events.entity` + `counterparty_entity` SET
  NULL, `company_metrics.entity`, `event_participants.participant`,
  `entity_tags.entity_name`, every `agent_id` → `provenance_agents` SET
  NULL). So on canonical: deletes never fail AND never clean children —
  every delete/rename parent flow must do the child work itself. Status:
  `name_dedup` dual-engine (information_schema join; 30/30 live),
  `index_label_dedup` explicit `hyper_incidences` cleanup (suite green),
  `purge_stranded_companies` predicate now enumerates every FK child
  surface incl `ppr_results` (live `--check` clean), `rename_entity` already routed through
  `swap_rewrite_flows.rename_entity` (rotate/repoint; tested).
  `swap_rewrite_flows` already ships `ENTITY_CHILDREN`/`drop_entity`/
  `drop_event`/`drop_agent` — no live callers yet; wire as P1 flows
  land. `provenance_agents` deletion has no live caller today.
- **P2 — snippet/highlight rework:** 4 call sites
  (`rebuild_doc_search.py:988`, `rebuild_memory_search.py:1271`,
  `rebuild_script_search.py:1956`, `app.py:1128`) → Python
  `note_snippet`-style window over stored text + native `match_bm25`
  ranking. ✅ VERIFIED ALREADY LANDED 2026-11-21: native DuckDB search
  paths exist in all three rebuild scripts (`_search_docs_duckdb` at
  rebuild_doc_search.py:1838, `_search_memory_duckdb` at
  rebuild_memory_search.py:1858, `_search_scripts_duckdb` at
  rebuild_script_search.py:2489, app.py `_snippet`) with Python
  highlighting; 121 duckdb-search/api tests pass on canonical
  (test_doc_query_duckdb, test_note_query_duckdb,
  test_script_query_duckdb, test_api_search, test_api_docs,
  test_api_scripts_search, test_fts_topk_parity).
- **P3 — maintenance/integrity finalization:** db_maint CHECKPOINT-only
  for duckdb stores (already in `_AUTO_VACUUM_MAP`, incl corpus/md_lint
  since P5); ✅ 2026-11-21 added first-party orphan scans to
  `helpers/misc/database_integrity_check.py` (canonical has no FK
  enforcement, so scans are load-bearing): `check_orphan_incidences`
  (hyper_incidences→hyper_edges/entities), `check_orphan_graph_edges`
  (source/target→entities), `check_orphan_event_participants`
  (event_id→events, participant→entities), `check_orphan_ppr_results`,
  `check_dangling_agent_ids` (warning). All 0 orphans on live canonical;
  97 integrity-checker tests pass; report + CLI now surface the new
  sections. Snapshot/verify already duckdb-dispatched (S2–S4);
  rollback-path tests flipped to "absent is handled".
- **P4 — deletion mechanics:** staged via
  `helpers/maintenance/build_route_p4.py` (plan/readiness CLI, never
  deletes). `--check` gates readiness: enumerates the 5 frozen stores
  (`memory/research.db` 363.5 MB + doc/memory/script/git `.db` twins),
  verifies every backup exists (`db-backup/research_backup.db.zst`,
  `memory_sidecars_backup.tar.zst`), the tracked parquet restore axis
  (`snapshots/parquet/` + `restore_sqlite_from_parquet`) is intact, and
  scans `helpers/` for LIVE openers of the frozen stores (reference-kind
  classification: only real `sqlite3.connect`/`.backup` calls outside
  bench/restore modules count — constant/CLI-default/docstring mentions
  are inert because `connect()` now dispatches to DuckDB). Current state:
  READY (exit 0). `--assert-gone` verifies post-deletion. After deletion:
  update `SIDECAR_SQLITE`, `store_sweep.py` census, `covered` sets;
  parquet `_schema.sqlite.sql` stays as the restore contract. Actual
  deletion runs only on operator go after soak.

  **STATUS: EXECUTED 2026-11-21 (operator go m01324).** Refresh
  `db-backup/research_backup.db.zst` (WAL-consistent online-backup →
  zstd, 102,263,277 B, restore-verified 39 tables; prior capture kept as
  `research_backup_pre_p4_delete_2026-11-21.db.zst`). Forensic copies →
  `/mnt/data/tmp/p4_forensic/`. Deleted all five frozen stores;
  `--assert-gone` exit 0. Census updated: `SIDECAR_SQLITE` = embed_store.db
  only; `store_sweep.py` `_OWNED_ELSEWHERE`/`_FILING_MB` pruned. 59 tests
  pass. Restore net: parquet axis (30 tables) + `_schema.sqlite.sql` via
  `restore_sqlite_from_parquet` + zstd backups; twins rebuildable.

  **BACKUP COVERAGE AUDIT (2026-11-21):** every store in `memory/` +
  `memory/data/` is covered by a backup artifact (verified: decompress +
  DuckDB open + table count):
  - Frozen SQLite (deleted): research.db → `research_backup.db.zst`;
    doc/memory/script/git FTS5 twins → `frozen_ft5_twins_backup.tar.zst`
    (all four members, from forensic copies).
  - Live SQLite: embed_store.db → `embed_store_backup.db.zst`.
  - DuckDB primaries: research.duckdb → `research_backup.parquet_export_2026-11-21.tar.zst`
    (fresh per-table COPY export over the quack wire, 32 base tables —
    file-level backup impossible while quack holds RW) + tracked
    `snapshots/parquet/duckdb/` axis; graph.duckdb →
    `graph_backup.duckdb.zst` + `graph.snapshot.duckdb.zst`.
  - DuckDB sidecars (new coverage): script_search/doc_search/memory_search/
    git_search/note_search/md_lint_cache/gate_runs/note_vectors/
    graph.xdist-shared/corpus → `<name>_backup.duckdb.zst`; convo_search →
    `convo_search_backup.duckdb.zst`; `data/sources` refreshed →
    `sources_backup.duckdb.zst` (2026-11-21); `data/agent_traces` +
    `data/model_usage` → existing artifacts (2026-11-21 07:44);
    embed_store.db refreshed → `embed_store_backup.db.zst` (2026-11-21).
  - Registry: `note_vectors.duckdb` + `gate_runs.duckdb` added to
    `SIDECAR_DUCKDB` so db_maint keeps them covered going forward.
- **P5 — embed stay + trivial-cache moves:** embed_store.db stays
  SQLite (WAL multi-process warming value; DuckDB cannot host the vecdb
  attach; single-RW would force backfill serialization). corpus.db →
  `corpus.duckdb` and md_lint_cache.db → `md_lint_cache.duckdb` move
  EARLY: they exercise the same migration mechanics at negligible cost,
  are trivially regenerable (corpus 8.6 s rebuild; lint verdicts
  recompute), and their SQL is portable as-is (INSERT OR REPLACE,
  PRAGMA table_info, ALTER ADD COLUMN). Move = new `.duckdb` file with
  identical schema + paths updated in `helpers/core/corpus.py`
  (`_CACHE_DB` :72) and `helpers/misc/markdown_lint.py`
  (`_CACHE_DB` :70), retire the `.db` file. ✅ DONE 2026-11-21:
  both caches moved (`memory/corpus.duckdb`, `memory/md_lint_cache.
  duckdb`), sqlite files deleted, `SIDECAR_SQLITE` reduced, backup leg
  switched to `_duckdb_zstd_backup`, 48-test integration chain green,
  corpus warm smoke 1,401 rows / 0 null-text.

## 6. Performance-mitigation plan (DuckDB single-RW)

1. **Quack server-per-store RW** — already live for research + graph
   (serialized writers; token auth; `assert_not_served` split-brain
   guard). Extend enrollment to remaining duckdb stores as they become
   primary.
2. **Write batching / transaction scope** — CLI one-shots already
   batch; keep DELETE-prefix-then-INSERT in one transaction
   (autocommit-per-statement otherwise).
3. **Reads over wire** — wire reads measured ≈5 ms (search_tui lanes);
   per-query embedded connect ≈ +50 ms avoided by persistent wire
   handles (already the pattern).
4. **Maintenance cost drop** — VACUUM/ANALYZE/REINDEX scheduling gone
   (CHECKPOINT only); unified per-store snapshot/backup shape.
5. **Potential hot path** — if a measurement shows a writer lane
   contending on the single-RW server, add a write queue (flock or
   server-side) with retry; the quack stop-file window pattern already
   exists for bulk loads.
6. **SQLite-specific tuning loss** — 80 MB cache / mmap / busy_timeout
   semantics are not portable; DuckDB uses its own buffer pool
   (default + `memory_cap_config`).

## 7. Acceptance criteria

1. `make snapshot-check` green against DuckDB source with zero SQLite
   research reads; `make qa` green.
2. `grep -r "memory/research.db" helpers/` → only documented
   rollback/restore/`_schema.sqlite.sql` references.
3. `SIDECAR_SQLITE` contains only embed_store.db (+ any future stays);
   corpus/md_lint_cache twins absent from census.
4. Concurrent-writer audit table (§4) verified against live tree — no
   undeclared SQLite multi-writer remains.
5. Cascade flows (P1) pass with explicit ordered rewrites; FK
   `foreign_key_check`/orphan scans green on DuckDB.
6. Snippet rework matches the old `<mark>` highlight contract at the 4
   sites.

## 8. Risks

- **Cascade rewrites** (P1) carry the highest regression risk — the
  exact surface `make qa`/gates.md protect; keep entities-rooted order.
- **Quack P2 posture** for research.db means no local open (even RO)
  while served — consumers must use the wire (scaffolding exists;
  113 importers already funnel through `connect()` S1).
- **embed_store.db stays** — a permanent two-engine boundary; accepted
  by design, documented, and revisited only on a concrete trigger.
- **corpus / md_lint_cache moves** — low-risk: single-writer trivial
  caches, regenerable; a bad move recovers by deleting the new file and
  re-running the corpus walk / lint once.
- **Deletion is irreversible** — soak gate (snapshot-check + qa green
  over weeks) MUST precede P4; the binary zstd back-ups remain the
  final safety net.
