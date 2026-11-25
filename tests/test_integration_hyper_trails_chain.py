#!/usr/bin/env python3
"""Integration: the hyper-trails chain (test_coverage_gap_sweep S5).

A tiny DuckDB h_incidence store flows through the whole trail lane:

- `build_bipartite` → census invariants (arcs = 2 × distinct pairs);
- `trail_distances` across all entities vs the even-hop oracle;
- `trail_source_stats` shape (reachable / hop_histogram / limit);
- Rust kernel batch lane equals the scipy per-source lane when
  `graph_kernels` is importable (skipped otherwise);
- `build_bipartite_csr` writes csr bins with matching metadata.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import duckdb
import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.graph import hyper_trails as ht  # noqa: E402

EDGES = [
    ("e_a", "p1"),
    ("e_a", "p2"),
    ("e_a", "c1"),
    ("e_b", "p2"),
    ("e_b", "c2"),
    ("e_c", "c1"),
    ("e_c", "c2"),
    ("e_c", "c3"),
]
HUB_PREFIX = "e:"


def _oracle_even_hop_bfs(pairs, limit: int) -> dict[str, dict[str, int]]:
    hub_members: dict[str, set[str]] = {}
    for hub, ent in pairs:
        hub_members.setdefault(hub, set()).add(ent)
    adj: dict[str, set[str]] = {}
    for members in hub_members.values():
        for ent in members:
            adj.setdefault(ent, set()).update(members - {ent})
    max_hops = limit // 2
    out: dict[str, dict[str, int]] = {}
    for src in sorted(adj):
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
        out[src] = {e: d for e, d in dist.items() if e != src}
    return out


@pytest.fixture()
def trail_graph():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE h_incidence (edge_id TEXT, entity_name TEXT)")
    con.executemany(
        "INSERT INTO h_incidence VALUES (?, ?)",
        [(HUB_PREFIX + e, ent) for e, ent in EDGES],
    )
    g = ht.build_bipartite(con)
    con.close()
    return g


def test_chain_census_invariants(trail_graph):
    g = trail_graph
    assert g["arcs"] == 2 * len(set(EDGES))
    assert g["n_hubs"] == 3
    assert g["n_entities"] == 5  # p1,p2,c1,c2,c3
    c = ht.census(g)
    assert c["components"] >= 1
    assert c["entities"] == 5
    assert c["largest_fraction"] > 0.0


def test_chain_trail_distances_match_oracle(trail_graph):
    g = trail_graph
    pairs = [(e, ent) for e, ent in EDGES]
    want = _oracle_even_hop_bfs(pairs, ht.DEFAULT_LIMIT)
    got = ht.trail_distances(g, list(want), limit=ht.DEFAULT_LIMIT)
    assert got == want


def test_chain_trail_source_stats_shape(trail_graph):
    g = trail_graph
    stats = ht.trail_source_stats(g, "p1", limit=ht.DEFAULT_LIMIT)
    assert stats["limit"] == ht.DEFAULT_LIMIT
    assert stats["reachable"] == g["n_entities"] - 1
    assert sum(stats["hop_histogram"].values()) == stats["reachable"]
    assert all(h >= 1 for h in stats["hop_histogram"])


def test_chain_rust_kernel_lane_matches_scipy(trail_graph):
    pytest.importorskip("graph_kernels")
    g = trail_graph
    src_labels = list(g["labels"][: g["n_entities"]])
    batch = ht._trail_stats_kernel_batch(g, src_labels, limit=ht.DEFAULT_LIMIT)
    assert batch is not None
    for label, row in zip(src_labels, batch):
        assert row["limit"] == ht.DEFAULT_LIMIT
        assert row["reachable"] == ht.trail_source_stats(g, label)["reachable"]
        assert row["hop_histogram"] == ht.trail_source_stats(g, label)["hop_histogram"]


def test_chain_csr_bins_write(trail_graph, tmp_path: Path):
    g = trail_graph
    out = tmp_path / "bins"
    manifest = ht.build_bipartite_csr(EDGES, out)
    assert manifest["nodes"] == g["n_entities"] + g["n_hubs"]
    assert manifest["edges"] == len(set(EDGES))
    offsets = (out / "bip_offsets.bin").read_bytes()
    neighbors = (out / "bip_neighbors.bin").read_bytes()
    assert len(offsets) == 4 * (manifest["nodes"] + 1)
    assert len(neighbors) == 4 * manifest["directed_rows"]
    import hashlib

    assert manifest["checksum"] == hashlib.sha256(offsets + neighbors).hexdigest()
    assert (out / "bip_names.json").exists()
