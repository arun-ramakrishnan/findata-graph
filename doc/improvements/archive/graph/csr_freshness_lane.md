---
title: "CSR fast-lane freshness (rebuild + freshness gate)"
status: executed
filed: "2026-10-06"
executed: "2026-10-06"
completed_md: "354"
area: "helpers/graph/csr.py helpers/graph/query.py Makefile helpers/validators/static_checks.py"
---

# CSR fast-lane freshness (rebuild + freshness gate)

## Motivation

The CSR substrate (memory/csr/) provides a ~500× speedup on unfiltered
shortest-path queries (1 ms vs 477 ms). However it was built on
2026-09-26 against SQLite generation 373511; the live generation is
414554. The substrate is therefore STALE and the fast lane silently
falls back to the SQL recursive-CTE path on every call.

There is currently no make target that rebuilds the substrate, and no
freshness gate that detects staleness. This means the regression persists
until an operator manually runs csr.build — and nobody knows it's
happening.

## Scope

In scope:
- Add make csr target that builds the CSR substrate into memory/csr/
- Add make csr-check / make csr-rebuild for freshness detection
- Optionally integrate into make advisory (warn if stale)

Out of scope:
- Making make csr automatic during make graph-rebuild (would change the
  rebuild contract; a separate arc to discuss with the operator)

## Implementation

### 1. Add make csr target

```makefile
csr: ## Build the CSR substrate (vault_scaling B-A)
    @python3 helpers/graph/csr.py --db memory/research.db --out memory/csr --verify

csr-rebuild: ## Rebuild CSR regardless of freshness
    @python3 helpers/graph/csr.py --db memory/research.db --out memory/csr --verify
```

### 2. Add freshness check

```makefile
csr-check: ## Check CSR freshness (exit 1 if stale)
    @python3 -c \
        'from helpers.graph import csr; from helpers.core.env import REPO_ROOT; \
         import sys; g = csr._live_generation(str(REPO_ROOT/"memory"/"research.db")); \
         ok = csr._generation_ok(g); sys.exit(0 if ok else 1)'
    @echo "CSR substrate is fresh"
```

### 3. Optional advisory integration

Add to helpers/validators/static_checks.py or create helpers/validators/csr_check.py

```python
# helpers/validators/csr_check.py
def check_csr_freshness() -> list[str]:
    from helpers.graph import csr
    from helpers.core.env import REPO_ROOT

    live_gen = csr._live_generation(str(REPO_ROOT / "memory" / "research.db"))
    manifest_gen = csr._manifest_generation(REPO_ROOT / "memory" / "csr")
    if manifest_gen != live_gen:
        return [f"CSR substrate stale: gen {manifest_gen} vs live {live_gen}"]
    return []
```

## Acceptance criteria

1. make csr builds the substrate in <1 s on the live graph
2. make csr-check returns exit 0 when fresh, exit 1 when stale
3. make advisory warns (but does not fail) when CSR is stale
4. csr try_shortest_path reports fresh=True after make csr
5. Unfiltered shortest-path latency returns to <10 ms after make csr

## Metrics

- CSR build cost: measured 0.23 s (nodes=22,063, edges=57,574)
- Fresh CSR shortest-path: ~1 ms (cold load ~9 ms)
- Stale fallback: 476.7 ms (SQL recursive CTE)
- Speedup when fresh: ~500×

## Timeline

- Build target: 15 min
- Freshness check: 10 min
- Optional advisory integration: 20 min
- Testing: 15 min

Total: ~1 hour
