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


def test_sequential_gate_runs_each_step(monkeypatch):
    # 2026-08-25: run-all semantics — a failing step does NOT stop later
    # steps (was make's abort-at-first-failure; replaced by user directive).
    failing = rgr.Step("first", ("true",))
    second = rgr.Step("second", ("true",))
    calls: list[str] = []

    def fake_run_step(step):
        calls.append(step.label)
        return _fake_result(step, rc=1 if step.label == "first" else 0)

    monkeypatch.setattr(rgr, "run_step", fake_run_step)
    results = rgr.run_gate(rgr.Gate(steps=(failing, second)), jobs=1)

    assert calls == ["first", "second"]  # both ran
    assert results[1].rc == 0 and not results[1].skipped
    assert not rgr.overall_ok(results)  # gate still fails on step 1


def test_keep_going_and_nonblocking(monkeypatch):
    ty = rgr.Step("ty-tests", ("ty",), nonblocking=True)
    others = tuple(rgr.Step(f"s{i}", ("true",)) for i in range(3))

    def fake_run_step(step):
        # every step fails; a keep-going gate still runs them all
        return _fake_result(step, rc=1)

    monkeypatch.setattr(rgr, "run_step", fake_run_step)
    results = rgr.run_gate(rgr.Gate(steps=(ty, *others)))

    assert all(not r.skipped for r in results)  # make -k semantics: nothing skipped
    assert not rgr.overall_ok(results)  # blocking failures fail the gate

    # flip ONLY the non-gating step to fail + the rest to pass: gate passes
    results[1:] = [_fake_result(r.step, rc=0) for r in results[1:]]
    assert rgr.overall_ok(results)  # ty-tests rc=1 does not block (was || true)


def test_report_contents(tmp_path):
    # 2026-08-25: EVERY step's tail is logged (user directive — a passing
    # live-invariants run's warnings must reach advisory_report.txt).
    ok_pytest = rgr.Step("pytest", ("pytest",))
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

    assert "=== make qa " in text  # perf_report-style header
    assert "✓ OK" in text and "✗ FAIL" in text and "− SKIP" in text
    assert "2/4 passed  ·  gate FAIL" in text
    assert "ra-summary-line" in text  # passing pytest tail kept
    assert "plain-ok-tail" in text  # passing plain step tail kept too
    assert "boom-line" in text  # failing step tail kept
    assert "FAILED" in text and "(OK)" in text  # per-step status markers


def test_main_rejects_unknown_gate(capsys):
    assert rgr.main(["bogus"]) == 2
    assert "usage:" in capsys.readouterr().err


# ----- parallel mode (--jobs; 2026-08-25) ---------------------------------- #

_SLOW = (sys.executable, "-c", "import time; time.sleep(1.5)")


def test_parallel_runs_steps_concurrently(monkeypatch):
    # two 1.5s steps under jobs=2 must finish in < 2.5s (sequential: 3s)
    import time as _t

    gate = rgr.Gate(steps=(rgr.Step("slow1", _SLOW), rgr.Step("slow2", _SLOW)))
    t0 = _t.perf_counter()
    results = rgr.run_gate(gate, jobs=2)
    wall = _t.perf_counter() - t0
    assert all(r.rc == 0 for r in results)
    assert wall < 2.5, f"parallel gate took {wall:.2f}s (no speedup)"


def test_parallel_results_keep_gate_order():
    gate = rgr.Gate(
        steps=tuple(rgr.Step(f"s{i}", (sys.executable, "-c", f"print({i})")) for i in range(4))
    )
    results = rgr.run_gate(gate, jobs=4)
    assert [r.step.label for r in results] == ["s0", "s1", "s2", "s3"]


def test_run_all_semantics_no_abort_on_failure():
    # 2026-08-25 directive: run everything; failures surface at the end.
    # boom fails instantly; slow steps still run and are reported.
    boom_cmd = (sys.executable, "-c", "import sys; sys.exit(1)")
    gate = rgr.Gate(
        steps=(rgr.Step("boom", boom_cmd), rgr.Step("slow1", _SLOW), rgr.Step("slow2", _SLOW))
    )
    results = rgr.run_gate(gate, jobs=3)
    by_label = {r.step.label: r for r in results}
    assert by_label["boom"].rc == 1  # failure recorded
    assert by_label["slow1"].rc == 0  # but later steps RAN
    assert by_label["slow2"].rc == 0
    assert not rgr.overall_ok(results)  # gate still fails overall
    # parallel: total wall ~= max(step) ~= 1.5s, not sum(3s)
    assert results and all(not r.skipped for r in results)


def test_sequential_run_all_too(monkeypatch):
    # jobs=1 also runs every step (no abort-at-first-failure anymore)
    calls: list[str] = []

    def fake(step, *, jobs=1, out_lock=None):
        calls.append(step.label)
        rc = 1 if step.label == "first-fails" else 0
        return rgr.Result(step, seconds=0.01, rc=rc, tail=["x"])

    monkeypatch.setattr(rgr, "run_step", fake)
    gate = rgr.Gate(
        steps=(
            rgr.Step("first-fails", ("x",)),
            rgr.Step("second", ("x",)),
            rgr.Step("third", ("x",)),
        )
    )
    results = rgr.run_gate(gate, jobs=1)
    assert calls == ["first-fails", "second", "third"]
    assert not rgr.overall_ok(results)


def test_jobs_resolution_cli_makeflags_default(monkeypatch):
    # CLI flag wins over everything
    assert rgr.resolve_jobs(8) == 8
    # MAKEFLAGS -jN beats the default
    monkeypatch.setenv("MAKEFLAGS", "-j6 --jobserver-auth=3,4")
    assert rgr.resolve_jobs(None) == 6
    # bare make -j (jobserver, no count) -> every core
    monkeypatch.setenv("MAKEFLAGS", "R --jobserver-auth=3,4")
    assert rgr.resolve_jobs(None) == (rgr.os.cpu_count() or 4)
    # no override -> default 4
    monkeypatch.delenv("MAKEFLAGS", raising=False)
    assert rgr.resolve_jobs(None) == 4
    # MAKEFLAGS present but no -j (plain make) -> default 4
    monkeypatch.setenv("MAKEFLAGS", "R")
    assert rgr.resolve_jobs(None) == 4


def test_parallel_pytest_steps_get_private_cache_dirs(capsys):
    # jobs=2 + a pytest arg: the runner appends -o cache_dir=.pytest_cache/<label>
    step = rgr.Step("pytest-x", (sys.executable, "-m", "pytest", "--version"))
    res = rgr.run_step(step, jobs=2)
    assert res.rc == 0
    # header echoes the injected flag (proof of isolation)
    assert "cache_dir=.pytest_cache/pytest-x" in capsys.readouterr().out
    # sequential mode never injects
    capsys.readouterr()
    rgr.run_step(step, jobs=1)
    assert "cache_dir=" not in capsys.readouterr().out


def test_report_header_records_jobs(tmp_path):
    step = rgr.Step("s", ("x",))
    res = rgr.Result(step, seconds=0.01, rc=0, tail=["l"])
    report = tmp_path / "g_report.txt"
    rgr.write_report(report, "advisory", [res], jobs=4)
    assert "jobs=4" in report.read_text().splitlines()[0]
    rgr.write_report(report, "advisory", [res], jobs=1)
    # sequential runs keep the historical header (no jobs marker)
    headers = [ln for ln in report.read_text().splitlines() if ln.startswith("=== make advisory")]
    assert "jobs=" not in headers[-1]


def test_concurrent_report_blocks_never_interleave(tmp_path):
    # Two gate runners appending to the SAME report file concurrently
    # (advisory's nested integration sub-runner vs a parallel
    # `make integration`). Without the flock, two open("a") writers
    # interleave mid-line; with it, whole blocks serialize. flock
    # conflicts apply across separate open()s even in one process.
    import threading

    report = tmp_path / "integration_report.txt"

    def block(marker: str, n: int) -> rgr.Result:
        step = rgr.Step(f"{marker}-step", ("x",))
        return rgr.Result(step, seconds=0.1, rc=0, tail=[f"{marker}{i:04d}" for i in range(n)])

    barrier = threading.Barrier(2)
    errs: list[Exception] = []

    def writer(marker: str) -> None:
        try:
            barrier.wait(timeout=5)  # maximize the race window
            rgr.write_report(report, "probe", [block(marker, 120)], jobs=4)
        except Exception as e:  # pragma: no cover - surfaced via assert
            errs.append(e)

    ths = [threading.Thread(target=writer, args=(m,)) for m in ("A", "B")]
    for th in ths:
        th.start()
    for th in ths:
        th.join(timeout=10)
    assert not errs

    lines = report.read_text().splitlines()
    headers = [ln for ln in lines if ln.startswith("=== make probe")]
    assert len(headers) == 2  # two complete blocks
    # every KEPT tail line survived whole: the report keeps the last
    # _TAIL_LINES (60) of a 120-line capture — the cap itself is part of
    # the contract under test (whole lines, no mid-line interleave).
    for m in ("A", "B"):
        assert f"{m}0059" not in lines  # beyond the tail cap: dropped
        for i in range(60, 120):
            assert f"{m}{i:04d}" in lines
    # and no line mixes the two blocks' content
    assert not [ln for ln in lines if "A" in ln and "B" in ln]


def test_ty_tests_step_logs_every_diagnostic():
    # 2026-08-26 logging fix: full-format ty diagnostics are ~10-14 lines
    # each, so the 60-line report tail only showed the last ~5 of a burst
    # ("Found 91 diagnostics" era). The gate step must run the target in
    # CONCISE format (1 line/diagnostic) with a raised tail budget.
    step = [s for s in rgr.GATES["advisory"].steps if s.label == "ty-tests"][0]
    assert "TYPES_TESTS_FMT=concise" in step.args
    assert step.tail_lines == rgr._TY_TESTS_TAIL_LINES
    assert rgr._TY_TESTS_TAIL_LINES > rgr._TAIL_LINES


def test_write_report_honors_step_tail_lines(tmp_path):
    step = rgr.Step("digest-step", ("x",), tail_lines=3)
    res = rgr.Result(step, seconds=0.1, rc=0, tail=[f"line{i}" for i in range(10)])
    rgr.write_report(tmp_path / "r.txt", "probe", [res])
    out = (tmp_path / "r.txt").read_text()
    assert "last 3 lines" in out
    assert "line9" in out and "line6" not in out  # keeps the LAST 3 only


def test_advisory_gate_includes_search_freshness_checks():
    # 2026-08-26: the three search indexes (doc/, script metadata, note
    # embeddings) flag STALE as advisory steps — granular parallel rows,
    # one per index (user ran these manually before). Removing them from
    # the gate silently resurrects the manual ritual — this guards that.
    labels = [s.label for s in rgr.GATES["advisory"].steps]
    assert "doc-search-check" in labels
    assert "script-search-check" in labels
    assert "note-search-check" in labels
