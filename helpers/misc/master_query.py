#!/usr/bin/env python3
"""
master_query — one federated query over every search leg this repo keeps.

The agent-facing GRAND UNIFIED surface: a session asks once and gets
per-leg structured results instead of picking (and remembering) the right
index CLI first. Unification lives in the CLIENT — the settled #229
doctrine: fan out over the legs in parallel, render GROUPED with
per-leg ranking, and never blend the legs' score spaces (BM25,
RRF-fused, corpus pointers and report rows are incomparable). The one
cross-leg blend offered is rank-based (`--flat`): RRF over each leg's
hit RANKS, leg-tagged, as a "best overall" reading order.

Legs (default six, `--legs all` adds the stateless structure tools):

    docs    doc/ knowledge index (doc_query backend, hybrid)
    notes   findata vault (note_search, per-note hybrid)
    scripts helpers/tests/make/Mojo/TS intent index (script_query backend)
    memory  harness-memory pools zcode/prime/opencode (memory_query core)
    convo   harvested past-session corpus pointers (convo_query backend)
    gates   gate-run reports (the search_tui reports lane)
    maint   maintenance timing runs (maint_runs/phases: cmd/phase/summary match)
    code    ripwire --for= structure (stateless; --legs all)
    literal rg (stateless; --legs all)

Every leg degrades independently — a missing/stale sidecar surfaces as
that leg's status, never as a failure of the whole query (the house
"every check runs even if one fails" doctrine). Freshness REPORTING
lives here (per-leg status + index ages); the freshness GATES stay
where the contracts live: `make search-fresh` (docs/notes/scripts/
memory) and `make convo-fresh` (corpus chain); the gate index refreshes
itself before every query. `--age-guard [HOURS]` (default 24) turns
that reporting into enforcement for one call: index legs whose sidecar
is older than the threshold are skipped up front with the refresh
command in the status — stale hits never reach the caller.

Usage:
    python3 helpers/misc/master_query.py "embed cache"
    python3 helpers/misc/master_query.py "stg refresh" --legs memory,convo
    python3 helpers/misc/master_query.py "integrity" --flat --limit 4
    python3 helpers/misc/master_query.py "graph rebuild" --age-guard 12
    python3 helpers/misc/master_query.py "graph rebuild" --json

Exit codes: 0 at least one leg actually ran (hits or an honest "0 hits"),
1 no leg ran (every sidecar missing / every leg errored or
age-guard-skipped), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

# Repo root: helpers/misc/master_query.py -> parents[2]. Must be on
# sys.path BEFORE the search_tui import (house bootstrap) so the script
# works as a subprocess the same way it works under pytest.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.misc import search_tui as st  # noqa: E402
from helpers.misc.search_tui import Hit  # noqa: E402

RRF_K = 60

# Master leg -> search_tui lane backend. "gates" is the TUI reports lane
# under its corpus-facing name; both names are accepted in --legs.
_LEGS: dict[str, str] = {
    "docs": "docs",
    "notes": "notes",
    "scripts": "scripts",
    "memory": "memory",
    "convo": "convo",
    "gates": "reports",
    "reports": "reports",  # alias
    "maint": "maint",
    "code": "code",
    "literal": "literal",
}
DEFAULT_LEGS = ("docs", "notes", "scripts", "memory", "convo", "gates")
ALL_LEGS = ("docs", "notes", "scripts", "memory", "convo", "gates", "maint", "code", "literal")

# Age-guard scope (S2): legs backed by a dedicated sidecar file whose
# mtime is the index age. notes' file is the SHARED research.db — many
# subsystems touch it, so its age reads fresh (the guard is
# approximate there; documented, not load-bearing). gates is exempt
# (self-refreshing before every query verb), code/literal stateless.
_SIDECAR_BY_LEG: dict[str, str] = {
    "docs": "memory/doc_search.db",
    "scripts": "memory/script_search.db",
    "notes": "memory/research.db",
    "memory": "memory/memory_search.db",
    "convo": "memory/convo_search.duckdb",
}
_REFRESH_BY_LEG: dict[str, str] = {
    "docs": "make search-fresh APPLY=1",
    "scripts": "make search-fresh APPLY=1",
    "notes": "make search-fresh APPLY=1",
    "memory": "make search-fresh APPLY=1",
    "convo": "make convo-fresh APPLY=1",
}
DEFAULT_AGE_GUARD_HOURS = 24.0

_ANSWERED = re.compile(r"^\d+ hits")


def _leg_age_hours(leg: str, root: Path | None = None) -> float | None:
    """Hours since the leg's sidecar was last written, or None when the
    leg has no guarded sidecar or the file is missing (missing lets the
    leg degrade with its own status instead of an age skip)."""
    rel = _SIDECAR_BY_LEG.get(leg)
    if rel is None:
        return None
    f = (root or _REPO_ROOT) / rel
    try:
        return max((time.time() - f.stat().st_mtime) / 3600.0, 0.0)
    except OSError:
        return None


def parse_legs(spec: str) -> list[str]:
    """--legs value -> deduped leg list (caller order preserved).

    'all' expands to every leg incl. the stateless code/literal tools.
    Unknown names raise ValueError (usage error, exit 2)."""
    names = [n.strip().lower() for n in spec.split(",") if n.strip()]
    if names == ["all"]:
        return list(ALL_LEGS)
    legs: list[str] = []
    for n in names:
        if n not in _LEGS:
            raise ValueError(
                f"unknown leg {n!r} (known: {', '.join(DEFAULT_LEGS)}, maint, code, literal, all)"
            )
        if n not in legs:
            legs.append(n)
    return legs


def _run_maint(query: str, limit: int) -> tuple[list, str]:
    """The maint leg: match the query against the maintenance timing
    corpus (maint_runs + maint_phases) and return recent matching runs
    as Hits. Same-table backend as maint_query, in-process (no
    subprocess): tokens AND-match across cmd/mode/target/summary/phase,
    newest first. maint is self-writing — no refresh step exists, so the
    leg is age-guard exempt like gates."""
    import duckdb

    db = _REPO_ROOT / "outputs" / "gate_runs.duckdb"
    con = None
    try:
        con = duckdb.connect(str(db), read_only=True)
    except Exception as exc:
        return [], f"error: cannot open timing DB ({exc})"
    try:
        names = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        if {"maint_runs", "maint_phases"} - names:
            return [], "0 hits (no maint runs recorded yet)"
        tokens = [t.lower() for t in query.split() if len(t) >= 2]
        scores: dict = {}
        for t in tokens:
            like = f"%{t}%"
            run_ids = {
                r[0]
                for r in con.execute(
                    "SELECT maint_run_id FROM maint_runs WHERE LOWER(cmd) LIKE ?"
                    " OR LOWER(mode) LIKE ? OR LOWER(target) LIKE ? OR LOWER(summary) LIKE ?",
                    [like] * 4,
                ).fetchall()
            }
            phase_ids = {
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT maint_run_id FROM maint_phases"
                    " WHERE LOWER(phase) LIKE ? OR LOWER(extra) LIKE ?",
                    [like, like],
                ).fetchall()
            }
            for rid in run_ids | phase_ids:
                scores[rid] = scores.get(rid, 0) + 1
        # scored OR: runs matching more tokens first, then newest.
        ranked = sorted(
            scores,
            key=lambda rid: (-scores[rid], -rid),
        )
        want = ranked if tokens else None
        q = "SELECT maint_run_id, cmd, mode, target, started_at, elapsed_s, exit_code, summary FROM maint_runs"
        if want is not None:
            if not want:
                return [], "0 hits"
            by_id = {
                r[0]: r
                for r in con.execute(
                    f"{q} WHERE maint_run_id IN ({','.join('?' * len(want))})", list(want)
                ).fetchall()
            }
            rows = [by_id[rid] for rid in want if rid in by_id][:limit]
        else:
            rows = con.execute(q + " ORDER BY started_at DESC LIMIT ?", [limit]).fetchall()
        hits = []
        for i, (rid, cmd, mode, target, started, elapsed, code, summary) in enumerate(rows):
            phases = con.execute(
                "SELECT phase, elapsed_s FROM maint_phases WHERE maint_run_id = ? ORDER BY started_at",
                [rid],
            ).fetchall()
            seg = " · ".join(f"{p} {s:.1f}s" for p, s in phases[:6])
            tgt = f" [{target}]" if target else ""
            hits.append(
                Hit(
                    path=f"maint:{rid}",
                    line=None,
                    title=f"{cmd} {mode}{tgt} {elapsed:.1f}s exit {code}",
                    snippet=f"{seg} — {(summary or '')[:100]}",
                    score=1.0 / (1 + i),
                    kind="maint",
                )
            )
        return hits, f"{len(hits)} hits"
    except Exception as exc:  # noqa: BLE001  # one leg must not fail the fan-out
        return [], f"error: {type(exc).__name__}: {exc}"
    finally:
        if con is not None:
            con.close()


def _leg_priority(leg: str) -> int:
    """Tie-break priority for the flat order (canonical leg order; the
    reports alias ranks with gates)."""
    canonical = "gates" if leg == "reports" else leg
    return DEFAULT_LEGS.index(canonical) if canonical in DEFAULT_LEGS else len(DEFAULT_LEGS)


def fan_out(
    query: str,
    legs: list[str],
    limit: int,
    mode: str = "hybrid",
    lane_runner=None,
    parallel: bool = True,
    age_guard_hours: float | None = None,
) -> dict[str, tuple[list, str]]:
    """Run every leg, return {leg: (hits, status)} keyed by caller's name.

    ``lane_runner`` defaults to search_tui.run_lane (the terminal-free,
    unit-tested adapters — the master CLI owns NO backend logic). Legs
    run in a thread pool by default: the corpus legs mix subprocess CLIs
    and in-process embedders, so wall-clock is the slowest leg, not the
    sum. A raising leg degrades to ([], "error: ...").

    ``age_guard_hours`` (S2, `--age-guard`): legs whose guarded sidecar
    (``_SIDECAR_BY_LEG``) is older than the threshold are skipped up
    front — status names the age and the refresh command; the backend
    never runs. A skipped leg is not "answered" (exit-1 eligible).

    Shared query vector (shared_query_vector proposal): hybrid mode
    embeds the query ONCE here and fans the vector out to every leg
    (``query_vec`` runner kwarg) instead of each leg loading the GGUF.
    bm25 mode embeds nothing (as before — no vector is needed)."""
    from helpers.maintenance import rebuild_common as rbc

    runner = lane_runner or st.run_lane
    ordered: list[str] = []
    for leg in legs:
        if leg not in ordered:
            ordered.append(leg)
    shared_vec = rbc.make_query_vector(query) if mode != "bm25" and query.strip() else None

    def _one(leg: str) -> tuple[str, tuple[list, str]]:
        if age_guard_hours is not None:
            age = _leg_age_hours(leg)
            if age is not None and age > age_guard_hours:
                return leg, (
                    [],
                    f"skipped: index {age:.1f}h old exceeds the {age_guard_hours:g}h "
                    f"age guard (refresh: {_REFRESH_BY_LEG.get(leg, 'make search-fresh APPLY=1')})",
                )
        try:
            if leg == "maint":
                # timing-corpus leg: local backend, not a search_tui lane
                # (no shared vector needed — token match, no embedding).
                hits, status = _run_maint(query, limit)
                return leg, (hits, status)
            hits, status = runner(_LEGS[leg], query, limit, mode, query_vec=shared_vec)
            return leg, (hits, status)
        except Exception as exc:  # noqa: BLE001  # one leg must not fail the fan-out
            return leg, ([], f"error: {type(exc).__name__}: {exc}")

    out: dict[str, tuple[list, str]] = {}
    if parallel and len(ordered) > 1:
        with ThreadPoolExecutor(max_workers=len(ordered)) as pool:
            for leg, result in pool.map(_one, ordered):
                out[leg] = result
    else:
        for leg in ordered:
            leg, result = _one(leg)
            out[leg] = result
    return out


def rrf_flat(per_leg: dict[str, tuple[list, str]], limit: int) -> list[dict]:
    """Cross-leg reading order: RRF over each leg's hit RANKS.

    Rank-based, not score-based — the legs' score spaces (bm25, fused
    RRF, similarity) are incomparable, but "how highly did this rank in
    its own leg" is not. Ties break by canonical leg order (repo
    surfaces before harness surfaces before stateless tools)."""
    scored: list[tuple[float, int, str, Hit]] = []
    for leg, (hits, _status) in per_leg.items():
        pri = _leg_priority(leg)
        for rank, hit in enumerate(hits):
            scored.append((1.0 / (RRF_K + rank + 1), pri, leg, hit))
    scored.sort(key=lambda t: (-t[0], t[1]))
    flat: list[dict] = []
    for score, _pri, leg, hit in scored[:limit]:
        entry = asdict(hit)
        entry["leg"] = leg
        entry["rrf"] = round(score, 6)
        flat.append(entry)
    return flat


def _leg_answered(status: str) -> bool:
    """True when the leg actually ran (hit count line, incl. '0 hits')
    rather than degrading (missing sidecar / error / empty query)."""
    return _ANSWERED.match(status) is not None


def render(per_leg: dict[str, tuple[list, str]], ages: str) -> None:
    """Grouped default: one section per leg, status line, then hits."""
    print(f"# master search — index ages: {ages}")
    for leg, (hits, status) in per_leg.items():
        print(f"\n## {leg}  ({status})")
        if not hits:
            print("    (no hits)")
            continue
        for h in hits:
            loc = h.path if h.line is None else f"{h.path}:{h.line}"
            head = f" [{h.score:.4f}]" if isinstance(h.score, (int, float)) else ""
            title = f" {h.title}" if h.title else ""
            kind = f" {h.kind}" if h.kind else ""
            print(f"  {loc}{head}{kind}{title}"[:180])
            if h.snippet:
                print(f"      {h.snippet[:140]}")


def render_flat(flat: list[dict]) -> None:
    """--flat: one interleave, each row leg-tagged."""
    for r in flat:
        loc = r["path"] if r.get("line") is None else f"{r['path']}:{r['line']}"
        print(f"[{r['leg']:>7}] {loc}  [{r['rrf']:.6f}]")
        if r.get("snippet"):
            print(f"           {r['snippet'][:140]}")


def _import_dotenv() -> None:
    import dotenv  # noqa: F401  (sentinel import — see interpreter_problem)


def interpreter_problem() -> str | None:
    """None when THIS interpreter can run the master fan-out; else one
    actionable error line.

    Sentinel = python-dotenv: helpers.core.env imports it at module load,
    so every in-process leg (notes, memory) dies without it and the
    subprocess legs inherit the same broken env — the symptom is a
    confusing six-leg error cascade. Happens when the CLI is invoked
    with a bare ``python`` that resolves outside the repo .venv (e.g.
    ~/.local/bin/python 3.14) — the AGENTS.md ``.venv/bin/python3``
    rule. Checked up front so the front door fails LOUD and instructive
    instead."""
    try:
        _import_dotenv()
    except ModuleNotFoundError:
        return (
            f"ERROR: {sys.executable} is not the repo venv — python-dotenv is "
            "missing, so every search leg would fail. Run:\n"
            "  .venv/bin/python3 helpers/misc/master_query.py '<query>'"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("query", nargs="?", default="", help="free-text query")
    p.add_argument(
        "--legs",
        default=",".join(DEFAULT_LEGS),
        help=f"comma list: {', '.join(DEFAULT_LEGS)}, code, literal, all "
        "(default: the six corpus legs)",
    )
    p.add_argument("--limit", type=int, default=4, help="hits per leg (default 4)")
    p.add_argument(
        "--bm25", action="store_true", help="lexical leg only (skip cosine in hybrid legs)"
    )
    p.add_argument(
        "--flat", action="store_true", help="also emit a rank-RRF cross-leg reading order"
    )
    p.add_argument(
        "--age-guard",
        nargs="?",
        type=float,
        const=DEFAULT_AGE_GUARD_HOURS,
        default=None,
        metavar="HOURS",
        help="skip index legs whose sidecar is older than HOURS "
        f"(default {DEFAULT_AGE_GUARD_HOURS:g}); gates/code/literal exempt",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="structured output (adds flat when --flat)",
    )
    p.add_argument("--serial", action="store_true", help="run legs sequentially (debug)")
    args = p.parse_args(argv)

    problem = interpreter_problem()
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2

    try:
        legs = parse_legs(args.legs)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    mode = "bm25" if args.bm25 else "hybrid"
    per_leg = fan_out(
        args.query,
        legs,
        max(1, min(args.limit, 20)),
        mode,
        parallel=not args.serial,
        age_guard_hours=args.age_guard,
    )
    flat = rrf_flat(per_leg, args.limit) if args.flat else []
    answered = any(_leg_answered(status) for _h, status in per_leg.values())

    if args.as_json:
        payload = {
            "query": args.query,
            "mode": mode,
            "age_guard_hours": args.age_guard,
            "index_ages": st.index_ages(_REPO_ROOT),
            "legs": {
                leg: {
                    "status": status,
                    "skipped": status.startswith("skipped:"),
                    "count": len(hits),
                    "results": [asdict(h) for h in hits],
                }
                for leg, (hits, status) in per_leg.items()
            },
        }
        if args.flat:
            payload["flat"] = flat
        print(json.dumps(payload, indent=2))
        return 0 if answered else 1

    render(per_leg, st.index_ages(_REPO_ROOT))
    if args.flat and flat:
        print("\n## flat (rank-RRF reading order)")
        render_flat(flat)
    if not any(hits for hits, _s in per_leg.values()):
        print(f"\n(no hits anywhere for {args.query!r})", file=sys.stderr)
    return 0 if answered else 1


if __name__ == "__main__":
    sys.exit(main())
