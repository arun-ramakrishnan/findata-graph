---
title: "convo_search dead-block compaction — swap-mode rebuild in the maintenance cycle"
status: executed
filed: "2026-11-06"
executed: "2026-11-07"
completed_md: "485"
area: "helpers/maintenance/rebuild_convo_search.py + helpers/maintenance/maint.py + helpers/maintenance/db_maint.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

# convo_search dead-block compaction — swap-mode rebuild in the maintenance cycle

**Date:** 2026-11-06 · **Status:** EXECUTED 2026-11-07 ·
**Area:** `helpers/maintenance/rebuild_convo_search.py` · `helpers/maintenance/maint.py` · `helpers/maintenance/db_maint.py`

## 1. TL;DR

`memory/convo_search.duckdb` reached **4.84 GB** while holding only
**~670 MB of live data** — ~3.7 GB (~77%) is dead blocks accumulated by
the incremental refresh path's in-place `DELETE` + reinsert cycles
(`rebuild_convo_search.py:754`, `:780`). DuckDB never returns freed
blocks to the OS; `VACUUM` + `CHECKPOINT` were measured to reclaim
**zero** bytes on a scratch copy (2026-11-06). The compacting path
already exists — full rebuild into a pid-tagged temp + atomic
`os.replace` swap (`rebuild_convo_search.py:837-952`, the graph-lane
pattern, with row-count parity and `assert_not_served` guards) — but
the maintenance cycle never invokes it: `maint.py`'s tier-1 runs
`rebuild_convo_search.py --incremental` only, and `db_maint.py`'s
VACUUM/ANALYZE/REINDEX cover the SQLite sidecars only.

## 2. Evidence (measured 2026-11-06, live store read-only)

### 2.1 File vs live data

| measure | value |
|---|---|
| file size | 4.842 GB (`ls`, inode 4344760) |
| `PRAGMA database_size` blocks | 17,315 / 18,471 used × 256 KiB ≈ 4.5 GiB |
| live data blocks (`PRAGMA storage_info` per table) | 2,553 ≈ **670 MB** |
| dead blocks (free-listed) | **~3.7 GB** |
| WAL at inspection | ~1,156 blocks (~290 MB), no `.wal` file on disk |

Live table footprint: `convo_search` 593 MB (197,515 rows) ·
`fts_main_convo_search.terms` 61.6 MB (27.4M rows) · `docs` 5.8 MB ·
`part_index` 4.7 MB · remainder < 2 MB each.

### 2.2 Reclamation recipes, measured on a scratch copy

| recipe | result |
|---|---|
| `VACUUM` + `CHECKPOINT` (read-write on copy) | 4.842 GB → 4.842 GB — **zero reclamation** |
| `LOAD vss; COPY FROM DATABASE src TO <fresh>` + `CHECKPOINT` | **4.842 GB → 1.252 GB**; row-count parity on `convo_search` / `part_index` / `convo_meta` / `harvest_meta`; FTS shadow schema + persisted HNSW carried |

The repo's own swap mode is strictly stronger than the raw COPY
experiment: it enforces row-count parity **before** the swap instant
(`:938-942`), refuses to run while the store is served
(`assert_not_served`, `:851` — `os.replace` would swap the inode under
a live reader), carries harvest watermarks / `part_index` forward
(`:128`), and persists the HNSW index into the temp before the swap
(`:889-905`).

### 2.3 Root cause in the cycle

- Incremental refresh = in-place `DELETE FROM convo_search WHERE …` +
  reinsert (`:754`, `:780`, `:420`, `:469`) — each cycle strands the
  replaced row groups as dead blocks.
- `maint.py` TIER1_STEPS run `rebuild_convo_search.py --incremental`
  only; `db_maint.py`'s VACUUM/ANALYZE/REINDEX are SQLite-sidecar ops.
  **No step in any maintenance path compacts the DuckDB.**
- The bloat is therefore monotonic under the current cycle.

## 3. Slices

### S1 — One-off compaction of the live store (EXECUTED 2026-11-06)

Ran `.venv/bin/python3 helpers/maintenance/rebuild_convo_search.py
--swap` on the main tree (no lock holder, store not served).

**Result (2026-11-06):** 4.842 GB → **1.256 GB** (−74%); block
anatomy after: 4,790/4,790 blocks live, **zero dead**, WAL 0.
Phases: hash+read 6.2 s · embed 7.5 s · **hnsw_build 1,638 s** (92% of
runtime; the RSS 4.4 GB peak) · fts_build 29.5 s · duckdb_write
0.4 s · swap 0.11 s · fts_sync 42.9 s; embed cache 197,515 hits /
0 misses (no re-embeds); harvest state carried (407 watermarks,
152,840 parts). `db_maint.py` post-swap: ANALYZE/REINDEX ok, integrity
ok, FK 0 violations.

**Acceptance.**
- ✅ `--check` drift exits 0 after the swap (0 new / 0 changed /
  0 deleted).
- ✅ File size after swap ≈ live data + index overhead (1.256 GB vs
  ~670 MB live + HNSW/FTS overhead).
- ✅ Sanity: `convo_query.py "hypergraph scaling ceiling"` answers
  normally post-swap.

### S2 — Dead-block tripwire + swap trigger in the maintenance cycle (EXECUTED 2026-11-07)

Shipped as `helpers/maintenance/duckdb_block_anatomy.py` (the probe:
`anatomy` / `anatomy_from_con` / `above_swap_trigger` / `summarize`,
read-only via `open_read_only`, advisory-degrading) wired at two seams:

- **maint.py** (`_maybe_upgrade_convo_swap`, run before the plan log):
  flips the convo-rebuild step `--incremental` → `--swap` when
  `dead_bytes >= max(1 GB, 40% of file)`; label/tuple shape/cmd[0]/cmd[1]
  untouched (test_maint structural assertions stay green); probe failure
  degrades to `--incremental` with a warning. Orchestrator-owned
  per-run decision, same idiom as `_served_file_lane_skip`.
- **db_maint.py** (F4 closed): the duckdb sidecar loop measures
  anatomy over its own connection (a nested `open_read_only` would
  deadlock on its exclusive flock) after CHECKPOINT, the SIDECARS
  report prints `dead {pct}% ({bytes})` per store, and the
  long-deferred DuckDB waste alert (docstring said "deferred until a
  valid waste metric exists, F4") fires P3.6-style above
  `DUCKDB_DEAD_PCT=40` / `DUCKDB_DEAD_BYTES=1 GB` — advisory only,
  pointing at the swap rebuild (db_maint never compacts DuckDB).

**Result (2026-11-07):** 55 targeted tests green (12 anatomy incl. a
live-store leg, 7 maint tripwire, 8 db_maint F4, + all pre-existing);
ruff clean; live `db_maint` SIDECARS report: convo_search 36.5% /
661.8 MB dead (the 23:01 incremental refresh after yesterday's swap
stranded 694 MB in ONE pass — the tripwire's raison d'être, measured),
xdist 0.4%, agent_traces 5.7%, sources 2.6%, model_usage 19.8%; no
false alert below threshold; maint `--dry-run` plan unchanged.

**Test-method note:** small synthetic stores CANNOT reliably strand
dead blocks — DuckDB 2.0 checkpoint fully compacts insert→delete,
delete+reinsert, and DROP patterns at tmp_path scale (measured). The
bloated case is pinned by the live-store test leg, not a synthetic
fixture; unit tests pin trigger arithmetic on crafted dicts.

**Acceptance.**
- ✅ `db_maint.py` report prints live/dead block anatomy per DuckDB
  store (verified live on all five duckdb sidecars).
- ✅ Trigger selects `--swap` above threshold, `--incremental` below
  and on probe failure (unit-tested with monkeypatched probe; live
  store currently below trigger → incremental stays).
- ✅ Step tuple shape in `maint.py` unchanged (test_maint 35 green).

### S3 — Analytics posture (no-op guard, CLOSED 2026-11-07)
`ANALYZE` on the DuckDB is a planner-stats refresh, not a size lever
(measured zero effect above) — run post-swap for query plans
(executed 2026-11-06, instant, block accounting exact after). Doc
sweep for VACUUM-reclamation claims: the assessment files already
record the correct law verbatim ("DuckDB VACUUM reclaims 0 bytes" —
`converged_db_domain_assessment.md:130`, `embedded_db_assessment.md:30`,
`unified_backend_assessment.md:171`); nothing to correct. **Size
reclamation in DuckDB is swap-rebuild-only** stands as the recorded law.

### S4 — Content-hash-era delta: compaction churn dies at the source (EXECUTED 2026-11-07)

**Why.** Gate arcs run `convo-fresh APPLY` multiple times a day; any
refresh wave that rewrites whole sessions strands dead blocks faster
than the corpus grows. Measured 2026-11-07: the live corpus is
**append-only in practice** (255/255 parts aligned, zero drops, zero
renumber across the active bili-era session — bili's compression is
reversible at the log/backing-db layer, so harvested originals never
disappear), which falsified the assumed "compaction re-shifts row_no →
whole-session rewrite" mechanism. The 694 MB dead is stable residue of
historical churn; A/B on a copy showed the FTS drop+recreate reuses its
own blocks (block-neutral), and a **no-op incremental run with the fix
below measures zero dead-block growth**. The fix removes the rewrite
CLASSES so future churn events have no mechanism, and the gated FTS
refresh stops paying ~34 s + index churn on every quiet gate run.

**Changes** (all landed):
- `rebuild_convo_search.py`: change fingerprint drops `row_no` —
  `(snippet, text_len, content_hash)` with `content_hash` = sha256(full
  text), new DDL column + stage field (legacy stores compare with
  hash=None until their next swap); `_refresh_fts_index` gated on
  `rows or drop_keys or not incremental`; `convo_meta` stamped
  `key_schema=content_v1`. PK stays `(harness, part_id)` — no
  duplicate-collapse risk, swap parity gate unaffected.
- `convo_query.py`: pointers are part-addressed (`file:part_id`;
  part_ids are stable across harness rewrites per harvest `_SCHEMA`),
  `expand()` filters the parquet by `part_id` instead of positional
  slice, legacy `file:<row_no>` pointers still expand. Pointer anchors
  on `.parquet`, not the last colon (part_id contains `:`).
- `harvest_conversations.py`: provenance drift check joins on
  `(file_path, part_id)` — the old positional row_no join would
  false-positive after every renumber.

**Acceptance.**
- ✅ Renumber no-op: `tests/test_convo_search.py::test_compaction_noop_and_drops_propagate`
  — reversed parquet order, identical text+part_ids → indexed == 0,
  zero re-embeds, index unchanged; pointers still resolve; a genuine
  drop still propagates.
- ✅ Zero-change run skips the FTS refresh
  (`test_no_change_run_skips_fts_refresh`); content change still refreshes.
- ✅ 96 targeted tests green across 7 suites; ruff clean.
- Migration: the DDL change rode a full `--swap` rebuild
  (corpus-sufficient; embed cache preserved in the FTS sidecar).

**Migration-route doctrine (recorded 2026-11-07 — do not repeat).**
This migration paid the ~27-min HNSW rebuild for a change that did not
need it. `rebuild_via_swap` builds a FRESH store from the corpus and
must construct the HNSW index pre-swap (readers never see an
index-less store), so EVERY swap pays the full HNSW tax even when the
vectors are byte-identical (here: embed cache 0 misses, 0 re-embeds).
Route selection for future store changes:

- **Schema-ADDITIVE migrations** (new column, nullable backfill OK):
  `COPY FROM DATABASE` — it carries the persisted HNSW index (proven
  in this proposal's §2.2 scratch experiment; requires `LOAD vss`
  before the COPY or the bind fails) — then `ALTER TABLE ADD COLUMN`
  on the copy (rows read NULL until they naturally rewrite; the S4
  fingerprint compares hash=None fine), then `os.replace`. Minutes,
  zero HNSW rebuild, zero dead blocks.
- **`rebuild_via_swap`** is for compaction and DDL changes that alter
  stored data SHAPES (dims, column types, PK).
- **In-place `UPDATE` backfill is never the answer** on these stores:
  a DuckDB UPDATE rewrites the whole row INCLUDING the vector column —
  the dead-block factory this proposal exists to kill. If a backfill
  is unavoidable in place, make it lazy (compute at compare/read time
  from the stored `text`) rather than materialising it row-by-row.

### S5 — Index retention window (FILED, DEFERRED — trigger-gated)

**Problem (measured 2026-11-07).** The index mirrors an append-only
corpus 1:1, so it grows monotonically: corpus ~2 MB/day of text
(November run-rate) → ~350 MB/month of index after vectors + FTS
(~5.8× amplification). ~4 GB/year organic on a ~1.3 GB compacted
steady state. Unbounded in principle, slow in practice; the binding
cost is not disk but the swap rebuild's HNSW build (~27 min at 197k
rows, roughly linear).

**Design.** Decouple the layers — the corpus stays complete forever
(archive of record: 115 MB zstd text is cheap, and it exists precisely
because the harness backing dbs purge); the INDEX becomes
`corpus ∩ retention window`:
- rebuild gains `--retention-days` (default: unlimited); rows with
  `ts` older than the window join `drop_keys` (S4's drop path already
  propagates corpus and window removals);
- window stamp in `convo_meta` (`retention_days`) so the check mode
  can report scope, not just drift;
- aged-out content stays text-reachable (corpus grep, `path:line`
  locators); a cold tier (keep text+FTS, drop vectors — 45% of index
  bytes) is the optional half-step before full expiry;
- corpus expiry is OUT of scope by doctrine.

**Source-side context (why no size-cap lever).** Bili's Absorb already
replaces large tool outputs with distilled summaries at the proxy
(~5.5K → ~1.1K tokens, `bili_absorb_stats_display`), on top of the
tiered T1/T2/T3 summaries — so corpus text for bili-covered sessions
is post-absorb, and the harvest's dedicated bili lane indexes the
compression summaries as their own rows (disjoint `sid#bid` part-id
scheme, `harvest_conversations.py:1384`). Originals are recoverable
from the bili log/backing db, not the corpus. A per-part size cap in
the indexer would therefore mostly re-implement what Absorb already
did at the source; the organic growth that remains is conversation
content itself.

**Trigger (any one, measured via the S2 anatomy probe + swap logs):**
- index live size > 3 GB, or
- swap rebuild > 45 min, or
- corpus run-rate doubles (>4 MB/day sustained).

Until a trigger fires: S2 tripwire + occasional swap suffice. Do not
build on spec — every layer here (window drop, cold tier) is a
reversible policy knob once the S4 no-churn base is in place, which
it now is.

## 4. Constraints and non-goals

- **No retention changes.** Corpus windowing / session expiry is a
  separate question; this arc only reclaims stranded space.
- **No hand-rolled COPY for the live store.** The swap path's guards
  (parity, not-served, sidecar carry) are the reason; the §2.2 COPY
  experiment is evidence, not a runbook.
- **Worktrees stay read-only** — APPLY (including `--swap`) is a
  main-tree operation, per the standing `*-fresh` doctrine.
- Single-writer law: the swap holds the DuckDB write lock for the
  rebuild duration; `witr -f memory/convo_search.duckdb` is the
  arbiter if a concurrent rebuild is suspected.

## 5. Gates

- S1: targeted checks only (`--check` + one `master_query` leg); no
  `make pytest`.
- S2: new unit test for the tripwire + `tests/test_maint.py` green;
  full arc gates on the operator's go per `doc/procedures/gates.md`.

## 6. Production execution record (2026-11-08)

First production activation of the S2 maint-cycle tripwire. `make maint`
logged `convo-rebuild upgraded to --swap for this cycle: dead blocks
3.32 GB, live 3570 blocks, dead 2052 MB (61.7%) >= trigger`; the swap
step ran 1596.16 s and `memory/convo_search.duckdb` went from
3,323,998,208 B (3170.01 MiB) to 1,300,508,672 B (1240.29 MiB) —
~1.88 GiB reclaimed, matching this proposal's projected live-data +
index size (~1.256 GB). Row-count parity was enforced before the swap
per S1. Measured during the ifub/store-census close-out arc; the
census reconciliation in `doc/improvements/pending.md` keeps the
pre-swap 3170.01 MiB value as history and records this post-swap size
separately.

**Trigger recalibrated 2026-11-08 (operator: "swap criteria is too
tight").** A swap rebuilds only the live data (~3570 blocks, ~27 min),
so the trigger sets fire frequency, not per-swap cost; the measured
cycle showed one incremental pass strands ~650-700 MB dead, so the
original max(1 GB, 40%) fired after just two passes.
`helpers/maintenance/duckdb_block_anatomy.py` now trips at
max(2 GB, 60% of file) — roughly doubling the interval (post-fire
trajectory: 35.7% dead after the next pass, firing ~3 passes out).
This first production fire would still fire under the raised trigger
(2,052 MB >= max(2 GB, 60% of 3.324 GB) = 1.994 GB); the boundary is
re-pinned in `tests/test_duckdb_block_anatomy.py`
(`test_recalibrated_trigger_2026_11_08`) and the flip fixtures in
`tests/test_maint.py` sit above the new legs.
