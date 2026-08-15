"""
Tier 1 regression tests for SQLite FK cascade behaviour.

These pin the contract that `helpers.core.db.connect()` enables FK enforcement
and that the `entity_tags` and `relations` tables declare both
ON DELETE CASCADE and ON UPDATE CASCADE. Without these, deletes and renames
silently leave orphaned rows (the bug that prompted this test file).

These tests construct a minimal DB matching the production schema, then
exercise delete and rename flows to prove the cascades fire.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helpers"))

from core.db import connect  # noqa: E402
from datetime import UTC


PRODUCTION_SCHEMA = """
CREATE TABLE entities (
    name TEXT PRIMARY KEY NOT NULL,
    entity_type TEXT NOT NULL,
    file_path TEXT,
    normalized_name TEXT,
    sector_classification TEXT,
    ticker TEXT
);
CREATE TABLE relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    UNIQUE(source, target, relation_type),
    FOREIGN KEY (source) REFERENCES entities(name)
        ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (target) REFERENCES entities(name)
        ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE TABLE entity_tags (
    entity_name TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (entity_name, tag),
    FOREIGN KEY (entity_name) REFERENCES entities(name)
        ON DELETE CASCADE ON UPDATE CASCADE
);
"""


@pytest.fixture
def cascade_db(tmp_path: Path) -> Path:
    """A DB with the production schema + one company + one sector + tag/relation."""
    db_path = tmp_path / "cascade.db"
    # Use the helper under test (which sets PRAGMA foreign_keys = ON).
    conn = connect(db_path, row_factory=None)
    try:
        conn.executescript(PRODUCTION_SCHEMA)
        conn.execute(
            "INSERT INTO entities (name, entity_type, normalized_name) "
            "VALUES ('Acme Co', 'company', 'Acme_Co')"
        )
        conn.execute(
            "INSERT INTO entities (name, entity_type, normalized_name) "
            "VALUES ('Industrials', 'sector', 'Industrials')"
        )
        conn.execute(
            "INSERT INTO entity_tags (entity_name, tag) VALUES "
            "('Acme Co', 'entity_type/company'), "
            "('Acme Co', 'sector/industrials')"
        )
        conn.execute(
            "INSERT INTO relations (source, target, relation_type) VALUES "
            "('Acme Co', 'Industrials', 'part_of'), "
            "('Industrials', 'Acme Co', 'has_company')"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


# --------------------------------------------------------------------------- #
# connect() enables PRAGMA foreign_keys                                        #
# --------------------------------------------------------------------------- #
def test_connect_enables_foreign_keys(cascade_db):
    """connect() must enable FK enforcement so cascades fire."""
    conn = connect(cascade_db, row_factory=None)
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1, "connect() failed to enable PRAGMA foreign_keys = ON"
    finally:
        conn.close()


def test_connect_enables_wal(cascade_db):
    """connect() must use WAL journal mode (production layout)."""
    conn = connect(cascade_db, row_factory=None)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_connect_sets_busy_timeout(cascade_db):
    """Bundle U1: connect() must set PRAGMA busy_timeout so concurrent
    writers wait instead of failing immediately with SQLITE_BUSY. SQLite's
    default is 0 (fail-fast); the project has concurrent-writer scenarios
    (parse_newsletter parent + sync_tags subprocess)."""
    conn = connect(cascade_db, row_factory=None)
    try:
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout >= 5000, (
            f"busy_timeout is {timeout}ms; expected >=5000ms so concurrent "
            f"writers queue instead of crashing with SQLITE_BUSY"
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Bundle T1: UTC timestamp helper                                             #
# --------------------------------------------------------------------------- #
def test_utc_now_matches_current_timestamp_shape():
    """T1: utc_now() must produce 'YYYY-MM-DD HH:MM:SS' — the same shape
    SQLite's CURRENT_TIMESTAMP uses. This is what makes the staleness
    comparison (entities.last_updated vs graph_analytics.computed_at)
    reliable: both columns hold the same-format UTC string, so TEXT
    comparison sorts correctly."""
    from helpers.core.db import utc_now
    from datetime import datetime

    ts = utc_now()
    # Must parse as the expected format (raises ValueError if malformed)
    datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    # Must be 19 chars: 'YYYY-MM-DD HH:MM:SS'
    assert len(ts) == 19, f"utc_now() returned {ts!r} (len {len(ts)}, expected 19)"

    # Must be close to UTC NOW (within 60s — allows for test scheduling).
    # Parse as UTC (not local) because utc_now() returns UTC.
    parsed = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    now_utc = datetime.now(UTC)
    assert abs((now_utc - parsed).total_seconds()) < 60, (
        f"utc_now() returned {ts}, which is >60s from UTC now"
    )


def test_utc_now_sorts_consistently_with_current_timestamp():
    """T1: a utc_now() value and a CURRENT_TIMESTAMP value from the same
    instant must sort consistently as TEXT — this is the core T1 contract
    that makes the staleness comparison reliable."""
    import sqlite3
    from helpers.core.db import utc_now

    con = sqlite3.connect(":memory:")
    try:
        ct = con.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
        un = utc_now()
        # Both should be 19-char UTC timestamps; their TEXT sort order must
        # match their temporal order. Since they're within seconds of each
        # other, either may be slightly larger — the test is that they're
        # within the same magnitude (not one date-only and one full-datetime).
        assert len(ct) == len(un) == 19
        # They should be within 2 seconds of each other (temporal proximity).
        from datetime import datetime
        ct_parsed = datetime.strptime(ct, "%Y-%m-%d %H:%M:%S")
        un_parsed = datetime.strptime(un, "%Y-%m-%d %H:%M:%S")
        assert abs((ct_parsed - un_parsed).total_seconds()) < 5
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# ON DELETE CASCADE                                                            #
# --------------------------------------------------------------------------- #
def test_delete_entity_cascades_to_relations_and_tags(cascade_db):
    """Deleting a company must remove its relations + tags automatically."""
    conn = connect(cascade_db, row_factory=None)
    try:
        # Sanity: pre-conditions
        assert conn.execute(
            "SELECT COUNT(*) FROM relations WHERE source='Acme Co' OR target='Acme Co'"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM entity_tags WHERE entity_name='Acme Co'"
        ).fetchone()[0] == 2

        conn.execute("DELETE FROM entities WHERE name='Acme Co'")
        conn.commit()

        # Both relations rows (one source, one target) should be gone.
        assert conn.execute(
            "SELECT COUNT(*) FROM relations WHERE source='Acme Co' OR target='Acme Co'"
        ).fetchone()[0] == 0
        # Both tag rows should be gone.
        assert conn.execute(
            "SELECT COUNT(*) FROM entity_tags WHERE entity_name='Acme Co'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# ON UPDATE CASCADE (rename)                                                   #
# --------------------------------------------------------------------------- #
def test_rename_entity_cascades_to_relations_and_tags(cascade_db):
    """Renaming a company must update relations.source/target AND tags.entity_name."""
    conn = connect(cascade_db, row_factory=None)
    try:
        conn.execute("UPDATE entities SET name='Acme Co Ltd' WHERE name='Acme Co'")
        conn.commit()

        # relations should now reference the new name in both directions.
        assert conn.execute(
            "SELECT COUNT(*) FROM relations WHERE source='Acme Co Ltd'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM relations WHERE target='Acme Co Ltd'"
        ).fetchone()[0] == 1
        # Old name must be entirely gone from relations.
        assert conn.execute(
            "SELECT COUNT(*) FROM relations WHERE source='Acme Co' OR target='Acme Co'"
        ).fetchone()[0] == 0
        # entity_tags must follow too.
        assert conn.execute(
            "SELECT COUNT(*) FROM entity_tags WHERE entity_name='Acme Co Ltd'"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM entity_tags WHERE entity_name='Acme Co'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_rename_sector_cascades_to_relations(cascade_db):
    """Renaming the target sector must update relations.target (and source for has_company)."""
    conn = connect(cascade_db, row_factory=None)
    try:
        conn.execute("UPDATE entities SET name='Indy' WHERE name='Industrials'")
        conn.commit()

        assert conn.execute(
            "SELECT COUNT(*) FROM relations WHERE target='Indy'"
        ).fetchone()[0] == 1   # part_of row
        assert conn.execute(
            "SELECT COUNT(*) FROM relations WHERE source='Indy'"
        ).fetchone()[0] == 1   # has_company row
        assert conn.execute(
            "SELECT COUNT(*) FROM relations WHERE "
            "source='Industrials' OR target='Industrials'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Without PRAGMA foreign_keys = ON, cascades must NOT fire (negative test)     #
# --------------------------------------------------------------------------- #
def test_raw_connect_silently_disables_cascades(cascade_db):
    """A bare sqlite3.connect() (without PRAGMA) does NOT enable cascades.

    This pins the rationale for helpers.core.db.connect(): without it, deletes
    leave orphans silently. This is exactly the bug class we want to prevent
    by forcing every caller through the shared helper.
    """
    # Raw connect — no pragma.
    conn = sqlite3.connect(str(cascade_db))
    try:
        conn.execute("DELETE FROM entities WHERE name='Acme Co'")
        conn.commit()

        # FKs off, so the relation rows are orphaned rather than cascaded.
        # This is the bug we guard against by using connect().
        orphaned = conn.execute(
            "SELECT COUNT(*) FROM relations WHERE source='Acme Co' OR target='Acme Co'"
        ).fetchone()[0]
        assert orphaned == 2, (
            "expected 2 orphaned relation rows when FKs are off; "
            "did SQLite change its default?"
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# FK enforcement actually blocks bad inserts when enabled                      #
# --------------------------------------------------------------------------- #
def test_fk_blocks_insert_with_nonexistent_parent(cascade_db):
    """With FKs on, inserting a relation row pointing at a missing entity fails."""
    conn = connect(cascade_db, row_factory=None)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO relations (source, target, relation_type) "
                "VALUES ('Acme Co', 'Nonexistent Sector', 'part_of')"
            )
    finally:
        conn.close()


def test_fk_blocks_insert_into_tags_for_missing_entity(cascade_db):
    """With FKs on, inserting a tag for a missing entity fails."""
    conn = connect(cascade_db, row_factory=None)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO entity_tags (entity_name, tag) "
                "VALUES ('Ghost Co', 'entity_type/company')"
            )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Bundle F11: rename_entity.py docstring accuracy                            #
# --------------------------------------------------------------------------- #
def test_rename_entity_docstring_describes_cascade_not_fk_disable():
    """F11: the rename_entity.py module docstring must describe the ACTUAL
    safety mechanism — ``PRAGMA foreign_keys = ON`` + ``ON UPDATE CASCADE`` —
    not the stale claim that it "temporarily disables FKs inside a single
    transaction". connect() enables FKs globally and the rename relies on the
    cascade; disabling FKs would defeat the cascade. A future editor who
    trusts a "disables FKs" docstring could remove the CASCADE and break
    graph consistency silently.
    """
    from maintenance import rename_entity

    doc = (rename_entity.__doc__ or "").lower()
    # The mechanism that's actually in use.
    assert "cascade" in doc, "docstring must mention ON UPDATE CASCADE"
    # The stale, incorrect CLAIM (that the helper disables FKs as its
    # mechanism) must be gone. We forbid the verb phrase "disables fk" /
    # "temporarily disables", not the word "disabled" used to clarify that
    # FKs are NOT disabled (the correct description).
    assert "disables fk" not in doc, (
        "docstring must not claim FKs are disabled (the rename relies on "
        "PRAGMA foreign_keys = ON + ON UPDATE CASCADE, not FK toggling)"
    )
    assert "temporarily disables" not in doc
    # And FKs must be confirmed ON (the precondition for the cascade).
    assert "foreign_keys = on" in doc or "foreign keys = on" in doc, (
        "docstring must state PRAGMA foreign_keys = ON is required"
    )
