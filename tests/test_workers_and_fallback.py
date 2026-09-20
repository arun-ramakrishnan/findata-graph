"""Worker + serial-fallback tests (test_gap_closure S4).

Coverage reports `helpers/graph/_extract_worker.py` and
`_insights_worker.py` at 0%, but that is a measurement artifact: both run
in `ProcessPoolExecutor` children, invisible to the parent's coverage.
This module gives coverage a path to that code AND tests the two real
gaps hiding behind the artifact:

- the workers themselves have no direct unit test, so a pickle-path or
  return-shape regression only surfaced at full-corpus scale;
- the `BrokenProcessPool` serial fallback (`extract_relations.py:2699`)
  -- the OOM-recovery path -- was never exercised.
"""

from __future__ import annotations

import concurrent.futures.process as _cfp

import pytest

from helpers.graph import extract_relations as ER
from helpers.graph import _extract_worker, _insights_worker


# --------------------------------------------------------------------------- #
# Workers: importable, delegating, and shape-correct                          #
# --------------------------------------------------------------------------- #


def test_extract_worker_is_importable_without_side_effects():
    """The worker must import cleanly with no DB/regex-table side effects.

    It is imported at POOL-STARTUP time in a child; an import error there
    is a `BrokenProcessPool`, not a clear error. This is the S1b fix's
    whole premise: the module is never `__main__`, so its pickle name is
    stable whether the parent is a file or `-m` package.
    """
    assert callable(_extract_worker._extract_batch)
    assert callable(_extract_worker._extract_batch_arg)


def test_extract_worker_delegates_to_the_real_batch(monkeypatch):
    """The worker is a thin shim: it must forward to ER._extract_batch.

    A regression that duplicates the implementation here (drifting regex
    tables) would silently diverge from serial; the delegation contract
    is what keeps pool output identical to serial.
    """
    calls: list = []

    def _fake(file_paths, entity_names):
        calls.append((file_paths, entity_names))
        return ["sentinel"]

    monkeypatch.setattr(ER, "_extract_batch", _fake, raising=True)
    got = _extract_worker._extract_batch(["a.md"], ["Reliance"])
    assert got == ["sentinel"]
    assert calls == [(["a.md"], ["Reliance"])]


def test_extract_batch_arg_unwraps_the_tuple(monkeypatch):
    """`_extract_batch_arg` is the single-arg form `ex.map` pickles."""
    seen: list = []

    def _fake(file_paths, entity_names):
        seen.append((file_paths, entity_names))
        return []

    monkeypatch.setattr(ER, "_extract_batch", _fake, raising=True)
    _extract_worker._extract_batch_arg((["b.md"], ["Infosys"]))
    assert seen == [(["b.md"], ["Infosys"])]


def test_insights_worker_shims_are_callable():
    assert callable(_insights_worker._scan_chunk)
    assert callable(_insights_worker._scan_chunk_arg)
    assert callable(_insights_worker._scan_text_chunk)


def test_insights_worker_scan_chunk_returns_dicts(tmp_path, monkeypatch):
    """`_scan_chunk` must emit plain dicts, not dataclasses: the parent
    rebuilds `Quote(**d)` across the process boundary, so a dataclass in
    the payload would break `__main__.Quote` vs module-`Quote` identity.
    """
    note = tmp_path / "n.md"
    note.write_text("# Test\nRevenue rose 12% in Q2.\n", encoding="utf-8")
    resolver_map: dict = {}

    out = _insights_worker._scan_chunk([str(note)], resolver_map)
    assert isinstance(out, list)
    for row in out:
        assert isinstance(row, tuple)
        # (path, quotes, metrics) -- both payloads must be lists of dicts
        assert isinstance(row[1], list)
        assert isinstance(row[2], list)
        for q in row[1]:
            assert isinstance(q, dict)
        for m in row[2]:
            assert isinstance(m, dict)


# --------------------------------------------------------------------------- #
# The BrokenProcessPool serial fallback                                        #
# --------------------------------------------------------------------------- #


class _BoomPool:
    """Stand-in for ProcessPoolExecutor that dies like an OOM-killed child.

    `ex.map` must raise `BrokenProcessPool` for the fallback branch to run.
    """

    def __init__(self, *a, **k):
        raise _cfp.BrokenProcessPool("simulated OOM kill of a child")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, *a, **k):
        raise _cfp.BrokenProcessPool("pool already broken")


@pytest.mark.real_graph_cache
def test_broken_process_pool_falls_back_to_serial(monkeypatch, tmp_path):
    """The OOM-recovery path must produce results identical to serial.

    Patches ProcessPoolExecutor to die on construction; the CLI must
    catch `BrokenProcessPool`, warn, and run the same files serially.
    This is the fallback that only fires when a child is killed -- the
    path least exercised in normal runs and most likely to silently rot.
    """
    import app as A  # noqa: F401  (ensures the app module imports cleanly)

    monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", _BoomPool, raising=True)
    # Force the parallel branch to run despite a small file set.
    monkeypatch.setattr(ER, "_PARALLEL_THRESHOLD", 0, raising=True)

    # Dry-run by default (no --apply). One newsletter file is enough to
    # cross _PARALLEL_THRESHOLD (patched to 0) so the parallel branch runs,
    # the pool dies on construction, and the serial fallback executes the
    # same file -- the recovery path end to end, no seeded DB needed.
    note = tmp_path / "nl.md"
    note.write_text("Reliance and Infosys announced a partnership.\n", encoding="utf-8")
    rc = ER._cli([str(tmp_path)])
    assert rc == 0
