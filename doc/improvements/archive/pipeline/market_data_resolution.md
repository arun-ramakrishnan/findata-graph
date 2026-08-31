---
title: Combined market-data resolution — FinnHub search → yfinance bulk → GF fallback
status: executed
filed: '2026-08-25'
executed: '2026-08-25'
completed_md: '152'
area: pipeline
---

# Proposal: Combined market-data resolution — FinnHub search → yfinance bulk → GF fallback

**Date**: 2026-08-25
**Status**: EXECUTED & CLOSED (2026-08-25). S1–S4 landed same day; S2
apply: 11 ticker writebacks incl. TMPV.NS; S3 live apply executed by the
user (entity_gf_map seeded — Chemcart gf_only with 6 Sheets-metric rows,
Swastika Castal yahoo_mapped_back); finnhub `--apply` landed Kinetic
Engg → 500240.BO (multi-probe discovery). Piramal resolved by user
decision as a full entity merge into `Piramal Finance` / PIRAMALFIN.NS
(Yahoo's degenerate payloads for PEL.NS made the writeback path moot).
Resolution-pass hardening (R1 below) shipped the same day. User ran all
manual steps incl. qa green. Archived to archive/pipeline/.

**R1 — resolution-pass hardening (2026-08-25): DONE.** Three guards +
one discovery upgrade, all live-verified:

- **FinnHub multi-probe** (`fh_search_multi`, new default lookup):
  progressive queries full→first-two→first-word, per-query cached,
  politeness-paced; stops at first non-empty. Live win: Kinetic Engg
  `no-candidates → writeback-candidate` (KINETIC.NS → 500240.BO, 0.77);
  Bajaj Allianz GI / Globus Agronics moved to `unverified` for curation.
- **Ticker-ownership guard**: a candidate another entity ALREADY owns is
  never eligible — kills the measured Kotak trap ('Kotak Mahindra Life
  Insurance' fuzzy-scored 0.71 against the BANK's payload and would have
  taken KOTAKBANK.NS). Now `no-candidates`.
- **Stale-target filter**: report rows whose DB ticker already differs
  are skipped with a logged list ("resolution happens ONCE") — live:
  targets 33 → 17 (12 prior S2 writebacks + 4 hand-fixed tickers:
  BAJAJFINSV/HYUNDAI/LINC/PICCADIL).
- **Terminal classifications** (§5 classes): `entity_ticker_status`
  table + `--classify ENTITY {delisted|amalgamated} [SUCCESSOR]` /
  `--unclassify` (the only writers); both passes skip classified
  entities and append a `[terminal]` report section.
- **Piramal root cause closed**: not a query problem — FinnHub finds
  PEL.NS and the name check would pass; Yahoo itself serves degenerate
  1-key payloads for PEL.NS/PEL.BO right now. Self-heals via the
  success-only verify cache on a later sweep; no code path can conjure
  it earlier.

Gate: 152 targeted tests (12 new), ruff + ty clean, live dry-runs for
both passes.
**Builds on**: `google_finance_ticker_fallback.md` (F1–F3 EXECUTED
2026-08-25; its remaining F4 slice is absorbed here as S3) and the
yfinance pass in `enrich_relations.py` (E2).
**User doctrine (2026-08-25, binding)**: **yfinance is the ONLY source
allowed for bulk market-data queries**, and even it must lean on
caching to the largest extent possible. Every other source is
failure-path-only, per-target, cached permanently.

---

## 1. Problem statement

The corpus's 916 tickered companies are served by yfinance except for a
persistent failure residue (33 dead/stale tickers measured 2026-08-24,
plus 147 deliberately-unlisted entities). Three disjoint tool gaps
caused this: no name→ticker discovery for renamed/demerged companies,
no data source for Yahoo-dead-but-actually-listed companies (Srigee
class), and no disciplined way to turn a discovered listing into
permanent yfinance coverage. This proposal combines the session's
capability probes into one resolution pipeline with a single bulk-safe
data doctrine.

## 2. Measured capability findings (all probed live 2026-08-25)

| Source | Free-tier capability | Verdict / role |
|---|---|---|
| **yfinance** | full .info for ~883/916; bulk-safe; we control rate via workers=2 + persistent fetch cache | **primary + only bulk source** |
| **FinnHub** `symbol_lookup` | name → Yahoo-format ticker, India included: `Srigee`→`544399.BO`, `Tata Motors`→`TMPV.NS`+`TMCV.NS`, `Piramal Enterprises`→`PEL.NS` (fixes our failing `PIEIL.NS`), `Hanesbrands`→`HBI`. Indian quotes/profiles/symbol-masters = premium 403; search q ≤ ~20 chars (422 otherwise); name noise exists ('Gati' → navigation cos); delisted tickers return truthful all-zero quotes (HBI is private post-Gildan). Note (user, 2026-08-25): `Akzo Nobel` returning only the Dutch parent `AKZA.AS` is NOT a gap — Akzo Nobel India was amalgamated into JSW Paints, so the Indian listing no longer exists; such entities need an `amalgamated` classification with curated successor, not resolution | **discovery source #1** — candidates go straight into yfinance verification |
| **BSE PeerSmartSearch** (F3, live) | name → scrip + NSE symbol + ISIN + listed name; carries the G4 symbol (TMPV row); substring noise (JAIN IRRIGATION for 'gati') | **discovery source #2** — needed when the GF slug itself must be found (BSE-scrip-only listings) |
| **NSE official API** | Akamai-hard-blocked from this network (403 + bot-manager on warmup) | rejected; BSE rows carry NSE symbols anyway |
| **GF web beta pages** (F1/F2, live) | parseable stats; dead slugs = HTTP 200 shells (content-verified); tier-1 stem-preserving swaps yield 1/33 (Chemcart `544442:BOM`); numeric BOM slugs served | **fallback resolver + no-credential metrics** |
| **GOOGLEFINANCE via Sheets + gspread** (service account, live) | batch write-formulas-read-values PROVEN: `BOM:544399` → price 84 / mcap 501,782,400 / pe 7.3 — matches the GF web parser to the decimal; eval instant; one write + one read per whole batch (2 API calls); API-key route is read-only (401 categorical); `raw=False` required; historical data blocked via API; no name attribute | **fallback metrics path** (gf_only companies) |
| **FinnHub premium, Screener.in, web-search scraping** | Indian quotes behind paywall / ToS gray zone / consent-walled | not adopted |

Credentials (all gitignored `memory/`): `goog_svc_account.json`
(service account, 'Search Test' sheet shared with it),
`FINNHUB_API_KEY` in `memory/.env`. Libraries venv-only until their slice lands:
gspread 6.2.1, finnhub-python 2.4.29.

## 3. The combined pipeline

Per sweep, in order — each stage only sees the previous stage's residue:

```
0. yfinance BULK sweep (existing pass, cache-first)
     ├─ OK (~883) → done; fetch cache persisted
     └─ failures (33) + opt-in unlisted (≤147) ↓
1. FINNHUB DISCOVERY (per target, cached)
     symbol_lookup(name) → Yahoo-format candidates
     (q trimmed to 20 chars; ≥1s politeness; permanent query cache)
     ├─ candidate ≠ stored ticker → SINGLE-ticker yfinance re-check
     │    ├─ works → G4 WRITEBACK: entities.ticker + frontmatter +
     │    │         fetch-cache extension (candidate enters the next
     │    │         bulk sweep — resolution is permanent)
     │    └─ still fails → fall through
     └─ no candidate → fall through        ↓
2. GF RESOLUTION (F2/F3 machinery: curated → tier-1 swaps → BSE search)
     verified slug → kind: yahoo_mapped_back (re-enter stage 1
     verification next sweep) or gf_only ↓
3. GF METRICS (gf_only only): one Sheets batch — formulas in,
     computed values out (2 API calls/sweep, not per company)
     → company_metrics rows, source_ref='googlefinance:<slug>:<metric>'
```

### Doctrine (binding)

- **Bulk = yfinance only.** Every other network source touches only the
  failure residue (bounded ~40–180 targets) one query at a time.
  Sheets is exempt per-batch (2 calls/sweep total) because the batch
  IS the unit.
- **Cache everything, permanently** (all under gitignored `memory/`):
  yfinance fetch cache `yf_relations_fetch_cache.json` (exists — extend,
  never blindly refresh), GF pages `gf_page_cache/` (exists), BSE search
  texts (exist), FinnHub query results (new, S1), entity_gf_map rows in
  research.db (resolution happens ONCE per company). A repeat sweep
  over unchanged inputs must make near-zero network calls outside the
  yfinance bulk leg.
- **Verification is name-based and never auto-bypassed**: FinnHub/BSE
  candidates are symbol strings, not identities — a candidate is only
  trusted after the yfinance single-fetch (stage 1) or the GF
  About-name fuzzy match ≥0.6 (stage 2) passes. Misses land in the
  report for curation, never auto-applied.

## 4. Implementation slices

| Slice | Content | Gate |
|---|---|---|
| S1 ✅ | `helpers/maintenance/finnhub_search.py`: raw-urllib `/search` client (q-length guard, per-query cache, token from `FINNHUB_API_KEY` in `memory/.env`, loud-but-non-fatal 403/422) + 4 live fixtures | pytest + live probe ✓ |
| S2 ✅ | Stage-1 wiring in `enrich_relations.py`: FinnHub candidates → single-ticker yfinance verify (longName fuzzy check) → dry-run writeback table → `--apply` writes entities.ticker + frontmatter + fetch-cache extension. **Live 2026-08-25: 11 writebacks applied** (TMPV.NS, AADHARHFC.NS, 543544.BO, 543997.BO, KMEW.NS, LRRPL.NS, MDL.NS, TBI.NS, VGL.NS, APOLLO.NS, ATLANTAELE.NS); Akzo guard held (AKZA.AS never reached verify); ABS/Trident degenerate yfinance payloads rejected by the name check | pytest + live dry-run + apply ✓ |
| S3 ✅ | GF `--apply`: `googlesheets_metrics.py` batch (lazy gspread, raw=False, 2 calls/sweep) + `entity_gf_map` persistence + company_metrics rows (marketcap converted INR→crore, delete-by-prefix idempotent) + GF-page header-price extraction (`parse_price`) | pytest + parity ✓ |
| S4 ✅ | Success-only verify cache (`memory/fh_verify_cache.json`, failures retry) + budgets documented in the driver docstring. Warm re-sweep measured: GF+tier2 2.1s, finnhub 4.4s, zero non-yfinance network | timing run ✓ |

S2 ordering note: FinnHub runs BEFORE GF tiers because its candidates
resolve against yfinance directly (no scraping, permanent fix); BSE/GF
remain the path for scrip-only listings yfinance can never serve.

## 5. Risks

- **FinnHub free-tier drift** (limits, India symbol coverage): thin
  client + per-query cache caps blast radius; BSE/GF paths unaffected.
- **Wrong-entity writeback**: stage-1 verify is a yfinance info fetch —
  an info payload for the WRONG company passes; mitigated by name check
  of yfinance `shortName`/`longName` against the entity name (same
  fuzzy matcher) before writing. Tier-C discipline still applies.
- **Parent/subsidiary name collision** (the Akzo Nobel case): FinnHub
  returns the Dutch parent `AKZA.AS` for 'Akzo Nobel', and
  `name_match_score("Akzo Nobel NV", "Akzo Nobel India")` scores HIGH —
  the fuzzy name check alone cannot separate parent from subsidiary.
  Guard: stage-1 candidates must match the entity's exchange class
  (`.NS`/`.BO` for India-domiciled entities, exact-suffix or bare-US
  otherwise); cross-exchange candidates are reported, never written.
- **Amalgamated/delisted entities are terminal classes, not failures**:
  Akzo Nobel India (→ JSW Paints) and HBI (private, post-Gildan) will
  never resolve; the report must classify them and stop re-probing
  (curated successor mapping for amalgamations).
- **Sheets quota/drift**: 2 calls/sweep is negligible; formula
  attribute set is documented surface; eval lag handled by one retry.
- **Token hygiene**: keys live only in gitignored `memory/`; never in
  code, reports, or commits; `.env`-style loaders strip `export `.

## 6. Success criteria

- `Piramal Enterprises` ticker corrected PIEIL.NS → PEL.NS and picked
  up by the next bulk yfinance sweep (cache extension verified).
- Srigee DLM: resolved `544399:BOM`, metrics in company_metrics with
  googlefinance provenance matching the web parser values exactly.
- ≥50% of the 33 ticker_issues permanently resolved (writeback or
  gf_only metrics), remainder explicitly classified — carried over
  from the GF proposal §7.
- A second, immediately repeated resolution run makes ~zero non-yfinance
  network calls (all caches warm) and writes nothing new.

## 7. Open questions

1. Unlisted opt-in sweep cadence — with FinnHub+writeback, do the 147
   unlisted entities get a quarterly one-shot sweep? (default: manual,
   `--include-unlisted` as today)
2. Sheets worksheet lifecycle — one growing workbook vs per-sweep
   worksheets (default: single 'Search Test'-style scratch sheet,
   rewritten per sweep; the DB is the record, not the sheet)
3. Corporate-action classifications in the report — `delisted` (HBI,
   private post-Gildan) and `amalgamated` with a curated successor field
   (Akzo Nobel India → JSW Paints, per user 2026-08-25) instead of a
   forever-`still-dead`? Terminal classes would also shrink future
   sweeps (stop re-probing the dead). Default: yes, in S2.
