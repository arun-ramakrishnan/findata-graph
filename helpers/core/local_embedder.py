#!/usr/bin/env python3
"""
Local semantic embedder: bge-small-en-v1.5 via llama.cpp, offline (2026-08-20).

Single source of embedding truth for the three vector surfaces that used to
run on deterministic SHA-256 pseudo-embeddings (see
doc/improvements/archive/database/local_embeddings.md):

- ``helpers/graph/embeddings.py``        — company_embeddings table (index side)
- ``helpers/maintenance/rebuild_note_search.py`` — note_search hybrid ranking
- ``helpers/core/get_tickers.py``        — vss_match entity resolution (query side)

WHY ONE MODULE: bge-small-en-v1.5 is ASYMMETRIC — retrieval queries must
carry an instruction prefix, documents must NOT. An index row embedded with
``embed_document`` is only comparable against a query embedded with
``embed_query``; getting this backwards anywhere silently degrades cosine
scores. Keeping both functions (and the prefix constant) here means the rule
lives in exactly one place and the prefix asymmetry is unit-tested.

Model artifact: ``models/bge-small-en-v1.5-q8_0.gguf`` (~35MB, gitignored,
sha256-pinned below). Fetch once with:

    mkdir -p models && curl -L -o models/bge-small-en-v1.5-q8_0.gguf \\
        "https://huggingface.co/CompendiumLabs/bge-small-en-v1.5-gguf/resolve/main/bge-small-en-v1.5-q8_0.gguf"

No network at index or query time, ever — the only fetch is the manual
download above; ``available()`` verifies the file hash before use.

Callers must gate on ``available()`` (best-effort pattern: a missing model
must never break ``make maint``); the ``embed_*`` functions raise
RuntimeError when the backend is absent so the failure is loud where the
caller chose not to gate.
"""

from __future__ import annotations

import atexit
import hashlib
import math
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# --- Artifact pin -----------------------------------------------------------
# CompendiumLabs is the GGUF conversion of BAAI/bge-small-en-v1.5 (MIT).
# The proposal's original citation (ggml-org/...-Q8_0-GGUF) does not exist;
# verified via the HF API 2026-08-20.
MODEL_ID = "bge-small-en-v1.5"
MODEL_FILE = "bge-small-en-v1.5-q8_0.gguf"
MODEL_PATH = _REPO_ROOT / "models" / MODEL_FILE
MODEL_SHA256 = "ec38e8da142596baa913124ae50550de284b6916bf59577ef2f0cb9660c2f514"
MODEL_URL = (
    "https://huggingface.co/CompendiumLabs/bge-small-en-v1.5-gguf"
    f"/resolve/main/{MODEL_FILE}"
)

# bge-small-en-v1.5: 384 dims — same as the live company_embeddings table,
# so the swap is schema-transparent (SQLite CHECK, DuckDB FLOAT[] cast, and
# the snapshot parquet shape are all unchanged).
DIM = 384

# BGE v1.5 retrieval instruction: queries are embedded with this prefix,
# documents without it. Official recipe from the BAAI model card.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# bge-small max sequence length is 512 wordpiece tokens; inputs longer than
# this are truncated by llama.cpp (by design — the embedding of a truncated
# long document still ranks fine against short queries).
_N_CTX = 512

# Load-once cache. _verified guards the (one-off, ~0.1s) sha256 check so
# repeated available() calls don't re-hash the 35MB file per process.
_MODEL = None
_verified = False


def _shutdown_model() -> None:
    """Close the llama.cpp handle during interpreter exit, while llama_cpp
    is still importable. Its destructor intermittently crashes
    ("'NoneType' object is not callable" in free_model) under pytest when
    module globals are cleared before the model is freed."""
    global _MODEL
    model, _MODEL = _MODEL, None
    if model is not None:
        try:
            model.close()
        except Exception:  # noqa: S110  # exit-time best effort
            pass


atexit.register(_shutdown_model)


def _hashes_ok() -> bool:
    global _verified
    if _verified:
        return True
    h = hashlib.sha256()
    with open(MODEL_PATH, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != MODEL_SHA256:
        raise RuntimeError(
            f"{MODEL_PATH} sha256 mismatch: expected {MODEL_SHA256}, got "
            f"{h.hexdigest()} — re-download via the command in the module docstring."
        )
    _verified = True
    return True


def available() -> bool:
    """True when the llama-cpp backend imports AND the pinned model file is
    present with the pinned hash. Never raises — callers use this to gate
    between the real embedder and the pseudo fallback."""
    try:
        import llama_cpp  # noqa: F401  # availability probe only
        return MODEL_PATH.is_file() and _hashes_ok()
    except Exception:
        return False


def _get_model():
    global _MODEL
    if _MODEL is None:
        if not available():
            raise RuntimeError(
                f"local embedder unavailable: {MODEL_ID} backend or model file "
                "missing (gate on available() or see local_embedder docstring)"
            )
        from llama_cpp import Llama

        _MODEL = Llama(
            model_path=str(MODEL_PATH),
            embedding=True,
            n_ctx=_N_CTX,
            verbose=False,
        )
    return _MODEL


def _embed(text: str) -> list[float]:
    """Raw embed + L2 normalise (both vectors unit-length so cosine == dot
    in every consumer). Empty input raises — callers decide fallback."""
    if not text or not text.strip():
        raise ValueError("cannot embed empty text")
    model = _get_model()
    vec = model.create_embedding(input=[text])["data"][0]["embedding"]
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        raise RuntimeError("embedder returned a zero vector")
    return [x / norm for x in vec]


def embed_document(text: str) -> list[float]:
    """Embed an INDEX-side text (company note, FTS doc). No prefix."""
    return _embed(text)


def embed_query(text: str) -> list[float]:
    """Embed a QUERY-side text (search string, Yahoo longName). Carries the
    BGE retrieval prefix — the asymmetry with embed_document is what makes
    bge retrieval work; do not bypass this function at query sites."""
    return _embed(QUERY_PREFIX + text)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Batch embed_document (one llama.cpp call; index-side bulk refresh)."""
    if not texts:
        return []
    model = _get_model()
    data = model.create_embedding(input=list(texts))["data"]
    out = []
    for d in data:
        vec = d["embedding"]
        norm = math.sqrt(sum(x * x for x in vec))
        out.append([x / norm for x in vec] if norm else vec)
    return out


if __name__ == "__main__":
    # Minimal self-check: `python3 helpers/core/local_embedder.py` prints
    # availability + one document/query pair (used in the network-disabled
    # verification run).
    print(f"available: {available()}")
    if available():
        d = embed_document("shrimp feed manufacturer")
        q = embed_query("aquaculture feed company")
        dot = sum(a * b for a, b in zip(d, q))
        print(f"dim={len(d)} doc·query={dot:.3f}")
        sys.exit(0)
    sys.exit(1)
