"""Fuzz tests — semantic_neighbors robustness invariants (Bundle N5).

Property-based tests (via Hypothesis) for `helpers/graph/query.py`:
``semantic_neighbors()`` against a synthetic populated DB (module-scoped,
built once — each Hypothesis example is a cheap warm query, not a rebuild).

Invariants pinned:
  1. For ANY company string and k: the call never raises and returns a list
     of (name, sector, score) 3-tuples; len <= max(k, 0); names unique;
     scores sorted descending (cosine).
  2. For the real seeded companies, the queried company itself is never in
     its own result set.
  3. metric must be 'cosine' or 'ip' — anything else raises ValueError.
  4. Negative/zero k is clamped, never crashes (regression: k=-1 raised
     BinderException "LIMIT/OFFSET cannot be negative").

Marked ``live``: building the graph materialisation needs duckdb + vss
extensions. No production data is used.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

duckdb = pytest.importorskip("duckdb")

from hypothesis import given, settings, strategies as st  # noqa: E402

from helpers.graph.query import clear_graph_cache, connect, semantic_neighbors  # noqa: E402

COMPANIES = ["CEAT", "Apollo Tyres", "MRF", "Infosys", "TCS"]


def _build_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE entities (
            name TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
            sector_classification TEXT, ticker TEXT, file_path TEXT,
            normalized_name TEXT
        );
        CREATE TABLE entity_tags (
            entity_name TEXT NOT NULL, tag TEXT NOT NULL,
            PRIMARY KEY (entity_name, tag)
        );
        CREATE TABLE graph_edges (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, target TEXT NOT NULL,
            edge_type TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
            properties TEXT NOT NULL DEFAULT '{}', valid_from DATE,
            valid_to DATE, source_ref TEXT NOT NULL,
            symmetric INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, target, edge_type), CHECK (source != target)
        );
        """
    )
    rows = [
        ("CEAT", "company", "Automotive", "CEAT.NS",
         "findata/Companies/Automotive/CEAT.md"),
        ("Apollo Tyres", "company", "Automotive", "APOLLOTYRE.NS",
         "findata/Companies/Automotive/Apollo_Tyres.md"),
        ("MRF", "company", "Automotive", "MRF.NS",
         "findata/Companies/Automotive/MRF.md"),
        ("Infosys", "company", "Technology", "INFY.NS",
         "findata/Companies/Technology/Infosys.md"),
        ("TCS", "company", "Technology", "TCS.NS",
         "findata/Companies/Technology/Tata_Consultancy_Services.md"),
        ("Automotive", "sector", None, None, "findata/Sectors/Automotive.md"),
        ("Technology", "sector", None, None, "findata/Sectors/Technology.md"),
    ]
    conn.executemany(
        "INSERT INTO entities (name, entity_type, sector_classification, "
        "ticker, file_path) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,?)",
        [
            ("CEAT", "Automotive", "part_of", "seed"),
            ("Apollo Tyres", "Automotive", "part_of", "seed"),
            ("MRF", "Automotive", "part_of", "seed"),
            ("Infosys", "Technology", "part_of", "seed"),
            ("TCS", "Technology", "part_of", "seed"),
        ],
    )
    from helpers.graph.embeddings import populate_dry_run

    populate_dry_run(conn, dims=8)
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def fcon(tmp_path_factory):
    """One warm DuckDB connection over a populated synthetic DB, shared by
    all Hypothesis examples in this module."""
    db_path = tmp_path_factory.mktemp("semfuzz") / "synth.db"
    _build_db(db_path)
    clear_graph_cache()
    con = connect(db_path=db_path)
    yield con
    con.close()
    clear_graph_cache()


@settings(max_examples=100, deadline=None)
@given(
    st.one_of(st.sampled_from(COMPANIES), st.text(min_size=0, max_size=40)),
    st.integers(min_value=-5, max_value=20),
)
def test_fuzz_semantic_neighbors_typed_and_bounded(fcon, company: str, k: int):
    """Invariants 1+2+4: no crash for arbitrary company/k; well-typed,
    bounded, unique, self-excluded, sorted results."""
    results = semantic_neighbors(fcon, company, k=k)
    assert isinstance(results, list)
    assert len(results) <= max(k, 0)
    names = []
    for r in results:
        assert isinstance(r, tuple) and len(r) == 3
        name, sector, score = r
        assert isinstance(name, str)
        assert sector is None or isinstance(sector, str)
        assert isinstance(score, float)
        names.append(name)
    assert len(set(names)) == len(names)  # no duplicate companies
    if company in COMPANIES:
        assert company not in names  # never self-recommend
    scores = [r[2] for r in results]
    assert scores == sorted(scores, reverse=True)  # cosine: desc


@settings(max_examples=50, deadline=None)
@given(
    st.sampled_from(COMPANIES),
    st.integers(min_value=0, max_value=10),
    st.booleans(),
)
def test_fuzz_semantic_neighbors_cross_sector_flag(fcon, company: str, k: int, cross_sector: bool):
    """cross_sector=True must exclude same-sector neighbours (when the
    reference company is one of the seeded ones)."""
    results = semantic_neighbors(fcon, company, k=k, cross_sector=cross_sector)
    assert isinstance(results, list)
    if cross_sector and company in COMPANIES:
        own_sector = {
            "CEAT": "Automotive", "Apollo Tyres": "Automotive", "MRF": "Automotive",
            "Infosys": "Technology", "TCS": "Technology",
        }[company]
        assert all(r[1] != own_sector for r in results)


@settings(max_examples=50, deadline=None)
@given(st.text(min_size=1, max_size=20))
def test_fuzz_semantic_neighbors_metric_validation(fcon, metric: str):
    """Invariant 3: only 'cosine' and 'ip' are accepted."""
    if metric in ("cosine", "ip"):
        results = semantic_neighbors(fcon, "CEAT", k=5, metric=metric)
        assert isinstance(results, list)
    else:
        with pytest.raises(ValueError):
            semantic_neighbors(fcon, "CEAT", k=5, metric=metric)
