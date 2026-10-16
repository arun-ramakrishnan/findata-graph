---
title: "Entity rename pass — quarterly rename convergence with tiered evidence, executing through rename_entity"
status: executed
filed: "2026-10-16"
executed: "2026-10-16"
completed_md: "405"
area: "helpers/maintenance"
---

# Entity rename pass

## 0. TL;RA

Every quarterly refresh mints two permanent tails: new note-less stubs
(exchange_sync D14) and — worse — companies that RENAMED, where BSE/Yahoo's
current name has drifted from our entity name. The ticker-hygiene lane
(S1 of `d14_stub_ticker_and_industry_convergence`, completed.md #404)
already refuses to write a symbol whose identity guard fails, and emits the
refusal as EVIDENCE (`## renames` rows: our name, scrip, current exchange
name). This proposal makes that evidence actionable: an operator-signed
rename queue executed through the existing `rename_entity.py` — which
already carries ticker/sector/`file_path` forward — after which the next
tier run resolves the renamed entity trivially, because the name now
matches what the exchanges publish.

## 1. The recurring defect

Measured 2026-10-16: of 4,159 scrip-code tickers, 3,678 resolved (88.4%).
The 481 tail splits into renames (~66 tier-1 refusals holding a live
best-match under a different name), ETFs/InvITs without a kite code, and
suspended/delisted scrips (for those, the scrip code IS the correct market
identifier). The tail regrows every quarter: stubs are minted quarterly and
exchange names drift continuously — without a pass it only grows.

## 2. Design

1. Evidence: `ticker_hygiene --fetch-only --retry-failed --tier3` emits
   `## renames` (tier3_rename) plus tier-2 refusals into
   `outputs/ticker_hygiene_report.md`.
2. Queue: a `--rename-report <path>` mode writes a standalone queue file —
   one row per rename: entity name, scrip, current exchange name, evidence
   source (tier + report block ref).
3. Execution (operator-gated): approved rows run through
   `rename_entity.py <old> <new>` — the hook point: it already updates
   `entities.name`/`normalized_name` and carries ticker/sector/`file_path`
   forward. The next `ticker_hygiene --apply-only --apply` converges the
   ticker on the renamed row (the identity guard passes because the name
   now matches).
4. Tier coverage: 3a direct-symbol probe from local `kite_tradingsymbol`
   (implemented); 3b GF fallback (`googlefinance.py` + curated
   `entity_gf_map`); 3c finnhub (`finnhub_search.py`).

## 3. Behaviour neutrality

The machinery only reads and reports; renames are operator-signed and flow
through the existing audited `rename_entity` contract. No eval gate — no
query-visible semantics change from the machinery itself; renames follow
the existing rename contract (rosters/hierarchies updated by that path).

## 4. Non-goals

- No auto-rename: identity is operator-owned (Tier-C — matches are
  evidence, never auto-applied).
- No ticker write on identity-guard refusal (d14 S1 contract).
- Suspended/delisted scrips stay scrip — flagged, not "fixed".

## 5. Cadence

Quarterly, after refresh-chain: the phase-A lane is already wired
(`--fetch-only`), phase B carries `--apply-only`; the rename-report step
rides on the same run.

## 6. Log

| date | event |
|---|---|
| 2026-10-16 | filed after the S1 hygiene pass surfaced the 481 tail (66 renames + 415 kite-less/absent) |
| 2026-10-16 | **First pass executed** (operator go): 47 rename candidates → **41 renamed** through `rename_entity` (store-convention names: corporate suffix stripped per the entities CHECK; `Kinetic Engg → Kinetic Engineering`…), 2 no-ops (exchange name == store name), 3 routed to the alias lane (1-char token mutations — `Chemkart`, `Raunaq lnternational`, `RNIT Al` — fix via `yf_name_aliases.json`, never a rename), 1 collision rejected by the UNIQUE constraint (`Grand Foundry` → Tikona Communication already exists = wrong-symbol match, not a rename). `rename_entity.py` fixed for fileless D14 stubs (`file_path` NULL stays NULL — no fabricated `findata/Companies/None/x.md`, no `Path / None` crash); 12 existing tests green. Post-rename tier run converged the renamed rows' tickers: **applied=40**, scrip tail 481 → **380** (115 dead + 258 no-kite + 5 review rows). Queue: `findata/Misc/_rename_queue_20261016.json`. |
