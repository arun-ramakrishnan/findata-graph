# Procedure: Routine maintenance (make maint / maint-full)

**Date:** 2026-08-29
**Scope:** the `maint.py` orchestrator's step composition — what runs when,
why each block is ordered the way it is, and the recovery/snapshot
semantics. The authoritative, always-current list is the `maint.py`
module docstring; this doc explains the doctrine for humans.

## The three blocks

`make maint-full` composes **PRE_FULL + TIER1(− snapshot) + TIER2**, in
that order. Plain `make maint` runs **TIER1 only**.

### PRE_FULL — `--full` only, BEFORE the recovery backup

| Step | Writes |
|---|---|
| `sync-tags` | `entity_tags` + `note_tags` from note YAML; E5a-derives `entities.sector_classification` |
| `rebuild-note-search` | `note_search` FTS over findata markdowns |

Both are **full rebuilds of derived indexes**. They run before
`db_maint` so their output lands inside the recovery backup — without
this, the backup's FTS was one step stale by construction (the rebuild
ran after it). Keeping them outside the backup's protection costs
nothing: a corrupt rebuild is fixed by rerunning the step. A secondary
win: sync-tags' E5a `entities` write now lands before `graph-rebuild`,
so the DuckDB cache is rebuilt from the post-sync state instead of
lazily re-materialising on next connect.

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
is off for the same reason.

## Placement invariant

A derivation step belongs in maint-full iff it writes **only SQLite
tables** — anything touching `entities`/`graph_edges` needs a paired
DuckDB rebuild and stays a standalone `make` target (e.g.
`derive-themes` = `make derive-themes` + `make graph-rebuild`).

## When to run what

- **Routine** → `make maint` (3 steps, always safe).
- **Post-ingest / post-surgery** → `make maint-full` (12 steps).
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
