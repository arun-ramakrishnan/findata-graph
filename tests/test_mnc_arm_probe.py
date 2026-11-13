"""Tests for helpers/maintenance/mnc_arm_probe.py."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance.mnc_arm_probe import (  # noqa: E402
    _MISSING_ARMS,
    _entity_row,
    probe_arm,
)

COMPANIES = [
    ("ZF Commercial Vehicle Control Systems India", "company"),
    ("AstraZeneca Pharma India", "company"),
    ("AstraZeneca AB, Sweden", "company"),
    ("Novartis India", "company"),
    ("Abbott India", "company"),
    ("Strides Pharma Science", "company"),
    ("SBI", "company"),
    ("Person", "person"),
]


def _make_con():
    con = duckdb.connect(":memory:", **memory_cap_config())
    con.execute("CREATE TABLE entities (name VARCHAR, entity_type VARCHAR)")
    con.executemany("INSERT INTO entities VALUES (?, ?)", COMPANIES)
    con.execute(
        """
        CREATE TABLE entity_identifiers (
            entity_name VARCHAR, identifier_value VARCHAR,
            identifier_type VARCHAR, namespace VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE graph_edges (
            id INTEGER, source VARCHAR, target VARCHAR,
            edge_type VARCHAR, source_ref VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE hyper_edges (
            id INTEGER, edge_type VARCHAR, label VARCHAR,
            source_ref VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name VARCHAR)
        """
    )
    con.execute(
        """
        CREATE TABLE company_embeddings (company_name VARCHAR, embedding BLOB)
        """
    )
    import struct

    blob = struct.pack("<512f", *([0.25] * 512))
    con.execute("INSERT INTO company_embeddings VALUES (?, ?)", ("Strides Pharma Science", blob))
    con.execute("INSERT INTO company_embeddings VALUES (?, ?)", ("AstraZeneca Pharma India", blob))
    edges = [
        (
            1,
            "AstraZeneca Pharma India",
            "AstraZeneca AB, Sweden",
            "subsidiary_of",
            "vigil:rpt:ASTRAZENEC",
        ),
        (
            2,
            "AstraZeneca Pharma India",
            "Strides Pharma Science",
            "same_group",
            "vigil:rpt:ASTRAZENEC",
        ),
        (3, "SBI", "Person", "same_group", "vigil:rpt:SBIN"),
    ]
    con.executemany("INSERT INTO graph_edges VALUES (?, ?, ?, ?, ?)", edges)
    con.execute("INSERT INTO hyper_edges VALUES (1, 'sector', 'Pharma', 'x')")
    con.execute("INSERT INTO hyper_incidences VALUES (1, 'AstraZeneca Pharma India')")
    return con


def test_entity_row_fragment_resolution():
    con = _make_con()
    # no ticker rows exist; canonical fragments resolve to the Indian arm
    for ticker, expected in [
        ("ZFCV", "ZF Commercial Vehicle Control Systems India"),
        ("ASTRAZENEC", "AstraZeneca Pharma India"),
        ("NOVARTIS", "Novartis India"),
        ("ABBOTINDIA", "Abbott India"),
    ]:
        assert _entity_row(con, ticker) == expected
    con.close()


def test_probe_arm_evidence():
    con = _make_con()
    arm = probe_arm(con, "ASTRAZENEC")
    assert arm["entity"] == "AstraZeneca Pharma India"
    assert len(arm["subsidiary_of_edges"]) >= 1
    assert "Strides Pharma Science" in arm["same_group_members"]
    assert any(h["edge_type"] == "sector" for h in arm["hyperedges"])
    assert arm["verdict"] == "evidence"
    con.close()


def test_probe_arm_absent():
    con = _make_con()
    arm = probe_arm(con, "NOVARTIS")
    assert arm["entity"] == "Novartis India"
    assert arm["same_group_members"] == []
    assert arm["subsidiary_of_edges"] == []
    assert arm["verdict"] == "no-dyad-evidence"
    con.close()


def test_probe_all_arms_resolve():
    con = _make_con()
    for ticker in _MISSING_ARMS:
        arm = probe_arm(con, ticker)
        assert arm["entity"] is not None, ticker
    con.close()
