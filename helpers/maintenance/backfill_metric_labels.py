#!/usr/bin/env python3
"""Backfill NULL metric_label rows using the improved classifier.

Usage:
    python3 helpers/maintenance/backfill_metric_labels.py [--dry-run]

Audit fixes (2026-10-15, completed.md #395 revisit): rows are keyed on
the company_metrics `id` PK — (entity, value_raw) is not unique (9 live
duplicate groups), so one row's quote must never label its duplicate —
and the UPDATE carries `AND metric_label IS NULL` so the pass can never
overwrite an existing label. A completed run exits 0 regardless of how
many rows it labeled (exit codes signal execution failure, not data
outcomes — the #380 doctrine).
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402  # needs the shim above
from helpers.graph import derive_insights as di  # noqa: E402  # needs the shim above


def count_null_labels(conn) -> tuple[int, int]:
    """Count NULL metric_label rows and affected entities."""
    rows = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT entity) FROM company_metrics WHERE metric_label IS NULL"
    ).fetchone()
    return rows[0], rows[1]


def get_null_rows(conn, limit: int | None = None) -> list[dict]:
    """Fetch NULL metric_label rows with context needed for classification."""
    query = """
        SELECT id, entity, value_raw, value_num, unit, source_quote
        FROM company_metrics
        WHERE metric_label IS NULL
        ORDER BY entity, source_quote
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    return [
        {
            "id": r[0],
            "entity": r[1],
            "value_raw": r[2],
            "value_num": r[3],
            "unit": r[4],
            "source_quote": r[5],
        }
        for r in rows
    ]


def classify_row(row: dict) -> str | None:
    """Apply classifier to a row's source_quote.

    Uses direct pattern matching on source_quote text for more robust
    classification when value_raw positioning is unreliable.
    """
    source_quote = row["source_quote"] or ""
    if not source_quote:
        return None

    low = source_quote.lower()

    # Check for guidance prefix
    is_guidance = "guidance" in low

    # Try each pattern
    for pat, label in di._METRIC_LABEL_PATTERNS:
        if pat.search(low):
            return f"guidance_{label}" if is_guidance else label

    # Check margin pattern
    if di._L_MARGIN.search(low):
        label = "ebitda_margin" if di._L_EBITDA.search(low) else "margin"
        return f"guidance_{label}" if is_guidance else label

    return None


def backfill_metric_labels(dry_run: bool = False, conn=None) -> tuple[int, int, int]:
    """Backfill NULL metric_label rows.

    Returns: (updated_count, skipped_count, total_processed)
    """
    owned = conn is None
    conn = conn or connect()
    try:
        null_count, entity_count = count_null_labels(conn)
        if null_count == 0:
            print("No NULL metric_label rows found.")
            return 0, 0, 0

        print(f"Found {null_count} NULL metric_label rows across {entity_count} entities")

        if dry_run:
            print("DRY RUN MODE - no changes will be made")

        rows = get_null_rows(conn)
        updated = 0
        skipped = 0

        for row in rows:
            label = classify_row(row)
            if label:
                if not dry_run:
                    conn.execute(
                        "UPDATE company_metrics SET metric_label = ? "
                        "WHERE id = ? AND metric_label IS NULL",
                        (label, row["id"]),
                    )
                updated += 1
            else:
                skipped += 1

        if not dry_run and owned:
            conn.commit()

        print("\nResults:")
        print(f"  Updated: {updated} rows")
        print(f"  Skipped (remain NULL): {skipped} rows")
        if dry_run:
            print("\nTo apply changes, run without --dry-run flag")

        return updated, skipped, len(rows)

    finally:
        if owned:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill NULL metric_label rows")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without applying"
    )
    args = parser.parse_args()

    backfill_metric_labels(dry_run=args.dry_run)
    # A completed run is a success: exit codes signal execution failure,
    # not how many rows the data happened to have (the #380 doctrine).
    sys.exit(0)


if __name__ == "__main__":
    main()
