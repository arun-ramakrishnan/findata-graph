---
title: maint runtime hardening — sys.executable spawns, okf-backfill + derive-cited-in into PRE_FULL, converter title fix
status: executed
filed: '2026-09-04'
executed: '2026-09-04'
completed_md: '206'
area: helpers/maintenance/
---

# maint runtime hardening — sys.executable spawns, okf-backfill + derive-cited-in into PRE_FULL, converter title fix

**Status:** EXECUTED 2026-09-04 (filed + executed same day — completed.md #206) · **Trigger:** user directive after the
2026-09-04 Milky Mist ingest — `make maint-full` aborted at step 1
(`ModuleNotFoundError: dotenv`) in any shell without the venv on PATH, and
the OKF edition entities / `cited_in` edges had silently gone stale because
`make derive-cited-in-rebuild` is never run manually. Slices 3–4 added
after the manual `backfill_okf_provenance.py --apply` healed 25 notes
(five from this ingest + twenty stragglers from prior hand-written
enhancements — evidence the splice-by-discipline approach fails).

## Motivation

**1. PATH-`python3` child spawns crash under a venv parent.** Any repo
script invoked as `.venv/bin/python3 <script>` that then shells
`["python3", <repo-script>]` launches the *system* interpreter for the
child — which lacks `dotenv` (and every other venv dep) — and dies with a
raw traceback. `parse_newsletter.py` was already fixed this way
(2026-09-04, live-verified); the remaining census:

- `helpers/maintenance/maint.py` — 13 step definitions (`PRE_FULL_STEPS`,
  `TIER1_STEPS`, `TIER2_STEPS`), all `["python3", ...]`.
- `helpers/maintenance/db_maint.py` — `_run_sync_check()` (line ~718)
  spawns the `_SYNC_HELPERS` scripts.

Cleared as non-violators (external binaries, PATH by design):
`static_checks` (node), `markdown_lint` (markdownlint-cli2),
`git_secret_scan` (git), `pdf_conv_md` (pdfinfo),
`rebuild_script_search` (repo-local venv `mojo` + repo-local node).
Makefile entry points (`python3 <script>`) remain PATH-dependent by
design — the user's interactive shell owns that interpreter choice; the
nasty class is exactly the invisible parent→child mismatch.

**2. `derive_cited_in` fell between two chairs.** The maintenance
placement invariant (`doc/procedures/maintenance.md`) keeps
entities/graph_edges writers out of maint-full as *standalone paired
targets* (e.g. `derive-themes`). But cited-in is not an analytical
derivation like themes — it is a deterministic INSERT-OR-IGNORE projection
of `sources[]` frontmatter already stamped at render time: a per-ingest
no-op between ingests, exactly the PRE_FULL profile. TIER1 already
contains the paired `graph-rebuild` the rule demands; the manual
`make derive-cited-in-rebuild` simply never happens, so edition entities
and `cited_in` edges rot between explicit runs (observed: the
`Milky_Mist_Bluestone` edition entity missing after the 2026-09-04
ingest+maint-full cycle).

## Design

1. **Interpreter doctrine:** every repo→repo Python subprocess uses
   `sys.executable` (the child must run under the interpreter the parent
   is running under — venv or system). Replace the 13 `maint.py` literals
   and the 1 `db_maint.py` site. Tests asserting spawn argv
   (`test_parse_newsletter_analytics.py`, already updated) and step
   structure (`test_maint.py`) pin the new shape.
2. **`derive-cited-in` joins PRE_FULL** as step 3
   (`sync-tags → rebuild-note-search → derive-cited-in`), before
   `db_maint`'s recovery backup (new entities land inside it — the
   existing PRE_FULL rationale) and before TIER1 `graph-rebuild` (the
   paired DuckDB rebuild the placement rule requires, in the same run).
   `derive-themes` stays standalone — cadence argument unchanged
   (corpus-wide theme passes, not per-ingest).
3. **Doctrine text updated** (`doc/procedures/maintenance.md`): the
   invariant gains the "…or runs inside maint-full before graph-rebuild,
   when it is a deterministic projection of already-stamped frontmatter"
   clause; step composition 12 → 13.
4. `derive_cited_in.py`'s pairing docstring + the post-render chain note
   in `doc/procedures/markdown_parse.md` point at maint-full as the
   automatic path.

3. **Slice 3 — `okf-backfill` into PRE_FULL** (option A of the
   "avoid the manual backfill" review, user-selected 2026-09-04): the
   derived-mode `backfill_okf_provenance.py --apply` converger joins
   PRE_FULL as step 2 (`sync-tags → okf-backfill →
   rebuild-note-search → derive-cited-in`). Rationale: hand-written
   Stage-5 edition blocks have no writer that splices `sources[]`
   (derive_insights maintains it at render time only, and never renders
   hand-written blocks) — every hand-enhanced note accumulates citation
   debt until the manual backfill runs. The converger is idempotent
   (no-op once converged), actor-honest, and vault-wide fast (~1s).
   Doctrine carve-out: "housekeeping never mutates notes" now excludes
   **machine-owned frontmatter provenance keys** (`sources[]`,
   `stale_after`, `process:*` stamps) — bodies, rosters, and chatter
   blocks remain untouchable. Rejected alternative: splicing at
   Stage-5 write time (procedure discipline) — kept as hygiene, not the
   guarantee, because 20 straggler notes across five sessions prove
   discipline fails.

4. **Slice 4 — converter edition-title fix**: `pdf_conv_md.py` set the
   note `title:` from the first markdown heading, which grabs whatever
   H1 the layout emits first — a sector header ("FMCG"), a company
   section line, even another newsletter's masthead ("Subtext by
   Zerodha"). Five of the ten in-tree local-engine edition notes carry
   those degenerate titles, and they propagate into company-note
   `sources[].title` via the converger. Fix: prefer the PDF's own
   pdfinfo `Title` metadata (already read for the sources[] entry),
   first-heading and stem stay as fallbacks; repair the five notes from
   the same metadata; fix the stale `derive_cited_in.py` docstring that
   claimed edition `normalized_name` = frontmatter title (the code uses
   the stem).

5. **Slice 5 — converger semantics (emerged during slice-4
   verification)**: `merged_sources` (edition_index.py) kept existing
   entries verbatim, so a repaired edition title never propagated into
   already-stamped notes. Now entries for editions the body still
   references CONVERGE to the canonical builder output
   (title/resource/last_modified refresh); deleted/uncited editions stay
   verbatim (accepted Q2 unchanged for the historical-pointer case).
   En route, `note_title` was found grabbing the raw `title:` line —
   YAML quote characters landed IN the value (triple-quote soup in
   `sources[].title`, latent since the OKF arc); it now parses the
   scalar via `yaml.safe_load` with a raw fallback for the legacy
   unquoted-with-colon shape. Idempotence pinned by tests (converge
   once → byte-identical after) and verified live: converger re-run on
   a converged vault reports 0 notes.

## Verification

- `ruff check` + `pytest tests/test_maint.py` (step pins: PRE_FULL
  2→4, composition 12→14), `tests/test_pdf_conv_md.py` (+2 title
  tests), db_maint/derive_cited_in/parse_newsletter suites.
- Full `make maint-full` (venv-PATH shell): 14/14 steps OK; okf-backfill
  no-ops on a converged vault (note churn only when debt exists);
  `database_integrity_check` exit 0.
- `make search-fresh APPLY=1` after doc edits.

## Rejected

- Hardcoding `.venv/bin/python3` paths — breaks venv relocation and
  non-venv runs; `sys.executable` is self-describing.
- Wiring `derive-themes`/`extract_relations` into maint-full too —
  cadence + pending-triage coupling argue for standalone; not part of the
  observed gap.
- Makefile-wide `$(PYTHON)` variable sweep — separate question (entry
  points, not child spawns); not this crash class.
- Splice-at-write-time as the sole mechanism (option B) — discipline
  failure record above; survives as optional hygiene only.
