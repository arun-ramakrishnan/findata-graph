---
title: "Company composite query — one-call DuckDB analytics across ticker/company data"
status: proposed
filed: "2026-11-27"
executed: null
completed_md: null
area: "helpers/research/company_query.py + DuckDB snapshot/live query surface + opt-in master_query leg (helpers/misc/master_query.py, helpers/misc/search_tui.py)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Company composite query — one-call DuckDB analytics across ticker/company data

**Date:** 2026-11-27 · **Status:** PROPOSED ·
**Area:** `helpers/research/company_query.py` (new) + read-only DuckDB query
composition over `memory/research.duckdb`, `memory/data/sources.duckdb`, and
`snapshots/parquet/**`; opt-in `master_query` leg wiring in `helpers/misc/`

> **Revised 2026-11-27 (second session):** evidence re-measured after the
> FIGI run finished and the snapshot was refreshed (the first probe's CIN and
> "arc in flight" facts were stale); module relocated from
> `helpers/analytics/` (harness telemetry) to `helpers/research/`; S2
> rescoped onto the graph-layer surfaces that already exist; slice S5 (an
> opt-in `master_query` leg) added on the strength of the measurement in
> §2.4.
>
> **Revised again (third pass, same day):** one flexible query string — a
> plain company name, a sector or super_sector, or a ticker / ISIN / FIGI —
> now resolves through typed keys to an entity set that every verb accepts
> (§2.6, "Query resolution"); S2 corrected to return the three disagreeing
> group rosters as a labeled union instead of leading with the ownership
> roll-up (§2.5).

## 1. Motivation

The exchange/ticker/identifier arc (`exchange_ticker_data_update.md`,
executed 2026-11-27 as completed.md entry 576) has made company data
materially richer: exchange listings, BhavCopy samples, FIGI instruments,
MCA CIN, shareholding filings, RPT transactions, credit ratings, index
constituents, graph edges/centrality, company metrics, and entity
identifiers now sit across DuckDB stores and snapshot parquet. Answering a
simple question like "show me everything we know about SBIN.NS", "give me
the business group around Reliance Industries including
parent/subsidiaries", or "roll up Banking" currently requires manual,
error-prone SQL spanning multiple stores, schema quirks, and join keys.

Pain observed (2026-11-27, this box):

| Question | Current path | Cost/risk |
|---|---|---|
| One-company snapshot by any name or identifier | Ad hoc DuckDB script: `connect_snapshots()` already mounts the 84 snapshot views (~0.1 s), then 10+ tables are hand-joined across stores | ~15 min, schema mistakes (BIGINT node-ID edge columns, numeric BSE scrip codes, ISIN-only joins, VARCHAR amount columns, multi-vintage index rows — §2) |
| Business group / parent-subsidiary | Three sources exist and disagree (§2.5): the curated family roster, the ownership roll-up and the RPT-disclosed sets; the graph layer answers name-resolved one-hop relations (`subsidiary_of_company`, `group_siblings`, `acquisitions`) | No one call returns them together, and leading with any single source answers Tata-type group queries wrongly or not at all |
| Sector / super_sector analytics | Hand-joined SQL over `e_belongs`, `e_belongs_to` and `entity_tags`; labels are stored underscored | Two membership notions (1,231 and 2,074 companies); no universe selector |
| Bulk analytics over many tickers | Repeat per-ticker query or write custom SQL | No stable contract; no batch/caching; no CSV/JSON output shape |

The data is already there; the missing piece is a stable, read-only,
composable query surface that accepts one query string.

## 2. Evidence

First probe 2026-11-27 (session A, FIGI run still writing); re-measured the
same day (session B) after the FIGI run finished and the snapshot was
refreshed, and again (session C) for the flexible-query and group-roster
questions. The tables hold the re-measured state — earlier logs stay in
Appendix A and Appendix B, the third-pass measurements in Appendix C.

### Data sources available

| Store / path | Tables/views relevant to company analytics | Notes |
|---|---|---|
| `memory/data/sources.duckdb` | 9 tables: `bhavcopy_sample` (74,641 rows, Q3 2026 sample), `credit_ratings` (13,746), `exchange_listings` (52,560), `figi_instruments` (1,230 rows: IN 968, US 262), `index_constituents` (14,210 rows, 58 indexes, 9 `as_of` vintages), `mca_cin` (1,020), `rpt_transactions` (356,473), `shp_filings` (196), `shp_holders` (8,612); plus 4 store views (vw_equity, vw_sme, vw_instruments, vw_index_constituent — the last keeps the latest `as_of` per index) | Canonical DuckDB source; read-only attach works. `memory/research.duckdb` was read-locked by the concurrent FIGI run at the first probe; at re-measurement it opened read-only in ~0.02 s. |
| `snapshots/parquet/duckdb/*.parquet` | `v_node`, `v_company`, 19 `e_*` edge tables, 9 `v_centrality_*` views, `v_embeddings` | Snapshot edge tables are BIGINT node-ID keyed (`e_subsidiary.subsidiary_name/parent_name` are BIGINT IDs, join `v_node.id`). |
| `snapshots/parquet/sqlite/*.parquet` | 31 tables incl. `entities` (32,136 rows), `entity_identifiers` (8,487), `company_metrics` (75,457), `graph_analytics` (244,436), `ultimate_parent` (25,200), `hyper_edges` (4,253), `hyper_incidences` (58,942), `entity_ticker_status` | Entity/ticker/CIN/identifiers/metrics, the precomputed ownership roll-up, and the group hyperedges. |
| `snapshots/parquet/sources/*.parquet` | the same 9 source tables | In sync with live at re-measurement (`bhavcopy_sample` 74,641 and `figi_instruments` 1,230 rows on both sides); drifts again until the next snapshot refresh. |

### Schema quirks confirmed by probe

| Table | Confirmed columns / gotcha |
|---|---|
| `e_subsidiary` (snapshot duckdb) | `subsidiary_name BIGINT, parent_name BIGINT, weight, properties, source_ref, valid_from, valid_to` — **node IDs**, not names. 17,348 edges; `source_ref` is RPT-derived on 17,269 (one edge per filing/period/transaction), so dedupe on the (parent, subsidiary) node pair before walking; 2,876 subsidiaries carry more than one parent node |
| `e_invested`, `e_acquired`, `e_competes`, `e_customer`, `e_supplier`, `e_group` | BIGINT node-ID endpoint columns in snapshot parquet (`e_group` uses `a_name`/`b_name`); names resolved via `v_node` |
| `e_belongs`, `e_belongs_to` | company-to-sector (1,231 edges, `company_name`/`sector_name`) and the taxonomy hierarchy (185 edges, `child_id`/`parent_id`); both needed for a super_sector universe |
| `ultimate_parent` (snapshot sqlite) | `company, ultimate_parent, depth, path, root_kind, coalition_pct, computed_at` — 25,200 rows; `root_kind` is `self-root` (16,766), `chain-root` (6,897) or `cycle-guard` (1,537); 8,434 companies sit under a non-self root; written by `derive_ultimate_parent`. An ownership closure, not a group roster (§2.5) |
| `hyper_edges` + `hyper_incidences` | group sets by label class: `family:<Name>` (37 curated families, 2,331 members), `mnc:<parent>` (24, 1,857), `vigil:rpt:<SYMBOL>` (602 filer sets, 14,969), 8 legacy quoted labels; `edge_type='subsidiary'` sets are an exact projection of the deduped `subsidiary_of` dyads |
| `entities` (snapshot sqlite) | carries `ticker`, `cin` (plus `cin_listing`, `cin_nic5`, `cin_state`, `cin_year`, `cin_ownership`), `normalized_name`, `sector_classification`; 10,455 of 29,583 companies carry a ticker; 314 tickers have no exchange suffix; 2,074 companies carry a `sector_classification` |
| `entity_identifiers` (snapshot sqlite) | `entity_name, identifier_type, identifier_value, namespace, ...` — `isin` 6,838 rows over 5,875 entities, `figi` 1,241, `cik` 333, `alias` 75; the entity-level source for ISIN and FIGI |
| `bhavcopy_sample` (live sources.duckdb) | `exchange, trade_date, symbol, ticker, isin, name, series, quarter, source, fetched_at` — **no OHLCV/close/volume**; presence/sample lane, not price analytics; BSE rows carry the numeric scrip code as `ticker` (`500112.BO`, series `A`) |
| `exchange_listings` | no `series` column; has `exchange,symbol,isin,industry,segment,...`; BSE rows key by numeric scrip code in `symbol` (SBI is `500112`) — join by ISIN, never by ticker stem; only 1,284 NSE equity rows carry an `industry` label (22 labels) |
| `index_constituents` | has `series` and 9 `as_of` vintages; reading every vintage nearly doubles the rows (SBI: 27 rows for 14 indexes) — read the latest `as_of` per index (live view `vw_index_constituent`) |
| `credit_ratings`, `rpt_transactions` | join by ISIN (`equity_isin`): `company_name` casing differs from `entities.name` (`State Bank Of India`); `rpt_transactions.amount_during_period` is VARCHAR — cast before aggregating |
| `v_node` | `id BIGINT, name, kind, sector_classification, market_cap, ticker`; kinds include `company` (25,201), `sector` (42), `sub_sector` (143), `super_sector` (10) |
| `figi_instruments` | present: 1,230 rows (IN 968, US 262), mixed IN/US instruments; SBI has a folded FIGI in `entity_identifiers` (`BBG000GQ5749`) but no `figi_instruments` row — read identifiers first, and still guard with `information_schema.tables` |

### Composite probe (SBIN.NS)

Measured by running a one-off composite DuckDB query (snapshots + live
`sources.duckdb` read-only attach) on 2026-11-27 and re-checking each
section against the live stores the same day.

| Section | Result shape (re-measured) | Verdict |
|---|---|---|
| entity | name/entity_type/ticker/CIN/sector present (`State Bank of India`, `SBIN.NS`, CIN `U65190MH1993PLC074381` — identical in live `research.duckdb`, snapshot `entities` and `mca_cin`) | useful |
| identifiers | ISIN `INE062A01020` (NSE), FIGI `BBG000GQ5749` | useful |
| graph_analytics | pagerank 0.000412, pagerank_weighted 0.000475, ho_pagerank 0.00229 (computed 2026-11-25) | useful; freshness = snapshot |
| edge counts | requires `v_node` ID mapping; naive name-vs-BIGINT join failed (`ConversionException`) on the first probe | must normalize |
| company_metrics | market_capitalization `₹885,078 Cr` | useful |
| listings | NSE `SBIN` + BSE `500112`, both `main` | useful; join by ISIN |
| bhavcopy_recent | 18 rows: 9 NSE (`SBIN.NS`, series `EQ`) + 9 BSE (`500112.BO`, series `A`), 2026-07-29 to 2026-09-30; no OHLCV | useful for presence, not price |
| rpt_summary | 268 rows touch SBI (129 as filer) across 3 periods, 30-SEP-2025 to 31-MAR-2026 | useful; cast amounts |
| shareholding_recent | **0 rows for SBI** — `shp_filings` holds 196 filings and none is SBI's; the columns (`promoter_pct`, `public_pct`, `emptr`) exist | empty for SBI; section stays, labeled with coverage |
| credit_ratings | 277 rows by `equity_isin`; BOND ratings AAA / AA+ (CRISIL, CARE), latest 2026-07-24 | useful; join by ISIN |
| index_membership | 14 indexes at the latest vintage (NIFTY 50, Nifty Bank, NIFTY 500, ...) | useful |
| mca_cin | 1 row; CIN `U65190MH1993PLC074381`, `via` `auto-web`, `query` `websearch-consensus-2votes` | useful with provenance shown |
| figi | no `figi_instruments` row; the folded FIGI is present in identifiers | read identifiers first |

Raw probe log (first probe): `/mnt/data/tmp/opencode/` shell outputs from the
2026-11-27 session (`sbIN` composite probe iterations; the first query
succeeded after fixing node-ID edge joins, removing nonexistent `series` on
listings, removing OHLCV from bhavcopy, and adding a FIGI fallback view).

### Is this a `master_query` leg? (measured)

All eight `helpers/misc/*_query.py` siblings are `master_query` legs, so the
question deserves data rather than analogy. `master_query` is a client over
search legs: one free-text query is fanned out to every leg with no routing
(`fan_out`), each leg returns a ranked list of locators, and that shared
shape is what lets `rrf_flat` blend ranks and the TUI reuse the same
runners. The repo asked the same question for a sector/cluster leg and
answered "no leg" on a three-part test
(`doc/local/engineering/emeans_algorithm.md` §8):

| Test | Company composite query | Result |
|---|---|---|
| Same query | Input is a selector (ticker, name, identifier, sector) plus a subcommand; `bulk --index` is not a per-query fan-out at all | fails |
| Same object | 14 sections of data rows with no rank; a leg is ranked locators rendered as one 180-character line plus a 140-character snippet | fails |
| Covers a result set the others miss | `master_query.py "SBIN.NS"` on the default legs (5.3 s): the company note tops the notes leg; no leg returns a structured data row | passes |

So the composite is not a leg. What a leg can carry is a pointer: one
digest line per exact-key match. Whether that pointer is signal or noise
depends entirely on the matching rule, so the rules were measured against
18 queries — 9 ordinary repo-style queries (the AGENTS.md and
search-procedure examples plus one deliberately adversarial one) and 9
company-shaped ones. Substring hits sum, per query token of three or more
characters, the entities whose name contains the token — an upper bound on
what a token-OR leg would surface. Regenerate with
`bench_data/company_composite_query/leg_noise_probe.py`.

| Query | Substring hits | Exact-key hits | Ticker/CIN-shaped |
|---|---:|---:|---|
| embed cache | 1 (`cache` 1) | 0 | no |
| why did we not adopt langgraph | 26 (`not` 25, `why` 1) | 0 | no |
| promoter holding | 1,064 (`holding` 1,044, `promoter` 20) | 0 | no |
| hypergraph scaling ceiling | 1 (`scaling` 1) | 0 | no |
| bili namespace routing | 137 (`bili` 137) | 0 | no |
| stg refresh scope | 4 (`scope` 3, `stg` 1) | 0 | no |
| integrity | 1 (`integrity` 1) | 0 | no |
| graph rebuild | 13 (`graph` 13) | 0 | no |
| reliance on cache | 176 (`reliance` 175, `cache` 1) | 0 | no |
| tata | 350 (`tata` 350) | 1 (`Tata`) | no |
| HDFC | 47 (`HDFC` 47) | 0 | no |
| HDFC Bank Limited | 414 (`Bank` 239, `Limited` 128, `HDFC` 47) | 0 | no |
| State Bank of India | 1,933 (`India` 1,457, `Bank` 239, `State` 237) | 1 (`State Bank of India`) | no |
| State_Bank_of_India | 2 (`State_Bank_of_India` 2) | 1 (`State Bank of India`) | no |
| SBIN.NS | 0 | 1 (`State Bank of India`) | yes |
| RELIANCE.NS | 0 | 1 (`Reliance Industries`) | yes |
| U65190MH1993PLC074381 | 0 | 1 (`State Bank of India`) | yes |
| L65191MH1955GOI038333 | 0 | 0 | yes |
| **total** | **4,169** (ordinary 1,423) | **6** | **4** |

Readings:

- **Substring matching is noise.** It fires on 9 of 9 ordinary queries for
  1,423 junk hits — `promoter holding` alone matches 1,064 entity names
  and `bili namespace routing` 137. That is the rule the in-process `maint`
  leg applies to a small timing corpus; across 32,136 entity names it does
  not work.
- **Exact keys are clean but quiet.** 0 of 9 ordinary queries resolve; 6 of 9
  company-shaped ones do. The misses are the accepted cost: `HDFC` and
  `HDFC Bank Limited` match no stored name exactly (the notes leg already
  surfaces those company notes), and `tata` resolves to the `family:Tata`
  set under the final resolution order (§3) — the 350 substring matches are
  group members, not answers.
- **A shape regex alone is not enough.** It accepts any CIN-shaped string,
  including `L65191MH1955GOI038333`, which no store holds; only the exact
  lookup separates a stored key from a well-formed one.

The exact-key digest was prototyped to price the leg and to check that it
fits the leg's render budget (3 runs, identical output):

| Query | Head · snippet length | Snippet |
|---|---|---|
| `SBIN.NS`, `sbin.ns`, `State Bank of India`, `State_Bank_of_India`, and the stored CIN | 62 · 97 | BSE+NSE · NIFTY 100/NIFTY 50 · mcap ₹885,078 Cr · run: company_query.py snapshot --ticker SBIN.NS |
| `RELIANCE.NS` | 65 · 103 | BSE+NSE · NIFTY 100/NIFTY 50 · mcap ₹1,637,835 Cr · run: company_query.py snapshot --ticker RELIANCE.NS |
| `tata` | 38 · 42 | run: company_query.py bulk Tata --format csv (family set; amended with the final key order, §7) |

| Step | Measured |
|---|---|
| Snapshot mount via `connect_snapshots()` (84 views) | 0.08–0.13 s |
| Exact-key resolve | 10–13 ms |
| Digest per hit | 16–20 ms |
| One digest end to end as its own process (import + mount + resolve + digest) | 0.40 s wall, 92–95 MB RSS |
| Live research store, read-only open / exact lookup | 0.02 s / 6–13 ms |
| Default `master_query` fan-out, for comparison | 5.3 s wall |

Decision: the composite stays a standalone surface (S1–S3); the only
`master_query` addition is S5 — an opt-in digest leg keyed on typed exact
keys (§2.6). It is not a default leg: `maint`, the one in-process
non-corpus leg, is opt-in too, and a default slot would add an always-empty
section to nearly every query.

### Group rosters: three sources that disagree (measured)

"Group companies" has no single source of truth. Regenerate with
`bench_data/company_composite_query/group_sources_probe.py`. "Listed"
means members carrying a ticker.

| Group | Curated family roster (listed) | Ownership roll-up (`ultimate_parent`) | Disclosed group set |
|---|---|---|---|
| Tata | 235 (23) | No common apex: 6 of 9 flagships are their own root; TCS points to "Tata sons" (5 members) | TCS 205, Tata Steel 141 |
| Reliance | 67 (5) | Reliance Industries 260; Jio Financial Services is its own root | 276 |
| Adani | 66 (8) | Three roots: Adani Green Energy 139, Adani Power 17, Adani Enterprises 2 | none found |
| Aditya Birla | 68 (11) | Grasim 65, with UltraTech at depth 1; Hindalco separate | Grasim 56 |
| Mahindra | 65 (7) | Mahindra & Mahindra 125; Tech Mahindra resolves elsewhere | Mahindra & Mahindra 173 |
| Bajaj | 18 (5) | Bajaj Finserv 17, with Bajaj Finance at depth 1; Bajaj Auto separate | Bajaj Finserv 32 |
| JSW | 37 (5) | Two roots: JSW Steel 67, JSW Energy 9 | — |
| HDFC | no label | HDFC Bank 13 | HDFC Bank 11 |

- **Curated family roster** — `family:<Name>` hyperedges, 37 families,
  curated; the only source that lists Tata as one group. The MNC arms are
  `mnc:<parent>`.
- **Ownership roll-up** — one apex per company. Right for clean majority
  chains (Bajaj Finance under Bajaj Finserv, UltraTech under Grasim, Jio
  Platforms under Reliance Industries); inverted or cycle-guarded elsewhere:
  Tata Motors resolves to Fiat India Automobiles, Tech Mahindra to Eventus
  Solutions Group, and ICICI Lombard's path runs through ICICI Prudential
  Life. 1,537 members sit on cycle-guard paths.
- **RPT-disclosed structure** — `subsidiary_of` dyads and the filer's
  related-party set. Broad but noisy: 2,876 of 9,167 subsidiaries (31%)
  have more than one parent node, so "parent of X" is ambiguous for them;
  some filers have none (TCS has 0 subsidiary edges yet a 205-member group
  set). The per-parent subsidiary sets are consistent, though: they equal
  the deduped dyads for all 12 parents checked (Reliance Industries 161,
  Infosys 88, Tata Power 71, Tata Steel 48, HDFC Bank 13).
- **Name variants** — the Tata promoter is three entities (`Tata`,
  `Tata sons`, `Tata Sons (including TBExG)`), so an exact-name parent
  lookup can miss.

Plain-word group and parent queries through a leg were measured too
(`group_grammar_probe.py`): of 14 such queries ("subsidiaries of Reliance
Industries", "parent of Jio Platforms", "Tata group companies", "peers of
HDFC Bank", ...), exact keys route 1; adding a closed relation grammar —
strip one relation phrase from the start or the end, then exact-key the
remainder — routes 12, and the 2 misses are bare family names that match a
family label. 0 of the 9 ordinary queries trigger the grammar.

### One query string: what it can resolve to (measured)

Regenerate with `bench_data/company_composite_query/flex_resolver_probe.py`.

| Key | Shape test | Looked up in | Reach | Values mapping to more than one entity |
|---|---|---|---|---|
| ticker | `SYM.NS` / `SYM.BO` | `entities.ticker` | 10,455 entities | 1 |
| bare NSE symbol, typed in caps | all-caps token | ticker stem | 2,819 entities | stems are unique |
| ISIN | 12 characters | `entity_identifiers`, else NSE symbol via ISIN | 7,313 ISINs | 3 |
| FIGI | `BBG` + 17 characters | `entity_identifiers` | 1,232 | 7 |
| CIN | 21 characters | `entities.cin` | 1,020 | 6 |
| exact or normalized name | — | `entities.name`, `normalized_name` | 32,136 names | 17 duplicated names |
| sector, super_sector, sub_sector label | normalized label | `v_node` kinds, `e_belongs`, `e_belongs_to` | 195 labels over 1,231 companies | `Energy`, `Healthcare` are both a sector and a super_sector |

- **Taxonomy labels need normalizing.** 10 of 42 sectors and 80 of 143
  sub_sectors are stored with underscores (`Building_Materials`,
  `Engineering_Capital_Goods`); two super_sectors carry a `_Super` suffix
  (`Energy_Super`, `Healthcare_Super`); the tenth super_sector node,
  `Quotes`, is a stray with no sectors. Membership is 1,231 companies
  through the graph edges and 2,074 through `sector_classification`.
- **Sector universes are small and cheap.** Consumer_Discretionary 247
  companies, Industrials 224, Materials 207, Financials 162; Automotive 103,
  Technology 92, FMCG 86, Banking 54. A sector digest (members, listed,
  NIFTY 50 count) costs about 60 ms.
- **Plain names resolve in tiers.** 49 typed names were run through the
  tiers in order; the 12 long-name variants of the company eval bank all
  resolve.

| Tier | Resolves | Examples |
|---|---|---|
| exact name, case-insensitive | 21 | `Infosys`, `HDFC Bank`, `ITC` |
| suffix-stripped exact (Limited, Pvt, Corporation, ...) | 11 | `Avanti Feeds Limited`, `CEAT Limited` |
| abbreviation table (105 entries, 101 targets stored) | 6 | `TCS`, `M&M`, `Paytm`, `Maruti`, `Nykaa`, `Reliance` |
| falls to ranked candidates, listed first | 8 | `HDFC`, `UltraTech`, `Adani Ports`, `Dr Reddy` |
| needs a curated alias — none is stored | 3 | `SBI`, `L&T`, `HUL` |

- **The existing fuzzy matcher must not pick.** `word_overlap_match`
  returns the first entity contained in the query or containing it. That is
  fine on the vault's names and wrong on the 32,136-name roster: it named
  the right company for 18 of the 49 typed names.

| Typed | Its pick | Correct |
|---|---|---|
| Infosys | HCL Infosystems | Infosys |
| ITC | ABB Beijing Switchgear | ITC |
| ONGC | Falcon Oil & Gas BV, Netherlands | ONGC |
| State Bank of India | Bank of India | State Bank of India |
| Tata Consultancy Services | Tata | Tata Consultancy Services |
| TCS | Max_Life_Tempsens_TCS | Tata Consultancy Services |
| DLF | AFAAF BUILDERS & DEVELOPERS | DLF |
| Adani Ports | Adani Ports & Special Economic zone | same |

- **No initials tier.** Initials of multi-word names give 10,783 acronym
  groups, only 7,703 of them unique; `TCS` alone collides with 8 names.
  Short names that the abbreviation table lacks stay a curated-alias task.
- **Typed keys are quiet on real questions.** Over the 158 question strings
  of three eval banks (`script_eval_questions.json`,
  `embed_eval_questions.json`, `answerability_questions.json`), the typed
  keys fire 8 times, all on genuine company names (`Avanti Feeds`, `CEAT`,
  `Coal India`, ...), and never on ticker, ISIN, FIGI, symbol or taxonomy
  shape.

## 3. Design

### Name

Working name in this proposal: **company_query** (module:
`helpers/research/company_query.py`, CLI:
`.venv/bin/python3 helpers/research/company_query.py`).

Placement: `helpers/analytics/` is harness telemetry (agent traces, model
usage, session monitoring); research-data lanes live in `helpers/research/`
(price join, event study, signal sweep), chartered as report-only lanes —
the right family for a read-only company surface.

Alternatives considered:

| Name | Verdict | Why |
|---|---|---|
| fin_query | acceptable alias | short, but ambiguous ("fin" could mean financial statements only); fine as CLI alias if desired |
| company_query | **recommended** | intuitive, pairs with the graph layer's company relations and the `company_metrics` table, clear scope |
| entity_snapshot | rejected | "snapshot" collides with DB snapshot tooling (`snapshot_db.py`, `make snapshot`) |
| ticker_profile | rejected | ticker-only; misses business-group/CIN/entity-name queries |

Decision: implement as `helpers/research/company_query.py`. Every verb takes
one positional query string; the typed flags (`--ticker`, `--name`, `--cin`,
`--isin`, `--figi`, `--sector`, `--super-sector`, `--sub-sector`) stay as
the explicit form for scripts. Optionally expose fin_query as a tiny
wrapper/alias only if users want it; do not make it the canonical module
name.

### Query resolution — one string, typed keys

Every query resolves to one of four outcomes: **one** entity, a **set** of
entities, a **candidates** list, or **unresolved**. Keys are tried in this
order and the first decisive type wins:

1. **Identifier shape** — ticker, ISIN, FIGI or CIN, each with its own shape
   test, then a lookup on the stored value. A well-formed identifier that is
   not stored is reported as "not in store" with its type; it never falls
   through to name matching.
2. **Bare NSE symbol typed in all caps** — looked up on the ticker stem. The
   caps requirement keeps ordinary words out (`sail` is a word, `SAIL` is a
   company).
3. **Taxonomy label** — sector, super_sector or sub_sector, matched after
   normalization (case-insensitive, underscores as spaces, `_Super`
   dropped). Yields the member set. A label that exists at two levels
   (`Energy`, `Healthcare`) resolves to the sector unless `--as
   super_sector` says otherwise.
4. **Family label** — a curated `family:<Name>` yields its member set and
   routes to the `group` verb.
5. **Relation phrase** — a closed grammar ("subsidiaries of X", "parent of
   X", "X group companies", "peers of X", "acquisitions of X", "ultimate
   parent of X"): strip the phrase, resolve the remainder as above, run the
   `group` verb.
6. **Plain name** — exact, normalized, suffix-stripped, then the
   abbreviation table and alias identifiers; each yields one entity. No
   suffix-stripped or abbreviation hit means **candidates**: prefix and
   token matches ranked listed-first, capped at 10, with the command to
   re-run. Nothing is ever picked automatically below this tier.

Rules:

- Ambiguity inside a key (duplicated names, an ISIN held by two entities)
  yields the candidates outcome with the key named.
- The `master_query` leg (S5) uses keys 1–5 only. Plain-name candidates stay
  in the CLI, where a person reads the list.
- Sector and family universes are the curated ones (1,231 companies through
  the graph edges); they are not the whole listed universe.

### Slices (independently landable)

#### S1 — Read-only composite company snapshot

Add `helpers/research/company_query.py` with a read-only `snapshot()` API and
CLI. The same verb accepts a ticker, a name, an ISIN or a FIGI:

```bash
.venv/bin/python3 helpers/research/company_query.py snapshot SBIN.NS
.venv/bin/python3 helpers/research/company_query.py snapshot "State Bank of India"
.venv/bin/python3 helpers/research/company_query.py snapshot "Avanti Feeds Limited" --format json
```

By identifier: `snapshot INE062A01020` and `snapshot BBG000GQ5749` both
reach `State Bank of India`; so does `snapshot U65190MH1993PLC074381`, the
CIN as stored — the first probe's `L65191MH1955GOI038333` is in no store and
exits as an unknown identifier. `snapshot HDFC` prints the ranked
candidates and exits non-zero without picking one.

Output sections (JSON object / human-readable table): `entity`, `identifiers`,
`centrality`, `graph_analytics`, `edge_counts`, `company_metrics`, `listings`,
`bhavcopy_recent`, `rpt_summary`, `shareholding_recent`, `credit_ratings`,
`index_membership`, `mca_cin`, `figi`.

Rules:

- Read-only; no writes, no schema mutation.
- Source priority: live DuckDB when available/unlocked, snapshot parquet
  fallback. If `memory/research.duckdb` is read-locked, warn and use
  snapshots for research-domain tables; read live `sources.duckdb` for
  source-domain tables.
- House entry points for every open: `connect_snapshots()` mounts the
  snapshot parquet, and live file-backed stores are reached through the
  duckdb-lock module — `open_read_only()` for a standalone connection, or
  `io_lock()` (shared) plus `connect_with_lock_retry()` around a read-only
  `ATTACH` on the snapshot connection, so the lock is held around the open
  only and a served store attaches over the wire. No bare `ATTACH`.
- Identifiers first: the `identifiers` section reads `entity_identifiers`;
  `figi_instruments` adds instrument detail when a row exists.
- Source-domain joins by ISIN (listings, ratings, bhavcopy); the ticker stem
  (`split_part(ticker,'.',1)`) only for tables keyed by NSE symbol. Retain
  the full ticker for entity and FIGI lookup; never conflate NSE/BSE rows
  without exchange/suffix context.
- Index membership reads the latest `as_of` per index.
- Missing-table guards: `information_schema.tables` check before selecting;
  a missing table means an empty section plus a warning, not a crash. Empty
  sections report their source coverage (e.g. "0 of 196 filings").
- Node-ID edge normalization: snapshot `e_*` BIGINT endpoints joined through
  `v_node` before counting or naming; `e_subsidiary` deduped on the node
  pair.
- CIN selects on the stored value; the `mca_cin` section shows `via` and
  `query` next to the CIN. Ticker status (`entity_ticker_status`) is shown
  when a row exists (amalgamated / delisted, with successor).
- BhavCopy section labeled as sample/presence, not OHLCV.

#### S2 — Business-group query (parent/subsidiaries/group peers)

Add `group` subcommand. It accepts a company, a family label or a relation
phrase:

```bash
.venv/bin/python3 helpers/research/company_query.py group RELIANCE.NS
.venv/bin/python3 helpers/research/company_query.py group Tata --depth 2
.venv/bin/python3 helpers/research/company_query.py "parent of Jio Platforms"
```

Scope — a labeled union of the three rosters, never one silent source:

- **Rosters, each labeled with its source and size.**
  - `family`: the curated `family:<Name>` roster, when the company carries
    one. This is the default answer to "group companies" because it is the
    only source that holds promoter groups together.
  - `ownership`: the `ultimate_parent` apex, depth, path and `root_kind`,
    plus every company under the same apex. A `cycle-guard` path is flagged
    as unreliable, never presented as the group.
  - `disclosed`: the filer's related-party set and `e_group` peers.
- **Relations, bounded by `--depth`** (default 2, max configurable):
  parents and subsidiaries over `e_subsidiary` (deduped on the node pair),
  `e_group` peers, optionally `e_acquired`/`e_invested` (`--include
  acquired,invested,group`; default includes subsidiary+group). Reference
  semantics are the graph layer's existing wrappers
  (`subsidiary_of_company`, `group_siblings`, `acquisitions`); S2 adds
  depth, the tree shape and the guards, and does not redefine a relation.
- **Parent of X returns every claimed parent** with its source, not one:
  31% of subsidiaries have more than one parent node.
- **Return shape**: per roster, members with entity name, ticker, CIN,
  kind, relation, depth, source_ref/properties summary.
- Visited set on the relation walk (the RPT-derived edges are not acyclic)
  and a node cap with an explicit truncation warning.

Non-goal for S2: probabilistic corporate-family inference. Use existing
rosters and edges; do not invent ownership.

#### S3 — Bulk analytics / batch mode

Add `bulk` subcommand over any resolved set:

```bash
.venv/bin/python3 helpers/research/company_query.py bulk --tickers-file tickers.txt --fields entity,identifiers,figi,index_membership --format jsonl
.venv/bin/python3 helpers/research/company_query.py bulk --index "NIFTY 50" --format csv
.venv/bin/python3 helpers/research/company_query.py bulk Banking --format csv
.venv/bin/python3 helpers/research/company_query.py bulk "Consumer Discretionary" --format jsonl
```

Rules:

- The universe is whatever the query resolves to: one entity for a ticker,
  ISIN, FIGI or name; N for a sector, super_sector, sub_sector or family
  label; a file or an index for the explicit forms. Files may mix tickers,
  ISINs and FIGIs, one per line.
- Batch resolves the universe once; reuses DuckDB connection/views.
- `--fields` selects sections to avoid expensive joins when not needed.
- JSONL/CSV output; stable schema; one row/object per member; unresolved
  members are reported on stderr with their candidates, never dropped
  silently.
- Bounded concurrency: default single-process DuckDB; no parallel writers.

Landed 2026-11-28 (§7): a `--summary` flag emits one aggregate row per numeric
field (count, sum, median) for a set — top-level numeric columns, list-section
lengths, and numeric `company_metrics` values (raw strings like `"1,000 Cr"`
are skipped, not guessed); per-member rows already carry the data.

#### S4 — Tests, docs, and guardrails

- `tests/test_company_query.py`: fixture DuckDB/parquet with node-ID edges,
  missing FIGI table, bhavcopy sample rows, symbol normalization, the
  resolver tiers (each key type, the four outcomes, label normalization,
  the stray taxonomy node), the three group rosters, group cycle, and
  lock-fallback behavior.
- Doc: `doc/design/` or `doc/reference/` short reference page; update
  `doc/design/data_sources.md` / `doc/design/db_schema.md` only if contracts
  change (not expected for read-only query).
- Static checks: no raw sqlite3 connect; `memory_cap_config()` on DuckDB
  connects; live opens and attaches only through the duckdb-lock entry
  points; no write path.

#### S5 — `master_query` opt-in digest leg (`--legs company`)

Deferred until S1 lands: the leg is a thin client of the composite and has
nothing to point at before it exists. Scope is front-door discovery, not
data delivery — a session that asks `master_query` for a ticker learns that
the composite holds data and which command fetches it. The measurements
behind the rules are in §2.4–§2.6.

```bash
.venv/bin/python3 helpers/misc/master_query.py "SBIN.NS" --legs company
.venv/bin/python3 helpers/misc/master_query.py "Banking" --legs company
.venv/bin/python3 helpers/misc/master_query.py "SBIN.NS" --legs all
```

One hit per typed-key match, in the standard leg render — an entity digest
for one entity, a set digest for a sector or family:

```text
## company  (1 hits · typed-key digest)
  company:SBIN.NS [1.0000] company State Bank of India · Banking
      BSE+NSE · NIFTY 100/NIFTY 50 · mcap ₹885,078 Cr · run: company_query.py snapshot --ticker SBIN.NS
  sector:Banking [0.5000] sector Banking · 54 companies (44 listed, 5 in NIFTY 50)
      run: company_query.py bulk Banking --format csv
```

Rules:

- **Typed keys only** — resolver keys 1–5 of "Query resolution": identifier
  shape, bare symbol in caps, taxonomy label, family label, exact or
  normalized name, and the relation grammar. No substring, token, initials
  or fuzzy matching.
- **Ambiguity is surfaced, not resolved** — more than one match on a key
  emits one hit per candidate (capped at `--limit`), prefixed
  `ambiguous (N)`.
- **No match is an honest `0 hits`**, exit-0 eligible like any other leg.
- **Hit shape** — path `company:` or `sector:` plus the key, kind to match,
  snippet at most 140 characters so it survives grouped render, ending in
  the composite command to run.
- **A pointer, not a carrier** — no section rows; the composite CLI serves
  the data.
- **Backend** — a digest subcommand on the composite CLI emitting JSON,
  called by subprocess from a `search_tui` adapter registered in
  `_LANE_RUNNERS` only. The master CLI keeps owning no backend logic and
  TUI `LANES` stay at 8 (the git-leg precedent).
- **Registration** — `_LEGS` and `ALL_LEGS`, so `--legs all` runs it; not in
  `DEFAULT_LEGS`. Age-guard exempt (no sidecar): the digest resolves
  live-first and names its source per domain in the JSON `sources` key
  (amended 2026-11-28, §7).
- **Cost** — one entity digest as its own process: 0.40 s wall, 92–95 MB
  RSS, hidden by the 5.3 s parallel fan-out; a set digest adds about 60 ms.

Landed 2026-11-28 (§7): live-first resolution for the digest — a served
store routes through the quack wire client (`connect_shared`), an unserved
one attaches read-only through the lock ladder, and the snapshot is the
fallback of last resort, per domain. Parked still: a `DEFAULT_LEGS` slot
only with a fresh measurement — the wall cost
is hidden, so what a default slot buys is an always-empty section on nearly
every query.

Tests and docs:

- `tests/test_master_query.py`: `parse_legs` accepts the new leg and
  `parse_legs("all")` includes it; `DEFAULT_LEGS` unchanged; the adapter
  parses recorded JSON fixtures (entity digest, set digest) with no
  database; an unavailable snapshot degrades to a status line and never
  fails the fan-out.
- `tests/test_company_query.py`: every typed key resolves and nothing else
  does — `HDFC`, `HDFC Bank Limited`, `SBI` and `promoter holding` return no
  hit through the leg; a non-stored CIN returns none; a duplicated key
  emits N candidates; `consumer discretionary` and `Consumer_Discretionary`
  resolve to the same set; every snippet is at most 140 characters.
- Docs: the `master_query` module docstring leg list, the
  `doc/procedures/search.md` master section, and an AGENTS.md surface row.

### Source resolution strategy

```text
<query> -> typed target -> entity set
  identifier shape (ticker, ISIN, FIGI, CIN)      -> stored value -> one entity
  bare symbol in caps / exact name / suffix-stripped / abbreviation -> one entity
  sector, super_sector, sub_sector or family label -> member set
  relation phrase + remainder                      -> group verb
  anything else                                    -> ranked candidates (CLI only)
  -> entity row + node id (entities / v_node) for each member
  -> identifiers (entity_identifiers: isin, figi, cik)
  -> graph edges/counts via v_node id mapping; group rosters labeled family / ownership / disclosed
  -> live sources.duckdb for exchange_listings/bhavcopy/rpt/shp/ratings/index/mca/figi
  -> snapshot parquet fallback when live table unavailable/locked
```

The helper must degrade gracefully when a source table or view is absent,
and must never wait on a live-store lock when the snapshot can answer.

## 4. Acceptance criteria & shakedown

1. `.venv/bin/python3 helpers/research/company_query.py snapshot SBIN.NS --format json` returns JSON with required section keys and no traceback when `figi_instruments` is absent.
2. Each key type reaches `State Bank of India` by its stored value: the ticker, the ISIN, the FIGI, the CIN, the exact name and the suffix-stripped long name; a well-formed identifier that is not stored exits non-zero naming the key type.
3. Edge counts/group traversal use `v_node` ID-name mapping; no BIGINT-vs-VARCHAR binder/conversion errors on snapshot edge tables.
4. `group RELIANCE.NS --depth 2` returns the three labeled rosters (family 67, ownership 260, disclosed 276 on the current snapshot), flags cycle-guard paths, lists every claimed parent with its source, dedupes cycles, and reports truncation when the cap is hit.
5. `bulk --fields ... --format jsonl` emits one object per member and honors field selection; `bulk Banking` and `bulk "Consumer Discretionary"` emit one object per member (54 and 247 on the current snapshot).
6. Read-only: test asserts no writes to `memory/*.duckdb`, no temp DBs outside `$TMPDIR`.
7. `ruff format --check` + `ruff check` + targeted pytest pass; `make static-checks`/`make lint-audit` clean for touched files.
8. No full `make qa`/perf rerun required by this proposal alone; validate by targeted tests.
9. `snapshot HDFC` prints ranked candidates, listed first, and exits non-zero without picking one; `snapshot Infosys` resolves to `Infosys` and never to `HCL Infosystems`.
10. (S5) `master_query.py "SBIN.NS" --legs company` prints one `company:SBIN.NS` hit whose snippet ends with the composite command; `master_query.py "Banking" --legs company` prints a set digest ending in the bulk command; `master_query.py "promoter holding" --legs company` prints `0 hits`.
11. (S5) The default fan-out is unchanged: `master_query.py "embed cache"` output is identical with and without S5, `parse_legs("all")` includes the leg, and TUI `LANES` stay at 8.
12. (S5) Nothing below the typed keys resolves through the leg: `HDFC`, `SBI` and `promoter holding` return no hit; `tata` resolves to the `family:Tata` set — the family label precedes the plain-name tier in the resolution order (§3). *(Amended 2026-11-28: the original line said `tata` returns only the entity literally named `Tata`, which predates the final key order — see §7.)*

| Projected outcome | Today | After S1-S3 |
|---|---|---|
| One-company composite answer | ad hoc SQL, 10-15 min, schema-fragile | one command by ticker, name, ISIN or FIGI; stable JSON/table sections |
| Business-group lookup | three disagreeing sources, no single call | `group` returns all three rosters, labeled, plus a bounded relation tree |
| Sector / super_sector analytics | hand-joined SQL, underscored labels | a sector or super_sector label resolves to its member set for `bulk` |
| Bulk ticker analytics | bespoke script per request | `bulk` over any resolved set with field selection + JSONL/CSV |
| Missing FIGI/bhavcopy/live-lock behavior | ad hoc failure | explicit fallback/empty section + warning |
| Company data through the `master_query` front door (S5) | locators only; no data pointer | opt-in `--legs company` digest on typed keys; default legs unchanged |

## 5. Risks

- **Snapshot/live drift**: snapshots can lag live source DBs. Mitigation: prefer live read-only where possible; include `source_mode`/freshness metadata in output.
- **Read lock contention**: `memory/research.duckdb` may be locked by maintenance. Mitigation: open through the duckdb-lock entry points, lock-aware fallback to snapshots, clear warning; never take write lock.
- **Schema drift**: source tables evolve (bhavcopy/figi columns). Mitigation: select explicit columns, guard missing tables/cols, test fixtures pin expected contract.
- **BIGINT node-id edge shape**: snapshot `e_*` differ from name-shaped expectations. Mitigation: normalize through `v_node`; tests cover this.
- **Disagreeing group sources (S2)**: no single roster is "the group" (§2.5). Mitigation: labeled union, curated family roster as the default, cycle-guard paths flagged, every claimed parent shown.
- **Fuzzy auto-pick**: the existing substring matcher picks the wrong company on most typed names when run on the full roster (§2.6). Mitigation: it may rank candidates only; no automatic pick below the exact tiers.
- **Identifier ambiguity**: a few ISIN (3), FIGI (7) and CIN (6) values map to more than one entity. Mitigation: candidates outcome with the key named.
- **Taxonomy labels**: underscored storage, the `_Super` suffix, a stray `Quotes` node, and two labels that exist at two levels; membership is 1,231 companies through the graph edges. Mitigation: normalization in one place, level disambiguation, stray node ignored, coverage stated in output.
- **Short names without an alias**: `SBI`, `L&T` and `HUL` match no stored key. Mitigation: candidates plus a parked curated-alias task; no initials tier (measured noisy).
- **Large group traversals**: cyclic/huge groups can explode — the RPT-derived subsidiary edges are not acyclic and 2,876 subsidiaries have several parent nodes. Mitigation: depth cap, node cap, visited set, truncation warning.
- **Leg noise (S5)**: any non-exact matching rule fires on ordinary queries (measured, §2.4). Mitigation: typed keys only, opt-in leg; a `DEFAULT_LEGS` slot needs a fresh measurement.
- **CIN provenance**: `mca_cin` rows come from harvest (677), auto-web consensus (290) and yahoofin (53); SBI's stored CIN was an auto-web 2-vote row. Mitigation: show `via`/`query` beside every CIN; a CIN is a lookup on the stored value, not a registry check. Resolved 2026-11-28: that row was wrong — `U65190MH1993PLC074381` belongs to SBI Commercial and International Bank Ltd (struck-off 1993 subsidiary); a manual sidecar row (manual wins on conflict) now carries `L65191MH1955GOI038333` (SBI Act 1955 statutory body), applied to `entities.cin` surgically and recorded with the correction in `query`.

## 6. Non-goals

- No write path, no cache materialization, no new canonical DB tables.
- No OHLCV/price analytics (bhavcopy sample is presence/sample only).
- No probabilistic entity resolution or ownership inference beyond existing rosters and edges.
- No web/API service; CLI + importable Python API only.
- No full-graph export; query is entity/ticker/group/sector scoped.
- Not a default `master_query` leg, and no fuzzy, substring or initials matching inside the leg (S5 is typed-key and opt-in).

## 7. Execution results (2026-11-28)

Landed: `helpers/research/company_query.py` (S1-S3, S5 wired; operator folds into the patch stack), `tests/test_company_query.py` (63 fixture-backed tests over parquet snapshots — no live-store reach), `TestCompanyLeg` in `tests/test_master_query.py` (7 tests). Docs: `doc/reference/company_query.md`, AGENTS.md search-surfaces row, `doc/procedures/search.md` master section.

| Validation | Result |
|---|---|
| ruff format + check (modules + tests) | clean |
| `make types` (ty) | rc=0 |
| targeted pytest (63 + 35) | pass |
| `make static-checks` / `make lint-audit` | rc=0 / rc=0 (C901 noqa per house pattern; S608 per-file rationale) |
| md-lint (4 doc files) | clean |
| doc-drift `ripwire . --doc-drift=company_composite` | drift=0 (anchors 206, checked 50) |
| AC smoke `bench_data/company_composite_query/ac_smoke.py` | ALL PASS |

| AC | Result |
|---|---|
| 1 | snapshot SBIN.NS json: all 14 sections, live mode; figi-absent case covered by fixture |
| 2 | ticker / isin / figi / cin / exact / suffix-stripped all reach State Bank of India; unstored isin → rc=1 `not in store: isin` |
| 3 | v_node ID joins clean across snapshot/group; no binder errors |
| 4 | rosters family 67 / ownership 260 / disclosed 309, each carrying `source`; root_kinds self-root; cycle flags + truncation exercised by fixtures |
| 5 | bulk Banking 54 rows, Consumer Discretionary 247, `--fields` honored |
| 6 | fixtures assert read-only (tmp_path parquet, no WAL) |
| 7 | see validation matrix |
| 8 | skipped full qa by design; targeted tests only |
| 9 | snapshot HDFC → 10 candidates rc=1 listed-first; snapshot Infosys → Infosys |
| 10 | entity / set / 0-hits digest all render through `master_query --legs company` as specified |
| 11 | DEFAULT_LEGS stays 7, `parse_legs("all")` includes company, TUI LANES stay 8 |
| 12 | HDFC / SBI / `promoter holding` → no hit. **Deviation**: `tata` → `family:Tata` set (235), not the literal entity — the family key precedes the plain-name tier by design (§3 resolution order); the AC line predates the final key order |

Notes: AC4's projected disclosed 276 measured 309 — snapshot drift since the probe (hyperedge 276 members + cross joins), not a behavior change. Fixtures caught four real defects pre-landing: `has()` snapshot prefix mismatch (every guarded section silently skipped), BBG-prefixed FIGI strings satisfying the ISIN shape (figi tried first now), hyper_edges joined against the wrong domain (`{t2}` two-table fetch), and `ultimate_parent.path` returned as a JSON-encoded string (now decoded).

### 2026-11-28 addendum — operator four-item close (data fix + two un-parked slices)

1. **SBI CIN corrected** (§5 CIN provenance, resolved). The stored
   `U65190MH1993PLC074381` was the struck-off 1993 subsidiary *SBI
   Commercial and International Bank Ltd*, not SBI (auto-web 2-vote
   consensus attached the wrong company). Fix through the owning lane:
   `memory/data/mca_cin_manual.csv` row (manual wins on conflict) →
   `mca_cin_sync.py build --apply` (1,020 rows, SBI row replaced) →
   surgical `backfill_identifiers.py --set-cin "State Bank of
   India=L65191MH1955GOI038333" --apply` (one entity written, no
   `last_updated` churn on the other 1,019). Verified live: the new CIN
   resolves to SBI, the old one reports `not in store: cin`, and the
   `mca_cin` section shows `via=manual` with the correction in `query`.
   The sqlite-side snapshot parquet still carries the old CIN until the
   next maint-full snapshot refresh — live-first resolution is the
   mitigation, which is what un-parked the digest slice below.
2. **`bulk --summary` landed** (§S3 parked slice). One aggregate row per
   numeric field over the per-member rows: `bulk Banking --summary
   --format csv` → `identifiers,54,45.0,1.0` and
   `index_membership,54,367.0,4.0` (a length of 0 counts — absence is
   data); `bulk --index "NIFTY 50" --summary --fields
   entity,company_metrics` → 49 members, 34 numeric metric fields (e.g.
   `company_metrics.beta` count 46 · median 0.33), one unresolved ISIN
   on stderr. Non-numeric metric strings (`"1,000 Cr"` in the fixture)
   are skipped, never guessed.
3. **Live-first digest resolution landed** (§S5 parked slice). Every
   domain now resolves live-first: a served store routes through the
   quack wire client (`connect_shared` — `memory/graph.duckdb` is served
   on port 9494), an unserved one attaches read-only through the lock
   ladder, and the snapshot is the fallback of last resort. The digest
   JSON names its source per domain: `"sources": {"research": "live",
   "sources": "live", "graph": "wire"}`. The wire path immediately caught
   a latent defect: 14 section queries hardcoded the snapshot view name
   `duckdb__v_node`, which only worked because the graph domain had been
   snapshot-only — now a `{v}` placeholder renders per mode
   (snapshot/live/wire). Full 14-section snapshot, `group` (family 67 /
   ownership 260 / disclosed 309 / subsidiaries 333 edges) and digest
   verified over the wire, rc=0, zero warnings.
4. **AC12 amended** — `tata` resolves to the `family:Tata` set (family
   label precedes plain-name in §3's order); AC text now matches the
   landed key order.

Validation: 104 tests green (`tests/test_company_query.py` 69 +
`tests/test_master_query.py` 35; +6 new: 2 wire routing, 4 summary),
`make types`/`make static-checks` rc=0.

## Appendix A — first-probe measurement log (2026-11-27, session A)

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-27 | `duckdb` read-only attach sources; `information_schema.tables` | `sources.duckdb` has 9 tables including `bhavcopy_sample` (74641 rows) and `figi_instruments` DDL path; `figi_instruments` absent before live apply completes | live source inspect |
| 2026-11-27 | `find snapshots/parquet -name '*.parquet'` + `pragma table_info` | confirmed v_node/e_* BIGINT endpoint schemas; exchange_listings has no `series`; bhavcopy has no OHLCV | snapshot schema inspect |
| 2026-11-27 | composite SBIN.NS DuckDB probe (views over snapshots + ATTACH sources) | initial failures: `e_subsidiary.child_name` missing, `series` missing, `close` missing, BIGINT/VARCHAR mismatch on `e_invested` | established normalization/guard requirements |
| 2026-11-27 | corrected composite SBIN.NS probe | entity/identifiers/metrics/listings/bhavcopy sample/rpt/shareholding/ratings/index/mca returned; figi empty (table absent) | proof of composite feasibility; SBI CIN and shareholding cells superseded by Appendix B |
| 2026-11-27 | `witr -p 669582` + log tail | FIGI run active, setsid-detached, progress `3750/7354 tickers, 1089 records` at 18:37:04 | concurrent lock context |

## Appendix B — re-measurement log (2026-11-27, session B)

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-27 | `master_query.py "SBIN.NS" --limit 3` (default legs, hybrid) | 5.3 s wall; notes leg top hit `findata/Companies/Banking/State_Bank_of_India.md`; no structured data row in any leg | front-door baseline for §2.4 |
| 2026-11-27 | `bench_data/company_composite_query/leg_noise_probe.py`, 3 runs | noise table (§2.4) identical across runs; exact-key resolve median 12–13 ms (n=18), digest median 18 ms (n=6) | matching-rule measurement |
| 2026-11-27 | one-query digest prototype, 3 runs | 0.40 s wall, 92–95 MB RSS (import + 84-view mount + resolve + digest) | leg cost |
| 2026-11-27 | live `research.duckdb` read-only open, 3 runs | 0.020–0.024 s open, 6–13 ms exact lookup; SBI CIN `U65190MH1993PLC074381`; no entity carries `L65191MH1955GOI038333` | first-probe CIN corrected |
| 2026-11-27 | `sources.duckdb` census | 9 tables + 4 `vw_*` views; `figi_instruments` 1,230 rows (IN 968 / US 262); `mca_cin` by `via`: harvest 677, auto-web 290, yahoofin 53; SBI: 0 `shp_filings` rows, 277 `credit_ratings` rows, 268 `rpt_transactions` rows | FIGI run finished (`witr -p 669582`: no such process) |
| 2026-11-27 | snapshot census | `ultimate_parent` 25,200 rows (root_kind in the quirks table, rosters in §2.5); `e_subsidiary` 17,348 edges; exact-key duplicates: 17 names, 6 CINs, 1 ticker | S2 rescope; S5 ambiguity rule |
| 2026-11-27 | `check_proposal_lifecycle()` | 2 FATAL on the proposals README live list (the archived exchange/ticker proposal still advertised as live) | fixed in the same change by an archived stub |

## Appendix C — third-pass measurement log (2026-11-27, session C)

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-27 | `bench_data/company_composite_query/group_sources_probe.py` | per-group table (§2.5); Reliance Lithium Werks B.V is its own root now (2 rows under it); apex breakdown for the five largest ownership roots | group rosters |
| 2026-11-27 | `bench_data/company_composite_query/group_grammar_probe.py` | 14 plain-word group/parent queries: 1 route by exact keys, 12 with the relation grammar; 0 of 9 ordinary queries trigger the grammar; 51 entity names end with a grammar keyword, exact keys tried first | leg routing for group forms |
| 2026-11-27 | `bench_data/company_composite_query/flex_resolver_probe.py` | identifier reach and ambiguity (7,313 ISINs reachable); taxonomy labels and membership; 49 typed names by tier (21 exact, 11 suffix-stripped, 6 abbreviation, 8 to candidates, 3 needing an alias); fuzzy matcher right on 18 of 49; typed keys fire 8 times over 158 real questions, all company names; sector digest ~60 ms | flexible query design |
| 2026-11-27 | initials-tier check | 10,783 acronym groups over multi-word company names, 7,703 unique; `TCS` collides with 8 names | initials tier not adopted |
