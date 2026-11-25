---
title: "Rewrite sync_tags' per-row DuckDB writes as set-based statements — the perf leg from ~28 s under its 2.0 s budget"
status: executed
filed: "2026-11-24"
executed: "2026-11-24"
completed_md: "567"
area: "helpers/core/sync_tags.py + tests/test_sync_tags.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Rewrite sync_tags' per-row DuckDB writes as set-based statements — the perf leg from ~28 s under its 2.0 s budget

**Date:** 2026-11-24 · **Status:** EXECUTED 2026-11-24 (archived same day,
completed.md #567) ·
**Area:** `helpers/core/sync_tags.py`, `tests/test_sync_tags.py`

## 1. Motivation

`sync_tags` is a perf-gate leg (`tests/run_perf_benchmarks.py:203`,
`helpers/core/sync_tags.py --apply`, budget 2.0 s). Its recorded history
(gate_query timing) is: 0.68 s ✓ (2026-11-21, twice) → 0.10 s FAIL(rc)
twice (11-22 morning, crashed legs) → **151.20 → 87.14 → 29.38 → 28.49 s
FAIL(budget)** across 11-22 → 11-24. That is a ~40x regression that grew
steadily and then plateaued — not a loaded-box artefact (the 11-24 forced
perf run 4760 recorded it under FORCED attribution; `doc_query` and
`graph_l1_centrality`, which failed in the same run, did NOT plateau —
they are load). Investigated on the operator's direction (coverage-sweep
gate arc, 2026-11-24); this proposal files the cause and the fix.

## 2. Evidence

Phase profiler (`profile_st.py`, scratch copy of the 304 MB research
store, 2026-11-24; TOTAL 24.80 s ≈ the perf observation):

| phase | time | statement count |
|---|---|---|
| connect + DDL + entities fetch (27,754 rows) + note walk (1,401 files) | 0.96 s | — |
| entity_tags DELETE + executemany + COMMIT (7,431 rows) | **13.8 s** | 7,431 INSERTs |
| sector UPDATE sweep + membership (1,216 updates) | **8.4 s** | 1,216 UPDATEs |
| rebuild_note_tags (1,433 rows) | 2.5 s | 1,433 INSERTs |
| close/checkpoint | ~0 s | — |

Root cause: DuckDB's python `executemany` executes the prepared statement
**once per parameter set** (~1.9 ms per INSERT/UPDATE on a PK-indexed
table — MVCC local storage + ART key maintenance per row), against
SQLite's per-statement microseconds where the same code was written. The
decay 151 → 87 → 29 → 28 s is the `sector_updates` list shrinking as
classification converged; ~28 s is the floor of ~8,600 individual
statements. The fix is statement *shape*, not hardware.

## 3. Design — three slices, one file

**S1 — `entity_tags` + `note_tags` bulk insert (the 16.3 s).** Add one
engine-branching helper, `_bulk_insert(conn, table, cols, rows)`, and use
it at `sync_tags.py:487` (entity_tags) and `:200` (note_tags):

- DuckDB (`_is_duckdb_conn(conn)`, already imported): register `rows` as
  a pyarrow relation (`conn.register`), then ONE
  `INSERT INTO <table> (<cols>) SELECT DISTINCT … FROM <stg>` and
  `conn.unregister`. `SELECT DISTINCT` preserves `INSERT OR IGNORE`'s
  duplicate-tolerance (bulk can carry repeated (name, tag) pairs across
  walk paths); no SQL literals are interpolated, so no quoting risk.
- SQLite (legacy path): keep the existing `executemany` — per-row
  overhead is microseconds there.

**S2 — sector UPDATE sweep (the 8.4 s).** At `:502`: DuckDB — register
the (canonical, name) pairs and run ONE
`UPDATE entities SET sector_classification = s.canonical FROM <stg> s
WHERE entities.name = s.name AND entities.sector_classification
IS DISTINCT FROM s.canonical`; keep the existing rowcount clamp
(`max(cur.rowcount or 0, 0)`). SQLite — keep `executemany`.

**S4 — D17 sector-membership sweep (the residual 7.57 s).** The first
measurement attributed 8.4 s to the sector UPDATE sweep; a finer probe
showed the sweep itself is sub-second and the cost is the 1,216
per-company `put_sector_membership` calls (edge SELECT + incidence
INSERT each), even though they add 0 rows in steady state. Add
`_bulk_put_sector_membership(conn, pairs)`: one edge-id lookup for the
~42 sectors (create missing edges once), ONE anti-join SELECT for the
new pairs (the returned `membership_added`), ONE
`INSERT OR IGNORE … SELECT he.id, p.name, NULL, NULL` for all
incidences, and `_refresh_member_count` only for edges that gained
rows. `put_sector_membership` itself is unchanged — the canonical
single-write API still serves CLI / move_sector. SQLite — keep the old
loop.

**S3 — pins.** In `tests/test_sync_tags.py`: three statement-shape pins
over a `_CountingDuckDBConn` proxy (`raw` = the real connection): (a) the
bulk tag insert is ONE `INSERT … SELECT DISTINCT` and dedupes a repeated
(entity_name, tag) pair; (b) the sector sweep is ONE `UPDATE … FROM`
that skips already-matching values; (c) the D17 membership sweep issues
ONE `INSERT OR IGNORE INTO hyper_incidences … SELECT`, preserves
idempotence and reports the correct added count. All three fail against
the old per-row shapes (shown by can-fail).

## 4. Acceptance

1. **Output equality:** old vs new `--apply` on two copies of a scratch
   store → `entity_tags`, `note_tags`, `entities.sector_classification`
   AND `hyper_incidences` dumps identical.
2. **Budget:** standalone `--apply` on the scratch copy, quiet box,
   < 2.0 s (the perf budget). The perf gate re-measures on live on the
   operator's go.
3. `pytest tests/test_sync_tags.py` all green, plus the three new pins;
   can-fail shown against the pre-fix code (all three pins fail).

## 5. Risks

- pyarrow enters the sync path — already a repo dependency (snapshot
  reader, hyper pipeline); no silent fallback to `executemany` on a
  register failure: fail loudly instead (a silent fallback would hide
  the regression).
- `helpers.core.db.connect` returns a `_DuckDBConn` PROXY with `.raw`:
  `register`/`unregister` must be called on the raw connection, while
  `execute` goes through the proxy (same session). A quack-wire
  connection cannot register a local Arrow relation — if the research
  store becomes served, revisit this path (`is_served` is False now;
  the read-only-store gate fixes in qa run 4755 rely on that).
- `UPDATE … FROM` + `IS DISTINCT FROM` semantics differ on SQLite → the
  engine branch keeps SQLite on the old path entirely.
- Registered relations are session-scoped; unregister after use to keep
  long-lived connections (maint) clean.

## 6. Non-goals

- No schema changes to `entity_tags` / `note_tags` / `entities` /
  `hyper_incidences`.
- No change to `put_sector_membership` itself (the canonical single-write
  API); S4 only gives sync_tags a batched caller of the same contract.
- No perf-harness changes; no re-run of the perf gate inside this arc.

## 7. Pending slices — other per-row DuckDB sites (triage 2026-11-24)

Read-only triage after S1–S4 landed: `rg -n 'executemany\(' helpers/**/*.py`
then classification by the connection each call site actually receives
(`helpers/core/db.py:143` `connect()` is selector-aware: the research
store resolves to the DuckDB proxy; other `.db` paths stay SQLite).
Row counts read from `memory/research.duckdb` through
`helpers.misc.duckdb_lock.open_read_only`. **P1–P4 executed 2026-11-24;
P5 pending.** P1 and P2 keep the SQLite path byte-for-byte and
prove equality against the pre-fix code on scratch store copies; P3 was
measured and deliberately left unchanged (below).

- **P1 (do first) — `helpers/graph/derive_event_participants.py:203`.**
  `conn` comes from `connect(DEFAULT_DB_PATH)` at `:213` → DuckDB
  proxy, so the per-event `DELETE` + `executemany` INSERT block pays the
  same ~1.9 ms/statement overhead; `event_participants` holds 2,786 rows
  and the module is a standing perf leg (`derive_events`). Shape: stage
  all `(event_id, participant, role, source_ref)` rows in one Arrow
  relation, one `DELETE … WHERE event_id IN (SELECT … FROM staging)` and
  one `INSERT … SELECT`, preserving `stats` and `dry_run`; keep the
  SQLite path as-is. Acceptance: scratch-store equality dump + one
  statement-shape pin, as in A6/A7.
  **EXECUTED:** `_replace_participants_duckdb` stages the touched event
  ids and all participant rows in two Arrow relations, then one DELETE
  and one INSERT SELECT; no SELECT DISTINCT (the PK still rejects
  duplicate (event, participant, role) pairs as the old INSERT did).
  Pin in `tests/test_derive_event_participants.py`; old-shape can-fail
  `1 failed`. Scratch equality: OLD 33.16 s → NEW 2.65 s, 2,786 rows
  byte-identical (A8).
- **P2 (opportunistic) — `helpers/graph/derive_org_membership.py:168`
  and `helpers/graph/derive_merger_hyperedges.py:363`.** Both write
  `hyper_incidences` via the house door (`:187` / `:65`) → DuckDB, and
  both loop per fact with an edge SELECT + executemany INSERT. Their
  edge types are outside the live top-8 (`group` 19,180 … `sector`
  1,229 incidences), so the win is likely small — measure the per-run
  row count before editing. Same engine-branch + equality discipline;
  do not change any canonical membership API.
  **EXECUTED:** shared `helpers/graph/incidence_batch.py`
  (`bulk_replace_hyper_incidences`) stages `(edge_type, label,
  entity_name, weight, role, valid_from)` rows and issues one DELETE +
  one INSERT SELECT for both call sites; three pins in
  `tests/test_incidence_batch.py`, old-shape can-fail `2 failed`.
  Live sizes are small (34 org edges / 287 incidences, 5 merger facts),
  so the scratch equality run showed no wall-time win (OLD 0.70 s vs NEW
  0.70 s for org; 0.05 vs 0.04 for merger) — the win is removed statement
  count and a single code path, not measured latency (A9).
- **P3 (measure, then decide) — `helpers/core/stable_write.py:135–144`.**
  Dual-engine by design (`sqlite_master` probe at `:125`); the
  per-stale-id `executemany DELETE` is only a problem when a DuckDB
  caller prunes many ids. Map callers and count stale ids first; do not
  convert blind.
  **MEASURED — NO CHANGE:** callers are `derive_events` (`events` +
  `event_participants` children) and `derive_insights` (`quotes` and
  `company_metrics`, both 0 live derived rows). A real dry-run derive
  over the live store reported `64 new, 1965 unchanged, 64 stale`
  (2,029 events under the derived prefix): at most ~64 child + 64 parent
  DELETEs + 64 INSERTs ≈ 0.36 s at the measured ~1.9 ms/statement — far
  below the 24 s sync_tags case. Not converted; revisit if a converge
  cycle ever reports hundreds of stale ids (A10).
- **P4 — `helpers/maintenance/tag_international_entities.py`**
  (was: defer — "a small un-gated CLI; no evidence of a material cost").
  **EXECUTED 2026-11-24, as a cutover defect rather than a latency
  slice.** The default was `--db memory/research.db` opened with a raw
  `duckdb.connect(args.db, **memory_cap_config())`, so since the
  2026-11-21 cutover a default invocation created a fresh empty DuckDB
  store at the retired path and died with `Catalog Error: Table with
  name entities does not exist!` (scratch repro, A13). Nothing noticed:
  the CLI is un-gated and the live store holds 0 `geography/international`
  rows. Changes: `_cli` opens through the house door
  (`helpers.core.db.connect`, `read_only=not args.apply`; `--db` defaults
  to None so the canonical store and `FINDATA_RESEARCH_DB_PATH` are
  honoured; relative paths anchor at the project root; `try/finally`
  close); the tagged-among lookup moved from DuckDB-only
  `IN (SELECT unnest(?))` to a portable `IN (?,?,…)` — the two functions
  had never worked on a SQLite connection (`no such function: unnest`);
  `apply_tags` issues one `INSERT … SELECT DISTINCT … WHERE NOT EXISTS`
  on DuckDB and keeps `executemany` on SQLite. Write path on scratch
  copies of the live store: 150 rows 0.84–1.06 s → 0.015–0.016 s, tag
  sets identical (A13). Pins in `tests/test_tag_international_entities.py`
  (3 → 9: statement shape, SQLite path, engine agreement, three hermetic
  CLI tests); against the pre-fix module `6 failed, 3 passed`.
- **P5 (pending — needs a go) — six sibling CLIs carry the same stale
  default.** `rg 'default="memory/research.db"' helpers` lists ten files
  (A14). Six pair the literal with the same raw
  `duckdb.connect(args.db, **memory_cap_config())` and are
  broken-by-default by code shape — inferred, not executed:
  `helpers/maintenance/psu_dyad_pass.py:183`,
  `helpers/maintenance/mnc_arm_probe.py:171`,
  `helpers/maintenance/mnc_dyad_pass.py:255`,
  `helpers/maintenance/jv_dyad_census.py:147`,
  `helpers/maintenance/foreign_links_generate.py:305`,
  `helpers/maintenance/foreign_name_isin_probe.py:241`. The other four
  already resolve the cutover (`helpers/graph/csr.py:407`,
  `helpers/maintenance/group_discovery_growth_monitor.py:233`,
  `helpers/maintenance/group_discovery_inventory.py:164`,
  `helpers/validators/graph_scale_watch.py:168`). Shape: the P4 fix plus
  one hermetic default-invocation pin per CLI. Not a latency slice; it
  lives here because P4 is what surfaced it.

Non-candidates, recorded so a future sweep does not re-litigate them:
`helpers/graph/derive_co_mentions.py:465` (`conn.total_changes` →
SQLite only), `helpers/graph/derive_relational_insights.py:501`
(`sqlite3.Connection` type hints), `helpers/maintenance/fold_identifiers.py:165`
(same), `helpers/maintenance/shareholding_sync.py:437,657,1011,1129,1135,1139`
(main store via `sqlite_connect` at `:1301`/`:1488`), and the
`rebuild_*_search` index writers (9 SQLite index DBs). SQLite's
per-statement cost is microseconds; these need no change. The
`executemany` warning at `helpers/graph/query.py:2563` concerns the
embed cache's own WAL path, not this pattern.

## Appendix — raw measurement log

| # | what | value |
|---|---|---|
| A1 | phase profiler on scratch copy (2026-11-24) | TOTAL 24.80 s; walk 0.587 s; entity_tags 13.797 s; sector sweep 8.4 s (of 22.216 s incl. membership writes 0); note_tags 2.5 s |
| A2 | gate_query timing, perf/sync_tags | 0.68 ✓ (11-21) → 151.20 → 87.14 → 29.38 → 28.49 FAIL (11-22 → 11-24) |
| A3 | live-store row counts (read-only) | entity_tags 7,431; note_tags 1,433; entities 27,754 (1,401 with file_path) |
| A4 | in-memory mechanism probes (duckdb + pyarrow register) | `INSERT INTO … SELECT DISTINCT … FROM stg` dedupes and inserts in one statement; `UPDATE … FROM stg … IS DISTINCT FROM` updates only the changed row; `INSERT OR IGNORE … SELECT` batch-inserts incidences (rowcount = new rows) |
| A5 | finer membership probe (scratch copy, 1,273 pairs / 42 sectors) | the per-company `put_sector_membership` loop alone = 7.57 s — the whole residual after S1/S2; this is why S4 exists |
| A6 | old vs new `--apply` on two copies of the scratch store (2026-11-24) | OLD 24.21 s → NEW **1.40 s**; `entity_tags` 7,431 / `note_tags` 1,433 / `entities.sector_classification` 27,754 / `hyper_incidences` 59,660 rows — all four dumps **identical** |
| A7 | new pins vs old per-row shapes (can-fail driver) | `3 failed, 66 deselected` — all three statement-shape pins fail when the four helpers are monkeypatched back to `executemany` / per-company loop; live code: 69 passed |
| A8 | P1 old vs new rebuild on two copies of the live store (`p123/equality_p1.py`) | 2,786 rows / 2,029 events; OLD 33.16 s → NEW 2.65 s; dumps identical; old-shape can-fail `1 failed` |
| A9 | P2 old vs new derive apply on scratch copies (`p123/equality_p2.py`) | org 34 edges / 287 incidences: OLD 0.70 s → NEW 0.70 s; merger 5 facts: 0.05 → 0.04 s; both dumps identical; old-shape can-fail `2 failed` |
| A10 | P3 decision measurement (`p123/measure_p3.py`, real dry-run derive on a read-only live connection) | `events would persist: 64 new, 1965 unchanged, 64 stale` — ~192 DuckDB statements (~0.36 s); no code change |
| A11 | corpus-cache follow-on (`helpers/core/corpus.py` `_bulk_replace_cache_rows`, live store, 2026-11-24) | live `Corpus.load("findata", use_cache=True)`: 120 s → 2.3 s (rewrite) → 1.4–1.8 s (true warm; 1,406/1,406 mtime hits). Root cause was NOT write persistence — `mtime REAL` (float32) quantized float64 stat mtimes (ULP ≈ 256 s at 1.79e9; float32(1794583708.66) == 1794583680.0), so every mtime compare misfired and every load re-read/re-hashed/re-upserted all rows. Fix: DROP/CREATE + one INSERT SELECT from a registered Arrow relation, with the DDL recreated using `mtime DOUBLE`; SQLite path untouched. New pin `tests/test_corpus_cache_text.py::test_duckdb_bulk_rewrite_shape_and_double_type` (statement shape + DOUBLE column). |
| A12 | markdown-lint verdict writes follow-on (`helpers/misc/markdown_lint.py` `_bulk_record_verdicts`, fake-npx cold run 2026-11-24) | empty sidecar, 1,933 stale files: 17.32 s → 1.07 s (one DELETE-IN + INSERT SELECT from a registered Arrow relation); warm rerun 0.28 s, 1,933 rows persisted. SQLite path untouched. New pin `tests/test_markdown_lint.py::test_duckdb_bulk_verdict_write_shape`. |
| A13 | P4 write path on scratch copies of the live store (`p4/measure_write.py`) + pre-fix CLI repro (`p4/demo_cli.py`) | executemany, 150 rows: 0.842 / 0.926 / 1.062 s; set-based: 0.015 / 0.016 / 0.016 s; rerun 0.003–0.006 s on both; tag sets identical (150 rows; `entity_tags` 7,431 → 7,581). Pre-fix default invocation from a scratch cwd: `CatalogException` plus a stray 12,288-byte `memory/research.db` |
| A14 | stale-default class (`rg 'default="memory/research.db"' helpers`, HEAD `761b0a164`) | 10 files; 6 broken-by-default by code shape (P5); 4 already resolve the cutover |

P4 EXECUTED (2026-11-24): `helpers/maintenance/tag_international_entities.py`
opens through the house door, the tagged-among lookup is portable across
engines, and the DuckDB write is one `INSERT … SELECT` (A13). Nine tests
in `tests/test_tag_international_entities.py` pass; six fail against the
pre-fix module. Live default dry-run now reads the canonical store —
`international candidates: 150 (already tagged 0, missing 150)` — and
leaves no file at the retired path. P5 (six sibling CLIs) is pending.

## Execution log (2026-11-24)

S1–S4 landed in `helpers/core/sync_tags.py` (SQLite path untouched); S3
pins in `tests/test_sync_tags.py` (69 passed, ruff + ty clean); can-fail
A7; equality + budget A6. Operator direction (2026-11-24): no full qa run
3 and no perf re-run — targeted legs plus the A6 equality proof are the
acceptance evidence for this arc. Patch message applied with
`stg edit -f … duck_sync_tags`; search-fresh APPLY + check rc 0;
convo-fresh APPLY rc 0 (its later check drifts on the live session and is
parked per `doc/procedures/gates.md`).

P1–P3 (operator go 2026-11-24): P1 added
`_replace_participants_duckdb` and a statement-shape pin (16 tests in
`tests/test_derive_event_participants.py` pass, old shape fails);
P2 added the shared `helpers/graph/incidence_batch.py` with both derive
call sites branched and three pins in `tests/test_incidence_batch.py`
(old shape fails); P3 measured and left unchanged. Equality/timings A8–
A10. No full qa or perf run — operator direction stands.

Gate run after the patch (2026-11-24, tree at `ca7f504b8`): qa 4776
17/17 PASS (pytest 1,093.84 s, parity 82.91 s ✓); advisory 4777 23/26 —
`lint-audit` caught four findings in this patch (2× S608 in `sync_tags.py`
f-string SQL, C901 in `derive_merger_hyperedges.apply_derivation`, S110 in
`tests/test_incidence_batch.py`); all four fixed with in-house `noqa` +
rationale, `ruff check --select S,UP,C901 .` clean, 88 targeted tests
pass. `gate-timing-watch` FIRE (5 regressions) and `convo-fresh-check`
(live-session drift) are not correctness failures.

Whole timing picture (2026-11-24): the pytest wall growth is a REAL
post-DuckDB-cutover regression, not just load. A quiet-box rerun of the
five slowest tests reproduced it (5 passed, 514.84 s):
`chain_idempotence` 328 s, `harvest_retention` 99 s, `markdown_lint` 16 s,
`maint --dry-run` 17 s. cProfile: `helpers/misc/markdown_lint.py:334`
writes 1,933 verdict rows through DuckDB `executemany` (16.73 s self time);
`helpers/maintenance/maint.py:916 _prewarm_corpus` →
`helpers/core/corpus.py:204 load` → `helpers/core/db_engine.py:181
executemany` (120.5 s) because all 1,406 cached note mtimes are stale
(e.g. cached 1794583680.0 vs file 1794583708.66), so every load rehashes
and upserts all rows. Same per-row DuckDB pattern as sync_tags — follow-on
slices: set-based corpus-cache upsert + mtime precision and set-based
verdict writes.

Corpus-cache follow-on EXECUTED (2026-11-24): `helpers/core/corpus.py`
gained `_bulk_replace_cache_rows` (DuckDB: DROP/CREATE + one INSERT SELECT
from a registered Arrow relation, `mtime DOUBLE` column) and
`_is_duckdb_conn`; `load()`'s upsert+eviction pair became one full-table
rewrite (preserved cached rows merged with fresh upserts; root-scoped
eviction folded in); `_init_db_cache` uses the helper; `iter_notes`
mtime-drift fixups now carry full rows (body stripped in light mode) into
the upsert list. Pin: `tests/test_corpus_cache_text.py::test_duckdb_bulk_rewrite_shape_and_double_type`
(one DROP/CREATE/INSERT SELECT, no executemany, mtime DOUBLE). Live
measurements A11: 120 s → ~2 s (rewrite) → ~1.5 s warm (all 1,406 mtime
hits). The float32 quantization was the actual regression driver — the
cache writes were persisting all along; the `mtime REAL` column made every
float64 compare misfire. `markdown_lint.py:334` verdict writes remain the
next set-based candidate (unchanged).

Markdown-lint verdict writes EXECUTED (2026-11-24): `helpers/misc/markdown_lint.py`
gained `_bulk_record_verdicts` (DuckDB: one DELETE-IN + one INSERT SELECT
from a registered Arrow relation; SQLite keeps `executemany`) and
`_is_duckdb_conn`; `_record_and_report` branches on engine (the `except
sqlite3.Error` guard broadened to `except Exception` — DuckDB errors are
not sqlite3.Error, and a lost record only costs a re-scan). Pin:
`tests/test_markdown_lint.py::test_duckdb_bulk_verdict_write_shape`.
Cold-run measurement (fake npx, empty sidecar, 1,933 stale files): 17.32 s
→ 1.07 s; warm rerun 0.28 s with 1,933 verdict rows persisted. A12.
