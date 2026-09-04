"""
Shared pytest fixtures for the FinData QA suite.

Builds a synthetic vault + a seeded SQLite DB with intentional defects so the
two validators can be exercised deterministically, isolated from the live
memory/research.db / findata vault.
"""

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

# Make helpers/ importable as top-level modules (validators live under helpers/),
# and make the repo root importable so `import app as A` works regardless of
# whether pytest was invoked as `pytest tests/foo.py` (rootdir = tests/) or as
# `pytest` / `pytest tests/` (rootdir = repo root via pytest.ini's testpaths).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from misc.database_integrity_check import DatabaseIntegrityChecker  # noqa: E402
from validators.verify_notes import NotesVerifier  # noqa: E402
from maintenance.migrate_to_graph_edges import ENTITIES_DDL  # noqa: E402

# --------------------------------------------------------------------------- #
# Markdown note templates (values chosen to match what each validator checks) #
# --------------------------------------------------------------------------- #
GOOD_COMPANY = """\
---
title: Good Bank
type: company
tags:
- entity_type/company
- sector/banking
created: '2025-01-01'
last_modified: '2025-01-02'
normalized_name: Good_Bank
sector: banking
permalink: /companies/banking/good-bank
---
# Good Bank

A large private-sector bank with a nationwide retail and corporate franchise.
Strong deposit franchise and digital banking leadership.
"""

# Missing the required 'type' field -> check_yaml_structure flags it.
MISSING_TYPE_COMPANY = """\
---
title: No Type Co
tags:
- entity_type/company
permalink: /companies/x/no-type
---
# No Type Co

Some real content line one.
Some real content line two.
"""

# Has a heading but <2 meaningful (non-placeholder) lines -> content_minimal.
THIN_CONTENT_COMPANY = """\
---
title: Thin Co
type: company
permalink: /companies/x/thin
---
# Thin Co

N/A
"""

# 2 meaningful lines but NO heading anywhere -> content_missing_structure.
NO_HEADING_COMPANY = """\
---
title: No Heading Co
type: company
permalink: /companies/x/no-heading
---

This is a meaningful content line one.
This is a meaningful content line two.
"""

# Sector that violates the /sectors/ permalink rule.
BAD_SECTOR_PERMALINK = """\
---
title: Bad Permalink Sector
type: sector
tags:
- entity_type/sector
created: '2025-01-01'
last_modified: '2025-01-02'
normalized_name: Bad_Permalink_Sector
permalink: /companies/wrong
---
# Bad Permalink Sector

Description of this sector line one.
Description of this sector line two.
"""

# A compliant sector note (allowed fields only, dates quoted, title unquoted).
GOOD_SECTOR = """\
---
title: Banking
type: sector
tags:
- entity_type/sector
created: '2025-01-01'
last_modified: '2025-01-02'
normalized_name: Banking
permalink: /sectors/banking
---
# Banking

Sector overview line one.
Sector overview line two.
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def fake_vault(tmp_path) -> Path:
    """A synthetic findata/ vault tree under tmp_path with good + broken notes."""
    companies = tmp_path / "findata" / "Companies" / "Banking"
    sectors = tmp_path / "findata" / "Sectors"

    _write(companies / "Good_Bank.md", GOOD_COMPANY)
    _write(companies / "Missing_Type.md", MISSING_TYPE_COMPANY)
    _write(companies / "Thin_Content.md", THIN_CONTENT_COMPANY)
    _write(companies / "No_Heading.md", NO_HEADING_COMPANY)
    _write(sectors / "Bad_Permalink_Sector.md", BAD_SECTOR_PERMALINK)
    _write(sectors / "Banking.md", GOOD_SECTOR)
    return tmp_path


@pytest.fixture
def notes_verifier(fake_vault) -> NotesVerifier:
    return NotesVerifier(project_root=fake_vault)


def _create_schema(conn: sqlite3.Connection) -> None:
    # entities: canonical DDL from the migration module (Bundle E1) — the
    # name-suffix CHECK and all 10 production columns live in one place.
    conn.execute(ENTITIES_DDL)
    conn.executescript(
        """
        CREATE TABLE relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            UNIQUE(source, target, relation_type),
            -- FKs declared to match production; tests that intentionally
            -- create inconsistent rows do so with PRAGMA foreign_keys = OFF
            -- (the SQLite default), so the inserts succeed.
            FOREIGN KEY (source) REFERENCES entities(name)
                ON DELETE CASCADE ON UPDATE CASCADE,
            FOREIGN KEY (target) REFERENCES entities(name)
                ON DELETE CASCADE ON UPDATE CASCADE
        );
        CREATE TABLE entity_tags (
            entity_name TEXT NOT NULL,
            tag         TEXT NOT NULL,
            PRIMARY KEY (entity_name, tag),
            FOREIGN KEY (entity_name) REFERENCES entities(name)
                ON DELETE CASCADE ON UPDATE CASCADE
        );
        """
    )


@pytest.fixture
def integrity_db(fake_vault):
    """
    A seeded DB whose file_path values point into fake_vault, with intentional
    defects: a missing (nonexistent) file, an empty path, and a bad filename.
    """
    db_path = fake_vault / "test.db"
    conn = sqlite3.connect(db_path)
    _create_schema(conn)
    conn.executemany(
        "INSERT INTO entities (name, entity_type, file_path, normalized_name) VALUES (?, ?, ?, ?)",
        [
            # Valid company: file exists on disk under the vault.
            (
                "Good Bank",
                "company",
                "findata/Companies/Banking/Good_Bank.md",
                "Good_Bank",
            ),
            # Defect 1: file_path points at a file that does not exist.
            (
                "Ghost Co",
                "company",
                "findata/Companies/Banking/Does_Not_Exist.md",
                "Does_Not_Exist",
            ),
            # Defect 2: empty file_path.
            ("No Path Co", "company", "", "No_Path"),
            # Defect 3: invalid filename format (hyphen) — file created below so
            # the only failing check is filename format, not file-not-found.
            (
                "Bad Name Co",
                "company",
                "findata/Companies/Banking/Bad-Name.md",
                "Bad-Name",
            ),
            # Valid sector.
            ("Banking", "sector", "findata/Sectors/Banking.md", "Banking"),
        ],
    )
    # A clean bidirectional pair: Good Bank <-> Banking.
    conn.executemany(
        "INSERT INTO relations (source, target, relation_type) VALUES (?, ?, ?)",
        [
            ("Good Bank", "Banking", "part_of"),
            ("Banking", "Good Bank", "has_company"),
        ],
    )
    conn.commit()
    conn.close()

    # Materialise the bad-filename file so its sole defect is the format.
    _write(fake_vault / "findata" / "Companies" / "Banking" / "Bad-Name.md", GOOD_COMPANY)

    checker = DatabaseIntegrityChecker(db_path=str(db_path), base_path=str(fake_vault))
    yield db_path, checker
    checker.close()  # release the memoized connection


# --------------------------------------------------------------------------- #
# Shared graph-API unit-test infrastructure                                   #
# --------------------------------------------------------------------------- #
# Promoted from tests/test_api_graph.py during the file split so every split
# file (test_api_graph_unit/metrics/bundles) shares one schema + seed +
# client fixture. `app` is imported lazily inside the context manager so
# collecting non-graph tests doesn't trigger Flask app startup (load_dotenv
# etc.); the repo root is already on sys.path via REPO_ROOT above.

_UNIT_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY,
    entity_type TEXT,
    file_path TEXT,
    sector_classification TEXT,
    ticker TEXT,
    last_updated DATETIME
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag)
);
CREATE TABLE graph_edges (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    properties  TEXT NOT NULL DEFAULT '{}',
    valid_from  DATE,
    valid_to    DATE,
    source_ref  TEXT NOT NULL,
    symmetric   INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, target, edge_type),
    CHECK (source != target)
);
CREATE TABLE graph_analytics (
    entity_name TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       TEXT NOT NULL,
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric, entity_name)
);
"""

# (name, type, file_path, sector_classification, ticker)
_UNIT_ENTITIES = [
    ("HDFC Bank", "company", "findata/Companies/Banking/Hdfc_Bank.md", "Banking", "HDFCBANK"),
    ("ICICI Bank", "company", "findata/Companies/Banking/ICICI_Bank.md", "Banking", "ICICIBANK"),
    ("Infosys", "company", "findata/Companies/Technology/Infosys.md", "Technology", "INFY"),
    ("Banking", "sector", "findata/Sectors/Banking.md", None, None),
    ("Technology", "sector", "findata/Sectors/Technology.md", None, None),
    ("No Ticker Co", "company", "findata/Companies/X/No_Ticker.md", "Technology", None),
]

# market_cap/* tags for the unit entities (the source of truth post-C2).
_UNIT_TAGS = [
    ("HDFC Bank", "market_cap/large_cap"),
    ("ICICI Bank", "market_cap/large_cap"),
    ("Infosys", "market_cap/large_cap"),
    ("No Ticker Co", "market_cap/small_cap"),
]

# (source, target, edge_type, source_ref)
_UNIT_EDGES = [
    ("HDFC Bank", "Banking", "part_of", "seed"),
    ("Banking", "HDFC Bank", "has_company", "seed"),
    ("ICICI Bank", "Banking", "part_of", "seed"),
    ("Infosys", "Technology", "part_of", "seed"),
    ("No Ticker Co", "Technology", "part_of", "seed"),
    ("HDFC Bank", "ICICI Bank", "competes_with", "seed"),
    ("ICICI Bank", "Infosys", "subsidiary_of", "seed"),
]


@contextmanager
def seeded_graph_sqlite_db(tmp_path):
    """Build a tiny SQLite DB with the schema + seed data above.

    Yields a Flask test_client with app.get_db_connection monkey-patched to
    return a fresh sqlite3.connect() against the temp file. The connection
    uses sqlite3.Row factory to match production (helpers.core.db.connect
    sets row_factory=sqlite3.Row); without this, route code that does
    row["name"] would break under the test fixture.
    """
    import app as A  # lazy: avoid Flask-app startup at collection time

    db_path = tmp_path / "unit_graph.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_UNIT_SCHEMA)
    conn.executemany(
        "INSERT INTO entities "
        "(name, entity_type, file_path, sector_classification, ticker) "
        "VALUES (?,?,?,?,?)",
        _UNIT_ENTITIES,
    )
    conn.executemany(
        "INSERT INTO entity_tags (entity_name, tag) VALUES (?,?)",
        _UNIT_TAGS,
    )
    conn.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, source_ref) VALUES (?,?,?,?)",
        _UNIT_EDGES,
    )
    conn.commit()
    conn.close()

    _open_conns: list[sqlite3.Connection] = []

    def _open():
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        _open_conns.append(c)
        return c

    # Hermetic DuckDB graph layer: routes that need DuckDB/Onager (e.g. the
    # /api/graph/stats structure block, Phase 2 of the graph_algos proposal)
    # must read THIS temp DB, not the production research.db. We patch the
    # underlying helpers.graph.query.connect (NOT app.get_graph_connection)
    # so the app's real connection machinery — lazy init, cached-error TTL,
    # locking — keeps running and its guard tests keep exercising real
    # semantics. query.connect() already isolates the .duckdb cache file as
    # a sibling of a non-production db_path.
    import helpers.graph.query as _q

    _real_q_connect = _q.connect
    _real_q_rebuild = _q.rebuild
    _real_q_fresh_rebuild = _q.fresh_rebuild

    def _mock_q_connect(*args, **kwargs):
        kwargs.pop("db_path", None)
        return _real_q_connect(db_path, *args, **kwargs)

    def _mock_rebuild(_db_path=db_path, *, fresh=False):
        # Hermetic rebuild: /api/graph/refresh must never touch the
        # PRODUCTION memory/graph.duckdb under this fixture. Since
        # rebuild() swaps a pid-tagged temp onto the resolved production
        # path (2026-08-26 deadlock fix), routing it through the mocked
        # connect alone is NOT enough — patch rebuild itself to rebuild
        # the FIXTURE duckdb in place (pre-swap behaviour).
        _q.clear_graph_cache()
        c = _real_q_connect(db_path, rebuild=True, fresh=fresh)
        c.close()

    saved = A.get_db_connection
    A.get_db_connection = _open  # ty: ignore[invalid-assignment]
    # Drop any graph connection cached by an earlier test (module global):
    # it would point at a different temp DB (or production).
    try:
        A._reset_graph_connection()
    except Exception:  # noqa: S110  # defensive
        pass
    _q.connect = _mock_q_connect  # ty: ignore[invalid-assignment]
    _q.rebuild = _mock_rebuild  # ty: ignore[invalid-assignment]
    _q.fresh_rebuild = _mock_rebuild  # ty: ignore[invalid-assignment]
    try:
        yield A.app.test_client()
    finally:
        _q.connect = _real_q_connect
        _q.rebuild = _real_q_rebuild
        _q.fresh_rebuild = _real_q_fresh_rebuild
        A.get_db_connection = saved
        try:
            A._reset_graph_connection()
        except Exception:  # noqa: S110  # defensive
            pass
        for c in _open_conns:
            try:
                c.close()
            except sqlite3.Error:
                pass


@pytest.fixture(autouse=True)
def _no_local_embedder():
    """Pin the local bge embedder OFF for every unit test (local_embeddings,
    2026-08-20): the suite must behave identically on machines with and
    without the models/ GGUF artifact + llama-cpp backend. Everything then
    takes the deterministic 64-dim pseudo path. Tests that exercise the real
    or faked local path opt in explicitly (test_local_embedder.py re-enables
    it; consumer tests monkeypatch available/embed_* behind the same
    interface).

    Deliberately NOT using monkeypatch: an autouse fixture that requests
    monkeypatch instantiates it BEFORE fixtures like unit_client, so at
    teardown unit_client's restore (helpers.graph.query.connect) runs first
    and monkeypatch's snapshot-restore then RE-APPLIES the mock — the leak
    behind the test_integration_graph_rebuild failures (found 2026-08-21).
    A plain save/restore in this fixture keeps the ordering untouched."""
    from helpers.core import local_embedder

    orig = local_embedder.available

    def _unavailable() -> bool:
        return False

    local_embedder.available = _unavailable  # ty: ignore[invalid-assignment]
    yield
    local_embedder.available = orig


@pytest.fixture(autouse=True)
def _embed_store_to_tmp(tmp_path):
    """Redirect the consolidated embed store (vec_search.EMBED_DB_PATH) into
    the per-test tmp dir (embed_store_consolidation, 2026-08). Without this,
    ANY file-backed connection reaching vec_search._attach_vec_db —
    rebuilders' tmp index DBs included — would attach and CREATE the live
    memory/embed_store.db during pytest. Same plain save/restore style as
    _no_local_embedder: an autouse monkeypatch would instantiate before
    unit_client and re-apply after its restore (the leak ordering documented
    there). The :memory:-main branch in _attach_vec_db bypasses this path
    entirely; a dedicated test pins that."""
    from helpers.core import vec_search

    orig = vec_search.EMBED_DB_PATH
    # Deliberately NO mkdir here: _attach_vec_db self-creates parents on
    # first real use, and tests that assert EXACT tmp_path contents
    # (e.g. test_exchange_search filename sanitisation) would see the stray
    # memory/ dir as pollution.
    vec_search.EMBED_DB_PATH = tmp_path / "memory" / "embed_store.db"
    yield
    vec_search.EMBED_DB_PATH = orig


@pytest.fixture(autouse=True)
def _embed_matrix_to_tmp(tmp_path):
    """Redirect the aligned f32 matrix (embed_matrix._MATRIX_PATH/_META_PATH)
    into the per-test tmp dir — same class as _embed_store_to_tmp above.

    rebuild_note_search._refresh_embed_matrix constructs EmbedMatrixStore()
    with the DEFAULT paths, so any in-process rebuild against a seeded DB
    (test_rebuild_note_search rebuilds, the maint-chain _shim_note_search)
    would clobber the real derived memory/embed_matrix.* — a 13×64
    pseudo-embedder stub landed there during qa 2026-09-04 (refresh()
    falls to a full build() on id mismatch, so a stub REPLACES a real
    matrix). Plain save/restore, not monkeypatch, for the same leak-
    ordering reason; EmbedMatrixStore's default_factory resolves the
    module globals at construction, so the assignment covers every
    store instantiated during the test."""
    from helpers.core import embed_matrix as emm

    orig = (emm._MATRIX_PATH, emm._META_PATH)
    emm._MATRIX_PATH = tmp_path / "memory" / "embed_matrix.f32"
    emm._META_PATH = tmp_path / "memory" / "embed_matrix.json"
    yield
    emm._MATRIX_PATH, emm._META_PATH = orig


@pytest.fixture(autouse=True)
def _clear_graph_query_cache():
    """Isolation: the process-global query result cache must not leak
    between tests.

    ``_with_generation_cache`` keys results on the SQLite ``db_meta``
    generation, but tests pin temp source DBs via ``query.connect(
    db_path=...)`` whose generation can EQUAL the live DB's (seeded
    copies). A result computed against a temp DB would then be served to
    a later test running against the live one (observed live:
    ``sector_members("Banking")`` returning a temp 2-row list while
    ``sector_members_with_market_cap`` returned the real 51). Clearing
    before every test is cheap and removes the whole class."""
    try:
        from helpers.graph import query as _gq

        _gq._query_cache_clear()
    except Exception:  # noqa: S110  # defensive: cache is best-effort
        pass
    yield


@pytest.fixture
def unit_client(tmp_path):
    """Flask test_client backed by a seeded SQLite graph DB (unit-isolated)."""
    with seeded_graph_sqlite_db(tmp_path) as c:
        yield c


@pytest.fixture
def bare_client():
    """Bare Flask test_client (no DB patching — for endpoints that never
    touch the research DB)."""
    import app as A  # lazy: avoid Flask-app startup at collection time

    with A.app.test_client() as c:
        yield c


# --------------------------------------------------------------------------- #
# xdist: per-worker graph cache (gate_xdist_phase2 Slice A, 2026-08-31)        #
# --------------------------------------------------------------------------- #
# query.connect() resolves the default cache path from the module global
# DUCKDB_PATH at call time. Under pytest-xdist each worker is a separate
# PROCESS, and a process holding an open RW connection excludes every other
# process's open — the live suite's module-scoped fixtures held the real
# memory/graph.duckdb for a module lifetime, so `pytest -m live -n 4`
# collided 67-72 times at setup ("Could not set lock on file ...graph.duckdb:
# Conflicting lock is held"). Redirecting the default to
# memory/graph.xdist-<worker>-<pid>.duckdb gives every worker its own cache
# built from the same research.db: N workers, N files, zero cross-process
# locks. The PID is part of the key because the advisory gate runs TWO
# concurrent pytest invocations (live-invariants AND integration), each
# numbering its workers gw0..gw3 — worker index alone would put both
# invocations' gw0 on the same file (measured 2026-08-31: 4/5 advisory
# runs FAILed before the PID was added). Tests that must exercise the REAL
# default path use the `real_graph_cache` marker; worker files (+
# .wal/.build.lock/rebuild temporaries) are removed at session finish.


def pytest_configure(config):
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    from helpers.graph import query as gq

    key = f"{worker}-{os.getpid()}"
    gq._REAL_DUCKDB_PATH = gq.DUCKDB_PATH
    gq.DUCKDB_PATH = gq.DUCKDB_PATH.with_name(
        f"{gq.DUCKDB_PATH.stem}.xdist-{key}{gq.DUCKDB_PATH.suffix}"
    )


@pytest.fixture(autouse=True)
def _real_graph_cache_optout(request):
    """`real_graph_cache`-marked tests use the production default path even
    under xdist (they assert real-cache semantics, not worker-copy ones)."""
    if os.environ.get("PYTEST_XDIST_WORKER") is None:
        yield
        return
    if request.node.get_closest_marker("real_graph_cache") is None:
        yield
        return
    from helpers.graph import query as gq

    redirected = gq.DUCKDB_PATH
    gq.DUCKDB_PATH = gq._REAL_DUCKDB_PATH
    yield
    gq.DUCKDB_PATH = redirected


def pytest_sessionfinish(session, exitstatus):
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    from helpers.graph import query as gq

    cache = gq.DUCKDB_PATH
    if f".xdist-{worker}-{os.getpid()}" not in cache.name:
        return  # name guard: never unlink anything but this worker's copy
    # cache file + .wal + .build.lock, plus the rebuild path's
    # <cache>.rebuild-<id>.tmp[.build.lock] temporaries (prefix glob)
    for leftover in cache.parent.glob(cache.name + "*"):
        leftover.unlink(missing_ok=True)
