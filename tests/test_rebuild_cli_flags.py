"""CLI flag resolution for the shared rebuild_*_search scaffold.

The shared parser (rebuild_common.build_rebuild_parser + run_rebuild_cli)
made incremental the default and added --full as the opt-out.

Every case here drives the REAL run_rebuild_cli with a recording spec, so
a change in the production resolver cannot silently outrun these tests.

--full is registered ONLY when the spec carries full_help (convo_search
omits it; its atomic swap rebuilds the whole duckdb anyway). The resolver
must therefore read --full defensively: reading it unconditionally raised
AttributeError on any apply run of a spec lacking the flag, which broke
"make convo-fresh APPLY=1" before the rebuild ever started.
"""

import pytest

from helpers.maintenance import rebuild_common as rbc


def _capture_spec(record, *, full_help="h"):
    """A spec whose rebuild_fn records the kwargs run_rebuild_cli passed it."""

    def _capture(db_path, **kwargs):
        record["db_path"] = db_path
        record.update(kwargs)
        return {"indexed": 0}

    return rbc.RebuildCliSpec(
        description="probe",
        default_db="unused",
        db_help="db",
        check_help="check",
        incremental_help="incremental",
        full_help=full_help,
        rebuild_fn=_capture,
        summary=lambda stats: "ok",
        migrated_msg="",
    )


@pytest.mark.parametrize(
    ("argv", "expected_incremental"),
    [
        ([], True),  # incremental is the default
        (["--incremental"], True),  # explicit opt-in equals the default
        (["--full"], False),  # --full wins over the incremental default
    ],
)
def test_resolution_matrix(argv, expected_incremental):
    """The flag matrix as run_rebuild_cli actually resolves it."""
    record = {}
    assert rbc.run_rebuild_cli(argv, _capture_spec(record)) == 0
    assert record["incremental"] is expected_incremental
    assert record["write"] is True


def test_check_never_writes():
    """--check reports staleness without writing, on any spec shape."""
    record = {}
    rbc.run_rebuild_cli(["--check"], _capture_spec(record))
    assert record["write"] is False
    assert record["incremental"] is False


def test_spec_without_full_flag_does_not_crash_apply():
    """Regression: a spec lacking --full must survive an apply run.

    run_rebuild_cli read args.full unconditionally, so convo_search (which
    omits full_help) raised AttributeError before reaching its rebuild. This
    is the "make convo-fresh APPLY=1" path.
    """
    record = {}
    assert rbc.run_rebuild_cli([], _capture_spec(record, full_help=None)) == 0
    assert record["incremental"] is True


def test_spec_without_full_flag_check_still_reports_stale():
    """The --check path on a no-full-help spec must reach the core, not crash."""
    seen = {}

    def _stale(db_path, **kwargs):
        seen.update(kwargs)
        return {"indexed": 0, "index_stale": True}

    spec = rbc.RebuildCliSpec(
        description="probe",
        default_db="unused",
        db_help="db",
        check_help="check",
        incremental_help="incremental",
        rebuild_fn=_stale,
        summary=lambda stats: "ok",
        migrated_msg="",
    )
    assert rbc.run_rebuild_cli(["--check"], spec) == 1  # stale -> exit 1
    assert seen["write"] is False


def test_full_flag_rejected_by_spec_without_full_help():
    """--full must be a clean argparse error, not a silently ignored flag."""
    with pytest.raises(SystemExit):
        rbc.run_rebuild_cli(["--full"], _capture_spec({}, full_help=None))
