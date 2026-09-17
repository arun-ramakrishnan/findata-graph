"""Unit tests for search_tui adapters (terminal-free; no textual needed).

Pinned to one xdist worker. Pilots deliberately avoid heavy real
backends (the docs lane loads the embedding model; code lanes walk
the tree): the scripts sidecar and rg are the only live subprocesses.
Manual smoke for the rest: `make search-tui`.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.xdist_group("search_tui")

from helpers.misc.search_tui import (
    Hit,
    _int_or_none,
    editor_chain,
    fts_safe,
    index_ages,
    locate_make_target,
    notes_query,
    open_command,
    parse_doc_json,
    parse_rg_lines,
    parse_riprewire,
    parse_script_json,
    ripwire_argv,
    run_lane,
    RunStep,
    parse_gate_report,
    parse_perf_report,
    parse_integrity_report,
    parse_verify_report,
    parse_report_summary,
)

# ---------------------------------------------------------------- parsers


def test_parse_doc_json_normalizes_hits() -> None:
    raw = (
        '{"mode":"hybrid","results":[{"path":"doc/a.md","name":"a.md",'
        '"section":"s","title":"A","section_title":"Why",'
        '"anchor":42,"snippet":"x <mark>q</mark> y",'
        '"score":0.03,"similarity":0.88}]}'
    )
    hits = parse_doc_json(raw, 10)
    assert len(hits) == 1
    h = hits[0]
    assert (h.path, h.line, h.title, h.section, h.score, h.lane) == (
        "doc/a.md",
        42,
        "Why",
        "s",
        0.88,
        "docs",
    )
    assert "<mark>" not in h.snippet


def test_parse_doc_json_respects_limit() -> None:
    rows = ",".join(f'{{"path":"p{i}.md"}}' for i in range(5))
    assert len(parse_doc_json('{"results":[' + rows + "]}", 3)) == 3


def test_parse_script_json_make_rows_have_no_file() -> None:
    raw = (
        '{"results":[{"path":"snapshot","title":"make snapshot",'
        '"kind":"make","area":"make","purpose":"Refresh",'
        '"snippet":"s","score":1.0,"similarity":0.9}]}'
    )
    (h,) = parse_script_json(raw, 10)
    assert h.line is None
    assert h.kind == "make"
    assert h.snippet == "Refresh"


def test_parse_rg_lines() -> None:
    hits = parse_rg_lines("./a/b.py:12:txt: with colons\nc/d.md:3:hi\nnoise")
    assert [h.path for h in hits] == ["a/b.py", "c/d.md"]
    assert (hits[0].line, hits[0].snippet) == (12, "txt: with colons")


def test_parse_riprewire_symbol_rows() -> None:
    raw = '<sigs><d l="246" n="TIER2_STEPS" p="helpers/x.py" cx="0" r="1">body</d></sigs>'
    (h,) = parse_riprewire(raw)
    assert (h.path, h.line, h.title, h.score) == ("helpers/x.py", 246, "TIER2_STEPS", 1.0)


def test_parse_riprewire_callers_rows() -> None:
    raw = '<callers of="f"><s t="fn" n="g" p="m.py:7" tested="1"/></callers>'
    (h,) = parse_riprewire(raw)
    assert (h.path, h.line, h.title, h.section) == ("m.py", 7, "g", "fn")


def test_parse_riprewire_grep_rows() -> None:
    raw = (
        '<grep pattern="p"><f p="a.py"><hit l="1" in="p"><![CDATA[line1]]>'
        '</hit><hit l="2" in="p"><![CDATA[line2]]></hit></f></grep>'
    )
    hits = parse_riprewire(raw)
    assert [(h.path, h.line) for h in hits] == [("a.py", 1), ("a.py", 2)]


def test_parse_riprewire_recall_blocks() -> None:
    raw = "━━ AGENTS.md  (relevance 4.612) ━━  [sections: 2 of 7]\nbody"
    (h,) = parse_riprewire(raw)
    assert (h.path, h.score, h.kind) == ("AGENTS.md", 4.612, "recall")


# ---------------------------------------------------------------- verbs


@pytest.mark.parametrize(
    ("query", "argv"),
    [
        ("callers: f", ["ripwire", ".", "--callers=f"]),
        ("impact: f", ["ripwire", ".", "--impact=f"]),
        ("grep: STR", ["ripwire", ".", "--grep=STR", "--grep-in=any", "--legend=compact"]),
        ("recall: why so", ["ripwire", ".", "--recall=why so"]),
        ("plain task", ["ripwire", ".", "--for=plain task"]),
        ("grep:", ["ripwire", ".", "--for=grep:"]),
    ],
)
def test_ripwire_argv(query: str, argv: list[str]) -> None:
    assert ripwire_argv(query) == argv


# ---------------------------------------------------------------- openers


def test_editor_chain_prefers_visual(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISUAL", "code -w")
    monkeypatch.delenv("EDITOR", raising=False)
    assert editor_chain() == ["code", "-w"]


def test_editor_chain_falls_back_to_vim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/vim" if c == "vim" else None)
    assert editor_chain() == ["/usr/bin/vim"]


def test_open_command_glow_for_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "x.md"
    f.write_text("# hi\n")
    monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/glow" if c == "glow" else None)
    cmd = open_command(Hit(path="x.md", line=2), tmp_path)
    assert cmd is not None and cmd[0] == "/usr/bin/glow" and cmd[1] == "-p"


def test_open_command_editor_line_aware(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "x.py").write_text("pass\n")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/nvim" if c == "nvim" else None)
    cmd = open_command(Hit(path="x.py", line=9), tmp_path)
    assert cmd == ["/usr/bin/nvim", "+9", str(tmp_path / "x.py")]


def test_locate_make_target(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("a:\n\tb\n\nc-d:\n\te\n")
    assert locate_make_target(tmp_path, "c-d") == (str(tmp_path / "Makefile"), 4)
    assert locate_make_target(tmp_path, "zz") is None


# ---------------------------------------------------------------- notes FTS


def _notes_db(tmp_path: Path) -> Path:
    db = tmp_path / "n.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE VIRTUAL TABLE note_search USING fts5("
        "doc_type, file_path UNINDEXED, title, sector, content,"
        " embedding UNINDEXED, section_title, anchor UNINDEXED)"
    )
    conn.executemany(
        "INSERT INTO note_search(doc_type, file_path, title, sector, content,"
        " section_title, anchor) VALUES (?,?,?,?,?,?,?)",
        [
            ("company", "a.md", "Alpha", "Tech", "mojo benchmark lane", "Body", 3),
            ("company", "b.md", "Beta", "Energy", "unrelated text", "Body", 4),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_notes_query_ranks_by_bm25(tmp_path: Path) -> None:
    hits = notes_query(_notes_db(tmp_path), "mojo", 10)
    assert [h.path for h in hits] == ["a.md"]
    assert hits[0].line == 3
    assert "mojo" in hits[0].snippet or hits[0].snippet == ""


def test_fts_safe_degrades_bad_syntax() -> None:
    assert fts_safe('plain"query') != 'plain"query'
    assert "OR" in fts_safe('we "ird bro ken')


def test_index_ages_missing_and_present(tmp_path: Path) -> None:
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "research.db").write_text("")
    ages = index_ages(tmp_path)
    assert "docs MISSING" in ages
    assert "notes 0h" in ages


# ---------------------------------------------------------------- pilot UI


def _fake_hits() -> list[Hit]:
    return [
        Hit(path="a/b.py", line=10, title="one", section="s", snippet="snip", lane="scripts"),
        Hit(path="a/b.py", line=20, title="two", section="s", snippet="snip2", lane="scripts"),
        Hit(path="doc/x.md", line=3, title="md hit", section="d", snippet="md", lane="scripts"),
    ]


def test_app_pilot_rows_preview_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    """UI mechanics against canned hits — no lane backend, no subprocess, no
    embedding model (the docs/scripts lanes embed the query; running them in
    pytest repeatedly pegged the CPU). Live smoke stays manual: make search-tui."""
    pytest.importorskip("textual")
    import helpers.misc.search_tui_app as appmod
    from textual.widgets import DataTable

    monkeypatch.setattr(
        appmod, "run_lane", lambda lane, q, limit, mode="hybrid": (_fake_hits(), "3 hits · fake")
    )

    app = appmod.SearchApp("anything", "scripts", 10)

    async def drive() -> tuple[int, bool, int, int]:
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(40):
                await pilot.pause(0.05)
                if app.query_one("#results", DataTable).row_count > 0:
                    break
            rows = app.query_one("#results", DataTable).row_count
            first = [ln.text for ln in app.query_one("#preview").lines[:1]]
            await pilot.press("down")
            await pilot.pause(0.1)
            second = [ln.text for ln in app.query_one("#preview").lines[:1]]
            moved = first != second
            app.action_monitor()  # direct: the "i" key is gated while typing (by design)
            await pilot.pause(0.2)
            mrows = app.screen.query_one("#idx-table", DataTable).row_count
            app.pop_screen()
            await pilot.pause(0.1)
            app.action_lane("literal")
            await pilot.pause(0.2)
            return rows, moved, mrows, app.query_one("#results", DataTable).row_count

    import asyncio

    rows, moved, mrows, relane = asyncio.run(drive())
    assert rows == 3  # duplicate path:line rows survive (index keys)
    assert moved  # preview follows the cursor
    assert mrows == 3  # monitor: three indexes, ages only, no auto checks
    assert relane == 3  # lane switch re-runs (mocked) and repopulates


def test_call_chain_drill_and_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """h renders chain rows in the table; enter on a caller drills; b unwinds."""
    pytest.importorskip("textual")
    import helpers.misc.search_tui_app as appmod

    def fake_chain(sym: str) -> dict:
        return {
            "of": sym,
            "callers": [
                Hit(path=f"helpers/a_{sym}.py", line=10, title=f"caller_{sym}", kind="symbol")
            ],
            "callees": [],
        }

    monkeypatch.setattr(appmod, "call_chain", fake_chain)

    async def wait_for(app, cond) -> None:  # noqa: ANN001
        for _ in range(40):
            await app.workers.wait_for_complete()
            await asyncio.sleep(0.05)
            if cond():
                return
        raise AssertionError("condition never satisfied")

    async def drive() -> tuple[int, str, int, str]:
        app = appmod.SearchApp("iter_tree_files", "code", 10)
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.press("enter")
            await wait_for(app, lambda: app._w_table.row_count > 0)
            await pilot.press("h")
            await wait_for(app, lambda: app._chain_sym == "iter_tree_files")
            n_chain = app._w_table.row_count
            app._w_table.move_cursor(row=1)  # first caller row
            await pilot.press("enter")
            await wait_for(app, lambda: app._chain_sym == "caller_iter_tree_files")
            drilled = app._chain_sym
            await pilot.press("b")
            await wait_for(app, lambda: app._chain_sym == "iter_tree_files")
            return n_chain, drilled, app._w_table.row_count, app._chain_sym

    import asyncio

    n_chain, drilled, n_back, sym_back = asyncio.run(drive())
    assert n_chain == 3  # callers header + 1 caller + callees header
    assert drilled == "caller_iter_tree_files"
    assert n_back == 3
    assert sym_back == "iter_tree_files"


def test_int_or_none() -> None:
    assert _int_or_none("42") == 42
    assert _int_or_none(7) == 7
    assert _int_or_none(None) is None
    assert _int_or_none("x") is None


# ---------------------------------------------------------------- report
# fixtures use the markdown format produced by the updated writers.
# Section headers use `## LABEL (SEVERITY ...)` where SEVERITY is the
# first word in parens (ERROR, WARNING, advisory→WARNING, gate-failing→ERROR).

_GATE_FIXTURE = (
    "# make qa \u2014 gate report\n"
    "\n"
    "**Generated:** 2026-09-17 09:18:12  \u00b7  **Python:** 3.14.4  jobs=4\n"
    "\n"
    "| Step | Time (s) | Status |\n"
    "|---|---|---|\n"
    "| lint | 0.06 | \u2713 OK |\n"
    "| md-lint | 6.79 | \u2713 OK |\n"
    "| types | 2.97 | \u2713 OK |\n"
    "| deptry | 1.20 | \u2717 FAIL |\n"
    "| **3/4 passed** | | **gate FAIL** |\n"
    "\n"
    "## deptry (FAILED)\n"
    "\n"
    "Scanning 97 files...\n"
    "Found 1 dependency issue.\n"
)

_GATE_TWO_RUNS = (
    "# make qa \u2014 gate report\n"
    "\n"
    "**Generated:** 2026-09-11 15:21:06  \u00b7  **Python:** 3.14.4  jobs=8\n"
    "\n"
    "| Step | Time (s) | Status |\n"
    "|---|---|---|\n"
    "| lint | 0.06 | \u2713 OK |\n"
    "| deptry | 1.20 | \u2717 FAIL |\n"
    "| **8/9 passed** | | **gate FAIL** |\n"
    "\n"
    "# make qa \u2014 gate report\n"
    "\n"
    "**Generated:** 2026-09-12 10:03:03  \u00b7  **Python:** 3.14.4  jobs=8\n"
    "\n"
    "| Step | Time (s) | Status |\n"
    "|---|---|---|\n"
    "| lint | 0.05 | \u2713 OK |\n"
    "| deptry | 1.10 | \u2713 OK |\n"
    "| **9/9 passed** | | **gate PASS** |\n"
)

_INTEGRITY_FIXTURE = (
    "# FinData Knowledge Graph \u2014 Database Integrity Report\n"
    "\n"
    "**Generated:** 2026-09-17 09:18:12  \u00b7  **Database:** `/home/arun/memory/research.db`\n"
    "\n"
    "## RELATIONS (ERROR-level; gate-failing)\n"
    "total=11663 unknown_type=0 self_loops=0 -> errors=0\n"
    "\n"
    "## ENTITY_TAGS (ERROR-level; gate-failing)\n"
    "total=7206 orphaned=0 -> errors=0\n"
    "\n"
    "## FUZZY NAME SIMILARITY (advisory \u2014 likely-same-company pairs)\n"
    "similar_pairs=157 -> warnings=157\n"
    "  - EaseMyTrip (Easy Trip Planners)  ~=  Easy Trip Planners\n"
    "\n"
    "## GRAPH SUMMARY (advisory; shape snapshot)\n"
    "entity counts: company=6203, institution=207\n"
)

_VERIFY_FIXTURE = (
    "# FinData Knowledge Graph \u2014 Notes Verification Report\n"
    "\n"
    "**Generated:** 2026-09-17 09:18:12  \u00b7  **Project Root:** `/home/arun/repo`\n"
    "\n"
    "| Metric | Value |\n"
    "|---|---|\n"
    "| Total Files Checked | 1233 |\n"
    "| Errors | 0 |\n"
    "| Warnings | 0 |\n"
    "\n"
    "## ERRORS (gate-failing)\n"
    "\n"
    "## Verdict\n"
    "\n"
    "\u2705 All notes passed verification (no errors).\n"
)

_PERF_FIXTURE = (
    "# make perf \u2014 benchmark report\n"
    "\n"
    "**Generated:** 2026-09-11 15:12:55  \u00b7  **Python:** 3.14.4\n"
    "\n"
    "| Benchmark | Time (s) | Budget | Status |\n"
    "|---|---|---|---|\n"
    "| integrity_check | 0.37 | 2.0s | \u2713 OK |\n"
    "| verify_notes | 0.49 | 3.0s | \u2713 OK |\n"
    "| **22/22 passed** | | | |\n"
)


def test_parse_gate_report_fixture() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(_GATE_FIXTURE)
        path = Path(f.name)
    try:
        blocks = parse_gate_report(path)
        assert len(blocks) == 1
        b = blocks[0]
        assert b.gate == "qa"
        assert b.timestamp == "2026-09-17 09:18:12"
        assert b.jobs == 4
        assert len(b.steps) == 4
        assert b.steps[0] == RunStep("lint", 0.06, "\u2713 OK")
        assert b.steps[3] == RunStep("deptry", 1.20, "\u2717 FAIL")
        assert "3/4 passed" in b.summary
        assert "gate FAIL" in b.summary
    finally:
        path.unlink()


def test_parse_gate_report_multi_run() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", prefix="qa_", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(_GATE_TWO_RUNS)
        path = Path(f.name)
    try:
        blocks = parse_gate_report(path)
        assert len(blocks) == 2
        assert blocks[0].timestamp == "2026-09-11 15:21:06"
        assert blocks[0].summary.split("/")[0].strip() == "8"
        assert blocks[1].timestamp == "2026-09-12 10:03:03"
        assert blocks[1].summary.split("/")[0].strip() == "9"
        assert "9/9 passed" in parse_report_summary(path)
    finally:
        path.unlink()


def test_parse_perf_report() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(_PERF_FIXTURE)
        path = Path(f.name)
    try:
        blocks = parse_perf_report(path)
        assert len(blocks) == 1
        b = blocks[0]
        assert b.gate == "perf:perf"
        assert b.timestamp == "2026-09-11 15:12:55"
        assert len(b.steps) == 2
        assert b.steps[0] == RunStep("integrity_check", 0.37, "\u2713 OK")
        assert "2 benchmarks" in b.summary
    finally:
        path.unlink()


def test_parse_integrity_report() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(_INTEGRITY_FIXTURE)
        path = Path(f.name)
    try:
        rows = parse_integrity_report(path)
        assert len(rows) == 4
        assert rows[0].name == "RELATIONS"
        assert rows[0].severity == "ERROR"
        assert rows[0].summary == "total=11663 unknown_type=0 self_loops=0 -> errors=0"
        assert rows[2].name == "FUZZY NAME SIMILARITY"
        assert rows[2].severity == "WARNING"
        assert rows[3].name == "GRAPH SUMMARY"
        assert rows[3].severity == "WARNING"
    finally:
        path.unlink()


def test_parse_verify_report() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(_VERIFY_FIXTURE)
        path = Path(f.name)
    try:
        d = parse_verify_report(path)
        assert d["total_files"] == 1233
        assert d["errors"] == 0
        assert d["warnings"] == 0
        assert "All notes passed verification" in d["verdict"]
    finally:
        path.unlink()


def test_parse_report_summary_gate() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", prefix="qa_", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(_GATE_FIXTURE)
        path = Path(f.name)
    try:
        s = parse_report_summary(path)
        assert "3/4 passed" in s
        assert "gate FAIL" in s
    finally:
        path.unlink()


def test_parse_report_summary_integrity() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", prefix="database_integrity_", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(_INTEGRITY_FIXTURE)
        path = Path(f.name)
    try:
        s = parse_report_summary(path)
        assert "4 checks" in s
    finally:
        path.unlink()


def test_parse_report_summary_verify() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", prefix="verify_notes_", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(_VERIFY_FIXTURE)
        path = Path(f.name)
    try:
        s = parse_report_summary(path)
        assert "1233 files" in s
        assert "0 errors" in s
        assert "0 warnings" in s
    finally:
        path.unlink()


# ---------------------------------------------------------------- reports lane


_LANE_QA = """# make qa — gate report

**Generated:** 2026-09-11 15:21:06  ·  **Python:** 3.14.4  jobs=4

| Step | Time (s) | Status |
|---|---|---|
| lint | 0.06 | ✓ OK |
| pytest | 72.12 | ✗ FAIL |
| **1/2 passed** | | **gate FAIL** |

## pytest (FAILED)

E   first failure tail

# make qa — gate report

**Generated:** 2026-09-12 10:03:03  ·  **Python:** 3.14.4  jobs=4

| Step | Time (s) | Status |
|---|---|---|
| lint | 0.05 | ✓ OK |
| pytest | 65.10 | ✓ OK |
| **2/2 passed** | | **gate PASS** |
"""

_LANE_PERF = """# make perf — benchmark report

**Generated:** 2026-09-12 10:04:00  ·  **Python:** 3.14.4

| Benchmark | Time (s) | Budget | Status |
|---|---|---|---|
| integrity_check | 0.37 | 2.0s | ✓ OK |
| graph_pagerank | 4.10 | 3.0s | ✗ FAIL |
| **1/2 passed** | | | |
"""

_LANE_INT = """# FinData Knowledge Graph — Database Integrity Report

**Generated:** 2026-09-12T18:20:21  ·  **Database:** `memory/research.db`

## RELATIONS (ERROR-level; gate-failing)

total=100 unknown_type=2 -> errors=2

## ENTITY_TAGS (ERROR-level; gate-failing)

total=50 orphaned=0 -> errors=0
"""

_LANE_VERIFY = """# FinData Knowledge Graph — Notes Verification Report

**Generated:** 2026-09-12 18:20:22  ·  **Project Root:** `/tmp/x`

| Metric | Value |
|---|---|
| Total Files Checked | 10 |
| Errors | 1 |
| Warnings | 0 |

## ERRORS (gate-failing)

### yaml_structure (1)
- /path/f.md: bad frontmatter

## Verdict

⚠️  Found 1 errors that need attention.
"""


def _lane_root(tmp_path: Path) -> Path:
    out = tmp_path / "outputs"
    out.mkdir()
    (out / "qa_report.md").write_text(_LANE_QA)
    (out / "perf_report.md").write_text(_LANE_PERF)
    (out / "database_integrity_report.md").write_text(_LANE_INT)
    (out / "verify_notes_report.md").write_text(_LANE_VERIFY)
    return tmp_path


def test_reports_lane_overview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import helpers.misc.search_tui as tui

    monkeypatch.setattr(tui, "REPO_ROOT", _lane_root(tmp_path))
    hits, status = run_lane("reports", "", 40)
    assert {h.kind for h in hits} == {"overview"}
    assert len(hits) == 4  # only the files present (no advisory/integration/maint)
    assert "missing" in status
    assert all(h.line and h.line > 0 for h in hits)
    assert all(h.path.startswith("outputs/") for h in hits)


def test_reports_lane_verb_latest_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import helpers.misc.search_tui as tui

    monkeypatch.setattr(tui, "REPO_ROOT", _lane_root(tmp_path))
    hits, status = run_lane("reports", "qa:", 40)
    assert [h.title for h in hits] == ["lint", "pytest"]
    assert all("2026-09-12" in h.section for h in hits)  # latest run only
    assert all(h.line and h.line > 0 for h in hits)


def test_reports_lane_verb_plus_text_searches_all_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import helpers.misc.search_tui as tui

    monkeypatch.setattr(tui, "REPO_ROOT", _lane_root(tmp_path))
    hits, _ = run_lane("reports", "qa: fail", 40)
    assert len(hits) == 2  # run verdict matches: both rows of the failed run
    assert all("2026-09-11" in h.section for h in hits)
    hits, _ = run_lane("reports", "qa: 2026-09-11", 40)
    assert len(hits) == 2  # stamp-scoped: the old run's rows


def test_reports_lane_bare_text_searches_latest_everywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import helpers.misc.search_tui as tui

    monkeypatch.setattr(tui, "REPO_ROOT", _lane_root(tmp_path))
    hits, _ = run_lane("reports", "pagerank", 40)
    assert [h.title for h in hits] == ["graph_pagerank"]
    hits, _ = run_lane("reports", "RELATIONS", 40)
    assert [h.title for h in hits] == ["RELATIONS"]
    assert hits[0].line == 5


def test_reports_lane_verify_issues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import helpers.misc.search_tui as tui

    monkeypatch.setattr(tui, "REPO_ROOT", _lane_root(tmp_path))
    hits, status = run_lane("reports", "verify:", 40)
    assert len(hits) == 1
    assert hits[0].title == "yaml_structure"
    assert "/path/f.md: bad frontmatter" in hits[0].snippet
    assert "1 errors" in status


def test_reports_lane_unknown_verb_is_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import helpers.misc.search_tui as tui

    monkeypatch.setattr(tui, "REPO_ROOT", _lane_root(tmp_path))
    hits, status = run_lane("reports", "zzz_no_such_step", 40)
    assert hits == []
    assert "no report rows" in status


def test_reports_lane_empty_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import helpers.misc.search_tui as tui

    monkeypatch.setattr(tui, "REPO_ROOT", tmp_path)  # no outputs/ at all
    hits, status = run_lane("reports", "", 40)
    assert hits == []
    assert "missing" in status


# ---------------------------------------------------------------- themes


def test_theme_cycle_order() -> None:
    from helpers.misc.search_tui import THEME_ORDER, next_theme

    assert len(THEME_ORDER) == 4
    assert next_theme("github-dark") == "github-light"
    assert next_theme("high-contrast") == "github-dark"  # wraps
    assert next_theme("bogus") == "github-dark"  # unknown restarts


def test_theme_token_sets_uniform() -> None:
    from helpers.misc.search_tui import THEMES

    base = set(THEMES["github-dark"]["rich"])
    assert len(base) >= 12
    for name, spec in THEMES.items():
        assert set(spec["rich"]) == base, name
        assert {"primary", "background", "dark"} <= set(spec["variables"]), name
        assert isinstance(spec["description"], str) and spec["description"]


def test_theme_persist_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from helpers.misc.search_tui import load_theme_name, save_theme_name

    monkeypatch.setenv("HOME", str(tmp_path))
    assert load_theme_name() == "github-dark"  # absent file
    assert save_theme_name("solarized-dark") is True
    assert load_theme_name() == "solarized-dark"
    assert save_theme_name("bogus") is False
    assert load_theme_name() == "solarized-dark"  # unknown write refused
    (tmp_path / ".config" / "search_tui" / "theme").write_text("bogus\n")
    assert load_theme_name() == "github-dark"  # unknown content falls back


def test_theme_cycle_pilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """t cycles widget theme + preview palette and persists the choice."""
    pytest.importorskip("textual")
    import asyncio

    import helpers.misc.search_tui_app as appmod

    monkeypatch.setenv("HOME", str(tmp_path))
    app = appmod.SearchApp("", "docs", 10)

    async def drive() -> tuple[str, str]:
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(40):
                await pilot.pause(0.05)
                if app._mounted:
                    break
            assert app._theme == "github-dark"
            # direct action: the "t"/"T" keys are gated while typing
            # (same reason the monitor test calls the action directly)
            app.action_cycle_theme()
            await pilot.pause(0.1)
            first = app._theme
            app.action_theme_picker()
            await pilot.pause(0.2)
            assert isinstance(app.screen, appmod.ThemeScreen)
            rows = app.screen.query_one("#theme-table").row_count
            assert rows == 4
            await pilot.press("escape")
            await pilot.pause(0.1)
            return first, (tmp_path / ".config" / "search_tui" / "theme").read_text().strip()

    first, saved = asyncio.run(drive())
    assert first == "github-light"
    assert saved == "github-light"


# ---------------------------------------------------------------- report screen helpers


def test_report_run_spans_and_verb(tmp_path: Path) -> None:
    from helpers.misc.search_tui import report_run_spans, report_verb_for_path

    p = tmp_path / "qa_report.md"
    p.write_text(_LANE_QA)
    spans = report_run_spans(p)
    assert len(spans) == 2
    assert spans[0][0] == 1
    assert spans[1][1] == len(_LANE_QA.splitlines())
    assert report_verb_for_path("outputs/qa_report.md") == "qa"
    assert report_verb_for_path("qa_report.md") == "qa"
    assert report_verb_for_path("outputs/verify_notes_report.md") == "verify"
    assert report_verb_for_path("nope.md") is None


def test_report_rerun_argv() -> None:
    from helpers.misc.search_tui import REPORT_NAMES, report_rerun_argv

    assert len(REPORT_NAMES) == 7
    for name in REPORT_NAMES:
        argv = report_rerun_argv(name)
        assert argv and len(argv) == 2
    assert report_rerun_argv("bogus") is None
    assert report_rerun_argv("qa")[1] == "qa"


def test_report_screen_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V opens the comprehensive view; rows drill; esc closes (no rerun)."""
    pytest.importorskip("textual")
    import asyncio

    import helpers.misc.search_tui as tui
    import helpers.misc.search_tui_app as appmod
    from textual.widgets import DataTable

    out = tmp_path / "outputs"
    out.mkdir()
    (out / "qa_report.md").write_text(_LANE_QA)
    (out / "verify_notes_report.md").write_text(_LANE_VERIFY)
    monkeypatch.setattr(appmod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tui, "REPO_ROOT", tmp_path)
    app = appmod.SearchApp("", "docs", 10)

    async def drive() -> tuple[int, int]:
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(40):
                await pilot.pause(0.05)
                if app._mounted:
                    break
            # direct action: V is gated while typing (same as t/T/i)
            app.action_report_screen()
            await pilot.pause(0.3)
            assert isinstance(app.screen, appmod.ReportScreen)
            table = app.screen.query_one("#rep-table", DataTable)
            n = table.row_count
            assert n > 0  # 2 qa runs (2+2 steps + 2 headers) + verify run + issue
            table.move_cursor(row=0)
            await pilot.pause(0.2)
            detail = app.screen.query_one("#rep-detail").lines
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, appmod.ReportScreen)
            return n, len(detail)

    n, detail_lines = asyncio.run(drive())
    assert n == 8
    assert detail_lines > 0
