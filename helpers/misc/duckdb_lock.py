#!/usr/bin/env python3
"""Transient DuckDB file-lock classification, a bounded retry ladder, and
cross-process io-lock coordination.

DuckDB embedded file mode permits any number of read-only holders OR
exactly one process in total — never a mixture (the five-row matrix in
``helpers/graph/query.py::connect``). A read-write holder therefore
blocks every other open, and the failure is a TRANSIENT condition that
resolves the moment the holder closes — but it surfaces as a hard
``duckdb.IOException``. This module classifies exactly that class and
backs off across a short ladder instead of failing the leg, and (S3)
coordinates LONG holds that no ladder can cover.

Specified in ``doc/improvements/archive/tooling/duckdb_transient_lock_retry.md``
S1+S3 (reimplemented from DBX t8y2/dbx master 497d7c9e, Apache-2.0, as a
provenance citation only — the borrowed artefact is the idea, a
conjunctive transient classifier plus a bounded ladder, rewritten in
Python). First call site: the shared xdist graph cache open
(``xdist_shared_graph_cache.md`` S3); the production openers are S2 of
the retry proposal, the ``io_lock`` windows are its S3.

The conjunction is the load-bearing detail: a DISJUNCTION would retry
genuine "file does not exist" and "permission denied" failures,
converting fast errors into 1.55 s of pointless backoff. Both halves
must match — a file-open phrase AND a lock phrase. Our own archived
failure string (gate_xdist_phase2) matches both halves::

    Could not set lock on file "...": Conflicting lock is held

S3's two-class split (transient vs long): the ladder is the right tool
for sub-second holders; a writer that holds the file for tens of
seconds (``stamp_centrality_cache`` ~27.5 s, ``rebuild_convo_search``
~2 min) needs COORDINATION — readers take ``LOCK_SH`` on ``<db>.io.lock``
around their open and queue while a long writer holds ``LOCK_EX`` across
its whole compute window. The reader releases the flock once its DuckDB
open has succeeded: from that moment DuckDB's own per-file lock protects
it (an RW opener is refused while any RO holder exists), so holding the
flock for the reader's whole lifetime would add nothing but deadlock
surface. The writer must close its DuckDB connection BEFORE releasing
``LOCK_EX`` — that ordering is what makes the reader's post-flock open
collision-free.
"""

from __future__ import annotations

import fcntl
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

# file-open half: how DuckDB phrases the failed open itself
_FILE_OPEN_PHRASES = (
    "cannot open file",
    "could not set lock",
    "file is already open",
)

# lock half: how the OS/DuckDB phrases WHY the open failed
_LOCK_PHRASES = (
    "conflicting lock",
    "being used by another process",
    "process cannot access the file",
    "sharing violation",
    "resource temporarily unavailable",
)

# 50, 100, 200, 400, 800 ms — then give up. Total worst-case wait 1.55 s.
_RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.4, 0.8)


def is_transient_lock_error(message: str) -> bool:
    """True only for messages carrying BOTH a file-open phrase and a lock
    phrase — the signature of a live holder, not a missing/unreadable
    file."""
    msg = message.lower()
    return any(p in msg for p in _FILE_OPEN_PHRASES) and any(p in msg for p in _LOCK_PHRASES)


def lock_retry_delay(attempt: int) -> float | None:
    """Backoff before retry ``attempt`` (0-based), or None when the
    ladder is exhausted and the original error must surface."""
    if 0 <= attempt < len(_RETRY_DELAYS_S):
        return _RETRY_DELAYS_S[attempt]
    return None


def connect_with_lock_retry[T](
    open_fn: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``open_fn`` retrying only transient lock conflicts across the
    bounded ladder. Callers opt in explicitly; a non-lock failure raises
    immediately on the first attempt, and a genuine long hold surfaces as
    the original error 1.55 s later — masked deadlocks are not a thing
    here by construction."""
    attempt = 0
    while True:
        try:
            return open_fn()
        except Exception as exc:  # duckdb raises IOException (an OSError)
            delay = lock_retry_delay(attempt)
            if delay is None or not is_transient_lock_error(str(exc)):
                raise
            sleep(delay)
            attempt += 1


@contextmanager
def io_lock(db_path: Path | str, *, exclusive: bool) -> Generator[None]:
    """Cross-process coordination flock on ``<db>.io.lock`` (S3).

    Readers pass ``exclusive=False`` (``LOCK_SH`` — any number may hold
    it concurrently) around their OPEN; long writers pass
    ``exclusive=True`` (``LOCK_EX``) across their whole compute window.
    A reader arriving mid-writer BLOCKS until the window ends instead of
    surfacing DuckDB's ``Conflicting lock is held`` — queueing is the
    correct CLI semantics, and a 1.55 s ladder cannot cover a 2-minute
    hold. The lock file is created lazily and persists (a flock's
    mutual exclusion depends on its inode — same law as
    ``<db>.build.lock``). Kernel-managed: a crashed holder releases it.
    """
    lock_path = Path(str(db_path) + ".io.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def open_read_only(db_path: Path | str):
    """The S3 reader entry: queue on ``<db>.io.lock`` (``LOCK_SH``), then
    open DuckDB read-only with the S1 ladder as belt-and-braces for
    holders that never took the flock (a ``db_maint`` backup, a manual
    CLI). The flock releases the moment the open succeeds — DuckDB's own
    lock protects the connection from then on. DuckDB is imported lazily
    so this module stays dependency-light for classification-only users.
    """
    import duckdb

    with io_lock(db_path, exclusive=False):
        return connect_with_lock_retry(lambda: duckdb.connect(str(db_path), read_only=True))
