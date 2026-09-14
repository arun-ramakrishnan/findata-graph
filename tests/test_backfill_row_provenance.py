"""Unit tests for helpers/misc/backfill_row_provenance.py.

S1 of the ontology_convention_stack proposal: schema self-ensure,
prefix→agent mapping (longest-prefix precedence, LIKE-escaping of
underscores), idempotent convergence, dry-run vs apply, registry
seeding, and the never-blocking contract on partial schemas.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from helpers.core.db import connect
from helpers.misc import backfill_row_provenance as brp


@pytest.fixture()
def db(tmp_path: Path):
    conn = connect(tmp_path / "s1.db")
    # Minimal fact tables — only the columns the converger touches.
    conn.execute(
        "CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT, source_ref TEXT)"
    )
    conn.execute("CREATE TABLE events (entity TEXT, source_ref TEXT)")
    conn.execute("CREATE TABLE quotes (entity TEXT, source_ref TEXT)")
    conn.execute("CREATE TABLE company_metrics (entity TEXT, source_ref TEXT)")
    conn.execute("CREATE TABLE hyper_edges (edge_type TEXT, label TEXT, source_ref TEXT)")
    yield conn
    conn.close()


class TestEnsureSchema:
    def test_creates_registry_and_columns(self, db):
        brp.ensure_schema(db)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "provenance_agents" in tables
        for table in brp.FACT_TABLES:
            cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
            assert {"agent_id", "source_tier"} <= cols

    def test_idempotent(self, db):
        brp.ensure_schema(db)
        brp.ensure_schema(db)  # must not raise
        cols = {r[1] for r in db.execute("PRAGMA table_info(graph_edges)")}
        assert "agent_id" in cols and "source_tier" in cols

    def test_missing_table_never_blocks(self, tmp_path: Path):
        conn = connect(tmp_path / "partial.db")
        conn.execute("CREATE TABLE quotes (entity TEXT, source_ref TEXT)")
        brp.ensure_schema(conn)  # four absent tables — skipped silently
        brp.seed_agents(conn)
        report = brp.converge(conn, apply=True)
        assert report["graph_edges"] == {
            "total": 0,
            "mapped": 0,
            "unmapped": 0,
            "skipped": 1,
        }
        conn.close()


class TestConverge:
    ROWS = [
        ("derive:quotes:Some_Edition:12", "derive_insights", "derive"),
        ("derive:metrics:Some_Edition:3", "derive_insights", "derive"),
        ("derive:cited_in:A_Quarter_That", "derive_cited_in", "derive"),
        ("derive:co_mentioned:A:B", "derive_co_mentions", "derive"),
        ("embeddings:bge-small:Reliance:Infosys", "enrich_relations", "derive"),
        ("yfinance:industry:INFY", "enrich_relations", "external"),
        ("yfinance:holders:TCS", "enrich_relations", "external"),
        ("googlefinance:544442:x", "googlefinance", "external"),
        ("parse_newsletter:Some_Edition", "parse_newsletter", "manual"),
        ("manual:Tanla:sector", "manual", "manual"),
        ("migration:relations", "migration", "migration"),
        ("backfill:sector_edges:X", "backfill_sector_edges", "migration"),
        ("stub_sector_backfill:Y", "stub_sector_backfill", "migration"),
        ("coinfer:2026-08-25:Z", "coinfer", "migration"),
        ("derive:sector_hierarchy:Banks", "build_sector_hierarchy", "derive"),
        ("derive:unmapped_new_lane:q", "derive_unspecified", "derive"),
        ("triage:competes_with:HDFC:ICICI", "triage_accept", "manual"),
        ("pending_relations:jv_with:A", "triage_accept", "manual"),
        ("Phase 2 seed (Edition #69 + gr:acquired)", "phase2_seed", "migration"),
        ("move_sector:part_of:TCS", "move_sector", "manual"),
        ("fix:part_of:Reliance", "manual", "manual"),
        ("extract_relations:acquired:A:B", "extract_relations", "derive"),
    ]

    def _seed_rows(self, db):
        for sr, _, _ in self.ROWS:
            db.execute("INSERT INTO quotes (entity, source_ref) VALUES ('X', ?)", (sr,))

    def test_mapping_and_tiers(self, db):
        brp.ensure_schema(db)
        brp.seed_agents(db)
        self._seed_rows(db)
        brp.converge(db, apply=True)
        for sr, want_agent, want_tier in self.ROWS:
            got = db.execute(
                "SELECT agent_id, source_tier FROM quotes WHERE source_ref = ?",
                (sr,),
            ).fetchone()
            assert (got[0], got[1]) == (want_agent, want_tier), sr

    def test_underscore_is_literal_not_wildcard(self, db):
        # 'derive:coXmentioned' must NOT match the co_mentioned lane
        # (LIKE '_' is escaped) — it falls through to the bare-derive
        # fallback instead of mis-routing.
        brp.ensure_schema(db)
        brp.seed_agents(db)
        db.execute("INSERT INTO quotes (entity, source_ref) VALUES ('X', 'derive:coXmentioned:1')")
        brp.converge(db, apply=True)
        row = db.execute(
            "SELECT agent_id FROM quotes WHERE source_ref = 'derive:coXmentioned:1'"
        ).fetchone()
        assert row[0] == "derive_unspecified"

    def test_unmapped_stays_null_and_reported(self, db):
        brp.ensure_schema(db)
        brp.seed_agents(db)
        db.execute("INSERT INTO quotes (entity, source_ref) VALUES ('X', 'weird:thing:1')")
        report = brp.converge(db, apply=True)
        assert report["quotes"]["unmapped"] == 1
        row = db.execute(
            "SELECT agent_id, source_tier FROM quotes WHERE source_ref = 'weird:thing:1'"
        ).fetchone()
        assert row[0] is None and row[1] is None
        assert brp.unmapped_prefixes(db, "quotes") == [("weird", 1)]

    def test_second_run_converges_nothing(self, db):
        brp.ensure_schema(db)
        brp.seed_agents(db)
        self._seed_rows(db)
        first = brp.converge(db, apply=True)
        assert all(
            stats["mapped"] == len(self.ROWS)
            for stats in first.values()
            if not stats["skipped"] and stats["total"]
        )
        second = brp.converge(db, apply=True)
        assert second["quotes"]["mapped"] == 0
        assert second["quotes"]["unmapped"] == 0

    def test_dry_run_writes_nothing(self, db):
        brp.ensure_schema(db)
        self._seed_rows(db)
        report = brp.converge(db, apply=False)
        assert report["quotes"]["mapped"] == len(self.ROWS)
        n = db.execute("SELECT COUNT(*) FROM quotes WHERE agent_id IS NOT NULL").fetchone()[0]
        assert n == 0

    def test_new_row_only_on_rerun(self, db):
        brp.ensure_schema(db)
        brp.seed_agents(db)
        self._seed_rows(db)
        brp.converge(db, apply=True)
        db.execute("INSERT INTO quotes (entity, source_ref) VALUES ('Y', 'derive:quotes:New:1')")
        report = brp.converge(db, apply=True)
        assert report["quotes"]["mapped"] == 1  # only the new row


class TestRegistry:
    def test_seed_inserts_all_and_is_idempotent(self, db):
        brp.ensure_schema(db)
        first = brp.seed_agents(db)
        assert first == len(brp.AGENT_SEED)
        second = brp.seed_agents(db)
        assert second == 0
        ids = {r[0] for r in db.execute("SELECT agent_id FROM provenance_agents")}
        assert ids == set(brp.AGENT_SEED)

    def test_converge_respects_fk(self, db):
        # agent_id FK → provenance_agents: converging without seeding
        # must not blow up mid-transaction on FK enforcement... it WOULD,
        # so the contract is: seed before converge (main() does).
        brp.ensure_schema(db)
        brp.seed_agents(db)
        db.execute("INSERT INTO quotes (entity, source_ref) VALUES ('X', 'manual:a:b')")
        brp.converge(db, apply=True)
        fk = db.execute("PRAGMA foreign_key_check").fetchall()
        assert fk == []


class TestMain:
    def test_dry_run_rc_zero(self, db, tmp_path: Path):
        # reuse the fixture path via a second connection is racy; instead
        # run main against a fresh db file
        path = tmp_path / "main.db"
        conn = connect(path)
        conn.execute("CREATE TABLE quotes (entity TEXT, source_ref TEXT)")
        conn.execute("INSERT INTO quotes (entity, source_ref) VALUES ('X', 'derive:quotes:A:1')")
        conn.commit()
        conn.close()
        assert brp.main(["--db", str(path)]) == 0

    def test_apply_rc_zero(self, tmp_path: Path):
        path = tmp_path / "main2.db"
        conn = connect(path)
        conn.execute("CREATE TABLE events (entity TEXT, source_ref TEXT)")
        conn.execute("INSERT INTO events (entity, source_ref) VALUES ('X', 'derive:events:z')")
        conn.commit()
        conn.close()
        assert brp.main(["--db", str(path), "--apply"]) == 0
        conn = connect(path, read_only=True)
        row = conn.execute(
            "SELECT agent_id, source_tier FROM events WHERE source_ref = 'derive:events:z'"
        ).fetchone()
        assert (row[0], row[1]) == ("derive_events", "derive")
        conn.close()
