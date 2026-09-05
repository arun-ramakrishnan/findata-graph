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

import pytest


from helpers.core import local_embedder as LE  # noqa: E402

# Captured at import (collection) time, BEFORE any test's monkeypatch of
# module attributes: the genuine available() for tests that need the real
# backend behind the conftest pin.
real_available = LE.available

_BACKEND = importlib.util.find_spec("llama_cpp") is not None
_MODEL_FILE = LE.MODEL_PATH.is_file()
needs_model = pytest.mark.skipif(
    not (_BACKEND and _MODEL_FILE),
    reason="llama-cpp-python + pinned GGUF not present (setup: local_embedder module docstring)",
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

    def test_query_prefix_matches_model_symmetry(self):
        # The prefix must match the resident model's contract: bge-small is
        # asymmetric (queries carry the instruction prefix — the missing-
        # prefix recall trap); granite-embedding is symmetric (empty prefix
        # either side — the 2026-09-06 swap, embed_full_reembed S6).
        if "granite" in LE.MODEL_ID:
            assert LE.QUERY_PREFIX == ""
        else:
            assert isinstance(LE.QUERY_PREFIX, str) and LE.QUERY_PREFIX.strip()

    def test_model_id_label(self):
        # Sanctioned residents of models/ only: granite (production) and
        # bge-small (the rollback — constants in local_embedder's rollback
        # comment). Anything else is an unpinned model swap.
        assert LE.MODEL_ID in {"granite-embedding-97m-r2", "bge-small-en-v1.5"}

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
            LE,
            "MODEL_SHA256",
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
        # Model-contract canary: asymmetric models (bge) must keep the two
        # call shapes distinct — queries carry the prefix, documents don't;
        # collapse = the recall trap. Symmetric models (granite) must be
        # EXACTLY equal — inequality would mean a stray prefix crept into
        # one side (the 2026-09-06 swap flipped this canary's direction).
        if "granite" in LE.MODEL_ID:
            assert LE.embed_query("shrimp feed") == LE.embed_document("shrimp feed")
        else:
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
        diamonds = LE.embed_document("Ramkrishna Exports cuts and polishes diamonds.")
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


# --------------------------------------------------------------------------- #
# Parallel pool (parallel_cold_embed proposal, 2026-08-29)                    #
# --------------------------------------------------------------------------- #


@needs_model
class TestParallelPool:
    """Pinned spawn pool: parity + fallback behaviour (real backend)."""

    def test_pool_matches_sequential(self, real_backend):
        texts = [
            "Avanti Feeds quarterly results show strong export demand.",
            "Infosys management commentary on margin expansion.",
            "Sector note: Indian packaging industry competitive dynamics.",
            "The Chatter edition recap and company mentions overview.",
        ]
        seq = LE.embed_documents(texts)
        par = LE.embed_documents_parallel(texts, workers=2)
        assert len(par) == len(seq)
        worst = max(abs(a - b) for vs, vp in zip(seq, par) for a, b in zip(vs, vp))
        # Byte-identical: same model, same per-text forward, same
        # normalizer — worker count must not move a single bit.
        assert worst == 0.0

    def test_pool_disabled_falls_back_in_process(self, real_backend, monkeypatch):
        called = []
        monkeypatch.setattr(
            LE,
            "embed_documents",
            lambda texts: called.append(list(texts)) or [_fake_vec(t) for t in texts],
        )
        monkeypatch.setenv("EMBED_POOL_WORKERS", "0")
        out = LE.embed_documents_parallel(["a b", "c d"], workers=None)
        assert called == [["a b", "c d"]]  # in-process path, no spawn
        assert len(out) == 2


def _fake_vec(text: str) -> list[float]:
    return [float(len(text)), 1.0]
