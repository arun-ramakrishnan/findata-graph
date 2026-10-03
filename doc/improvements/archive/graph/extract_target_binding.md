---
title: extract_relations `acquired from|by` target binding — wrong target for breadcrumb-style MHP-acquired-from-Porsche rows
status: executed
filed: '2026-10-04'
area: graph
executed: '2026-10-04'
completed_md: '343'
---

# Proposal: `extract_relations` `acquired from|by` target binding

**Date**: 2026-10-04
**Status**: EXECUTED 2026-10-04 (completed.md #343). The rewrite landed
plus two measured refinements the slice surfaced: (1) a scope guard — the
rewrite fires only on `acquired`-anchored matches, because the condition
`edge_type == "acquired" and direction == "reverse"` also catches the H1
`demerged from` / `merged with` patterns and a cross-company mention
("SteelCo demerged from ParentCo" in a third party's section) minted
`(section, acquired, SteelCo)` — a false M&A edge, verified live before the
guard; (2) a noise-gate exemption — `noise_target("MHP")` was True (the
`<4`-char fragment rule, only rbi/sebi exempted), so in production the
corrected row (MHP not yet an entity → unresolved) died at the write-time
gate and never reached the queue. Exact-name corporate exemption
`tpr._CORPORATE_SHORT_NAMES = {"mhp"}`, per the I1 arc's narrowness
doctrine. Regression: `tests/test_extract_relations_extraction.py::
TestReverseAcquiredPredecessorBinding` (4 tests incl. the demerged/merged
scope guard and the generic-object noise-gate pin). Gate key re-run
(59 items): write gate noise 49/49 dropped, non-noise 10/10 kept
(recall 1.0 — the corrected MHP row now survives), truncation 5/9, bucket
9/10 (remaining miss = the pre-existing Clix fuzzy-outside-universe
artifact). S5 interaction: the bold-label capture itself stays
`label_not_prose`-deferred at the unresolved path by design (audit S5,
`TestS5SentenceIntegrity`); the extraction-level edge (acceptance 1) needs
MHP to resolve, i.e. after the stub the operator's labeled row calls for.

## Bug

In a TCS newsletter the prose reads:

> **MHP acquired from Porsche:** TCS takes over Porsche's
> automotive-consulting arm MHP — ~€700 mn revenue at double-digit margins.

`extract_relations.py` has the reverse-form pattern

```text
acquired\s+(?:by|from)\s+((?-i:[A-Z])...)
```

with `direction="reverse"`, `edge_type="acquired"`, and the captured group
as `target_mention`. For the text above it binds:

- `target_mention = "Porsche"`, `source = Tata Consultancy Services`,
  `direction = reverse`

and minted the sidecar row `acquired:Tata Consultancy Services:Porsche`
(direction reverse) — the prior owner, not the acquired object.

The correct edge, per the labeled row: `source = Tata Consultancy Services`,
`target = MHP`, `direction = forward` (TCS acquired MHP from Porsche). The
extractor has no capture for the object that precedes `acquired` in the
reverse form.

Live advisory probe (2026-10-04, `triage_preannotate` escalation lane on the
sidecar) confirmed the row routes to rubric keep-out today, so it cannot
write a bad edge — but the row itself is nonsense and every consumer of the
advisory/extraction (sidecar, review queue) inherits it. The same malformed
form has 9 duplicates in the 31-line sidecar from the measured bin of
rows all living on this one extractor binding.

## Slice (narrow, one pattern-level change)

For the two reverse `acquired` patterns only: when there is a capitalized
proper noun immediately before `acquired` (e.g. `**MHP acquired from…`),
emit the edge with that preceding noun as the forward target — i.e.

```text
acquired by X  ->  X acquired  (target = the object X acquired, which
                   precedes the verb)
acquired from X -> X sold its stake in the preceding entity; target = the
                   preceding entity (which the section company acquired)
acquired [obj] by|from X  ->  the preceding obj is the direct target
```

Concretely: if the match is NOT of the shape `section company (the writer's
own company) acquired …`, then rewrite as forward, target = the noun phrase
captured before the verb, e.g. `MHP`. Do not parallel-edit the other
reverse patterns.

Grounding: the labeled row's operator note already records the correction
("mention Porsche is the PRIOR OWNER; true target MHP … direction corrected
to forward"). The fix must make the extractor emit that same row before the
operator ever answers it.

## Eval-gate regression coverage

`extractor_gate_key.py` pins the noise/non-noise routing on the frozen
`dataset/gate3/items.jsonl`; this slice adds the target-binding axis:

- add an item to `dataset/gate3/items.jsonl` with `target_mention: "MHP"`,
  `operator_label: "admit"`, `operator_bucket: "stub_candidate"` — the
  expected shape after the fix;
- assert the currently-minted `target_mention: "Porsche"` row no longer
  appears in extraction output (regression pointer in the score file);
- re-run the gate key (expect same routing confusion for unrelated rows; the
  new MHP row routes `human`/`stub_candidate`, and `truncation_hits` stays
  on the frozen-row axis).

## Acceptance criteria

1. `extract_relations.extract(...)` on the TCS newsletter span emits
   `(Tata Consultancy Services, acquired, MHP, forward)` — and NO
   `acquired:…:Porsche` row.
2. No other labeled/pinned row in `dataset/gate3` changes bucket or routing
   (re-run the key, read the diff).
3. A regression test lands: one prose fixture with `**MHP acquired from
   Porsche:** …`, assert target `MHP`, direction forward.
