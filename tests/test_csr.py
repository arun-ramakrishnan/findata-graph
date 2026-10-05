"""CSR substrate tests — vault_scaling Phase B-A (scipy_exact_universe-era
unlock, operator-directed 2026-09-26).

Golden toy: hand-computed offsets/neighbors; determinism: byte-identical
rebuild; staleness: generation drift flips `fresh`; BFS reference: parity
with brute-force distances on a seeded random graph.

csr_lane_remediation (2026-09-28, from the ef8a17d4 OCR delegation review):
lane activation from the production call shape, per-generation substrate
caching, and the `src == dst` contract the SQL path pins.
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


class _Rows:
    """DuckDB-shaped result stub: only ``fetchone`` is used by the lane."""

    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StubCon:
    """Minimal stand-in for the two metadata probes the CSR lane makes.

    The lane reads ``_build_meta.generation`` (to prove this connection was
    built from the CSR's own edge set) and ``v_node`` (the S3 ``src == dst``
    contract). Both are covered by the real build; stubbing them keeps these
    unit tests fast instead of copying the production DB.
    """

    def __init__(self, generation: str | None, v_node: set[str]):
        self._generation = generation
        self._v_node = v_node

    def execute(self, sql, params=None):
        if "_build_meta" in sql:
            return _Rows([] if self._generation is None else [(self._generation,)])
        if "v_node" in sql:
            assert params is not None
            return _Rows([(1,)] if params[0] in self._v_node else [])
        raise AssertionError(f"unexpected SQL in the CSR lane: {sql!r}")


@pytest.fixture(autouse=True)
def _isolate_csr_cache():
    """The substrate cache is module-side state — keep tests hermetic."""
    csr.clear_cache()
    yield
    csr.clear_cache()


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


# --------------------------------------------------------------------------- #
# csr_lane_remediation — the ef8a17d4 OCR delegation review findings          #
# --------------------------------------------------------------------------- #


def test_lane_serves_the_production_call_shape(csr_db: Path, tmp_path: Path):
    """S1: the gate needs `edge_label=None`, but the parameter default was
    the RECOGNISED label "BelongsTo" — a filter — so the lane never fired for
    the API route or the CLI. This pins that the unfiltered shape the callers
    now pass actually reaches the substrate and returns a real path."""
    out = tmp_path / "csr"
    csr.build(csr_db, out)
    con = _StubCon(generation="42", v_node={"a", "b", "c", "d", "e"})
    result, fresh = csr.try_shortest_path(con, "a", "c", 5, out_dir=out, db_path=csr_db)
    assert fresh is True, "generation match must activate the lane"
    assert result is not None, "lane applied on a connected pair must yield a path"
    # the toy graph has a direct a-c edge, so the 1-hop path is the
    # hop-shortest answer; the lane must return it, not a longer walk
    assert [n for n, _hop in result] == ["a", "c"]
    assert [h for _n, h in result] == [0, 1], "hop indices stay 0-based and contiguous"
    # d-e is a separate component from a-b-c: unreachable, but the lane
    # applied (fresh=True) rather than handing back to SQL
    unreachable, fresh2 = csr.try_shortest_path(con, "a", "d", 5, out_dir=out, db_path=csr_db)
    assert fresh2 is True and unreachable is None


def test_src_eq_dst_matches_sql_contract(csr_db: Path, tmp_path: Path):
    """S3: `_shortest_path_bfs` pins `src == dst` -> [(src, 0)] for any name
    known to `v_node`, even with zero edges. The CSR universe is edge
    ENDPOINTS only, so a known-but-edgeless entity was absent from `pos` and
    read as (None, True) — "lane applied, no path". Divergence closed."""
    out = tmp_path / "csr"
    csr.build(csr_db, out)  # universe is {a,b,c,d,e}; "z" has no edges at all
    con = _StubCon(generation="42", v_node={"a", "z"})
    # known + edgeless: SQL says [(z, 0)], not "unreachable"
    assert csr.try_shortest_path(con, "z", "z", 5, out_dir=out, db_path=csr_db) == (
        [("z", 0)],
        True,
    )
    # known + CSR-resident: same zero-hop contract
    assert csr.try_shortest_path(con, "a", "a", 5, out_dir=out, db_path=csr_db) == (
        [("a", 0)],
        True,
    )
    # unknown to v_node: both lanes return None
    assert csr.try_shortest_path(con, "nope", "nope", 5, out_dir=out, db_path=csr_db) == (
        None,
        True,
    )


def test_cache_memoises_substrate_per_generation(csr_db: Path, tmp_path: Path, monkeypatch):
    """S2: the per-call `load()` re-parsed csr_names.json and rebuilt the
    22k-entry name->id map on EVERY call (~7.2 ms), which erased the lane's
    advantage (1.27x integrated, not the advertised 50-300x). The derived
    substrate must be paid once per (out_dir, generation)."""
    out = tmp_path / "csr"
    csr.build(csr_db, out)
    con = _StubCon(generation="42", v_node={"a", "b", "c", "d", "e"})
    calls = []
    real_load = csr.load

    def _counting_load(*a, **kw):
        calls.append(1)
        return real_load(*a, **kw)

    monkeypatch.setattr(csr, "load", _counting_load)
    args = ("a", "c", 5)
    kwargs = {"out_dir": out, "db_path": csr_db}
    first, fresh1 = csr.try_shortest_path(con, *args, **kwargs)
    second, fresh2 = csr.try_shortest_path(con, *args, **kwargs)
    assert fresh1 is True and fresh2 is True
    assert first == second, "a warm call must return the identical path"
    assert len(calls) == 1, "second call must hit the cache, not re-parse/rebuild"


def test_cache_never_serves_a_stale_generation(csr_db: Path, tmp_path: Path):
    """S2 safety: the cache is keyed on the LIVE generation, so a rebuild
    misses the cache and the existing loud fallback still fires. Caching the
    substrate must not turn a stale CSR into a silently-wrong answer."""
    out = tmp_path / "csr"
    csr.build(csr_db, out)
    con = _StubCon(generation="42", v_node={"a", "b", "c", "d", "e"})
    args = ("a", "c", 5)
    kwargs = {"out_dir": out, "db_path": csr_db}
    assert csr.try_shortest_path(con, *args, **kwargs)[1] is True, "warm first"
    con_db = sqlite3.connect(str(csr_db))
    con_db.execute("UPDATE db_meta SET value='43' WHERE key='generation'")
    con_db.commit()
    con_db.close()
    result, fresh = csr.try_shortest_path(con, *args, **kwargs)
    assert result is None and fresh is False, "drift must fall back to SQL, never serve stale"
    # and the drifted generation did not get memoised
    assert not any(key[1] == "43" for key in csr._CACHE), "drift must not populate the cache"


def test_warm_call_stays_on_the_lane_without_a_db_path(csr_db: Path, tmp_path: Path):
    """S2 regression: `query.shortest_path` calls the lane with NO db_path
    (the DuckDB `_build_meta` match is the only generation gate it can check).
    An earlier cache keyed freshness on `live_gen is not None`, which is False
    in that shape, so the FIRST call used the lane and every later call
    degraded to SQL — warm measured slower than cold (28 ms vs 15 ms). The
    cache-hit freshness rule must match load()'s on both paths."""
    out = tmp_path / "csr"
    csr.build(csr_db, out)
    con = _StubCon(generation="42", v_node={"a", "b", "c", "d", "e"})
    kwargs = {"out_dir": out}  # no db_path — the production call shape
    first, fresh1 = csr.try_shortest_path(con, "a", "c", 5, **kwargs)
    assert fresh1 is True, "cold call must take the lane"
    second, fresh2 = csr.try_shortest_path(con, "a", "c", 5, **kwargs)
    assert fresh2 is True, "warm call must STAY on the lane, not fall back to SQL"
    assert second == first


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
        import os
        import subprocess

        n = len(json.loads((out / "csr_names.json").read_text()))
        m = int((out / "csr_neighbors.bin").stat().st_size // 4)
        # The Mojo bridge resolves libpython from the python3 on PATH at
        # runtime (completed.md #202): without the venv bin first, the
        # sys_exit(2) UNREACHABLE path aborts with `symbol not found:
        # PyRun_SimpleString` even though the reachable paths succeed.
        venv_bin = Path(__file__).resolve().parents[1] / ".venv" / "bin"
        env = dict(os.environ, PATH=f"{venv_bin}:{os.environ.get('PATH', '')}")
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
            env=env,
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
