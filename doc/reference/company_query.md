# Reference: company_query — the company composite CLI

**Date:** 2026-11-28 (proposal `doc/improvements/proposals/company_composite_query.md`)
**Scope:** `helpers/research/company_query.py` — read-only typed-key query
over the company data in the served stores/snapshots (research + sources +
graph domains). This page owns the CLI contract; the proposal owns the
design rationale and the §2 measurements behind every rule.

The composite is NOT a search leg: it is typed-key exact lookup (ticker,
ISIN, FIGI, CIN, taxonomy label, family label, relation grammar, exact or
normalized name) over one query string, per the admission test in
`doc/local/engineering/emeans_algorithm.md` §8 — legs return ranked Hit
lists over the same query; this returns resolved entities. The one piece
that IS a search leg is the opt-in `master_query --legs company` digest
(S5), which is a thin pointer client of the `digest` verb.

## Verbs

```bash
Q=".venv/bin/python3 helpers/research"   # run from repo root
$Q/company_query.py snapshot SBIN.NS                    # 14-section report
$Q/company_query.py snapshot SBIN.NS --format json      # machine shape
$Q/company_query.py snapshot SBIN.NS --fields entity,listings,rpt_summary
$Q/company_query.py group RELIANCE.NS --depth 2         # 3 rosters + relations
$Q/company_query.py group "Tata" --include subsidiaries,acquisitions
$Q/company_query.py bulk Banking --format csv           # one row per member
$Q/company_query.py bulk --index "NIFTY 50" --format jsonl
$Q/company_query.py bulk --tickers-file t.txt           # mixed keys per line
$Q/company_query.py bulk Banking --summary              # one aggregate row per numeric field
$Q/company_query.py digest SBIN.NS --limit 5            # master_query leg backend
$Q/company_query.py resolve "parent of Jio Platforms"   # show the outcome only
$Q/company_query.py "parent of Jio Platforms"           # bare query autoroutes
```

- **snapshot** — the 14 sections (`entity`, `identifiers`, `centrality`,
  `graph_analytics`, `edge_counts`, `company_metrics`, `listings`,
  `bhavcopy_recent`, `rpt_summary`, `shareholding_recent`,
  `credit_ratings`, `index_membership`, `mca_cin`, `figi`) in stable
  order; `--fields` selects, absent sections are omitted (never null).
- **group** — three labeled rosters (`family`, `ownership`, `disclosed`)
  plus BFS-bounded relations (`subsidiaries`, `peers`, `parents`,
  `acquisitions`, `invested`, `jv`; default first two) with cycle flags;
  `--depth` default 2. The ownership roster walks `ultimate_parent` and
  reports apexes, root kinds (`self-root`/`chain-root`/`cycle-guard`) and
  cycle-guard members — never silently truncated.
- **bulk** — one row per member of any resolved set (sector, sub-sector,
  super-sector, family, relation set, index universe). `--index` and
  `--tickers-file` are explicit universes that skip target resolution;
  unresolved members go to stderr with candidates (or the JSON payload's
  `unresolved` key), never dropped silently. `--fields` picks section
  groups; output jsonl (default) / csv / json. `--summary` replaces the
  member rows with one aggregate row per numeric field — `field, count,
  sum, median` — over top-level numeric columns, list-section lengths
  and numeric `company_metrics` values (raw strings like `"1,000 Cr"`
  are skipped, not guessed; a list length of 0 counts — absence is
  data); the JSON envelope adds `members`.
- **digest** — typed-key JSON for the `master_query` company leg; entity
  hit carries a ≤140-char snippet ending in the composite command to run;
  ambiguity emits one hit per candidate prefixed `ambiguous (N)`; no
  match is an honest `0 hits` (exit 0). The envelope names its source
  per domain in `sources` (e.g. `research=live sources=live graph=wire`)
  and carries `warnings` from any degradation.
- **resolve** — print the resolution outcome (kind/key/entities/
  candidates/note) and exit; useful to debug key routing.

A bare first argument that is not a verb autoroutes: relation grammar →
`group`, otherwise `snapshot`.

## Resolution order (first decisive wins)

1. **Identifier shape** — ticker (`X.NS`-style dotted or ALL-CAPS),
   FIGI (`BBG…`, tried before ISIN: BBG strings also satisfy the ISIN
   shape), ISIN (ISO 6166 12-char), CIN (20-char L/U form). A well-formed
   identifier that is not in the store is DECISIVE: error
   `not in store: <type> <value>`, exit 1 — never a silent fall-through
   to fuzzy name matching.
2. **Bare symbol in caps** — ticker stem (`split_part('.', 1)`).
3. **Taxonomy label** — sector / sub_sector / super_sector
   (case- and underscore-insensitive; two-level labels like
   `Energy_Super` resolve to the sector unless `--as super-sector`).
4. **Family label** — `family:<Name>` (case-insensitive; canonical
   casing from the stored label).
5. **Relation grammar** — `X of Y` lead form or `Y <verb>` tail form
   over the verb list above.
6. **Name tiers** — exact → normalized → suffix-stripped → known
   abbreviation → alias identifier → ranked candidates (listed-first,
   cap 10, exit 1). Fuzzy `word_overlap_match` is never auto-picked:
   it was right for only 18/49 typed names in the §2 measurement.

## Exit codes

`0` resolved (including an honest `0 hits` from digest); `1`
unresolved or ambiguous (candidates rendered); `2` usage error.

## Flags

Every verb takes the same target-selection group — `--ticker`, `--name`,
`--cin`, `--isin`, `--figi`, `--sector`, `--sub-sector`,
`--super-sector` — resolved through the order above; two-level labels
(`Energy_Super`) land on the sector unless `--as super-sector` (or
`sub_sector`) redirects the tier. A bare positional argument autoroutes
through the same resolution instead. Per verb:

- `snapshot` — `--format human|json`; `--fields` comma list of section
  names (absent sections omitted, never null).
- `group` — `--depth` (BFS bound, default 2, cap 2000); `--include`
  comma list of relations (default `subsidiaries,peers`; all six:
  subsidiaries, peers, parents, acquisitions, invested, jv);
  `--format human|json`.
- `bulk` — `--index NAME` / `--tickers-file FILE` (explicit universes:
  skip target resolution entirely; unresolved members reported, never
  dropped); `--fields` section groups; `--format jsonl|csv|json`;
  `--summary` (aggregate rows replace member rows).
- `digest` — `--limit` (default 4 candidates per ambiguous group).
- `resolve` — no extra flags; prints the outcome JSON and exits.

## Stores and modes

Read-only through `helpers.core.duckdb_lock` entry points only
(`io_lock` shared for readers, `connect_with_lock_retry` ladder on the
live attach; no raw `sqlite3.connect`, no write path). Snapshot
parquet views (`<side>__<table>`) are always mounted; live-first on
top: a store served on the quack wire routes through `connect_shared`
(a wire client — e.g. `memory/graph.duckdb` on its resident server),
an unserved one attaches read-only through the lock ladder, and the
snapshot is the fallback of last resort. Every section reports its
`source_mode` per domain (live / wire / snapshot). Cross-store joins
always ride the domain system (`fetch(domain, table, …)` renders
`{t}`/`{t2}`/`{v}` per mode) — never hard-coded side prefixes.

## Tests

`tests/test_company_query.py` builds the full three-side parquet
snapshot from an in-module fixture spec (no live store contact) and
covers every resolver key and tier, the four outcomes, snapshot
sections, group rosters + cycle, bulk universes + `--summary`
aggregates, digest shapes (incl. the `sources` map), the sources layer
(missing-live fallback, wire routing, wire-failure degradation,
`{t2}` joins), and CLI exit codes. master_query-side leg wiring:
`TestCompanyLeg` in `tests/test_master_query.py`.
