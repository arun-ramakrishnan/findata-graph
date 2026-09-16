#!/usr/bin/env python3
"""Textual front-end for search_tui (the App half; adapters live next door).

Imported lazily by ``helpers/misc/search_tui.py::main`` so the adapter
module — and every test over it — works without the optional ``tui``
extra installed. Layout follows the witr/htop template: title bar,
query line, lane tabs, results table | preview pane, status line,
keybind footer.

Interaction model (operator-directed 2026-09-16): search fires on
``enter`` only — the query line never runs lanes under your fingers
while you pick one of the five tabs. Markdown hits preview rendered
(``rich.markdown``), code hits preview as a numbered context slice.
``i`` opens the index monitor: per-index store age + deep ``--check``
freshness, ``r`` refreshes the selected index, ``R`` refreshes all
(refreshing ``notes`` touches research.db — the monitor says so, run
``make snapshot`` before the next qa).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from textual import work
from textual.app import App, ComposeResult

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static, Tab, Tabs

from helpers.misc.search_tui import (
    INDEXES,
    LANES,
    REPO_ROOT,
    Hit,
    call_chain,
    index_ages,
    index_check_argv,
    index_refresh_argv,
    index_rows,
    open_command,
    run_lane,
)


import datetime
from pathlib import Path


def _build_stamp() -> str:
    return datetime.datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%H:%M:%S")


BUILD_STAMP = _build_stamp()


_EVLOG = Path(tempfile.gettempdir()) / "search_tui.log"


def _evlog(msg: str) -> None:
    """Append one event line to the temp-dir search_tui.log — never raises."""
    try:
        with _EVLOG.open("a") as fh:
            fh.write(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]} {msg}\n")
    except Exception:  # noqa: S110 — logging must never kill the app
        pass


_CONTEXT = 14  # preview lines either side of the hit line


def _fmt_age(age: object) -> str:
    if not isinstance(age, int):
        return "—"
    a = age
    return (
        f"{a // 86400}d{a % 86400 // 3600}h" if a >= 86400 else f"{a // 3600}h{a % 3600 // 60:02d}m"
    )


class IndexMonitor(ModalScreen[None]):
    """search-fresh surfaced: store ages, deep checks, targeted rebuilds.

    Refreshes run SEQUENTIALLY (one embedding model at a time — parallel
    rebuilds spiked RAM/CPU). Every rebuild is followed by a deep check so
    the state column settles on fresh/STALE, never a dangling "rebuilt".
    """

    BINDINGS = [
        ("c", "check_one", "deep-check selected"),
        ("C", "check_all", "deep-check all"),
        ("r", "refresh_one", "rebuild selected"),
        ("R", "refresh_all", "rebuild ALL"),
        ("escape", "dismiss_monitor", "close"),
        ("q", "dismiss_monitor", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="idx-box"):
            yield DataTable(id="idx-table", cursor_type="row", zebra_stripes=True)
            yield Static(
                "check = is the index current · rebuild = re-index it (notes writes research.db)",
                id="idx-note",
            )
            yield Static(
                "↑↓ select · c check · C check all · r rebuild · R rebuild all · esc close",
                id="idx-keys",
            )
            yield Footer()

    def on_mount(self) -> None:
        """Cheap mount: ages only. Deep checks are on demand (c/C) —
        rebuild_note_search --check re-verifies 16,520 notes."""
        table = self.query_one("#idx-table", DataTable)
        table.border_title = "index monitor — the three search-fresh sidecars"
        table.add_column("index", width=8)
        table.add_column("store", width=26)
        table.add_column("age", width=7)
        table.add_column("state", width=13)
        self._fill(index_rows(REPO_ROOT))

    def _fill(self, rows: list[dict[str, object]]) -> None:
        table = self.query_one("#idx-table", DataTable)
        table.clear()
        for i, row in enumerate(rows):
            table.add_row(
                str(row["index"]),
                str(row["store"]),
                _fmt_age(row["age"]),
                "—",
                key=str(i),
            )

    def _selected_index(self) -> str | None:
        table = self.query_one("#idx-table", DataTable)
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return None
        return str(row[0]) if row else None

    def _set_state(self, name: str, verdict: str) -> None:
        table = self.query_one("#idx-table", DataTable)
        for i in range(table.row_count):
            if str(table.get_row_at(i)[0]) == name:
                table.update_cell_at(Coordinate(i, 3), verdict)  # type: ignore[arg-type]
                break
        if name == "notes":
            self.query_one("#idx-note", Static).set_classes("stale" if verdict == "STALE" else "")

    def action_check_one(self) -> None:
        name = self._selected_index()
        if name is not None:
            self._check_worker([name])

    def action_check_all(self) -> None:
        self._check_worker([n for n, _, _ in INDEXES])

    def action_refresh_one(self) -> None:
        name = self._selected_index()
        if name is not None:
            self._refresh_worker([name])

    def action_refresh_all(self) -> None:
        self._refresh_worker([n for n, _, _ in INDEXES])

    @work(thread=True, exclusive=False)
    def _check_worker(self, names: list[str]) -> None:
        for name in names:
            self.app.call_from_thread(self._set_state, name, "checking…")
            argv = index_check_argv(name)
            verdict = "no checker"
            if argv is not None:
                try:
                    r = subprocess.run(  # noqa: S603 — repo-local script, fixed argv
                        argv, capture_output=True, text=True, timeout=900, cwd=REPO_ROOT
                    )
                    verdict = "fresh" if r.returncode == 0 else "STALE"
                except OSError, subprocess.TimeoutExpired:
                    verdict = "check failed"
            self.app.call_from_thread(self._set_state, name, verdict)

    @work(thread=True, exclusive=False)
    def _refresh_worker(self, names: list[str]) -> None:
        """Rebuild-then-check, SEQUENTIALLY (one embedder at a time)."""
        for name in names:
            argv = index_refresh_argv(name)
            if argv is None:
                continue
            self.app.call_from_thread(self._set_state, name, "rebuilding…")
            try:
                subprocess.run(  # noqa: S603 — repo-local script, fixed argv
                    argv, capture_output=True, text=True, timeout=3600, cwd=REPO_ROOT
                )
            except OSError, subprocess.TimeoutExpired:
                self.app.call_from_thread(self._set_state, name, "rebuild FAILED")
                continue
            self.app.call_from_thread(self._set_state, name, "checking…")
            chk = index_check_argv(name)
            verdict = "fresh"
            if chk is not None:
                try:
                    r = subprocess.run(  # noqa: S603
                        chk, capture_output=True, text=True, timeout=900, cwd=REPO_ROOT
                    )
                    verdict = "fresh" if r.returncode == 0 else "STALE"
                except OSError, subprocess.TimeoutExpired:
                    verdict = "check failed"
            self.app.call_from_thread(self._set_state, name, verdict)
            self.app.call_from_thread(self._reload_ages)

    def _reload_ages(self) -> None:
        rows = index_rows(REPO_ROOT)
        table = self.query_one("#idx-table", DataTable)
        for i, row in enumerate(rows):
            table.update_cell_at(Coordinate(i, 2), _fmt_age(row["age"]))  # type: ignore[arg-type]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()  # enter here must NOT open the underlying hit behind the modal

    def action_dismiss_monitor(self) -> None:
        app = self.app
        if isinstance(app, SearchApp):
            app._w_status.update(index_ages(REPO_ROOT))
        self.dismiss()


class SearchApp(App[None]):
    """One screen over every search lane the repo keeps fresh."""

    TITLE = "search — repo knowledge front door"
    CSS = """
    Screen { layout: vertical; }
    #query { border: round $panel; height: 3; }
    #query:focus { border: round $accent; }
    #lanes { height: 3; }
    #body { height: 1fr; }
    #results { border: round $panel; width: 3fr; scrollbar-color: #55555c #26262b; }
    #results:focus { border: round $accent; }
    #preview { border: round $panel; width: 2fr; scrollbar-size-vertical: 0; }
    #preview:focus { border: round $accent; }
    #results { scrollbar-color: $panel-lighten-1 $panel; }
    #lanes:focus { border: tall $accent; }
    #status { height: 1; color: $text-muted; }
    IndexMonitor { align: center middle; }
    #idx-box { border: round $accent; background: $surface; width: 92; height: auto; padding: 0 1; }
    #idx-table { height: auto; }
    #idx-keys { color: $text-muted; }
    #idx-note { color: $warning; }
    #idx-note.stale { color: $error; text-style: bold; }
    """

    BINDINGS = [
        Binding("tab", "focus_cycle", "next pane", priority=True),
        Binding("shift+tab", "focus_cycle_back", "prev pane", priority=True),
        ("m", "toggle_mode", "kw/semantic"),
        ("h", "call_chain", "call chain"),
        ("b", "chain_back", "back"),
        ("i", "monitor", "indexes"),
        ("e", "editor", "editor"),
        ("y", "copy_path", "y: path"),
        ("Y", "copy_loc", "Y: path:line"),
        ("/", "focus_query", "query"),
        ("q", "quit", "quit"),
        ("ctrl+c", "quit", "quit"),
        ("1", "lane('docs')", "docs"),
        ("2", "lane('scripts')", "scripts"),
        ("3", "lane('notes')", "notes"),
        ("4", "lane('code')", "code"),
        ("5", "lane('literal')", "rg"),
        ("escape", "blur_to_results", "to results"),
    ]

    def __init__(self, query: str, lane: str, limit: int) -> None:
        super().__init__()
        self._lane = lane if lane in LANES else "docs"
        self._limit = limit
        self._hits: list[Hit] = []
        self._gen = 0
        self._tabs_armed = False
        self._mode = "hybrid"  # or "bm25" — key m toggles (docs/scripts lanes)
        self._terms: list[str] = []
        self._chain_stack: list[tuple[list[Hit], str, str | None]] = []
        self._chain_sym: str | None = None
        self._raw_status = ""
        self._mounted = False
        self._initial_query = query

    # -- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False, name=f"search_tui · build {BUILD_STAMP} · pid {os.getpid()}")
        yield Input(
            value=self._initial_query,
            placeholder="query — pick a lane (tabs / 1-5), type, press enter · verbs: callers: impact: grep: recall:",
            id="query",
        )
        yield Tabs(id="lanes")
        with Horizontal(id="body"):
            yield DataTable(id="results", cursor_type="row", zebra_stripes=True)
            # wrap=True + small min_width: renderables (Markdown) render at
            # the PANE width — min_width=78 forced 78 cols and clipped.
            yield RichLog(
                id="preview", markup=True, wrap=True, max_lines=4000, min_width=1, auto_scroll=False
            )
        yield Static("", id="status")
        yield Footer()

    def on_key(self, event) -> None:  # noqa: ANN001 — Textual Key event
        if event.character or event.key in ("enter", "tab", "shift+tab", "escape"):
            _evlog(
                f"key {event.key!r} focused={type(self.focused).__name__ if self.focused else None}"
            )

    async def on_mount(self) -> None:
        _evlog(f"mount build={BUILD_STAMP} pid={os.getpid()}")
        # Widget refs captured once: query_one is scoped to the ACTIVE
        # screen, so late worker callbacks must not re-query while the
        # index-monitor modal is up.
        self._w_input = self.query_one("#query", Input)
        self._w_tabs = self.query_one("#lanes", Tabs)
        # await each add: add_tab's activation coroutine must settle before
        # the requested lane is set, or the strip flips back to tab[0]
        for _name in LANES:
            await self._w_tabs.add_tab(Tab(_name, id=f"lane-{_name}"))
        self._w_table = self.query_one("#results", DataTable)
        self._w_log = self.query_one("#preview", RichLog)
        self._w_status = self.query_one("#status", Static)
        table = self._w_table
        # Title/section is the informative column — it gets the max width
        # (set per-populate from the pane size); path and score stay narrow.
        self._w_title_key = table.add_column("title / section", key="title")
        table.add_column("where", width=32)
        table.add_column("score", width=6)
        self._w_tabs.active = f"lane-{self._lane}"
        # arm on a short timer: mount-time auto-activation churn lands within
        # ~0.1s but NOT always inside the awaited add_tab calls — a direct arm
        # here let churn switch lanes (2026-09-16). A dropped timer call also
        # left clicks dead for a full arc — keep this line!
        self.set_timer(0.75, self._arm_tabs)
        self._w_status.update(f"ready — pick a lane, type, press enter  ·  {index_ages(REPO_ROOT)}")
        self.set_focus(self._w_input)
        try:  # diff-flavoured palette (GitHub dark): red headings are unreadable
            from rich.theme import Theme

            self.console.push_theme(
                Theme(
                    {
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
                    }
                )
            )
        except Exception:  # noqa: S110 - cosmetic only
            pass
        self._mounted = True
        if self._initial_query:
            self._start_query()

    # -- lanes ------------------------------------------------------------

    def _arm_tabs(self) -> None:
        self._tabs_armed = True
        # the late auto-activation may have flipped the widget's underline
        # even though the lane guard blocked the switch — re-assert
        try:
            self._w_tabs.active = f"lane-{self._lane}"
        except ValueError:  # same mid-flux guard
            pass

    def _set_lane(self, lane: str) -> None:
        self._lane = lane
        try:
            self._w_tabs.active = f"lane-{lane}"
        except ValueError:  # tab list mid-flux (late mount churn) — lane state stays authoritative
            pass

    def _tab_lane(self, tab: Tab | None) -> str | None:
        if tab is None or tab.id is None:
            return None
        lane = tab.id.removeprefix("lane-")
        return lane if lane in LANES else None

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        # Tabs auto-activates its first tab from Tab.Mounted — AFTER
        # on_mount set the requested lane. Ignore activations until the
        # arming timer fires; user clicks land after it.
        _evlog(f"tabs_activated tab={event.tab.id if event.tab else None} armed={self._tabs_armed}")
        if not self._tabs_armed:
            return
        lane = self._tab_lane(event.tab)
        if lane is not None and lane != self._lane:
            self.action_lane(lane)

    # -- querying (enter-to-search) ---------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._w_status.update(
            f"mode {self._mode} — enter to search {self._lane}  ·  {index_ages(REPO_ROOT)}"
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        _evlog(f"submit lane={self._lane} q={self._w_input.value!r} focus={self.focused!r}")
        self._start_query()
        self.set_focus(self._w_table)

    def _start_query(self) -> None:
        q = self._w_input.value
        _evlog(f"start lane={self._lane} q={q!r} mode={self._mode}")
        self._chain_stack.clear()
        self._chain_sym = None
        self._terms = [t for t in re.split(r"\W+", q) if len(t) > 2]
        self._gen += 1
        self._w_status.update(f"querying {self._lane} ({self._mode})…")
        self._query_worker(q, self._lane, self._limit, self._gen, self._mode)

    @work(thread=True, exclusive=True)
    def _query_worker(self, q: str, lane: str, limit: int, gen: int, mode: str) -> None:
        try:
            hits, status = run_lane(lane, q, limit, mode)
        except Exception as e:  # noqa: BLE001 — surface, never crash the thread
            import traceback

            _evlog(f"worker CRASH lane={lane}: {e}\n{traceback.format_exc()}")
            hits, status = [], f"lane crashed: {e}"
        self.call_from_thread(self._apply_results, hits, status, gen)

    def _apply_results(self, hits: list[Hit], status: str, gen: int) -> None:
        _evlog(f"apply gen={gen} cur={self._gen} n={len(hits)} {status[:50]}")
        if gen != self._gen:
            return  # a newer query already landed
        self._raw_status = status
        first_results = not self._hits and bool(hits)
        self._hits = hits
        table = self._w_table
        table.clear()
        # title/section takes the pane beyond path + score; path keeps its
        # tail (filename + :line); score stays a tiny right column
        try:
            title_w = max(int(table.container_size.width) - 32 - 6 - 4, 20)
        except Exception:
            title_w = 40
        self._w_table.columns[self._w_title_key].width = title_w
        for i, hit in enumerate(hits):
            where = f"{hit.path}{':' + str(hit.line) if hit.line else ''}"
            if len(where) > 30:
                where = "…" + where[-29:]
            score = "" if hit.score is None else f"{hit.score:.2f}"
            # first column = the informative text: title, else the matched
            # line (literal/rg rows), else the kind
            title = (hit.title or hit.snippet or hit.kind)[: title_w - 1]
            cell = Text(title)
            if self._terms:
                cell.highlight_words(self._terms, "black on #d29922", case_sensitive=False)
            table.add_row(cell, where, score, key=str(i))
        self._w_status.update(f"mode {self._mode} · {status}  ·  {index_ages(REPO_ROOT)}")
        if hits:
            # arrow-browsing on the strip keeps focus there; otherwise the
            # first landing (or an already-focused table) takes focus
            if isinstance(self.focused, DataTable) or (
                first_results and not isinstance(self.focused, Tabs)
            ):
                self.set_focus(table)
            table.move_cursor(row=0, animate=False)
        self._preview()

    # -- preview ---------------------------------------------------------

    def _selected(self) -> Hit | None:
        row = self._w_table.cursor_row
        if 0 <= row < len(self._hits):
            return self._hits[row]
        return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._preview()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter/mouse-click: chain rows drill into that symbol; other rows
        open as before."""
        if 0 <= event.cursor_row < len(self._hits):
            hit = self._hits[event.cursor_row]
            if hit.kind in ("caller", "callee") and hit.title:
                if hit.title == self._chain_sym:
                    self.notify(f"already showing {hit.title}")
                    return
                self._push_view()
                self._chain_sym = hit.title
                self._w_status.update(f"call chain: {hit.title}…")
                self._chain_worker(hit.title)
                return
        self._open()

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        self._preview()

    def _hit_line(self, n: int, text: str, is_hit: bool) -> Text:
        """Numbered gutter line with the search terms highlighted."""
        gutter = f"{n:>6}│ "
        t = Text(gutter + text)
        t.highlight_words(self._terms, "black on #d29922", case_sensitive=False)
        if is_hit:
            t.stylize("reverse", 0, len(gutter) + len(text))
        return t

    def _preview(self) -> None:
        log = self._w_log
        log.clear()
        hit = self._selected()
        if hit is None:
            return
        log.write(
            f"[bold]{escape(hit.path)}[/]"
            f"{':' + str(hit.line) if hit.line else ''}"
            f"  [dim]{escape(hit.section)}[/]"
        )
        p = REPO_ROOT / hit.path
        file_ok = p.exists() and p.is_file()
        if hit.snippet and not file_ok:
            # file-backed hits render the real content below; the snippet
            # (raw markdown source) is noise on top of it
            log.write("")
            log.write(escape(hit.snippet))
        if not file_ok:
            return
        lines = p.read_text(errors="replace").splitlines()
        pane_w = max(log.container_size.width - 2, 20)
        if hit.line is None:
            log.write("")
            if p.suffix == ".md":
                log.write(Markdown("\n".join(lines), code_theme="ansi_dark"), width=pane_w)
            else:
                log.write(f"[dim]({len(lines)} lines — enter opens it)[/dim]")
            log.scroll_to(y=0, animate=False)
            return
        lo = max(hit.line - _CONTEXT, 1)
        hi = min(hit.line + _CONTEXT, len(lines))
        log.write("")
        if p.suffix == ".md":
            # the hit line itself, numbered + highlighted, then rendered context
            hit_text = lines[hit.line - 1] if hit.line <= len(lines) else ""
            log.write(self._hit_line(hit.line, hit_text, is_hit=True))
            log.write("")
            log.write(Markdown("\n".join(lines[lo - 1 : hi]), code_theme="ansi_dark"), width=pane_w)
            log.scroll_to(y=0, animate=False)
            return
        for n in range(lo, hi + 1):
            log.write(self._hit_line(n, lines[n - 1], is_hit=(n == hit.line)))
        log.scroll_to(y=0, animate=False)

    # -- actions ---------------------------------------------------------

    def action_lane(self, lane: str) -> None:
        _evlog(f"action_lane {lane} (from lane {self._lane})")
        from_strip = isinstance(self.focused, Tabs)  # arrow-browsing lanes
        self._set_lane(lane)
        # clear the stale rows immediately — old-lane results must not sit
        # under a new lane's header while its query walks
        self._hits = []
        self._w_table.clear()
        self._w_log.clear()
        self._w_status.update(f"querying {lane} ({self._mode})…")
        self._start_query()
        if not from_strip:
            self.set_focus(self._w_table)

    def action_focus_query(self) -> None:
        self.set_focus(self._w_input)

    def action_monitor(self) -> None:
        self.push_screen(IndexMonitor())

    def _open(self, force_editor: bool = False) -> None:
        hit = self._selected()
        if hit is None:
            return
        cmd = open_command(hit, REPO_ROOT, force_editor=force_editor)
        if cmd is None:
            self.notify("no opener: markdown needs glow, code an editor", severity="warning")
            return
        try:
            with self.suspend():
                subprocess.run(cmd, check=False)  # noqa: S603 — resolved opener argv, no shell
        except Exception as exc:  # pragma: no cover - terminal-dependent
            self.notify(f"suspend failed ({exc}) — run manually: {' '.join(cmd)}", severity="error")
            return
        self.refresh(layout=True)
        self._preview()

    def action_editor(self) -> None:
        self._open(force_editor=True)

    def _copy(self, with_line: bool) -> None:
        hit = self._selected()
        if hit is None:
            return
        text = hit.path + (f":{hit.line}" if with_line and hit.line else "")
        try:
            self.copy_to_clipboard(text)
            self.notify(f"copied {text}")
            return
        except Exception:  # noqa: S110 — OSC52 best-effort; fallback follows
            pass
        import shutil

        for tool, args in (
            ("wl-copy", []),
            ("xclip", ["-selection", "clipboard"]),
            ("xsel", ["-ib"]),
            ("pbcopy", []),
        ):
            if shutil.which(tool):
                subprocess.run(  # noqa: S603 — clipboard tool by absolute path
                    [tool, *args], input=text, text=True, check=False
                )
                self.notify(f"copied via {tool}")
                return
        self.notify("no clipboard tool (OSC52 refused)", severity="warning")

    def action_copy_path(self) -> None:
        self._copy(False)

    def action_copy_loc(self) -> None:
        self._copy(True)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """Gate single-key actions: while typing in the query line they are text;
        on the tabs strip the Tabs widget owns the arrow keys itself."""
        if isinstance(self.focused, Input):
            return action in ("focus_query", "blur_to_results", "focus_cycle", "focus_cycle_back")
        if isinstance(self.focused, Tabs) and action in ("lane_next", "lane_prev"):
            return False
        return True

    def action_blur_to_results(self) -> None:
        if self._hits:
            self.set_focus(self._w_table)

    def action_focus_cycle(self) -> None:
        """Explicit pane order: query -> lane tabs -> results -> preview -> query.

        The generic focus_next skipped the tabs strip; the operator wants
        tab to reach the search-type selector directly.
        """
        focused = self.focused
        if isinstance(focused, Input):
            self.set_focus(self._w_tabs)
        elif isinstance(focused, Tabs):
            self.set_focus(self._w_table)
        elif isinstance(focused, DataTable):
            self.set_focus(self._w_log)
        else:
            self.set_focus(self._w_input)

    def _push_view(self) -> None:
        """Snapshot the current rows for `b` (chain back)."""
        self._chain_stack.append((self._hits, self._raw_status, self._chain_sym))

    def action_call_chain(self) -> None:
        """h: 1-hop call chain (callers + callees) for the selected symbol.

        Meaningful in the code lane; other lanes notify. Chain rows land in
        the results table — click/enter a caller/callee to drill deeper,
        `b` walks back through the chain stack.
        """
        if self._lane != "code":
            self.notify("call chain applies to code-lane hits", severity="warning")
            return
        hit = self._selected()
        if hit is None or not hit.title:
            self.notify("select a symbol row first")
            return
        sym = hit.title
        if sym == self._chain_sym:
            self.notify(f"already showing {sym}")
            return
        self._push_view()
        self._chain_sym = sym
        self._w_status.update(f"call chain: {sym}…")
        self._chain_worker(sym)

    @work(thread=True, exclusive=True)
    def _chain_worker(self, sym: str) -> None:
        try:
            chain = call_chain(sym)
        except Exception as e:  # noqa: BLE001 — surface, never crash the thread
            import traceback

            _evlog(f"chain CRASH {sym}: {e}\n{traceback.format_exc()}")
            self.call_from_thread(self.notify, f"call chain failed: {e}")
            return
        self.call_from_thread(self._show_chain, chain)

    def _show_chain(self, chain: dict) -> None:
        """Render the chain as table rows: section headers + caller/callee
        rows that preview on cursor and drill on click/enter."""
        sym = chain["of"]
        rows: list[Hit] = []
        for label, key in (("callers", "callers"), ("callees", "callees")):
            group = chain[key]
            rows.append(
                Hit(
                    path="",
                    line=None,
                    title=f"── {label} of {sym} ({len(group)}) ──",
                    kind="chain-header",
                )
            )
            rows.extend(
                Hit(
                    path=r.path,
                    line=r.line,
                    title=r.title or r.snippet[:40],
                    section=r.section,
                    snippet=r.snippet,
                    score=r.score,
                    lane=r.lane,
                    kind=label[:-1],
                )
                for r in group[:20]
            )
        status = (
            f"call chain of {sym} · {len(chain['callers'])} callers ·"
            f" {len(chain['callees'])} callees · click/enter drills · b back"
        )
        self._apply_results(rows, status, self._gen)

    def action_chain_back(self) -> None:
        """b: pop the previous view (search results or the prior chain)."""
        if not self._chain_stack:
            self.notify("nothing to go back to")
            return
        hits, status, sym = self._chain_stack.pop()
        self._chain_sym = sym
        self._apply_results(hits, status, self._gen)

    def action_toggle_mode(self) -> None:
        self._mode = "bm25" if self._mode == "hybrid" else "hybrid"
        self.notify(
            "match mode: "
            + (
                "keyword — every hit contains the words"
                if self._mode == "bm25"
                else "semantic (hybrid)"
            )
        )
        self._start_query()

    def action_focus_cycle_back(self) -> None:
        """Reverse pane order: query -> preview -> results -> tabs -> query."""
        focused = self.focused
        if isinstance(focused, Input):
            self.set_focus(self._w_log)
        elif isinstance(focused, Tabs):
            self.set_focus(self._w_input)
        elif isinstance(focused, DataTable):
            self.set_focus(self._w_tabs)
        else:
            self.set_focus(self._w_table)

    def action_lane_next(self) -> None:
        self._cycle_lane(1)

    def action_lane_prev(self) -> None:
        self._cycle_lane(-1)

    def _cycle_lane(self, step: int) -> None:
        """Arrow keys switch search type from the results/preview panes.

        In the query line arrows move the caret; on the tabs strip the
        Tabs widget owns them natively — check_action gates both away.
        """
        i = LANES.index(self._lane)
        self.action_lane(LANES[(i + step) % len(LANES)])
