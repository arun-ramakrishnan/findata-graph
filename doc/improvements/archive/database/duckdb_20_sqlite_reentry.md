---
title: "duckdb_20_sqlite_reentry: gated SQLite retirement re-entry under 2.0"
status: deferred
filed: "2026-10-21"
executed: "2026-10-23"
completed_md: "431"
area: "helpers/core (db.py) + schema + app"
---

# duckdb_20_sqlite_reentry: gated SQLite retirement re-entry under 2.0

**Date:** 2026-10-21 · **Status:** DEFERRED 2026-10-23 (completed.md
entry 431) — gate evidence produced (B2 PASS, B1 sized); port not
attempted.

Part of the duckdb_20_* family, split from the bundled
`duckdb20_quack_variant` filing (2026-10-21). Siblings:
`duckdb_20_quack.md` (EXECUTED 2026-10-22 #428 — the direct unlock for
blocker #1, same directory here), `duckdb_vector_search_assessment.md`
(variant sibling, resolved DEFERRED 2026-10-22, same directory).
Upstream changeset:
<https://github.com/duckdb/duckdb/releases/tag/v2.0.0>.

## 1. TL;DR

The big open question ("dump SQLite, move to DuckDB") has a full
measured record: `doc/local/evaluations/duckdb_consolidation_assessment.md`
(2026-09-10, trial **terminated after B3** by operator decision, with
explicit re-entry points). This proposal carried the blocker list
forward into the 2.0 world and restated the re-entry gates; on
2026-10-23 the gate evidence was produced (B2 contention map **PASS**,
B1 port sizing **≈17–24 focused days across ~120 files**) and the
operator deferred the port: the measured cost exceeds the win at
today's scale. §3 carries the full map and sizing so a re-entry needs
no re-derivation.

## 2. What stopped the migration (verbatim from the record, re-verified on 2.0.0 2026-10-21; fts re-verified 2026-10-23)

1. **Multi-process writes (the crux) — DISSOLVED in principle,
   2026-10-22, CONFIRMED BY MAP 2026-10-23.** Search/embed lanes live
   on SQLite WAL multi-writer semantics (N Flask readers + rebuilder +
   parallel `--check` warmers); a consolidated DuckDB file allows one
   RW process. The quack arc (#428, `duckdb_20_quack.md` here) landed
   the server-per-store wire routing (Slice B waves 1 + 2: graph.duckdb
   + convo_search served over the wire, one RW server per store
   serialising writers; gates green) — the pattern is proven. The B2
   map (§3.1) then confirmed every research.db lane is livable under a
   single RW server, with one carve-out (note_search, §3.1.3).
2. **FTS5 regression, measured** (B3 EVAL, 16,479 rows): lexical
   recall@5 0.93 vs 0.85 (`match_bm25` defaults 1.2/0.75 in the trial,
   no column-weighted bm25, no snippet/highlight, no phrase syntax —
   "Coal India" drops out of DuckDB's top-5); hybrid RRF + bge vectors
   closes it to 1.00 = 1.00, warm 32.6 ms vs 10.4 ms. Verdict:
   migration must carry the vector leg; display parity
   (snippet/highlight at `app.py:872`) still unresolved. 2.0 fts
   recheck (BUNDLE P in `duckdb_improvs.md` here, 2026-10-23): the only
   2.0 change is `create_fts_index` gaining `incremental = 1`
   (trigger-based maintenance, needs NOT NULL id); `match_bm25` is
   byte-identical to 1.5 — `k`/`b`/`conjunctive` were already exposed
   there (the record's "fixed params" was an overstatement; the
   defaults were never hard-coded) — and snippet/highlight, phrase
   syntax, per-field bm25 weighting are all still absent → Alternate B
   stands (FTS5 stays SQLite permanently as the lexical engine; vectors
   move to DuckDB — brute-force parity proven). This blocker is now
   VERIFIED-PERMANENT, not merely open. *(2026-10-30 probe: the gap
   narrowed further — `match_bm25` accepts per-field `fields:=`
   scoring scope and `create_fts_index` accepts `stemmer=`/
   `stopwords=`/`strip_accents=`/`cluster_terms=`/`layered_search=`
   on 2.0.0; snippet/highlight/phrase/wildcard remain absent. Full
   note: `duckdb_vector_search_assessment.md` §6 trigger 3. Verdict
   unchanged.)*
3. **FK CASCADE still does not port** (re-verified 2.0.0:
   `ON DELETE CASCADE/SET NULL/SET DEFAULT` parse-rejected; plain FK
   enforced RESTRICT-only). Live `research.db` cascades (graph_edges →
   entities) + `db.py` `PRAGMA foreign_keys = ON` mean entity
   renames/moves/deletes become explicit ordered rewrites. The 2026-
   10-23 census counted **20 FK actions ≠ NO ACTION** in the live
   schema (14 CASCADE, 6 SET NULL), entities-rooted, ≤2 hops deep.
4. **Port cost — WAS unsized, NOW SIZED (§3.2):** ≈17–24 focused days
   across ~120 files in 6 phases. The 2026-10-23 census also corrected
   the old "44 modules" figure: **105 modules** import
   `helpers.core.db` today, with **391** `connect()` call sites outside
   db.py (majority mechanical RO reads) and **62** files carrying
   INSERT/UPDATE/DELETE (the true write-port review set).
   `corpus.db` (55 MB) is a fourth datastore needing an explicit
   fold-or-stay call (**STAY** — derived load-cache, zero integrity
   surface, §3.4); `embed_store.db` (437 MB, SQLite+WAL, attached as
   schema `vecdb` by `helpers/core/vec_search.py` `_attach_vec_db`) is
   live data, not bloat (shrink-first recovers ~10%) — the quack arc's
   "wave 3 embed_store" was a NO-OP precisely because it is SQLite, not
   DuckDB, so consolidation would also port its sqlite-vec vector leg
   to DuckDB vss (Alternate B already assumes vectors-on-DuckDB, which
   softens this) (**STAY under this proposal** — own decision, §3.4).

## 3. Gate evidence (2026-10-23 preflight — full detail)

Working file with the same content:
`doc/local/evaluations/duckdb_sqlite_reentry_preflight.md` (kept as the
scratch census; this section is the canonical record).

### 3.1 B2 — static contention map: PASS

Ground truth about today's concurrency (from `helpers/core/db.py`,
457 LOC, the sole sanctioned entry):

- Every connection gets `PRAGMA foreign_keys=ON`, `journal_mode=WAL`,
  `busy_timeout=5000`, `synchronous=NORMAL`, 80 MB page cache, 256 MB
  mmap. WAL ⇒ readers never block; the single-writer lock + 5 s busy
  queue ⇒ **writers already serialize today**. The module comment names
  the real overlap: `parse_newsletter` spawns `sync_tags` while the
  parent still holds a write connection — they queue, not crash.
- Cross-process `io_lock`/flock discipline (db_maint, snapshot_db,
  harvest, index_sync, store_sweep) guards the **DuckDB cache
  rebuild**, not research.db — research.db relies on SQLite's own
  locking only.
- The old crux — "N Flask readers + rebuilder + parallel `--check`
  warmers as simultaneous writers" — applies to the **search sidecars**
  (`doc_search.db` etc.), out of Alternate A's scope. On research.db
  itself, `app.py` has **zero direct write statements** (rg: no
  INSERT/UPDATE/DELETE in app.py): Flask is a pure reader + occasional
  `connect()` RW holder; all canonical writes are CLI one-shots.

Lane inventory (research.db):

| Lane | Access | Today | Under 1 RW server | Verdict |
|---|---|---|---|---|
| Flask app (app.py) | RO reads, per-request `connect()` | WAL parallel readers | wire reads multiplex | LIVABLE |
| search_tui / note_query | RO (`wal=False`, mode=ro) | WAL parallel readers | wire reads (≈5 ms) or file-RO when unserved | LIVABLE |
| sync_tags / coverage / sector-links | RW, rebuild-from-YAML | busy-queue | wire write (serialized) | LIVABLE |
| derive-* family (cited_in, co_mentions, events, participants, org_membership, merger, hyperedges, indices, insights) | RW, batch | CLI one-shot, run singly | wire write | LIVABLE |
| extract_relations / embeddings / name_dedup / speaker_resolution | RW | busy-queue | wire write | LIVABLE |
| maintenance syncs (exchange, shareholding, mca_cin, wikidata, related_party, enrich_*, fold_identifiers, move_sector, rename_entity, retire_sector_dyads, ticker_hygiene) | RW | operator-invoked one-shots | wire write | LIVABLE |
| rebuild_note_search | RW — **FTS5 `note_search` INSIDE research.db** (17,497 rows) | busy-queue | wire write — but the TABLE cannot move (B3) | LIVABLE lane, BLOCKED payload → carve-out |
| triage quotes/relations | **no DB writes** — journals to `findata/Misc/*_decisions.jsonl`; `--apply-decisions` is a separate batch pass | n/a | n/a | LIVABLE |
| db_maint (VACUUM/ANALYZE/REINDEX/integrity, backup pairs) | RW maintenance | sequential in `make maint` | redesign → CHECKPOINT + first-party checks | LIVABLE after redesign |
| snapshot_db (parquet + zstd, 4-worker ThreadPool per store) | RO read + sidecar write | serialized vs writer via WAL snapshot | per-store workers keep shape; SQLite half becomes DuckDB export | LIVABLE after port |
| gates (qa/integration/live-invariants) | RO + fixture DBs | batch window | unchanged | LIVABLE |

**Verdict: PASS — single-writer-per-store is livable on every lane**,
with ONE structural carve-out that any port must size first:
`note_search` (FTS5) lives *inside* research.db, and B3 is
VERIFIED-PERMANENT (lexical parity lost on DuckDB fts). Alternate A's
premise "search sidecars never read research.db" is therefore false
today for note_query/search_tui, which read `note_search` from
research.db RO — the note_search table must be **split into its own
sidecar** (or folded into `doc_search.db`) and stay SQLite-FTS5
permanently BEFORE any merge.

### 3.2 B1 — port sizing (Alternate A: research.db → DuckDB only)

Measured surface (2026-10-23): 105 modules import `helpers.core.db`
(incl. app.py, Mojo/bench, doc templates); 391 `connect(` sites outside
db.py; 62 files with INSERT/UPDATE/DELETE among db users; 114 PRAGMA
call sites; 20 FK actions ≠ NO ACTION (14 CASCADE, 6 SET NULL);
research.db 333 MB / 32 tables; note_search FTS5 17,497 rows inside it.

Phase plan (focused-day estimates):

1. **note_search carve-out** (prerequisite): split FTS5 table +
   rebuild/check lanes into `doc_search.db` (or a new sidecar);
   retarget note_query/search_tui/snapshot/integrity. **1.5–2 d.**
2. **Engine swap in db.py** (457 LOC): `connect()` → wire funnel
   (`duckdb_shared.connect_shared`), drop WAL/pragma block, row-factory
   shim, `user_version`/`db_meta` → meta table, 32-table DDL port
   (FKs become plain RESTRICT — no CASCADE/SET NULL in DuckDB 2.0).
   **3–5 d.**
3. **Cascade-dependent flows → ordered rewrites**: rename_entity,
   move_sector, store_sweep, exchange_sync, name_dedup,
   retire_sector_dyads, fold_identifiers, build_sector_hierarchy,
   migrate_to_graph_edges (+ tests). FK graph is entities-rooted and
   shallow (≤2 hops). **3–4 d.**
4. **Maintenance redesign**: db_maint VACUUM/ANALYZE/REINDEX →
   CHECKPOINT (no analogs in DuckDB); integrity gate
   (`foreign_key_check` ×6, `integrity_check`/`quick_check` ×4 sites)
   → first-party orphan scans covering the 20 FK edges + row-count
   parities; snapshot_db SQLite half → DuckDB export/verify. **3–4 d.**
5. **Consumer sweep**: ~330 mechanical RO sites → funnel; 62 writer
   files reviewed for transaction idioms (`isolation_level=None`
   autocommit patterns in db_maint/backup paths), `PRAGMA table_info`
   (25 sites) → `information_schema`. **4–6 d incl. test updates.**
6. **Cutover**: EXPORT/IMPORT (or `COPY FROM DATABASE`), storage-
   version check, quack server enrollment for the research store, full
   gates arc. **2–3 d.**

**Total ≈ 17–24 focused days across ~120 files**, with the integrity
surface (phase 4) carrying the highest regression risk — it is exactly
what `make qa`/gates.md protect.

PRAGMA census → disposition (114 sites): `table_info` ×25 →
information_schema; `foreign_keys` ×11 + `defer_foreign_keys` ×2 →
delete / reorder writes; `user_version` ×10 → meta table;
`auto_vacuum` ×8, `incremental_vacuum` ×2, `freelist_count` ×4,
`page_count` ×4, `wal_checkpoint` ×3, `journal_mode` ×5,
`journal_size_limit` ×2, `busy_timeout` ×2, `page_size` ×1 → DELETE
(no DuckDB analog — a maintenance simplification, i.e. an ops win);
`optimize` ×6 → delete; `foreign_key_check` ×6 + `integrity_check` ×2
+ `quick_check` ×2 → **first-party checkers** (the real work);
`index_list/index_info/index_xinfo` ×5 → information_schema.

### 3.3 Ops win ledger (the "<" right-hand side)

Wins: one SQL engine for the canonical store (drops the SQLite/DuckDB
dialect drift: json_extract variants, type coercion, PRAGMA schema
introspection); research store joins the quack wire (N-process readers,
serialized writers, token auth — pattern proven on graph + convo
stores); `-wal`/`-shm` sidecars gone; VACUUM/REINDEX/ANALYZE scheduling
gone (CHECKPOINT only); unified per-store snapshot/backup shape.
Costs: the 17–24 d port; loss of SQLite-specific tuning (80 MB cache,
mmap, busy_timeout semantics); the FTS5 split adds a 5th store to
maintain; every gate touching integrity/snapshot needs redesign before
it can be trusted again; the DuckDB rw-file-lock reality means an
unserved window is required for bulk loads (already the norm).

### 3.4 Fold-or-stay calls

- `corpus.db` (55 MB): **STAY** — derived load-cache for the 1,243-file
  walk, zero integrity surface, regenerable; revisit only if it ever
  joins a query path.
- `embed_store.db` (437 MB, SQLite+WAL, vecdb-attached): **STAY** under
  this proposal (vectors-on-DuckDB remains Alternate B's assumption
  whenever that opens).
- `note_search`: **SPLIT** (new sidecar or fold into doc_search.db) —
  forced by §3.1.

### 3.5 Storage-posture note

Existing DBs keep the ≤1.5 storage version under 2.0 writes, so the
1.5.6 pin fallback stays valid until the first conversion
(`doc/improvements/completed.md` #404 §storage). Any merge here is a
conversion point: before it, verify every consumer venv is ≥ 2.0;
conversion via `EXPORT`/`IMPORT DATABASE` or `COPY FROM DATABASE`, with
`-storage-version v1.5.6` compatibility writes while any 1.x process
remains.

## 4. Gate outcome → DEFERRED

hybrid-attached accepted ✓ (B3) + single-writer livable (B2, **PASS**
2026-10-23) + port cost vs ops win (B1, **SIZED** 2026-10-23:
≈17–24 focused days / ~120 files vs the §3.3 win ledger). With the
evidence complete, the operator judged the cost to exceed the win at
today's scale and **deferred** the port (2026-10-23, completed.md
entry 431) rather than scheduling it. Nothing was merged; `db.py` and
research.db are untouched.

**Reopen triggers** (any one re-opens this proposal as `proposed`):

- The DuckDB rw-file-lock / single-RW-server model starts to bite a
  real lane the map did not forsee (i.e. research.db contention shows
  up in practice).
- DuckDB ships CASCADE/SET-NULL FK actions AND a first-party
  integrity-check surface, collapsing phases 3–4 of the sizing.
- Alternate B (vectors-on-DuckDB) opens for embed_store and a
  unified-storage push follows it.
- A new lane needs cross-store joins between research.db and a DuckDB
  store that the hybrid attachment cannot express.
