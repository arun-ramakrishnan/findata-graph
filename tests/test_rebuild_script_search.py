"""
Tests for helpers/maintenance/rebuild_script_search.py — the builder behind
the script_search sidecar index (script_metadata_search proposal S1).
Hermetic: tmp mini-tree + sidecar, injected fake embedder.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "helpers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from helpers.maintenance import rebuild_script_search as rss  # noqa: E402

pytestmark = [pytest.mark.integration]

_MAKEFILE = (
    ".RECIPEPREFIX := >\n"
    ".PHONY: widget-audit qa\n"
    "widget-audit: ## Run the widget audit pass\n"
    "> python3 helpers/misc/widget_audit.py --apply\n"
    "qa: ## Run the quality gate\n"
    "> python3 tests/test_widget_audit.py\n"
)

_WIDGET_AUDIT = (
    "#!/usr/bin/env python3\n"
    '"""Audit widget diffs across releases.\n'
    "\n"
    "Longer detail paragraph about the audit.\n"
    '"""\n'
    "import argparse\n"
    "\n"
    "\n"
    "def audit_widgets():\n"
    '    """One widget at a time."""\n'
    "    return []\n"
    "\n"
    "\n"
    "def main():\n"
    "    p = argparse.ArgumentParser()\n"
    '    p.add_argument("--apply", action="store_true")\n'
    '    p.add_argument("--db", default="x")\n'
    "    sub = p.add_subparsers()\n"
    '    sub.add_parser("find")\n'
)

# Mentions build_tree in prose/comments but only IMPORTS widget_audit — the
# AST-imports-only tightened contract (grep-mention would over-match).
_TEST_WIDGET = (
    '"""Tests for the widget audit CLI.\n'
    "Related: helpers/graph/build_tree.py (comment mention, not an import).\n"
    '"""\n'
    "from helpers.misc import widget_audit  # noqa: F401\n"
)

_BUILD_TREE = '"""Build the sector hierarchy tree from DB rows."""\n'
_APP = '"""Flask app serving the FinData API."""\n'


def _fake_embed(text: str) -> list[float]:
    import math

    v = [0.0] * 8
    if "widget" in text.lower():
        v[2] = 1.0
    if "gate" in text.lower():
        v[3] = 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture(autouse=True)
def _isolated_backup(tmp_path, monkeypatch):
    """Full-rebuild tests here used to write their 6-row fixture build into
    the REAL db-backup/ (script_search_backup.db) — the un-redirected module
    BACKUP_DIR leaked the fixture index over the last-good backup for the
    module's entire history. Isolate every test in this module."""
    monkeypatch.setattr(rss, "BACKUP_DIR", tmp_path / "db-backup")


@pytest.fixture
def tree(tmp_path):
    """Mini repo: two helpers + app.py + one test + Makefile, under tmp."""
    (tmp_path / "helpers" / "misc").mkdir(parents=True)
    (tmp_path / "helpers" / "graph").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "helpers" / "misc" / "widget_audit.py").write_text(_WIDGET_AUDIT)
    (tmp_path / "helpers" / "graph" / "build_tree.py").write_text(_BUILD_TREE)
    (tmp_path / "app.py").write_text(_APP)
    (tmp_path / "Makefile").write_text(_MAKEFILE)
    (tmp_path / "tests" / "test_widget_audit.py").write_text(_TEST_WIDGET)
    return tmp_path


@pytest.fixture
def roots(tree):
    return {
        "helpers_root": tree / "helpers",
        "tests_root": tree / "tests",
        "app_py": tree / "app.py",
        "makefile": tree / "Makefile",
    }


def _rebuild(tree, db=None, **kw):
    kw.setdefault("helpers_root", tree / "helpers")
    kw.setdefault("tests_root", tree / "tests")
    kw.setdefault("app_py", tree / "app.py")
    kw.setdefault("makefile", tree / "Makefile")
    return rss.rebuild(db or tree / "script_search.db", embed_fn=_fake_embed, **kw)


def _row(conn, title):
    return conn.execute(
        "SELECT title, kind, area, purpose, content FROM script_search WHERE title = ?",
        (title,),
    ).fetchone()


class TestBuild:
    def test_full_rebuild_row_inventory(self, tree):
        stats = _rebuild(tree)
        assert stats["mode"] == "full"
        assert stats["total_rows"] == 6  # 2 helpers + app.py + 1 test + 2 make
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM script_search GROUP BY kind"))
            assert kinds == {"script": 3, "test": 1, "make": 2}
            areas = dict(conn.execute("SELECT area, COUNT(*) FROM script_search GROUP BY area"))
            assert areas == {"misc": 1, "graph": 1, "app": 1, "test": 1, "make": 2}
        finally:
            conn.close()

    def test_script_row_carries_cli_defs_make_tested_by(self, tree):
        _rebuild(tree)
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            row = _row(conn, "helpers/misc/widget_audit.py")
            content = row[4]
            assert "cli: --apply --db" in content
            assert "subcommands: find" in content
            assert "defs: audit_widgets, main" in content
            assert "make: widget-audit" in content
            assert "tested_by: tests/test_widget_audit.py" in content
            assert row[3] == "Audit widget diffs across releases."
        finally:
            conn.close()

    def test_make_row_annotation_and_scripts(self, tree):
        _rebuild(tree)
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            row = _row(conn, "make widget-audit")
            assert row[3] == "Run the widget audit pass"
            assert "python3 helpers/misc/widget_audit.py --apply" in row[4]
            assert "scripts: helpers/misc/widget_audit.py" in row[4]
            qa = _row(conn, "make qa")
            assert qa[3] == "Run the quality gate"
            assert "scripts: tests/test_widget_audit.py" in qa[4]
        finally:
            conn.close()

    def test_tested_by_is_import_based_not_mention_based(self, tree):
        _rebuild(tree)
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            widget = _row(conn, "helpers/misc/widget_audit.py")
            build_tree = _row(conn, "helpers/graph/build_tree.py")
            # The test file MENTIONS build_tree in its docstring only.
            assert "tests/test_widget_audit.py" in widget[4]
            assert "tested_by" not in build_tree[4]
            test_row = _row(conn, "tests/test_widget_audit.py")
            assert "imports: helpers.misc.widget_audit" in test_row[4]
            assert "build_tree" not in test_row[4].split("imports:", 1)[1]
        finally:
            conn.close()

    def test_no_docstring_falls_back_to_filename(self, tree):
        (tree / "helpers" / "misc" / "silent_tool.py").write_text("x = 1\n")
        _rebuild(tree)
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            row = _row(conn, "helpers/misc/silent_tool.py")
            assert row[3] == "silent tool"
        finally:
            conn.close()

    def test_syntax_error_file_still_indexed_by_path(self, tree):
        (tree / "helpers" / "misc" / "broken.py").write_text("def oops(:\n")
        stats = _rebuild(tree)
        assert stats["total_rows"] == 7
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            assert _row(conn, "helpers/misc/broken.py") is not None
        finally:
            conn.close()

    def test_full_backup_written(self, tree, monkeypatch):
        backup = tree / "db-backup"
        monkeypatch.setattr(rss, "BACKUP_DIR", backup)
        _rebuild(tree)
        assert (backup / "script_search_backup.db.zst").exists()


class TestMakefileParser:
    def test_parse_skips_assignments_and_pseudo_targets(self):
        targets = rss._parse_makefile(_MAKEFILE)
        assert [t["name"] for t in targets] == ["widget-audit", "qa"]

    def test_parse_target_line_vars_keep_first_token(self):
        text = (
            ".RECIPEPREFIX := >\n"
            'relations-enrich ARGS="--source yfinance --dry-run":\n'
            "> python3 helpers/maintenance/enrich_relations.py $(ARGS)\n"
        )
        targets = rss._parse_makefile(text)
        assert len(targets) == 1
        assert targets[0]["name"] == "relations-enrich"
        assert targets[0]["purpose"] == ""  # no ## annotation
        assert "enrich_relations.py" in targets[0]["recipe"]

    def test_recipe_ends_at_comment_block(self):
        text = (
            ".RECIPEPREFIX := >\n"
            "t: ## one\n"
            "> echo one\n"
            "# comment breaks the recipe\n"
            "> echo orphan\n"
        )
        targets = rss._parse_makefile(text)
        assert targets[0]["recipe"] == "echo one"


class TestFreshnessAndIncremental:
    def test_check_fresh_then_drift_exit_code(self, tree, monkeypatch, capsys):
        # main() reads the module constants — retarget ALL of them (the
        # VAULT_ROOT lesson) or --check would default to the live repo tree.
        monkeypatch.setattr(rss, "SCRIPT_DB", tree / "script_search.db")
        monkeypatch.setattr(rss, "HELPERS_ROOT", tree / "helpers")
        monkeypatch.setattr(rss, "TESTS_ROOT", tree / "tests")
        monkeypatch.setattr(rss, "APP_PY", tree / "app.py")
        monkeypatch.setattr(rss, "MAKEFILE", tree / "Makefile")
        monkeypatch.setattr(rss, "BACKUP_DIR", tree / "db-backup")
        _rebuild(tree)
        assert rss.main(["--check"]) == 0
        capsys.readouterr()
        (tree / "helpers" / "misc" / "widget_audit.py").write_text(_WIDGET_AUDIT + "# touched\n")
        assert rss.main(["--check"]) == 1
        err = capsys.readouterr().err
        assert "helpers/misc/widget_audit.py" in err
        assert "rebuild_script_search.py" in err

    def test_check_fresh_after_mtime_drift(self, tree, monkeypatch, capsys):
        """Worktree/checkout regression (2026-08-30): mtime skew on
        identical content must stay FRESH — the content hash is the
        identity of record; mtime is only a carry hint."""
        import os
        import time as _time

        monkeypatch.setattr(rss, "SCRIPT_DB", tree / "script_search.db")
        monkeypatch.setattr(rss, "HELPERS_ROOT", tree / "helpers")
        monkeypatch.setattr(rss, "TESTS_ROOT", tree / "tests")
        monkeypatch.setattr(rss, "APP_PY", tree / "app.py")
        monkeypatch.setattr(rss, "MAKEFILE", tree / "Makefile")
        monkeypatch.setattr(rss, "BACKUP_DIR", tree / "db-backup")
        _rebuild(tree)
        capsys.readouterr()
        future = _time.time() + 1000
        for sub in ("helpers", "tests"):
            for p in (tree / sub).rglob("*.py"):
                os.utime(p, (future, future))
        os.utime(tree / "app.py", (future, future))
        os.utime(tree / "Makefile", (future, future))
        assert rss.main(["--check"]) == 0
        assert "index state: FRESH" in capsys.readouterr().err

    def test_check_flags_makefile_and_new_units(self, tree, monkeypatch):
        monkeypatch.setattr(rss, "SCRIPT_DB", tree / "script_search.db")
        _rebuild(tree)
        (tree / "Makefile").write_text(_MAKEFILE + "extra: ## extra\n> true\n")
        (tree / "helpers" / "misc" / "new_tool.py").write_text('"""Fresh."""\n')
        stats = rss.rebuild(
            tree / "script_search.db",
            write=False,
            embed_fn=_fake_embed,
            helpers_root=tree / "helpers",
            tests_root=tree / "tests",
            app_py=tree / "app.py",
            makefile=tree / "Makefile",
        )
        assert stats["index_stale"]
        assert stats["stale_changed"] == ["Makefile"]
        assert stats["stale_new"] == ["helpers/misc/new_tool.py"]

    def test_incremental_picks_up_cross_file_inputs(self, tree):
        _rebuild(tree)
        # A NEW test importing build_tree must extend build_tree's tested_by —
        # even though build_tree.py itself never changed.
        (tree / "tests" / "test_build_tree.py").write_text(
            '"""Tests for the tree builder."""\n'
            "from helpers.graph import build_tree  # noqa: F401\n"
        )
        stats = _rebuild(tree, incremental=True)
        assert stats["mode"] == "incremental"
        assert stats["upserts"] >= 2  # build_tree row + the new test row
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            row = _row(conn, "helpers/graph/build_tree.py")
            assert "tested_by: tests/test_build_tree.py" in row[4]
            assert _row(conn, "tests/test_build_tree.py") is not None
        finally:
            conn.close()

    def test_incremental_noop_writes_nothing(self, tree):
        _rebuild(tree)
        stats = _rebuild(tree, incremental=True)
        assert stats["upserts"] == 0
        assert stats["deletes"] == 0
        assert stats["indexed"] == 6

    def test_incremental_gcs_deleted_units(self, tree):
        _rebuild(tree)
        (tree / "tests" / "test_widget_audit.py").unlink()
        stats = _rebuild(tree, incremental=True)
        assert stats["deletes"] == 1
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            assert _row(conn, "tests/test_widget_audit.py") is None
            gone = conn.execute(
                "SELECT COUNT(*) FROM script_search_meta "
                "WHERE unit_path = 'tests/test_widget_audit.py'"
            ).fetchone()[0]
            assert gone == 0
            # widget_audit lost its tested_by link (cross-file GC).
            widget = _row(conn, "helpers/misc/widget_audit.py")
            assert "tested_by" not in widget[4]
        finally:
            conn.close()

    def test_staleness_probe(self, tree):
        _rebuild(tree)
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            assert not rss.script_index_stale(
                conn,
                helpers_root=tree / "helpers",
                tests_root=tree / "tests",
                app_py=tree / "app.py",
                makefile=tree / "Makefile",
            )
            (tree / "app.py").write_text(_APP + "# bump\n")
            assert rss.script_index_stale(
                conn,
                helpers_root=tree / "helpers",
                tests_root=tree / "tests",
                app_py=tree / "app.py",
                makefile=tree / "Makefile",
            )
        finally:
            conn.close()
