"""Tests for Bundle P: one-shot schema rebuild (rebuild_schema.py).

Pins that the rebuild brings the live SQLite DB into conformance with the
canonical DDL constants — landing the json_valid CHECK (P1), the canonical
entities layout + complete name-suffix CHECK (P2), and the reversed
graph_analytics PK (P3) — while preserving every row.

These tests run against a COPY of the live DB (sqlite3.backup into tmp_path),
not the live DB itself, so they're safe to run in CI without mutating
production data. They're NOT marked ``live`` because they don't hit
memory/research.db directly — they copy it and rebuild the copy.
"""

import sqlite3
from pathlib import Path

import pytest

from helpers.graph.query import DB_PATH
from helpers.maintenance.rebuild_schema import rebuild
import helpers.maintenance.rebuild_schema as rs  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    """A copy of the production SQLite DB at tmp_path/test.db."""
    out = tmp_path / "test.db"
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(out))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return out


def _ddl_for(con, table):
    return con.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]


class TestRebuildDataPreservation:
    def test_rebuild_preserves_row_counts(self, tmp_db):
        """All three tables must keep their exact row counts across the
        rebuild — no data loss from the CREATE-INSERT-DROP-RENAME cycle.

        Asserts the actual invariant: the rebuild loses zero rows, so the
        post-rebuild count equals the pre-rebuild count for each table. The
        ``pre_*`` counts are captured by a plain SELECT COUNT(*) on the live
        table *before* any rewrite (rebuild_schema.py), so they are an
        independent source of truth; a buggy INSERT-SELECT that dropped rows
        would surface here as ``post < pre``.

        We deliberately do NOT pin absolute counts. The live DB grows and
        trims with every ingest and maintenance run, so a hardcoded total
        would break on every legitimate change while never catching a real
        bug (a genuine data-loss regression fails ``pre == post``). The
        ``> 0`` guard rejects the one false-pass the equality allows on its
        own (an empty rebuild: ``0 == 0``)."""
        s = rebuild(tmp_db, dry_run=False)
        assert s["pre_entities"] == s["post_entities"]  # zero entity rows lost
        assert s["pre_edges"] == s["post_edges"]  # zero edge rows lost
        assert s["pre_analytics"] == s["post_analytics"]  # zero analytics rows lost
        assert s["pre_entities"] > 0 and s["pre_edges"] > 0 and s["pre_analytics"] > 0

    def test_rebuild_preserves_relations_view(self, tmp_db):
        """The relations VIEW (dropped during graph_edges rebuild,
        recreated after) must survive and still expose every graph_edges row.

        Asserts the real invariant — the VIEW's row count matches its
        underlying table — rather than a hardcoded live-DB total, which would
        break on every ingest. See test_rebuild_preserves_row_counts for why
        absolute counts are not pinned here."""
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
        """Post-rebuild, PRAGMA foreign_key_check must report 0 violations —
        the INSERT-SELECT copy preserves every FK value verbatim."""
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            violations = con.execute("PRAGMA foreign_key_check").fetchall()
            assert violations == [], f"FK violations post-rebuild: {violations}"
        finally:
            con.close()


class TestRebuildP1JsonValidCheck:
    """P1: the live graph_edges was missing CHECK (json_valid(properties)).
    The rebuild must land it."""

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
        """A malformed-JSON insert that SUCCEEDS on the pre-rebuild live DB
        must RAISE after the rebuild — the json_valid CHECK is now enforced."""
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO graph_edges (source, target, edge_type, "
                    "source_ref, properties) VALUES (?, ?, ?, ?, ?)",
                    ("CEAT", "MRF", "test_probe", "t", "NOT_JSON{"),
                )
        finally:
            con.close()


class TestRebuildP2EntitiesCheck:
    """P2: the live entities CHECK accepted 'Foo Ltd' (no dot) because it
    only had '%Ltd.%'. The canonical CHECK has both '%Ltd' and '%Ltd.'.
    The rebuild must land the complete CHECK."""

    def test_no_dot_suffix_rejected_post_rebuild(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
                    ("Foo Ltd", "company"),
                )
        finally:
            con.close()

    def test_canonical_suffixes_all_rejected(self, tmp_db):
        """All 5 canonical suffixes must be rejected: Ltd, Ltd., Limited,
        Pvt, Private."""
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        con.execute("PRAGMA foreign_keys = ON")
        try:
            for bad in ("Foo Ltd", "Foo Ltd.", "Foo Limited", "Foo Pvt", "Foo Private"):
                with pytest.raises(sqlite3.IntegrityError):
                    con.execute(
                        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
                        (bad, "company"),
                    )
        finally:
            con.close()

    def test_suffix_guard_is_company_scoped(self, tmp_path):
        """Bundle M4: the name-suffix CHECK is scoped to entity_type='company'.
        Non-company types (sector, super_sector, sub_sector) must ACCEPT names
        containing 'Private' — e.g. the 'Private_Sector' sub_sector under
        Banking. The guard exists for company name cleanup (reject 'Foo Pvt
        Ltd'), not for curated taxonomy terms.

        Uses a fresh DB with the canonical DDL (not the rebuilt live copy,
        which already contains Private_Sector and would hit the UNIQUE PK)."""
        from helpers.maintenance.migrate_to_graph_edges import ENTITIES_DDL

        db = tmp_path / "fresh.db"
        con = sqlite3.connect(str(db))
        con.executescript(ENTITIES_DDL)
        con.execute("PRAGMA foreign_keys = ON")
        try:
            # These would all be rejected under the old blanket CHECK.
            for name, etype in [
                ("Private_Sector", "sub_sector"),
                ("Acme Private Banking", "sector"),
                ("Something Private", "super_sector"),
            ]:
                con.execute(
                    "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
                    (name, etype),
                )
            con.commit()
            # Sanity: a company with the same suffix is STILL rejected.
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(
                    "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
                    ("Foo Private", "company"),
                )
        finally:
            con.close()


class TestRebuildP3PkReversal:
    """P3: graph_analytics PK reversed from (entity_name, metric) to
    (metric, entity_name). The hot query WHERE metric=? must SEARCH not SCAN."""

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
        """EXPLAIN QUERY PLAN for the /api/graph/metrics hot query must show
        SEARCH (prefix scan on the reversed PK), not SCAN (full table)."""
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            plan = con.execute(
                "EXPLAIN QUERY PLAN SELECT entity_name, value FROM "
                "graph_analytics WHERE metric = 'pagerank' ORDER BY entity_name"
            ).fetchall()
            detail = plan[-1][-1]  # the detail column of the leaf node
            assert "SEARCH" in detail, f"expected SEARCH after P3 PK reversal, got: {detail}"
            assert "metric=?" in detail, f"expected metric=? prefix scan, got: {detail}"
        finally:
            con.close()


class TestRebuildIndexes:
    """All indexes must be recreated post-rebuild (they're dropped with
    their tables and re-run from the canonical index constants)."""

    def test_all_indexes_present_post_rebuild(self, tmp_db):
        rebuild(tmp_db, dry_run=False)
        con = sqlite3.connect(str(tmp_db))
        try:
            indexes = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND sql IS NOT NULL ORDER BY name"
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
        """Running rebuild twice must be a no-op: same counts, no error,
        no data loss. The second run rebuilds already-canonical tables."""
        s1 = rebuild(tmp_db, dry_run=False)
        s2 = rebuild(tmp_db, dry_run=False)
        assert s1["post_entities"] == s2["post_entities"]
        assert s1["post_edges"] == s2["post_edges"]
        assert s1["post_analytics"] == s2["post_analytics"]


class TestRebuildGenerationTriggers:
    """DROP TABLE destroys a table's triggers — the rebuild must restore
    the six trg_*_gen triggers or every later entities/graph_edges write
    stops bumping db_meta.generation and _is_warm goes blind silently
    (the DuckDB cache serves stale graph data; the snapshot gen check
    always 'matches' a frozen number)."""

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
        """The pin that matters behaviorally: after the rebuild, a row write
        on a rebuilt table actually moves the epoch."""
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
        """If the rebuild fails mid-way (e.g. a constraint violation on the
        new table), ROLLBACK must restore the DB to its pre-rebuild state —
        no partial rebuild, no data loss."""
        pre = sqlite3.connect(str(tmp_db))
        pre_edges = pre.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        pre.close()

        import helpers.maintenance.rebuild_schema as rs

        orig = rs._rebuild_table
        calls = {"n": 0}

        def failing(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:  # graph_analytics rebuild (3rd call)
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
    """_ddl_for_new_table strips IF NOT EXISTS and swaps name."""
    ddl = "CREATE TABLE IF NOT EXISTS entities (name TEXT PRIMARY KEY)"
    result = rs._ddl_for_new_table(ddl, "entities", "entities_new")
    assert "IF NOT EXISTS" not in result
    assert "entities_new" in result
    assert "(name TEXT PRIMARY KEY)" in result  # column definitions preserved


def test_ddl_preserves_columns():
    """_ddl_for_new_table keeps all column definitions intact."""
    ddl = "CREATE TABLE IF NOT EXISTS foo (a TEXT, b INTEGER, c REAL)"
    result = rs._ddl_for_new_table(ddl, "foo", "foo_new")
    assert "(a TEXT, b INTEGER, c REAL)" in result


def test_ddl_only_replaces_first_occurrence():
    """Only the first occurrence of the table name is replaced."""
    ddl = "CREATE TABLE IF NOT EXISTS my_table (my_table_id TEXT)"
    result = rs._ddl_for_new_table(ddl, "my_table", "my_table_new")
    # First occurrence replaced, column name kept
    assert "my_table_new" in result
    assert "my_table_id" in result


# ---------------------------------------------------------------------------
# rebuild — error paths
# ---------------------------------------------------------------------------
def test_rebuild_raises_on_missing_db(tmp_path):
    """rebuild() raises FileNotFoundError when DB doesn't exist."""
    import pytest

    with pytest.raises(FileNotFoundError, match="Database not found"):
        rs.rebuild(str(tmp_path / "nonexistent.db"))


# ---------------------------------------------------------------------------
# CLI guard polarity (shared_routines_cli_guards W2)
# ---------------------------------------------------------------------------
def test_cli_dry_run_default_apply_writes(tmp_db, capsys):
    """Bare main() must NOT rebuild (dry-run report); --apply performs the
    destructive rebuild. Formerly the polarity was inverted (--dry-run opted
    out of a default-write). Runs against a tmp backup of the live DB —
    same pattern as the rebuild data-preservation tests."""
    # Bare invocation: report only, DB untouched (mtime-independent proof:
    # stats say dry-run and no rebuild side effects).
    assert rs.main(["--db", str(tmp_db), "--no-duckdb-refresh"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" in out

    # --apply: rebuild runs.
    assert rs.main(["--db", str(tmp_db), "--apply", "--no-duckdb-refresh"]) == 0
    out = capsys.readouterr().out
    assert "dry-run" not in out
