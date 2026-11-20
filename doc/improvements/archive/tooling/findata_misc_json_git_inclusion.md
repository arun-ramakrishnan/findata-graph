---
title: "Git-tracking criteria for findata/Misc operator JSON surfaces"
status: executed
filed: "2026-11-20"
executed: "2026-11-20"
completed_md: "547"
area: ".gitignore + findata/Misc/*.json"
---

# Git-tracking criteria for findata/Misc operator JSON surfaces

**Date:** 2026-11-20 · **Status:** EXECUTED · **Area:** `.gitignore`, seven
`findata/Misc/*.json` files

## 1. Why this proposal exists

`.gitignore:34` blanket-ignores `findata/**/*.json`, then whitelists a short
list of exceptions (`.gitignore:35-56`). The comments on those exceptions state
the underlying rule — files that are **pipeline state loaded at runtime** or
**must survive a fresh clone** get whitelisted; regenerated run artifacts and
scratch do not. But the rule was never written down as a reusable *criterion*,
so seven operator surfaces silently accumulated outside git despite producer
docstrings that call them "git-tracked" (`helpers/graph/derive_countries.py:139`,
`helpers/graph/derive_indices.py:776-778`).

This proposal records **(a)** the inclusion criterion, **(b)** the audited
inventory of all seven surfaces with the decision for each, and **(c)** the
staleness findings and regeneration decisions — so a future session does not
have to re-derive why these files are in the repo, or accidentally regenerate
one that carries irreplaceable human decisions.

## 2. The inclusion criterion (the thing not to lose)

Track a `findata/**/*.json` **iff** it is either:

1. **Runtime input** — code reads it at runtime; a fresh clone without it
   degrades a check or pipeline (e.g. `swap_fk_integrity.py` reading the FK
   manifest), **or**
2. **Human-curation carrier** — it holds operator decisions (skips, accepted
   aliases, taxonomy judgments, coding worklist promotions) that **cannot be
   regenerated** from the vault or the DB because the journal that produced
   them is gone or the artifact is one-shot.

Do **not** track (stay ignored): regenerable run artifacts, scratch, and dated
one-offs — `_pending_relations.txt`, `_pending_suggestions.txt`,
`_pending_triage_decisions.jsonl`, `_pending_triage_report.md`,
`quote_triage_decisions.jsonl`, `quote_triage_report.md` (ignored at
`.gitignore:58-60,145-147`), `_rename_queue_20261016.json` (dated one-off),
`speaker_supplement_draft.json` (draft scratch).

Supporting doctrine: `doc/procedures/review-kit.md` §"Export-side parking
(S4)" — `country`/`subsector`/`counterparty` worklists are consumed by hand and
get the parking lane; `retro_resolution_worklist` is "deliberately NOT wired: a
one-shot curation artifact." Harness memory: "findata JSONs = operator-worklist
whitelist only."

## 3. Audited inventory — all seven should be tracked

| File | Class (criterion) | Content (2026-11-20) | Evidence |
|---|---|---|---|
| `duckdb_research_swap_cascade_manifest.json` | Runtime input | 22 FK edges, owners (S3) | `helpers/misc/swap_fk_integrity.py:29`, `swap_convert.py:20,495` read it; a fresh clone without it breaks swap integrity checks. **Urgent.** |
| `country_worklist.json` | Human surface (regenerable) | 19,141 entries | `derive_countries.py:136-162` writes it; ticker fills consume it; parked journal empty → no curation to lose. |
| `index_worklist.json` | Human surface (regenerable) | 38 constituents | `derive_indices.py:775-789`; docstring calls it "git-tracked operator surface". |
| `subsector_worklist.json` | Human-curation carrier | 6 unmapped, each with a human `suggestion` | `derive_hyperedges.py:1111-1135`; taxonomy decisions. |
| `nic_worklist.json` | Human-curation carrier | 33 suggested / 86 promoted / 1 no-signal | `seed_nic2008.py:960-1078` — candidate closeMatch decisions. |
| `retro_resolution_worklist.json` | Human-curation carrier (one-shot) | 47 accepted aliases + operator flags, `curation_2026_09_15` | No generator exists (`git log`/grep confirm); purest "must survive a fresh clone" case. |
| `counterparty_worklist.json` | Human surface (regenerable) | 0 unresolved / 4 parked | `derive_hyperedges.py:602-618`; same 5-worklist family; track for consistency. |

All seven are currently under the blanket ignore. The five worklists are also
the "operator-worklist whitelist" family already partially represented by the
existing exceptions at `.gitignore:37-56`.

## 4. Staleness audit and regeneration decisions (2026-11-20)

Each file was compared against a **read-only** re-derivation from the live
`memory/research.db` (363,528,192 B / 25,200 companies / 19,127 no-ticker),
writing candidates to `$TMPDIR` only. Decisions:

| File | Finding | Decision |
|---|---|---|
| cascade manifest | Current — edited same day (S3); 22/22 edges owned | unchanged |
| index_worklist | Fresh — 38 = live dry-run | unchanged |
| subsector_worklist | Fresh — 6 = live dry-run | unchanged |
| retro_resolution_worklist | One-shot, no generator | unchanged (kept as-is) |
| country_worklist | Grossly stale: 235 (2026-09-09) → 19,141; only 122/235 still exist; parked journal empty | **regenerated** → 19,141 |
| nic_worklist | Stale: `skipped:30, suggested:0` came from `outputs/nic_review/journal.jsonl`, which **no longer exists**; fresh = `skipped:0, suggested:33, promoted:85→86, labels:117→120` | **regenerated** → 0 skips / 33 suggested (operator accepted loss of the 30 orphaned skip decisions) |
| counterparty_worklist | Minor drift: 1 stale entry `Ghost CP Co`; live = 0 unresolved / 4 parked | **regenerated** → 0 unresolved |

**Caveat to carry forward:** `nic_worklist.json`'s 30 skip decisions are
irrecoverable — their source journal is gone. If those decisions matter later,
they must be re-made by hand; do not expect a regeneration to restore them.
`retro_resolution_worklist.json` must **never** be regenerated (no writer;
regeneration would delete the embedded curation).

Regeneration used each producer's pure worklist-export function
(`derive_rows`/`write_worklist`, `export_worklist`, `resolve_counterparties` +
parking journal) with a **read-only** DB connection — no entities/edges were
written to the live store.

## 5. Execution plan (operator-gated)

1. Add seven whitelist exceptions after `.gitignore:56` under a comment naming
   this criterion (runtime input OR human-curation carrier).
2. `git add` the seven files. The three regenerated files were never tracked,
   so they land in full — `country_worklist.json` alone is ~19k entries (large
   diff, expected).
3. Run `make search-fresh APPLY=1` (advisory) — the regeneration + `.gitignore`
   change touch indexed surfaces.
4. Record the outcome in `completed.md` on execution; no eval-gate bullet is
   required — adding data files to git does not alter query-visible semantics
   (rosters / crosswalks / hierarchies / extractor rules).

## 6. Acceptance criteria

- All seven files report `git check-ignore` = not ignored and appear in
  `git ls-files`.
- No regenerable scratch file from §2 becomes tracked.
- `nic_worklist.json` and `retro_resolution_worklist.json` carry their
  documented curation state; `retro` is byte-identical to its pre-proposal
  content.
- `static_checks` rc=0 after the `.gitignore` edit.

**Gate posture:** operator owns git/stg — no `git add`/commit/stg by the agent;
this proposal documents criteria only. Staging is the operator's action.

## Progress log

- 2026-11-20 (agent): step 1 EXECUTED — seven whitelist exceptions added after
  `.gitignore:56` under the criterion comment; `git check-ignore` clean for all
  seven, §2 scratch files still ignored. Step 3 EXECUTED —
  `make search-fresh APPLY=1 --allow-stale` (git_search stamped commit
  rewritten mid-arc: HEAD c7a92bbb1 replaced 202567d5, routine operator
  commit); plain `make search-fresh` check rc=0 afterwards; `static_checks`
  rc=0 (3 advisory). Steps 2 and 4 remain operator actions (git add +
  completed.md entry on commit).
- 2026-11-20 (operator + agent): step 2 DONE — operator committed all seven
  (verified tracked in `git ls-files`, tree clean). Step 4 DONE — completed.md
  entry #547 filed with the archival change; proposal moved to
  `doc/improvements/archive/tooling/` (status flipped in the same change).
