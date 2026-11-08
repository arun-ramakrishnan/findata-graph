#!/usr/bin/env python3
"""Bench the fuzzy word-overlap backends: Python scan vs DuckDB pushdown.

algorithms_adoption_sweep S1 — records the per-query win of the SQL
backend over the O(all-entities) Python loop, on the LIVE entity corpus
(research.db, read-only). Parity is asserted once per query before
timing (a divergence aborts the bench — the py path is the contract).

Usage:
    python3 helpers/bench/fuzzy_backend_bench.py [--queries 200] [--repeat 20]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from helpers.core.db import connect  # noqa: E402
from helpers.core.fuzzy_duckdb import word_overlap_match_sql  # noqa: E402
from helpers.core.fuzzy_match import word_overlap_match  # noqa: E402

QUERIES_SEED = [
    "Adani Port",
    "Tata Motors",
    "HDFC Bank",
    "power grid",
    "Reliance",
    "Larsen Toubro",
    "Sun Pharma",
    "Bajaj Finance",
    "Maruti",
    "Nestle",
]


def live_entities() -> list[str]:
    con = connect(read_only=True)
    try:
        return [
            r["name"] for r in con.execute("SELECT name FROM entities ORDER BY name").fetchall()
        ]
    finally:
        con.close()


def query_set(entities: list[str], n: int) -> list[str]:
    """Deterministic query mix: real prefixes/truncations + noise."""
    import random

    rng = random.Random(42)  # noqa: S311 — deterministic seeded bench sampler, not crypto
    out: list[str] = []
    base = [q for q in QUERIES_SEED] + [e[: max(4, len(e) // 2)] for e in entities[:50]]
    while len(out) < n:
        if rng.random() < 0.8:
            out.append(rng.choice(base))
        else:  # noise token pair — exercises the miss path
            out.append(f"{rng.choice(entities)}"[3:12])
    return out[:n]


MISS_QUERIES = [
    "zzq wobble craft",
    "flurgen burmistan",
    "zorvath petrochem zorvia",
    "qwerty fnord holdings",
]


def _assert_parity(queries: list[str], entities: list[str]) -> int | None:
    """Parity once per query — a divergence aborts the bench (py path is the contract)."""
    for q in queries:
        py = word_overlap_match(q, entities)
        sql = word_overlap_match_sql(q, entities)
        if py != sql:
            print(f"PARITY FAILURE q={q!r}: py={py} sql={sql}", file=sys.stderr)
            return 1
    return None


def _time_backend(fn, queries: list[str], repeat: int) -> float:
    """Mean ms/query over repeat passes of fn(q)."""
    t0 = time.perf_counter()
    for _ in range(repeat):
        for q in queries:
            fn(q)
    return (time.perf_counter() - t0) * 1000 / (repeat * len(queries))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--queries", type=int, default=200)
    p.add_argument("--repeat", type=int, default=20)
    args = p.parse_args()

    entities = live_entities()
    queries = query_set(entities, args.queries)
    print(f"entities: {len(entities)}  queries: {len(queries)}  repeat: {args.repeat}")

    # parity once per query — abort on divergence; MISS_QUERIES have no
    # containment anywhere, exercising the full-corpus scan path
    for qs in (queries, MISS_QUERIES):
        rc = _assert_parity(qs, entities)
        if rc is not None:
            return rc
    print(
        f"parity: {len(queries) + len(MISS_QUERIES)}/{len(queries) + len(MISS_QUERIES)} identical"
    )

    py_ms = _time_backend(lambda q: word_overlap_match(q, entities), queries, args.repeat)
    py_miss_ms = _time_backend(lambda q: word_overlap_match(q, entities), MISS_QUERIES, args.repeat)

    import duckdb

    conn = duckdb.connect()
    sql_ms = _time_backend(
        lambda q: word_overlap_match_sql(q, entities, conn=conn), queries, args.repeat
    )
    sql_miss_ms = _time_backend(
        lambda q: word_overlap_match_sql(q, entities, conn=conn), MISS_QUERIES, args.repeat
    )
    conn.close()

    print(f"python  scan (mixed):       {py_ms:8.3f} ms/query")
    print(f"duckdb pushdn (mixed):      {sql_ms:8.3f} ms/query  ({py_ms / sql_ms:.2f}x)")
    print(f"python  scan (miss/full):   {py_miss_ms:8.3f} ms/query")
    print(
        f"duckdb pushdn (miss/full):  {sql_miss_ms:8.3f} ms/query  ({py_miss_ms / sql_miss_ms:.2f}x)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
