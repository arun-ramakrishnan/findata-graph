---
title: "Store-wide reclaim — every sqlite/duckdb store under memory/ and memory/data/ gets a maint path"
status: executed
filed: "2026-10-14"
executed: "2026-10-14"
completed_md: "391"
area: "helpers/maintenance/db_maint.py, helpers/maintenance/store_sweep.py, Makefile"
---

# Proposal: Store-wide reclaim

**Filed:** 2026-10-14 · **Status:** EXECUTED (completed.md #391)
**Area:** `helpers/maintenance/db_maint.py`, `helpers/maintenance/store_sweep.py`
(new), `Makefile`

S1 (the standalone runner) is already implemented and validated as
single-file tooling — filed here BEFORE the multi-slice work (S2/S3:
Makefile + `db_maint` integration) per the 2026-08-21 house rule.

## 1. Problem

`make maint` → `helpers/maintenance/db_maint.py run()` maintains exactly
two files as primaries: `--db memory/research.db` (SNAPSHOT, BACKUP,
VACUUM, ANALYZE, REINDEX, wal_checkpoint, integrity/foreign-key/provenance
suite) and `--duckdb memory/graph.duckdb` (BACKUP, CHECKPOINT, VACUUM).
Five sqlite sidecars are **backed up but never reclaimed**
(`_backup_embed_store/_corpus/_convo_search/_convo_corpus/_memory_sidecars`),
and several stores have **no maint path at all**: `script_search`,
`doc_search`, `memory_search`, `md_lint_cache` (sqlite) and
`graph.xdist-shared.duckdb` + all three `memory/data/*.duckdb` (DuckDB).
Dead pages therefore accumulate unmeasured and unstamped — measured
2026-10-14: `script_search.db` sat at **529 freelist pages = 40 % of its
5.2 MB file** with no gate ever watching it.

Two related defects were found and fixed/closed in the same pass
(completed.md #390, 2026-10-13/14):

- `reclaim_freelist()` — the auto_vacuum=INCREMENTAL branch ran
  `incremental_vacuum(5003)` and reclaimed **1** page with no error, so
  the old error-only fallback never fired and the run ended by
  P3.6-alerting at its own 12.5 % freelist. Patched: re-measure after
  the incremental pass, full VACUUM above `WASTED_PCT` (5.0). Six unit
  tests in `tests/test_db_maint.py::TestReclaimFreelist`.
- REINDEX-after-VACUUM was hypothesised as the re-leak source and
  **disproved**: REINDEX on a post-VACUUM copy of the live research.db
  moves freelist 0 → 0.

## 2. Inventory — all stores (census 2026-10-14)

| store | eng | MiB | journal | auto_vac | freelist @census | stat1 | maint path TODAY |
|---|---|---:|---|---|---:|---:|---|
| `memory/research.db` | sql | 270.3 | wal | INCREMENTAL | 0 % | 46 | db_maint primary (full) |
| `memory/embed_store.db` | sql | 312.3 | wal | NONE | 0 % | 5 | backup only |
| `memory/convo_search_fts.db` | sql | 207.2 | wal | NONE | 0 % | 5 | backup only |
| `memory/corpus.db` | sql | 27.2 | wal | NONE | 0 % | 1 | backup only |
| `memory/doc_search.db` | sql | 21.3 | wal | NONE | 0 % (37 pgs pre-sweep) | 7 | none (index rebuild ≠ vacuum) |
| `memory/script_search.db` | sql | 3.1 | wal | NONE | 0 % (**529 pgs = 40 %**) | 9 | none |
| `memory/memory_search.db` | sql | 1.4 | wal | NONE | 0 % | 7 | none |
| `memory/md_lint_cache.db` | sql | 0.3 | **delete** | NONE | 0 % (1 pg pre-sweep) | 2 | none |
| `memory/convo_search.duckdb` | duck | 841.0 | — | — | n/a (WAL 0 B) | — | swap-rebuild (maint TIER1) |
| `memory/graph.duckdb` | duck | 52.3 | — | — | n/a (WAL 0 B) | — | db_maint BACKUP+CHECKPOINT+VACUUM |
| `memory/graph.xdist-shared.duckdb` | duck | 48.5 | — | — | n/a (WAL 0 B) | — | none |
| `memory/data/agent_traces.duckdb` | duck | 185.8 | — | — | n/a (WAL 0 B) | — | none |
| `memory/data/sources.duckdb` | duck | 42.0 | — | — | n/a (WAL 0 B) | — | none |
| `memory/data/model_usage.duckdb` | duck | 2.3 | — | — | n/a (WAL 0 B) | — | none |

A 0-byte `memory/graph.db` artifact was observed during the morning
census and was gone by 08:46 (external cleanup; the sweep tool reports
and skips 0-byte files rather than opening them). `memory/data/` also
holds the harvested conversation **parquet** corpus
(`_backup_convo_corpus` target) — not a db, out of scope. §2's
`md_lint_cache.db` `delete` journal is historical: the S1 validation
sweep's `connect()` normalized it to `wal` the same day (§5.1 ruling:
accepted).

## 3. Findings (measured)

- **F1 — dead-page accumulation is real, episodic, store-specific.**
  Pre-10-13 census: research 39.1 MiB (12.5 %), convo_fts 25.1 (10.8 %),
  doc_search 11.8 (37.2 %), memory_search 1.1 (43.3 %), embed_store 0,
  corpus 0. After the 10-13 reclaim (≈81.7 MiB) and a re-sweep on
  10-14: the only new debt was script_search 529 pages (40 %), doc_search
  37 pages, md_lint_cache 1 page — i.e. **within one day** two index
  stores re-accumulated. Nothing observed the accumulators before the
  census; nothing watches them after.
- **F2 — ANALYZE gaps.** `script_search` and `md_lint_cache` had never
  carried a `sqlite_stat1` table (planner flying blind on
  `script_query.py`'s index); primed in the sweep (stat1 rows 9 / 2).
  The other six carried stat1 from the 10-13 pass / db_maint.
- **F3 — cost.** Full acting sweep over all 14 stores ≈ **19.6 s**,
  of which **14.0 s is `quick_check` on convo_search_fts** (FTS5 content
  scan). With `quick_check` gated to mutations only, a clean sweep runs
  **0.0 s per sqlite store + 0.02 s per duckdb** — i.e. the integration
  cost in `make maint` is noise when clean.
- **F4 — DuckDB semantics.** All six stores: WAL 0 B; CHECKPOINT+VACUUM
  is size-neutral (DuckDB reuses free space inside the file and does not
  shrink files) → **file size is the wrong DuckDB waste metric**; any
  future alerting should baseline `PRAGMA database_size` instead. A store
  with another read-write holder fails the open and is **skipped, never
  forced** (no conflicts observed in either run).
- **F5 — growth ≠ waste.** embed_store 1.69× / convo_fts 2.28× growth
  since the 2026-09-29 census filing is real data (gemma cohort +
  harvest rows; that census trigger FIRED and is recorded in
  `pending.md`). The sweep complements — never replaces — the pending
  census re-run: it reclaims free pages, it does not shrink payloads.
- **F6 — coordination (two engines, two mechanisms).** DuckDB: the
  `.io.lock` flock estate — db_maint backup SH / maint EX,
  `agent_traces` EX, `index_sync` EX (S2.0, §5.2), query readers SH; a
  foreign RW holder fails the open and is skipped. SQLite: **no
  io_lock anywhere** — WAL already gives concurrent readers plus a
  single writer, so every conflict is a bounded `busy_timeout` (5 s,
  house `connect()`) instead of an unbounded flock wait; the sweep
  converts a loss into a `skipped` row (operator ruling 2026-10-14).
  A live FTS reader (`-shm` present) coexisted with the sweep window.
- **F7 — connection discipline.** The tool routes all sqlite opens
  through house `helpers.core.db.connect()` (B2 guard: no
  `sqlite3.connect`, no allowlist edit needed). VACUUM/ANALYZE/PRAGMA are
  transaction-safe on the deferred house connection when no DML precedes
  them — empirically validated on a scratch db 2026-10-14. Known side
  effect: connect() normalizes journal_mode to WAL → `md_lint_cache.db`
  flips `delete → wal` on its first acting sweep (house doctrine).

## 4. Slices

- **S1 (DONE; hardened post-audit 2026-10-14):
  `helpers/maintenance/store_sweep.py`.**
  Promoted from the trial script (`/mnt/data/tmp/store_sweep.py`).
  Census every store; per sqlite: `reclaim_freelist()` → ANALYZE iff
  stat1 missing → wal_checkpoint(TRUNCATE) → quick_check iff mutated;
  per duckdb: io_lock → CHECKPOINT+VACUUM+CHECKPOINT, skip-busy;
  `--dry-run` read-only census. Validated 2026-10-14 with both a dirty
  run (2.3 MiB reclaimed, all `quick_check ok`) and a clean idempotent
  re-run (0.0 s/store); ruff + B2 guard green. Deep-audit hardening
  (same day): per-store exception isolation on the sqlite path (a busy
  store is a `skipped` row — pre-fix, the sweep died on
  `OperationalError: database is locked` and every later store went
  unswept); sqlite io_lock dropped entirely (WAL + busy_timeout bound
  every conflict; no other sqlite tool flocks — the lock was ceremony
  with an unbounded wait); exit-code contract (`exit_code()`: busy
  skips stay 0, `error` rows and a non-ok `quick_check` → 1);
  `tests/test_store_sweep.py` (4 cases).
- **S2 — `make store-sweep` target + wiring decision.** Makefile target
  with `ARGS=--dry-run` passthrough; decide TIER1 (≈0.1 s clean, ~20 s
  dirty — the observed numbers above) vs `maint-full`. Recommendation:
  TIER1, with the sweep's timing surfaced in `outputs/maint_report.md`.
- **S3 — fold sidecars into `db_maint.run()`.** After the existing
  BACKUP step (backups already precede), loop the registered store list
  through the SAME `reclaim_freelist()` + analyze-if-missing + report row
  (`result["sidecars"]`), so one maint owns the estate and the decision
  logic lives in exactly one place. `store_sweep.py` remains the
  standalone/CI entry (it should import, not re-implement).
- **S4 — alerting.** Generalise the P3.6 alert: every registered store
  reports `wasted_pct` in the maint result and warns above `WASTED_PCT`
  (today only the primary alerts). DuckDB alerting deferred until a
  valid waste metric exists (F4).

Non-goals: no payload deletion (growth is real data), no schema change,
no eval-gate (no query-visible semantics change — the README's
eval-gate rule targets rosters/crosswalks/extractor rules).

### Acceptance criteria

1. Every store in §2 either has a maint path or an explicit
   "owned elsewhere" note in this proposal's archive form.
2. Consecutive sweeps are idempotent: second run reports `0.0M`
   reclaimed, sqlite stores all `skipped`, `quick_check` not paid.
3. `test_no_sqlite3_connect_outside_allowlist` stays green without any
   allowlist addition; `test_db_maint` + new sidecar-loop test green;
   ruff check/format clean.
4. Hot-store safety: a busy store (embed_store mid-embedding,
   convo_fts mid-harvest, xdist-shared mid-graph-build) is skipped with a
   reported reason, never queued or forced; VACUUM stays atomic (a failed
   run leaves the file untouched).
5. `make maint` TIER1 wall-time regression ≤ ~1 s when all stores are
   clean (measured in S2).

### Risks

- Write windows on hot stores — mitigated by io_lock EX + sqlite busy
  timeout + skip-on-error (F6); worst case a skipped store next run.
- DuckDB double-RW — skip-busy, already observed to work (F4).
- TIER1 creep — S2 gate: measure before wiring (criterion 5).
- `md_lint_cache.db` journal flip — accepted, or exclude the file by
  name if the operator objects (open question).

## 5. Open questions — all ruled 2026-10-14

1. `md_lint_cache.db` journal: **ACCEPT the `delete → wal`
   normalization** — in fact already paid during the S1 validation run
   (the next census reads `wal`); §2's `delete` row is historical.
2. `memory/data/*.duckdb` ownership: **resolved.** The `agent_traces`
   writer (`helpers/analytics/agent_traces.py` :3349) already honours
   the flock. The `sources.duckdb` writer is
   `helpers/maintenance/index_sync.py` (`DB` at :52; lanes
   `make refresh-indices` / `refresh-vigil` / `refresh-shp`; store born
   in completed.md #253) — it wrote with a bare `duckdb.connect`;
   S2.0 wrapped both connect sites (fresh-check SH, apply EX), making
   the DuckDB flock estate uniform.
3. `graph.xdist-shared.duckdb`: **REGISTER** in the sweep — skip-busy
   protects a mid-build store (operator ruling).
4. `convo_search.duckdb`: **CHECKPOINT-ONLY as belt-and-braces** — a
   swap is a full rewrite anyway; the sweep's ~free CHECKPOINT catches
   WAL leakage between rebuilds (operator ruling).

## Evidence

- Trial sweep 2026-10-14 (acting, all 14 stores): script_search
  `vacuum` 5.2→3.1 MiB, doc_search 21.5→21.3, md_lint_cache 1 pg,
  total 2.3 MiB reclaimed, every mutated store `quick_check ok`.
- Promoted tool, clean idempotent re-run same day: 8× sqlite `skipped`
  at 0.0 s, 6× duckdb `checkpoint+vacuum` at ≤0.02 s.
- Dry-run census of all 14 stores (table in §2).
- `tests/test_db_maint.py::TestReclaimFreelist` (6 cases),
  `tests/test_static_checks.py` (112) — green.
- Deep audit 2026-10-14 (pre-S2): census re-verified against §2 and
  overnight idempotence held (no new freelist debt beyond md_lint 1 pg);
  F7 reproduced on scratch (freelist 154 → 0 through house
  `connect()`); the busy-store crash was demoed live
  (`OperationalError: database is locked`) then fixed (A1); hardened
  sweep re-run: 7× sqlite `skipped` at 0.0 s + md_lint 1-pg vacuum
  (`qc:ok`) + 6× duckdb at 0.02 s, exit 0, ≈0.15 s wall — the S2 TIER1
  case. New: `tests/test_store_sweep.py` (4 cases), all suites green.

## 6. Execution record (2026-10-14, completed.md #391)

All four slices landed the day of filing (S1 was pre-validated):

- **S2.0 hardening** (deep-audit fixes, operator rulings): sqlite
  io_lock dropped — WAL + `busy_timeout` (5 s) bound every conflict and
  no other sqlite tool flocks, so the lock was ceremony with an
  unbounded wait; per-store skip-on-error on BOTH engines (the sqlite
  busy-store crash was demoed live, then fixed); exit-code contract
  (`exit_code()`: busy skips stay 0, `error` rows and a non-ok
  `quick_check` → 1 — the #380 anti-pattern closed). The `sources.duckdb`
  writer found via the search surfaces (`index_sync.py` :52; lanes
  `refresh-indices`/`refresh-vigil`/`refresh-shp`; store born #253) and
  wrapped in the flock (fresh-check SH, apply EX).
- **S2**: `make store-sweep` (ARGS passthrough, .PHONY + help echo).
  Wiring decision: **TIER1 via S3** — the estate rides inside
  `db_maint.run()`, so `make maint` covers it with zero maint.py
  changes; the standalone target remains the CI/on-demand entry. Clean
  estate cost measured ≈0.1 s (criterion 5 met with 10× headroom).
- **S3**: `SIDECAR_SQLITE` / `SIDECAR_DUCKDB` (path, vacuum) registry
  constants in `db_maint.py` — the single decision home — looped by
  `maintain_registered_sidecars()` after the primary's backup + reclaim;
  `result["sidecars"]` + a `=== SIDECARS ===` report section.
  `store_sweep.py` imports the registry (never re-implements) and
  cross-checks disk vs registry (#380: an unregistered store is
  reported, never silently swept). `convo_search.duckdb` carries
  `vacuum=False` (§5.4 ruling); xdist-shared registered skip-busy
  (§5.3).
- **S4**: every sqlite row reports `wasted_pct`; above `WASTED_PCT`
  (5.0) the run logs the P3.6 alert naming the store; a non-ok
  `quick_check` logs an ERROR. DuckDB alerting stays deferred (F4).
- **Live proof**: db_maint run 2026-10-14 — 12 sidecars, all clean
  (7× sqlite `skipped` 0.0 s, md_lint 1-pg vacuum `qc:ok`,
  checkpoint-only + 4× checkpoint+vacuum ≤0.02 s), exit 0.
- **Acceptance**: (1) registry covers §2 — primaries owned by db_maint
  itself, parquet corpus noted out-of-scope; (2) idempotent second sweep
  shown twice; (3) B2 guard green with zero allowlist edits, 163
  targeted tests + ruff clean; (4) skip-never-force both engines,
  unit-tested; (5) ≈0.1 s ≤ ~1 s.
