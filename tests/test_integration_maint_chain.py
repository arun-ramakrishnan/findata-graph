#!/usr/bin/env python3
"""Integration tests for the maint / maint-full orchestration chain
(doc/improvements/archive/testing/integration_fuzz_enhancement.md §4 A2).

maint.py runs each step as a subprocess with cwd=PROJECT_ROOT, and every
helper anchors on its own module-level PROJECT_ROOT/DB_PATH — a true
subprocess E2E would need env-var overrides in production scripts (rejected:
it widens the live-path surface for testability only). Instead these tests
monkeypatch ``subprocess.Popen`` (maint streams each step's output through
a Popen pipe) with an in-process DISPATCHER that maps each registered step
command to the same library entrypoint the subprocess would run, over tmp
roots. Consequences pinned here:

  * the chain actually EXECUTES (test_maint.py pins only the step lists);
  * a maint step added without a dispatcher shim FAILS the chain loudly —
    the wiring cannot drift silently;
  * a failing step aborts the chain (later steps never run);
  * --dry-run executes nothing;
  * the full chain is idempotent: a second run is green, notes are
    byte-identical, and derived tables don't churn.

Non-helper subprocesses (e.g. the edition index's ``git`` date lookups)
pass through to the real subprocess.Popen — the dispatcher never recurses.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from helpers.core import sync_tags as st  # noqa: E402
from helpers.misc import seed_nic2008 as sn2  # noqa: E402
from helpers.core.db import connect as db_connect  # noqa: E402
from helpers.graph import algorithms as alg  # noqa: E402
from helpers.graph import derive_events as de  # noqa: E402
from helpers.graph import derive_insights as di  # noqa: E402
from helpers.graph import embeddings as emb  # noqa: E402
from helpers.graph import query  # noqa: E402
from helpers.graph import csr  # noqa: E402
from helpers.graph import derive_cited_in as dci  # noqa: E402
from helpers.graph import derive_hyperedges as dh_mod  # noqa: E402
from helpers.graph import hyper_arrow as har  # noqa: E402
from helpers.graph import hyper_centralities as hcen  # noqa: E402
from helpers.graph import hyper_communities as hcom  # noqa: E402
from helpers.maintenance import build_sector_hierarchy as bsh  # noqa: E402
from helpers.maintenance import db_maint  # noqa: E402
from helpers.maintenance import fold_identifiers as fi  # noqa: E402
from helpers.maintenance import maint  # noqa: E402
from helpers.maintenance import rebuild_doc_search as rds  # noqa: E402
from helpers.maintenance import rebuild_note_search as rns  # noqa: E402
from helpers.maintenance import snapshot_db  # noqa: E402
from helpers.maintenance import sync_coverage_tags as sct  # noqa: E402
from helpers.maintenance import sync_sector_wikilinks as ssw  # noqa: E402
from helpers.misc import backfill_identifiers as bi  # noqa: E402
from helpers.misc import backfill_okf_provenance as bf  # noqa: E402
from helpers.misc import backfill_row_provenance as brp  # noqa: E402
from helpers.misc import seed_concepts as sc  # noqa: E402

pytestmark = [pytest.mark.integration]

# The only companies the fixture writes vault notes for (their entities
# carry file_path so _build_vault can place them). The full company set
# (all COMPANY_SUB_SECTORS members) is seeded in _seed_test_data.
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
        f"title: {title}\n"
        "type: company\n"
        "tags:\n"
        "- entity_type/company\n"
        f"- sector/{sector}\n"
        "created: '2026-01-01'\n"
        "last_modified: '2026-01-02'\n"
        "---\n"
        f"# {title}\n"
        "\n"
        "A company overview line.\n"
        "\n"
        "- **FY27 guidance reiterated (10-12%):** management expects\n"
        "  revenue growth at the lower end of the range.\n"
    )


class _MaintProject:
    """A tmp repo stand-in: schema_template DB (canonical DDL, zero rows)
    + minimal FK-consistent seed + minimal vault, with the two explicit
    WRITES already applied (sector hierarchy + sector rosters) so the
    chain's --check gates are green — exactly the state maint-full assumes
    in production."""

    def __init__(self, root: Path, tmp_path_factory) -> None:
        self.root = root
        self.db = root / "research.db"
        self.duckdb = root / "graph.duckdb"
        self.vault = root / "findata"
        self._build_db(tmp_path_factory)
        self._build_vault()
        self._apply_explicit_writes()

    # -- fixture construction ------------------------------------------ #
    def _build_db(self, tmp_path_factory) -> None:
        """Clone the shared schema_template (canonical DDL, zero rows, ~2MB)
        and seed minimal FK-consistent data. Replaces the old live-DB backup
        (1.25s / 404MB) + prune + VACUUM (5.13s) with a reflink + seed
        (<200ms); schema matches production exactly, so every FK constraint
        holds under helpers.core.db.connect() (foreign_keys = ON)."""
        from tests._tmp_hygiene import reflink_or_copy, schema_template

        reflink_or_copy(schema_template(tmp_path_factory), self.db)
        con = sqlite3.connect(str(self.db))
        con.execute("PRAGMA journal_mode=WAL")
        con.close()
        self._seed_test_data(self.db)

    def _build_vault(self) -> None:
        (self.vault / "The_Chatter").mkdir(parents=True)
        (self.vault / "The_Chatter" / "TC_Alpha.md").write_text(_NEWSLETTER, encoding="utf-8")
        (self.vault / "Sectors").mkdir(parents=True)
        (self.vault / "Sectors" / "Banking.md").write_text(
            "---\ntitle: Banking\ntype: sector\ntags:\n- entity_type/sector\n"
            "created: '2026-01-01'\nlast_modified: '2026-01-02'\n---\n"
            "# Banking\n\nBanking sector overview.\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(str(self.db))
        for name in _KEEP_COMPANIES:
            fp = conn.execute("SELECT file_path FROM entities WHERE name = ?", (name,)).fetchone()[
                0
            ]
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
            "bsh.DB_PATH": bsh.DB_PATH,
            "bsh.VAULT_ROOT": bsh.VAULT_ROOT,
            "bsh.SUPER_SECTORS_DIR": bsh.SUPER_SECTORS_DIR,
            "ssw.DB_PATH": ssw.DB_PATH,
            "ssw.SECTORS_DIR": ssw.SECTORS_DIR,
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
            sys.argv = ["sync_sector_wikilinks.py", "--apply"]
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
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()  # noqa: S608  # schema-constant identifier from the caller allowlist
        conn.close()
        return rows

    def _seed_test_data(self, db_path: Path) -> None:
        """Seed minimal FK-consistent data into the schema_template clone.

        Sectors: EXACTLY the bsh.SUPER_SECTORS member set (42) — the
        fixture's build_sector_hierarchy --apply validates zero-orphan
        coverage (every live sector mapped, no unknown refs), so seeding
        any other sector set fails coverage. The step itself then creates
        the 10 super_sector + 142 sub_sector entities via INSERT OR IGNORE.
        Companies: all 182 dh_mod.COMPANY_SUB_SECTORS members (derive-
        hyperedges rides this map; hyper_incidences.entity_name is
        FK-bound to entities(name)) plus the two fixture companies
        _build_vault writes notes for.
        graph_analytics is left EMPTY on purpose — the chain's
        recompute-graph refreshes it (the pruned-live fixture deleted it
        for the same reason).
        """
        con = sqlite3.connect(str(db_path))
        con.execute("PRAGMA foreign_keys = ON")
        try:
            sectors = sorted({s for members in bsh.SUPER_SECTORS.values() for s in members})
            entities = [
                (name, "sector", f"Sectors/{name}.md", name, "2026-01-01") for name in sectors
            ]
            # Fixture companies need file_path for _build_vault to find them;
            # REPO-ROOT-RELATIVE (live contract: findata/...) — derive_events'
            # path_to_name join and the vault walk both key on this form.
            entities += [
                (
                    "HDFC Bank",
                    "company",
                    "findata/Companies/HDFC_Bank.md",
                    "HDFC Bank",
                    "2026-01-01",
                ),
                (
                    "ICICI Bank",
                    "company",
                    "findata/Companies/ICICI_Bank.md",
                    "ICICI Bank",
                    "2026-01-01",
                ),
            ]
            # All COMPANY_SUB_SECTORS members as companies (no vault notes)
            map_members = sorted({m for ms in dh_mod.COMPANY_SUB_SECTORS.values() for m in ms})
            entities += [
                (name, "company", None, name, "2026-01-01")
                for name in map_members
                if name not in _KEEP_COMPANIES
            ]

            for name, etype, fp, norm, updated in entities:
                con.execute(
                    "INSERT INTO entities (name, entity_type, file_path, normalized_name, last_updated)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (name, etype, fp, norm, updated),
                )

            edges = [
                (1, "HDFC Bank", "ICICI Bank", "competes_with", 1.0, "{}", None, None, "test", 0),
                (2, "HDFC Bank", "Banking", "part_of", 1.0, "{}", None, None, "test", 0),
                (3, "ICICI Bank", "Banking", "part_of", 1.0, "{}", None, None, "test", 0),
            ]
            for e in edges:
                con.execute(
                    "INSERT INTO graph_edges (id, source, target, edge_type, weight, properties, valid_from, valid_to, source_ref, symmetric) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    e,
                )

            con.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES ('generation', '0')")
            con.execute(
                "INSERT OR REPLACE INTO db_meta (key, value) VALUES ('schema_version', '16')"
            )

            # Scrub the shared index's apply stamps: schema_template keeps the
            # production db_meta rows naming a worktree HEAD that has nothing
            # to do with this tmp project — the note-search step's trample
            # guard would refuse a "diverged" HEAD (chain HEAD-coupled).
            # Scrubbed = the step bootstraps fresh and stamps THIS tree.
            con.execute(
                "DELETE FROM db_meta WHERE key IN ('apply_head', 'apply_worktree', 'apply_ts')"
            )
            # Same for the embed stamp: the tmp note surface is never
            # gemma-stamped (conftest pins the pseudo embedder); keeping the
            # copied stamp makes guard_gemma_stamp refuse to populate it.
            con.execute("DELETE FROM db_meta WHERE key = 'note_embed_model'")

            con.commit()
        finally:
            con.close()


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> _MaintProject:
    # Module-scoped like the pre-schema_template fixture: template clone +
    # seed + hierarchy/roster applies cost ~11s, and every test here is
    # order-tolerant by design (chain_order asserts the FIRST --full
    # snapshot; idempotence compares its own run1 vs run2 bytes).
    return _MaintProject(tmp_path_factory.mktemp("maint_chain"), tmp_path_factory)


# --------------------------------------------------------------------------- #
# The dispatcher: in-process shims for every registered maint step            #
# --------------------------------------------------------------------------- #
def _rc(v) -> int:
    return 0 if v is None else int(v)


def _shim_db_maint(p, mp, args):
    mp.setattr(
        sys,
        "argv",
        [
            "db_maint.py",
            "--db",
            str(p.db),
            "--backup",
            str(p.root / "db-backup" / "b.db"),
            "--duckdb",
            str(p.duckdb),
            "--duckdb-backup",
            str(p.root / "db-backup" / "b.duckdb"),
        ],
    )
    return _rc(db_maint.main())


def _shim_snapshot(p, mp, args):
    mp.setattr(
        sys,
        "argv",
        [
            "snapshot_db.py",
            "--db",
            str(p.db),
            "--out",
            str(p.root / "db-backup" / "s.db.zst"),
            "--duckdb",
            str(p.duckdb),
            "--duckdb-out",
            str(p.root / "db-backup" / "s.duckdb.zst"),
            "--parquet-dir",
            str(p.root / "snapshots" / "parquet"),
        ],
    )
    return _rc(snapshot_db.main())


def _shim_graph_rebuild(p, mp, args):
    query.rebuild(p.db)  # `query.py rebuild` — the library wrapper
    return 0


def _shim_sync_tags(p, mp, args):
    mp.setattr(st, "_REPO_ROOT", p.root)
    # Forward the step's flags (notably --apply since guard unification —
    # dropping them made the chain exercise a dry-run where production
    # writes), but keep dropping --corpus: the shared corpus cache
    # (memory/corpus.db) is repo-global and must not be polluted from the
    # tmp project; the plain walk covers the chain behavior under test.
    forwarded = [a for a in args if a != "--corpus"]
    mp.setattr(sys, "argv", ["sync_tags.py", "--db", str(p.db), *forwarded])
    return _rc(st.main())


def _shim_sync_coverage_tags(p, mp, args):
    return _rc(sct.main(["--db", str(p.db), "--root", str(p.vault), *args]))


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


def _shim_doc_search(p, mp, args):
    # Redirect ONLY the sidecar DB + its recovery backup to the tmp project
    # root — DOC_ROOT stays the repo doc/ (read-only index source). The step
    # is otherwise hermetic; the local embedder is pinned off by conftest
    # (pseudo path), so this stays green on machines without the model.
    mp.setattr(rds, "DOC_DB", p.root / "doc_search.db")
    return _rc(rds.main(["--db", str(p.root / "doc_search.db")]))


def _shim_embeddings(p, mp, args):
    mp.setattr(emb, "DEFAULT_DB_PATH", p.db)
    return _rc(emb.main(list(args)))


def _shim_algorithms(p, mp, args):
    mp.setattr(alg, "duckdb_connect", lambda *a, **k: query.connect(p.db))
    mp.setattr(alg, "connect", lambda *a, **k: db_connect(str(p.db)))
    # Pin engine routing off: this chain tests orchestration hermetically
    # on synth data, and the lanes only know the live SQLite store (their
    # hermetic coverage lives in tmp-store dispatch tests + live parity
    # tests). Without this, routed legs read live populations and write
    # them into the synth DB (FK failure).
    mp.setattr(alg, "_scipy_routed", lambda metric: False)
    mp.setattr(alg, "_l1b_routed", lambda: False)
    return _rc(alg._cli(list(args)))


def _shim_csr(p, mp, args):
    # maint.py registers csr-rebuild with repo-relative --db/--out constants
    # (memory/research.db, memory/csr) — repoint both at the tmp project so
    # the hermetic chain never reads or writes the live substrate.
    flags = [a for a in args if a in ("--verify", "--check")]
    return _rc(csr.main(["--db", str(p.db), "--out", str(p.root / "csr"), *flags]))


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
    # The chain step passes --corpus (S1b shared walk); Corpus.load would
    # return the REAL repo's cached notes, whose paths can never resolve
    # under the tmp _REPO_ROOT — every note would be skipped and the
    # prose arm would legitimately derive 0 events. Hermetic choice:
    # disable the fast path so extraction re-walks the tmp vault.
    mp.setattr(de, "_HAS_CORPUS", False)
    return _rc(de._cli(list(args)))


def _shim_derive_hyperedges(p, mp, args):
    mp.setattr(dh_mod, "_REPO_ROOT", p.root)
    mp.setattr(dh_mod, "connect", lambda *a, **k: db_connect(str(p.db)))
    return _rc(dh_mod._cli(list(args)))


def _shim_hyper_communities(p, mp, args):
    # HGX lanes (hyper_lane_wiring W3): data via hyper_arrow's module
    # default, writes via algorithms.write_analytics → alg.connect. W1
    # skips cleanly when the tmp store is empty/degenerate — never blocks.
    mp.setattr(har, "DEFAULT_DB_PATH", p.db)
    mp.setattr(alg, "connect", lambda *a, **k: db_connect(str(p.db)))
    return _rc(hcom._cli(list(args)))


def _shim_hyper_centralities(p, mp, args):
    mp.setattr(har, "DEFAULT_DB_PATH", p.db)
    mp.setattr(alg, "connect", lambda *a, **k: db_connect(str(p.db)))
    return _rc(hcen._cli(list(args)))


def _shim_okf_backfill(p, mp, args):
    # Notes-only converger: redirect the vault-resolution root. The tmp
    # project has no git, so canonical edition entries carry no
    # last_modified (same tmp-tree behavior the splice tests document).
    mp.setattr(bf, "_REPO_ROOT", p.root)
    return _rc(bf._cli(list(args)))


def _shim_row_provenance(p, mp, args):
    # Row converger: run in-process against the tmp project DB. Absent
    # fact tables are skipped by design (never-blocking), so a minimal
    # fixture stays green.
    return _rc(brp.main(["--db", str(p.db), *args]))


def _shim_seed_concepts(p, mp, args):
    # Concept converger: same contract — absent source tables are
    # skipped, never-blocking.
    return _rc(sc.main(["--db", str(p.db), *args]))


def _shim_seed_nic2008(p, mp, args):
    # NIC-2008 seed converger (S1 maint step 8): same --db contract as
    # the concept converger; the seed JSON is vendored in the repo so
    # the tmp project needs no extra fixture.
    return _rc(sn2.main(["--db", str(p.db), *args]))


def _shim_identifiers(p, mp, args):
    # Identifier converger: self-ensure DDL + CIN facet projection —
    # absent entities table is reported, never blocking.
    return _rc(bi.main(["--db", str(p.db), *args]))


def _shim_fold_identifiers(p, mp, args):
    return _rc(fi.main(["--db", str(p.db), *args]))


def _shim_derive_cited_in(p, mp, args):
    mp.setattr(dci, "_REPO_ROOT", p.root)
    mp.setattr(dci, "connect", lambda *a, **k: db_connect(str(p.db)))
    return _rc(dci._cli(list(args)))


def _shim_static_checks_full(p, mp, args):
    # The --full validation backstop reads the REAL repo (not the sandbox
    # vault, which carries intentional defects) and is covered by its own
    # suites (test_static_checks, test_dirty_scope). The sandbox chain
    # asserts placement (pre-snapshot) and idempotence, not verdicts —
    # executing the real gate here would couple the chain to worktree
    # state, so the shim passes it through as green.
    assert args == ["--full"], args
    return 0


def _shim_frontmatter_report(p, mp, args):
    # Advisory twin of the backstop above: always-rc-0 by contract, so
    # the shim mirrors the rc without executing (same worktree-coupling
    # reason; --report output shape is covered in test_dirty_scope).
    assert args == ["--report"], args
    return 0


_SHIMS = {
    "helpers/maintenance/db_maint.py": _shim_db_maint,
    "helpers/maintenance/snapshot_db.py": _shim_snapshot,
    "helpers/graph/query.py": _shim_graph_rebuild,
    "helpers/graph/csr.py": _shim_csr,
    "helpers/core/sync_tags.py": _shim_sync_tags,
    "helpers/maintenance/sync_coverage_tags.py": _shim_sync_coverage_tags,
    "helpers/maintenance/sync_sector_wikilinks.py": _shim_sector_links,
    "helpers/maintenance/build_sector_hierarchy.py": _shim_hierarchy,
    "helpers/maintenance/rebuild_note_search.py": _shim_note_search,
    "helpers/maintenance/rebuild_doc_search.py": _shim_doc_search,
    "helpers/graph/embeddings.py": _shim_embeddings,
    "helpers/graph/algorithms.py": _shim_algorithms,
    "helpers/graph/derive_insights.py": _shim_derive_insights,
    "helpers/graph/derive_events.py": _shim_derive_events,
    "helpers/misc/backfill_okf_provenance.py": _shim_okf_backfill,
    "helpers/misc/backfill_row_provenance.py": _shim_row_provenance,
    "helpers/misc/seed_concepts.py": _shim_seed_concepts,
    "helpers/misc/seed_nic2008.py": _shim_seed_nic2008,
    "helpers/misc/backfill_identifiers.py": _shim_identifiers,
    "helpers/maintenance/fold_identifiers.py": _shim_fold_identifiers,
    "helpers/graph/derive_cited_in.py": _shim_derive_cited_in,
    "helpers/validators/static_checks.py": _shim_static_checks_full,
    "helpers/validators/frontmatter_schema.py": _shim_frontmatter_report,
    "helpers/graph/derive_hyperedges.py": _shim_derive_hyperedges,
    "helpers/graph/hyper_communities.py": _shim_hyper_communities,
    "helpers/graph/hyper_centralities.py": _shim_hyper_centralities,
}


class _ShimProc:
    """Popen stand-in for a shimmed step: stdout line iterator + wait()."""

    def __init__(self, rc: int):
        self._rc = rc
        self.stdout = iter([f"(dispatched to in-process shim: rc={rc})\n"])

    def wait(self) -> int:
        return self._rc


def _make_dispatcher(p, mp, record: list, overrides: dict | None = None):
    """Patch subprocess.Popen for the maint.main() call (maint streams
    step output through Popen). Every python3 helpers/... command MUST
    have a shim (an unmapped one fails loudly); anything else (git, ...)
    passes through to the real subprocess.Popen. The maint run report is
    repointed into the tmp project root — never the repo-root file."""
    real_popen = subprocess.Popen
    mp.setattr(maint, "REPORT_PATH", p.root / "maint_report.md")
    shims = dict(_SHIMS)
    if overrides:
        shims.update(overrides)

    def dispatch(cmd, **kwargs):
        if (
            isinstance(cmd, list)
            and len(cmd) >= 2
            and cmd[0] == sys.executable
            and cmd[1].startswith("helpers/")
        ):
            shim = shims.get(cmd[1])
            if shim is None:
                record.append((cmd[1], "UNSHIMMED", 1))
                return _ShimProc(1)
            rc = shim(p, mp, cmd[2:])
            record.append((cmd[1], tuple(cmd[2:]), rc))
            return _ShimProc(rc)
        return real_popen(cmd, **kwargs)

    mp.setattr(subprocess, "Popen", dispatch)
    return record


def _scripts(record) -> list[str]:
    return [entry[0] for entry in record]


def _full_expected_steps() -> list[str]:
    """Script order of one maint --full run: PRE_FULL + TIER1 (snapshot
    elided per TIER1_FULL_SKIP) + TIER2 — ONE snapshot at the tail
    (maint_full_single_snapshot.md D1); the PRE_FULL index refreshers
    (sync-tags, note-search) run before the backup."""
    return (
        [cmd[1] for _, cmd in maint.PRE_FULL_STEPS]
        + [cmd[1] for label, cmd in maint.TIER1_STEPS if label not in maint.TIER1_FULL_SKIP]
        + [cmd[1] for _, cmd in maint.TIER2_STEPS]
    )


class TestMaintChain:
    def test_tier1_executes_all_steps_green(self, project, monkeypatch):
        # The served-skip must not fire in the hermetic chain: the
        # dispatcher shims query.py, and the live quack server (if any) is
        # not this project's store.
        monkeypatch.setattr(maint, "_graph_store_served", lambda: False)
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main([]) == 0
        assert _scripts(record) == [cmd[1] for _, cmd in maint.TIER1_STEPS]

    def test_graph_rebuild_skipped_when_served(self, project, monkeypatch):
        """A quack-served graph.duckdb makes the file-lane steps (rebuild,
        stamp-centrality — both end in _rebuild_via_swap) refuse by design
        (split-brain guard); maint must skip them with a warning instead
        of aborting the chain (2026-10-22)."""
        monkeypatch.setattr(maint, "_graph_store_served", lambda: True)
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main([]) == 0
        executed = _scripts(record)
        assert all(entry[1] != ("rebuild",) for entry in record), (
            "rebuild ran despite the served skip"
        )
        assert all(entry[1] != ("stamp-centrality",) for entry in record), (
            "stamp-centrality ran despite the served skip"
        )
        # Everything except the file-lane steps still runs, in order.
        skipped = {label.split(" (")[0] for label in maint._FILE_LANE_STEPS}
        expected = [
            cmd[1] for label, cmd in maint.TIER1_STEPS if label.split(" (")[0] not in skipped
        ]
        assert executed == expected

    @pytest.mark.onager
    def test_chain_order(self, project, monkeypatch):
        """--full runs PRE_FULL + TIER1 then TIER2 in order: derive-insights
        strictly before derive-events, and the TIER1 snapshot is elided —
        ONE snapshot at the tail. The embeddings best-effort step stays
        green (local embedder pinned off by conftest — the maint
        never-blocks contract)."""
        # Hermetic contract (same as test_tier1_executes_all_steps_green):
        # the served-skip must not fire in the chain — the dispatcher shims
        # query.py, and the live quack server (if any) is not this
        # project's store. Without the patch the file-lane steps
        # (graph-rebuild/stamp-centrality) skip unrecorded when the REAL
        # repo's graph store happens to be served, and the order
        # assertion reads a phantom mismatch (2026-10-24 gate arc).
        monkeypatch.setattr(maint, "_graph_store_served", lambda: False)
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main(["--full"]) == 0
        executed = _scripts(record)
        # --full elides the TIER1 snapshot (TIER1_FULL_SKIP): ONE snapshot
        # at the tail (maint_full_single_snapshot.md D1). PRE_FULL index
        # refreshers (sync-tags, note-search) run before the backup.
        expected = (
            [cmd[1] for _, cmd in maint.PRE_FULL_STEPS]
            + [cmd[1] for label, cmd in maint.TIER1_STEPS if label not in maint.TIER1_FULL_SKIP]
            + [cmd[1] for _, cmd in maint.TIER2_STEPS]
        )
        assert executed == expected
        assert executed.index("helpers/graph/derive_insights.py") < executed.index(
            "helpers/graph/derive_events.py"
        )
        assert executed.count("helpers/maintenance/snapshot_db.py") == 1
        # embeddings --maint: rc 0 despite unavailable local embedder
        emb_entry = next(e for e in record if e[0] == "helpers/graph/embeddings.py")
        assert emb_entry[2] == 0
        # derive-events really worked (the seeded guidance bullet)
        assert any(r[0] == "guidance" for r in _table(project, "SELECT event_type FROM events"))

    @pytest.mark.onager
    def test_chain_idempotence(self, project, monkeypatch):
        """A SECOND --full run is a byte-stable no-op: same steps, note
        bytes identical, and derived tables (events/quotes plus the
        doc_search sidecar in canonical row order) unchanged."""
        monkeypatch.setattr(maint, "_graph_store_served", lambda: False)
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main(["--full"]) == 0
        expected = _full_expected_steps()
        assert _scripts(record) == expected

        notes1 = project.notes_snapshot()
        events1 = _table(
            project,
            "SELECT entity, event_type, source_quote FROM events ORDER BY 1, 2, 3",
        )
        quotes1 = _table(
            project,
            "SELECT entity, quote_text, as_of_edition FROM quotes ORDER BY 1, 2, 3",
        )
        # doc_search sidecar (step 6c, shimmed to p.root/doc_search.db over
        # the real repo doc/): full rebuilds rewrite in canonical row order,
        # so a no-op cycle must be row-stable INCLUDING order (the zero-churn
        # doctrine; embeddings are deterministic pseudo under conftest pin).
        docsearch1 = _sidecar_table(
            project,
            "SELECT file_path, anchor, section_title, "
            "title, content, embedding FROM doc_search "
            "ORDER BY rowid",
        )

        record2 = _make_dispatcher(project, monkeypatch, [])
        assert maint.main(["--full"]) == 0
        assert _scripts(record2) == expected
        assert project.notes_snapshot() == notes1
        assert (
            _table(
                project,
                "SELECT entity, event_type, source_quote FROM events ORDER BY 1, 2, 3",
            )
            == events1
        )
        assert (
            _table(
                project,
                "SELECT entity, quote_text, as_of_edition FROM quotes ORDER BY 1, 2, 3",
            )
            == quotes1
        )
        assert (
            _sidecar_table(
                project,
                "SELECT file_path, anchor, section_title, "
                "title, content, embedding FROM doc_search "
                "ORDER BY rowid",
            )
            == docsearch1
        )

    def test_unshimmed_step_fails_loudly(self, project, monkeypatch):
        """A maint step whose script has no dispatcher shim aborts the
        chain — new steps cannot run half-executed or silently skipped.

        The fake step is PREPENDED, not appended: the abort contract is
        "the chain never executes past a shim miss", which a first-step
        miss proves at zero chain cost (the mid-chain later-steps-never-run
        half is test_step_failure_aborts_chain's job). Appended, this test
        paid a full TIER1 run (~10s standalone, ~15s under xdist co-run)
        before reaching the assertion — the third-most-expensive test in
        the suite for a property the first step already demonstrates
        (gate_wall_time_reclaim S2)."""
        fake = ("future step", [sys.executable, "helpers/maintenance/future.py"])
        monkeypatch.setattr(maint, "TIER1_STEPS", [fake, *maint.TIER1_STEPS])
        record = _make_dispatcher(project, monkeypatch, [])
        assert maint.main([]) == 1
        assert record[-1][1] == "UNSHIMMED"
        assert record[-1][2] == 1
        assert len(record) == 1  # nothing ran before or after the miss

    def test_step_failure_aborts_chain(self, project, monkeypatch):
        """A failing step stops the run: later steps never execute (the
        first-failure-aborts contract in main())."""

        def _boom(p, mp, args):
            return 1

        record = _make_dispatcher(
            project, monkeypatch, [], overrides={"helpers/core/sync_tags.py": _boom}
        )
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


def _sidecar_table(p, sql: str) -> list[tuple]:
    """_table against the doc_search sidecar (p.root/doc_search.db — where
    _shim_doc_search redirects step 6c), never the project's main DB."""
    conn = sqlite3.connect(str(p.root / "doc_search.db"))
    rows = conn.execute(sql).fetchall()
    conn.close()
    return rows
