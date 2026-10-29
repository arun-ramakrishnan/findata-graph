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
  per-response ``toolCalls`` as searchable ``tool`` rows (S7,
  convo_reasoning_capture), responseId-less error records as
  searchable ``error`` rows (S2).
- bili: live billion-context ``sessions/index.db`` compression summaries
  (ALL blocks regardless of `active`; per-session {block: [hash, active]}
  fingerprint, quiet on match).

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

from helpers.core import jsonio  # noqa: E402  (grouped with core imports)
from helpers.core.db import connect  # noqa: E402
from helpers.core import duckdb_shared  # noqa: E402  (bare-script entry point; bootstrap above)
from helpers.core.ranges import RangeError, cutoff_from  # noqa: E402  (bare-script entry point; bootstrap above)

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
            # Wire-capable writer: when the store is served, connect_shared
            # attaches over the quack wire and the server serializes this
            # writer against readers (Slice B wave 2). Unserved it is the
            # plain file connect below — the LOCK_EX flock above stays the
            # file-mode coordination.
            self.con = duckdb_shared.connect_shared(str(db), read_only=False)
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
            # Literal SQL, one statement per row: prepared parameters do
            # not cross the quack wire (§2.6), and file mode takes the
            # literals identically — one audited path for both modes.
            # Batches are small (pending watermarks), so the loop cost
            # is noise.
            for key, value in self._pending_wm:
                con.execute(
                    "INSERT OR REPLACE INTO harvest_meta VALUES ("  # noqa: S608  — key/value via ds.sql_lit, wire-safe claiming path
                    f"{duckdb_shared.sql_lit(key)}, {duckdb_shared.sql_lit(value)}, now())"
                )
            self._pending_wm.clear()
        if self._pending_parts:
            for row in self._pending_parts:
                lits = ", ".join(duckdb_shared.sql_lit(v) for v in row)
                con.execute(f"INSERT OR REPLACE INTO part_index VALUES ({lits})")  # noqa: S608  — tuple lits via ds.sql_lit
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
        self.fresh: set[tuple[str, str]] = set()

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
            if f.exists() and (harness, sid) not in self.fresh:
                for r in pq.read_table(f).to_pylist():
                    buf.setdefault(r["part_id"], r)
            rows = sorted(buf.values(), key=lambda r: (r["ts"], r["part_id"]))
            pq.write_table(pa.Table.from_pylist(rows, schema=_SCHEMA), f, compression="zstd")
            written += 1
        return written


def _oc_msg_model(d: dict) -> object:
    """Model value of an opencode `message` row, across schema vintages.

    Assistant rows in every store surveyed carry `modelID` and NOT `model`
    (measured 2026-10-18: 24,155 of 24,155 assistant rows across the two
    newest snapshots) — so reading `model` alone blanked the facet for the
    whole v2 era: 129,430 of 167,274 opencode corpus rows carry `model=''`.
    The older store wrote `model`, a nested dict on a user message, hence
    the pass-through for `_oc_model_value` rather than a `str()` here.
    """
    if not isinstance(d, dict):
        return ""
    return d.get("modelID") or d.get("model") or ""


def _oc_reasoning_meta(d: dict) -> dict:
    """Why a reasoning row can carry no text — the provider's own marker.

    Newer opencode stores OpenAI-Responses *encrypted* reasoning with an
    empty `text` (`metadata.openai.reasoningEncryptedContent` + `itemId`;
    measured 2026-10-18: 71 of the 76 empty reasoning parts in the live
    store). Unrecorded, such a row is indistinguishable from a harvest
    defect — the corpus held 9,657 empty reasoning rows with not one
    carrying the marker. Records the fact and the size, never the ciphertext.
    """
    meta = d.get("metadata")
    oai = meta.get("openai") if isinstance(meta, dict) else None
    if not isinstance(oai, dict):
        return {}
    enc = oai.get("reasoningEncryptedContent")
    if not enc:
        return {}
    return {"encrypted": True, "enc_bytes": len(enc), "item_id": oai.get("itemId")}


def _oc_part_rows(cur_parts: Iterator[tuple], msgs: dict[str, tuple], src: str) -> list[dict]:
    out = []
    for pid, mid, sid, tc, tu, data in cur_parts:
        d = jsonio.loads(data)
        ptype = d.get("type") or "unknown"
        text, meta = "", {}
        if ptype in ("text", "reasoning"):
            text = d.get("text") or ""
            if ptype == "reasoning":
                meta = _oc_reasoning_meta(d)
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
                (jsonio.loads(r[1]) or {}).get("role", ""),
                (jsonio.loads(r[1]) or {}).get("agent", ""),
                _oc_msg_model(jsonio.loads(r[1]) or {}),
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
                model = jsonio.loads(model)
            except jsonio.JSONDecodeError:
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
                d = jsonio.loads(data) or {}
            except jsonio.JSONDecodeError:
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
                d = jsonio.loads(data) or {}
            except jsonio.JSONDecodeError:
                continue
            out[mid] = (
                d.get("role", ""),
                d.get("agent", ""),
                _oc_model_value(_oc_msg_model(d)),
            )
    except sqlite3.OperationalError:
        pass
    return out


def _assistant_block_row(
    pid: str, mid: str, sid: str, ts: datetime, agent: str, model: str, b: dict, src: str
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
                d = jsonio.loads(data) or {}
            except jsonio.JSONDecodeError:
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
                d = jsonio.loads(data)
            except jsonio.JSONDecodeError:
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


def _harvest_opencode(store: Store, writer: SessionWriter, *, force: bool = False) -> str:
    stats, parts_total = [], 0
    n_snaps = 0
    for path, src, immutable in _opencode_sources():
        key = f"{src}:{hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()}"
        if immutable:
            n_snaps += 1
        hwm_raw = store.watermark(key)
        if immutable and hwm_raw is not None and not force:
            stats.append(f"{src}: quiet")
        else:
            # force means FULL re-read: the per-source hwm filter would
            # re-scan the db yet re-emit nothing already harvested — a
            # parser fix stays unrecoverable (measured 2026-10-18: a
            # forced opencode pass with the filter intact rewrote 1 file,
            # live traffic only; part_ids are stable, so a full re-read
            # merges, never duplicates).
            hwm = None if force else (int(hwm_raw) if hwm_raw is not None else None)
            # One unreadable source must not abort the whole lane: a corrupt or
            # truncated backup image otherwise costs every other snapshot AND the
            # live db, and surfaces as a bare "disk I/O error" that names no file.
            # Observed 2026-09-29: timeshift snapshot 2026-09-28_23-23-31 failed
            # integrity_check ("database disk image is malformed") at message
            # rowid 29963, while the other 7 sources scanned clean. Skip + name it.
            # Two corruption modes live here: a bad sqlite image raises
            # sqlite3.DatabaseError/OSError at open, and a valid image with a
            # corrupt row raises jsonio.JSONDecodeError or AttributeError (JSON
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
                jsonio.JSONDecodeError,
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
            if immutable and store.watermark(lkey) is not None and not force:
                stats.append(f"{src}:{lane}: quiet")
                continue
            try:
                lrows, lnew = fn(path, src, seeds, writer.root)
            except (
                sqlite3.DatabaseError,
                OSError,
                jsonio.JSONDecodeError,
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


# prime content-block kinds → corpus part_type. A `thinking` block keeps its
# reasoning under `thinking` (Anthropic shape, plus `thinkingSignature` and
# `redacted`), NOT `text` — reading `text` alone dropped 100% of prime's
# reasoning and every tool call (measured 2026-10-18: snapshots hold 4,425
# thinking blocks / 10,535,564 chars and 9,272 toolCall blocks, while the
# corpus held 0 reasoning and 0 tool rows for prime-rlm — 39% of the lane's
# conversation content was absent from the archive and searchable nowhere).
_PRIME_THINKING = ("thinking", "redacted_thinking")


def _prime_block(blk: object) -> tuple[str, str, dict]:
    """(text, part_type, meta) for one prime content block; "" text skips it."""
    if not isinstance(blk, dict):
        return str(blk or ""), "text", {}
    btype = blk.get("type") or "text"
    if btype in _PRIME_THINKING:
        meta = {
            "block_type": btype,
            "signature": blk.get("thinkingSignature"),
            "redacted": bool(blk.get("redacted")) or None,
        }
        return (
            blk.get("thinking") or blk.get("text") or "",
            "reasoning",
            {k: v for k, v in meta.items() if v is not None},
        )
    if btype == "toolCall":
        args = blk.get("arguments")
        meta = {"block_type": btype, "tool": blk.get("name"), "call_id": blk.get("id")}
        return (
            args if isinstance(args, str) else json.dumps(args or {}),
            "tool",
            {k: v for k, v in meta.items() if v is not None},
        )
    return blk.get("text") or "", "text", {"block_type": btype}


def _harvest_prime_file(f: Path, src: str) -> list[dict]:
    rows = []
    for line in open(f, "rb"):
        try:
            d = jsonio.loads(line)
        except jsonio.JSONDecodeError:
            continue
        if d.get("type") not in ("message", "custom_message"):
            continue
        m = d.get("message") or {}
        sid = m.get("sessionID") or d.get("sessionID") or f.stem
        ts = _ms(d.get("timestamp"))
        for i, blk in enumerate(m.get("content") or []):
            text, ptype, meta = _prime_block(blk)
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
                    ptype,
                    text,
                    meta,
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


def _harvest_prime(store: Store, writer: SessionWriter, *, force: bool = False) -> str:
    """`force` ignores the per-file watermark (a parser change must re-read a
    source the watermark declares done; see `--force-lane`). A re-read emits
    the same part_ids, so it merges instead of duplicating."""
    stats, total = [], 0
    for f, src in _prime_files():
        key = f"{src}:{hashlib.blake2b(str(f).encode(), digest_size=6).hexdigest()}"
        wm_raw = store.watermark(key)
        wm = jsonio.loads(wm_raw) if wm_raw else None
        st = f.stat()
        cur = [int(st.st_mtime), st.st_size]
        if wm == cur and not force:
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
            d = jsonio.loads(line)
        except jsonio.JSONDecodeError:
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


def _harvest_dsh(store: Store, writer: SessionWriter, *, force: bool = False) -> str:
    stats, total = [], 0
    for path, sid, src in _dsh_logs():
        key = f"{src}:{sid}"
        wm_raw = store.watermark(key)
        wm = jsonio.loads(wm_raw) if wm_raw else None
        st = path.stat()
        cur = [int(st.st_mtime), int(st.st_size)]
        if wm is not None and wm[1:] == cur and not force:
            stats.append(f"{src}:{sid[:13]}: quiet")
            continue
        # Rotation/truncation (smaller file than recorded): the seq floor
        # is meaningless — full ingest, stable ids merge newest-wins.
        after_seq = None if (force or wm is None or int(st.st_size) < wm[2]) else wm[0]
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


def _bili_index_path() -> Path:
    """Resolved per call (not at import) so Path.home patching in tests
    isolates the lane — same pattern as the dsh lane."""
    return Path.home() / ".local" / "share" / "billion-context" / "sessions" / "index.db"


# Harnesses whose session files the bili lane owns outright — i.e. no other
# harvest lane writes them. 'unknown' is the index-without-a-sessions-table
# case. A namespace that HAS a lane (opencode, prime-rlm, zcode, dsh) is
# excluded: `fresh` would make flush() overwrite that lane's rows.
_BILI_OWNED_HARNESSES = {"unknown"}


def _bili_root() -> Path:
    """The billion-context store root that CONTAINS the session index.

    Distinguishes "this machine has no billion-context" (a quiet skip —
    the lane's designed behaviour) from "billion-context is installed but
    the index is gone" (storage migration or a shadow-store reset, which
    silently truncates this lane's history). Review 2026-10-17: under
    billion-context 0.1.197 `storage.unified=shadow` every session write
    is double-ingested into `sessions/index.db` and digest-reconciled,
    making that file a derived artefact whose lifecycle is governed by
    the proxy's migration, not by us. A reset must not read as healthy.
    """
    return _bili_index_path().parent.parent


def _bili_index_state() -> str:
    """'ready' | 'absent' | 'lost' — 'lost' is present-root/missing-index."""
    idx = _bili_index_path()
    if idx.exists():
        return "ready"
    return "lost" if _bili_root().exists() else "absent"


def _bili_absent_note(state: str, idx: Path) -> str:
    """One line for both the harvest and --check paths.

    'absent' keeps the lane's designed quiet skip (no proxy on this
    machine). 'lost' is NOT quiet: the proxy is installed, so a missing
    index is a reset or a storage migration, and reporting it as a skip
    would silently truncate this lane's history while the gate stayed
    green.
    """
    if state == "absent":
        return "bili: SKIP missing index.db (no billion-context on this machine)"
    return (
        f"bili: LOST index.db — billion-context present at {_bili_root()} but "
        f"{idx} is gone (shadow-store reset or storage migration); "
        f"this lane's history is NOT harvested"
    )


def _bili_namespace(con, sid: str) -> str:
    """The host namespace that owns this session ('opencode', 'dsh', …).

    Bili compression blocks for a `dsh` session were being filed under
    harness 'bili' with nothing recording the true owner, so dsh- and
    opencode-hosted summaries were indistinguishable inside the lane.

    billion-context became a harness-specific plugin system (2026-10-17),
    so a namespace is the harness whose sessions it compressed, not a peer
    of them. The lane therefore ROUTES: a dsh session's summaries go to
    harness `dsh`, an opencode session's to `opencode`, joining that lane's
    rows in the same session file. Rows stay under 'bili' only if the index
    reports a namespace with no corpus directory.

    Safe because the lanes use disjoint part_id schemes — bili `sid#bid`,
    opencode/dsh `sid:seq` — and share ONE SessionWriter, so both lanes'
    rows land in the same per-session file. Routing also retires a real
    duplicate: every bili session file collided by NAME with the owning
    lane's own copy, holding the same session twice.

    Degrades to 'unknown' on an index without a sessions table.
    """
    try:
        row = con.execute("SELECT namespace FROM sessions WHERE session_id = ?", [sid]).fetchone()
    except sqlite3.Error:
        return "unknown"
    return (row[0] if row else None) or "unknown"


def _bili_fingerprint(con, sid: str) -> dict:
    """{block_id: [summary_hash, active]} — deactivation is a metadata flip,
    so `active` rides the fingerprint; a pure active-flip still re-harvests
    the row (bili_lane proposal S3).

    `v` versions the ROW SHAPE — the part_id scheme — and bumping it forces
    a clean rewrite so two generations never union (v1->v2: session-scoped
    part_ids). `ns` rides the fingerprint WITHOUT bumping `v`: the owning
    namespace changes only a meta field, so its rows must be re-harvested but
    must MERGE into the namespace's session file, which another lane owns.
    Bumping `v` for it would mark the session fresh and flush() would write
    the bili rows alone over that lane's parts (review 2026-10-17).
    """
    return {
        "v": 2,
        "ns": _bili_namespace(con, sid),
        "b": {
            bid: [sh, act]
            for bid, sh, act in con.execute(
                "SELECT block_id, summary_hash, active FROM blocks WHERE session_id = ?",
                [sid],
            )
        },
    }


def _harvest_bili(store: Store, writer: SessionWriter, *, force: bool = False) -> str:
    """BILI compression summaries (bili_convo_lane proposal S1-S3).

    Every block is indexed regardless of `active` — manual compacts
    deactivate blocks without replacement and the inactive set holds
    unique topics (trial 4/10 ACTIVE-only vs 9/10 ALL). Missing store is a
    quiet skip (source absent, not drift), so machines without the proxy
    stay green.
    """
    idx = _bili_index_path()
    state = _bili_index_state()
    if state != "ready":
        return _bili_absent_note(state, idx)
    stats, total = [], 0
    con = connect(idx, read_only=True)
    try:
        sessions = sorted(r[0] for r in con.execute("SELECT DISTINCT session_id FROM blocks"))
        for sid in sessions:
            ns = _bili_namespace(con, sid)
            key = f"bili:{sid}"
            cur = _bili_fingerprint(con, sid)
            wm_raw = store.watermark(key)
            wm = jsonio.loads(wm_raw) if wm_raw else None
            if wm is not None and wm == cur and not force:
                stats.append(f"bili:{sid[:13]}: quiet")
                continue
            if wm is None or wm.get("v") != cur["v"]:
                # Row-shape migration normally forces a clean rewrite so two
                # generations never union (live lesson: 72 corpus rows indexed
                # as 57 + 57 stale doublons). That `fresh` mark is ONLY safe
                # when bili owns the session file outright: `fresh` makes
                # flush() write the touched buffer ALONE, so on a routed
                # harness it would erase the owning lane's rows for that same
                # session. Routed lanes therefore merge unconditionally —
                # correct because part_ids are keyed `sid#bid` and stable, so
                # a re-harvest overwrites its own rows in place.
                if ns in _BILI_OWNED_HARNESSES:
                    writer.fresh.add((ns, sid))
            rows = []
            for bid, payload, created, tier, act in con.execute(
                "SELECT block_id, payload_json, created_at, tier, active"
                " FROM blocks WHERE session_id = ?",
                [sid],
            ):
                try:
                    d = jsonio.loads(payload)
                except jsonio.JSONDecodeError, TypeError:
                    continue
                topic = d.get("topic") or ""
                text = (topic + "\n" + d.get("summary", "")).strip()
                if not text:
                    continue
                rows.append(
                    _row(
                        f"{sid}#{bid}",  # session-scoped: bare block ids
                        bid,  # collide across sessions on the
                        sid,  # (harness, part_id) primary key
                        _ms(created),
                        "assistant",
                        "bili",
                        "",
                        "compression-summary",
                        text,
                        {
                            "tier": tier,
                            "active": bool(act),
                            "topic": topic,
                            "namespace": ns,
                            "summary_hash": cur["b"].get(bid, [None])[0],
                            "created_at": created,
                        },
                        f"bili-index/{ns}",
                    )
                )
            writer.add(ns, rows)
            store.register(ns, rows)
            store.set_watermark(key, json.dumps(cur, sort_keys=True))
            total += len(rows)
            stats.append(f"bili:{sid[:13]} ({ns}): {len(rows)} parts")
    finally:
        con.close()
    return "\n".join(stats) + f"\nbili total new: {total}"


def _bili_drift(store: Store) -> list[str]:
    """--check lines for the bili lane.

    Silent only when billion-context is genuinely absent. A present root
    with a missing index reports drift (review 2026-10-17): the quiet-skip
    path used to cover both cases, so a shadow-store reset read as healthy.
    """
    idx = _bili_index_path()
    state = _bili_index_state()
    if state == "absent":
        return []
    if state == "lost":
        return [_bili_absent_note(state, idx)]
    drift = []
    try:
        con = connect(idx, read_only=True)
        try:
            sessions = sorted(r[0] for r in con.execute("SELECT DISTINCT session_id FROM blocks"))
            for sid in sessions:
                key = f"bili:{sid}"
                wm_raw = store.watermark(key)
                wm = jsonio.loads(wm_raw) if wm_raw else None
                if wm != _bili_fingerprint(con, sid):
                    drift.append(f"changed bili:{sid[:13]}")
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        drift.append(f"unreadable bili index ({type(exc).__name__})")
    return drift


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
            v = jsonio.loads(s)
        except jsonio.JSONDecodeError:
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
                d = jsonio.loads(s)
            except jsonio.JSONDecodeError:
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
        for line in open(f, "rb"):
            try:
                d = jsonio.loads(line)
            except jsonio.JSONDecodeError:
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
                    r.get("toolCalls") or [],
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
    """Build response, reasoning, tool, and error rows from collected attempts.

    S2: error attempts (no responseId) become searchable `error` rows
    keyed by synthetic `zc:<sid>:err:<requestId>` part ids. S7
    (convo_reasoning_capture): each `response.toolCalls` entry becomes a
    searchable `tool` row — `zc:<sid>:tool:<rid>:<i>` — text is the
    serialised `input` (the command/arguments are the searchable content),
    meta keeps the tool name + call id (prime's `_prime_block` convention),
    so a forced re-read merges on stable part ids instead of duplicating.
    Measured 2026-10-18: 87 of 102 live rollout records carry toolCalls
    while the corpus held 0 zcode tool rows."""
    rows = []
    for rid, (started, text, reasoning, model, tool_calls) in resps.items():
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
        for i, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            inp = call.get("input")
            call_text = inp if isinstance(inp, str) else json.dumps(inp or {})
            meta = {"tool": call.get("name"), "call_id": call.get("id")}
            rows.append(
                _row(
                    f"zc:{sid}:tool:{rid}:{i}",
                    rid,
                    sid,
                    ts,
                    "assistant",
                    "zcode",
                    model,
                    "tool",
                    call_text,
                    {k: v for k, v in meta.items() if v is not None},
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


def _harvest_zcode(store: Store, writer: SessionWriter, *, force: bool = False) -> str:
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
        if store.watermark(key) == cur and not force:
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
    cur = jsonio.loads(wm_raw) if wm_raw else None
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


def _coverage(store: Store) -> list[str]:
    """One quiet-confirmation line per lane for --check mode: drift output
    alone can't distinguish 'covered and quiet' from 'lane never ran'."""
    lines = []
    try:
        lines.append(f"opencode: {len(_opencode_sources())} sources tracked")
    except OSError as exc:
        lines.append(f"opencode: source enumeration failed ({exc})")
    try:
        lines.append(f"prime-rlm: {len(_prime_files())} session files tracked")
    except OSError as exc:
        lines.append(f"prime-rlm: source enumeration failed ({exc})")
    try:
        zc = _zcode_groups()
        lines.append(
            f"zcode: {len(zc)} sessions tracked ({sum(len(v) for v in zc.values())} files)"
        )
    except OSError as exc:
        lines.append(f"zcode: source enumeration failed ({exc})")
    try:
        lines.append(f"dsh: {len(_dsh_logs())} session logs tracked")
    except OSError as exc:
        lines.append(f"dsh: source enumeration failed ({exc})")
    idx = _bili_index_path()
    if _bili_index_state() != "ready":
        lines.append(_bili_absent_note(_bili_index_state(), idx))
    else:
        n_ses, n_blocks = 0, 0
        for key, raw in store._wm.items():
            if not key.startswith("bili:"):
                continue
            try:
                n_ses += 1
                n_blocks += len(jsonio.loads(raw).get("b", {}))
            except ValueError, AttributeError:
                continue
        lines.append(f"bili: {n_ses} sessions tracked ({n_blocks} blocks fingerprinted)")
    return lines


def _provenance_drift(corpus_root: Path = CORPUS_ROOT, index_db: Path = INDEX_DB) -> list[str]:
    """Rows whose `convo_search.provenance` disagrees with the parquet's `source`.

    `provenance` is a COPY taken at read time. An in-place rewrite of `source`
    — which is what `--resolve-orphans` does, and what any future provenance
    migration would do — moves no file and no row, so the rebuild's diff sees
    nothing and the two diverge silently. `--resolve-orphans` now refreshes the
    index itself, but a convention that only holds while everyone remembers it
    is not a check. Measured 2026-10-17: 912 rows were wrong after a relabel and
    `make convo-fresh APPLY=1` did not correct them.

    The join is positional on `(file_path, row_no)`, the same key
    `_backfill_provenance` uses, because one parquet can carry MIXED
    provenance. Rows the index does not have are skipped — an unindexed file is
    `stale_new`'s business, not a provenance disagreement.
    """
    import pyarrow.parquet as pq

    from helpers.misc.duckdb_lock import open_read_only

    if not Path(index_db).exists():
        return []
    try:
        con = open_read_only(index_db)  # wire-routes when the store is served
    except Exception:
        return []
    try:
        idx = {
            (r[0], r[1]): r[2]
            for r in con.execute(
                "SELECT file_path, row_no, provenance FROM convo_search"
            ).fetchall()
        }
    except Exception:
        return []
    finally:
        con.close()

    out: list[str] = []
    for f in _all_corpus_files(Path(corpus_root)):
        try:
            srcs = pq.read_table(f, columns=["source"]).column("source").to_pylist()
        except Exception:  # noqa: S112  # unreadable file: --check reports it, a source census must not abort
            continue
        # `--corpus-root` may point outside REPO. Falling back to the absolute
        # path keeps the check working there; letting `relative_to` raise would
        # skip the file silently, which is how an unchecked drift gets in.
        try:
            rel = str(f.resolve().relative_to(REPO))
        except ValueError:
            rel = str(f.resolve())
        buckets: dict[str, int] = {}
        for row_no, s in enumerate(srcs):
            got = idx.get((rel, row_no))
            if got is not None and got != s:
                buckets[f"{got or '(empty)'} -> {s or '(empty)'}"] = (
                    buckets.get(f"{got or '(empty)'} -> {s or '(empty)'}", 0) + 1
                )
        for change, n in sorted(buckets.items(), key=lambda kv: (-kv[1], kv[0])):
            out.append(f"{rel}: {n} row(s) {change}")
    return out


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
    drift.extend(_bili_drift(store))
    # dsh-index lag lines are NOT corpus drift: they describe the EXTERNAL
    # dsh session-query index (~/.dsh/**/session-search.db), which no
    # harvest APPLY can advance. Folding them into the drift list made
    # `make convo-fresh APPLY=1` permanently STALE (measured 2026-10-17:
    # 13 missing + 1 lagging sessions kept the flag up across applies).
    # They surface as an advisory in main()'s check output instead; the
    # lines close when the dsh indexer next catches up.
    for f, src in _prime_files():
        key = f"{src}:{hashlib.blake2b(str(f).encode(), digest_size=6).hexdigest()}"
        st = f.stat()
        wm_raw = store.watermark(key)
        cur = jsonio.loads(wm_raw) if wm_raw else None
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
    force_lane: str | None = None,
) -> dict:
    """The S1 core: union all sources into the per-session parquet corpus.

    ``timer`` (a maint_timing.RunTimer, optional so tests keep calling
    with kwargs) receives one phase per lane plus the flush, with
    wall-clock start/end — the queryable record behind maint_query.

    ``force_lane`` re-reads one lane even where its watermark says the
    source is already harvested (a parser change — e.g. prime's dropped
    thinking/toolCall blocks — recovers nothing without it).
    """
    t0 = time.perf_counter()
    store = Store(db, read_only=check)
    try:
        if check:
            drift = _drift(store, corpus_root)
            fresh = not drift
            deleted, unresolved = _deleted_sessions(corpus_root)
            prov = _provenance_drift(corpus_root)
            return {
                "check": True,
                "index_stale": not fresh,
                "stale_new": drift,
                "stale_changed": [],
                # Every corpus session the harness no longer has. This was the
                # vestigial field that hid the leak: declared here since the
                # v2 cutover, assigned nowhere, and 29 sessions / 16,949 rows
                # sat behind it until someone counted by hand on 2026-10-17.
                "stale_deleted": deleted,
                # The subset still labelled `opencode:live` — the ones
                # `--resolve-orphans` has not yet relabelled. Those DO gate:
                # the corpus is claiming provenance it does not have.
                "stale_deleted_unresolved": unresolved,
                # Index-vs-corpus `provenance` disagreement. A different axis:
                # the corpus may be perfectly correct and the index still be
                # serving labels the parquet no longer carries.
                "stale_provenance": prov,
                "coverage": _coverage(store),
                "elapsed": 0.0,
            }
        writer = SessionWriter(corpus_root)
        lanes, lane_seconds = [], {}
        for name, fn in (
            ("opencode", _harvest_opencode),
            ("prime-rlm", _harvest_prime),
            ("zcode", _harvest_zcode),
            ("dsh", _harvest_dsh),
            ("bili", _harvest_bili),
        ):
            if timer is not None:
                with timer.phase(f"lane:{name}"):
                    t_lane = time.perf_counter()
                    lanes.append(fn(store, writer, force=force_lane == name))
                    lane_seconds[name] = round(time.perf_counter() - t_lane, 2)
            else:
                t_lane = time.perf_counter()
                lanes.append(fn(store, writer, force=force_lane == name))
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


def _deleted_sessions(corpus_root: Path) -> tuple[list[str], list[str]]:
    """Corpus sessions the harness no longer has, and how many are unresolved.

    Returns `(deleted, unresolved)`. `unresolved` is the subset still labelled
    `opencode:live` — the corpus claiming provenance it does not have — and is
    what the gate counts. Already-relabelled `orphan:` sessions are reported
    but not gated, so `--resolve-orphans` closes the gate once and the corpus
    stays green afterwards.

    Snapshot-backed sessions are excluded: the snapshot lanes retain them on
    purpose, so "the harness purged it" is not drift.
    """
    deleted: list[str] = []
    unresolved: list[str] = []
    root = Path(corpus_root)
    if not root.is_dir():
        return (deleted, unresolved)
    for harness_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        conv = harness_dir / "conversations"
        if not conv.is_dir():
            continue
        harness = harness_dir.name
        live = _live_session_ids(harness)
        if live is None:
            continue  # no manifest for this harness — see _live_session_ids
        for f in sorted(conv.glob("*.parquet")):
            try:
                t = pq.read_table(f, columns=["session_id", "source"])
            except Exception:  # noqa: S112  # unreadable file: --check reports it, not this scan
                continue
            sids = {v for v in t.column("session_id").to_pylist() if v}
            srcs = set(t.column("source").to_pylist())
            if not sids:
                continue
            (sid,) = (next(iter(sids)),)
            if sid in live:
                continue
            deleted.append(sid)
            # Retention and provenance are separate questions. A snapshot keeps
            # the file; it does NOT make `opencode:live` true. So a session that
            # still carries a live label is unresolved whether or not a snapshot
            # backs it — and treating "snapshot-backed" as "clean" hid 21,127
            # rows across 14 sessions on 2026-10-17.
            #
            # Unresolved also means ANY row still claims the harness owns this
            # session, not "the orphan label is absent". A file can hold both:
            # `--resolve-orphans` relabels the rows present when it runs, and
            # rows harvested afterwards re-assert `<harness>:live`.
            # Checking for the label's PRESENCE let a 3-of-42-row relabel
            # pass as resolved (3 sessions, 2,926 rows, 2026-10-17).
            if any(_claims_live_harness(s, harness) for s in srcs):
                unresolved.append(sid)
    return (sorted(deleted), sorted(unresolved))


def _session_sources(corpus_root: Path) -> dict[str, set[str]]:
    """`{session_id: {source, ...}}` for every parquet in the corpus."""
    out: dict[str, set[str]] = {}
    for harness_dir in sorted(p for p in Path(corpus_root).iterdir() if p.is_dir()):
        conv = harness_dir / "conversations"
        if not conv.is_dir():
            continue
        for f in sorted(conv.glob("*.parquet")):
            try:
                t = pq.read_table(f, columns=["session_id", "source"])
            except Exception:  # noqa: S112  # unreadable file: reported by --check, not here
                continue
            cols = t.to_pydict()
            for sid, src in zip(cols["session_id"], cols["source"], strict=True):
                if sid:
                    out.setdefault(sid, set()).add(src or "")
    return out


def _snapshot_backed(srcs: set[str]) -> bool:
    """A snapshot lane deliberately retains a session the harness purged."""
    return any(s.startswith("opencode-snap") or "snap:" in s for s in srcs)


def _all_corpus_files(corpus_root: Path) -> list[Path]:
    out: list[Path] = []
    if not Path(corpus_root).is_dir():
        return out
    for harness_dir in sorted(p for p in Path(corpus_root).iterdir() if p.is_dir()):
        conv = harness_dir / "conversations"
        if conv.is_dir():
            out.extend(sorted(conv.glob("*.parquet")))
    return out


def _refresh_index_provenance(files: list[Path], *, dry_run: bool = False) -> None:
    """Re-read `source` from these parquets into `convo_search.provenance`.

    `_backfill_provenance` joins on `(file_path, row_no)` — positional, no
    re-embedding — because a single parquet can carry MIXED provenance (19 of
    the 271 corpus files do). That makes it exactly right for repairing an
    in-place relabel, which produces no file-level diff for the rebuild to
    notice.
    """
    if dry_run:
        return
    if not files:
        print("  provenance: nothing to refresh", file=sys.stderr)
        return
    from helpers.maintenance.rebuild_convo_search import _backfill_provenance  # noqa: PLC0415

    idx = INDEX_DB
    if not idx.exists():
        print(f"  provenance: no index at {idx}; skipped", file=sys.stderr)
        return
    nf, nr = _backfill_provenance(idx, files)
    print(
        f"  provenance: refreshed {nr} index row(s) across {nf} file(s)",
        file=sys.stderr,
    )


def _refresh_index_models(files: list[Path]) -> None:
    """Re-read `model` from these parquets into `convo_search.model`.

    Sibling of `_refresh_index_provenance`: the in-place model migrations
    rewrite corpus parquets with text and part_ids untouched, which no
    incremental rebuild notices (`_backfill_models` joins positionally on
    `(file_path, row_no)` — no re-embedding, embed cache stays valid).
    """
    if not files:
        print("  models: nothing to refresh", file=sys.stderr)
        return
    idx = INDEX_DB
    if not idx.exists():
        print(f"  models: no index at {idx}; skipped", file=sys.stderr)
        return
    from helpers.maintenance.rebuild_convo_search import _backfill_models  # noqa: PLC0415

    nf, nr = _backfill_models(idx, files)
    print(
        f"  models: refreshed {nr} index row(s) across {nf} file(s)",
        file=sys.stderr,
    )


def _is_snapshot_src(src: str, harness: str) -> bool:
    """True for a provenance label that records a SNAPSHOT, not a live store.

    Two shapes exist and both must survive a relabel:
      `opencode-snap:2026-09-19_17-00-05`  (harness-prefixed)
      `zcode:snap`, `prime-snap:...`        (per-lane naming)
    Anything else in a session the harness dropped is a live claim, and a live
    claim about a session the harness dropped is the lie we are fixing.
    """
    return src.startswith(f"{harness}-snap") or src.endswith(":snap") or src == "snap"


def _claims_live_harness(src: str, harness: str) -> bool:
    """True when this provenance label asserts the harness still holds the row.

    The inverse of "is this fine", so every accepted shape must be named:
    snapshot labels (retained and truthful) and the orphan label (already
    relabelled). Everything else — `opencode:live`, `bili-index/opencode`,
    `dsh:live` — is a claim about a session the harness has dropped.
    """
    if src == f"orphan:{harness}":
        return False
    return not _is_snapshot_src(src, harness)


def _resolve_orphans(  # noqa: C901
    corpus_root: Path, *, dry_run: bool, collect: list[Path] | None = None
) -> tuple[int, int]:
    """Relabel provenance for sessions the harness no longer has.

    The writer is add/merge-only (`SessionWriter.flush` iterates the sessions a
    source *reported*), so deleting a session in the harness leaves its parquet
    behind claiming `opencode:live` — a provenance lie, and one that made 29
    pure orphans invisible until they were counted by hand on 2026-10-17.

    Retention and provenance are SEPARATE questions, and conflating them is how
    21,127 rows stayed wrong. A snapshot-backed session is retained (that is
    what the snapshot lanes are for) — but its rows may ALSO carry
    `opencode:live`, and that label is still a claim the harness does not
    honour. So this rewrites per ROW, not per file: every non-snapshot source in
    a session the harness dropped becomes `orphan:<harness>`, while the
    snapshot rows keep their provenance and the file keeps its retention.

    The rows stay. Deleting them is `--keep`'s job, and it is a separate
    decision from "stop claiming the harness owns these".
    """
    if not Path(corpus_root).is_dir():
        print(f"--resolve-orphans: no corpus root {corpus_root}", file=sys.stderr)
        return (0, 0)
    changed_files = changed_rows = scanned = 0
    for harness_dir in sorted(p for p in Path(corpus_root).iterdir() if p.is_dir()):
        conv = harness_dir / "conversations"
        if not conv.is_dir():
            continue
        harness = harness_dir.name
        for f in sorted(conv.glob("*.parquet")):
            try:
                table = pq.read_table(f)
            except Exception:  # noqa: S112  # unreadable file: --check reports it, not this pass
                continue
            scanned += 1
            sids = {v for v in table.column("session_id").to_pylist() if v}
            if not sids:
                continue
            # `live` here means "the harness's own storage". Only opencode has
            # a queryable live-session list (its `session` table); dsh/prime/
            # zcode are file-enumerated with no equivalent manifest, so they
            # return None and are skipped rather than guessed at — relabelling
            # 500 live sessions as orphans is not a recoverable mistake.
            known_live = _live_session_ids(harness)
            if known_live is None:
                continue
            (sid,) = (next(iter(sids)),)
            if sid in known_live:
                continue
            # Rewrite only the LIVE-ish labels. A snapshot row is the truth
            # about where this content came from and is kept verbatim.
            n = sum(
                1
                for s in table.column("source").to_pylist()
                if not s.startswith(f"{harness}-snap")
                and not s.endswith(":snap")
                and s != f"orphan:{harness}"
            )
            if not n:
                continue
            changed_files += 1
            changed_rows += n
            if collect is not None:
                collect.append(f)
            if dry_run:
                continue
            src_col = table.column("source").to_pylist()
            table = table.set_column(
                table.schema.get_field_index("source"),
                "source",
                pa.array(
                    [
                        s if not _claims_live_harness(s, harness) else f"orphan:{harness}"
                        for s in src_col
                    ],
                    type=pa.string(),
                ),
            )
            pq.write_table(table, f, compression="zstd")
    verb = "would relabel" if dry_run else "relabelled"
    print(f"{verb} {changed_rows} row(s) across {changed_files} file(s) as orphan", file=sys.stderr)
    if scanned:
        print(f"  scanned {scanned} parquet file(s) under {corpus_root}", file=sys.stderr)
    print("  rows are RETAINED; --keep <range> is what purges them", file=sys.stderr)
    return (changed_files, changed_rows)


_LIVE_SESSION_IDS: dict[str, set[str] | None] = {}
_LIVE_SESSION_PROBE: dict[str, float] = {}
_LIVE_PROBE_TTL_S = 30.0


def _live_session_ids(harness: str) -> set[str] | None:
    """Session ids the harness currently holds, or None when it cannot be asked.

    Only `opencode` answers: its state is a SQLite `session` table. The other
    three lanes are file-enumerated and have no comparable manifest, so they
    return None and every session is left alone.

    A positive answer is cached for the process (one query, not one per
    corpus file). A FAILED probe is cached only briefly — a transient lock
    (the live store is busy) must not silence the orphan lane for the rest
    of the run: the lane would report "relabelled 0 row(s)" and every
    dropped session would stay labelled `opencode:live` until the next
    process. Measured 2026-10-22: a poisoned negative cache made the
    --resolve-orphans gate fail for every later caller in that process.
    """
    if harness in _LIVE_SESSION_IDS:
        return _LIVE_SESSION_IDS[harness]
    if harness == "opencode":
        ids = _oc_live_ids()
        if ids is not None:
            _LIVE_SESSION_IDS[harness] = ids
            return ids
        now = time.monotonic()
        last = _LIVE_SESSION_PROBE.get(harness)
        if last is not None and now - last < _LIVE_PROBE_TTL_S:
            return None  # recently failed — do not re-probe per corpus file
        _LIVE_SESSION_PROBE[harness] = now
        return None
    _LIVE_SESSION_IDS[harness] = None
    return None


def _opencode_live_session_ids() -> set[str]:
    """Session ids currently in `opencode.db`. Empty when it is not there."""
    return _live_session_ids("opencode") or set()


def _oc_live_ids() -> set[str] | None:
    import sqlite3

    for cand in sorted(Path.home().glob(".local/share/opencode/opencode.db")) + sorted(
        (SNAP_ROOT).glob("*/localhost/home/arun/.local/share/opencode/opencode.db")
    ):
        try:
            con = sqlite3.connect(f"file:{cand}?mode=ro", uri=True)
            try:
                return {r[0] for r in con.execute("SELECT id FROM session")}
            finally:
                con.close()
        except sqlite3.Error:
            continue
    return None


def _purge_older_than(corpus_root: Path, keep: str, *, dry_run: bool) -> tuple[int, int]:
    """Delete corpus parquets whose newest row predates `keep` ago.

    Age is the session's own newest `ts`, not a filesystem mtime: `flush()`
    rewrites a file on every harvest it touches, so mtime tracks the last
    APPLY, not the conversation. Purging on mtime would keep dead sessions
    alive forever and delete live ones the moment the store was restored.

    Returns (files, rows).
    """
    cutoff = cutoff_from(keep)
    files = rows = 0
    for harness_dir in sorted(p for p in Path(corpus_root).iterdir() if p.is_dir()):
        conv = harness_dir / "conversations"
        if not conv.is_dir():
            continue
        for f in sorted(conv.glob("*.parquet")):
            try:
                newest = pq.read_table(f, columns=["ts"]).column("ts").to_pylist()
            except Exception:  # noqa: S112  # unreadable file: --check reports it, not this pass
                continue
            stamps = [t for t in newest if t is not None]
            if not stamps or max(stamps) >= cutoff:
                continue
            n = len(newest)
            files += 1
            rows += n
            if not dry_run:
                f.unlink()
    verb = "would purge" if dry_run else "purged"
    print(
        f"{verb} {files} parquet file(s) / {rows} row(s) older than "
        f"{cutoff:%Y-%m-%d} (keep={keep})",
        file=sys.stderr,
    )
    if files:
        print("  refresh the index: make convo-fresh APPLY=1", file=sys.stderr)
    return (files, rows)


def main(argv: list[str] | None = None) -> int:  # noqa: C901
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
        "--keep",
        metavar="RANGE",
        help=(
            "purge corpus parquet files whose newest row predates RANGE ago "
            "(30d / 6mo / 1y, or a bare number of days). Combined with "
            "--dry-run this reports without deleting."
        ),
    )
    p.add_argument(
        "--resolve-orphans",
        action="store_true",
        help=(
            "relabel sessions the harness no longer has as `orphan:<harness>` "
            "so provenance stops claiming opencode:live. Rows are retained; "
            "only --keep purges."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="with --keep / --resolve-orphans: report what would change, write nothing",
    )
    p.add_argument(
        "--refresh-index-provenance",
        action="store_true",
        help="re-read `source` from every corpus parquet into "
        "convo_search.provenance; repairs an index left stale by an in-place "
        "provenance rewrite (--resolve-orphans does this for itself)",
    )
    p.add_argument(
        "--refresh-index-models",
        action="store_true",
        help="re-read `model` from every corpus parquet into "
        "convo_search.model; repairs an index left stale by an in-place "
        "model migration (positional join, no re-embed; the migrate "
        "flags do this for themselves)",
    )
    p.add_argument(
        "--migrate-opencode-models",
        action="store_true",
        help="one-shot (opencode v2-lane S1): rewrite existing opencode "
        "parquet model dict-strings to plain model-id strings; no harvest",
    )
    p.add_argument(
        "--force-lane",
        metavar="LANE",
        choices=["opencode", "prime-rlm", "zcode", "dsh", "bili"],
        help="re-read one lane even where its per-source watermark says the "
        "source is done. A parser change recovers nothing from an "
        "already-harvested source without this (part_ids are stable, so a "
        "forced re-read merges rather than duplicating). Runs the full "
        "harvest; only the named lane ignores its watermark.",
    )
    args = p.parse_args(argv)
    if (
        args.keep
        or args.resolve_orphans
        or args.refresh_index_provenance
        or args.refresh_index_models
    ):
        try:
            if args.keep:
                cutoff_from(args.keep)  # fail fast, before anything is touched
        except RangeError as exc:
            print(f"--keep: {exc}", file=sys.stderr)
            return 2
        if args.resolve_orphans:
            touched: list[Path] = []
            _resolve_orphans(Path(args.corpus_root), dry_run=args.dry_run, collect=touched)
            # The corpus is the source of truth, but `convo_search.provenance`
            # is a COPY taken at read time. A relabel rewrites `source` in
            # place — no new file, no moved row — so the rebuild's file-diff
            # never notices and the index keeps serving the stale label.
            # Measured 2026-10-17: after relabelling 842 rows, 865 rows of
            # ses_ec7af063cffew2a8xvEcV142n6 still read `opencode:live` in the
            # index. Refresh it here, where the change is known, rather than
            # hoping a later APPLY notices.
            _refresh_index_provenance(touched, dry_run=args.dry_run)
        if args.refresh_index_provenance:
            # Standalone repair: the corpus is already correct but the index is
            # not (an older relabel, or any other in-place provenance rewrite).
            _refresh_index_provenance(_all_corpus_files(Path(args.corpus_root)))
        if args.refresh_index_models:
            # Standalone repair: same in-place blind spot, model column — the
            # migrations rewrite corpus parquets (text untouched) and no
            # incremental rebuild notices a changed facet.
            _refresh_index_models(_all_corpus_files(Path(args.corpus_root)))
        if args.keep:
            _purge_older_than(Path(args.corpus_root), args.keep, dry_run=args.dry_run)
        if not args.dry_run:
            print("  done; run make convo-fresh APPLY=1 to reindex", file=sys.stderr)
        return 0
    if args.migrate_zcode_models:
        files, rows = _migrate_zcode_models(Path(args.corpus_root))
        print(
            f"migrated {rows} model values across {files} zcode parquet files",
            file=sys.stderr,
        )
        # Fix-forward: the rewrite is in-place (text/part_ids untouched), so
        # no incremental rebuild would notice — refresh the index now.
        if files:
            _refresh_index_models(
                sorted((Path(args.corpus_root) / "zcode" / "conversations").glob("*.parquet"))
            )
        return 0
    if args.migrate_opencode_models:
        files, rows = _migrate_opencode_models(Path(args.corpus_root))
        print(
            f"migrated {rows} model values across {files} opencode parquet files",
            file=sys.stderr,
        )
        if files:
            _refresh_index_models(
                sorted((Path(args.corpus_root) / "opencode" / "conversations").glob("*.parquet"))
            )
        return 0
    from helpers.maintenance.maint_timing import RunTimer

    timer = RunTimer("harvest_conversations", mode="check" if args.check else "apply")
    out = harvest(
        check=args.check,
        corpus_root=Path(args.corpus_root),
        db=Path(args.db),
        timer=timer,
        force_lane=args.force_lane,
    )
    if out["check"]:
        for line in out.get("coverage", []):
            print(f"  {line}", file=sys.stderr)
        lag = _dsh_index_lag()
        if lag:
            print(
                "  dsh tool index (external session-search.db — advisory, "
                "closes when the dsh indexer catches up; not corpus drift):",
                file=sys.stderr,
            )
            for line in lag[:15]:
                print(f"    {line}", file=sys.stderr)
        deleted = out.get("stale_deleted") or []
        unresolved = out.get("stale_deleted_unresolved") or []
        if deleted:
            print(
                f"deleted sessions: {len(deleted)} corpus session(s) the harness no "
                f"longer has ({len(deleted) - len(unresolved)} already relabelled "
                f"orphan:*)",
                file=sys.stderr,
            )
            for sid in deleted[:10]:
                mark = "UNRESOLVED" if sid in set(unresolved) else "orphan:*"
                print(f"  {mark:<11} {sid}", file=sys.stderr)
        if out["index_stale"] or unresolved or out.get("stale_provenance"):
            prov = out.get("stale_provenance") or []
            if prov:
                print(
                    f"provenance drift: {len(prov)} file(s) where convo_search "
                    f"disagrees with the corpus",
                    file=sys.stderr,
                )
                for d in prov[:10]:
                    print(f"  {d}", file=sys.stderr)
                print(
                    "  fix: harvest_conversations.py --refresh-index-provenance",
                    file=sys.stderr,
                )
            if out["index_stale"]:
                print("corpus state: STALE — sources drifted:", file=sys.stderr)
                for d in out["stale_new"][:15]:
                    print(f"  {d}", file=sys.stderr)
                print("refresh: make convo-fresh APPLY=1", file=sys.stderr)
            if unresolved:
                print(
                    f"  {len(unresolved)} deleted session(s) still labelled "
                    f"opencode:live — fix: harvest_conversations.py --resolve-orphans",
                    file=sys.stderr,
                )
            return timer.finish(
                1,
                f"STALE sources={len(out['stale_new'])} orphans={len(unresolved)} "
                f"provenance={len(prov)}",
            )
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
