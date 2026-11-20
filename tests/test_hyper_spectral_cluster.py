"""QA pin for the spectral community consumer (hyper_spectral_cluster).

Hermetic twin of the live-store verdict (#549 smoke, 2026-11-20): ARI vs
the hand-authored sector taxonomy is ~0 at every representative order —
max 0.0418 @ order 101 — so the spectral lane is a QA instrument, not a
taxonomy rediscovery (harvest_promotions_and_census_writeup S2a / §2.5
T1.3). House convention: synthetic hypergraphs only, no live store.

The fixture is an 80-company grid: 4 sector hyperedges (size 20, each
company in exactly one — the taxonomy, as in the real partition families)
plus 10 theme hyperedges (size 8, two members per sector — taxonomy-
CROSSING structure, the shape the verdict says the store's real signal
has). An order-k Laplacian only sees edges of size k+1, so order 7
resolves themes alone, order 19 resolves sectors alone, and every other
order mixes both with no taxonomy signal — three regimes to pin:

  * aligned structure IS recovered (the instrument is not dead);
  * crossing structure is recovered as itself while ARI vs the taxonomy
    stays in the no-signal band (structure exists, taxonomy-blind);
  * mixed orders carry no taxonomy signal (the measured store regime).

A future store that grows real sector-aligned spectral structure fails
`make hyper-spectral-check` loudly (the CLI --max-ari guard) — the
verdict decays by re-measurement, never silently. Numbers below were
measured with seed 42, sklearn KMeans (use_emeans=False for
determinism; the CLI auto-prefers e-means when faiss is importable).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from hypergraphx import Hypergraph  # noqa: E402
from hypergraphx.linalg import laplacian_matrices_all_orders  # noqa: E402

import helpers.graph.hyper_spectral_cluster as hsc  # noqa: E402

SECTORS, COMPANIES, THEMES = 4, 20, 10
SECTOR_ORDER = COMPANIES - 1  # 19: only size-20 sector edges contribute
THEME_ORDER = 8 - 1  # 7: only size-8 theme edges contribute


def _fixture():
    """4 sector edges (size 20) + 10 theme edges (size 8, 2 per sector)."""
    incidence = {f"sector:S{s}": [f"c{s}_{i}" for i in range(COMPANIES)] for s in range(SECTORS)}
    for t in range(THEMES):
        incidence[f"theme:T{t}"] = [
            f"c{s}_{(t * 2 + r) % COMPANIES}" for s in range(SECTORS) for r in range(2)
        ]
    hg = Hypergraph(edge_list=[tuple(v) for v in incidence.values()])
    node2id = hg.get_mapping()._to_int
    id2node = {v: n for n, v in node2id.items()}
    nodes = [id2node[i] for i in range(len(id2node))]
    sector_of = hsc._sector_labels(incidence)
    theme_of = {m: lab for lab, mem in incidence.items() if lab.startswith("theme:") for m in mem}
    s_codes = {lab: i for i, lab in enumerate(sorted(set(sector_of.values())))}
    t_codes = {lab: i for i, lab in enumerate(sorted(set(theme_of.values())))}
    y_sector = np.array([s_codes[sector_of[n]] for n in nodes])
    y_theme = np.array([t_codes[theme_of[n]] for n in nodes])
    return hg, nodes, y_sector, y_theme


@pytest.fixture(scope="module")
def grid():
    hg, nodes, y_sector, y_theme = _fixture()
    return {
        "laps": laplacian_matrices_all_orders(hg),
        "nodes": nodes,
        "y_sector": y_sector,
        "y_theme": y_theme,
    }


def _ari(grid, order, m, k):
    _, ari = hsc._require_sklearn()
    vecs = hsc._eigenvectors(grid["laps"][order], m)
    labels = hsc._cluster(vecs, k, seed=42, use_emeans=False)
    return ari(labels, grid["y_sector"]), ari(labels, grid["y_theme"])


def test_aligned_order_recovers_the_taxonomy(grid):
    # order 19 sees only the size-20 sector edges: the partition IS the
    # structure — recovery must be strong (measured +0.6414 @ k=10)
    ari_sector, _ = _ari(grid, SECTOR_ORDER, m=8, k=10)
    assert ari_sector > 0.5


def test_crossing_order_blind_to_taxonomy_but_not_to_structure(grid):
    # order 7 sees only the size-8 theme edges: themes are recovered
    # exactly (measured 1.0) while ARI vs sector stays in the no-signal
    # band (measured -0.0604) — the live verdict's shape
    ari_sector, ari_theme = _ari(grid, THEME_ORDER, m=8, k=10)
    assert ari_theme > 0.95
    assert ari_sector < 0.1


def test_mixed_orders_carry_no_taxonomy_signal(grid):
    # every other order mixes both families: no taxonomy signal at CLI
    # defaults (measured max +0.0204 across orders 1..18 excl. 7)
    for order in (1, 5, 10, 15):
        ari_sector, _ = _ari(grid, order, m=8, k=4)
        assert ari_sector < 0.1, f"order {order}: {ari_sector:+.4f}"
