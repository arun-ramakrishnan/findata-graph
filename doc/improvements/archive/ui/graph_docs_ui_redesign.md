---
title: Graph + Docs UI Redesign — "The Research Desk"
status: executed
filed: '2026-08-22'
executed: '2026-08-22'
completed_md: '145'
area: app.py` (4 new read-only endpoint groups)
---

# Proposal: Graph + Docs UI Redesign — "The Research Desk"

**Status:** IMPLEMENTED — S0–S7 executed 2026-08-22 (implementation log
§10, R1–R7). Full gates pending explicit user permission; archive move
follows after gates + user push. (Originally PROPOSED 2026-08-22; all
five open questions resolved 2026-08-22, §9.)
**Date:** 2026-08-22
**Author:** Agent analysis (user-directed); current-state inventory from a
full read of `app.py` (2,331 lines), `frontend/src/findata.ts` (2,300 lines),
`static/` + `templates/`, and the graph helper layer.
**Scope:** `app.py` (4 new read-only endpoint groups), `frontend/` + `static/`
+ `templates/` (Graph, Docs, entity pages, shared shell/CSS), tests. No
schema changes, no write endpoints, no SQLite/DuckDB mutations from the web
layer.
**Builds on:** `sql_capability_unlocks.md` (archived; BFS shortest path,
`similar_notes`, `edition_companies`, `v_note_embeddings`),
`local_embeddings.md` (archived; hybrid search + semantic neighbors),
`okf_sources_maintenance.md` (archived; `sources[]` frontmatter),
`integration_fuzz_enhancement.md` (archived #144).

---

## 1. TL;DR

The web app's API layer is far richer than its UI. Of ~24 JSON routes,
**ten graph-power surfaces have no UI at all** (semantic neighbors, similar
notes, edition companies, events timelines, 8 metrics incl. louvain
communities, co-mentions, cross-sector bridges, edges-by-year, peers, hybrid
search). The Graph view renders **every** node/edge (1,209 / ~4,110) with
labels forcibly blanked and a capped approximate layout; the Docs view
renders `doc/` markdown but not `[[wikilinks]]`, not frontmatter, not the
vault, and **runs `marked` without DOMPurify** (a real XSS gap the entity
page doesn't have). Meanwhile four capability families are CLI-only
(near-duplicates, link-prediction suggestions, 4 unserved centralities +
voterank, 5 analytics reports).

This proposal rebuilds the front of house as a two-register **research
desk**: a dark analytical **Lens** for the graph (cytoscape rework on
fcose, edge-semantic color system, temporal As-Of scrubber, five modes) and
a warm-paper **Reading Room** for the corpus (unified doc/ + vault reader,
live wikilinks, frontmatter provenance chips, related-notes rail), plus the
4 read-only endpoint groups that feed the new panels. Seven slices; the
2,300-line single-file TS app is split into modules first,
behavior-preserving, before any redesign lands on top.

## 2. Background — current state (measured, file:line)

### 2.1 Serving & API layer (`app.py`)

- Flask, 2 HTML routes (`/` → `templates/findata.html`, `/entity/<path>` →
  `entity_detail.html`) + ~24 JSON routes; port 5200; CSP `default-src
  'self'` (app.py:2272-2282), no CDN anywhere; JSON-404 parity for
  `/api/*` (app.py:2304); weak ETag + 304 on `GET /api/graph/*` from
  `_build_meta.built_at` (app.py:1216-1251); lazy DuckDB singleton with
  60s error TTL (app.py:123-176). **No DB writes** except
  `POST /api/graph/refresh` (rebuild, app.py:2229).
- Unwired-but-existing endpoints (zero call sites in the bundle):
  `/api/graph/semantic/<name>` (app.py:1570, company VSS neighbors),
  `/api/graph/similar/<path>` (app.py:1620, note VSS),
  `/api/graph/edition_companies` (app.py:1668), `/api/events/<name>`
  (app.py:1469, dated timeline), `/api/graph/metrics/<metric>`
  (app.py:2037; pagerank, degree, betweenness, closeness, eigenvector,
  clustering, louvain, wcc), `/api/graph/co-mentions` (app.py:2176),
  `/api/graph/bridges` (app.py:2200), `/api/graph/edges-by-year`
  (app.py:2215), `/api/graph/peers/<name>` (app.py:1268),
  `/api/search?hybrid=1` (app.py:610 — RRF fusion of BM25 + cosine;
  the UI never passes `hybrid`).

### 2.2 Frontend

- One 2,300-line TS class (`frontend/src/findata.ts`) — no router, no
  state library, no fetch wrapper; inline `onclick` strings requiring a
  `window.viewer` global (README.md:44-56 contract). Types in
  `frontend/types/api.ts` (the shape-drift gate — esbuild doesn't
  typecheck, `tsc --noEmit` does). esbuild IIFE → **committed**
  `static/findata.bundle.js` (76KB) so deploy stays Node-free.
- Five tabs: Companies / Sectors / Statistics / Graph / Docs
  (findata.html:31-47). Entity detail is a separate 667-line vanilla-JS
  page (`static/entity_detail.js`) outside the TS build.
- **Graph view** (findata.ts:1164-2159): cytoscape 3.28 (vendored global).
  Ego mode (neighbors bundle, as_of already supported) and Cloud mode
  (`/api/graph/cloud`) which renders **all** elements: edge labels
  blanked and font-size 0 because "4110 edge labels are the #1 render
  cost" (findata.ts:1434, 2056-2071); `cose` capped at 300 iterations
  (findata.ts:1817-1832); sector ego capped at 60 members with a
  synthetic "+N more" node (findata.ts:1644-1678); cloud components get
  static preset positions, no physics (findata.ts:1852-1896); no edge
  tooltips; detail panel only understands company/sector bundles.
- **Docs view** (findata.ts:1024-1143): browses **`doc/` only** (not the
  vault); `marked` + hljs with regex post-processing; **no DOMPurify on
  this path** (findata.html:14-21 loads marked but not purify — unlike
  entity_detail.js:310-315 which sanitizes); **no `[[wikilink]]`
  rendering anywhere in the app** (grep-verified); **no frontmatter
  display** (the API response has no frontmatter field either,
  api.ts:255-263).
- **Styling**: `static/findata.css` (2,389 lines) + `entity_detail.css`
  (878), all hardcoded hex, no CSS custom properties, system font stack,
  white cards on a purple gradient, no dark mode / `prefers-color-scheme`.

### 2.3 Capability families with no HTTP surface

From the query/algorithms layer inventory: `near_duplicate_notes`
(query.py:2619, ~1s pairwise self-join, deliberately off the hot path),
`suggest_relations` (suggest_relations.py:156, link-prediction
suggestions with sidecar append — a read-only projection is safe),
`harmonic/katz/laplacian/local_reaching` centralities + `link_prediction`
+ `voterank` (computed & persisted into `graph_analytics` by
`make recompute-graph`, but not in the metrics allowlist at
app.py:2025-2034), and `analytics.fetch(name)` (analytics.py:261 — 5 named
reports over the git-tracked Parquet snapshot).

## 3. Design system

One token system (CSS custom properties), two intentional registers.
Colors below are the committed direction; exact hexes finalize during S3/S5
visual iteration, but the *structure* (semantic palette, registers, mono
data voice) is the contract.

### 3.1 Desk register (shell + Graph + stats chrome)

| Token | Value | Use |
|---|---|---|
| `--ink-0` | `#0B0F14` | canvas base (deep blue-black, not pure black) |
| `--ink-1` | `#11161D` | panels |
| `--ink-2` | `#1A222C` | raised / hover |
| `--rule` | `#263241` | hairlines |
| `--text-hi/mid/low` | `#E8EDF2 / #9FB0BF / #5C6E7E` | text tiers |
| `--accent` | `#E0A93E` | brass — selection, focus, active only |

**The edge semantics ARE the palette** — every edge type gets a stable
accent (belongs teal `#2DD4BF`, competes amber `#F5B14C`, M&A rose
`#F28B82`, jv violet `#C39BFF`, supply-chain steel `#7CA8C9`, co-mention
slate `#6B7F94`, theme exposure orchid, cited-in cream, hierarchy teals),
driven by the same `relationship_types` data `/api/graph/cloud` already
returns (app.py:1887-1936) so the legend, filters, and canvas can never
drift. Entity types get shapes/sizes: company off-white round, sector
teal, super-sector larger deep-teal, sub-sector small teal, theme violet,
edition cream square. Single brass accent for interaction — explicitly
not the generic acid-green-on-black terminal look.

### 3.2 Paper register (Docs reader + entity-page prose)

Warm parchment `#F5F1E8`, umber text, rust links — market-letter
newsprint, not the template cream+terracotta look. Edition notes render
with a **masthead** (publication name / date / issue from frontmatter,
double hairline rule) — the secondary signature.

### 3.3 Type

Self-hosted **IBM Plex** (OFL): Plex Sans (UI), **Plex Mono as the data
voice** — tickers, years, edge types, frontmatter keys, all metric values
render as small tracked mono labels; Plex Serif for reader prose.
`@font-face` from `static/vendor/fonts/` (CSP stays same-origin; ~3
weights × 3 families ≈ 400-500KB committed once).

### 3.4 Signature element — the As-Of Chronoscope

A temporal year scrubber docked in the Lens rail. Dragging it re-queries
ego bundles and shortest paths with `as_of` (both endpoints already
accept it — app.py:1281, app.py:1524) and dims out-of-validity edges.
It encodes what makes this graph unusual: it is a *temporal* knowledge
graph (edges carry `valid_from/valid_to`; editions are dated).

### 3.5 Layouts

```
GRAPH — "The Lens"                        DOCS — "The Reading Room"
┌──────┬───────────────────────┬───────┐  ┌──────────┬────────────┐
│ LENS │ canvas                │INSPECT│  │COLLECTION│ READER     │
│ ──── │ fcose, semantic       │ ───── │  │docs/vault│ masthead   │
│ ▸ Ego│ edge colors, hover    │ facts │  │tabs      │ fm chips   │
│ ▸ All│ tooltips, zoom-fade   │ edges │  │search    │ 68ch serif │
│ ▸Path│ labels, louvain       │ events│  │(hybrid ◉)│ [[links]]  │
│ ▸Rank│ shading               │ action│  │          │ TOC rail   │
│ ▸Time│ ┌AS OF ──|──► 2024┐   │ s     │  │          │ RELATED    │
│      │ legend: belongs …    │       │  │          │ rail ▸     │
└──────┴───────────────────────┴───────┘  └──────────┴────────────┘
```

Lens modes: **Ego** (entity search; tap-to-expand progressively — the
60-member cap becomes "expand" affordances; semantic tab via
`/api/graph/semantic`), **All** (cloud with fcose, edge-type filters,
min-degree ≥ 2 **on by default** with an "everything" toggle, louvain
community shading toggle), **Path** (A→B, hops, as_of,
hop-ribbon highlight), **Rank** (metric tables/bars — all 10 centralities,
louvain groups, link-prediction suggestions, voterank seeds), **Time**
(edges-by-year bars, cross-sector bridges, co-mentions leaderboard,
near-duplicate triage list). **Inspector**: persistent right rail —
entity facts, its edges, events timeline, jump actions ("open note",
"graph it").

Entity pages: paper register + mono facts sidebar, vertical events
timeline, semantic-peers chips, similar-notes list, live wikilinks.

## 4. New endpoint contracts (S1)

All read-only; JSON-404 parity; `/api/graph/*` ones inherit the ETag
hook for free.

1. **`GET /api/graph/near-duplicates`** — `min_sim=0.9`, `doc_type=company`,
   `limit=100` (1-500 clamp like siblings). → `{pairs: [{path_a, path_b,
   title_a, title_b, similarity}]}`. Wraps `near_duplicate_notes`
   (query.py:2619). Budget: ~1s; served on-demand only (panel with
   loading state), never prefetched.
2. **`GET /api/graph/suggestions`** — `method=jaccard`, `top=25` (1-100),
   `min_score=0.3`, `companies_only`. → `{method, suggestions: [{source,
   target, score, edition}]}`. Wraps `suggest_relations()` **read-only —
   never touches `findata/_pending_relations.txt`** (sidecar writes stay
   CLI-only, all-writes-explicit doctrine).
3. **`/api/graph/metrics` allowlist extension** (app.py:2025-2034):
   `harmonic_centrality`, `katz_centrality`, `laplacian_centrality`,
   `local_reaching_centrality` (scalar `ranked[]` like existing), plus
   `link_prediction` (per-entity `candidates[]` payload already persisted
   by algorithms.py:409) and `voterank` (`seeds[]`, algorithms.py:446).
   Reads `graph_analytics` only; 404 unknown metric unchanged.
4. **`GET /api/analytics/<name>`** — name ∈ {summary, edge-growth,
   sector-growth, top-entities, coverage}; wraps `analytics.fetch(name)`
   → `{title, headers, rows, note}`; 404 otherwise. Reads
   `snapshots/parquet/` (git-tracked, read-only). No ETag hook (outside
   `/api/graph/*`); acceptable — reports are cold-opened rarely.

## 5. Implementation plan (slices)

| Slice | Content | New tests | Gate per slice |
|---|---|---|---|
| **S0** | This proposal | — | user review |
| **S1** | 4 endpoint groups (§4) in app.py | ~12 (near-dup ×2, suggestions ×2 incl. sidecar-untouched assert, metrics ×3, analytics ×5 incl. 404) | targeted `pytest tests/test_api_*`, `ty check app.py` |
| **S2** | Frontend foundation, behavior-preserving: split findata.ts → `src/core` (typed api client, dom, toast, view router), `src/views/*`; token CSS + both registers + Plex fonts vendored; shell/nav redesign; cytoscape → npm import + `cytoscape-fcose`; **load DOMPurify in findata.html** (XSS gap); keep `window.viewer` contract | 0 (TS gated by tsc) | `make frontend-check`, `make frontend`, manual smoke |
| **S3** | Lens: modes rail, Ego/All/Path redesign, Chronoscope, tooltips, zoom-fade labels, legend filters, progressive expansion | 0 | frontend-check + screenshot pass |
| **S4** | Rank + Time modes + Inspector (metrics, louvain, suggestions, near-dup, timeline, events) | 0 | frontend-check + screenshot pass |
| **S5** | Reading Room: unified sidebar (doc/ + vault via `/api/entities`), frontmatter chips, mastheads, `[[wikilink]]` in-place navigation (client-side stem→file_path index; `[[stem\|title]]` form per the wikilink-resolves-to-stem rule), hybrid toggle, related rail (similar + edition_companies) | 0 | frontend-check + screenshot pass |
| **S6** | Entity pages: migrate `entity_detail.js` into the TS bundle (one pipeline, one type contract); tokens/theme, events timeline, semantic peers, similar notes, wikilinks | 0 | frontend-check + screenshot pass |
| **S7** | Visual iteration with the web-gui-tester skill (run app locally, screenshot every view/mode, polish to the design), a11y floor (visible focus, `prefers-reduced-motion`, keyboard nav), responsive sanity; bundle rebuilt + committed | — | **full gates ONCE, only with explicit permission**: make qa, integration, advisory (incl. frontend-check), then user pushes + `make secret-scan` |

Runtime budgets: the new endpoints are all sub-second except
near-duplicates (~1s, on-demand) — no perf-gate entries needed; the
existing `make perf` suite is untouched (frontend isn't benchmarked).

## 6. Out of scope (explicitly)

- No write endpoints: refresh stays the only mutating route; sidecar
  append, rebuild, `--apply` anything remain CLI/make-only.
- No framework migration (stays vanilla TS + esbuild IIFE, committed
  bundle, vendored/no-CDN posture).
- No changes to Companies/Sectors/Statistics views beyond what the S2
  token/shell inheritance does for free.
- `context_pack` (GraphRAG-lite) — deferred; candidate follow-up.
- No dark/light user toggle — the two registers are fixed by surface.

## 7. Risks

- **Refactor churn**: 2,300-line single-file split. Mitigation: S2 is
  mechanical and behavior-preserving (same DOM ids, same behavior),
  verified by smoke before any redesign lands on top.
- **Committed-bundle doctrine**: every TS-touching slice rebuilds
  `static/findata.bundle.js`; deploy stays Node-free (build-time deps
  only: cytoscape, cytoscape-fcose, cose-base via npm, bundled in).
- **fcose on the full cloud** (~1,209 nodes): cose-family cost scales
  with iterations × edges; mitigations already proven in-repo (bounded
  iterations, no animation, degree filter defaults, component-packing
  fallback). If fcose is still slow, fall back to the existing
  component-preset for "All" and keep fcose for ego/path.
- **near-duplicates latency** (~1s): loading state, on-demand, ETag/304.
- **Wikilink resolution drift**: links target note *filename stems*
  (obsidian-wikilink-resolves-to-filename rule); the client-side index is
  built from `/api/entities` (name + file_path), the same source the app
  trusts — no new resolution semantics invented.
- **Font weight on repo**: ~500KB of OFL woff2 committed once, with
  license files. Acceptable for a self-hosted personal tool; flagged in
  Open questions if the user disagrees.

## 8. Success criteria

- Every capability surface listed in §2.1/§2.3 has a visible UI
  affordance; the 4 new endpoint groups are live with green tests.
- Behavior-preserving baseline: all current features (browse, filter,
  search, ego/cloud/path, docs reading, entity pages) still work after S2.
- `make frontend-check` (tsc strict) green; bundle rebuilt + committed;
  CSP unchanged; **DOMPurify on every markdown path** (verified with an
  injected-payload note during the S7 visual pass).
- A11y floor: visible keyboard focus, reduced-motion respected,
  keyboard-navigable main flows.
- Full gates green once at the end (qa / integration / advisory), with
  the user's explicit permission; user pushes and runs secret-scan.

## 9. Open questions — RESOLVED 2026-08-22 (user accepted all recommendations)

1. **Fonts**: ✅ **self-hosted IBM Plex** (~500KB OFL woff2 + license files
   committed to `static/vendor/fonts/`; mono data voice confirmed).
2. **Cloud defaults**: ✅ **min-degree ≥ 2 filter ON by default** in the
   All mode, with an explicit "everything" toggle for the full render
   (§3.5 updated).
3. **Entity-page script**: ✅ **migrate `entity_detail.js` into the TS
   bundle** during S6 — one pipeline, one `api.ts` type contract (§5
   updated).
4. **Suggestions placement**: ✅ **Rank mode** (alongside metrics and
   louvain groups; Time keeps near-duplicates/timeline).
5. **Archive category**: ✅ **create `doc/improvements/archive/ui/`** at
   execution (first UI proposal; `graph/` would mislabel the
   frontend-majority work).

## 10. Implementation log

**R0 — Review fold (2026-08-22): PASSED, no changes required.** Every
file:line claim verified against current code: all ten unwired routes exist
at exactly the cited lines (peers 1268, events 1469, semantic 1570,
similar 1620, edition_companies 1668, co-mentions 2176, bridges 2200,
edges-by-year 2215, refresh 2229); CSP block 2272–2282 ✓; metrics allowlist
2025–2034 ✓ (`harmonic/katz/laplacian/local_reaching` absent,
`link_prediction.candidates[]` + `voterank.seeds[]` persisted shapes
confirmed in algorithms.py); `near_duplicate_notes` query.py:2619 ✓;
`suggest_relations.py:156` ✓ → `_pending_relations.txt` ✓;
`analytics.fetch` :261 ✓ with all five report names ✓;
`DocContentResponse` carries no frontmatter field ✓; **DOMPurify gap
confirmed** (templates/findata.html:14 loads marked only;
entity_detail.html:15-16 loads both) ✓; edge-label blanking + `font-size: 0`
+ "4110 edge labels are the #1 render cost" comments found verbatim
(findata.ts:1434, 2056-2071) ✓; sector ego `MAX = 60` (:1644) ✓; cose
`numIter = 300` (:1827) ✓. Single drift: `/api/search` hybrid parsing is at
app.py:641 (proposal cited 610) — cosmetic, folded here. Green light for S1.

**R1 — S1 endpoint groups (2026-08-22): DONE.** All four §4 contracts live:
`GET /api/graph/near-duplicates` (app.py:1709; min_sim/doc_type/limit clamps
as specified; on-demand only), `GET /api/graph/suggestions` (:1759;
read-only wrap of suggest_relations() — sidecar untouched, asserted by a
dedicated test), metrics allowlist extension (`harmonic/katz/laplacian/
local_reaching` scalar ranked[] + `link_prediction`/`voterank` payload
metrics at :2174/:2182), and `GET /api/analytics/<name>` (:1819; five
report names over snapshots/parquet). 100 new tests: test_api_graph_unit.py
(84) + test_api_graph_metrics.py (16); `ty check app.py` clean.

**R2 — S2 frontend foundation (2026-08-22): DONE.** Behavior-preserving,
verified by live smoke before any redesign work on top.

- **Split**: findata.ts (2,309 lines) → `src/core/{api,dom,toast,markdown,
  router}.ts` + `src/views/{companies,sectors,stats,docs,graph}.ts`
  (graph.ts 1,061 lines) + ~120-line shell. Views own their state and get
  `isActive` callbacks bound to `Router.isActive(view)`; SectorsView gets an
  `onSectorPicked` callback that deep-links into Companies with the sector
  filter applied. Shell keeps exactly three delegating methods for inline
  onclick: `window.viewer.goToPage / openLightbox / copyCode`.
- **API client**: `fetchJson<T>` throws `ApiError(status, message)` on
  non-OK. Lenient legacy call sites previously ignored response.ok — console
  error text changes slightly; user-visible behavior unchanged.
  `performContentSearch` deliberately keeps raw fetch for its 503
  special-case message.
- **Latent bug fixed en route**: graph-detail panel's "Centre on search"
  button used an inline `onclick="getEl(...)"` attribute — `getEl` was never
  global under esbuild IIFE, so the button never worked. Now a programmatic
  listener (same visuals).
- **XSS gap closed**: purify.min.js (already vendored, unloaded) now loaded
  in findata.html; `processRichContent` sanitizes through DOMPurify.
- **cytoscape → npm**: cytoscape@3.28.1 + cytoscape-fcose@2.2.0 (exact
  pins), imported and fcose registered at module load; script tag removed
  from findata.html. Bundle 76KB → 1.4MB (+2.2MB map) as expected per §7.
- **Tokens + fonts**: static/tokens.css (loaded before findata.css) with
  @font-face for self-hosted IBM Plex Sans/SemiBold/Mono Regular+Medium/
  Serif Regular+Italic (6 latin-complete woff2, 373KB total, OFL license at
  static/vendor/fonts/OFL-IBMPLEX.txt), Desk register (--ink-*/--text-*/
  --accent brass), Paper register (--paper-*), stable edge-type accents
  keyed to relationship_types, shared metrics. Chrome pass: body/container/
  header/tagline/badges/nav-link retargeted onto Desk tokens; interior
  surfaces stay light until S3/S5 by design.
- **Smoke (live, port 5201)**: all five views switch via nav clicks;
  Companies 20 cards "1-20 of 1314"; Sectors 42 tags; Stats 10 stat-cards;
  Docs catalog 47 items → doc opens, TOC anchors generated through the
  DOMPurify path; Graph lazy-init on bundled cytoscape OK, ego search
  ("Tata Motors Passenger Vehicles") renders status + detail rows, bad-name
  404 path correct; fonts served (12 woff2 requests); window.viewer 3-method
  contract intact; DOMPurify defined. Only console entries: pre-existing
  favicon 404 + cytoscape wheelSensitivity warning (pre-existing config)
  + the deliberate not-found probe.
- **Gates**: `make frontend-check` (tsc strict) green; `make frontend`
  bundle rebuilt + committed. Committed together with S1 in fb92960.

**R3 — S3 Graph Lens (2026-08-22): DONE.** Implemented + browser-verified
(screenshots in gitignored `gui-test-screenshots/`).

- **Delivered**: modes rail (Ego/All/Path with active-state brass),
  As-Of Chronoscope (year slider; ego + path re-query with `as_of`), cloud
  min-degree ≥ 2 default + everything toggle + louvain community shading
  (1293 entities coloured from `/api/graph/metrics/louvain_community`),
  interactive edge-type legend chips (client-side hide/show, all/none),
  hover tooltips (#graph-tip; node degree/type/community, edge
  source→target+props), zoom-fade cloud labels (3 buckets, hubs ≥ deg 6),
  progressive expansion ("＋ neighbours" merges a 2nd ring, 150-node cap),
  sector egos render ALL members (60-cap synthetic node removed), Path
  mode renders its own hop-chain subgraph + clickable ribbon chips,
  fcose default for ego, edge/node colours read from tokens.css `--edge-*`
  (single source of truth with the legend).
- **Bugs found & fixed during verification**: (1) fcose ran with
  `fit: false` (a cloud-path setting) so ego networks laid out outside the
  viewport — ego now fits; (2) `quality: "trajectory"` is not a valid
  fcose option (default|proof only) and fcose-as-cloud-default wedged the
  render (swallowed async rejection) — cloud now defaults to the fast
  component-packing preset, fcose on the cloud is an explicit opt-in
  (bounded 600 iters), and `loadGraphCloud` wraps the render in try/catch
  with status-line surfacing; (3) stale empty-state hint ("Tata Motors"
  is not an entity — now CEAT / Reliance Industries / Infosys).
- **Verified in-browser** (DOM asserts + screenshots): lens shell, CEAT
  ego (status + detail panel), chronoscope 2022 re-query (CEAT 3→2
  relationships — a later-dated edge correctly drops out), All cloud
  (1208 entities · 5035 edges, 15 chips), chip toggle round-trip
  (`edge-chip` ⇄ `off`), louvain shading status, Path no-path case
  (server-confirmed truth: CEAT↔HDFC Bank has no ≤5-hop path) and the
  connected case (HDFC Bank→Banking→ICICI Bank, 3 hop chips, ribbon +
  subgraph). **Not browser-verified** (canvas-coordinate interactions):
  hover tooltip contents and the expand-neighbours merge — code-reviewed
  only; both are quick manual checks.
- **Verification-methodology lesson** (recorded for S5/S7): the vision
  model agrees with leading prompts — one "rail present" verdict was a
  false positive against a stale cached template. All subsequent checks
  pair DOM ground-truth reads with NEUTRAL screenshot prompts. Also:
  restart the Flask process after template edits (Jinja caches with
  debug=False).
- **Gates**: tsc strict green, bundle rebuilt (1.4MB). Full gates remain
  end-of-work (S7), with permission.

**R4 — S4 Rank + Time + Inspector (2026-08-22): DONE.** Implemented +
browser-verified (screenshots `gui-test-screenshots/s4_1_rank.png`,
`s4_2_time.png`, `s4_3_ego_timeline.png`).

- **Rank mode**: metric dropdown covering the full `/api/graph/metrics`
  scalar allowlist (10 centralities incl. clustering coefficient) plus
  both payload metrics — `link_prediction` (entity / predicted partner /
  best score columns, best candidate first) and `voterank` (ordered seed
  ladder); top-N select (10/25/50/100); score-bar table rows, every
  entity clickable → jumps the Lens to an Ego view (shared `_centreOn`).
  `weakly_connected_component` deliberately omitted (degenerate lens:
  one giant component — All mode + layouts show the same fact).
- **Rank side panels**: louvain groups (top 8 by size, palette swatches,
  member chips, modularity note) and read-only link suggestions
  (`/api/graph/suggestions`; method select; pref-attach runs unfiltered
  since its scores are unnormalized; explicit "sidecar untouched" note).
- **Time mode**: deal-activity-by-year stacked bars (year tracks scaled
  to the max year, per-edge-type segments coloured from the same
  `--edge-*` token palette, legend chips), cross-sector bridges (top 12),
  co-mention leaderboard (top 15, slate bars), near-duplicate triage
  behind an explicit "run check" button — on-demand only per §4.1
  (~1s, loading state, result rows deep-link the entity pages).
- **Inspector events timeline**: company-ish node inspections fetch
  `/api/events/<name>`; dated events render at their stored precision
  (year/month/day), undated sort last as "—"; counterparty + magnitude
  inline, `source_quote` as a native tooltip. A monotonic `detailSeq`
  token guards stale async renders; sector/theme/edition/sub-sector
  groups skip the fetch entirely.
- **Mode plumbing**: Rank/Time swap the canvas row for the data panels
  (canvas gets a resize kick on return), the Chronoscope hides in table
  modes (no `as_of` semantics for whole-graph aggregates), and all S4
  caches (per `metric:top`, groups, per-method suggestions, time panels,
  near-dup) are invalidated by Refresh DB.
- **Types**: `MetricRanked/Seeds/LinkPrediction*/Suggestions*/`
  `NearDuplicate*/CoMentions*/Bridges*/EdgesByYear*` added to
  types/api.ts from the verbatim route payloads.
- **Verification** (curl ground truth first, then in-browser DOM asserts
  that matched the curls exactly): pagerank top row Nureca `9.0e-4`
  (25 of 1065); link_prediction 25 of 236 with "Allianz ↔ Mastercard
  1.00" first; voterank 10 seeds; 8 louvain groups; 9 jaccard
  suggestions; edges-by-year 6 year rows whose inline bar widths read
  14.3/57.1/42.9/28.6/100/28.6% (= per-year counts 1,4,3,2,7,2 over the
  max-7 year) — the vision pass had called the central panel "empty"
  because clicking run-check scrolls it out of view; widths verified
  from DOM styles instead; bridges 12 rows (FMCG ↔ Consumer 4 first);
  co-mentions 15 (HDFC AMC 28 first); near-dup 3 pairs (0.923 Nerofix ↔
  Perma first, button re-enabled); CEAT inspector timeline 2 items
  ("2023 ACQUISITION Camso · ₹1,000 cr", "— GUIDANCE 5%"); a
  co-mention click-out landed Ego on HDFC AMC with the canvas restored.
- **Not browser-verified** (quick manual checks): native title-tooltips
  on timeline items, and the non-jaccard suggestion methods (identical
  code path; jaccard exercised in-browser).
- **Gates**: `tsc --noEmit` (strict) green; bundle rebuilt + committed
  size 1.4MB. Full gates remain end-of-work (S7), with permission.

### R5 — S5 Reading Room (2026-08-22): DONE

- **Sidebar — unified collections**: the Docs view (retitled "The
  Reading Room") now has a two-tab Collection browser: `doc/` (the
  design corpus, unchanged behavior) and `vault` (every note-bearing
  entity from `/api/entities?limit=5000` — 1,224 notes of 1,314
  entities; sub_sectors/themes have null file_path and are skipped).
  Vault rows group: Super Sectors → Sectors → editions by series
  (The Chatter 81 / Points & Figures 25 / The Plotlines 2) → companies
  by sector_classification (42 groups). Sticky mono group headers,
  brass-active rows.
- **Search — per-corpus + hybrid**: the sidebar search hits
  `/api/docs/search` (doc/ mode, unchanged) or `/api/search` FTS5 with
  a `hybrid` ◉ toggle (vault mode only; re-runs the query on flip).
  Vault hits render doc_type chips (company/sector/P&F/chatter…),
  <mark> snippets and, when hybrid, per-row cosine badges. Stem titles
  from note_search are prettified (`Avanti_Feeds` → `Avanti Feeds`).
- **Reader — paper register**: vault notes render through
  `/api/entity/<path>` (encoded-slash form verified against Flask's
  path converter) in the warm-parchment register on Plex Serif.
  Company/sector notes get a frontmatter chip row (type, ticker,
  sector, industry, market_cap, created, last_modified — mono keys,
  umber values). Newsletter editions get the **masthead**: publication
  (series label), issue title, and `publisher · generated <date> ·
  fresh through <stale_after>` between double hairline rules. The TOC
  moved into a right rail (levels 1–6 indented).
- **[[wikilinks]] in place**: a client-side stem→file_path index is
  built from `/api/entities` (stem first, entity name as alias — the
  obsidian-resolves-to-stem rule; corpus has 3,188 links, 936 in
  `[[stem|label]]` form). After render, a DOM TreeWalker rewrites
  `[[target]]`/`[[target|label]]` text nodes into rust in-place nav
  links, skipping CODE/PRE/A/SCRIPT subtrees; unresolved targets
  render as muted non-links (e.g. the corpus's `[[Tariffs__Tailwinds.]]`
  typo). Verified live: Avanti Feeds body → `[[Inflection_Watch]]`
  resolved to the Chatter edition and swapped the reader with the URL
  pinned at `#docs`.
- **Related rail**: every vault note loads
  `/api/graph/similar/<path>?k=6` (embedding cosine, quiet 404 for
  unembedded notes); editions additionally load
  `/api/graph/edition_companies?edition=<stem>&k=8`. Rows show title +
  mini cosine bar; clicking navigates in place.
- **Verification** (curl ground truth, then in-browser DOM asserts):
  entities 1,224 note-bearing; Avanti Feeds chips exactly
  `company / ticker AVANTIFEED.NS / sector Agriculture / industry
  Packaged Foods / market cap mid_cap / created 2025-11-16 / last
  modified 2026-06-24`, 12 TOC items, 6 similar notes, 2 wikilinks
  resolved; Inflection Watch masthead `The Chatter / The Chatter:
  Inflection Watch / Zerodha · generated 2026-08-15 · fresh through
  2027-02-11` with 6+8 rail rows; "shrimp feed" FTS 11 matches (26
  marks, polymorphic chips), hybrid badges 77/69/63/…/48% with Avanti
  first — matching the API's 0.7695 cosine; doc/ regression (48
  documents, architecture.md opens, 10 TOC items, no rail). Screenshots
  s5_1_vault_sidebar / s5_2_company_note / s5_3_edition_masthead in
  gui-test-screenshots/.
- **Known scope notes**: vault rows show entity names (frontmatter
  titles only materialize once a note is opened — the list payload
  has no title field); `doc/local/` files (gitignored) remain
  browseable as before — pre-existing `/api/docs` behavior.
- **Gates**: `tsc --noEmit` (strict) green; bundle rebuilt (1.4MB).
  Full gates remain end-of-work (S7), with permission.

### R6 — S6 Entity pages (2026-08-22): DONE

- **Migrated into the TS build**: `static/entity_detail.js` (668 lines,
  console-noisy, duplicated markdown pipeline) is deleted; the page now
  runs `frontend/src/entity.ts`, a second esbuild entry →
  `static/entity.bundle.js` (**24KB** — no cytoscape tax; the SPA bundle
  stays 1.4MB). `npm run build` is now multi-entry (`--outdir +
  --entry-names=[name].bundle`). One pipeline, one type contract:
  core/api fetchJson, core/markdown renderer, core/toast, types/api.
- **Inline-onclick removal**: processRichContent no longer emits
  `onclick="viewer.openLightbox/copyCode"` — images and copy buttons
  carry `data-lightbox`/`data-copy`, wired per container by
  `markdown.wireRichInteractions()`. This is what lets the standalone
  entity pages share the pipeline (no `window.viewer` there); a
  `setLightboxOpener` hook routes SPA images to the core lightbox and
  entity-page images to that page's navigable lightbox (prev/next +
  arrows + Escape preserved). `window.viewer`'s only remaining inline
  consumer is pagination (`goToPage`).
- **Paper register + rail**: `entity_detail.html` restructured — tokens.css
  + findata.css supply the shared reader classes (masthead, fm-chips,
  wikilinks, related rows, lightbox); a rewritten 470-line
  entity_detail.css owns the page shell. Layout: serif article (74ch) +
  sticky intel rail — **Facts** (mono dl: sector, market cap, normalized,
  permalink, file), **TOC**, **Events** (vertical timeline, rust dots on
  a hairline spine, dated oldest→newest, undated "—", source_quote as
  native tooltip), **Semantic peers** (`/api/graph/semantic?k=8` chips
  with cosine %), **Similar notes** (`/api/graph/similar?k=6` rows with
  mini bars). Rail/Export/Print/Fullscreen chrome preserved; the old
  left-TOC sidebar became the Rail toggle.
- **Live wikilinks**: same stem→file_path index as S5 (built from
  `/api/entities`); `[[X]]`/`[[X|label]]` rewrite into rust links that
  navigate the browser to `/entity/<encoded path>`.
- **Verification**: Avanti Feeds page — chips verbatim from frontmatter,
  facts dl 5 rows, 12 TOC links, 1 event ("— jv · Thai Union Frozen
  Products"), 8 peers (Sharat Industries 81% → Venkys 70%, all linked),
  6 similar rows (Sharat 80% first); footnote `[[Inflection_Watch]]`
  click navigated to the edition page whose masthead read
  `The Chatter / The Chatter: Inflection Watch / Zerodha · generated
  2026-08-15 · fresh through 2027-02-11` with 6 similar notes; Rail
  toggle hides/restores. SPA regression after the onclick removal: an
  edition note in the Reading Room showed 5 `data-lightbox` images and
  clicking one opened the lightbox. Screenshots s6_1_company_entity /
  s6_2_edition_masthead in gui-test-screenshots/.
- **Bug found + fixed during verification**: the first cut delegated
  wikilink clicks through a handler that `preventDefault`ed and read
  `dataset.href` — which the entity linkifier never set (it emits real
  hrefs), so wikilink clicks did nothing. Handler removed; anchors
  navigate natively.
- **Not browser-verified** (code paths preserved from the old page):
  Export/Print/Fullscreen buttons and lightbox prev/next arrows.
- **Gates**: `tsc --noEmit` (strict) green; both bundles rebuilt +
  committed. Full gates remain end-of-work (S7), with permission.

### R7 — S7 Visual pass, a11y floor, responsive, XSS proof (2026-08-22): DONE

- **Screenshot sweep** (web-gui-tester methodology — DOM ground truth +
  neutral-prompt vision checks, per the R3/R4 lesson that leading
  checklist prompts make the vision model hallucinate every category):
  t01 Companies, t02 Sectors, t03 Statistics, t04 Graph Ego, t05 All,
  t06 Path (CEAT → Automotive → MRF, 2 hops — ribbon exercised; an
  unconnected pair honestly renders "No path found"), t07 Rank, t08
  Time, t09–t11 the three surfaces at 768px, t12 keyboard-focus ring.
  Verdicts: Companies/Sectors/Stats/Ego/All/Rank/Time coherent, no
  overlap/truncation; responsive stacking works on all three surfaces.
  Two vision claims were checked against the DOM and REJECTED as
  artifacts (louvain "chip truncation" — chips wrap with no
  max-width/ellipsis in CSS and full names in the markup; "pagerank
  dropdown cut off" — fully visible on the focused re-query).
- **Fixes landed** (findata.css S7 section + one TS touch):
  1. `:focus-visible` rings — brass on desk, rust on paper surfaces
     (verified visually after Tab-through: t12).
  2. `.lens-rail` max-height + overflow-y — the sticky rail clipped
     when taller than the viewport with no way to scroll.
  3. `.docs-article-meta` overflow-wrap — long mono file paths pushed
     the reader past the viewport edge at 768px.
  4. Reduced-motion: transitions already ride --motion-* tokens
     (auto-zeroed by the tokens.css media query); the one JS smooth
     scroll now falls back to `auto` under
     `prefers-reduced-motion: reduce`.
- **DOMPurify injected-payload proof (§8)**: a temporary
  `doc/local/xss_selftest_s7.md` (gitignored path; vault untouched)
  carrying `<script>`, `img onerror`, `javascript:` links and an
  inline-handler span rendered in the Reading Room with 0 script
  elements, 0 onerror attributes, 0 javascript: hrefs, 0 handler
  attributes — and the safe paragraph intact. File deleted after the
  check; doc/local back to its 6 real notes.
- **Bundles**: tsc strict green; findata + entity bundles rebuilt.
- **Full gates NOT run** — awaiting explicit user permission (per the
  standing gate etiquette), then: make qa, integration, advisory
  (incl. frontend-check), user pushes, `make secret-scan`.

### R7 addendum — edge-chip filter bug, found by the user (2026-08-22): FIXED

- **Bug**: All mode → "none" chip → select a relationship chip (e.g.
  `supplier_to`) showed nothing usable. Root cause: the chips only
  `.hide()`/`.show()` EDGES on the pre-filtered full-cloud node set —
  nodes were never re-filtered, the layout never re-ran, and the canvas
  never re-fit. After "none" the 1,208 nodes stayed scattered with the 6
  supplier edges lost in the haystack (labels zoom-faded) — the induced
  subgraph technically existed but was practically invisible.
- **Fix**: `_applyCloudFilter` now computes the subgraph INDUCED by the
  visible edge types whenever any chip is off (nodes = endpoints of
  visible edges; the min-degree rail toggle is suspended for that view,
  restored when all chips are back on). `_toggleEdgeType` and
  `_setAllEdgeTypes` rebuild via `_applyCloudFilter` — re-layout + fit +
  status line ("Edge filter — 12 entities · 6 edges (1 of 13 types)");
  the all-hidden case renders an empty canvas with guidance instead of
  1,208 orphan dots.
- **Verified** (browser, after rebuild): the exact user sequence now
  yields 12 entities · 6 edges (Acrysil↔IKEA, EPACK↔Hisense, Laxmi
  Organic↔Hitachi, MTAR↔Bloom, Shree Digvijay↔ONGC, Talbros↔Tata
  Motors PV — endpoints match the /api/graph/cloud payload exactly);
  "all" restores 1208 · 5035; "everything" still gives 1293 · 5120.
  Evidence: fix_supplier_to.png (bug_supplier_to.png = before).
- **Observed but NOT fixed** (pre-existing, out of this bug's scope):
  node `component` data is never assigned in the cloud path, so the
  connected-set tap-highlight no-ops and the "components" layout
  silently falls back to concentric. Candidate follow-up.

### R7 addendum 2 — component gap fixed + follow-ups closed (2026-08-22)

- **Cloud `component` gap FIXED**: `_applyCloudFilter` now runs a
  union-find over the VISIBLE edges and stamps each node element with
  its component root — re-computed per rebuild, so an edge-filtered
  subgraph gets its own correct components. This re-arms the
  tap-to-highlight connected set and the component-packing "components"
  layout (both were silently no-opping). Verified: full cloud
  1,208/1,208 nodes rooted (1 giant component, matching the known wcc
  shape); supplier_to-filtered subgraph = 6 components × 2 nodes.
- **All four "not browser-verified" manual checks are now verified**:
  (1) native tooltips present where they matter — inspector timeline
  items carry source_quote titles (CEAT's guidance event), rank entity
  buttons + suggestion rows carry centre-on titles; (2) all four
  non-jaccard suggestion methods render (adamic-adar 15 rows, common-
  neighbors 15, resource-alloc 5, pref-attach 15 with raw scores);
  (3) entity-page chrome — Export fired a real download, Print showed
  its toast, Fullscreen flipped to Exit Fullscreen, lightbox arrows
  changed images + Escape closed; (4) Reading Room P&F + Plotlines
  mastheads render ("Points & Figures: Capital at Work" with 100 OCR
  images + working lightbox; "Plotlines: The Pilot").
- Separately (vault pipeline, not this proposal's UI scope): the
  build_sector_hierarchy frontmatter-stripping bug was fixed
  region-scoped (apply now swaps only the Child Sectors sentinel
  region; see tests/test_integration_note_writers.py — the strict
  xfail flipped to a real, passing assertion; 99 hierarchy-related
  tests green; live `--check` green with zero writes).
