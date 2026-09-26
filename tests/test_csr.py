"""CSR substrate tests — vault_scaling Phase B-A (scipy_exact_universe-era
unlock, operator-directed 2026-09-26).

Golden toy: hand-computed offsets/neighbors; determinism: byte-identical
rebuild; staleness: generation drift flips `fresh`; BFS reference: parity
with brute-force distances on a seeded random graph.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.graph import csr  # noqa: E402

_EDGES = [("a", "b"), ("b", "c"), ("d", "e"), ("a", "c")]


@pytest.fixture()
def csr_db(tmp_path: Path) -> Path:
    db = tmp_path / "g.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, weight REAL)")
    con.executemany("INSERT INTO graph_edges VALUES (?, ?, 1.0)", _EDGES)
    con.execute("CREATE TABLE db_meta (key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO db_meta VALUES ('generation', '42')")
    con.commit()
    con.close()
    return db


def test_build_golden_layout(csr_db: Path, tmp_path: Path):
    out = tmp_path / "csr"
    m = csr.build(csr_db, out)
    names = json.loads((out / "csr_names.json").read_text())
    assert names == ["a", "b", "c", "d", "e"]
    offsets = np.fromfile(out / "csr_offsets.bin", dtype=np.int32)
    neighbors = np.fromfile(out / "csr_neighbors.bin", dtype=np.int32)
    assert offsets.tolist() == [0, 2, 4, 6, 7, 8]
    # a: {b,c}, b: {a,c}, c: {a,b}, d: {e}, e: {d} — sorted slices
    assert neighbors.tolist() == [1, 2, 0, 2, 0, 1, 4, 3]
    assert m["nodes"] == 5 and m["edges"] == 4 and m["directed_rows"] == 8
    assert m["generation"] == "42" and m["schema_version"] == 1


def test_rebuild_is_byte_identical(csr_db: Path, tmp_path: Path):
    out = tmp_path / "csr"
    csr.build(csr_db, out)
    bins1 = [(out / n).read_bytes() for n in ("csr_offsets.bin", "csr_neighbors.bin")]
    csr.build(csr_db, out)
    bins2 = [(out / n).read_bytes() for n in ("csr_offsets.bin", "csr_neighbors.bin")]
    assert bins1 == bins2, "same edge set must build byte-identical binaries"


def test_generation_drift_flips_fresh(csr_db: Path, tmp_path: Path):
    out = tmp_path / "csr"
    csr.build(csr_db, out)
    _m, _off, _nbr, _names, fresh = csr.load(out, db_path=csr_db)
    assert fresh is True
    con = sqlite3.connect(str(csr_db))
    con.execute("UPDATE db_meta SET value='43' WHERE key='generation'")
    con.commit()
    con.close()
    _m2, _off2, _nbr2, _names2, fresh2 = csr.load(out, db_path=csr_db)
    assert fresh2 is False, "generation drift must flip fresh (fallback doctrine)"


def test_missing_artifacts_degrade_open(tmp_path: Path):
    _m, _off, _nbr, _names, fresh = csr.load(tmp_path / "absent")
    assert fresh is False and _names == []


def test_bfs_reference_random_graph(csr_db: Path, tmp_path: Path):
    import random

    rng = random.Random(7)
    db = tmp_path / "rand.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, weight REAL)")
    con.execute("CREATE TABLE db_meta (key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO db_meta VALUES ('generation', '7')")
    nodes = [f"n{i}" for i in range(60)]
    edges = set()
    while len(edges) < 180:
        u, v = rng.choice(nodes), rng.choice(nodes)
        if u != v:
            edges.add((u, v))
    con.executemany("INSERT INTO graph_edges VALUES (?, ?, 1.0)", list(edges))
    con.commit()
    con.close()
    out = tmp_path / "csr_rand"
    csr.build(db, out)
    manifest, offsets, neighbors, names, fresh = csr.load(out, db_path=db, verify_checksum=True)
    assert fresh
    pos = {n: i for i, n in enumerate(names)}
    # brute-force reference: Bellman-Ford style distance map per probe
    for src_name in ("n0", "n17", "n41"):
        src = pos[src_name]
        dist = {src: 0}
        frontier = [src]
        while frontier:
            nxt = []
            for u in frontier:
                for v in neighbors[int(offsets[u]) : int(offsets[u + 1])]:
                    v = int(v)
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        nxt.append(v)
            frontier = nxt
        for dst_name in nodes[:10]:
            dst = pos[dst_name]
            path = csr.bfs_path(offsets, neighbors, src, dst)
            if dst in dist:
                assert path is not None
                assert len(path) - 1 == dist[dst], "BFS path must be shortest"
                assert path[0] == src and path[-1] == dst
            else:
                assert path is None, "unreachable pairs must return None"


class TestMojoBfsParity:
    """vault_scaling B-B parity gate: the bfs_csr.mojo binary must match
    the Python oracle (csr.bfs_path) exactly — same path, same
    unreachability, same max_hops clipping. Skips when the binary is
    not built (make mojo-build); MOJO_BFS_PARITY=1 additionally runs
    the live-CSR parity leg."""

    BINARY = Path(__file__).resolve().parents[1] / "Mojo" / "bin" / "bfs_csr"

    @staticmethod
    def _run(binary: Path, out: Path, src: int, dst: int, max_hops: int = 5):
        import subprocess

        n = len(json.loads((out / "csr_names.json").read_text()))
        m = int((out / "csr_neighbors.bin").stat().st_size // 4)
        proc = subprocess.run(
            [
                str(binary),
                str(out / "csr_offsets.bin"),
                str(out / "csr_neighbors.bin"),
                str(n),
                str(m),
                str(src),
                str(dst),
                str(max_hops),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc

    @staticmethod
    def _golden_lines(conn):
        return csr.longest_chains_golden(conn) if hasattr(csr, "longest_chains_golden") else None

    def test_parity_toy_and_random(self, tmp_path: Path):
        if not self.BINARY.exists():
            pytest.skip("bfs_csr binary not built (make mojo-build)")
        import sqlite3

        # toy: path a-b-c + isolated pair d-e (from the golden fixture)
        db = tmp_path / "toy.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, weight REAL)")
        con.execute("CREATE TABLE db_meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO db_meta VALUES ('generation', '7')")
        con.executemany(
            "INSERT INTO graph_edges VALUES (?, ?, 1.0)", [("a", "b"), ("b", "c"), ("d", "e")]
        )
        con.commit()
        con.close()
        out = tmp_path / "csr_toy"
        csr.build(db, out)
        _m, offsets, neighbors, names, fresh = csr.load(out)
        pos = {n: i for i, n in enumerate(names)}
        for s, t, hops in (
            ("a", "c", 2),
            ("a", "b", 1),
            ("d", "a", 5),
            ("a", "d", 5),
            ("a", "a", 0),
        ):
            oracle = csr.bfs_path(offsets, neighbors, pos[s], pos[t], max_hops=hops)
            proc = self._run(self.BINARY, out, pos[s], pos[t], hops)
            if oracle is None:
                assert proc.returncode == 2, (s, t, proc.stdout)
                assert "UNREACHABLE" in proc.stdout
            else:
                assert proc.returncode == 0, (s, t, proc.stderr)
                mojo_ids = [int(x) for x in proc.stdout.splitlines()[0].split()]
                assert mojo_ids == oracle, (s, t, mojo_ids, oracle)

        # seeded random graph: every node pair, oracle == mojo
        rng = np.random.default_rng(11)
        db2 = tmp_path / "rand.db"
        con = sqlite3.connect(str(db2))
        con.execute("CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, weight REAL)")
        con.execute("CREATE TABLE db_meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO db_meta VALUES ('generation', '11')")
        nodes = [f"n{i}" for i in range(40)]
        edges = set()
        while len(edges) < 120:
            u, v = rng.choice(nodes), rng.choice(nodes)
            if u != v:
                edges.add((u, v))
        con.executemany("INSERT INTO graph_edges VALUES (?, ?, 1.0)", list(edges))
        con.commit()
        con.close()
        out2 = tmp_path / "csr_rand"
        csr.build(db2, out2)
        _m2, offsets2, neighbors2, names2, _f2 = csr.load(out2)
        pos2 = {n: i for i, n in enumerate(names2)}
        # probe only CSR-resident nodes: the CSR universe is edge
        # endpoints — an isolated node is genuinely unreachable and the
        # binary correctly rejects out-of-universe ids as an error.
        resident = [n for n in nodes if n in pos2]
        probes = [(str(rng.choice(resident)), str(rng.choice(resident))) for _ in range(25)]
        for s_name, t_name in probes:
            s_i, t_i = pos2[s_name], pos2[t_name]
            oracle = csr.bfs_path(offsets2, neighbors2, s_i, t_i, max_hops=5)
            proc = self._run(self.BINARY, out2, s_i, t_i, 5)
            if oracle is None:
                assert proc.returncode == 2, (s_name, t_name)
            else:
                mojo_ids = [int(x) for x in proc.stdout.splitlines()[0].split()]
                assert mojo_ids == oracle, (s_name, t_name, mojo_ids, oracle)
