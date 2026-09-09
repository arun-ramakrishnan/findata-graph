# Commit messages — house template

Every commit message starts from the seed
[`doc/templates/commit_message.md`](../templates/commit_message.md): copy it,
fill the slots, delete unused variant blocks, then apply it with
`stg edit -f <file> <patch>` (stg; no editor, temp file at an absolute path)
or `git commit -t <file>` (git). One-time convenience:
`git config commit.template doc/templates/commit_message.md`.

## Subject grammar

    [Findata] <area>: <imperative summary>

| Prefix | Use | Real examples |
|---|---|---|
| `[Findata] <area>:` | everything GitHub-bound; `<area>` names the arc/subsystem | `73e3c8dc` OKF v0.2 provenance backfill, `b19ca3dd` Code duplication consolidation (#216) |
| `db_sync:` / `snapshot:` | mechanical, stack-local data refreshes — bare prefix, no `[Findata]` | `29e34e44`, `d29410a9`, `aa499829` |
| `(#N)` suffix | upstream PR squash merges | `03cd70e8` (#218), `aed9d84e` (#217) |

Imperative mood; name the mechanism, not the symptom; 50 chars target,
72 hard cap. An em-dash may carry the payoff: `[Findata] quote capture:
person/role catch-all tier — chairman/regulator quotes route to Quotes.md`.

## Body order

1. **WHY** — root cause, the directive, or the proposal back-ref
   (`Implements doc/improvements/archive/... (completed.md #N)`).
   WHY-first is the house signature: `aa499829` opens with the GitHub
   50 MB warn / 100 MB block before any file list.
2. **WHAT** — bulleted slices, smallest-risk first, one concrete number
   per bullet (lines, rows, files, before -> after). Arc slices use the
   S1/S2 ladder.
3. **DATA PROVENANCE** — corpus/note-render patches: rendered-by stamp
   (UTC timestamp, tool + version, `--apply`), plus the paired-patch
   pointer: "Content only; machinery lives in the paired derive_perf
   patch" (`9ab4968c`).
4. **SNAPSHOT MECHANICS** — parquet/binary patches (see next section).
5. **`Gates:` trailer** — only gates actually run, with counts:
   `Gates: make qa 9/9, make advisory 10/10, make perf 22/22, make
   search-fresh APPLY=1, make static_checks all pass` (`03cd70e8`,
   `f458fb04`). Data refreshes may omit it.

## Binary DB diffs are reviewable text — the textconv pair

Two binary families carry this repo's state; both get text diffs through
git textconv drivers wired in `.gitattributes` + git config:

| Family | Driver wiring | textconv | Output |
|---|---|---|---|
| `*.parquet` | repo `.gitattributes` `binary diff=parquet`; repo `.git/config` `diff.parquet.textconv` (overrides the global driver of the same name) | **`helpers/misc/parquet_textconv.py`** (versioned — the canonical reference) | file, row count, schema, bounded sample |
| `*.sqlite3`, `*.db` | global `~/.config/git/attributes`; `~/.gitconfig` `[diff "sqlite3"]` | `sh -c 'sqlite3 $0 .dump'` — the one-liner pattern | full SQL dump, complete content |

The parquet helper exists in two copies by design: the versioned
`helpers/misc/parquet_textconv.py` is authoritative (referenced by this
repo's config), and `~/.config/git/parquet_textconv.py` is the
machine-global fallback that re-execs the repo venv when system python
lacks duckdb. Refer to the versioned path in committed documents; the
`~/.config` path is install detail, not API. Sample size is
perf-bounded via `PARQUET_TEXTCONV_ROWS` (default 10; `0` = all rows)
and `PARQUET_TEXTCONV_CELL` (96).

The two drivers model the two review intents: sqlite `.dump` gives the
complete content (small DBs), parquet gives shape + sample (snapshots
are canonical `ORDER BY ALL` exports, so the textconv skips sorting and
stays sub-second). `git diff` / `stg diff` on a db_sync patch therefore
read as one row-count line per changed table.

Because the rendered diff leads with row counts, db_sync/snapshot messages
**cite the row deltas the diff will show** (`graph_edges 18,269 -> 18,322`)
so a reviewer can cross-check message against diff in one glance. Also
state expected absences: `note_search_content` / `v_note_embeddings` are
untracked (`aa499829`), so their non-appearance in a db_sync patch is
correct, not data loss.

## Worked examples

| Commit | Pattern it exemplifies |
|---|---|
| `2636e06c` | code fix (WHY with invariants) + data apply + provenance bullets |
| `29e34e44` | db_sync: mechanics block, row deltas, untracked-artifact note |
| `f458fb04` | full arc: S-slices, proposal archival, docs log, gates trailer |
| `902b2f48` | single-mechanism arc patch with directive back-ref |
| `73e3c8dc` | minimal backfill: two paragraphs + gates trailer |

## Gates

`tests/test_templates.py` guards the seed's contract declaration;
`make md-lint` covers this file; `make search-fresh APPLY=1` re-indexes
after edits (doc/ is the remediable surface).
