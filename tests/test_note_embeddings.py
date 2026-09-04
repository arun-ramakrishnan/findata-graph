#!/usr/bin/env python3
"""sql_capability_unlocks A1/A2 — v_note_embeddings materialisation + wrappers.

End-to-end over a temp SQLite (entities + FTS5 note_search with JSON
embedding columns) → connect(fresh=True) → DuckDB materialisation → the
four wrappers. Plus the warm-path drift checks (_is_warm model/dims
stamps) that force cold on a model swap.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "helpers"))

from helpers.maintenance.rebuild_note_search import NOTE_SEARCH_DDL  # noqa: E402
from helpers.graph.query import (  # noqa: E402
    connect,
    edition_companies,
    near_duplicate_notes,
    notes_like_entity,
    notes_like_text,
    similar_notes,
    _is_warm,
)
from tests.schema import ENTITY_TAGS  # noqa: E402

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

# Vector geometry: HDFC ≈ ICICI ≈ chatter-note (near-parallel, +x); Infosys
# is orthogonal (+y → cosine 0, filtered by the sim > 0 guard); the _Old
# note is an EXACT duplicate of HDFC's vector (the near-dup tripwire).
_VEC_HDFC = [1.0, 0.0, 0.0, 0.0]
_VEC_ICICI = [0.9, 0.1, 0.0, 0.0]
_VEC_INFY = [0.0, 1.0, 0.0, 0.0]
_VEC_CHATTER = [0.95, 0.05, 0.0, 0.0]

_NOTES = [
    # (doc_type, file_path, title, vector)
    ("company", "findata/Companies/Banking/Hdfc_Bank.md", "HDFC Bank", _VEC_HDFC),
    ("company", "findata/Companies/Banking/ICICI_Bank.md", "ICICI Bank", _VEC_ICICI),
    ("company", "findata/Companies/Technology/Infosys.md", "Infosys", _VEC_INFY),
    ("company", "findata/Companies/Banking/Hdfc_Bank_Old.md", "HDFC Bank Old", _VEC_HDFC),
    ("chatter", "findata/The_Chatter/Bank_Chatter.md", "The Chatter: Banks", _VEC_CHATTER),
]


def _make_db(tmp_path, dims=_DIM, with_model_stamp=None):
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
    conn.execute(NOTE_SEARCH_DDL)
    for dtype, fpath, title, vec in _NOTES:
        stored = vec if dims == _DIM else vec + [0.0] * (dims - _DIM)
        conn.execute(
            "INSERT INTO note_search (doc_type, file_path, title, sector, "
            "content, embedding) VALUES (?,?,?,?,?,?)",
            (dtype, fpath, title, "", f"body of {title}", json.dumps(stored)),
        )
    if with_model_stamp is not None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS db_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('note_embed_model', ?)",
            (with_model_stamp,),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def note_con(tmp_path):
    db_path = _make_db(tmp_path)
    con = connect(db_path=db_path, fresh=True)
    yield con, db_path
    con.close()


class TestMaterialisation:
    def test_projects_all_embedded_rows(self, note_con):
        con, _ = note_con
        n, dim = con.execute("SELECT COUNT(*), first(len(emb)) FROM v_note_embeddings").fetchone()
        assert n == len(_NOTES)
        assert dim == _DIM

    def test_dims_stamped_in_build_meta(self, note_con):
        con, _ = note_con
        row = con.execute("SELECT value FROM _build_meta WHERE key='note_embed_dims'").fetchone()
        assert row is not None and row[0] == str(_DIM)

    def test_model_stamp_round_trips(self, tmp_path):
        db_path = _make_db(tmp_path, with_model_stamp="bge-small-en-v1.5")
        duckdb_path = db_path.with_suffix(".duckdb")
        con = connect(db_path=db_path, fresh=True)
        con.close()
        # _build_meta lives in the duckdb file; read it back read-only.
        import duckdb as _ddb

        ro = _ddb.connect(str(duckdb_path), read_only=True)
        try:
            row = ro.execute(
                "SELECT value FROM _build_meta WHERE key='note_embed_model'"
            ).fetchone()
        finally:
            ro.close()
        assert row is not None and row[0] == "bge-small-en-v1.5"
        assert _is_warm(duckdb_path) is True

    def test_model_swap_forces_cold(self, tmp_path):
        """A same-dims model swap (the case the dims probe alone cannot
        see) must flip _is_warm — never serve cross-model cosines."""
        db_path = _make_db(tmp_path, with_model_stamp="bge-small-en-v1.5")
        duckdb_path = db_path.with_suffix(".duckdb")
        con = connect(db_path=db_path, fresh=True)
        con.close()
        assert _is_warm(duckdb_path) is True

        sc = sqlite3.connect(str(db_path))
        sc.execute(
            "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('note_embed_model', 'minilm-l6-v2')"
        )
        sc.commit()
        sc.close()
        assert _is_warm(duckdb_path) is False

    def test_dims_drift_forces_cold(self, tmp_path):
        """Rewriting note_search at a different vector size (no generation
        bump — FTS5 has no triggers) must still flip _is_warm via the
        note_embed_dims stamp."""
        db_path = _make_db(tmp_path)
        duckdb_path = db_path.with_suffix(".duckdb")
        con = connect(db_path=db_path, fresh=True)
        con.close()
        assert _is_warm(duckdb_path) is True

        sc = sqlite3.connect(str(db_path))
        sc.execute("UPDATE note_search SET embedding = ?", (json.dumps(_VEC_HDFC + [0.0] * 4),))
        sc.commit()
        sc.close()
        assert _is_warm(duckdb_path) is False


class TestSimilarNotes:
    def test_self_excluded_and_ranked(self, note_con):
        con, _ = note_con
        res = similar_notes(con, "findata/Companies/Banking/Hdfc_Bank.md")
        assert res is not None
        paths = [p for p, _t, _s in res]
        assert "findata/Companies/Banking/Hdfc_Bank.md" not in paths  # self-exclusion
        # Exact duplicate first, then the near-parallel rows; orthogonal
        # Infosys is filtered by sim > 0.
        assert paths[0] == "findata/Companies/Banking/Hdfc_Bank_Old.md"
        assert set(paths) == {
            "findata/Companies/Banking/Hdfc_Bank_Old.md",
            "findata/Companies/Banking/ICICI_Bank.md",
            "findata/The_Chatter/Bank_Chatter.md",
        }

    def test_doc_type_filter(self, note_con):
        con, _ = note_con
        res = similar_notes(con, "findata/Companies/Banking/Hdfc_Bank.md", doc_type="company")
        assert [p for p, _t, _s in res] == [
            "findata/Companies/Banking/Hdfc_Bank_Old.md",
            "findata/Companies/Banking/ICICI_Bank.md",
        ]

    def test_k_limit(self, note_con):
        con, _ = note_con
        res = similar_notes(con, "findata/Companies/Banking/Hdfc_Bank.md", k=1)
        assert len(res) == 1

    def test_unknown_note_returns_none(self, note_con):
        con, _ = note_con
        assert similar_notes(con, "findata/Companies/Nope.md") is None


class TestNotesLikeEntity:
    def test_newsletters_ranked(self, note_con):
        con, _ = note_con
        res = notes_like_entity(con, "HDFC Bank")
        assert res is not None
        assert [p for p, _t, _s in res] == ["findata/The_Chatter/Bank_Chatter.md"]

    def test_unknown_entity_returns_none(self, note_con):
        con, _ = note_con
        assert notes_like_entity(con, "No Such Co") is None


class TestNotesLikeText:
    def test_text_matches_nearest_company(self, note_con):
        con, _ = note_con
        res = notes_like_text(con, "HDFC Bank", embed_fn=lambda _t: _VEC_HDFC)
        assert res is not None
        # External text has no self-exclusion: both HDFC rows rank first
        # (exact-duplicate vectors tie), Infosys is orthogonal → filtered.
        assert res[0][0] == "findata/Companies/Banking/Hdfc_Bank.md"
        assert all("Infosys" not in p for p, _t, _s in res)

    def test_doc_type_filter(self, note_con):
        con, _ = note_con
        res = notes_like_text(
            con, "bank chatter", doc_type="chatter", embed_fn=lambda _t: _VEC_CHATTER
        )
        assert res is not None
        assert [p for p, _t, _s in res] == ["findata/The_Chatter/Bank_Chatter.md"]

    def test_min_sim_and_k(self, note_con):
        con, _ = note_con
        # Anti-parallel text vector → all cosines ≤ 0 → filtered by the
        # sim > 0 guard (an exact-duplicate fixture vector would survive
        # any min_sim < 1.0, so this is the deterministic empty case).
        assert notes_like_text(con, "x", embed_fn=lambda _t: [-1.0, 0.0, 0.0, 0.0]) == []
        res = notes_like_text(con, "x", k=1, embed_fn=lambda _t: _VEC_HDFC)
        assert res is not None and len(res) == 1

    def test_dims_mismatch_returns_none(self, note_con):
        con, _ = note_con
        assert notes_like_text(con, "x", embed_fn=lambda _t: [1.0, 0.0]) is None

    def test_no_embedder_returns_none(self, note_con):
        # conftest's autouse _no_local_embedder pin makes the default
        # path take the unavailable branch — the parse --cross-check
        # "warn and skip" contract depends on this None.
        con, _ = note_con
        assert notes_like_text(con, "HDFC Bank") is None


class TestEditionCompanies:
    def test_resolves_by_stem(self, note_con):
        con, _ = note_con
        res = edition_companies(con, "Bank_Chatter")
        assert res is not None
        paths = [p for p, _t, _s in res]
        # All four companies have sim > 0 vs the chatter note (Infosys is
        # NEARLY orthogonal at ~0.05, not exactly 0). The two identical
        # HDFC vectors tie for first — assert them as a set.
        assert set(paths[:2]) == {
            "findata/Companies/Banking/Hdfc_Bank.md",
            "findata/Companies/Banking/Hdfc_Bank_Old.md",
        }
        assert paths[2:] == [
            "findata/Companies/Banking/ICICI_Bank.md",
            "findata/Companies/Technology/Infosys.md",
        ]
        # Monotone similarity ordering.
        sims = [s for _p, _t, s in res]
        assert sims == sorted(sims, reverse=True)

    def test_resolves_by_title(self, note_con):
        con, _ = note_con
        res = edition_companies(con, "The Chatter: Banks")
        assert res is not None and len(res) == 4

    def test_unresolvable_returns_none(self, note_con):
        con, _ = note_con
        assert edition_companies(con, "No_Such_Edition") is None


class TestNearDuplicateNotes:
    def test_exact_duplicate_top_pair(self, note_con):
        con, _ = note_con
        pairs = near_duplicate_notes(con, min_sim=0.5)
        assert pairs, "expected at least the injected duplicate pair"
        pa, pb, ta, tb, sim = pairs[0]
        assert sim == pytest.approx(1.0)
        assert {pa, pb} == {
            "findata/Companies/Banking/Hdfc_Bank.md",
            "findata/Companies/Banking/Hdfc_Bank_Old.md",
        }

    def test_threshold_and_doc_type(self, note_con):
        con, _ = note_con
        # Above 0.999: only the exact duplicate (HDFC-ICICI sits at ~0.994).
        strict = near_duplicate_notes(con, min_sim=0.999)
        assert len(strict) == 1
        # The chatter note is excluded from a company-only self-join even
        # at a loose threshold.
        loose = near_duplicate_notes(con, min_sim=0.5)
        assert all(
            "The_Chatter" not in pa and "The_Chatter" not in pb for pa, pb, _ta, _tb, _s in loose
        )
