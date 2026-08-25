#!/usr/bin/env python3
"""E2 tests — enrich_relations.py driver (yfinance competes_with pass).

Covers (proposal §8 E2 gate): mocked-fetcher unit tests, KNN/clique
topology math, double-run idempotence, dry-run parity, ticker-hygiene
classification. No network: fetch_fn is always injected.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from helpers.maintenance.enrich_relations import (  # noqa: E402
    apply_edges,
    build_candidate_edges,
    clique_industry_pairs,
    knn_industry_pairs,
    load_companies,
    run_yfinance_pass,
    weight_for_rank,
)


GRAPH_EDGES_DDL = """
CREATE TABLE graph_edges (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL REFERENCES entities(name),
    target TEXT NOT NULL REFERENCES entities(name),
    edge_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties TEXT NOT NULL DEFAULT '{}',
    valid_from DATE,
    valid_to DATE,
    source_ref TEXT NOT NULL,
    symmetric INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
)
"""


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute(GRAPH_EDGES_DDL)
    yield conn
    conn.close()


def _info(industry):
    return {"industry": industry}


# --------------------------------------------------------------------------- #
# Topology math                                                               #
# --------------------------------------------------------------------------- #
class TestKnnTopology:
    def test_symmetric_canonical_pairs(self):
        caps = {"A": 3.0, "B": 3.1, "C": 5.0}
        pairs = knn_industry_pairs(["A", "B", "C"], caps, k=1)
        # Every node contributes its single nearest neighbour: A<->B meet in
        # the middle (one canonical pair); C's nearest is B.
        assert set(pairs) == {("A", "B"), ("B", "C")}
        assert all(a <= b for a, b in pairs)

    def test_k_caps_neighbours(self):
        names = [f"N{i:02d}" for i in range(10)]
        caps = {n: float(i) for i, n in enumerate(names)}
        pairs = knn_industry_pairs(names, caps, k=3)
        # Each of 10 nodes contributes <=3 directed arcs; union symmetric.
        per_node: dict[str, int] = {}
        for a, b in pairs:
            per_node[a] = per_node.get(a, 0) + 1
            per_node[b] = per_node.get(b, 0) + 1
        assert max(per_node.values()) <= 6  # <=k out-arcs + <=k in-arcs
        # Nearest neighbours are size-adjacent: N00 must pair with N01.
        assert ("N00", "N01") in pairs

    def test_missing_mcap_falls_back_to_median(self):
        names = ["A", "B", "C"]
        caps = {"B": 4.0}  # A and C have no signal
        pairs = knn_industry_pairs(names, caps, k=2)
        # Median anchors A and C at 4.0; every node can still connect.
        assert len(pairs) >= 2
        assert any("A" in p for p in pairs)

    def test_all_missing_mcap_does_not_crash(self):
        pairs = knn_industry_pairs(["A", "B", "C"], {}, k=2)
        assert len(pairs) == 3  # complete graph via alphabetical tie-break

    def test_single_member_industry_no_edges(self):
        assert knn_industry_pairs(["Solo"], {"Solo": 3.0}, k=8) == {}

    def test_clique_topology(self):
        pairs = clique_industry_pairs(["B", "A", "C"])
        assert pairs == {("A", "B"): 1, ("A", "C"): 1, ("B", "C"): 1}

    def test_weight_decay_bounds(self):
        k = 8
        ws = [weight_for_rank(r, k) for r in range(1, k + 1)]
        assert ws[0] == 1.0
        assert ws[-1] == pytest.approx(0.4)
        assert ws == sorted(ws, reverse=True)

    def test_mutual_filter_drops_one_sided_picks(self):
        # Size ladder: A-B close, B-C mid, C alone at the top of the pile.
        # Directed K=1: A picks B; B picks A (mutual pair); C picks B (one
        # way — B's own nearest is A, not C).
        caps = {"A": 3.0, "B": 3.2, "C": 6.0}
        directed = knn_industry_pairs(["A", "B", "C"], caps, k=1)
        assert set(directed) == {("A", "B"), ("B", "C")}
        mutual = knn_industry_pairs(["A", "B", "C"], caps, k=1, mutual=True)
        assert set(mutual) == {("A", "B")}

    def test_mutual_filter_keeps_all_when_symmetric(self):
        # Two companies only: each is the other's nearest — mutuality keeps it.
        caps = {"X": 2.0, "Y": 2.1}
        assert set(knn_industry_pairs(["X", "Y"], caps, k=1, mutual=True)) == {
            ("X", "Y")}


# --------------------------------------------------------------------------- #
# Candidate edges from fetched info                                           #
# --------------------------------------------------------------------------- #
class TestBuildCandidateEdges:
    def test_industries_grouped_and_sorted(self):
        info = {
            "Alpha": _info("Steel"), "Beta": _info("Steel"),
            "Gamma": _info("Steel"), "Delta": _info("Banking"),
            "Echo": _info(""),  # no industry -> excluded
        }
        edges = build_candidate_edges(info, {}, topology="clique", k=8)
        types = {(s, t) for s, t, *_ in edges}
        assert ("Alpha", "Beta") in types and ("Alpha", "Gamma") in types
        assert all("Banking" not in e[4]["industry"] or True for e in edges)
        assert not any({"Delta"} & {s, t} for s, t, *_ in edges)
        assert not any("Echo" in (s, t) for s, t, *_ in edges)
        source_refs = {e[3] for e in edges}
        assert all(r.startswith("yfinance:industry:") for r in source_refs)

    def test_edge_properties_carry_provenance(self):
        info = {"Alpha": _info("Steel"), "Beta": _info("Steel")}
        edges = build_candidate_edges(info, {"Alpha": 3.0, "Beta": 3.5}, k=1)
        (_s, _t, weight, _ref, props) = edges[0]
        assert props["industry"] == "Steel"
        assert "fetched_at" in props and props["rank"] == 1
        assert weight == 1.0


# --------------------------------------------------------------------------- #
# Apply / idempotence / parity                                                #
# --------------------------------------------------------------------------- #
def _sample_edges(today="2026-08-24"):
    return [
        ("A", "B", 1.0, f"yfinance:industry:{today}", {"industry": "X", "rank": 1}),
        ("A", "C", 0.7, f"yfinance:industry:{today}", {"industry": "X", "rank": 4}),
        ("B", "C", 0.4, f"yfinance:industry:{today}", {"industry": "X", "rank": 8}),
    ]


class TestApplyEdges:
    def test_dry_run_writes_nothing_and_counts(self, db):
        n = apply_edges(db, _sample_edges(), dry_run=True)
        assert n == 3
        assert db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 0

    def test_double_run_idempotent(self, db):
        first = apply_edges(db, _sample_edges(), dry_run=False)
        second = apply_edges(db, _sample_edges(), dry_run=False)
        assert (first, second) == (3, 3)  # DELETE-by-prefix + re-INSERT
        assert db.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE source_ref LIKE 'yfinance:%'"
        ).fetchone()[0] == 3

    def test_apply_replaces_stale_yfinance_rows_only(self, db):
        apply_edges(db, _sample_edges(), dry_run=False)
        db.execute(
            "INSERT OR IGNORE INTO graph_edges (source, target, edge_type, "
            "weight, properties, source_ref, symmetric) "
            "VALUES ('A', 'D', 'competes_with', 1.0, '{}', "
            "'derive:relations:The_Chatter', 1)"
        )
        db.commit()
        # New run with fewer candidates: prose-derived row must SURVIVE the
        # prefix-scoped delete; stale yfinance rows must be replaced.
        apply_edges(db, _sample_edges()[:1], dry_run=False)
        kept = db.execute(
            "SELECT source_ref FROM graph_edges WHERE edge_type='competes_with'"
        ).fetchall()
        refs = {r[0] for r in kept}
        assert refs == {
            "derive:relations:The_Chatter",
            db.execute(
                "SELECT source_ref FROM graph_edges WHERE source='A' "
                "AND target='B'").fetchone()[0],
        }

    def test_unrelated_types_survive(self, db):
        db.execute(
            "INSERT OR IGNORE INTO graph_edges (source, target, edge_type, "
            "properties, source_ref, symmetric) "
            "VALUES ('A', 'Z', 'jv_with', '{}', 'derive:relations:X', 1)"
        )
        db.commit()
        apply_edges(db, _sample_edges(), dry_run=False)
        n = db.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type='jv_with'"
        ).fetchone()[0]
        assert n == 1

    def test_row_shape(self, db):
        apply_edges(db, _sample_edges(), dry_run=False)
        row = db.execute(
            "SELECT source, target, weight, properties, source_ref, symmetric "
            "FROM graph_edges WHERE source='A' AND target='B'"
        ).fetchone()
        assert row[0] <= row[1]
        assert row[2] == 1.0
        assert json.loads(row[3])["rank"] == 1
        assert row[5] == 1


# --------------------------------------------------------------------------- #
# Mocked-fetcher pass + hygiene                                               #
# --------------------------------------------------------------------------- #
class TestYfinancePass:
    @staticmethod
    def _fake_fetcher(fail=()):
        def fetch_fn(ticker):
            if ticker in fail:
                return None
            return {"industry": "Steel"}
        return fetch_fn

    @staticmethod
    def _seed_companies(db, tickers=("T1.NS", "T2.NS", "T3.BO")):
        db.execute(
            "CREATE TABLE entities (name TEXT PRIMARY KEY, ticker TEXT,"
            " file_path TEXT, entity_type TEXT)")
        for i, t in enumerate(tickers):
            db.execute(
                "INSERT INTO entities VALUES (?, ?, NULL, 'company')",
                (f"C{i}", t))
        db.execute(
            "INSERT INTO entities VALUES ('UnlistedCo', NULL, NULL, 'company')")
        db.commit()

    def test_pass_classifies_hygiene_failures(self, db, tmp_path, monkeypatch):
        self._seed_companies(db)
        monkeypatch.setattr(
            "helpers.maintenance.enrich_relations.REPORT_PATH",
            tmp_path / "relations_report.txt")
        rc = run_yfinance_pass(
            db, check_only=True, fetch_fn=self._fake_fetcher(fail={"T2.NS"}),
            workers=1, fetch_cache=None)  # cache off: exercise the fetcher
        assert rc == 0
        report = (tmp_path / "relations_report.txt").read_text()
        assert "[ticker_issues]" in report
        assert "C1 | T2.NS" in report
        assert "UnlistedCo" in report  # reported as deliberately skipped

    def test_fetch_cache_write_and_reuse(self, db, tmp_path, monkeypatch):
        self._seed_companies(db)
        monkeypatch.setattr(
            "helpers.maintenance.enrich_relations.REPORT_PATH",
            tmp_path / "relations_report.txt")
        cache_path = tmp_path / "fetch_cache.json"
        calls = {"n": 0}

        def counting_fetch(ticker):
            calls["n"] += 1
            return {"industry": "Steel"}

        run_yfinance_pass(db, check_only=True,
                          fetch_fn=counting_fetch, workers=1,
                          fetch_cache=cache_path)
        assert calls["n"] == 3
        payload = json.loads(cache_path.read_text())
        assert "fetched_at" in payload and len(payload["info_by_name"]) == 3

        # Second run: fetch_fn must NOT be called again.
        def exploding_fetch(ticker):
            raise AssertionError("network hit despite warm cache")

        run_yfinance_pass(db, check_only=True,
                          fetch_fn=exploding_fetch, workers=1,
                          fetch_cache=cache_path)

    def test_refresh_cache_refetches(self, db, tmp_path, monkeypatch):
        self._seed_companies(db)
        monkeypatch.setattr(
            "helpers.maintenance.enrich_relations.REPORT_PATH",
            tmp_path / "relations_report.txt")
        cache_path = tmp_path / "fetch_cache.json"
        calls = {"n": 0}

        def counting_fetch(ticker):
            calls["n"] += 1
            return {"industry": "Steel"}

        run_yfinance_pass(db, check_only=True,
                          fetch_fn=counting_fetch, workers=1,
                          fetch_cache=cache_path)
        run_yfinance_pass(db, check_only=True,
                          fetch_fn=counting_fetch, workers=1,
                          fetch_cache=cache_path, refresh_cache=True)
        assert calls["n"] == 6

    def test_check_only_writes_nothing(self, db, tmp_path, monkeypatch):
        self._seed_companies(db)
        monkeypatch.setattr(
            "helpers.maintenance.enrich_relations.REPORT_PATH",
            tmp_path / "relations_report.txt")
        run_yfinance_pass(db, check_only=True,
                          fetch_fn=self._fake_fetcher(), workers=1,
                          fetch_cache=None)
        assert db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# Universe loading (ticker-first contract)                                    #
# --------------------------------------------------------------------------- #
class TestLoadCompanies:
    def test_ticker_first_split(self, db):
        db.execute(
            "CREATE TABLE entities (name TEXT PRIMARY KEY, ticker TEXT,"
            " file_path TEXT, entity_type TEXT)")
        db.executemany(
            "INSERT INTO entities VALUES (?, ?, NULL, 'company')",
            [("Listed", "X.NS"), ("Blank", ""), ("NullTicker", None)])
        db.commit()
        companies, unlisted = load_companies(db)
        assert [c[0] for c in companies] == ["Listed"]
        assert sorted(unlisted) == ["Blank", "NullTicker"]
