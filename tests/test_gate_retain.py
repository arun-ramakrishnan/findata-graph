"""Unit tests for `helpers/misc/gate_retain.py`.

The module is the writer half of the artifact-retention contract: whatever
filename it derives, `gate_query output --leg` must derive the same one.
These cases pin the derivation rules — label sanitization, the `**Artifacts:**`
relative value, section naming, and bounded pruning — so a change here fails
loudly instead of silently orphaning retained logs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers.misc import gate_retain


def test_safe_label_flattens_path_separators() -> None:
    assert gate_retain.safe_label("tests/test_x.py::TestA::test_b") == (
        "tests_test_x.py::TestA::test_b"
    )
    assert gate_retain.safe_label("a\\b") == "a_b"
    assert gate_retain.safe_label("plain-label_1") == "plain-label_1"


def test_step_log_name_matches_the_reader_derivation() -> None:
    # gate_query.cmd_output builds f"{gate}.{_safe_step_label(label)}.log"
    assert gate_retain.step_log_name("perf", "derive_events") == "perf.derive_events.log"
    assert gate_retain.step_log_name("maint", "graph/store") == "maint.graph_store.log"


def test_artifacts_root_keys_off_the_report_file(tmp_path: Path) -> None:
    report = tmp_path / "outputs" / "perf_report.md"
    assert gate_retain.artifacts_root_for(report) == tmp_path / "outputs" / ".artifacts"


def test_artifact_meta_value_is_relative_to_the_outputs_root(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    root = outputs / ".artifacts"
    run = gate_retain.run_dir(root, "perf", "20261109T120000-000001")
    assert run.is_dir()
    # the stamped value is what gate_query resolves against the outputs root
    assert gate_retain.artifact_meta_value(outputs, run) == (
        ".artifacts/perf/20261109T120000-000001"
    )
    # a run dir outside the given base degrades to the bare name, never a ".." path
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert gate_retain.artifact_meta_value(outputs, other) == "elsewhere"


def test_run_id_is_sortable_and_unique() -> None:
    first = gate_retain.run_id_now()
    second = gate_retain.run_id_now()
    assert len(first) == len("20261109T120000-000001")
    assert first <= second


def test_section_name_drops_only_the_last_parenthetical() -> None:
    assert (
        gate_retain.section_name("## RELATIONS (ERROR-level; gate-failing)", "## ") == "RELATIONS"
    )
    assert gate_retain.section_name("### yaml_structure (1)", "### ") == "yaml_structure"
    assert gate_retain.section_name("## plain", "## ") == "plain"


def test_split_sections_returns_the_preamble_under_report() -> None:
    lines = ["# Title", "", "**Generated:** x", "## A (1)", "body a", "## B", "body b"]
    sections = dict(gate_retain.split_sections(lines))
    assert list(sections) == ["report", "A", "B"]
    assert sections["report"] == ["# Title", "", "**Generated:** x"]
    assert sections["A"] == ["## A (1)", "body a"]


def test_write_section_logs_names_files_after_the_headings(tmp_path: Path) -> None:
    lines = ["meta", "## RELATIONS (ERROR-level; gate-failing)", "-> errors=2"]
    written = gate_retain.write_section_logs(tmp_path, "integrity", lines)
    names = sorted(p.name for p in written)
    assert names == ["integrity.RELATIONS.log", "integrity.report.log"]
    assert "-> errors=2" in (tmp_path / "integrity.RELATIONS.log").read_text()


def test_write_step_log_keeps_the_header_and_the_full_body(tmp_path: Path) -> None:
    path = gate_retain.write_step_log(tmp_path / "x.log", "leg: cmd", "a\nb\nc\n")
    assert path.read_text() == "--- leg: cmd\na\nb\nc\n"


def test_prune_old_runs_keeps_the_newest(tmp_path: Path) -> None:
    for stamp in ("20260101T000000-000001", "20260102T000000-000001", "20260103T000000-000001"):
        gate_retain.run_dir(tmp_path, "perf", stamp).joinpath("perf.json").write_text("{}")
    removed = gate_retain.prune_old_runs(tmp_path, "perf", keep=2)
    assert [p.name for p in removed] == ["20260101T000000-000001"]
    assert sorted(p.name for p in (tmp_path / "perf").iterdir()) == [
        "20260102T000000-000001",
        "20260103T000000-000001",
    ]


@pytest.mark.parametrize("keep", [0, -1])
def test_prune_old_runs_is_inert_for_a_nonpositive_keep(tmp_path: Path, keep: int) -> None:
    gate_retain.run_dir(tmp_path, "perf", "20260101T000000-000001")
    assert gate_retain.prune_old_runs(tmp_path, "perf", keep=keep) == []
    assert (tmp_path / "perf" / "20260101T000000-000001").is_dir()
