#!/usr/bin/env python3
"""gate_query — DuckDB-indexed search over the repo's gate run reports.

The append-only ``outputs/*_report.md`` corpus (main + worktree copies
under ``outputs/wt/<name>/outputs/``) is already machine-shaped:
``# make <gate>`` headers, ``**Generated/Started/Elapsed**`` meta lines,
``| leg | seconds | status |`` tables. This tool indexes that corpus
incrementally (byte-offset: only new bytes are parsed; the trailing
incomplete run stays pending until its summary row lands) and answers
the two prompts actually ask:

    latest      digest of the newest run (when, wall, exit, legs)
                (--gate defaults to qa; --gate all = newest run PER gate)
    recent      last N runs of a gate, PASS/FAIL each (--tests for ids)
    failures    failed legs + failed test node_ids + error heads
    tests       full per-test rows for a run (junitxml-backed), or history
                 by --node/--marker/--slowest
    compare     compare two indexed runs
    clusters    group recurring normalized test failures
    artifacts   read-only generic gate artifact records
    timing      per-leg timing history vs budget
    grep        keyword over failure text
    rotate      archive run prefixes to outputs/archives/*.zst (dry-run default;
                keep policy: last 30 runs per report, rotate past 8 MB —
                override with --keep-runs / --max-mb)
    refresh     force (re)index (--full rebuilds; default incremental)

Refresh runs automatically before every verb. The reports stay the
source of truth; the index (``outputs/gate_runs.duckdb``) is derived
and rebuildable. Parsing truth lives in search_tui (regexes + block
builders imported — one parser, two surfaces).

Examples:
    python3 helpers/misc/gate_query.py latest
    python3 helpers/misc/gate_query.py failures --full
    python3 helpers/misc/gate_query.py latest -wt graph_algos   # -wt = --wt
    python3 helpers/misc/gate_query.py timing --leg graph_l1_centrality --last 20
    python3 helpers/misc/gate_query.py rotate --apply --keep-runs 30

Root override for tests: env ``GATE_QUERY_ROOT`` (default: this repo's
``outputs/``); the DB lands at ``<root>/gate_runs.duckdb`` unless
``GATE_QUERY_DB`` says otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
from compression.zstd import compress, decompress

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from helpers.misc import search_tui as st  # noqa: E402  (stdlib-only at import)

REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ROOT = REPO_ROOT / "outputs"
ROOT = Path(os.environ.get("GATE_QUERY_ROOT", _DEFAULT_ROOT))
DB_PATH = Path(os.environ.get("GATE_QUERY_DB", ROOT / "gate_runs.duckdb"))
ERR_CAP = 16_000  # per-test err_blob cap
TEST_ARTIFACT_SCHEMA = "test-facts.v1"
ARTIFACT_SCHEMA = "gate-artifact.v1"

_STARTED_RE = re.compile(r"\*\*Started:\*\*\s+([\d: -]+)")
_COMMIT_RE = re.compile(r"\*\*Commit:\*\*\s*([0-9a-f]+)")
_WT_RE = re.compile(r"\*\*Worktree:\*\*\s*(\S+)")
_EXIT_RE = re.compile(r"\*\*Exit:\*\*\s*(\d+)")
_ARTIFACTS_RE = re.compile(r"\*\*Artifacts:\*\*\s+(\S+)")
_SUMMARY_ROW_RE = re.compile(r"^\|\s*\*\*", re.MULTILINE)
_FAILED_LINE_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)")
_TS_FMT = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS run_seq;
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY DEFAULT nextval('run_seq'),
    src_rel TEXT NOT NULL,
    wt TEXT NOT NULL DEFAULT '',
    gate TEXT NOT NULL,
    started_at TIMESTAMP,
    generated_at TIMESTAMP,
    elapsed_s DOUBLE,
    jobs INTEGER,
    python_ver TEXT,
    commit_sha TEXT,
    patch_name TEXT,
    exit_code INTEGER,
    summary TEXT,
    header_offset UBIGINT NOT NULL,
    nbytes UBIGINT,
     junit_path TEXT,
     arch_path TEXT,
     artifact_schema TEXT,
     artifact_dir TEXT,
     artifact_state TEXT,
     complete BOOLEAN NOT NULL DEFAULT TRUE
 );

CREATE TABLE IF NOT EXISTS legs (
    run_id INTEGER NOT NULL,
    leg TEXT NOT NULL,
    seconds DOUBLE,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bench (
    run_id INTEGER NOT NULL,
    bench TEXT NOT NULL,
    seconds DOUBLE NOT NULL,
    budget_s DOUBLE,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tests (
    run_id INTEGER NOT NULL,
    leg TEXT NOT NULL,
    node_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    seconds DOUBLE,
    err_head TEXT,
    err_blob TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
    run_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_s DOUBLE,
    message TEXT,
    fingerprint TEXT,
    metadata_json TEXT,
    schema TEXT NOT NULL,
    source_rel TEXT,
    source_offset UBIGINT
);
CREATE TABLE IF NOT EXISTS test_facts (
    run_id INTEGER NOT NULL,
    leg TEXT NOT NULL,
    node_id TEXT NOT NULL,
    file TEXT,
    outcome TEXT NOT NULL,
    seconds DOUBLE,
    phase TEXT,
    phase_seconds_json TEXT,
    markers_json TEXT,
    file_line INTEGER,
    err_head TEXT,
    err_blob TEXT,
    error_fingerprint TEXT,
    worker TEXT,
    artifact_schema TEXT NOT NULL
);
ALTER TABLE test_facts ADD COLUMN IF NOT EXISTS phase_seconds_json TEXT;
CREATE INDEX IF NOT EXISTS test_facts_node_idx ON test_facts(node_id, run_id);
CREATE INDEX IF NOT EXISTS test_facts_error_idx ON test_facts(error_fingerprint, run_id);
CREATE INDEX IF NOT EXISTS artifacts_kind_target_idx ON artifacts(kind, target, run_id);
CREATE INDEX IF NOT EXISTS artifacts_status_idx ON artifacts(status, run_id);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS artifact_schema TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS artifact_dir TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS artifact_state TEXT;
ALTER TABLE legs ADD COLUMN IF NOT EXISTS err_head TEXT;
CREATE TABLE IF NOT EXISTS parse_state (
    src_rel TEXT PRIMARY KEY,
    parse_offset UBIGINT NOT NULL,
    size UBIGINT NOT NULL,
    mtime_ns BIGINT NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
"""


# ---------------------------------------------------------------- helpers


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB_PATH))  # sqlite rule is helpers.core.db; DuckDB goes direct
    con.execute(SCHEMA)
    return con


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), _TS_FMT)
    except ValueError:
        return None


def report_copies(root: Path | None = None) -> list[tuple[str, Path, str, str]]:
    """(gate, path, wt, kind) for every report copy under root.

    Mirrors search_tui.report_files() but parameterised by root so tests
    can point it at a tmp corpus.
    """
    root = root or ROOT
    out: list[tuple[str, Path, str, str]] = []
    for name, rel, kind in st._REPORTS:  # noqa: SLF001
        p = root / Path(rel).name  # rel is repo-rooted ("outputs/x.md"); root IS outputs/
        if p.exists():
            out.append((name, p, "", kind))
    wt_root = root / "wt"
    if wt_root.is_dir():
        for wt in sorted(p.name for p in wt_root.iterdir() if p.is_dir()):
            for name, rel, kind in st._REPORTS:
                p = wt_root / wt / "outputs" / Path(rel).name
                if p.exists():
                    out.append((name, p, wt, kind))
    return out


def _split_blocks(text: str, base_offset: int) -> list[tuple[int, int, str, list[str]]]:
    """(start_off, end_off, kind, lines) per run block, byte-tracked."""
    lines = text.splitlines(keepends=True)
    offs, off = [], 0
    for ln in lines:
        offs.append(off)
        off += len(ln.encode())
    starts: list[tuple[int, int, str]] = []
    for i, ln in enumerate(lines):
        stripped = ln.rstrip("\n")
        kind = ""
        if st._GATE_HEADER_RE.match(stripped):  # noqa: SLF001
            kind = "gate"
        elif st._PERF_HEADER_RE.match(stripped):  # noqa: SLF001
            kind = "perf"
        if kind:
            starts.append((i, base_offset + offs[i], kind))
    blocks = []
    for j, (i, off, kind) in enumerate(starts):
        end_i = starts[j + 1][0] if j + 1 < len(starts) else len(lines)
        end_off = starts[j + 1][1] if j + 1 < len(starts) else base_offset + len(text.encode())
        blocks.append((off, end_off, kind, [ln.rstrip("\n") for ln in lines[i:end_i]]))
    return blocks


def _extra_meta(lines: list[str]) -> dict:
    out: dict = {}
    for ln in lines[:8]:
        for key, rx in (
            ("started", _STARTED_RE),
            ("commit", _COMMIT_RE),
            ("wt", _WT_RE),
            ("artifacts", _ARTIFACTS_RE),
            ("exit", _EXIT_RE),
        ):
            if key not in out and (m := rx.search(ln)):
                out[key] = m.group(1).strip()
    return out


def _parse_block(gate: str, kind: str, lines: list[str]) -> st.RunBlock | None:
    try:
        if kind == "perf":
            rb = st._build_perf_block(gate, lines)  # noqa: SLF001
        else:
            rb = st._build_gate_block(gate, lines)  # noqa: SLF001
    except Exception:
        return None
    return rb


def _leg_err_head(lines: list[str], label: str) -> str | None:
    """First non-blank lines of a leg's ``## <label> (FAILED)`` section."""
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"## {label} (FAILED"):
            start = i + 1
            break
    if start is None:
        return None
    body: list[str] = []
    for ln in lines[start:]:
        if ln.startswith("## "):
            break
        if ln.strip():
            body.append(ln)
        if len(body) >= 6:
            break
    text = "\n".join(body).strip()
    return text[:2000] or None


def _error_fingerprint(blob: str) -> str | None:
    if not blob:
        return None
    text = re.sub(r"/tmp/pytest-of-[^\s'\"]+", "<tmp>", blob)  # noqa: S108
    text = re.sub(r"/tmp/[^\s'\"]+", "<tmp>", text)  # noqa: S108
    text = re.sub(r"0x[0-9a-fA-F]+", "<addr>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?s\b", "<duration>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b", "<time>", text)
    text = re.sub(r"line \d+", "line <line>", text)
    return hashlib.sha256(text[:ERR_CAP].encode()).hexdigest()[:16]


def _test_metadata_by_node(
    junit: Path, run_start: datetime | None, run_gen: datetime | None
) -> dict[str, dict]:
    lo = run_start or run_gen or datetime.fromtimestamp(junit.stat().st_mtime)
    hi = run_gen or datetime.fromtimestamp(junit.stat().st_mtime)
    base_name = junit.name.removesuffix(".junit.xml")
    base = junit.with_name(f"{base_name}.metadata.json")
    candidates = [base, *sorted(junit.parent.glob(f"{base_name}.metadata.*.json"))]
    out: dict[str, dict] = {}
    for path in candidates:
        if not path.exists():
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        if not (lo - timedelta(seconds=120) <= mtime <= hi + timedelta(seconds=300)):
            continue
        try:
            payload = json.loads(path.read_text())
        except OSError, json.JSONDecodeError:
            continue
        if payload.get("schema") != "test-metadata.v1":
            continue
        for item in payload.get("tests", []):
            if isinstance(item, dict) and item.get("node_id"):
                out[item["node_id"]] = item
    return out


def _junit_facts(
    gate: str,
    wt: str,
    run_start: datetime | None,
    run_gen: datetime | None,
    junit: Path | None = None,
) -> tuple[str | None, list[dict]]:
    retained = junit is not None
    junit = junit or ROOT / ".junit" / f"{gate}.junit.xml"
    if not junit.exists() or (wt and not retained):
        return None, []
    mtime = datetime.fromtimestamp(junit.stat().st_mtime)
    lo = run_start or (run_gen or mtime)
    hi = run_gen or mtime
    if not (lo - timedelta(seconds=120) <= mtime <= hi + timedelta(seconds=300)):
        return None, []
    try:
        root = ET.parse(junit).getroot()  # noqa: S314
    except ET.ParseError:
        return None, []
    metadata = _test_metadata_by_node(junit, run_start, run_gen)
    facts: list[dict] = []
    for case in root.iter("testcase"):
        cls = case.get("classname", "")
        name = case.get("name", "")
        node = cls.replace(".", "/") + ".py::" + name if cls else name
        meta = metadata.get(node, {})
        try:
            secs = float(case.get("time") or 0)
        except ValueError:
            secs = None
        kid = next((c for c in case if c.tag in ("failure", "error", "skipped")), None)
        outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}.get(
            kid.tag if kid is not None else "", "passed"
        )
        blob = "" if kid is None else ((kid.get("message") or "") + "\n" + (kid.text or "")).strip()
        props = {
            p.get("name", ""): p.get("value", "") for p in case.findall("./properties/property")
        }
        markers = sorted(
            {name.removeprefix("pytest.mark.") for name in props if name.startswith("pytest.mark.")}
            | set(meta.get("markers") or [])
        )
        line = props.get("line") or props.get("lineno")
        try:
            file_line = int(line) if line else None
        except ValueError:
            file_line = None
        if file_line is None:
            file_line = meta.get("file_line")
        facts.append(
            {
                "node_id": node,
                "file": node.split("::", 1)[0],
                "outcome": outcome,
                "seconds": secs,
                "phase": "total",
                "phase_seconds": meta.get("phases") or {},
                "markers": markers,
                "file_line": file_line,
                "err_head": blob[:400] or None,
                "err_blob": blob[:ERR_CAP] or None,
                "error_fingerprint": _error_fingerprint(blob),
                "worker": props.get("worker") or props.get("xdist_worker") or meta.get("worker"),
                "artifact_schema": TEST_ARTIFACT_SCHEMA,
            }
        )
    return str(junit), facts


def _junit_rows(
    gate: str, wt: str, run_start: datetime | None, run_gen: datetime | None
) -> tuple[str | None, list[tuple]]:
    junit, facts = _junit_facts(gate, wt, run_start, run_gen)
    rows = [
        (
            fact["node_id"],
            fact["outcome"],
            fact["seconds"],
            fact["err_head"] or "",
            fact["err_blob"] or "",
        )
        for fact in facts
    ]
    return junit, rows


def _artifact_kind(label: str, report_kind: str) -> str | None:
    if label == "pytest":
        return None
    if report_kind == "perf":
        return "perf"
    return {
        "lint": "ruff",
        "md-lint": "search",
        "types": "ty",
        "deptry": "security",
        "static_checks": "integrity",
        "verify_notes": "integrity",
        "integrity_check": "integrity",
        "snapshot-fresh": "integrity",
        "snapshot_check": "integrity",
        "tmp-sweep": "search",
    }.get(label)


def _artifact_base(wt: str, artifact_dir: str | None) -> Path | None:
    if not artifact_dir:
        return None
    worktree_root = ROOT / f"wt/{wt}/outputs" if wt else ROOT
    base = (worktree_root / artifact_dir).resolve()
    root = ROOT.resolve()
    if root != base and root not in base.parents:
        return None
    return base


def _retained_artifact_junit(
    gate: str, wt: str, artifact_dir: str | None
) -> tuple[Path | None, str]:
    base = _artifact_base(wt, artifact_dir)
    if base is None:
        return None, "not_collected" if not artifact_dir else "invalid"
    junit = base / f"{gate}.junit.xml"
    return (junit, "retained") if junit.exists() else (None, "missing")


def _artifact_record(
    kind: str,
    target: str,
    status: str,
    path: Path,
    schema: str,
    *,
    duration_s: float | None = None,
    message: str | None = None,
    fingerprint: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "kind": kind,
        "target": target[:500],
        "status": status,
        "duration_s": duration_s,
        "message": message[:400] if message else None,
        "fingerprint": fingerprint,
        "metadata_json": json.dumps(metadata or {}, separators=(",", ":")),
        "schema": schema,
        "source_rel": str(path),
        "source_offset": None,
    }


def _diagnostic_artifact_records(path: Path, kind: str, raw: object, schema: str) -> list[dict]:
    if not isinstance(raw, list):
        return []
    if not raw:
        return [
            {
                "kind": kind,
                "target": kind,
                "status": "pass",
                "duration_s": None,
                "message": None,
                "fingerprint": None,
                "metadata_json": json.dumps({"adapter": f"{kind}-json", "diagnostics": 0}),
                "schema": schema,
                "source_rel": str(path),
                "source_offset": None,
            }
        ]
    records = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(
            item.get("code") or item.get("rule") or item.get("ruleId") or item.get("id") or ""
        )
        target = str(
            item.get("filename")
            or item.get("path")
            or item.get("file")
            or item.get("uri")
            or item.get("target")
            or kind
        )[:500]
        message = str(item.get("message") or item.get("description") or "")[:400]
        severity = str(item.get("severity") or item.get("level") or "error").lower()
        status = (
            "fail"
            if severity in {"error", "fatal", "failure"}
            else ("warn" if severity in {"warning", "warn"} else "info")
        )
        records.append(
            {
                "kind": kind,
                "target": target,
                "status": status,
                "duration_s": None,
                "message": message or None,
                "fingerprint": _error_fingerprint(f"{code}\n{message}\n{target}"),
                "metadata_json": json.dumps(
                    {"adapter": f"{kind}-json", "code": code or None, "severity": severity},
                    separators=(",", ":"),
                ),
                "schema": schema,
                "source_rel": str(path),
                "source_offset": None,
            }
        )
    return records


def _native_artifact_records(base: Path) -> list[dict]:  # noqa: C901
    records = []
    for kind, filename in (("ruff", "ruff.json"), ("ty", "ty.json")):
        path = base / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except OSError, json.JSONDecodeError:
            continue
        raw = payload.get("diagnostics", []) if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or item.get("rule") or item.get("id") or "")
            target = str(
                item.get("filename")
                or item.get("path")
                or item.get("file")
                or item.get("target")
                or kind
            )[:500]
            message = str(item.get("message") or item.get("description") or "")[:400]
            severity = str(item.get("severity") or item.get("level") or "error").lower()
            status = (
                "fail"
                if severity in {"error", "fatal", "failure"}
                else ("warn" if severity in {"warning", "warn"} else "info")
            )
            records.append(
                {
                    "kind": kind,
                    "target": target,
                    "status": status,
                    "duration_s": None,
                    "message": message or None,
                    "fingerprint": _error_fingerprint(f"{code}\n{message}\n{target}"),
                    "metadata_json": json.dumps(
                        {
                            "adapter": f"{kind}-json",
                            "code": code or None,
                            "severity": severity,
                        },
                        separators=(",", ":"),
                    ),
                    "schema": f"{kind}-json.v1",
                    "source_rel": str(path),
                    "source_offset": None,
                }
            )
        if not raw:
            records.append(
                {
                    "kind": kind,
                    "target": kind,
                    "status": "pass",
                    "duration_s": None,
                    "message": None,
                    "fingerprint": None,
                    "metadata_json": json.dumps({"adapter": f"{kind}-json", "diagnostics": 0}),
                    "schema": f"{kind}-json.v1",
                    "source_rel": str(path),
                    "source_offset": None,
                }
            )
    for filename in ("coverage.json", "coverage.xml"):
        path = base / filename
        if not path.exists():
            continue
        fail_under = None
        try:
            if filename.endswith(".xml"):
                root = ET.parse(path).getroot()  # noqa: S314
                line_rate = float(root.get("line-rate", "0"))
                branch_rate = float(root.get("branch-rate", "0"))
                percent = line_rate * 100
                message = f"line {percent:.2f}% · branch {branch_rate * 100:.2f}%"
                status = "pass"
            else:
                payload = json.loads(path.read_text())
                totals = payload.get("totals", payload) if isinstance(payload, dict) else {}
                percent = float(totals.get("percent", totals.get("line_percent", 0)))
                fail_under = totals.get("fail_under")
                message = f"line coverage {percent:.2f}%"
                status = "pass"
        except ET.ParseError, OSError, TypeError, ValueError, json.JSONDecodeError:
            continue
        if fail_under is not None:
            try:
                if percent < float(fail_under):
                    status = "fail"
            except TypeError, ValueError:
                pass
        records.append(
            _artifact_record(
                "coverage",
                "total",
                status,
                path,
                "coverage-json.v1" if filename.endswith(".json") else "coverage-xml.v1",
                message=message,
                metadata={"adapter": "coverage", "percent": percent},
            )
        )
    for filename in ("perf.json", "perf.jsonl"):
        path = base / filename
        if not path.exists():
            continue
        try:
            if filename.endswith(".jsonl"):
                payload = [
                    json.loads(line) for line in path.read_text().splitlines() if line.strip()
                ]
            else:
                payload = json.loads(path.read_text())
        except OSError, json.JSONDecodeError:
            continue
        raw = (
            payload.get("benchmarks", payload.get("results", []))
            if isinstance(payload, dict)
            else payload
        )
        if not isinstance(raw, list):
            raw = [payload] if isinstance(payload, dict) else []
        for item in raw:
            if not isinstance(item, dict):
                continue
            target = str(item.get("name") or item.get("bench") or item.get("target") or "benchmark")
            try:
                duration = float(item.get("seconds", item.get("duration_s", item.get("time", 0))))
            except TypeError, ValueError:
                duration = None
            budget = item.get("budget_s", item.get("budget"))
            status = str(item.get("status", "pass")).lower()
            if status not in {"pass", "fail", "warn", "info"}:
                status = "pass"
            try:
                threshold = float(budget)
            except TypeError, ValueError:
                threshold = None
            if (
                status == "pass"
                and threshold is not None
                and duration is not None
                and duration > threshold
            ):
                status = "fail"
            records.append(
                _artifact_record(
                    "perf",
                    target,
                    status,
                    path,
                    "perf-json.v1",
                    duration_s=duration,
                    message=str(item.get("message") or "")[:400] or None,
                    fingerprint=_error_fingerprint(f"{target}\n{item.get('message', '')}"),
                    metadata={"adapter": "perf", "budget_s": budget},
                )
            )
        if not raw:
            records.append(_artifact_record("perf", "perf", "pass", path, "perf-json.v1"))
    for filename in ("integrity.json", "snapshot.json"):
        path = base / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except OSError, json.JSONDecodeError:
            continue
        raw = payload.get("checks", [payload]) if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            raw = [raw]
        for item in raw:
            if not isinstance(item, dict):
                continue
            target = str(item.get("name") or item.get("check") or filename.removesuffix(".json"))
            ok = item.get("ok", item.get("valid", item.get("passed")))
            status = str(item.get("status", "pass" if ok is not False else "fail")).lower()
            if status not in {"pass", "fail", "warn", "info"}:
                status = "pass"
            message = str(item.get("message") or item.get("summary") or "")[:400] or None
            records.append(
                _artifact_record(
                    "integrity",
                    target,
                    status,
                    path,
                    "integrity-json.v1",
                    message=message,
                    fingerprint=_error_fingerprint(message or target),
                    metadata={"adapter": "integrity"},
                )
            )
    for filename in ("security.sarif", "secret-scan.json"):
        path = base / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except OSError, json.JSONDecodeError:
            continue
        if filename.endswith(".sarif"):
            raw = []
            if isinstance(payload, dict):
                for run in payload.get("runs", []):
                    if isinstance(run, dict):
                        raw.extend(run.get("results", []))
        else:
            raw = payload.get("findings", payload) if isinstance(payload, dict) else payload
        records.extend(
            _diagnostic_artifact_records(
                path,
                "security",
                raw if isinstance(raw, list) else [],
                "sarif.v1" if filename.endswith(".sarif") else "secret-scan-json.v1",
            )
        )
    for filename in ("frontend.json", "tsc.json", "eslint.json", "prettier.json"):
        path = base / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except OSError, json.JSONDecodeError:
            continue
        raw = (
            payload.get("diagnostics", payload.get("results", payload))
            if isinstance(payload, dict)
            else payload
        )
        records.extend(_diagnostic_artifact_records(path, "frontend", raw, "frontend-json.v1"))
    return records


def _retained_artifact_records(wt: str, artifact_dir: str | None) -> list[dict]:
    base = _artifact_base(wt, artifact_dir)
    if base is None:
        return []
    records = []
    manifest = base / "gate-artifacts.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text())
        except OSError, json.JSONDecodeError:
            payload = None
        raw = payload.get("artifacts", []) if isinstance(payload, dict) else payload
        default_schema = payload.get("schema") if isinstance(payload, dict) else None
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not item.get("kind") or not item.get("target"):
                continue
            if item.get("status") not in {"pass", "fail", "warn", "info"}:
                continue
            records.append(
                {
                    "kind": str(item["kind"]),
                    "target": str(item["target"]),
                    "status": item["status"],
                    "duration_s": item.get("duration_s"),
                    "message": str(item["message"])[:400] if item.get("message") else None,
                    "fingerprint": item.get("fingerprint")
                    or _error_fingerprint(str(item.get("message", ""))),
                    "metadata_json": json.dumps(item.get("metadata") or {}, separators=(",", ":")),
                    "schema": item.get("schema") or default_schema or ARTIFACT_SCHEMA,
                    "source_rel": str(manifest),
                    "source_offset": None,
                }
            )
    records.extend(_native_artifact_records(base))
    return records


def refresh(con, *, full: bool = False, root: Path | None = None) -> dict:
    """Incremental byte-offset index of every report copy. Returns counts."""
    root = root or ROOT
    counts = {"files": 0, "runs": 0, "skipped_pending": 0}
    for gate, path, wt, _kind in report_copies(root):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        rel = str(path.relative_to(root))
        row = con.execute(
            "SELECT parse_offset, size, mtime_ns FROM parse_state WHERE src_rel = ?", [rel]
        ).fetchone()
        new_offset = 0 if (full or row is None) else min(row[0], len(raw))
        if row is not None and row[1] > len(raw):
            new_offset = 0  # truncated/rotated by hand: re-parse whole file
        if len(raw) == new_offset:
            counts["files"] += 1
            continue
        text = raw[new_offset:].decode("utf-8", errors="replace")
        new_off = new_offset
        blocks = _split_blocks(text, new_offset)
        for j, (boff, eoff, kind, lines) in enumerate(blocks):
            is_last = j == len(blocks) - 1
            has_summary = bool(_SUMMARY_ROW_RE.search("\n".join(lines)))
            has_exit = any(ln.startswith("**Exit:**") for ln in lines)
            has_bench_rows = sum(1 for ln in lines if st._PERF_TABLE_ROW_RE.match(ln)) >= 2  # noqa: SLF001
            complete = (
                (not is_last) or has_summary or has_exit or (kind == "perf" and has_bench_rows)
            )
            if is_last and not complete:
                counts["skipped_pending"] += 1
                break  # pending tail: do not advance offset past it
            rb = _parse_block(gate, kind, lines)
            if rb is None:
                new_off = eoff
                continue
            meta = _extra_meta(lines)
            new_off = eoff
            _store_run(con, rel, wt, gate, kind, lines, rb, meta, boff, eoff - boff, complete=True)
            counts["runs"] += 1
        now = datetime.now()
        con.execute(
            "INSERT OR REPLACE INTO parse_state VALUES (?, ?, ?, ?, ?)",
            [rel, new_off, len(raw), path.stat().st_mtime_ns, now],
        )
        counts["files"] += 1
    return counts


def _store_run(con, rel, wt, gate, kind, lines, rb, meta, boff, nbytes, complete) -> int:  # noqa: C901
    con.execute("DELETE FROM runs WHERE src_rel = ? AND header_offset = ?", [rel, boff])
    gen = _ts(rb.timestamp) or datetime.now()
    started = _ts(meta.get("started")) or gen
    pyv = next((ln.split("**Python:**")[-1].strip() for ln in lines if "**Python:**" in ln), None)
    run_wt = meta.get("wt", wt) or wt
    retained_junit, artifact_state = _retained_artifact_junit(gate, run_wt, meta.get("artifacts"))
    junit_path, jfacts = _junit_facts(gate, run_wt, started, gen, retained_junit)
    if retained_junit is not None and junit_path is None:
        artifact_state = "corrupt"
    elif not meta.get("artifacts") and junit_path is not None:
        artifact_state = "live"
    jrows = [
        (
            fact["node_id"],
            fact["outcome"],
            fact["seconds"],
            fact["err_head"] or "",
            fact["err_blob"] or "",
        )
        for fact in jfacts
    ]
    exit_code = (
        int(meta["exit"])
        if "exit" in meta
        else (1 if "gate FAIL" in rb.summary else (0 if "gate PASS" in rb.summary else None))
    )
    run_id = con.execute(
        """INSERT INTO runs (src_rel, wt, gate, started_at, generated_at, elapsed_s,
                             jobs, python_ver, commit_sha, patch_name, exit_code, summary,
                             header_offset, nbytes, junit_path, arch_path, artifact_schema,
                             artifact_dir, artifact_state, complete)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?) RETURNING run_id""",
        [
            rel,
            meta.get("wt", wt) or wt,
            gate,
            started,
            gen,
            rb.elapsed,
            rb.jobs,
            pyv,
            meta.get("commit"),
            None,
            exit_code,
            rb.summary,
            boff,
            nbytes,
            junit_path,
            TEST_ARTIFACT_SCHEMA if junit_path else None,
            meta.get("artifacts"),
            artifact_state,
            complete,
        ],
    ).fetchone()[0]
    con.execute("DELETE FROM legs WHERE run_id = ?", [run_id])
    for s in rb.steps:
        err_head = _leg_err_head(lines, s.label) if "FAIL" in s.status else None
        con.execute(
            "INSERT INTO legs VALUES (?, ?, ?, ?, ?)",
            [run_id, s.label, s.seconds, s.status, err_head],
        )
    con.execute("DELETE FROM artifacts WHERE run_id = ?", [run_id])
    for s in rb.steps:
        artifact_kind = _artifact_kind(s.label, kind)
        if artifact_kind is None:
            continue
        message = _leg_err_head(lines, s.label) if "FAIL" in s.status else None
        status = "fail" if "FAIL" in s.status else ("info" if "SKIP" in s.status else "pass")
        con.execute(
            """INSERT INTO artifacts
               (run_id, kind, target, status, duration_s, message, fingerprint,
                metadata_json, schema, source_rel, source_offset)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                run_id,
                artifact_kind,
                s.label,
                status,
                s.seconds,
                message,
                _error_fingerprint(message or ""),
                json.dumps({"adapter": "gate-report-leg", "leg": s.label}, separators=(",", ":")),
                ARTIFACT_SCHEMA,
                rel,
                boff,
            ],
        )
    for artifact in _retained_artifact_records(run_wt, meta.get("artifacts")):
        con.execute(
            """INSERT INTO artifacts
               (run_id, kind, target, status, duration_s, message, fingerprint,
                metadata_json, schema, source_rel, source_offset)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                run_id,
                artifact["kind"],
                artifact["target"],
                artifact["status"],
                artifact["duration_s"],
                artifact["message"],
                artifact["fingerprint"],
                artifact["metadata_json"],
                artifact["schema"],
                artifact["source_rel"],
                artifact["source_offset"],
            ],
        )
    con.execute("DELETE FROM bench WHERE run_id = ?", [run_id])
    if kind == "perf":
        budgets = _perf_budgets(lines)
        for s in rb.steps:
            con.execute(
                "INSERT INTO bench VALUES (?, ?, ?, ?, ?)",
                [run_id, s.label, s.seconds or 0.0, budgets.get(s.label), s.status],
            )
    con.execute("DELETE FROM tests WHERE run_id = ?", [run_id])
    for node, outcome, secs, ehead, eblob in jrows:
        con.execute(
            "INSERT INTO tests VALUES (?, ?, ?, ?, ?, ?, ?)",
            [run_id, "pytest", node, outcome, secs, ehead, eblob],
        )
    con.execute("DELETE FROM test_facts WHERE run_id = ?", [run_id])
    for fact in jfacts:
        con.execute(
            """INSERT INTO test_facts
               (run_id, leg, node_id, file, outcome, seconds, phase, phase_seconds_json,
                markers_json, file_line, err_head, err_blob, error_fingerprint, worker,
                artifact_schema)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                run_id,
                "pytest",
                fact["node_id"],
                fact["file"],
                fact["outcome"],
                fact["seconds"],
                fact["phase"],
                json.dumps(fact["phase_seconds"], separators=(",", ":"))
                if fact["phase_seconds"]
                else None,
                json.dumps(fact["markers"], separators=(",", ":")) if fact["markers"] else None,
                fact["file_line"],
                fact["err_head"],
                fact["err_blob"],
                fact["error_fingerprint"],
                fact["worker"],
                fact["artifact_schema"],
            ],
        )
    if junit_path is not None:
        return run_id  # junit ingested — the -ra text fallback would double-count
    for ln in lines:  # fallback: pytest -ra summary lines (no junit / legacy blocks)
        m = _FAILED_LINE_RE.match(ln)
        if m:
            con.execute(
                "INSERT INTO tests VALUES (?, ?, ?, ?, NULL, ?, NULL)",
                [run_id, "pytest", m.group(2), m.group(1).lower(), ln.strip()[:400]],
            )
    return run_id


def _perf_budgets(lines: list[str]) -> dict[str, float]:
    out = {}
    for ln in lines:
        m = st._PERF_TABLE_ROW_RE.match(ln)  # noqa: SLF001
        if m and not ln.startswith("| Benchmark") and "---" not in ln:
            try:
                out[m.group(1).strip()] = float(m.group(3).rstrip("s"))
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------- queries


def _pick_run(con, run: int | None, wt: str, gate: str | None, only_failed: bool = False):
    q = "SELECT * FROM runs WHERE wt = ?"
    args: list = [wt]
    if gate:
        q += " AND gate = ?"
        args.append(gate)
    if only_failed:
        q += """ AND run_id IN (
            SELECT run_id FROM legs WHERE status LIKE '%FAIL%'
            UNION SELECT run_id FROM tests WHERE outcome IN ('failed','error'))"""
    if run:
        q += " AND run_id = ?"
        args.append(run)
    q += " ORDER BY started_at DESC NULLS LAST LIMIT 1"
    rows = con.execute(q, args).fetchall()
    if not rows:
        return None
    cols = [d[0] for d in con.description]
    return dict(zip(cols, rows[0]))


def _run_digest(con, r: dict, max_legs: int = 24) -> str:
    bad = [
        lg
        for lg in con.execute(
            "SELECT leg, seconds, status FROM legs WHERE run_id = ? AND status LIKE '%FAIL%'",
            [r["run_id"]],
        ).fetchall()
    ]
    legs = con.execute(
        "SELECT leg, seconds, status FROM legs WHERE run_id = ? LIMIT ?", [r["run_id"], max_legs]
    ).fetchall()
    ntests = con.execute(
        "SELECT outcome, COUNT(*) FROM tests WHERE run_id = ? GROUP BY outcome", [r["run_id"]]
    ).fetchall()
    out = [
        f"run {r['run_id']}  {r['gate']}{'@' + r['wt'] if r['wt'] else ''}  started {r['started_at']}  wall {r['elapsed_s']}s  exit {r['exit_code']}"
    ]
    out.append(
        f"  summary: {r['summary']}" + (f"  commit {r['commit_sha']}" if r["commit_sha"] else "")
    )
    for leg, secs, status in legs:
        out.append(f"  {leg:.<34s} {secs if secs is not None else '—':>7}  {status}")
    if ntests:
        out.append("  tests: " + ", ".join(f"{n} {o}" for o, n in ntests))
    if bad:
        out.append("  FAILED LEGS: " + ", ".join(lg[0] for lg in bad))
    return "\n".join(out)


def cmd_artifacts(con, args) -> str:  # noqa: C901
    where = ["1 = 1"]
    params: list = []
    if args.run is not None:
        where.append("a.run_id = ?")
        params.append(args.run)
    if args.kind:
        where.append("a.kind = ?")
        params.append(args.kind)
    if args.status:
        where.append("a.status = ?")
        params.append(args.status)
    if args.gate:
        where.append("r.gate = ?")
        params.append(args.gate)
    if args.wt:
        where.append("r.wt = ?")
        params.append(args.wt)
    rows = con.execute(
        f"""SELECT a.run_id, r.gate, r.wt, r.started_at, a.kind, a.target, a.status,
                          a.duration_s, a.message, a.fingerprint, a.metadata_json, a.schema,
                          a.source_rel, a.source_offset
             FROM artifacts a JOIN runs r USING (run_id)
             WHERE {" AND ".join(where)}
             ORDER BY r.started_at DESC, a.kind, a.target LIMIT ?""",  # noqa: S608
        [*params, args.last],
    ).fetchall()
    names = [
        "run_id",
        "gate",
        "wt",
        "started_at",
        "kind",
        "target",
        "status",
        "duration_s",
        "message",
        "fingerprint",
        "metadata_json",
        "schema",
        "source_rel",
        "source_offset",
    ]
    records = []
    for row in rows:
        record = dict(zip(names, row, strict=True))
        raw_metadata = record.pop("metadata_json")
        try:
            record["metadata"] = json.loads(raw_metadata or "{}")
        except json.JSONDecodeError:
            record["metadata"] = {"raw": raw_metadata}
        records.append(record)
    if args.json:
        return json.dumps(records, default=str, sort_keys=True)
    if not records:
        return "no matching artifacts (optional artifact outputs are not collected)"
    out = [f"{len(records)} artifact record(s)"]
    for record in records:
        duration = f" {record['duration_s']:.2f}s" if record["duration_s"] is not None else ""
        scope = f"@{record['wt']}" if record["wt"] else ""
        message = f" — {record['message'].splitlines()[0]}" if record["message"] else ""
        out.append(
            f"  run {record['run_id']} {record['gate']}{scope} {record['kind']} "
            f"{record['target']} {record['status']}{duration}{message}"
        )
    return "\n".join(out)


def cmd_latest(con, args) -> str:
    gate = None if args.gate == "all" else args.gate
    if gate is None and not args.run:
        gates = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT gate FROM runs WHERE wt = ? ORDER BY gate", [args.wt]
            ).fetchall()
        ]
        out = ["newest run per gate:"]
        for g in gates:
            r = _pick_run(con, None, args.wt, g)
            if r:
                out.append(
                    f"  {r['gate']:12s} run {r['run_id']}  {r['started_at']}  "
                    f"exit {r['exit_code']}  {r['summary']}"
                )
        return "\n".join(out)
    r = _pick_run(con, args.run, args.wt, gate)
    if r is None:
        return "no indexed runs (run a gate, then retry; refresh is automatic)"
    if args.json:
        return json.dumps(r, default=str)
    return _run_digest(con, r)


def cmd_failures(con, args) -> str:
    if getattr(args, "recent", None):
        return _recent_failures(con, args)
    gate = None if args.gate == "all" else args.gate
    r = _pick_run(con, args.run, args.wt, gate, only_failed=not args.any_run)
    if r is None:
        return "no failed runs indexed" if not args.any_run else "no runs indexed"
    tests = con.execute(
        "SELECT node_id, outcome, seconds, err_head, err_blob FROM tests "
        "WHERE run_id = ? AND outcome IN ('failed','error') LIMIT ?",
        [r["run_id"], args.limit],
    ).fetchall()
    bad_legs = con.execute(
        "SELECT leg, status FROM legs WHERE run_id = ? AND status LIKE '%FAIL%'", [r["run_id"]]
    ).fetchall()
    lines = [
        f"run {r['run_id']}  {r['gate']}{'@' + r['wt'] if r['wt'] else ''}  started {r['started_at']}  exit {r['exit_code']}"
    ]
    if not tests and not bad_legs:
        lines.append("  no failures in this run")
    leg_heads = {
        lg[0]: lg[1]
        for lg in con.execute(
            "SELECT leg, err_head FROM legs WHERE run_id = ? AND err_head IS NOT NULL",
            [r["run_id"]],
        ).fetchall()
    }
    for leg, status in bad_legs:
        lines.append(f"  LEG FAIL: {leg} ({status})")
        for el in (leg_heads.get(leg) or "").splitlines()[:6]:
            lines.append(f"      {el}")
    for node, outcome, secs, ehead, _eblob in tests:
        lines.append(
            f"  {outcome.upper()}: {node} ({secs:.2f}s)" if secs else f"  {outcome.upper()}: {node}"
        )
        if ehead and not args.brief:
            for el in ehead.splitlines()[:6]:
                lines.append(f"      {el}")
    if args.full:
        lines.extend(_full_tail(r))
    text = "\n".join(lines)
    return (
        text
        if args.full or len(text) < 4000
        else text[:4000] + "\n  … (--full for complete error text)"
    )


def cmd_recent(con, args) -> str:
    """Last N runs of a gate, newest first, PASS/FAIL each (run history)."""
    gate = None if args.gate == "all" else args.gate
    q = "SELECT * FROM runs WHERE wt = ?"
    qargs: list = [args.wt]
    if gate:
        q += " AND gate = ?"
        qargs.append(gate)
    q += " ORDER BY started_at DESC LIMIT ?"
    qargs.append(args.last)
    rows = con.execute(q, qargs).fetchall()
    if not rows:
        return "no indexed runs for this gate/wt"
    cols = [d[0] for d in con.description]
    out = [f"last {len(rows)} {gate or 'all-gate'} runs (newest first):"]
    for row in rows:
        r = dict(zip(cols, row))
        bad_legs = [
            lg[0]
            for lg in con.execute(
                "SELECT leg FROM legs WHERE run_id = ? AND status LIKE '%FAIL%'", [r["run_id"]]
            ).fetchall()
        ]
        failed = con.execute(
            "SELECT node_id FROM tests WHERE run_id = ? AND outcome IN ('failed','error')",
            [r["run_id"]],
        ).fetchall()
        status = "FAIL" if (bad_legs or failed) else "PASS"
        wt = f"@{r['wt']}" if r["wt"] else ""
        line = (
            f"  run {r['run_id']}  {r['gate']}{wt}  {r['started_at']}  "
            f"exit {r['exit_code']}  {status}"
        )
        if bad_legs:
            line += f"  legs: {', '.join(bad_legs)}"
        if failed:
            line += f"  tests: {len(failed)} failed"
        out.append(line)
        if args.tests and failed:
            for (node,) in failed[:5]:
                out.append(f"        {node}")
            if len(failed) > 5:
                out.append(f"        +{len(failed) - 5} more")
    return "\n".join(out)


def _recent_failures(con, args) -> str:
    """Cross-run view: one line per failed run among the last N runs."""
    gate = None if args.gate == "all" else args.gate
    q = "SELECT * FROM runs WHERE wt = ?"
    qargs: list = [args.wt]
    if gate:
        q += " AND gate = ?"
        qargs.append(gate)
    q += " ORDER BY started_at DESC LIMIT ?"
    qargs.append(args.recent)
    rows = con.execute(q, qargs).fetchall()
    if not rows:
        return "no indexed runs for this gate/wt"
    cols = [d[0] for d in con.description]
    out, nfail = [], 0
    for row in rows:
        r = dict(zip(cols, row))
        legs = con.execute(
            "SELECT leg FROM legs WHERE run_id = ? AND status LIKE '%FAIL%'", [r["run_id"]]
        ).fetchall()
        ntests = con.execute(
            "SELECT COUNT(*) FROM tests WHERE run_id = ? AND outcome IN ('failed','error')",
            [r["run_id"]],
        ).fetchone()[0]
        if not legs and not ntests:
            continue
        nfail += 1
        legnames = ", ".join(lg[0] for lg in legs)
        tpart = f"; {ntests} failed test(s)" if ntests else ""
        wt = f"@{r['wt']}" if r["wt"] else ""
        out.append(
            f"  run {r['run_id']}  {r['gate']}{wt}  {r['started_at']}  "
            f"exit {r['exit_code']}  - {legnames or 'tests'}{tpart}"
        )
    scope = gate or "all gates"
    head = f"{nfail} of the last {len(rows)} {scope} runs failed  (detail: --run ID)"
    return head + ("\n" + "\n".join(out) if out else " — all green")


def _full_tail(r: dict) -> list[str]:
    """Last 60 lines of the run's raw block — live file or zstd archive."""
    if r["arch_path"]:
        blob = decompress(Path(r["arch_path"]).read_bytes()).decode("utf-8", errors="replace")
        return ["--- archived block tail (last 60 lines) ---"] + blob.splitlines()[-60:]
    path = ROOT / r["src_rel"]
    if not path.exists():
        return ["  (raw report file no longer present)"]
    with open(path, "rb") as f:
        f.seek(r["header_offset"])
        blob = f.read(r["nbytes"]).decode("utf-8", errors="replace")
    return ["--- full block tail (last 60 lines) ---"] + blob.splitlines()[-60:]


def _run_record(con, run_id: int, gate: str | None = None) -> dict | None:
    query = "SELECT * FROM runs WHERE run_id = ?"
    params: list = [run_id]
    if gate:
        query += " AND gate = ?"
        params.append(gate)
    row = con.execute(query, params).fetchone()
    if row is None:
        return None
    return dict(zip([column[0] for column in con.description], row))


def _compare_facts(con, run_id: int) -> dict[str, dict]:
    rows = con.execute(
        """SELECT node_id, leg, file, outcome, seconds, phase, phase_seconds_json,
                  markers_json, file_line, error_fingerprint, worker
           FROM test_facts WHERE run_id = ? ORDER BY node_id""",
        [run_id],
    ).fetchall()
    names = [
        "leg",
        "file",
        "outcome",
        "seconds",
        "phase",
        "phase_seconds_json",
        "markers_json",
        "file_line",
        "error_fingerprint",
        "worker",
    ]
    return {row[0]: dict(zip(names, row[1:], strict=True)) | {"node_id": row[0]} for row in rows}


def _pct_delta(before: float | None, after: float | None) -> float | None:
    if before in (None, 0) or after is None:
        return None
    return (after - before) / before * 100


def _compare_payload(con, run_a: int, run_b: int, gate: str | None = None) -> dict:  # noqa: C901
    left = _run_record(con, run_a, gate)
    right = _run_record(con, run_b, gate)
    if left is None or right is None:
        missing = run_a if left is None else run_b
        return {"error": f"run {missing} not found"}
    leg_rows = {}
    for label, record in (("a", left), ("b", right)):
        leg_rows[label] = {
            row[0]: {"seconds": row[1], "status": row[2]}
            for row in con.execute(
                "SELECT leg, seconds, status FROM legs WHERE run_id = ?", [record["run_id"]]
            ).fetchall()
        }
    leg_names_a, leg_names_b = set(leg_rows["a"]), set(leg_rows["b"])
    leg_changes = []
    for leg in sorted(leg_names_a & leg_names_b):
        before = leg_rows["a"][leg]
        after = leg_rows["b"][leg]
        if before != after:
            leg_changes.append(
                {
                    "leg": leg,
                    "before": before,
                    "after": after,
                    "delta_s": (
                        after["seconds"] - before["seconds"]
                        if after["seconds"] is not None and before["seconds"] is not None
                        else None
                    ),
                    "delta_pct": _pct_delta(before["seconds"], after["seconds"]),
                }
            )
    facts_a, facts_b = _compare_facts(con, run_a), _compare_facts(con, run_b)
    failed_a = {node for node, fact in facts_a.items() if fact["outcome"] in ("failed", "error")}
    failed_b = {node for node, fact in facts_b.items() if fact["outcome"] in ("failed", "error")}
    outcome_changes = []
    for node in sorted(facts_a.keys() & facts_b.keys()):
        before, after = facts_a[node], facts_b[node]
        if before["outcome"] != after["outcome"]:
            outcome_changes.append(
                {
                    "node_id": node,
                    "before": before["outcome"],
                    "after": after["outcome"],
                    "before_seconds": before["seconds"],
                    "after_seconds": after["seconds"],
                    "delta_s": (
                        after["seconds"] - before["seconds"]
                        if after["seconds"] is not None and before["seconds"] is not None
                        else None
                    ),
                }
            )
    warnings = []
    for key, label in (
        ("gate", "gate"),
        ("wt", "worktree"),
        ("python_ver", "Python version"),
        ("jobs", "job count"),
        ("commit_sha", "source commit"),
    ):
        if left.get(key) != right.get(key):
            warnings.append(f"{label} differs: {left.get(key)!r} -> {right.get(key)!r}")
    if left.get("complete") != right.get("complete"):
        warnings.append("run completeness differs")
    if leg_names_a != leg_names_b:
        warnings.append("gate shape differs: leg labels are not identical")
    if len(facts_a) != len(facts_b):
        warnings.append(f"test count differs: {len(facts_a)} -> {len(facts_b)}")
    for label, record in (("a", left), ("b", right)):
        record["test_count"] = con.execute(
            "SELECT COUNT(*) FROM test_facts WHERE run_id = ?", [record["run_id"]]
        ).fetchone()[0]
    return {
        "run_a": left,
        "run_b": right,
        "legs": {
            "added": sorted(leg_names_b - leg_names_a),
            "removed": sorted(leg_names_a - leg_names_b),
            "changed": leg_changes,
        },
        "tests": {
            "added": sorted(facts_b.keys() - facts_a.keys()),
            "removed": sorted(facts_a.keys() - facts_b.keys()),
            "failed_added": sorted(failed_b - failed_a),
            "failed_removed": sorted(failed_a - failed_b),
            "failed_persisting": sorted(failed_a & failed_b),
            "outcome_changes": outcome_changes,
        },
        "warnings": warnings,
    }


def cmd_compare(con, args) -> str:
    payload = _compare_payload(con, args.run_a, args.run_b, args.gate)
    if "error" in payload:
        return payload["error"]
    if args.json:
        return json.dumps(payload, default=str, sort_keys=True)
    left, right = payload["run_a"], payload["run_b"]
    out = [
        f"compare run {left['run_id']} -> {right['run_id']} ({left['gate']} -> {right['gate']})",
        f"  metadata: commit {left.get('commit_sha')} -> {right.get('commit_sha')}, "
        f"elapsed {left.get('elapsed_s')}s -> {right.get('elapsed_s')}s",
    ]
    changed = payload["legs"]["changed"]
    out.append(f"  legs: {len(changed)} changed")
    for change in changed:
        out.append(
            f"    {change['leg']}: {change['before']['seconds']}s {change['before']['status']} -> "
            f"{change['after']['seconds']}s {change['after']['status']} "
            f"({change['delta_s']:+.2f}s)"
            if change["delta_s"] is not None
            else f"    {change['leg']}: {change['before']['status']} -> {change['after']['status']}"
        )
    for key, label in (
        ("failed_added", "failed added"),
        ("failed_removed", "failed removed"),
        ("failed_persisting", "failed persisting"),
    ):
        values = payload["tests"][key]
        if values:
            out.append(f"  {label}: {', '.join(values)}")
    changes = payload["tests"]["outcome_changes"]
    if changes:
        out.append(f"  outcome changes: {len(changes)}")
        for change in changes:
            out.append(f"    {change['node_id']}: {change['before']} -> {change['after']}")
    if payload["warnings"]:
        out.append("  warnings:")
        out.extend(f"    {warning}" for warning in payload["warnings"])
    return "\n".join(out)


def _test_history_rows(con, args) -> tuple[list[dict], str]:
    where = ["1 = 1"]
    params: list = []
    if getattr(args, "gate", None):
        where.append("r.gate = ?")
        params.append(args.gate)
    if getattr(args, "wt", ""):
        where.append("r.wt = ?")
        params.append(args.wt)
    if args.node:
        where.append("tf.node_id = ?")
        params.append(args.node)
        metric = "node history"
        order = "r.started_at DESC"
    elif args.marker:
        where.append("tf.markers_json LIKE ?")
        params.append(f'%"{args.marker}"%')
        metric = f"marker={args.marker}"
        order = "r.started_at DESC, tf.seconds DESC NULLS LAST"
    elif args.slowest:
        metric = "slowest serial testcase seconds (not wall time)"
        order = "tf.seconds DESC NULLS LAST, r.started_at DESC"
    else:
        return [], "specify --run, --node, --marker, or --slowest"
    query = f"""SELECT r.run_id, r.gate, r.wt, r.started_at, r.commit_sha, r.python_ver,
                         r.jobs, tf.node_id, tf.file, tf.outcome, tf.seconds, tf.phase,
                         tf.phase_seconds_json, tf.markers_json, tf.file_line,
                         tf.error_fingerprint, tf.worker
                  FROM test_facts tf JOIN runs r USING (run_id)
                   WHERE {" AND ".join(where)} ORDER BY {order} LIMIT ?"""  # noqa: S608
    params.append(args.last)
    names = [
        "run_id",
        "gate",
        "wt",
        "started_at",
        "commit_sha",
        "python_ver",
        "jobs",
        "node_id",
        "file",
        "outcome",
        "seconds",
        "phase",
        "phase_seconds_json",
        "markers_json",
        "file_line",
        "error_fingerprint",
        "worker",
    ]
    rows = []
    for row in con.execute(query, params).fetchall():
        record = dict(zip(names, row, strict=True))
        for key, default in (("phase_seconds_json", "{}"), ("markers_json", "[]")):
            try:
                record[key.removesuffix("_json")] = json.loads(record[key] or default)
            except json.JSONDecodeError:
                record[key.removesuffix("_json")] = default
        rows.append(record)
    return rows, metric


def cmd_tests(con, args) -> str:
    if args.run is not None:
        q = "SELECT node_id, outcome, seconds, err_head FROM tests WHERE run_id = ?"
        args_list: list = [args.run]
        if args.outcome != "all":
            q += " AND outcome = ?"
            args_list.append(args.outcome)
        rows = con.execute(q + " LIMIT 200", args_list).fetchall()
        if args.json:
            return json.dumps(
                [dict(zip(("node_id", "outcome", "seconds", "err_head"), r)) for r in rows]
            )
        return (
            "\n".join(
                f"{o.upper():7s} {n} ({s:.2f}s)" if s else f"{o.upper():7s} {n}"
                for n, o, s, _e in rows
            )
            or "no test rows (junit absent and no failures text-parsed)"
        )
    rows, metric = _test_history_rows(con, args)
    if args.json:
        return json.dumps(rows, default=str, sort_keys=True)
    if not rows:
        return f"no test history for {metric}"
    out = [metric]
    for row in rows:
        duration = f" ({row['seconds']:.2f}s)" if row["seconds"] is not None else ""
        markers = ",".join(row["markers"])
        marker_text = f" markers={markers}" if markers else ""
        scope = f"@{row['wt']}" if row["wt"] else ""
        out.append(
            f"  run {row['run_id']} {row['gate']}{scope} {row['started_at']} "
            f"{row['outcome']} {row['node_id']}{duration}{marker_text}"
        )
    return "\n".join(out)


def _cluster_payload(con, args) -> list[dict]:
    where = [
        "tf.error_fingerprint IS NOT NULL",
        "tf.error_fingerprint != ''",
        "tf.outcome IN ('failed', 'error')",
    ]
    params: list = []
    if args.fingerprint:
        where.append("tf.error_fingerprint = ?")
        params.append(args.fingerprint)
    if args.last:
        where.append("r.run_id IN (SELECT run_id FROM runs ORDER BY started_at DESC LIMIT ?)")
        params.append(args.last)
    names = [
        "fingerprint",
        "run_id",
        "gate",
        "wt",
        "started_at",
        "commit_sha",
        "node_id",
        "outcome",
        "err_head",
        "src_rel",
        "header_offset",
        "junit_path",
    ]
    grouped: dict[str, dict] = {}
    for row in con.execute(
        f"""SELECT tf.error_fingerprint, r.run_id, r.gate, r.wt, r.started_at,
                   r.commit_sha, tf.node_id, tf.outcome, tf.err_head, r.src_rel,
                   r.header_offset, r.junit_path
            FROM test_facts tf JOIN runs r USING (run_id)
             WHERE {" AND ".join(where)} ORDER BY r.started_at, r.run_id, tf.node_id""",  # noqa: S608
        params,
    ).fetchall():
        record = dict(zip(names, row, strict=True))
        fingerprint = record["fingerprint"]
        cluster = grouped.setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "first_seen": record["started_at"],
                "last_seen": record["started_at"],
                "run_ids": set(),
                "distinct_commits": set(),
                "distinct_worktrees": set(),
                "affected_node_ids": set(),
                "representative_error_head": None,
                "transitions": [],
                "sources": [],
                "history": [],
            },
        )
        cluster["last_seen"] = record["started_at"]
        cluster["run_ids"].add(record["run_id"])
        cluster["distinct_commits"].add(record["commit_sha"] or "")
        cluster["distinct_worktrees"].add(record["wt"] or "")
        cluster["affected_node_ids"].add(record["node_id"])
        if not cluster["representative_error_head"] and record["err_head"]:
            cluster["representative_error_head"] = record["err_head"][:400]
        prior = next(
            (item for item in reversed(cluster["history"]) if item["node_id"] == record["node_id"]),
            None,
        )
        if prior and prior["outcome"] != record["outcome"]:
            cluster["transitions"].append(
                {
                    "node_id": record["node_id"],
                    "from": prior["outcome"],
                    "to": record["outcome"],
                    "at": record["started_at"],
                }
            )
        cluster["history"].append(
            {
                "run_id": record["run_id"],
                "node_id": record["node_id"],
                "outcome": record["outcome"],
                "started_at": record["started_at"],
            }
        )
        source = {
            "run_id": record["run_id"],
            "report": record["src_rel"],
            "report_offset": record["header_offset"],
            "junit": record["junit_path"],
        }
        if source not in cluster["sources"]:
            cluster["sources"].append(source)
    for cluster in grouped.values():
        cluster["run_count"] = len(cluster.pop("run_ids"))
        cluster["distinct_commits"] = len(cluster["distinct_commits"])
        cluster["distinct_worktrees"] = len(cluster["distinct_worktrees"])
        cluster["affected_node_ids"] = sorted(cluster["affected_node_ids"])
    return sorted(grouped.values(), key=lambda item: item["last_seen"], reverse=True)


def cmd_clusters(con, args) -> str:
    clusters = _cluster_payload(con, args)
    if args.json:
        return json.dumps(clusters, default=str, sort_keys=True)
    if not clusters:
        return "no recurring normalized failures"
    out = [f"{len(clusters)} normalized failure cluster(s)"]
    for cluster in clusters:
        out.append(
            f"  {cluster['fingerprint']}  runs={cluster['run_count']}  "
            f"commits={cluster['distinct_commits']}  worktrees={cluster['distinct_worktrees']}"
        )
        out.append(
            f"    {cluster['first_seen']} -> {cluster['last_seen']}  "
            f"nodes={', '.join(cluster['affected_node_ids'])}"
        )
        if cluster["transitions"]:
            transitions = ", ".join(
                f"{item['node_id']} {item['from']}->{item['to']}" for item in cluster["transitions"]
            )
            out.append(f"    transitions: {transitions}")
        if cluster["representative_error_head"]:
            out.append(
                f"    representative: {cluster['representative_error_head'].splitlines()[0]}"
            )
    return "\n".join(out)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _test_phase_totals(con, run_ids: list[int]) -> dict[str, float]:
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    totals = {"total": 0.0, "setup": 0.0, "call": 0.0, "teardown": 0.0}
    for seconds, phase_json in con.execute(
        f"""SELECT seconds, phase_seconds_json FROM test_facts
            WHERE run_id IN ({placeholders})""",  # noqa: S608
        run_ids,
    ).fetchall():
        if seconds is not None:
            totals["total"] += seconds
        try:
            phases = json.loads(phase_json or "{}")
        except json.JSONDecodeError:
            phases = {}
        for phase in ("setup", "call", "teardown"):
            value = phases.get(phase, {}).get("seconds")
            if isinstance(value, (int, float)):
                totals[phase] += value
    return totals


def cmd_timing(con, args) -> str:
    pass_only = getattr(args, "pass_only", False)
    status_clause = (
        " AND b.status NOT LIKE '%FAIL%' AND b.status NOT LIKE '%SKIP%'" if pass_only else ""
    )
    rows = con.execute(
        f"""SELECT r.run_id, r.started_at, b.seconds, b.budget_s, b.status, r.wt
            FROM bench b JOIN runs r USING (run_id)
            WHERE b.bench = ?{status_clause} ORDER BY r.started_at DESC LIMIT ?""",  # noqa: S608
        [args.leg, args.last],
    ).fetchall()
    if not rows:
        leg_clause = (
            " AND l.status NOT LIKE '%FAIL%' AND l.status NOT LIKE '%SKIP%'" if pass_only else ""
        )
        rows = con.execute(
            f"""SELECT r.run_id, r.started_at, l.seconds, NULL, l.status, r.wt
                FROM legs l JOIN runs r USING (run_id)
                WHERE l.leg = ?{leg_clause} ORDER BY r.started_at DESC LIMIT ?""",  # noqa: S608
            [args.leg, args.last],
        ).fetchall()
    if not rows:
        return f"no history for leg {args.leg!r}"
    out = [f"{args.leg} — last {len(rows)} runs (newest first)"]
    for _, started, secs, budget, status, wt in rows:
        b = f" / budget {budget}s" if budget else ""
        out.append(f"  {started}{'@' + wt if wt else '':14s} {secs:7.2f}s{b}  {status}")
    values = [row[2] for row in rows if row[2] is not None]
    if values:
        out.append(
            f"  stats: min {min(values):.2f}s · p25 {_percentile(values, 0.25):.2f}s · "
            f"median {_percentile(values, 0.5):.2f}s · p75 {_percentile(values, 0.75):.2f}s · "
            f"max {max(values):.2f}s"
        )
    budget = rows[0][3]
    if budget:
        out.append(
            f"  latest budget utilization: {rows[0][2] / budget * 100:.1f}% "
            f"({rows[0][2]:.2f}s / {budget:.2f}s)"
        )
    run_ids = [row[0] for row in rows]
    phase_totals = _test_phase_totals(con, run_ids)
    if phase_totals["total"]:
        out.append(
            "  test phases (serial testcase seconds; not wall time): "
            f"total {phase_totals['total']:.2f}s · setup {phase_totals['setup']:.2f}s · "
            f"call {phase_totals['call']:.2f}s · teardown {phase_totals['teardown']:.2f}s"
        )
    if getattr(args, "critical_path", False):
        placeholders = ",".join("?" for _ in run_ids)
        critical = con.execute(
            f"""SELECT r.run_id, r.started_at, tf.node_id, tf.worker, tf.seconds
                FROM test_facts tf JOIN runs r USING (run_id)
                WHERE tf.run_id IN ({placeholders})
                ORDER BY tf.seconds DESC NULLS LAST LIMIT 10""",  # noqa: S608
            run_ids,
        ).fetchall()
        out.append("  critical-path candidates (serial testcase seconds; not wall time):")
        out.extend(
            f"    run {run_id} {started} {seconds:.2f}s worker {worker or 'unknown'} {node}"
            for run_id, started, node, worker, seconds in critical
            if seconds is not None
        )
    return "\n".join(out)


def cmd_grep(con, args) -> str:
    rows = con.execute(
        """SELECT r.run_id, r.gate, r.started_at, t.node_id, t.err_head
           FROM tests t JOIN runs r USING (run_id)
           WHERE t.err_blob LIKE ? LIMIT ?""",
        [f"%{args.substr}%", args.limit],
    ).fetchall()
    return (
        "\n".join(
            f"[{rid}] {gate} {started}: {node} — {(ehead or '')[:120]}"
            for rid, gate, started, node, ehead in rows
        )
        or "no matches in indexed failure text"
    )


# ---------------------------------------------------------------- rotation


def cmd_rotate(con, args) -> str:
    plan = []
    for gate, path, wt, _kind in report_copies():
        if wt:
            continue  # worktree copies are tiny; main corpus is the growth risk
        rel = str(path.relative_to(ROOT))
        size = path.stat().st_size
        if size <= args.max_mb * 1024 * 1024:
            continue
        blocks = con.execute(
            """SELECT run_id, header_offset, started_at FROM runs
               WHERE src_rel = ? AND arch_path IS NULL AND complete
               ORDER BY header_offset""",
            [rel],
        ).fetchall()
        if len(blocks) <= args.keep_runs:
            continue
        cut = blocks[len(blocks) - args.keep_runs]
        prefix_end = cut[1]
        ts = blocks[0][2]
        stamp = ts.strftime("%Y%m%dT%H%M%S") if ts else str(int(time.time()))
        arch = ROOT / "archives" / Path(rel).stem / f"{Path(rel).stem}.{stamp}.zst"
        plan.append((rel, path, prefix_end, arch, len(blocks) - args.keep_runs))
    if not plan:
        return (
            f"nothing to rotate (default keep policy: last {args.keep_runs} runs, "
            f"rotate past {args.max_mb:g} MB per report; tune with --keep-runs/--max-mb)"
        )
    out = []
    for rel, path, prefix_end, arch, n in plan:
        out.append(f"  {rel}: archive first {n} runs (bytes < {prefix_end}) -> {arch}")
        if args.apply:
            with open(path, "rb") as f:
                prefix = f.read(prefix_end)
            arch.parent.mkdir(parents=True, exist_ok=True)
            arch.write_bytes(compress(prefix))
            tail = path.read_bytes()[prefix_end:]
            tmp = path.with_suffix(path.suffix + ".rot")
            tmp.write_bytes(tail)
            os.replace(tmp, path)
            con.execute(
                "UPDATE runs SET arch_path = ? WHERE src_rel = ? AND header_offset < ?",
                [str(arch), rel, prefix_end],
            )
            con.execute(
                "UPDATE parse_state SET parse_offset = GREATEST(parse_offset - ?, 0), size = ?, mtime_ns = ?, updated_at = ? WHERE src_rel = ?",
                [prefix_end, len(tail), path.stat().st_mtime_ns, datetime.now(), rel],
            )
    header = "ROTATION PLAN (dry-run; --apply to execute):" if not args.apply else "ROTATED:"
    return header + "\n" + "\n".join(out)


def cmd_refresh(con, args) -> str:
    counts = refresh(con, full=args.full)
    return f"indexed: {counts['files']} files, {counts['runs']} new runs, {counts['skipped_pending']} pending tails"


# ---------------------------------------------------------------- main


def _build_parser() -> argparse.ArgumentParser:  # noqa: C901
    ap = argparse.ArgumentParser(
        prog="gate_query",
        description=__doc__.split("\n")[0],
        epilog="Gates indexed: qa | advisory | integration | maint | perf "
        "(integrity/verify pending S4). --gate defaults to qa — the "
        "gate prompts run — pass --gate all for the newest run "
        "across gates.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (
        ("latest", "digest of the newest run"),
        ("recent", "last N runs of a gate, PASS/FAIL each (--tests for node ids)"),
        ("failures", "failed legs/tests of the newest (or given) run"),
        ("tests", "per-test rows for a run, or historical node/marker/slowest queries"),
        ("compare", "compare two indexed runs"),
        ("clusters", "group recurring normalized test failures"),
        ("artifacts", "read-only generic gate artifact records"),
        ("timing", "leg timing history vs budget"),
        ("grep", "keyword over indexed failure text"),
        ("rotate", "archive old runs to outputs/archives (zstd)"),
        ("refresh", "force re-index"),
    ):
        sp = sub.add_parser(name, help=help_)
        if name in ("latest", "failures"):
            sp.add_argument(
                "-wt",
                "--wt",
                default="",
                metavar="NAME",
                help="worktree name (default main; -wt NAME and --wt NAME both work)",
            )
            sp.add_argument(
                "--gate",
                default="qa",
                help="gate filter (default: qa; one of qa|advisory|integration|maint|perf, or 'all')",
            )
            sp.add_argument("--run", type=int, default=None, help="explicit run id")
            sp.add_argument("--json", action="store_true")
        if name == "failures":
            sp.add_argument("--full", action="store_true", help="append full block tail")
            sp.add_argument("--brief", action="store_true", help="node ids only")
            sp.add_argument("--limit", type=int, default=20)
            sp.add_argument("--any-run", action="store_true", help="don't require failures")
            sp.add_argument(
                "--recent",
                type=int,
                default=None,
                metavar="N",
                help="cross-run view: one line per failed run among the last N runs",
            )
        if name == "recent":
            sp.add_argument(
                "-wt", "--wt", default="", metavar="NAME", help="worktree name (default main)"
            )
            sp.add_argument("--gate", default="qa", help="gate filter (default: qa; or 'all')")
            sp.add_argument(
                "--last", type=int, default=10, help="how many runs to list (default: 10)"
            )
            sp.add_argument(
                "--tests",
                action="store_true",
                help="list failed test node ids under each failed run",
            )
        if name == "compare":
            sp.add_argument("run_a", type=int)
            sp.add_argument("run_b", type=int)
            sp.add_argument("--gate", default=None, help="require both runs to use this gate")
            sp.add_argument("--json", action="store_true")
        if name == "clusters":
            sp.add_argument("--fingerprint", default=None, help="restrict to one fingerprint")
            sp.add_argument("--last", type=int, default=0, help="inspect only the last N runs")
            sp.add_argument("--json", action="store_true")
        if name == "artifacts":
            sp.add_argument("--run", type=int, default=None, help="restrict to one run")
            sp.add_argument("--kind", default=None, help="filter by artifact kind")
            sp.add_argument("--status", default=None, choices=("pass", "fail", "warn", "info"))
            sp.add_argument("--gate", default=None, help="optional gate filter")
            sp.add_argument("-wt", "--wt", default="", metavar="NAME", help="worktree filter")
            sp.add_argument("--last", type=int, default=50, help="maximum records")
            sp.add_argument("--json", action="store_true")
        if name == "tests":
            sp.add_argument("--run", type=int, default=None, help="show one run (legacy mode)")
            sp.add_argument("--node", help="show history for one node ID")
            sp.add_argument("--marker", help="show history for one pytest marker")
            sp.add_argument("--slowest", action="store_true", help="rank serial testcase seconds")
            sp.add_argument("--last", type=int, default=10, help="history rows or runs to inspect")
            sp.add_argument("--gate", default=None, help="optional gate filter")
            sp.add_argument("-wt", "--wt", default="", metavar="NAME", help="worktree filter")
            sp.add_argument("--outcome", default="all")
            sp.add_argument("--json", action="store_true")
        if name == "timing":
            sp.add_argument("--leg", required=True)
            sp.add_argument("--last", type=int, default=20)
            sp.add_argument("--pass-only", action="store_true", help="exclude failed/skipped runs")
            sp.add_argument(
                "--critical-path",
                action="store_true",
                help="show slowest indexed testcase candidates",
            )
        if name == "grep":
            sp.add_argument("substr")
            sp.add_argument("--limit", type=int, default=25)
        if name == "rotate":
            sp.add_argument("--apply", action="store_true", help="execute (default: dry-run plan)")
            sp.add_argument(
                "--keep-runs", type=int, default=30, help="runs kept live per report (default: 30)"
            )
            sp.add_argument(
                "--max-mb",
                type=float,
                default=8.0,
                help="rotate only when the report exceeds this size (default: 8 MB)",
            )
        if name == "refresh":
            sp.add_argument("--full", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    con = connect()
    try:
        if args.cmd != "refresh":
            refresh(con)
        fn = {
            "latest": cmd_latest,
            "recent": cmd_recent,
            "failures": cmd_failures,
            "tests": cmd_tests,
            "compare": cmd_compare,
            "clusters": cmd_clusters,
            "artifacts": cmd_artifacts,
            "timing": cmd_timing,
            "grep": cmd_grep,
            "rotate": cmd_rotate,
            "refresh": cmd_refresh,
        }[args.cmd]
        print(fn(con, args))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
