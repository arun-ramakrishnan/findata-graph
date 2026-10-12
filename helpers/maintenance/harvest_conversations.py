#!/usr/bin/env python3
"""Harvest ALL harness conversation histories into the convo_search corpus.

The three harnesses (opencode, prime-rlm, zcode) delete/rotate/purge
their own histories — the corpus under
``memory/data/harness/<harness>/conversations/<session_id>.parquet``
(zstd, Arrow-written, one row per message part) is the archive of
record; live stores are harvest sources only (convo_search proposal,
2026-09-27).

Lanes and sources, processed oldest-first so the newest source wins a
part_id collision:

- opencode: 7 timeshift snapshot dbs (oldest → newest), the
  pre-redaction ``agent_traces.duckdb`` fact_event lane (bus events
  recovered after db deletion/redaction), then the live db.
- prime-rlm: live + snapshot ``sessions/*.jsonl``.
- zcode: live + snapshot ``rollout/model-io-*.jsonl`` — model identity
  normalized to a plain string at write (S1,
  zcode_conversation_ingest_repair), fullest ``full``-kind request per
  session = final context (tail/delta fallbacks marked partial in
  meta), per-response ``text``/``reasoningText`` keyed by responseId,
  responseId-less error records as searchable ``error`` rows (S2).

Incremental: per-source watermarks in ``memory/convo_search.duckdb``
(opencode: max ``time_updated`` per db; files: mtime+size). Immutable
snapshot sources go quiet after the first full pass; the live db and
active session files pay only their delta. Touched sessions are
re-merged with their existing parquet file (part_id-keyed, newest
wins) and rewritten; untouched sessions are never re-read.

CLI mirrors the search-fresh contract: ``--check`` reports drift and
exits 1 without writing; default (or APPLY=1 via ``make convo-fresh``)
harvests.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sqlite3
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

REPO = Path(__file__).resolve().parents[2]
SNAP_ROOT = Path("/mnt/store/timeshift/snapshots")
CORPUS_ROOT = REPO / "memory/data/harness"
INDEX_DB = REPO / "memory/convo_search.duckdb"

# The Store's S3 io-lock coordination imports helpers.misc.duckdb_lock at
# module level — as a bare-script entry point this file must bootstrap
# sys.path itself before that import (static check: entry-point sys.path).
sys.path.insert(0, str(REPO))

from helpers.core.db import connect  # noqa: E402  (bare-script entry point; bootstrap above)

_SCHEMA = pa.schema(
    [
        ("part_id", pa.string()),
        ("message_id", pa.string()),
        ("session_id", pa.string()),
        ("ts", pa.timestamp("ms", tz="UTC")),
        ("role", pa.string()),
        ("agent", pa.string()),
        ("model", pa.string()),
        ("part_type", pa.string()),
        ("text", pa.string()),
        ("meta", pa.string()),
        ("source", pa.string()),
    ]
)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(name: str) -> str:
    return _UNSAFE.sub("_", name)[:120]


def _ms(ms: int | float | str | None) -> datetime:
    """Epoch-millis or ISO-8601 (prime emits strings) → UTC datetime."""
    if not ms:
        return datetime(1970, 1, 1, tzinfo=UTC)
    if isinstance(ms, str):
        return _iso_ms(ms)
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def _s(v: object) -> str:
    """String-column coercion: dicts/lists json-encode, None → ''."""
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v or "")


def _row(
    pid: str,
    mid: str,
    sid: str,
    ts: datetime,
    role: str,
    agent: str,
    model: str,
    ptype: str,
    text: str,
    meta: dict,
    src: str,
) -> dict:
    return {
        "part_id": _s(pid),
        "message_id": _s(mid),
        "session_id": _s(sid),
        "ts": ts,
        "role": _s(role),
        "agent": _s(agent),
        "model": _s(model),
        "part_type": _s(ptype),
        "text": _s(text),
        "meta": json.dumps(meta or {}),
        "source": _s(src),
    }


class Store:
    """Watermark + part registry backed by the convo_search duckdb.

    S3 (duckdb_transient_lock_retry): the read-only branch queues on the
    index's io.lock and retries transient conflicts; the WRITER branch
    takes LOCK_EX for the Store's lifetime (released in close()) so a
    concurrent rebuild queues instead of colliding with the watermark
    DDL/writes.
    """

    def __init__(self, db: Path, read_only: bool = False) -> None:
        import duckdb

        from helpers.misc.duckdb_lock import open_read_only

        self.read_only = read_only
        self._io_lock_fh = None
        if read_only:
            # --check must never write: a check that CREATEs tables takes the
            # DuckDB write lock and collides with a running rebuild.
            if not Path(db).exists():
                self.con = None
                self._wm, self._parts = {}, {}
                self._pending_wm, self._pending_parts = [], []
                return
            self.con = open_read_only(db)
        else:
            db.parent.mkdir(parents=True, exist_ok=True)
            # Lifetime LOCK_EX: held until close() releases it. A crashed
            # harvest releases via process death (kernel-managed flock).
            self._io_lock_fh = open(str(db) + ".io.lock", "w")
            fcntl.flock(self._io_lock_fh.fileno(), fcntl.LOCK_EX)
            self.con = duckdb.connect(str(db))
            self.con.execute(
                "CREATE TABLE IF NOT EXISTS harvest_meta ("
                "source VARCHAR PRIMARY KEY, watermark VARCHAR, updated_ts TIMESTAMP)"
            )
            self.con.execute(
                "CREATE TABLE IF NOT EXISTS part_index ("
                "harness VARCHAR, part_id VARCHAR, session_id VARCHAR, "
                "PRIMARY KEY (harness, part_id))"
            )
        try:
            wm = dict(self.con.execute("SELECT source, watermark FROM harvest_meta").fetchall())
            rows = self.con.execute(
                "SELECT harness, part_id, session_id FROM part_index"
            ).fetchall()
        except duckdb.CatalogException:
            # read_only against a db whose tables do not exist yet
            wm, rows = {}, []
        self._wm: dict[str, str] = wm
        self._parts: dict[tuple[str, str], str] = {(h, p): s for h, p, s in rows}
        # Staged until commit() — a crashed harvest must not leave watermarks
        # that make immutable sources look quiet while their rows were never
        # flushed to parquet (transactionality lesson, first cold run).
        self._pending_wm: list[tuple[str, str]] = []
        self._pending_parts: list[tuple[str, str, str]] = []

    def watermark(self, key: str) -> str | None:
        return self._wm.get(key)

    def set_watermark(self, key: str, value: str) -> None:
        self._wm[key] = value
        self._pending_wm.append((key, value))

    def known(self, harness: str, part_id: str) -> bool:
        return (harness, part_id) in self._parts

    def register(self, harness: str, rows: list[dict]) -> None:
        fresh = {(harness, r["part_id"]): r["session_id"] for r in rows}
        novel = [(h, p, s) for (h, p), s in fresh.items() if (h, p) not in self._parts]
        if novel:
            self._parts.update({(h, p): s for h, p, s in novel})
            self._pending_parts.extend(novel)

    def commit(self) -> None:
        if self.read_only:
            raise RuntimeError("commit() on a read-only Store")
        con = self.con
        if con is None:  # pragma: no cover - read_only is the only None path
            raise RuntimeError("no open Store connection")
        if self._pending_wm:
            con.executemany(
                "INSERT OR REPLACE INTO harvest_meta VALUES (?, ?, now())", self._pending_wm
            )
            self._pending_wm.clear()
        if self._pending_parts:
            con.executemany(
                "INSERT OR REPLACE INTO part_index VALUES (?, ?, ?)", self._pending_parts
            )
            self._pending_parts.clear()

    def close(self) -> None:
        if self.con is not None:
            self.con.close()
        if self._io_lock_fh is not None:
            # DuckDB closed first; releasing LOCK_EX now lets queued
            # readers/rebuilds proceed against a quiescent file.
            fcntl.flock(self._io_lock_fh.fileno(), fcntl.LOCK_UN)
            self._io_lock_fh.close()
            self._io_lock_fh = None


class SessionWriter:
    """Merges harvested rows into per-session parquet files (newest wins)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.touched: dict[tuple[str, str], dict[str, dict]] = {}

    def add(self, harness: str, rows: list[dict]) -> None:
        for r in rows:
            buf = self.touched.setdefault((harness, r["session_id"]), {})
            old = buf.get(r["part_id"])
            if old is None or (r["ts"], r["source"]) >= (old["ts"], old["source"]):
                buf[r["part_id"]] = r

    def flush(self) -> int:
        written = 0
        for (harness, sid), buf in sorted(self.touched.items()):
            out = self.root / harness / "conversations"
            out.mkdir(parents=True, exist_ok=True)
            f = out / f"{_safe(sid)}.parquet"
            if f.exists():
                for r in pq.read_table(f).to_pylist():
                    buf.setdefault(r["part_id"], r)
            rows = sorted(buf.values(), key=lambda r: (r["ts"], r["part_id"]))
            pq.write_table(pa.Table.from_pylist(rows, schema=_SCHEMA), f, compression="zstd")
            written += 1
        return written


def _oc_part_rows(cur_parts: Iterator[tuple], msgs: dict[str, tuple], src: str) -> list[dict]:
    out = []
    for pid, mid, sid, tc, tu, data in cur_parts:
        d = json.loads(data)
        ptype = d.get("type") or "unknown"
        text, meta = "", {}
        if ptype in ("text", "reasoning"):
            text = d.get("text") or ""
        elif ptype == "tool":
            st = d.get("state") or {}
            text = st.get("output") or ""
            meta = {
                "title": st.get("title"),
                "status": st.get("status"),
                "tool": d.get("tool"),
                "input": str(st.get("input"))[:2000],
            }
        elif ptype == "step-finish":
            text = d.get("reason") or ""
            meta = {"tokens": d.get("tokens"), "cost": d.get("cost")}
        elif ptype == "patch":
            meta = {"files": d.get("files"), "hash": d.get("hash")}
        elif ptype == "compaction":
            text = d.get("summary") or ""
            meta = {"intent": d.get("intent")}
        else:
            meta = {k: v for k, v in d.items() if k not in ("type",)}
        m = msgs.get(mid) or ("", "", "")
        out.append(
            _row(
                pid,
                mid,
                sid,
                _ms(tu or tc),
                m[0],
                m[1],
                _oc_model_value(m[2]),
                ptype,
                text,
                meta,
                src,
            )
        )
    return out


def _harvest_opencode_db(db_path: Path, src: str, hwm: int | None) -> tuple[list[dict], int]:
    con = connect(db_path, read_only=True)
    try:
        msgs = {
            r[0]: (
                (json.loads(r[1]) or {}).get("role", ""),
                (json.loads(r[1]) or {}).get("agent", ""),
                (json.loads(r[1]) or {}).get("model", ""),
            )
            for r in con.execute("SELECT id, data FROM message")
        }
        where, args = "", []
        if hwm is not None:
            where = "WHERE time_updated > ?"
            args = [hwm]
        rows = _oc_part_rows(
            con.execute(
                f"SELECT id, message_id, session_id, time_created, "  # noqa: S608  # {where} is a code constant ('WHERE ...' or '')
                f"time_updated, data FROM part {where}",
                args,
            ),
            msgs,
            src,
        )
        new_hwm = con.execute("SELECT COALESCE(MAX(time_updated), 0) FROM part").fetchone()[0]
        return rows, int(new_hwm)
    finally:
        con.close()


# session_message types the v2 lane skips: outcome + routing markers with
# no searchable text (measured 2026-10-11: idle 20, model-switched 5).
_V2_SKIP_TYPES = ("idle", "model-switched")

# event part types the v2 message store drops (measured 2026-10-11) — the
# S2 lane emits exactly these; text/reasoning/tool stay with S1 so the two
# lanes never double-ingest one block under different ids.
_V2_DROPPED_TYPES = ("step-start", "step-finish", "patch", "compaction", "file")

_SNAP_COUNT_KEY = "opencode-snap:source-count"


def _corpus_max_ts(corpus_root: Path, sid: str) -> int | None:
    """Max harvested ts (epoch ms) for a session's corpus parquet.

    None when the session was never harvested. Seeds are timestamps, not
    watermarks: the seed skips exactly what the corpus already holds — no
    duplicates, and no residual window between the last legacy harvest and
    the v2 cutover. A deleted parquet falls back to full ingest; re-emitted
    rows merge newest-wins under stable ids, so the lane self-heals.
    """
    f = corpus_root / "opencode" / "conversations" / f"{_safe(sid)}.parquet"
    if not f.exists():
        return None
    vals = [
        int(v.timestamp() * 1000)
        for v in pq.read_table(f, columns=["ts"]).column("ts").to_pylist()
        if v is not None
    ]
    return max(vals) if vals else None


def _v2_envelope(con) -> dict[str, tuple]:
    """session_id -> (agent, model) from session_v2; {} when pre-v2 schema."""
    try:
        rows = con.execute("SELECT id, agent, model FROM session_v2").fetchall()
    except sqlite3.OperationalError:
        return {}
    out = {}
    for sid, agent, model in rows:
        if isinstance(model, str):
            try:
                model = json.loads(model)
            except json.JSONDecodeError:
                pass
        out[sid] = (agent or "", _oc_model_value(model))
    return out


def _v2_msg_envelope(con) -> dict[str, tuple]:
    """message_id -> (role, agent, model): session_message first (v2 rows),
    message table fallback (v1 rows). Missing tables contribute nothing."""
    out: dict[str, tuple] = {}
    try:
        for mid, data in con.execute("SELECT id, data FROM session_message"):
            try:
                d = json.loads(data) or {}
            except json.JSONDecodeError:
                continue
            m = d.get("model") or {}
            out[mid] = (
                d.get("type", ""),
                d.get("agent", ""),
                _oc_model_value(m),
            )
    except sqlite3.OperationalError:
        pass
    try:
        for mid, data in con.execute("SELECT id, data FROM message"):
            if mid in out:
                continue
            try:
                d = json.loads(data) or {}
            except json.JSONDecodeError:
                continue
            out[mid] = (
                d.get("role", ""),
                d.get("agent", ""),
                _oc_model_value(d.get("model", "")),
            )
    except sqlite3.OperationalError:
        pass
    return out


def _assistant_block_row(
    pid: str, mid: str, sid: str, ts: int, agent: str, model: str, b: dict, src: str
) -> dict | None:
    """One corpus row for an assistant content block; None skips it.

    Unknown future block kinds return None — skipped loudly via census
    tests, never silently (new kinds must extend this branch + AC4).
    """
    btype = b.get("type")
    if btype == "text":
        return _row(
            pid, mid, sid, ts, "assistant", agent, model, "text", b.get("text") or "", {}, src
        )
    if btype == "reasoning":
        return _row(
            pid,
            mid,
            sid,
            ts,
            "assistant",
            agent,
            model,
            "reasoning",
            b.get("text") or "",
            {},
            src,
        )
    if btype == "tool":
        st = b.get("state") or {}
        texts = [c.get("text") or "" for c in st.get("content") or [] if isinstance(c, dict)]
        # legacy meta parity minus title: the v2 projection drops
        # part titles, so there is nothing to carry (measured
        # 2026-10-11 against the restore run, not assumed).
        meta = {
            "status": st.get("status"),
            "tool": b.get("name"),
            "input": str(st.get("input"))[:2000],
        }
        return _row(
            pid, mid, sid, ts, "assistant", agent, model, "tool", " ".join(texts), meta, src
        )
    return None


def _v2_block_rows(
    mid: str, sid: str, tc, tu, agent: str, model: str, data: dict, src: str
) -> list[dict]:
    """One corpus row per searchable unit of a session_message row.

    part_id is the message id for message-grain rows (user, compaction,
    synthetic — one row each) and f"{mid}#{i}" per assistant content block
    (deterministic: content order is append-only, so re-harvests merge
    newest-wins instead of duplicating).
    """
    rows = []
    ts = _ms(tu or tc)
    ctype = data.get("type") or "unknown"
    if ctype == "user":
        if data.get("text"):
            rows.append(
                _row(mid, mid, sid, ts, "user", agent, model, "text", data["text"], {}, src)
            )
    elif ctype == "assistant":
        for i, b in enumerate(data.get("content") or []):
            if not isinstance(b, dict):
                continue
            row = _assistant_block_row(f"{mid}#{i}", mid, sid, ts, agent, model, b, src)
            if row is not None:
                rows.append(row)
    elif ctype == "compaction":
        # The summary lives ONLY here — event compaction parts carry just
        # {auto, tail_start_id} (measured 2026-10-11). Dropping this row
        # loses the text with no recovery path.
        rows.append(
            _row(
                mid,
                mid,
                sid,
                ts,
                "",
                agent,
                model,
                "compaction",
                data.get("summary") or "",
                {"status": data.get("status"), "reason": data.get("reason")},
                src,
            )
        )
    elif ctype == "synthetic":
        # The legacy v1 lane emits synthetic text parts as text — match it.
        rows.append(
            _row(mid, mid, sid, ts, "", agent, model, "text", data.get("text") or "", {}, src)
        )
    # idle / model-switched: outcome + routing markers, no searchable text.
    return rows


def _harvest_opencode_v2(
    db_path: Path, src: str, seeds: dict, corpus_root: Path
) -> tuple[list[dict], int]:
    """S1 lane: session_message -> corpus rows. Returns (rows, new_hwm).

    `seeds` is a memo cache mapping session_id -> epoch-ms seed (None =
    full ingest); uncached sessions resolve via `_corpus_max_ts`. Rows at
    or below their session seed are already in the corpus. Missing v2
    schema (pre-v2 snapshots) yields ([], 0) — a quiet lane, not an
    unreadable source: raising here would SKIP the source's legacy rows too.
    """
    con = connect(db_path, read_only=True)
    try:
        try:
            cur = con.execute(
                "SELECT id, session_id, time_created, time_updated, type, data FROM session_message"
            )
        except sqlite3.OperationalError:
            return [], 0
        env = _v2_envelope(con)

        def seed_for(sid: str) -> int | None:
            if sid not in seeds:
                seeds[sid] = _corpus_max_ts(corpus_root, sid)
            return seeds[sid]

        rows = []
        for mid, sid, tc, tu, typ, data in cur:
            seed = seed_for(sid)
            if seed is not None and (tu or tc or 0) <= seed:
                continue
            try:
                d = json.loads(data) or {}
            except json.JSONDecodeError:
                continue
            d["type"] = typ  # row type is a column, not in the payload
            if typ == "assistant":
                m = d.get("model") or {}
                agent = d.get("agent") or env.get(sid, ("", ""))[0]
                model = _oc_model_value(m) or env.get(sid, ("", ""))[1]
            else:
                agent, model = env.get(sid, ("", ""))
            rows.extend(_v2_block_rows(mid, sid, tc, tu, agent, model, d, src))
        try:
            new_hwm = con.execute(
                "SELECT COALESCE(MAX(time_updated), 0) FROM session_message"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            new_hwm = 0
        return rows, int(new_hwm)
    finally:
        con.close()


def _event_part_row(
    pid: str, p: dict, t, sid: str, seed: int | None, role: str, agent: str, model: str, src: str
) -> dict | None:
    """One event-lane row; None skips (undropped type or at/below seed).

    Conventions mirror `_oc_part_rows` exactly (patch is text="" +
    meta{files,hash} — searchable-text parity with the legacy corpus,
    not the proposal draft's text=files).
    """
    ptype = p.get("type")
    if ptype not in _V2_DROPPED_TYPES:
        return None
    mid = p.get("messageID", "")
    if seed is not None and t <= seed:
        return None
    ts = _ms(t)
    if ptype == "step-finish":
        text, meta = (
            p.get("reason") or "",
            {
                "tokens": p.get("tokens"),
                "cost": p.get("cost"),
                "snapshot": p.get("snapshot"),
            },
        )
    elif ptype == "patch":
        text, meta = "", {"files": p.get("files"), "hash": p.get("hash")}
    elif ptype == "compaction":
        text, meta = (
            p.get("summary") or "",
            {
                "auto": p.get("auto"),
                "tail_start_id": p.get("tail_start_id"),
            },
        )
    else:  # step-start, file: legacy else-branch parity
        text, meta = "", {k: v for k, v in p.items() if k != "type"}
    return _row(pid, mid, sid, ts, role, agent, model, ptype, text, meta, src)


def _harvest_opencode_events(
    db_path: Path, src: str, seeds: dict, corpus_root: Path
) -> tuple[list[dict], int]:
    """S2 lane: event part rows of the five types session_message drops.

    Last-write-wins per part id (ORDER BY rowid) — parts arrive 1-8x each
    (measured 2026-10-11: 50,160 ids over 114,159 rows). Part ids are the
    stable v1 space, so re-emission merges newest-wins with legacy-lane
    rows instead of duplicating. Conventions mirror `_oc_part_rows`
    exactly (patch is text="" + meta{files,hash} — searchable-text parity
    with the legacy corpus, not the proposal draft's text=files).
    """
    con = connect(db_path, read_only=True)
    try:
        try:
            cur = con.execute(
                "SELECT data FROM event WHERE type='message.part.updated.1' ORDER BY rowid"
            )
        except sqlite3.OperationalError:
            return [], 0
        latest: dict[str, tuple] = {}
        for (data,) in cur:
            try:
                d = json.loads(data)
            except json.JSONDecodeError:
                continue
            p = d.get("part") or {}
            if p.get("id"):
                latest[p["id"]] = (p, d.get("time") or 0)
        env = _v2_msg_envelope(con)

        def seed_for(sid: str) -> int | None:
            if sid not in seeds:
                seeds[sid] = _corpus_max_ts(corpus_root, sid)
            return seeds[sid]

        rows = []
        for pid, (p, t) in latest.items():
            sid = p.get("sessionID", "")
            role, agent, model = env.get(p.get("messageID", ""), ("", "", ""))
            row = _event_part_row(pid, p, t, sid, seed_for(sid), role, agent, model, src)
            if row is not None:
                rows.append(row)
        try:
            new_hwm = con.execute(
                "SELECT COALESCE(MAX(json_extract(data,'$.time')),0) FROM event "
                "WHERE type='message.part.updated.1'"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            new_hwm = 0
        return rows, int(new_hwm or 0)
    finally:
        con.close()


def _snapshot_opencode_dbs() -> list[Path]:
    pat = "*/localhost/home/arun/.local/share/opencode/opencode.db"
    return sorted(SNAP_ROOT.glob(pat))


def _snap_ts(p: Path) -> str:
    return p.relative_to(SNAP_ROOT).parts[0]


def _opencode_sources() -> list[tuple[Path, str, bool]]:
    """(path, src_label, immutable) oldest→newest; patchable in tests."""
    dbs = [(p, f"opencode-snap:{_snap_ts(p)}", True) for p in _snapshot_opencode_dbs()]
    live = Path.home() / ".local/share/opencode/opencode.db"
    if live.exists():
        dbs.append((live, "opencode:live", False))
    return dbs


def _harvest_opencode(store: Store, writer: SessionWriter) -> str:
    stats, parts_total = [], 0
    n_snaps = 0
    for path, src, immutable in _opencode_sources():
        key = f"{src}:{hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()}"
        if immutable:
            n_snaps += 1
        hwm_raw = store.watermark(key)
        if immutable and hwm_raw is not None:
            stats.append(f"{src}: quiet")
        else:
            hwm = int(hwm_raw) if hwm_raw is not None else None
            # One unreadable source must not abort the whole lane: a corrupt or
            # truncated backup image otherwise costs every other snapshot AND the
            # live db, and surfaces as a bare "disk I/O error" that names no file.
            # Observed 2026-09-29: timeshift snapshot 2026-09-28_23-23-31 failed
            # integrity_check ("database disk image is malformed") at message
            # rowid 29963, while the other 7 sources scanned clean. Skip + name it.
            # Two corruption modes live here: a bad sqlite image raises
            # sqlite3.DatabaseError/OSError at open, and a valid image with a
            # corrupt row raises json.JSONDecodeError or AttributeError (JSON
            # scalar where a dict is expected) when the message/part `data`
            # columns are parsed inside _harvest_opencode_db. Catch those EXACT
            # types — a bare ValueError net would silently skip-and-relabel
            # unrelated bugs as "unreadable source" (muse-review finding 2,
            # 2026-09-29: silent data loss wearing a corruption label).
            try:
                rows, new_hwm = _harvest_opencode_db(path, src, hwm)
            except (
                sqlite3.DatabaseError,
                OSError,
                json.JSONDecodeError,
                AttributeError,
            ) as exc:
                stats.append(f"{src}: SKIP unreadable ({type(exc).__name__}: {exc})")
                continue
            writer.add("opencode", rows)
            store.register("opencode", rows)
            store.set_watermark(key, str(new_hwm))
            parts_total += len(rows)
            stats.append(f"{src}: {'full' if hwm is None else 'delta'} {len(rows)} parts")
        # S1/S2 lanes: own watermark keys, per-session corpus seeds. A source
        # the legacy lane already finished still runs these once (their keys
        # never existed) — that is the backfill for post-cutover snapshots,
        # and per-session seeds keep it duplicate-free (see _corpus_max_ts).
        seeds: dict[str, int | None] = {}
        for lane, fn in (("v2", _harvest_opencode_v2), ("evt", _harvest_opencode_events)):
            lkey = key + ":" + lane
            if immutable and store.watermark(lkey) is not None:
                stats.append(f"{src}:{lane}: quiet")
                continue
            try:
                lrows, lnew = fn(path, src, seeds, writer.root)
            except (
                sqlite3.DatabaseError,
                OSError,
                json.JSONDecodeError,
                AttributeError,
            ) as exc:
                stats.append(f"{src}:{lane}: SKIP unreadable ({type(exc).__name__}: {exc})")
                continue
            writer.add("opencode", lrows)
            store.register("opencode", lrows)
            store.set_watermark(lkey, str(lnew))
            parts_total += len(lrows)
            stats.append(f"{src}:{lane}: {len(lrows)} parts")
    store.set_watermark(_SNAP_COUNT_KEY, str(n_snaps))
    return "\n".join(stats) + f"\nopencode total new: {parts_total}"


def _prime_text(block: object) -> str:
    if isinstance(block, dict):
        return block.get("text") or ""
    return str(block or "")


def _harvest_prime_file(f: Path, src: str) -> list[dict]:
    rows = []
    for line in open(f, errors="replace"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") not in ("message", "custom_message"):
            continue
        m = d.get("message") or {}
        sid = m.get("sessionID") or d.get("sessionID") or f.stem
        ts = _ms(d.get("timestamp"))
        for i, blk in enumerate(m.get("content") or []):
            text = _prime_text(blk)
            if not text:
                continue
            rows.append(
                _row(
                    f"{d.get('id')}:{i}",
                    d.get("id") or "",
                    sid,
                    ts,
                    m.get("role", ""),
                    m.get("agent", ""),
                    m.get("model", ""),
                    "text",
                    text,
                    {"block_type": blk.get("type") if isinstance(blk, dict) else None},
                    src,
                )
            )
    return rows


def _prime_files() -> list[tuple[Path, str]]:
    out = [
        (p, f"prime-snap:{_snap_ts(p)}")
        for p in sorted(SNAP_ROOT.glob("*/localhost/home/arun/.prime/agent/sessions/*.jsonl"))
    ]
    live = Path.home() / ".prime/agent/sessions"
    out += [(p, "prime:live") for p in sorted(live.glob("*.jsonl"))]
    return out


def _harvest_prime(store: Store, writer: SessionWriter) -> str:
    stats, total = [], 0
    for f, src in _prime_files():
        key = f"{src}:{hashlib.blake2b(str(f).encode(), digest_size=6).hexdigest()}"
        wm_raw = store.watermark(key)
        wm = json.loads(wm_raw) if wm_raw else None
        st = f.stat()
        cur = [int(st.st_mtime), st.st_size]
        if wm == cur:
            stats.append(f"{src}: quiet")
            continue
        rows = _harvest_prime_file(f, src)
        writer.add("prime-rlm", rows)
        store.register("prime-rlm", rows)
        store.set_watermark(key, json.dumps(cur))
        total += len(rows)
        stats.append(f"{src}: {len(rows)} parts")
    return "\n".join(stats) + f"\nprime-rlm total new: {total}"


def _dsh_home() -> Path:
    """Resolved per call (not at import) so Path.home patching in tests
    isolates the lane — same pattern as the prime/zcode file lanes."""
    return Path.home() / ".dsh" / "sessions"


def _dsh_index_path() -> Path | None:
    """Location of the session-query derived index, if the backend ever
    materializes one. The backend needs an explicit `path` config
    (opt-in per upstream docs); without it there is nothing to read and
    the S4 lines stay quiet. Resolved per call for test isolation."""
    candidates = sorted(Path.home().glob(".dsh/**/session-search.db"))
    return candidates[0] if candidates else None


def _dsh_index_lag() -> list[str]:
    """S4: derived-index coherence. Compares per-session max seq between
    the session logs (source of truth) and the session-query index.
    A lagging or missing index is the real operational risk of a derived
    store — silent staleness wearing a healthy face. Lines close
    automatically as the indexer catches up; absent index = quiet."""
    out = []
    idx = _dsh_index_path()
    if idx is None:
        return out
    try:
        con = connect(str(idx), read_only=True)
    except sqlite3.DatabaseError:
        return out
    try:
        try:
            indexed = {
                r[0]: r[1]
                for r in con.execute(
                    "SELECT session_id, MAX(seq) FROM persisted_docs GROUP BY session_id"
                ).fetchall()
            }
        except sqlite3.OperationalError:
            return out
    finally:
        con.close()
    for path, sid, _src in _dsh_logs():
        live_max = -1
        try:
            for seq, _ts, _typ, _data in _dsh_read_log(path):
                if isinstance(seq, int) and seq > live_max:
                    live_max = seq
        except OSError, zstandard.ZstdError:
            continue
        if sid not in indexed:
            out.append(f"dsh-index:missing {sid[:13]} (log {live_max})")
        elif indexed[sid] is not None and live_max > int(indexed[sid]):
            out.append(f"dsh-index:lagging {sid[:13]} (log {live_max} > index {int(indexed[sid])})")
    return out
    return Path.home() / ".dsh" / "sessions"


def _dsh_logs() -> list[tuple[Path, str, str]]:
    """(log path, session id, src). v4 preferred; v0 only when v4 is absent
    for a session dir — the generations overlap (measured 2026-10-11: 23
    shared (seq,type) keys, v4 re-stamping identical content by 1-2ms),
    so reading both would double-ingest under different seqs."""
    out = []
    home = _dsh_home()
    if not home.is_dir():
        return out
    for sess_dir in sorted(home.glob("*/*/")):
        sid = sess_dir.name
        v4 = sess_dir / "session.v4.jsonl.zstd"
        v0 = sess_dir / "session.jsonl.zstd"
        if v4.is_file():
            out.append((v4, sid, "dsh:live"))
        elif v0.is_file():
            out.append((v0, sid, "dsh:live"))
    return out


def _dsh_read_log(path: Path):
    """Streamed zstd decode -> parsed records (torn trailing line skipped).

    zstandard rides the venv (uv add with this lane's first import —
    dependency-gate ordering); stream_reader (not one-shot decompress) so
    a live session's concurrent append never fails the whole file.
    Yields (seq, time, type, data) per record.
    """
    dctx = zstandard.ZstdDecompressor()
    buf = b""
    with open(path, "rb") as fh, dctx.stream_reader(fh) as reader:
        while True:
            chunk = reader.read(1 << 20)
            if not chunk:
                break
            buf += chunk
    for line in buf.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        yield (d.get("seq"), d.get("time"), d.get("type"), d.get("data") or {})


def _dsh_content_text(content: object) -> str:
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _dsh_assistant_rows(pid: str, sid: str, ts, m: dict, model: str, src: str) -> list[dict]:
    """Text/reasoning rows for one assistant message's content blocks."""
    rows = []
    for i, b in enumerate(m.get("content") or []):
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            rows.append(
                _row(
                    f"{pid}#{i}",
                    pid,
                    sid,
                    _ms(ts),
                    "assistant",
                    "dsh",
                    model,
                    "text",
                    b.get("text") or "",
                    {},
                    src,
                )
            )
        elif b.get("type") == "reasoning":
            rows.append(
                _row(
                    f"{pid}#{i}",
                    pid,
                    sid,
                    _ms(ts),
                    "assistant",
                    "dsh",
                    model,
                    "reasoning",
                    b.get("text") or "",
                    {},
                    src,
                )
            )
    return rows


def _dsh_tool_result_row(
    pid: str, sid: str, ts, data: dict, m: dict, calls: dict, src: str
) -> dict | None:
    """Tool-result row: matched call wins, else orphan-result; None when textless."""
    cid = data.get("toolCallId") or (m.get("source") or {}).get("callId")
    text = _dsh_content_text(m.get("content"))
    call = calls.pop(cid, None)
    if call is not None:
        cpid, _cts, cdata = call
        meta = {
            "status": "error" if data.get("isError") else "completed",
            "tool": cdata.get("name"),
            "input": str(cdata.get("arguments"))[:2000],
        }
        return _row(cpid, cpid, sid, _ms(ts), "assistant", "dsh", "", "tool", text, meta, src)
    if text:
        return _row(
            pid,
            pid,
            sid,
            _ms(ts),
            "assistant",
            "dsh",
            "",
            "tool",
            text,
            {"status": "orphan-result"},
            src,
        )
    return None


def _dsh_record_rows(
    typ: str, pid: str, sid: str, ts, data: dict, calls: dict, src: str
) -> tuple[list[dict], str | None]:
    """(rows for one log record, session-model update or None).

    `calls` is mutated in place (tool/call registers, tool/result pops).
    Unknown types yield no rows (turn/step markers, titles, system
    prompts and todo/workspace metadata carry no searchable text).
    """
    if typ == "model/selection":
        return [], data.get("model") or None
    if typ == "user/message":
        m = data.get("message", data)
        text = _dsh_content_text(m.get("content")) if isinstance(m, dict) else ""
        if text:
            return [_row(pid, pid, sid, _ms(ts), "user", "", "", "text", text, {}, src)], None
        return [], None
    if typ == "assistant/message":
        m = data.get("message") or {}
        srcinfo = m.get("source") or {}
        model = srcinfo.get("model", "") or ""
        return _dsh_assistant_rows(pid, sid, ts, m, model, src), (model or None)
    if typ == "tool/call":
        if data.get("callId"):
            calls[data["callId"]] = (pid, ts, data)
        return [], None
    if typ == "tool/result":
        m = data.get("message") or {}
        row = _dsh_tool_result_row(pid, sid, ts, data, m, calls, src)
        return ([row] if row is not None else []), None
    return [], None


def _harvest_dsh_file(
    path: Path, sid: str, src: str, after_seq: int | None
) -> tuple[list[dict], int]:
    """(rows, max seq seen). part_ids are stable ({sid}:{seq}[#{i}]) — the
    log is append-only, so re-reads merge newest-wins instead of
    duplicating. Records at/below after_seq are already harvested.
    turn/step markers, titles, system prompts and todo/workspace metadata
    carry no searchable text and are skipped with reason (no legacy dsh
    corpus exists, so no parity constraint). Rows without a model take
    the session model (model/selection, last wins) so the facet stays
    dense — tool and user rows carry no per-row model.
    """
    rows = []
    max_seq = -1
    sess_model = ""
    calls: dict[str, tuple] = {}
    for seq, ts, typ, data in _dsh_read_log(path):
        if isinstance(seq, int) and seq > max_seq:
            max_seq = seq
        if after_seq is not None and isinstance(seq, int) and seq <= after_seq:
            continue
        pid = f"{sid}:{seq}"
        rec_rows, model_update = _dsh_record_rows(typ, pid, sid, ts, data, calls, src)
        rows.extend(rec_rows)
        if model_update:
            sess_model = model_update
    if sess_model:
        rows = [r if r["model"] else {**r, "model": sess_model} for r in rows]
    return rows, max_seq


def _harvest_dsh(store: Store, writer: SessionWriter) -> str:
    stats, total = [], 0
    for path, sid, src in _dsh_logs():
        key = f"{src}:{sid}"
        wm_raw = store.watermark(key)
        wm = json.loads(wm_raw) if wm_raw else None
        st = path.stat()
        cur = [int(st.st_mtime), int(st.st_size)]
        if wm is not None and wm[1:] == cur:
            stats.append(f"{src}:{sid[:13]}: quiet")
            continue
        # Rotation/truncation (smaller file than recorded): the seq floor
        # is meaningless — full ingest, stable ids merge newest-wins.
        after_seq = None if (wm is None or int(st.st_size) < wm[2]) else wm[0]
        try:
            rows, max_seq = _harvest_dsh_file(path, sid, src, after_seq)
        except (OSError, zstandard.ZstdError) as exc:
            # A live session's concurrent append can tear the tail frame;
            # name it and keep the other sessions (opencode-lane rule).
            stats.append(f"{src}:{sid[:13]}: SKIP unreadable ({type(exc).__name__}: {exc})")
            continue
        writer.add("dsh", rows)
        store.register("dsh", rows)
        store.set_watermark(key, json.dumps([max_seq, *cur]))
        total += len(rows)
        stats.append(f"{src}:{sid[:13]}: {len(rows)} parts")
    return "\n".join(stats) + f"\ndsh total new: {total}"


def _zc_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _zc_model_value(v: object) -> str:
    """S1 (zcode_conversation_ingest_repair): a plain model string out of
    any recorded model shape. The normalized rollout carries
    `model: {modelId, providerId}` (older snapshot shapes add
    role/source/variant keys), which `_s` json-encoded into the corpus
    and broke model faceting — 14 distinct dict values over 7,670 parts."""
    if isinstance(v, str):
        s = v.strip()
        if not s.startswith("{"):
            return s
        try:
            v = json.loads(s)
        except json.JSONDecodeError:
            return s
    if isinstance(v, dict):
        return str(v.get("modelId") or "")
    return str(v or "")


def _zc_model(d: dict, resp: dict) -> str:
    """S1 resolution order: response.modelId ?? record.model.modelId."""
    mid = resp.get("modelId")
    if isinstance(mid, str) and mid:
        return mid
    return _zc_model_value(d.get("model"))


def _oc_model_value(v: object) -> str:
    """Model-id string out of any recorded model shape.

    Three vintages, one facet: v1 message/part rows carry a pre-normalized
    string (occasionally a dict-string, which the legacy lane json-encoded
    into the corpus and split the facet — measured 2026-10-11: opencode
    corpus rows carry `{"providerID": ..., "modelID": ...}`); zcode rows
    carry `{modelId}`; opencode v2 rows carry `{providerID, id, variant}`
    (no `modelId` key — the bare zcode normalizer returns "" for all of
    them, which would blank the facet while passing a naive no-brace check).
    """
    if isinstance(v, dict):
        return str(v.get("modelId") or v.get("modelID") or v.get("id") or "")
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("{"):
            try:
                d = json.loads(s)
            except json.JSONDecodeError:
                return s
            if isinstance(d, dict):
                return str(d.get("modelId") or d.get("modelID") or d.get("id") or "")
        return s
    return str(v or "")


def _migrate_opencode_models(corpus_root: Path) -> tuple[int, int]:
    """One-shot (opencode v2-lane S1): rewrite existing opencode parquet
    `model` values from dict-string shapes to plain model-id strings.

    Same argument as `_migrate_zcode_models`: sessions whose source rows
    rotated away never re-harvest, so this — not the watermark — is what
    makes the corpus facet-clean. Text is untouched, so the embed cache
    stays valid. Run once via --migrate-opencode-models, before the first
    S1 APPLY so old and new rows never split the facet.
    """
    fixed_files = fixed_rows = 0
    for f in sorted((corpus_root / "opencode" / "conversations").glob("*.parquet")):
        t = pq.read_table(f)
        models = t.column("model").to_pylist()
        new = [_oc_model_value(m) for m in models]
        if new == models:
            continue
        pq.write_table(
            pa.Table.from_pylist(
                [{**r, "model": m} for r, m in zip(t.to_pylist(), new)], schema=_SCHEMA
            ),
            f,
            compression="zstd",
        )
        fixed_rows += sum(1 for a, b in zip(models, new) if a != b)
        fixed_files += 1
    return fixed_files, fixed_rows


def _zcode_best_and_resps(files: list[Path]) -> tuple[dict | None, bool, dict, dict]:
    """Find the fullest request and collect responses by id.

    S2 (zcode_conversation_ingest_repair): only `full`-kind requests
    carry the whole context, so they are preferred for the conversation
    rows; a tail/delta fallback is flagged partial. ResponseId-less
    error records come back as error entries keyed by requestId —
    failures stay searchable instead of being skipped."""
    best, best_n = None, -1
    best_full, best_full_n = None, -1
    resps: dict[str, tuple] = {}
    errs: dict[str, tuple] = {}
    for f in files:
        for line in open(f, errors="replace"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            req = d.get("request") or {}
            n = req.get("messageCount") or 0
            if n > best_n:
                best, best_n = d, n
            if req.get("messagesKind") == "full" and n > best_full_n:
                best_full, best_full_n = d, n
            r = d.get("response") or {}
            rid = r.get("responseId")
            if rid:
                resps[rid] = (
                    d.get("startedAt"),
                    r.get("text") or "",
                    r.get("reasoningText") or "",
                    _zc_model(d, r),
                )
            elif d.get("error"):
                e = d["error"] or {}
                key = d.get("requestId") or f"attempt{len(errs)}"
                errs[key] = (
                    d.get("startedAt"),
                    f"{e.get('name') or 'Error'}: {e.get('message') or ''}".strip(),
                    _zc_model(d, r),
                )
    if best_full is not None:
        return best_full, False, resps, errs
    return best, best is not None, resps, errs


def _zcode_req_rows(best: dict, sid: str, src_tag: str, partial: bool) -> list[dict]:
    """Build request-message rows from the fullest request.

    S2: the request's messagesKind rides the meta — `full` rows are the
    whole conversation; `tail`/`delta` (and kind-blind fallbacks) are
    marked partial so a reader never mistakes a compacted context for
    the conversation."""
    rows = []
    rid = best.get("requestId") or "req"
    ts = _iso_ms(best.get("startedAt") or best.get("completedAt"))
    req = best.get("request") or {}
    meta: dict = {"messageOffset": req.get("messageOffset"), "contextKind": req.get("messagesKind")}
    if partial:
        meta["partial"] = True
    for i, m in enumerate(req.get("messages") or []):
        text = _zc_content(m.get("content"))
        if not text:
            continue
        rows.append(
            _row(
                f"zc:{sid}:{i}",
                rid,
                sid,
                ts,
                m.get("role", ""),
                "zcode",
                _zc_model(best, best.get("response") or {}),
                "req-msg",
                text,
                meta,
                f"zcode:{src_tag}",
            )
        )
    return rows


def _zcode_resp_rows(resps: dict, errs: dict, sid: str, src_tag: str) -> list[dict]:
    """Build response, reasoning, and error rows from collected attempts.

    S2: error attempts (no responseId) become searchable `error` rows
    keyed by synthetic `zc:<sid>:err:<requestId>` part ids."""
    rows = []
    for rid, (started, text, reasoning, model) in resps.items():
        ts = _iso_ms(started)
        if text:
            rows.append(
                _row(
                    f"zc:{sid}:resp:{rid}",
                    rid,
                    sid,
                    ts,
                    "assistant",
                    "zcode",
                    model,
                    "text",
                    text,
                    {},
                    f"zcode:{src_tag}",
                )
            )
        if reasoning:
            rows.append(
                _row(
                    f"zc:{sid}:reason:{rid}",
                    rid,
                    sid,
                    ts,
                    "assistant",
                    "zcode",
                    model,
                    "reasoning",
                    reasoning,
                    {},
                    f"zcode:{src_tag}",
                )
            )
    for rid, (started, message, model) in errs.items():
        rows.append(
            _row(
                f"zc:{sid}:err:{rid}",
                rid,
                sid,
                _iso_ms(started),
                "assistant",
                "zcode",
                model,
                "error",
                message,
                {},
                f"zcode:{src_tag}",
            )
        )
    return rows


def _harvest_zcode_file(files: list[Path], sid: str, src_tag: str) -> list[dict]:
    """One zcode session: fullest full-kind request = final context;
    responses by id; error attempts searchable (S1+S2)."""
    best, partial, resps, errs = _zcode_best_and_resps(files)
    rows = []
    if best:
        rows.extend(_zcode_req_rows(best, sid, src_tag, partial))
    rows.extend(_zcode_resp_rows(resps, errs, sid, src_tag))
    return rows


def _iso_ms(v: str | None) -> datetime:
    if not v:
        return datetime(1970, 1, 1, tzinfo=UTC)
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=UTC)


def _harvest_zcode(store: Store, writer: SessionWriter) -> str:
    by_sid: dict[str, list[Path]] = {}
    srcs: dict[str, str] = {}
    for base, tag in ((SNAP_ROOT, "snap"), (Path.home(), "live")):
        pat = (
            "*/localhost/home/arun/.zcode/cli/rollout/model-io-*.jsonl"
            if tag == "snap"
            else ".zcode/cli/rollout/model-io-*.jsonl"
        )
        for f in sorted(base.glob(pat)):
            sid = _safe(f.stem.replace("model-io-sess_", ""))
            by_sid.setdefault(sid, []).append(f)
            srcs[sid] = tag
    stats, total = [], 0
    for sid, files in sorted(by_sid.items()):
        key = f"zcode:{sid}"
        cur = json.dumps([[int(f.stat().st_mtime), f.stat().st_size] for f in files])
        if store.watermark(key) == cur:
            stats.append(f"zcode:{sid}: quiet")
            continue
        rows = _harvest_zcode_file(files, sid, srcs[sid])
        writer.add("zcode", rows)
        store.register("zcode", rows)
        store.set_watermark(key, cur)
        total += len(rows)
        stats.append(f"zcode:{sid}: {len(rows)} parts")
    return "\n".join(stats) + f"\nzcode total new: {total}"


def _lane_uncovered(
    con, corpus_root: Path, stored_v2: int | None, stored_evt: int | None
) -> dict[str, str]:
    """Per-lane drift detail for one opencode source: {lane: line}.

    A lane fires when some session holds rows newer than both its corpus
    seed and the lane's stored watermark — the check-mode mirror of the
    harvest seeding rule (`_corpus_max_ts`). Missing tables mean the lane
    is absent (pre-v2 snapshots), never drift. An unreadable source raises
    to the caller: check mode must fail loudly on corruption, matching the
    apply path's SKIP only in harvest (where one bad source must not cost
    the rest).
    """
    out: dict[str, str] = {}
    seeds: dict[str, int | None] = {}

    def seed_for(sid: str) -> int:
        if sid not in seeds:
            seeds[sid] = _corpus_max_ts(corpus_root, sid)
        seed = seeds[sid]
        return seed if seed is not None else -1

    try:
        v2max = con.execute(
            "SELECT session_id, MAX(time_updated) FROM session_message GROUP BY session_id"
        ).fetchall()
    except sqlite3.OperationalError:
        v2max = None
    if v2max:
        floor = stored_v2 if stored_v2 is not None else -1
        new_sessions = [s for s, t in v2max if (t or 0) > max(seed_for(s), floor)]
        if new_sessions:
            out["v2"] = f"{len(new_sessions)} sessions ({max(t for _, t in v2max)})"
    try:
        evtmax = con.execute(
            "SELECT json_extract(data,'$.part.sessionID'), MAX(json_extract(data,'$.time')) "
            "FROM event WHERE type='message.part.updated.1' GROUP BY 1"
        ).fetchall()
    except sqlite3.OperationalError:
        evtmax = None
    if evtmax:
        floor = stored_evt if stored_evt is not None else -1
        new_sessions = [s for s, t in evtmax if s and (t or 0) > max(seed_for(s), floor)]
        if new_sessions:
            out["evt"] = f"{len(new_sessions)} sessions ({max(t for _, t in evtmax if t)})"
    return out


def _wm_int(store: Store, key: str) -> int | None:
    raw = store.watermark(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _opencode_source_drift(
    store: Store, corpus_root: Path, path: Path, src: str, immutable: bool
) -> list[str]:
    """Drift lines for one opencode source (snapshot or live)."""
    key = f"{src}:{hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()}"
    found = []
    if immutable:
        if store.watermark(key) is None:
            found.append(f"new {src}")
        # per-lane backfill visibility on already-seen snapshots: the
        # :v2/:evt keys never existed before the lanes, so their first
        # check fires here and the next APPLY closes them (see S3).
        try:
            con = connect(path, read_only=True)
        except sqlite3.DatabaseError, OSError:
            return [f"unreadable {src}"]
        try:
            detail = _lane_uncovered(
                con, corpus_root, _wm_int(store, key + ":v2"), _wm_int(store, key + ":evt")
            )
        finally:
            con.close()
        for lane, line in detail.items():
            verb = "new" if store.watermark(key + ":" + lane) is None else "changed"
            found.append(f"{verb} {src}:{lane} ({line})")
        return found
    con = connect(path, read_only=True)
    try:
        row = con.execute("SELECT COALESCE(MAX(time_updated), 0) FROM part").fetchone()
        hwm = row[0] if row else 0
        lane_detail = _lane_uncovered(
            con, corpus_root, _wm_int(store, key + ":v2"), _wm_int(store, key + ":evt")
        )
    finally:
        con.close()
    wm = store.watermark(key)
    if wm is None or int(wm) < int(hwm):
        found.append(f"changed {src} ({hwm})")
    for lane, detail in lane_detail.items():
        found.append(f"changed {src}:{lane} ({detail})")
    return found


def _dsh_log_drift(store: Store, path: Path, sid: str, src: str) -> str | None:
    """One dsh log's drift line; None when the watermark covers it."""
    key = f"{src}:{sid}"
    wm_raw = store.watermark(key)
    try:
        st = path.stat()
    except OSError:
        return f"missing {src}:{sid[:13]} ({path})"
    cur = json.loads(wm_raw) if wm_raw else None
    if cur is None:
        return f"new {src}:{sid[:13]}"
    if cur[1:] != [int(st.st_mtime), int(st.st_size)]:
        return f"changed {src}:{sid[:13]}"
    return None


def _snap_count_drift(store: Store) -> list[str]:
    """Snapshot-root visibility drift (lane-blind when the root is gone)."""
    if not SNAP_ROOT.is_dir():
        return [f"missing snapshot root {SNAP_ROOT} (snapshot lanes blind)"]
    cur_n = len(_snapshot_opencode_dbs())
    rec = store.watermark(_SNAP_COUNT_KEY)
    if rec is not None and cur_n < int(rec):
        return [f"opencode-snap sources shrank {rec}->{cur_n} ({SNAP_ROOT})"]
    return []


def _drift(store: Store, corpus_root: Path = CORPUS_ROOT) -> list[str]:
    """--check verdict: sources whose current fingerprint differs from the watermark."""
    drift = []
    for path, src, immutable in _opencode_sources():
        drift.extend(_opencode_source_drift(store, corpus_root, path, src, immutable))
    drift.extend(_snap_count_drift(store))
    for path, sid, src in _dsh_logs():
        line = _dsh_log_drift(store, path, sid, src)
        if line is not None:
            drift.append(line)
    for line in _dsh_index_lag():
        drift.append(line)
    for f, src in _prime_files():
        key = f"{src}:{hashlib.blake2b(str(f).encode(), digest_size=6).hexdigest()}"
        st = f.stat()
        wm_raw = store.watermark(key)
        cur = json.loads(wm_raw) if wm_raw else None
        if cur != [int(st.st_mtime), st.st_size]:
            drift.append(f"changed {src} ({f.name})")
    for sid, files in _zcode_groups().items():
        key = f"zcode:{sid}"
        cur = json.dumps([[int(f.stat().st_mtime), f.stat().st_size] for f in files])
        if store.watermark(key) != cur:
            drift.append(f"changed zcode:{sid}")
    return drift


def _zcode_groups() -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for base, tag in ((SNAP_ROOT, "snap"), (Path.home(), "live")):
        pat = (
            "*/localhost/home/arun/.zcode/cli/rollout/model-io-*.jsonl"
            if tag == "snap"
            else ".zcode/cli/rollout/model-io-*.jsonl"
        )
        for f in sorted(base.glob(pat)):
            groups.setdefault(_safe(f.stem.replace("model-io-sess_", "")), []).append(f)
    return groups


def _migrate_zcode_models(corpus_root: Path) -> tuple[int, int]:
    """S1 one-shot (zcode_conversation_ingest_repair): rewrite existing
    zcode parquet `model` values from the pre-normalization dict shapes
    to the plain modelId string. Sessions whose rollout file already
    rotated away never re-harvest, so this — not the watermark — is what
    makes the corpus facet-clean (zero `{` model values). Text is
    untouched, so the embed cache stays valid."""
    fixed_files = fixed_rows = 0
    for f in sorted((corpus_root / "zcode" / "conversations").glob("*.parquet")):
        t = pq.read_table(f)
        models = t.column("model").to_pylist()
        new = [_zc_model_value(m) for m in models]
        if new == models:
            continue
        pq.write_table(
            pa.Table.from_pylist(
                [{**r, "model": m} for r, m in zip(t.to_pylist(), new)], schema=_SCHEMA
            ),
            f,
            compression="zstd",
        )
        fixed_rows += sum(1 for a, b in zip(models, new) if a != b)
        fixed_files += 1
    return fixed_files, fixed_rows


def harvest(
    check: bool = False,
    corpus_root: Path = CORPUS_ROOT,
    db: Path = INDEX_DB,
    timer=None,
) -> dict:
    """The S1 core: union all sources into the per-session parquet corpus.

    ``timer`` (a maint_timing.RunTimer, optional so tests keep calling
    with kwargs) receives one phase per lane plus the flush, with
    wall-clock start/end — the queryable record behind maint_query.
    """
    t0 = time.perf_counter()
    store = Store(db, read_only=check)
    try:
        if check:
            drift = _drift(store, corpus_root)
            fresh = not drift
            return {
                "check": True,
                "index_stale": not fresh,
                "stale_new": drift,
                "stale_changed": [],
                "stale_deleted": [],
                "elapsed": 0.0,
            }
        writer = SessionWriter(corpus_root)
        lanes, lane_seconds = [], {}
        for name, fn in (
            ("opencode", _harvest_opencode),
            ("prime-rlm", _harvest_prime),
            ("zcode", _harvest_zcode),
            ("dsh", _harvest_dsh),
        ):
            if timer is not None:
                with timer.phase(f"lane:{name}"):
                    t_lane = time.perf_counter()
                    lanes.append(fn(store, writer))
                    lane_seconds[name] = round(time.perf_counter() - t_lane, 2)
            else:
                t_lane = time.perf_counter()
                lanes.append(fn(store, writer))
                lane_seconds[name] = round(time.perf_counter() - t_lane, 2)
        if timer is not None:
            with timer.phase("flush"):
                t_flush = time.perf_counter()
                sessions = writer.flush()
                lane_seconds["flush"] = round(time.perf_counter() - t_flush, 2)
        else:
            t_flush = time.perf_counter()
            sessions = writer.flush()
            lane_seconds["flush"] = round(time.perf_counter() - t_flush, 2)
        store.commit()
        store.close()
        return {
            "check": False,
            "lanes": "\n".join(lanes),
            "sessions_written": sessions,
            "lane_seconds": lane_seconds,
            "elapsed": time.perf_counter() - t0,
        }
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--check",
        action="store_true",
        help="report drift, exit 1 if the corpus is behind; no writes",
    )
    p.add_argument("--corpus-root", default=str(CORPUS_ROOT))
    p.add_argument("--db", default=str(INDEX_DB))
    p.add_argument(
        "--migrate-zcode-models",
        action="store_true",
        help="one-shot (zcode_conversation_ingest_repair S1): rewrite existing "
        "zcode parquet model dicts to plain modelId strings; no harvest",
    )
    p.add_argument(
        "--migrate-opencode-models",
        action="store_true",
        help="one-shot (opencode v2-lane S1): rewrite existing opencode "
        "parquet model dict-strings to plain model-id strings; no harvest",
    )
    args = p.parse_args(argv)
    if args.migrate_zcode_models:
        files, rows = _migrate_zcode_models(Path(args.corpus_root))
        print(
            f"migrated {rows} model values across {files} zcode parquet files",
            file=sys.stderr,
        )
        return 0
    if args.migrate_opencode_models:
        files, rows = _migrate_opencode_models(Path(args.corpus_root))
        print(
            f"migrated {rows} model values across {files} opencode parquet files",
            file=sys.stderr,
        )
        return 0
    from helpers.maintenance.maint_timing import RunTimer

    timer = RunTimer("harvest_conversations", mode="check" if args.check else "apply")
    out = harvest(
        check=args.check,
        corpus_root=Path(args.corpus_root),
        db=Path(args.db),
        timer=timer,
    )
    if out["check"]:
        if out["index_stale"]:
            print("corpus state: STALE — sources drifted:", file=sys.stderr)
            for d in out["stale_new"][:15]:
                print(f"  {d}", file=sys.stderr)
            print("refresh: make convo-fresh APPLY=1", file=sys.stderr)
            return timer.finish(1, f"STALE sources={len(out['stale_new'])}")
        print("corpus state: FRESH", file=sys.stderr)
        return timer.finish(0, "FRESH")
    print(out["lanes"], file=sys.stderr)
    per_lane = " ".join(f"{k}={v}s" for k, v in out["lane_seconds"].items())
    print(
        f"✓ corpus: {out['sessions_written']} session files written "
        f"in {out['elapsed']:.1f}s ({Path(args.db).stat().st_size // 1024} KiB meta db)",
        file=sys.stderr,
    )
    print(f"  lanes: {per_lane}", file=sys.stderr)
    return timer.finish(0, f"sessions_written={out['sessions_written']}")


if __name__ == "__main__":
    sys.exit(main())
