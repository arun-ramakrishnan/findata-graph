"""Tests for the c901_complexity_debt S1 tooling.

Two pieces: the advisory dead-suppression sweep in static_checks, and the
byte-parity harness that a C901 extraction is supposed to be verified with.
The harness tests deliberately include a determinism-detection case, because
a harness that can only ever report PASS is indistinguishable from no
harness at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.misc import parity_harness as ph  # noqa: E402
from helpers.validators import static_checks as sc  # noqa: E402

# --- directive classification --------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "# noqa: C901",
        "# noqa: E501, C901",
        "# type: ignore  # noqa: C901",
        "# ruff: noqa: C901",
    ],
)
def test_real_directives_are_recognised(text):
    assert sc._is_c901_directive(text) or sc._C901_FILE_NOQA_RE.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "# noqa: E501",
        "# noqa anchor moved to the def line (ruff-format split)",
        "# A ``# noqa: C901`` is a claim that a function is too complex",
        "# C901 complexity debt lives in the c901_complexity_debt proposal",
    ],
)
def test_prose_and_other_rules_are_not_c901_suppressions(text):
    assert not sc._is_c901_directive(text)
    assert not sc._C901_FILE_NOQA_RE.search(text)


def test_docstring_mention_is_not_a_suppression(tmp_path):
    """A noqa named inside a string is documentation, not a suppression."""
    f = tmp_path / "m.py"
    f.write_text('def f():\n    """See # noqa: C901 for why this is suppressed."""\n    return 1\n')
    assert sc._c901_comment_lines(f) == {}


def test_strip_neutralises_but_keeps_file_valid():
    src = "def f(a, b):  # noqa: C901\n    if a:\n        return b\n    return a\n"
    comments = {1: "# noqa: C901"}
    out = sc._strip_c901(src, comments)
    assert "noqa" not in out
    compile(out, "m.py", "exec")


def test_def_lines_maps_function_names():
    defs = sc._def_lines("def a():\n    pass\n\n\ndef b():\n    pass\n")
    assert defs == {"a": {1}, "b": {5}}


# --- the sweep ------------------------------------------------------------- #


def test_sweep_is_advisory_only_on_the_real_tree():
    """Never fatal, and it must actually say it swept something."""
    fatal, advisory = sc.check_dead_c901_noqa()
    assert fatal == []
    assert any("c901-swept" in line for line in advisory)


def test_sweep_flags_misplaced_and_stale(tmp_path, monkeypatch):
    """A misplaced anchor and a stale anchor must both be reported."""
    f = tmp_path / "m.py"
    f.write_text(
        "def complex_thing(\n"
        "    a,  # noqa: C901\n"
        "    b,\n"
        "):\n"
        "    return a\n"
        "\n"
        "\n"
        "def simple_thing():  # noqa: C901\n"
        "    return 1\n"
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    fatal, advisory = sc.check_dead_c901_noqa()
    assert fatal == []
    blob = "\n".join(advisory)
    assert "misplaced" in blob
    assert "stale" in blob and "simple_thing" in blob


# --- the parity harness ---------------------------------------------------- #


def test_first_diff_points_at_the_offending_line():
    assert "line 2" in ph._first_diff("a\nb\nc", "a\nZ\nc")
    assert "length differs" in ph._first_diff("a\nb", "a\nb\nc")


def test_baseline_module_exposes_the_same_file_path():
    """A fixture reading __file__ must not see a phantom difference."""
    mod = ph._load_baseline("HEAD", "helpers/graph/csr.py", "helpers.graph.csr")
    assert mod.__file__ == str(ph.REPO_ROOT / "helpers/graph/csr.py")
    assert callable(mod.bfs_path)


def test_registered_fixture_is_byte_identical_to_head():
    ok, report = ph.compare(ph.REGISTRY["bfs_path"], "HEAD", (0, 7))
    assert ok, "\n".join(report)


def test_harness_reports_a_seed_dependent_render_as_determinism():
    """Output that varies with PYTHONHASHSEED is flagged, not passed off as parity."""
    ok, report = ph.compare(ph.REGISTRY["_canary_hash_order"], "HEAD", (0, 1, 7))
    assert ok, "a seed-dependent render is not a parity failure"
    assert any("DETERMINISM" in line for line in report)


def test_harness_fails_on_a_working_tree_that_differs():
    """Parity must be able to go red -- else the check is vacuous."""
    ok, report = ph.compare(ph.REGISTRY["_canary_divergence"], "HEAD", (0,))
    assert not ok
    assert any("PARITY MISMATCH" in line for line in report)
    assert any("TAMPERED-WORKING-TREE" in line for line in report)


def test_canaries_are_excluded_from_a_default_run():
    """A default run must not report the deliberate canaries as failures."""
    assert ph.main([]) == 0


def test_divergence_warns_by_default_and_only_gates_under_strict(capsys):
    """The house ruling: parity warns, it does not assert. --strict opts into gating."""
    assert ph.main(["_canary_divergence", "--seeds", "0"]) == 0
    err = capsys.readouterr().err
    assert "WARNING" in err and "diverged" in err

    assert ph.main(["_canary_divergence", "--seeds", "0", "--strict"]) == 1


def test_unknown_fixture_is_rejected():
    assert ph.main(["no_such_fixture"]) == 2
