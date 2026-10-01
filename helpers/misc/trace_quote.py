#!/usr/bin/env python3
"""trace_quote — resolve agent-trace:<harness>#<anchor> citations against the
behavioral-trace store.

Anchor kinds (mirror the star schema identity columns):
    turn:<turn_id>          -> fact_turn
    tool:<tool_call_id>     -> fact_tool_call
    req:<request_id>        -> fact_model_request
    event:<span_id>         -> fact_event

Token form: agent-trace:<harness>#<anchor>, harness in {prime, opencode,
zcode}. Every entity id is unique within its harness namespace, so the
token resolves unambiguously. Event span_ids may repeat within a harness;
when they do, all matches are printed.

Stability (S2 of trace_quote_citations): every identity column is
harness-native and 100% stable across full re-ingest runs (captured in
doc/local/engineering/capture_traces.md §11), so all four anchor kinds are
safe to cite: dim_session.session_id, fact_turn.turn_id,
fact_tool_call.tool_call_id, fact_model_request.request_id, fact_event.span_id,
fact_file_edit.snapshot_hash.

Precedent: the convo_query --expand pointer expansion. Grammar + resolver
arc: doc/improvements/archive/tooling/trace_quote_citations.md (slice S1).

Exit codes:
    0  cited anchor resolved successfully
    1  unrecognized token, id not found, or store missing
    2  usage error

Usage:
    python3 helpers/misc/trace_quote.py "agent-trace:prime#turn:816642a2"
    python3 helpers/misc/trace_quote.py "agent-trace:opencode#event:evt_0d3c1b043001SjhBk6F5uc1LRl" --json
    python3 helpers/misc/trace_quote.py "agent-trace:zcode#req:00e1f7fe" --db /tmp/store.duckdb
    python3 helpers/misc/trace_quote.py --sweep doc/              # resolve every token under doc/
    python3 helpers/misc/trace_quote.py --sweep doc/ findata/     # scan several trees

When to cite: anchor an arc-record claim to the exact turn that decided it,
a cost/latency figure to the request behind it, or an incident to the event
that caused it. Never cite by timestamp or prose quote — both drift. Only the
token is emitted; the payload stays in the store (machine-local, not tracked).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence
from typing import Any

import duckdb

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "memory" / "data" / "agent_traces.duckdb"
HARNESSES = ("prime", "opencode", "zcode")

_TOKEN_RE = re.compile(r"^agent-trace:(\S+)#(turn|tool|req|event):(\S+)$")


@dataclass
class Resolution:
    citation: str = ""
    harness: str = ""
    kind: str = ""
    anchor_type: str = ""
    anchor_id: str = ""
    found: bool = False
    row: dict[str, Any] | None = None
    context: dict[str, Any] = field(default_factory=dict)
    near_misses: Sequence[dict[str, Any]] = ()

    @property
    def success(self) -> bool:
        return self.found


def _row_to_dict(
    rel: duckdb.DuckDBPyConnection | duckdb.DuckDBPyRelation | None,
) -> dict[str, Any] | None:
    if rel is None:
        return None
    cols = [c[0] for c in rel.description]
    row = rel.fetchone()
    if row is None:
        return None
    return dict(zip(cols, row))


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else ""


def _summarize_row(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    s: dict[str, Any] = {"source": row.get("source")}
    if kind == "turn":
        s.update(
            turn_id=row["turn_id"],
            session_id=row["session_id"],
            ts=row["ts"],
            status=row.get("status"),
            model_requests=row.get("model_requests"),
            tool_calls=row.get("tool_calls"),
            tokens=row.get("tokens"),
        )
    elif kind == "tool":
        s.update(
            tool_call_id=row["tool_call_id"],
            tool_name=row.get("tool_name"),
            ts=row["ts"],
            status=row.get("status"),
            exit_code=row.get("exit_code"),
        )
    elif kind == "req":
        s.update(
            request_id=row["request_id"],
            model=row.get("model"),
            provider=row.get("provider"),
            status=row.get("status"),
            ts=row["ts"],
            input=row.get("input"),
            output=row.get("output"),
        )
    elif kind == "event":
        s.update(
            span_id=row["span_id"],
            trace_id=row.get("trace_id"),
            parent_span_id=row.get("parent_span_id"),
            event_name=row.get("event_name"),
            ts=row["ts"],
            status=row.get("status"),
        )
    for k, v in s.items():
        if isinstance(v, datetime):
            s[k] = v.isoformat()
    return s


def _near_misses(
    con: duckdb.DuckDBPyConnection, harness: str, kind: str, limit: int = 5
) -> list[dict[str, Any]]:
    if kind == "turn":
        q = """
            SELECT turn_id, session_id, ts, status, tokens
            FROM fact_turn WHERE source = ? ORDER BY ts DESC LIMIT ?
        """
        cols = ["turn_id", "session_id", "ts", "status", "tokens"]
    elif kind == "tool":
        q = """
            SELECT tool_call_id, tool_name, ts, status, exit_code
            FROM fact_tool_call WHERE source = ? ORDER BY ts DESC LIMIT ?
        """
        cols = ["tool_call_id", "tool_name", "ts", "status", "exit_code"]
    elif kind == "req":
        q = """
            SELECT request_id, model, provider, status, ts
            FROM fact_model_request WHERE source = ? ORDER BY ts DESC LIMIT ?
        """
        cols = ["request_id", "model", "provider", "status", "ts"]
    else:  # event
        q = """
            SELECT span_id, event_name, parent_span_id, ts, status
            FROM fact_event WHERE source = ? ORDER BY ts DESC LIMIT ?
        """
        cols = ["span_id", "event_name", "parent_span_id", "ts", "status"]
    rows = con.execute(q, (harness, limit)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def _fetch_one(con: duckdb.DuckDBPyConnection, query: str, params: tuple) -> dict[str, Any] | None:
    rel = con.execute(query, params)
    row = rel.fetchone()
    if row is None:
        return None
    return dict(zip((c[0] for c in rel.description), row))


def _resolve_turn(con: duckdb.DuckDBPyConnection, harness: str, turn_id: str) -> dict[str, Any]:
    r = _fetch_one(
        con, "SELECT * FROM fact_turn WHERE source = ? AND turn_id = ?", (harness, turn_id)
    )
    out: dict[str, Any] = {"model_requests": [], "tool_calls": []}
    if r:
        out["model_requests"] = [
            dict(
                zip(
                    [
                        "request_id",
                        "model",
                        "provider",
                        "status",
                        "attempt_index",
                        "ttft_ms",
                        "duration_ms",
                        "input",
                        "output",
                    ],
                    rr,
                )
            )
            for rr in con.execute(
                """SELECT request_id, model, provider, status, attempt_index, ttft_ms, duration_ms, input, output
                   FROM fact_model_request WHERE source = ? AND session_id = ? AND turn_id = ? ORDER BY attempt_index""",
                (harness, r["session_id"], turn_id),
            ).fetchall()
        ]
        out["tool_calls"] = [
            dict(
                zip(
                    ["tool_call_id", "tool_name", "ts", "status", "exit_code"],
                    rr,
                )
            )
            for rr in con.execute(
                """SELECT tool_call_id, tool_name, ts, status, exit_code
                   FROM fact_tool_call WHERE source = ? AND session_id = ? AND turn_id = ? ORDER BY ts, tool_call_id""",
                (harness, r["session_id"], turn_id),
            ).fetchall()
        ]
    return out


def _resolve_tool(
    con: duckdb.DuckDBPyConnection, harness: str, tool_call_id: str
) -> dict[str, Any]:
    r = _fetch_one(
        con,
        "SELECT * FROM fact_tool_call WHERE source = ? AND tool_call_id = ?",
        (harness, tool_call_id),
    )
    out: dict[str, Any] = {"parent_turn": None, "adjacent_tool_calls": []}
    if r:
        pt = con.execute(
            """SELECT source, session_id, turn_id, ts, status FROM fact_turn
               WHERE source = ? AND session_id = ? AND turn_id = ?""",
            (harness, r["session_id"], r["turn_id"]),
        )
        out["parent_turn"] = _row_to_dict(pt)
        all_tc = con.execute(
            """SELECT tool_call_id, tool_name, ts, exit_code
               FROM fact_tool_call WHERE source = ? AND turn_id = ?
               ORDER BY ts, tool_call_id""",
            (harness, r["turn_id"]),
        ).fetchall()
        idx = next(i for i, t in enumerate(all_tc) if t[0] == tool_call_id)
        for i, t in enumerate(all_tc):
            if i in (idx - 1, idx + 1):
                out["adjacent_tool_calls"].append(
                    dict(
                        tool_call_id=t[0],
                        tool_name=t[1],
                        ts=t[2],
                        exit_code=t[3],
                        role=("previous" if i == idx - 1 else "next"),
                    )
                )
    return out


def _resolve_req(con: duckdb.DuckDBPyConnection, harness: str, request_id: str) -> dict[str, Any]:
    r = _fetch_one(
        con,
        "SELECT * FROM fact_model_request WHERE source = ? AND request_id = ?",
        (harness, request_id),
    )
    out: dict[str, Any] = {"parent_turn": None}
    if r:
        out["parent_turn"] = _row_to_dict(
            con.execute(
                """SELECT source, session_id, turn_id, ts, status FROM fact_turn
                   WHERE source = ? AND session_id = ? AND turn_id = ?""",
                (harness, r["session_id"], r["turn_id"]),
            )
        )
    return out


def _resolve_event(
    con: duckdb.DuckDBPyConnection, harness: str, span_id: str
) -> list[dict[str, Any]]:
    rel = con.execute(
        "SELECT * FROM fact_event WHERE source = ? AND span_id = ?", (harness, span_id)
    )
    rows = rel.fetchall()
    if not rows:
        return []
    cols = [c[0] for c in rel.description]
    results = []
    for row in rows:
        r = dict(zip(cols, row))
        chain: list[dict[str, Any]] = []
        cur = span_id
        seen: set[str] = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            e = con.execute(
                "SELECT span_id, parent_span_id, event_name, ts, status FROM fact_event WHERE span_id = ?",
                (cur,),
            ).fetchone()
            if e is None:
                chain.append(
                    {
                        "span_id": cur,
                        "parent_span_id": None,
                        "event_name": "(session anchor)",
                        "ts": None,
                        "status": None,
                        "is_root": True,
                    }
                )
                break
            chain.append(
                {
                    "span_id": e[0],
                    "parent_span_id": e[1],
                    "event_name": e[2],
                    "ts": e[3],
                    "status": e[4],
                    "is_root": False,
                }
            )
            cur = e[1]
        results.append({"row": r, "parent_span_chain": chain, "context": r.get("context")})
    return results


# --------------------------------------------------------------------------- #
# Doc-sweep mode (citations S3): find agent-trace: tokens in doc/** and resolve
# --------------------------------------------------------------------------- #

_S3_RE = re.compile(r"agent-trace:(\S+)#(?:turn|tool|req|event):([A-Za-z0-9_\-]+)")
_SCAN_SUFFIXES = {".md", ".markdown", ".rst", ".txt"}


def _collect(path: Path, hits: list[tuple[Path, int, str]]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # noqa: BLE001
        print(f"  skip {path}: {exc}", file=sys.stderr)
        return
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in _S3_RE.finditer(line):
            harness, anchor_id = m.groups()
            # validate the harness and anchor kind before emitting
            token = m.group(0)
            parsed = parse_token(token)
            if parsed is None:
                continue
            hits.append((path, line_no, token))


def sweep_tokens(root: Path) -> list[tuple[Path, int, str]]:
    """Walk a path and collect every agent-trace: token occurrence."""
    hits: list[tuple[Path, int, str]] = []
    if root.is_file():
        _collect(root, hits)
    else:
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() in _SCAN_SUFFIXES:
                _collect(path, hits)
    return hits


def _sweep_result(res: Resolution, path: Path, line: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "token": res.citation,
        "location": {"file": str(path), "line": line},
        "resolved": res.found,
        "kind": res.kind,
    }
    if res.found and res.row:
        out["source"] = res.row.get("source")
    if not res.found:
        ids = [
            nm.get(k)
            for nm in res.near_misses
            for k in ("turn_id", "tool_call_id", "request_id", "span_id")
            if nm.get(k)
        ][:3]
        out["near_misses"] = ids if ids else None
    return out


def sweep(con: duckdb.DuckDBPyConnection, root: Path, limit: int = 100000) -> dict[str, Any]:
    """Resolve every agent-trace: token found under `root`; returns a report."""
    hits = sweep_tokens(root)
    seen: dict[str, tuple[Path, int]] = {}
    for path, line, token in hits:
        seen.setdefault(token, (path, line))
    results = []
    for token, (path, line) in sorted(seen.items()):
        if len(results) >= limit:
            break
        res = resolve(con, token)
        results.append(_sweep_result(res, path, line))
    total = len(results)
    resolved = sum(1 for r in results if r["resolved"])
    return {
        "root": str(root),
        "tokens": total,
        "resolved": resolved,
        "unresolved": total - resolved,
        "results": results,
    }


def parse_token(citation: str) -> tuple[str, str, str, str] | None:
    m = _TOKEN_RE.match(citation)
    if not m:
        return None
    harness, anchor_type, anchor_id = m.groups()
    if harness not in HARNESSES:
        return None
    return harness, anchor_type, anchor_id, citation


def resolve(con: duckdb.DuckDBPyConnection, citation: str) -> Resolution:
    parsed = parse_token(citation)
    if parsed is None:
        return Resolution(
            citation=citation,
            found=False,
            near_misses=[
                {"hint": "expected form: agent-trace:<harness>#<kind>:<id>", "kind": "hint"}
            ],
        )
    harness, anchor_type, anchor_id, full = parsed
    res = Resolution(
        citation=full,
        harness=harness,
        kind=anchor_type,
        anchor_type=anchor_type,
        anchor_id=anchor_id,
    )

    if anchor_type == "turn":
        res.row = _row_to_dict(
            con.execute(
                "SELECT * FROM fact_turn WHERE source = ? AND turn_id = ?", (harness, anchor_id)
            )
        )
        res.context = _resolve_turn(con, harness, anchor_id) if res.row else {}
    elif anchor_type == "tool":
        res.row = _row_to_dict(
            con.execute(
                "SELECT * FROM fact_tool_call WHERE source = ? AND tool_call_id = ?",
                (harness, anchor_id),
            )
        )
        res.context = _resolve_tool(con, harness, anchor_id) if res.row else {}
    elif anchor_type == "req":
        res.row = _row_to_dict(
            con.execute(
                "SELECT * FROM fact_model_request WHERE source = ? AND request_id = ?",
                (harness, anchor_id),
            )
        )
        res.context = _resolve_req(con, harness, anchor_id) if res.row else {}
    else:  # event
        matches = _resolve_event(con, harness, anchor_id)
        if matches:
            res.row = matches[0]["row"]
            res.context = {
                "event_matches": len(matches),
                "rows": [
                    {"context": m["context"], "parent_span_chain": m["parent_span_chain"]}
                    for m in matches
                ],
            }
        else:
            res.row = None
            res.context = {}
    res.found = res.row is not None
    if not res.found:
        res.near_misses = _near_misses(con, harness, anchor_type)
    return res


def _fmt_timestamp(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v) if v is not None else "-"


def _fmt_event_context(v: str) -> dict | None:
    try:
        return json.loads(v) if v else None
    except TypeError, json.JSONDecodeError:
        return None


def render_text(res: Resolution) -> str:  # noqa: C901  # one render ladder per citation kind
    lines: list[str] = []
    if not res.found:
        lines.append(f"unresolved citation: {res.citation}")
        lines.append("")
        lines.append("near matches (most recent in this harness):")
        for nm in res.near_misses:
            if nm.get("kind") == "hint":
                lines.append(f"  hint: {nm['hint']}")
                continue
            fields = []
            if res.kind == "turn":
                fields = [
                    f"turn {nm['turn_id']} (session {nm['session_id']})",
                    f"ts {_fmt_timestamp(nm['ts'])}",
                    f"status {nm['status']}",
                    f"tokens {nm['tokens']}",
                ]
            elif res.kind == "tool":
                fields = [
                    f"tool {nm['tool_call_id']}",
                    f"name {nm['tool_name']}",
                    f"ts {_fmt_timestamp(nm['ts'])}",
                    f"exit {nm['exit_code']}",
                ]
            elif res.kind == "req":
                fields = [
                    f"request {nm['request_id']}",
                    f"model {nm['model']} ({nm['provider']})",
                    f"ts {_fmt_timestamp(nm['ts'])}",
                    f"status {nm['status']}",
                ]
            else:
                fields = [
                    f"event {nm['span_id']}",
                    f"name {nm['event_name']}",
                    f"ts {_fmt_timestamp(nm['ts'])}",
                ]
            lines.append(f"  {', '.join(fields)}")
        return "\n".join(lines)

    if res.row is None:
        raise RuntimeError("resolved Resolution is missing its row")
    s = _summarize_row(res.kind, res.row)
    lines.append(f"resolved: {res.citation} [{res.kind}]")
    lines.append(
        f"  source={s['source']}  "
        + "  ".join(
            f"{k}={_fmt_timestamp(v)}"
            for k, v in s.items()
            if k not in ("source",) and v is not None
        )
    )
    lines.append("")
    ctx = res.context or {}
    if res.kind == "turn":
        mr = ctx.get("model_requests") or []
        tc = ctx.get("tool_calls") or []
        lines.append(f"context: {len(mr)} model request(s), {len(tc)} tool call(s)")
        for m in mr:
            lines.append(
                f"  - req {m['request_id']}: {m['model']} ({m['provider']}) {m['status']} "
                f"ttft={m['ttft_ms']}ms dur={m['duration_ms']}ms in={m['input']} out={m['output']}"
            )
        for t in tc:
            lines.append(
                f"  - tool {t['tool_call_id']}: {t['tool_name']} {t['status']} exit={t['exit_code']}"
            )
    elif res.kind == "tool":
        pt = ctx.get("parent_turn")
        lines.append(
            f"context: parent turn {pt['turn_id']} ({pt['ts']}, {pt['status']})"
            if pt
            else "context: parent turn (unresolved)"
        )
        adj = ctx.get("adjacent_tool_calls") or []
        for a in adj:
            lines.append(f"  ~ {a['role']}: tool {a['tool_call_id']} ({a['tool_name']}) {a['ts']}")
    elif res.kind == "req":
        pt = ctx.get("parent_turn")
        lines.append(
            f"context: parent turn {pt['turn_id']} ({pt['ts']}, {pt['status']})"
            if pt
            else "context: parent turn (unresolved)"
        )
    elif res.kind == "event":
        em = ctx.get("event_matches") or 0
        if em > 1:
            lines.append(f"context: {em} events share this span_id (printing all)")
        for m in ctx.get("rows", []):
            chain = m["parent_span_chain"]
            hops = " -> ".join(
                f"{c['event_name']}{' (root)' if c.get('is_root') else ''}" for c in chain
            )
            lines.append(f"context: span chain: {hops}")
            ec = _fmt_event_context(m["context"])
            if ec:
                lines.append("  context json:")
                for k, v in ec.items():
                    lines.append(f"    {k}: {v}")
    return "\n".join(lines)


def _sweep_mode(args: argparse.Namespace) -> int:
    """Resolve every agent-trace: token found under the given PATH(s)."""
    roots = args.sweep if args.sweep else [REPO / "doc"]
    try:
        con = duckdb.connect(str(args.db))
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not open trace store: {exc}", file=sys.stderr)
        return 1
    try:
        results: list[dict[str, Any]] = []
        total = 0
        for root in roots:
            root = root if isinstance(root, Path) else Path(root)
            report = sweep(con, root, limit=args.limit)
            total += report["tokens"]
            results.extend(report["results"])
    finally:
        con.close()

    if args.as_json:
        payload: dict[str, Any] = {
            "mode": "sweep",
            "roots": [str(r) for r in roots],
            "tokens": total,
            "results": results,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0 if all(r["resolved"] for r in results) else 1

    lines = [
        f"# agent-trace: doc sweep of {', '.join(str(r) for r in roots)} "
        f"({total} tokens, {sum(1 for r in results if not r['resolved'])} unresolved)"
    ]
    for r in sorted(results, key=lambda x: (x["location"]["file"], x["location"]["line"])):
        marker = "[OK]" if r["resolved"] else "[FAIL]"
        if r["resolved"]:
            detail = f"  ({r.get('source')}/{r['kind']})"
        else:
            ids = r.get("near_misses")
            detail = f"  near matches: {', '.join(ids) if ids else 'none'}"
        lines.append(
            f"  {marker}  {r['token']}  {r['location']['file']}:{r['location']['line']}{detail}"
        )
    print("\n".join(lines))
    return 0 if all(r["resolved"] for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.strip().split("Exit codes:")[1].strip(),
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the full resolution (row + context) as JSON",
    )
    p.add_argument(
        "--db",
        default=str(DB_PATH),
        help="trace store path (default: memory/data/agent_traces.duckdb)",
    )
    p.add_argument("--limit", type=int, default=5, help="near-miss suggestions (default 5)")
    p.add_argument(
        "citation",
        nargs="?",
        default=None,
        help="agent-trace:<harness>#<kind>:<id> citation, e.g. agent-trace:prime#turn:816642a2",
    )
    p.add_argument(
        "--sweep",
        nargs="*",
        metavar="PATH",
        default=None,
        help="scan PATH(s) for agent-trace: tokens and resolve each (default: doc/)",
    )
    args = p.parse_args(argv)

    if args.sweep is not None:
        return _sweep_mode(args)

    if not Path(args.db).exists():
        print(f"error: trace store not found at {args.db}", file=sys.stderr)
        print(
            "build it by running: python3 bench_data/code/agent_traces.py load <harness>",
            file=sys.stderr,
        )
        print(
            "  (machine-local store; see helpers/misc/trace_quote.py for the citation grammar)",
            file=sys.stderr,
        )
        return 1

    try:
        con = duckdb.connect(args.db)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not open trace store: {exc}", file=sys.stderr)
        return 1
    try:
        if args.citation is None:
            print("error: provide a citation or --sweep <path>", file=sys.stderr)
            return 2
        res = resolve(con, args.citation)
    finally:
        con.close()

    if args.as_json:
        payload: dict[str, Any] = {
            "kind": res.kind,
            "citation": res.citation,
            "resolved": res.found,
            "row": res.row,
            "context": res.context,
        }
        if not res.found:
            payload["near_misses"] = [
                k for nm in res.near_misses for k, v in nm.items() if k != "kind"
            ]
            payload["hint"] = next(
                (nm["hint"] for nm in res.near_misses if nm.get("kind") == "hint"), None
            )
        print(json.dumps(payload, indent=2, default=str))
        return 0 if res.found else 1

    print(render_text(res))
    return 0 if res.found else 1


if __name__ == "__main__":
    sys.exit(main())
