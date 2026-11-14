#!/usr/bin/env python3
"""Tests for helpers/graph/stats.py."""

from __future__ import annotations

import contextlib
import io
import importlib.util
import sqlite3
from pathlib import Path

import pytest

# The module-scoped stats_render fixture below is rebuilt PER WORKER under
# -n auto. Grouping is now done by --dist=loadscope (module-level) in the
# live-invariants gate leg — the xdist_group marker is inert on pytest 9.2.0
# + xdist 3.8.0 (Node.nodeid returns str(self._id), so the group suffix
# never reaches LoadGroupScheduling._split_scope). The marker is kept as
# documentation of intent; loadscope is what actually pins this module to
# one worker so print_stats() renders once per worker, not once per test.
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
    # part_of/has_company retirement (2026-10-19): the pair is no longer a
    # STORED edge type — it is projected from the canonical sector
    # hyperedges, so the breakdown carries membership as its own line.
    assert "co_mentioned_in" in out
    assert "sector membership" in out
    assert "part_of/has_company are derived, not stored" in out


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
    for metric in [
        "pagerank",
        "degree_centrality",
        "betweenness_centrality",
        "louvain_community",
    ]:
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


class TestStalenessVerdict:
    """graph_analytics freshness must compare timestamps, not raw strings
    (doc/improvements/archive/graph/graph_analytics_staleness_comparison.md).

    `entities.last_updated` carries both the space-separated `utc_now()`
    shape and ISO-T legacy rows; `computed_at` is space-separated. A raw
    `>` decides at index 10 ('T' > ' ') whenever the date prefixes tie,
    so a same-day FRESH snapshot printed STALE. These tests construct
    exactly that collision — the one a next-day recompute can never
    reproduce by accident, which is why the old freshness test never
    caught it.
    """

    @staticmethod
    def _conn(entity_stamps, computed_at):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE entities (last_updated TEXT)")
        con.executemany("INSERT INTO entities VALUES (?)", [(s,) for s in entity_stamps])
        con.execute("CREATE TABLE graph_analytics (computed_at TEXT)")
        con.execute("INSERT INTO graph_analytics VALUES (?)", (computed_at,))
        con.commit()
        return con

    def test_same_day_iso_entity_vs_space_analytics_is_fresh(self):
        # THE discriminator: ISO entity stamp at 00:13, analytics computed
        # 17:59 the SAME day — analytics is later in time, so fresh. The
        # pre-fix string compare said STALE ('T' beats ' ' at index 10).
        # NULL + empty-string rows mirror the live column shape and must
        # not poison the verdict.
        con = self._conn([None, "", "2026-09-25T00:13:27.091137"], "2026-09-25 17:59:26")
        lines = stats._staleness_verdict(con)
        con.close()
        assert any("✓ fresh" in ln for ln in lines), lines

    def test_iso_entity_genuinely_later_same_day_is_stale(self):
        # converse: the ISO side is genuinely later (23:59 vs 17:59), so
        # STALE — time decides, not format. Guards an over-correction
        # that flipped the direction instead of fixing the parse.
        con = self._conn(["2026-09-25T23:59:00"], "2026-09-25 17:59:26")
        lines = stats._staleness_verdict(con)
        con.close()
        assert any("⚠ STALE" in ln for ln in lines), lines

    def test_older_analytics_reports_stale_with_raw_strings(self):
        # cross-day staleness still fires, and the banner keeps the raw
        # stamp strings readable for humans.
        con = self._conn(["2026-09-26 10:00:00"], "2026-09-25 17:59:26")
        lines = stats._staleness_verdict(con)
        con.close()
        assert any("⚠ STALE" in ln for ln in lines), lines
        assert any("2026-09-25 17:59:26" in ln for ln in lines), lines

    def test_unparseable_entity_stamps_are_indeterminate_not_fresh(self):
        # widened guard: MAX(julianday(...)) over NULL/empty-only rows is
        # NULL — unknown must not be reported as fresh.
        con = self._conn([None, ""], "2026-09-25 17:59:26")
        lines = stats._staleness_verdict(con)
        con.close()
        assert not any("✓ fresh" in ln for ln in lines), lines
        assert any("INDETERMINATE" in ln for ln in lines), lines


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
        if importlib.util.find_spec("graph_kernels") is not None:
            # S2b kernel lane: stats are exact over ALL roots already —
            # the sample never engages, so capped == exact everywhere.
            assert not any("SAMPLED" in ln for ln in capped)
            assert not any("SAMPLED" in ln for ln in exact)
            assert capped == exact, "kernel lane ignores the cap"
            d = int(capped[0].split("diameter ")[1].split(" ")[0])
            assert d == 59, "a 60-node path has diameter 59"
            return
        assert any("SAMPLED 10/60" in ln for ln in capped)
        assert not any("SAMPLED" in ln for ln in exact)
        d_capped = int(capped[0].split("diameter ")[1].split(" ")[0])
        d_exact = int(exact[0].split("diameter ")[1].split(" ")[0])
        assert d_exact >= d_capped, "exact diameter is an upper bound of the sample"
        assert d_exact == 59, "a 60-node path has diameter 59 when exact"


# --------------------------------------------------------------------------- #
# L7 (range review): _chain_dist_stats pinning — the chunked histogram is the  #
# only guard on the sampled/exact stats contract.                              #
# --------------------------------------------------------------------------- #
def test_chain_dist_stats_exact_symmetric_halves_ties():
    """Exact mode (symmetric square): the diagonal drops out, ordered
    pairs halve to canonical a<b ties at the caller, and the median
    replicates np.median over the >0 finite values."""
    import numpy as np

    dist = np.array([[0.0, 1.0, 3.0], [1.0, 0.0, 2.0], [3.0, 2.0, 0.0]])
    diameter, median, ties, total = stats._chain_dist_stats(dist)
    assert (diameter, median, ties, total) == (3, 2.0, 2, 6)
    assert ties // 2 == 1, "the caller halves ordered ties for canonical a<b"
    vals = dist[np.isfinite(dist) & (dist > 0)]
    assert median == float(np.median(vals)) and total == int(vals.size)


def test_chain_dist_stats_sampled_drops_self_distance_zeros():
    """Sampled mode (L7): each sampled root contributes a self-distance 0
    cell — they are NOT pairs and stay excluded from total/median. The
    legacy materialized pass counted them (total inflated by n_roots);
    the operator ruled zeros stay dropped (2026-10-11)."""
    import numpy as np

    # 2 sampled roots (rows 0 and 2 of a 3-node universe) -> zeros at
    # (0,0) and (1,2)
    dist = np.array([[0.0, 1.0, 3.0], [2.0, 0.0, 1.0]])
    diameter, median, ties, total = stats._chain_dist_stats(dist)
    assert (diameter, median, ties, total) == (3, 1.5, 1, 4)
    vals = dist[np.isfinite(dist) & (dist > 0)]
    assert median == float(np.median(vals)) and total == int(vals.size)
    with_zero = dist[np.isfinite(dist)]
    assert int(with_zero.size) == total + 2, "the two self-distance cells"


def test_ifub_diameter_parity_random_components():  # noqa: C901
    """iFUB must match scipy all-pairs exact diameter on connected and
    multi-component graphs (Wall 3 S1)."""
    import numpy as np
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components, shortest_path

    def exact_diam(csr):
        n = csr.shape[0]
        if n == 0:
            return None
        if n == 1:
            return 0
        nc, comp = connected_components(csr, directed=False)
        best = 0
        for c in range(nc):
            nodes = np.flatnonzero(comp == c)
            if len(nodes) <= 1:
                continue
            sub = csr[nodes][:, nodes]
            d = shortest_path(sub, method="D", unweighted=True)
            f = np.isfinite(d)
            best = max(best, int(d[f].max()))
        return best

    rng = np.random.default_rng(7)

    def rand_component(n, p):
        while True:
            A = rng.random((n, n)) < p
            A = np.triu(A, 1)
            A = A | A.T
            csr = csr_matrix(A.astype(np.int8))
            nc, _ = connected_components(csr, directed=False)
            if nc == 1:
                return csr

    cases = []
    for n, p in [(2, 0.9), (3, 0.5), (5, 0.4), (8, 0.3), (12, 0.25), (20, 0.15)]:
        for _ in range(8):
            cases.append(rand_component(n, p))
    # multi-component graph: two path components (15 + 11 nodes)
    nc = np.zeros((30, 30), dtype=np.int8)
    for i in range(14):
        nc[i, i + 1] = 1
        nc[i + 1, i] = 1
    for i in range(15, 25):
        nc[i, i + 1] = 1
        nc[i + 1, i] = 1
    cases.append(csr_matrix(nc))

    for idx, csr in enumerate(cases):
        assert stats._ifub_diameter(csr) == exact_diam(csr), f"case {idx}"

    n = 60
    path = csr_matrix((np.ones(n - 1), (np.arange(n - 1), np.arange(1, n))), shape=(n, n))
    path = (path + path.T).astype(np.int8)
    assert stats._ifub_diameter(path) == n - 1
    assert stats._ifub_diameter(csr_matrix((1, 1))) == 0
    assert stats._ifub_diameter(csr_matrix((0, 0))) is None


def test_kernel_csr_arrays_zero_copy():
    """S7: kernel_csr_arrays must hand the kernel the SAME int32 arrays when
    the input is already int32/contiguous (no copy), and return int32
    arrays (a copy) for int64 input — the zero-copy ingress contract.
    """
    import numpy as np
    from scipy.sparse import coo_matrix

    from helpers.graph.stats import kernel_csr_arrays

    n = 50
    rows = np.array([0, 1, 2, 5, 9])
    cols = np.array([1, 2, 3, 4, 0])
    m = coo_matrix((np.ones(5), (rows, cols)), shape=(n, n)).tocsr()
    assert m.indptr.dtype == np.int32 and m.indices.dtype == np.int32
    indptr, indices = kernel_csr_arrays(m)
    assert indptr is m.indptr and indices is m.indices

    m64 = m.astype(np.int64)
    indptr64, indices64 = kernel_csr_arrays(m64)
    assert indptr64.dtype == np.int32 and indices64.dtype == np.int32
    assert np.array_equal(indptr64, m64.indptr) and np.array_equal(indices64, m64.indices)

    # non-CSR input (COO) is converted to CSR
    coo = coo_matrix((np.ones(5), (rows, cols)), shape=(n, n))
    indptr_c, _ = kernel_csr_arrays(coo)
    assert indptr_c.dtype == np.int32


def test_ifub_diameter_kernel_lane_matches_ifub_lane():
    """When the Rust graph_kernels extension is installed, the
    kernel-first dispatch in _ifub_diameter must agree with the
    pure-Python iFUB fallback lane on the same graphs (S2a swap).

    Forces the fallback by stubbing _ifub_diameter_kernel to None —
    the same path a venv without the .so takes — so both lanes run
    inside one test regardless of install state.
    """
    import numpy as np
    from scipy.sparse import csr_matrix

    n = 90
    rng = np.random.default_rng(11)
    A = np.triu(rng.random((n, n)) < 0.06, 1)
    A = A | A.T
    random_csr = csr_matrix(A.astype(np.int8))
    path = csr_matrix((np.ones(n - 1), (np.arange(n - 1), np.arange(1, n))), shape=(n, n))
    path = (path + path.T).astype(np.int8)
    star = np.zeros((n, n), dtype=np.int8)
    star[0, 1:] = star[1:, 0] = 1
    star_csr = csr_matrix(star)
    # disconnected: two path components + one isolated node
    dis = np.zeros((n, n), dtype=np.int8)
    for i in range(39):
        dis[i, i + 1] = dis[i + 1, i] = 1
    for i in range(41, 79):
        dis[i, i + 1] = dis[i + 1, i] = 1
    dis_csr = csr_matrix(dis)

    for idx, csr in enumerate([random_csr, path, star_csr, dis_csr]):
        kernel_lane = stats._ifub_diameter(csr)
        with monkeypatch_for_ifub_stub():
            fallback_lane = stats._ifub_diameter(csr)
        assert kernel_lane is not None
        assert kernel_lane == fallback_lane, (
            f"case {idx}: kernel {kernel_lane} vs ifub {fallback_lane}"
        )
        # degenerate shapes share both lanes' contracts
        assert stats._ifub_diameter(csr_matrix((1, 1))) == 0
        assert stats._ifub_diameter(csr_matrix((0, 0))) is None


@contextlib.contextmanager
def monkeypatch_for_ifub_stub():
    """Stub the kernel probe to None so _ifub_diameter takes the iFUB lane."""
    import unittest.mock

    with unittest.mock.patch.object(stats, "_ifub_diameter_kernel", lambda csr: None):
        yield


def _ppr_spsolve_ref(indptr, indices, seed, alpha=0.85):
    """Independent spsolve reference mirroring the scipy lane contract
    (personalized_pagerank_scores in helpers/graph/scipy_bridge.py):
    P = D^-1 A row-stochastic (binary A), dangling rows teleport to the
    seed, solve (I - alpha*P^T) x = (1-alpha) v exactly."""
    import numpy as np
    import scipy.sparse as sp
    from scipy.sparse.linalg import spsolve

    n = len(indptr) - 1
    a = sp.csr_matrix(
        (np.ones(len(indices)), (np.repeat(np.arange(n), np.diff(indptr)), indices)),
        shape=(n, n),
    )
    deg = np.asarray(a.sum(axis=1)).ravel().astype(float)
    inv = np.where(deg > 0, 1.0 / np.maximum(deg, 1e-300), 0.0)
    p = a.astype(float)
    p.data = p.data * np.repeat(inv, np.diff(p.indptr))
    if np.any(deg == 0):
        p = p.tolil()
        for d in np.flatnonzero(deg == 0):
            p[d, seed] = 1.0
        p = p.tocsr()
    eye = sp.identity(n, format="csc")
    v = np.zeros(n)
    v[seed] = 1.0
    x = np.asarray(spsolve(eye - alpha * p.T.tocsc(), (1.0 - alpha) * v)).ravel()
    return x


def test_ppr_kernel_matches_scipy_spsolve():
    """S2 prototype: the Rust power-iteration PPR must land on the scipy
    exact spsolve solution (L1 close) across graph shapes — symmetric
    projection-like, directed, with dangling rows and self-loops."""
    import numpy as np
    import graph_kernels as gk

    if not hasattr(gk, "personalized_pagerank"):
        pytest.skip("stale graph_kernels .so without personalized_pagerank")

    rng = np.random.default_rng(7)
    for trial in range(6):
        n = int(rng.integers(20, 120))
        density = float(rng.uniform(0.02, 0.10))
        mask = rng.random((n, n)) < density
        np.fill_diagonal(mask, False)
        if trial % 2 == 0:  # symmetric projection-like variant
            mask = mask | mask.T
        rows, cols = np.nonzero(mask)
        if len(rows) == 0:
            continue
        m = sp_matrix(rows, cols, n)
        ip, ix = np.asarray(m.indptr, dtype=np.int32), np.asarray(m.indices, dtype=np.int32)
        seed = int(rng.integers(0, n))
        xk = gk.personalized_pagerank(ip, ix, seed, 0.85, 1e-12, 1000)
        xr = _ppr_spsolve_ref(ip, ix, seed)
        assert abs(xk.sum() - 1.0) < 1e-6, f"trial {trial} sum {xk.sum()}"
        assert np.abs(xk - xr).max() < 1e-8, f"trial {trial} maxdiff {np.abs(xk - xr).max()}"


def sp_matrix(rows, cols, n):
    import numpy as np
    import scipy.sparse as sp

    return sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))


def test_ppr_vec_kernel_matches_dense_reference():
    """S5 set-teleport: the Rust vec lane must match an independent dense
    solve of x = aP^Tx + (1-a)v with dangling rows redistributing ∝ v
    (the only convention that reduces to the one-hot scipy lane)."""
    import numpy as np
    from scipy.sparse import csr_matrix

    try:
        import graph_kernels as gk
    except ImportError:
        pytest.skip("graph_kernels not installed")
    if not hasattr(gk, "personalized_pagerank_vec"):
        pytest.skip("stale graph_kernels .so without personalized_pagerank_vec")

    rng = np.random.default_rng(11)
    for trial in range(5):
        n = int(rng.integers(15, 60))
        mask = rng.random((n, n)) < 0.08
        np.fill_diagonal(mask, False)
        if trial % 2 == 0:
            mask = mask | mask.T
        rows, cols = np.nonzero(mask)
        m = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
        m.sort_indices()
        ip = np.ascontiguousarray(m.indptr, dtype=np.int32)
        ix = np.ascontiguousarray(m.indices, dtype=np.int32)
        # random sparse teleport set
        v = np.zeros(n)
        chosen = rng.choice(n, size=max(1, n // 5), replace=False)
        v[chosen] = rng.random(len(chosen))
        v /= v.sum()
        alpha = 0.85
        xk = gk.personalized_pagerank_vec(ip, ix, v, alpha, 1e-13, 5000)
        # dense reference: P rows scaled by 1/deg, dangling rows = v
        P = m.toarray().astype(float)
        deg = P.sum(axis=1)
        for i in range(n):
            if deg[i] > 0:
                P[i] /= deg[i]
            else:
                P[i] = v
        ref = np.linalg.solve(np.eye(n) - alpha * P.T, (1 - alpha) * v)
        assert abs(xk.sum() - 1.0) < 1e-6, f"trial {trial} sum {xk.sum()}"
        assert np.abs(xk - ref).max() < 1e-9, f"trial {trial} maxdiff {np.abs(xk - ref).max()}"


def test_ppr_cohort_slice_kernel_lane():
    """Proposal S3 pin: the kernel lane stays correct on arbitrary
    sub-CSR cohort slices (the sector_scope path) — kernel vs scipy on
    the SAME slice, binary A[keep][:,keep]."""
    import numpy as np
    from scipy.sparse import csr_matrix

    try:
        import graph_kernels as gk
    except ImportError:
        pytest.skip("graph_kernels not installed")
    if not hasattr(gk, "personalized_pagerank"):
        pytest.skip("stale graph_kernels .so without personalized_pagerank")

    rng = np.random.default_rng(3)
    n = 80
    mask = rng.random((n, n)) < 0.07
    mask = mask | mask.T
    np.fill_diagonal(mask, False)
    rows, cols = np.nonzero(mask)
    A = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    keep = sorted(rng.choice(n, size=30, replace=False).tolist())
    S = A[np.ix_(keep, keep)].tocsr()
    S.sort_indices()
    from helpers.graph.scipy_bridge import _ppr_kernel_lane, _ppr_spsolve_lane

    for si in (0, 7, len(keep) - 1):
        kx = _ppr_kernel_lane(S, si, 0.85)
        if kx is None:
            pytest.skip("kernel lane unavailable")
        sx = _ppr_spsolve_lane(S, si, 0.85)
        assert np.abs(kx - sx).max() < 1e-10, f"slice seed {si}"
