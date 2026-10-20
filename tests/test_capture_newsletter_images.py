"""Regression tests for helpers/pdf/capture_newsletter_images.py fetch() —
the bounded read (unbounded-read finding, run 2026-09-19-extractors-1).

The finding: r.read() with no byte argument buffered the ENTIRE https
response before the magic-byte gate, so a large body could exhaust
memory (demonstrated with a 64 MiB simulated transport at the A6
verification). The remediation caps the read at MAX_IMAGE_BYTES (+1
probe byte); an oversized body is reported skipped and never written.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.pdf import capture_newsletter_images as cap  # noqa: E402


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


def _fetch_with(monkeypatch, body, tmp_path):
    """Run fetch() against a simulated transport serving `body` (no network)."""
    payload = bytes(body)

    def fake_urlopen(req, timeout=None):
        return _FakeResp(payload)

    monkeypatch.setattr(cap, "urlopen", fake_urlopen)
    dest = tmp_path / "img.jpeg"
    result = cap.fetch("https://cdn.example/x.jpeg", dest, retries=1)
    return result, dest


def test_oversized_nonmagic_body_is_skipped_not_buffered_not_written(monkeypatch, tmp_path):
    # Shrink the ceiling so the test streams kilobytes, not 64 MiB.
    monkeypatch.setattr(cap, "MAX_IMAGE_BYTES", 1024)
    result, dest = _fetch_with(monkeypatch, b"\x41" * (1024 + 10), tmp_path)
    assert result == f"skipped oversized body (> {cap.MAX_IMAGE_BYTES} bytes)"
    assert not dest.exists()


def test_body_one_past_the_ceiling_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(cap, "MAX_IMAGE_BYTES", 1024)
    result, dest = _fetch_with(monkeypatch, b"\x41" * (1024 + 1), tmp_path)
    assert result.startswith("skipped oversized body")
    assert not dest.exists()


def test_body_exactly_at_the_ceiling_passes_the_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(cap, "MAX_IMAGE_BYTES", 1024)
    # A magic-bearing body AT the ceiling must still write: 4-byte JPEG
    # signature + 1020 padding = exactly MAX bytes.
    body = b"\xff\xd8\xff\xe0" + b"\x00" * 1020
    result, dest = _fetch_with(monkeypatch, body, tmp_path)
    assert result is True
    assert dest.exists() and dest.read_bytes() == body


def test_ceiling_does_not_downgrade_a_small_magic_body(monkeypatch, tmp_path):
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60  # PNG signature + padding
    result, dest = _fetch_with(monkeypatch, body, tmp_path)
    assert result is True
    assert dest.exists()
