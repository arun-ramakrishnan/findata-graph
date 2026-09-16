---
title: "Concall-title edition normalisation — H1 guard at the capture boundary + writer worklist"
status: executed
filed: "2026-09-15"
executed: "2026-09-16"
completed_md: "236"
area: "helpers/core/edition_index.py (predicate), helpers/graph/derive_insights.py (_edition_title), tests, doc/design/db_schema.md — no schema change, no DB rewrite"
---

# Concall-title edition normalisation — H1 guard at the capture boundary
+ writer worklist

**Date:** 2026-09-15 · **Status:** EXECUTED ·
**Area:** `helpers/core/edition_index.py`,
`helpers/graph/derive_insights.py`, `tests/`, `doc/design/db_schema.md` —
D8 of the hyper_lane_wiring §5 backlog (#233 §6.1 note: "quote
`as_of_edition` values that are concall titles, not editions"). No
schema change; **no DB rewrite** — the storage layer measured clean.

## 1. Problem (measured 2026-09-15, read-only)

The storage layer is already normal: since #136 the write boundary
resolves display titles to edition STEMS, and today
`quotes.as_of_edition` joins 8,272/8,272 and `company_metrics` 4,033/4,033
to edition entities (100%). What remains is the **capture surface**:

1. **Ten source notes carry diseased H1/`title:` values** — a concall
   header, a bare stem, or a section-heading leak rides where the
   edition title belongs:

   | Stem | Title today | Disease |
   |---|---|---|
   | `BEL_HUL_Tata_Capital` | Bharat Electronics Limited \| Large Cap \| Aerospace & Defence | concall header |
   | `Infosys_Adani_Power` | Spandana Sphoorty Financial Limited \| Small Cap \| NBFC - Microfinance | concall header |
   | `Bosch_Amara_Zydus` | Zydus Lifesciences Ltd.\|Large Cap\| Pharmaceuticals | concall header |
   | `Meesho_Marico_Bajaj` | Meesho_Marico_Bajaj | bare stem |
   | `Marico_DLF_BSE` | Marico_DLF_BSE | bare stem |
   | `RBI_Tata_Steel` | RBI_Tata_Steel | bare stem |
   | `Milky_Mist_Bluestone` | FMCG | section leak |
   | `Borosil_Orchid_Welspun` | Consumer Durables | section leak |
   | `Maruti_Lumino_Indian_Bank` | Regulator | section leak |
   | `Max_Life_Tempsens_TCS` | Subtext by Zerodha | series/section leak |

   These flow into `Quotes.md sources[].title`, note-search titles, and
   any title-keyed resolution. (Observed, non-goal: the
   `Where_Data_meets_Direction` title carries a writer typo
   "Directic"; stem-format oddities — `Bets and blueprints` spaces,
   `Tariffs__Tailwinds.` trailing period, `The_Pivot_Quarter_output`
   suffix — are writer filenames, untouched.)

2. **No capture guard**: `_edition_title()` accepts any 3–80-char H1,
   so a concall-header H1 becomes the edition display title. When the
   index happens to hold that same string as the note's title it still
   resolves to the stem (the happy accident keeping today's data
   clean); when it does not, the row rides as a verbatim straggler —
   the exact #136 failure mode (`Adani Green | Large Cap | Energy`).

## 2. Design

- **Guard at the capture boundary**: `edition_index.is_concall_header(
  title)` — true for the `[Company | Cap | Sector]` shape (2+ pipe
  separators). `_edition_title` returns the **STEM** when the H1 trips
  the guard or equals the stem: the stem is the canonical key, resolves
  exactly, and `as_of_edition` stores it unchanged. Deterministic
  only — section leaks (`FMCG`, `Regulator`) have no capture-time
  signature and stay a writer-worklist item.
- **Writer worklist, no agent edits**: the ten notes above are
  writer-owned source notes; the worklist lives here (§1 table) and in
  the completion report. Title fixes happen in Obsidian by the
  operator; the render footnotes follow the note once fixed.
- **Nothing to backfill**: DELETE-then-INSERT derived state already
  holds 100% stems; the next `--apply --stale-only` pass simply
  replays through the guarded boundary.

## 3. Slices

- **N1**: `is_concall_header()` in `helpers/core/edition_index.py`
  (pure predicate, exported), wired at THREE arms — `_edition_title`
  (pipe guard + title==stem → stem; missing-H1 stem-space fallback
  stays, pinned) and BOTH `note_title` chains (frontmatter title arm
  + first-heading arm): execution found the live disease in the
  frontmatter/heading layer, not the H1-pick layer — the diseased
  notes carry the pipe header as `title:` AND first heading, which
  feed `sources[].title`, index title keys, and the OKF backfill.
- **N2**: tests — pipe header rejected (both spacing variants), bare
   stem normalised, healthy `The Chatter: …` H1 passes, missing H1
   falls back; one live dry-run over a diseased note shows the stem
   as edition title.
- **N3**: docs — db_schema.md `quotes.as_of_edition` column note gains
   the guard sentence; proposal §6 records the worklist as handed to
   the writer.

## 4. Acceptance

- Guarded unit tests green; existing derive suites green (no
   extraction change — only the display-title choice for diseased
   H1s).
- Live check (read-only): `quotes`/`company_metrics` join rates stay
   100% after a dry-run scan; no new verbatim stragglers.
- Gates: targeted per slice; full qa/advisory once at arc end with the
   operator's go (parked with D1's).

## 5. Non-goals

- Section-leak detection at capture time (needs a sector/section
   vocabulary — coupling without a deterministic signature).
- Editing writer-owned note H1s/`title:` frontmatter (worklist only).
- Concall hyperedges / per-call participant semantics (the #233
   "concall participant sets" signal) — a demand-gated follow-up, not
   title hygiene.
- Stem-format oddities in filenames; the `Directic` typo.

## 6. Execution Results

- **N1 EXECUTED (2026-09-15)** — `is_concall_header` (2+-pipe shape)
   in edition_index, wired into `_edition_title` (pipe/stem-repeat →
   canonical stem) and `note_title` (both the frontmatter arm and the
   heading arm fall through on a pipe header). The H1 arm of
   `_edition_title` is future-proofing: execution showed the live
   notes' H1s do not sit at body position 0, so `_edition_title` was
   already falling to the stem-space form (which resolves exactly —
   verified live).
- **N1 live effect (read-only checks, no vault writes)** — the six
   pipe/bare-stem notes now surface: `Bosch_Amara_Zydus` → "The
   Chatter: Bosch, Amara, Zydus & More" and `Marico_DLF_BSE` → "The
   Chatter: Marico, DLF, BSE, Nykaa & More" (the guard RECOVERED the
   healthy H1s beneath the diseased frontmatter — two titles
   self-healed); the other four → their stems. Write boundary
   resolution verified: display → stem, both directions exact.
- **N2 EXECUTED (2026-09-15)** — `test_is_concall_header_shape` +
   `test_note_title_guards_concall_headers` (edition_index, 2) +
   `TestEditionTitleGuard` (derive_insights, 5: pipe spaced/tight,
   bare stem, healthy pass-through, missing-H1 fallback pinned).
   Suites green: edition_index + derive_insights +
   backfill_okf_provenance + integration + fuzz = 207 + 192-scope
   runs, ruff/format clean.
- **N3 EXECUTED (2026-09-15)** — db_schema.md `quotes.as_of_edition`
   column note names the guard; the writer worklist below (final,
   post-recovery state) handed over in the completion report.
- **Writer worklist (post-recovery, 8 notes)** — fix `title:` in
   Obsidian; two notes now show their real edition titles via the
   guard, six need a human title: `BEL_HUL_Tata_Capital`,
   `Infosys_Adani_Power`, `Meesho_Marico_Bajaj`, `RBI_Tata_Steel`
   (pipe/bare-stem titles, both layers diseased) + the four
   section-leak titles `Milky_Mist_Bluestone` (FMCG),
   `Borosil_Orchid_Welspun` (Consumer Durables),
   `Maruti_Lumino_Indian_Bank` (Regulator), `Max_Life_Tempsens_TCS`
   (Subtext by Zerodha) — leaks have no deterministic capture-time
   shape, writer-only.
- **No DB rewrite needed** — quotes 8,272/8,272 and metrics
   4,033/4,033 already join edition entities (100%, re-measured this
   arc); the next sanctioned derive/backfill pass simply replays
   through the guarded `note_title` (sources[].title converges on the
   next `merged_sources` run).
