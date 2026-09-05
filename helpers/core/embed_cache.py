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
other's spaces.

Everything here is best-effort: when the store can't be attached the callers
degrade to uncached embedding (correct, just slower).
"""

import hashlib
import json
import sys
import time
from collections.abc import Callable

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
    " embedding TEXT NOT NULL,"
    " source    TEXT NOT NULL DEFAULT '',"
    " PRIMARY KEY (text_hash, model)"
    ")"
)
EMBED_CACHE_DDL = CACHE_DDL_BARE.replace(CACHE_TABLE_BARE, EMBED_CACHE_TABLE)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


class CachedEmbed:
    """Per-text wrapper around a resolved embedder (note_search rebuild).

    Counts hits/misses/dirty for the stats report. ``source`` stamps the
    pooled-cache cohort ('note'/'doc'/'script'/'company') — analytics only,
    never a lookup key. Best-effort: if the store can't be attached the
    wrapper degrades to the raw embed_fn.
    """

    def __init__(
        self,
        embed_fn: Callable[[str], list[float]],
        model_label: str,
        conn,
        source: str = "",
    ):
        self._fn = embed_fn
        self._model = model_label
        self._conn = conn
        self._source = source
        self.hits = 0
        self.misses = 0
        self.dirty = 0
        self._ok = self._try_init()

    def _try_init(self) -> bool:
        try:
            from helpers.core.vec_search import _attach_vec_db

            _attach_vec_db(self._conn)
            self._conn.execute(EMBED_CACHE_DDL)
            return True
        except Exception:  # noqa: S110  # no sidecar -> embed uncached
            return False

    def __call__(self, text: str) -> list[float]:
        h = None
        if self._ok:
            h = _hash(text)
            try:
                row = self._conn.execute(
                    f"SELECT embedding FROM {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                    "WHERE text_hash = ? AND model = ?",
                    (h, self._model),
                ).fetchone()
                if row:
                    self.hits += 1
                    return json.loads(row[0])
            except Exception:  # noqa: S110  # cache read fails -> embed
                pass
        # Live progress every 128 misses (the "long jobs read as stuck"
        # lesson, 2026-09-05): stderr + flush survive block-buffered log
        # redirection; hits are cheap so only miss-misses tick the meter.
        self._miss_total = getattr(self, "_miss_total", 0) + 1
        if self._miss_total % 128 == 0:
            t0 = getattr(self, "_t0", None)
            if t0 is None:
                self._t0 = time.perf_counter()
            else:
                rate = self._miss_total / (time.perf_counter() - t0)
                _progress(
                    f"{_tag(self._source, self._model)} miss #{self._miss_total} ({rate:.1f}/s)"
                )
        vec = self._fn(text)
        self.misses += 1
        if self._ok and h is not None:
            try:
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                    "(text_hash, model, embedding, source) VALUES (?, ?, ?, ?)",
                    (h, self._model, json.dumps(vec), self._source),
                )
                self.dirty += 1
            except Exception:  # noqa: S110  # cache write fails -> fine
                pass
        return vec


def cached_embed_batch(
    conn,
    texts: list[str],
    model_label: str,
    embed_missing: Callable[[list[str]], list[list[float]]],
    source: str = "",
) -> tuple[list[list[float]], dict]:
    """Cache-aware BATCH embed (company-embeddings populate).

    Hits are served from the pooled store cache; only the misses go through ONE
    ``embed_missing`` call (the batch embedder — a single llama.cpp call for
    the whole corpus), and those vectors are stored back into the cache.
    Returns ``(vectors_in_input_order, {"hits", "misses", "dirty"})``.

    Cache rows are committed here, not by the caller's later transaction:
    pre-warm flows (e.g. a count-only run) must persist them (the
    rebuild_note_search --check lesson). The cache is content-addressed, so
    committing early is safe even if the caller's write later fails.
    """
    stats = {"hits": 0, "misses": 0, "dirty": 0}
    try:
        from helpers.core.vec_search import _attach_vec_db

        _attach_vec_db(conn)
        conn.execute(EMBED_CACHE_DDL)
    except Exception:  # noqa: S110  # no sidecar -> embed uncached
        vecs = embed_missing(texts)
        stats["misses"] = len(texts)
        return vecs, stats

    # One bulk load of this model's cache slice (a few thousand rows at
    # most — note_search + company texts) beats N point SELECTs.
    cached: dict[str, str] = {
        row[0]: row[1]
        for row in conn.execute(
            f"SELECT text_hash, embedding FROM {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
            "WHERE model = ?",
            (model_label,),
        )
    }

    hashes = [_hash(t) for t in texts]
    vecs: list[list[float] | None] = []
    for h in hashes:
        raw = cached.get(h)
        try:
            vecs.append(json.loads(raw) if raw else None)
        except ValueError:  # corrupted cache row -> treat as a miss
            vecs.append(None)
    stats["hits"] = sum(v is not None for v in vecs)

    miss_idx = [i for i, v in enumerate(vecs) if v is None]
    stats["misses"] = len(miss_idx)
    if miss_idx:
        miss_texts = [texts[i] for i in miss_idx]
        # Chunked ONLY for live progress (the "long jobs read as stuck"
        # lesson, 2026-09-05) — embed_missing loops per-text inside each
        # chunk (S1 shape), so chunking never reintroduces batch decodes.
        # stderr + flush survive block-buffered log redirection.
        chunk = 512
        new_vecs: list[list[float]] = []
        t0 = time.perf_counter()
        for off in range(0, len(miss_texts), chunk):
            part = embed_missing(miss_texts[off : off + chunk])
            if len(part) != len(miss_texts[off : off + chunk]):
                # A short/long reply would silently shift vectors onto the
                # wrong texts below — fail loudly instead.
                raise ValueError(
                    f"batch embedder returned {len(part)} vectors for "
                    f"{len(miss_texts[off : off + chunk])} texts"
                )
            new_vecs.extend(part)
            done = min(off + chunk, len(miss_texts))
            rate = done / (time.perf_counter() - t0)
            _progress(f"{_tag(source, model_label)} {done}/{len(miss_texts)} ({rate:.1f}/s)")
        for i, vec in zip(miss_idx, new_vecs):
            vecs[i] = vec
        try:
            conn.executemany(
                f"INSERT OR REPLACE INTO {EMBED_CACHE_TABLE} "  # noqa: S608  # constant table name
                "(text_hash, model, embedding, source) VALUES (?, ?, ?, ?)",
                [
                    (hashes[i], model_label, json.dumps(v), source)
                    for i, v in zip(miss_idx, new_vecs)
                ],
            )
            conn.commit()  # persist NOW (pre-warm lesson; see docstring)
            stats["dirty"] = len(miss_idx)
        except Exception:  # noqa: S110  # cache write fails -> vectors still returned
            pass

    # All slots are filled by here (hits from cache, misses embedded).
    return [v for v in vecs if v is not None], stats
