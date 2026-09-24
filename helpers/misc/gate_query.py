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
    tests       full per-test rows for a run (junitxml-backed)
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
    text = re.sub(r"/tmp/pytest-of-[^\s'\"]+", "<tmp>", blob)
    text = re.sub(r"/tmp/[^\s'\"]+", "<tmp>", text)
    text = re.sub(r"0x[0-9a-fA-F]+", "<addr>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?s\b", "<duration>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b", "<time>", text)
    text = re.sub(r"line \d+", "line <line>", text)
    return hashlib.sha256(text[:ERR_CAP].encode()).hexdigest()[:16]


def _retained_artifact_junit(
    gate: str, wt: str, artifact_dir: str | None
) -> tuple[Path | None, str]:
    if not artifact_dir:
        return None, "not_collected"
    worktree_root = ROOT / f"wt/{wt}/outputs" if wt else ROOT
    base = (worktree_root / artifact_dir).resolve()
    root = ROOT.resolve()
    if root != base and root not in base.parents:
        return None, "invalid"
    junit = base / f"{gate}.junit.xml"
    return (junit, "retained") if junit.exists() else (None, "missing")


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
        root = ET.parse(junit).getroot()
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


# ---------------------------------------------------------------- refresh


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


def _store_run(con, rel, wt, gate, kind, lines, rb, meta, boff, nbytes, complete) -> int:
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


def cmd_tests(con, args) -> str:
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
            f"{o.upper():7s} {n} ({s:.2f}s)" if s else f"{o.upper():7s} {n}" for n, o, s, _e in rows
        )
        or "no test rows (junit absent and no failures text-parsed)"
    )


def cmd_timing(con, args) -> str:
    rows = con.execute(
        """SELECT r.started_at, b.seconds, b.budget_s, b.status, r.wt
           FROM bench b JOIN runs r USING (run_id)
           WHERE b.bench = ? ORDER BY r.started_at DESC LIMIT ?""",
        [args.leg, args.last],
    ).fetchall()
    if not rows:
        rows = con.execute(
            """SELECT r.started_at, l.seconds, NULL, l.status, r.wt
               FROM legs l JOIN runs r USING (run_id)
               WHERE l.leg = ? ORDER BY r.started_at DESC LIMIT ?""",
            [args.leg, args.last],
        ).fetchall()
    if not rows:
        return f"no history for leg {args.leg!r}"
    out = [f"{args.leg} — last {len(rows)} runs (newest first)"]
    for started, secs, budget, status, wt in rows:
        b = f" / budget {budget}s" if budget else ""
        out.append(f"  {started}{'@' + wt if wt else '':14s} {secs:7.2f}s{b}  {status}")
    import statistics as _st

    meds = _st.median(r[1] for r in rows)
    out.append(
        f"  median {meds:.2f}s"
        + (
            f", latest {rows[0][1]:.2f}s ({(rows[0][1] / budget - 1) * 100:+.0f}% vs budget)"
            if budget
            else ""
        )
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


def _build_parser() -> argparse.ArgumentParser:
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
        ("tests", "per-test rows for a run"),
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
        if name == "tests":
            sp.add_argument("--run", type=int, required=True)
            sp.add_argument("--outcome", default="all")
            sp.add_argument("--json", action="store_true")
        if name == "timing":
            sp.add_argument("--leg", required=True)
            sp.add_argument("--last", type=int, default=20)
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
