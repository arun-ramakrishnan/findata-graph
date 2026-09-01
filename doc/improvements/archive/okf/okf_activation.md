---
title: Activating OKF Metadata — Coverage Analytics, Source-Driven Incremental Derivation, `cited_in` Provenance Edges
status: executed
filed: '2026-08-19'
executed: '2026-08-19'
completed_md: '134'
area: okf
---

# Proposal: Activating OKF Metadata — Coverage Analytics, Source-Driven Incremental Derivation, `cited_in` Provenance Edges

**Status:** **EXECUTED 2026-08-19 — COMPLETE** (all of F0/P/C2/I below
shipped; see the per-step EXECUTED notes). The first `--stale-only`
apply ran the same day as the user's `notes_refresh` patch (323 company
notes re-rendered + re-stamped `derive_insights.py/v1`); the fixed
point is proven (`make derive-all`: 328 notes gated, 0 would write;
census 1,227 machine-confirmed, 196 past stale_after). End gate green
(qa 1,793 tests / perf 19/19 / advisory). Post-ship hardening, same
day: `make derive-insights` became a DRY-RUN `--stale-only` preview
(apply is explicit); maint-full's note-writing steps became gates
(sync-sector-links `--check`; build_sector_hierarchy `--check` gained a
region-scoped note-drift gate); `make derive-all` added as the
read-only family preview. Follow-up PROPOSED separately:
`okf_sources_maintenance.md` (the `sources[]` lifecycle gap + the
`--stale-only` evidence hole it closes). Shipped: completed.md #134.
**F0 EXECUTED**: `helpers/core/edition_index.py` (norm_key, note_title,
source_trees, source_note_index, resolve_edition_string, resolve_editions);
backfill rewired (19 tests; live dry-run 0-write). **P EXECUTED 2026-08-19** (live): 108 edition entities + 1,005
cited_in edges applied; idempotent re-run 0/0; DuckDB rebuilt
(`e_cited_in` 1,005 / `v_edition` 108; hub = A_Quarter_That_Refuses_
To_Behave at 360); snapshot regenerated (generation 25373, schema 10;
parquets refreshed — note the 33 tracked snapshot files carry
skip-worktree, so the refresh is invisible to `git status` and the two
NEW parquets `duckdb/e_cited_in` + `duckdb/v_edition` land untracked);
integrity check 0 invalid (Edition section green); analytics
top-entities shows no editions (exclusion working); context packs
render `—cited_in→` facts ranked last. **C2 EXECUTED 2026-08-19**:
`make analytics REPORT=coverage` — series × sector matrix (companies,
editions, quotes from edge `n_quotes`) over clean entity/note_tags/
cited_in joins, per-series rollup + joined/total hygiene line in the
note (live: the_chatter 74 editions/432 companies/2,236 quotes;
points_and_figures 12/62; the_plotlines 1/5; 1005/1005 edges joined).
C1 (fuzzy quotes bridge) is now SKIPPED by design — C2 supersedes it.
**I IMPLEMENTED 2026-08-19** (live apply pending user go-ahead):
`derive_insights.py --stale-only` — note-level gate
`_stale_only_skip` (skip iff `generated.by == derive_insights.py/v1`
AND sources[] non-empty AND max(last_modified) ≤ generated.at; None
evidence → render, Q3); wired into render_notes (now returns
`(written, skipped, gated)`) + render_metrics_notes (`(written,
gated)`); shared `_paths_by_entity` resolver extracted; summary lines
report gated counts; opt-in flag (Q2). Live dry-run: 0 gated — the
backfill stamps are not render stamps, so run #1 re-renders all 431
sourced notes and re-stamps them; gating engages from run #2).
**Gate run 2026-08-19 (end of cycle): make qa + perf (19/19) + advisory
all green.** Gate-caught fixes en route: edition_index shebang,
`_KNOWN_EDGE_TYPES` += cited_in, cache-consistency v_node kind list
+= edition, edition `normalized_name` = STEM (theme precedent — was
frontmatter title, broke the name-format invariant; 108 live rows
repaired + snapshot regen), normalization check exempts editions from
the PascalCase format rule (5 OCR stems legitimately break it; the
filename check got the same exemption).
**Remaining: the first live `--stale-only --apply` — DONE via the
user's `notes_refresh` patch; completed.md entry + archive move
executed 2026-08-19.**
**Date:** 2026-08-19
**Author:** Agent analysis (user-directed)
**Builds on:** the archived `okf_adoption.md` and
`newsletter_notes_adoption.md` (completed.md #130–#133): all 1,227 notes
carry `generated`/`stale_after`, 476 carry `sources[]` (471 edition links +
5 PDF links), the 108 source notes carry namespaced tags mirrored into
`note_tags` (216 rows).
**Scope:** `helpers/graph/analytics.py`, `helpers/graph/derive_insights.py`,
new `helpers/graph/derive_cited_in.py`, `Makefile`, tests, procedure docs.
Explicitly out of scope: `Reports/`, frontend UI, S5 `company/` coverage
tags (deferred), Q4 per-claim footnotes (deferred).

---

## 1. Summary

Three capabilities that turn the OKF frontmatter from **descriptive**
(census, advisories) into **operational** (SQL joins, targeted worklists,
traversable edges):

- **C — Coverage analytics**: a `make analytics coverage` report answering
  "which series covers which sectors/companies, and how deeply" from the
  git-tracked snapshot.
- **I — Incremental source-driven derivation**: `derive_insights.py
  --stale-only` re-renders only notes whose *evidence* (`sources[].
  last_modified`) changed since their last render, instead of the full
  1,100-note corpus.
- **P — `cited_in` provenance edges**: editions become graph nodes and
  every `sources[]` entry becomes a traversable edge, so provenance feeds
  the existing graph layer (context packs, analytics, link prediction).

Ordered by blast radius: C is read-only, I changes one writer's loop,
P populates `entities` + `graph_edges`.

---

## 2. Findings that shape the design

**F0 — Edition identity is fragmented (measured 2026-08-19).** The same
edition is keyed four ways today:

| Key | Where | Quality |
|---|---|---|
| note **stem** | `sources[].id`, wikilinks | clean, machine-written |
| note **path** | `note_tags.note_path`, `sources[].resource` | clean |
| frontmatter **title** | newsletter notes | prose-y |
| `quotes.as_of_edition` | quotes table (71 distinct) | free text — matches titles **28/71** exactly, stems 1/71 |

So `quotes` cannot be joined to `note_tags` without normalization. The
backfill already built the right tool: `_source_note_index`/`_norm` in
`helpers/misc/backfill_okf_provenance.py` (fuzzy title→stem resolution).
Proposal: lift that index into a shared helper (`helpers/core/`, used by
both) and make the **note stem** the canonical edition key everywhere
(it already is for `sources[].id` and wikilink resolution).

**F1 — Placement rules (from maint-vs-maint-full).** C is read-only
parquet (safe anywhere). I touches note bodies → standalone
`make derive-insights` only (maint-full already runs `--no-notes`; its DB
half stays full-rebuild). P writes `entities` + `graph_edges` → standalone
target **plus a paired DuckDB rebuild**, exactly the
`derive-themes`/`derive-themes-rebuild` pattern; never in maint-full.

**F2 — Edition entities are schema-legal but have a blast radius.**
`entities.entity_type` has no vocabulary CHECK (only the company-suffix
guard), so `entity_type='edition'` needs no migration. But +108 entities
and +659-ish edges shift everything downstream: snapshot parquet row
counts, `graph_analytics` degree distributions, `analytics` reports, and
`suggest_relations` feature extraction. Each needs an explicit
include/exclude decision (§3.3).

**F3 — Precedent.** `co_mentioned_in` edges already carry edition names —
but as edge *properties* (`{"edition": …, "newsletter": …}`), which keeps
editions non-traversable. That precedent is the thing P deliberately
breaks.

**F4 — C1 deep-dive (measured 2026-08-19, via the F0 helper, live).**
The quotes bridge is *nearly complete*: 67/71 distinct
`as_of_edition` values resolve (39 exact + 28 via variants/containment),
**99.4% of quote rows** (2,548/2,564) sit on resolvable editions, and
only 4 strings are honestly unresolvable (`Adani Green | Large Cap |
Energy`, `Blue Star`, `Tata Power`, `United Breweries` — report, don't
guess). **But the scope is narrower than the proposal implied: quotes
come from concall-chatter mining, which runs on The_Chatter only** —
the coverage matrix C1 produces is a single-series view (today: 38
series×sector cells, all The_Chatter; 332 distinct companies; top cells
FMCG/Automotive/Engineering at ~30). Full three-series coverage lives
in `sources[]`, not `quotes`.

**F5 — P deep-dive (measured 2026-08-19, from YAML `sources[]`, live).**
471 sourced notes → **1,005 note↔edition pairs** across **87 distinct
editions** (all three series — e.g. Points_And_Figures'
`Beneath_the_pixels` is among the most-cited). Edition-stem ↔
entity-name collisions: **NONE**. Edition nodes: 108. Fan-in is heavily
skewed: the quarterly roundup `A_Quarter_That_Refuses_To_Behave`
(The_Chatter) alone accounts for 360 of 1,005 edges — an honest hub
that would dominate any degree-based view (hence the
`_MEMBERSHIP_TYPES` exclusion) and make co-citation via it noise (hence
the link-prediction exclusion). Fan-out: 106 notes cite 1 edition, 325
cite 2–3, 40 cite 4+. Sector/super-sector edges: 4. **Q4 amendment
needed:** PDF `sources[]` exist only on the 5 edition notes themselves
(derived notes cite editions exclusively), so "PDFs included" would
require non-note PDF nodes — recommend **dropping PDFs from P's edge
set** (they stay as YAML provenance).

---

## 3. Plan

### 3.1 C — Coverage analytics (`make analytics coverage`)

New report in `helpers/graph/analytics.py` (`REPORTS` tuple + `Report`
dataclass; reads the parquet snapshot via DuckDB — established A3 pattern).

- **C1 (ships now, no schema changes):** matrix of `series` tag ×
  `sector_classification` → distinct companies covered, plus per-series
  rollup (editions, companies, quotes). Join path: `note_tags`
  (edition note → `series/…`) ⋈ **F0 bridge** (edition stem → series, via
  the shared `_norm` index over `quotes.as_of_edition`) ⋈ `quotes`
  (edition → entity) ⋈ `entities` (entity → sector). Editions the bridge
  cannot resolve are **reported in the `note` field, never guessed**.
- **C2 (free once P lands):** same matrix via `graph_edges('cited_in')`
  ⋈ `entities` — clean joins, no fuzzy matching; C1 remains as the
  pre-P fallback.

- **Effort ~2 h. Risk: Low** — read-only; unresolved-count reported.

### 3.2 I — Incremental source-driven derivation (`--stale-only`)

`derive_insights.py` gains `--stale-only` (default off = today's
behavior). Decision at render time, per company note:

```text
skip  iff  generated.by == the derive writer          # a prior real render
       and sources[] non-empty                        # evidence to key on
       and max(sources[].last_modified) <= generated.at
render otherwise    # incl. every note without sources[] (safe default)
```

- Rationale: blocks can only change if quotes changed; quotes can only
  change if an edition changed; edition `last_modified` (git add-date) is
  in `sources[]`. Evidence unchanged → render is wasted I/O and git churn
  (the `generated.at` bump alone would dirty the note).
- **First run after landing re-renders everything with sources** — current
  stamps say `process:okf_backfill`, which is deliberately *not* treated
  as a derive stamp (same real-writer rule as the backfill itself). From
  the second run on, only newly-ingested editions trigger re-renders.
- Dry-run + `--stale-only` = the stale worklist preview; the `--okf`
  census stays the aggregate view (199 past `stale_after` today).
- Summary line per run: `rendered / skipped-unchanged / no-evidence`.
- **Effort ~3 h. Risk: Medium** — touches the note writer; guarded by
  byte-identical-skip tests (below). maint-full unaffected.

### 3.3 P — `cited_in` provenance edges (editions as graph nodes)

New `helpers/graph/derive_cited_in.py` + `make derive-cited-in`
(+ paired rebuild, `derive-themes-rebuild` pattern).

- **Edition entities**: one per source note, `entity_type='edition'`,
  `name` = note stem, `file_path` = vault-relative path,
  `normalized_name` = normalized title; no ticker; **not** in the
  `belongs_to` forest. Collision-guard: SELECT-by-name before insert
  (stub-creation rule); fail loudly on stem collisions across the three
  trees (assumed unique — `sources[].id` already relies on it).
- **Edges**: for each derived note with `sources[]` —
  `source` = note entity (company **or** sector), `target` = edition
  entity, `edge_type='cited_in'`, `weight=1`, `properties=
  {resource, n_quotes}`, `symmetric=0`, `source_ref='okf:sources'`.
  Idempotent: DELETE-then-INSERT scoped to `edge_type + source_ref`.
- **Consumer decisions (F2)**:
  - `analytics.py`: add `cited_in` to `_MEMBERSHIP_TYPES` exclusions —
    provenance is structural, it would drown activity signals.
  - `suggest_relations`: exclude `cited_in` from v1 features; co-citation
    ("two companies cited in the same edition") is a natural future
    feature, noted in its docstring.
  - `context_pack`: Relations facts are ranked by per-type priority and
    trimmed to a budget (ownership/structural first, co-mentions drop
    first). Give `cited_in` a priority at the display-only end (or verify
    the unmapped default) so provenance can't crowd out activity edges.
  - Integrity checks: verify `check_hierarchy` ignores non-forest types
    (it walks `belongs_to` only — confirm, don't assume).
- **Snapshot**: entities +108 rows, `graph_edges` +471 (one per sourced
  note) up to +659 (if per-link); regenerate per the standard rule.
- Rejected alternatives (on record): a `note_sources` SQL mirror table
  (cheap but non-traversable; duplicates what `quotes.as_of_edition`
  already gives row-level); edition-as-edge-property (the
  `co_mentioned_in` precedent — keeps editions out of the graph).
- **Effort ~4 h. Risk: Medium-High** — entity-population blast radius
  across snapshot/analytics/link-prediction; hence ordered last and
  shipped behind its own target.

---

## 4. Effort & risk

| Step | Effort | Risk |
|---|---|---|
| F0 shared edition-key helper (lift `_norm` index) | ~1 h | Low — refactor, backfill tests pin behavior |
| C1 coverage report (+ unresolved reporting) | ~2 h | Low — read-only parquet |
| I `--stale-only` + summary line + tests | ~3 h | Medium — writer path; byte-identical guards |
| P edition entities + `cited_in` + consumer excludes | ~4 h | Medium-High — blast radius §3.3 |
| Docs + full gate run (once, at end) | ~1 h | None |

Total ~11 h. Each step independently shippable; C and I have no
dependency on P.

---

## 5. Open questions (recommendations in bold)

1. **C sequencing** (held open by the user; numbers now in F4/F5):
   - **(a) C1 now** — Low risk, ships immediately, but the matrix is
     The_Chatter-only (quotes are single-series; F4).
   - **(b) P first, then C2** — the full three-series matrix via clean
     SQL joins, plus the graph capabilities; carries P's Med-High blast
     radius before any analytics value lands.
   - **(c) C-yaml first** — coverage report parses `sources[]` YAML
     directly (full three-series matrix, no graph dependency), then C2
     supersedes it after P. Costs one throwaway join path.
   **Recommendation (updated post-deep-dive): (b) P first** — the
   question "which series covers which sectors" genuinely spans three
     series, F4 shows quotes can't answer it, and F5 removed the main
     unknowns (no collisions, edge count known, hub skew understood).
     (c) only if P's blast radius needs to wait a cycle.
2. **`--stale-only` default**: opt-in flag or eventual default?
   **Recommend opt-in** for at least one ingest cycle, then reconsider.
3. **Notes without `sources[]` under `--stale-only`**: always render
   (safe, current proposal) or skip (faster, blind)?
   **Recommend always render** — 641 companies today, correctness first.
4. **Edge granularity**: one `cited_in` per (note, edition) pair.
   Originally accepted as "per pair, PDFs included" — **amended
   post-deep-dive (F5), pending user confirmation**: PDF `sources[]`
   exist only on edition notes (derived notes cite editions
   exclusively), so PDF edges would need non-note PDF nodes.
   **Recommend dropping PDFs from the edge set** (they remain YAML
   provenance).
5. **`weight`**: `1` or quote-count? **Recommend 1** — centralities are
   unweighted by adoption (Onager decision); depth lives in properties.
6. **Sector notes** get `cited_in` too (4 have sources today)?
   **Recommend yes** — same rule, no special-casing.

---

## 6. Definition of Done

- `make analytics coverage` runs on the live snapshot and its numbers
  reconcile: Σ(companies covered per series) ≥ companies with edition
  quotes (unresolved bridge rows reported, not guessed).
- `derive_insights.py --apply --stale-only` run **twice**: the first
  re-renders sourced notes (backfill stamps ≠ derive stamps); the second
  is a no-op — `git status` clean over `findata/`, summary reports
  `0 rendered`.
- `make derive-cited-in` idempotent (re-run = 0 changes); paired DuckDB
  rebuild green; `graph-smoke`/`graph-stats` sane with edition nodes;
  `analytics` summary reflects the new entity/edge counts.
- Full `make qa` + `perf` + `advisory` green **once, at the end**;
  OKF census unchanged (1,227 / 199 stale).
- Docs: procedures (`markdown_parse.md` or a new `provenance.md` section)
  cover the edition-key rule and the three new targets; `completed.md`
  entry when shipped; this proposal moves to `archive/` per convention.
