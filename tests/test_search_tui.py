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
