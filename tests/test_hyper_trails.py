"""Hermetic tests for the repo-native bipartite BFS trail lane
(helpers/graph/hyper_trails.py, proposal
hyper_trail_bipartite_bfs_lane).

Ground truth is networkx shortest paths over the same bipartite
incidence graph: trail hops == bipartite distance / 2, even steps
only. Toy duckdb fixtures cover the DISTINCT/absence SQL paths; no
live store.
"""

from __future__ import annotations

import sys
from pathlib import Path

import json
import os

import duckdb
import networkx as nx
import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from helpers.graph import hyper_trails as ht  # noqa: E402
from helpers.graph.hyper_trails import (  # noqa: E402
    bipartite_from_pairs,
    build_bipartite,
    census,
    trail_distances,
    trail_source_stats,
    trail_source_stats_parallel,
)

CHAIN3 = [("e:1", "a"), ("e:1", "b"), ("e:2", "b"), ("e:2", "c"), ("e:3", "c"), ("e:3", "d")]
STAR = [("e:9", "a"), ("e:9", "b"), ("e:9", "c")]
SPLIT = [("e:1", "a"), ("e:1", "b"), ("e:2", "c"), ("e:2", "d")]
CHAIN5 = [
    ("e:1", "a"),
    ("e:1", "b"),
    ("e:2", "b"),
    ("e:2", "c"),
    ("e:3", "c"),
    ("e:3", "d"),
    ("e:4", "d"),
    ("e:4", "e"),
    ("e:5", "e"),
    ("e:5", "f"),
]


def _nx_hops(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """Ground truth: networkx shortest path over the same bipartite graph.

    Entity-entity hop count = nx shortest path length // 2, odd lengths
    (mid-hop) excluded — mirrors _trail_bfs exactly.
    """
    g = nx.Graph()
    g.add_edges_from(pairs)
    ents = sorted({e for _, e in pairs})
    out: dict[tuple[str, str], int] = {}
    for i, s in enumerate(ents):
        for t in ents[i + 1 :]:
            d = nx.shortest_path_length(g, s, t)
            if d % 2 == 0:
                out[(s, t)] = d // 2
    return out


def _toy_con(rows: list[tuple[int, str]]):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE h_incidence(edge_id INTEGER, entity_name VARCHAR)")
    con.executemany("INSERT INTO h_incidence VALUES (?, ?)", rows)
    return con


def test_chain_distances_match_networkx_zero_mismatches():
    g = bipartite_from_pairs(CHAIN3)
    got = trail_distances(g, ["a", "b", "c", "d"])
    truth = _nx_hops(CHAIN3)
    mismatches = [
        (pair, truth[pair], got[s].get(t))
        for pair in truth
        for s, t in [pair]
        if got[s].get(t) != truth[pair]
    ]
    assert mismatches == []
    assert got["a"]["d"] == 3  # a-e1-b-e2-c-e3-d: 6 bipartite steps = 3 hops


def test_star_pairwise_one_hop():
    g = bipartite_from_pairs(STAR)
    got = trail_distances(g, ["a", "b", "c"])
    assert got["a"]["b"] == 1 and got["a"]["c"] == 1 and got["b"]["c"] == 1


def test_arcs_guard_two_x_distinct_pairs():
    # duplicate incidence (b inside e:1 twice) collapses at DISTINCT
    dup = CHAIN3 + [("e:1", "b")]
    g = bipartite_from_pairs(dup)
    assert g["pairs"] == len(set(dup))
    assert g["arcs"] == 2 * g["pairs"]  # a silent clique growth is impossible


def test_limit_truncates_and_semantics():
    g = bipartite_from_pairs(CHAIN5)
    full = trail_distances(g, ["a"])  # default limit 40 reaches f (10 steps)
    assert full["a"]["f"] == 5
    cut = trail_distances(g, ["a"], limit=8)  # 8 steps = 4 hops: f is out
    assert "f" not in cut["a"]
    assert cut["a"]["e"] == 4
    odd = trail_distances(g, ["a"], limit=1)  # only hub spokes inside 1 step
    assert odd["a"] == {}  # odd bipartite distances end mid-hop: dropped


def test_stats_report_limit():
    g = bipartite_from_pairs(STAR)
    st = trail_source_stats(g, "a")
    assert st["limit"] == 40  # outputs are lower bounds: the limit travels
    assert st["reachable"] == 2


def test_absent_store_degrades_none():
    con = duckdb.connect(":memory:")  # no h_incidence table
    assert build_bipartite(con) is None


def test_build_from_store_distinct_pairs():
    con = _toy_con([(1, "a"), (1, "b"), (2, "b"), (2, "c"), (1, "b")])  # dup row
    g = build_bipartite(con)
    assert g["pairs"] == 4
    assert g["arcs"] == 8
    got = trail_distances(g, ["a"])
    assert got["a"]["c"] == 2  # a-e1-b-e2-c


def test_unknown_source_raises():
    g = bipartite_from_pairs(STAR)
    with pytest.raises(ValueError):
        trail_distances(g, ["nope"])


def test_census_two_components():
    g = bipartite_from_pairs(SPLIT)
    c = census(g)
    assert c["components"] == 2
    assert c["entities"] == 4
    assert c["hubs"] == 2
    assert c["largest_component_entities"] == 2
    assert c["largest_fraction"] == 0.5


def test_census_single_giant_component():
    g = bipartite_from_pairs(CHAIN5)
    c = census(g)
    assert c["components"] == 1
    assert c["largest_fraction"] == 1.0


def test_hub_prefix_no_collision():
    # an entity literally named like a hub stays a separate node
    pairs = [("e:1", "a"), ("e:1", "e:1")]
    g = bipartite_from_pairs(pairs)
    assert g["n_entities"] == 2 and g["n_hubs"] == 1
    assert g["arcs"] == 4


def test_live_store_smoke_guard():
    """The one live-store touch: build + census only (no per-source work).

    Skipped when the star store is absent — degrades, never blocks.
    """
    g = build_bipartite()
    if g is None:
        pytest.skip("h_incidence absent (star store not built)")
    c = census(g)
    assert c["arcs"] == 2 * g["pairs"]
    assert c["entities"] == g["n_entities"]


def test_self_is_excluded():
    g = bipartite_from_pairs(STAR)
    got = trail_distances(g, ["a"])
    assert "a" not in got["a"]


def test_odd_limit_mid_hop_boundary():
    g = bipartite_from_pairs(CHAIN3)
    # limit=5: a->d needs 6 steps; a->c needs 4. 5-step cutoff keeps c, drops d
    got = trail_distances(g, ["a"], limit=5)
    assert got["a"]["c"] == 2
    assert "d" not in got["a"]


def test_parallel_equals_serial_chain5():
    """Wall-2 S1: process-pool per-source stats must match serial exactly."""
    g = bipartite_from_pairs(CHAIN5)
    srcs = ["a", "b", "c", "d", "e", "f"]
    serial = [trail_source_stats(g, s) for s in srcs]
    parallel = trail_source_stats_parallel(g, srcs, workers=2)
    assert len(parallel) == len(serial)
    for s, par in zip(serial, parallel):
        # ms timing will legitimately differ; compare the structural payload
        assert par["reachable"] == s["reachable"]
        assert par["hop_histogram"] == s["hop_histogram"]
        assert par["limit"] == s["limit"]


def test_parallel_fallback_when_single_source():
    g = bipartite_from_pairs(STAR)
    got = trail_source_stats_parallel(g, ["a"], workers=4)
    assert got[0]["reachable"] == 2


def test_pool_spawns_with_capped_workers(monkeypatch: pytest.MonkeyPatch):
    """Goldfish-trial tooth (a): the multicore path must actually SPAWN a
    pool — not silently degrade to the serial loop — and the spawn is
    capped at min(workers, len(sources))."""
    import multiprocessing as mp

    spawned: list[int] = []
    real_pool = mp.Pool  # a bound context method — wrap it, never subclass

    class _SpyPool:
        def __init__(self, *a, **k):
            spawned.append(k.get("processes"))
            self._pool = real_pool(*a, **k)

        def __enter__(self):
            self._pool.__enter__()
            return self

        def __exit__(self, *exc):
            return self._pool.__exit__(*exc)

        def map(self, fn, iterable):
            return self._pool.map(fn, iterable)

    monkeypatch.setattr(mp, "Pool", _SpyPool)
    g = bipartite_from_pairs(CHAIN5)
    srcs = ["a", "b", "c", "d", "e", "f"]
    serial = [trail_source_stats(g, s) for s in srcs]
    got = trail_source_stats_parallel(g, srcs, workers=2)
    assert spawned == [2], "pool must spawn exactly min(workers, len(sources)) processes"
    for s, par in zip(serial, got):
        assert par["reachable"] == s["reachable"]
        assert par["hop_histogram"] == s["hop_histogram"]


def test_pool_start_failure_prints_stderr_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Goldfish-trial tooth (b): when the pool cannot start, the fallback
    to serial must NOT be silent — one stderr line carries the reason, and
    results still equal the serial oracle."""
    import multiprocessing as mp

    class _Boom:
        def __init__(self, *a, **k):
            raise OSError("pool start failed (simulated no-fork)")

    monkeypatch.setattr(mp, "Pool", _Boom)
    g = bipartite_from_pairs(CHAIN5)
    srcs = ["a", "b", "c", "d", "e", "f"]
    serial = [trail_source_stats(g, s) for s in srcs]
    got = trail_source_stats_parallel(g, srcs, workers=2)
    err = capsys.readouterr().err
    assert "trail parallel pool unavailable" in err
    assert "falling back to serial" in err
    assert "pool start failed (simulated no-fork)" in err
    for s, par in zip(serial, got):
        assert par["reachable"] == s["reachable"]
        assert par["hop_histogram"] == s["hop_histogram"]


class TestMojoBipartiteBfsParity:
    """S3: Wall 2 parity gate — the bfs_csr.mojo binary run on the
    bipartite CSR bins (build_bipartite_csr) must agree with the Python
    trail oracle (trail_distances) on reachability and hops.

    A trail hop == bipartite distance / 2 (even steps only), so the
    Mojo "hops H" line (bipartite steps) must be 2 * oracle_hops, and
    unreachable pairs must exit 2. Skips when the binary is not built
    (make mojo-build); MOJO_BFS_PARITY=1 additionally runs a seeded
    random-graph leg.
    """

    BINARY = _REPO / "Mojo" / "bin" / "bfs_csr"

    @staticmethod
    def _run(binary, out, src: int, dst: int, max_steps: int):
        import os
        import subprocess

        n = len(json.loads((out / "bip_names.json").read_text()))
        m = int((out / "bip_neighbors.bin").stat().st_size // 4)
        venv_bin = _REPO / ".venv" / "bin"
        env = dict(os.environ, PATH=f"{venv_bin}:{os.environ.get('PATH', '')}")
        return subprocess.run(
            [
                str(binary),
                str(out / "bip_offsets.bin"),
                str(out / "bip_neighbors.bin"),
                str(n),
                str(m),
                str(src),
                str(dst),
                str(max_steps),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )

    @staticmethod
    def _oracle_hops(g, src_label: str, dst_label: str, limit: int):
        dist = trail_distances(g, [src_label], limit=limit)[src_label]
        return dist.get(dst_label)

    def test_parity_toy(self, tmp_path: Path):
        if not self.BINARY.exists():
            pytest.skip("bfs_csr binary not built (make mojo-build)")
        out = tmp_path / "bip_toy"
        ht.build_bipartite_csr(CHAIN3, out)
        g = bipartite_from_pairs(CHAIN3)
        ents = g["labels"][: g["n_entities"]]
        index = {name: i for i, name in enumerate(ents)}
        for s, t, steps in (
            ("a", "d", 6),  # a-b-c-d via e1,e2,e3 = 3 hops
            ("a", "c", 4),  # 2 hops
            ("a", "b", 2),  # 1 hop
        ):
            oracle = self._oracle_hops(g, s, t, limit=40)
            proc = self._run(self.BINARY, out, index[s], index[t], steps)
            if oracle is None:
                assert proc.returncode == 2, (s, t, proc.stdout)
                assert "UNREACHABLE" in proc.stdout
            else:
                assert proc.returncode == 0, (s, t, proc.stderr)
                hops_line = [ln for ln in proc.stdout.splitlines() if ln.startswith("hops ")]
                bipartite_steps = int(hops_line[0].split()[1])
                assert bipartite_steps == oracle * 2, (s, t, bipartite_steps, oracle)

        # src == dst: the Mojo binary returns hops 0 (path of length 0),
        # while the trail oracle excludes self — both are the same fact.
        self_proc = self._run(self.BINARY, out, index["a"], index["a"], 0)
        assert self_proc.returncode == 0
        assert "hops 0" in self_proc.stdout

    def test_parity_random_gated(self, tmp_path: Path):
        if not self.BINARY.exists():
            pytest.skip("bfs_csr binary not built (make mojo-build)")
        if os.environ.get("MOJO_BFS_PARITY") != "1":
            pytest.skip("MOJO_BFS_PARITY=1 not set")
        import random as _random

        rng = _random.Random(21)
        n_ent, n_hub = 30, 15
        pairs = set()
        for h in range(n_hub):
            for _ in range(rng.randint(2, 5)):
                pairs.add((f"e:{h}", f"n{rng.randrange(n_ent)}"))
        pairs = sorted(pairs)
        out = tmp_path / "bip_rand"
        ht.build_bipartite_csr(pairs, out)
        g = bipartite_from_pairs(pairs)
        ents = g["labels"][: g["n_entities"]]
        index = {name: i for i, name in enumerate(ents)}
        for _ in range(25):
            s, t = rng.choice(ents), rng.choice(ents)
            if s == t:
                self_proc = self._run(self.BINARY, out, index[s], index[t], 40)
                assert self_proc.returncode == 0
                assert "hops 0" in self_proc.stdout
                continue
            oracle = self._oracle_hops(g, s, t, limit=40)
            proc = self._run(self.BINARY, out, index[s], index[t], 40)
            if oracle is None:
                assert proc.returncode == 2, (s, t, proc.stdout)
            else:
                assert proc.returncode == 0, (s, t, proc.stderr)
                hops_line = [ln for ln in proc.stdout.splitlines() if ln.startswith("hops ")]
                bipartite_steps = int(hops_line[0].split()[1])
                assert bipartite_steps == oracle * 2, (s, t, bipartite_steps, oracle)


class TestMojoTrailDispatch:
    """S3 promotion: the MOJO_TRAIL_BFS gate routes _trail_bfs through the
    Mojo kernel and must return identical trail hops to scipy."""

    def test_dispatch_mojo_equals_scipy(self):
        import os

        pairs = [("e:1", "a"), ("e:1", "b"), ("e:2", "b"), ("e:2", "c"), ("e:3", "c"), ("e:3", "d")]
        binary = _REPO / "Mojo" / "bin" / "bfs_bipartite_all"
        if not binary.exists():
            pytest.skip("bfs_bipartite_all not built (make mojo-build)")
        os.environ["MOJO_TRAIL_BFS"] = "1"
        g = bipartite_from_pairs(pairs)
        res_mojo = {
            lbl: trail_distances(g, [lbl], limit=40)[lbl] for lbl in g["labels"][: g["n_entities"]]
        }
        os.environ["MOJO_TRAIL_BFS"] = "0"
        g2 = bipartite_from_pairs(pairs)
        res_scipy = {
            lbl: trail_distances(g2, [lbl], limit=40)[lbl]
            for lbl in g2["labels"][: g2["n_entities"]]
        }
        os.environ["MOJO_TRAIL_BFS"] = "0"
        assert res_mojo == res_scipy
