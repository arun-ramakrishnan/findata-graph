---
title: "Triage hygiene: word-overlap alias guard + discard-persistence noise gate"
status: executed
filed: "2026-09-09"
executed: "2026-09-09"
completed_md: "218"
area: "helpers/graph"
---

# Triage hygiene: word-overlap alias guard + discard-persistence noise gate

**Date:** 2026-09-09 · **Status:** PROPOSED · **Area:** helpers/graph (`triage_pending_relations.py`, `suggest_relations.py`, `extract_relations.py`)

Two pick-ups from `doc/local/future_items.txt` (§G2, §G3), both newly
unlocked by the #215/#217 arcs. One hygiene slice (G2) and one operational
slice (G3); no machinery behavior change.

§G1 (`listed_on_index` membership edge) was reviewed and **deferred** — see
§7. It is not part of this proposal.

## 1. Motivation

**G2 — word-overlap alias guard (small hygiene).** The `suggest_relations` /
    `triage_pending_relations` alias-candidate bucket is a known
    false-positive source. `future_items.txt` §G2 records the evidence
    doubling: after the 09-05 trio (20 Microns → Micron,
    Sailing_the_Tide → SAIL, Manufacturing), #217's S3 rejected two more
    word-overlap alias FPs (Circle, American_Express hints). Known misfire
    taxonomy: edition-note entity, sector, different real company,
    self-target. Harvest-triage is now routine (#217), so the guard pays
    every run.

**G3 — discard-persistence noise gate (operational).** §G3: the #169 / #217
    re-entry lesson observed live — the 10 discarded noise rows came straight
    back on the next extract because plain discards persist nothing. The queue
    is 0/0 today, but harvest-triage is now routine, so every future run
    refills with already-adjudicated rows unless discards persist. Lighter than
    full B2 sidecars: a runtime-loaded discard gate consulted by
    `extract_relations` at write time.

## 2. Evidence (measured 2026-09-09, this box)

| Check | Result | Verdict |
|---|---|---|
| `future_items.txt` §G2 | 5 word-overlap alias FPs across two runs | taxonomy is real, not theoretical |
| `triage_pending_relations.py` `_bucket()` | `alias_candidate` returned when `fuzzy_match` resolves to a DIFFERENT existing name, `method != "spellfix"` | the only path that emits the bucket |
| `--report` render | alias_candidate rows printed with `(method, score)` detail, no confirm/verify prompt | operator has no signal the suggestion is word-overlap-only |
| #217 S3 | 0 aliases persisted (all alias_candidate hints were FPs) | manual triage already rejects them, but only by eyeball |
| `derive_insights` bare dry-run | 3.65 s wall on 13× corpus (12 s perf budget) | B5 fused-kernel trigger (minutes) NOT met |
| `extract_relations.py:1761` | `noise_target()` already gates countries/generic/fragments at write time | the ~35-row class never reaches the sidecar — G3 extends this to operator discards |
| `relation_aliases.json` | 20 entries, runtime-loaded via `_alias_overrides()` | the exact pattern G3 mirrors for discards |
| `triage_pending_relations._apply_write` | plain `discard` decisions drop rows but persist nothing | the #169 / #217 re-entry lesson: 10 discarded noise rows came straight back |

## 3. Design

**S1 — word-overlap alias guard (G2).** Add a `word_overlap` flag to the alias-candidate path so the report marks word-overlap-only candidates as **confirm** rather than silently presenting them as aliases.

- In `_bucket()`, detect when the fuzzy match's method is word-overlap / jaccard (the family that produced all 5 FPs) and set `confirm: true` on the row.
- Report render: word-overlap candidates get a distinct line prefix (e.g. `_confirm?_`) so the operator distinguishes them from genuine re-spelling aliases.
- Downrank alias_candidates whose fuzzy target is an edition/sector note rather than a company (the #217 lesson: Circle / American_Express hints pointed at non-company nodes).

**S2 — discard-persistence noise gate (G3).** A runtime-loaded discard gate, mirroring the existing `_alias_overrides()` pattern (which already loads `relation_aliases.json` once per process):

- `extract_relations.py`: add `_noise_overrides()` over `findata/Misc/relation_noise.json` — a set of `(edge_type, source, target_mention)` triples, keyed on the normalized mention so re-spellings of the same noise target still hit. Consulted at write time alongside the existing `noise_target()` gate (line 1761), so operator-rejected rows never reach the sidecar.
- `triage_pending_relations.py`: on `--apply-decisions`, `_merge_noise_file()` persists `discard` decisions into the same file (merged, deduped, sorted for stable diffs — same discipline as the alias file). Aliases / stubs / accepts are not noise-gate material (they resolve or create real edges), so only `discard` rows land here.

Rejected: full B2 sidecars (revisit only if accepted edges become annoying to audit in `graph_edges` alone); changing extractor patterns or predictor thresholds (rule-candidates wait for recurrence — per #217 proposal §3 S3).

## 4. Acceptance criteria

1. `triage_pending_relations.py --report` on a queue containing the 5 known FPs renders each with the confirm marker; no word-overlap row presents as a plain alias.
2. Re-run #217's S3 harvest queue through the guard: 0 aliases persisted, all word-overlap rows flagged.
3. G3: `--apply-decisions` with a `discard` decision writes the normalized triple to `relation_noise.json`; a subsequent `extract_relations` run suppresses that exact triple (and not an unrelated one); `make graph-rebuild` clean.
4. `make qa` green on touched suites; no behavior change to `--apply-decisions` output.

## 5. Risks

- **Confirm fatigue** — if every alias candidate needs confirming, triage gets noisy; mitigation: only word-overlap method gets the marker, genuine spellfix/jaccard-neighbour stays as-is.
- **Anchor drift** — line refs in `_bucket()` move on edit; mitigation: symbol-only refs where the lane supports them.
- **Noise-gate drift** — a rejected triple that later becomes legitimate must be removed by hand; mitigation: the gate is keyed exactly on (edge_type, source, target), so removal is a one-line edit, and the file is git-tracked data, not code.
- **Gate over-reach** — a noisy gate could suppress a genuine unresolved row; mitigation: exact triple match, not a prefix; the existing `noise_target()` classifier still runs first for the country/generic/fragment class.

## 6. Non-goals

- B2 relation sidecars (future_items §A2) — separate arc.
- Archive doc-drift outside the selected surface.
- Any change to extractor patterns, predictor thresholds, or `--apply-decisions` semantics.
- Cold-embed §7 GPU levers, vault_scaling T1, Security Phase 4, MCP server / OpenViking (all trigger-gated or operator-parked).

## 7. Deferred: §G1 `listed_on_index` membership edge

Reviewed 2026-09-09 and deferred — not part of this proposal, and the
enrich pass it imagined does not exist.

- **The key is a dropped key, not an empty one.** `doc/okf/frontmatter.company.v1.json:133` types `index_membership` as `"type": "null"` — "Dropped key (2026-07-28); tolerated as null on legacy notes, absent on new ones." Writing `index_membership: true` would fail the OKF conformance check. All 9 notes that carry it have `null`; the key was dropped in L2-findata (completed.md #120) because it was 99.4% empty (6/1,031 populated at the time).
- **The signal doesn't exist.** The premise was that the derive render arc's per-note block plans (`_KfPlan`) would let us *add* the key. But "is this company a member of an index" (Nifty 50, Sensex, a sector index) is not produced by any pipeline in this repo. No note-bulk-edit produces it.
- **Tickers prove the wrong thing.** 945/1,165 companies (81.1%) carry a ticker, 850 are India-exchange (.NS/.BO/.BSE/.NSE). That answers "listed on an *exchange*", not "listed on an *index*". Deriving the edge from tickers would label a 945-edge relation with a name that means something else — worse than the 9-edge relation it replaces.
- **Revisit trigger:** only if a real index-membership data source appears (an index constituent feed, or an extraction pass that resolves companies to indices). Until then the 9-edge relation stays as-is and `listed_on_index` is not built.
