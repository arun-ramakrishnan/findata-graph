"""Unit tests for helpers/misc/backfill_identifiers.py (S3 of the
ontology_convention_stack proposal): self-ensure DDL (entities.cin facet
block + entity_identifiers registry), the deterministic facet
convergence, and the validated --set-cin/--set-id write surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from helpers.core.db import connect
from helpers.misc import backfill_identifiers as bi

CIN = "L01631KA2010PTC096843"
CIN2 = "U01631MH2005PLC123456"


@pytest.fixture()
def db(tmp_path: Path):
    conn = connect(tmp_path / "s3.db")
    conn.execute(
        "CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT, "
        "created_at DATETIME, last_updated DATETIME)"
    )
    for name in ("Tata Steel", "Zen LLP"):
        conn.execute("INSERT INTO entities (name, entity_type) VALUES (?, 'company')", (name,))
    conn.commit()
    yield conn
    conn.close()


def _facets(conn, name: str) -> tuple:
    return tuple(
        conn.execute(
            "SELECT cin, cin_listing, cin_nic5, cin_state, cin_year, cin_ownership "
            "FROM entities WHERE name = ?",
            (name,),
        ).fetchone()
    )


class TestEnsureSchema:
    def test_adds_facet_block_and_registry(self, db):
        bi.ensure_schema(db)
        cols = {r[1] for r in db.execute("PRAGMA table_info(entities)")}
        assert {"cin", "cin_listing", "cin_nic5", "cin_state", "cin_year", "cin_ownership"} <= cols
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='entity_identifiers'").fetchone()

    def test_idempotent(self, db):
        bi.ensure_schema(db)
        bi.ensure_schema(db)  # no duplicate ALTER / CREATE errors
        cols = [r[1] for r in db.execute("PRAGMA table_info(entities)")]
        assert cols.count("cin") == 1


class TestConverge:
    def test_projects_facets_from_cin(self, db):
        bi.ensure_schema(db)
        db.execute("UPDATE entities SET cin = ? WHERE name = 'Tata Steel'", (CIN,))
        db.commit()
        stats = bi.converge(db, apply=True)
        assert stats["cin_entities"] == 1 and stats["facets_stale"] == 1
        assert _facets(db, "Tata Steel") == (CIN, "L", "01631", "KA", 2010, "PTC")
        # idempotent: second pass is a no-op
        assert bi.converge(db, apply=True)["facets_stale"] == 0

    def test_dry_run_writes_nothing(self, db):
        bi.ensure_schema(db)
        db.execute("UPDATE entities SET cin = ? WHERE name = 'Tata Steel'", (CIN,))
        db.commit()
        stats = bi.converge(db, apply=False)
        assert stats["facets_stale"] == 1
        assert _facets(db, "Tata Steel") == (CIN, None, None, None, None, None)

    def test_malformed_counted_never_cleared(self, db):
        bi.ensure_schema(db)
        db.execute("UPDATE entities SET cin = 'garbage' WHERE name = 'Tata Steel'")
        db.commit()
        stats = bi.converge(db, apply=True)
        assert stats["malformed_cin"] == 1
        assert _facets(db, "Tata Steel")[0] == "garbage"


class TestSetOps:
    def test_set_cin_plan_and_apply(self, db):
        bi.ensure_schema(db)
        ops, failures = bi.plan_set_ops(db, [f"Tata Steel={CIN}"], None)
        assert not failures and len(ops) == 1
        wrote = bi.apply_set_ops(db, ops)
        assert wrote["cin_set"] == 1
        assert _facets(db, "Tata Steel") == (CIN, "L", "01631", "KA", 2010, "PTC")

    def test_set_id_upsert_and_owner_conflict(self, db):
        bi.ensure_schema(db)
        ops, failures = bi.plan_set_ops(db, None, ["Zen LLP:llpin=AAA-1234"])
        assert not failures
        bi.apply_set_ops(db, ops)
        rows = [
            tuple(r)
            for r in db.execute(
                "SELECT entity_name, identifier_type, identifier_value FROM entity_identifiers"
            )
        ]
        assert rows == [("Zen LLP", "llpin", "AAA-1234")]
        # re-plan the same id: same owner → upsert, not a failure
        ops2, failures2 = bi.plan_set_ops(db, None, ["Zen LLP:llpin=AAA-1234"])
        assert not failures2 and bi.apply_set_ops(db, ops2)["ids_upserted"] == 1

    def test_cin_type_rejected(self, db):
        _, failures = bi.plan_set_ops(db, None, [f"Tata Steel:cin={CIN}"])
        assert failures and "--set-cin" in failures[0]

    def test_cross_entity_owner_blocked(self, db):
        bi.ensure_schema(db)
        ops, failures = bi.plan_set_ops(db, None, ["Tata Steel:lei=A" + "B" * 19])
        assert not failures
        bi.apply_set_ops(db, ops)
        _, failures2 = bi.plan_set_ops(db, None, ["Zen LLP:lei=A" + "B" * 19])
        assert failures2 and "already owned" in failures2[0]

    def test_unknown_entity_and_bad_format_block(self, db):
        _, failures = bi.plan_set_ops(db, ["Ghost=" + CIN], None)
        assert failures and "not found" in failures[0]
        _, failures = bi.plan_set_ops(db, ["Tata Steel=short"], None)
        assert failures and "21-char CIN" in failures[0]
        _, failures = bi.plan_set_ops(db, None, ["Tata Steel:lei=TOOSHORT"])
        assert failures and "format gate" in failures[0]

    def test_name_matching_is_case_insensitive(self, db):
        bi.ensure_schema(db)
        ops, failures = bi.plan_set_ops(db, ["tata steel=" + CIN2], None)
        assert not failures
        assert ops[0][1] == "Tata Steel"  # canonical row written


class TestMain:
    def test_dry_run_then_apply_rc_zero(self, tmp_path: Path):
        path = tmp_path / "main1.db"
        conn = connect(path)
        conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT)")
        conn.commit()
        conn.close()
        assert bi.main(["--db", str(path)]) == 0
        assert bi.main(["--db", str(path), "--apply"]) == 0
        conn = connect(path)
        assert "cin" in {r[1] for r in conn.execute("PRAGMA table_info(entities)")}
        conn.close()

    def test_bare_db_never_blocks(self, tmp_path: Path):
        # no entities table at all: apply must not crash (self-ensure
        # skips both blocks) and the report says so
        path = tmp_path / "bare.db"
        assert bi.main(["--db", str(path), "--apply"]) == 0

    def test_validation_failure_blocks_batch(self, tmp_path: Path):
        path = tmp_path / "block.db"
        conn = connect(path)
        conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, entity_type TEXT)")
        conn.execute("INSERT INTO entities VALUES ('Real Co', 'company')")
        conn.commit()
        conn.close()
        rc = bi.main(
            [
                "--db",
                str(path),
                "--apply",
                "--set-cin",
                f"Real Co={CIN}",
                "--set-cin",
                f"Ghost Co={CIN2}",
            ]
        )
        assert rc == 1
        conn = connect(path)
        # the VALID op in the batch must NOT have landed (nothing applied)
        assert conn.execute("SELECT cin FROM entities").fetchone()[0] is None
        conn.close()
