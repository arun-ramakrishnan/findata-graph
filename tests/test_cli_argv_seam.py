#!/usr/bin/env python3
"""CLI test-seam probe for the five out-of-census bare mains.

The 41-`ArgumentParser` census parsers took the house argv seam under
#196 W4; these five carry no `ArgumentParser` (three hand-roll
`sys.argv`, two are flag-less) and got the same seam via
doc/improvements/archive/tooling/argv_seam_tail.md. This module pins the
seam contract: every `main` accepts a post-script-name `argv` defaulting
to None, and the two hand-rolled CLIs actually route it (invocation
probes hit their usage branch). The flag-less / eval-running mains are
deliberately NOT invoked — the signature probe is their whole gate.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.misc import database_integrity_check, embed_eval  # noqa: E402
from helpers.maintenance import move_sector, rename_entity  # noqa: E402
from helpers.validators import static_checks  # noqa: E402

SEAM_MAINS = [
    rename_entity.main,
    move_sector.main,
    embed_eval.main,
    static_checks.main,
    database_integrity_check.main,
]


@pytest.mark.parametrize("fn", SEAM_MAINS, ids=lambda f: f.__module__)
def test_main_takes_optional_argv(fn):
    """The seam: post-script-name argv, defaulting to None (real argv)."""
    params = inspect.signature(fn).parameters
    assert "argv" in params, f"{fn.__module__}.main lost its argv seam"
    assert params["argv"].default is None


def test_rename_entity_empty_argv_hits_usage_branch(capsys):
    """Relocal routes the injected argv: 2 positional args are required,
    so main([]) must print usage (the module docstring) and return 2
    without touching anything."""
    assert rename_entity.main([]) == 2
    assert capsys.readouterr().out.strip()


def test_move_sector_empty_argv_hits_usage_branch(capsys):
    assert move_sector.main([]) == 2
    assert capsys.readouterr().out


def test_rename_entity_argv_reaches_positionals(tmp_path, monkeypatch, capsys):
    """The index shift (sys.argv[N] -> raw[N-1]) really feeds the injected
    argv through to the SQL lookup: a well-formed rename of an unknown
    entity fails NOT-FOUND (rc 1), not usage (rc 2). DB_PATH is pinned to
    a tmp db so the probe never touches the live research.db."""
    import sqlite3

    db = tmp_path / "seam.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE entities (name TEXT, normalized_name TEXT, "
        "sector_classification TEXT, file_path TEXT)"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(rename_entity, "DB_PATH", db)

    assert rename_entity.main(["__no_such_entity__", "New Name"]) == 1
    assert "not found" in capsys.readouterr().err
