---
title: "MNC arm lane — mnc:<parent> group hyperedges from foreign-parent filer labels"
status: executed
filed: 2026-11-13
executed: 2026-11-13
completed_md: "517"
area: graph
---

# MNC arm lane — `mnc:<parent>` group hyperedges from foreign-parent filer labels

## 1. Motivation

The `same_group` dyad store carries related-party-disclosure filer labels
(`vigil:rpt:<SYMBOL>`). Many filers are **arms of foreign multinationals** —
Indian-listed subsidiaries whose RPT disclosures enumerate their global parent
networks (ABB India ↔ ABB Switzerland, Siemens India ↔ Siemens Ukraine,
Hitachi Energy India ↔ Hitachi China/Spain, …). The v6/v7 family-seed triage
excluded these as "MNC-ARM — foreign parent cluster, decide if we want them":
they are not Indian promoter families, so they never entered `family_seeds.json`
(except two mis-seeded cases, §5).

The MNC grouping is independently valuable. These companies run to their
international parents' governance/accounting standards, and the market prices
an MNC premium into them. A dedicated, stable **`mnc:<parent>`** group label
makes that structure queryable and gate-able, without polluting the
Indian-promoter family lane.

## 2. Evidence — S1 catalog (implemented 2026-11-13)

Read-only pass `helpers/maintenance/mnc_dyad_pass.py` (tests 5/5) pulls the
company-only members of each known foreign-parent filer label (same membership
rule as the family-seed lane) and profiles them. Report + manifest:
`bench_data/mnc_dyad_pass/report.json`.

25 of 29 curated MNC filer symbols matched (later 24 after CIE reclassification); 4 absent from the store
(`ZFCV`, `ASTRAZENEC`, `NOVARTIS`, `ABBOTINDIA`). Top arms:

| symbol | parent | country | size | dyads | density | sub-of |
|---|---|---|---|---|---|---|
| SIEMENS | Siemens AG | DE | 232 | 322 | 0.012 | 1 |
| HONAUT | Honeywell Automation | US | 159 | 158 | 0.013 | 0 |
| SCHNEIDER | Schneider Electric | FR | 155 | 154 | 0.013 | 1 |
| POWERINDIA | Hitachi Energy | JP | 134 | 134 | 0.015 | 0 |
| BOSCHLTD | Robert Bosch GmbH | DE | 129 | 128 | 0.016 | 1 |
| ABB | ABB Ltd | CH | 122 | 121 | 0.016 | 0 |
| BASF | BASF SE | DE | 86 | 102 | 0.028 | 0 |
| CUMMINSIND | Cummins Inc | US | 76 | 75 | 0.026 | 3 |
| KSB | KSB SE | DE | 75 | 74 | 0.027 | 0 |
| SCHAEFFLER | Schaeffler AG | DE | 73 | 72 | 0.027 | 0 |
| AKZOINDIA | AkzoNobel | NL | 67 | 68 | 0.031 | 0 |
| LINDEINDIA | Linde plc | US | 65 | 64 | 0.031 | 0 |
| CIEINDIA | CIE Automotive | ES | 59 | 59 | 0.034 | 0 |
| 3MINDIA | 3M | US | 59 | 58 | 0.034 | 0 |
| HINDUNILVR | Unilever plc | UK | 61 | 79 | 0.043 | 7 |
| COLPAL | Colgate-Palmolive | US | 51 | 50 | 0.039 | 2 |
| CRISIL | S&P Global | US | 47 | 67 | 0.062 | 24 |
| MARUTI | Suzuki Motor Corp | JP | 47 | 57 | 0.053 | 6 |
| BOSCH-HCIL | Bosch Home Comfort | DE | 45 | 74 | 0.075 | 0 |
| TIMKEN | The Timken Company | US | 38 | 36 | 0.051 | 1 |
| SANOFI | Sanofi | FR | 23 | 22 | 0.087 | 2 |
| GLAXO | GSK plc | UK | 16 | 15 | 0.125 | 0 |
| NESTLEIND | Nestlé | CH | 14 | 13 | 0.143 | 0 |
| PFIZER | Pfizer Inc | US | 7 | 6 | 0.286 | 0 |

## 3. Plan

- **S1 (done)** — catalog + manifest (`mnc_dyad_pass.py`).
- **S2 (implemented 2026-11-13)** — `helpers/misc/mnc_seeds.json` v1 (25
  families / 1,919 members) from the manifest; `derive_hyperedges.py` gained
  `collect_mnc_seeds()` / `_collect_seed_lane()` (shared loader with the
  family lane, `mnc:` prefix), S12 apply block, and group-compaction skip for
  `mnc:` labels. Validate resolved 0 dropped; live apply → 25 `mnc:`
  hyperedges / 1,919 incidences.
- **S3 (implemented 2026-11-13)** — `ontology_questions.json` v18→v19: +25
  `hyper-mnc-*` pins + `hyper-family-tata` refreshed 230→238. Gate ACCEPT —
  242 questions, 0 reasons (report gate_accept_mnc_v8_report.json).
- **S4** — growth-monitor: components now also carry MNC family hits; monitor
  stays advisory (no gate change needed for this lane).

## 4. Acceptance

1. ✅ `mnc_seeds.json` 25 families → validate 0 dropped → 25 `mnc:` hyperedges
   in store (1,919 incidences).
2. ✅ All 25 `hyper-mnc-*` pins match expected exactly on the live store.
3. ✅ Gate ACCEPT — 242 questions, 0 reasons (post-apply snapshot).
4. ✅ `family:` lane untouched; `hyper-family-tata` refreshed to v8 (238).
5. ✅ Tests: seed-loader prefix handling (7/7 `test_mnc_seeds_lane.py`),
   `run_pass` (5/5 `test_mnc_dyad_pass.py`); related suites 69 passed.

## 5. Open adjudications (not blocking)

- `HINDUNILVR` / `COLPAL` were seeded as family seeds (v6). **Relabeled
  2026-11-13**: removed from `family_seeds.json` (v8→v9, now 37 families) and
  covered by `mnc:Unilever plc` (61) / `mnc:Colgate-Palmolive` (51);
  `hyper-family-hindunilvr/colpal` pins dropped (questions v19→v20). S2 prune
  removed the 2 stale family hyperedges; gate ACCEPT — 240 questions, 0 reasons.
- `CIEINDIA` carried 2 Mahindra family hits (MAHINDRA ACCELO, MAHINDRA
  DEFENCE SYSTEMS). **Resolved 2026-11-13 by operator**: CIE Automotive India
  reclassified into `family:Mahindra` (65 members); `mnc:CIE Automotive`
  removed (24 mnc seeds remain; mnc v2, family v10, questions v21, gate ACCEPT
  239/0).
- Members include foreign global-network entities (Siemens Ukraine, Hitachi
  China…) — kept as labels (the MNC network IS the disclosure), but the
  "Indian companies only" view should filter on `entity_type='company'` +
  exchange/CIN presence at query time.

## 6. Risks

- Label namespace growth (+25) raises group-hyperedge count (~618 → ~643);
  network queries must scope to `mnc:` when counting MNC arms.
- Foreign-member inclusion blurs `company`-only analysis unless filtered.
- Date-consistency: manifest drawn from current store; members may drift on
  next refresh — growth monitor catches size changes.

## 7. Non-goals

- No auto-merge of MNC arms into family seeds (#507 veto).
- No store schema change; no new tables.
- No change to `family_seeds.json` unless the §5 relabel is approved.
