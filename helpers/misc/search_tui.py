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
===== ======== =====================================================

Lanes 1-3 are the indexes ``make search-fresh`` maintains; 4-5 are the
stateless structure/literal tools from AGENTS.md (``--grep`` always
carries ``--grep-in=any``). Nothing here builds a new index — a lane is
only as fresh as ``make search-fresh APPLY=1`` left it (the status bar
shows each index's age).

Reading: ``enter`` opens the hit — markdown via ``glow -p`` when glow
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

REPO_ROOT = Path(__file__).resolve().parents[2]
LANES: tuple[str, ...] = ("docs", "scripts", "notes", "code", "literal")
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
}


def run_lane(lane: str, query: str, limit: int, mode: str = "hybrid") -> tuple[list[Hit], str]:
    """Execute one lane. Returns (hits, status message for the bar).

    ``mode`` applies to the two hybrid backends: "hybrid" (semantic +
    lexical blend, default) or "bm25" (every hit contains the word).
    """
    q = query.strip()
    if not q:
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
