"""Unit tests for helpers/misc/markdown_lint.py (offline; subprocess faked)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import helpers.misc.markdown_lint as ml  # noqa: E402

import pytest  # noqa: E402

# cli2 default-formatter shapes the digest regex must parse (severity word
# present; column optional — see the MD047 line)
_VIOLATION_LINES = [
    "doc/improvements/proposals/x.md:10:1 error MD040/Fenced-code-language Language should be specified",
    "findata/Companies/UPL.md:214:145 error MD037/Multiple-spaces-in-emphasis Space found in emphasis markers",
    "findata/Companies/UPL.md:220 error MD047/Single-trailing-newline File should end with a single newline character",
    "findata/Points_And_Figures/Foo.md:35:1 error MD056/Table-column-count Values in table are inconsistent",
]
_NOISE_LINES = ["npx: installed 12 in 1.4s", "Summary: 4 error(s)"]


def test_skip_without_node_exits_zero(capsys, monkeypatch):
    monkeypatch.setattr(ml, "_NPX", None)
    assert ml.main([]) == 0
    assert "SKIP" in capsys.readouterr().out


def test_command_is_version_pinned(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(ml, "_NPX", "/usr/bin/npx")
    monkeypatch.setattr(ml.subprocess, "run", fake_run)
    assert ml.main([]) == 0
    cmd = seen["cmd"]
    assert f"markdownlint-cli2@{ml.MARKDOWNLINT_CLI2_VERSION}" in cmd
    assert "-y" in cmd
    # globs/config live in .markdownlint-cli2.jsonc — no path arguments
    assert cmd[2:].count("doc") == 0 and cmd[2:].count("findata") == 0


def test_digest_counts_rules_and_files():
    violations, files, rules = ml._digest(_VIOLATION_LINES + _NOISE_LINES)
    assert violations == 4
    assert files == 3
    assert rules == {"MD040": 1, "MD037": 1, "MD047": 1, "MD056": 1}


def test_digest_run_red_outputs_counts_and_passthrough_rc(capsys, monkeypatch):
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="\n".join(_VIOLATION_LINES + _NOISE_LINES))

    monkeypatch.setattr(ml, "_NPX", "/usr/bin/npx")
    monkeypatch.setattr(ml.subprocess, "run", fake_run)
    assert ml.main([]) == 1
    out = capsys.readouterr().out
    assert "4 violation(s) in 3 file(s)" in out
    assert "MD056  1" in out
    assert "exit 1 (findings present" in out


def test_digest_run_clean(capsys, monkeypatch):
    monkeypatch.setattr(ml, "_NPX", "/usr/bin/npx")
    monkeypatch.setattr(
        ml.subprocess, "run", lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="")
    )
    assert ml.main([]) == 0
    assert "clean" in capsys.readouterr().out


def test_execution_failure_surfaces_raw_tail(capsys, monkeypatch):
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=2, stdout="Error: malformed .markdownlint-cli2.jsonc")

    monkeypatch.setattr(ml, "_NPX", "/usr/bin/npx")
    monkeypatch.setattr(ml.subprocess, "run", fake_run)
    assert ml.main([]) == 2
    out = capsys.readouterr().out
    assert "malformed" in out and "FAILED" in out


def test_config_file_matches_pinned_helper():
    # The repo-root config must exist next to the pinned helper (S1 artifact).
    assert (PROJECT_ROOT / ".markdownlint-cli2.jsonc").is_file()


@pytest.mark.parametrize(
    ("arg", "full"),
    [([], False), (["--full"], True)],
)
def test_full_flag_selects_streaming_mode(monkeypatch, arg, full):
    seen: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(ml, "_NPX", "/usr/bin/npx")
    monkeypatch.setattr(ml.subprocess, "run", fake_run)
    assert ml.main(arg) == 0
    if full:
        assert "stdout" not in seen["kwargs"]  # streams, does not capture
    else:
        assert "stdout" in seen["kwargs"]
