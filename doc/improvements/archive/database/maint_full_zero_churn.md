---
title: Zero-Churn maint-full — Stable Event/Embedding Writes, Canonical Louvain Labels, Deterministic e_* Exports
status: executed
filed: '2026-08-22'
executed: '2026-08-22'
completed_md: '147'
area: four small code changes across three helpers plus shared-module
---

# Proposal: Zero-Churn maint-full — Stable Event/Embedding Writes, Canonical Louvain Labels, Deterministic e_* Exports

**Status:** EXECUTED (implemented 2026-08-22; four live maint-full cycles
verified — the 4th left all 38 snapshot blobs byte-identical; gates green
2026-08-22: qa 8/8 with 2,061 tests + snapshot-check, user-run
advisory/perf, lint-audit findings cleared). Archived to
`doc/improvements/archive/database/maint_full_zero_churn.md`.
**Date:** 2026-08-22
**Author:** Agent analysis (user-directed)
**Builds on:** commit `76ab554` (snapshot refresh documenting the churn,
with the table-by-table diff evidence), completed.md #147 (stable derived
writes + B4 writer-side generation bumps, 2026-08-21), and the
`_stable_prefix_replace` pattern from derive_insights
(`helpers/graph/derive_insights.py:914`).
**Scope:** four small code changes across three helpers plus shared-module
extraction, tests for each, and one ride-along live maint-full + snapshot
refresh at the end. Explicitly out of scope: incremental derive_events
(assessed NO 2026-08-21), any change to the louvain partition algorithm
itself, and sqlite-side export ordering.

---

## 1. TL;DR

The 2026-08-22 post-gates `make maint-full` changed 7 of the 36 parquet
snapshot blobs despite a corpus with zero content changes (commit
`76ab554` has the full diff audit). Every change was benign churn from
three mechanisms: **created_at restamps** on tables whose writers still
delete-and-reinsert (`events`, `company_embeddings`), a **community-label
permutation** (all 1,293 `louvain_community` rows renumbered under a
bit-identical modularity), and **physical-order drift** in the two
promoted-edge parquet exports. This proposal eliminates all three classes
so a no-op maint-full cycle produces **zero changed snapshot blobs** —
the same guarantee the 2026-08-21 stable-writes work already delivered
for quotes/company_metrics and note_search.

Success is one sentence: run `make maint-full` twice on an unchanged
corpus; the second cycle's re-snapshot leaves `git status` clean under
`snapshots/`.

## 2. Background — the observed churn, with evidence

All findings below come from the keyed EXCEPT diff (parent `aa1bc65` vs
patch `76ab554`) documented in the db_sync commit message.

| Snapshot blob | Churn | Mechanism | Writer |
|---|---|---|---|
| `sqlite/events.parquet` | all 349 rows' `created_at` restamped (ids + every content column identical) | DELETE-prefix-then-INSERT re-inserts each cycle; `created_at` re-defaults | `derive_events.apply()` — `helpers/graph/derive_events.py:479` |
| `sqlite/company_embeddings.parquet` | all 1,065 rows' `created_at` restamped (vectors byte-identical) | `INSERT OR REPLACE` = delete+insert → `created_at` re-defaults | maint loop — `helpers/graph/embeddings.py:243` |
| `sqlite/db_meta.parquet` + `duckdb/_build_meta.parquet` | generation 25496→25497, built_at date roll | B4 manual bump fired on a cache miss whose re-embed produced an identical vector | `helpers/graph/embeddings.py:268` |
| `sqlite/graph_analytics.parquet` | exactly the 1,293 `louvain_community` rows: labels renumbered, modularity bit-identical (`0.3294992733001716`) | community numbering follows node iteration order, which follows the rebuilt tables' physical order | `onager.louvain_communities` — `helpers/graph/onager.py:354`; persisted via upsert `helpers/graph/algorithms.py:622` |
| `duckdb/e_belongs.parquet`, `duckdb/e_has.parquet` | zero row diffs; bytes differ from row order only (now id-sequential after rebuild) | export has no ORDER BY → physical order leaks into blob bytes | `snapshot_db.py:627` (`COPY (SELECT * FROM {t}) TO …`) |

The 29 unchanged blobs prove the export pipeline itself is deterministic
— only genuinely rewritten tables moved. The perf suite's live-DB writer
benchmarks (`graph_rebuild`, `derive_events`, …) produce the same churn
class, so this fix also quiets post-perf drift.

Why this matters beyond cosmetics: a snapshot diff that shows change
should mean content change ("a created_at diff now means real change" —
the #143 principle). Today every maint-full cycle cry-wolves 7 blobs.

## 3. Design

### F1 — Stable writes for derive_events (shared `_stable_prefix_replace`)

`derive_events.apply()` (`helpers/graph/derive_events.py:479`) documents
its own idempotency as "DELETE-then-INSERT": rows under
`source_ref LIKE 'derive:events:%'` are cleared, then the scan re-inserts,
and `created_at` (omitted from `_INSERT_SQL`, :472) re-defaults to
CURRENT_TIMESTAMP every run. The stable variant already exists —
`derive_insights._stable_prefix_replace` (`helpers/graph/derive_insights.py:914`)
multiset-matches new rows against existing derived rows on full content,
keeps id AND created_at for matches, deletes stale rows by id, and
inserts only genuinely new rows.

Changes:

1. Extract the helper to `helpers/core/stable_write.py` as
   `stable_prefix_replace(conn, table, prefix, cols, insert_sql,
   new_rows)`; `derive_insights` keeps a one-line private alias so its
   call sites and tests are untouched.
2. Rewire `derive_events.apply()` to use it. The events table is
   drop-in compatible: it has both `id` and `source_ref`, uses the same
   prefix convention, and `properties` is already canonically serialized
   (`json.dumps(..., sort_keys=True)`) so content matching is
   deterministic. Hand-seeded `manual:`/`migration:` rows stay untouched
   (prefix-scoped).

Semantics are identical to today (final derived row set = current scan);
only id/created_at stability is new.

### F2 — Stable writes for the company_embeddings maint loop

The maint loop (`helpers/graph/embeddings.py:243`) writes every company
with `INSERT OR REPLACE INTO company_embeddings (company_name, embedding,
model)` — OR REPLACE deletes and re-inserts, so `created_at` re-defaults
for all 1,065 rows even when the vector is byte-identical (warm cache).

Changes:

1. Replace with a guarded upsert that never touches unchanged rows:

   ```sql
   INSERT INTO company_embeddings (company_name, embedding, model, created_at)
   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
   ON CONFLICT(company_name) DO UPDATE SET
       embedding = excluded.embedding,
       model     = excluded.model,
       created_at = CURRENT_TIMESTAMP
   WHERE company_embeddings.embedding IS NOT excluded.embedding
      OR company_embeddings.model    IS NOT excluded.model
   ```

   (`IS NOT` for NULL-safe compare; requires SQLite ≥ 3.35 — verify at
   implementation, fall back to read-compare-skip if not.) A changed
   vector correctly restamps its row; an identical one writes nothing.
2. Tighten the B4 bump (`helpers/graph/embeddings.py:268`) from
   `if cache_stats["misses"] or gc:` to fire on **actually changed
   rows** (upsert `total_changes` delta) or GC — the comment's stated
   intent ("ONLY when content actually changed") already promises this;
   today a miss that re-embeds unchanged text bumps and flips `_is_warm`
   for nothing.

### F3 — Canonical louvain community labeling

`onager.louvain_communities` (`helpers/graph/onager.py:354`) returns
whatever community ids the underlying pass assigned; numbering follows
node iteration order, so a DuckDB rebuild renumbers every community
under an identical partition (observed: modularity bit-identical across
all 1,293 rows, every label changed).

Change: inside `louvain_communities`, before returning, relabel
canonically — group members by community, order groups by
**(-member count, lexicographically smallest member name)**, renumber
0..k−1. Deterministic for a given partition regardless of node order;
when the graph genuinely changes, labels change (correctly) with it.

Single choke point: the CLI `louvain`, `recompute-graph --apply` (via
`_run_louvain` → `write_analytics`, `helpers/graph/algorithms.py:495/622`),
and any app consumer all inherit stable labels for free. Note
`write_analytics`' upsert sets only `value` (never `computed_at`), so
once values stop churning, no stamp churn either.

### F4 — Deterministic e_* parquet export order

`export_parquet_duckdb` copies each materialized table with
`COPY (SELECT * FROM {t}) TO …` (`helpers/maintenance/snapshot_db.py:627`)
— no ORDER BY, so physical row order leaks into blob bytes. That is the
sole cause of the e_belongs/e_has churn (content set-identical, order
not).

Change: order by primary key for the materialized edge tables — a small
explicit map (e.g. `{"e_belongs": "id", "e_has": "id", …}` for the
`EDGE_REGISTRY` tables, all `id`-keyed) applied as
`COPY (SELECT * FROM {t} ORDER BY {key})`. Default stays unordered for
anything not in the map rather than silently reordering every blob.
SQLite-side exports (`pandas.read_sql`, :677) are rowid-ordered in
practice and have never churned — leave as-is.

## 4. Slice plan & gates

| Slice | Change | Tests | Per-slice gate |
|---|---|---|---|
| 1 (F1) | Extract `stable_prefix_replace` → `helpers/core/stable_write.py`; rewire `derive_events.apply()` | events: two applies → id/created_at byte-stable; stale-row deletion; duplicate-content multiset match; hand-seeded rows untouched; derive_insights alias parity | targeted pytest (derive_events + derive_insights + note-writers suites) |
| 2 (F2) | Guarded upsert in embeddings maint loop; B4 bump tied to real row changes | warm-cache second run writes 0 rows / no bump; perturbed text restamps exactly one row + one bump; GC path still bumps | targeted pytest (embeddings suites) |
| 3 (F3) | Canonical relabel in `onager.louvain_communities` | permutation invariance: shuffled edge order → identical labels dict; community count unchanged; existing louvain/graph tests | targeted pytest (onager + algorithms suites) |
| 4 (F4) | `ORDER BY id` map for e_* exports | export determinism: export → rebuild (no content change) → export again → byte-identical blobs; unordered default for unmapped tables | targeted pytest (snapshot suite) |
| Close | Live `make maint-full` once (absorbs the one-time canonical louvain relabel + normalized e_* order), `make snapshot`, verify zero-churn criterion, full gates ONCE (only on explicit go) | — | qa/integration/fuzz/advisory/perf + snapshot-check; then archive + completed.md entry |

Slices 1–4 are independent; land in order (1 is the extraction 3 others
don't depend on, but sequential keeps review simple). The close-out
snapshot refresh is a one-time expected churn of `graph_analytics.parquet`
(canonical labels) and the two e_* blobs (canonical order) — after it,
cycles are clean.

## 5. Out of scope

- Incremental derive_events (assessed NO 2026-08-21 — loses self-healing
  convergence; stable writes are the compatible alternative).
- Making the louvain *partition* order-independent (canonical relabel
  already removes the observable churn; the partition itself was
  deterministic on this corpus).
- SQLite-side export ordering (rowid-ordered, never churned).
- `graph_analytics.computed_at` semantics on genuine value change
  (see open questions).
- The DuckDB `built_at` date roll in `_build_meta` (inherent — a rebuild
  happened; only same-day cycles can be byte-stable, and they will be).

## 6. Risks

- **Extraction touches tested production code** (derive_insights). The
  thin private alias keeps its call sites and tests byte-identical; risk
  is confined to import plumbing.
- **Upsert WHERE-guard needs SQLite ≥ 3.35.** Verify the vendored/system
  version first; the read-compare-skip fallback is behaviorally identical
  and only costs one SELECT per company (~1 ms total at 1,065 rows).
- **B4 bump tightening changes invalidation behavior**: if any hidden
  consumer relied on miss-triggered bumps (not just real changes), it
  would see stale DuckDB. `_is_warm` exists precisely to detect
  content-staleness via generation; a no-change cycle leaving it warm is
  the desired outcome, and the #142/#143 test surface covers the
  warm/cold matrix.
- **One-time label renumbering on landing** for louvain communities —
  absorbed by the close-out snapshot; no known consumer hardcodes
  community numbers (Inspector/CLI read live values).
- **Multiset matching on 11 content columns** for events: a note prose
  tweak legitimately changes rows — that is a real change, correctly
  restamped. `properties` canonicalization (sort_keys) is already in
  place at the insert site.

## 7. Success criteria

1. `make maint-full` twice back-to-back on an unchanged corpus →
   second cycle leaves `git status` clean under `snapshots/parquet/`
   (same-day; `db_meta.generation` unchanged, `_build_meta.built_at`
   unchanged).
2. `derive_events --apply` twice → events table byte-identical between
   runs (id + created_at preserved).
3. Embeddings maint second run on warm cache: 0 row writes, 0 generation
   bump, log line "embed cache: N hits, 0 misses".
4. Shuffled-edge louvain test returns an identical labels dict.
5. Double-export determinism test for e_* blobs green.
6. Full gates green once, after explicit go (house etiquette).

## 8. Open questions

1. `graph_analytics.computed_at` never updates when a value genuinely
   changes (`algorithms.py:622` DO UPDATE sets only `value`). Restamp on
   change? Post-F3 this is cosmetic (louvain churn gone); leaning leave-
   as-is unless a consumer needs it.
2. Shared home for the helper: `helpers/core/stable_write.py` (proposed)
   vs. `helpers/core/db.py` next to `bump_generation`. Leaning the former
   to keep db.py connection-focused.
3. Should the F4 ordering map live in `snapshot_db.py` beside
   `MATERIALISED_TABLES` (proposed) or derive from DuckDB PK
   introspection? Leaning the explicit map — it is the
   snapshot-stray-table-trap lesson applied to ordering (new tables
   extend the manifest deliberately, in the same change).

## 9. Implementation log

- **R1 (F1 — events stable writes)** — `helpers/core/stable_write.py`
  created with `stable_prefix_replace` (body moved verbatim from
  derive_insights); derive_insights keeps a one-line private alias
  (`helpers/graph/derive_insights.py:914`); `derive_events.apply()`
  rewired (`helpers/graph/derive_events.py:479`) with
  `_EVENT_CONTENT_COLS`; module docstring updated. Tests:
  `test_stable_apply_preserves_id_and_created_at` +
  `test_stable_apply_removes_stale_derived_rows`
  (test_integration_derive_events_cli.py).
- **R2 (F2 — embeddings guarded upsert)** — the maint loop's
  `INSERT OR REPLACE` replaced with an `ON CONFLICT(company_name) DO
  UPDATE … WHERE embedding IS NOT excluded.embedding` upsert
  (helpers/graph/embeddings.py); B4 bump now keyed to actually-written
  rows (`if count or gc:`). Four existing tests updated to the new
  contract (warm re-run returns 0, "refreshed 0 row(s)", bump requires
  genuine byte change); new `test_warm_rerun_preserves_created_at`.
- **R3 (F3 — louvain determinism; scope grew, see deviation 1)** — three
  coordinated changes in `helpers/graph/onager.py`: (a)
  `_canonical_relabel` (order communities by -size, then smallest
  member) applied to both return paths of `onager_louvain`; (b)
  `ORDER BY src, dst` on the `_onager_e` materialisation (parallel scan
  order previously fed louvain a different edge order every run); (c)
  **fixed seed** — `onager_cmm_louvain(TABLE, seed BIGINT)` accepts a
  seed the calls never passed; without it the extension is
  non-deterministic run-to-run on the SAME graph (observed modularity
  0.3286–0.3322, community counts 21–24 across four consecutive runs;
  `SET threads TO 1` does not help). All calls now pass `seed => 42`.
  Verified 4/4 identical (labels + modularity 0.3279308605194091, 24
  communities) across fresh connections. New permutation-invariance +
  canonical-ordering test in test_onager_capabilities.py.
- **R4 (F4 — deterministic exports)** — implemented as
  `COPY (SELECT * FROM {t} ORDER BY ALL)` for every materialised DuckDB
  table (snapshot_db.py) rather than the proposed per-table id map —
  the e_*tables have no id column (their name-labeled columns hold
  v_node ids), and ORDER BY ALL covers the v_* projections too with
  zero manifest maintenance. Verify path is row-count based, so this is
  round-trip neutral. New double-export determinism test in
  test_snapshot.py.
- **R5 (unplanned fifth fix — rebuild_note_search full-branch bump)** —
  live cycle 2 showed the generation still bumping: maint-full calls
  `rebuild_note_search.py` with no flags (default FULL rebuild), whose
  B4 bump was unconditional (the incremental branch was change-guarded,
  the full branch never was). Fixed with a pre/post multiset compare
  (Counter over the 6 content columns) that gates the bump
  (helpers/maintenance/rebuild_note_search.py). Two traps en route:
  (a) `sqlite3.Row` objects never compare equal to plain tuples, so the
  first cut reported a phantom change every cycle — rows are now
  `tuple()`-normalized; (b) reusing the incremental path's `existing`
  variable tripped ty — renamed `existing_rows`.
- **R6 (close-out verification, 2026-08-22 21:19–21:34)** — four live
  `make maint-full` cycles on the unchanged corpus:
  cycle 1 absorbed the one-time canonicalisations (all 13 e_*+ 3 v_*
  blobs reordered by ORDER BY ALL; louvain relabelled); cycle 2 exposed
  the R5 bug (db_meta/_build_meta/graph_analytics churn); cycle 3
  absorbed the seeded-partition state + diagnostic drift (gen now
  25500, no bump in-cycle); **cycle 4: ZERO churn — all 38 blobs
  byte-identical** (md5-verified), parquet verify 36/36 OK. events and
  company_embeddings blobs were already byte-stable from cycle 1.
  Targeted tests: 350 passed across 19 suites; ruff + ty clean on all
  seven changed helpers.

### Deviations from the proposal

1. **F3's canonical relabel alone was insufficient** — the proposal
   assumed the partition was deterministic and only the label numbering
   permuted. Live measurement showed onager's louvain is non-seeded and
   returns different partitions run-to-run; the seed parameter (its
   existence discoverable only via `duckdb_functions()` — the pragma
   probe doesn't exist) is the real fix. Canonical relabel + input
   ordering remain as defense-in-depth. Trade-off accepted: the seeded
   partition's modularity (0.3279) sits ~0.4% below the best unseeded
   run observed (0.3322) — determinism chosen over restart-luck.
2. **F4 used `ORDER BY ALL` instead of a per-table id map** (no id
   columns exist; covers v_* too; see R4).
3. **A fifth writer needed the F2 treatment** (rebuild_note_search full
   branch, R5) — discovered only by the cycle-2 verification the
   proposal's success criterion forced.
