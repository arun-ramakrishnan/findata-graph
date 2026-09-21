# Procedure: Search surfaces — indexes, query, TUI

**Date:** 2026-09-23 (merged from `doc-search.md`, `script-search.md`,
`search-tui.md`)
**Scope:** build/refresh/query of every search index this repo keeps
fresh (`doc_search`, `script_search`, `note_search`), the shared
`--check` contract, and the terminal front door (`search_tui`).
Embedding **apply** / model upgrades (company embeddings + note vectors
write path) stay in `doc/procedures/embeddings.md` — that procedure owns
the write side; this one owns freshness and query.

| Former file | Section here |
|---|---|
| `doc-search.md` | §doc_search · §Corpus lifecycle · §Eval |
| `script-search.md` | §script_search |
| `search-tui.md` | §Search TUI |

---

## Surfaces at a glance

| Surface | Store | Corpus | Query |
|---|---|---|---|
| `doc_search` FTS5 (BM25, section-level) + per-chunk embeddings | `memory/doc_search.db` (gitignored sidecar) | `doc/**` (incl. gitignored `doc/local/`) | `doc_query.py`, `GET /api/docs/search` |
| `script_search` FTS5 + embeddings, hybrid RRF | `memory/script_search.db` (gitignored sidecar) | `helpers/**`, `tests/**`, `app.py`, Makefile | `script_query.py` |
| `note_search` FTS5 (+ vec0 mirror in research.db) | inside `memory/research.db` | `findata/**` markdowns | `GET /api/search?hybrid=true`, TUI notes lane |
| Embed cache `(sha256(text), model) -> vector` | pooled `memory/embed_store.db` (schema `vecdb`) | shared by all indexers | automatic |
| TUI (consumer only — builds no index) | reads the three above + `ripwire` + `rg` | — | `make search-tui` |

Sidecars are deliberately NOT in `research.db`: `doc/local/` is private
and the published DB is the git-tracked `snapshots/parquet/` export, so
keeping doc/script plaintext out of research.db makes privacy structural.
Sidecars are derived state — delete and rebuild.

---

## Shared `--check` contract

All three rebuilders share one drift-gate contract (`make search-fresh`
runs them sequentially; the advisory gate runs three parallel rows):

| Rebuilder | Index | `--check` exit 1 on |
|---|---|---|
| `rebuild_doc_search.py` | `doc_search` | content-hash / mtime / count drift |
| `rebuild_script_search.py` | `script_search` | same |
| `rebuild_note_search.py` | `note_search` | FRESH/STALE drift report (#164, 2026-08-26) |

`--check` writes no index rows (it may warm the embed cache) and exits 1
on drift with the exact refresh command in the output — house gate
doctrine, CI-able as-is.

```bash
make search-fresh              # check all three (every check runs even if one fails)
make search-fresh APPLY=1      # refresh all three instead
```

**Which mode when**

- **After a known edit** — `--incremental` (doc/script; reprocesses only
  changed files; script still re-extracts every unit but only *writes*
  row-tuple diffs).
- **Periodically / after anything unusual** — plain full rebuild: the
  self-healing convergence pass (catches same-mtime content edits and
  table drift). `make maint-full` step 6c runs FULL for doc_search for
  this reason.
- **Not sure?** `--check` is the arbiter — hash-exact. Loop: `--check` →
  STALE → `--incremental` → `--check`; if still dirty, full run.
- **Model swap** — no special procedure: embed cache is keyed by model
  label; next full rebuild re-embeds and re-stamps the sidecar info
  table. The apply/write path is `embeddings.md`.

---

## doc_search — the `doc/` knowledge index

### One-time setup (per machine)

Reuses the same local bge-small model as the notes index (setup in
`helpers/core/local_embedder.py`; see `embeddings.md` §one-time-setup —
model file is shared, no second download). Without the model the rebuild
still succeeds with deterministic 64-dim pseudo-embeddings (WARNING on
stderr) — BM25 works, the cosine leg is lexical-ish only.

### Build / refresh

```bash
.venv/bin/python3 helpers/maintenance/rebuild_doc_search.py              # full (convergence)
.venv/bin/python3 helpers/maintenance/rebuild_doc_search.py --incremental
.venv/bin/python3 helpers/maintenance/rebuild_doc_search.py --check
```

Measured 2026-08-23: warm full ≈ 0.7 s, warm incremental ≈ 0.02 s
(51 files / 382 sections at the time — scale with corpus). Apply runs
report absorbed drift: `index was STALE before this rebuild: … — now
fresh`.

### Query

```bash
.venv/bin/python3 helpers/misc/doc_query.py "how does the embed cache work"
.venv/bin/python3 helpers/misc/doc_query.py "rrf fusion" --json --limit 10

GET /api/docs/search?q=<free text>            # hybrid BM25+cosine (default)
GET /api/docs/search?q=<text>&hybrid=0        # BM25 only
```

Results are section-level: one file can appear once per matching
section, each hit deep-linked by `anchor` (1-based line of its `##`
header). Missing/stale index → filesystem scan, `"mode": "scan",
"stale": true`.

### Recovery / backup

- Every successful FULL rebuild writes a last-good recovery point into
  gitignored `db-backup/`: `doc_search_backup.db` (SQLite backup API —
  WAL-safe, FTS-shadow aware). `--check`/`--incremental` don't touch it.
  Failed rebuild rolls back; previous backup survives.
- Embed-cache recovery is CENTRAL (2026-08 consolidation): cache lives in
  shared `memory/embed_store.db`, covered by maint step-1 twin
  (`db-backup/embed_store_backup.db.zst`) and the snapshot zstd stream.
  Restoring `doc_search.db` alone loses nothing expensive — rows re-warm
  from the pooled cache.
- Restore: `zstd -dc db-backup/doc_search_backup.db.zst > memory/doc_search.db`.
  Or rebuild — doc/ is the source of truth; only expensive loss is a cold
  pool (minutes of re-embedding).
- Git-tracked `snapshots/parquet/` deliberately does NOT cover this index
  (doc/local/ plaintext must stay structurally un-publishable — proposal
  §2.1).

### Corpus lifecycle (new proposals, archive moves)

Index tracks `doc/` content-addressably (per-file mtime + blake2b):

| Event | What the index does |
|---|---|
| New proposal under `improvements/proposals/` | Nothing until next refresh; then indexed as new. Until then `/api/docs/search` reports `stale: true` and serves the live scan. |
| Proposal edited while live | Next refresh re-embeds only changed sections (cache keyed per-chunk-text). |
| Executed → `improvements/archive/<topic>/` + completed.md entry | Next refresh GC's old path rows, indexes new path. Verbatim text hits embed cache. |
| Between change and refresh | CLI warns + answers from stale index; endpoint degrades to scan. `make maint-full` (6c) or manual rebuild converges. |

**Eval-label caveat:** `helpers/misc/embed_eval_questions.json` (`docs`
section) references docs by path — when a referenced doc is archived,
update `expect` in the same change or the question misses by label, not
ranking.

### Eval

```bash
.venv/bin/python3 helpers/misc/embed_eval.py docs
```

scan vs BM25 vs hybrid recall@5 over the labeled docs question set.
Report-only, always exits 0.

---

## script_search — the code-surface index

### What it answers

Intent questions grep can't answer: *which script audits relation
diffs*, *which test covers the yfinance driver*, *what does `make qa`
actually run*. Each row is one script / test / make target, composed
from the module docstring (purpose = first paragraph), regex-extracted
argparse flags, AST-import-derived `tested_by`, and Makefile wiring —
zero new authoring burden.

### Build / refresh

```bash
.venv/bin/python3 helpers/maintenance/rebuild_script_search.py              # full
.venv/bin/python3 helpers/maintenance/rebuild_script_search.py --incremental
.venv/bin/python3 helpers/maintenance/rebuild_script_search.py --check
make script-search-rebuild
```

Measured 2026-08-25: warm full ≈ 1 s with near-100% embed-cache hit rate;
first cold build embeds ~230 rows. Incremental always re-extracts every
unit (cross-file inputs) but only WRITES changed row tuples. Plain full
is the convergence pass and writes `db-backup/script_search_backup.db` +
`_vec`.

### Query

```bash
.venv/bin/python3 helpers/misc/script_query.py "audit relation diffs"
.venv/bin/python3 helpers/misc/script_query.py "yfinance" --kind test
.venv/bin/python3 helpers/misc/script_query.py "integrity" --area misc --json
.venv/bin/python3 helpers/misc/script_query.py "what does make qa run" --kind make
```

Filters post on the UNINDEXED `kind` (`script|test|make`) and `area`
columns. Stale index warns on stderr and still answers; missing index is
hard exit 1 with the build command. Division of labor: this index is
INTENT (what is it for, what runs it); `ripwire` is STRUCTURE (symbols,
callers). No HTTP endpoint yet (S4 deferred).

### Extraction contract (what a row promises)

- `purpose` — first paragraph of the module docstring; filename fallback.
  The `## annotation` for make rows.
- `cli:` / `subcommands:` — regex over `add_argument("--x")` /
  `add_parser("x")`. Discovery surface only; `--help` is ground truth.
- `tested_by:` / `imports:` — **AST imports only** (no grep-mention:
  comments/strings would make the map noisy).
- `make:` / `scripts:` — bidirectional Makefile wiring.
- `defs:` — top-level def/class names, enrichment only.

### Agent sessions

`AGENTS.md` directs sessions to query `script_query.py` before guessing
filenames — and before writing any new helper/test (it may already
exist).

---

## Search TUI (`search_tui`)

**Scope:** one full-screen terminal front door over every search surface
the repo already keeps fresh. No new index — the TUI is a consumer of
the three `search-fresh` sidecars plus two stateless structural tools.

### Lanes

| # | Lane | Backend | Notes |
|---|------|---------|-------|
| 1 | docs | `helpers/misc/doc_query.py --json` | FTS5 + embeddings over `doc/` (incl. `doc/local/`) |
| 2 | scripts | `helpers/misc/script_query.py --json` | helpers/tests/make/mojo; `make` rows jump to the Makefile rule |
| 3 | notes | `note_search` FTS5 in `memory/research.db` | bm25-ranked keyword search over the findata vault |
| 4 | code | `ripwire` | default `--for=`; verbs below |
| 5 | literal | `rg` | gitignore-aware, `path:line:text` rows, capped at 300 |
| 6 | reports | `outputs/*_report.md` | append-only run reports; empty query = per-report overview |

Code-lane verbs: `callers:SYM`, `impact:SYM`, `grep:STR` (always
`--grep-in=any`), `recall: question`. Else → `--for=<query>`.

Reports-lane verbs: `qa:`, `advisory:`, `integration:`, `maint:`,
`perf:`, `integrity:`, `verify:` select a report file. Verb alone →
latest run; verb + text → matches across runs; bare text → matches
across latest runs of every report; empty → one overview row per report.
Unknown `x:` prefixes are plain text.

### Keys

| Key | Action |
|-----|--------|
| type + enter | run the query (enter-to-search; no type-to-search) |
| `1`-`6` | switch lane and re-run (stale rows clear immediately) |
| tab / shift+tab | cycle panes: query → tabs → results → preview |
| `m` | toggle hybrid / bm25 match mode (docs & scripts lanes) |
| `t` | cycle theme (github-dark → github-light → solarized-dark → high-contrast) |
| `T` | theme picker screen (enter applies + persists) |
| `h` | call chain of the selected code symbol → table rows |
| click/enter on a chain row | drill into that caller/callee |
| `b` | back — unwind the chain stack |
| `i` | index monitor (c/C deep-check, r/R rebuild, esc close; busy indexes animate) |
| `V` | report screen — comprehensive view over `outputs/` reports |
| `d` | database screen — schema tree + SQL + results (read-only) |
| alt+`d` | database screen from anywhere |
| enter (overview row, reports lane) | open the report screen |
| `/` | focus the query line |
| enter (normal row) | open the hit: markdown via `glow -p`, else `$VISUAL`/`$EDITOR` |
| `e` | open in the editor even for markdown |
| `y` / `Y` | copy path / `path:line` (OSC52 + wl-copy/xclip/xsel/pbcopy) |
| `q` / ctrl+c | quit |

Status bar shows each index's age — the `search-fresh` contract: lanes
are only as fresh as the last `make search-fresh APPLY=1`. Query /
call-chain runs carry a loading overlay; busy index-monitor rows animate
with elapsed-time notes.

### Database screen (`d` / alt+`d`)

Three panes: schema tree (left), SQL editor (middle-top, highlighted),
results grid (middle-bottom). Read-only by construction: one
short-lived read-only connection per query (SQLite `mode=ro`, DuckDB
`read_only=True`), closed after each run — no lock held across the
session. 200-row cap; SQLite statements abort after 2 s via progress
handler; every query runs in a worker thread. Needs the `tui` extra
(`textual` + `tree-sitter` + `tree-sitter-sql`).

| Key | Action |
|-----|--------|
| enter (tree row) | run `SELECT *` (table) or one column (leaf), capped |
| f5 | run the editor SQL (enter is a newline — multiline supported) |
| type prefix + tab | complete from keywords + tables + `table.column` |
| `P` / `N` | older / newer query (per-store history in `~/.config/search_tui/`; ctrl+`p` is the app command palette) |
| `/` | focus the row filter (fuzzy subsequence, live) |
| enter (filter) | back to the editor |
| `v` | inspect the row (`col: value` lines under the grid) |
| `y` / `Y` | yank cell / row (tab-separated) |
| `j`/`k`/`h`/`l` | vim-style move (expand/collapse on `l` in tree) |
| `s` | switch store: `research.db` (SQLite) ↔ `sources.duckdb` (DuckDB) |
| `[` / `]` | widen/narrow the schema tree |
| `-` / `=` | shrink/grow the SQL box |
| esc / `q` | safe harbor — focus the tree (never exits the modal) |
| alt+`q` | close (only way out; ctrl+q is Textual force-quit) |

Single-key actions are inert while the editor or filter has focus.
Footer shows modal keys only. Safety line names store + posture
(`research.db · SQLite · read-only · live · one connection per query ·
200-row cap`). Blobs render as `<N bytes>`; cells truncate at 120 chars.
FTS5 `note_search MATCH …` works through the same box. Terminal-free
adapters (`db_schema`, `db_run`) live in `helpers/misc/search_tui.py`
and are unit-tested against tmp fixture DBs — never the live stores.

### Dependency & launch

```bash
uv sync --extra tui          # or: uv pip install textual
make search-tui
python3 helpers/misc/search_tui.py -q "langgraph" --lane docs
```

Without `textual`, the CLI prints install help and exits 2. Adapters
(`run_lane` and friends) import without textual, so tests never need the
extra.

---

## Gates & perf coverage

- **Freshness:** out of `make perf` (2026-08-26, #159) — lives in
  `make search-fresh` (all three `--check`s) and three advisory rows
  (`doc-search-check`, `script-search-check`, `note-search-check`; STALE
  = FAILED with the refresh command in the tail).
- **`make perf`:** query-latency only — `doc_query` hybrid (budget 3.0 s;
  warm ≈0.6 s incl. model load) and `script_query` hybrid (budget 3.0 s)
  as real subprocesses against the live tree.
- **Not in `make qa`:** doc/code edits land between maint cycles and
  would redden qa constantly — staleness degrades to scan / warn-answer
  instead. Freshness gates stay in `search-fresh` + advisory.
- **Fuzz / unit:** chunker + MATCH generator Hypothesis tests in
  `tests/test_fuzz_rebuild_doc_search.py` (`make fuzz`); builder + CLI
  behavior in `tests/test_rebuild_script_search.py`,
  `tests/test_script_query.py` (hermetic tmp mini-trees,
  `integration`-marked).

## Agent sessions

`AGENTS.md` (repo root): query `doc_query.py` before reading doc files
and `script_query.py` before guessing filenames or writing helpers —
never read `completed.md` whole. Ranked `path:line` hits, `--json`
supported on both CLIs.

## Related

- `doc/procedures/embeddings.md` — model apply / note_search write path /
  one-time embedder setup
- `doc/procedures/doc-hygiene.md` — reference & index breakage sweeps
  (post-rename link rot, archive-index gaps)
- `AGENTS.md` — ripwire verb reference, query-don't-scan doctrine
