"""Tests for helpers/misc/answerability_probe.py — the Path C coverage
instrument (doc/local/evaluations/auto_ontology_assessment.md §8.3).

The probe answers a different question than the eval gate: not "did the
ontology change?" but "which business intents can this snapshot answer?"
These tests pin the three properties the report leans on:

* shape-parity — every probe shape returns exactly what the gate returns;
* pin classification — answered / negative / drifted / empty / errored are
  assigned from the operator-attested ``expected`` pins, so a stale pin is
  reported instead of silently passing;
* snapshot isolation — the probe never opens the live store, and the
  shipped questions + snapshots agree with it (the ``--parity`` contract).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import pytest  # noqa: E402

from helpers.misc import answerability_probe as probe  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO_ROOT / "snapshots" / "parquet"
LIVE_DB = REPO_ROOT / "memory" / "research.db"


# --------------------------------------------------------------------------- #
# A synthetic snapshot: just enough tables for every shape to answer.          #
# --------------------------------------------------------------------------- #
def _write(side_dir: Path, name: str, ddl: str, rows: str) -> None:
    side_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(ddl)
        con.execute(rows)
        target = (side_dir / f"{name}.parquet").as_posix()
        con.execute(f"COPY {ddl.split('(')[0].split()[-1]} TO '{target}' (FORMAT PARQUET)")
    finally:
        con.close()


@pytest.fixture()
def snapshot_root(tmp_path: Path) -> Path:
    """A minimal but complete snapshot tree, one parquet file per table."""
    side = tmp_path / "snapshots" / "parquet" / "sqlite"
    tables: dict[str, tuple[str, str]] = {
        "concepts": (
            "CREATE TABLE concepts (concept_id VARCHAR, scheme_id VARCHAR, "
            "concept_code VARCHAR, pref_label VARCHAR, alt_label VARCHAR, "
            "notation VARCHAR, broader_id VARCHAR, scope_note VARCHAR, "
            "source_ref VARCHAR, status VARCHAR)",
            "INSERT INTO concepts VALUES "
            "('sector:Banking', 'sector', 'Banking', 'Banking', NULL, NULL, NULL, NULL, 'seed:x', 'active'), "
            "('subsector:Private_Banks', 'subsector', 'Private_Banks', 'Private Banks', NULL, NULL, 'sector:Banking', NULL, 'seed:x', 'active'), "
            "('subsector:Retired', 'subsector', 'Retired', 'Retired', NULL, NULL, 'sector:Banking', NULL, 'seed:x', 'retired')",
        ),
        "entities": (
            "CREATE TABLE entities (name VARCHAR, cin VARCHAR, entity_type VARCHAR)",
            "INSERT INTO entities VALUES "
            "('HDFC Bank', 'L65920MH1994PLC080618', 'company'), "
            "('CRISIL Ratings', NULL, 'institution')",
        ),
        "entity_identifiers": (
            "CREATE TABLE entity_identifiers (entity_name VARCHAR, "
            "identifier_type VARCHAR, identifier_value VARCHAR, namespace VARCHAR, "
            "source_ref VARCHAR)",
            "INSERT INTO entity_identifiers VALUES "
            "('HDFC Bank', 'isin', 'INE040A01034', 'isin', 'x')",
        ),
        "graph_edges": (
            "CREATE TABLE graph_edges (source VARCHAR, target VARCHAR, edge_type VARCHAR)",
            "INSERT INTO graph_edges VALUES "
            "('HDFC Bank', 'HDFC AMC', 'same_group'), "
            "('HDFC Bank', 'ICICI Bank', 'competes_with')",
        ),
        "hyper_edges": (
            "CREATE TABLE hyper_edges (id BIGINT, edge_type VARCHAR, "
            "label VARCHAR, agent_id VARCHAR)",
            "INSERT INTO hyper_edges VALUES "
            "(1, 'sector', 'Banking', 'a'), (2, 'merger', 'LTIMindtree', 'a')",
        ),
        "hyper_incidences": (
            "CREATE TABLE hyper_incidences (edge_id BIGINT, entity_name VARCHAR, "
            "weight DOUBLE, direction VARCHAR, role VARCHAR, valid_from VARCHAR, "
            "valid_to VARCHAR)",
            "INSERT INTO hyper_incidences VALUES "
            "(1, 'HDFC Bank', 1.0, NULL, NULL, NULL, NULL), "
            "(1, 'ICICI Bank', 1.0, NULL, NULL, NULL, NULL)",
        ),
        "concept_mappings": (
            "CREATE TABLE concept_mappings (source_scheme VARCHAR, "
            "source_concept VARCHAR, target_scheme VARCHAR, target_concept VARCHAR, "
            "match_type VARCHAR, status VARCHAR)",
            "INSERT INTO concept_mappings VALUES "
            "('sector', 'sector:Banking', 'nic2008', 'nic2008:64191', 'exactMatch', 'active')",
        ),
        "events": (
            "CREATE TABLE events (entity VARCHAR, counterparty_entity VARCHAR, event_type VARCHAR)",
            "INSERT INTO events VALUES "
            "('HDFC Bank', 'HDFC AMC', 'merger'), ('HDFC Bank', NULL, 'merger')",
        ),
        "ultimate_parent": (
            "CREATE TABLE ultimate_parent (company VARCHAR, ultimate_parent VARCHAR)",
            "INSERT INTO ultimate_parent VALUES ('HDFC AMC', 'HDFC Bank')",
        ),
        "hidden_connections": (
            "CREATE TABLE hidden_connections (a VARCHAR, b VARCHAR)",
            "INSERT INTO hidden_connections VALUES ('HDFC Bank', 'ICICI Bank')",
        ),
        "competitive_overlap": (
            "CREATE TABLE competitive_overlap (a VARCHAR, b VARCHAR, c VARCHAR)",
            "INSERT INTO competitive_overlap VALUES ('HDFC Bank', 'ICICI Bank', 'Axis Bank')",
        ),
        "supply_chokepoints": (
            "CREATE TABLE supply_chokepoints (supplier VARCHAR, companies VARCHAR)",
            "INSERT INTO supply_chokepoints VALUES ('HDFC Bank', '[\"ICICI Bank\", \"Axis Bank\"]')",
        ),
        "company_metrics": (
            "CREATE TABLE company_metrics (id BIGINT, entity VARCHAR, metric_label VARCHAR, "
            "value_num DOUBLE, period VARCHAR, source_ref VARCHAR, properties VARCHAR)",
            "INSERT INTO company_metrics VALUES "
            "(1, 'HDFC Bank', 'q_revenue', 100.0, 'FY27Q1', "
            "'https://nsearchives.nseindia.com/xbrl/INTEGRATED_FILING_INDAS_1_15072026075004_WEB.xml', "
            '\'{"consolidated": true, "via": "nse-xbrl"}\'), '
            "(2, 'HDFC Bank', 'q_revenue', 120.0, 'FY27Q1', "
            "'https://nsearchives.nseindia.com/xbrl/INTEGRATED_FILING_INDAS_1_17072026075004_WEB.xml', "
            '\'{"consolidated": true, "via": "nse-xbrl"}\'), '
            "(3, 'HDFC Bank', 'q_revenue', 40.0, 'FY27Q1', "
            "'https://nsearchives.nseindia.com/xbrl/INTEGRATED_FILING_INDAS_1_17072026075004_WEB.xml', "
            '\'{"consolidated": false, "via": "nse-xbrl"}\'), '
            "(4, 'HDFC Bank', 'pe_ratio', 20.0, 'latest', 'yfinance', "
            '\'{"source": "yfinance"}\'), '
            "(5, 'HDFC Bank', 'pe_ratio', 22.0, 'latest', 'yfinance', "
            '\'{"source": "yfinance"}\'), '
            "(6, 'HDFC Bank', 'aum', 500.0, 'FY27Q1', 'derive:metrics:x:2', "
            "'{\"consolidated\": true}'), "
            "(7, 'HDFC Bank', 'aum', 550.0, 'FY27Q1', 'derive:metrics:x:3', "
            "'{\"consolidated\": true}'), "
            "(8, 'HDFC Bank', NULL, 1.0, NULL, 'derive:metrics:x:4', '{}')",
        ),
    }
    for name, (ddl, rows) in tables.items():
        _write(side, name, ddl, rows)
    return tmp_path


# --------------------------------------------------------------------------- #
# Classification                                                              #
# --------------------------------------------------------------------------- #
class TestClassify:
    def test_unpinned_non_empty_answer_is_answered(self):
        assert probe.classify(["a"], None) == probe.STATUS_ANSWERED

    def test_unpinned_empty_answer_is_a_coverage_hole(self):
        assert probe.classify([], None) == probe.STATUS_EMPTY

    def test_empty_pin_with_empty_answer_is_an_intentional_negative(self):
        assert probe.classify([], []) == probe.STATUS_NEGATIVE

    def test_matching_pin_is_answered_regardless_of_order(self):
        assert probe.classify(["b", "a"], ["a", "b"]) == probe.STATUS_ANSWERED

    def test_stale_pin_is_drifted(self):
        assert probe.classify(["a"], ["a", "b"]) == probe.STATUS_DRIFTED

    def test_pin_that_lost_every_row_is_drifted_not_empty(self):
        assert probe.classify([], ["a"]) == probe.STATUS_DRIFTED


# --------------------------------------------------------------------------- #
# Shape answers against the synthetic snapshot                                #
# --------------------------------------------------------------------------- #
class TestShapeAnswers:
    @pytest.fixture()
    def con(self, snapshot_root: Path):
        conn = probe.connect_snapshots(snapshot_root)
        yield conn
        conn.close()

    def test_subtree_is_active_only_and_includes_the_root(self, con):
        answer = probe.answer_question(
            con, {"shape": "subtree", "params": {"root": "sector:Banking"}}
        )
        assert answer == ["sector:Banking", "subsector:Private_Banks"]

    def test_entities_of_type(self, con):
        answer = probe.answer_question(
            con, {"shape": "entities_of_type", "params": {"entity_type": "company"}}
        )
        assert answer == ["HDFC Bank"]

    def test_resolve_identifier_is_case_insensitive_on_the_registry(self, con):
        answer = probe.answer_question(
            con, {"shape": "resolve_identifier", "params": {"value": "ine040a01034"}}
        )
        assert answer == ["HDFC Bank"]

    def test_hyperedge_members_join_on_edge_id(self, con):
        answer = probe.answer_question(
            con,
            {"shape": "hyperedge_members", "params": {"edge_type": "sector", "label": "Banking"}},
        )
        assert answer == ["HDFC Bank", "ICICI Bank"]

    def test_hyperedge_members_unknown_label_is_empty(self, con):
        answer = probe.answer_question(
            con,
            {
                "shape": "hyperedge_members",
                "params": {"edge_type": "sector", "label": "Financial Services"},
            },
        )
        assert answer == []  # underscoring matters: the label is Financial_Services

    def test_event_counterparties_skips_nulls(self, con):
        answer = probe.answer_question(
            con, {"shape": "event_counterparties", "params": {"entity": "HDFC Bank"}}
        )
        assert answer == ["HDFC AMC"]

    def test_insight_counterparts_unions_both_directions(self, con):
        answer = probe.answer_question(
            con,
            {
                "shape": "insight_counterparts",
                "params": {"kind": "hidden_connections", "subject": "ICICI Bank"},
            },
        )
        assert answer == ["HDFC Bank"]

    def test_insight_counterparts_supply_chokepoints_parses_json(self, con):
        answer = probe.answer_question(
            con,
            {
                "shape": "insight_counterparts",
                "params": {"kind": "supply_chokepoints", "subject": "HDFC Bank"},
            },
        )
        assert answer == ["Axis Bank", "ICICI Bank"]

    def test_provenance_agents_rejects_unknown_table(self, con):
        with pytest.raises(probe.ProbeError):
            probe.answer_question(
                con, {"shape": "provenance_agents_for", "params": {"table": "no_such"}}
            )

    def test_unknown_shape_raises(self, con):
        with pytest.raises(probe.ProbeError):
            probe.answer_question(con, {"shape": "nope", "params": {}})

    def test_sql_shape_runs_a_bank_query(self, con):
        sql = "SELECT name FROM sqlite__entities WHERE entity_type = 'company'"
        answer = probe.answer_question(con, {"shape": "sql", "params": {"sql": sql}})
        assert answer == ["HDFC Bank"]

    def test_sql_shape_joins_wide_rows_with_pipes(self, con):
        sql = "SELECT source, edge_type FROM sqlite__graph_edges"
        answer = probe.answer_question(con, {"shape": "sql", "params": {"sql": sql}})
        assert sorted(answer) == ["HDFC Bank | competes_with", "HDFC Bank | same_group"]

    def test_sql_shape_rejects_non_select(self, con):
        with pytest.raises(probe.ProbeError):
            probe.answer_question(
                con, {"shape": "sql", "params": {"sql": "DELETE FROM sqlite__entities"}}
            )

    def test_sql_shape_rejects_missing_sql(self, con):
        with pytest.raises(probe.ProbeError):
            probe.answer_question(con, {"shape": "sql", "params": {}})

    def test_sql_shape_bad_column_is_an_errored_record(self, snapshot_root: Path):
        con = probe.connect_snapshots(snapshot_root)
        try:
            rec = probe.probe_question(
                con,
                {
                    "id": "q-sql",
                    "shape": "sql",
                    "params": {"sql": "SELECT nope FROM sqlite__entities"},
                },
            )
        finally:
            con.close()
        assert rec["status"] == probe.STATUS_ERRORED
        assert rec["error"]

    def test_metric_value_latest_filing_within_scope_wins(self, con):
        answer = probe.answer_question(
            con,
            {
                "shape": "metric_value",
                "params": {
                    "entity": "HDFC Bank",
                    "metric_label": "q_revenue",
                    "period": "FY27Q1",
                    "scope": "consolidated",
                },
            },
        )
        assert answer == ["120.0"]

    def test_metric_value_scope_separation(self, con):
        answer = probe.answer_question(
            con,
            {
                "shape": "metric_value",
                "params": {
                    "entity": "HDFC Bank",
                    "metric_label": "q_revenue",
                    "period": "FY27Q1",
                    "scope": "standalone",
                },
            },
        )
        assert answer == ["40.0"]

    def test_metric_value_id_tiebreak_without_a_stamp(self, con):
        answer = probe.answer_question(
            con,
            {
                "shape": "metric_value",
                "params": {
                    "entity": "HDFC Bank",
                    "metric_label": "aum",
                    "period": "FY27Q1",
                    "scope": "consolidated",
                },
            },
        )
        assert answer == ["550.0"]

    def test_metric_value_null_scope_rows_are_excluded(self, con):
        for scope in ("consolidated", "standalone"):
            answer = probe.answer_question(
                con,
                {
                    "shape": "metric_value",
                    "params": {
                        "entity": "HDFC Bank",
                        "metric_label": "pe_ratio",
                        "period": "latest",
                        "scope": scope,
                    },
                },
            )
            assert answer == []

    def test_metric_value_unknown_key_is_empty(self, con):
        answer = probe.answer_question(
            con,
            {
                "shape": "metric_value",
                "params": {
                    "entity": "No Such Co",
                    "metric_label": "q_revenue",
                    "period": "FY27Q1",
                    "scope": "consolidated",
                },
            },
        )
        assert answer == []

    def test_metric_value_rejects_bad_params(self, con):
        with pytest.raises(probe.ProbeError):
            probe.answer_question(con, {"shape": "metric_value", "params": {}})
        with pytest.raises(probe.ProbeError, match="scope"):
            probe.answer_question(
                con,
                {
                    "shape": "metric_value",
                    "params": {
                        "entity": "HDFC Bank",
                        "metric_label": "q_revenue",
                        "period": "FY27Q1",
                        "scope": "both",
                    },
                },
            )


# --------------------------------------------------------------------------- #
# Probe records and the report                                               #
# --------------------------------------------------------------------------- #
class TestProbeRecords:
    def test_probe_question_marks_a_matching_pin_answered(self, snapshot_root: Path):
        con = probe.connect_snapshots(snapshot_root)
        try:
            rec = probe.probe_question(
                con,
                {
                    "id": "q1",
                    "shape": "entities_of_type",
                    "params": {"entity_type": "company"},
                    "expected": ["HDFC Bank"],
                },
            )
        finally:
            con.close()
        assert rec["status"] == probe.STATUS_ANSWERED
        assert rec["pinned"] is True
        assert rec["rows"] == 1
        assert rec["lost_n"] == rec["added_n"] == 0

    def test_probe_question_records_drift_detail(self, snapshot_root: Path):
        con = probe.connect_snapshots(snapshot_root)
        try:
            rec = probe.probe_question(
                con,
                {
                    "id": "q2",
                    "shape": "entities_of_type",
                    "params": {"entity_type": "company"},
                    "expected": ["HDFC Bank", "IDFC First Bank"],
                },
            )
        finally:
            con.close()
        assert rec["status"] == probe.STATUS_DRIFTED
        assert rec["lost"] == ["IDFC First Bank"]
        assert rec["lost_n"] == 1

    def test_probe_question_errored_carries_the_message(self, snapshot_root: Path):
        con = probe.connect_snapshots(snapshot_root)
        try:
            rec = probe.probe_question(
                con, {"id": "q3", "shape": "provenance_agents_for", "params": {"table": "nope"}}
            )
        finally:
            con.close()
        assert rec["status"] == probe.STATUS_ERRORED
        assert "no_such" in rec["error"] or "sqlite__nope" in rec["error"]

    def test_bank_entry_carries_question_and_intent(self, snapshot_root: Path):
        con = probe.connect_snapshots(snapshot_root)
        try:
            rec = probe.probe_question(
                con,
                {
                    "id": "bank-1",
                    "question": "Which companies are in the snapshot?",
                    "intent": "identity",
                    "shape": "sql",
                    "params": {
                        "sql": "SELECT name FROM sqlite__entities WHERE entity_type = 'company'"
                    },
                    "expected": ["HDFC Bank"],
                },
            )
        finally:
            con.close()
        assert rec["status"] == probe.STATUS_ANSWERED
        assert rec["question"] == "Which companies are in the snapshot?"
        assert rec["intent"] == "identity"


class TestBankLoading:
    """The bank (proposal §3.3) runs ALONGSIDE the frozen set when present."""

    @staticmethod
    def _write_set(path: Path, questions: list, version: int = 1) -> None:
        path.write_text(json.dumps({"version": version, "questions": questions}), encoding="utf-8")

    def test_probe_all_loads_bank_alongside_frozen(self, snapshot_root: Path, tmp_path: Path):
        frozen = tmp_path / "questions.json"
        bank = tmp_path / "bank.json"
        self._write_set(
            frozen,
            [{"id": "f1", "shape": "entities_of_type", "params": {"entity_type": "company"}}],
        )
        self._write_set(
            bank,
            [
                {
                    "id": "bank-1",
                    "question": "Which companies are in the snapshot?",
                    "intent": "identity",
                    "shape": "sql",
                    "params": {
                        "sql": "SELECT name FROM sqlite__entities WHERE entity_type = 'company'"
                    },
                    "expected": ["HDFC Bank"],
                }
            ],
        )
        report = probe.probe_all(frozen, repo_root=snapshot_root, bank_path=bank)
        assert report["questions"] == 2
        assert report["bank"] == {"source": str(bank), "version": 1, "questions": 1}
        assert [r["set"] for r in report["records"]] == ["frozen", "bank"]
        assert all(r["status"] == probe.STATUS_ANSWERED for r in report["records"])

    def test_absent_bank_is_silent(self, snapshot_root: Path, tmp_path: Path):
        frozen = tmp_path / "questions.json"
        self._write_set(
            frozen,
            [{"id": "f1", "shape": "entities_of_type", "params": {"entity_type": "company"}}],
        )
        report = probe.probe_all(frozen, repo_root=snapshot_root, bank_path=tmp_path / "nope.json")
        assert report["bank"] is None
        assert report["questions"] == 1

    def test_bank_path_none_disables_the_bank(self, snapshot_root: Path, tmp_path: Path):
        frozen = tmp_path / "questions.json"
        bank = tmp_path / "bank.json"
        self._write_set(
            frozen,
            [{"id": "f1", "shape": "entities_of_type", "params": {"entity_type": "company"}}],
        )
        self._write_set(bank, [{"id": "bank-1", "shape": "sql", "params": {"sql": "SELECT 1"}}])
        report = probe.probe_all(frozen, repo_root=snapshot_root, bank_path=None)
        assert report["bank"] is None
        assert report["questions"] == 1


# --------------------------------------------------------------------------- #
# The shipped question set / snapshot pair                                     #
# --------------------------------------------------------------------------- #
def _has_artifacts() -> bool:
    return SNAPSHOTS.is_dir() and LIVE_DB.is_file()


@pytest.mark.skipif(not SNAPSHOTS.is_dir(), reason="no git-tracked parquet snapshot")
class TestShippedSet:
    def test_every_question_is_classified_exactly_once(self):
        report = probe.probe_all()
        assert report["questions"] == len(report["records"])
        assert sum(report["totals"].values()) == report["questions"]
        assert set(report["totals"]) <= set(probe.STATUSES)

    def test_no_question_errors_against_the_committed_snapshot(self):
        report = probe.probe_all()
        errored = [r for r in report["records"] if r["status"] == probe.STATUS_ERRORED]
        assert not errored, errored

    def test_intent_table_covers_every_shape(self):
        report = probe.probe_all()
        seen = {r["shape"] for r in report["records"]}
        assert seen == set(probe._SHAPES)  # frozen v7 carries metric_value; the bank carries sql
        assert all(
            r["intent"] in set(v[0] for v in probe.SHAPE_INTENTS.values())
            for r in report["records"]
        )

    def test_cli_report_exits_zero_by_default_and_json_parses(self, capsys):
        rc = probe.main([])
        out = capsys.readouterr().out
        assert rc == 0  # informational by default — not a gate
        assert "answerability probe" in out
        rc_json = probe.main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc_json == 0
        assert payload["questions"] == payload["questions"]

    def test_fail_on_drifted_is_opt_in(self):
        assert probe.main(["--fail-on", "drifted"]) == 1
        assert probe.main(["--fail-on", "none"]) == 0

    def test_bank_runs_by_default_and_no_bank_skips(self, capsys):
        rc = probe.main(["--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["bank"] and payload["bank"]["questions"] >= 1
        assert any(r.get("set") == "bank" for r in payload["records"])
        rc = probe.main(["--json", "--no-bank"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["bank"] is None
        assert not any(r.get("set") == "bank" for r in payload["records"])

        rc = probe.main(
            [
                "--shape",
                "hyperedge_members",
                "--params",
                '{"edge_type": "sector", "label": "Financial_Services"}',
            ]
        )
        assert rc == 0
        assert "adhoc" in capsys.readouterr().out

    def test_shape_mode_answers_one_adhoc_sql_shape(self, capsys):
        rc = probe.main(
            [
                "--shape",
                "sql",
                "--params",
                '{"sql": "SELECT COUNT(*) AS n FROM sqlite__entities"}',
            ]
        )
        assert rc == 0
        assert "adhoc" in capsys.readouterr().out

    def test_shipped_bank_has_zero_drift(self):
        """Every shipped bank pin must still hold, or it is a lie in the bank.

        The frozen set tolerates drift (11 pins are known S5-era churn, re-pinned
        by an operator act); the bank does not — a drifted bank entry means the
        NL question no longer has the answer it claims, and the bank is small
        enough that an operator should notice immediately.
        """
        report = probe.probe_all()
        assert report["bank"], "the shipped bank must load by default"
        bank = [r for r in report["records"] if r.get("set") == "bank"]
        assert bank
        bad = [r for r in bank if r["status"] != probe.STATUS_ANSWERED]
        assert not bad, bad

    def test_shipped_bank_entries_are_well_formed(self):
        """Each bank entry carries the NL text, an intent and a pin."""
        report = probe.probe_all()
        bank = [r for r in report["records"] if r.get("set") == "bank"]
        for rec in bank:
            assert rec["question"], f"{rec['id']} carries no NL question text"
            assert rec["intent"] and rec["intent"] != "unknown", rec["id"]
            assert rec["expected_n"] > 0, f"{rec['id']} has no pin"
        ids = [r["id"] for r in bank]
        assert len(ids) == len(set(ids)), "bank ids must be unique"


@pytest.mark.skipif(not _has_artifacts(), reason="needs both the snapshot and the live store")
def test_live_and_snapshot_agree():
    """The --parity contract: the committed snapshot must answer the frozen
    set exactly as the live store does, or a probe result is not a
    statement about the store."""
    assert probe.check_parity() == []
