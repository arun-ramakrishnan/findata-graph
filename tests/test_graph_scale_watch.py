"""Tests for helpers/validators/graph_scale_watch.py — the T1 tripwire.

doc/local/perf/graph_scaling.md §T1 watch promises "the advisory tripwire on
e_all_und rows" as the Phase B activation watcher. These tests pin the four
contract branches: OK, WARN (50% of T1), FIRE (exit 1 at T1), and the two
SKIP paths (missing DB, missing e_all_und) that keep a cold or rebuilding
cache from reding the advisory gate. Thresholds are monkeypatched so no test
inserts a million rows.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
graph_scale_watch = importlib.import_module("helpers.validators.graph_scale_watch")


def _mk_db(tmp_path: Path, doubled: int, directed: int | None = None) -> Path:
    db = tmp_path / "graph.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE e_all_und (a INTEGER, b INTEGER)")
    con.execute("INSERT INTO e_all_und SELECT i, i + 1 FROM range(?) t(i)", [doubled])
    if directed is not None:
        con.execute("CREATE TABLE e_dir (a INTEGER, b INTEGER)")
        con.execute("INSERT INTO e_dir SELECT i, i + 1 FROM range(?) t(i)", [directed])
    con.close()
    return db


def _run(tmp_path: Path, db: Path, monkeypatch: pytest.MonkeyPatch, warn: int, fire: int) -> int:
    monkeypatch.setattr(graph_scale_watch, "WARN_ROWS", warn)
    monkeypatch.setattr(graph_scale_watch, "T1_ROWS", fire)
    rc = graph_scale_watch.main(["--check", "--db", str(db)])
    return rc  # caller asserts on return; output asserted separately


def test_ok_below_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_db(tmp_path, doubled=100, directed=50)
    rc = _run(tmp_path, db, monkeypatch, warn=500, fire=1000)
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out
    assert "e_all_und=100" in out
    assert "0.10 of T1" in out


def test_warn_at_half_of_t1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_db(tmp_path, doubled=600, directed=300)
    rc = _run(tmp_path, db, monkeypatch, warn=500, fire=1000)
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARN" in out
    assert "record the growth" in out


def test_fire_at_t1_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_db(tmp_path, doubled=1000, directed=500)
    rc = _run(tmp_path, db, monkeypatch, warn=500, fire=1000)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FIRE" in out
    assert "Phase B" in out


def test_skip_when_db_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run(tmp_path, tmp_path / "absent.duckdb", monkeypatch, warn=500, fire=1000)
    out = capsys.readouterr().out
    assert rc == 0
    assert "SKIP" in out


def test_skip_when_e_all_und_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "graph.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE other (a INTEGER)")
    con.close()
    rc = _run(tmp_path, db, monkeypatch, warn=500, fire=1000)
    out = capsys.readouterr().out
    assert rc == 0
    assert "not built yet" in out


def test_live_thresholds_match_doctrine() -> None:
    """The shipped constants must equal the recorded doctrine, not drift."""
    assert graph_scale_watch.T1_ROWS == 1_000_000
    assert graph_scale_watch.WARN_ROWS == 500_000


# --- chains-cap watch (longest_chains_cap_policy S3) -------------------------


def _mk_research(tmp_path: Path, edge_touched: int, index_only: int = 0) -> Path:
    """Tiny SQLite store: `edge_touched` entities on real edges +
    `index_only` entities on listed_on_index edges ONLY (must not count)."""
    import sqlite3

    db = tmp_path / "research.db"
    con = sqlite3.connect(str(db))
    con.executescript(
        "CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT);"
        "CREATE TABLE graph_edges (id INTEGER PRIMARY KEY, source TEXT, target TEXT,"
        " edge_type TEXT, weight REAL DEFAULT 1.0);"
    )
    names = [f"e{i}" for i in range(edge_touched + index_only)]
    con.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')",
        ((n,) for n in names),
    )
    for i in range(edge_touched - 1):
        con.execute(
            "INSERT INTO graph_edges (source, target, edge_type) VALUES (?, ?, 'supplier_to')",
            (names[i], names[i + 1]),
        )
    for i in range(index_only):
        con.execute(
            "INSERT INTO graph_edges (source, target, edge_type) VALUES (?, ?, 'listed_on_index')",
            (names[edge_touched + i], names[0]),
        )
    con.commit()
    con.close()
    return db


def test_chains_cap_sampled_line_above_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_db(tmp_path, doubled=10, directed=5)
    research = _mk_research(tmp_path, edge_touched=7)
    monkeypatch.setattr(graph_scale_watch, "CHAIN_CAP", 5)
    rc = graph_scale_watch.main(["--check", "--db", str(db), "--research-db", str(research)])
    out = capsys.readouterr().out
    assert rc == 0, "sampled mode is sanctioned — never a gate failure"
    assert "chains-cap-watch: SAMPLED" in out
    assert "LOWER BOUNDS" in out
    assert "1.4x" in out  # 7/5


def test_chains_cap_exact_line_below_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_db(tmp_path, doubled=10, directed=5)
    research = _mk_research(tmp_path, edge_touched=4)
    monkeypatch.setattr(graph_scale_watch, "CHAIN_CAP", 5)
    rc = graph_scale_watch.main(["--check", "--db", str(db), "--research-db", str(research)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "chains-cap-watch: exact" in out


def test_chains_cap_index_only_entities_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """listed_on_index-only entities are NOT edge-touched (the stats.py
    universe rule) — 4 real + 3 index-only must read n=4, not n=7."""
    db = _mk_db(tmp_path, doubled=10, directed=5)
    research = _mk_research(tmp_path, edge_touched=4, index_only=3)
    monkeypatch.setattr(graph_scale_watch, "CHAIN_CAP", 5)
    graph_scale_watch.main(["--check", "--db", str(db), "--research-db", str(research)])
    out = capsys.readouterr().out
    assert "chains-cap-watch: exact" in out  # 4 <= 5 — the index-only 3 did not count


def test_chains_cap_skip_missing_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_db(tmp_path, doubled=10, directed=5)
    monkeypatch.setattr(graph_scale_watch, "CHAIN_CAP", 5)
    rc = graph_scale_watch.main(
        ["--check", "--db", str(db), "--research-db", str(tmp_path / "nope.db")]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "chains-cap-watch: SKIP" in out


def test_chains_cap_constant_matches_stats() -> None:
    """No drift: the watcher trips on the SAME constant the diagnostic
    defaults to (imported, not duplicated)."""
    from helpers.graph.stats import CHAIN_CAP

    assert graph_scale_watch.CHAIN_CAP == CHAIN_CAP == 6000


# --- hyper-scale watch (2026-11-02 re-anchor) ---------------------------------


def _mk_hyper_db(
    tmp_path: Path,
    groups: tuple[int, ...],
    doubled: int = 10,
    directed: int = 5,
) -> Path:
    """Cache with the dyadic tables AND h_incidence (edge_id -> members)."""
    db = _mk_db(tmp_path, doubled=doubled, directed=directed)
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE h_incidence (edge_id INTEGER, entity_name VARCHAR)")
    con.executemany(
        "INSERT INTO h_incidence VALUES (?, ?)",
        [(e, f"e{e}_n{i}") for e, m in enumerate(groups) for i in range(m)],
    )
    con.close()
    return db


def test_hyper_ok_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_hyper_db(tmp_path, groups=(2, 2, 3))  # 7 incidences, max 3, tax C(2,2)x2 + C(3,2) = 5
    rc = graph_scale_watch.main(["--check", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "hyper-scale-watch: h_incidence=7" in out
    assert "max hyperedge=3" in out
    assert "clique tax=5 dyads" in out
    assert "OK" in out


def test_hyper_warn_at_bench_posture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_hyper_db(tmp_path, groups=(3, 3))  # 6 incidences
    monkeypatch.setattr(graph_scale_watch, "H_INCIDENCE_WARN", 5)
    rc = graph_scale_watch.main(["--check", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0, "WARN is advisory — never a gate failure"
    assert "WARN" in out
    assert "re-anchor hyper_scale_bench" in out


def test_hyper_fire_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_hyper_db(tmp_path, groups=(3, 3))
    monkeypatch.setattr(graph_scale_watch, "H_INCIDENCE_FIRE", 5)
    rc = graph_scale_watch.main(["--check", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 1, "h_incidence FIRE is the only exit-1 hyper axis"
    assert "FIRE" in out
    assert "hyper Phase B" in out


def test_hyper_skip_when_tables_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_db(tmp_path, doubled=10, directed=5)  # no h_incidence
    rc = graph_scale_watch.main(["--check", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "hyper-scale-watch: SKIP" in out
    assert "h_incidence not built yet" in out


def test_hyper_clique_tax_warn_at_oom_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_hyper_db(tmp_path, groups=(3, 3))  # tax = 6
    monkeypatch.setattr(graph_scale_watch, "CLIQUE_TAX_WARN", 5)
    rc = graph_scale_watch.main(["--check", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0, "clique tax reports the wall's approach — never a gate failure"
    assert "OOM anchor" in out
    assert "infeasible" in out


def test_hyper_max_edge_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _mk_hyper_db(tmp_path, groups=(3, 1))
    monkeypatch.setattr(graph_scale_watch, "MAX_HYPEREDGE_WARN", 2)
    rc = graph_scale_watch.main(["--check", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "max hyperedge 3 >= 2" in out
    assert "max_k" in out


def test_dyadic_fire_does_not_mask_hyper_watch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sections are independent: a dyadic T1 FIRE must not skip the hyper axes."""
    db = _mk_hyper_db(tmp_path, groups=(2, 2))
    monkeypatch.setattr(graph_scale_watch, "T1_ROWS", 5)  # doubled=10 >= 5 -> dyadic FIRE
    rc = graph_scale_watch.main(["--check", "--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FIRE" in out
    assert "hyper-scale-watch: h_incidence=4" in out


def test_live_hyper_thresholds_match_doctrine() -> None:
    """Shipped hyper constants must equal the recorded anchors, not drift."""
    assert graph_scale_watch.H_INCIDENCE_WARN == 100_000
    assert graph_scale_watch.H_INCIDENCE_FIRE == 250_000
    assert graph_scale_watch.MAX_HYPEREDGE_WARN == 1_000
    assert graph_scale_watch.CLIQUE_TAX_WARN == 3_400_000
