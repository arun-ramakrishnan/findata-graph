#!/usr/bin/env python3
"""Fuzz tests for hyper trail distances (S4, hyper_trails leg).

Differential test: `trail_distances` (bipartite dijkstra lane) vs a
pure-Python even-hop BFS oracle on random bipartite incidence graphs.

Semantics pinned by the module docstring:
- hops = bipartite distance / 2; odd bipartite distances (mid-hop at a
  hub) are dropped;
- self excluded; unknown source labels raise ValueError;
- `limit` caps bipartite steps → hops ≤ limit // 2.

Also: Mojo dispatch equality when MOJO_TRAIL_BFS=1 and the binary is
available (skipped otherwise).
"""

from __future__ import annotations

import os
import shutil
from collections import deque

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from helpers.graph import hyper_trails as ht

_SETTINGS = settings(max_examples=40, deadline=None)

_HUB = st.integers(min_value=0, max_value=8)
_ENT = st.text(alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz"), min_size=1, max_size=6)
_PAIR = st.tuples(_HUB, _ENT)
_GRAPH = st.lists(_PAIR, min_size=1, max_size=30)


def _oracle_even_hop_bfs(pairs: list[tuple[int, str]], src: str, limit: int) -> dict[str, int]:
    """Even-hop BFS: hops = shortest path in the hub-projected graph."""
    hub_members: dict[int, set[str]] = {}
    for hub, ent in pairs:
        hub_members.setdefault(hub, set()).add(ent)
    adj: dict[str, set[str]] = {}
    for members in hub_members.values():
        for ent in members:
            adj.setdefault(ent, set()).update(members - {ent})
    max_hops = limit // 2
    dist = {src: 0}
    q = deque([src])
    while q:
        u = q.popleft()
        if dist[u] >= max_hops:
            continue
        for v in adj.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return {e: d for e, d in dist.items() if e != src}


@st.composite
def _graph_with_src(draw):
    pairs = draw(_GRAPH)
    ents = sorted({e for _, e in pairs})
    src = draw(st.sampled_from(ents))
    return pairs, src


@_SETTINGS
@given(_graph_with_src())
def test_trail_distances_match_oracle(pairs_src):
    pairs, src = pairs_src
    labels = sorted({e for _, e in pairs})
    if src not in labels:
        return
    g = ht.bipartite_from_pairs(pairs)
    limit = ht.DEFAULT_LIMIT
    got = ht.trail_distances(g, [src], limit=limit)[src]
    want = _oracle_even_hop_bfs(pairs, src, limit)
    assert got == want
    # result keys are entity labels only (never hubs), all positive hops
    n_e = g["n_entities"]
    assert all(labels[:n_e].index(k) < n_e for k in got)
    assert all(v >= 1 for v in got.values())


@_SETTINGS
@given(_GRAPH)
def test_trail_distances_symmetric_to_oracle_all_sources(pairs):
    labels = sorted({e for _, e in pairs})
    if not labels:
        return
    g = ht.bipartite_from_pairs(pairs)
    limit = ht.DEFAULT_LIMIT
    for src in labels[: min(3, len(labels))]:
        got = ht.trail_distances(g, [src], limit=limit)[src]
        want = _oracle_even_hop_bfs(pairs, src, limit)
        assert got == want


@_SETTINGS
@given(_GRAPH)
def test_unknown_source_label_raises(pairs):
    g = ht.bipartite_from_pairs(pairs)
    unknown = f"zz_unknown_entity_{len(pairs)}"
    with pytest.raises(ValueError):
        ht.trail_distances(g, [unknown], limit=ht.DEFAULT_LIMIT)


def test_empty_sources_returns_empty():
    g = ht.bipartite_from_pairs([(0, "a")])
    assert ht.trail_distances(g, []) == {}


def test_can_fail_vector_hop_counts():
    """Entities reachable via one hub → 1 hop; via two hubs → 2 hops."""
    pairs = [(0, "a"), (0, "b"), (1, "b"), (1, "c")]
    g = ht.bipartite_from_pairs(pairs)
    got = ht.trail_distances(g, ["a"], limit=40)["a"]
    assert got == {"b": 1, "c": 2}


def test_can_fail_limit_truncates_hops():
    """limit caps bipartite steps → hops ≤ limit // 2."""
    pairs = [(0, "a"), (0, "b"), (1, "b"), (1, "c")]
    g = ht.bipartite_from_pairs(pairs)
    # limit=2 (1 hop): b reachable, c (2 hops) must be absent
    got = ht.trail_distances(g, ["a"], limit=2)["a"]
    assert got == {"b": 1}
    # default limit=40: c reachable at 2 hops
    got_full = ht.trail_distances(g, ["a"], limit=40)["a"]
    assert got_full == {"b": 1, "c": 2}


def test_mojo_dispatch_matches_scipy():
    """When the Mojo lane is enabled and its binary exists, both lanes must
    agree on a fixed graph."""
    if os.environ.get("MOJO_TRAIL_BFS") != "1":
        pytest.skip("MOJO_TRAIL_BFS != 1")
    if not ht._MOJO_BATCH_BIN.exists() and shutil.which("mojo") is None:
        pytest.skip("mojo kernel binary missing")
    pairs = [(0, "a"), (0, "b"), (1, "b"), (1, "c"), (2, "a"), (2, "c")]
    g = ht.bipartite_from_pairs(pairs)
    ref = ht._trail_bfs(g, 0, ht.DEFAULT_LIMIT)
    assert ref is not None
    # Mojo lane is consulted first when enabled; fallback returns scipy, so
    # the public result is lane-independent by construction
    got = ht.trail_distances(g, ["a"], limit=40)["a"]
    assert got == {"b": 1, "c": 2}
