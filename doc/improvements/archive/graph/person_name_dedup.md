---
title: "Person-name resolution — identity registry, dedup, and speaker attribution"
status: executed
filed: "2026-10-15"
executed: "2026-10-16"
completed_md: "399"
area: "helpers/core"
---

## 0. TL;RA

The graph's person surface is three disjoint populations that all
suffer the same disease `entity_name_dedup` just treated for companies:

| population | count | where | shape |
|---|---|---|---|
| SHP holder stubs | 30 | `entities` (entity_type=person) | `MANHAR GORDHANDAS GANDHI`, `MANHAR G GANDHI (SMALL )HUF` |
| person-shaped graph clusters | 28 | `graph_edges` (excluded from the company merge) | `D. Sivanandhan` / `D Sivanandhan`, `Deepak Agarwal (HUF)` / `Deepak Agarwal HUF` |
| Quotes speakers | 188 | `findata/Super_Sectors/Quotes.md` attribution lines | `— Akash Minda, Executive Director` |

Zero overlap between holders and speakers today. The #293 lane
(`person_names.py`) resolves holders at INSERT time; nothing owns
person-name DEDUP, speaker identity, or the HUF boundary. This proposal
is the person analog of the company arc: same slice shape — registry +
census, gated merge, ingestion prevention, standing gate — with one
load-bearing difference: **the identity model must separate legal
persons (individual vs HUF vs trust) and forbid cross-line merges**, a
hazard companies don't have.

## 1. Identity model (the load-bearing difference)

- **Legal-person lines**: individual, HUF, trust, body-corporate-as-
  person. `(SMALL )HUF` suffixes mark a DISTINCT legal person — merging
  `MANHAR G GANDHI (SMALL )HUF` into `MANHAR GORDHANDAS GANDHI` is
  forbidden even though the names scream same-family (#293's founding
  example). Merge WITHIN a line only; the line is part of identity.
- **Initial collapse is evidence-gated**: `MANHAR G GANDHI` →
  `MANHAR GORDHANDAS GANDHI` only with co-evidence (same company
  context, same role/relationship class, no contradicting dates).
  Auto-folding initials is how two cousins become one promoter.
- **Roles are attributes, not identity**: "Executive Director",
  "Co-Founder & CEO" (the Quotes corpus carries titles on every
  attribution) attach to a person-in-context, never merge people.
- **Listed entities are never persons** (the company arc's exemption,
  mirrored here — `Dr. Reddy's` subsidiaries must not resolve into
  the founder).

## 2. Slices

### S1 — registry + census (dry-run)

`helpers/core/person_names.py` extends (it already owns insert-time
holder resolution):

- Harvest all four sources: person entities (30), SHP holder names
  (the filing feeds), Quotes speakers (188), and the 28 graph_edges
  person clusters (the `name_dedup.is_person_name` filter already
  isolates them).
- Normalised identity keys: case/punct-fold (the company census key)
  PLUS legal-line extraction (`(HUF)` / `(SMALL )HUF` suffixes strip
  into the line attribute, not the name key).
- Artifact: `findata/Misc/_person_merge_plan.txt` — within-line folds
  (safe, e.g. `D. Sivanandhan` ≡ `D Sivanandhan`), evidence-gated
  expansions (each carrying its co-evidence for review), and the HUF
  pairs listed as NEVER-MERGE. **Gate: operator review before S2.**

### S1 — executed 2026-10-16 (plan awaiting operator review)

`helpers/graph/person_dedup.py` (the census/plan/check CLI; apply
reuses `name_dedup.apply_plan` with `skip_persons=False`) + the plan
artifacts (`findata/Misc/_person_merge_plan.txt` + `.jsonl`):

- **24 fold clusters** — within-line variants only: 21 person-line
  (`D. Sivanandhan` ≡ `D Sivanandhan`, honorific/spacing/footnote
  forms) + 3 huf-line (`Deepak Agarwal (HUF)` ≡ `Deepak Agarwal HUF` —
  within the HUF line, which is allowed; the LINE is the identity).
- **0 never-merge** today (no cross-line base collisions live) — the
  class is built and tested (the #293 hazard: an `X (HUF)` never folds
  into `X`).
- **4 company-shaped clusters routed back** to the company arc by hand
  (`A S Packers`, `M.G.T Cements`, `MABELLA TRUSTEE SERVICES`,
  `Dr. Reddy's Laboratories Chile SPA`) — S1 filter over-inclusion,
  listed in the plan's review section, never folded here.
- **Registry verified clean**: the 30 SHP stubs carry zero same-line
  collisions; the 188 Quotes speakers zero.

S3 ingestion hook landed:
`person_names.person_fold_registry()` (the signed plan as the fold
map) consulted by `shareholding_sync.apply_candidates` before minting
a person entity — the resolver and the dedup share one artifact and
cannot drift. `make person-census` = the S4 gate (baseline pending
post-S2).

### S2–S4 — executed 2026-10-16 (operator approved the plan)

- **S2 live**: 25 variants folded across the 24 clusters — colliding
  rows consolidated (25), registry rows folded (25, 0
  retained-referenced), zero loss (64,090 → 64,065); duckdb cache
  rebuilt. Post-merge: **0 fold clusters**; the HUF boundary held
  (`Deepak Agarwal` the individual and `Deepak Agarwal HUF` the HUF
  both survive — only the `(HUF)` spelling variant folded). Baseline
  set: `_person_dedup_baseline.txt` = 4 (the company-shaped review
  clusters) — `make person-census` gates growth.
- **S3 caveat (residual)**: the shareholding holder-minting hook is
  live (`person_fold_registry` consulted before minting);
  **`derive_insights` speaker-attribution resolution is NOT wired yet**
  — Quotes speaker names still land as written. Follow-up slice.
- **S4**: `make person-census` gate live (baseline 4; the 4 are the
  company-shaped review clusters — they clear when hand-merged into
  the company arc).

**2026-10-16 addendum — operator ruling: the 4 are unlisted
subsidiaries, not persons.** Company markers now VETO the person shape
(`entity_identity.is_person_name`), the company plan re-merged the 4
clusters (6 rows consolidated, 5 entities folded, zero loss), and both
censuses sit at absolute zero — 20,373 distinct names from 22,067 at
the arc's start; both baselines 0 (`make dup-census` + `make
person-census` green). The S6 speaker-attribution slice landed same
day (see above).

Note: the folded variant rows were mostly registry-typed `company`
(holder names from bulk filings) — the person arc folded duplicate
NAME rows; re-typing person entities is the resolver arc's business.

### S6 — speaker attribution (2026-10-16, implemented same day)

`entity_identity.resolve_speaker()` — signed person-plan fold →
Quotes-corpus display form → as-written — wired into
`derive_insights._create_quote`: attribution spellings whose key
matches resolve to the canonical display at extraction time ("Akash
Minda." → "Akash Minda"); unknown names pass through untouched (the
`_ATTR_RE` capitalized-token shape still gates what reaches the
resolver). Unit-pinned at both layers (resolver + extraction).

### S2 — merge mechanics (gated on plan sign-off)

- Fold within-line variants across `entities`, `graph_edges`
  (holder-sourced `invested_in` edges), and the SHP store, mirroring
  the company arc's zero-loss accounting.
- Evidence-gated expansions apply only after explicit per-row
  acceptance in the reviewed plan.
- Quotes speakers fold into the same registry (speaker → canonical
  person, title kept as attribute).

### S3 — ingestion prevention

- `shareholding_sync` holder minting consults the registry before
  creating a person entity (the #293 `apply_candidates` pattern,
  tightened with the legal-line rule: a `(HUF)` suffix on either side
  blocks reuse).
- `derive_insights` attribution lines resolve speakers through the
  registry (extraction quality: title captured, name canonical).

### S4 — standing gate

Person dup-census count vs baseline, riding the same
`make dup-census` pattern (separate baseline file) — the count can
only go down.

## 3. Acceptance

- Toy oracle: within-line fold folds; HUF pair never folds; initial
  expansion without co-evidence stays UNMERGED and lands in the
  review queue.
- Live: `D. Sivanandhan`-class pairs collapse; `Deepak Agarwal (HUF)` /
  `Deepak Agarwal HUF` collapse (same line); the 30 stubs keep their
  HUF separations; speaker rows in Quotes resolve to registry names.
- Registry integrity: no person entity row deleted while an edge
  still references it (the company arc's retained-referenced report).

## 4. Non-goals

- No DIN/MCA scraping (blocked upstream — #293's 403 finding stands).
- No fuzzy auto-merge across legal-person lines, ever.
- No person-node graph expansion (directorship edges stay deferred —
  no appointments source).

## 5. Follows

- `doc/improvements/archive/graph/person_resolver_lane.md` (#293) —
  the insert-time resolver this arc extends to dedup.
- `doc/improvements/archive/graph/entity_name_dedup.md` — the company arc;
  its S1 person filter is the census source for this proposal, its
  merge/gate mechanics are the template.
- `doc/improvements/completed.md` #267 — the SHP holder lane that
  created the 30 stubs.
