---
title: "Shared routines pass 2 — stale-gate helper, graph-conn adoption, --apply CLI guard unification"
status: executed
filed: "2026-09-03"
executed: "2026-09-03"
completed_md: "196"
area: "helpers/core (corpus.py stale gate, env.py repo_root), helpers/graph (context_pack conn), CLI guard flags across helpers/, Makefile wiring, tests"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Shared routines pass 2 — stale-gate helper, graph-conn adoption, `--apply` CLI guard unification

**Date:** 2026-09-03 · **Status:** EXECUTED (same day) ·
**Area:** `helpers/core/corpus.py` (stale gate) · `helpers/graph/context_pack.py` (conn) ·
CLI surfaces across `helpers/` + `Makefile` wiring · follows the 2026-09-03
consolidation survey (operator-driven; raw counts in the Appendix)

## 1. Motivation

A consolidation survey (2026-09-03) re-examined the codebase for repeated
work after the S1-era wins (`frontmatter.py` 7→1, `corpus.py` 5×rglob+yaml
replay→1, `env.py`, `db.py`, `stable_write`). What remains splits into two
families:

1. **Logic duplication** — three near-identical `--stale-only` gate blocks in
   the derive family; one query-surface module bypassing the shared graph
   connection.
2. **CLI guard drift** — the house mutation guard (`--apply`, dry-run by
   default, 13 scripts) coexists with four other spellings
   (`--dry-run`, `--rewrite`, `--write`, `--apply-decisions --write`), and
   **five mutators have inverted or missing polarity** — they write
   research.db / findata notes by default with no opt-in.

The second family is the operator-named gap: "the `--dry-run`/`--apply`
missing gap" — running `sync_tags.py` or `enrich_from_yfinance.py` bare
today mutates state silently.

## 2. Survey evidence (measured 2026-09-03)

### 2.1 Mutation-guard census — 41 CLI parsers, 216 `add_argument` calls

| Guard spelling | Files | Polarity |
|---|---|---|
| `--apply` | 13 — derive_{events,insights,themes,co_mentions,cited_in}, extract_relations, algorithms, triage_pending_relations, backfill_okf_provenance, okf_verify, enrich_relations, parse_newsletter, build_sector_hierarchy | dry-run default (house standard) |
| `--dry-run` | 5 — db_maint, maint, enrich_relations (redundant extra), enrich_from_yfinance, rebuild_schema | **write default** (opt-OUT) |
| none | sync_tags (writes entity_tags), sync_sector_wikilinks (`--check` is opt-in report; default writes) | **write default, no guard** |
| `--rewrite` | capture_newsletter_images (caller `parse_newsletter.py:206` hardcodes it into a subprocess argv) | write default when passed |
| `--apply-decisions --write` | triage_pending_relations (`--apply-decisions` alone = dry-run preview) | two-key write |
| `--check` (read mode) | sync_sector_wikilinks, verify/static family, rebuild_* sidecars | report opt-in — fine |

Cache-backfill writers (`vec_search.py:241,341`, `embeddings.py:256` INSERT
on read paths) and the pdf ingestion tools (`pdf_conv_md`,
`verify_extraction`) are write-by-design on derived/explicitly-targeted
state — documented exceptions, not guarded (§4).

### 2.2 Epilog / formatter defects

- Only `extract_relations.py` sets `RawDescriptionHelpFormatter`. The
  multi-line epilogs in `get_tickers.py:753` and `enrich_relations.py:2551`
  render **collapsed to one line** under the default help formatter.
- `get_tickers` epilog uses a bare `get_tickers.py`; `extract_relations`
  hardcodes `python3 helpers/graph/extract_relations.py` — both should be
  `%(prog)s`.

### 2.3 argv adoption

27/41 parsers take `def main(argv)` + `parse_args(argv)` (the
test-friendly idiom). 15 stragglers: get_tickers, parse_newsletter,
sync_tags, vec_search, build_sector_hierarchy, db_maint,
migrate_embed_store, rebuild_schema, snapshot_db, sync_sector_wikilinks,
git_secret_scan, capture_newsletter_images, pdf_conv_md,
verify_extraction, verify_notes.

### 2.4 `-k` vs `--limit`

Not drift: in `query.py` subcommands `-k` = neighbors per node (4 sites,
3 missing help text), `--limit` = row caps (3 sites + the two search CLIs).
Semantically distinct spellings — keep, document, add the missing help.

### 2.5 Logic duplication (the pre-argparse findings)

- Stale-gate triplicated: `derive_events.py:628`,
  `derive_themes.py:422`, `derive_cited_in.py:325` — same
  `MAX(created_at)` → `fromisoformat` → max-mtime-over-tree → skip block,
  with copy-pasted comments.
- `context_pack.py:370` opens a raw `duckdb.connect(read_only=True)`,
  bypassing `query.connect()`'s warm/cold + `_prep_graph_connection` +
  `_attach_sqlite` contract (works today only because it touches
  DuckDB-local views).
- `Path(__file__).resolve().parents[2]` re-derived in 12+ files under four
  spellings; `utc_now()` exists in `db.py` while `enrich_relations` has 15
  raw `datetime.now(UTC)` call sites (enrich_from_yfinance 3,
  git_secret_scan 1).

## 3. Design — work packages

**W1 — canonical guard flag is `--apply`** (operator decision 2026-09-03):

- Rename `capture_newsletter_images --rewrite` → `--apply`; update the
  hardcoded caller argv in `parse_newsletter.py:206` and the two
  `doc/procedures/markdown_parse.md` lines. No test references (verified).
- Fold triage's two-key path: `--apply-decisions` implies write; drop
  `--write`. Update `tests/test_triage_pending_relations.py` (8 call sites
  — note `:140` currently asserts the dry-run-preview mode) and the
  `Makefile:193` echo text.
- `enrich_relations` keeps `--apply` (already canonical); its extra
  `--dry-run` (explicit no-op override, pinned by `Makefile:161` default
  ARGS) and `--check-only` stay — mode selectors, not guards.

**W2 — polarity flips to dry-run/report default + `--apply`** for the four
data mutators: `sync_tags`, `sync_sector_wikilinks`,
`enrich_from_yfinance`, `rebuild_schema`. Programmatic callers and Makefile
wiring (`Makefile:137,166,170` + any rebuild_schema sites) pass
`--apply` / `apply=True`. Direct CLI invocation becomes safe-by-default.
`db_maint`/`maint` keep `--dry-run` plan-mode: they act on derived
artifacts (backups/vacuum/orchestration), make is the only caller, and
`maint`'s plan-mode is its own useful feature — documented deviation (§7).

**W3 — epilog cleanup**: add `RawDescriptionHelpFormatter` to get_tickers +
enrich_relations; `%(prog)s` in all three epilogs; refresh
enrich_relations examples to the canonical flag set.

**W4 — argv adoption** for the 15 stragglers (mechanical
`def main(argv: list[str] | None = None) -> int` + `parse_args(argv)`).

**W5 — `-k` help strings** on the 3 bare query.py subcommands; one
convention line in the operator doc (neighbors `-k` vs row caps
`--limit`).

**W6 — shared stale gate**: `stale_gate(conn, table, trees)` in
`helpers/core/corpus.py` collapsing the three derive-family copies;
returns bool (skip). Keeps each script's rglob-vs-fs_walk choice as a
parameter (derive_themes' "single max, rglob is fine" comment becomes the
docstring).

**W7 — context_pack adopts the shared graph connection**:
`query.connect(read_only=True)` or a thin `connect_read_only()` wrapper
extracted in `query.py` if the warm/cold probe logic is reusable.

**W8 — repo-root + utc_now cosmetics** (opportunistic, rides along):
single `REPO_ROOT` export in `helpers/core/env.py`; adopt `db.utc_now()`
at the raw call sites.

**W9 — guard-census advisory test**: extend the advisory-test pattern
(`tests/test_static_checks.py` house, S1d Corpus-nudge style): WARN (never
rc 1) when a `helpers/**` file has both mutation writes
(`INSERT INTO|write_text|stable_write`) and an `ArgumentParser` but no
`--apply|--dry-run|--check` guard. Keeps future CLIs from regressing the
convention; the write-by-design exceptions (§4) go on an allowlist.

## 4. Non-goals

- **Shared argparse builder** — rejected on evidence: the most-repeated
  flag across 216 definitions is `--limit` ×5; a builder would own ~2
  flags while adding indirection to 41 CLIs pinned by make + integration
  tests. What repeats is conventions, and those are enforced by W4/W9 +
  review, not code.
- **`-k`/`--limit` aliasing** — semantically distinct (§2.4).
- **Guarding cache backfill** (`vec_search`, `embeddings`) — derived,
  self-healing state written on read paths.
- **Guarding pdf ingestion** (`pdf_conv_md`, `verify_extraction`) —
  operator-invoked per-target conversion; adding guards changes the
  workflow without protecting anything (W1 gives
  capture_newsletter_images `--apply` only because it rewrites existing
  note content).
- **`migrate_embed_store`** — one-shot, already executed (#166); frozen.
- **Staleness-machinery unification** (rebuild_note_search /
  rebuild_script_search / rebuild_doc_search fingerprint caches) —
  deferred with trigger (§7); direction already recorded in #194
  (S1b.2 blake-hash-not-mtime).
- **Flipping db_maint/maint** — accepted deviation (§3 W2).

## 5. Tests

- `tests/test_triage_pending_relations.py` — 8 `--apply-decisions` call
  sites; `:140`'s dry-run-preview assertion moves to the default report
  mode.
- `tests/test_sync_tags.py`, `tests/test_sync_sector_wikilinks.py`,
  `tests/test_rebuild_schema.py`, yfinance enrich tests — pin the new
  dry-run default + `--apply` writes (makefile-flag parity asserts where
  the pattern already exists).
- `tests/test_integration_maint_chain.py` / `test_maint.py` —
  orchestration passes `--apply` through; `maint --dry-run` plan-mode
  untouched.
- New: formatter smoke (`get_tickers --help` output contains a newline
  inside the Examples block), W9 census advisory, argv-straggler `_cli`
  tests where behavior is non-trivial.
- Derive-family stale-gate: existing `--stale-only` tests must pass
  unchanged through W6 (behavior-preserving refactor).

## 6. Risks / rollback

- **Operator muscle memory** — direct-CLI invocations that wrote silently
  now need `--apply`; mitigated by make wiring carrying the flag and the
  safety gain being the point. Each flip is a single-file revert.
- **Subprocess argv drift** — the `parse_newsletter.py:206` hardcoded
  `--rewrite` shows why W1 must land caller + callee + docs in one change;
  a missed caller fails loudly (unrecognized argument), not silently.
- **Make wiring** — one-line edits (`Makefile:137,166,170,193`); verified
  by the integration-maint-chain tests.
- **W7** — behavior-preserving only if context_pack stays read-only;
  `connect()`'s warm/cold probe must not trigger a build from a pack
  request (read-only wrapper guarantees it).

## 7. Deferred (record, don't do)

- **Staleness trio unification** — trigger: 10k notes or mtime-churn pain
  (rebases forcing full re-embed). Follows #194 S1b.2 (content hash, not
  mtime).
- **db_maint/maint polarity flip** — trigger: any future direct-CLI usage
  pattern; today make-only.
- **`enrich_relations --dry-run` removal** — only if `Makefile:161`
  default ARGS is ever emptied; harmless today.

## 8. Slices (execution order)

1. W6 + W7 (behavior-preserving refactors, no CLI surface change).
2. W1 + W2 + Makefile + docs (the guard unification, one commit).
3. W3 + W4 + W5 (cosmetic CLI pass).
4. W8 (rides any slice above).
5. W9 (advisory lands last so it greets the converged state).

## 9. Execution notes (2026-09-03)

All work packages landed; deviations and findings:

- **W6** — `corpus.notes_stale_since(db_max, trees)`; the three derive
  `--stale-only` gates now delegate. Semantics note: OSError on a stat
  now SKIPS the file in all three (uniform `derive_cited_in` behavior);
  `derive_events`/`derive_themes` previously bailed to a full derive on
  the race — documented in the helper docstring.
- **W7** — `query.connect_read_only(duckdb_path)` (no warm/cold build
  path, per the risk note); `context_pack` uses it. Finding: the raw
  open previously relied on DuckDB extension AUTOLOAD for the vss
  scalars `semantic_neighbors` needs — the shared prep now LOADs them
  explicitly.
- **W1** — `--rewrite` → `--apply` incl. the hardcoded
  `parse_newsletter.py` subprocess argv; triage `--write` dropped (CLI
  always writes; `apply_decisions(..., write=False)` retained at the
  library level for validate-only passes — the retired dry-run preview
  assertion now pins that API).
- **W2** — the four flips landed with dry-run summaries (sync_tags
  projects the per-category breakdown from the scan, not the stale
  table); `maint.py` + three Makefile targets pass the guard flags.
  `algorithms.py --no-apply` checked during execution: already
  dry-run-default with an explicit no-op flag — house-compliant, no
  change.
- **W4** — 14 files (verify_notes already took `argv`); get_tickers got
  a full `cli(argv)` extraction (its parser lived under `__main__`).
- **W8** — `env.REPO_ROOT` exported and adopted by `db.py`; two
  constraints recorded: (a) script ENTRY POINTS keep `parents[2]` —
  their sys.path bootstrap runs before `helpers` is importable
  (chicken-and-egg), (b) `db.utc_now()` NOT adopted at the
  enrich_relations/enrich_from_yfinance call sites — their formats
  diverge (`isoformat(timespec="seconds")` T-separator, `"… UTC"`
  suffix) and adoption would change output bytes.
- **W9** — `tests/test_cli_guards_advisory.py`; census clean at
  execution; 8-entry allowlist with per-entry reasons; hard assertions
  prune stale allowlist entries.
- **Python 3.14 note:** `ruff format` (py314 target) rewrites
  parenthesized multi-excepts to the new PEP 758 unparenthesized form
  (`except TypeError, ValueError:`) — valid 3.14+, surprising to
  pre-3.14 eyes.

## Appendix — raw survey log (2026-09-03)

- `add_argument` total: 216 across 41 `ArgumentParser` sites.
- Flag frequency (long+short): `--limit` ×5, `-k` ×4, then ×2 tail
  (`--workers --top --max-hops --log --doc-type --db --bm25 --apply`).
- Guards: `--apply` ×13 files, `--dry-run` ×5, `--check` ×7 (read-mode),
  `--write` ×2, `--rewrite` ×1, `--check-only` ×1, `--force` ×1
  (snapshot restore — destructive confirm, keep).
- `__main__` guards: 48; `parse_args(argv)` idiom: 27/41.
- Stale-gate copies: `derive_events.py:628`, `derive_themes.py:422`,
  `derive_cited_in.py:325-345`.
- Raw DuckDB connects: 8 sites — only `context_pack.py:370` is a
  query-surface bypass (db_maint/snapshot_db/integrity_check are
  administrative by design; analytics uses in-memory parquet fetch).
- Repo-root spellings: `_REPO_ROOT`, `PROJECT_ROOT`, `_PROJECT_ROOT` ×12+
  files; `utc_now` raw call sites: enrich_relations ×15,
  enrich_from_yfinance ×3, git_secret_scan ×1.
