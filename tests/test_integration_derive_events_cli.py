#!/usr/bin/env python3
"""Integration tests for the derive_events CLI `--apply`
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §4 A7).

test_derive_events.py covers the extractors and promote_from_edges;
test_integration_derive_chain.py chains the library calls. Missing: the
real `_cli` over a tmp vault+DB — both arms in one run (edge promotion +
prose guidance), dry-run/apply parity, DELETE-then-INSERT idempotence,
and the FK-safety contract (every event's entity must exist).
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect as db_connect  # noqa: E402
from helpers.graph import derive_events as de  # noqa: E402
from helpers.graph.query import DB_PATH  # noqa: E402

pytestmark = [pytest.mark.integration]

_HDFC = "HDFC Bank"
_ICICI = "ICICI Bank"

_GUIDANCE = ("- **FY27 guidance reiterated (10-12%):** management expects "
             "revenue growth at the lower end of the range.")


def _note(title: str) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        "type: company\n"
        "tags:\n"
        "- entity_type/company\n"
        "- sector/Banking\n"
        "created: '2026-01-01'\n"
        "last_modified: '2026-01-02'\n"
        "---\n"
        f"# {title}\n"
        "\n"
        "A company overview line.\n"
        "\n"
        f"{_GUIDANCE}\n"
    )


class _EventsProject:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / "research.db"
        self.companies = root / "findata" / "Companies" / "Banking"
        self.companies.mkdir(parents=True)
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(self.db))
        src.backup(dst)
        src.close()
        for t in ("graph_edges", "entity_tags", "graph_analytics", "events",
                  "quotes", "company_metrics", "company_embeddings",
                  "note_search", "note_search_meta"):
            dst.execute(f"DELETE FROM {t}")  # noqa: S608  # schema-constant identifiers
        dst.execute("DELETE FROM entities")
        dst.executemany(
            "INSERT INTO entities (name, entity_type, file_path) "
            "VALUES (?, 'company', ?)",
            [(_HDFC, "findata/Companies/Banking/Hdfc_Bank.md"),
             (_ICICI, "findata/Companies/Banking/ICICI_Bank.md")])
        # Promotion arm: one dated acquired edge with a stake property.
        dst.execute(
            "INSERT INTO graph_edges (source, target, edge_type, valid_from, "
            "source_ref, properties) VALUES (?, ?, 'acquired', "
            "'2026-03-15', 'extract_relations', ?)",
            (_HDFC, _ICICI, '{"stake": "51%"}'))
        dst.commit()
        dst.close()
        (self.companies / "Hdfc_Bank.md").write_text(
            _note(_HDFC), encoding="utf-8")
        (self.companies / "ICICI_Bank.md").write_text(
            _note(_ICICI), encoding="utf-8")

    def run(self, monkeypatch, *args: str) -> tuple[int, str]:
        monkeypatch.setattr(de, "COMPANIES_DIR", self.companies)
        monkeypatch.setattr(de, "_REPO_ROOT", self.root)
        monkeypatch.setattr(de, "connect",
                            lambda *a, **k: db_connect(str(self.db)))
        err = StringIO()
        with redirect_stderr(err):
            rc = de._cli(list(args))
        return rc, err.getvalue()

    def events(self) -> list[tuple]:
        conn = sqlite3.connect(str(self.db))
        rows = conn.execute(
            "SELECT entity, event_type, event_date, magnitude, "
            "counterparty, source_ref FROM events ORDER BY 1, 2, 3"
        ).fetchall()
        conn.close()
        return rows


@pytest.fixture
def events_project(tmp_path) -> _EventsProject:
    return _EventsProject(tmp_path)


class TestDeriveEventsCli:
    def test_apply_writes_both_arms(self, events_project, monkeypatch):
        """Edge promotion (acquired -> acquisition, dated, counterparty)
        AND prose guidance (the hand-written bullet) in one run."""
        rc, err = events_project.run(monkeypatch, "--apply")
        assert rc == 0
        rows = events_project.events()
        by_type = {r[1] for r in rows}
        assert by_type == {"acquisition", "guidance"}
        acq = next(r for r in rows if r[1] == "acquisition")
        assert acq[0] == _HDFC and acq[2] == "2026-03-15"
        assert acq[3] == "51%" and acq[4] == _ICICI
        assert sum(1 for r in rows if r[1] == "guidance") == 2  # both notes

    def test_dry_run_apply_parity(self, events_project, monkeypatch):
        """Dry-run reports exactly what apply later writes, and writes
        nothing meanwhile."""
        rc, err = events_project.run(monkeypatch)  # dry-run
        assert rc == 0
        assert events_project.events() == []
        assert "3 events would insert" in err
        rc, err = events_project.run(monkeypatch, "--apply")
        assert rc == 0
        assert "3 events inserted" in err
        assert len(events_project.events()) == 3

    def test_second_apply_idempotent(self, events_project, monkeypatch):
        assert events_project.run(monkeypatch, "--apply")[0] == 0
        first = events_project.events()
        assert events_project.run(monkeypatch, "--apply")[0] == 0
        assert events_project.events() == first

    def test_fk_safety_every_entity_exists(self, events_project, monkeypatch):
        assert events_project.run(monkeypatch, "--apply")[0] == 0
        conn = sqlite3.connect(str(events_project.db))
        orphans = conn.execute(
            "SELECT COUNT(*) FROM events e "
            "WHERE NOT EXISTS (SELECT 1 FROM entities n WHERE n.name = e.entity)"
        ).fetchone()[0]
        fk_on = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        assert orphans == 0
        assert fk_on == []
