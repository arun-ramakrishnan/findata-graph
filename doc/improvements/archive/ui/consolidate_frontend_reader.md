---
title: "Consolidate the duplicated paper-register reader — entity.ts / docs.ts shared core, head partial, modal CSS"
status: executed
filed: "2026-09-03"
executed: "2026-09-04"
completed_md: 200
area: "frontend/src (entity.ts, views/docs.ts, core/), templates (findata.html, entity_detail.html), static (findata.css)"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Consolidate the duplicated paper-register reader (frontend + templates)

**Date:** 2026-09-03 · **Status:** PROPOSED ·
**Area:** `frontend/src` · `templates/` · `static/findata.css` ·
disposition index for duplicative TS/CSS/HTML across `entity.ts` / `views/docs.ts`

## 1. Motivation

The `entity.ts` and `views/docs.ts` views are two front-ends over the same
concept — a paper-register / company markdown reader. Today they each
maintain an independent copy of the whole reader stack: wikilink regex +
DOM walker, frontmatter extraction, masthead/chips rendering, and the
entity→file_path index. Meanwhile `templates/` duplicates the vendored
 `<head>` vendor core, and `static/findata.css` re-declares the same
 `.modal-content`/`.modal-header` base object with sizing/width/position differences.

A shared reader already has an obvious home: `frontend/src/core/markdown.ts`
owns the markdown pipeline both views use, and `core/` houses the other
shared modules (`dom.ts`, `router.ts`, `api.ts`, `toast.ts`).

## 2. Census (measured 2026-09-03) and disposition

| Site (entity.ts / docs.ts) | What is duplicated | Disposition |
|---|---|---|
| `entity.ts:39` / `docs.ts:72` | `WIKILINK_RE` regex constant (byte-identical) | **CONSOLIDATE** to shared module |
| `entity.ts:350-408` / `docs.ts:634-693` | `linkifyWikilinks` DOM tree-walker (same walker/filter/fragment logic; single `href` emission diff: `/entity/…` vs `href="#"`+`data-href`; comments differ) | **CONSOLIDATE** (parameterized `hrefFor`) |
| `entity.ts:32-36` / `docs.ts:55-59` | `SERIES_LABELS` constant | **CONSOLIDATE** |
| `entity.ts:42` / `docs.ts:75` | `CHIP_KEYS` array | **CONSOLIDATE** |
| `entity.ts:494-524` / `docs.ts:777-810` | four `fm*` frontmatter helpers (`fmString`, `fmScalar`, `fmGeneratedAt`, `fmPublisher`) | **CONSOLIDATE** |
| `entity.ts:163-202` / `docs.ts:580-625` | edition masthead + chips renderer (shared chips/masthead classes; structure differs: `h1`+tags vs `h3`+meta, `stale_after` slice) | **CONSOLIDATE** (parameterize structure) |
| `entity.ts:330-347` / `docs.ts:269-283` | `ensureWikilinkIndex` / `ensureVault` — same `/api/entities?limit=5000` fetch + stem/name→path Map kernel; shapes differ (Map\|null+try/catch vs list+index, no catch) | **CONSOLIDATE** (index builder; decide signature/error handling) |
| `templates/entity_detail.html:10-24` / `findata.html:10-24` | 5-line vendor `<script>` core identical; stylesheet links (6 vs 5 — entity has extra `entity_detail.css`) + SEC-3 comments + Cytoscape note differ | **PINNED local** (partial reverted 2026-09-03: operator prefers explicit per-page heads; the 5-line core stays duplicated by decision) |
| `findata.css` modal rules (two `.modal-content`/`.modal-header` pairs + `.modal*` chrome + dead overrides in 3 media queries + print) | zero references in templates/, frontend/src/, app.py, or built bundles — dead CSS, not live variants | **DELETE** (no call sites to update; live `.btn-secondary`/`.toc-*` interleaved rules kept) |
| `views/companies.ts`, `sectors.ts`, `stats.ts` (`fetch` → `isActive?` → display → `catch`) | repeated `try { fetchJson; if (isActive) display } catch { console.error }` (not in `docs.ts` `loadCatalog`/`runSearch`/`openNote`, which lack the guard) | **CONSOLIDATE** (`loadActive` helper) |

Every row is a pure reader-side duplication — no DB, no build config. The
behavioral variations are parameterizable, not reasons to keep two copies:
the wikilink `href` (docs.ts uses `href="#"`
+ `data-href`; entity.ts uses `/entity/...`), the masthead structure
(`h1`+tags vs `h3`+meta, `stale_after` slice), and the index-builder
signature (Map|null+try/catch vs list+index).

## 3. Design

- **`frontend/src/core/reader.ts`** (new, or fold into `core/markdown.ts`):
  export `WIKILINK_RE`, `SERIES_LABELS`, `CHIP_KEYS`, `linkifyWikilinks`
  (parameterized on `hrefFor(target)` / `hrefForMiss(target)`),
  `buildWikilinkIndex(entities)`, the four `fm*` helpers, and the
  masthead/chips renderers. Both `entity.ts` and `docs.ts` import from it.
  **Shape note:** the `fm*` helpers and `linkifyWikilinks` are PRIVATE
  CLASS METHODS today (`this.fmString(fm, k)` in both views), not free
  functions — extraction is a method→module-function shift at every call
  site (`fmString(fm, k)` as an import). Mechanical, but a wider diff
  than "move the function"; do it in one pass per view so `tsc` catches
  every `this.` miss.
- **Bundle**: these source files are *input* to the ESBuild bundle
  (`*.bundle.js` under `static/` is build output). Edit only
  `frontend/src/**`; do not hand-edit the bundles — rebuild via the
  existing `frontend` build step.
- ~~**`templates/_partials/head_vendor.html`** (or a `{% macro %}`):
  one copy of the 5-line vendor `<script>` core; both templates `{% include %}`
  it. Per-page stylesheet links + SEC-3 comments stay at the call sites.
  Keeps the asset pins in one place.~~ **REVERTED** 2026-09-03 — operator
  decision: per-page heads stay explicit (folder deleted, lines restored
  inline in both templates, render-verified).
- **`findata.css`**: the modal rules had no call sites at all (verified
  zero references across templates/, frontend/src/, app.py, built bundles)
  — deleted as dead CSS (~190 lines: both content/header pairs, close/
  actions/layout chrome, dead media-query + print overrides) instead of
  merging. Live interleaved rules (`.btn-secondary`, `.toc-*`) kept.
- **`core/loadActive.ts`** (or a `View` base): collapse the repeated
  `fetch → isActive? → display → catch` scaffolding in
  `companies`/`sectors`/`stats` — with an `onFetched` hook for unguarded
  post-fetch side work (sectors' cross-view filter population,
  companies' `totalCount`) and per-site `onError`; companies' `finally`
  stays at the call site.

## 4. Non-goals

- **Not** migrating marked/purify/prism/hljs from `<script>` globals into
  the npm bundle in this change — that changes the SEC-3 surface and the
  CSP analysis; the `<head>` partial just unifies *where* they're pinned
  (bundle-vs-script is a separate concern, noted for a future arc).
- **Not** rewriting the `escapeHtml` + template-literal row-assembly idiom
  wholesale — pattern-level similarity, not verbatim; out of scope.
- **No** behavior change to wikilink resolution or frontmatter rendering —
  a pure DRY refactor; the `href` difference stays as a parameter.

## 5. Gates

- TypeScript **type-check** via `make frontend-check` (runs
  `npx tsc --noEmit` + `prettier --check src types` — Node-gated) passes.
- Bundle rebuilds cleanly via `make frontend` (`npm ci && npm run build`
  → `static/findata.bundle.js` / `entity.bundle.js`); both
  `entity_detail.html` and `findata.html` render their existing content.
- Existing frontend/API integration tests
  (`tests/test_integration_ts_contract.py` and friends) pass unchanged.
- `md-lint` clean on the new/edited doc.
- `make qa` once at arc end.

## 6. Risks

- **Bundle drift**: if the build step is skipped, `static/*.bundle.js`
  stays stale while `frontend/src` changes. Mitigation: rebuild in the
  same change and verify the bundle hash/size changes.
- **`href` divergence regression**: the shared `linkifyWikilinks` must keep
  the two `href` behaviors strictly parameterized — covered by the
  ts-contract test.
- **Modal CSS**: deleted, not merged — the rules were dead (no call
  sites), so there is no variant-leak risk. Brace balance + zero remaining
  `modal` selectors verified; live neighbors untouched.

## 7. Note on corpus/surface

`findata/` (the vault) is a writer-owned markdown corpus, not code — no
consolidation applies there. This proposal is scoped to the reader
front-end and its templates/CSS only.
