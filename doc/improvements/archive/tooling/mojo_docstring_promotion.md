# Mojo docstring promotion — `#` prose → `##` docstrings

**Status:** EXECUTED 2026-08-30 (see completed.md #189 — with two deviations
from the design as written, captured in Execution notes below)
**Scope:** 13 files under `Mojo/src/*/*.mojo` + `Mojo/tests/*.mojo` (mechanical,
comment-only edits). No build/bench/query code changes.
**Builds on:** `mojo_doc_script_search.md` (EXECUTED, completed.md #188) —
the script_search Mojo rows whose quality this directly lifts.

## Problem

The Mojo footprint's intent prose is real and well-written — it just lives in
plain `#` comments, which **neither** consumer can see:

- `mojo doc` surfaces only `##` docstrings: today **all 13 module
  descriptions are empty** and only **16/70 functions** carry a summary.
- `script_search`'s mojo rows therefore embed mostly signatures + thin
  purposes. 5 of 13 modules start with imports (their prose banner sits
  mid-file), so purpose falls back to the filename stem — the live index
  literally shows `integrity_check.mojo` as purpose *"integrity check"*,
  and semantic demo queries regress to noise:
  `"grouping nodes into communities" --kind mojo` → `integrity_check.mojo`
  (irrelevant); `"how consistent is the database"` → `test_cosine.mojo`
  first. The embedding leg answers; the prose it needs isn't there.

Inventory (measured 2026-08-30):

| Pattern | Files | Content |
|---|---|---|
| Leading `#` block, 4–25 lines | 8 | module intent (e.g. `corpus_sweep.mojo`'s 25-line phase/parity header) |
| Import-first, prose banner mid-file | 5 | `integrity_check.mojo` (policy + section map, lines 6–40), `graph_algos_probe.mojo`, `db_access_probe.mojo`, `mojo_regex_probe.mojo`, `multiproc_bridge.mojo` |
| `#` block directly above a `fn` | 6+ | function docs that belong in `summary` |

## Proposal — promote, don't rewrite

Mechanical, comment-only promotion. The prose is already good; we change its
marker so the toolchain can see it. Prose that is promoted is REMOVED from
its old position (single source of truth — no duplicated banners).

### 1. Module docstrings (all 13 files)

A `##` block at the very top of the file becomes `decl.description`.

- Leading-`#` files: convert the header block `#` → `##` in place.
- Import-first files: MOVE the mid-file prose banner to the top of the file
  and convert to `##` (a `##` block after the imports would not attach to the
  module). `integrity_check.mojo`'s POLICY + section-map banner becomes its
  module docstring; the index purpose stops being a stem.
- Decorative separator lines (`# ---`, `# ===`) are dropped, not promoted —
  they'd pollute the first-paragraph extraction.
- Keep a single `# ---` section banner where it still serves navigation in
  long files; banners carry no prose and stay plain `#`.

### 2. Function docstrings (6+ sites)

A contiguous `#` block immediately above a `fn`/`def`/`struct` converts to
`##` attached to the declaration. Mojo doc convention does the rest:
first line → `summary`, remainder → `description`. `sort_strs`-style
one-liners already in `##` form stay as-is.

### 3. What is NOT touched

- Inline right-side comments, TODOs, section markers without prose.
- `Mojo/vendor/**` (third-party).
- Any executable line — docstrings are inert comments; zero behavior change.

## Payoff

1. **script_search** — mojo row purposes become the real prose (the
   composition already prefers `decl.description`; **no indexer code
   changes needed** — this is a pure data-side win). Embed quality for the
   failing semantic-query class should flip from noise to useful; the two
   demo queries above become the acceptance check.
2. **`mojo doc` output** — module descriptions 0/13 → 13/13; documented
   functions 16/70 → ~25+/70; the JSON API reference becomes shareable
   standalone.
3. **Future sessions** — `mojo doc` and `script_query` answers carry the
   POLICY-level context (bridge-first decision, parity gating, phase
   semantics) that currently only lives one grep away.

## Cost & risk

- One-time mechanical pass, ~13 files; every edit is comment-line moves +
  `#`→`##`. Behavior risk: zero (comments are inert). Cosmetic risk: the
  `mojo format --check` gate must stay green (formatter leaves comment text
  alone; wrapping may need a manual pass).
- One-time costs after the edit: doc cache invalidates → ~45 s of
  `mojo doc` regens across 13 files; 13 script_search rows re-embed
  (bge-small, seconds); `make search-fresh APPLY=1`.

## Verification

1. `make mojo-build` + `make mojo-test` green (docstrings are syntax-checked
   but inert).
2. `make mojo-fmt` idempotent; `tests/test_lint_gates.py` green.
3. `mojo doc` JSON: `decl.description` non-empty for 13/13 modules.
4. Live rebuild → `script_query` shows prose purposes (no stem fallbacks);
   re-run the two failing demo queries — `integrity_check.mojo` must rank
   first for "how consistent is the database" / "database integrity checks".
5. `make search-fresh` green after APPLY.

## Execution notes (2026-08-30)

1. **The marker form was wrong in the design; the toolchain uses `"""`
   string literals, not `##` comments.** The `##` blocks produced ZERO module
   descriptions (verified 0/13) — in this toolchain, docstrings are
   Python-style `"""..."""` string literals (as the 16 already-documented
   functions were). All 13 promoted blocks were re-written as top-of-file
   `"""` literals; result: 13/13 modules carry docstring text in `mojo doc`
   JSON. Related API discovery: `mojo doc` SPLITS the docstring — first
   paragraph → `decl.summary`, remainder → `decl.description`; the indexer's
   purpose now joins both (`_extract_mojo_unit`), pinned by test.
2. **The "6+ fn-adjacent sites" never existed** — the inventory script
   counted them through a state-machine bug (blank lines didn't reset the
   in-comment flag; the hits were the module banners themselves). A corrected
   scan (direct adjacency AND one-blank-gap adjacency) finds ZERO function
   promotion sites: all existing function docs were already `"""` literals.
   The promotion is therefore module-level only.
3. `mojo format` is docstring-neutral (rewraps nothing; idempotent);
   `mojo build` + `make mojo-test` green; live reindex: 13 rows recomposed,
   warm rebuild 1.6 s / 271 embed-cache hits / 0 regens. Acceptance query
   "how consistent is the database" --kind mojo → `integrity_check.mojo`
   FIRST (was `test_cosine.mojo`).
