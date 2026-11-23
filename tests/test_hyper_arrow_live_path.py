"""Pin the live loader of ``helpers/graph/hyper_arrow`` (S9).

The research store is DuckDB; the live loader and ``store_rowcount``
must follow ``helpers.core.db.connect`` rather than a SQLite-only path.
Hermetic: tmp-path stores only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers.core.db import connect
from helpers.graph import hyper_arrow as ha

EDGES = [("sector", "Banking", ["Alpha", "Beta"]), ("theme", "Digital", ["Beta", "Gamma"])]


def _duck_store(path: Path) -> None:
    conn = connect(path)
    conn.execute(
        "CREATE TABLE hyper_edges (id BIGINT, edge_type VARCHAR, label VARCHAR, weight DOUBLE)"
    )
    conn.execute(
        "CREATE TABLE hyper_incidences (edge_id BIGINT, entity_name VARCHAR, weight DOUBLE)"
    )
    for i, (edge_type, label, members) in enumerate(EDGES, start=1):
        conn.execute("INSERT INTO hyper_edges VALUES (?, ?, ?, NULL)", (i, edge_type, label))
        for m in members:
            conn.execute("INSERT INTO hyper_incidences VALUES (?, ?, NULL)", (i, m))
    conn.close()


def _expected() -> list[tuple[str, str, str]]:
    return sorted((et, lbl, m) for et, lbl, members in EDGES for m in members)


def _live_rows(path: Path) -> list[tuple[str, str, str]]:
    t = ha.load_incidence_arrow(["sector", "theme"], source="live", db_path=path)
    assert t.column_names == ["edge_type", "label", "node", "weight"]
    return sorted(
        zip(t["edge_type"].to_pylist(), t["label"].to_pylist(), t["node"].to_pylist(), strict=True)
    )


def test_live_load_reads_a_duckdb_store(tmp_path: Path):
    duck = tmp_path / "x.duckdb"
    _duck_store(duck)
    assert _live_rows(duck) == _expected()
    assert ha.store_rowcount(["sector", "theme"], db_path=duck) == 2


def test_live_load_follows_the_research_store_redirect(tmp_path: Path, monkeypatch):
    # The canonical default names the retired SQLite file; the funnel
    # redirects that one path (and only that path) to its .duckdb sibling.
    from helpers.core import db_engine

    monkeypatch.delenv("FINDATA_RESEARCH_ENGINE", raising=False)
    monkeypatch.delenv("FINDATA_RESEARCH_DB_PATH", raising=False)
    _duck_store(tmp_path / "research.duckdb")
    legacy = tmp_path / "research.db"
    monkeypatch.setattr(db_engine, "DEFAULT_DB_PATH", legacy)
    monkeypatch.setattr(ha, "DEFAULT_DB_PATH", legacy)
    assert not legacy.exists()

    assert _live_rows(legacy) == _expected()
    assert ha.store_rowcount(["sector", "theme"]) == 2
    assert not legacy.exists()


def test_absent_store_is_never_created(tmp_path: Path):
    legacy = tmp_path / "absent.db"
    assert ha.store_rowcount(["sector"], db_path=legacy) is None
    assert not legacy.exists() and not legacy.with_suffix(".duckdb").exists()


def test_store_without_incidence_tables_reports_absent(tmp_path: Path):
    duck = tmp_path / "bare.duckdb"
    connect(duck).close()
    assert ha.store_rowcount(["sector"], db_path=duck) is None


def test_live_matches_snapshot_on_the_default_snapshot_when_present():
    snap = ha.SNAPSHOT_DIR
    if not (snap / ha.HYPER_EDGES_PARQUET).exists():
        pytest.skip("snapshot parquet not on this checkout")
    live = ha.load_incidence_arrow(["edition"], source="live")
    ref = ha.load_incidence_arrow(["edition"], source="snapshot")
    assert live.num_rows > 0
    assert live.drop(["weight"]).equals(ref.drop(["weight"]))
