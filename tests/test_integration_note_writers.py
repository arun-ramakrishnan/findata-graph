#!/usr/bin/env python3
"""Integration tests for cross-writer note convergence
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §4 A5).

Four writers own interleaved, order-sensitive regions of the same notes:
build_sector_hierarchy (Child Sectors (auto) + sector up-links),
sync_sector_wikilinks (sector-note company rosters), sync_tags (DB side
of note YAML), derive_cited_in (edition entities + cited_in edges from
OKF sources[]). Each is unit-tested alone; what no test exercised is all
of them over ONE vault: rosters matching the DB, both --check gates
green after apply, re-runs converging byte-identically, hierarchy writes
staying REGION-scoped (never clobbering OKF frontmatter), and cited_in
edge counts matching the sources[] evidence.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core.db import connect as db_connect  # noqa: E402
from helpers.core.frontmatter import yaml_safe_load  # noqa: E402
from helpers.graph import derive_cited_in as dci  # noqa: E402
from helpers.graph.query import DB_PATH  # noqa: E402
from helpers.maintenance import build_sector_hierarchy as bsh  # noqa: E402
from helpers.maintenance import sync_sector_wikilinks as ssw  # noqa: E402

pytestmark = [pytest.mark.integration]

_COMPANY = "HDFC Bank"
_NOTE = """\
---
title: HDFC Bank
type: company
tags:
- entity_type/company
- sector/Banking
created: '2026-01-01'
last_modified: '2026-01-02'
sources:
- id: TC_Alpha
  resource: /findata/The_Chatter/TC_Alpha.md
  last_modified: '2026-08-01'
generated:
  by: process:okf_backfill
  at: '2026-08-19T00:00:00Z'
---
# HDFC Bank

India's largest private sector bank.
"""


class _WritersProject:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / "research.db"
        self.vault = root / "findata"
        self._build_db()
        self._build_vault()
        self._run_bsh(apply=True)
        self._run_ssw(apply=True)

    def _build_db(self) -> None:
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(self.db))
        src.backup(dst)
        src.close()
        dst.execute(
            "CREATE TEMP TABLE keep AS SELECT name FROM ("
            "SELECT name, 0 o FROM entities WHERE entity_type != 'company' "
            "UNION ALL SELECT name, 1 o FROM entities WHERE name = ? "
            "GROUP BY name HAVING MIN(o))",
            (_COMPANY,),
        )
        dst.execute(
            "DELETE FROM graph_edges WHERE source NOT IN "
            "(SELECT name FROM keep) OR target NOT IN (SELECT name FROM keep)"
        )
        # cited_in edges are RE-DERIVED from sources[] by derive_cited_in;
        # production edges survive the entity prune (editions are
        # non-company) and would pollute the count assertions.
        dst.execute("DELETE FROM graph_edges WHERE edge_type = 'cited_in'")
        dst.execute("DELETE FROM entities WHERE name NOT IN (SELECT name FROM keep)")
        dst.commit()
        dst.close()

    def _build_vault(self) -> None:
        (self.vault / "The_Chatter").mkdir(parents=True)
        (self.vault / "The_Chatter" / "TC_Alpha.md").write_text(
            "# The Chatter: Alpha Edition\n\nNewsletter body.\n", encoding="utf-8"
        )
        (self.vault / "Sectors").mkdir(parents=True)
        (self.vault / "Sectors" / "Banking.md").write_text(
            "---\ntitle: Banking\ntype: sector\ntags:\n- entity_type/sector\n"
            "created: '2026-01-01'\nlast_modified: '2026-01-02'\n---\n"
            "# Banking\n\nBanking sector overview.\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(str(self.db))
        fp = conn.execute("SELECT file_path FROM entities WHERE name = ?", (_COMPANY,)).fetchone()[
            0
        ]
        conn.close()
        p = self.root / fp
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_NOTE, encoding="utf-8")

    # -- writer drivers (the same entrypoints the A2 dispatcher uses) --- #
    def _patch(self, saved: dict) -> None:
        # SUPER_SECTORS_DIR patched DIRECTLY: it binds at import
        # (VAULT_ROOT / "Super_Sectors"), so patching VAULT_ROOT alone
        # leaves the writer pointing at the live vault.
        saved.update(
            bsh_DB=bsh.DB_PATH,
            bsh_VAULT=bsh.VAULT_ROOT,
            bsh_SUPERS=bsh.SUPER_SECTORS_DIR,
            ssw_DB=ssw.DB_PATH,
            ssw_DIR=ssw.SECTORS_DIR,
            argv=list(sys.argv),
        )
        bsh.DB_PATH = self.db
        bsh.VAULT_ROOT = self.vault
        bsh.SUPER_SECTORS_DIR = self.vault / "Super_Sectors"
        ssw.DB_PATH = self.db
        ssw.SECTORS_DIR = self.vault / "Sectors"

    def _unpatch(self, saved: dict) -> None:
        bsh.DB_PATH = saved["bsh_DB"]
        bsh.VAULT_ROOT = saved["bsh_VAULT"]
        bsh.SUPER_SECTORS_DIR = saved["bsh_SUPERS"]
        ssw.DB_PATH = saved["ssw_DB"]
        ssw.SECTORS_DIR = saved["ssw_DIR"]
        sys.argv[:] = saved["argv"]

    def _run_bsh(self, apply: bool) -> int:
        saved: dict = {}
        self._patch(saved)
        try:
            sys.argv = ["build_sector_hierarchy.py", "--apply" if apply else "--check"]
            err = StringIO()
            with redirect_stderr(err):
                rc = bsh.main()
            self.last_bsh_err = err.getvalue()
            return rc
        finally:
            self._unpatch(saved)

    def _run_ssw(self, apply: bool) -> int:
        saved: dict = {}
        self._patch(saved)
        try:
            sys.argv = ["sync_sector_wikilinks.py"] + ([] if apply else ["--check"])
            return ssw.main() or 0
        finally:
            self._unpatch(saved)

    def _run_dci(self, monkeypatch) -> int:
        monkeypatch.setattr(dci, "_REPO_ROOT", self.root)
        monkeypatch.setattr(dci, "connect", lambda *a, **k: db_connect(str(self.db)))
        err = StringIO()
        with redirect_stderr(err):
            return dci._cli(["--apply", "--vault", str(self.vault)])

    # -- helpers --------------------------------------------------------- #
    def note_bytes(self) -> dict[str, bytes]:
        return {
            p.relative_to(self.vault).as_posix(): p.read_bytes()
            for p in sorted(self.vault.rglob("*.md"))
        }

    def sql(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn = sqlite3.connect(str(self.db))
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows


@pytest.fixture(scope="module")
def writers(tmp_path_factory) -> _WritersProject:
    return _WritersProject(tmp_path_factory.mktemp("note_writers"))


class TestConvergence:
    def test_child_sectors_regions_written(self, writers):
        supers = sorted((writers.vault / "Super_Sectors").glob("*.md"))
        assert len(supers) == len(bsh.SUPER_SECTORS)
        for p in supers:
            assert bsh._CHILD_BEGIN in p.read_text(encoding="utf-8")

    def test_sector_roster_matches_db(self, writers):
        """The Banking roster lists exactly the DB's Banking companies, in
        the [[stem|title]] wikilink form (the Obsidian resolution rule)."""
        fp = writers.sql("SELECT file_path FROM entities WHERE name = ?", (_COMPANY,))[0][0]
        stem = Path(fp).stem
        text = (writers.vault / "Sectors" / "Banking.md").read_text(encoding="utf-8")
        assert re.search(rf"\[\[{stem}\|{re.escape(_COMPANY)}\]\]", text)

    def test_check_gates_green_after_apply(self, writers):
        assert writers._run_ssw(apply=False) == 0
        assert writers._run_bsh(apply=False) == 0

    def test_rerun_converges_byte_identical(self, writers):
        before = writers.note_bytes()
        assert writers._run_bsh(apply=True) == 0
        assert writers._run_ssw(apply=True) == 0
        assert writers.note_bytes() == before

    def test_hierarchy_write_is_region_scoped(self, writers):
        """OKF frontmatter on a super-sector note (generated/sources keys
        the hierarchy writer doesn't own) survives a re-apply; only the
        Child Sectors region is its business — the known region-scoping
        trap from the OKF adoption."""
        note = writers.vault / "Super_Sectors" / "Financials.md"
        text = note.read_text(encoding="utf-8")
        opener, fm_text, body = _split_fm(text)
        fm = yaml_safe_load(fm_text)
        fm["generated"] = {"by": "process:okf_backfill", "at": "2026-08-19T00:00:00Z"}
        fm["sources"] = [{"id": "TC_Alpha", "resource": "/findata/The_Chatter/TC_Alpha.md"}]
        note.write_text(_render_fm(fm) + body, encoding="utf-8")

        assert writers._run_bsh(apply=True) == 0
        _, fm_text2, _ = _split_fm(note.read_text(encoding="utf-8"))
        fm2 = yaml_safe_load(fm_text2)
        assert fm2["generated"]["by"] == "process:okf_backfill"
        assert fm2["sources"][0]["id"] == "TC_Alpha"
        assert bsh._CHILD_BEGIN in note.read_text(encoding="utf-8")
        # Restore the pre-test bytes: the injected sources[] would otherwise
        # leak into TestCitedIn's derive_cited_in run below.
        note.write_text(text, encoding="utf-8")


class TestCitedIn:
    def test_cited_in_edges_match_sources(self, writers, monkeypatch):
        """derive_cited_in --apply: one cited_in edge per valid sources[]
        entry, the edition entity created, and a second apply idempotent."""
        assert writers._run_dci(monkeypatch) == 0
        edges = writers.sql("SELECT source, target FROM graph_edges WHERE edge_type = 'cited_in'")
        assert edges == [(_COMPANY, "TC_Alpha")]
        assert (
            writers.sql(
                "SELECT COUNT(*) FROM entities WHERE name='TC_Alpha' AND entity_type='edition'"
            )[0][0]
            == 1
        )
        assert writers._run_dci(monkeypatch) == 0
        assert writers.sql(
            "SELECT source, target FROM graph_edges WHERE edge_type = 'cited_in'"
        ) == [(_COMPANY, "TC_Alpha")]


def _split_fm(text: str) -> tuple[str, str, str]:
    from helpers.core.frontmatter import split_frontmatter

    return split_frontmatter(text)


def _render_fm(fm: dict) -> str:
    from helpers.core.frontmatter import render_frontmatter, stringify_dates

    return render_frontmatter(stringify_dates(fm))
