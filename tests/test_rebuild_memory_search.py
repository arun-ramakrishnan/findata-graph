"""
Tests for helpers/maintenance/rebuild_memory_search.py + helpers/misc/
memory_query.py — the builder/query pair behind the memory_search sidecar
index over the three harness memory pools. Hermetic: tmp pools + sidecar,
injected/monkeypatched embedder (mirrors tests/test_rebuild_script_search.py
/ tests/test_script_query.py).
"""

import json
import os
import time
from pathlib import Path

import pytest


from helpers.core import gemma_embedder
from helpers.maintenance import rebuild_memory_search as rms  # noqa: E402
from helpers.misc import memory_query  # noqa: E402

pytestmark = [pytest.mark.integration]

_ZCODE_NOTE = (
    "---\n"
    "name: user-workflow\n"
    "description: Operator contract for gates, patches\n"
    "  and staging discipline\n"
    "metadata:\n"
    "  node_type: memory\n"
    "  type: user\n"
    "---\n"
    "\n"
    "The operator owns stgit patch structure; agents edit files.\n"
)

_ZCODE_INDEX = "# Memory index\n\n- [user-workflow](user-workflow.md) — gates, patches, staging\n"

_PRIME_STATE = {
    "schema": 1,
    "entries": {
        "prompt": {},
        "memory": {
            "findata_graph_gate_idioms": {
                "id": "findata_graph_gate_idioms",
                "kind": "memory",
                "title": "gate_query test idioms",
                "content": "Use TARGETED tests only; never make pytest after a fix.",
            },
            "findata_graph_witr_first": {
                "id": "findata_graph_witr_first",
                "kind": "memory",
                "title": "witr before ps",
                "content": "Run witr -f <db> to name a DuckDB lock holder.",
            },
        },
        "skill": {},
        "subagent": {},
    },
}

_OPENCODE_LOGFMT = (
    "ts=2026-09-24T13:54:48.990Z type=context scope=findata-graph/identity "
    'content="Canonical identity: repo \\"findata-graph\\", origin fixed." '
    "tags=identity,harness-shared\n"
    "ts=2026-09-24T13:54:54.010Z type=preference scope=findata-graph/operator-workflow "
    'content="Follow the repository AGENTS.md as the authority for gates." '
    "tags=workflow,operator-contract\n"
    # plugin escapeValue contract: a REAL newline inside content rides as
    # the two-char \n escape — must decode back to a newline, not 'n'
    "ts=2026-09-25T00:00:00Z type=context scope=findata-graph/multiline "
    'content="alpha\\nbeta" tags=x\n'
)
_OPENCODE_DELETIONS = 'ts=2026-09-24T00:00:00Z type=context scope=x content="deleted record"\n'


def _fake_embed(text: str) -> list[float]:
    import math

    v = [0.0] * 8
    low = text.lower()
    if "operator" in low or "gates" in low:
        v[2] = 1.0
    if "witr" in low or "duckdb" in low:
        v[3] = 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _write_pools(tree):
    """Mini three-pool layout under tmp: zcode (with a symlinked second
    scope), prime, opencode (incl. the deletions audit file)."""
    mem = tree / "zcode-projects" / "scope-main" / "memory"
    mem.mkdir(parents=True)
    (mem / "user-workflow.md").write_text(_ZCODE_NOTE)
    (mem / "MEMORY.md").write_text(_ZCODE_INDEX)
    (tree / "zcode-projects" / "scope-wt").mkdir()
    (tree / "zcode-projects" / "scope-wt" / "memory").symlink_to(
        "../scope-main/memory", target_is_directory=True
    )
    harness = tree / "prime" / "harness"
    harness.mkdir(parents=True)
    (harness / "harness_state.json").write_text(json.dumps(_PRIME_STATE))
    oc = tree / "opencode"
    oc.mkdir()
    (oc / "2026-09-24.logfmt").write_text(_OPENCODE_LOGFMT)
    (oc / "deletions.logfmt").write_text(_OPENCODE_DELETIONS)


@pytest.fixture(autouse=True)
def _isolated_backup(tmp_path, monkeypatch):
    """Full-rebuild tests must never write the REAL db-backup/ (the
    un-redirected module BACKUP_DIR lesson from test_rebuild_script_search)."""
    monkeypatch.setattr(rms, "BACKUP_DIR", tmp_path / "db-backup")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Mini three-pool layout; ALL module constants retargeted (the
    VAULT_ROOT lesson) so CLI defaults land inside tmp, never the live repo."""
    _write_pools(tmp_path)
    monkeypatch.setattr(rms, "MEMORY_DB", tmp_path / "memory_search.db")
    monkeypatch.setattr(rms, "ZCODE_PROJECTS", tmp_path / "zcode-projects")
    monkeypatch.setattr(rms, "PRIME_STATE", tmp_path / "prime" / "harness" / "harness_state.json")
    monkeypatch.setattr(rms, "OPENCODE_MEM", tmp_path / "opencode")
    return tmp_path


def _rebuild(tree, db=None, **kw):
    kw.setdefault("zcode_projects", tree / "zcode-projects")
    kw.setdefault("prime_state", tree / "prime" / "harness" / "harness_state.json")
    kw.setdefault("opencode_dir", tree / "opencode")
    return rms.rebuild(db or tree / "memory_search.db", embed_fn=_fake_embed, **kw)


class TestMemoryGemmaMigration:
    """memory_search gemma migration (2026-10-08): per-surface selector,
    top-level demotion guard, gemma prefix basis, stamp-keyed query side.
    Mirrors the script-adoption contract tests."""

    def _gemma_rebuild(self, tree, calls):
        """Rebuild with the gemma path forced via a recording embedder.
        Patches are scoped to THIS call (pytest.MonkeyPatch.context) so a
        later phase in the same test sees the REAL resolver — the refusal
        test depends on that."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(gemma_embedder, "available", lambda port=None: True)

            def _rec(text: str) -> list[float]:
                calls.append(text)
                return [0.5] * gemma_embedder.DIM

            mp.setattr(
                rms,
                "resolve_memory_embedder",
                lambda: (_rec, gemma_embedder.DIM, gemma_embedder.MODEL_LABEL, True),
            )
            kw = dict(
                zcode_projects=tree / "zcode-projects",
                prime_state=tree / "prime" / "harness" / "harness_state.json",
                opencode_dir=tree / "opencode",
                embed_fn=None,
            )
            return rms.rebuild(tree / "memory_search.db", **kw)

    def test_gemma_stamp_and_prefix_basis(self, tree, monkeypatch):
        calls: list[str] = []
        stats = self._gemma_rebuild(tree, calls)
        assert stats["embed_model"] == gemma_embedder.MODEL_LABEL
        assert stats["embedded"] == stats["total_rows"] == 7

        con = __import__("sqlite3").connect(str(tree / "memory_search.db"))
        try:
            info = dict(con.execute("SELECT key, value FROM memory_search_info").fetchall())
        finally:
            con.close()
        assert info == {
            "embed_model": gemma_embedder.MODEL_LABEL,
            "embed_dims": str(gemma_embedder.DIM),
        }
        # every basis is gemma-prefixed over the same title/purpose/content
        assert calls and all(c.startswith("title: ") and " | text: " in c for c in calls)

    def test_gemma_stamp_sidecar_down_refuses(self, tree, monkeypatch):
        calls: list[str] = []
        self._gemma_rebuild(tree, calls)  # stamp the index gemma
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: False)
        monkeypatch.delenv("MEMORY_EMBEDDER", raising=False)

        kw = dict(
            zcode_projects=tree / "zcode-projects",
            prime_state=tree / "prime" / "harness" / "harness_state.json",
            opencode_dir=tree / "opencode",
            embed_fn=None,
        )
        with pytest.raises(gemma_embedder.GemmaStampDemotion) as ei:
            rms.rebuild(tree / "memory_search.db", **kw)
        assert "make embgemma-server" in str(ei.value)

        # the explicit escape completes (demotion on record)
        monkeypatch.setenv("MEMORY_EMBEDDER", "granite")
        stats = rms.rebuild(tree / "memory_search.db", **kw)
        assert stats["embed_model"] != gemma_embedder.MODEL_LABEL

    def test_cosine_leg_query_side_is_stamp_keyed(self, tree, monkeypatch):
        import sqlite3

        calls: list[str] = []
        self._gemma_rebuild(tree, calls)  # gemma-stamped index

        seen: list[str] = []

        def _fake_query(_q: str) -> list[float]:
            seen.append(_q)
            return [0.5] * gemma_embedder.DIM

        monkeypatch.setattr(gemma_embedder, "embed_query", _fake_query)
        monkeypatch.setattr(gemma_embedder, "available", lambda port=None: True)

        con = sqlite3.connect(str(tree / "memory_search.db"))
        try:
            scored, _sims = rms._cosine_leg(con, "gate idioms")
        finally:
            con.close()
        assert seen == ["gate idioms"]  # the gemma query embedder ran
        assert scored  # the 512-d vectors rank against the 512-d index


class TestBuild:
    def test_full_rebuild_row_inventory(self, tree):
        stats = _rebuild(tree)
        assert stats["mode"] == "full"
        # 2 zcode files (symlinked scope deduped) + 2 prime + 3 opencode
        assert stats["by_kind"] == {"zcode": 2, "prime": 2, "opencode": 3}
        assert stats["total_rows"] == 7

    def test_full_backup_written(self, tree):
        _rebuild(tree)
        assert (tree / "db-backup" / "memory_search_backup.db.zst").exists()

    def test_zcode_frontmatter_folded_description(self, tree):
        _rebuild(tree)
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            row = conn.execute(
                "SELECT title, purpose, content, source_path FROM memory_search WHERE name = 'user-workflow'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "user-workflow"
        assert row[1] == "Operator contract for gates, patches and staging discipline"
        assert row[2].startswith("type: user\n")
        assert "stgit patch structure" in row[2]
        assert row[3].endswith("user-workflow.md")

    def test_abbrev_home_prefixes_under_home(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/fake")
        assert rms._abbrev_home(Path("/home/fake/x/memory/a.md")) == "~/x/memory/a.md"
        assert rms._abbrev_home(Path("/tmp/elsewhere/a.md")) == "/tmp/elsewhere/a.md"  # noqa: S108  # fixture: non-HOME path must not be abbreviated

    def test_memory_md_row_without_frontmatter(self, tree):
        _rebuild(tree)
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            row = conn.execute(
                "SELECT title, name FROM memory_search WHERE kind = 'zcode' AND name = 'MEMORY'"
            ).fetchone()
        finally:
            conn.close()
        assert tuple(row) == ("MEMORY.md index", "MEMORY")

    def test_prime_rows(self, tree):
        _rebuild(tree)
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            row = conn.execute(
                "SELECT purpose, source_path, content FROM memory_search "
                "WHERE kind = 'prime' AND name = 'findata_graph_witr_first'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "witr before ps"
        assert row[1].endswith("harness_state.json#findata_graph_witr_first")
        assert "witr -f" in row[2]

    def test_opencode_rows_parse_and_skip_deletions(self, tree):
        _rebuild(tree)
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            rows = conn.execute(
                "SELECT name, purpose, content FROM memory_search WHERE kind = 'opencode' ORDER BY name"
            ).fetchall()
        finally:
            conn.close()
        assert [r[0] for r in rows] == ["2026-09-24#1", "2026-09-24#2", "2026-09-24#3"]
        assert rows[0][1] == "findata-graph/identity"
        # escaped quotes in the logfmt content are unescaped
        assert '"findata-graph"' in rows[0][2]
        # and the \n escape decodes to a REAL newline (plugin escapeValue)
        assert "alpha\nbeta" in rows[2][2]
        assert "deletions" not in " ".join(r[0] for r in rows)

    def test_empty_pools_rebuild_clean(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rms, "MEMORY_DB", tmp_path / "memory_search.db")
        monkeypatch.setattr(rms, "ZCODE_PROJECTS", tmp_path / "absent-zcode")
        monkeypatch.setattr(rms, "PRIME_STATE", tmp_path / "absent-prime.json")
        monkeypatch.setattr(rms, "OPENCODE_MEM", tmp_path / "absent-opencode")
        stats = rms.rebuild(tmp_path / "memory_search.db", embed_fn=_fake_embed)
        assert stats["total_rows"] == 0
        assert not stats["index_stale"]


class TestFreshnessAndIncremental:
    def test_check_fresh_then_drift_exit_code(self, tree, monkeypatch, capsys):
        # main() reads the module constants — retarget ALL of them (the
        # VAULT_ROOT lesson) or --check would default to the live pools.
        monkeypatch.setattr(rms, "BACKUP_DIR", tree / "db-backup")
        _rebuild(tree)
        assert rms.main(["--check"]) == 0
        capsys.readouterr()
        state = tree / "prime" / "harness" / "harness_state.json"
        doc = json.loads(state.read_text())
        doc["entries"]["memory"]["findata_graph_new"] = {
            "id": "findata_graph_new",
            "title": "fresh convention",
            "content": "Newly added prime memory.",
        }
        state.write_text(json.dumps(doc))
        assert rms.main(["--check"]) == 1
        err = capsys.readouterr().err
        assert "harness_state.json" in err
        assert "rebuild_memory_search.py" in err

    def test_check_fresh_after_mtime_drift(self, tree, monkeypatch, capsys):
        """Worktree/checkout regression (2026-08-30): mtime skew on
        identical content must stay FRESH — the content hash is the
        identity of record; mtime is only a carry hint."""
        monkeypatch.setattr(rms, "BACKUP_DIR", tree / "db-backup")
        _rebuild(tree)
        capsys.readouterr()
        future = time.time() + 1000
        for p in (tree / "zcode-projects").rglob("*.md"):
            os.utime(p, (future, future))
        os.utime(tree / "prime" / "harness" / "harness_state.json", (future, future))
        os.utime(tree / "opencode" / "2026-09-24.logfmt", (future, future))
        assert rms.main(["--check"]) == 0
        assert "index state: FRESH" in capsys.readouterr().err

    def test_incremental_picks_up_and_gcs(self, tree):
        _rebuild(tree)
        mem = tree / "zcode-projects" / "scope-main" / "memory"
        (mem / "new-doctrine.md").write_text(
            "---\nname: new-doctrine\ndescription: fresh\n---\n\nJust landed.\n"
        )
        (tree / "opencode" / "2026-09-24.logfmt").unlink()
        stats = _rebuild(tree, incremental=True)
        assert stats["mode"] == "incremental"
        assert stats["upserts"] == 1
        assert stats["deletes"] == 3  # all three opencode records gone
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            kinds = {
                r[0] for r in conn.execute("SELECT DISTINCT kind FROM memory_search").fetchall()
            }
            meta = {
                r[0] for r in conn.execute("SELECT unit_path FROM memory_search_meta").fetchall()
            }
        finally:
            conn.close()
        assert "opencode" not in kinds
        assert not any("2026-09-24.logfmt" in m for m in meta)

    def test_staleness_probe(self, tree):
        _rebuild(tree)
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            assert rms.memory_index_stale(conn) is False
        finally:
            conn.close()
        (tree / "zcode-projects" / "scope-main" / "memory" / "user-workflow.md").write_text(
            _ZCODE_NOTE + "\nEdited doctrine.\n"
        )
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            assert rms.memory_index_stale(conn) is True
        finally:
            conn.close()


class TestSearch:
    @pytest.fixture
    def fake_local(self, tree, monkeypatch):
        from helpers.core import local_embedder as LE

        def _vec(text: str) -> list[float]:
            return _fake_embed(text)

        monkeypatch.setattr(LE, "available", lambda: True)
        monkeypatch.setattr(LE, "embed_document", _vec)
        monkeypatch.setattr(LE, "embed_query", _vec)
        monkeypatch.setattr(LE, "DIM", 8)
        return tree

    def test_kind_filter(self, tree):
        _rebuild(tree)
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            out = rms.search_memories(conn, "witr duckdb", kind="prime")
            assert {r["kind"] for r in out["results"]} == {"prime"}
        finally:
            conn.close()

    def test_hybrid_leg_engages_with_fake_embedder(self, fake_local):
        rms.rebuild(fake_local / "memory_search.db")  # resolve_embedder -> LE fake
        conn = rms.connect_memory_db(fake_local / "memory_search.db")
        try:
            out = rms.search_memories(conn, "operator gates discipline")
            assert out["mode"] == "hybrid"
            assert out["results"]
            assert all(r["similarity"] is not None for r in out["results"])
        finally:
            conn.close()

    def test_bm25_degrades_on_dims_mismatch(self, tree, monkeypatch):
        """Index built with the 8-dim fake; query side resolving 3-dim — the
        cosine leg must degrade to BM25 instead of zip-truncating garbage."""
        from helpers.core import local_embedder as LE

        rms.rebuild(tree / "memory_search.db", embed_fn=_fake_embed)
        monkeypatch.setattr(LE, "available", lambda: True)
        monkeypatch.setattr(LE, "embed_query", lambda _t: [0.1, 0.2, 0.3])
        monkeypatch.setattr(LE, "DIM", 3)
        conn = rms.connect_memory_db(tree / "memory_search.db")
        try:
            out = rms.search_memories(conn, "witr")
            assert out["mode"] == "bm25"
            assert out["results"]
            assert all(r["similarity"] is None for r in out["results"])
        finally:
            conn.close()


class TestQueryCli:
    def test_cli_json_output(self, tree, capsys):
        _rebuild(tree)
        rc = memory_query.main(["witr duckdb", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["results"]
        hit = payload["results"][0]
        assert {"name", "kind", "path", "score"} <= set(hit)

    def test_cli_kind_flag(self, tree, capsys):
        _rebuild(tree)
        rc = memory_query.main(["identity", "--kind", "opencode", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert {r["kind"] for r in payload["results"]} <= {"opencode"}

    def test_cli_missing_index_exit_1(self, tree, capsys):
        rc = memory_query.main(["anything"])
        assert rc == 1
        assert "not built" in capsys.readouterr().err

    def test_cli_stale_warning_answers_anyway(self, tree, monkeypatch, capsys):
        _rebuild(tree)
        (tree / "zcode-projects" / "scope-main" / "memory" / "user-workflow.md").write_text(
            _ZCODE_NOTE + "\nEdited after indexing.\n"
        )
        rc = memory_query.main(["stgit", "--json"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING" in err
