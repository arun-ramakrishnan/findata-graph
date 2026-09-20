"""
Tier 1 tests for dirty-gated corpus validation
(doc/improvements/archive/tooling/dirty_gated_corpus_validation.md S3,
completed.md #260).

Each test seeds a minimal fake vault under tmp_path and proves the claim:
the porcelain builder parses renames/deletes/untracked correctly and
degrades to full (never to skip); each gateable leg honors an explicit
scope; full-vs-scoped parity holds; and the weakened guarantee — a break
in a clean file goes unflagged while others are dirty — is DOCUMENTED by
a test, not hidden.

No wall-clock assertions: timing lives in `make perf` by house rule
(pytest.ini notes the `slow` marker removal 2026-08-14). The fast-path
numbers are measured manually best-of-3 and recorded in the proposal
appendix.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import helpers.validators.frontmatter_schema as hfs  # the copy the wrappers import
from validators import data_format_checks as dfc
from validators import frontmatter_schema as fs
from validators import static_checks as sc

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
GOOD_NOTE = (
    "---\n"
    "title: Good\ntype: company\npermalink: companies/other/good\n"
    "created: '2026-01-01'\nlast_modified: '2026-02-01'\n"
    "tags:\n- sector/energy\n"
    "---\n# Good\nbody.\n"
)
# Uppercase sector tag: fatal in the YAML leg (one defect, one leg).
BAD_TAG_NOTE = (
    "---\n"
    "title: Bad\ntype: company\npermalink: companies/other/bad\n"
    "tags:\n- sector/Pharma\n"
    "---\n# Bad\nbody.\n"
)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Minimal fake repo: findata notes + one live proposal.

    good.md is clean in every leg; badtag.md breaks the YAML leg only;
    empty.md (no frontmatter) breaks the schema + OKF legs; the empty
    live proposal breaks the proposal-units leg.
    """
    energy = tmp_path / "findata" / "Companies" / "energy"
    other = tmp_path / "findata" / "Other"
    energy.mkdir(parents=True)
    other.mkdir(parents=True)
    paths = {
        "good": other / "good.md",
        "badtag": other / "badtag.md",
        "empty": energy / "empty.md",
        "proposal": tmp_path / "doc" / "improvements" / "proposals" / "dirty.md",
    }
    paths["good"].write_text(GOOD_NOTE, encoding="utf-8")
    paths["badtag"].write_text(BAD_TAG_NOTE, encoding="utf-8")
    paths["empty"].write_text("", encoding="utf-8")
    paths["proposal"].parent.mkdir(parents=True)
    paths["proposal"].write_text("", encoding="utf-8")
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(fs, "REPO_ROOT", tmp_path)
    # Schemas stay real (fs.SCHEMA_DIR untouched) — only the corpus is fake.
    return paths


def _abs_set(paths):
    return {p.absolute() for p in paths}


class _FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


# --------------------------------------------------------------------------- #
# Builder: porcelain parsing + degrade-to-full                                 #
# --------------------------------------------------------------------------- #
def test_builder_parses_porcelain(tmp_path, monkeypatch):
    """Modified/added/renamed(new side)/untracked in; deletes, non-md,
    and out-of-roots paths out."""
    (tmp_path / "findata" / "Companies" / "energy").mkdir(parents=True)
    (tmp_path / "findata" / "Other").mkdir(parents=True)
    (tmp_path / "doc" / "improvements" / "proposals").mkdir(parents=True)
    keep = [
        "findata/Companies/energy/a.md",
        "findata/Other/b.md",
        "findata/Companies/energy/new.md",
        "findata/untracked.md",
        "doc/improvements/proposals/new.md",
    ]
    for rel in keep:
        (tmp_path / rel).write_text("# x\n", encoding="utf-8")
    porcelain = (
        "M  findata/Companies/energy/a.md\n"
        "A  findata/Other/b.md\n"
        "R  findata/Companies/old.md -> findata/Companies/energy/new.md\n"
        "D  findata/Companies/gone.md\n"
        "?? findata/untracked.md\n"
        "?? helper.py\n"
        " M app.py\n"
        "?? doc/improvements/proposals/new.md\n"
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: _FakeProc(porcelain))
    assert sc.get_dirty_scope() == _abs_set(tmp_path / r for r in keep)


def test_builder_schema_change_forces_full(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        sc.subprocess,
        "run",
        lambda *a, **k: _FakeProc(
            "M  findata/Other/b.md\nM  doc/okf/frontmatter.company.v1.json\n"
        ),
    )
    assert sc.get_dirty_scope() is None


@pytest.mark.parametrize("failure", ["rc", "missing"])
def test_builder_git_failure_degrades_to_full(tmp_path, monkeypatch, failure):
    """No git, no gate: degrade to full, never to skip."""
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    if failure == "rc":
        monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: _FakeProc("", 128))
    else:

        def _raise(*a, **k):
            raise FileNotFoundError("no git")

        monkeypatch.setattr(sc.subprocess, "run", _raise)
    assert sc.get_dirty_scope() is None


# --------------------------------------------------------------------------- #
# Leg scoping: dirty-break flagged, clean scope quiet                          #
# --------------------------------------------------------------------------- #
def test_yaml_leg_scope(vault):
    fatal, _ = sc.check_findata_yaml(scope=_abs_set([vault["badtag"]]))
    assert len(fatal) == 1 and "badtag" in fatal[0]
    fatal, _ = sc.check_findata_yaml(scope=_abs_set([vault["good"]]))
    assert fatal == []
    fatal, _ = sc.check_findata_yaml(scope=set())
    assert fatal == []


def test_schema_leg_scope(vault):
    root = vault["good"].parents[2]
    fatal, _ = fs.check_frontmatter_schema(root, scope=_abs_set([vault["empty"]]))
    assert len(fatal) == 1 and "empty.md" in fatal[0]
    fatal, _ = fs.check_frontmatter_schema(root, scope=_abs_set([vault["good"]]))
    assert fatal == []
    fatal, _ = fs.check_frontmatter_schema(root, scope=set())
    assert fatal == []


def test_okf_leg_scope_and_partial_census(vault):
    root = vault["good"].parents[2]
    fatal, advisory = fs.check_okf_conformance(root, scope=_abs_set([vault["empty"]]))
    assert any("empty.md" in line for line in fatal)
    assert any("dirty subset" in line for line in advisory)
    fatal, advisory = fs.check_okf_conformance(root, scope=set())
    assert fatal == []
    _, advisory_full = fs.check_okf_conformance(root)
    assert not any("dirty subset" in line for line in advisory_full)


def test_proposal_units_scope(vault):
    root = vault["good"].parents[2]
    fatal, _ = fs.check_frontmatter_schema(root, scope=_abs_set([vault["proposal"]]))
    assert any("dirty.md" in line for line in fatal)
    fatal, _ = fs.check_frontmatter_schema(root, scope=set())
    assert fatal == []


def test_schema_empty_scope_skips_jsonschema_import(vault, monkeypatch):
    """Import-trim slice 1: an empty scope returns BEFORE any engine
    import. Blocking both engines proves it: empty scope stays
    clean-silent, while a non-empty scope with fastjsonschema blocked
    degrades to strict (advisory), and with both blocked reports the
    jsonschema-absent advisory (minimal-env contract)."""
    import sys

    root = vault["good"].parents[2]
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    monkeypatch.setitem(sys.modules, "fastjsonschema", None)
    assert fs.check_frontmatter_schema(root, scope=set()) == ([], [])
    _, advisory = fs.check_frontmatter_schema(root, scope=_abs_set([vault["good"]]))
    assert any("not installed" in a for a in advisory)


# --------------------------------------------------------------------------- #
# Parity: full == scoped-to-all                                                #
# --------------------------------------------------------------------------- #
def test_full_vs_scoped_parity(vault):
    root = vault["good"].parents[2]
    everything = _abs_set([vault["good"], vault["badtag"], vault["empty"], vault["proposal"]])
    full_yaml = sc.check_findata_yaml()
    scoped_yaml = sc.check_findata_yaml(scope=everything)
    assert scoped_yaml == full_yaml
    full_schema = fs.check_frontmatter_schema(root)
    scoped_schema = fs.check_frontmatter_schema(root, scope=everything)
    assert scoped_schema[0] == full_schema[0]  # fatals identical; census may differ


def test_weakened_guarantee_is_documented(vault):
    """A break in a CLEAN file goes unflagged while others are dirty.

    This is the accepted cost of dirty-gating (proposal §5): the full
    run (`--full`, the default) still catches it. If this test ever
    fails by flagging badtag, gating got stronger — update the proposal.
    """
    scoped_fatal, _ = sc.check_findata_yaml(scope=_abs_set([vault["good"]]))
    assert scoped_fatal == []  # badtag.md is broken AND unflagged here
    full_fatal, _ = sc.check_findata_yaml()
    assert any("badtag" in line for line in full_fatal)  # --full catches it


# --------------------------------------------------------------------------- #
# Wiring: wrappers pass the global through; main() flags                       #
# --------------------------------------------------------------------------- #
def test_wrappers_pass_global_scope(monkeypatch):
    """The CHECKS callables consult sc._DIRTY_SCOPE (None = full)."""
    seen = {}

    def _stub_schema(scope=None, strict=False):
        seen["schema"] = scope
        seen["strict"] = strict
        return [], []

    def _stub_okf(scope=None):
        seen["okf"] = scope
        return [], []

    monkeypatch.setattr(hfs, "check_frontmatter_schema", _stub_schema)
    monkeypatch.setattr(hfs, "check_okf_conformance", _stub_okf)
    sentinel = {Path("/x.md")}
    monkeypatch.setattr(sc, "_DIRTY_SCOPE", sentinel)
    assert sc.check_frontmatter_schema_contract() == ([], [])
    assert sc.check_okf_conformance_contract() == ([], [])
    assert seen == {"schema": sentinel, "strict": False, "okf": sentinel}


def test_main_flags(monkeypatch, capsys):
    """Gating is the default; --full opts out; scope-unavailable → full."""
    monkeypatch.setattr(sc, "CHECKS", [("Fake", lambda: ([], []))])
    monkeypatch.setattr(sc, "_DIRTY_SCOPE", None)
    monkeypatch.setattr(sc, "_DIRTY_PY_SCOPE", None)
    monkeypatch.setattr(sc, "_DIRTY_JS_SCOPE", None)
    monkeypatch.setattr(sc, "_STRICT", False)
    monkeypatch.setattr(sc, "get_dirty_scope", lambda: {Path("/d.md")})
    monkeypatch.setattr(sc, "get_dirty_py_scope", lambda: {Path("/e.py"), Path("/f.py")})
    monkeypatch.setattr(sc, "get_dirty_js_scope", lambda: set())

    assert sc.main([]) == 0  # default gates
    assert sc._DIRTY_SCOPE == {Path("/d.md")}
    assert sc._DIRTY_PY_SCOPE == {Path("/e.py"), Path("/f.py")}
    assert sc._DIRTY_JS_SCOPE == set()
    assert "dirty-gated: 1 note(s), 2 python file(s), 0 js file(s)" in capsys.readouterr().out

    monkeypatch.setattr(sc, "_DIRTY_SCOPE", None)
    monkeypatch.setattr(sc, "_DIRTY_PY_SCOPE", None)
    monkeypatch.setattr(sc, "_DIRTY_JS_SCOPE", None)
    assert sc.main(["--full"]) == 0
    assert sc._DIRTY_SCOPE is None
    assert sc._DIRTY_PY_SCOPE is None
    assert sc._DIRTY_JS_SCOPE is None

    assert sc.main(["--dirty", "--full"]) == 0
    assert sc._DIRTY_SCOPE is None

    monkeypatch.setattr(sc, "get_dirty_scope", lambda: None)
    assert sc.main([]) == 0  # scope unavailable → full with notice
    assert sc._DIRTY_SCOPE is None
    assert "full" in capsys.readouterr().out

    assert sc.main(["--strict"]) == 0
    assert sc._STRICT is True


def test_fs_report_mode_never_blocks(vault, capsys):
    """--report: full corpus + strict engine, violations as warnings, rc 0
    even on a broken vault (the maint advisory contract)."""
    root = vault["good"].parents[2]
    assert hfs.main(["--report", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "empty.md" in out and "never blocks" in out
    assert "FATAL" not in out


# --------------------------------------------------------------------------- #
# .py legs (gate_latency_followups Slice A)                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def pyvault(tmp_path, monkeypatch):
    """Minimal fake code tree: one clean file per breakage class."""
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    static = tmp_path / "static"
    static.mkdir()
    (tmp_path / "tests").mkdir()
    paths = {
        "good": helpers / "good.py",
        "badsyntax": helpers / "bad_syntax.py",
        "badchoke": helpers / "bad_choke.py",
        "badzstd": helpers / "bad_zstd.py",
        "lane": helpers / "lane.py",
        "plain": helpers / "plain.py",
        "dbuser": helpers / "db_user.py",
        "testfile": tmp_path / "tests" / "t.py",
        "app": tmp_path / "app.py",
        "goodjs": static / "good.js",
        "badjs": static / "bad.js",
    }
    paths["good"].write_text("x = 1\n", encoding="utf-8")
    paths["badsyntax"].write_text("def broken(:\n", encoding="utf-8")
    paths["badchoke"].write_text("import json\nx = json.loads(emb_vec)\n", encoding="utf-8")
    paths["badzstd"].write_text(
        "import pyarrow.parquet as pq\npq.write_table(t, 'x.parquet')\n", encoding="utf-8"
    )
    paths["lane"].write_text("def load_stuff():\n    return {'a': 1}\n", encoding="utf-8")
    paths["plain"].write_text("x = 1\n", encoding="utf-8")
    paths["dbuser"].write_text('import sqlite3\nconn = sqlite3.connect("x.db")\n', encoding="utf-8")
    paths["testfile"].write_text(
        'import sqlite3\nconn = sqlite3.connect(":memory:")\n', encoding="utf-8"
    )
    paths["app"].write_text("x = 1\n", encoding="utf-8")
    paths["goodjs"].write_text('console.log("x");\n', encoding="utf-8")
    paths["badjs"].write_text("function broken( {\n", encoding="utf-8")
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(dfc, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(dfc, "SCAN_ROOTS", [tmp_path / "helpers", tmp_path / "app.py"])
    monkeypatch.setattr(dfc, "DATA_LANE_MODULES", {"helpers/lane.py"})
    return paths


def test_py_builder(tmp_path, monkeypatch):
    """Porcelain → .py set; a schema change does NOT force py-full
    (schemas say nothing about Python files); git failure → None."""
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    for rel in ("helpers/a.py", "app.py", "helpers/new.py", "tests/new_test.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    porcelain = (
        "M  helpers/a.py\n"
        "A  app.py\n"
        "R  helpers/old.py -> helpers/new.py\n"
        "D  helpers/gone.py\n"
        "?? tests/new_test.py\n"
        "?? helper.txt\n"
        "M  findata/Other/b.md\n"
    )
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: _FakeProc(porcelain))
    assert sc.get_dirty_py_scope() == _abs_set(
        tmp_path / r for r in ("helpers/a.py", "app.py", "helpers/new.py", "tests/new_test.py")
    )
    monkeypatch.setattr(
        sc.subprocess,
        "run",
        lambda *a, **k: _FakeProc(porcelain + "M  doc/okf/frontmatter.company.v1.json\n"),
    )
    assert sc.get_dirty_py_scope() is not None  # schemas don't gate Python
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: _FakeProc("", 128))
    assert sc.get_dirty_py_scope() is None


def test_syntax_leg_scope(pyvault):
    fatal = sc.check_python_syntax(scope=_abs_set([pyvault["badsyntax"]]))
    assert len(fatal) == 1 and "bad_syntax" in fatal[0]
    assert sc.check_python_syntax(scope=_abs_set([pyvault["good"]])) == []
    assert sc.check_python_syntax(scope=set()) == []


def test_choke_leg_scope(pyvault):
    fatal = sc.check_embedding_decode_chokepoint(scope=_abs_set([pyvault["badchoke"]]))
    assert len(fatal) == 1 and "bad_choke" in fatal[0] and "emb_vec" in fatal[0]
    assert sc.check_embedding_decode_chokepoint(scope=_abs_set([pyvault["good"]])) == []


def test_data_format_leg_scope(pyvault):
    fatal, _ = dfc.check_data_format(scope=_abs_set([pyvault["badzstd"]]))
    assert any("bad_zstd" in line for line in fatal)
    fatal, _ = dfc.check_data_format(scope=_abs_set([pyvault["good"]]))
    assert fatal == []
    fatal, _ = dfc.check_data_format(scope=_abs_set([pyvault["lane"]]))
    assert any("lane.py:load_stuff" in line for line in fatal)


def test_py_full_vs_scoped_parity(pyvault):
    everything = _abs_set(
        [
            pyvault["good"],
            pyvault["badsyntax"],
            pyvault["badchoke"],
            pyvault["badzstd"],
            pyvault["lane"],
        ]
    )
    assert sc.check_python_syntax(scope=everything) == sc.check_python_syntax()
    assert sc.check_embedding_decode_chokepoint(scope=everything) == (
        sc.check_embedding_decode_chokepoint()
    )
    # dfc fatals compared order-insensitive (walk order vs sorted scope).
    assert sorted(dfc.check_data_format(scope=everything)[0]) == sorted(dfc.check_data_format()[0])


def test_py_weakened_guarantee_is_documented(pyvault):
    """Same accepted cost as the corpus flip, now for .py: a break in a
    clean file goes unflagged while others are dirty; --full catches it."""
    assert sc.check_python_syntax(scope=_abs_set([pyvault["good"]])) == []
    assert any("bad_syntax" in line for line in sc.check_python_syntax())


def test_baseline_hygiene_skipped_when_scoped(pyvault, monkeypatch):
    """Staleness needs the file scanned this run: a seeded entry whose
    file is out of scope must not false-flag when scoped, but must
    flag on a full run (hygiene stays a full-corpus property)."""
    monkeypatch.setattr(dfc, "_ZSTD_BASELINE", {"helpers/gone.py:1": "seeded"})
    fatal, _ = dfc.check_data_format(scope=_abs_set([pyvault["good"]]))
    assert not any("stale" in line for line in fatal)
    fatal, _ = dfc.check_data_format()
    assert any("stale _ZSTD_BASELINE" in line for line in fatal)


def test_baseline_hygiene_flags_in_scope_staleness(pyvault, monkeypatch):
    """Refinement (Slice D): a seeded entry whose file IS in scope and
    scans clean is genuinely stale — flag it even on a scoped run."""
    monkeypatch.setattr(dfc, "_ZSTD_BASELINE", {"helpers/good.py:<module>": "seeded"})
    fatal, _ = dfc.check_data_format(scope=_abs_set([pyvault["good"]]))
    assert any("stale _ZSTD_BASELINE" in line for line in fatal)


def test_no_dead_corpus_import():
    """Slice B: importing static_checks must not pull helpers.core.corpus
    (the ~70 ms chain); fresh interpreter, real assertion."""
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'helpers'); "
            "import validators.static_checks; "
            "print('helpers.core.corpus' in sys.modules)",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == "False"


def test_route_inventory_multi_route(tmp_path):
    """Regression (Slice E): route_inventory must handle 2+ routes from a
    single read — the read-once refactor shadowed `text` with the first
    segment and IndexError'd on the second route (caught by shakedown,
    not by the suite: no multi-route coverage existed)."""
    from validators import coverage_ledger as cl

    app = tmp_path / "app.py"
    app.write_text(
        "from flask import Flask\napp = Flask(__name__)\n"
        "@app.route('/a')\ndef ha():\n    return 'a'\n"
        "@app.route('/b', methods=['POST'])\ndef hb():\n    return 'b'\n",
        encoding="utf-8",
    )
    inv = cl.route_inventory(app)
    assert set(inv) == {"/a", "/b"}
    assert inv["/a"]["handler"] == "ha" and inv["/b"]["methods"] == ["POST"]
    assert inv["/a"]["fingerprint"] != inv["/b"]["fingerprint"]


def test_sqlite_leg_scope(pyvault):
    fatal = sc.check_sqlite_helper_usage(scope=_abs_set([pyvault["dbuser"]]))
    assert len(fatal) == 1 and "db_user" in fatal[0]
    assert sc.check_sqlite_helper_usage(scope=_abs_set([pyvault["good"]])) == []
    # tests/ exemption holds on both paths.
    assert sc.check_sqlite_helper_usage(scope=_abs_set([pyvault["testfile"]])) == []
    assert not any("tests/t.py" in line for line in sc.check_sqlite_helper_usage())


def test_shebang_leg_scope(pyvault):
    fatal = sc.check_helper_shebangs(scope=_abs_set([pyvault["plain"]]))
    assert len(fatal) == 1 and "plain.py" in fatal[0] and "shebang" in fatal[0]


def test_sqlite_shebang_parity(pyvault):
    everything = _abs_set(p for k, p in pyvault.items() if not k.endswith("js"))
    assert sc.check_sqlite_helper_usage(scope=everything) == sc.check_sqlite_helper_usage()
    # Shebang fatals compared order-insensitive (walk order vs sorted scope).
    assert sorted(sc.check_helper_shebangs(scope=everything)) == sorted(sc.check_helper_shebangs())


def test_js_builder(tmp_path, monkeypatch):
    """Porcelain → .js set (repo-wide; the leg filters to static/)."""
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    for rel in ("static/a.js", "docs/c.js"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x();\n", encoding="utf-8")
    monkeypatch.setattr(
        sc.subprocess,
        "run",
        lambda *a, **k: _FakeProc("M  static/a.js\n?? docs/c.js\nM  helpers/b.py\n"),
    )
    assert sc.get_dirty_js_scope() == _abs_set([tmp_path / "static/a.js", tmp_path / "docs/c.js"])


need_node = pytest.mark.skipif(not shutil.which("node"), reason="node absent")


@need_node
def test_js_leg_scope(pyvault):
    fatal = sc.check_js_syntax(scope=_abs_set([pyvault["badjs"]]))
    assert len(fatal) == 1 and "bad.js" in fatal[0]
    assert sc.check_js_syntax(scope=_abs_set([pyvault["goodjs"]])) == []
    assert sc.check_js_syntax(scope=set()) == []


@need_node
def test_js_parity(pyvault):
    everything = _abs_set([pyvault["goodjs"], pyvault["badjs"]])
    assert sc.check_js_syntax(scope=everything) == sc.check_js_syntax()


def test_fs_main_default_gates(monkeypatch):
    """The frontmatter CLI gates by default; --full opts out."""
    import helpers.validators.static_checks as hsc

    seen = {}

    def _stub(root, scope=None, strict=False):
        seen["scope"] = scope
        return [], []

    monkeypatch.setattr(hsc, "get_dirty_scope", lambda: {Path("/d.md")})
    monkeypatch.setattr(hfs, "check_frontmatter_schema", _stub)
    assert hfs.main([]) == 0
    assert seen["scope"] == {Path("/d.md")}
    assert hfs.main(["--full"]) == 0
    assert seen["scope"] is None
