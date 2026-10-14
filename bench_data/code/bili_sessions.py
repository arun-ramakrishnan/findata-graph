#!/usr/bin/env python3
"""Discover and aggregate billion-context sessions from the live proxy dashboard
and local logs.

Usage:
    python bench_data/code/bili_sessions.py
    python bench_data/code/bili_sessions.py --json
    python bench_data/code/bili_sessions.py --detail SID
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bili_analytics import parse_log, parse_report  # noqa: E402

RE_SES = re.compile(r"ses_[A-Za-z0-9]+")
LIVE_LOG = Path.home() / ".local" / "state" / "billion-context" / "bili.log"
ROOTS = [
    Path.home() / ".local" / "state" / "billion-context",
    Path(__file__).resolve().parents[2] / "bench_data" / "billion-context",
]
DASHBOARD_URLS = ["http://127.0.0.1:18787/__bili"]


def _dashboard_urls() -> list[str]:
    urls = ["http://127.0.0.1:18787/__bili"]
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            cmd = (pid_dir / "cmdline").read_text(errors="replace").replace("\x00", " ")
            if "billion-context" not in cmd:
                continue
            m = re.search(r"--port\s+(\d+)", cmd)
            if m:
                urls.append(f"http://127.0.0.1:{m.group(1)}/__bili")
        except OSError:
            continue
    return sorted(set(urls))


def dashboard_sessions() -> list[dict]:
    collected: list[dict] = []
    for base in _dashboard_urls():
        try:
            with urllib.request.urlopen(f"{base}/sessions", timeout=2) as r:
                collected.extend(json.load(r).get("sessions", []))
        except Exception:
            continue
    # de-dup by id, keeping the entry with the newest lastSeen (different proxies
    # can expose the same session id with conflicting stale state)
    by_id: dict[str, dict] = {}
    for s in collected:
        sid = s.get("id")
        if sid is None:
            continue
        prev = by_id.get(sid)
        if prev is None or str(s.get("lastSeen", "")) > str(prev.get("lastSeen", "")):
            by_id[sid] = s
    return list(by_id.values())


def dashboard_detail(sid: str) -> dict:
    best = None
    for base in _dashboard_urls():
        try:
            with urllib.request.urlopen(f"{base}/sessions/{sid}/detail", timeout=2) as r:
                d = json.load(r)
            if best is None or str(d.get("lastSeen", "")) > str(best.get("lastSeen", "")):
                best = d
        except Exception:
            continue
    return best or {}


def dashboard_stats_map() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for base in _dashboard_urls():
        try:
            with urllib.request.urlopen(f"{base}/stats", timeout=2) as r:
                data = json.load(r)
            for s in data.get("sessions", []):
                sid = s.get("id")
                if sid is None:
                    continue
                prev = out.get(sid)
                if prev is None or str(s.get("lastSeen", "")) > str(prev.get("lastSeen", "")):
                    out[sid] = s
        except Exception:
            continue
    return out


def find_report(sid: str) -> Optional[Path]:
    for root in ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.is_file() and "cacherpt" in p.name and p.name.endswith(".md") and sid in p.name:
                return p
    return None


def file_logs_for_sid(sid: str) -> list[Path]:
    out: list[Path] = []
    for root in ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            name = p.name
            if name == "bili.log" or "billion-context-log" in name or "billion-context-full-log" in name:
                out.append(p)
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            cmd = (pid_dir / "cmdline").read_text(errors="replace").replace("\x00", " ")
            if "billion-context" not in cmd:
                continue
            lp = (pid_dir / "cwd").resolve() / "bili.log"
            if lp.is_file():
                out.append(lp)
        except OSError:
            continue
    return sorted(set(out))


def run_for_sid(sid: str) -> dict:
    d = dashboard_detail(sid)
    st = dashboard_stats_map().get(sid, {})
    report = find_report(sid)

    candidates = [p for p in file_logs_for_sid(sid)]
    containing = [p for p in candidates if sid in p.read_text(errors="replace")]
    if not containing and LIVE_LOG.is_file():
        containing = [LIVE_LOG]
    log_path = max(containing, key=lambda p: p.stat().st_size) if containing else None

    switches = (
        st.get("modelSwitches", 0)
        + st.get("keySwitches", 0)
        + st.get("promptSwitches", 0)
        + st.get("projSwitches", 0)
    )

    row = {
        "session": sid,
        "title": d.get("title") or st.get("title"),
        "client": d.get("clientHint") or st.get("clientHint", ""),
        "protocol": d.get("protocol") or st.get("protocol", ""),
        "upstream": d.get("upstreamOrigin") or st.get("upstreamOrigin") or st.get("upstream", ""),
        "requests": d.get("requests", st.get("requests", 0)),
        "context_now": d.get("contextTokens", st.get("contextTokens", 0)),
        "cum_input": d.get("inputTokens", st.get("inputTokens", 0)),
        "cached_input": d.get("cachedTokens", st.get("cachedTokens", 0)),
        "saved": d.get("tokensSaved", st.get("tokensSaved", 0)),
        "hit_pct": d.get("cacheHitPct", st.get("cacheHitPct", 0)),
        "blocks": d.get("blocks", st.get("blocks", 0)),
        "switches": switches,
        "context_window": d.get("contextWindow", st.get("contextWindow", 0)),
        "new_content": d.get("newContent", 0),
        "comp_repay": d.get("compRepay", 0),
        "ttl_repay": d.get("ttlRepay", 0),
        "last_seen": d.get("lastSeen", st.get("lastSeen", "")),
        "model": d.get("lastModel") or st.get("lastModel", ""),
        "log": str(log_path) if log_path else None,
        "report": str(report) if report else None,
        "source": "dashboard",
    }

    if log_path is not None and sid in log_path.read_text(errors="replace"):
        a = parse_log(log_path, session_filter=sid)
        if report:
            parse_report(report)
        last_nudge = a.nudges[-1] if a.nudges else None
        if not row.get("client"):
            row["client"] = "log"
        if not row.get("protocol"):
            row["protocol"] = "?"
        if not row.get("upstream"):
            row["upstream"] = "?"
        row.update({
            "requests": len(a.usages) or row["requests"],
            "log_hit_pct": a.overall_hit_pct(),
            "hit_pct": row.get("hit_pct") or a.overall_hit_pct(),
            "folds": len(a.folds),
            "folds_per_100k": a.folds_per_100k(),
            "intervals": dict(a.intervals_seen),
            "nudges": len(a.nudges),
            "t1_fired": sum(1 for n in a.nudges if n.fired_t1),
            "warnings": sorted(set(w.kind for w in a.warnings)),
            "reason_pair_violations": a.reasoning_pair_violations,
            "degenerate_folds": sum(1 for w in a.warnings if w.kind == "degenerate-fold"),
            "effective_interval": a.effective_interval(),
            "cum_input": row.get("cum_input") or sum(u.input_tok for u in a.usages),
            "cached_input": row.get("cached_input") or sum(u.cached_tok for u in a.usages),
            "context_now": row.get("context_now") or (last_nudge.usage_tok if last_nudge else 0),
            "model": row.get("model") or (last_nudge.model if last_nudge else row.get("model", "")),
            "saved": row.get("saved") or sum(f.saved_tok for f in a.folds),
            "source": ("log+dashboard" if row.get("source") == "dashboard" else "log"),
        })
    return row


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Discover and aggregate billion-context sessions.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--detail", metavar="SID")
    ap.add_argument("--aggregate", action="store_true", help="print cross-session totals after the table")
    ap.add_argument("--markdown", metavar="PATH", help="write a Markdown ledger to PATH")
    args = ap.parse_args(argv)

    meta_sessions = dashboard_sessions()
    if meta_sessions:
        rows = [run_for_sid(m["id"]) for m in meta_sessions]
    else:
        sids = sorted(set(re.findall(r"sess=([^\]]+)", Path(LIVE_LOG).read_text(errors="replace"))))
        rows = [run_for_sid(s) for s in sids]

    rows.sort(key=lambda r: r.get("last_seen") or "", reverse=True)

    if args.detail:
        row = next((r for r in rows if args.detail in str(r["session"])), None)
        if not row:
            print(f"session not found: {args.detail}", file=sys.stderr)
            return 2
        log = row.get("log")
        report = row.get("report")
        if not log:
            print("no log available for that session", file=sys.stderr)
            return 2
        cmd = [sys.executable, str(HERE / "bili_analytics.py"), log]
        if report:
            cmd.append(report)
        cmd += ["--session", row["session"]]
        raise SystemExit(subprocess.call(cmd))

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
        return 0

    print(f"{'session':<22} {'client':<9} {'proto':<10} {'upstream':<22} {'reqs':>5} "
          f"{'ctxNow':>8} {'cumInput':>10} {'saved':>8} {'hit%':>5} {'sw':>3} "
          f"{'blk':>3} {'model':<16} {'lastSeen':<19}")
    for r in rows:
        ls = (r.get("last_seen") or "").replace("T", " ").replace("Z", "")
        try:
            from datetime import datetime, timezone
            lsdt = datetime.fromisoformat(ls)
            if lsdt.tzinfo is None:
                lsdt = lsdt.replace(tzinfo=timezone.utc)
            ls = lsdt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ls = (r.get("last_seen") or "")[:19].replace("T", " ")
        print(f"{str(r['session'])[:22]:<22} {str(r.get('client',''))[:9]:<9} "
              f"{str(r.get('protocol',''))[:10]:<10} {str(r.get('upstream',''))[:22]:<22} "
              f"{r.get('requests',0):>5} {r.get('context_now',0):>8} "
              f"{r.get('cum_input',0):>10} {r.get('saved',0):>8} "
              f"{(r.get('hit_pct') or 0):>5.1f} {r.get('switches',0):>3} "
              f"{r.get('blocks',0):>3} {str(r.get('model',''))[:16]:<16} {ls:<19}")

    # aggregate across sessions
    total_reqs = sum(r.get("requests", 0) for r in rows)
    total_input = sum(r.get("cum_input", 0) for r in rows)
    total_cached = sum(r.get("cached_input", 0) for r in rows)
    total_saved = sum(r.get("saved", 0) for r in rows)
    total_ctx = sum(r.get("context_now", 0) for r in rows)
    agg_hit = (100.0 * total_cached / total_input) if total_input else 0.0
    agg_text = (
        f"AGGREGATE: sessions={len(rows)} requests={total_reqs} "
        f"cumInput={total_input} cached={total_cached} hit={agg_hit:.2f}% "
        f"contextNow={total_ctx} saved={total_saved} blocks={sum(r.get('blocks',0) for r in rows)}"
    )

    if args.aggregate or args.markdown:
        print()
        print(agg_text)

    if args.markdown:
        path = Path(args.markdown)
        lines = ["# billion-context session ledger", "", agg_text, "",
                 "| session | client | proto | upstream | reqs | ctxNow | cumInput | saved | hit% | sw | blk | model | lastSeen |",
                 "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
        for r in rows:
            ls = (r.get("last_seen") or "").replace("T", " ").replace("Z", "")
            try:
                from datetime import datetime, timezone
                lsdt = datetime.fromisoformat(ls)
                if lsdt.tzinfo is None:
                    lsdt = lsdt.replace(tzinfo=timezone.utc)
                ls = lsdt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ls = (r.get("last_seen") or "")[:19].replace("T", " ")
            lines.append(
                f"| {r.get('session','')} | {r.get('client','')} | {r.get('protocol','')} | "
                f"{r.get('upstream','')} | {r.get('requests',0)} | {r.get('context_now',0)} | "
                f"{r.get('cum_input',0)} | {r.get('saved',0)} | {(r.get('hit_pct') or 0):.1f} | "
                f"{r.get('switches',0)} | {r.get('blocks',0)} | {r.get('model','')} | {ls} |"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
