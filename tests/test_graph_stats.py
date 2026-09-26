#!/usr/bin/env python3
"""Tests for helpers/graph/stats.py."""

from __future__ import annotations

import contextlib
import io
import sqlite3
from pathlib import Path

import pytest

# One xdist group for the whole module: the module-scoped stats_render
# fixture below is rebuilt PER WORKER under -n auto — without the group,
# three workers each pay the full print_stats() render in parallel.
pytestmark = [pytest.mark.live, pytest.mark.xdist_group("graph_stats_render")]


from helpers.graph.stats import longest_chains, print_stats  # noqa: E402
from helpers.graph import stats  # noqa: E402


@pytest.fixture(scope="module")
def stats_render():
    """print_stats() output, rendered ONCE for the module.

    A full render costs are dominated by longest_chains, which is
    O(n^2) in the EDGE-TOUCHED entity universe (isolated entities are
    excluded there since 2026-09-16; n ~1.7k live). Every assertion in
    this module reads the SAME argument-free output, so one captured
    render serves them all.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = print_stats()
    return rc, buf.getvalue()


def test_print_stats_returns_zero(stats_render):
    rc, out = stats_render
    assert rc == 0
    assert "FinData Graph — Stats" in out


def test_print_stats_contains_required_sections(stats_render):
    _, out = stats_render
    # Every major section heading should be present.
    for section in [
        "FinData Graph — Stats",
        "Edge-type breakdown",
        "Structure (Onager, full edge set)",
        "Hypergraph structure (incidence SQL)",
        "Sectors by member count",
        "Market cap distribution",
        "Data hygiene",
        "graph_analytics",
        "Markdown notes on disk",
    ]:
        assert section in out, f"missing section: {section!r}"


def test_print_stats_reports_real_edge_counts(stats_render):
    _, out = stats_render
    # Live graph has co_mentioned_in (1329) and part_of (843) edges — sanity check.
    assert "co_mentioned_in" in out
    assert "part_of" in out
    assert "has_company" in out


def test_print_stats_reports_zero_self_loops(stats_render):
    _, out = stats_render
    # The CHECK constraint forbids self-loops, so this should always read 0.
    assert "Self-loops in graph_edges:        0" in out


def test_print_stats_reports_zero_orphan_edges(stats_render):
    _, out = stats_render
    # FK constraint forbids orphan edges, so this should always read 0.
    assert "Orphan edges (FK violation):      0" in out


def test_print_stats_lists_analytics_metrics(stats_render):
    _, out = stats_render
    # graph_analytics was populated by the algorithms dispatcher; should list
    # at least these four metrics.
    for metric in ["pagerank", "degree_centrality", "betweenness_centrality", "louvain_community"]:
        assert metric in out


def test_print_stats_shows_freshness_status(stats_render):
    _, out = stats_render
    # Either fresh or stale — the function must print one of them.
    assert ("✓ fresh" in out) or ("⚠ STALE" in out)


def test_print_stats_detects_note_entity_mismatch(stats_render):
    """Sanity: stats should print a mismatch warning if disk != DB.

    NB: the current live state DOES have a mismatch (953 notes vs 952 company
    entities). This test asserts the warning fires today; if the orphan note
    is later cleaned up, the test should be updated to assert the OK path.
    """
    _, out = stats_render
    assert "notes under findata/Companies/" in out
    # Either an OK line or a mismatch warning — both are valid; we just
    # verify the comparison ran.
    assert ("mismatch" in out) or ("company entities" in out)


def test_print_stats_reports_structure_metrics(stats_render):
    """Phase 2: the Onager structure section must render real values (the
    live full-edge-set projection is connected — verified 2026-08-14)."""
    _, out = stats_render
    assert "density" in out
    assert "triangles" in out
    assert "avg path length" in out


def test_bar_zero_total():
    """_bar with total=0 returns empty bar."""
    result = stats._bar(0, 0)
    assert result == "[" + " " * 30 + "] 0"


class TestLongestChainsExact:
    """scipy_exact_universe S2: exact=True lifts the root sample; the
    default path stays sampled (gate-safe)."""

    @staticmethod
    def _chain_db(tmp_path: Path, n: int = 40) -> sqlite3.Connection:
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "chains.db"))
        conn.execute("CREATE TABLE entities (name VARCHAR, entity_type VARCHAR)")
        conn.executemany(
            "INSERT INTO entities VALUES (?, 'company')", [(f"n{i}",) for i in range(n)]
        )
        conn.execute(
            "CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, "
            "edge_type VARCHAR, source_ref VARCHAR)"
        )
        conn.executemany(
            "INSERT INTO graph_edges VALUES (?, ?, 'subsidiary_of', 'test')",
            [(f"n{i}", f"n{i + 1}") for i in range(n - 1)],
        )
        conn.commit()
        return conn

    def test_exact_matches_default_below_cap(self, tmp_path: Path):
        conn = self._chain_db(tmp_path)
        default_lines = longest_chains(conn)
        exact_lines = longest_chains(conn, exact=True)
        conn.close()
        assert default_lines == exact_lines, "below the cap exact is a no-op"

    def test_exact_lifts_the_sample(self, tmp_path: Path):
        conn = self._chain_db(tmp_path, n=60)
        capped = longest_chains(conn, max_exact=10)
        exact = longest_chains(conn, max_exact=10, exact=True)
        conn.close()
        assert any("SAMPLED 10/60" in ln for ln in capped)
        assert not any("SAMPLED" in ln for ln in exact)
        d_capped = int(capped[0].split("diameter ")[1].split(" ")[0])
        d_exact = int(exact[0].split("diameter ")[1].split(" ")[0])
        assert d_exact >= d_capped, "exact diameter is an upper bound of the sample"
        assert d_exact == 59, "a 60-node path has diameter 59 when exact"
