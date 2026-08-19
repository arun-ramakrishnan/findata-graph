#!/usr/bin/env python3
"""Routine maintenance orchestrator for the FinData DBs.

Chains the always-safe maintenance steps in the right order:

  1. ``db_maint.py``     — VACUUM/ANALYZE/REINDEX/integrity on SQLite +
                           CHECKPOINT/VACUUM on the DuckDB cache.
                           Produces ``db-backup/research_backup.db`` (a
                           pre-mutation recovery point, kept distinct from
                           the versioned snapshot below).
  2. ``snapshot_db.py``  — refresh ``db-backup/research.snapshot.db.gz`` +
                           ``db-backup/graph.snapshot.duckdb.gz`` (the
                           git-tracked versioned snapshots, post-mutation).
  3. ``query.py rebuild`` — rebuild the DuckDB cache from the just-
                             snapshotted SQLite so the cache matches.

With ``--full``, additionally runs after step 3:
  4. ``sync_tags.py``    — rebuild entity_tags from note YAML.
  5. ``sync_sector_wikilinks.py --check`` — GATE only: abort when sector-
                           note company indexes are stale; the WRITE is the
                           explicit ``make sync-sector-links`` (housekeeping
                           never mutates notes).
  5b. ``build_sector_hierarchy.py --check`` — GATE only: abort on taxonomy
                           coverage errors or drifted Child Sectors (auto) /
                           up-link regions; the WRITE (which also writes
                           entities + belongs_to edges) is the explicit
                           ``--apply`` run.
  6. ``rebuild_note_search.py`` — rebuild FTS over findata markdowns.
  7. ``algorithms.py --all --apply`` — refresh pagerank/betweenness/louvain.
  8. ``derive_insights.py findata --apply --no-notes`` — scan newsletter concall
                           bodies; capture quotes + magnitudes into the
                           quotes/company_metrics tables ONLY (DB-write).
                           Note-rendering (`## The Chatter` / `## Key Figures`
                           blocks) is deliberately OFF here so a housekeeping
                           run never mutates notes — run ``derive_insights.py
                           findata --apply`` standalone (make derive-insights
                           is only the dry-run preview) to render/refresh
                           those blocks.
                           derive-events (step 9) reads note prose rendered by
                           the last standalone apply, so this
                           step and step 9 are no longer within-run coupled.
  9. ``derive_events.py --apply`` — refresh the events timeline (D7) from the
                           newly-synced note prose + existing edges.
 10. ``snapshot_db.py``  — re-snapshot so the committed snapshots include
                           the recomputed analytics + refreshed events.

Each step is a separate subprocess so its CLI logging is preserved and
a failure in one step doesn't corrupt another's state.

Order rationale: db_maint compacts SQLite first → snapshot reflects the
compacted state → graph-rebuild produces a DuckDB cache matching the
snapshot. If snapshot ran before db_maint, the snapshot would have
un-vacuumed free pages; if graph-rebuild ran first, the cache would be
stale after the snapshot.

Usage:
  python3 helpers/maintenance/maint.py            # tier 1 (always-safe)
  python3 helpers/maintenance/maint.py --full     # tier 1 + post-ingest cleanup
  python3 helpers/maintenance/maint.py --dry-run  # show plan, do nothing
  make maint                                      # equivalent
  make maint-full                                 # equivalent to --full
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# (label, command). Each step is a list suitable for subprocess.run.
# Commands use repo-root-relative paths so the cwd is explicit.
TIER1_STEPS: list[tuple[str, list[str]]] = [
    ("db_maint (VACUUM/ANALYZE/REINDEX/integrity)",
     ["python3", "helpers/maintenance/db_maint.py"]),
    ("snapshot (refresh versioned snapshots)",
     ["python3", "helpers/maintenance/snapshot_db.py"]),
    ("graph-rebuild (refresh DuckDB cache)",
     ["python3", "helpers/graph/query.py", "rebuild"]),
]

TIER2_STEPS: list[tuple[str, list[str]]] = [
    ("sync-tags (rebuild entity_tags from note YAML)",
     ["python3", "helpers/core/sync_tags.py"]),
    # sync-sector-links CHECKS only (2026-08-19): the write mutates sector
    # notes, so it is an explicit `make sync-sector-links` — housekeeping
    # never mutates notes. The check gates: stale sector indexes abort
    # maint-full with the exact remediation in the step's own output.
    ("sync-sector-links --check (gate: sector-note company indexes fresh)",
     ["python3", "helpers/maintenance/sync_sector_wikilinks.py", "--check"]),
    # Same doctrine for the hierarchy notes: taxonomy + Child Sectors (auto)
    # regions + sector up-links are CHECKED here; the write (which also
    # writes entities/belongs_to edges) is the explicit --apply run.
    ("sector-hierarchy --check (gate: taxonomy + super-sector notes fresh)",
     ["python3", "helpers/maintenance/build_sector_hierarchy.py", "--check"]),
    ("rebuild-note-search (rebuild FTS over findata markdowns)",
     ["python3", "helpers/maintenance/rebuild_note_search.py"]),
    ("recompute-graph (refresh analytics in graph_analytics)",
     ["python3", "helpers/graph/algorithms.py", "--all", "--apply"]),
    # D7 — refresh the events timeline (acquisition/jv/guidance/management_
    # change) from the newly-synced notes. SQLite-only (no graph-rebuild
    # needed), so it sits before the final re-snapshot so the snapshot
    # captures the new event rows. derive-themes is deliberately NOT here:
    # it writes theme entities + edges that the DuckDB cache must rebuild to
    # pick up, so it stays a separate `make derive-themes` + `make graph-
    # rebuild` pair (run that after a corpus-wide theme pass, not per-ingest).
    #
    # derive-insights runs --no-notes here: it scans newsletter sources and
    # writes ONLY the quotes/company_metrics tables (SQLite-only, no note
    # mutation). derive-events reads NOTE prose (## The Chatter / analyst
    # bullets) rendered by the last STANDALONE apply
    # (`derive_insights.py findata --apply [--stale-only]`; `make
    # derive-insights` is only the dry-run preview), not
    # this step's DB rows — so freshly-captured chatter enters the events
    # timeline one apply cycle later. Keeping note-rendering
    # out of maint-full means a housekeeping run can never mutate notes (the
    # maint-full placement invariant); order retained for stability.
    ("derive-insights (capture concall quotes + magnitudes into DB; --no-notes)",
     ["python3", "helpers/graph/derive_insights.py", "findata", "--apply", "--no-notes"]),
    ("derive-events (refresh events timeline from note prose + edges)",
     ["python3", "helpers/graph/derive_events.py", "--apply"]),
    ("snapshot (re-snapshot to include recomputed analytics + events)",
     ["python3", "helpers/maintenance/snapshot_db.py"]),
]
# NOTE: sync-sector-links used to WRITE here (regenerating the auto company
# index in sector notes). Since 2026-08-19 maint-full only CHECKS it; the
# write is the explicit `make sync-sector-links` (it mutates notes, and a
# housekeeping run must never mutate notes — same rule that keeps
# derive-insights note-rendering out of TIER2).


def _run_step(
    label: str, cmd: list[str], dry_run: bool, logger: logging.Logger
) -> int:
    """Run one maintenance step. Returns the subprocess exit code."""
    logger.info("=" * 60)
    logger.info(label)
    logger.info("$ %s", " ".join(cmd))
    if dry_run:
        logger.info("(dry-run; skipping)")
        return 0
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))  # noqa: S603  # list-form call; shell=False (default); args are constants/controlled paths
    if proc.returncode != 0:
        logger.error("step failed with exit %d: %s", proc.returncode, label)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--full", action="store_true",
        help="also run sync-tags + sync-sector-links + recompute-graph + re-snapshot (post-ingest cleanup)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="show the plan, do nothing",
    )
    p.add_argument("--log", default="INFO", help="Logging level.")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format=LOG_FORMAT,
    )
    logger = logging.getLogger("maint")

    steps = list(TIER1_STEPS)
    if args.full:
        steps.extend(TIER2_STEPS)

    logger.info(
        "Maintenance plan (%d steps%s):",
        len(steps), " --full" if args.full else "",
    )
    for i, (label, _) in enumerate(steps, 1):
        logger.info("  %d. %s", i, label)

    failures = 0
    for label, cmd in steps:
        rc = _run_step(label, cmd, args.dry_run, logger)
        if rc != 0:
            failures += 1
            # Stop on first failure — later steps may depend on earlier
            # ones (e.g. snapshot must reflect a vacuumed DB; graph-rebuild
            # must match the snapshot).
            logger.error("aborting — step failed: %s", label)
            break

    if failures:
        logger.error("✗ maintenance failed (%d step(s) failed)", failures)
        return 1
    logger.info("✓ maintenance complete (%d steps)", len(steps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
