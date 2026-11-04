"""Range-review R12: pin the lane_chains_exact memory guards.

The 128 MiB dense-block budget (roots_chunk) and the dense-generator
ceiling (_DENSE_GEN_MAX_N) are load-bearing: unguarded, the sampled
diameter leg materialized multi-GB dense blocks and thrashed the box
(2026-11-03). The constants and the two generators were nested inside
lane_chains_exact and untestable; hoisted to module level 2026-11-04 so
they can be pinned here without running the bench.
"""

import numpy as np
from scipy.sparse.csgraph import connected_components

from helpers.bench import hyper_scale_bench as hsb


def _shape_props(g):
    """(n_components, symmetric, no self-loops) for a symmetric csr graph."""
    n_comp = connected_components(g, directed=False)[0]
    symmetric = (g != g.T).nnz == 0
    no_self_loops = g.diagonal().sum() == 0
    return n_comp, symmetric, no_self_loops


class TestMemoryGuards:
    def test_budget_constant(self):
        assert hsb._SAMPLED_MEM_BUDGET_BYTES == 128 * 1024 * 1024

    def test_dense_ceiling_constant(self):
        # above this n the dense rng.random((n, n)) intermediate thrashed
        # the box (7.2 GB at n=30k) — the generator must switch to sparse
        assert hsb._DENSE_GEN_MAX_N == 5000

    def test_roots_chunk_blocks_stay_under_budget(self):
        for n in (10_000, 30_000, 60_000):
            chunk = hsb.roots_chunk(n)
            assert chunk >= 1
            assert chunk * 8 * n <= hsb._SAMPLED_MEM_BUDGET_BYTES

    def test_roots_chunk_never_below_one(self):
        # huge n must degrade to chunk=1 (still correct), never 0/negative
        assert hsb.roots_chunk(100_000_000) == 1

    def test_wall_posture_bounds_block(self):
        # the documented wall case: cap 6000 roots at n=60k -> ~128MB block
        chunk = hsb.roots_chunk(60_000)
        assert chunk == hsb._SAMPLED_MEM_BUDGET_BYTES // (8 * 60_000)
        assert 278 <= chunk <= 280  # 128MiB / 480000 = 279.6 -> 279


class TestGenerators:
    def test_dense_generator_connected_and_wellformed(self):
        g = hsb.connected_rand(200, 0.02, np.random.default_rng(7))
        n_comp, symmetric, no_self_loops = _shape_props(g)
        assert n_comp == 1  # path backbone guarantees one component
        assert symmetric
        assert no_self_loops

    def test_sparse_generator_connected_and_wellformed(self):
        g = hsb.connected_rand_sparse(3000, 0.002, np.random.default_rng(7))
        n_comp, symmetric, no_self_loops = _shape_props(g)
        assert n_comp == 1
        assert symmetric
        assert no_self_loops
        # backbone pairs are re-added explicitly
        assert g[0, 1] == 1
        assert g[2998, 2999] == 1

    def test_generators_share_ensemble_contract(self):
        # same n/p: both produce one connected component over the same
        # backbone-plus-Bernoulli ensemble (the "same ensemble" docstring)
        for gen in (hsb.connected_rand, hsb.connected_rand_sparse):
            g = gen(300, 0.05, np.random.default_rng(11))
            n_comp, symmetric, no_self_loops = _shape_props(g)
            assert n_comp == 1 and symmetric and no_self_loops
            assert g[0, 1] == 1  # backbone edge present in both builds
