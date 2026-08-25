"""
Tests for helpers/misc/script_query.py — the agent-facing CLI over the
script_search sidecar index (script_metadata_search proposal S2). Hermetic:
tmp mini-tree + sidecar, fake embedder (mirrors tests/test_doc_query.py).
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "helpers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from helpers.misc import script_query  # noqa: E402
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
    '"""Audit widget diffs across releases.\n\nDetail paragraph.\n"""\n'
    "import argparse\n"
    "def main():\n"
    "    p = argparse.ArgumentParser()\n"
    '    p.add_argument("--apply", action="store_true")\n'
)

_TEST_WIDGET = (
    '"""Tests for the widget audit CLI."""\n'
    "from helpers.misc import widget_audit  # noqa: F401\n"
)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    tree = tmp_path
    (tree / "helpers" / "misc").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "helpers" / "misc" / "widget_audit.py").write_text(_WIDGET_AUDIT)
    (tree / "helpers" / "graph").mkdir()
    (tree / "helpers" / "graph" / "build_tree.py").write_text(
        '"""Build the sector hierarchy tree from DB rows."""\n'
    )
    (tree / "app.py").write_text('"""Flask app serving the FinData API."""\n')
    (tree / "Makefile").write_text(_MAKEFILE)
    (tree / "tests" / "test_widget_audit.py").write_text(_TEST_WIDGET)
    # Retarget ALL module constants (the VAULT_ROOT lesson) — the CLI's
    # defaults must land inside the tmp tree, never the live repo.
    monkeypatch.setattr(rss, "SCRIPT_DB", tree / "script_search.db")
    monkeypatch.setattr(rss, "HELPERS_ROOT", tree / "helpers")
    monkeypatch.setattr(rss, "TESTS_ROOT", tree / "tests")
    monkeypatch.setattr(rss, "APP_PY", tree / "app.py")
    monkeypatch.setattr(rss, "MAKEFILE", tree / "Makefile")
    monkeypatch.setattr(rss, "BACKUP_DIR", tree / "db-backup")
    return tree


@pytest.fixture
def fake_local(seeded, monkeypatch):
    from helpers.core import local_embedder as LE

    def _vec(text: str) -> list[float]:
        import math

        v = [0.0] * 8
        if "widget" in text.lower():
            v[2] = 1.0
        if "gate" in text.lower():
            v[3] = 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    monkeypatch.setattr(LE, "available", lambda: True)
    monkeypatch.setattr(LE, "embed_document", _vec)
    monkeypatch.setattr(LE, "embed_query", _vec)
    monkeypatch.setattr(LE, "DIM", 8)
    rss.rebuild(write=True)  # resolve_embedder -> fake via local_embedder
    return LE


class TestScriptQueryCli:
    def test_hits_print_path_kind_area_score_purpose(self, fake_local, capsys):
        rc = script_query.main(["widget audit"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "helpers/misc/widget_audit.py" in out
        assert "[script/misc]" in out
        assert "Audit widget diffs across releases." in out
        # <mark> tags are stripped for terminal output.
        assert "<mark>" not in out

    def test_make_rows_display_as_make_target(self, fake_local, capsys):
        rc = script_query.main(["quality gate", "--kind", "make"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "make qa  [make/make]" in out
        assert "Run the quality gate" in out

    def test_kind_filter_excludes_other_rows(self, fake_local, capsys):
        rc = script_query.main(["widget", "--kind", "test", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"]
        assert all(h["kind"] == "test" for h in payload["results"])

    def test_area_filter(self, fake_local, capsys):
        rc = script_query.main(["build", "--area", "graph", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"]
        assert all(h["area"] == "graph" for h in payload["results"])
        assert payload["results"][0]["path"] == "helpers/graph/build_tree.py"

    def test_json_mode_field_shape(self, fake_local, capsys):
        rc = script_query.main(["widget", "--json", "--limit", "3"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] in ("hybrid", "bm25")
        assert payload["results"]
        assert {
            "path", "title", "kind", "area", "purpose",
            "snippet", "score", "similarity",
        } <= set(payload["results"][0])

    def test_missing_index_exit_1_with_hint(self, seeded, capsys):
        rc = script_query.main(["widget"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "rebuild_script_search.py" in err

    def test_stale_index_still_answers_with_warning(self, fake_local, capsys):
        audit = Path(rss.HELPERS_ROOT) / "misc" / "widget_audit.py"
        future = time.time() + 10
        os.utime(audit, (future, future))
        rc = script_query.main(["widget audit"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "widget_audit.py" in captured.out

    def test_bm25_flag(self, fake_local, capsys):
        rc = script_query.main(["widget", "--bm25", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["mode"] == "bm25"

    def test_no_hits_exit_0(self, fake_local, capsys):
        rc = script_query.main(["zzz_never_matches", "--bm25"])
        assert rc == 0
        assert "no hits" in capsys.readouterr().err

    def test_golden_top_hits(self, fake_local, capsys):
        """The proposal's success shape: the right artifact in the top-3."""
        rc = script_query.main(["widget audit", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        top3 = [h["path"] for h in payload["results"][:3]]
        assert "helpers/misc/widget_audit.py" in top3

        capsys.readouterr()
        rc = script_query.main(["what does the quality gate run", "--kind", "make", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["path"] == "qa"

        capsys.readouterr()
        rc = script_query.main(["tests for the widget audit", "--kind", "test", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"][0]["path"] == "tests/test_widget_audit.py"
