"""Tests for helpers/maintenance/tag_international_entities.py."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from helpers.core.duckdb_shared import memory_cap_config  # noqa: E402
from helpers.maintenance import tag_international_entities as tie  # noqa: E402
from helpers.maintenance.tag_international_entities import (  # noqa: E402
    INTERNATIONAL_TAG,
    already_tagged,
    apply_tags,
    collect_international_candidates,
)

_COMPANIES = [
    ("Apple", "AAPL"),
    ("Samsung Electronics", "005930.KS"),
    ("Tencent", "0700.HK"),
    ("Reliance", "RELIANCE.NS"),
    ("Tata Motors", "TATAMOTORS.NS"),
    ("Infosys", "INFY.BO"),
    ("NoTicker", None),
]
_TAG_ROWS_SQL = "SELECT entity_name, tag FROM entity_tags ORDER BY entity_name, tag"


def _seed(con) -> None:
    con.execute("CREATE TABLE entities (name VARCHAR, ticker VARCHAR)")
    con.execute("CREATE TABLE entity_tags (entity_name VARCHAR, tag VARCHAR)")
    con.executemany("INSERT INTO entities VALUES (?, ?)", _COMPANIES)
    con.execute(
        "INSERT INTO entity_tags VALUES ('Apple', 'geography/usa'), "
        "('Tencent', 'geography/hong_kong'), "
        "('Reliance', 'geography/india')"
    )


def _make_con():
    con = duckdb.connect(":memory:", **memory_cap_config())
    _seed(con)
    return con


def test_candidates_exclude_indian_and_null_tickers():
    con = _make_con()
    assert collect_international_candidates(con) == ["Apple", "Samsung Electronics", "Tencent"]
    con.close()


def test_apply_is_idempotent_and_preserves_existing_tags():
    con = _make_con()
    names = collect_international_candidates(con)
    assert apply_tags(con, names) == 3  # all three get INTERNATIONAL_TAG
    # reapply → 0 new
    assert apply_tags(con, names) == 0
    rows = con.execute(
        "SELECT entity_name, tag FROM entity_tags ORDER BY entity_name, tag"
    ).fetchall()
    tencent = [t for n, t in rows if n == "Tencent"]
    assert INTERNATIONAL_TAG in tencent
    assert "geography/hong_kong" in tencent  # additive, old country tag kept
    con.close()


def test_empty_candidates_apply_noop():
    con = _make_con()
    assert apply_tags(con, []) == 0
    con.close()


# ---------------------------------------------------------------------------
# Write shape (proposal sync_tags_duckdb_setbased P4)
# ---------------------------------------------------------------------------


class _CountingDuckDBConn:
    """Delegate to a raw DuckDB connection but count write statements.

    ``raw`` is what ``_is_duckdb_conn`` ducks on — the same shape the repo's
    own connection proxies expose (mirrors tests/test_sync_tags.py).
    """

    def __init__(self, raw) -> None:
        self.raw = raw
        self.statements: list[str] = []
        self.batches: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql)
        return self.raw.execute(sql, *args, **kwargs)

    def executemany(self, sql, *args, **kwargs):
        self.batches.append(sql)
        return self.raw.executemany(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.raw, name)


class _CountingSQLite(sqlite3.Connection):
    """sqlite3.Connection counting executemany batches (the SQLite path)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.batches: list[str] = []

    def executemany(self, sql, parameters, /):
        self.batches.append(sql)
        return super().executemany(sql, parameters)


def _make_sqlite_con():
    con = sqlite3.connect(":memory:", factory=_CountingSQLite)
    _seed(con)
    return con


def test_duckdb_apply_is_one_insert_select_statement():
    """DuckDB write shape: the tag insert used to be one executemany round
    trip per row (≈6 ms/row — 150 rows ≈ 0.9 s on a copy of the live
    store). It must be exactly one INSERT … SELECT DISTINCT … WHERE NOT
    EXISTS and no executemany; a rerun with nothing missing writes nothing."""
    con = _CountingDuckDBConn(_make_con())
    names = collect_international_candidates(con)
    assert apply_tags(con, names) == 3
    inserts = [s for s in con.statements if s.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1
    assert inserts[0].count("?") == 3
    assert "SELECT DISTINCT" in inserts[0] and "NOT EXISTS" in inserts[0]
    assert con.batches == []
    assert already_tagged(con, names) == 3
    assert already_tagged(con, []) == 0
    n_before = len(con.statements)
    assert apply_tags(con, names) == 0
    assert len(con.statements) == n_before + 1  # only the tagged-among SELECT
    assert apply_tags(con, []) == 0
    assert len(con.statements) == n_before + 1
    assert con.execute(_TAG_ROWS_SQL).fetchall() == [
        ("Apple", "geography/international"),
        ("Apple", "geography/usa"),
        ("Reliance", "geography/india"),
        ("Samsung Electronics", "geography/international"),
        ("Tencent", "geography/hong_kong"),
        ("Tencent", "geography/international"),
    ]
    con.close()


def test_sqlite_path_works_with_executemany():
    """The SQLite rollback store (what the house door hands out under
    FINDATA_RESEARCH_ENGINE=sqlite) works end to end: until P4 the
    tagged-among lookup bound its list with ``unnest(?)`` — DuckDB-only, so
    this raised ``no such function: unnest``. The write stays one
    executemany batch of VALUES rows (``unnest(?)`` is still DuckDB-only)."""
    con = _make_sqlite_con()
    names = collect_international_candidates(con)
    assert already_tagged(con, names) == 0
    n_before = len(con.batches)
    assert apply_tags(con, names) == 3
    assert len(con.batches) == n_before + 1
    assert "VALUES (?, ?)" in con.batches[-1]
    assert already_tagged(con, names) == 3
    assert apply_tags(con, names) == 0
    assert len(con.batches) == n_before + 1
    rows = con.execute(_TAG_ROWS_SQL).fetchall()
    assert ("Tencent", "geography/hong_kong") in rows  # additive
    assert ("Tencent", INTERNATIONAL_TAG) in rows
    con.close()


def test_duckdb_and_sqlite_paths_agree():
    """Both engines land the same tag rows (additive, idempotent)."""
    duck, lite = _make_con(), _make_sqlite_con()
    for con in (duck, lite):
        names = collect_international_candidates(con)
        assert [apply_tags(con, names), apply_tags(con, names)] == [3, 0]
    assert duck.execute(_TAG_ROWS_SQL).fetchall() == lite.execute(_TAG_ROWS_SQL).fetchall()
    duck.close()
    lite.close()


# ---------------------------------------------------------------------------
# CLI: house door instead of a raw connect on the retired SQLite-era path
# ---------------------------------------------------------------------------

_LEGACY = Path("memory") / "research.db"


@pytest.fixture
def hermetic_cli_store(tmp_path, monkeypatch):
    """A research store OUTSIDE the repo, selected the way the house door
    honours for pathless connects; cwd is a scratch tree whose ``memory/``
    holds no legacy ``research.db``."""
    store = tmp_path / "research.duckdb"
    con = duckdb.connect(str(store), **memory_cap_config())
    _seed(con)
    con.close()
    monkeypatch.setenv("FINDATA_RESEARCH_DB_PATH", str(store))
    cwd = tmp_path / "cwd"
    (cwd / "memory").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    return store


def _tagged(store) -> int:
    con = duckdb.connect(str(store), read_only=True)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM entity_tags WHERE tag = ?", [INTERNATIONAL_TAG]
        ).fetchone()[0]
    finally:
        con.close()


def test_cli_default_dry_run_reads_the_canonical_store(hermetic_cli_store, capsys):
    """Before P4 the default ``--db`` was the retired ``memory/research.db``
    and a raw ``duckdb.connect`` on it CREATED an empty store there and
    died with ``Catalog Error: Table with name entities does not exist``.
    A default dry-run must read the selected store, write nothing, and
    never create the legacy file."""
    assert Path.cwd() != REPO_ROOT
    before = hermetic_cli_store.stat().st_mtime_ns
    assert tie._cli([]) == 0
    out = capsys.readouterr().out
    assert "international candidates: 3 (already tagged 0, missing 3)" in out
    assert "dry-run: 3 would be tagged (rerun with --apply)" in out
    assert not (Path.cwd() / _LEGACY).exists()
    assert hermetic_cli_store.stat().st_mtime_ns == before
    assert _tagged(hermetic_cli_store) == 0


def test_cli_apply_writes_through_the_house_door(hermetic_cli_store, capsys):
    assert tie._cli(["--apply"]) == 0
    assert "applied geography/international to 3 entit(ies)" in capsys.readouterr().out
    assert tie._cli(["--apply"]) == 0
    assert "applied geography/international to 0 entit(ies)" in capsys.readouterr().out
    assert not (Path.cwd() / _LEGACY).exists()
    assert _tagged(hermetic_cli_store) == 3


def test_cli_relative_db_is_anchored_at_the_project_root(hermetic_cli_store, monkeypatch, capsys):
    """An explicit relative ``--db`` means repo-relative (never cwd-relative)
    and goes through ``helpers.core.db.connect``; read-only unless --apply."""
    seen: dict[str, object] = {}

    def fake_connect(db_path, **kwargs):
        seen["path"] = db_path
        seen["kwargs"] = kwargs
        return duckdb.connect(str(hermetic_cli_store), **memory_cap_config())

    monkeypatch.setattr(tie.db, "connect", fake_connect)
    assert tie._cli(["--db", "memory/research.duckdb"]) == 0
    assert seen["path"] == REPO_ROOT / "memory" / "research.duckdb"
    assert seen["kwargs"] == {"read_only": True}
    assert tie._cli(["--db", "memory/research.duckdb", "--apply"]) == 0
    assert seen["path"] == REPO_ROOT / "memory" / "research.duckdb"
    assert seen["kwargs"] == {"read_only": False}
    assert not (Path.cwd() / _LEGACY).exists()
    capsys.readouterr()
