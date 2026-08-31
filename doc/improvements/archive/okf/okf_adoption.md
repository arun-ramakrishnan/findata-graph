---
title: Adopt OKF v0.2 Provenance Vocabulary in Note Frontmatter
status: executed
filed: '2026-08-18'
executed: '2026-08-18'
completed_md: '130'
area: helpers/` writers
---

# Proposal: Adopt OKF v0.2 Provenance Vocabulary in Note Frontmatter

**Status:** EXECUTED 2026-08-18 (§2.1/§2.2/§2.3/§2.5/§2.6 shipped; §2.4 is
by-design manual — `verified` is strictly human-written, no code path).
Decisions accepted as recommended: Q1 sources only when the PDF is under
`Reports/`; Q2 `verified` strictly human; Q3 `stale_after` =
max(sources last_modified) + 180d else derive-date + 180d, `status` omitted
(defaults stable); Q4 per-claim footnotes deferred; Q5 gradual rollout (no
backfill) — **superseded 2026-08-19**: a one-off backfill
(`helpers/misc/backfill_okf_provenance.py`, both modes) stamped the whole
corpus; Q6 `okf_version` key deferred (would need another schema
enumeration; nothing consumes it yet). Shipped: completed.md #130
(+ #131 conformance sweep, #133 backfill).
**Date:** 2026-08-18
**Author:** Agent analysis (user-directed)
**Design reference:** [`doc/okf.md`](../../okf.md) — OKF background, current-state
analysis, and the frontmatter/JSON-Schema design. This document is the
**plan/implementation**: concrete file changes, tests, effort, and open
questions.
**Scope:** `helpers/` writers, `findata/` note frontmatter,
`doc/schema/*.v1.json`, `tests/test_frontmatter_schema.py`. Explicitly **out of
scope**: `Reports/`, `frontend/`, `memory/`, `doc/` prose pages.

---

## 1. Summary

Adopt OKF v0.2's **optional, additive** provenance/trust/lifecycle frontmatter
keys (`generated`, `verified`, `sources`, `status`, `stale_after`) into
`findata/` note frontmatter, written by the two generators that own the data
(`pdf_conv_md.py`, `derive_insights.py`) and validated by the existing
frontmatter schema gate. Rationale and field shapes: [`doc/okf.md`](../../okf.md).

Net effect: signals that today exist only in SQLite columns and sentinel markers
become queryable note metadata, without any schema migration (all new keys
optional) or frontend impact.

---

## 2. Plan

### 2.1 JSON-Schema additions (3 files)

In each of `doc/schema/frontmatter.company.v1.json`,
`doc/schema/frontmatter.sector.v1.json`,
`doc/schema/frontmatter.super_sector.v1.json`, add the `generated`, `verified`,
`sources`, `status`, `stale_after` keys. Exact schema + deliberate deviations:
[`doc/okf.md` §3.3](../../okf.md).

- `helpers/validators/frontmatter_schema.py` needs no logic change; the
  existing `_normalize()` date-object handling extends to the new keys.
- **Effort ~30 min. Risk: Low** — additive, validated by the existing suite.

### 2.2 `helpers/pdf/pdf_conv_md.py` — emit frontmatter

In `write_outputs`, prepend a frontmatter block to the emitted `<stem>.md`:

```yaml
---
type: newsletter            # self-describing; output dir MUST be outside the
                            # validated Companies/Sectors/Super_Sectors tree (see note)
title: <derived from first heading or filename>
generated:
  by: pdf_conv_md.py/PP-StructureV3
  at: <ISO 8601 now, UTC>
sources:
  - id: <stem>
    resource: /Reports/<stem>.pdf   # bundle-relative path (leading /) per OKF §6.2
    title: <pdfinfo Title if available, else stem>
    author: process:pdf_conv_md     # optional OKF §5.1 credibility signal
    last_modified: <pdfinfo ModDate converted to YYYY-MM-DD>  # see note
---
```

`by:` values follow OKF's actor convention (`<producer>/<version>`,
`human:<id>`, `process:<id>`).

**Output-location note (validator scope):** the frontmatter validator only
validates notes under `Companies/`, `Sectors/`, `Super_Sectors/` (mapped via
`DIR_TO_TYPE`). `type: newsletter` is *not* in any schema, so if
`pdf_conv_md.py`'s `output_dir` lands inside one of those trees the note will be
**rejected** (missing required keys, `additionalProperties: false`). Ensure the
newsletter output dir is outside the validated tree, **or** add a minimal
`doc/schema/frontmatter.newsletter.v1.json`, **or** have the validator skip
`type: newsletter`. **Resolved 2026-08-19:** the newsletter trees are now
registered in `DIR_TO_TYPE` and `frontmatter.newsletter.v1.json` exists —
see `newsletter_notes_adoption.md` S1.

**`last_modified` conversion note:** `pdfinfo` reports `ModDate` in PDF date
syntax (`D:YYYYMMDDHHmmSSOHH'mm'`), not `YYYY-MM-DD`. Convert it to ISO
`YYYY-MM-DD` (handling the timezone offset) before writing, or the schema's
`YYYY-MM-DD` date pattern will reject the note.

- **Effort ~1 h. Risk: Low** — new writer path; unit-tested (include the ModDate
  conversion + output-location check in the unit test).

### 2.3 `helpers/graph/derive_insights.py` — bump `generated`

When writing the sentinel-wrapped auto block into a company note, bump
`generated` in that note's frontmatter (`by: derive_insights.py/<version>`,
`at: <ISO 8601 now, UTC>`). **This is new logic:** today `derive_insights.py`
never reads or writes YAML frontmatter (it only writes sentinel HTML comments
into the body), so this step must (a) load the note's existing frontmatter,
(b) **preserve every existing key — especially any hand-written `verified`** — and
(c) set/update `generated`, then write the note back via the project's YAML
serializer (do **not** regex-splice the file, which would corrupt key order,
quoting, or formatting). Leaving `verified` intact means trust tiers fall out
mechanically: notes only ever touched by `derive_insights.py` report
*machine-confirmed*; a note with a `human:` verification reports
*human-reviewed*. The DB DELETE-then-INSERT idempotency is unchanged.

- **Effort ~1 h. Risk: Medium** — new frontmatter read/modify/write path (the
  current derive flow has none); cover with a round-trip unit test asserting a
  pre-existing `verified` survives a `generated` bump unchanged.

### 2.4 `helpers/core/parse_newsletter.py` (Stage 4, optional)

When an agent curates a `## The Chatter — <edition>` block, add a `verified:`
entry (`by: human:<reviewer>`, `at: <date>`). Optional; manual step.

- **Effort ~1 h. Risk: Low.**

### 2.5 Tests

Extend `tests/test_frontmatter_schema.py`:

- `GOOD_COMPANY` / `GOOD_SECTOR` fixtures gain representative `generated` /
  `sources` / `status` / `stale_after` entries (must validate).
- Negative cases: malformed `generated.by` (empty), non-ISO `verified.at`,
  empty `sources` (missing `resource`), bad `status` enum value, rogue key
  inside `generated` (still rejected by `additionalProperties: false`).
- A corpus check asserting every *schema-targeted* note (Companies/Sectors/
  Super_Sectors) still parses with the extended schemas (no regressions on the
  1,068 + 42 + 9 live notes).
- Optional: an OKF-conformance smoke test mirroring OKF §11 (every non-reserved
  `.md` has parseable frontmatter) — already implied by `verify_notes.py`, kept
  as an explicit marker if desired.

- **Effort ~1 h. Risk: Low** — mirrors existing patterns.

### 2.6 Documentation

Regenerate `doc/schema/frontmatter_keys.md` via
`python3 -m helpers.validators.frontmatter_schema --emit-doc` (single source of
truth). No other doc changes required; `doc/improvements/completed.md` gets the
item when shipped.

- **Effort <5 min. Risk: None.**

---

## 3. Effort & risk

| Step | Effort | Risk |
|---|---|---|
| JSON-Schema additions (3 files) | ~30 min | Low — additive, validated by existing suite |
| `pdf_conv_md.py` frontmatter emit | ~1 h | Low — new writer path; unit-tested |
| `derive_insights.py` frontmatter bump | ~1 h | Medium — new frontmatter read/modify/write path (see §2.3) |
| Test fixtures + negative cases | ~1 h | Low — mirrors existing patterns |
| Regenerate `frontmatter_keys.md` | <5 min | None |

Total ~4 h. No migration of the existing 1,119 notes (all new keys optional).
Full `make qa` + `pytest tests/test_frontmatter_schema.py` as the gate.

---

## 4. Open questions

1. Should `sources` be written for *every* newsletter note from
   `pdf_conv_md.py`, or only when the source PDF is present under `Reports/`?
   (Recommend: when present; else omit.)
2. Should `verified` ever be written automatically (e.g. `by: derive_insights.py`
   self-verify on deterministic extraction), or strictly by humans? (Recommend:
   strictly human, matching OKF's "who wrote ≠ who confirmed". Trust tiers
   already give us *machine-confirmed* for free whenever `generated` exists but
   `verified` does not — no need to fake human review.)
3. Do we want `status`/`stale_after` now, or defer until a lifecycle need
   appears? (Recommend: add to schema now, populate later. Spec default for
   absent `status` is `stable`, so omission is already a correct value.)
   **Population rule (recommended):** have `derive_insights.py` set
   `stale_after = max(source last_modified) + N days` (e.g. N=180) when it
   writes `generated`, so the lifecycle signal has value instead of staying
   empty; `status` can default to `stable` and be overridden manually.
4. Is there appetite for the per-claim `[^source-id]` citation footnotes in
   quote blocks (binds a quote to its edition/PDF)? (Nice-to-have, separate
   item. The spec's `sources[].id`-keyed footnote join is exactly the existing
   `as_of_edition`/`source_quote` DB columns surfaced into the note.)

5. **Backfill / rollout:** the 1,119 existing notes only gain the new keys on
   their next derive/render. Is an optional one-off backfill (re-run
   `derive_insights.py` across the corpus) wanted to realize provenance
   immediately, or is gradual rollout acceptable? (Recommend: gradual; keys are
   optional so nothing breaks.)
6. **Completeness extras (optional):** should hand-authored company notes also
   carry `generated: {by: human:<id>, at: <date>}` for symmetry, and should
   generated notes emit `okf_version: 0.2` so consumers know the vocabulary
   version? (Both optional; cheap future-proofing.)


## 5. Definition of Done (acceptance criteria)

This proposal is complete when:

- **Schema:** `generated`, `verified`, `sources`, `status`, `stale_after` are
  present and optional in all three `doc/schema/frontmatter.*.v1.json` files;
  `make qa` (which runs the frontmatter validator) is green.
- **Newsletter provenance:** every note emitted by `pdf_conv_md.py` carries
  `generated` + a `sources[]` entry with `resource` (bundle-relative, leading
  `/`), `title`, `author`, and `last_modified` (ISO `YYYY-MM-DD`, converted from
  pdfinfo's `D:…` PDF date). Output lands outside the validated
  Companies/Sectors/Super_Sectors dirs (or a `frontmatter.newsletter.v1.json`
  exists) so the validator does not reject `type: newsletter`.
- **Derive provenance:** every company note re-derived by `derive_insights.py`
  gains/updates `generated` while preserving any hand-written `verified` and all
  other frontmatter keys (safe YAML read/modify/write, no regex splicing).
- **No regressions:** the corpus check over the 1,068 + 42 + 9 live notes still
  validates 100% with the extended schemas (`pytest tests/test_frontmatter_schema.py`
  + `verify_notes.py`).
- **Tests:** `GOOD_*` fixtures and negative cases (empty `generated.by`,
  non-ISO `verified.at`, `sources` missing `resource`, bad `status` enum, rogue
  key inside `generated`) pass.
- **Docs:** `doc/schema/frontmatter_keys.md` regenerated; `doc/improvements/completed.md`
  gets the shipped item.

**Gradual rollout note:** the 1,119 existing notes only gain the new keys on
their next derive/render; until then they remain valid (all keys optional). An
optional one-off backfill (re-run `derive_insights.py` across the corpus) can
realize the provenance benefit immediately. **Executed 2026-08-19** via
`helpers/misc/backfill_okf_provenance.py` (derived + `--sources` modes; all
1,227 notes stamped — completed.md #133).