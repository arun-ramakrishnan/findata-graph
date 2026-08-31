---
title: 'Mojo doc → script_search: store, process, embed the Mojo API surface'
status: executed
filed: '2026-08-30'
executed: '2026-08-30'
completed_md: '187'
area: helpers/maintenance/rebuild_script_search.py
---

# Mojo doc → script_search: store, process, embed the Mojo API surface

**Status:** EXECUTED 2026-08-30 (same day; see completed.md #188)
**Scope:** `helpers/maintenance/rebuild_script_search.py`, `helpers/misc/script_query.py`,
new sidecar table, tests, AGENTS.md line.

## Problem

`mojo doc <file>` compiles a source and emits a structured JSON API reference
(`decl` tree: module description, functions with overloads/signatures/args/
returns, aliases, structs/fields, traits). This is exactly the intent+surface
knowledge script_search exists for — but the Mojo footprint
(`Mojo/src/*/*.mojo`, `Mojo/tests/*.mojo`, 15 files, 138 functions) is absent
from the index: `script_query.py "louvain parity"` cannot see
`integrity_check.mojo`'s checks or `graph_algos_probe.mojo`'s canonicalisation
helpers.

Two measured facts shape the design:

1. `mojo doc` costs **~3.5 s/file** (full compiler invocation) — it can never
   run unconditionally on every rebuild (~50 s for 15 files).
2. Our `##` docstring prose is thin (16/70 functions; module descriptions all
   empty) — but the real intent prose lives in leading `#` header comment
   blocks, which `mojo doc` does not surface. The index must combine both.

## Design

**Units.** Every Mojo source/test file becomes ONE script_search row:
`kind='mojo'`, `area` = package dir (`bench`/`common`) or `test` for
`Mojo/tests/`, `title` = repo-rooted rel (`Mojo/src/common/integrity_check.mojo`).
No FTS schema change (kind/area are UNINDEXED text) → no migration.

**Storage.** New sidecar table `script_search_mojo_doc(unit_path PK,
content_hash, doc_json)` inside the gitignored script_search.db (rides the
existing last-good backup). Raw `mojo doc` JSON is stored content-addressed:
regenerated via subprocess only when blake2b(source) changes; on rebuild of
an unchanged source the stored JSON is reused (0 subprocess cost). If the
`mojo` binary is missing or doc fails for a changed file: reuse the last
stored JSON; if none, degrade to source-text-only composition.

**Processing.** Per unit:
- `purpose`: `decl.description` (module `##` docstring) → else first paragraph
  of the leading `#` block → else stem-derived.
- `details`: flattened decl — `fn name(signature) — summary` lines with arg
  names/types, aliases with values, struct fields, traits; capped at
  `_DETAILS_CAP`.
- `defs`: top-level function/struct names (same enrichment contract as py).
- Embedding through the existing `_row` → shared embed cache; hybrid
  BM25+cosine and RRF fusion work unchanged.

**Freshness.** Mojo units join `units_meta` with the same (mtime, hash)
fingerprint → `--check`, `--incremental`, `make search-fresh` cover them with
zero new logic. The read-path probe (`_scan_disk_units`/`_unit_bases`) learns
the Mojo roots so staleness stays cheap and correct.

**Read path.** `script_query.py --kind` gains `mojo` (`--kind mojo` for API
queries; `--area bench|common|test` still drills). AGENTS.md script-index
blurb gains one line.

## Testing

New `tests/test_rebuild_script_search_mojo.py`: tmp Mojo tree + monkeypatched
roots + a FAKE `mojo doc` generator (subprocess runner monkeypatched) — no
toolchain dependency in unit tests. Covers: purpose-from-header fallback,
decl flattening, JSON cache hit (second rebuild shells out 0 times),
staleness flow, kind/area filtering, doc-failure degradation. Plus one live
smoke via the real toolchain when present.

## Verification

1. Unit tests green; ruff/ty clean.
2. Live rebuild: `script_search` grows by 15 mojo rows, hybrid mode intact.
3. `script_query.py "canonical parity" --kind mojo` returns the probe modules.
4. `make search-fresh` green after APPLY.
