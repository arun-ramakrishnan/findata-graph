# Procedure: Routine maintenance (make maint / maint-full)

**Date:** 2026-08-29
**Scope:** the `maint.py` orchestrator's step composition — what runs when,
why each block is ordered the way it is, and the recovery/snapshot
semantics. The authoritative, always-current list is the `maint.py`
module docstring; this doc explains the doctrine for humans.

## The three blocks

> **Diagram:** `../design/diagrams/maint_full.{json,html}` — the 14-step
> chain as a phase-banded workflow (archify; JSON IR is the committed
> source, HTML regenerable). Re-render when the step composition changes
> (see `../improvements/archive/tooling/archify_diagram_pipeline.md`).
>
> **Diagram (snapshot lifecycle):** `../design/diagrams/snapshot_lifecycle.{json,html}`
> — create → verify → restore as a state machine with the drift and
> restore paths (archify; JSON IR committed, HTML regenerable; suite:
> `tests/test_integration_snapshot_cycle.py`). Needs a broad/tall
> viewport (~2100px-wide class, or zoom out) — the four-band canvas
> overflows 1440×900 by design trade-off.

`make maint-full` composes **PRE_FULL + TIER1(− snapshot) + TIER2**, in
that order. Plain `make maint` runs **TIER1 only**.

### PRE_FULL — `--full` only, BEFORE the recovery backup

| Step | Writes |
|---|---|
| `sync-tags` | `entity_tags` + `note_tags` from note YAML; E5a-derives `entities.sector_classification` |
| `okf-backfill` | machine-owned note frontmatter (`sources[]`, `stale_after`) converged from note bodies — the one sanctioned note mutation in maint-full |
| `rebuild-note-search` | `note_search` FTS over findata markdowns |
| `derive-cited-in` | edition `entities` + `cited_in` `graph_edges` from OKF `sources[]` |

All four are **full rebuilds / deterministic projections of
already-stamped state**. They run before `db_maint` so their output
lands inside the recovery backup — without this, the backup's FTS was
one step stale by construction (the rebuild ran after it). Keeping them
outside the backup's protection costs nothing: a corrupt rebuild is
fixed by rerunning the step. A secondary win: sync-tags' E5a `entities`
write — and derive-cited-in's edition entities/edges — now land before
`graph-rebuild`, so the DuckDB cache is rebuilt from the post-sync
state instead of lazily re-materialising on next connect (that pairing
is the placement rule's DuckDB-rebuild requirement, satisfied in-run;
the standalone `make derive-cited-in-rebuild` remains for edge refreshes
without a full maintenance pass).

`okf-backfill` exists because hand-written edition blocks (the Stage-5
curation layer of `markdown_parse.md`) have no writer that splices
`sources[]` — `derive_insights` only maintains it at render time, which
hand-written blocks never get. Before this step (2026-09-04), every
hand-enhanced note accumulated citation debt until someone remembered
the manual backfill (20+ straggler notes healed on first run).

Between ingests both steps are guaranteed no-ops — which is why plain
`make maint` never runs them (post-ingest re-derivations per the
placement invariant).

### TIER1 — always-safe housekeeping (plain `make maint`)

| Step | Notes |
|---|---|
| `db_maint` | VACUUM/ANALYZE/REINDEX/integrity; takes the `db-backup/*_backup.*.zst` recovery copies (pre-VACUUM) |
| `snapshot` | versioned snapshots (post-mutation) — **elided in `--full`** (`TIER1_FULL_SKIP`), the TIER2 tail re-snapshots everything |
| `graph-rebuild` | DuckDB cache from the snapshotted SQLite |

The recovery copy is the **POST-index-refresh / PRE-data-derivation /
pre-VACUUM** restore point. The data-writing derivations (analytics,
quotes/company_metrics, events) deliberately run *after* it: the backup
is their restore point, so a corrupting derivation never flows into the
recovery copy. Snapshot vs recovery copy is temporal redundancy, not
duplication — recovery = newest pre-mutation state; snapshot = last
verified committed state (the git-tracked parquet is the real artifact).

### TIER2 — `--full` only, post-ingest re-derivation

Sector `--check` gates → `company-embeddings --maint` (best-effort,
never auto-upgrades) → `rebuild-doc-search` (sidecar-only, self-backing)
→ `recompute-graph` → `derive-insights --no-notes` → `derive-events` →
tail `snapshot` (the single snapshot of a `--full` run).

Gates write nothing; their WRITE paths are explicit make targets
(`make sync-sector-links`, `build_sector_hierarchy --apply`) because
housekeeping must never mutate notes. `derive-insights` note-rendering
is off for the same reason. The ONE carve-out: `okf-backfill` (PRE_FULL)
updates **machine-owned frontmatter provenance keys only**
(`sources[]`, `stale_after`, `process:*` stamps) — bodies, rosters, and
chatter blocks remain untouchable; that metadata is a projection of the
body the way `entity_tags` is of the YAML.

## Placement invariant

A derivation step belongs in maint-full iff it writes **only SQLite
tables** — anything touching `entities`/`graph_edges` needs a paired
DuckDB rebuild and stays a standalone `make` target (e.g.
`derive-themes` = `make derive-themes` + `make graph-rebuild`).
Exception (2026-09-04): a *deterministic projection of already-stamped
frontmatter* that is a guaranteed no-op between ingests may join
maint-full **ahead of `graph-rebuild`**, so the pairing happens in the
same run — `derive-cited-in` (INSERT-OR-IGNORE projection of OKF
`sources[]`) is the incumbent case; the never-run manual pairing had
let edition entities/edges rot between explicit runs. Analytical
derivations (themes, relations) keep the standalone form — their
cadence is corpus-wide passes + human triage, not per-ingest
bookkeeping.

## When to run what

- **Routine** → `make maint` (3 steps, always safe).
- **Post-ingest / post-surgery** → `make maint-full` (14 steps).
- **Crash mid-VACUUM** → restore from `db-backup/*_backup.*.zst`:
  `zstd -dc db-backup/research_backup.db.zst > memory/research.db`.
- **Verify a backup** → decompress to an alt location, `PRAGMA
  integrity_check`, diff row counts vs live (expect drift only in
  tables written after the backup point).

Run history appends to `maint_report.txt` (summary table always;
failed-step output tails on abort).

## CLI conventions (guard unification 2026-09-03)

- **Mutation guard:** scripts that write notes or research.db are
  dry-run/report by default and take `--apply` to write
  (`sync_tags`, `sync_sector_wikilinks`, `enrich_from_yfinance`,
  `rebuild_schema`, the derive family, `extract_relations`,
  `triage_pending_relations`, `capture_newsletter_images`). `--rewrite`
  is retired; the two-key `--apply-decisions --write` collapsed to
  `--apply-decisions`. Planned deviations: `db_maint` / `maint` keep
  `--dry-run` (plan mode; make is their only caller), `enrich_relations`
  keeps `--dry-run`/`--check-only` as mode selectors.
- **Row-cap spellings:** `-k` = neighbors per node (kNN-style
  subcommands of `query.py`), `--limit` = cap on rows/cycles returned.
  Not interchangeable.
