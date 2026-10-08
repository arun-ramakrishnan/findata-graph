#!/usr/bin/env python3
"""EmbeddingGemma-2 query/index embedder for the script_search surface.

Q8_0 GGUF at 512 dims (truncate 768->512 + re-normalise, the Matryoshka
rule) with two-sided task prefixes. Adopted for script_search
(vector-primary) by the script_search_gemma_adoption arc; every other
surface keeps granite — see helpers/core/local_embedder.py, which stays
the single source of truth for those surfaces. Full eval record:
doc/local/evaluations/emb_gemma_assessment.md.

RUNTIME (D3 assume-running, mirrors helpers/pdf/teleocr_engine.py): the
installed llama-cpp-python (0.3.36) cannot load the gemma-embedding2
arch (llama.cpp PR #30054, first in builds from b11457; PyPI latest is
still 0.3.36), so this module owns NO process supervision and makes NO
in-process Llama calls. It requires a healthy llama-server on
127.0.0.1:8732 (override via EMBGEMMA_PORT); `make embgemma-server`
starts it (one process, 4 threads — the quiet-box verdict, assessment
§4.6; no pool, no batch, no slots). Request surface is one
OpenAI-compatible /v1/embeddings POST per text over loopback urllib.

PREFIX CONTRACT (assessment §§3.2/3.5): the server does NOT apply
prompt_name — prefixes are caller-supplied, two-sided, and omission
degrades SILENTLY (hard-set MRR 1.000 -> 0.880 with no query prefix).
Query side applies here (task= code|search); doc side is basis text
(`title: {t} | text: {content}`) applied at the index site
(rebuild_script_search), NOT in this module — embed_document embeds
raw text, same division as local_embedder's symmetric granite pair.

Model artifact: models/embeddinggemma-2-Q8_0.gguf (~310MB, gitignored,
sha256-pinned below) — copied from the eval scratch dir that every
assessment number was measured against. Fetch once with:

    mkdir -p models && curl -L -o models/embeddinggemma-2-Q8_0.gguf \\
        "https://huggingface.co/unsloth/embeddinggemma-2-GGUF/resolve/main/embeddinggemma-2-Q8_0.gguf"

then verify the hash matches MODEL_SHA256 before use.

Callers must gate on available() (best-effort pattern: a missing model
file or a down sidecar must never break a rebuild of a NON-gemma-stamped
index — the selector falls back to the granite path). A gemma-STAMPED
index refuses the fallback instead: :func:`guard_gemma_stamp` raises, so
a sidecar-down rebuild can never silently un-migrate a gemma surface
(2026-10-08: the fallback populated the granite leg and un-migrated the
script stamp while the servers were down).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# --- Artifact pin -----------------------------------------------------------
MODEL_ID = "embeddinggemma-2"
MODEL_FILE = "embeddinggemma-2-Q8_0.gguf"
MODEL_PATH = _REPO_ROOT / "models" / MODEL_FILE
MODEL_SHA256 = "6f1bd4ac6c5df7444f9cca7ca36cafe6cfa34cd6f49fefb1e0b4be8143aed8bc"
MODEL_URL = f"https://huggingface.co/unsloth/embeddinggemma-2-GGUF/resolve/main/{MODEL_FILE}"

# Served dims are 768 (d_model 512 + 512->768 projection head); the
# working dim is 512 — truncate + RE-NORMALISE per the Matryoshka rule
# (assessment §5.1: lossless everywhere measured; 512d stays below the
# 50 MB Mojo GPU-routing bar). Truncation is post-forward (~6 us/vec),
# never a speed lever.
SERVER_DIMS = 768
DIM = 512

# Two-sided task prefixes, CALLER-supplied (server applies nothing).
QUERY_PREFIX_CODE = "task: code retrieval | query: "
QUERY_PREFIX_SEARCH = "task: search result | query: "
_TASK_PREFIXES = {"code": QUERY_PREFIX_CODE, "search": QUERY_PREFIX_SEARCH}

# Index stamp: model label + dims, checked by check_query_vector so a
# granite QueryVector against a gemma index (or vice versa) degrades to
# the leg's own embed — never mixed-model scores.
MODEL_LABEL = "embeddinggemma-2-q8_512"


class GemmaStampDemotion(RuntimeError):
    """A rebuild tried to move a gemma-stamped index to another model.

    Raised by :func:`guard_gemma_stamp` (2026-10-08): with the sidecar
    down, the follow-availability selector fell back to granite and
    silently re-embedded + un-migrated the script stamp (after purging
    its cache). Demotion must be EXPLICIT or refused.
    """


def guard_gemma_stamp(
    stored_model: str | None, resolved_model: str, escape: str | None = None
) -> None:
    """Top-level guard for ANY gemma-adopted surface's rebuild write path.

    Refuses (raises :class:`GemmaStampDemotion`) when the stored index
    stamp is the gemma label but this rebuild resolved a DIFFERENT
    embedder — unless ``escape`` is the caller's explicit override value
    ("granite"). Call right after resolving the embedder, passing the
    stored-stamp lookup, the resolved model label, and the surface's env
    value; a gemma-adopted surface must never be silently populated by
    the fallback model, no matter how many surfaces move to gemma.
    """
    if stored_model != MODEL_LABEL or resolved_model == MODEL_LABEL:
        return
    if (escape or "").strip().lower() == "granite":
        return
    raise GemmaStampDemotion(
        f"index is gemma-stamped ({MODEL_LABEL}) but the gemma sidecar is "
        f"down and this rebuild resolved {resolved_model!r} — refusing to "
        "populate the fallback model's leg and un-migrate the stamp. "
        "Start the sidecar with `make embgemma-server` and retry, or set "
        "the surface's embedder env to granite to un-migrate explicitly."
    )


# D3 sidecar: assume running, never spawn. Port override for tests.
EMBGEMMA_HOST = "127.0.0.1"
EMBGEMMA_PORT = int(os.environ.get("EMBGEMMA_PORT", "8732"))
REQUEST_TIMEOUT_S = 300  # 4000-char bases take ~1.3 s; wide headroom

# llama-server identity: warn (not fail) when the port serves something else.
MODEL_ID_SUBSTRING = "gemma"

# Load-once hash guard: _hashes_ok re-reads 310 MB, so available() must
# not re-hash per call (same _verified pattern as local_embedder).
_verified = False

_START_HINT = (
    "start it with `make embgemma-server` "
    "(llama-server -m models/embeddinggemma-2-Q8_0.gguf --embeddings "
    "--pooling mean --host 127.0.0.1 --port 8732 -c 8192 -t 4)"
)


class GemmaEmbedderError(Exception):
    """Base for gemma sidecar failures."""


class GemmaEmbedderUnavailable(GemmaEmbedderError):
    """Sidecar unreachable, unhealthy, or serving the wrong shape."""


def _base_url(port: int | None = None) -> str:
    return f"http://{EMBGEMMA_HOST}:{port or EMBGEMMA_PORT}"


def _hashes_ok() -> bool:
    global _verified
    if _verified:
        return True
    h = hashlib.sha256()
    with open(MODEL_PATH, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != MODEL_SHA256:
        raise GemmaEmbedderError(
            f"{MODEL_PATH} sha256 mismatch: expected {MODEL_SHA256}, got "
            f"{h.hexdigest()} — re-download via the command in the module docstring."
        )
    _verified = True
    return True


def preflight(port: int | None = None) -> str:
    """Health + identity probe. Returns the served model id.

    Raises GemmaEmbedderUnavailable when the server is down (message
    carries the start command); warns to stderr when the port serves a
    non-gemma model. No shared state — urllib calls are thread-safe, so
    unlike local_embedder's Llama handle this needs no serialising lock.
    """
    base = _base_url(port)
    try:
        # scheme+host are the module's own loopback constant, never caller-supplied
        with urllib.request.urlopen(f"{base}/health", timeout=10) as r:  # noqa: S310
            body = json.load(r)
        if body.get("status") != "ok":
            raise GemmaEmbedderUnavailable(f"embgemma server unhealthy: {body} — {_START_HINT}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise GemmaEmbedderUnavailable(
            f"embgemma server not reachable on {base}: {e} — {_START_HINT}"
        ) from e
    try:
        with urllib.request.urlopen(f"{base}/v1/models", timeout=10) as r:  # noqa: S310
            body = json.load(r)
        models = body.get("data") or body.get("models") or []
        model_id = ""
        if models:
            model_id = str(models[0].get("id") or models[0].get("name") or "")
        if MODEL_ID_SUBSTRING not in model_id.lower():
            print(
                f"  warn: port {port or EMBGEMMA_PORT} serves '{model_id or '?'}' — "
                f"expected a {MODEL_ID_SUBSTRING}* GGUF",
                file=sys.stderr,
                flush=True,
            )
        return model_id
    except urllib.error.URLError, OSError, json.JSONDecodeError:
        return ""


def available(port: int | None = None) -> bool:
    """True when the model file is present with the pinned hash AND the
    sidecar answers healthy. Never raises — callers use this to gate
    between the gemma path and the granite fallback."""
    try:
        if not MODEL_PATH.is_file():
            return False
        _hashes_ok()
        preflight(port)
        return True
    except Exception:
        return False


def _post_embedding(text: str, port: int | None = None) -> list[float]:
    """One /v1/embeddings forward. Returns the raw 768-d server vector."""
    body = json.dumps({"input": text}).encode()
    req = urllib.request.Request(  # noqa: S310
        f"{_base_url(port)}/v1/embeddings", body, {"Content-Type": "application/json"}
    )
    try:
        # loopback constant URL, see _base_url
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:  # noqa: S310
            payload = json.load(r)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise GemmaEmbedderUnavailable(f"embgemma embed request failed: {e}") from e
    try:
        vec = payload["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as e:
        raise GemmaEmbedderUnavailable(f"embgemma unexpected response shape: {payload!r}") from e
    return [float(x) for x in vec]


def _truncate(vec: list[float]) -> list[float]:
    """Matryoshka reduction 768->512 + re-normalise (unit vectors so
    cosine == dot in every consumer, same contract as local_embedder).
    Wrong-dims input raises (never silently mixes spaces); zero vector
    raises — same contract as _embed."""
    if len(vec) != SERVER_DIMS:
        raise GemmaEmbedderUnavailable(
            f"embgemma served {len(vec)} dims, expected {SERVER_DIMS} — wrong model?"
        )
    trunc = vec[:DIM]
    norm = math.sqrt(sum(x * x for x in trunc))
    if norm == 0.0:
        raise GemmaEmbedderError("sidecar returned a zero vector")
    return [x / norm for x in trunc]


def _embed(text: str, *, port: int | None = None) -> list[float]:
    """Raw sidecar forward + Matryoshka reduction. Empty input raises —
    callers decide fallback."""
    if not text or not text.strip():
        raise ValueError("cannot embed empty text")
    return _truncate(_post_embedding(text, port))


def embed_query(text: str, *, task: str = "code", port: int | None = None) -> list[float]:
    """Embed a QUERY-side text with its task prefix (the prefix contract:
    omission degrades silently — never bypass this function at gemma
    query sites). task= code (script surface) | search (prose)."""
    try:
        prefix = _TASK_PREFIXES[task]
    except KeyError:
        raise ValueError(
            f"unknown gemma query task {task!r} (want one of {sorted(_TASK_PREFIXES)})"
        )
    if not text or not text.strip():
        raise ValueError("cannot embed empty text")
    return _embed(prefix + text, port=port)


def embed_document(text: str, *, port: int | None = None) -> list[float]:
    """Embed an INDEX-side text. Raw — the doc-side prefix
    (`title: {t} | text: {content}`) is basis text applied at the index
    site, never here."""
    return _embed(text, port=port)


if __name__ == "__main__":
    # Minimal self-check: prints availability + one document/query pair
    # (needs the sidecar: `make embgemma-server` in another shell).
    print(f"available: {available()}")
    if available():
        d = embed_document("title: none | text: shrimp feed manufacturer")
        q = embed_query("aquaculture feed company")
        dot = sum(a * b for a, b in zip(d, q))
        print(f"dim={len(d)} doc·query={dot:.3f}")
        sys.exit(0)
    sys.exit(1)
