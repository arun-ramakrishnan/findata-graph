#!/usr/bin/env python3
"""Integration tests for the extract_relations CLI walk + sidecar
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §4 A6).

The unit suites cover the resolver, the YAML patterns, and apply_edges in
isolation; nothing drove the real `_cli` — directory walk, alias
resolution end-to-end, the _pending_relations sidecar contract
(append-on-dry-run, --no-write-sidecar keeps it clean), symmetric
canonical ordering, and re-apply idempotence.
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect as db_connect  # noqa: E402
from helpers.graph import extract_relations as xr  # noqa: E402
from helpers.graph.query import DB_PATH  # noqa: E402

pytestmark = [pytest.mark.integration]

_NEWSLETTER = """\
# The Chatter: Alpha Edition

## Energy

## Reliance Industries | Large Cap | Energy

Reliance is building an integrated energy and retail empire.

The company acquired Nykaa in a landmark consumer-internet deal.

It also announced a joint venture with FSN E-Commerce for beauty retail.

Reliance Industries acquired Globex, a small regional player.
"""

_REL = "Reliance Industries"
_FSN = "FSN E-Commerce"  # reached via the "nykaa" brand alias
_UNKNOWN = "Globex"  # unresolved -> sidecar


def _make_db(db: Path) -> None:
    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(db))
    src.backup(dst)
    src.close()
    for t in (
        "graph_edges",
        "entity_tags",
        "graph_analytics",
        "events",
        "quotes",
        "company_metrics",
        "company_embeddings",
        "note_search",
        "note_search_meta",
    ):
        dst.execute(f"DELETE FROM {t}")  # noqa: S608  # schema-constant identifiers
    dst.execute("DELETE FROM entities")
    dst.executemany(
        "INSERT INTO entities (name, entity_type) VALUES (?, 'company')", [(_REL,), (_FSN,)]
    )
    dst.commit()
    dst.close()


class _XrProject:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / "research.db"
        self.vault = root / "findata"
        (self.vault / "The_Chatter").mkdir(parents=True)
        (self.vault / "The_Chatter" / "TC_Alpha.md").write_text(_NEWSLETTER, encoding="utf-8")
        self.sidecar = self.vault / "_pending_relations.txt"
        _make_db(self.db)

    def run(self, monkeypatch, *args: str) -> tuple[int, str]:
        monkeypatch.setattr(xr, "_REPO_ROOT", self.root)
        # write_sidecar's default arg binds SIDECAR_PATH at import — patching
        # the constant does nothing, so intercept the function and pin the
        # tmp path (otherwise dry-runs append to the LIVE sidecar).
        real_ws = xr.write_sidecar
        monkeypatch.setattr(
            xr, "write_sidecar", lambda unresolved, path=None: real_ws(unresolved, self.sidecar)
        )
        monkeypatch.setattr(xr, "connect", lambda *a, **k: db_connect(str(self.db)))
        err = StringIO()
        with redirect_stderr(err):
            rc = xr._cli([str(self.vault / "The_Chatter"), *args])
        return rc, err.getvalue()

    def edges(self, edge_type: str) -> list[tuple]:
        conn = sqlite3.connect(str(self.db))
        rows = conn.execute(
            "SELECT source, target, symmetric FROM graph_edges WHERE edge_type = ?", (edge_type,)
        ).fetchall()
        conn.close()
        return rows


@pytest.fixture
def xr_project(tmp_path) -> _XrProject:
    return _XrProject(tmp_path)


class TestExtractRelationsCli:
    def test_dry_run_writes_sidecar_and_zero_edges(self, xr_project, monkeypatch):
        rc, err = xr_project.run(monkeypatch)  # dry-run, sidecar ON
        assert rc == 0
        assert xr_project.sidecar.exists()
        assert _UNKNOWN in xr_project.sidecar.read_text(encoding="utf-8")
        # Nothing applied in dry-run (both resolved AND unresolved).
        assert xr_project.edges("acquired") == []
        assert xr_project.edges("jv_with") == []

    def test_apply_writes_edges_with_alias_resolution(self, xr_project, monkeypatch):
        """ "acquired Nykaa" resolves the BRAND ALIAS to the FSN E-Commerce
        entity — the _ALIASES end-to-end contract."""
        rc, _ = xr_project.run(monkeypatch, "--apply")
        assert rc == 0
        assert xr_project.edges("acquired") == [(_REL, _FSN, 0)]

    def test_symmetric_edge_canonical_order(self, xr_project, monkeypatch):
        """jv_with is symmetric and stored ONCE in canonical (sorted)
        order regardless of which section voiced the mention."""
        rc, _ = xr_project.run(monkeypatch, "--apply")
        assert rc == 0
        assert xr_project.edges("jv_with") == [(_FSN, _REL, 1)]

    def test_no_write_sidecar_keeps_it_clean(self, xr_project, monkeypatch):
        xr_project.sidecar.write_text("", encoding="utf-8")
        rc, _ = xr_project.run(monkeypatch, "--apply", "--no-write-sidecar")
        assert rc == 0
        assert xr_project.sidecar.read_text(encoding="utf-8") == ""

    def test_reapply_does_not_duplicate(self, xr_project, monkeypatch):
        assert xr_project.run(monkeypatch, "--apply")[0] == 0
        acq1 = xr_project.edges("acquired")
        jv1 = xr_project.edges("jv_with")
        assert xr_project.run(monkeypatch, "--apply")[0] == 0
        assert xr_project.edges("acquired") == acq1
        assert xr_project.edges("jv_with") == jv1

    def test_no_self_edges_ever_written(self, xr_project, monkeypatch):
        assert xr_project.run(monkeypatch, "--apply")[0] == 0
        conn = sqlite3.connect(str(xr_project.db))
        n = conn.execute("SELECT COUNT(*) FROM graph_edges WHERE source = target").fetchone()[0]
        conn.close()
        assert n == 0
