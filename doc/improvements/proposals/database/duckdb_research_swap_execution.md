---
title: "Execute the research.db SQLite→DuckDB swap from the de-risked readiness program"
status: proposed
filed: "2026-11-19"
executed: null
completed_md: null
area: "helpers/core/db.py + schema + app + consumer sweep"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->

**Date:** 2026-11-19 · **Status:** PROPOSED ·
**Area:** `helpers/core/db.py` + `schema/duckdb/` + `app.py` + maintenance
+ full consumer sweep

> **Follows:** `doc/improvements/archive/database/duckdb_research_swap_steps.md`
> (Steps 0–8 EXECUTED 2026-11-17, completed.md #533; **swap execution
> PARKED/DEFERRED**), `doc/improvements/archive/database/duckdb_20_sqlite_reentry.md`
> (#431), `note_search_carve_out.md` (#530), `duckdb_sidecar_fold.md` (#524),
> `duckdb_rebuild_incremental_carry.md` (#528), `duckdb_watchlist_hardening.md`
> (#532). This is the **re-file** the readiness proposal required on unpark:
> "when unparked, the execution must be re-filed as a new proposal carrying
> the eval-gate bullet (§4) and lifting this program's phase plan and
> de-risked precedents from the Step 8 status table."

## 1. Motivation

The canonical `memory/research.db` is the last production store still on
SQLite. Every surrounding store has moved to DuckDB-native: the five search
sidecars (#524), the embed cache (`dee6527507`), and the last in-store FTS5
index (`note_search` carve-out, #530). The swap itself was sized
(**≈17–24 focused days / ~120 files**) and **deferred 2026-10-23** because
cost exceeded the win at that scale; the 2026-11-15 re-open swept the
prerequisites, and the readiness program (Steps 0–8, #533) converted a
snapshot, drafted the 38-table DDL + cascade manifest, prototyped the rewrite
flows and FK checker, and shipped the engine funnel — **without writing any
production store**. What is left is the port and cutover itself.

The operator has now unparked the execution. This proposal files it with the
mandatory query-visible-semantics eval gate (§4), freezes a **fresh dated
census as the baseline** (§2, measured 2026-11-19), and lays out each slice
independently landable. The win is one SQL engine for the canonical store
(drops SQLite/DuckDB dialect drift, joins the quack wire for N-process
readers with serialized writers, removes `-wal`/`-shm` sidecars and
VACUUM/REINDEX scheduling, unifies snapshot/backup shape) — cost ledger in
reentry §3.3.

**Trigger:** operator unpark directive 2026-11-19 (session, after the
"look at our preparation to move from sqlite to duckdb" review).

## 2. Evidence — baseline census, measured 2026-11-19 (this box)

All numbers below are fresh measurements on the live tree, not carried from
the readiness doc. The machine-readable census instrument is
`helpers/misc/swap_prep_census.py` (advisory, read-only, deterministic);
`--json` emits the structured form. The §2.2 baseline is the frozen
comparison point for every later checkpoint — **do not silently re-baseline**.

### 2.1 Store footprint

| Metric | Value | Method |
|---|---|---|
| `memory/research.db` size | **363,528,192 B (346.6 MiB)** | `os.path.getsize` |
| page count / page size | 44,376 / 8,192 B | `PRAGMA page_count/page_size` |
| freelist pages | 309 | `PRAGMA freelist_count` |
| journal mode | wal | `PRAGMA journal_mode` |
| `user_version` / `db_meta.schema_version` | 17 / 17 | PRAGMA + meta key |
| foreign_keys pragma (raw connect) | 0 (set ON by `helpers.core.db.connect`) | `PRAGMA foreign_keys` |

### 2.2 Schema object census (read-only `sqlite_master`)

| Object | Count | Note |
|---|---:|---|
| tables (excl. `sqlite_*`) | **38** | includes FTS5 rollback artifacts |
| views | **3** | `company_metrics_ruled`, `relations`, `v_quote_speaker` |
| indexes (user) | **23** | one NOCASE (`idx_entities_name_nocase`) |
| triggers | **16** | 10 `agent_id`-registration (ABORT), 6 generation bump |

### 2.3 Per-table row counts (frozen baseline — full 38 tables)

| Table | Rows | Table | Rows |
|---|---:|---|---:|
| graph_analytics | 203,637 | entity_identifiers | 6,796 |
| graph_edges | 86,931 | hyper_edges | 4,636 |
| company_metrics | 79,395 | event_participants | 2,705 |
| hyper_incidences | 59,657 | concepts | 2,517 |
| hidden_connections | 41,328 | events | 1,572 |
| ultimate_parent | 25,200 | note_search_meta | 1,398 |
| note_search (FTS5) | 19,356 | speaker_identity | 1,419 |
| note_search_content | 19,356 | nic2008 | 1,301 |
| note_search_docsize | 19,356 | company_embeddings | 1,209 |
| entities | 27,751 | note_tags | 1,429 |
| ppr_results | 4,924 | co_mention_editions | 1,644 |
| entity_tags | 7,417 | graph_analytics_contract | 5,193 |
| quotes | 8,848 | concept_mappings | 510 |
| competitive_overlap | 8,160 | provenance_agents | 24 |
| note_search_idx | 6,101 | db_bump_log | 24 |
| note_search_data | 10,288 | concept_schemes | 12 |
| entity_gf_map | 2 | derived_cohort_meta | 4 |
| entity_ticker_status | 7 | supply_chokepoints | 9 |
| note_search_config | 1 | db_meta | 6 |
| db_meta (key rows) | — | (see §2.1) | — |

> **FTS5 rollback residue:** `note_search` + its 4 shadow tables
> (`note_search_content`/`_data`/`_docsize`/`_idx`) + `note_search_config`
> remain in `research.db` as a **documented rollback artifact** after the
> #530 carve-out (live index is `memory/note_search.duckdb`). Step 2's DDL
> mirrors them as plain tables with a rollback comment; the cutover must
> decide carry-vs-drop explicitly (§5 risks).

### 2.4 FK census

- **24 `REFERENCES` links across 15 tables**; **22 action clauses across
  14 tables**: 15 × `ON DELETE CASCADE`, 7 × `ON DELETE SET NULL`,
  21 × `ON UPDATE CASCADE` (the reentry doc's older 14/6 split was
  corrected by the Step 0 instrument; this is truth).
- Parent set is `entities.name` (dominant), `provenance_agents.agent_id`,
  `events.id`, `hyper_edges.id`, `concept_schemes`, self-ref `concepts`.
- The cascade manifest (`findata/Misc/duckdb_research_swap_cascade_manifest.json`,
  22 rows) assigns rewrite-flow owners: `store_sweep` 5 (agent_id edges),
  `name_dedup` 5, `migrate_to_graph_edges` 4, `rename_entity` 3,
  **TBD 5** (graph_analytics, graph_analytics_contract, ppr_results,
  speaker_identity, event_participants→events). DuckDB 2.0.0 parse-rejects
  CASCADE/SET NULL — all 22 become RESTRICT + ordered code rewrites.

### 2.5 Consumer surface census (`rg` over `helpers/`, self-excluded)

| Metric | 2026-10-23 | **2026-11-19** | Trend |
|---|---:|---:|---|
| PRAGMA sites | 114 | **161** | +47 |
| `connect(` sites | 391 | **491** | +100 |
| writer files (INSERT/UPDATE/DELETE/UPSERT) | 62 | **93** | +31 |
| modules importing `helpers.core.db` | 105 | **120** | +15 |
| raw `sqlite3.connect` sites | — | **44** | allowlist-enforced |
| `duckdb.connect` sites | — | 117 | already DuckDB lanes |
| `research.db` path references | — | 291 | CLI defaults + tests |

PRAGMA sites by kind (the disposition set): `table_info` 33, `user_version`
12, `foreign_keys` 11, `create/drop_fts_index` 19, `auto_vacuum` 8,
`foreign_key_check` 7, `optimize` 6, `database_list` 6,
`journal_mode` 5, `page_count`/`freelist_count` 4 each,
`wal_checkpoint` 3, `integrity_check`/`quick_check` 2 each,
`foreign_key_list` 4, `defer_foreign_keys` 2, `index_*` 4, rest ≤1.

Highest-churn files: `helpers/maintenance/db_maint.py` (34 PRAGMA sites),
`helpers/core/db.py` (17), `helpers/maintenance/snapshot_db.py` (9),
`helpers/misc/database_integrity_check.py` (9), `helpers/maintenance/store_sweep.py`
(7), `helpers/core/db_engine.py` (7). Connect-site hot files:
`helpers/graph/query.py` (27), `helpers/graph/algorithms.py` (24),
`helpers/maintenance/snapshot_db.py` (16), `db_maint.py` (13).

### 2.6 What already exists (reuse, do not rebuild)

- DDL draft + cascade manifest: `findata/Misc/duckdb_research_swap_ddl_draft.sql`
  (38 tables, 23 indexes, FK→RESTRICT, NOCASE→`lower(name)`,
  `datetime('now')`→`CURRENT_TIMESTAMP`), `..._cascade_manifest.json`.
- Rewrite flows: `helpers/misc/swap_rewrite_flows.py` (`drop_entity`,
  `rename_entity` clone-move-delete; 6/6 dual-engine snapshot tests).
- FK integrity: `helpers/misc/swap_fk_integrity.py` (0 orphans over 22 edges)
  + gate wrapper `helpers/misc/swap_fk_gate.py`, wired as advisory
  `swap-fk-integrity` leg at `tests/run_gate_report.py:452,459-460`; 19
  targeted tests across gate/integrity/engine pass.
- Engine funnel: `helpers/core/db_engine.py::connect_funnel` (extension
  router + `DuckDBRow` name-index shim + `table_info`/`user_version`
  introspection parity).
- Conversion: `sqlite_scanner` ATTACH + per-table CTAS with full declared
  type fidelity (Step 1; 38/38 row-count + column-order parity, 0 FK
  orphans both engines). Conversion cache at
  `$TMPDIR/opencode/swap_fk_gate/research_converted.duckdb`.
- Shared wire routing: `helpers/core/duckdb_shared.py` (`connect_shared`,
  quack wire when served, memory-cap config).
- Historical blocker record + re-open triggers: reentry doc §2/§3/§4;
  `doc/local/evaluations/duckdb_sqlite_reentry_preflight.md`.

### 2.7 Measured precedent numbers (do not re-audit)

- Conversion parity 38/38 tables, 0/0 FK orphans (readiness Step 1).
- Write-batch matrix (Step 7, quack-served copy): autocommit 9.31 ms/row,
  25-row batch 2.99 ms/row (3.1×), 1000-row bulk 2.0× local; upsert
  idempotent both engines; ROLLBACK consistent (0 rows landed). Finding:
  conversion carries no UNIQUE/PK constraints — idempotent upserts depend
  on preserving the 23 user indexes in the swap DDL.
- One data-quality fix landed: malformed `entities.created_at` literal
  (Birkenstock, was rowid 746/755) corrected 2026-11-17; fresh CTAS now
  yields full TIMESTAMP fidelity — the Step 1 all-VARCHAR fallback for
  `entities` is obsolete.
- `swap_fk_gate.py` rebuild ~5.8 s on the converted artifact.

## 3. Design

**Chosen mechanism:** execute the port behind an opt-in engine switch,
read lanes first, writers last, with the converted-store parity + FK gate
green at every phase; cut over only on the operator's full-gates go. Keep
the SQLite path runnable (rollback) until cutover is accepted. Each slice is
independently landable and keeps `make qa` green with the switch defaulting
to SQLite.

**Ordering / unblocks:** S0 (gate + baseline) → S1 (converted artifact +
parity) unblocks S2 (funnel routing) and S3 (rewrite flows) → S4
(integrity/maintenance redesign) needs S3 → S5 (consumer sweep) needs S2+S4
→ S6 (cutover) needs S5 + full gates.

### S0 — Governance gate + frozen baseline (this filing)

- Re-file this proposal with the §4 eval-gate bullet; publish §2 census as
  the dated baseline (instrument: `swap_prep_census.py`, already advisory).
- Record the unpark ruling and the re-open triggers; confirm no live
  store is written by any readiness artifact.
- **Acceptance:** `swap_prep_census.py` exit 0, output matches §2 exactly
  (re-run byte-identical); `static_checks` proposal-lifecycle green.

### S1 — Canonical converted artifact + full parity

- Build a fresh converted `research.duckdb` in scratch from the live
  `research.db` via `sqlite_scanner` ATTACH + per-table CTAS (reuse the
  Step 1 driver pattern), using the Step 2 DDL types.
- Port the 3 views and 16 triggers (10 agent_id ABORT checks → DuckDB
  CHECK constraints or application guards; 6 generation bumps → explicit
  counter increments in the write funnel), and the 23 user indexes
  (incl. NOCASE→`lower(name)` expression).
- Parity checks: 38/38 row counts + column order; typed spot-values
  (`entities.created_at` TIMESTAMP, `cin_year` int); 0 FK orphans on both
  engines; 3 view result-set equality; index/collation coverage.
- Decide the FTS5 rollback residue disposition (carry as plain tables vs
  drop), recorded in the proposal.
- **Acceptance:** conversion script exit 0; per-table parity table in the
  arc log; `swap_fk_integrity.py <converted>` → 0 orphans; ontology eval gate
  (§4) run parent=live / candidate=converted with zero regressions.

### S2 — Engine switch in `db.py` (opt-in, read lanes first)

- Extend `helpers/core/db_engine.py::connect_funnel` as the sanctioned
  entry for research-store consumers: route by resolved engine, keep
  `helpers.core.db.connect` byte-identical as the SQLite default.
- Add an opt-in `ResearchEngine` selector (env/arg, default SQLite):
  `AUTO`/`DUCKDB` chooses the funnel; `SQLITE` is the rollback.
- Route the first low-risk **read-only** lanes through the funnel on the
  converted copy: `price_sync.load_tickers` (already funneled), then
  `note_query`/`master_query` notes legs, `graph/query.py` RO projections.
  `app.py` has zero direct writes — a pure reader — so it is an early
  target once the funnel is green.
- PRAGMA disposition (reentry §3.2) applied to funnel consumers:
  `table_info`→`information_schema`; `user_version`→`db_meta`;
  `foreign_keys`/`auto_vacuum`/`journal_mode`/`optimize`→delete/reorder;
  `foreign_key_check`/`integrity_check`/`quick_check`→first-party checkers.
- **Acceptance:** full suite green with switch defaulting to SQLite; the
  duckdb branch passes the same read queries on S1's artifact; a
  dual-engine query-parity test file covers the routed lanes; ontology
  eval gate green at each routed lane.

### S3 — Cascade rewrite flows → production wiring

- Wire `swap_rewrite_flows.py` (`drop_entity`, `rename_entity`) into the
  production writers they replace: `rename_entity`, `name_dedup`,
  `migrate_to_graph_edges`, `store_sweep`, `event_participants`/`events`
  deletes, plus the 5 TBD manifest owners (§2.4) — assign each explicitly.
- Prove every one of the 22 manifest edges has an owner and a dual-engine
  equivalence test (SQLite CASCADE vs DuckDB ordered rewrite, identical
  final row sets), including SET-NULL-on-delete and compound chains.
- **Acceptance:** 22/22 manifest edges owned and tested; 6 existing
  rewrite tests green plus new per-flow tests; no orphan introduced
  (`swap_fk_integrity` stays 0) after each rewrite scenario.

### S4 — Integrity + maintenance redesign

- `database_integrity_check.py`: replace `user_version` (12 sites) /
  `table_info` (33) / `foreign_key_check` (7) / `integrity_check`/
  `quick_check` (2 each) with funnel introspection + first-party checkers;
  keep gate-failing parity on the canonical checks.
- `db_maint.py`: VACUUM/ANALYZE/REINDEX/incremental_vacuum → DuckDB
  CHECKPOINT; `wal_checkpoint`/`journal_mode`/`freelist_count`→delete;
  keep the `--sync-check` surface.
- `snapshot_db.py`: SQLite export/verify half → DuckDB export/verify,
  preserving the parquet manifest + zstd shape and the FTS-shadow
  restore guard extended in #533 (`expected_shadow_prefixes`).
- `store_sweep.py`, `rebuild_schema.py`, `rename_entity.py`: route FK
  checks through the new checker.
- **Acceptance:** `make db-maint --check` and `make snapshot-verify`
  green on a converted copy; integrity gate reports 0 errors with
  identical check coverage; FK checker 0 orphans; ontology eval gate green.

### S5 — Consumer sweep

- Route the remaining RO sites (491 `connect(` sites; 120 importers) and
  review all 93 writer files for transaction idioms (autocommit
  `isolation_level=None`, batch upserts) per Step 7's matrix.
- Replace the 44 raw `sqlite3.connect` sites that touch research.db with
  the funnel or an explicit allowlist entry; update `static_checks`
  allowlist accordingly.
- Resolve the 291 `research.db` path references (CLI defaults, Makefile
  `csr` legs, fixtures) to engine-aware paths where they are live readers.
- **Acceptance:** zero unwaived raw `sqlite3.connect` to the canonical
  store outside the funnel; `static_checks` green; a dual-engine smoke
  run of the query CLIs (`master_query`, `convo_query`, `note_query`,
  `search_tui`) at identical results; targeted tests only (never
  `make pytest` mid-arc per gates.md).

### S6 — Cutover + full gates (operator go)

- EXPORT/IMPORT (or `COPY FROM DATABASE`) the verified converted copy into
  the canonical path; storage-version check (all consumer venvs ≥ 2.0);
  quack-server enrollment for the research store (single RW server,
  serialized writers); remove `-wal`/`-shm`; re-point `db_maint` registry.
- Run the full gates quartet once (qa + integration + perf + advisory per
  `doc/procedures/gates.md`) on the operator's go; perf timed on a quiet
  box; rollback = restore the pre-cutover SQLite file from backup.
- **Acceptance:** `make qa` 15/15; `ontology_eval_gate` ACCEPT over the
  v21 frozen set with zero regressions and no undeclared changes;
  `swap_fk_integrity` 0 orphans; snapshot export/verify round-trip;
  advisory reds triaged (chronic only).

## 4. Acceptance criteria & shakedown

1. **Eval gate — mandatory (query-visible semantics change).** Run
   `helpers/misc/ontology_eval_gate.py` over the frozen question set
   (`helpers/misc/ontology_questions.json`, v21 / 239 questions) with
   `--parent <pre-swap live SQLite snapshot>` and
   `--candidate <converted DuckDB copy>`, **between dry-run and canonical
   apply**: verdict must be ACCEPT — zero regressions, no undeclared
   changes, every declared improvement materializes. Run at S1 (full
   conversion) and again at S6 before cutover. The gate currently opens
   via `helpers.core.db.connect`; S1 must route it through
   `connect_funnel` and handle `sqlite_master`→`information_schema` in the
   `resolve_identifier` / `provenance_agents_for` question shapes so the
   DuckDB candidate can be answered (recorded as an S1 deliverable).
2. **Parity** — 38/38 table row counts + column order; 3 view result sets
   equal; 0 FK orphans on both engines; per-type spot values equal;
   query-CLI results identical across engines.
3. **Repeat count** — conversion parity and FK gate run on ≥2 consecutive
   conversions (byte-identical); write-batch matrix measured with
   median-of-rounds (≥5) per Step 7; no single-run timing claim.
4. **Gate discipline** — `make qa` stays green with the switch defaulting
   to SQLite at every slice; full quartet once at S6 on operator go;
   perf parked if the box is contended; no `make pytest`/`make test`
   mid-arc (targeted tests only).
5. **Rollback** — SQLite path remains runnable until S6 acceptance;
   pre-cutover backup + restore rehearsed before cutover.

| Projected outcome | Today (2026-11-19) | After S6 |
|---|---|---|
| Engines for canonical store | SQLite + 5 DuckDB sidecars | **one DuckDB engine** |
| `-wal`/`-shm` sidecars on research | present | removed |
| Vacuum/reindex scheduling | required | CHECKPOINT only |
| Cross-store joins (research ↔ sidecars) | ATTACH drift | native |
| FK cascade enforcement | engine (SQLite) | 22 ordered rewrites + first-party checker |

## 5. Risks

- **Integrity surface is the highest regression risk** — `foreign_key_check`
  ×7, `integrity_check`/`quick_check` ×2, `user_version` ×12 must become
  first-party checks with identical gate-failing semantics; mitigated by
  the Step 4 checker + dual-engine tests + S4 parity acceptance.
- **22 cascade edges ported to code** — a missed owner or ordering bug
  silently drops/keeps children; mitigated by 22/22 owner+test coverage
  and the FK gate after every rewrite scenario. DuckDB's in-place UPDATE
  refusal on referenced rows needs the clone-move-delete flow (already
  prototyped).
- **FTS5 rollback residue** (§2.3) — carrying `note_search*` shadows as
  plain tables could let a stale reader hit them; mitigated by explicit
  carry-vs-drop ruling in S1 + the post-restore FTS-shadow guard (#532).
- **Conversion fidelity** — the malformed `entities.created_at` caused an
  all-VARCHAR fallback in Step 1; fixed at source, but S1 must re-verify
  TIMESTAMP fidelity and 38/38 typed spot values. Loss of UNIQUE/PK on
  conversion makes upsert idempotence depend on the 23 preserved indexes.
- **Single-writer model** — DuckDB allows one RW process; the B2 map
  (reentry §3.1) says all research.db lanes are livable under one quack
  server, but CLI one-shots must serialize; mitigated by wire routing +
  the write-batch matrix (25-row batch 3.1×).
- **Storage-version compatibility** — conversion is a storage point; any
  remaining DuckDB 1.x consumer needs `-storage-version v1.5.6` writes or
  an upgrade first (reentry §3.5).
- **Scope growth** — census surface grew 114→161 PRAGMA / 391→491
  `connect(` since sizing; S5 re-baselines and does not assume the old
  file count.

## 6. Non-goals

- `corpus.db` (derived load-cache) and `embed_store.duckdb` vector leg —
  STAY (reentry §3.4); no fold in this arc.
- The FTS lexical engine itself / query-token parity — already DuckDB;
  no re-implementation (per #524/#531/#544).
- Any query-visible ontology change (rosters, crosswalks, hierarchies,
  extractor rules) — the engine swap must be **answer-preserving**; the §4
  gate enforces this. Declared improvements are out of scope here.
- Rust/Mojo perf lanes, `signal_sweep`, thin-tail D2/D3, co-supply S3 —
  separate objects, untouched.
- Production writes to `research.db` during S0–S5 (all conversion work on
  scratch copies); no `git commit`/`stg` lifecycle ops by the agent — the
  operator owns patch structure.

## Appendix — raw measurement log (2026-11-19, this box)

| Run | Command | Result | Notes |
|---|---|---|---|
| census | `.venv/bin/python3 helpers/misc/swap_prep_census.py` | 38 tables / sv17 / 24 links / 22 actions / 161 PRAGMA / 491 connect / 93 writers / 120 importers | read-only, exit 0 |
| footprint | `sqlite3` PRAGMA + `os.path.getsize` | 363,528,192 B; 44,376 pages × 8,192; freelist 309; wal | mode=ro |
| schema | `sqlite_master` counts | 38 tables / 3 views / 23 indexes / 16 triggers | FTS5 residue present |
| FK | `PRAGMA foreign_key_list` over 38 tables | 15 CASCADE / 7 SET NULL / 21 UPDATE CASCADE | matches Step 0 |
| manifest | `json.load(cascade_manifest)` | 22 edges; owners store_sweep 5 / name_dedup 5 / migrate 4 / rename 3 / TBD 5 | — |
| surface | `rg` over `helpers/` | 44 sqlite3.connect / 117 duckdb.connect / 291 research.db refs | self-excluded |
| PRAGMA kinds | `rg -n PRAGMA helpers` | table_info 33 / user_version 12 / foreign_keys 11 / fk_check 7 / optimize 6 / journal 5 … | top file db_maint.py 34 |
| gate tests | `pytest test_swap_fk_gate test_swap_fk_integrity test_db_engine` | **19 passed** in 2.85 s | xdist cache reclaimed 1 stale lock |
| ontology questions | `json.load(ontology_questions.json)` | v21 / 239 questions / no `draft` flag | gate-ready |
| eval gate, engine-agnostic | edits to `helpers/misc/ontology_eval_gate.py` + `helpers/core/db_engine.py` (untracked; agent never commits) | funnelized parent/candidate; `_table_exists` helper; `PRAGMA table_info`→`table_info()`; error tuples broadened to sqlite3+duckdb; `_DuckDBCursor.__iter__` fixed (conn not iterable → `iter(fetchall())`) | ruff/ty clean; 60/60 targeted tests green |
| eval gate, live parity smoke | `pytest tests/test_ontology_eval_gate.py tests/test_db_engine.py tests/test_swap_fk_gate.py tests/test_swap_fk_integrity.py` | **60 passed** | incl. new `TestDuckDbCandidate` parity tests + `test_duckdb_cursor_is_iterable` |
| eval gate, live smoke conversion | `$TMPDIR/opencode/db_swap_smoke_convert.py` (fetchmany+executemany, read-only) | 15 tables incl. `quotes` converted → `$TMPDIR/opencode/candidate_smoke.duckdb`, in 2713.6 s | smoke scratch only; S1 plans `sqlite_scanner` ATTACH + CTAS — much faster; LIVE untouched |
| eval gate, live smoke result | `ontology_eval_gate.py --parent memory/research.db --candidate <scratch.duckdb> --questions helpers/misc/ontology_questions.json` | **ACCEPT — 239/239 `unchanged`, 0 reasons** | first failure was `agents-quotes`: smoke scratch omitted the `quotes` table (not a gate bug); added it, re-ran → ACCEPT |

### Progress log

- **2026-11-19 — S1 mandatory-eval-gate groundwork landed & validated**
  (untracked; agent does not run git/stg/commit): the §4 eval gate can now
  ANSWER a converted DuckDB candidate, not just a SQLite store —
  parent/candidate both route through `connect_funnel`
  (`.duckdb` → quack-wire-if-served else file mode), SQLite path
  byte-identical to before. Live parity smoke on the real
  `ontology_questions.json` v21/239 set: **ACCEPT, 239/239 unchanged** —
  every question answers identically on both engines. §4 (run at S1 and
  re-run at S6) is now unblocked by this prerequisite; the full 38-table /
  schema census parity (S1) and FTS residue disposition remain open.
  Smoke conversion via naive fetchmany+executemany took 2713.6 s for
  ~350k rows — S1 must use ATTACH + CTAS, not per-row Python inserts.

- **2026-11-19 — DuckDB 2.0.1/2.1 gap check (all three S1 gaps stay).**
  Release landscape verified via PyPI JSON + roadmap: installed/PyPI
  latest is **2.0.0** (2026-10-21, cp312-abi3 wheel on the py3.14 venv);
  **2.0.1 is dated 2026-11-20** (patch = bugfixes only per semver policy);
  **2.1 "Islandica" is 2027 H1** — no packages exist yet, only plans
  (confirmed: "no packages yet. Only plans.", operator 2026-11-19). The
  three S1 gaps are NOT closed by 2.0.x or the 2.1 roadmap:
  1. *FK actions* (15 CASCADE / 7 SET NULL / 21 UPDATE CASCADE):
     2.0.0 parse-rejects ("FOREIGN KEY constraints cannot use CASCADE,
     SET NULL or SET DEFAULT"); roadmap has no FK-action item — nearest is
     "ALTER TABLE support for adding foreign keys" (constraints, not
     actions); discussion #10851 open since 2024, no PR. → keep FK→RESTRICT
     DDL + S3's 22 code rewrites.
  2. *Triggers*: 2.0.0 ships a narrow subset (AFTER INSERT/DELETE FOR EACH
     ROW with INSERT body; FOR EACH STATEMENT + transition tables).
     Unsupported in 2.0.0 and unlisted for 2.1: BEFORE ... FOR EACH ROW
     (both our agent_id guards), UPDATE/DELETE ... FOR EACH ROW and UPDATE
     bodies (both our generation bumps), recursive chains, RAISE()/error()
     bodies, MERGE/ON CONFLICT on triggered tables. → agent_id guards stay
     application guards + first-party checker; generation counter moves
     into the write funnel (S3).
  3. *FTS5 virtual tables*: unsupported in every version (duckdb-sqlite
     #128); sqlite_scanner reads FTS5 data fine (FTS compiled in, scanner
     PR #70; local probe passes); DuckDB-native FTS is the `fts` extension
     API (PRAGMA create_fts_index + match_bm25), a different engine.
     → S1 ruling: DROP the note_search + 4 shadow tables + note_search_config
     residue from the converted artifact (live FTS index is
     memory/note_search.duckdb, already DuckDB-native); CARRY
     note_search_meta (plain file_path/mtime/content_hash table used by
     rebuild_note_search.py).
  Also noted: 2.0.0 ships a view-query regression (up to 37× slower, fix
  #27760 backported to v2.0-cyanoptera → expected in 2.0.1) — relevant to
  the 3 ported views; a 2.0.1 pin bump is an operator call, not part of S1.

- **2026-11-19 — 2.0.1 bump ruling (operator-approved, deferred):** S1
  proceeds **now on 2.0.0** — 2.0.1 is bugfix-only, so everything S1 needs
  (declared-type DDL, FK-without-actions, CHECK/UNIQUE/PK, expression+DESC
  indexes, regexp views) is identical on both; the one 2.0.1-relevant item
  (view-query perf fix) is runtime-only, not parity. duckdb is unpinned in
  pyproject (decision D8), so the "bump" is lock-only: when 2.0.1 publishes
  (2026-11-20), run `uv lock --upgrade-package duckdb && uv sync`, then
  re-verify on the new version: fresh `swap_convert.py` artifact +
  `swap_fk_integrity` 0 orphans + §4 eval gate ACCEPT + targeted tests.
  Cheap (sqlite_scanner rebuild ≈6 s); storage format unchanged (patch
  release).

- **2026-11-19 — S1 EXECUTED: canonical artifact built + full parity
  PASS.** New `helpers/misc/swap_convert.py` (S1 contract in module
  docstring): PRAGMA-derived declared-type DDL (SMALLINT→BIGINT,
  DATETIME→TIMESTAMP), defaults normalized, composite PKs carried,
  FK edges WITHOUT actions (manifest keeps the 22 original action
  clauses for S3), FTS residue dropped (6 artifacts incl
  `note_search_docsize` — live store does have it; earlier census
  missed it → **32 carried / 38 source**, not 33), `note_search_meta`
  carried. Views: GLOB translated to `regexp_matches(source_ref,
  '_[0-9]{14}_WEB\.xml$')` (DuckDB 2.0 also parses GLOB natively as
  the `~~~` operator — parity-true both ways; regexp chosen as the
  engine-idiomatic, §4-validated form). Indexes: 23/23 with
  `COLLATE NOCASE` → `lower(col)` expression form, DESC preserved.
  Self-referencing FK (`concepts.broader_id`) loaded by iterative
  parent-first INSERT with dangling-ref ValueError. Build:
  `bench_data/duckdb_swap/stores/s1/research_converted.duckdb`
  (168 MB) in **5.3 s** via sqlite_scanner ATTACH (vs 2713.6 s naive
  smoke). Parity report
  (`…/stores/s1/research_converted.report.json`): row parity all
  tables, column order + declared-type parity all tables, 3/3 view
  result-set equality (sorted-row sha256), FK orphans 0 both engines
  (`PRAGMA foreign_key_check` + `swap_fk_integrity`), 23/23 index
  coverage, typed spots (entities.created_at TIMESTAMP — Birkenstock
  2026-01-10 09:12:00; cin_year BIGINT; value_num DOUBLE;
  db_meta.generation=1145754). Acceptance legs on the rebuilt
  artifact: `swap_fk_integrity` → "no FK orphans across 22 edges";
  §4 gate → **ACCEPT — 239 questions, 0 reason(s)**
  (report `bench_data/duckdb_swap/findings/s1_eval_gate_report.json`).
  Tests: 13 in `tests/test_swap_convert.py` (type map, view/index
  translation, load order, mini-store end-to-end, dangling self-FK
  loud failure, check-mode match) — all green; ruff + ty clean.
  Engine-agnostic gate prerequisite also landed
  (`ontology_eval_gate.py` routes parent/candidate through
  `helpers.core.db_engine.connect_funnel`; presence checks via
  `information_schema`; `_MISSING_TABLE_ERRORS` = sqlite3.OperationalError
  | duckdb.CatalogException | duckdb.BinderException;
  `db_engine._DuckDBCursor.__iter__` fixed to `iter(self.fetchall())`).

- **2026-11-19 — S2 EXECUTED: opt-in selector + app.py read-lane routing.**
  New public `ResearchEngine` selector in `helpers/core/db_engine.py`:
  `FINDATA_RESEARCH_ENGINE` in {`sqlite` (default/rollback), `auto`,
  `duckdb` (opt-in)}; `engine=` kwarg overrides env; `AUTO`/`DUCKDB`
  opt-in resolves a bare `research.db` path to the sibling `.duckdb`,
  and a pathless call honors `FINDATA_RESEARCH_DB_PATH` (app.py).
  `connect_funnel` routes by resolved engine + extension; SQLite branch
  delegates to the **byte-identical** `helpers.core.db.connect`.
  Added `_DuckDBConnCursor` shim + `_DuckDBConn.cursor()` so app.py's
  `conn.cursor()` pattern works on DuckDB. `db.py` untouched.
  `helpers/maintenance/price_sync.py::load_tickers` already funneled
  (verified: 6072 tickers on both engines). `app.py::get_db_connection()`
  now `connect_funnel(read_only=True)` — zero writes — with new
  engine-agnostic `_research_table_exists` (information_schema on
  DuckDB, sqlite_master on SQLite) and `_research_has_column` (via
  `table_info`): converted every `sqlite_master`/`PRAGMA table_info`/
  `pragma_table_info` probe (former :108/:674/:687/:886/:891/:896/
  :3215; `_search_schema(cursor)`→`(conn)`; `_hyper_present`). Remaining
  `sqlite_master` strings live only inside the helper. `COLLATE NOCASE`
  left unchanged — verified identical semantics on both engines.
  `note_query.py`/`master_query.py` notes legs were researched but NOT
  changed: they target the separate `note_search` sidecar, not the
  research store. `graph/query.py` RO projections likewise deferred
  (large dual-engine module; its DuckDB graph-cache connection already
  native). Tests: new `tests/test_research_engine_switch.py` (13 tests —
  selector semantics, dual-engine price_sync/app probes/cursor/
  name-indexed rows, S1-artifact integration) all green; 86 green across
  db_engine/ontology_eval_gate/swap_convert/research_engine_switch/
  swap_fk_gate/swap_fk_integrity; ruff + ty clean. Default engine stays
  SQLite (full suite unaffected); §4 gate on S1 artifact still ACCEPT.
  Post-S2 open: route note_query + graph/query.py RO projections, and
  the `sqlite_master`/COLLATE NOCASE sites in those modules (S5 scope).

- **2026-11-19 — S2 OPEN PARTS WRAPPED: note_query + graph/query.py routing.**
  Latent bug fixed in `helpers/core/db_engine.py::is_duckdb_conn`: duckdb 2.0
  raw connections report `type(conn).__module__ == '_duckdb'` (older builds
  `'duckdb'`); the bare `== 'duckdb'` check silently misdetected raw sidecar
  connections, so note_query's `_is_duckdb` (now delegating here) would have
  routed them down the SQLite FTS5 branch (ParserException on MATCH). Both
  module names now accepted; regression test added in tests/test_db_engine.py.
  `helpers/misc/note_query.py`: `_is_duckdb` delegates to the shared helper;
  `_index_count_dims`/`_stored_note_model` now route through `connect_funnel`
  (opt-in selector applies; `.duckdb` extension still wins; default SQLite
  branch byte-identical). `open_note_index`'s `.duckdb` branch deliberately
  KEEPS `duckdb_lock.open_read_only` (flock + 2/10/48 s ladder) — the note
  sidecar is rebuilt by a long holder and a plain funnel open would regress
  into fail-fast; documented in the function docstring.
  `helpers/graph/query.py`: the three source RO opens in
  `_check_generation_staleness`, `_stored_note_dims*`, and `_mark_warm` now
  go through `connect_funnel` (default delegates to the unchanged
  `helpers.core.db.connect`). The two `sqlite_master` probes at former
  :2090/:2257 were investigated, NOT changed: they run on the DuckDB graph
  cache conn where research.db is ATTACHed as `fin` (TYPE sqlite); verified
  2026-11-19 on duckdb 2.0 that unqualified `sqlite_master` resolves the
  fin-attached catalog (real tables → 1, fake → 0), so they are already
  engine-agnostic. Do NOT switch to `fin.sqlite_master` — that raises
  CatalogException (the sqlite compat view exists only at the root). Findings
  documented inline at both sites. `COLLATE NOCASE` left unchanged (verified
  identical on both engines). Tests: 118 green across note_query(×2)/
  db_engine/research_engine_switch/ontology_eval_gate/swap_convert/swap_fk_*;
  40 green graph_disk + graph_note_sidecar; 2 new S2 funnel-routing tests in
  tests/test_note_query_duckdb.py; ruff + ty clean.
  CAVEAT + RESOLUTION — `static_checks` rc=1 on 4 STALE `/api/resolve`
  coverage-ledger rows (tracked doc/security/coverage-ledger.json): my
  app.py edits in S2 changed those handler bodies (added `_research_has_column`
  at :693, replaced a `sqlite_master` probe at :707). Operator authorised a
  restamp (m05356): `coverage_ledger.py seed --app app.py --ledger
  doc/security/coverage-ledger.json` updated exactly 4 fingerprints (all
  `/api/resolve`; verdicts untouched — `seed` never invents coverage).
  `check --strict` now rc=0 (39/39 routes, no orphans); full `static_checks`
  rc=0 (3 advisory warnings, non-blocking). Operator owns git/stg — all
  edits in the `db_swap` patch.


- **2026-11-20 — S3 EXECUTED: unified cascade rewrite flows wired into production.**
  Operator approved design (m05457): Option A — one unified `swap_rewrite_flows.py`
  module, reuse the S2 `ResearchEngine` selector for the destructive-write
  branch, add the agent flow to the same module.
  - Manifest `findata/Misc/duckdb_research_swap_cascade_manifest.json`: the 5
    `TBD (phase 6 consumer sweep)` `rewrite_flow_owner` fields assigned to
    concrete flows — edge 6 `event_participants.event_id` → `drop_event`;
    edges 10/11 `graph_analytics`/`graph_analytics_contract` → `rename_entity`;
    edge 18 `ppr_results.entity` → `drop_entity`; edge 21
    `speaker_identity.person_name` → `rename_entity`. All 22 edges now have a
    non-TBD owner (asserted in a new test). NOTE: the manifest is under
    `findata/**/*.json` (gitignored, writer-owned vault), so these owner-field
    edits are on-disk only and will NOT appear in `git status` / `stg diff` —
    the operator must `git add -f` if it is to be committed (the file already
    existed as an ignored S0 artifact).
  - `helpers/misc/swap_rewrite_flows.py`: added `drop_event` (consumes the
    previously-unused `EVENT_CHILDREN`) and `drop_agent` (5 `agent_id` SET-NULL
    fact tables, owner store_sweep; `db_maint._AGENT_FACT_TABLES` mirrored as a
    local `AGENT_FACT_TABLES` frozenset to avoid a maintenance→misc import).
    `drop_agent` uses `_rotate_rows` for `events`/`hyper_edges` (DuckDB 2.0
    blocks in-place UPDATE of an agent_id column on a row that is itself an FK
    parent). Added `NO_UPDATE_CASCADE = {"ppr_results"}`: `ppr_results.entity`
    is DELETE-CASCADE-only in both live DDL and the draft (:347-354), SQLite
    blocks a parent rename there, so `rename_entity` now skips it on DuckDB too
    (verified: SQLite raises `IntegrityError FOREIGN KEY constraint failed` on
    `UPDATE entities SET name` while ppr_results still references the old key).
    `rename_entity` gained optional `new_values` to seed extra columns on the
    cloned row (production writer passes normalized_name/file_path/sector/
    ticker/last_updated). Docstring updated: flows are the canonical S3 rewrite
    logic, not "a later phase". `_rotate_rows` `new: str | None` for the SET-NULL
    rotation.
  - Production wiring `helpers/maintenance/rename_entity.py`: opens the store
    with `connect_funnel(..., engine=ResearchEngine.from_env())`; SQLite default
    path (BEGIN…COMMIT + in-place UPDATE CASCADE + `PRAGMA foreign_key_check`)
    stays byte-identical; DuckDB path routes the destructive write through
    `swap_rewrite_flows.rename_entity`. DuckDB 2.0 FK validation is NOT
    transaction-safe for the clone-move-delete rewrite — an intermediate FK
    check aborts the whole transaction (not recoverable by ROLLBACK), so the
    DuckDB branch autocommits each ordered statement; documented inline.
    End-to-end probe (tmp DuckDB store, env `FINDATA_RESEARCH_ENGINE=duckdb`):
    `main()` RC 0, entity renamed with all extra columns, graph_edges repointed,
    `swap_fk_integrity`-style orphan count 0.
  - New tests `tests/test_swap_rewrite_flows_s3.py`: dual-engine equivalence for
    `drop_event`, `drop_agent`, `drop_entity` (covering the 3 marked cascade
    edges ppr_results/graph_analytics/graph_analytics_contract), rename with
    NO_UPDATE_CASCADE skip, plus a 22-edge manifest-ownership assertion. The 6
    pre-existing `test_swap_rewrite_flows.py` tests are byte-identical.
    DuckDB 2.0 has no `.executescript()` (fixture uses `.execute(DDL_string)`).
  - Acceptance: 57 green across rename_entity + swap_rewrite_flows(×2) +
    research_engine_switch + db_engine + swap_fk_integrity + swap_fk_gate;
    ruff + ty clean on all edited files; `static_checks` rc=0 (3 advisory
    warnings, non-blocking). No `make pytest` (targeted only). Operator owns
    git/stg — all edits uncommitted. S4 (integrity/maintenance) and S5
    (consumer sweep) remain out of scope.

- **2026-11-20 — DuckDB 2.0.1 lock bump EXECUTED + re-verified (no code
  changes).** Operator go (m06876) on the 2026-11-19 deferred ruling: PyPI
  published 2.0.1 on 2026-11-20 (wheels uploaded 13:35Z, incl.
  cp312-abi3 manylinux for our py3.14 venv). Ran `uv lock
  --upgrade-package duckdb` → "Updated duckdb v2.0.0 -> v2.0.1"; `uv sync`
  alone dropped the dev/optional extras from the venv (pytest/ruff/ty/
  mojo/textual/faiss), restored with `uv sync --all-extras` (matches the
  pre-bump venv; duckdb is unpinned in pyproject per decision D8 so the
  bump is lock-only). NOTE: `uv.lock` is gitignored (.gitignore:149), so
  the bump lives only in the local lock/venv — nothing to stage.
  Re-verification on 2.0.1 (all PASS):
  - Fresh `swap_convert.py` → `bench_data/duckdb_swap/stores/s1_201/`
    (in `.gitignore` bench_data): tables 32/38, indexes 23/23, views 3/3,
    fk_orphans 0; report `research_converted.report.json`.
  - `swap_fk_integrity.py` on the artifact: 0 orphans / 22 edges, exit 0.
  - §4 eval gate `--parent memory/research.db --candidate <s1_201 artifact>`:
    ACCEPT 239 questions / 0 reasons
    (`bench_data/duckdb_swap/findings/s1_201_eval_gate_report.json`).
  - 98 targeted tests green across swap_convert / swap_fk_gate /
    swap_fk_integrity / ontology_eval_gate / db_engine /
    research_engine_switch / swap_rewrite_flows(+s3); ruff + ty clean;
    `static_checks` rc=0 (3 advisory). No `make pytest` (targeted only).
  - Runtime: view #27760 perf fix is in 2.0.1 (bugfix-only; no parity
    change). 2.0.0's three gaps (FK CASCADE/SET NULL, BEFORE row
    triggers, FTS5) remain — S3/S1 doctrine unchanged. S4/S5 still
    out of scope.
