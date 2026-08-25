# Proposal: Relationship Enrichment from External Sources — "Relations 2.0"

**Date**: 2026-08-22
**Status**: EXECUTED & CLOSED (2026-08-25) — E1 DONE 2026-08-23, E2 DONE & APPLIED 2026-08-24 (3425 competes_with), E3 DONE & APPLIED 2026-08-25 (7776 semantic_peer), E4 DONE 2026-08-25 (478 coinfer pending), E5 DONE & APPLIED 2026-08-25 (715 invested_in, 205 institutions), E6 DONE 2026-08-25 (API/UI).
**Depends on**: nothing running; extends `enrich_from_yfinance.py`,
`extract_relations.py`, `suggest_relations.py`; feeds the graph consumed by
the Research Desk redesign (`graph_docs_ui_redesign.md` S3/S4 legend +
Inspector).

---

## 1. Background & measured state

The knowledge graph carries 13 edge types, but coverage is bimodal:
backbone relations derived mechanically from note structure are rich;
business-semantics relations extracted from prose are nearly empty.

Measured 2026-08-22 (`SELECT edge_type, COUNT(*) FROM graph_edges`):

| edge_type | count | share | origin |
|---|---|---|---|
| co_mentioned_in | 1,329 | 26.0% | mechanical (note co-occurrence) |
| has_company | 1,065 | 20.8% | mechanical (sector → company) |
| part_of | 1,065 | 20.8% | mechanical |
| cited_in | 1,005 | 19.6% | mechanical |
| exposed_to | 359 | 7.0% | prose extraction (G-series synonyms) |
| belongs_to | 120 | 2.3% | mechanical |
| subsidiary_of | 63 | 1.2% | prose extraction |
| jv_with | 54 | 1.1% | prose extraction |
| acquired | 39 | 0.8% | prose extraction |
| competes_with | **10** | 0.2% | prose + yfinance pipeline **not applied** |
| supplier_to | 6 | 0.1% | prose extraction |
| same_group | **4** | 0.1% | prose extraction |
| customer_of | **1** | 0.0% | prose extraction |

Two structural findings from today's investigation:

1. **The yfinance `competes_with` pipeline was never effectively applied.**
   `enrich_from_yfinance.py::write_competitor_edges` deletes
   `source_ref LIKE 'yfinance:%'` then inserts industry-clique pairs, but
   the DB contains **zero** rows tagged `yfinance:*` — only the 10
   prose-derived `competes_with` remain. The "5900 projected" figure in
   completed.md came from the dry-run projection path, which never wrote.
   (Root cause worth confirming during E1 — possibly the apply run
   predated most tickers, or a later `graph_edges` rebuild wiped them.)
2. **Stale tickers silently starve enrichment.** `TATAMOTORS.NS` now 404s
   (post-demerger the entity is "Tata Motors Passenger Vehicles"); every
   404 ticker is a company that gets no metrics, no industry tag, no
   candidate edges — invisibly.

## 2. Problem statement

Sector/company backbone edges answer "what sector is X in" but not the
questions the redesign's Inspector and Lens modes are built around: who
are X's real peers, who owns X, what group does X belong to, who supplies
whom. The prose extractor captures these only when a newsletter happens
to spell the relation out — which for structural facts (ownership,
subsidiaries, groups) is rare. Structured external sources cover exactly
this gap, but each candidate source has hard limits that were probed
today (§4). We need a realistic, doctrine-compliant enrichment plan that
spends effort where yield actually exists.

## 3. Goals / non-goals

### Goals

- G1. Materially densify the six near-empty business-relation types
  (`competes_with`, `same_group`, `subsidiary_of`, `supplier_to`,
  `customer_of`, `jv_with`) plus one new ownership relation.
- G2. Every enrichment pass follows house doctrine: read-only fetch →
  explicit apply, dry-run parity, idempotent DELETE-by-source_ref-prefix +
  INSERT, full provenance in `source_ref`/`properties`.
- G3. Uncertain candidates route into the **existing**
  `_pending_relations.txt` triage workflow instead of polluting the graph
  (the sidecar stays CLI-written; enrichment joins it, never bypasses it).
- G4. Ticker hygiene: stale/demerged/dead tickers detected and reported
  so silent starvation stops.
- G5. New relations surface in the graph API/UI (legend filters,
  Inspector) without breaking existing consumers.

### Non-goals (explicit)

- No scraping of Screener.in / Moneycontrol / NSE (ToS gray zones,
  fragile selectors, maintenance burden). Revisit only if structured
  sources prove insufficient AND the user accepts the tradeoff.
- No LLM-based relation extraction in v1 (API cost, nondeterminism);
  recorded as a follow-up option (§12 Q5).
- No changes to the mechanical backbone edges (co_mentioned_in,
  cited_in, part_of/has_company) — they're healthy.
- No write-path changes to `_pending_relations.txt` semantics; enrichment
  appends candidates in its format only.

## 4. Source capability assessment (probed 2026-08-22)

Live probes against the installed stack (yfinance 1.6.0) and public APIs:

### 4.1 yfinance — what it actually gives this universe

Universe composition: 725 `.NS`, 113 `.BO`, 64 bare, ~14 foreign suffixes
(931 tickered companies).

| Capability | Probe result | Verdict for us |
|---|---|---|
| `.info` industry/sector | works (already used; 86 industries seen) | **strong** — basis for peers |
| Institutional holders (`get_institutional_holders`) | AAPL ✓ (Blackrock 7.97%, Vanguard 6.57%, pctHeld+shares+date); **RELIANCE.NS, TATASTEEL.NS → empty** | **US-listings only** — applies to ~14 tickers; honest ceiling ≈ ≤140 rows |
| Mutual-fund holders | method exists; same regional limitation expected | weak, skip in v1 |
| Peers field / screener module | removed in modern yfinance; `Screeners` absent in 1.6.0 | n/a — emulate peers ourselves from industry + mcap (§6.2) |
| Officers (`info["companyOfficers"]`) | available in info | deferred (person entities = bigger blast radius) |

**Conclusion**: yfinance's marginal value here is (a) finally applying the
industry→competes_with pass with a smarter topology than raw cliques,
(b) ownership edges for the handful of US-listed names, (c) nothing for
Indian corporate groups.

### 4.2 Wikidata SPARQL — sparse where we need it

- Search API works: "Tata Motors" → `Q188514` cleanly resolved.
- Structure queries: `Q188514` has **no `P749` (parent org)** and no
  usable `P355` (subsidiary) bindings returned (HTTP 200, 0 rows).
  Corporate-structure properties for Indian issuers are inconsistent at
  best; coverage decays fast below large caps.

**Conclusion**: viable only as an opportunistic tertiary pass for global/
blue-chip names, exact-normalized-name match required, misses logged.
Default: **deferred behind a flag** (§12 Q4).

### 4.3 Local embeddings (bge-small-en, #141) — the underrated source

We hold real 384-d vectors for all 1,314 entities (DuckDB VSS,
`semantic_neighbors` already computed at query time). Top-k cosine pairs
materialized as a new **`semantic_peer`** edge type would:

- cover **every** entity including non-company notes (no tickers needed),
- cost nothing and stay fully local (no network, no ToS questions),
- give the Lens a similarity view that survives when prose relations are
  absent (exactly the thin regions we're fixing),

with the honest caveat that vector-neighbors ≠ business relation; they
belong in a distinct type + lower weight, clearly labeled in the UI.

### 4.4 Our own co-mention graph — inference from data already held

co_mentioned_in (1,329 edges, weighted) × same-sector boost is a classic
link-prediction setup. Candidates like "A and B co-mentioned 6× inside
Auto-Ancl components notes but have no relation edge" are precisely
peers/JV/supplier leads. Route these into `_pending_relations.txt`
(G3) — the triage workflow already exists and the DB shows past manual
triage batches (`pending_relations:triage`, `manual:foreign-parents:*`).

### 4.5 Prose extractor itself — cheapest first win

Current synonym families (G1.x series) miss common Indian-market phrasing:
promoter/"promoter group", "group company", "flagship", "step-down
subsidiary", "holds X% in", "acquisition of Y% stake in". Pattern-family
v2 + a diff-audit of what changed is hours of work, zero network risk,
and directly attacks `same_group`=4 / `customer_of`=1.

### 4.6 Summary matrix

| Source | Edge types fed | Expected yield | Cost/risk | Priority |
|---|---|---|---|---|
| Prose patterns v2 | same_group, subsidiary_of, customer_of, supplier_to, jv_with | 2–4× current counts (audit will firm this) | hours, local | **E1** |
| yfinance apply + topology | competes_with | ~1.5–3k (bounded, §6.2) | network ~931 calls | **E2** |
| Embedding neighbors | semantic_peer (new) | ≤ ~13k rows (k=10 sym-dedup) | local, seconds | **E3** |
| Co-mention inference → triage | candidates for all business types | dozens of high-quality leads | local | **E4** |
| yfinance US holders | invested_in (new) | ≤ ~140 rows (~14 cos) | network, trivial | **E5** |
| Wikidata | subsidiary_of, same_group | unknown, likely small | network, sparse | deferred (flag) |

## 5. Relation taxonomy & provenance design

New edge types (both feed the S3 legend naturally; tokens.css gains two
`--edge-*` accents):

- `semantic_peer` — symmetric, weight 0.5, `source_ref='embeddings:bge-small:v1:<run-date>'`,
  properties carry `{cosine, rank}`. Regenerable; deletable wholesale by prefix.
- `invested_in` — directed institution → company, weight scaled by stake
  (≥5% → 3.0, else 1.0), `source_ref='yfinance:holders:<date>'`,
  properties `{pctHeld, shares, value}`, **`valid_from=dateReported`**
  (temporal convention already used by `acquired`; enables future
  ownership-delta events).

Entity model change (§12 Q3): institutional holders introduce
`entity_type='institution'` rows with `file_path=NULL` (not note-backed).
All existing note-derived entity flows ignore them naturally; `/api/entities`
grows a type param so UI datalists can exclude/include deliberately.

Confidence routing (G3):

- **Tier A, direct insert**: structured facts with deterministic identity —
  industry peers (within a company's own fetched row), US holder tables,
  Wikidata only on exact normalized-name hit (deferred).
- **Tier B, pending sidecar**: everything inferred — co-mention link
  prediction, embedding pairs *below* the store threshold if the user
  wants curation over volume (§12 Q2 alternative), prose-pattern hits on
  foreign/unresolved entity stubs.
- **Tier C, log only**: fuzzy-match failures, 404 tickers, ambiguous
  resolutions — into the run report, never the graph.

Every insert stamps `properties.fetched_at` + `source_ref` prefix
(`prose-v2:`, `yfinance:industry:`, `yfinance:holders:`,
`embeddings:bge-small:v1:`, `coinfer:`), preserving the
delete-scoped-by-prefix idempotence contract.

## 6. Pipeline design

### 6.0 Endpoint resolution: ticker-first (user directive, 2026-08-22)

Company notes already carry yfinance-format tickers in frontmatter
(`ticker: RELIANCE.NS`), mirrored into `entities.ticker` — 916 of 1,065
companies (86%). **All external-source lookups key on that ticker first**.
Per the frontmatter contract (`ticker: null` + optional `listed: false`
records known-unlisted status), the 149 ticker-less companies are
deliberately unlisted — Reliance Retail, Wabco India… — and are **skipped,
never fuzz-matched**. Fuzzy-name resolution therefore has no resolution
role at all; it survives only as an audit safety net whose hits land in
the tier-C report for manual promotion to explicit tickers. No lookup
ever invents a ticker. KNN peer distance (§6.2) likewise prefers stored
data: `company_metrics.market_capitalization` (last yfinance refresh),
falling back to the coarse `market_cap/` bucket tag when the metric is
absent.

### 6.1 Shape: subcommands under one driver

Extend `enrich_from_yfinance.py` into `helpers/maintenance/enrich_relations.py`
(importing reusable bits from the former) OR add sibling scripts per source.
Decision baked here: **one driver, `--source {prose,yfinance,embeddings,coinfer,holders,wikidata}`**,
each source independently runnable, independently idempotent, sharing
resolver/report/pending-append helpers. Rationale: single make entry,
shared report format, no cross-source write races.

Common pass skeleton (mirrors existing metrics flow):

```
fetch/generate candidates (network or local)
  → resolve endpoints ticker-first (§6.0)
  → classify Tier A/B/C
  → dry-run: print projected counts + samples (parity asserted in tests)
  → apply (default OFF): single transaction per pass,
      DELETE ... WHERE source_ref LIKE '<pass-prefix>:%',
      INSERT OR IGNORE tier-A rows; append tier-B to _pending_relations.txt
  → write report (metrics_report.txt-style, mode-labeled)
  → remind: make graph-rebuild (qa's check_cache_consistency ERRORs
      on a stale DuckDB cache otherwise)
```

Writes go through `helpers/graph/_edge_writer.py` conventions — symmetric
pairs stored as **one canonical row with `source ≤ target`** (decision D4),
relying on `UNIQUE(source, target, edge_type)` so INSERT OR IGNORE is the
dedupe; never touch the read-only `relations` VIEW.

### 6.2 Competes_with topology: bounded peers, not raw cliques

Raw industry cliques = Σ n·(n−1)/2 ≈ 5,900 pairs — dense to the point of
visual uselessness (and the reason the number never got applied, per §1).
Design: within each industry, connect each company to its **K nearest
neighbors by market-cap proximity** (|log(mcap_a/mcap_b)| distance),
K default 8, union of directed-KNN made symmetric. Expected ≈ 931·K/2 ≈
3.7k upper bound, realistically ~1.5–2.5k with sparse industries. Weight
decays with rank distance (1.0 → 0.4). Full clique remains reproducible
via `--topology clique` flag. (§12 Q1 confirms K.)

### 6.3 Co-mention inference (tier B generator)

Score(s,t) = co_mention_weight(s,t) · idf_boost(shared_note_types) ·
sector_match_boost · (1 − existing_edge_penalty). Emit top-N per company
(N default 3) above score threshold into `_pending_relations.txt` with
`edge_type_hint` lines; human triage promotes to real edges via the
existing flow. No auto-insert.

### 6.4 Ticker hygiene (runs inside every yfinance pass)

404/no-data tickers collected into a dedicated report section + a
`ticker_issues` list in the report file (name, ticker, error class).
Known case: TATAMOTORS.NS → TMPV-era rename needs a manual
`entities.ticker` edit; the tool reports, never guesses (all-writes-
explicit). Optional `--check-only` mode runs hygiene alone.

## 7. CLI / make integration

```
make relations-enrich ARGS="--source prose --dry-run"
make relations-enrich ARGS="--source yfinance --workers 2"
make relations-enrich ARGS="--source embeddings --k 10"
make relations-enrich ARGS="--source coinfer --per-company 3"
make relations-enrich ARGS="--source holders --dry-run"
make relations-enrich ARGS="--source all --dry-run"
```

Not wired into `maint`/`maint-full` (network-dependent; same standalone
precedent as `metrics-rebuild`). Ordering note documented: run
relations-enrich **before** `make graph-rebuild` so DuckDB picks the new
edges up on rebuild.

## 8. Implementation plan (slices)

| Slice | Content | Tests | Gate |
|---|---|---|---|
| **E1** | Prose patterns v2 + diff-audit harness (before/after edge counts per family); resolver ambiguity logging | pattern fixtures, before/after snapshot test | targeted pytest, `ty` clean |
| **E2** | Driver skeleton + yfinance industry pass with KNN topology + ticker-hygiene report; retire dead clique path | mocked-fetcher unit tests; double-run idempotence; dry-run parity | pytest + one live `--dry-run` |
| **E3** | `semantic_peer` materialization from DuckDB VSS (k, threshold, sym-dedupe, weight 0.5) | determinism, k-cap math, prefix-delete idempotence | pytest + live dry-run |
| **E4** | Co-mention inference scorer + pending-sidecar append (format-compatible) | scorer unit tests; sidecar append format test; **no-graph-write assertion** | pytest |
| **E5** | `invested_in` holders pass + `institution` entity creation (dedup by normalized_name) | fixture DataFrame tests; dedupe test | pytest + live dry-run |
| **E6** | API/UI surfacing: `/api/graph/*` include new types; tokens.css accents; legend filter wiring lands with redesign S3/S4 | endpoint param tests | frontend-check + smoke |

Each slice independently shippable; E2 first live apply is the moment
competes_with goes 10 → ~2k (single command, reversible via prefix delete).

## 9. Risks

- **False peers from blunt industries**: Yahoo industry labels are coarse;
  KNN-by-mcap narrows but "peers" remains approximate. Mitigation: weight
  decay, distinct source_ref, UI legend can toggle the type off entirely.
- **Embedding neighbors misread as business ties**: mitigated by separate
  type + weight 0.5 + explicit labeling; never merged into competes_with.
- **Rate limiting / IP throttling (yfinance)**: existing retry/backoff +
  workers=2 default; failures degrade to report entries (partial-write
  safety preserved: DB transaction opens only after fetch phase completes).
- **Pending-sidecar flooding (E4)**: capped per-company N; threshold
  tunable; sidecar growth reviewed in report.
- **Entity-universe growth (institutions)**: ~dozens of new NULL-file_path
  rows; contained via entity_type + /api/entities type param.
- **Silent wipe hazard**: every DELETE scoped by strict source_ref prefix;
  regression test asserts unrelated edges survive every pass.

## 10. Success criteria

- competes_with: 10 → 1.5k–2.5k bounded, mcap-plausible pairs; spot-check
  of 20 random pairs judged sensible by the user.
- same_group + subsidiary_of combined: ≥150 (from 67) after prose v2.
- customer_of/supplier_to: >25 combined (from 7).
- semantic_peer: ≤13k rows, deterministic across runs, regenerable.
- invested_in: present for every US-listed ticker with holder data.
- Zero rows written without tier classification; zero unscoped DELETEs
  (test-enforced).
- Dry-run of each pass prints counts matching subsequent apply ±0
  (parity test).
- Ticker-hygiene section lists every dead ticker incl. the TATAMOTORS
  class, with zero false negatives on the known cases.

## 11. Out-of-scope follow-ups recorded

- LLM-assisted prose extraction (needs budget decision) — strongest
  long-term lever for JV/supplier/customer nuance; propose separately.
- Screener.in promoter/shareholding scrape (Indian group truth) — blocked
  on user's ToS appetite.
- Person/officer entities from `companyOfficers`.
- Event-stamped validity windows (`valid_from/valid_to`) for ownership
  changes from quarterly holder deltas.

## 12. Open questions — status after user review 2026-08-22

1. **Competes-with topology**: KNN cap K=8 with rank-weight decay
   (recommended default) vs full clique. → **Proceeding on recommendation**:
   `--topology knn --k 8` default, clique behind flag. Override anytime.
2. **semantic_peer storage**: materialize as edges, weight 0.5
   (recommended) vs compute-on-demand. → **Proceeding on recommendation**:
   stored, regenerable via source_ref prefix.
3. **Institutions as new `entity_type='institution'`** with NULL
   file_path (recommended). → **Proceeding on recommendation**: `/api/entities`
   grows a type param so note-backed datalists stay clean.
4. **Wikidata pass**: → **DEFERRED** (probe sparsity; ticker-keyed lookup
   unsupported there anyway).
5. **LLM extraction**: → **RESOLVED by user: OUT OF SCOPE for now.**
   Remains the recorded follow-up lever for JV/supplier/customer nuance
   when budget appetite changes.

## 13. Implementation log

**R3 — E3 implemented + APPLIED (2026-08-25): semantic_peer from DuckDB VSS DONE.**

Driver `helpers/maintenance/enrich_relations.py --source embeddings` (`make relations-enrich ARGS="--source embeddings [--k 10] [--threshold 0.0] [--apply]"`):

- `helpers/graph/query.py` EDGE_REGISTRY `semantic_peer` → `e_semantic_peer` (SemanticPeer), `_SCHEMA_VERSION 11→12`, `database_integrity_check.py` allowlist `semantic_peer`, DuckDB rebuild materialises `e_semantic_peer`.
- `build_semantic_peer_edges()` : per-company `semantic_neighbors(k=10)` via `v_embeddings` (bge-small-en-v1.5, 384d, 1061 rows), threshold filter, canonical `_pair` + sym-dedup keeping highest cosine, weight `0.5`, `source_ref embeddings:bge-small:v1:<date>` + `properties {cosine, rank, fetched_at}`.
- `apply_semantic_peer_edges()` : `DELETE ... WHERE edge_type='semantic_peer' AND source_ref LIKE 'embeddings:%'` + `INSERT OR IGNORE` (idempotent, prefix-scoped), dry-run counts fresh pairs.
- **Applied 2026-08-25 `--apply` : 7776 pairs (k=10, 1061 companies, sym-dedup, threshold 0.0, deterministic, `≤13k` band)**, `make graph-rebuild` → `e_semantic_peer=7776`, `database_integrity_check` 0 errors (exposed as `semantic_peer` in summary/validity).

Gate: E3 live dry-run + apply + rebuild + integrity clean.

**R4 — E4 implemented (2026-08-25): co-mention inference → pending sidecar DONE.**

Driver `helpers/maintenance/enrich_relations.py --source coinfer` :

- `build_coinfer_suggestions()` : per-company top-N co-mention neighbours (`co_mentioned_in` weight), sector boost `1.5×` same-sector else `1.0`, existing business-edge penalty `0` if any typed edge exists, threshold filter, deterministic sort by score desc.
- `coinfer_to_row()` : Unresolved JSONL `{"edge_type":"suggested","source":s,"target_mention":t,"quote":"","edition":"coinfer/<date>","origin":"coinfer","score":<float>,"method":"co_mention"}` + `edge_type_hint` compatible.
- `append_coinfer_suggestions()` : idempotent vs `graph_edges` (any type) + prior `origin=coinfer` sidecar rows; `--per-company` (default 3) caps per-company, `--threshold` filters, **no graph writes** (verified).
- **Dry-run 2026-08-25: 478 candidates (42 at threshold 1.4 same-sector only), per_company 1→169, 2→329**, `graph_edges 16302` unchanged, `findata/_pending_relations.txt 82→560 (478 appended)`, second apply `0` appended, dry-run warm `0/478` fresh.

Gate: `tests/test_enrich_relations.py 65 passed, tests/test_suggest_relations.py 12 passed`, scorer unit checks, format/no-write assertions, sidecar `Jsonl` valid.

**R5 — E5 implemented + APPLIED (2026-08-25): holders → institution + invested_in DONE.**

Driver `helpers/maintenance/enrich_relations.py --source holders` :

- yfinance `Ticker.institutional_holders` DataFrame (Holder, Shares, Date Reported, % Out, Value) — US-only (AAPL ✓, .NS empty) as probed §4.1; `78` companies with holders, `206` distinct holder names.
- `institution` entities: `entity_type='institution'`, `file_path NULL`, `normalized_name` slug, `INSERT OR IGNORE` dedup (Sanofi company-holder edge kept as company source, handled via `v_node`/`v_institution` + `_KIND_TO_TABLE` mixed-source fix).
- `invested_in` edges: directed `institution → company`, `weight 3.0 if pct≥5% else 1.0`, `source_ref yfinance:holders:<date>`, `properties {pctHeld,shares,value,fetched_at}`, `valid_from=dateReported`, `DELETE ... LIKE 'yfinance:holders:%'` idempotent.
- `helpers/graph/query.py: EDGE_REGISTRY invested_in (e_invested, institution_name→company_name, InvestedIn)`, `v_institution` + `v_node IN (institution)` widened, `_SCHEMA_VERSION 12→13` (already for E3), `database_integrity_check.py` allowlist + noteless `institution` exemption, cache clean.
- **Applied 2026-08-25: 715 invested_in edges, 205 institutions (206 names, one normalized collision), weight 1.0=610 / 3.0=105, `valid_from` 715 populated.**

Gate: `holders --dry-run 715 candidates, 206 institutions` → `apply 715 inserted`, second apply `0 fresh`, `148` targeted tests pass, `integrity 0 errors`.

**R6 — E6 implemented (2026-08-25): API/UI surfacing DONE.**

- `helpers/graph/query.py`: `invested_in` already in `EDGE_REGISTRY` (E5) + `semantic_peer` (E3), `v_institution` + `_KIND_TO_TABLE` generalized, `e_invested=0→715` after E5, `e_semantic_peer=7776` after E3.
- `helpers/misc/database_integrity_check.py`: `semantic_peer, invested_in` allowlisted, noteless `institution` exempted.
- `app.py: _EDGE_SEMANTICS` entries `cited_in, semantic_peer (symmetric), invested_in (directed institution→company)`, cloud legend returns correct semantics/symmetric.
- `static/tokens.css: --edge-cited-in #F5F1E8, --edge-semantic-peer #8AD7C6, --edge-invested-in #E0A93E`
- `frontend/src/views/graph.ts: _EDGE_TOKENS` maps new types to CSS tokens; `frontend/types/api.ts: CompanyNeighbors` optional `semantic_peers?, invested_by?`.
- `e_all_und/e_dir` substrates auto-include new types via `EDGE_REGISTRY`.

Gate: `database_integrity_check 13/0`, `graph rebuild 16 e_*`, `tsc/build` pass, `85 api unit + 442 api/graph` pass, `/api/graph/cloud` returns `semantic_peer 7776`, `invested_in` semantics correct, `ruff` clean.

**R2 — E2 implemented + APPLIED (2026-08-24): yfinance KNN pass, ticker hygiene, persistent fetch cache DONE.**

Driver `helpers/maintenance/enrich_relations.py` (`make relations-enrich ARGS="..."`):

- `--source {prose,yfinance,...}` skeleton; non-yfinance sources exit pointing
  at their slice (E3/E4/E5/deferred). `--topology knn|clique`, `--k`,
  `--mutual` (reciprocity precision filter), `--apply` (default OFF),
  `--check-only` (hygiene alone), workers default 2.
- **mcap resolution fix**: company_metrics.market_capitalization covered only
  30 companies → original dry-run ranked alphabetically. Now priority is
  fresh `info['marketCap']` (883/883 coverage; INR→crore) > stored metric >
  frontmatter bucket tag (also fixed: `_cap` suffix stripping).
- **Persistent fetch cache** `memory/yf_relations_fetch_cache.json`
  (~6 MB; fetched_at-stamped; `--refresh-cache` / `--no-cache`). The 916-ticker
  sweep costs ~350s at workers=2; cache-warm runs are <1s.
- **Dead clique path retired** from `enrich_from_yfinance.py` including the
  misleading dry-run "Projected competes_with edges" block that produced the
  phantom ~5900 figure (§1 root cause).
- Ticker hygiene: 33 dead tickers reported in relations_report.txt incl.
  confirmed TATAMOTORS.NS (post-demerger) and PIEIL.NS.

APPLIED 2026-08-24 (`--mutual --apply`, user-approved): **competes_with
10 → 2,350** (yfinance:industry:2026-08-24 = 2,340 mutual-KNN pairs,
weights 0.4–1.0 by rank; prose-derived 10 preserved by prefix-scoped delete).
Within the §10 band. `make graph-rebuild` run; DuckDB e_competes = 2,350.
Spot-check of 20 random pairs pending user review (§10).

Gate: 55 targeted tests (21 new in tests/test_enrich_relations.py),
ruff C901/S/UP + ty clean.

**R1 — E1 implemented (2026-08-23): prose patterns v2 + diff-audit harness DONE.**

Changes (`helpers/graph/extract_relations.py`):

- **G2 stake family** (3 new patterns, two capture groups: pct + mention):
  "acquisition of N% stake in X" / "acquired|purchased|bought|picked up N%
  stake in X" → `acquired`; "holds/owns N% stake in X" → `subsidiary_of`
  REVERSE (X = subsidiary) at N≥50%, silently dropped below 50% (Tier C,
  precision-first). `properties.stake_pct` recorded on every hit.
- **step-down subsidiary**: qualifier added to the v1 `subsidiary of`
  pattern ("wholly owned step-down subsidiary of X", bare step-down form).
- **Group patterns v2** (`GROUP_RES`, 5 patterns): promoter group, flagship
  of Group, Group flagship, "<G> Group company" — feeding existing
  `same_group` clustering; new `_normalize_group_name()` also strips leading
  articles ("A Mahindra Group company" keys as "Mahindra").
- **Resolver ambiguity logging (Tier C)**: `EntityResolver._fuzzy` now
  records N-way score ties in `ambiguous_log` (resolution unchanged);
  `_extract_batch` returns per-file ambiguities; CLI prints
  `~ ambiguous resolve:` lines + total.
- **Diff-audit harness**: `--counts-json PATH` writes per-type totals;
  new `helpers/misc/relation_diff_audit.py` prints a before/after delta
  table (`--fail-on-regression` guards v1 coverage).

Measured (108 files, dry-run): extracted 85 → 86 (+1: `Titan → Damas`,
stake_pct=67); no v1 regressions; 2 ambiguity ties surfaced ('D.B. Corp' ↔
D B Corp/D B Realty). Live phrase population is genuinely sparse (4 stake
mentions, 2 step-down, ~8 group phrases corpus-wide) — most targets
unresolvable or single-member groups, correctly routed to sidecar/no-op.
Gate: 248 targeted tests pass incl. 18 new (`tests/test_extract_relations_v2.py`),
ReDoS fuzz clean over all PATTERNS, `make types` + `make lint` + C901 clean.
NOT applied to graph_edges yet — run with `--apply` when ready.

**R0 — Review fold (2026-08-22): PASSED with two user directives.**
(i) All external lookups resolve **ticker-first** off the note-frontmatter
yfinance tickers (.NS/.BO), fuzzy-name fallback only for the 149
ticker-less companies, every fallback logged — folded into §6.0.
(ii) LLM-assisted extraction confirmed out of scope for now (§12.5).
Wikidata deferred (§12.4). Q1–Q3 proceed on stated recommendations
(K=8+flag; semantic_peer stored w=0.5; institution entity_type).
Measured during fold: 1,065 companies / 916 tickered; yfinance holder
tables empty for .NS probes, AAPL populated (US-only ceiling stands);
TATAMOTORS.NS 404s post-demerger. Green light for E1 on review.
