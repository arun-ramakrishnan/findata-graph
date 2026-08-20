"""
Tests for helpers/core/local_embedder.py — the single owner of the BGE
query/document asymmetry (local_embeddings proposal, 2026-08-20).

Two tiers:

- Hermetic (always run): availability gate behaviour, sha pin, empty-input
  guard, constants contract. The conftest autouse pin keeps the module OFF
  for the rest of the suite; here we exercise both sides explicitly.
- Real-model (skipped when llama-cpp-python or the pinned GGUF is absent):
  dims, L2 norm, determinism, the prefix-asymmetry canary, semantic sanity
  on findata vocabulary, and batch/per-doc parity.

The module owns ONE correctness rule — queries carry the BGE retrieval
prefix, documents must not — so the canaries here fail loudly if anyone
bypasses embed_query/embed_document at a call site.
"""

import hashlib
import importlib.util
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (REPO_ROOT, REPO_ROOT / "helpers"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from helpers.core import local_embedder as LE  # noqa: E402

# Captured at import (collection) time, BEFORE any test's monkeypatch of
# module attributes: the genuine available() for tests that need the real
# backend behind the conftest pin.
real_available = LE.available

_BACKEND = importlib.util.find_spec("llama_cpp") is not None
_MODEL_FILE = LE.MODEL_PATH.is_file()
needs_model = pytest.mark.skipif(
    not (_BACKEND and _MODEL_FILE),
    reason="llama-cpp-python + pinned GGUF not present "
           "(setup: local_embedder module docstring)",
)


@pytest.fixture
def real_backend(monkeypatch):
    """Re-enable the genuine availability check for real-model tests."""
    monkeypatch.setattr(LE, "available", real_available)
    assert real_available(), "skipif should have prevented this test"


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b)))


# --------------------------------------------------------------------------- #
# Hermetic: constants + gates                                                 #
# --------------------------------------------------------------------------- #
class TestConstantsContract:
    def test_dims_384_matches_live_table(self):
        # 384 is what makes the swap schema-transparent with company_embeddings.
        assert LE.DIM == 384

    def test_query_prefix_nonempty(self):
        assert isinstance(LE.QUERY_PREFIX, str) and LE.QUERY_PREFIX.strip()

    def test_model_id_label(self):
        assert LE.MODEL_ID == "bge-small-en-v1.5"

    def test_model_file_pinned_by_sha(self):
        # The artifact is gitignored; the pin is its provenance record.
        assert len(LE.MODEL_SHA256) == 64


class TestGates:
    def test_embed_refuses_when_unavailable(self):
        # conftest pins available() -> False: embed_* must raise, not return
        # junk vectors.
        with pytest.raises(RuntimeError, match="unavailable"):
            LE.embed_document("text")
        with pytest.raises(RuntimeError, match="unavailable"):
            LE.embed_query("text")

    def test_empty_document_raises_before_model_load(self):
        with pytest.raises(ValueError, match="empty"):
            LE.embed_document("   ")

    def test_available_false_on_sha_mismatch(self, monkeypatch, tmp_path):
        """A model file that doesn't match the pin must never load (wrong or
        tampered artifact), and available() must swallow the error."""
        fake = tmp_path / "fake.gguf"
        fake.write_bytes(b"definitely not a gguf model")
        monkeypatch.setattr(LE, "MODEL_PATH", fake)
        monkeypatch.setattr(LE, "_verified", False)
        assert real_available() is False
        # And the correct pin against the same file flips it to True
        # (hermetic happy path; no load happens inside available()).
        monkeypatch.setattr(
            LE, "MODEL_SHA256",
            hashlib.sha256(fake.read_bytes()).hexdigest(),
        )
        monkeypatch.setattr(LE, "_verified", False)
        assert real_available() is True

    @needs_model
    def test_real_file_matches_pin(self, monkeypatch, real_backend):
        # The downloaded artifact genuinely matches the pinned hash.
        monkeypatch.setattr(LE, "_verified", False)
        assert real_available() is True


# --------------------------------------------------------------------------- #
# Real model: geometry + the asymmetry canary                                 #
# --------------------------------------------------------------------------- #
@needs_model
class TestRealModel:
    def test_dims_and_l2_norm(self, real_backend):
        v = LE.embed_document("Avanti Feeds manufactures shrimp feed.")
        assert len(v) == LE.DIM
        assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-5

    def test_deterministic(self, real_backend):
        assert LE.embed_document("same text") == LE.embed_document("same text")

    def test_query_document_asymmetry_canary(self, real_backend):
        # THE BGE trap: queries carry the instruction prefix, documents don't.
        # If this fails, a call site (or this module) collapsed the two.
        assert LE.embed_query("shrimp feed") != LE.embed_document("shrimp feed")

    def test_prefix_is_the_only_query_difference(self, real_backend):
        # embed_query(x) == embed_document(prefix + x): proves the prefix is
        # applied exactly once, on the query side only.
        x = "diesel engine maker"
        assert LE.embed_query(x) == LE.embed_document(LE.QUERY_PREFIX + x)

    def test_semantic_sanity_findata_vocabulary(self, real_backend):
        shrimp = LE.embed_document(
            "Avanti Feeds manufactures shrimp feed and exports frozen shrimp."
        )
        diamonds = LE.embed_document(
            "Ramkrishna Exports cuts and polishes diamonds."
        )
        q = LE.embed_query("aquaculture feed company")
        # A related-domain query must rank the shrimp-feed company above the
        # diamond company — the whole point of real embeddings over pseudo.
        assert _cos(shrimp, q) > _cos(diamonds, q)

    def test_batch_matches_per_doc(self, real_backend):
        texts = ["alpha feed", "beta engines", "gamma banking"]
        batch = LE.embed_documents(texts)
        per = [LE.embed_document(t) for t in texts]
        assert len(batch) == 3
        for b, p in zip(batch, per):
            assert b == p

    def test_embed_documents_empty_list(self, real_backend):
        assert LE.embed_documents([]) == []
