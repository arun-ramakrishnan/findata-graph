---
title: "tests/ fixture & scaffolding consolidation — shared schema, production-DB copy, Flask client, seed data"
status: proposed
filed: "2026-09-03"
executed: null
completed_md: null
area: "tests/ (conftest.py + 44 schema-copy files, ~6 copy-production-DB files, 14 Flask test_client sites, ~86 sys.path-boilerplate files)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# tests/ fixture & scaffolding consolidation

**Date:** 2026-09-03 · **Status:** PROPOSED ·
**Area:** `tests/` — shared conftest fixtures/helpers · schema DDL ·
production-DB copy · Flask test_client · seed data · sys.path boilerplate

## 1. Motivation

The test suite grew integration/API coverage fast, and each new file
re-implemented the same scaffolding rather than reusing the existing
`conftest.py` primitives. The result is the largest raw-duplication cluster
 in the repo: 44 files each define their own `entities`/`graph_edges` DDL
 (`rg -l "CREATE TABLE entities" tests/`; 35 with `graph_edges`),
 6 define the "backup production DB then nuke derived tables" helper,
 14 sites re-roll the same Flask `_open()`/`test_client` monkeypatch, 28
 files seed the same HDFC/ICICI/Infosys entities, and ~86 files repeat the
`sys.path.insert` that `conftest.py` already performs at import time.

This is a **behavior-preserving** consolidation: shared fixtures/helpers
adopt the same semantics the per-file copies already use. It makes the
suite cheaper to grow (new tests import, not re-copy) and removes schema
drift risk when production DDL evolves.

## 2. Census (measured 2026-09-03) and disposition

### 2.1 Schema DDL copy-paste → `tests/schema.py`

44 files define a near-identical `CREATE TABLE entities(...)` +
`CREATE TABLE graph_edges(...)` block with subtle drift (some add `id
INTEGER PRIMARY KEY`, some `CHECK(source != target)`, some FKs; at least
three families: minimal 5-col entities + 6-col edges, full 12-col edges +
`id`/`CHECK`/`weight`, and `relations`-instead-of-edges + FTS variants).
`conftest.py:250-286` already carries `_UNIT_SCHEMA` but only a minority use
it; the rest define local `_SCHEMA`/`_schema_sql()`.

Representative: `test_derive_themes.py:28-46`, `test_edge_writer.py:28-46`,
`test_api_flask_integration.py:26-74`, `test_integration_ts_contract.py:147-201`,
`test_integration_graph_algorithms.py:53-93`, `test_integration_graph_rebuild.py:21-72`,
`test_integration_filesystem_layout.py:30-59`, `test_integration_perf.py:102-129`,
`test_integration_derive_chain.py:45-108`, `test_api_search.py:26-42`,
`test_api_entities.py:16-41`, plus the derive-family unit tests.

**Disposition:** `tests/schema.py` exports named schema variants
(`minimal` = entities + graph_edges, `full` = + entity_tags /
graph_analytics / events, `search` = + note_search FTS5) and a
`build_test_db(path, variant, *, extra_ddl="")` one-liner. Test files
import from it instead of copy-pasting.

### 2.2 Copy-production-DB-then-nuke → `tests/helpers.py::copy_production_db`

6 files define the `sqlite3.connect(DB_PATH).backup(dst)` + DELETE pattern
over the derived-table list (`graph_edges`, `entity_tags`,
`graph_analytics`, `events`, `quotes`, `company_metrics`,
`company_embeddings`, `note_search`, `note_search_meta`) + `DELETE FROM entities` —
identical 9-table tuple in 4/6 (`extract_relations_cli:52-74`,
`near_duplicates:50-80`, `derive_events_cli:65-81` verbatim incl. `# noqa: S608`);
`snapshot_cycle:64-97` is an 8-table subset (drops `note_search_meta`);
`note_writers:73-95` and `maint_chain:113-146` are keep-list variants
(`DELETE ... WHERE NOT IN (SELECT name FROM keep)` per-table + `cited_in`
prune + `DROP TABLE note_search(_meta)` + `VACUUM`; plus a second partial
prune at `near_duplicates:160-163` covering only `note_search*`).

Files: `test_integration_extract_relations_cli.py:52-74`,
`test_integration_near_duplicates.py:50-80`,
`test_integration_derive_events_cli.py:65-81`,
`test_integration_snapshot_cycle.py:64-97` (8-table subset),
`test_integration_note_writers.py:73-95` (keep-list variant),
`test_integration_maint_chain.py:113-146` (keep-list variant).

**Disposition:** `tests/helpers.py::copy_production_db(db_path, *, keep=(), keep_all=False, include_note_search_meta=True, vacuum=False, drop_fts=False)`
centralizes backup + prune; the subset and keep-list variants are
parameterized, not re-litigated per file.

### 2.3 Flask test_client monkeypatch → `tests/helpers.py::flask_test_client`

14 sites across 7+ files re-roll `_open()` (open sqlite, `row_factory =
sqlite3.Row`, patch `A.get_db_connection`, `yield A.app.test_client()`,
restore in `finally`). Worst: `test_api_graph_bundles.py` has **7 copies**
in one file; also `test_api_graph_metrics.py:98-108`,
`test_api_entities.py:86-106`, `test_api_search.py:131-141`,
`test_api_flask_integration.py:157-163`,
`test_integration_ts_contract.py:305-331`,
`test_integration_graph_algorithms.py:495-513`. Three sub-variants force
parameters: restore style (`monkeypatch.setattr` in bundles vs
`saved`/`try`/`finally`), conn tracking (`_open_conns` list + close in
ts_contract/graph_algorithms/conftest vs untracked), and connect fn
(`sqlite3.connect` + `Row` in most vs `helpers.core.db.connect` in
`flask_integration:157-163` vs a Row-less `lambda: sqlite3.connect(...)`
in `entities:102` — centralizing that one to `Row` is a semantic, likely
fixing, change). `ts_contract` additionally stubs `A.get_graph_connection`.

**Disposition:** a context-manager / fixture
`flask_test_client(db_path, *, connect_fn=None, track_conns=False)`
in `tests/helpers.py`; all sites collapse to one call with the variant the
file already used. The Row-less lambda is adopted to `Row` explicitly, not
silently.

### 2.4 Seed data → reuse `conftest._UNIT_ENTITIES`/`_UNIT_EDGES`

28 files mention the HDFC Bank / ICICI Bank / Infosys / Banking /
Technology seed core with column-tuple, edge-type, and tag drift
(`(name,type,sector,slug)` vs 5-col + ticker vs 6-col + tags;
`part_of` vs `belongs_to`; `Infosys small_cap` in ts_contract vs
`large_cap` in conftest/flask_integration; TCS/SBI/Small Co-op/TinyTech
extras) (`test_api_flask_integration.py:76-118`,
`test_integration_ts_contract.py:228-273`, `test_api_entities.py:46-82`,
`test_integration_graph_rebuild.py:98-117`,
`test_integration_filesystem_layout.py:67-84`,
`test_integration_graph_algorithms.py:117-193`,
`test_integration_snapshot_cycle.py:53-61`).

**Disposition:** `tests/schema.py` or `tests/helpers.py` re-exports the
canonical `_UNIT_ENTITIES`/`_UNIT_EDGES`/`_UNIT_TAGS` with per-caller
member/tag/edge-type adapters; files import rather than redefine. A bare
constant import with no adapter is out of scope where values conflict.

**Outcome (surveyed 2026-09-03 — mostly EXCLUDED, see §8):** the seeds
are contract-pinning fixtures, not copies. Only `test_api_flask_integration`
is viable-for-future (same 5 members as canonical, tag-only diffs, no
exact-count assertions); all others excluded with evidence.

### 2.5 sys.path boilerplate → rely on conftest

~86 files repeat `PROJECT_ROOT = Path(__file__).resolve().parents[1]` +
`sys.path.insert(...)`, already performed once by `conftest.py:21-23` before
any module is collected.

**Disposition:** delete the redundant `sys.path.insert` from test files
(conftest already runs first). Keep the `PROJECT_ROOT`/`REPO_ROOT` constant
*only* where a file uses it for path construction (not just import) — it is
load-bearing in at least `ts_contract:35` (`frontend/types/api.ts`) and
`maint_chain` (subprocess cwd) — and have those import from
`conftest.REPO_ROOT`.

**Scope guard:** the sweep targets `test_*.py` files ONLY. The non-test
runner scripts in `tests/` (`run_perf_benchmarks.py`, `run_gate_report.py`)
carry no `sys.path.insert` today (verified — their `REPO_ROOT` defs stay,
both use it for path construction) and must stay conftest-free —
they run standalone outside pytest collection, where no conftest has
executed.

### 2.6 Minor shared helpers

- Bare Flask `test_client()` fixture duplicated in `test_api_docs.py:34-38`,
  `test_security_headers.py:32-35`, `test_api_graph_live.py:19-30` →
  add `bare_client` to conftest.
- `_count(resp)` byte-identical in `test_api_entities.py` + `test_api_search.py`
  (plus `_names`/`_results` accessors) → conftest/helpers.
- Company-note template fns (`test_integration_maint_chain.py:77-94`,
  `test_integration_derive_events_cli.py:40-56`, `test_sync_tags.py:31`) →
  **DEFERRED** (decision 2026-09-03, see §7): purpose-built minimal
  fixtures, not copies — the sync_tags variant deliberately omits dates
  and takes a full tag string, while the other two take a bare sector +
  fixed guidance body. A shared helper needs sector+body+dates params for
  3 call sites (~25 net lines saved) and couples unrelated test files.
- `conn.row_factory = sqlite3.Row` scattered 50× in 21 files → centralize via
  `tests/helpers.py::open_conn(db_path)` returning a Row-factory conn.

## 3. Design

- **`tests/conftest.py`**: keep as the pytest-plumbing home (fixtures,
  session-scoped setup); the plain helpers live in a new
  **`tests/helpers.py`** (module functions, importable by any test without
  pytest fixture injection) — mirroring how `helpers/` splits core modules
  from entry-point scripts.
- **`tests/schema.py`**: the DDL variants + `build_test_db`.
- Mechanical, behavior-preserving: each adoption is a "delete local copy,
  import shared" edit parameterized with the variant the file already used
  (`variant=`/`extra_ddl`, `keep=`, `connect_fn`/`track_conns`, seed adapters);
  the shared helper reproduces the exact statements the copy used (same table
  list per variant, same row factory, same restore order).

## 4. Non-goals

- **Not** re-architecting the integration "Project" classes
  (`_MaintProject`, `_EventsProject`, ...) into one factory in this pass —
  that's a larger, higher-risk change; the shared helpers here are the
  substrate it could later use. Recorded, deferred (optional follow-up).
- **No** change to test *behavior*: fixture semantics, seeded values, and
  assertions stay identical. A shared helper that requires an assertion
  edit is out of scope.
- **Not** touching `findata/` (data vault) or `Mojo/`.
- **No** pytest plugin / conftest auto-load of helpers beyond what's
  needed — keep imports explicit.

## 5. Gates

- Full suite passes unchanged (`make qa`, including the xdist live suite
  and integration tests). Any assertion-level drift means the consolidation
  broke a semantic — roll that file back.
- `rg -l 'sqlite3\.connect\(str\(DB_PATH\)\)' tests/` → only the shared
  helper file (file-level check: the backup call sits on its own line, so
  a single-line `connect…backup` regex would match nothing — the original
  formulation of this gate was vacuously green);
  `rg "row_factory = sqlite3.Row" tests/*.py` drops to the shared
  `open_conn` plus any explicitly-pinned sites.
- One standalone invocation (`pytest tests/<touched-file>`) passes, proving
  the conftest-first `sys.path` assumption holds outside a full-suite run.
- `ruff` on touched files; `make qa` once at arc end.

## 6. Risks

- **Highest-scope-but-lowest-logic-risk** of the four consolidation
  proposals: touches ~90 files, but every edit is a mechanical
  delete-import swap already exercised by the copy it replaces. The suite
  itself is the oracle.
- **Fixture ordering**: moving `sys.path` handling off individual files
  relies on conftest being collected first — true for pytest, but files run
  standalone (`pytest tests/foo.py`) still get conftest at rootdir. Verify
  one standalone invocation in gates.
- **Flask client**: the shared `flask_test_client` must restore
  `get_db_connection` in `finally` exactly as the copies did, and track any
  per-file open-conn cleanup. Covered by the API tests.

## 7. Deferred (record, don't do)

- Integration "Project"-class factory — follow-up after the shared helpers
  land, if operator wants it.
- Cross-language note-template consolidation (tests vs `helpers/pdf`) —
  out of scope; tests only.
- `make_company_note` shared template (decision 2026-09-03) — the three
  call sites differ semantically (dates/no-dates, guidance/generic body,
  bare-sector/full-tag params); unifying couples unrelated fixtures for
  ~25 net lines. Revisit only if a fourth caller appears.

## 8. Execution record (batches 1–3, 2026-09-03)

- **Skeletons:** `tests/schema.py` (canonical table constants, verified
  byte-identical against sources; `minimal`/`full`/`search` variants +
  `build_test_db`) and `tests/helpers.py` (`copy_production_db`,
  `flask_test_client`, `open_conn`, `response_count`, `response_names`).
- **Schema migrated (17 files):** 8-col family (7: filesystem_layout,
  validators, rebuild, derive_chain, graph_algorithms, ts_contract,
  flask_integration — canonical tables only, differing defs kept local);
  derive-minimal (5: themes, edge_writer, cited_in, events, insights);
  relations/4-col (4: api_entities, db_cascades, api_search-entities-only,
  note_embeddings/semantic_neighbors-tags-only).
- **Schema excluded:** order-sensitive bespoke (`sector_hierarchy`,
  `sync_sector_wikilinks`, `parse_newsletter_entities`,
  `perf`/`onager`/`snapshot` 1–2-col); api_search FTS variants (graceful-
  degradation pin); `db_maint`/`snapshot_db` (no canonical tables).
- **Copy-DB migrated (4):** extract_relations_cli, near_duplicates (main;
  second partial prune stays local), derive_events_cli, snapshot_cycle
  (8-table subset, local VACUUM kept). **Excluded:** note_writers/
  maint_chain keep-lists (bespoke keep-SQL, not parameterizable).
- **Flask migrated (14 sites / 8 files):** bundles ×7 via `open_conn`
  lambdas (structure-preserving; contextmanager would re-shape every
  test), metrics/ts_contract/graph_algorithms/flask_integration/search ×2/
  entities via `flask_test_client` (Row-less site preserved explicitly via
  `connect_fn`), parse_newsletter `_open_db` via `open_conn`. conftest's
  reference implementation untouched.
- **Minor:** `bare_client` added to conftest (docs, security_headers
  delegate; graph_live differs — excluded); `_count`/`_names` migrated,
  `_results` left (heterogeneous uses, alias adds nothing).
- **snapshot_cycle failure — FIXED, was a helpers regression, not drift:**
  `test_main_create_then_check_green` failed after the helpers migration
  with "attempt to write a readonly database". Root cause (bisected
  pragma-by-pragma): `db.connect`'s `PRAGMA journal_mode=WAL` on the fresh
  empty backup-dest file breaks the sqlite online-backup API into it;
  raw+raw works. Fix in `snapshot_db.create_snapshot`: backup dest uses
  `db.connect(tmp_path, wal=False)` (recorded in the helpers proposal §6).
  File now 6/6 green. The earlier stash-tree failure remains unexplained
  (likely environmental) — current tree is deterministically green across
  repeated runs.
- **Seed consolidation — surveyed, left out except one viable file:**
  the "same HDFC/ICICI/Infosys seed" premise is refuted by measurement —
  the seeds pin divergent contracts: ts_contract asserts `total_entities
  == 5` / `large_cap == 2` (canonical's No Ticker Co + large_cap Infosys
  break all four); api_entities is purpose-built around Small Co-op/
  TinyTech small_cap filtering; filesystem_layout/graph_algorithms/
  snapshot_cycle/rebuild are custom topologies (`belongs_to`, Alpha/Beta,
  reseed sets). **Viable-for-future:** `test_api_flask_integration` only
  (same 5 members, tag-only diffs, no exact-count assertions) — migrate
  on a future pass with test verification, or leave; everything else
  stays local by decision 2026-09-03.
- **Open for revisit:** sys.path strip
  (~86 files, bulk), `make_company_note`, full `make qa`.
