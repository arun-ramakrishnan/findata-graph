"""Pins for helpers/core/parallelism.default_workers — the one-core-free
fan-out default (2026-11-25 operator steer: "reduce it to 2/3 and leave
one free for other tasks")."""

from __future__ import annotations

from typing import Any

import pytest

from helpers.core.parallelism import default_workers


class TestDefaultWorkers:
    def test_leaves_one_core_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("helpers.core.parallelism.os.cpu_count", lambda: 4)
        assert default_workers() == 3

    def test_floor_of_one_on_single_core(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("helpers.core.parallelism.os.cpu_count", lambda: 1)
        assert default_workers() == 1

    def test_explicit_cap_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("helpers.core.parallelism.os.cpu_count", lambda: 4)
        assert default_workers(cap=2) == 2

    def test_env_cap_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("helpers.core.parallelism.os.cpu_count", lambda: 4)
        monkeypatch.setenv("FINDATA_WORKER_CAP", "2")
        assert default_workers() == 2

    def test_env_cap_beats_explicit_cap_when_tighter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("helpers.core.parallelism.os.cpu_count", lambda: 16)
        monkeypatch.setenv("FINDATA_WORKER_CAP", "3")
        assert default_workers(cap=8) == 3

    def test_garbage_env_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("helpers.core.parallelism.os.cpu_count", lambda: 4)
        monkeypatch.setenv("FINDATA_WORKER_CAP", "lots")
        assert default_workers() == 3

    def test_floor_survives_caps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("helpers.core.parallelism.os.cpu_count", lambda: 4)
        monkeypatch.setenv("FINDATA_WORKER_CAP", "0")
        assert default_workers(floor=2, cap=1) == 2

    def test_returns_int(self) -> None:
        result: Any = default_workers()
        assert isinstance(result, int)
        assert result >= 1
