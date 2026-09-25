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
    _rrf_note_hits,
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
    parse_verify_runs,
    report_where_label,
    db_run,
    db_complete,
    db_completions,
    db_filter_rows,
    db_match_words,
    db_preview_sql,
    db_schema,
    db_store_path,
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


def test_rrf_note_hits_unions_lexical_and_semantic_candidates():
    lexical = [Hit(path="a.md", line=None, lane="notes"), Hit(path="b.md", line=None, lane="notes")]
    semantic = [
        Hit(path="c.md", line=None, score=0.9, lane="notes"),
        Hit(path="b.md", line=None, score=0.8, lane="notes"),
    ]
    fused = _rrf_note_hits(lexical, semantic, 3)
    assert {hit.path for hit in fused} == {"a.md", "b.md", "c.md"}
    assert fused[0].path == "b.md"


def test_notes_lane_falls_back_when_semantic_matrix_is_stale(monkeypatch):
    import helpers.misc.search_tui as st

    monkeypatch.setattr(
        st, "notes_query", lambda *_args: [Hit(path="a.md", line=None, lane="notes")]
    )
    monkeypatch.setattr(st, "_semantic_note_hits", lambda *_args: (None, "matrix stale"))
    hits, status = st.run_lane("notes", "query", 10, "hybrid")
    assert [hit.path for hit in hits] == ["a.md"]
    assert "fallback" in status and "matrix stale" in status
    hits, status = st.run_lane("notes", "query", 10, "bm25")
    assert "bm25" in status and "fallback" not in status


def test_fts_safe_degrades_bad_syntax() -> None:
    assert fts_safe("plain query") == "plain query"
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
    from textual.widgets import DataTable, RichLog

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
            first = [ln.text for ln in app.query_one("#preview", RichLog).lines[:1]]
            await pilot.press("down")
            await pilot.pause(0.1)
            second = [ln.text for ln in app.query_one("#preview", RichLog).lines[:1]]
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

    async def drive() -> tuple[int, str | None, int, str | None]:
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


def test_query_busy_overlay_pilot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A1 (search_tui_ux_pass): the loading overlay rides the
    results table while a query worker runs and clears when results land
    (auto initial-query path)."""
    pytest.importorskip("textual")
    import asyncio
    import time as _time

    import helpers.misc.search_tui_app as appmod

    def slow_lane(lane, q, limit, mode="hybrid"):  # noqa: ANN001, ANN202
        _time.sleep(0.4)
        return _fake_hits(), "3 hits · slow"

    monkeypatch.setattr(appmod, "run_lane", slow_lane)

    app = appmod.SearchApp("anything", "scripts", 10)

    async def drive() -> tuple[bool, bool]:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            mid = app._w_table.loading
            for _ in range(40):
                await pilot.pause(0.1)
                if not app._w_table.loading:
                    break
            return mid, app._w_table.loading

    mid, after = asyncio.run(drive())
    assert mid, "overlay must be up while the worker runs"
    assert not after, "overlay must clear when results land"


def test_index_monitor_busy_frames_pilot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A2/A3: claimed rows animate with ported FluxFrames while the
    checker runs, the note line carries phase + elapsed, and verdicts
    release rows and restore the hint."""
    pytest.importorskip("textual")
    import asyncio
    import sys

    import helpers.misc.search_tui_app as appmod
    from textual.widgets import DataTable, Static

    argv = [sys.executable, "-c", "import time; time.sleep(0.35)"]
    monkeypatch.setattr(appmod, "index_check_argv", lambda name: argv)

    app = appmod.SearchApp("", "scripts", 10)  # no auto query: monitor is the subject

    async def drive() -> tuple[list[str], str, list[str], object]:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.action_monitor()
            await pilot.pause(0.3)
            mon = app.screen
            assert isinstance(mon, appmod.IndexMonitor)  # narrows ty (modal attrs)
            mon.action_check_all()
            mon._spin()  # deterministic frame write — no timer wait needed
            table = mon.query_one("#idx-table", DataTable)
            states = [str(table.get_row_at(i)[3]) for i in range(3)]
            note = str(mon.query_one("#idx-note", Static).content)
            for _ in range(80):
                await pilot.pause(0.1)
                if not mon._busy:
                    break
            final = [str(table.get_row_at(i)[3]) for i in range(3)]
            note_after = mon.query_one("#idx-note", Static).content
            return states, note, final, note_after

    states, note, final, note_after = asyncio.run(drive())
    frames = set(appmod.FLUX_CLASSIC)
    assert all(s[0] in frames and "…" in s for s in states), states
    assert "elapsed" in note, note
    assert final == ["fresh"] * 3, final
    assert note_after == appmod._IDX_NOTE_HINT


def test_index_monitor_double_refresh_guard_pilot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A4: a second R press in the same pump cycle must not double-spawn
    rebuild workers (parallel embedder rebuilds spike RAM/CPU — the
    sequential-by-design invariant)."""
    pytest.importorskip("textual")
    import asyncio
    import sys

    import helpers.misc.search_tui_app as appmod

    argv = [sys.executable, "-c", "import time; time.sleep(0.35)"]
    calls: list[str] = []

    def counting_refresh(name: str) -> list[str]:
        calls.append(name)
        return argv

    monkeypatch.setattr(appmod, "index_refresh_argv", counting_refresh)
    monkeypatch.setattr(appmod, "index_check_argv", lambda name: argv)

    app = appmod.SearchApp("", "scripts", 10)

    async def drive() -> list[str]:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.action_monitor()
            await pilot.pause(0.3)
            mon = app.screen
            assert isinstance(mon, appmod.IndexMonitor)  # narrows ty (modal attrs)
            mon.action_refresh_all()
            mon.action_refresh_all()  # same pump cycle — must be a no-op
            for _ in range(120):
                await pilot.pause(0.1)
                if not mon._busy:
                    break
            return list(calls)

    assert sorted(asyncio.run(drive())) == ["docs", "notes", "scripts"]


_VERIFY_RUNS_FIXTURE = (
    "# FinData Knowledge Graph — Notes Verification Report\n"
    "\n"
    "**Generated:** 2026-09-20 09:35:20  ·  **Project Root:** `/r`\n"
    "\n"
    "| Metric | Value |\n"
    "|---|---|\n"
    "| Total Files Checked | 1233 |\n"
    "| Errors | 0 |\n"
    "\n"
    "✅ All notes passed verification (no errors).\n"
    "\n"
    "# FinData Knowledge Graph — Notes Verification Report\n"
    "\n"
    "**Generated:** 2026-09-21 10:00:00  ·  **Project Root:** `/r`\n"
    "\n"
    "| Metric | Value |\n"
    "|---|---|\n"
    "| Total Files Checked | 1240 |\n"
    "| Errors | 1 |\n"
    "| Warnings | 2 |\n"
    "\n"
    "## ERROR issues (1)\n"
    "\n"
    "### frontmatter (1)\n"
    "\n"
    "- findata/A.md: missing title\n"
)


def test_report_where_label() -> None:
    assert report_where_label("outputs/qa_report.md", 6098) == "qa:6098"
    assert report_where_label("outputs/qa_report.md", None) == "qa"
    assert (
        report_where_label("outputs/wt/graph_algos/outputs/qa_report.md", 12) == "graph_algos/qa:12"
    )
    assert report_where_label("outputs/database_integrity_report.md", 3) == "integrity:3"


def test_integrity_verdict() -> None:
    """Colors follow the ACTUAL verdict, not the declared level: an
    ERROR-level section with errors=0 is green; warnings>0 is yellow."""
    from helpers.misc.search_tui import integrity_verdict

    ok = integrity_verdict("ERROR", "total=11663 unknown_type=0 -> errors=0")
    assert ok == "OK"
    bad = integrity_verdict("ERROR", "total=10 orphaned=3 -> errors=3")
    assert bad == "ERROR"
    warn = integrity_verdict("WARNING", "similar_pairs=157 -> warnings=157")
    assert warn == "WARNING"
    snapshot = integrity_verdict("WARNING", "entity counts: company=6203, institution=207")
    assert snapshot == "OK"  # counter-less advisory snapshot reads as OK


def test_report_files_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Main copies first, then each outputs/wt/<name>/outputs copy."""
    import helpers.misc.search_tui as tui

    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "qa_report.md").write_text(
        "# make qa — gate report\n", encoding="utf-8"
    )
    wt = tmp_path / "outputs" / "wt" / "graph_algos" / "outputs"
    wt.mkdir(parents=True)
    (wt / "qa_report.md").write_text("# make qa — gate report\n", encoding="utf-8")
    (wt / "perf_report.md").write_text("# x\n", encoding="utf-8")
    monkeypatch.setattr(tui, "REPO_ROOT", tmp_path)
    assert tui.report_files() == [
        ("qa", "outputs/qa_report.md", "gate"),
        ("qa", "outputs/wt/graph_algos/outputs/qa_report.md", "gate"),
        ("perf", "outputs/wt/graph_algos/outputs/perf_report.md", "perf"),
    ]


def test_run_reports_lists_worktree_copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import helpers.misc.search_tui as tui

    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "qa_report.md").write_text(_GATE_TIMED_FIXTURE, encoding="utf-8")
    wt = tmp_path / "outputs" / "wt" / "graph_algos" / "outputs"
    wt.mkdir(parents=True)
    (wt / "qa_report.md").write_text(_GATE_TIMED_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(tui, "REPO_ROOT", tmp_path)
    hits, status = tui._run_reports("", 50)
    titles = [h.title for h in hits if h.kind == "overview"]
    assert any(t.startswith("qa — ") for t in titles), titles
    assert any(t.startswith("qa@graph_algos — ") for t in titles), titles
    assert "qa@graph_algos" in status


def test_parse_verify_runs() -> None:
    """Verify history: every appended run, its metrics and its issues."""
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(_VERIFY_RUNS_FIXTURE)
        path = Path(f.name)
    from helpers.misc.search_tui import VerifyIssue

    try:
        runs = parse_verify_runs(path)
        assert len(runs) == 2
        assert runs[0].timestamp == "2026-09-20 09:35:20"
        assert (runs[0].total_files, runs[0].errors, runs[0].warnings) == (1233, 0, 0)
        assert runs[0].issues == ()
        assert runs[1].timestamp == "2026-09-21 10:00:00"
        assert (runs[1].total_files, runs[1].errors, runs[1].warnings) == (1240, 1, 2)
        assert runs[1].issues == (VerifyIssue("frontmatter", "findata/A.md: missing title"),)
    finally:
        path.unlink()


def test_report_screen_tree_runs_pilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B2–B4: V lists the run HISTORY per report as collapsed tree nodes
    (integration + verify included) and scopes to the row's file — a
    worktree copy shows only its own runs."""
    pytest.importorskip("textual")
    import asyncio

    import helpers.misc.search_tui as tui
    import helpers.misc.search_tui_app as appmod
    from textual.widgets import Tree

    out = tmp_path / "outputs"
    out.mkdir()
    integ = _GATE_TIMED_FIXTURE.replace(
        "# make qa — gate report", "# make integration — gate report"
    )
    (out / "integration_report.md").write_text(
        integ
        + integ.replace("2026-09-21 14:07:25", "2026-09-21 15:07:25").replace(
            "2026-09-21 14:05:19", "2026-09-21 15:05:19"
        ),
        encoding="utf-8",
    )
    (out / "verify_notes_report.md").write_text(_VERIFY_RUNS_FIXTURE, encoding="utf-8")
    wt = out / "wt" / "graph_algos" / "outputs"
    wt.mkdir(parents=True)
    (wt / "qa_report.md").write_text(_GATE_TIMED_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(tui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(appmod, "REPO_ROOT", tmp_path)

    app = appmod.SearchApp("", "reports", 10)

    async def drive() -> tuple[list[str], list[str], bool]:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.push_screen(appmod.ReportScreen())  # all report copies
            await pilot.pause(0.4)
            tree = app.screen.query_one("#rep-tree", Tree)
            labels = [str(c.label) for c in tree.root.children]
            collapsed = [c.is_expanded for c in tree.root.children]
            # enter expands the run node under the cursor (keyboard parity
            # with mouse: no double-toggle flash)
            tree.cursor_line = 0
            await pilot.press("enter")
            await pilot.pause(0.15)
            enter_expanded = tree.root.children[0].is_expanded
            app.pop_screen()
            await pilot.pause(0.1)
            app.push_screen(appmod.ReportScreen(rel="outputs/wt/graph_algos/outputs/qa_report.md"))
            await pilot.pause(0.4)
            wt_tree = app.screen.query_one("#rep-tree", Tree)
            wt_labels = [str(c.label) for c in wt_tree.root.children]
            assert not any(collapsed), "run nodes must start collapsed"
            return labels, wt_labels, enter_expanded

    labels, wt_labels, expanded = asyncio.run(drive())
    assert sum("integration ·" in lab for lab in labels) == 2, labels  # both runs
    assert sum("verify ·" in lab for lab in labels) == 2, labels
    assert len(wt_labels) == 1 and "qa@graph_algos" in wt_labels[0], wt_labels
    assert expanded, "enter must expand the run node (auto_expand, no double toggle)"


def test_report_rerun_busy_pilot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C2: the run tree carries a loading overlay while a rerun executes
    and drops it when the rerun lands."""
    pytest.importorskip("textual")
    import asyncio
    import sys

    import helpers.misc.search_tui as tui
    import helpers.misc.search_tui_app as appmod
    from textual.widgets import Tree

    out = tmp_path / "outputs"
    out.mkdir()
    (out / "qa_report.md").write_text(_GATE_TIMED_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(tui, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(appmod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        appmod,
        "report_rerun_argv",
        lambda name: [sys.executable, "-c", "import time; time.sleep(0.4)"],
    )

    app = appmod.SearchApp("", "reports", 10)

    async def drive() -> tuple[bool, bool]:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.push_screen(appmod.ReportScreen(rel="outputs/qa_report.md"))
            await pilot.pause(0.4)
            mon = app.screen
            assert isinstance(mon, appmod.ReportScreen)  # narrows ty (modal attrs)
            mon.query_one("#rep-tree", Tree).cursor_line = 0  # a run row
            mon.action_rerun_report()
            await pilot.pause(0.1)
            mid = mon.query_one("#rep-tree", Tree).loading
            for _ in range(60):
                await pilot.pause(0.1)
                if not mon.query_one("#rep-tree", Tree).loading:
                    break
            return mid, mon.query_one("#rep-tree", Tree).loading

    mid, after = asyncio.run(drive())
    assert mid, "overlay must ride the tree while the rerun runs"
    assert not after, "overlay must clear when the rerun lands"


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
        assert b.elapsed is None  # pre-timing header: no Elapsed field
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
        assert b.elapsed is None  # pre-timing header: no Elapsed field
        assert len(b.steps) == 2
        assert b.steps[0] == RunStep("integrity_check", 0.37, "\u2713 OK")
        assert "2 benchmarks" in b.summary
    finally:
        path.unlink()


_GATE_TIMED_FIXTURE = (
    "# make qa — gate report\n"
    "\n"
    "**Generated:** 2026-09-21 14:07:25  ·  **Started:** 2026-09-21 14:05:19  ·  "
    "**Elapsed:** 126.1s  ·  **Python:** 3.14.0  jobs=8\n"
    "\n"
    "| Step | Time (s) | Status |\n"
    "|---|---|---|\n"
    "| lint | 0.06 | ✓ OK |\n"
    "| **10/10 passed** | | |\n"
)

_PERF_TIMED_FIXTURE = (
    "# make perf — benchmark report\n"
    "\n"
    "**Generated:** 2026-09-21 14:35:56  ·  **Started:** 2026-09-21 14:35:20  ·  "
    "**Ended:** 2026-09-21 14:35:56  ·  **Elapsed:** 35.4s  ·  **Python:** 3.14.0\n"
    "\n"
    "| Benchmark | Time (s) | Budget | Status |\n"
    "|---|---|---|---|\n"
    "| integrity_check | 0.77 | 2.0s | ✓ OK |\n"
)


def test_parse_gate_report_timed_header() -> None:
    """Gate-latency era header: Started/Elapsed sit between Generated
    and Python — timestamp + jobs must still parse and Elapsed lands on
    the block (the 'unknown run' regression)."""
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(_GATE_TIMED_FIXTURE)
        path = Path(f.name)
    try:
        blocks = parse_gate_report(path)
        assert len(blocks) == 1
        b = blocks[0]
        assert b.timestamp == "2026-09-21 14:07:25"
        assert b.jobs == 8
        assert b.elapsed == 126.1
        assert "10/10 passed" in b.summary
    finally:
        path.unlink()


def test_parse_perf_report_timed_header() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(_PERF_TIMED_FIXTURE)
        path = Path(f.name)
    try:
        blocks = parse_perf_report(path)
        assert len(blocks) == 1
        b = blocks[0]
        assert b.timestamp == "2026-09-21 14:35:56"
        assert b.elapsed == 35.4
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
    assert all(s in status for s in ("qa", "perf", "integrity", "verify")), status
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
    assert "no report rows" in status


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
    argv_qa = report_rerun_argv("qa")
    assert argv_qa is not None and argv_qa[1] == "qa"


def test_report_screen_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V opens the comprehensive view; rows drill; esc closes (no rerun)."""
    pytest.importorskip("textual")
    import asyncio

    import helpers.misc.search_tui as tui
    import helpers.misc.search_tui_app as appmod
    from textual.widgets import Tree

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
            tree = app.screen.query_one("#rep-tree", Tree)
            n = len(tree.root.children)
            assert n > 0  # top-level = collapsed run nodes
            tree.cursor_line = 0
            await pilot.pause(0.2)
            detail = app.screen.query_one("#rep-detail").lines
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not isinstance(app.screen, appmod.ReportScreen)
            return n, len(detail)

    n, detail_lines = asyncio.run(drive())
    assert n == 3  # 2 qa runs + 1 verify run; steps/issues nest under runs
    assert detail_lines > 0


# ---------------------------------------------------------------- db screen


def _fixture_root(tmp_path: Path) -> Path:
    """tmp repo layout: memory/research.db (sqlite) + memory/data/sources.duckdb."""
    mem = tmp_path / "memory"
    (mem / "data").mkdir(parents=True)
    conn = sqlite3.connect(mem / "research.db")
    conn.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE VIEW v_entities AS SELECT id, name FROM entities")
    conn.executemany("INSERT INTO entities (name) VALUES (?)", [(f"n{i}",) for i in range(210)])
    conn.commit()
    conn.close()
    duckdb = pytest.importorskip("duckdb")
    dconn = duckdb.connect(str(mem / "data" / "sources.duckdb"))
    dconn.execute("CREATE TABLE listings (sym VARCHAR, px DOUBLE)")
    dconn.execute("INSERT INTO listings VALUES ('A', 1.0), ('B', 2.0)")
    dconn.execute("CREATE VIEW vw_list AS SELECT * FROM listings")
    dconn.close()
    return tmp_path


def test_db_preview_sql_quotes_identifiers() -> None:
    assert db_preview_sql("entities") == 'SELECT * FROM "entities" LIMIT 200'
    assert db_preview_sql("entities", "name", limit=10) == 'SELECT "name" FROM "entities" LIMIT 10'
    assert db_preview_sql('we"ird', 'c"ol') == 'SELECT "c""ol" FROM "we""ird" LIMIT 200'


def test_db_completions_cover_keywords_tables_columns(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    words = db_completions("research", root)
    assert "SELECT" in words and "entities" in words
    assert "entities.name" in words and "name" in words
    assert words.index("SELECT") < words.index("entities") < words.index("entities.name")


def test_db_store_path_resolves_and_rejects() -> None:
    assert db_store_path("research").name == "research.db"
    try:
        db_store_path("nope")
    except ValueError as e:
        assert "known:" in str(e)
    else:  # pragma: no cover - must raise
        raise AssertionError("db_store_path accepted an unknown store")


def test_db_schema_sqlite_lists_tables_views_columns(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    tables = {t.name: t for t in db_schema("research", root)}
    assert tables["entities"].kind == "table"
    assert [c.name for c in tables["entities"].columns] == ["id", "name"]
    assert tables["v_entities"].kind == "view"


def test_db_run_sqlite_select_and_errors(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    ok = db_run("research", "SELECT id, name FROM entities ORDER BY id LIMIT 2", root=root)
    assert ok.error is None and ok.columns == ["id", "name"] and len(ok.rows) == 2
    bad = db_run("research", "SELECT * FRM entities", root=root)
    assert bad.error is not None and not bad.rows
    write = db_run("research", "CREATE TABLE t_x (x)", root=root)
    assert write.error is not None and "readonly" in write.error
    empty = db_run("research", "   ", root=root)
    assert empty.error == "empty query"


def test_db_run_sqlite_row_cap(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    res = db_run("research", "SELECT id FROM entities", root=root)
    assert res.error is None and res.truncated and len(res.rows) == 200


def test_db_run_sqlite_timeout_aborts(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    res = db_run(
        "research",
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c LIMIT 50000000)"
        " SELECT count(*) FROM c",
        root=root,
        timeout_ms=50,
    )
    assert res.error is not None and "interrupted" in res.error


def test_db_schema_run_duckdb(tmp_path: Path) -> None:
    pytest.importorskip("duckdb")
    root = _fixture_root(tmp_path)
    tables = {t.name: t for t in db_schema("sources", root)}
    assert tables["listings"].kind == "table"
    assert [c.name for c in tables["listings"].columns] == ["sym", "px"]
    assert tables["vw_list"].kind == "view"
    ok = db_run("sources", "SELECT sym FROM listings ORDER BY sym", root=root)
    assert ok.error is None and [r[0] for r in ok.rows] == ["A", "B"]
    write = db_run("sources", "CREATE TABLE t_x (x INTEGER)", root=root)
    assert write.error is not None and "read-only" in write.error


def test_db_complete_matches_prefix_skips_exact() -> None:
    words = ["SELECT", "FROM", "WHERE", "LIMIT", "entities", "entities.name", "name"]
    assert db_complete(words, "ent") == "entities"
    assert db_complete(words, "entities.n") == "entities.name"
    assert db_complete(words, "SEL") == "SELECT"
    assert db_complete(words, "SELECT") is None  # exact match completes nothing
    assert db_complete(words, "entities.name") is None  # qualified exact
    assert db_complete(words, "nope.n") is None  # unknown table head
    assert db_complete(words, "") is None
    assert db_complete(words, "xyz") is None
    assert db_complete(words, '"ent') == "entities"  # quoted fragment


def test_db_match_words_lists_options() -> None:
    words = ["SELECT", "FROM", "entities", "entities.name", "entities.id", "name"]
    assert db_match_words(words, "ent") == ["entities", "entities.name", "entities.id"]
    assert db_match_words(words, "entities.") == ["entities.name", "entities.id"]
    assert db_match_words(words, "SELECT") == []
    assert db_match_words(words, "") == []


def test_db_history_roundtrip_dedupe_cap(tmp_path: Path) -> None:
    from helpers.misc.search_tui import append_db_history, load_db_history

    p = tmp_path / "h.txt"
    assert load_db_history("research", p) == []
    append_db_history("research", "SELECT 1", p)
    append_db_history("research", "SELECT 1", p)  # repeat-of-last skipped
    append_db_history("research", "   ", p)  # blank skipped
    append_db_history("research", "SELECT 2", p)
    assert load_db_history("research", p) == ["SELECT 1", "SELECT 2"]
    for i in range(210):
        append_db_history("research", f"SELECT {i}", p)
    assert len(load_db_history("research", p)) == 200


def test_db_filter_rows_fuzzy(tmp_path: Path) -> None:
    rows: list[tuple[object, ...]] = [("alpha", 1), ("beta", 2), ("gamma", 3)]
    assert db_filter_rows(rows, "") == rows
    assert db_filter_rows(rows, "alp") == [("alpha", 1)]
    assert db_filter_rows(rows, "am") == [("gamma", 3)]  # subsequence, not substring
    assert db_filter_rows(rows, "zzz") == []
    assert db_filter_rows(rows, "2") == [("beta", 2)]


def test_db_screen_tab_completes_and_alt_d_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tab completes the editor token; alt+d opens the db screen while
    typing in the main query box; plain d stays gated (it is text)."""
    pytest.importorskip("textual")
    import helpers.misc.search_tui_app as appmod
    import helpers.misc.search_tui as tui
    from textual.widgets import Static

    _fixture_root(tmp_path)
    monkeypatch.setattr(appmod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tui, "REPO_ROOT", tmp_path)

    async def drive() -> tuple[str, bool, bool, str]:
        app = appmod.SearchApp("", "docs", 10)
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(40):
                await pilot.pause(0.05)
                if app._mounted:
                    break
            # plain d while typing is text, not a screen jump
            app.set_focus(app._w_input)
            await pilot.press("d")
            await pilot.pause(0.1)
            typed = app._w_input.value
            gated = not isinstance(app.screen, appmod.DbScreen)
            # alt+d opens from anywhere, even mid-word
            await pilot.press("alt+d")
            await pilot.pause(0.3)
            opened = isinstance(app.screen, appmod.DbScreen)
            # tab completes the editor token (TextArea, sql-highlighted)
            editor = app.screen.query_one("#db-sql", appmod.TextArea)
            editor.text = "SEL"
            editor.cursor_location = (0, 3)
            await pilot.pause(0.2)  # Changed → hints line lists options
            hints = app.screen.query_one("#db-hints", Static).content
            assert "SELECT" in str(hints)  # content is a RichRenderable union — coerce for `in`
            await pilot.press("tab")
            await pilot.pause(0.1)
            return typed, gated, opened, editor.text

    import asyncio

    typed, gated, opened, completed = asyncio.run(drive())
    assert typed == "d" and gated
    assert opened
    assert completed == "SELECT"


def test_db_screen_pilot_tree_run_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DbScreen against tmp fixture DBs (REPO_ROOT patched): tree fill,
    enter-to-run via worker, store switch, dismiss. No live stores."""
    pytest.importorskip("textual")
    import helpers.misc.search_tui_app as appmod
    import helpers.misc.search_tui as tui
    from textual.widgets import DataTable, Input, Static, Tree

    _fixture_root(tmp_path)
    monkeypatch.setattr(appmod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tui, "REPO_ROOT", tmp_path)
    _real_db_run = appmod.db_run

    def _slow_db_run(store: str, sql: str):  # noqa: ANN001, ANN202
        import time as _t

        _t.sleep(0.4)
        return _real_db_run(store, sql)

    monkeypatch.setattr(appmod, "db_run", _slow_db_run)
    app = appmod.SearchApp("", "docs", 10)

    async def drive() -> tuple[int, str, bool, bool, int, int, str, str, str, bool, bool, str]:
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(40):
                await pilot.pause(0.05)
                if app._mounted:
                    break
            app.action_db_screen()  # direct: "d" is typing-gated like V/i/t
            await pilot.pause(0.3)
            assert isinstance(app.screen, appmod.DbScreen)
            tree = app.screen.query_one("#db-tree", Tree)
            kids = len(tree.root.children)
            first = tree.root.children[0].label.plain if kids else ""
            sql_highlight = app.screen.query_one("#db-sql", appmod.TextArea).language == "sql"
            # vim-style: j moves the tree cursor
            app.screen.set_focus(tree)
            await pilot.pause(0.1)
            line0 = tree.cursor_line
            await pilot.press("j")
            await pilot.pause(0.1)
            vim_moved = tree.cursor_line != line0
            # F5 runs the editor text (real key path, not the action)
            app.screen.query_one("#db-sql", appmod.TextArea).text = "SELECT name FROM entities"
            await pilot.press("f5")
            await pilot.pause(0.1)
            db_mid_busy = app.screen.query_one("#db-results", DataTable).loading  # C1
            for _ in range(40):
                await pilot.pause(0.05)
                if app.screen.query_one("#db-results", DataTable).row_count > 0:
                    break
            db_after_busy = app.screen.query_one("#db-results", DataTable).loading
            assert db_mid_busy and not db_after_busy
            rows = app.screen.query_one("#db-results", DataTable).row_count
            status = app.screen.query_one("#db-status", Static).content
            # filter narrows the loaded working set (live-apply on change)
            app.screen.query_one("#db-filter", Input).value = "n20"
            await pilot.pause(0.2)
            shown = app.screen.query_one("#db-results", DataTable).row_count
            # inspect shows the row as col: value lines
            app.screen.action_toggle_detail()
            await pilot.pause(0.1)
            detail = app.screen.query_one("#db-detail", Static).content
            assert detail.startswith("name: n")  # inspect shows col: value lines
            # history: the run above was recorded; ctrl+p recalls it
            app.screen.action_history_older()
            recalled = app.screen.query_one("#db-sql", appmod.TextArea).text
            # guard: single keys are text while the editor has focus
            app.screen.set_focus(app.screen.query_one("#db-sql", appmod.TextArea))
            await pilot.pause(0.1)
            app.screen.action_switch_store()
            guarded = app.screen._store
            app.screen.set_focus(tree)
            await pilot.pause(0.1)
            app.screen.action_switch_store()
            await pilot.pause(0.1)
            store = app.screen._store
            # resize runs with tree focused, then dismiss
            app.screen.action_widen_tree()
            app.screen.action_narrow_tree()
            wide_ok = app.screen._tree_fr == 3
            app.screen.action_grow_sql()
            await pilot.press("equals_sign")  # real key path (was "equals": dead)
            await pilot.pause(0.1)
            app.screen.action_shrink_sql()
            assert app.screen._sql_h == 6  # 5 +1 +1 -1
            # footer shows modal keys only — no search-lane leftovers
            footer_keys = set(app.screen.active_bindings)
            assert "m" not in footer_keys and "1" not in footer_keys
            assert {"s", "escape", "q"} <= footer_keys
            # esc is a safe harbor (tree), alt+q closes — no accidental exit
            app.screen.set_focus(app.screen.query_one("#db-sql", appmod.TextArea))
            await pilot.pause(0.1)
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert isinstance(app.screen, appmod.DbScreen)
            assert isinstance(app.screen.focused, Tree)
            await pilot.press("alt+q")
            await pilot.pause(0.1)
            return (
                kids,
                first,
                sql_highlight,
                vim_moved,
                rows,
                shown,
                recalled,
                guarded,
                store,
                wide_ok,
                isinstance(app.screen, appmod.DbScreen),
                status,
            )

    import asyncio

    vals = asyncio.run(drive())
    (
        kids,
        first,
        sql_highlight,
        vim_moved,
        rows,
        shown,
        recalled,
        guarded,
        store,
        wide_ok,
        still_open,
        status,
    ) = vals
    assert kids == 2  # entities + v_entities
    assert first.startswith("entities (2)")
    assert sql_highlight
    assert vim_moved
    assert rows == 200  # 210-row table, 200-row cap
    assert 0 < shown < 200  # fuzzy filter narrowed the working set
    assert recalled == "SELECT name FROM entities"
    assert guarded == "research"  # switch ignored while typing
    assert "200 rows" in status
    assert store == "sources"
    assert wide_ok  # widen then narrow returns to 3
    assert not still_open
