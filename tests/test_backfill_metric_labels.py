"""Backfill metric-label script — row-keying, NULL-safety, exit semantics.

Pins the 2026-10-15 audit fixes (completed.md #395 revisit): the UPDATE
keys on the `id` PK (not (entity, value_raw), which has 9 live duplicate
groups), a labeled row can never be overwritten, and a completed run
over an already-clean table is a success, not an error.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.maintenance import backfill_metric_labels as bfm  # noqa: E402


def _tmp_db(tmp_path: Path, rows: list[tuple]) -> sqlite3.Connection:
    """Minimal company_metrics mirror (the module touches only these cols)."""
    conn = sqlite3.connect(tmp_path / "company_metrics.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE company_metrics ("
        "id INTEGER PRIMARY KEY, entity TEXT, metric_label TEXT, "
        "value_raw TEXT, value_num REAL, unit TEXT, source_quote TEXT)"
    )
    conn.executemany(
        "INSERT INTO company_metrics (entity, metric_label, value_raw, "
        "value_num, unit, source_quote) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return conn


def test_duplicate_value_raw_rows_labeled_independently(tmp_path):
    """Two rows sharing (entity, value_raw) with different quotes get their
    OWN labels — the pre-fix (entity, value_raw)-keyed UPDATE let one
    row's quote classify its duplicate (9 live duplicate groups)."""
    conn = _tmp_db(
        tmp_path,
        [
            (
                "Blue Star",
                None,
                "40%",
                40.0,
                "percent",
                "EBITDA margin guidance for the quarter at 40%",
            ),
            (
                "Blue Star",
                None,
                "40%",
                13.0,
                "percent",
                "revenue up 13% yoy on broad-based demand",
            ),
        ],
    )
    updated, skipped, total = bfm.backfill_metric_labels(dry_run=False, conn=conn)
    assert (updated, skipped, total) == (2, 0, 2)
    labels = {
        r["source_quote"]: r["metric_label"]
        for r in conn.execute("SELECT source_quote, metric_label FROM company_metrics")
    }
    assert labels["EBITDA margin guidance for the quarter at 40%"] == "guidance_ebitda_margin"
    assert labels["revenue up 13% yoy on broad-based demand"] == "revenue"


def test_existing_label_never_overwritten(tmp_path):
    """The UPDATE carries AND metric_label IS NULL — a classified row is
    sticky, whatever the quote would reclassify it to."""
    conn = _tmp_db(
        tmp_path,
        [
            (
                "Acme",
                "revenue",
                "40%",
                40.0,
                "percent",
                "EBITDA margin guidance for the quarter at 40%",
            )
        ],
    )
    updated, skipped, total = bfm.backfill_metric_labels(dry_run=False, conn=conn)
    assert (updated, skipped, total) == (0, 0, 0)
    label = conn.execute(
        "SELECT metric_label FROM company_metrics WHERE entity = 'Acme'"
    ).fetchone()[0]
    assert label == "revenue"


def test_clean_table_is_success(tmp_path):
    """A completed run over an already-clean table returns zeros — the
    CLI maps that to exit 0 (data outcomes are not exit codes, #380)."""
    conn = _tmp_db(
        tmp_path,
        [("Acme", "revenue", "100", 100.0, "crore", "revenue of 100 crore")],
    )
    assert bfm.backfill_metric_labels(dry_run=False, conn=conn) == (0, 0, 0)


def test_dry_run_touches_nothing(tmp_path):
    conn = _tmp_db(
        tmp_path,
        [("Acme", None, "100", 100.0, "crore", "revenue of 100 crore")],
    )
    updated, skipped, total = bfm.backfill_metric_labels(dry_run=True, conn=conn)
    assert (updated, total) == (1, 1) and skipped == 0
    label = conn.execute(
        "SELECT metric_label FROM company_metrics WHERE entity = 'Acme'"
    ).fetchone()[0]
    assert label is None  # dry-run: classified but not written
