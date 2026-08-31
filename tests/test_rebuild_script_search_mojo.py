"""
Tests for the Mojo footprint in rebuild_script_search.py — `mojo doc` JSON
ingestion, content-addressed doc cache, kind='mojo' rows, staleness flow
(mojo_doc_script_search proposal).

Hermetic: tmp mini-tree INCLUDING Mojo/src + Mojo/tests, injected fake
embedder, and a FAKE doc generator (rss._run_mojo_doc monkeypatched — the
real toolchain costs ~3.5 s/file and must never run under pytest). The
roots-derivation contract (<helpers parent>/Mojo/...) is what keeps the
LIVE Mojo tree out of these fixtures.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "helpers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from helpers.maintenance import rebuild_script_search as rss  # noqa: E402

pytestmark = [pytest.mark.integration]

_WIDGET_MOJO = (
    "# Mojo bench probe — drives the widget surface end to end.\n"
    "#\n"
    "# Longer header paragraph: parity is GATED on the canonical strings.\n"
    "def run_probe(x: Int) -> String:\n"
    "    return String(x)\n"
)

_TEST_MOJO = "# Tests for the widget probe.\nfrom tests.test_widget import check\n"

_FAKE_DECL = {
    "decl": {
        "name": "widget",
        "description": "",
        "functions": [
            {
                "name": "run_probe",
                "overloads": [
                    {
                        "signature": "def run_probe(x: Int) -> String",
                        "summary": "Drives the probe once per rep.",
                        "args": [{"name": "x", "type": "Int"}],
                    }
                ],
            },
        ],
        "aliases": [
            {"name": "SEED", "value": "42"},
        ],
        "structs": [],
        "traits": [],
    }
}


def _fake_embed(text: str) -> list[float]:
    import math

    v = [0.0] * 8
    if "widget" in text.lower():
        v[2] = 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Mini repo with a Mojo footprint + faked doc generation. Roots are
    retargeted BOTH via rebuild kwargs and the module constants (the
    rss.main() path reads the constants — the VAULT_ROOT lesson)."""
    tree = tmp_path
    (tree / "helpers" / "misc").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "helpers" / "misc" / "widget_audit.py").write_text('"""Audit widget diffs."""\n')
    (tree / "tests" / "test_widget_audit.py").write_text(
        '"""Tests."""\nfrom helpers.misc import widget_audit  # noqa: F401\n'
    )
    (tree / "Makefile").write_text(
        ".RECIPEPREFIX := >\nwidget-audit: ## audit\n> python3 helpers/misc/widget_audit.py\n"
    )
    (tree / "Mojo" / "src" / "bench").mkdir(parents=True)
    (tree / "Mojo" / "tests").mkdir()
    (tree / "Mojo" / "src" / "bench" / "widget.mojo").write_text(_WIDGET_MOJO)
    (tree / "Mojo" / "tests" / "test_widget.mojo").write_text(_TEST_MOJO)

    calls: list[Path] = []

    def fake_doc(path, src_root, mojo_bin=None):
        calls.append(path)
        return json.dumps(_FAKE_DECL)

    monkeypatch.setattr(rss, "_run_mojo_doc", fake_doc)
    monkeypatch.setattr(rss, "HELPERS_ROOT", tree / "helpers")
    monkeypatch.setattr(rss, "TESTS_ROOT", tree / "tests")
    monkeypatch.setattr(rss, "APP_PY", tree / "app.py")
    monkeypatch.setattr(rss, "MAKEFILE", tree / "Makefile")
    monkeypatch.setattr(rss, "SCRIPT_DB", tree / "script_search.db")
    monkeypatch.setattr(rss, "BACKUP_DIR", tree / "db-backup")
    return {"tree": tree, "calls": calls}


def _rebuild(env, db=None, **kw):
    tree = env["tree"]
    kw.setdefault("helpers_root", tree / "helpers")
    kw.setdefault("tests_root", tree / "tests")
    kw.setdefault("app_py", tree / "app.py")
    kw.setdefault("makefile", tree / "Makefile")
    return rss.rebuild(db or tree / "script_search.db", embed_fn=_fake_embed, **kw)


class TestMojoRows:
    def test_mojo_rows_composed(self, env):
        stats = _rebuild(env)
        tree = env["tree"]
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM script_search GROUP BY kind"))
            assert kinds["mojo"] == 2  # src/bench/widget.mojo + test_widget.mojo
            row = conn.execute(
                "SELECT title, kind, area, purpose, content FROM script_search "
                "WHERE title = 'Mojo/src/bench/widget.mojo'"
            ).fetchone()
        finally:
            conn.close()
        assert row[1] == "mojo" and row[2] == "bench"
        # purpose: decl.description is empty -> leading '#' header paragraph
        assert row[3].startswith("Mojo bench probe — drives the widget surface")
        # api block: signature + summary + alias value
        assert "fn def run_probe(x: Int) -> String — Drives the probe once per rep." in row[4]
        assert "alias SEED = 42" in row[4]
        assert stats["total_units"] == 5  # 1 py + 1 test + 1 Makefile + 2 mojo

    def test_doc_cache_hit_skips_regen_until_hash_changes(self, env):
        _rebuild(env)
        assert len(env["calls"]) == 2
        stats2 = _rebuild(env)
        assert len(env["calls"]) == 2  # unchanged sources: 0 regens
        assert stats2["content_changed"] is False  # cache-hit rows == generated rows
        # Regression pin: the cached path must unwrap the stored document to
        # its decl EXACTLY like the generated path — returning the full doc
        # silently stripped every api: block (and re-embedded 13 rows).
        conn = rss.connect_script_db(env["tree"] / "script_search.db")
        try:
            content = conn.execute(
                "SELECT content FROM script_search WHERE title = 'Mojo/src/bench/widget.mojo'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert "fn def run_probe(x: Int) -> String" in content
        (env["tree"] / "Mojo" / "src" / "bench" / "widget.mojo").write_text(
            _WIDGET_MOJO + "\n# touched\n"
        )
        _rebuild(env)
        assert len(env["calls"]) == 3  # only the changed unit regenerated

    def test_purpose_prefers_module_docstring(self, env, monkeypatch):
        decl = json.loads(json.dumps(_FAKE_DECL))
        # mojo doc splits the docstring: first paragraph -> summary, rest ->
        # description; the purpose must join BOTH in order.
        decl["decl"]["summary"] = "Module docstring wins over the header."
        decl["decl"]["description"] = "Remainder follows."
        monkeypatch.setattr(rss, "_run_mojo_doc", lambda *a, **k: json.dumps(decl))
        _rebuild(env)
        conn = rss.connect_script_db(env["tree"] / "script_search.db")
        try:
            (purpose,) = conn.execute(
                "SELECT purpose FROM script_search WHERE title = 'Mojo/src/bench/widget.mojo'"
            ).fetchone()
        finally:
            conn.close()
        assert purpose == ("Module docstring wins over the header. Remainder follows.")

    def test_doc_failure_degrades_to_stored_doc(self, env, monkeypatch):
        _rebuild(env)  # good doc cached
        # Changed source + doc generation fails (binary missing): the unit
        # must still be indexed — via the LAST stored doc, never dropped.
        (env["tree"] / "Mojo" / "src" / "bench" / "widget.mojo").write_text(
            _WIDGET_MOJO + "\n# touched\n"
        )
        monkeypatch.setattr(rss, "_run_mojo_doc", lambda *a, **k: None)
        stats = _rebuild(env)
        assert stats["total_units"] == 5
        conn = rss.connect_script_db(env["tree"] / "script_search.db")
        try:
            row = conn.execute(
                "SELECT content FROM script_search WHERE title = 'Mojo/src/bench/widget.mojo'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert "run_probe" in row[0]  # last-stored doc's API block survived

    def test_mojo_units_flow_through_freshness(self, env, capsys):
        _rebuild(env)
        assert rss.main(["--check"]) == 0
        capsys.readouterr()
        (env["tree"] / "Mojo" / "tests" / "test_widget.mojo").write_text(
            _TEST_MOJO + "\n# touched\n"
        )
        assert rss.main(["--check"]) == 1
        err = capsys.readouterr().err
        assert "Mojo/tests/test_widget.mojo" in err

    def test_kind_and_area_filters(self, env):
        _rebuild(env)
        conn = rss.connect_script_db(env["tree"] / "script_search.db")
        try:
            mojo_hits = rss.search_scripts(conn, "probe", kind="mojo")
            script_hits = rss.search_scripts(conn, "probe", kind="script")
            bench_hits = rss.search_scripts(conn, "probe", area="bench")
        finally:
            conn.close()
        paths = {h["path"] for h in mojo_hits["results"]}
        assert "Mojo/src/bench/widget.mojo" in paths
        assert "Mojo/tests/test_widget.mojo" in paths
        assert all(h["kind"] == "mojo" for h in mojo_hits["results"])
        assert all(h["path"] != "Mojo/src/bench/widget.mojo" for h in script_hits["results"])
        assert {h["area"] for h in bench_hits["results"]} == {"bench"}

    def test_new_mojo_file_is_stale_new(self, env):
        _rebuild(env)
        (env["tree"] / "Mojo" / "src" / "bench" / "second.mojo").write_text(
            "# second probe module\n"
        )
        stats = _rebuild(env, write=False)
        assert stats["stale_new"] == ["Mojo/src/bench/second.mojo"]
