# Tests for helpers/misc/git_secret_scan.py
"""Pattern-detection, delta, and redaction tests (no git objects needed)."""
from __future__ import annotations

from helpers.misc.git_secret_scan import PATTERNS, _redact, compute_delta


def _ids() -> dict[str, object]:
    return {pid: pat for pid, pat in PATTERNS}


class TestPatterns:
    def test_aws_key(self):
        assert _ids()["aws-key"].search(b"aws_key = 'AKIAIOSFODNN7EXAMPLE'")

    def test_google_api_key_real(self):
        # Shape of the SEC-9 finding: 39-char high-entropy AIza... key.
        assert _ids()["google-api"].search(b'GOOGLE_API_KEY = "AIzaSyB1234567890abcdefghijklmnopqrstuv"')

    def test_placeholder_not_matched(self):
        # Placeholders and short values must never match.
        assert not _ids()["google-api"].search(b'GOOGLE_API_KEY = "YOUR_API_KEY"')
        assert not _ids()["google-api"].search(b'GOOGLE_API_KEY = ""')

    def test_github_pat(self):
        assert _ids()["github-pat"].search(b"ghp_" + b"a" * 36)

    def test_pem(self):
        assert _ids()["pem-private"].search(b"-----BEGIN RSA PRIVATE KEY-----")

    def test_generic_assign(self):
        assert _ids()["generic-assign"].search(b"api_key: 'abcdefghij0123456789abcd'")


class TestDelta:
    def test_delta_excludes_scanned(self):
        assert compute_delta({"a", "b", "c"}, {"a"}) == {"b", "c"}

    def test_delta_empty_when_caught_up(self):
        assert compute_delta({"a"}, {"a"}) == set()

    def test_reintroduced_blob_not_rescanned(self):
        # Blobs are immutable: a sha scanned once never needs rescanning even
        # if it becomes reachable again (e.g. stgit patch refs resurrect it).
        assert compute_delta({"x"}, {"x"}) == set()


class TestRedact:
    def test_long_tokens_redacted(self):
        out = _redact(b"key = 'AKIAIOSFODNN7EXAMPLE123'")
        assert "AKIAIOSFODNN7EXAMPLE123" not in out
        assert "<redacted>" in out

    def test_short_text_untouched(self):
        assert _redact(b"short text") == "short text"
