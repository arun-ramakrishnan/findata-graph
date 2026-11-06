#!/usr/bin/env python3
"""Backfill temporal anchors on sidecar-promoted graph edges (S3).

The B2 sidecar lane (findata/Misc/_relations/*.yaml) carries a human-audited
as_of and confidence for each relation. Before S3 the promotion writer dropped
both: every promoted edge got valid_from = NULL and its properties carried only
doc_type/b2_version/provenance. S3 fixes the writer
(helpers/graph/extract_relations.py::promote_relation_sidecars), but edges
promoted before the fix still need the anchor copied across.

This script re-derives the anchor from the surviving sidecar files. A row whose
sidecar file was deleted has nothing to re-derive from and is left untouched.

Usage:
    python3 helpers/maintenance/backfill_sidecar_temporal.py            # dry-run
    python3 helpers/maintenance/backfill_sidecar_temporal.py --apply    # write

Idempotent: the UPDATE only touches rows whose valid_from is still NULL, and
properties keys already present are never overwritten. Exit codes signal
execution failure, not data outcomes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.graph import extract_relations as _er  # noqa: E402


def _sidecar_temporal_index() -> dict[str, tuple[str | None, str | None, object]]:
    """Map sidecar filename -> (valid_from, properties.as_of, confidence)."""
    index: dict[str, tuple[str | None, str | None, object]] = {}
    if not _er.RELATIONS_DIR.exists():
        return index
    for path in sorted(_er.RELATIONS_DIR.glob("*.yaml")):
        try:
            data = _er.yaml_safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: S112  # best-effort; skip malformed YAML file
            continue
        if not isinstance(data, dict):
            continue
        # Attribute access (not a from-import) keeps the promotion writer the
        # single source of truth for as_of -> (valid_from, properties.as_of).
        valid_from, as_of_props = _er._sidecar_temporal_fields(data)
        index[path.name] = (valid_from, as_of_props, data.get("confidence"))
    return index


def backfill_sidecar_temporal(dry_run: bool = True, conn=None) -> tuple[int, int, int]:  # noqa: C901  # scan/backfill/report phases in one pass
    """Copy sidecar as_of/confidence onto promoted edges still missing them.

    Returns (scanned, updated, skipped): scanned counts promoted edges whose
    valid_from is still NULL, updated the re-derivable ones, skipped the orphans
    whose sidecar file no longer exists.
    """
    owned = conn is None
    conn = conn or connect()
    try:
        index = _sidecar_temporal_index()
        rows = conn.execute(
            "SELECT id, source_ref, properties FROM graph_edges "
            "WHERE source_ref LIKE 'promote:sidecar:%' "
            "AND (valid_from IS NULL OR valid_from = '')"
        ).fetchall()
        scanned = len(rows)
        updated = skipped = 0
        for row_id, source_ref, props_json in rows:
            slug = source_ref.split("promote:sidecar:", 1)[1]
            entry = index.get(slug)
            if entry is None:
                skipped += 1
                print(f"  orphan (sidecar file gone): edge {row_id} ({slug})")
                continue
            valid_from, as_of_props, confidence = entry
            if valid_from is None and as_of_props is None:
                skipped += 1
                continue
            props: dict = {}
            if props_json:
                try:
                    loaded = json.loads(props_json)
                except ValueError:
                    loaded = {}
                if isinstance(loaded, dict):
                    props = loaded
            if as_of_props is not None and "as_of" not in props:
                props["as_of"] = as_of_props
            if confidence is not None and "confidence" not in props:
                props["confidence"] = confidence
            updated += 1
            verb = "would set" if dry_run else "set"
            print(
                f"  {verb} valid_from={valid_from!r} as_of={as_of_props!r} "
                f"confidence={confidence!r} on edge {row_id} ({slug})"
            )
            if not dry_run:
                conn.execute(
                    "UPDATE graph_edges SET valid_from = ?, properties = ? "
                    "WHERE id = ? AND (valid_from IS NULL OR valid_from = '')",
                    (
                        valid_from,
                        json.dumps(props, ensure_ascii=False, sort_keys=True),
                        row_id,
                    ),
                )
        if not dry_run:
            conn.commit()
        return scanned, updated, skipped
    finally:
        if owned:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill valid_from/properties.as_of/confidence on sidecar-promoted edges (S3)."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the backfill (default: dry-run census)",
    )
    args = parser.parse_args(argv)
    scanned, updated, skipped = backfill_sidecar_temporal(dry_run=not args.apply)
    mode = "APPLY" if args.apply else "dry-run"
    print(
        f"sidecar temporal backfill ({mode}): candidates={scanned} "
        f"updated={updated} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
