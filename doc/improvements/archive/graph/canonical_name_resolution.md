---
title: Canonical-name resolution - NSE-certified duplicate-spelling folds
status: executed
filed: 2026-11-12
executed: "2026-11-12"
completed_md: '509'
area: graph (helpers/graph/name_dedup.py canonical lane)
---

# Canonical-name resolution - NSE-certified duplicate-spelling folds

**Date:** 2026-11-12 . **Status:** EXECUTED (completed.md #509). Operator go received: "NSE listing is reliable way to pick a winner for tie break and fold in the right direction. Write a slice for that and lets fix." - session m06076, implementing the canonical_name_slice follow-on recorded in bench_data/name_alias_triage/rulings.json)

## 1. Motivation

The store carries multiple spellings of the same listed company as separate entities (Titan / TITAN COMPANY / Titan Company / Titan company; Indian Hotels plus three replay-minted IHCL variants; Tata Power / THE TATA POWER COMPANY / Tata Power Company). Exact-name surfaces - suggest_relations, A-square pairing, hyperedge membership, gate pins - see each spelling separately, and the alias triage menu (bench_data/name_alias_triage/) showed the mechanic the ledger lacked: it could fold a spelling only when told which side wins, and its recorded directions pointed at the RARER spelling for nine pairs.

## 2. Design (the slice)

New canonical lane in helpers/graph/name_dedup.py (this proposal is the S1 sign-off surface; S2 rides the existing --apply):

- --canonical-plan clusters ENTITIES by the loader own loose key (related_party_sync._loose_key, the same rule that would mint a variant), so the fold and the mint path can never disagree.
- Certification: a cluster is foldable only when an NSE equity listing (exchange_listings, exchange=NSE, asset_type=equity, from memory/data/sources.duckdb) shares its loose key - a listed issuer certifies the cluster is one real company spellings. Clusters with no NSE member are reported, never folded (777 of 840).
- Winner: an exact-normalised NSE match; else refs descending, then the shorter display form, then stable lex.
- Variant classifier (_diff_class, tokens the variant ADDS over the winner):
  - safe (folds): the, company, co, limited, ltd, and private/pvt only as the Private Limited unit - plus case/punctuation differences.
  - review (cluster held out, report-only): corp / corporation - part of some listed companies own legal names (Hindustan Petroleum Corporation) and of other groups parent entities (Bosch Corporation); needs a human.
  - reject (never folds): any other added token - Wipro Inc, Cyient Inc, Mphasis Corporation are FOREIGN-ARM legal entities, not spellings; folding them would destroy real subsidiary distinctions (the same doctrine as the ledger (UK) blind spot).
  - mixed entity_type clusters (company + institution duplicates) are review-only.
- Durability: the apply appends the folds to findata/Misc/_name_fold_ledger.jsonl, so every loader refresh reuses the canonical spelling instead of re-minting.
- --plan-path lets --apply consume this plan through the exact S2 machinery (re-point, collapse duplicates, incidence sweep, ledger append, FK-cascade guard).

## 3. Measured population (2026-11-12, live store)

- Loose-key clusters over 28,725 entities: 840 with more than one member.
- NSE-certified (foldable): 22 clusters. Review (Corp-form or mixed-type): 41. Foreign-form rejects (kept separate by design): roughly 30 across the certified clusters. Unlisted (never folded): 777.
- Applied (S2, this session): 28 variants folded, 147 edge rows re-pointed, 141 duplicate rows collapsed, 28 ledger rows appended, zero loss (rows_before 87,263 -> rows_after 87,122). Head folds: Tata Power 137 refs, Indian Hotels 92, Titan 79, Phoenix Mills 36, ICICI Lombard 22.
- The Nifty Private Bank index survives: the private-only difference is rejected (regression-tested).

## 4. Eval gate and acceptance

Query-visible semantics change (endpoint names inside pinned answers move). Acceptance:

- gate --parent /mnt/data/tmp/research.db.precanon --candidate live ACCEPT with --expect-change for exactly the pins whose endpoint spellings moved (expected: edge-rallis-india-same-group; anything further is investigated, not declared).
- probe --parity 0 mismatches; frozen re-pins land with rationale; bank re-pins ditto.
- name_dedup --check baseline updated; 8 new classifier/census tests pass (TestCanonicalResolution); the ledger invariant honours operator-ruled alias rows via the kind=alias + ruled_by marker (HDFC Life row, ruling m06076).

## 5. Risks and non-goals

- Tie-breaks are deterministic (refs, then shorter display form, then lex) and recorded in the plan artifact; re-running --canonical-plan after the apply yields an empty foldable set.
- Foreign-arm entities (... Inc, ... Corporation where a group parent is meant) stay separate BY DESIGN - this proposal does not merge distinct legal entities; the review list is the input for any future human-ruled merges.
- Index-name collisions (Nifty Bank vs Nifty Private Bank) are guarded by the standalone-private reject rule.

## 6. Reproduce

```bash
.venv/bin/python3 helpers/graph/name_dedup.py --canonical-plan   # dry plan + review lists
.venv/bin/python3 helpers/graph/name_dedup.py --apply --yes --plan-path findata/Misc/_name_canonical_plan.txt
make derive-relational-insights APPLY=1 && make snapshot
```
