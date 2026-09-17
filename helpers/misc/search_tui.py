#!/usr/bin/env python3
"""search_tui — one terminal front door over the repo's search surfaces.

Full-screen Textual TUI (htop/witr -i class) over the lanes the repo
already keeps fresh, plus the two sanctioned structural tools:

===== ======== =====================================================
lane  source   backend
===== ======== =====================================================
1     docs     helpers/misc/doc_query.py (FTS5 + embeddings, --json)
2     scripts  helpers/misc/script_query.py (--json; make/test/mojo too)
3     notes    note_search FTS5 in memory/research.db (bm25-ranked)
 4     code     ripwire --for= (verbs: callers:/impact:/grep:/recall:)
 5     literal  rg (gitignore-aware, no color, path:line:text rows)
 6     reports  outputs/*_report.md (verbs: qa:/advisory:/integration:/
              maint:/perf:/integrity:/verify:; empty query = per-report overview)
 ===== ======== =====================================================

Lanes 1-3 are the indexes ``make search-fresh`` maintains; 4-5 are the
stateless structure/literal tools from AGENTS.md (``--grep`` always
carries ``--grep-in=any``); 6 reads the append-only ``outputs/`` run
reports (only as fresh as the last gate run — the status bar says which
runs are shown). Nothing here builds a new index — a lane is
only as fresh as ``make search-fresh APPLY=1`` left it (the status bar
shows each index's age).

``d`` opens the read-only database screen: schema tree + SQL input +
results grid over ``memory/research.db`` (SQLite) and
``memory/data/sources.duckdb`` (DuckDB), one short-lived read-only
connection per query, 200-row cap.Reading: ``enter`` opens the hit — markdown via ``glow -p`` when glow
is installed, everything else via $VISUAL/$EDITOR/nvim/vim/less (line
aware). ``e`` forces the editor. ``y``/``Y`` copy path / path:line.

Usage::

    make search-tui                       # from the repo root
    python3 helpers/misc/search_tui.py    # same, direct
    python3 helpers/misc/search_tui.py -q "langgraph" --lane docs
    python3 helpers/misc/search_tui.py -q "callers: longest_chains"

The textual dependency is the optional ``tui`` extra
(``uv sync --extra tui``); without it this prints install help and
exits 2. Needs a TTY.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace as dc_replace
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
LANES: tuple[str, ...] = ("docs", "scripts", "notes", "code", "literal", "reports")
DEFAULT_LIMIT = 40
RG_ROW_CAP = 300
_SUB_TIMEOUT = 90  # ripwire walks the tree; doc/script CLIs embed queries.

# ---------------------------------------------------------------------------
# Hit model + lane adapters (terminal-free, unit-tested)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    """One result row, normalized across lanes."""

    path: str
    line: int | None
    title: str = ""
    section: str = ""
    snippet: str = ""
    score: float | None = None
    lane: str = ""
    kind: str = ""


def _strip_marks(s: str) -> str:
    return s.replace("<mark>", "").replace("</mark>", "")


def _int_or_none(v: int | str | None) -> int | None:
    """Coerce index/anchor columns (untyped SQLite cols yield str)."""
    if v is None:
        return None
    try:
        return int(v)
    except TypeError, ValueError:
        return None


def parse_doc_json(raw: str, limit: int) -> list[Hit]:
    """Normalize ``doc_query.py --json`` output into hits."""
    hits: list[Hit] = []
    for r in json.loads(raw).get("results", [])[:limit]:
        hits.append(
            Hit(
                path=r.get("path", ""),
                line=_int_or_none(r.get("anchor")),
                title=r.get("section_title") or r.get("title", ""),
                section=r.get("section", ""),
                snippet=_strip_marks(r.get("snippet", "")).strip(),
                score=r.get("similarity"),
                lane="docs",
                kind="doc",
            )
        )
    return hits


def parse_script_json(raw: str, limit: int) -> list[Hit]:
    """Normalize ``script_query.py --json`` output into hits.

    ``make`` rows have no file: path is the target name and the file is
    located at open time (see :func:`locate_make_target`).
    """
    hits: list[Hit] = []
    for r in json.loads(raw).get("results", [])[:limit]:
        kind = r.get("kind", "")
        path = r.get("path", "")
        hits.append(
            Hit(
                path=path,
                line=None,
                title=r.get("title", ""),
                section=f"{kind}/{r.get('area', '')}".strip("/"),
                snippet=r.get("purpose") or _strip_marks(r.get("snippet", "")),
                score=r.get("similarity"),
                lane="scripts",
                kind=kind or "script",
            )
        )
    return hits


def parse_rg_lines(raw: str) -> list[Hit]:
    """Parse ``rg -n --no-heading`` output lines into hits."""
    hits: list[Hit] = []
    for ln in raw.splitlines()[:RG_ROW_CAP]:
        m = re.match(r"^(\S+?):(\d+):(.*)$", ln)
        if not m:
            continue
        hits.append(
            Hit(
                path=m.group(1).removeprefix("./"),
                line=int(m.group(2)),
                snippet=m.group(3).strip(),
                lane="literal",
                kind="rg",
            )
        )
    return hits


_RW_D_ROW = re.compile(r'<d\s+l="(\d+)"\s+n="([^"]+)"\s+p="([^"]+)"([^>]*)>')
_RW_RANK = re.compile(r'\br="(\d+)"')
_RW_S_ROW = re.compile(r'<s\s+t="([^"]*)"\s+n="([^"]+)"\s+p="([^":]+):(\d+)"')
_RW_RECALL = re.compile(r"^━━ (\S+)\s+\(relevance ([\d.]+)\)", re.M)
_RW_GREP_HIT = re.compile(r'<hit l="(\d+)"[^>]*><!\[CDATA\[(.*?)\]\]></hit>')
_RW_GREP_FILE = re.compile(r'<f p="([^"]+)">')


def parse_riprewire(raw: str) -> list[Hit]:
    """Parse ripwire stdout into hits.

    Handles the ``<d l= n= p= r=>`` ranked-symbol rows (--for/--callers/
    --impact/--grep) and the ``━━ path (relevance x)`` doc blocks
    (--recall).
    """
    hits: list[Hit] = []
    for m in _RW_D_ROW.finditer(raw):
        attrs = m.group(4)
        tail = raw[m.end() : raw.find("</d>", m.end())]
        tail = re.sub(r"<[^>]+>", "", tail)
        rank = _RW_RANK.search(attrs)
        hits.append(
            Hit(
                path=m.group(3),
                line=int(m.group(1)),
                title=m.group(2),
                snippet=html.unescape(tail).strip()[:200],
                score=float(rank.group(1)) if rank else None,
                lane="code",
                kind="symbol",
            )
        )
    if not hits:
        for m in _RW_S_ROW.finditer(raw):
            hits.append(
                Hit(
                    path=m.group(3),
                    line=int(m.group(4)),
                    title=m.group(2),
                    section=m.group(1),
                    lane="code",
                    kind="symbol",
                )
            )
    if not hits:
        pieces: list[tuple[str, int, str]] = []
        for m in _RW_GREP_FILE.finditer(raw):
            end = _RW_GREP_FILE.search(raw, m.end())
            span = raw[m.end() : end.start() if end else len(raw)]
            for h in _RW_GREP_HIT.finditer(span):
                pieces.append((m.group(1), int(h.group(1)), h.group(2)))
        for path, line, text in pieces[:200]:
            hits.append(
                Hit(path=path, line=line, snippet=text.strip()[:200], lane="code", kind="grep")
            )
    if not hits:
        for m in _RW_RECALL.finditer(raw):
            hits.append(
                Hit(
                    path=m.group(1),
                    line=None,
                    snippet=m.group(0),
                    score=float(m.group(2)),
                    lane="code",
                    kind="recall",
                )
            )
    return hits


_RW_VERBS = ("callers", "impact", "grep", "recall")


def ripwire_argv(query: str) -> list[str]:
    """Build the ripwire argv for a code-lane query.

    ``callers:SYM`` / ``impact:SYM`` / ``grep:STR`` / ``recall:question``
    select the verb; anything else runs ``--for=<query>``. grep always
    carries ``--grep-in=any`` (AGENTS.md rule).
    """
    low = query.lower()
    for verb in _RW_VERBS:
        pref = verb + ":"
        if low.startswith(pref) and len(query) > len(pref):
            arg = query[len(pref) :].strip()
            if verb == "grep":
                return ["ripwire", ".", f"--grep={arg}", "--grep-in=any", "--legend=compact"]
            if verb == "recall":
                return ["ripwire", ".", f"--recall={arg}"]
            return ["ripwire", ".", f"--{verb}={arg}"]
    return ["ripwire", ".", f"--for={query}"]


def fts_safe(query: str) -> str:
    """Make a raw query safe for an FTS5 MATCH, degrading to OR-tokens."""
    from helpers.core.db import connect as _db_connect

    try:
        probe = _db_connect(":memory:", wal=False)
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        probe.execute("SELECT * FROM t WHERE t MATCH ?", (query,))
        probe.close()
        return query
    except sqlite3.OperationalError:
        toks = re.findall(r"[\w']+", query)
        if not toks:
            return '""'
        return " OR ".join(f'"{t}"' for t in toks)


def notes_query(db_path: Path, query: str, limit: int) -> list[Hit]:
    """Keyword search over note_search (bm25-ranked, porter tokens)."""
    from helpers.core.db import connect as _db_connect

    conn = _db_connect(db_path, read_only=True, wal=False)
    try:
        rows = conn.execute(
            "SELECT file_path, title, section_title, anchor,"
            " snippet(note_search, 4, '»', '«', '…', 16),"
            " bm25(note_search)"
            " FROM note_search WHERE note_search MATCH ?"
            " ORDER BY bm25(note_search) LIMIT ?",
            (fts_safe(query), limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        Hit(
            path=r[0].removeprefix("/"),
            line=_int_or_none(r[3]),
            title=r[1] or "",
            section=r[2] or "",
            snippet=(r[4] or "").strip(),
            score=-r[5] if r[5] is not None else None,
            lane="notes",
            kind="note",
        )
        for r in rows
    ]


def _run(argv: list[str]) -> str:
    """Run a lane backend, returning stdout ('' on any failure)."""
    try:
        r = subprocess.run(  # noqa: S603 — fixed argv, no shell, repo-local backends
            argv, capture_output=True, text=True, timeout=_SUB_TIMEOUT, cwd=REPO_ROOT
        )
    except OSError, subprocess.TimeoutExpired:
        return ""
    return r.stdout if r.returncode == 0 else ""


def _run_docs(q: str, limit: int, mode: str = "hybrid") -> tuple[list[Hit], str]:
    # hybrid blends the embedding cosine: sections that never contain the
    # query word can rank (semantic recall). --bm25 = lexical leg only —
    # every hit contains the word (operator toggle, key m).
    argv = [sys.executable, "helpers/misc/doc_query.py", q, "--limit", str(limit)]
    if mode == "bm25":
        argv.append("--bm25")
    argv.append("--json")
    raw = _run(argv)
    hits = parse_doc_json(raw, limit) if raw.strip().startswith("{") else []
    if not hits and not raw.strip():
        return [], "doc_search sidecar missing/stale — refresh from the index monitor (i)"
    return hits, f"{len(hits)} hits · doc_search {mode}"


def _run_scripts(q: str, limit: int, mode: str = "hybrid") -> tuple[list[Hit], str]:
    argv = [sys.executable, "helpers/misc/script_query.py", q, "--limit", str(limit)]
    if mode == "bm25":
        argv.append("--bm25")
    argv.append("--json")
    raw = _run(argv)
    hits = parse_script_json(raw, limit) if raw.strip().startswith("{") else []
    if not hits and not raw.strip():
        return [], "script_search sidecar missing/stale — refresh from the index monitor (i)"
    return hits, f"{len(hits)} hits · script_search {mode}"


def _run_notes(q: str, limit: int, mode: str = "hybrid") -> tuple[list[Hit], str]:
    db = REPO_ROOT / "memory" / "research.db"
    if not db.exists():
        return [], "memory/research.db missing"
    try:
        hits = notes_query(db, q, limit)
    except sqlite3.Error as e:  # pragma: no cover - defensive
        return [], f"note_search error: {e}"
    return hits, f"{len(hits)} hits · note_search bm25"


def _run_code(q: str, limit: int, mode: str = "hybrid") -> tuple[list[Hit], str]:
    if shutil.which("ripwire") is None:
        return [], "ripwire missing on PATH"
    raw = _run(ripwire_argv(q))
    hits = parse_riprewire(raw)
    if hits:
        # --for bundles rank doc sections alongside symbols; a task-y query
        # can fill the table with prose. Code first, doc mentions appended.
        is_verb = bool(re.match(r"^(callers|impact|grep|recall):", q.strip(), re.I))
        if not is_verb:
            code_rows = [h for h in hits if not h.path.endswith(".md")]
            doc_rows = [dc_replace(h, kind="doc mention") for h in hits if h.path.endswith(".md")]
            if code_rows:
                return code_rows + doc_rows, (
                    f"{len(code_rows)} code · {len(doc_rows)} doc mentions · ripwire"
                )
        return hits, f"{len(hits)} hits · ripwire"
    # ranked rows absent: a no-symbol bundle still carries a file-grain tail
    tails: list[Hit] = []
    for m in re.finditer(r'<t p="([^"]+)"', raw):
        tails.append(Hit(path=m.group(1), line=None, title="(file)", lane="code", kind="tail"))
    if tails:
        return tails[:24], f"{len(tails)} file hits (no symbol rows) · ripwire tail"
    return [], "no ripwire rows for that query — try callers:/impact:/grep:/recall:"


def _run_literal(q: str, limit: int, mode: str = "hybrid") -> tuple[list[Hit], str]:
    if shutil.which("rg") is None:
        return [], "rg missing on PATH"
    # regex first (rg is a regex tool); -F fallback only when the pattern
    # is not valid regex — parens/dots queries must not die (2026-09-16)
    for flag, label in (("--", "rg regex"), ("-F", "rg fixed-string")):
        argv = ["rg", "-n", "-S", "--no-heading", "--color=never"]
        if flag != "--":
            argv.append(flag)
        try:
            r = subprocess.run(  # noqa: S603 — fixed argv, no shell
                [*argv, q, "."],
                capture_output=True,
                text=True,
                timeout=_SUB_TIMEOUT,
                cwd=REPO_ROOT,
            )
        except OSError, subprocess.TimeoutExpired:
            return [], "rg failed to run"
        if r.returncode == 1:  # 1 = no matches (rg contract), not an error
            return [], "no matches"
        if r.returncode == 2 and flag == "--":
            continue  # invalid regex — retry as fixed-string
        if r.returncode != 0:
            return [], f"rg error: {(r.stderr or '').strip().splitlines()[-1][:80]}"
        hits = parse_rg_lines(r.stdout)
        return hits, f"{len(hits)} hits · {label} (capped {RG_ROW_CAP})"
    return [], "no matches"


_LaneRunner = Callable[[str, int, str], tuple[list[Hit], str]]

_LANE_RUNNERS: dict[str, _LaneRunner] = {
    "docs": _run_docs,
    "scripts": _run_scripts,
    "notes": _run_notes,
    "code": _run_code,
    "literal": _run_literal,
    # reports adapters live below (beside the other parse_*); resolve late.
    "reports": lambda q, limit, mode="hybrid": _run_reports(q, limit, mode),  # noqa: E731
}


def run_lane(lane: str, query: str, limit: int, mode: str = "hybrid") -> tuple[list[Hit], str]:
    """Execute one lane. Returns (hits, status message for the bar).

    ``mode`` applies to the two hybrid backends: "hybrid" (semantic +
    lexical blend, default) or "bm25" (every hit contains the word).
    The reports lane accepts an empty query (per-report overview).
    """
    q = query.strip()
    if not q and lane != "reports":
        return [], "empty query"
    runner = _LANE_RUNNERS.get(lane)
    if runner is None:
        return [], f"unknown lane {lane!r}"
    return runner(q, limit, mode)


def call_chain(symbol: str) -> dict:
    """1-hop call chain for a symbol: callers (who calls it) + callees."""
    out: dict = {"of": symbol, "callers": [], "callees": []}
    for key, flag in (("callers", "--callers="), ("callees", "--callees=")):
        raw = _run(["ripwire", ".", f"{flag}{symbol}"])
        out[key] = parse_riprewire(raw)
    return out


# ---------------------------------------------------------------------------
# Open/copy helpers
# ---------------------------------------------------------------------------


INDEXES: tuple[tuple[str, str, str], ...] = (
    ("docs", "memory/doc_search.db", "helpers/maintenance/rebuild_doc_search.py"),
    ("scripts", "memory/script_search.db", "helpers/maintenance/rebuild_script_search.py"),
    ("notes", "memory/research.db", "helpers/maintenance/rebuild_note_search.py"),
)

# ---------------------------------------------------------------------------
# Report adapters — parse the seven repo report files (all markdown)
# into structured data for the TUI report lane.
# Append-across-runs files (qa/advisory/integration/maint/perf) return
# RunBlock list in file order (TUI defaults to last = most recent).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunStep:
    label: str
    seconds: float | None
    status: str  # "✓ OK" | "✗ FAIL" | "⌀ SKIP"


@dataclass(frozen=True)
class RunBlock:
    gate: str  # e.g. "qa", "advisory", "integration", "perf"
    timestamp: str  # YYYY-MM-DD HH:MM:SS
    jobs: int | None
    steps: tuple[RunStep, ...]
    summary: str  # e.g. "8/9 passed  ·  gate FAIL"


@dataclass(frozen=True)
class CheckRow:
    name: str  # check label
    severity: str  # "ERROR" | "WARNING" | "OK"
    summary: str  # e.g. "total=1020 errors=0 warnings=3"
    detail: tuple[str, ...]  # detail lines under this header


_GATE_HEADER_RE = re.compile(r"^#\s+make\s+(\S+)\s+—\s+(gate|maint)\s+report\s*$")
_GATE_META_RE = re.compile(
    r"^\*\*Generated:\*\*\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"
    r"\s+·\s+\*\*Python:\*\*\s+(\S+)"
    r"(?:\s+jobs=(\d+))?"
)
_GATE_TABLE_SEP_RE = re.compile(r"^\|[\s\-|]+\|$")
_GATE_TABLE_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$")
_PERF_TABLE_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$")


_PERF_HEADER_RE = re.compile(r"^#\s+make\s+(\S+)\s+—\s+benchmark\s+report\s*$")
_PERF_META_RE = re.compile(r"^\*\*Generated:\*\*\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})")
_INTEGRITY_SECTION_RE = re.compile(
    r"^##\s+(.+?)\s+\((ERROR|WARNING|ADVISORY)[^\)]*\)",
    re.IGNORECASE,
)


def _parse_gate_table_block(lines: list[str]) -> tuple[tuple[RunStep, ...], str]:
    """Parse markdown table rows into (steps, summary)."""
    steps: list[RunStep] = []
    summary = ""
    in_table = False
    for ln in lines:
        if _GATE_TABLE_SEP_RE.match(ln):
            in_table = True
            continue
        if in_table:
            m = _GATE_TABLE_ROW_RE.match(ln)
            if m:
                label = m.group(1).strip()
                secs_s = m.group(2).strip()
                status = m.group(3).strip()
                if label.startswith("**") and label.endswith("**"):
                    summary = f"{label.strip('*')}  ·  {status.strip('*')}"
                    continue
                seconds: float | None = None
                if secs_s != "—":
                    try:
                        seconds = float(secs_s)
                    except ValueError:
                        pass
                steps.append(RunStep(label, seconds, status))
            elif not ln.strip():
                break
    return tuple(steps), summary


def parse_gate_report(path: Path) -> list[RunBlock]:
    """Parse a gate/maint report (qa/advisory/integration/maint) into RunBlocks.

    Append-across-runs: returns all blocks in file order (most recent last).
    Splits on ``#`` headers; a headerless leading chunk (stale content)
    is skipped.
    """
    blocks: list[RunBlock] = []
    current: list[str] = []
    gate = ""
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        m = _GATE_HEADER_RE.match(ln)
        if m and current and gate:
            blocks.append(_build_gate_block(gate, current))
            current = []
        if m:
            gate = m.group(1)
        current.append(ln)
    if current and gate:
        blocks.append(_build_gate_block(gate, current))
    return blocks


def _build_gate_block(gate: str, lines: list[str]) -> RunBlock:
    timestamp = ""
    jobs: int | None = None
    table_lines: list[str] = []
    for ln in lines:
        mm = _GATE_META_RE.match(ln)
        if mm:
            timestamp = mm.group(1)
            jobs = int(mm.group(3)) if mm.group(3) else None
            continue
        table_lines.append(ln)
    steps, summary = _parse_gate_table_block(table_lines)
    if not summary:
        summary = f"{len([s for s in steps if s.status != 'SKIP'])}/{len(steps)} passed"
    return RunBlock(gate, timestamp, jobs, steps, summary)


def parse_perf_report(path: Path) -> list[RunBlock]:
    """Parse a perf report into RunBlock list (append-across-runs)."""
    blocks: list[RunBlock] = []
    current: list[str] = []
    gate = ""
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        m = _PERF_HEADER_RE.match(ln)
        if m and current and gate:
            blocks.append(_build_perf_block(gate, current))
            current = []
        if m:
            gate = m.group(1)
        current.append(ln)
    if current and gate:
        blocks.append(_build_perf_block(gate, current))
    return blocks


def _build_perf_block(gate: str, lines: list[str]) -> RunBlock:
    timestamp = ""
    steps: list[RunStep] = []
    for ln in lines:
        mm = _PERF_META_RE.match(ln)
        if mm:
            timestamp = mm.group(1)
            continue
        m = _PERF_TABLE_ROW_RE.match(ln)
        if m:
            label = m.group(1).strip()
            secs_s = m.group(2).strip()
            status = m.group(4).strip()
            if label in ("Benchmark", "---", "|", "Time (s)", "Budget", "Status", "passed"):
                continue
            seconds: float | None = None
            if secs_s and secs_s != "—":
                try:
                    seconds = float(secs_s)
                except ValueError:
                    pass
            if seconds is None:
                continue
            steps.append(RunStep(label, seconds, status))
    summary = f"{len(steps)} benchmarks" if steps else "0 benchmarks"
    return RunBlock(f"perf:{gate}", timestamp, None, tuple(steps), summary)


def parse_integrity_report(path: Path) -> list[CheckRow]:
    """Parse a database integrity report into CheckRow list.

    Multi-run file: only the latest run's sections are returned (runs are
    ``#``-header delimited; a headerless leading chunk is stale content).
    Severity from heading parens (not _CHECKS).
    Section headers: `## LABEL (SEVERITY ...)` — severity is the first
    word in parens (ERROR, WARNING, advisory, gate-failing).
    """
    rows: list[CheckRow] = []
    current_name = ""
    current_severity = "OK"
    current_summary = ""
    current_detail: list[str] = []
    has_summary = False

    def _flush() -> None:
        if current_name:
            rows.append(
                CheckRow(
                    current_name,
                    current_severity,
                    current_summary,
                    tuple(current_detail),
                )
            )

    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        if ln.startswith("# ") and not ln.startswith("##"):
            # New run block: drop the previous run's sections, keep latest only.
            rows.clear()
            current_name = ""
            current_summary = ""
            current_detail = []
            has_summary = False
            continue
        m = _INTEGRITY_SECTION_RE.match(ln)
        if m:
            _flush()
            current_name = m.group(1).strip()
            current_severity = m.group(2).upper()
            if current_severity == "ADVISORY":
                current_severity = "WARNING"
            current_summary = ""
            current_detail = []
            has_summary = False
            continue
        if current_name and ln.strip() and not ln.startswith("#"):
            stripped = ln.strip()
            if not has_summary:
                current_summary = stripped
                has_summary = True
            else:
                current_detail.append(stripped)
    _flush()
    return rows


def parse_verify_report(path: Path) -> dict:
    """Parse a verify_notes report into a summary dict.

    Multi-run file: metric rows and the verdict overwrite as scanned, so
    the returned dict describes the latest run.
    """
    text = Path(path).read_text(encoding="utf-8")
    result: dict = {"errors": 0, "warnings": 0, "total_files": 0, "verdict": ""}
    for ln in text.splitlines():
        m = re.match(
            r"\|\s*(Total Files Checked|Errors|Warnings)\s*\|\s*(\d+)\s*\|",
            ln,
        )
        if m:
            raw = m.group(1).lower()
            key = "total_files" if raw == "total files checked" else raw.replace(" ", "_")
            result[key] = int(m.group(2))
        vm = re.match(r"^(✅|⚠️)\s*(.+)", ln)
        if vm:
            result["verdict"] = vm.group(2)
    return result


def parse_report_summary(path: Path) -> str:
    """One-line summary for the status bar / sidebar."""
    name = Path(path).name
    if name.startswith(("qa", "advisory", "integration", "maint")):
        blocks = parse_gate_report(path)
        if blocks:
            return blocks[-1].summary
        return "no runs"
    if name.startswith("perf"):
        blocks = parse_perf_report(path)
        if blocks:
            return blocks[-1].summary
        return "no runs"
    if name.startswith("database_integrity"):
        rows = parse_integrity_report(path)
        err_count = 0
        warn_count = 0
        for r in rows:
            em = re.search(r"errors=(\d+)", r.summary)
            if em and int(em.group(1)) > 0:
                err_count += 1
            wm = re.search(r"warnings=(\d+)", r.summary)
            if wm and int(wm.group(1)) > 0:
                warn_count += 1
        return f"{len(rows)} checks  ·  {err_count} errors  ·  {warn_count} warnings"
    if name.startswith("verify_notes"):
        d = parse_verify_report(path)
        return (
            f"{d.get('total_files', 0)} files  ·  "
            f"{d.get('errors', 0)} errors  ·  "
            f"{d.get('warnings', 0)} warnings"
        )
    return name


# ---------------------------------------------------------------------------
# Themes — palette data (terminal-free) + persistence
# ---------------------------------------------------------------------------
# Applied by the app via textual's built-in theme registry
# (register_theme + self.theme) for widget chrome, plus a rich Theme push
# for the markdown preview pane. No CSS strings change hands — the app's
# single CSS block resolves $panel/$accent/... from the active theme.

THEME_ORDER: tuple[str, ...] = ("github-dark", "github-light", "solarized-dark", "high-contrast")

THEMES: dict[str, dict[str, Any]] = {
    "github-dark": {
        "description": "GitHub dark (default) — blue headings, dark panels",
        "variables": {
            "primary": "#58a6ff",
            "secondary": "#56d364",
            "accent": "#d29922",
            "foreground": "#c9d1d9",
            "background": "#0d1117",
            "surface": "#161b22",
            "panel": "#21262d",
            "error": "#f85149",
            "warning": "#d29922",
            "success": "#56d364",
            "dark": True,
        },
        "rich": {
            "markdown.h1": "bold #79c0ff",
            "markdown.h2": "bold #56d364",
            "markdown.h3": "bold #d2a8ff",
            "markdown.h4": "bold #c9d1d9",
            "markdown.h5": "bold #c9d1d9",
            "markdown.h6": "bold #8b949e",
            "markdown.link": "#58a6ff underline",
            "markdown.code": "#a5d6ff on #1b1f24",
            "markdown.item.number": "bold #56d364",
            "markdown.item.bullet": "bold #56d364",
            "markdown.quote": "italic #8b949e",
            "markdown.quote_barrier": "#30363d",
        },
    },
    "github-light": {
        "description": "GitHub light — white panels, blue headings",
        "variables": {
            "primary": "#0969da",
            "secondary": "#1a7f37",
            "accent": "#9a6700",
            "foreground": "#24292f",
            "background": "#ffffff",
            "surface": "#f6f8fa",
            "panel": "#eaeef2",
            "error": "#cf222e",
            "warning": "#9a6700",
            "success": "#1a7f37",
            "dark": False,
        },
        "rich": {
            "markdown.h1": "bold #24292f",
            "markdown.h2": "bold #0969da",
            "markdown.h3": "bold #8250df",
            "markdown.h4": "bold #24292f",
            "markdown.h5": "bold #24292f",
            "markdown.h6": "bold #57606a",
            "markdown.link": "#0969da underline",
            "markdown.code": "#24292f on #ddf4ff",
            "markdown.item.number": "bold #1a7f37",
            "markdown.item.bullet": "bold #1a7f37",
            "markdown.quote": "italic #57606a",
            "markdown.quote_barrier": "#d0d7de",
        },
    },
    "solarized-dark": {
        "description": "Solarized dark — desaturated base, amber accents",
        "variables": {
            "primary": "#268bd2",
            "secondary": "#859900",
            "accent": "#b58900",
            "foreground": "#839496",
            "background": "#002b36",
            "surface": "#073642",
            "panel": "#073642",
            "error": "#dc322f",
            "warning": "#b58900",
            "success": "#859900",
            "dark": True,
        },
        "rich": {
            "markdown.h1": "bold #268bd2",
            "markdown.h2": "bold #859900",
            "markdown.h3": "bold #b58900",
            "markdown.h4": "bold #839496",
            "markdown.h5": "bold #839496",
            "markdown.h6": "bold #586e75",
            "markdown.link": "#268bd2 underline",
            "markdown.code": "#93a1a1 on #073642",
            "markdown.item.number": "bold #859900",
            "markdown.item.bullet": "bold #859900",
            "markdown.quote": "italic #586e75",
            "markdown.quote_barrier": "#073642",
        },
    },
    "high-contrast": {
        "description": "High contrast — black/white/yellow (accessibility)",
        "variables": {
            "primary": "#ffff00",
            "secondary": "#00ffff",
            "accent": "#ffff00",
            "foreground": "#ffffff",
            "background": "#000000",
            "surface": "#000000",
            "panel": "#1a1a1a",
            "error": "#ff0000",
            "warning": "#ffff00",
            "success": "#00ff00",
            "dark": True,
        },
        "rich": {
            "markdown.h1": "bold #ffff00",
            "markdown.h2": "bold #ffffff",
            "markdown.h3": "bold #00ffff",
            "markdown.h4": "bold #ffffff",
            "markdown.h5": "bold #ffffff",
            "markdown.h6": "bold #ffffff",
            "markdown.link": "bold #00ffff underline",
            "markdown.code": "bold #ffffff on #000000",
            "markdown.item.number": "bold #ffff00",
            "markdown.item.bullet": "bold #ffff00",
            "markdown.quote": "#ffffff",
            "markdown.quote_barrier": "#ffffff",
        },
    },
}

DEFAULT_THEME = "github-dark"


def theme_file() -> Path:
    """Persistence path: ~/.config/search_tui/theme, else repo memory/."""
    home_cfg = Path.home() / ".config" / "search_tui" / "theme"
    try:
        home_cfg.parent.mkdir(parents=True, exist_ok=True)
        return home_cfg
    except OSError:
        return REPO_ROOT / "memory" / "search_tui_theme"


def load_theme_name() -> str:
    """Saved theme, or DEFAULT_THEME when absent/unreadable/unknown."""
    try:
        name = theme_file().read_text(encoding="utf-8").strip().split()[0]
    except OSError, IndexError:
        return DEFAULT_THEME
    return name if name in THEMES else DEFAULT_THEME


def save_theme_name(name: str) -> bool:
    """Persist a theme; False when unknown (nothing written)."""
    if name not in THEMES:
        return False
    try:
        theme_file().write_text(name + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def next_theme(name: str) -> str:
    """Cycle order; unknown names restart at the default."""
    if name not in THEME_ORDER:
        return THEME_ORDER[0]
    return THEME_ORDER[(THEME_ORDER.index(name) + 1) % len(THEME_ORDER)]


# ---------------------------------------------------------------------------
# Reports lane — queryable view over outputs/*_report.md
# ---------------------------------------------------------------------------

# (verb, filename, kind). kind selects the parser: gate covers the
# run_gate_report family (qa/advisory/integration) plus maint.
_REPORTS: tuple[tuple[str, str, str], ...] = (
    ("qa", "outputs/qa_report.md", "gate"),
    ("advisory", "outputs/advisory_report.md", "gate"),
    ("integration", "outputs/integration_report.md", "gate"),
    ("maint", "outputs/maint_report.md", "gate"),
    ("perf", "outputs/perf_report.md", "perf"),
    ("integrity", "outputs/database_integrity_report.md", "integrity"),
    ("verify", "outputs/verify_notes_report.md", "verify"),
)

_VERIFY_BUCKET_RE = re.compile(r"^###\s+(.+?)\s+\((\d+)\)\s*$")
_VERIFY_ITEM_RE = re.compile(r"^-\s+(.+?):\s+(.+)$")


def _split_report_verb(query: str) -> tuple[str | None, str]:
    """Split ``verb: rest`` (case-insensitive); unknown verbs are text."""
    m = re.match(r"^([A-Za-z_]+):\s*(.*)$", query.strip())
    if m and m.group(1).lower() in {name for name, _, _ in _REPORTS}:
        return m.group(1).lower(), m.group(2).strip()
    return None, query.strip()


def _locate_line(lines: list[str], start: int, end: int, needle: str) -> int | None:
    """1-based line number of the first line in [start, end) holding needle."""
    for i in range(start, min(end, len(lines))):
        if needle in lines[i]:
            return i + 1
    return None


def _report_block_starts(lines: list[str]) -> list[int]:
    """0-based offsets where a new run block starts (``# `` H1 lines)."""
    return [i for i, ln in enumerate(lines) if ln.startswith("# ") and not ln.startswith("##")]


def report_run_spans(path: Path) -> list[tuple[int, int]]:
    """(start, end) 1-based inclusive line spans per ``#``-header run block.

    Headerless leading content is not a run and never spans. Used by the
    report screen to slice detail views out of the file.
    """
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    starts = _report_block_starts(lines)
    return [
        (s + 1, (starts[i + 1] if i + 1 < len(starts) else len(lines)))
        for i, s in enumerate(starts)
    ]


def report_rerun_argv(name: str) -> list[str] | None:
    """Regenerate argv for a report (make target where one exists)."""
    make = shutil.which("make") or "make"
    argv: dict[str, list[str]] = {
        "qa": [make, "qa"],
        "advisory": [make, "advisory"],
        "integration": [make, "integration"],
        "maint": [make, "maint"],
        "perf": [make, "perf"],
        "integrity": [sys.executable, "helpers/misc/database_integrity_check.py"],
        "verify": [sys.executable, "helpers/validators/verify_notes.py"],
    }
    return argv.get(name)


REPORT_NAMES: tuple[str, ...] = tuple(n for n, _, _ in _REPORTS)


def report_verb_for_path(rel: str) -> str | None:
    """Registry verb for a report path (``outputs/qa_report.md`` → ``qa``)."""
    for n, r, _ in _REPORTS:
        if r == rel or rel.endswith("/" + r) or Path(rel).name == Path(r).name:
            return n
    return None


def _run_gate_hits(
    name: str, rel: str, kind: str, lines: list[str], text: str, limit: int, hits: list[Hit]
) -> str:
    """Append gate/perf hits: latest run's rows, or all-run matches."""
    path = REPO_ROOT / rel
    blocks = parse_gate_report(path) if kind == "gate" else parse_perf_report(path)
    if not blocks:
        return ""
    starts = _report_block_starts(lines)
    # block i spans [starts[i], starts[i+1]); headerless preamble is chunk -1.
    match_all = bool(text)
    low = text.lower()
    for bi, b in enumerate(blocks):
        if not match_all and bi != len(blocks) - 1:
            continue
        lo = starts[bi] if bi < len(starts) else 0
        hi = starts[bi + 1] if bi + 1 < len(starts) else len(lines)
        for s in b.steps:
            if match_all and low not in f"{s.label} {s.status} {b.summary} {b.timestamp}".lower():
                continue
            secs = "" if s.seconds is None else f"{s.seconds:.2f}s"
            hits.append(
                Hit(
                    path=rel,
                    line=_locate_line(lines, lo, hi, s.label),
                    title=s.label,
                    section=f"{name} · {b.timestamp or 'unknown run'}",
                    snippet=f"{secs} · {s.status} · run: {b.summary}".strip(" ·"),
                    lane="reports",
                    kind="step",
                )
            )
            if len(hits) >= limit:
                return f"{name} {b.timestamp}"
    return f"{name} {blocks[-1].timestamp}" if not match_all else f"{name} all runs"


def _run_integrity_hits(
    name: str, rel: str, lines: list[str], text: str, limit: int, hits: list[Hit]
) -> str:
    rows = parse_integrity_report(REPO_ROOT / rel)
    low = text.lower()
    for r in rows:
        if text and low not in f"{r.name} {r.severity} {r.summary}".lower():
            continue
        detail = f" — {r.detail[0][:120]}" if r.detail else ""
        hits.append(
            Hit(
                path=rel,
                line=_locate_line(lines, 0, len(lines), f"## {r.name}"),
                title=r.name,
                section=f"integrity · {r.severity}",
                snippet=f"{r.summary}{detail}",
                lane="reports",
                kind="check",
            )
        )
        if len(hits) >= limit:
            break
    return f"{name} {len(rows)} checks"


def _run_verify_hits(
    name: str, rel: str, lines: list[str], text: str, limit: int, hits: list[Hit]
) -> str:
    """Issue rows from the latest run chunk (``### bucket`` + ``-`` items)."""
    starts = _report_block_starts(lines)
    lo = starts[-1] if starts else 0
    low = text.lower()
    bucket = ""
    in_issues = False
    for i in range(lo, len(lines)):
        ln = lines[i]
        if ln.startswith("## "):
            in_issues = "ERROR" in ln.upper() or "WARNING" in ln.upper()
            continue
        bm = _VERIFY_BUCKET_RE.match(ln)
        if bm and in_issues:
            bucket = bm.group(1)
            continue
        im = _VERIFY_ITEM_RE.match(ln)
        if im and in_issues and bucket:
            detail = f"{im.group(1)}: {im.group(2)}"
            if text and low not in f"{bucket} {detail}".lower():
                continue
            hits.append(
                Hit(
                    path=rel,
                    line=i + 1,
                    title=bucket,
                    section="verify · issue",
                    snippet=detail[:200],
                    lane="reports",
                    kind="issue",
                )
            )
            if len(hits) >= limit:
                break
    d = parse_verify_report(REPO_ROOT / rel)
    return (
        f"{name} {d.get('total_files', 0)} files · "
        f"{d.get('errors', 0)} errors · {d.get('warnings', 0)} warnings"
    )


def _run_reports(q: str, limit: int, mode: str = "hybrid") -> tuple[list[Hit], str]:
    """Reports lane: verbs select a file, text filters rows, empty = overview."""
    verb, text = _split_report_verb(q)
    names = [verb] if verb else [name for name, _, _ in _REPORTS]
    hits: list[Hit] = []
    shown: list[str] = []
    missing: list[str] = []
    for name in names:
        rel = next(rel for n, rel, _ in _REPORTS if n == name)
        kind = next(k for n, _, k in _REPORTS if n == name)
        p = REPO_ROOT / rel
        if not p.exists():
            missing.append(name)
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if verb is None and not text:
            # Comprehensive view: one overview row per report.
            summary = parse_report_summary(p)
            starts = _report_block_starts(lines)
            hits.append(
                Hit(
                    path=rel,
                    line=(starts[-1] + 1) if starts else 1,
                    title=f"{name} — {summary}",
                    section="latest run",
                    snippet=summary,
                    lane="reports",
                    kind="overview",
                )
            )
            shown.append(name)
            continue
        if kind in ("gate", "perf"):
            shown.append(_run_gate_hits(name, rel, kind, lines, text, limit, hits))
        elif kind == "integrity":
            shown.append(_run_integrity_hits(name, rel, lines, text, limit, hits))
        else:
            shown.append(_run_verify_hits(name, rel, lines, text, limit, hits))
        if len(hits) >= limit:
            hits = hits[:limit]
            break
    shown_s = ", ".join(s for s in shown if s)
    missing_s = f" · missing: {','.join(missing)}" if missing else ""
    if not hits:
        return [], f"no report rows for {q!r} ({shown_s}){missing_s}"
    return hits, f"{len(hits)} rows · {shown_s}{missing_s}"


# ---------------------------------------------------------------- db screen

DB_STORES: dict[str, dict[str, object]] = {
    # Short-lived read-only connections per query (open → run → close),
    # so no lock is ever held across the TUI session — the same posture
    # as the notes lane. Writes are rejected by the engines, never by
    # parsing: SQLite via URI mode=ro, DuckDB via read_only=True.
    "research": {
        "label": "research.db · SQLite",
        "engine": "sqlite",
        "rel": "memory/research.db",
    },
    "sources": {
        "label": "sources.duckdb · DuckDB",
        "engine": "duckdb",
        "rel": "memory/data/sources.duckdb",
    },
}
DB_ROW_CAP = 200
DB_TIMEOUT_MS = 2000
DB_SQL_KEYWORDS = (
    "SELECT",
    "FROM",
    "WHERE",
    "ORDER BY",
    "GROUP BY",
    "LIMIT",
    "WITH",
    "JOIN",
    "LEFT JOIN",
    "ON",
    "AND",
    "OR",
    "NOT",
    "AS",
    "DISTINCT",
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "PRAGMA",
    "EXPLAIN",
    "MATCH",
)


@dataclass
class DbColumn:
    """One column of a table/view (name + declared type, '' if unknown)."""

    name: str
    ctype: str = ""


@dataclass
class DbTable:
    """One relation: name, kind ('table'/'view'), ordered columns."""

    name: str
    kind: str
    columns: list[DbColumn]


@dataclass
class DbResult:
    """Outcome of one read-only query — errors are data, never raised."""

    columns: list[str]
    rows: list[tuple[object, ...]]
    truncated: bool
    elapsed_ms: float
    error: str | None = None


def db_store_path(store: str, root: Path | None = None) -> Path:
    """Resolve a store id to its DB file (ValueError on unknown id)."""
    try:
        rel = str(DB_STORES[store]["rel"])
    except KeyError:
        known = ", ".join(sorted(DB_STORES))
        raise ValueError(f"unknown db store {store!r} (known: {known})") from None
    return (REPO_ROOT if root is None else root) / rel


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def db_preview_sql(table: str, column: str | None = None, limit: int = DB_ROW_CAP) -> str:
    """Capped SELECT over one catalog relation (tree enter-to-run).

    Identifiers come from the engine's own catalog and are double-quote
    escaped; the limit is int-coerced; execution is read-only — so the
    worst case is a failed query, never a write.
    """
    cols = _quote_ident(column) if column else "*"
    return f"SELECT {cols} FROM {_quote_ident(table)} LIMIT {int(limit)}"  # noqa: S608


def db_completions(store: str, root: Path | None = None) -> list[str]:
    """Completion words for the SQL box: keywords, tables, table.column,
    bare columns (deduped, priority-ordered for first-match suggestion)."""
    tables = db_schema(store, root)
    words = list(DB_SQL_KEYWORDS)
    words.extend(t.name for t in tables)
    for t in tables:
        words.extend(f"{t.name}.{c.name}" for c in t.columns)
    seen: set[str] = set()
    bare = []
    for t in tables:
        for c in t.columns:
            if c.name not in seen:
                seen.add(c.name)
                bare.append(c.name)
    words.extend(bare)
    return words


def db_complete(words: list[str], fragment: str) -> str | None:
    """Complete one token: first case-insensitive prefix match that extends
    the fragment (exact matches complete nothing). A ``table.`` prefix
    completes that table's columns. Pure — the modal calls it on the
    cursor-line fragment for Tab completion. Returns the full token."""
    matches = db_match_words(words, fragment, 1)
    return matches[0] if matches else None


def db_match_words(words: list[str], fragment: str, limit: int = 6) -> list[str]:
    """All completions for a fragment (db_complete returns the first)."""
    frag = fragment.lstrip('"')
    if not frag:
        return []
    if "." in frag:
        head, _, tail = frag.rpartition(".")
        base = head + "."
        cands = [w for w in words if "." in w and w.split(".")[0].casefold() == head.casefold()]
        sub = tail
    else:
        base, cands, sub = "", words, frag
    fold = sub.casefold()
    out = []
    for word in cands:
        stem = word[len(base) :] if base else word
        if stem.casefold().startswith(fold) and stem.casefold() != fold:
            out.append(base + stem)
            if len(out) >= limit:
                break
    return out


def _db_history_path(store: str) -> Path:
    return Path.home() / ".config" / "search_tui" / f"db_history_{store}.txt"


def load_db_history(store: str, path: Path | None = None, limit: int = 200) -> list[str]:
    """Past queries, oldest-first (operator state; missing file → [])."""
    p = path if path is not None else _db_history_path(store)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln for ln in lines if ln.strip()][-limit:]


def append_db_history(store: str, sql: str, path: Path | None = None, cap: int = 200) -> None:
    """Record one run: skip blanks/repeat-of-last, cap the file. Never raises."""
    sql = sql.strip()
    if not sql:
        return
    p = path if path is not None else _db_history_path(store)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        hist = load_db_history(store, p, cap)
        if hist and hist[-1] == sql:
            return
        hist.append(sql)
        p.write_text("\n".join(hist[-cap:]) + "\n", encoding="utf-8")
    except OSError:  # noqa: S110 — history is a nicety, never fatal
        pass


def db_row_matches(cells: list[object], query: str) -> bool:
    """Fuzzy subsequence match over a row's cells (casefolded)."""
    q = query.casefold()
    if not q:
        return True
    hay = " ".join("" if v is None else str(v) for v in cells).casefold()
    it = iter(hay)
    return all(ch in it for ch in q)


def db_filter_rows(rows: list[tuple[object, ...]], query: str) -> list[tuple[object, ...]]:
    """Rows whose cells fuzz-match the query (empty query → all)."""
    return [r for r in rows if db_row_matches(list(r), query)]


def db_schema(store: str, root: Path | None = None) -> list[DbTable]:
    """List tables/views + columns for a store (read-only, alphabetical)."""
    path = db_store_path(store, root)
    engine = str(DB_STORES[store]["engine"])
    if engine == "sqlite":
        from helpers.core.db import connect as _db_connect

        conn = _db_connect(path, read_only=True, wal=False)
        try:
            names = conn.execute(
                "SELECT name, type FROM sqlite_master"
                " WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
                " ORDER BY name"
            ).fetchall()
            tables = []
            for name, kind in names:
                cols = conn.execute(f"PRAGMA table_info({_quote_ident(name)})").fetchall()
                tables.append(
                    DbTable(
                        name=name,
                        kind="view" if kind == "view" else "table",
                        columns=[DbColumn(name=str(c[1]), ctype=str(c[2] or "")) for c in cols],
                    )
                )
            return tables
        finally:
            conn.close()
    import duckdb  # lazy: duckdb is a main dep, not part of the tui extra

    conn = duckdb.connect(str(path), read_only=True)
    try:
        names = conn.execute(
            "SELECT table_name, table_type FROM information_schema.tables"
            " WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        tables = []
        for name, table_type in names:
            cols = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_schema = 'main' AND table_name = ?"
                " ORDER BY ordinal_position",
                [name],
            ).fetchall()
            tables.append(
                DbTable(
                    name=str(name),
                    kind="view" if str(table_type).upper() == "VIEW" else "table",
                    columns=[DbColumn(name=str(c[0]), ctype=str(c[1] or "")) for c in cols],
                )
            )
        return tables
    finally:
        conn.close()


def _db_run_sqlite(path: Path, sql: str, limit: int, timeout_ms: int) -> DbResult:
    from helpers.core.db import connect as _db_connect

    t0 = time.perf_counter()
    conn = _db_connect(path, read_only=True, wal=False)
    try:
        deadline = t0 + timeout_ms / 1000.0

        def _abort() -> int:
            return 1 if time.perf_counter() >= deadline else 0

        conn.set_progress_handler(_abort, 1000)
        try:
            cur = conn.execute(sql)
        finally:
            conn.set_progress_handler(None, 0)
        cols = [d[0] for d in (cur.description or [])]
        fetched = cur.fetchmany(limit + 1)
        elapsed = (time.perf_counter() - t0) * 1000.0
        return DbResult(
            columns=[str(c) for c in cols],
            rows=list(fetched[:limit]),
            truncated=len(fetched) > limit,
            elapsed_ms=elapsed,
        )
    except Exception as e:  # noqa: BLE001 — errors are DbResult data
        elapsed = (time.perf_counter() - t0) * 1000.0
        return DbResult(columns=[], rows=[], truncated=False, elapsed_ms=elapsed, error=str(e))
    finally:
        conn.close()


def _db_run_duckdb(path: Path, sql: str, limit: int) -> DbResult:
    import duckdb  # lazy: see db_schema

    t0 = time.perf_counter()
    conn = duckdb.connect(str(path), read_only=True)
    try:
        try:
            cur = conn.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            fetched = cur.fetchmany(limit + 1)
        except Exception as e:  # noqa: BLE001 — errors are DbResult data
            elapsed = (time.perf_counter() - t0) * 1000.0
            return DbResult(columns=[], rows=[], truncated=False, elapsed_ms=elapsed, error=str(e))
        elapsed = (time.perf_counter() - t0) * 1000.0
        return DbResult(
            columns=[str(c) for c in cols],
            rows=list(fetched[:limit]),
            truncated=len(fetched) > limit,
            elapsed_ms=elapsed,
        )
    finally:
        conn.close()


def db_run(
    store: str,
    sql: str,
    limit: int = DB_ROW_CAP,
    timeout_ms: int = DB_TIMEOUT_MS,
    root: Path | None = None,
) -> DbResult:
    """Run one read-only query; failures and the row cap are DbResult data.

    timeout_ms is enforced on SQLite via a progress-handler abort; DuckDB
    is bounded by the row cap (its result sets here are ms-scale) and the
    UI runs every query in a worker thread so the screen never blocks.
    """
    if not sql.strip():
        return DbResult(columns=[], rows=[], truncated=False, elapsed_ms=0.0, error="empty query")
    path = db_store_path(store, root)
    if not path.exists():
        return DbResult(
            columns=[], rows=[], truncated=False, elapsed_ms=0.0, error=f"missing db file: {path}"
        )
    engine = str(DB_STORES[store]["engine"])
    if engine == "sqlite":
        return _db_run_sqlite(path, sql, limit, timeout_ms)
    return _db_run_duckdb(path, sql, limit)


def index_rows(root: Path) -> list[dict[str, object]]:
    """Fast freshness snapshot: store existence + age per index."""
    rows: list[dict[str, object]] = []
    for name, rel, script in INDEXES:
        f = root / rel
        if f.exists():
            age = max(int(time.time() - f.stat().st_mtime), 0)
            rows.append({"index": name, "store": rel, "age": age, "state": "present"})
        else:
            rows.append({"index": name, "store": rel, "age": None, "state": "MISSING"})
    return rows


def index_check_argv(name: str) -> list[str] | None:
    """Deep-check argv (rc 0 = fresh) for an index, None if unknown."""
    for n, _, script in INDEXES:
        if n == name:
            return [sys.executable, script, "--check"]
    return None


def index_refresh_argv(name: str) -> list[str] | None:
    """Write-mode rebuild argv for an index, None if unknown."""
    for n, _, script in INDEXES:
        if n == name:
            return [sys.executable, script]
    return None


def editor_chain() -> list[str]:
    """Resolve an editor, line-argument capable: VISUAL, EDITOR, nvim, vim, less."""
    for var in ("VISUAL", "EDITOR"):
        v = os.environ.get(var, "").strip()
        if v:
            return v.split()
    for cand in ("nvim", "vim", "less"):
        resolved = shutil.which(cand)
        if resolved:
            return [resolved]
    return []


def locate_make_target(root: Path, target: str) -> tuple[str, int] | None:
    """Find a make target's rule line (script lane 'make' rows)."""
    mk = root / "Makefile"
    if not mk.exists():
        return None
    pat = re.compile(rf"^{re.escape(target)}:")
    for i, ln in enumerate(mk.read_text(errors="replace").splitlines(), 1):
        if pat.match(ln):
            return str(mk), i
    return None


def open_command(hit: Hit, root: Path, force_editor: bool = False) -> list[str] | None:
    """Build the open command for a hit (glow for md, else editor)."""
    p = root / hit.path
    if p.exists() and p.is_file():
        target, line = str(p), hit.line
    else:
        loc = locate_make_target(root, hit.path)
        if loc is None:
            return None
        target, line = loc
    is_md = target.endswith(".md")
    glow = shutil.which("glow")
    if is_md and not force_editor and glow:
        return [glow, "-p", target]
    ed = editor_chain()
    if not ed:
        return None
    exe = ed[0].rsplit("/", 1)[-1]
    if line and (exe in ("vim", "nvim") or "less" in exe):
        return [*ed, f"+{line}", target]
    return [*ed, target]


def index_ages(root: Path) -> str:
    """Human ages of the search-fresh sidecars, for the status bar."""
    parts = []
    for label, rel in (
        ("docs", "memory/doc_search.db"),
        ("scripts", "memory/script_search.db"),
        ("notes", "memory/research.db"),
    ):
        f = root / rel
        if f.exists():
            age = max(int(time.time() - f.stat().st_mtime), 0)
            parts.append(f"{label} {age // 3600}h{age % 3600 // 60:02d}m")
        else:
            parts.append(f"{label} MISSING")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Textual app
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - TUI entry
    if not sys.stdin.isatty():
        print("search_tui needs a TTY", file=sys.stderr)
        return 2
    # Script-mode runs put helpers/misc (not the repo root) on sys.path.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    # textual is an optional extra; import lazily so adapters/tests run without it.
    try:
        from helpers.misc.search_tui_app import SearchApp
    except ImportError as exc:
        hint = (
            "textual is not installed — run `uv sync --extra tui`"
            " (or `uv pip install textual`) and retry."
            if "textual" in str(exc)
            else f"cannot import the TUI module: {exc}"
        )
        print(hint, file=sys.stderr)
        return 2

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-q", "--query", default="", help="initial query")
    ap.add_argument("--lane", default="docs", choices=LANES, help="initial lane (default docs)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="result cap")
    args = ap.parse_args(argv)
    SearchApp(args.query, args.lane, args.limit).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
