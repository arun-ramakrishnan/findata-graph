#!/usr/bin/env python3
"""Unified LLM usage analytics - one local DuckDB store.

Loads each authoritative source into memory/data/model_usage.duckdb, then
queries read locally. Stops the same past data being mined repeatedly: run
`load` to refresh a source, `report` to read any slice.

REFRESH MODEL: `load <source>` REPLACES that source's rows (delete + reinsert),
it does not append. Old days are immutable, but the current day is not - the
Zhipu quota reset collapses "today" (Sep 21: 48.6M -> 142K) and a live
prime-rlm session JSONL is still being written. Incremental by default: only
the last --days (2) are re-fetched and replaced, leaving immutable history
untouched; --full reloads the whole source. Recovery sources always load in
full (point-in-time recovery, not live feeds). Replace keeps the newest day
correct and the store idempotent (a reload yields identical row counts).

Sources (full contracts in doc/local/notes/model_usage.md) — three rows in
the store, one per LLM surface. Each family loader reads EVERY population of
its surface, because the harnesses rotate their own history away (the
-backup/-journal inputs are the same harness's data lost over time and
recovered from snapshots, not a different origin):
  prime-rlm   ~/.prime/agent/sessions/*.jsonl (live)
            + memory/data/prime_sessions_backup/*.jsonl (purged sessions,
              staged from timeshift snapshots; live stems skipped)
            + daemon command-journal sessions with no JSONL anywhere
              (session-grain floor, no cache split)
              Cost computed from the ~/.prime/agent/models.json cost cards
              (Atria is unique to this harness - no other store has it)
  zai         the Zhipu surface: plan API usage-detail (authoritative
              tokens/cost across EVERY harness on the quota). The surface
              also has ZCode-local request stats (~/.zcode/cli/db/db.sqlite,
              mined via the vendored `local_stats`, ex-`zai_usage_query.py`)
              - ops metrics, not usage rows.
  opencode    opencode.db live + memory/data/telemetry_backup/opencode-*.db
              snapshots, deduped on message.id (Zen models have no usage API)

There is deliberately NO OpenRouter Analytics source: its model slugs
(date-suffixed, :free-less) never matched harness model ids, so the coverage
rule could not fire and ~93% of its rows duplicated harness rows while the
rest were all-zero (tokens/cost only) - the harness DBs carry the full
per-message detail. Ad-hoc Analytics queries stay available via
opencode_usage_query.py.

Token semantics differ by source and are normalised on load:
  GLM (zai, prime-rlm Zhipu rows): input INCLUDES cache reads, so
      fresh = input - cached
  OpenCode and prime-rlm Atria/openrouter rows: input EXCLUDES cache reads,
      so fresh = input
  In both cases tokens = fresh + cached + output (+ reasoning when reported),
  and cache hit rate = cached / (fresh + cached).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, UTC
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))  # repo root, for helpers.core.db
from helpers.core.db import connect  # noqa: E402  (path insert must precede)
from helpers.misc.duckdb_lock import connect_with_lock_retry  # noqa: E402


def _ro_connect(db, *, read_only: bool = True):
    """Reader open with the S2 transient-lock ladder
    (duckdb_transient_lock_retry): the bench ETL holds its store RW for
    the whole load window; the ladder absorbs a race we arrive late to.
    helpers.core.db.connect stays the opener (its prep is shared with
    every other caller — only the retry wrapper is added here)."""
    return connect_with_lock_retry(lambda: connect(db, read_only=read_only))


DB_PATH = REPO / "memory" / "data" / "model_usage.duckdb"
PRIME_DIR = Path.home() / ".prime" / "agent"
# Purged prime-rlm session JSONLs recovered from external backups (timeshift
# snapshots), staged locally so a load never needs the backup device mounted.
# Refresh by copying newer session files in; see load_prime_backup.
BACKUP_DIR = REPO / "memory" / "data" / "prime_sessions_backup"
BACKUP_TELEMETRY = REPO / "memory" / "data" / "telemetry_backup"
OC_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
ZCODE_DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_usage (
    source       TEXT NOT NULL,
    day          DATE NOT NULL,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    reqs         BIGINT NOT NULL,
    fresh_in     BIGINT NOT NULL,
    cached       BIGINT NOT NULL,
    cache_write  BIGINT NOT NULL,
    output       BIGINT NOT NULL,
    reasoning    BIGINT NOT NULL,
    tokens       BIGINT NOT NULL,
    cost_usd     DOUBLE NOT NULL,
    cost_basis   TEXT NOT NULL,
    loaded_at    TIMESTAMP NOT NULL,
    PRIMARY KEY (source, day, provider, model)
);
CREATE TABLE IF NOT EXISTS load_log (
    source      TEXT NOT NULL,
    started_at  TIMESTAMP NOT NULL,
    rows        BIGINT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT
);
"""

# Provider aggregation registry: which API source in the store is AUTHORITATIVE
# for a provider, account-/quota-wide. A provider reached through several
# harnesses (Zhipu: ZCode IDE, OpenCode, Prime RLM) is totalled once under its
# API source; each harness's rows for that provider survive only on days the
# API doesn't have (older than its fetch window). _drop_api_covered reads this
# at load time — to aggregate a NEW provider API, write its loader, add a
# LOADERS entry, and register it here; both family loaders honor it
# automatically. Unregistered providers (Atria, OpenRouter, OpenCode/Zen)
# have no covering API and always survive.
# OpenRouter was registered here once ("openrouter") — removed 2026-09-21:
# the Analytics API's slugs (date-suffixed, :free-less) never matched harness
# model ids, so coverage could never fire and its rows ghost-duplicated.
AUTHORITATIVE = {
    "Zhipu": "zai",
}


# provider for a prime-rlm model id, from the settings.json namespace prefix
def prime_provider(model: str, prov_of_model: dict[str, str]) -> str:
    if model in prov_of_model:
        return prov_of_model[model]
    if "/" in model:
        head = model.split("/", 1)[0]
        return {
            "openrouter": "OpenRouter",
            "opencode": "OpenCode",
            "Zhipu": "Zhipu",
            "Atria": "Atria",
            # openrouter-style slugs: vendor/model[:free]
            "thinkingmachines": "OpenRouter",
            "inclusionai": "OpenRouter",
            "nex-agi": "OpenRouter",
            "deepseek": "OpenRouter",
            "meta": "OpenRouter",
            "google": "OpenRouter",
            "minimax": "OpenRouter",
            "poolside": "OpenRouter",
            "qwen": "OpenRouter",
            "liquid": "OpenRouter",
            "sakana": "OpenRouter",
            "nvidia": "OpenRouter",
            "cohere": "OpenRouter",
            "dots-studio": "OpenRouter",
            "z-ai": "OpenRouter",
            "openai": "OpenRouter",
        }.get(head, head)
    # bare zen model ids (big-pickle, muse-spark-*, nemotron-*, ling-*, mimo-*)
    return "OpenCode"


def _prime_cost_cards() -> tuple[dict[str, str], dict[str, dict]]:
    """(model -> provider, model -> cost card) from ~/.prime/agent/models.json."""
    models_json = PRIME_DIR / "models.json"
    if not models_json.is_file():
        return {}, {}
    providers = json.loads(models_json.read_text()).get("providers", {})
    prov_of_model = {m["id"]: p for p, c in providers.items() for m in c.get("models", [])}
    cards = {
        m["id"]: {k: v for k, v in m.get("cost", {}).items()}
        for c in providers.values()
        for m in c.get("models", [])
    }
    return prov_of_model, cards


def _prime_session_rows(files, since: date | None, prov_of_model: dict[str, str]):
    """Parse assistant usage rows out of prime-rlm session JSONLs.

    Used for both the live sessions and the staged backup copies (the same
    format, disjoint sessions) so the two cannot drift. Returns (day, prov,
    model, fresh, cached, cache_write, output, reasoning, cost) per message;
    no-usage messages are skipped (failed/rate-limited call, not a
    zero-token call). Zhipu GLM rows count cache reads INSIDE input, so
    fresh is deflated there to keep the cache-hit rate honest."""
    rows = []
    for f in files:
        for line in Path(f).read_text(errors="replace").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "message":
                continue
            msg = d.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            u = msg.get("usage")
            if not isinstance(u, dict) or not u:
                continue
            ts = d.get("timestamp")
            if not ts:
                continue
            day = date.fromisoformat(ts[:10])
            if since and day < since:
                continue
            model = msg.get("model") or d.get("modelId") or "?"
            inp, out = u.get("input", 0), u.get("output", 0)
            cr, cw = u.get("cacheRead", 0), u.get("cacheWrite", 0)
            prov = prime_provider(model, prov_of_model)
            fresh = max(inp - cr, 0) if prov == "Zhipu" else inp
            rows.append(
                (
                    day,
                    prov,
                    model,
                    fresh,
                    cr,
                    cw,
                    out,
                    u.get("reasoning", 0),
                    (u.get("cost") or {}).get("total", 0.0),
                )
            )
    return rows


def _prime_agg(rows, source: str, con, since: date | None) -> int:
    """Collapse per-message rows to the day|provider|model grain and write them,
    replacing only the window this refresh covers. Rows for providers with an
    authoritative API in AUTHORITATIVE are dropped where that API has the
    (day, model) — see _drop_api_covered."""
    agg: dict[tuple, list] = {}
    for day, prov, model, fresh, cr, cw, out, reason, cost in rows:
        a = agg.setdefault((day, prov, model), [0, 0, 0, 0, 0, 0, 0.0])
        a[0] += 1
        a[1] += fresh
        a[2] += cr
        a[3] += cw
        a[4] += out
        a[5] += reason
        a[6] += cost
    recs = [
        (
            day,
            prov,
            model,
            a[0],
            a[1],
            a[2],
            a[3],
            a[4],
            a[5],
            a[1] + a[2] + a[4] + a[5],
            a[6],
            "card",
        )
        for (day, prov, model), a in agg.items()
    ]
    recs = _drop_api_covered(con, recs)
    if recs:
        _delete_window(con, source, since)
        con.executemany(
            f"""
            INSERT INTO fact_usage
            (source, day, provider, model, reqs, fresh_in, cached, cache_write,
             output, reasoning, tokens, cost_usd, cost_basis, loaded_at)
            VALUES ('{source}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())""",  # noqa: S608  # constants + fixed columns, not user input
            recs,
        )
    return len(recs)


def _prime_journal_floor(live: set[str], staged: set[str], since: date | None) -> list[tuple]:
    """Session-grain floor rows from the daemon command journal, for sessions
    with NO session JSONL anywhere (not live, not staged in the backup dir).

    The journal keeps appending cumulative snapshots for every session the
    daemon saw — including live and backup-staged ones, whose per-message
    JSONLs are strictly better — so anything with a JSONL is skipped here or
    it would double-count. What survives is the only record of sessions whose
    JSONLs were rotated away before any snapshot captured them: one row per
    session under its last-activity day, no cache split (unmeasured, not
    zero), reqs = messageCount. Grain is a floor, not a reconciliation
    target. (day, prov, model, fresh, cached, cw, output, reason, cost)."""
    prov_map = {
        "opencode": "OpenCode",
        "openrouter": "OpenRouter",
        "Zhipu": "Zhipu",
        "Atria": "Atria",
    }
    last: dict[str, dict] = {}
    for f in sorted((PRIME_DIR / "daemon-workers").glob("*/command-journal.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            data = (d.get("response") or {}).get("data") or {}
            u = data.get("usage")
            sid = data.get("sessionId")
            if not isinstance(u, dict) or not sid:
                continue
            stamp = data.get("lastActivityAt") or d.get("recordedAt") or ""
            if sid not in last or (stamp or "") >= (last[sid]["last"] or ""):
                model = data.get("model") or {}
                last[sid] = dict(
                    last=stamp,
                    model=model.get("id") or "?",
                    provider=prov_map.get(
                        model.get("provider") or "", model.get("provider") or "OpenCode"
                    ),
                    inp=int(u.get("inputTokens", 0) or 0),
                    out=int(u.get("outputTokens", 0) or 0),
                    cost=float(u.get("cost", 0) or 0),
                    msgs=int(data.get("messageCount", 0) or 0),
                )
    rows = []
    for sid, r in last.items():
        if not r["last"] or sid in live or sid in staged:
            continue
        day = date.fromisoformat(r["last"][:10])
        if since and day < since:
            continue
        rows.append((day, r["provider"], r["model"], r["inp"], 0, 0, r["out"], 0, r["cost"]))
    return rows


def load_prime_rlm(con, since: date | None = None) -> int:
    """The whole Prime RLM harness under one source name: live session JSONLs,
    staged backup JSONLs for purged sessions, and the journal floor for
    sessions with no JSONL anywhere — one aggregation, so populations that
    share a (day, provider, model) key merge by summing instead of colliding.

    The plan API totals this harness's Zhipu calls account-wide (ZCode IDE,
    OpenCode and RLM all share the quota), so Zhipu rows drop wherever zai
    has the (day, model) — see _drop_api_covered. Atria and OpenRouter rows
    have no covering API in the store and always survive."""
    live_files = sorted(glob.glob(str(PRIME_DIR / "sessions" / "*.jsonl")))
    live = {Path(f).stem for f in live_files}
    staged = {Path(f).stem for f in glob.glob(str(BACKUP_DIR / "*.jsonl"))} - live
    files = live_files + sorted(str(BACKUP_DIR / f"{s}.jsonl") for s in staged)
    prov_of_model, _ = _prime_cost_cards()
    rows = _prime_session_rows(files, since, prov_of_model)
    rows += _prime_journal_floor(live, staged, since)
    return _prime_agg(rows, "prime-rlm", con, since)


BASE_URL = "https://api.z.ai"
MAX_WINDOW_DAYS = 30  # MONITOR_MAX_RANGE_DAYS in the ZCode source
MEMORY_ENV_GLOB = Path.home() / ".zcode/cli/memories/projects/*/memory/.env"
# USD per 1M tokens — coding-plan API rates (operator-provided, 2026-09-21)
COST_RATES_PER_M: dict[str, dict[str, float]] = {
    "glm-5.3": {"cached": 0.26, "uncached": 1.40, "output": 4.40},
    "glm-5.3-flash": {"cached": 0.03, "uncached": 0.15, "output": 0.50},
}
ENDPOINTS: dict[str, tuple[str, str]] = {
    "quota": ("/api/monitor/usage/quota/limit", ""),
    "model-usage": ("/api/monitor/usage/model-usage", ""),
    "tool-usage": ("/api/monitor/usage/tool-usage", ""),
    "activity": ("/api/monitor/credit-usage/activity", "&type=1"),
    "usage-detail": ("/api/monitor/credit-usage/usage-detail", "&type=1&usageType=MODEL"),
}


def _zai_parse_range(spec: str) -> tuple[date, date]:
    """'7d' | '2w' | '1m' | '2026-09-01' | '2026-09-01..2026-09-21'."""
    spec = spec.strip().lower()
    today = date.today()
    if spec.endswith(("d", "w", "m")) and spec[:-1].isdigit():
        days = int(spec[:-1]) * {"d": 1, "w": 7, "m": 30}[spec[-1]]
        return today - timedelta(days=days - 1), today
    for sep in ("..", ":"):
        if sep in spec:
            a, b = spec.split(sep, 1)
            return _day(a), _day(b)
    return _day(spec), _day(spec)


def _day(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"error: bad date {text!r} (expected YYYY-MM-DD)")


def slices(start: date, end: date) -> list[tuple[date, date]]:
    """Split into ordered <=30d chunks (API cap)."""
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=MAX_WINDOW_DAYS - 1), end)
        out.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return out


def load_api_key(env_file: str | None) -> str:
    if env_file:
        path = Path(env_file).expanduser()
        if key := _read_env_key(path):
            return key
        print(f"note: no ZAI_API_KEY in {path}", file=sys.stderr)
    if os.environ.get("ZAI_API_KEY"):
        return os.environ["ZAI_API_KEY"].strip()
    script_dir = Path(__file__).resolve().parent
    memory_envs = sorted(Path(p) for p in glob.glob(str(MEMORY_ENV_GLOB)))
    for path in [script_dir / ".env", Path(".env"), *memory_envs]:
        if key := _read_env_key(path):
            return key
    sys.exit(
        "error: no API key. Set ZAI_API_KEY, pass --env-file, or put "
        "'ZAI_API_KEY=<key>' in a .env next to this script (or in "
        "~/.zcode/cli/memories/projects/*/memory/.env)."
    )


def _read_env_key(path: Path) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("ZAI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("'\"")
            if key:
                return key
    return None


def fetch(key: str, endpoint: str, start: date, end: date) -> dict:
    parts = [_fetch_slice(key, endpoint, a, b) for a, b in slices(start, end)]
    if endpoint == "quota":
        return parts[0]
    if endpoint == "usage-detail":
        # server returns HOURLY xTime for short windows — always fold to daily
        return _fold_daily(parts[0] if len(parts) == 1 else _merge_detail(parts))
    if len(parts) == 1:
        return parts[0]
    return _merge_series(parts)


def _fold_daily(detail: dict) -> dict:
    usage = detail.get("modelUsage", {})
    days = usage.get("xTime", [])
    if not any(len(d) > 10 for d in days):
        return detail
    order: list[str] = []
    index: dict[str, list[int]] = {}
    for i, stamp in enumerate(days):
        day = stamp[:10]
        if day not in index:
            index[day] = []
            order.append(day)
        index[day].append(i)
    models = []
    for model in usage.get("modelDataList", []):
        folded = {**model}
        for field in TOKEN_FIELDS:
            src = model.get(field, [])
            folded[field] = [sum(src[j] for j in idxs) for idxs in index.values()]
        folded["totalTokens"] = sum(folded["totalTokensUsage"])
        models.append(folded)
    return {
        **detail,
        "modelUsage": {
            **usage,
            "xTime": order,
            "modelDataList": models,
            "modelSummaryList": [
                {
                    "modelCode": m["modelCode"],
                    "modelName": m.get("modelName"),
                    "totalTokens": m["totalTokens"],
                }
                for m in models
            ],
        },
    }


def _fetch_slice(key: str, endpoint: str, start: date, end: date) -> dict:
    query = urllib.parse.urlencode(
        {
            "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    path, extra = ENDPOINTS[endpoint]
    request = urllib.request.Request(  # noqa: S310  # fixed https API endpoint, repo-owned
        f"{BASE_URL}{path}?{query}{extra}", headers={"authorization": key}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310  # fixed https API endpoint, repo-owned
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: {endpoint} -> HTTP {exc.code}: {exc.read().decode()[:200]}")
    except urllib.error.URLError as exc:
        sys.exit(f"error: {endpoint} -> {exc.reason}")
    if not payload.get("success"):
        sys.exit(f"error: {endpoint} -> code {payload.get('code')}: {payload.get('msg')}")
    return payload["data"]


def _merge_series(parts: list[dict]) -> dict:
    merged: dict = {}
    for key in ("x_time", "modelCallCount", "tokensUsage"):
        merged[key] = [item for part in parts for item in part.get(key, [])]
    return merged


TOKEN_FIELDS = (
    "uncachedInputTokensUsage",
    "cachedInputTokensUsage",
    "inputTokensUsage",
    "outputTokensUsage",
    "totalTokensUsage",
)


def _merge_detail(parts: list[dict]) -> dict:
    models: dict[str, dict] = {}
    days: list[str] = []
    for part in parts:
        usage = part.get("modelUsage", {})
        days.extend(usage.get("xTime", []))
        for model in usage.get("modelDataList", []):
            acc = models.setdefault(model["modelCode"], {**model, **{f: [] for f in TOKEN_FIELDS}})
            for field in TOKEN_FIELDS:
                acc[field] = acc.get(field, []) + model.get(field, [])
    for model in models.values():
        model["totalTokens"] = sum(model.get("totalTokensUsage", []))
    summary_list = [
        {"modelCode": code, "modelName": m.get("modelName"), "totalTokens": m["totalTokens"]}
        for code, m in models.items()
    ]
    total = sum(m["totalTokens"] for m in models.values())
    cached = sum(sum(m.get("cachedInputTokensUsage", [])) for m in models.values())
    inputs = sum(sum(m.get("inputTokensUsage", [])) for m in models.values())
    return {
        "summary": {"cacheHitRate": {"value": f"{cached / inputs:.4f}" if inputs else "0"}},
        "modelUsage": {
            "xTime": days,
            "modelDataList": list(models.values()),
            "modelSummaryList": summary_list,
            "totalUsage": {"totalTokens": total},
        },
    }


# ---------------------------------------------------------------- local ----
def local_stats(db_path: Path, start: date, end: date) -> dict[str, dict]:
    """Per-day ZCode-local requests/turns/latency/TTFT/errors, keyed by date."""
    if not db_path.is_file():
        print(f"note: local telemetry db not found at {db_path}", file=sys.stderr)
        return {}
    begin = int(datetime(start.year, start.month, start.day).timestamp() * 1000)
    finish = int(datetime(end.year, end.month, end.day, 23, 59, 59).timestamp() * 1000)
    day = "date(started_at/1000,'unixepoch','localtime')"
    try:
        con = connect(db_path, read_only=True)
    except sqlite3.Error as exc:
        print(f"note: cannot open local db: {exc}", file=sys.stderr)
        return {}
    try:
        reqs = {
            row[0]: row
            for row in con.execute(
                f"SELECT {day}, COUNT(*), SUM(status!='completed'), SUM(retry_count), "  # noqa: S608  # constants + fixed columns, not user input
                "AVG(duration_ms), AVG(time_to_first_token_ms) "
                "FROM model_usage WHERE started_at >= ? AND started_at <= ? GROUP BY 1",
                (begin, finish),
            )
        }
        turns = {
            row[0]: row
            for row in con.execute(
                f"SELECT {day}, COUNT(*), SUM(tool_call_count), SUM(tool_error_count) "  # noqa: S608  # constants + fixed columns, not user input
                "FROM turn_usage WHERE started_at >= ? AND started_at <= ? GROUP BY 1",
                (begin, finish),
            )
        }
    finally:
        con.close()
    out: dict[str, dict] = {}
    for day_key in set(reqs) | set(turns):
        r = reqs.get(day_key, (day_key, 0, 0, 0, None, None))
        t = turns.get(day_key, (day_key, 0, 0, 0))
        out[day_key] = {
            "reqs": r[1],
            "errs": r[2] or 0,
            "retries": r[3] or 0,
            "lat": r[4] / 1000 if r[4] is not None else None,
            "ttft": r[5] / 1000 if r[5] is not None else None,
            "turns": t[1],
            "tool_calls": t[2] or 0,
            "tool_errs": t[3] or 0,
        }
    return out


# ----------------------------------------------------------------- cost ----
def day_cost(model_code: str, uncached: float, cached: float, output: float) -> float | None:
    rates = COST_RATES_PER_M.get(model_code.lower())
    if not rates:
        return None
    return (
        cached * rates["cached"] + uncached * rates["uncached"] + output * rates["output"]
    ) / 1e6


DAY = "date(part.time_created/1000,'unixepoch','localtime')"  # JOIN queries
DAY_MSG = "date(time_created/1000,'unixepoch','localtime')"  # message table only


def local_latency(
    db_path: Path, begin: int, finish: int, provider: str | None = None
) -> dict[str, dict]:
    """Per-day reqs/lat/TTFT/tool counts — annotation only, never tokens."""
    if not db_path.is_file():
        print(f"note: local db not found at {db_path}", file=sys.stderr)
        return {}
    try:
        con = connect(db_path, read_only=True)
    except sqlite3.Error as exc:
        print(f"note: cannot open local db: {exc}", file=sys.stderr)
        return {}
    join = " FROM part JOIN message ON part.message_id = message.id "
    where = " WHERE part.time_created >= ? AND part.time_created <= ? "
    params: list = [begin, finish]
    if provider:
        where += " AND json_extract(message.data,'$.providerID') = ? "
        params.append(provider)

    stats: dict[str, dict] = {}
    for day, calls, errs in con.execute(
        f"""SELECT {DAY},  # noqa: S608  # DAY is the module constant at :624; values are ?-bound via params
                  SUM(json_extract(part.data,'$.type')='tool'),
                  SUM(json_extract(part.data,'$.type')='tool'
                      AND json_extract(part.data,'$.state.status')!='completed')
           {join}{where} GROUP BY 1""",
        params,
    ):
        stats.setdefault(day, {})
        stats[day]["tool_calls"] = int(calls or 0)
        stats[day]["tool_errs"] = int(errs or 0)

    # NOTE on TTFT: deliberately not computed. OpenCode writes the
    # step-start part and the first reasoning/text part ~4ms apart, so that gap
    # measures DB write ordering, not provider TTFT (ZCode's DB has an explicit
    # time_to_first_token_ms; OpenCode has no equivalent). Step latency is real.
    buckets: dict[str, list] = {}
    for day, started, finished in con.execute(
        f"""SELECT {DAY},  # noqa: S608  # DAY is the module constant at :624; values are ?-bound via params
                  MIN(CASE WHEN json_extract(part.data,'$.type')='step-start'
                           THEN part.time_created END),
                  MAX(CASE WHEN json_extract(part.data,'$.type')='step-finish'
                           THEN part.time_updated END)
           {join}{where} GROUP BY part.message_id, 1""",
        params,
    ):
        if started is None or finished is None:
            continue
        buckets.setdefault(day, []).append((finished - started) / 1000)
    for day, lats in buckets.items():
        cell = stats.setdefault(day, {})
        cell["reqs"] = len(lats)
        cell["lat"] = sum(lats) / len(lats)
    con.close()
    return stats


def load_zai(con, since: date | None = None) -> int:
    """Zhipu plan API: usage-detail + model-usage (authoritative)."""

    env = REPO / "memory" / ".env"
    key = load_api_key(str(env))
    if not key:
        raise SystemExit("zai: no ZAI_API_KEY in memory/.env")
    # usage-detail is DAY granularity with a full per-model split:
    # modelUsage.modelDataList[] = {modelCode, xTime:[dates],
    #   uncachedInputTokensUsage, cachedInputTokensUsage, inputTokensUsage,
    #   outputTokensUsage, totalTokensUsage, *CreditsUsage (all 0 on plan)}
    start, end = _zai_parse_range("30d")
    detail = fetch(key, "usage-detail", start, end)
    mu = detail.get("modelUsage") or {}
    xtime = mu.get("xTime") or []  # day labels live on modelUsage, not per-model
    recs = []
    for md in mu.get("modelDataList") or []:
        model = md.get("modelCode") or md.get("modelName") or "?"
        for i, day_str in enumerate(xtime):
            day = date.fromisoformat(day_str[:10])
            if since and day < since:
                continue
            fresh = int(md.get("uncachedInputTokensUsage", [0] * len(xtime))[i] or 0)
            cached = int(md.get("cachedInputTokensUsage", [0] * len(xtime))[i] or 0)
            output = int(md.get("outputTokensUsage", [0] * len(xtime))[i] or 0)
            # GLM semantics: input INCLUDES cache reads (fresh = input - cached,
            # and the feed reports uncached/cached separately, already split)
            cost = day_cost(model, fresh, cached, output) or 0.0
            recs.append(
                (day, "Zhipu", model, fresh, cached, output, fresh + cached + output, cost, "rate")
            )
    if recs:
        _delete_window(con, "zai", since)
        con.executemany(
            """INSERT INTO fact_usage
            (source, day, provider, model, reqs, fresh_in, cached, cache_write,
             output, reasoning, tokens, cost_usd, cost_basis, loaded_at)
            VALUES ('zai', ?, ?, ?, 0, ?, ?, 0, ?, 0, ?, ?, ?, now())""",
            recs,
        )
    return len(recs)


def load_opencode(con, since: date | None = None) -> int:
    """The whole OpenCode harness under one source name: the live opencode.db
    plus the timeshift snapshot copies, deduped on message.id (the message
    table PK) — the live DB only keeps recent sessions and each older
    snapshot holds date ranges the current file has rotated away. There is no
    itemized usage API for Zen models, so these local DBs are authoritative.

    providerID is mapped to the store's provider names BEFORE aggregating so
    the two Zhipu plan spellings (zai-coding-plan / zhipuai-coding-plan) fold
    into one key. The harness's Zhipu calls are a strict subset of the plan
    API, so those rows drop wherever zai has the (day, model); Zen and
    OpenRouter rows always survive (no covering API in the store). OpenCode
    semantics: input EXCLUDES cache reads."""
    if not OC_DB.is_file():
        raise SystemExit(f"opencode: db not found at {OC_DB}")
    dbs = [str(OC_DB)] + sorted(glob.glob(str(BACKUP_TELEMETRY / "opencode-*.db")))
    pmap = {
        "zai-coding-plan": "Zhipu",
        "zhipuai-coding-plan": "Zhipu",
        "opencode": "OpenCode",
        "openrouter": "OpenRouter",
    }
    seen: set[str] = set()  # message.id across the overlapping snapshots
    agg: dict[tuple, list] = {}
    for db in dbs:
        for day_s, prov, model, mid, inp, cached, outp, reason, cost in _oc_messages(db):
            if mid in seen:
                continue
            seen.add(mid)
            day = date.fromisoformat(day_s)
            if since and day < since:
                continue
            a = agg.setdefault(
                (day, pmap.get(prov, prov or "OpenCode"), model), [0, 0, 0, 0, 0, 0.0]
            )
            a[0] += 1
            a[1] += inp
            a[2] += cached
            a[3] += outp
            a[4] += reason
            a[5] += cost
    recs = [
        (d_, p_, m_, a[0], a[1], a[2], 0, a[3], a[4], a[1] + a[2] + a[3] + a[4], a[5], "local")
        for (d_, p_, m_), a in agg.items()
    ]
    recs = _drop_api_covered(con, recs)
    if recs:
        _delete_window(con, "opencode", since)
        con.executemany(
            """INSERT INTO fact_usage
            (source, day, provider, model, reqs, fresh_in, cached, cache_write,
             output, reasoning, tokens, cost_usd, cost_basis, loaded_at)
            VALUES ('opencode', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())""",
            recs,
        )
    return len(recs)


def _drop_api_covered(con, recs: list) -> list:
    """Drop rows an authoritative API source already accounts for.

    For every provider in AUTHORITATIVE that appears in `recs`, the
    registered API source sees that provider's usage account-/quota-wide
    (Zhipu: every harness on the plan), so the harness rows for it are a
    strict subset. Where the API HAS data for the (day, model), it is
    authoritative — drop our row there. Where it does not (days older than
    the API's fetch window, or the API was never loaded), the local row is
    the only record, so it survives. Without this, `report --range all`
    double-counts the same calls once per source."""
    provs = {r[1] for r in recs} & set(AUTHORITATIVE)
    if not provs:
        return recs
    api_covered: set[tuple] = set()
    for src in {AUTHORITATIVE[p] for p in provs}:
        api_covered |= {
            (d_, m_)
            for d_, m_ in con.execute(
                "SELECT DISTINCT day, model FROM fact_usage WHERE source = ?", [src]
            ).fetchall()
        }
    if not api_covered:
        return recs
    return [r for r in recs if r[1] not in provs or (r[0], r[2]) not in api_covered]


def _oc_messages(db: str):
    """Yield one assistant message's usage from an opencode snapshot DB:
    (day, provider, model, message_id, input, cache_read, output, reasoning,
    cost). OpenCode semantics: input EXCLUDES cache reads."""
    con = _ro_connect(db, read_only=True)
    try:
        yield from con.execute(
            """SELECT date(time_created/1000,'unixepoch','localtime'),
                      json_extract(data,'$.providerID'),
                      json_extract(data,'$.modelID'), id,
                      COALESCE(json_extract(data,'$.tokens.input'),0),
                      COALESCE(json_extract(data,'$.tokens.cache.read'),0),
                      COALESCE(json_extract(data,'$.tokens.output'),0),
                      COALESCE(json_extract(data,'$.tokens.reasoning'),0),
                      COALESCE(json_extract(data,'$.cost'),0)
               FROM message WHERE json_extract(data,'$.role')='assistant'"""
        )
    finally:
        con.close()


def _delete_window(con, source: str, since: date | None) -> None:
    """Delete only the rows a refresh will re-insert. With no `since` the whole
    source is replaced (first load); with `since` only days >= since are touched,
    so immutable history is never clobbered."""
    if since is None:
        con.execute(f"DELETE FROM fact_usage WHERE source = '{source}'")  # noqa: S608  # constants + fixed columns, not user input
    else:
        con.execute(f"DELETE FROM fact_usage WHERE source = '{source}' AND day >= ?", [since])  # noqa: S608  # constants + fixed columns, not user input


# ------------------------------------------------------------- reporting ----

Q_RANGE = """
SELECT source, provider, model, sum(reqs) AS reqs, sum(fresh_in) AS fresh_in,
       sum(cached) AS cached, sum(output) AS output, sum(reasoning) AS reasoning,
       sum(tokens) AS tokens, round(sum(cost_usd), 4) AS cost_usd,
       CASE WHEN sum(fresh_in) + sum(cached) > 0
            THEN round(sum(cached)::DOUBLE / (sum(fresh_in) + sum(cached)) * 100, 1)
       END AS hit_pct
FROM fact_usage {where}
GROUP BY 1, 2, 3 ORDER BY tokens DESC, source, provider, model
"""

Q_TOTAL = """
SELECT count(*) AS rows, sum(reqs) AS reqs, sum(tokens) AS tokens,
       round(sum(cost_usd), 4) AS cost_usd,
       min(day) AS first_day, max(day) AS last_day,
       max(loaded_at) AS last_load
FROM fact_usage {where}
"""


def store_range(con: duckdb.DuckDBPyConnection) -> tuple[date, date]:
    """The full span of data actually in the store — the default range, so a
    report always covers everything loaded (including imported history) rather
    than an arbitrary trailing window."""
    r = con.execute("SELECT min(day), max(day) FROM fact_usage").fetchone()
    if not r or not r[0]:
        from datetime import date as _d

        t = _d.today()
        return t, t
    return r[0], r[1]


def parse_range(spec: str, con: duckdb.DuckDBPyConnection | None = None) -> tuple[date, date]:
    """Range spec: 'all' (store span) | Nd | Nw | Nm | date | date..date.
    'all' needs the connection to read the store's min/max day."""
    from datetime import timedelta

    today = date.today()
    if spec == "all":
        if con is None:
            raise SystemExit("range 'all' needs the db connection")
        return store_range(con)
    if spec.endswith("d"):
        return today - timedelta(days=int(spec[:-1]) - 1), today
    if spec.endswith("w"):
        return today - timedelta(weeks=int(spec[:-1])), today
    if spec.endswith("m"):
        return today - timedelta(days=30 * int(spec[:-1])), today
    if ".." in spec:
        a, b = spec.split("..")
        return date.fromisoformat(a), date.fromisoformat(b)
    d = date.fromisoformat(spec)
    return d, d


def query_rows(
    con: duckdb.DuckDBPyConnection,
    spec: str,
    source: str | None,
    provider: str | None,
    model: str | None,
) -> tuple[dict, list]:
    """Run a filtered query. Returns (totals, per-model rows) as plain data
    so both the text renderer and --json share one code path."""
    start, end = parse_range(spec, con)
    clauses = ["day BETWEEN ? AND ?"]
    params: list = [start, end]
    if source and source != "all":
        clauses.append("source = ?")
        params.append(source)
    if provider and provider != "all":
        clauses.append("provider = ?")
        params.append(provider)
    if model:
        clauses.append("model LIKE ?")
        params.append(f"%{model}%")
    where = "WHERE " + " AND ".join(clauses)

    tot = con.execute(Q_TOTAL.format(where=where), params).fetchone()  # noqa: S608  # named {where} in a module template; values ?-bound via params
    if tot is None:
        raise RuntimeError("usage totals query returned no row")
    totals = {
        "range": [str(start), str(end)],
        "source": source or "all",
        "provider": provider or "all",
        "model": model or "all",
        "rows": tot[0],
        "reqs": tot[1],
        "tokens": tot[2],
        "cost_usd": round(tot[3] or 0.0, 4),
        "first_day": str(tot[4]) if tot[4] else None,
        "last_day": str(tot[5]) if tot[5] else None,
        "loaded_at": str(tot[6]) if tot[6] else None,
    }
    rows = [
        dict(
            zip(
                (
                    "source",
                    "provider",
                    "model",
                    "reqs",
                    "fresh_in",
                    "cached",
                    "output",
                    "reasoning",
                    "tokens",
                    "cost_usd",
                    "hit_pct",
                ),
                r,
            )
        )
        for r in con.execute(Q_RANGE.format(where=where), params).fetchall()  # noqa: S608  # named {where} in a module template; values ?-bound via params
    ]
    return totals, rows


def _prime_timing_rows(start: date, end: date, provider: str | None) -> list[dict]:
    live_files = sorted(glob.glob(str(PRIME_DIR / "sessions" / "*.jsonl")))
    live = {Path(f).stem for f in live_files}
    staged = {Path(f).stem for f in glob.glob(str(BACKUP_DIR / "*.jsonl"))} - live
    files = live_files + sorted(str(BACKUP_DIR / f"{s}.jsonl") for s in staged)
    prov_of_model, _ = _prime_cost_cards()
    daily: dict[str, list[float]] = {}
    for file_name in files:
        for line in Path(file_name).read_text(errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = record.get("message") or {}
            if record.get("type") != "message" or message.get("role") != "assistant":
                continue
            stamp = record.get("timestamp")
            started = message.get("timestamp")
            if not stamp or not isinstance(started, (int, float)):
                continue
            day = date.fromisoformat(stamp[:10])
            if day < start or day > end:
                continue
            model = message.get("model") or record.get("modelId") or "?"
            row_provider = prime_provider(model, prov_of_model)
            if provider not in (None, "all") and row_provider != provider:
                continue
            completed = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000
            duration = (completed - started) / 1000
            if duration >= 0:
                daily.setdefault(str(day), []).append(duration)
    return [
        {
            "harness": "prime-rlm",
            "day": day,
            "reqs": len(values),
            "avg_latency_s": sum(values) / len(values),
            "avg_ttft_s": None,
            "turns": None,
            "errors": None,
            "retries": None,
            "tool_calls": None,
            "tool_errors": None,
        }
        for day, values in daily.items()
    ]


def timing_rows(
    con: duckdb.DuckDBPyConnection,
    spec: str,
    source: str | None,
    provider: str | None,
    model: str | None,
) -> list[dict]:
    """Local per-harness timing and operations annotations for the report."""
    if model:
        return []
    start, end = parse_range(spec, con)
    rows: list[dict] = []
    if source in (None, "all", "zai") and provider in (None, "all", "Zhipu"):
        for day, stats in local_stats(ZCODE_DB, start, end).items():
            rows.append(
                {
                    "harness": "zcode",
                    "day": day,
                    "reqs": stats.get("reqs", 0),
                    "avg_latency_s": stats.get("lat"),
                    "avg_ttft_s": stats.get("ttft"),
                    "turns": stats.get("turns", 0),
                    "errors": stats.get("errs", 0),
                    "retries": stats.get("retries", 0),
                    "tool_calls": stats.get("tool_calls", 0),
                    "tool_errors": stats.get("tool_errs", 0),
                }
            )
    if source in (None, "all", "prime-rlm"):
        rows.extend(_prime_timing_rows(start, end, provider))
    if source in (None, "all", "opencode"):
        provider_map = {
            "Zhipu": "zai-coding-plan",
            "OpenRouter": "openrouter",
            "OpenCode": "opencode",
        }
        selected = None if provider in (None, "all") else provider_map.get(provider)
        if selected is not None or provider in (None, "all"):
            begin = int(datetime(start.year, start.month, start.day).timestamp() * 1000)
            finish = int(datetime(end.year, end.month, end.day, 23, 59, 59).timestamp() * 1000)
            for day, stats in local_latency(OC_DB, begin, finish, selected).items():
                rows.append(
                    {
                        "harness": "opencode",
                        "day": day,
                        "reqs": stats.get("reqs", 0),
                        "avg_latency_s": stats.get("lat"),
                        "avg_ttft_s": None,
                        "turns": None,
                        "errors": None,
                        "retries": None,
                        "tool_calls": stats.get("tool_calls", 0),
                        "tool_errors": stats.get("tool_errs", 0),
                    }
                )
    return sorted(rows, key=lambda r: r["reqs"], reverse=True)


def _telemetry_cell() -> dict:
    return {
        "latencies": [],
        "ttfts": [],
        "turn_ids": set(),
        "generated": 0,
        "duration_s": 0.0,
        "observed_reasoning": 0,
        "output_tokens": 0,
        "errors": 0,
        "retries": 0,
        "cancellations": 0,
        "context_exceeded": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "compactions": 0,
        "patches": 0,
        "metrics": set(),
    }


def _distribution(values: list[float]) -> tuple[float | None, ...]:
    if not values:
        return None, None, None, None
    ordered = sorted(values)
    p95 = ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]
    return statistics.mean(ordered), statistics.median(ordered), p95, max(ordered)


def _finish_model_telemetry(agg: dict[tuple, dict], harness: str) -> list[dict]:
    rows = []
    for (day, provider, model), cell in agg.items():
        lat_avg, lat_p50, lat_p95, lat_max = _distribution(cell["latencies"])
        ttft_avg, ttft_p50, ttft_p95, ttft_max = _distribution(cell["ttfts"])
        metrics = cell["metrics"]
        tool_calls = cell["tool_calls"]
        tool_errors = cell["tool_errors"]
        rows.append(
            {
                "harness": harness,
                "day": str(day),
                "provider": provider,
                "model": model,
                "requests": cell.get("requests", 0),
                "turns": len(cell["turn_ids"]) if "turns" in metrics else None,
                "observed_reasoning": cell["observed_reasoning"],
                "output_tokens": cell["output_tokens"],
                "latency_avg_s": lat_avg,
                "latency_p50_s": lat_p50,
                "latency_p95_s": lat_p95,
                "latency_max_s": lat_max,
                "ttft_avg_s": ttft_avg,
                "ttft_p50_s": ttft_p50,
                "ttft_p95_s": ttft_p95,
                "ttft_max_s": ttft_max,
                "generated_tps": (
                    cell["generated"] / cell["duration_s"] if cell["duration_s"] > 0 else None
                ),
                "errors": cell["errors"] if "errors" in metrics else None,
                "retries": cell["retries"] if "retries" in metrics else None,
                "cancellations": (cell["cancellations"] if "cancellations" in metrics else None),
                "context_exceeded": (
                    cell["context_exceeded"] if "context_exceeded" in metrics else None
                ),
                "tool_calls": tool_calls if "tool_calls" in metrics else None,
                "tool_errors": tool_errors if "tool_errors" in metrics else None,
                "tool_error_pct": (
                    tool_errors / tool_calls * 100
                    if "tool_calls" in metrics and tool_calls
                    else 0.0
                    if "tool_calls" in metrics
                    else None
                ),
                "compactions": (cell["compactions"] if "compactions" in metrics else None),
                "patches": cell["patches"] if "patches" in metrics else None,
            }
        )
    return rows


def _zcode_model_telemetry(
    start: date, end: date, provider: str | None, model: str | None
) -> list[dict]:
    if not ZCODE_DB.is_file() or provider not in (None, "all", "Zhipu"):
        return []
    begin = int(datetime(start.year, start.month, start.day).timestamp() * 1000)
    finish = int(datetime(end.year, end.month, end.day, 23, 59, 59).timestamp() * 1000)
    con = _ro_connect(ZCODE_DB, read_only=True)
    try:
        turn_errors = {
            turn: count
            for turn, count in con.execute(
                "SELECT turn_id, sum(tool_error_count) FROM turn_usage "
                "WHERE turn_id IS NOT NULL GROUP BY turn_id"
            )
        }
        agg: dict[tuple, dict] = {}
        rows = con.execute(
            """SELECT date(started_at/1000,'unixepoch','localtime'), model_id,
                      status, duration_ms, time_to_first_token_ms, turn_id,
                      tool_call_count, output_tokens, reasoning_tokens,
                      retry_count, cancelled_by_user, context_exceeded
               FROM model_usage WHERE started_at >= ? AND started_at <= ?""",
            (begin, finish),
        )
        for (
            day,
            model_id,
            status,
            duration,
            ttft,
            turn_id,
            tools,
            output,
            reasoning,
            retries,
            cancelled,
            context_exceeded,
        ) in rows:
            model_id = (model_id or "?").lower()
            if model and model not in model_id:
                continue
            key = (day, "Zhipu", model_id)
            cell = agg.setdefault(key, _telemetry_cell())
            cell["metrics"].update(
                {
                    "turns",
                    "errors",
                    "retries",
                    "cancellations",
                    "context_exceeded",
                    "tool_calls",
                    "tool_errors",
                }
            )
            cell["requests"] = cell.get("requests", 0) + 1
            if turn_id:
                cell["turn_ids"].add(turn_id)
            if duration is not None:
                seconds = duration / 1000
                cell["latencies"].append(seconds)
                cell["duration_s"] += seconds
            if ttft is not None:
                cell["ttfts"].append(ttft / 1000)
            cell["observed_reasoning"] += reasoning or 0
            cell["output_tokens"] += output or 0
            cell["generated"] += (output or 0) + (reasoning or 0)
            cell["errors"] += status != "completed"
            cell["retries"] += retries or 0
            cell["cancellations"] += cancelled or 0
            cell["context_exceeded"] += context_exceeded or 0
            cell["tool_calls"] += tools or 0
            cell["tool_errors"] += turn_errors.get(turn_id, 0) or 0
    finally:
        con.close()
    return _finish_model_telemetry(agg, "zcode")


def _opencode_model_telemetry(
    start: date, end: date, provider: str | None, model: str | None
) -> list[dict]:
    if not OC_DB.is_file():
        return []
    begin = int(datetime(start.year, start.month, start.day).timestamp() * 1000)
    finish = int(datetime(end.year, end.month, end.day, 23, 59, 59).timestamp() * 1000)
    dbs = [str(OC_DB)] + sorted(glob.glob(str(BACKUP_TELEMETRY / "opencode-*.db")))
    provider_map = {
        "zai-coding-plan": "Zhipu",
        "zhipuai-coding-plan": "Zhipu",
        "opencode": "OpenCode",
        "openrouter": "OpenRouter",
    }
    agg: dict[tuple, dict] = {}
    seen: set[str] = set()
    query = """SELECT m.id, date(m.time_created/1000,'unixepoch','localtime'),
                      json_extract(m.data,'$.providerID'),
                      json_extract(m.data,'$.modelID'),
                      coalesce(json_extract(m.data,'$.tokens.reasoning'),0),
                      coalesce(json_extract(m.data,'$.tokens.output'),0),
                      min(CASE WHEN json_extract(p.data,'$.type')='step-start'
                               THEN p.time_created END),
                      max(CASE WHEN json_extract(p.data,'$.type')='step-finish'
                               THEN p.time_updated END),
                      sum(json_extract(p.data,'$.type')='tool'),
                      sum(json_extract(p.data,'$.type')='tool'
                          AND json_extract(p.data,'$.state.status')!='completed'),
                      sum(json_extract(p.data,'$.type')='compaction'),
                      sum(json_extract(p.data,'$.type')='patch'),
                      json_extract(m.data,'$.tokens.total') IS NULL
               FROM message m LEFT JOIN part p ON p.message_id=m.id
               WHERE json_extract(m.data,'$.role')='assistant'
                 AND m.time_created >= ? AND m.time_created <= ?
               GROUP BY m.id"""
    for db in dbs:
        con = _ro_connect(db, read_only=True)
        try:
            rows = con.execute(query, (begin, finish))
            for values in rows:
                (
                    message_id,
                    day,
                    raw_provider,
                    model_id,
                    reasoning,
                    output,
                    started,
                    finished,
                    tools,
                    tool_errors,
                    compactions,
                    patches,
                    missing_tokens,
                ) = values
                if message_id in seen or not model_id:
                    continue
                seen.add(message_id)
                row_provider = provider_map.get(raw_provider, raw_provider or "OpenCode")
                if provider not in (None, "all") and row_provider != provider:
                    continue
                if model and model not in model_id:
                    continue
                key = (day, row_provider, model_id)
                cell = agg.setdefault(key, _telemetry_cell())
                cell["metrics"].update(
                    {
                        "errors",
                        "tool_calls",
                        "tool_errors",
                        "compactions",
                        "patches",
                    }
                )
                cell["requests"] = cell.get("requests", 0) + 1
                cell["observed_reasoning"] += reasoning or 0
                cell["output_tokens"] += output or 0
                if started is not None and finished is not None and finished >= started:
                    seconds = (finished - started) / 1000
                    cell["latencies"].append(seconds)
                    cell["duration_s"] += seconds
                    cell["generated"] += (output or 0) + (reasoning or 0)
                cell["errors"] += missing_tokens or 0
                cell["tool_calls"] += tools or 0
                cell["tool_errors"] += tool_errors or 0
                cell["compactions"] += compactions or 0
                cell["patches"] += patches or 0
        finally:
            con.close()
    return _finish_model_telemetry(agg, "opencode")


def _prime_model_telemetry(  # noqa: C901  # moved verbatim; split needs a parity harness (c901 deferred class)
    start: date, end: date, provider: str | None, model: str | None
) -> list[dict]:
    live_files = sorted(glob.glob(str(PRIME_DIR / "sessions" / "*.jsonl")))
    live = {Path(f).stem for f in live_files}
    staged = {Path(f).stem for f in glob.glob(str(BACKUP_DIR / "*.jsonl"))} - live
    files = live_files + sorted(str(BACKUP_DIR / f"{s}.jsonl") for s in staged)
    prov_of_model, _ = _prime_cost_cards()
    agg: dict[tuple, dict] = {}
    for file_name in files:
        for line in Path(file_name).read_text(errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = record.get("message") or {}
            if record.get("type") != "message" or message.get("role") != "assistant":
                continue
            stamp = record.get("timestamp")
            started = message.get("timestamp")
            if not stamp:
                continue
            day = date.fromisoformat(stamp[:10])
            if day < start or day > end:
                continue
            model_id = message.get("model") or record.get("modelId") or "?"
            row_provider = prime_provider(model_id, prov_of_model)
            if provider not in (None, "all") and row_provider != provider:
                continue
            if model and model not in model_id:
                continue
            key = (day, row_provider, model_id)
            cell = agg.setdefault(key, _telemetry_cell())
            cell["metrics"].update({"errors", "tool_calls"})
            usage = message.get("usage") or {}
            reasoning = usage.get("reasoning", 0) or 0
            output = usage.get("output", 0) or 0
            cell["requests"] = cell.get("requests", 0) + 1
            cell["observed_reasoning"] += reasoning
            cell["output_tokens"] += output
            if isinstance(started, (int, float)):
                completed = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000
                duration = (completed - started) / 1000
                if duration >= 0:
                    cell["latencies"].append(duration)
                    cell["duration_s"] += duration
                    cell["generated"] += output + reasoning
            stop_reason = str(message.get("stopReason") or "").lower()
            cell["errors"] += (not usage) or stop_reason in {
                "error",
                "aborted",
                "cancelled",
                "canceled",
            }
            content = message.get("content")
            if isinstance(content, list):
                cell["tool_calls"] += sum(
                    isinstance(item, dict) and item.get("type") == "toolCall" for item in content
                )
    return _finish_model_telemetry(agg, "prime-rlm")


def model_telemetry_rows(
    con: duckdb.DuckDBPyConnection,
    spec: str,
    source: str | None,
    provider: str | None,
    model: str | None,
) -> list[dict]:
    start, end = parse_range(spec, con)
    rows = []
    if source in (None, "all", "zai"):
        rows.extend(_zcode_model_telemetry(start, end, provider, model))
    if source in (None, "all", "opencode"):
        rows.extend(_opencode_model_telemetry(start, end, provider, model))
    if source in (None, "all", "prime-rlm"):
        rows.extend(_prime_model_telemetry(start, end, provider, model))
    return sorted(rows, key=lambda r: r["output_tokens"] or 0, reverse=True)


def _table(headers: list[str], rows: list[list[str]], right: set[int]) -> str:
    """Aligned table: column widths come from the actual cells, so long model
    slugs never truncate (the old fixed-width cut `-20260910` to `-2` and
    bred ghost-alias confusion) and numerics right-align."""
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    def line(cells: list[str]) -> str:
        return "  ".join(
            c.rjust(w) if i in right else c.ljust(w) for i, (c, w) in enumerate(zip(cells, widths))
        )

    out = [line(headers), "  ".join("-" * w for w in widths)]
    out += [line(r) for r in rows]
    return "\n".join(out)


def _seconds(value: float | None) -> str:
    return f"{value:.1f}s" if value is not None else "-"


def _integer(value: int | None) -> str:
    return f"{value:,}" if value is not None else "-"


def _decimal(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "-"


def _percent(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "-"


def _render_model_telemetry(rows: list[dict]) -> None:
    print("== observed model telemetry ==")
    timing_cells = []
    operation_cells = []
    for row in rows:
        identity = [row["harness"], row["day"], row["provider"], row["model"]]
        timing_cells.append(
            identity
            + [
                _integer(row["requests"]),
                _integer(row["observed_reasoning"]),
                _integer(row["output_tokens"]),
                _seconds(row["latency_p50_s"]),
                _seconds(row["latency_p95_s"]),
                _seconds(row["latency_max_s"]),
                _seconds(row["ttft_p50_s"]),
                _seconds(row["ttft_p95_s"]),
                _decimal(row["generated_tps"]),
            ]
        )
        operation_cells.append(
            identity
            + [
                _integer(row["turns"]),
                _integer(row["errors"]),
                _integer(row["retries"]),
                _integer(row["cancellations"]),
                _integer(row["context_exceeded"]),
                _integer(row["tool_calls"]),
                _integer(row["tool_errors"]),
                _percent(row["tool_error_pct"]),
                _integer(row["compactions"]),
                _integer(row["patches"]),
            ]
        )
    print(
        _table(
            [
                "harness",
                "day",
                "provider",
                "model",
                "reqs",
                "obs_reason",
                "output",
                "lat_p50",
                "lat_p95",
                "lat_max",
                "ttft_p50",
                "ttft_p95",
                "tok/s",
            ],
            timing_cells,
            right=set(range(4, 13)),
        )
    )
    print()
    operation_cells.sort(
        key=lambda cells: int(cells[9].replace(",", "")) if cells[9] != "-" else -1,
        reverse=True,
    )
    print(
        _table(
            [
                "harness",
                "day",
                "provider",
                "model",
                "turns",
                "errors",
                "retries",
                "cancel",
                "context",
                "tools",
                "tool_errs",
                "tool_err%",
                "compactions",
                "patches",
            ],
            operation_cells,
            right=set(range(4, 14)),
        )
    )
    print()


def report(  # noqa: C901  # moved verbatim; split needs a parity harness (c901 deferred class)
    con: duckdb.DuckDBPyConnection,
    spec: str,
    source: str | None,
    provider: str | None,
    model: str | None,
    as_json: bool = False,
    endpoint: str = "summary",
    no_timing: bool = False,
) -> None:
    totals, rows = query_rows(con, spec, source, provider, model)
    timings = (
        [] if no_timing or endpoint == "totals" else timing_rows(con, spec, source, provider, model)
    )
    model_telemetry = (
        []
        if no_timing or endpoint == "totals"
        else model_telemetry_rows(con, spec, source, provider, model)
    )
    if as_json:
        out = {"totals": totals, "endpoint": endpoint, "rows": rows}
        if endpoint == "summary":
            out["timing"] = timings
            out["model_telemetry"] = model_telemetry
        if endpoint == "totals":
            out.pop("rows")
        print(json.dumps(out, indent=1, default=str))
        return
    print(
        f"range {totals['range'][0]}..{totals['range'][1]}  source={totals['source']}"
        f" provider={totals['provider']}  model={totals['model']}"
    )
    print(
        f"  rows={totals['rows']} reqs={totals['reqs']:,} tokens={totals['tokens']:,}"
        f" cost=${totals['cost_usd']:.4f}  days {totals['first_day']}..{totals['last_day']}"
        f"  loaded {totals['loaded_at']}\n"
    )
    if endpoint == "totals":
        return
    if totals["rows"] == 0:
        print("== per source/provider/model ==\n  no usage data — run `load` first\n")
    else:
        print("== per source/provider/model ==")
        cells = []
        for r in rows:
            hit = f"{r['hit_pct']}%" if r["hit_pct"] is not None else "-"
            cells.append(
                [
                    r["source"],
                    r["provider"],
                    r["model"],
                    f"{r['reqs']:,}",
                    f"{r['fresh_in']:,}",
                    f"{r['cached']:,}",
                    hit,
                    f"{r['output']:,}",
                    f"{r['reasoning']:,}",
                    f"{r['tokens']:,}",
                    f"{r['cost_usd']:.4f}",
                ]
            )
        print(
            _table(
                [
                    "source",
                    "provider",
                    "model",
                    "reqs",
                    "fresh_in",
                    "cached",
                    "hit%",
                    "output",
                    "reason",
                    "tokens",
                    "cost",
                ],
                cells,
                right=set(range(3, 11)),
            )
        )
        print()
    if model:
        print("== local timing and operations ==\n  unavailable for model filter\n")
    elif timings:
        print("== local timing and operations (telemetry annotation) ==")
        timing_cells = []
        for row in timings:
            timing_cells.append(
                [
                    row["harness"],
                    row["day"],
                    f"{row['reqs']:,}",
                    _seconds(row["avg_latency_s"]),
                    _seconds(row["avg_ttft_s"]),
                    _integer(row["turns"]),
                    _integer(row["errors"]),
                    _integer(row["retries"]),
                    _integer(row["tool_calls"]),
                    _integer(row["tool_errors"]),
                ]
            )
        print(
            _table(
                [
                    "harness",
                    "day",
                    "reqs",
                    "avg_lat",
                    "avg_ttft",
                    "turns",
                    "errors",
                    "retries",
                    "tools",
                    "tool_errs",
                ],
                timing_cells,
                right=set(range(2, 10)),
            )
        )
        print()
    elif not no_timing:
        print("== local timing and operations ==\n  no local telemetry in range\n")
    if not no_timing:
        expected = []
        if source in (None, "all", "zai"):
            expected.append("zcode")
        if source in (None, "all", "prime-rlm"):
            expected.append("prime-rlm")
        if source in (None, "all", "opencode"):
            expected.append("opencode")
        present = {row["harness"] for row in timings} | {row["harness"] for row in model_telemetry}
        coverage = ", ".join(
            f"{name}={'rows' if name in present else 'no rows'}" for name in expected
        )
        print(f"coverage: {coverage}")
        if model_telemetry:
            _render_model_telemetry(model_telemetry)
        else:
            print("== observed model telemetry ==\n  no model telemetry in range\n")


# ------------------------------------------------------------------ main ----

LOADERS = {"prime-rlm": load_prime_rlm, "zai": load_zai, "opencode": load_opencode}


def _refresh_backup(store: Path) -> None:
    """Last-good recovery copy into db-backup/ after a successful load —
    the house sidecar pattern (rebuild_common.last-good semantics), using
    snapshot_db's CHECKPOINT+zstd technique. Best-effort: a failed backup
    logs a WARNING and never fails the load. Only the default store path
    is ever backed up (a --db scratch run must not clobber the recovery
    point)."""
    if store.resolve() != DB_PATH.resolve():
        return
    import logging

    from helpers.maintenance.snapshot_db import create_duckdb_snapshot

    try:
        create_duckdb_snapshot(
            store,
            REPO / "db-backup" / f"{store.stem}_backup.duckdb.zst",
            logging.getLogger("analytics-backup"),
        )
    except Exception as exc:  # noqa: BLE001  (backup must not fail the load)
        print(f"WARNING: db-backup refresh failed: {exc}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Unified LLM usage analytics (local DuckDB store)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="  load   prime-rlm | zai | opencode | all\n  report  any slice of the stored data",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("load", help="refresh a source into the local store")
    lp.add_argument("source", choices=[*LOADERS, "all"])
    lp.add_argument(
        "--days",
        type=int,
        default=2,
        help="incremental: refresh only the last N days, leaving older "
        "rows untouched (default 2). Use --full for a complete reload.",
    )
    lp.add_argument(
        "--full", action="store_true", help="ignore --days; delete and reload the whole source"
    )
    lp.add_argument("--db", type=Path, default=DB_PATH)
    rp = sub.add_parser("report", help="query the stored data")
    rp.add_argument(
        "--range",
        default="all",
        help="all (store span, default) | Nd | Nw | Nm | YYYY-MM-DD | YYYY-MM-DD..YYYY-MM-DD",
    )
    rp.add_argument("--source", choices=[*LOADERS, "all"], default="all")
    rp.add_argument("--provider", default="all")
    rp.add_argument("--model", default=None, help="substring match")
    rp.add_argument(
        "--endpoint",
        choices=["summary", "totals"],
        default="summary",
        help="summary = totals + per-model rows | totals only",
    )
    rp.add_argument(
        "--json", action="store_true", help="emit JSON (for piping into jq / other tools)"
    )
    rp.add_argument(
        "--no-timing",
        action="store_true",
        help="omit local harness timing and model telemetry rows",
    )
    rp.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args()

    con = duckdb.connect(str(args.db))
    con.execute(SCHEMA)
    load_ok = False
    from helpers.maintenance.maint_timing import RunTimer

    _timer = RunTimer(
        "model_analytics", mode=f"load:{args.source}" if args.cmd == "load" else args.cmd
    )
    try:
        if args.cmd == "load":
            # The family loaders (prime-rlm, opencode) read zai's rows at
            # load time to drop plan-covered Zhipu rows (_drop_api_covered),
            # so zai must land first. On a fresh store, loading prime-rlm
            # before zai leaves nothing to drop against and double-counts.
            srcs = list(LOADERS) if args.source == "all" else [args.source]
            if args.source == "all":
                api_first = [s for s in srcs if s == "zai"]
                srcs = api_first + [s for s in srcs if s not in api_first]
            _loaded_total = 0
            for s in srcs:
                started = datetime.now(UTC)
                # incremental: refresh only recent days unless --full
                since = date.today() - timedelta(days=args.days) if not args.full else None
                try:
                    _p0 = datetime.now(UTC)
                    n = LOADERS[s](con, since)
                    _loaded_total += n or 0
                    _timer.record_phase(f"load:{s}", _p0, datetime.now(UTC), extra=f"rows={n}")
                    con.execute(
                        "INSERT INTO load_log VALUES (?, ?, ?, 'ok', ?)",
                        [s, started, n, f"since={since}"],
                    )
                    print(f"  loaded {s}: {n} rows (since={since})")
                except Exception as exc:
                    con.execute(
                        "INSERT INTO load_log VALUES (?, ?, -1, 'error', ?)",
                        [s, started, str(exc)[:200]],
                    )
                    print(f"  FAILED {s}: {exc}", file=sys.stderr)
            load_ok = True
        else:
            report(
                con,
                args.range,
                args.source,
                args.provider,
                args.model,
                as_json=args.json,
                endpoint=args.endpoint,
                no_timing=args.no_timing,
            )
    finally:
        con.close()
    if args.cmd == "load":
        _timer.finish(0, f"load:{args.source} rows={_loaded_total}")
    if args.cmd == "load" and load_ok:
        _refresh_backup(args.db)


if __name__ == "__main__":
    main()
