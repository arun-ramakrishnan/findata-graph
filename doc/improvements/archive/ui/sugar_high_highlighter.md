---
title: "Swap dual highlighters for sugar-high — one bundled lexer, tokens.css theming, vendor purge"
status: executed
filed: "2026-09-12"
executed: "2026-09-12"
completed_md: "227"
area: "frontend/src, templates/, static/vendor"
---

<!-- schema: doc/okf/frontmatter.proposal.v1.json — the bold-line header
     below STAYS for human readers; the block above is the
     machine-checkable status (static_checks: Proposal lifecycle). On
     archival, flip status/executed/completed_md in the same change. -->
# Swap dual highlighters for sugar-high — one bundled lexer, tokens.css theming, vendor purge

**Date:** 2026-09-12 · **Status:** EXECUTED 2026-09-12 ·
**Completed:** completed.md entry #227 ·
**Area:** `frontend/src/core/markdown.ts` (one call site),
`templates/findata.html` + `templates/entity_detail.html` (four tags each),
`static/vendor/{hljs,prism}/` (deletion), `frontend/src/core/tokens.css`
(theme), `frontend/package.json` (one dep)

## 1. Motivation

The docs reader renders note markdown client-side with two syntax
highlighters loaded simultaneously: hljs (live) and Prism (dead). Trigger:
component evaluation 2026-09-12
(`doc/local/evaluations/web_components_assessment.md` — full study,
smoke-tested). Measured waste in the status quo:

- Prism ships in BOTH templates (`prism-core` + `prism-autoloader` +
  `prism-tomorrow` CSS) but its only call site,
  `initializeInteractiveElements()` (`markdown.ts:222`), is dead code —
  the function's own comment says "currently unreferenced by any view".
  Its autoloader also fetches per-language components over the network
  at render time.
- Two conflicting stylesheets load on every page (hljs `github-dark` +
  Prism `prism-tomorrow`); which theme wins depends on CSS order, not
  intent.
- Vendored weight: hljs 128K + Prism 80K = 208K static for one working
  call site (`markdown.ts:149 highlightCode()` →
  `window.hljs.highlight(code, {language})`).

## 2. Evidence (measured 2026-09-12, this box)

| Configuration | Result | Verdict |
|---|---|---|
| status quo (hljs live + Prism dead) | 208K vendored, 2 stylesheets, 4 template tags, runtime autoloader fetches | baseline — redundant by half |
| sugar-high npm 2.4.0, full registry, `bun build --minify` | 27,886 B (~10K for TS-only per upstream bench; smaller via `sugar-high/core` + per-language imports) | adopt |
| smoke test: sql / python / yaml / diff snippets | all produce `sh__token--*` spans; inline `var(--sh-*)` colors; diff line classes `sh__line--diff-add` | drop-in for the fences the corpus actually renders |
| DOM-free output | HTML string, no DOM dependency — safe in the post-DOMPurify augmentation step | fits marked → sanitize → augment pipeline unchanged |

Upstream: 1,350 stars, active (pushed 2026-09-09), MIT (declared in
package.json; no LICENSE file at repo top — §5). Import-path check
(corrected 2026-09-12 at execution: an earlier smoke test here was wrong —
it imported `lang` from the root package): `import { lang } from
"sugar-high/lang"` works on npm 2.4.0 and normalizes all fence aliases the
corpus needs (py/yml/sh/bash/ts/js/md/rb/c++/tf → canonical), unknown names
→ 'plaintext'. What was ruled
out: keeping hljs and merely deleting Prism (leaves 128K + theme
mismatch + no CSS-variable theming); adopting highlight.js v11 upgrade
(29.5K min per upstream bench, no token-class hooks, same global-script
pattern that caused the dual-vendor drift).

## 3. Design

`highlight()` returns an HTML string the pipeline already knows how to
consume; the swap is confined to `highlightCode()` internals plus theme
variables. sugar-high's `cx`/`mark` hooks go unused for now (YAGNI;
semantic classes `sh__token--*` suffice).

- **S1 — swap + theme (executed 2026-09-12).** `bun add sugar-high` in
  `frontend/`; rewrite `highlightCode()` (markdown.ts:149) to call the
  bundled `highlight(code, {lang})`, keeping the existing try/catch
  escaped-text fallback shape; fence languages normalized via `lang()`
  from `sugar-high/lang` (export verified at execution — the alias-table
  fallback was unneeded); the nine `--sh-*` variables appended to
  tokens.css (upstream dark hexes, scoped to `.code-block`). Bundle
  committed to `static/` per the Node-free-deploy convention.
- **S2 — vendor purge (executed 2026-09-12).** Remove from both templates: `prism-core`,
  `prism-autoloader`, hljs script tag, both highlighter stylesheets.
  Delete `static/vendor/hljs/`, `static/vendor/prism/`. Drop
  `hljs`/`Prism` from `types/vendors.d.ts`. Delete the dead
  `initializeInteractiveElements()` (unreferenced per its own comment;
  git history preserves it) — this also removes its `Prism.highlightAll()`
  branch, the only Prism consumer.
- **S3 — verification + snapshot (executed 2026-09-12).** `bun run build && bun run
  typecheck`; grep the tree for residual `hljs|prism` references (expect
  none outside git history); docs-reader pass over a note rich in the
  four smoke-tested fence languages (sql/python/yaml/diff) plus one
  `plaintext` fence; record before/after static/ size in the completed.md
  entry.

S1 alone is landable (status quo keeps working with hljs as fallback
until S2 removes it); S2 unblocks nothing downstream; S3 is the gate
record.

## 4. Acceptance criteria & shakedown

1. `cd frontend && bun run build && bun run typecheck` — both green.
2. `rg -l 'hljs|prism|Prism' frontend/ templates/ static/ --glob
   '!*.bundle.js.map'` — returns nothing after S2.
3. Docs reader (findata page): fenced sql/python/yaml/diff blocks render
   `sh__token--*` spans; a malformed/unknown fence language degrades to
   escaped plain text (fallback path exercised once deliberately).
4. Repeat the four-snippet smoke test against the *built bundle* import
   path, not just a scratch script (guards against esbuild
   tree-shaking surprises).
5. `make qa` full gates once at arc end, with user go.

| Projected outcome | Today | After |
|---|---|---|
| Highlighter static weight | 208K (hljs 128K + prism 80K) | ~0K vendor + ~10-28K inside findata.bundle.js |
| Stylesheets per page | 2 (conflicting themes) | 0 extra (tokens.css variables) |
| Render-time network fetches | autoloader per-language components | 0 |
| Working call sites | 1 of 2 loaded libraries | 1 of 1 |

## 5. Risks

- **Grammar edge cases differ from hljs** — mitigated by the escaped-text
  fallback (existing shape, worst case = unhighlighted code) and the S3
  fence-language pass over real corpus content.
- **API drift** — retracted at execution: the `lang()` named export works
  on npm 2.4.0 from `sugar-high/lang` (an earlier smoke test imported it
  from the root package — test error, not repo drift). `sugar-high@2.4.0`
  is pinned in package.json regardless.
- **esbuild IIFE + global-scope assumptions** — sugar-high is ESM with no
  deps; S1's committed-bundle build verifies before S2 removes hljs, so
  there is no window where the reader loses highlighting.
- **No LICENSE file upstream** — irrelevant for local/vault use; note it
  in the completed.md entry in case bundles ever ship externally.

## 6. Non-goals

- **web-llm is separate work** (per the 2026-09-12 evaluation: DEFER with
  explicit gates — no lane wants in-browser inference today; needs its
  own proposal if an "ask the vault" lane is ever filed). Nothing in
  this arc touches inference, embeddings, or the GPU doctrine.
- No markdown-renderer swap (marked + DOMPurify pipeline stays).
- No Prism/hljs re-vendoring, no autoloader tuning, no third theme.
- No `cx`/`mark`/`markLine` token customization beyond theme variables.
- No changes to the entity bundle's non-markdown surfaces beyond the
  shared `highlightCode` swap.

## Appendix — raw measurement log

| Run | Command | Result | Notes |
|---|---|---|---|
| 2026-09-12 | `du -sh static/vendor/hljs static/vendor/prism` | 128K / 80K | status quo weight |
| 2026-09-12 | `rg -n "Prism" frontend/src/core/markdown.ts` | :222 only, inside dead `initializeInteractiveElements` | "currently unreferenced by any view" (own comment) |
| 2026-09-12 | `bun build` full-registry import, `--minify` | 27,886 B | /tmp scratch, bun 1.4.2 |
| 2026-09-12 | smoke: `highlight(..., {lang:"sql"/"python"/"yaml"/"diff"})` | all tokenized; `var(--sh-*)` inline colors | npm 2.4.0 |
| 2026-09-12 | `import { lang } from "sugar-high/lang"` | all aliases normalize; unknown → 'plaintext' | correction: root-package import was the error |
| 2026-09-12 | upstream bench table (v2.2.2, TS-only) | 9.90 KiB min / 4.35 KiB gzip vs Prism 14.63 / hljs 29.54 | README, measured 2026-09-04 upstream |
