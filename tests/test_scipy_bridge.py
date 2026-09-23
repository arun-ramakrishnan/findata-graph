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
        "value VARCHAR, PRIMARY KEY (metric, entity_name))"
    )
    scon.executemany(
        "INSERT INTO graph_analytics VALUES (?, 'closeness_centrality', ?)",
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
    assert routed == {"closeness_centrality", "harmonic_centrality"}
    assert set(sb.ROUTING) <= set(alg._METRIC_DISPATCH) | {"link_prediction"}
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


def test_dispatch_compute_plumbs_db_path(store):
    got = alg.compute("harmonic_centrality", edges=None, db_path=store)
    assert set(got) == set(_CONTRACT)
    assert got["e"] == pytest.approx(1.0)


def test_dispatch_drift_fails_loud(store):
    scon = sqlite3.connect(str(store))
    scon.execute("INSERT INTO graph_analytics VALUES ('ghost', 'closeness_centrality', '0.0')")
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
        "value VARCHAR, PRIMARY KEY (metric, entity_name))"
    )
    scon.execute("INSERT INTO graph_analytics VALUES ('a', 'closeness_centrality', '0.0')")
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


def test_dispatch_live_closeness_exact_vs_incumbents():
    import json as _json

    def _val(s):
        try:
            return float(s)
        except ValueError:
            return float(_json.loads(s)["value"])

    got = alg._run_closeness(None, edges=None)
    scon = sqlite3.connect("memory/research.db")
    try:
        inc = {
            r[0]: _val(r[1])
            for r in scon.execute(
                "SELECT entity_name, value FROM graph_analytics WHERE metric='closeness_centrality'"
            ).fetchall()
        }
    finally:
        scon.close()
    assert set(got) == set(inc) and len(got) == 1734
    assert max(abs(got[k] - inc[k]) for k in inc) == 0.0


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
    scon.executemany(
        "INSERT INTO graph_edges VALUES (?, ?, 'cited_in')",
        [("a", "b"), ("b", "c"), ("c", "d")],
    )
    scon.execute(
        "CREATE TABLE graph_analytics (entity_name VARCHAR, metric VARCHAR, "
        "value VARCHAR, PRIMARY KEY (metric, entity_name))"
    )
    scon.executemany(
        "INSERT INTO graph_analytics VALUES (?, ?, '0.0')",
        [(n, metric) for n in ["a", "b", "c", "d"]],
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
