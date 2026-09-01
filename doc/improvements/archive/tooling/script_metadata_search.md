---
title: Script metadata search (`script_search`) — find helpers/tests by purpose
status: executed
filed: '2026-08-25'
executed: '2026-08-25'
completed_md: '154'
area: tooling
---

# Proposal: Script metadata search (`script_search`) — find helpers/tests by purpose

**Date**: 2026-08-25
**Status**: EXECUTED 2026-08-25 (S1–S3; S4 endpoint still deferred) —
234 rows / 187 units live, all golden queries in top-3, gates green
(measurements in §7).
**Depends on**: `rebuild_doc_search.py` shared machinery (embedder
resolution, FTS5 patterns); independent of Relations-2.0 and GF fallback.
**Complements**: codebase-memory-mcp (already indexes CODE STRUCTURE —
symbols, callers, callees). This proposal adds the missing METADATA
layer: what each script is FOR, its CLI surface, its make wiring.

---

## 1. Problem statement

The repo has **57 scripts under helpers/ and 127 test modules under
tests/** — a large collection that grows every week. A future session
that needs "the script that audits relation diffs" or "the test file for
the yfinance driver" today must guess filenames, grep, or read AGENTS.md
prose. The doc/ tree already got the content-addressable treatment
(`doc_query.py`, ~380 indexed sections); scripts did not.

Codebase-memory answers structural questions ("who calls X"). It does NOT
answer intent questions: which script validates DB integrity, which one
builds sector hierarchies, which make target runs the newsletter parse,
which test covers exchange search. That knowledge lives in module
docstrings and the Makefile — unindexed.

## 2. Existing assets to reuse

| Asset | Reuse |
|---|---|
| `helpers/maintenance/rebuild_doc_search.py` | FTS5+embedding pattern, `resolve_embedder()`, `query_embedder()`, `fts_match_expr()`, `connect_doc_db()` shape, meta/staleness design, `--check` gate doctrine |
| House docstring discipline | nearly every script opens with a rich purpose docstring (verified: analytics.py, enrich_relations.py, tests/*) — extraction is reliable without new annotations |
| Makefile | single wiring point; targets name their scripts |
| Sidecar-DB doctrine | own gitignored `memory/*.db`; never research.db |

## 3. Goals / non-goals

### Goals
- G1. One command answers "which script/test does X": ranked hits with
  path, purpose line, area, CLI surface, related make targets and tests.
- G2. Zero new authoring burden: metadata comes from existing docstrings,
  argparse declarations, and Makefile — no per-script annotation files.
- G3. Same freshness doctrine as doc_search: `--check` exits 1 on drift,
  wired into `make perf`; stale-but-answering query behaviour.
- G4. Machine-readable output for agent sessions (--json).

### Non-goals
- No symbol/callgraph indexing (codebase-memory owns that).
- No execution of indexed code at build time (static AST/regex only).
- No Flask-app dependency for queries (CLI-first; optional endpoint later).

## 4. Design

### 4.1 Index units and rows

Two row kinds plus two extras in one FTS5 table (`script_search`),
mirroring doc_search's section model so the same hybrid ranking applies:

- **script row** (one per helpers/**/*.py, plus root `app.py`): title =
  relative path; section_title = "(module)"; content = composed block:
  - purpose: first paragraph of the module docstring
  - details: remaining docstring paragraphs (capped)
  - cli: `--flag` tokens extracted from add_argument calls +
      subcommand names
  - make: targets whose recipe invokes this script
  - tested_by: test module paths importing/grep-naming it
- **test row** (one per tests/**/*.py, conftest kept — it carries
  fixtures worth finding): purpose = docstring first para; content
  includes the names of helpers modules it imports.
- **make row** (one per Makefile target): title = `make <target>`;
  content = the target's recipe script invocations — answers "what does
  make X do" as a first-class row instead of an attachment on script
  rows.

~184 rows total — tiny corpus; whole-corpus rebuild stays sub-second
warm (shared sha256-keyed embed cache makes re-embeds free).

### 4.2 Builder: `helpers/maintenance/rebuild_script_search.py`

- Walk `helpers/**` and `tests/**` (*.py, skip `__pycache__`/conftest? keep
  conftest — it carries fixtures worth finding).
- AST-parse each file: module docstring; top-level defs/classes with
  one-line summaries (row enrichment only, NOT a symbol index).
- Argparse surface: regex over source for `add_argument("--x"` /
  `add_parser("name"` — good enough for discovery; exactness belongs to
  `--help`.
- Makefile scan: parse targets+recipes, map referenced script paths to
  targets.
- Area derivation: directory-based — `helpers/misc/x.py` → `misc`,
  `helpers/maintenance/x.py` → `maintenance`, other `helpers/*`
  subdirs likewise; root `app.py` → `app`; every `tests/**` row →
  `test`; make-target rows → `make`.
- Test mapping: AST-import based ONLY (no grep-mention — comments and
  string literals make mention-matching noisy and `tested_by` is only
  useful if precise): for each test file, record the helper module
  paths it IMPORTS; write the inverse map onto script rows.
- Schema: `script_search` FTS5(title, kind UNINDEXED, rel_path UNINDEXED,
  area UNINDEXED, purpose UNINDEXED — display column added at execution so
  hits carry the purpose line without re-parsing content, content,
  embedding UNINDEXED) + `script_search_meta` (per-unit mtime/hash) +
  `script_search_info` (model stamp). Own DB:
  `memory/script_search.db`. Reuse rds `_embedding_json`,
  `stored_embed_dims`, backup helpers by import — extract nothing unless
  import proves awkward (then duplicate small, note it). (Execution note:
  `stored_embed_dims` proved awkward — it hardcodes the doc_search table —
  so `_script_stored_embed_dims` duplicates its ~15 lines verbatim;
  `_backup_file` was imported clean; incremental re-extracts every unit —
  script rows embed cross-file inputs (test imports, Makefile refs) — but
  writes are row-keyed diffs and re-embeds hit the shared cache.)
- Flags: full rebuild, `--incremental` (mtime/hash skip), `--check`
  (freshness verdict + exit-code doctrine verbatim from doc_search).

### 4.3 Query CLI: `helpers/misc/script_query.py`

    .venv/bin/python3 helpers/misc/script_query.py "audit relation diffs"
    .venv/bin/python3 helpers/misc/script_query.py "yfinance" --kind test
    .venv/bin/python3 helpers/misc/script_query.py "integrity" --area misc --json

- Output lines: `rel_path  [kind/area]  score` + purpose snippet —
  same shape as doc_query so sessions transfer the habit.
- Filters post-filter on UNINDEXED columns (cheap at this size).
- Stale index warns to stderr and still answers; missing index exits 1
  with the build command (verbatim doc_query contract).

### 4.4 Wiring

- Makefile: `script-search-rebuild` target; perf-gate entry mirroring
  `rebuild_doc_search`.
- AGENTS.md: extend the "query first" rule — before writing ANY new
  helper/test, query `script_query.py`; link doc/procedures/script-search.md.
- New operator doc: `doc/procedures/script-search.md`.

## 5. Implementation slices

| Slice | Content | Gate |
|---|---|---|
| S1 | Builder + extractor + schema + `--check`/`--incremental` | pytest (fixture mini-tree) + live rebuild timing |
| S2 | `script_query.py` CLI + filters + `--json` | pytest (synthetic index) + golden queries |
| S3 | Makefile targets, perf gate, AGENTS.md rule, procedures doc | make qa subset chosen by user |

(S4 optional, deferred: `/api/scripts/search` endpoint reusing the query
core, for the running app.)

## 6. Risks

- Docstring-quality variance → rows fall back to filename-derived title;
  worst case a script matches only by name/path (still better than grep).
- Regex argparse parsing imperfect → acceptable: discovery surface, not
  contract surface; `--help` remains ground truth.
- Builder complexity (C901) → per-file helper functions per house
  convention (memory: c901_extract_per_file_helper_pattern).
- Stale-index confusion → identical warn-and-answer semantics as docs,
  plus perf-gate drift detection.

## 7. Success criteria — MET 2026-08-25 (live index)

- Golden queries return the right artifact in top-3: **all six** —
  "database integrity" → `helpers/misc/database_integrity_check.py` (#1),
  "sector hierarchy build" → `build_sector_hierarchy.py` (#2, its test #1),
  "newsletter parse" → `parse_newsletter.py` (#1), "exchange search NSE" →
  `exchange_search.py` (#1), "relation diff audit" →
  `helpers/misc/relation_diff_audit.py` (#1), "yfinance driver tests" →
  `test_enrich_yfinance.py`/`test_enrich_from_yfinance.py` (top-3).
- Timing on the live tree: warm full ≈ 1.1 s (234/234 embed-cache hits),
  warm `--incremental` ≈ 1.1 s, `--check` ≈ 1.1 s (budget 2.0 s),
  `script_query` ≈ 0.7 s (budget 3.0 s). Cold first build: 234 fresh
  embeds via bge-small.
- `make perf` carries the drift gate: `rebuild_script_search --check`
  (rc 1 on drift/missing, same doctrine as the doc-search pair; verified
  FRESH rc 0 post-rebuild).
- AGENTS.md now instructs sessions to query `script_query.py` before
  guessing filenames and before writing new helpers/tests.

## 8. Open questions — RESOLVED 2026-08-25 (review)

1. Include function-level rows (one per public def with docstring)?
   **DEFER** (script-level rows + codebase-memory symbols cover the gap).
2. Index Makefile targets as standalone rows? **YES, S1** — trivially
   derived, high value ("what does make X do" is a motivating example).
3. Include scripts outside helpers/tests? **YES, S1** — root `app.py`
   (exactly one exists; it is the operator surface) gets a script row;
   OQ2's make rows cover the rest.
