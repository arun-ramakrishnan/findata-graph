---
title: Corpus uniformity — templates, contracts, and the doc/okf boundary
status: proposed
filed: '2026-08-31'
executed: null
completed_md: null
area: doc/templates/` (new)
---

# Corpus uniformity — templates, contracts, and the doc/okf boundary

**Date:** 2026-08-31 · **Status:** PROPOSED ·
**Area:** `doc/templates/` (new) · `doc/schema/` → `doc/okf/` (rename) ·
`doc/okf.md` → `doc/okf/README.md` · `helpers/validators/frontmatter_schema.py`
· `helpers/validators/static_checks.py` · `tests/test_templates.py` ·
`doc/procedures/markdown_parse.md` · `frontend/` (TS seed) ·
`helpers/maintenance/rebuild_script_search.py` (kind='ts')

> **Execution order (agreed 2026-08-31):** small independent work first —
> S4 → S5 seed → S6 (Stage 1, no wide diffs) — then the wide steps in
> footprint order, each behind an explicit go: S1 rename sweep (~30
> files, hard prerequisite for S2/S3) → S2 → S3 incl. its backfill run →
> S7a prettier normalize (13 files + lockfile) → S7b ruff-format
> 209-file normalize LAST and only after the stgit stack drains. Full
> gates once at wrap, then archive + completed.md entry.

## 1. Context and assumption corrections

Three assumptions from the 2026-08-31 discussion, corrected before design:

1. **"These are non-OKF templates" — CORRECT, and deliberate.** The code
   templates (`python_module.py`, `mojo_module.mojo`) and the proposal
   skeleton carry no OKF frontmatter: OKF provenance machinery
   (`sources[]`, `generated/stale_after`) exists to make knowledge claims
   freshness-checkable, while code files are gated by ruff / `mojo format`
   and the proposal skeleton by `test_templates.py`. Different artifact
   class, different gate.
2. **"You followed option 2 (full proposal frontmatter)" — NOT YET.** What
   landed is the template encoding the CURRENT bold-line convention, with
   an explicit comment that it gains frontmatter if the contract lands.
   This proposal adopts **option 2 as the chosen path** (user selection
   2026-08-31) and specs it as Slice S3.
3. **"Tests don't need their own template" — PARTLY.** Fuzz tests need no
   separate template (they are a marker + hypothesis-seed section of a
   test module). But test modules as a class DO carry contracts ordinary
   modules don't: marker routing (live / integration / fuzz decide which
   gate runs them), xdist-safety rules (never open a real DB read-write —
   the 2026-08-31 `test_integration_perf.py` fixture race), conftest reuse
   (never redefine `_UNIT_SCHEMA`/seeded fixtures), and the burst +
   best-of-3 pattern for any timing assertion. Those rules are exactly
   what new test files get wrong; they belong in a small
   `doc/templates/test_module.py` (Slice S4).

## 2. Definitions — contract vs template (the boundary the questions ask for)

| | **Contract (schema)** | **Template (seed)** |
|---|---|---|
| Question it answers | "Is this EXISTING artifact valid?" | "How do I start a NEW artifact correctly?" |
| Form | JSON Schema, `additionalProperties: false` | copy-paste skeleton, conformant by construction |
| Enforced by | a **validator** plugged into the gate (`frontmatter_schema`, static_checks P0 checks) | a **guard test** (`tests/test_templates.py`) that keeps the seed itself conformant |
| Lives in | `doc/okf/*.v1.json` (post-rename) | `doc/templates/*` |
| Drift caught by | the validator, at gate time | the guard test, at gate time |
| Rule | every contract has exactly one validator registration | every template demonstrates exactly one contract's shape; when a contract changes, schema + template change in the same commit |

A template is NOT a lenient schema and a schema is NOT a strict template —
they are start-point and acceptance-test for the same shape.

### 2.1 Pairing — every template names its contract, every contract names its template

Templates and schemas live in SEPARATE directories on purpose (seeds vs
contracts, §2's table) — so pairing is enforced by pointers, not by
directory adjacency, and the pointers are checked:

- **Template → contract:** each template's header carries the target,
  e.g. `# schema: doc/okf/frontmatter.company.v1.json` in the YAML/Python
  seeds; code templates declare their toolchain contract instead
  (`# contract: pyproject.toml ruff config (make lint, lint-audit)` /
  `# contract: mojo format copy-diff gate`).
- **Contract → template:** each `doc/okf/frontmatter.*.v1.json` gains the
  JSON-Schema extension keyword `"x-template": "doc/templates/<seed>"`.
  `x-` keywords are ignorable-by-spec to validators, so
  `frontmatter_schema` needs no change to keep validating; our guard
  reads them.
- **Enforcement — one manifest, one guard:** `PAIRINGS` (a dict in
  `tests/test_templates.py` — the same registry pattern as
  `SCHEMA_FILES`) is the single place pairing is declared, and one test
  iterates it to assert: (a) every `doc/okf/*.v1.json` carries
  `x-template` and the referenced file exists; (b) every file in
  `doc/templates/` declares a contract and the target exists; (c) for
  YAML pairs, template keys ⊆ schema properties; (d) the
  `doc/templates/README.md` index table matches the directory listing.
- Unpaired is a THIRD state, not a default: an OKF schema without a
  template (or vice versa) fails the guard — a new pairing is a
  conscious edit to `PAIRINGS`, which is the review point.

## 3. Slice S1 — `doc/` consolidation: `doc/okf/`, `doc/design/`, rename sweep

**EXECUTED 2026-08-31** (user go). 6 git-mv renames + 37-file repoint
sweep (ordered: `doc/schema.md` before `doc/schema/`) + the two
split-path `SCHEMA_DIR` constructions (frontmatter_schema.py,
test_templates.py) + regenerated `frontmatter_keys.md` (header drift =
the path itself). Execution found three fixture-shaped traps the
inventory missed — tmp trees and assertions that assumed design docs at
doc/ ROOT: `seeded_docs` writes architecture/graph_design under
`doc/design/` now; `/api/docs/content?path=architecture.md` (exact
rel-path resolution, no basename matching) became
`?path=design/architecture.md`; and the `section == ""` assertions
became `"design"` — doc/ has NO loose top-level files anymore.

`doc/schema/` holds ONLY OKF frontmatter contracts (4 ×
`frontmatter.<type>.v1.json` + the GENERATED `frontmatter_keys.md`) — the
name hides that. And the general design docs sit loose in `doc/`'s root.
One move fixes both, and gives `doc/` an explicit five-class taxonomy:

| Directory | Class | Contents |
|---|---|---|
| `doc/design/` | what the system IS and WHY | `architecture.md`, `findata.md`, `graph_design.txt`, `db_schema.md` (was `schema.md`) |
| `doc/okf/` | CONTRACTS (JSON Schemas) | the 5 `frontmatter.<type>.v1.json`, generated `frontmatter_keys.md`, `README.md` (was `doc/okf.md`) |
| `doc/templates/` | SEEDS | code/note/proposal skeletons |
| `doc/procedures/` | how to RUN things | existing |
| `doc/improvements/` | work history | `pending.md`, `completed.md`, `archive/` (`doc/local/` stays machine-local, gitignored) |

Mechanically complete:

- `git mv doc/schema doc/okf`; `git mv doc/okf.md doc/okf/README.md`;
  `git mv` the four design docs into `doc/design/` (schema.md renames to
  `db_schema.md` in the same mv — it documents DATABASE schemas, a
  different domain from OKF, so "schema" unambiguously means OKF
  contracts after this arc).
- Repoint hardcoded paths: `frontmatter_schema.py` (`SCHEMA_DIR`,
  `KEY_DOC`, line ~52) is the only code that READS the schema dir; the
  design-doc references in code are docstring/table prose (7 sites —
  Appendix), plus the root `README.md` doc-index table. Watch for the
  RELATIVE link in `markdown_parse.md` §YAML (~L321,
  `../schema/frontmatter_keys.md`) — it breaks quietly under the rename.
  S2 executions joined the sweep (2026-08-31): the four
  `doc/templates/*_note.yaml` seeds' `# schema:` header lines, the
  `PAIRINGS` registry + `SCHEMA_DIR` constant in
  `tests/test_templates.py`, and the schema column of
  `doc/templates/README.md`'s Index all reference `doc/schema/` paths.
  `doc/improvements/` history stays as-is: completed.md and archive
  entries are historical record, never edited backwards.
- Regenerate the reference: `python3 -m helpers.validators.frontmatter_schema --emit-doc`.
- Effort: ~2h incl. index refresh. Pure rename — no behavior change,
  gates must stay green untouched.

Post-S1 layout (contracts and seeds adjacent, paired by the §2.1
registry — not merged, because §2's boundary is the point):

```
doc/
  design/               ← what the system IS and WHY
    architecture.md  findata.md  graph_design.txt  db_schema.md
  okf/                  ← CONTRACTS (JSON Schemas + their README)
    README.md             (was doc/okf.md)
    frontmatter.<type>.v1.json   × 5 (company, sector, super_sector,
    frontmatter_keys.md          newsletter, proposal — each carries
                                 "x-template")
  templates/            ← SEEDS (each declares `# schema:` / `# contract:`)
    python_module.py  mojo_module.mojo  test_module.py  proposal.md
    company_note.yaml  sector_note.yaml  super_sector_note.yaml
    newsletter_note.yaml
    README.md           (index table; guard-checked against the listing)
  procedures/  improvements/  local/
```

## 4. Slice S2 — note frontmatter templates move into `doc/templates/`

**EXECUTED 2026-08-31** (pre-S1 by user direction — the seeds, the
PAIRINGS registry, and the README Index column reference `doc/schema/`
paths and join the S1 repoint sweep; see §3).

The entity/sector YAML template currently lives inline in
`markdown_parse.md` §YAML Front Matter. Single-source-of-truth rule: the
CANONICAL seed moves to `doc/templates/`, the procedure REFERENCEs it
(never re-states the YAML — duplicated contracts drift).

- `doc/templates/company_note.yaml` (entity frontmatter, from
  markdown_parse.md's template — `ticker: null` rule, quoted dates,
  underscore permalinks, market_cap tag derivation notes)
- `doc/templates/sector_note.yaml` (+ super_sector delta note — the
  permalink no-leading-slash quirk)
- `doc/templates/newsletter_note.yaml` (the `pdf_conv_md.py`-emitted
  shape: series/publisher tags, sources[] provenance — documentation of
  the writer's contract, the writer itself is the only producer)
- New guard in `test_templates.py`: the §2.1 `PAIRINGS` registry — every
  YAML template carries `# schema: doc/okf/...` and its key set ⊆ the
  corresponding schema's properties (a seed that demonstrates a rogue
  key is itself a bug); every `doc/okf/*.v1.json` carries `x-template`
  back. Keys are parsed with the existing YAML loader; no new validator
  process.
- `doc/templates/README.md`: the index table (template · contract ·
  validator · gate), guard-checked against the directory listing so a
  template can never exist unlisted.
- `markdown_parse.md` §YAML keeps the prose rules (the four drift rules),
  links the templates, drops the inline YAML block.
- Effort: ~2h.

## 5. Slice S3 — proposals contract, option 2 (FULL; user-selected)

**EXECUTED 2026-08-31** (user go) — 36 files backfilled, not the ~26
the recon counted: the bold-line headers have FOUR spellings
(`**Date:**` / `**Date**:` / `**Status:**` / `**Status**:` — colon in
or out of the bold), which the recon's single pattern under-matched.
Two files' headers mis-pointed at wrong completed.md numbers and were
corrected from the completed.md entries themselves:
maint_full_zero_churn #143→#147 (its #143 belongs to
sql_capability_unlocks), local_embeddings #115→#141 (#115 is the
openai-removal entry). One absorption encoded:
google_finance_ticker_fallback → 152 ("absorbed" per completed.md);
one compound: graph_docs_ui_redesign → 145+146 (pattern widened to
allow + chains). okf_sources_maintenance keeps 135 — recorded as a
NESTED "#135" bullet inside entry #134, not a top-level heading. The
proposals README live list was cleaned in the same change (executed
bullets had lingered; the new Proposal-lifecycle check flags them) and
gained checklist item 6 (flip frontmatter on archival).

- `doc/okf/frontmatter.proposal.v1.json`: `title` (string),
  `status` (enum `proposed|executed`), `filed` (date), `executed`
  (date or null), `completed_md` (string or null — the `completed.md`
  entry number), `area` (string), `additionalProperties: false`,
  `"x-template": "doc/templates/proposal.md"`.
- Validator: `proposal` type registered in `frontmatter_schema.py`'s
  `SCHEMA_FILES`; the check runs on `doc/improvements/proposals/*.md`
  (archive copies keep validating — `status: executed` there is legal).
  Registration detail (recon 2026-08-31): `DIR_TO_TYPE` maps
  `findata/<dirname>` → type and does NOT fit proposals — the walker
  needs a second, decoupled loop over `proposals/` + `archive/**`
  (precedent: `check_okf_conformance`), excluding both `README.md`
  files from validation.
- static_checks P0-style consistency check (the option-1 check, folded
  in): every file in `proposals/` has `status: proposed`; no `archive/`
  proposal keeps `status: proposed`; the proposals README's live list
  references only existing files. Lands as one `(label, fn)` entry in
  static_checks' `CHECKS` registry.
- Backfill (recon-corrected 2026-08-31): **25 archive + 1 live** get the
  block via script (parse the existing bold-line header; review the
  diff — the header line STAYS for human readers, frontmatter is
  additive). The live file is `corpus_uniformity.md` itself
  (`gate_xdist_phase2.md` is already archived — earlier draft was
  stale); 12 further archive .md files are triage/acceptance docs with
  no proposal header and are EXCLUDED.
- `doc/templates/proposal.md` gains the frontmatter block + the README
  archival checklist gains "update frontmatter status in the same
  change" (the writer-conformance rule: contract + writer + check land
  together).
- Effort: ~3h (schema+validator 1h, backfill script+review 1h, docs 1h).

## 6. Slice S4 — `doc/templates/test_module.py`

Small variant of `python_module.py` encoding the test-only contracts:

- marker routing (what `live` / `integration` / unmarked mean for gate
  placement; declare new markers in pytest.ini — `--strict-markers`)
- xdist-safety: no RW connections to real DBs (RO when the shared cache
  exists; per-test temp DBs otherwise), per-worker cache_dir is handled
  by the gate runner
- conftest reuse (never redefine seeded fixtures/schemas)
- timing assertions: summed burst above any skip threshold + best-of-3,
  budgets in `make perf` for wall-clock (ratio guards only in pytest)
- hypothesis: `--hypothesis-seed=0` is pinned; how to explore with a
  fresh seed

No new validator — `test_templates.py`'s ruff selection covers the seed.
Effort: ~1h.

## 7. Slice S5 — TypeScript seed (`ts_module.ts`) and the language matrix

Follow-up scope question (2026-08-31): "we use more languages —
typescript, javascript etc. — should they be in the templates design?"
Survey: first-party TS is `frontend/` (~5,700 LOC over ~20 files, actively
developed — the UI arcs). First-party JavaScript is **zero** (only
vendored `static/vendor/*.min.js` and the committed build output
`static/*.bundle.js`). So: one TS seed, plus the no-first-party-JS rule
written down — a template for an empty class is noise.

`doc/templates/ts_module.ts` encodes the frontend-specific contracts a
newcomer gets wrong:

- `// contract: frontend/tsconfig.json — tsc --noEmit strict
  (make frontend-check, advisory)`
- `// contract: frontend/package.json build — esbuild bundles src/*.ts →
  ../static/*.bundle.js is COMMITTED; the Python deploy stays Node-free
  (never hand-edit bundles)`
- `types/api.ts` is the response-shape contract:
  `test_integration_ts_contract` demands every declared field — including
  `?:` optional ones — be PRESENT in responses; emit null, never omit.
- vendor boundary: third-party code lives in `static/vendor/` (committed),
  typed via `types/vendors.d.ts`.
- the module header doc block is the doc-extraction source once S6 lands
  (first line = purpose — the same rule as the Mojo template).

The S2 README index grows a **language matrix** — this table IS the
extension framework for "format + docs per language"; adopting a language
means adding a row and following it, never inventing a new gate pattern:

| Language | Seed | Format gate | Types gate | Doc extraction | Placement |
|---|---|---|---|---|---|
| Python | `python_module.py` / `test_module.py` | `ruff format --check` (S7) | `ty` (make types / types-tests) | AST docstrings → script_search (EXISTS) | qa (lint+format); types-tests advisory |
| Mojo | `mojo_module.mojo` | `mojo format` copy-diff (EXISTS) | compiler | `mojo doc` → kind='mojo' (EXISTS) | qa |
| TypeScript | `ts_module.ts` (this slice) | `prettier --check` (S7) | `tsc --noEmit` (EXISTS) | tsc compiler API → kind='ts' (S6) | advisory |
| JavaScript | none — no first-party JS (rule, not omission) | prettier ignores `static/` | — | — | — |
| Markdown notes | YAML seeds (S2) | — | frontmatter validators (EXISTS) | doc_search (EXISTS) | static-checks |

Guard: `test_templates.py` extends its directory scan to `.ts` seeds
(ruff doesn't cover them) — assert the two `// contract:` lines and the
header block exist, and that the matrix's seed column matches the
directory listing. Effort: ~1.5h.

## 8. Slice S6 — TS footprint in script_search (`kind='ts'`)

Mojo got `mojo doc → kind='mojo'`; Python has AST docstrings; TS is
invisible to both content-addressable indexes — "which view wires the
stats endpoint" or "where is the double-click-isolate handler" is a grep
question today. Mirror the mojo_doc_script_search arc:

- `rebuild_script_search.py` gains a TS footprint over
  `frontend/src/**` + `frontend/types/**`: exported symbols (signatures)
  + the module header doc block (purpose). CORRECTED during execution
  (2026-08-31): the planned typescript-compiler-API extraction is
  IMPOSSIBLE with the installed toolchain — typescript@7 is the native
  (Go) compiler and its npm package ships only version metadata, no JS
  API. Rather than pin typescript@5 just to parse, the extractor
  (`frontend/scripts/extract_ts_docs.mjs`) is a structural scanner:
  depth-0 `export` statements + preceding JSDoc, with documented
  limitations (computed names, brace-in-string edges) — acceptable for
  a BM25 intent index, not a correctness gate. Content-addressed doc
  cache + staleness flow exactly like the Mojo footprint; tests mirror
  `tests/test_rebuild_script_search_mojo.py`.
- `script_query` needs a ONE-LINE change (recon 2026-08-31): the
  `--kind` choices list is explicit (`script|test|make|mojo`) — add
  `"ts"`; `search_scripts()` filters by free string, so nothing
  downstream moves.
- A `_scan_ts_units` analog in the read-path staleness probe is
  MANDATORY, not optional: if TS rows land in `script_search_meta` but
  `_scan_disk_units` never yields them, `meta.keys() - on_disk` is
  permanently non-empty and the index reads stale forever.
- Node-absent degradation follows the index-stale contract: warn + answer
  (or exit 1 with the build command), never a silent empty kind. The
  builder mirrors `_run_mojo_doc`'s silent-skip (extractor returns None →
  degraded purpose from header comment/filename).

Deliberate non-goal (§13): no published docsites (pdoc/typedoc HTML) —
the extraction product is the searchable index, same doctrine as Mojo.
Effort: ~2-3h.

## 9. Slice S7 — format gates for the two unformatted languages

"`mojo format` is part of the build — should other languages get the
same?" Yes; Python and TS are the gaps. Follow the Mojo precedent:
normalize once, then a gate keeps it canonical.

- **Python — `ruff format`.** `ruff format --check .` added to
  `test_lint_gates.py` (native `--check` — no copy-diff workaround
  needed) + a `make format` fixer (help line sorts between
  `frontend-check` and `fuzz`). Effective width is the repo's
  `[tool.ruff] line-length = 100` — NOT ruff's 88 default (recon
  2026-08-31) — so a `[tool.ruff.format]` section is only needed if the
  review pass wants non-default knobs. Measured 2026-08-31: **209 of
  1,333 files would be reformatted** — one mechanical normalization
  commit, landing TOGETHER with the gate (qa must not go red in
  between). The review pass is not purely mechanical: format wraps to
  width 100 while lint ignores E501, so long help/SQL lines need eyes
  on them. Sequencing constraint: see §12 — a 209-file Python diff
  conflicts with every open stgit patch touching `.py`.
- **TypeScript — prettier, scoped to `frontend/`.** EXECUTED 2026-08-31:
  prettier 3.9.6 devDependency added; `.prettierrc` = `printWidth 100,
  tabWidth 4` — chosen by churn probe (the frontend is 4-space indented
  and double-quoted: tabWidth 4 lands at 8 files / 1,309 lines vs ~14
  files / ~7k at prettier defaults; `singleQuote` tripled it and was
  rejected). Gate wired as an appended line in the advisory
  `frontend-check` target (tsc + prettier); one-time `--write` over
  src + types, and the COMMITTED bundles rebuilt in the same change
  (esbuild preserves comments in non-minified output, so the JSDoc
  reflow flows into `static/*.bundle.js` — verified `make frontend-check`
  green and bundle rebuilt). Prettier ignores nothing else: explicit
  `src types` paths mean `static/` (vendored + bundles) is never touched.
- **eslint: deferred.** At ~20 files, `tsc --noEmit` strict already
  catches the bug classes eslint would; config churn without a
  motivating bug. Revisit if the frontend grows.

Effort: ~1h gates + ~3.5h normalization (Python review dominates).

## 10. Validator matrix (what plugs where — answering "are we writing validators?")

| Artifact class | Validator | Plugged into |
|---|---|---|
| notes (company/sector/super_sector/newsletter) | `frontmatter_schema` (EXISTS) | `make static-checks` |
| proposals (post-S3) | `frontmatter_schema` + static_checks status/dir consistency | same |
| note YAML templates (post-S2) | §2.1 `PAIRINGS` guard: mutual pointers + key-set ⊆ schema | `tests/test_templates.py` |
| python/mojo/proposal templates | rot guards (EXISTS, this session) | default pytest suite |
| TS template + language matrix (post-S5) | `.ts` seed guard: contract lines + header block + matrix ↔ directory | `tests/test_templates.py` |
| template ↔ schema pairing (all) | §2.1 `PAIRINGS` manifest — unpaired is a failure | `tests/test_templates.py` |
| helpers/*.py shebangs, bare-DB | static_checks P0 checks (EXISTS) | `make static-checks` |
| Python + TS formatting (post-S7) | `ruff format --check` / `prettier --check` | qa (`test_lint_gates`) / advisory (`frontend-check`) |
| TS doc extraction (post-S6) | search-index staleness flow | `make search-fresh` / advisory |

No new validator FRAMEWORK — every new check lands in an existing
registry (`SCHEMA_FILES`, `_CHECKS`-style tuple, `test_templates.py`).

## 8. Acceptance criteria

1. `rg -l "doc/schema" | rg -v "doc/improvements/"` → 0 files, and the
   same for the four moved design docs' old paths (`doc/architecture.md`,
   `doc/findata.md`, `doc/graph_design.txt`, `doc/schema.md`) outside
   `doc/improvements/` history.
2. `make static-checks` green with the proposal validator active; a
   deliberately-rogue-key proposal doc FAILS the gate (tested once).
3. `tests/test_templates.py` extended: 3 new guards green (§2.1 `PAIRINGS`
   mutual pairing incl. `x-template` on every `doc/okf/*.v1.json`;
   note-template ⊆ schema; proposal template carries frontmatter).
4. `pytest -m "not live" -n auto` fully green; all three search indexes
   FRESH after the move (doc_query must find `doc/okf/README.md` for
   "okf frontmatter contract" and `doc/design/architecture.md` for
   "operational path").
5. (S5) `doc/templates/ts_module.ts` exists, carries both `// contract:`
   lines, and the README language matrix matches reality — every gate the
   table names is wired where the table says its Placement column does.
6. (S6) `kind='ts'` rows appear after a script-index rebuild;
   `script_query "graph stats endpoint" --kind ts` returns `frontend/src`
   hits; `make search-fresh` covers the TS footprint.
7. (S7) `ruff format --check .` and `prettier --check` pass in their
   placements; a deliberately misformatted seed fails its gate (tested
   once each).

## 12. Risks

- **23-file reference sweep** — mechanical but wide; the Appendix is the
  checklist. External clones/notes referencing `doc/schema` by path break
  quietly → acceptable (repo-internal contract; `doc_query` still ranks
  the new path).
- **Backfill mis-parses** the bold-line headers on unusual historical
  proposals — script outputs a review diff; anything ambiguous is
  hand-annotated, never auto-guessed.
- **`frontmatter_keys.md` regeneration** after the rename must precede
  the gate run (it is GENERATED — don't hand-edit).
- **209-file normalization vs the patch stack (S7)** — the `ruff format`
  gate and the reformat land in ONE commit, but the diff conflicts with
  any open stgit patch touching `.py` (conflicts are the mechanical
  whole-file-reformat kind, but noisy). Sequence the normalization after
  the current stack drains; the gate itself can merge earlier only in the
  same change as the reformat, never alone.
- **Node enters the advisory path (S6/S7)** — prettier and the TS
  footprint need node. Acceptable: `frontend-check` already requires it
  and advisory is non-gating; the footprint builder degrades per the
  index-stale contract (warn + answer) when node is absent.

## 13. Non-goals

No OKF provenance (`sources[]`/`stale_after`) on proposals or code
templates (wrong artifact class — §1.1); no new note types; no changes to
what any existing validator asserts; no YAML-for-code-files. Also: no
JavaScript template (empty class — the rule is "no first-party JS", not
an omission to fix later); no eslint adoption (deferred, §9); no
published per-language docsites (pdoc/typedoc HTML) — per-language doc
extraction feeds the searchable index only, same doctrine as Mojo.

## Appendix — `doc/schema` reference inventory (2026-08-31, 23 files)

Code (path constants — MUST repoint): `helpers/validators/frontmatter_schema.py`
(`SCHEMA_DIR`, `KEY_DOC`), `helpers/validators/static_checks.py`,
`helpers/validators/verify_notes.py`, `helpers/misc/database_integrity_check.py`,
`helpers/pdf/pdf_conv_md.py`, `app.py`, `helpers/maintenance/migrate_to_graph_edges.py`.
Data: `helpers/misc/embed_eval_questions.json`. Docs: `README.md` (root),
`doc/okf.md`, `doc/findata.md`, `doc/procedures/markdown_parse.md`,
`doc/schema/frontmatter_keys.md` (moves with the dir), `doc/improvements/completed.md`
+ `archive/tooling/mcp_tool_eval.txt` (historical — leave).

## Appendix 2 — design-doc reference inventory (S1 mv sweep, 2026-08-31)

Docstring/table prose in code (repoint): `sync_tags.py:16` (doc/architecture.md §5),
`snapshot_db.py:217` + `db_maint.py:375` (doc/graph_design.txt §9.3),
`migrate_to_graph_edges.py:11,40` (graph_design.txt §4 + doc/schema.md),
`build_sector_hierarchy.py:180` (doc/findata.md). Root `README.md` doc-index
table (4 rows + the findata spec link at :187). `rebuild_doc_search.py:6`
prose mention (indexing is recursive over doc/ — moved files stay indexed
with their new paths automatically). Historical: `doc/improvements/**` (leave).
