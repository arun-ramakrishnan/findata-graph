---
title: OKF `sources[]` Maintenance at Render Time
status: executed
filed: '2026-08-19'
executed: '2026-08-19'
completed_md: '135'
area: helpers/graph/derive_insights.py
---

# Proposal: OKF `sources[]` Maintenance at Render Time

**Status:** EXECUTED 2026-08-19. §3.1–§3.4 shipped (all §5 recommendations
accepted: Q1 git-add-date basis, Q2 keep existing entries, Q3 no cap, Q4 no
splice under `--no-notes`, Q5 backfill kept as bootstrap). Tests + full gate
green. The one-time live convergence apply (`--apply --stale-only`; dry-run
shows 52 chatter + 45 key-figures notes, all stem-leg) is intentionally HELD
at dry-run for the operator to run.
**Date:** 2026-08-19
**Author:** Agent analysis (user-directed)
**Builds on:** the archived `okf_adoption.md` (#130–#133), F0/P/C2/I of
`okf_activation.md` (edition keys, `cited_in` edges, coverage analytics,
`--stale-only`), and the 2026-08-19 maint-full placement audit.
**Scope:** `helpers/graph/derive_insights.py`,
`helpers/core/edition_index.py` (gains the source-entry builders),
`helpers/misc/backfill_okf_provenance.py` (donor of the lifted logic),
tests, procedure docs. Explicitly out of scope: `verified[]` (strictly
human), per-claim footnotes (deferred Q4 of okf_adoption), S5
`company/` coverage tags.

---

## 1. Summary

Make `derive_insights.py` maintain its own evidence trail: every time it
renders auto blocks into a company note, it splices the cited editions
into that note's OKF `sources[]` before bumping `generated`. This closes
the lifecycle gap left when the one-off backfill stamped the corpus —
and fixes a **correctness hole in `--stale-only`** that gap already
opened.

## 2. Problem

Today `sources[]` on derived notes only ever grows via
`backfill_okf_provenance.py` (a one-off). When a new chatter edition
arrives:

1. **Provenance goes incomplete** — the note gains a
   `## The Chatter — <New Edition>` block citing an edition that is
   absent from `sources[]`.
2. **`stale_after` understates freshness** — `bump_generated`
   recomputes `stale_after = max(sources[].last_modified) + 180d`; with
   the new edition missing from `sources[]`, the lifecycle signal lags
   behind the actual evidence.
3. **`--stale-only` gates on stale evidence (the hole)** — the gate
   skips a note iff `max(sources[].last_modified) <= generated.at`.
   A note whose new edition is not yet in `sources[]` looks
   "evidence-unchanged" and is **skipped, so the new chatter never
   renders** under `--stale-only`. This is the exact workflow
   `--stale-only` exists for: ingest → maint-full (quotes in DB) →
   standalone `--stale-only` apply.
4. **`cited_in` edges lag** — `derive_cited_in.py` projects
   `sources[]`, so new citations only appear after another
   backfill-style pass.

Root cause: the writer that renders evidence into notes does not
maintain the evidence list that everything downstream (lifecycle signal,
the incremental gate, the provenance graph) keys on.

## 3. Plan

### 3.1 Lift the source-entry builders into `edition_index` (shared)

Move out of `backfill_okf_provenance.py` (donor keeps thin aliases if
tests import them; measured: they don't):

- `git_add_date(path)` + its memo — the `last_modified` basis for
  edition entries (consistent with the backfill's stamps).
- `edition_source_entry(src, vault)` — the
  `{id, resource, title, last_modified}` dict.
- `merged_sources(fm, text, index, vault)` — existing entries + newly
  resolved ones, deduped by `id`.

`edition_index.py` already owns resolution (`resolve_editions`); the
entry builders are its natural companions. The backfill imports them
back (behavior pinned by its 14 tests).

### 3.2 Splice in `render_notes` / `render_metrics_notes` — AND teach the gate

Two coordinated changes; the splice alone does NOT fix §2.3 (the gate
skips the note before any render would splice):

**(a) Splice** — once per note per run, after the blocks are rendered
and **before** `bump_generated` (which then re-bases `stale_after` from
the merged list automatically — no change to `frontmatter.py`):

```
index = source_note_index(vault)          # once per run (108 notes)
entries = merged_sources(fm, rendered_text, index, vault)
if entries != fm.get("sources"):          # splice only on change
    fm["sources"] = entries               # (re-render via render_frontmatter)
new_text = bump_generated(new_text, _OKF_ACTOR)
```

- The harvest is the note-level union over the whole body (`## The
  Chatter — <edition>` headings + `*Source:*` footers) — covers chatter
  AND key-figures blocks, and any hand-added edition references.
- `sources[]` is machine-owned under OKF (§5.1: producer-written); no
  curation-safety concern, unlike block content.
- Unresolvable footers (yfinance/Yahoo) resolve to nothing — same
  honest-miss discipline as the backfill.

**(b) Gate amendment** — `_stale_only_skip` (or its call site) gains the
run's scanned editions for the note: **an edition in this run's scan
that is not yet in `sources[]` forces a render**. Skip then requires
BOTH (i) every scanned edition for the entity is already in
`sources[]` AND (ii) `max(sources[].last_modified) <= generated.at`.
Fixed point after one render: the splice added the editions, so the
next run gates correctly — evidence list and gate now read the same
world.

### 3.3 Chain + docs

The post-render chain becomes: `derive_insights --apply [--stale-only]`
→ `make derive-cited-in-rebuild` (new citations become edges) →
`make maint-full` (snapshot captures everything). Document in
`markdown_parse.md` §Edition identity next to the existing targets.
The backfill tool stays (pre-OKF notes, real-writer augment path) but
becomes rarely needed — say so in its docstring.

### 3.4 Tests

- Splice: note rendered with a new edition → `sources[]` gains the
  entry (id/resource/title/git-date), `stale_after` re-based.
- Dedup: re-render adds nothing (`merged_sources` id-dedupe).
- **The §2.3 hole, pinned end-to-end**: scan contains an edition the
  note's `sources[]` lacks → `--stale-only` RENDERS (gate amendment),
  `sources[]` gains the entry, and the second `--stale-only` run
  writes 0 notes (fixed point).
- Gate purity: scan editions all present in `sources[]` with
  `lm <= generated.at` → still skipped byte-identically.
- Idempotency: second `--stale-only` run writes 0 notes.

## 4. Effort & risk

| Step | Effort | Risk |
|---|---|---|
| 3.1 lift builders (backfill → edition_index) | ~1 h | Low — refactor, tests pin behavior |
| 3.2 splice before bump + gate amendment (both renderers) | ~2 h | Medium — frontmatter write path + gate semantics; round-trip tests |
| 3.3 chain docs + backfill docstring | ~0.5 h | None |
| 3.4 tests (incl. the hole) | ~1.5 h | Low |

Total ~4.5 h. Full gate once at end.

## 5. Open questions (recommendations in bold)

1. **`last_modified` basis for edition entries**: git add-date (backfill
   consistency) vs the edition note's OKF `generated.at`?
   **Recommend git add-date** — identical to the 476 existing entries;
   no mixed vocabulary.
2. **When sources shrink** (an edition note is deleted): drop the entry
   or keep it as a historical pointer? **Recommend keep** (same stance
   as dangling `/Reports/*.pdf` resources — OKF advisories surface it,
   never silently rewrite history).
3. **Cap on `sources[]` growth**: notes accumulate one entry per cited
   edition (max observed fan-out today: 4+). **Recommend no cap** — the
   list IS the evidence trail; size stays tiny.
4. **Should the splice also run on `--no-notes` maint-full runs?**
   **Recommend no** — no render, no new evidence; maint-full never
   touches notes (placement invariant).
5. **Retire `backfill_okf_provenance.py`?** **Recommend keep** — it
   remains the pre-OKF bootstrap and the real-writer augment path;
   note the reduced role in its docstring only.

## 6. Definition of Done

- A newly-ingested edition flows end-to-end: `--stale-only` apply
  renders its blocks, `sources[]` gains the entry, `stale_after`
  re-bases, `make derive-cited-in-rebuild` materialises the new
  `cited_in` edge, and the second `--stale-only` run writes 0 notes.
- Backfill tests (14) + edition_index tests (5) stay green after the
  3.1 lift; new splice tests pin the round trip.
- OKF census unchanged in shape (1,227 / group-scoped); `--okf`
  advisories unchanged or reduced.
- Full `make qa` + `perf` + `advisory` green, run once at the end.
- `markdown_parse.md` chain updated; `completed.md` entry + archive
  move when shipped.
