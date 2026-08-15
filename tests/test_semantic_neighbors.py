"""Integration tests for the VSS semantic-neighbors feature (Bundle N5).

Covers the full pipeline against a SYNTHETIC SQLite DB (no production data):

  entities + entity_tags + graph_edges + company_embeddings (populated with
  deterministic dry-run pseudo-embeddings) -> DuckDB connect() materialises
  v_node + v_embeddings -> semantic_neighbors() queries it.

Marked ``live`` (like tests/test_graph_disk.py)
because building the DuckDB materialisation needs the duckdb + vss
extensions installed (network install on first run). The tests themselves do
NOT touch memory/research.db or the live findata vault.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

duckdb = pytest.importorskip("duckdb")

from helpers.graph.query import (  # noqa: E402
    _lit,
    clear_graph_cache,
    connect,
    semantic_neighbors,
)

# --------------------------------------------------------------------------- #
# Synthetic SQLite fixture                                                    #
# --------------------------------------------------------------------------- #
_SYNTH_DDL = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    sector_classification TEXT,
    ticker TEXT,
    file_path TEXT,
    normalized_name TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_updated DATETIME
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag)
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
"""

# (name, entity_type, sector_classification, ticker, file_path, normalized_name)
_SYNTH_ENTITIES = [
    ("CEAT", "company", "Automotive", "CEAT.NS",
     "findata/Companies/Automotive/CEAT.md", "CEAT"),
    ("Apollo Tyres", "company", "Automotive", "APOLLOTYRE.NS",
     "findata/Companies/Automotive/Apollo_Tyres.md", "Apollo_Tyres"),
    ("MRF", "company", "Automotive", "MRF.NS",
     "findata/Companies/Automotive/MRF.md", "MRF"),
    ("Infosys", "company", "Technology", "INFY.NS",
     "findata/Companies/Technology/Infosys.md", "Infosys"),
    ("TCS", "company", "Technology", "TCS.NS",
     "findata/Companies/Technology/Tata_Consultancy_Services.md",
     "Tata_Consultancy_Services"),
    ("Automotive", "sector", None, None,
     "findata/Sectors/Automotive.md", "Automotive"),
    ("Technology", "sector", None, None,
     "findata/Sectors/Technology.md", "Technology"),
]

_SYNTH_TAGS = [
    ("CEAT", "market_cap/mid_cap"),
    ("Apollo Tyres", "market_cap/large_cap"),
    ("MRF", "market_cap/large_cap"),
    ("Infosys", "market_cap/large_cap"),
    ("TCS", "market_cap/large_cap"),
]

_SYNTH_EDGES = [
    ("CEAT", "Automotive", "part_of", "seed"),
    ("Automotive", "CEAT", "has_company", "seed"),
    ("Apollo Tyres", "Automotive", "part_of", "seed"),
    ("MRF", "Automotive", "part_of", "seed"),
    ("Infosys", "Technology", "part_of", "seed"),
    ("TCS", "Technology", "part_of", "seed"),
]

COMPANIES = ["CEAT", "Apollo Tyres", "MRF", "Infosys", "TCS"]
SECTOR_OF = {"CEAT": "Automotive", "Apollo Tyres": "Automotive",
             "MRF": "Automotive", "Infosys": "Technology", "TCS": "Technology"}


def _build_synthetic_db(db_path: Path, dims: int = 8,
                        with_embeddings: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SYNTH_DDL)
    conn.executemany(
        "INSERT INTO entities (name, entity_type, sector_classification, "
        "ticker, file_path, normalized_name) VALUES (?,?,?,?,?,?)",
        _SYNTH_ENTITIES,
    )
    conn.executemany(
        "INSERT INTO entity_tags (entity_name, tag) VALUES (?,?)", _SYNTH_TAGS
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) "
        "VALUES (?,?,?,?)",
        _SYNTH_EDGES,
    )
    if with_embeddings:
        from helpers.graph.embeddings import populate_dry_run

        populate_dry_run(conn, dims=dims)
    conn.commit()
    return conn


@pytest.fixture
def synth_db(tmp_path) -> Path:
    """Synthetic SQLite DB path with entities + embeddings (dims=8)."""
    db_path = tmp_path / "synth.db"
    conn = _build_synthetic_db(db_path, dims=8)
    conn.close()
    return db_path


@pytest.fixture
def gcon(synth_db):
    """Warm DuckDB connection over the synthetic DB.

    Clears the in-process query cache on teardown so a later test using a
    different DB with the same args isn't served stale cached results
    (the cache key includes args but not the connection/db identity).
    """
    clear_graph_cache()
    con = connect(db_path=synth_db)
    yield con
    con.close()
    clear_graph_cache()


# --------------------------------------------------------------------------- #
# Pipeline / materialisation                                                   #
# --------------------------------------------------------------------------- #
class TestPipeline:
    def test_v_embeddings_materialised(self, gcon):
        n = gcon.execute("SELECT COUNT(*) FROM v_embeddings").fetchone()[0]
        assert n == len(COMPANIES)  # 5 companies, sectors have no embeddings

    def test_v_node_has_companies_and_sectors(self, gcon):
        kinds = {
            r[0]: r[1]
            for r in gcon.execute("SELECT kind, COUNT(*) FROM v_node GROUP BY 1").fetchall()
        }
        assert kinds.get("company") == 5
        assert kinds.get("sector") == 2

    def test_empty_embeddings_db_returns_empty(self, tmp_path):
        """A DB with no company_embeddings table must not crash the build;
        v_embeddings is created empty and semantic_neighbors returns []."""
        db_path = tmp_path / "no_emb.db"
        conn = _build_synthetic_db(db_path, with_embeddings=False)
        conn.close()
        clear_graph_cache()
        con = connect(db_path=db_path)
        try:
            _row = con.execute("SELECT COUNT(*) FROM v_embeddings").fetchone()
            assert _row is not None
            assert _row[0] == 0
            assert semantic_neighbors(con, "CEAT", k=5) == []
        finally:
            con.close()
            clear_graph_cache()


# --------------------------------------------------------------------------- #
# semantic_neighbors behaviour                                                 #
# --------------------------------------------------------------------------- #
class TestSemanticNeighbors:
    def test_returns_other_companies_only(self, gcon):
        results = semantic_neighbors(gcon, "CEAT", k=10)
        names = {r[0] for r in results}
        assert "CEAT" not in names  # self excluded
        assert names <= set(COMPANIES)

    def test_every_result_is_typed_tuple(self, gcon):
        for r in semantic_neighbors(gcon, "CEAT", k=10):
            assert isinstance(r, tuple) and len(r) == 3
            name, sector, score = r
            assert isinstance(name, str)
            assert sector is None or isinstance(sector, str)
            assert isinstance(score, float)

    def test_sorted_by_similarity_desc(self, gcon):
        results = semantic_neighbors(gcon, "CEAT", k=10)
        scores = [r[2] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_k_limits_results(self, gcon):
        for k in (0, 1, 2, 3, 10):
            results = semantic_neighbors(gcon, "CEAT", k=k)
            assert len(results) <= k

    def test_k_zero_returns_empty(self, gcon):
        assert semantic_neighbors(gcon, "CEAT", k=0) == []

    def test_k_negative_clamped_to_zero(self, gcon):
        # Regression: k=-1 used to raise BinderException
        # ("LIMIT/OFFSET cannot be negative") once embeddings existed.
        assert semantic_neighbors(gcon, "CEAT", k=-1) == []

    def test_k_non_int_coerced(self, gcon):
        results = semantic_neighbors(gcon, "CEAT", k=2.7)
        assert len(results) <= 2

    def test_deterministic(self, gcon):
        a = semantic_neighbors(gcon, "CEAT", k=10)
        b = semantic_neighbors(gcon, "CEAT", k=10)
        assert a == b

    def test_cross_sector_excludes_same_sector(self, gcon):
        results = semantic_neighbors(gcon, "CEAT", k=10, cross_sector=True)
        assert all(SECTOR_OF[r[0]] != "Automotive" for r in results)
        assert all(r[0] != "CEAT" for r in results)

    def test_cross_sector_still_returns_same_sector_for_others(self, gcon):
        results = semantic_neighbors(gcon, "Infosys", k=10, cross_sector=False)
        # Without the filter, Infosys' same-sector peer TCS may appear;
        # with it, it must not. This pins the flag actually doing work.
        restricted = semantic_neighbors(gcon, "Infosys", k=10, cross_sector=True)
        assert all(SECTOR_OF[r[0]] != "Technology" for r in restricted)
        unrestricted_sectors = {SECTOR_OF[r[0]] for r in results}
        assert unrestricted_sectors <= {"Automotive", "Technology"}

    def test_ip_metric_returns_negative_scores(self, gcon):
        results = semantic_neighbors(gcon, "CEAT", k=10, metric="ip")
        assert all(r[2] < 0 for r in results)

    def test_bogus_metric_raises_value_error(self, gcon):
        with pytest.raises(ValueError):
            semantic_neighbors(gcon, "CEAT", metric="bogus")

    def test_unknown_company_returns_empty(self, gcon):
        assert semantic_neighbors(gcon, "Does Not Exist", k=5) == []

    def test_single_quote_company_name_safe(self, gcon):
        # _lit escaping must prevent both crashes and SQL injection.
        assert semantic_neighbors(gcon, "O'Reilly", k=5) == []
        assert semantic_neighbors(gcon, "x' OR '1'='1", k=5) == []

    def test_empty_string_company_safe(self, gcon):
        assert semantic_neighbors(gcon, "", k=5) == []


# --------------------------------------------------------------------------- #
# Staleness contract (mirrors test_graph_disk.py::test_rebuild_picks_up...)   #
# --------------------------------------------------------------------------- #
class TestStalenessContract:
    def test_embeddings_visible_after_rebuild(self, tmp_path):
        db_path = tmp_path / "stale.db"
        conn = _build_synthetic_db(db_path, with_embeddings=False)
        conn.close()

        clear_graph_cache()
        con = connect(db_path=db_path)
        assert semantic_neighbors(con, "CEAT", k=5) == []  # no embeddings yet
        con.close()

        # Populate embeddings on the SQLite side.
        conn = sqlite3.connect(str(db_path))
        from helpers.graph.embeddings import populate_dry_run

        populate_dry_run(conn, dims=8)
        conn.close()

        # Warm connect stays stale (by design — no auto-detection).
        con = connect(db_path=db_path)
        assert semantic_neighbors(con, "CEAT", k=5) == []
        con.close()

        # rebuild=True picks the embeddings up.
        con = connect(db_path=db_path, rebuild=True)
        try:
            assert len(semantic_neighbors(con, "CEAT", k=5)) > 0
        finally:
            con.close()
            clear_graph_cache()


# --------------------------------------------------------------------------- #
# _lit (unit) — the SQL-literal escaping semantic_neighbors relies on         #
# --------------------------------------------------------------------------- #
class TestLitEscaping:
    def test_plain(self):
        assert _lit("CEAT") == "'CEAT'"

    def test_single_quote_doubled(self):
        assert _lit("O'Reilly") == "'O''Reilly'"

    def test_sql_injection_pattern_neutralised(self):
        assert _lit("x' OR '1'='1") == "'x'' OR ''1''=''1'"

    def test_nul_byte_stripped(self):
        # Regression: NUL inside a literal made DuckDB raise ParserException
        # ("unterminated quoted string") — fuzz-discovered 2026-08-09.
        assert _lit("CEAT\x00") == "'CEAT'"
        assert "\x00" not in _lit("a\x00b")

    def test_control_chars_stripped(self):
        for ch in ("\x00", "\n", "\t", "\r", "\x1b", "\x7f"):
            out = _lit("A" + ch + "B")
            assert out == "'AB'", f"{ch!r} not stripped: {out!r}"

    def test_nul_company_query_safe(self, gcon):
        # Full path: semantic_neighbors with a NUL-bearing name must not
        # crash. The NUL is stripped by _lit, so the query degrades to
        # the sanitised name ("CEAT") and returns a well-typed result list.
        results = semantic_neighbors(gcon, "CEAT\x00", k=5)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, tuple) and len(r) == 3
