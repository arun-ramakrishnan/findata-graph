"""Bundle Q2: EXPLAIN QUERY PLAN regression guards for hot queries.

Pre-Q2, only ONE hot query had a plan-level regression test (the C2
NOCASE-index guard in test_static_checks.py:315). P3 and Q1 both went
unnoticed because no test pinned their query plans — a full SCAN was the
only signal something was wrong, and it surfaced only via manual EXPLAIN.

This file parametrically asserts each hot query resolves to SEARCH (not
SCAN) over the expected index. If a future schema change (dropping an
index, a query rewrite, a PK reversal) silently degrades a hot path to a
full scan, these tests fail immediately.

These tests run against the once-per-run shared full-corpus template
(``tests/_tmp_hygiene.full_template``, a backup of the live DB built
once per run), opened READ-ONLY in place — production_db_copy_audit S2:
every test here is EXPLAIN-only, so the ten former per-test backups
(10 × 307 MiB ≈ 3.0 GiB transient per run) were byte-identical waste.
They're marked ``live`` because they need the real schema + indexes (a
synthetic fixture might not reproduce the planner's index choices).
"""

import sqlite3
from pathlib import Path

import pytest

from tests._tmp_hygiene import full_template  # noqa: E402


@pytest.fixture(scope="module")
def tmp_db(tmp_path_factory) -> Path:
    """The shared full-corpus template (S2): read-only EXPLAIN tests
    share ONE copy instead of backing up per test. ``_ro`` fails loudly
    if a future test tries to write the file every worker shares."""
    return full_template(tmp_path_factory)


def _ro(db: Path) -> sqlite3.Connection:
    """Read-only connection: a write attempt raises instead of mutating
    the shared template."""
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _plan_detail(conn, sql, params=()):
    """Run EXPLAIN QUERY PLAN and return the concatenated detail string
    (the last column of every row in the plan). Tests assert on this."""
    plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return " ".join(row[-1] for row in plan)


@pytest.mark.live
class TestEntityQueryPlans:
    """Hot queries on the entities table."""

    def test_entity_by_name_uses_pk(self, tmp_db):
        """Direct lookup by name (the PK) must SEARCH, not SCAN."""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(con, "SELECT name FROM entities WHERE name = ?", ("CEAT",))
            assert "SEARCH" in detail, f"name lookup scanning: {detail}"
        finally:
            con.close()

    def test_entity_by_name_nocase_uses_index(self, tmp_db):
        """The C2 guard: COLLATE NOCASE resolver must use the NOCASE index.
        (Mirrors the existing test_static_checks.py guard, but against the
        live schema to catch a production-index drop.)"""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(
                con,
                "SELECT name FROM entities WHERE name = ? COLLATE NOCASE",
                ("ceat",),
            )
            assert "SEARCH" in detail and "idx_entities_name_nocase" in detail, (
                f"NOCASE resolver not using index: {detail}"
            )
        finally:
            con.close()

    def test_entity_by_file_path_uses_index(self, tmp_db):
        """Q1 guard: file_path lookup must use idx_entities_file_path, not
        SCAN. This was a full scan before Q1 added the index."""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(
                con,
                "SELECT name FROM entities WHERE file_path = ?",
                ("findata/Companies/CEAT.md",),
            )
            assert "SEARCH" in detail and "idx_entities_file_path" in detail, (
                f"file_path lookup not using index: {detail}"
            )
        finally:
            con.close()


@pytest.mark.live
class TestGraphAnalyticsQueryPlans:
    """Hot queries on graph_analytics."""

    def test_metric_filter_uses_pk_prefix(self, tmp_db):
        """P3 guard: WHERE metric=? must SEARCH via the reversed PK
        (metric, entity_name), not SCAN. This was a full scan before P3
        reversed the PK column order."""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(
                con,
                "SELECT entity_name, value FROM graph_analytics "
                "WHERE metric = ? ORDER BY entity_name",
                ("pagerank",),
            )
            assert "SEARCH" in detail and "metric=?" in detail, (
                f"metric filter not using PK prefix: {detail}"
            )
            # Must NOT be a SCAN.
            assert "SCAN" not in detail, f"metric filter scanning: {detail}"
        finally:
            con.close()


@pytest.mark.live
class TestEntityTagsQueryPlans:
    """Hot queries on entity_tags."""

    def test_tags_by_entity_name_uses_index(self, tmp_db):
        """The entity_tags lookup by entity_name (used by api_entity_detail
        to fetch tags) must SEARCH via the PK, not SCAN."""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(
                con,
                "SELECT tag FROM entity_tags WHERE entity_name = ? ORDER BY tag",
                ("CEAT",),
            )
            assert "SEARCH" in detail, f"entity_tags lookup scanning: {detail}"
        finally:
            con.close()

    # NOTE: a tag-prefix LIKE ('sector/%') is intentionally NOT covered here.
    # LIKE with a wildcard defeats the index unless case_sensitive_like is
    # set or the index is COLLATE NOCASE (see doc/improvements/pending_improvs.md C3, DONE).
    # The scan is the documented, accepted behavior — not a regression.


@pytest.mark.live
class TestGraphEdgesQueryPlans:
    """Hot queries on graph_edges."""

    def test_edges_by_source_uses_index(self, tmp_db):
        """Filtering graph_edges by source (the forward-edge lookup) must
        SEARCH, not SCAN. With ge_source_idx removed (redundant — 2026-08-04),
        the planner uses sqlite_autoindex_graph_edges_1, the UNIQUE(source,
        target, edge_type) index that leads with `source`; it's covering for a
        source-only filter."""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(
                con,
                "SELECT target, edge_type FROM graph_edges WHERE source = ?",
                ("CEAT",),
            )
            assert "SEARCH" in detail, f"source filter scanning: {detail}"
        finally:
            con.close()

    def test_edges_by_target_uses_index(self, tmp_db):
        """Filtering graph_edges by target (the reverse-edge lookup) must
        SEARCH via ge_target_idx (the UNIQUE index is source-first, so it
        can't serve a target-only filter)."""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(
                con,
                "SELECT source, edge_type FROM graph_edges WHERE target = ?",
                ("CEAT",),
            )
            assert "SEARCH" in detail and "ge_target_idx" in detail, (
                f"target filter not using ge_target_idx: {detail}"
            )
        finally:
            con.close()

    def test_edges_by_type_uses_index(self, tmp_db):
        """Filtering graph_edges by edge_type must use ge_type_idx."""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(
                con,
                "SELECT source, target FROM graph_edges WHERE edge_type = ?",
                ("part_of",),
            )
            assert "SEARCH" in detail and "ge_type_idx" in detail, (
                f"edge_type filter not using index: {detail}"
            )
        finally:
            con.close()

    def test_edges_unique_constraint_used_for_lookup(self, tmp_db):
        """The (source, target, edge_type) UNIQUE constraint must be used
        for a full triple lookup (the apply_edges existence check)."""
        con = _ro(tmp_db)
        try:
            detail = _plan_detail(
                con,
                "SELECT 1 FROM graph_edges WHERE source = ? AND target = ? AND edge_type = ?",
                ("CEAT", "Automotive", "part_of"),
            )
            assert "SEARCH" in detail, f"triple lookup scanning: {detail}"
        finally:
            con.close()

    def test_cross_sector_bridges_uses_index(self, tmp_db):
        """C3 guard: cross_sector_bridges() filters graph_edges to
        edge_type IN ('jv_with','acquired') then joins entities twice (source,
        target). The edge_type filter must use ge_type_idx and both entity
        joins must hit the entities PK — no full SCAN. This is the Slice-B plan
        tripwire (test_cross_sector_bridges_plan_uses_indexes) that the empty
        tests/test_sql_perf_guards.py husk was meant to hold; it lives here now.
        """
        con = _ro(tmp_db)
        try:
            sql = """
                SELECT e.edge_type,
                       c1.sector_classification AS sector_a,
                       c2.sector_classification AS sector_b,
                       COUNT(*) AS n
                FROM graph_edges e
                JOIN entities c1 ON c1.name = e.source
                JOIN entities c2 ON c2.name = e.target
                WHERE e.edge_type IN ('jv_with', 'acquired')
                  AND c1.sector_classification IS NOT NULL
                  AND c2.sector_classification IS NOT NULL
                  AND c1.sector_classification <> c2.sector_classification
                GROUP BY e.edge_type, c1.sector_classification, c2.sector_classification
                ORDER BY n DESC, e.edge_type
            """
            detail = _plan_detail(con, sql)
            assert "SEARCH" in detail, f"cross_sector_bridges scanning: {detail}"
            assert "ge_type_idx" in detail, f"edge_type filter not using ge_type_idx: {detail}"
            assert "SCAN" not in detail, f"cross_sector_bridges full scan: {detail}"
        finally:
            con.close()
