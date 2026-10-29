#!/usr/bin/env python3
"""Typed metapath layer — graph_relational_depth R1.

``connect(a, b, semantics=..., max_hops=...)`` answers "how are these two
companies actually connected" with a TYPED, hop-explained path over the
source ``graph_edges`` store, composed per relation family (ownership,
flow, affiliation, competition, joint, attention) instead of one
untyped hop at a time.

Design constraints (proposal §2/§5):

- READ-SIDE ONLY over ``memory/research.db`` — no schema migration, no
  derived-store dependency (the quack server is never touched; the
  derived ``e_all_und`` stores both orientations per dyad and reads ~2x
  the source census — R6 audit).
- Substrate choice is capability-gated: at 62,434 source edges a typed
  in-memory adjacency BFS is sub-millisecond. The untyped CSR bins
  (csr.py) carry no edge labels BY DESIGN (B-A non-goal), so per-family
  CSR promotion waits for a measured wall ("connect() p95 > 250 ms at
  depth 4") — the count-gated doctrine this proposal exists to retire.
- Adjacency is cached per ``db_meta.generation``; drift misses the
  cache and rebuilds (same doctrine as csr._CACHE).

Ownership semantics on live data (R6 audit findings baked in):

- vigil ``subsidiary_of`` rows are trusted ONLY when the filing's
  relationship text is in the strict positive vocabulary — 6,120 of
  10,677 live. The rest describe third-party parentage ("Subsidiary/JV
  of an Entity with Significant Influence on Holding Company") or
  subsidiary-ADJACENT parties ("KMP of Subsidiary", "Related Party of
  Subsidiary"); trusting them flattens group siblings into false
  child→parent chains (TCS→Rallis India is a live example).
- both vigil classes already store child→parent (sub_fwd:
  counter→filer; sub_rev: filer→counter) — trusted rows need no
  re-orientation.
- prose ``subsidiary_of`` (source_ref not ``vigil:``) is trusted as-is.
- ``acquired`` is stored acquirer→acquiree (JSW Paints→Akzo Nobel
  India, 59 prose rows) and joins ownership REVERSED.
- ``invested_in`` is NOT routed into ownership: its weights are ranking
  weights (1.0/3.0/100.0 live), not stakes, so "majority invested_in"
  is not derivable (R6).

Hyper semantics (R5): ``connect(a, b, semantics="hyper")`` / ``hyper_path``
answers over the INCIDENCE store instead of graph_edges — two entities
are s-adjacent when they share a hyperedge; s-distance counts hyperedges
traversed. Distance 1 answers with ALL shared hyperedges ("A and B
share: group Tata, edition The Chatter #69, sub_sector Pharma"); longer
walks go via bridging entities with min-(label, type) claimant
determinism (the same convention as the dyadic BFS). Primitive lane
only — 1.7K hyperedges / 10.1K incidences is sub-millisecond; the HGX
``s_walk`` shelf stays unwired until a measured wall asks for it.

Flow semantics (R6 verdict): the RPT sync coins BOTH trade directions
as oriented ``supplier_to`` edges (source is always the supplier);
``customer_of`` is the prose extractor's mirror and is normalised into
the same orientation here. Known topology caveat: 672 cross-period
pairs keep one direction with the reverse only in properties.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from helpers.core import jsonio  # noqa: E402  (orjson parse cutover; dumps stay stdlib per db_scaling_tuning §6.3 criteria)
from helpers.core.db import connect as _db_connect  # noqa: E402  # aliased: THIS module's connect() would shadow it

DB_PATH = _PROJECT_ROOT / "memory" / "research.db"
_VIGIL_PREFIX = "vigil:"

# Filing relationship texts that mean "the counterparty is OUR subsidiary
# (or, for 'holding company', OUR parent)". Case-folded + whitespace-
# normalised before membership. Everything else vigil is treated as a
# sibling/adjacent-party description, not control.
_STRICT_OWNERSHIP_REL = frozenset(
    {
        "subsidiary",
        "subsidiaries",
        "subsidiary company",
        "wholly owned subsidiary",
        "wholly owned subsidiaries",
        "wholly-owned subsidiary",
        "wholly-owned subsidiaries",
        "wholly owned subsidiary company",
        "step down subsidiary",
        "stepdown subsidiary",
        "subsidiary of the company",
        "holding company",
    }
)

# Undirected families: (semantic name -> edge types).
UNDIRECTED_FAMILIES: dict[str, tuple[str, ...]] = {
    "affiliation": ("same_group", "listed_on_index", "listed_in", "belongs_to"),
    "competition": ("competes_with",),
    "joint": ("jv_with",),
    "attention": ("co_mentioned_in", "cited_in"),
}

# Edge types outside every family; they join only semantics="any".
_PERIPHERAL_TYPES = ("rated_by", "regulated_by", "approved_by", "exposed_to", "semantic_peer")

SEMANTICS = ("ownership", "flow", *UNDIRECTED_FAMILIES, "hyper", "any")


def _trust_ownership_edge(source_ref: str, properties: str | None) -> bool:
    """vigil subsidiary_of rows pass only on strict positive vocabulary."""
    if not source_ref.startswith(_VIGIL_PREFIX):
        return True
    try:
        rel = jsonio.loads(properties or "{}").get("relationship") or ""
    except TypeError, ValueError:
        return False
    return " ".join(str(rel).lower().split()) in _STRICT_OWNERSHIP_REL


class _Adjacency:
    """Per-generation adjacency bundle (built once, cached)."""

    def __init__(self) -> None:
        # directed families: name -> [(out-neighbour, edge_type)]
        self.out: dict[str, dict[str, list[tuple[str, str]]]] = {"ownership": {}, "flow": {}}
        self.rev: dict[str, dict[str, list[tuple[str, str]]]] = {"ownership": {}, "flow": {}}
        self.undirected: dict[str, dict[str, list[tuple[str, str]]]] = {
            name: {} for name in UNDIRECTED_FAMILIES
        }
        self.any: dict[str, list[tuple[str, str]]] = {}

    def add_out(self, family: str, src: str, dst: str, etype: str) -> None:
        self.out[family].setdefault(src, []).append((dst, etype))
        self.rev[family].setdefault(dst, []).append((src, etype))
        self._add_any(src, dst, etype)

    def add_undirected(self, family: str, a: str, b: str, etype: str) -> None:
        fam = self.undirected[family]
        fam.setdefault(a, []).append((b, etype))
        fam.setdefault(b, []).append((a, etype))
        self._add_any(a, b, etype)

    def _add_any(self, src: str, dst: str, etype: str) -> None:
        self.any.setdefault(src, []).append((dst, etype))
        self.any.setdefault(dst, []).append((src, etype))


_CACHE: dict[tuple[str, str | None], _Adjacency] = {}
_HYPER_CACHE: dict[tuple[str, str | None, str | None], _Hyper] = {}


def clear_cache() -> None:
    """Drop the memoised adjacency + hyper stores (tests, post-rebuild callers)."""
    _CACHE.clear()
    _HYPER_CACHE.clear()


def _live_generation(db_path: str | Path) -> str | None:
    con = _db_connect(db_path, read_only=True, row_factory=None)
    try:
        row = con.execute("SELECT value FROM db_meta WHERE key='generation'").fetchone()
        return None if row is None else str(row[0])
    except Exception:  # noqa: BLE001  # no db_meta / pre-migration store
        return None
    finally:
        con.close()


def _properties_column(con) -> str:
    cols = {r[1] for r in con.execute("PRAGMA table_info(graph_edges)").fetchall()}
    return "properties" if "properties" in cols else "properties_json"


def _build_adjacency(db_path: str | Path) -> _Adjacency:
    adj = _Adjacency()
    con = _db_connect(db_path, read_only=True, row_factory=None)
    try:
        prop_col = _properties_column(con)
        rows = con.execute(
            f"SELECT source, target, edge_type, source_ref, {prop_col} "  # noqa: S608
            "FROM graph_edges"
        ).fetchall()
    finally:
        con.close()
    for src, dst, etype, ref, props in rows:
        if etype == "subsidiary_of":
            if _trust_ownership_edge(ref, props):
                adj.add_out("ownership", src, dst, etype)
        elif etype == "acquired":
            adj.add_out("ownership", dst, src, etype)  # stored acquirer→acquiree
        elif etype == "supplier_to":
            adj.add_out("flow", src, dst, etype)
        elif etype == "customer_of":
            adj.add_out("flow", dst, src, etype)  # mirror into supplier orientation
        else:
            fam = next(
                (f for f, types in UNDIRECTED_FAMILIES.items() if etype in types),
                None,
            )
            if fam is not None:
                adj.add_undirected(fam, src, dst, etype)
            elif etype in _PERIPHERAL_TYPES:
                adj._add_any(src, dst, etype)
    return adj


def _adjacency(db_path: str | Path) -> _Adjacency:
    gen = _live_generation(db_path)
    key = (str(db_path), gen)
    hit = _CACHE.get(key)
    if hit is None:
        hit = _build_adjacency(db_path)
        _CACHE.clear()  # single-entry: one live store, tests rebuild per tmp path
        _CACHE[key] = hit
    return hit


def ownership_parents(db_path: str | Path = DB_PATH) -> dict[str, list[tuple[str, str]]]:
    """Trusted ownership parent map — child → [(parent, edge_type)].

    The strict-trust subsidiary_of ∪ reversed-acquired adjacency that
    ``connect(semantics='ownership')`` walks, exposed for derive lanes
    (graph_relational_depth R3 ``ultimate_parent`` closure) so closure
    and path search share ONE trust filter and cannot drift.
    """
    return _adjacency(db_path).out["ownership"]


# --------------------------------------------------------------------------- #
# R5 — hyper s-paths over the incidence store (hyper_edges / hyper_incidences)
# --------------------------------------------------------------------------- #
class _Hyper:
    """Bipartite incidence store: entity ↔ hyperedge, cached per
    generation + incidence stamp (COUNT:MAX(rowid) — hyper refreshes
    invalidate the cache without depending on graph_edges triggers)."""

    def __init__(self, rows: list[tuple[int, str, str, str]]) -> None:
        self.of: dict[str, list[tuple[int, str, str]]] = {}
        self.members: dict[int, list[str]] = {}
        for eid, entity, etype, label in rows:
            self.of.setdefault(entity, []).append((eid, etype, label or ""))
            self.members.setdefault(eid, []).append(entity)
        for lst in self.of.values():
            lst.sort(key=lambda t: (t[2], t[1], t[0]))
        for lst in self.members.values():
            lst.sort()


def _hyper_stamp(con) -> str | None:
    try:
        n, mx = con.execute(
            "SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM hyper_incidences"
        ).fetchone()
        return f"{n}:{mx}"
    except Exception:  # noqa: BLE001  # no incidence store -> unkeyed
        return None


def _build_hyper(db_path: str | Path) -> _Hyper:
    con = _db_connect(db_path, read_only=True, row_factory=None)
    try:
        rows = con.execute(
            "SELECT i.edge_id, i.entity_name, e.edge_type, e.label "
            "FROM hyper_incidences i JOIN hyper_edges e ON e.id = i.edge_id"
        ).fetchall()
    except Exception:  # noqa: BLE001  # no hyper tables -> empty store
        rows = []
    finally:
        con.close()
    return _Hyper(rows)


def _hyper(db_path: str | Path) -> _Hyper:
    con = _db_connect(db_path, read_only=True, row_factory=None)
    try:
        gen = _live_generation(db_path)
        stamp = _hyper_stamp(con)
    finally:
        con.close()
    key = (str(db_path), gen, stamp)
    hit = _HYPER_CACHE.get(key)
    if hit is None:
        hit = _build_hyper(db_path)
        _HYPER_CACHE.clear()  # single-entry, same doctrine as _adjacency
        _HYPER_CACHE[key] = hit
    return hit


def shared_hyperedges(a: str, b: str, db_path: str | Path = DB_PATH) -> list[tuple[str, str]]:
    """All hyperedges holding BOTH entities, sorted (edge_type, label) —
    the s-distance-1 explanation ("A and B share: group Tata, …")."""
    hy = _hyper(db_path)
    ea = {eid for eid, _t, _label in hy.of.get(a, ())}
    return sorted({(etype, label) for eid, etype, label in hy.of.get(b, ()) if eid in ea})


def _hyper_answer(
    a: str, b: str, s_distance: int, shared: list[tuple[str, str]], walk: list[str]
) -> dict:
    """Uniform result shape for every ``hyper_path`` termination."""
    return {
        "semantics": "hyper",
        "a": a,
        "b": b,
        "s_distance": s_distance,
        "shared": shared,
        "walk": walk,
        "n_hops": s_distance,
    }


def _hyper_claims(
    hy: _Hyper,
    frontier: list[str],
    parent: dict[str, tuple[str, str, str]],
) -> dict[str, tuple[str, str, str]]:
    """One BFS level over entity -> hyperedge -> entity.

    min-(label, edge_type) claimant per reached node, mirroring the
    dyadic BFS convention; already-claimed nodes and the origin's own
    incidences are skipped.
    """
    claims: dict[str, tuple[str, str, str]] = {}
    for node in frontier:
        for _eid, etype, label in hy.of.get(node, ()):
            for other in hy.members.get(_eid, ()):
                if other == node or other in parent:
                    continue
                claim = (label, etype, node)
                if other not in claims or claim < claims[other]:
                    claims[other] = claim
    return claims


def _hyper_chain(parent: dict[str, tuple[str, str, str]], b: str, a: str) -> list[str]:
    """Walk parent pointers b -> a into the alternating entity/bridge walk."""
    chain: list[str] = [b]
    cur = b
    while cur != a:
        label, etype, prev = parent[cur]
        chain.append(f"{etype}:{label}")
        chain.append(prev)
        cur = prev
    chain.reverse()
    return chain


def hyper_path(a: str, b: str, *, max_hops: int = 3, db_path: str | Path = DB_PATH) -> dict | None:
    """Shortest s-walk between two entities over the incidence store (R5).

    s-distance counts hyperedges traversed (1 = the pair shares
    hyperedges directly). Deterministic: min-(label, edge_type) claimant
    per level, mirroring the dyadic BFS convention.

    Returns ``{"semantics": "hyper", "a", "b", "s_distance", "shared",
    "walk", "n_hops"}`` — at distance 1 ``shared`` lists every shared
    hyperedge and ``walk`` is ``[a, b]``; at greater distances ``walk``
    alternates entity / ``"type:label"`` bridging hyperedges and
    ``shared`` is empty. ``a == b`` answers zero-hop; unreachable within
    ``max_hops`` (or an entity with no incidences) answers None.
    """
    if a == b:
        return _hyper_answer(a, b, 0, [], [a])
    hy = _hyper(db_path)
    if not hy.of.get(a) or not hy.of.get(b):
        return None
    shared = shared_hyperedges(a, b, db_path)
    if shared:
        return _hyper_answer(a, b, 1, shared, [a, b])
    # BFS over entity -> hyperedge -> entity. Distance 1 was answered
    # above, so iteration 1's reach is redundant for b — it feeds the
    # bridge frontier. max_hops iterations cover s-distances up to
    # max_hops. The a-seed is never unpacked (the chain walk stops at a).
    parent: dict[str, tuple[str, str, str]] = {a: ("", "", a)}
    frontier = [a]
    for _level in range(1, max_hops + 1):
        claims = _hyper_claims(hy, frontier, parent)
        if not claims:
            return None
        parent.update(claims)
        if b in claims:
            chain = _hyper_chain(parent, b, a)
            return _hyper_answer(a, b, len(chain) // 2, [], chain)
        frontier = sorted(claims)
    return None


def _bfs(
    adj: dict[str, list[tuple[str, str]]],
    src: str,
    dst: str,
    max_hops: int,
) -> list[tuple[str, str, str]] | None:
    """Deterministic level-BFS; min-(name, edge_type) claimant per level.

    Returns [(node, next_node, edge_type)] hops, or None when unreachable
    within ``max_hops``. Neighbour lists are sorted at claim time so the
    path depends only on the parent tree, never dict order.
    """
    if src == dst:
        return []
    parent: dict[str, tuple[str, str]] = {src: (src, "")}
    frontier = [src]
    level = 0
    while frontier and level < max_hops:
        claims: dict[str, tuple[str, str]] = {}
        for u in frontier:
            for v, et in sorted(adj.get(u, ())):
                if v in parent:
                    continue
                cand = (u, et)
                prev = claims.get(v)
                if prev is None or cand < (prev[0], prev[1]):
                    claims[v] = cand
        if not claims:
            return None
        if dst in claims:
            hops: list[tuple[str, str, str]] = []
            node = dst
            while node != src:
                p, et = parent[node] if node in parent else claims[node]
                hops.append((p, node, et))
                node = p
            hops.reverse()
            return hops
        parent.update(claims)
        frontier = sorted(claims)
        level += 1
    return None


def _explain(hops: list[tuple[str, str, str]], semantics: str, direction: str, a: str) -> dict:
    return {
        "semantics": semantics,
        "path": [a, *(h[1] for h in hops)],
        "hops": [
            {"src": s, "dst": d, "edge_type": et, "direction": direction} for s, d, et in hops
        ],
        "n_hops": len(hops),
    }


def connect(  # noqa: C901  # the per-family search ladder reads flat, not nested
    a: str,
    b: str,
    semantics: str | None = None,
    max_hops: int = 3,
    db_path: str | Path = DB_PATH,
) -> dict | None:
    """Typed connection between ``a`` and ``b`` with hop explanations.

    ``semantics`` selects the relation family (default ``"any"`` = union
    of all families + peripheral types):

    - ``ownership`` (directed child→parent): trusted subsidiary_of +
      reversed acquired. Searched up from ``a`` (``b`` controls ``a``)
      then up from ``b``.
    - ``flow`` (directed supplier→customer): searched downstream from
      ``a`` (``a`` supplies into ``b``'s chain) then upstream.
    - undirected families (affiliation/competition/joint/attention).
    - ``hyper`` (R5): s-walk over the incidence store — delegates to
      :func:`hyper_path` and returns its explanation shape (``shared``
      hyperedges at s-distance 1, bridging-entity walk beyond).

    Returns ``{"semantics", "path", "hops": [{src,dst,edge_type,
    direction}], "n_hops"}`` or None when unreachable within
    ``max_hops``. ``a == b`` returns a zero-hop path. Unknown semantics
    raises ValueError. On directed families ``direction`` names the
    traversal relative to the STORED edge orientation: ownership
    ``up`` (child→parent) / ``down`` (descendant walk, against the
    stored edge); flow ``downstream`` / ``upstream``.
    """
    sem = semantics or "any"
    if sem not in SEMANTICS:
        raise ValueError(f"unknown semantics {sem!r}; one of {SEMANTICS}")
    if sem == "hyper":
        return hyper_path(a, b, max_hops=max_hops, db_path=db_path)
    adj = _adjacency(db_path)
    if sem in adj.out:  # directed family: forward then reverse
        hops = _bfs(adj.out[sem], a, b, max_hops)
        direction = "up" if sem == "ownership" else "downstream"
        if hops is not None:
            return _explain(hops, sem, direction, a)
        hops = _bfs(adj.rev[sem], a, b, max_hops)
        if hops is not None:
            direction = "down" if sem == "ownership" else "upstream"
            return _explain(hops, sem, direction, a)
        return None
    if sem == "any":
        hops = _bfs(adj.any, a, b, max_hops)
        return None if hops is None else _explain(hops, sem, "", a)
    hops = _bfs(adj.undirected[sem], a, b, max_hops)
    return None if hops is None else _explain(hops, sem, "", a)


def _fmt(result: dict) -> str:
    lines = [f"[{result['semantics']}] {result['n_hops']} hop(s)", result["path"][0]]
    for h in result["hops"]:
        d = f" ({h['direction']})" if h["direction"] else ""
        lines.append(f"  —{h['edge_type']}{d}→ {h['dst']}")
    return "\n".join(lines)


def _fmt_hyper(result: dict) -> str:
    lines = [f"[hyper] s-distance {result['s_distance']}"]
    if result["s_distance"] == 0:
        lines.append(f"  {result['a']} (self)")
    elif result["s_distance"] == 1:
        lines.append(f"  {result['a']} ↔ {result['b']} share {len(result['shared'])} hyperedge(s):")
        for etype, label in result["shared"]:
            lines.append(f"    {etype}: {label}")
    else:
        lines.append("  " + " —s→ ".join(result["walk"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--semantics", default="any", choices=SEMANTICS)
    ap.add_argument("--max-hops", type=int, default=3)
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = connect(args.a, args.b, args.semantics, args.max_hops, args.db)
    if result is None:
        print(f"[{args.semantics}] no connection within {args.max_hops} hop(s)")
        return 1
    if args.json:
        print(json.dumps(result, indent=1))
    elif result.get("semantics") == "hyper":
        print(_fmt_hyper(result))
    else:
        print(_fmt(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
