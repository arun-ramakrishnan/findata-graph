#!/usr/bin/env python3
"""CSR substrate — vault_scaling Phase B-A (build-early, gated promotion).

Frozen binary layout (vault_scaling.md §5 B-A):
    csr_offsets.bin    (N+1 × int32 LE)   row offsets into neighbors
    csr_neighbors.bin  (M × int32 LE)     neighbor ids, both directions,
                                          each row's slice sorted ascending
    csr_names.json     (N names, sorted = deterministic ids)
    csr_manifest.json  {schema_version, nodes, directed_rows, edges,
                         checksum, generation, built_at}

Determinism: node ids are the SORTED endpoint names (scipy_bridge
720e38ff discipline); neighbors sorted ascending per row; checksum
covers both binaries — two builds over the same edge set are
byte-identical (the manifest's built_at is the only differing byte).

Staleness: the manifest carries the SQLite ``db_meta.generation`` at
build time; :func:`load` compares it against the live generation and
reports ``fresh=False`` on drift — callers fall back to the DuckDB path
loudly, never silently (vault_scaling fallback doctrine). This module
is structure-only: edge labels/weights/as-of stay on the DuckDB path
until a filtered workload proves hot (B-A non-goal).

Caching: :func:`try_shortest_path` memoises the derived substrate (mmaps,
names, name -> id) per (out_dir, live generation). The generation is
re-read on every call and is the cache key, so the gate above is
unchanged — drift misses the cache and falls back exactly as before
(:func:`clear_cache` drops it explicitly for tests and post-rebuild
callers).

Int32 holds while N, M < 2³¹ (live: N=22,054, M=114,782; the recorded
growth path past that is shard-by-node-range, never a silent widen).
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "memory" / "csr"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core.db import connect  # noqa: E402  # needs the shim above

# Module-side substrate cache, keyed on (out_dir, live SQLite generation).
# The lane used to re-parse csr_names.json and rebuild the 22k-entry
# name -> id map on EVERY call (~7.2 ms measured), which erased the whole
# point of the fast path: end-to-end was 9.15 ms vs 11.6 ms SQL (1.27x),
# not the 0.08-0.26 ms kernel-vs-SQL figure the commit advertised.
#
# Freshness is NOT cached: the live db_meta.generation is re-read on every
# call and is the cache key, so a rebuild lands on a different key and the
# stale entry is never served (drift -> fresh=False -> SQL fallback, the
# same loud fallback doctrine the manifest gate already enforces). Only the
# derived artefacts (mmaps + names + pos) are memoised.
_CACHE: dict[
    tuple[str, str | None], tuple[dict, np.ndarray, np.ndarray, list[str], dict[str, int]]
] = {}
_CACHE_MAX = 4


def clear_cache() -> None:
    """Drop the memoised substrate (tests, and any post-rebuild caller)."""
    _CACHE.clear()


def _read_edges(db_path: str | Path) -> list[tuple[str, str]]:
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        return [
            (r[0], r[1]) for r in con.execute("SELECT source, target FROM graph_edges").fetchall()
        ]
    finally:
        con.close()


def _live_generation(db_path: str | Path) -> str | None:
    con = connect(db_path, read_only=True, row_factory=None)
    try:
        row = con.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
        return None if row is None else str(row[0])
    except Exception:  # noqa: BLE001  # pre-migration db -> no generation
        return None
    finally:
        con.close()


def build(db_path: str | Path, out_dir: str | Path = DEFAULT_OUT_DIR) -> dict:
    """Build the CSR bins + manifest from ``graph_edges``; idempotent.

    Deterministic: sorted node ids, sorted neighbor slices, fixed dtypes.
    Returns the manifest dict that was written.
    """
    edges = _read_edges(db_path)
    names = sorted({s for s, _ in edges} | {t for _, t in edges})
    pos = {n: i for i, n in enumerate(names)}
    n = len(names)
    clear_cache()  # the bins below are rewritten under any live memmap
    r = np.fromiter((pos[s] for s, _ in edges), dtype=np.int64, count=len(edges))
    c = np.fromiter((pos[t] for _, t in edges), dtype=np.int64, count=len(edges))
    both_r = np.concatenate([r, c])
    both_c = np.concatenate([c, r])
    order = np.lexsort((both_c, both_r))  # row-major, neighbors ascending
    both_r, both_c = both_r[order], both_c[order]
    m = both_r.size
    offsets = np.zeros(n + 1, dtype=np.int32)
    np.add.at(offsets, both_r + 1, 1)
    np.cumsum(offsets, out=offsets)
    offsets_bin = offsets.tobytes()
    neighbors_bin = both_c.astype(np.int32).tobytes()
    checksum = hashlib.sha256(offsets_bin + neighbors_bin).hexdigest()
    generation = _live_generation(db_path)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "nodes": n,
        "directed_rows": int(m),
        "edges": len(edges),
        "checksum": checksum,
        "generation": generation,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "csr_offsets.bin").write_bytes(offsets_bin)
    (out / "csr_neighbors.bin").write_bytes(neighbors_bin)
    (out / "csr_names.json").write_text(json.dumps(names, ensure_ascii=False))
    (out / "csr_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def load(
    out_dir: str | Path = DEFAULT_OUT_DIR,
    *,
    db_path: str | Path | None = None,
    verify_checksum: bool = False,
) -> tuple[dict, np.ndarray, np.ndarray, list[str], bool]:
    """Load the CSR (mmap) + manifest; report freshness.

    Returns ``(manifest, offsets, neighbors, names, fresh)``. ``fresh``
    is False when the manifest is missing/corrupt, the checksum fails
    (only checked when ``verify_checksum``), or the live SQLite
    generation has drifted past the manifest's — callers fall back to
    the DuckDB path on ``not fresh`` and say so.
    """
    out = Path(out_dir)
    try:
        manifest = json.loads((out / "csr_manifest.json").read_text())
        offsets = np.memmap(out / "csr_offsets.bin", dtype=np.int32, mode="r")
        neighbors = np.memmap(out / "csr_neighbors.bin", dtype=np.int32, mode="r")
        names = json.loads((out / "csr_names.json").read_text())
    except OSError, json.JSONDecodeError:
        return {}, np.array([]), np.array([]), [], False
    if verify_checksum:
        digest = hashlib.sha256(
            (out / "csr_offsets.bin").read_bytes() + (out / "csr_neighbors.bin").read_bytes()
        ).hexdigest()
        if digest != manifest.get("checksum"):
            return manifest, offsets, neighbors, names, False
    fresh = True
    if db_path is not None:
        live = _live_generation(db_path)
        fresh = live is not None and live == manifest.get("generation")
    return manifest, offsets, neighbors, names, fresh


def _claim_parents(
    frontier: list[int],
    offsets: np.ndarray,
    neighbors: np.ndarray,
    parent: np.ndarray,
) -> dict[int, int]:
    """MIN-claimant discovery for one BFS level (see ``bfs_path``).

    Each newly discovered node claims the MINIMUM discoverer id in this
    level — the recorded vault_scaling B-B rule; first-claim order was
    retracted. Collecting the whole level's claims BEFORE any is written
    to ``parent`` is what makes the outcome depend only on the parent
    tree rather than on scan order.
    """
    claims: dict[int, int] = {}
    for u in frontier:
        for v in neighbors[int(offsets[u]) : int(offsets[u + 1])]:
            v = int(v)
            if parent[v] != -1:
                continue
            prev = claims.get(v)
            if prev is None or u < prev:
                claims[v] = u
    return claims


def _walk_to_src(parent: np.ndarray, node: int, src: int) -> list[int]:
    """Predecessor walk from ``node`` back to ``src``, then reversed."""
    path = [node]
    while path[-1] != src:
        path.append(int(parent[path[-1]]))
    path.reverse()
    return path


def bfs_path(
    offsets: np.ndarray,
    neighbors: np.ndarray,
    src: int,
    dst: int,
    max_hops: int | None = None,
) -> list[int] | None:
    """Reference level-BFS over the CSR (parity oracle for the Mojo lane).

    Returns the node-id path ``[src, ..., dst]`` or None when unreachable
    (or farther than ``max_hops``). The per-level parent rule
    (MIN-claimant) lives in :func:`_claim_parents`; the path therefore
    depends only on the parent tree, making the engine lanes (Python
    top-down, Mojo bottom-up) exactly parity-equal. Deterministic run to
    run: neighbor scans are ascending-sorted.
    """
    n = len(offsets) - 1
    if not (0 <= src < n and 0 <= dst < n):
        return None
    if src == dst:
        return [src]
    parent = np.full(n, -1, dtype=np.int64)
    parent[src] = src
    frontier = [src]
    level = 0
    while frontier and (max_hops is None or level < max_hops):
        claims = _claim_parents(frontier, offsets, neighbors, parent)
        if not claims:
            return None
        if dst in claims:
            parent[dst] = claims[dst]
            return _walk_to_src(parent, dst, src)
        for v, u in claims.items():
            parent[v] = u
        frontier = sorted(claims)
        level += 1
    return None


def _hit_fresh(manifest: dict, db_path: str | Path | None, live_gen: str | None) -> bool:
    """Cache-hit freshness: mirror load()'s rule exactly. With no db_path
    there is no generation to compare, so the manifest check is skipped
    (the DuckDB _build_meta gate in try_shortest_path still applies).
    Deriving this differently from the miss path silently degraded every
    warm call to the SQL fallback while the cold call used the lane."""
    if db_path is None:
        return True
    return live_gen is not None and live_gen == manifest.get("generation")


def _cache_lookup(
    cache_key: tuple[str, str | None],
    out: Path,
    db_path: str | Path | None,
    live_gen: str | None,
) -> tuple[dict, np.ndarray, np.ndarray, list[str], dict[str, int]] | None:
    """Cache hit or load, with pos derived and the entry stored on a fresh
    miss. Returns None when the substrate is stale or empty — the caller
    falls back to the SQL path. A stale HIT must not reload: it returns
    None directly, the same single-load semantics as the pre-split body."""
    cached = _CACHE.get(cache_key)
    if cached is not None:
        manifest, offsets, neighbors, names, pos = cached
        if not _hit_fresh(manifest, db_path, live_gen):
            return None
        return manifest, offsets, neighbors, names, pos
    manifest, offsets, neighbors, names, fresh = load(out, db_path=db_path)
    pos: dict[str, int] = {}
    if fresh and names:
        pos = {n: i for i, n in enumerate(names)}
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[cache_key] = (manifest, offsets, neighbors, names, pos)
    if not fresh or not names:
        return None
    return manifest, offsets, neighbors, names, pos


def _generation_ok(duckdb_con, manifest: dict) -> bool:
    """The DuckDB-side safety gate: this connection must have been built
    from the same edge set the CSR manifest was."""
    try:
        row = duckdb_con.execute(
            "SELECT value FROM _build_meta WHERE key = 'generation'"
        ).fetchone()
    except Exception:  # noqa: BLE001  # cold/odd con -> SQL path
        return False
    if row is None or str(row[0]) != str(manifest.get("generation")):
        return False
    return True


def try_shortest_path(
    duckdb_con,
    src: str,
    dst: str,
    max_hops: int = 5,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    db_path: str | Path | None = None,
) -> tuple[list[tuple[str, int]] | None, bool]:
    """CSR lane for ``query.shortest_path`` — unfiltered queries only.

    Safety gate: the lane activates only when the DuckDB connection's own
    ``_build_meta.generation`` matches the CSR manifest's generation —
    i.e. this connection was built from the same edge set the CSR was.
    A tmp-fixture connection (or any other store) fails the match and
    the caller falls back to the SQL path, so tests and cross-store calls
    can never read another graph's CSR.

    Returns ``(result, fresh)``: ``result`` is the
    ``[(name, hop_index)]`` path (or None = unreachable within hops),
    ``fresh`` False means "lane did not apply" — fall back to SQL.
    """
    out = Path(out_dir)
    # Freshness is re-read every call and is the cache key (see _CACHE), so
    # a rebuild lands on a fresh key and the stale substrate is never served.
    live_gen = _live_generation(db_path) if db_path is not None else None
    cache_key = (str(out), live_gen)
    entry = _cache_lookup(cache_key, out, db_path, live_gen)
    if entry is None:
        return None, False
    manifest, offsets, neighbors, names, pos = entry
    if not _generation_ok(duckdb_con, manifest):
        return None, False
    if src == dst:
        # CSR universe is edge ENDPOINTS, so a known-but-edgeless entity is
        # absent from ``pos`` and would read as "unreachable". The SQL path
        # pins src == dst -> [(src, 0)] for any name known to v_node, so
        # resolve it there first and match the contract (csr_lane_remediation
        # S3; the old comment here claimed SQL parity that did not hold).
        known = duckdb_con.execute("SELECT 1 FROM v_node WHERE name = ?", [src]).fetchone()
        return ([(src, 0)], True) if known is not None else (None, True)
    if src not in pos or dst not in pos:
        # edge-endpoint universe: a name with no edges cannot lie on any
        # path — genuinely unreachable, exactly what the SQL BFS returns.
        return None, True
    path = bfs_path(offsets, neighbors, pos[src], pos[dst], max_hops=max_hops)
    if path is None:
        return None, True
    return [(names[i], hop) for hop, i in enumerate(path)], True


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Build the CSR substrate (vault_scaling B-A)")
    ap.add_argument("--db", default="memory/research.db")
    ap.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--verify", action="store_true", help="verify checksum after build")
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    manifest = build(args.db, args.out)
    t1 = time.perf_counter()
    print(
        f"CSR: nodes={manifest['nodes']:,} directed_rows={manifest['directed_rows']:,} "
        f"edges={manifest['edges']:,} generation={manifest['generation']} "
        f"build={t1 - t0:.2f}s -> {args.out}"
    )
    if args.verify:
        _m, _off, _nbr, _names, fresh = load(args.out, db_path=args.db, verify_checksum=True)
        print(f"verify: checksum OK, fresh={fresh}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
