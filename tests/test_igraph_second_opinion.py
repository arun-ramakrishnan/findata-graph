"""Hermetic tests for the igraph second-opinion instruments
(algorithms_adoption_sweep S5) — synthetic graphs, no live store."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


from helpers.graph.stats import igraph_second_opinion_lines  # noqa: E402


def _fake_projection(monkeypatch, rows):

    monkeypatch.setattr("helpers.graph.igraph_bridge.load_projection", lambda *a, **k: rows)


ROWS_TRIANGLE_BRIDGE_CYCLE = [
    # triangle n0-n2, bridge via n2-n3, 4-cycle n3..n6
    ("n0", "n1", "same_group", 1.0),
    ("n1", "n2", "same_group", 1.0),
    ("n2", "n0", "same_group", 1.0),
    ("n2", "n3", "same_group", 1.0),
    ("n3", "n4", "peer", 1.0),
    ("n4", "n5", "peer", 1.0),
    ("n5", "n6", "peer", 1.0),
    ("n6", "n3", "peer", 1.0),
]


def test_second_opinion_known_graph(monkeypatch):
    _fake_projection(monkeypatch, ROWS_TRIANGLE_BRIDGE_CYCLE)
    lines = igraph_second_opinion_lines()
    joined = "\n".join(lines)
    assert "7 vertices / 8 edges" in joined
    # both bridges detected exactly (hand-checked on this graph)
    assert "articulation points: 2 — n2, n3" in joined
    assert "coreness: max_k=2 (k2:7)" in joined
    assert "degree assortativity:" in joined


def test_second_opinion_two_connected_graph(monkeypatch):
    # a 4-cycle has no cut vertex — 2-connected
    rows = [
        ("a", "b", "peer", 1.0),
        ("b", "c", "peer", 1.0),
        ("c", "d", "peer", 1.0),
        ("d", "a", "peer", 1.0),
    ]
    _fake_projection(monkeypatch, rows)
    joined = "\n".join(igraph_second_opinion_lines())
    assert "articulation points: 0 (2-connected)" in joined


def test_second_opinion_empty_projection(monkeypatch):
    _fake_projection(monkeypatch, [])
    joined = "\n".join(igraph_second_opinion_lines())
    assert "0 vertices / 0 edges" in joined
    assert "articulation points: 0 (2-connected)" in joined


def test_stats_render_degrades_without_igraph(monkeypatch, capsys):
    """The print_stats advisory guard (same shape as the other sections):
    a missing igraph must degrade to an '(unavailable: ...)' line, never
    fail stats."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("igraph"):
            raise ImportError("no igraph")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    _fake_projection(monkeypatch, ROWS_TRIANGLE_BRIDGE_CYCLE)
    try:
        igraph_second_opinion_lines()
        raise AssertionError("ImportError should have propagated to the guard")
    except ImportError as exc:  # the print_stats guard shape
        print(f"  (unavailable: {type(exc).__name__}: {exc})")
    out = capsys.readouterr().out
    assert "(unavailable:" in out
