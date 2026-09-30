---
title: "Fix VIGIL symmetric-emission pairs: deterministic direction precision in related_party_sync"
status: executed
filed: "2026-09-29"
executed: "2026-09-30"
completed_md: "320"
area: "helpers/maintenance/related_party_sync.py"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Fix VIGIL symmetric-emission pairs: deterministic direction precision in related_party_sync

**Date:** 2026-09-29 · **Status:** EXECUTED 2026-09-30 — S1–S3 DELIVERED ·
**Area:** `helpers/maintenance/related_party_sync.py`

> **Disposition: EXECUTED 2026-09-30.** S1 audit → S2 fix → S3
> tests + eval gate + canonical apply, all verified. Post-apply state:
> same-ref `subsidiary_of` cycles **37 → 0**; cross-period
> `supplier_to` artifacts **0** (91 pruned from the existing graph, 729
> collapsed at mint); **4,367 legit same-period two-way supplier pairs
> intact**; cross-ref mutuals untouched (30 supplier + 2 subsidiary);
> 57,515 edges total. Eval gate ACCEPT (164 questions, 0 regressions)
> between the candidate-copy dry-run and the canonical apply.

## S1 — classification table (measured 2026-09-30, read-only)

Of 4,521 mutual same-type pairs:

| class | n | verdict |
|---|---|---|
| `supplier_to`, same-ref, both directions evidenced in a SHARED period_end | **4,361** | legit two-way — keep both edges |
| `supplier_to`, same-ref, cross-period MAX-aggregate | **91** | artifact — one dominant edge survives, both amounts + both period_ends in properties |
| `supplier_to`, cross-ref | **30** | zero-evidence contradiction class — NEVER touched |
| `subsidiary_of`, same-ref cycle | **37** | every one is exactly one sub_fwd + one sub_rev mint from ONE filer's filing (rule-order artifact texts in 15 — 'Subsidiary of Holding Company' hits `%holding company%` before `%subsidiar%`) |
| `subsidiary_of`, cross-ref | **2** | NEVER touched |
| entity-dup (normalized_name collision on endpoints) | **0** | screen clean; the near-dup examples ("Atul ↔ Atul products" — distinct normalized names) stay resolver-lane material |

**Direction rules S2 implements:**
- `subsidiary_of` same-ref cycle → keep the **sub_fwd** edge (counter
  claimed as the filer's subsidiary), drop the sub_rev edge. Reality
  spot-checks (Redington×3, JSW/Piombino, NTPC/NEEPCO, BLS,
  Britannia/Gilt Edge) confirm sub_fwd; the residual risk the proposal
  flagged is real but bounded — a same-filing contradiction is filing
  data error no text rule can fix, and the rule is at least
  deterministic. The surviving edge keeps its `xbrl_url` evidence.
- `supplier_to` same-ref cross-period → keep the **larger-amount**
  direction (tie: the sale direction); the survivor's properties carry
  both amounts + both period_ends + `two_way: cross-period`.
- Same-ref scope ONLY, enforced in code: every prune/mint decision
  requires `source_ref` equality between the two directions.

## S2 — delivered mechanism (`related_party_sync.py`)

- `classify_relationship(text, rel_group)` — Python mirror of the
  `_CLASSIFY_SQL` CASE order, pinned by test against the SQL (the rule
  order IS load-bearing; the test includes the artifact texts).
- Mint-time drops: `apply_pairs` never mints the sub_rev side of a
  same-ref cycle; `apply_supply_pairs` emits both directions only when
  `sale_period == buy_period` (per-direction periods now computed in
  `_SUPPLY_SQL`), else one dominant edge with both amounts.
- `prune_symmetric_artifacts()` — the existing-graph cleanup (the S1
  table's 37 + 91), same-ref scope, dups flagged never merged, wired
  into `main()` before the passes with per-class counts in both
  dry-run and apply modes.

## S3 — tests, gate, apply

- 6 new tests in `tests/test_related_party_sync.py` (14 total, green):
  same-ref cycle collapses at mint (sub_fwd evidence survives), prune
  drops an existing same-ref cycle and leaves a cross-ref pair intact,
  entity-dup flagged never merged, cross-period collapses to the
  dominant direction with both amounts preserved, genuine same-period
  two-way still survives, and the classify-mirror-agrees-with-SQL pin.
- Eval gate: ACCEPT — 164 questions, 0 reasons — between the
  candidate-copy dry-run (counts identical to apply: 37 + 91, 0 dups)
  and the canonical apply.
- Canonical apply 2026-09-30: prune 37+91; group pass re-minted 22,692
  edges with 40 mint-time cycle drops (three cycles the historical
  graph never had — entity-resolution drift since the last sync);
  supply pass 17,778 edges with 729 mint-time cross-period collapses;
  ratings 213. Graph rebuilt post-apply; post-state verified above.

## Motivation

The MemGraphRAG assessment measured the live `graph_edges` (57,632 edges,
50,097 distinct pairs) and found the anomaly population is not cross-source
contradiction but same-source symmetric emission: **9,042 edges in 4,521
mutual `A ↔ B` same-type pairs** — `supplier_to` 4,482 pairs,
`subsidiary_of` 39. 99.3% of reciprocal `supplier_to` pairs (4,452/4,482)
carry an identical `source_ref` on both directions, as do 94.9% of
reciprocal `subsidiary_of` pairs (37/39); 100% of the reciprocal
`supplier_to` population comes from the `vigil` lane
(`SOURCE_REF_PREFIX = "vigil:rpt"`, `related_party_sync.py:43`). Single bulk
refs manufacture hubs: one `vigil:rpt:<TICKER>` ref carries 218 subsidiary
edges for Axis Bank, 170 for ITC, 298 for Infosys, 306 for Reliance, 224 for
Tata Steel, 254 for Wipro. The 39 mutual-`subsidiary_of` pairs are outright
logical cycles ("Atul ↔ Atul products"). Full numbers in
`doc/local/evaluations/memgraphrag_assessment.md` ("Borrow 1 re-scoped by
measurement").

One honest brake before cutting: `supplier_to` two-way emission is partly by
design — `build_supply_pairs` (`related_party_sync.py:382-388`) mints an
edge per evidenced direction (filer SELLS → `supplier_to(filer, counter)`,
filer BUYS → `supplier_to(counter, filer)`). But the amounts are
`MAX`-aggregates across records and periods (`_SUPPLY_SQL`,
`:363-379`), so a sale in one period and a purchase in another land as two
directed edges under one ref. And several mutual pairs ("Atul ↔ Atul
products") are entity-resolution duplicates, not direction errors at all.
S1 classifies first; S2 cuts only what S1 proves is artifact.

## Slices

- **S1 — read-only audit.** Classify every mutual same-type pair into:
  `subsidiary_of` cycle same-ref vs cross-ref; `supplier_to` both-amounts
  same-`period_end` vs cross-period aggregate vs single-amount artifact;
  entity-dup screen (normalised-name collisions on either endpoint). Output
  a counts table plus the direction rule per class. S2's cut list is gated
  on this table — no wholesale mutual-pair collapse.
- **S2 — deterministic fix in `related_party_sync.py`.** `subsidiary_of`
  cycles → single direction per the S1 rule, loser dropped, surviving edge
  keeps the `xbrl_url` evidence in properties. `supplier_to` → both edges
  only when both directions are evidenced in the same `period_end`;
  otherwise the surviving direction keeps both amounts in properties.
  Same-ref scope only: cross-ref pairs are never touched (that is the
  zero-evidence contradiction class, out of scope). Dry-run counts before
  any write; entity-dup pairs are flagged, never silently merged.
- **S3 — tests, eval gate, apply.** Regression tests on seeded fixtures:
  same-ref cycle collapses, genuine same-period two-way survives,
  cross-ref pair untouched, entity-dup pair flagged. Eval-gate bullet (the
  extractor-rules house rule): `helpers/misc/ontology_eval_gate.py` over
  the frozen question set between dry-run and canonical apply. Report edge
  deltas per class on apply.

## Acceptance

1. [x] Same-ref `subsidiary_of` cycles: 0 (37 → 0, per-pair direction
       audit in the S1 table)
2. [x] `supplier_to` mutuals reduced exactly to the S1-evidenced set
       (4,367 same-period two-way; every survivor minted under the
       same-period rule, both amounts in properties)
3. [x] Seeded fixtures green: collapse / survive / untouched / flagged
       (6 new tests, 14/14 in the module)
4. [x] `ontology_eval_gate.py` ACCEPT between dry-run and apply
       (164 questions, 0 reasons)
5. [x] Zero cross-ref pairs modified (ref-equality enforced in every
       prune and mint decision; test-pinned)

## Non-goals / risks

- No LLM adjudication, no BFS components, no size caps — Borrow 1 is
  retired; that machinery would price a deterministic defect as a modelling
  problem.
- No review queue for this defect class — it is deterministic, and the
  relations triage lane stays for genuinely ambiguous items.
- No cross-source contradiction handling — the named class measured zero.
- No entity-resolver merge — dups are flagged for the resolver lane.
- Risk: the RPT `relationship` text may not determine direction for every
  cycle — mitigated by S1's rule table plus dry-run counts as the S2 gate.
