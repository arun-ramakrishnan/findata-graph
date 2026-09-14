---
title: "Hypergraph incidence layer — hyper_edges/hyper_incidences + hy-MMSBM overlapping communities"
status: proposed
filed: "2026-09-13"
executed: null
completed_md: null
area: "helpers/graph (new derive_hyperedges.py, hyper_communities.py), helpers/maintenance/migrate_to_graph_edges.py, doc/design/db_schema.md"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Hypergraph incidence layer — hyper_edges/hyper_incidences + hy-MMSBM overlapping communities

**Date:** 2026-09-13 · **Status:** PROPOSED ·
**Area:** graph layer (SQLite schema + derive family + HGX compute lane); Onager/igraph untouched

## 1. Motivation

`doc/local/evaluations/hyper_graph_assessment.md` (2026-09-13, verdict revision
MEDIUM) found four producers that destroy set-information at derive time and a
taxonomy that can only represent set-valued membership lossily. The sharpest
instances, re-measured live on this box (2026-09-13):

| Set-valued fact | Dyadic storage today | Loss |
|---|---|---|
| edition → set of co-mentioned companies | 1,329 `co_mentioned_in` pairs over only 20 editions | a 7-company block becomes 21 indistinguishable pairs; "which entity-sets recur" is unanswerable |
| sector / country / theme membership | `part_of` 1,165 + `listed_in` 930 + `exposed_to` 359 star dyads | a category IS one grouping decision; stored as a materialised star, single-assignment algorithms can't see multi-theme membership |
| promoter group (`same_group`) | 3 dyads carrying `properties.group` as prose | the group name has no first-class node (§7.2 of the assessment) |
| JV with k partners | 69 `jv_with` dyads, only 2 rows carry a `venture` name | k-partner happening fragmented; regroup key absent |

The taxonomy-limitation driver (§7.4): the corpus's natural groupings have no
first-class structure to run higher-order community detection over, so the
taxonomy stays frozen at 78 hand-curated sub_sectors while the incidence data
that could propose candidates sits flattened in dyads.

This arc lands the assessment's Phase 0 (incidence storage + backfill) plus
ONE consumer: **hy-MMSBM overlapping communities over the sector+theme
incidence** (HypergraphX, BSD-3) — the consumer that attacks the taxonomy
limitation head-on: soft multi-block memberships for every company, computed
from the incidence rather than hand-authored.

## 2. Evidence (measured 2026-09-13, this box)

Install + fitness probe (full log in Appendix): `/tmp` venv on CPython 3.14,
`uv pip install hypergraphx` → 1.8.0 in 3.3 s, 8 packages (delta vs `.venv`:
scipy 1.18.1 + tqdm 4.70.1 only — networkx 3.6.1/numpy/pandas already present).

| Configuration | Result | Verdict |
|---|---|---|
| HGX 1.8.0 install on py3.14 | clean, no sdist builds | adopt (main venv — BSD-3, unlike GPL igraph's pilot split) |
| HyMMSBM.fit, sector+theme incidence (1,165 nodes, 54 hyperedges, sizes 2/21/95), K=8, n_iter=500 | 0.5 s, blocks 300/248/138/119/116/99/85/60 | viable consumer |
| same, K=12 | 0.5 s, blocks 327…4 | K is cheap to sweep |
| sector+country+theme (adds 21 country hyperedges incl. one of size 850) | EM collapses: ALL 1,165 nodes in one block | country OUT of consumer default scope |
| determinism: two seeded fits (seed=42) | max \|Δu\| = 0.0 | reproducible analytics rows |
| qualitative: block purity vs sector (K=8) | Pharma block 0.82 purity (API_Manufacturing theme); Metals+Renewables+Battery block; Make-in-India industrials block; FMCG+Tech block | blocks cut across the taxonomy — the §7.4 signal |

Measured, do not re-audit: HGX 3.14 install cleanliness; fit timing at live
scale; the two-shaped degeneracy (singletons + giants — §5); seed
determinism.

### 2.1 Scale posture — same truth, two stacks (measured 2026-09-13)

Synthetic hypergraph with live-like heavy-tailed sizes (lognormal, median
~28, capped 500), scaled to 100,030 incidences / 3,101 hyperedges / 10,000
nodes, seed=7. The dyadic lane is the clique expansion of the SAME truth —
the comparison the "100K+ edges" question asks for:

| Lane | Data shape at this scale | Time (this box) |
|---|---|---|
| HGX HyMMSBM (star) | 100,030 incidences | build 0.11 s + **1.0–1.2 s per 100 EM iters**, ~9 MB peak |
| HGX HyMMSBM, deep regime (147 hyperedges ≤ 1,200 members) | 100,104 incidences | 0.94 s / 100 iters — big hyperedges do not change the cost class |
| DuckDB clique expansion (SQL self-join) | 100K incidences → **3,422,116 dyadic edges (34× blow-up)** | 0.34 s |
| Onager louvain over the clique | 2.85 M dyads (cap 150) | **142.7 s** (completed, 22 communities) |
| Onager louvain over the clique | 3.42 M dyads (cap 500) | **did not complete** — > 440 s wall then process death (suspected OOM, 14 GB box, unconfirmed) |
| Onager pagerank over the clique | 3.42 M dyads | **196.6 s** |
| Onager @ the small end | 138 K dyads | pagerank 0.31 s, louvain 0.32 s — fast until the blow-up lands |

Reading: the star lane's compute is scipy-sparse linear algebra (per-iter
cost ~ Σ|e|² pair terms inside numpy, not Python loops) and stays ~1 s at
100K incidences in BOTH shape regimes. The dyadic stack's expansion is cheap
in SQL, but its analytics engines pay the 34× edge blow-up: at 2.85–3.42 M
clique edges Onager louvain takes 143 s+ and dies one step up, pagerank
197 s, while the hyper-native model fit completes in ~1 s. Caveats: (a) the
clique blow-up is specifically the SET-derived-edge tax — a graph that is
naturally dyadic at 100K edges is a different, much lighter Onager workload;
(b) our live graph (19,261 dyads / 2,687 incidences) is two orders of
magnitude below this benchmark on both axes — Onager is instant there, so
this row is a growth-posture measurement, not a today-problem.

**Edge blowout by shape** (distinct clique pairs materialised vs star
incidences — the quantity the two stores hold for the SAME truth; all rows
this box, seeded generator, DISTINCT materialised in SQL):

| Shape | hyperedges | incidences | clique dyads | blow-up | expand SQL | HGX fit /100 iters |
|---|---|---|---|---|---|---|
| LIVE set-shaped (edition+group, real DB) | 21 | 233 | 1,332 | **5.7×** | — | — |
| synthetic, cap 150 | 3,239 | 100,003 | 2,847,100 | **28.5×** | 0.28 s | 1.03 s |
| synthetic, cap 500 | 3,101 | 100,030 | 3,422,116 | **34.2×** | 0.34 s | 1.01 s |
| synthetic, cap 1200 | 3,070 | 100,016 | 4,343,878 | **43.4×** | 0.39 s | 1.03 s |

The blow-up ratio is ~E[|e|]/2 — it grows with hyperedge size cap
(28.5× → 43.4×) exactly as Σ|e|²/Σ|e| predicts, while the star lane's fit
time is flat (~1.0 s) across all three shapes. Today's live set-shaped data
is only 5.7× (small edition sets, avg ~12 members) — the tax grows as
capture deepens (the §6 quotes regroup alone takes the edition lane to
~93 sets), which is the storage argument for star-not-clique (§9.1 of the
assessment memo).

Reproduce: ``python3 helpers/bench/hyper_scale_bench.py --incidences 100000``
(star + expansion only, ~10 s); add ``--dyadic-analytics`` for the pagerank/
louvain legs (minutes — deliberately NOT wired into ``make perf``). Recorded
rows: ``tests/data/hyper_scale_baseline.json`` (house baseline pattern,
alongside ``onager_toy_baseline.json``).

## 3. Design

Star expansion is storage truth; clique projections stay what they are
(§9.1 of the assessment — no change to `graph_edges`). Dyads are the n=2
special case; nothing existing is forked or rewritten.

- **S18 — data-format standard + HIF interop (operator 2026-09-13:
  no demand gates — (b)(c)(d)(e) queued for build; restructured on the
  same directive):** repo-wide two-tier data standard,
  first applied to the hyper lane:
  **at rest = parquet(zstd); in flight = Arrow.**
  - **(a) At rest — parquet(zstd), already true on this lane:** the
    45-table snapshot writer emits `pq.write_table(...,
    compression="zstd")` (verified against the on-disk files: codec
    ZSTD). Any NEW on-disk artifact in this lane uses the same codec.
    `db-backup/*.zst` (whole-SQLite-file zstd) stays a distinct class —
    DB-file backup, not table data.
  - **(b) In flight — Arrow as the canonical interface:**
    `load_incidence_arrow(sources)` returns the incidence as Arrow
    tables (nodes / edges / incidences), from EITHER source behind one
    signature: (i) live read — DuckDB's sqlite scanner
    (`ATTACH 'memory/research.db'`) with `.fetch_arrow_table()`, or
    (ii) snapshot read — pyarrow reading the zstd parquet directly
    (parquet → Arrow, no pandas round-trip). The existing SQL-to-dicts
    `load_incidence` becomes a thin wrapper over this loader.
  - **(c) Consumers, one Arrow table feeding both engines:** HGX has no
    Arrow API, so it takes `to_pylist()` at its construction boundary;
    DuckDB consumes the SAME Arrow table natively
    (`duckdb.from_arrow()` / `register()`) — which unlocks
    SQL-over-incidence IN FLIGHT (the memo's Phase-1 cache) without
    ever materializing `h_*` tables at rest. That zero-copy DuckDB↔Arrow
    bridge is the reason Arrow is the OTW standard.
  - **(d) Foreign boundary — HIF CANONICAL FORM IS PARQUET (operator
    correction 2026-09-13, supersedes the earlier "JSON exempt" framing;
    memo §9.4 already pinned this):** the three HIF record arrays ARE
    three Arrow tables (`nodes`/`edges`/`incidences`, column mapping in
    memo §9.4 — repo-specific fields packed into `attrs`, network-type
    in parquet footer metadata), so the HIF export lane writes **three
    zstd parquet tables with HIF column names**. Nothing about HIF
    forces JSON: `write_hif` JSON (verified in the engine) is a
    TRANSIENT, consumer-demand-driven serialization produced FROM the
    canonical parquet tables — emitted on demand, never stored. The
    at-rest standard has no boundary exemption. Round-trip validator
    asserts node/edge/incidence counts, per-incidence weights, edge
    types, and `attrs` packing survive BOTH forms
    (parquet canonical <-> store; JSON skin <-> hif_schema.json).
    BUILT 2026-09-14 as ``helpers/graph/hyper_hif.py`` (live run:
    1,179 nodes / 206 edges / 3,327 incidences / 1,318 weighted,
    zstd-verified). Measured fidelity caveat: HGX's ``read_hif`` keys
    edges by frozen member tuples, so 7/206 edition edges with identical
    member sets COLLAPSE in the HGX object — the parquet canonical keeps
    edge ids first-class; the JSON skin is interop, not identity.
  - **(e) Parquet-source loader, spelled out (the sub-item asked
    about):** today the ONLY reader of the hyper store is live SQLite —
    `load_incidence()` runs SQL against `memory/research.db`, and the
    zstd parquet snapshots are written but never read back. (e) makes
    the snapshot a compute source: pyarrow opens
    `snapshots/parquet/sqlite/hyper_{edges,incidences}.parquet` →
    Arrow → the SAME consumer interface as (b). Effect: a fresh machine
    restored from git runs communities/centralities with zero SQLite
    involvement (no live DB, no WAL, no ATTACH). One interface, two
    sources: live-sqlite-arrow or parquet-arrow.
  - **(f) Fidelity caveats carried over:** HGX's HIF writer pins
    `"type": "undirected"` (our `direction` column does not round-trip
    through it; a directed writer would be hand-built when directed
    sources appear); the HIF-Arrow three-table variant stays
    hand-built-on-demand.
- **S19 — data-format enforcement guards (BUILT 2026-09-14; sweep
  counts in the appendix):** prose standards
  drift; checks do not. New validator
  `helpers/validators/data_format_checks.py` wired into
  `make static_checks`, with a SHRINKING BASELINE so existing code never
  fails retroactively — only new violations do:
  - **(a) At-rest guard — parquet(zstd) codec check:** every parquet
    write in `helpers/**` must specify zstd — scans
    `pq.write_table(` / `.to_parquet(` call sites for
    `compression="zstd"` (or `COMPRESSION zstd` in DuckDB `COPY ...
    TO '*.parquet'`); violations unless the writer is in the baseline
    manifest (`_ZSTD_BASELINE`, e.g. any legacy snapshot writer not yet
    converged — expected empty today).
  - **(b) In-flight guard — no custom data structures between
    components:** inter-component loader/producer functions in the
    designated data-lane modules must move Arrow (`-> pa.Table` in the
    signature, or a `pa.Table`-returning call contract at the consumer
    boundary). Non-Arrow producers are violations unless baselined in
    `_ARROW_BASELINE` with a reason and an exit slice (e.g. legacy
    `load_incidence` SQL-to-dicts until S18(b) lands; HGX `to_pylist()`
    calls are engine-boundary, exempt by design, not baselined).
    Engine-internal reads (sqlite3 rows feeding one function) are not
    in-flight and stay out of scope.
  - **(c) Wiring + tests:** check registered in
    `helpers/validators/static_checks.py`'s registry; unit tests pin
    (1) a zstd-less parquet writer fails, (2) a dict-returning
    designated loader fails, (3) baselined entries pass, (4) removing a
    baseline entry whose code is still custom fails loudly (baseline
    hygiene). Per house rule: wiring is code; `make qa` still runs only
    at arc end with the operator's go.
  - **(d) Straggler sweep + convergence ledger (runs LAST, once the
    guards are live):** run both guards across the FULL repo — not just
    the hyper lane — and capture every failure: every parquet writer
    missing zstd, every inter-component producer returning dicts/lists/
    tuples/DataFrames where Arrow is the contract. Each straggler is
    either (i) fixed immediately if trivial, or (ii) entered into
    `_ZSTD_BASELINE` / `_ARROW_BASELINE` with owner reason + exit slice.
    The baselines ARE the straggler ledger: monotonically shrinking,
    visible in `make static_checks` output, emptied slice by slice.
    Deliverables: initial sweep count in the proposal appendix (same
    row style as the S14 live rows), and the baselines committed with
    zero silent exemptions. Every later arc that touches a baselined
    writer/loader either fixes it or must justify keeping the entry.
- **S20 — real-world large-dataset pull + benchmark (EXECUTED 2026-09-14
  on trivago-clicks; appendix row has timings):** pull ONE large hypergraph from hypergraphx-data
  (external reference below; JSON + binary formats per the COMNET
  article) and benchmark the HGX lane at real scale against the
  synthetic-seed bench baseline: download → HGX read → construction
  time; hy-MMSBM sweep timing, s-centralities, ho_pagerank/
  stationary_pi; memory peak (tracemalloc). Selection: largest
  incidence count available, one domain-diverse second choice noted.
  Dataset license + citation recorded in the appendix row; data lands
  in a gitignored data directory, never the vault or snapshots.
- - **S21 — quotes attribution recovery to >95% (operator goal
  2026-09-13; measured baseline 7,561/8,272 = 91.4%):** the entire gap
  is 711 mis-attributed rows — 522 with entity='Quotes' (parse
  assigned the newsletter section header as the entity), 185
  sector-attributed, 4 edition-attributed. Recovery, two stages:
  - **(a) Deterministic (code-only), measured 2026-09-13 pass 2.**
    Three stages: (a1) speaker roster from the 7,561 resolved quotes
    (1,287 speakers, 1,268 unambiguous) — 188 rows; (a2) single-company
    token match in title/text — ~63 rows; (a3) **heading walk** —
    `quotes.source_ref` → edition note → nearest COMPANY section
    heading (speaker sub-headings skipped, `[Company | Cap | Sector]`
    link-line fallback, apostrophe/ampersand normalization) — 168 rows,
    incl. Hitachi Energy, Page Industries, Greenpanel, Paras Defence,
    Hester→Venky's correction, CAMS, Divi's, Kotak, ICICI-AMC/Sanghavi.
  - **(b) Speaker supplement table — EVALUATED pass 2 (draft:
    `findata/Misc/speaker_supplement_draft.json`, 92 speakers).
    Classification of the 460-row residue: 168 walk + 93
    speaker-table → existing entities; 67 rows need entity creation
    (16 names: PepsiCo, Target, WBD, Liberty Media, F1, Parle, TIL,
    Godrej Agrovet, Honda, Synaptics, Dave & Buster's, Graviss, ACE,
    Sportking, CRIF High Mark, CCL Products — operator APPROVED);
    46 rows NON_COMPANY by operator ruling (journalists/analysts:
    Tamal Bandyopadhyay, Varun Goel, Chetan Seth, Brad Setser;
    regulators/academics; Subtext epigraphs); 85 rows honestly
    unknown (86 no-speaker/sector-blocked minus resolved; Ajesh
    Pillai, Peruru, Rajmohan class). **Projection: 97.6% with
    existing entities; 98.4% with creation; residue 1.6%.** The
    `Quotes` super_sector (findata/Super_Sectors/Quotes.md) is the
    DESIGNED catch-all where non-attributable quotes stay — not an
    artifact to retire (operator correction 2026-09-13).
  - **Mechanics:** idempotent backfill UPDATE with audit trail
    (properties.reattributed_from + original entity), dry-run/apply
    flags, re-run S8 derive, measured coverage row in the appendix.
    Forward guard: quotes-ingest flags entity values that are section
    headers or non-company kinds for review (company quotes feed the
    edition hyperedges; everything else stays under the Quotes
    catch-all by design).
- - **S1 — schema (Phase 0):** `HYPER_EDGES_DDL` + `HYPER_INCIDENCES_DDL`
  canonical constants in `migrate_to_graph_edges.py` (+ `migrate()` steps,
  idempotent IF NOT EXISTS), mirroring EVENTS_DDL's placement.

  ```sql
  hyper_edges(id PK, edge_type TEXT NOT NULL, label TEXT NOT NULL,
              weight REAL NOT NULL DEFAULT 1.0, valid_from DATE, valid_to DATE,
              source_ref TEXT NOT NULL, properties TEXT NOT NULL DEFAULT '{}'
                CHECK(json_valid), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(edge_type, label))
  hyper_incidences(edge_id → hyper_edges ON DELETE CASCADE,
                   entity_name → entities(name) ON DELETE CASCADE,
                   weight REAL,            -- HIF per-incidence weight (§9.4)
                   direction TEXT CHECK(direction IN ('head','tail')),
                   PRIMARY KEY(edge_id, entity_name))
  ```

  `label` is the hyperedge's own name (category / edition title / group);
  `UNIQUE(edge_type, label)` is the idempotency key backfills key into.
  Incidence `weight`+`direction` keep the pair HIF-lossless (the §9.4 catch —
  HIF carries weight at BOTH granularities). Both tables join the SQLite
  parquet snapshot list (`SQLITE_PARQUET_TABLES`) so they ship like every
  other data table. `db_schema.md` gains the two sections.

- **S2 — backfill:** `helpers/graph/derive_hyperedges.py`, derive-family
  conventions (dry-run default, `--apply`, `--verbose`; `derive_cli` scaffold).
  Five sources, all clean SQL regroupings of existing dyads — no note re-scan:

  | edge_type | source dyads | grouping key | live hyperedges |
  |---|---|---|---|
  | `sector` | `part_of` 1,165 | target | 42 |
  | `theme` | `exposed_to` 359 | target | 12 |
  | `country` | `listed_in` 930 | target | 21 |
  | `group` | `same_group` 3 | `properties.group` | 1 |
  | `edition` | `co_mentioned_in` 1,329 | `properties.edition` | 20 (230 distinct members) |

  `sub_sector` is deliberately absent: company→sub_sector membership is not
  recoverable from any current source (measured — appendix + §6 ledger);
  it needs capture work first, not a different regroup query.

  96 hyperedges / 2,687 incidences expected (edition source dedups 1,329 dyads into 230 distinct members). Provenance: `source_ref`
  = `derive:hyperedges:<dyad_type>`; `properties.upstream_types` records the
  dyad family each hyperedge regrouped. Wired: `make derive-hyperedges` +
  a maint-full TIER2 step (after `derive_events`, its upstream).

- **S3 — consumer:** `helpers/graph/hyper_communities.py`, igraph_bridge
  pattern (lazy HGX import so collection never requires it; read-only default;
  `--apply` persists via `algorithms.write_analytics`). Loads incidences for
  `--sources sector,theme` (default; the measured-degenerate `country` is
  opt-in), drops degenerate hyperedges at the seam (singletons always,
  giants > 25% of nodes unless `--allow-giant`), builds
  `Hypergraph(edge_list=...)`, fits `HyMMSBM(K, assortative=
  True, seed)` — row-normalised `u` memberships written per company as metric
  `hypermmsbm_community`: `{"block": 4, "memberships": {"4": 0.61, "0": 0.21,
  ...}, "k": 8, "sources": ["sector","theme"], "seed": 42}`. Dry-run prints
  block sizes + top sectors/themes per block (the probe output shape).
  Defaults: K=8, seed=42, n_iter=500. pyproject gains `hypergraphx`
  (unpinned, house convention).

- **S5 — `industry` hyperedge lane (operator-approved 2026-09-13):** the
  capture-ledger #1 follow-up. 816 non-null yfinance `industry:` frontmatter
  values over 117 distinct controlled-vocabulary labels are already in the
  company notes — regroup them as a 6th source (`edge_type='industry'`,
  ~117 hyperedges / 816 incidences), read from note YAML (frontmatter field,
  not prose; no sync_tags change). Tripled incidence density for the
  consumer; default sources widen to `sector,theme,industry`.
- **S6 — K selection sweep:** `--sweep 4,6,8,12,16` fits each K (same seed)
  and reports `log_likelihood` (API verified: `HyMMSBM.log_likelihood(hg)`)
  + effective block count; K=8 stays the default until the sweep says
  otherwise. Report mode only — `--apply` still writes a single `--k` fit.
- **S7 — retire the igraph pilot; HGX is the alternate engine
  (operator decision 2026-09-13):** delete `helpers/graph/igraph_bridge.py`
  + `tests/test_igraph_bridge.py` + its static_checks exemption; D16 row in
  `graph_design.txt`. Lane disposition — Leiden: superseded by hy-MMSBM
  overlapping communities (the "better communities than louvain" need,
  served natively, landed S3); weighted centralities: replaced by HGX
  higher-order lanes (`RW_stationary_state`, `s_betweenness`/`s_closeness`,
  s-walk semantics over the incidence — NOT dyadic-weighted; semantics
  change recorded, not hidden); `maxflow_mincut` + directed weighted
  shortest path: RETIRED unserved (no engine; directed paths stay SQL BFS,
  D9). Onager keeps every dyadic SQL lane unchanged. Reviving any dyadic
  weighted lane needs its own proposal.
- **S8 — quotes→edition hyperedges (promoted from the MAJOR ledger item,
  operator 2026-09-13):** the quotes table IS an edition→participant
  incidence — 93 editions × 693 entities, 1,257 distinct (edition, entity)
  pairs (1,180 company-only; avg 13.5, max 35; 87 editions ≥5 entities).
  Merge into the existing `edition` hyperedges as a UNION with the
  co_mentioned regroup (one edition = one participant set, however
  captured), with **per-incidence weight = quote count** — the first real
  use of the HIF-granularity-2 column. Concall-title `as_of_edition` values
  are included (the guard drops degenerate ones); members are company-type
  only (the 12 sector / 4 edition / 1 super_sector quote rows are
  mis-captures, not participants).
- **S9 — events.counterparty resolution → FK + event hyperedges:** resolve
  the free-text `counterparty` (436 events) against `entities` (exact →
  nocase → normalized), add an `events.counterparty_entity` FK column
  (schema change, canonical DDL), report the match rate + worklist of
  unresolved names, and derive `event` hyperedges (participants = entity +
  resolved counterpart(y|ies)) for events with ≥2 resolved participants.
- **S10 — JV venture-name capture:** extract_relations upgrade — capture the
  venture name into `jv_with.properties.venture` where prose names it (2/69
  rows today); regroup `jv_with` by venture into `jv` hyperedges where the
  key exists; worklist the rest (honest scope).
- **S11 — sub-sector capture via industry→sub_sector mapping:** S5 landed
  the 117-label Yahoo industry lane; curate a static industry→canonical
  sub_sector map (THEME_ALIASES precedent), derive `sub_sector` hyperedges
  from it, and emit an unmapped-industries worklist (country_worklist
  precedent). Unblocked by S5.
- **S12 — HGX alternate lanes (D16 dispositions made runnable):** wrappers
  for higher-order pagerank (`RW_stationary_state`) and s-centralities
  (`s_betweenness`/`s_closeness`, s-walk semantics, default s=1) over the
  incidence, persisting via write_analytics (`ho_pagerank`, `s_betweenness`,
  `s_closeness`). Demand-gated: build only when a consumer asks.
- **S13 — penalized K selection:** add a BIC-style column to the S6 sweep
  (−2·loglik + p·ln N, p = N(K−1) + K for the assortative model) so K
  selection has a criterion, not just the raw-loglik slope.
- **S14 — weighted higher-order pagerank (executes now):** HGX's
  `RW_stationary_state` is **binary** (verified 2026-09-13:
  `transition_matrix` builds `binary_incidence_matrix`; `weighted=True`
  on the constructor changes nothing for the walk). Build the weighted
  analogue in `hyper_centralities.py`: incidence matrix
  `W[n,e] = hyper_incidences.weight` (default 1),
  `M = W · diag(s_e − 1) · Wᵀ`, zero diagonal, row-normalise; stationary
  distribution by power iteration (uniform start, L1 tol 1e−12, cap 10⁴
  iters). Design properties: (a) **exact unweighted reduction** — unit
  weights reproduce the HGX construction term-for-term, pinned by an
  equivalence test against `RW_stationary_state`; (b) weights ride
  per-incidence in W (no per-edge collapse — a quote-heavy member pulls
  harder than a quote-light one inside the same edition, the HIF
  granularity-2 semantics); (c) auto-engages when the loaded sources
  carry incidence weights (today: `edition`, 1,180 weighted rows), and
  the payload records `weighted` + `weighted_incidences` for provenance.
- **S15 — CEC/ZEC/HEC eigenvector wrappers (BUILT 2026-09-14):** HGX
  `hypergraphx.measures.eigen_centralities` wired into
  `hyper_centralities.py` as `eigen_cec`/`eigen_zec`/`eigen_hec`
  metrics (METRICS tuple, write_analytics payload, CLI). Semantics
  guards built in: the trio is defined for UNIFORM hypergraphs only
  (Benson, doi:10.1137/18M1203031; ZEC/HEC assert it) — mixed-size
  lanes skip with a `meta.eigen_skipped` note instead of raising;
  HGX's implementations index tensors by node objects (strings
  crash), so the eigen block builds an int-mapped copy of the
  component-restricted family and maps keys back. Power-method
  determinism: seed=42, verified bitwise in tests.

- **S16 — s-centrality weights investigation (RESOLVED 2026-09-14:
  stay unweighted; design note below):** HGX's ``line_graph`` already
  exposes ``weighted=True`` + ``distance="intersection"|"jaccard"``, but
  ``s_betweenness``/``s_closeness`` call it unweighted — and wiring the
  weighted line-graph straight into networkx INVERTS semantics: nx
  centralities treat ``weight`` as path COST while overlap size is a
  SIMILARITY (bigger overlap = closer). Measured on the live
  sector+sub_sector lanes (97 hyperedges, s=1): top-8 edge ranking keeps
  only 4/8 entities under the naive weighted variant, and a
  cost=1/overlap transform ALSO keeps only 4/8 — the ranking is highly
  sensitive to the weight convention, and per-incidence weights (S8
  quote counts) have no canonical aggregation onto edge-pair overlap.
  Decision: the s-lanes stay unweighted (overlap-threshold semantics,
  ``s`` parameter only); weighted questions route to the S14 weighted
  walk (ho_pagerank), which is exact. A weighted s-variant gets built
  only when a consumer brings a semantics definition.

- **S17 — taxonomy additions from the S11 worklist (operator decision
  FINAL 2026-09-13; supersedes the earlier GICS-parent sketch —
  Financials/Industrials/Utilities are not sectors in this vocabulary,
  and NBFC/Housing_Finance/Capital_Markets/Semiconductors/Building_
  Materials are already SECTORS, so same-named sub_sector entities are
  impossible: entity names are globally unique across kinds):
  - **22 new sub_sector nodes** — 16 high-confidence + 6 by the approved
    bright-line rule (create when members ≥ 5 AND a clean name exists),
    all names collision-checked against every entity kind. Parents
    verified live; super-sector rollup follows automatically (S17 adds
    ONLY sub_sector→sector belongs_to edges; sectors already roll up):
    Auto_Ancillary→Automotive, Automobiles→Automotive,
    Electrical_Equipment→Engineering_Capital_Goods,
    Capital_Goods→Engineering_Capital_Goods,
    Construction_Equipment→Infrastructure, Infra_EPC→Infrastructure,
    Construction_Materials→Building_Materials (renamed from the natural
    label — collision), IT_Services→Technology, Software→Technology,
    Digital_Platforms→Technology, Asset_Management→Financial_Services,
    Power_Generation→Energy, Gas_Distribution→Energy, Biotech→Pharma,
    Ecommerce→Retail, Reinsurance→Insurance, Consumer_Durables→Consumer,
    Luxury_Goods→Retail, Apparel_Retail→Retail,
    Real_Estate_Development→Real_Estate, Restaurants→Consumer,
    Consumer_Electronics→Electronics. New-node members: 255 + 56.
  - **1 alias to an existing node:** Aerospace & Defense → Aerospace
    (12 members; the Yahoo label is a superset — defense primes land in
    Aerospace; per-company Aerospace/Military split deferred).
  - **9 deliberate skips — sector level already covers (each target
    sector hyperedge verified non-empty live: Banking 54, NBFC 26,
    Housing_Finance 10, Capital_Markets 29, Semiconductors 7,
    Chemicals 68, Telecommunications 11, Renewables 23, Packaging):**
    Banks - Regional/Diversified, Credit Services, Mortgage Finance,
    Capital Markets, Semiconductors, Chemicals, Telecom Services,
    Utilities - Renewable, Packaging & Containers. Every member already
    carries part_of→sector (verified on Ujjivan: industry
    Banks - Regional, part_of→Banking), so a generic sub_sector would
    duplicate the grouping; finer splits (regional vs diversified
    banks, life vs general insurance) need per-company signal the
    industry label does not carry — future slice if a question demands.
  - **Deferred (stay in the worklist):** Oil_Gas_Downstream (coined
    name; Energy groups the 10), Travel_Services (Travel covers),
    Medical_Devices (2 members), Exchanges (4 — institutions, not
    companies), plus the ≤3-member tail.
  - **Projected coverage:** 277 + 255 + 56 + 12 = 600/816 = **73.5%**
    (exact on rerun) ≥ 70% target. Execution: vault notes via the
    standard entity ingest path (never hand-SQL), one-line alias
    additions, `make derive-hyperedges` rerun, worklist re-emit,
    measured delta row in the appendix.
- **S4 — tests + docs:** tests for DDL bootstrap, backfill dry-run/apply/
  idempotence/FK-cascade, consumer determinism on a toy incidence (seeded,
  <1 s) + degeneracy regression (giant hyperedge → single block is DETECTED,
  warned). `db_schema.md` sections + assessment memo cross-link + this
  proposal's slices.

Alternatives considered and rejected: HyperNetX (heavy deps: igraph +
scikit-learn + matplotlib required, `pandas<3.0` pin conflicts with pandas
3.0.5 in `.venv`); HAT (no license — hard blocker); extending `graph_edges`
with a JSON members column (destroys the relational seam + FK integrity the
assessment §6.3 wanted back); Onager (C API is dyadic `onager_ctr_*(src,dst)`,
cannot express hyperedges); doing nothing (no committed consumer — rejected:
the consumer IS the demand, this proposal).

## 4. Acceptance criteria & shakedown

1. `python3 helpers/graph/derive_hyperedges.py` → dry-run summary listing 5
   sources, 96 hyperedges, 2,687 incidences; `--apply` twice → second run
   inserts 0 rows (UNIQUE idempotence).
2. `python3 helpers/graph/hyper_communities.py` → blocks table (K=8, seeded);
   `--apply` → 1,165 `hypermmsbm_community` rows in `graph_analytics`;
   re-run with same seed → identical memberships (bitwise).
3. `python3 helpers/maintenance/migrate_to_graph_edges.py` on a fresh tmp DB
   → hyper tables exist; on the live DB → no-op (IF NOT EXISTS).
4. `make snapshot` ships `hyper_edges.parquet` + `hyper_incidences.parquet`.
5. `make static-checks` green; new tests pass (3 repeat runs for the seeded
   determinism criterion — never one run).

| Projected outcome | Today | After |
|---|---|---|
| set-valued facts stored losslessly | 0 of 5 sources | 5 sources, 96 hyperedges |
| higher-order analytics lanes | 0 (Onager/igraph dyadic only) | 1 (hy-MMSBM, 0.5 s) |
| new runtime deps | — | hypergraphx (+scipy, +tqdm transitively) |
| schema tables | 16 | 18 |

## 5. Risks

- **EM local optima** — hy-MMSBM is non-convex; mitigations: fixed default
  seed (measured deterministic), K sweep is 0.5 s per run, log-likelihood
  reported for cross-K comparison.
- **Degenerate hyperedges break the EM** (measured on this box, two shapes)
  — singletons (size 1) divide 0/0 in the pair terms, NaN the memberships
  and collapse every node into block 0 (8 singleton country lanes do it);
  giants (> 25% of nodes, e.g. the size-850 India lane) dominate the
  likelihood and collapse the fit the same way. The consumer drops both
  classes at the seam (`_exclude_degenerate`): singletons always, giants
  unless `--allow-giant`; default sources exclude country.
- **dep-tree growth** — scipy 1.18.1 + tqdm 4.70.1 are the only additions
  (measured); no compiled sdist, no torch-class weight (EasyGraph lesson).
- **HGX API churn** (1.8.0, pre-2.0) — the consumer touches exactly two HGX
  symbols (`Hypergraph`, `HyMMSBM`); the bridge seam is one function.
- **Snapshot shape change** — two new parquet files; `snapshot-check`
  compares against the SAME run's manifest, and `make snapshot` regen is
  part of shakedown (acceptance 4).

## 6. Non-goals

- No DuckDB `h_*` materialisation (assessment Phase 1 cache) — follow-up if a
  SQL-over-incidence consumer appears.
- ~~No HIF JSON export lane, no parquet↔HIF validator (§9.3/§9.4)~~ —
  upgraded to slice S18 (2026-09-13) once HGX's own readwrite.hif module
  was verified in the installed engine; demand-gated.
- No events / JV-hyperedge backfill (`events.counterparty` regroup, jv
  `venture` key) — jv lacks a clean regroup key today (measured: 2 of 69
  rows); needs an extract_relations change first.
- No `graph_edges` / `relations` VIEW / Onager / igraph changes; no API/UI
  surface for hyperedges; no hyperedge prediction, motifs, or dynamics lanes.
- Taxonomy NOTES are untouched — hy-MMSBM outputs are analytics rows
  (proposals for a human), never auto-written taxonomy.

### Capture-gap ledger (measured 2026-09-13; follow-up scope, not this arc)

The five backfill sources exhaust what today's capture holds. The gaps, their
measured state, and the cheapest honest fix per gap:

| Gap | Measured state | Recoverable from existing notes? | Path |
|---|---|---|---|
| company→sub_sector membership | 78 sub_sector entities, 0 notes; `entity_tags` `subsector/*` = 57 rows, ~1 company each (ad-hoc, not classification); `sector_classification` holds the SECTOR only | NO for the canonical 78 — the authored `## Sub-Sectors` regions in 17 sector notes describe ACTIVITIES ("Steel production and processing"), not member lists | capture discipline: optional `subsector:` YAML in the company-note template + sync_tags lane |
| external sub-sector-classification | 921/1,165 company notes already carry yfinance `industry:` frontmatter — 816 non-null over 117 distinct values (a controlled vocabulary) | YES — already captured, unused as structure | cheapest follow-up: regroup `industry:` frontmatter into `industry` hyperedges (~117 hyperedges / 816 incidences) — zero new sources |
| JV partner sets | only 2/69 `jv_with` rows carry `venture` in properties | PARTIAL — venture names exist in edition prose | extract_relations upgrade: capture venture into properties; the regroup key then exists |
| events.counterparty | 436 events, free-text counterparty, no FK | YES (name resolution against `entities`) | resolve + FK backfill; event hyperedges follow |
| promoter groups | 1 seed group (3 members, Muthoot) | PARTIAL — group prose exists in editions | same extract upgrade as the JV gap |

External sources: NOT needed for the first tier — the in-tree yfinance
industry lane already IS an external classification source at 70% company
coverage. Seek more only if closing the remaining 30% (105 `industry: null`
+ 244 notes without the field) matters.

### MAJOR — edition→participant capture: the flagship signal is under-captured (added 2026-09-13, operator)

The §6.1 flagship ("edition → set of co-mentioned companies is literally the
clique expansion") is the thinnest source in the store, and the richest
evidence for it is already in the DB, unprojected:

| Signal | Measured state (live, 2026-09-13) | Verdict |
|---|---|---|
| `co_mentioned_in` regroup (landed S2) | 20 editions / 230 members | thin — 18% of editions |
| **`quotes` table regroup (NOT projected)** | **93 editions × 693 distinct entities; 1,257 distinct (edition, entity) pairs (1,180 company-only); avg 13.5 entities/edition (max 35); 87 editions with ≥5 entities; 1,208 pairs have intensity > 1** | **recoverable NOW, pure SQL — and the per-(edition, entity) quote COUNT is exactly the per-incidence `weight` the HIF-parity column was built for (§3 S1)** |
| `cited_in` star (edition fan-in) | 1,899 dyads over edition hubs | same incidence, stored as stars |
| concall participant sets | quote `as_of_edition` values that are concall titles, not editions (the analytics T1 honest-miss) | capture upgrade — concall title normalisation |

Why this is the major item **for using the stack**: every higher-order
consumer's signal ceiling is set by incidence density. Landing the quotes
regroup takes the edition lane from 20/230 to ~93/1,257 (5× editions, 5.5×
members) with REAL weights, all from existing data — no extraction, no new
sources. That is what makes higher-order motifs ("3-way co-mention vs 3
pairwise") and hyperedge prediction meaningful instead of starved. Natural
next slice after S5–S7.

## External references

- **hypergraphx-data** — curated real-world hypergraph datasets; two
  formats (human-readable JSON + binary) balancing accessibility and
  computational efficiency: <https://hgx-team.github.io/hypergraphx-data>
- Lotito, Q. F., et al. (2026). *Hypergraphx-data: a repository for
  higher-order network data*. COMNET 14(3), cnag014.
  DOI [10.1093/comnet/cnag014](https://doi.org/10.1093/comnet/cnag014) ·
  [arXiv:2605.18166](https://arxiv.org/abs/2605.18166) — dataset
  taxonomy, format spec, and collection methodology (S20's source;
  operator-supplied references).

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-13 | `uv venv --python 3.14 /tmp/venv_hgx && uv pip install hypergraphx` | 1.8.0, 8 pkgs, 3.3 s, no builds | +scipy 1.18.1, tqdm 4.70.1, python-dateutil, six; networkx/numpy/pandas already in `.venv` |
| 2026-09-13 | HyMMSBM fit, sector+theme, K=8, n_iter=500, seed=42 | 0.5 s, iters=499 (tol off), blocks 300/248/138/119/116/99/85/60 | 1,165 nodes / 54 hyperedges, sizes 2/21/95 |
| 2026-09-13 | same, K=12 | 0.5 s, blocks 327/172/144/91/90/84/79/67/50/38/19/4 | K sweep cheap |
| 2026-09-13 | sector+country+theme (75 hyes, incl. size-850 India) | all nodes in 1 block (K=8 and K=12) | giant-edge degeneracy — country excluded by default |
| 2026-09-13 | two seeded fits, seed=42 | max abs u-diff 0.0 | deterministic |
| 2026-09-13 | sector+country+theme minus India (74 hyes) | STILL 1 block — NaN path (`RuntimeWarning: divide by zero` in `model.py:489`) | giant edge is not the only failure: 8 singleton country lanes (size 1) 0/0 the pair terms |
| 2026-09-13 | same, singletons (<2 members) also dropped (63 hyes) | healthy blocks 296/164/146/129/124/117/100/89 | guard drops singleton+giant at the seam |
| 2026-09-13 | block purity vs sector, K=8 | Pharma 0.82 (API_Manufacturing), Metals 0.54 (+Battery/Renewables), Building/EMS/Defense 0.39 (+Make_In_India), FMCG+Tech 0.34, Logistics/RE/NBFC/Media 0.20 | blocks cross the taxonomy — the §7.4 signal |
| 2026-09-13 | live DB counts (read-only) | entities 1,649 (company 1,165, edition 114, institution 207, categories 163); edges 19,261 over 19 types; co_mentioned_in editions 20; same_group groups 1; jv_with venture-named 2/69 | proposal motivation table |
| 2026-09-13 | HGX API probe | `HyMMSBM` NOT exported from `hy_mmsbm/__init__` (empty) — import from `...hy_mmsbm.model`; constructor REQUIRES `assortative` when `w` absent (first fit died `ValueError`); node remap = `get_mapping()._to_int` (LabelEncoder) | API pinned in the bridge; recorded so nobody re-probes |
| 2026-09-13 | HyMMSBM convergence, sector+theme | runs all 500 iters (`tolerance=None` → `tolerance_reached=False`); 0.5 s regardless | early-stop not needed at this scale |
| 2026-09-13 | theme concentration inside blocks (K=8) | API_Manufacturing 14/20 members (70%) inside the Pharma block; Renewable_Energy 23/89 (26%) in Metals+Renewables | blocks are not sector echoes — themes move real membership |
| 2026-09-13 | sub_sector recoverability | 78 sub_sector entities, 0 file_path; `subsector/*` tags 57 rows × ~1 company; `sector_classification` = sector name; 17 sector notes author `## Sub-Sectors` as activity prose (Metals/Aviation checked) | sub_sector OUT of backfill scope (§3 S2) |
| 2026-09-13 | yfinance `industry:` frontmatter | 921/1,165 company notes carry it; 816 non-null; 117 distinct values | capture-ledger follow-up #1 (§6) |
| 2026-09-13 | scale comparison §2.1 | see §2.1 rows; script `helpers/bench/hyper_scale_bench.py`, baseline `tests/data/hyper_scale_baseline.json` | synthetic, seed=7, lognormal(3.1, 0.9) sizes, caps 150/500/1200 |
| 2026-09-13 | S5 industry apply (live) | 117 hyperedges / 816 incidences inserted; second apply 0/0; store now 213 hyperedges / 3,503 incidences over 6 edge types | consumer default widened to sector,theme,industry; blocks sharpened (247/196/176/165/144/134/56/47; industry labels top 4 blocks) |
| 2026-09-13 | S5 singleton guard on industry lane | 117 labels include 30 singletons — auto-dropped by the guard | guard does real work on real data |
| 2026-09-13 | S6 sweep (live, sector+theme+industry) | K=4 -497.1 / K=6 -452.8 / K=8 -406.0 / K=12 -359.0 / K=16 -314.7; effective_blocks == K at every K (no dead blocks) | log-lik rises with K by construction — caveat printed; K=8 default retained until a penalised criterion is wanted |
| 2026-09-13 | S7 igraph retirement | igraph_bridge.py + test_igraph_bridge.py deleted; static_checks exemption removed; D16 row in graph_design.txt | `rg igraph` → only D16 pointer + "multigraph" false positives |
| 2026-09-13 | longest-chains report (`stats.longest_chains`, in `make graph-stats`) | ALL view: 2 comps, diameter 6 (2,057 ties), top chains ride invested_in x11 + membership; ACTIVITY view: 370 comps, diameter 6 (505 ties), chains ride invested_in x20, semantic_peer x7, co_mentioned_in x3 — jv/subsidiary/supplier lanes absent | operator-requested capture-quality readout: which domains carry long-range connectivity |
| 2026-09-13 | S8 apply (live) | 87 new edition hyperedges + 1,180 weighted incidences; edition lane 20 -> 107; store 300 hyes / 4,683 inc; second apply 0/0; S5 upstream_types slip (industry []) repaired via props-converge UPDATE | top weighted: Reliance 41 quotes in Reliance_Vedanta_Gillette |
| 2026-09-13 | S9 apply (live) | events: ALTER + counterparty_entity FK; resolution 110/110 EXACT (0 fuzzy needed, 0 unresolved, no comma lists); 110 event hyperedges / 220 incidences (per-row: provenance is not a happening key — 60 JVs share one source_ref); FK check clean | store 410 / 4,903 |
| 2026-09-13 | S10 apply (live) | 2 jv hyperedges (seed ventures); retro-yield of capture_venture_name on 69 existing jv quotes: **0** — corpus quote-windows do not name ventures; hook lands for future prose | store 412 / 4,907 |
| 2026-09-13 | S13 sweep BIC (live, sector+theme+industry) | K=4 -497.1/BIC 25,698.8 · K=6 42,075 · K=8 58,447 · K=12 91,283 · K=16 124,124 — BIC min at K=4 | the N(K-1) penalty (~8K BIC per K step at N=1,165) overwhelms +~45 loglik/K: BIC over-penalises membership models; minimum = lower-bound guide (caveat printed); K=8 default retained |
| 2026-09-13 | S12 centralities (live) | ho_pagerank top: Exide 0.0044, Indag/JBM 0.0038 (auto-component cluster); s_betweenness top: SpaceX 0.178, Deep Industries 0.171; 1,158 rows × 3 metrics written (7 nodes off the largest component; RW requires connected) | D16 lanes now runnable: `helpers/graph/hyper_centralities.py` |
| 2026-09-13 | S11 sub-sector map (live) | 42 curated aliases -> 32 sub_sector hyperedges / 277 incidences (Formulations 37, Specialty_Chemicals 32, Iron_and_Steel 27); FIRST company->sub_sector linkage in the store (taxonomy had only sub_sector belongs_to sector); rerun 0/0 | 77 labels unmapped -> findata/Misc/subsector_worklist.json (top: Auto Parts 45, Banks - Regional 38, Electrical Equipment 26 — NBFC/banks/auto-ancillary/IT need new canonical nodes, operator taxonomy decision); coverage 277/816 industry incidences (~34%) |
| 2026-09-13 | S14 weighted walk (live) | HGX RW verified binary (transition_matrix builds binary_incidence_matrix — weighted=True is a no-op for the walk); own `stationary_pi`: W[n,e]=incidence weight, M=W·diag(s_e−1)·Wᵀ, power iteration; unit-weight reduction to HGX test-pinned (default run reproduces S12 values exactly); --sources sector,theme,industry,edition: 1,180 weighted incidences engaged, 1,165 rows × 3 metrics reapplied | headline shift: Reliance 0.0131 #1 (41-quote edition intensity) vs absent from unweighted top-10 — first analytical payoff of S8 weights |
| 2026-09-13 | `PRAGMA table_info` + DDL read | `belongs_to` is hierarchy-only (78 sub→sec, 42 sec→super); company membership = part_of/listed_in/exposed_to | corrected vs assessment §1 wording |
| 2026-09-13 | operator decision + live PRAGMA/entity checks | S17 vocabulary FINAL: 22 new nodes + Aerospace alias + 9 sector-level skips; 5 collision classes found (NBFC/Housing_Finance/Capital_Markets/Semiconductors/Building_Materials are sectors); bright-line rule approved | projected 600/816 = 73.5% coverage |
| 2026-09-13 | speaker-supplement eval pass 1 (read-only; residue digest + entity joins) | 92 speakers / 324 rows classified: 169 company-existing / 48 needs-entity / 54 non-company / 53 unknown | projected 96.6–97.2% coverage; draft at findata/Misc/speaker_supplement_draft.json |
| 2026-09-13 | S21 pass 2 (rollup tags + source-note heading walk, read-only) | 460-row residue: 261 deterministic (roster/token/heading-walk) + 93 speaker-table; 67 needs-entity (16 names, approved); 46 non-company (operator ruling); 85 unknown | projected 97.6% existing-only / 98.4% after creation; Quotes super_sector = designed catch-all, retirement idea withdrawn |
| 2026-09-14 | S17 EXECUTED (build_sector_hierarchy --apply + 26 alias lines + derive --apply) | 22 new sub_sector entities + belongs_to (98 total sub_sectors, 140 edges); 23 new sub_sector hyperedges / 323 incidences; worklist 77→51 labels | coverage 600/816 = 73.5% ≥ 70% target (50.9% of all companies) |
| 2026-09-14 | S21 EXECUTED (backfill_quotes_attribution.py --apply: ensure-entities 14 created + roster/token/heading-walk/speaker-map) | 572 quotes re-attributed with audit trail (roster 188, token 57, heading-walk 210, speaker-map 117); 8,086/8,272 = 97.8% company-attributed | 94 rows remain under Quotes catch-all + ~45 regulator/analyst rows — by design |
| 2026-09-14 | derive_hyperedges --apply (post S17+S21) | 469 hyperedges / 5,659 incidences / 1,318 weighted (edition weights grew with new members); sector +14, sub_sector +23/+323, edition +2/+138 | store delta vs 444/5,184 |
| 2026-09-14 | S18(b,c,e) EXECUTED (helpers/graph/hyper_arrow.py + wrapper + 7 tests) | load_incidence_arrow live(DuckDB sqlite ATTACH)/snapshot(zstd parquet, in-flight join); incidence_query SQL-over-Arrow; hypergraph_from_arrow to_pylist boundary; legacy load_incidence = thin wrapper | 48/48 tests; live==snapshot parity incl. weights; ruff clean |
| 2026-09-14 | S16 RESOLVED (investigation: HGX line_graph weighted=True + nx cost-vs-similarity inversion; measured 97-edge live lanes) | top-8 ranking churn 4/8 overlap under naive AND cost-transformed weights — no canonical semantics | s-lanes stay unweighted; weighted demand routes to S14 ho_pagerank |
| 2026-09-14 | S15 EXECUTED (eigen wrappers in hyper_centralities.py + 2 tests) | eigen_cec/zec/hec: uniformity gate with skip-note, int-mapped HGX workaround (string nodes crash CEC's W[edge[i], edge[j]]), seed=42 determinism | 50/50 tests; live dry-run: sector+sub_sector correctly reports non-uniform skip |
| 2026-09-14 | S18(d) EXECUTED (helpers/graph/hyper_hif.py + 2 tests) | three zstd HIF-column parquet tables (footer network_type), transient JSON skin from parquet, round-trip validator; HGX read_hif consumes the skin | live: 1179/206/3327/1318-weighted; caveat: 7 member-set-colliding edition edges collapse in HGX objects |
| 2026-09-14 | S19(a-c) EXECUTED + (d) SWEEP (helpers/validators/data_format_checks.py wired into make static_checks; 10 tests) | AST guards: parquet-zstd call-sites + DuckDB COPY (docstrings/f-strings handled), Arrow-in-flight data-lane producers with HGX-boundary exemption; baseline hygiene fails on stale entries | full-repo initial sweep: 0 zstd violations, 2 Arrow baselines (load_incidence legacy dict, load_incidence_weights) — the straggler ledger; bonus fix: pre-existing sqlite3.connect P0 red in hyper_centralities -> connect(db_path) |
| 2026-09-14 | S20 EXECUTED (trivago-clicks full pipeline; helpers/bench/hyper_data_bench.py) | Catalog host TLS chain broken -> pulled ORIGINALS from the sources the catalog reproducibility READMEs cite (Cornell /~arb/data via Drive; bench_data/ gitignored). trivago-clicks: 233,202 edges / 726,861 incidences / 172,738 nodes; HGX construct dedupes to 220,971 unique edges (matches catalog). Quiet-box timings (tracemalloc peaks): parse 3.6s/130MB, construct 4.9s/158MB, filter 0.1s (0 giants 0 singletons), Hy-MMSBM k=8 x 100 iters 41.1s/334MB, stationary-pi walk 67.8s/199MB pi_sum=1.0. s-centralities KILLED at ~25 min / ~7GB RSS (tracemalloc): s-betweenness is BFS-per-node superlinear — the six-lane centralities stage stays a TAXONOMY-scale tool (20K-edge smoke: 48.5s), not web-scale. EM per-iter cost 0.41s at 727K incidences vs 0.011s at 100K synthetic — superlinear in nodes (u is N x K), seconds-scale posture holds. License: not stated on catalog/Cornell pages; cite Chodrow, Veldt & Benson (2021) Science Advances (dataset construction) + Lotito et al. (2026) COMNET for the catalog. | pi_sum=1.0; node/edge counts match the published catalog exactly  OPERATOR RULING 2026-09-14: stackoverflow-answers (largest pick, ~5.4M incidences) SKIPPED after two OOM incidents strained the box — S20 closes on trivago-clicks; bench script + cached data are in place for a later run (--skip-centralities --no-tracemalloc, solo).|
| 2026-09-14 | S18(b)/S14 EXITS EXECUTED — Arrow baseline ledger EMPTIED | consumers (hyper_communities + hyper_centralities CLIs) now call load_incidence_arrow directly and derive working shapes via the sanctioned Arrow->dict boundaries incidence_dict / weights_from_arrow (hyper_arrow.py); load_incidence + load_incidence_weights DELETED (the weights SQLite read dies with the loader; weight column rides the Arrow table); stationary_pi contract unchanged (weights dict in, Arrow-derived) | static_checks advisory 2 -> 0 (check shows ✓); CLIs smoke-tested (sector fit + centralities); 66 tests green (incl. repointed label-prefix + arrow-boundary tests); `_ARROW_BASELINE = {}` with retirement note |
