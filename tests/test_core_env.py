#!/usr/bin/env python3
"""Tests for helpers/core/env.py - memory/.env consolidation loader."""

from __future__ import annotations

import os

import pytest

from helpers.core import env as env_mod
from helpers.core.env import load_memory_env, require_env


class TestLoadMemoryEnv:
    def test_missing_file_returns_false(self, tmp_path):
        assert load_memory_env(tmp_path / "absent.env") is False

    def test_sets_vars_and_strips_quotes(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_SECRET_A", raising=False)
        f = tmp_path / ".env"
        f.write_text('TEST_SECRET_A="abc123"\n# comment\n')
        assert load_memory_env(f) is True
        assert os.environ["TEST_SECRET_A"] == "abc123"  # noqa: S105  # dummy fixture value, not a credential
        monkeypatch.delenv("TEST_SECRET_A")

    def test_existing_env_wins_unless_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_SECRET_B", "from-env")
        f = tmp_path / ".env"
        f.write_text("TEST_SECRET_B=from-file\n")
        load_memory_env(f)
        assert os.environ["TEST_SECRET_B"] == "from-env"  # noqa: S105  # dummy fixture value, not a credential
        load_memory_env(f, override=True)
        assert os.environ["TEST_SECRET_B"] == "from-file"  # noqa: S105  # dummy fixture value, not a credential

    def test_real_memory_env_loads(self, monkeypatch):
        # The repo's actual memory/.env must parse and contain both
        # consolidated keys (values never asserted/printed).
        names = ["FINNHUB_API_KEY", "PADDLE_API_KEY"]
        for name in names:
            monkeypatch.delenv(name, raising=False)
        if not env_mod.MEMORY_ENV.is_file():
            pytest.skip("memory/.env not present on this machine")
        assert load_memory_env() is True
        for name in names:
            assert len(os.environ[name]) >= 20
            monkeypatch.delenv(name)


class TestRequireEnv:
    def test_present(self, monkeypatch):
        monkeypatch.setenv("TEST_SECRET_C", "v")
        assert require_env("TEST_SECRET_C", what="thing") == "v"

    def test_absent_raises_with_house_error(self, monkeypatch):
        monkeypatch.delenv("TEST_SECRET_D", raising=False)
        with pytest.raises(RuntimeError, match=r"memory/\.env"):
            require_env("TEST_SECRET_D", what="thing")
