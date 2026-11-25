#!/usr/bin/env python3
"""Integration: the org-membership derive chain (test_coverage_gap_sweep S5).

invested_in dyads → collect → apply_derivation (dry-run, then real,
then idempotency) on one hermetic store. Mirrors the hyper_family chain
shape: fixture store only, no live research store.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.schema import EDGES_12COL, ENTITIES_8COL, HYPER_STORE  # noqa: E402

from helpers.graph.derive_org_membership import (  # noqa: E402
    MEMBERSHIP_HYPER_TYPE,
    apply_derivation,
    collect,
)
from helpers.misc.backfill_row_provenance import ensure_schema, seed_agents  # noqa: E402

PEOPLE = ("Alice", "Bob", "Carol")
COMPANIES = ("Acme Ltd", "Globex Inc")
SOURCE_REF = "seed:fixture"


def _seed(conn: sqlite3.Connection) -> None:
    conn.executescript(ENTITIES_8COL)
    conn.executescript(EDGES_12COL)
    conn.executescript(HYPER_STORE)
    ensure_schema(conn)  # production parity: agent_id/source_tier columns + registry
    seed_agents(conn)
    conn.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
        [(p, "person") for p in PEOPLE] + [(c, "company") for c in COMPANIES],
    )
    dyads = [
        ("Alice", "Acme Ltd", "invested_in", {"category": "promoter"}, "2026-01-01"),
        ("Bob", "Acme Ltd", "invested_in", {"category": "promoter"}, "2026-02-01"),
        ("Carol", "Acme Ltd", "invested_in", {"category": "holder"}, "2026-01-15"),
        ("Alice", "Globex Inc", "invested_in", {}, "2026-03-01"),
        ("Bob", "Globex Inc", "invested_in", {}, "2026-03-01"),
    ]
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, properties, valid_from, source_ref) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(s, t, et, json.dumps(props), vf, SOURCE_REF) for s, t, et, props, vf in dyads],
    )
    conn.commit()


@pytest.fixture()
def chain_store(tmp_path: Path):
    db = tmp_path / "chain.db"
    conn = sqlite3.connect(str(db))
    _seed(conn)
    yield db, conn
    conn.close()


def test_collect_folds_latest_filing_per_company_person(chain_store):
    _db, conn = chain_store
    members = collect(conn)
    assert set(members) == set(COMPANIES)
    assert set(members["Acme Ltd"]) == set(PEOPLE)
    assert members["Acme Ltd"]["Alice"]["role"] == "promoter"
    assert members["Acme Ltd"]["Carol"]["role"] == "holder"
    assert members["Globex Inc"]["Alice"]["role"] == "holder"
    # latest valid_from wins for duplicated (company, person)
    assert members["Acme Ltd"]["Bob"]["valid_from"] == "2026-02-01"


def test_dry_run_reports_but_writes_nothing(chain_store):
    _db, conn = chain_store
    members = collect(conn)
    stats = apply_derivation(conn, members, dry_run=True)
    assert stats["companies"] == len(COMPANIES)
    assert stats["members"] == 5
    assert stats["new_edges"] == 2
    assert stats["dropped_edges"] == []
    assert stats["new_incidences"] == 5
    assert conn.execute("SELECT COUNT(*) FROM hyper_edges").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0] == 0


def test_apply_writes_store_then_is_idempotent(chain_store):
    db, conn = chain_store
    members = collect(conn)
    apply_derivation(conn, members, dry_run=False)

    edges = conn.execute(
        "SELECT label, properties FROM hyper_edges WHERE edge_type = ?",
        (MEMBERSHIP_HYPER_TYPE,),
    ).fetchall()
    assert len(edges) == 2
    assert {label for label, _ in edges} == set(COMPANIES)
    assert conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0] == 5

    # memberships carry roles (role column, not properties)
    roles = dict(
        conn.execute(
            "SELECT entity_name, role FROM hyper_incidences hi "
            "JOIN hyper_edges he ON he.id = hi.edge_id "
            "WHERE he.label = 'Acme Ltd'"
        )
    )
    assert roles["Alice"] == "promoter"
    assert roles["Carol"] == "holder"

    # re-run: the convergence replace is a no-op
    members2 = collect(conn)
    stats2 = apply_derivation(conn, members2, dry_run=False)
    assert stats2["new_edges"] == 0
    assert stats2["new_incidences"] == 0
    assert stats2["dropped_edges"] == []
    assert conn.execute("SELECT COUNT(*) FROM hyper_edges").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0] == 5


def test_apply_converges_on_shrinkage(chain_store):
    """A member removed from the source disappears from the family."""
    db, conn = chain_store
    members = collect(conn)
    apply_derivation(conn, members, dry_run=False)

    conn.execute("DELETE FROM graph_edges WHERE source='Bob' AND target='Globex Inc'")
    conn.commit()
    members2 = collect(conn)
    assert members2["Globex Inc"] == {"Alice": members2["Globex Inc"]["Alice"]}

    stats = apply_derivation(conn, members2, dry_run=False)
    assert stats["new_incidences"] == 0
    assert stats["dropped_edges"] == []
    n_inc = conn.execute("SELECT COUNT(*) FROM hyper_incidences").fetchone()[0]
    assert n_inc == 4
