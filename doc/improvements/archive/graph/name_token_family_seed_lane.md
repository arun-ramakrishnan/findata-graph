---
title: "Extend the name-token/family-seed group lane (candidates for adjudication, never auto-apply)"
status: executed
filed: "2026-11-12"
executed: "2026-11-12"
completed_md: "510"
area: "group-relation candidate generation (new read-only helper) and curated family lane (helpers/misc/family_seeds.json)"
---

# Extend the name-token/family-seed group lane (candidates for adjudication, never auto-apply)

**Date:** 2026-11-12 · **Status:** EXECUTED 2026-11-12 (completed.md #510) ·
**Area:** new read-only candidate helper (`helpers/maintenance/` mould);
curated family sink `helpers/misc/family_seeds.json` (S6 lane, #507);
no changes to `GROUP_RES`

## 1. Motivation

Group/subsidiary/supplier relations are a standing struggle. This session
measured whether the *vector* space (e-means clustering over
`company_embeddings`) identifies them "more elegantly". It does not — and
the same measurements rank the alternative that already half-exists: the
**name-token lane**. This proposal funds that lane: a read-only candidate
generator over stored entity names, feeding the existing curated family
sink. The vector-space alternative is measured and rejected in the same
breath, so it does not get re-litigated later.

## 2. Evidence (measured 2026-11-12, this box)

Signal comparison for `same_group` recovery over the 1,209 listed
companies (223 true company-company pairs):

| signal | lift | works on unlisted/stub parents? |
|---|---|---|
| shared distinctive name token | **76×** (38.5% of true pairs vs 0.5% of random pairs) | yes — names exist for stubs |
| embedding cosine (mean percentile 0.827) | 4.8× | **no** |

Why the vector space is blind on the hard edges: of 17,489
`subsidiary_of` edges, only **105** connect two embedded companies —
**99.4% have at least one endpoint with no embedding** (12,073 have
neither; top unlisted parents: "Adani Green Energy" ×185, "H.G. Infra
Engineering" ×102, "Tata Steel UK" ×57). Cluster co-membership recovers
only **12.1%** of true `same_group` pairs (22.3% of `subsidiary_of`) at
**0.15% precision**. Groups are cross-sector by construction (172 of 175
hubs with ≥3 listed members span >1 sector; sibling pairs 7.6%
same-sector, cosine 0.7123 vs 0.6523 baseline) — similarity is the wrong
axis for a relation that deliberately crosses industries.

Candidate-generator yield (stopworded tokens, len>2, df-capped):

| df cap | candidates | hit existing sg/sub/jv | precision | new candidates |
|---|---|---|---|---|
| ≤5 | 554 | 80 | **14.4%** | 474 |
| ≤10 | 1,321 | 95 | 7.2% | 1,226 |
| ≤25 | 2,375 | 135 | 5.7% | 2,240 |

The family signal concentrates in a handful of tokens: tata (39 pairs,
df 11), mahindra (5, df 9), godrej (4, df 4), icici (3, df 4), muthoot
(3, df 3), adani (2, df 5), aarti / raymond / welspun (2 each, df 3) —
exactly the families the curated lane already carries (8 sets,
`family_seeds.json` v4). High-df tokens (bank 49, finance 26, energy 24,
power 22) are sector words, not family words — the df cap excludes them.

Raw log: `doc/local/engineering/emeans_algorithm.md` (machine-local), §9.

## 3. Design

S1. **Candidate generator** (read-only, new helper in the
   `helpers/maintenance/` mould): over stored company names, stopworded
   distinctive tokens (len>2), df-capped at 5 for the core queue (df≤25
   extended view); exclude pairs already connected by `same_group` /
   `subsidiary_of` / `jv_with`; emit a ranked adjudication report (pair,
   token, df, existing-edge check). **No store writes.**

**S1 implemented 2026-11-12** — `helpers/maintenance/name_token_candidates.py`
(`python -m helpers.maintenance.name_token_candidates --db memory/research.db`).
Default universe = the 1,209 `company_embeddings` companies (the exact
measurement population); `--all-companies` widens to every
`entity_type='company'` name; `--strict` additionally stops
`NOISE_STOPWORDS` (corporate suffixes/segment words — raises precision to
18.9% but diverges from the recorded evidence counts). Pair rows carry
source/target (display names), the rarest shared distinctive token + its df,
and `hit` (already connected by same_group/subsidiary_of/jv_with). Report is
ordered by (df, token, source, target) — deterministic; `--json FILE` writes
the full ordered list. Re-run on the 2026-11-12 store:

| mode | total | hits | precision | new |
|---|---|---|---|---|
| core df≤5 (default) | 571 | 80 | 14.0% | 491 |
| extended df≤25 | 3,228 | 136 | 4.2% | 3,092 |
| core df≤5 --strict | 424 | 80 | 18.9% | 344 |

Read-only asserted: `graph_edges` (87,109 rows) and `hyper_incidences`
(57,148 rows) unchanged across generator runs. Tests:
`tests/test_name_token_candidates.py` (7) — determinism, df cap,
existing-edge exclusion (direction-independent), stopword behavior (incl.
strict gate), all-companies widening, empty corpus.
S2. **Adjudication sink stays the curated lane**: confirmed families are
   operator edits of `helpers/misc/family_seeds.json` (`family:<name>`
   sets; the S6 lane is FK-clean by construction — unknown names are
   dropped and printed, never minted — and prune-convergent: a removed
   seed is pruned on the next derive run). The generator never writes
   edges: the #507 lesson (auto-derived group edges inflated
   `same_group` 10,373 → 33,270, 3.2×, destroying real parent claims) is
   the standing veto on auto-apply.
S3. **Eval-gate**: family-lane widening is query-visible (group hyperedges
   grow); every new family set is declared as a first-apply improvement in
   the gate run that lands it. **First apply 2026-11-12** — 6 new
   `hyper-family-*` pins added to `helpers/misc/ontology_questions.json`
   (v12 → v13; godrej/icici/raymond/jsw/welspun/aarti; expected = sorted
   seed members); pins are declared for the next gate run.

**S2 implemented 2026-11-12** — v5 seeds applied: Godrej (4), ICICI (4),
Raymond (3), JSW (5), Welspun (3), Aarti (3) = 6 families / 22 members
(`helpers/misc/family_seeds.json` 4 -> 5; `_note` records the provenance).
`validate_family_seeds` over the new file: 14 families / 351 members, 0
dropped. Derive dry-run: `S6 family seeds: 14 curated family set(s)
widening group (351 member(s))`; new hyperedges = 6 / incidences 26.
Excluded on evidence (documented in `_note`): Shriram (polysemy — DCM
Shriram persons-cluster vs Shriram Finance corporate-cluster vs Shriram
Pistons SPR cluster are three distinct groups with no cross-edges; kept
separate per operator), Samsung/Oracle (foreign non-promoter), Max
(Healthcare leg edge-less — optional later).

> **Sibling proposal:** the S9 counterparty and S11 sub-sector gaps found
> while closing this lane are tracked separately in
> `subsector_lane_completion.md` (curated aliases + authored mapping).

## 4. Acceptance criteria & shakedown

1. **Eval-gate bullet** (mandatory — extractor rules / query-visible):
   `ontology_eval_gate.py` parent-vs-candidate over the frozen set; new
   family pins declared first-apply; zero undeclared changes; ACCEPT.
   **VERIFIED/ACCEPTED 2026-11-12** — gate ACCEPT (188 questions, 0
   reasons; parent = 14:18 `research.snapshot.db.zst` pre-apply copy,
   candidate = live `memory/research.db`); six new `hyper-family-*` pins
   declared `--expect-improve` and materialized as pure gains; rebaselined
   to questions v13 (ref `derive_hyperedges_apply_family_seeds_v5_2026-11-12`).
2. Generator is read-only: a full run leaves `graph_edges` and
   `hyper_incidences` untouched (assert via the integrity snapshot).
   **VERIFIED 2026-11-12** — counts unchanged across runs.
3. Core queue (df≤5) bounded: ≤600 candidates on the 2026-11-12 store
   (measured 554), each annotated with token, df, and existing-edge status.
   **VERIFIED 2026-11-12** — 571 (≤600), annotated row-per-pair.
4. Precision floor recorded in the report: ≥14% of core candidates hit
   existing relations on the 2026-11-12 store (measured 14.4%).
   **VERIFIED 2026-11-12** — 80/571 = 14.0% (hit count exact).
5. `family_seeds.json` edits pass `validate_family_seeds`
   (`helpers/graph/derive_hyperedges.py:1269`); version bumped.
   **VERIFIED 2026-11-12** — v5, 14 families / 351 members, 0 dropped.
6. Targeted tests: generator determinism, df cap, existing-edge exclusion,
   stopword list. **DONE 2026-11-12** — 7/7 green.

## 5. Risks

- **Token noise** — high-df tokens are sector words; the df cap is the
  primary filter and the stopword list the second line of defense, grown
  only from measured noise.
- **Name-sharing ≠ relation** — a shared token proposes a *candidate*,
  never an edge; direction (parent/subsidiary/supplier) still needs
  RPT/prose evidence. The generator deliberately emits untyped pairs only.
- **Queue rot** — candidates age with the store; the report is regenerable
  at will (read-only, seconds).

## 6. Non-goals

- No auto-apply of candidates (the 3.2× `same_group` inflation lesson,
  #507).
- No vector-space group detection — measured and rejected (§9 of the raw
  log: 4.8× vs 76×, blind on 99.4% of the hard edges, cluster
  co-membership 12.1%).
- No changes to the `GROUP_RES` prose regexes
  (`helpers/graph/extract_relations.py:1206`) — they mine evidence text;
  the token lane mines names. Both stay.
- No `subsidiary_of` auto-derivation from names.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-11-12 | token lift vs random | 38.5% vs 0.5% → 76× | 223 true same_group pairs |
| 2026-11-12 | candidate yield df≤5 | 554 candidates, 80 hits (14.4%) | stopworded, len>2 |
| 2026-11-12 | S1 re-run df≤5 | 571 candidates, 80 hits (14.0%) | grammar-only stopwords; hits exact |
| 2026-11-12 | subsidiary_of embedding coverage | 105 both-sided of 17,489 | 99.4% ≥1 side unembedded; 12,073 neither |
| 2026-11-12 | cluster co-membership k=42 | same_group 12.1%, subsidiary 22.3% | e-means seed 0, precision 0.15% |
| 2026-11-12 | group sector spread | 172/175 hubs multi-sector | siblings 7.6% same-sector |
