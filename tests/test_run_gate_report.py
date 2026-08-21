"""Unit tests for the gate-report runner (tests/run_gate_report.py).

Exercises the runner mechanics against fake steps only — the real gates
(``make qa`` / ``make integration`` / ``make advisory``) stay manual runs.
"""
from __future__ import annotations

import sys

from tests import run_gate_report as rgr


def _fake_result(step: rgr.Step, rc: int, tail: list[str] | None = None) -> rgr.Result:
    return rgr.Result(step, seconds=0.01, rc=rc, tail=tail or ["fake-tail-line"])


def test_run_step_streams_and_captures(capsys):
    ok = rgr.Step("echo-ok", (sys.executable, "-c", "print('streamed-line')"))
    result = rgr.run_step(ok)
    assert result.rc == 0
    assert "streamed-line" in result.tail
    assert "streamed-line" in capsys.readouterr().out  # streamed live, not just captured

    bad = rgr.Step("echo-fail", (sys.executable, "-c", "import sys; sys.exit(3)"))
    assert rgr.run_step(bad).rc == 3


def test_fail_fast_skips_remaining(monkeypatch):
    failing = rgr.Step("first", ("true",), tail_on_success=True)
    never = rgr.Step("second", ("true",))
    calls: list[str] = []

    def fake_run_step(step):
        calls.append(step.label)
        return _fake_result(step, rc=1)

    monkeypatch.setattr(rgr, "run_step", fake_run_step)
    results = rgr.run_gate(rgr.Gate(steps=(failing, never), fail_fast=True))

    assert calls == ["first"]                      # second never ran
    assert results[1].skipped and results[1].status == "SKIP"
    assert not rgr.overall_ok(results)


def test_keep_going_and_nonblocking(monkeypatch):
    ty = rgr.Step("ty-tests", ("ty",), nonblocking=True)
    others = tuple(rgr.Step(f"s{i}", ("true",)) for i in range(3))

    def fake_run_step(step):
        # every step fails; a keep-going gate still runs them all
        return _fake_result(step, rc=1)

    monkeypatch.setattr(rgr, "run_step", fake_run_step)
    results = rgr.run_gate(rgr.Gate(steps=(ty, *others), fail_fast=False))

    assert all(not r.skipped for r in results)     # make -k semantics: nothing skipped
    assert not rgr.overall_ok(results)             # blocking failures fail the gate

    # flip ONLY the non-gating step to fail + the rest to pass: gate passes
    results[1:] = [_fake_result(r.step, rc=0) for r in results[1:]]
    assert rgr.overall_ok(results)                 # ty-tests rc=1 does not block (was || true)


def test_report_contents(tmp_path):
    ok_pytest = rgr.Step("pytest", ("pytest",), tail_on_success=True)
    ok_plain = rgr.Step("lint", ("ruff",))
    failed = rgr.Step("deptry", ("deptry",))
    results = [
        _fake_result(ok_pytest, 0, tail=["ra-summary-line"]),
        _fake_result(ok_plain, 0, tail=["plain-ok-tail"]),
        _fake_result(failed, 1, tail=["boom-line"]),
        rgr.Result(rgr.Step("skipped", ("x",)), skipped=True),
    ]
    report = tmp_path / "qa_report.txt"
    rgr.write_report(report, "qa", results)
    text = report.read_text()

    assert "=== make qa " in text                   # perf_report-style header
    assert "✓ OK" in text and "✗ FAIL" in text and "− SKIP" in text
    assert "2/4 passed  ·  gate FAIL" in text
    assert "ra-summary-line" in text                # tail_on_success kept on success
    assert "boom-line" in text                      # failing step tail kept
    assert "FAILED" in text
    assert "plain-ok-tail" not in text              # plain steps tail only on failure
    assert "success tail (-ra summary)" in text


def test_main_rejects_unknown_gate(capsys):
    assert rgr.main(["bogus"]) == 2
    assert "usage:" in capsys.readouterr().err
