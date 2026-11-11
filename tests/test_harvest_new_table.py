"""Targeted tests for the APPLY new-parts tally + breakdown table.

The tally rides the existing lane calls as a kwarg-only param (default
None = inert). APPLY renders the table after flush; --check dry-runs
the lanes on the read-only store (watermarks buffer in memory, the
writer never flushes) so it shows exactly what APPLY would harvest,
"""

from helpers.maintenance.harvest_conversations import (
    SessionWriter,
    Store,
    _format_new_table,
    _harvest_bili,
    harvest,
)
from tests.test_harvest_bili import _fake_index

_LANES = ("opencode", "prime-rlm", "zcode", "dsh", "bili")


def test_format_table_renders_rows_totals_and_estimate():
    tally = {
        "opencode": {"units": {}, "quiet": 9, "skipped": 0},
        "prime-rlm": {"units": {}, "quiet": 12, "skipped": 0},
        "zcode": {"units": {}, "quiet": 3, "skipped": 0},
        "dsh": {
            "units": {"dsh:live:session-98388": 72, "dsh:live:session-cbd25": 35},
            "quiet": 40,
            "skipped": 1,
        },
        "bili": {"units": {"cbd2522a-841 (bili-index/dsh)": 58}, "quiet": 0, "skipped": 0},
    }
    out = _format_new_table(tally, 0.3012, _LANES)
    assert "new-parts breakdown" in out
    assert "total" in out
    # dsh: 2 new sessions, 107 parts, detail lists both units
    assert "107" in out and "session-98388: 72" in out
    # bili row keeps its routing namespace
    assert "bili-index/dsh" in out
    # estimate line uses the measured rate
    assert "est embed" in out and "0.30s/part" in out


def test_format_table_empty_tally_and_missing_rate():
    out = _format_new_table({}, None, _LANES)
    assert "total" in out
    assert "est embed" not in out  # no measured rate yet -> no estimate
    for lane in _LANES:
        assert lane in out


def test_bili_lane_fills_tally_across_passes(tmp_path, monkeypatch):
    """First pass counts parts per (sid, ns) unit; a quiet re-run counts
    quiet — the same tally dict the APPLY table renders."""
    idx = tmp_path / "index.db"
    _fake_index(
        idx,
        [
            ("b1", "ses_x", 1, "t1", "s1"),
            ("b2", "ses_x", 1, "t2", "s2"),
        ],
        namespace="opencode",
    )
    monkeypatch.setattr("helpers.maintenance.harvest_conversations._bili_index_path", lambda: idx)
    store = Store(tmp_path / "meta.duckdb")
    writer = SessionWriter(tmp_path / "corpus")
    tally: dict = {}
    _harvest_bili(store, writer, tally=tally)
    assert tally["bili"]["units"] == {"ses_x (opencode)": 2}
    assert tally["bili"]["quiet"] == 0
    writer2 = SessionWriter(tmp_path / "corpus")
    tally2: dict = {}
    _harvest_bili(store, writer2, tally=tally2)
    assert tally2["bili"]["units"] == {}
    assert tally2["bili"]["quiet"] == 1
    out = _format_new_table(tally, None, _LANES)
    assert "ses_x (opencode): 2" in out
    store.close()


def test_check_mode_dry_run_table_without_persisting(tmp_path, monkeypatch):
    """--check dry-runs the lanes on the read-only store: the table reports
    pending parts, and NO watermark is persisted (dry-run purity)."""
    idx = tmp_path / "index.db"
    _fake_index(
        idx,
        [("b1", "ses_x", 1, "t1", "s1"), ("b2", "ses_x", 1, "t2", "s2")],
        namespace="opencode",
    )
    monkeypatch.setattr("helpers.maintenance.harvest_conversations._bili_index_path", lambda: idx)
    # hermeticity: the check path also scans corpus-vs-index provenance
    monkeypatch.setattr(
        "helpers.maintenance.harvest_conversations._provenance_drift",
        lambda corpus_root: [],
    )
    db = tmp_path / "meta.duckdb"
    Store(db).close()  # create tables so the read_only open succeeds
    out = harvest(check=True, corpus_root=tmp_path / "corpus", db=db, lanes=("bili",))
    assert "ses_x (opencode): 2" in out["table"]
    assert out["embed_rate"] is None  # no measured rate in a fresh store
    store = Store(db)
    assert store.watermark("bili:ses_x") is None  # dry-run persisted nothing
    store.close()
