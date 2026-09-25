---
title: "derive_insights render hygiene — dedupe section markers, strip heading-bleed paraphrases"
status: executed
filed: "2026-09-25"
executed: "2026-09-25"
completed_md: "298"
area: "helpers/graph"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json -->

# derive_insights render hygiene — dedupe section markers, strip heading-bleed paraphrases

**Date:** 2026-09-25 · **Status:** EXECUTED ·
**Area:** helpers/graph (`derive_insights.py::render_chatter_block`)

## 1. Motivation

The auto `## The Chatter` first pass is working as designed — the
2026-09-25 `--apply --stale-only` flush rendered 481 backlogged notes
with zero hand-content loss (592 hand blocks skipped, sentinels balanced
in all 484 touched notes, validators green). But the flush also
quantified two cosmetic defects in `render_chatter_block` output that
now sit live in company notes:

| Defect | Count in flush (24,756 added lines, commit `b9a8cd1a`) | Source line |
|---|---|---|
| Repeated `- *[Section]*` context markers — one per quote even when N quotes share one section (7 identical lines in a row in the worst block) | 165 | `derive_insights.py` ~1640 |
| Heading-bleed paraphrase bullets `- **###### …` — captured markdown headings render as real headings inside the bullet | 14 | `derive_insights.py` ~1647 |

Trigger: operator review of the Chatter #92 arc (2026-09-25) — "are the
mods correct on the notes". Content underneath is intact (right quotes,
right companies, footnotes resolve); this is render roughness the
curation pass should not have to keep absorbing. Both defects are
pre-existing renderer behavior, not regressions.

## 2. Evidence (measured 2026-09-25, this box)

```bash
git diff HEAD~1 HEAD -- 'findata/**/*.md' | grep -c "^+.*\*\*###"        # 14
git diff HEAD~1 HEAD -- 'findata/**/*.md' | grep -cE "^\+\s*-\s*\*\[[^*]" # 165
```

Root causes, both in `render_chatter_block`:

1. **Markers:** `sec_heading = q.properties.get("heading")` emits
   `- *[{safe_heading}]*` unconditionally per quote. The S4 provenance
   comment says the intent is triage orientation ("read the note,
   decide the real home") — that intent is served once per distinct
   heading, not N times. The existing `*`-strip (MD037 guard) shows the
   function already sanitizes this line; dedupe is the missing half.
2. **Bleed:** `p_text` escapes inner `*` but never strips leading `#`s,
   so a paraphrase captured from a markdown subheading
   (e.g. `"###### Q3 and Q4 FY26 may see…"`) closes the bold wrap around
   a live heading. Quote bodies are immune (the `> "` prefix blocks
   heading parse) — paraphrase bullets are the only affected shape.

Ruled out: footnote/emphasis breakage (the `text…**` closer and `\*`
escape are correct and stay), quote truncation policy (the 140/280 cuts
with `…` are deliberate excerpting, not addressed here).

## 3. Design

**S1 — dedupe section-context markers.** Track emitted headings per
block; append `- *[…]*` only for the first quote carrying each distinct
heading (as-implemented 2026-09-26: no single-heading skip — the S4
sector test requires the lone marker as provenance). Net effect on the
flush corpus: 165 → one per distinct heading.

**S2 — sanitize paraphrase lead.** After the existing `*` handling,
`p_text = p_text.lstrip("#").lstrip()` (heading markers are never
meaningful paraphrase content — a paraphrase that IS just hashes is
empty after strip and should fall back to no-bullet, same as
`paraphrase = … or None` upstream). One line, beside the existing
escapes, with a comment citing this proposal.

**S3 — regression tests + dry-run proof.** Fixture: block with 3
quotes sharing one heading (one paraphrase `#`-leading) + 1 quote
with a second heading. Assert: exactly 2 marker lines, zero
heading-bleed output bullets, byte-stable re-render. Then corpus
dry-run (`derive_insights.py findata`, no `--apply`) must report 0
prospective `- **#` bullets and no repeated adjacent markers.

**S4 (opt-in, operator call) — historical scrub.** S1–S3 apply
forward only; the ~179 live lines stay until curated. If the operator
wants them gone mechanically, add a `--scrub-render` dry-run-first
pass over sentinel regions only (never hand content), default OFF.
Recommended: leave for curation (they sit inside sentinel regions the
curator already rewrites by hand).

Alternatives considered: stripping `#` at capture time (rejected —
the quotes table should keep raw capture; render is the right layer,
mirroring the existing `*` escapes); removing context markers
entirely (rejected — S4 provenance intent is real for multi-section
catch-all blocks, e.g. Quotes.md routing triage).

## 4. Acceptance criteria & shakedown

1. `rg '^\+.*\*\*#{1,6} ' $(dry-run diff)` — zero heading-bleed bullets
   in a full-corpus dry-run diff; repeated adjacent `- *[…]*` markers
   zero (distinct-per-block only).
2. `pytest tests/test_derive_insights*.py` green (new S3 fixture
   included); `ruff` clean on the touched function.
3. Re-render idempotency: second dry-run after apply reports 0 notes
   would write for already-rendered editions (no marker-count churn).
4. `make static-checks` proposal-lifecycle leg stays green (this file
   keeps `status: proposed` until executed).

## 5. Non-goals

- No capture-layer changes (patterns, thresholds, resolver ladder).
- No truncation-policy changes (140/280 + `…` excerpts stay).
- No mass rewrite of historical blocks (S4 is explicit opt-in).
- No Quotes.md catch-all heading-gate change (exact-match suppression
  verified 2026-09-25 — separate, working as designed).

## 6. Risks

- Over-dedupe hiding real multi-section provenance: mitigated — dedupe
  is per-distinct-heading, not per-block. As-implemented amendment
  (2026-09-26): NO single-heading skip — the S4 provenance test
  (`TestSectorRender::test_sector_block_inserts_before_synthesis`)
  proved the marker is the only provenance for lone sector/catch-all
  rows, so even one heading is emitted.
- `lstrip("#")` eating meaningful content: a paraphrase of only hashes
  carries no information; the `or None` fallback already handles empty.

## 7. References

- `helpers/graph/derive_insights.py::render_chatter_block` (~1601–1660)
- `doc/procedures/markdown_parse.md` Stage 11 (auto-block contract +
  curation-safety rule)
- Chatter #92 arc evidence: commit `b9a8cd1a` (481-note flush),
  `findata/Super_Sectors/Quotes.md` deleted catch-all (worst-case
  7-identical-marker block, pre-fix sample)
- Lifecycle: file BEFORE implementing (house rule 2026-08-21); on
  EXECUTED → `../archive/graph/`, completed.md unique number,
  `make search-fresh APPLY=1`
