"""Tests for helpers/misc/ontology_eval_gate.py (ontology_governance S2):
the frozen-question-set set-diff gate — accept rule (zero regressions,
no undeclared changes, declared improvements materialize), draft refusal,
explicit rebaseline, and the CLI exit codes.

Each test builds parent/candidate SQLite copies from one helper fixture
so planted differences are the ONLY differences the gate sees.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from helpers.core.db import connect
from helpers.misc import ontology_eval_gate as gate


def _build_db(path: Path) -> None:
    conn = connect(path)
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY, cin TEXT, entity_type TEXT)")
    conn.execute(
        "CREATE TABLE entity_identifiers (entity_name TEXT, identifier_type TEXT, "
        "identifier_value TEXT, namespace TEXT, source_ref TEXT)"
    )
    conn.execute(
        "CREATE TABLE concept_schemes (scheme_id TEXT PRIMARY KEY, label TEXT, "
        "scheme_type TEXT, version TEXT, source_uri TEXT, license TEXT, "
        "attribution TEXT, active INTEGER DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE concepts (concept_id TEXT PRIMARY KEY, scheme_id TEXT, "
        "concept_code TEXT, pref_label TEXT, alt_label TEXT, notation TEXT, "
        "broader_id TEXT, scope_note TEXT, source_ref TEXT, "
        "status TEXT NOT NULL DEFAULT 'active')"
    )
    conn.execute(
        "CREATE TABLE events (entity TEXT, event_type TEXT, event_date TEXT, "
        "counterparty_entity TEXT, agent_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE concept_mappings (source_scheme TEXT, source_concept TEXT, "
        "target_scheme TEXT, target_concept TEXT, match_type TEXT, source_ref TEXT, "
        "version TEXT, status TEXT NOT NULL DEFAULT 'active')"
    )
    conn.execute("CREATE TABLE graph_edges (source TEXT, target TEXT, edge_type TEXT)")
    conn.execute("CREATE TABLE hyper_edges (id INTEGER PRIMARY KEY, edge_type TEXT, label TEXT)")
    conn.execute("CREATE TABLE hyper_incidences (edge_id INTEGER, entity_name TEXT)")
    for name, scheme_id, broader in (
        ("Financials", "super_sector", None),
        ("Banking", "sector", "super_sector:Financials"),
        ("Software", "subsector", "sector:Banking"),
    ):
        conn.execute(
            "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label, "
            "source_ref, broader_id, status) VALUES (?, ?, ?, ?, 'seed:test', ?, 'active')",
            (f"{scheme_id}:{name}", scheme_id, name, name, broader),
        )
    conn.execute(
        "INSERT INTO entities (name, cin, entity_type) VALUES "
        "('Acme Ltd', 'L00000KA0000PLC000000', 'company'), "
        "('Financials', NULL, 'super_sector')"
    )
    conn.execute(
        "INSERT INTO entity_identifiers VALUES ('Zen Corp', 'lei', 'LEI-ZEN', 'gleif', 'test')"
    )
    conn.execute(
        "INSERT INTO events (entity, event_type, counterparty_entity, agent_id) "
        "VALUES ('Acme Ltd', 'acquisition', 'Target One', 'derive:events')"
    )
    conn.execute(
        "INSERT INTO concept_mappings VALUES ('industry', 'Op Label', 'subsector', "
        "'Software', 'exactMatch', 'manual:t', 'v1', 'active'), "
        "('industry', 'Op Label', 'subsector', 'Ghost', 'closeMatch', 'manual:t', 'v1', 'superseded')"
    )
    conn.execute(
        "INSERT INTO graph_edges (source, target, edge_type) VALUES "
        "('Acme Ltd', 'Financials', 'part_of'), ('Financials', 'Acme Ltd', 'has_company')"
    )
    conn.execute("INSERT INTO hyper_edges (id, edge_type, label) VALUES (1, 'edition', 'Ed. 1')")
    conn.execute("INSERT INTO hyper_incidences VALUES (1, 'Acme Ltd'), (1, 'Zen Corp')")
    conn.commit()
    conn.close()


def _questions(tmp_path: Path) -> Path:
    data = {
        "version": 3,
        "questions": [
            {
                "id": "subtree-fin",
                "shape": "subtree",
                "params": {"root": "super_sector:Financials"},
                "expected": [],
            },
            {
                "id": "acq-counterparties",
                "shape": "event_counterparties",
                "params": {"entity": "Acme Ltd", "event_type": "acquisition"},
                "expected": [],
            },
            {
                "id": "resolve-lei",
                "shape": "resolve_identifier",
                "params": {"value": "LEI-ZEN"},
                "expected": [],
            },
        ],
    }
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def dbs(tmp_path: Path):
    parent = tmp_path / "parent.db"
    candidate = tmp_path / "candidate.db"
    _build_db(parent)
    _build_db(candidate)
    return parent, candidate


def _mutate(path: Path, sql: str, args: tuple = ()) -> None:
    conn = connect(path)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


class TestAcceptRule:
    def test_no_change_accepts(self, dbs, tmp_path):
        parent, candidate = dbs
        q = _questions(tmp_path)
        accept, results, reasons = gate.evaluate(
            connect(parent), connect(candidate), json.loads(q.read_text())["questions"], set()
        )
        # NOTE: connections stay open here — evaluate is pure; callers close.
        assert accept and reasons == []
        assert all(r["verdict"] == "unchanged" for r in results)

    def test_improvement_only_with_declaration_accepts(self, dbs, tmp_path):
        parent, candidate = dbs
        _mutate(
            candidate,
            "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label, "
            "source_ref, broader_id, status) VALUES ('subsector:New', 'subsector', "
            "'New', 'New', 'seed:test', 'sector:Banking', 'active')",
        )
        q = json.loads(_questions(tmp_path).read_text())["questions"]
        accept, results, reasons = gate.evaluate(
            connect(parent), connect(candidate), q, {"subtree-fin"}
        )
        assert accept, reasons
        verdicts = {r["id"]: r["verdict"] for r in results}
        assert verdicts["subtree-fin"] == "improved"

    def test_undeclared_change_rejects(self, dbs, tmp_path):
        parent, candidate = dbs
        _mutate(candidate, "DELETE FROM events WHERE counterparty_entity='Target One'")
        q = json.loads(_questions(tmp_path).read_text())["questions"]
        accept, results, reasons = gate.evaluate(connect(parent), connect(candidate), q, set())
        assert not accept
        assert any("undeclared answer change" in r for r in reasons)

    def test_declared_change_that_loses_rows_rejects(self, dbs, tmp_path):
        parent, candidate = dbs
        _mutate(candidate, "DELETE FROM events WHERE counterparty_entity='Target One'")
        q = json.loads(_questions(tmp_path).read_text())["questions"]
        accept, results, reasons = gate.evaluate(
            connect(parent), connect(candidate), q, {"acq-counterparties"}
        )
        assert not accept
        assert any("LOST rows" in r for r in reasons)

    def test_declared_improvement_not_materialized_rejects(self, dbs, tmp_path):
        parent, candidate = dbs
        q = json.loads(_questions(tmp_path).read_text())["questions"]
        accept, _, reasons = gate.evaluate(connect(parent), connect(candidate), q, {"resolve-lei"})
        assert not accept
        assert any("did not materialize" in r for r in reasons)

    def test_unanswerable_question_rejects(self, dbs, tmp_path):
        parent, candidate = dbs
        _mutate(candidate, "DROP TABLE events")
        q = json.loads(_questions(tmp_path).read_text())["questions"]
        accept, results, _ = gate.evaluate(connect(parent), connect(candidate), q, set())
        assert not accept
        assert any(r["verdict"] == "errored" for r in results)

    def test_unknown_expect_improve_id_raises(self, dbs, tmp_path):
        parent, candidate = dbs
        q = json.loads(_questions(tmp_path).read_text())["questions"]
        with pytest.raises(gate.GateError):
            gate.evaluate(connect(parent), connect(candidate), q, {"nope"})


class TestQuestionSet:
    def test_draft_set_refused(self, tmp_path):
        path = tmp_path / "draft.json"
        path.write_text(json.dumps({"draft": True, "questions": []}), encoding="utf-8")
        with pytest.raises(gate.GateError, match="DRAFT"):
            gate.load_questions(path)

    def test_unknown_shape_refused(self, tmp_path):
        path = tmp_path / "q.json"
        path.write_text(
            json.dumps({"questions": [{"id": "x", "shape": "nope", "params": {}}]}),
            encoding="utf-8",
        )
        with pytest.raises(gate.GateError, match="known shape"):
            gate.load_questions(path)


class TestMain:
    def test_cli_accept_rc0_with_report(self, dbs, tmp_path, capsys):
        parent, candidate = dbs
        q = _questions(tmp_path)
        report = tmp_path / "report.json"
        rc = gate.main(
            [
                "--parent",
                str(parent),
                "--candidate",
                str(candidate),
                "--questions",
                str(q),
                "--report",
                str(report),
            ]
        )
        assert rc == 0
        assert "ACCEPT" in capsys.readouterr().out
        data = json.loads(report.read_text())
        assert data["protocol"] == "set_diff" and data["accept"] is True

    def test_cli_reject_rc1(self, dbs, tmp_path):
        parent, candidate = dbs
        _mutate(candidate, "DELETE FROM entity_identifiers")
        rc = gate.main(
            [
                "--parent",
                str(parent),
                "--candidate",
                str(candidate),
                "--questions",
                str(_questions(tmp_path)),
            ]
        )
        assert rc == 1

    def test_cli_missing_parent_rc2(self, dbs, tmp_path):
        _, candidate = dbs
        rc = gate.main(["--candidate", str(candidate), "--questions", str(_questions(tmp_path))])
        assert rc == 2

    def test_shipped_question_set_is_live_and_wellformed(self):
        """The shipped set (derived 2026-09-17 from the live store) loads
        non-draft with unique ids and known shapes — a draft flip back or a
        malformed roster must not slip in silently."""
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        data = gate.load_questions(repo / "helpers/misc/ontology_questions.json")
        ids = [q["id"] for q in data["questions"]]
        assert len(ids) == len(set(ids))
        assert len(ids) >= 20  # proposal §S2: ≥20 questions
        assert all(q["shape"] in gate._SHAPES for q in data["questions"])
        assert set(gate._SHAPES) == {q["shape"] for q in data["questions"]}


class TestRebaseline:
    def test_rebaseline_records_ref_and_bumps_version(self, dbs, tmp_path):
        parent, candidate = dbs
        _mutate(
            candidate,
            "INSERT INTO concepts (concept_id, scheme_id, concept_code, pref_label, "
            "source_ref, broader_id, status) VALUES ('subsector:New', 'subsector', "
            "'New', 'New', 'seed:test', 'sector:Banking', 'active')",
        )
        q = _questions(tmp_path)
        rc = gate.main(
            [
                "--parent",
                str(parent),
                "--candidate",
                str(candidate),
                "--questions",
                str(q),
                "--expect-improve",
                "subtree-fin",
                "--rebaseline",
                "completed.md #999 (test change)",
            ]
        )
        assert rc == 0
        data = json.loads(q.read_text())
        assert data["version"] == 4
        assert data["rebaselined"]["ref"] == "completed.md #999 (test change)"
        assert "subsector:New" in data["questions"][0]["expected"]

    def test_rebaseline_refused_when_gate_rejects(self, dbs, tmp_path):
        parent, candidate = dbs
        _mutate(candidate, "DELETE FROM events")  # undeclared change → reject
        q = _questions(tmp_path)
        rc = gate.main(
            [
                "--parent",
                str(parent),
                "--candidate",
                str(candidate),
                "--questions",
                str(q),
                "--rebaseline",
                "should-not-land",
            ]
        )
        assert rc == 1
        data = json.loads(q.read_text())
        assert "rebaselined" not in data and data["version"] == 3


class TestShapes:
    """The five doc-driven shapes added for comprehensive coverage (v2)."""

    def test_entities_of_type(self, dbs):
        parent, _ = dbs
        assert gate.answer_question(
            connect(parent),
            {"shape": "entities_of_type", "params": {"entity_type": "super_sector"}},
        ) == ["Financials"]

    def test_edge_neighbors(self, dbs):
        parent, _ = dbs
        assert gate.answer_question(
            connect(parent),
            {"shape": "edge_neighbors", "params": {"source": "Acme Ltd", "edge_type": "part_of"}},
        ) == ["Financials"]

    def test_hyperedge_members(self, dbs):
        parent, _ = dbs
        assert gate.answer_question(
            connect(parent),
            {"shape": "hyperedge_members", "params": {"edge_type": "edition", "label": "Ed. 1"}},
        ) == ["Acme Ltd", "Zen Corp"]

    def test_concept_mappings_for_active_only(self, dbs):
        parent, _ = dbs
        assert gate.answer_question(
            connect(parent),
            {
                "shape": "concept_mappings_for",
                "params": {"source_scheme": "industry", "source_concept": "Op Label"},
            },
        ) == ["subsector:Software:exactMatch"]  # superseded closeMatch excluded

    def test_provenance_agents_for(self, dbs):
        parent, _ = dbs
        assert gate.answer_question(
            connect(parent), {"shape": "provenance_agents_for", "params": {"table": "events"}}
        ) == ["derive:events"]

    def test_provenance_agents_rejects_bad_table(self, dbs):
        parent, _ = dbs
        with pytest.raises(gate.GateError):
            gate.answer_question(
                connect(parent),
                {"shape": "provenance_agents_for", "params": {"table": "no;drop"}},
            )
        with pytest.raises(gate.GateError, match="no agent_id"):
            gate.answer_question(
                connect(parent),
                {"shape": "provenance_agents_for", "params": {"table": "graph_edges"}},
            )
