#!/usr/bin/env python3
"""Server-side whole-graph layout — precomputed cloud positions (lane 3).

Companions: ``doc/improvements/proposals/graph_rendering_overhaul.md`` (S0
measured fcose at 55 s client-side at the 1,649/19,261 scale; this module
turns that into a snapshot-time job with an edge-set hash gate, the same
pattern as ``helpers/core/embed_matrix.py``). The engine is a numpy
ForceAtlas2 variant (multigraph edge weights, degree-sized nodes,
swing/traction damping) — chosen over headless fcose so the Python deploy
stays Node-free; the positions also seed any future sigma.js lane (1+3
composition) since WebGL renderers need an initial position set anyway.

Storage: ``memory/graph_layout.json`` sidecar —
``{"edge_set_hash", "engine", "engine_params", "computed_at",
"node_count", "edge_count", "positions": {id: [x, y]}}``. Regenerates only
when the edge set changes; ``POST /api/graph/refresh`` calls the gate after
rebuilding the DuckDB cache, and ``GET /api/graph/positions`` self-heals
(stale sidecar → recompute) so direct SQLite writes without a refresh
still converge.

Determinism: sorted-node circular init + seeded jitter, then purely
deterministic float ops — same edge set, same positions. Consumers get
stable cross-visit coordinates (the thing neither concentric nor fcose
gave, per the proposal's motivation).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
POSITIONS_PATH = _REPO_ROOT / "memory" / "graph_layout.json"

# Mirror of the S0 harness params (graph_measure.mjs lane 1) so the server
# engine and the measured browser FA2 stay comparable.
ENGINE = "fa2-numpy"
ENGINE_PARAMS: dict[str, Any] = {
    "iterations": 600,
    "scaling_ratio": 10.0,
    "gravity": 1.0,
    "slow_down": 2.0,
    "seed": 42,
}

# O(n^2) chunked repulsion: at ~8k nodes the snapshot-time job starts to
# dominate a refresh unacceptably — refuse loudly instead of stalling it.
_MAX_NODES = 8000
_CHUNK = 256


def edge_set_hash(conn: Any) -> tuple[str, list[str], list[tuple[str, str, str]]]:
    """Hash the whole-graph edge set exactly as ``/api/graph/cloud`` reads it.

    The cloud endpoint derives nodes from edge endpoints (incident nodes
    only), so the hash covers the same triples + incident ids — a filter or
    entity-text edit cannot move it, only edge/topology changes can.
    """
    rows = conn.execute("SELECT source, target, edge_type FROM graph_edges").fetchall()
    edges = sorted((r[0], r[1], r[2]) for r in rows)
    incident = sorted({name for e in edges for name in (e[0], e[1])})
    h = hashlib.sha256()
    h.update("\x1e".join(incident).encode())
    h.update(b"\x1f")
    h.update("\x1e".join(f"{s}\x1d{t}\x1d{et}" for s, t, et in edges).encode())
    return h.hexdigest(), incident, edges


def compute_positions_fa2(  # noqa: C901  (fallback chain: cached → components → concentric)
    nodes: list[str],
    edges: list[tuple[str, str, str]],
    *,
    iterations: int = ENGINE_PARAMS["iterations"],
    scaling_ratio: float = ENGINE_PARAMS["scaling_ratio"],
    gravity: float = ENGINE_PARAMS["gravity"],
    slow_down: float = ENGINE_PARAMS["slow_down"],
    seed: int = ENGINE_PARAMS["seed"],
) -> dict[str, list[int]]:
    """Deterministic ForceAtlas2 positions for the whole-graph cloud.

    Vectorized numpy, chunked O(n^2) repulsion (no Barnes-Hut at cloud
    scale), multigraph edges aggregated into pair weights, node radii from
    a sqrt-degree curve so hubs push neighbours apart (FA2 adjustSizes
    behaviour). Returns ``{node_id: [x, y]}`` with rounded ints, centred
    and normalized to a stable output radius.

    Raises ValueError above ``_MAX_NODES`` (refresh hook logs and skips;
    the endpoint surfaces 503) — grow into Barnes-Hut before lifting it.
    """
    n = len(nodes)
    if n == 0:
        return {}
    if n > _MAX_NODES:
        raise ValueError(f"graph layout refuses {n} nodes (ceiling {_MAX_NODES})")

    index = {name: i for i, name in enumerate(nodes)}
    k = scaling_ratio

    # Deterministic init: circle by sorted index + seeded jitter. float32
    # throughout the solve: half the memory traffic of the elementwise
    # passes, and layout precision beyond 1e-3 is meaningless after the
    # int rounding at the end.
    rng = np.random.default_rng(seed)
    angles = 2.0 * np.pi * np.arange(n) / n
    radius = max(50.0, np.sqrt(n) * k * 0.5)
    pos = np.column_stack([np.cos(angles), np.sin(angles)]) * radius
    pos += rng.normal(0.0, radius * 0.05, size=(n, 2))
    pos = pos.astype(np.float32)

    # Degree → node radius (sqrt curve, 2..12 units) for overlap avoidance.
    degree = np.zeros(n, dtype=np.float64)
    valid_edges: list[tuple[int, int]] = []
    for s, t, _et in edges:
        si, ti = index.get(s), index.get(t)
        if si is None or ti is None or si == ti:
            continue
        valid_edges.append((si, ti))
        degree[si] += 1
        degree[ti] += 1
    sizes = (2.0 + 10.0 * np.sqrt(degree) / max(1.0, np.sqrt(degree.max() if n else 1.0))).astype(
        np.float32
    )

    # Multigraph → weighted unique pairs (parallel edges strengthen pull).
    if valid_edges:
        pair_w: dict[tuple[int, int], float] = {}
        for si, ti in valid_edges:
            key = (si, ti) if si < ti else (ti, si)
            pair_w[key] = pair_w.get(key, 0.0) + 1.0
        pairs = np.array(list(pair_w.keys()), dtype=np.int64)
        weights = np.array(list(pair_w.values()), dtype=np.float64)
    else:
        pairs = np.zeros((0, 2), dtype=np.int64)
        weights = np.zeros(0, dtype=np.float64)

    force_prev = np.zeros((n, 2), dtype=np.float32)
    eps = np.float32(1e-4)

    # Repulsion is linear in the separation vector:
    #   F_i = Σ_j f_ij (pos_i − pos_j) = (Σ_j f_ij) pos_i − (f @ pos)
    # so the (chunk, n, 2) delta tensor collapses into two BLAS matmuls
    # (distances via |a|²+|b|²−2ab) — exact same physics, matmul-bound
    # instead of allocation-bound (130 s → ~10 s at the 1.6k/19k scale).
    for _ in range(iterations):
        force = np.zeros((n, 2), dtype=np.float32)
        sq = (pos * pos).sum(-1)
        for lo in range(0, n, _CHUNK):
            hi = min(lo + _CHUNK, n)
            block = pos[lo:hi]
            d2 = sq[lo:hi, None] + sq[None, :] - 2.0 * (block @ pos.T)
            np.maximum(d2, eps, out=d2)
            d = np.sqrt(d2)
            factor = (k * k) / d2
            factor[np.arange(hi - lo), np.arange(lo, hi)] = 0.0  # no self-repulsion
            overlap = (sizes[lo:hi, None] + sizes[None, :]) - d
            np.clip(overlap, 0.0, None, out=overlap)
            factor += overlap * (k / d2) * 8.0
            force[lo:hi] = block * factor.sum(-1)[:, None] - factor @ pos

        # Attraction along weighted unique pairs: F = w * d^2 / k toward
        # the neighbour (opposes the separation direction). bincount
        # scatter (np.add.at is an order slower at 19k pairs × 600 iters).
        if len(pairs):
            ui, vi = pairs[:, 0], pairs[:, 1]
            d_vec = pos[ui] - pos[vi]
            d_len = np.sqrt((d_vec * d_vec).sum(-1)) + eps
            f_mag = weights * (d_len * d_len) / k
            f_vec = d_vec * (f_mag / d_len)[:, None]
            for dim in (0, 1):
                force[:, dim] -= np.bincount(ui, weights=f_vec[:, dim], minlength=n)
                force[:, dim] += np.bincount(vi, weights=f_vec[:, dim], minlength=n)

        # Gravity to the origin: linear in distance (FA2 non-strong mode).
        d_center = np.sqrt((pos * pos).sum(-1)) + eps
        force -= pos * (gravity * k / d_center)[:, None]

        # Swing/traction damping (FA2): converged hubs stop moving, loose
        # satellites keep travelling. Displacement capped per node.
        f_mag_node = np.sqrt((force * force).sum(-1)) + eps
        p_mag_node = np.sqrt((force_prev * force_prev).sum(-1)) + eps
        swing = np.abs(f_mag_node - p_mag_node)
        traction = 0.5 * (f_mag_node + p_mag_node)
        factor = slow_down * traction.sum() / (1.0 + np.sqrt(swing.sum()) + eps)
        node_factor = factor * (1.0 + np.sqrt(swing))
        step = force * (node_factor / f_mag_node)[:, None]
        step_mag = np.sqrt((step * step).sum(-1))
        cap = np.maximum(1.0, sizes * 0.5)
        step *= np.minimum(1.0, cap / (step_mag + eps))[:, None]
        pos += step
        force_prev = force

    # Normalize: centre on the mean, scale the 95th-percentile radius to a
    # stable canvas extent, round to ints for a compact sidecar.
    pos -= pos.mean(axis=0)
    r = np.sqrt((pos * pos).sum(-1))
    target = 1200.0
    p95 = np.percentile(r, 95) if n > 1 else 1.0
    if p95 > eps:
        pos *= target / p95
    return {name: [int(round(v)) for v in pos[index[name]]] for name in nodes}


def load_or_compute_positions(
    conn: Any,
    *,
    path: Path | str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hash-gated read/write of the positions sidecar.

    Returns the full sidecar payload (``positions`` + meta + ``recomputed``
    flag). Computes only when the stored ``edge_set_hash`` differs from the
    live edge set (or the file is missing/corrupt) — the embed-matrix
    refresh pattern. The write is atomic (tmp file + ``os.replace``) so a
    crashed compute never leaves a half-written sidecar behind.
    """
    sidecar = Path(path) if path is not None else POSITIONS_PATH
    digest, incident, edges = edge_set_hash(conn)

    stale = True
    stored: dict[str, Any] = {}
    if sidecar.exists():
        try:
            stored = json.loads(sidecar.read_text())
            stale = (
                stored.get("edge_set_hash") != digest
                or stored.get("engine") != ENGINE
                or stored.get("engine_params") != (params or ENGINE_PARAMS)
            )
        except json.JSONDecodeError, OSError:
            stale = True
    if not stale:
        return {**stored, "recomputed": False}

    positions = compute_positions_fa2(incident, edges, **(params or {}))
    payload = {
        "edge_set_hash": digest,
        "engine": ENGINE,
        "engine_params": params or ENGINE_PARAMS,
        "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "node_count": len(incident),
        "edge_count": len(edges),
        "positions": positions,
        "recomputed": True,
    }
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(sidecar.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.chmod(tmp, 0o644)  # mkstemp is 0600; match the other sidecars
        os.replace(tmp, sidecar)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return payload
