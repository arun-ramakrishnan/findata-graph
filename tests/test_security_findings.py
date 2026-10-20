"""Tests for helpers/validators/security_findings.py — the findings validator.

Ported from cloudflare/security-audit-skill's `validate-findings.cjs` +
`report-schema.json` (MIT, (c) 2025-2026 Cloudflare, Inc.). The tests pin
the RULES, not the upstream engine: the three verdicts, the two
anti-inflation rules, the trace ordering, and the fingerprint discipline.

The load-bearing pair is the anti-inflation pair (R1, R2): without them a
review can report a claim whose evidence does not support its severity,
and that is the failure mode prose-only reporting cannot catch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.validators import security_findings as sf  # noqa: E402


def _trace(kind="entrypoint", file="app.py", line=10):
    return {
        "kind": kind,
        "file": file,
        "line": line,
        "scope": "handler",
        "description": "the request parameter enters the handler",
    }


def _confirmed(**over):
    finding = {
        "verdict": "confirmed",
        "fingerprint": "app.py:query-injection",
        "title": "SQL injection via the name parameter",
        "description": "The name parameter is concatenated into a query string.",
        "root_cause": "raw SQL string concatenation",
        "intended_behavior": "the parameter is bound",
        "trace": [_trace("entrypoint"), _trace("propagation", line=42), _trace("sink", line=77)],
        "evidence": [
            {"file": "app.py", "line": 77, "description": "the f-string builds the query"}
        ],
        "conditions": [{"kind": "authentication_level", "description": "any caller"}],
        "execution": {
            "attacker_perspective": "an unauthenticated caller",
            "payloads": ["' OR 1=1--"],
            "instructions": ["call the endpoint with the payload"],
            "observed_result": "the response returned rows from the users table",
        },
        "remediation": {"strategy": "bind the parameter"},
        "severity": {
            "likelihood": {"score": "high", "reason": "the parameter is unvalidated"},
            "impact": {"score": "medium", "reason": "read-only data exposure"},
            "overall_severity": "medium",
        },
        "confidence": {"score": "high", "reason": "the payload reproduced the read"},
    }
    finding.update(over)
    return finding


def _needs_validation(**over):
    finding = {
        "verdict": "needs_validation",
        "fingerprint": "app.py:maybe-race",
        "title": "Possible TOCTOU on the cache file",
        "description": "The cache is written without an atomic rename.",
        "claimed_root_cause": "a non-atomic write",
        "trace": [_trace("entrypoint"), _trace("sink", line=200)],
        "evidence": [{"file": "app.py", "line": 200, "description": "the write is not atomic"}],
        "blockers": ["the write happens in a vendored library"],
        "validation_plan": {"local": "reproduce with a concurrent reader"},
    }
    finding.update(over)
    return finding


def _rejected(**over):
    finding = {
        "verdict": "rejected",
        "fingerprint": "app.py:not-a-bug",
        "title": "Suspected path traversal in the doc route",
        "description": "The path segment is joined to the docs root.",
        "claimed_root_cause": "an unsanitised path join",
        "trace": [_trace("entrypoint"), _trace("sink", line=310)],
        "evidence": [
            {"file": "app.py", "line": 310, "description": "the join is bounded by the root"}
        ],
        "reason": "the join is bounded by the resolved docs root, so no traversal is reachable",
    }
    finding.update(over)
    return finding


def _write(tmp_path, findings):
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(findings, indent=1))
    return path


# ---------------------------------------------------------------------------
# the three verdicts
# ---------------------------------------------------------------------------


def test_a_valid_confirmed_finding_passes(tmp_path):
    path = _write(tmp_path, [_confirmed()])
    assert sf.check_findings(path) == 0


def test_a_valid_needs_validation_finding_passes(tmp_path):
    path = _write(tmp_path, [_needs_validation()])
    assert sf.check_findings(path) == 0


def test_a_valid_rejected_finding_passes(tmp_path):
    path = _write(tmp_path, [_rejected()])
    assert sf.check_findings(path) == 0


def test_an_unknown_verdict_fails(tmp_path, capsys):
    path = _write(tmp_path, [{"verdict": "probably-fine"}])
    assert sf.check_findings(path) == 1
    assert "verdict" in capsys.readouterr().out


def test_a_missing_required_field_fails(tmp_path, capsys):
    finding = _confirmed()
    del finding["root_cause"]
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "root_cause" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# R1 — the severity must not exceed the demonstrated impact
# ---------------------------------------------------------------------------


def test_severity_above_the_demonstrated_impact_fails(tmp_path, capsys):
    """R1: the overall grade is bounded by the impact that was shown."""
    finding = _confirmed(
        severity={
            "likelihood": {"score": "high", "reason": "the parameter is unvalidated"},
            "impact": {"score": "low", "reason": "a single row is read"},
            "overall_severity": "critical",
        }
    )
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    out = capsys.readouterr().out
    assert "cannot exceed demonstrated impact" in out


def test_severity_equal_to_the_impact_passes(tmp_path):
    finding = _confirmed(
        severity={
            "likelihood": {"score": "high", "reason": "the parameter is unvalidated"},
            "impact": {"score": "medium", "reason": "read-only data exposure"},
            "overall_severity": "medium",
        }
    )
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 0


def test_severity_below_the_impact_passes(tmp_path):
    finding = _confirmed(
        severity={
            "likelihood": {"score": "low", "reason": "needs an unusual caller"},
            "impact": {"score": "critical", "reason": "arbitrary write"},
            "overall_severity": "medium",
        }
    )
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 0


# ---------------------------------------------------------------------------
# R2 — an unvalidated claim carries no severity
# ---------------------------------------------------------------------------


def test_a_needs_validation_finding_with_a_severity_fails(tmp_path, capsys):
    """R2: an unvalidated claim has no demonstrated impact to grade."""
    finding = _needs_validation(
        severity={
            "likelihood": {"score": "high", "reason": "unvalidated"},
            "impact": {"score": "high", "reason": "unvalidated"},
            "overall_severity": "high",
        }
    )
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "must not contain 'severity'" in capsys.readouterr().out


def test_a_rejected_finding_with_a_severity_fails(tmp_path, capsys):
    finding = _rejected(severity={"overall_severity": "high"})
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "must not contain 'severity'" in capsys.readouterr().out


def test_a_rejected_finding_with_a_root_cause_fails(tmp_path, capsys):
    finding = _rejected(root_cause="an unsanitised path join")
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "must not contain 'root_cause'" in capsys.readouterr().out


def test_a_confirmed_finding_with_a_claimed_root_cause_fails(tmp_path, capsys):
    finding = _confirmed(claimed_root_cause="raw concatenation")
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "must not contain 'claimed_root_cause'" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the trace must read entrypoint -> propagation -> sink
# ---------------------------------------------------------------------------


def test_a_trace_that_does_not_start_at_the_entrypoint_fails(tmp_path, capsys):
    finding = _confirmed(trace=[_trace("propagation"), _trace("sink", line=77)])
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "must be 'entrypoint'" in capsys.readouterr().out


def test_a_trace_that_does_not_end_at_the_sink_fails(tmp_path, capsys):
    finding = _confirmed(trace=[_trace("entrypoint"), _trace("propagation", line=42)])
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "must be 'sink'" in capsys.readouterr().out


def test_a_one_line_trace_must_be_entrypoint_or_sink(tmp_path, capsys):
    finding = _confirmed(trace=[_trace("propagation")])
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "one-line trace" in capsys.readouterr().out


def test_a_one_line_trace_of_entrypoint_passes(tmp_path):
    finding = _confirmed(trace=[_trace("entrypoint")])
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 0


# ---------------------------------------------------------------------------
# fingerprints: unique, sorted, and stable across verdicts
# ---------------------------------------------------------------------------


def test_duplicate_fingerprints_fail(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed(), _confirmed()])
    assert sf.check_findings(path) == 1
    assert "duplicate" in capsys.readouterr().out


def test_unsorted_fingerprints_fail(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed(fingerprint="zzz"), _confirmed(fingerprint="aaa")])
    assert sf.check_findings(path) == 1
    assert "sorted lexicographically" in capsys.readouterr().out


def test_a_claim_keeps_its_fingerprint_when_its_verdict_changes(tmp_path):
    """A claim that moves from needs_validation to confirmed must not
    change identity — that is what makes the prior record findable.

    Upstream keeps ONE record per fingerprint and changes its verdict, so
    the two states are alternative records, never two records at once.
    """
    unvalidated = _needs_validation(fingerprint="app.py:maybe-race")
    validated = _confirmed(fingerprint="app.py:maybe-race")
    assert sf.check_findings(_write(tmp_path, [unvalidated])) == 0
    assert sf.check_findings(_write(tmp_path, [validated])) == 0


# ---------------------------------------------------------------------------
# paths and placeholders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd", "../outside.py", "a/../b.py", "C:/win.py", "dir./file.py", "con", "dir/aux"],
)
def test_unsafe_source_paths_fail(bad):
    assert sf.is_safe_relative_source_path(bad) is False


@pytest.mark.parametrize("good", ["app.py", "helpers/core/db.py", "src/a/b/c.ts"])
def test_safe_source_paths_pass(good):
    assert sf.is_safe_relative_source_path(good) is True


def test_an_unsafe_evidence_path_fails(tmp_path, capsys):
    finding = _confirmed(evidence=[{"file": "../etc/passwd", "line": 1, "description": "x"}])
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "safe repository-relative" in capsys.readouterr().out


def test_a_placeholder_description_fails(tmp_path, capsys):
    finding = _confirmed(description="TBD")
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "visible prose" in capsys.readouterr().out


def test_a_confirmed_finding_without_an_observed_result_fails(tmp_path, capsys):
    finding = _confirmed()
    del finding["execution"]["observed_result"]
    path = _write(tmp_path, [finding])
    assert sf.check_findings(path) == 1
    assert "observed_result" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the file-level contract
# ---------------------------------------------------------------------------


def test_a_missing_file_fails(tmp_path):
    assert sf.check_findings(tmp_path / "nope.json") == 1


def test_a_non_array_fails(tmp_path, capsys):
    path = tmp_path / "findings.json"
    path.write_text(json.dumps("not a list"))
    assert sf.check_findings(path) == 1
    assert "must be a JSON array or an envelope object" in capsys.readouterr().out


def test_an_envelope_without_findings_fails(tmp_path, capsys):
    path = tmp_path / "findings.json"
    path.write_text(json.dumps({"verdict": "confirmed"}))
    assert sf.check_findings(path) == 1
    assert "envelope needs a findings array" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the run envelope — one findings file per run, sharing the ledger's scopes
# ---------------------------------------------------------------------------


def _envelope(findings, **over):
    env = {
        "schema": "security-findings.v1",
        "run": "2026-10-21-app-1",
        "scope": ["app"],
        "ledger": "doc/security/coverage-ledger.json",
        "findings": findings,
    }
    env.update(over)
    return env


def test_a_valid_envelope_passes(tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_envelope([_confirmed()])))
    assert sf.check_findings(path) == 0, capsys.readouterr().out


def test_a_wrong_schema_fails(tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_envelope([_confirmed()], schema="security-findings.v0")))
    assert sf.check_findings(path) == 1
    assert "$.schema" in capsys.readouterr().out


def test_an_unknown_run_scope_fails(tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_envelope([_confirmed()], scope=["kubernetes"])))
    assert sf.check_findings(path) == 1
    assert "$.scope: 'kubernetes'" in capsys.readouterr().out


def test_an_empty_run_name_fails(tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_envelope([_confirmed()], run="  ")))
    assert sf.check_findings(path) == 1
    assert "$.run" in capsys.readouterr().out


def test_a_finding_cannot_widen_its_run_scope(tmp_path, capsys):
    finding = _confirmed()
    finding["scope"] = "graph"
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_envelope([finding], scope=["app"])))
    assert sf.check_findings(path) == 1
    assert "is not declared by the run" in capsys.readouterr().out


def test_a_finding_scope_in_the_run_is_accepted(tmp_path, capsys):
    finding = _confirmed()
    finding["scope"] = "app"
    path = tmp_path / "run.json"
    path.write_text(json.dumps(_envelope([finding], scope=["app"])))
    assert sf.check_findings(path) == 0, capsys.readouterr().out


def test_invalid_json_fails(tmp_path, capsys):
    path = tmp_path / "findings.json"
    path.write_text("{not json")
    assert sf.check_findings(path) == 1
    assert "not valid JSON" in capsys.readouterr().out


def test_the_counts_are_reported(tmp_path, capsys):
    # fingerprints must be sorted lexicographically
    path = _write(
        tmp_path,
        [
            _needs_validation(fingerprint="app.py:a-maybe-race"),
            _rejected(fingerprint="app.py:b-not-a-bug"),
            _confirmed(fingerprint="app.py:c-query-injection"),
        ],
    )
    assert sf.check_findings(path) == 0
    out = capsys.readouterr().out
    assert "confirmed 1" in out
    assert "needs_validation 1" in out
    assert "rejected 1" in out


# ---------------------------------------------------------------------------
# A6 — the verifier role: a blind brief, and a merge that cannot inflate
# ---------------------------------------------------------------------------


def _envelope(findings, **over):
    envelope = {
        "schema": "security-findings.v1",
        "run": "2026-10-21-app-1",
        "scope": ["app"],
        "findings": findings,
    }
    envelope.update(over)
    return envelope


def _result(tmp_path, **over):
    result = {
        "fingerprint": "app.py:query-injection",
        "verdict": "disproved",
        "method": "read the handler and its guard",
        "command": "none",
        "observed": "the parameter is bound by the query builder, so the f-string never reaches SQL",
    }
    result.update(over)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(result, indent=1))
    return path


def test_the_brief_carries_the_claim_and_the_anchors(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed()])
    assert sf.brief(path, index=0) == 0
    out = capsys.readouterr().out
    assert "SQL injection via the name parameter" in out
    assert "app.py:77" in out  # the evidence anchor
    assert "app.py:10" in out  # the entry point


def test_the_brief_is_blind_to_the_finders_reasoning(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed()])
    assert sf.brief(path, index=0) == 0
    out = capsys.readouterr().out
    # the mechanism under test must not reach the verifier
    assert "raw SQL string concatenation" not in out
    assert "the response returned rows from the users table" not in out
    assert "bind the parameter" not in out
    assert "the payload reproduced the read" not in out


def test_the_brief_reads_an_envelope(tmp_path, capsys):
    path = _write(tmp_path, _envelope([_confirmed()]))
    assert sf.brief(path, index=0) == 0
    out = capsys.readouterr().out
    assert "2026-10-21-app-1" in out
    assert "scope: app" in out


def test_the_brief_selects_by_fingerprint(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed(), _needs_validation()])
    assert sf.brief(path, fingerprint="app.py:maybe-race") == 0
    assert "Possible TOCTOU on the cache file" in capsys.readouterr().out


def test_the_brief_needs_a_selector(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed()])
    assert sf.brief(path) == 1
    assert "select one candidate" in capsys.readouterr().out


def test_the_brief_rejects_an_out_of_range_index(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed()])
    assert sf.brief(path, index=3) == 1
    assert "out of range" in capsys.readouterr().out


def test_the_json_brief_has_only_allowlisted_keys(tmp_path, capsys):
    path = _write(tmp_path, _envelope([_confirmed()]))
    assert sf.brief(path, index=0, as_json=True) == 0
    packet = json.loads(capsys.readouterr().out)
    assert set(packet) == {
        "schema",
        "fingerprint",
        "title",
        "surface",
        "attack_class",
        "evidence",
        "entrypoint",
        "contract",
        "run",
        "scope",
    }
    assert packet["schema"] == "verification-brief.v1"


def test_a_disproved_result_rejects_the_finding(tmp_path):
    path = _write(tmp_path, [_confirmed()])
    result = _result(tmp_path)
    merged = tmp_path / "merged.json"
    assert sf.record_verification(path, result, out=merged) == 0
    finding = json.loads(merged.read_text())[0]
    assert finding["verdict"] == "rejected"
    assert finding["claimed_root_cause"] == "raw SQL string concatenation"
    assert "the parameter is bound" in finding["reason"]
    assert finding["verification"]["verdict"] == "disproved"
    for field in ("severity", "execution", "remediation"):
        assert field not in finding
    assert sf.check_findings(merged) == 0


def test_a_reproduced_result_does_not_invent_missing_fields(tmp_path, capsys):
    path = _write(tmp_path, [_needs_validation()])
    result = _result(
        tmp_path,
        fingerprint="app.py:maybe-race",
        verdict="reproduced",
        observed="a concurrent reader observed a torn file",
    )
    merged = tmp_path / "merged.json"
    assert sf.record_verification(path, result, out=merged) == 0
    assert "NOT promoted" in capsys.readouterr().out
    finding = json.loads(merged.read_text())[0]
    assert finding["verdict"] == "needs_validation"
    assert "severity" not in finding
    assert sf.check_findings(merged) == 0


def test_a_reproduced_result_confirms_a_complete_record(tmp_path):
    path = _write(tmp_path, [_confirmed()])
    result = _result(
        tmp_path, verdict="reproduced", observed="the payload returned rows from the users table"
    )
    merged = tmp_path / "merged.json"
    assert sf.record_verification(path, result, out=merged) == 0
    finding = json.loads(merged.read_text())[0]
    assert finding["verdict"] == "confirmed"
    assert finding["verification"]["verdict"] == "reproduced"
    assert sf.check_findings(merged) == 0


def test_an_inconclusive_result_records_the_blocker(tmp_path):
    path = _write(tmp_path, [_needs_validation()])
    result = _result(
        tmp_path,
        fingerprint="app.py:maybe-race",
        verdict="inconclusive",
        observed="the write path needs a second process the probe cannot start",
    )
    merged = tmp_path / "merged.json"
    assert sf.record_verification(path, result, out=merged) == 0
    finding = json.loads(merged.read_text())[0]
    assert finding["verdict"] == "needs_validation"
    assert any("second process" in blocker for blocker in finding["blockers"])
    assert "severity" not in finding
    assert sf.check_findings(merged) == 0


def test_an_unknown_verifier_verdict_is_refused(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed()])
    result = _result(tmp_path, verdict="looks-bad")
    assert sf.record_verification(path, result, out=tmp_path / "merged.json") == 1
    assert "verdict" in capsys.readouterr().out


def test_a_result_for_an_unknown_fingerprint_is_refused(tmp_path, capsys):
    path = _write(tmp_path, [_confirmed()])
    result = _result(tmp_path, fingerprint="app.py:invented")
    assert sf.record_verification(path, result, out=tmp_path / "merged.json") == 1
    assert "expected exactly one finding" in capsys.readouterr().out


def test_the_merge_never_overwrites_its_input(tmp_path):
    path = _write(tmp_path, [_confirmed()])
    before = path.read_text()
    result = _result(tmp_path)
    merged = tmp_path / "merged.json"
    assert sf.record_verification(path, result, out=merged) == 0
    assert path.read_text() == before
    assert merged.exists()


def test_record_requires_an_out_path(tmp_path):
    path = _write(tmp_path, [_confirmed()])
    result = _result(tmp_path)
    with pytest.raises(SystemExit):
        sf._main_brief([str(path), "--index", "0", "--record", str(result)])
