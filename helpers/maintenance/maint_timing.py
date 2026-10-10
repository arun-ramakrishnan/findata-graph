#!/usr/bin/env python3
"""Run + phase timing recorder for maintenance CLIs (gate-style evidence).

Every maintenance entry point (``search-fresh`` / ``convo-fresh`` /
``embed-gc`` / ``analytics-fresh`` / ``snapshot`` legs) wraps its flow in
:func:`RunTimer`: phases are recorded with wall-clock start/end/elapsed,
one row per run lands in ``maint_runs`` plus one row per phase in
``maint_phases``, inside ``outputs/gate_runs.duckdb`` — the same query
layer as the gates (see ``helpers/misc/maint_query.py`` and
``helpers/misc/gate_query.py``).

Recording NEVER breaks the CLI: any failure to open or write the timing
DB degrades to one stderr line; the CLI's own exit code is unchanged.
The make targets pass ``MAINT_TARGET=<target>`` in the environment so a
row knows which target invoked it; ``cmd`` defaults to the script
basename and ``mode`` is passed explicitly (``check`` / ``apply`` /
whatever the CLI's own vocabulary is).

Write gating: ``finish()`` persists ONLY when a target is known
(``MAINT_TARGET`` set by the make recipes, or passed explicitly).
Unit tests call these mains/cores directly with tmp trees — without the
gate their runs would flood the corpus. The human Step table still
prints in every invocation.
"""

import contextlib
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "outputs" / "gate_runs.duckdb"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS maint_runs (
    maint_run_id INTEGER PRIMARY KEY,
    cmd TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT '',
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    elapsed_s DOUBLE,
    exit_code INTEGER,
    summary TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS maint_phases (
    maint_run_id INTEGER NOT NULL,
    phase TEXT NOT NULL,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    elapsed_s DOUBLE,
    extra TEXT NOT NULL DEFAULT ''
);
"""


def _warn(msg: str) -> None:
    print(f"[maint_timing] {msg}", file=sys.stderr, flush=True)


# Active-timer stack: nested library code (rebuild cores, harvest lanes)
# records phases against the CLI's timer without signature changes.
# ``with active(timer):`` in the CLI main; ``with phase("x"):`` (or a
# direct ``current()`` lookup) anywhere underneath. No timer active →
# ``phase()`` is a pass-through, so tests calling the cores directly
# behave exactly as before.
_STACK: list["RunTimer"] = []


@contextlib.contextmanager
def active(timer: "RunTimer"):
    """Push ``timer`` as the phase sink for nested code."""
    _STACK.append(timer)
    try:
        yield timer
    finally:
        _STACK.pop()


def current() -> "RunTimer | None":
    """The innermost active timer, or None outside a CLI run."""
    return _STACK[-1] if _STACK else None


@contextlib.contextmanager
def phase(name: str, extra: str = ""):
    """Record one named phase on the active timer; pass-through when idle."""
    timer = current()
    if timer is None:
        yield
    else:
        with timer.phase(name, extra):
            yield


class RunTimer:
    """Wall-clock run + phase recorder; also a context manager.

    Usage::

        timer = RunTimer("rebuild_convo_search", mode="apply")
        with timer.phase("embed", extra="rows=219"):
            ...work...
        return timer.finish(exit_code, summary)

    As a context manager (``with RunTimer(...) as t:``) the run
    auto-finishes with exit 0; an exception finishes with exit 1 and
    re-raises. Either way the CLI's own control flow is untouched.
    """

    def __init__(self, cmd: str = "", mode: str = "", target: str = "") -> None:
        self.cmd = cmd or Path(sys.argv[0]).stem
        self.mode = mode
        self.target = target or os.environ.get("MAINT_TARGET", "")
        self.started = datetime.now()
        self.phases: list[dict] = []

    def __enter__(self) -> "RunTimer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.finish(1 if exc_type else 0, f"{exc_type.__name__}: {exc}" if exc else "")
        return None

    @contextlib.contextmanager
    def phase(self, name: str, extra: str = ""):
        """Record one named phase with start/end/elapsed wall time."""
        start = datetime.now()
        try:
            yield
        finally:
            self.record_phase(
                name,
                start,
                datetime.now(),
                extra=extra,
            )

    def record_phase(
        self,
        name: str,
        started_at: datetime,
        ended_at: datetime,
        elapsed_s: float | None = None,
        extra: str = "",
    ) -> None:
        """Record a pre-measured phase (for timers like _Phases whose
        marks are cumulative segments computed at mark time)."""
        if elapsed_s is None:
            elapsed_s = round(ended_at.timestamp() - started_at.timestamp(), 3)
        self.phases.append(
            {
                "phase": name,
                "started_at": started_at,
                "ended_at": ended_at,
                "elapsed_s": elapsed_s,
                "extra": extra,
            }
        )

    def step_table(self) -> str:
        """Gate-style Step table for stdout (human UX mirrors the gates)."""
        lines = ["Step                                  Time (s)"]
        for p in self.phases:
            lines.append(f"  {p['phase']:<36s} {p['elapsed_s']:7.3f}")
        total = round(sum(p["elapsed_s"] for p in self.phases), 3)
        lines.append(f"  {'total':<36s} {total:7.3f}")
        return "\n".join(lines)

    def finish(self, exit_code: int = 0, summary: str = "") -> int:
        """Persist the run + phase rows; always returns ``exit_code``.

        No known target (direct unit-test invocation, ad-hoc CLI call
        outside make, gate-subprocess leg) → skip the write AND the
        Step table (zero output footprint outside make), keep the exit
        code.
        """
        if self.target:
            print(self.step_table(), file=sys.stderr)
        else:
            return exit_code
        ended = datetime.now()
        elapsed = round(ended.timestamp() - self.started.timestamp(), 3)
        try:
            import duckdb

            con = duckdb.connect(str(DB_PATH))
            try:
                con.execute(_SCHEMA)
                max_row = con.execute(
                    "SELECT COALESCE(MAX(maint_run_id), 0) + 1 FROM maint_runs"
                ).fetchone()
                if max_row is None:  # aggregate always returns one row
                    raise RuntimeError("maint_runs MAX(id) aggregate returned no row")
                nxt = max_row[0]
                con.execute(
                    "INSERT INTO maint_runs (maint_run_id, cmd, target, mode,"
                    " started_at, ended_at, elapsed_s, exit_code, summary)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        nxt,
                        self.cmd,
                        self.target,
                        self.mode,
                        self.started,
                        ended,
                        elapsed,
                        exit_code,
                        summary[:2000],
                    ],
                )
                for p in self.phases:
                    con.execute(
                        "INSERT INTO maint_phases (maint_run_id, phase,"
                        " started_at, ended_at, elapsed_s, extra)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            nxt,
                            p["phase"],
                            p["started_at"],
                            p["ended_at"],
                            p["elapsed_s"],
                            p["extra"][:500],
                        ],
                    )
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001  # recording is best-effort by design
            _warn(f"timing record skipped ({exc})")
        return exit_code
