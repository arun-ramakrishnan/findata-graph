# Open Knowledge Format (OKF) — Design

Where OKF fits in this codebase: the reference vocabulary for **note
frontmatter provenance, trust, and lifecycle** fields. This is a design/reference
document, not a work plan — implementation steps, effort, and open questions
live in `doc/improvements/archive/okf/okf_adoption.md` (proposal, executed
2026-08-18/19).

Upstream: [`GoogleCloudPlatform/knowledge-catalog/okf/SPEC.md`](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
(v0.2, July 2026), including its reference implementation
[`src/reference_agent/bundle/document.py`](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/src/reference_agent/bundle/document.py)
and [conformance tests](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf/tests).
This page was verified against `main` on 2026-08-18.

---

## 1. What OKF is

Google Cloud's **Open Knowledge Format** is a vendor-neutral, agent- and
human-friendly specification for representing *knowledge* as **a directory of
markdown files with YAML frontmatter**.

- **One file = one concept.** The file path is the concept's identity.
- **Frontmatter carries the structured, queryable surface**; the body carries
  prose, tables, and code that humans and LLMs read.
- **Minimally opinionated.** Only `type` is required. Everything else is opt-in;
  unknown keys must be preserved, never rejected.
- **Provenance/trust/lifecycle are first-class (v0.2):** `generated`,
  `verified`, `sources`, `status`, `stale_after`.
- **Reserved files:** `index.md` (directory listing / progressive disclosure)
  and `log.md` (date-grouped change history).
- **Cross-links:** ordinary markdown links (relative or bundle-relative `/`).

### 1.1 Conformance (OKF §11)

A bundle is conformant if:

1. Every non-reserved `.md` file has a parseable YAML frontmatter block.
2. Every frontmatter block has a non-empty `type` field.
3. Reserved files (`index.md`, `log.md`) follow their §8/§9 structure when
   present.

Consumers must not reject a bundle for missing optional fields, unknown
`type` values, unknown frontmatter keys, broken links, or missing `index.md`.

This project enforces (1) and (2) mechanically — `python3 -m
helpers.validators.frontmatter_schema --okf` sweeps every non-reserved
note (derived + OCR-source trees), shape-checks newsletter OKF blocks,
and prints the provenance census; an advisory-only variant rides
`make qa` via static_checks (completed.md #131).

### 1.2 Why this project is already ~80% OKF

The `findata/` vault is an OKF bundle in spirit:

| OKF concept | This project today |
|---|---|
| One concept per `.md` | `findata/Companies/<Sector>/<Name>.md` |
| `type` discriminator | `type: company \| sector \| super_sector` |
| `title` | `title:` frontmatter key |
| `description` | Absent (body prose only) |
| `resource` (canonical URI) | `permalink:` + `file_path:` |
| `tags` | `tags:` (namespaced `prefix/value`) |
| Cross-links | Obsidian wikilinks `[[Company]]`, `[[sector]]` |
| Provenance (v0.2) | **Only in DB columns** (`source_ref`, `as_of_edition`), not notes |
| Trust (`generated`/`verified`) | Sentinel-wrapped auto blocks vs hand-curated (implicit, not queryable) |
| Lifecycle (`status`/`stale_after`) | Absent |
| `index.md` / `log.md` | Absent |

The project also **exceeds** OKF conformance: OKF requires only a non-empty
`type`; this project enforces 10 required keys with regex constraints and
`additionalProperties: false` via `helpers/validators/frontmatter_schema.py` +
`doc/schema/frontmatter.*.v1.json`. No conformance work is needed — the format
is already stricter than the spec.

**Interop caveat (important):** because the project validator enforces
`additionalProperties: false` and 10 required keys, it is **stricter than OKF
§11**, which forbids consumers from rejecting a bundle for unknown keys, absent
optional fields, or unknown `type` values. The three deliberate deviations in
§3.3 (required array `verified`, required `sources[].id`, required
`generated.at`) compound this: a spec-valid OKF note that uses a bare `verified`
mapping, omits `sources[].id`, or omits `generated.at` will **fail** this
project's validator. Conclusion: this project is a *producer-only* OKF subset —
it can emit conformant OKF, but it cannot losslessly *consume* externally
authored OKF notes without first relaxing the validator. That is acceptable for
the stated goal (adopt the field *vocabulary*), but it must be stated explicitly.

### 1.3 What OKF actually adds here

The one genuine gap OKF names and this project already *computes but does not
store in a queryable place*: **who generated a note/claim, who verified it,
where it came from, and whether it is current.** These signals exist today only
as:

- `source_ref` prefixes in SQLite (`derive:...` / `manual:...` / `migration:...`),
- `as_of_edition` / `source_quote` columns on `quotes` and `company_metrics`,
- sentinel-wrapped `<!-- BEGIN auto chatter block -->` markers in company notes
  (`helpers/graph/derive_insights.py`),
- the `pdf_conv_md.py` / `capture_newsletter_images.py` pipeline provenance
  (source PDF, model used, manifest).

OKF v0.2 formalizes these into *optional, additive frontmatter keys* that a
consumer (agent, validator, UI) can filter on **before** reading the body. This
is the only substantive value the format adds to this codebase.

---

## 2. Current-state analysis (per area)

### 2.1 `helpers/` — the real leverage point

- `helpers/pdf/pdf_conv_md.py` writes **zero frontmatter** — output is bare
  markdown + wikilinks. Natural place to emit `generated: {by, at}` and
  `sources: [{resource, title, last_modified}]` for the source PDF.
- `helpers/pdf/capture_newsletter_images.py` writes a manifest
  (`<slug>_image_manifest.json`) but no note frontmatter.
- `helpers/graph/derive_insights.py` already distinguishes *auto-generated* vs
  *curated* via sentinel markers — literally OKF's `generated` vs `verified`
  split. Mapping it to frontmatter is mechanical.
- `helpers/validators/frontmatter_schema.py` is the enforcement point. Adding
  optional keys is a small JSON-Schema change per note type.
- Unaffected: `parse_newsletter.py`, `sync_tags.py`, graph helpers, validators
  other than the schema module.

### 2.2 `Reports/` — OKF's `sources` payload

The `Reports/*.pdf` files are exactly what OKF calls a *source*:
`{id, resource: Reports/<stem>.pdf, title, last_modified}`. No on-disk change
needed — they become the concrete `sources[]` entries emitted into every note
generated from them, closing the provenance loop that today stops at the DB
`source_ref`.

### 2.3 `frontend/` — intentionally a non-issue

`frontend/src/findata.ts` consumes `/api/*` JSON (DB-backed), never note files.
`file_path` appears only as a "View note" link. Note-frontmatter changes cannot
break the frontend. The only optional, product-level idea is UI trust gating
(e.g. badge "auto-derived" vs "human-reviewed" quotes) — out of scope here.

### 2.4 `tests/` — already an OKF-superset gate

`tests/test_frontmatter_schema.py` covers rogue keys, wrong types, bad enums,
`'N/A'` tickers, unparsable dates, missing required keys, corpus walker, and
key-doc determinism — stricter than OKF §11 conformance. Upstream exercises the
same surface via `okf/tests/test_document.py` (parse/serialize/validate,
`normalize_verified`, `trust_tier`, `is_stale`) and `test_bundle_tools.py`; no
need to port them, but our `_normalize()` date-object handling mirrors their
`is_stale()`.

---

## 3. Frontmatter design (additive, OKF v0.2 §5 shapes)

### 3.1 Principles

1. **Additive only.** No existing key changes meaning; nothing becomes
   required. A note with none of the new keys remains fully valid (matching
   OKF's "absence carries meaning" rule).
2. **Write provenance where it is generated.** `pdf_conv_md.py` writes
   `generated` + `sources` at conversion time. `derive_insights.py` writes
   `generated` (machine) and preserves `verified` (hand) semantics via the
   existing sentinel rule.
3. **One source of truth.** The JSON-Schemas in `doc/schema/` and the generated
   `frontmatter_keys.md` doc stay the single contract; the validator enforces it
   (as today).
4. **DB remains the writer of relational truth.** Note-side change only;
   SQLite schema and DuckDB cache are untouched.

### 3.2 New frontmatter keys (all optional)

Applies to `company`, `sector`, `super_sector` note types (newsletter inputs
intentionally carry no frontmatter).

```yaml
# --- provenance/trust (OKF v0.2 §5), ALL OPTIONAL ---
generated:                 # who wrote this content, and when (§5.2)
  by: derive_insights.py/v1  # actor convention: <producer>/<version> | human:<id> | process:<id> (§7)
  at: 2026-08-18T09:00:00Z  # ISO 8601; last meaningful content change
verified:                  # independent confirmations (§5.2); bare {by,at} == 1-element list
  - by: human:arun          # human: prefix => "human-reviewed" tier (§5.3)
    at: 2026-08-18T12:00:00Z
sources:                   # materials this concept derives from (§5.1)
  - id: bosch-amara-zydus   # stable key; joins body footnotes [^bosch-amara-zydus]
    resource: /Reports/Bosch_Amara_Zydus.pdf   # REQUIRED; URL, or bundle-relative (/Reports/...) path
    title: The Chatter: Bosch, Amara, Zydus & More
    author: process:pdf_conv_md   # optional OKF §5.1 credibility signal (actor convention, §7)
    last_modified: 2026-08-13   # credibility signal; YYYY-MM-DD
  # optional credibility signals per source: author, usage_count (+ shared usage_window)
status: stable             # draft | stable | deprecated (absent = stable) (§5.4)
stale_after: 2027-02-28    # absolute date; plain comparison today >= stale_after (§5.5)
```

**Trust tiers (§5.3)** — a consumer derives, never stores: no `verified` ⇒
*unverified*; `verified` by non-`human:` actors only ⇒ *machine-confirmed*;
any `human:<id>` verification ⇒ *human-reviewed*. The reference implementation
encodes exactly this in `document.py:trust_tier()` / `normalize_verified()` /
`is_stale()`.

Per-claim citation (optional, later): footnote-style references in quote blocks
keyed to `sources[].id`, so a quote in the body can name the edition/PDF it came
from — mirroring OKF's `[^source-id]` pattern and this project's existing
`source_quote`/`as_of_edition` DB columns.

### 3.3 JSON-Schema design

In each of `frontmatter.company.v1.json`, `frontmatter.sector.v1.json`,
`frontmatter.super_sector.v1.json`, add (schema-level `additionalProperties`
remains `false`; the new keys are simply enumerated):

```json
"generated": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "by": { "type": "string", "minLength": 1 },
    "at": { "type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}[Tt].*$" }
  },
  "required": ["by", "at"]
},
"verified": {
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "by": { "type": "string", "minLength": 1 },
      "at": { "type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}[Tt].*$" }
    },
    "required": ["by", "at"]
  }
},
"sources": {
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "id": { "type": "string", "minLength": 1 },
      "resource": { "type": "string", "minLength": 1 },
      "title": { "type": "string" },
      "author": { "type": "string" },
      "last_modified": { "type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$" }
    },
    "required": ["id", "resource"]
  }
},
"status": { "enum": ["draft", "stable", "deprecated"] },
"stale_after": { "type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$" }
```

**Deliberate deviations from the spec (all stricter, consistent with this
project's existing superset stance):**

- `verified` is schema-enforced as an **array**; the spec only requires
  consumers to *normalize* a bare `{by, at}` mapping to a one-element list
  (`document.py:normalize_verified()`). We forbid the bare form at write time.
- `sources[].id` is `required` here; the spec marks it optional ("SHOULD be
  present when the body cites the source"). Requiring a stable id is what makes
  per-claim footnote attribution safe — the exact motivation §5.1 gives for ids
  surviving reordering.
- `generated.at` / `verified[].at` are `required` here; the spec marks
  `generated.at` optional. Our writer always knows the timestamp.

The date-object normalization quirk already handled by
`frontmatter_schema._normalize()` extends naturally to these keys.

---

## 4. Out of scope (design decisions)

- **`memory/` (SQLite / DuckDB / `.vec.db`):** OKF is explicitly not a store
  ("you can index a bundle into one"). Converting stores is wrong; the only
  future use is *exporting* OKF bundles from SQLite as an interchange artifact.
- **`doc/` prose pages:** adding `type`/`description` frontmatter to the ~30
  active docs is cosmetic; deferred.
- **`index.md` / `log.md`:** optional progressive-disclosure files; no demand
  yet. Deferred.
- **Attested Computation (§10):** this project's DuckDB cache already enforces
  "SQLite is sole writer"; run-attestation machinery is not needed.
- **`frontend/`:** no file-format impact; UI trust badges are a separate
  product decision.
- **OKF conformance (`index.md`/`log.md`/bundle-root `okf_version`):** the
  vault is not distributed as an OKF *bundle*; adopting the field *vocabulary*
  is the goal, not conformant packaging.
- **Interop / consumer stance:** the project is a *producer-only* OKF subset
  (see §1.2 interop caveat). Consuming external OKF bundles would require
  relaxing `additionalProperties: false` and the required-key set; out of scope.
- **`description` / `resource` (OKF §4.1 recommended keys):** scoped out as
  real keys — the project already captures them implicitly (`permalink` /
  `file_path` stand in for `resource`; body prose stands in for `description`).
  Revisit only if an `index.md` generator or an external OKF consumer appears.
- **Source-tree tags/sync follow-up:** the OCR source trees gained their own
  schema (`doc/schema/frontmatter.newsletter.v1.json`), a namespaced tag
  vocabulary, and a `note_tags` SQL mirror AFTER this design landed — see
  `doc/improvements/archive/okf/newsletter_notes_adoption.md` (supersedes the
  "newsletter notes need no schema" note in §3.1; their OKF block is now
  schema-validated).
- **Activation (2026-08-19, `doc/improvements/archive/okf/okf_activation.md`):**
  the OKF metadata became operational — editions are graph nodes with
  `cited_in` edges projected from `sources[]` (canonical edition key = note
  STEM; `quotes.as_of_edition` is free text, never a join key), the
  `make analytics REPORT=coverage` matrix reads those joins, and
  `derive_insights.py --stale-only` renders only notes whose evidence moved
  past `generated.at`. Edition staleness (`stale_after` census) is covered
  by the `--okf` sweep's group-scoped report.
