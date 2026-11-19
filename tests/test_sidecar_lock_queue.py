"""Sidecar opener lock queueing (embed-gc lesson, 2026-11-19).

Every ``connect_*_db`` dispatch helper shared by query CLIs and rebuild
paths must queue behind a live DuckDB holder (an APPLY in flight) via the
house lock ladder instead of asserting with
``IOException: Could not set lock on file ...: Conflicting lock is held``.
``helpers/misc/duckdb_lock`` owns the semantics; these tests pin that each
opener actually routes through it with the long-holder schedule.
"""

from __future__ import annotations

import duckdb
import pytest

from helpers.maintenance import (
    rebuild_common as rbc,
)
from helpers.maintenance import (
    rebuild_convo_search as rcs,
)
from helpers.maintenance import (
    rebuild_doc_search as rds,
)
from helpers.maintenance import (
    rebuild_git_search as rgs,
)
from helpers.maintenance import (
    rebuild_memory_search as rms,
)
from helpers.maintenance import (
    rebuild_note_search as rns,
)
from helpers.maintenance import (
    rebuild_script_search as rss,
)
from helpers.misc import duckdb_lock

LOCK_MSG = (
    'IO Error: Could not set lock on file "/x/store.duckdb": '
    "Conflicting lock is held in /x/python3.14 (PID 678232) by user arun."
)


def _flaky_connect(monkeypatch, tmp_path, name: str):
    """First attempt raises the transient lock error, second attempt
    opens a real store under tmp_path — proving the retry happened
    against a genuine connect, not a stub return."""
    calls = {"n": 0}
    real_connect = duckdb.connect

    def flaky(path, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise duckdb.IOException(LOCK_MSG)
        return real_connect(str(tmp_path / name), *a, **k)

    monkeypatch.setattr(duckdb, "connect", flaky)
    return calls


def _zero_ladder(monkeypatch):
    """Keep the real ladder logic but collapse every delay to 0 s —
    asserts the EXTENDED (long-holder) schedule was selected without
    sleeping 60 s in a test."""
    seen: dict = {}
    real_delay = duckdb_lock.lock_retry_delay

    def recording(attempt, delays=duckdb_lock._RETRY_DELAYS_S):
        seen["delays"] = delays
        return real_delay(attempt, (0.0,))

    monkeypatch.setattr(duckdb_lock, "lock_retry_delay", recording)
    return seen


CASES = [
    ("doc", lambda p: rds.connect_doc_db(p)),
    ("git", lambda p: rgs.connect_git_db(p)),
    ("script", lambda p: rss.connect_script_db(p)),
    ("memory", lambda p: rms.connect_memory_db(p)),
    ("note", lambda p: rns.connect_note_db(p)),
]


@pytest.mark.parametrize("label,open_fn", CASES, ids=[c[0] for c in CASES])
def test_sidecar_opener_queues_on_a_transient_lock(monkeypatch, tmp_path, label, open_fn):
    seen = _zero_ladder(monkeypatch)
    calls = _flaky_connect(monkeypatch, tmp_path, f"{label}.duckdb")
    con = open_fn(tmp_path / f"{label}.duckdb")
    con.close()
    assert calls["n"] == 2
    assert seen["delays"] == duckdb_lock._EXTENDED_RETRY_DELAYS_S


def test_stamp_db_opener_queues_on_a_transient_lock(monkeypatch, tmp_path):
    seen = _zero_ladder(monkeypatch)
    calls = _flaky_connect(monkeypatch, tmp_path, "stamp.duckdb")
    con = rbc._open_stamp_db(tmp_path / "stamp.duckdb", read_only=True)
    con.close()
    assert calls["n"] == 2
    assert seen["delays"] == duckdb_lock._EXTENDED_RETRY_DELAYS_S


def test_convo_probe_queues_and_still_reads_absence(monkeypatch, tmp_path):
    """_provenance_migration's probe must not misread a held lock as
    'table absent' — it queues first, and a genuinely fresh store still
    reports no provenance column."""
    seen = _zero_ladder(monkeypatch)
    real_connect = duckdb.connect
    calls = _flaky_connect(monkeypatch, tmp_path, "convo.duckdb")
    store = tmp_path / "convo.duckdb"
    real_connect(str(store)).close()  # the exists() gate needs a live file
    assert rcs._provenance_migration(store) is False
    assert calls["n"] == 2
    assert seen["delays"] == duckdb_lock._EXTENDED_RETRY_DELAYS_S
