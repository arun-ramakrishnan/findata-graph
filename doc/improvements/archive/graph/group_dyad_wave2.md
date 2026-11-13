---
title: "Group-dyad wave 2 — PSU/state & JV catalogs, residual MNC arms, index-label hygiene"
status: executed
filed: 2026-11-13
executed: 2026-11-13
completed_md: "521"
area: graph
---

# Group-dyad wave 2 — PSU/state & JV catalogs, residual MNC arms, index-label hygiene

## 1. Motivation

The `same_group` dyad blobs were triaged 2026-11-12
(`bench_data/group_discovery_inventory/blob_split_{analysis,clusters}.json`,
findings recorded under completed.md #514 §4): every label-cluster in the 5
largest components received a verdict — FAMILY, MERGED, PSU/STATE, JV,
MNC-ARM, SINGLETON. Two lanes landed on that triage's back: the curated
family seeds (`helpers/misc/family_seeds.json` v10 — 37 families, including
the blob-split groups Aditya Birla 68, Bharti 29, Vedanta 18, SP Group 43,
Torrent 48, Max 19, Jubilant 41, Leela 5; gate ACCEPT 239/0) and the MNC arm
lane (`helpers/misc/mnc_seeds.json` v2 — 24 arms; completed.md #517/#518).

The remaining verdict classes have no artifact:

- **PSU/STATE clusters** (~283 members across 25 filer labels) — ruled
  "never seed" into the family lane, correct for family curation, but
  state-owned-enterprise identity is itself queryable information (SOE
  screen: central vs state, ministry/group, subsidiary texture).
- **JV clusters** — foreign-partner joint ventures (Wilmar: AWL 29 +
  RENUKA 10) ruled "never seed"; only 6 `jv_with` hyperedges exist
  (hgx star lane, opt-in family), none covering the dyad-triage JV
  verdicts.
- **4 curated MNC arms absent from the dyad store** (`ZFCV`,
  `ASTRAZENEC`, `NOVARTIS`, `ABBOTINDIA` — no `vigil:rpt` label
  observed; `mnc_arm_lane.md` §2). Alternate evidence sources were
  never probed for them.
- **One index-label duplication measured** (NIFTY 500 ≡ Nifty500
  Multicap, overlap 499/1.0) — hygiene note with no owner.

Each gets a small, mostly read-only slice. Nothing here changes curated
rosters by default; the only query-visible outcomes (`psu:` / `jv:` label
lanes) are explicit operator decision points, defaulting to catalog-only.

## 2. Evidence

**(a) PSU/STATE clusters** — company-only members of `vigil:rpt` labels
from `blob_split_clusters.json`:

- SBI trio (blob1): SBILIFE 33, SBICARD 16, SBIN 11 — 60 members.
- Blob2 PSU singletons: NHPC 37, NTPC 25, RVNL 24, RAILTEL 19, PFC 15,
  LICI 11, POWERGRID 11, RITES 11, IDBI 10, IREDA 10, NTPCGREEN 10,
  PTC 8, LICHSGFIN 7, NMDC 5, IRCTC 4, IRCON 3, NIACL 3, COALINDIA 2,
  CONCOR 2, NATIONALUM 2, NSLNISP 2, ONGC 2 — 223 members.
- Total ≈ 283 members / 25 labels. (Triage §4 counted the 19
  ≥10-member ones; the inventory JSON is authoritative and also holds
  borderline cases — e.g. GPPL 12 is a port JV — that S1 adjudicates
  per symbol.)

**(b) JV clusters** — AWL 29 + RENUKA 10 (Wilmar JV, blob4). JV-shaped
members also sit inside family seeds (e.g. SP Group's "Afcons - KPTL
Joint Venture"), so a name-pattern census can size the population.

**(c) Absent MNC arms** — `ZFCV`, `ASTRAZENEC`, `NOVARTIS`,
`ABBOTINDIA`: in the 29-parent catalog, zero same_group labels.

**(d) Index duplication** — NIFTY 500 × Nifty500 Multicap overlap
499/1.0 (`group_discovery_scale_lane.md` S5 measured;
`hgx_over_dyads.md` corroborates the label set).

## 3. Plan

- **S1 — PSU/state catalog (read-only).**
  `helpers/maintenance/psu_dyad_pass.py` mirroring
  `mnc_dyad_pass.py`: `PSU_PARENTS` table (symbol → ministry/group:
  SBIN→State Bank group; NTPC/NHPC/NTPCGREEN→Power PSUs;
  RVNL/IRCON/IRCTC/RITES→Railways; LICI/LICHSGFIN/NIACL→LIC/insurance;
  …), same membership rule as the family/MNC lane (company-only members
  of `vigil:rpt:<SYMBOL>`), profile (size, dyads, density,
  subsidiary_of share, sector mix, central-vs-state classification,
  borderline flags). Report + manifest at `bench_data/psu_dyad_pass/`.
  No seeds, no gate pins.
- **S2 — JV census (read-only).** Scan same_group labels + name
  patterns ("Joint Venture", JV abbreviations, partnership shapes) for
  JV-class entities; cross-ref AWL/RENUKA and the 6 live `jv_with`
  hyperedges; report JV-partner pairs where both sides resolve to
  entities. Advisory only.
- **S3 — Residual MNC arms.** For the 4 absent filers, probe
  `subsidiary_of` hyperedges + `name_token_candidates` +
  `bench_data/foreign_probe/links_*.json`. Outcome per arm: either
  seeded via alternate evidence (goes through validate + adjudication +
  gate pins like any seed wave — requires the eval-gate bullet) or a
  documented-absence note appended to `mnc_arm_lane.md` §5.
- **S4 — Index-label hygiene note.** Verify the NIFTY 500 ≡ Multicap
  duplication at the next derive run; if it persists, file the dedup as
  a one-line finding. No store change inside this proposal.
- **Decision points (operator):** D1 — catalog-only vs `psu:` label
  lane (S12-style via `_collect_seed_lane`, which would need gate pins
  + an eval-gate bullet); D2 — same for a `jv:` lane. Both default to
  catalog-only.

## 4. Acceptance

1. S1 report + JSON exist; every PSU-filer symbol profiled; read-only
   (row counts unchanged across runs).
2. S2 census report lists JV-shaped entities with partner resolution
   status.
3. S3: each of the 4 arms carries a verdict — alternate-evidence seed
   (only on operator approval, gate-pinned) or documented absence.
4. No changes to `family_seeds.json` / `mnc_seeds.json` without an
   operator ruling; any roster change that does land satisfies the
   house eval-gate bullet: validate 0-dropped + `ontology_eval_gate`
   ACCEPT between dry-run and canonical apply.
5. Tests per helper (manifest shape, membership rule, read-only
   behavior) mirroring `tests/test_mnc_dyad_pass.py`.
6. Catalog-only default leaves the gate green — 239 questions, 0
   reasons.

## 4.1 Execution log (2026-11-13)

- **S1 IMPLEMENTED** — `helpers/maintenance/psu_dyad_pass.py` (mirrors
  `mnc_dyad_pass.py`; `PSU_PARENTS` maps 26 symbols →
  {ministry, level} with central/state/borderline classification; reuse
  `_members_for_filer`/`_component_ids`). `tests/test_psu_dyad_pass.py`
  5/5. Live run → `bench_data/psu_dyad_pass/report.json`: **26 labels
  profiled / 419 members / 0 uncharted**. Top: POWERGRID 50, ONGC 37,
  SBILIFE 33, NHPC 33, COALINDIA 31, NTPC 25, RVNL 24, RAILTEL 19,
  CONCOR 18, SBICARD 16, PFC 15, PTC 13, NTPCGREEN 12, GPPL 12, SBIN
  11, RITES 11, LICI 11, IREDA 10, IDBI 10, LICHSGFIN 9, NMDC 5,
  IRCTC 4, IRCON 3, NIACL 3, NATIONALUM 2, NSLNISP 2.
- **S2 IMPLEMENTED** — `helpers/maintenance/jv_dyad_census.py`
  (`_JV_PATTERNS` name shapes, `_JV_ANCHORS=[AWL, RENUKA]`, scans
  `graph_edges` `jv_with` — key discovery: live jv_with edges live in
  `graph_edges`, NOT `hyper_edges`; the hyperedge star-lane query
  returns []). `tests/test_jv_dyad_census.py` 5/5. Live run →
  `bench_data/jv_dyad_census/report.json`: **208 JV-shaped entities /
  212 label-memberships / 1,507 live jv_with edges**; anchors AWL=29,
  RENUKA=10; sample resolved pairs: DBL×JV family (DBL SIPL/DBL-AHC/
  DBL-HCC/HCC-DBL/VKMCPL-DBL), Aditya Birla Power Composites↔Grasim,
  Matsui Technologies India↔Samvardhana Motherson B.V. & Co. KG,
  Aravali Power Company↔NTPC, NTPC-SAIL Power↔NTPC, DBL PEL JV↔Patel
  Engineering, Turf Estate Joint Venture LLP↔PRESTIGE, Itezhi Tezhi
  Power Corporation↔Tata Power.
- **S3 IMPLEMENTED** — `helpers/maintenance/mnc_arm_probe.py`
  (canonical Indian-listed name fragments; subsidiary_of + same_group +
  hyperedge + embedding-peer evidence). `tests/test_mnc_arm_probe.py`
  4/4. Verdicts: **ZFCV → already-homed**: `vigil:rpt:ZFCVINDIA`
  exists with 56 members (full ZF/WABCO global network incl. ZF CV
  Control Systems Manufacturing India + 20+ foreign arms) — the dyad
  pass missed it because it keyed ticker `ZFCV`; the filer label is
  `ZFCVINDIA`. **ASTRAZENEC → documented absence**: AstraZeneca Pharma
  India (4 `subsidiary_of` edges, no same_group label, +Strides/Novartis
  India embedding peers). **NOVARTIS → documented absence**: Novartis
  India (1 `subsidiary_of`, no label, +Abbott India/Zydus peers).
  **ABBOTINDIA → documented absence**: Abbott India (1
  `subsidiary_of`, no label, 18 index hyperedges, +Novartis India
  peers). Absence notes to `mnc_arm_lane.md` §5 (operator-facing).
- **S4 VERIFIED** — current store still shows NIFTY 500 ≡ Nifty500
  Multicap 50-25-25 Index: 499/499 members, Jaccard 1.0 (same as S5
  measurement). Dedup remains a one-line finding, no store change here.
- **S4 follow-up (2026-11-13, post-archival).** Operator asked for the
  dedup to actually be executed. Delivered as a separate maintenance
  helper (`helpers/maintenance/index_label_dedup.py`) plus a fold in
  `derive_indices.resolve()`: alias labels fold to `NIFTY 500` at
  derivation time and the live store folded 2 alias hyperedges + 2 alias
  entities (499 incidences preserved, 998 `listed_on_index` alias edges
  dropped by CASCADE). Index census 57 → 55 entities/hyperedges. The two
  smaller duplicates (Nifty MidSmallcap400 50-50 ≡ Nifty MidSmallcap 400
  399/399; Nifty Financial Services 25/50 ≡ Nifty Financial Services
  20/20) remain reported-not-folded pending an operator ruling.
- **S4 follow-up #2 (2026-11-14, operator ruling: fold).** The two
  remaining weight-scheme pairs moved from `OTHER_MEASURED_DUPS` into
  `INDEX_NAME_CANONICAL` (helper + `derive_indices.resolve()`). Applied:
  2 alias hyperedges merged, 2 alias entities dropped (402 + 20
  `listed_on_index` alias edges cascade-removed), index census 55 → 53;
  canonical member sets unchanged (MidSmallcap 400 at 402 after 3
  constituents landed since the first measurement, Financial Services at
  20). `derive_indices --apply` re-inserted nothing (fold idempotent).
  Cache rebuilt (v_node 27,827 → 27,825), integrity exit 0, gate ACCEPT
  239q/0 vs the post-fold-1 parent. Weight-scheme nuance survives only
  in each edge's `index_slug` property.
- **S4 follow-up #3 (2026-11-14, census sweep — no further folds).** Pairwise
  constituent-set overlap computed over all 57 sidecar labels in
  `vw_index_constituent` at their current `as_of` vintages (2025-07-22 →
  2026-09-19). The only Jaccard-1.000 pairs remaining are the five already
  folded (NIFTY 500 ×2, MidSmallcap 50-50, Financial Services 25/50). Every
  other high-overlap pair is a subset/nesting relationship, NOT a duplicate
  (operator ruling: subset pairs are not foldable) — e.g. Nifty 200 ⊂
  NIFTY LargeMidcap 250 (200/200, jacc 0.800), Nifty MidSmallcap 400 ⊂
  NIFTY 500 (401/401, jacc 0.800), NIFTY TOTAL MARKET ⊃ NIFTY 500 (501/501,
  jacc 0.664), NIFTY Midcap 100 ⊂ Nifty Midcap 150 (100/100, jacc 0.667),
  Nifty India FPI 150 vs Nifty 200 (147 inter, cov 0.98, cross-vintage
  skew). Index census stays 53. Vintage skew noted (11 sector/theme labels
  at 2026-06-11, 2 at 2025-07-22, 1 at 2026-02-16, 1 at 2026-08-04 vs the
  2026-09-18/19 core) — the fold evidence for all five folded pairs was
  same-vintage; cross-vintage pairs (e.g. FPI 150) need a sidecar resync
  before any membership ruling. Weight-scheme/subset nuance unchanged.
- **S4 follow-up #4 (2026-11-14, sidecar resync + valid_to prune lane).** The
  vintage skew noted in #3 is closed: `make refresh-indices APPLY=1` pulled
  every label to its 2026-11-13/14 vintage (58 labels, 7,649 latest-vintage
  rows, +988 over the previous 6,661). `helpers/maintenance/index_sync.py`
  import order fixed first — `REPO`/`sys.path.insert` now precede
  `import duckdb` and the `helpers.core.duckdb_shared` import (was
  `ModuleNotFoundError: No module named 'helpers'` for the bare script and
  for `make refresh-indices`). The sidecar holds both the new membership and
  the full vintage history, so a departed member is now a first-class fact
  instead of a silent absence: `helpers/graph/derive_indices.py` gained
  `current_pairs()`, `plan_close()`, `close_superseded()`,
  `latest_vintages()`, `reopen_current()` and the `--prune-absent` /
  `--max-close` CLI flags.
  `valid_to` is the EXCLUSIVE window end (`helpers/graph/as_of.py`), so a
  departure is stamped with the new vintage date: the member stays visible
  through the day before the reconstitution and disappears from it onward.
  A returning symbol is revived (valid_to cleared, `properties.as_of` and
  `valid_from` refreshed) rather than duplicated —
  `UNIQUE(source, target, edge_type)` leaves no room for a second row.
  Applied: 281 departed edges closed (240 stamped 2026-11-13, 41 stamped
  2026-11-14); `listed_on_index` rose 5,226 → 6,622 with `valid_from` on
  all of them and 0 inverted/empty windows; visibility check
  5,226 @2026-09-30 → 6,302 @2026-11-13 → 6,341 @2026-11-14.
  `--max-close` (default 5,000) aborts a
  sweep that would close more than a reconstitution plausibly explains.
  Re-derive is idempotent (0 inserted, 0 closed, 0 reopened on the live
  store). 8 new tests in `TestReconstitutionSweep`
  (tests/test_derive_indices.py, 22 passed total). NOT wired into
  `make derive-indices` yet — that target still runs the bare `--apply`
  (Makefile:750), so the sweep is a manual invocation for now. NOT landed:
  snapshot parquet still shows the pre-resync sources
  (index_constituents 6,661 vs 14,210 live) and pre-prune
  entities/graph_edges — `make snapshot` is the operator call.

## 5. Risks

- PSU classification is two-dimensional (central vs state; parent vs
  subsidiary — SBICARD is an SBI subsidiary, NSLNISP a port SPV, GPPL
  a port JV): misclassification risk is why S1 profiles rather than
  asserts a single `psu:` tag by default.
- Name-pattern JV detection is fragile (abbreviations, glyphs); the
  census is advisory, never auto-applied.
- Alternate-evidence MNC seeding risks #507-style inflation; any seed
  wave goes through the validate + adjudication + gate-pins path.
- Label-namespace growth if D1/D2 approve lanes; the group growth
  monitor catches size drift.

## 6. Non-goals

- No PSU/JV seeds by default; no family-lane changes (v10 landed).
- No store schema changes; no new tables.
- Supply-chain and bridge-entity structure stays advisory — banked in
  `bench_data/hyperedge_cooccurrence/`; no lane here.
- No index-store dedup execution (S4 is verify + note only).
