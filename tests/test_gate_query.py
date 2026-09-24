"""gate_query tests — synthetic corpus round-trips (gate_run_search S3).

Every case builds a tmp outputs/ tree (GATE_QUERY_ROOT env), writes
report blocks in the exact house format (search_tui regexes parse them),
and drives the CLI functions directly.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from types import SimpleNamespace

from helpers.misc import gate_query as gq
from tests import conftest as test_conftest


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    root.mkdir()
    monkeypatch.setenv("GATE_QUERY_ROOT", str(root))
    monkeypatch.setenv("GATE_QUERY_DB", str(root / "gate_runs.duckdb"))
    monkeypatch.setattr(gq, "ROOT", root)
    monkeypatch.setattr(gq, "DB_PATH", root / "gate_runs.duckdb")
    return root


def test_connect_migrates_existing_test_facts(corpus):
    con = gq.duckdb.connect(str(corpus / "gate_runs.duckdb"))
    con.execute(
        """CREATE TABLE test_facts (
            run_id INTEGER NOT NULL,
            leg TEXT NOT NULL,
            node_id TEXT NOT NULL,
            file TEXT,
            outcome TEXT NOT NULL,
            seconds DOUBLE,
            phase TEXT,
            markers_json TEXT,
            file_line INTEGER,
            err_head TEXT,
            err_blob TEXT,
            error_fingerprint TEXT,
            worker TEXT,
            artifact_schema TEXT NOT NULL
        )"""
    )
    con.close()
    con = gq.connect()
    columns = {row[1] for row in con.execute("PRAGMA table_info('test_facts')").fetchall()}
    assert "phase_seconds_json" in columns
    con.close()


GATE_BLOCK = """# make qa — gate report

**Generated:** {gen}  ·  **Started:** {start}  ·  **Elapsed:** {elapsed}s  ·  **Python:** 3.14.0{extra}

**Exit:** {exit}

| Step | Time (s) | Status |
|---|---|---|
| lint | 0.50 | ✓ OK |
| pytest | 42.00 | {pyst} |
{extra_rows}| **{nok}/{nsteps} passed** | | **gate {verdict}** |

## pytest (OK)

pytest tail
{fail_section}"""

PERF_BLOCK = """# make perf — benchmark report

**Generated:** {gen}  ·  **Started:** {start}  ·  **Ended:** {gen}  ·  **Elapsed:** 10.0s  ·  **Python:** 3.14.0

| Benchmark | Time (s) | Budget | Status |
|---|---|---|---|
| graph_l1_centrality | 2.50 | 5.0s | ✓ OK |
| graph_l1_betweenness | {bt} | 8.0s | ✓ OK |
"""

JUNIT = """<testsuites><testsuite tests="2" failures="1">
  <testcase classname="tests.test_x" name="test_ok" time="0.10"/>
  <testcase classname="tests.test_x" name="test_bad" time="1.50">
    <failure message="assert 1 == 2">assert 1 == 2
 + where 1 = f()</failure>
  </testcase>
</testsuite></testsuites>"""

JUNIT_HISTORY_A = """<testsuites><testsuite tests="3" failures="1">
  <testcase classname="tests.test_history" name="test_stable" time="1.0">
    <properties><property name="pytest.mark.integration" value=""/></properties>
  </testcase>
  <testcase classname="tests.test_history" name="test_fail" time="2.0">
    <failure message="assert 1 == 2">old failure</failure>
  </testcase>
  <testcase classname="tests.test_history" name="test_removed" time="1.0"/>
</testsuite></testsuites>"""

JUNIT_HISTORY_B = """<testsuites><testsuite tests="3" failures="1">
  <testcase classname="tests.test_history" name="test_stable" time="3.0">
    <properties><property name="pytest.mark.integration" value=""/></properties>
  </testcase>
  <testcase classname="tests.test_history" name="test_fail" time="2.0"/>
  <testcase classname="tests.test_history" name="test_added" time="4.0">
    <failure message="new failure">new failure</failure>
  </testcase>
</testsuite></testsuites>"""

JUNIT_FACTS = """<testsuites><testsuite tests="5" failures="2" errors="1">
  <testcase classname="tests.test_facts" name="test_pass" time="0.10">
    <properties>
      <property name="pytest.mark.live" value=""/>
      <property name="worker" value="gw2"/>
      <property name="line" value="42"/>
    </properties>
  </testcase>
  <testcase classname="tests.test_facts" name="test_fail_a" time="1.50">
    <failure message="assert 1 == 2">/tmp/pytest-of-a/test.txt line 10 after 0.1s</failure>
  </testcase>
  <testcase classname="tests.test_facts" name="test_fail_b" time="1.50">
    <failure message="assert 1 == 2">/tmp/pytest-of-b/test.txt line 10 after 0.2s</failure>
  </testcase>
  <testcase classname="tests.test_facts" name="test_skip" time="0.01">
    <skipped message="skip"/>
  </testcase>
  <testcase classname="tests.test_facts" name="test_error" time="0.20">
    <error message="boom">ValueError: boom</error>
  </testcase>
</testsuite></testsuites>"""


def one(con, sql: str):
    """fetchone() with a non-None guard (ty-clean single-row reads)."""
    row = con.execute(sql).fetchone()
    assert row is not None
    return row


def _write(path: Path, text: str, mode: str = "a") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode) as f:
        f.write(text)
    # deterministic mtimes so junit window checks behave
    st = path.stat()
    os.utime(path, (time.time(), st.st_mtime))


def _gate_block(gen: str, start: str, *, fail: bool = False, exit_code: int | None = None) -> str:
    return GATE_BLOCK.format(
        gen=gen,
        start=start,
        elapsed=12.5,
        extra="  ·  **Commit:** abc1234" if exit_code is not None else "",
        exit=exit_code if exit_code is not None else (1 if fail else 0),
        fail_section="\n## static_checks (FAILED)\n\n✗ 4 static-check failure(s)\n" if fail else "",
        pyst="✗ FAIL" if fail else "✓ OK",
        extra_rows="" if not fail else "| static_checks | 0.40 | ✗ FAIL |\n",
        nok="1" if fail else "2",
        nsteps="3" if fail else "2",
        verdict="FAIL" if fail else "PASS",
    )


def test_refresh_latest_incremental(corpus):
    rep = corpus / "qa_report.md"
    _write(rep, _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50"))
    con = gq.connect()
    counts = gq.refresh(con)
    assert counts["runs"] == 1
    # re-refresh: nothing new
    assert gq.refresh(con)["runs"] == 0
    # append a second run: only new bytes parsed
    _write(rep, _gate_block("2026-09-23 11:00:00", "2026-09-23 10:59:50"))
    assert gq.refresh(con)["runs"] == 1
    runs = con.execute("SELECT generated_at FROM runs ORDER BY started_at").fetchall()
    assert len(runs) == 2
    con.close()


def test_pending_tail_not_indexed(corpus):
    rep = corpus / "qa_report.md"
    _write(rep, _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50"))
    _write(rep, "# make qa — gate report\n\n**Generated:** partial")  # no table yet
    con = gq.connect()
    counts = gq.refresh(con)
    assert counts["runs"] == 1 and counts["skipped_pending"] == 1
    n = one(con, "SELECT COUNT(*) FROM runs")[0]
    assert n == 1
    con.close()


def test_latest_and_failures(corpus):
    rep = corpus / "qa_report.md"
    _write(rep, _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50"))
    _write(rep, _gate_block("2026-09-23 11:00:00", "2026-09-23 10:59:50", fail=True, exit_code=1))
    con = gq.connect()
    gq.refresh(con)
    args = SimpleNamespace(wt="", gate="qa", run=None, json=False)
    digest = gq.cmd_latest(con, args)
    assert "10:59:50" in digest and "FAIL" in digest
    args2 = SimpleNamespace(
        wt="", gate="qa", run=None, json=False, full=False, brief=False, limit=20, any_run=False
    )
    fails = gq.cmd_failures(con, args2)
    assert "LEG FAIL: pytest" in fails and "LEG FAIL: static_checks" in fails
    con.close()


def test_junit_ingestion_and_fallback(corpus):
    rep = corpus / "qa_report.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write(rep, _gate_block(now, now, fail=True))
    junit_dir = corpus / ".junit"
    _write(junit_dir / "qa.junit.xml", JUNIT)
    # junit mtime now; make the run window cover it by regenerating the
    # report's Generated from NOW (parser allows [start-120s, gen+300s])
    con = gq.connect()
    gq.refresh(con)
    rows = con.execute("SELECT node_id, outcome FROM tests WHERE outcome != 'passed'").fetchall()
    assert ("tests/test_x.py::test_bad", "failed") in rows
    # -ra fallback line from the report text also lands (legacy blocks)
    con.close()


def test_test_metadata_writer_emits_phases_and_markers(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    manifest = tmp_path / "qa.metadata.json"
    config = SimpleNamespace(getoption=lambda name: str(manifest))
    session = SimpleNamespace(config=config)
    monkeypatch.setattr(test_conftest, "_test_metadata_items", {})
    monkeypatch.setattr(test_conftest, "_test_metadata_reports", {})
    node_id = "tests/test_x.py::test_a"
    monkeypatch.setitem(
        test_conftest._test_metadata_items,
        node_id,
        {
            "node_id": node_id,
            "file": "tests/test_x.py",
            "file_line": 12,
            "markers": ["live"],
        },
    )
    monkeypatch.setitem(
        test_conftest._test_metadata_reports,
        node_id,
        {"call": {"seconds": 0.25, "outcome": "passed"}},
    )
    test_conftest._write_test_metadata(session)
    payload = json.loads(manifest.read_text())
    assert payload["schema"] == "test-metadata.v1"
    assert payload["tests"][0]["markers"] == ["live"]
    assert payload["tests"][0]["phases"]["call"]["seconds"] == 0.25


def test_test_metadata_writer_filters_unexecuted_xdist_items(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    manifest = tmp_path / "qa.metadata.json"
    config = SimpleNamespace(getoption=lambda name: str(manifest))
    session = SimpleNamespace(config=config)
    monkeypatch.setattr(test_conftest, "_test_metadata_items", {})
    monkeypatch.setattr(test_conftest, "_test_metadata_reports", {})
    monkeypatch.setitem(
        test_conftest._test_metadata_items,
        "tests/test_x.py::test_run",
        {
            "node_id": "tests/test_x.py::test_run",
            "file": "tests/test_x.py",
            "file_line": 1,
            "markers": [],
        },
    )
    monkeypatch.setitem(
        test_conftest._test_metadata_items,
        "tests/test_x.py::test_other_worker",
        {
            "node_id": "tests/test_x.py::test_other_worker",
            "file": "tests/test_x.py",
            "file_line": 2,
            "markers": [],
        },
    )
    monkeypatch.setitem(
        test_conftest._test_metadata_reports,
        "tests/test_x.py::test_run",
        {"call": {"seconds": 0.1, "outcome": "passed"}},
    )
    test_conftest._write_test_metadata(session)
    worker_path = tmp_path / "qa.metadata.gw0.json"
    payload = json.loads(worker_path.read_text())
    assert payload["worker"] == "gw0"
    assert [item["node_id"] for item in payload["tests"]] == ["tests/test_x.py::test_run"]


def test_junit_facts_normalize_metadata_and_fingerprints(corpus):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write(corpus / "qa_report.md", _gate_block(now, now, fail=True))
    _write(corpus / ".junit" / "qa.junit.xml", JUNIT_FACTS)
    _write(
        corpus / ".junit" / "qa.metadata.json",
        json.dumps(
            {
                "schema": "test-metadata.v1",
                "worker": "gw2",
                "tests": [
                    {
                        "node_id": "tests/test_facts.py::test_pass",
                        "file": "tests/test_facts.py",
                        "file_line": 42,
                        "markers": ["live"],
                        "phases": {
                            "setup": {"seconds": 0.01, "outcome": "passed"},
                            "call": {"seconds": 0.09, "outcome": "passed"},
                        },
                        "worker": "gw2",
                    }
                ],
            }
        ),
    )
    con = gq.connect()
    gq.refresh(con)
    rows = con.execute(
        "SELECT node_id, file, outcome, phase, phase_seconds_json, markers_json, file_line, "
        "worker, error_fingerprint, artifact_schema FROM test_facts ORDER BY node_id"
    ).fetchall()
    assert len(rows) == 5
    passed = next(row for row in rows if row[0].endswith("::test_pass"))
    assert passed[1] == "tests/test_facts.py"
    assert passed[2] == "passed"
    assert passed[3] == "total"
    assert json.loads(passed[4])["call"]["seconds"] == 0.09
    assert passed[5] == '["live"]'
    assert passed[6] == 42
    assert passed[7] == "gw2"
    assert passed[9] == gq.TEST_ARTIFACT_SCHEMA
    failed = [row for row in rows if row[0].endswith(("::test_fail_a", "::test_fail_b"))]
    assert failed[0][8] == failed[1][8]
    assert all(row[8] for row in failed)
    assert one(con, "SELECT artifact_schema FROM runs")[0] == gq.TEST_ARTIFACT_SCHEMA
    con.close()


def test_retained_worktree_artifact_rebuilds_test_facts(corpus):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    artifact_dir = ".artifacts/qa/run-1"
    wt_root = corpus / "wt" / "graph_algos" / "outputs"
    retained = wt_root / artifact_dir
    _write(retained / "qa.junit.xml", JUNIT_FACTS)
    _write(
        retained / "qa.metadata.json",
        json.dumps(
            {
                "schema": "test-metadata.v1",
                "worker": "gw1",
                "tests": [
                    {
                        "node_id": "tests/test_facts.py::test_pass",
                        "file": "tests/test_facts.py",
                        "file_line": 42,
                        "markers": ["integration"],
                        "phases": {"call": {"seconds": 0.1, "outcome": "passed"}},
                        "worker": "gw1",
                    }
                ],
            }
        ),
    )
    block = _gate_block(now, now).replace(
        "**Python:** 3.14.0",
        f"**Python:** 3.14.0  ·  **Artifacts:** {artifact_dir}  ·  **Worktree:** graph_algos",
    )
    _write(wt_root / "qa_report.md", block)
    con = gq.connect()
    gq.refresh(con)
    row = one(
        con,
        "SELECT wt, artifact_dir, artifact_state, junit_path FROM runs",
    )
    assert row[:3] == ("graph_algos", artifact_dir, "retained")
    assert str(retained / "qa.junit.xml") in row[3]
    assert one(con, "SELECT COUNT(*) FROM test_facts")[0] == 5
    con.execute("DELETE FROM test_facts")
    gq.refresh(con, full=True)
    assert one(con, "SELECT COUNT(*) FROM test_facts")[0] == 5
    con.close()


def test_missing_retained_artifact_is_explicit(corpus):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = _gate_block(now, now).replace(
        "**Python:** 3.14.0",
        "**Python:** 3.14.0  ·  **Artifacts:** .artifacts/qa/missing",
    )
    _write(corpus / "qa_report.md", block)
    con = gq.connect()
    gq.refresh(con)
    assert one(con, "SELECT artifact_state FROM runs")[0] == "missing"
    assert one(con, "SELECT COUNT(*) FROM test_facts")[0] == 0
    con.close()


def test_corrupt_retained_artifact_is_explicit(corpus):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    artifact_dir = ".artifacts/qa/corrupt"
    _write(corpus / artifact_dir / "qa.junit.xml", "<testsuites>")
    block = _gate_block(now, now).replace(
        "**Python:** 3.14.0",
        f"**Python:** 3.14.0  ·  **Artifacts:** {artifact_dir}",
    )
    _write(corpus / "qa_report.md", block)
    con = gq.connect()
    gq.refresh(con)
    assert one(con, "SELECT artifact_state FROM runs")[0] == "corrupt"
    assert one(con, "SELECT COUNT(*) FROM test_facts")[0] == 0
    con.close()


def test_junit_suppresses_fallback_dupes(corpus):
    rep = corpus / "qa_report.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # block contains a -ra FAILED line AND junit covers the same failure
    block = _gate_block(now, now, fail=True) + "FAILED tests/test_x.py::test_bad - assert 1 == 2\n"
    _write(rep, block)
    _write(corpus / ".junit" / "qa.junit.xml", JUNIT)
    con = gq.connect()
    gq.refresh(con)
    rows = con.execute(
        "SELECT outcome, COUNT(*) FROM tests WHERE node_id = 'tests/test_x.py::test_bad' GROUP BY outcome"
    ).fetchall()
    assert rows == [("failed", 1)]  # junit once, fallback suppressed
    con.close()


def test_timing_history(corpus):
    rep = corpus / "perf_report.md"
    for i, (gen, bt) in enumerate(
        [("2026-09-23 10:00:00", "3.00"), ("2026-09-23 11:00:00", "4.00")]
    ):
        _write(rep, PERF_BLOCK.format(gen=gen, start=gen, bt=bt))
    con = gq.connect()
    gq.refresh(con)
    args = SimpleNamespace(leg="graph_l1_betweenness", last=10)
    out = gq.cmd_timing(con, args)
    assert "4.00s" in out and "median" in out and "budget 8.0s" in out
    con.close()


def test_compare_and_historical_test_queries(corpus):
    now = datetime.now()
    gen_a = (now - timedelta(minutes=2)).strftime(gq._TS_FMT)
    gen_b = (now - timedelta(minutes=1)).strftime(gq._TS_FMT)
    retained_a = corpus / ".artifacts" / "qa" / "run-a"
    retained_b = corpus / ".artifacts" / "qa" / "run-b"
    _write(retained_a / "qa.junit.xml", JUNIT_HISTORY_A)
    _write(retained_b / "qa.junit.xml", JUNIT_HISTORY_B)
    block_a = _gate_block(gen_a, gen_a).replace(
        "**Python:** 3.14.0",
        "**Python:** 3.14.0  ·  **Artifacts:** .artifacts/qa/run-a",
    )
    block_b = (
        _gate_block(gen_b, gen_b)
        .replace(
            "**Python:** 3.14.0",
            "**Python:** 3.14.0  ·  **Artifacts:** .artifacts/qa/run-b",
        )
        .replace("| pytest | 42.00 | ✓ OK |", "| pytest | 63.00 | ✓ OK |")
    )
    _write(corpus / "qa_report.md", block_a + block_b)
    con = gq.connect()
    gq.refresh(con)
    run_a, run_b = con.execute("SELECT run_id FROM runs ORDER BY started_at").fetchall()
    payload = json.loads(
        gq.cmd_compare(
            con,
            SimpleNamespace(run_a=run_a[0], run_b=run_b[0], gate="qa", json=True),
        )
    )
    assert payload["legs"]["changed"][0]["delta_s"] == 21.0
    assert payload["tests"]["failed_added"] == ["tests/test_history.py::test_added"]
    assert payload["tests"]["failed_removed"] == ["tests/test_history.py::test_fail"]
    assert payload["tests"]["outcome_changes"][0]["node_id"] == "tests/test_history.py::test_fail"
    assert "outcome changes" in gq.cmd_compare(
        con,
        SimpleNamespace(run_a=run_a[0], run_b=run_b[0], gate="qa", json=False),
    )
    legacy = json.loads(gq.cmd_tests(con, SimpleNamespace(run=run_a[0], outcome="all", json=True)))
    assert len(legacy) == 3
    history_args = SimpleNamespace(
        run=None,
        node="tests/test_history.py::test_stable",
        marker=None,
        slowest=False,
        last=10,
        gate="qa",
        wt="",
        json=True,
        outcome="all",
    )
    history = json.loads(gq.cmd_tests(con, history_args))
    assert [row["outcome"] for row in history] == ["passed", "passed"]
    assert history[0]["markers"] == ["integration"]
    history_args.node = None
    history_args.marker = "integration"
    marker_rows = json.loads(gq.cmd_tests(con, history_args))
    assert len(marker_rows) == 2
    history_args.marker = None
    history_args.slowest = True
    history_args.json = False
    slowest = gq.cmd_tests(con, history_args)
    assert "serial testcase seconds (not wall time)" in slowest
    con.close()


def test_failure_clusters_use_normalized_fingerprints(corpus):
    now = datetime.now()
    gen_a = (now - timedelta(minutes=2)).strftime(gq._TS_FMT)
    gen_b = (now - timedelta(minutes=1)).strftime(gq._TS_FMT)
    for run, generated in (("run-a", gen_a), ("run-b", gen_b)):
        _write(corpus / ".artifacts" / "qa" / run / "qa.junit.xml", JUNIT_FACTS)
        block = _gate_block(generated, generated).replace(
            "**Python:** 3.14.0",
            f"**Python:** 3.14.0  ·  **Artifacts:** .artifacts/qa/{run}",
        )
        _write(corpus / "qa_report.md", block)
    con = gq.connect()
    gq.refresh(con)
    clusters = json.loads(
        gq.cmd_clusters(
            con,
            SimpleNamespace(fingerprint=None, last=10, json=True),
        )
    )
    failure_cluster = next(
        cluster for cluster in clusters if len(cluster["affected_node_ids"]) == 2
    )
    assert failure_cluster["run_count"] == 2
    assert failure_cluster["distinct_commits"] == 1
    assert len(failure_cluster["sources"]) == 2
    assert all(source["junit"] for source in failure_cluster["sources"])
    selected = json.loads(
        gq.cmd_clusters(
            con,
            SimpleNamespace(fingerprint=failure_cluster["fingerprint"], last=10, json=True),
        )
    )
    assert len(selected) == 1
    assert "normalized failure cluster" in gq.cmd_clusters(
        con,
        SimpleNamespace(fingerprint=None, last=10, json=False),
    )
    con.close()


def test_timing_reports_budget_stats_and_serial_phases(corpus):
    now = datetime.now().strftime(gq._TS_FMT)
    retained = corpus / ".artifacts" / "qa" / "run-1"
    _write(retained / "qa.junit.xml", JUNIT_FACTS)
    _write(
        retained / "qa.metadata.json",
        json.dumps(
            {
                "schema": "test-metadata.v1",
                "tests": [
                    {
                        "node_id": "tests/test_facts.py::test_pass",
                        "phases": {
                            "setup": {"seconds": 0.2, "outcome": "passed"},
                            "call": {"seconds": 0.3, "outcome": "passed"},
                            "teardown": {"seconds": 0.1, "outcome": "passed"},
                        },
                    }
                ],
            }
        ),
    )
    block = _gate_block(now, now).replace(
        "**Python:** 3.14.0",
        "**Python:** 3.14.0  ·  **Artifacts:** .artifacts/qa/run-1",
    )
    _write(corpus / "qa_report.md", block)
    con = gq.connect()
    gq.refresh(con)
    out = gq.cmd_timing(
        con,
        SimpleNamespace(
            leg="pytest",
            last=10,
            pass_only=False,
            critical_path=True,
        ),
    )
    assert "stats:" in out and "p25" in out and "p75" in out
    assert "test phases (serial testcase seconds; not wall time)" in out
    assert "critical-path candidates" in out
    con.close()


def test_generic_artifact_records_include_report_legs_and_manifest(corpus):
    now = datetime.now().strftime(gq._TS_FMT)
    artifact_dir = ".artifacts/qa/run-1"
    retained = corpus / artifact_dir
    _write(
        retained / "ruff.json",
        json.dumps(
            [
                {
                    "filename": "helpers/example.py",
                    "code": "F401",
                    "message": "unused import",
                    "severity": "error",
                }
            ]
        ),
    )
    _write(
        retained / "ty.json",
        json.dumps({"diagnostics": [{"path": "app.py", "message": "unknown type"}]}),
    )
    _write(
        retained / "gate-artifacts.json",
        json.dumps(
            {
                "schema": gq.ARTIFACT_SCHEMA,
                "artifacts": [
                    {
                        "kind": "ruff",
                        "target": "helpers/example.py",
                        "status": "warn",
                        "message": "optional diagnostic",
                        "metadata": {"adapter": "test"},
                    }
                ],
            }
        ),
    )
    block = _gate_block(now, now, fail=True).replace(
        "**Python:** 3.14.0",
        f"**Python:** 3.14.0  ·  **Artifacts:** {artifact_dir}",
    )
    _write(corpus / "qa_report.md", block)
    con = gq.connect()
    gq.refresh(con)
    rows = con.execute(
        "SELECT kind, target, status, schema, source_rel FROM artifacts ORDER BY kind, target"
    ).fetchall()
    assert ("integrity", "static_checks", "fail", gq.ARTIFACT_SCHEMA, "qa_report.md") in rows
    assert (
        "ruff",
        "helpers/example.py",
        "warn",
        gq.ARTIFACT_SCHEMA,
        str(retained / "gate-artifacts.json"),
    ) in rows
    assert (
        "ruff",
        "helpers/example.py",
        "fail",
        "ruff-json.v1",
        str(retained / "ruff.json"),
    ) in rows
    assert (
        "ty",
        "app.py",
        "fail",
        "ty-json.v1",
        str(retained / "ty.json"),
    ) in rows
    args = SimpleNamespace(
        run=None,
        kind=None,
        status=None,
        gate="qa",
        wt="",
        last=10,
        json=True,
    )
    records = json.loads(gq.cmd_artifacts(con, args))
    assert len(records) == 5
    assert {record["metadata"].get("adapter") for record in records} == {
        "gate-report-leg",
        "test",
        "ruff-json",
        "ty-json",
    }
    args.json = False
    assert "artifact record" in gq.cmd_artifacts(con, args)
    con.close()


def test_native_artifact_adapters_cover_remaining_formats(corpus):
    now = datetime.now().strftime(gq._TS_FMT)
    artifact_dir = ".artifacts/qa/run-native"
    retained = corpus / artifact_dir
    _write(retained / "coverage.json", json.dumps({"totals": {"percent": 80, "fail_under": 90}}))
    _write(retained / "coverage.xml", "<coverage>")
    _write(
        retained / "perf.json",
        json.dumps(
            {
                "benchmarks": [
                    {"name": "small", "seconds": 1, "budget_s": 2},
                    {"name": "slow", "seconds": 3, "budget_s": 2},
                ]
            }
        ),
    )
    _write(
        retained / "integrity.json",
        json.dumps({"checks": [{"name": "foreign_keys", "ok": False, "summary": "orphan row"}]}),
    )
    _write(
        retained / "security.sarif",
        json.dumps(
            {
                "runs": [
                    {
                        "results": [
                            {
                                "ruleId": "SQL001",
                                "uri": "app.py",
                                "message": {"text": "unsafe query"},
                            }
                        ]
                    }
                ]
            }
        ),
    )
    _write(retained / "secret-scan.json", "[]")
    _write(
        retained / "frontend.json",
        json.dumps(
            {"diagnostics": [{"file": "app.ts", "message": "implicit any", "severity": "warning"}]}
        ),
    )
    block = _gate_block(now, now).replace(
        "**Python:** 3.14.0",
        f"**Python:** 3.14.0  ·  **Artifacts:** {artifact_dir}",
    )
    _write(corpus / "qa_report.md", block)
    con = gq.connect()
    gq.refresh(con)
    rows = con.execute(
        "SELECT kind, target, status, schema, duration_s FROM artifacts ORDER BY kind, target"
    ).fetchall()
    assert ("coverage", "total", "fail", "coverage-json.v1", None) in rows
    assert ("perf", "small", "pass", "perf-json.v1", 1.0) in rows
    assert ("perf", "slow", "fail", "perf-json.v1", 3.0) in rows
    assert ("integrity", "foreign_keys", "fail", "integrity-json.v1", None) in rows
    assert ("security", "app.py", "fail", "sarif.v1", None) in rows
    assert ("security", "security", "pass", "secret-scan-json.v1", None) in rows
    assert ("frontend", "app.ts", "warn", "frontend-json.v1", None) in rows
    assert not any(row[3] == "coverage-xml.v1" for row in rows)
    assert len(rows) == 8
    con.close()


def test_rotation_archive_and_readback(corpus):
    rep = corpus / "qa_report.md"
    gens = [f"2026-09-23 {h:02d}:00:00" for h in range(8)]
    for g in gens:
        _write(rep, _gate_block(g, g))
    con = gq.connect()
    gq.refresh(con)
    args = SimpleNamespace(apply=False, keep_runs=4, max_mb=0.000001)  # force plan
    plan = gq.cmd_rotate(con, args)
    assert "ROTATION PLAN" in plan
    args.apply = True
    gq.cmd_rotate(con, args)
    # live file keeps last 4 runs; archive holds the first 4
    live = rep.read_text()
    assert (
        "2026-09-23 03:00:00" not in live
        and "2026-09-23 04:00:00" in live
        and "2026-09-23 07:00:00" in live
    )
    archs = list((corpus / "archives").rglob("*.zst"))
    assert len(archs) == 1
    from compression.zstd import decompress

    raw = decompress(archs[0].read_bytes()).decode()
    assert "2026-09-23 00:00:00" in raw and "2026-09-23 03:00:00" in raw
    # index still answers: 8 runs total, archived ones flagged
    n = one(con, "SELECT COUNT(*) FROM runs")[0]
    n_arch = one(con, "SELECT COUNT(*) FROM runs WHERE arch_path IS NOT NULL")[0]
    assert n == 8 and n_arch == 4
    # incremental refresh after rotation: no dupes, no re-parse
    counts = gq.refresh(con)
    assert counts["runs"] == 0
    assert one(con, "SELECT COUNT(*) FROM runs")[0] == 8
    con.close()


def test_worktree_copy_namespacing(corpus):
    wt_rep = corpus / "wt" / "graph_algos" / "outputs" / "qa_report.md"
    _write(wt_rep, _gate_block("2026-09-23 12:00:00", "2026-09-23 11:59:50"))
    con = gq.connect()
    gq.refresh(con)
    r = con.execute("SELECT wt, gate FROM runs").fetchone()
    assert r == ("graph_algos", "qa")
    con.close()


def test_enriched_meta_parsed(corpus):
    rep = corpus / "qa_report.md"
    _write(rep, _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50", exit_code=1))
    con = gq.connect()
    gq.refresh(con)
    commit, exit_code = one(con, "SELECT commit_sha, exit_code FROM runs")
    assert commit == "abc1234" and exit_code == 1
    con.close()


def test_default_gate_is_qa_and_all_escapes(corpus):
    # a NEWER perf run must not shadow the newest QA run (default gate: qa)
    _write(
        corpus / "perf_report.md",
        PERF_BLOCK.format(gen="2026-09-23 12:00:00", start="2026-09-23 12:00:00", bt="3.5"),
    )
    _write(corpus / "qa_report.md", _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50"))
    con = gq.connect()
    gq.refresh(con)
    args = SimpleNamespace(wt="", run=None, json=False)
    args.gate = "qa"
    assert "qa" in gq.cmd_latest(con, args)
    args.gate = "all"
    assert "perf" in gq.cmd_latest(con, args)
    con.close()


def test_recent_failures_cross_run(corpus):
    _write(corpus / "qa_report.md", _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50"))
    _write(
        corpus / "qa_report.md",
        _gate_block("2026-09-23 11:00:00", "2026-09-23 10:59:50", fail=True),
    )
    con = gq.connect()
    gq.refresh(con)
    args = SimpleNamespace(wt="", gate="qa", run=None, json=False)
    args.full, args.brief, args.limit, args.any_run = False, False, 20, False
    args.recent = 10
    out = gq.cmd_failures(con, args)
    assert "1 of the last 2 qa runs failed" in out
    assert "run 2" in out and "static_checks" in out and "pytest" in out
    con.close()


def test_legacy_exit_backfill_and_leg_err_head(corpus):
    rep = corpus / "qa_report.md"
    block = _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50", fail=True)
    legacy = block.replace("**Exit:** 1\n\n", "")  # pre-enrichment block
    assert "**Exit:**" not in legacy
    _write(rep, legacy)
    con = gq.connect()
    gq.refresh(con)
    (exit_code,) = one(con, "SELECT exit_code FROM runs")
    assert exit_code == 1  # derived from the gate FAIL verdict row
    (err_head,) = one(con, "SELECT err_head FROM legs WHERE leg = 'static_checks'")
    assert err_head and "static-check failure" in err_head
    con.close()


def test_wt_alias_visible_in_help(capsys):
    with pytest.raises(SystemExit) as e:
        gq.main(["latest", "--help"])
    assert e.value.code == 0
    out = " ".join(capsys.readouterr().out.split())  # argparse wraps lines
    assert "-wt" in out and "--wt" in out and "both work" in out


def test_wt_single_dash_alias(corpus, capsys, monkeypatch):
    _write(
        corpus / "wt" / "graph_algos" / "outputs" / "qa_report.md",
        _gate_block("2026-09-23 12:00:00", "2026-09-23 11:59:50"),
    )
    monkeypatch.chdir(corpus.parent)
    assert gq.main(["latest", "-wt", "graph_algos"]) == 0
    out = capsys.readouterr().out
    assert "qa@graph_algos" in out and "11:59:50" in out


def test_recent_history_view(corpus):
    # pass run, fail run, pass run — all three listed, newest first
    _write(corpus / "qa_report.md", _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50"))
    _write(
        corpus / "qa_report.md",
        _gate_block("2026-09-23 11:00:00", "2026-09-23 10:59:50", fail=True),
    )
    _write(corpus / "qa_report.md", _gate_block("2026-09-23 12:00:00", "2026-09-23 11:59:50"))
    con = gq.connect()
    gq.refresh(con)
    args = SimpleNamespace(wt="", gate="qa", last=10, tests=True)
    out = gq.cmd_recent(con, args)
    assert out.count("PASS") == 2 and out.count("FAIL") == 1
    assert "legs: pytest, static_checks" in out
    args2 = SimpleNamespace(wt="", gate="qa", last=10, tests=False)
    out2 = gq.cmd_recent(con, args2)
    assert "tests:" not in out2 and "FAIL" in out2
    con.close()


def test_latest_gate_all_shows_every_gate(corpus):
    _write(corpus / "qa_report.md", _gate_block("2026-09-23 10:00:00", "2026-09-23 09:59:50"))
    _write(
        corpus / "perf_report.md",
        PERF_BLOCK.format(gen="2026-09-23 11:00:00", start="2026-09-23 11:00:00", bt="3.5"),
    )
    con = gq.connect()
    gq.refresh(con)
    args = SimpleNamespace(wt="", run=None, json=False, gate="all")
    out = gq.cmd_latest(con, args)
    assert "newest run per gate:" in out and "qa" in out and "perf" in out
    con.close()


def test_cli_end_to_end(corpus, capsys, monkeypatch):
    rep = corpus / "perf_report.md"
    _write(rep, PERF_BLOCK.format(gen="2026-09-23 10:00:00", start="2026-09-23 10:00:00", bt="3.5"))
    monkeypatch.chdir(corpus.parent)
    assert gq.main(["latest", "--gate", "perf"]) == 0
    assert "graph_l1_betweenness" in capsys.readouterr().out
    assert gq.main(["timing", "--leg", "graph_l1_betweenness"]) == 0
    capsys.readouterr()
