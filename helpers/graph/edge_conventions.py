#!/usr/bin/env python3
"""One home for the graph_edges symmetric-type convention (range review M3/S5).

Three writers used to carry their own idea of which edge types are
symmetric: extract_relations promoted sidecars with an 8-type set,
triage_pending_relations accepted with a 5-type set, and derive_cited_in
pinned symmetric=0 for cited_in. The same accepted relation promoted via
different ingresses got opposite flags, and
UNIQUE(source, target, edge_type) cannot dedupe reversed pairs, so
contradictory duplicate rows landed (6 found 2026-10-11; e.g. the Tata
Group same_group pairs written in both orientations).

Operator ruling (2026-10-11, S5): invested_in / exposed_to / cited_in are
DIRECTIONAL — every one of the 3,136 live rows already carries
symmetric=0 — so the shared set is the narrower five. A symmetric type
stores ONE row with the pair canonicalised (sorted); directional types
store the row as written.
"""

from __future__ import annotations

SYMMETRIC_EDGE_TYPES = frozenset(
    {
        "competes_with",
        "jv_with",
        "same_group",
        "co_mentioned_in",
        "semantic_peer",
    }
)


def canonical_pair(source: str, target: str) -> tuple[str, str]:
    """Canonical storage order for a symmetric edge: the sorted pair.

    Symmetric writes MUST route through this so ('A','B') and ('B','A')
    collapse onto one row — UNIQUE(source, target, edge_type) sees the
    order. Directional writes must NOT call it.
    """
    return (source, target) if source <= target else (target, source)
