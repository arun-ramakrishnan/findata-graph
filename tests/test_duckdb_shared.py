"""Unit tests for the quack wire helpers (helpers/core/duckdb_shared.py).

The parameter-inlining class is load-bearing for the whole wire lane: the
quack extension drops bound parameters on any table-reading query, so
`inline_params` is what makes prepared SQL work at all. These tests pin
the rendering rules without needing a live server.
"""

from __future__ import annotations

import json
import os

import pytest

from helpers.core import duckdb_shared as ds


class TestSqlLit:
    def test_none_becomes_null(self):
        assert ds.sql_lit(None) == "NULL"

    def test_booleans(self):
        assert ds.sql_lit(True) == "TRUE"
        assert ds.sql_lit(False) == "FALSE"

    def test_numbers_are_bare(self):
        assert ds.sql_lit(7) == "7"
        assert ds.sql_lit(-3) == "-3"
        assert ds.sql_lit(1.5) == "1.5"

    def test_nan_and_infinity_are_cast(self):
        assert ds.sql_lit(float("nan")) == "'NaN'::DOUBLE"
        assert ds.sql_lit(float("inf")) == "'Infinity'::DOUBLE"

    def test_strings_double_their_quotes(self):
        assert ds.sql_lit("abc") == "'abc'"
        assert ds.sql_lit("O'Brien") == "'O''Brien'"

    def test_bytes_are_hex_blobs(self):
        # one \xNN escape per byte (DuckDB blob-literal syntax)
        assert ds.sql_lit(b"\x00\xff") == "'\\x00\\xff'::BLOB"


class TestInlineParams:
    def test_no_params_returns_sql_unchanged(self):
        assert ds.inline_params("SELECT 1", []) == "SELECT 1"

    def test_positional_question_mark(self):
        assert ds.inline_params("SELECT $1", ["a"]) == "SELECT 'a'"

    def test_question_mark_placeholder(self):
        assert ds.inline_params("SELECT ? AS x", ["a"]) == "SELECT 'a' AS x"

    def test_one_based_indexed_placeholder(self):
        assert ds.inline_params("SELECT $1, $2", ["a", "b"]) == "SELECT 'a', 'b'"

    def test_named_placeholder_with_dict(self):
        assert ds.inline_params("SELECT $name", {"name": "x"}) == "SELECT 'x'"

    def test_numeric_key_in_dict(self):
        assert ds.inline_params("SELECT $1", {1: "x"}) == "SELECT 'x'"

    def test_quotes_in_values_are_escaped(self):
        assert ds.inline_params("SELECT $1", ["O'Brien"]) == "SELECT 'O''Brien'"

    def test_none_value_renders_null(self):
        assert ds.inline_params("SELECT $1", [None]) == "SELECT NULL"

    def test_missing_value_raises(self):
        with pytest.raises(ValueError, match="not enough parameters"):
            ds.inline_params("SELECT $1", [])

    def test_missing_named_value_raises(self):
        with pytest.raises(ValueError, match="no value for parameter"):
            ds.inline_params("SELECT $name", {"other": "x"})

    def test_too_few_sequence_values_raises(self):
        with pytest.raises(ValueError, match="not enough parameters"):
            ds.inline_params("SELECT $1, $2", ["a"])

    def test_named_placeholder_needs_a_dict(self):
        with pytest.raises(ValueError, match="needs a dict"):
            ds.inline_params("SELECT $name", ["a"])

    def test_placeholders_inside_string_literals_are_ignored(self):
        sql = "SELECT 'has ? inside' AS a, $1 AS b"
        assert ds.inline_params(sql, ["v"]) == "SELECT 'has ? inside' AS a, 'v' AS b"

    def test_placeholders_inside_comments_are_ignored(self):
        sql = "SELECT 1 -- ? not a param\n, $1 AS b"
        assert ds.inline_params(sql, ["v"]) == "SELECT 1 -- ? not a param\n, 'v' AS b"

    def test_repeated_placeholder_uses_its_value_each_time(self):
        assert ds.inline_params("SELECT $1, $1", ["v"]) == "SELECT 'v', 'v'"


class TestDiscovery:
    def test_missing_discovery_file_reads_as_empty(self, tmp_path):
        assert ds.load_discovery(tmp_path / "absent.json") == {}

    def test_discovery_without_pid_reads_as_empty(self, tmp_path):
        p = tmp_path / "disc.json"
        p.write_text(json.dumps({"alias": {"uri": "quack:localhost:1", "token": "t"}}))
        assert ds.load_discovery(p) == {}

    def test_dead_pid_entry_is_dropped(self, tmp_path):
        p = tmp_path / "disc.json"
        p.write_text(
            json.dumps(
                {
                    "alias": {
                        "uri": "quack:localhost:1",
                        "token": "t",
                        "pid": 999_999_999,
                        "path": str(tmp_path / "x.duckdb"),
                    }
                }
            )
        )
        assert ds.load_discovery(p) == {}

    def test_live_pid_entry_is_kept(self, tmp_path):
        p = tmp_path / "disc.json"
        p.write_text(
            json.dumps(
                {
                    "alias": {
                        "uri": "quack:localhost:1",
                        "token": "t",
                        "pid": os.getpid(),
                        "path": str(tmp_path / "x.duckdb"),
                    }
                }
            )
        )
        loaded = ds.load_discovery(p)
        assert loaded is not None
        assert set(loaded) == {"alias"}

    def test_is_served_is_false_without_discovery(self, tmp_path):
        assert ds.is_served(tmp_path / "x.duckdb", discovery=tmp_path / "absent.json") is False

    def test_assert_not_served_is_quiet_when_unserved(self, tmp_path):
        ds.assert_not_served(tmp_path / "x.duckdb", discovery=tmp_path / "absent.json")
