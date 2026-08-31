---
title: Namespaced Tags, Validation & Tag Sync for Source Newsletter Notes
status: executed
filed: '2026-08-19'
executed: '2026-08-19'
completed_md: '132'
area: findata/{The_Chatter
---

# Proposal: Namespaced Tags, Validation & Tag Sync for Source Newsletter Notes

**Status:** EXECUTED 2026-08-19 (S1–S4 + S6 shipped; S5 `company/`
coverage DEFERRED per Q2). Earlier accepted decisions: (1) **namespaced
tag vocabulary** (the derived-note grammar `^[a-z0-9_]+/[a-z0-9_]+$`, not
flat tags); (2) **fully machine-driven** — hand-editing is explicitly
ruled out; every writer path below is code. §6 resolved as recommended:
Q1 publisher omitted when unknown; Q2 S5 deferred; Q4 producers never
emit permalink/publish chrome; Q5 dangling `/Reports/*.pdf` resources
are kept as historical pointers (accept the `--okf` advisories).
Applied: 108 notes tagged (2 tags each; 1 flat-tag migration), 216
`note_tags` rows, corpus census 1227 machine-confirmed. Shipped:
completed.md #132.
**Date:** 2026-08-19
**Author:** Agent analysis (user-directed)
**Builds on:** [`okf_adoption.md`](okf_adoption.md) (OKF v0.2 provenance,
shipped 2026-08-18) and the 2026-08-19 provenance backfill
(`helpers/misc/backfill_okf_provenance.py`, both modes EXECUTED —
completed.md #133).
**Scope:** `findata/{The_Chatter,The_PlotLines,Points_And_Figures}` (the
"source newsletter trees", 108 notes today) + any future non-derived
`findata/` tree; `helpers/pdf/pdf_conv_md.py`,
`helpers/misc/backfill_okf_provenance.py`, `helpers/core/sync_tags.py`,
`doc/schema/frontmatter.newsletter.v1.json` (new),
`helpers/validators/frontmatter_schema.py`, `doc/procedures/markdown_parse.md`.
Explicitly out of scope: `Reports/`, `frontend/` tag UI, Obsidian-publish
permalink emission (open question Q4).

---

## 1. Summary

The source newsletter notes now carry OKF provenance (done), but their tags
are either absent (107 notes) or flat hand-added outliers
(`zerodha`, `chatter` on `Scaling_Through_Slowdowns.md`) — and nothing
consumes them: `sync_tags` is entity-driven (newsletters have no entity
rows), the schema gate scopes only the derived trees, and `note_search`
indexes titles/bodies only. This proposal gives the source notes the same
three-part machinery the derived notes already have
([markdown_parse.md §Tags](../../procedures/markdown_parse.md)):

1. a **namespaced vocabulary** (`series/…`, `publisher/…`, later
   `company/…` coverage) emitted by the producer (`pdf_conv_md.py`) and
   backfilled by the existing provenance tool;
2. a **JSON-Schema** (`frontmatter.newsletter.v1.json`) bringing the trees
   under the B1 frontmatter gate;
3. a **SQL mirror** (`note_tags` table) rebuilt by `sync_tags`, mirroring
   the `entity_tags` pattern, so source tags are queryable.

Everything is machine-written and idempotent; the notes' YAML is the source
of truth (same contract as derived notes), and the only manual act ever
required is running a command.

## 2. Current state (verified 2026-08-19)

- 108 source notes: The_Chatter 81, The_PlotLines 2 (+`image_map.md`
  chrome), Points_And_Figures 25 (+`image_map.md`). All have OKF frontmatter
  from the `--sources` backfill (`type: newsletter`, `title`,
  `generated`, `stale_after`; 5 have `sources[]` → `/Reports/*.pdf`).
- Exactly ONE note has tags (`Scaling_Through_Slowdowns.md`): flat
  `zerodha`, `chatter` — grammar violations under the namespaced rule —
  plus Obsidian-publish chrome (`permalink`, `visibility: public`,
  `language: en`, `last_updated`).
- Producer: `pdf_conv_md.py` (markdown_parse Stage 0) emits
  `type/title/generated/sources` — **no tags**.
- Consumers today: none for source-note tags.
  - `sync_tags.py` rebuilds `entity_tags` joined via `entities.file_path`
    with a 9-namespace whitelist (`entity_type/ sector/ market_cap/
    subsector/ holding_company/ geography/ business_model/ risk_investment/
    investment_theme/`); newsletters have zero entity rows → skipped.
  - `verify_notes.py` + the schema gate scope `Companies/Sectors/
    Super_Sectors` only (DIR_TO_TYPE); okf_adoption §2.2 deliberately left
    newsletter notes schema-less **because they had no frontmatter** — that
    premise is now false.
  - `rebuild_note_search.py` indexes the corpora as
    `doc_type chatter|points_and_figures|plotlines` (title+content, FTS5).
  - The OKF `--okf` sweep checks §11 + newsletter producer shape (advisory).

## 3. Tag vocabulary (namespaced, machine-derived)

| namespace | value | derived how (machine) | example |
|---|---|---|---|
| `series/` | output-dir slug | `slugify(Path(out_dir).name)` at write; tree name at backfill | `series/the_chatter` |
| `publisher/` | publisher slug | per-series constant map; **omitted when unknown** (never guessed) | `publisher/zerodha` |
| `company/` *(S5, optional)* | entity slug | `quotes`/`entities` join per edition (see §4.5) | `company/avanti_feeds` |

Rules:
- Grammar `^[a-z0-9_]+/[a-z0-9_]+$` (same pattern as derived schemas) —
  enforced by the new schema, so violations are gate-fatal, not advisory.
- **No free-text/topic tags** — nothing can derive them reliably; a future
  slice can add `topic/…` only if a machine source (e.g. an LLM pass with a
  whitelist) is approved. Not proposed now.
- `edition/n` numbering: **no machine source exists** for edition numbers on
  the notes themselves (numbers appear only in some company-note footers) —
  deliberately not part of the vocabulary.
- The flat outliers on `Scaling_Through_Slowdowns.md` migrate via a fixed
  map in the backfill: `zerodha → publisher/zerodha`,
  `chatter → series/the_chatter`; unknown flat tags are dropped to a report
  line (not silently).

## 4. Plan

### 4.1 S1 — `frontmatter.newsletter.v1.json` + gate scope (~1 h, low risk)

New schema (mirrors the OKF additions in the three existing schemas):
required `type` (const `newsletter`), `title`; optional `tags` (array,
namespaced pattern, min 1), `permalink`, `visibility`, `language`,
`last_updated`, plus the five OKF keys (`generated` `verified` `sources`
`status` `stale_after` — same definitions as the company schema);
`additionalProperties: false` (publish chrome is enumerated, so nothing
legit is rejected). Register the three trees in
`frontmatter_schema.py:DIR_TO_TYPE` (newsletters → `newsletter`) so the B1
corpus check covers them. The OKF `--okf` sweep keeps its advisory role
(§11 shape) — unchanged.

### 4.2 S2 — `pdf_conv_md.py` emits tags (~30 min, low risk)

`build_okf_frontmatter` gains `tags`:
`series/<slugify(out_dir.name)>` always; `publisher/<PUBLISHER_BY_SERIES
.get(series, …)>` when known (seed map: all three current series →
`zerodha`); omitted otherwise. Unit tests mirror the existing
`TestBuildOkfFrontmatter` cases (unknown dir → series-only).

### 4.3 S3 — tag backfill pass in `backfill_okf_provenance.py --sources` (~1.5 h, medium risk)

The `--sources` pass already round-trips every note's YAML; it additionally
sets `tags` when absent/incomplete: series tag from the tree name, publisher
from the map, then the flat-tag migration map (§3) for pre-existing
non-namespaced values. Same idempotency contract as the provenance stamp
(second run = 0 writes; covered by a byte-identical re-run test). Notes with
real-writer `generated.by` are still augmented (tags don't imply content
authorship).

### 4.4 S4 — `note_tags` SQL mirror in `sync_tags.py` (~1.5 h, medium risk)

New table (full-rebuild, mirroring `entity_tags`):

```sql
CREATE TABLE note_tags (
  note_path TEXT NOT NULL,   -- findata/The_Chatter/<Edition>.md
  tag       TEXT NOT NULL,   -- namespaced, whitelisted
  PRIMARY KEY (note_path, tag)
);
```

`sync_tags` walks the source trees (same discovery as the backfill:
non-derived trees, chrome-skipped), inserts rows for whitelisted source
namespaces (`series/`, `publisher/`, `company/`), DELETE-then-reinsert per
run. Wire into `make maint` (it already runs sync-tags). Snapshot DDL picks
it up automatically; integrity-check registry gains a one-line consistency
check (note_tags ⊆ frontmatter tags). This makes source tags queryable
(`SELECT note_path FROM note_tags WHERE tag='company/avanti_feeds'`) without
fabricating entity rows.

### 4.5 S5 — `company/` coverage tags (DEFERRED per Q2; ~1.5 h, medium risk)

Derivable from data we already have: `SELECT entity, as_of_edition FROM
quotes` (+ the metrics/relations tables if desired) → per-edition entity
list → edition↔note matching reusing the backfill's `_norm` index matcher →
`company/<slug(entity)>` tags (capped, e.g. all entities; slugs must
grammar-fit). Two consumers benefit immediately: Obsidian tag navigation
("which editions discussed X") and `note_tags` SQL joins. DEFERRED (Q2,
2026-08-19): execute only after S1–S4 have landed and stabilized.

### 4.6 S6 — docs (~30 min, low risk)

`markdown_parse.md`: §Tags gains the source-note vocabulary + writer paths;
Stage 0 notes that `pdf_conv_md.py` emits series/publisher tags.
`doc/okf.md` cross-ref. `completed.md` entry on ship.

## 5. Effort & risk

| Slice | Effort | Risk |
|---|---|---|
| S1 schema + gate scope | ~1 h | Low — additive; corpus check extends to 108 notes (they must validate: all have type+title today) |
| S2 producer tags | ~30 min | Low |
| S3 backfill tags | ~1.5 h | Medium — YAML rewrite of 108 notes; idempotency + flat-tag migration tests required |
| S4 note_tags sync | ~1.5 h | Medium — new table + maint wiring; snapshot regen needed |
| company/ coverage tags (DEFERRED) | ~1.5 h | Medium — edition↔note fuzzy matching (reuse `_norm`); tag count per note can grow |
| S6 docs | ~30 min | None |

Gate: `make qa` (schema corpus now 1,227 notes) + targeted
`pytest tests/test_frontmatter_schema.py tests/test_pdf_conv_md.py
tests/test_backfill_okf_provenance.py tests/test_sync_tags.py` + census
unchanged (1,227 machine-confirmed).

## 6. Open questions — RESOLVED (accepted as recommended, 2026-08-19)

1. **Publisher for future series** — RESOLVED: omit-when-unknown. The
   series map is extended when a new series lands; Stage 0 stays zero-config.
2. **S5 now or later?** — RESOLVED: later. S1–S4 deliver the
   infrastructure; coverage tags are pure addition afterwards.
3. **note_search tags column** (FTS5 filter for the frontend) — not
   proposed; `note_tags` serves SQL, and frontend tag UI is out of scope.
4. **Permalink / publish chrome emission** — RESOLVED: producers do NOT
   emit them. `Scaling_Through_Slowdowns`'s `permalink`+`visibility`+
   `language` look like Obsidian-publish setup; the schema tolerates their
   presence, no writer copies them.
5. **The 5 `/Reports/*.pdf` sources** — RESOLVED: accept the advisories.
   Once the PDFs are deleted, the `sources[].resource` strings remain true
   historical pointers; the `--okf` sweep reports them as advisories and
   nothing strips them.

## 7. Definition of Done

- **Schema:** `frontmatter.newsletter.v1.json` exists; `DIR_TO_TYPE`
  covers the three trees; the B1 corpus check validates all 1,227 notes.
- **Producer:** every note emitted by `pdf_conv_md.py` carries
  `series/` (+ `publisher/` when known) alongside its OKF block.
- **Backfill:** all 108 existing notes carry ≥ the series tag; the flat
  outliers are migrated; re-runs write 0 notes.
- **Sync:** `note_tags` exists and rebuilds idempotently from note YAML;
  `make maint` refreshes it; snapshots capture it.
- **Validation:** namespaced-grammar violations on source notes are
  gate-fatal (schema pattern), and `verify_notes` scope is unchanged.
- **Docs:** markdown_parse.md §Tags + Stage 0 updated; completed.md entry.
