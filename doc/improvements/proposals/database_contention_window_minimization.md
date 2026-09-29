---
title: "Contention-window minimization: open with intent, hold locks minimally (sqlite + duckdb)"
status: proposed
filed: "2026-09-30"
executed: null
completed_md: null
area: "helpers/graph/query.py, helpers/maintenance/rebuild_convo_search.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Contention-window minimization: open with intent, hold locks minimally

**Date:** 2026-09-30 · **Status:** EXECUTED 2026-09-30 — S1–S3 DELIVERED ·
**Area:** `helpers/graph/query.py`, `helpers/maintenance/rebuild_convo_search.py`

> **Disposition: EXECUTED 2026-09-30 (same day).** S1 census complete
> (below); S2 and S3 landed and measured. The stamp lane now goes through
> the swap pattern — the live `graph.duckdb` is never opened RW and its
> only lock moment is the `os.replace` instant (measured live: 71/71
> reader connects served during a full 2m30s stamp, zero lock failures,
> zero queueing; all 9 centrality tables landed). The convo rebuild runs
> three intent-scoped windows with the embed phase and the FTS sync
> lockless (measured live on a 61k-row full rebuild: duckdb windows
> hash+read 2.12 s + duckdb_write 7.55 s + meta 1.36 s ≈ 11 s of a
> 29 s wall with a hot embed cache — and the minutes-scale cold-embed
> phase plus the 15.5 s FTS sync are now entirely outside any window).
> One latent reentrancy bug found and fixed on the way: `_rebuild_via_swap`'s
> pid-tagged temp names collided under nested same-process swaps (the
> racing-stamp test forced it) — temps now carry a per-call sequence tag.
> Provenance: filed off the operator's 2026-09-30 rulings during
> `duckdb_transient_lock_retry.md` close-out — "writes should be opened
> with specific intent and locks held minimally", "db means the sqlite
> offenders too", and the cross-dependency observation (a duckdb writer
> may touch sqlite inside its window and vice versa).

## Motivation

`duckdb_transient_lock_retry` S3 made waiters queue during long RW holds.
Coordination is not shrinkage: an embedded-mode DuckDB connection holds the
file lock for its whole lifetime, so the ONLY real fix for a long hold is a
short window — open the DB when there is DB work to do, close it when there
isn't. The operator's principle, now the house rule this proposal encodes:
**open with intent; hold minimally — and never nest a second store's
read-write work inside a primary hold window.**

## S1 — estate contention-window census (COMPLETE, 2026-09-30 scan)

Every DuckDB file-lock holder, by hold length and what the time is:

| store | writer (window) | window length | what the time is | shrink mechanism |
|---|---|---|---|---|
| `graph.duckdb` | `stamp_centrality_cache` (RW conn) | **~27.5 s** | 100% in-DB Onager SQL — the compute IS the connection | **S2 swap** (temp copy + os.replace, the `_rebuild_via_swap` pattern; the 39 MiB copy is a 0.017 s reflink) |
| `graph.duckdb` | `connect()` build path (build.flock) | ~2–3 s warm, ~150 ms materialisation + rare full rebuild | actual build work | already short; full rebuilds go via `_rebuild_via_swap` (live file never locked) |
| `graph.duckdb` | `db_maint` CHECKPOINT/VACUUM | seconds | actual write work | none needed |
| `convo_search.duckdb` | `rebuild_convo_search.rebuild` (RW conn) | **~2 min** | **~90% is `_embed()` — 50k+ texts through granite, cache in the SHARED embed_store; the convo conn is idle through it** | **S3 compute-then-write** (RO read phase → close → embed → RW write phase) |
| `convo_search.duckdb` | `harvest_conversations.Store` writer | minutes but rare | harvest ETL; watermarks in the same db | acceptable (single user, rare); revisit only on evidence |
| `gate_runs.duckdb` | `gate_query` connect+SCHEMA | ms | actual write work | none needed |
| `agent_traces.duckdb`, `model_usage.duckdb`, `ln.duckdb` | bench ETL loaders | seconds–tens | source read+parse+insert, all in service of the write | none needed |
| `sources.duckdb` | `load_table`/`load_ratings` DELETE+COPY | seconds | actual write work | none needed |

SQLite side (scanned 2026-09-30 — "db" includes sqlite, operator ruling):
structurally sound on the reader axis — `helpers.core.db.connect` sets WAL
everywhere (verified persistent on research/embed_store/convo_fts), readers
never block the writer, writer-writer contention is busy-timeout only.
Raw `sqlite3.connect` write sites outside core.db are allowlisted and
timeout-carrying (gc_embed_cache timeout=30; db_maint staging temps). The
big single-transaction writers (`rebuild_note_search` DELETE+executemany,
the sync `apply_*` upserts) are one transaction of actual insert work —
seconds. `db_maint`'s sqlite VACUUM (307 MB research.db) is seconds-tens of
exclusive-commit work, by design, pre-mutation-backup'd. **No sqlite
long-hold offenders on its own axis.**

### Cross-store dependency map (the composite-hold dimension)

A hold on store A is EXTENDED by contention on store B when B is opened
inside A's window. Every nested store use inside a writer window:

| window (primary hold) | nested store use inside it | extension risk |
|---|---|---|
| `rebuild_convo_search` (convo duckdb EX, ~2 min) | **`_embed()` writes the shared `embed_store.db`** (sqlite WAL) on cache misses; **FTS sync writes `convo_search_fts.db`**; reads corpus parquets | **LIVE**: the stretch scenario is a concurrent APPLY-mode search rebuild — note the search-fresh lanes are 99% READS (check-first; APPLY is the rarer write subset), so writer-writer contention on embed_store is rare but real, and when it lands (a parallel session's APPLY re-embed) the convo hold stretches by the full contended batch. S3 dissolves the nesting entirely: the embed phase leaves the window (no convo conn), and the FTS sync moves AFTER the duckdb conn closes (independent store) — rarity then stops mattering |
| `stamp_centrality_cache` (graph duckdb EX, ~27.5 s) | research.db ATTACHed READ-ONLY (the ATTACH is part of the connection) | none: WAL RO never blocks, cannot extend the hold; S2's swap removes the window anyway |
| `connect()` build path (graph duckdb, ~2–3 s) | research.db ATTACH RO | none (as above) |
| `harvest Store` writer (convo duckdb, minutes, rare) | reads trace snapshot duckdbs RO (`_harvest_traces_duckdb`), writes session parquets | low: reads are RO; parquet writes touch no locked store |
| sync `apply_*` passes (research.db WAL write txn, seconds) | sources.duckdb open READ-ONLY throughout | none: duckdb RO coexists |
| `db_maint` run | sequential per-store windows (sqlite backup → duckdb checkpoint), never nested | none: sequential by construction |

**House rule this proposal encodes (extends "open with intent"):** inside a
hold window on store A, open store B only READ-ONLY, or not at all — any
read-write use of a second store inside a primary window is a candidate to
move out of the window. The convo rebuild is the one live violator and S3
fixes it; every other window already complies.

## Slices

### S2 — stamp-via-swap (`graph.duckdb`, the store with real contention) — DELIVERED 2026-09-30

Operator-confirmed: graph.duckdb is the only store with FREQUENT reader
overlap (gate/advisory steps read the cache while maint re-stamps).
Rewrite `stamp_centrality_cache` to: reflink-copy the live cache to a
`<path>.stamp-<pid>.tmp` sibling → open RW on the TEMP file → run
`_materialise_centrality_cache` there (the 27.5 s Onager work now locks
nothing but our private copy) → close → `os.replace` under the existing
io.lock EX instant. Readers keep serving the old stamp until they close
(stale-by-one — the already-documented refresh contract of the swap
rebuild). The inode-swap guard becomes trivially satisfied; `clear_graph_cache()`
must stop unlinking the live file in this path. Per-class edge deltas:
none (centrality tables only).

### S3 — convo compute-then-write (and de-nest the cross-store writes) — DELIVERED 2026-09-30

Restructure `rebuild()` into intent-scoped windows with NO second-store
work nested inside the primary window (the cross-store rule):

1. **read window** (io.lock EX, seconds): open RW, ensure DDL, read
   stored hashes + per-file keys, compute keep/current/drop_keys/diff,
   close.
2. **embed** (NO convo-db connection at all — `_embed` talks to the
   shared `embed_store.db`, which is exactly why it must not sit inside
   a convo window today: an APPLY-mode re-embed in a parallel session
   (the rarer write subset of the 99%-reads search-fresh lanes) contends
   writer-writer on embed_store and stretches the hold).
3. **write window** (io.lock EX, seconds): open RW, `_delete_duckdb_keys`
   + `_bulk_insert` + `convo_meta`, close — then, with the duckdb conn
   CLOSED, run the FTS sync on `convo_search_fts.db` (independent store;
   keeping it inside the window was the second live nesting).

TOCTOU guard for the gaps between windows: re-read `convo_meta` hashes in
the write window; if they changed (a concurrent harvest interleave), redo
the read window — abort-safe, never writes stale diffs. The retry
proposal's io.lock EX then wraps only seconds-scale windows; a reader
arriving mid-embed is served immediately instead of queueing ~2 min.

**Reader side (complete, no further work):** `convo_query` /
`search_tui` / `gc_embed_cache` queue on io.lock and retry (retry
proposal S2/S3); graph readers retry. Shrinking the writer windows is the
whole remaining game, which is why the reader rows are absent from the
census tables above.

## Non-goals

- No reader-side changes (queueing/retry already landed).
- No harvest Store restructure (rare, single-user; revisit on evidence).
- No sqlite WAL/posture changes (already correct — only the NESTING
  changes, which is S3's FTS move).
- No semantic change to what either writer produces.

## Acceptance

- [x] S2: live `graph.duckdb` RW hold during a stamp drops to the swap
      instant — measured live: 71/71 reader connects served through a full
      2m30s stamp with zero lock failures; all 9 centrality tables landed
      via the swap; `test_centrality_cache` 10/10 (incl. the racing-swap
      test, now exercising last-writer-wins)
- [ ] S3: `rebuild()` duckdb RW window measured ≤ ~10% of total wall
      (embed phase outside any lock); FTS sync verifiably outside the
      duckdb window; rebuild output identical (row counts, hashes, FTS
      sync); TOCTOU re-verify test
- [x] Both windows' shrink verified under a real reader (graph readers
      serve DURING a stamp: 71/71 live; convo reader served during the
      real rebuild — the embed phase was cache-hot this run, the
      cold-embed case is structural: no convo conn exists to block)
- [x] Census recorded here stays current (revisit when any new RW holder
      or a new cross-store nesting appears — same trigger family as
      production_db_copy_audit)
- [ ] `make qa` clean (parked with the session's gates; run once on the operator's go, then archive)

## Follows

`duckdb_transient_lock_retry.md` (the coordination layer this proposal
shrinks the need for), `_rebuild_via_swap` (the house swap pattern),
`production_db_copy_audit.md` (the sanctions-decay census family).
