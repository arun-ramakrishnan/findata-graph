"""
Tier 1 tests for the static_checks validator itself.

Each test seeds a minimal fake findata/ + DB layout under tmp_path and proves
the check under test flags the intended defect and passes when clean.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from validators import static_checks as sc  # noqa: E402


# --------------------------------------------------------------------------- #
# Tag canonicalization                                                         #
# --------------------------------------------------------------------------- #
def test_tag_canonicalization_flags_uppercase(tmp_path, monkeypatch):
    """A note with sector/Pharma (uppercase) is flagged as fatal."""
    # Repoint REPO_ROOT/findata to our tmp_path so the walk stays isolated.
    fake_findata = tmp_path / "findata"
    fake_findata.mkdir()
    (fake_findata / "X.md").write_text(
        "---\n"
        "title: X\ntype: company\npermalink: companies/x/y\n"
        "tags:\n- sector/Pharma\n"
        "---\n# X\nbody.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    fatal, advisory = sc.check_tag_canonicalization()
    assert len(fatal) == 1
    assert "sector/Pharma" in fatal[0]
    assert advisory == []


def test_tag_canonicalization_lowercase_canonical_passes(tmp_path, monkeypatch):
    """A note with sector/pharma (lowercase, canonical) is clean."""
    fake_findata = tmp_path / "findata"
    fake_findata.mkdir()
    (fake_findata / "X.md").write_text(
        "---\n"
        "title: X\ntype: company\npermalink: companies/x/y\n"
        "tags:\n- sector/pharma\n"
        "---\n# X\nbody.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    fatal, advisory = sc.check_tag_canonicalization()
    assert fatal == []
    assert advisory == []  # 'pharma' is in the canonical set


def test_tag_canonicalization_synonym_is_advisory_not_fatal(tmp_path, monkeypatch):
    """sector/telecom is lowercase but not in the canonical set -> advisory only."""
    fake_findata = tmp_path / "findata"
    fake_findata.mkdir()
    (fake_findata / "X.md").write_text(
        "---\n"
        "title: X\ntype: company\npermalink: companies/x/y\n"
        "tags:\n- sector/telecom\n"
        "---\n# X\nbody.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    fatal, advisory = sc.check_tag_canonicalization()
    assert fatal == []  # casing is correct
    assert len(advisory) == 1
    assert "sector/telecom" in advisory[0]


# --------------------------------------------------------------------------- #
# Orphan markdown files                                                        #
# --------------------------------------------------------------------------- #
def test_orphan_markdown_flags_file_not_in_db(tmp_path, monkeypatch):
    """A file on disk whose normalized_name isn't in entities is flagged."""
    # Build a fake DB with one entity.
    db = tmp_path / "memory" / "research.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE entities (name TEXT, normalized_name TEXT)")
    conn.execute("INSERT INTO entities VALUES ('Acme', 'Acme')")
    conn.commit()
    conn.close()

    # Two files: one matches 'Acme', one is an orphan.
    companies = tmp_path / "findata" / "Companies" / "Tech"
    companies.mkdir(parents=True)
    (companies / "Acme.md").write_text("# Acme", encoding="utf-8")
    (companies / "Mystery_Co.md").write_text("# Mystery", encoding="utf-8")

    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    failures = sc.check_orphan_markdown_files()
    assert len(failures) == 1
    assert "Mystery_Co.md" in failures[0]
    assert "Acme.md" not in failures[0]


def test_orphan_markdown_passes_when_all_files_known(tmp_path, monkeypatch):
    """All files match entities.normalized_name -> no failures."""
    db = tmp_path / "memory" / "research.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE entities (name TEXT, normalized_name TEXT)")
    conn.execute("INSERT INTO entities VALUES ('Acme', 'Acme')")
    conn.commit()
    conn.close()

    (tmp_path / "findata" / "Companies" / "Tech").mkdir(parents=True)
    (tmp_path / "findata" / "Companies" / "Tech" / "Acme.md").write_text(
        "# Acme", encoding="utf-8"
    )

    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    assert sc.check_orphan_markdown_files() == []


# --------------------------------------------------------------------------- #
# Permalink / sector consistency                                               #
# --------------------------------------------------------------------------- #
def test_permalink_mismatch_is_fatal(tmp_path, monkeypatch):
    """A file in Companies/Tech/ with permalink 'companies/wrong/x' is fatal."""
    companies = tmp_path / "findata" / "Companies" / "Tech"
    companies.mkdir(parents=True)
    (companies / "Acme.md").write_text(
        "---\n"
        "title: Acme\npermalink: companies/wrong/acme\n"
        "---\n# Acme\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    fatal, advisory = sc.check_permalink_sector_consistency()
    assert len(fatal) == 1
    assert "wrong" in fatal[0]
    assert advisory == []


def test_permalink_match_passes(tmp_path, monkeypatch):
    """A file in Companies/Tech/ with permalink 'companies/tech/acme' is clean."""
    companies = tmp_path / "findata" / "Companies" / "Tech"
    companies.mkdir(parents=True)
    (companies / "Acme.md").write_text(
        "---\n"
        "title: Acme\npermalink: companies/tech/acme\n"
        "---\n# Acme\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    fatal, advisory = sc.check_permalink_sector_consistency()
    assert fatal == []
    assert advisory == []


# --------------------------------------------------------------------------- #
# Date sanity                                                                  #
# --------------------------------------------------------------------------- #
def test_date_sanity_flags_modified_before_created(tmp_path, monkeypatch):
    findata = tmp_path / "findata"
    findata.mkdir()
    (findata / "X.md").write_text(
        "---\n"
        "title: X\ncreated: '2026-07-10'\nlast_modified: '2026-07-01'\n"
        "---\n# X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    failures = sc.check_date_sanity()
    assert len(failures) == 1
    assert "2026-07-01" in failures[0]


def test_date_sanity_passes_when_modified_after_created(tmp_path, monkeypatch):
    findata = tmp_path / "findata"
    findata.mkdir()
    (findata / "X.md").write_text(
        "---\n"
        "title: X\ncreated: '2026-07-01'\nlast_modified: '2026-07-10'\n"
        "---\n# X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    assert sc.check_date_sanity() == []


# --------------------------------------------------------------------------- #
# Dependency pinning                                                           #
# --------------------------------------------------------------------------- #
def test_dependency_pinning_flags_unpinned(tmp_path, monkeypatch):
    """pyproject.toml [project].dependencies with a '>= pin' is advisory, not fatal."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "x"\n'
        'version = "0.1.0"\n'
        'dependencies = ["flask==3.0", "somepkg>=1.24.6"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    fatal, advisory = sc.check_dependency_pinning()
    assert fatal == []  # advisory only
    assert len(advisory) == 1
    assert "somepkg" in advisory[0]


def test_dependency_pinning_passes_when_all_pinned(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "x"\n'
        'version = "0.1.0"\n'
        'dependencies = ["flask==3.0", "gunicorn==23.0"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    fatal, advisory = sc.check_dependency_pinning()
    assert fatal == []
    assert advisory == []


# --------------------------------------------------------------------------- #
# Existing checks still work (smoke)                                          #
# --------------------------------------------------------------------------- #


def test_helper_shebangs_detects_missing(tmp_path, monkeypatch):
    helpers = tmp_path / "helpers" / "misc"
    helpers.mkdir(parents=True)
    (helpers / "no_shebang.py").write_text('print("x")\n', encoding="utf-8")
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    failures = sc.check_helper_shebangs()
    assert len(failures) == 1
    assert "no_shebang.py" in failures[0]


def test_helper_shebangs_exempts_private_modules_and_init(tmp_path, monkeypatch):
    """`_*`-prefixed modules and `__init__.py` are library/package files, not
    executables, so they're exempt from the shebang rule (added when
    helpers/graph/_edge_writer.py was introduced as a shared lib module)."""
    helpers = tmp_path / "helpers" / "graph"
    helpers.mkdir(parents=True)
    (helpers / "__init__.py").write_text('"""pkg"""\n', encoding="utf-8")
    (helpers / "_edge_writer.py").write_text('"""internal lib"""\n', encoding="utf-8")
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    failures = sc.check_helper_shebangs()
    assert failures == []


# --------------------------------------------------------------------------- #
# sqlite3.connect discipline (B2 guard)                                        #
# --------------------------------------------------------------------------- #
# Modules allowed to call sqlite3.connect() directly. Each has a documented
# reason it can't (or shouldn't) route through helpers.core.db.connect().
# The guard catches NEW writer bypasses that silently skip FK enforcement +
# WAL — the class of bug that left orphaned rows on rename/delete.
_SQLITE3_CONNECT_ALLOWLIST = {
    # The canonical helper itself.
    "helpers/core/db.py",
    # autocommit (isolation_level=None) — VACUUM/ANALYZE/REINDEX can't run in
    # a transaction; connect() exposes no isolation_level kwarg.
    "helpers/maintenance/db_maint.py",
    # Online-backup API + snapshot verify need their own throwaway connections
    # to scratch files; connect() targets the production DB path.
    "helpers/maintenance/snapshot_db.py",
    # Read-only diagnostics (foreign_key_check, integrity_check) — FK
    # enforcement is irrelevant for PRAGMAs and SELECTs.
    "helpers/misc/database_integrity_check.py",
    # Read-only SELECT in a validator. FK enforcement irrelevant for SELECT.
    "helpers/validators/static_checks.py",
}


def test_no_sqlite3_connect_outside_allowlist():
    """B2 guard: sqlite3.connect() must only appear in the allowlisted modules.

    Every other DB-touching module must route through helpers.core.db.connect(),
    which guarantees PRAGMA foreign_keys = ON + journal_mode = WAL. A direct
    sqlite3.connect() silently skips both — the ON DELETE/UPDATE CASCADE rules
    never fire and deletes/renames leave orphaned rows.

    If you genuinely need a new direct-connect site, add it to the allowlist
    WITH a comment explaining why connect() doesn't fit (e.g. autocommit mode,
    backup API, read-only diagnostics).
    """
    import subprocess

    # grep -rn for sqlite3.connect( across helpers/ + app.py, excluding venv.
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", r"sqlite3\.connect(",  # noqa: S607  # PATH-resolved interpreter/binary (python3/node/grep) by design
         "helpers/", "app.py"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    # grep returns 1 for "no matches", which is the success case for new code.
    hits = [line for line in result.stdout.strip().splitlines() if line]

    violations = []
    for line in hits:
        # Each line is "path:lineno:code". Extract the path.
        path = line.split(":", 1)[0]
        if path not in _SQLITE3_CONNECT_ALLOWLIST:
            violations.append(line)

    assert not violations, (
        "sqlite3.connect() found outside the allowlist. These callers bypass "
        "helpers.core.db.connect() and silently skip FK enforcement + WAL. "
        "Route through connect() or, if you have a documented reason, add the "
        "path to _SQLITE3_CONNECT_ALLOWLIST.\n\nViolations:\n  "
        + "\n  ".join(violations)
    )


# --------------------------------------------------------------------------- #
# entities.name COLLATE NOCASE index (C2 guard)                               #
# --------------------------------------------------------------------------- #
def test_entities_name_nocase_index_constant_is_valid_ddl(tmp_path):
    """C2 guard: the migration DDL constant creates a usable COLLATE NOCASE
    index, and a resolver-style query uses it (SEARCH, not SCAN).

    Every /api/graph/<name> request resolves the path segment via
    `WHERE name = ? COLLATE NOCASE`. Without this index SQLite falls back to
    a SCAN of the entities PRIMARY KEY (verified via EXPLAIN QUERY PLAN on the
    live DB during Bundle C2). This test pins the fix against regression: if
    someone drops the constant or weakens it to a plain (non-NOCASE) index,
    the resolver silently reverts to a full scan.
    """
    from maintenance.migrate_to_graph_edges import ENTITIES_NAME_NOCASE_INDEX

    db = tmp_path / "c2.db"
    conn = sqlite3.connect(db)
    # Minimal entities table — just the column the index covers.
    conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO entities VALUES ('CEAT'), ('MRF'), ('Apollo Tyres')")
    conn.execute(ENTITIES_NAME_NOCASE_INDEX)
    conn.commit()

    # The index exists and is declared COLLATE NOCASE.
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='idx_entities_name_nocase'"
    ).fetchone()[0]
    assert "COLLATE NOCASE" in sql

    # And the resolver query plan uses it as a SEARCH, not a SCAN.
    plan = conn.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT name FROM entities WHERE name = ? COLLATE NOCASE",
        ("ceat",),
    ).fetchall()
    plan_str = " ".join(row[-1] for row in plan)
    assert "SEARCH" in plan_str and "idx_entities_name_nocase" in plan_str, (
        f"resolver query is not using the NOCASE index; plan={plan_str!r}"
    )
    conn.close()


# --------------------------------------------------------------------------- #
# Entry-point sys.path discipline (B2 regression guard)                       #
# --------------------------------------------------------------------------- #
def test_helper_entry_points_set_sys_path_before_helpers_import():
    """Guard: any helpers/*.py entry point (has ``if __name__ == "__main__"``)
    that imports from ``helpers.*`` must set up ``sys.path`` itself.

    Without this, the script works under pytest (which puts the repo root on
    sys.path) but crashes with ``ModuleNotFoundError: No module named 'helpers'``
    when invoked as a subprocess — the path used by ``make sync-tags``,
    ``maint.py --full``, and the performance tests. This class of regression
    was introduced when B2 routed writers through ``from helpers.core.db
    import connect``; the fix is a ``sys.path.insert(0, str(PROJECT_ROOT))``
    bootstrap before the import.

    Uses AST (not textual grep) so docstring usage examples like
    ``from helpers.graph.query import connect`` don't false-positive.
    """
    import ast

    helpers_dir = REPO_ROOT / "helpers"
    violations: list[str] = []

    for py in helpers_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        src = py.read_text(encoding="utf-8")
        # Only check entry points (scripts runnable as subprocesses).
        if '__name__ == "__main__"' not in src and "__name__ == '__main__'" not in src:
            continue
        # Does it import from helpers.* at all? (AST, so docstrings are skipped.)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        has_helpers_import = any(
            (isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("helpers"))
            or (isinstance(n, ast.Import) and any(a.name.startswith("helpers") for a in n.names))
            for n in ast.walk(tree)
        )
        if not has_helpers_import:
            continue
        # Must set up sys.path somewhere in the file.
        if "sys.path.insert" not in src and "sys.path.append" not in src:
            violations.append(str(py.relative_to(REPO_ROOT)))

    assert not violations, (
        "These helpers/ entry-point scripts import from helpers.* but never set "
        "up sys.path — they crash with ModuleNotFoundError when run as a "
        "subprocess (make targets, maint.py, perf tests). Add a "
        "`sys.path.insert(0, str(PROJECT_ROOT))` bootstrap before the first "
        "`from helpers.* import`.\n\nViolations:\n  "
        + "\n  ".join(violations)
    )


# --------------------------------------------------------------------------- #
# Bundle E1: canonical ENTITIES_DDL + indexes                                 #
# --------------------------------------------------------------------------- #
def test_entities_ddl_constant_creates_table_with_all_columns():
    """E1: ENTITIES_DDL produces the full 8-column production schema on a
    fresh DB. Previously the canonical entities DDL existed only in the live
    .db file (built out-of-band + ALTER-added); a from-scratch rebuild silently
    dropped columns. This pins the canonical definition.

    Bundle C2/L2 (2026-07-28): market_cap + index_membership were DROPPED
    (market_cap is sourced from the market_cap/* tag; index_membership was
    99.4% empty). Column count went 10 -> 8.
    """
    from maintenance.migrate_to_graph_edges import ENTITIES_DDL

    conn = sqlite3.connect(":memory:")
    conn.execute(ENTITIES_DDL)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()]
    expected = [
        "name", "entity_type", "created_at",
        "file_path", "last_updated", "normalized_name", "sector_classification",
        "ticker",
    ]
    assert cols == expected, f"columns mismatch: {cols}"
    conn.close()


def test_entities_ddl_enforces_name_suffix_check():
    """E1: the name-suffix CHECK rejects company names ending in the banned
    suffixes (Ltd, Limited, Ltd., Pvt, Private). This constraint existed only
    in the live .db file before E1; a rebuild would lose it silently.
    """
    from maintenance.migrate_to_graph_edges import ENTITIES_DDL

    conn = sqlite3.connect(":memory:")
    conn.execute(ENTITIES_DDL)
    # A clean name inserts fine.
    conn.execute(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
        ("Reliance Industries", "company"),
    )
    # Each banned suffix is rejected.
    for bad in ["Foo Ltd", "Bar Limited", "Baz Ltd.", "Qux Pvt", "Quux Private"]:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
                (bad, "company"),
            )
    conn.close()


def test_entities_indexes_constants_are_valid_ddl():
    """E1: ENTITIES_INDEXES are valid DDL that create the 4 production indexes
    on a fresh entities table. Previously these indexes had no source DDL.
    """
    from maintenance.migrate_to_graph_edges import ENTITIES_DDL, ENTITIES_INDEXES

    conn = sqlite3.connect(":memory:")
    conn.execute(ENTITIES_DDL)
    for idx in ENTITIES_INDEXES:
        conn.execute(idx)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='entities'"
        ).fetchall()
    }
    expected = {
        "idx_entities_sector_classification",
        "idx_entities_normalized_name",
        "idx_entities_entity_type",
        "idx_entities_file_path",
    }
    assert expected <= names, f"missing indexes: {expected - names}"
    conn.close()


def test_entities_file_path_index_constant_is_valid_ddl():
    """Bundle Q1: the idx_entities_file_path index must be present in
    ENTITIES_INDEXES and create cleanly. Without it, /api/entity/<path>
    and derive_co_mentions do a full SCAN of entities on every call."""
    from maintenance.migrate_to_graph_edges import ENTITIES_DDL, ENTITIES_INDEXES

    # The index constant must exist in the list.
    assert any("idx_entities_file_path" in idx for idx in ENTITIES_INDEXES), (
        "idx_entities_file_path missing from ENTITIES_INDEXES"
    )
    # It must create without error on a fresh entities table.
    conn = sqlite3.connect(":memory:")
    conn.execute(ENTITIES_DDL)
    for idx in ENTITIES_INDEXES:
        conn.execute(idx)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='entities'"
        ).fetchall()
    }
    assert "idx_entities_file_path" in names
    conn.close()


def test_migrate_creates_entities_with_check(tmp_path):
    """E1: migrate() on a fresh DB creates entities with the CHECK live. This
    is the end-to-end proof that Step 0 works: a bad-suffix insert is rejected
    after running migrate() against an empty DB.
    """
    import sqlite3

    # Patch DB_PATH to a temp file, run migrate, then verify the CHECK.
    import maintenance.migrate_to_graph_edges as m

    tmp_db = tmp_path / "fresh.db"
    orig_path = m.DB_PATH
    m.DB_PATH = tmp_db
    try:
        m.migrate(verbose=False)
    finally:
        m.DB_PATH = orig_path

    conn = sqlite3.connect(tmp_db)
    # Good name works.
    conn.execute(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
        ("Good Co", "company"),
    )
    # Bad suffix is rejected — proves the CHECK survived a fresh migrate().
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
            ("Bad Co Ltd", "company"),
        )
    conn.close()


# --------------------------------------------------------------------------- #
# Bundle E4: CHECK (json_valid(properties)) on graph_edges                    #
# --------------------------------------------------------------------------- #
def test_graph_edges_ddl_has_json_valid_check():
    """E4: GRAPH_EDGES_DDL includes a CHECK(json_valid(properties)) constraint
    so malformed JSON cannot be inserted. Pins the constraint against removal.
    """
    from maintenance.migrate_to_graph_edges import GRAPH_EDGES_DDL

    assert "json_valid(properties)" in GRAPH_EDGES_DDL, (
        "GRAPH_EDGES_DDL lost its json_valid CHECK — malformed JSON could be "
        "inserted into the properties column again."
    )


def test_graph_edges_rejects_malformed_properties():
    """E4: a fresh graph_edges table (from GRAPH_EDGES_DDL) accepts valid JSON
    in the properties column but rejects malformed JSON via the CHECK.
    """
    from maintenance.migrate_to_graph_edges import ENTITIES_DDL, GRAPH_EDGES_DDL

    conn = sqlite3.connect(":memory:")
    conn.execute(ENTITIES_DDL)
    conn.execute(GRAPH_EDGES_DDL)
    conn.execute(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)", ("A", "company")
    )
    conn.execute(
        "INSERT INTO entities (name, entity_type) VALUES (?, ?)", ("B", "company")
    )
    # Valid JSON inserts fine.
    conn.execute(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref, properties) "
        "VALUES (?, ?, ?, ?, ?)",
        ("A", "B", "part_of", "test", '{"year": 2020}'),
    )
    # Malformed JSON is rejected.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO graph_edges (source, target, edge_type, source_ref, properties) "
            "VALUES (?, ?, ?, ?, ?)",
            ("B", "A", "has_company", "test", "not json{"),
        )
    conn.close()


def test_graph_edges_indexes_constants_are_valid_ddl():
    """GRAPH_EDGES_INDEXES are valid DDL that create the production indexes
    on a fresh graph_edges table. Mirrors the E1 test for ENTITIES_INDEXES;
    closes the gap noted in doc/improvements/sqlite_improvs.txt (the edges indexes had no
    validity test even on :memory:).

    ge_source_idx was removed (2026-08-04): a standalone index on graph_edges(source)
    is redundant with the UNIQUE(source,target,edge_type) auto-index, which leads with
    `source` — EXPLAIN QUERY PLAN confirms the planner prefers it even when the manual
    index is absent. The count is now 3, not 4."""
    from maintenance.migrate_to_graph_edges import (
        GRAPH_EDGES_DDL,
        GRAPH_EDGES_INDEXES,
        ENTITIES_DDL,
    )

    conn = sqlite3.connect(":memory:")
    conn.execute(ENTITIES_DDL)
    conn.execute(GRAPH_EDGES_DDL)
    for idx in GRAPH_EDGES_INDEXES:
        conn.execute(idx)
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='graph_edges'"
        ).fetchall()
    }
    expected = {"ge_type_idx", "ge_target_idx", "ge_valid_idx"}
    assert expected <= names, f"missing graph_edges indexes: {expected - names}"
    # ge_source_idx is intentionally absent — it's redundant with the UNIQUE
    # auto-index (sqlite_autoindex_graph_edges_1 leads with `source`).
    assert "ge_source_idx" not in names, (
        "ge_source_idx was removed as redundant; if re-adding, update this test "
        "and db_maint.py's redundancy checker expectations."
    )
    conn.close()


# --------------------------------------------------------------------------- #
# Bundle P: live-DB schema conformance regression guards                      #
# --------------------------------------------------------------------------- #
# These tests hit the LIVE memory/research.db to verify the physical schema
# matches the canonical DDL constants. They exist because CREATE TABLE IF
# NOT EXISTS (used by migrate()) is a no-op on pre-existing tables, so the
# live DB can silently diverge from source. The rebuild_schema.py script
# (Bundle P) closes the gap; these tests keep it closed.
#
# Marked `live` so they only run via `make test-live` / `make qa` (which
# includes live), not the fast unit-only `make test`.

LIVE_DB = REPO_ROOT / "memory" / "research.db"


@pytest.mark.live
def test_live_graph_edges_has_json_valid_check():
    """P1 regression guard: the LIVE graph_edges must have the
    CHECK (json_valid(properties)) constraint in its DDL.

    Pre-Bundle-P this failed (the live table was built before the CHECK
    existed; CREATE TABLE IF NOT EXISTS couldn't add it). rebuild_schema.py
    fixed it. This test fails if a future schema regression drops the CHECK
    or if the live DB is rebuilt from a stale snapshot."""
    if not LIVE_DB.exists():
        pytest.skip("live DB not present")
    conn = sqlite3.connect(str(LIVE_DB))
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='graph_edges'"
        ).fetchone()[0]
        assert "json_valid(properties)" in ddl, (
            "LIVE graph_edges is missing CHECK (json_valid(properties)) — "
            "run: python3 helpers/maintenance/rebuild_schema.py"
        )
    finally:
        conn.close()


@pytest.mark.live
def test_live_graph_analytics_pk_is_metric_first():
    """P3 regression guard: the LIVE graph_analytics PK must be
    (metric, entity_name), not the legacy (entity_name, metric).

    The reversal turns /api/graph/metrics WHERE metric=? from a full SCAN
    into a prefix SEARCH. This test fails if a future schema regression
    reverts the PK order."""
    if not LIVE_DB.exists():
        pytest.skip("live DB not present")
    conn = sqlite3.connect(str(LIVE_DB))
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='graph_analytics'"
        ).fetchone()[0]
        assert "PRIMARY KEY (metric, entity_name)" in ddl, (
            "LIVE graph_analytics PK is not (metric, entity_name) — "
            "run: python3 helpers/maintenance/rebuild_schema.py"
        )
    finally:
        conn.close()


@pytest.mark.live
def test_live_entities_file_path_query_uses_index():
    """Q1 regression guard: the LIVE entities table must use an index for
    the file_path lookup (the /api/entity/<path> + derive_co_mentions hot
    path). Pre-Q1 this was a full SCAN because no index existed."""
    if not LIVE_DB.exists():
        pytest.skip("live DB not present")
    conn = sqlite3.connect(str(LIVE_DB))
    try:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT name FROM entities WHERE file_path = 'x'"
        ).fetchall()
        detail = plan[-1][-1]
        assert "SEARCH" in detail and "idx_entities_file_path" in detail, (
            f"file_path lookup is not using the index: {detail} — "
            f"run: python3 helpers/maintenance/migrate_to_graph_edges.py"
        )
    finally:
        conn.close()


@pytest.mark.live
def test_live_entities_has_no_market_cap_column():
    """C2 regression guard: the LIVE entities table must NOT have a
    market_cap column.

    The column was dropped (2026-07-28) because it disagreed with the
    market_cap/* tag for 126 companies — the tag (synced from note YAML via
    sync_tags.py) is the source of truth. This test fails if a future stale-
    snapshot restore re-adds the column. Run rebuild_schema.py to fix."""
    if not LIVE_DB.exists():
        pytest.skip("live DB not present")
    conn = sqlite3.connect(str(LIVE_DB))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()}
        assert "market_cap" not in cols, (
            "LIVE entities still has a market_cap column (dropped in C2) — "
            "run: python3 helpers/maintenance/rebuild_schema.py"
        )
        # The index on the dropped column must also be gone.
        idx_names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='entities'"
            ).fetchall()
        }
        assert "idx_entities_market_cap" not in idx_names, (
            "LIVE entities still has idx_entities_market_cap (column dropped in C2)"
        )
    finally:
        conn.close()


@pytest.mark.live
def test_live_entities_has_no_index_membership_column():
    """L2 regression guard: the LIVE entities table must NOT have an
    index_membership column.

    The column was dropped (2026-07-28) — it was 99.4% empty (6/1031 rows
    populated, 3 of those literal '[]'). This test fails if a future stale-
    snapshot restore re-adds the column. Run rebuild_schema.py to fix."""
    if not LIVE_DB.exists():
        pytest.skip("live DB not present")
    conn = sqlite3.connect(str(LIVE_DB))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()}
        assert "index_membership" not in cols, (
            "LIVE entities still has an index_membership column (dropped in L2) — "
            "run: python3 helpers/maintenance/rebuild_schema.py"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _parse_frontmatter — pure function
# ---------------------------------------------------------------------------
def test_parse_frontmatter_valid():
    text = "---\ntitle: Test\ntags:\n- sector/healthcare\n---\n\n# Body"
    fm = sc._parse_frontmatter(text)
    assert fm is not None
    assert fm["title"] == "Test"


def test_parse_frontmatter_no_frontmatter():
    text = "# Just a heading"
    assert sc._parse_frontmatter(text) is None


def test_parse_frontmatter_no_closing():
    text = "---\ntitle: Test\n"
    assert sc._parse_frontmatter(text) is None


def test_parse_frontmatter_bad_yaml():
    text = "---\nbad: yaml: [unclosed\n---"
    assert sc._parse_frontmatter(text) is None


# ---------------------------------------------------------------------------
# _check_tags_one — per-file tag logic
# ---------------------------------------------------------------------------
def test_check_tags_one_lowercase_canonical():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"tags": ["sector/healthcare", "entity_type/company"]}
    f, a = sc._check_tags_one(p, fm)
    assert f == []
    assert a == []


def test_check_tags_one_uppercase_fatal():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"tags": ["sector/Healthcare"]}
    f, a = sc._check_tags_one(p, fm)
    assert len(f) == 1
    assert "Healthcare" in f[0]


def test_check_tags_one_synonym_advisory():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"tags": ["sector/nonexistent"]}
    f, a = sc._check_tags_one(p, fm)
    assert f == []
    assert len(a) == 1


def test_check_tags_one_non_sector_tag_ignored():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"tags": ["entity_type/company", "edition/123"]}
    f, a = sc._check_tags_one(p, fm)
    assert f == []
    assert a == []


def test_check_tags_one_tags_not_list():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"tags": "sector/healthcare"}
    f, a = sc._check_tags_one(p, fm)
    assert f == []
    assert a == []


def test_check_tags_one_no_tags():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {}
    f, a = sc._check_tags_one(p, fm)
    assert f == []
    assert a == []


# ---------------------------------------------------------------------------
# _check_permalink_one — per-file permalink/sector logic
# ---------------------------------------------------------------------------
def test_check_permalink_one_match():
    p = sc.REPO_ROOT / "findata" / "Companies" / "healthcare" / "Test_Co.md"
    fm = {"permalink": "companies/healthcare/Test_Co"}
    assert sc._check_permalink_one(p, fm) == []


def test_check_permalink_one_mismatch():
    p = sc.REPO_ROOT / "findata" / "Companies" / "healthcare" / "Test_Co.md"
    fm = {"permalink": "companies/technology/Test_Co"}
    failures = sc._check_permalink_one(p, fm)
    assert len(failures) == 1
    assert "technology" in failures[0]


def test_check_permalink_one_no_permalink():
    p = sc.REPO_ROOT / "findata" / "Companies" / "healthcare" / "Test_Co.md"
    fm = {}
    assert sc._check_permalink_one(p, fm) == []


def test_check_permalink_one_non_companies():
    p = sc.REPO_ROOT / "findata" / "Sectors" / "healthcare.md"
    fm = {"permalink": "sectors/healthcare"}
    assert sc._check_permalink_one(p, fm) == []


# ---------------------------------------------------------------------------
# _check_date_one — per-file date logic
# ---------------------------------------------------------------------------
def test_check_date_one_ok():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"created": "2025-01-01", "last_modified": "2025-06-01"}
    assert sc._check_date_one(p, fm) == []


def test_check_date_one_modified_before_created():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"created": "2025-06-01", "last_modified": "2025-01-01"}
    failures = sc._check_date_one(p, fm)
    assert len(failures) == 1


def test_check_date_one_missing_created():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"last_modified": "2025-06-01"}
    assert sc._check_date_one(p, fm) == []


def test_check_date_one_missing_modified():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"created": "2025-01-01"}
    assert sc._check_date_one(p, fm) == []


def test_check_date_one_quoted_dates():
    p = sc.REPO_ROOT / "findata" / "Test.md"
    fm = {"created": "\'2025-06-01\'", "last_modified": "\'2025-01-01\'"}
    failures = sc._check_date_one(p, fm)
    assert len(failures) == 1


# ---------------------------------------------------------------------------
# _iter_findata_md — with temp directory
# ---------------------------------------------------------------------------
def test_iter_findata_md(tmp_path, monkeypatch):
    # Create a simple findata-like tree
    findata = tmp_path / "findata"
    findata.mkdir()
    (findata / "Test.md").write_text("---\ntitle: T\ntags: []\n---\n\n# T")
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    results = list(sc._iter_findata_md(findata))
    assert len(results) == 1
    p, text, fm = results[0]
    assert fm is not None
    assert fm["title"] == "T"


# ---------------------------------------------------------------------------
# _has_node — just verify it returns a bool
# ---------------------------------------------------------------------------
def test_has_node_returns_bool():
    assert isinstance(sc._has_node(), bool)


# ---------------------------------------------------------------------------
# check_dependency_pinning — with temp pyproject.toml
# ---------------------------------------------------------------------------
def test_dependency_pinning_with_unpinned(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "x"\n'
        'version = "0.1.0"\n'
        'dependencies = ["yfinance>=0.2", "yaml==6.0"]\n',
    )
    _, advisory = sc.check_dependency_pinning()
    assert len(advisory) == 1
    assert "yfinance" in advisory[0]


def test_dependency_pinning_all_pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "x"\n'
        'version = "0.1.0"\n'
        'dependencies = ["yfinance==0.2.31"]\n',
    )
    _, advisory = sc.check_dependency_pinning()
    assert advisory == []


def test_dependency_pinning_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    _, advisory = sc.check_dependency_pinning()
    assert advisory == []


# ---------------------------------------------------------------------------
# _walk — with temp directory
# ---------------------------------------------------------------------------
def test_walk_finds_py_files(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y = 2")
    (tmp_path / "c.txt").write_text("z")
    result = list(sc._walk(tmp_path, ".py"))
    assert len(result) == 2
    names = {p.name for p in result}
    assert names == {"a.py", "b.py"}


def test_walk_skips_venv(tmp_path):
    (tmp_path / "good.py").write_text("x")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "bad.py").write_text("x")
    result = list(sc._walk(tmp_path, ".py"))
    assert len(result) == 1
    assert result[0].name == "good.py"


# ---------------------------------------------------------------------------
# check_python_syntax — with temp directory
# ---------------------------------------------------------------------------
def test_python_syntax_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "good.py").write_text("x = 1\n")
    assert sc.check_python_syntax() == []


def test_python_syntax_catches_error(tmp_path, monkeypatch):
    """check_python_syntax surfaces compilation errors."""
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "bad.py").write_text("def f(\n")
    failures = sc.check_python_syntax()
    assert len(failures) >= 1
    assert "bad.py" in failures[0]


# ---------------------------------------------------------------------------
# check_stray_artifacts — with temp directory
# ---------------------------------------------------------------------------
def test_stray_artifacts_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "normal.py").write_text("x = 1")
    # _all_files uses REPO_ROOT.rglob, so tmp_path needs to look like repo root
    assert sc.check_stray_artifacts() == []


def test_stray_artifacts_finds_ds_store(tmp_path, monkeypatch):
    """Find stray .DS_Store files."""
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / ".DS_Store").write_bytes(b"\x00\x00")
    failures = sc.check_stray_artifacts()
    assert len(failures) >= 1


def test_stray_artifacts_finds_bak(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "config.bak").write_text("old")
    failures = sc.check_stray_artifacts()
    assert len(failures) == 1


# ---------------------------------------------------------------------------
# check_helper_shebangs — with temp directory
# ---------------------------------------------------------------------------
def test_shebangs_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "good.py").write_text("#!/usr/bin/env python3\nx = 1")
    assert sc.check_helper_shebangs() == []


def test_shebangs_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "bad.py").write_text("x = 1")
    failures = sc.check_helper_shebangs()
    assert len(failures) == 1
    assert "bad.py" in failures[0]


def test_shebangs_skips_init(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "__init__.py").write_text("x = 1")
    assert sc.check_helper_shebangs() == []


def test_shebangs_skips_private(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "_private.py").write_text("x = 1")
    assert sc.check_helper_shebangs() == []


def test_shebangs_empty_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    (helpers / "empty.py").write_text("")
    failures = sc.check_helper_shebangs()
    assert len(failures) == 1
    assert "empty" in failures[0]


# ---------------------------------------------------------------------------
# check_merge_markers_and_artifacts — with temp directory
# ---------------------------------------------------------------------------
def test_merge_markers_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "code.py").write_text("x = 1\n")
    merge_failures, artifact_failures = sc.check_merge_markers_and_artifacts()
    assert merge_failures == []
    assert artifact_failures == []


def test_merge_markers_detects_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "conflict.py").write_text(
        "x = 1\n"
        "<<<<<<< HEAD\n"
        "a = 1\n"
        "=======\n"
        "a = 2\n"
        ">>>>>>> feature\n"
    )
    merge_failures, artifact_failures = sc.check_merge_markers_and_artifacts()
    assert len(merge_failures) == 1
    assert artifact_failures == []


def test_merge_markers_skips_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00\x00\x00")
    merge_failures, artifact_failures = sc.check_merge_markers_and_artifacts()
    assert merge_failures == []
    assert artifact_failures == []


# ---------------------------------------------------------------------------
# check_required_files
# ---------------------------------------------------------------------------
def test_required_files_all_present(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    for f in ("pytest.ini", "pyproject.toml", "Makefile"):
        (tmp_path / f).write_text("x")
    assert sc.check_required_files() == []


def test_required_files_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    (tmp_path / "pytest.ini").write_text("x")
    failures = sc.check_required_files()
    assert "pyproject.toml" in failures
    assert "Makefile" in failures


# ---------------------------------------------------------------------------
# check_yaml_frontmatter — with temp directory
# ---------------------------------------------------------------------------
def test_yaml_frontmatter_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    findata = tmp_path / "findata"
    findata.mkdir()
    (findata / "test.md").write_text("---\ntitle: T\n---\n\n# T")
    assert sc.check_yaml_frontmatter() == []


def test_yaml_frontmatter_bad_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    findata = tmp_path / "findata"
    findata.mkdir()
    (findata / "bad.md").write_text("---\nbad: [unclosed\n---\n\n# B")
    failures = sc.check_yaml_frontmatter()
    assert len(failures) >= 1


def test_yaml_frontmatter_no_closing(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    findata = tmp_path / "findata"
    findata.mkdir()
    (findata / "open.md").write_text("---\ntitle: No close\n\n# Body")
    failures = sc.check_yaml_frontmatter()
    assert len(failures) == 1


# ---------------------------------------------------------------------------
# check_findata_yaml — combined runner with temp directory
# ---------------------------------------------------------------------------
def test_findata_yaml_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    findata = tmp_path / "findata"
    findata.mkdir()
    (findata / "test.md").write_text("---\ntitle: T\ntags: []\n---\n\n# T")
    fatal, advisory = sc.check_findata_yaml()
    assert fatal == []


# ---------------------------------------------------------------------------
# _db_path
# ---------------------------------------------------------------------------
def test_db_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    result = sc._db_path()
    assert result == tmp_path / "memory" / "research.db"


# ---------------------------------------------------------------------------
# check_sqlite_helper_usage — with temp directory
# ---------------------------------------------------------------------------
def test_sqlite_helper_usage_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    helpers = tmp_path / "helpers"
    core = helpers / "core"
    core.mkdir(parents=True)
    (core / "db.py").write_text("import sqlite3\n")
    (core / "other.py").write_text("from helpers.core.db import connect\nx = 1")
    failures = sc.check_sqlite_helper_usage()
    assert failures == []


def test_sqlite_helper_usage_detects_violation(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    helpers = tmp_path / "helpers"
    misc = helpers / "misc"
    misc.mkdir(parents=True)
    (misc / "bad.py").write_text("import sqlite3\nconn = sqlite3.connect('x.db')\n")
    failures = sc.check_sqlite_helper_usage()
    assert len(failures) >= 1
    assert "bad.py" in failures[0]


# ---------------------------------------------------------------------------
# check_db_meta_generation — with temp DB
# ---------------------------------------------------------------------------
def test_db_meta_generation_missing_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    assert sc.check_db_meta_generation() == []


def test_db_meta_generation_missing_table(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    db_dir = tmp_path / "memory"
    db_dir.mkdir()
    db = db_dir / "research.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE entities (name TEXT)")
    conn.commit()
    conn.close()
    failures = sc.check_db_meta_generation()
    assert len(failures) >= 1
    assert "db_meta" in failures[0]
