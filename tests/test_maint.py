#!/usr/bin/env python3
"""Tests for helpers/maintenance/maint.py — the routine maintenance orchestrator.

These are pure unit tests against the orchestrator's plan/structure. No
subprocess spawning, no live DB. The actual maintenance steps (db_maint,
snapshot_db, query rebuild) have their own test coverage; here we only
pin the orchestrator's wiring.

Runs in QA (not live-marked) since it doesn't touch real DBs.
"""
from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance import maint  # noqa: E402


# --------------------------------------------------------------------------- #
# TestPlan — pin the orchestrator's structure                                 #
# --------------------------------------------------------------------------- #
class TestPlan:
    """Pin the step labels + commands so reordering is a deliberate
    test update, not a silent regression."""

    def test_tier1_has_three_steps(self):
        assert len(maint.TIER1_STEPS) == 3

    def test_tier1_steps_order(self):
        # The order is load-bearing:
        #   1. db_maint compacts SQLite first
        #   2. snapshot reflects the compacted state
        #   3. graph-rebuild produces a DuckDB cache matching the snapshot
        labels = [label for label, _ in maint.TIER1_STEPS]
        assert labels == [
            "db_maint (VACUUM/ANALYZE/REINDEX/integrity)",
            "snapshot (refresh versioned snapshots)",
            "graph-rebuild (refresh DuckDB cache)",
        ]

    def test_tier2_has_ten_steps(self):
        assert len(maint.TIER2_STEPS) == 10

    def test_tier2_steps_order(self):
        # Post-ingest cleanup: structural work first (sync-tags settles
        # entity sector_classification, sync-sector-links CHECKS the
        # company index — the write is explicit, housekeeping never
        # mutates notes — rebuild-note-search reads notes into the FTS
        # index and company-embeddings --maint refreshes the derived
        # company_embeddings table over the same note text (cached,
        # best-effort, never auto-upgrading); rebuild-doc-search refreshes
        # the doc/ corpus sidecar index (research.db untouched);
        # recompute-graph refreshes graph analytics, then derive-insights
        # scans newsletter concall bodies
        # into the quotes/company_metrics tables ONLY (--no-notes), then
        # derive-events refreshes the events timeline (D7) from note prose
        # rendered by the last standalone derive_insights --apply, then
        # re-snapshot captures the full post-ingest state.
        labels = [label for label, _ in maint.TIER2_STEPS]
        assert labels == [
            "sync-tags (rebuild entity_tags from note YAML)",
            "sync-sector-links --check (gate: sector-note company indexes fresh)",
            "sector-hierarchy --check (gate: taxonomy + super-sector notes fresh)",
            "rebuild-note-search (rebuild FTS over findata markdowns)",
            "company-embeddings --maint (cached refresh of company_embeddings)",
            "rebuild-doc-search (refresh doc/ FTS+embeddings sidecar index)",
            "recompute-graph (refresh analytics in graph_analytics)",
            "derive-insights (capture concall quotes + magnitudes into DB; --no-notes)",
            "derive-events (refresh events timeline from note prose + edges)",
            "snapshot (re-snapshot to include recomputed analytics + events)",
        ]

    def test_derive_insights_precedes_derive_events(self):
        # Ordering retained for stability: with --no-notes, derive-insights
        # writes only the quotes/company_metrics DB tables (no note prose),
        # and derive-events reads note prose rendered by the last standalone
        # derive_insights --apply — so there is no longer a within-run data
        # dependency. Order kept so a future revert to in-maint-full note-
        # rendering would keep working, and for log readability.
        labels = [label for label, _ in maint.TIER2_STEPS]
        assert labels.index(
            "derive-insights (capture concall quotes + magnitudes into DB; --no-notes)"
        ) < labels.index(
            "derive-events (refresh events timeline from note prose + edges)"
        )

    def test_tier1_db_maint_runs_before_snapshot(self):
        # The key ordering invariant: db_maint must precede snapshot
        # so the snapshot reflects a vacuumed state.
        labels = [label for label, _ in maint.TIER1_STEPS]
        assert labels.index("db_maint (VACUUM/ANALYZE/REINDEX/integrity)") < \
               labels.index("snapshot (refresh versioned snapshots)")

    def test_tier1_snapshot_runs_before_graph_rebuild(self):
        # snapshot must precede graph-rebuild so the cache matches the
        # committed snapshot, not a pre-snapshot SQLite state.
        labels = [label for label, _ in maint.TIER1_STEPS]
        assert labels.index("snapshot (refresh versioned snapshots)") < \
               labels.index("graph-rebuild (refresh DuckDB cache)")


# --------------------------------------------------------------------------- #
# TestCommands — pin the command shape                                        #
# --------------------------------------------------------------------------- #
class TestCommands:
    """The orchestrator shells out via subprocess; pin the command shape."""

    def test_all_commands_use_python3(self):
        # All commands must invoke `python3 <script>` explicitly. Using
        # bare script paths would fail on Windows and skip the venv shim.
        for _, cmd in maint.TIER1_STEPS + maint.TIER2_STEPS:
            assert cmd[0] == "python3", f"command doesn't start with python3: {cmd}"

    def test_all_scripts_exist(self):
        # Every referenced script path must exist relative to PROJECT_ROOT.
        # A typo or moved file would silently fail at runtime.
        for label, cmd in maint.TIER1_STEPS + maint.TIER2_STEPS:
            script = cmd[1]
            path = maint.PROJECT_ROOT / script
            assert path.exists(), f"{label}: script not found: {path}"

    def test_db_maint_command_has_no_flags(self):
        # Default invocation — no --sync-check (that's a separate concern),
        # no --dry-run (the orchestrator handles dry-run at its own level).
        cmd = dict(maint.TIER1_STEPS)["db_maint (VACUUM/ANALYZE/REINDEX/integrity)"]
        assert cmd == ["python3", "helpers/maintenance/db_maint.py"]

    def test_snapshot_command_includes_duckdb_by_default(self):
        # snapshot_db.py defaults to --with-duckdb (on), so the orchestrator
        # doesn't need to pass it explicitly. But it also doesn't pass
        # --no-duckdb, which would skip the DuckDB snapshot.
        cmd = dict(maint.TIER1_STEPS)["snapshot (refresh versioned snapshots)"]
        assert "--no-duckdb" not in cmd
        # The default IS with-duckdb, so we don't need to pass it.
        assert cmd == ["python3", "helpers/maintenance/snapshot_db.py"]

    def test_recompute_graph_command_uses_apply(self):
        # The whole point of maint-full is to persist analytics.
        cmd = dict(maint.TIER2_STEPS)[
            "recompute-graph (refresh analytics in graph_analytics)"
        ]
        assert "--all" in cmd
        assert "--apply" in cmd

    def test_derive_insights_in_maint_full_is_db_only(self):
        # maint-full must run derive-insights with --no-notes so a housekeeping
        # run never mutates company notes (the placement invariant). Note-
        # rendering lives in the standalone derive_insights --apply path (make is dry-run).
        # Removing --no-notes here reintroduces the profile-stripping class
        # of bug.
        cmd = dict(maint.TIER2_STEPS)[
            "derive-insights (capture concall quotes + magnitudes into DB; --no-notes)"
        ]
        assert "--apply" in cmd
        assert "--no-notes" in cmd


# --------------------------------------------------------------------------- #
# TestDryRun                                                                  #
# --------------------------------------------------------------------------- #
class TestDryRun:
    """--dry-run must show the plan without executing anything."""

    def test_dry_run_returns_zero(self, capsys):
        rc = maint.main(["--dry-run"])
        assert rc == 0

    def test_dry_run_does_not_invoke_subprocess(self, monkeypatch):
        # Spy on subprocess.run — it should never be called in dry-run.
        called = {"count": 0}

        def spy_run(*a, **kw):
            called["count"] += 1
            # Return a fake CompletedProcess-like object in case it's read.
            class _R:
                returncode = 0
            return _R()

        monkeypatch.setattr(maint.subprocess, "run", spy_run)
        maint.main(["--dry-run"])
        assert called["count"] == 0, "dry-run must not invoke subprocess"

    def test_dry_run_lists_all_tier1_steps(self, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="maint"):
            maint.main(["--dry-run"])
        output = caplog.text
        for label, _ in maint.TIER1_STEPS:
            assert label in output, f"tier1 step missing from dry-run output: {label}"

    def test_dry_run_full_lists_all_steps(self, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="maint"):
            maint.main(["--full", "--dry-run"])
        output = caplog.text
        all_steps = maint.TIER1_STEPS + maint.TIER2_STEPS
        # 3 tier1 (db_maint, snapshot, graph-rebuild) + 10 tier2.
        assert len(all_steps) == 13
        for label, _ in all_steps:
            assert label in output, f"step missing from --full dry-run: {label}"


# --------------------------------------------------------------------------- #
# TestSubprocessFailure — first-failure-aborts semantics                     #
# --------------------------------------------------------------------------- #
class TestSubprocessFailure:
    """If a step fails, the orchestrator must abort — later steps may
    depend on earlier ones (e.g. snapshot must reflect a vacuumed DB)."""

    def test_first_failure_aborts(self, monkeypatch):
        # Stub subprocess.run to fail on the first call and succeed after.
        call_count = {"n": 0}

        def fake_run(cmd, *a, **kw):
            call_count["n"] += 1
            class _R:
                returncode = 1 if call_count["n"] == 1 else 0
            return _R()

        monkeypatch.setattr(maint.subprocess, "run", fake_run)
        rc = maint.main([])
        assert rc == 1
        # Should have stopped after the first failure, not continued.
        assert call_count["n"] == 1, "must abort on first failure, not continue"

    def test_all_succeed_returns_zero(self, monkeypatch):
        def fake_run(cmd, *a, **kw):
            class _R:
                returncode = 0
            return _R()

        monkeypatch.setattr(maint.subprocess, "run", fake_run)
        rc = maint.main([])
        assert rc == 0

    def test_failure_in_full_mode_aborts_before_tier2(self, monkeypatch):
        # If step 3 (graph-rebuild, last tier1 step) fails, tier2 must not run.
        call_count = {"n": 0}
        invoked_cmds: list[str] = []

        def fake_run(cmd, *a, **kw):
            call_count["n"] += 1
            invoked_cmds.append(cmd[1])  # script path
            # Fail on the 3rd call (graph-rebuild).
            class _R:
                returncode = 1 if call_count["n"] == 3 else 0
            return _R()

        monkeypatch.setattr(maint.subprocess, "run", fake_run)
        rc = maint.main(["--full"])
        assert rc == 1
        # Tier1 steps ran; tier2 must NOT have.
        assert "helpers/core/sync_tags.py" not in invoked_cmds
        assert "helpers/graph/algorithms.py" not in invoked_cmds
