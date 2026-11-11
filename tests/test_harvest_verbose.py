"""--verbose quiet-line suppression on the APPLY print (user request 2026-11-11).

Steady-state APPLY printed 100+ per-unit "...: quiet" watermark-match
lines — zero information. Default now drops them at the presentation
layer only: lane functions still RETURN quiet lines (other tests assert
on those strings), and the new-parts table keeps counting them.
"""

from __future__ import annotations

import helpers.maintenance.harvest_conversations as hc


def test_drop_keeps_progress_drops_quiet():
    block = "\n".join(
        [
            "dsh:live:session-fe99a: quiet",
            "bili:session-f91a3: quiet",
            "dsh:live:session-abc: delta 3 parts",
            "opencode-snap:2026-09-28: full 120 parts",
            "prime-rlm:s1: SKIP unreadable (OSError: boom)",
            "zcode:ses_z: quiet",
        ]
    )
    assert hc._drop_quiet_lines(block).splitlines() == [
        "dsh:live:session-abc: delta 3 parts",
        "opencode-snap:2026-09-28: full 120 parts",
        "prime-rlm:s1: SKIP unreadable (OSError: boom)",
    ]


def test_drop_all_quiet_yields_empty():
    # The steady-state shape: every line quiet -> nothing printed at all.
    assert hc._drop_quiet_lines("a: quiet\nb: quiet") == ""


def _fake_apply_out():
    return {
        "check": False,
        "lanes": ("dsh:live:session-aaaa: quiet\ndsh:live:session-bbbb: delta 2 parts"),
        "tally": {"dsh": {"units": {"session-bbbb": 2}, "quiet": 1, "skipped": 0}},
        "embed_rate": None,
        "sessions_written": 1,
        "lane_seconds": {"dsh": 0.1},
        "elapsed": 0.2,
    }


def test_apply_suppresses_quiet_by_default(tmp_path, monkeypatch, capsys):
    touched = tmp_path / "m.duckdb"
    touched.write_bytes(b"x")  # main() stats the --db file for the size line
    monkeypatch.setattr(hc, "harvest", lambda **kw: _fake_apply_out())
    rc = hc.main(["--db", str(touched)])
    err = capsys.readouterr().err
    assert rc == 0
    assert "session-aaaa: quiet" not in err  # per-unit quiet line dropped
    assert "delta 2 parts" in err  # progress lines kept
    assert "1 quiet" in err  # table still counts the quiet unit


def test_apply_verbose_prints_quiet(tmp_path, monkeypatch, capsys):
    touched = tmp_path / "m.duckdb"
    touched.write_bytes(b"x")
    monkeypatch.setattr(hc, "harvest", lambda **kw: _fake_apply_out())
    rc = hc.main(["--db", str(touched), "--verbose"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "session-aaaa: quiet" in err  # unfiltered block
    assert "delta 2 parts" in err
