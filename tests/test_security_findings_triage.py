"""Tests for the interactive verdict sitting in helpers/validators/security_findings.py.

The triage command walks a findings document (default: needs_validation
findings) and writes a merged copy validated through the same staged check
record_verification uses. The input is never overwritten implicitly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

from helpers.validators import security_findings as sf  # noqa: E402


def _trace():
    return [
        {
            "kind": "entrypoint",
            "file": "app.py",
            "line": 1,
            "scope": "app",
            "description": "route handler",
        },
        {
            "kind": "sink",
            "file": "app.py",
            "line": 2,
            "scope": "app",
            "description": "query executed",
        },
    ]


def _base(verdict="needs_validation", **over):
    f = {
        "verdict": verdict,
        "fingerprint": "a",
        "title": "injection on /api/thing",
        "description": "the handler interpolates a query string",
        "claimed_root_cause": "string concatenation in SQL",
        "trace": _trace(),
        "evidence": [
            {
                "kind": "source",
                "file": "app.py",
                "line": 1,
                "label": "query parameter",
                "description": "the raw query string is interpolated",
            }
        ],
    }
    if verdict == "needs_validation":
        f["blockers"] = ["needs a live probe"]
        f["validation_plan"] = {"local": "run the probe from helpers/misc"}
    elif verdict == "confirmed":
        f.update(
            {
                "root_cause": f["claimed_root_cause"],
                "intended_behavior": "parameterized query only",
                "conditions": [{"kind": "data_state", "description": "user input"}],
                "execution": {
                    "attacker_perspective": "send a crafted payload",
                    "observed_result": "the query fails",
                    "payloads": ["' OR 1=1"],
                    "instructions": ["run pytest -q tests/test_x.py"],
                },
                "remediation": {
                    "strategy": "use bound parameters",
                    "code_changes": [{"file_name": "app.py"}],
                },
                "severity": "high",
                "confidence": {"score": "high", "reason": "reproduced once"},
            }
        )
    elif verdict == "rejected":
        f["reason"] = "counterparty data is inert; no sink exists"
    f.update(over)
    return f


def _write_findings(tmp_path, findings, name="findings.json"):
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "schema": "security-findings.v1",
                "run": "2026-10-21-t-1",
                "scope": ["app"],
                "ledger": "doc/security/coverage-ledger.json",
                "findings": findings,
            },
            indent=2,
        )
        + "\n"
    )
    return path


def _answers(tmp_path, lines):
    path = tmp_path / "answers.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_triage_reject_confirm_keep_writes_valid_merged(tmp_path):
    find_path = _write_findings(
        tmp_path,
        [
            _base("needs_validation", fingerprint="aa"),
            _base(
                "needs_validation",
                fingerprint="bb",
                **{
                    # the record already carries the full confirmed shape (e.g.
                    # a previous verifier run completed it) — only the verdict
                    # is still pending.
                    "root_cause": "string concatenation in SQL",
                    "intended_behavior": "parameterized query only",
                    "conditions": [{"kind": "data_state", "description": "user input"}],
                    "execution": {
                        "attacker_perspective": "send a crafted payload",
                        "observed_result": "the query fails",
                        "payloads": ["' OR 1=1"],
                        "instructions": ["run pytest -q tests/test_x.py"],
                    },
                    "remediation": {
                        "strategy": "use bound parameters",
                        "code_changes": [{"file_name": "app.py"}],
                    },
                    "severity": {
                        "likelihood": {"score": "high", "reason": "trivial to craft"},
                        "impact": {"score": "high", "reason": "database read"},
                        "overall_severity": "high",
                        "overall_severity_reason": "full database read",
                    },
                    "confidence": {"score": "high", "reason": "reproduced once"},
                },
            ),
        ],
    )
    out = tmp_path / "merged.json"
    tape = [
        "r|probe shows the payload never reaches SQL — the claim is inert",
        "a",
        "y",  # batch confirm
    ]
    outcome = sf.triage(
        find_path,
        out=out,
        journal_dir=tmp_path / "review",
        input_fn=lambda _p: tape.pop(0) if tape else "q",
    )
    assert isinstance(outcome, dict)
    merged = json.loads(out.read_text())
    assert merged["schema"] == "security-findings.v1"
    a = merged["findings"][0]
    b = merged["findings"][1]
    assert a["verdict"] == "rejected"
    assert "never reaches SQL" in a["reason"]
    assert "blockers" not in a and "validation_plan" not in a
    assert b["verdict"] == "confirmed"
    assert "reason" not in b and "blockers" not in b
    assert sf.check_findings(out) == 0


def test_triage_confirm_incomplete_refused_no_write(tmp_path):
    find_path = _write_findings(tmp_path, [_base("needs_validation", fingerprint="aa")])
    out = tmp_path / "merged.json"
    tape = ["a", "q"]
    outcome = sf.triage(
        find_path,
        out=out,
        journal_dir=tmp_path / "review",
        input_fn=lambda _p: tape.pop(0) if tape else "q",
    )
    assert isinstance(outcome, dict)
    assert not out.exists()  # no transitions -> nothing written


def test_triage_requires_out_when_transitioning(tmp_path, capsys):
    find_path = _write_findings(tmp_path, [_base("needs_validation", fingerprint="aa")])
    tape = ["r|because", "y"]
    outcome = sf.triage(
        find_path,
        journal_dir=tmp_path / "review",
        input_fn=lambda _p: tape.pop(0) if tape else "q",
    )
    assert isinstance(outcome, dict)
    captured = capsys.readouterr().out
    assert "no --out" in captured
    assert not (tmp_path / "merged.json").exists()


def test_triage_menu_prompt_mutually_exclusive(tmp_path):
    find_path = _write_findings(tmp_path, [_base("needs_validation", fingerprint="aa")])
    with pytest.raises(ValueError):
        sf.triage(
            find_path,
            menu=True,
            prompt=True,
            answers_path=tmp_path / "x.txt",
            journal_dir=tmp_path / "review",
        )
    with pytest.raises(ValueError):
        sf.triage(find_path, prompt=True, journal_dir=tmp_path / "review")


def test_triage_scripted_mode_packets(tmp_path):
    find_path = _write_findings(
        tmp_path,
        [
            _base("needs_validation", fingerprint="aa"),
            _base("needs_validation", fingerprint="bb"),
        ],
    )
    out = tmp_path / "merged.json"
    answers = _answers(tmp_path, ["k", "k", "q"])
    packets = tmp_path / "packets.jsonl"
    outcome = sf.triage(
        find_path,
        out=out,
        prompt=True,
        answers_path=answers,
        packets_out=packets,
        journal_dir=tmp_path / "review",
    )
    assert isinstance(outcome, dict)
    lines = packets.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["schema"] == "review_menu/v1"
    assert first["surface"] == "findings"
    assert first["item_id"] in {"aa", "bb"}
