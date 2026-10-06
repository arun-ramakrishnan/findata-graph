#!/usr/bin/env python3
"""
Rebuild the `memory_search` FTS5 index over the harness memory pools.

The repo keeps durable cross-session doctrine in THREE harness memory
stores outside the tree (doc/local/engineering/consolidate_memory.md is
the layout record): the zcode per-project markdown pool (frontmatter
files + the MEMORY.md index), the prime-rlm global store (one
harness_state.json, memories under ``entries.memory``), and the opencode
simple-memory records (``.opencode/memory/<date>.logfmt``). Until now
none of them had a query CLI — a session asking "what did we settle
about X" had to read whole pools. This script gives them the
content-addressable treatment doc/ and helpers/ already have.

One FTS5 row per MEMORY RECORD — the recall unit in every harness
(zcode recalls whole files; prime/opencode inject whole records), so
per-section rows would point at a granularity no harness can use:

- zcode: every ``<scope>/memory/*.md`` in ``~/.zcode/cli/memories/``,
  scopes deduped by realpath (worktree scopes are symlinks to the
  canonical store — consolidate_memory.md §Scope-key scheme);
- prime: one row per ``entries.memory`` entry of harness_state.json;
- opencode: one row per logfmt record (``deletions.logfmt`` skipped —
  audit trail, not live doctrine).

RESIDENCE — own sidecar DB, never research.db, same doctrine as
doc_search / script_search: memory/memory_search.db is gitignored via
memory/, never snapshotted, never attached to DuckDB. Derived state;
delete it and one warm rebuild restores everything.

Usage:
    python3 helpers/maintenance/rebuild_memory_search.py            # rebuild, exit 0
    python3 helpers/maintenance/rebuild_memory_search.py --db PATH  # alternate sidecar
    python3 helpers/maintenance/rebuild_memory_search.py --check    # freshness report
    python3 helpers/maintenance/rebuild_memory_search.py --incremental

Freshness verdict: unit-level (source-file) content-hash diff, mtime only
a carry hint — the shared-index worktree lesson (2026-08-30). ``--check``
recomposes units but does NOT embed (hash verdict only — the house
--check doctrine) and exits 1 on drift. Machine-local pools are SMALL
(~30 + ~30 + ~10 records), so the full rewrite is the everyday path and
--incremental is a row-keyed courtesy diff, mirroring rebuild_script_search.

Exit codes: 0 success/fresh, 1 fatal error OR --check detected drift.
"""

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

# Repo root: helpers/maintenance/rebuild_memory_search.py -> parents[2].
# Must be on sys.path BEFORE the helpers.* imports below so the script
# works as a subprocess (make search-fresh) the same way it works under
# pytest. (House bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.embed_cache import CachedEmbed  # noqa: E402
from helpers.maintenance import rebuild_common as rbc  # noqa: E402
from helpers.maintenance import rebuild_doc_search as rds  # noqa: E402

# Monkeypatchable (the VAULT_ROOT lesson: import-bound root constants
# silently point tests at the live tree — tests MUST retarget all of
# these). ZCODE_PROJECTS is the projects DIR (scopes are walked, deduped
# by realpath); PRIME_STATE the global harness store; OPENCODE_MEM the
# repo-local logfmt dir (same through the worktree's .opencode symlink).
MEMORY_DB = _REPO_ROOT / "memory" / "memory_search.db"
BACKUP_DIR = _REPO_ROOT / "db-backup"
ZCODE_PROJECTS = Path.home() / ".zcode" / "cli" / "memories" / "projects"
PRIME_STATE = Path.home() / ".prime" / "agent" / "harness" / "harness_state.json"
OPENCODE_MEM = _REPO_ROOT / ".opencode" / "memory"

# FTS5 DDL, mirroring script_search's shape (title is the match-heavy
# column; the handles are UNINDEXED). FTS5 can't ALTER TABLE ADD COLUMN,
# so a schema change requires DROP + recreate (see _migrate_schema).
MEMORY_SEARCH_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5("
    "title, "  # 0  memory slug / prime title / opencode scope
    "kind UNINDEXED, "  # 1  'zcode' | 'prime' | 'opencode'
    "name UNINDEXED, "  # 2  slug / prime id / '<stem>#<n>'
    "source_path UNINDEXED, "  # 3  absolute locator ('path#record' off-file)
    "purpose UNINDEXED, "  # 4  frontmatter description / prime title / scope
    "content, "  # 5  body text (frontmatter stripped / record content)
    "embedding UNINDEXED, "  # 6  f32 vector for hybrid ranking; not tokenized
    "tokenize = 'porter unicode61'"
    ")"
)
_MEMORY_SEARCH_COLUMNS = {
    "title",
    "kind",
    "name",
    "source_path",
    "purpose",
    "content",
    "embedding",
}

# Per-SOURCE-FILE fingerprint (a prime/logfmt file holds many records; a
# content change re-derives all of them — trivial at this corpus size).
# blake2b(raw text) is the identity of record; mtime is only a carry hint.
MEMORY_SEARCH_META_DDL = (
    "CREATE TABLE IF NOT EXISTS memory_search_meta ("
    " unit_path TEXT PRIMARY KEY,"
    " mtime REAL NOT NULL,"
    " content_hash TEXT NOT NULL"
    ")"
)

# Model stamp home inside the sidecar (never research.db). --check never
# writes it: the stamp must describe the table's CONTENT.
MEMORY_SEARCH_INFO_DDL = (
    "CREATE TABLE IF NOT EXISTS memory_search_info ( key TEXT PRIMARY KEY, value TEXT NOT NULL)"
)

_ROW_COLS = "title, kind, name, source_path, purpose, content, embedding"

# opencode audit file: deleted records are history, not live doctrine —
# the plugin's own loader filters it the same way.
_OPENCODE_SKIP = {"deletions.logfmt"}

# logfmt ``key="quoted value"`` / ``key=bare`` scanner. The record is one
# physical line by logfmt contract (content newlines are escaped \n).
_LOGFMT_KV = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')

# The FORMAT CONTRACT for these records is owned by the external plugin
# (@knikolov/opencode-plugin-simple-memory, not opencode core): single-line
# ``key=value`` records, content always quoted, escapes exactly
# \\ -> \, \n -> LF, \r -> CR, \" -> " (plugin src/index.ts field/encodeMemory,
# v2.0.0 verified 2026-10-05). A plugin upgrade that changes the shape is the
# watch item for this leg. Layout record:
# doc/local/engineering/consolidate_memory.md §OpenCode.
_LOGFMT_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}


def connect_memory_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the memory_search sidecar via the house
    connection helper (standard pragmas: Row factory, WAL, busy_timeout)."""
    path = Path(db_path) if db_path is not None else MEMORY_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    from helpers.core.db import connect as _db_connect

    return _db_connect(path)


def _folded_fm_field(fm: str, key: str) -> str:
    """A frontmatter field whose value may fold over indented lines.

    ``description:`` in the zcode memory pool wraps mid-sentence; YAML
    folds continuation whitespace, so join with single spaces."""
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", fm, re.M)
    if m is None:
        return ""
    parts = [m.group(1).strip()]
    lines = fm[m.end() :].splitlines()
    if lines and not lines[0]:  # the newline right after the key line
        lines = lines[1:]
    for line in lines:
        if line[:1] not in (" ", "\t"):
            break
        parts.append(line.strip())
    return " ".join(p for p in parts if p)


def _split_zcode_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """(frontmatter fields of interest, body) for a zcode memory file.

    Returns ({}, text) when no frontmatter block is present (MEMORY.md)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm = text[3:end]
    body = text[end + 4 :]
    fields = {key: _folded_fm_field(fm, key) for key in ("name", "description")}
    # type lives NESTED under `metadata:` (node_type/type/originSessionId) —
    # the ^key anchor must not match it top-level, so dig it out of the
    # indented block instead.
    mtype = re.search(r"^\s+type:[ \t]*(\S+)", fm, re.M)
    fields["type"] = mtype.group(1) if mtype else ""
    return fields, body


def _abbrev_home(path: Path) -> str:
    """$HOME-abbreviated absolute path for stable source_path storage."""
    home = Path.home()
    try:
        rel = path.resolve().relative_to(home)
    except ValueError:
        return str(path.resolve())
    return f"~/{rel.as_posix()}"


def _hash_unit(raw: str) -> str:
    return hashlib.blake2b(raw.encode("utf-8", errors="replace"), digest_size=8).hexdigest()


def _extract_zcode_units(projects: Path) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """zcode rows + file meta: every ``<scope>/memory/*.md``, scopes
    deduped by realpath (the worktree scope is a symlink to the canonical
    store — realpath dedupe is what stops double-indexing)."""
    units: list[dict] = []
    meta: dict[str, tuple[float, str]] = {}
    seen_real: set[str] = set()
    if not projects.is_dir():
        return units, meta
    for scope in sorted(projects.iterdir()):
        mem = scope / "memory"
        if not mem.is_dir():
            continue
        try:
            real = str(mem.resolve())
        except OSError:
            continue
        if real in seen_real:
            continue
        seen_real.add(real)
        for p in sorted(mem.glob("*.md")):
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
                mtime = p.stat().st_mtime
            except OSError:
                continue
            fields, body = _split_zcode_frontmatter(raw)
            name = fields.get("name") or p.stem
            title = name if name != "MEMORY" else "MEMORY.md index"
            units.append(
                {
                    "kind": "zcode",
                    "name": name,
                    "title": title,
                    "purpose": fields.get("description", ""),
                    "content": (
                        f"type: {fields['type']}\n\n{body.strip()}"
                        if fields.get("type")
                        else body.strip()
                    ),
                    "source_path": _abbrev_home(p),
                    "mtime": mtime,
                    "hash": _hash_unit(raw),
                    "unit_key": _abbrev_home(p),
                }
            )
            meta[_abbrev_home(p)] = (mtime, _hash_unit(raw))
    return units, meta


def _extract_prime_units(state: Path) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """prime rows + file meta from harness_state.json ``entries.memory``."""
    meta: dict[str, tuple[float, str]] = {}
    if not state.is_file():
        return [], meta
    try:
        raw = state.read_text(encoding="utf-8", errors="replace")
        mtime = state.stat().st_mtime
        doc = json.loads(raw)
    except OSError, json.JSONDecodeError:
        return [], meta
    loc = _abbrev_home(state)
    entries = doc.get("entries") if isinstance(doc, dict) else None
    memories = entries.get("memory") if isinstance(entries, dict) else None
    units: list[dict] = []
    for mid, entry in sorted((memories or {}).items()):
        entry = entry or {}
        title = (entry.get("title") or mid).strip()
        units.append(
            {
                "kind": "prime",
                "name": mid,
                "title": title,
                "purpose": title,
                "content": (entry.get("content") or "").strip(),
                "source_path": f"{loc}#{mid}",
                "mtime": mtime,
                "hash": _hash_unit(raw),
                "unit_key": loc,
            }
        )
    meta[loc] = (mtime, _hash_unit(raw))
    return units, meta


def _parse_logfmt_line(line: str) -> dict[str, str]:
    """One logfmt record line -> {key: value} (plugin escape set decoded)."""
    out: dict[str, str] = {}
    for key, value in _LOGFMT_KV.findall(line):
        if value.startswith('"') and value.endswith('"'):
            value = re.sub(
                r"\\(.)",
                lambda m: _LOGFMT_ESCAPES.get(m.group(1), m.group(1)),
                value[1:-1],
            )
        out[key] = value
    return out


def _extract_opencode_units(memdir: Path) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """opencode rows + file meta: one row per logfmt record; deletions
    skipped (audit trail). Records carry no title — scope is the handle."""
    units: list[dict] = []
    meta: dict[str, tuple[float, str]] = {}
    if not memdir.is_dir():
        return units, meta
    for p in sorted(memdir.glob("*.logfmt")):
        if p.name in _OPENCODE_SKIP:
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            mtime = p.stat().st_mtime
        except OSError:
            continue
        loc = _abbrev_home(p)
        n = 0
        for line in raw.splitlines():
            rec = _parse_logfmt_line(line)
            if not rec.get("content"):
                continue
            n += 1
            name = f"{p.stem}#{n}"
            scope = rec.get("scope", "")
            units.append(
                {
                    "kind": "opencode",
                    "name": name,
                    "title": scope or name,
                    "purpose": scope,
                    "content": f"type: {rec.get('type', '')}\ntags: {rec.get('tags', '')}\n\n"
                    + rec["content"],
                    "source_path": f"{loc}#{n}",
                    "mtime": mtime,
                    "hash": _hash_unit(raw),
                    "unit_key": loc,
                }
            )
        meta[loc] = (mtime, _hash_unit(raw))
    return units, meta


def _collect_units(
    zcode_projects: Path | None = None,
    prime_state: Path | None = None,
    opencode_dir: Path | None = None,
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """Extract every unit from the three pools + their source-file meta.

    Roots default to the module constants (tests retarget them)."""
    units: list[dict] = []
    meta: dict[str, tuple[float, str]] = {}
    zc, m = _extract_zcode_units(Path(zcode_projects or ZCODE_PROJECTS))
    units += zc
    meta.update(m)
    pm, m = _extract_prime_units(Path(prime_state or PRIME_STATE))
    units += pm
    meta.update(m)
    oc, m = _extract_opencode_units(Path(opencode_dir or OPENCODE_MEM))
    units += oc
    meta.update(m)
    return units, meta


def _row(u: dict, embed_fn) -> tuple:
    """One FTS row tuple; embed basis = title + purpose + capped content,
    identical to the script/doc rows so the shared GC text basis holds."""
    return (
        u["title"],
        u["kind"],
        u["name"],
        u["source_path"],
        u["purpose"],
        u["content"],
        rds._embedding_f32(embed_fn, u["title"], u["purpose"], u["content"]),
    )


def _compose_rows(units: list[dict], embed_fn) -> list[tuple]:
    return [_row(u, embed_fn) for u in units]


def _migrate_schema(conn: sqlite3.Connection) -> bool:
    """Drop a stale memory_search so the new DDL applies (FTS5 can't ALTER
    TABLE ADD COLUMN; the rebuild repopulates anyway). True if dropped."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_search'"
    ).fetchone()
    if not row:
        return False
    if all(col in row[0] for col in _MEMORY_SEARCH_COLUMNS):
        return False
    conn.execute("DROP TABLE memory_search")
    return True


def _stamp_model(conn: sqlite3.Connection, model_label: str, dims: int) -> None:
    """Record the embedding model + dims in memory_search_info (apply only)."""
    conn.execute(MEMORY_SEARCH_INFO_DDL)
    conn.executemany(
        "INSERT OR REPLACE INTO memory_search_info (key, value) VALUES (?, ?)",
        [("embed_model", model_label), ("embed_dims", str(dims))],
    )


def _backup_last_good_index(db_path: Path) -> None:
    """Last-good-state recovery copy into gitignored db-backup/ after a
    successful FULL rewrite (same semantics as rebuild_script_search;
    best-effort, empty-index guarded via rebuild_common)."""
    rbc.backup_last_good_index(
        db_path,
        rbc.IndexBackupSpec(
            backup_dir=BACKUP_DIR,
            table="memory_search",
            dest_name="memory_search_backup.db",
            copier=rds._backup_file,
        ),
    )


def _stored_meta(conn: sqlite3.Connection) -> dict[str, tuple[float, str]]:
    return {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT unit_path, mtime, content_hash FROM memory_search_meta")
    }


def _write_full(
    conn: sqlite3.Connection,
    all_rows: list[tuple],
    files_meta: dict,
    model_label: str | None,
    embed_dims: int,
) -> bool:
    """Full rewrite (the convergence pass). Returns content_changed (the
    zero-churn lesson); tuple() each stored row because sqlite3.Row never
    == a plain tuple."""
    from collections import Counter

    stored = [
        tuple(r)
        for r in conn.execute(
            f"SELECT {_ROW_COLS} FROM memory_search"  # noqa: S608  # interpolates the fixed column-list constant only
        )
    ]
    content_changed = Counter(stored) != Counter(all_rows)
    with conn:
        conn.execute("DELETE FROM memory_search")
        conn.executemany(
            f"INSERT INTO memory_search ({_ROW_COLS}) "  # noqa: S608  # fixed column list
            f"VALUES (?, ?, ?, ?, ?, ?, ?)",
            all_rows,
        )
        conn.execute("DELETE FROM memory_search_meta")
        conn.executemany(
            "INSERT OR REPLACE INTO memory_search_meta "
            "(unit_path, mtime, content_hash) VALUES (?, ?, ?)",
            [(u, m, h) for u, (m, h) in sorted(files_meta.items())],
        )
        if model_label is not None:
            _stamp_model(conn, model_label, embed_dims)
    return content_changed


def _write_incremental(
    conn: sqlite3.Connection,
    all_rows: list[tuple],
    files_meta: dict,
    stored_meta: dict,
    model_label: str | None,
    embed_dims: int,
) -> tuple[int, int]:
    """Row-keyed diff write keyed on source_path (unique per record):
    only moved tuples get DELETE+INSERT; meta is refreshed for changed
    source files and GC'd for vanished ones. Returns (upserts, deletes)."""
    stored_by_path: dict[str, tuple] = {
        r[3]: tuple(r)
        for r in conn.execute(
            f"SELECT {_ROW_COLS} FROM memory_search"  # noqa: S608  # interpolates the fixed column-list constant only
        )
    }
    new_by_path = {r[3]: r for r in all_rows}
    to_delete = [p for p in stored_by_path if p not in new_by_path]
    to_upsert = [p for p, row in new_by_path.items() if stored_by_path.get(p) != row]
    changed_units = [
        u for u in files_meta if u not in stored_meta or stored_meta[u][1] != files_meta[u][1]
    ]
    with conn:
        for p in to_delete:
            conn.execute("DELETE FROM memory_search WHERE source_path = ?", (p,))
        for p in to_upsert:
            conn.execute("DELETE FROM memory_search WHERE source_path = ?", (p,))
            conn.executemany(
                f"INSERT INTO memory_search ({_ROW_COLS}) "  # noqa: S608  # fixed column list
                f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                [new_by_path[p]],
            )
        for u in stored_meta:
            if u not in files_meta:
                conn.execute("DELETE FROM memory_search_meta WHERE unit_path = ?", (u,))
        conn.executemany(
            "INSERT OR REPLACE INTO memory_search_meta "
            "(unit_path, mtime, content_hash) VALUES (?, ?, ?)",
            [(u, *files_meta[u]) for u in changed_units],
        )
        if (to_upsert or to_delete) and model_label is not None:
            _stamp_model(conn, model_label, embed_dims)
    return len(to_upsert), len(to_delete)


def rebuild(
    db_path: Path | None = None,
    write: bool = True,
    incremental: bool = False,
    embed_fn=None,
    zcode_projects: Path | None = None,
    prime_state: Path | None = None,
    opencode_dir: Path | None = None,
) -> dict:
    """Rebuild the memory_search FTS index. Returns a stats dict."""
    db_path = Path(db_path) if db_path is not None else MEMORY_DB
    conn = connect_memory_db(db_path)
    stats: dict = {}
    try:
        migrated = _migrate_schema(conn)
        conn.execute(MEMORY_SEARCH_DDL)
        conn.execute(MEMORY_SEARCH_META_DDL)
        conn.execute(MEMORY_SEARCH_INFO_DDL)
        embed_dims = rds._PSEUDO_DIMS
        model_label: str | None = None
        if embed_fn is None:
            if write:
                embed_fn, embed_dims, model_label = rds.resolve_embedder()
                stats["embed_model"] = model_label
                if model_label != f"dry-run-v{rds._PSEUDO_DIMS}":
                    embed_fn = CachedEmbed(
                        embed_fn, model_label, conn, source="memory", purge_foreign=True
                    )
            else:
                # --check: verdict is source-file content-hash — skip model
                # resolution + cache (the house --check doctrine).
                embed_fn = rds._noop_embed

        units, files_meta = _collect_units(zcode_projects, prime_state, opencode_dir)
        all_rows = _compose_rows(units, embed_fn)

        if isinstance(embed_fn, CachedEmbed):
            stats["embed_cache_hits"] = embed_fn.hits
            stats["embed_cache_misses"] = embed_fn.misses
            if embed_fn.dirty:
                # Commit cache rows NOW (the --check pre-warm lesson).
                conn.commit()

        stats["total_units"] = len(units)
        stats["total_rows"] = len(all_rows)
        stats["embedded"] = sum(1 for r in all_rows if r[6])
        by_kind: dict[str, int] = {}
        for r in all_rows:
            by_kind[r[1]] = by_kind.get(r[1], 0) + 1
        stats["by_kind"] = by_kind
        stats["migrated"] = migrated

        # Freshness verdict: source-file hash diff vs stored meta (mtime is
        # only the carry hint — the shared-index worktree lesson).
        stored_meta = _stored_meta(conn)
        stale_new = sorted(u for u in files_meta if u not in stored_meta)
        stale_deleted = sorted(u for u in stored_meta if u not in files_meta)
        stale_changed = sorted(
            u for u in files_meta if u in stored_meta and stored_meta[u][1] != files_meta[u][1]
        )
        stats["stale_new"] = stale_new
        stats["stale_changed"] = stale_changed
        stats["stale_deleted"] = stale_deleted
        stats["index_stale"] = bool(stale_new or stale_changed or stale_deleted)

        if not write:
            print(
                f"(--check mode: would index {stats['total_units']} records / "
                f"{stats['total_rows']} rows)",
                file=sys.stderr,
            )
            _print_staleness(stats)
            return stats

        if not incremental:
            stats["mode"] = "full"
            stats["content_changed"] = _write_full(
                conn, all_rows, files_meta, model_label, embed_dims
            )
            stats["indexed"] = conn.execute("SELECT COUNT(*) FROM memory_search").fetchone()[0]
            _backup_last_good_index(db_path)
            return stats

        stats["mode"] = "incremental"
        stats["upserts"], stats["deletes"] = _write_incremental(
            conn, all_rows, files_meta, stored_meta, model_label, embed_dims
        )
        stats["indexed"] = conn.execute("SELECT COUNT(*) FROM memory_search").fetchone()[0]
        return stats
    finally:
        conn.close()


def _print_staleness(stats: dict) -> None:
    """--check verdict: FRESH, or the drift breakdown + remediation."""
    rbc.print_staleness(
        stats,
        count_key="total_units",
        unit="records",
        refresh_cmd="python3 helpers/maintenance/rebuild_memory_search.py",
    )


def _summary_line(stats: dict) -> str:
    by_kind = stats.get("by_kind", {})
    breakdown = ", ".join(f"{k}={by_kind[k]}" for k in sorted(by_kind))
    return (
        f"memory_search: {stats.get('total_rows', 0)} records ({breakdown}) "
        f"({stats.get('embed_model', 'n/a')})"
    )


# --- read-path gates (memory_query CLI) --------------------------------------


def memory_index_ready(conn: sqlite3.Connection) -> bool:
    """True when the memory_search table exists (at least one rebuild ran)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_search'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def memory_index_stale(
    conn: sqlite3.Connection,
    *,
    zcode_projects: Path | None = None,
    prime_state: Path | None = None,
    opencode_dir: Path | None = None,
) -> bool:
    """True when the pools differ from memory_search_meta (file set or
    content hashes) — the read-path staleness probe. Any error counts as
    stale (safe side)."""
    try:
        meta = {
            r[0]: (r[1], r[2])
            for r in conn.execute("SELECT unit_path, mtime, content_hash FROM memory_search_meta")
        }
    except sqlite3.Error:
        return True
    if not meta:
        return True
    _units, files_meta = _collect_units(zcode_projects, prime_state, opencode_dir)
    if meta.keys() - files_meta.keys():
        return True
    # hash-exact (mtime excluded — the shared-index worktree lesson), and
    # cheap at this corpus size
    return any(u not in meta or meta[u][1] != files_meta[u][1] for u in files_meta)


def _stored_embed_dims(conn: sqlite3.Connection) -> int | None:
    """Dims of the first stored memory_search embedding, or None when empty
    (mirror of the script/doc helpers — table name hardcoded there)."""
    try:
        row = conn.execute(
            "SELECT embedding FROM memory_search "
            "WHERE embedding IS NOT NULL AND embedding != '' LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: S110  # missing table / corrupt index -> None
        return None
    if not row or not row[0]:
        return None
    from helpers.core.vec_codec import load_vec

    vec = load_vec(row[0])
    return len(vec) if vec else None


def _stored_embed_model(conn: sqlite3.Connection) -> str | None:
    """Index-side model label from memory_search_info, or None when the
    sidecar predates the stamp (dims-only back-compat, not a reject)."""
    try:
        row = conn.execute(
            "SELECT value FROM memory_search_info WHERE key = 'embed_model'"
        ).fetchone()
    except Exception:  # noqa: S110  # missing table on old sidecars -> None
        return None
    return row[0] if row else None


def _cosine_leg(
    conn: sqlite3.Connection, q: str, query_vec: rbc.QueryVector | None = None
) -> tuple[list[tuple[int, float]], dict[int, float]]:
    """Cosine ranking: (scored [(rowid, sim)] desc, sims map). ([], {}) when
    the embedder is unavailable or stored dims mismatch — the BM25 leg then
    carries the whole ranking (the house degradation contract).
    ``query_vec`` (shared_query_vector): parent-fanned-out embedding used
    instead of a local model load when its stamp matches; on mismatch the
    leg embeds locally as before."""
    try:
        idx_dims = _stored_embed_dims(conn)
        q_vec: list[float] | None = None
        if query_vec is not None:
            q_vec = rbc.check_query_vector(_stored_embed_model(conn), idx_dims, query_vec)
        if q_vec is None:
            embed_q, _dims = rds.query_embedder()
            candidate = embed_q(q)
            if idx_dims != len(candidate):
                return [], {}
            q_vec = candidate
    except Exception:  # noqa: S110  # embedder unavailable -> BM25 only
        return [], {}
    from helpers.core.vec_codec import load_vec

    sims: dict[int, float] = {}
    scored: list[tuple[int, float]] = []
    norm_q = sum(x * x for x in q_vec) ** 0.5 or 1.0
    for rid, emb in conn.execute(
        "SELECT rowid, embedding FROM memory_search WHERE embedding IS NOT NULL AND embedding != ''"
    ):
        vec = load_vec(emb)
        if not vec or len(vec) != len(q_vec):
            continue
        norm_v = sum(x * x for x in vec) ** 0.5 or 1.0
        sim = sum(a * b for a, b in zip(q_vec, vec)) / (norm_q * norm_v)
        scored.append((rid, sim))
        sims[rid] = sim
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored, sims


def search_memories(
    conn: sqlite3.Connection,
    q: str,
    limit: int = 5,
    *,
    kind: str | None = None,
    hybrid: bool = True,
    query_vec: rbc.QueryVector | None = None,
) -> dict:
    """Hybrid BM25 + cosine search over memory_search. Never raises.

    Same candidate-union + RRF design as rebuild_script_search.search_scripts
    (rds._RRF_K formula), filtered on the UNINDEXED kind column. At this
    corpus size (~70 rows) the Python cosine loop is free."""
    expr = rds.fts_match_expr(q)
    if not expr:
        return {"mode": "bm25", "results": []}
    where = "memory_search MATCH ?"
    params: list = [expr]
    if kind:
        where += " AND kind = ?"
        params.append(kind)
    try:
        page = conn.execute(
            f"SELECT rowid, title, kind, name, source_path, purpose, rank, "  # noqa: S608  # WHERE fully parameterized; f-string interpolates fixed columns only
            f"snippet(memory_search, 5, '<mark>', '</mark>', ' … ', 16) AS snip "
            f"FROM memory_search WHERE {where} "
            f"ORDER BY bm25(memory_search, 2.0, 0.0, 0.0, 0.0, 1.5, 1.0, 0.0) "
            f"LIMIT ?",
            [*params, limit],
        ).fetchall()
    except sqlite3.Error:
        return {"mode": "bm25", "results": []}

    scored, sims = _cosine_leg(conn, q, query_vec) if hybrid else ([], {})
    cos_rank = {rid: pos for pos, (rid, _s) in enumerate(scored)} if scored else None

    candidates: list[tuple[int, sqlite3.Row, str]] = [
        (pos, row, row[7]) for pos, row in enumerate(page)
    ]
    if cos_rank is not None:
        page_rids = {row[0] for row in page}
        rows_by_rid = {
            r[0]: r
            for r in conn.execute(
                "SELECT rowid, title, kind, name, source_path, purpose, content FROM memory_search"
            )
            if kind is None or r[2] == kind
        }
        extra = 0
        for rid, _sim in scored[:limit]:
            if rid in page_rids:
                continue
            row = rows_by_rid.get(rid)
            if row is None:
                continue
            head = " ".join((row[6] or "").split())[:200]
            candidates.append((len(page) + extra, row, head))
            extra += 1

    worst = len(cos_rank) if cos_rank else 0
    fused = []
    for bm25_pos, row, snippet in candidates:
        if cos_rank is not None:
            rrf = (1.0 / (rds._RRF_K + bm25_pos + 1)) + (
                1.0 / (rds._RRF_K + cos_rank.get(row[0], worst + bm25_pos) + 1)
            )
        else:
            rrf = 1.0 / (rds._RRF_K + bm25_pos + 1)
        fused.append((rrf, row, snippet))
    fused.sort(key=lambda t: t[0], reverse=True)

    results = [
        {
            "name": row[3],
            "title": row[1],
            "kind": row[2],
            "path": row[4],
            "purpose": row[5],
            "snippet": snippet,
            "score": round(rrf, 6),
            "similarity": round(sims[row[0]], 6) if row[0] in sims else None,
        }
        for rrf, row, snippet in fused[:limit]
    ]
    mode = "hybrid" if cos_rank is not None else "bm25"
    return {"mode": mode, "results": results}


def main(argv: list[str] | None = None) -> int:
    return rbc.run_rebuild_cli(
        argv,
        rbc.RebuildCliSpec(
            description=__doc__.split("\n\n")[0],
            default_db=str(MEMORY_DB),
            db_help="Path to the memory_search sidecar (default: memory/memory_search.db).",
            check_help="Dry-run: count records, report index freshness "
            "(changed/new/deleted), no writes. Exits 1 when stale.",
            incremental_help="Incremental rebuild (row-keyed diff; unchanged rows not rewritten).",
            rebuild_fn=rebuild,
            summary=_summary_line,
            migrated_msg="(schema migrated: memory_search recreated)",
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
