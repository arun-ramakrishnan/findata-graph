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
- zcode: live + snapshot ``rollout/model-io-*.jsonl`` (fullest request
  per session = final context; per-response ``text``/``reasoningText``
  keyed by responseId).

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
        out.append(_row(pid, mid, sid, _ms(tu or tc), m[0], m[1], m[2], ptype, text, meta, src))
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
    for path, src, immutable in _opencode_sources():
        key = f"{src}:{hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()}"
        hwm_raw = store.watermark(key)
        if immutable and hwm_raw is not None:
            stats.append(f"{src}: quiet")
            continue
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
    for bdb in sorted(
        SNAP_ROOT.glob(
            "*/localhost/home/arun/Research/findata-graph/memory/data/agent_traces.duckdb"
        )
    ):
        key = f"opencode-traces:{_snap_ts(bdb)}"
        if store.watermark(key) is not None:
            stats.append("opencode-traces: quiet")
            continue
        try:
            rows = _harvest_traces_duckdb(bdb)
        except Exception as exc:  # noqa: BLE001 — duckdb raises its own hierarchy
            stats.append(f"opencode-traces:{_snap_ts(bdb)}: SKIP unreadable ({exc})")
            continue
        writer.add("opencode", rows)
        store.register("opencode", rows)
        store.set_watermark(key, str(len(rows)))
        parts_total += len(rows)
        stats.append(f"opencode-traces: full {len(rows)} parts")
    return "\n".join(stats) + f"\nopencode total new: {parts_total}"


def _harvest_traces_duckdb(db_path: Path) -> list[dict]:
    """Pre-redaction bus events: message.part.updated.1 parts + roles."""
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        roles = {}
        for (ctx,) in con.execute(
            "SELECT context FROM fact_event WHERE event_name='message.updated.1'"
        ).fetchall():
            info = (json.loads(ctx) or {}).get("info") or {}
            if info.get("id"):
                roles[info["id"]] = (
                    info.get("role", ""),
                    info.get("agent", ""),
                    info.get("model", ""),
                )
        out = []
        for (ctx,) in con.execute(
            "SELECT context FROM fact_event WHERE event_name='message.part.updated.1'"
        ).fetchall():
            d = json.loads(ctx) or {}
            p = d.get("part") or {}
            data = p.get("data") or {}
            ptype = data.get("type") or "unknown"
            meta: dict = {}
            mid, sid = p.get("messageID") or "", p.get("sessionID") or d.get("sessionID") or ""
            if ptype in ("text", "reasoning"):
                text = data.get("text") or ""
            elif ptype == "tool":
                st = data.get("state") or {}
                text = st.get("output") or ""
                meta = {"status": st.get("status")}
            else:
                text = ""
            if not text and ptype not in ("patch", "compaction", "step-finish"):
                continue
            ts = _ms((p.get("time") or {}).get("updated"))
            out.append(
                _row(
                    p.get("id") or "",
                    mid,
                    sid,
                    ts,
                    *(roles.get(mid) or ("", "", "")),
                    ptype,
                    text,
                    meta,
                    "opencode:traces",
                )
            )
        return out
    finally:
        con.close()


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


def _zc_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _zcode_best_and_resps(files: list[Path]) -> tuple[dict | None, dict]:
    """Find the fullest request and collect responses by id."""
    best, best_n, resps = None, -1, {}
    for f in files:
        for line in open(f, errors="replace"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            n = (d.get("request") or {}).get("messageCount") or 0
            if n > best_n:
                best, best_n = d, n
            r = d.get("response") or {}
            rid = r.get("responseId")
            if rid:
                resps[rid] = (
                    d.get("startedAt"),
                    r.get("text") or "",
                    r.get("reasoningText") or "",
                    r.get("model") or d.get("model") or "",
                )
    return best, resps


def _zcode_req_rows(best: dict, sid: str, src_tag: str) -> list[dict]:
    """Build request-message rows from the fullest request."""
    rows = []
    rid = best.get("requestId") or "req"
    ts = _iso_ms(best.get("startedAt") or best.get("completedAt"))
    for i, m in enumerate((best.get("request") or {}).get("messages") or []):
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
                best.get("model") or "",
                "req-msg",
                text,
                {"messageOffset": (best.get("request") or {}).get("messageOffset")},
                f"zcode:{src_tag}",
            )
        )
    return rows


def _zcode_resp_rows(resps: dict, sid: str, src_tag: str) -> list[dict]:
    """Build response and reasoning rows from collected responses."""
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
    return rows


def _harvest_zcode_file(files: list[Path], sid: str, src_tag: str) -> list[dict]:
    """One zcode session: fullest request = final context; responses by id."""
    best, resps = _zcode_best_and_resps(files)
    rows = []
    if best:
        rows.extend(_zcode_req_rows(best, sid, src_tag))
    rows.extend(_zcode_resp_rows(resps, sid, src_tag))
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


def _drift(store: Store) -> list[str]:
    """--check verdict: sources whose current fingerprint differs from the watermark."""
    drift = []
    for path, src, immutable in _opencode_sources():
        key = f"{src}:{hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()}"
        if immutable:
            if store.watermark(key) is None:
                drift.append(f"new {src}")
            continue
        con = connect(path, read_only=True)
        try:
            row = con.execute("SELECT COALESCE(MAX(time_updated), 0) FROM part").fetchone()
            hwm = row[0] if row else 0
        finally:
            con.close()
        wm = store.watermark(key)
        if wm is None or int(wm) < int(hwm):
            drift.append(f"changed {src} ({hwm})")
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


def harvest(check: bool = False, corpus_root: Path = CORPUS_ROOT, db: Path = INDEX_DB) -> dict:
    """The S1 core: union all sources into the per-session parquet corpus."""
    t0 = time.perf_counter()
    store = Store(db, read_only=check)
    try:
        if check:
            drift = _drift(store)
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
        ):
            t_lane = time.perf_counter()
            lanes.append(fn(store, writer))
            lane_seconds[name] = round(time.perf_counter() - t_lane, 2)
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
    args = p.parse_args(argv)
    out = harvest(check=args.check, corpus_root=Path(args.corpus_root), db=Path(args.db))
    if out["check"]:
        if out["index_stale"]:
            print("corpus state: STALE — sources drifted:", file=sys.stderr)
            for d in out["stale_new"][:15]:
                print(f"  {d}", file=sys.stderr)
            print("refresh: make convo-fresh APPLY=1", file=sys.stderr)
            return 1
        print("corpus state: FRESH", file=sys.stderr)
        return 0
    print(out["lanes"], file=sys.stderr)
    per_lane = " ".join(f"{k}={v}s" for k, v in out["lane_seconds"].items())
    print(
        f"✓ corpus: {out['sessions_written']} session files written "
        f"in {out['elapsed']:.1f}s ({Path(args.db).stat().st_size // 1024} KiB meta db)",
        file=sys.stderr,
    )
    print(f"  lanes: {per_lane}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
