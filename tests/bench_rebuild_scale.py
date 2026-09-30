#!/usr/bin/env python3
"""Perf diagnostic: PRODUCTION graph rebuild cost vs source scale.

Companion to ``tests/bench_scale_bfs.py``, and the in-repo answer to the
provenance hole ``doc/improvements/archive/graph/vault_scaling.md`` §3.1
carries. That ladder's ``materialize`` column was measured by
``_materialise_walk_substrate`` — the ``e_dir`` + ``e_all_und`` CTAS pair —
on a purpose-built synthetic DuckDB with no SQLite ATTACH, no ``v_node``, no
per-edge-type tables, no hypergraph, no note embeddings and no DROP churn.
This harness measures the function the tier ladder is actually about:
``helpers.graph.query.connect(rebuild=True)`` → ``_build_graph`` (schema 17:
56 ``DROP TABLE IF EXISTS`` + ~50 CTAS over an attached SQLite source).

Two further reasons the old column is not a production number, both checked
2026-10-01 on this box:

1. **Different function / substrate** (above).
2. **Density.** ``bench_scale_bfs.py --degree`` defaults to 22 with help text
   "prod ~= 22.4", but the live corpus measures **2.61 directed rows/node**
   (5.22 doubled; 57,515 edges over 22,054 nodes that appear in an edge).
   The sibling therefore builds graphs ~8.4x denser than production, which
   changes both the GEMM shape and the per-block mask density.
   ``--density-check`` quantifies the ladder column against this harness at
   the same R so the two effects are separated rather than conflated.

Method: clone the production schema with no rows, bulk-generate a synthetic
source at the requested scale (deterministic — a Knuth multiplicative hash of
``(i, k)``, no RNG state, matching the sibling's idiom), then time the real
rebuild. Best-of-N, minimum reported, per the house rule for timing numbers.

Safety: the resolved ``db_path`` is a temp file, so ``query.connect`` takes
its sibling-``.duckdb`` branch and production ``memory/graph.duckdb`` /
``memory/research.db`` are never opened for write. That is asserted, not
assumed: both files' size and mtime are captured before and after and the run
aborts if either moved.

NOT a make-perf leg (10M doubled rows is ~2 GB of scratch SQLite and tens of
seconds of generation). Run on demand::

    .venv/bin/python3 tests/bench_rebuild_scale.py                    # live + T1
    .venv/bin/python3 tests/bench_rebuild_scale.py --rows 10000000    # T2
    .venv/bin/python3 tests/bench_rebuild_scale.py --breakdown        # fixed-cost split
    .venv/bin/python3 tests/bench_rebuild_scale.py --density-check    # vs the ladder column

Reference measured 2026-10-01 (this box, R = ``e_all_und`` doubled rows):
T(R) ~= 2.28 s + 3.0 us * R, i.e. ~87% fixed statement overhead at live
scale; 5 s is crossed at R ~= 907K. See
``doc/improvements/archive/graph/vault_scaling.md`` §3.1.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SRC_DB = _REPO_ROOT / "memory" / "research.db"
DUCK = _REPO_ROOT / "memory" / "graph.duckdb"

# Measured 2026-10-01 against memory/research.db. Doubled rows per node is the
# e_all_und convention the tier ladder counts in.
LIVE = {
    "rows": 115_030,  # e_all_und
    "degree_directed": 2.61,  # 57,515 edges / 22,054 nodes touching an edge
    "dims": 384,
    "notes": 17_237,  # note_search rows -> v_note_embeddings
    "embeddings": 1_186,  # company_embeddings -> v_embeddings
    "hyper_edges": 1_558,
    "hyper_incidences": 8_070,
    "tags_per_entity": 0.28,  # 7,313 entity_tags / 26,153 entities
}

SIBLING_DEGREE = 22  # bench_scale_bfs.py's default, kept for --density-check
MARKET_CAPS = ("market_cap/large_cap", "market_cap/mid_cap", "market_cap/small_cap")
SECTORS = ("Banking", "Energy", "IT", "Pharma", "Auto", "FMCG", "Metals", "Utilities")
# Live edge_type mix is dominated by a few types; keep the head so the
# per-derivation CTAS fan-out is representative rather than uniform.
EDGE_TYPES = (
    ("belongs_to", 0.30),
    ("supplier_to", 0.27),
    ("subsidiary_of", 0.20),
    ("same_group", 0.17),
    ("jv_with", 0.03),
    ("invested_in", 0.02),
    ("competes", 0.01),
)
M32 = 1 << 32


def _h(i: int, k: int) -> int:
    """Knuth multiplicative hash — deterministic, no RNG state."""
    return (i * 2_654_435_761 + k * 40_503) % M32


def _edge_type(i: int) -> str:
    x = _h(i, 7) % 1000
    acc = 0
    for name, share in EDGE_TYPES:
        acc += int(share * 1000)
        if x < acc:
            return name
    return EDGE_TYPES[-1][0]


def _prod_guard() -> dict[str, tuple[int, int]]:
    out = {}
    for p in (SRC_DB, DUCK):
        try:
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            out[str(p)] = (-1, -1)
    return out


def _assert_prod_untouched(before: dict[str, tuple[int, int]]) -> None:
    after = _prod_guard()
    moved = [p for p in before if before[p] != after[p]]
    if moved:
        for p in moved:
            print(f"FATAL: production file moved during the run: {p}", file=sys.stderr)
        raise SystemExit(2)


def _clone_schema(dest: Path) -> None:
    """Empty DB with production's exact schema (tables, indexes, triggers).

    Cloning rather than inventing keeps every CHECK / FK / generated column
    the materialiser relies on. The build is proven to handle empty inputs
    (the R=0 intercept measurement), so unpopulated tables are not a gap.
    """
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    objs = [
        (typ, sql)
        for typ, sql in src.execute(
            "SELECT type, sql FROM sqlite_master WHERE sql IS NOT NULL"
        )
        # sqlite_stat1 is ANALYZE's own table, reserved for internal use.
        if "sqlite_stat" not in sql
    ]
    src.close()
    # Virtual tables first: FTS5 owns shadow tables (…_data, …_idx, …_docsize,
    # …_content) that also appear in sqlite_master, so materialising those
    # before CREATE VIRTUAL TABLE makes the virtual create fail with
    # "table already exists". Then tolerate anything already created.
    objs.sort(key=lambda o: 0 if o[1].lstrip().upper().startswith("CREATE VIRTUAL") else 1)
    out = sqlite3.connect(dest)
    for _typ, sql in objs:
        try:
            out.execute(sql)
        except sqlite3.OperationalError as exc:
            if "already exists" not in str(exc):
                raise
    out.commit()
    out.close()


def _f32_blob(vec: list[float]) -> bytes:
    import struct

    return struct.pack(f"<{len(vec)}f", *vec)


def _generate(dest: Path, rows: int, *, degree: float, notes: int, dims: int) -> dict[str, int]:
    """Bulk-generate a synthetic source at ``rows`` doubled (e_all_und) scale."""
    directed = max(1, rows // 2)
    nodes = max(4, int(directed / max(degree, 0.01)))
    con = sqlite3.connect(dest)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    # entities — company head plus a taxonomy tail so v_node's `kind` split and
    # the v_* hierarchy projections are exercised.
    n_tax = max(1, nodes // 20)
    con.executemany(
        "INSERT OR IGNORE INTO entities (name, entity_type, normalized_name, sector_classification)"
        " VALUES (?,?,?,?)",
        (
            (f"Co_{i:09d}", "company", f"co {i:09d}", SECTORS[_h(i, 3) % len(SECTORS)])
            for i in range(nodes - n_tax)
        ),
    )
    con.executemany(
        "INSERT OR IGNORE INTO entities (name, entity_type, normalized_name, sector_classification)"
        " VALUES (?,?,?,?)",
        ((f"Tax_{i:06d}", "sector", f"tax {i:06d}", None) for i in range(n_tax)),
    )

    # entity_tags — includes market_cap/* because v_node's CTAS runs a
    # correlated subselect per row for it (the per-row hot spot in the build).
    tags_per = LIVE["tags_per_entity"]
    tag_rows = []
    for i in range(nodes - n_tax):
        if _h(i, 11) % 1000 < int(tags_per * 1000):
            tag_rows.append((f"Co_{i:09d}", MARKET_CAPS[_h(i, 13) % len(MARKET_CAPS)]))
        if _h(i, 17) % 1000 < 60:
            tag_rows.append((f"Co_{i:09d}", f"sector/{SECTORS[_h(i, 19) % len(SECTORS)]}"))
    con.executemany("INSERT OR IGNORE INTO entity_tags (entity_name, tag) VALUES (?,?)", tag_rows)

    # graph_edges — collision-free by construction. A hashed (source, target)
    # pair violates UNIQUE(source, target, edge_type) by birthday collision at
    # any real scale, so enumerate ordered pairs instead: a = i % N and an
    # offset d = 1 + i // N, giving each (a, a+d) exactly once. Deterministic,
    # no RNG state, and d >= 1 satisfies the source != target CHECK.
    # >= 2 so the pair enumeration below can never emit a self-loop
    # (graph_edges has CHECK (source != target)); at R=0 the row count
    # collapses to 1 without this floor.
    company_n = max(2, nodes - n_tax)
    con.executemany(
        "INSERT INTO graph_edges (source, target, edge_type, weight, properties, source_ref,"
        " symmetric, source_tier) VALUES (?,?,?,?,?,?,?,?)",
        (
            (
                f"Co_{a:09d}",
                f"Co_{(a + d) % company_n:09d}",
                _edge_type(i),
                1.0,
                "{}",
                "synthetic://bench",
                0,
                "derive",
            )
            for i, (a, d) in enumerate(
                (i % company_n, 1 + i // company_n) for i in range(directed)
            )
        ),
    )

    # hypergraph + embeddings, scaled to the live ratios so the build's
    # per-stage mix matches production rather than being edge-dominated.
    h_edges = max(1, int(directed * LIVE["hyper_edges"] / 57_515))
    con.executemany(
        "INSERT INTO hyper_edges (edge_type, label, weight, source_ref, properties, source_tier)"
        " VALUES (?,?,?,?,?,?)",
        (("sector", f"Syn_{i:06d}", 1.0, "synthetic://bench", "{}", "derive") for i in range(h_edges)),
    )
    # Same collision-free pairing for the incidences' (edge_id, entity) PK.
    con.executemany(
        "INSERT INTO hyper_incidences (edge_id, entity_name, weight) VALUES (?,?,?)",
        (
            (1 + (i % h_edges), f"Co_{(i // h_edges) % company_n:09d}", 1.0)
            for i in range(int(directed * LIVE["hyper_incidences"] / 57_515))
        ),
    )

    emb_n = max(0, int(company_n * LIVE["embeddings"] / 25_469))
    base = [(((_h(i, j) % 2000) - 1000) / 1000.0) for j in range(dims)]
    # company_embeddings: (company_name PK, embedding BLOB, model NOT NULL,
    # CHECK length(embedding) = 384*4) — so dims is pinned to 384 by the schema.
    con.executemany(
        "INSERT INTO company_embeddings (company_name, embedding, model) VALUES (?,?,?)",
        ((f"Co_{i:09d}", _f32_blob(base), "synthetic://bench") for i in range(emb_n)),
    )
    if notes:
        con.executemany(
            "INSERT INTO note_search (doc_type, file_path, title, sector, content, embedding,"
            " section_title, anchor) VALUES (?,?,?,?,?,?,?,?)",
            (
                (
                    "company",
                    f"findata/Companies/{SECTORS[i % len(SECTORS)]}/N_{i:06d}.md",
                    f"Note {i:06d}",
                    SECTORS[i % len(SECTORS)],
                    "synthetic body " * 8,
                    _f32_blob(base),
                    None,
                    None,
                )
                for i in range(notes)
            ),
        )
    con.commit()
    counts = {
        "entities": con.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        "graph_edges": con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0],
        "notes": con.execute("SELECT COUNT(*) FROM note_search").fetchone()[0],
    }
    con.close()
    counts["nodes_target"] = nodes
    return counts


def _best_of(fn, reps: int) -> tuple[float, list[float]]:
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return min(samples), samples


def _time_rebuild(db: Path, reps: int) -> tuple[float, int, int]:
    from helpers.graph import query as q

    duck = db.with_suffix(".duckdb")
    duck.unlink(missing_ok=True)
    duck.with_suffix(".duckdb.wal").unlink(missing_ok=True)

    def once() -> None:
        con = q.connect(db_path=db, rebuild=True)
        con.close()

    best, samples = _best_of(once, reps)
    import duckdb

    ro = duckdb.connect(str(duck), read_only=True)
    doubled = ro.execute("SELECT COUNT(*) FROM e_all_und").fetchone()[0]
    ro.close()
    return best, doubled, duck.stat().st_size


def _breakdown(db: Path) -> None:
    """Decompose the fixed cost: extensions / DROPs / DDL / residual CTAS."""
    import duckdb

    from helpers.graph import query as q

    scratch = db.with_name("breakdown.duckdb")
    scratch.unlink(missing_ok=True)
    con = duckdb.connect(str(scratch))
    t = time.perf_counter()
    q._prep_graph_connection(con)
    t_ext = time.perf_counter() - t
    t = time.perf_counter()
    q._attach_sqlite(con, db)
    t_attach = time.perf_counter() - t
    tables = [s["table"] for s in q.EDGE_REGISTRY.values()] + list(q._EXTRA_MATERIALIZED)
    tables += list(q._CENTRALITY_TABLES)
    t = time.perf_counter()
    for name in tables:
        con.execute(f"DROP TABLE IF EXISTS {name}")
    t_drop = time.perf_counter() - t
    con.close()
    scratch.unlink(missing_ok=True)
    _, doubled, _ = _time_rebuild(db, 1)
    print("\nfixed-cost breakdown (R=0 intercept is the total; parts do not sum to it)")
    print(f"  extension INSTALL+LOAD (sqlite, vss)   {t_ext:8.3f} s")
    print(f"  ATTACH the sqlite as fin                {t_attach:8.3f} s")
    print(f"  {len(tables):>2} x DROP TABLE IF EXISTS            {t_drop:8.3f} s")
    print("  -> residual = planning + executing the CTAS set over the attached sqlite")
    print(f"  measured total at R={doubled:,}: see the table above")


def _density_check(rows: int, reps: int) -> None:
    """Compare the ladder column with the production path at the same R."""
    print("\ndensity check — ladder `materialize` vs the production rebuild")
    print(f"{'variant':>34} {'best_s':>8}")
    for label, degree in (("ladder degree 22 (sibling default)", SIBLING_DEGREE),
                          ("measured degree 2.61 (production)", LIVE["degree_directed"])):
        scratch = Path(tempfile.mkdtemp(prefix="scale_bfs_"))
        try:
            db = scratch / "research.db"
            _clone_schema(db)
            _generate(db, rows, degree=degree, notes=LIVE["notes"], dims=LIVE["dims"])
            best, doubled, _ = _time_rebuild(db, reps)
            print(f"{label:>34} {best:>8.2f}   (R={doubled:,})")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--rows", type=int, nargs="*", default=None,
                   help="e_all_und doubled-row targets (default: live, T1)")
    p.add_argument("--reps", type=int, default=3, help="Best-of-N, min reported")
    p.add_argument("--degree", type=float, default=LIVE["degree_directed"],
                   help=f"Directed edges/node (measured {LIVE['degree_directed']}; "
                        f"sibling defaults to {SIBLING_DEGREE})")
    p.add_argument("--notes", type=int, default=LIVE["notes"],
                   help="note_search rows -> v_note_embeddings (0 disables that stage)")
    p.add_argument("--breakdown", action="store_true", help="Fixed-cost decomposition")
    p.add_argument("--density-check", action="store_true",
                   help="Compare the sibling ladder's default degree against the measured one")
    p.add_argument("--keep", action="store_true", help="Keep the scratch directory")
    args = p.parse_args(argv)

    if not SRC_DB.is_file():
        print(f"no production schema source at {SRC_DB}", file=sys.stderr)
        return 2
    targets = args.rows if args.rows else [LIVE["rows"], 1_000_000]
    before = _prod_guard()

    print("production rebuild cost — T(R) ~= a + b*R, R = e_all_und doubled rows")
    print(f"degree {args.degree} directed edges/node · notes {args.notes} · "
          f"best of {args.reps}\n")
    print(f"{'R target':>12} {'R actual':>12} {'entities':>10} {'edges':>10} "
          f"{'best_s':>8} {'duck_MB':>8}")
    fits: list[tuple[float, float]] = []
    for rows in targets:
        scratch = Path(tempfile.mkdtemp(prefix="scale_bfs_"))
        try:
            db = scratch / "research.db"
            _clone_schema(db)
            counts = _generate(db, rows, degree=args.degree, notes=args.notes,
                               dims=LIVE["dims"])
            best, doubled, size = _time_rebuild(db, args.reps)
            fits.append((doubled, best))
            print(f"{rows:>12,} {doubled:>12,} {counts['entities']:>10,} "
                  f"{counts['graph_edges']:>10,} {best:>8.2f} {size / 1048576:>8.1f}")
        finally:
            if args.keep:
                print(f"  kept: {scratch}")
            else:
                shutil.rmtree(scratch, ignore_errors=True)

    if len(fits) >= 2:
        (r0, t0), (r1, t1) = fits[0], fits[-1]
        slope = (t1 - t0) / (r1 - r0)
        intercept = t0 - slope * r0
        cross = (5.0 - intercept) / slope if slope > 0 else float("inf")
        print(f"\nfit: T(R) ~= {intercept:.2f} s + {slope * 1e6:.2f} us * R")
        print(f"  5 s line crossed at R ~= {cross:,.0f} doubled rows "
              f"({cross / LIVE['rows']:.1f}x live)")
    if args.breakdown:
        scratch = Path(tempfile.mkdtemp(prefix="scale_bfs_"))
        try:
            db = scratch / "research.db"
            _clone_schema(db)
            _generate(db, 0, degree=args.degree, notes=0, dims=LIVE["dims"])
            _breakdown(db)
        finally:
            if not args.keep:
                shutil.rmtree(scratch, ignore_errors=True)
    if args.density_check:
        _density_check(min(targets), args.reps)

    _assert_prod_untouched(before)
    print("\nproduction memory/graph.duckdb + memory/research.db unchanged (size+mtime).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
