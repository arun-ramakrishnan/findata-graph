"""Tests for Bundle P: one-shot schema rebuild (rebuild_schema.py).

Pins that the rebuild brings the live SQLite DB into conformance with the
canonical DDL constants — landing the json_valid CHECK (P1), the canonical
entities layout + complete name-suffix CHECK (P2), and the reversed
graph_analytics PK (P3) — while preserving every row.

These tests run against a per-test CLONE of a schema-only template seeded
with minimal FK-consistent test data (tests._tmp_hygiene.schema_template).
The template IS a backup of the same live DB schema (same DDL, zero rows),
so only the data changes. Per-test files stay: these tests mutate. They're
NOT marked live because they don't hit memory/research.db directly —
they clone the template, seed data, and rebuild the clone.
"""

import sqlite3
from pathlib import Path

import pytest

from helpers.maintenance.rebuild_schema import rebuild
import helpers.maintenance.rebuild_schema as rs  # noqa: E402
from tests._tmp_hygiene import schema_template, reflink_or_copy  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, tmp_path_factory) -> Path:
    """Per-test isolated DB: fresh clone of the tiny schema template.

    The schema-only template is ~2MB, so a reflink/copy per test is ~ms
    (vs 404MB production clone previously, or SAVEPOINT games that broke
    isolation). Data is seeded here once; tests just use tmp_db.
    """
    out = tmp_path / "test.db"
    reflink_or_copy(schema_template(tmp_path_factory), out)
    con = sqlite3.connect(str(out))
    con.execute("PRAGMA journal_mode=WAL")
    con.close()
    _seed_test_data(out)
    return out


def _seed_test_data(db_path: Path) -> dict:
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    try:
        entities = [
            ("CEAT", "company"),
            ("MRF", "company"),
            ("JK_Tyre", "company"),
            ("Auto_Sector", "sector"),
            ("Tyres", "sub_sector"),
        ]
        for name, etype in entities:
            con.execute("INSERT INTO entities (name, entity_type) VALUES (?, ?)", (name, etype))

        edges = [
            (1, "CEAT", "MRF", "competes_with", 1.0, "{}", None, None, "test", 0),
            (2, "CEAT", "JK_Tyre", "competes_with", 1.0, "{}", None, None, "test", 0),
            (3, "MRF", "Auto_Sector", "belongs_to", 1.0, "{}", None, None, "test", 0),
            (4, "JK_Tyre", "Auto_Sector", "belongs_to", 1.0, "{}", None, None, "test", 0),
            (5, "Auto_Sector", "Tyres", "has_sub_sector", 1.0, "{}", None, None, "test", 0),
        ]
        for e in edges:
            con.execute(
                "INSERT INTO graph_edges (id, source, target, edge_type, weight, properties, valid_from, valid_to, source_ref, symmetric) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                e,
            )

        analytics = [
            ("pagerank", "CEAT", "0.15"),
            ("pagerank", "MRF", "0.12"),
            ("pagerank", "JK_Tyre", "0.10"),
            ("pagerank", "Auto_Sector", "0.08"),
            ("pagerank", "Tyres", "0.05"),
            ("betweenness", "CEAT", "0.25"),
            ("betweenness", "MRF", "0.20"),
            ("betweenness", "JK_Tyre", "0.18"),
            ("betweenness", "Auto_Sector", "0.15"),
            ("betweenness", "Tyres", "0.10"),
        ]
        for metric, entity, value in analytics:
            con.execute(
                "INSERT INTO graph_analytics (metric, entity_name, value) VALUES (?, ?, ?)",
                (metric, entity, value),
            )

        con.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES ('generation', '0')")
        con.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES ('schema_version', '16')")

        con.commit()
        pre_entities = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        pre_edges = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        pre_analytics = con.execute("SELECT COUNT(*) FROM graph_analytics").fetchone()[0]

        return {
            "pre_entities": pre_entities,
            "pre_edges": pre_edges,
            "pre_analytics": pre_analytics,
        }
    finally:
        con.close()


def _ddl_for(con, table):
    return con.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]


class TestRebuildDataPreservation:
    def test_rebuild_preserves_row_counts(self, tmp_db):
        s = rebuild(tmp_db, dry_run=False)
        assert s["pre_entities"] == s["post_entities"]
        assert s["pre_edges"] == s["post_edges"]
        assert s["pre_analytics"] == s["post_analytics"]
        assert s["pre_entities"] > 0 and s["pre_edges"] > 0 and s["pre_analytics"] > 0

    def test_rebuild_preserves_relations_view(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            view_rows = con.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
            edge_rows = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
            assert view_rows == edge_rows
            assert edge_rows > 0
        finally:
            con.close()

    def test_rebuild_preserves_fk_integrity(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            violations = con.execute("PRAGMA foreign_key_check").fetchall()
            assert violations == [], f"FK violations post-rebuild: {violations}"
        finally:
            con.close()


class TestRebuildP1JsonValidCheck:
    def test_json_valid_check_in_live_ddl(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            ddl = _ddl_for(con, "graph_edges")
            assert "json_valid(properties)" in ddl, (
                "json_valid CHECK missing from rebuilt graph_edges DDL"
            )
        finally:
            con.close()

    def test_malformed_json_rejected_post_rebuild(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO graph_edges (source, target, edge_type, source_ref, properties) VALUES (?, ?, ?, ?, ?)",
                    ("CEAT", "MRF", "test_probe", "t", "NOT_JSON{"),
                )
        finally:
            con.close()


class TestRebuildP2EntitiesCheck:
    def test_no_dot_suffix_rejected_post_rebuild(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO entities (name, entity_type) VALUES (?, ?)", ("Foo Ltd", "company")
                )
        finally:
            con.close()

    def test_canonical_suffixes_all_rejected(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        con.execute("PRAGMA foreign_keys = ON")
        try:
            for bad in ("Foo Ltd", "Foo Ltd.", "Foo Limited", "Foo Pvt", "Foo Private"):
                with pytest.raises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO entities (name, entity_type) VALUES (?, ?)", (bad, "company")
                    )
        finally:
            con.close()

    def test_suffix_guard_is_company_scoped(self, tmp_path):
        from helpers.maintenance.migrate_to_graph_edges import ENTITIES_DDL

        db = tmp_path / "fresh.db"
        con = sqlite3.connect(str(db))
        con.executescript(ENTITIES_DDL)
        con.execute("PRAGMA foreign_keys = ON")
        try:
            for name, etype in [
                ("Private_Sector", "sub_sector"),
                ("Acme Private Banking", "sector"),
                ("Something Private", "super_sector"),
            ]:
                con.execute("INSERT INTO entities (name, entity_type) VALUES (?, ?)", (name, etype))
            con.commit()
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
                    ("Foo Private", "company"),
                )
        finally:
            con.close()


class TestRebuildP3PkReversal:
    def test_pk_reversed_in_ddl(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            ddl = _ddl_for(con, "graph_analytics")
            assert "PRIMARY KEY (metric, entity_name)" in ddl
            assert "PRIMARY KEY (entity_name, metric)" not in ddl
        finally:
            con.close()

    def test_metric_query_uses_search_not_scan(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            plan = con.execute(
                "EXPLAIN QUERY PLAN SELECT entity_name, value FROM graph_analytics WHERE metric = 'pagerank' ORDER BY entity_name"
            ).fetchall()
            detail = plan[-1][-1]
            assert "SEARCH" in detail, f"expected SEARCH after P3 PK reversal, got: {detail}"
            assert "metric=?" in detail, f"expected metric=? prefix scan, got: {detail}"
        finally:
            con.close()


class TestRebuildIndexes:
    def test_all_indexes_present_post_rebuild(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            indexes = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name"
                ).fetchall()
            }
            expected = {
                "idx_entities_entity_type",
                "idx_entities_name_nocase",
                "idx_entities_normalized_name",
                "idx_entities_sector_classification",
                "ge_type_idx",
                "ge_target_idx",
                "ge_valid_idx",
            }
            assert expected <= indexes, f"missing indexes post-rebuild: {expected - indexes}"
        finally:
            con.close()


class TestRebuildIdempotency:
    def test_rebuild_is_idempotent(self, tmp_db):
        s1 = rebuild(tmp_db, dry_run=False)
        s2 = rebuild(tmp_db, dry_run=False)
        assert s1["post_entities"] == s2["post_entities"]
        assert s1["post_edges"] == s2["post_edges"]
        assert s1["post_analytics"] == s2["post_analytics"]


class TestRebuildGenerationTriggers:
    def test_generation_triggers_restored_post_rebuild(self, tmp_db):
        stats = rebuild(tmp_db, dry_run=False)
        assert stats["generation_triggers"] == 6
        con = sqlite3.connect(str(tmp_db))
        try:
            names = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
            for tbl in ("entities", "graph_edges"):
                for op in ("insert", "delete", "update"):
                    assert f"trg_{tbl}_{op}_gen" in names
        finally:
            con.close()

    def test_post_rebuild_write_bumps_generation(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            gen_before = int(
                con.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()[0]
            )
            name = con.execute("SELECT name FROM entities LIMIT 1").fetchone()[0]
            con.execute("UPDATE entities SET last_updated = last_updated WHERE name = ?", (name,))
            con.commit()
            gen_after = int(
                con.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()[0]
            )
        finally:
            con.close()
        assert gen_after == gen_before + 1


class TestRebuildAtomicity:
    def test_rebuild_rolls_back_on_failure(self, tmp_db, monkeypatch):
        pre = sqlite3.connect(str(tmp_db))
        pre_edges = pre.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        pre.close()

        import helpers.maintenance.rebuild_schema as rs

        orig = rs._rebuild_table
        calls = {"n": 0}

        def failing(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("INJECTED FAILURE")
            return orig(*args, **kwargs)

        monkeypatch.setattr(rs, "_rebuild_table", failing)

        with pytest.raises(RuntimeError, match="INJECTED"):
            rebuild(tmp_db, dry_run=False)

        post = sqlite3.connect(str(tmp_db))
        post_edges = post.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        post.close()

        assert pre_edges == post_edges, (
            f"ROLLBACK failed: graph_edges went {pre_edges} -> {post_edges}"
        )


# ---------------------------------------------------------------------------
# _ddl_for_new_table — pure unit tests
# ---------------------------------------------------------------------------
def test_ddl_strips_if_not_exists():
    ddl = "CREATE TABLE IF NOT EXISTS entities (name TEXT PRIMARY KEY)"
    result = rs._ddl_for_new_table(ddl, "entities", "entities_new")
    assert "IF NOT EXISTS" not in result
    assert "entities_new" in result
    assert "(name TEXT PRIMARY KEY)" in result


def test_ddl_preserves_columns():
    ddl = "CREATE TABLE IF NOT EXISTS foo (a TEXT, b INTEGER, c REAL)"
    result = rs._ddl_for_new_table(ddl, "foo", "foo_new")
    assert "(a TEXT, b INTEGER, c REAL)" in result


def test_ddl_only_replaces_first_occurrence():
    ddl = "CREATE TABLE IF NOT EXISTS my_table (my_table_id TEXT)"
    result = rs._ddl_for_new_table(ddl, "my_table", "my_table_new")
    assert "my_table_new" in result
    assert "my_table_id" in result


# ---------------------------------------------------------------------------
# rebuild — error paths
# ---------------------------------------------------------------------------
def test_rebuild_raises_on_missing_db(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError, match="Database not found"):
        rs.rebuild(str(tmp_path / "nonexistent.db"))


# ---------------------------------------------------------------------------
# CLI guard polarity (shared_routines_cli_guards W2)
# ---------------------------------------------------------------------------
def test_cli_dry_run_default_apply_writes(tmp_db, capsys):
    # tmp_db fixture already seeds data
    assert rs.main(["--db", str(tmp_db), "--no-duckdb-refresh"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out

    assert rs.main(["--db", str(tmp_db), "--apply", "--no-duckdb-refresh"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" not in out
