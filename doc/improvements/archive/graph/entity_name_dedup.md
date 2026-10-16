---
title: "Entity-name dedup — one canonical name per real-world entity across graph_edges"
status: executed
filed: "2026-10-15"
executed: "2026-10-16"
completed_md: "398"
area: "helpers/graph"
---

## 0. TL;RA

7.1% of the graph's node names are near-duplicates of another node for
the same real-world entity: 1,575 collision clusters over 22,067
distinct names, touching 7,383 edge rows (11.2% of 65,898). The S4b
unlock run proved the cost is not cosmetic — the strongest live
suggestion (Cholamandalam FH ↔ E.I.D. Parry, operator-confirmed
correct) surfaced *through* a three-way name variant
(`E.I.D.PARRY (INDIA)` / `E.I.D.Parry (India)` / `E.I.D. Parry
(India)`), and exact-name dedup could not collapse the variants.
Every name-keyed surface pays this tax: suggest_relations dedup, A²
pairing, co-mention pairing, fuzzy match, same_group rollups, the
frontend graph.

**Verdict sought**: execute the merge in three gated slices — census +
merge plan (dry-run artifact), merge mechanics (re-point + cache
refresh), ingestion prevention (normalize at write time, the #293
person-resolver pattern extended to companies) — plus a standing
dup-census gate so the count can only go down.

## 1. Census (2026-10-15, memory/research.db graph_edges)

Normalization key: lowercase, punctuation → space, whitespace
collapsed.

| metric | value |
|---|---|
| distinct node names | 22,067 |
| collision clusters | 1,575 |
| cluster sizes | 1,428×2, 125×3, 18×4, 3×5, 1×6 |
| edge rows touching a variant | 7,383 / 65,898 (11.2%) |
| footnote-`**` names | 45 (parser-leak class) |

Variant classes, by eye and by edge-type mix of the touched rows
(supplier_to 2,452, same_group 2,240, subsidiary_of 2,181, jv_with 259,
co_mentioned_in 94, …):

1. **Case** — UPPER bulk-import forms vs Title-case newsletter forms
   (`SCHNEIDER ELECTRIC SACHSENWERK GmbH` ×5 variants).
2. **Punctuation/spacing** — `E.I.D. Parry` / `E.I.D - Parry` /
   `EIDPARRY`; `IndianOil - Adani Gas` ×4.
3. **Footnote markers** — `BNP Paribas Bank**` / `**` suffixes (45
   names): newsletter bold/table markers leaking into entity names —
   a parser bug class, not an ER judgement.
4. **Parenthetical qualifiers** — `Godrej Household (Products)
   Bangladesh` vs unparenthesised.

Largest cluster: `BNP PARIBAS BANK` family, 6 variants.

## 2. Store topology (what a merge must touch)

`memory/research.db` sqlite `graph_edges` is the owning store
(names inline in source/target — there is no node table). The duckdb
`e_*` tables in `memory/graph.duckdb` are 1:1 caches (verified:
e_supplier 16,584 = sqlite supplier_to 16,584) rebuilt by the graph
query path; `v_company`/`v_node` ride the same cache estate. A merge
re-points sqlite rows, then refreshes caches — never edits caches
directly.

### S2 — executed 2026-10-16 (operator go)

Backup first: `snapshots/pre_name_merge_research_20261016_081440.db`
(289 MB, 65,898 edges verified). **Removed 2026-10-16 after the merge
verified clean** — it was the only raw `.db` tracked under `snapshots/`,
which `.gitignore` marks local scratch; the canonical recovery path is
`make snapshot-restore` from the git-tracked `snapshots/parquet/`, already
re-exported after the merge. Rollback, if ever needed, is a restore from
that parquet snapshot + `helpers/graph/query.py rebuild` (the blob itself
survives in git history under the commit that introduced it).

Two live constraints surfaced and handled inside `apply_plan`
(unit-pinned before the run):

1. `UNIQUE(source, target, edge_type)` — relations already stored under
   BOTH spellings: the colliding variant row consolidates into the
   canonical row (1,809 such rows).
2. `CHECK (source != target)` — edges BETWEEN two spellings of the same
   entity (a company "supplying" its own variant): dropped as
   intra-variant artifacts (part of the 1,809).

A third discovery: **8 entity names contain raw newlines** — a
line-format plan artifact cannot carry them, and the first apply pass
left 8 residual clusters. Fix: the plan gained a machine form
(`_name_merge_plan.jsonl`, verbatim names); the .txt stays
human-reviewable; apply folds by verbatim name from the JSONL.

**Result**: 1,656 variants folded (257 rows re-pointed, 1,809
collisions/self-loops collapsed — 65,898 → 64,089 rows,
`zero_loss_ok` true, no semantic relation lost); 1,656 registry rows
folded (26,162 → 24,506 entities, 0 retained-referenced); duckdb
cache rebuilt (e_supplier 16,584 → 16,485 = sqlite exact). Post-merge
census: **0 mergeable clusters** (22,067 → 20,403 distinct names); 28
person clusters remain excluded (person arc); 23 transpositions
report-only. Baseline set: `findata/Misc/_name_dedup_baseline.txt` =
0 — any future cluster is a writer regression (`make dup-census`).

Consumer post-check: the Chola↔E.I.D. Parry suggestion is now
**natively suppressed** (the typed edges exist under one name); the
fresh supplier-peer top-25 surfaced the post-merge structure
(Tata/Adani group siblings sharing suppliers) — appended to
`_pending_suggestions.txt` for human triage.

## 3. Slices

### S1 — merge plan (dry-run artifact, no writes)

`helpers/graph/name_dedup.py`:

- `normalise(name)` — the census key, plus token-sort as a secondary
  cluster key (`Adani Gas IndianOil` ≡ `IndianOil Adani Gas`).
- `merge_plan()` — clusters + canonical choice per cluster. Canonical
  rule, first match wins: (a) name matching a listed_on_index entity
  exactly; (b) highest total edge degree; (c) lexicographic (stable
  tie-break). Non-name-bearing variants (`**` suffixes) can never win.
- Plan written to `findata/Misc/_name_merge_plan.txt` (human-reviewable:
  cluster, canonical, variants, edge counts). **Gate**: operator review
  of the plan before S2 runs; the plan is the audit trail.

### S2 — merge mechanics (gated on plan sign-off)

- Re-point `graph_edges.source/target` variant → canonical (parameterised
  UPDATE per cluster; exact-duplicate edge rows collapse — expected and
  reported).
- **Registry fold (discovered during implementation, 2026-10-15)**: the
  dup disease lives one layer up too — the `entities` registry holds
  spelling variants as separate rows with distinct `normalized_name`s
  (`EID_Parry_India` / `EIDPARRY_INDIA` / `EIDParry_India` for one
  company), and the vigil sync mints a new entity per spelling when its
  `normalized_name` lookup misses. A graph_edges-only merge is therefore
  REVERSIBLE: the next sync re-creates the variants from the registry.
  S2 must fold both: `graph_edges` re-point AND `entities` rows
  (canonical name survives; variant rows deleted after edge re-point;
  the plan artifact records every fold). Implemented in
  `helpers/graph/name_dedup.py --apply --yes`.
- Cache refresh via the existing rebuild path; verify e_* row deltas
  match the plan.
- **Acceptance**: zero edge loss (row count − exact-duplicate collapse =
  plan's prediction); every cluster collapses to one node name in a
  post-merge census re-run; Chola/EID Parry-class pair now dedupes
  natively; suggest_relations + A² re-runs show the merged graph.
- Aliases: canonicalisation is lossless-by-registry — the plan file
  records every folded variant, so downstream name lookups (and future
  re-imports) can map old → new. No alias table is introduced in this
  slice; the registry IS the plan artifact.

### S3 — ingestion prevention (the durable slice)

Implemented 2026-10-15 in `helpers/maintenance/related_party_sync.py`
(the vigil boundary — the census's dominant variant source), plus
`helpers/graph/name_dedup.py`:

- **Footnote markers stripped at extraction** (`_safe_display`): a
  `**`-class variant can never be born.
- **Resolve-before-insert**: `apply_pairs` / `apply_supply_pairs`
  resolve an unresolved counter-party through (1) the plan artifact's
  fold map, then (2) a census-key index over the `entities` registry
  (`name_dedup.normalise`) — strictly stronger than the
  `normalized_name` lookup that was missing these hits. Only when both
  miss does a new entity get minted. No auto-merge of unseen
  near-hits — ER judgement stays with the reviewed artifact.
- **Acceptance**: re-importing the same source twice yields no new
  variant rows; the `**` class cannot recur (unit-pinned);
  census-key/fold-map hits reuse the existing entity (tested).

### S4 — standing dup-census gate

- `db_maint` sidecar section or a `make` check: collision-cluster count
  recomputed on the live graph; alert when it grows above the
  post-S2 baseline. The count can only go down; a rise means a writer
  is regressing.

## 4. Non-goals

- No fuzzy auto-merge of genuinely distinct entities (`HDFC Bank` vs
  `HDFC Securities` must never collapse — the normalisation key is
  deliberately conservative; near-misses queue for humans).
- No person-entity changes here — **enforced by the S1 filter, not
  just convention**: person-shaped clusters (honorifics, initial runs,
  holder phrases, Quotes-speaker matches; listed entities exempt) are
  excluded from the census and the plan, and the S2 apply re-checks
  defensively. The person analog is its own arc:
  `person_name_dedup.md` (identity registry with the legal-person-line
  model — an HUF never merges into the individual).
- No frontend/schema changes; the graph contract is untouched — only
  fewer distinct names.

## 5. Follows

- `doc/improvements/archive/graph/person_resolver_lane.md` (#293) —
  the resolve-before-insert pattern S3 extends.
- `doc/improvements/archive/graph/scipy_csgraph_scale_lanes.md` §9b —
  the S4b execution that surfaced the issue, with the ER-boundary
  finding recorded.
- `doc/improvements/completed.md` #218 (word-overlap alias guard) —
  the relation-level analog; this proposal is the node-name level.
