# Procedure: The doc/ Knowledge Index (doc_search)

**Date:** 2026-08-23
**Proposal:** `doc/improvements/archive/tooling/doc_search_embeddings.md`
**Scope:** build/refresh/query of the FTS5 + embeddings index over the
repo's own `doc/` corpus (including gitignored `doc/local/`). Companion
to `procedures/embeddings.md`, which covers the findata notes index.

## What exists where

| Surface | Store | Refresh |
|---|---|---|
| `doc_search` FTS5 (BM25, section-level chunks) | `memory/doc_search.db` (gitignored sidecar) | this procedure |
| Per-chunk embeddings (JSON column, bge-small) | same sidecar | same run |
| Embed cache (`(sha256(text), model) -> vector`) | `memory/doc_search.db_vec.db` | automatic |
| Model stamp (`embed_model` / `embed_dims`) | `doc_search_info` in the sidecar | same run |

The sidecar is deliberately NOT in `memory/research.db`: `doc/local/` is
private and the published form of the database is the git-tracked
`snapshots/parquet/` export, so keeping doc plaintext out of research.db
makes privacy structural (nothing to leak via a future export-allowlist
edit). The sidecar is derived state — delete it and rebuild.

## One-time setup (per machine)

The indexer reuses the same local bge-small model as the notes index
(setup in `helpers/core/local_embedder.py`; see `procedures/embeddings.md`
§one-time-setup — the model file is shared, no second download). Without
the model the rebuild still succeeds using deterministic 64-dim
pseudo-embeddings (WARNING on stderr) — BM25 works, the cosine leg is
lexical-ish only.

## Build / refresh

```bash
.venv/bin/python3 helpers/maintenance/rebuild_doc_search.py             # full rebuild (convergence)
.venv/bin/python3 helpers/maintenance/rebuild_doc_search.py --incremental # diff-only, ~28x faster warm
.venv/bin/python3 helpers/maintenance/rebuild_doc_search.py --check     # freshness gate
```

**Which mode when** (measured 2026-08-23: warm full ≈ 0.7 s, warm
incremental ≈ 0.02 s at 51 files / 382 sections):

- **After a known doc edit** — `--incremental` is the right tool: it
  reprocesses only mtime-changed/new files and carries the rest verbatim.
- **Periodically / after anything unusual** — plain full rebuild is the
  self-healing convergence pass: it also catches same-mtime content edits
  (incremental's carry is mtime-keyed and would keep them stale) and any
  pre-existing table drift. `make maint-full` step 6c runs FULL for
  exactly this reason (mirrors rebuild-note-search step 6): 0.7 s is
  nothing in the pipeline, and the pipeline run is where convergence is
  guaranteed.
- **Not sure which you need?** `--check` is the arbiter — hash-exact. A
  clean operational loop: `--check` → STALE → `--incremental` → `--check`
  again; if it still reports drift (same-mtime edit), the full run
  converges. `--check` prints FRESH or the exact drift breakdown
  (`N changed, N new, N deleted` + file list + refresh command), **exits
  1 on drift** (house gate doctrine, CI-able as-is), writes no rows, but
  still warms the embed cache. Apply runs report absorbed drift:
  `index was STALE before this rebuild: … — now fresh`.
- A model swap needs no special procedure: the cache is keyed by model
  label, so the next full rebuild re-embeds everything and re-stamps
  `doc_search_info`.

## Query

```bash
# Agent CLI (no server needed): ranked path:line hits + snippets
.venv/bin/python3 helpers/misc/doc_query.py "how does the embed cache work"
.venv/bin/python3 helpers/misc/doc_query.py "rrf fusion" --json --limit 10

# HTTP (same core, powers the app's Docs view)
GET /api/docs/search?q=<free text>            # hybrid BM25+cosine (default)
GET /api/docs/search?q=<text>&hybrid=0        # BM25 only
```

Results are section-level: one file can appear once per matching section,
each hit deep-linked by `anchor` (1-based line of its `##` header). When
the index is missing or stale (`doc/` changed since the last rebuild) the
endpoint degrades to the pre-#107 filesystem scan and reports
`"mode": "scan", "stale": true` — refresh with the rebuild command above.

## Recovery / backup

- Every successful FULL rebuild writes a last-good-state recovery point
  into gitignored `db-backup/`: `doc_search_backup.db` +
  `doc_search_backup_vec.db` (SQLite backup API — WAL-safe, FTS-shadow
  aware). `--check`/`--incremental` don't touch it. A failed rebuild
  rolls back and the previous backup survives.
- Restore: copy both files back into `memory/` (`doc_search.db`,
  `doc_search.db_vec.db`). Or simply rebuild — doc/ is the source of
  truth; the only expensive loss is the embed cache (cold re-embed
  ≈ minutes; the backup makes that instant).
- The git-tracked `snapshots/parquet/` export deliberately does NOT
  cover this sidecar (doc/local/ plaintext must stay structurally
  un-publishable — proposal §2.1). Row order inside the sidecar is
  irrelevant to results (full rebuilds restore canonical order;
  incremental runs append edited files' rows — zero functional impact).

## Corpus lifecycle (new proposals, archive moves)

The index tracks `doc/` content-addressably (per-file mtime + blake2b),
so the proposal lifecycle needs no special handling — but here is what
happens at each transition:

| Event | What the index does |
|---|---|
| New proposal filed under `improvements/proposals/` | Nothing until the next refresh; then indexed as new sections. Until then `/api/docs/search` reports `stale: true` and serves the live filesystem scan — nothing breaks, the new file is still findable via the scan. |
| Proposal edited while live (Status, §10/§11 fills) | Next refresh re-embeds only the changed sections (cache is keyed per-chunk-text, so untouched sections are cache hits). |
| Executed → moved to `improvements/archive/<topic>/` + completed.md entry | Next refresh sees the old path deleted (rows + meta GC'd) and the new path indexed. Sections whose text survived the move verbatim hit the embed cache — the move itself re-embeds nothing. completed.md's new entry re-embeds as usual. |
| Any time between a change and the next refresh | Endpoint degrades to scan (`mode: "scan", stale: true`); the CLI warns on stderr and answers from the slightly-outdated index. `make maint-full` (step 6c) or a manual rebuild converges. |

**Eval-label caveat:** `helpers/misc/embed_eval_questions.json` (`docs`
section) references docs by path. When a referenced doc is archived
(this arc's own proposal was the first case: `improvements/proposals/
doc_search_embeddings.md` → `improvements/archive/tooling/`, dsem-04's
`expect` moved in the same change), update its `expect` list to the
archive path in the same change — otherwise that question starts missing
by label, not by ranking.

## Gates & perf coverage

`make perf` (tests/run_perf_benchmarks.py) carries two doc-search
entries: `rebuild_doc_search --check` (budget 2.0 s; warm ≈0.5 s) and a
`doc_query` hybrid query (budget 3.0 s; warm ≈0.6 s incl. model load).
Both run as real subprocesses against the live tree — the only automated
exercise of the scripts' bootstraps — and the `--check` entry is the
only automated consumer of its exit-code contract: **a stale or missing
sidecar fails `make perf`** (rc 1 with the drift breakdown naming the
refresh command), by the same live-state doctrine as
`rebuild_note_search --check`. It is deliberately NOT in `make qa`:
doc/ edits (proposals!) land between maint cycles and would redden qa
constantly; perf is the single home for rc + wall-clock budgets. The
chunker (`_split_sections`) and MATCH generator (`fts_match_expr`) have
Hypothesis property tests in `tests/test_fuzz_rebuild_doc_search.py`
(`make fuzz`).

## Agent sessions

`AGENTS.md` (repo root) directs fresh LLM sessions to query
`helpers/misc/doc_query.py` before reading doc files — see that file for
the exact contract (ranked `path:line` hits, `--json`, never read
`completed.md` whole).

## Eval

```bash
.venv/bin/python3 helpers/misc/embed_eval.py docs
```

scan vs BM25 vs hybrid recall@5 over the labeled docs question set in
`helpers/misc/embed_eval_questions.json` (`docs` section). Report-only,
always exits 0.
