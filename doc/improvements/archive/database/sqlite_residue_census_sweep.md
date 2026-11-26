---
title: "SQLite-residue census + DuckDB-cutover completion sweep"
status: "executed"
filed: "2026-11-25"
executed: "2026-11-26"
completed_md: "573"
area: "helpers/maintenance/* + helpers/graph/* + helpers/core/* (SQLite-era defaults and dialect residue post research-store cutover)"
---

# SQLite-residue census + DuckDB-cutover completion sweep

The 2026-11-25 arc ran the first full maint-full TIER2 chain against the
DuckDB canonical store and hit eight distinct SQLite-era failure classes.
Each was fixed at the crash site, but the survey greps show the residue is
widespread: ~61 helper files carry `memory/research.db` references with no
DuckDB awareness marker. This proposal completes the cutover: census the
residue precisely, fix what is actually broken, and add a static leg so
the class cannot regrow.

## Why now

The cutover (#551) swapped the store engine; the maint chain is the first
workload that exercises every helper CLI against it. Today's chain failed
three times on three *different* members of the same disease family
(sync_coverage_tags dead default, fold_identifiers sys.path order,
derive_hyperedges scalar-SQL dialect) — each found only by running the
chain. Waiting for crashes to find the rest is the expensive order.

## Evidence (all from the 2026-11-25 arc)

- **E1 — dead default**: `sync_coverage_tags.py` `--db` default
  `memory/research.db` (deleted in build-route P4) → `ERROR: database not
  found`; chain stopped at step 3.
- **E2 — import order**: `fold_identifiers.py` imported `helpers.*` before
  its REPO_ROOT sys.path insert → `ModuleNotFoundError` on direct CLI runs
  (only worked under maint.py's prepared environment).
- **E3 — per-row write cost**: funnel-proxy per-row upsert measured
  ~20 ms/row (7.5 min per 22k-row metric; 31+ min apply block); native
  DuckDB `executemany` 22k plain INSERTs = 145.5 s (6.6 ms/row) on a warm
  scratch table. The batched diff-write path landed in the gate_fixes
  collector: 0.47 s fresh / 1.57 s idempotent for the same 22k population.
- **E4 — prune exonerated**: the suspected 22k-literal `NOT IN` DELETE
  runs 1.44 s; a temp-table anti-join is no better (4.84 s). The prune
  shape is fine on DuckDB.
- **E5 — scalar dialect**: `min(entity, counterparty_entity)` (SQLite
  2-arg scalar form) → DuckDB `BinderException: GROUP BY clause cannot
  contain aggregates`; SQLite 3.50.4 has no `least`/`greatest`, so CASE
  pairs are the only dual-engine form (verified identical on both).
- **E6 — aborted implicit transaction**: a caught ParserException inside
  DuckDB's implicit transaction poisons everything after it
  (`TransactionContext: Current transaction is aborted`). Best-effort
  try/except restamps must pre-guard instead of probe-by-throwing.
- **E7 — snapshot axis as recovery**: company_embeddings (dropped by the
  chain crash) was restored byte-exact from the same-day parquet capture
  (1,213 rows) — the parquet axis is a viable restore source for
  dropped-table incidents.
- **E8 — residue size**: grep census: ~61 helper files reference
  `research.db` with zero `is_duckdb_conn`/`.duckdb` markers (crude —
  includes docstring-only mentions; S0 produces the accurate table).
- **E9 — first-run framing**: the 2026-11-20 bump-log row shows the last
  embeddings populate ran on SQLite; the cutover conversion carried 35
  tables, and today's chain was the first-ever DuckDB `_ensure_schema` for
  several helpers.
- **E10 — silent verify in the create path**: `snapshot_db.py:2444-2448`
  (`_cmd_create`'s `_sqlite_parquet_worker`) reduces
  `_verify_parquet_sqlite_side(...)["mismatches"]` to a bare bool — on the
  2026-11-25 chain a real mismatch (`sqlite/company_embeddings: MISSING ON
  LIVE`) surfaced as rc=1 with **zero diagnostic lines**; the MISMATCH
  warnings only print in the `--check`/`verify_parquet_snapshot` path. The
  same shape applies to the `_duckdb_parquet_worker` (:2450-2458) and
  `_sources_parquet_worker` (:2460+) call sites.

## Grounded current state

- The canonical research store is `memory/research.duckdb`; the SQLite
  host is deleted (build-route P4). `helpers.core.db.connect(None)` is the
  selector-aware door (canonical DuckDB by default, SQLite under
  `FINDATA_RESEARCH_ENGINE=sqlite`).
- Two house fix patterns exist and are proven in this arc:
  1. canonical-default: `--db` default `None` → `db.connect(None)`,
     existence guard kept only for explicit paths (sync_coverage_tags,
     fold_identifiers, foreign_links_generate, and four sibling probes);
  2. sibling-resolution: `.db` default kept, resolved to the `.duckdb`
     sibling when the host is missing (helpers/core/sync_tags.py).
- Already DuckDB-aware (no work needed): the six maintenance probes
  re-plumbed in #570, helpers/core/sync_tags.py, derive_events,
  derive_hyperedges, hyper_*, query.py, csr.py.

## Slices

### S0 — census

Classify every grep-sweep hit (method below) into: `broken-runtime` (the
CLI fails standalone today), `dead-default` (default path dead but another
resolution saves it or the path is overridable), or `text-only`
(docstring/comment). Probe each CLI standalone: `--help` plus its
read-only mode (`--check`/dry-run) against the canonical store. Produce
the classification table in this document.

**Accept**: table complete for all S8 hits; every `broken-runtime` row has
a repro command; no wholesale file reads (query/grep only).

### S1 — canonical-default sweep

Apply pattern 1 (canonical-default) to every `broken-runtime`/`dead-default`
maintenance/graph CLI from S0. Keep per-engine guards where a script also
writes (fold_identifiers-class RW vs sync_coverage_tags-class RO).
Standone-run `--check`/dry-run green per touched CLI.

**Accept**: every S0 `broken-runtime` row re-probed green standalone;
`make maint` + the maint-full TIER2 chain still green (chain is the
regression harness); targeted tests for any touched helper with a test
file.

### S2 — residue-regrowth static leg

Add a static_checks leg encoding today's found idioms so the class cannot
regrow: (a) a script under `helpers/` that imports `helpers.*` must have
its REPO_ROOT sys.path insert above the first such import; (b) no
2-argument `min(`/`max(` inside SQL string literals; (c) no bare
`IS NOT ?`; (d) `PRAGMA defer_foreign_keys` only under an
sqlite-gated context. Test the leg against seeded-bad fixtures.

**Accept**: leg green on the repo as-is; seeded-bad fixture caught; runs
in the same budget class as the other static legs.

### S3 — #570 rule addendum (no churn)

Append E3/E4 to the archived #570 as an evidence addendum: the
`executemany` cold-lane rule stands, with today's measured constants
(6.6 ms/row executemany, ~20 ms/row per-statement upsert through the
funnel proxy, prune shape exonerated). No code change.

**Accept**: addendum merged; no rule overturned.

### S4 — snapshot create-path verify logging (small)

Make `_cmd_create`'s three parquet workers log every mismatch line they
fold into the bool (E10): each entry of the `mismatches` list goes out at
WARNING through the same `logger` already passed to
`_verify_parquet_sqlite_side`/`_verify_parquet_duckdb_side`
(snapshot_db.py:2444-2478). The `--check` path keeps its current output;
create gains diagnostics only on the failure it already reports via rc.

**Accept**: seeded mismatch (drop a tracked table between export and
verify on a scratch store) prints `sqlite/<t>: ...` in create output;
`--check` output unchanged; snapshot gates still green.

### S5 — embed-gc company ref on the DuckDB research store (small)

**E11 (2026-11-26)**: `make embed-gc` is broken by default — it aborts with
`reference index missing: company (.../memory/research.db) — refusing to
GC; a partial reference set would delete live cache rows`. Two defects:
`gc_embed_cache.py:269` still points the `company` ref at the retired
SQLite host (the cutover left this path behind in dee652750), and
`_ref_basis_text()` at `:315-317` rejects every non-`sqlite3.Connection`
reference, so a path-only fix would just swap one abort for `company:
text_db needs a sqlite reference index`. No basis-helper port is needed:
`helpers/graph/embeddings.py:209-266` only calls `conn.execute(...)` and
reads the note file, and an in-memory-patched read-only survey matched
1,212 / 1,213 live company cache rows against `research.duckdb`.

Work, in this order:

1. Repoint `Ref("company", ...)` to `memory/research.duckdb` (where
   `company_embeddings` / `entities` now live). Do **not** recreate the
   SQLite host (`doc/improvements/pending.md:1351-1357` ruling).
2. Make `_ref_basis_text()`'s `text_db` guard engine-agnostic (duck-type
   `execute`) and drop the now-unused `import sqlite3`. Keep the fail-closed
   behaviour at `:340-343`: any basis exception still aborts the whole GC.
3. Pin both in `tests/test_embed_gc.py`: the production ref's path/stamp,
   and a seeded tmp `research.duckdb` exercised through `_read_refs()`.

**Scope note (why this is not a space win)**: the read-only survey found
369 dead rows (~1 MiB logical) of 257,068 (795.5 MiB). DuckDB does not
return file pages on DELETE (`gc_embed_cache.py:13-15,516-518`), so the
on-disk reclaim from GC is ~0 — `memory/embed_store.duckdb` stays ~796 M.
This slice restores a broken maintenance path; a `COPY`-rebuild of
`embed_cache` for real reclamation is a separate operator-gated slice.

**Accept**: `make embed-gc` (report mode, no `APPLY=1`) reaches the survey
without `reference index missing` / `refusing to GC` — exit 1 with dead
rows is the documented report-mode signal, not a failure of this slice;
`tests/test_embed_gc.py` green; no live store written during validation.

## Results — S0 census + S1 sweep (2026-11-26)

### Method

- Sweep: `grep -rl "research\.db" helpers/ --include="*.py"` = **106
  files**; code-assignment filter (a `research.db` line matching
  `default=|DEFAULT_*DB|DB_PATH|add_argument|connect(|= REPO /|path = REPO|
  memory/research`) → **84 code-context files**, 22 text-only.
- Runtime probe per suspect (the 20 code-context files with a `__main__`
  CLI and NO canonical-door/duckdb-awareness signal): `--help`, then the
  advertised read-only mode (`--check`/`--dry-run`/report default). The
  probe driver deleted an empty `memory/research.db` whenever a
  `sqlite3.connect` recreated the retired host — 2 offenders, both fixed
  below. Non-suspects were classified statically (canonical door import
  `from helpers.core(.db)? import … connect`, or `is_duckdb_conn` /
  `research.duckdb` signal) — no wholesale file reads.

### broken-runtime → FIXED in S1 (maintenance/graph scope)

| file | pre-fix repro + observed |
| --- | --- |
| `helpers/maintenance/hyperedge_cooccurrence_lens.py` | `.venv/bin/python3 helpers/maintenance/hyperedge_cooccurrence_lens.py` → Traceback + **created an empty `memory/research.db`** (bare `duckdb.connect(dead .db default)`; `DEFAULT_DB` at :40) |
| `helpers/maintenance/name_token_candidates.py` | same shape (`--db` default `str(_REPO_ROOT / "memory" / "research.db")` :501; same create hazard) |

Fix (sync_coverage_tags-class, pattern 1): `--db` `type=Path` default
`None` → `helpers.core.db` `connect(args.db, read_only=True)`; existence
guard kept only for explicit paths; dead default constants and the
now-unused `duckdb`/`memory_cap_config` imports removed.

Post-fix evidence: default run rc=0 against the canonical store (lens:
country cohort 21 labels / 930 members; ntc: df-sorted candidate list);
explicit `--db memory/nope.db` → `ERROR: database not found: …` rc=1 (no
create); targeted tests `test_hyperedge_cooccurrence_lens.py` +
`test_name_token_candidates.py` + `test_fuzz_name_tokens.py` = 22 passed;
`make maint` PASS (8/10 ok + 2 skipped served-store); retired host still
absent.

### broken-runtime — bench-tier (out of S1 scope per slice text; left as-is)

Module-level `sqlite3.connect("file:…/research.db?mode=ro", uri=True)`
crashes at import (read-only URI → no create hazard):
`helpers/bench/embed_pool_probe.py`, `embed_runtime_bench.py`,
`embed_token_cap.py`, `note_deep_probe_candidates.py`,
`embed_model_trial.py` (`_RO_CONN` at module scope). Also:
`helpers/bench/fts_duckdb_parity.py` (`--db` default dead; default run
rc=1), `helpers/bench/company_neighbors_base_probe.py` (`DB_PATH` dead;
leg-CLI, no argparse), `helpers/bench/note_deep_probe.py` (default run
needs legacy store rows: "building pre-sectioning simulation…").
`helpers/bench/note_ab_granite.py` touches only its own `/tmp` sandbox
copy — no repo-store contact, no work.

### works — pattern-2 sibling resolution (no work)

`helpers/graph/csr.py:54` (`research_store_connect`, S6 selector;
`--check` rc=0 "CSR: fresh (gen=1145755)"),
`helpers/maintenance/family_label_coverage_check.py:310-312`,
`helpers/maintenance/group_discovery_inventory.py:160-162`,
`helpers/maintenance/group_discovery_growth_monitor.py:227-229` — raw
`.db` defaults resolved to the `.duckdb` sibling; probes green.
`helpers/core/sync_tags.py` per Grounded state.

### works — canonical/aware (no work)

Probed green standalone: `helpers/maintenance/maint.py` (`--dry-run` rc=0,
10-step plan), `helpers/maintenance/migrate_embedding_blob.py` (`--check`
rc=0; "memory/research.db note_search ABSENT (skipped)" — audits absence
by design), `helpers/maintenance/build_route_p4.py` (audits absence by
design), `helpers/misc/answerability_probe.py` (rc=0). The remaining
code-context files carry the canonical door or a duckdb-awareness signal
and are exercised green by the chain / `make maint` / search lane:
`algorithms.py`, `query.py`, `scipy_bridge.py`, `csr.py`, `embeddings.py`,
`derive_*`, `hyper_*`, `l1_*`, `snapshot_db.py`, `rebuild_*_search`,
`seed_concepts.py`, `seed_nic2008.py`, `fold_identifiers.py`,
`sync_tags.py`, `vec_search.py`, `db_maint.py`, `store_sweep.py`,
`db.py`/`db_engine.py` (the door itself), et al.

### text-only (no work)

Comment/docstring/help-text mentions only, no runtime path:
`hyper_predict_bank.py`, `corpus.py`, `embed_cache.py`, `git_state.py`,
`derive_indices.py`, `derive_relational_insights.py`, `enrich_relations.py`,
`suggest_relations.py`, `hyper_hif.py`, `maint.py` (comment line),
`seed_note_gemma_cache.py`, `master_query.py`, `search_tui_app.py`,
`swap_fk_integrity.py`, `swap_prep_census.py`.

### S2 — residue-regrowth static leg (DONE 2026-11-26)

`check_sqlite_residue_idioms` in `helpers/validators/static_checks.py`
(registered as "SQLite residue idioms", **blocking**), encoding the four
idioms from this arc: (a) a column-0 `from/import helpers` above the
module's `sys.path.insert` (E2 shape), (b) 2-argument scalar `min/max(`
inside SQL string constants (E5), (c) bare `IS NOT ?`, (d)
`defer_foreign_keys` outside an sqlite-gate window (±6 lines of the
observed gate idioms). Scans AST string constants so comments never enter
the tree; **docstrings are excluded** (formula prose like
`max(|q|, |e|)` false-positived in the first draft); `TYPE_CHECKING`
imports are exempt (they never execute); the leg's own file is exempt (it
cites every pattern in its finding messages).

The leg found **4 real (a) violations on landing** — `exchange_sync.py`,
`related_party_sync.py`, `shareholding_sync.py`,
`bench/note_deep_probe_candidates.py` had a `helpers.*` import above the
insert — all fixed mechanically (insert moved up, `# noqa: E402` on the
imports that follow a statement). Green as-is after those fixes.

Tests (5, seeded-bad per acceptance): all-four catch, clean-file shape
(Python `min(a, b)` and `IS NOT NULL` must not trip), the three
false-positive classes, gated-pragma legal case, self-site exemption.
Full `static_checks.py` run: 9 s total (same budget class as before the
leg), 128 + 5 tests pass.

### Census residuals (user-ordered) — DONE 2026-11-26

The 8 bench-tier broken rows are repointed to the post-cutover stores
(`note_search` → `memory/note_search.duckdb` sidecar, `doc_search` →
`memory/doc_search.duckdb`, `entities`/`company_embeddings` → canonical
`research.duckdb`; embeddings decode via `helpers.core.vec_codec.load_vec`
— blobs, not JSON; throwaway sqlite FTS simulation stores stay sqlite):
`embed_pool_probe`, `embed_runtime_bench`, `embed_token_cap`,
`embed_model_trial` (corpora + stamp-pinned `_ro_conn`),
`note_deep_probe_candidates` (live sections + import order),
`fts_duckdb_parity` (sidecar corpus, BIGINT ids, subquery `match_bm25`
for the new DuckDB alias rule, FTS5 arm against a throwaway production-
shape sqlite copy, vector leg stamp-aware with BM25-only degradation),
`company_neighbors_base_probe` (dominant-stamp model filter),
`note_deep_probe` (entities + note-count reads). The 4 dead-default rows
got live defaults (`research.duckdb`; sibling-resolution blocks retained
for explicit `.db` paths): `csr.py`, `family_label_coverage_check.py`,
`group_discovery_inventory.py`, `group_discovery_growth_monitor.py`.

Probes: `fts_duckdb_parity --limit 2` green end-to-end (19,379-row
corpus, stamp `embeddinggemma-2-q8_512` dim=512, recall 1.00 both arms);
embed sample probes return live rows; `_live_vectors()` decodes
1,213 × 512; `csr --check` fresh (gen=1145755); family gate PASS; group
tools print. Targeted tests for every touched file with a suite: **132
passed**. Retired host still absent; `make maint` PASS from the S1 run
stands (none of these files are maint steps).

## Validation loop

- md lint (markdownlint-cli2 0.23.2) on touched doc files
- frontmatter schema (all six keys; status `proposed` while live)
- static/frontmatter checks + the S2 leg once landed
- `make search-fresh APPLY=1 ARGS=--allow-stale` after doc edits (advisory)
