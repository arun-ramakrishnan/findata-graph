"""Hermetic tests for the full-incidence cross-edge overlap census
(hyper_motifs — HGX unlock-shelf lane 1 reopen, algorithms_adoption_sweep S3).

Synthetic hypergraphs only — no live incidence store (house convention,
test_hyper_spectral.py style).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from helpers.graph.hyper_motifs import (  # noqa: E402
    census,
    configuration_null,
    run_family,
    verdict,
)

CHAIN = [["a", "b", "c"], ["b", "c", "d"], ["d", "e"]]


def test_census_counts_cross_edge_pairs():
    # e0∩e1 = {b,c} → shared 2; e1∩e2 = {d} → 1; e0∩e2 = {} → 0
    out = census(CHAIN)
    assert out["pairs_sharing_any"] == 2
    assert out["pairs_ge_2"] == 1
    assert out["pairs_ge_3"] == 0
    assert out["hist_shared_2"] == 1
    assert out["hist_shared_3"] == 0
    assert out["hist_shared_4plus"] == 0


def test_census_full_incidence_sees_large_edges():
    """The visibility-defect fix: a size-5 edge overlapping two size-4
    edges in 3+ members is counted — HGX's size<=order census was blind
    to exactly this shape."""
    edges = [
        ["a", "b", "c", "d", "e"],
        ["a", "b", "c", "f"],
        ["a", "b", "c", "g"],
    ]
    out = census(edges, orders=(2, 3, 4))
    assert out["pairs_ge_2"] == 3  # all three pairs
    assert out["pairs_ge_3"] == 3  # {a,b,c} common to all
    assert out["pairs_ge_4"] == 0


def test_census_disjoint_edges():
    out = census([["a", "b"], ["c", "d"]])
    assert out["pairs_sharing_any"] == 0
    assert out["pairs_ge_2"] == 0


def test_planted_core_reads_enriched():
    """10 edges sharing a 5-node core + private fill → far above the
    configuration null at k=2 and k=3."""
    edges = []
    for i in range(10):
        edges.append(["c1", "c2", "c3", "c4", "c5", f"u{i}a", f"u{i}b"])
    obs = census(edges)
    null = configuration_null(edges, runs=10, seed=42)
    for k in (2, 3):
        std = null[f"pairs_ge_{k}_null_std"]
        if std > 0:
            nd = (obs[f"pairs_ge_{k}"] - null[f"pairs_ge_{k}_null_mean"]) / std
            assert nd >= 2.0, f"planted core not enriched at k={k}: nd={nd}"


def test_random_null_reads_null():
    """A config-model-consistent graph (random stub deal itself) → the
    statistic sits inside the null band."""
    rng = np.random.default_rng(7)
    nodes = [f"n{i}" for i in range(60)]
    edges: list[list[str]] = []
    for _ in range(40):
        edges.append(sorted(rng.choice(nodes, size=4, replace=False).tolist()))
    obs = census(edges)
    null = configuration_null(edges, runs=10, seed=42)
    std = null["pairs_ge_2_null_std"]
    if std > 0:  # near-degenerate nulls skip the assert (verdict ~NULL)
        nd = abs(obs["pairs_ge_2"] - null["pairs_ge_2_null_mean"]) / std
        assert nd < 2.0, f"random graph reads enriched: nd={nd}"


def test_configuration_null_preserves_sizes_and_degrees():
    edges = [["a", "b", "c"], ["b", "c", "d"], ["d", "e"]]
    null = configuration_null(edges, runs=3, seed=1)
    assert null["runs"] == 3 and null["seed"] == 1
    assert "pairs_ge_2_null_mean" in null and "pairs_ge_2_null_std" in null


def test_null_determinism():
    edges = [["a", "b", "c"], ["b", "c", "d"], ["d", "e"], ["a", "e", "f"]]
    a = configuration_null(edges, runs=5, seed=42)
    b = configuration_null(edges, runs=5, seed=42)
    assert a == b


def test_verdict_bands():
    assert verdict(3.1) == "ENRICHED"
    assert verdict(-2.5) == "DEPLETED"
    assert verdict(0.4) == "~NULL"
    assert verdict(None) == "~NULL (degenerate null)"


def test_run_family_end_to_end(tmp_path, monkeypatch):
    """run_family on a synthetic store-free family: monkeypatch the
    hyper_spectral loader (run_family imports it module-side) so no live
    store is touched."""
    import helpers.graph.hyper_motifs as hm

    planted = {f"e{i}": ["c1", "c2", "c3", "c4", "c5", f"u{i}a", f"u{i}b"] for i in range(10)}
    monkeypatch.setattr(hm, "load_family", lambda *a, **k: planted)
    res = run_family("synthetic", runs=10, seed=42)
    assert res["edges"] == 10
    assert res["nodes"] == 25  # 5 core + 10×2 private fill
    assert res["pairs_ge_2"] == 45  # every pair shares the 5-node core
    assert res["pairs_ge_3"] == 45
    for k in (2, 3):
        std = res["null"][f"pairs_ge_{k}_null_std"]
        if std > 0:
            assert res["norm_delta"][k] >= 2.0
            assert res["verdicts"][k] == "ENRICHED"


def test_run_family_giant_guard_propagates():
    """The load_family k>=2 cap guard surfaces as ValueError (giant-k
    families need a deliberate --k2-cap override)."""
    import helpers.graph.hyper_motifs as hm

    def boom(*a, **k):
        raise ValueError("999 k>=2 edges exceeds the probe cap 300")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(hm, "load_family", boom)
    with pytest.raises(ValueError, match="exceeds the probe cap"):
        run_family("giant", runs=2, seed=1)
    monkey.undo()


def test_pair_shared_counts_and_relation():
    from helpers.graph.hyper_motifs import pair_shared_counts, relation_of

    edges = [["a", "b", "c"], ["b", "c", "d"], ["d", "e"]]
    shared = pair_shared_counts(edges)
    assert shared == {(0, 1): 2, (1, 2): 1}
    assert relation_of({"a", "b"}, {"a", "b", "c"}) == "subset"
    assert relation_of({"a", "c"}, {"a", "b", "d"}) == "overlap"


def test_pairs_out_cli(tmp_path):
    """--pairs-out writes a deterministic CSV for one family (monkeypatched
    loader — no live store)."""
    import csv

    import helpers.graph.hyper_motifs as hm

    planted = {
        "group:Alpha": ["c1", "c2", "c3", "c4"],
        "group:Beta": ["c1", "c2", "c3", "x1"],
        "group:Gamma": ["z1", "z2"],
    }
    import pyarrow as pa

    out = tmp_path / "pairs.csv"
    monkey = pytest.MonkeyPatch()
    monkey.setattr(hm, "load_family", lambda *a, **k: planted)
    # stub the CLI's family-validation probe too (would touch the live store)
    monkey.setattr(
        hm.ha,
        "load_incidence_arrow",
        lambda sources, **k: pa.table({"edge_type": list(sources)}),
    )
    rc = hm._cli(
        ["--sources", "synthetic", "--orders", "2", "--runs", "2", "--pairs-out", str(out)]
    )
    monkey.undo()
    assert rc == 0
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 1  # only Alpha/Beta share >=2
    assert rows[0]["label1"] == "group:Alpha" and rows[0]["label2"] == "group:Beta"
    assert rows[0]["shared"] == "3" and rows[0]["relation"] == "overlap"
