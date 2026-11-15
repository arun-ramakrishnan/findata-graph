#!/usr/bin/env python3
"""S3 (note_search carve-out) — graph projections read the sidecar.

Covers the sidecar-first branches in helpers/graph/query.py:
- ``_materialise_note_embeddings`` reads ``v_note_embeddings`` from a
  colocated ``note_search.duckdb`` (legacy sqlite FTS5 fallback stays
  covered by tests/test_note_embeddings.py);
- ``_probe_note_embed_state`` (via ``_is_warm``) reads the info table;
- ``_compute_input_fingerprints`` stamps the note fingerprint from the
  sidecar;
- ``_mark_warm`` stamps note_embed_model from the sidecar info table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pytest

from helpers.core.vec_codec import pack_f32
from helpers.graph.query import (
    connect,
    similar_notes,
    _compute_input_fingerprints,
    _is_warm,
)
from tests.schema import ENTITY_TAGS

pytestmark = [pytest.mark.integration]

_SCHEMA = (
    """
CREATE TABLE entities (
    name TEXT PRIMARY KEY NOT NULL,
    entity_type TEXT NOT NULL,
    file_path TEXT,
    normalized_name TEXT,
    sector_classification TEXT,
    ticker TEXT
);
"""
    + ENTITY_TAGS
    + """
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
"""
)

_DIM = 4

_VEC_HDFC = [1.0, 0.0, 0.0, 0.0]
_VEC_ICICI = [0.9, 0.1, 0.0, 0.0]
_VEC_INFY = [0.0, 1.0, 0.0, 0.0]
_VEC_CHATTER = [0.95, 0.05, 0.0, 0.0]

_NOTES = [
    ("company", "findata/Companies/Banking/Hdfc_Bank.md", "HDFC Bank", _VEC_HDFC),
    ("company", "findata/Companies/Banking/ICICI_Bank.md", "ICICI Bank", _VEC_ICICI),
    ("company", "findata/Companies/Technology/Infosys.md", "Infosys", _VEC_INFY),
    (
        "chatter",
        "findata/The_Chatter/Bank_Chatter.md",
        "The Chatter: Banks",
        _VEC_CHATTER,
    ),
]


def _make_sidecar(tmp_path: Path, model="embeddinggemma-2-q8_512") -> Path:
    """Build the note-search sidecar colocated with the graph cache."""
    sidecar = tmp_path / "note_search.duckdb"
    con = duckdb.connect(str(sidecar))
    con.execute(
        """
        CREATE TABLE note_search (
            id BIGINT PRIMARY KEY,
            doc_type VARCHAR,
            file_path VARCHAR,
            title VARCHAR,
            sector VARCHAR,
            content VARCHAR,
            embedding BLOB,
            section_title VARCHAR,
            anchor VARCHAR,
            ticker VARCHAR,
            edge_context VARCHAR,
            searchtext VARCHAR
        )
        """
    )
    con.execute(
        "CREATE TABLE note_search_meta (file_path TEXT PRIMARY KEY, mtime REAL NOT NULL, content_hash TEXT NOT NULL)"
    )
    con.execute("CREATE TABLE note_search_info (key VARCHAR PRIMARY KEY, value VARCHAR)")
    for i, (dtype, fpath, title, vec) in enumerate(_NOTES):
        con.execute(
            "INSERT INTO note_search (id, doc_type, file_path, title, sector, "
            "content, embedding, section_title, anchor, ticker, edge_context, "
            "searchtext) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                i,
                dtype,
                fpath,
                title,
                "Finance",
                f"body of {title}",
                pack_f32(vec),
                title,
                "sec-1",
                "",
                "",
                f"{dtype} {title} Finance body of {title}",
            ),
        )
    con.execute("INSERT INTO note_search_info VALUES ('embed_model', ?)", (model,))
    con.execute("INSERT INTO note_search_info VALUES ('embed_dims', ?)", (str(_DIM),))
    con.execute("INSERT INTO note_search_info VALUES ('row_count', ?)", (str(len(_NOTES)),))
    con.close()
    return sidecar


def _make_db(tmp_path: Path) -> Path:
    """Entities + graph_edges sqlite source (NO FTS5 note_search — the
    sidecar owns the note index now)."""
    db_path = tmp_path / "notes.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name, "
        "sector_classification) VALUES (?,?,?,?,?)",
        [
            (
                "HDFC Bank",
                "company",
                "findata/Companies/Banking/Hdfc_Bank.md",
                "HDFC Bank",
                "Banking",
            ),
            (
                "ICICI Bank",
                "company",
                "findata/Companies/Banking/ICICI_Bank.md",
                "ICICI Bank",
                "Banking",
            ),
            (
                "Infosys",
                "company",
                "findata/Companies/Technology/Infosys.md",
                "Infosys",
                "Technology",
            ),
            ("Banking", "sector", "findata/Sectors/Banking.md", "Banking", None),
        ],
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,'seed')",
        [
            ("HDFC Bank", "Banking", "part_of"),
            ("ICICI Bank", "Banking", "part_of"),
            ("HDFC Bank", "ICICI Bank", "competes_with"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def sidecar_con(tmp_path):
    db_path = _make_db(tmp_path)
    _make_sidecar(tmp_path)
    con = connect(db_path=db_path, fresh=True)
    yield con, db_path
    con.close()


class TestSidecarMaterialisation:
    def test_projects_all_embedded_rows_from_sidecar(self, sidecar_con):
        con, _ = sidecar_con
        n, dim = con.execute("SELECT COUNT(*), first(len(emb)) FROM v_note_embeddings").fetchone()
        assert n == len(_NOTES)
        assert dim == _DIM

    def test_dims_stamped_in_build_meta(self, sidecar_con):
        con, _ = sidecar_con
        row = con.execute("SELECT value FROM _build_meta WHERE key='note_embed_dims'").fetchone()
        assert row is not None and row[0] == str(_DIM)

    def test_model_stamp_read_from_sidecar_info(self, sidecar_con):
        con, duckdb_path = sidecar_con
        row = con.execute("SELECT value FROM _build_meta WHERE key='note_embed_model'").fetchone()
        assert row is not None and row[0] == "embeddinggemma-2-q8_512"
        con.close()
        assert _is_warm(duckdb_path.with_suffix(".duckdb")) is True

    def test_sidecar_model_swap_forces_cold(self, tmp_path):
        db_path = _make_db(tmp_path)
        sidecar = _make_sidecar(tmp_path)
        duckdb_path = db_path.with_suffix(".duckdb")
        con = connect(db_path=db_path, fresh=True)
        con.close()
        assert _is_warm(duckdb_path) is True

        sc = duckdb.connect(str(sidecar))
        sc.execute("INSERT OR REPLACE INTO note_search_info VALUES ('embed_model', 'minilm-l6-v2')")
        sc.close()
        assert _is_warm(duckdb_path) is False

    def test_sidecar_dims_drift_forces_cold(self, tmp_path):
        db_path = _make_db(tmp_path)
        sidecar = _make_sidecar(tmp_path)
        duckdb_path = db_path.with_suffix(".duckdb")
        con = connect(db_path=db_path, fresh=True)
        con.close()
        assert _is_warm(duckdb_path) is True

        sc = duckdb.connect(str(sidecar))
        sc.execute("INSERT OR REPLACE INTO note_search_info VALUES ('embed_dims', '512')")
        sc.close()
        assert _is_warm(duckdb_path) is False

    def test_fingerprint_reads_sidecar(self, tmp_path):
        db_path = _make_db(tmp_path)
        _make_sidecar(tmp_path)
        con = connect(db_path=db_path, fresh=True)
        try:
            fps = _compute_input_fingerprints(con)
        finally:
            con.close()
        n, dims, model = fps["note_embeddings"].split(":")
        assert int(n) == len(_NOTES)
        assert dims == str(_DIM)
        assert model == "embeddinggemma-2-q8_512"


class TestSimilarNotesSidecar:
    def test_self_excluded_and_ranked(self, sidecar_con):
        con, _ = sidecar_con
        res = similar_notes(con, "findata/Companies/Banking/Hdfc_Bank.md")
        assert res is not None
        paths = [p for p, _t, s in res if s > 1e-6]
        assert "findata/Companies/Banking/Hdfc_Bank.md" not in paths
        assert set(paths) == {
            "findata/Companies/Banking/ICICI_Bank.md",
            "findata/The_Chatter/Bank_Chatter.md",
        }
