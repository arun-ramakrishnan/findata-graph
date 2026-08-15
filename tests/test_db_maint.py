"""Unit tests for helpers/maintenance/db_maint.py — pure helpers + DBMaintainer."""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from helpers.maintenance.db_maint import (  # noqa: E402
    _fmt_bytes,
    _pragma_ident,
    _compute_root,
    _print_report,
    DBMaintainer,
)


# ---------------------------------------------------------------------------
# _fmt_bytes — pure function
# ---------------------------------------------------------------------------
class TestFmtBytes:
    def test_bytes(self):
        assert _fmt_bytes(512) == "512 B"

    def test_kb(self):
        assert _fmt_bytes(2048) == "2.0 KB"

    def test_mb(self):
        assert _fmt_bytes(5 * 1024 * 1024) == "5.0 MB"

    def test_gb(self):
        assert _fmt_bytes(2 * 1024 * 1024 * 1024) == "2.0 GB"

    def test_zero(self):
        assert _fmt_bytes(0) == "0 B"


# ---------------------------------------------------------------------------
# _pragma_ident — identifier validation
# ---------------------------------------------------------------------------
class TestPragmaIdent:
    def test_valid_identifier(self):
        assert _pragma_ident("entities") == "entities"

    def test_valid_with_underscore(self):
        assert _pragma_ident("my_table") == "my_table"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _pragma_ident("")

    def test_special_chars_raises(self):
        with pytest.raises(ValueError):
            _pragma_ident("table; DROP")

    def test_sql_injection_raises(self):
        with pytest.raises(ValueError):
            _pragma_ident("'; DROP TABLE--")


# ---------------------------------------------------------------------------
# _compute_root
# ---------------------------------------------------------------------------
class TestComputeRoot:
    def test_returns_path(self):
        root = _compute_root()
        assert isinstance(root, Path)
        assert root.name == "pdf-ocr-obsidian"


# ---------------------------------------------------------------------------
# DBMaintainer.settings — with a real SQLite file
# ---------------------------------------------------------------------------
class TestDBMaintainerSettings:
    def test_returns_dict(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE x (a)")
        conn.commit()
        maint = DBMaintainer(db_path, backup_path=tmp_path / "backup.db")
        result = maint.settings(conn)
        conn.close()
        assert isinstance(result, dict)
        assert "journal_mode" in result
        assert "page_size" in result

    def test_synchronous_decoded(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE x (a)")
        conn.commit()
        maint = DBMaintainer(db_path, backup_path=tmp_path / "backup.db")
        result = maint.settings(conn)
        conn.close()
        # synchronous should be a string label, not a raw int
        assert isinstance(result["synchronous"], str)


# ---------------------------------------------------------------------------
# DBMaintainer.metrics — with a real SQLite file
# ---------------------------------------------------------------------------
class TestDBMaintainerMetrics:
    def test_returns_dict(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE x (a)")
        conn.execute("INSERT INTO x VALUES (1)")
        conn.commit()
        maint = DBMaintainer(db_path, backup_path=tmp_path / "backup.db")
        result = maint.metrics(conn)
        conn.close()
        assert "file_size" in result
        assert "pages" in result
        assert "freelist" in result
        assert "wasted_pct" in result
        assert result["file_size"] > 0

    def test_empty_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE x (a)")
        conn.commit()
        maint = DBMaintainer(db_path, backup_path=tmp_path / "backup.db")
        result = maint.metrics(conn)
        conn.close()
        assert result["pages"] > 0
        assert result["wasted_pct"] == 0.0


# ---------------------------------------------------------------------------
# DBMaintainer.stat_staleness — with a real SQLite file
# ---------------------------------------------------------------------------
class TestDBMaintainerStatStaleness:
    def test_no_stats(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE x (a)")
        conn.commit()
        maint = DBMaintainer(db_path, backup_path=tmp_path / "backup.db")
        result = maint.stat_staleness(conn)
        conn.close()
        # No ANALYZE run yet → empty dict
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# DBMaintainer.index_report — with a real SQLite file
# ---------------------------------------------------------------------------
class TestDBMaintainerIndexReport:
    def test_returns_list(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE x (a TEXT PRIMARY KEY, b TEXT)")
        conn.execute("CREATE INDEX idx_b ON x(b)")
        conn.commit()
        maint = DBMaintainer(db_path, backup_path=tmp_path / "backup.db")
        result = maint.index_report(conn)
        conn.close()
        assert isinstance(result, list)
        assert len(result) >= 1
        # Find table x
        x_entry = next(e for e in result if e["table"] == "x")
        assert x_entry["row_count"] == 0

    def test_with_rows(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE entities (name TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO entities VALUES ('A')")
        conn.execute("INSERT INTO entities VALUES ('B')")
        conn.commit()
        maint = DBMaintainer(db_path, backup_path=tmp_path / "backup.db")
        result = maint.index_report(conn)
        conn.close()
        ent = next(e for e in result if e["table"] == "entities")
        assert ent["row_count"] == 2


# ---------------------------------------------------------------------------
# _print_report — smoke test with a mock report dict
# ---------------------------------------------------------------------------
class TestPrintReport:
    def _make_mock_report(self):
        return {
            "settings": {
                "journal_mode": "wal",
                "synchronous": "NORMAL",
                "auto_vacuum": "NONE",
                "cache_size": -8000,
                "page_size": 4096,
                "encoding": "UTF-8",
            },
            "before": {
                "file_size": 1024 * 1024,
                "pages": 256,
                "freelist": 10,
                "wasted_pct": 3.9,
                "wasted_bytes": 40960,
            },
            "stat_staleness_before": {},
            "backup": {
                "path": "/tmp/backup.db",  # noqa: S108  # test-only throwaway path/fixture
                "size": 1024 * 1024,
            },
            "after": {
                "file_size": 900000,
                "pages": 220,
                "freelist": 0,
                "wasted_pct": 0.0,
                "wasted_bytes": 0,
            },
            "stat_staleness_after": {},
            "indexes": [
                {
                    "table": "entities",
                    "row_count": 100,
                    "indexes": [
                        {
                            "name": "pk",
                            "columns": ["name"],
                            "collations": ["BINARY"],
                            "unique": True,
                            "origin": "pk",
                            "partial": False,
                            "redundant_with": None,
                            "empty_table": False,
                        },
                    ],
                },
            ],
            "integrity_check": "ok",
            "foreign_key_violations": [],
        }

    def test_prints_without_error(self, capsys):
        report = self._make_mock_report()
        _print_report(report)
        captured = capsys.readouterr()
        assert "SETTINGS" in captured.out
        assert "BEFORE" in captured.out
        assert "AFTER" in captured.out
        assert "INDEXES" in captured.out
        assert "INTEGRITY" in captured.out

    def test_prints_redundant_flag(self, capsys):
        report = self._make_mock_report()
        report["indexes"][0]["indexes"][0]["redundant_with"] = "other_idx"
        _print_report(report)
        captured = capsys.readouterr()
        assert "REDUNDANT" in captured.out

    def test_prints_empty_table_flag(self, capsys):
        report = self._make_mock_report()
        report["indexes"][0]["row_count"] = 0
        _print_report(report)
        captured = capsys.readouterr()
        assert "EMPTY" in captured.out
