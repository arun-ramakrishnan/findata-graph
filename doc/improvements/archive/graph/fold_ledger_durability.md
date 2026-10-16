---
title: "Fold ledger durability — an append-only record so a signed name fold survives a re-plan"
status: executed
filed: "2026-10-16"
executed: "2026-10-16"
completed_md: "402"
area: "helpers/graph"
---

## 0. TL;RA

`entity_identity.fold_registry()` documented a guarantee it could not
keep: *"The registry IS the artifact: write paths consult this so a
signed fold keeps routing old spellings to the canonical forever."* The
registry was read from the S1 **merge-plan artifact**
(`findata/Misc/_name_merge_plan.txt`), and `name_dedup.write_plan`
**regenerates** that artifact. After a merge the census is empty, so the
next `--plan` writes a header-only file (`clusters: 0 | edge rows
touched: 0`, 383 bytes) and the registry collapses to `{}` — every
signed fold's routing promise silently void, exactly when it starts to
matter.

Observed on the live store after the 82-cluster company merge:
`fold_registry entries: 0`, alongside `registry_index entries: 24,832`.

Fix: an **append-only fold ledger**, `findata/Misc/_name_fold_ledger.jsonl`
(company) and `_person_fold_ledger.jsonl` (person). `apply_plan` now
EXTENDS the ledger — it never replaces it — and both readers union
ledger ∪ plan, so the durable half cannot be emptied by a re-plan.

## 1. The defect

Two artefacts were conflated in one file:

| role | who writes | lifetime |
|---|---|---|
| **proposal** — what to fold, for operator sign-off | `--plan` | regenerated every run; empty once merged |
| **registry** — what WAS folded, for write paths | — (nobody) | needed forever |

`fold_registry()` read the proposal role and was documented as the
registry role. Nothing recorded what had been folded; the map survived
only as long as nobody re-planned. `--plan` is the first command anyone
runs, so the window closed immediately.

Blast radius is a re-mint, not a corrupt write: with an empty registry a
filing whose holder spelling was already folded falls through to
`registry_index`, which keys by the same `name_key` and therefore
resolves it *this* time. The promise is what breaks — not today's rows.

## 2. The fix

**`helpers/graph/name_dedup.py`**

- `LEDGER_PATH = findata/Misc/_name_fold_ledger.jsonl`.
- `ledger_path_for(plan_path)` — derives the ledger from the plan stem,
  so the company and person arcs keep separate ledgers and a merged
  spelling never routes across arcs:
  `_name_merge_plan.* → _name_fold_ledger.jsonl`,
  `_person_merge_plan.* → _person_fold_ledger.jsonl`.
- `_append_ledger(folded, ledger_path)` — one JSON row per fold,
  `{"variant", "canonical", "folded_at"}`, VERBATIM variant (names may
  contain raw newlines, which only the machine form survives).
  Idempotent: an already-recorded `(variant, canonical)` pair is not
  re-appended, so re-running `--apply` on an unchanged plan appends
  nothing. Returns rows appended.
- `apply_plan(..., ledger_path=None)` appends after the DB write and
  reports `fold_ledger_appended` + `fold_ledger`; the stale-plan no-op
  branch reports `fold_ledger_appended: 0`.

**`helpers/core/entity_identity.py`**

- `DEFAULT_LEDGER_PATH`, `_ledger_folds(ledger_path)`, and
  `fold_registry(plan_path, ledger_path)` = ledger ∪ plan (the plan half
  keeps back-compat with a plan applied before the ledger existed, and
  is the live source within a plan-not-yet-applied session).

**`helpers/core/person_names.py`**

- `PERSON_LEDGER_PATH` and `person_fold_registry(path, ledger_path)` =
  ledger ∪ plan, the same union for the SHP resolver's fold map.

## 3. Behaviour neutrality — no eval gate

The change is a durability fix, not a semantic one, and that was
verified rather than assumed:

- `person_fold_registry()` — 3 entries before, 3 after (the ledger holds
  the same 3 identities the plan still carried).
- `fold_registry()` (company) — 0 before, 0 after (the plan was already
  header-only).

No roster, crosswalk, hierarchy or extractor rule moved, so the
ontology eval gate (`helpers/misc/ontology_eval_gate.py` over the frozen
question set) is not triggered. Nothing observable changes; the ledger
only guarantees the map going forward.

## 4. Residual gap — the 82 company folds are not backfillable

The ledger starts recording from its own deployment. The 82 company
folds applied 2026-10-16 predate it, and the pre-merge plan is gone:

```text
git show f2319c5f4:findata/Misc/_name_merge_plan.txt | grep -c '^== '   # 0
git show a8af1a917:findata/Misc/_name_merge_plan.txt | grep -c '^== '   # 0
git show HEAD:findata/Misc/_name_merge_plan.txt     | grep -c '^== '    # 0
```

The 82-cluster plan existed only in the working tree between `--plan`
and `--apply` and was never committed, so the variant spellings are
unrecoverable from the repositories.

This is acceptable, and for a structural reason: **every fold cluster is
same-`name_key` by construction**. `cluster_names()` groups by
`normalise(n)`, which delegates to `entity_identity.name_key`
(`helpers/graph/name_dedup.py:105-111`, `:57-61`), so a variant and its
canonical always share one key — and `registry_index` returns the same
canonical for that key. The ledger is therefore belt-and-braces plus an
audit trail for the pre-ledger generation, not the sole safety net.

The person arc **was** backfillable — its plan still held the 3 applied
HUF folds — and was seeded:

```text
Pradeep Kumar Agarwal (HUF)  → Pradeep Kumar Agarwal
Rajendra Kumar Gupta Huf .   → Rajendra Kumar Gupta
Rohit Kedia(Huf)             → Rohit Kedia
```

## 5. Artifact contract

`findata/Misc/_*_fold_ledger.jsonl` is **pipeline state that must
survive a fresh clone**, the same class as the two baselines
(`_name_dedup_baseline.txt`, `_person_dedup_baseline.txt`, without which
`check()` degrades to informational and the gate silently disarms), the
plan artifacts `fold_registry` reads at runtime, and
`findata/Misc/relation_aliases.json` (which carries an explicit
`.gitignore` exception for exactly this reason). Append-only means the
file grows monotonically; a fold is never retracted in place.

## 6. Log

- 2026-10-16 — defect found while answering the operator's
  git-tracking question about the `findata/Misc` dedup artefacts;
  `fold_registry entries: 0` against `registry_index entries: 24,832`
  after the 82-cluster company merge.
- `helpers/graph/name_dedup.py`, `helpers/core/entity_identity.py`,
  `helpers/core/person_names.py` — ledger, readers, wiring.
- `tests/test_name_dedup.py::test_apply_extends_ledger_and_survives_replan`
  — pins the contract: `fold_ledger_appended == variants_folded > 0`;
  after `write_plan` empties the plan (`-> fold` absent) the ledger alone
  still resolves; a re-apply against the regenerated plan appends
  nothing. Also cleared a pre-existing unused local (F841) in the same
  file.
- Targeted: `pytest tests/test_name_dedup.py tests/test_person_dedup.py`
  → 13 passed. `ruff check` + `ruff format --check` clean.
- Person ledger backfilled (3 rows); company ledger left empty by
  necessity (§4). Committed by the operator in `7773be804`.
