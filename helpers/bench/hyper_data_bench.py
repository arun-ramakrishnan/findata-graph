#!/usr/bin/env python3
"""S20 benchmark: the HGX star lane on a REAL hypergraphx-data dataset.

Proposal S20 (hypergraph_incidence_hyx): benchmark our production lane
functions at real-world scale against the synthetic-seed baseline
(hyper_scale_bench, 100K incidences -> 1.0-1.2 s / 100 EM iters, 9 MB).

Data: bench_data/<dataset>/hyperedges-<dataset>.txt (Cornell /~arb/data
format — one hyperedge per line, comma-separated node indices; the same
files the hypergraphx-data catalog distributes via cricca.disi.unitn.it,
whose TLS chain is broken, so we pull the originals the catalog's own
reproducibility READMEs point to). bench_data/ is gitignored (S20:
"never the vault or snapshots").

Stages (each timed, tracemalloc peak per stage):
  parse        hyperedges txt -> incidence dict (str ids, catalog semantics:
               duplicate member-sets kept as distinct edges)
  construct    Hypergraph(edge_list=...) (the lane's own constructor call)
  filter       _exclude_degenerate (production degeneracy guard, defaults)
  fit          hyper_communities.fit_communities (Hy-MMSBM, seed pinned)
  walk         hyper_centralities.stationary_pi on the fitted blocks
  centralities hyper_centralities.compute_centralities (six lanes; pass
               --skip-centralities on multi-million-node datasets —
               s-betweenness is BFS-per-node, superlinear)

Usage:
    python3 helpers/bench/hyper_data_bench.py --dataset trivago-clicks
    python3 helpers/bench/hyper_data_bench.py --dataset stackoverflow-answers \
        --skip-centralities --json bench_data/so_bench.json
Not wired into make perf (BENCHMARKS is an explicit list): minutes-scale.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import tracemalloc
import zipfile
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "helpers"))

import warnings  # noqa: E402

warnings.filterwarnings("ignore", category=SyntaxWarning)  # HGX docstrings

from helpers.graph import hyper_centralities as hc  # noqa: E402
from helpers.graph import hyper_communities as hcm  # noqa: E402

ROWS: list[dict] = []


@contextmanager
def stage(name: str, track: bool = True):
    t0 = time.perf_counter()
    if track:
        tracemalloc.start()
    out = {}
    try:
        yield out
    finally:
        secs = time.perf_counter() - t0
        peak = tracemalloc.get_traced_memory()[1] / 1e6 if track else None
        if track:
            tracemalloc.stop()
        row = {"stage": name, "secs": round(secs, 3), "peak_mb": round(peak, 1) if peak else None}
        row.update(out)
        ROWS.append(row)
        print(f"[{name}] {secs:.2f}s peak={row['peak_mb']}MB { {k: v for k, v in out.items()} }")


def load_incidence_python(path: Path, max_edges: int | None) -> dict[str, list[str]]:
    """Fallback parser: pure Python line split (.txt / .txt.gz / .zip)."""
    if path.exists():
        lines = path.read_text().splitlines()
    elif path.with_suffix(path.suffix + ".gz").exists():
        with gzip.open(path.with_suffix(path.suffix + ".gz")) as f:
            lines = f.read().decode().splitlines()
    else:  # Drive mislabel: <name>.json.gz is actually a zip
        ds_dir, name = path.parent, path.name.replace("hyperedges-", "").replace(".txt", "")
        with zipfile.ZipFile(ds_dir / f"{name}.json.gz") as zf:
            inner = next(z for z in zf.namelist() if z.endswith(f"hyperedges-{name}.txt"))
            with zf.open(inner) as f:
                lines = f.read().decode().splitlines()
    inc: dict[str, list[str]] = {}
    for i, line in enumerate(lines):
        if max_edges is not None and i >= max_edges:
            break
        inc[str(i)] = line.split(",")
    return inc


def load_incidence_duckdb(path: Path, max_edges: int | None) -> dict[str, list[str]]:
    """House data plane: DuckDB read_csv + string_split (C-speed parse).

    '|' never occurs in the node-id files, so each line reads as one
    VARCHAR column; split into the lane's list[str] shape in SQL.
    """
    import duckdb

    con = duckdb.connect()
    try:
        limit = f" LIMIT {int(max_edges)}" if max_edges is not None else ""
        rel = con.execute(
            """
            SELECT string_split(column0, ',') AS members
            FROM read_csv(?, header = false, columns = {'column0': 'VARCHAR'},
                          delim = '|')
           """
            + limit,
            [str(path)],
        )
        rows = rel.fetchall()
        return {str(i): r[0] for i, r in enumerate(rows)}
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", default="trivago-clicks")
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--n-iter", type=int, default=100)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--s", type=int, default=2)
    p.add_argument("--max-edges", type=int, default=None, help="smoke: first N hyperedges")
    p.add_argument("--allow-giant", action="store_true")
    p.add_argument("--skip-centralities", action="store_true")
    p.add_argument("--no-tracemalloc", action="store_true")
    p.add_argument("--json", default=None, help="write ROWS here")
    args = p.parse_args(argv)

    ds_dir = REPO_ROOT / "bench_data" / args.dataset
    track = not args.no_tracemalloc

    he_txt = ds_dir / f"hyperedges-{args.dataset}.txt"
    parser = load_incidence_duckdb if he_txt.exists() else load_incidence_python
    with stage("parse", track) as st:
        raw = (
            parser(he_txt, args.max_edges)
            if he_txt.exists()
            else load_incidence_python(he_txt, args.max_edges)
        )
        st["edges"] = len(raw)
        st["incidences"] = sum(len(v) for v in raw.values())
        st["nodes"] = len({n for v in raw.values() for n in v})

    with stage("construct", track) as st:
        Hypergraph, *_ = hc._require_hgx()
        hg = Hypergraph(edge_list=[tuple(v) for v in raw.values()])
        st["hg_edges"] = hg.num_edges()
        st["hg_nodes"] = hg.num_nodes()

    with stage("filter", track) as st:
        kept, giants, singletons = hc._exclude_degenerate(raw, allow_giant=args.allow_giant)
        st["kept"] = len(kept)
        st["giants"] = len(giants)
        st["singletons"] = len(singletons)

    with stage("fit", track) as st:
        memberships = hcm.fit_communities(kept, k=args.k, seed=args.seed, n_iter=args.n_iter)
        st["fitted_nodes"] = len(memberships)

    with stage("walk", track) as st:
        pi = hc.stationary_pi({e: set(m) for e, m in kept.items()})
        st["walk_nodes"] = len(pi)
        st["pi_sum"] = round(sum(pi.values()), 4)

    if not args.skip_centralities:
        with stage("centralities", track) as st:
            cen = hc.compute_centralities(kept, s=args.s, allow_giant=args.allow_giant)
            st["lanes"] = [k for k in cen if k != "_meta"]
            st["scored_nodes"] = len(cen.get("ho_pagerank", {}))

    rows = ROWS
    total = sum(r["secs"] for r in rows)
    print(f"TOTAL {total:.1f}s across {len(rows)} stages")
    if args.json:
        out = {
            "dataset": args.dataset,
            "k": args.k,
            "n_iter": args.n_iter,
            "seed": args.seed,
            "skip_centralities": args.skip_centralities,
            "allow_giant": args.allow_giant,
            "total_secs": round(total, 2),
            "stages": rows,
        }
        Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"rows -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
