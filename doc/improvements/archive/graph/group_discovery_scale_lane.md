---
title: "Group-discovery scale lane: component inventory for curated family seeds"
status: executed
filed: "2026-11-12"
executed: "2026-11-12"
completed_md: "514"
area: "curated group discovery (helpers/maintenance/group_discovery_inventory.py; family_seeds.json sink)"
---

# Group-discovery scale lane: component inventory for curated family seeds

**Date:** 2026-11-12 · **Status:** EXECUTED (2026-11-12, completed.md #514) ·
**Area:** helpers/maintenance/group_discovery_inventory.py; family_seeds.json
(v5 → v6); eval-gate pins

## 1. Motivation

The `same_group` dyad store holds **24,766 company-company edges over 13,233
companies** — the richest raw group signal in the repo — but it is unusable
as-is: one filer label piles hundreds of edges under a single `vigil:rpt:*`
source_ref, names collide across groups, and multi-national webs blur
ownership. The curated sink (`family_seeds.json` v5, 14 families / 351
members) is clean but covers < 3% of the store. There is currently **no
repeatable path from raw dyads to curated groups** — each family was found
by hand via the name-token lens (#510). This lane provides that path: a
read-only **component inventory + triage report** that a curator can
adjudicate at the component level, growing `family_seeds.json` as the corpus
grows.

## 2. Evidence (measured 2026-11-12, live store)

Weakly-connected components of the company-company `same_group` graph:

| measure | value |
|---|---|
| components | 657 (13,233 companies) |
| size distribution | 2:189, 3:81, 4:54, 6:34, 13:18, big tail |
| largest components | 1,082 (TCS+TMPV refs; flags TVS,Tata), 854 (JSWENERGY+BHARTIARTL; flags Aditya Birla,JSW), 798 (TECHM+M&M), 612 (ADANIGREEN+ADANIPORTS), 469 (RELIANCE+VEDL) |
| dominant refs per component | single filer (e.g. HCLTECH 784/130, DLF 555/213, MOTHERSON 298/171) |
| clean single-ref components | HCLTECH (130), DLF (213, Real_Estate), MOTHERSON (171, Automotive), HONAUT (159), SCHNEIDER (155), ABB (122), ENTERO (106), PRESTIGE (102) … |
| cross-group merge flags | components carrying ≥2 distinct curated families: e.g. #1 (TVS,Tata), #2 (Aditya Birla,JSW) → adjudicate, not trust |
| seed coverage | 226 family-hit members in #1, 62 in #4, 8 in #2; rest 0 |

The inventory's `top_refs` column shows exactly *which* filer disclosures
feed each component — the triage lever for a curator (trust single-ref,
clean-sector components first).

## 3. Design

S1. `helpers/maintenance/group_discovery_inventory.py` — read-only CLI
   (implemented, tested 3/3): company-only same_group components, per-
   component profile (size, internal edges, density, top refs, sector
   diversity, family-seed hits, merge flags), ranked markdown + JSON.
   No store writes.
S2. Curated sink: adjudicate components (clean single-ref first) → append
   accepted families to `family_seeds.json` v6 (validate_family_seeds
   drops unresolvable; additive `INSERT OR IGNORE` apply).
   **IMPLEMENTED 2026-11-12 (v7)** — blob-decomposition second apply:
   +9 families from cross-label core extraction (members in ≥2 RPT labels
   of a cluster ∪ name-token matches within cluster labels): Mahindra 64,
   Bharti 29, Vedanta 18, Reliance 67, SP Group 43, Torrent 48, Max 19,
   Jubilant 41, Leela 5. Extended: Aditya Birla 4→68, JSW 5→37
   (+JSW Steel-filer arm). validate_family_seeds: 39 families / 2,439
   members / 0 dropped. `derive --apply` S6: 39 families widen `group`.
   **IMPLEMENTED 2026-11-12** — family_seeds.json v5 → v6: +16 families
   (DLF 213, HCLTECH 130, MOTHERSON 179, EMBDL 187, GVT&D 110,
   ENTERO 106, PRESTIGE 102, APOLLOHOSP 90, MANKIND 73, ASHOKA 83,
   MPHASIS 71, REDINGTON 78, HGINFRA 65, MASTEK 59, HINDUNILVR 61,
   COLPAL 51) = the first-wave clean single-filer components (ref share
   ≥0.90, ≤2 sectors, no merge flags); members = company-only sorted
   canonical names from the dominant vigil:rpt: ref. validate_family_seeds:
   30 families / 2,009 members / 0 dropped. `derive --apply` S6: 30
   families widen `group` (2,009 members).
S3. Gate pins: one `hyper-family-*` pin per accepted family (expected =
   sorted members), `--expect-improve` + `--expect-shrink` declarations.
   **IMPLEMENTED 2026-11-12** — ontology_questions.json v15 → v16 (207 q,
   +16 `hyperedge_members` pins hyper-family-dlf…colpal, id=label, params
   {edge_type group, label family:<Name>}, expected = sorted seed members).
   Gate: **ACCEPT — 207 questions, 0 reason(s)** (--expect-improve on the
   16 pins, parent = 2026-11-12 18:49 post-semantic-apply snapshot; report
   gate_accept_v6_report.json).
   **SECOND APPLY 2026-11-12** — v16 → v18 (217 q): +9 v7 pins (Mahindra/
   Bharti/Vedanta/Reliance/SP Group/Torrent/Max/Jubilant/Leela) + added
   hyper-family-aditya-birla + refreshed hyper-family-jsw expected (v7
   extension). Gate: **ACCEPT — 217 questions, 0 reason(s)** on parent
   research_parent_v6.db (fresh 21:45 snapshot, post-v6 pre-v7), all 11
   new/extended pins declared `--expect-improve` (report
   gate_accept_v7_report.json).
S4. Optional corpus-growth monitor: re-run inventory on the snapshot;
   component-size/ref delta report (drift signal as the corpus grows).
   **IMPLEMENTED 2026-11-12** — `helpers/maintenance/group_discovery_growth_monitor.py`
   (read-only, advisory; components identified by filer-ref signature,
   not size): diff two inventory `--json` reports → component count,
   sampled coverage, family-hit delta, new/gone components, merge-flag
   additions, size-band histogram delta. First run (report.json pre-seed
   vs report.current.json post-v7): coverage 3,120 → 5,037 sampled;
   family hits +2,088; 9 new merge flags (Bharti/Jubilant/Leela/Mahindra/
   Max/Reliance/SP Group/Torrent/Vedanta); components 657 → 657 (dense
   same_group structure stable — no new filer label blobs appeared).
S5. **Hyperedge co-occurrence lens** — `helpers/maintenance/hyperedge_cooccurrence_lens.py`
   (read-only): pairwise label-overlap per edge type (overlap + Jaccard),
   ranked; bridge-member report (entities appearing in the most labels);
   `--types`/`--limit`/`--min-overlap`/`--json`. Closes the inspection
   gap between the dyad-component view and the hypergraph: group-type top
   overlaps recapitulate the family structure (family:DLF × vigil:rpt:DLF
   213/1.0; family:Tata × TCS 136; TCS × VOLTAS 81), and the index/subsidiary/
   supply types expose their own latent structures (NIFTY 500 × Nifty500
   Multicap 499/1.0; H.G. Green Energy × H.G. Infra 83; Wipro × Wipro
   Travel Services 78).
   **IMPLEMENTED 2026-11-12** — helper + 5 tests; reports at
   bench_data/hyperedge_cooccurrence/{group,event,mixed}.json. Measured:
   group 640 labels / 13,243 members, top bridges Tata Communications (15),
   Indian Hotels (14), Tata AIG (14), Tata Capital (14); event 1,123 labels
   (avg 2–4 members), hubs Indian Hotels (90), Patel Engineering (42),
   Reliance Industries (35); index 57 labels, NIFTY 500 ≡ Nifty500 Multicap
   50-25-25 (499/1.0); subsidiary 1,464 labels, hubs Motherson Technology
   Services (93), Wipro (69); supply 687 labels, top ACC × Ambuja Cements
   37/0.57, bridges Tata Communications (105), Voltas (92). The cross-label
   overlaps match the dyad-component analysis verbatim (TCS × VOLTAS 81 =
   the identical number from the dyad bridge table), confirming the insight
   was latent in the hypergraph all along — the lens merely renders it.

## 4. Blob triage findings (2026-11-12, recorded for future analysis)

The 5 largest dyad components were decomposed into label-clusters (union-find
over shared-member overlap ≥ 10). Full JSON: bench_data/group_discovery_inventory/
blob_split_analysis.json + blob_split_clusters.json. Result summary:

| blob | size | clusters | verdicts |
|---|---|---|---|
| #1 Tata | 1,081 | 11 | C1 = Tata super-group (678, 15 RPT labels) — already seeded v5 family:Tata (230); MNC arms standalone: Bosch 129, Bosch Home Comfort 45, Wipro 46, Hitachi Energy 134; PSU: SBI Life/Cards/IN 33/16/11; singletons: Trent 9 (belongs C1), Garfibrès 11, Balram Chini 2 |
| #2 Mahindra | 798 | 37 | **Mahindra family 324** (M&M/M&MFIN/MAHLIFE/TECHM); **Torrent 52+10** (Power+Pharma); **Max 18+15** (MFSL+MAXESTATES); Maruti-Suzuki 47 (foreign promoter — optional); 19 PSU singletons (NHPC/NTPC/rail/ONGC…); MNC arms CRISIL 47, CIE 46 |
| #3 JSW+Bharti | 780 | 16 | **Bharti 166** (Airtel/Hexacom/Indus Towers); **Aditya Birla ~271** (Grasim/UltraTech/Hindalco/ABC; IDEA label mis-glued — excluded); **Jubilant 41** (Pharma+Ingrevia); JSW Energy 244 already seeded; Dr Reddy's/Dixon/Dalmia singletons |
| #4 Adani | 607 | 9 | Adani core (Green+Energy+Ports+Ambuja+Power) already seeded 66; **excluded**: RECLTD 56 (PSU finance SPVs), Wilmar JV (AWL 29, Renuka 10), ORIENTCEM 3 already in Adani |
| #5 Reliance | 469 | 9 | **Reliance 281** (RIL/Jio/Retail/JioFin/Network18); **Vedanta 100** (VEDL+STLTECH+HINDZINC); **SP Group 69** (Afcons+Sterling&Wilson); Leela 33; Alok/GSFC singletons |

Verdict classes: FAMILY (seed), MERGED (already seeded/merge), PSU/STATE (never seed),
JV (foreign partner — never), MNC-ARM (foreign parent — optional), SINGLETON (noise).

## 5. Acceptance criteria & shakedown

1. **Eval-gate bullet**: ACCEPT, 0 undeclared changes (v6 seeds declared).
   **VERIFIED 2026-11-12** — ACCEPT, 207 questions, 0 reasons (16 new pins
   declared improve).
2. Inventory read-only verified (row counts unchanged across runs).
3. ≥ 10 candidate families from clean single-ref components available for
   first adjudication pass.
   **VERIFIED 2026-11-12** — 16 first-wave families applied (2nd-wave MNC
   arms + blob splits pending).
4. `validate_family_seeds` pass; 0 dropped for accepted families.
   **VERIFIED 2026-11-12** — 0 dropped across all 30 families (2,009
   members).
5. Tests: 3/3 inventory unit tests; integration via validate chain.
   **VERIFIED 2026-11-12** — 5/5 co-occurrence lens tests + 3/3 inventory
   tests + 4/4 growth-monitor tests pass; ruff/py_compile clean.

## 5. Risks

- **Cross-group blobs**: components #1/#2/#5 are merged messes (multiple
  filer refs). Adjudication must split, not trust the whole component —
  that is the triage report's job.
- **Noise refs**: some `vigil:rpt:*` groups are person/filer artifacts;
  the company-only filter plus sector/seed cross-checks mitigate.
- **Coverage ceiling**: 13,233 companies are in components, but curated
  seeds stay human-adjudicated by design (auto-apply veto precedent #507).

## 6. Non-goals

- No automatic merging of components into families (the #507 3.2×
  inflation veto stands).
- No changes to the embedding lens (`semantic_cluster_label_metric.md`
  stays a sector-anchored coherence instrument, family-agnostic).
- No new store tables; the inventory consumes existing `graph_edges` /
  `entities` / `family_seeds.json`.
