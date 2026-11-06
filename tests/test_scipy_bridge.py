#!/usr/bin/env python3
"""Unit tests for the scipy bridge L1a lane (helpers/graph/scipy_bridge.py).

scipy_graph_bridge S1 = L1's route (d): multi-source dijkstra over the
graph_edges CSR. Runs fully in-repo (.venv has scipy) — no venv gating.
Fixture mirrors the live schema in miniature: a tmp sqlite with
graph_edges + graph_analytics (the duckdb leg of the live stack is not
involved — the bridge is SQLite-only by design).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from helpers.graph import scipy_bridge as sb
from helpers.graph import algorithms as alg

# component A = {a,b,c} (path), component B = {d,e} (pair).
# distances from a: [0,1,2,inf,inf] — unreachable pairs drop from sums.
_NODES = ["a", "b", "c", "d", "e"]
_EDGES = [("a", "b"), ("b", "c"), ("d", "e")]
_CONTRACT = ["a", "c", "e"]  # deliberately NOT sorted-position order


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    spath = tmp_path / "r.db"
    scon = sqlite3.connect(str(spath))
    scon.execute("CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, weight REAL)")
    scon.executemany("INSERT INTO graph_edges VALUES (?, ?, 1.0)", _EDGES)
    scon.execute(
        "CREATE TABLE graph_analytics (entity_name VARCHAR, metric VARCHAR, "
        "value VARCHAR, computed_at DATETIME, PRIMARY KEY (metric, entity_name))"
    )
    scon.executemany(
        "INSERT INTO graph_analytics (entity_name, metric, value) VALUES (?, 'closeness_centrality', ?)",
        [(n, "0.0") for n in _CONTRACT],
    )
    scon.commit()
    scon.close()
    return spath


def test_load_projection_deterministic_ids(store):
    A, names = sb.load_projection(store)
    assert names == _NODES  # sorted names = deterministic ids (720e38ff discipline)
    assert A.shape == (5, 5)
    # undirected: both directions present
    assert A[0, 1] == 1 and A[1, 0] == 1 and A[2, 3] == 0


def test_fused_derive_hand_checked():
    # path 0-1-2, source row = distances from node 0: [0, 1, 2]
    D = np.array([[0.0, 1.0, 2.0]])
    clo, harm = sb.fused_derive(D)
    assert clo[0] == pytest.approx(2.0 / 3.0)  # (N-1)/sum_d
    assert harm[0] == pytest.approx(1.5)  # 1/1 + 1/2


def test_fused_derive_unreachable_excluded():
    D = np.array([[0.0, 1.0, np.inf], [1.0, 0.0, np.inf]])
    clo, harm = sb.fused_derive(D)
    assert clo[0] == pytest.approx(2.0)  # (N-1)/sum(reachable) = 2/1
    assert harm[0] == pytest.approx(1.0)
    assert clo[1] == pytest.approx(2.0)
    assert harm[1] == pytest.approx(1.0)


def test_source_positions_contract_order(store):
    """The (d)-spike part-1 shuffle bug: results row i must be contract[i],
    NOT sorted order. Reverse the contract and the positions must follow."""
    A, names = sb.load_projection(store)
    src = sb.source_positions(_CONTRACT, names)
    assert list(src) == [0, 2, 4]  # a=0, c=2, e=4 in contract order
    src_rev = sb.source_positions(list(reversed(_CONTRACT)), names)
    assert list(src_rev) == [4, 2, 0]


def test_source_positions_missing_name_raises(store):
    A, names = sb.load_projection(store)
    with pytest.raises(KeyError):
        sb.source_positions(["a", "ghost"], names)


# jobs=2 fork-splits by design (fork_map CoW); the 3.14 fork-after-threads
# notice is expected here, not a regression signal.
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded.*:DeprecationWarning")
def test_compute_jobs1_matches_jobs2(store):
    A, names = sb.load_projection(store)
    src = sb.source_positions(_CONTRACT, names)
    clo1, harm1 = sb.compute(A, src, jobs=1)
    clo2, harm2 = sb.compute(A, src, jobs=2)
    np.testing.assert_allclose(clo1, clo2)
    np.testing.assert_allclose(harm1, harm2)


def test_values_match_hand_computed(store):
    A, names = sb.load_projection(store)
    src = sb.source_positions(_CONTRACT, names)
    clo, harm = sb.compute(A, src, jobs=1)
    # a: dists [0,1,2,inf,inf] -> clo = 4/3, harm = 1.5
    assert clo[0] == pytest.approx(4.0 / 3.0)
    assert harm[0] == pytest.approx(1.5)
    # e: dists [inf,inf,inf,1,0] -> clo = 4/1, harm = 1.0
    assert clo[2] == pytest.approx(4.0)
    assert harm[2] == pytest.approx(1.0)


def test_cli_end_to_end_apply_vs_dry_run(store, capsys):
    argv = ["closeness-harmonic", "--db", str(store), "--top", "2", "--jobs", "1"]
    # dry-run: no writes
    assert sb.main(argv) == 0
    scon = sqlite3.connect(str(store))
    n_before = scon.execute("SELECT COUNT(*) FROM graph_analytics").fetchone()[0]
    assert n_before == 3  # only the seeded closeness rows

    # apply: 3 closeness + 3 harmonic rows, values json-serialised
    assert sb.main([*argv, "--apply"]) == 0
    rows = scon.execute(
        "SELECT entity_name, metric, value FROM graph_analytics ORDER BY metric, entity_name"
    ).fetchall()
    assert len(rows) == 6
    assert {m for _, m, _ in rows} == {
        "closeness_centrality",
        "harmonic_centrality",
    }
    for _, _, value in rows:
        json.loads(value)  # write_analytics contract: json-serialised values
    # contract order preserved end-to-end: 'a' distances [0,1,2,inf,inf]
    a_clo = json.loads([v for e, m, v in rows if e == "a" and m == "closeness_centrality"][0])
    assert a_clo == pytest.approx(4.0 / 3.0)
    a_harm = json.loads([v for e, m, v in rows if e == "a" and m == "harmonic_centrality"][0])
    assert a_harm == pytest.approx(1.5)
    scon.close()


def test_upsert_on_reapply(store):
    """write_analytics UPSERTs on (metric, entity_name): re-running --apply
    must UPDATE, not duplicate, the contract rows."""
    argv = ["closeness-harmonic", "--db", str(store), "--jobs", "1", "--apply"]
    assert sb.main(argv) == 0
    assert sb.main(argv) == 0
    scon = sqlite3.connect(str(store))
    n = scon.execute("SELECT COUNT(*) FROM graph_analytics").fetchone()[0]
    assert n == 6  # 3 closeness + 3 harmonic, not 12
    scon.close()


def test_routing_command(capsys):
    assert sb.main(["routing"]) == 0
    out = capsys.readouterr().out
    assert "closeness_centrality" in out and "SCIPY" in out


# --------------------------------------------------------------------------- #
# ROUTING dispatch (scipy_routing_dispatch S2) — algorithms.py serves the
# lane for SCIPY-owned metrics on the DB-backed path.
# --------------------------------------------------------------------------- #
def test_routing_table_covers_known_metrics():
    routed = {m for m, o in sb.ROUTING.items() if o == "SCIPY"}
    assert routed == {
        "closeness_centrality",
        "harmonic_centrality",
        sb.YEN_LANE,
        sb.MAXFLOW_LANE,
        sb.PPR_LANE,
    }
    # metric lanes must be dispatchable; the s-t lanes are CLI commands, not metrics
    assert set(sb.ROUTING) <= set(alg._METRIC_DISPATCH) | {
        "link_prediction",
        sb.YEN_LANE,
        sb.MAXFLOW_LANE,
    }
    assert sb.YEN_LANE not in alg._METRIC_DISPATCH
    assert sb.MAXFLOW_LANE not in alg._METRIC_DISPATCH
    assert set(sb.ROUTING.values()) <= {"SCIPY", "ONAGER_DEFAULT", "L1B_FOLD"}


def test_dispatch_closeness_tmpstore(store):
    got = alg._run_closeness(None, edges=None, db_path=store)
    assert set(got) == set(_CONTRACT)  # contract population, not full graph
    assert got["a"] == pytest.approx(4.0 / 3.0)
    assert got["c"] == pytest.approx(4.0 / 3.0)
    assert got["e"] == pytest.approx(4.0)


def test_dispatch_harmonic_tmpstore(store):
    got = alg._run_harmonic(None, edges=None, db_path=store)
    assert set(got) == set(_CONTRACT)
    assert got["a"] == pytest.approx(1.5)
    assert got["c"] == pytest.approx(1.5)
    assert got["e"] == pytest.approx(1.0)


def test_dispatch_pair_uses_one_scipy_kernel(store, monkeypatch):
    calls = 0
    real_compute = sb.compute

    def counted_compute(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(sb, "compute", counted_compute)
    got = alg._run_scipy_lane_pair(store)
    assert calls == 1
    assert got[sb.CLOSINESS_METRIC]["a"] == pytest.approx(4.0 / 3.0)
    assert got[sb.HARMONIC_METRIC]["a"] == pytest.approx(1.5)


def test_dispatch_compute_plumbs_db_path(store):
    got = alg.compute("harmonic_centrality", edges=None, db_path=store)
    assert set(got) == set(_CONTRACT)
    assert got["e"] == pytest.approx(1.0)


def test_dispatch_drift_fails_loud(store):
    scon = sqlite3.connect(str(store))
    scon.execute(
        "INSERT INTO graph_analytics (entity_name, metric, value) VALUES ('ghost', 'closeness_centrality', '0.0')"
    )
    scon.commit()
    scon.close()
    with pytest.raises(KeyError, match="ghost"):
        alg._run_closeness(None, edges=None, db_path=store)


def test_dispatch_empty_projection_fails_loud(tmp_path):
    spath = tmp_path / "empty.db"
    scon = sqlite3.connect(str(spath))
    scon.execute("CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, weight REAL)")
    scon.execute(
        "CREATE TABLE graph_analytics (entity_name VARCHAR, metric VARCHAR, "
        "value VARCHAR, computed_at DATETIME, PRIMARY KEY (metric, entity_name))"
    )
    scon.execute(
        "INSERT INTO graph_analytics (entity_name, metric, value) VALUES ('a', 'closeness_centrality', '0.0')"
    )
    scon.commit()
    scon.close()
    with pytest.raises(ValueError, match="empty"):
        alg._run_closeness(None, edges=None, db_path=spath)


def test_dispatch_synthetic_edges_stay_onager():
    edges = [(0, 1, 1.0), (1, 2, 1.0), (2, 3, 1.0), (3, 4, 1.0)]
    via_dispatch = alg._run_closeness(None, edges=edges)
    direct = alg.closeness_centrality(None, edges=edges)
    assert via_dispatch == direct  # same Onager path, not the lane
    assert set(via_dispatch) == {0, 1, 2, 3, 4}  # full population, int ids


@pytest.mark.live
def test_dispatch_live_closeness_serving_stack_invariants():
    """Serve-from-cache doctrine (operator ruling 2026-10-11, see the
    betweenness twin in test_l1_betweenness.py): the live assertions are
    the serving-stack invariants — full population, closeness in [0, 1],
    deterministic within a generation. Fresh-exact parity is off-gate
    slow-class; the stamped graph_analytics rows are the durable exact
    record and are deliberately NOT refreshed for lane-served metrics."""
    first = alg._run_closeness(None, edges=None)
    assert len(first) > 1500, "the live centrality universe must be served in full"
    for name, value in first.items():
        assert name, "entity names are non-empty"
        assert 0.0 <= value <= 1.0, f"closeness out of range for {name!r}: {value}"
    second = alg._run_closeness(None, edges=None)
    assert second == first, "serving stack must be deterministic within a generation"


# --------------------------------------------------------------------------- #
# S2 exact-solve lanes (scipy_katz_exact_solve + scipy_eigenvector_eigsh):
# hand oracles, dense-oracle agreement, guard behavior, live parity vs
# FRESH Onager (same generation — never vs persisted rows: lineage is
# not correctness, dispatch proposal §8.1).
# --------------------------------------------------------------------------- #
def _star_csr(n_leaves: int):
    """Star K1,n CSR (center 0, float64, symmetric, no self-loops)."""
    from scipy.sparse import csr_matrix as _csr

    r, c = (
        [0] * n_leaves + list(range(1, n_leaves + 1)),
        list(range(1, n_leaves + 1)) + [0] * n_leaves,
    )
    return _csr(
        (np.ones(2 * n_leaves), (np.array(r), np.array(c))),
        shape=(n_leaves + 1, n_leaves + 1),
    )


def test_katz_star_hand_exact():
    # Proposal §2: star K1,4, beta=1, alpha=0.1 (admissible: radius 0.5):
    # x_c = 1.4/0.96, x_leaf = 1.1458.
    x = sb.katz_scores(_star_csr(4), alpha=0.1, beta=1.0)
    assert x[0] == pytest.approx(1.4 / 0.96)
    assert list(x[1:]) == pytest.approx([1.1 / 0.96] * 4)
    # Dense oracle agreement (acceptance item 3).
    import numpy.linalg as _la

    dense = _la.solve(np.eye(5) - 0.1 * _star_csr(4).toarray(), np.ones(5))
    np.testing.assert_allclose(x, dense, rtol=1e-9)


def test_katz_guard_refuses_beyond_radius():
    # Star K1,4 radius is 0.5: alpha=0.6 must refuse with the bound named.
    with pytest.raises(ValueError, match="0.495"):
        sb.katz_scores(_star_csr(4), alpha=0.6)
    with pytest.raises(ValueError, match="positive"):
        sb.katz_scores(_star_csr(4), alpha=0.0)


def test_katz_near_critical_demo():
    # 200-leaf star: lambda_max = sqrt(200) ~ 14.14, so alpha=0.1 is
    # outside the radius (existing test pins Onager raising "Convergence
    # failed" there) while alpha = 0.9/lambda_max solves exact.
    A = _star_csr(200)
    lam = sb.spectral_radius(A)
    assert lam == pytest.approx(14.1421, rel=1e-3)
    with pytest.raises(ValueError, match="diverges"):
        sb.katz_scores(A, alpha=0.1)
    x = sb.katz_scores(A, alpha=0.9 / lam)
    assert np.all(np.isfinite(x))
    assert x[0] > x[1]  # spread restored (pin would flatten to ~1.0003)


def test_eigenvector_star_matches_eigh():
    # Hand oracle (eigen equation + unit norm): star K1,4 center
    # 1/sqrt(2), leaves 1/sqrt(8) (4*l = 2*c, c^2 + 4*l^2 = 1) + dense
    # oracle (acceptance item 2).
    import numpy.linalg as _la

    A = _star_csr(4)
    v = sb.eigenvector_scores(A)
    assert np.linalg.norm(v) == pytest.approx(1.0)
    assert v[0] == pytest.approx(1 / np.sqrt(2))
    assert list(v[1:]) == pytest.approx([1 / np.sqrt(8)] * 4)
    assert v[0] > 0
    w, vecs = _la.eigh(A.toarray())
    ref = vecs[:, np.argmax(w)]
    ref = ref / np.linalg.norm(ref)
    if ref[np.argmax(np.abs(ref))] < 0:
        ref = -ref
    np.testing.assert_allclose(v, ref, rtol=1e-6)


def test_eigenvector_nonconvergence_raises_loud(monkeypatch):
    from scipy.sparse.linalg import ArpackNoConvergence as _ANC

    def _boom(*a, **k):
        raise _ANC("no-converge", None, None)

    monkeypatch.setattr(sb, "eigsh", _boom)
    with pytest.raises(RuntimeError, match="failed to converge"):
        sb.eigenvector_scores(_star_csr(4))
    with pytest.raises(RuntimeError, match="failed to converge"):
        sb.spectral_radius(_star_csr(4))


@pytest.mark.onager  # pins onager's own "Convergence failed" raise (completed.md #427)
def test_eigenvector_onager_failure_case():
    # P100 slow-mixing path: Onager's fixed 100-iteration loop raises,
    # ARPACK converges (acceptance item 3).
    from helpers.graph import onager as _on

    edges = [(i, i + 1, 1.0) for i in range(100)]
    with pytest.raises(Exception, match="Convergence failed"):
        _on.onager_eigenvector(edges=edges)
    n = 101
    r = list(range(n - 1)) + list(range(1, n))
    c = list(range(1, n)) + list(range(n - 1))
    from scipy.sparse import csr_matrix as _csr

    Ap = _csr((np.ones(2 * (n - 1)), (np.array(r), np.array(c))), shape=(n, n))
    v = sb.eigenvector_scores(Ap)
    assert np.all(np.isfinite(v)) and np.linalg.norm(v) == pytest.approx(1.0)


@pytest.mark.live
def test_exact_lanes_live_parity_vs_fresh():
    # Katz r ~ 1.0 + eigenvector r ~ 1.0 vs fresh Onager (same
    # generation); absolute tolerance (not bit-exact): the C++ stops at
    # its own iteration tolerance (katz maxdiff 7.6e-3 measured).
    A, names = sb.load_centrality_projection()
    xk = sb.katz_scores(A, alpha=1e-4, beta=1.0)
    ve = sb.eigenvector_scores(A)
    mine_k = {names[i]: float(xk[i]) for i in range(len(names))}
    mine_e = {names[i]: float(ve[i]) for i in range(len(names))}
    with alg.bypass_centrality_cache():
        fresh_k = alg.katz_centrality()
        fresh_e = alg.eigenvector_centrality()
    ks = [k for k in mine_k if k in fresh_k]
    xk_a = np.array([mine_k[k] for k in ks])
    yk_a = np.array([fresh_k[k] for k in ks])
    assert float(np.corrcoef(xk_a, yk_a)[0, 1]) > 0.999
    assert float(np.max(np.abs(xk_a - yk_a))) < 0.02
    es = [k for k in mine_e if k in fresh_e]
    xe_a = np.array([mine_e[k] for k in es])
    ye_a = np.array([fresh_e[k] for k in es])
    assert float(np.corrcoef(xe_a, ye_a)[0, 1]) > 0.999


def _exact_store(tmp_path, metric):
    spath = tmp_path / f"{metric}.db"
    scon = sqlite3.connect(str(spath))
    scon.execute("CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, edge_type VARCHAR)")
    # PPR include-only projection (PPR_INCLUDE_EDGE_TYPES) filters edge
    # types, so the PPR-parameterised fixture must use a relational type
    # ('competes_with'); katz/eigenvector lanes see every type either way.
    edge_type = "competes_with" if metric == "pagerank_personalized" else "cited_in"
    scon.executemany(
        f"INSERT INTO graph_edges VALUES (?, ?, '{edge_type}')",  # noqa: S608  # edge_type is a module-local literal pair
        [("a", "b"), ("b", "c"), ("c", "d")],
    )
    scon.execute(
        "CREATE TABLE graph_analytics (entity_name VARCHAR, metric VARCHAR, "
        "value VARCHAR, computed_at DATETIME, PRIMARY KEY (metric, entity_name))"
    )
    scon.executemany(
        "INSERT INTO graph_analytics (entity_name, metric, value) VALUES (?, ?, '0.0')",
        [(n, metric) for n in ["a", "b", "c", "d"]],
    )
    if metric == "pagerank_personalized":
        # ppr_results_table §2: the PPR lane persists here, not in
        # graph_analytics. Same seed-keyed shape as the canonical DDL
        # (helpers/maintenance/migrate_to_graph_edges.py PPR_RESULTS_TABLE_DDL).
        scon.execute(
            "CREATE TABLE ppr_results (seed TEXT NOT NULL, entity TEXT NOT NULL, "
            "score REAL NOT NULL, alpha REAL NOT NULL DEFAULT 0.85, "
            "computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (seed, entity))"
        )
    scon.commit()
    scon.close()
    return spath


def test_katz_cli_apply_tmpstore(tmp_path):
    spath = _exact_store(tmp_path, "katz_centrality")
    assert sb.main(["katz", "--db", str(spath), "--top", "2", "--apply"]) == 0
    scon = sqlite3.connect(str(spath))
    rows = scon.execute(
        "SELECT entity_name, value FROM graph_analytics WHERE metric='katz_centrality'"
    ).fetchall()
    scon.close()
    assert len(rows) == 4
    vals = {e: float(v) for e, v in rows}
    assert vals["b"] > vals["a"] > 0  # interior outranks leaf


def test_eigenvector_cli_apply_tmpstore(tmp_path):
    spath = _exact_store(tmp_path, "eigenvector_centrality")
    assert sb.main(["eigenvector", "--db", str(spath), "--top", "2", "--apply"]) == 0
    scon = sqlite3.connect(str(spath))
    n = scon.execute(
        "SELECT COUNT(*) FROM graph_analytics WHERE metric='eigenvector_centrality'"
    ).fetchone()[0]
    scon.close()
    assert n == 4


def test_live_parity_checks_are_classified():
    from tests import test_l1_betweenness

    checks = (
        test_dispatch_live_closeness_serving_stack_invariants,
        test_exact_lanes_live_parity_vs_fresh,
        test_l1_betweenness.test_dispatch_live_betweenness_serving_stack_invariants,
    )
    assert all(
        any(mark.name == "live" for mark in getattr(check, "pytestmark", ())) for check in checks
    )


# --------------------------------------------------------------------------- #
# s-t lanes (scipy_st_lanes_routing_switch): ROUTING switch + wall-clock
# budget guard; toy oracles from the archived Yen / max-flow proposals.
# --------------------------------------------------------------------------- #
def _undirected_csr(n: int, edges: list[tuple[int, int, float]]):
    from scipy.sparse import csr_matrix as _csr

    r = [a for a, _, _ in edges] + [b for _, b, _ in edges]
    c = [b for _, b, _ in edges] + [a for a, _, _ in edges]
    v = [w for _, _, w in edges] * 2
    return _csr((v, (r, c)), shape=(n, n))


def test_s_t_lanes_registered_and_budgeted():
    assert sb.ROUTING[sb.YEN_LANE] == "SCIPY"
    assert sb.ROUTING[sb.MAXFLOW_LANE] == "SCIPY"
    # the s-t lanes carry wall-clock budgets (switch owns lane TIME); the
    # closeness/harmonic lanes keep their `make perf` budgets instead.
    assert {sb.YEN_LANE, sb.MAXFLOW_LANE} <= set(sb.LANE_BUDGETS)
    # budgets only exist for lanes the switch actually owns
    assert set(sb.LANE_BUDGETS) <= {m for m, o in sb.ROUTING.items() if o == "SCIPY"}


def test_yen_toy_oracle():
    # Archived scipy_yen_k_shortest.md §2: 0-1(1),1-2(2),2-4(1),0-3(2),3-2(2),1-3(3)
    names = ["n0", "n1", "n2", "n3", "n4"]
    A = _undirected_csr(5, [(0, 1, 1), (1, 2, 2), (2, 4, 1), (0, 3, 2), (3, 2, 2), (1, 3, 3)])
    paths = sb.yen_paths(A, names, "n0", "n4", k=3, unweighted=False)
    costs = [c for c, _ in paths]
    assert costs == [4.0, 5.0, 7.0]
    assert paths[0][1] == ["n0", "n1", "n2", "n4"]
    assert paths[1][1] == ["n0", "n3", "n2", "n4"]
    assert paths[2][1] == ["n0", "n1", "n3", "n2", "n4"]
    for _, seq in paths:
        assert len(seq) == len(set(seq))  # simple paths
    assert costs == sorted(costs)  # nondecreasing


def test_maxflow_toy_oracle_clrs():
    # Archived scipy_maximum_flow.md §2: maxflow 23 = mincut.
    from scipy.sparse import csr_array

    nodes = ["s", "v1", "v2", "v3", "v4", "t"]
    idx = {n: i for i, n in enumerate(nodes)}
    edges = [
        ("s", "v1", 16),
        ("s", "v2", 13),
        ("v2", "v1", 4),
        ("v1", "v3", 12),
        ("v3", "v2", 9),
        ("v2", "v4", 14),
        ("v4", "v3", 7),
        ("v3", "t", 20),
        ("v4", "t", 4),
    ]
    cap = csr_array(
        (
            np.array([c for _, _, c in edges], dtype=np.int64),
            (
                np.array([idx[a] for a, _, _ in edges]),
                np.array([idx[b] for _, b, _ in edges]),
            ),
        ),
        shape=(6, 6),
    )
    value, cut = sb.max_flow(cap, nodes, "s", "t")
    assert value == 23
    assert sum(c for _, _, c in cut) == 23  # cut capacity == max flow
    assert ("v1", "v3", 12) in cut
    assert ("v4", "v3", 7) in cut
    assert ("v4", "t", 4) in cut


def test_capacity_projection_dedupes_reverse_edges_and_weights(store):
    con = sqlite3.connect(str(store))
    con.executemany(
        "INSERT INTO graph_edges VALUES (?, ?, ?)",
        [
            ("b", "a", 4.0),
            ("b", "a", 2.0),
            ("a", "b", 3.0),
            ("c", "d", 2.6),
        ],
    )
    con.commit()
    con.close()

    weighted, names = sb.load_capacity_projection(store)
    pos = {name: i for i, name in enumerate(names)}
    assert weighted[pos["a"], pos["b"]] == 4
    assert weighted[pos["b"], pos["a"]] == 4
    assert weighted[pos["c"], pos["d"]] == 3

    unit, unit_names = sb.load_capacity_projection(store, unweighted=True)
    unit_pos = {name: i for i, name in enumerate(unit_names)}
    assert unit[unit_pos["a"], unit_pos["b"]] == 1
    assert unit[unit_pos["c"], unit_pos["d"]] == 1


def test_yen_k_and_endpoint_guards():
    A = _undirected_csr(3, [(0, 1, 1), (1, 2, 1)])
    names = ["a", "b", "c"]
    with pytest.raises(ValueError, match="unbounded"):
        sb.yen_paths(A, names, "a", "c", k=sb.YEN_K_MAX + 1)
    with pytest.raises(ValueError, match="differ"):
        sb.yen_paths(A, names, "a", "a", k=1)
    with pytest.raises(KeyError, match="ghost"):
        sb.yen_paths(A, names, "a", "ghost", k=1)


def test_budget_guard_raises(monkeypatch):
    A = _undirected_csr(3, [(0, 1, 1), (1, 2, 1)])
    monkeypatch.setitem(sb.LANE_BUDGETS, sb.YEN_LANE, 0.0)
    with pytest.raises(RuntimeError, match="wall-clock budget"):
        sb.yen_paths(A, ["a", "b", "c"], "a", "c", k=1)


def test_switch_guard_raises(monkeypatch):
    from scipy.sparse import csr_array

    monkeypatch.setitem(sb.ROUTING, sb.MAXFLOW_LANE, "ONAGER_DEFAULT")
    cap = csr_array((np.ones(2, dtype=np.int64), ([0, 1], [1, 2])), shape=(3, 3))
    with pytest.raises(RuntimeError, match="not routed to SCIPY"):
        sb.max_flow(cap, ["a", "b", "c"], "a", "c")


def test_s_t_cli_yen_and_max_flow(store, capsys):
    assert sb.main(["yen", "--db", str(store), "--source", "a", "--sink", "c", "--k", "1"]) == 0
    yen_output = capsys.readouterr().out
    assert "yen a -> c: 1 path(s) (k=1)" in yen_output
    assert "a -> b -> c" in yen_output

    assert sb.main(["max-flow", "--db", str(store), "--source", "a", "--sink", "c"]) == 0
    max_flow_output = capsys.readouterr().out
    assert "max-flow a -> c: value=1" in max_flow_output
    assert "min-cut:" in max_flow_output


def test_s_t_cli_budget_guard(store, monkeypatch):
    monkeypatch.setitem(sb.LANE_BUDGETS, sb.YEN_LANE, 0.0)
    with pytest.raises(RuntimeError, match="wall-clock budget"):
        sb.main(["yen", "--db", str(store), "--source", "a", "--sink", "c", "--k", "1"])


@pytest.mark.live
def test_s_t_lanes_live_under_budget():
    A, names = sb.load_projection()
    paths = sb.yen_paths(A, names, "Infosys", "Wipro", k=5)
    assert paths and paths[0][1] == ["Infosys", "Wipro"]
    cap, cnames = sb.load_capacity_projection()
    value, cut = sb.max_flow(cap, cnames, "Infosys", "Wipro")
    assert value > 0 and cut


# --- scipy_exact_universe: structure lane + all-universe centralities ---


# jobs=2 structure lane fork-splits by design (fork_map CoW) — same posture
# as test_compute_jobs1_matches_jobs2 below.
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded.*:DeprecationWarning")
def test_structure_stats_golden(store):
    """P3 (a-b-c) + isolated pair (d-e): exact scalars, giant-comp radius."""
    s = sb.structure_stats(sb.load_projection(store)[0], jobs=2)
    assert s["components"] == 2
    assert s["largest_component"] == 3
    assert s["diameter"] == 2
    # radius = min ecc over the GIANT component (node b, ecc 1) — the
    # d-e pair's ecc 1 must NOT pin the global radius (it does under a
    # naive global min: the degenerate-radius defect).
    assert s["radius"] == 1
    # ordered finite pairs: 6 within the path (1+2+1+2+1+1) + 1 pair x2
    # directions in the isolated pair = 8; sum = 6+2 = 8 -> APL 1.25.
    assert s["reachable_pairs"] == 8
    assert abs(s["avg_path_length"] - 1.25) < 1e-12


def test_fused_derive_counts_wasserman_faust(store):
    """counts feed the WF factor: full-reach rows scale by 1.0, the
    2-node pair stops exploding (raw clo 4 -> 0.25)."""
    A, names = sb.load_projection(store)
    clo, harm, counts = sb.compute(A, np.arange(A.shape[0]), jobs=1, return_counts=True)
    pos = {n: i for i, n in enumerate(names)}
    assert counts[pos["d"]] == 2
    wf = clo * ((counts - 1.0) / (A.shape[0] - 1)) ** 2
    assert abs(wf[pos["a"]] - 1 / 3) < 1e-12  # r=3, sum=3: (2/4)*(2/3)
    assert abs(wf[pos["d"]] - 0.25) < 1e-12  # raw 4.0 -> 4.0 * (1/4)^2


def test_closeness_harmonic_all_universe_refuses_apply(store):
    with pytest.raises(ValueError, match="refuses --apply"):
        sb.main(
            [
                "closeness-harmonic",
                "--universe",
                "all",
                "--apply",
                "--db",
                str(store),
                "--metrics",
                "closeness",
            ]
        )


def test_closeness_harmonic_all_universe_dryrun(store, capsys):
    rc = sb.main(
        [
            "closeness-harmonic",
            "--universe",
            "all",
            "--db",
            str(store),
            "--metrics",
            "closeness",
            "--top",
            "5",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "sources: 5 (all)" in captured.err
    out = captured.out
    assert "compute-only" in out
    assert "b: 0.500000" in out  # WF top: center of the path component


# --------------------------------------------------------------------------- #
# Personalized PageRank (scipy exact-solve lane) — proposal
# doc/improvements/archive/graph/scipy_personalized_pagerank.md §4
# --------------------------------------------------------------------------- #
def test_ppr_hand_oracle_ab():
    # Hand oracle (proposal §4.1): a-b path, seed a, alpha=0.85.
    from scipy.sparse import csr_matrix as _csr

    A = _csr(np.array([[0.0, 1.0], [1.0, 0.0]]))
    x = sb.personalized_pagerank_scores(A, ["a", "b"], "a", alpha=0.85)
    # x = (I-a*P^T)^-1 (1-a)v ; P=[[0,1],[1,0]] symmetric
    # => x_a = 0.15/(1-0.85^2) = 0.5405405..., x_b = 0.85 * x_a = 0.4594594...
    assert x[0] == pytest.approx(0.15 / (1 - 0.85**2), rel=1e-9)
    assert x[1] == pytest.approx(0.85 * 0.15 / (1 - 0.85**2), rel=1e-9)
    assert float(x.sum()) == pytest.approx(1.0, abs=1e-9)


def test_ppr_dense_oracle():
    # Dense oracle (§4.2): numpy solve over the same matrix.
    import numpy.linalg as _la

    A = _star_csr(4)
    names = ["c", "l1", "l2", "l3", "l4"]
    x = sb.personalized_pagerank_scores(A, names, "l1", alpha=0.85)
    # Build P = D^-1 A (symmetric here; row-normalisable).
    deg = A.sum(axis=1).A1 if hasattr(A.sum(axis=1), "A1") else np.asarray(A.sum(axis=1)).ravel()
    P = A.toarray() / deg[:, None]
    v = np.zeros(5)
    v[1] = 1.0
    ref = _la.solve(np.eye(5) - 0.85 * P.T, 0.15 * v)
    np.testing.assert_allclose(x, ref, rtol=1e-6)


def test_ppr_seed_conditioning_and_missing():
    A = _star_csr(4)
    names = ["c", "l1", "l2", "l3", "l4"]
    x1 = sb.personalized_pagerank_scores(A, names, "l1", alpha=0.85)
    x2 = sb.personalized_pagerank_scores(A, names, "l2", alpha=0.85)
    # Seed mass follows the seed (hub degree trait — the center dominates
    # any leaf seed, but the seeded leaf outscores the other leaves).
    assert not np.allclose(x1, x2)
    assert x1[1] > x1[2] and x2[2] > x2[1]  # seeded leaf > other leaves
    with pytest.raises(KeyError, match="not in the projection"):
        sb.personalized_pagerank_scores(A, names, "nope", alpha=0.85)
    with pytest.raises(ValueError, match=r"alpha must be in \(0, 1\)"):
        sb.personalized_pagerank_scores(A, names, "l1", alpha=1.0)


def test_ppr_cli_dry_run_and_apply(tmp_path):
    spath = _exact_store(tmp_path, "pagerank_personalized")
    assert sb.main(["pagerank-personalized", "--seed", "a", "--db", str(spath), "--top", "2"]) == 0
    # dry-run wrote nothing: graph_analytics keeps its 4 pre-seeded rows,
    # ppr_results is still empty.
    scon = sqlite3.connect(str(spath))
    n_ga = scon.execute(
        "SELECT COUNT(*) FROM graph_analytics WHERE metric='pagerank_personalized'"
    ).fetchone()[0]
    n0 = scon.execute("SELECT COUNT(*) FROM ppr_results").fetchone()[0]
    scon.close()
    assert n_ga == 4  # _exact_store pre-seeds 4 rows with value '0.0'
    assert n0 == 0
    assert (
        sb.main(
            ["pagerank-personalized", "--seed", "a", "--db", str(spath), "--top", "2", "--apply"]
        )
        == 0
    )
    scon = sqlite3.connect(str(spath))
    rows = scon.execute("SELECT entity, score FROM ppr_results WHERE seed='a'").fetchall()
    n_ga_after = scon.execute(
        "SELECT COUNT(*) FROM graph_analytics WHERE metric='pagerank_personalized'"
    ).fetchone()[0]
    scon.close()
    # ppr_results_table: the PPR lane persists seed-keyed rows in
    # ppr_results; graph_analytics is never touched.
    assert n_ga_after == 4
    vals = {e: float(v) for e, v in rows}
    assert len(vals) == 2  # --top 2 truncation
    assert vals["b"] > vals["a"] > 0  # seed mass decays with path distance
    assert sum(vals.values()) < 1.0  # top-2 slice of a 4-node path


def test_ppr_dispatch_compute(tmp_path):
    # algorithms.compute dispatches the lane (seed required). Needs the
    # include-set edge type: the plain `store` fixture has no edge_type
    # column, so PPR's include-only projection would be empty there.
    spath = _exact_store(tmp_path, "pagerank_personalized")
    res = alg.compute("pagerank_personalized", seed="a", db_path=spath)
    assert res["a"] > res["d"]
    with pytest.raises(ValueError, match="requires a seed"):
        alg.compute("pagerank_personalized", db_path=spath)


def test_ppr_all_ppr_top50_tmpstore(tmp_path, monkeypatch):
    """§3.5 batch scope: 50 India large_cap seeds land in ppr_results,
    one row set per seed; graph_analytics untouched."""
    spath = tmp_path / "top50.db"
    scon = sqlite3.connect(str(spath))
    scon.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    scon.execute("CREATE TABLE entity_tags (entity_name TEXT, tag TEXT)")
    scon.execute(
        "CREATE TABLE ppr_results (seed TEXT NOT NULL, entity TEXT NOT NULL, "
        "score REAL NOT NULL, alpha REAL NOT NULL DEFAULT 0.85, "
        "computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY (seed, entity))"
    )
    names = [f"s{i:02d}" for i in range(25)]
    rows = [(names[i], names[i + 1], "cited_in") for i in range(24)]
    rows += [(names[i + 1], names[i], "cited_in") for i in range(24)]
    # 4 tagged seeds (LIMIT 50 keeps them all) with their listed_in edges.
    for n in names[:4]:
        rows.append((n, "india", "listed_in"))
        scon.execute("INSERT INTO entity_tags VALUES (?, 'market_cap/large_cap')", (n,))
    scon.executemany("INSERT INTO graph_edges VALUES (?, ?, ?)", rows)
    scon.commit()
    scon.close()

    # The batch block builds its projection via the module-level import;
    # point it at the tmp graph (25-node path) instead of the live store.
    import scipy.sparse as _sp

    a = np.zeros((25, 25))
    for i in range(24):
        a[i, i + 1] = 1.0
        a[i + 1, i] = 1.0
    A = _sp.csr_matrix(a)

    def fake_load(db_path=None, *, include_edge_types=None):
        return A, names

    def fake_connect(*_a, **_k):
        return sqlite3.connect(str(spath))

    monkeypatch.setattr(sb, "load_centrality_projection", fake_load)
    monkeypatch.setattr(alg, "connect", fake_connect)

    rc = alg._cli(["--all-ppr-top50", "--apply"])
    assert rc == 0
    scon = sqlite3.connect(str(spath))
    n_rows = scon.execute("SELECT COUNT(*) FROM ppr_results").fetchone()[0]
    n_seeds = scon.execute("SELECT COUNT(DISTINCT seed) FROM ppr_results").fetchone()[0]
    has_ga = scon.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='graph_analytics'"
    ).fetchone()[0]
    scon.close()
    assert rc == 0 and n_seeds == 4
    # top_n = PPR_TOP_DEFAULT (100) > |projection| → all positive rows/seed.
    assert n_rows == 100
    assert has_ga == 0  # batch scope never touches graph_analytics (absent here)


def test_ppr_sector_scope_hybrid(tmp_path):
    """Proposal §5c hybrid per-sector scope: solve on the seed's sector
    cohort UNION its direct relational neighbours. A same-sector node and
    a cross-sector direct neighbour enter; a cross-sector non-neighbour
    stays out (present only in the global solve)."""
    spath = tmp_path / "sector.db"
    scon = sqlite3.connect(str(spath))
    scon.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, sector_classification TEXT)")
    scon.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    scon.execute(
        "CREATE TABLE ppr_results (seed TEXT NOT NULL, entity TEXT NOT NULL, "
        "score REAL NOT NULL, alpha REAL NOT NULL DEFAULT 0.85, "
        "computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY (seed, entity))"
    )
    # a = seed (sector S); b = same sector; x = cross sector, DIRECT
    # neighbour of a; y = cross sector, NO edge to a; z = same sector,
    # linked to b (second hop within cohort).
    rows = [
        ("a", "S"),
        ("b", "S"),
        ("z", "S"),
        ("x", "T"),
        ("y", "T"),
    ]
    scon.executemany("INSERT INTO entities VALUES (?, ?)", rows)
    edges = [
        ("a", "b", "competes_with"),
        ("b", "a", "competes_with"),
        ("a", "x", "supplier_to"),
        ("x", "a", "supplier_to"),
        ("b", "z", "same_group"),
        ("z", "b", "same_group"),
        ("x", "y", "competes_with"),
        ("y", "x", "competes_with"),
    ]
    scon.executemany("INSERT INTO graph_edges VALUES (?, ?, ?)", edges)
    scon.commit()
    scon.close()

    # Sector solve: cohort(a) = {a, b, z} ∪ direct neighbours {x}; y out.
    cohort = sb.sector_cohort(str(spath), "a")
    assert cohort == {"a", "b", "z", "x"}
    # Unknown seed / no-sector-no-neighbours seed → {seed} only.
    assert sb.sector_cohort(str(spath), "ghost") == {"ghost"}

    rc = sb.main(
        ["pagerank-personalized", "--seed", "a", "--sector", "--db", str(spath), "--apply"]
    )
    assert rc == 0
    scon = sqlite3.connect(str(spath))
    got = {r[0] for r in scon.execute("SELECT entity FROM ppr_results WHERE seed='a'")}
    assert "y" not in got  # cross-sector non-neighbour excluded
    assert {"a", "b", "x"} <= got  # seed + same-sector + direct cross-sector
    scon.close()

    # Global solve (no --sector): y enters via the x–y edge.
    rc = sb.main(["pagerank-personalized", "--seed", "a", "--db", str(spath), "--apply"])
    assert rc == 0
    scon = sqlite3.connect(str(spath))
    got_global = {r[0] for r in scon.execute("SELECT entity FROM ppr_results WHERE seed='a'")}
    scon.close()
    assert "y" in got_global
    assert rc == 0
