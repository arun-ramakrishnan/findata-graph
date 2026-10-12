---
title: "Default incremental rebuilds for search indexes + graph fast path"
status: executed
filed: "2026-10-12"
executed: "2026-10-12"
completed_md: "384"
area: helpers/maintenance, helpers/graph
---
# Proposal: Default incremental rebuilds for search indexes

**Filed:** 2026-10-12  ·  **Status:** EXECUTED (2026-10-12)
**Area:** helpers/maintenance/rebuild_script_search.py, rebuild_doc_search.py, helpers/maintenance/rebuild_common.py, helpers/graph/query.py

---

## 1. Motivation

All search and analytics indexes support incremental rebuilds today — but the default on every path is **full** rebuild (DELETE-all + reinsert, or full DuckDB rebuild into a temp), because `--incremental` is registered as `action="store_true"` (default False) in `helpers/maintenance/rebuild_common.py` and the `Makefile` targets (`search-fresh`, `script-search-rebuild`) invoke the rebuild scripts with no `--incremental` flag. On `search-fresh APPLY=1` (run by `make advisory` and `make maint-full`) this rewrites:

- `memory/script_search.db` (~5 MB) row-keyed DELETE + reinsert
- `memory/doc_search.db` (~32 MB) file-granularity rewrite
- the same full-write pattern for note_search / memory_search when incremental isn't enabled

**Impact: ~40 MB+ of rewrites on every advisory/maint gate plus redundant embedding computation.** The fix is to make incremental the default and reserve `--full` for callers that actually need it.

---

## 2. Rebuild-patterns table (recorded)

| DB | Location | Size | Rebuild program | Full-mode write | Incremental? | Default |
|----|----------|------|-----------------|-----------------|--------------|---------|
| script_search | memory/script_search.db | 5.2 MB | rebuild_script_search.py | DELETE + reinsert (`_write_full`); DROP only in `_migrate_schema` (FTS5 can't `ALTER TABLE ADD COLUMN`) | row-keyed diff (`_write_incremental`) | ❌ full |
| doc_search | memory/doc_search.db | 32 MB | rebuild_doc_search.py | DELETE + reinsert (`DELETE FROM doc_search`); DROP only in `_migrate_schema` | file-granularity diff via `doc_search_meta` fingerprints | ❌ full |
| note_search | memory/research.db | 314 MB | rebuild_note_search.py | row-keyed DELETE + INSERT | delta writes | ❌ full |
| memory_search | memory/memory_search.db | 2.6 MB | rebuild_memory_search.py | row-keyed DELETE + INSERT | `_write_incremental` | ❌ full |
| convo_search | memory/convo_search.duckdb | 829 MB | rebuild_convo_search.py | full DuckDB rebuild into temp | `--incremental` + atomic swap | ❌ full |
| graph | memory/graph.duckdb | 48 MB | helpers/graph/query.py `rebuild()` | rebuild into pid temp + atomic `os.replace` | fast path `_patch_dirty_tables` (see §3) | full by default; patch when conditions hold |
| agent_traces | memory/data/agent_traces.duckdb | 186 MB | helpers/analytics/agent_traces.py `load()` | DELETE window (day >= since) + INSERT | `--since`; default loads recent days | incremental |
| model_usage | memory/data/model_usage.duckdb | 2 MB | helpers/analytics/model_analytics.py `load()` | DELETE window (day >= since) + INSERT | `--since` | incremental |

Non-DB build state (no rebuild loop):

| artifact | Location | Size | Build mechanism |
|----------|----------|------|-----------------|
| corpus | memory/corpus.db | 28 MB | Corpus() walk+parse from S1b notes list; per-file mtime cache |
| lint cache | memory/md_lint_cache.db | 336 KB | content-hash cache, helpers/misc/markdown_lint.py |
| embed cache | memory/embed_store.db | 292 MB | pooled `vecdb.embed_cache`, content-hash key, `INSERT OR REPLACE` |
| verify cache | memory/fh_verify_cache.json | 112 KB | JSON cache, helpers/maintenance/enrich_relations.py |

---

## 3. Graph rebuild: atomic swap + the fast path

The graph cache does **not** do in-place DROP + CREATE of the live file. The flow in `helpers/graph/query.py`:

1. `rebuild()` / `fresh_rebuild()` (lines 1442 / 1474) → `_rebuild_via_swap(db_path)` (line 1338).
2. A full DuckDB rebuild runs into a pid-tagged temp file (never the live cache).
3. On success, `os.replace()` swaps the temp into place atomically (readers keep serving the old file until their connection closes — "stale-by-one" contract).

**`_patch_dirty_tables` (line 1206) is the fast path.** Its contract: a COPY of the warm live cache is opened read-write on the temp (never the live file); each input's fingerprint is diffed against stored `fp:*` keys; `DROP TABLE` + recreate is issued for **only** the tables whose inputs changed (entity change → vertices dirty → all edge/substrate tables via dependency closure; edge/vertex changes drop `v_centrality_*` stamps, which are recomputed on demand). It returns `True` when the temp was patched (caller swaps it in), `False` when the caller must rebuild fully.

Why isn't it always the default? In `_rebuild_via_swap` the patch lane only runs when **all** of: `not fresh` (cache exists), `not stamp_centrality` (centrality stamps must refresh every cycle), no live `*.wal` behind the cache file (dirty close → refuse to swap), and `_patch_capable(db_path)`. `rebuild()` (the `make graph-rebuild` entry point) passes `stamp_centrality=True` into its contract, so the fast path is gated off whenever stamps are being refreshed. Proposal: make `_patch_dirty_tables` the default when its preconditions hold, full build as the explicit `--full` fallback.

---

## 4. Design

### 4.1 Flip the incremental default
Move `rebuild_common.py` `build_rebuild_parser()` so a plain invocation is incremental; add `--full` as the opt-out. Concretely:

    make script-search-rebuild        # → incremental (no writes on no-op)
    make script-search-rebuild --full # → full rebuild
    python3 rebuild_doc_search.py     # → incremental by default now

No-change cycles become true no-ops: script_search row-keyed diff means "unchanged rows not rewritten"; doc_search file-granularity diff means unchanged files are not touched.

### 4.2 Acceptance criteria
1. `make search-fresh APPLY=1` rewrites only changed content (verify via `--check` before/after a no-op change).
2. `make script-search-rebuild` / `make doc-search-rebuild` are incremental by default; `--full` still forces full rebuild.
3. `make graph-rebuild` uses `_patch_dirty_tables` by default when conditions hold; `--full` forces full rebuild.
4. Gate runs with an unchanged corpus complete in <1s (down from multi-second rewrites of ~40 MB).
5. No functional or query-visible change — incremental writes produce identical row sets.

### 4.3 Executed — what shipped (2026-10-12)

Shipped as described:

- `build_rebuild_parser()` registers `--incremental` with `default=True` and adds `--full`; the four search rebuilders (doc/script/memory/note) each set `full_help`, so `--full` is advertised on all four.
- `run_rebuild_cli()` resolves `incremental = not check and not full and incremental`, and the mode string now reads `apply+incremental`.
- The library-level `rebuild(..., incremental=False)` defaults are deliberately unchanged. Only the CLI default flipped, so the rebuild cores and every existing test that calls `rebuild()` directly keep exercising the full path.
- No Makefile change was needed: `script-search-rebuild` and `search-fresh APPLY=1` pass no mode flag and therefore inherit the incremental default; `--check` never writes.

**Bug found and fixed during verification.** `--full` is registered conditionally (only when the spec carries `full_help`), but the first cut of the resolver read `args.full` unconditionally. `rebuild_convo_search.py` builds a spec without `full_help`, so every APPLY run crashed before its rebuild:

    AttributeError: 'Namespace' object has no attribute 'full'

That is the `make convo-fresh APPLY=1` path, so a documented refresh workflow was broken. Fixed by reading `getattr(args, "full", False)`.

`tests/test_rebuild_cli_flags.py` pins this regression: it drives the real `run_rebuild_cli` and fails with exactly the original `AttributeError` if the defensive read is reverted. (convo_search is left without `--full` on purpose — its atomic swap already rebuilds the whole DuckDB, so a full/incremental split would be meaningless there.)

Graph criterion 3 was already satisfied before this proposal: `rebuild()` defaults `stamp_centrality=False`, so `_patch_dirty_tables` already runs whenever the cache is warm, cleanly closed (no live `.wal`) and `_patch_capable()`.

### 4.4 Risks
- **Migration**: fingerprint meta tables may be missing/stale → first run after the flip falls back to full build (existing code already handles missing meta via the diff).
- **False negatives**: fingerprint mismatch would under-build → mitigated by content-hash fingerprints matching the existing note_search scheme.
- **Makefile surface**: targets that genuinely need full rebuilds (e.g., post-schema-migration) pass `--full`; audit gate targets after the flip.

---

## 5. Summary

| Index | Size | Before (default) | After (default) |
|-------|------|------------------|-----------------|
| script_search | 5.2 MB | full (DELETE + reinsert) | incremental (row-keyed; often no writes) |
| doc_search | 32 MB | full (DELETE + reinsert) | incremental (file diff; often no writes) |
| note_search | 314 MB | full | incremental (delta writes) |
| memory_search | 2.6 MB | full | incremental |
| convo_search | 829 MB | full | incremental (already: --incremental opt-in) |
| graph | 48 MB | full (build to temp) | full-build-to-temp → patch-dirty fast path default |

**Total I/O avoided on no-op gates: ~40 MB of rewrites + redundant embedding work.**

---
