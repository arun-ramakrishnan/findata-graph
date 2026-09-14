---
title: "HGX lane wiring — hyper compute into make/maint + doc truth"
status: proposed
filed: "2026-09-15"
executed: null
completed_md: null
area: "helpers/graph (hyper lanes), helpers/maintenance/maint.py, Makefile, doc/design — wiring arc, no schema change"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# HGX lane wiring — hyper compute into make/maint + doc truth

**Date:** 2026-09-15 · **Status:** PROPOSED ·
**Area:** helpers/graph (hyper lanes), maint.py, Makefile, doc/design —
wiring arc, **no schema change** (trigger: /tmp/hgx_gaps.txt findings,
verified against the repo)

## 1. Problem (verified 2026-09-15)

The #233 hypergraph arc landed the star store WIRED (derive_hyperedges:
make target + maint-full TIER2, before the tail snapshot) but its two
compute lanes never got the same treatment:

1. **Not automated**: `hyper_communities.py` (hypermmsbm_community) and
   `hyper_centralities.py` (ho_pagerank, s_betweenness, s_closeness;
   eigen_cec/zec/hec skipped at the non-uniform default scope) are
   standalone CLIs with no make target and no maint slot — the 4 live
   metrics (1,165 company rows each) go STALE after every maint-full
   until run by hand, and only land in the snapshot if run before it.
2. **Crash on degenerate stores**: both lanes exit 1 on absent
   `hyper_edges`/`hyper_incidences` tables (duckdb CatalogException) and
   on empty-after-degeneracy-guard (ValueError) — fine for a hand-run
   CLI, fatal for a maint step on fresh/minimal DBs (the chain fixture
   can exhaust the guard).
3. **Doc falsehoods**: db_schema.md says `graph_analytics` is "Written
   ONLY by algorithms.py (`make recompute-graph`)" and graph_design.md
   §5.6 hangs "(+ hyper lanes)" off the recompute-graph clause —
   recompute-graph computes neither. (Correction to the findings file:
   the `graph.ts` "run make recompute-graph" hints serve only
   `louvain_community` + the scalar rank select — metrics recompute-graph
   DOES compute — so no frontend change is needed; hyper metrics are
   simply not surfaced in the UI/API at all, which is a deferred
   non-goal, not a hint bug.)

## 2. Design

- **Two new TIER2 steps**, not folding into `algorithms.py --all`: hyper
  metrics consume the incidence store that `derive-hyperedges` refreshes
  later in TIER2 — folding would compute from stale incidence or force
  reordering a settled step; engine separation (Onager dyadic vs HGX)
  was deliberate in #233. Placement: after `derive-hyperedges`, before
  the tail `snapshot` (SQLite-only writes; the snapshot captures the
  metrics).
- **Never-block first (W1)**: absent tables / no rows for the requested
  sources / guard-exhausted → printed skip + rc 0 at the CLI level;
  compute functions keep raising (their behavior is pinned). Genuine
  compute errors still fail loudly.
- **Zero-churn is inherited, not hoped for**: both lanes are seeded
  (hy-MMSBM seed 42, eigen trio seed 42, power iterations from uniform
  start) and write via `algorithms.write_analytics` — a true upsert
  (`ON CONFLICT DO UPDATE` sets only `value`; `computed_at` stamps on
  INSERT only; no triggers on graph_analytics) — so warm maint-full
  cycles converge byte-stable values into the parquet snapshot, the
  same class as the recompute-graph step (#147 doctrine).
- **On-demand command**: `make recompute-hyper` (pairs with
  `recompute-graph`) runs both lanes `--apply` with defaults — sources
  `sector,theme,industry`, k 8, seed 42, s 1 — matching live metric
  semantics exactly; source-scope changes are operator decisions and
  stay manual.

## 3. Slices

- **W1 skip-hardening + write-path tests**: `_cli` store probe (tables
  present? rows for `--sources`?) before load; skip cleanly when
  nothing to compute. NEW tests: the `--apply` → `graph_analytics`
  write path (currently unpinned — rows appear, re-run idempotent,
  `computed_at` unchanged) + skip paths (absent tables, empty store,
  guard-exhausted).
- **W2 Makefile**: `recompute-hyper` target + `.PHONY` + help entry
  (alphabetical r-cluster).
- **W3 maint wiring**: TIER2 entries `hyper-communities` /
  `hyper-centralities` after derive-hyperedges; test_maint pins
  (TIER2 9→11, full composition 18→20 ×2 sites); two dispatcher shims
  in test_integration_maint_chain.py (redirect
  `hyper_arrow.DEFAULT_DB_PATH` + `algorithms.connect` for the lazy
  `write_analytics` import).
- **W4 doc truth**: db_schema.md graph_analytics ownership (algorithms.py
  AND the two HGX lanes, all via the write_analytics upsert; refresh =
  recompute-graph / recompute-hyper / maint-full); graph_design.md §5.6
  — drop "(+ hyper lanes)" from the recompute-graph clause, add the
  lanes' own refresh line; maintenance.md TIER2 flow sentence.
- **W5 live apply**: `make recompute-hyper` (values already live →
  converging upsert, `computed_at` untouched); row-count check before /
  after.

## 4. Acceptance

- Chain: maint-chain integration green with the two steps shimmed
  in-process (guard-exhausted fixture skips, never blocks); test_maint
  pins enumerate 11 TIER2 / 20 full-composition steps.
- Zero-churn: re-run `make recompute-hyper` on an unchanged store →
  identical `graph_analytics.value` set, `computed_at` column unchanged.
- Docs: no remaining claim that recompute-graph computes hyper metrics.
- Gates: targeted per slice; full `make qa` + advisory once at arc end;
  close order maint-full → search-fresh → snapshot.

## 5. Deferred tasks (durable backlog — this arc's non-goals, folded as tasks)

Working order (agreed 2026-09-15, operator amended D9 to last):
**D1** (visibility, own small proposal) → **D8** → **D5+D6** (one
extract_relations arc — the regroup keys already exist in
derive_hyperedges) → **D7** → **D11** (operator authoring pass over
the unmapped subsector worklist, opened 2026-09-15) → **D3** (operator
checkpoint: re-baseline once, over authored data) → **D2** → **D9**
(mechanical closer); **D4 / D10 deferred** (no SQL-over-incidence
consumer; motif lane viable on dense sources but prediction needs the
fine-grained lanes denser — measured 2026-09-15: group 8/k=3.8, jv
6/k=2.0). Sequencing law: capture density
(D8→D5/D6→D7) precedes semantics (D3) precedes new lanes (D4/D10);
visibility (D1) first because it is independent and makes the rest
observable. Each multi-slice item files its own proposal at start time.

Own (were §5 non-goals):

- **D1 Hyper metrics in the UI/API** (EXECUTED 2026-09-15 — own
  proposal `hyper_metric_ui_visibility.md`): extend the three fixed rosters —
  app.py `_GRAPH_METRIC_ALLOWLIST` (scalar/label/payload buckets;
  hypermmsbm is label-shaped, ho_pagerank/s_* scalar),
  `templates/findata.html` rank-metric `<select>`, `graph.ts`
  `METRIC_BLURBS`. Decides what the Rank panel + community shading can
  promise; until then the UI surfaces nothing hyper (the graph.ts
  recompute-graph hints stay correct for the dyadic metrics they serve).
- **D2 HIF export wiring** (`hyper_hif.py`): a make target + maybe a
  snapshot rider; today on-demand file output only.
  EXECUTED 2026-09-15: `make hif-export` (defaults SOURCES=sector,
  sub_sector,group,jv → snapshots/hif/; canonical zstd parquet per
  architecture.md §10) + rider on `make snapshot`; --validate
  round-trip green (142 edges / 1,985 incidences).
- **D3 Source-scope change** (`sub_sector`/`country` into
  DEFAULT_SOURCES): semantic change to every hyper metric; operator
  decision + re-baseline, never a silent default change.
  EXECUTED 2026-09-15 (operator go; sub_sector only, country stays
  opt-in; operator offered country 2026-09-15, measured: the lane is
  1 giant (india, 850 members = 72% of nodes — guard-stripped) + 12
  singletons + 8 usable edges; the usa edge (41 members) acts as a
  41-clique in the s-walk projection — SpaceX becomes #1 s_betweenness
  (0.157) purely as an artifact — so country stays opt-in):
  DEFAULT_SOURCES = sector, theme, industry, sub_sector
  (scope 171→257 edges, 2,352→3,123 incidences, node set unchanged);
  re-baselined ho_pagerank/s_betweenness/s_closeness (1,171 rows) +
  hy-MMSBM k=8 (1,178 rows; sub_sector labels now dominate block
  signatures — Iron_and_Steel, Formulations). Note: write_analytics
  UPSERTs value-only, computed_at stays at first-insert (by design).

Inherited — still-open items pulled from the archived #233 ledger
(`archive/graph/hypergraph_incidence_hyx.md` §6; landed items NOT
pulled: HIF lane S18, quotes regroup S8, counterparty FK S9, industry
regroup S5):

- **D4 DuckDB `h_*` materialisation** (#233 §6): the cache does not read
  the incidence tables (the maint.py "Phase 1 cache" note) — reopen when
  a SQL-over-incidence consumer appears; D1's API work is the likely
  first consumer.
- **D5 JV venture capture upgrade** (#233 capture-gap; EXECUTED
  2026-09-15 — own proposal `jv_promoter_capture_upgrade.md`):
  extract_relations capturing `venture` into jv_with properties (2/69
  rows at filing; 6/81 live after the name-before-marker families +
  props-converge) — the regroup key for real JV partner sets.
- **D6 Promoter-group prose extraction** (#233 capture-gap; EXECUTED
  2026-09-15 — same proposal as D5): same extract upgrade as D5; 1
  seed group at filing; 8 group hyperedges live after the cross-note
  accumulation lane.
- **D7 company→sub_sector YAML capture discipline** (#233 capture-gap;
  EXECUTED 2026-09-15 — own proposal `company_subsector_authored_lane.md`):
  optional `subsector:` YAML field + sync_tags lane (the derived
  S11/S17 industry→sub_sector map covers 600 memberships; authored
  classification is canonical — exclusive precedence, unknown values
  worklisted; live no-op with 0 authored values until the operator
  authors).
- **D8 Concall-title edition normalisation** (#233 §6.1 note): quote
  `as_of_edition` concall titles riding as editions.
- **D9 Three .txt stragglers under archive/graph/** (#233 DEFERRED):
  `networkx_duckpgq_gap_plan.txt`, `graph_improvs.txt`,
  `hierarchy_design_roadmap.txt` — markdown migration incomplete until
  converted (repoint archive/README in the same change; the synthetic
  .txt fixture in test_rebuild_doc_search stays by design until then).
  EXECUTED 2026-09-15: all three converted to frontmatter+markdown
  (lint-clean), archive/README + verify_notes.py comment + hyx §6 +
  graph_docs_ui_polish references repointed.
- **D10 Hyperedge prediction / motifs / dynamics lanes** (#233 §6):
  gated on incidence density (the #233 flagship argument) — not before
  D5–D8 move the capture forward.
- **D11 Operator authoring pass — `subsector:` values over the unmapped
  worklist** (opened 2026-09-15, follow-up of D7; IN PROGRESS — A-bucket
  applied, B-bucket rulings complete, single apply pass pending):
  hand-author `subsector: <Sub_Sector_Entity_Name>` on company notes,
  starting with the unmapped industries in
  `findata/Misc/subsector_worklist.json` (Banks - Regional ×38, Credit
  Services ×23, Capital Markets ×10, …) and any mapped company the alias
  union classifies poorly — authored is canonical, unknown values are
  worklisted, `sync_tags` mirrors the slug. Operator-owned surface (D7
  wired the lane; no agent authoring). Best BEFORE D3's re-baseline so
  the one-time source-scope change lands over real authored data.
  Naming rule (operator, 2026-09-15, encoded in the D11 apply): children
  name the segment only — the parent supplies the domain word
  (Vehicle_Loans/Gold_Loans/Microfinance yes; Telecom_Services under
  Telecommunications no); never collide with a sector name; preferred
  name source is the Indian classification list (see D12).
- **D14 Exchange-master universe seed — incl. SME boards, tickers, and
  query coverage** (filed 2026-09-15): the graph today is corpus-driven —
  1,178 company entities, all born from newsletter/relation mentions
  (create_entity), 950 tickered (731 .NS). The operator wants the listed
  universe seeded from exchange masters, SME boards included, so queries
  cover SMEs with tickers. Scope: (a) pull NSE EQUITY_L (~2k, slim — no
  industry), NSE index lists with Industry (nifty500 + nifty total-market
  ~755, `ind_niftytotalmarket_list.csv` URL confirmed live), NSE SME/
  EMERGE list (via the NIFTY SME EMERGE index page), BSE main board and
  BSE SME (ListofScripData API — needs bseindia.com Referer headers;
  exact endpoint to be pinned at execution); (b) dedup by ISIN across
  boards (dual listings common); Active-only filter; (c) create stub
  company entities for names missing from the graph — NO notes (schema
  legal; note-derived lanes skip them naturally, entity/alias lanes
  cover them), each with Yahoo-style ticker (.NS/.BO by board) and
  sector_classification from the exchange industry column; (d) feed
  Yahoo industry labels via the get_tickers/enrich lane so the S11
  alias map derives sub_sector hyperedges for stubs; (e) resolve CINs
  for new entities through the D12 sidecar lane (mca_cin_sync resolve);
  (f) verify derive/worklist/query surfaces handle note-less stubs.
  Decision recorded: universe becomes corpus ∪ listed-market, SMEs
  explicitly in.
  ENDPOINTS (verified 2026-09-15; canonical register:
  `doc/design/data_sources.md`; store:
  memory/data/sources.duckdb::exchange_listings, 24,305 listing rows
  (long format — exchange/segment/symbol per row; supersedes the retired
  exchange_sources.parquet)). NSE (static CSVs on nsearchives — browser UA +
  Referer `https://www.nseindia.com/`; `www.nseindia.com` `/api/*` routes
  need cookie handshakes and mostly 404 — avoid): main board slim list
  `nsearchives.nseindia.com/content/equities/EQUITY_L.csv` (~2.6k, NO
  industry; header fields carry leading spaces); industry-bearing index
  lists `.../content/indices/ind_nifty500list.csv`,
  `.../ind_niftytotalmarket_list.csv` (755; underscore before `list` —
  find via the index page href),
  `.../ind_niftysmelist.csv` (SME EMERGE index, 529, Series SM).
  BSE (api.bseindia.com needs `Referer: https://www.bseindia.com/` +
  browser UA): full instrument master
  `api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?...Segment=Equity&status=Active&pagesize=5000`
  — Segment param ignored (returns ALL segments: 12,855 rows =
  equity 5.2k + debt 5.4k + CP + MF + pref); INDUSTRY field null;
  Group=SME returns 0 (SME board not in this API; MT group 128 =
  migrated-to-main). Official BSE SME board WITH industry:
  `www.bsesme.com/corpoaratefilings/ScripsList.aspx?expandable=0`
  (SSR HTML table, 582 scrips: code|ticker|name|status|group|fv|ISIN|
  industry — note the `corpoaratefilings` typo in the path is real).
  Dead: `bseindia.com/sensex/IndicesWatch_Weight.aspx?iname=SMEIPO`
  (redirects to the SPA homepage). Remaining gap: full NSE Emerge board
  list (EQUITY_SME.csv and /api/live-analysis-sme-scrips both 404) —
  NSE-SME coverage rides the EMERGE index list (529) until pinned.
- **D13 Post-D12 classifier revisit** (filed 2026-09-15): the D11
  classifier set (20 new sub_sectors + the 174-entry
  `COMPANY_SUB_SECTORS` map) is PROVISIONAL until D12's NIC-2008 seed
  lands. Revisit: (a) the generated naming-rule review list
  (`build_sector_hierarchy --check` prints ~30 parent-token echoes,
  incl. Telecom_Services/Telecom_Equipment and legacy names);
  (b) re-derive classifications over NIC codes where they disagree with
  the curated map; (c) the empty Education_Training Level-3 skeleton —
  operator ruled it too thin for sub-classifiers; keep or prune;
  (d) the NSDL / National Securities Depository duplicate entity (both
  authored into Depositories pending an entity merge); (e) the ~353
  no-label companies and ~44 thin-label leavers.EXECUTED 2026-09-15 (operator decisions inline): (1) echoes CLOSED —
  all 31 benign hierarchy echoes, no renames; (2) E&T skeleton pruned
  (Formal/Vocational/Corporate — 0 members each) and replaced by
  Training_Services (3 real members); (3,4) merges REVERSED per operator —
  NSDL and ONGC retained, the long forms folded in and their notes
  deleted (National Securities Depository: 22 graph_edges re-pointed,
  1 self-loop dropped, ticker NSDL.BO moved; Oil and Natural Gas: 44
  re-pointed, ticker ONGC.NS moved; COMPANY_SUB_SECTORS map re-keyed,
  Depositories dup de-duped); (5) 26 alias extensions onto existing
  nodes (Telecom Services/Equipment, Brokers, Real_Estate_Development,
  Specialty_Chemicals, E&P, Construction_Equipment); (6) 20 NEW
  sub_sector nodes created (operator: "members can come as the project
  evolves") across 13 sectors. Taxonomy: 123 → 140 sub_sector entities,
  165 → 182 belongs_to; hyperedges 86 → 106, memberships 771 → 819
  (authored coverage 819/1,176; 0 empty edges). 227 tests green.
  NIC re-derivation (b) stays CLOSED per the D12 measured negative.
  Remaining ~357 no-industry notes ride D18.
- **D12 NIC-2008 ingestion** (filed 2026-09-15, from the ontology
  assessment §6.1 — decided but never ingested): pull the MCA company
  master for the 1,179 companies and populate `entities.cin` +
  `cin_nic5` (schema facets already exist, all rows empty today), build
  the NIC-2008 → sub_sector seed table over the canonical D11 taxonomy
  (1304 5-digit sub-classes; ISIC Rev.4-aligned at 4-digit; NACE as the
  secondary crosswalk, GICS mapping-target-only per the #234 posture),
  and add the SKOS exactMatch crosswalk rows to
  `doc/reference/ontology_glossary.md`. Buys: mechanical classification
  of the ~353 no-label companies (every listed Indian company has a CIN
  encoding its NIC code), an authoritative Indian reference for
  sub_sector naming, and a NIC↔sector sanity cross-check of the
  hand-curated aliases. Sequencing note: ideally BEFORE D3's one-time
  re-baseline so it covers NIC-seeded classification too — operator
  call whether D12 preempts D3 in the working order.
  MEASURED 2026-09-15 (harvest sample, n=205): 88% of CINs are pre-2008
  incorporations → CIN NIC field carries legacy NIC codes, and the OGD
  PBA column is NA throughout — mechanical NIC→sub_sector classification
  is sound only for nic2008-era CINs (~12%); the seed table is scoped
  accordingly, pre-2008 rows keep cin_nic5 + legacy flag, and the
  no-label pool shifts to the sidecar manual curation lane
  (memory/data/mca_cin_manual.csv). Sidecar: memory/data/mca_cin.parquet via
  helpers/maintenance/mca_cin_sync.py (build/apply/resolve).
  EXECUTION RESULT (2026-09-15, CLOSED with measured negative on the NIC
  lane): CIN identifier layer LANDED — 730/1,178 companies carry
  `entities.cin` + all five facets (validated via backfill_identifiers;
  sidecar `memory/data/mca_cin.parquet` 730 rows + format gate; harvester
  731/1,093 exact+fuzzy via OGD, Kite/OGD endpoints below). NIC
  classification REFUTED for this corpus: 83% of CINs pre-2008 (legacy
  NIC codes), 2008-13 vintages still carry NIC-2004 codes (adoption
  effective 2014), post-2014 codes dominated by generic buckets — the
  honest seed table maps only 9/730 companies and HALF the mappings are
  wrong (Bandhan Bank → Brokers). Seed table NOT added to code; the 5
  defensible NIC↔sub_sector pairs are recorded as SKOS crosswalk rows
  in `doc/reference/ontology_glossary.md` for reference only. No-label
  coverage rides the curated map + `memory/data/mca_cin_manual.csv`
  curation + D13. OGD ENDPOINTS (verified 2026-09-15): catalog
  `api.data.gov.in/catalog/ec58dab7-d891-4abb-936e-d5d274a6ce9b?api-key=…`
  (SINGULAR `/catalog`; `/catalogs/<uuid>` 404s); resource exact-name
  lookup `api.data.gov.in/resource/<same-uuid>?api-key=…&format=json
  &limit=10&filters[company_name]=<NAME>` — `q=` unsupported; offset
  capped ~10k (ES result window) ⇒ NO bulk pagination; personal key in
  `memory/.env` `GOV_API_KEY` (strip the surrounding quotes; demo key is
  429-saturated); MCA portal itself is Akamai-walled (403, no bulk zip).

- **D16 Global exchange lanes** (filed 2026-09-15; markets per operator:
  Japan Nikkei, Taiwan TWSE, China SSE (+SZSE), Toronto TSX, KOSPI, Hang
  Seng, FTSE, DAX, CAC; US already verified end-to-end): fold non-India
  listings into `memory/data/sources.duckdb::exchange_listings`
  (exchange column carries LSE/XETRA/TSE/TWSE/SSE/TSX/KRX/HKEX etc.).
  Status: US fully verified (nasdaqtrader ×2 + SEC EDGAR 10.4k + Wikipedia
  S&P 500 with GICS — 503 parsed); SSE JSON API verified; Wikipedia
  constituent tables need per-page parsers (generic parser caught stats
  rows — table shapes differ per index); JPX Toyo-list xlsx, TWSE ISIN,
  TSX directory, KRX POST portal, HKEX CSVs to pin; SZSE unprobed.
  Endpoint register: `doc/design/data_sources.md` (Global exchanges
  section). Buys: tickers (.L/.DE/.KS/.HK/.TW/.T Yahoo suffixes) for the
  ~243 no-ticker foreign entities → the yfinance label lane covers them,
  + optional index-universe seeding.
  EXECUTION 2026-09-15 (near-complete): 15,666 non-India rows folded —
  TSE 4,441 (JPX data_j.xlsx, 33-sector labels), SZSE 2,901 (official
  JSON API; main/sme/chinext), HKEX 2,810 Equity (official xlsx), TSX
  2,248 equities (operator-delivered official TMX xlsx: 747 tsx + 1,501
  tsxv, TMX Sub-Sector labels, suffixes .TO/.V; 1,465 ETPs/funds
  filtered), SSE 1,844 A-shares (query.sse.com.cn JSONP; main/star,
  .SS, CSRC industry labels), TWSE 982 Stocks/REIT/TDR (ISIN page), KRX
  200 (KOSPI 200 components from en.wikipedia.org/wiki/KOSPI_200 —
  operator-curated JSON handoff, tickers .KS/.KQ; first Wikipedia parse
  had symbol/name swapped and was purged) + LSE 100 (FTSE) + XETRA 40
  (DAX) + EURONEXT 40 (CAC) + TSX 60 (S&P/TSX 60, .TO) via Wikipedia
  parsers. Nikkei/HSI parsed but skipped as redundant. Open: KRX full
  universe (openapi.krx.co.kr needs an API key — operator item).
  Snapshot artifacts refreshed.
  EXECUTED 2026-09-15 (operator go, full scope): 5,019 stub companies
  inserted (INSERT OR IGNORE; entities.company 1,178 → 6,197; 5,969
  tickered). Main batch 4,693 — BSE-main 3,333, NSE-main
  156, dual 209, NSE Emerge 494, BSE SME 501; plus a first 326 that
  passed the CHECK untouched (DVR/REIT variants etc.); 1,208 carry
  exchange industry labels. Stub
  semantics: MINIMAL (name + ticker + normalized_name) —
  sector_classification stays NULL (authored-sector vocabulary lives
  there; industry labels query via sources.duckdb::exchange_listings
  join on symbol). Names cleaned to satisfy the entities CHECK (no
  Ltd/Limited suffix, no Pvt/Private token) — 4,729 first-pass inserts
  were silently IGNOREd until the constraint was found. Zero hyper-
  metric shift at insert time (industry lane reads note YAML, not
  entities). Stub-exposed surfaces green: integrity/api/notes/resolver
  183 tests passed. Estimate was 6,586; actual 5,019 (delta: ticker
  match caught 1,159 not 1,003, name-clean merged suffix variants).
  Follow-up D17 files the note-linking pass.
- **D18 yfinance industry enrichment for unlabelled companies**
  (filed 2026-09-15, operator: separate task before arc-end gates): ~357
  authored companies still carry no sub_sector because their notes lack
  an `industry:` field (324 never enriched + ~30 note-file reconciliation
  misses + 5 odd labels). Pass: `enrich_from_yfinance.fetch_company` per
  tickered name (our 950 tickers cover most Indian names; foreign giants
  via their Yahoo tickers), write `industry:` into note YAML, then the
  S11 alias map (95 labels after D13) classifies them automatically on
  the next derive — no code change beyond the enrichment run itself.
  Unmapped-label remainder lands in findata/Misc/subsector_worklist.json
  for the next triage round. Success metric: no-industry count < ~80.
  EXECUTED 2026-09-15: targeted driver over authored tickered notes —
  pool was 28, not ~357 (the triage number conflated "no industry field"
  with "unmapped label" and untickered names): 27/28 fetched, 23 notes
  gained `industry:`, 1 failure (Gujarat Energy — dead ticker), 4
  fetched-but-industryless. Every tickered authored note now carries the
  field (930/935). Re-derive: sub_sector memberships 819 → 836. The
  residual 340-without-sub_sector decomposes as: 110 tickered with
  UNMAPPED labels (top: Banks - Regional 38, Packaging & Containers 11,
  Capital Markets 10 — next S11 triage round, taxonomy decisions) +
  230 UNTICKERED authored (Wabco, Reliance Retail, diagnostics labs,
  foreign privates — need the D12 pass2b longName bridge or manual).
  Success metric met on the fetch side.

- **D19 exchange intake lane — refresh + IPO/listing detection + stub
  seed hooks** (filed 2026-09-15, operator: "how do we keep the exchange
  DB updated — IPOs, listings etc", build scheduled next session):
  D16 left `exchange_listings` populated (38,426 rows, 11 exchanges) but
  every fold ran from ephemeral /tmp scripts — nothing in the repo
  refetches, detects new listings, or seeds stubs. Build
  `helpers/maintenance/exchange_sync.py` (same driver shape as
  `mca_cin_resolve.py`, tests included):

  1. **Adapters per lane** — fetch → parse → idempotent INSERT deduped
     on (exchange, symbol, segment); failures land in a worklist, never
     block other lanes. Lane stability (register in
     `doc/design/data_sources.md`): HKEX/SZSE/SSE/TWSE/Wikipedia
     stable+scriptable; JPX monthly xlsx with rotating attachment id
     (follow from misc/01.html); India BSE/NSE needs repinning
     (BhavCopy monthly is the clean re-source); TSX+KRX
     operator-gated — provide `ingest-file <path>` for hand-delivered
     xlsx/json (same as the 2026-09-15 TSX/KRX handoffs).
  2. **Diff lane (the IPO detector)** — symbols not present in the
     previous snapshot export → `ipo_worklist` (new per exchange);
     also flags renames/symbol-reuse for one eyeball pass before
     seeding. This worklist is the review artifact, not the store.
  3. **Auto-seed stubs** from confirmed worklist rows — promote the
     /tmp D14 seeder (name+ticker+normalized_name, entities CHECK name
     cleaning — INSERT OR IGNORE swallowed 4,729 CHECK failures before
     the cleaning fix) into the helper.
  4. **Hooks downstream (both already built, need triggers)**:
     `mca_cin_resolve.py ogd` for Indian IPOs (fresh CINs resolve well
     on the OGD mirror), targeted `enrich_from_yfinance` pass for
     industry labels on new stubs only (never full runs — 5k+ stubs).
  5. **Wiring**: `make refresh-exchanges`; freshness surfaced as
     max(fetched_at) per exchange (add a `fetched_at` column — current
     rows backfill to 2026-09-15); snapshot rider already picks the
     store up. Cadence per operator: India monthly (SME/Emerge churn
     is where vault-relevant names appear), global quarterly, TSX/KRX
     opportunistically.
  Success metric: one command, idempotent, reports "N new per exchange
  — seeded / CIN'd / labeled"; zero manual typing; tests for adapters
  run offline against fixture pages. Promote the still-useful /tmp
  recipes (d16_fold parsers, d14_seed2) into the helper; delete the
  rest.
  **EXECUTION 2026-09-16 (core lanes done):**
  `helpers/maintenance/exchange_sync.py` + 10 offline tests +
  `make refresh-exchanges` (APPLY=1 to write). Lanes live and at store
  parity: HKEX (GEM vs main via sub-category col; the dimension attr
  and the type col were transposed in the first cut — 306 mis-segmented
  rows purged), SZSE (field mapping agdm/agjc-HTML/bk), TWSE, wiki
  (FTSE/DAX40/CAC/TSX60; gone-detection scoped to fetched segments),
  NSE EQUITY_L (series EQ/BE/BZ vs SM/YM; header keys carry stray
  spaces), BSE ListofScripData (Active-only — the all-status call drags
  3,338 delisted scrips; SME not distinguishable in the API, rides the
  NSE lane + existing store rows), US via the nasdaqtrader FTP
  symboldirectory (nasdaqlisted + otherlisted; HTTP symdir paths are
  walled now) — 7,506 rows first-folded store-only, NO US stubs by
  design (India-core vault; foreign stubs stay opt-in). First run
  detected: 3 NSE + 3 BSE new listings (stubs seeded:
  GLASSWALL/KANOHAR/PRASOLCHEM .NS) + 6 TSX60 churn stubs (Brookfield
  Infra, Canadian Tire, CCL, CGI, Rogers, Teck). Lane isolation: one
  endpoint failure logs FETCH-FAILED and moves on (SSE runs SLOW MODE by
  default — 2s pacing + 3-attempt backoff, operator ruling — and
  reached full parity 2026-09-16 evening after fixing the key
  convention (.SS suffix + FULL_NAME_IN_ENGLISH; the first cut keyed
  bare codes and seeded 1,803 junk stubs — purged, seeder fixed to
  never emit NULL tickers, worklist writes MERGE instead of clobber).
  ALL LANES AT PARITY. US fold store-only (India-core vault). DuckDB
  connection held ONLY during fold (operator lockout directive).
  fetched_at column added (backfill 2026-09-15); stub seeding sets
  ticker (exchange symbol fallback; .NS/.BO for India lanes).

- **D17 Stub-aware note linking + unresolved-notes retro-pass**
  (filed 2026-09-15, operator; depends on D14 stubs): the entity universe
  grew 1,178 → 6,197 companies, most without notes. Two pieces:
  (a) markdown_parse / note-capture must treat stubs as existing
  entities — resolve company mentions against the stub universe (reuse
  `search_ticker`/`vss_match`/`resolve_entity`, `word_overlap_match`);
  a note landing on a stub UPGRADES it (file_path + authored fields),
  never births a second entity. (b) retro-pass: re-run entity
  resolution over note company mentions that previously went
  unresolved (pre-stub universe 1,178 names / 950 tickers; now
  6,197 / 5,969) and link the misses. Success metric:
  unresolved-mention count drops materially with zero new duplicate
  entities (dedup audit before/after).
  EXECUTED 2026-09-15: (a) classify() already saw stubs (entity_type
  filter covers them); the GAP was worklist semantics — emit_worklist now
  annotates is_stub/known_ticker (+ likely_is_stub on uncertain entries)
  and its instructions carry the upgrade rule; markdown_parse.md §
  Enhancing Existing Entities documents the procedure. (b) retro-pass
  over 36 past edition worklists: 55 uncertain mentions re-resolved —
  4 exact (3 onto stubs), 42 fuzzy-safe, 4 flagged review (matcher
  proposed SBI→SBI Life, Chambal→TIL, Siemens Energy AG→Siemens, Rupa
  escaping — family/abbreviation collisions, left for manual call);
  28/28 previously-new entities confirmed created; 1 counterparty ghost
  unchanged; 0 new duplicate entities created (proposal-only worklist:
  findata/Misc/retro_resolution_worklist.json). BONUS finding: Tata
  Motors exists BOTH as exchange stub and authored "Tata Motors
  Passenger Vehicles" — near-dupe flagged for an operator rename/merge
  decision (left as-is).
  CURATED 2026-09-15 (operator go): 46 mentions accepted into
  findata/Misc/relation_aliases.json (68 total; every target verified
  against entities); tier_review corrected — SBI→State Bank of India,
  Siemens Energy AG→Siemens Energy, Chambal→"Chambal Fertilisers and
  Chemicals" (British spelling), Rupa→authored "Rupa"; 2 rejected
  (Tata Motors mention = ambiguous post-demerger; Amara Raja Energy =
  family ambiguity). Data fixes: Gujarat Energy ticker GUJGASLTD→
  GUJENERGY.NS (was pointing at Gujarat Gas — root cause of the D18
  fetch failure; industry now fetched: Utilities - Regulated Gas).
  Operator flags filed in retro_resolution_worklist.json: Rupa &
  Company stub vs authored Rupa (merge/delete); Amara Raja Batteries
  carries the renamed parent's ticker ARE&M.NS (ticker/name mismatch).
  Tata Motors near-dupe RESOLVED (demerger): stub renamed to Tata
  Motors Commercial Vehicles (TMCV.NS), TMPV authored.
- **D15 snapshot: fold sources.duckdb into the parquet snapshot**
  (filed 2026-09-15, small): extend snapshot_db.py export/restore/check to
  cover a SECOND duckdb — `memory/data/sources.duckdb` (tables
  `exchange_listings` 24,305, `mca_cin` 730; views vw_equity/vw_sme/
  vw_instruments) — into `snapshots/parquet/sources/` with its own schema
  SQL, mirroring the graph.duckdb manifest pattern (a SOURCES_TABLES
  constant, NOT MATERIALISED_TABLES — that manifest means "tables the
  graph materialisation owns" and must stay clean). Interim protection:
  the ETL is fully re-runnable (endpoints documented in
  `doc/design/data_sources.md`), and db_maint's `--duckdb/--duckdb-backup`
  args can cover the file config-only. Rationale for a separate duckdb:
  lifecycle isolation — graph.duckdb is a derived cache; sources.duckdb
  is primary external data (harvest = hours of API grind).
  EXECUTED 2026-09-15: export/verify/restore legs all in snapshot_db.py
  (parametrised manifest/schema/subdir on the duckdb paths; sources
  worker = 3rd thread; --sources-db/--no-sources CLI; _cmd_restore/_cmd_check
  branches). Live run: exchange_listings 24,305 + mca_cin 730 exported,
  verified, and restored into a throwaway target with all three views
  replayed (vw_sme 1,059 / vw_equity 16,668). Regression test
  test_sources_duckdb_export_restore_roundtrip; suite 14/14. Binary zstd
  legs refreshed in the same pass (they were pre-D14-stale: entities
  1,685 vs 6,726 live).

Standing non-goals (never tasks): taxonomy auto-write from hy-MMSBM
outputs (analytics rows are proposals for a human, #233 doctrine);
graph.ts hint rephrasing (cosmetic).

## 6. Execution Results

- **W1 EXECUTED (2026-09-15)** — never-block hardening +
  `hyper_arrow.store_rowcount()` read-only probe (absent store / no rows
  for sources → printed skip, rc 0; guard-exhausted store → same, at CLI
  level — `fit_communities`/`compute_centralities` keep raising, their
  contracts stay pinned). NEW `tests/test_hyper_wiring.py` (7): the
  previously-unpinned `--apply` write path (rows appear; idempotent
  re-run with a sentinel `computed_at` untouched — the zero-churn pin),
  centralities core lanes + uniform-scope eigen trio, dry-run
  writes-nothing, and the three skip paths. test_hyper_incidence 52/52
  unchanged.
- **W2 EXECUTED (2026-09-15)** — `make recompute-hyper` (both lanes
  `--apply`) + `.PHONY` + help entry; MakefileHelpCompleteness 2/2.
- **W3 EXECUTED (2026-09-15)** — TIER2 steps `hyper-communities` /
  `hyper-centralities` between derive-hyperedges and the tail snapshot;
  test_maint pins 9→11 TIER2 / 20 full-composition (both sites); chain
  shims ×2 (redirect `hyper_arrow.DEFAULT_DB_PATH` +
  `algorithms.connect` for the lazy `write_analytics` import) —
  test_maint + chain 33/33.
- **W4 EXECUTED (2026-09-15)** — doc truth: db_schema.md graph_analytics
  ownership names all three writers converging on the write_analytics
  upsert (also fixed the stale 1,648→1,684 dyadic row count); §5.6 split
  into the recompute-graph (14 dyadic) and recompute-hyper (4 hyper @
  company rows, eigen trio uniform-only, seeded zero-churn) clauses;
  maintenance.md TIER2 flow sentence. The two false claims from §1.3
  are gone.
- **W5 EXECUTED (2026-09-15)** — live apply (pre-backed-up): first
  `make recompute-hyper` converged the store growth since the manual
  era — 1,172-member fits upserted, table now 1,179 rows/metric (the +7
  difference is stale-carried members the upsert never deletes — the
  same linger semantics recompute-graph has, not a defect); a second
  run over the unchanged store: counts and `computed_at` ranges
  byte-identical (zero-churn proven live). Stamps: 2026-09-13/14
  originals preserved — the upsert only touches `value`.
- **Backlog consolidation** — §5 rewritten as the D1–D10 task list:
  this arc's non-goals folded as tasks (D1 UI/API rosters, D2 HIF
  wiring, D3 source-scope) + the still-open #233 ledger items pulled
  with provenance (D4 `h_*` cache materialisation, D5 JV venture
  capture, D6 promoter prose, D7 subsector YAML discipline, D8
  concall-title normalisation, D9 archive .txt stragglers, D10
  prediction/motifs/dynamics).
