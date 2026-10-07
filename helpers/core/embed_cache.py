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
other's spaces. Since 2026-09-16 the swap also self-cleans: production
indexers pass ``purge_foreign=True`` so the previous generation's
unreachable rows are GCed at attach (bench/trial embedders must NOT —
they share this store with candidate labels).

Everything here is best-effort: when the store can't be attached the callers
degrade to uncached embedding (correct, just slower).
"""

import hashlib
import sys
import time
from collections.abc import Callable

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


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def purge_foreign_models(conn, model_label: str, source: str = "") -> int:
    """GC for the pooled cache: delete rows that can never be served again.

    Lookup key is (text_hash, model), so a model swap strands the previous
    generation's rows as dead bytes. Since embedgemma adoption the store is
    MULTI-model (scripts gemma, notes/docs granite), so "foreign model" is
    only dead WITHIN the calling indexer's own source cohort — a global
    ``model !=`` purge from the granite indexers deleted every other
    surface's rows on every rebuild (the script_search gemma cache was
    wiped by each search-fresh, forcing a full ~550-row HTTP re-embed —
    hit ten times on 2026-10-07 before the fix). Scoped purge: pass the
    caller's ``source`` cohort and only that cohort's foreign-model rows
    die. Source-less callers keep the legacy global purge.
    Best-effort: failures return 0, never break the embed flow.
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
    wrapper degrades to the raw embed_fn.
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
        self._ok = self._try_init()
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
                    vec = load_vec(row[0])
                    if vec:
                        self.hits += 1
                        return vec
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
                    (h, self._model, pack_f32(vec), self._source),
                )
                # Commit per row (WAL = tiny writer windows, the house
                # pattern): a long rebuild that dies mid-pass — OOM kill,
                # sidecar crash — otherwise loses the WHOLE pass's embeds
                # with its end-of-run commit (2026-10-07: 550 gemma embeds,
                # ~17 min, evaporated when the sidecar was SIGKILLed).
                self._conn.commit()
                self.dirty += 1
            except Exception:  # noqa: S110  # cache write fails -> fine
                pass
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
    # unique_misses is always present (callers assert the whole dict)
    try:
        from helpers.core.vec_search import _attach_vec_db

        _attach_vec_db(conn)
        conn.execute(EMBED_CACHE_DDL)
    except Exception:  # noqa: S110  # no sidecar -> embed uncached
        vecs = embed_missing(texts)
        stats["misses"] = len(texts)
        stats["unique_misses"] = len(texts)
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
        vec = load_vec(raw) if raw else None
        vecs.append(vec)
    stats["hits"] = sum(v is not None for v in vecs)

    miss_idx = [i for i, v in enumerate(vecs) if v is None]
    stats["misses"] = len(miss_idx)
    if miss_idx:
        uniq_first: dict[str, int] = {}
        for i in miss_idx:
            uniq_first.setdefault(hashes[i], i)
        uniq_hashes = list(uniq_first)
        uniq_texts = [texts[uniq_first[h]] for h in uniq_hashes]
        stats["unique_misses"] = len(uniq_texts)
        new_vecs, dirty = _embed_misses_chunked(
            conn, uniq_hashes, uniq_texts, model_label, embed_missing, source
        )
        by_hash = dict(zip(uniq_hashes, new_vecs))
        for i in miss_idx:
            vecs[i] = by_hash[hashes[i]]
        stats["dirty"] = dirty

    return [v for v in vecs if v is not None], stats
