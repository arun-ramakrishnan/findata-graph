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
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# --- Artifact pin -----------------------------------------------------------
# granite-embedding-97m-r2 SWAP (embed_full_reembed S6, user decision
# 2026-09-06): mykor Q8_0 GGUF of IBM granite-embedding-97m-multilingual-r2.
# Won the sectioned deep-content probe 14/15 vs bge's 11/15 at identical
# endpoint latency (27/27 search held; p50 121ms both) — full program in
# doc/improvements/archive/database/embed_full_reembed.md. Accepted costs:
# docs 15/18 (bge 18/18), neighbors 3/10 (bge 5/10).
# ROLLBACK = bge-small: restore the previous constants (CompendiumLabs
# bge-small-en-v1.5-q8_0.gguf, sha ec38e8da…c2f514, QUERY_PREFIX
# "Represent this sentence for searching relevant passages: ", _N_CTX
# 512) + rebuild — every bge vector remains in the (text, model)-keyed
# shared cache, so rollback re-embeds nothing.
MODEL_ID = "granite-embedding-97m-r2"
MODEL_FILE = "granite-embedding-97M-multilingual-r2-Q8_0.gguf"
MODEL_PATH = _REPO_ROOT / "models" / MODEL_FILE
MODEL_SHA256 = "25155b89638e501ac33495fa278d551d7545e1e2f62722a499bba1f064c080f2"
MODEL_URL = (
    "https://huggingface.co/mykor/granite-embedding-97m-multilingual-r2-GGUF/"
    f"resolve/main/{MODEL_FILE}"
)

# granite-embedding-97m-r2: 384 dims — same as bge-small and the live
# company_embeddings table, so the swap is schema-transparent (SQLite
# CHECK, DuckDB FLOAT[] cast, and the snapshot parquet shape unchanged).
DIM = 384

# granite-embedding is SYMMETRIC: no instruction prefix on either side
# (empty string = raw text, the tested shape throughout the A/B program).
QUERY_PREFIX = ""

# granite n_ctx_train = 32768; we run 2048 — the tested shape, covering
# ~90%+ of section bases whole (with the rebuild's 8000-char cap). 32k
# stays a non-goal: quadratic encoder attention on CPU and mean-pooling
# over huge windows lose to more sections (measured, proposal §3.3).
_N_CTX = 2048

# Parallel cold-embed pool (parallel_cold_embed proposal, 2026-08-29).
# Measured on this 4C/4T box: llama.cpp per-doc forwards gain nothing from
# in-process threads (1T == 4T) or sequence packing, but N spawn workers
# each pinned to a distinct core scale ~3.7x (unpinned pools COLLAPSE
# ~24x — ggml-internal; pinning sidesteps it). Pool is spawned only for
# miss-heavy batches; warm cycles (0 misses) never reach it.
_DEFAULT_POOL_WORKERS = 4
_POOL_MIN_TEXTS = 8  # below this the spawn overhead beats the speedup

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


def _get_model(n_threads: int | None = None):
    """Lazy model singleton. ``n_threads=1`` is the pool-worker shape:
    per-doc forwards are sync-bound past 1 thread (bench 2026-08-29);
    the default (None) keeps llama.cpp's own thread choice for
    single-text/query callers."""
    global _MODEL
    if _MODEL is None:
        if not available():
            raise RuntimeError(
                f"local embedder unavailable: {MODEL_ID} backend or model file "
                "missing (gate on available() or see local_embedder docstring)"
            )
        from llama_cpp import Llama

        kwargs: dict = {}
        if n_threads is not None:
            kwargs["n_threads"] = n_threads
        _MODEL = Llama(
            model_path=str(MODEL_PATH),
            embedding=True,
            n_ctx=_N_CTX,
            verbose=False,
            **kwargs,
        )
    return _MODEL


def _normalize(vec: list[float]) -> list[float]:
    """L2 normalise (unit vectors so cosine == dot in every consumer).
    Shared by every embed path so batch/parallel/serial vectors are
    byte-identical. Zero vector raises — same contract as _embed."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        raise RuntimeError("embedder returned a zero vector")
    return [x / norm for x in vec]


def _embed(text: str) -> list[float]:
    """Raw embed + L2 normalise. Empty input raises — callers decide fallback."""
    if not text or not text.strip():
        raise ValueError("cannot embed empty text")
    model = _get_model()
    vec = model.create_embedding(input=[text])["data"][0]["embedding"]
    return _normalize(vec)


def embed_document(text: str) -> list[float]:
    """Embed an INDEX-side text (company note, FTS doc). No prefix."""
    return _embed(text)


def embed_query(text: str) -> list[float]:
    """Embed a QUERY-side text (search string, Yahoo longName). Carries the
    BGE retrieval prefix — the asymmetry with embed_document is what makes
    bge retrieval work; do not bypass this function at query sites."""
    return _embed(QUERY_PREFIX + text)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Batch embed_document (index-side bulk refresh).

    PER-TEXT calls (embed_full_reembed S1, 2026-09-06): llama.cpp's
    multi-input create_embedding accumulates every text into ONE decode,
    so heterogeneous section lengths pay near-worst-case compute per
    text — measured 2.74/s batch vs 12.31/s per-text on section texts
    (4.5x). Order and contract unchanged; vectors differ from the old
    batch call by <=1.3e-3 per component (decode-shape numerics — the
    same scale the Q8<->F16 probe settled as ~2 orders below eval
    resolution, so cached batch-era and fresh per-text vectors mix
    fine). The pool path's workers were already per-text.
    """
    if not texts:
        return []
    model = _get_model()
    out = []
    for text in texts:
        vec = model.create_embedding(input=[text])["data"][0]["embedding"]
        out.append(_normalize(vec) if any(vec) else vec)
    return out


def _pool_workers(n_texts: int, workers: int | None) -> int:
    """Resolve worker count: 0 = in-process (no pool spawn).

    EMBED_POOL_WORKERS env is the operator knob (0/1 disables); the count
    is clamped to the text count and gated on _POOL_MIN_TEXTS — spawning
    4 workers for 3 docs pays spawn+model-load for nothing.
    """
    if workers is None:
        try:
            workers = int(os.environ.get("EMBED_POOL_WORKERS", _DEFAULT_POOL_WORKERS))
        except ValueError:
            workers = _DEFAULT_POOL_WORKERS
    if workers <= 1 or n_texts < _POOL_MIN_TEXTS:
        return 0
    return max(2, min(workers, n_texts))


def _pool_init(core_queue) -> None:
    """Spawn-worker initializer: claim a distinct core, pin, load model.

    Pin BEFORE the Llama() load — affinity applies to threads created
    after the call, so pinning post-init leaves the compute thread
    floating (bench 2026-08-29: pinned pool 3.7x; unpinned collapses).
    n_threads=1: per-doc forwards are sync-bound past one thread.
    """
    core = core_queue.get()
    try:
        os.sched_setaffinity(0, {core % (os.cpu_count() or 1)})
    except OSError:
        pass  # restricted env: float (still correct, just slower)
    _get_model(n_threads=1)


def _pool_embed_chunk(arg: tuple[int, list[str]]) -> tuple[int, list[list[float]]]:
    """Embed one contiguous chunk; returns (start_index, vectors)."""
    start, texts = arg
    model = _get_model(n_threads=1)
    out = []
    for text in texts:
        vec = model.create_embedding(input=[text])["data"][0]["embedding"]
        out.append(_normalize(vec))
    return start, out


def embed_documents_parallel(texts: list[str], workers: int | None = None) -> list[list[float]]:
    """Batch embed_document via a pinned spawn pool (cold-path only).

    Same contract and output as embed_documents (index side, no BGE
    prefix, L2-normalised, input order preserved, byte-identical
    vectors). Falls back to in-process embed_documents when the pool is
    disabled (EMBED_POOL_WORKERS=0/1) or the batch is tiny. Fail-loud:
    a worker crash surfaces as an exception here — callers decide their
    own degrade policy.
    """
    if not texts:
        return []
    n = _pool_workers(len(texts), workers)
    if n == 0:
        return embed_documents(texts)

    import multiprocessing as mp

    ncpu = os.cpu_count() or 1
    bounds = [(i * len(texts) // n, (i + 1) * len(texts) // n) for i in range(n)]
    chunks = [(start, texts[start:end]) for start, end in bounds if end > start]
    ctx = mp.get_context("spawn")
    core_queue = ctx.Queue()
    for core in sorted({i % ncpu for i in range(n)}):
        core_queue.put(core)
    out: list[list[float] | None] = [None] * len(texts)
    try:
        with ctx.Pool(n, initializer=_pool_init, initargs=(core_queue,)) as pool:
            for start, vecs in pool.map(_pool_embed_chunk, chunks):
                for j, vec in enumerate(vecs):
                    out[start + j] = vec
    finally:
        core_queue.close()
        core_queue.join_thread()
    if any(v is None for v in out):
        raise RuntimeError("parallel embed left unfilled slots — chunk bug")
    return [v for v in out if v is not None]


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
