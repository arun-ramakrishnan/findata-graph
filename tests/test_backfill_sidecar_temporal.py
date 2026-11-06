"""Backfill sidecar temporal anchors - dry-run no-op, apply fills, idempotence.

S3 (polyadic_capture_edge_fidelity): a promoted edge whose sidecar file still
exists MUST gain valid_from/confidence on apply; a promoted edge whose sidecar
file was deleted is skipped. Idempotent: second apply is a no-op.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.graph import extract_relations as _er  # noqa: E402
from helpers.maintenance import backfill_sidecar_temporal as bst  # noqa: E402

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


def _seed_edges(conn: sqlite3.Connection) -> None:
    conn.execute(GRAPH_EDGES_DDL)
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, "
        "properties) VALUES (?, ?, ?, ?, ?)",
        [
            (
                "TVS Supply Chain Solutions",
                "Sankyu Corporation",
                "jv_with",
                "promote:sidecar:TVS_Supply_Chain_Solutions-jv_with-Sankyu_Corporation.yaml",
                json.dumps({"doc_type": "sidecar", "b2_version": "B2.1", "provenance": "manual"}),
            ),
            (
                "Acme Corp",
                "Deleted Corp",
                "acquired",
                "promote:sidecar:Acme_Corp-acquired-Deleted_Corp.yaml",
                json.dumps({"doc_type": "sidecar", "b2_version": "B2.1", "provenance": "manual"}),
            ),
        ],
    )
    conn.commit()


def _write_lane(tmp_path: Path, monkeypatch) -> Path:
    lane = tmp_path / "findata" / "Misc" / "_relations"
    lane.mkdir(parents=True, exist_ok=True)
    (lane / "TVS_Supply_Chain_Solutions-jv_with-Sankyu_Corporation.yaml").write_text(
        "source: TVS Supply Chain Solutions\n"
        "target: Sankyu Corporation\n"
        "edge_type: jv_with\n"
        "as_of: '2026-10-03'\n"
        "confidence: 0.85\n"
        "provenance: manual\n"
        "b2_version: B2.1\n"
        "doc_type: sidecar\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_er, "RELATIONS_DIR", lane)
    return lane


def test_backfill_dry_run_is_noop(tmp_path: Path, monkeypatch) -> None:
    _write_lane(tmp_path, monkeypatch)
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.row_factory = sqlite3.Row
    _seed_edges(conn)
    scanned, updated, skipped = bst.backfill_sidecar_temporal(dry_run=True, conn=conn)
    assert scanned == 2
    assert updated == 1
    assert skipped == 1
    row = conn.execute(
        "SELECT valid_from FROM graph_edges WHERE source_ref LIKE '%TVS%'"
    ).fetchone()
    assert row["valid_from"] is None
    conn.close()


def test_backfill_apply_fills_valid_from_and_confidence(tmp_path: Path, monkeypatch) -> None:
    _write_lane(tmp_path, monkeypatch)
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.row_factory = sqlite3.Row
    _seed_edges(conn)
    scanned, updated, skipped = bst.backfill_sidecar_temporal(dry_run=False, conn=conn)
    assert (scanned, updated, skipped) == (2, 1, 1)
    row = conn.execute(
        "SELECT valid_from, properties FROM graph_edges WHERE source_ref LIKE '%TVS%'"
    ).fetchone()
    assert row["valid_from"] == "2026-10-03"
    assert json.loads(row["properties"])["confidence"] == 0.85
    orphan = conn.execute(
        "SELECT valid_from FROM graph_edges WHERE source_ref LIKE '%Acme%'"
    ).fetchone()
    assert orphan["valid_from"] is None
    conn.close()


def test_backfill_idempotent_second_apply_is_noop(tmp_path: Path, monkeypatch) -> None:
    _write_lane(tmp_path, monkeypatch)
    conn = sqlite3.connect(tmp_path / "test.db")
    _seed_edges(conn)
    bst.backfill_sidecar_temporal(dry_run=False, conn=conn)
    scanned, updated, skipped = bst.backfill_sidecar_temporal(dry_run=False, conn=conn)
    assert (scanned, updated, skipped) == (1, 0, 1)
    conn.close()


def test_sidecar_temporal_index_shape(tmp_path: Path, monkeypatch) -> None:
    _write_lane(tmp_path, monkeypatch)
    idx = bst._sidecar_temporal_index()
    vf, as_of, conf = idx["TVS_Supply_Chain_Solutions-jv_with-Sankyu_Corporation.yaml"]
    assert vf == "2026-10-03"
    assert as_of is None
    assert conf == 0.85
    assert "Acme_Corp-acquired-Deleted_Corp.yaml" not in idx
