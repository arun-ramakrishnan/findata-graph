#!/usr/bin/env python3
"""Micro-bench for the entity-resolution ladder (entity_resolution_ladder_indexing).

Synthetic `_ResolverMap` (5,000 keys, token mix matching production shapes)
exercised with production-shaped call counts:
  - 729 `_qualifier_variants` calls (query-side strip + entity-side scan)
  - 94 `_fuzzy_suggestions` calls (token-set jaccard over map)

Guards the O(N) full-map scan regression: pre-indexing measured ~10.3 ms and
~69.6 ms per call respectively; post-indexing ~0.075 ms and ~2.9 ms. The
ceilings below are ~2 orders of magnitude above post-indexing per-call cost,
so a return to full-map scanning trips them hard while noise stays green.

Exits 0 on pass, 1 on ceiling breach.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from helpers.graph.derive_insights import (  # noqa: E402
    _ResolverMap,
    _fuzzy_suggestions,
    _qualifier_variants,
)

_QUALIFIERS = [
    "Company",
    "Group",
    "Limited",
    "Holdings",
    "Corporation",
    "Ventures",
    "Ltd",
    "Inc",
    "India",
    "International",
]
_WORDS = [
    "Titan",
    "Mitsubishi",
    "Reliance",
    "Tata",
    "Adani",
    "Airtel",
    "Vodafone",
    "HDFC",
    "ICICI",
    "SBI",
    "Infosys",
    "Wipro",
    "TCS",
    "Larsen",
    "Toubro",
    "Bharti",
    "Maruti",
    "Suzuki",
    "Hero",
    "Bajaj",
    "Cipla",
    "Dr",
    "Reddy",
    "Sun",
    "Pharma",
    "Metal",
    "Steel",
    "Coal",
    "Cement",
    "Power",
    "Energy",
    "Oil",
    "Gas",
    "Chemicals",
    "Fertilizer",
    "Plastics",
    "Auto",
    "Motor",
    "Electronics",
    "Software",
    "Cloud",
    "Retail",
    "Textiles",
    "Cement",
    "Infrastructure",
    "Defence",
    "Aerospace",
    "Renewable",
    "Solar",
    "Wind",
    "Banking",
    "Finance",
    "Insurance",
    "Realty",
    "Hospital",
    "Diagnostics",
    "FMCG",
    "Consumer",
    "Packaging",
    "Logistics",
    "Shipping",
    "Ports",
    "Aviation",
    "Hotels",
    "Tourism",
    "Media",
    "Entertainment",
    "Telecom",
    "Metals",
    "Mining",
    "Paints",
    "Tyres",
    "Bearings",
    "Forgings",
    "Castings",
    "Gems",
    "Jewellery",
    "Shipbuilding",
    "Railways",
    "Highways",
    "Water",
    "Waste",
    "Recycling",
    "EV",
    "Battery",
    "Charging",
    "Semiconductor",
    "Chip",
    "Design",
    "Manufacturing",
    "Services",
    "Consulting",
    "Audit",
    "Legal",
    "Education",
    "Agritech",
    "Healthtech",
    "Fintech",
    "D2C",
]

random.seed(42)
keys: list[str] = []
for _ in range(5000):
    w1 = random.choice(_WORDS)
    w2 = random.choice(_WORDS)
    key = f"{w1} {w2}"
    if random.random() < 0.25:
        key = f"{key} {random.choice(_QUALIFIERS)}"
    # lowercased keys mirror _build_resolver_map
    keys.append(key.lower())
resolver_map = _ResolverMap({k: k.title() for k in keys})

# Production-shaped call mixes: qualifier queries lean on common first tokens;
# fuzzy queries include near-misses that never resolve (the miss path).
qv_queries = [
    f"{random.choice(_WORDS).lower()} {random.choice(_WORDS).lower()}" for _ in range(729)
]
fz_queries = [
    random.choice(keys).replace(" ", "-", 1) if i % 3 == 0 else random.choice(keys)
    for i in range(94)
]

N_ROUNDS = 3


def measure(fn, queries):
    times = []
    for _ in range(N_ROUNDS):
        t0 = time.perf_counter()
        for q in queries:
            fn(q, resolver_map)
        times.append(time.perf_counter() - t0)
    return min(times) / len(queries) * 1e3  # ms per call (best round)


# warm both builds once
_qualifier_variants(qv_queries[0], resolver_map)
_fuzzy_suggestions(fz_queries[0], resolver_map)

qv_ms = measure(_qualifier_variants, qv_queries)
fz_ms = measure(_fuzzy_suggestions, fz_queries)

print(f"ladder_indexing: qv {qv_ms:.3f} ms/call (729 calls), fz {fz_ms:.3f} ms/call (94 calls)")

QV_CEIL_MS = 5.0  # measured 0.075 post-index / ~10.3 pre-index
FZ_CEIL_MS = 40.0  # measured 2.9 post-index / ~69.6 pre-index

ok = qv_ms < QV_CEIL_MS and fz_ms < FZ_CEIL_MS
print(f"ceilings: qv < {QV_CEIL_MS} ms, fz < {FZ_CEIL_MS} ms -> {'OK' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
