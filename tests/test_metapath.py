"""graph_relational_depth R1 — typed metapath layer (helpers/graph/metapath.py).

Pins the three load-bearing contracts:
- the semantics registry routes every edge type to its family with the
  LIVE orientations (subsidiary_of child→parent; acquired stored
  acquirer→acquiree and joined REVERSED; customer_of mirrored into the
  supplier orientation — R6 audit);
- vigil subsidiary_of rows are trusted ONLY on the strict positive
  relationship vocabulary (the TCS→Rallis India false-parentage class);
- BFS is deterministic, hop-bounded, and explains every hop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers.core.db import connect
from helpers.graph import metapath

# (source, target, edge_type, source_ref, properties-or-None)
SCRATCH_ROWS = [
    # prose ownership chain: Leaf → Mid → Apex (child→parent)
    ("LeafCo", "MidCo", "subsidiary_of", "derive:relations:n1", None),
    ("MidCo", "ApexCo", "subsidiary_of", "derive:relations:n1", None),
    # vigil strict positive → trusted
    (
        "KidCo",
        "ParentCo",
        "subsidiary_of",
        "vigil:rpt:PARENT",
        {"relationship": "Wholly Owned Subsidiary"},
    ),
    # vigil third-party-parentage description → EXCLUDED (the live
    # TCS→Rallis India pollution class)
    (
        "SiblingA",
        "SiblingB",
        "subsidiary_of",
        "vigil:rpt:SIB",
        {
            "relationship": "Subsidiary/Joint Venture of an Entity with "
            "Significant Influence on Holding Company"
        },
    ),
    # vigil subsidiary-ADJACENT party → EXCLUDED
    (
        "KmpCo",
        "SomeCo",
        "subsidiary_of",
        "vigil:rpt:KMP",
        {"relationship": "Key management personnel of Subsidiary"},
    ),
    # vigil plain holding company → trusted (counter IS the filer's parent)
    ("SubCo", "HoldCo", "subsidiary_of", "vigil:rpt:HOLD", {"relationship": "Holding Company"}),
    # acquired: stored acquirer→acquiree; ownership joins REVERSED
    ("BigCo", "TargetCo", "acquired", "derive:relations:n2", None),
    # flow: source is always the supplier
    ("SupCo", "CustCo", "supplier_to", "vigil:rpt:SUP", {"relationship": "Subsidiary"}),
    # customer_of: stored customer→supplier, mirrored into flow
    ("CustX", "SupX", "customer_of", "derive:relations:n3", None),
    # affiliation (undirected)
    ("AffA", "AffB", "same_group", "vigil:rpt:G", {"relationship": "Fellow Subsidiary"}),
    ("AffB", "AffC", "listed_on_index", "derive:index", None),
    # competition
    ("CompA", "CompB", "competes_with", "derive:relations:n4", None),
    # bridge for any-semantics cross-family paths
    ("MidCo", "AffB", "competes_with", "derive:relations:n4", None),
    # peripheral: joins only semantics="any"
    ("RateCo", "LeafCo", "rated_by", "derive:ratings", None),
]


@pytest.fixture()
def scratch_db(tmp_path: Path) -> Path:
    metapath.clear_cache()
    db = tmp_path / "scratch.db"
    con = connect(str(db))
    try:
        con.executescript(
            """
            CREATE TABLE graph_edges (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                properties TEXT,
                source_ref TEXT DEFAULT ''
            )
            """
        )
        con.executemany(
            "INSERT INTO graph_edges (source, target, edge_type, properties, source_ref) "
            "VALUES (?, ?, ?, ?, ?)",
            [(s, t, et, json.dumps(p) if p else None, ref) for s, t, et, ref, p in SCRATCH_ROWS],
        )
        # R5 hyper s-paths: incidence store for the same universe
        con.executescript(
            """
            CREATE TABLE hyper_edges (id INTEGER PRIMARY KEY, edge_type TEXT, label TEXT);
            CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name TEXT);
            """
        )
        con.executemany(
            "INSERT INTO hyper_edges VALUES (?,?,?)",
            [
                (1, "group", "Alpha Group"),
                (2, "edition", "Chatter #69"),
                (3, "sub_sector", "Pharma"),
                (4, "edition", "Bridge Ed"),
            ],
        )
        con.executemany(
            "INSERT INTO hyper_incidences VALUES (?,?)",
            [
                # AffA/AffB share TWO hyperedges -> s-distance 1
                (1, "AffA"),
                (1, "AffB"),
                (2, "AffA"),
                (2, "AffB"),
                # LeafCo -(sub_sector:Pharma)-> MidCo -(edition:Bridge Ed)-> CompA
                (3, "LeafCo"),
                (3, "MidCo"),
                (4, "MidCo"),
                (4, "CompA"),
            ],
        )
        con.commit()
    finally:
        con.close()
    return db


def _c(db: Path, a: str, b: str, semantics=None, hops=3):
    return metapath.connect(a, b, semantics, hops, db)


class TestOwnership:
    def test_chain_up(self, scratch_db):
        r = _c(scratch_db, "LeafCo", "ApexCo", "ownership")
        assert r["path"] == ["LeafCo", "MidCo", "ApexCo"]
        assert [h["edge_type"] for h in r["hops"]] == ["subsidiary_of", "subsidiary_of"]
        assert all(h["direction"] == "up" for h in r["hops"])

    def test_descendant_walk_against_stored_edge(self, scratch_db):
        r = _c(scratch_db, "ApexCo", "LeafCo", "ownership")
        assert r["path"] == ["ApexCo", "MidCo", "LeafCo"]
        assert all(h["direction"] == "down" for h in r["hops"])

    def test_vigil_strict_positive_trusted(self, scratch_db):
        r = _c(scratch_db, "KidCo", "ParentCo", "ownership")
        assert r["n_hops"] == 1

    def test_vigil_plain_holding_company_trusted(self, scratch_db):
        assert _c(scratch_db, "SubCo", "HoldCo", "ownership")["n_hops"] == 1

    def test_vigil_third_party_parentage_excluded(self, scratch_db):
        assert _c(scratch_db, "SiblingA", "SiblingB", "ownership") is None

    def test_ownership_parents_accessor_for_derive_lanes(self, scratch_db):
        # R3 consume seam: closure lanes share connect()'s trust filter
        parents = metapath.ownership_parents(scratch_db)
        assert ("MidCo", "subsidiary_of") in parents["LeafCo"]
        assert ("ApexCo", "subsidiary_of") in parents["MidCo"]
        assert ("HoldCo", "subsidiary_of") in parents["SubCo"]
        # acquired joins REVERSED (acquiree -> acquirer)
        assert ("BigCo", "acquired") in parents["TargetCo"]
        # untrusted vigil descriptions + non-ownership types never appear
        assert "SiblingA" not in parents
        assert "KmpCo" not in parents
        assert "SupCo" not in parents

    def test_vigil_kmp_adjacent_excluded(self, scratch_db):
        assert _c(scratch_db, "KmpCo", "SomeCo", "ownership") is None

    def test_acquired_joins_reversed(self, scratch_db):
        r = _c(scratch_db, "TargetCo", "BigCo", "ownership")
        assert r["path"] == ["TargetCo", "BigCo"]
        assert r["hops"][0]["edge_type"] == "acquired"

    def test_hop_bound(self, scratch_db):
        assert _c(scratch_db, "LeafCo", "ApexCo", "ownership", hops=1) is None


class TestFlow:
    def test_downstream_along_stored_orientation(self, scratch_db):
        r = _c(scratch_db, "SupCo", "CustCo", "flow")
        assert r["path"] == ["SupCo", "CustCo"]
        assert r["hops"][0]["edge_type"] == "supplier_to"
        assert r["hops"][0]["direction"] == "downstream"

    def test_upstream_reverse_search(self, scratch_db):
        r = _c(scratch_db, "CustCo", "SupCo", "flow")
        assert r["n_hops"] == 1
        assert r["hops"][0]["direction"] == "upstream"

    def test_customer_of_mirrored_into_supplier_orientation(self, scratch_db):
        r = _c(scratch_db, "SupX", "CustX", "flow")
        assert r["n_hops"] == 1
        assert r["hops"][0]["edge_type"] == "customer_of"


class TestUndirectedFamilies:
    def test_affiliation_two_hop(self, scratch_db):
        r = _c(scratch_db, "AffA", "AffC", "affiliation")
        assert r["path"] == ["AffA", "AffB", "AffC"]
        assert {h["edge_type"] for h in r["hops"]} == {"same_group", "listed_on_index"}

    def test_affiliation_reverse_direction(self, scratch_db):
        assert _c(scratch_db, "AffC", "AffA", "affiliation")["n_hops"] == 2

    def test_competition(self, scratch_db):
        assert _c(scratch_db, "CompA", "CompB", "competition")["n_hops"] == 1


class TestAnyAndRegistry:
    def test_any_crosses_families(self, scratch_db):
        r = _c(scratch_db, "LeafCo", "AffC", "any")
        assert r["path"] == ["LeafCo", "MidCo", "AffB", "AffC"]

    def test_peripheral_type_joins_any_only(self, scratch_db):
        r = _c(scratch_db, "RateCo", "LeafCo", "any")
        assert r["hops"][0]["edge_type"] == "rated_by"

    def test_unknown_semantics_raises(self, scratch_db):
        with pytest.raises(ValueError, match="unknown semantics"):
            _c(scratch_db, "A", "B", "teleportation")

    def test_self_connection_zero_hop(self, scratch_db):
        r = _c(scratch_db, "LeafCo", "LeafCo", "ownership")
        assert r == {
            "semantics": "ownership",
            "path": ["LeafCo"],
            "hops": [],
            "n_hops": 0,
        }


class TestContract:
    def test_unreachable_is_none(self, scratch_db):
        assert _c(scratch_db, "LeafCo", "CustCo", "ownership") is None

    def test_deterministic_repeat(self, scratch_db):
        assert _c(scratch_db, "LeafCo", "AffC", "any") == _c(scratch_db, "LeafCo", "AffC", "any")

    def test_cli_smoke(self, scratch_db, capsys):
        rc = metapath.main(["SupCo", "CustCo", "--semantics", "flow", "--db", str(scratch_db)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "supplier_to" in out and "downstream" in out

    def test_cli_no_connection_exit_1(self, scratch_db, capsys):
        rc = metapath.main(
            ["LeafCo", "CustCo", "--semantics", "ownership", "--db", str(scratch_db)]
        )
        assert rc == 1
        assert "no connection" in capsys.readouterr().out


def _reference_tables(db_path: Path) -> tuple[dict[int, set[str]], dict[str, set[int]]]:
    """Naive incidence re-read straight from the tables (parity oracle)."""
    import collections
    import sqlite3

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    members: dict[int, set[str]] = collections.defaultdict(set)
    for eid, ent in con.execute("SELECT edge_id, entity_name FROM hyper_incidences"):
        members[eid].add(ent)
    of: dict[str, set[int]] = collections.defaultdict(set)
    for eid, ents in members.items():
        for e in ents:
            of[e].add(eid)
    return members, of


def _ref_dist(members: dict[int, set[str]], of: dict[str, set[int]], a: str, b: str, cap: int = 4):
    """Naive reference BFS: set-expansion s-distance, no determinism rules."""
    if a == b:
        return 0
    seen, frontier = {a}, {a}
    for d in range(1, cap + 1):
        nxt: set[str] = set()
        for n in frontier:
            for eid in of[n]:
                nxt |= members[eid]
        nxt -= seen
        if b in nxt:
            return d
        seen |= nxt
        frontier = nxt
    return None


class TestHyperSPaths:
    """R5: s-walks over the incidence store as the hyper explanation leg."""

    def test_shared_hyperedges_distance_one(self, scratch_db):
        r = metapath.hyper_path("AffA", "AffB", db_path=scratch_db)
        assert r["s_distance"] == 1
        assert r["shared"] == [("edition", "Chatter #69"), ("group", "Alpha Group")]
        assert r["walk"] == ["AffA", "AffB"]
        assert metapath.shared_hyperedges("AffA", "AffB", scratch_db) == r["shared"]

    def test_two_hop_s_walk_via_bridging_entity(self, scratch_db):
        r = metapath.hyper_path("LeafCo", "CompA", max_hops=2, db_path=scratch_db)
        assert r["s_distance"] == 2
        assert r["shared"] == []
        assert r["walk"] == ["LeafCo", "sub_sector:Pharma", "MidCo", "edition:Bridge Ed", "CompA"]

    def test_unreachable_self_and_hop_bound(self, scratch_db):
        # no incidences at all -> unreachable
        assert metapath.hyper_path("RateCo", "CompA", db_path=scratch_db) is None
        # disjoint incidence components -> unreachable within the cap
        assert metapath.hyper_path("AffA", "KidCo", db_path=scratch_db) is None
        # hop bound: LeafCo/CompA need 2 hyperedge hops
        assert metapath.hyper_path("LeafCo", "CompA", max_hops=1, db_path=scratch_db) is None
        r = metapath.hyper_path("AffA", "AffA", db_path=scratch_db)
        assert r["s_distance"] == 0 and r["walk"] == ["AffA"]

    def test_connect_dispatches_hyper_semantics(self, scratch_db):
        r = _c(scratch_db, "AffA", "AffB", "hyper")
        assert r["semantics"] == "hyper" and r["s_distance"] == 1

    def test_deterministic_repeat(self, scratch_db):
        assert metapath.hyper_path("LeafCo", "CompA", db_path=scratch_db) == metapath.hyper_path(
            "LeafCo", "CompA", db_path=scratch_db
        )

    def test_parity_vs_inlined_reference(self, scratch_db):
        """R5 acceptance: s-distances match a naive reference BFS over the
        same incidence store (reference re-reads the tables directly)."""
        members, of = _reference_tables(scratch_db)

        pairs = (
            ("AffA", "AffB"),
            ("LeafCo", "CompA"),
            ("MidCo", "CompA"),
            ("AffA", "KidCo"),
            ("LeafCo", "RateCo"),
            ("MidCo", "AffA"),
            ("CompA", "LeafCo"),
            ("SubCo", "HoldCo"),
        )
        for a, b in pairs:
            got = metapath.hyper_path(a, b, max_hops=4, db_path=scratch_db)
            got_d = None if got is None else got["s_distance"]
            assert got_d == _ref_dist(members, of, a, b), (a, b, got_d)

    def test_cli_hyper_format(self, scratch_db, capsys):
        rc = metapath.main(["AffA", "AffB", "--semantics", "hyper", "--db", str(scratch_db)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "s-distance 1" in out and "group: Alpha Group" in out
