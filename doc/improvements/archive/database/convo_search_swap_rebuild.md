---
title: "convo_search.duckdb swap-rebuild"
status: executed
filed: "2026-10-06"
executed: "2026-10-06"
completed_md: "355"
area: "helpers/maintenance/rebuild_convo_search.py"
---

# convo_search.duckdb swap-rebuild

## Motivation

convo_search.duckdb is 648 MB but a full rewrite of the same data is
only 216.5 MB — 431.5 MB (67%) is reclaimable dead/fragmented space
from the incremental rebuild_convo_search DELETE+reinsert cycle.

Critically, DuckDB 1.5.6 VACUUM and VACUUM ANALYZE reclaim 0 bytes.
The only way to reclaim the space is a table rewrite — and the current
rebuild does not use a swap pattern (it writes in-place), so the space
never gets reclaimed.

This store is already the largest in the DB estate. If the corpus doubles
(row 9 of pending_improvs.md), it will be 1.3 GB with ~860 MB reclaimable.

## Scope

In scope:
- Port the rebuild to the graph lane's _rebuild_via_swap pattern
- Use a pid-tagged temp file + os.replace atomic swap
- Handle long writers (the ~2 min rebuild) with io_lock

Out of scope:
- Adding VACUUM (it does not work for this case; verified)

## Implementation

### 1. Adopt the swap pattern (already in helpers/graph/query.py)

```python
# helpers/maintenance/rebuild_convo_search.py (simplified)
import duckdb, os, shutil
from pathlib import Path


def _rebuild_convo_search_via_swap(db_path: Path | str, fresh: bool = False):
    """Build into a temp, then atomically swap. Mirrors helpers/graph/query.py."""
    tmp = db_path.with_name(f"{db_path.name}.rebuild-{os.getpid()}.tmp")
    tmp_lock = tmp.with_name(tmp.name + ".io.lock")

    # 1. Delete old temp (leftover from crash)
    tmp.unlink(missing_ok=True)

    # 2. Build into temp (RW connection)
    con = duckdb.connect(str(tmp))
    try:
        if not fresh:
            # Optional: copy from live first to reuse index/segment metadata
            live = Path(str(db_path))
            if live.exists():
                shutil.copyfile(str(live), str(tmp))
                # then DROP + CREATE tables on top

        # 3. Write all tables
        for t in ["convo_meta", "convo_search", "harvest_meta", "part_index"]:
            con.execute(f"DROP TABLE IF EXISTS {t}")
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM {t}")

        # 4. Clean close (no WAL left behind)
        con.close()
    except:
        tmp.unlink(missing_ok=True)
        raise

    # 5. Swap under io_lock (mirrors helpers/misc/duckdb_lock.py)
    with io_lock(db_path, exclusive=True):
        os.replace(tmp, db_path)
```

### 2. Update Makefile target

```makefile
convo-search-rebuild: ## Rebuild convo_search.duckdb (atomic swap)
    @python3 helpers/maintenance/rebuild_convo_search.py --swap
```

### 3. Add advisory check

```makefile
convo-search-check: ## Check convo_search size vs live row count
    @python3 -c 'import duckdb,os;d="memory/convo_search.duckdb";c=duckdb.connect(d,read_only=True);r=c.execute("SELECT sum(row_count) FROM duckdb_tables()").fetchone();size=os.path.getsize(d)/1e6;print(f"convo_search: {size:.0f} MB, {r[0]/1e6:.1f} rows");c.close()'
```

## Acceptance criteria

1. make convo-search-rebuild completes without leaving a WAL or temp file
2. Post-build file size is within 10% of measured payload (216.5 MB)
3. convo_query.py returns correct results after swap (verified by existing tests)
4. convo-search-check shows the reduced size

## Metrics

- Post-rebuild file size: 216.5 MB (target)
- Payload: 76,652 rows × (384-d floats + 105 MB text) ≈ 223 MB
- Space reclaimed: 431.5 MB (67%)

## Timeline

- Port to swap pattern: 30 min
- Update Makefile: 10 min
- Test on snapshot (not live): 20 min
- Run on live (once): 2 min rebuild + immediate reclaim

Total: ~1 hour (excluding the once-off live run)
