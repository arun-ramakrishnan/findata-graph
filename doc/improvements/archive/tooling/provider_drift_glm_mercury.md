---
title: Provider-drift — GLM pre-annotator is not a verdict-stable gate; mercury-decide is
status: executed
executed: '2026-10-04'
completed_md: '341'
filed: '2026-10-04'
area: tooling
---

# Proposal: Provider-drift — GLM pre-annotator is not a verdict-stable gate; mercury-decide is

**Date**: 2026-10-04
**Status**: ADOPTED — operator decision 2026-10-04: keep GLM on the
discovery/extraction lane (its quote+evidence scouting) and use
mercury-decide for the rubric-management cross-check, where GLM's
day-to-day endpoint drift would corrupt an ADMIT verdict table. Probe
record in `doc/local/evaluations/jev_pilot/legs3/drift_log.md`.
**Depends on**: `search_enablers.md` (implemented, lanes),
`system_one_typed_judgment_framework.md` (S1–S2 shipped: the typed client),
`doc/local/evaluations/jev_pilot/` (all eval-v3 keys), the relations
pre-annotation orchestrator (`triage_preannotation_escalation.md`,
implemented).

## What the pilot measured today (eval-v3 key, 56 items, same tree, same code)

`doc/local/evaluations/jev_pilot/legs3/drift_log.md` is the canonical record.
Our two-run probe (baseline `drift_prev.json`, probe `answers.json`) recorded:

|            | GLM-5.3 pre-annotator | mercury-decide:free |
|---|---|---|
| admit True | 3 → **0** | 0 → 0 |
| escalated (Q2 low-confidence sweep) | 19 → **27** | N/A |
| rows with evidence URL | 7 → **2** | N/A |
| fact wag | 5 flips (4 hard \| 1 marginal, ids 20, 27, 30, 40, 54) | **0** |
| admit flips | 3 flips (all hard: ids 20 [0.15], 47 [0.30], 54 [0.70]) | **0** |
| verdicts over the drift window | not stable — three keys in 48h, reversed | bit-identical Oct 3 → Oct 4 AM → Oct 4 PM |

"Hard" = the poll is a crossing of p=0.5 at more than ±0.15 of the
`AGREE_TOL = 0.2` band; "marginal" = within band.

## Reading

- The **unit of change tracks the endpoint, not the tree or the web**.
  Mercury is deterministic per key-version; GLM flips eval-wide at
  non-marginal confidence. Z.AI's GLM 5.3 no-code-change path varies the
  answer axis well beyond one standard-deviation of the boundary band.
- Which facts this invalidates: any claim in a proposal or §8-style
  objective reading GLM p_fact/p_rubric alone as gate-ready
  ("GLM refuses rows X, Y, Z — trustworthy") is invalid; p_* is not P(yes)
  but self-rated confidence in an incoherent mapping as shown Oct 3.
- A carrier probe of "the relation never happened" is preserved across
  both GLM paths if the bound is ≤ ~0.30/0.80. GLM's 0.55–0.60 admit
  returns on the drift window are arguably the least trustworthy flavour.

## Recommendation (body-side we can act on)

1. **Do not publish any `p_fact/p_rubric` GLM admit as a partition into
   the write layer.** GLM is a *discovery scout* whose role is the quote
   + URL evidence. The rubric-keeper on ADMIT remains the two-carrier
   cross-check; mercury is bit-stable, GLM is scout-only unless it admits
   with marginal-evidence evidence.
2. Land drift as a *baseline test* — the `provider_drift_check.py` lane
   driver from the pilot is wired, and future carriers inherit
   drift_prev.json baseline.
3. Once we recover a stable GLM probe window, we reopen this S1–S2
   decision of this proposal (impl: constrain the GLM id to the stable
   OpenRouter exec' rather than Z.AI, or formalise their gratis gem is
   acceptance-weighted).
4. Proposal considered for closing once a subsequent two-run probe shows
   both lanes clean with no new flips.
