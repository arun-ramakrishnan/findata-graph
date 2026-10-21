#!/usr/bin/env python3
"""Findings validator — the machine-checkable findings record (A4 + A5).

Ported from cloudflare/security-audit-skill's `validate-findings.cjs` +
`report-schema.json` (MIT, (c) 2025-2026 Cloudflare, Inc.). The RULES are
ported; the Node engine is not. See
doc/local/notes/cloudflare_audit.md §4 A4/A5.

WHY: our security review currently records its outcome as PROSE in a
gitignored report (`outputs/reviews/review_report_<from>..<to>.md`).
Prose is not checkable, so the two failure modes upstream names are
unfalsifiable for us:

  - a false positive that survives the review and gets reported as a
    finding, and
  - a finding with a severity its evidence does not support.

Both are structural, not stylistic. The upstream fix is to make every
finding carry a MACHINE-READABLE verdict and to make the inflation rules
part of the schema, not of the reviewer's memory.

The three verdicts (upstream `report-schema.json` -> `items.oneOf`):

  confirmed        — the claim is demonstrated. Carries root_cause,
                     intended_behavior, a trace, execution (with a
                     VISIBLE observed_result), remediation, severity,
                     confidence.
  needs_validation — the claim is plausible but the decisive fact is not
                     source-visible. Carries claimed_root_cause, blockers,
                     validation_plan. MUST NOT carry severity: an
                     unvalidated claim has no demonstrated impact, and
                     a severity on it is an inflation.
  rejected         — the claim was examined and did not survive. Carries
                     claimed_root_cause, reason. MUST NOT carry severity,
                     execution, remediation, blockers, validation_plan or
                     root_cause: a rejected claim is not a finding, and
                     dressing it as one inflates the count.

The two anti-inflation rules (A5), both from `validate-findings.cjs`:

  R1  severity.overall_severity MUST NOT exceed severity.impact.score.
      The overall grade is bounded by the impact that was DEMONSTRATED.
  R2  A needs_validation finding MUST NOT carry a severity at all.

Plus the structural rules the upstream validator enforces, ported:
fingerprints unique and sorted lexicographically; trace ordered
entrypoint -> propagation* -> sink; every trace/evidence file a safe
repository-relative path; every line a positive integer; a confirmed
finding's execution.observed_result and remediation.strategy must be
visible prose (not a placeholder).

Usage:
    security_findings.py findings.json            # check (exit 1 on error)
    security_findings.py findings.json --strict    # warnings become errors
    security_findings.py brief findings.json --index 0          # blind verifier packet
    security_findings.py brief findings.json --fingerprint FP   # the same, by fingerprint
    security_findings.py brief findings.json --fingerprint FP --record result.json --out merged.json

READ-ONLY in check and brief modes. It never invents a finding, and it never
approves one: `brief --record` writes ONLY the verifier's own record and the
verdict transition that record forces, to the path the operator names.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Run as a script (sys.path[0] == helpers/validators), so the repo root has to be
# on the path before the sibling validator can be imported by dotted name.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.validators.coverage_ledger import SCOPE_ENUM  # noqa: E402

DEFAULT_FINDINGS = REPO / "doc/local/security/findings.json"

# One findings file per audit RUN. The envelope names the run and the scope
# families it covers; findings carry the same scope vocabulary as the coverage
# ledger, so `plan --scope X` is the follow-up work order for a run.
SCHEMA = "security-findings.v1"

# Upstream SEVERITY_RANK (validate-findings.cjs:35).
SEVERITY_RANK = {
    "informational": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

VERDICTS = ("confirmed", "needs_validation", "rejected")

# Upstream report-schema.json fingerprint pattern.
FINGERPRINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")

# Upstream report-schema.json conditions[].kind enum.
CONDITION_KINDS = (
    "authentication_level",
    "authorization_role",
    "user_interaction",
    "system_configuration",
    "network_routing",
    "environmental_dependency",
    "data_state",
    "timing_dependency",
    "third_party_dependency",
)

TRACE_KINDS = ("entrypoint", "propagation", "sink")

# Fields each verdict must NOT carry (upstream validate-findings.cjs:550,
# :566, :576).
FORBIDDEN_FIELDS = {
    "confirmed": ("claimed_root_cause", "blockers", "validation_plan", "reason"),
    "needs_validation": ("execution", "remediation", "reason", "root_cause", "severity"),
    "rejected": (
        "severity",
        "execution",
        "remediation",
        "blockers",
        "validation_plan",
        "root_cause",
    ),
}

REQUIRED_FIELDS = {
    "confirmed": (
        "verdict",
        "fingerprint",
        "title",
        "description",
        "root_cause",
        "intended_behavior",
        "trace",
        "evidence",
        "conditions",
        "execution",
        "remediation",
        "severity",
        "confidence",
    ),
    "needs_validation": (
        "verdict",
        "fingerprint",
        "title",
        "description",
        "claimed_root_cause",
        "trace",
        "evidence",
        "blockers",
        "validation_plan",
    ),
    "rejected": (
        "verdict",
        "fingerprint",
        "title",
        "description",
        "claimed_root_cause",
        "trace",
        "evidence",
        "reason",
    ),
}

# Upstream validate-findings.cjs:471 isSafeRelativeSourcePath — a path that
# is relative, has no "..", no empty/dot segments, no trailing space or
# dot, no ":" or "\\" anywhere, and no Windows-reserved component. The
# reserved-component test matches the WHOLE segment, so "con.py" is fine
# but "con" is not.
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.IGNORECASE)


def is_safe_relative_source_path(path: str) -> bool:
    """True when `path` is a safe repository-relative source path."""
    if not path or path.strip() != path:
        return False
    if "\\" in path or ":" in path:
        return False
    if path.startswith("/") or path.startswith("~"):
        return False
    if re.match(r"^[A-Za-z]:", path):
        return False
    segments = path.split("/")
    for segment in segments:
        if segment in ("", ".", ".."):
            return False
        if segment.endswith(" ") or segment.endswith("."):
            return False
        if WINDOWS_RESERVED.match(segment):
            return False
    return True


def has_visible_prose(value: object) -> bool:
    """True for a string with visible, non-placeholder content.

    Upstream uses a "visibleContent" marker plus a placeholder check; the
    placeholder list upstream carries is long. We enforce the two that
    actually matter in practice: the string must be non-empty after
    stripping, and it must not be one of the obvious empty-answers.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return text.lower() not in {"tbd", "todo", "n/a", "na", "none", "unknown", "..."}


def _check_trace(finding: dict, base: str, errors: list[str]) -> None:  # noqa: C901
    """The trace must read entrypoint -> propagation* -> sink."""
    trace = finding.get("trace")
    if not isinstance(trace, list) or not trace:
        errors.append(f"{base}.trace: must be a non-empty array")
        return
    kinds = []
    for i, entry in enumerate(trace):
        if not isinstance(entry, dict):
            errors.append(f"{base}.trace[{i}]: must be an object")
            kinds.append("")
            continue
        kind = entry.get("kind")
        if kind not in TRACE_KINDS:
            errors.append(f"{base}.trace[{i}].kind: must be one of {TRACE_KINDS}, got {kind!r}")
        kinds.append(kind)
        for field in ("file", "line", "scope", "description"):
            if field not in entry:
                errors.append(f"{base}.trace[{i}]: missing required field {field!r}")
        line = entry.get("line")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            errors.append(f"{base}.trace[{i}].line: must be a positive integer, got {line!r}")
        file = entry.get("file")
        if isinstance(file, str) and not is_safe_relative_source_path(file):
            errors.append(
                f"{base}.trace[{i}].file: must be a safe repository-relative source path, got {file!r}"
            )
        if not has_visible_prose(entry.get("description")):
            errors.append(f"{base}.trace[{i}].description: must be visible prose")
        if not has_visible_prose(entry.get("scope")):
            errors.append(f"{base}.trace[{i}].scope: must be visible prose")

    if len(kinds) == 1:
        if kinds[0] not in ("entrypoint", "sink"):
            errors.append(f"{base}.trace[0].kind: a one-line trace must be 'entrypoint' or 'sink'")
        return
    if kinds and kinds[0] != "entrypoint":
        errors.append(f"{base}.trace[0].kind: must be 'entrypoint', got {kinds[0]!r}")
    if kinds and kinds[-1] != "sink":
        errors.append(f"{base}.trace[-1].kind: must be 'sink', got {kinds[-1]!r}")
    for i in range(1, len(kinds) - 1):
        if kinds[i] != "propagation":
            errors.append(f"{base}.trace[{i}].kind: must be 'propagation', got {kinds[i]!r}")


def _check_evidence(finding: dict, base: str, errors: list[str]) -> None:
    evidence = finding.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{base}.evidence: must be a non-empty array")
        return
    for i, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            errors.append(f"{base}.evidence[{i}]: must be an object")
            continue
        for field in ("file", "line", "description"):
            if field not in entry:
                errors.append(f"{base}.evidence[{i}]: missing required field {field!r}")
        line = entry.get("line")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            errors.append(f"{base}.evidence[{i}].line: must be a positive integer, got {line!r}")
        file = entry.get("file")
        if isinstance(file, str) and not is_safe_relative_source_path(file):
            errors.append(
                f"{base}.evidence[{i}].file: must be a safe repository-relative source path, got {file!r}"
            )
        if not has_visible_prose(entry.get("description")):
            errors.append(f"{base}.evidence[{i}].description: must be visible prose")


def _check_severity(finding: dict, base: str, errors: list[str]) -> None:
    """R1: overall_severity must not exceed the demonstrated impact."""
    severity = finding.get("severity")
    if not isinstance(severity, dict):
        errors.append(f"{base}.severity: must be an object")
        return
    for part in ("likelihood", "impact"):
        sub = severity.get(part)
        if not isinstance(sub, dict):
            errors.append(f"{base}.severity.{part}: must be an object")
            continue
        score = sub.get("score")
        if score not in SEVERITY_RANK:
            errors.append(
                f"{base}.severity.{part}.score: must be one of {tuple(SEVERITY_RANK)}, got {score!r}"
            )
        if not has_visible_prose(sub.get("reason")):
            errors.append(f"{base}.severity.{part}.reason: must be visible prose")
    overall = severity.get("overall_severity")
    if overall not in SEVERITY_RANK:
        errors.append(
            f"{base}.severity.overall_severity: must be one of {tuple(SEVERITY_RANK)}, got {overall!r}"
        )
    impact = severity.get("impact")
    impact_score = impact.get("score") if isinstance(impact, dict) else None
    if overall in SEVERITY_RANK and impact_score in SEVERITY_RANK:
        if SEVERITY_RANK[overall] > SEVERITY_RANK[impact_score]:
            errors.append(
                f"{base}.severity.overall_severity: cannot exceed demonstrated impact {impact_score!r}"
            )
    if not has_visible_prose(severity.get("overall_severity_reason", "")) and (
        "overall_severity_reason" in severity
    ):
        errors.append(f"{base}.severity.overall_severity_reason: must be visible prose")


def _check_confirmed(finding: dict, base: str, errors: list[str]) -> None:  # noqa: C901
    execution = finding.get("execution")
    if not isinstance(execution, dict):
        errors.append(f"{base}.execution: must be an object")
    else:
        for field in ("attacker_perspective", "observed_result"):
            if not has_visible_prose(execution.get(field)):
                errors.append(f"{base}.execution.{field}: must be visible prose")
        for field in ("payloads", "instructions"):
            value = execution.get(field)
            if not isinstance(value, list) or not value:
                errors.append(f"{base}.execution.{field}: must be a non-empty array")
    remediation = finding.get("remediation")
    if not isinstance(remediation, dict):
        errors.append(f"{base}.remediation: must be an object")
    else:
        if not has_visible_prose(remediation.get("strategy")):
            errors.append(f"{base}.remediation.strategy: must be visible prose")
        changes = remediation.get("code_changes")
        if changes is not None:
            if not isinstance(changes, list):
                errors.append(f"{base}.remediation.code_changes: must be an array")
            else:
                for i, change in enumerate(changes):
                    if not isinstance(change, dict):
                        errors.append(f"{base}.remediation.code_changes[{i}]: must be an object")
                        continue
                    name = change.get("file_name")
                    if not isinstance(name, str) or not is_safe_relative_source_path(name):
                        errors.append(
                            f"{base}.remediation.code_changes[{i}].file_name: "
                            f"must be a safe repository-relative source path, got {name!r}"
                        )
    confidence = finding.get("confidence")
    if not isinstance(confidence, dict):
        errors.append(f"{base}.confidence: must be an object")
    else:
        if confidence.get("score") not in ("low", "medium", "high"):
            errors.append(
                f"{base}.confidence.score: must be one of ('low','medium','high'), "
                f"got {confidence.get('score')!r}"
            )
        if not has_visible_prose(confidence.get("reason")):
            errors.append(f"{base}.confidence.reason: must be visible prose")
    conditions = finding.get("conditions")
    if not isinstance(conditions, list):
        errors.append(f"{base}.conditions: must be an array")
    else:
        for i, condition in enumerate(conditions):
            if not isinstance(condition, dict):
                errors.append(f"{base}.conditions[{i}]: must be an object")
                continue
            if condition.get("kind") not in CONDITION_KINDS:
                errors.append(
                    f"{base}.conditions[{i}].kind: must be one of {CONDITION_KINDS}, "
                    f"got {condition.get('kind')!r}"
                )
            if not has_visible_prose(condition.get("description")):
                errors.append(f"{base}.conditions[{i}].description: must be visible prose")


def _check_needs_validation(finding: dict, base: str, errors: list[str]) -> None:
    blockers = finding.get("blockers")
    if not isinstance(blockers, list) or not blockers:
        errors.append(f"{base}.blockers: must be a non-empty array")
    else:
        for i, blocker in enumerate(blockers):
            if not has_visible_prose(blocker):
                errors.append(f"{base}.blockers[{i}]: must be visible prose")
    plan = finding.get("validation_plan")
    if not isinstance(plan, dict):
        errors.append(f"{base}.validation_plan: must be an object")
    else:
        if not has_visible_prose(plan.get("local")) and not has_visible_prose(
            plan.get("deployment")
        ):
            errors.append(
                f"{base}.validation_plan: requires at least one visible local or deployment plan"
            )


def _unwrap(data: object, path: Path) -> tuple[list | None, dict | None]:
    """Split a findings file into (findings, envelope).

    Two shapes are accepted: a bare JSON array (the legacy shape, still what
    `--json` dumps) and an envelope {schema, run, scope[], ledger, findings[]}.
    """
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        findings = data.get("findings")
        if not isinstance(findings, list):
            print(f"FAIL: envelope needs a findings array: {path}")
            return None, None
        return findings, data
    print(f"FAIL: findings must be a JSON array or an envelope object: {path}")
    return None, None


def _check_envelope(env: dict) -> list[str]:
    """Validate the run envelope: schema, run id, scope families, ledger link."""
    errors: list[str] = []
    schema = env.get("schema")
    if schema is not None and schema != SCHEMA:
        errors.append(f"$.schema: must be {SCHEMA!r}, got {schema!r}")
    run = env.get("run")
    if not isinstance(run, str) or not run.strip():
        errors.append(
            "$.run: a run envelope must name the run, e.g. '2026-10-21-app-1' "
            "(the id the ledger history[] links back to)"
        )
    elif any(ch.isspace() for ch in run):
        errors.append(f"$.run: must not contain whitespace, got {run!r}")
    scopes = env.get("scope")
    if not isinstance(scopes, list) or not scopes:
        errors.append("$.scope: must be a non-empty array of scope families")
    else:
        for scope in scopes:
            if scope not in SCOPE_ENUM:
                errors.append(f"$.scope: {scope!r} not in {SCOPE_ENUM}")
    ledger = env.get("ledger")
    if ledger is not None and not isinstance(ledger, str):
        errors.append("$.ledger: must be the ledger path string this run updated")
    return errors


def check_findings(path: Path, *, strict: bool = False) -> int:  # noqa: C901
    """Validate a findings file. Returns 0 when clean, 1 when errors."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        print(f"FAIL: {path} not found")
        return 1
    except json.JSONDecodeError as exc:
        print(f"FAIL: {path} is not valid JSON: {exc}")
        return 1

    findings, envelope = _unwrap(data, path)
    if findings is None:
        return 1

    errors: list[str] = []
    warns: list[str] = []
    seen_fingerprints: dict[str, int] = {}
    previous: str | None = None

    run_scopes: set[str] = set()
    if envelope is not None:
        errors.extend(_check_envelope(envelope))
        run_scopes = {str(s) for s in envelope.get("scope") or [] if isinstance(s, str)}

    for index, finding in enumerate(findings):
        base = f"$[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{base}: must be an object")
            continue
        verdict = finding.get("verdict")
        if verdict not in VERDICTS:
            errors.append(f"{base}.verdict: must be one of {VERDICTS}, got {verdict!r}")
            continue

        fscope = finding.get("scope")
        if fscope is not None:
            if fscope not in SCOPE_ENUM:
                errors.append(f"{base}.scope: {fscope!r} not in {SCOPE_ENUM}")
            elif run_scopes and fscope not in run_scopes:
                errors.append(
                    f"{base}.scope: {fscope!r} is not declared by the run "
                    f"(scope={sorted(run_scopes)}); a finding cannot widen its run"
                )

        for field in REQUIRED_FIELDS[verdict]:
            if field not in finding:
                errors.append(f"{base}: {verdict} finding is missing required field {field!r}")
        for field in FORBIDDEN_FIELDS[verdict]:
            if field in finding:
                errors.append(f"{base}: {verdict} finding must not contain {field!r}")

        fingerprint = finding.get("fingerprint")
        if not isinstance(fingerprint, str) or not FINGERPRINT_RE.match(fingerprint):
            errors.append(
                f"{base}.fingerprint: must match {FINGERPRINT_RE.pattern}, got {fingerprint!r}"
            )
        else:
            if fingerprint in seen_fingerprints:
                errors.append(
                    f"{base}.fingerprint: duplicate of $[{seen_fingerprints[fingerprint]}].fingerprint"
                )
            else:
                seen_fingerprints[fingerprint] = index
            if previous is not None and previous > fingerprint:
                errors.append(f"{base}.fingerprint: findings must be sorted lexicographically")
            previous = fingerprint

        _check_trace(finding, base, errors)
        _check_evidence(finding, base, errors)

        if verdict == "confirmed":
            _check_severity(finding, base, errors)
            _check_confirmed(finding, base, errors)
        elif verdict == "needs_validation":
            _check_needs_validation(finding, base, errors)
        else:
            if not has_visible_prose(finding.get("reason")):
                errors.append(f"{base}.reason: must be visible prose")
            if not has_visible_prose(finding.get("claimed_root_cause")):
                errors.append(f"{base}.claimed_root_cause: must be visible prose")

        if not has_visible_prose(finding.get("title")):
            errors.append(f"{base}.title: must be visible prose")
        if not has_visible_prose(finding.get("description")):
            errors.append(f"{base}.description: must be visible prose")

    for w in warns:
        print(f"warn: {w}")
    for e in errors:
        print(f"FAIL: {e}")

    counts = {
        v: sum(1 for f in findings if isinstance(f, dict) and f.get("verdict") == v)
        for v in VERDICTS
    }
    print(
        f"findings: {len(findings)} total — confirmed {counts['confirmed']}, "
        f"needs_validation {counts['needs_validation']}, rejected {counts['rejected']}"
    )
    if errors:
        return 1
    print("ok: findings schema and anti-inflation rules satisfied")
    return 0


# --- A6: the verifier role ---------------------------------------------------
#
# The verifier is a ROLE, not a step: a fresh context, ONE candidate, blind to
# the finder's reasoning, whose only job is to reproduce or disprove. The
# blindness has to be mechanical, so the brief is built from an ALLOWLIST —
# anything not named below never reaches the verifier, even if the record grows
# a new field later. A denylist would leak every field added after it was
# written. `title` is the claim; the anchors are where to look; the mechanism
# (root_cause, trace propagation, execution, remediation, severity) is the
# thing under test and stays behind.

BRIEF_ANCHOR_KEYS = ("file", "line", "symbol")

VERIFIER_VERDICTS = ("reproduced", "disproved", "inconclusive")

VERIFIER_CONTRACT = """\
Return exactly one record, and nothing else:

  {"fingerprint": "<as printed above>",
   "verdict": "reproduced" | "disproved" | "inconclusive",
   "method": "<what you did, one line>",
   "command": "<the command you ran, or none for a source-only reading>",
   "observed": "<what you saw — quote the output, do not summarise it>",
   "notes": "<optional>"}

  reproduced    you observed the claimed behaviour.
  disproved     you observed the guard or constraint the claim says is absent.
  inconclusive  say what blocked you: a missing dependency, a destructive step,
                an environment you cannot reach. Inconclusive is NOT low
                severity. It is unknown.

Do not assign a severity. Do not propose a fix. Do not restate the claim.
"""


def _anchor(entry: object) -> dict:
    """The locator half of a trace/evidence entry — never its prose."""
    if not isinstance(entry, dict):
        return {}
    return {key: entry[key] for key in BRIEF_ANCHOR_KEYS if entry.get(key) is not None}


def _brief_packet(finding: dict, envelope: dict | None) -> dict:
    """The blind packet, as data. Only allowlisted fields are read."""
    entrypoint = next(
        (
            item
            for item in finding.get("trace") or []
            if isinstance(item, dict) and item.get("kind") == "entrypoint"
        ),
        None,
    )
    packet: dict = {
        "schema": "verification-brief.v1",
        "fingerprint": finding.get("fingerprint"),
        "title": finding.get("title"),
        "surface": finding.get("surface"),
        "attack_class": finding.get("attack_class"),
        "evidence": [_anchor(item) for item in finding.get("evidence") or [] if _anchor(item)],
        "entrypoint": _anchor(entrypoint) if entrypoint else None,
        "contract": VERIFIER_CONTRACT.strip(),
    }
    if envelope is not None:
        packet["run"] = envelope.get("run")
        packet["scope"] = envelope.get("scope")
    return packet


def _render_brief(packet: dict) -> str:
    lines = [
        f"# Verification brief — {packet['fingerprint']}",
        "",
        "You are the VERIFIER. You did not write this claim, you are not shown the",
        "finder's reasoning, and you are not being asked whether the claim is good.",
        "Reproduce it or disprove it.",
        "",
        f"- claim: {packet.get('title')}",
        f"- surface: {packet.get('surface') or '(not recorded)'}",
        f"- attack class: {packet.get('attack_class') or '(not recorded)'}",
    ]
    if "run" in packet:
        scopes = packet.get("scope") or []
        lines.append(f"- run: {packet.get('run')} (scope: {', '.join(map(str, scopes))})")
    lines += ["", "## Where to look"]
    for anchor in packet.get("evidence") or []:
        symbol = f" ({anchor['symbol']})" if anchor.get("symbol") else ""
        lines.append(f"- {anchor.get('file')}:{anchor.get('line')}{symbol}")
    if packet.get("entrypoint"):
        anchor = packet["entrypoint"]
        symbol = f" ({anchor['symbol']})" if anchor.get("symbol") else ""
        lines += [
            "",
            "## Entry point",
            f"- {anchor.get('file')}:{anchor.get('line')}{symbol}",
        ]
    lines += ["", "## Contract", "", packet["contract"], ""]
    return "\n".join(lines)


def brief(
    findings_path: Path,
    *,
    index: int | None = None,
    fingerprint: str | None = None,
    as_json: bool = False,
) -> int:
    """Emit a blind verification brief for exactly ONE finding (read-only)."""
    try:
        data = json.loads(findings_path.read_text())
    except FileNotFoundError:
        print(f"FAIL: {findings_path} not found")
        return 1
    except json.JSONDecodeError as exc:
        print(f"FAIL: {findings_path} is not valid JSON: {exc}")
        return 1

    findings, envelope = _unwrap(data, findings_path)
    if findings is None:
        return 1
    if index is None and fingerprint is None:
        print("FAIL: select one candidate with --index or --fingerprint")
        return 1
    if index is not None:
        if index < 0 or index >= len(findings) or not isinstance(findings[index], dict):
            print(f"FAIL: --index {index} is out of range (0..{len(findings) - 1})")
            return 1
        finding = findings[index]
    else:
        matches = [
            item
            for item in findings
            if isinstance(item, dict) and item.get("fingerprint") == fingerprint
        ]
        if len(matches) != 1:
            print(
                f"FAIL: expected exactly one finding with fingerprint {fingerprint!r}, got {len(matches)}"
            )
            return 1
        finding = matches[0]

    packet = _brief_packet(finding, envelope)
    if as_json:
        print(json.dumps(packet, indent=2))
    else:
        print(_render_brief(packet))
    return 0


def record_verification(findings_path: Path, result_path: Path, *, out: Path) -> int:  # noqa: C901
    """Merge one verifier result into the findings document, writing to `out`.

    The transition is forced by the verifier's evidence, not by this tool's
    judgement: `disproved` rejects, `reproduced` confirms only when the record
    already carries the confirmed shape (a verifier proves reproduction; it does
    not invent severity or remediation), `inconclusive` stays needs_validation
    with the blocker recorded.
    """
    try:
        data = json.loads(findings_path.read_text())
        result = json.loads(result_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read input: {exc}")
        return 1

    findings, envelope = _unwrap(data, findings_path)
    if findings is None:
        return 1
    if not isinstance(result, dict):
        print("FAIL: a verifier result must be a JSON object")
        return 1

    errors: list[str] = []
    fingerprint = result.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        errors.append("result.fingerprint: required")
    verdict = result.get("verdict")
    if verdict not in VERIFIER_VERDICTS:
        errors.append(f"result.verdict: must be one of {VERIFIER_VERDICTS}, got {verdict!r}")
    if not has_visible_prose(result.get("method")):
        errors.append("result.method: must be visible prose")
    if not has_visible_prose(result.get("observed")):
        errors.append("result.observed: must be visible prose")
    command = result.get("command")
    if command is not None and not isinstance(command, str):
        errors.append("result.command: must be a string when present")
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1

    matches = [
        position
        for position, item in enumerate(findings)
        if isinstance(item, dict) and item.get("fingerprint") == fingerprint
    ]
    if len(matches) != 1:
        print(
            f"FAIL: expected exactly one finding with fingerprint {fingerprint!r}, got {len(matches)}"
        )
        return 1

    position = matches[0]
    finding = dict(findings[position])
    verification = {
        "by": "fresh-context verifier",
        "at": date.today().isoformat(),
        "verdict": verdict,
        "method": result["method"],
        "command": command or "none",
        "observed": result["observed"],
    }
    if has_visible_prose(result.get("notes")):
        verification["notes"] = result["notes"]
    finding["verification"] = verification

    claimed = finding.get("claimed_root_cause") or finding.get("root_cause") or finding.get("title")
    messages: list[str] = []
    if verdict == "disproved":
        finding["verdict"] = "rejected"
        finding["claimed_root_cause"] = claimed
        reason = result["observed"]
        if has_visible_prose(result.get("notes")):
            reason = f"{reason} — {result['notes']}"
        finding["reason"] = reason
        for field in FORBIDDEN_FIELDS["rejected"]:
            finding.pop(field, None)
        messages.append("verdict rejected — the verifier disproved the claim")
    elif verdict == "reproduced":
        missing = [field for field in REQUIRED_FIELDS["confirmed"] if field not in finding]
        if missing:
            messages.append(
                "reproduced, but NOT promoted to confirmed — the record still lacks: "
                + ", ".join(missing)
                + " (a verifier proves reproduction; it does not invent severity or remediation)"
            )
        else:
            finding["verdict"] = "confirmed"
            for field in FORBIDDEN_FIELDS["confirmed"]:
                finding.pop(field, None)
            messages.append("verdict confirmed — the verifier reproduced the claim")
    else:
        finding["verdict"] = "needs_validation"
        finding["claimed_root_cause"] = claimed
        blockers = list(finding.get("blockers") or [])
        blocker = result["observed"]
        if has_visible_prose(result.get("notes")):
            blocker = f"{blocker} — {result['notes']}"
        if blocker not in blockers:
            blockers.append(blocker)
        finding["blockers"] = blockers
        plan = finding.get("validation_plan")
        if not isinstance(plan, dict) or not (
            has_visible_prose(plan.get("local")) or has_visible_prose(plan.get("deployment"))
        ):
            finding["validation_plan"] = {
                "local": f"re-run the verifier's probe: {command or 'none'}"
            }
        for field in FORBIDDEN_FIELDS["needs_validation"]:
            finding.pop(field, None)
        messages.append("verdict needs_validation — the verifier was blocked")

    findings[position] = finding
    findings.sort(key=lambda item: item.get("fingerprint") or "")
    document = findings if envelope is None else {**envelope, "findings": findings}

    staged = out.with_name(out.name + ".staged")
    try:
        staged.write_text(json.dumps(document, indent=2) + "\n")
        print(f"merged {fingerprint}: {messages[0]}")
        print(f"writing {out}")
        if check_findings(staged) != 0:
            print(f"FAIL: the merged document does not validate; {out} was not written")
            return 1
        staged.replace(out)
    finally:
        staged.unlink(missing_ok=True)
    return 0


def triage(  # noqa: C901 — keypress parsing + kit wiring, split would scatter state
    findings_path: Path,
    *,
    out: Path | None = None,
    all_: bool = False,
    journal_dir: Path | None = None,
    menu: bool = False,
    prompt: bool = False,
    answers_path: Path | None = None,
    packets_out: Path | None = None,
    preannotate: bool = False,
    input_fn=input,
    print_fn=print,
) -> dict | int:
    """Interactive verdict sitting over a findings document (review-kit S3).
    Returns the ReviewSession outcome (dict) or 1 on a fatal read error;
    callers on the CLI convert the dict to an exit code.

    One keypress per finding — the sitting only PRODUCES transitions; the
    confirm gate applies them to a copy of the document, validated with
    the staged write used by ``record_verification`` and written to
    ``--out`` (the input is never overwritten implicitly).

      k        keep — no transition (needs_validation stays)
      r|reason reject — the finding becomes ``rejected`` with the reason
               (blockers / validation_plan are dropped; a reason is
               required)
      a        confirm — promotes to ``confirmed`` but only when the
               record already carries the full confirmed shape (severity,
               execution, remediation, confidence, conditions, …); a
               verifier result is the normal path for that
      p        park — never re-asked (journal read-back)
      ?        re-render evidence
      q / x    quit-and-review / abort the walk

    The default walk covers ``needs_validation`` findings; ``--all``
    includes every verdict. ``menu=True`` renders a numbered menu;
    ``prompt=True`` reads one answer per finding from ``answers_path``
    (walk order; a final ``y``/``n`` line answers the batch confirm);
    ``packets_out`` writes the prompt packets (JSONL) for the agent flow.
    ``menu``/``prompt`` are mutually exclusive.
    """
    from helpers.core.review_kit import (
        Journal,
        ReviewSession,
        assemble_entries,
        latest_action_by,
    )
    from helpers.core.review_menu import (
        Choice,
        MenuSpec,
        make_menu_input,
        make_scripted_input,
        write_packets,
    )

    _MENU_SPEC = MenuSpec(
        name="findings",
        prompt="Review the finding and decide:",
        choices=(
            Choice("k", "keep — no transition"),
            Choice("r", "reject — must carry |reason"),
            Choice("a", "confirm — promoted only when the record is already complete"),
            Choice("p", "park — never re-asked (reopen via --skipped)"),
            Choice("?", "legend + evidence"),
            Choice("q/x", "quit-and-review / abort walk"),
        ),
    )
    if menu and prompt:
        raise ValueError("--menu and --prompt are mutually exclusive")
    if prompt and answers_path is None:
        raise ValueError("--prompt requires --answers <file>")
    reader = input_fn
    if menu:
        _menu_rec: dict[str, str | None] = {"key": None}
        reader = make_menu_input(
            _MENU_SPEC,
            print_fn=print_fn,
            input_fn=input_fn,
            recommended=lambda: _menu_rec["key"],
        )
    elif prompt:
        if answers_path is None:  # unreachable — the ValueError above guards prompt w/o answers
            raise ValueError("--prompt requires --answers <file>")
        answers = answers_path.read_text(encoding="utf-8").splitlines()
        reader = make_scripted_input(answers, _MENU_SPEC)
    input_fn = reader  # ask() closures and the confirm gate read through the backend

    journal_path = (journal_dir or REPO / "outputs" / "findings_review") / "journal.jsonl"
    try:
        data = json.loads(findings_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print_fn(f"FAIL: cannot read findings: {exc}")
        return 1
    findings, envelope = _unwrap(data, findings_path)
    if findings is None:
        return 1

    from helpers.core.decisions_preannotate import load_id_annotations, render_carrier_for

    if preannotate:
        try:
            from helpers.core.decisions_preannotate import annotate_surface, make_surface_specs

            _, annotated, _ = annotate_surface(make_surface_specs()["findings"])
            if annotated:
                print_fn(f"(auto-pre-annotated {annotated} new finding rows)")
        except Exception as exc:  # non-fatal — the sitting is the human gate
            print_fn(f"(auto-pre-annotation unavailable: {exc})")

    c_map = load_id_annotations(
        (journal_dir or REPO / "outputs" / "findings_review") / "mercury_annotations.jsonl"
    )

    def _recommend(f: dict) -> str | None:
        """Carrier-recommended choice key: ``a`` (confirm) on admit, ``r``
        (reject) on keep-out, ``None`` without a rubric verdict."""
        a = c_map.get(f.get("fingerprint", ""))
        if not a or a.get("p_rubric") is None:
            return None
        try:
            p = float(a["p_rubric"])
        except TypeError, ValueError:
            return None
        return "a" if p > 0.5 else "r"

    def _missing_confirmed_fields(f: dict) -> list[str]:
        return [field for field in REQUIRED_FIELDS["confirmed"] if field not in f]

    parked_ids = latest_action_by(journal_path, key_field="id")

    def _entry(f: dict) -> dict:
        e = dict(f)
        e["label"] = f.get("fingerprint", "")
        return e

    open_rows = [
        f
        for f in findings
        if isinstance(f, dict)
        and (all_ or f.get("verdict") == "needs_validation")
        and f.get("fingerprint") not in parked_ids
    ]
    wl = {
        "suggested": [_entry(f) for f in open_rows],
        "promoted": [],
        "skipped": [
            _entry(f)
            for f in findings
            if isinstance(f, dict) and f.get("fingerprint") in parked_ids
        ],
        "no_signal": [],
    }
    entries = assemble_entries(
        wl,
        labels_filter=None,
        redecide=False,
        skipped=False,
        sort_key=lambda e: (e["label"],),
        limit=None,
    )
    if packets_out is not None and entries:
        write_packets(entries, _MENU_SPEC, packets_out, recommend=_recommend)

    legend = [
        "  keys: k  keep    r  reject (must carry |reason)    a  confirm (complete records only)",
        "        p  park — reopen via --skipped    ?  this legend + evidence    q/x  quit/abort",
    ]
    if entries:
        print_fn("\n".join(legend))

    def _evidence(f: dict) -> None:
        print_fn(f"     verdict: {f.get('verdict')}")
        if f.get("surface"):
            print_fn(f"     surface: {f['surface']} attack_class: {f.get('attack_class')}")
        if f.get("title"):
            print_fn(f"     title: {f['title']}")
        if f.get("claimed_root_cause"):
            print_fn(f"     claimed root cause: {f['claimed_root_cause']}")
        if f.get("reason"):
            print_fn(f"     reason: {f['reason']}")
        if f.get("blockers"):
            print_fn(f"     blockers: {f['blockers']}")
        if f.get("verification"):
            print_fn(f"     verification: {f['verification']}")
        render_carrier_for(f.get("fingerprint", ""), c_map=c_map, print_fn=print_fn)

    def render(idx: int, total: int, e: dict) -> None:
        print_fn(f"[{idx}/{total}] `{e['label']}`")
        _evidence(e)

    def ask(e: dict, note) -> dict:
        rid = e["label"]
        if menu:
            _menu_rec["key"] = _recommend(e)
        while True:
            ans = input_fn("  k / r|reason / a / p / ? / q / x: ").strip()
            ans, _, free_note = ans.partition("|")
            ans, free_note = ans.strip(), free_note.strip()
            if ans in {"p", "q", "x"}:
                return {"id": rid, "action": {"p": "skip", "q": "quit", "x": "abort"}[ans]}
            if ans == "?":
                print_fn("\n".join(legend))
                _evidence(e)
                continue
            if ans == "k":
                return {"id": rid, "action": "skip"}
            if ans == "r":
                if not has_visible_prose(free_note):
                    print_fn("  reject needs a visible reason — r|why")
                    continue
                return {
                    "id": rid,
                    "action": "approve",
                    "file_decision": "rejected",
                    "note": free_note,
                }
            if ans == "a":
                missing = _missing_confirmed_fields(e)
                if missing:
                    print_fn(
                        "  confirm requires the full confirmed shape — missing: "
                        + ", ".join(missing)
                        + " (a verifier result is the normal promotion path)"
                    )
                    continue
                return {
                    "id": rid,
                    "action": "approve",
                    "file_decision": "confirmed",
                    "note": "",
                }
            print_fn("  ? (k / r|reason / a / p / ? / q / x)")
            continue

    def spec_of(d: dict, e: dict) -> str:
        return f"{d['id']} -> {d['file_decision']}"

    def apply_batch(specs: list[str], decisions: list[dict]) -> tuple[list[str], list[str]]:
        transitions = [d for d in decisions if d.get("action") == "approve"]
        if not transitions:
            return (["no transitions"], [])
        if out is None:
            return (
                [],
                [
                    "triage: transitions made but no --out given — the input is never "
                    "overwritten implicitly; re-run with --out PATH to write the merged document"
                ],
            )
        mutable = [dict(f) for f in findings]
        by_fp = {f.get("fingerprint"): i for i, f in enumerate(mutable)}
        applied = 0
        for d in transitions:
            pos = by_fp.get(d["id"])
            if pos is None:
                continue
            f = mutable[pos]
            if d["file_decision"] == "rejected":
                f["verdict"] = "rejected"
                f["claimed_root_cause"] = (
                    f.get("claimed_root_cause") or f.get("root_cause") or f.get("title")
                )
                f["reason"] = d["note"]
                for field in FORBIDDEN_FIELDS["rejected"]:
                    f.pop(field, None)
            else:
                f["verdict"] = "confirmed"
                for field in FORBIDDEN_FIELDS["confirmed"]:
                    f.pop(field, None)
            applied += 1
        mutable.sort(key=lambda item: item.get("fingerprint") or "")
        document = mutable if envelope is None else {**envelope, "findings": mutable}
        staged = out.with_name(out.name + ".staged")
        try:
            staged.write_text(json.dumps(document, indent=2) + "\n")
            if check_findings(staged) != 0:
                print_fn(f"FAIL: the merged document does not validate; {out} was not written")
                return ([], ["triage: validation failed — nothing written"])
            staged.replace(out)
        finally:
            staged.unlink(missing_ok=True)
        return (
            [f"applied {applied} transition(s) -> {out}"],
            ["next: security_findings.py check — the merged document should be clean"],
        )

    return ReviewSession(
        entries=entries,
        journal=Journal(journal_path),
        render=render,
        ask=ask,
        spec_of=spec_of,
        apply_batch=apply_batch,
        batch_header="batch to apply to the findings document:",
        apply_noun="transition(s)",
        input_fn=reader,
        print_fn=print_fn,
        apply_flag=True,
    ).run()


def _main_brief(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="security_findings.py brief",
        description="Emit a blind verification brief for one finding (A6), or merge a verifier result.",
    )
    ap.add_argument("findings", nargs="?", type=Path, default=DEFAULT_FINDINGS)
    selector = ap.add_mutually_exclusive_group(required=True)
    selector.add_argument("--index", type=int, help="0-based position in the findings array")
    selector.add_argument("--fingerprint", help="the finding's fingerprint")
    ap.add_argument("--json", dest="as_json", action="store_true", help="emit the packet as JSON")
    ap.add_argument(
        "--record", type=Path, help="merge a verifier result into the findings document"
    )
    ap.add_argument(
        "--out", type=Path, help="where to write the merged document (required with --record)"
    )
    parsed = ap.parse_args(argv)
    if parsed.record is not None:
        if parsed.out is None:
            ap.error("--record requires --out PATH (the input is never overwritten implicitly)")
        return record_verification(parsed.findings, parsed.record, out=parsed.out)
    return brief(
        parsed.findings,
        index=parsed.index,
        fingerprint=parsed.fingerprint,
        as_json=parsed.as_json,
    )


def _main_triage(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="security_findings.py triage",
        description="Interactive verdict sitting over a findings document (review-kit S3).",
    )
    ap.add_argument("findings", nargs="?", type=Path, default=DEFAULT_FINDINGS)
    ap.add_argument(
        "--out", type=Path, help="where to write the merged document (required to apply)"
    )
    ap.add_argument(
        "--all",
        dest="all_",
        action="store_true",
        help="walk every verdict, not just needs_validation",
    )
    ap.add_argument("--menu", action="store_true", help="render each finding as a numbered menu")
    ap.add_argument(
        "--prompt", action="store_true", help="read answers from --answers (walk order)"
    )
    ap.add_argument("--answers", type=Path, help="answers file (one verb per finding + final y/n)")
    ap.add_argument(
        "--packets-out", type=Path, help="write the prompt packets (JSONL) for the agent flow"
    )
    ap.add_argument(
        "--no-preannotate",
        action="store_true",
        help="do NOT auto-run decisions-preannotate for the findings surface "
        "(default: run it first, idempotent per key)",
    )
    parsed = ap.parse_args(argv)
    outcome = triage(
        parsed.findings,
        out=parsed.out,
        all_=parsed.all_,
        menu=parsed.menu,
        prompt=parsed.prompt,
        answers_path=parsed.answers,
        packets_out=parsed.packets_out,
        preannotate=not parsed.no_preannotate,
    )
    return 0 if isinstance(outcome, dict) else outcome


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "brief":
        return _main_brief(args[1:])
    if args and args[0] == "triage":
        return _main_triage(args[1:])
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("findings", nargs="?", type=Path, default=DEFAULT_FINDINGS)
    ap.add_argument("--strict", action="store_true", help="reserved; warnings become errors")
    a = ap.parse_args(argv)
    return check_findings(a.findings, strict=a.strict)


if __name__ == "__main__":
    raise SystemExit(main())
