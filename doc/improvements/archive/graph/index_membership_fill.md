---
title: "Index-membership fill — NSE constituents into the sidecar, SQLite, and graph cache"
status: executed
filed: "2026-09-19"
executed: "2026-09-19"
completed_md: "253"
area: "helpers/maintenance (index_sync.py), helpers/graph (derive_indices.py, query.py), doc/design (ontology.md, db_schema.md), tests"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Index-membership fill — NSE constituents into the sidecar, SQLite, and graph cache

**Date:** 2026-09-19 · **Status:** EXECUTED 2026-09-19 ·
completed.md entry 253 ·
**Area:** `helpers/maintenance/index_sync.py`,
`helpers/graph/derive_indices.py` + `query.py`,
`doc/design/{ontology,db_schema,data_sources}.md`, `tests/`

> Plan-of-record: `doc/local/evaluations/index_fill.md` (2026-09-19,
> second-session reviewed; §13 resolves the storage and hyperedge
> questions). This proposal converts that memo into independently
> landable slices; it does not re-derive the analysis.

## 1. Motivation

`listed_on_index` (company → index membership) was evaluated and
**deferred** twice — word-overlap alias guard (`#218`, §7) and the
country-layer arc (`#219`, §6) — each time with the same revisit
trigger: *"only if a real index-membership data source appears."* It
has appeared. NSE Indices publishes a constituent CSV per equity index
(`niftyindices.com/IndexConstituent/*.csv`), and the repo already folds
three NSE index feeds into the sidecar (`data_sources.md:47-49`).

The fold is lossy: `exchange_sync.py` keeps one row per
`(exchange, symbol, segment)` and records only the `industry` it read
from the CSV — **never which index the symbol came from**. So the
sidecar today carries 1,284 NSE `industry` values whose membership
provenance is already discarded, and the graph has no index node, no
`listed_on_index` edge, and no way to answer "which index is Reliance
in?".

This is a viewer-visible gap: an index is a first-class thing users
query, and the constituent feed makes it cheap to fill.

## 2. Evidence (measured 2026-09-19, this box)

| Fact | Value | Source |
|---|---|---|
| `sources.duckdb::exchange_listings` rows | 47,489 | live `COUNT(*)` |
| NSE rows / NSE rows with non-null `industry` | 9,342 / 1,284 | live query |
| `sources.duckdb::mca_cin` rows | 1,020 | live query |
| `sources.duckdb::index_constituents` | **absent** | `SHOW TABLES` |
| `research.db` entities / `company` / `index` | 6,754 / 6,203 / 0 | live query |
| D14 stubs (`company` + `file_path NULL` + ticker) | 5,022 | live query |
| Stub `sector_classification` non-null | **0** | live query |
| Stubs with any edge / with `listed_in` | 4 / 0 | live query |
| Companies with `file_path` | 1,181 | live query |
| `graph_edges` `listed_in` / `listed_on_index` | 930 / **0** | live query |
| `graph.duckdb` `_build_meta.schema_version` / `v_node` / `company` | 15 / 6,754 / 6,203 | live query |
| `v_node` kinds | company, country, edition, institution, sector, sub_sector, super_sector, theme | live query |

The sidecar already holds the index *instruments* as ordinary rows
(`NSE / segment=NULL / symbol='NIFTY 50'`) but no member lists (§2.4).
`derive_countries` ran 2026-09-10, five days before the stubs were
seeded (2026-09-15/16), so **0 of 5,022 stubs have `listed_in`** — the
node set is populated but largely disconnected.

Two pre-existing doc drifts surfaced while measuring, both in scope to
correct in the same arc: `data_sources.md:109` records `mca_cin` at 730
(live 1,020), and `db_schema.md:358` records the cache
`schema_version` at 14 (live 15).

**Yahoo Finance was ruled out** (measured, do not re-audit). yfinance
1.7.0: `^NSEI`/`^GSPC` → "No Fund data found"; `NIFTYBEES.NS` ETF → no
fund data; US India ETFs (`SPY`, `INDA`, `EPI`, `INDY`) → `top_holdings`
of **10 only** (MSCI weighting, not Nifty 50). No `constituents`
attribute, no `info` field, no screener. NSE Indices is the source.

## 3. Design

Three layers, one script and one make target per hop; `research.db`
stays canonical, `graph.duckdb` is a rebuildable projection, raw web
pulls stay in the sidecar and reach git only via the parquet snapshot:

```text
web CSVs (nsearchives + niftyindices)
  -> index_sync.py      -> sources.duckdb::index_constituents   [raw]
  -> derive_indices.py  -> research.db (index entities +
                           listed_on_index edges)               [canonical]
  -> make graph-rebuild -> graph.duckdb (v_index,
                           e_listed_on_index)                   [cache]
  -> snapshot_db.py     -> parquets + --check                   [db_sync]
```

**Decisions already taken (eval §13), not reopened here:**

- Raw store = sibling table `index_constituents` in the existing
  `sources.duckdb`, not columns on `exchange_listings` (which would
  break its dedup key and cannot express a symbol in NIFTY 50 + NIFTY
  BANK simultaneously) and not a git-tracked JSON (breaks the
  sidecar→snapshot symmetry). Append-only per reconstitution,
  `PRIMARY KEY (index_name, symbol, as_of)`, latest view
  `vw_index_constituent`.
- Index is a **fileless `entity_type='index'` node**, not a company and
  not note frontmatter. The dropped `index_membership` YAML key is
  typed `null` (`doc/okf/frontmatter.company.v1.json:190`) and writing
  it fails OKF conformance — do not revive it.
- Membership is a **dyadic** `listed_on_index` edge, kept **out of
  `EDGE_REGISTRY`** (mixed-endpoint, like `listed_in`/`exposed_to`), so
  it gets a dedicated CTAS + `v_*` projection.
- **Hyperedges (co-membership) are deferred to Wave 2** — reconstitution
  churn plus the D10 incidence-density skew a 500-member star would
  inject.

**Sidecar table** (append-only per reconstitution; `vw_index_constituent`
selects the max `as_of` per index):

```sql
CREATE TABLE IF NOT EXISTS index_constituents (
    index_name   VARCHAR NOT NULL,   -- canonical NSE Indices display name
    index_slug   VARCHAR,            -- niftyindices page slug / CSV stem
    asset_class  VARCHAR NOT NULL DEFAULT 'equity',
    symbol       VARCHAR,            -- NSE symbol (join key)
    isin         VARCHAR,            -- cross-check / fallback join key
    company_name VARCHAR,
    industry     VARCHAR,
    series       VARCHAR,            -- EQ / SM / ...
    as_of        DATE,               -- Last-Modified / reconstitution vintage
    fetched_at   DATE NOT NULL,
    PRIMARY KEY (index_name, symbol, as_of)
);

CREATE VIEW vw_index_constituent AS
SELECT * FROM index_constituents c
WHERE c.as_of = (SELECT MAX(as_of) FROM index_constituents
                 WHERE index_name = c.index_name);
```

**Edge shape:** `edge_type='listed_on_index'`, `source=<company name>`,
`target=<index name>`, `symmetric=0`,
`source_ref='derive:indices:nse-constituents'`, properties
`{"symbol","isin","as_of","index_slug"}`.

**Open decision (memo §10.3):** canonical index naming — the
niftyindices display name vs the instrument name already in
`exchange_listings` (`NIFTY FIN SERVICE` vs `NIFTY FINANCIAL
SERVICES`). The node name and `index_name` key follow whichever the
operator picks; the sidecar stores the site display name verbatim.

**Slices (run in order):**

- **S1 — sidecar lane** (`helpers/maintenance/index_sync.py` +
  `make refresh-indices`). Lanes: `nsearchives` for the three documented
  feeds, `niftyindices` for the long tail (page-scrape the
  `IndexConstituent/*.csv` href — stems are inconsistent, never guess:
  `ind_nifty50list.csv` vs `ind_niftyindiadefence_list.csv`). The
  long-tail CSV lives at
  `https://www.niftyindices.com/IndexConstituent/<stem>.csv` and returns
  `Company Name, Industry, Symbol, Series, ISIN Code`; the nsearchives
  lane needs a browser `User-Agent` + `Referer:
  https://www.nseindia.com/`. Reuse `exchange_sync.py`'s
  `_get()`/freshness idiom; freshness is measured
  against the CSV `Last-Modified` (semi-annual reconstitution), not a
  flat day count. `--apply` writes; default dry-run. Add
  `index_constituents` to `SOURCES_TABLES` (`snapshot_db.py:111`) for
  the parquet export + `--check` round-trip.
- **S2 — derive** (`helpers/graph/derive_indices.py` +
  `make derive-indices`), mirroring `derive_countries.py`. Read
  `vw_index_constituent`; resolve each row to a company by precedence
  **ISIN** (`exchange_listings.isin`) → **NSE symbol**
  (`entities.ticker == SYMBOL || SYMBOL + '.NS'`) → worklist. Fuzzy
  name matching is vetoed (`#218` misfires four ways). Idempotent
  `INSERT OR IGNORE` for index entities; edges via `apply_typed_edges`;
  unresolved rows → `findata/Misc/index_worklist.json` (the
  `country_worklist.json` parking pattern). **Industry convergence is
  folded in here**: for each resolved constituent, converge
  `index_constituents.industry` into the company's
  `sector_classification` via an explicit map (raw value stays
  sidecar-side; the exchange labels are not the `sector_classification`
  scheme). No `seed_stubs`/`exchange_sync` change — the D14 seed stays
  name/ticker-only, and the `data_sources.md:145-148` claim that it
  emits industry is corrected in the S3 doc sweep rather than made true.
- **S3 — graph cache surfaces** (bump `_SCHEMA_VERSION` and touch every
  surface; misses cause silent empty tables or integrity failures):

  | # | Surface | Change |
  |---|---|---|
  | 1 | `query.py:_SCHEMA_VERSION` | `"15"` → `"16"` (forces cold rebuild) |
  | 2 | `query.py` `v_node` kind IN-list | add `'index'` |
  | 3 | `query.py` `v_index` projection | new filtered table, `kind='index'` |
  | 4 | `query.py` `e_listed_on_index` CTAS | company→index, mirror `e_listed_in` (`query.py:1519`) |
  | 5 | `query.py:_EXTRA_MATERIALIZED` | add `"v_index"`, `"e_listed_on_index"` |
  | 6 | `database_integrity_check.py:_KNOWN_EDGE_TYPES` | add `"listed_on_index"` |
  | 6b | `doc/design/ontology.md` §2.2 `edge_types` roster | add `- listed_on_index` in the **same change** as #6 (two-directional static check) |
  | 7 | `database_integrity_check.py` `v_node` count IN-list | add `'index'` |
  | 8 | `sync_tags.py:FILELESS_ENTITY_TYPES` | add `"index"` |
  | 9 | `database_integrity_check.py` fileless tuple | add `"index"` |
  | 11 | `doc/design/db_schema.md:358-366` | object count + `v_index`/`e_listed_on_index` rows; fix the `schema_version` 14→16 drift |
  | 12 | `doc/design/data_sources.md` | register the index lane; fix `mca_cin` 730→1,020 (`:109`) and the false D14 "industry + sector_classification" claim (`:145-148`) |

  Surface #10 (hyperedge star `("index","listed_on_index")`) is Wave 2.
- **S4 — verification & reconciliation** (§4 below). New
  `tests/test_index_sync.py`, `tests/test_derive_indices.py`; extend
  `test_snapshot.py`, `test_static_checks.py`.

**Scope:** Wave 1 = broad-based + sectoral (~55 indices, all company
constituents). Thematic + strategy (~90) = Wave 2; fixed-income /
multi-asset / hybrid (~120) are bonds/baskets and are skipped.

## 4. Acceptance criteria & shakedown

1. `make refresh-indices` is idempotent: a second `--apply` writes 0
   new rows on `(index_name, symbol, as_of)`.
2. `make derive-indices --apply` creates the `index` entities and
   `listed_on_index` edges and writes the converged
   `sector_classification` onto resolved companies; every unresolved
   constituent appears in `findata/Misc/index_worklist.json` with its
   resolution key recorded, and unmapped industry labels park separately
   rather than being silently dropped.
3. **Eval gate (mandatory — the change adds an `edge_types` roster entry
   and a `v_node` kind, both query-visible).** Run
   `helpers/misc/ontology_eval_gate.py` over the frozen question set
   between dry-run and canonical apply: zero regressions, no undeclared
   changes. Expected trivially green (no frozen question references
   `entity_type='index'`), stated per house rule.
4. `make graph-rebuild` then `make integrity`: `v_node` kind counts and
   every `e_*` count reconcile; no schema-version staleness.
5. `make qa` green, including `static-checks` (Ontology doc rosters
   two-directional check after #6/#6b) and snapshot `--check`.
6. Acceptance numbers recorded in the completed.md entry: `index`
   entities created, `listed_on_index` edges, worklist count, `v_node`
   before/after, `e_listed_on_index` count.

| Projected outcome | Today | After (Wave 1) |
|---|---|---|
| `index` entities | 0 | ~55 |
| `listed_on_index` edges | 0 | ~thousands (≈55×avg members, deduped) |
| Companies with `sector_classification` filled from index industry | 0 | ≥1,284 (Wave 1 union) |
| `graph.duckdb` schema_version | 15 | 16 |

## 5. Risks

- **Scrape fragility** — niftyindices stems are inconsistent and the
  site throttles. Prefer `nsearchives` for the three documented feeds;
  treat the page scrape as best-effort long-tail only, with a browser
  `User-Agent` (and `Referer`) as the archive lane already requires.
- **Industry vocabulary mismatch** (folded into S2) — exchange labels
  (`Oil Gas & Consumable Fuels`) are not the `sector_classification`
  scheme; a blind copy would pollute the taxonomy. Mitigate with an
  explicit convergence map and keep the raw value sidecar-side.
- **Identity traps** — ETFs are company rows (`Quantum Nifty 50 ETF`)
  and must not become `index` nodes; constituent display names differ
  from entity names (`Reliance Industries Ltd.` vs `... Industries`).
  ISIN/symbol resolution avoids both; fuzzy match stays vetoed.
- **Stale `listed_in`** — the derive also needs a re-run to connect the
  5,022 stubs regardless of the index work; not a regression this arc
  introduces, but the acceptance numbers should not conflate the two.

## 6. Non-goals

- Wave 2 (thematic + strategy indices) and fixed-income / multi-asset /
  hybrid indices (constituents are not companies).
- Index co-membership **hyperedges** (surface #10) — deferred with the
  D10 rationale; the dyadic edges carry the data.
- Note-frontmatter membership — the `index_membership` key stays
  dropped.
- Yahoo/yfinance as a membership source (measured dead, §2).
- Any change to `exchange_listings`' `(exchange, symbol, segment)` key.
- Any `seed_stubs`/`exchange_sync.py` code change — industry enrichment
  is folded into the index derive (S2), not the D14 seed.
- Re-running `derive_countries` to connect the pre-existing 5,022 stubs'
  `listed_in` — a separate derive pass, tracked separately; this arc's
  acceptance numbers must not conflate it with index membership.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-19 | `.venv/bin/python3` × duckdb read-only `COUNT(*)` on `sources.duckdb` | `exchange_listings=47,489`, `mca_cin=1,020`, `index_constituents` absent | main tree, HEAD `0bea56de` |
| 2026-09-19 | sqlite3 on `research.db` | entities 6,754; company 6,203; index 0; stubs 5,022; stub sector_classification non-null 0; stub any-edge 4; stub `listed_in` 0; `listed_in` edges 930 | |
| 2026-09-19 | duckdb read-only on `graph.duckdb` | `schema_version='15'`; `v_node=6,754`; company 6,203; kinds = company, country, edition, institution, sector, sub_sector, super_sector, theme | |
| 2026-09-19 | yfinance 1.7.0 `funds_data` probes | `^NSEI`/`^GSPC`/`NIFTYBEES.NS` no fund data; `SPY`/`INDA`/`EPI`/`INDY` top-10 holdings only | membership source ruled out |

## Execution outcome (2026-09-19)

All four slices landed; acceptance criteria met.

| Metric | Projected | Actual |
|---|---|---|
| `index` entities | ~55 | **57** |
| `listed_on_index` edges | ~thousands | **6,615** |
| Companies `sector_classification`-filled from index industry | ≥1,284 | **639** |
| `graph.duckdb` schema_version | 16 | **16** |

- **S1 sidecar**: `make refresh-indices APPLY=1` → 57 indices / 6,661
  `index_constituents` rows; a second apply inserted **0** (idempotent).
  Discovery collapses 65 detail paths → 57 CSV stems (three indices were
  linked from two detail pages each).
- **S2 derive**: `make derive-indices` → 57 index entities, 6,615 edges,
  639 sectors filled; 46 unresolved constituents → `findata/Misc/index_worklist.json`
  (gitignored). Unmapped labels parked: `Services` (86), `Utilities` (12).
- **Eval gate**: **ACCEPT** (`helpers/misc/ontology_questions.json`,
  164 questions, 0 moved — no frozen question references `entity_type='index'`).
- **S3 cache**: `make graph-rebuild` → `v_node` 6,811 (+57), `v_index` 57,
  `e_listed_on_index` 6,615; `make integrity` rc=0 (cache 16/16, 0 drift).
- **Gates**: `make qa` 10/10, `make advisory` 11/11, `make search-fresh`
  fresh. `make perf` 19/22 — only `graph_closeness`/`graph_betweenness`
  timing over-budget (ignored) and `snapshot_check` at the pre-snapshot
  moment (the snapshot change rides its own db_sync patch).

The proposed ≥1,284 fill proxy was the D14 NSE `industry` non-null count;
actual fills are lower because already-classified companies are never
overwritten (639 empty fields filled) and two coarse label groups park
unmapped rather than being copied.
