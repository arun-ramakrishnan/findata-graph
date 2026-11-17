"""graph_relational_depth R3 — derived relational-insight lanes
(helpers/graph/derive_relational_insights.py).

Hermetic per-lane contract tests: closure + adjudication (min-claimant,
cycle guard, coalition annotation), hidden-connection filter floors,
chokepoint/overlap thresholds, and idempotent apply with a stable
content digest. No network; the fixture mirrors the production
entities/graph_edges shape on a tmp store.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from helpers.graph import derive_relational_insights as dri
from helpers.graph import metapath


def _edge(conn, s, t, et, ref, props=None):
    conn.execute(
        "INSERT INTO graph_edges (source, target, edge_type, weight, properties, source_ref) "
        "VALUES (?,?,?,?,?,?)",
        (s, t, et, 1.0, json.dumps(props) if props is not None else "{}", ref),
    )


@pytest.fixture()
def db(tmp_path: Path):
    metapath.clear_cache()
    path = tmp_path / "research.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE entities (
            name TEXT PRIMARY KEY, entity_type TEXT NOT NULL,
            normalized_name TEXT, file_path TEXT);
        CREATE TABLE graph_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL REFERENCES entities(name),
            target TEXT NOT NULL REFERENCES entities(name),
            edge_type TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
            properties TEXT NOT NULL DEFAULT '{}',
            valid_from DATE, valid_to DATE, source_ref TEXT NOT NULL,
            symmetric INTEGER NOT NULL DEFAULT 0, source_tier TEXT,
            UNIQUE(source, target, edge_type), CHECK (source != target));
        """
    )
    names = (
        # ultimate_parent cast
        ("LeafLtd", "company"),
        ("MidLtd", "company"),
        ("ApexLtd", "company"),
        ("SoloLtd", "company"),
        ("CycX", "company"),
        ("CycY", "company"),
        ("AcqTarget", "company"),
        ("Acquirer", "company"),
        ("MPChild", "company"),
        ("AParent", "company"),
        ("ZParent", "company"),
        ("UntrustedKid", "company"),
        ("Sub-Total (A)(1)", "institution"),
        # hidden_connections cast — intermediates are institution-typed so
        # they cannot qualify as (company) endpoints and blur the pairs
        ("CoP", "company"),
        ("CoQ", "company"),
        ("CoR", "company"),
        ("CoS", "company"),
        ("NicheSup", "institution"),
        ("JvMid", "institution"),
        ("HubSup", "institution"),
        # chokepoint / overlap cast
        ("ChokeSup", "company"),
        ("WeakSup", "company"),
        ("C1", "company"),
        ("C2", "company"),
        ("C3", "company"),
        ("CoA", "company"),
        ("CoB", "company"),
        ("CoC", "company"),
        ("CoD", "company"),
    )
    for name, etype in names:
        conn.execute(
            "INSERT INTO entities VALUES (?, ?, ?, NULL)",
            (name, etype, name.replace(" ", "_")),
        )
    for i in range(6):  # hub intermediate neighbourhood (degree 6 <= default cap)
        conn.execute(
            "INSERT INTO entities VALUES (?, 'company', ?, NULL)",
            (f"Hub{i}", f"Hub{i}"),
        )
        _edge(conn, "HubSup", f"Hub{i}", "supplier_to", "vigil:rpt:H")

    # --- ultimate_parent seed ------------------------------------------ #
    _edge(conn, "LeafLtd", "MidLtd", "subsidiary_of", "derive:relations:n1")
    _edge(conn, "MidLtd", "ApexLtd", "subsidiary_of", "derive:relations:n1")
    # vigil UNTRUSTED text (the R6a sibling-description class) -> excluded
    _edge(
        conn,
        "UntrustedKid",
        "ApexLtd",
        "subsidiary_of",
        "vigil:rpt:U",
        {"relationship": "Subsidiary of Holding Company"},
    )
    # cycle
    _edge(conn, "CycX", "CycY", "subsidiary_of", "derive:relations:c")
    _edge(conn, "CycY", "CycX", "subsidiary_of", "derive:relations:c")
    # acquired stored acquirer->acquiree; closure joins REVERSED
    _edge(conn, "Acquirer", "AcqTarget", "acquired", "derive:relations:a")
    # multi-parent child -> min-name claimant (metapath BFS convention)
    _edge(conn, "MPChild", "ZParent", "subsidiary_of", "derive:relations:m")
    _edge(conn, "MPChild", "AParent", "subsidiary_of", "derive:relations:m")
    # promoter-group aggregate (an R6b category-holder row) on the self-root
    _edge(
        conn,
        "Sub-Total (A)(1)",
        "SoloLtd",
        "invested_in",
        "nse:shp:BSE_S",
        {"stake_pct": 64.08, "category_holder": True},
    )
    # --- hidden_connections seed --------------------------------------- #
    # CoP/CoQ share TWO intermediates (supplier + jv) -> kept
    _edge(conn, "NicheSup", "CoP", "supplier_to", "vigil:rpt:S")
    _edge(conn, "NicheSup", "CoQ", "supplier_to", "vigil:rpt:S")
    _edge(conn, "NicheSup", "CoS", "supplier_to", "vigil:rpt:S")
    _edge(conn, "JvMid", "CoP", "jv_with", "derive:relations:j")
    _edge(conn, "JvMid", "CoQ", "jv_with", "derive:relations:j")
    _edge(conn, "JvMid", "CoR", "jv_with", "derive:relations:j")
    _edge(conn, "JvMid", "CoS", "jv_with", "derive:relations:j")
    # CoP/CoS share 2 intermediaries BUT carry a direct structural edge -> excluded
    _edge(conn, "CoP", "CoS", "same_group", "derive:relations:g")
    conn.commit()
    yield path, conn
    conn.close()
    metapath.clear_cache()


def _rows(conn, table, *cols):
    order = ", ".join(cols) if cols else "*"
    return conn.execute(f"SELECT {order} FROM {table}").fetchall()  # noqa: S608 — test-local


class TestUltimateParent:
    def test_chain_depth_and_root_kinds(self, db):
        path, conn = db
        dri.run_lane(conn, "ultimate-parent", apply_=True, db_path=path)
        rows = {r[0]: r for r in _rows(conn, "ultimate_parent")}
        assert rows["LeafLtd"][1:5] == (
            "ApexLtd",
            2,
            json.dumps(["LeafLtd", "MidLtd", "ApexLtd"]),
            "chain-root",
        )
        assert rows["MidLtd"][1:3] == ("ApexLtd", 1)
        # the apex has no parent of its own: self-root at depth 0
        assert rows["ApexLtd"][1:3] == ("ApexLtd", 0)
        assert rows["ApexLtd"][4] == "self-root"

    def test_self_root_carries_coalition_annotation(self, db):
        path, conn = db
        dri.run_lane(conn, "ultimate-parent", apply_=True, db_path=path)
        rows = {r[0]: r for r in _rows(conn, "ultimate_parent")}
        assert rows["SoloLtd"][1:3] == ("SoloLtd", 0)
        assert rows["SoloLtd"][4] == "self-root"
        assert rows["SoloLtd"][5] == pytest.approx(64.08)
        # chain children never carry the coalition annotation
        assert rows["LeafLtd"][5] is None

    def test_untrusted_vigil_text_is_self_root(self, db):
        path, conn = db
        dri.run_lane(conn, "ultimate-parent", apply_=True, db_path=path)
        rows = {r[0]: r for r in _rows(conn, "ultimate_parent")}
        assert rows["UntrustedKid"][1:3] == ("UntrustedKid", 0)
        assert rows["UntrustedKid"][4] == "self-root"

    def test_cycle_guard_acquired_reversed_min_claimant(self, db):
        path, conn = db
        dri.run_lane(conn, "ultimate-parent", apply_=True, db_path=path)
        rows = {r[0]: r for r in _rows(conn, "ultimate_parent")}
        assert rows["CycX"][4] == "cycle-guard"
        assert rows["CycY"][4] == "cycle-guard"
        # acquired: stored Acquirer->AcqTarget, closure parents AcqTarget to Acquirer
        assert rows["AcqTarget"][1:3] == ("Acquirer", 1)
        # multi-parent child takes the min-name claimant
        assert rows["MPChild"][1:3] == ("AParent", 1)


class TestHiddenConnections:
    def test_floor_direct_edge_and_hub_behaviour(self, db):
        path, conn = db
        dri.run_lane(conn, "hidden-connections", apply_=True, db_path=path)
        rows = {(r[0], r[1]): r for r in _rows(conn, "hidden_connections")}
        # two shared intermediates -> kept, intermediaries recorded
        kept = rows[("CoP", "CoQ")]
        assert kept[2] == 2 and kept[3] == 2
        assert set(json.loads(kept[4])) == {"JvMid", "NicheSup"}
        assert ("CoQ", "CoS") in rows  # 2 shared, no direct edge
        # direct same_group edge kills the pair despite 2 shared intermediaries
        assert ("CoP", "CoS") not in rows
        # single shared intermediate -> below the corroboration floor
        for pair in (("CoP", "CoR"), ("CoQ", "CoR"), ("CoR", "CoS")):
            assert pair not in rows
        # hub pairs only ever share the one hub -> floor excludes them
        assert not any(a.startswith("Hub") or b.startswith("Hub") for a, b in rows)

    def test_co_mentioned_pair_excluded(self, db):
        path, conn = db
        # S5: co_mentioned_in dyads are retired — seed the CANONICAL edition
        # family instead (the pair view projects from it).
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hyper_edges (
                id INTEGER PRIMARY KEY, edge_type TEXT NOT NULL,
                label TEXT NOT NULL, weight REAL, valid_from TEXT,
                valid_to TEXT, source_ref TEXT, properties TEXT,
                created_at TEXT, agent_id TEXT, source_tier TEXT,
                UNIQUE(edge_type, label));
            CREATE TABLE IF NOT EXISTS hyper_incidences (
                edge_id INTEGER NOT NULL REFERENCES hyper_edges(id),
                entity_name TEXT NOT NULL,
                weight REAL, direction TEXT, role TEXT,
                valid_from TEXT, valid_to TEXT,
                PRIMARY KEY (edge_id, entity_name));
            INSERT INTO hyper_edges (id, edge_type, label) VALUES (9001, 'edition', 'Edition Z');
            INSERT INTO hyper_incidences (edge_id, entity_name) VALUES (9001, 'CoP'), (9001, 'CoQ');
            """
        )
        conn.commit()
        dri.run_lane(conn, "hidden-connections", apply_=True, db_path=path)
        rows = {(r[0], r[1]) for r in _rows(conn, "hidden_connections")}
        assert ("CoP", "CoQ") not in rows  # now discussed together
        assert rows == {("CoQ", "CoS")}

    def test_hub_cap_constant_filters_hub_intermediates(self, db, monkeypatch):
        path, conn = db
        monkeypatch.setattr(dri, "HIDDEN_MAX_INTER_DEGREE", 4)
        _edge(conn, "HubSup", "CoP", "supplier_to", "vigil:rpt:H")
        _edge(conn, "HubSup", "CoQ", "supplier_to", "vigil:rpt:H")
        conn.commit()
        dri.run_lane(conn, "hidden-connections", apply_=True, db_path=path)
        rows = {(r[0], r[1]): r for r in _rows(conn, "hidden_connections")}
        # HubSup (degree 8 > cap 4) contributes nothing; CoP/CoQ still kept
        # via the capped-in NicheSup + JvMid
        assert ("CoP", "CoQ") in rows and ("CoQ", "CoS") in rows
        assert not any(a.startswith("Hub") or b.startswith("Hub") for a, b in rows)


class TestSupplyChokepoints:
    def test_threshold_and_counts(self, db):
        path, conn = db
        _edge(conn, "ChokeSup", "C1", "supplier_to", "vigil:rpt:C")
        _edge(conn, "ChokeSup", "C2", "supplier_to", "vigil:rpt:C")
        _edge(conn, "ChokeSup", "C3", "supplier_to", "vigil:rpt:C")
        _edge(conn, "WeakSup", "C1", "supplier_to", "vigil:rpt:C")
        _edge(conn, "WeakSup", "C2", "supplier_to", "vigil:rpt:C")
        _edge(conn, "C1", "C2", "competes_with", "derive:relations:k")
        _edge(conn, "C2", "C3", "competes_with", "derive:relations:k")
        conn.commit()
        dri.run_lane(conn, "supply-chokepoints", apply_=True, db_path=path)
        rows = {r[0]: r for r in _rows(conn, "supply_chokepoints")}
        assert rows["ChokeSup"][1:3] == (3, 2)
        assert set(json.loads(rows["ChokeSup"][3])) == {"C1", "C2", "C3"}
        assert "WeakSup" not in rows  # only 2 competitors served


class TestCompetitiveOverlap:
    def test_triangles_only(self, db):
        path, conn = db
        _edge(conn, "CoA", "CoB", "competes_with", "derive:relations:k")
        _edge(conn, "CoB", "CoC", "competes_with", "derive:relations:k")
        _edge(conn, "CoA", "CoC", "competes_with", "derive:relations:k")
        # open triple: CoD competes CoA only -> no triad row
        _edge(conn, "CoA", "CoD", "competes_with", "derive:relations:k")
        conn.commit()
        dri.run_lane(conn, "competitive-overlap", apply_=True, db_path=path)
        triads = [(r[0], r[1], r[2]) for r in _rows(conn, "competitive_overlap")]
        assert triads == [("CoA", "CoB", "CoC")]


class TestIdempotencyAndDryRun:
    def test_dry_run_writes_nothing(self, db):
        path, conn = db
        dri.run_lane(conn, "ultimate-parent", apply_=False, db_path=path)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='ultimate_parent'"
            ).fetchone()[0]
            == 0
        )

    def test_apply_is_idempotent_with_stable_digest(self, db):
        path, conn = db
        dri.ensure_tables(conn)
        conn.commit()
        n1, d1 = dri.run_lane(conn, "ultimate-parent", apply_=True, db_path=path)
        n2, d2 = dri.run_lane(conn, "ultimate-parent", apply_=True, db_path=path)
        assert (n1, d1) == (n2, d2)
        assert conn.execute("SELECT COUNT(*) FROM ultimate_parent").fetchone()[0] == n2


class TestCohortStaleness:
    """S2 reader guard: `cohort_staleness` decides whether a cohort's stamp
    licenses serving its rows as a derivation of the CURRENT entity set."""

    def _ensure(self, conn, stamps: dict[str, tuple[str, str]]):
        dri.ensure_tables(conn)
        conn.commit()
        from helpers.graph.derive_relational_insights import entities_fingerprint

        current = entities_fingerprint(conn)
        for table, (mode, fp) in stamps.items():
            conn.execute(
                "INSERT INTO derived_cohort_meta VALUES (?, ?, ?, '2026-11-17')",
                (table, mode, current if fp == "CURRENT" else fp),
            )
        conn.commit()
        return current

    def test_absent_meta_table_is_not_a_verdict(self, db):
        # A pre-S2 store cannot answer the question, so it must not warn on
        # every cohort (noise on every restore).
        _, conn = db
        assert dri.cohort_staleness(conn, "ultimate_parent") is None

    def test_fresh_stamp_passes(self, db):
        _, conn = db
        self._ensure(conn, {"ultimate_parent": ("derived", "CURRENT")})
        assert dri.cohort_staleness(conn, "ultimate_parent") is None

    def test_repointed_stamp_blocks(self, db):
        _, conn = db
        self._ensure(conn, {"hidden_connections": ("repointed", "CURRENT")})
        stale = dri.cohort_staleness(conn, "hidden_connections")
        assert stale is not None
        reason, message = stale
        assert reason == dri.STALE_REPOINTED
        assert "repointed" in message
        assert dri.STALE_REMEDY in message

    def test_missing_row_in_a_present_table_blocks(self, db):
        _, conn = db
        self._ensure(conn, {"ultimate_parent": ("derived", "CURRENT")})
        stale = dri.cohort_staleness(conn, "supply_chokepoints")
        assert stale is not None
        reason, message = stale
        assert reason == dri.STALE_UNSTAMPED
        assert dri.STALE_REMEDY in message

    def test_fingerprint_drift_blocks(self, db):
        _, conn = db
        self._ensure(conn, {"ultimate_parent": ("derived", "DEADBEEF00000000")})
        stale = dri.cohort_staleness(conn, "ultimate_parent")
        assert stale is not None
        reason, message = stale
        assert reason == dri.STALE_FINGERPRINT
        assert "DEADBEEF00000000" in message

    def test_caller_supplied_fingerprint_avoids_a_reread(self, db):
        _, conn = db
        current = self._ensure(conn, {"ultimate_parent": ("derived", "CURRENT")})
        # Equal fingerprint passed in -> fresh, no recompute needed.
        assert dri.cohort_staleness(conn, "ultimate_parent", fingerprint=current) is None
        # A different one supplied by the caller is trusted over the store.
        assert dri.cohort_staleness(conn, "ultimate_parent", fingerprint="0" * 16) is not None

    def test_apply_stamps_derived_and_clears_a_repointed_mark(self, db):
        path, conn = db
        self._ensure(conn, {"ultimate_parent": ("repointed", "CURRENT")})
        assert dri.cohort_staleness(conn, "ultimate_parent")[0] == dri.STALE_REPOINTED
        dri.run_lane(conn, "ultimate-parent", apply_=True, db_path=path)
        assert dri.cohort_staleness(conn, "ultimate_parent") is None

    def test_stale_cohorts_hashes_at_most_once(self, db, monkeypatch):
        """The batch form must not re-hash the entity set per stale cohort."""
        _, conn = db
        self._ensure(
            conn,
            {
                "ultimate_parent": ("derived", "0000000000000000"),
                "hidden_connections": ("derived", "0000000000000000"),
                "competitive_overlap": ("derived", "0000000000000000"),
            },
        )
        calls = []
        original = dri.entities_fingerprint
        monkeypatch.setattr(
            dri, "entities_fingerprint", lambda c: (calls.append(1), original(c))[1]
        )
        stale = dri.stale_cohorts(
            conn, ["ultimate_parent", "hidden_connections", "competitive_overlap"]
        )
        # Order follows the caller's list, so a reader reports cohorts in the
        # order it walks them.
        assert [t for t, _r, _m in stale] == [
            "ultimate_parent",
            "hidden_connections",
            "competitive_overlap",
        ]
        assert all(reason == dri.STALE_FINGERPRINT for _t, reason, _m in stale)
        assert len(calls) == 1

    def test_stale_cohorts_hashes_zero_times_when_only_modes_are_wrong(self, db, monkeypatch):
        _, conn = db
        self._ensure(
            conn,
            {
                "ultimate_parent": ("repointed", "CURRENT"),
                "hidden_connections": ("repointed", "CURRENT"),
            },
        )
        calls = []
        original = dri.entities_fingerprint
        monkeypatch.setattr(
            dri, "entities_fingerprint", lambda c: (calls.append(1), original(c))[1]
        )
        stale = dri.stale_cohorts(conn, ["ultimate_parent", "hidden_connections"])
        assert {r for _t, r, _m in stale} == {dri.STALE_REPOINTED}
        assert calls == []  # no stamp needs a fingerprint comparison

    def test_stale_cohorts_skips_absent_tables(self, db):
        _, conn = db
        dri.ensure_tables(conn)
        conn.commit()
        # An absent table cannot be stale; a PRESENT table with no meta row is.
        assert dri.stale_cohorts(conn, ["never_created"]) == []
