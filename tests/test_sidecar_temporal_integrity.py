"""S3 sidecar-temporal integrity counter tests.

Validates the check_relations() keys sidecar_temporal_missing (ERROR) and
sidecar_temporal_orphaned (report-only).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.misc.database_integrity_check import DatabaseIntegrityChecker  # noqa: E402

GRAPH_EDGES_DDL = (
    "CREATE TABLE IF NOT EXISTS graph_edges ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "source TEXT, target TEXT, edge_type TEXT, "
    "weight REAL, properties TEXT NOT NULL DEFAULT '{}', "
    "valid_from TEXT, valid_to TEXT, "
    "source_ref TEXT NOT NULL, symmetric INTEGER NOT NULL DEFAULT 0, "
    "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "agent_id TEXT, source_tier TEXT)"
)

TVS_SIDECAR = (
    "source: TVS Supply Chain Solutions\n"
    "target: Sankyu Corporation\n"
    "edge_type: jv_with\n"
    "as_of: '2026-10-03'\n"
    "confidence: 0.85\n"
    "provenance: manual\n"
    "b2_version: B2.1\n"
    "doc_type: sidecar\n"
)


def _seed_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE entities ("
        "name TEXT PRIMARY KEY, entity_type TEXT, file_path TEXT, "
        "normalized_name TEXT, sector_classification TEXT, ticker TEXT)"
    )
    conn.executemany(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) VALUES (?, ?, ?, ?)",
        [
            (
                "TVS Supply Chain Solutions",
                "company",
                "findata/Companies/T/TVS.md",
                "TVS_Supply_Chain_Solutions",
            ),
            (
                "Sankyu Corporation",
                "company",
                "findata/Companies/S/Sankyu.md",
                "Sankyu_Corporation",
            ),
            ("Acme Corp", "company", "findata/Companies/A/Acme.md", "Acme_Corp"),
            ("Deleted Corp", "company", "findata/Companies/D/Deleted.md", "Deleted_Corp"),
        ],
    )
    conn.execute(GRAPH_EDGES_DDL)
    conn.execute(
        "CREATE VIEW relations AS SELECT source, target, edge_type AS relation_type "
        "FROM graph_edges"
    )
    rows = [
        (
            "TVS Supply Chain Solutions",
            "Sankyu Corporation",
            "jv_with",
            "promote:sidecar:TVS_Supply_Chain_Solutions-jv_with-Sankyu_Corporation.yaml",
            json.dumps({"doc_type": "sidecar", "b2_version": "B2.1", "provenance": "manual"}),
            None,
        ),
        (
            "Acme Corp",
            "Deleted Corp",
            "acquired",
            "promote:sidecar:Acme_Corp-acquired-Deleted_Corp.yaml",
            json.dumps({"doc_type": "sidecar", "b2_version": "B2.1", "provenance": "manual"}),
            None,
        ),
        (
            "TVS Supply Chain Solutions",
            "Sankyu Corporation",
            "jv_with",
            "promote:sidecar:Already_Dated.yaml",
            json.dumps({"doc_type": "sidecar", "b2_version": "B2.1", "provenance": "manual"}),
            "2025-01-01",
        ),
    ]
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, "
        "properties, valid_from) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _seed_lane(base_path: Path) -> None:
    lane = base_path / "findata" / "Misc" / "_relations"
    lane.mkdir(parents=True, exist_ok=True)
    (lane / "TVS_Supply_Chain_Solutions-jv_with-Sankyu_Corporation.yaml").write_text(
        TVS_SIDECAR, encoding="utf-8"
    )


def _run_checker(db_path: Path, base_path: Path) -> dict:
    checker = DatabaseIntegrityChecker(db_path=str(db_path), base_path=str(base_path))
    try:
        return checker.check_relations()
    finally:
        checker.close()


def test_sidecar_temporal_missing_counts_error(tmp_path: Path) -> None:
    db_path = tmp_path / "check.db"
    _seed_db(db_path)
    _seed_lane(tmp_path)
    rel = _run_checker(db_path, tmp_path)
    assert rel["sidecar_temporal_missing"] == 1
    assert rel["sidecar_temporal_orphaned"] == 1
    assert rel["errors"] == 1


def test_sidecar_temporal_orphaned_report_only(tmp_path: Path) -> None:
    db_path = tmp_path / "check.db"
    _seed_db(db_path)
    rel = _run_checker(db_path, tmp_path)
    assert rel["sidecar_temporal_missing"] == 0
    assert rel["sidecar_temporal_orphaned"] == 2
    assert rel["errors"] == 0


def test_sidecar_temporal_already_dated_not_counted(tmp_path: Path) -> None:
    db_path = tmp_path / "check.db"
    _seed_db(db_path)
    _seed_lane(tmp_path)
    rel = _run_checker(db_path, tmp_path)
    assert rel["sidecar_temporal_missing"] == 1
    assert rel["sidecar_temporal_orphaned"] == 1
    assert rel["errors"] == 1
