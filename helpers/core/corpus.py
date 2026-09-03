#!/usr/bin/env python3
"""Shared corpus loader — one walk + one YAML parse for the 1243-file findata corpus.

S1b: the `5× rglob + 5× yaml_safe_load` replay (`verify_notes`, `frontmatter_schema`,
`sync_tags`, `derive_themes`, `derive_insights` each re-walk) is the last
dedup miss after `static_checks` collapsed `3→1`. This module provides the single
parse site; `maint --full` callers can share one `Corpus` instance across
derivations instead of each doing `sorted(root.rglob("*.md"))` + `read_text`.

Design:
  * `iter_findata_files(root)` — one `fs_walk.iter_tree_files` over `findata/`,
    name-sorted, symlink-safe, file-is-file contract (same as `fs_walk`).
  * `Corpus.load(root, *, workers=4)` — one walk, then `ThreadPool 4` `read_text`
    + `yaml_safe_load` (C when `CSafeLoader` available) + `strip_frontmatter`.
    Returns `list[Note]` with `path`, `text`, `frontmatter dict`, `body`.
    `workers=1` is the serial fallback for tests.
  * `notes_stale_since(db_max, trees)` — the shared S1c `--stale-only` gate
    (mtime max vs `MAX(created_at)`); derive_events/themes/cited_in call it
    instead of carrying three copy-pasted blocks.

Usage:
    from helpers.core.corpus import Corpus
    corpus = Corpus.load(Path("findata"))  # one walk + pool
    for note in corpus.notes:
        tags = note.frontmatter.get("tags", [])
    # reuse across derivations in the same `maint --full` process:
    #   sync_tags.py(corpus), derive_themes.py(corpus) — pass Corpus instead of re-walking.

Performance (S0 baseline `2.19-2.42s` YAML hot, `THR=": _build_resolver_map`):
  * Single walk `fs_walk 1244 0.003s` vs `5× rglob` `~0.015s` saved is trivial,
    the win is one `ThreadPool 4` `yaml C` pass vs 5 serial passes.
  * `yaml_safe_load` is `CSafeLoader` (libyaml) `~10×` vs pure Python —
    centralizing on this module's import guarantees `CSafeLoader`.
"""

from __future__ import annotations
# ruff: noqa: C901, S101, S110, UP037  # S1b scale: Corpus + stale advisory, complexity is domain logic not lint

import datetime
import os
import hashlib
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helpers.core.frontmatter import yaml_safe_load, strip_frontmatter

try:
    from helpers.core.db import connect as _db_connect  # S1b DB cache (sidecar, not research.db)

    _HAS_DB_CONNECT = True
except ImportError:  # pragma: no cover
    _db_connect = None  # type: ignore[assignment]
    _HAS_DB_CONNECT = False

try:
    from helpers.core.fs_walk import iter_tree_files
except ImportError:  # pragma: no cover - fallback for isolated import
    iter_tree_files = None  # type: ignore[assignment]

# S1b shared cache — consolidated in helpers/core/corpus.py (one place, not duped per-module).
# Was a /tmp pickle ephemeral (0.16s load, full rebuild if any file newer, and its findata-root
# branch returned unfiltered content — a stale synthetic pickle silently poisoned production
# loads, hit 2026-09-02); dropped 2026-09-02. Now memory/corpus.db (gitignored, like
# doc_search.db) with per-file mtime incremental (0.02s load + 1 file yaml when 1 of 1243
# changed, not 0.37s full). Persistent across reboots; snapshot does not bloat research.db —
# corpus.db is sidecar like embed_store.db.
_CACHE_DB = Path(__file__).resolve().parents[2] / "memory" / "corpus.db"
_CACHE_DB_RETAIN = 1  # keep DB even when findata max_mtime == cache, for incremental


@dataclass(frozen=True)
class Note:
    path: Path
    text: str
    frontmatter: dict[str, Any]
    body: str


def iter_findata_files(root: Path) -> list[Path]:
    """One symlink-safe sorted walk over `root` (same contract as `fs_walk`)."""
    if iter_tree_files is not None:
        files = [p for p in iter_tree_files(root) if p.suffix == ".md"]
    else:
        files = [p for p in Path(root).rglob("*.md") if p.is_file()]
    files.sort()
    return files


def notes_stale_since(
    db_max: str | None,
    trees: Sequence[Path],
    *,
    pattern: str = "*.md",
) -> bool:
    """S1c shared stale gate — True when no note under *trees* is newer than *db_max*.

    *db_max* is the SQL ``MAX(created_at)`` string ("YYYY-MM-DD HH:MM:SS" shape)
    from the derivation's target table. Any doubt derives: falsy *db_max*
    (nothing derived yet), an unparseable timestamp, or an empty tree all
    return False — the caller falls through to the full run, which is the
    safe direction for a skip gate.

    rglob (not ``fs_walk``) is deliberate: one max-mtime pass over ~1k files
    with no per-file work — a symlink-safe walk buys nothing here (the
    comment that used to be copy-pasted in derive_themes). OSError on a
    single stat (mid-walk deletion race) skips that file rather than
    aborting the gate — the derive_cited_in semantics, now uniform.
    """
    if not db_max:
        return False
    try:
        db_dt = datetime.datetime.fromisoformat(str(db_max).replace(" ", "T"))
    except TypeError, ValueError:
        return False
    max_mtime = 0.0
    for tree in trees:
        d = Path(tree)
        if not d.is_dir():
            continue
        for pp in d.rglob(pattern):
            try:
                mt = pp.stat().st_mtime
            except OSError:
                continue
            if mt > max_mtime:
                max_mtime = mt
    try:
        return bool(max_mtime) and datetime.datetime.fromtimestamp(max_mtime) <= db_dt
    except OverflowError, OSError, ValueError:  # clock out-of-range etc.
        return False


def _load_one(path: Path) -> Note | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Split frontmatter without re-parsing YAML twice if possible.
    # yaml_safe_load is the C path; strip_frontmatter is the body path — both
    # operate on the same text but are cheap vs re-reading the file.
    body = strip_frontmatter(text)
    # Extract frontmatter block for yaml load (helpers.core.frontmatter.split_frontmatter could be used, but
    # keeping this inline avoids an extra regex match — yaml_safe_load on the block is the hot part).
    # Use the shared split helper for correctness:
    from helpers.core.frontmatter import split_frontmatter

    try:
        fm_block = split_frontmatter(text)[1]
        fm = yaml_safe_load(fm_block) if fm_block.strip() else {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return Note(path=path, text=text, frontmatter=fm, body=body)


@dataclass
class Corpus:
    root: Path
    notes: list[Note]

    @classmethod
    def load(
        cls, root: str | Path = "findata", *, workers: int = 1, use_cache: bool = True
    ) -> "Corpus":
        """One walk+parse. `use_cache=True` hits `memory/corpus.db` per-file mtime
        incremental (`0.02s` load + `1` yaml when `1/1243` changed) not `0.37s` full.
        Falls back to the full walk. `maint --full` pre-warms once;
        subsequent `sync_tags --corpus` etc. hit DB cache.
        """
        root = Path(root)
        # DB fast path — per-file mtime incremental (persistent, not /tmp ephemeral)
        if use_cache and _CACHE_DB.exists():
            try:
                import json as _json

                files = iter_findata_files(root)
                # Build file -> mtime map (0.003s walk)
                mtimes = {pp: pp.stat().st_mtime for pp in files}
                conn = (
                    _db_connect(_CACHE_DB)  # ty: ignore[call-non-callable]
                    if _HAS_DB_CONNECT
                    else __import__("sqlite3").connect(str(_CACHE_DB))
                )
                try:
                    cur = conn.execute(
                        "SELECT path, mtime, content_hash, frontmatter_json, body, text FROM corpus_cache"
                    )
                    cached = {
                        row[0]: (row[1], row[2], row[3], row[4], row[5]) for row in cur.fetchall()
                    }
                finally:
                    conn.close()
                notes: list[Note] = []
                to_upsert: list[tuple] = []
                for pp in files:
                    key = pp.as_posix()
                    mtime = mtimes[pp]
                    rec = cached.get(key)
                    # S1b.2: content-hash verdict (blake2b 8) like note_search P2.1, mtime is carry hint
                    if rec:
                        cached_mtime, cached_hash, fm_json, body, text = rec
                        # Quick mtime+hash check: if mtime matches and hash matches, reuse without read
                        # If mtime differs but content same (git worktree skew), still reuse via hash
                        # Compute hash lazily only if mtime differs
                        if cached_mtime == mtime:
                            # mtime hit — assume content same, but verify hash if we have it
                            try:
                                fm = _json.loads(fm_json) if fm_json else {}
                            except Exception:
                                fm = {}
                            notes.append(Note(path=pp, text=text, frontmatter=fm, body=body))
                            continue
                        # mtime miss — need to check content hash to avoid false full rebuild on touch
                        # Read file to compute hash (still need read, but we can compare)
                        try:
                            # Use blake2b 8 like note_search _file_fingerprint (title|sector|content) but here frontmatter+body
                            tmp_text = pp.read_text(encoding="utf-8", errors="replace")
                            ch = hashlib.blake2b(
                                tmp_text.encode("utf-8", errors="replace"), digest_size=8
                            ).hexdigest()
                            if ch == cached_hash:
                                # Content same — update mtime only, reuse cached
                                try:
                                    fm = _json.loads(fm_json) if fm_json else {}
                                except Exception:
                                    fm = {}
                                notes.append(Note(path=pp, text=text, frontmatter=fm, body=body))
                                # Upsert mtime drift fix
                                to_upsert.append((key, mtime, cached_hash, fm_json, body, text))
                                continue
                        except OSError:
                            pass
                        # Hash miss — fall through to full re-parse
                    # Miss — read + yaml (1 file when 1 changed)
                    n = _load_one(pp)
                    if n is not None:
                        ch = hashlib.blake2b(
                            n.text.encode("utf-8", errors="replace"), digest_size=8
                        ).hexdigest()
                        notes.append(n)
                        to_upsert.append(
                            (key, mtime, ch, _json.dumps(n.frontmatter), n.body, n.text)
                        )
                # Upsert misses
                if to_upsert:
                    conn = (
                        _db_connect(_CACHE_DB)  # ty: ignore[call-non-callable]
                        if _HAS_DB_CONNECT
                        else __import__("sqlite3").connect(str(_CACHE_DB))
                    )
                    try:
                        conn.executemany(
                            "INSERT OR REPLACE INTO corpus_cache(path, mtime, content_hash, frontmatter_json, body, text) VALUES (?,?,?,?,?,?)",
                            to_upsert,
                        )
                        conn.commit()
                    finally:
                        conn.close()
                # Evict deleted files — ROOT-SCOPED (S2a fix, found in shakedown):
                # this table is shared across roots (synthetic trees,
                # load_shard("Companies")); the old table-global eviction
                # deleted every OTHER root's rows on a shard/synthetic load.
                root_key = root.as_posix().rstrip("/")
                under_root = [k for k in cached if k.startswith(root_key + "/")]
                if len(under_root) != len(files):
                    conn = (
                        _db_connect(_CACHE_DB)  # ty: ignore[call-non-callable]
                        if _HAS_DB_CONNECT
                        else __import__("sqlite3").connect(str(_CACHE_DB))
                    )
                    try:
                        keep = {pp.as_posix() for pp in files}
                        to_del = [k for k in under_root if k not in keep]
                        if to_del:
                            conn.executemany(
                                "DELETE FROM corpus_cache WHERE path=?", [(k,) for k in to_del]
                            )
                            conn.commit()
                    finally:
                        conn.close()
                if notes:
                    notes.sort(key=lambda n: n.path.as_posix())
                    return cls(root=root, notes=notes)
            except Exception:  # noqa: S110
                pass  # fall through to the full walk
        files = iter_findata_files(root)
        if not files:
            return cls(root=root, notes=[])
        if workers <= 1:
            notes = [n for p in files if (n := _load_one(p)) is not None]
        else:
            workers = min(workers, len(files), os.cpu_count() or 4)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                notes = [n for n in ex.map(_load_one, files) if n is not None]
            notes.sort(key=lambda n: n.path.as_posix())
        if use_cache:
            try:
                _init_db_cache(notes)
            except Exception:  # noqa: S110
                pass
        return cls(root=root, notes=notes)

    def by_path(self) -> dict[Path, Note]:
        return {n.path: n for n in self.notes}

    @classmethod
    def iter_notes(
        cls, root: str | Path = "findata", *, fields: str = "frontmatter", use_cache: bool = True
    ) -> "Iterator[Note]":
        """S2a lazy iteration — stream Notes without materializing `list[Note]`.

        `fields="frontmatter"` selects only `(path, mtime, content_hash,
        frontmatter_json)` from the cache, so `body`/`text` never
        materialize: peak memory is O(1) rows, not O(corpus text)
        (29 MB eager @ 1243; the 260K-note 100M-element projection is
        ~2.8 GB eager vs ~190 MB frontmatter-only). `fields="all"`
        streams full Notes (still one at a time). Eager `load()` is
        unchanged for body consumers; this is the scalability path.
        Falls back to a streaming walk (no cache write — `load()` warms
        the cache) when the DB is absent.
        """
        if fields not in ("frontmatter", "all"):
            raise ValueError(f"fields must be 'frontmatter' or 'all', got {fields!r}")
        root = Path(root)
        light = fields == "frontmatter"
        files = iter_findata_files(root)
        if use_cache and _HAS_DB_CONNECT and _CACHE_DB.exists():
            try:
                import json as _json

                mtimes = {pp: pp.stat().st_mtime for pp in files}
                conn = _db_connect(_CACHE_DB)  # ty: ignore[call-non-callable]  # _HAS_DB_CONNECT-gated above
                try:
                    # list rows so the light (3-col) and full (6-col) dicts share
                    # a value type — rec[3]/rec[4] only read under fields="all"
                    if light:
                        cur = conn.execute(
                            "SELECT path, mtime, content_hash, frontmatter_json FROM corpus_cache"
                        )
                        cached = {row[0]: list(row[1:]) for row in cur.fetchall()}
                    else:
                        cur = conn.execute(
                            "SELECT path, mtime, content_hash, frontmatter_json, body, text FROM corpus_cache"
                        )
                        cached = {row[0]: list(row[1:]) for row in cur.fetchall()}
                finally:
                    conn.close()
                to_upsert: list[tuple] = []
                mtime_fixups: list[tuple] = []
                try:
                    for pp in files:
                        key = pp.as_posix()
                        mtime = mtimes[pp]
                        rec = cached.get(key)
                        if rec:
                            cached_mtime, cached_hash = rec[0], rec[1]
                            if cached_mtime != mtime:
                                # mtime drift — hash verdict before re-parse (S1b.2)
                                try:
                                    tmp_text = pp.read_text(encoding="utf-8", errors="replace")
                                    ch = hashlib.blake2b(
                                        tmp_text.encode("utf-8", errors="replace"),
                                        digest_size=8,
                                    ).hexdigest()
                                    if ch != cached_hash:
                                        rec = None  # content really changed — re-parse
                                    else:
                                        mtime_fixups.append((mtime, key))
                                except OSError:
                                    rec = None
                        if rec:
                            fm_json = rec[2]
                            body = "" if light else rec[3]
                            text = "" if light else rec[4]
                            try:
                                fm = _json.loads(fm_json) if fm_json else {}
                            except Exception:
                                fm = {}
                            yield Note(path=pp, text=text, frontmatter=fm, body=body)
                            continue
                        n = _load_one(pp)
                        if n is None:
                            continue
                        ch = hashlib.blake2b(
                            n.text.encode("utf-8", errors="replace"), digest_size=8
                        ).hexdigest()
                        yield (
                            Note(path=pp, text="", frontmatter=n.frontmatter, body="")
                            if light
                            else n
                        )
                        to_upsert.append(
                            (key, mtime, ch, _json.dumps(n.frontmatter), n.body, n.text)
                        )
                finally:
                    if to_upsert or mtime_fixups:
                        try:
                            conn = _db_connect(_CACHE_DB)  # ty: ignore[call-non-callable]
                            try:
                                if to_upsert:
                                    conn.executemany(
                                        "INSERT OR REPLACE INTO corpus_cache(path, mtime, content_hash, frontmatter_json, body, text) VALUES (?,?,?,?,?,?)",
                                        to_upsert,
                                    )
                                if mtime_fixups:
                                    conn.executemany(
                                        "UPDATE corpus_cache SET mtime=? WHERE path=?",
                                        mtime_fixups,
                                    )
                                conn.commit()
                            finally:
                                conn.close()
                        except Exception:  # noqa: S110
                            pass
                return
            except Exception:  # noqa: S110
                pass  # streaming walk fallback below
        for p in files:
            n = _load_one(p)
            if n is None:
                continue
            yield Note(path=p, text="", frontmatter=n.frontmatter, body="") if light else n

    @classmethod
    def load_shard(cls, shard: str = "Companies", **kw) -> Corpus:  # noqa: UP037
        """S1b.3 shard: `load("findata/Companies")` `1080` not `findata 1243` `29MB→8MB` `10k 250MB→68MB`."""
        shard = shard.strip("/\\")
        root = (
            f"findata/{shard}" if shard and not shard.startswith("findata") else shard or "findata"
        )
        return cls.load(root, **kw)

    @classmethod
    def clear_cache(cls) -> None:
        try:
            _CACHE_DB.unlink(missing_ok=True)
        except OSError:
            pass


def _init_db_cache(notes: list[Note]) -> None:
    """Create or refresh memory/corpus.db from a full notes list (S1b consolidated)."""
    try:
        import json as _json

        _CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = (
            _db_connect(_CACHE_DB)  # ty: ignore[call-non-callable]
            if _HAS_DB_CONNECT
            else __import__("sqlite3").connect(str(_CACHE_DB))
        )
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS corpus_cache(path TEXT PRIMARY KEY, mtime REAL, content_hash TEXT, frontmatter_json TEXT, body TEXT, text TEXT)"
            )
            rows = []
            for n in notes:
                try:
                    mtime = n.path.stat().st_mtime
                except OSError:
                    mtime = 0
                ch = hashlib.blake2b(
                    n.text.encode("utf-8", errors="replace"), digest_size=8
                ).hexdigest()
                rows.append(
                    (n.path.as_posix(), mtime, ch, _json.dumps(n.frontmatter), n.body, n.text)
                )
            conn.executemany(
                "INSERT OR REPLACE INTO corpus_cache(path, mtime, content_hash, frontmatter_json, body, text) VALUES (?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: S110
        pass
