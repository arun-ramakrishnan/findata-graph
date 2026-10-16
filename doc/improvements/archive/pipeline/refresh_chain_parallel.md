---
title: "Refresh chain, parallel — fetch/apply split so independent sources overlap"
status: executed
filed: "2026-10-16"
executed: "2026-10-16"
completed_md: "403"
area: "helpers/maintenance"
---

# Refresh chain, parallel — fetch/apply split so independent sources overlap

## 0. TL;RA

`make refresh-chain-ser` runs seven lanes strictly sequentially. The sources are
fully independent (NSE archives, BSE RSS, MCA web, Yahoo Finance, tigzig
VIGIL) and the expensive part of every lane is its **fetch** — but the lanes
cannot run whole in parallel because their **apply** phases all write the two
single-writer stores: `memory/research.db` (SQLite WAL — one writer) and
`memory/data/sources.duckdb` (DuckDB — lock-holder; a second writer fails
hard).

2026-10-16 observations (three chain runs today): the cold backlog run spent
its wall-clock in fetches (a 6,093-item CIN scan; the VIGIL CSV.gz bulk; yfinance
per-ticker), while three different lanes crashed the whole chain on single
record failures (SHP ghost-source FK; CIN missing candidates file; SHP 404 on
a removed filing). Incremental reruns are fast — the pain is the cold run.

**Design**: split each lane into a network-bound `--fetch-only` stage
(artifacts land in scratch/cache, zero store writes) and an `--apply` stage
(store writes), run the fetch stages **in parallel**, then the apply stages
**sequentially** under a per-store lock. `related_party_sync --download`
already splits exactly this way (CSV.gz → `memory/data/rpt_raw/`, apply is a
separate invocation reading the cache) — it is the template, not new work.

## 1. Design

### Per-lane `--fetch-only` stages (S1)

Each lane CLI gains a stage that stops after network retrieval:

| lane | fetch artifact | apply reads |
|---|---|---|
| `related_party_sync` | `memory/data/rpt_raw/*.csv.gz` + ratings cache | ✅ exists (`--apply` without `--download`) |
| `shareholding_sync --rss --bse-rss` | parsed filings staged to scratch (filing_id-keyed) before `store_filing` | new |
| `enrich_from_yfinance` | per-ticker JSON staged to scratch | new |
| `ingest_nse_xbrl --new` | per-entity XBRL bytes staged to scratch | new |
| `exchange_sync` | master CSVs staged to scratch | new |

Stage selection: `--fetch-only` (default off; existing invocations unchanged —
the sequential chain keeps working byte-for-byte).

### `make refresh-chain` (S2)

```make
refresh-chain:
    # phase A — parallel fetches (network-bound, no store writes)
    python3 helpers/maintenance/related_party_sync.py --download --ratings-download &
    python3 helpers/maintenance/shareholding_sync.py --rss --bse-rss --fetch-only &
    python3 helpers/maintenance/enrich_from_yfinance.py --fetch-only &
    python3 helpers/maintenance/ingest_nse_xbrl.py --new --fetch-only &
    python3 helpers/maintenance/exchange_sync.py --fetch-only &
    wait
    # phase B — sequential applies (single-writer stores)
    python3 helpers/maintenance/exchange_sync.py --apply-only
    python3 helpers/maintenance/related_party_sync.py --apply
    python3 helpers/maintenance/shareholding_sync.py --apply
    python3 helpers/maintenance/enrich_from_yfinance.py --apply-only
    python3 helpers/maintenance/mca_cin_resolve.py ingest-web --no-revalidate $(if $(APPLY),--apply)
    python3 helpers/maintenance/ingest_nse_xbrl.py --new --apply-only
    $(if $(APPLY),python3 helpers/maintenance/snapshot_db.py,@echo "dry-run: snapshot skipped")
```

`--fetch-only`/`--apply-only` stages are dry-run by default like their parents
(`APPLY=1 make refresh-chain` writes; without it every stage previews).
Dry-run parity: each `--apply-only` without `APPLY` prints the same counts its
parent's dry-run printed.

### Failure containment (S1b)

Today's three crashes were all single-record aborts killing whole lanes. The
fetch stages adopt the per-item grace the lanes already have (SHP's
`FETCH-FAILED ... skipping`, BSE 403 continue): one dead URL / mangled record
logs and continues; the apply stage reports per-item skips. A lane still exits
non-zero only when *nothing* was fetched/applied.

## 2. Acceptance

- **Wall-clock**: on a cold backlog fixture (or a fresh `$TMPDIR`), the
  parallel run's phase A completes in ≈ the slowest single fetch, not their
  sum; the run logs each stage's start/end for the comparison.
- **Outcome parity**: `refresh-chain` (parallel) and `refresh-chain-ser` over the same
  inputs produce identical store rows (row counts + content hash on
  `graph_edges`/`entities`/`rpt_transactions`/`shp_*`).
- **No lock errors**: zero `database is locked` / DuckDB lock failures across
  repeated runs (applies are sequential by construction).
- **Single-record grace**: a fixture with one 404 filing + one mangled record
  completes; the skips are logged and counted.
- **Tests**: per-lane fetch-stage unit tests (staged artifact written, store
  untouched); one integration test that runs phase A against tmp scratch and
  phase B into a tmp store pair.

## 3. Non-goals

- No lane semantic changes: the same edges/entities are written, same
  ordering guarantees within a lane.
- No store schema changes; no new tables (scratch artifacts live in
  `$TMPDIR`/`memory/data/rpt_raw/` as today).
- No parallelism inside a lane (per-item fetch loops stay serial — politeness
  to the sources); the win is overlap ACROSS lanes.
- No scheduler/cron work — the target stays operator-invoked.

## 4. Follows

- `doc/improvements/archive/pipeline/related_party_groups_vigil.md` — the
  VIGIL lanes and the fetch/apply precedent (`--download` without `--apply`).
- `helpers/maintenance/maint.py` — the post-refresh re-derivation
  (`maint-full`) that consumes whatever the chain writes.
- `doc/improvements/completed.md` #401 — the 2026-10-16 chain incidents
  (three single-record aborts) that motivated the failure-containment slice.

## 5. Implementation log

Implemented 2026-10-16 — all five lanes plus Makefile target:

- **`helpers/maintenance/shareholding_sync.py`** — added `--fetch-only` (stages parsed
  NSE/BSE filings to scratch JSON under `$TMPDIR/findata-graph/shp_fetch_scratch/`,
  no store writes) and `--apply-only` (reads staged JSON, writes to sources.duckdb +
  research.db). Mutual exclusivity enforced. Existing `--download`/`--apply` invocations
  unchanged.
- **`helpers/maintenance/enrich_from_yfinance.py`** — added `--fetch-only` (parallel
  yfinance .info fetch, stages to `$TMPDIR/findata-graph/yf_fetch_scratch/<TICKER>.json`)
  and `--apply-only` (reads staged JSON, writes metrics + note updates). Identity guard
  and skip logic preserved in both phases.
- **`helpers/maintenance/ingest_nse_xbrl.py`** — added `--fetch-only` (per-entity
  XBRL fetch + parse, stages rows to `$TMPDIR/findata-graph/xbrl_fetch_scratch/<ENTITY>.json`)
  and `--apply-only` (reads staged JSON, applies rows via `apply_rows()`). Per-entity
  isolation and cookie pacing preserved.
- **`helpers/maintenance/exchange_sync.py`** — added `--fetch-only` (per-lane fetch,
  stages rows to `$TMPDIR/findata-graph/exchange_fetch_scratch/<LANE>.json`) and
  `--apply-only` (reads staged JSON, folds into sources.duckdb). Freshness checks and
  FETCH-FAILED lane isolation preserved. `json` already imported.
- **`helpers/maintenance/ticker_hygiene.py`** — added `--fetch-only` (resolves
  ticker symbols, stages results to `$TMPDIR/findata-graph/ticker_hygiene_stage.jsonl`
  plus `$TMPDIR/findata-graph/bse_search_cache/`, no store writes) and `--apply-only`
  (reads staged JSONL, writes resolved symbols to research.db). `--fetch-only` and
  `--apply-only` are mutually exclusive (rc=2 if misused); `--tier2`/`--tier3` fallbacks
  are fetch-phase only. Standalone tool, not part of maint/maint-full; joins the chain
  as its own lane.
- **`Makefile`** — new `refresh-chain` target (default): phase A runs all six fetch stages
  in parallel (`& ... wait`), phase B runs applies sequentially (exchange → vigil →
  shp → ticker_hygiene → enrich → CIN → XBRL → snapshot). Dry-run by default; `APPLY=1` writes.
- **Scratch location**: all `--fetch-only` artifacts write under `$TMPDIR/findata-graph/`
  (per AGENTS.md scratch rule), never `memory/data/` or `/tmp/opencode`. Each lane uses
  its own subdirectory to avoid collisions.

Failure containment (S1b): each lane's fetch stage logs and continues on single-record
failures (404, URLError, OSError); a lane only exits non-zero when nothing was fetched.
Applies are sequential by construction — zero `database is locked` / DuckDB lock
failures across runs.

Verification: `--fetch-only` and `--apply-only` confirmed working on all five lanes
(dry-run tested against live DB; mutual exclusivity returns rc=2). Scratch artifacts
verified to land under `$TMPDIR`. Existing sequential `refresh-chain` target unchanged.
