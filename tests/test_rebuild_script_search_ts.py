"""
Tests for the TS footprint in rebuild_script_search.py — extractor JSON
ingestion, content-addressed doc cache, kind='ts' rows, staleness flow
(corpus_uniformity proposal S6).

Hermetic: tmp mini-tree INCLUDING frontend/src + frontend/types, injected
fake embedder, and a FAKE extractor (rss._run_ts_extract monkeypatched —
the real node startup costs ~150 ms/file and must never run under
pytest). The roots-derivation contract (<helpers parent>/frontend/...)
is what keeps the LIVE frontend out of these fixtures. Mirrors
test_rebuild_script_search_mojo.py one-for-one.
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

_VIEW_TS = (
    "/**\n"
    " * Stats view — summary tiles plus a sector table.\n"
    " *\n"
    " * Longer prose: refresh re-runs the query and diff-patches the DOM.\n"
    " */\n"
    "export class StatsView {\n"
    "  mount(el: HTMLElement): void {\n"
    '    el.textContent = "stats";\n'
    "  }\n"
    "}\n"
)

_API_TS = (
    "/**\n"
    " * Typed fetch client for the /api/* surface.\n"
    " */\n"
    "export interface StatsResponse {\n"
    "  entities: number;\n"
    "}\n"
)

_FAKE_TS_DECL = {
    "module_doc": "",
    "exports": [
        {
            "name": "StatsView",
            "signature": "class StatsView",
            "doc": "Renders the summary tiles.",
        },
    ],
}


def _fake_embed(text: str) -> list[float]:
    import math

    v = [0.0] * 8
    if "stats" in text.lower():
        v[2] = 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Mini repo with a TS footprint + faked extraction. Roots are
    retargeted BOTH via rebuild kwargs and the module constants (the
    rss.main() path reads the constants — the VAULT_ROOT lesson)."""
    tree = tmp_path
    (tree / "helpers" / "misc").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "helpers" / "misc" / "stats_audit.py").write_text('"""Audit stats diffs."""\n')
    (tree / "tests" / "test_stats_audit.py").write_text(
        '"""Tests."""\nfrom helpers.misc import stats_audit  # noqa: F401\n'
    )
    (tree / "Makefile").write_text(
        ".RECIPEPREFIX := >\nstats-audit: ## audit\n> python3 helpers/misc/stats_audit.py\n"
    )
    (tree / "frontend" / "src" / "views").mkdir(parents=True)
    (tree / "frontend" / "types").mkdir()
    (tree / "frontend" / "src" / "views" / "stats.ts").write_text(_VIEW_TS)
    (tree / "frontend" / "types" / "api.ts").write_text(_API_TS)

    calls: list[Path] = []

    def fake_extract(path, node_bin=None):
        calls.append(path)
        if path.name == "api.ts":
            return json.dumps(
                {
                    "module_doc": "Response contracts for the stats endpoint.",
                    "exports": [
                        {
                            "name": "StatsResponse",
                            "signature": "interface StatsResponse",
                            "doc": "Summary tiles payload.",
                        }
                    ],
                }
            )
        return json.dumps(_FAKE_TS_DECL)

    monkeypatch.setattr(rss, "_run_ts_extract", fake_extract)
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


class TestTsRows:
    def test_ts_rows_composed(self, env):
        stats = _rebuild(env)
        tree = env["tree"]
        conn = rss.connect_script_db(tree / "script_search.db")
        try:
            kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM script_search GROUP BY kind"))
            assert kinds["ts"] == 2  # src/views/stats.ts + types/api.ts
            row = conn.execute(
                "SELECT title, kind, area, purpose, content FROM script_search "
                "WHERE title = 'frontend/src/views/stats.ts'"
            ).fetchone()
        finally:
            conn.close()
        assert row[1] == "ts" and row[2] == "views"
        # purpose: module_doc empty -> leading block comment paragraph
        assert row[3].startswith("Stats view — summary tiles plus a sector table")
        # api block: signature + JSDoc line
        assert "class StatsView — Renders the summary tiles." in row[4]
        assert stats["total_units"] == 5  # 1 py + 1 test + 1 Makefile + 2 ts

    def test_types_row_area(self, env):
        _rebuild(env)
        conn = rss.connect_script_db(env["tree"] / "script_search.db")
        try:
            row = conn.execute(
                "SELECT area FROM script_search WHERE title = 'frontend/types/api.ts'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "types"

    def test_extract_cache_hit_skips_node_until_hash_changes(self, env):
        _rebuild(env)
        assert len(env["calls"]) == 2
        stats2 = _rebuild(env)
        assert len(env["calls"]) == 2  # unchanged sources: 0 extractions
        assert stats2["content_changed"] is False
        (env["tree"] / "frontend" / "src" / "views" / "stats.ts").write_text(
            _VIEW_TS + "\n// touched\n"
        )
        _rebuild(env)
        assert len(env["calls"]) == 3  # only the changed unit re-extracted

    def test_purpose_prefers_module_doc(self, env, monkeypatch):
        decl = json.loads(json.dumps(_FAKE_TS_DECL))
        decl["module_doc"] = "Module doc wins over the block comment."
        monkeypatch.setattr(rss, "_run_ts_extract", lambda *a, **k: json.dumps(decl))
        _rebuild(env)
        conn = rss.connect_script_db(env["tree"] / "script_search.db")
        try:
            (purpose,) = conn.execute(
                "SELECT purpose FROM script_search WHERE title = 'frontend/src/views/stats.ts'"
            ).fetchone()
        finally:
            conn.close()
        assert purpose == "Module doc wins over the block comment."

    def test_extraction_failure_degrades_to_stored_doc(self, env, monkeypatch):
        _rebuild(env)  # good extract cached
        # Changed source + extraction fails (node missing): the unit must
        # still be indexed — via the LAST stored doc, never dropped.
        (env["tree"] / "frontend" / "src" / "views" / "stats.ts").write_text(
            _VIEW_TS + "\n// touched\n"
        )
        monkeypatch.setattr(rss, "_run_ts_extract", lambda *a, **k: None)
        stats = _rebuild(env)
        assert stats["total_units"] == 5
        conn = rss.connect_script_db(env["tree"] / "script_search.db")
        try:
            row = conn.execute(
                "SELECT content FROM script_search WHERE title = 'frontend/src/views/stats.ts'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert "StatsView" in row[0]  # last-stored doc's API block survived

    def test_ts_units_flow_through_freshness(self, env, capsys):
        _rebuild(env)
        assert rss.main(["--check"]) == 0
        capsys.readouterr()
        (env["tree"] / "frontend" / "types" / "api.ts").write_text(_API_TS + "\n// touched\n")
        assert rss.main(["--check"]) == 1
        err = capsys.readouterr().err
        assert "frontend/types/api.ts" in err

    def test_kind_and_area_filters(self, env):
        _rebuild(env)
        conn = rss.connect_script_db(env["tree"] / "script_search.db")
        try:
            ts_hits = rss.search_scripts(conn, "stats", kind="ts")
            script_hits = rss.search_scripts(conn, "stats", kind="script")
            views_hits = rss.search_scripts(conn, "stats", area="views")
        finally:
            conn.close()
        paths = {h["path"] for h in ts_hits["results"]}
        assert "frontend/src/views/stats.ts" in paths
        assert "frontend/types/api.ts" in paths
        assert all(h["kind"] == "ts" for h in ts_hits["results"])
        assert all(h["path"] != "frontend/src/views/stats.ts" for h in script_hits["results"])
        assert {h["area"] for h in views_hits["results"]} == {"views"}

    def test_new_ts_file_is_stale_new(self, env):
        _rebuild(env)
        core = env["tree"] / "frontend" / "src" / "core"
        core.mkdir(parents=True, exist_ok=True)
        (core / "dom.ts").write_text(
            "/** dom helpers */\nexport const byId = (id: string) =>\n"
            "  document.getElementById(id);\n"
        )
        stats = _rebuild(env, write=False)
        assert stats["stale_new"] == ["frontend/src/core/dom.ts"]
