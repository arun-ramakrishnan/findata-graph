#!/usr/bin/env python3
"""Routine maintenance orchestrator for the FinData DBs.

Chains the maintenance steps in the right order, in THREE blocks:

  PRE_FULL (``--full`` only, BEFORE the recovery backup) — pure index
  rebuilds whose output must land INSIDE the db_maint recovery copy:

    0a. ``sync_tags.py``           — rebuild entity_tags from note YAML.
                                     Also E5a-derives entities.sector_
                                     classification from sector/* tags:
                                     running it before graph-rebuild means
                                     the DuckDB cache is rebuilt from the
                                     post-sync state instead of lazily
                                     re-materialising on next connect.
    0b. ``rebuild_note_search.py`` — rebuild FTS over findata markdowns.

    Both are full rebuilds of derived indexes, so keeping them outside
    the backup's protection costs nothing (a corrupt rebuild is fixed by
    rerunning the step) — while the backup captures fresh indexes
    instead of a one-step-stale FTS.

  TIER1 (``make maint``; always-safe housekeeping):

    1. ``db_maint.py``     — VACUUM/ANALYZE/REINDEX/integrity on SQLite +
                             CHECKPOINT/VACUUM on the DuckDB cache.
                             Produces ``db-backup/*_backup.*.zst``: the
                             POST-index-refresh / PRE-data-derivation /
                             pre-VACUUM recovery point, kept distinct
                             from the versioned snapshot below. The
                             data-writing derivations (analytics,
                             quotes/company_metrics, events) deliberately
                             run AFTER it — the backup is their restore
                             point, so a corrupting derivation never
                             flows into the recovery copy.
    2. ``snapshot_db.py``  — refresh ``db-backup/research.snapshot.db.zst`` +
                             ``db-backup/graph.snapshot.duckdb.zst`` (the
                             git-tracked versioned snapshots, post-mutation).
    3. ``query.py rebuild`` — rebuild the DuckDB cache from the just-
                             snapshotted SQLite so the cache matches.

  In ``--full`` mode step 2 is ELIDED (TIER1_FULL_SKIP): the TIER2 tail
  re-snapshots everything (step 10), so the mid-run snapshot's artifacts
  would be unconditionally overwritten — its only value is crash-safety,
  which ``db_maint``'s pre-mutation ``db-backup/`` copies already cover.

  TIER2 (``--full`` only; post-ingest re-derivation):
    4. ``sync_sector_wikilinks.py --check`` — GATE only: abort when sector-
                             note company indexes are stale; the WRITE is the
                             explicit ``make sync-sector-links`` (housekeeping
                             never mutates notes).
    4b. ``build_sector_hierarchy.py --check`` — GATE only: abort on taxonomy
                             coverage errors or drifted Child Sectors (auto) /
                             up-link regions; the WRITE (which also writes
                             entities + belongs_to edges) is the explicit
                             ``--apply`` run.
    5. ``embeddings.py --maint`` — cached refresh of ``company_embeddings``
                             over the note text (Q3 sidecar cache; GC of
                             deleted companies). Best-effort: exits 0 with
                             a WARNING (no writes) when the local embedder
                             is unavailable or the table isn't bge-populated
                             — never an auto-upgrade.
    6. ``rebuild_doc_search.py`` — refresh the doc/ corpus sidecar index
                             (FTS + embeddings; research.db untouched, and
                             the step takes its own last-good backup).
    7. ``algorithms.py --all --apply`` — refresh pagerank/betweenness/louvain.
    8. ``derive_insights.py findata --apply --no-notes`` — scan newsletter concall
                           bodies; capture quotes + magnitudes into the
                           quotes/company_metrics tables ONLY (DB-write).
                           Note-rendering (``## The Chatter`` / ``## Key Figures``
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
 10. ``snapshot_db.py``  — the single snapshot of a --full run: commits the
                           vacuumed DBs, the recomputed analytics and the
                           refreshed events in one pass.

Plain ``make maint`` runs ONLY TIER1: PRE_FULL and TIER2 are post-ingest
re-derivations (the placement invariant — a derivation step belongs in
maint-full iff it writes only SQLite tables, NOT entities/graph_edges,
which need a paired DuckDB rebuild). Between ingests both PRE_FULL steps
are guaranteed no-ops, so plain maint never pays for them.

Each step is a separate subprocess so its CLI logging is preserved and
a failure in one step doesn't corrupt another's state. Step output
streams live to the console AND is captured: every real run appends a
timestamped summary table — plus the tail of any failed step's output —
to ``maint_report.txt`` at the repo root (same append-only philosophy
as the gate reports in tests/run_gate_report.py → qa_report.txt), so a
bare "step failed with exit 1" always leaves the step's own stderr
behind for post-mortem. ``--dry-run`` writes no report.

Order rationale: PRE_FULL index refreshes land before the backup so the
recovery copy carries fresh indexes; db_maint compacts SQLite before
the snapshot (plain mode: step 2; --full: step 10) so it reflects the
compacted state, and graph-rebuild (step 3) produces a DuckDB cache
whose content the final --full snapshot re-commits. If snapshot ran
before db_maint, the snapshot would have un-vacuumed free pages; if
graph-rebuild ran first, the cache would be stale after the snapshot.

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
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))  # bootstrap for helpers.* imports  # noqa: E402

try:
    from helpers.core.corpus import Corpus  # S1b shared walk  # noqa: E402

    _HAS_CORPUS = True
except ImportError:  # pragma: no cover
    Corpus = None  # type: ignore[assignment]
    _HAS_CORPUS = False

# Append-only run log (gitignored, like the gate reports). Module-level
# and monkeypatchable so tests never touch the real file.
REPORT_PATH = PROJECT_ROOT / "maint_report.txt"
_TAIL_LINES = 60  # lines appended to the report per failed step
_CAPTURE_LINES = 400  # rolling in-memory cap while streaming


# (label, command). Each step is a list suitable for subprocess.run.
# Commands use repo-root-relative paths so the cwd is explicit.
#
# PRE_FULL (--full only, BEFORE db_maint's recovery backup): pure index
# rebuilds (entity_tags from note YAML, note_search FTS from the
# markdowns) whose output should land INSIDE the backup. A corrupt
# rebuild here is fixed by rerunning the step, so nothing is lost by
# keeping it outside the backup's protection. sync-tags additionally
# E5a-UPDATEs entities.sector_classification — running it before
# graph-rebuild keeps the DuckDB cache consistent (it used to run AFTER
# graph-rebuild and relied on lazy re-materialisation). Plain `make
# maint` never runs these: post-ingest re-derivations, guaranteed
# no-ops between ingests.
PRE_FULL_STEPS: list[tuple[str, list[str]]] = [
    (
        "sync-tags (rebuild entity_tags from note YAML)",
        ["python3", "helpers/core/sync_tags.py", "--corpus", "--apply"],
    ),
    (
        "rebuild-note-search (rebuild FTS over findata markdowns)",
        ["python3", "helpers/maintenance/rebuild_note_search.py"],
    ),
]

TIER1_STEPS: list[tuple[str, list[str]]] = [
    ("db_maint (VACUUM/ANALYZE/REINDEX/integrity)", ["python3", "helpers/maintenance/db_maint.py"]),
    ("snapshot (refresh versioned snapshots)", ["python3", "helpers/maintenance/snapshot_db.py"]),
    ("graph-rebuild (refresh DuckDB cache)", ["python3", "helpers/graph/query.py", "rebuild"]),
]

TIER2_STEPS: list[tuple[str, list[str]]] = [
    # sync-sector-links CHECKS only (2026-08-19): the write mutates sector
    # notes, so it is an explicit `make sync-sector-links` — housekeeping
    # never mutates notes. The check gates: stale sector indexes abort
    # maint-full with the exact remediation in the step's own output.
    (
        "sync-sector-links --check (gate: sector-note company indexes fresh)",
        ["python3", "helpers/maintenance/sync_sector_wikilinks.py", "--check"],
    ),
    # Same doctrine for the hierarchy notes: taxonomy + Child Sectors (auto)
    # regions + sector up-links are CHECKED here; the write (which also
    # writes entities/belongs_to edges) is the explicit --apply run.
    (
        "sector-hierarchy --check (gate: taxonomy + super-sector notes fresh)",
        ["python3", "helpers/maintenance/build_sector_hierarchy.py", "--check"],
    ),
    # company_embeddings refresh (2026-08-21, company_embeddings_maint
    # proposal): cached populate + GC of deleted companies — both this and
    # rebuild-note-search refresh derived indexes over note text, and notes
    # are not rewritten later in the stack. SQLite-only (company_embeddings
    # table; no entities/graph_edges writes -> no paired graph rebuild),
    # so maint-full-eligible per the placement invariant. Best-effort by
    # contract: --maint exits 0 with a WARNING when the local embedder is
    # unavailable or the table isn't bge-populated — maint never
    # auto-upgrades company embeddings (the upgrade is the user-held apply,
    # doc/procedures/embeddings.md). Warm no-change cycles cost reads +
    # hashes only (Q3 sidecar cache). v_embeddings.parquet drift after a
    # run that changed vectors is handled by the existing snapshot regen
    # flow (step 10 re-snapshots; DuckDB materialises on connect()).
    (
        "company-embeddings --maint (cached refresh of company_embeddings)",
        ["python3", "helpers/graph/embeddings.py", "--maint"],
    ),
    # rebuild-doc-search (2026-08-23, doc_search_embeddings proposal):
    # refresh the FTS5 + embeddings index over the repo's own doc/ corpus
    # (section-level chunks; the agent-queryable knowledge index). Sidecar-
    # only (memory/doc_search.db — gitignored, never snapshotted, and
    # deliberately NOT in research.db so doc/local/ privacy is structural,
    # never manifest-dependent). Writes no research.db tables, no
    # entities/graph_edges, no notes — maint-full-eligible per the placement
    # invariant. Pseudo-embedding fallback keeps this green on machines
    # without the model; warm cycles are reads + hashes (the shared
    # embed-cache discipline).
    (
        "rebuild-doc-search (refresh doc/ FTS+embeddings sidecar index)",
        ["python3", "helpers/maintenance/rebuild_doc_search.py"],
    ),
    (
        "recompute-graph (refresh analytics in graph_analytics)",
        ["python3", "helpers/graph/algorithms.py", "--all", "--apply"],
    ),
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
    (
        "derive-insights (capture concall quotes + magnitudes into DB; --no-notes)",
        ["python3", "helpers/graph/derive_insights.py", "findata", "--apply", "--no-notes"],
    ),
    (
        "derive-events (refresh events timeline from note prose + edges)",
        ["python3", "helpers/graph/derive_events.py", "--apply"],
    ),
    (
        "snapshot (re-snapshot to include recomputed analytics + events)",
        ["python3", "helpers/maintenance/snapshot_db.py"],
    ),
]
# NOTE: sync-sector-links used to WRITE here (regenerating the auto company
# index in sector notes). Since 2026-08-19 maint-full only CHECKS it; the
# write is the explicit `make sync-sector-links` (it mutates notes, and a
# housekeeping run must never mutate notes — same rule that keeps
# derive-insights note-rendering out of TIER2).

# TIER1 step labels dropped from the --full composition: their artifacts
# are unconditionally overwritten by the TIER2 tail snapshot, so running
# them mid-run is ~19 s of wasted work (maint_full_single_snapshot.md).
# Plain `make maint` keeps every TIER1 step (always-safe semantics).
TIER1_FULL_SKIP: frozenset[str] = frozenset(
    {
        "snapshot (refresh versioned snapshots)",
    }
)


@dataclass
class _StepResult:
    label: str
    rc: int
    seconds: float
    tail: list[str] = field(default_factory=list)


def _run_step(label: str, cmd: list[str], dry_run: bool, logger: logging.Logger) -> _StepResult:
    """Run one maintenance step: stream its output live to the console
    while keeping a rolling tail for the run report. Returns the result
    (rc 0, no output for a skipped dry-run step)."""
    logger.info("=" * 60)
    logger.info(label)
    logger.info("$ %s", " ".join(cmd))
    if dry_run:
        logger.info("(dry-run; skipping)")
        return _StepResult(label, 0, 0.0)
    capture: deque[str] = deque(maxlen=_CAPTURE_LINES)
    t0 = time.perf_counter()
    proc = subprocess.Popen(  # noqa: S603  # list-form call; shell=False (default); args are TIER*_STEPS constants
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None  # noqa: S101  # type narrowing only — stdout=PIPE guarantees a stream
    for raw in proc.stdout:
        sys.stdout.write(raw)
        sys.stdout.flush()
        capture.append(raw.rstrip("\n"))
    rc = proc.wait()
    if rc != 0:
        logger.error("step failed with exit %d: %s", rc, label)
    return _StepResult(label, rc, time.perf_counter() - t0, list(capture))


def _table_lines(results: list[_StepResult]) -> list[str]:
    """Perf-style summary table (console + report). Labels longer than
    the 56-char column widen the whole table instead of overflowing it
    and shifting the Time/Status columns (the long --check gate labels
    used to garble the report)."""
    width = max(56, max((len(r.label) for r in results), default=0))
    lines = ["", "Step" + " " * (width - 2) + "Time (s)   Status", "-" * (width + 32)]
    for r in results:
        flag = "✓" if r.rc == 0 else "✗"
        status = "OK" if r.rc == 0 else "FAIL"
        lines.append(f"  {r.label:.<{width}s} {r.seconds:8.2f}   {flag} {status}")
    lines.append("-" * (width + 32))
    ok = sum(1 for r in results if r.rc == 0)
    verdict = "PASS" if all(r.rc == 0 for r in results) else "FAIL (aborted)"
    lines.append(f"  {ok}/{len(results)} steps ok  ·  maint {verdict}")
    return lines


def _write_report(results: list[_StepResult], full: bool) -> None:
    """Append the run record: header + summary table, then the captured
    tail of every FAILED step (qa_report.txt philosophy — successes stay
    lean, failures keep their evidence)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "maint-full" if full else "maint"
    with open(REPORT_PATH, "a") as f:
        f.write(f"=== make {mode}  {ts}  (Python {sys.version.split()[0]}) ===\n")
        f.write("\n".join(_table_lines(results)) + "\n")
        for r in results:
            if r.rc == 0:
                continue
            n = min(_TAIL_LINES, len(r.tail))
            f.write(f"--- {r.label} · last {n} lines (FAILED) ---\n")
            f.write("\n".join(r.tail[-_TAIL_LINES:]) + "\n")
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--full",
        action="store_true",
        help="also run PRE_FULL index refresh (sync-tags, note-search) + "
        "TIER2 re-derivations (sector gates, embeddings, doc-search, "
        "analytics, insights, events) + re-snapshot (post-ingest cleanup)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="show the plan, do nothing",
    )
    p.add_argument("--log", default="INFO", help="Logging level.")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format=LOG_FORMAT,
    )
    logger = logging.getLogger("maint")

    # S1b: pre-warm Corpus cache once so PRE_FULL/TIER2 --corpus steps hit corpus.db (0.02s vs 0.37s walk)
    if args.full and _HAS_CORPUS and Corpus is not None:
        try:
            assert Corpus is not None  # noqa: S101  # ty narrow
            # workers=1 is faster for 1243 small files on this box (see perf_skills §15)
            Corpus.load("findata", workers=1, use_cache=True)
        except Exception as e:
            logger.debug("corpus pre-warm failed: %s", e)
    if args.full:
        steps = (
            list(PRE_FULL_STEPS)
            + [s for s in TIER1_STEPS if s[0] not in TIER1_FULL_SKIP]
            + list(TIER2_STEPS)
        )
    else:
        steps = list(TIER1_STEPS)

    logger.info(
        "Maintenance plan (%d steps%s):",
        len(steps),
        " --full" if args.full else "",
    )
    for i, (label, _) in enumerate(steps, 1):
        logger.info("  %d. %s", i, label)

    failures = 0
    results: list[_StepResult] = []
    for label, cmd in steps:
        result = _run_step(label, cmd, args.dry_run, logger)
        results.append(result)
        if result.rc != 0:
            failures += 1
            # Stop on first failure — later steps may depend on earlier
            # ones (e.g. snapshot must reflect a vacuumed DB; graph-rebuild
            # must match the snapshot).
            logger.error("aborting — step failed: %s", label)
            break

    if not args.dry_run:
        print("\n".join(_table_lines(results)))
        _write_report(results, args.full)
        print(f"appended to {REPORT_PATH.name}")

    if failures:
        logger.error("✗ maintenance failed (%d step(s) failed)", failures)
        return 1
    logger.info("✓ maintenance complete (%d steps)", len(steps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
