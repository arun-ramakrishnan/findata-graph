#!/usr/bin/env python3
"""Derive the R3 relational-insight tables (graph_relational_depth R3).

Four lanes, each a first-class research.db table, fully derived from the
cured graph (the R6a subsidiary cure and R6b participation semantics
landed BEFORE this — the closure stands on the final ownership semantic):

* ``ultimate_parent`` — ownership closure: every company's control apex
  over trusted subsidiary_of (strict vocabulary) ∪ reversed ``acquired``
  — metapath's ownership adjacency via ``ownership_parents()`` (one
  trust filter, no drift with ``connect(semantics='ownership')``).
  Promoter-coalition companies (no trusted parent — the I2 doctrine:
  Indian listed control is coalitions, not single majorities) are their
  own roots, annotated with the promoter-group aggregate stake where the
  shareholding sync provides it.
* ``hidden_connections`` — company pairs at structural distance exactly
  2 (ownership/flow/joint/same_group; membership + attention families
  excluded by construction) with NO direct structural edge and NO
  co_mentioned_in edge — structurally close, never discussed together.
  Corroboration floor ≥2 shared intermediates (measured 2026-10-25:
  93,647 pairs pass the filters at ≥1 shared intermediate, 1,893 at ≥2 —
  the floor is the signal).
* ``supply_chokepoints`` — suppliers serving ≥3 mutually competing
  companies (competitor dyads inside the supplier's customer set).
* ``competitive_overlap`` — triangles in ``competes_with``.

Each lane is idempotent: apply DELETEs + re-INSERTs the whole table in
one transaction (the tables are wholly derived — no manual rows). The
parity pin is a content digest (sha256 over the rows MINUS computed_at),
printed per lane; tests pin counts on fixtures, the proposal records the
live values.

Usage:
    .venv/bin/python3 helpers/graph/derive_relational_insights.py            # dry-run, all lanes
    .venv/bin/python3 helpers/graph/derive_relational_insights.py --apply    # write
    .venv/bin/python3 helpers/graph/derive_relational_insights.py --lane ultimate-parent -v
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.db import DEFAULT_DB_PATH, connect as _db_connect  # noqa: E402
from helpers.graph.metapath import ownership_parents  # noqa: E402

LANES = (
    "ultimate-parent",
    "hidden-connections",
    "supply-chokepoints",
    "competitive-overlap",
)

# hidden_connections: corroboration floor + hub cap. Measured 2026-10-25:
# >=1 shared intermediate leaves 93,647 pairs (mostly single-hub noise);
# >=2 collapses to 1,893 — corroboration is the signal. Intermediates
# with structural degree > 50 are hubs ("everyone shares the giant").
HIDDEN_MIN_SHARED = 2
HIDDEN_MAX_INTER_DEGREE = 50

# supply_chokepoints: the slice's "suppliers shared by >=3 competitors" —
# >=3 customers of the supplier that compete with each other.
CHOKE_MIN_COMPETITORS = 3

# Promoter-group aggregate rows on the invested_in store (structural
# shareholding-page rows, R6b category holders) carry the coalition's
# combined stake — the I2 annotation for self-root companies.
_PROMOTER_AGG_RE = re.compile(r"^total shareholding of promoter|^sub-total \(a\)\(1\)", re.I)

_DDL = [
    """CREATE TABLE IF NOT EXISTS ultimate_parent (
        company TEXT PRIMARY KEY,
        ultimate_parent TEXT NOT NULL,
        depth INTEGER NOT NULL,
        path TEXT NOT NULL,
        root_kind TEXT NOT NULL,
        coalition_pct REAL,
        computed_at DATETIME NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS hidden_connections (
        a TEXT NOT NULL,
        b TEXT NOT NULL,
        distance INTEGER NOT NULL,
        shared_count INTEGER NOT NULL,
        intermediaries TEXT NOT NULL,
        computed_at DATETIME NOT NULL,
        PRIMARY KEY (a, b))""",
    """CREATE TABLE IF NOT EXISTS supply_chokepoints (
        supplier TEXT PRIMARY KEY,
        competitors_served INTEGER NOT NULL,
        competitor_dyads INTEGER NOT NULL,
        companies TEXT NOT NULL,
        computed_at DATETIME NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS competitive_overlap (
        a TEXT NOT NULL,
        b TEXT NOT NULL,
        c TEXT NOT NULL,
        computed_at DATETIME NOT NULL,
        PRIMARY KEY (a, b, c))""",
]


def ensure_tables(conn: sqlite3.Connection) -> None:
    for ddl in _DDL:
        conn.execute(ddl)


# --------------------------------------------------------------------------- #
# lane: ultimate_parent
# --------------------------------------------------------------------------- #
def _closure(subject: str, parents: dict[str, list[tuple[str, str]]]) -> tuple[list[str], str]:
    """Walk child→parent to the apex.

    Min-(name, edge_type) claimant per hop — the metapath BFS convention,
    so a multi-parent child (name-variant parents, R6a C3 census) resolves
    deterministically. A revisit cuts the walk (cycle guard): the 2-cycle
    mutual-claim pairs close at the second node.
    """
    path = [subject]
    seen = {subject}
    cur = subject
    while True:
        outs = parents.get(cur)
        if not outs:
            return path, ("chain-root" if len(path) > 1 else "self-root")
        nxt = min(outs)[0]
        if nxt in seen:
            return path, "cycle-guard"
        path.append(nxt)
        seen.add(nxt)
        cur = nxt


def derive_ultimate_parent(
    conn: sqlite3.Connection, *, now: str, db_path: str | Path | None = None
) -> list[tuple]:
    parents = ownership_parents(db_path or DEFAULT_DB_PATH)
    coalition: dict[str, float] = {}
    for holder, target, props_json in conn.execute(
        "SELECT source, target, properties FROM graph_edges WHERE edge_type='invested_in'"
    ).fetchall():
        if not _PROMOTER_AGG_RE.match(holder or ""):
            continue
        try:
            v = float(jsonio.loads(props_json or "{}").get("stake_pct"))
        except TypeError, ValueError:
            continue
        if target not in coalition or v > coalition[target]:
            coalition[target] = v
    rows = []
    for (company,) in conn.execute(
        "SELECT name FROM entities WHERE entity_type='company' ORDER BY name"
    ).fetchall():
        path, kind = _closure(company, parents)
        rows.append(
            (
                company,
                path[-1],
                len(path) - 1,
                json.dumps(path),
                kind,
                coalition.get(company) if kind == "self-root" else None,
                now,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# lane: hidden_connections
# --------------------------------------------------------------------------- #
def derive_hidden_connections(
    conn: sqlite3.Connection, *, now: str, db_path: str | Path | None = None
) -> list[tuple]:
    companies = {
        r[0]
        for r in conn.execute("SELECT name FROM entities WHERE entity_type='company'").fetchall()
    }
    # structural proximity: ownership + flow + joint + group. Membership
    # (index/sector/country) and attention families are excluded — an
    # index would connect its every constituent at distance 2.
    struct: dict[str, set[str]] = defaultdict(set)
    for s, t in conn.execute(
        "SELECT source, target FROM graph_edges "
        "WHERE edge_type IN ('subsidiary_of','supplier_to','jv_with','same_group')"
    ).fetchall():
        struct[s].add(t)
        struct[t].add(s)
    comention: dict[str, set[str]] = defaultdict(set)
    for s, t in conn.execute(
        "SELECT source, target FROM graph_edges WHERE edge_type='co_mentioned_in'"
    ).fetchall():
        comention[s].add(t)
        comention[t].add(s)

    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    for mid, nbrs in struct.items():
        if len(nbrs) > HIDDEN_MAX_INTER_DEGREE or len(nbrs) < 2:
            continue
        ordered = sorted(nbrs)
        for i in range(len(ordered)):
            a = ordered[i]
            if a not in companies:
                continue
            for j in range(i + 1, len(ordered)):
                b = ordered[j]
                if b not in companies or b in struct[a] or b in comention[a]:
                    continue
                pairs[(a, b)].add(mid)

    return [
        (a, b, 2, len(mids), json.dumps(sorted(mids)), now)
        for (a, b), mids in sorted(pairs.items())
        if len(mids) >= HIDDEN_MIN_SHARED
    ]


# --------------------------------------------------------------------------- #
# lane: supply_chokepoints
# --------------------------------------------------------------------------- #
def derive_supply_chokepoints(
    conn: sqlite3.Connection, *, now: str, db_path: str | Path | None = None
) -> list[tuple]:
    custs: dict[str, set[str]] = defaultdict(set)
    for s, t in conn.execute(
        "SELECT source, target FROM graph_edges WHERE edge_type='supplier_to'"
    ).fetchall():
        custs[s].add(t)
    comp: dict[str, set[str]] = defaultdict(set)
    for s, t in conn.execute(
        "SELECT source, target FROM graph_edges WHERE edge_type='competes_with'"
    ).fetchall():
        comp[s].add(t)
        comp[t].add(s)

    rows = []
    for supplier, served_all in sorted(custs.items()):
        served = {c for c in served_all if comp.get(c) and (comp[c] & served_all)}
        if len(served) < CHOKE_MIN_COMPETITORS:
            continue
        dyads = sum(1 for a in served for b in comp[a] if b in served and a < b)
        rows.append((supplier, len(served), dyads, json.dumps(sorted(served)), now))
    return rows


# --------------------------------------------------------------------------- #
# lane: competitive_overlap
# --------------------------------------------------------------------------- #
def derive_competitive_overlap(
    conn: sqlite3.Connection, *, now: str, db_path: str | Path | None = None
) -> list[tuple]:
    comp: dict[str, set[str]] = defaultdict(set)
    for s, t in conn.execute(
        "SELECT source, target FROM graph_edges WHERE edge_type='competes_with'"
    ).fetchall():
        a, b = sorted((s, t))
        comp[a].add(b)
        comp[b].add(a)
    rows = []
    for a in sorted(comp):
        for b in sorted(n for n in comp[a] if a < n):
            for c in sorted(n for n in comp[b] if b < n and n in comp[a]):
                rows.append((a, b, c, now))
    return rows


_TABLES = {
    "ultimate-parent": ("ultimate_parent", derive_ultimate_parent),
    "hidden-connections": ("hidden_connections", derive_hidden_connections),
    "supply-chokepoints": ("supply_chokepoints", derive_supply_chokepoints),
    "competitive-overlap": ("competitive_overlap", derive_competitive_overlap),
}


def _digest(rows: list[tuple]) -> str:
    """Content parity pin — sha256 over the rows MINUS computed_at."""
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(row[:-1], sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def run_lane(
    conn: sqlite3.Connection,
    lane: str,
    *,
    apply_: bool,
    verbose: bool = False,
    db_path: str | Path | None = None,
) -> tuple[int, str]:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    table, fn = _TABLES[lane]
    rows = fn(conn, now=now, db_path=db_path)
    if apply_:
        ensure_tables(conn)  # idempotent IF NOT EXISTS — self-sufficient apply
        with conn:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — registry-owned constant
            if rows:
                conn.executemany(
                    f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})",  # noqa: S608
                    rows,
                )
    digest = _digest([r[:-1] for r in rows])
    mode = "APPLIED" if apply_ else "dry-run"
    print(f"[{mode}] {lane} -> {table}: {len(rows)} rows, content digest {digest}")
    if verbose:
        for r in rows[:10]:
            print("   ", " | ".join(str(x)[:56] for x in r[:-1]))
    return len(rows), digest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--lane", choices=("all", *LANES), default="all", help="which lane (default all)"
    )
    ap.add_argument("--apply", action="store_true", help="write the table(s) (default: dry-run)")
    ap.add_argument("--verbose", "-v", action="store_true", help="sample rows per lane")
    args = ap.parse_args(argv)

    conn = _db_connect(DEFAULT_DB_PATH)
    try:
        if args.apply:
            ensure_tables(conn)
            conn.commit()
        lanes = LANES if args.lane == "all" else (args.lane,)
        for lane in lanes:
            run_lane(conn, lane, apply_=args.apply, verbose=args.verbose, db_path=DEFAULT_DB_PATH)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
