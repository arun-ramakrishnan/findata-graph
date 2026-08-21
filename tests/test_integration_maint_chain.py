#!/usr/bin/env python3
"""Integration tests for the maint / maint-full orchestration chain
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §4 A2).

maint.py runs each step as a subprocess with cwd=PROJECT_ROOT, and every
helper anchors on its own module-level PROJECT_ROOT/DB_PATH — a true
subprocess E2E would need env-var overrides in production scripts (rejected:
it widens the live-path surface for testability only). Instead these tests
monkeypatch ``subprocess.run`` with an in-process DISPATCHER that maps each
registered step command to the same library entrypoint the subprocess would
run, over tmp roots. Consequences pinned here:

  * the chain actually EXECUTES (test_maint.py pins only the step lists);
  * a maint step added without a dispatcher shim FAILS the chain loudly —
    the wiring cannot drift silently;
  * a failing step aborts the chain (later steps never run);
  * --dry-run executes nothing;
  * the full chain is idempotent: a second run is green, notes are
    byte-identical, and derived tables don't churn.

Non-helper subprocesses (e.g. the edition index's ``git`` date lookups)
pass through to the real subprocess.run — the dispatcher never recurses.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.core import sync_tags as st  # noqa: E402
from helpers.core.db import connect as db_connect  # noqa: E402
from helpers.graph import algorithms as alg  # noqa: E402
from helpers.graph import derive_events as de  # noqa: E402
from helpers.graph import derive_insights as di  # noqa: E402
from helpers.graph import embeddings as emb  # noqa: E402
from helpers.graph import query  # noqa: E402
from helpers.graph.query import DB_PATH  # noqa: E402
from helpers.maintenance import build_sector_hierarchy as bsh  # noqa: E402
from helpers.maintenance import db_maint  # noqa: E402
from helpers.maintenance import maint  # noqa: E402
from helpers.maintenance import rebuild_note_search as rns  # noqa: E402
from helpers.maintenance import snapshot_db  # noqa: E402
from helpers.maintenance import sync_sector_wikilinks as ssw  # noqa: E402

pytestmark = [pytest.mark.integration]

_KEEP_COMPANIES = ("HDFC Bank", "ICICI Bank")

_NEWSLETTER = """\
# The Chatter: Alpha Edition

## Banking

## HDFC Bank | Large Cap | Banking

HDFC Bank is India's largest private-sector bank.

## [Concall]

Management reiterated FY27 loan growth guidance at 12-14% for the full year.

"We delivered 8% loan growth this quarter and gained over 300 basis points of market share."

— Sashidhar Jagdishan, MD & CEO
"""


def _company_note(title: str, sector: str) -> str:
    return (
        "---\n"
        "title: {t}\n"
        "type: company\n"
        "tags:\n"
        "- entity_type/company\n"
        "- sector/{s}\n"
        "created: '2026-01-01'\n"
        "last_modified: '2026-01-02'\n"
        "---\n"
        "# {t}\n"
        "\n"
        "A company overview line.\n"
        "\n"
        "- **FY27 guidance reiterated (10-12%):** management expects\n"
        "  revenue growth at the lower end of the range.\n"
    ).format(t=title, s=sector)


class _MaintProject:
    """A tmp repo stand-in: pruned production-schema DB + minimal vault,
    with the two explicit WRITES already applied (sector hierarchy + sector
    rosters) so the chain's --check gates are green — exactly the state
    maint-full assumes in production."""

    def __init__(self, root: Path):
        self.root = root
        self.db = root / "research.db"
        self.duckdb = root / "graph.duckdb"
        self.vault = root / "findata"
        self._build_db()
        self._build_vault()
        self._apply_explicit_writes()

    # -- fixture construction ------------------------------------------ #
    def _build_db(self) -> None:
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(self.db))
        src.backup(dst)
        src.close()
        dst.execute("CREATE TEMP TABLE keep AS SELECT name FROM ("
                    "SELECT name, 0 o FROM entities WHERE entity_type != 'company' "
                    "UNION ALL SELECT name, 1 o FROM entities WHERE name IN (?, ?) "
                    "GROUP BY name HAVING MIN(o))",
                    _KEEP_COMPANIES)
        dst.execute("DELETE FROM graph_edges WHERE source NOT IN "
                    "(SELECT name FROM keep) OR target NOT IN (SELECT name FROM keep)")
        dst.execute("DELETE FROM entity_tags WHERE entity_name NOT IN (SELECT name FROM keep)")
        dst.execute("DELETE FROM quotes WHERE entity NOT IN (SELECT name FROM keep)")
        dst.execute("DELETE FROM company_metrics WHERE entity NOT IN (SELECT name FROM keep)")
        dst.execute("DELETE FROM company_embeddings WHERE company_name NOT IN (SELECT name FROM keep)")
        dst.execute("DELETE FROM graph_analytics")  # recomputed by the chain
        dst.execute("DELETE FROM events")           # recomputed by the chain
        # DROP the FTS index (shadows included — DELETEs leave tombstones
        # that keep the file ~7MB, which dominates the snapshot gzip cost);
        # the chain's rebuild-note-search step recreates it via CREATE
        # VIRTUAL TABLE IF NOT EXISTS.
        dst.execute("DROP TABLE IF EXISTS note_search")
        dst.execute("DROP TABLE IF EXISTS note_search_meta")
        dst.execute("DELETE FROM entities WHERE name NOT IN (SELECT name FROM keep)")
        dst.commit()
        dst.execute("VACUUM")
        dst.close()

    def _build_vault(self) -> None:
        (self.vault / "The_Chatter").mkdir(parents=True)
        (self.vault / "The_Chatter" / "TC_Alpha.md").write_text(
            _NEWSLETTER, encoding="utf-8")
        (self.vault / "Sectors").mkdir(parents=True)
        (self.vault / "Sectors" / "Banking.md").write_text(
            "---\ntitle: Banking\ntype: sector\ntags:\n- entity_type/sector\n"
            "created: '2026-01-01'\nlast_modified: '2026-01-02'\n---\n"
            "# Banking\n\nBanking sector overview.\n", encoding="utf-8")
        conn = sqlite3.connect(str(self.db))
        for name in _KEEP_COMPANIES:
            fp = conn.execute(
                "SELECT file_path FROM entities WHERE name = ?", (name,)
            ).fetchone()[0]
            if not fp:
                raise AssertionError(f"fixture company {name!r} has no file_path")
            p = self.root / fp
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_company_note(name, "Banking"), encoding="utf-8")
        conn.close()

    def _patch_roots(self) -> dict:
        """Save + apply the module-root patches shared by the explicit
        writes and the shims. Returns the saved state for _restore_roots.
        SUPER_SECTORS_DIR is patched DIRECTLY: it binds at import
        (VAULT_ROOT / "Super_Sectors"), so patching VAULT_ROOT alone
        leaves the writer pointing at the live vault."""
        saved = {
            "argv": list(sys.argv),
            "bsh.DB_PATH": bsh.DB_PATH, "bsh.VAULT_ROOT": bsh.VAULT_ROOT,
            "bsh.SUPER_SECTORS_DIR": bsh.SUPER_SECTORS_DIR,
            "ssw.DB_PATH": ssw.DB_PATH, "ssw.SECTORS_DIR": ssw.SECTORS_DIR,
        }
        bsh.DB_PATH = self.db
        bsh.VAULT_ROOT = self.vault
        bsh.SUPER_SECTORS_DIR = self.vault / "Super_Sectors"
        ssw.DB_PATH = self.db
        ssw.SECTORS_DIR = self.vault / "Sectors"
        return saved

    def _restore_roots(self, saved: dict) -> None:
        bsh.DB_PATH = saved["bsh.DB_PATH"]
        bsh.VAULT_ROOT = saved["bsh.VAULT_ROOT"]
        bsh.SUPER_SECTORS_DIR = saved["bsh.SUPER_SECTORS_DIR"]
        ssw.DB_PATH = saved["ssw.DB_PATH"]
        ssw.SECTORS_DIR = saved["ssw.SECTORS_DIR"]
        sys.argv[:] = saved["argv"]

    def _apply_explicit_writes(self) -> None:
        """The two writes maint-full deliberately does NOT do: hierarchy
        --apply (entities/edges/super-sector notes) + sector-links apply
        (rosters). Running them here makes the chain's gates meaningful."""
        saved = self._patch_roots()
        try:
            sys.argv = ["build_sector_hierarchy.py", "--apply"]
            assert bsh.main() == 0
            sys.argv = ["sync_sector_wikilinks.py"]
            rc = ssw.main()
            assert (rc or 0) == 0
        finally:
            self._restore_roots(saved)

    # -- assertions helper ---------------------------------------------- #
    def notes_snapshot(self) -> dict[str, bytes]:
        return {
            p.relative_to(self.root).as_posix(): p.read_bytes()
            for p in sorted(self.vault.rglob("*.md"))
        }

    def table_dump(self, table: str) -> list[tuple]:
        conn = sqlite3.connect(str(self.db))
        rows = conn.execute(
            f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()  # noqa: S608  # schema-constant identifier from the caller allowlist
        conn.close()
        return rows


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> _MaintProject:
    return _MaintProject(tmp_path_factory.mktemp("maint_chain"))


# --------------------------------------------------------------------------- #
# The dispatcher: in-process shims for every registered maint step            #
# --------------------------------------------------------------------------- #
def _rc(v) -> int:
    return 0 if v is None else int(v)


def _shim_db_maint(p, mp, args):
    mp.setattr(sys, "argv", ["db_maint.py",
                             "--db", str(p.db),
                             "--backup", str(p.root / "db-backup" / "b.db"),
                             "--duckdb", str(p.duckdb),
                             "--duckdb-backup", str(p.root / "db-backup" / "b.duckdb")])
    return _rc(db_maint.main())


def _shim_snapshot(p, mp, args):
    mp.setattr(sys, "argv", ["snapshot_db.py",
                             "--db", str(p.db),
                             "--out", str(p.root / "db-backup" / "s.db.gz"),
                             "--duckdb", str(p.duckdb),
                             "--duckdb-out", str(p.root / "db-backup" / "s.duckdb.gz"),
                             "--parquet-dir", str(p.root / "snapshots" / "parquet")])
    return _rc(snapshot_db.main())


def _shim_graph_rebuild(p, mp, args):
    query.rebuild(p.db)  # `query.py rebuild` — the library wrapper
    return 0


def _shim_sync_tags(p, mp, args):
    mp.setattr(st, "_REPO_ROOT", p.root)
    mp.setattr(sys, "argv", ["sync_tags.py", "--db", str(p.db)])
    return _rc(st.main())


def _shim_sector_links(p, mp, args):
    mp.setattr(ssw, "DB_PATH", p.db)
    mp.setattr(ssw, "SECTORS_DIR", p.vault / "Sectors")
    mp.setattr(sys, "argv", ["sync_sector_wikilinks.py", *args])
    return _rc(ssw.main())


def _shim_hierarchy(p, mp, args):
    mp.setattr(bsh, "DB_PATH", p.db)
    mp.setattr(bsh, "VAULT_ROOT", p.vault)
    mp.setattr(bsh, "SUPER_SECTORS_DIR", p.vault / "Super_Sectors")
    mp.setattr(sys, "argv", ["build_sector_hierarchy.py", *args])
    return _rc(bsh.main())


def _shim_note_search(p, mp, args):
    mp.setattr(rns, "FINDATA", p.vault)
    return _rc(rns.main(["--db", str(p.db)]))


def _shim_embeddings(p, mp, args):
    mp.setattr(emb, "DEFAULT_DB_PATH", p.db)
    return _rc(emb.main(list(args)))


def _shim_algorithms(p, mp, args):
    mp.setattr(alg, "duckdb_connect", lambda *a, **k: query.connect(p.db))
    mp.setattr(alg, "connect", lambda *a, **k: db_connect(str(p.db)))
    return _rc(alg._cli(list(args)))


def _shim_derive_insights(p, mp, args):
    mp.setattr(di, "PROJECT_ROOT", p.root)
    mp.setattr(di, "connect", lambda *a, **k: db_connect(str(p.db)))
    # The step says `findata` (repo-relative); resolve it against the tmp
    # root — the real subprocess would run with cwd=PROJECT_ROOT.
    argv = [str(p.vault) if a == "findata" else a for a in args]
    return _rc(di._cli(argv))


def _shim_derive_events(p, mp, args):
    mp.setattr(de, "COMPANIES_DIR", p.vault / "Companies")
    mp.setattr(de, "_REPO_ROOT", p.root)
    mp.setattr(de, "connect", lambda *a, **k: db_connect(str(p.db)))
    return _rc(de._cli(list(args)))


_SHIMS = {
    "helpers/maintenance/db_maint.py": _shim_db_maint,
    "helpers/maintenance/snapshot_db.py": _shim_snapshot,
    "helpers/graph/query.py": _shim_graph_rebuild,
    "helpers/core/sync_tags.py": _shim_sync_tags,
    "helpers/maintenance/sync_sector_wikilinks.py": _shim_sector_links,
    "helpers/maintenance/build_sector_hierarchy.py": _shim_hierarchy,
    "helpers/maintenance/rebuild_note_search.py": _shim_note_search,
    "helpers/graph/embeddings.py": _shim_embeddings,
    "helpers/graph/algorithms.py": _shim_algorithms,
    "helpers/graph/derive_insights.py": _shim_derive_insights,
    "helpers/graph/derive_events.py": _shim_derive_events,
}


def _make_dispatcher(p, mp, record: list, overrides: dict | None = None):
    """Patch subprocess.run for the maint.main() call. Every python3
    helpers/... command MUST have a shim (an unmapped one fails loudly);
    anything else (git, ...) passes through to the real subprocess.run."""
    real_run = subprocess.run
    shims = dict(_SHIMS)
    if overrides:
        shims.update(overrides)

    def dispatch(cmd, **kwargs):
        if (isinstance(cmd, list) and len(cmd) >= 2
                and cmd[0] == "python3" and cmd[1].startswith("helpers/")):
            shim = shims.get(cmd[1])
            if shim is None:
                record.append((cmd[1], "UNSHIMMED", 1))
                return subprocess.CompletedProcess(cmd, 1)
            rc = shim(p, mp, cmd[2:])
            record.append((cmd[1], tuple(cmd[2:]), rc))
            return subprocess.CompletedProcess(cmd, rc)
        return real_run(cmd, **kwargs)

    mp.setattr(subprocess, "run", dispatch)
    return record


def _scripts(record) -> list[str]:
    return [entry[0] for entry in record]


class TestMaintChain:
    def test_tier1_executes_all_steps_green(self, project, monkeypatch):
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main([]) == 0
        assert _scripts(record) == [cmd[1] for _, cmd in maint.TIER1_STEPS]

    def test_full_chain_order_and_idempotence(self, project, monkeypatch):
        """--full runs TIER1 then TIER2 in order (derive-insights strictly
        before derive-events; snapshot first and last), the embeddings
        best-effort step stays green (local embedder pinned off by conftest
        — the maint never-blocks contract), and a SECOND full run is a
        byte-stable no-op: same steps, note bytes identical, derived
        tables unchanged."""
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main(["--full"]) == 0
        executed = _scripts(record)
        expected = [cmd[1] for _, cmd in maint.TIER1_STEPS + maint.TIER2_STEPS]
        assert executed == expected
        assert executed.index("helpers/graph/derive_insights.py") < \
            executed.index("helpers/graph/derive_events.py")
        assert executed.count("helpers/maintenance/snapshot_db.py") == 2
        # embeddings --maint: rc 0 despite unavailable local embedder
        emb_entry = next(e for e in record
                         if e[0] == "helpers/graph/embeddings.py")
        assert emb_entry[2] == 0
        # derive-events really worked (the seeded guidance bullet)
        assert any(r[0] == "guidance"
                   for r in _table(project, "SELECT event_type FROM events"))

        notes1 = project.notes_snapshot()
        events1 = _table(project, "SELECT entity, event_type, source_quote "
                                  "FROM events ORDER BY 1, 2, 3")
        quotes1 = _table(project, "SELECT entity, quote_text, as_of_edition "
                                  "FROM quotes ORDER BY 1, 2, 3")

        record2 = _make_dispatcher(project, monkeypatch, [])
        assert maint.main(["--full"]) == 0
        assert _scripts(record2) == expected
        assert project.notes_snapshot() == notes1
        assert _table(project, "SELECT entity, event_type, source_quote "
                               "FROM events ORDER BY 1, 2, 3") == events1
        assert _table(project, "SELECT entity, quote_text, as_of_edition "
                               "FROM quotes ORDER BY 1, 2, 3") == quotes1

    def test_unshimmed_step_fails_loudly(self, project, monkeypatch):
        """A maint step whose script has no dispatcher shim aborts the
        chain — new steps cannot run half-executed or silently skipped."""
        fake = ("future step", ["python3", "helpers/maintenance/future.py"])
        monkeypatch.setattr(maint, "TIER1_STEPS",
                            [*maint.TIER1_STEPS, fake])
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main([]) == 1
        assert record[-1][1] == "UNSHIMMED"
        assert record[-1][2] == 1

    def test_step_failure_aborts_chain(self, project, monkeypatch):
        """A failing step stops the run: later steps never execute (the
        first-failure-aborts contract in main())."""
        def _boom(p, mp, args):
            return 1
        record = _make_dispatcher(
            project, monkeypatch, [],
            overrides={"helpers/core/sync_tags.py": _boom})
        assert maint.main(["--full"]) == 1
        executed = _scripts(record)
        assert executed[-1] == "helpers/core/sync_tags.py"
        assert "helpers/maintenance/rebuild_note_search.py" not in executed

    def test_dry_run_executes_nothing(self, project, monkeypatch):
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main(["--dry-run", "--full"]) == 0
        assert record == []


def _table(p, sql: str) -> list[tuple]:
    conn = sqlite3.connect(str(p.db))
    rows = conn.execute(sql).fetchall()
    conn.close()
    return rows
