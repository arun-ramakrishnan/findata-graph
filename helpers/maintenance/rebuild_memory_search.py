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
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

# Repo root: helpers/maintenance/rebuild_memory_search.py -> parents[2].
# Must be on sys.path BEFORE the helpers.* imports below so the script
# works as a subprocess (make search-fresh) the same way it works under
# pytest. (House bootstrap.)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core import gemma_embedder  # noqa: E402
from helpers.core import duckdb_shared as ds  # noqa: E402  (served-store guard for the writer flock)
from helpers.core.embed_cache import CachedEmbed  # noqa: E402
from helpers.core.rank_fusion import rrf_two_leg  # noqa: E402  (algorithms_adoption_sweep S7)
from helpers.maintenance import rebuild_common as rbc  # noqa: E402
from helpers.maintenance import rebuild_doc_search as rds  # noqa: E402
from helpers.misc.duckdb_lock import connect_with_lock_retry, io_lock  # noqa: E402  (writer queue + transient ladder)

import duckdb  # noqa: E402  (duckdb_sidecar_fold S2: native-FTS memory sidecar path)

# Monkeypatchable (the VAULT_ROOT lesson: import-bound root constants
# silently point tests at the live tree — tests MUST retarget all of
# these). ZCODE_PROJECTS is the projects DIR (scopes are walked, deduped
# by realpath); PRIME_STATE the global harness store; OPENCODE_MEM the
# repo-local logfmt dir (same through the worktree's .opencode symlink).
# duckdb_sidecar_fold S2 default: the DuckDB-native store (native FTS +
# inline embeddings). The legacy FTS5 sidecar stays openable via --db
# (rollback path; both engines are tested — tests/test_memory_query_duckdb.py
# keeps the duckdb contract, tests/test_memory_snapshot_lane.py the legacy one).
MEMORY_DB = _REPO_ROOT / "memory" / "memory_search.duckdb"
MEMORY_DUCKDB_DB = MEMORY_DB
_MEMORY_SQLITE_DB = _REPO_ROOT / "memory" / "memory_search.db"
ZCODE_PROJECTS = Path.home() / ".zcode" / "cli" / "memories" / "projects"
PRIME_STATE = Path.home() / ".prime" / "agent" / "harness" / "harness_state.json"
OPENCODE_MEM = _REPO_ROOT / ".opencode" / "memory"
# Timeshift backup mount — the SNAPSHOT-HISTORY lane's only source. It is a
# backup mount: ABSENT IS NORMAL (never an error, never drift), so every
# extractor below returns empty when it is not there.
SNAP_ROOT = Path("/mnt/store/timeshift/snapshots")

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

# Snapshot-history kinds: doctrine recovered from timeshift images. They exist
# in the same table as the live pools but must NOT win a live recall by
# default — "what do we believe now" is the everyday question. `--history`
# includes them; `--history-only` asks "was this ever true" without borrowing
# today's answer.
_HISTORY_KINDS = ("zcode-snap", "prime-snap", "opencode-snap")
_HISTORY_KIND_SET = frozenset(_HISTORY_KINDS)
_LIVE_KINDS = ("zcode", "prime", "opencode")

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


def connect_memory_db(db_path: Path | str | None = None):
    """Open (creating if needed) the memory_search sidecar.

    Engine dispatch (duckdb_sidecar_fold S2): a ``.duckdb`` suffix opens a
    DuckDB-native store (native FTS + inline embeddings, house memory cap);
    anything else opens the legacy FTS5 sidecar via the house sqlite helper
    (standard pragmas: Row factory, WAL, busy_timeout)."""
    path = Path(db_path) if db_path is not None else MEMORY_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".duckdb":
        from helpers.core.duckdb_shared import memory_cap_config
        from helpers.misc.duckdb_lock import (
            _EXTENDED_RETRY_DELAYS_S,
            connect_with_lock_retry,
        )

        # Queue behind a live holder instead of asserting (the read-only
        # sibling below already does; the build/reopen path must too).
        return connect_with_lock_retry(
            lambda: duckdb.connect(str(path), **memory_cap_config()),
            delays=_EXTENDED_RETRY_DELAYS_S,
        )

    from helpers.core.db import connect as _db_connect

    return _db_connect(path)


def connect_memory_read_only(db_path: Path | str | None = None):
    """Reader entry for the memory_search sidecar (duckdb_long_holder_backoff).

    Routes the DuckDB-native store through the shared retry+flock entry with
    the 60 s / 3-retry ladder, so a reader arriving while a sidecar rebuild
    holds the file waits instead of failing with ``Conflicting lock is held``.
    The legacy FTS5 sidecar uses house sqlite (WAL/busy_timeout already set).
    An ABSENT store answers with an empty in-memory handle (no table →
    ``ready_fn`` False → the CLI's "not built" hint), because a missing
    sidecar is a normal state here, not a crash. Writers keep using
    :func:`connect_memory_db` with the extended ladder (see
    :func:`_memory_writer_conn`).
    """
    # An in-memory scratch handle has no table, so ready_fn answers False
    # ("not built") — the shared query-CLI idiom for an absent sidecar.
    path = Path(db_path) if db_path is not None else MEMORY_DB
    if path.suffix.lower() == ".duckdb":
        if not path.exists():
            return duckdb.connect(":memory:", **ds.memory_cap_config())
        from helpers.misc.duckdb_lock import (
            _EXTENDED_RETRY_DELAYS_S,
            open_read_only,
        )

        return open_read_only(path, delays=_EXTENDED_RETRY_DELAYS_S)
    from helpers.core.db import connect as _db_connect

    if not path.exists():
        return _db_connect(":memory:")
    return _db_connect(path, read_only=True, wal=False)


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


def _extract_zcode_units(
    projects: Path,
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
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


def _extract_prime_units(
    state: Path,
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """prime rows + file meta from harness_state.json ``entries.memory``."""
    meta: dict[str, tuple[float, str]] = {}
    if not state.is_file():
        return [], meta
    try:
        raw = state.read_text(encoding="utf-8", errors="replace")
        mtime = state.stat().st_mtime
        doc = jsonio.loads(raw)
    except OSError, jsonio.JSONDecodeError:
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


def _extract_opencode_units(
    memdir: Path,
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
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


# --- snapshot-history lane -------------------------------------------------
# Doctrine that has ROTATED OUT of the live pools survives only in the
# timeshift images. It is history, not live doctrine, so it goes in the SAME
# sidecar under distinct kinds (`zcode-snap` / `prime-snap` /
# `opencode-snap`) rather than a separate index: "what do we believe now"
# and "what did we believe on 2026-08-26" stay one query apart.
#
# DEDUPE IS THE WHOLE DESIGN. Six images carrying the same unchanged record
# would otherwise yield six identical rows. Each history unit is keyed by the
# CONTENT hash of the record it carries, so:
#   - a record byte-identical to its live version produces NO history row
#     (asking "was this ever true" about today's text is noise);
#   - a record present unchanged across N images produces ONE row whose
#     `seen` stamps list all N images (provenance without duplication).
#
# The row keeps the OLDEST stamp the content was seen at: "how far back does
# this go" is the question the lane exists to answer.


def _snap_stamps(root: Path) -> list[str]:
    """Visible image stamps, oldest first. Empty when the mount is absent —
    the backup mount is routinely unmounted, and that is NORMAL."""
    if not root.is_dir():
        return []
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def _snap_ts(path: Path, root: Path) -> str:
    """Image stamp of a path under the mount ("" when it is not under it)."""
    try:
        return path.relative_to(root).parts[0]
    except ValueError:
        return ""


def _snap_home(root: Path, stamp: str) -> Path:
    """The image's $HOME: timeshift stores <root>/<stamp>/localhost/home/<user>."""
    return root / stamp / "localhost" / "home" / Path.home().name


def _history_unit(
    *,
    kind: str,
    name: str,
    title: str,
    purpose: str,
    content: str,
    source_path: str,
    stamp: str,
    mtime: float,
) -> dict:
    """One snapshot-history unit. `hash` is the CONTENT identity (not the
    file's): dedupe on it is what keeps six images from six rows."""
    return {
        "kind": kind,
        "name": name,
        "title": title,
        "purpose": purpose,
        "content": content,
        "source_path": source_path,
        "mtime": mtime,
        "hash": _hash_unit(content),
        "unit_key": source_path,
    }


def _dedupe_history(
    units: list[dict], live_hashes: set[str]
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """Drop rows whose content is live unchanged, merge repeats across
    images, and return (units, source-file meta).

    Two content versions of the SAME name both survive (that is the point:
    the doctrine changed, and both states are historically true); they are
    told apart by their source_path stamp."""
    by_key: dict[str, tuple[dict, set[str]]] = {}
    for unit in units:
        if unit["hash"] in live_hashes:
            continue
        key = f"{unit['kind']}|{unit['name']}|{unit['hash']}"
        kept = by_key.get(key)
        if kept is None:
            by_key[key] = (unit, {unit["_stamp"]})
            continue
        row, stamps = kept
        stamps.add(unit["_stamp"])
        if unit["_stamp"] < row["_stamp"]:
            row["_stamp"] = unit["_stamp"]
            row["source_path"] = unit["source_path"]
            row["mtime"] = unit["mtime"]
    out: list[dict] = []
    meta: dict[str, tuple[float, str]] = {}
    for row, stamps in by_key.values():
        ordered = sorted(stamps)
        seen = ", ".join(ordered) if len(ordered) <= 3 else f"{ordered[0]} … {ordered[-1]}"
        tail = f"seen {seen} in {len(ordered)} image(s)"
        row["purpose"] = f"{tail} — {row['purpose']}" if row["purpose"] else tail
        row.pop("_stamp", None)
        out.append(row)
        meta[row["source_path"]] = (row["mtime"], row["hash"])
    return out, meta


def _live_pool_hashes(
    zcode_projects: Path, prime_state: Path, opencode_dir: Path
) -> dict[str, set[str]]:
    """Per-pool content hashes of TODAY'S live records: the dedupe basis.

    A snapshot record byte-identical to a live record is not history — it is
    today's doctrine, and the live row already says so."""
    units, _meta = _collect_units(zcode_projects, prime_state, opencode_dir)
    out: dict[str, set[str]] = {"zcode": set(), "prime": set(), "opencode": set()}
    for unit in units:
        out[unit["kind"]].add(_hash_unit(unit["content"]))
    return out


def _extract_zcode_history(
    projects: Path, root: Path, live_hashes: set[str]
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """zcode doctrine versions that exist only inside timeshift images.

    Scopes are deduped by realpath exactly like the live extractor, and a
    scope whose memory dir resolves OUTSIDE the image (the 2026-11-07 image
    carries live-pointing symlinks) contributes NOTHING: reading through such
    a link would re-index the live pool as history."""
    units: list[dict] = []
    seen_real: set[str] = set()
    for stamp in _snap_stamps(root):
        projects_dir = _snap_home(root, stamp) / ".zcode" / "cli" / "memories" / "projects"
        if not projects_dir.is_dir():
            continue
        for scope in sorted(projects_dir.iterdir()):
            mem = scope / "memory"
            if not mem.is_dir():
                continue
            try:
                real = str(mem.resolve())
            except OSError:
                continue
            if not real.startswith(str(root)) or real in seen_real:
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
                content = (
                    f"type: {fields['type']}\n\n{body.strip()}"
                    if fields.get("type")
                    else body.strip()
                )
                unit = _history_unit(
                    kind="zcode-snap",
                    name=name,
                    title=name if name != "MEMORY" else "MEMORY.md index",
                    purpose=fields.get("description", ""),
                    content=content,
                    source_path=_abbrev_home(p),
                    stamp=stamp,
                    mtime=mtime,
                )
                unit["_stamp"] = stamp
                units.append(unit)
    return _dedupe_history(units, live_hashes)


def _extract_prime_history(
    state: Path, root: Path, live_hashes: set[str]
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """prime-rlm `entries.memory` entries recoverable only from images."""
    units: list[dict] = []
    for stamp in _snap_stamps(root):
        snap_state = _snap_home(root, stamp) / ".prime" / "agent" / "harness" / "harness_state.json"
        if not snap_state.is_file():
            continue
        try:
            raw = snap_state.read_text(encoding="utf-8", errors="replace")
            mtime = snap_state.stat().st_mtime
            doc = jsonio.loads(raw)
        except OSError, jsonio.JSONDecodeError:
            continue
        entries = doc.get("entries") if isinstance(doc, dict) else None
        memories = entries.get("memory") if isinstance(entries, dict) else None
        for mid, entry in sorted((memories or {}).items()):
            entry = entry or {}
            content = (entry.get("content") or "").strip()
            if not content:
                continue
            title = (entry.get("title") or mid).strip()
            unit = _history_unit(
                kind="prime-snap",
                name=mid,
                title=title,
                purpose=title,
                content=content,
                source_path=f"{_abbrev_home(snap_state)}#{mid}",
                stamp=stamp,
                mtime=mtime,
            )
            unit["_stamp"] = stamp
            units.append(unit)
    return _dedupe_history(units, live_hashes)


def _extract_opencode_history(
    memdir: Path, root: Path, live_hashes: set[str]
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """opencode logfmt records recoverable only from images.

    The live pool sits INSIDE the repo, so the images carry it under the
    worktree path; deletions.logfmt is skipped there too (audit trail)."""
    units: list[dict] = []
    try:
        rel = memdir.relative_to(Path.home())
    except ValueError:
        return [], {}
    for stamp in _snap_stamps(root):
        snap_dir = _snap_home(root, stamp) / rel
        if not snap_dir.is_dir():
            continue
        for p in sorted(snap_dir.glob("*.logfmt")):
            if p.name in _OPENCODE_SKIP:
                continue
            try:
                raw = p.read_text(encoding="utf-8", errors="replace")
                mtime = p.stat().st_mtime
            except OSError:
                continue
            for line in raw.splitlines():
                rec = _parse_logfmt_line(line)
                if not rec.get("content"):
                    continue
                content = (
                    f"type: {rec.get('type', '')}\ntags: {rec.get('tags', '')}\n\n" + rec["content"]
                )
                scope = rec.get("scope", "")
                unit = _history_unit(
                    kind="opencode-snap",
                    # Name carries NO stamp: the dedupe key is
                    # kind|name|content-hash, and a stamped name would keep
                    # six images of one unchanged record as six rows.
                    name=f"{p.stem}#{_hash_unit(content)[:8]}",
                    title=scope or p.stem,
                    purpose=scope,
                    content=content,
                    source_path=f"{_abbrev_home(p)}#{_hash_unit(content)[:8]}",
                    stamp=stamp,
                    mtime=mtime,
                )
                unit["_stamp"] = stamp
                units.append(unit)
    return _dedupe_history(units, live_hashes)


def _collect_history_units(
    zcode_projects: Path | None = None,
    prime_state: Path | None = None,
    opencode_dir: Path | None = None,
    snap_root: Path | None = None,
) -> tuple[list[dict], dict[str, tuple[float, str]]]:
    """Snapshot-history units across the three doctrine pools.

    Absent mount => no rows and no meta, silently: the timeshift root is a
    backup mount and its absence is normal, NOT drift."""
    root = Path(snap_root or SNAP_ROOT)
    if not root.is_dir():
        return [], {}
    zcode_projects = Path(zcode_projects or ZCODE_PROJECTS)
    prime_state = Path(prime_state or PRIME_STATE)
    opencode_dir = Path(opencode_dir or OPENCODE_MEM)
    live = _live_pool_hashes(zcode_projects, prime_state, opencode_dir)
    units: list[dict] = []
    meta: dict[str, tuple[float, str]] = {}
    for extract, pool in (
        (_extract_zcode_history, "zcode"),
        (_extract_prime_history, "prime"),
        (_extract_opencode_history, "opencode"),
    ):
        target = {
            "zcode": zcode_projects,
            "prime": prime_state,
            "opencode": opencode_dir,
        }[pool]
        got, m = extract(target, root, live[pool])
        units += got
        # meta keys carry the snap:: prefix so a reader can tell a history
        # source file from a live one without re-deriving the lane.
        meta.update({f"snap::{k}": v for k, v in m.items()})
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


MEMORY_EMBEDDER_ENV = "MEMORY_EMBEDDER"


def resolve_memory_embedder() -> tuple:
    """(embed_fn, dims, model_label, gemma_active) for the memory surface.

    memory_search gemma migration (2026-10-08): gemma whenever the
    sidecar/model is available, granite otherwise — mirroring
    resolve_script_embedder. A gemma-stamped index refuses the fallback
    at the write path (helpers.core.gemma_embedder.guard_gemma_stamp).
    MEMORY_EMBEDDER overrides: `granite` forces the shared in-process
    path (the demotion escape), `gemma` forces gemma.
    """
    want = os.environ.get(MEMORY_EMBEDDER_ENV, "").strip().lower()
    if want == "granite":
        fn, dims, label = rds.resolve_embedder()
        return fn, dims, label, False
    if gemma_embedder.available():
        return (
            gemma_embedder.embed_document,
            gemma_embedder.DIM,
            gemma_embedder.MODEL_LABEL,
            True,
        )
    if want == "gemma":
        print(
            "WARNING: MEMORY_EMBEDDER=gemma but the gemma sidecar/model is "
            "unavailable — falling back to the granite path",
            file=sys.stderr,
            flush=True,
        )
    fn, dims, label = rds.resolve_embedder()
    return fn, dims, label, False


def _query_embedder_for(stored_model: str | None) -> tuple:
    """Stamp-keyed query embedder (the script adoption's pattern): a
    gemma-stamped memory index embeds queries with the gemma sidecar
    (search-task prefix); anything else keeps the shared granite query
    embedder. Sidecar down -> granite 384 -> dims mismatch -> BM25-only
    (the house degradation contract, never mixed-model scores)."""
    if stored_model == gemma_embedder.MODEL_LABEL and gemma_embedder.available():
        return gemma_embedder.embed_query, gemma_embedder.DIM
    return rds.query_embedder()


def _row(u: dict, embed_fn, gemma: bool = False) -> tuple:
    """One FTS row tuple; embed basis = title + purpose + capped content,
    identical to the script/doc rows so the shared GC text basis holds.
    gemma=True wraps the SAME title/purpose/content in the gemma doc-side
    prefix form (shared recipe with rebuild_script_search._gemma_basis) —
    the prefix is load-bearing (1.000 -> 0.880 without it)."""
    if gemma:
        from helpers.maintenance.rebuild_script_search import _gemma_basis

        try:
            vec = embed_fn(_gemma_basis(u["title"], u["purpose"], u["content"]))
        except Exception:  # noqa: S110  # best-effort; missing rows stay searchable
            vec = None
        from helpers.core.vec_codec import pack_f32

        emb = pack_f32(vec) if vec else None
        return (
            u["title"],
            u["kind"],
            u["name"],
            u["source_path"],
            u["purpose"],
            u["content"],
            emb,
        )
    return (
        u["title"],
        u["kind"],
        u["name"],
        u["source_path"],
        u["purpose"],
        u["content"],
        rds._embedding_f32(embed_fn, u["title"], u["purpose"], u["content"]),
    )


def _compose_rows(units: list[dict], embed_fn, gemma: bool = False) -> list[tuple]:
    return [_row(u, embed_fn, gemma=gemma) for u in units]


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


def _stored_meta(conn) -> dict[str, tuple[float, str]]:
    return {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT unit_path, mtime, content_hash FROM memory_search_meta"
        ).fetchall()  # fetchall: duckdb conns are not row-iterable (S1 lesson)
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


def _resolve_embedder(conn, write: bool, stats: dict, embed_fn=None) -> tuple:
    """Embedder + dims + model label + gemma flag for a rebuild run
    (helper keeps rebuild()'s decision count down — C901)."""
    if embed_fn is not None:
        return embed_fn, rds._PSEUDO_DIMS, None, False
    if write:
        resolved, embed_dims, model_label, gemma_active = resolve_memory_embedder()
        stats["embed_model"] = model_label
        # Top-level demotion guard (helpers.core.gemma_embedder):
        # a gemma-stamped memory index is never silently
        # re-embedded by the fallback model — that exact accident
        # un-migrated the script stamp twice. MEMORY_EMBEDDER=
        # granite is the explicit escape.
        gemma_embedder.guard_gemma_stamp(
            _stored_embed_model(conn),
            model_label,
            os.environ.get(MEMORY_EMBEDDER_ENV),
        )
        if model_label != f"dry-run-v{rds._PSEUDO_DIMS}":
            # No automatic model-difference purge (2026-10-08): a
            # prior-model row is rollback insurance, dead-text
            # eviction is `make embed-gc`.
            resolved = CachedEmbed(resolved, model_label, conn, source="memory")
        return resolved, embed_dims, model_label, gemma_active
    # --check: verdict is source-file content-hash — skip model
    # resolution + cache (the house --check doctrine).
    return rds._noop_embed, rds._PSEUDO_DIMS, None, False


def _source_staleness(conn, files_meta: dict) -> tuple[dict, dict]:
    """Freshness verdict: source-file hash diff vs stored meta (mtime is
    only the carry hint — the shared-index worktree lesson)."""
    stored_meta = _stored_meta(conn)
    stale_new = sorted(u for u in files_meta if u not in stored_meta)
    stale_deleted = sorted(u for u in stored_meta if u not in files_meta)
    stale_changed = sorted(
        u for u in files_meta if u in stored_meta and stored_meta[u][1] != files_meta[u][1]
    )
    stats_part = {
        "stale_new": stale_new,
        "stale_changed": stale_changed,
        "stale_deleted": stale_deleted,
        "index_stale": bool(stale_new or stale_changed or stale_deleted),
    }
    return stats_part, stored_meta


def rebuild(
    db_path: Path | None = None,
    write: bool = True,
    incremental: bool = False,
    embed_fn=None,
    zcode_projects: Path | None = None,
    prime_state: Path | None = None,
    opencode_dir: Path | None = None,
    snap_root: Path | None = None,
) -> dict:
    """Rebuild the memory_search FTS index. Returns a stats dict.

    The snapshot-history lane rides along: `snap_root` defaults to SNAP_ROOT,
    and an absent mount simply contributes nothing."""

    from datetime import datetime

    from helpers.maintenance.maint_timing import current as _mt_timer

    db_path = Path(db_path) if db_path is not None else MEMORY_DB
    if db_path.suffix.lower() == ".duckdb":
        # duckdb_rebuild_incremental_carry S3: the DuckDB-native store
        # carries a row-keyed delta; --full forces the replacement rebuild.
        return rebuild_memory_duckdb(
            db_path,
            write=write,
            incremental=incremental,
            embed_fn=embed_fn,
            zcode_projects=zcode_projects,
            prime_state=prime_state,
            opencode_dir=opencode_dir,
            snap_root=snap_root,
        )

    conn = connect_memory_db(db_path)
    stats: dict = {}
    _mt = _mt_timer()  # None outside a CLI run (tests): all timing below is a no-op
    try:
        migrated = _migrate_schema(conn)
        conn.execute(MEMORY_SEARCH_DDL)
        conn.execute(MEMORY_SEARCH_META_DDL)
        conn.execute(MEMORY_SEARCH_INFO_DDL)
        model_label: str | None = None
        embed_fn, embed_dims, model_label, gemma_active = _resolve_embedder(
            conn, write, stats, embed_fn
        )

        units, files_meta, all_rows, hist_units = _collect_and_compose(
            zcode_projects,
            prime_state,
            opencode_dir,
            snap_root,
            embed_fn,
            gemma_active,
            _mt,
        )

        if isinstance(embed_fn, CachedEmbed):
            stats["embed_cache_hits"] = embed_fn.hits
            stats["embed_cache_misses"] = embed_fn.misses
            if embed_fn.dirty:
                # Commit cache rows NOW (the --check pre-warm lesson).
                conn.commit()

        stats["total_units"] = len(units)
        stats["total_rows"] = len(all_rows)
        stats["history_units"] = len(hist_units)
        stats["history_snapshots"] = len(_snap_stamps(Path(snap_root or SNAP_ROOT)))
        stats["embedded"] = sum(1 for r in all_rows if r[6])
        by_kind: dict[str, int] = {}
        for r in all_rows:
            by_kind[r[1]] = by_kind.get(r[1], 0) + 1
        stats["by_kind"] = by_kind
        stats["migrated"] = migrated

        stats_part, stored_meta = _source_staleness(conn, files_meta)
        stats.update(stats_part)

        if not write:
            print(
                f"(--check mode: would index {stats['total_units']} records / "
                f"{stats['total_rows']} rows)",
                file=sys.stderr,
            )
            _print_staleness(stats)
            return stats

        _write_started = datetime.now()
        if not incremental:
            stats["mode"] = "full"
            stats["content_changed"] = _write_full(
                conn, all_rows, files_meta, model_label, embed_dims
            )
            stats["indexed"] = conn.execute("SELECT COUNT(*) FROM memory_search").fetchone()[0]
            if _mt:
                _mt.record_phase("write", _write_started, datetime.now())
            return stats

        stats["mode"] = "incremental"
        stats["upserts"], stats["deletes"] = _write_incremental(
            conn, all_rows, files_meta, stored_meta, model_label, embed_dims
        )
        if stats["upserts"] or stats["deletes"]:
            # S3 (duckdb_rebuild_incremental_carry): the native FTS shadow
            # is one-shot - plain DML leaves it stale and lexical search
            # would silently miss the delta until the next --full.
            rbc.refresh_fts_shadow(conn, "memory_search")
        stats["indexed"] = conn.execute("SELECT COUNT(*) FROM memory_search").fetchone()[0]
        if _mt:
            _mt.record_phase("write", _write_started, datetime.now())
        return stats
    finally:
        conn.close()


def _collect_and_compose(
    zcode_projects, prime_state, opencode_dir, snap_root, embed_fn, gemma_active, _mt
):
    """Collect live + snapshot-history units, then compose/embed all rows.
    ``_mt`` is the maint-timing handle (None outside a CLI run: timing
    no-ops). Returns (units, files_meta, all_rows, hist_units)."""
    from datetime import datetime

    _collect_started = datetime.now()
    units, files_meta = _collect_units(zcode_projects, prime_state, opencode_dir)
    hist_units, hist_meta = _collect_history_units(
        zcode_projects, prime_state, opencode_dir, snap_root
    )
    units += hist_units
    files_meta.update(hist_meta)
    if _mt:
        _mt.record_phase("collect", _collect_started, datetime.now(), extra=f"units={len(units)}")
    _compose_started = datetime.now()
    all_rows = _compose_rows(units, embed_fn, gemma=gemma_active)
    if _mt:
        _mt.record_phase(
            "compose_embed",
            _compose_started,
            datetime.now(),
            extra=f"rows={len(all_rows)}",
        )
    return units, files_meta, all_rows, hist_units


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
    hist = stats.get("history_units", 0)
    hist_note = f", {hist} snapshot-history record(s)" if hist else ""
    return (
        f"memory_search: {stats.get('total_rows', 0)} records ({breakdown}){hist_note} "
        f"({stats.get('embed_model', 'n/a')})"
    )


# --- read-path gates (memory_query CLI) --------------------------------------


def memory_index_ready(conn) -> bool:
    """True when the memory_search table exists (at least one rebuild ran)."""
    try:
        if isinstance(conn, duckdb.DuckDBPyConnection):
            row = conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name = 'memory_search'"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_search'"
            ).fetchone()
    except Exception:  # noqa: S110  # engine error / absent store -> not ready
        return False
    return row is not None


def memory_index_stale(
    conn: sqlite3.Connection,
    *,
    zcode_projects: Path | None = None,
    prime_state: Path | None = None,
    opencode_dir: Path | None = None,
    snap_root: Path | None = None,
) -> bool:
    """True when the pools differ from memory_search_meta (file set or
    content hashes) — the read-path staleness probe. Any error counts as
    stale (safe side)."""
    try:
        meta = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                "SELECT unit_path, mtime, content_hash FROM memory_search_meta"
            ).fetchall()
        }
    except Exception:  # sqlite3.Error or duckdb.CatalogException: stale (safe side)
        return True
    if not meta:
        return True
    _units, files_meta = _collect_units(zcode_projects, prime_state, opencode_dir)
    # The snapshot-history lane participates in the staleness verdict ONLY as a
    # REMOVAL probe. It may legitimately be empty (mount absent — a backup
    # mount, not an error), so an ABSENT mount must never mark the index stale;
    # while a history row that vanished from a PRESENT mount must. Snapshots are
    # immutable, so no history meta key is ever "changed" — only new or gone.
    root = Path(snap_root or SNAP_ROOT)
    _hist_units, hist_meta = _collect_history_units(zcode_projects, prime_state, opencode_dir, root)
    files_meta.update(hist_meta)
    removed = meta.keys() - files_meta.keys()
    if not root.is_dir():
        # Lane idle, not deleted: keep stored snap:: rows out of the verdict.
        removed = {k for k in removed if not k.startswith("snap::")}
    if removed:
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
            embed_q, _dims = _query_embedder_for(_stored_embed_model(conn))
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


def _kind_ok(value: str, kind: str | None, history: str) -> bool:
    """Kind predicate for the history axis; an explicit kind wins over it."""
    if kind:
        return value == kind
    if history == "only":
        return value in _HISTORY_KIND_SET
    if history == "exclude":
        return value not in _HISTORY_KIND_SET
    return True


def _memory_where(expr: str, kind: str | None, history: str) -> tuple[str, list]:
    """FTS MATCH plus the kind filter. An explicit kind wins over history."""
    where = "memory_search MATCH ?"
    params: list = [expr]
    if kind:
        where += " AND kind = ?"
        params.append(kind)
    elif history in ("only", "exclude"):
        placeholders = ", ".join("?" * len(_HISTORY_KINDS))
        where += f" AND kind {'IN' if history == 'only' else 'NOT IN'} ({placeholders})"  # noqa: S608  # fixed keyword + fixed placeholder count
        params.extend(_HISTORY_KINDS)
    return where, params


def _memory_candidates(
    conn: sqlite3.Connection,
    page: list[sqlite3.Row],
    scored: list[tuple[int, float]],
    cos_rank: dict[int, int] | None,
    kind: str | None,
    history: str,
    limit: int,
) -> list[tuple[int, sqlite3.Row, str]]:
    """BM25 page plus cosine-only rows, as (rank, row, snippet-head) triples."""
    candidates: list[tuple[int, sqlite3.Row, str]] = [
        (pos, row, row[7]) for pos, row in enumerate(page)
    ]
    if cos_rank is None:
        return candidates
    page_rids = {row[0] for row in page}
    rows_by_rid = {
        r[0]: r
        for r in conn.execute(
            "SELECT rowid, title, kind, name, source_path, purpose, content FROM memory_search"
        )
        if _kind_ok(r[2], kind, history)
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
    return candidates


def search_memories(
    conn: sqlite3.Connection,
    q: str,
    limit: int = 5,
    *,
    kind: str | None = None,
    hybrid: bool = True,
    query_vec: rbc.QueryVector | None = None,
    history: str = "exclude",
) -> dict:
    """Hybrid BM25 + cosine search over memory_search. Never raises.

    Same candidate-union + RRF design as rebuild_script_search.search_scripts
    (the shared two-leg RRF formula), filtered on the UNINDEXED kind column.

    ``history`` governs the snapshot-history kinds (zcode-snap / prime-snap /
    opencode-snap): "exclude" (default) answers "what do we believe NOW",
    "include" adds the recovered doctrine, "only" asks "was this ever true".
    An explicit ``kind`` wins over ``history`` — naming a kind is unambiguous.
    """
    if isinstance(conn, duckdb.DuckDBPyConnection):
        return _search_memory_duckdb(
            conn, q, limit, kind=kind, hybrid=hybrid, query_vec=query_vec, history=history
        )
    expr = rds.fts_match_expr(q)
    if not expr:
        return {"mode": "bm25", "results": []}
    where, params = _memory_where(expr, kind, history)
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
    candidates = _memory_candidates(conn, page, scored, cos_rank, kind, history, limit)
    worst = len(cos_rank) if cos_rank else 0
    fused = rrf_two_leg(candidates, cos_rank, worst)

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


# ---------------------------------------------------------------------------
# DuckDB-native sidecar (duckdb_sidecar_fold S2, 2026-11-15)
#
# Native FTS (fts_main_memory_search.match_bm25) over a composed searchtext
# (title + purpose + content — the legacy bm25 weight ordering); kind/name/
# source_path become plain SQL columns (better filters than the legacy
# UNINDEXED FTS5 columns). The doc S1 lessons apply unchanged: native
# duckdb cache handle (embed-store fold S0), replacement rebuild, no
# snippet()/highlight() (rds._highlight_window replacement).
MEMORY_DUCKDB_DDL = """
CREATE TABLE IF NOT EXISTS memory_search (
  id BIGINT PRIMARY KEY,
  title TEXT,
  kind TEXT,
  name TEXT,
  source_path TEXT,
  purpose TEXT,
  content TEXT,
  embedding BLOB,
  searchtext TEXT
)
"""
MEMORY_DUCKDB_META_DDL = """
CREATE TABLE IF NOT EXISTS memory_search_meta (
  unit_path TEXT PRIMARY KEY,
  mtime REAL NOT NULL,
  content_hash TEXT NOT NULL
)
"""
MEMORY_DUCKDB_INFO_DDL = """
CREATE TABLE IF NOT EXISTS memory_search_info ( key TEXT PRIMARY KEY, value TEXT NOT NULL)
"""


def _compose_memory_searchtext(row: tuple) -> str:
    """Composed native-FTS field: title first (the legacy 2.0 weight), then
    purpose (1.5), then content (1.0)."""
    title, _kind, _name, _source_path, purpose, content, _emb = row
    return " ".join(s for s in (title, purpose, content) if s)


@contextmanager
def _memory_writer_conn(db_path: Path):
    """Writer-window coordination for the memory_search sidecar rebuild.

    The sidecar rebuild holds its DuckDB connection across the whole
    window (DDL + embed + delta write, ~2 s warm). Without coordination a
    concurrent reader's open fails with ``Conflicting lock is held`` and
    the writer's own open races any lifetime reader. Mirroring the convo
    rebuild: an UNSERVED store takes ``LOCK_EX`` on ``<db>.io.lock``
    across the window so readers queue on their ``LOCK_SH`` instead of
    failing, and the open itself retries the transient ladder for
    holders that never took the flock (a manual CLI, a db_maint backup).
    A SERVED store does not participate in the flock (the quack server
    serializes writes), so the queue is skipped and the plain open stays.
    """
    if ds.is_served(db_path):
        yield connect_memory_db(db_path)
        return
    with io_lock(db_path, exclusive=True):
        # The writer's open retries the transient ladder for holders that
        # never took the flock. The default 1.55 s ladder is right for
        # sub-second holds; the extended 60 s schedule (duckdb_long_holder_
        # backoff) covers the manual-CLI / db_maint-backup case the convo
        # rebuild also faces, so a writer waits out a long foreign holder
        # instead of failing after 1.55 s.
        from helpers.misc.duckdb_lock import _EXTENDED_RETRY_DELAYS_S

        yield connect_with_lock_retry(
            lambda: connect_memory_db(db_path),
            delays=_EXTENDED_RETRY_DELAYS_S,
        )


def rebuild_memory_duckdb(
    db_path: Path | str | None = None,
    *,
    write: bool = True,
    embed_fn=None,
    zcode_projects: Path | None = None,
    prime_state: Path | None = None,
    opencode_dir: Path | None = None,
    snap_root: Path | None = None,
    incremental: bool = True,
) -> dict:
    """Build/rebuild the DuckDB-native memory_search store (S2).

    Reuses the engine-agnostic extraction pipeline (``_collect_units`` +
    ``_collect_history_units`` + ``_compose_rows``) and the pooled embed
    cache via the native duckdb handle (``rds.CachedEmbed`` — the
    duckdb host conn IS the cache conn, embed-store fold S0).
    ``incremental`` (the CLI default via rebuild_common —
    duckdb_rebuild_incremental_carry S3) writes a row-keyed delta: rows
    are recomposed for everyone (cross-file inputs) but only titles whose
    tuple moved are DELETE+INSERTed with stable row ids; a no-change
    cycle writes nothing and leaves the one-shot FTS shadow untouched.
    A cold store, an embed-model stamp mismatch, or ``incremental=False``
    (--full) forces the replacement rebuild. ``write=False`` (--check):
    freshness verdict only — no DDL, no writes, no embedder resolution
    (content hashes are the identity of record).
    """
    db_path = Path(db_path) if db_path is not None else MEMORY_DUCKDB_DB
    stats: dict = {}

    units, files_meta = _collect_units(zcode_projects, prime_state, opencode_dir)
    hist_units, hist_meta = _collect_history_units(
        zcode_projects, prime_state, opencode_dir, snap_root
    )
    units += hist_units
    files_meta.update(hist_meta)
    stats["total_units"] = len(units)
    stats["history_units"] = len(hist_units)
    stats["history_snapshots"] = len(_snap_stamps(Path(snap_root or SNAP_ROOT)))
    stats["mode"] = "full"

    if not write:
        # Freshness verdict vs the stored meta (same doctrine as the legacy
        # --check: source-file content hashes; stale = new/changed/deleted).
        by_kind: dict[str, int] = {}
        for u in units:
            by_kind[u["kind"]] = by_kind.get(u["kind"], 0) + 1
        stats["by_kind"] = by_kind
        stats.update(_check_freshness_memory_duckdb(db_path, files_meta, stats))
        _print_staleness(stats)
        return stats

    with _memory_writer_conn(db_path) as conn:
        try:
            conn.execute(MEMORY_DUCKDB_DDL)
            conn.execute(MEMORY_DUCKDB_META_DDL)
            conn.execute(MEMORY_DUCKDB_INFO_DDL)

            embed_fn, embed_dims, model_label, gemma_active = _resolve_memory_embedder_duckdb(
                conn, embed_fn, stats
            )

            all_rows = _compose_rows(units, embed_fn, gemma=gemma_active)
            stats["total_rows"] = len(all_rows)
            stats["embedded"] = sum(1 for r in all_rows if r[6])
            by_kind: dict[str, int] = {}
            for r in all_rows:
                by_kind[r[1]] = by_kind.get(r[1], 0) + 1
            stats["by_kind"] = by_kind

            stored_meta: dict = {}
            try:
                stored_meta = {
                    r[0]: (r[1], r[2])
                    for r in conn.execute(
                        "SELECT unit_path, mtime, content_hash FROM memory_search_meta"
                    ).fetchall()
                }
            except duckdb.Error:
                stored_meta = {}
            try:
                stamp_row = conn.execute(
                    "SELECT value FROM memory_search_info WHERE key = 'embed_model'"
                ).fetchone()
                stamp = stamp_row[0] if stamp_row else None
            except duckdb.Error:
                stamp = None
            can_carry = (
                bool(incremental)
                and bool(stored_meta)
                and (model_label is None or stamp is None or model_label == stamp)
            )
            if can_carry:
                stats["mode"] = "incremental"
                stats["upserts"], stats["deletes"] = _write_memory_incremental(
                    conn, all_rows, files_meta, stored_meta, model_label, embed_dims
                )
            else:
                stats["mode"] = "full"
                _write_memory_store(conn, all_rows, files_meta, model_label, embed_dims)

            stats["indexed"] = conn.execute("SELECT COUNT(*) FROM memory_search").fetchone()[0]
            if isinstance(embed_fn, CachedEmbed):
                stats["embed_cache_hits"] = embed_fn.hits
                stats["embed_cache_misses"] = embed_fn.misses
                stats["embed_cache_state"] = embed_fn.status_line()
            return stats
        finally:
            if isinstance(embed_fn, CachedEmbed):
                embed_fn.close()
            conn.close()


def _check_freshness_memory_duckdb(db_path: Path, files_meta: dict, stats: dict) -> dict:
    """--check staleness verdict vs the stored meta; absent store reads as
    all-new. Mutates stats["total_rows"] and returns the staleness part."""
    try:
        # Reader path: --check must never take the writer flock or fail on a
        # rebuild window (it runs routinely against the live sidecar).
        conn = connect_memory_read_only(db_path)
        try:
            stats["total_rows"] = conn.execute("SELECT COUNT(*) FROM memory_search").fetchone()[0]
            stats_part, _stored = _source_staleness(conn, files_meta)
        finally:
            conn.close()
    except Exception:  # noqa: S110  # store absent/unreadable: everything reads as new
        stats_part = {
            "stale_new": sorted(files_meta),
            "stale_changed": [],
            "stale_deleted": [],
            "index_stale": bool(files_meta),
        }
        stats["total_rows"] = 0
    return stats_part


def _resolve_memory_embedder_duckdb(conn, embed_fn, stats: dict):
    """Resolve the memory embedder for the DuckDB store. An injected
    embed_fn passes through as the dry-run pseudo-embedder."""
    embed_dims = rds._PSEUDO_DIMS
    model_label: str | None = None
    gemma_active = False
    if embed_fn is None:
        embed_fn, embed_dims, model_label, gemma_active = resolve_memory_embedder()
        stats["embed_model"] = model_label
        # Top-level demotion guard (mirrors the legacy sqlite path): a
        # gemma-stamped store is never silently re-embedded by the
        # fallback model. MEMORY_EMBEDDER=granite is the explicit escape.
        gemma_embedder.guard_gemma_stamp(
            _stored_embed_model(conn),
            model_label,
            os.environ.get(MEMORY_EMBEDDER_ENV),
        )
        if model_label != f"dry-run-v{rds._PSEUDO_DIMS}":
            # Native duckdb cache handle: the duckdb host conn IS the
            # cache conn (embed-store fold S0 — no sqlite attach).
            embed_fn = rds.CachedEmbed(embed_fn, model_label, conn, source="memory")
    return embed_fn, embed_dims, model_label, gemma_active


def _memory_row_key(row: tuple) -> str:
    """Content identity of a memory row (title..content).

    duckdb_rebuild_incremental_carry S5 lesson: titles are NOT unique
    across the live/history axis — a zcode memory and its history
    snapshot share a title, so a title-keyed diff dict collapses the
    pair and masks the live delta entirely. The delta is keyed by row
    content instead; ids stay stable because the key is stable.
    """
    return hashlib.blake2b(
        chr(31).join(str(x) for x in row[:6]).encode("utf-8", errors="replace"),
        digest_size=16,
    ).hexdigest()


def _stable_memory_row_id(key_hex: str) -> int:
    """Stable row id: 47 masked blake2b bits of the content key.

    The 128-bit key digest is re-hashed and MASKED to 47 bits
    (int64-safe for the row binds; a raw 128-bit shift would overflow).
    """
    return (
        int(
            hashlib.blake2b(
                f"memory-row:{key_hex}".encode("utf-8", errors="replace"), digest_size=16
            ).hexdigest(),
            16,
        )
        & 0x7FFF_FFFF_FFFF
    )


def _write_memory_incremental(
    conn, all_rows: list, files_meta: dict, stored_meta: dict, model_label, embed_dims
) -> tuple[int, int]:
    """Content-keyed delta write for the duckdb store (S3/S5).

    Rows are recomposed for everyone (cross-file inputs), then matched
    against the stored rows by CONTENT key (titles are not unique — live
    vs history rows share one); only moved rows get DELETE+INSERT with
    stable ids (:func:`_stable_memory_row_id`); meta is refreshed for
    changed units and garbage-collected for vanished ones. A no-change
    cycle writes nothing and leaves the one-shot FTS shadow untouched —
    any dirty apply refreshes it (plain DML leaves the shadow stale and
    lexical search would miss the delta until the next --full)."""
    stored: dict = {}
    for r in conn.execute(
        "SELECT id, title, kind, name, source_path, purpose, content, embedding FROM memory_search"
    ).fetchall():
        stored[_memory_row_key(tuple(r[1:]))] = (r[0], tuple(r[1:]))
    new: dict = {}
    for r in all_rows:
        new[_memory_row_key(tuple(r))] = tuple(r)
    to_upsert_keys = [k for k, row in new.items() if stored.get(k, (None, None))[1] != row]
    to_delete_ids = [stored[k][0] for k in stored if k not in new]
    changed_units = [
        u for u in files_meta if u not in stored_meta or stored_meta[u][1] != files_meta[u][1]
    ]
    if not to_upsert_keys and not to_delete_ids and not changed_units:
        return 0, 0
    for rid in to_delete_ids:
        conn.execute("DELETE FROM memory_search WHERE id = ?", (rid,))
    for k in to_upsert_keys:
        row = new[k]
        conn.execute(
            "DELETE FROM memory_search WHERE id = ?",
            (_stable_memory_row_id(k),),
        )
        conn.execute(
            "INSERT INTO memory_search "
            "(id, title, kind, name, source_path, purpose, content, embedding, searchtext) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _stable_memory_row_id(k),
                *row,
                _compose_memory_searchtext(row),
            ),
        )
    for u in stored_meta:
        if u not in files_meta:
            conn.execute("DELETE FROM memory_search_meta WHERE unit_path = ?", (u,))
    conn.executemany(
        "INSERT OR REPLACE INTO memory_search_meta "
        "(unit_path, mtime, content_hash) VALUES (?, ?, ?)",
        [(u, *files_meta[u]) for u in changed_units],
    )
    if (to_upsert_keys or to_delete_ids) and model_label is not None:
        conn.executemany(
            "INSERT OR REPLACE INTO memory_search_info (key, value) VALUES (?, ?)",
            [("embed_model", model_label), ("embed_dims", str(embed_dims))],
        )
    if to_upsert_keys or to_delete_ids:
        rbc.refresh_fts_shadow(conn, "memory_search")
    return len(to_upsert_keys), len(to_delete_ids)


def _write_memory_store(
    conn, all_rows: list[tuple], files_meta: dict, model_label: str | None, embed_dims: int
) -> None:
    """Replacement write: delete-all + insert rows/meta/info stamps, then
    rebuild the native FTS index (one-shot structure; drop+recreate covers
    both fresh and pre-existing stores)."""
    conn.execute("DELETE FROM memory_search")
    conn.execute("DELETE FROM memory_search_meta")
    if all_rows:
        conn.executemany(
            "INSERT INTO memory_search "
            "(id, title, kind, name, source_path, purpose, content, embedding, searchtext) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(i, *r, _compose_memory_searchtext(r)) for i, r in enumerate(all_rows)],
        )
    conn.executemany(
        "INSERT OR REPLACE INTO memory_search_meta (unit_path, mtime, content_hash) "
        "VALUES (?, ?, ?)",
        [(u, m, h) for u, (m, h) in sorted(files_meta.items())],
    )
    if model_label is not None:
        conn.executemany(
            "INSERT OR REPLACE INTO memory_search_info (key, value) VALUES (?, ?)",
            [("embed_model", model_label), ("embed_dims", str(embed_dims))],
        )
    try:
        conn.execute("PRAGMA drop_fts_index('memory_search')")
    except duckdb.Error:
        pass
    conn.execute("PRAGMA create_fts_index('memory_search', 'id', 'searchtext')")


def _search_memory_duckdb(
    conn,
    q: str,
    limit: int = 5,
    *,
    kind: str | None = None,
    hybrid: bool = True,
    query_vec: rbc.QueryVector | None = None,
    history: str = "exclude",
) -> dict:
    """Native match_bm25 + Python highlight + cosine leg over the DuckDB store.

    Result dict shape is identical to the FTS5 path (mode/results), so
    memory_query/master_query/search_tui consumers are untouched. The kind/
    history axis rides as plain SQL predicates on the kind column (the
    native store needs no UNINDEXED-column workaround).
    """
    expr = rds.fts_match_expr(q)
    if not expr:
        return {"mode": "bm25", "results": []}
    # Fast path: SQL top-k over the FTS postings (fts_stem_ladder S2).
    # Falls back to the match_bm25 macro below when any token is
    # unresolvable. kind/history ride as ss.kind predicates via
    # where_extra; the macro path keeps its unqualified form.
    tokens = ds.fts_query_tokens_duckdb(q)
    page: list[tuple]
    rows: list[tuple] | None = None
    mode = "macro"
    if tokens:
        tk_where = ""
        tk_params: list = []
        if kind:
            tk_where = " AND ss.kind = ?"
            tk_params = [kind]
        elif history in ("only", "exclude"):
            tk_ph = ", ".join("?" * len(_HISTORY_KINDS))
            tk_where = f" AND ss.kind {'IN' if history == 'only' else 'NOT IN'} ({tk_ph})"  # noqa: S608  # fixed keyword + fixed placeholder count
            tk_params = list(_HISTORY_KINDS)
        try:
            rows, mode = ds.fts_topk(
                conn,
                fts_table="fts_main_memory_search",
                base_table="memory_search",
                tokens=tokens,
                limit=limit,
                where_extra=tk_where,
                params_extra=tk_params,
            )
        except duckdb.Error:
            rows, mode = None, "macro"
    if mode == "topk" and rows is not None:
        ids = [rid for rid, _ in rows]
        page = []
        if ids:
            ph = ", ".join("?" for _ in ids)
            by_id = {
                r[0]: r
                for r in conn.execute(
                    "SELECT id, title, kind, name, source_path, purpose, content "  # noqa: S608  # ph is a computed ?, list, not user input
                    f"FROM memory_search WHERE id IN ({ph})",
                    ids,
                ).fetchall()
            }
            page = [by_id[i] for i in ids if i in by_id]
    else:
        sc = "fts_main_memory_search.match_bm25(id, ?)"
        where = f"{sc} IS NOT NULL"
        params: list = [expr, expr]
        if kind:
            where += " AND kind = ?"
            params.append(kind)
        elif history in ("only", "exclude"):
            placeholders = ", ".join("?" * len(_HISTORY_KINDS))
            where += f" AND kind {'IN' if history == 'only' else 'NOT IN'} ({placeholders})"  # noqa: S608  # fixed keyword + fixed placeholder count
            params.extend(_HISTORY_KINDS)
        try:
            page = conn.execute(
                f"SELECT id, title, kind, name, source_path, purpose, content, {sc} AS score "  # noqa: S608  # WHERE fully parameterized; f-string interpolates fixed columns only
                f"FROM memory_search WHERE {where} "
                f"ORDER BY score DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        except duckdb.Error:
            return {"mode": "bm25", "results": []}

    scored, sims, cos_rank = _cosine_leg_memory_duckdb(conn, q, hybrid, query_vec)

    # candidates: bm25 page first (highlight via the Python window), then
    # cosine-only extras (200-char head) — the legacy _memory_candidates shape.
    candidates: list[tuple[int, tuple, str]] = [
        (pos, row, rds._highlight_window(row[6], q)) for pos, row in enumerate(page)
    ]
    _append_cosine_extras(conn, candidates, page, scored, cos_rank, limit, kind, history)

    worst = len(cos_rank) if cos_rank else 0
    fused = rrf_two_leg(candidates, cos_rank, worst)

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


def _cosine_leg_memory_duckdb(conn, q: str, hybrid: bool, query_vec: rbc.QueryVector | None):
    """Cosine leg over the DuckDB store (same stamp-keyed embedder +
    degradation contract as the legacy _cosine_leg, keyed on id instead of
    rowid). Returns (scored, sims, cos_rank); ([], {}, None) when the
    embedder is unavailable or dims mismatch — BM25-only then."""
    scored: list[tuple[int, float]] = []
    sims: dict[int, float] = {}
    if not hybrid:
        return scored, sims, None
    try:
        idx_dims = _stored_embed_dims(conn)
        q_vec: list[float] | None = None
        if query_vec is not None:
            q_vec = rbc.check_query_vector(_stored_embed_model(conn), idx_dims, query_vec)
        if q_vec is None:
            embed_q, _dims = _query_embedder_for(_stored_embed_model(conn))
            candidate = embed_q(q)
            if idx_dims == len(candidate):
                q_vec = candidate
        if q_vec is not None:
            from helpers.core.vec_codec import load_vec

            norm_q = sum(x * x for x in q_vec) ** 0.5 or 1.0
            for rid, emb in conn.execute(
                "SELECT id, embedding FROM memory_search "
                "WHERE embedding IS NOT NULL AND embedding != ''"
            ).fetchall():
                vec = load_vec(emb)
                if not vec or len(vec) != len(q_vec):
                    continue
                norm_v = sum(x * x for x in vec) ** 0.5 or 1.0
                sim = sum(a * b for a, b in zip(q_vec, vec)) / (norm_q * norm_v)
                scored.append((rid, sim))
                sims[rid] = sim
            scored.sort(key=lambda t: t[1], reverse=True)
    except Exception:  # noqa: S110  # embedder unavailable -> BM25 only
        scored, sims = [], {}
    cos_rank = {rid: pos for pos, (rid, _s) in enumerate(scored)} if scored else None
    return scored, sims, cos_rank


def _append_cosine_extras(
    conn,
    candidates: list[tuple[int, tuple, str]],
    page: list[tuple],
    scored: list[tuple[int, float]],
    cos_rank: dict | None,
    limit: int,
    kind: str | None,
    history: str,
) -> None:
    """Append cosine-only rows (not already on the bm25 page) to the
    candidate list, 200-char head as snippet — the legacy shape."""
    if cos_rank is None:
        return
    page_ids = {row[0] for row in page}
    rows_by_id = {
        r[0]: r
        for r in conn.execute(
            "SELECT id, title, kind, name, source_path, purpose, content FROM memory_search"
        ).fetchall()
        if _kind_ok(r[2], kind, history)
    }
    extra = 0
    for rid, _sim in scored[:limit]:
        if rid in page_ids:
            continue
        row = rows_by_id.get(rid)
        if row is None:
            continue
        head = " ".join((row[6] or "").split())[:200]
        candidates.append((len(page) + extra, row, head))
        extra += 1


def main(argv: list[str] | None = None) -> int:
    return rbc.run_rebuild_cli(
        argv,
        rbc.RebuildCliSpec(
            description=__doc__.split("\n\n")[0],
            default_db=str(MEMORY_DB),
            db_help="Path to the memory_search sidecar (default: memory/memory_search.duckdb; "
            "the legacy FTS5 .db stays openable via --db).",
            check_help="Dry-run: count records, report index freshness "
            "(changed/new/deleted), no writes. Exits 1 when stale.",
            incremental_help="Incremental rebuild (row-keyed diff; unchanged rows not rewritten).",
            full_help="Full rebuild: delete the sidecar and rebuild from scratch.",
            rebuild_fn=rebuild,
            summary=_summary_line,
            migrated_msg="(schema migrated: memory_search recreated)",
            stamp_table="memory_search_info",
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
