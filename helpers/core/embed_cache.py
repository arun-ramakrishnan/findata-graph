#!/usr/bin/env python3
"""Shared content-hash embedding cache for ALL embed indexers.

Q3 of the local_embeddings proposal (2026-08-20): with a real embedding
model, a FULL refresh costs minutes of CPU, busting the maint budget. Remedy:
a ``(sha256(text), model)`` -> vector cache so unchanged text never re-embeds.
Since the embed_store consolidation it lives in ONE pooled SQLite database
(the vec store, ``memory/embed_store.db``, attached as schema ``vecdb``)
shared by every consumer; the old per-index ``<db>_vec.db`` copies were
migrated once (``helpers/maintenance/migrate_embed_store.py``). Derived,
snapshot-excluded state exactly like the vec0 mirror beside it; a new table
in research.db would collide with the schema-drift guards and DuckDB scanner
expectations.

One cache serves EVERY text population — note_search rebuild
(per-doc wrapper), doc/script indexers, company-embeddings populate (batch
wrapper). The key is content + model label, never the population; the
optional ``source`` column only stamps which indexer wrote a row (cohort
analytics) and never participates in lookups. A model swap re-embeds
everything (label is part of the key), which is exactly the required
semantics: vectors from different models must never be served for each
other's spaces. Since 2026-10-08 no production indexer purges foreign
models at all: a stamp-mismatched index can never READ another model's
rows, so the previous generation's rows are rollback insurance, not
garbage — and a sidecar-down fallback rebuild that flipped a stamp used
to destroy the adopted model's whole cache with it (three live incidents
2026-10-07/08). Dead-text eviction is `make embed-gc`'s job; the explicit
``purge_foreign_models()`` survives for one-off model retirement only.

Everything here is best-effort: when the store can't be attached the callers
degrade to uncached embedding (correct, just slower).

The batch wrapper can opt into a read-only Rust hit lookup for file-backed
stores; Python remains responsible for misses, embedding, and persistence.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.vec_codec import load_vec, pack_f32

# Greppable surface tag for every long-running progress line — filter a
# mixed rebuild log with e.g. `grep '\[notes\]'`. Source-cohort names map
# to tags ('note' -> '[notes]', 'doc' -> '[docs]', 'script' -> '[scripts]',
# 'company' -> '[companies]'); unknown sources pass through bracketed raw.
_SURFACE_TAGS = {
    "note": "notes",
    "doc": "docs",
    "script": "scripts",
    "company": "companies",
}


def _tag(source: str, model_label: str = "") -> str:
    return f"[{_SURFACE_TAGS.get(source, source or model_label or 'embed')}]"


def _progress(msg: str) -> None:
    """Unbuffered stderr progress line (survives redirected logs)."""
    print(msg, file=sys.stderr, flush=True)


# Lazy re-init cadence for a CachedEmbed whose attach failed at construction
# (doc_leg_embed_cache_persistence S1, 2026-10-17): retry the attach every N
# misses instead of staying dead for a whole pass — a busy embed_store at
# init (a concurrent rebuild's writer) used to cost the ENTIRE pass its
# cache silently (377 doc embeds computed, zero stored, no signal).
_REINIT_EVERY = 128


# Qualified name inside the attached ``vecdb`` schema used at runtime.
EMBED_CACHE_TABLE = "vecdb.embed_cache"
# Bare (unqualified) names for tooling that opens the store file directly
# (the migration script) instead of going through vec_search._attach_vec_db.
CACHE_TABLE_BARE = "embed_cache"
LEGACY_CACHE_TABLE = "note_search_emb_cache"
CACHE_DDL_BARE = (
    f"CREATE TABLE IF NOT EXISTS {CACHE_TABLE_BARE} ("
    " text_hash TEXT NOT NULL,"
    " model     TEXT NOT NULL,"
    " embedding BLOB NOT NULL,"  # f32 binary (embedding_blob_migration S2)
    " source    TEXT NOT NULL DEFAULT '',"
    " PRIMARY KEY (text_hash, model)"
    ")"
)
EMBED_CACHE_DDL = CACHE_DDL_BARE.replace(CACHE_TABLE_BARE, EMBED_CACHE_TABLE)

_RUST_LOOKUP_ENV = "FINDATA_RUST_EMBED_CACHE_LOOKUP"
_RUST_LOOKUP_BIN_ENV = "FINDATA_RUST_EMBED_CACHE_BIN"
_RUST_LOOKUP_TIMING_ENV = "FINDATA_RUST_EMBED_CACHE_TIMING"
_RUST_LOOKUP_WARNED = False


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _rust_lookup_binary() -> Path | None:
    """Return the built S1 CLI, or the explicit operator-provided binary."""
    configured = os.environ.get(_RUST_LOOKUP_BIN_ENV)
    if configured:
        path = Path(configured)
        return path if path.is_file() else None

    root = Path(__file__).resolve().parents[2]
    for profile in ("release", "debug"):
        path = root / "Rust" / "target" / profile / "embed-cache-lookup"
        if path.is_file():
            return path
    return None


def _parse_lookup_response(
    response: dict, texts: list[str]
) -> tuple[dict[int, list[float]], list[int]]:
    """Validate the Rust CLI response shape; raises ValueError on any defect.

    The caller treats every defect identically (warn once, fall back to the
    Python path), so all shape violations funnel through one exception type.
    """
    if response.get("protocol") != 1:
        raise ValueError("unsupported response protocol")

    hit_vectors: dict[int, list[float]] = {}
    for hit in response["hit_vectors"]:
        index = hit["index"]
        vector = hit["vector"]
        if type(index) is not int or not 0 <= index < len(texts):
            raise ValueError("invalid hit index")
        if index in hit_vectors or not isinstance(vector, list):
            raise ValueError("invalid or duplicate hit vector")
        if any(type(value) not in (int, float) for value in vector):
            raise ValueError("non-numeric hit vector")
        hit_vectors[index] = [float(value) for value in vector]

    miss_indices = response["miss_indices"]
    if any(type(index) is not int or not 0 <= index < len(texts) for index in miss_indices):
        raise ValueError("invalid miss index")
    if len(set(miss_indices)) != len(miss_indices):
        raise ValueError("duplicate miss index")
    if set(hit_vectors).intersection(miss_indices):
        raise ValueError("index reported as both hit and miss")
    if set(hit_vectors).union(miss_indices) != set(range(len(texts))):
        raise ValueError("incomplete lookup response")
    return hit_vectors, miss_indices


def _rust_cache_lookup(
    conn, texts: list[str], model_label: str
) -> tuple[dict[int, list[float]], list[int]] | None:
    """Try the opt-in Rust hit-only lookup; None means use Python fallback.

    The subprocess opens the already-attached file-backed vecdb read-only.
    Anonymous in-memory stores cannot cross the process boundary and always
    stay on the established Python path.
    """
    if os.environ.get(_RUST_LOOKUP_ENV) != "1":
        return None

    global _RUST_LOOKUP_WARNED

    def warn_once(detail: str) -> None:
        global _RUST_LOOKUP_WARNED
        if not _RUST_LOOKUP_WARNED:
            _progress(f"[embed-cache] Rust lookup unavailable ({detail}) — falling back to Python")
            _RUST_LOOKUP_WARNED = True

    try:
        db_path = next(
            (
                row[2]
                for row in conn.execute("PRAGMA database_list")
                if row[1] == "vecdb" and row[2]
            ),
            None,
        )
        if not db_path:
            return None
        binary = _rust_lookup_binary()
        if binary is None:
            warn_once("CLI binary not found")
            return None

        # Python hashes with errors="replace". Normalize only the wire copy so
        # a lone surrogate (if present) hashes to the identical UTF-8 bytes.
        wire_texts = [text.encode("utf-8", errors="replace").decode("utf-8") for text in texts]
        request = json.dumps(
            {
                "protocol": 1,
                "db_path": db_path,
                "model": model_label,
                "texts": wire_texts,
            },
            separators=(",", ":"),
        )
        result = subprocess.run(  # noqa: S603  fixed argv: repo-built or operator-configured embed-cache-lookup CLI, no shell
            [str(binary)],
            input=request,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            warn_once(detail)
            return None

        hit_vectors, miss_indices = _parse_lookup_response(jsonio.loads(result.stdout), texts)
        return hit_vectors, miss_indices
    except Exception as exc:  # cache accelerator failures must not block embedding
        warn_once(f"{type(exc).__name__}: {exc}")
        return None


def purge_foreign_models(conn, model_label: str, source: str = "") -> int:
    """Explicit one-off GC: delete a cohort's foreign-model cache rows.

    NO production caller (2026-10-08 doctrine) — kept for deliberate
    model retirement only (e.g. retiring a quant candidate for good).
    Lookup key is (text_hash, model), so a model swap strands the previous
    generation's rows as dead bytes — but a stamp-mismatched index can
    never READ them, so they are rollback insurance, not garbage: the
    automatic per-indexer purge (global, then per-cohort) destroyed the
    adopted gemma script cache three times live (2026-10-07/08 — the
    third was a sidecar-down fallback rebuild that flipped the stamp and
    purged with it). Automatic purge is gone; dead-text eviction is
    `make embed-gc`. Source scopes the DELETE to one cohort; source-less
    callers purge globally. Best-effort: failures return 0.
    """
    try:
        if source:
            cur = conn.execute(
                f"DELETE FROM {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                "WHERE source = ? AND model != ?",
                (source, model_label),
            )
        else:
            cur = conn.execute(
                f"DELETE FROM {EMBED_CACHE_TABLE} WHERE model != ?",  # noqa: S608  # constant table name
                (model_label,),
            )
        conn.commit()
        return cur.rowcount or 0
    except Exception:  # noqa: S110  # purge is never load-bearing
        return 0


class CachedEmbed:
    """Per-text wrapper around a resolved embedder (note_search rebuild).

    Counts hits/misses/dirty for the stats report. ``source`` stamps the
    pooled-cache cohort ('note'/'doc'/'script'/'company') — analytics only,
    never a lookup key. Best-effort: if the store can't be attached the
    wrapper degrades to the raw embed_fn — but the degradation is LOUD
    (stderr warning + ``status_line()`` in the rebuild summary) and
    self-heals: the attach is retried every ``_REINIT_EVERY`` misses, so a
    store that was busy only at construction time recovers mid-pass
    (doc_leg_embed_cache_persistence S1, 2026-10-17: a full doc pass
    embedded 377 sections uncached because a concurrent writer held the
    store at init, and nothing reported it).
    """

    def __init__(
        self,
        embed_fn: Callable[[str], list[float]],
        model_label: str,
        conn,
        source: str = "",
        purge_foreign: bool = False,
    ):
        self._fn = embed_fn
        self._model = model_label
        self._conn = conn
        self._source = source
        self.hits = 0
        self.misses = 0
        self.dirty = 0
        self._store_errors = 0
        self._miss_total = 0
        self._attach_error: str | None = None
        self._ok = self._try_init()
        if not self._ok:
            _progress(
                f"[embed-cache] attach failed ({self._attach_error}) — "
                f"{_tag(self._source, self._model)} embedding UNCACHED; "
                f"retrying every {_REINIT_EVERY} misses"
            )
        if purge_foreign and self._ok:
            gone = purge_foreign_models(self._conn, model_label, self._source)
            if gone:
                print(
                    f"[embed-cache] purged {gone} foreign-model row(s) in "
                    f"source cohort {self._source!r} (model-swap GC)",
                    file=sys.stderr,
                    flush=True,
                )

    def _try_init(self) -> bool:
        try:
            from helpers.core.vec_search import _attach_vec_db

            _attach_vec_db(self._conn)
            self._conn.execute(EMBED_CACHE_DDL)
            self._attach_error = None
            return True
        except Exception as exc:  # recorded, surfaced via status_line()
            self._attach_error = f"{type(exc).__name__}: {exc}"
            return False

    def status_line(self) -> str:
        """One-line cache state for rebuild summaries (S1 surfacing)."""
        if not self._ok:
            return f"OFF — attach failed: {self._attach_error}"
        extra = f", {self._store_errors} store failures" if self._store_errors else ""
        return f"on (hits={self.hits}, misses={self.misses}, cached={self.dirty}{extra})"

    def _maybe_reinit(self) -> None:
        # Rate-limited lazy re-init (S1): a store that was busy only at
        # construction no longer sentences the whole pass to uncached
        # embedding. The modulo gate (miss #0, #128, ...) keeps a
        # persistently-dead store from retry-spamming.
        if self._ok or self._miss_total % _REINIT_EVERY != 0:
            return
        self._ok = self._try_init()
        if self._ok:
            _progress(
                f"{_tag(self._source, self._model)} cache attach "
                f"recovered after {self._miss_total} uncached miss(es)"
            )

    def _tick_miss_progress(self) -> None:
        # Live progress every 128 misses (the "long jobs read as stuck"
        # lesson, 2026-09-05): stderr + flush survive block-buffered log
        # redirection; hits are cheap so only miss-misses tick the meter.
        if self._miss_total % 128 != 0:
            return
        t0 = getattr(self, "_t0", None)
        if t0 is None:
            self._t0 = time.perf_counter()
        else:
            rate = self._miss_total / (time.perf_counter() - t0)
            _progress(f"{_tag(self._source, self._model)} miss #{self._miss_total} ({rate:.1f}/s)")

    def _store_vec(self, h: str | None, vec: list[float]) -> None:
        # Store is best-effort: the vector is already computed, so a cache
        # write must never lose it or kill the pass. Commit per row (WAL =
        # tiny writer windows, the house pattern): a long rebuild that dies
        # mid-pass — OOM kill, sidecar crash — otherwise loses the WHOLE
        # pass's embeds with its end-of-run commit (2026-10-07: 550 gemma
        # embeds, ~17 min, evaporated when the sidecar was SIGKILLed).
        if not (self._ok and h is not None):
            return
        try:
            self._conn.execute(
                f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                "(text_hash, model, embedding, source) VALUES (?, ?, ?, ?)",
                (h, self._model, pack_f32(vec), self._source),
            )
            self._conn.commit()
            self.dirty += 1
        except Exception as exc:  # vector still returned; count + surface
            self._store_errors += 1
            if self._store_errors == 1:
                _progress(
                    f"[embed-cache] cache store failed "
                    f"({type(exc).__name__}: {exc}) — continuing "
                    f"uncached; per-row retries continue"
                )

    def __call__(self, text: str) -> list[float]:
        self._maybe_reinit()
        h = _hash(text) if self._ok else None
        if h is not None:
            try:
                row = self._conn.execute(
                    f"SELECT embedding FROM {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                    "WHERE text_hash = ? AND model = ?",
                    (h, self._model),
                ).fetchone()
                if row:
                    vec = load_vec(row[0])
                    if vec:
                        self.hits += 1
                        return vec
            except Exception:  # noqa: S110  # cache read fails -> embed
                pass
        self._miss_total += 1
        self._tick_miss_progress()
        vec = self._fn(text)
        self.misses += 1
        self._store_vec(h, vec)
        return vec


def _embed_misses_chunked(
    conn,
    uniq_hashes: list[str],
    uniq_texts: list[str],
    model_label: str,
    embed_missing: Callable[[list[str]], list[list[float]]],
    source: str,
) -> tuple[list[list[float]], int]:
    """Embed unique misses in chunks, persisting each chunk to the cache.
    Returns (vectors, dirty_count)."""
    chunk = 512
    new_vecs: list[list[float]] = []
    dirty = 0
    t0 = time.perf_counter()
    for off in range(0, len(uniq_texts), chunk):
        part = embed_missing(uniq_texts[off : off + chunk])
        if len(part) != len(uniq_texts[off : off + chunk]):
            raise ValueError(
                f"batch embedder returned {len(part)} vectors for "
                f"{len(uniq_texts[off : off + chunk])} texts"
            )
        new_vecs.extend(part)
        done = min(off + chunk, len(uniq_texts))
        try:
            conn.executemany(
                f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                "(text_hash, model, embedding, source) VALUES (?, ?, ?, ?)",
                [
                    (h, model_label, pack_f32(v), source)
                    for h, v in zip(uniq_hashes[off:done], new_vecs[off:done])
                ],  # fmt: skip
            )
            conn.commit()
            dirty += done - off
        except Exception:  # noqa: S110  # cache write fails -> vectors still returned
            pass
        rate = done / (time.perf_counter() - t0)
        _progress(f"{_tag(source, model_label)} {done}/{len(uniq_texts)} unique ({rate:.1f}/s)")
    return new_vecs, dirty


def _python_cache_lookup(
    conn, texts: list[str], model_label: str
) -> tuple[list[list[float] | None], list[str | None], list[int]]:
    """Cache-table hit pass (the default Python backend).

    Returns ``(vectors, hashes, miss_idx)`` — ``vectors`` mirrors ``texts``
    with ``None`` at every cache miss, ``hashes`` the sha256 per text (the
    caller only consumes the miss rows), ``miss_idx`` the miss positions.
    """
    cached: dict[str, str] = {
        row[0]: row[1]
        for row in conn.execute(
            f"SELECT text_hash, embedding FROM {EMBED_CACHE_TABLE} "  # noqa: S608  constant table name
            "WHERE model = ?",
            (model_label,),
        )
    }
    hashes: list[str | None] = [_hash(text) for text in texts]
    vecs: list[list[float] | None] = []
    for hash_value in hashes:
        raw = cached.get(hash_value) if hash_value else None
        vec = load_vec(raw) if raw else None
        vecs.append(vec)
    miss_idx = [i for i, vector in enumerate(vecs) if vector is None]
    return vecs, hashes, miss_idx


def _embed_and_fill_misses(
    conn,
    texts: list[str],
    vecs: list[list[float] | None],
    miss_idx: list[int],
    hashes: list[str | None],
    model_label: str,
    embed_missing_timed: Callable[[list[str]], list[list[float]]],
    source: str,
    stats: dict,
) -> None:
    """Embed the cache-miss rows and write the new vectors back into ``vecs``.

    Miss rows always carry a hash: the Python path hashes every text, the Rust
    path fills exactly the miss indices. The dict comprehension does the type
    narrowing a list index can't express. Mutates ``vecs`` and ``stats`` in
    place (input order is the caller's contract).
    """
    miss_hashes = {i: h for i in miss_idx if (h := hashes[i]) is not None}
    uniq_first: dict[str, int] = {}
    for i, hash_value in miss_hashes.items():
        uniq_first.setdefault(hash_value, i)
    uniq_hashes = list(uniq_first)
    uniq_texts = [texts[uniq_first[h]] for h in uniq_hashes]
    stats["unique_misses"] = len(uniq_texts)
    new_vecs, dirty = _embed_misses_chunked(
        conn, uniq_hashes, uniq_texts, model_label, embed_missing_timed, source
    )
    by_hash = dict(zip(uniq_hashes, new_vecs))
    for i in miss_idx:
        vecs[i] = by_hash[miss_hashes[i]]
    stats["dirty"] = dirty


def _lookup_vectors(
    conn, texts: list[str], model_label: str
) -> tuple[list[list[float] | None], list[str | None], list[int], str, float]:
    """Cache lookup pass; returns ``(vecs, hashes, miss_idx, backend, seconds)``.

    Tries the Rust accelerator first, falls back to the Python table scan.
    ``vecs`` mirrors ``texts`` (``None`` at misses); ``hashes`` is fully
    populated on the Python path and miss-only on the Rust path.
    """
    lookup_started = time.perf_counter()
    rust_lookup = _rust_cache_lookup(conn, texts, model_label)
    if rust_lookup is None:
        vecs, hashes, miss_idx = _python_cache_lookup(conn, texts, model_label)
        backend = "python"
    else:
        hit_vectors, miss_idx = rust_lookup
        vecs = [hit_vectors.get(i) for i in range(len(texts))]
        hashes = [None] * len(texts)
        for index in miss_idx:
            hashes[index] = _hash(texts[index])
        backend = "rust"
    return vecs, hashes, miss_idx, backend, time.perf_counter() - lookup_started


def cached_embed_batch(
    conn,
    texts: list[str],
    model_label: str,
    embed_missing: Callable[[list[str]], list[list[float]]],
    source: str = "",
    purge_foreign: bool = False,
) -> tuple[list[list[float]], dict]:
    """Cache-aware BATCH embed (company-embeddings populate).

    Hits are served from the pooled store cache; only the misses go through ONE
    ``embed_missing`` call (the batch embedder — a single llama.cpp call for
    the whole corpus), and those vectors are stored back into the cache.
    Returns ``(vectors_in_input_order, {"hits", "misses", "unique_misses",
    "dirty"})`` — ``misses`` counts input texts, ``unique_misses`` the
    deduplicated embed calls actually issued.

    Cache rows are committed here, not by the caller's later transaction:
    pre-warm flows (e.g. a count-only run) must persist them (the
    rebuild_note_search --check lesson). The cache is content-addressed, so
    committing early is safe even if the caller's write later fails.
    """
    stats = {"hits": 0, "misses": 0, "unique_misses": 0, "dirty": 0}
    timing_enabled = os.environ.get(_RUST_LOOKUP_TIMING_ENV) == "1"
    miss_embed_seconds = 0.0

    def embed_missing_timed(batch: list[str]) -> list[list[float]]:
        nonlocal miss_embed_seconds
        if not timing_enabled:
            return embed_missing(batch)
        started = time.perf_counter()
        try:
            return embed_missing(batch)
        finally:
            miss_embed_seconds += time.perf_counter() - started

    def record_timing(backend: str, lookup_seconds: float) -> None:
        if timing_enabled:
            stats["timing"] = {
                "backend": backend,
                "cache_lookup_seconds": round(lookup_seconds, 6),
                "miss_embed_callback_seconds": round(miss_embed_seconds, 6),
            }

    # unique_misses is always present (callers assert the whole dict)
    try:
        from helpers.core.vec_search import _attach_vec_db

        _attach_vec_db(conn)
        conn.execute(EMBED_CACHE_DDL)
    except Exception:  # noqa: S110  # no sidecar -> embed uncached
        vecs = embed_missing_timed(texts)
        stats["misses"] = len(texts)
        stats["unique_misses"] = len(texts)
        record_timing("uncached", 0.0)
        return vecs, stats
    if purge_foreign:
        gone = purge_foreign_models(conn, model_label, source)
        if gone:
            print(
                f"[embed-cache] purged {gone} foreign-model row(s) in "
                f"source cohort {source!r} (model-swap GC)",
                file=sys.stderr,
                flush=True,
            )

    vecs, hashes, miss_idx, lookup_backend, lookup_seconds = _lookup_vectors(
        conn, texts, model_label
    )

    stats["hits"] = sum(v is not None for v in vecs)
    stats["misses"] = len(miss_idx)
    if miss_idx:
        _embed_and_fill_misses(
            conn,
            texts,
            vecs,
            miss_idx,
            hashes,
            model_label,
            embed_missing_timed,
            source,
            stats,
        )

    record_timing(lookup_backend, lookup_seconds)
    return [v for v in vecs if v is not None], stats
