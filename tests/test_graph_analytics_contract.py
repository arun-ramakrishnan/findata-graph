"""graph_analytics_contract S1 — the served-universe contract table.

Covers the four acceptance surfaces from the proposal:
byte-identical parity (seeded reader == legacy self-read, order
included), unseeded fallback, dry-run writes nothing, FK cascade keeps
the delete/rename self-healing, and seed idempotence.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from helpers.core.db import connect
from helpers.graph.l1_betweenness import BETWEENNESS_METRIC
from helpers.graph.scipy_bridge import (
    CLOSINESS_METRIC,
    HARMONIC_METRIC,
    contract_sources,
)
from helpers.maintenance.seed_analytics_contract import CONTRACT_DDL, SERVED_METRICS, seed

# PK order (metric, entity_name) means the legacy DISTINCT returns
# entity_name ASC — deliberately insert in a NON-sorted order so the
# parity test proves seq captures legacy ORDER, not insertion order.
_ENTITIES = ["Xavier Corp", "Alpha Ltd", "Midden Co", "Bravo Inc"]
_ANALYTICS = [
    # (entity_name, metric, value)
    ("Xavier Corp", CLOSINESS_METRIC, "0.5"),
    ("Alpha Ltd", CLOSINESS_METRIC, "0.6"),
    ("Midden Co", CLOSINESS_METRIC, "0.7"),
    ("Bravo Inc", CLOSINESS_METRIC, "0.8"),
    ("Alpha Ltd", HARMONIC_METRIC, "0.4"),
    ("Bravo Inc", HARMONIC_METRIC, "0.3"),
    ("Midden Co", BETWEENNESS_METRIC, "0.2"),
]

_FIXTURE_DDL = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    file_path TEXT,
    sector_classification TEXT,
    ticker TEXT,
    last_updated DATETIME
);
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
CREATE TABLE graph_analytics (
    entity_name TEXT NOT NULL
                  REFERENCES entities(name) ON DELETE CASCADE ON UPDATE CASCADE,
    metric      TEXT NOT NULL,
    value       TEXT NOT NULL,
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric, entity_name)
);
"""


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "research.db"
    con = connect(db)
    try:
        con.executescript(_FIXTURE_DDL)
        con.executemany(
            "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
            ((n,) for n in _ENTITIES),
        )
        con.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
            "VALUES ('Alpha Ltd', 'Bravo Inc', 'competes_with', 'test')"
        )
        con.executemany(
            "INSERT INTO graph_analytics (entity_name, metric, value) VALUES (?, ?, ?)",
            _ANALYTICS,
        )
        con.commit()
    finally:
        con.close()
    return db


def _legacy(db: Path, metric: str) -> list[str]:
    """The pre-S1 definition, verbatim — what contract_sources must match."""
    con = connect(db, read_only=True, row_factory=None)
    try:
        return [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT entity_name FROM graph_analytics WHERE metric = ?",
                (metric,),
            ).fetchall()
        ]
    finally:
        con.close()


def test_unseeded_falls_back_to_legacy_self_read(db_path):
    """No contract table anywhere → the reader IS the legacy query (byte
    for byte, per served metric). Restore-safe by construction."""
    for metric in SERVED_METRICS:
        assert contract_sources(db_path, metric=metric) == _legacy(db_path, metric)


def test_seed_apply_parity_including_order(db_path):
    """The S1 acceptance: after seeding, the reader returns the legacy
    contract LIST-EQUAL (order included) for every served metric."""
    report = seed(db_path, apply=True)
    for metric in SERVED_METRICS:
        legacy_n, table_n = report[metric]
        assert legacy_n == table_n
        assert contract_sources(db_path, metric=metric) == _legacy(db_path, metric)


def test_seed_dry_run_writes_nothing(db_path):
    report = seed(db_path, apply=False)
    # The report measures the would-be seed...
    assert all(legacy > 0 for legacy, _ in report.values())
    # ...but no table materialized and the reader still falls back.
    con = connect(db_path, read_only=True)
    try:
        assert (
            con.execute(
                "SELECT count(*) FROM sqlite_master WHERE name = 'graph_analytics_contract'"
            ).fetchone()[0]
            == 0
        )
    finally:
        con.close()
    assert contract_sources(db_path, metric=CLOSINESS_METRIC) == _legacy(db_path, CLOSINESS_METRIC)


def test_seed_idempotent_and_seq_stable(db_path):
    """A second --apply changes nothing: same counts, same order — the
    ON CONFLICT DO NOTHING + seq pinning make re-runs no-ops."""
    seed(db_path, apply=True)
    first = {m: contract_sources(db_path, metric=m) for m in SERVED_METRICS}
    seed(db_path, apply=True)
    second = {m: contract_sources(db_path, metric=m) for m in SERVED_METRICS}
    assert first == second


def test_fk_cascade_keeps_self_healing(db_path):
    """The delete/rename healing the self-read inherited from
    graph_analytics' FK now lives in the contract table's own FK: delete
    the entity, the contract row goes with it, the served set shrinks."""
    seed(db_path, apply=True)
    assert "Xavier Corp" in contract_sources(db_path, metric=CLOSINESS_METRIC)
    con = connect(db_path)
    try:
        con.execute("DELETE FROM entities WHERE name = 'Xavier Corp'")
        con.commit()
    finally:
        con.close()
    assert "Xavier Corp" not in contract_sources(db_path, metric=CLOSINESS_METRIC)
    assert contract_sources(db_path, metric=CLOSINESS_METRIC) == _legacy(db_path, CLOSINESS_METRIC)


def test_partial_metric_seeding_falls_back_per_metric(db_path):
    """A table seeded for closeness only must NOT freeze the other
    metrics: each metric falls back independently until its own seed."""
    con = connect(db_path)
    try:
        con.execute(CONTRACT_DDL)
        con.executemany(
            "INSERT INTO graph_analytics_contract (metric, entity_name, seq) VALUES (?, ?, ?)",
            ((CLOSINESS_METRIC, n, i) for i, n in enumerate(_legacy(db_path, CLOSINESS_METRIC))),
        )
        con.commit()
    finally:
        con.close()
    # seeded metric: served from the table
    assert contract_sources(db_path, metric=CLOSINESS_METRIC) == _legacy(db_path, CLOSINESS_METRIC)
    # unseeded metrics: legacy fallback, not an empty freeze
    for metric in (HARMONIC_METRIC, BETWEENNESS_METRIC):
        assert contract_sources(db_path, metric=metric) == _legacy(db_path, metric)


def test_schema_bump_constants_consistent():
    """The schema stamp rides EXPECTED_USER_VERSION and its db_meta mirror;
    they must move together (integrity_check errors on drift).

    Pins the INVARIANT, not the number: a literal here breaks on every
    legitimate bump (v15 = speaker_identity, person_ontology_spine S2)
    without saying anything the equality below does not already say.
    """
    from helpers.core.db import EXPECTED_SCHEMA_VERSION, EXPECTED_USER_VERSION

    assert EXPECTED_USER_VERSION >= 14  # v14 = graph_analytics_contract floor
    assert EXPECTED_SCHEMA_VERSION == str(EXPECTED_USER_VERSION)


def test_no_stray_sqlite_connection_leak(db_path):
    """The reader opens RO and closes in a finally — hammer it and make
    sure nothing accumulates (sqlite3 handles on tmp dbs can block
    tmp_path cleanup)."""
    for _ in range(5):
        assert isinstance(contract_sources(db_path), list)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.close()  # just prove the file is not exclusively locked
