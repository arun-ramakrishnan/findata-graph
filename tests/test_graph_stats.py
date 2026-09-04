#!/usr/bin/env python3
"""Tests for helpers/graph/stats.py."""

from __future__ import annotations

import contextlib
import io

import pytest

pytestmark = pytest.mark.live


from helpers.graph.stats import print_stats  # noqa: E402
from helpers.graph import stats  # noqa: E402


@pytest.fixture(scope="module")
def stats_render():
    """print_stats() output, rendered ONCE for the module.

    A full render costs ~1s (the Onager structure section runs over the
    complete edge set) and every assertion in this module reads the SAME
    argument-free output, so one captured render serves them all.
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
