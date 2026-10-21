---
title: "Production-store copy census re-run — the doubling trigger has fired on two stores"
status: executed
filed: "2026-10-18"
area: "helpers/maintenance"
executed: "2026-10-19"
completed_md: "415"
---

# Production-store copy census re-run — the doubling trigger has fired on two stores

**Date:** 2026-10-18 · **Status:** EXECUTED 2026-10-19 (completed.md #415)

## 1. TL;DR

The production-store copy census (`production_db_copy_audit.md`, armed
2026-09-29) carries a revisit trigger: "re-run the census when any
production store roughly doubles — a sanction is sized against the data
of its day and decays silently." The 2026-10-13 measurement already
flagged convo_fts at 2.28× and embed_store at 1.69×. Live sizes today
put **both past 2×** the filing sizes — embed_store 383 MB (2.07×) and
convo_fts 257 MB (2.52×) — and the census re-run is still owed. The
2026-10-14 dead-page reclaim (#391) reclaimed ≈84 MiB but, by its own
§F5 record, "reclaim frees pages, never shrinks payloads": the trigger
stays armed on payload growth, which is exactly what happened.

## 2. Evidence (measured 2026-10-18, live `du` vs filing)

| store | filing (MB) | 2026-10-13 | live now | ratio |
|---|---|---|---|---|
| convo_fts | 102.0 | 232.5 (2.28×) | **257** | **2.52×** |
| embed_store | 184.6 | 311.7 (1.69×) | **383** | **2.07×** |
| graph | 39.3+39.0 | 48.0+48.5 | 53+48 | 1.35× |
| convo_search | 820.5 | 841 | 880 | 1.07× |
| research | 307.2 | 313.7 | 318 | 1.04× |
| corpus | 52.6 | 27.2 | 54 | 1.03× |
| doc_search | 24.7 | 31.8 | 29 | 1.17× |

**Growth drivers recorded at filing**: convo_fts/embed_store scale with
the harvest+embed lanes (gemma cohort rows). Live corroboration:
`embed_cache` holds **139,833 rows** against a 17,493-row
`note_search_vec` — the cache out-populates the indexed corpus ~8×.

**Sanction history** — the keep_all class is the amplifier on record:
copies sanctioned as small became "307 MiB × 30 per gate run"
(`production_db_copy_audit.md`); the chokepoint is now
deny-by-default (`copy_production_db(requestor=...)` +
`SANCTIONED_REQUESTORS`, static check 1:1).

## 3. Slices

- **S1 Census re-run** — the three outstanding measurements per the
  pending.md wording: size per lane (which cohort/lane owns which
  fraction of embed_store/convo_fts), per-run transient (what a gate
  run actually copies today), backup-lane cost.
- **S2 Verdict-table refresh** — update
  `production_db_copy_audit.md` sanction rows against live sizes;
  anything above its sanctioned size without a recorded waiver gets
  one or gets shrunk.
- **S3 Trigger formalization** — the current "roughly doubles" trigger
  is prose. Land the measured-ratio check alongside the existing
  store-size probes (a `du`-based advisory line in `maint`, warn at
  1.5×, act at 2×) so the next doubling files its own finding instead
  of waiting for a session to notice.

## 4. Acceptance

- Verdict table updated with live numbers; zero stores above sanction
  without a row.
- S3 advisory is read-only and off-gate (advisory lane), measured cost
  ≈0.1 s-class like the store sweep.
- Targeted: db_maint/store-sweep tests for the new advisory line.

## 5. Risks & non-goals

- No store deletions in this arc — re-sizing sanctions is a verdict
  change, not a cleanup; any keep_all re-class follows the same
  deny-by-default chokepoint.
  [2026-10-19 addendum: SUPERSEDED in part — the operator's lane ruling
  retired the convo_search FTS backup (see §7); no store was deleted.]
- embed_store growth is a *lane* decision (cache cohort policy), not a
  copy decision — the census measures it; the cohort policy belongs to
  the embed lane owners.

## 6. References

- `doc/improvements/archive/testing/production_db_copy_audit.md`
  (filing + verdict table), `doc/improvements/pending.md:20-38` (the
  armed trigger + 2026-10-13 measurement), `store_wide_reclaim.md` §F5
  (reclaim ≠ payload shrink), `pending_improvs.md` row 9 (prior
  NO-ACTION, now superseded by measurement).

## 7. Arc addendum — 2026-10-19 (executed same arc)

**S1 verdicts (measured live):** embed_store 2.08× and
convo_search_fts 2.51× past the doubling trigger; all other stores
≤1.35×; per-run transient **7 MB** pytest-of — the 2026-09-29 S2/S3
mechanisms held (structural, size-insensitive); backup lane
1,465 MiB = 1.96× of the post-S4 749 MiB figure. Both fired stores are
dense payload (embed_cache 139,833 rows vs 17,493 indexed notes;
harvest-lane rows) — freelist 0.0% on both, nothing left to reclaim.

**S3 landed:** `size_watch()` + `_FILING_MB` baseline (warn 1.5× / ACT
2.0×) in `helpers/maintenance/store_sweep.py` — advisory-only, wired
into `main()`, primaries excluded (db_maint-owned); no-baseline stores
get one prompt line; 2 targeted tests; live dry-run validated.

**The ruling story — recorded so future readers don't re-litigate it.**
The arc's first pass classified embed_store as "derived ⇒ droppable"
and stripped its backup from the db_maint lane. **The operator reversed
this**: the embed-store backup exists as *downtime-window insurance* —
it rescued multiple embed-cache blowout incidents at 2-5 h of re-embed
work each. Size was never the design factor; the disruption window was.
Standing lesson (session doctrine): a backup's rationale is recovery
time, not storage size — ask WHY a thing was built before proposing its
removal.

**Final code change (operator ruling 2026-10-19):**

- `convo_search_fts` backup RETIRED from the db_maint lane
  (`_backup_convo_search`): derived lookup index, zero unique content,
  minutes-class mechanical re-index from the corpus tar
  (convo_search_swap_rebuild #355) — −79 MiB/run. The snapshot lane
  never carried the convo FTS index (its FTS references are the vault
  `note_search` shadow, unrelated). Pinned by
  `test_backup_convo_search_writes_primary_not_fts_sidecar`.
- `embed_store` backup STAYS on both legs (db_maint twin +
  snapshot-lane `_snapshot_embed_store`); its 2.08× growth is
  explicitly SANCTIONED — the premium for skipping a 2-5 h disruption.
- convo_search primary backup stays (vectors included).
- Validation: 68/68 targeted green (db_maint + convo + snapshot
  suites), ruff clean; qa 12/12 PASS earlier in the arc.

**Amended acceptance:** S2 verdict rows live in the audit doc's
2026-10-19 addendum; S3 landed as above; the §5 non-goal is superseded
per the bracketed note.

## 8. Addendum — db-backup tenant census (2026-10-19, recorded for future work)

A follow-up pass mapped EVERY tenant of `/mnt/data/findata-graph/db-backup/`
(18 artifacts, 1.5 G) and found **three distinct lanes**, not one:

1. **db_maint sidecars** (`db_maint.py:901-907`): research + embed +
   convo_search + convo_corpus + memory_sidecars + corpus + graph
   (`duckdb_backup_path`, `db_maint.py:786-795`) — plus the RETIRED
   convo FTS artifact, still on disk (76 M, mtime 2026-10-17, nothing
   rewrites it — dead file).
2. **Self-backing derived indexes**: `rebuild_doc_search` (:577),
   `rebuild_script_search` (:1093), `rebuild_memory_search` (:485) each
   write a last-good backup on every successful refresh via
   `rebuild_common.backup_last_good_index`; `agent_traces.py:1532`
   `_refresh_backup` does the same for the traces store.
   `model_usage_backup.duckdb.zst` (76 K) has **no writer in the repo**
   — writer unidentified.
3. **Snapshot-lane twins** (`research/embed_store/graph/corpus.snapshot.*`,
   D3 mtime-skipped) — the gate-consistency lane, out of scope here.

**Tenant table** (sizes 2026-10-19; verdict under the operator's
downtime-window doctrine — *size was not the factor but the downtime
window*):

| artifact | size | rebuild story | verdict |
|---|---|---|---|
| research_backup.db.zst | 79 M | none — the primary | KEEP |
| embed_store_backup.db.zst | 235 M | 2-6 h cold re-embed | KEEP (sanctioned, §7) |
| convo_search_backup.duckdb.zst | 463 M | vectors, hours-class | KEEP (§7) |
| convo_corpus_backup.tar.zst | 71 M | **no rebuild path** — harnesses purge their own logs; corpus is the archive of record | KEEP |
| memory_sidecars_backup.tar.zst | 90 M | unregistered memory/ files, no other copy | KEEP |
| corpus_backup.db.zst | 8.9 M | corpus cache is its only copy of note text | KEEP |
| graph_backup.duckdb.zst | 36 M | derived from research.db — materialization 2.6 s + stamp/fold chain ≈ minutes | **candidate** ⚑ ask-why (predates arc; likely paired-restore convenience) |
| convo_search_fts_backup.db.zst | 76 M | retired lane (§7); stale artifact | DEAD — deletable |
| doc_search_backup.db.zst | 19 M (stale 10-13) | search-fresh family: seconds-warm | **candidate** |
| script_search_backup.db.zst | 2.2 M (stale 10-12) | same | **candidate** |
| memory_search_backup.db.zst | 1.1 K | same | **candidate** |
| agent_traces_backup.duckdb.zst | 30 M | reload from memory/data/telemetry_backup sources | **candidate** ⚑ verify source chain first |
| model_usage_backup.duckdb.zst | 76 K | writer unknown | ⚑ identify first |

**Finding, not action:** Lane B is a whole design class (five scripts
self-backing seconds-to-minutes-rebuildable indexes whose sources are
always present) that fails the downtime-window test. Under the arc's
doctrine these are candidates for removal (~52 M + the 76 M dead FTS
file). **Nothing was removed** — per the standing lesson (ask WHY a
thing was designed before proposing removals), the two ⚑ flags need the
operator's design knowledge first: (a) why db_maint carries a paired
graph recovery point when it mutates only research.db; (b) who writes
model_usage_backup. A future slice — *backup-lane tenant pruning* —
picks this up with the rulings recorded here.

## 9. Arc-1b addendum — the WHY findings (2026-10-19, queries before removals)

Both ⚑ flags resolved via git_query / convo_query / doc_query; verdicts
updated accordingly. **No embeddings among the candidates** — verified:
`doc_search`/`script_search`/`memory_search` backups contain pure FTS5
shadow tables (no vec/embed); the only embedding stores are
`embed_store.db` (sanctioned KEEP, §7) and `note_search_vec` (inside
research.db, rides the primary).

**(a) graph_backup — rationale FOUND, recorded in the code:**
`db_maint.py` module docstring: *"`research_backup.db.zst` /
`graph_backup.duckdb.zst`: recovery if VACUUM corrupts (rare but
possible; overwritten each run, not versioned)"*; `_backup_duckdb`
(:785-801) is a **pre-mutation** recovery copy — db_maint's chain is
db_maint → snapshot_db → graph-rebuild (`make maint`), so during the
window the cache's rebuild source (research.db) is itself mid-VACUUM.
The backup covers exactly that race. 36 M, rebuilt every run.

**(b) model_usage_backup — writer OUTSIDE the repo; store is
partially primary:** the loader is operator-side tooling (store and
backup mtimes move together — 2026-10-18 19:46); `bench_data/code/`
and `helpers/` write neither. Substantively: `memory/data/` is coverage
doctrine — *"'at bare minimum everything under memory/ and memory/data/
belongs in db-backup/' — Operator thesis, 2026-09-27"* (`db_maint.py:657-660`,
implemented as bespoke routines because engines need WAL-consistent /
checkpointed copies). And the store's sources PURGE:
`doc/local/notes/model_usage.md:116-130` (RLM daemon rotates sessions;
Atria rows unique to session JSONLs) — accumulated rows are
irreplaceable. This is the `convo_corpus` class, not the FTS class.

**Verdict table (operator-ruled 2026-10-19, EXECUTED same day):**

| candidate | WHY it exists (found) | ruling → action |
|---|---|---|
| doc_search / script_search / memory_search backups | coverage thesis (2026-09-27) | RETIRE → backup steps + `rebuild_common.backup_last_good_index`/`IndexBackupSpec` + `BACKUP_DIR` constants removed; artifacts deleted; BACKUP_DIR monkeypatch sweep across 8 test files (187/187 targeted green) |
| dead `convo_search_fts_backup.db.zst` | §7 retirement's last output | DELETED (−76 M) |
| graph_backup | pre-mutation race insurance (docstring) | KEEP — recorded rationale stands; removal win negligible |
| agent_traces_backup | refresh-on-load, `agent_traces.py:1532` | **KEEP — operator ruling** (telemetry-source chain too valuable) |
| model_usage_backup | coverage thesis + purge-prone sources | KEEP — convo_corpus class (irreplaceable rows) |

Net: db-backup/ 18 → 14 artifacts, ~98 M reclaimed; every remaining
tenant carries either unique content or a recorded pre-mutation /
downtime-window rationale. The 2026-09-27 coverage thesis is amended
exactly where the §7 FTS precedent pointed: derived, zero-unique-content,
mechanical-rebuild indexes no longer self-back.
