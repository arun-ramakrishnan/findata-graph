"""swap_convert S1 tests — translator units + a tiny end-to-end conversion.

Full-store parity lives in the S1 artifact report
(bench_data/duckdb_swap/stores/s1/research_converted.report.json); these
tests pin the pure logic the converter relies on, plus one miniature
round-trip exercising the self-FK loop, view/index translation and the
parity battery itself.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from helpers.misc import swap_convert as sc


class TestDuckType:
    def test_int_family(self):
        assert sc._duck_type("INTEGER") == "BIGINT"
        assert sc._duck_type("SMALLINT") == "BIGINT"  # the cin_year bug
        assert sc._duck_type("BIGINT") == "BIGINT"

    def test_datetime_not_int(self):
        assert sc._duck_type("DATETIME") == "TIMESTAMP"  # 'INT' substring trap
        assert sc._duck_type("TIMESTAMP") == "TIMESTAMP"

    def test_others(self):
        assert sc._duck_type("TEXT") == "VARCHAR"
        assert sc._duck_type("CHAR(21)") == "VARCHAR"
        assert sc._duck_type("REAL") == "DOUBLE"
        assert sc._duck_type("BLOB") == "BLOB"
        assert sc._duck_type("DATE") == "DATE"
        assert sc._duck_type("") == "VARCHAR"
        assert sc._duck_type(None) == "VARCHAR"


class TestTranslateIndex:
    def test_plain(self):
        sql = "CREATE INDEX ge_target_idx ON graph_edges(target)"
        assert sc._translate_index("graph_edges", sql) == (
            'CREATE INDEX "ge_target_idx" ON "graph_edges" (target)'
        )

    def test_nocase_becomes_lower_expr(self):
        sql = "CREATE INDEX idx_entities_name_nocase ON entities(name COLLATE NOCASE)"
        assert sc._translate_index("entities", sql) == (
            'CREATE INDEX "idx_entities_name_nocase" ON "entities" (lower(name))'
        )

    def test_desc_preserved_and_only_nocase_lowered(self):
        sql = "CREATE INDEX idx_ppr_results_seed_rank ON ppr_results(seed, score DESC)"
        assert sc._translate_index("ppr_results", sql) == (
            'CREATE INDEX "idx_ppr_results_seed_rank" ON "ppr_results" (seed, score DESC)'
        )


class TestTranslateView:
    def test_glob_becomes_regexp(self):
        sql = (
            "CREATE VIEW v AS SELECT * FROM company_metrics "
            "WHERE source_ref GLOB '*_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]"
            "[0-9][0-9][0-9][0-9][0-9][0-9]_WEB.xml'"
        )
        out = sc._translate_view(sql)
        assert "CREATE OR REPLACE VIEW v" in out
        assert "GLOB" not in out
        assert "regexp_matches(source_ref, '_[0-9]{14}_WEB\\.xml$')" in out

    def test_plain_view_passthrough(self):
        sql = "CREATE VIEW relations AS SELECT source, target FROM graph_edges"
        out = sc._translate_view(sql)
        assert "CREATE OR REPLACE VIEW relations" in out
        assert "regexp" not in out


class TestLoadOrder:
    def test_parents_before_children(self):
        tables = {
            "events": {"fks": [{"parent": "entities", "from": ["entity"], "to": ["name"]}]},
            "entities": {"fks": []},
        }
        order = sc._load_order(tables)
        assert order.index("entities") < order.index("events")

    def test_self_fk_does_not_cycle(self):
        tables = {
            "concepts": {
                "fks": [
                    {"parent": "concepts", "from": ["broader_id"], "to": ["concept_id"]},
                    {"parent": "concept_schemes", "from": ["scheme_id"], "to": ["scheme_id"]},
                ]
            },
            "concept_schemes": {"fks": []},
        }
        order = sc._load_order(tables)
        assert order.index("concept_schemes") < order.index("concepts")


def _mini_store(path: Path) -> None:
    """Tiny store with the S1 shapes: self-FK hierarchy, NOCASE index,
    GLOB-free view, a DATETIME default, FTS-named junk that must drop."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO db_meta VALUES ('generation','3')")
    con.execute(
        "CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, broader_id TEXT, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY (broader_id) REFERENCES concepts (concept_id))"
    )
    con.execute("INSERT INTO concepts (concept_id, broader_id) VALUES ('a', NULL), ('b', 'a'), ('c', 'b')")
    con.execute("CREATE INDEX idx_nocase ON concepts(concept_id COLLATE NOCASE)")
    con.execute("CREATE VIEW v AS SELECT concept_id FROM concepts")
    con.execute("CREATE VIRTUAL TABLE note_search USING fts5(a)")  # must be dropped
    con.commit()
    con.close()


def test_end_to_end_mini_store(tmp_path: Path):
    src = tmp_path / "mini.db"
    _mini_store(src)
    dest = tmp_path / "mini.duckdb"
    rc = sc.main(["--source", str(src), "--dest", str(dest)])
    assert rc == 0
    report = json.loads(dest.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["parity_ok"] is True
    assert report["carried_tables"] == 2  # db_meta + concepts; note_search dropped
    assert report["dropped_fts"] == [
        "note_search",
        "note_search_config",
        "note_search_content",
        "note_search_data",
        "note_search_docsize",
        "note_search_idx",
    ]  # sqlite3 auto-creates all five shadow tables for the fts5 fixture
    assert report["indexes"]["created"] == 1
    assert report["views"]["v"]["result_set_parity"] is True
    assert report["tables"]["concepts"]["type_parity"] is True

    import duckdb

    con = duckdb.connect(str(dest), read_only=True)
    try:
        # self-FK chain fully loaded via the iterative loop, order preserved
        assert con.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 3
        assert con.execute("SELECT broader_id FROM concepts WHERE concept_id='c'").fetchone()[0] == "b"
        # NOCASE index landed as an expression index
        idx_sql = con.execute(
            "SELECT sql FROM duckdb_indexes() WHERE index_name='idx_nocase'"
        ).fetchone()[0]
        assert "lower" in idx_sql
    finally:
        con.close()


def test_check_mode_matches_build(tmp_path: Path):
    src = tmp_path / "mini.db"
    _mini_store(src)
    dest = tmp_path / "mini.duckdb"
    assert sc.main(["--source", str(src), "--dest", str(dest)]) == 0
    assert sc.main(["--source", str(src), "--check", str(dest)]) == 0


def test_dangling_self_fk_fails_loudly(tmp_path: Path):
    """SQLite does not enforce FKs by default — a dangling ref must fail the
    build, never silently drop rows."""
    src = tmp_path / "dangling.db"
    con = sqlite3.connect(src)
    con.execute("CREATE TABLE db_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO db_meta VALUES ('generation','0')")
    con.execute(
        "CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, broader_id TEXT, "
        "FOREIGN KEY (broader_id) REFERENCES concepts (concept_id))"
    )
    con.execute("INSERT INTO concepts VALUES ('a', NULL), ('b', 'missing')")  # dangling
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="dangling"):
        sc.main(["--source", str(src), "--dest", str(tmp_path / "d.duckdb")])
