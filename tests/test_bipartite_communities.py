"""Tests for the bipartite-louvain community lane (Wall 4 S3)."""

from __future__ import annotations

from helpers.graph.bipartite_communities import (
    bipartite_louvain_communities,
    _community_report,
)


def test_bipartite_louvain_recovers_planted_communities():
    """On a planted 2-community incidence the lane recovers the partition."""
    # hub "h0" links {n0,n1,n2}, hub "h1" links {n3,n4,n5}
    pairs = [
        ("h0", "n0"),
        ("h0", "n1"),
        ("h0", "n2"),
        ("h1", "n3"),
        ("h1", "n4"),
        ("h1", "n5"),
    ]
    comm = bipartite_louvain_communities(pairs)
    assert comm is not None
    # n0..n2 share a community; n3..n5 share a community
    assert comm["n0"] == comm["n1"] == comm["n2"]
    assert comm["n3"] == comm["n4"] == comm["n5"]
    assert comm["n0"] != comm["n3"]
    assert len({comm[n] for n in ("n0", "n1", "n2")}) == 1
    assert len({comm[n] for n in ("n3", "n4", "n5")}) == 1


def test_bipartite_louvain_fewer_than_two_returns_none():
    assert bipartite_louvain_communities([("h0", "n0")]) is None


def test_community_report_lines():
    comm = {"n0": 0, "n1": 0, "n2": 1}
    lines = _community_report(comm)
    assert lines[0].startswith("bipartite-louvain:")
    assert any("block 0" in line for line in lines)
    assert any("block 1" in line for line in lines)


def test_load_pairs_degrades_on_missing_store(monkeypatch):
    """When the star store has no h_incidence the lane returns None, not a crash."""
    import helpers.misc.duckdb_lock as dlock

    class FakeRow:
        def __getitem__(self, i):
            return 0  # information_schema row: 0 tables named h_incidence

        def fetchone(self):
            return self

    class FakeCon:
        def execute(self, sql, params=None):
            if "information_schema" in sql:
                return FakeRow()
            raise AssertionError(f"Unexpected SQL: {sql}")

        def close(self):
            return None

    def fake_open_read_only(_path):
        return FakeCon()

    from helpers.graph import bipartite_communities as mod

    monkeypatch.setattr(dlock, "open_read_only", fake_open_read_only)
    assert mod._load_pairs() is None
